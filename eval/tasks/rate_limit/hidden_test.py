from limiter import REQUESTS_PER_WINDOW, WINDOW_SECONDS, UploadLimiter

assert REQUESTS_PER_WINDOW == 7
assert WINDOW_SECONDS == 45
limiter = UploadLimiter()
assert all(limiter.allow("u1", second) for second in range(7))
assert not limiter.allow("u1", 7)
assert limiter.allow("u2", 7)
assert limiter.allow("u1", 45)
print("rate limit contract passed")
