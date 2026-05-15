"""Pure-function tests for `app.core.auth.session.extract_*` helpers.

La auditoría 2026-05 marcó como Critical el gap de cobertura del módulo
de auth — éste cubre los extractors. Los tests de `lookup_user_id_for_token`
con DB real viven en `tests/integration/` cuando se agreguen.
"""

from __future__ import annotations

import pytest

from app.core.auth.session import extract_bearer_token, extract_session_token


# -----------------------------------------------------------------------------
# extract_session_token (cookie)
# -----------------------------------------------------------------------------


class TestExtractSessionToken:
    def test_returns_none_on_empty(self) -> None:
        assert extract_session_token(None) is None
        assert extract_session_token("") is None

    def test_strips_hmac_suffix(self) -> None:
        # Token raw "abc123", HMAC "def456" → cookie "abc123.def456"
        assert extract_session_token("abc123.def456") == "abc123"

    def test_url_decodes_before_split(self) -> None:
        # BetterAuth URL-encodes the cookie. "abc%3Cxyz.hmac" → "abc<xyz.hmac"
        assert extract_session_token("abc%3Cxyz.hmac") == "abc<xyz"

    def test_no_dot_returns_full_token(self) -> None:
        # Transitional / older format: no HMAC suffix → return as-is.
        assert extract_session_token("plaintoken") == "plaintoken"

    def test_empty_token_part_returns_none(self) -> None:
        # Edge: cookie is ".hmac" → token part is empty.
        assert extract_session_token(".hmac") is None

    @pytest.mark.parametrize(
        "bad",
        [
            "...",  # multiple dots — rsplit(".",1) keeps "..", which is non-empty
            "a.b.c",  # token = "a.b" — valid because we use rsplit
        ],
    )
    def test_multiple_dots_rsplit_only_last(self, bad: str) -> None:
        """rsplit('.', 1) only trims the LAST dot — multi-dot tokens stay
        intact except for the trailing HMAC. Pinning this behaviour so a
        future refactor doesn't switch to split() which would mangle them."""
        result = extract_session_token(bad)
        # Si el resultado no es None, debe contener al menos un dot.
        # (E.g. "a.b.c" → "a.b"; "..." → "..").
        assert result is not None
        assert "." in result or result == bad.rsplit(".", 1)[0]


# -----------------------------------------------------------------------------
# extract_bearer_token (Authorization header)
# -----------------------------------------------------------------------------


class TestExtractBearerToken:
    def test_returns_none_on_empty(self) -> None:
        assert extract_bearer_token(None) is None
        assert extract_bearer_token("") is None

    def test_strips_hmac_suffix(self) -> None:
        assert extract_bearer_token("Bearer abc123.def456") == "abc123"

    def test_case_insensitive_scheme(self) -> None:
        # HTTP spec allows mixed-case "Bearer".
        assert extract_bearer_token("bearer abc.h") == "abc"
        assert extract_bearer_token("BEARER abc.h") == "abc"
        assert extract_bearer_token("BeArEr abc.h") == "abc"

    def test_rejects_non_bearer_schemes(self) -> None:
        assert extract_bearer_token("Basic dXNlcjpwYXNz") is None
        assert extract_bearer_token("Token abc.h") is None

    def test_no_dot_returns_full_token(self) -> None:
        assert extract_bearer_token("Bearer plaintoken") == "plaintoken"

    def test_empty_token_returns_none(self) -> None:
        assert extract_bearer_token("Bearer ") is None
        assert extract_bearer_token("Bearer .hmac") is None

    def test_missing_scheme_returns_none(self) -> None:
        # Just a token without "Bearer " prefix — fails parse_format.
        assert extract_bearer_token("abc123.hmac") is None
