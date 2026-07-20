"""Canonicalize a job URL for dedup AND display.

Career-page hrefs often carry click-tracking params (`?utm_source=feed`,
`?ref=greenhouse-index`) that pass through `urljoin` unchanged and
silently produce duplicate job entries differing only by their tracking
query. Two hrefs surfaced from different referrers would emit two
`seen_hrefs` and two uid hashes — i.e. the same job listed twice.

Stripping them at extraction time gives one URL per role, and the
cleaner URL is also the one the user sees. Params removed here are ads
/ attribution only — they don't change what the link resolves to on the
ATS, so reusing the normalized form for display is safe.
"""
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

# Common click-tracking / analytics params. Lowercase; comparison in
# normalize_url is case-insensitive. Keep this list conservative: a
# slip here overwrites otherwise-distinct URLs.
_TRACKING_PARAMS = frozenset({
    # Google / Urchin
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "utm_name", "utm_brand", "utm_social", "utm_social_type",
    # Click identifiers (Meta, Google Ads, LinkedIn, Mailchimp)
    "fbclid", "gclid", "gclsrc", "dclid", "msclkid", "mc_cid", "mc_eid",
    "igshid", "yclid", "icid",
    # HubSpot / Vero
    "_ga", "_gl", "_hsenc", "_hsmi", "vero_id", "vero_conv",
    # Generic
    "trk", "trkcampaign", "trackingid", "ref", "ref_src",
    # Specific platforms
    "_branch_match_id",  # branch.io
    "spm", "scm",         # alibaba
})


def _drop_default_port(scheme: str, hostport: str) -> str:
    """Strip a default port (80 for http, 443 for https). Both forms are
    equivalent to the portless URL; keeping them around breaks
    hash-equality between the two."""
    defaults = {"http": "80", "https": "443"}
    if ":" not in hostport:
        return hostport
    host, _, port = hostport.partition(":")
    if port == defaults.get(scheme):
        return host
    return hostport


def normalize_url(url: str) -> str:
    """Return a canonicalized form of `url`:

    - scheme + host lowercased (DNS is case-insensitive)
    - userinfo stripped (rare tracking abuse; never legitimate for jobs)
    - default port (80/443) dropped
    - tracking query params removed (case-insensitive)
    - remaining query string sorted for stable equality
    - fragment stripped (never sent to the server anyway)
    - path's trailing slash removed (unless path == "/")
    - leading `www.` subdomain stripped

    The PATH is NOT lowercased — many sites have case-sensitive paths
    (GitHub blob URLs, Greenhouse board slugs). Lowercasing here would
    silently overwrite the right URL with a `404` candidate.
    """
    parsed = urlparse(url)

    scheme = (parsed.scheme or "").lower()
    netloc = parsed.netloc or ""

    # userinfo (user[:pass]@) is junk for job URLs — strip it.
    if "@" in netloc:
        netloc = netloc.rpartition("@")[2]

    # Drop default port + lowercase host; preserve path case.
    hostport = _drop_default_port(scheme, netloc.lower())

    # Strip `www.` prefix for sub-host uniformity only (leave `www2.`,
    # `www-archive.`, etc. alone — they're meaningful distinct hosts).
    if hostport.startswith("www."):
        hostport = hostport[4:]

    # Drop tracking params (case-insensitive) and sort the rest for
    # stable equality across query-string order variations.
    cleaned_qsl = [
        (k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=False)
        if k.lower() not in _TRACKING_PARAMS
    ]
    cleaned_qsl.sort()
    query = urlencode(cleaned_qsl, doseq=True)

    # Collapse trailing slash on non-root paths.
    path = parsed.path or ""
    if path != "/":
        path = path.rstrip("/") or "/"

    return urlunparse((scheme, hostport, path, "", query, ""))
