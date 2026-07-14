from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts import verify_authoritative_export_scope as scope


ROOT = Path(__file__).resolve().parents[1]


def _init_repository(root: Path) -> None:
    _ = subprocess.run(["git", "init", "-q", str(root)], check=True)
    _ = subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "test@example.com"], check=True
    )
    _ = subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)


def test_scope_verifier_accepts_only_owned_todo_paths(tmp_path: Path) -> None:
    # Given: a repository whose only new file is owned by Todo 2.
    root = tmp_path / "repo"
    _ = root.mkdir()
    _init_repository(root)
    owned = root / "src/loreloop/knowledge/authoritative_ids.py"
    owned.parent.mkdir(parents=True)
    _ = owned.write_text("# owned\n", encoding="utf-8")
    ownership = tmp_path / "ownership.json"
    _ = ownership.write_text(
        json.dumps(
            {
                "schema_version": 4,
                "planning_read_only": [".omo/**"],
                "exact_preserve": [],
                "whole_file_hash_pinned_replacements": [],
                "existing_partial_regions": [],
                "new_product_files_by_todo": {"2": [str(owned.relative_to(root))]},
                "new_test_files_by_todo": {"2": []},
                "new_script_files_by_todo": {"2": []},
            }
        ),
        encoding="utf-8",
    )

    # When: Todo 2 scope is verified.
    report = scope.verify_scope(root, ownership, todo=2, require_complete=True)

    # Then: the owned path is reported with no scope violation.
    assert report.owned_paths == ("src/loreloop/knowledge/authoritative_ids.py",)


def test_scope_verifier_rejects_an_unowned_dirty_path(tmp_path: Path) -> None:
    # Given: an otherwise valid repository with an unowned source file.
    root = tmp_path / "repo"
    _ = root.mkdir()
    _init_repository(root)
    _ = (root / "surprise.py").write_text("print('unexpected')\n", encoding="utf-8")
    ownership = tmp_path / "ownership.json"
    _ = ownership.write_text(
        json.dumps(
            {
                "schema_version": 4,
                "planning_read_only": [],
                "exact_preserve": [],
                "whole_file_hash_pinned_replacements": [],
                "existing_partial_regions": [],
                "new_product_files_by_todo": {"2": []},
                "new_test_files_by_todo": {"2": []},
                "new_script_files_by_todo": {"2": []},
            }
        ),
        encoding="utf-8",
    )

    # When / Then: the binary scope is rejected rather than hidden by a success string.
    with pytest.raises(scope.ScopeViolation, match="surprise.py"):
        _ = scope.verify_scope(root, ownership, todo=2, require_complete=False)


def test_todo2_ownership_lists_the_split_binding_and_ast_modules() -> None:
    # Given: the current implementation-read-only ownership registry.
    ownership = scope.load_ownership_contract(
        ROOT / ".omo/evidence/authoritative-export-spec-v4/ownership-v4.json"
    )

    # When: Todo 2 product ownership is read directly from the current bundle.
    product_paths = set(ownership.by_todo[2])

    # Then: the clean responsibility split is explicitly authorized.
    assert {
        "src/loreloop/knowledge/authoritative_bindings.py",
        "src/loreloop/knowledge/authoritative_ast.py",
    }.issubset(product_paths)
