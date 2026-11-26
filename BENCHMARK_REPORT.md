# Benchmark & Observability Report — Day 14

> Mọi con số lấy từ report JSON trong [`reports/`](reports/) (bản gốc ở `06-lab-complete/benchmark-observability/develop/reports/`), CSV trong [`benchmark-results/`](benchmark-results/) và screenshot thật.

**Họ tên:** Nguyen Huy Tan
**Ngày chạy:** 2026-11-26
**Môi trường:** Laptop Windows 11, AMD Ryzen 7 8745H (16 luồng), 14 GB RAM; chạy trong WSL2 Ubuntu 26.04 (6 GB RAM cấp cho WSL), Python 3.11.18, Docker Desktop 29.9.3. Agent (1 process uvicorn) và công cụ tạo tải chạy **trên cùng một máy**.

---

## 1. SLO của agent

| SLI | SLO | Lý do chọn ngưỡng này |
|---|---|---|
| p95 latency `/ask` | < 500 ms | Latency nền của 1 câu ~190–220 ms (p50 đo được 193 ms); 500 ms cho dư ~2,5 lần để chịu tải tăng và nhiễu, mà người dùng vẫn thấy phản hồi gần như tức thì. Đặt theo p95 chứ không theo average để 95% người dùng thật sự được đảm bảo |
| Error rate `/ask` | < 2 % | Mock LLM upstream lỗi ngẫu nhiên ~1% (`MOCK_ERROR_RATE=0.01`); đặt gấp đôi để có error budget cho spike ngắn, vượt 2% nghĩa là có vấn đề thật chứ không phải nhiễu |
| Accuracy trên golden dataset | ≥ 85 % và **không regression** | Trùng ngưỡng của job CI (`--min-accuracy 0.85`). Kèm điều kiện không câu nào đúng ở bản trước lại sai ở bản mới, vì accuracy tổng có thể giữ nguyên trong khi đã hỏng một nhóm câu cụ thể |

---

## 2. Benchmark hiệu năng (develop, `load_test.py`)

### 2.0 Đọc metrics của agent (Exercise 7.1)

Sau 1 request `/ask` tới v2, `curl localhost:8002/metrics`:

| Loại | Ví dụ thật trong output | Ý nghĩa |
|---|---|---|
| **Counter** (chỉ tăng) | `http_requests_total{method="POST",route="/ask",status="200"} 1.0`, `llm_tokens_total{model="mock-llm-small",type="input"} 91.0` | Đếm dồn; dùng với `rate()` để ra req/s, token/s |
| **Gauge** (lên xuống) | `agent_in_flight_requests 0.0`, `agent_info{model="mock-llm-small",version="v2"} 1.0` | Giá trị tức thời (số request đang xử lý) |
| **Histogram** (bucket) | `http_request_duration_seconds_bucket{le="0.2",method="POST",route="/ask"} 1.0` (+ `_count`, `_sum`) | Đếm số request rơi vào từng ngưỡng latency → `histogram_quantile()` tính p95/p99 |

**Vì sao label `route` dùng `route.path` chứ không dùng URL thật?** Mỗi tổ hợp giá trị label là một time series riêng trong Prometheus. Dùng `request.url.path` thì mỗi URL rác mà bot quét gọi (`/wp-admin`, `/.env`, `/a1b2c3`...) tạo thêm một series mới không bao giờ mất → hàng nghìn/hàng triệu series, Prometheus tốn RAM tới mức sập, query chậm (cardinality explosion). `route.path` là *template* của route đã khai báo (vd `/ask`) nên số giá trị luôn hữu hạn; URL không khớp route nào đều gộp chung vào `unmatched`.

**Metric thêm vào:** `agent_question_length_chars` (Histogram, bucket 20/50/100/200/500/1000/2000) trong `develop/app.py`, gọi `QUESTION_LENGTH.observe(len(body.question))` trong `/ask`. Sau request thử (câu 40 ký tự): `agent_question_length_chars_bucket{le="50.0"} 1.0`, `_sum 40.0`, `_count 1.0`. Dùng Histogram chứ không dùng label `length=...` vì độ dài có vô số giá trị → mỗi giá trị một series → lại bùng nổ cardinality.

### 2.1 Concurrency vs latency (agent v2)

`python load_test.py --url http://localhost:8002 --requests 300 --concurrency <c> --out reports/load_v2_c<c>.json` (cấu hình mock mặc định: `MOCK_SLOW_RATE=0.03`, `MOCK_ERROR_RATE=0.01`).

| Concurrency | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Error rate | SLO |
|---|---|---|---|---|---|---|
| 1 | 3.99 | 192.7 | 224.5 | 1717.2 | 0.67% (2×503) | ✅ PASS |
| 10 | 38.12 | 192.9 | 221.8 | 1693.7 | 2.00% (6×503) | ✅ PASS (sát ngưỡng lỗi) |
| 50 | 97.77 | 209.9 | 608.8 | 1766.5 | 1.00% (3×503) | ❌ FAIL (p95) |
| 100 | 42.80 | 569.4 | 4577.1 | 5062.4 | 1.33% (4×503) | ❌ FAIL (p95) |

Nguồn: `reports/load_v2_c1.json`, `load_v2_c10.json`, `load_v2_c50.json`, `load_v2_c100.json`.

**Nhận xét:** RPS **không tăng tuyến tính**.
- Từ 1 → 10: gần tuyến tính (3.99 → 38.12, ×9.6) và p50 gần như không đổi (192.7 → 192.9 ms). Mỗi request chủ yếu là *chờ* LLM (`asyncio.sleep`), server async chồng được nhiều request đang chờ lên nhau nên thêm song song là thêm throughput.
- Từ 10 → 50: chỉ ×2.6 (38 → 98 RPS), p95 bắt đầu vọt lên 608.8 ms. Một process uvicorn chỉ có 1 event loop / 1 lõi CPU cho phần việc thật (parse JSON, middleware, metrics, ghi log), lại phải chia CPU với chính công cụ tạo tải chạy cùng máy → bắt đầu bão hoà.
- Ở 100: RPS **giảm** còn 42.8 trong khi p50 = 569 ms, p95 = 4.6 s. Server đã quá tải, request phải xếp hàng chờ: theo định luật Little, 100 request đang trong hệ thống / 42.8 req/s ≈ 2.3 s chờ trung bình mỗi request (avg đo được 1.5 s). Tăng song song quá điểm bão hoà không tăng throughput mà chỉ đẩy latency lên. Muốn chịu 100 user phải scale ngang (nhiều worker/instance), không phải tăng concurrency.

### 2.2 Tail latency

Khởi động lại v2 với từng giá trị, cùng `--requests 300 --concurrency 20`:

| `MOCK_SLOW_RATE` | avg (ms) | p95 (ms) | p99 (ms) |
|---|---|---|---|
| 0 | 193.3 | 221.9 | 236.5 |
| 0.03 (mặc định) | 218.0 | 222.9 | 1700.9 |
| 0.1 | 345.4 | 1693.0 | 1716.5 |

Nguồn: `reports/load_v2_slow_0.json`, `load_v2_slow_0.03.json`, `load_v2_slow_0.1.json`.

**Nhận xét:**
- Với 3% request chậm (+1500 ms): **avg chỉ tăng 24.7 ms** (193.3 → 218.0, +13%) và p95 gần như không đổi (221.9 → 222.9), nhưng **p99 tăng 7,2 lần** (236.5 → 1700.9 ms). 3% < 5% nên p95 chưa "chạm" tới nhóm chậm, chỉ p99 thấy được.
- Với 10% request chậm: nhóm chậm đã vượt 5% nên **p95 cũng nhảy lên 1693 ms** (gấp 7,6 lần), trong khi avg chỉ tăng 152 ms (+79%). SLO p95 < 500 ms FAIL, còn nếu nhìn avg 345 ms thì vẫn tưởng là đạt.
- **Chỉ số nhạy nhất với request chậm là percentile cao (p99, rồi p95)**. Average là trung bình cộng nên vài request cực chậm bị "pha loãng" bởi số đông request nhanh: avg 218 ms trông ổn trong khi cứ 100 người thì 3 người chờ ~1,7 s. SLO đặt theo average sẽ che giấu đúng nhóm người dùng đang gặp vấn đề; đặt theo p95/p99 thì cam kết với cả những người ở "đuôi" phân bố.

---

## 3. Benchmark chất lượng (develop, `eval/run_eval.py`)

Eval chạy với `MOCK_ERROR_RATE=0` để lỗi upstream ngẫu nhiên không làm sai lệch accuracy (theo gợi ý trong TROUBLESHOOTING).

| Chỉ số | Agent v1 | Agent v2 | Agent v2 (sau khi sửa KB) |
|---|---|---|---|
| Accuracy | 4.3% (1/23) | 91.3% (21/23) | 100.0% (23/23) |
| Avg tokens / câu | 57.2 | 109.0 | 116.5 |
| Tổng cost (USD) | 0.000405 | 0.000759 | 0.000815 |
| Cost / câu đúng (USD) | 0.00040515 | 0.00003616 | 0.00003544 |
| Latency p50 / p95 (ms) | 167.5 / 186.6 | 191.1 / 224.6 | 197.5 / 219.1 |
| Quality gate | ❌ FAIL (min 80%) | ✅ PASS (min 80%, baseline v1: 0 regression) | ✅ PASS (min 100%, baseline v2: 0 regression) |

Nguồn: `reports/eval_v1.json`, `reports/eval_v2.json`, `reports/eval_v2_fixed.json`.

**Những câu v2 trả lời sai ban đầu và cách bạn sửa:**

| ID | Keyword bị thiếu | Bạn đã sửa gì |
|---|---|---|
| `security-02` — "JWT khác gì API key trong xác thực?" | `token`, `hết hạn`, `chữ ký` | Retrieval không tìm được tài liệu nào (KB không có từ khoá `jwt`) nên agent trả câu chung chung. Thêm entry `(["jwt", "json web token"], ...)` vào `KNOWLEDGE_BASE`: JWT là token server ký bằng chữ ký số, chứa user/role/thời điểm hết hạn; API key là chuỗi cố định, không tự hết hạn |
| `scaling-03` — "Kubernetes HPA scale dựa trên những chỉ số nào?" | `cpu`, `memory`, `custom metrics` | Tương tự, KB không có gì về HPA. Thêm entry `(["hpa", "autoscal"], ...)`: HPA tăng giảm số pod theo CPU, memory hoặc custom metrics (request/giây, độ dài hàng đợi) |

Từ khoá kích hoạt chọn hẹp (`jwt`, `hpa`, `autoscal`) — không câu nào khác trong dataset chứa các từ này nên không đổi tài liệu được chọn cho 21 câu đang đúng. Kết quả: 2 câu được cải thiện, **0 regression**. Thay đổi cũng được chép sang `production/utils/mock_llm.py` để stack Docker ở Bước 2 dùng cùng knowledge base.

**Quality gate trong CI:** [`.github/workflows/eval.yml`](.github/workflows/eval.yml) — mỗi pull request sửa code `develop/` sẽ bật agent v2 rồi chạy `run_eval.py --min-accuracy 0.85 --baseline reports/eval_v2_fixed.json`. `run_eval.py` thoát với exit code 1 khi accuracy dưới ngưỡng hoặc có câu bị regression → step fail → job fail; bật branch protection "Require status checks to pass" thì PR không merge được. Kiểm chứng: chạy eval v1 với `--baseline reports/eval_v2.json` → in `⚠️ Câu bị regression : 20`, `Quality gate ... ❌ FAIL`, **exit code 1**.

**Nhận xét về trade-off chất lượng / chi phí / latency giữa v1 và v2:**
- **Chất lượng:** v2 thêm bước retrieval (RAG) nên accuracy từ 4.3% lên 91.3% (100% sau khi bổ sung KB). v1 chỉ trả lời đúng câu "Deploy nghĩa là gì?" nhờ câu trả lời có sẵn.
- **Chi phí:** prompt v2 dài hơn vì nhét thêm tài liệu → tokens/câu gần gấp đôi (57.2 → 109.0, +91%) và tổng cost ×1,87. Nhưng **cost / câu đúng rẻ hơn 11,2 lần** (0.00040515 → 0.00003616 USD): v1 rẻ mỗi lần gọi nhưng gần như mọi lần gọi đều vô ích. So 2 phiên bản phải nhìn cost trên mỗi câu trả lời *dùng được*, không nhìn cost mỗi request.
- **Latency:** v2 chậm hơn ~38 ms ở p95 (186.6 → 224.6 ms) do bước retrieval (~20 ms) và prompt dài hơn (latency mock tăng theo số input token) — vẫn trong SLO 500 ms.
- Kết luận: v2 đáng đổi. Sửa KB tăng thêm 7.5 tokens/câu nhưng cost / câu đúng còn giảm nhẹ (0.00003616 → 0.00003544) vì số câu đúng tăng.

---

## 4. Observability stack (production)

Stack: `docker compose -p k5-day14-observability up -d --build` trong `06-lab-complete/benchmark-observability/production/` (agent + Nginx :8080 + Prometheus :9090 + Grafana :3000 + Jaeger :16686 + Locust). Tải nền khi quan sát: Locust headless 30 user, ramp 5 user/s.

> **Sửa lỗi trước khi chạy được:** lần chạy đầu mọi request trả 502 vì container agent crash liên tục với `ModuleNotFoundError: No module named 'uvicorn'`. Dockerfile cài package bằng `pip install --user` rồi copy sang `/home/agent/.local`, nhưng user `agent` được tạo với home `/app` (`useradd -d /app`) nên Python tìm package ở `/app/.local`. Sửa bằng `ENV PYTHONUSERBASE=/home/agent/.local` trong `production/Dockerfile`; kết quả của lần chạy hỏng đã bị xoá và chạy lại toàn bộ.

### 4.1 Screenshot

- [x] Dashboard Grafana khi đang có tải — [screenshots/grafana-dashboard.png](screenshots/grafana-dashboard.png): Traffic 18.6 req/s, Error rate 1.27%, Latency p95 292.70 ms, 2 instance, đồ thị p50/p95/p99 và req/s theo status code.
- [x] Một trace chậm trong Jaeger — [screenshots/jaeger-slow-trace.png](screenshots/jaeger-slow-trace.png) (chi tiết ở 4.3).
- [x] Một trace lỗi trong Jaeger — [screenshots/jaeger-error-trace.png](screenshots/jaeger-error-trace.png): trace `b1c38c9411ebfd0608afee5d45885b68` (103 ms), span `chat mock-llm-small` có `error.type = upstream_unavailable`, status ERROR và event `exception` với `exception.type = utils.mock_llm.LLMError`, `exception.message = Upstream LLM error: 503 Service Unavailable (mock)`. Log tương ứng: `{"event": "llm_error", "trace_id": "b1c38c9411ebfd0608afee5d45885b68", ..., "error": "Upstream LLM error: 503 Service Unavailable (mock)"}` và dòng `http_request` cùng `trace_id` với `"status": 503`.
- [x] Trang Alerts của Prometheus — [screenshots/prometheus-alert-firing.png](screenshots/prometheus-alert-firing.png): nhóm `agent-slo` **FIRING (2)** — `AgentHighErrorRate` và `AgentHighLatencyP95` cùng firing (thí nghiệm A+B).

**Trả lời Exercise 7.4** (PromQL lúc tải 30 user, cấu hình mặc định, 17:03 và 17:08):

1. **p95 và avg:** p95 ≈ **294–296 ms**, avg ≈ **237–241 ms**, p99 ≈ **1838 ms**. avg và p95 chỉ cách nhau ~55 ms vì 97% request nằm quanh 200 ms; nhưng p99 gấp ~7 lần p95 — đó là 3% request bị chậm thêm 1,5 s (`MOCK_SLOW_RATE=0.03`). Khoảng cách avg ↔ percentile cao cho thấy phân bố bị lệch về đuôi: average không cho biết có một nhóm người dùng chờ gần 2 giây.
2. **Nginx có chia đều không?** Có: `sum by (instance) (rate(http_requests_total{route="/ask"}[1m]))` = **9.33 req/s** (172.21.0.6) và **9.47 req/s** (172.21.0.8), chênh < 2%. Nginx `upstream` dùng round-robin, hỏi lại Docker DNS mỗi 10s (`resolve`).
3. **Vì sao input tokens/s > output tokens/s?** 1427–1449 input token/s so với 753–763 output token/s (~1,9 lần). Prompt v2 = system prompt + **tài liệu retrieve được** + câu hỏi (~95 token), còn câu trả lời chỉ ~55 token. RAG làm prompt dài ra — đây cũng là lý do v2 tốn token hơn v1 ở phần 3.
4. **`curl http://localhost:8080/metrics` trả về gì?** **404**. `nginx.conf` có `location = /metrics { return 404; }`: metrics lộ thông tin nội bộ (route, lưu lượng, version, chi phí) nên không public ra internet. Prometheus vẫn scrape được vì nó gọi thẳng `agent:8000/metrics` bên trong network Docker, không đi qua Nginx.

### 4.2 Thí nghiệm "phá hệ thống"

Giữ Locust 30 user suốt các thí nghiệm. Đổi `.env` rồi `docker compose -p k5-day14-observability up -d --scale agent=3 agent`, script poll `GET /api/v1/alerts` mỗi 5 giây để đo thời gian. A và B làm **cùng lúc** để có ≥ 2 alert firing trên cùng một ảnh.

| # | Thay đổi | Alert nào firing | Thời gian từ lúc đổi → firing | Panel nào phản ánh rõ nhất |
|---|---|---|---|---|
| A | `MOCK_ERROR_RATE=0.2` | `AgentHighErrorRate` | pending +25 s → **firing +86 s** | Error rate: 1.28% → **18.7%**; Requests/s theo status code (503 tăng) |
| B | `MOCK_SLOW_RATE=0.3` | `AgentHighLatencyP95` | pending +15 s → **firing +132 s** | Latency p50/p95/p99: p95 296 ms → **1.90 s**, trong khi avg chỉ 0.24 → 0.60 s |
| C | `LLM_MODEL=mock-llm-large` | `LLMCostBurnRateHigh` | pending +21 s → **firing +142 s** | Cost burn rate: $1.87/giờ → **$16.48/giờ**; LLM latency p95 0.198 s → **0.497 s** |
| D | `docker pause` 1 instance (agent-1) | `AgentInstanceDown` | pending +16 s → **firing +46 s** | Instances up 3 → 2 (`up{instance="172.21.0.6:8000"} = 0`); req/s 18.5 → 14.0 |
| E | `AGENT_VERSION=v1` | **Không có** (theo dõi 240 s) | — | Retrieval hit ratio mất hẳn (không còn series), input tokens/s 1427 → **681**, bảng version đổi sang v1, cost $1.28/giờ, p95 còn 199 ms |

Phân tích:
- **Thời gian tới firing = thời gian `rate()` phản ánh thay đổi + thời gian `for`.** A có `for: 1m` nên ~86 s; B và C có `for: 2m` nên ~132–142 s; D có `for: 30s` nên chỉ 46 s. `for` giúp bỏ qua spike ngắn (lúc agent được tạo lại, `AgentInstanceDown` nhảy sang pending vài giây rồi tự hết — không firing là đúng).
- **C — cost:** chi phí mỗi request của model large đắt ~16,7 lần (giá input 0.0025 vs 0.00015, output 0.01 vs 0.0006), nhưng cost/giờ chỉ tăng ~8,8 lần vì model large chậm 2,5 lần → Locust (mỗi user chờ trả lời xong mới hỏi tiếp) gửi ít request hơn (18.5 → 16.1 req/s). Sau khi trả về model small, alert vẫn firing thêm hơn 3 phút vì `rate(...[5m])` còn chứa dữ liệu 5 phút cũ.
- **Cost ở mức nền đã sát ngưỡng:** với cấu hình mặc định, cost dao động **$1.87–2.44/giờ** quanh ngưỡng $2 — `LLMCostBurnRateHigh` đã firing trong lúc benchmark 100 user và trong A+B lại tự về inactive (lỗi 20% và request chậm làm số lần gọi LLM thành công giảm, cost còn $1.90/giờ). Ngưỡng này cần đặt theo budget thật của production chứ không phải theo tải lab.
- **D — vì sao `pause` mà không `stop`:** container bị pause vẫn còn trong Docker DNS nên Prometheus vẫn scrape và thấy `up == 0`; nếu `stop`, container biến khỏi DNS, Prometheus coi như target không tồn tại → không có alert. **Error rate của agent vẫn xanh (1.7%)** trong khi 1/3 instance không phản hồi và tổng req/s giảm từ 18.5 xuống 14.0: request Nginx gửi tới instance bị pause bị treo tới `proxy_read_timeout` (10 s) rồi Nginx trả lỗi (504 theo cấu hình) — agent chưa bao giờ xử lý xong request đó nên metric của agent không đếm. (Mình không tách được số 504 từ log Locust headless trong lần chạy này; bằng chứng đo được là req/s giảm ~24% trong khi error rate phía agent không đổi.) Metric chỉ phản ánh những gì *chính service đó* thấy → cần giám sát thêm ở tầng load balancer và synthetic check từ bên ngoài.

**Thí nghiệm E không làm alert nào firing. Bạn sẽ phát hiện loại sự cố này trong production bằng cách nào?**

Đổi sang v1 làm accuracy rơi từ 100% xuống 4.3% (phần 3), nhưng với hạ tầng thì v1 còn "khoẻ" hơn: nhanh hơn (p95 199 ms), rẻ hơn, không lỗi. Metrics hạ tầng chỉ đo *nhanh/sống/lỗi HTTP*, không đo *đúng hay sai*. Cách phát hiện:
1. **Chặn trước khi deploy:** quality gate trong CI ([.github/workflows/eval.yml](.github/workflows/eval.yml)) — chạy golden dataset, fail nếu accuracy < 85% hoặc có regression; v1 so với baseline v2 cho 20 câu regression, exit code 1 → không merge được.
2. **Giám sát chất lượng khi đang chạy:** lấy mẫu một phần traffic thật chấm điểm liên tục (keyword/LLM-as-judge) và xuất thành metric `agent_eval_accuracy`; thu feedback người dùng (thích/không thích) thành metric; alert khi tỉ lệ thấp kéo dài.
3. **Proxy metric cho chất lượng:** alert khi `agent_retrieval_total{result="hit"}` giảm mạnh hoặc mất hẳn (đúng dấu hiệu của E), khi tokens/câu đổi đột ngột, khi `agent_info{version}` thay đổi ngoài lịch deploy.

### 4.3 Debug bằng trace

Chọn 1 trace chậm nhất bạn tìm được (lọc Jaeger `duration ≥ 1s`, sắp xếp theo thời gian, lấy trace dài nhất):

- `trace_id`: `cf77d78e724892d24defa316b5aa7f48` ([screenshots/jaeger-slow-trace.png](screenshots/jaeger-slow-trace.png))
- Tổng thời gian: **1801.5 ms** (span gốc `POST /ask`)
- Span chiếm nhiều thời gian nhất: **`chat mock-llm-small` (1769.6 ms, 98.2%)** — `gen_ai.usage.input_tokens = 95`, `output_tokens = 55`. Các span khác: `agent.run` 1796.0 ms (span cha), `agent.retrieve_context` chỉ **26.2 ms (1.5%)**. → Request chậm vì **gọi LLM upstream** (rơi vào 3% request chậm của mock), không phải do code agent hay retrieval; tối ưu code của mình gần như không giúp gì, phải xử lý ở tầng LLM (timeout + retry, model nhanh hơn, cache).
- Dòng log tương ứng (grep theo `trace_id`):

```json
{"ts": "2026-11-26T10:00:23.505469+00:00", "level": "INFO", "service": "observable-agent", "instance": "f546182dc13d", "event": "llm_call", "trace_id": "cf77d78e724892d24defa316b5aa7f48", "span_id": "ab4b5da26834ab47", "request_id": "00bc25e5927d", "model": "mock-llm-small", "agent_version": "v2", "input_tokens": 95, "output_tokens": 55, "cost_usd": 4.7e-05, "llm_ms": 1694.2}
{"ts": "2026-11-26T10:00:23.506628+00:00", "level": "INFO", "service": "observable-agent", "instance": "f546182dc13d", "event": "http_request", "trace_id": "cf77d78e724892d24defa316b5aa7f48", "span_id": "ab4b5da26834ab47", "request_id": "00bc25e5927d", "method": "POST", "route": "/ask", "status": 200, "ms": 1798.5}
```

Ba trụ cột khớp nhau: dashboard thấy p99 ~1.8 s → Jaeger chỉ ra span LLM chiếm 98% → log cùng `trace_id` cho `llm_ms: 1694.2`, `ms: 1798.5`.

---

## 5. Benchmark scaling (production, Locust headless, 100 users, 2 phút)

`docker compose -p k5-day14-observability run --rm locust ... --headless -u 100 -r 10 -t 2m --csv /mnt/results/scale_<n>`. Số liệu dòng `POST /ask` trong `benchmark-results/scale_1_stats.csv` và `scale_3_stats.csv` (trước khi đo scale 3 đã chờ Prometheus thấy đủ 3 target `up`).

| Số instance | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Failures |
|---|---|---|---|---|---|
| 1 | 59.65 | 200 | 260 | 1700 | 75 / 7090 (1.06%) |
| 3 | 59.91 | 200 | 280 | 1700 | 79 / 7124 (1.11%) |

**Giải thích kết quả:** Scale từ 1 lên 3 instance **không cải thiện** gì: RPS, p50, p99 gần như bằng nhau, p95 chênh 20 ms là nhiễu; failure ~1% ở cả hai đều là 503 do mock LLM lỗi ngẫu nhiên 1%.
- **Lưu lượng do người dùng quyết định, không do server:** Locust là tải "vòng kín" — mỗi user hỏi, chờ trả lời (~0,24 s) rồi nghỉ 0,5–2 s (trung bình 1,25 s). 100 user × 1/(1,25 + 0,24) s ≈ 67 req/s, đúng với tổng 65.9 req/s đo được (ask + health). 1 instance đã phục vụ hết lượng này (ở develop, 1 process chịu được ~98 req/s) nên thêm instance không có thêm request nào để xử lý.
- **Nút thắt là latency của LLM upstream:** trace cho thấy span `chat mock-llm-*` chiếm ~98% thời gian request; thời gian đó không giảm khi thêm instance — mỗi request vẫn phải chờ LLM ~200 ms (và 1,7 s với 3% request chậm). Vì vậy p50 = 200 ms và p99 = 1700 ms ở cả hai cấu hình.
- Scale ngang chỉ có ích khi một instance đã **bão hoà** (CPU, event loop, kết nối) — như ở develop concurrency 100 (RPS giảm còn 42.8, p95 4.6 s). Muốn thấy lợi ích phải tăng tải vượt sức 1 instance; còn muốn giảm p95/p99 thì phải tác động vào LLM (model nhanh hơn, cache, timeout + retry/hedging).

---

## 6. Kết luận

- 3 điều bạn học được về benchmark & observability:
  1. **Average nói dối, percentile thì không.** Với 3% request chậm, avg chỉ tăng 13% nhưng p99 tăng 7 lần; với 10% thì p95 tăng 7,6 lần. SLO phải đặt theo p95/p99, và load test phải chạy đủ nhiều request mới tin được percentile cao.
  2. **Hạ tầng xanh không có nghĩa là agent đúng.** Đổi sang v1 làm accuracy từ 100% xuống 4.3% mà không alert nào kêu; lỗi 504 ở Nginx cũng không hiện trên metric của agent. Cần eval trong CI + giám sát chất lượng khi chạy, và giám sát ở nhiều tầng (LB, synthetic check), không chỉ metric của chính service.
  3. **Ba trụ cột phải nối được với nhau.** Dashboard chỉ ra *có vấn đề*, trace chỉ ra *ở bước nào* (98% thời gian ở LLM), log có `trace_id` cho *chi tiết* — đi từ panel p99 tới đúng dòng log trong vài phút. Ngoài ra số đo chỉ đáng tin khi kiểm tra đầu vào: lần chạy Locust đầu cho "latency 3 ms" vì 100% request là 502 — nếu chỉ nhìn số mà không nhìn failure thì đã kết luận sai.
- Nếu đưa agent này lên production thật, bạn sẽ thêm metric / alert nào nữa?
  - **Metric chất lượng:** `agent_eval_accuracy` từ eval trên traffic lấy mẫu, tỉ lệ feedback tiêu cực, alert khi retrieval hit ratio giảm mạnh.
  - **Alert theo error budget (multi-window burn rate)** thay cho ngưỡng cố định: ví dụ đốt 2% budget trong 1 giờ → page, 5% trong 6 giờ → ticket; giảm báo động giả mà vẫn bắt được sự cố chậm.
  - **Giám sát ngoài service:** metric của Nginx (tỉ lệ 502/504, upstream response time) và synthetic probe gọi `/ask` từ bên ngoài mỗi phút — bắt được lỗi kiểu thí nghiệm D.
  - **Chi phí theo budget thật:** cost theo ngày/tháng so với `DAILY_BUDGET_USD`, cảnh báo ở 80% budget (giống cost guard Part 6), và cost / câu trả lời đúng theo version.
  - **Saturation:** alert khi `agent_in_flight_requests` hoặc p95 tăng liên tục ở mọi instance (dấu hiệu cần scale), và metric thời gian chờ của LLM upstream (timeout, retry).
