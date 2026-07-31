<div align="center">

# JobRadar

Self-hosted job radar that scrapes ATS careers pages and startup funding signals, filters for entry-level US roles, AI-scores fit against your resume, and pushes matches to a live dashboard and email digest.

**This is a personal tool, tuned to one job search.** Filter thresholds (role titles, US-only location, ≤2 years experience, citizenship/sponsorship language) are hardcoded assumptions for the owner's search, not general-purpose defaults — fork and retune `config.py` if you want to run this for yourself.

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

JobRadar polls custom career pages and ATS boards (Greenhouse/Lever/Ashby) for new job postings, cross-references them against a US-only, entry-level filter (`filter.py`: title/description years-of-experience regex, degree-requirement checks, role-title allowlist, citizenship/sponsorship language), and scores the survivors against two uploaded resumes using a free-tier LLM via OpenRouter. Matches stream to a React dashboard over SSE and batch into a 4-hour email digest. State is plain JSON on disk — no database.

Filtering runs in two passes. The regex gate (`filter.py`) is cheap and runs on every scraped job, catching most out-of-range experience requirements and non-US roles from the title/description text. Jobs that clear the regex gate but have real description text (fetched from the job's own detail page) go through a second AI pass (`ai_match.py`, via OpenRouter) that catches phrasing the regex misses — e.g. experience requirements stated as prose rather than "N years" — and produces a resume-fit score. The regex gate re-runs after the description fetch (not just before), since the AI pass fails open on provider errors/rate-limits and shouldn't be the only thing standing between a bad match and the dashboard.

## Features

- **Multi-source scraping** — custom company career pages and ATS boards scraped directly via `httpx`; sites that block simple HTTP scraping run through a separate Playwright scraper, offloaded to GitHub Actions (see [Why Playwright runs separately](#why-playwright-runs-separately))
- **Two-pass filtering** — cheap regex gate on every job, AI second-pass (OpenRouter) only for survivors with real description text; see [Architecture](#architecture)
- **Funding signal watcher** — polls TechCrunch's funding RSS hourly and surfaces newly-funded startups as scraping targets (YC itself was dropped as a source; funding-signal targets are still added as custom career-page/ATS entries)
- **Entry-level filter** — regex-based years-of-experience cap, degree+experience combo rejection, US-location matching with citizenship/sponsorship language handling
- **AI resume-fit scoring** — uploads two resumes (backend/frontend slots), scores each match via an ordered list of free-tier LLM providers (OpenRouter, Groq) with per-provider daily-cap guards and automatic fallback
- **On-demand contact lookup** — `POST /api/jobs/{id}/contacts` fetches one hiring contact at the job's company via Hunter.io, right after you apply; per-company results are cached so re-applying to the same company costs no further credit
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
| `HUNTER_API_KEY` / `HUNTER_MONTHLY_CALL_CAP` | On-demand contact lookup (Hunter.io free tier, 50 credits/mo default) — fired manually per job, cached per company |
| `AI_PROVIDERS` | Ordered fallback list for AI resume-fit scoring (default `openrouter`) |
| `OPENROUTER_API_KEY` / `GROQ_API_KEY` | API keys per AI provider — a provider is skipped entirely if its key is unset |
| `INGEST_TOKEN` | Shared secret authenticating the Playwright offload's `POST /api/ingest` and resume uploads |
| `DATA_DIR` | JSON state directory (`/data` in Docker, e.g. `./data` locally) |
| `POLL_INTERVAL_SECONDS` | Scrape poll cadence (default 300) |
| `FUNDING_CHECK_INTERVAL` | Funding RSS poll cadence (default 3600) |
| `PURGE_AFTER_DAYS` | Unapplied job retention window (default 7 — kept in sync with `MAX_POSTED_AGE_DAYS`) |
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

Two background loops (`poll_loop`, `funding_loop`) scrape sources and write matched jobs into shared JSON state; a third (`digest_loop`) batches matches into periodic emails. New matches also push onto an in-memory queue consumed by `GET /api/stream` (SSE) for the live dashboard.

**Scrape → filter → AI review pipeline:**

1. Each source (Greenhouse/Lever/Ashby APIs, custom career pages, or the Playwright offload) returns raw job listings — title, URL, company, and usually a bare-bones or missing description. (Y Combinator's job board was removed as a source — too much bot-blocking and low signal relative to direct ATS/career-page scraping.)
2. `filter.py`'s `matches()` runs the cheap regex gate: title/role allowlist, years-of-experience cap, US-location check, degree+experience combo rejection, citizenship/sponsorship language. This runs on every job and rejects most out-of-range postings immediately.
3. For jobs that pass, the job's own detail page is fetched for full description text, and the regex gate re-runs against that real text (listing-page scrapes rarely carry a real description, so the first pass mostly only had the title to work with).
4. If the description is substantial (>100 chars), it goes through `ai_match.py`'s AI second-pass gate via OpenRouter — catching phrasing the regex can't parse (e.g. "must have led a team for several years" instead of "N years") — and scoring resume fit. This gate fails open (job stays matched) if the provider errors or rate-limits, since the regex pass already screened it.
5. Matched jobs land in JSON state, stream to the dashboard, and queue for the email digest.

### Why Playwright runs separately

Some target sites (Microsoft, Wipro, HCL among them) block plain `httpx` requests — they require a real browser to render and pass basic bot checks. Running Playwright/Chromium in the main container isn't viable long-term: the deploy target has no persistent `/data` volume for the offload runner, and bundling a full browser into the always-on service bloats the image for scraping that only a handful of sites need. Instead, Playwright scraping runs as a scheduled GitHub Action, which POSTs results back to the host through a token-authenticated `POST /api/ingest` endpoint — same filter/AI pipeline as the in-process scrapers, just a different entry point. This keeps the host container lightweight and lets the Playwright side scale/retry independently of the main poll loop.

Resume uploads feed `ai_match.py`, which re-reads resumes from disk per call and scores each incoming job against them by trying providers in `AI_PROVIDERS` order until one succeeds. Contact lookup (`enricher.py`) never runs from these loops — it's triggered on demand via `POST /api/jobs/{id}/contacts`, resolving a domain (curated → URL-derived → best-effort guess), checking a per-domain cache, and only calling Hunter on a cache miss within the monthly credit budget.

## API Reference

| Method | Endpoint | Description |
|--------|----------|--------------|
| GET | `/api/health` | Per-source scraper health; 503 if any source has failed 3+ consecutive polls |
| GET | `/api/jobs` | Matched jobs |
| POST | `/api/jobs/{job_id}/apply` | Mark a job applied (exempts it from purge) |
| POST | `/api/jobs/{job_id}/contacts` | On-demand Hunter.io lookup for one hiring contact at the job's company (cached per company) |
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

