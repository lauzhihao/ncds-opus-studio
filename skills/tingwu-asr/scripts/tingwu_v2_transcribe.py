#!/usr/bin/env python3
"""Thin CLI wrapper around the project TingWu adapter."""
from __future__ import annotations

import json
import logging
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ncds_opus_factory.common.capabilities.tingwu import (  # noqa: E402
    build_create_offline_task,
    build_get_task,
    collect_text,
    create_task,
    extract_text_from_task,
    poll_task,
    resolve_file_url,
    transcribe_file,
    upload_local_file,
)


LOGGER = logging.getLogger("tingwu_v2")
if not LOGGER.handlers:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    LOGGER.addHandler(handler)
LOGGER.setLevel(logging.INFO)
LOGGER.propagate = False


def main() -> int:
    if len(sys.argv) < 2:
        print(json.dumps({"status": "error", "error": "usage: tingwu_v2_transcribe.py <file_url_or_path>"}))
        return 1

    try:
        LOGGER.info("running tingwu transcription")
        result = transcribe_file(sys.argv[1])
        print(
            json.dumps(
                {
                    "status": "success",
                    "backend": result.backend,
                    "dataId": result.data_id,
                    "model": result.model,
                    "text": result.text,
                    "rawResponse": result.raw_response,
                },
                ensure_ascii=False,
            )
        )
        return 0
    except Exception as exc:
        LOGGER.error("tingwu transcription failed: %s", exc)
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
