"""Canonical filesystem paths for LoreLoop state and operator-owned data."""

from __future__ import annotations

import os
from pathlib import Path

STATE_DIR_NAME = ".loreloop"


def state_root(workdir: Path) -> Path:
    return workdir / STATE_DIR_NAME


def state_path(workdir: Path, *parts: str) -> Path:
    return state_root(workdir).joinpath(*parts)


def key_directory() -> Path:
    configured = os.environ.get("LORELOOP_KEY_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".loreloop/keys"


def registry_file() -> Path:
    configured = os.environ.get("LORELOOP_REGISTRY")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".loreloop/projects.json"
