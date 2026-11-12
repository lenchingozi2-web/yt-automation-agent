"""Bounded retries for external render services."""
from __future__ import annotations

import subprocess
import time

import httpx

REVISION_STALE_SECONDS = 20 * 60
RENDER_STALE_SECONDS = 95 * 60


class TemporaryRenderError(RuntimeError):
    """An external render dependency is temporarily unavailable."""


def transient(error: BaseException) -> bool:
    current = error
    while current is not None:
        if isinstance(current, (TemporaryRenderError, httpx.TransportError, TimeoutError, subprocess.TimeoutExpired)):
            return True
        status = getattr(getattr(current, "response", None), "status_code", None) or getattr(current, "status", None)
        if str(status).isdigit() and (int(status) in (408, 425, 429) or 500 <= int(status) < 600):
            return True
        current = current.__cause__
    return False


def retry_io(operation, description: str):
    for attempt in range(3):
        try:
            return operation()
        except Exception as error:
            if attempt == 2 or not transient(error):
                raise
            print(f"[retry] {description}; attempt {attempt + 2}", flush=True)
            time.sleep(2 ** attempt)
