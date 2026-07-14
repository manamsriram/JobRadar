from dotenv import load_dotenv

load_dotenv()  # populate env before modules read it (F5)

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

import state
from config import COMPANIES
from scraper import digest_loop, new_jobs_queue, poll_loop

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

FRONTEND_DIST = "frontend/dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not (os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_SERVICE_KEY")):
        log.warning("SUPABASE_URL/SUPABASE_SERVICE_KEY unset — scraper will error.")
    tasks = [
        asyncio.create_task(poll_loop(COMPANIES)),
        asyncio.create_task(digest_loop()),
    ]
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(lifespan=lifespan)

# Frontend is served from GitHub Pages (separate origin); allow it to call the API.
# ALLOWED_ORIGINS is comma-separated; defaults to the Pages origin.
_origins = os.getenv(
    "ALLOWED_ORIGINS", "https://manamsriram.github.io"
).split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _origins if o.strip()],
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/healthz")
async def healthz():
    # Cheap liveness endpoint for UptimeRobot keep-alive (no DB hit).
    return {"ok": True}


@app.get("/api/jobs")
async def get_jobs():
    return JSONResponse(await state.get_matched())


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


# Serve the built React frontend (only if present — absent during backend-only dev).
if os.path.isdir(FRONTEND_DIST):
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="static")
