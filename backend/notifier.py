"""Gmail SMTP alerts — one email per matched job (design doc §6).

Tight new-grad filters keep volume low, so per-job alerts stay manageable and
each email is actionable on its own (no digest batching). Degrades gracefully
(logs + returns False) when Gmail credentials are unset. Use a Gmail App Password
(requires 2FA), not the account login password.
"""
import os
from email.message import EmailMessage

import aiosmtplib

SOURCE_LABELS = {
    "custom": "Company site",
    "levels": "Levels.fyi",
    "yc": "Y Combinator",
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
    for c in job.get("contacts") or []:
        parts = [f"{c.get('name', '')} ({c.get('title', '')})"]
        if c.get("email"):
            parts.append(c["email"])
        if c.get("linkedin"):
            parts.append(c["linkedin"])
        lines.append("Contact: " + " — ".join(p for p in parts if p))
    return "\n".join(lines)


async def send_email_alert(job: dict) -> bool:
    """Email a single matched job. Returns True if sent, False if skipped."""
    user = os.getenv("GMAIL_USER")
    password = os.getenv("GMAIL_APP_PASSWORD")
    alert_to = os.getenv("ALERT_TO") or user
    if not user or not password:
        print("[notifier] Gmail credentials unset — skipping alert.")
        return False

    msg = EmailMessage()
    msg["Subject"] = f"🚨 JobRadar: {job.get('title', 'New role')} @ {job.get('company', '')}"
    msg["From"] = user
    msg["To"] = alert_to
    msg.set_content(_format_body(job))

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
