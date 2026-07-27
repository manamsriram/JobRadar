"""Gmail SMTP alerts — one digest email per alert cycle (design doc §6).

Degrades gracefully (logs + returns False) when Gmail credentials are unset.
Use a Gmail App Password (requires 2FA), not the account login password.
"""
import os
from email.message import EmailMessage

import aiosmtplib

SOURCE_LABELS = {
    "custom": "Company site",
    "levels": "Levels.fyi",
    "funding": "Funding signal",
}


def _format_body(job: dict) -> str:
    source = SOURCE_LABELS.get(job.get("source", ""), job.get("source", ""))
    lines = [
        f"{job.get('title', '')} — {job.get('company', '')}",
        f"Location: {job.get('location', '')}",
        f"Source:   {source}",
        f"Posted:   {job.get('posted_at') or '—'}",
        f"Apply:    {job.get('url', '')}",
    ]
    if job.get("ai_score") is not None:
        lines.append(
            f"Fit:      {job['ai_score']}/100 ({job.get('ai_resume', '?')} resume) — {job.get('ai_reason', '')}"
        )
    for c in job.get("contacts") or []:
        parts = [f"{c.get('name', '')} ({c.get('title', '')})"]
        if c.get("email"):
            parts.append(c["email"])
        if c.get("linkedin"):
            parts.append(c["linkedin"])
        lines.append("Contact: " + " — ".join(p for p in parts if p))
    return "\n".join(lines)


async def send_pipeline_alert(application: dict, event: dict) -> bool:
    """Email one alert per pipeline state transition. Registered as a
    pipeline_events subscriber (backend/pipeline_events.py)."""
    user = os.getenv("GMAIL_USER")
    password = os.getenv("GMAIL_APP_PASSWORD")
    alert_to = os.getenv("ALERT_TO") or user
    if not user or not password:
        print("[notifier] Gmail credentials unset — skipping pipeline alert.")
        return False

    from_state = event.get("from_state") or "—"
    to_state = event["to_state"]
    subject_verb = "logged" if from_state == to_state else "moved"

    msg = EmailMessage()
    msg["Subject"] = f"📋 JobRadar: {application['company']} {subject_verb} to {to_state}"
    msg["From"] = user
    msg["To"] = alert_to
    lines = [
        f"{application['job_title']} — {application['company']}",
        f"State:    {from_state} -> {to_state}",
        f"Apply:    {application.get('job_url') or '—'}",
    ]
    if event.get("note"):
        lines.append(f"Note:     {event['note']}")
    if event.get("scorecard"):
        lines.append(f"Scorecard: {event['scorecard']}")
    msg.set_content("\n".join(lines))

    try:
        await aiosmtplib.send(
            msg, hostname="smtp.gmail.com", port=465,
            username=user, password=password, use_tls=True,
        )
    except Exception as e:
        print(f"[notifier] pipeline alert error: {e}")
        return False
    return True


async def send_digest_alert(jobs: list[dict]) -> bool:
    """Email a digest of the given (already-limited) job list. Returns True if sent."""
    if not jobs:
        return False
    user = os.getenv("GMAIL_USER")
    password = os.getenv("GMAIL_APP_PASSWORD")
    alert_to = os.getenv("ALERT_TO") or user
    if not user or not password:
        print("[notifier] Gmail credentials unset — skipping alert.")
        return False

    msg = EmailMessage()
    msg["Subject"] = f"🚨 JobRadar: {len(jobs)} new match{'es' if len(jobs) != 1 else ''}"
    msg["From"] = user
    msg["To"] = alert_to
    msg.set_content("\n\n".join(_format_body(job) for job in jobs))

    try:
        await aiosmtplib.send(
            msg,
            hostname="smtp.gmail.com",
            port=465,
            username=user,
            password=password,
            use_tls=True,
        )
    except Exception as e:
        print(f"[notifier] error: {e}")
        return False
    return True
