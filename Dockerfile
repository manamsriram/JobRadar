# Backend-only image. The React frontend is built + hosted on GitHub Pages.
FROM python:3.12-slim
WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# ponytail: chromium omitted — no custom-ATS companies in the seed, so
# playwright_scraper never launches a browser. Add
# `playwright install chromium --with-deps` when a custom career-page target exists.

COPY backend/ .

EXPOSE 8000
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
