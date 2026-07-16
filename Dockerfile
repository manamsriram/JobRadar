# Multi-stage: Node builds the React frontend, the Python image runs the API +
# scraper loop. Phase 2 split: Chromium/Playwright never runs on this host —
# JS-rendered scraping happens in a GitHub Action (scrapers/playwright_scraper.py)
# which POSTs results to /api/ingest. That's why no apt-get Chromium libs or
# `playwright install` here: this image fits comfortably on a 1GB free-tier VM.

FROM node:20-alpine AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ .
RUN npm run build

FROM python:3.12-slim
WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ .
COPY --from=frontend-builder /app/frontend/dist ./frontend/dist

RUN mkdir -p /data
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
