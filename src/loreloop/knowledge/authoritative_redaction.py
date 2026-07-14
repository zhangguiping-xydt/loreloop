"""Deterministic secret redaction for source-derived configuration values."""

from __future__ import annotations

import re
from typing import Final

_SECRET_NAME: Final = re.compile(
    r"(?:^|[_-])(password|passwd|secret|token|api[_-]?key|private[_-]?key|credential)(?:$|[_-])",
    re.IGNORECASE,
)
_SECRET_VALUE: Final = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|\b(?:sk|ghp|github_pat)_[A-Za-z0-9_-]{12,}",
)


def redact_default(key: str, value: str | None) -> tuple[str | None, bool]:
    """Return a portable default without exposing a likely credential."""
    if value is None:
        return None, False
    if _SECRET_NAME.search(key) is not None or _SECRET_VALUE.search(value) is not None:
        return None, True
    return value, False
