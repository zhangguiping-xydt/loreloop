import json
import subprocess
from pathlib import Path

import pytest

from loreloop.knowledge.code_reverse import (
    ExtractionError,
    IngestionPolicy,
    RawAssertion,
    classify_assertions,
    dirty_source_files,
    drifted_code_entry_ids,
    extract_assertions,
    reverse_code,
    scan_repo,
    scan_repo_manifest,
)
from loreloop.knowledge.model import Channel, Curation, Entry, Kind, Source, Verification
from loreloop.knowledge.repos import save_repos


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


def test_scan_repo_preserves_non_ascii_git_paths(repo):
    source = repo / "上传策略.ts"
    source.write_text("export const maxUploadMiB = 50;\n", encoding="utf-8")
    subprocess.run(["git", "add", source.name], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "中文路径"],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    assert source in scan_repo(repo)


def test_scan_repo_rejects_tracked_symlink_escape(repo):
    outside = repo.parent / f"{repo.name}-outside.py"
    outside.write_text("SECRET = 'outside repository'\n", encoding="utf-8")
    link = repo / "escape.py"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable on this platform")
    subprocess.run(["git", "add", "escape.py"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "link"],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    assert link not in scan_repo(repo)


def test_scan_manifest_covers_contract_files_and_reports_every_skip(repo):
    (repo / "schema.proto").write_text("message Upload {}\n")
    (repo / "Dockerfile").write_text("FROM python:3.12\n")
    (repo / "custom.contract").write_text("upload=enabled\n")
    (repo / "oversize.md").write_text("x" * 100)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "contracts"],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    manifest = scan_repo_manifest(
        repo,
        include=("*.contract",),
        exclude=("notes.txt",),
        max_file_bytes=80,
    )

    assert {path.name for path in manifest.files} >= {
        "app.py",
        "schema.proto",
        "Dockerfile",
        "custom.contract",
    }
    assert manifest.skipped["excluded"] == ["notes.txt"]
    assert manifest.skipped["too-large"] == ["oversize.md"]


def test_dirty_source_files_includes_staged_unstaged_and_untracked(repo):
    (repo / "app.py").write_text("def upload():\n    return 202\n")
    (repo / "policy.yaml").write_text("max_upload: 50\n")
    subprocess.run(["git", "add", "policy.yaml"], cwd=repo, check=True, capture_output=True)
    (repo / "fresh.ts").write_text("export const enabled = true;\n")

    assert dirty_source_files(repo) == ["app.py", "fresh.ts", "policy.yaml"]


def test_dirty_source_files_uses_the_same_custom_ingestion_policy(repo):
    (repo / "contract.avsc").write_text('{"limit": 8}\n')
    (repo / "README.md").write_text("changed documentation\n")

    policy = IngestionPolicy(include=("*.avsc",), exclude=("README.md",))

    assert dirty_source_files(repo, policy=policy) == ["contract.avsc"]


def test_reverse_code_splits_batches_by_total_source_bytes(repo):
    files = []
    for index in range(4):
        path = repo / f"large_{index}.py"
        path.write_text("x" * 50_000)
        files.append(path)
    runner = FakeRunner(["[]", "[]"])

    assert reverse_code(runner, repo, files=files) == []
    assert len(runner.prompts) == 2


def test_reverse_code_reports_progress_in_model_call_order(repo):
    files = []
    for index in range(9):
        path = repo / f"batch_{index}.py"
        path.write_text(f"value = {index}\n")
        files.append(path)
    events = []

    class OrderedRunner(FakeRunner):
        def run(self, prompt: str) -> str:
            events.append(("model", len(self.prompts) + 1))
            return super().run(prompt)

    runner = OrderedRunner(
        [
            json.dumps(
                [
                    {
                        "claim": "The first value is zero.",
                        "title": "First value",
                        "file": "batch_0.py",
                    }
                ]
            ),
            json.dumps([{"id": 0, "kind": "constraint"}]),
            "[]",
        ]
    )

    reverse_code(
        runner,
        repo,
        files=files,
        on_progress=lambda progress: events.append(
            (
                progress.stage,
                progress.batch_index,
                progress.batch_total,
                progress.file_count,
                progress.assertion_count,
            )
        ),
    )

    assert events == [
        ("extract", 1, 2, 8, None),
        ("model", 1),
        ("classify", 1, 2, 8, 1),
        ("model", 2),
        ("extract", 2, 2, 1, None),
        ("model", 3),
    ]


def test_reverse_code_without_progress_callback_writes_nothing(repo, capsys):
    reverse_code(FakeRunner(["[]"]), repo, files=[repo / "app.py"])

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_reverse_code_produces_anchored_entries(repo):
    extract_out = json.dumps(
        [
            {
                "claim": "POST /upload returns 201.",
                "title": "Upload status",
                "file": "app.py",
                "evidence": {
                    "line_start": 1,
                    "line_end": 2,
                    "symbol": "upload",
                    "excerpt": "def upload():\n    return 201",
                },
            }
        ]
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


def test_extraction_rejects_non_string_claim_fields(repo):
    runner = FakeRunner([json.dumps([{"claim": 123, "title": "Value", "file": "app.py"}])])

    with pytest.raises(ExtractionError, match="claim must be a non-empty string"):
        extract_assertions(runner, repo, [repo / "app.py"])


def test_reverse_code_prefixes_declared_repository(repo):
    runner = FakeRunner(
        [
            json.dumps([{"claim": "The value is one.", "title": "Value", "file": "app.py"}]),
            json.dumps([{"id": 0, "kind": "behavior"}]),
        ]
    )

    entry = reverse_code(runner, repo, repo_name="backend")[0]

    assert entry.source.locator.startswith("repo:backend/app.py@")


def test_extract_rejects_unknown_file(repo):
    out = json.dumps([{"claim": "x", "title": "t", "file": "ghost.py"}])
    with pytest.raises(ExtractionError, match="unknown file"):
        extract_assertions(FakeRunner([out]), repo, [repo / "app.py"])


def test_extract_rejects_evidence_lines_outside_file(repo):
    out = json.dumps(
        [
            {
                "claim": "POST /upload returns 201.",
                "title": "Upload status",
                "file": "app.py",
                "evidence": {"line_start": 9, "line_end": 10, "symbol": "upload"},
            }
        ]
    )

    with pytest.raises(ExtractionError, match="evidence line"):
        extract_assertions(FakeRunner([out]), repo, [repo / "app.py"])


def test_extract_first_attempt_rejects_fabricated_evidence_excerpt(repo):
    out = json.dumps(
        [
            {
                "claim": "POST /upload returns 201.",
                "title": "Upload status",
                "file": "app.py",
                "evidence": {
                    "line_start": 1,
                    "line_end": 2,
                    "symbol": "upload",
                    "excerpt": "return 500",
                },
            }
        ]
    )

    with pytest.raises(ExtractionError, match="excerpt does not match"):
        extract_assertions(FakeRunner([out]), repo, [repo / "app.py"])


def test_extract_accepts_qualified_module_symbol_derived_from_file_path(repo):
    source = repo / "src/loreloop/agents.py"
    source.parent.mkdir(parents=True)
    source.write_text('"""Agent adapters."""\n\nENABLED = True\n')
    out = json.dumps(
        [
            {
                "claim": "Agent adapters are enabled.",
                "title": "Agent adapters",
                "file": "src/loreloop/agents.py",
                "evidence": {
                    "line_start": 3,
                    "line_end": 3,
                    "symbol": "loreloop.agents",
                    "excerpt": "ENABLED = True",
                },
            }
        ]
    )

    assertions = extract_assertions(FakeRunner([out]), repo, [source])

    assert assertions[0].symbol == "loreloop.agents"


def test_extract_still_rejects_missing_qualified_function_or_class_symbol(repo):
    source = repo / "src/loreloop/agents.py"
    source.parent.mkdir(parents=True)
    source.write_text('"""Agent adapters."""\n\nENABLED = True\n')
    out = json.dumps(
        [
            {
                "claim": "Agent adapters are enabled.",
                "title": "Agent adapters",
                "file": "src/loreloop/agents.py",
                "evidence": {
                    "line_start": 3,
                    "line_end": 3,
                    "symbol": "loreloop.agents.MissingAgent",
                    "excerpt": "ENABLED = True",
                },
            }
        ]
    )

    with pytest.raises(ExtractionError, match="MissingAgent.*does not occur"):
        extract_assertions(FakeRunner([out]), repo, [source])


def test_reverse_retries_once_after_deterministic_evidence_rejection(repo):
    bad = json.dumps(
        [
            {
                "claim": "POST /upload returns 201.",
                "title": "Upload status",
                "file": "app.py",
                "evidence": {
                    "line_start": 1,
                    "line_end": 2,
                    "symbol": "upload",
                    "excerpt": "return 500",
                },
            }
        ]
    )
    repaired = json.dumps(
        [
            {
                "claim": "POST /upload returns 201.",
                "title": "Upload status",
                "file": "app.py",
                "evidence": {
                    "line_start": 1,
                    "line_end": 2,
                    "symbol": "upload",
                    "excerpt": "def upload():\n    return 201",
                },
            }
        ]
    )
    classified = json.dumps([{"id": 0, "kind": "interface"}])
    runner = FakeRunner([bad, repaired, classified])

    entries = reverse_code(runner, repo)

    assert len(entries) == 1
    assert len(runner.prompts) == 3
    assert "failed deterministic validation" in runner.prompts[1]
    assert "excerpt does not match app.py:1-2" in runner.prompts[1]


def test_reverse_canonicalizes_mismatched_excerpt_on_retry(repo):
    first_bad = json.dumps(
        [
            {
                "claim": "POST /upload returns 201.",
                "title": "Upload status",
                "file": "app.py",
                "evidence": {
                    "line_start": 1,
                    "line_end": 2,
                    "symbol": "upload",
                    "excerpt": "return 500",
                },
            }
        ]
    )
    retry_bad = json.dumps(
        [
            {
                "claim": "POST /upload returns 201.",
                "title": "Upload status",
                "file": "app.py",
                "evidence": {
                    "line_start": 1,
                    "line_end": 2,
                    "symbol": "upload",
                    "excerpt": "return 204",
                },
            }
        ]
    )
    classified = json.dumps([{"id": 0, "kind": "interface"}])
    runner = FakeRunner([first_bad, retry_bad, classified])

    entries = reverse_code(runner, repo)

    assert len(entries) == 1
    assert entries[0].source.excerpt == "def upload():\n    return 201"


@pytest.mark.parametrize(
    ("item", "message"),
    [
        (
            {
                "claim": "POST /upload returns 201.",
                "title": "Upload status",
                "file": "ghost.py",
                "evidence": {"line_start": 1, "line_end": 1, "excerpt": "fabricated"},
            },
            "unknown file",
        ),
        (
            {
                "claim": "POST /upload returns 201.",
                "title": "Upload status",
                "file": "app.py",
                "evidence": {"line_start": 1, "line_end": 9, "excerpt": "fabricated"},
            },
            "line range",
        ),
        (
            {
                "claim": "POST /upload returns 201.",
                "title": "Upload status",
                "file": "app.py",
                "evidence": {
                    "line_start": 1,
                    "line_end": 2,
                    "symbol": "missing_symbol",
                    "excerpt": "fabricated",
                },
            },
            "symbol.*does not occur",
        ),
        (
            {
                "claim": "POST /upload returns 201.",
                "title": "Upload status",
                "file": "app.py",
                "evidence": {"line_start": 1, "line_end": 2, "excerpt": 201},
            },
            "excerpt must be a string or null",
        ),
    ],
)
def test_retry_excerpt_canonicalization_preserves_other_validation(repo, item, message):
    with pytest.raises(ExtractionError, match=message):
        extract_assertions(
            FakeRunner([json.dumps([item])]),
            repo,
            [repo / "app.py"],
            retry_reason="previous validation failure",
        )


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
    assertions = [
        RawAssertion(claim="a", title="t", file="f"),
        RawAssertion(claim="b", title="t", file="f"),
    ]
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
        cwd=repo,
        check=True,
        capture_output=True,
    )

    changed = code_entry("app.py", base)
    untouched = code_entry("notes.txt", base)
    web = Entry(
        title="login page",
        content="Login redirects.",
        kind=Kind.BEHAVIOR,
        source=Source(channel=Channel.WEB, locator="http://x/login", snapshot_ref="h1"),
    )

    assert drifted_code_entry_ids(repo, [changed, untouched, web]) == {changed.id}


def test_drift_detection_ignores_latest_exclude_for_existing_anchors(repo):
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    entry = code_entry("app.py", base)
    (repo / "app.py").write_text("def upload():\n    return 500\n")

    drifted = drifted_code_entry_ids(
        repo,
        [entry],
        policies={".": IngestionPolicy(exclude=("app.py",))},
    )

    assert drifted == {entry.id}


def test_drift_detection_treats_unknown_anchor_as_drifted(repo):
    ghost = code_entry("app.py", "0000000000000000000000000000000000000000")
    assert drifted_code_entry_ids(repo, [ghost]) == {ghost.id}


def test_drift_detection_treats_missing_anchor_as_drifted(repo):
    # a code entry with no snapshot_ref can never prove freshness
    anchorless = Entry(
        title="claim about app.py",
        content="app.py does things.",
        kind=Kind.BEHAVIOR,
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
    (repo / ".loreloop/repos.json").unlink()

    assert drifted_code_entry_ids(repo, [entry]) == {entry.id}


def test_json_extraction_tolerates_surrounding_prose():
    assertions = [RawAssertion(claim="a", title="t", file="f")]
    out = 'Here you go:\n[{"id": 0, "kind": "constraint"}]\nDone.'
    kinds = classify_assertions(FakeRunner([out]), assertions)
    assert kinds == [Kind.CONSTRAINT]
