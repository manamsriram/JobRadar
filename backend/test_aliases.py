"""Company alias canonicalization tests.

Run: pytest backend/test_aliases.py
"""
import pytest

from aliases import canonicalize_company


def test_maps_known_alias_case_insensitive():
    aliases = {"acme ai inc": "Acme AI"}
    assert canonicalize_company("Acme AI Inc", aliases) == "Acme AI"


def test_unmapped_name_passes_through_unchanged():
    assert canonicalize_company("Totally New Co", {}) == "Totally New Co"


def test_empty_name_passes_through():
    assert canonicalize_company("", {"x": "Y"}) == ""


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
