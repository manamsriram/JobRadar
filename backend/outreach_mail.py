"""Sending outreach and keeping Gmail labels in step with its stage.

Why IMAP and not the Gmail API: SMTP can send but cannot label, and the Gmail
API would mean an OAuth client, a consent screen and a refresh token. Gmail
exposes every label as an IMAP folder, and `imaplib` is stdlib, so the same
GMAIL_APP_PASSWORD that already sends the job alerts can do the labelling too:
APPEND the sent message into "reach out", and when a reply shows up, APPEND it
into "hiring" and delete the copy from "reach out".

Every function degrades to False/None with a log line when credentials are
unset — same contract as notifier.py. A labelling failure never fails a send:
the mail is already gone, and a missing label is a cosmetic problem.
"""
import asyncio
import email
import imaplib
import logging
import os
import time
from email.message import EmailMessage
from email.utils import make_msgid, parsedate_to_datetime

import aiosmtplib

import outreach

logger = logging.getLogger(__name__)

IMAP_HOST = os.getenv("GMAIL_IMAP_HOST", "imap.gmail.com")
SMTP_HOST = os.getenv("GMAIL_SMTP_HOST", "smtp.gmail.com")


def _credentials() -> tuple[str, str] | None:
    user, password = os.getenv("GMAIL_USER"), os.getenv("GMAIL_APP_PASSWORD")
    if not user or not password:
        logger.warning("outreach_mail: Gmail credentials unset — skipping")
        return None
    return user, password


def build_message(to_email: str, subject: str, body: str, from_email: str,
                  reply_to: str | None = None) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_email
    msg["To"] = to_email
    msg["Message-ID"] = make_msgid()
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(body)
    return msg


async def send_outreach(to_email: str, subject: str, body: str,
                        label: str | None = None) -> dict:
    """Send one outreach email and file a copy under `label`.

    Returns {"sent": bool, "message_id": str|None, "labelled": bool}. The
    caller decides what to log — this function never touches the outreach log,
    so a send can't be recorded as something it wasn't.
    """
    creds = _credentials()
    if not creds:
        return {"sent": False, "error": "no_credentials"}
    user, password = creds

    msg = build_message(to_email, subject, body, from_email=user)
    try:
        await aiosmtplib.send(msg, hostname=SMTP_HOST, port=465,
                              username=user, password=password, use_tls=True)
    except Exception as e:
        logger.error("outreach_mail: send failed: %s", e)
        return {"sent": False, "error": str(e)}

    labelled = False
    if label:
        # Off the event loop: imaplib is blocking and this is not worth an
        # async IMAP dependency for one APPEND per sent mail.
        labelled = await asyncio.to_thread(append_to_label, msg, label)
    return {"sent": True, "message_id": msg["Message-ID"], "labelled": labelled}


def append_to_label(msg: EmailMessage, label: str) -> bool:
    """File a copy of `msg` under a Gmail label (an IMAP folder), creating the
    label if it doesn't exist yet."""
    creds = _credentials()
    if not creds:
        return False
    user, password = creds
    try:
        with imaplib.IMAP4_SSL(IMAP_HOST) as imap:
            imap.login(user, password)
            imap.create(_quote(label))  # already-exists is a no-op error
            status, _ = imap.append(_quote(label), "\\Seen",
                                    imaplib.Time2Internaldate(time.time()),
                                    msg.as_bytes())
        return status == "OK"
    except Exception as e:
        logger.warning("outreach_mail: labelling %r failed: %s", label, e)
        return False


def _quote(label: str) -> str:
    """IMAP mailbox names with spaces must be quoted ("reach out")."""
    return f'"{label}"'


def find_replies(message_ids: list[str], since_days: int = 60) -> set[str]:
    """Which of our sent Message-IDs have been replied to.

    Matched on References/In-Reply-To rather than on the sender address: a
    recruiter often replies from a different address than the one mailed
    (shared inbox, ATS relay), and a threaded reply is the reliable signal.
    """
    creds = _credentials()
    if not creds or not message_ids:
        return set()
    user, password = creds
    wanted = {m for m in message_ids if m}
    replied: set[str] = set()
    try:
        with imaplib.IMAP4_SSL(IMAP_HOST) as imap:
            imap.login(user, password)
            imap.select("INBOX", readonly=True)
            since = time.strftime("%d-%b-%Y", time.gmtime(time.time() - since_days * 86400))
            status, data = imap.search(None, "SINCE", since)
            if status != "OK":
                return set()
            for num in (data[0] or b"").split():
                status, fetched = imap.fetch(num, "(BODY.PEEK[HEADER])")
                if status != "OK" or not fetched or not isinstance(fetched[0], tuple):
                    continue
                headers = email.message_from_bytes(fetched[0][1])
                refs = f"{headers.get('In-Reply-To', '')} {headers.get('References', '')}"
                replied |= {mid for mid in wanted if mid in refs}
    except Exception as e:
        logger.warning("outreach_mail: reply scan failed: %s", e)
    return replied


def move_label(msg_id: str, from_label: str, to_label: str) -> bool:
    """Move our filed copy between stage labels. Best-effort: if the copy
    isn't found under `from_label` (user moved it, or labelling failed at send
    time), the message is left alone rather than duplicated."""
    creds = _credentials()
    if not creds:
        return False
    user, password = creds
    try:
        with imaplib.IMAP4_SSL(IMAP_HOST) as imap:
            imap.login(user, password)
            imap.create(_quote(to_label))
            status, _ = imap.select(_quote(from_label))
            if status != "OK":
                return False
            status, data = imap.search(None, "HEADER", "Message-ID", msg_id)
            nums = (data[0] or b"").split() if status == "OK" else []
            if not nums:
                return False
            for num in nums:
                imap.copy(num, _quote(to_label))
                imap.store(num, "+FLAGS", "\\Deleted")
            imap.expunge()
        return True
    except Exception as e:
        logger.warning("outreach_mail: relabel failed: %s", e)
        return False


def sync_reply_labels(since_days: int = 60) -> list[dict]:
    """Scan for replies to anything still marked sent, and promote those
    records (and their Gmail label) to the replied stage. Returns the records
    that moved."""
    log = outreach.load_log()
    pending = {r.get("message_id"): r for r in log
               if r.get("status") == "sent" and r.get("message_id")}
    if not pending:
        return []
    moved = []
    for msg_id in find_replies(list(pending), since_days):
        record = pending[msg_id]
        move_label(msg_id, outreach.LABEL_SENT, outreach.LABEL_REPLIED)
        updated = outreach.mark_replied(record["id"])
        if updated:
            moved.append(updated)
    return moved
