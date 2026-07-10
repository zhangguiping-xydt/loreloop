import unittest

from limiter import UploadLimiter


class UploadLimiterTests(unittest.TestCase):
    def test_first_request_is_allowed(self):
        self.assertTrue(UploadLimiter().allow("u1", 0))

    def test_users_have_independent_buckets(self):
        limiter = UploadLimiter()
        self.assertTrue(limiter.allow("u1", 0))
        self.assertTrue(limiter.allow("u2", 0))


if __name__ == "__main__":
    unittest.main()
