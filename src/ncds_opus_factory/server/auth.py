"""Google + Apple OAuth + cookie session。

未配置任何 provider 且无 AUTH_SESSION_SECRET 时 auth 关闭。
路径：/api/auth/*（对齐 vps-insight）。
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
from pathlib import Path
from typing import Any

import jwt
from fastapi import HTTPException, Request
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from jwt import PyJWKClient

from ncds_opus_factory.server.auth_store import AuthStore, AuthUserRecord, now_utc_text

SESSION_COOKIE = "nof_session"
STATE_COOKIE = "nof_oauth_state"
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
APPLE_AUTH_URL = "https://appleid.apple.com/auth/authorize"
APPLE_TOKEN_URL = "https://appleid.apple.com/auth/token"
APPLE_KEYS_URL = "https://appleid.apple.com/auth/keys"
APPLE_ISSUER = "https://appleid.apple.com"
SESSION_TTL_DAYS = 30
STATE_TTL_SECONDS = 600
STUDIO_HOME = "/studio/"
_REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class AuthConfig:
    # Google Web
    client_id: str | None
    client_secret: str | None
    # Google native audiences (comma-separated optional extras for mobile id_token)
    google_ios_client_id: str | None
    google_android_client_id: str | None
    session_secret: str | None
    allowed_emails: tuple[str, ...]
    allowed_domain: str | None
    public_base_url: str
    # Apple
    apple_team_id: str | None
    apple_key_id: str | None
    apple_services_id: str | None
    apple_bundle_id: str | None
    apple_private_key: str | None

    @property
    def google_enabled(self) -> bool:
        return bool(self.client_id and self.client_secret and self.session_secret)

    @property
    def apple_enabled(self) -> bool:
        return bool(
            self.session_secret
            and self.apple_team_id
            and self.apple_key_id
            and self.apple_private_key
            and (self.apple_services_id or self.apple_bundle_id)
        )

    @property
    def enabled(self) -> bool:
        return bool(self.session_secret) and (self.google_enabled or self.apple_enabled)

    @property
    def secure_cookie(self) -> bool:
        return self.public_base_url.startswith("https://")

    @property
    def google_redirect_uri(self) -> str:
        return f"{self.public_base_url}/api/auth/google/callback"

    @property
    def apple_redirect_uri(self) -> str:
        return f"{self.public_base_url}/api/auth/apple/callback"

    # 兼容旧字段名
    @property
    def redirect_uri(self) -> str:
        return self.google_redirect_uri


def load_auth_config() -> AuthConfig:
    public_base_url = (
        optional_env("PUBLIC_BASE_URL")
        or optional_env("NOF_PUBLIC_BASE_URL")
        or "http://127.0.0.1:8810"
    ).rstrip("/")
    return AuthConfig(
        client_id=optional_env("GOOGLE_CLIENT_ID"),
        client_secret=optional_env("GOOGLE_CLIENT_SECRET"),
        google_ios_client_id=optional_env("GOOGLE_IOS_CLIENT_ID"),
        google_android_client_id=optional_env("GOOGLE_ANDROID_CLIENT_ID"),
        session_secret=optional_env("AUTH_SESSION_SECRET"),
        allowed_emails=parse_csv_env("AUTH_ALLOWED_EMAILS"),
        allowed_domain=normalize_domain(optional_env("AUTH_ALLOWED_DOMAIN")),
        public_base_url=public_base_url,
        apple_team_id=optional_env("APPLE_TEAM_ID"),
        apple_key_id=optional_env("APPLE_KEY_ID"),
        apple_services_id=optional_env("APPLE_SERVICES_ID")
        or "com.claudelight.claudeTrafficLight.web",
        apple_bundle_id=optional_env("APPLE_BUNDLE_ID")
        or "com.claudelight.claudeTrafficLight",
        apple_private_key=_load_apple_private_key(),
    )


def _load_apple_private_key() -> str | None:
    raw = optional_env("APPLE_PRIVATE_KEY")
    if raw:
        return raw.replace("\\n", "\n")
    path = optional_env("APPLE_PRIVATE_KEY_PATH")
    if not path:
        # 默认仓库 secrets/
        default = _REPO_ROOT / "secrets" / "AuthKey_5HN57B866Z.p8"
        if default.is_file():
            path = str(default)
        else:
            return None
    p = Path(path).expanduser()
    if not p.is_file():
        return None
    text = p.read_text(encoding="utf-8").strip()
    return text or None


def auth_status(store: AuthStore, config: AuthConfig, request: Request) -> dict[str, Any]:
    if not config.enabled:
        return {
            "authRequired": False,
            "authenticated": True,
            "user": None,
            "providers": {"google": False, "apple": False},
        }

    user = current_user(store, request)
    return {
        "authRequired": True,
        "authenticated": user is not None,
        "user": user_to_dict(user) if user else None,
        "providers": {
            "google": config.google_enabled,
            "apple": config.apple_enabled,
        },
    }


def build_google_login(store: AuthStore, config: AuthConfig) -> tuple[str, str]:
    if not config.google_enabled:
        raise HTTPException(status_code=500, detail="Google OAuth is not configured")
    state = issue_oauth_state(store, config)
    query = urllib.parse.urlencode(
        {
            "client_id": config.client_id,
            "redirect_uri": config.google_redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "prompt": "select_account",
        }
    )
    return f"{GOOGLE_AUTH_URL}?{query}", make_state_cookie(state, config)


def build_apple_login(store: AuthStore, config: AuthConfig) -> tuple[str, str]:
    if not config.apple_enabled or not config.apple_services_id:
        raise HTTPException(status_code=500, detail="Apple Sign In is not configured")
    state = issue_oauth_state(store, config)
    query = urllib.parse.urlencode(
        {
            "client_id": config.apple_services_id,
            "redirect_uri": config.apple_redirect_uri,
            "response_type": "code",
            "response_mode": "form_post",
            "scope": "name email",
            "state": state,
        }
    )
    return f"{APPLE_AUTH_URL}?{query}", make_state_cookie(state, config)


def issue_oauth_state(store: AuthStore, config: AuthConfig) -> str:
    """签发一次性 CSRF state（落库 + cookie 双轨；Apple form_post 可能不带 cookie）。"""
    state = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=STATE_TTL_SECONDS)
    store.save_oauth_state(state, expires_at=format_utc_datetime(expires_at))
    return state


def verify_oauth_state(
    store: AuthStore,
    config: AuthConfig,
    *,
    state: str | None,
    state_cookie: str | None,
) -> bool:
    """优先消费服务端 state；cookie HMAC 作同站 GET 回调兜底。"""
    if not state:
        return False
    if store.consume_oauth_state(state):
        return True
    # cookie 兜底（Google 顶层 GET 回跳时常可用）
    return verify_state_cookie(state_cookie, state, config)


def handle_google_callback(
    store: AuthStore,
    config: AuthConfig,
    *,
    state: str | None,
    code: str | None,
    state_cookie: str | None,
) -> tuple[str, AuthUserRecord]:
    if not config.google_enabled:
        raise HTTPException(status_code=500, detail="Google OAuth is not configured")
    if not code:
        raise HTTPException(status_code=400, detail="Google OAuth callback is missing code")
    if not verify_oauth_state(store, config, state=state, state_cookie=state_cookie):
        raise HTTPException(status_code=400, detail="Google OAuth state is invalid or expired")

    token_response = exchange_google_code(config, code)
    raw_id_token = token_response.get("id_token")
    if not isinstance(raw_id_token, str) or not raw_id_token:
        raise HTTPException(status_code=400, detail="Google OAuth response did not include id_token")

    claims = verify_google_id_token(config, raw_id_token, audience=config.client_id)
    return issue_session_from_claims(store, config, provider="google", claims=claims)


def handle_apple_callback(
    store: AuthStore,
    config: AuthConfig,
    *,
    state: str | None,
    code: str | None,
    state_cookie: str | None,
    user_json: str | None = None,
) -> tuple[str, AuthUserRecord]:
    if not config.apple_enabled:
        raise HTTPException(status_code=500, detail="Apple Sign In is not configured")
    if not code:
        raise HTTPException(status_code=400, detail="Apple callback is missing code")
    if not verify_oauth_state(store, config, state=state, state_cookie=state_cookie):
        raise HTTPException(status_code=400, detail="Apple OAuth state is invalid or expired")

    token_response = exchange_apple_code(config, code)
    raw_id_token = token_response.get("id_token")
    if not isinstance(raw_id_token, str) or not raw_id_token:
        raise HTTPException(status_code=400, detail="Apple response did not include id_token")

    claims = verify_apple_id_token(
        config,
        raw_id_token,
        audiences=[a for a in (config.apple_services_id, config.apple_bundle_id) if a],
    )
    # 首次授权 Apple 把 name 放在 form 的 user JSON，不在 id_token 里
    name = None
    if user_json:
        try:
            u = json.loads(user_json)
            name_obj = u.get("name") if isinstance(u, dict) else None
            if isinstance(name_obj, dict):
                parts = [name_obj.get("firstName") or "", name_obj.get("lastName") or ""]
                name = " ".join(p for p in parts if p).strip() or None
        except json.JSONDecodeError:
            name = None
    if name:
        claims = {**claims, "name": name}
    return issue_session_from_claims(store, config, provider="apple", claims=claims)


def handle_mobile_id_token(
    store: AuthStore,
    config: AuthConfig,
    *,
    provider: str,
    id_token: str,
) -> tuple[str, AuthUserRecord]:
    """App 原生登录：客户端拿 id_token 交给服务端验签并发 session。"""
    provider = provider.strip().lower()
    if provider == "google":
        if not config.google_enabled and not config.client_id:
            # 允许仅配 GOOGLE_CLIENT_ID + 移动端 client 验签
            if not (config.client_id or config.google_ios_client_id or config.google_android_client_id):
                raise HTTPException(status_code=500, detail="Google is not configured")
        audiences = [
            a
            for a in (
                config.client_id,
                config.google_ios_client_id,
                config.google_android_client_id,
            )
            if a
        ]
        claims = None
        last_err: Exception | None = None
        for aud in audiences:
            try:
                claims = verify_google_id_token(config, id_token, audience=aud)
                break
            except HTTPException as exc:
                last_err = exc
        if claims is None:
            raise last_err or HTTPException(status_code=403, detail="Google ID token is invalid")
        return issue_session_from_claims(store, config, provider="google", claims=claims)

    if provider == "apple":
        if not config.apple_enabled:
            raise HTTPException(status_code=500, detail="Apple Sign In is not configured")
        audiences = [a for a in (config.apple_bundle_id, config.apple_services_id) if a]
        claims = verify_apple_id_token(config, id_token, audiences=audiences)
        return issue_session_from_claims(store, config, provider="apple", claims=claims)

    raise HTTPException(status_code=400, detail=f"unsupported provider: {provider}")


def issue_session_from_claims(
    store: AuthStore,
    config: AuthConfig,
    *,
    provider: str,
    claims: dict[str, Any],
) -> tuple[str, AuthUserRecord]:
    if not config.session_secret:
        raise HTTPException(status_code=500, detail="AUTH_SESSION_SECRET is not configured")

    sub = str(claims.get("sub") or "").strip()
    if not sub:
        raise HTTPException(status_code=403, detail="identity subject is missing")

    email = normalize_email(claims.get("email"))
    if provider == "google":
        if not email:
            raise HTTPException(status_code=403, detail="Google account email is missing")
        # 某些移动端 token 用字符串 "true"
        if claims.get("email_verified") not in (True, "true"):
            raise HTTPException(status_code=403, detail="Google account email is not verified")
    else:
        # Apple 后续登录可能不带 email
        if not email:
            email = f"apple_{sub[:16].lower()}@privaterelay.appleid.local"

    if not email_allowed(config, email):
        raise HTTPException(status_code=403, detail="account is not allowed")

    user = store.upsert_auth_user(
        provider=provider,
        provider_sub=sub,
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
        # App 可用 Authorization: Bearer <session>
        auth = request.headers.get("authorization") or request.headers.get("Authorization")
        if auth and auth.lower().startswith("bearer "):
            token = auth[7:].strip()
    if not token:
        return None
    return store.get_auth_user_by_session(
        session_hash=hash_session_token(token),
        now=now_utc_text(),
    )


def delete_current_session(store: AuthStore, request: Request) -> None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        auth = request.headers.get("authorization") or request.headers.get("Authorization")
        if auth and auth.lower().startswith("bearer "):
            token = auth[7:].strip()
    if token:
        store.delete_auth_session(hash_session_token(token))


def session_cookie_kwargs(config: AuthConfig) -> dict[str, Any]:
    return {
        "httponly": True,
        "secure": config.secure_cookie,
        "samesite": "lax",
        "path": "/",
    }


def oauth_state_cookie_kwargs(config: AuthConfig) -> dict[str, Any]:
    """OAuth state cookie。

    Apple 用 form_post 从 appleid.apple.com 跨站 POST 回回调，SameSite=Lax 不会带 cookie。
    HTTPS 下用 None+Secure，使跨站 POST 也能带上（与服务端 state 双保险）。
    """
    if config.secure_cookie:
        return {
            "httponly": True,
            "secure": True,
            "samesite": "none",
            "path": "/",
        }
    return session_cookie_kwargs(config)


def user_to_dict(user: AuthUserRecord) -> dict[str, Any]:
    return {
        "id": user.id,
        "provider": user.provider,
        "email": user.email,
        "name": user.name,
        "pictureUrl": user.picture_url,
        "lastLoginAt": user.last_login_at,
    }


def ensure_auth_config(config: AuthConfig) -> None:
    if not config.enabled:
        raise HTTPException(status_code=500, detail="OAuth is not configured")


def exchange_google_code(config: AuthConfig, code: str) -> dict[str, Any]:
    payload = urllib.parse.urlencode(
        {
            "code": code,
            "client_id": config.client_id,
            "client_secret": config.client_secret,
            "redirect_uri": config.google_redirect_uri,
            "grant_type": "authorization_code",
        }
    ).encode("utf-8")
    return _post_form(GOOGLE_TOKEN_URL, payload, err_label="Google token exchange")


def exchange_apple_code(config: AuthConfig, code: str) -> dict[str, Any]:
    client_secret = make_apple_client_secret(config)
    payload = urllib.parse.urlencode(
        {
            "code": code,
            "client_id": config.apple_services_id,
            "client_secret": client_secret,
            "redirect_uri": config.apple_redirect_uri,
            "grant_type": "authorization_code",
        }
    ).encode("utf-8")
    return _post_form(APPLE_TOKEN_URL, payload, err_label="Apple token exchange")


def make_apple_client_secret(config: AuthConfig) -> str:
    """ES256 JWT，有效期最多 6 个月；这里签 1 小时足够换 token。"""
    assert config.apple_team_id and config.apple_key_id and config.apple_private_key
    assert config.apple_services_id
    now = int(time.time())
    headers = {"kid": config.apple_key_id, "alg": "ES256"}
    payload = {
        "iss": config.apple_team_id,
        "iat": now,
        "exp": now + 3600,
        "aud": APPLE_ISSUER,
        "sub": config.apple_services_id,
    }
    return jwt.encode(payload, config.apple_private_key, algorithm="ES256", headers=headers)


def verify_google_id_token(
    config: AuthConfig,
    raw_id_token: str,
    *,
    audience: str | None,
) -> dict[str, Any]:
    if not audience:
        raise HTTPException(status_code=500, detail="Google audience is not configured")
    try:
        return google_id_token.verify_oauth2_token(
            raw_id_token,
            google_requests.Request(),
            audience,
        )
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Google ID token is invalid") from exc


def verify_apple_id_token(
    config: AuthConfig,
    raw_id_token: str,
    *,
    audiences: list[str],
) -> dict[str, Any]:
    if not audiences:
        raise HTTPException(status_code=500, detail="Apple audience is not configured")
    try:
        jwks = PyJWKClient(APPLE_KEYS_URL)
        signing_key = jwks.get_signing_key_from_jwt(raw_id_token)
        last_err: Exception | None = None
        for aud in audiences:
            try:
                return jwt.decode(
                    raw_id_token,
                    signing_key.key,
                    algorithms=["RS256"],
                    audience=aud,
                    issuer=APPLE_ISSUER,
                )
            except jwt.exceptions.InvalidTokenError as exc:
                last_err = exc
        raise HTTPException(status_code=403, detail="Apple ID token is invalid") from last_err
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=403, detail="Apple ID token is invalid") from exc


def _post_form(url: str, payload: bytes, *, err_label: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise HTTPException(status_code=502, detail=f"{err_label} failed: {body}") from exc
    except OSError as exc:
        raise HTTPException(status_code=502, detail=f"{err_label} failed") from exc


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
    if method == "OPTIONS" or not config.enabled:
        return False
    if path in {"/health", "/docs", "/openapi.json", "/redoc"}:
        return False
    if path.startswith("/api/auth/"):
        return False
    if path == "/studio" or path.startswith("/studio/"):
        return False
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
