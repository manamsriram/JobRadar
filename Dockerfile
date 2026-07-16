# Multi-stage: Node builds the React frontend, the Python image runs everything
# (FastAPI + scraper loop + Playwright/Chromium). 12 GB RAM on the Oracle ARM VM
# makes running Chromium in-container viable for Phase 1.

FROM node:20-alpine AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ .
RUN npm run build

FROM python:3.12-slim
WORKDIR /app

# System libs required by headless Chromium.
# Phase 2: if Playwright is offloaded to GitHub Actions, this apt-get block and
# the `playwright install` line below can be removed to free ~500 MB on the VM.
RUN apt-get update && apt-get install -y \
    libnss3 libatk-bridge2.0-0 libdrm2 libxkbcommon0 \
    libgbm1 libasound2 libxshmfence1 --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Phase 1: Chromium ships inside the image. Phase 2: remove this line.
RUN playwright install chromium --with-deps

COPY backend/ .
COPY --from=frontend-builder /app/frontend/dist ./frontend/dist

RUN mkdir -p /data
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
