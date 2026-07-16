from dotenv import load_dotenv

load_dotenv()  # populate env before other modules read it

import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

import state
from filter import matches
from scraper import funding_loop, new_jobs_queue, poll_loop

FRONTEND_DIST = "frontend/dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    tasks = [
        asyncio.create_task(poll_loop()),
        asyncio.create_task(funding_loop()),
    ]
    try:
        yield
    finally:
        for t in tasks:
            t.cancel()


app = FastAPI(lifespan=lifespan)
# No CORS: the React build is served same-origin from this app (StaticFiles below).


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/jobs")
async def get_jobs():
    return JSONResponse(state.get_matched(state.load_seen()))


@app.post("/api/jobs/{job_id}/apply")
async def apply_job(job_id: str):
    # Mark applied so purge_old preserves the job past the 3-day window.
    if not state.mark_applied(job_id):
        raise HTTPException(status_code=404, detail="job not found")
    return {"ok": True}


@app.get("/api/stream")
async def stream_jobs():
    async def event_generator():
        while True:
            job = await new_jobs_queue.get()
            yield f"data: {json.dumps(job)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---- Phase 2 ingest endpoint ----
# The playwright_scraper.yml GitHub Action runs Chromium off-box and POSTs
# scraped jobs here, so this host never has to hold a browser in RAM.
# Auth via the INGEST_TOKEN shared secret.
@app.post("/api/ingest")
async def ingest(request: Request, x_ingest_token: str = Header(default="")):
    token = os.getenv("INGEST_TOKEN")
    if not token or x_ingest_token != token:
        raise HTTPException(status_code=401, detail="invalid ingest token")
    incoming = await request.json()
    if not isinstance(incoming, list) or not all(
        isinstance(j, dict) and isinstance(j.get("id"), str) for j in incoming
    ):
        raise HTTPException(status_code=400, detail="expected a list of job objects with string ids")
    seen = state.load_seen()
    added = 0
    now = datetime.now(timezone.utc).isoformat()
    for job in state.get_new_jobs(seen, incoming):
        job["scraped_at"] = now
        if not job.get("posted_at"):
            job["posted_at"] = job["scraped_at"]
        job["matched"] = matches(job)
        seen[job["id"]] = job
        added += 1
    state.save_seen(seen)
    return {"ingested": added}


# Serve the built React frontend (only if present — absent during backend-only dev).
if os.path.isdir(FRONTEND_DIST):
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="static")
