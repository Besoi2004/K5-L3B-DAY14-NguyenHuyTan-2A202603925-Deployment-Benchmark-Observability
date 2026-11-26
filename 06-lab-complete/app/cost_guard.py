"""
Cost guard (tiêu chí 8).

  • Cộng dồn chi phí LLM (tính theo token) vào Redis: 1 key theo ngày, 1 key theo tháng
  • Tổng chi phí đã chạm DAILY_BUDGET_USD hoặc MONTHLY_BUDGET_USD → HTTP 402
  • Tên key chứa ngày/tháng (UTC) → sang ngày/tháng mới tự dùng key mới = reset
"""
from datetime import datetime, timezone

from fastapi import HTTPException

from app.config import settings
from app.redis_client import r

# Giá USD / 1K tokens (tham khảo gpt-4o-mini)
PRICE_INPUT_PER_1K = 0.00015
PRICE_OUTPUT_PER_1K = 0.0006


def _keys() -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    return f"cost:day:{now:%Y-%m-%d}", f"cost:month:{now:%Y-%m}"


def estimate_tokens(text: str) -> int:
    """Ước lượng ~4 ký tự / token (mock LLM không trả số token thật)."""
    return max(1, len(text) // 4)


def check_budget() -> None:
    """Gọi TRƯỚC khi gọi LLM. Hết budget → raise HTTPException(status_code=402)."""
    day_key, month_key = _keys()
    spent_day, spent_month = (float(v or 0) for v in r.mget(day_key, month_key))
    if spent_day >= settings.daily_budget_usd or spent_month >= settings.monthly_budget_usd:
        raise HTTPException(
            status_code=402,
            detail={
                "error": "Budget exceeded",
                "spent_today_usd": round(spent_day, 6),
                "daily_budget_usd": settings.daily_budget_usd,
                "spent_month_usd": round(spent_month, 6),
                "monthly_budget_usd": settings.monthly_budget_usd,
            },
        )


def record_cost(input_tokens: int, output_tokens: int) -> float:
    """Gọi SAU khi gọi LLM. Cộng chi phí của request vào tổng, trả về chi phí request."""
    cost = input_tokens / 1000 * PRICE_INPUT_PER_1K + output_tokens / 1000 * PRICE_OUTPUT_PER_1K
    day_key, month_key = _keys()
    pipe = r.pipeline(transaction=True)
    pipe.incrbyfloat(day_key, cost)        # nguyên tử: nhiều worker cộng cùng lúc không mất số
    pipe.expire(day_key, 2 * 24 * 3600)
    pipe.incrbyfloat(month_key, cost)
    pipe.expire(month_key, 32 * 24 * 3600)
    pipe.execute()
    return cost
