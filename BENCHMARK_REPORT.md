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

*(Làm ở CP8 — Part 7 Bước 2, chạy stack Docker Compose.)*

### 4.1 Screenshot

- [ ] Dashboard Grafana khi đang có tải (thấy rõ Golden Signals)
- [ ] Một trace chậm trong Jaeger (thấy span nào chiếm thời gian)
- [ ] Một trace lỗi trong Jaeger (có event `exception`)
- [ ] Trang Alerts của Prometheus với ít nhất 1 alert đang **firing**

### 4.2 Thí nghiệm "phá hệ thống"

| # | Thay đổi | Alert nào firing | Thời gian từ lúc đổi → firing | Panel nào phản ánh rõ nhất |
|---|---|---|---|---|
| A | `MOCK_ERROR_RATE=0.2` | | | |
| B | `MOCK_SLOW_RATE=0.3` | | | |
| C | `LLM_MODEL=mock-llm-large` | | | |
| D | `docker pause` 1 instance | | | |
| E | `AGENT_VERSION=v1` | | | |

**Thí nghiệm E không làm alert nào firing. Bạn sẽ phát hiện loại sự cố này trong production bằng cách nào?**

...

### 4.3 Debug bằng trace

Chọn 1 trace chậm nhất bạn tìm được:

- `trace_id`: ...
- Tổng thời gian: ... ms
- Span chiếm nhiều thời gian nhất: ... (... ms, ...%)
- Dòng log tương ứng (grep theo `trace_id`):

```json

```

---

## 5. Benchmark scaling (production, Locust headless, 100 users, 2 phút)

| Số instance | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Failures |
|---|---|---|---|---|---|
| 1 | | | | | |
| 3 | | | | | |

**Giải thích kết quả:** Scale có cải thiện p95 không? Nút thắt nằm ở đâu?

...

---

## 6. Kết luận

- 3 điều bạn học được về benchmark & observability:
  1. ...
  2. ...
  3. ...
- Nếu đưa agent này lên production thật, bạn sẽ thêm metric / alert nào nữa?

...
