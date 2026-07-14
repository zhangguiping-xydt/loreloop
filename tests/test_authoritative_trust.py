from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from loreloop.evidence.chain import EvidenceChain
from loreloop.knowledge.authoritative_capsule import CapsuleArtifact
from loreloop.knowledge.authoritative_git import capture_source_snapshot
from loreloop.knowledge.authoritative_trust import (
    ExportTrustError,
    attest_export,
    verify_trusted_export,
)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _repository(path: Path) -> Path:
    path.mkdir()
    _git(path, "init")
    (path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "initial")
    return path


def test_trusted_export_binds_package_to_chain_and_checkout_location(tmp_path: Path) -> None:
    root = _repository(tmp_path / "root")
    peer = _repository(tmp_path / "peer")
    snapshot = capture_source_snapshot(root, {"peer": peer})
    capsule = CapsuleArtifact(".loreloop-export.json", "{}\n", "1" * 64)
    chain = EvidenceChain.for_workdir(root, key_dir=tmp_path / "keys")
    _ = attest_export(chain, root, snapshot, capsule, "2" * 64, {"peer": peer})

    verified = verify_trusted_export(
        chain.verify(), root, capsule, "2" * 64, {"peer": peer}
    )

    assert verified.event == "authoritative_export_attested"


def test_trusted_export_rejects_same_history_clone_substituted_for_alias(tmp_path: Path) -> None:
    root = _repository(tmp_path / "root")
    peer = _repository(tmp_path / "peer")
    replacement = tmp_path / "replacement"
    subprocess.run(["git", "clone", str(peer), str(replacement)], check=True, capture_output=True)
    snapshot = capture_source_snapshot(root, {"peer": peer})
    capsule = CapsuleArtifact(".loreloop-export.json", "{}\n", "1" * 64)
    chain = EvidenceChain.for_workdir(root, key_dir=tmp_path / "keys")
    _ = attest_export(chain, root, snapshot, capsule, "2" * 64, {"peer": peer})

    with pytest.raises(ExportTrustError, match="identity or checkout location"):
        _ = verify_trusted_export(
            chain.verify(), root, capsule, "2" * 64, {"peer": replacement}
        )


def test_trusted_export_rejects_unattested_capsule(tmp_path: Path) -> None:
    root = _repository(tmp_path / "root")
    capsule = CapsuleArtifact(".loreloop-export.json", "{}\n", "1" * 64)
    chain = EvidenceChain.for_workdir(root, key_dir=tmp_path / "keys")

    with pytest.raises(ExportTrustError, match="no local trust attestation"):
        _ = verify_trusted_export(chain.verify(), root, capsule, "2" * 64)


def test_trusted_export_supports_non_git_aggregate_project_root(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    backend = _repository(workspace / "backend")
    peers = {"backend": backend}
    snapshot = capture_source_snapshot(workspace, peers)
    capsule = CapsuleArtifact(".loreloop-export.json", "{}\n", "1" * 64)
    chain = EvidenceChain.for_workdir(workspace, key_dir=tmp_path / "keys")
    _ = attest_export(chain, workspace, snapshot, capsule, "2" * 64, peers)

    verified = verify_trusted_export(
        chain.verify(), workspace, capsule, "2" * 64, peers
    )

    assert set(verified.payload["repositories"]) == {"backend"}
