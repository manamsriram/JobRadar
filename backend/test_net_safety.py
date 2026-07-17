"""SSRF guard tests: net_safety.is_safe_url.

Run: pytest backend/test_net_safety.py
"""
import socket

import pytest

from net_safety import is_safe_url


def _fake_getaddrinfo(ip: str):
    def _inner(host, port):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]
    return _inner


def test_rejects_non_http_scheme():
    assert not is_safe_url("ftp://example.com")


def test_rejects_missing_hostname():
    assert not is_safe_url("http://")


def test_accepts_public_ip(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("93.184.216.34"))
    assert is_safe_url("https://example.com")


def test_rejects_loopback_ip(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("127.0.0.1"))
    assert not is_safe_url("https://evil.example.com")


def test_rejects_private_ip(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("10.0.0.5"))
    assert not is_safe_url("https://internal.example.com")


def test_rejects_link_local_ip(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("169.254.169.254"))
    assert not is_safe_url("https://metadata.example.com")


def test_dns_failure_rejected(monkeypatch):
    def _raise(host, port):
        raise socket.gaierror("no such host")
    monkeypatch.setattr(socket, "getaddrinfo", _raise)
    assert not is_safe_url("https://nonexistent.invalid")


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
