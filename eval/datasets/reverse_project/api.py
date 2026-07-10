from pathlib import Path

from auth import require_uploader
from config import (
    ALLOWED_EXTENSIONS,
    MAX_UPLOAD_BYTES,
    RATE_LIMIT_REQUESTS,
    RATE_LIMIT_WINDOW_SECONDS,
)


def upload(file, user, limiter, store):
    require_uploader(user)
    if not limiter.allow(
        user.id,
        requests=RATE_LIMIT_REQUESTS,
        window_seconds=RATE_LIMIT_WINDOW_SECONDS,
    ):
        return 429, {"error": "rate_limited", "retry_after_seconds": 60}
    if Path(file.name).suffix.lower() not in ALLOWED_EXTENSIONS:
        return 415, {"error": "unsupported_file_type"}
    if file.size > MAX_UPLOAD_BYTES:
        return 413, {"error": "file_too_large", "max_bytes": MAX_UPLOAD_BYTES}
    file_id = store.save(file, owner_id=user.id)
    return 201, {"id": file_id}
