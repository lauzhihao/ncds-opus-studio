"""OAuth 路由：Google / Apple Web + App mobile token。"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from ncds_opus_factory.server.auth import (
    SESSION_COOKIE,
    SESSION_TTL_DAYS,
    STATE_COOKIE,
    STUDIO_HOME,
    auth_status,
    build_apple_login,
    build_google_login,
    delete_current_session,
    handle_apple_callback,
    handle_google_callback,
    handle_mobile_id_token,
    session_cookie_kwargs,
    user_to_dict,
)
from ncds_opus_factory.server.state import AUTH_CONFIG, AUTH_STORE

router = APIRouter(prefix="/api/auth", tags=["auth"])


class MobileLoginBody(BaseModel):
    provider: Literal["google", "apple"]
    id_token: str = Field(min_length=20)


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
    return _session_redirect(session_token)


@router.get("/apple/login", include_in_schema=False)
def apple_login() -> RedirectResponse:
    auth_url, state_cookie = build_apple_login(AUTH_CONFIG)
    response = RedirectResponse(auth_url)
    response.set_cookie(
        STATE_COOKIE,
        state_cookie,
        max_age=600,
        **session_cookie_kwargs(AUTH_CONFIG),
    )
    return response


@router.post("/apple/callback", include_in_schema=False)
async def apple_callback(
    request: Request,
    code: str | None = Form(default=None),
    state: str | None = Form(default=None),
    error: str | None = Form(default=None),
    user: str | None = Form(default=None),
) -> RedirectResponse:
    """Apple 默认 response_mode=form_post，走 POST form。"""
    if error:
        raise HTTPException(status_code=400, detail=f"Apple Sign In failed: {error}")
    session_token, _user = handle_apple_callback(
        AUTH_STORE,
        AUTH_CONFIG,
        state=state,
        code=code,
        state_cookie=request.cookies.get(STATE_COOKIE),
        user_json=user,
    )
    return _session_redirect(session_token)


@router.post("/mobile")
def mobile_login(body: MobileLoginBody) -> dict[str, Any]:
    """Flutter / 原生：提交 Google 或 Apple 的 id_token，返回 session token + user。"""
    session_token, user = handle_mobile_id_token(
        AUTH_STORE,
        AUTH_CONFIG,
        provider=body.provider,
        id_token=body.id_token.strip(),
    )
    return {
        "ok": True,
        "sessionToken": session_token,
        "expiresInDays": SESSION_TTL_DAYS,
        "user": user_to_dict(user),
    }


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


def _session_redirect(session_token: str) -> RedirectResponse:
    response = RedirectResponse(STUDIO_HOME, status_code=303)
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
