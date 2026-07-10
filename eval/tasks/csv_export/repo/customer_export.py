import json


def export_customers(customers: list[dict[str, str]]) -> str:
    """Serialize customer records for an operator download."""
    return json.dumps(customers)
