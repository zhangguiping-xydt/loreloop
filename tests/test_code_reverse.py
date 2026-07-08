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
        [{"claim": "POST /upload returns 201.", "title": "Upload status", "file": "app.py"}]
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
    assert e.trust.curation is Curation.DRAFT
    assert e.trust.verification is Verification.UNVERIFIED


def test_extract_rejects_unknown_file(repo):
    out = json.dumps([{"claim": "x", "title": "t", "file": "ghost.py"}])
    with pytest.raises(ExtractionError, match="unknown file"):
        extract_assertions(FakeRunner([out]), repo, [repo / "app.py"])


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


def test_json_extraction_tolerates_surrounding_prose():
    assertions = [RawAssertion(claim="a", title="t", file="f")]
    out = 'Here you go:\n[{"id": 0, "kind": "constraint"}]\nDone.'
    kinds = classify_assertions(FakeRunner([out]), assertions)
    assert kinds == [Kind.CONSTRAINT]
