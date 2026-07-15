"""Google OAuth 配置与门闸单元测试（不连 Google）。"""

from __future__ import annotations

from pathlib import Path

from ncds_opus_factory.server.auth import (
    AuthConfig,
    email_allowed,
    hash_session_token,
    make_state_cookie,
    should_require_auth,
    verify_state_cookie,
)
from ncds_opus_factory.server.auth_store import AuthStore


def _cfg(**overrides: object) -> AuthConfig:
    base = dict(
        client_id="cid",
        client_secret="csec",
        session_secret="s3cret-s3cret-s3cret",
        allowed_emails=(),
        allowed_domain=None,
        public_base_url="http://127.0.0.1:8810",
    )
    base.update(overrides)
    return AuthConfig(**base)  # type: ignore[arg-type]


def test_auth_disabled_without_secrets() -> None:
    cfg = AuthConfig(
        client_id=None,
        client_secret=None,
        session_secret=None,
        allowed_emails=(),
        allowed_domain=None,
        public_base_url="http://127.0.0.1:8810",
    )
    assert cfg.enabled is False
    assert should_require_auth("/jobs", "GET", cfg) is False


def test_should_require_auth_when_enabled() -> None:
    cfg = _cfg()
    assert cfg.enabled is True
    assert should_require_auth("/jobs", "GET", cfg) is True
    assert should_require_auth("/tasks", "POST", cfg) is True
    assert should_require_auth("/health", "GET", cfg) is False
    assert should_require_auth("/api/auth/me", "GET", cfg) is False
    assert should_require_auth("/api/auth/google/login", "GET", cfg) is False
    assert should_require_auth("/studio/", "GET", cfg) is False
    assert should_require_auth("/studio/assets/x.js", "GET", cfg) is False
    assert should_require_auth("/jobs", "OPTIONS", cfg) is False


def test_email_allowlist_and_domain() -> None:
    assert email_allowed(_cfg(allowed_emails=("a@x.com",)), "a@x.com") is True
    assert email_allowed(_cfg(allowed_emails=("a@x.com",)), "b@x.com") is False
    assert email_allowed(_cfg(allowed_domain="vooice.tech"), "me@vooice.tech") is True
    assert email_allowed(_cfg(allowed_domain="vooice.tech"), "me@gmail.com") is False
    assert email_allowed(_cfg(), "anyone@gmail.com") is True


def test_state_cookie_roundtrip() -> None:
    cfg = _cfg()
    state = "abc-state-token"
    cookie = make_state_cookie(state, cfg)
    assert verify_state_cookie(cookie, state, cfg) is True
    assert verify_state_cookie(cookie, "wrong", cfg) is False
    assert verify_state_cookie("tampered", state, cfg) is False


def test_auth_store_user_session(tmp_path: Path) -> None:
    store = AuthStore(tmp_path / "auth.db")
    user = store.upsert_auth_user(
        google_sub="sub-1",
        email="u@example.com",
        name="U",
        picture_url=None,
    )
    assert user.id >= 1
    assert user.email == "u@example.com"

    token = "raw-session-token"
    store.create_auth_session(
        user_id=user.id,
        session_hash=hash_session_token(token),
        expires_at="2099-01-01 00:00:00",
    )
    found = store.get_auth_user_by_session(
        session_hash=hash_session_token(token),
        now="2026-01-01 00:00:00",
    )
    assert found is not None
    assert found.email == "u@example.com"

    store.delete_auth_session(hash_session_token(token))
    assert (
        store.get_auth_user_by_session(
            session_hash=hash_session_token(token),
            now="2026-01-01 00:00:00",
        )
        is None
    )


def test_redirect_uri() -> None:
    cfg = _cfg(public_base_url="https://opus.vooice.tech")
    assert cfg.redirect_uri == "https://opus.vooice.tech/api/auth/google/callback"
    assert cfg.secure_cookie is True
