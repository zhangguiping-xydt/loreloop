MAX_UPLOAD_MIB = 32
PREMIUM_REQUESTS_PER_MINUTE = 12
AUDIT_RETENTION_DAYS = 90


def upload_allowed(size_mib: int) -> bool:
    return 0 <= size_mib <= MAX_UPLOAD_MIB


def premium_request_allowed(requests_this_minute: int) -> bool:
    return requests_this_minute < PREMIUM_REQUESTS_PER_MINUTE


def audit_expired(age_days: int) -> bool:
    return age_days > AUDIT_RETENTION_DAYS
