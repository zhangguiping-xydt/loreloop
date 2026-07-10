"""Small security primitives shared by runtime and evaluation paths."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping

_SECRET_ASSIGNMENT = re.compile(
    r"(?im)^([A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)[A-Z0-9_]*)=([^\r\n]*)"
)
_SECRET_LABEL = re.compile(r"(?im)\b(password|token|secret|api[_ -]?key)\s*[:=]\s*([^\s,;]+)")


def redact_sensitive(text: str, environ: Mapping[str, str] | None = None) -> str:
    redacted = _SECRET_ASSIGNMENT.sub(r"\1=<redacted>", text)
    redacted = _SECRET_LABEL.sub(r"\1: <redacted>", redacted)
    for name, secret in (environ or os.environ).items():
        upper = name.upper()
        if (
            secret
            and len(secret) >= 6
            and any(
                marker in upper for marker in ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")
            )
        ):
            redacted = redacted.replace(secret, "<redacted>")
    return redacted
