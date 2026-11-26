#!/usr/bin/env bash
# Demo Part 1 — chạy trong Ubuntu (WSL):
#   bash "01-localhost-vs-production/curl_demo.sh"
# Mỗi bước in ra: lệnh curl, ý nghĩa, rồi output thật.
# curl -i  : in cả status line + header (HTTP/1.1 200 OK ...) chứ không chỉ body
# curl -s  : im lặng, không in thanh tiến trình
# -X POST  : phương thức HTTP;  -H : thêm header;  -d : body gửi đi

. ~/venvs/day14/bin/activate
HERE="$(cd "$(dirname "$0")" && pwd)"

step() { echo; echo "────────────────────────────────────────"; echo "▶ $1"; echo "  Ý nghĩa: $2"; echo "────────────────────────────────────────"; }
wait_up() { for _ in $(seq 1 40); do curl -s "$1" >/dev/null 2>&1 && return; sleep 0.25; done; }

echo "════════ PHẦN A — BẢN DEVELOP (anti-pattern) ════════"
cd "$HERE/develop"
python app.py > /tmp/dev.log 2>&1 &
wait_up http://localhost:8000/

step 'curl -i -X POST "http://localhost:8000/ask?question=Hello"' \
     'Gửi câu hỏi qua query string. Mong đợi 200 + câu trả lời mock.'
curl -s -i -X POST "http://localhost:8000/ask?question=Hello"; echo

step "curl -i -X POST http://localhost:8000/ask -H 'Content-Type: application/json' -d '{\"question\":\"Hello\"}'" \
     'Gửi JSON body. Bản develop khai báo ask_agent(question: str) => FastAPI hiểu là query param => thiếu => 422.'
curl -s -i -X POST http://localhost:8000/ask -H 'Content-Type: application/json' -d '{"question":"Hello"}'; echo

step 'curl -i http://localhost:8000/health' \
     'Platform (Render/Railway) gọi đường này để biết app sống không. Develop không có => 404.'
curl -s -i http://localhost:8000/health; echo

step 'grep "Using key" /tmp/dev.log' \
     'Xem log server: secret bị print() ra log — ai đọc log là lấy được key.'
grep "Using key" /tmp/dev.log

pkill -f "python app.py"; sleep 1

echo; echo "════════ PHẦN B — BẢN PRODUCTION (12-factor) ════════"
cd "$HERE/production"
python app.py > /tmp/prod.log 2>&1 &
PID=$!
wait_up http://localhost:8000/health

step 'curl -i http://localhost:8000/health' \
     'Liveness: process còn sống không. 200 + uptime. Nếu fail, platform restart container.'
curl -s -i http://localhost:8000/health; echo

step 'curl -i http://localhost:8000/ready' \
     'Readiness: đã sẵn sàng nhận traffic chưa (vd đã kết nối DB/Redis). Chưa sẵn sàng => 503, LB tạm không gửi request.'
curl -s -i http://localhost:8000/ready; echo

step "curl -i -X POST http://localhost:8000/ask -H 'Content-Type: application/json' -d '{\"question\":\"Hello\"}'" \
     'Bản production nhận JSON body. 200 + answer + model (model đọc từ env LLM_MODEL).'
curl -s -i -X POST http://localhost:8000/ask -H 'Content-Type: application/json' -d '{"question":"Hello"}'; echo

step "curl -i -X POST http://localhost:8000/ask -H 'Content-Type: application/json' -d '{}'" \
     'Input sai => 422 với thông báo rõ ràng, không lộ stack trace.'
curl -s -i -X POST http://localhost:8000/ask -H 'Content-Type: application/json' -d '{}'; echo

step 'Graceful shutdown: gửi 1 request rồi NGAY LẬP TỨC kill -TERM' \
     'SIGTERM là tín hiệu platform gửi khi tắt container. App phải trả lời xong request đang chạy rồi mới thoát (exit 0).'
curl -s -o /tmp/inflight.txt -w 'request đang chạy nhận HTTP %{http_code}\n' -X POST http://localhost:8000/ask \
     -H 'Content-Type: application/json' -d '{"question":"Long task"}' &
CURL=$!
sleep 0.05
kill -TERM $PID
wait $CURL
wait $PID; echo "process thoát với exit code $?"
grep -E "Shutting down|shutdown|Finished" /tmp/prod.log

echo; echo "════════ PHẦN C — ENVIRONMENT VARIABLES ════════"
step 'PORT=9000 ENVIRONMENT=staging python app.py  rồi  curl http://localhost:9000/health' \
     'Không sửa 1 dòng code nào, chỉ đổi biến môi trường => app nghe port 9000 và báo environment=staging.'
PORT=9000 ENVIRONMENT=staging python app.py > /tmp/prod9000.log 2>&1 &
PID=$!
wait_up http://localhost:9000/health
curl -s http://localhost:9000/health; echo
kill -TERM $PID; wait $PID 2>/dev/null

step 'ENVIRONMENT=production python app.py   (không đặt AGENT_API_KEY)' \
     'Fail fast: config.validate() từ chối khởi động production khi thiếu secret bắt buộc.'
ENVIRONMENT=production python app.py 2>&1 | grep -E "ValueError|AGENT_API_KEY" | tail -1
echo; echo "✅ Xong demo."
