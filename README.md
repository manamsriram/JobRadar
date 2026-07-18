<div align="center">

# JobRadar

Self-hosted job radar that scrapes YC/ATS careers pages and startup funding signals, filters for entry-level US roles, AI-scores fit against your resume, and pushes matches to a live dashboard and email digest.

![Python](https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white)
![React](https://img.shields.io/badge/React-61DAFB?style=for-the-badge&logo=react&logoColor=white)
![TypeScript](https://img.shields.io/badge/TypeScript-3178C6?style=for-the-badge&logo=typescript&logoColor=white)
![Vite](https://img.shields.io/badge/Vite-646CFF?style=for-the-badge&logo=vite&logoColor=white)
![Tailwind CSS](https://img.shields.io/badge/Tailwind_CSS-06B6D4?style=for-the-badge&logo=tailwindcss&logoColor=white)
![Playwright](https://img.shields.io/badge/Playwright-2EAD33?style=for-the-badge&logo=playwright&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-2496ED?style=for-the-badge&logo=docker&logoColor=white)
![Caddy](https://img.shields.io/badge/Caddy-1F88C0?style=for-the-badge&logo=caddy&logoColor=white)
![GitHub Actions](https://img.shields.io/badge/GitHub_Actions-2088FF?style=for-the-badge&logo=githubactions&logoColor=white)

</div>

---

## Overview

JobRadar polls YC company pages, custom career pages, and ATS boards for new job postings, cross-references them against a US-only, entry-level filter (years-of-experience regex, degree-requirement checks, citizenship/sponsorship language), and scores the survivors against two uploaded resumes using a free-tier LLM via OpenRouter. Matches stream to a React dashboard over SSE and batch into a 4-hour email digest. State is plain JSON on disk — no database.

## Features

- **Multi-source scraping** — YC job boards, custom company career pages, and an off-box Playwright scraper (run via GitHub Actions) that POSTs results back through a token-authenticated ingest endpoint
- **Funding signal watcher** — polls TechCrunch's funding RSS hourly and surfaces newly-funded startups as scraping targets
- **Entry-level filter** — regex-based years-of-experience cap, degree+experience combo rejection, US-location matching with citizenship/sponsorship language handling
- **AI resume-fit scoring** — uploads two resumes (backend/frontend slots), scores each match via OpenRouter's free-tier model with a daily-cap guard
- **Live dashboard** — SSE-streamed job feed with dedup, source filter, and apply-tracking
- **Email digest** — batches matched jobs into a 4-hour digest instead of one email per match
- **Scraper resilience** — per-source health tracking, retry with backoff, a cycle-wide retry budget, and atomic state writes with backups
- **Company alias canonicalization** — merges duplicate company names across sources

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python, FastAPI, Uvicorn |
| Frontend | React, TypeScript, Vite, Tailwind CSS |
| Scraping | httpx, BeautifulSoup, Playwright (offloaded via GitHub Actions) |
| AI | OpenRouter (free-tier model) for resume-fit scoring |
| State | Flat JSON files (atomic writes, backups) — no database |
| Infra | Docker, Docker Compose, Caddy reverse proxy |
| CI/CD | GitHub Actions (tests, deploy, Playwright offload, keep-alive) |

## Getting Started

### Prerequisites

- Python 3.11+
- Node.js 18+
- Docker and Docker Compose (for deployment)
- Gmail account with an [App Password](https://myaccount.google.com/apppasswords) for email alerts

### Installation

```bash
git clone <repo-url>
cd JobRadar

# backend
cd backend
pip install -r requirements.txt
playwright install chromium

# frontend
cd ../frontend
npm install
```

### Configuration

```bash
cp .env.example .env
```

| Variable | Purpose |
|----------|---------|
| `GMAIL_USER` / `GMAIL_APP_PASSWORD` / `ALERT_TO` | SMTP email digest |
| `HUNTER_API_KEY` | Optional contact enrichment (Hunter.io free tier) |
| `INGEST_TOKEN` | Shared secret authenticating the Playwright offload's `POST /api/ingest` and resume uploads |
| `DATA_DIR` | JSON state directory (`/data` in Docker, e.g. `./data` locally) |
| `POLL_INTERVAL_SECONDS` | Scrape poll cadence (default 300) |
| `FUNDING_CHECK_INTERVAL` | Funding RSS poll cadence (default 3600) |
| `PURGE_AFTER_DAYS` | Unapplied job retention window (default 3) |
| `MAX_POSTED_AGE_DAYS` | Recency cutoff for surfaced jobs (default 7) |
| `MAX_YEARS_EXPERIENCE` | Years-of-experience filter cap (default 2) |

### Running Locally

```bash
# backend
cd backend
uvicorn main:app --reload

# frontend (separate terminal)
cd frontend
npm run dev
```

### Running with Docker

```bash
docker compose up -d --build
```

Docker Compose runs the FastAPI app (with the built React frontend served same-origin) behind Caddy for TLS termination.

## Architecture

Two background loops (`poll_loop`, `funding_loop`) scrape sources and write matched jobs into shared JSON state; a third (`digest_loop`) batches matches into periodic emails. New matches also push onto an in-memory queue consumed by `GET /api/stream` (SSE) for the live dashboard. Heavier scraping (Playwright/Chromium) runs off-box as a scheduled GitHub Action and reports results back to `POST /api/ingest`, keeping the host container lightweight. Resume uploads feed `ai_match.py`, which re-reads resumes from disk per call and scores each incoming job against them through OpenRouter.

## API Reference

| Method | Endpoint | Description |
|--------|----------|--------------|
| GET | `/api/health` | Per-source scraper health; 503 if any source has failed 3+ consecutive polls |
| GET | `/api/jobs` | Matched jobs |
| POST | `/api/jobs/{job_id}/apply` | Mark a job applied (exempts it from purge) |
| GET | `/api/stream` | SSE stream of newly matched jobs |
| POST | `/api/ingest` | Token-authenticated bulk job ingest (used by the Playwright offload) |
| POST | `/api/resumes/{slot}` | Upload a resume (`backend` or `frontend` slot, `.txt`/`.pdf`, 2MB max) |
| GET | `/api/resumes` | Resume upload metadata per slot |

## Contributing

```bash
git checkout -b feature/your-feature
git commit -m "feat: describe your change"
git push origin feature/your-feature
```

Open a pull request. Follow the existing code style.

