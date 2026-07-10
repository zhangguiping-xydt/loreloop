from pathlib import Path

from loreloop.paths import key_directory, registry_file, state_root


def test_new_projects_use_loreloop_state(tmp_path):
    assert state_root(tmp_path) == tmp_path / ".loreloop"


def test_operator_paths_use_loreloop_home_by_default(monkeypatch, tmp_path):
    monkeypatch.delenv("LORELOOP_KEY_DIR", raising=False)
    monkeypatch.delenv("LORELOOP_REGISTRY", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    assert key_directory() == tmp_path / ".loreloop/keys"
    assert registry_file() == tmp_path / ".loreloop/projects.json"


def test_operator_paths_respect_loreloop_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("LORELOOP_KEY_DIR", str(tmp_path / "keys"))
    monkeypatch.setenv("LORELOOP_REGISTRY", str(tmp_path / "registry.json"))

    assert key_directory() == tmp_path / "keys"
    assert registry_file() == tmp_path / "registry.json"
