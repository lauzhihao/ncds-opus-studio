"""Google OAuth 路由：/api/auth/me、/api/auth/google/login|callback、/api/auth/logout。

路径与 vps-insight 一致，方便共用 Google OAuth Client 的 Authorized redirect URI。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from ncds_opus_factory.server.auth import (
    SESSION_COOKIE,
    SESSION_TTL_DAYS,
    STATE_COOKIE,
    STUDIO_HOME,
    auth_status,
    build_google_login,
    delete_current_session,
    handle_google_callback,
    session_cookie_kwargs,
)
from ncds_opus_factory.server.state import AUTH_CONFIG, AUTH_STORE

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/me")
def auth_me(request: Request) -> dict[str, Any]:
    return auth_status(AUTH_STORE, AUTH_CONFIG, request)


@router.get("/google/login", include_in_schema=False)
def google_login() -> RedirectResponse:
    auth_url, state_cookie = build_google_login(AUTH_CONFIG)
    response = RedirectResponse(auth_url)
    response.set_cookie(
        STATE_COOKIE,
        state_cookie,
        max_age=600,
        **session_cookie_kwargs(AUTH_CONFIG),
    )
    return response


@router.get("/google/callback", include_in_schema=False)
def google_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    if error:
        raise HTTPException(status_code=400, detail=f"Google OAuth failed: {error}")
    session_token, _user = handle_google_callback(
        AUTH_STORE,
        AUTH_CONFIG,
        state=state,
        code=code,
        state_cookie=request.cookies.get(STATE_COOKIE),
    )
    response = RedirectResponse(STUDIO_HOME)
    response.set_cookie(
        SESSION_COOKIE,
        session_token,
        max_age=SESSION_TTL_DAYS * 24 * 60 * 60,
        **session_cookie_kwargs(AUTH_CONFIG),
    )
    response.delete_cookie(
        STATE_COOKIE,
        path="/",
        secure=AUTH_CONFIG.secure_cookie,
        httponly=True,
        samesite="lax",
    )
    return response


@router.post("/logout")
def logout(request: Request) -> JSONResponse:
    delete_current_session(AUTH_STORE, request)
    response = JSONResponse({"ok": True})
    response.delete_cookie(
        SESSION_COOKIE,
        path="/",
        secure=AUTH_CONFIG.secure_cookie,
        httponly=True,
        samesite="lax",
    )
    return response
