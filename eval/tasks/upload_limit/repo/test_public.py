import unittest

from upload_policy import accepts_upload


class UploadPolicyTests(unittest.TestCase):
    def test_small_upload_is_accepted(self):
        self.assertTrue(accepts_upload(1024))

    def test_negative_size_is_rejected(self):
        with self.assertRaises(ValueError):
            accepts_upload(-1)


if __name__ == "__main__":
    unittest.main()
