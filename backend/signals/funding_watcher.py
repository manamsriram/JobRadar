"""TechCrunch funding-signal watcher (design doc §3).

Polls the TechCrunch funding RSS feed. Each newly-funded company is a hiring
signal — freshly raised startups staff up fast. New entries are pushed onto the
funding queue (persisted on /data) for the orchestrator to discover careers pages
for. Seen-funding ids dedupe across restarts.
"""
import asyncio
import random
import re
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
# defusedxml, not stdlib ElementTree: the feed is remote untrusted input and the
# stdlib parser is vulnerable to entity-expansion (billion-laughs) attacks.
from defusedxml import ElementTree

import state
from net_safety import is_safe_url

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_FEED = "https://techcrunch.com/tag/funding/feed/"

# Hosts that are never the funded company: TC itself, socials, aggregators.
# Used to skip past boilerplate links when scanning an article for the company site.
_SKIP_HOSTS = (
    "techcrunch.com", "twitter.com", "x.com", "linkedin.com", "facebook.com",
    "instagram.com", "youtube.com", "crunchbase.com", "google.com", "apple.com",
    "wordpress.org", "yahoo.com", "reddit.com", "github.com", "medium.com",
    "wikipedia.org", "bit.ly", "t.co",
)

# TC funding headlines read "<Company> raises/lands/secures/nabs $Xm ...".
# Fallback only — resolve_domain_from_article() is tried first because it reads
# the real outbound link. This regex guesses "<company>.com" from the name, which
# breaks on multi-word/rebranded names ("Hugging Face" -> huggingface.com is luck).
_HEADLINE_RE = re.compile(
    r"^([A-Z][\w.&'-]*(?:\s+[A-Z][\w.&'-]*){0,2})\s+"
    r"(?:raises|lands|secures|nabs|closes|banks|scores|grabs|snags)\b",
)


def _guess_company_domain(headline: str) -> tuple[str | None, str | None]:
    m = _HEADLINE_RE.match(headline.strip())
    if not m:
        return None, None
    company = m.group(1).strip()
    slug = re.sub(r"[^a-z0-9]", "", company.lower())
    return company, (f"{slug}.com" if slug else None)


def _host(url: str) -> str | None:
    try:
        host = urlparse(url).netloc.lower()
    except ValueError:
        return None
    host = host.split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    if not host or "." not in host:
        return None
    if any(host == s or host.endswith("." + s) for s in _SKIP_HOSTS):
        return None
    if not is_safe_url(f"https://{host}"):
        return None
    return host


async def resolve_domain_from_article(article_url: str, company: str | None) -> str | None:
    """Read a TC article and return the funded company's real domain.

    TC hyperlinks the startup's name to its homepage in the article body, so the
    first outbound non-boilerplate link is almost always the company site. Prefers
    a link whose host echoes the company name; otherwise takes the first outbound
    link inside the article body.
    """
    await asyncio.sleep(random.uniform(1.5, 3.0))  # polite jitter
    try:
        async with httpx.AsyncClient(headers={"User-Agent": _UA}) as client:
            r = await client.get(article_url, timeout=15, follow_redirects=True)
            r.raise_for_status()
    except httpx.HTTPError as e:
        print(f"[funding_watcher] error fetching article {article_url}: {e}")
        return None

    soup = BeautifulSoup(r.text, "html.parser")
    # Broken selector? TC wraps post copy in <div class="entry-content"> (or an
    # article-content variant). Falling back to the whole page still works, just
    # with more nav/footer noise to skip.
    body = soup.select_one("div.entry-content, div[class*='article-content'], article") or soup
    hosts = [h for h in (_host(a.get("href", "")) for a in body.find_all("a", href=True)) if h]
    if not hosts:
        return None

    # Prefer a host that contains the company slug ("Acme AI" -> acmeai.com/acme.ai).
    if company:
        slug = re.sub(r"[^a-z0-9]", "", company.lower())
        if slug:
            for h in hosts:
                if slug in re.sub(r"[^a-z0-9]", "", h):
                    return h
    return hosts[0]


async def check_funding() -> list[dict]:
    """Fetch the feed, enqueue unseen funding items, return the newly enqueued ones."""
    await asyncio.sleep(random.uniform(1.5, 3.0))  # polite jitter
    try:
        async with httpx.AsyncClient(headers={"User-Agent": _UA}) as client:
            r = await client.get(_FEED, timeout=15, follow_redirects=True)
            r.raise_for_status()
    except httpx.HTTPError as e:
        print(f"[funding_watcher] error: {e}")
        return []

    # RSS is XML — parse it with an XML parser. (An HTML parser silently drops
    # <link> text because <link> is a void element in HTML, which leaves every
    # article_url empty.) Broken? If items is empty, confirm the feed still uses
    # <item>/<title>/<link>/<guid> tags.
    try:
        root = ElementTree.fromstring(r.text)
    except ElementTree.ParseError as e:
        print(f"[funding_watcher] error parsing feed XML: {e}")
        return []
    items = root.findall(".//item")

    seen = state.load_seen_funding()
    queue = state.load_funding_queue()
    queued_ids = {q.get("id") for q in queue}
    new_entries: list[dict] = []

    for item in items:
        def _text(tag: str) -> str:
            el = item.find(tag)
            return (el.text or "").strip() if el is not None else ""

        link = _text("link")
        guid = _text("guid") or link
        if not guid or guid in seen or guid in queued_ids:
            continue
        title = _text("title")
        company, domain = _guess_company_domain(title)
        entry = {
            "id": guid, "headline": title, "article_url": link,
            "company": company, "domain": domain, "careers_url": None,
        }
        new_entries.append(entry)
        seen.add(guid)

    if new_entries:
        state.save_funding_queue(queue + new_entries)
        state.save_seen_funding(seen)
        print(f"[funding_watcher] enqueued {len(new_entries)} funding signal(s).")
    return new_entries
