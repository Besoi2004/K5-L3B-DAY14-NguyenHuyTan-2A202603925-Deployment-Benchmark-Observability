"""
Rate limiting (tiêu chí 7) — sliding window 60 giây bằng Redis sorted set.

Mỗi API key có 1 sorted set: member = id request, score = thời điểm (giây).
Vì state nằm trong Redis nên mọi worker / instance dùng chung, restart agent vẫn còn.
"""
import hashlib
import math
import time
import uuid

from fastapi import HTTPException

from app.config import settings
from app.redis_client import r

WINDOW_SECONDS = 60


def _bucket(key: str) -> str:
    # Không lưu API key thô trong Redis, chỉ lưu hash
    return "ratelimit:" + hashlib.sha256(key.encode()).hexdigest()[:16]


def check_rate_limit(key: str) -> None:
    limit = settings.rate_limit_per_minute
    bucket = _bucket(key)
    now = time.time()
    member = f"{now:.6f}:{uuid.uuid4().hex[:8]}"

    # MULTI/EXEC: xoá request cũ → thêm request này → đếm, chạy nguyên tử,
    # nên 2 worker nhận request cùng lúc không cùng lọt qua giới hạn
    pipe = r.pipeline(transaction=True)
    pipe.zremrangebyscore(bucket, 0, now - WINDOW_SECONDS)
    pipe.zadd(bucket, {member: now})
    pipe.zcard(bucket)
    pipe.expire(bucket, WINDOW_SECONDS)
    _, _, count, _ = pipe.execute()

    if count > limit:
        r.zrem(bucket, member)  # request bị từ chối không tính vào hạn mức
        oldest = r.zrange(bucket, 0, 0, withscores=True)
        retry_after = WINDOW_SECONDS
        if oldest:
            retry_after = max(1, math.ceil(oldest[0][1] + WINDOW_SECONDS - now))
        raise HTTPException(
            status_code=429,
            detail={"error": "Rate limit exceeded", "limit_per_minute": limit,
                    "retry_after_seconds": retry_after},
            headers={"Retry-After": str(retry_after)},
        )
