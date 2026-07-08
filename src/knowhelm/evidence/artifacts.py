"""Content-addressed evidence artifacts.

Stores what the browser actually observed so a report can be independently
re-audited after the live page changes. Files are named by the SHA-256 of
their canonical JSON, and that hash is recorded on the evidence chain, so an
edited artifact no longer matches its chain record.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from ..webexplore.browser import Observation


class ArtifactStore:
    def __init__(self, root: Path) -> None:
        self._root = root
        # Observations may capture post-login page content: owner-only, like
        # the evidence signing key.
        self._root.mkdir(parents=True, exist_ok=True)
        os.chmod(self._root, 0o700)

    @classmethod
    def for_workdir(cls, workdir: Path) -> "ArtifactStore":
        return cls(workdir / ".knowhelm/evidence/artifacts")

    def save_observation(self, obs: Observation) -> tuple[str, Path]:
        payload = {
            "type": "page_observation",
            "url": obs.url,
            "title": obs.title,
            "text": obs.text,
            "forms": obs.forms,
            "links": obs.links,
            "snapshot_hash": obs.snapshot_hash,
        }
        data = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        sha = hashlib.sha256(data.encode()).hexdigest()
        path = self._root / f"{sha}.json"
        if not path.exists():
            path.write_text(data, encoding="utf-8")
            os.chmod(path, 0o600)
        return sha, path

    def load(self, sha: str) -> dict:
        path = self._root / f"{sha}.json"
        data = path.read_text(encoding="utf-8")
        actual = hashlib.sha256(data.encode()).hexdigest()
        if actual != sha:
            raise ValueError(f"artifact {sha} has been modified (content hash {actual})")
        return json.loads(data)
