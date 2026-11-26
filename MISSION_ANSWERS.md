# Day 14 Lab — Mission Answers

> Môi trường chạy: Windows 11 + WSL2 Ubuntu 26.04, Python 3.11.18 (uv), Docker Desktop 29.9.3.

## Part 1: Localhost vs Production

### Exercise 1.1: Anti-patterns tìm được trong `01-localhost-vs-production/develop/app.py`

| # | Anti-pattern | Dòng | Hậu quả |
|---|---|---|---|
| 1 | Hardcode secret: `OPENAI_API_KEY = "sk-hardcoded-..."` | 17 | Push lên GitHub là lộ key; muốn đổi key phải sửa code và deploy lại |
| 2 | Hardcode connection string có mật khẩu: `postgresql://admin:password123@...` | 18 | Lộ mật khẩu DB; không đổi được DB giữa dev/staging/prod |
| 3 | Log ra secret: `print(f"[DEBUG] Using key: {OPENAI_API_KEY}")` | 34 | Key nằm trong log, ai đọc được log là lấy được key (thấy thật khi chạy, xem 1.2) |
| 4 | Dùng `print()` thay vì logging có cấu trúc | 33, 38 | Không có level, timestamp; log aggregator không parse/lọc được |
| 5 | Config cứng `DEBUG = True`, `MAX_TOKENS = 500` | 21–22 | Không chỉnh được theo môi trường, debug bật cả trên production |
| 6 | Không có `/health` (và `/ready`) | 42–43 | Platform không biết app chết để restart; LB không biết lúc nào route traffic (gọi thử trả **404**) |
| 7 | `host="localhost"` | 51 | Trong container chỉ nghe loopback, bên ngoài container không gọi vào được |
| 8 | `port=8000` cố định | 52 | Railway/Render cấp port qua biến `PORT`; app không nghe đúng port thì health check fail |
| 9 | `reload=True` | 53 | Reloader theo dõi file, tốn tài nguyên, không dành cho production |
| 10 | Không xử lý SIGTERM / không có lifespan | — | Khi platform tắt container, request đang chạy có thể bị cắt ngang, không dọn dẹp kết nối |

### Exercise 1.2: Chạy bản develop

```text
$ curl -i -X POST "http://localhost:8000/ask?question=Hello"
HTTP/1.1 200 OK
{"answer":"Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé."}

$ curl -i http://localhost:8000/health
HTTP/1.1 404 Not Found
{"detail":"Not Found"}

$ curl -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question":"Hello"}'
422          # bản develop nhận question qua query string, không nhận JSON body
```

Log server (trích):

```text
[DEBUG] Got question: Hello
[DEBUG] Using key: sk-hardcoded-fake-key-never-do-this      ← secret bị in ra log
INFO:     127.0.0.1:50704 - "GET /health HTTP/1.1" 404 Not Found
```

**Quan sát:** app chạy được và trả lời, nhưng chưa production-ready: secret lộ trong log, không có health check, chỉ nghe `localhost:8000`.

### Exercise 1.3: So sánh develop và production

Chạy bản production (`cp .env.example .env`, `python app.py`):

```text
$ curl -i http://localhost:8000/health   → 200 {"status":"ok","uptime_seconds":0.5,"version":"1.0.0","environment":"development",...}
$ curl -i http://localhost:8000/ready    → 200 {"ready":true}
$ curl -i -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question":"Hello"}'
                                         → 200 {"question":"Hello","answer":"...","model":"gpt-4o-mini"}
$ kill -TERM <pid>                       → "Waiting for application shutdown... Finished server process", exit code 0
```

| Feature | Develop | Production | Tại sao quan trọng? |
|---|---|---|---|
| Config | Hằng số trong code (`DEBUG = True`, port 8000) | `config.py` đọc env (`os.getenv("PORT")`, `DEBUG`, `ENVIRONMENT`...) | Cùng một code/image chạy được ở dev, staging, prod; đổi cấu hình không cần sửa code |
| Secrets | `OPENAI_API_KEY = "sk-..."` trong code, còn in ra log | Đọc từ env, `.env` không commit (chỉ có `.env.example`), không log secret | Secret không nằm trong Git và log; rotate key chỉ cần đổi biến môi trường |
| Health check | Không có (`/health` → 404) | `GET /health` (liveness) + `GET /ready` (readiness, 503 khi chưa sẵn sàng) | Platform biết khi nào restart container; load balancer biết khi nào được gửi traffic |
| Logging | `print()` | Logging JSON (`{"time":..,"level":..,"msg":..}`), log số liệu (độ dài câu hỏi) thay vì nội dung/secret | Log có cấu trúc thì tìm, lọc, đếm được trong Loki/Datadog; không lộ dữ liệu nhạy cảm |
| Host / Port | `localhost:8000` | `HOST=0.0.0.0`, `PORT` từ env | Chạy được trong container và trên cloud, nơi platform cấp port |
| Shutdown | Không xử lý, tắt đột ngột | `lifespan` dọn dẹp khi tắt, có handler SIGTERM | Request đang chạy được hoàn thành, kết nối được đóng gọn, không mất dữ liệu |
| Reload / Debug | `reload=True` luôn bật | Chỉ reload khi `DEBUG=true` | Production không chạy reloader, không lộ thông tin debug |
| Validation | Không | `Settings.validate()` dừng app nếu `ENVIRONMENT=production` mà thiếu `AGENT_API_KEY` | Fail fast lúc khởi động thay vì chạy với cấu hình sai |

**Điều mình tự phát hiện khi chạy bản production:** log của chính app (`startup`, `agent_request`...) **không hề xuất hiện**, chỉ có log của uvicorn. Nguyên nhân là `from config import settings` chạy `validate()`, và `logging.warning()` trong đó tự cấu hình root logger với level WARNING. Đến khi `app.py` gọi `logging.basicConfig(...)` thì root logger đã có handler nên lệnh này không có tác dụng, toàn bộ log INFO bị bỏ. Ngoài ra format `'{"msg":"%(message)s"}'` không escape dấu `"`, nên message chứa JSON sẽ tạo ra dòng log không phải JSON hợp lệ. Ở Part 6 cần viết `logging.Formatter` riêng dùng `json.dumps` để tránh cả hai lỗi này.

### Checkpoint 1

Demo chạy được: `bash 01-localhost-vs-production/curl_demo.sh` (trong WSL).

**1. Vì sao hardcode secret nguy hiểm?**
- Secret đi vào lịch sử Git. Xoá file ở commit sau thì secret vẫn còn trong các commit cũ, ai clone repo là đọc được. Cách xử lý duy nhất là revoke key rồi tạo key mới.
- Bot quét GitHub public tìm chuỗi kiểu `sk-...` chỉ trong vài phút, rồi dùng key đó để tiêu tiền của bạn.
- Secret còn lan sang log: bản develop in `[DEBUG] Using key: sk-hardcoded-fake-key-never-do-this` ra log (thấy thật khi chạy demo).
- Muốn đổi (rotate) key thì phải sửa code và deploy lại. Dev, staging và prod cũng buộc phải dùng chung một key.

**2. Dùng environment variables thế nào?**
- Code chỉ đọc biến môi trường, không chứa giá trị: `port = int(os.getenv("PORT", "8000"))` (`production/config.py:18`). Giá trị mặc định chỉ dùng cho local và không bao giờ là secret.
- Local: đặt trong file `.env` (đã có trong `.gitignore`), commit `.env.example` không có giá trị thật làm mẫu. Cloud: đặt trong dashboard của Render/Railway, platform tự inject vào lúc chạy.
- Demo: chạy `PORT=9000 ENVIRONMENT=staging python app.py` mà không sửa dòng code nào. `/health` trên cổng 9000 trả `"environment":"staging"`.
- Fail fast: chạy `ENVIRONMENT=production` mà thiếu `AGENT_API_KEY` thì app dừng ngay với `ValueError: AGENT_API_KEY must be set in production!`, thay vì chạy với cấu hình sai.

**3. Vai trò của health check endpoint**
- `GET /health` (liveness) trả lời câu hỏi "process còn sống không?". Platform gọi định kỳ; nếu lỗi hoặc timeout nhiều lần thì platform restart container. Endpoint này chỉ kiểm tra chính process, không kiểm tra DB/Redis, để Redis chết không kéo theo việc restart app liên tục.
- `GET /ready` (readiness) trả lời câu hỏi "đã sẵn sàng nhận traffic chưa?". Endpoint trả 503 khi đang khởi động hoặc mất kết nối Redis/DB; khi đó load balancer tạm ngừng gửi request tới instance này nhưng không restart nó.
- Bản develop không có `/health` (trả 404) nên platform không phân biệt được app đang sống hay đã treo. Render cũng dùng `healthCheckPath` để quyết định deploy mới có thành công hay không.

**4. Graceful shutdown là gì?**
- Khi deploy bản mới, scale xuống hoặc chạy `docker stop`, platform gửi **SIGTERM** rồi chờ một khoảng thời gian (Docker chờ 10 giây) trước khi gửi SIGKILL.
- Tắt êm nghĩa là: ngừng nhận request mới, xử lý xong các request đang chạy, đóng kết nối (DB, Redis), rồi thoát với exit code 0.
- Demo: gửi một request rồi lập tức `kill -TERM`. Kết quả: `request đang chạy nhận HTTP 200` và `process thoát với exit code 0`, log có `Waiting for application shutdown... Application shutdown complete`.
- Nếu không tắt êm, người dùng nhận lỗi giữa chừng mỗi lần deploy, và dữ liệu đang ghi dở có thể bị mất.

---

## Part 2: Docker

### Exercise 2.1: Đọc `02-docker/develop/Dockerfile`

1. **Base image:** `python:3.11` (dòng 8), bản Python đầy đủ chạy trên Debian, có sẵn compiler, header, git... nên rất nặng.
2. **Working directory:** `/app` (dòng 11). Các lệnh `COPY`/`RUN`/`CMD` sau đó đều chạy tương đối từ thư mục này; nếu thư mục chưa có thì Docker tự tạo.
3. **Vì sao `COPY requirements.txt` đứng trước `COPY app.py`?** Mỗi lệnh trong Dockerfile tạo một layer, và Docker dùng lại layer cũ (cache) khi input của nó không đổi. `requirements.txt` hiếm khi thay đổi còn code thì đổi liên tục. Copy requirements trước rồi `pip install` thì lần build sau, khi chỉ sửa `app.py`, Docker dùng lại layer đã cài package và chỉ build lại từ `COPY app.py`. Nếu `COPY . .` trước thì mỗi lần sửa code đều phải `pip install` lại từ đầu.
4. **CMD vs ENTRYPOINT:**
   - `CMD ["python", "app.py"]` là lệnh **mặc định**, bị thay thế hoàn toàn nếu `docker run <image> <lệnh khác>` (vd `docker run my-agent:develop sh`).
   - `ENTRYPOINT` là lệnh **cố định** của image; tham số của `docker run` được nối vào sau nó (muốn đổi phải dùng `--entrypoint`). Hay dùng `ENTRYPOINT` cho chương trình chính + `CMD` cho tham số mặc định.
   - Cả hai nên viết dạng exec (mảng JSON) để process là PID 1 và nhận được SIGTERM; dạng shell (`CMD python app.py`) chạy qua `/bin/sh -c`, khi đó sh là PID 1 và không chuyển tiếp SIGTERM cho app.

### Exercise 2.2: Build và chạy image develop

Build từ **gốc repo** (Dockerfile `COPY` theo đường dẫn tính từ gốc):

```text
$ docker build -f 02-docker/develop/Dockerfile -t my-agent:develop .      # lần đầu 6m44s (tải python:3.11)
$ docker run -d --name p2-dev -p 8000:8000 my-agent:develop
$ curl -i -X POST "http://localhost:8000/ask?question=What%20is%20Docker?"
HTTP/1.1 200 OK
{"answer":"Container là cách đóng gói app để chạy ở mọi nơi. Build once, run anywhere!"}
$ curl http://localhost:8000/health
{"status":"ok","uptime_seconds":0.3,"container":true}
$ docker exec p2-dev sh -c 'whoami; pwd; ls'
root            ← container develop chạy bằng root
/app
__pycache__  app.py  requirements.txt  utils
```

`-p 8000:8000` map cổng 8000 của máy host vào cổng 8000 trong container. Không có `-p` thì app vẫn chạy nhưng từ ngoài không gọi vào được.

### Exercise 2.3: Multi-stage build và so sánh kích thước

`02-docker/production/Dockerfile`:
- **Stage 1 `builder`** (`python:3.11-slim`): cài `gcc`, `libpq-dev` (công cụ để biên dịch package có phần C), rồi `pip install --user` các dependency vào `/root/.local`. Stage này chỉ để build, không được deploy.
- **Stage 2 `runtime`** (`python:3.11-slim` mới, sạch): tạo user `appuser`, chỉ `COPY --from=builder /root/.local` (thư mục package đã cài) và code, chạy bằng `USER appuser`, có `HEALTHCHECK`. gcc, apt cache và các file trung gian của stage 1 bị bỏ lại.

Đo bằng `docker images` / `docker image inspect --format '{{.Size}}'`:

| Image | `docker images` | Bytes | MiB |
|---|---|---|---|
| `my-agent:develop` (`python:3.11`, single-stage) | 1.67GB | 1 667 792 165 | 1590 |
| `my-agent:advanced` (`python:3.11-slim`, multi-stage) | 240MB | 239 815 436 | 228 |
| **Chênh lệch** | | −1 427 976 729 | **nhỏ hơn 85,6%** (≈ 7 lần) |

Kiểm tra trong container runtime:

```text
$ docker exec p2-adv sh -c 'whoami; which gcc || echo "no gcc in runtime"'
appuser
no gcc in runtime
```

`docker history my-agent:advanced`: layer lớn nhất mình thêm vào là `COPY /root/.local /home/appuser/.local` = **40.8MB** (FastAPI + uvicorn). Phần còn lại là base slim.

**Vì sao nhỏ hơn?**
1. Base `slim` bỏ compiler, header, tài liệu, các thư viện hệ thống không cần cho lúc chạy (bản `python:3.11` đầy đủ nặng hơn 1 GB).
2. Multi-stage: những thứ chỉ cần để build (gcc, libpq-dev, apt lists, pip cache) nằm ở stage `builder` và không đi vào image cuối; image cuối chỉ có layer của stage `runtime`.
3. Image nhỏ hơn thì pull/deploy nhanh hơn, ít package hơn nên ít lỗ hổng bảo mật hơn; kèm user non-root thì nếu app bị khai thác, kẻ tấn công cũng không có quyền root trong container.

### Exercise 2.4: Docker Compose stack

```text
$ cd 02-docker/production && touch .env.local
$ docker compose -p k5-day14-docker up -d --build
SERVICE   STATUS                            PORTS
agent     Up 3 seconds (health: starting)   8000/tcp
nginx     Up 3 seconds                      0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp
qdrant    Up 15 seconds (healthy)           6333-6334/tcp
redis     Up 14 seconds (healthy)           6379/tcp
```

Sơ đồ:

```text
                    máy host
  curl ──► :80 ──────────────────────────────────────────────────┐
                                                                 │
  ┌──────────── network "internal" (bridge, 172.21.0.0/16) ──────┼─────┐
  │                                                              ▼     │
  │   ┌──────────────┐   proxy_pass http://agent:8000   ┌────────────┐ │
  │   │ nginx :80    │ ───────────────────────────────► │ agent :8000│ │
  │   │ LB, rate     │                                  │ FastAPI    │ │
  │   │ limit 10r/s, │                                  └──┬──────┬──┘ │
  │   │ sec. headers │                REDIS_URL=redis://redis:6379/0   │
  │   └──────────────┘                                     ▼      ▼    │
  │                                          ┌──────────────┐ ┌──────────────┐
  │                                          │ redis :6379  │ │ qdrant :6333 │
  │                                          │ cache/session│ │ vector DB    │
  │                                          └──────────────┘ └──────────────┘
  └────────────────────────────────────────────────────────────────────┘
```

**Các service giao tiếp thế nào:**
- Cả 4 container nằm chung network `internal`. Docker có DNS nội bộ nên container gọi nhau bằng **tên service** (`agent`, `redis`, `qdrant`) thay vì IP (`agent` = 172.21.0.4, `redis` = 172.21.0.3...).
- Chỉ `nginx` publish cổng ra host (`80:80`). Agent không publish cổng nên gọi thẳng `localhost:8000` từ host thất bại (curl trả `000`); mọi request phải đi qua Nginx.
- `depends_on: condition: service_healthy`: agent chỉ khởi động sau khi redis và qdrant đã healthy (lúc chạy thấy redis/qdrant `healthy` trước, agent `Started` sau).

Test qua Nginx:

```text
$ curl -i http://localhost/health
HTTP/1.1 200 OK
Server: nginx
X-Frame-Options: DENY
X-Content-Type-Options: nosniff
X-XSS-Protection: 1; mode=block
{"status":"ok","uptime_seconds":2.0,"version":"2.0.0","timestamp":"2026-11-26T08:18:55.059497"}

$ curl -i -X POST http://localhost/ask -H "Content-Type: application/json" -d '{"question":"Explain microservices"}'
HTTP/1.1 200 OK
Server: nginx
{"answer":"Đây là câu trả lời từ AI agent (mock). Trong production, đây sẽ là response từ OpenAI/Anthropic."}
```

Header `Server: nginx` và các security header (`X-Frame-Options`...) chứng tỏ request đã đi qua Nginx rồi mới tới agent.

### Checkpoint 2
- Debug container: `docker logs <container>` (xem log), `docker exec -it <container> sh` (mở shell bên trong), `docker compose -p <project> ps` / `logs <service>`.
- Dùng `-p k5-day14-docker` để project này không trùng tên với các thư mục `production/` khác (Part 5, Part 7); dọn bằng `docker compose -p k5-day14-docker down`.

---

## Part 3: Cloud Deployment

> Theo hướng dẫn K5, Part 3 deploy luôn agent Part 6 (`06-lab-complete/`). Nền tảng chính: **Render** (Lab Coach cho phép dùng Render vì có gói free). Chi tiết: [DEPLOYMENT.md](DEPLOYMENT.md).

### Exercise 3.2: Deploy Render (nền tảng chính)

- **URL:** https://day14-agent-62gh.onrender.com
- **Cách làm:** Render Dashboard → New → Blueprint → chọn repo → *Blueprint Path* `06-lab-complete/render.yaml` (file không nằm ở gốc repo nên phải điền) → nhập `AGENT_API_KEY` (Render chỉ hỏi biến `sync: false` một lần lúc tạo Blueprint) → Deploy. Blueprint tạo 2 tài nguyên: web service `day14-agent` (Docker) và Key Value `day14-redis`; `REDIS_URL` của agent lấy tự động từ Key Value qua `fromService`.
- **Sửa khi deploy:** Render mặc định chờ app nghe cổng `10000`, còn image đặt `ENV PORT=8000` → thêm `PORT=10000` vào `envVars` của `render.yaml`; CMD `--port ${PORT:-8000}` tự nhận giá trị này.
- **4 lệnh tự kiểm (CP6):**

```text
$ curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/health"     → 200   {"status":"ok","environment":"production",...}
$ curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/ready"      → 200   {"ready":true}   (đã kết nối Redis trên Render)
$ curl ... -X POST "$BASE/ask" (không key)                     → 401
$ curl ... -X POST "$BASE/ask" -H "X-API-Key: $AGENT_API_KEY"  → 200
```

- Screenshot: [dashboard](screenshots/dashboard.png), [running](screenshots/running.png), [test](screenshots/test.png).

### Nền tảng còn lại: so sánh `railway.toml` và `render.yaml`

So sánh 2 file mẫu trong `03-cloud-deployment/` (và 2 file mình viết trong `06-lab-complete/`):

| | `railway.toml` (Railway) | `render.yaml` (Render Blueprint) |
|---|---|---|
| Định dạng | TOML, mô tả **1 service** | YAML, danh sách **nhiều service** (`services:`) — mô tả cả hạ tầng (web + Redis) trong 1 file |
| Build | `[build] builder = "NIXPACKS"` (tự đoán Python) hoặc `"DOCKERFILE"` | `runtime: python` + `buildCommand`, hoặc `runtime: docker` + `dockerfilePath` |
| Start | `[deploy] startCommand = "uvicorn app:app --port $PORT"` | `startCommand: uvicorn app:app --port $PORT` (bản docker dùng CMD trong Dockerfile) |
| Health check | `healthcheckPath = "/health"`, `healthcheckTimeout = 30` | `healthCheckPath: /health` |
| Restart | Khai báo rõ `restartPolicyType = "ON_FAILURE"`, `restartPolicyMaxRetries = 3` | Không khai báo; platform tự restart khi health check fail |
| Biến môi trường / secret | **Không** khai báo trong file; đặt bằng `railway variables set` / dashboard | Khai báo ngay trong file: giá trị thường ghi thẳng (`value:`), secret dùng `sync: false` (nhập tay) hoặc `generateValue: true` (Render tự sinh) |
| Redis | Không có trong file; thêm plugin Redis trên dashboard rồi tự nối `REDIS_URL` | Khai báo service `type: keyvalue` (bản mẫu dùng `type: redis` — tên cũ, nay là alias) và nối bằng `fromService ... property: connectionString` |
| Region / plan | Chọn trên dashboard | Ghi trong file: `region: singapore`, `plan: free` |
| Deploy | `railway up` từ máy (CLI upload code) | Kết nối GitHub, `autoDeploy: true` → mỗi lần push là Render tự build |

**Vì sao khác:** Railway thiên về "config cho một service", phần hạ tầng (database, biến) quản lý trên dashboard/CLI. Render Blueprint là **Infrastructure as Code** đầy đủ hơn: cả web service, Redis, cách nối chúng và cách xử lý secret đều nằm trong git, tạo lại toàn bộ môi trường chỉ bằng một file. Đổi lại Render chỉ hỏi secret `sync: false` lúc tạo Blueprint lần đầu, đổi về sau phải sửa tay trên dashboard.

### Exercise 3.3: Pipeline CI/CD Cloud Run (`03-cloud-deployment/production-cloud-run/`)

`cloudbuild.yaml` mô tả pipeline Cloud Build chạy khi push lên `main`, gồm 4 bước nối tiếp nhau bằng `waitFor`:
1. **test** — trong image `python:3.11-slim`, cài `requirements.txt` + `pytest` rồi chạy `pytest tests/`; test fail thì pipeline dừng, không build.
2. **build** — `docker build` image gắn 2 tag `gcr.io/$PROJECT_ID/ai-agent:$COMMIT_SHA` và `:latest`, dùng `--cache-from :latest` để tái dùng layer.
3. **push** — đẩy cả 2 tag lên Container Registry.
4. **deploy** — `gcloud run deploy` image theo đúng `$COMMIT_SHA` (biết chính xác commit nào đang chạy, rollback dễ) tới `asia-southeast1`, 1–10 instance, 512Mi / 1 CPU, timeout 60s, `ENVIRONMENT=production`, secret `OPENAI_API_KEY` lấy từ **Secret Manager** (`--set-secrets`) thay vì ghi trong file.

`service.yaml` là định nghĩa Knative của service: `minScale: 1` (luôn giữ 1 instance để tránh cold start), `maxScale: 10`, mỗi instance tối đa 80 request đồng thời, `livenessProbe` gọi `/health`, `startupProbe` gọi `/ready`, secret lấy qua `secretKeyRef`.

---

## Part 4: API Security

### Exercise 4.1: API key (`04-api-gateway/develop/app.py`)

- **Key được check ở đâu?** Trong dependency `verify_api_key` (dòng 39–54). `APIKeyHeader(name="X-API-Key", auto_error=False)` (dòng 36) đọc header; endpoint `/ask` gắn `Depends(verify_api_key)` (dòng 70) nên FastAPI chạy hàm này **trước** khi vào thân endpoint. `/health` và `/` không gắn nên vẫn public (platform cần gọi health check không cần key).
- **Sai key thì sao?** Thiếu header → **401** "Missing API key"; có header nhưng sai → **403** "Invalid API key". Request bị chặn trước khi gọi LLM, nên không tốn tiền. (Ghi chú: Part 6 yêu cầu sai key cũng trả 401.)
- **Rotate key thế nào?** Key đọc từ env `AGENT_API_KEY` (dòng 35): tạo key mới → đặt lại biến môi trường trên platform → restart/redeploy, không sửa code. Muốn không có downtime khi đổi key thì cho app chấp nhận đồng thời 2 key (cũ + mới) một thời gian, báo client chuyển sang key mới, rồi xoá key cũ. Lưu ý dòng 91 `print(f"API Key: {API_KEY}")` lại in key ra log — không nên làm.

Chạy `AGENT_API_KEY=secret-key-123 python app.py`:

```text
$ curl -i -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question":"Hello"}'
HTTP/1.1 401 Unauthorized
{"detail":"Missing API key. Include header: X-API-Key: <your-key>"}

$ curl -i -X POST http://localhost:8000/ask -H "Content-Type: application/json" -H "X-API-Key: wrong-key" -d '{"question":"Hello"}'
HTTP/1.1 403 Forbidden
{"detail":"Invalid API key."}

$ curl -i -X POST http://localhost:8000/ask -H "Content-Type: application/json" -H "X-API-Key: secret-key-123" -d '{"question":"Hello"}'
HTTP/1.1 200 OK
{"question":"Hello","answer":"Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé."}
```

### Exercise 4.2: JWT (`04-api-gateway/production/auth.py`)

**JWT flow:**
1. Client `POST /auth/token` với username/password → `authenticate_user` kiểm tra (dòng 70–75), sai → 401.
2. Đúng → `create_token` (dòng 35–43) tạo payload `{sub, role, iat, exp = now + 60 phút}` và **ký** bằng HMAC-SHA256 với `JWT_SECRET`.
3. Client gửi `Authorization: Bearer <token>` ở mọi request sau.
4. `verify_token` (dòng 46–67) kiểm tra chữ ký + hạn: không có token → 401, hết hạn → 401, chữ ký sai/token giả → 403. Lấy `username`, `role` từ payload mà **không cần tra database** (stateless).

Token gồm 3 phần `header.payload.signature`. Payload chỉ là base64, **không mã hoá**: giải mã token của mình được `{'sub': 'student', 'role': 'user', 'iat': 1795681418, 'exp': 1795685018}` (exp − iat = 3600 giây = 60 phút). Vì vậy không đặt dữ liệu bí mật trong payload; an toàn nằm ở chữ ký — sửa `role` thành `admin` thì chữ ký không khớp và server từ chối.

```text
$ curl -i -X POST http://localhost:8000/auth/token -H "Content-Type: application/json" -d '{"username":"student","password":"wrong"}'
HTTP/1.1 401 Unauthorized
{"detail":"Invalid credentials"}

$ curl -i -X POST http://localhost:8000/auth/token -H "Content-Type: application/json" -d '{"username":"student","password":"demo123"}'
HTTP/1.1 200 OK
{"access_token":"eyJ***MASKED***","token_type":"bearer","expires_in_minutes":60,"hint":"..."}

$ curl -i -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question":"Explain JWT"}'
HTTP/1.1 401 Unauthorized
www-authenticate: Bearer
{"detail":"Authentication required. Include: Authorization: Bearer <token>"}

$ curl -i -X POST http://localhost:8000/ask -H "Authorization: Bearer abc.def.ghi" -H "Content-Type: application/json" -d '{"question":"Explain JWT"}'
HTTP/1.1 403 Forbidden
{"detail":"Invalid token."}

$ TOKEN=<token ở trên>
$ curl -i -X POST http://localhost:8000/ask -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"question":"Explain JWT"}'
HTTP/1.1 200 OK
x-content-type-options: nosniff
x-frame-options: DENY
{"question":"Explain JWT","answer":"...","usage":{"requests_remaining":9,"budget_remaining_usd":2.1e-05}}
```

### Exercise 4.3: Rate limiting (`04-api-gateway/production/rate_limiter.py`)

- **Thuật toán:** **sliding window log** (cửa sổ trượt) — mỗi user có một `deque` lưu timestamp các request; mỗi lần gọi xoá timestamp cũ hơn 60 giây (dòng 39–40), nếu số còn lại ≥ limit thì 429 (dòng 45–62). Khác *fixed window* (đếm theo phút đồng hồ, cho phép dồn 2× request quanh mốc đổi phút) và *token bucket* (cho phép burst rồi nạp lại dần).
- **Limit:** `student` (role `user`) **10 request / 60 giây**; `teacher` (role `admin`) 100 request / 60 giây (dòng 86–87).
- **Bypass cho admin:** không bỏ hẳn kiểm tra mà chọn limiter theo role trong `app.py` dòng 141 `limiter = rate_limiter_admin if role == "admin" else rate_limiter_user` → admin dùng hạn mức 100/phút. Muốn bypass hoàn toàn thì bỏ qua `limiter.check()` khi `role == "admin"` (nên vẫn giữ một mức trần để token admin bị lộ cũng không phá được hệ thống).
- **Hạn chế:** state nằm trong RAM của process → chạy nhiều worker/instance thì mỗi cái đếm riêng, restart là mất. Part 6 phải chuyển sang Redis.

Vòng lặp 20 request bằng token của `student` (đã dùng 1 request ở 4.2):

```text
Request 1: HTTP 200 ... Request 9: HTTP 200
Request 10: HTTP 429  {"detail":{"error":"Rate limit exceeded","limit":10,"window_seconds":60,"retry_after_seconds":59}}
Request 11–20: HTTP 429

$ curl -i -X POST http://localhost:8000/ask -H "Authorization: Bearer $TOKEN" ...   # response đầy đủ khi bị chặn
HTTP/1.1 429 Too Many Requests
x-ratelimit-limit: 10
x-ratelimit-remaining: 0
x-ratelimit-reset: 1795681480
retry-after: 59
{"detail":{"error":"Rate limit exceeded","limit":10,"window_seconds":60,"retry_after_seconds":59}}
```

Đúng 10 request thành công trong 60 giây (1 ở 4.2 + 9 trong vòng lặp), request thứ 11 bị 429 kèm `Retry-After: 59`. Cùng lúc, `teacher` gửi 15 request liên tiếp đều 200 (limit 100). `student` gọi `/admin/stats` → 403 (đúng role mới được vào).

### Exercise 4.4: Cost guard

**Cách bản mẫu (`cost_guard.py`) làm:**
- Trước khi gọi LLM: `check_budget(user)` (dòng 60–91) — user đã tiêu ≥ `$1/ngày` → **402 Payment Required**; tổng toàn hệ thống ≥ `$10/ngày` → 503; dùng ≥ 80% → log warning.
- Sau khi gọi LLM: `record_usage` (dòng 93–110) cộng token và tính cost = `input/1000 × $0.00015 + output/1000 × $0.0006`.
- Reset theo ngày: `_get_record` tạo record mới khi `day` khác hôm nay.
- Đo thật: 10 request của student tốn `$0.000191` (`/me/usage`), ≈ $0.000019/request → cần khoảng 52 000 request mới chạm $1, nên trong lab không chạm được 402 (Part 6 test bằng budget rất nhỏ).

**Lỗi/hạn chế mình thấy:**
- Lưu trong dict RAM → nhiều instance đếm riêng, restart là reset về 0 (người dùng có thể "tiêu lại" budget).
- `app.py` dòng 160 trả `"budget_remaining_usd": usage.total_cost_usd` — đó là số **đã dùng**, không phải số **còn lại** (thấy `2.1e-05` sau request đầu tiên).

**Cách mình implement (Redis, budget $10/tháng mỗi user):**

```python
import os
import redis
from datetime import datetime, timezone
from fastapi import HTTPException

r = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True)
MONTHLY_BUDGET_USD = float(os.getenv("MONTHLY_BUDGET_USD", "10"))

def _key(user_id: str) -> str:
    return f"budget:{user_id}:{datetime.now(timezone.utc):%Y-%m}"   # vd budget:alice:2026-11 → sang tháng là key mới = reset

def check_budget(user_id: str, estimated_cost: float) -> None:
    spent = float(r.get(_key(user_id)) or 0)
    if spent + estimated_cost > MONTHLY_BUDGET_USD:
        raise HTTPException(402, detail={"error": "Monthly budget exceeded",
                                         "used_usd": round(spent, 6), "budget_usd": MONTHLY_BUDGET_USD})

def record_cost(user_id: str, cost: float) -> float:
    key = _key(user_id)
    pipe = r.pipeline()
    pipe.incrbyfloat(key, cost)          # cộng nguyên tử, nhiều instance cùng ghi không mất số
    pipe.expire(key, 32 * 24 * 3600)     # key tự xoá sau ~1 tháng
    total, _ = pipe.execute()
    return float(total)
```

- **Vì sao Redis:** mọi instance/worker đọc ghi chung một chỗ, restart app không mất số đã tiêu; `INCRBYFLOAT` là nguyên tử nên 2 request đồng thời không ghi đè nhau.
- **Reset đầu tháng:** tên key chứa `YYYY-MM`, sang tháng mới tự dùng key mới (bắt đầu từ 0); `EXPIRE 32 ngày` dọn key cũ.
- **Check trước, ghi sau:** check bằng cost ước tính trước khi gọi LLM (chặn sớm, không tốn tiền), ghi cost **thật** theo token sau khi LLM trả về.

### Checkpoint 4
- **Rate limit bảo vệ tốc độ, cost guard bảo vệ tiền — vì sao cần cả hai?** Rate limit chặn *số request mỗi phút* (chống spam, DDoS, một user chiếm hết tài nguyên) nhưng không biết mỗi request tốn bao nhiêu: user gửi 10 câu/phút nhưng mỗi câu prompt rất dài vẫn đốt hết tiền trong ngày. Cost guard chặn theo *tổng tiền* trong ngày/tháng nhưng không chặn được burst: trong giới hạn tiền vẫn có thể gửi 1000 request/giây làm sập server. Hai lớp bổ sung cho nhau: auth (ai được gọi) → rate limit (gọi nhanh tới đâu) → cost guard (tiêu tới đâu).

---

## Part 5: Scaling & Reliability

### Exercise 5.1: Health check và readiness (`05-scaling-reliability/develop/app.py`)

- `GET /health` (dòng 104–144) — **liveness**: chỉ hỏi "process còn sống không?", luôn 200 khi process chạy; kèm `uptime`, `version`, `environment` và kiểm tra RAM bằng `psutil` (`degraded` khi > 90%).
- `GET /ready` (dòng 147–168) — **readiness**: trả 503 khi `_is_ready = False` (đang khởi động hoặc đang tắt), 200 khi sẵn sàng; kèm số request đang xử lý.
- `_is_ready` được bật ở cuối phần startup của `lifespan` (dòng 49) và tắt ngay khi bắt đầu shutdown (dòng 55) → load balancer ngừng gửi request mới vào instance sắp tắt.

```text
$ curl -i http://localhost:8000/health
HTTP/1.1 200 OK
{"status":"ok","uptime_seconds":0.6,"version":"1.0.0","environment":"development","timestamp":"...","checks":{"memory":{"status":"ok","used_percent":13.3}}}

$ curl -i http://localhost:8000/ready
HTTP/1.1 200 OK
{"ready":true,"in_flight_requests":1}      ← 1 chính là request /ready đang được xử lý
```

**Liveness khác readiness:** liveness fail → platform **restart** container (process treo/chết). Readiness fail → **không restart**, chỉ tạm không gửi traffic (đang khởi động, đang tắt, mất DB/Redis). Nếu đặt kiểm tra Redis vào `/health`, Redis chết một lúc là mọi instance bị restart liên tục mà không giải quyết được gì.

### Exercise 5.2: Graceful shutdown

Cơ chế trong code:
1. `uvicorn.run(..., timeout_graceful_shutdown=30)` (dòng 193–199): nhận SIGTERM thì ngừng nhận kết nối mới, chờ tối đa 30 giây cho request đang chạy.
2. Middleware `track_requests` (dòng 72–81) đếm `_in_flight_requests`.
3. `lifespan` phần shutdown (dòng 54–66): đặt `_is_ready = False`, chờ in-flight về 0 (tối đa 30 giây), rồi đóng tài nguyên.

Test: gửi request rồi `kill -TERM` sau 0,05 giây:

```text
in-flight request → HTTP 200
{"answer":"Đây là câu trả lời từ AI agent (mock). ..."}
process exit code: 0

--- log
INFO:     Shutting down                                         ← nhận SIGTERM, ngừng nhận kết nối mới
INFO:     127.0.0.1:52192 - "POST /ask?question=Long%20task HTTP/1.1" 200 OK   ← request đang chạy vẫn hoàn thành
INFO:     Waiting for application shutdown.
INFO 🔄 Graceful shutdown initiated...
INFO ✅ Shutdown complete
INFO:     Finished server process [3279]
INFO Received signal 15 — uvicorn will handle graceful shutdown
```

**Quan sát:** request nhận 200 dù SIGTERM đến trước khi nó xong, process thoát với exit code 0. Dòng `Received signal 15` của `handle_sigterm` lại in ra **sau cùng**: khi server chạy, uvicorn thay handler SIGTERM bằng handler của nó; lúc tắt xong mới trả lại handler cũ và phát lại tín hiệu. Vì vậy nếu tự đăng ký handler SIGTERM mà không gọi lại handler của uvicorn thì uvicorn không bao giờ biết để tắt (lưu ý cho Part 6).

### Exercise 5.3: Stateless design (`05-scaling-reliability/production/app.py`)

- **Anti-pattern:** `conversation_history = {}` trong RAM. Chạy 3 instance thì có 3 dict riêng: request 1 của user vào instance A (lưu ở A), request 2 vào instance B → B không có lịch sử. Restart/deploy là mất hết.
- **Cách bản production làm:** mọi state nằm trong Redis với key `session:<session_id>`:
  - `save_session` (dòng 59–65): `SETEX session:<id> 3600 <json>` — lưu kèm TTL 1 giờ, session cũ tự xoá.
  - `load_session` (dòng 68–73): `GET session:<id>`.
  - `append_to_history` (dòng 76–90): đọc → thêm message → giữ tối đa 20 message → ghi lại.
  - Client giữ `session_id` và gửi lại ở mỗi request; instance nào nhận cũng đọc được từ Redis.
- **Hạn chế mình thấy:**
  - Nếu không kết nối được Redis lúc khởi động (dòng 35–45) thì app **âm thầm chuyển sang dict trong RAM** — chạy vẫn được nhưng không còn stateless; production nên dừng hẳn hoặc `/ready` trả 503.
  - `append_to_history` là đọc–sửa–ghi không nguyên tử: 2 request cùng session tới 2 instance cùng lúc có thể ghi đè nhau mất 1 message (dùng `RPUSH` + `LTRIM` trên Redis list sẽ an toàn hơn).
  - `turn` bị lệch 1: tính sau khi đã thêm câu hỏi hiện tại vào history nên câu đầu tiên báo `turn 2`.

### Exercise 5.4: Load balancing (3 instance + Nginx)

```text
$ cd 05-scaling-reliability/production && touch .env.local
$ docker compose -p k5-day14-scaling up -d --build --scale agent=3
NAME                       STATUS                    PORTS
k5-day14-scaling-agent-1   Up 13 seconds (healthy)   8000/tcp
k5-day14-scaling-agent-2   Up 12 seconds (healthy)   8000/tcp
k5-day14-scaling-agent-3   Up 12 seconds (healthy)   8000/tcp
k5-day14-scaling-nginx-1   Up 11 seconds             0.0.0.0:8080->80/tcp
k5-day14-scaling-redis-1   Up 23 seconds (healthy)   6379/tcp
```

10 request `POST /chat` qua `http://localhost:8080`:

```text
Request 1  served_by: instance-fc64ae  X-Served-By: 172.21.0.3:8000
Request 2  served_by: instance-a580b3  X-Served-By: 172.21.0.5:8000
Request 3  served_by: instance-fc64ae  X-Served-By: 172.21.0.3:8000
Request 4  served_by: instance-e30256  X-Served-By: 172.21.0.4:8000
Request 5  served_by: instance-a580b3  X-Served-By: 172.21.0.5:8000
Request 6  served_by: instance-fc64ae  X-Served-By: 172.21.0.3:8000
Request 7  served_by: instance-e30256  X-Served-By: 172.21.0.4:8000
Request 8  served_by: instance-a580b3  X-Served-By: 172.21.0.5:8000
Request 9  served_by: instance-fc64ae  X-Served-By: 172.21.0.3:8000
Request 10 served_by: instance-e30256  X-Served-By: 172.21.0.4:8000

$ docker compose -p k5-day14-scaling logs agent | grep 'POST /chat'   → agent-1: 4, agent-2: 3, agent-3: 3
```

**Giải thích:** `upstream agent_cluster { server agent:8000; }` — Docker DNS trả về IP của cả 3 container cho tên `agent`, Nginx chia **round-robin** (10 request → 4/3/3). Header `X-Served-By` (`$upstream_addr`) cho biết request đi tới IP nào. `proxy_next_upstream error timeout http_503` khiến Nginx tự thử instance khác khi một instance lỗi.

### Exercise 5.5: Test stateless

`python test_stateless.py` (5 câu trong cùng một session):

```text
Session ID: 61fb081c-4059-4f49-a381-57f233df9b51
Request 1: [instance-a580b3]  Q: What is Docker?
Request 2: [instance-fc64ae]  Q: Why do we need containers?
Request 3: [instance-e30256]  Q: What is Kubernetes?
Request 4: [instance-a580b3]  Q: How does load balancing work?
Request 5: [instance-fc64ae]  Q: What is Redis used for?
Instances used: {'instance-e30256', 'instance-a580b3', 'instance-fc64ae'}
✅ All requests served despite different instances!
--- Conversation History ---
Total messages: 10      (5 user + 5 assistant, đúng thứ tự)
```

**Giải thích kết quả:** 5 câu của cùng một session đi qua cả 3 instance khác nhau, nhưng history vẫn đủ 10 message theo đúng thứ tự. Lý do: không instance nào giữ history trong RAM; mỗi instance đọc `session:<id>` từ Redis, thêm message rồi ghi lại, nên request tiếp theo vào instance nào cũng thấy đủ lịch sử.

**Thử thêm: tắt 1 instance giữa cuộc trò chuyện** (script mẫu không tự tắt instance nên mình tự làm):

```text
turn1 served_by instance-a580b3              (câu đầu: "My name is ...")
$ docker stop k5-day14-scaling-agent-1       (agent-1 = instance-fc64ae)
turn 3 served_by instance-e30256
turn 4 served_by instance-a580b3
turn 5 served_by instance-e30256
history count: 8                             (4 user + 4 assistant, có cả câu trước khi tắt instance)
```

Sau khi tắt agent-1, không request nào lỗi, traffic tự dồn sang 2 instance còn lại, session vẫn còn nguyên. Đây là lợi ích chính của stateless: instance có thể chết, được thay hoặc scale thêm bất kỳ lúc nào mà người dùng không mất dữ liệu. Dọn dẹp: `docker compose -p k5-day14-scaling down`.

### Checkpoint 5
- Đã chạy health/readiness, graceful shutdown (request đang chạy vẫn 200, exit 0), stateless với Redis, load balancing 3 instance, và chứng minh session còn nguyên khi tắt 1 instance.
- **Vì sao state trong RAM hỏng khi chạy nhiều instance?** Mỗi instance là một process riêng với bộ nhớ riêng; load balancer gửi các request của cùng một user tới các instance khác nhau, nên state nằm ở instance A thì instance B không thấy; instance bị restart/scale xuống là state mất. Đưa state ra một kho dùng chung (Redis) thì mọi instance như nhau và thay thế được cho nhau.

---

## Part 6: Final Project (`06-lab-complete/`)

### Kết quả `python check_production_ready.py` (bản đầy đủ, WSL2 Ubuntu + Docker)

Trước khi sửa: `0/12 tiêu chí đạt · 5/24 checks passed`. Sau khi sửa:

```text
================================================================
  📋 Checklist Deliverable
     ✅ Dockerfile (multi-stage, < 500 MB)
     ✅ docker-compose.yml (agent + redis)
     ✅ .dockerignore
     ✅ Health check endpoint (GET /health)
     ✅ Readiness endpoint (GET /ready)
     ✅ API Key authentication
     ✅ Rate limiting
     ✅ Cost guard
     ✅ Config từ environment variables
     ✅ Structured logging
     ✅ Graceful shutdown
     ✅ Public URL ready (Railway / Render config)
----------------------------------------------------------------
  Result: 12/12 tiêu chí đạt · 43/43 checks passed
  🎉 PASS 12/12 — PRODUCTION READY! Deploy nào!
================================================================
```

Chi tiết đáng chú ý trong output: image `273 MB`, build `48s`; rate limit `429 sau 5 request thành công (RATE_LIMIT_PER_MINUTE=5)` dù chạy 2 worker; restart agent vẫn bị 429; cost guard `402 ở request thứ 1` với budget 0.000001 USD; `17 dòng JSON`; SIGTERM lúc đang có request → `exit code 0`; `docker stop` → `exit code 0, 0.9s`.

### Cách làm từng tiêu chí

| # | Tiêu chí | Cách làm (file) |
|---|---|---|
| 9 | Config từ env | `app/config.py`: dataclass `Settings`, mỗi field `default_factory=lambda: os.getenv(...)`. `AGENT_API_KEY` mặc định rỗng → không có key thì mọi request bị 401 (fail closed). `.env.example` liệt kê đủ biến, không có giá trị thật |
| 10 | Log JSON | `app/main.py`: `JsonFormatter` tự viết dùng `json.dumps` (escape đúng dấu `"`), middleware ghi 1 dòng/request: `request_id, method, path, status, ms`. Không log header/body → không lộ key (kiểm tra: grep key trong log = 0 dòng) |
| 4 | `/health` | Luôn 200, không gọi Redis |
| 5 | `/ready` | `redis_healthy()` = `PING` Redis; lỗi → 503. Cũng trả 503 khi đang tắt (`STATE["ready"] = False` sau `yield` của lifespan) |
| 6 | API key | `app/auth.py`: `APIKeyHeader(name="X-API-Key", auto_error=False)` để tự trả **401** (nếu khai báo header bắt buộc thì FastAPI trả 422). So sánh bằng `secrets.compare_digest` để không đoán được key qua thời gian phản hồi |
| 7 | Rate limit | `app/rate_limiter.py`: sorted set Redis `ratelimit:<sha256(key)[:16]>`, trong 1 transaction `MULTI`: `ZREMRANGEBYSCORE` (xoá request > 60s) → `ZADD` (thêm request này) → `ZCARD` (đếm) → `EXPIRE`. Đếm > limit → xoá lại request vừa thêm, trả 429 + `Retry-After` (tính từ request cũ nhất). Chạy nguyên tử nên 2 worker cùng lúc không lọt quá giới hạn; lưu hash thay vì key thô |
| 8 | Cost guard | `app/cost_guard.py`: `check_budget()` **trước** khi gọi LLM: `MGET cost:day:YYYY-MM-DD cost:month:YYYY-MM`, chạm `DAILY_BUDGET_USD` hoặc `MONTHLY_BUDGET_USD` → 402. `record_cost()` **sau** khi gọi: `INCRBYFLOAT` cả 2 key (nguyên tử) + `EXPIRE`. Tên key chứa ngày/tháng → tự reset |
| 11 | Graceful shutdown | Dùng `lifespan` của FastAPI; **không** tự đăng ký handler SIGTERM (Part 5 cho thấy uvicorn thay handler, tự viết mà không gọi lại sẽ làm uvicorn không tắt). uvicorn nhận SIGTERM → ngừng nhận kết nối → chờ request đang chạy → chạy phần sau `yield` (log `shutdown`, đóng pool Redis) |
| 1 | Dockerfile | Multi-stage: `builder` tạo venv `/opt/venv` và `pip install`; `runtime` (`python:3.11-slim`) chỉ copy `/opt/venv` + `app/` + `utils/`, user `app` (non-root), `HEALTHCHECK` gọi `/health` theo `PORT`. `CMD ["sh","-c","exec uvicorn ... --port ${PORT:-8000} --workers 2"]`: `exec` làm uvicorn thành PID 1 nên nhận SIGTERM khi `docker stop`. Dùng venv thay vì `pip install --user` để không bị lỗi user non-root không thấy package |
| 3 | `.dockerignore` | Loại `.env*` (giữ `.env.example`), `__pycache__`, `venv`, `.git`, tài liệu, `benchmark-observability/` |
| 2 | Compose | `agent` build từ Dockerfile, `env_file: .env (required: false)`, `REDIS_URL=redis://redis:6379/0`, `depends_on: redis: condition: service_healthy`; `redis:7-alpine` có healthcheck `redis-cli ping` và volume |
| 12 | Deploy config | `render.yaml` (nền tảng chính): web service `runtime: docker`, `healthCheckPath: /health`, `AGENT_API_KEY` dùng `sync: false`, `REDIS_URL` lấy từ Key Value `day14-redis`. `railway.toml`: `builder = "DOCKERFILE"`, `healthcheckPath = "/health"` |

### Test bằng tay với Docker Compose

`docker compose -p k5-day14-final up -d --build` (file `.env` tạm có key ngẫu nhiên và `RATE_LIMIT_PER_MINUTE=3`):

```text
GET  /health                         → 200 {"status":"ok","environment":"development","uptime_seconds":0.2}
GET  /ready                          → 200 {"ready":true}
POST /ask (không key)                → 401 {"detail":"Missing or invalid API key. Send header X-API-Key: <key>"}
POST /ask (key sai)                  → 401
POST /ask (key đúng)                 → 200 {"question":"What is deployment?","answer":"Deployment là quá trình ...","usage":{"input_tokens":4,"output_tokens":19,"cost_usd":1.2e-05}}
POST /ask {"invalid":"data"}         → 422 {"detail":[{"type":"missing","loc":["body","question"],"msg":"Field required",...}]}
POST /ask '{bad json'                → 422 {"detail":[{"type":"json_invalid",...,"msg":"JSON decode error",...}]}
POST /ask lần 2, 3, 4 (limit 3/phút)  → 200, 200, 429 (retry-after: 60)

docker compose stop redis:
GET  /health                         → 200           ← process vẫn sống, không bị restart oan
GET  /ready                          → 503 {"ready":false,"reason":"redis unavailable"}   ← LB ngừng gửi traffic
POST /ask                            → 503 {"detail":"Storage unavailable, retry later"}  ← không lộ stack trace
```

Log JSON thật của agent:

```json
{"ts": "2026-11-26T08:43:46.684640+00:00", "lvl": "INFO", "logger": "agent", "event": "request", "request_id": "a59db4194796", "method": "GET", "path": "/health", "status": 200, "ms": 1.5}
{"ts": "2026-11-26T08:43:52.816787+00:00", "lvl": "ERROR", "logger": "agent", "event": "redis_error", "path": "/ask", "error": "ConnectionError"}
```

**Vì sao `/health` phải 200 khi Redis chết còn `/ready` phải 503?** Redis chết không phải lỗi của process agent, restart agent không làm Redis sống lại. Nếu `/health` phụ thuộc Redis, platform sẽ restart mọi instance liên tục (restart loop) trong khi vấn đề nằm ở Redis. `/ready` 503 thì load balancer chỉ tạm ngừng gửi traffic, khi Redis trở lại `/ready` về 200 và instance tự nhận traffic lại mà không cần restart.

---

## AI log

| Lần | Hỏi AI (Claude Code) | Nhận được | Mình tự kiểm tra thế nào |
|---|---|---|---|
| 1 | Đọc repo, liệt kê việc phải làm hôm nay | Tóm tắt các checkpoint, rubric, lưu ý K5 | Đối chiếu lại với `RUBRIC.md`, `CHECKPOINTS.md` |
| 2 | Kiểm tra máy có WSL2/Docker chưa, cài Ubuntu + Python 3.11 (uv) | Cài Ubuntu WSL2, uv, Python 3.11 | Chạy `python -c "import os; print(os.name)"` → `posix`; `docker version` trong Ubuntu |
| 3 | Part 1: tìm anti-pattern, chạy 2 bản, so sánh | Bảng anti-pattern có số dòng, output curl thật | Tự chạy lại curl, đọc lại từng dòng `develop/app.py` |
| 4 | Giải thích 4 câu Checkpoint 1, viết script demo curl | `curl_demo.sh` chạy 2 bản app, đổi env var, thử SIGTERM | Chạy script, đối chiếu từng output với phần giải thích |
| 5 | Part 2: build 2 image, đo kích thước, chạy compose qua Nginx | Lệnh build/run, số byte từ `docker image inspect`, sơ đồ stack | Tự chạy `docker images my-agent`, tự tính % chênh lệch |
| 6 | Part 4: test API key, JWT, vòng lặp rate limit, giải thích cost guard | Output `curl -i` 401/403/200/429 (token đã che), đoạn code cost guard Redis | Tự chạy vòng lặp thấy 429 ở request thứ 11; tự giải mã payload JWT |
| 7 | Part 5: test health/ready, SIGTERM, scale 3 instance, test stateless, tắt 1 instance | Output thật, phân tích hạn chế của code mẫu | Lần đầu script đọc nhầm lỗi 502 khi agent chưa sẵn sàng; sửa để chờ đủ 3 instance healthy rồi chạy lại |
| 8 | Part 6: viết code các TODO (config, auth, rate limit Redis, cost guard Redis, log JSON, health/ready, Dockerfile, compose, render.yaml) | Code từng file + giải thích, chạy `check_production_ready.py` bản đầy đủ | PASS 12/12 (43/43 checks); tự test bằng curl qua compose, tắt Redis xem `/health` 200 còn `/ready` 503; grep key trong log = 0 |
| 9 | Part 3: đối chiếu `render.yaml` với tài liệu Render, viết DEPLOYMENT.md, so sánh railway/render, mô tả pipeline Cloud Run | Phát hiện Render chờ cổng 10000 → thêm `PORT=10000`; nội dung so sánh và tóm tắt pipeline | Tự deploy Blueprint trên Render, tự chạy 4 lệnh tự kiểm trên URL public |
| 10 | Part 7 Bước 1: thêm metric độ dài câu hỏi, chạy load test 4 mức + 3 mức `MOCK_SLOW_RATE`, eval v1/v2, sửa KB, viết job CI, điền BENCHMARK_REPORT phần 1–3 | Script chạy toàn bộ lệnh, phân tích số liệu, nội dung 2 entry KB, file `.github/workflows/eval.yml` | Mọi số trong report lấy thẳng từ JSON trong `reports/`; eval cuối 23/23, 0 regression; v1 so với baseline v2 cho exit code 1 |
