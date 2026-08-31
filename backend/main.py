from dotenv import load_dotenv

load_dotenv()  # populate env before other modules read it

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Header, HTTPException, Request, Response, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

import ai_match
import enricher
import outreach
import outreach_mail
import pipeline_db
import pipeline_events
import pipeline_state
import state
from filter import matches
from notifier import send_pipeline_alert
from pipeline_models import CreateApplicationIn, SendOutreachIn, TransitionIn
from scraper import _fetch_job_description, digest_loop, funding_loop, new_jobs_queue, poll_loop, visa_sponsor_loop
from signals import visa_sponsors

logger = logging.getLogger(__name__)

FRONTEND_DIST = "frontend/dist"
RESUME_SLOTS = ("backend", "frontend")
RESUME_EXTENSIONS = {".txt", ".pdf"}
RESUME_MAX_BYTES = 2 * 1024 * 1024


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Run once at startup (not just on the weekly visa_sponsor_loop timer) so
    # all seeded companies are live immediately instead of after the first interval.
    await visa_sponsors.merge_seed_companies(state.load_companies())
    await pipeline_db.init_pool()
    pipeline_events.register(send_pipeline_alert)
    tasks = [
        asyncio.create_task(poll_loop()),
        asyncio.create_task(funding_loop()),
        asyncio.create_task(digest_loop()),
        asyncio.create_task(visa_sponsor_loop()),
    ]
    try:
        yield
    finally:
        for t in tasks:
            t.cancel()
        await pipeline_db.close_pool()


app = FastAPI(lifespan=lifespan)
# No CORS: the React build is served same-origin from this app (StaticFiles below).

# Shared secret with the Vercel proxy (frontend/api/[...path].ts) — rejects
# anyone hitting this VM's IP directly instead of through the proxy. Exempts
# routes the GH Actions workflow calls directly with their own INGEST_TOKEN,
# and /api/health for uptime monitors.
INTERNAL_KEY = os.getenv("INTERNAL_KEY")
_KEYLESS_PREFIXES = ("/api/ingest", "/api/link-patterns", "/api/health")


@app.middleware("http")
async def require_internal_key(request: Request, call_next):
    if (
        INTERNAL_KEY
        and request.url.path.startswith("/api/")
        and not request.url.path.startswith(_KEYLESS_PREFIXES)
        and request.headers.get("x-internal-key") != INTERNAL_KEY
    ):
        return JSONResponse({"detail": "forbidden"}, status_code=403)
    return await call_next(request)


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
    body = {"status": "degraded" if down else "ok", "sources": sources, "down": list(down.keys())}
    if down:
        return JSONResponse(body, status_code=503)
    return body


@app.get("/api/jobs")
async def get_jobs():
    return JSONResponse(state.get_matched(state.load_seen()))


@app.get("/api/jobs/applied")
async def get_applied_jobs():
    return JSONResponse(state.get_applied(state.load_seen()))


@app.post("/api/jobs/{job_id}/apply")
async def apply_job(job_id: str):
    # Mark applied so purge_old preserves the job past the 3-day window and
    # get_matched()/get_applied() move it into the separate Applied list.
    if not state.mark_applied(job_id):
        raise HTTPException(status_code=404, detail="job not found")
    # Recruiter discovery runs here rather than on every fetched job: applying
    # is the point where contacts are actually wanted, and it keeps the search
    # spend proportional to applications rather than to the whole feed. Cached
    # 7 days per domain, so a second application to the same company is free.
    job = state.load_seen().get(job_id) or {}
    asyncio.create_task(_discover_after_apply(job))
    return {"ok": True}


async def _discover_after_apply(job: dict) -> None:
    """Fire-and-forget discovery — never let a search failure fail the apply."""
    try:
        await outreach.research_company(job.get("company", ""), job.get("url"))
    except Exception as e:
        logger.warning("post-apply recruiter discovery failed: %s", e)


@app.delete("/api/jobs/{job_id}")
async def delete_job(job_id: str):
    """User-curated removal from the Applied list (or any job, generally)."""
    seen = state.load_seen()
    if not state.delete_job(seen, job_id):
        raise HTTPException(status_code=404, detail="job not found")
    state.save_seen(seen)
    return {"ok": True}


@app.get("/api/jobs/{job_id}/contacts")
async def get_job_contacts(job_id: str):
    """Cache-only read — no Hunter call, free. Lets any other job at an
    already-researched company show prior contacts immediately."""
    job = state.load_seen().get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    result = enricher.get_company_contacts(job.get("company", ""), job.get("url"))
    return {"contacts": result["contacts"], "domain_guessed": result["domain_guessed"]}


@app.get("/api/jobs/{job_id}/outreach")
async def get_job_outreach(job_id: str):
    """Cache-only outreach view: previously discovered recruiters plus their
    ranked address guesses. No search, no Hunter credit."""
    job = state.load_seen().get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    resolved = enricher.resolve_domain(job.get("company", ""), job.get("url"))
    if resolved is None:
        raise HTTPException(status_code=422, detail="could not resolve a domain for this company")
    domain, guessed = resolved
    return {
        **outreach.build_report(domain, outreach.cached_candidates(domain) or []),
        "domain_guessed": guessed,
        "cached": True,
    }


@app.post("/api/jobs/{job_id}/outreach/discover")
async def discover_job_outreach(job_id: str, force: bool = False):
    """Run the recruiter search for this company (cached 7 days per domain).
    Google's CAPTCHA backoff is shared with the job search, so this can come
    back 503 with the time it unblocks — an empty list would read as "this
    company has no recruiters", which is a different and wrong answer."""
    job = state.load_seen().get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    result = await outreach.research_company(job.get("company", ""), job.get("url"), force=force)
    if result.get("error") == "no_domain":
        raise HTTPException(status_code=422, detail="could not resolve a domain for this company")
    if result.get("error") == "search_backoff":
        raise HTTPException(
            status_code=503,
            detail=f"google search is rate-limited until {result['blocked_until']}",
        )
    return result


@app.post("/api/jobs/{job_id}/contacts")
async def find_job_contact(job_id: str):
    """On-demand Hunter.io lookup — fired manually right after applying, not
    from the poll loop. Fetches one *new* contact and appends it to the
    per-company cache, so it (and every contact found before it) shows up on
    any other job at the same company via GET, at no further cost."""
    seen = state.load_seen()
    job = seen.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")

    result = await enricher.find_contact(job.get("company", ""), job.get("url"))
    if result.get("error") == "no_domain":
        raise HTTPException(status_code=422, detail="could not resolve a domain for this company")
    if result.get("error") == "quota_exhausted":
        raise HTTPException(status_code=503, detail="hunter monthly quota exhausted")

    job["contacts"] = result["contacts"]
    state.save_seen(seen)
    return {
        "contacts": result["contacts"],
        "domain_guessed": result["domain_guessed"],
        "new_contact": result["new_contact"],
    }


@app.post("/api/jobs/{job_id}/outreach/verify")
async def verify_job_outreach(job_id: str, source_url: str | None = None):
    """Confirm one contact's address for this job's company.

    Spends at most one Hunter credit, and often none: a stored confirmation
    for that person, or a domain pattern already verified twice, both answer
    for free. Whatever Hunter says is folded back into the pattern memory, so
    the credit also pays for every future contact at the same company."""
    job = state.load_seen().get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    resolved = enricher.resolve_domain(job.get("company", ""), job.get("url"))
    if resolved is None:
        raise HTTPException(status_code=422, detail="could not resolve a domain for this company")
    domain, _ = resolved

    candidates = outreach.cached_candidates(domain) or []
    contact = next((c for c in candidates if c.get("source_url") == source_url), None) \
        if source_url else (outreach.rank_contacts(candidates)[0] if candidates else None)
    if contact is None:
        raise HTTPException(status_code=404, detail="no discovered contact to verify")

    result = await outreach.verify_contact(domain, contact)
    if result.get("error") == "quota_exhausted":
        raise HTTPException(status_code=503, detail="hunter monthly quota exhausted")
    if result.get("error") == "no_api_key":
        raise HTTPException(status_code=503, detail="HUNTER_API_KEY is not set")
    if result.get("error"):
        raise HTTPException(status_code=502, detail=f"verification failed: {result['error']}")
    return {**result, "contact": contact, "domain": domain}


@app.post("/api/jobs/{job_id}/outreach/send")
async def send_job_outreach(job_id: str, payload: SendOutreachIn):
    """Send one outreach email and log it. Drafting happens outside — this
    endpoint owns the gates (kill switch, daily cap, dedup, verification) so
    they hold no matter what composed the text."""
    job = state.load_seen().get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    resolved = enricher.resolve_domain(job.get("company", ""), job.get("url"))
    domain = resolved[0] if resolved else ""

    candidates = outreach.cached_candidates(domain) or []
    contact = next((c for c in candidates
                    if (c.get("email") or "").lower() == payload.email.lower()
                    or c.get("source_url") == payload.source_url), {})
    report = outreach.build_report(domain, [contact] if contact else [])
    eligibility = (report["candidates"][0]["eligibility"] if report["candidates"]
                   else {"eligible": False, "basis": "unknown_contact",
                         "blockers": ["contact_not_discovered"]})

    gate = outreach.check_send_allowed(job_id, payload.email, domain, eligibility,
                                       override=payload.override)
    if not gate["allowed"]:
        raise HTTPException(status_code=409, detail={"reason": gate["reason"],
                                                     **{k: v for k, v in gate.items()
                                                        if k not in ("allowed", "reason")}})

    result = await outreach_mail.send_outreach(payload.email, payload.subject, payload.body,
                                               label=outreach.LABEL_SENT)
    record = outreach.record_outreach({
        "job_id": job_id, "domain": domain, "email": payload.email,
        "contact_name": contact.get("name"), "contact_title": contact.get("title"),
        "source_url": contact.get("source_url"), "subject": payload.subject,
        "body": payload.body, "send_eligibility": eligibility.get("basis"),
        "override_used": gate.get("override_used", False),
        "message_id": result.get("message_id"), "label": outreach.LABEL_SENT,
        "status": "sent" if result.get("sent") else "failed",
        "sent_at": outreach._iso() if result.get("sent") else None,
        "error": result.get("error"),
    })
    if not result.get("sent"):
        raise HTTPException(status_code=502, detail=f"send failed: {result.get('error')}")
    return {"ok": True, "record": record, "labelled": result.get("labelled")}


@app.get("/api/outreach/log")
async def get_outreach_log():
    log = outreach.load_log()
    return {"records": log, "sent_today": outreach.sent_today(log),
            "daily_cap": outreach.DAILY_SEND_CAP, "send_enabled": outreach.SEND_ENABLED}


@app.post("/api/outreach/sync-replies")
async def sync_outreach_replies():
    """Scan the inbox for replies to sent outreach and move those threads from
    the sent label to the replied one."""
    moved = await asyncio.to_thread(outreach_mail.sync_reply_labels)
    return {"moved": moved, "count": len(moved)}


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
    _check_ingest_token(x_ingest_token)
    incoming = await request.json()
    if not isinstance(incoming, list) or not all(
        isinstance(j, dict)
        and isinstance(j.get("id"), str)
        and isinstance(j.get("title"), str)
        and isinstance(j.get("company"), str)
        and isinstance(j.get("source"), str)
        for j in incoming
    ):
        raise HTTPException(status_code=400, detail="expected a list of job objects with string id, title, company, and source")
    seen = state.load_seen()
    added = 0
    now = datetime.now(timezone.utc).isoformat()
    for job in state.get_new_jobs(seen, incoming):
        job["scraped_at"] = now
        if not job.get("posted_at"):
            job["posted_at"] = job["scraped_at"]
        job["matched"] = matches(job)

        # Unmatched jobs are dropped immediately rather than persisted and
        # purged later — they're re-evaluated (cheaply) if the source still
        # lists them on the next ingest.
        if not job["matched"]:
            continue

        # Cross-source duplicates are merged right after the cheap regex
        # gate, before the expensive description fetch / AI review — a
        # posting scraped from two boards shouldn't burn a second AI call
        # just to be discarded as a duplicate afterward.
        dup_id = state.find_cross_source_duplicate(seen, job)
        if dup_id:
            sources = seen[dup_id].setdefault("sources", [seen[dup_id].get("source", "unknown")])
            if job["source"] not in sources:
                sources.append(job["source"])
            continue

        # Same AI second-pass gate as the poll_loop path (scraper.py):
        # regex only catches years-of-experience mentions that fit a fixed
        # pattern, so it still lets some over-experienced roles through.
        if not job.get("description"):
            job["description"] = await _fetch_job_description(job["url"])
            job["matched"] = matches(job)
        if job["matched"] and len(job.get("description", "")) > 100:
            verdict = await ai_match.review(job)
            if verdict is not None:
                if verdict["verdict"] == "reject":
                    job["matched"] = False
                else:
                    job["ai_score"] = verdict.get("score")
                    job["ai_resume"] = verdict.get("resume")
                    job["ai_reason"] = verdict.get("reason")
        if not job["matched"]:
            continue
        seen[job["id"]] = job
        added += 1
    state.save_seen(seen)
    return {"ingested": added}


# ---- Adaptive link-pattern sync (scrapers/playwright_scraper.py) ----
# The GH Action runner has no /data volume, so it borrows the host's learned
# job-link prefixes (see adaptive.py) through these instead of keeping its
# own copy. Same shared secret as /api/ingest.
def _check_ingest_token(x_ingest_token: str) -> None:
    token = os.getenv("INGEST_TOKEN")
    if not token or x_ingest_token != token:
        raise HTTPException(status_code=401, detail="invalid ingest token")


@app.get("/api/link-patterns")
async def get_link_patterns(x_ingest_token: str = Header(default="")):
    _check_ingest_token(x_ingest_token)
    return state.load_link_patterns()


@app.post("/api/link-patterns")
async def update_link_patterns(request: Request, x_ingest_token: str = Header(default="")):
    _check_ingest_token(x_ingest_token)
    incoming = await request.json()
    if not isinstance(incoming, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in incoming.items()
    ):
        raise HTTPException(status_code=400, detail="expected a company->prefix string mapping")
    patterns = state.load_link_patterns()
    patterns.update(incoming)
    state.save_link_patterns(patterns)
    return {"ok": True, "updated": len(incoming)}


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
    # Re-literalize from the allowlist instead of reusing the request value,
    # so the path built below is provably not attacker-controlled.
    slot = "backend" if slot == "backend" else "frontend"
    _check_resume_token(x_resume_token)

    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in RESUME_EXTENSIONS:
        raise HTTPException(status_code=400, detail="only .txt and .pdf resumes are accepted")
    ext = ".pdf" if ext == ".pdf" else ".txt"

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


# ---- Pipeline tracking ----
# Applications + event ledger live in Postgres (Supabase), scoped to this
# feature only — jobs stay in the JSON store. See
# i-am-building-a-reactive-pearl.md for the full design.
@app.post("/api/applications")
async def create_application(body: CreateApplicationIn, response: Response):
    if body.job_id not in state.load_seen():
        raise HTTPException(status_code=404, detail="job not found")
    application, created = await pipeline_db.create_or_get_application(
        body.job_id, body.job_title, body.company, body.job_url
    )
    if created:
        state.mark_applied(body.job_id)
        response.status_code = 201
    else:
        response.status_code = 200
    return application


@app.get("/api/pipeline")
async def get_pipeline():
    return await pipeline_db.get_pipeline()


@app.post("/api/applications/{application_id}/transition")
async def transition_application(application_id: str, body: TransitionIn):
    try:
        application, event = await pipeline_db.transition(
            application_id, body.to_state, body.note, body.scorecard, body.metadata
        )
    except pipeline_db.ApplicationNotFoundError:
        raise HTTPException(status_code=404, detail="application not found")
    except pipeline_state.TerminalStateError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except (pipeline_state.InvalidTransitionError, pipeline_state.GateNotSatisfiedError) as e:
        raise HTTPException(status_code=422, detail=str(e))
    await pipeline_events.notify(application, event)
    return application


@app.get("/api/applications/{application_id}/events")
async def get_application_events(application_id: str):
    return await pipeline_db.get_events(application_id)


# Serve the built React frontend (only if present — absent during backend-only dev).
if os.path.isdir(FRONTEND_DIST):
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="static")
