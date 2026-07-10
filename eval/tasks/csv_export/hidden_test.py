import csv
import io

from customer_export import export_customers

customers = [
    {"id": "2", "name": "李雷", "email": "li@example.com"},
    {"id": "1", "name": "Ana", "email": "ana@example.com"},
]
text = export_customers(customers)
rows = list(csv.reader(io.StringIO(text)))
assert rows == [
    ["customer_id", "display_name", "primary_email"],
    ["2", "李雷", "li@example.com"],
    ["1", "Ana", "ana@example.com"],
], rows
text.encode("utf-8")
print("customer CSV contract passed")
