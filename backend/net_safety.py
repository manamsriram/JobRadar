"""SSRF guard shared by careers_discovery and funding_watcher.

Both modules turn attacker-influencible strings (a funding headline, a link
scraped out of a news article) into outbound HTTP requests. Without this check
a crafted "domain" or a redirect could point at loopback/link-local/RFC1918
addresses (cloud metadata endpoints, internal admin panels, etc).
"""
import ipaddress
import socket
from urllib.parse import urlparse


def _is_public_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )


def is_safe_url(url: str) -> bool:
    """True if `url` is http(s) and its host resolves only to public IPs.

    Known gap: this resolves DNS now and the caller's HTTP client (httpx) resolves
    again at connect time. A DNS-rebinding attacker who flips the record between
    these two lookups slips past this check. Not fixed: pinning the validated IP
    while keeping the original Host/SNI needs a custom httpx transport, which is
    more machinery than this single-user hobby project's threat model justifies.
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except (socket.gaierror, UnicodeError):
        return False
    ips = {info[4][0] for info in infos}
    return bool(ips) and all(_is_public_ip(ip) for ip in ips)
