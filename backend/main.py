from dotenv import load_dotenv

load_dotenv()  # populate env before other modules read it

import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Header, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

import state
from filter import matches
from scraper import digest_loop, funding_loop, new_jobs_queue, poll_loop

FRONTEND_DIST = "frontend/dist"
RESUME_SLOTS = ("backend", "frontend")
RESUME_EXTENSIONS = {".txt", ".pdf"}
RESUME_MAX_BYTES = 2 * 1024 * 1024


@asynccontextmanager
async def lifespan(app: FastAPI):
    tasks = [
        asyncio.create_task(poll_loop()),
        asyncio.create_task(funding_loop()),
        asyncio.create_task(digest_loop()),
    ]
    try:
        yield
    finally:
        for t in tasks:
            t.cancel()


app = FastAPI(lifespan=lifespan)
# No CORS: the React build is served same-origin from this app (StaticFiles below).


# Consecutive fetch failures before a source counts as "down" for the
# /api/health status code (not just the per-source detail).
HEALTH_FAILURE_THRESHOLD = 3


@app.get("/api/health")
async def health():
    sources = state.load_health()
    down = {
        name: s for name, s in sources.items()
        if s.get("consecutive_failures", 0) >= HEALTH_FAILURE_THRESHOLD
    }
    body = {"status": "degraded" if down else "ok", "sources": sources}
    if down:
        return JSONResponse(body, status_code=503)
    return body


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


# ---- Resume uploads (ai_match.py reads these fresh from disk per call) ----
# Reuses the /api/ingest shared secret — one token per deployment.
def _check_resume_token(x_resume_token: str) -> None:
    token = os.getenv("INGEST_TOKEN")
    if not token or x_resume_token != token:
        raise HTTPException(status_code=401, detail="invalid resume token")


@app.post("/api/resumes/{slot}")
async def upload_resume(
    slot: str, file: UploadFile, x_resume_token: str = Header(default="")
):
    if slot not in RESUME_SLOTS:
        raise HTTPException(status_code=400, detail="slot must be 'backend' or 'frontend'")
    _check_resume_token(x_resume_token)

    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in RESUME_EXTENSIONS:
        raise HTTPException(status_code=400, detail="only .txt and .pdf resumes are accepted")

    body = await file.read()
    if len(body) > RESUME_MAX_BYTES:
        raise HTTPException(status_code=400, detail="resume exceeds 2MB limit")

    state.DATA_DIR.mkdir(parents=True, exist_ok=True)
    # Drop any stale file for this slot under a different extension so
    # ai_match's loader (which probes .txt then .pdf) never reads two versions.
    for other_ext in RESUME_EXTENSIONS:
        (state.DATA_DIR / f"resume_{slot}{other_ext}").unlink(missing_ok=True)

    dest = state.DATA_DIR / f"resume_{slot}{ext}"
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.write_bytes(body)
    os.replace(tmp, dest)

    meta_path = state.DATA_DIR / "resume_meta.json"
    meta = state._read_json(meta_path, {})
    meta[slot] = {
        "filename": file.filename,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    state._write_json_atomic(meta_path, meta)
    return {"ok": True, "slot": slot}


@app.get("/api/resumes")
async def resume_status():
    meta = state._read_json(state.DATA_DIR / "resume_meta.json", {})
    return {slot: meta.get(slot) for slot in RESUME_SLOTS}


# Serve the built React frontend (only if present — absent during backend-only dev).
if os.path.isdir(FRONTEND_DIST):
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="static")
