from __future__ import annotations

import shutil
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

    verified = verify_trusted_export(chain.verify(), root, capsule, "2" * 64, {"peer": peer})

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
        _ = verify_trusted_export(chain.verify(), root, capsule, "2" * 64, {"peer": replacement})


def test_trusted_export_rejects_same_history_clone_recreated_at_original_path(
    tmp_path: Path,
) -> None:
    root = _repository(tmp_path / "root")
    peer = _repository(tmp_path / "peer")
    snapshot = capture_source_snapshot(root, {"peer": peer})
    capsule = CapsuleArtifact(".loreloop-export.json", "{}\n", "1" * 64)
    chain = EvidenceChain.for_workdir(root, key_dir=tmp_path / "keys")
    _ = attest_export(chain, root, snapshot, capsule, "2" * 64, {"peer": peer})
    original = tmp_path / "peer-original"
    peer.rename(original)
    subprocess.run(["git", "clone", str(original), str(peer)], check=True, capture_output=True)

    with pytest.raises(ExportTrustError, match="checkout instance changed"):
        _ = verify_trusted_export(chain.verify(), root, capsule, "2" * 64, {"peer": peer})


def test_trusted_export_rejects_different_head_in_same_checkout(tmp_path: Path) -> None:
    root = _repository(tmp_path / "root")
    snapshot = capture_source_snapshot(root)
    capsule = CapsuleArtifact(".loreloop-export.json", "{}\n", "1" * 64)
    chain = EvidenceChain.for_workdir(root, key_dir=tmp_path / "keys")
    _ = attest_export(chain, root, snapshot, capsule, "2" * 64)
    (root / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "changed")

    with pytest.raises(ExportTrustError, match="source snapshot changed"):
        _ = verify_trusted_export(chain.verify(), root, capsule, "2" * 64)


def test_trusted_export_ignores_git_dir_redirect_during_clone_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repository(tmp_path / "root")
    snapshot = capture_source_snapshot(root)
    capsule = CapsuleArtifact(".loreloop-export.json", "{}\n", "1" * 64)
    chain = EvidenceChain.for_workdir(root, key_dir=tmp_path / "keys")
    _ = attest_export(chain, root, snapshot, capsule, "2" * 64)
    records = chain.verify()
    original = tmp_path / "original"
    root.rename(original)
    subprocess.run(["git", "clone", str(original), str(root)], check=True, capture_output=True)
    (root / "app.py").write_text("MALICIOUS = True\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "replacement")
    monkeypatch.setenv("GIT_DIR", str(original / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(root))

    with pytest.raises(ExportTrustError, match="source snapshot changed"):
        _ = verify_trusted_export(records, root, capsule, "2" * 64)


def test_trusted_export_rejects_git_contents_replaced_inside_same_directory_inode(
    tmp_path: Path,
) -> None:
    root = _repository(tmp_path / "root")
    snapshot = capture_source_snapshot(root)
    capsule = CapsuleArtifact(".loreloop-export.json", "{}\n", "1" * 64)
    chain = EvidenceChain.for_workdir(root, key_dir=tmp_path / "keys")
    _ = attest_export(chain, root, snapshot, capsule, "2" * 64)
    records = chain.verify()
    replacement = tmp_path / "replacement"
    subprocess.run(["git", "clone", str(root), str(replacement)], check=True, capture_output=True)
    (replacement / "app.py").write_text("MALICIOUS = True\n", encoding="utf-8")
    _git(replacement, "add", "-A")
    _git(
        replacement,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@t",
        "commit",
        "-m",
        "replacement",
    )
    git_directory = root / ".git"
    original_identity = (git_directory.stat().st_dev, git_directory.stat().st_ino)
    for child in git_directory.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()
    for child in (replacement / ".git").iterdir():
        destination = git_directory / child.name
        if child.is_dir() and not child.is_symlink():
            shutil.copytree(child, destination, symlinks=True)
        else:
            shutil.copy2(child, destination, follow_symlinks=False)
    (root / "app.py").write_text("MALICIOUS = True\n", encoding="utf-8")
    assert (git_directory.stat().st_dev, git_directory.stat().st_ino) == original_identity

    with pytest.raises(ExportTrustError, match="source snapshot changed"):
        _ = verify_trusted_export(records, root, capsule, "2" * 64)


def test_trusted_export_allows_untracked_export_artifact(tmp_path: Path) -> None:
    root = _repository(tmp_path / "root")
    snapshot = capture_source_snapshot(root)
    capsule = CapsuleArtifact(".loreloop-export.json", "{}\n", "1" * 64)
    chain = EvidenceChain.for_workdir(root, key_dir=tmp_path / "keys")
    _ = attest_export(chain, root, snapshot, capsule, "2" * 64)
    (root / "knowledge.zip").write_bytes(b"export artifact")

    verified = verify_trusted_export(chain.verify(), root, capsule, "2" * 64)

    assert verified.event == "authoritative_export_attested"


def test_trusted_working_tree_export_excludes_its_managed_output(tmp_path: Path) -> None:
    root = _repository(tmp_path / "root")
    (root / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
    exclusions = {".": ("baseline.zip",)}
    snapshot = capture_source_snapshot(
        root,
        working_tree=True,
        excluded_paths=exclusions,
    )
    capsule = CapsuleArtifact(".loreloop-export.json", "{}\n", "1" * 64)
    chain = EvidenceChain.for_workdir(root, key_dir=tmp_path / "keys")
    _ = attest_export(chain, root, snapshot, capsule, "2" * 64)
    (root / "baseline.zip").write_bytes(b"managed export")

    verified = verify_trusted_export(chain.verify(), root, capsule, "2" * 64)

    assert verified.event == "authoritative_export_attested"
    (root / "app.py").write_text("VALUE = 3\n", encoding="utf-8")
    with pytest.raises(ExportTrustError, match="source snapshot changed"):
        _ = verify_trusted_export(chain.verify(), root, capsule, "2" * 64)


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

    verified = verify_trusted_export(chain.verify(), workspace, capsule, "2" * 64, peers)

    assert set(verified.payload["repositories"]) == {"backend"}


def test_trusted_export_selects_exact_capsule_when_package_id_is_shared(
    tmp_path: Path,
) -> None:
    root = _repository(tmp_path / "root")
    snapshot = capture_source_snapshot(root)
    first = CapsuleArtifact(".loreloop-export.json", "first\n", "1" * 64)
    second = CapsuleArtifact(".loreloop-export.json", "second\n", "3" * 64)
    chain = EvidenceChain.for_workdir(root, key_dir=tmp_path / "keys")
    _ = attest_export(chain, root, snapshot, first, "2" * 64)
    _ = attest_export(chain, root, snapshot, second, "2" * 64)

    verified = verify_trusted_export(chain.verify(), root, first, "2" * 64)

    assert verified.payload["capsule_sha256"] == first.sha256
