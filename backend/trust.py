"""Advisory trust/legitimacy check for scraped postings (finding #3). Never
drops a posting — only returns a reason string to flag it. Reasons surface
via job["low_confidence"] so the frontend/health endpoint can show them;
an unread flag is dead weight (senior-pass caveat), so callers must wire
this to a visible surface, not just a log line."""
from urllib.parse import urlparse

_SHORTENERS = {"bit.ly", "tinyurl.com", "t.co", "goo.gl", "ow.ly", "buff.ly"}


def score_posting(job: dict, company: dict | None) -> str | None:
    url = job.get("url", "")
    if not url:
        return "missing url"
    try:
        host = urlparse(url).hostname or ""
    except ValueError:
        return "unparseable url"
    if not host:
        return "missing host"
    if any(host == s or host.endswith("." + s) for s in _SHORTENERS):
        return "link-shortener domain"
    domain = company.get("domain") if company else None
    if domain and job.get("source") == "custom" and not (host == domain or host.endswith("." + domain)):
        return f"url host {host} doesn't match company domain {domain}"
    return None
