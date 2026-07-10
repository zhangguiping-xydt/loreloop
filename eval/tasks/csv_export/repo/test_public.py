import unittest

from customer_export import export_customers


class CustomerExportTests(unittest.TestCase):
    def test_returns_text(self):
        self.assertIsInstance(export_customers([]), str)


if __name__ == "__main__":
    unittest.main()
