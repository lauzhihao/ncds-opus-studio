"""Google OAuth + cookie session（对齐 vps-insight/src/vps_insight/auth.py）。

未配置 GOOGLE_CLIENT_ID / SECRET / AUTH_SESSION_SECRET 时 auth 关闭，
行为与历史一致：全站可访问，/api/auth/me 返回 authRequired=false。
路径对齐 vps-insight：/api/auth/*。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException, Request
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token

from ncds_opus_factory.server.auth_store import AuthStore, AuthUserRecord, now_utc_text


SESSION_COOKIE = "nof_session"
STATE_COOKIE = "nof_oauth_state"
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
SESSION_TTL_DAYS = 30
STATE_TTL_SECONDS = 600
STUDIO_HOME = "/studio/"


@dataclass(frozen=True)
class AuthConfig:
    client_id: str | None
    client_secret: str | None
    session_secret: str | None
    allowed_emails: tuple[str, ...]
    allowed_domain: str | None
    public_base_url: str

    @property
    def enabled(self) -> bool:
        return bool(self.client_id and self.client_secret and self.session_secret)

    @property
    def secure_cookie(self) -> bool:
        return self.public_base_url.startswith("https://")

    @property
    def redirect_uri(self) -> str:
        return f"{self.public_base_url}/api/auth/google/callback"


def load_auth_config() -> AuthConfig:
    public_base_url = (
        optional_env("PUBLIC_BASE_URL")
        or optional_env("NOF_PUBLIC_BASE_URL")
        or "http://127.0.0.1:8810"
    ).rstrip("/")
    return AuthConfig(
        client_id=optional_env("GOOGLE_CLIENT_ID"),
        client_secret=optional_env("GOOGLE_CLIENT_SECRET"),
        session_secret=optional_env("AUTH_SESSION_SECRET"),
        allowed_emails=parse_csv_env("AUTH_ALLOWED_EMAILS"),
        allowed_domain=normalize_domain(optional_env("AUTH_ALLOWED_DOMAIN")),
        public_base_url=public_base_url,
    )


def auth_status(store: AuthStore, config: AuthConfig, request: Request) -> dict[str, Any]:
    if not config.enabled:
        return {"authRequired": False, "authenticated": True, "user": None}

    user = current_user(store, request)
    return {
        "authRequired": True,
        "authenticated": user is not None,
        "user": user_to_dict(user) if user else None,
    }


def build_google_login(config: AuthConfig) -> tuple[str, str]:
    ensure_auth_config(config)
    state = secrets.token_urlsafe(32)
    query = urllib.parse.urlencode(
        {
            "client_id": config.client_id,
            "redirect_uri": config.redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "prompt": "select_account",
        }
    )
    return f"{GOOGLE_AUTH_URL}?{query}", make_state_cookie(state, config)


def handle_google_callback(
    store: AuthStore,
    config: AuthConfig,
    *,
    state: str | None,
    code: str | None,
    state_cookie: str | None,
) -> tuple[str, AuthUserRecord]:
    ensure_auth_config(config)
    if not code:
        raise HTTPException(status_code=400, detail="Google OAuth callback is missing code")
    if not verify_state_cookie(state_cookie, state, config):
        raise HTTPException(status_code=400, detail="Google OAuth state is invalid or expired")

    token_response = exchange_code_for_token(config, code)
    raw_id_token = token_response.get("id_token")
    if not isinstance(raw_id_token, str) or not raw_id_token:
        raise HTTPException(status_code=400, detail="Google OAuth response did not include id_token")

    claims = verify_id_token(config, raw_id_token)
    email = normalize_email(claims.get("email"))
    if not email:
        raise HTTPException(status_code=403, detail="Google account email is missing")
    if claims.get("email_verified") is not True:
        raise HTTPException(status_code=403, detail="Google account email is not verified")
    if not email_allowed(config, email):
        raise HTTPException(status_code=403, detail="Google account is not allowed")

    google_sub = str(claims.get("sub") or "").strip()
    if not google_sub:
        raise HTTPException(status_code=403, detail="Google account subject is missing")

    user = store.upsert_auth_user(
        google_sub=google_sub,
        email=email,
        name=optional_claim(claims.get("name")),
        picture_url=optional_claim(claims.get("picture")),
    )
    session_token = secrets.token_urlsafe(48)
    expires_at = datetime.now(timezone.utc) + timedelta(days=SESSION_TTL_DAYS)
    store.create_auth_session(
        user_id=user.id,
        session_hash=hash_session_token(session_token),
        expires_at=format_utc_datetime(expires_at),
    )
    return session_token, user


def current_user(store: AuthStore, request: Request) -> AuthUserRecord | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    return store.get_auth_user_by_session(
        session_hash=hash_session_token(token),
        now=now_utc_text(),
    )


def delete_current_session(store: AuthStore, request: Request) -> None:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        store.delete_auth_session(hash_session_token(token))


def session_cookie_kwargs(config: AuthConfig) -> dict[str, Any]:
    return {
        "httponly": True,
        "secure": config.secure_cookie,
        "samesite": "lax",
        "path": "/",
    }


def user_to_dict(user: AuthUserRecord) -> dict[str, Any]:
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "pictureUrl": user.picture_url,
        "lastLoginAt": user.last_login_at,
    }


def ensure_auth_config(config: AuthConfig) -> None:
    if not config.enabled:
        raise HTTPException(status_code=500, detail="Google OAuth is not configured")


def exchange_code_for_token(config: AuthConfig, code: str) -> dict[str, Any]:
    payload = urllib.parse.urlencode(
        {
            "code": code,
            "client_id": config.client_id,
            "client_secret": config.client_secret,
            "redirect_uri": config.redirect_uri,
            "grant_type": "authorization_code",
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        GOOGLE_TOKEN_URL,
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise HTTPException(status_code=502, detail=f"Google token exchange failed: {body}") from exc
    except OSError as exc:
        raise HTTPException(status_code=502, detail="Google token exchange failed") from exc


def verify_id_token(config: AuthConfig, raw_id_token: str) -> dict[str, Any]:
    try:
        return google_id_token.verify_oauth2_token(
            raw_id_token,
            google_requests.Request(),
            config.client_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Google ID token is invalid") from exc


def make_state_cookie(state: str, config: AuthConfig) -> str:
    issued_at = int(time.time())
    body = base64.urlsafe_b64encode(f"{state}.{issued_at}".encode("utf-8")).decode("ascii")
    signature = sign_value(body, config)
    return f"{body}.{signature}"


def verify_state_cookie(cookie_value: str | None, expected_state: str | None, config: AuthConfig) -> bool:
    if not cookie_value or not expected_state:
        return False
    body, separator, signature = cookie_value.partition(".")
    if not separator:
        return False
    if not hmac.compare_digest(signature, sign_value(body, config)):
        return False
    try:
        decoded = base64.urlsafe_b64decode(body.encode("ascii")).decode("utf-8")
        state, issued_at_text = decoded.rsplit(".", 1)
        issued_at = int(issued_at_text)
    except (ValueError, UnicodeDecodeError):
        return False
    if not hmac.compare_digest(state, expected_state):
        return False
    return time.time() - issued_at <= STATE_TTL_SECONDS


def sign_value(value: str, config: AuthConfig) -> str:
    assert config.session_secret is not None
    digest = hmac.new(
        config.session_secret.encode("utf-8"),
        value.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def email_allowed(config: AuthConfig, email: str) -> bool:
    if config.allowed_emails:
        return email in config.allowed_emails
    if config.allowed_domain:
        return email.endswith(f"@{config.allowed_domain}")
    return True


def should_require_auth(path: str, method: str, config: AuthConfig) -> bool:
    """Auth 开启时拦截业务 API；放行 health / OAuth / studio 静态与 OpenAPI。"""
    if method == "OPTIONS" or not config.enabled:
        return False
    if path in {"/health", "/docs", "/openapi.json", "/redoc"}:
        return False
    if path.startswith("/api/auth/"):
        return False
    if path == "/studio" or path.startswith("/studio/"):
        return False
    # 其它路径（/jobs /tasks /pipelines /preview ...）均需登录
    return True


def parse_csv_env(name: str) -> tuple[str, ...]:
    value = optional_env(name)
    if not value:
        return ()
    return tuple(
        item
        for item in (normalize_email(part) for part in value.split(","))
        if item
    )


def normalize_email(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    return normalized or None


def normalize_domain(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().strip(".").lower()
    return normalized or None


def optional_claim(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def optional_env(name: str) -> str | None:
    value = os.environ.get(name)
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def format_utc_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
