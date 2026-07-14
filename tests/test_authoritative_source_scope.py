from __future__ import annotations

import pytest

from loreloop.knowledge.authoritative_source import excluded_semantic_source


@pytest.mark.parametrize(
    "path",
    (
        "tests/test_api.py",
        "src/api.test.ts",
        "src/api.spec.tsx",
        "src/__tests__/api.ts",
        "fixtures/schema.sql",
        "src/schema.generated.ts",
        "pkg/client_test.go",
        "conftest.py",
    ),
)
def test_test_fixture_and_generated_paths_are_not_product_semantics(path: str) -> None:
    assert excluded_semantic_source(path)


@pytest.mark.parametrize(
    "path",
    ("src/api.py", "src/api.ts", "schema.sql", "src/contracts/api.d.ts"),
)
def test_production_and_declaration_paths_remain_semantic_sources(path: str) -> None:
    assert not excluded_semantic_source(path)
