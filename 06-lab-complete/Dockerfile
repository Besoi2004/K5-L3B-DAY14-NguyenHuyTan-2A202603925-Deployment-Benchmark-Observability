# ============================================================
# Production Dockerfile — multi-stage
#   builder : cài dependencies vào virtualenv /opt/venv
#   runtime : image slim sạch, chỉ copy /opt/venv + code, user non-root
# Build:  docker build -t day14-agent .     (chạy trong 06-lab-complete/)
# ============================================================

# ── Stage 1: builder ─────────────────────────────────────────
FROM python:3.11-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build
# Copy requirements trước → layer cài package được cache khi chỉ sửa code
COPY requirements.txt .
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install -r requirements.txt

# ── Stage 2: runtime ─────────────────────────────────────────
FROM python:3.11-slim AS runtime

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000

RUN groupadd --system app && useradd --system --gid app --home-dir /app app

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY --chown=app:app app/ ./app/
COPY --chown=app:app utils/ ./utils/

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os, urllib.request as u; u.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT', '8000'), timeout=3)" || exit 1

# `exec` thay sh bằng uvicorn → uvicorn là PID 1, nhận SIGTERM trực tiếp khi `docker stop`
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 2"]
