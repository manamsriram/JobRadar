"""Gmail SMTP digest notifier.

Sends ONE email per digest tick containing up to DIGEST_MAX_JOBS matched jobs.
Degrades gracefully (logs + returns) when Gmail credentials are unset (F5).
Use a Gmail App Password (requires 2FA), not your login password.
"""
import logging
import os

import aiosmtplib
from email.message import EmailMessage

log = logging.getLogger(__name__)


def _format_job(job: dict) -> str:
    lines = [
        f"• {job.get('title', '')} — {job.get('company', '')}",
        f"    Location: {job.get('location', '')}",
        f"    Posted:   {job.get('posted_at', '')}",
        f"    Apply:    {job.get('url', '')}",
    ]
    for c in job.get("contacts") or []:
        parts = [f"{c['name']} ({c.get('title', '')})"]
        if c.get("email"):
            parts.append(c["email"])
        if c.get("linkedin"):
            parts.append(c["linkedin"])
        lines.append("    Contact: " + " — ".join(parts))
    return "\n".join(lines)


async def send_digest(jobs: list[dict]) -> bool:
    """Send a digest email for `jobs`. Returns True if sent, False if skipped."""
    if not jobs:
        return False

    user = os.getenv("GMAIL_USER")
    password = os.getenv("GMAIL_APP_PASSWORD")
    alert_to = os.getenv("ALERT_TO") or user
    if not user or not password:
        log.warning("Gmail credentials unset — skipping digest of %d job(s).", len(jobs))
        return False

    body = f"{len(jobs)} new matching job(s):\n\n" + "\n\n".join(
        _format_job(j) for j in jobs
    )

    msg = EmailMessage()
    msg["Subject"] = f"🚨 JobRadar: {len(jobs)} new match(es)"
    msg["From"] = user
    msg["To"] = alert_to
    msg.set_content(body)

    await aiosmtplib.send(
        msg,
        hostname="smtp.gmail.com",
        port=465,
        username=user,
        password=password,
        use_tls=True,
    )
    return True
