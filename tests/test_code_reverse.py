import json
import subprocess
from pathlib import Path

import pytest

from knowhelm.knowledge.code_reverse import (
    ExtractionError,
    RawAssertion,
    classify_assertions,
    drifted_code_entry_ids,
    extract_assertions,
    reverse_code,
    scan_repo,
)
from knowhelm.knowledge.model import Channel, Curation, Entry, Kind, Source, Verification
from knowhelm.knowledge.repos import save_repos


class FakeRunner:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.prompts = []

    def run(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.outputs.pop(0)


@pytest.fixture()
def repo(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "app.py").write_text("def upload():\n    return 201\n")
    (tmp_path / "notes.txt").write_text("not source code")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "init"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    return tmp_path


def test_scan_repo_filters_by_extension(repo):
    files = scan_repo(repo)
    assert [f.name for f in files] == ["app.py"]


def test_reverse_code_produces_anchored_entries(repo):
    extract_out = json.dumps(
        [{
            "claim": "POST /upload returns 201.",
            "title": "Upload status",
            "file": "app.py",
            "evidence": {
                "line_start": 1,
                "line_end": 2,
                "symbol": "upload",
                "excerpt": "def upload():\n    return 201",
            },
        }]
    )
    classify_out = json.dumps([{"id": 0, "kind": "interface"}])
    runner = FakeRunner([extract_out, classify_out])

    entries = reverse_code(runner, repo)

    assert len(entries) == 1
    e = entries[0]
    assert e.kind is Kind.INTERFACE
    assert e.source.channel is Channel.CODE
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
    ).stdout.strip()
    assert e.source.locator == f"app.py@{head}"
    assert e.source.snapshot_ref == head
    assert e.source.line_start == 1
    assert e.source.line_end == 2
    assert e.source.symbol == "upload"
    assert e.source.excerpt == "def upload():\n    return 201"
    assert e.trust.curation is Curation.DRAFT
    assert e.trust.verification is Verification.UNVERIFIED


def test_reverse_code_prefixes_declared_repository(repo):
    runner = FakeRunner([
        json.dumps([{"claim": "The value is one.", "title": "Value", "file": "app.py"}]),
        json.dumps([{"id": 0, "kind": "behavior"}]),
    ])

    entry = reverse_code(runner, repo, repo_name="backend")[0]

    assert entry.source.locator.startswith("repo:backend/app.py@")


def test_extract_rejects_unknown_file(repo):
    out = json.dumps([{"claim": "x", "title": "t", "file": "ghost.py"}])
    with pytest.raises(ExtractionError, match="unknown file"):
        extract_assertions(FakeRunner([out]), repo, [repo / "app.py"])


def test_extract_rejects_evidence_lines_outside_file(repo):
    out = json.dumps([{
        "claim": "POST /upload returns 201.",
        "title": "Upload status",
        "file": "app.py",
        "evidence": {"line_start": 9, "line_end": 10, "symbol": "upload"},
    }])

    with pytest.raises(ExtractionError, match="evidence line"):
        extract_assertions(FakeRunner([out]), repo, [repo / "app.py"])


def test_extract_rejects_fabricated_evidence_excerpt(repo):
    out = json.dumps([{
        "claim": "POST /upload returns 201.",
        "title": "Upload status",
        "file": "app.py",
        "evidence": {
            "line_start": 1,
            "line_end": 2,
            "symbol": "upload",
            "excerpt": "return 500",
        },
    }])

    with pytest.raises(ExtractionError, match="excerpt does not match"):
        extract_assertions(FakeRunner([out]), repo, [repo / "app.py"])


def test_reverse_prompt_prioritizes_high_value_facts_and_versions_it(repo):
    runner = FakeRunner(["[]"])

    assert extract_assertions(runner, repo, [repo / "app.py"]) == []

    prompt = runner.prompts[0]
    assert "prompt-version:" in prompt
    assert "Do not extract" in prompt
    assert "test fixture values" in prompt
    assert "3 to 15" not in prompt
    assert "0001 | def upload():" in prompt


def test_extract_rejects_non_json():
    with pytest.raises(ExtractionError, match="not a JSON array"):
        extract_assertions(FakeRunner(["Sure! Here are the facts..."]), Path("/tmp"), [])


def test_classify_rejects_count_mismatch():
    assertions = [RawAssertion(claim="a", title="t", file="f"), RawAssertion(claim="b", title="t", file="f")]
    out = json.dumps([{"id": 0, "kind": "behavior"}])
    with pytest.raises(ExtractionError, match="returned 1 items"):
        classify_assertions(FakeRunner([out]), assertions)


def test_classify_rejects_unknown_kind():
    assertions = [RawAssertion(claim="a", title="t", file="f")]
    out = json.dumps([{"id": 0, "kind": "vibes"}])
    with pytest.raises(ExtractionError, match="invalid classification"):
        classify_assertions(FakeRunner([out]), assertions)


def code_entry(file, anchor):
    return Entry(
        title=f"fact about {file}",
        content=f"{file} does something.",
        kind=Kind.BEHAVIOR,
        source=Source(channel=Channel.CODE, locator=f"{file}@{anchor}", snapshot_ref=anchor),
    )


def test_drift_detection_flags_changed_files_only(repo):
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
    ).stdout.strip()
    (repo / "app.py").write_text("def upload():\n    return 202\n")
    subprocess.run(["git", "add", "app.py"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "change"],
        cwd=repo, check=True, capture_output=True,
    )

    changed = code_entry("app.py", base)
    untouched = code_entry("notes.txt", base)
    web = Entry(
        title="login page", content="Login redirects.", kind=Kind.BEHAVIOR,
        source=Source(channel=Channel.WEB, locator="http://x/login", snapshot_ref="h1"),
    )

    assert drifted_code_entry_ids(repo, [changed, untouched, web]) == {changed.id}


def test_drift_detection_treats_unknown_anchor_as_drifted(repo):
    ghost = code_entry("app.py", "0000000000000000000000000000000000000000")
    assert drifted_code_entry_ids(repo, [ghost]) == {ghost.id}


def test_drift_detection_treats_missing_anchor_as_drifted(repo):
    # a code entry with no snapshot_ref can never prove freshness
    anchorless = Entry(
        title="claim about app.py", content="app.py does things.", kind=Kind.BEHAVIOR,
        source=Source(channel=Channel.CODE, locator="app.py"),
    )
    assert drifted_code_entry_ids(repo, [anchorless]) == {anchorless.id}


def test_drift_detection_is_grouped_by_repository(repo):
    backend = repo.parent / f"{repo.name}-backend"
    backend.mkdir()
    subprocess.run(["git", "init"], cwd=backend, check=True, capture_output=True)
    (backend / "app.py").write_text("value = 1\n")
    subprocess.run(["git", "add", "-A"], cwd=backend, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "init"],
        cwd=backend,
        check=True,
        capture_output=True,
    )
    save_repos(repo, {"backend": backend})
    root_base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    backend_base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=backend, capture_output=True, text=True, check=True
    ).stdout.strip()
    root_entry = code_entry("app.py", root_base)
    backend_entry = Entry(
        title="Backend value",
        content="Backend value is one.",
        kind=Kind.BEHAVIOR,
        source=Source(
            channel=Channel.CODE,
            locator=f"repo:backend/app.py@{backend_base}",
            snapshot_ref=backend_base,
        ),
    )

    (backend / "app.py").write_text("value = 2\n")
    subprocess.run(["git", "add", "app.py"], cwd=backend, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "change"],
        cwd=backend,
        check=True,
        capture_output=True,
    )

    assert drifted_code_entry_ids(repo, [root_entry, backend_entry]) == {backend_entry.id}


def test_removing_repository_declaration_only_demotes_entries(repo):
    backend = repo.parent / f"{repo.name}-backend"
    backend.mkdir()
    subprocess.run(["git", "init"], cwd=backend, check=True, capture_output=True)
    (backend / "app.py").write_text("value = 1\n")
    subprocess.run(["git", "add", "-A"], cwd=backend, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "init"],
        cwd=backend,
        check=True,
        capture_output=True,
    )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=backend, capture_output=True, text=True, check=True
    ).stdout.strip()
    save_repos(repo, {"backend": backend})
    entry = Entry(
        title="Backend value",
        content="Backend value is one.",
        kind=Kind.BEHAVIOR,
        source=Source(
            channel=Channel.CODE,
            locator=f"repo:backend/app.py@{head}",
            snapshot_ref=head,
        ),
    )
    (repo / ".knowhelm/repos.json").unlink()

    assert drifted_code_entry_ids(repo, [entry]) == {entry.id}


def test_json_extraction_tolerates_surrounding_prose():
    assertions = [RawAssertion(claim="a", title="t", file="f")]
    out = 'Here you go:\n[{"id": 0, "kind": "constraint"}]\nDone.'
    kinds = classify_assertions(FakeRunner([out]), assertions)
    assert kinds == [Kind.CONSTRAINT]
