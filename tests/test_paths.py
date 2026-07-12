import os
from pathlib import Path

import pytest

from loreloop.evidence.artifacts import ArtifactStore
from loreloop.knowledge.store import KnowledgeStore
from loreloop.paths import (
    StatePathError,
    ensure_state_root,
    key_directory,
    load_trust_locations,
    register_key_directory,
    registry_file,
    require_key_directory_outside,
    secure_append_text,
    state_root,
    trust_locations_file,
    unregister_key_directory,
)


def test_new_projects_use_loreloop_state(tmp_path):
    assert state_root(tmp_path) == tmp_path / ".loreloop"


def test_operator_paths_use_loreloop_home_by_default(monkeypatch, tmp_path):
    monkeypatch.delenv("LORELOOP_KEY_DIR", raising=False)
    monkeypatch.delenv("LORELOOP_REGISTRY", raising=False)
    monkeypatch.delenv("LORELOOP_TRUST_REGISTRY", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    assert key_directory() == tmp_path / ".loreloop/keys"
    assert registry_file() == tmp_path / ".loreloop/projects.json"
    assert trust_locations_file() == tmp_path / ".loreloop/trust-locations.json"


def test_operator_paths_respect_loreloop_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("LORELOOP_KEY_DIR", str(tmp_path / "keys"))
    monkeypatch.setenv("LORELOOP_REGISTRY", str(tmp_path / "registry.json"))
    monkeypatch.setenv("LORELOOP_TRUST_REGISTRY", str(tmp_path / "trust.json"))

    assert key_directory() == tmp_path / "keys"
    assert registry_file() == tmp_path / "registry.json"
    assert trust_locations_file() == tmp_path / "trust.json"


def test_project_key_directory_registration_survives_new_sessions(monkeypatch, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    custom = tmp_path / "custom-keys"
    registry = tmp_path / "operator/trust-locations.json"
    monkeypatch.setenv("LORELOOP_TRUST_REGISTRY", str(registry))
    monkeypatch.setenv("LORELOOP_KEY_DIR", str(custom))

    register_key_directory(project, custom)
    monkeypatch.delenv("LORELOOP_KEY_DIR")

    assert key_directory(project) == custom.resolve()
    assert load_trust_locations() == {str(project.resolve()): custom.resolve()}
    unregister_key_directory(project)
    assert key_directory(project) == Path.home() / ".loreloop/keys"


def test_trust_location_registry_is_owner_only(monkeypatch, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    registry = tmp_path / "operator/trust-locations.json"
    monkeypatch.setenv("LORELOOP_TRUST_REGISTRY", str(registry))

    register_key_directory(project, tmp_path / "operator/keys")

    assert registry.is_file()
    if os.name != "nt":
        assert registry.parent.stat().st_mode & 0o777 == 0o700
        assert registry.stat().st_mode & 0o777 == 0o600


def test_malformed_trust_location_registry_is_rejected(monkeypatch, tmp_path):
    registry = tmp_path / "operator/trust-locations.json"
    registry.parent.mkdir()
    registry.write_text('{"version": 1, "projects": []}', encoding="utf-8")
    monkeypatch.setenv("LORELOOP_TRUST_REGISTRY", str(registry))

    with pytest.raises(StatePathError, match="invalid trust-location registry"):
        load_trust_locations()


def test_symlinked_trust_location_registry_is_rejected(monkeypatch, tmp_path):
    target = tmp_path / "actual-registry.json"
    target.write_text('{"version": 1, "projects": {}}', encoding="utf-8")
    registry = tmp_path / "trust-locations.json"
    try:
        registry.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable on this platform")
    monkeypatch.setenv("LORELOOP_TRUST_REGISTRY", str(registry))

    with pytest.raises(StatePathError, match="symlinked trust-location registry"):
        load_trust_locations()


def test_operator_key_directory_cannot_live_inside_project(monkeypatch, tmp_path):
    monkeypatch.setenv("LORELOOP_KEY_DIR", str(tmp_path / ".loreloop/keys"))

    with pytest.raises(StatePathError, match="outside the project tree"):
        require_key_directory_outside(tmp_path)


def test_operator_key_directory_symlink_is_rejected(monkeypatch, tmp_path):
    target = tmp_path.parent / f"{tmp_path.name}-keys-target"
    target.mkdir()
    link = tmp_path.parent / f"{tmp_path.name}-keys-link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable on this platform")
    monkeypatch.setenv("LORELOOP_KEY_DIR", str(link))

    with pytest.raises(StatePathError, match="symlinked key directory"):
        require_key_directory_outside(tmp_path)


def test_state_root_and_files_are_owner_only(tmp_path):
    root = ensure_state_root(tmp_path)
    trace = root / "runs/run-1.jsonl"
    secure_append_text(trace, "{}\n")
    with KnowledgeStore(root / "knowledge.db"):
        pass

    assert root.is_dir()
    assert trace.is_file()
    assert (root / "knowledge.db").is_file()
    if os.name != "nt":
        assert root.stat().st_mode & 0o777 == 0o700
        assert trace.parent.stat().st_mode & 0o777 == 0o700
        assert trace.stat().st_mode & 0o777 == 0o600
        assert (root / "knowledge.db").stat().st_mode & 0o777 == 0o600


def test_state_root_symlink_is_rejected(tmp_path):
    target = tmp_path / "outside-state"
    target.mkdir()
    try:
        (tmp_path / ".loreloop").symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable on this platform")

    with pytest.raises(StatePathError, match="symlinked private directory"):
        ensure_state_root(tmp_path)


def test_nested_evidence_directory_symlink_is_rejected(tmp_path):
    state = ensure_state_root(tmp_path)
    outside = tmp_path / "outside-evidence"
    outside.mkdir()
    try:
        (state / "evidence").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable on this platform")

    with pytest.raises(StatePathError, match="symlinked private directory"):
        ArtifactStore.for_workdir(tmp_path)


def test_database_symlink_is_rejected(tmp_path):
    state = ensure_state_root(tmp_path)
    outside = tmp_path / "outside.db"
    outside.write_bytes(b"")
    try:
        (state / "knowledge.db").symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable on this platform")

    with pytest.raises(StatePathError, match="symlinked knowledge database"):
        KnowledgeStore(state / "knowledge.db")

    assert os.path.getsize(outside) == 0
