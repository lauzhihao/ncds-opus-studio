"""Shared paths for the gpt-image gateway."""

from __future__ import annotations

import os
from pathlib import Path


GPT_IMAGE_OUTPUT_ROOT = Path(
    os.environ.get("NOF_GPT_IMAGE_OUTPUT_DIR", "/tmp/gpt-image")
).expanduser().resolve()


__all__ = ["GPT_IMAGE_OUTPUT_ROOT"]
