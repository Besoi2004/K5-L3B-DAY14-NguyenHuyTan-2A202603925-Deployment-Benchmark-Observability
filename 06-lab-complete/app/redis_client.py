"""
Kết nối Redis dùng chung cho rate limiter và cost guard.

redis.from_url() chỉ tạo connection pool, CHƯA kết nối → app khởi động được kể cả
khi Redis chưa sẵn sàng. Kết nối thật chỉ mở ở lệnh đầu tiên (ping, zadd...).
Timeout ngắn để /ready trả 503 nhanh thay vì treo khi Redis chết.
"""
import redis

from app.config import settings

r = redis.from_url(
    settings.redis_url,
    decode_responses=True,
    socket_connect_timeout=2,
    socket_timeout=2,
)


def redis_healthy() -> bool:
    """Dùng cho GET /ready: Redis trả lời PING → True, lỗi kết nối → False."""
    try:
        return bool(r.ping())
    except redis.RedisError:
        return False
