from __future__ import annotations

import hashlib
import json
import subprocess
from copy import deepcopy
from pathlib import Path
from typing import cast

import pytest

from loreloop.knowledge.authoritative_ast_render import render_document_set
from loreloop.knowledge.authoritative_capsule import (
    CAPSULE_FILENAME,
    JsonValue,
    build_capsule,
)
from loreloop.knowledge.authoritative_capsule_replay import (
    CapsuleReplayError,
    CapsuleTrustClaim,
    replay_capsule_directory,
)
from loreloop.knowledge.authoritative_capsule_render import render_capsule_ast
from loreloop.knowledge.authoritative_document_ast import build_document_ast_set
from loreloop.knowledge.authoritative_git import capture_source_snapshot
from loreloop.knowledge.authoritative_ids import canon_v4
from loreloop.knowledge.authoritative_semantic import build_semantic_core
from loreloop.knowledge.authoritative_source import detect_source_snapshot, read_snapshot_blobs


def _git(repo: Path, *args: str) -> None:
    _ = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def export_dir(tmp_path: Path) -> Path:
    repo = tmp_path / "project"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.name", "LoreLoop Test")
    _git(repo, "config", "user.email", "loreloop@example.invalid")
    _ = (repo / "app.py").write_text(
        '@app.get("/health")\ndef health(): return {"ok": True}\n', encoding="utf-8"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "initial")
    snapshot = capture_source_snapshot(repo)
    core = build_semantic_core(
        snapshot,
        read_snapshot_blobs(snapshot, repo),
        detect_source_snapshot(snapshot, repo),
        project_name="demo",
    )
    document_set = build_document_ast_set(core)
    documents = render_document_set(document_set)
    capsule = build_capsule(core, document_set, documents)
    output = tmp_path / "export"
    output.mkdir()
    for document in documents:
        _ = (output / document.filename).write_text(document.content, encoding="utf-8")
    _ = (output / capsule.filename).write_text(capsule.content, encoding="utf-8")
    return output


def _payload(export_dir: Path) -> dict[str, JsonValue]:
    return cast(
        dict[str, JsonValue],
        json.loads((export_dir / CAPSULE_FILENAME).read_text(encoding="utf-8")),
    )


def _documents(payload: dict[str, JsonValue]) -> list[dict[str, JsonValue]]:
    values = cast(list[JsonValue], payload["documents"])
    return [cast(dict[str, JsonValue], value) for value in values]


def _rewrite(export_dir: Path, payload: dict[str, JsonValue]) -> None:
    content = canon_v4(payload).decode() + "\n"
    _ = (export_dir / CAPSULE_FILENAME).write_text(content, encoding="utf-8")


def _ast(document: dict[str, JsonValue]) -> dict[str, JsonValue]:
    return cast(dict[str, JsonValue], document["ast"])


def _reseal_document(
    export_dir: Path,
    payload: dict[str, JsonValue],
    document: dict[str, JsonValue],
) -> None:
    ast = _ast(document)
    document["ast_sha256"] = hashlib.sha256(canon_v4(ast)).hexdigest()
    filenames = tuple(cast(str, item["filename"]) for item in _documents(payload))
    markdown = render_capsule_ast(cast(JsonValue, ast), filenames)
    filename = cast(str, document["filename"])
    _ = (export_dir / filename).write_text(markdown, encoding="utf-8")
    document["markdown_sha256"] = hashlib.sha256(markdown.encode()).hexdigest()
    _rewrite(export_dir, payload)


def test_no_key_replay_proves_complete_package_closure(export_dir: Path) -> None:
    result = replay_capsule_directory(export_dir)

    assert result.verification_mode == "no_key"
    assert len(result.documents) == 7
    assert result.package_id
    assert (
        result.capsule_sha256
        == hashlib.sha256((export_dir / CAPSULE_FILENAME).read_bytes()).hexdigest()
    )


def test_replay_rejects_markdown_even_if_attacker_updates_its_digest(export_dir: Path) -> None:
    payload = _payload(export_dir)
    document = _documents(payload)[0]
    filename = cast(str, document["filename"])
    changed = (export_dir / filename).read_text(encoding="utf-8") + "tampered\n"
    _ = (export_dir / filename).write_text(changed, encoding="utf-8")
    document["markdown_sha256"] = hashlib.sha256(changed.encode()).hexdigest()
    _rewrite(export_dir, payload)

    with pytest.raises(CapsuleReplayError, match="not the rendering"):
        replay_capsule_directory(export_dir)


def test_replay_rejects_ast_summary_tampering(export_dir: Path) -> None:
    payload = _payload(export_dir)
    document = _documents(payload)[0]
    ast = cast(dict[str, JsonValue], document["ast"])
    ast["title"] = "tampered title"
    _rewrite(export_dir, payload)

    with pytest.raises(CapsuleReplayError, match="AST digest mismatch"):
        replay_capsule_directory(export_dir)


def test_replay_rejects_resealed_document_id_not_derived_from_semantic_core(
    export_dir: Path,
) -> None:
    payload = _payload(export_dir)
    document = _documents(payload)[0]
    replacement = "DOC-" + "f" * 64
    document["document_id"] = replacement
    _ast(document)["document_id"] = replacement
    _reseal_document(export_dir, payload, document)

    with pytest.raises(CapsuleReplayError, match="deterministic SemanticCore projection"):
        replay_capsule_directory(export_dir)


def test_replay_rejects_resealed_ast_row_not_projected_from_semantic_core(
    export_dir: Path,
) -> None:
    payload = _payload(export_dir)
    document = _documents(payload)[0]
    sections = cast(list[dict[str, JsonValue]], _ast(document)["sections"])
    rows = cast(list[dict[str, JsonValue]], sections[0]["rows"])
    values = cast(list[dict[str, JsonValue]], rows[0]["values"])
    values[0]["value"] = "attacker-resealed-value"
    _reseal_document(export_dir, payload, document)

    with pytest.raises(CapsuleReplayError, match="deterministic SemanticCore projection"):
        replay_capsule_directory(export_dir)


def test_replay_rejects_resealed_row_routed_to_the_wrong_document(
    export_dir: Path,
) -> None:
    payload = _payload(export_dir)
    documents = _documents(payload)
    source = next(
        document
        for document in documents
        if any(
            section.get("section_id") == "modules"
            for section in cast(list[dict[str, JsonValue]], _ast(document)["sections"])
        )
    )
    module_section = next(
        section
        for section in cast(list[dict[str, JsonValue]], _ast(source)["sections"])
        if section.get("section_id") == "modules"
    )
    target = next(
        document
        for document in documents
        if _ast(document).get("required_family") == "acceptance"
    )
    target_sections = cast(list[JsonValue], _ast(target)["sections"])
    target_sections.append(cast(JsonValue, deepcopy(module_section)))
    _reseal_document(export_dir, payload, target)

    with pytest.raises(CapsuleReplayError, match="deterministic SemanticCore projection"):
        replay_capsule_directory(export_dir)


def test_replay_rejects_resealed_coverage_not_derived_from_semantic_core(
    export_dir: Path,
) -> None:
    payload = _payload(export_dir)
    document = _documents(payload)[0]
    header = cast(dict[str, JsonValue], _ast(document)["header"])
    coverage = cast(dict[str, JsonValue], header["coverage"])
    coverage["record_total"] = cast(int, coverage["record_total"]) + 1
    _reseal_document(export_dir, payload, document)

    with pytest.raises(CapsuleReplayError, match="deterministic SemanticCore projection"):
        replay_capsule_directory(export_dir)


def test_replay_rejects_resealed_evidence_projection_not_in_semantic_core(
    export_dir: Path,
) -> None:
    payload = _payload(export_dir)
    document = _documents(payload)[0]
    evidence_rows = cast(list[dict[str, JsonValue]], _ast(document)["evidence_rows"])
    values = cast(list[dict[str, JsonValue]], evidence_rows[0]["values"])
    path_value = next(value for value in values if value.get("pointer") == "/path")
    path_value["value"] = "attacker/rewritten.py"
    _reseal_document(export_dir, payload, document)

    with pytest.raises(CapsuleReplayError, match="deterministic SemanticCore projection"):
        replay_capsule_directory(export_dir)


def test_replay_requires_regeneration_of_legacy_weak_schema_v2_capsules(
    export_dir: Path,
) -> None:
    payload = _payload(export_dir)
    semantic = cast(dict[str, JsonValue], payload["semantic_core"])
    del semantic["evidence"]
    for raw_record in cast(list[dict[str, JsonValue]], semantic["records"]):
        del raw_record["atom_kind"]
        del raw_record["value_order"]
    _rewrite(export_dir, payload)

    with pytest.raises(CapsuleReplayError, match="legacy capsule.*regenerate"):
        replay_capsule_directory(export_dir)


def test_replay_rejects_json_identity_tampering_and_duplicate_fields(export_dir: Path) -> None:
    payload = _payload(export_dir)
    payload["package_id"] = "0" * 64
    _rewrite(export_dir, payload)
    with pytest.raises(CapsuleReplayError, match="package id mismatch"):
        replay_capsule_directory(export_dir)

    payload = _payload(export_dir)
    content = (export_dir / CAPSULE_FILENAME).read_text(encoding="utf-8")
    duplicate = content.replace('"package_id":', '"package_id":"0","package_id":', 1)
    _ = (export_dir / CAPSULE_FILENAME).write_text(duplicate, encoding="utf-8")
    with pytest.raises(CapsuleReplayError, match="duplicate field"):
        replay_capsule_directory(export_dir)


def test_replay_rejects_missing_and_unsafe_paths_but_ignores_unmanaged_files(
    export_dir: Path,
) -> None:
    result = replay_capsule_directory(export_dir)
    missing = export_dir / result.documents[0]
    original = missing.read_bytes()
    missing.unlink()
    with pytest.raises(CapsuleReplayError, match="file set mismatch"):
        replay_capsule_directory(export_dir)
    missing.write_bytes(original)
    _ = (export_dir / "extra.md").write_text("extra", encoding="utf-8")
    replayed = replay_capsule_directory(export_dir)
    assert replayed.documents == result.documents
    assert (export_dir / "extra.md").read_text(encoding="utf-8") == "extra"
    (export_dir / "extra.md").unlink()

    payload = _payload(export_dir)
    _documents(payload)[0]["filename"] = "../escape.md"
    _rewrite(export_dir, payload)
    with pytest.raises(CapsuleReplayError, match="unsafe document filename"):
        replay_capsule_directory(export_dir)


def test_replay_rejects_symlinked_export_entries(export_dir: Path, tmp_path: Path) -> None:
    result = replay_capsule_directory(export_dir)
    markdown = export_dir / result.documents[0]
    target = tmp_path / "outside.md"
    target.write_bytes(markdown.read_bytes())
    markdown.unlink()
    markdown.symlink_to(target)

    with pytest.raises(CapsuleReplayError, match="regular file"):
        replay_capsule_directory(export_dir)


def test_trusted_replay_delegates_exact_digest_without_touching_local_state(
    export_dir: Path,
) -> None:
    class Verifier:
        claim: CapsuleTrustClaim | None = None

        def verify(self, claim: CapsuleTrustClaim) -> None:
            self.claim = claim

    verifier = Verifier()
    result = replay_capsule_directory(export_dir, trust_verifier=verifier)

    assert result.verification_mode == "trusted"
    assert verifier.claim is not None
    assert verifier.claim.capsule_sha256 == result.capsule_sha256


def test_trusted_replay_reports_external_rejection(export_dir: Path) -> None:
    class RejectingVerifier:
        def verify(self, claim: CapsuleTrustClaim) -> None:
            raise ValueError(claim.package_id)

    with pytest.raises(CapsuleReplayError, match="trusted verifier rejected"):
        replay_capsule_directory(export_dir, trust_verifier=RejectingVerifier())
