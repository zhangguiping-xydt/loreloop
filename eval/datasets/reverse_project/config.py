MAX_UPLOAD_BYTES = 50 * 1024 * 1024
RATE_LIMIT_REQUESTS = 10
RATE_LIMIT_WINDOW_SECONDS = 60
ALLOWED_EXTENSIONS = frozenset({".pdf", ".png"})

# Disabled legacy draft: MAX_UPLOAD_BYTES = 10 * 1024 * 1024
# Possible future idea: allow .zip uploads after malware scanning exists.
