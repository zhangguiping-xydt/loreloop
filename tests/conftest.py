import pytest


@pytest.fixture(autouse=True)
def isolated_key_dir(tmp_path_factory, monkeypatch):
    """Evidence keys live outside the project tree (~/.knowhelm/keys); tests
    must never touch the real home directory."""
    monkeypatch.setenv(
        "KNOWHELM_KEY_DIR", str(tmp_path_factory.mktemp("knowhelm-keys"))
    )
