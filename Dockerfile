# syntax=docker/dockerfile:1.7
FROM node:22-bookworm-slim AS frontend
WORKDIR /build/apps/web
COPY apps/web/package*.json ./
RUN npm ci
COPY apps/web/ ./
RUN npm run build

FROM nvidia/cuda:12.6.3-cudnn-runtime-ubuntu24.04 AS runtime
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UPMIXER_DATA_DIR=/data \
    UPMIXER_FRONTEND_DIR=/app/web-dist \
    UPMIXER_HOST=0.0.0.0 \
    UPMIXER_PORT=8000
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ffmpeg libsndfile1 python3-pip python3.12 python3.12-venv \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY README.md LICENSE ./
COPY packages/core ./packages/core
COPY apps/api ./apps/api
RUN python3.12 -m pip install --break-system-packages --no-cache-dir \
    "./packages/core[separation-gpu]" "./apps/api"
COPY --from=frontend /build/apps/web/dist ./web-dist
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 CMD curl -fsS http://127.0.0.1:8000/api/v1/health || exit 1
CMD ["python3.12", "-m", "upmixer_web"]
