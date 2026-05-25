"""Dev-only reverse proxy: 把 /studio/* 反代到 Vite dev server。

仅在 NOF_DEV=1 时由 app.py 挂载。生产模式由 StaticFiles 提供 web/dist。
设计目标：浏览器统一从 :8810 进，HMR WebSocket 也走 :8810 升级，达成单端口开发体验。
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Iterable

import httpx
import websockets
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from starlette.responses import Response

logger = logging.getLogger(__name__)

DEFAULT_VITE_PORT = 5173

# RFC 7230 §6.1 + WebSocket upgrade 相关的逐跳头，反代时不能透传
_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
}


def _filter_headers(items: Iterable[tuple[str, str]]) -> dict[str, str]:
    return {k: v for k, v in items if k.lower() not in _HOP_BY_HOP}


def build_router(vite_base: str | None = None) -> APIRouter:
    if vite_base is None:
        vite_base = os.environ.get("NOF_VITE_BASE")
    if vite_base is None:
        port = int(os.environ.get("NOF_VITE_PORT") or DEFAULT_VITE_PORT)
        vite_base = f"http://127.0.0.1:{port}"
    base = vite_base.rstrip("/")
    ws_base = base.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
    client = httpx.AsyncClient(base_url=base, timeout=None)

    router = APIRouter()

    @router.api_route(
        "/studio",
        methods=["GET", "HEAD", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    )
    @router.api_route(
        "/studio/{path:path}",
        methods=["GET", "HEAD", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    )
    async def http_proxy(request: Request, path: str = "") -> Response:
        target = f"/studio/{path}" if path else "/studio/"
        if request.url.query:
            target = f"{target}?{request.url.query}"
        headers = _filter_headers(request.headers.items())
        body = await request.body()
        try:
            upstream = await client.request(
                request.method, target, headers=headers, content=body
            )
        except httpx.ConnectError as e:
            return Response(
                content=f"[dev-proxy] vite dev server unreachable at {base}: {e}".encode(),
                status_code=502,
                media_type="text/plain; charset=utf-8",
            )
        resp_headers = _filter_headers(upstream.headers.items())
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            headers=resp_headers,
            media_type=upstream.headers.get("content-type"),
        )

    @router.websocket("/studio")
    @router.websocket("/studio/{path:path}")
    async def ws_proxy(ws: WebSocket, path: str = "") -> None:
        # Vite HMR 用 'vite-hmr' subprotocol；要先 accept 同样的协议再连接 upstream
        client_protocols = [
            p.strip()
            for p in ws.headers.get("sec-websocket-protocol", "").split(",")
            if p.strip()
        ]
        chosen = client_protocols[0] if client_protocols else None
        await ws.accept(subprotocol=chosen)

        target_path = f"/studio/{path}" if path else "/studio/"
        if ws.url.query:
            target_path = f"{target_path}?{ws.url.query}"
        target_url = f"{ws_base}{target_path}"

        try:
            upstream = await websockets.connect(
                target_url,
                subprotocols=[chosen] if chosen else None,
                max_size=None,
            )
        except Exception as e:
            logger.warning("[dev-proxy] ws upstream connect failed: %s", e)
            try:
                await ws.close(code=1011)
            except Exception:
                pass
            return

        async def pump_c2s() -> None:
            try:
                while True:
                    msg = await ws.receive()
                    if msg["type"] == "websocket.disconnect":
                        return
                    data = msg.get("text")
                    if data is not None:
                        await upstream.send(data)
                        continue
                    data = msg.get("bytes")
                    if data is not None:
                        await upstream.send(data)
            except WebSocketDisconnect:
                return
            except Exception as e:
                logger.debug("[dev-proxy] c2s pump end: %s", e)

        async def pump_s2c() -> None:
            try:
                async for msg in upstream:
                    if isinstance(msg, (bytes, bytearray)):
                        await ws.send_bytes(bytes(msg))
                    else:
                        await ws.send_text(msg)
            except websockets.ConnectionClosed:
                return
            except Exception as e:
                logger.debug("[dev-proxy] s2c pump end: %s", e)

        try:
            await asyncio.gather(pump_c2s(), pump_s2c())
        finally:
            try:
                await upstream.close()
            except Exception:
                pass
            try:
                await ws.close()
            except Exception:
                pass

    return router
