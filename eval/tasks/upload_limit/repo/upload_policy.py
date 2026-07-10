MAX_UPLOAD_BYTES = 10 * 1024 * 1024


def accepts_upload(size_bytes: int) -> bool:
    """Return whether a payload fits the configured upload policy."""
    if size_bytes < 0:
        raise ValueError("size_bytes must be non-negative")
    return size_bytes <= MAX_UPLOAD_BYTES
