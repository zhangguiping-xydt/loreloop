"""Content-addressed evidence artifacts.

Stores what the browser actually observed so a report can be independently
re-audited after the live page changes. Files are named by the SHA-256 of
their canonical JSON, and that hash is recorded on the evidence chain, so an
edited artifact no longer matches its chain record.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from ..webexplore.browser import Observation
from ..paths import (
    ensure_private_directory,
    ensure_state_root,
    reject_symlink,
    secure_atomic_write_text,
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ArtifactStore:
    def __init__(self, root: Path) -> None:
        self._root = root
        # Observations may capture post-login page content: owner-only, like
        # the evidence signing key.
        ensure_private_directory(self._root)

    @classmethod
    def for_workdir(cls, workdir: Path) -> "ArtifactStore":
        state = ensure_state_root(workdir)
        evidence = ensure_private_directory(state / "evidence")
        return cls(evidence / "artifacts")

    def save_observation(self, obs: Observation) -> tuple[str, Path]:
        payload = {
            "type": "page_observation",
            "url": obs.url,
            "title": obs.title,
            "text": obs.text,
            "forms": obs.forms,
            "links": obs.links,
            "headings": obs.headings,
            "buttons": obs.buttons,
            "nav": obs.nav,
            "snapshot_hash": obs.snapshot_hash,
        }
        return self.save_json(payload)

    def save_json(self, payload: dict) -> tuple[str, Path]:
        if "type" not in payload:
            raise ValueError("artifact payload must include type")
        data = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        sha = hashlib.sha256(data.encode()).hexdigest()
        path = self._root / f"{sha}.json"
        reject_symlink(path, label="evidence artifact")
        if not path.exists():
            # Write-then-rename: a reader (or a crash) must never see a
            # half-written artifact under its final content-addressed name.
            secure_atomic_write_text(path, data)
        else:
            path.chmod(0o600)
        return sha, path

    def load(self, sha: str) -> dict:
        # sha comes from chain payloads, which the operator can influence;
        # validating the shape keeps it from ever acting as a path fragment.
        if not _SHA256.match(sha):
            raise ValueError(f"invalid artifact reference: {sha!r}")
        path = self._root / f"{sha}.json"
        reject_symlink(path, label="evidence artifact")
        data = path.read_text(encoding="utf-8")
        actual = hashlib.sha256(data.encode()).hexdigest()
        if actual != sha:
            raise ValueError(f"artifact {sha} has been modified (content hash {actual})")
        return json.loads(data)
