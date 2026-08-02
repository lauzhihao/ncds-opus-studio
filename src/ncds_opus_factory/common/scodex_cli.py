"""Shared SCodex CLI helper.

SCodex is the account-aware launcher used for Codex jobs.  Keeping its
launching and NDJSON parsing here lets commands depend only on ``common`` and
prevents each caller from carrying a subtly different CLI contract.
"""

from __future__ import annotations

import json
import shutil
import subprocess

DEFAULT_CODEX_MODEL = "gpt-5.6-terra"


def _resolve_scodex() -> str:
    """Resolve the SCodex launcher or raise a readable runtime error."""
    found = shutil.which("scodex")
    if found:
        return found
    raise RuntimeError("scodex launcher not found on PATH")


def is_scodex_available() -> bool:
    """Return whether a SCodex launcher is available on PATH."""
    try:
        _resolve_scodex()
    except RuntimeError:
        return False
    return True


def call_scodex(
    prompt: str,
    *,
    model: str = DEFAULT_CODEX_MODEL,
    timeout_seconds: int = 900,
) -> str:
    """Run ``scodex launch ... exec`` and return its final text item.

    The launcher emits JSON lines.  The Codex response can be represented by
    either ``item.text`` or a list of ``item.content`` text blocks, so both
    forms are normalized here for every caller.
    """
    args = [
        _resolve_scodex(),
        "launch",
        "--no-resume",
        "--",
        "exec",
        "--skip-git-repo-check",
        "--ephemeral",
        "-s",
        "read-only",
        "-m",
        model,
        "--json",
        prompt,
    ]
    proc = subprocess.run(  # noqa: S603 - executable resolved by _resolve_scodex.
        args,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        stdin=subprocess.DEVNULL,
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()[-500:]
        raise RuntimeError(f"scodex launcher exited {proc.returncode}: {tail}")

    final = ""
    for raw_line in proc.stdout.splitlines():
        line = raw_line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if payload.get("type") != "item.completed":
            continue
        item = payload.get("item") or {}
        text = item.get("text")
        if not isinstance(text, str) and isinstance(item.get("content"), list):
            text = "".join(
                part.get("text", "")
                for part in item["content"]
                if isinstance(part, dict) and isinstance(part.get("text"), str)
            )
        if isinstance(text, str) and text.strip():
            final = text.strip()
    if not final:
        raise RuntimeError(f"scodex empty result; stdout tail={proc.stdout[-300:]}")
    return final


__all__ = [
    "DEFAULT_CODEX_MODEL",
    "call_scodex",
    "is_scodex_available",
]
