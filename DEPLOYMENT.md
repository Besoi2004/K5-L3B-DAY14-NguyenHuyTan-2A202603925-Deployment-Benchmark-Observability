# Deployment Information

## Public URL

https://day14-agent-62gh.onrender.com

## Platform

**Render** (gói Free), deploy bằng Blueprint [`06-lab-complete/render.yaml`](06-lab-complete/render.yaml) — agent Part 6 (`06-lab-complete/`).

| Tài nguyên | Loại | Ghi chú |
|---|---|---|
| `day14-agent` | Web Service, runtime Docker | Build từ `06-lab-complete/Dockerfile`, health check `GET /health` |
| `day14-redis` | Key Value (Redis-compatible) | Chỉ nhận kết nối nội bộ (`ipAllowList: []`), agent dùng qua `REDIS_URL` |

## Test Commands

Đặt biến trước (key là giá trị đã nhập trên Render, không ghi vào repo):

```bash
BASE=https://day14-agent-62gh.onrender.com
AGENT_API_KEY=<key đã đặt trên Render>
```

4 lệnh tự kiểm (CP6) — mỗi lệnh chỉ in mã HTTP:

```bash
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/health"     # 200
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/ready"      # 200
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -d '{"question":"Hello"}'   # 401
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -H "X-API-Key: $AGENT_API_KEY" -d '{"question":"Hello"}'   # 200
```

### Health Check

```bash
curl https://day14-agent-62gh.onrender.com/health
# {"status":"ok","environment":"production","uptime_seconds":191.7}

curl https://day14-agent-62gh.onrender.com/ready
# {"ready":true}
```

### API Test (with authentication)

```bash
curl -X POST https://day14-agent-62gh.onrender.com/ask \
  -H "X-API-Key: $AGENT_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"user_id": "test", "question": "Hello"}'
```

Không có key hoặc sai key:

```bash
curl -i -X POST https://day14-agent-62gh.onrender.com/ask -H "Content-Type: application/json" -d '{"question":"Hello"}'
# HTTP/1.1 401 Unauthorized
# {"detail":"Missing or invalid API key. Send header X-API-Key: <key>"}
```

## Environment Variables Set

Đặt trong `render.yaml` và trên Render Dashboard — không có giá trị secret nào trong code hay trong repo:

| Biến | Nguồn | Giá trị |
|---|---|---|
| `AGENT_API_KEY` | Nhập tay trên Dashboard (`sync: false`) | *(secret, không ghi ở đây)* |
| `REDIS_URL` | `fromService` → Key Value `day14-redis`, `property: connectionString` | URL nội bộ do Render cấp |
| `PORT` | `render.yaml` | `10000` (cổng Render chờ app lắng nghe) |
| `ENVIRONMENT` | `render.yaml` | `production` |
| `RATE_LIMIT_PER_MINUTE` | `render.yaml` | `10` |
| `DAILY_BUDGET_USD` | `render.yaml` | `1.0` |
| `MONTHLY_BUDGET_USD` | `render.yaml` | `10.0` |
| `LOG_LEVEL` | `render.yaml` | `INFO` |

## Lưu ý gói Free

- Service tự ngủ sau 15 phút không có request; request đầu tiên sau đó mất ~1 phút để khởi động lại.
- Key Value Free chỉ lưu trong RAM: restart là mất state rate limit / cost (chấp nhận được cho lab).

## Screenshots

- [Deployment dashboard](screenshots/dashboard.png)
- [Service running](screenshots/running.png)
- [Test results](screenshots/test.png)
