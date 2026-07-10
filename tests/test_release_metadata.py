import re
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_uv_lock_uses_only_public_pypi_registry():
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    registries = {
        package["source"]["registry"]
        for package in lock["package"]
        if "registry" in package.get("source", {})
    }

    assert registries == {"https://pypi.org/simple"}


def test_github_actions_are_pinned_to_commit_shas():
    workflow_dir = ROOT / ".github/workflows"
    uses = []
    for path in workflow_dir.glob("*.yml"):
        uses.extend(
            line.strip().removeprefix("- uses: ").split(" #", 1)[0]
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip().startswith("- uses:")
        )

    assert uses
    assert all(re.search(r"@[0-9a-f]{40}$", item) for item in uses)
