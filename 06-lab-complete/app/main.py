"""
Production AI Agent — Part 6 Final Project.

Chạy:  uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 2
Kiểm tra:  python check_production_ready.py
"""
import json
import logging
import sys
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import redis
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.auth import verify_api_key
from app.config import settings
from app.cost_guard import check_budget, estimate_tokens, record_cost
from app.rate_limiter import check_rate_limit
from app.redis_client import r, redis_healthy
from utils.mock_llm import ask as llm_ask


# ─────────────────────────────────────────────────────────
# Structured logging (tiêu chí 10): mỗi dòng log là 1 JSON object
# ─────────────────────────────────────────────────────────
class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "lvl": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        entry.update(getattr(record, "fields", {}))
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        # json.dumps tự escape dấu " và xuống dòng → dòng log luôn là JSON hợp lệ
        return json.dumps(entry, ensure_ascii=False, default=str)


_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(JsonFormatter())
logger = logging.getLogger("agent")
logger.handlers = [_handler]
logger.setLevel(settings.log_level)
logger.propagate = False


def log_event(event: str, level: int = logging.INFO, **fields) -> None:
    logger.log(level, event, extra={"fields": fields})


# ─────────────────────────────────────────────────────────
# Lifespan + graceful shutdown (tiêu chí 11)
#   uvicorn tự bắt SIGTERM: ngừng nhận kết nối mới, chờ request đang chạy xong,
#   rồi chạy phần sau `yield`. Không tự đăng ký handler SIGTERM để khỏi đè handler của uvicorn.
# ─────────────────────────────────────────────────────────
STATE = {"ready": False, "started": time.time()}


@asynccontextmanager
async def lifespan(app: FastAPI):
    STATE["ready"] = True
    log_event("startup", environment=settings.environment, port=settings.port,
              redis_ok=redis_healthy())
    yield
    STATE["ready"] = False          # /ready trả 503 trong lúc tắt
    log_event("shutdown", note="in-flight requests finished, closing redis pool")
    r.close()


app = FastAPI(title="Production AI Agent", lifespan=lifespan)


@app.middleware("http")
async def access_log(request: Request, call_next):
    start = time.perf_counter()
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        # Không log header/body → không lộ API key hay nội dung câu hỏi
        log_event("request", request_id=request_id, method=request.method,
                  path=request.url.path, status=status,
                  ms=round((time.perf_counter() - start) * 1000, 1))


# ─────────────────────────────────────────────────────────
# Xử lý lỗi: trả JSON rõ ràng, không lộ stack trace ra client
# ─────────────────────────────────────────────────────────
@app.exception_handler(redis.RedisError)
async def redis_error_handler(request: Request, exc: redis.RedisError):
    log_event("redis_error", logging.ERROR, path=request.url.path, error=type(exc).__name__)
    return JSONResponse(status_code=503, content={"detail": "Storage unavailable, retry later"})


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception):
    log_event("unhandled_error", logging.ERROR, path=request.url.path, error=type(exc).__name__)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


# ─────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────
class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    user_id: str | None = Field(default=None, max_length=128)


@app.get("/health")
def health():
    """Liveness (tiêu chí 4): luôn 200 khi process còn chạy, KHÔNG phụ thuộc Redis."""
    return {"status": "ok", "environment": settings.environment,
            "uptime_seconds": round(time.time() - STATE["started"], 1)}


@app.get("/ready")
def ready():
    """Readiness (tiêu chí 5): 200 khi sẵn sàng, 503 khi đang tắt hoặc Redis không kết nối được."""
    if not STATE["ready"]:
        return JSONResponse(status_code=503, content={"ready": False, "reason": "shutting down"})
    if not redis_healthy():
        return JSONResponse(status_code=503, content={"ready": False, "reason": "redis unavailable"})
    return {"ready": True}


@app.post("/ask")
def ask(body: AskRequest, api_key: str = Depends(verify_api_key)):
    # def (không async): mock LLM dùng time.sleep → FastAPI chạy trong threadpool, không chặn event loop
    check_rate_limit(api_key)      # 429 khi vượt RATE_LIMIT_PER_MINUTE
    check_budget()                 # 402 khi hết budget — kiểm tra TRƯỚC khi tốn tiền gọi LLM
    answer = llm_ask(body.question)
    input_tokens, output_tokens = estimate_tokens(body.question), estimate_tokens(answer)
    cost = record_cost(input_tokens, output_tokens)
    log_event("llm_call", input_tokens=input_tokens, output_tokens=output_tokens,
              cost_usd=round(cost, 8))
    return {
        "question": body.question,
        "answer": answer,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens,
                  "cost_usd": round(cost, 8)},
    }
