import html
import re

# Descriptions are stored to power body-level filtering + UI display. Cap length
# so the free-tier Supabase table stays small (a few KB/row over <=3000 rows).
MAX_DESC_CHARS = 5000
_TAG_RE = re.compile(r"<[^>]+>")


def plaintext(raw: str | None) -> str:
    """Strip HTML tags + unescape entities to a trimmed, capped plaintext blob.
    Safe on already-plain text (just collapses whitespace and caps length)."""
    if not raw:
        return ""
    text = _TAG_RE.sub(" ", html.unescape(raw))
    return re.sub(r"\s+", " ", text).strip()[:MAX_DESC_CHARS]
