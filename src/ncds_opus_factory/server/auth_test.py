"""OAuth 配置与门闸单元测试（不连 Google/Apple 真网）。"""

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
    base: dict = dict(
        client_id="cid",
        client_secret="csec",
        google_ios_client_id=None,
        google_android_client_id=None,
        session_secret="s3cret-s3cret-s3cret",
        allowed_emails=(),
        allowed_domain=None,
        public_base_url="http://127.0.0.1:8810",
        apple_team_id=None,
        apple_key_id=None,
        apple_services_id=None,
        apple_bundle_id=None,
        apple_private_key=None,
    )
    base.update(overrides)
    return AuthConfig(**base)  # type: ignore[arg-type]


def test_auth_disabled_without_secrets() -> None:
    cfg = _cfg(client_id=None, client_secret=None, session_secret=None)
    assert cfg.enabled is False
    assert should_require_auth("/jobs", "GET", cfg) is False


def test_google_only_enabled() -> None:
    cfg = _cfg()
    assert cfg.google_enabled is True
    assert cfg.apple_enabled is False
    assert cfg.enabled is True


def test_apple_only_enabled() -> None:
    cfg = _cfg(
        client_id=None,
        client_secret=None,
        apple_team_id="TEAM",
        apple_key_id="KEY",
        apple_services_id="com.example.web",
        apple_bundle_id="com.example.app",
        apple_private_key="-----BEGIN PRIVATE KEY-----\nMII\n-----END PRIVATE KEY-----",
    )
    assert cfg.apple_enabled is True
    assert cfg.enabled is True
    assert cfg.apple_redirect_uri.endswith("/api/auth/apple/callback")


def test_should_require_auth_when_enabled() -> None:
    cfg = _cfg()
    assert should_require_auth("/jobs", "GET", cfg) is True
    assert should_require_auth("/api/auth/me", "GET", cfg) is False
    assert should_require_auth("/api/auth/apple/callback", "POST", cfg) is False
    assert should_require_auth("/studio/", "GET", cfg) is False


def test_email_allowlist_and_domain() -> None:
    assert email_allowed(_cfg(allowed_emails=("a@x.com",)), "a@x.com") is True
    assert email_allowed(_cfg(allowed_emails=("a@x.com",)), "b@x.com") is False
    assert email_allowed(_cfg(allowed_domain="vooice.tech"), "me@vooice.tech") is True


def test_state_cookie_roundtrip() -> None:
    cfg = _cfg()
    state = "abc-state-token"
    cookie = make_state_cookie(state, cfg)
    assert verify_state_cookie(cookie, state, cfg) is True
    assert verify_state_cookie(cookie, "wrong", cfg) is False


def test_auth_store_multi_provider(tmp_path: Path) -> None:
    store = AuthStore(tmp_path / "auth.db")
    g = store.upsert_auth_user(
        provider="google",
        provider_sub="g-sub-1",
        email="g@example.com",
        name="G",
        picture_url=None,
    )
    a = store.upsert_auth_user(
        provider="apple",
        provider_sub="a-sub-1",
        email="a@example.com",
        name="A",
        picture_url=None,
    )
    assert g.id != a.id
    assert g.provider == "google"
    assert a.provider == "apple"

    token = "raw-session"
    store.create_auth_session(
        user_id=a.id,
        session_hash=hash_session_token(token),
        expires_at="2099-01-01 00:00:00",
    )
    found = store.get_auth_user_by_session(
        session_hash=hash_session_token(token),
        now="2026-01-01 00:00:00",
    )
    assert found is not None
    assert found.provider == "apple"
    assert found.email == "a@example.com"
