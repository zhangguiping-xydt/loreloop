import pytest


@pytest.fixture(autouse=True)
def isolated_key_dir(tmp_path_factory, monkeypatch):
    """Evidence keys live outside the project tree (~/.loreloop/keys); tests
    must never touch the real home directory."""
    monkeypatch.setenv("LORELOOP_KEY_DIR", str(tmp_path_factory.mktemp("loreloop-keys")))
    monkeypatch.setenv(
        "LORELOOP_REGISTRY",
        str(tmp_path_factory.mktemp("loreloop-registry") / "projects.json"),
    )
    monkeypatch.setenv(
        "LORELOOP_TRUST_REGISTRY",
        str(tmp_path_factory.mktemp("loreloop-trust-registry") / "trust-locations.json"),
    )
