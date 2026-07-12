import json
import sys

import pytest

from loreloop.cli import main
from loreloop.evidence.chain import EvidenceChain
from loreloop.report.acceptance import (
    load_run,
    record_check,
    record_command_check,
    render_report,
)


@pytest.fixture()
def workdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def write_trace(workdir, run_id="run-20260708-abc123", finished=True):
    runs = workdir / ".loreloop/runs"
    runs.mkdir(parents=True, exist_ok=True)
    events = [
        {
            "ts": "t0",
            "event": "delegation_started",
            "task": "fix upload",
            "context_entries": ["e1"],
        },
    ]
    if finished:
        events.append({"ts": "t1", "event": "delegation_finished", "output_chars": 10})
    path = runs / f"{run_id}.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    return path


def endorse_run(chain, run_id, task="fix upload", context=None, base_commit=None):
    """The chain record cmd_run appends — the acceptance authority."""
    return chain.append(
        "delegation_completed",
        {
            "run_id": run_id,
            "task": task,
            "context_entries": context or ["e1"],
            "base_commit": base_commit,
        },
    )


def test_report_accepted_when_all_checks_pass(workdir):
    trace = write_trace(workdir)
    chain = EvidenceChain.for_workdir(workdir)
    run = load_run(trace)
    endorse_run(chain, run.run_id)
    record_check(chain, run.run_id, "upload returns 201", passed=True)
    report = render_report(run, chain)
    assert "Verdict: ACCEPTED" in report
    assert "upload returns 201" in report
    assert "PASS" in report


def test_report_not_accepted_on_failure_or_no_checks(workdir):
    trace = write_trace(workdir)
    chain = EvidenceChain.for_workdir(workdir)
    run = load_run(trace)
    endorse_run(chain, run.run_id)
    assert "Verdict: NOT ACCEPTED" in render_report(run, chain)
    record_check(chain, run.run_id, "upload works", passed=False, detail="got 500")
    report = render_report(run, chain)
    assert "Verdict: NOT ACCEPTED" in report
    assert "got 500" in report


def test_latest_result_for_same_check_supersedes_an_earlier_failure(workdir):
    trace = write_trace(workdir)
    chain = EvidenceChain.for_workdir(workdir)
    run = load_run(trace)
    endorse_run(chain, run.run_id)
    record_check(chain, run.run_id, "upload works", passed=False, detail="got 500")
    record_check(chain, run.run_id, "upload works", passed=True, detail="retry passed")

    report = render_report(run, chain)

    assert "Verdict: ACCEPTED" in report
    assert "Checks (1 passed / 0 failed)" in report
    assert "got 500" not in report


def test_acceptance_check_text_rejects_empty_control_and_oversized_values(workdir):
    chain = EvidenceChain.for_workdir(workdir)

    for invalid in ("", "   ", "contains\x00nul", "x" * 4_001):
        with pytest.raises(ValueError):
            record_check(chain, "run-1", invalid, passed=True)

    assert chain.verify() == []


def test_report_rejects_forged_trace_without_chain_record(workdir):
    # H1 attack: the agent forges delegation_finished in the trace and there
    # is a passing check on the chain — but no chain-endorsed
    # delegation_completed exists, so the run cannot be ACCEPTED.
    trace = write_trace(workdir, finished=True)
    chain = EvidenceChain.for_workdir(workdir)
    run = load_run(trace)
    record_check(chain, run.run_id, "upload returns 201", passed=True)

    report = render_report(run, chain)
    assert "Verdict: NOT ACCEPTED" in report
    assert "delegation_completed" in report
    assert "trace file alone is not acceptance evidence" in report


def test_report_ignores_checks_recorded_before_completion(workdir):
    # M1 attack: the run id is visible in the trace while the agent still
    # works, so it could pre-plant a passing check before the delegation
    # completes. Checks count only when they postdate the completion record.
    trace = write_trace(workdir)
    chain = EvidenceChain.for_workdir(workdir)
    run = load_run(trace)
    record_check(chain, run.run_id, "planted before completion", passed=True)
    endorse_run(chain, run.run_id)

    report = render_report(run, chain)
    assert "Verdict: NOT ACCEPTED" in report
    assert "No acceptance checks were recorded" in report


def test_report_rejects_duplicate_completion_records(workdir):
    # M1: two completion records for one run id would let later "evidence"
    # attach to an earlier delegation. Only exactly-one-completion accepts,
    # and the first record pins the metadata.
    trace = write_trace(workdir)
    chain = EvidenceChain.for_workdir(workdir)
    run = load_run(trace)
    endorse_run(chain, run.run_id, task="first completion")
    record_check(chain, run.run_id, "ok", passed=True)
    endorse_run(chain, run.run_id, task="second completion")

    report = render_report(run, chain)
    assert "Verdict: NOT ACCEPTED" in report
    assert "cannot be attributed to a single delegation" in report
    assert "Task: first completion" in report


def test_report_prefers_chain_metadata_over_trace(workdir):
    # The trace's task line is agent-writable; the chain record wins.
    trace = write_trace(workdir)
    chain = EvidenceChain.for_workdir(workdir)
    run = load_run(trace)
    endorse_run(chain, run.run_id, task="real task from chain", context=["e1", "e2"])
    record_check(chain, run.run_id, "ok", passed=True)

    report = render_report(run, chain)
    assert "Task: real task from chain" in report
    assert "Knowledge entries injected: 2" in report


def test_base_commits_reads_new_and_legacy_formats(workdir):
    legacy_trace = write_trace(workdir, run_id="run-legacy")
    legacy = load_run(legacy_trace)
    legacy_chain = EvidenceChain.for_workdir(workdir)
    legacy_chain.append(
        "delegation_completed",
        {
            "run_id": legacy.run_id,
            "task": legacy.task,
            "context_entries": [],
            "base_commit": "abc",
        },
    )
    from loreloop.report.acceptance import evaluate_run

    assert evaluate_run(legacy, legacy_chain).base_commits == {".": "abc"}

    new_trace = workdir / ".loreloop/runs/run-new.jsonl"
    new_trace.write_text(
        json.dumps(
            {
                "event": "delegation_started",
                "task": "new",
                "context_entries": [],
                "base_commits": {".": "def", "backend": "123"},
            }
        )
        + "\n"
    )
    assert load_run(new_trace).base_commits == {".": "def", "backend": "123"}


def test_load_run_rejects_invalid_repository_name_in_base_commits(workdir):
    from loreloop.report.acceptance import RunTraceError

    trace = workdir / ".loreloop/runs/run-bad-repo.jsonl"
    trace.parent.mkdir(parents=True, exist_ok=True)
    trace.write_text(
        json.dumps(
            {
                "event": "delegation_started",
                "task": "bad",
                "context_entries": [],
                "base_commits": {"../escape": "abc"},
            }
        )
        + "\n"
    )

    with pytest.raises(RunTraceError, match="base_commits"):
        load_run(trace)


def test_report_ignores_checks_from_other_runs(workdir):
    trace = write_trace(workdir)
    chain = EvidenceChain.for_workdir(workdir)
    record_check(chain, "run-other", "unrelated", passed=True)
    report = render_report(load_run(trace), chain)
    assert "No acceptance checks were recorded" in report


def test_cli_check_and_report_flow(workdir, capsys):
    trace = write_trace(workdir)
    run_id = trace.stem
    endorse_run(EvidenceChain.for_workdir(workdir), run_id)
    assert main(["check", run_id, "login page loads", "--pass"]) == 0
    assert main(["report", run_id]) == 0
    out = capsys.readouterr().out
    assert "Verdict: ACCEPTED" in out
    assert "login page loads" in out


def test_cli_begin_keeps_work_in_current_session_and_complete_signs_it(
    workdir, monkeypatch, capsys
):
    import loreloop.cli as cli
    from loreloop.knowledge.model import Channel, Entry, Kind, Source
    from loreloop.knowledge.store import KnowledgeStore

    entry = Entry(
        title="Upload endpoint contract",
        content="POST /upload returns 201.",
        kind=Kind.INTERFACE,
        source=Source(channel=Channel.MANUAL, locator="manual:upload"),
    )
    db = workdir / ".loreloop/knowledge.db"
    db.parent.mkdir(parents=True)
    with KnowledgeStore(db) as store:
        store.add(entry)

    monkeypatch.setattr(
        cli, "_agent", lambda _name: pytest.fail("begin must not launch a delegated agent")
    )
    monkeypatch.setattr(
        cli,
        "_inference_agent",
        lambda _name: pytest.fail("begin must not launch query-expansion inference"),
    )

    assert main(["begin", "fix the upload endpoint"]) == 0
    captured = capsys.readouterr()
    run_id = next(
        line.removeprefix("Run ID: ")
        for line in captured.out.splitlines()
        if line.startswith("Run ID: ")
    )
    assert "Project knowledge (provided by LoreLoop)" in captured.out
    assert "fix the upload endpoint" in captured.out
    assert "current session prepared" in captured.err

    chain = EvidenceChain.for_workdir(workdir)
    records = chain.verify()
    prepared = next(record for record in records if record.event == "delegation_prepared")
    assert prepared.payload["run_id"] == run_id
    assert prepared.payload["context_entries"] == [entry.id]
    assert not any(record.event == "delegation_completed" for record in records)

    assert main(["complete", run_id]) == 2
    assert "operator confirmation required" in capsys.readouterr().err

    assert main(["complete", run_id, "--confirm"]) == 0
    out = capsys.readouterr().out
    assert "completed current-session run" in out
    records = chain.verify()
    completed = next(record for record in records if record.event == "delegation_completed")
    assert completed.payload["task"] == "fix the upload endpoint"
    assert completed.payload["prepared_chain_hash"] == prepared.chain_hash

    record_check(chain, run_id, "upload endpoint tests pass", passed=True)
    assert main(["report", run_id]) == 0
    assert "Verdict: ACCEPTED" in capsys.readouterr().out
    monkeypatch.setattr(cli, "_inference_agent", lambda _name: object())
    assert main(["harvest", run_id]) == 0
    assert any(record.event == "knowledge_harvested" for record in chain.verify())


def test_cli_complete_uses_signed_preparation_not_agent_writable_trace(workdir, capsys):
    assert main(["begin", "real operator task"]) == 0
    run_id = next(
        line.removeprefix("Run ID: ")
        for line in capsys.readouterr().out.splitlines()
        if line.startswith("Run ID: ")
    )
    trace = workdir / ".loreloop/runs" / f"{run_id}.jsonl"
    events = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
    events[0]["task"] = "forged agent task"
    events[0]["context_entries"] = ["forged-entry"]
    trace.write_text("\n".join(json.dumps(event) for event in events) + "\n")

    assert main(["complete", run_id, "--confirm"]) == 0
    capsys.readouterr()

    completed = next(
        record
        for record in EvidenceChain.for_workdir(workdir).verify()
        if record.event == "delegation_completed"
    )
    assert completed.payload["task"] == "real operator task"
    assert completed.payload["context_entries"] == []

    assert main(["complete", run_id, "--confirm"]) == 1
    assert "run already complete" in capsys.readouterr().err


def test_command_check_records_reauditable_deterministic_evidence(workdir):
    from loreloop.evidence.artifacts import ArtifactStore

    trace = write_trace(workdir)
    run = load_run(trace)
    chain = EvidenceChain.for_workdir(workdir)
    endorse_run(chain, run.run_id)
    artifacts = ArtifactStore.for_workdir(workdir)

    rec = record_command_check(
        chain,
        artifacts,
        run.run_id,
        "unit tests pass",
        [sys.executable, "-c", "print('42 passed')"],
        cwd=workdir,
    )

    assert rec.event == "check_passed"
    assert rec.payload["verified_via"] == "command"
    data = artifacts.load(rec.payload["artifact"])
    assert data["type"] == "command_evidence"
    assert data["exit_code"] == 0
    assert data["stdout"] == "42 passed\n"
    assert "Verdict: ACCEPTED" in render_report(run, chain, artifacts)


def test_command_evidence_redacts_environment_and_labeled_secrets(workdir, monkeypatch):
    from loreloop.evidence.artifacts import ArtifactStore

    monkeypatch.setenv("LORELOOP_TEST_API_KEY", "super-secret-value")
    monkeypatch.setenv("LORELOOP_SECOND_SECRET", "another-secret")
    chain = EvidenceChain.for_workdir(workdir)
    artifacts = ArtifactStore.for_workdir(workdir)
    rec = record_command_check(
        chain,
        artifacts,
        "run-secret",
        "redaction works",
        [
            sys.executable,
            "-c",
            (
                "import os,sys; print(os.environ['LORELOOP_TEST_API_KEY']); "
                "print('token: ' + os.environ['LORELOOP_SECOND_SECRET'], file=sys.stderr)"
            ),
        ],
        cwd=workdir,
    )

    serialized = json.dumps(artifacts.load(rec.payload["artifact"]))
    assert "super-secret-value" not in serialized
    assert "another-secret" not in serialized
    assert "<redacted>" in serialized
    assert "super-secret-value" not in json.dumps(rec.payload)


def test_cli_ingest_rejects_dirty_source_before_inference(workdir, monkeypatch, capsys):
    import subprocess

    import loreloop.cli as cli

    subprocess.run(["git", "init"], cwd=workdir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=workdir, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=workdir, check=True)
    (workdir / "app.py").write_text("value = 1\n")
    subprocess.run(["git", "add", "app.py"], cwd=workdir, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=workdir, check=True)
    (workdir / "app.py").write_text("value = 2\n")

    monkeypatch.setattr(
        cli,
        "_inference_agent",
        lambda _name: pytest.fail("dirty ingestion must stop before model inference"),
    )

    assert main(["ingest", "--from", "code", "."]) == 2
    assert "uncommitted source files" in capsys.readouterr().err


def test_cli_ingest_reports_batch_progress_to_stderr(workdir, monkeypatch, capsys):
    import subprocess

    import loreloop.cli as cli
    from loreloop.knowledge.code_reverse import CodeIngestionProgress

    subprocess.run(["git", "init"], cwd=workdir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=workdir, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=workdir, check=True)
    (workdir / "app.py").write_text("value = 1\n")
    subprocess.run(["git", "add", "app.py"], cwd=workdir, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=workdir, check=True)

    def fake_reverse_code(*_args, on_progress=None, **_kwargs):
        assert on_progress is not None
        on_progress(CodeIngestionProgress("extract", 1, 1, 1))
        on_progress(CodeIngestionProgress("classify", 1, 1, 1, 2))
        return []

    monkeypatch.setattr(cli, "_inference_agent", lambda _name: object())
    monkeypatch.setattr(cli, "reverse_code", fake_reverse_code)

    assert main(["ingest", "--from", "code", "."]) == 0

    assert capsys.readouterr().err.splitlines() == [
        "code ingestion: batch 1/1, extracting 1 file",
        "code ingestion: batch 1/1, classifying 2 assertions",
        "ingestion manifest: tracked=1, scanned=1, skipped=0",
    ]


def test_cli_command_check_does_not_invoke_a_shell(workdir, capsys):
    trace = write_trace(workdir)
    run_id = trace.stem
    endorse_run(EvidenceChain.for_workdir(workdir), run_id)
    marker = workdir / "should-not-exist"

    assert (
        main(
            [
                "check",
                run_id,
                "command is isolated",
                "--command",
                f'{sys.executable} -c "print(1)" ; touch {marker}',
            ]
        )
        == 2
    )

    assert not marker.exists()
    assert "shell operators are not supported" in capsys.readouterr().err


def test_cli_report_without_runs_errors(workdir, capsys):
    assert main(["report"]) == 2
    assert "no runs found" in capsys.readouterr().err


def test_knowledge_usage_reports_injections_and_accepted_runs(workdir, capsys):
    from loreloop.knowledge.model import Channel, Entry, Kind, Source
    from loreloop.knowledge.store import KnowledgeStore

    entry = Entry(
        title="Upload limit",
        content="Uploads are limited to 50 MiB.",
        kind=Kind.CONSTRAINT,
        source=Source(channel=Channel.MANUAL, locator="manual:upload-limit"),
    )
    db = workdir / ".loreloop/knowledge.db"
    db.parent.mkdir(parents=True)
    with KnowledgeStore(db) as store:
        store.add(entry)
    chain = EvidenceChain.for_workdir(workdir)
    chain.append(
        "delegation_completed",
        {
            "run_id": "run-a",
            "task": "a",
            "context_entries": [entry.id],
            "base_commits": {},
        },
    )
    chain.append(
        "delegation_completed",
        {
            "run_id": "run-b",
            "task": "b",
            "context_entries": [entry.id],
            "base_commits": {},
        },
    )
    chain.append(
        "knowledge_harvested",
        {
            "run_id": "run-b",
            "minted": {},
            "reversed": {},
            "review": [],
        },
    )

    assert main(["knowledge", "usage"]) == 0

    out = capsys.readouterr().out
    assert entry.id[:8] in out
    assert "2" in out
    assert "1" in out
    assert "Upload limit" in out


def test_cli_report_missing_run_id_exits_cleanly(workdir, capsys):
    assert main(["report", "run-missing"]) == 2
    err = capsys.readouterr().err
    assert "no trace found for run-missing" in err
    assert "Traceback" not in err


def test_cli_report_broken_chain_exits_cleanly(workdir, capsys):
    trace = write_trace(workdir)
    chain = EvidenceChain.for_workdir(workdir)
    run = load_run(trace)
    endorse_run(chain, run.run_id)
    record_check(chain, run.run_id, "upload returns 201", passed=True)

    path = workdir / ".loreloop/evidence.jsonl"
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    lines[0]["payload"]["task"] = "forged task"
    path.write_text("\n".join(json.dumps(line, sort_keys=True) for line in lines) + "\n")

    assert main(["report", run.run_id]) == 2
    err = capsys.readouterr().err
    assert "evidence chain broken" in err
    assert "Traceback" not in err


def test_cli_report_bad_trace_json_exits_cleanly(workdir, capsys):
    runs = workdir / ".loreloop/runs"
    runs.mkdir(parents=True)
    (runs / "run-bad.jsonl").write_text("{not json\n", encoding="utf-8")

    assert main(["report", "run-bad"]) == 2
    err = capsys.readouterr().err
    assert "invalid run trace" in err
    assert "line 1 is not JSON" in err
    assert "Traceback" not in err


def test_cli_harvest_trace_without_started_exits_cleanly(workdir, capsys):
    runs = workdir / ".loreloop/runs"
    runs.mkdir(parents=True)
    (runs / "run-no-start.jsonl").write_text(
        json.dumps({"event": "delegation_finished"}) + "\n",
        encoding="utf-8",
    )

    assert main(["harvest", "run-no-start"]) == 2
    err = capsys.readouterr().err
    assert "missing delegation_started" in err
    assert "Traceback" not in err


def test_cli_knowledge_curation_flow(workdir, capsys):
    from loreloop.knowledge.model import Channel, Entry, Kind, Source
    from loreloop.knowledge.store import KnowledgeStore

    db = workdir / ".loreloop/knowledge.db"
    db.parent.mkdir(parents=True)
    entry = Entry(
        title="Upload contract",
        content="POST /upload returns 201.",
        kind=Kind.INTERFACE,
        source=Source(channel=Channel.CODE, locator="api.py@abc"),
    )
    with KnowledgeStore(db) as store:
        store.add(entry)

    assert main(["knowledge", "list"]) == 0
    assert "[ref   ]" in capsys.readouterr().out
    assert main(["knowledge", "approve", entry.id[:8]]) == 0
    capsys.readouterr()
    assert main(["knowledge", "list"]) == 0
    assert "[strong]" in capsys.readouterr().out

    # the approval is endorsed on the evidence chain, not just the DB,
    # and bound to the entry's content digest
    from loreloop.knowledge.endorsement import entry_digest

    records = EvidenceChain.for_workdir(workdir).verify()
    assert records[-1].event == "curation_changed"
    assert records[-1].payload | {"entry": None} == {
        "entry_id": entry.id,
        "curation": "approved",
        "entry_digest": entry_digest(entry),
        "entry": None,
    }
    assert records[-1].payload["entry"]["content"] == entry.content
    assert records[-1].payload["entry"]["source"]["locator"] == entry.source.locator


def test_cli_knowledge_export_markdown(workdir, capsys):
    from loreloop.knowledge.model import Channel, Entry, Kind, Source
    from loreloop.knowledge.store import KnowledgeStore

    db = workdir / ".loreloop/knowledge.db"
    db.parent.mkdir(parents=True)
    entry = Entry(
        title="Upload contract",
        content="POST /upload returns 201.",
        kind=Kind.INTERFACE,
        source=Source(channel=Channel.CODE, locator="api.py@abc", snapshot_ref="abc"),
    )
    with KnowledgeStore(db) as store:
        store.add(entry)

    assert main(["knowledge", "export"]) == 0
    out = capsys.readouterr().out
    assert "# loreloop knowledge export" in out
    assert "Upload contract" in out
    assert "POST /upload returns 201." in out
    assert "api.py@abc" in out

    target = workdir / ".loreloop/exports/knowledge.md"
    assert main(["knowledge", "export", "--output", str(target)]) == 0
    assert "exported 1 entries" in capsys.readouterr().out
    assert "Snapshot: `abc`" in target.read_text(encoding="utf-8")


def test_cli_curation_rejects_missing_or_ambiguous_prefix(workdir, capsys):
    from loreloop.knowledge.store import KnowledgeStore

    (workdir / ".loreloop").mkdir()
    KnowledgeStore(workdir / ".loreloop/knowledge.db").close()

    assert main(["knowledge", "approve", "deadbeef"]) == 2
    assert "no entry matches" in capsys.readouterr().err
    assert main(["knowledge", "approve"]) == 2
    assert EvidenceChain.for_workdir(workdir).verify() == []


def test_cli_invalid_curation_transition_exits_cleanly(workdir, capsys):
    from loreloop.knowledge.model import Channel, Entry, Kind, Source
    from loreloop.knowledge.store import KnowledgeStore

    db = workdir / ".loreloop/knowledge.db"
    db.parent.mkdir(parents=True)
    entry = Entry(
        title="T",
        content="C",
        kind=Kind.INTERFACE,
        source=Source(channel=Channel.CODE, locator="api.py@abc"),
    )
    with KnowledgeStore(db) as store:
        store.add(entry)

    assert main(["knowledge", "reject", entry.id[:8]]) == 0
    capsys.readouterr()
    assert main(["knowledge", "reject", entry.id[:8]]) == 2
    err = capsys.readouterr().err
    assert "invalid curation transition" in err
    assert "Traceback" not in err
    # no second endorsement for the refused transition
    records = EvidenceChain.for_workdir(workdir).verify()
    assert len([r for r in records if r.event == "curation_changed"]) == 1


def test_cli_reports_invalid_sqlite_enum_without_traceback(workdir, capsys):
    from loreloop.knowledge.model import Channel, Entry, Kind, Source
    from loreloop.knowledge.store import KnowledgeStore

    db = workdir / ".loreloop/knowledge.db"
    db.parent.mkdir(parents=True)
    entry = Entry(
        title="Corrupt projection",
        content="The cache contains an invalid enum.",
        kind=Kind.CONSTRAINT,
        source=Source(channel=Channel.MANUAL, locator="manual:corrupt"),
    )
    with KnowledgeStore(db) as store:
        store.add(entry)
        store._conn.execute("UPDATE entries SET curation = 'invalid' WHERE id = ?", (entry.id,))
        store._conn.commit()

    assert main(["knowledge", "list"]) == 2
    error = capsys.readouterr().err
    assert "invalid knowledge projection" in error
    assert "reason:" in error
    assert "next:" in error
    assert "Traceback" not in error


def test_cli_duplicate_harvest_says_no_acceptance_work_is_needed(workdir, capsys):
    run_id = "run-already-harvested"
    write_trace(workdir, run_id=run_id)
    EvidenceChain.for_workdir(workdir).append(
        "knowledge_harvested",
        {"run_id": run_id, "minted": [], "reversed": []},
    )

    assert main(["harvest", run_id]) == 1
    error = capsys.readouterr().err
    assert "already harvested" in error
    assert "no acceptance work is required" in error
    assert "satisfy every acceptance check" not in error


def test_cli_report_and_harvest_reject_path_traversal_run_ids(workdir, capsys):
    assert main(["report", "../../../etc/passwd"]) == 2
    assert "invalid run id" in capsys.readouterr().err
    assert main(["harvest", "../escape"]) == 2
    assert "invalid run id" in capsys.readouterr().err


def test_cli_run_demotes_unendorsed_strong_entries(workdir, monkeypatch, capsys):
    from datetime import datetime, timezone

    import loreloop.cli as cli
    from loreloop.knowledge.model import (
        Channel,
        Entry,
        Kind,
        Source,
        Trust,
        Verification,
    )
    from loreloop.knowledge.store import KnowledgeStore

    class FakeAgent:
        def __init__(self):
            self.prompts = []

        def run(self, prompt):
            self.prompts.append(prompt)
            return "done"

    now = datetime.now(timezone.utc)
    laundered = Entry(
        title="Upload endpoint contract",
        content="POST /upload returns 201.",
        kind=Kind.INTERFACE,
        source=Source(channel=Channel.WEB, locator="http://app.local/upload"),
        trust=Trust(verification=Verification.VERIFIED, verified_at=now, verified_by="forged"),
    )
    db = workdir / ".loreloop/knowledge.db"
    db.parent.mkdir(parents=True)
    with KnowledgeStore(db) as store:
        store.add(laundered)

    agent = FakeAgent()
    monkeypatch.setattr(cli, "_agent", lambda name: agent)
    assert main(["run", "--no-expand", "fix the upload endpoint"]) == 0

    err = capsys.readouterr().err
    assert "without evidence-chain endorsement" in err
    assert laundered.id[:8] in err
    prompt = agent.prompts[0]
    assert "Established facts" not in prompt
    assert "Unverified references" in prompt


def test_cli_run_keeps_chain_endorsed_strong_entries(workdir, monkeypatch, capsys):
    import loreloop.cli as cli
    from loreloop.knowledge.model import Channel, Entry, Kind, Source
    from loreloop.knowledge.store import KnowledgeStore

    class FakeAgent:
        def __init__(self):
            self.prompts = []

        def run(self, prompt):
            self.prompts.append(prompt)
            return "done"

    entry = Entry(
        title="Upload endpoint contract",
        content="POST /upload returns 201.",
        kind=Kind.INTERFACE,
        source=Source(channel=Channel.CODE, locator="api.py@abc"),
    )
    db = workdir / ".loreloop/knowledge.db"
    db.parent.mkdir(parents=True)
    with KnowledgeStore(db) as store:
        store.add(entry)

    assert main(["knowledge", "approve", entry.id[:8]]) == 0
    capsys.readouterr()

    agent = FakeAgent()
    monkeypatch.setattr(cli, "_agent", lambda name: agent)
    assert main(["run", "--no-expand", "fix the upload endpoint"]) == 0

    assert "without evidence-chain endorsement" not in capsys.readouterr().err
    assert "Established facts" in agent.prompts[0]


def test_cli_run_keeps_chain_approved_entry_after_db_curation_flip(workdir, monkeypatch, capsys):
    import loreloop.cli as cli
    from loreloop.knowledge.model import Channel, Entry, Kind, Source
    from loreloop.knowledge.store import KnowledgeStore

    class FakeAgent:
        def __init__(self):
            self.prompts = []

        def run(self, prompt):
            self.prompts.append(prompt)
            return "done"

    entry = Entry(
        title="Upload endpoint contract",
        content="POST /upload returns 201.",
        kind=Kind.INTERFACE,
        source=Source(channel=Channel.CODE, locator="api.py@abc"),
    )
    db = workdir / ".loreloop/knowledge.db"
    db.parent.mkdir(parents=True)
    with KnowledgeStore(db) as store:
        store.add(entry)
    assert main(["knowledge", "approve", entry.id[:8]]) == 0
    capsys.readouterr()

    with KnowledgeStore(db) as store:
        store._conn.execute("UPDATE entries SET curation = 'draft' WHERE id = ?", (entry.id,))
        store._conn.commit()
        assert not store.get(entry.id).is_strong_evidence()

    agent = FakeAgent()
    monkeypatch.setattr(cli, "_agent", lambda name: agent)
    assert main(["run", "--no-expand", "fix the upload endpoint"]) == 0
    assert "Established facts" in agent.prompts[0]
    assert "POST /upload returns 201." in agent.prompts[0]

    assert main(["knowledge", "list"]) == 0
    line = next(li for li in capsys.readouterr().out.splitlines() if entry.id[:8] in li)
    assert "[strong]" in line
    assert "[chain-backed" in line


def test_cli_run_keeps_chain_approved_entry_after_db_rejection(workdir, monkeypatch, capsys):
    import loreloop.cli as cli
    from loreloop.knowledge.model import Channel, Entry, Kind, Source
    from loreloop.knowledge.store import KnowledgeStore

    class FakeAgent:
        def __init__(self):
            self.prompts = []

        def run(self, prompt):
            self.prompts.append(prompt)
            return "done"

    entry = Entry(
        title="Upload endpoint contract",
        content="POST /upload returns 201.",
        kind=Kind.INTERFACE,
        source=Source(channel=Channel.CODE, locator="api.py@abc"),
    )
    db = workdir / ".loreloop/knowledge.db"
    db.parent.mkdir(parents=True)
    with KnowledgeStore(db) as store:
        store.add(entry)
    assert main(["knowledge", "approve", entry.id[:8]]) == 0
    capsys.readouterr()

    with KnowledgeStore(db) as store:
        store._conn.execute("UPDATE entries SET curation = 'rejected' WHERE id = ?", (entry.id,))
        store._conn.commit()
        assert store.get(entry.id).trust.curation == "rejected"

    agent = FakeAgent()
    monkeypatch.setattr(cli, "_agent", lambda name: agent)
    assert main(["run", "--no-expand", "fix the upload endpoint"]) == 0

    assert "Established facts" in agent.prompts[0]
    assert "POST /upload returns 201." in agent.prompts[0]


def test_cli_run_keeps_chain_approved_entry_after_db_only_supersede_link(
    workdir, monkeypatch, capsys
):
    import loreloop.cli as cli
    from loreloop.knowledge.model import Channel, Entry, Kind, Link, LinkType, Source
    from loreloop.knowledge.store import KnowledgeStore

    class FakeAgent:
        def __init__(self):
            self.prompts = []

        def run(self, prompt):
            self.prompts.append(prompt)
            return "done"

    old = Entry(
        title="Upload endpoint contract",
        content="POST /upload returns 201.",
        kind=Kind.INTERFACE,
        source=Source(channel=Channel.CODE, locator="api.py@abc"),
    )
    new = Entry(
        title="Replacement upload endpoint contract",
        content="POST /upload returns 202.",
        kind=Kind.INTERFACE,
        source=Source(channel=Channel.CODE, locator="api.py@def"),
    )
    db = workdir / ".loreloop/knowledge.db"
    db.parent.mkdir(parents=True)
    with KnowledgeStore(db) as store:
        store.add(old)
        store.add(new)
    assert main(["knowledge", "approve", old.id[:8]]) == 0
    capsys.readouterr()

    with KnowledgeStore(db) as store:
        store.add_link(Link(from_id=new.id, to_id=old.id, link_type=LinkType.SUPERSEDES))
        assert old.id in store.superseded_ids()

    agent = FakeAgent()
    monkeypatch.setattr(cli, "_agent", lambda name: agent)
    assert main(["run", "--no-expand", "fix the upload endpoint"]) == 0

    assert "Established facts" in agent.prompts[0]
    assert "POST /upload returns 201." in agent.prompts[0]


def test_cli_run_does_not_claim_drifted_chain_backed_entry_as_established(
    workdir, monkeypatch, capsys
):
    import subprocess

    import loreloop.cli as cli
    from loreloop.knowledge.model import Channel, Entry, Kind, Source
    from loreloop.knowledge.store import KnowledgeStore

    class FakeAgent:
        def __init__(self):
            self.prompts = []

        def run(self, prompt):
            self.prompts.append(prompt)
            return "done"

    def git(*args):
        subprocess.run(["git", *args], cwd=workdir, check=True, capture_output=True)

    git("init")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (workdir / "api.py").write_text("def upload(): return 201\n")
    git("add", "api.py")
    git("commit", "-m", "base")
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=workdir, check=True, capture_output=True, text=True
    ).stdout.strip()

    entry = Entry(
        title="Upload endpoint contract",
        content="POST /upload returns 201.",
        kind=Kind.INTERFACE,
        source=Source(channel=Channel.CODE, locator=f"api.py@{base}", snapshot_ref=base),
    )
    db = workdir / ".loreloop/knowledge.db"
    db.parent.mkdir(parents=True)
    with KnowledgeStore(db) as store:
        store.add(entry)
    assert main(["knowledge", "approve", entry.id[:8]]) == 0
    capsys.readouterr()

    with KnowledgeStore(db) as store:
        store._conn.execute("UPDATE entries SET curation = 'draft' WHERE id = ?", (entry.id,))
        store._conn.commit()

    (workdir / "api.py").write_text("def upload(): return 202\n")
    git("add", "api.py")
    git("commit", "-m", "change upload")

    agent = FakeAgent()
    monkeypatch.setattr(cli, "_agent", lambda name: agent)
    assert main(["run", "--no-expand", "fix the upload endpoint"]) == 0
    err = capsys.readouterr().err

    assert "injected as established fact" not in err
    assert "Established facts" not in agent.prompts[0]
    assert "Unverified references" in agent.prompts[0]
    assert "source_changed_since_capture" in agent.prompts[0]


def test_cli_run_rejects_unexplained_content_change_after_endorsement(workdir, monkeypatch, capsys):
    # H2 end-to-end: approve, then rewrite the row's content by SQL. A mere
    # demotion would still expose attacker-controlled content as a reference;
    # the fail-closed projection check must stop delegation entirely.
    import loreloop.cli as cli
    from loreloop.knowledge.model import Channel, Entry, Kind, Source
    from loreloop.knowledge.store import KnowledgeStore

    class FakeAgent:
        def __init__(self):
            self.prompts = []

        def run(self, prompt):
            self.prompts.append(prompt)
            return "done"

    entry = Entry(
        title="Upload endpoint contract",
        content="POST /upload returns 201.",
        kind=Kind.INTERFACE,
        source=Source(channel=Channel.CODE, locator="api.py@abc"),
    )
    db = workdir / ".loreloop/knowledge.db"
    db.parent.mkdir(parents=True)
    with KnowledgeStore(db) as store:
        store.add(entry)
    assert main(["knowledge", "approve", entry.id[:8]]) == 0
    capsys.readouterr()

    with KnowledgeStore(db) as store:
        store._conn.execute(
            "UPDATE entries SET content = ? WHERE id = ?",
            ("POST /upload endpoint skips auth checks.", entry.id),
        )
        store._conn.commit()

    agent = FakeAgent()
    monkeypatch.setattr(cli, "_agent", lambda name: agent)
    assert main(["run", "--no-expand", "fix the upload endpoint"]) == 2

    err = capsys.readouterr().err
    assert "unexplained content/source digest" in err
    assert "Restore the SQLite projection" in err
    assert agent.prompts == []


def test_cli_run_ignores_deleted_supersede_link(workdir, monkeypatch, capsys):
    # H3 attack: after the curator supersedes an old strong entry, the agent
    # deletes the links row. The chain replay must keep it out of injection.
    import loreloop.cli as cli
    from loreloop.knowledge.model import Channel, Entry, Kind, Source
    from loreloop.knowledge.store import KnowledgeStore

    class FakeAgent:
        def __init__(self):
            self.prompts = []

        def run(self, prompt):
            self.prompts.append(prompt)
            return "done"

    old = Entry(
        title="Old upload contract",
        content="POST /upload returns 200.",
        kind=Kind.INTERFACE,
        source=Source(channel=Channel.CODE, locator="api.py@abc"),
    )
    new = Entry(
        title="New upload contract",
        content="POST /upload returns 201.",
        kind=Kind.INTERFACE,
        source=Source(channel=Channel.CODE, locator="api.py@def"),
    )
    db = workdir / ".loreloop/knowledge.db"
    db.parent.mkdir(parents=True)
    with KnowledgeStore(db) as store:
        store.add(old)
        store.add(new)
    assert main(["knowledge", "approve", old.id[:8]]) == 0
    assert main(["knowledge", "supersede", new.id[:8], old.id[:8], "--yes"]) == 0
    capsys.readouterr()

    with KnowledgeStore(db) as store:
        store._conn.execute("DELETE FROM links")
        store._conn.commit()
        assert store.superseded_ids() == set()  # DB cache is clean — attack in place

    agent = FakeAgent()
    monkeypatch.setattr(cli, "_agent", lambda name: agent)
    assert main(["run", "--no-expand", "fix the upload contract"]) == 0

    assert "POST /upload returns 200." not in agent.prompts[0]

    # the list view also stays honest
    assert main(["knowledge", "list"]) == 0
    out = capsys.readouterr().out
    line = next(li for li in out.splitlines() if old.id[:8] in li)
    assert "[superseded]" in line


def test_cli_run_ignores_db_resurrection_of_rejected_entry(workdir, monkeypatch, capsys):
    # Round-5 H2: a verified entry keeps its digest endorsement after the
    # human rejects it (rejection is curation, not contradiction). If the
    # agent flips the DB curation back to draft, the digest check alone would
    # re-inject it as strong — the chain-rejected replay must keep it out.
    from datetime import datetime, timezone

    import loreloop.cli as cli
    from loreloop.knowledge.endorsement import entry_digest
    from loreloop.knowledge.model import Channel, Entry, Kind, Source
    from loreloop.knowledge.store import KnowledgeStore

    class FakeAgent:
        def __init__(self):
            self.prompts = []

        def run(self, prompt):
            self.prompts.append(prompt)
            return "done"

    entry = Entry(
        title="Upload endpoint contract",
        content="POST /upload returns 201.",
        kind=Kind.INTERFACE,
        source=Source(channel=Channel.WEB, locator="http://app.local/upload"),
    )
    db = workdir / ".loreloop/knowledge.db"
    db.parent.mkdir(parents=True)
    with KnowledgeStore(db) as store:
        store.add(entry)
    chain = EvidenceChain.for_workdir(workdir)
    chain.append("entry_verified", {"entry_id": entry.id, "entry_digest": entry_digest(entry)})
    assert main(["knowledge", "reject", entry.id[:8]]) == 0
    capsys.readouterr()

    now = datetime.now(timezone.utc).isoformat()
    with KnowledgeStore(db) as store:
        store._conn.execute(
            "UPDATE entries SET curation = 'draft', verification = 'verified',"
            " verified_at = ?, verified_by = 'run-x' WHERE id = ?",
            (now, entry.id),
        )
        store._conn.commit()

    agent = FakeAgent()
    monkeypatch.setattr(cli, "_agent", lambda name: agent)
    assert main(["run", "--no-expand", "fix the upload endpoint"]) == 0
    assert "POST /upload returns 201." not in agent.prompts[0]

    # the list view stays honest too
    assert main(["knowledge", "list"]) == 0
    line = next(li for li in capsys.readouterr().out.splitlines() if entry.id[:8] in li)
    assert "[rejected]" in line
    assert "[strong]" not in line


def test_cli_run_expands_query_and_degrades_on_bad_expansion(workdir, monkeypatch, capsys):
    import loreloop.cli as cli
    from loreloop.knowledge.model import Channel, Entry, Kind, Source
    from loreloop.knowledge.store import KnowledgeStore

    class FakeAgent:
        def __init__(self, output):
            self.output = output
            self.prompts = []

        def run(self, prompt):
            self.prompts.append(prompt)
            return self.output

    entry = Entry(
        title="Upload endpoint contract",
        content="POST /upload returns 201.",
        kind=Kind.INTERFACE,
        source=Source(channel=Channel.CODE, locator="api.py@abc"),
    )
    db = workdir / ".loreloop/knowledge.db"
    db.parent.mkdir(parents=True)
    with KnowledgeStore(db) as store:
        store.add(entry)

    inference = FakeAgent('["upload", "endpoint", "限流"]')
    delegation = FakeAgent("done")
    monkeypatch.setattr(cli, "_inference_agent", lambda name: inference)
    monkeypatch.setattr(cli, "_agent", lambda name: delegation)
    assert main(["run", "给上传接口加限流"]) == 0
    assert len(inference.prompts) == 1
    assert len(delegation.prompts) == 1
    assert "expanding a search query" in inference.prompts[0]
    # bridged via expansion terms; expansion text itself is not in the prompt
    assert "POST /upload returns 201." in delegation.prompts[0]
    assert "expanding a search query" not in delegation.prompts[0]
    capsys.readouterr()

    bad_inference = FakeAgent("not json at all")
    fallback_delegation = FakeAgent("done")
    monkeypatch.setattr(cli, "_inference_agent", lambda name: bad_inference)
    monkeypatch.setattr(cli, "_agent", lambda name: fallback_delegation)
    assert main(["run", "fix the upload endpoint"]) == 0
    assert "query expansion failed" in capsys.readouterr().err
    assert "POST /upload returns 201." in fallback_delegation.prompts[0]


def test_cli_knowledge_list_flags_unendorsed_strong_as_ref(workdir, capsys):
    # Round-4 F1: the DB is agent-writable, so a strong bit UPDATEd straight
    # into it must not render as [strong] in the list view either — list
    # informs curation decisions and applies the same endorsement rules as
    # injection.
    from datetime import datetime, timezone

    from loreloop.knowledge.model import (
        Channel,
        Entry,
        Kind,
        Source,
        Trust,
        Verification,
    )
    from loreloop.knowledge.store import KnowledgeStore

    laundered = Entry(
        title="Laundered fact",
        content="Agent says this is verified.",
        kind=Kind.BEHAVIOR,
        source=Source(channel=Channel.WEB, locator="http://app.local/x"),
        trust=Trust(
            verification=Verification.VERIFIED,
            verified_at=datetime.now(timezone.utc),
            verified_by="forged",
        ),
    )
    db = workdir / ".loreloop/knowledge.db"
    db.parent.mkdir(parents=True)
    with KnowledgeStore(db) as store:
        store.add(laundered)

    assert main(["knowledge", "list"]) == 0
    out = capsys.readouterr().out
    line = next(li for li in out.splitlines() if laundered.id[:8] in li)
    assert "[ref   ]" in line
    assert "[unendorsed" in line
    assert "[strong]" not in line


def test_report_flags_missing_and_tampered_artifacts(workdir):
    from loreloop.evidence.artifacts import ArtifactStore
    from loreloop.webexplore.browser import Observation

    trace = write_trace(workdir)
    chain = EvidenceChain.for_workdir(workdir)
    run = load_run(trace)
    endorse_run(chain, run.run_id)
    artifacts = ArtifactStore.for_workdir(workdir)
    obs = Observation(url="http://a", title="T", text="ok")
    sha, path = artifacts.save_observation(obs)
    chain.append(
        "check_passed",
        {
            "run_id": run.run_id,
            "check": "page ok",
            "artifact": sha,
            "url": obs.url,
            "page_snapshot": obs.snapshot_hash,
        },
    )

    assert "Verdict: ACCEPTED" in render_report(run, chain, artifacts)

    path.write_text('{"tampered": true}', encoding="utf-8")
    report = render_report(run, chain, artifacts)
    assert "Verdict: NOT ACCEPTED" in report
    assert "Evidence integrity failures" in report

    path.unlink()
    report = render_report(run, chain, artifacts)
    assert "Verdict: NOT ACCEPTED" in report
    assert "file is missing" in report


def test_report_flags_artifact_without_url_snapshot_pin(workdir):
    # M2: an artifact the chain cannot tie to a page proves nothing — a
    # signed check carrying an artifact but no url/page_snapshot pin would
    # let any hash-valid observation back an ACCEPTED verdict.
    from loreloop.evidence.artifacts import ArtifactStore
    from loreloop.webexplore.browser import Observation

    trace = write_trace(workdir)
    chain = EvidenceChain.for_workdir(workdir)
    run = load_run(trace)
    endorse_run(chain, run.run_id)
    artifacts = ArtifactStore.for_workdir(workdir)
    sha, _ = artifacts.save_observation(Observation(url="http://a", title="T", text="ok"))
    chain.append("check_passed", {"run_id": run.run_id, "check": "page ok", "artifact": sha})

    report = render_report(run, chain, artifacts)
    assert "Verdict: NOT ACCEPTED" in report
    assert "no url/page_snapshot pin" in report


def test_report_flags_artifact_swapped_for_a_different_observation(workdir):
    from loreloop.evidence.artifacts import ArtifactStore
    from loreloop.webexplore.browser import Observation

    trace = write_trace(workdir)
    chain = EvidenceChain.for_workdir(workdir)
    run = load_run(trace)
    endorse_run(chain, run.run_id)
    artifacts = ArtifactStore.for_workdir(workdir)
    real = Observation(url="http://app.local/upload", title="Upload", text="Max 50MB.")
    sha, _ = artifacts.save_observation(real)
    chain.append(
        "check_passed",
        {
            "run_id": run.run_id,
            "check": "page ok",
            "artifact": sha,
            "url": real.url,
            "page_snapshot": real.snapshot_hash,
        },
    )
    assert "Verdict: ACCEPTED" in render_report(run, chain, artifacts)

    # swap: another hash-valid artifact of a DIFFERENT page replaces the ref
    other = Observation(url="http://evil.local/", title="Other", text="whatever")
    other_sha, _ = artifacts.save_observation(other)
    swapped = EvidenceChain.for_workdir(workdir)
    swapped.append(
        "check_passed",
        {
            "run_id": run.run_id,
            "check": "swapped",
            "artifact": other_sha,
            "url": real.url,
            "page_snapshot": real.snapshot_hash,
        },
    )
    report = render_report(run, swapped, artifacts)
    assert "Verdict: NOT ACCEPTED" in report
    assert "does not match the chain record" in report


def test_report_audits_script_and_trace_artifacts(workdir):
    from loreloop.evidence.artifacts import ArtifactStore
    from loreloop.webexplore.actions import parse_action_script
    from loreloop.webexplore.browser import Observation

    trace = write_trace(workdir)
    chain = EvidenceChain.for_workdir(workdir)
    run = load_run(trace)
    endorse_run(chain, run.run_id)
    artifacts = ArtifactStore.for_workdir(workdir)
    obs = Observation(url="http://app.local/products", title="Products", text="Filtered")
    obs_sha, _ = artifacts.save_observation(obs)
    script = parse_action_script(
        {"version": 1, "base": "http://app.local", "steps": [{"goto": "/products"}]}
    )
    script_sha, _ = artifacts.save_json(
        {
            "type": "interaction_script",
            "script_digest": script.digest,
            "script": script.to_json(),
        }
    )
    trace_sha, trace_path = artifacts.save_json(
        {
            "type": "interaction_trace",
            "script_digest": script.digest,
            "status": "completed",
            "steps_completed": 1,
            "steps": [],
            "final_url": obs.url,
            "final_snapshot": obs.snapshot_hash,
        }
    )
    chain.append(
        "check_passed",
        {
            "run_id": run.run_id,
            "check": "filtered products",
            "artifact": obs_sha,
            "url": obs.url,
            "page_snapshot": obs.snapshot_hash,
            "script_digest": script.digest,
            "script_artifact": script_sha,
            "trace_artifact": trace_sha,
        },
    )
    assert "Verdict: ACCEPTED" in render_report(run, chain, artifacts)

    trace_path.unlink()
    report = render_report(run, chain, artifacts)
    assert "Verdict: NOT ACCEPTED" in report
    assert "trace artifact referenced on the chain but the file is missing" in report


def test_report_notes_passed_checks_without_artifacts(workdir):
    trace = write_trace(workdir)
    chain = EvidenceChain.for_workdir(workdir)
    run = load_run(trace)
    endorse_run(chain, run.run_id)
    rec = record_check(chain, run.run_id, "looks right on my screen", passed=True)
    assert rec.payload["judge"] == "operator"
    report = render_report(run, chain)
    assert "operator" in report
    assert "cannot be re-audited" in report


def test_report_escapes_markdown_table_breakers(workdir):
    trace = write_trace(workdir)
    chain = EvidenceChain.for_workdir(workdir)
    run = load_run(trace)
    record_check(chain, run.run_id, "cell | breaker", passed=False, detail="line1\nline2 | x")
    report = render_report(run, chain)
    row = next(line for line in report.splitlines() if "cell" in line and line.startswith("|"))
    assert "cell \\| breaker" in row
    assert "\n" not in row


def test_cli_supersede_links_and_hides_old_entry(workdir, capsys):
    from loreloop.knowledge.endorsement import entry_digest
    from loreloop.knowledge.model import Channel, Entry, Kind, Source
    from loreloop.knowledge.store import KnowledgeStore

    db = workdir / ".loreloop/knowledge.db"
    db.parent.mkdir(parents=True)
    old = Entry(
        title="Old upload contract",
        content="POST /upload returns 200.",
        kind=Kind.INTERFACE,
        source=Source(channel=Channel.CODE, locator="api.py@abc"),
    )
    new = Entry(
        title="New upload contract",
        content="POST /upload returns 201.",
        kind=Kind.INTERFACE,
        source=Source(channel=Channel.CODE, locator="api.py@def"),
    )
    with KnowledgeStore(db) as store:
        store.add(old)
        store.add(new)

    assert main(["knowledge", "supersede", new.id[:8], old.id[:8], "--yes"]) == 0
    assert "superseded: Old upload contract" in capsys.readouterr().out

    # supersession silences an entry — endorsed on the chain like curation
    records = EvidenceChain.for_workdir(workdir).verify()
    assert records[-1].event == "entry_superseded"
    payload = records[-1].payload
    assert payload["new_id"] == new.id
    assert payload["old_id"] == old.id
    assert payload["new_entry"]["id"] == new.id
    assert payload["old_entry"]["id"] == old.id
    assert payload["new_entry_digest"] == entry_digest(new)
    assert payload["old_entry_digest"] == entry_digest(old)

    assert main(["knowledge", "list"]) == 0
    out = capsys.readouterr().out
    assert "[superseded]" in out

    with KnowledgeStore(db) as store:
        assert {e.id for e in store.list_active()} == {new.id}


def test_cli_supersede_rejects_ambiguous_or_missing_prefix(workdir, capsys):
    from loreloop.knowledge.store import KnowledgeStore

    (workdir / ".loreloop").mkdir()
    KnowledgeStore(workdir / ".loreloop/knowledge.db").close()
    assert main(["knowledge", "supersede", "deadbeef", "cafebabe"]) == 2
    assert "no entry matches" in capsys.readouterr().err
    assert main(["knowledge", "supersede", "deadbeef"]) == 2


def test_cli_supersede_requires_confirmation_and_rejects_retired_endpoints(workdir, capsys):
    from loreloop.knowledge.model import Channel, Entry, Kind, Source
    from loreloop.knowledge.store import KnowledgeStore

    db = workdir / ".loreloop/knowledge.db"
    db.parent.mkdir(parents=True)
    entries = [
        Entry(
            title=title,
            content=f"{title} content",
            kind=Kind.CONSTRAINT,
            source=Source(channel=Channel.MANUAL, locator=f"manual:{title}"),
        )
        for title in ("A", "B", "C")
    ]
    with KnowledgeStore(db) as store:
        for entry in entries:
            store.add(entry)
    a, b, c = entries

    assert main(["knowledge", "supersede", a.id[:8], b.id[:8]]) == 2
    assert "--yes" in capsys.readouterr().err
    assert main(["knowledge", "supersede", a.id[:8], b.id[:8], "--yes"]) == 0
    capsys.readouterr()

    assert main(["knowledge", "supersede", b.id[:8], c.id[:8], "--yes"]) == 2
    assert "already retired" in capsys.readouterr().err
    assert main(["knowledge", "supersede", c.id[:8], b.id[:8], "--yes"]) == 2
    assert "already retired" in capsys.readouterr().err


def test_cli_rejected_entry_cannot_supersede_and_is_not_active(workdir, capsys):
    from loreloop.knowledge.model import Channel, Entry, Kind, Source
    from loreloop.knowledge.store import KnowledgeStore

    db = workdir / ".loreloop/knowledge.db"
    db.parent.mkdir(parents=True)
    replacement = Entry(
        title="Rejected replacement",
        content="Rejected replacement content",
        kind=Kind.CONSTRAINT,
        source=Source(channel=Channel.MANUAL, locator="manual:replacement"),
    )
    old = Entry(
        title="Active old knowledge",
        content="Active old content",
        kind=Kind.CONSTRAINT,
        source=Source(channel=Channel.MANUAL, locator="manual:old"),
    )
    with KnowledgeStore(db) as store:
        store.add(replacement)
        store.add(old)

    assert main(["knowledge", "reject", replacement.id[:8]]) == 0
    capsys.readouterr()
    assert main(["knowledge", "supersede", replacement.id[:8], old.id[:8], "--yes"]) == 2
    assert "already retired" in capsys.readouterr().err

    assert main(["knowledge", "list", "--active"]) == 0
    output = capsys.readouterr().out
    assert "Active old knowledge" in output
    assert "Rejected replacement" not in output


def test_cli_status_filters_use_chain_curation_and_disclose_cache_mismatch(workdir, capsys):
    from loreloop.knowledge.model import Channel, Curation, Entry, Kind, Source
    from loreloop.knowledge.store import KnowledgeStore

    db = workdir / ".loreloop/knowledge.db"
    db.parent.mkdir(parents=True)
    entry = Entry(
        title="Approved policy",
        content="The policy is approved.",
        kind=Kind.CONSTRAINT,
        source=Source(channel=Channel.MANUAL, locator="manual:policy"),
    )
    with KnowledgeStore(db) as store:
        store.add(entry)
    assert main(["knowledge", "approve", entry.id[:8]]) == 0
    capsys.readouterr()
    with KnowledgeStore(db) as store:
        store._conn.execute(
            "UPDATE entries SET curation = ? WHERE id = ?", (Curation.DRAFT.value, entry.id)
        )
        store._conn.commit()

    assert main(["knowledge", "list", "--status", "approved"]) == 0
    assert "Approved policy" in capsys.readouterr().out
    assert main(["knowledge", "review", "--status", "draft"]) == 0
    assert "Approved policy" not in capsys.readouterr().out
    assert main(["knowledge", "show", entry.id[:8]]) == 0
    shown = capsys.readouterr().out
    assert "Effective curation: approved" in shown
    assert "Stored curation: draft (cache mismatch)" in shown


def test_cli_curation_transition_uses_chain_state_not_sqlite_cache(workdir, capsys):
    from loreloop.knowledge.model import Channel, Curation, Entry, Kind, Source
    from loreloop.knowledge.store import KnowledgeStore

    db = workdir / ".loreloop/knowledge.db"
    db.parent.mkdir(parents=True)
    entry = Entry(
        title="Recoverable policy",
        content="The policy can be recovered.",
        kind=Kind.CONSTRAINT,
        source=Source(channel=Channel.MANUAL, locator="manual:recoverable"),
    )
    with KnowledgeStore(db) as store:
        store.add(entry)
    assert main(["knowledge", "reject", entry.id[:8]]) == 0
    capsys.readouterr()
    with KnowledgeStore(db) as store:
        store._conn.execute(
            "UPDATE entries SET curation = ? WHERE id = ?", (Curation.DRAFT.value, entry.id)
        )
        store._conn.commit()

    assert main(["knowledge", "reopen", entry.id[:8]]) == 0
    assert "reopened: Recoverable policy" in capsys.readouterr().out
    records = EvidenceChain.for_workdir(workdir).verify()
    assert records[-1].payload["curation"] == Curation.DRAFT.value
    with KnowledgeStore(db) as store:
        assert store.get(entry.id).trust.curation is Curation.DRAFT


def test_cli_reopen_restores_rejected_entry_deleted_from_sqlite(workdir, capsys):
    from loreloop.knowledge.model import Channel, Curation, Entry, Kind, Source, Verification
    from loreloop.knowledge.store import KnowledgeStore

    db = workdir / ".loreloop/knowledge.db"
    db.parent.mkdir(parents=True)
    entry = Entry(
        title="Deleted rejected policy",
        content="The signed policy remains recoverable.",
        kind=Kind.CONSTRAINT,
        source=Source(channel=Channel.MANUAL, locator="manual:deleted-rejected"),
    )
    with KnowledgeStore(db) as store:
        store.add(entry)

    assert main(["knowledge", "reject", entry.id[:8]]) == 0
    capsys.readouterr()
    with KnowledgeStore(db) as store:
        store._conn.execute("DELETE FROM entries WHERE id = ?", (entry.id,))
        store._conn.commit()

    assert main(["knowledge", "reopen", entry.id[:8]]) == 0
    assert "reopened: Deleted rejected policy" in capsys.readouterr().out
    with KnowledgeStore(db) as store:
        restored = store.get(entry.id)
        assert restored.content == entry.content
        assert restored.trust.curation is Curation.DRAFT
        assert restored.trust.verification is Verification.UNVERIFIED


def test_cli_run_pins_per_repository_ingestion_policies(workdir, monkeypatch, capsys):
    import subprocess

    import loreloop.cli as cli
    from loreloop.knowledge.repos import save_repos

    class FakeAgent:
        def run(self, _prompt):
            return "done"

    subprocess.run(["git", "init"], cwd=workdir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=workdir, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=workdir, check=True)
    (workdir / "app.py").write_text("value = 1\n")
    subprocess.run(["git", "add", "app.py"], cwd=workdir, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=workdir, check=True)
    backend = workdir.parent / "backend-policy"
    backend.mkdir()
    subprocess.run(["git", "init"], cwd=backend, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=backend, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=backend, check=True)
    (backend / "api.py").write_text("value = 2\n")
    subprocess.run(["git", "add", "api.py"], cwd=backend, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=backend, check=True)
    save_repos(workdir, {"backend": backend})
    chain = EvidenceChain.for_workdir(workdir)
    chain.append(
        "code_ingestion_policy_set",
        {
            "repo_name": ".",
            "policy": {"include": ["*.avsc"], "exclude": [], "max_file_bytes": 123_456},
        },
    )
    chain.append(
        "code_ingestion_policy_set",
        {
            "repo_name": "backend",
            "policy": {
                "include": ["*.proto"],
                "exclude": ["vendor/**"],
                "max_file_bytes": 654_321,
            },
        },
    )
    monkeypatch.setattr(cli, "_agent", lambda _name: FakeAgent())

    assert main(["run", "--no-expand", "inspect upload contract"]) == 0
    capsys.readouterr()

    completed = next(record for record in chain.verify() if record.event == "delegation_completed")
    assert completed.payload["repository_roots"] == {
        ".": str(workdir.resolve()),
        "backend": str(backend.resolve()),
    }
    assert completed.payload["ingestion_policies"] == {
        ".": {"include": ["*.avsc"], "exclude": [], "max_file_bytes": 123_456},
        "backend": {
            "include": ["*.proto"],
            "exclude": ["vendor/**"],
            "max_file_bytes": 654_321,
        },
    }


def test_cli_unsupersede_restores_entry_and_records_recovery(workdir, capsys):
    from loreloop.knowledge.model import Channel, Entry, Kind, Source
    from loreloop.knowledge.store import KnowledgeStore

    db = workdir / ".loreloop/knowledge.db"
    db.parent.mkdir(parents=True)
    old = Entry(
        title="Old",
        content="Old content",
        kind=Kind.CONSTRAINT,
        source=Source(channel=Channel.MANUAL, locator="manual:old"),
    )
    new = Entry(
        title="New",
        content="New content",
        kind=Kind.CONSTRAINT,
        source=Source(channel=Channel.MANUAL, locator="manual:new"),
    )
    with KnowledgeStore(db) as store:
        store.add(old)
        store.add(new)

    assert main(["knowledge", "supersede", new.id[:8], old.id[:8], "--yes"]) == 0
    capsys.readouterr()
    with KnowledgeStore(db) as store:
        store._conn.execute("DELETE FROM links WHERE to_id = ?", (old.id,))
        store._conn.execute("DELETE FROM entries WHERE id = ?", (old.id,))
        store._conn.commit()
    assert main(["knowledge", "unsupersede", new.id[:8], old.id[:8], "--yes"]) == 0
    assert "restored" in capsys.readouterr().out

    records = EvidenceChain.for_workdir(workdir).verify()
    assert records[-1].event == "entry_supersession_reverted"
    with KnowledgeStore(db) as store:
        assert {entry.id for entry in store.list_active()} == {old.id, new.id}


def test_cli_superseded_approved_entry_displays_as_reference(workdir, capsys):
    from loreloop.knowledge.model import Channel, Entry, Kind, Source
    from loreloop.knowledge.store import KnowledgeStore

    db = workdir / ".loreloop/knowledge.db"
    db.parent.mkdir(parents=True)
    old = Entry(
        title="Old",
        content="Old content",
        kind=Kind.CONSTRAINT,
        source=Source(channel=Channel.MANUAL, locator="manual:old"),
    )
    new = Entry(
        title="New",
        content="New content",
        kind=Kind.CONSTRAINT,
        source=Source(channel=Channel.MANUAL, locator="manual:new"),
    )
    with KnowledgeStore(db) as store:
        store.add(old)
        store.add(new)
    assert main(["knowledge", "approve", old.id[:8]]) == 0
    capsys.readouterr()
    assert main(["knowledge", "supersede", new.id[:8], old.id[:8], "--yes"]) == 0
    capsys.readouterr()

    assert main(["knowledge", "list"]) == 0
    line = next(line for line in capsys.readouterr().out.splitlines() if old.id[:8] in line)
    assert "[ref   ]" in line
    assert "[superseded]" in line


def test_cli_ingest_chain_failure_leaves_refreshed_projection_untouched(
    workdir, monkeypatch, capsys
):
    import subprocess

    import loreloop.cli as cli
    from loreloop.knowledge.model import Channel, Entry, Kind, Source
    from loreloop.knowledge.store import KnowledgeStore

    subprocess.run(["git", "init"], cwd=workdir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=workdir, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=workdir, check=True)
    (workdir / "app.py").write_text("value = 1\n")
    subprocess.run(["git", "add", "app.py"], cwd=workdir, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=workdir, check=True)
    old = Entry(
        title="Old title",
        content="The value is one.",
        kind=Kind.CONSTRAINT,
        source=Source(channel=Channel.CODE, locator="app.py@old", snapshot_ref="old"),
    )
    db = workdir / ".loreloop/knowledge.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    with KnowledgeStore(db) as store:
        store.add(old)
    assert main(["knowledge", "approve", old.id[:8]]) == 0
    capsys.readouterr()
    refreshed = Entry(
        title="New title",
        content=old.content,
        kind=Kind.CONSTRAINT,
        source=Source(channel=Channel.CODE, locator="app.py@new", snapshot_ref="new"),
    )
    monkeypatch.setattr(cli, "_inference_agent", lambda _name: object())
    monkeypatch.setattr(cli, "reverse_code", lambda *_args, **_kwargs: [refreshed])
    monkeypatch.setattr(
        cli,
        "record_reingested",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )

    assert main(["ingest", "--from", "code", "."]) == 2
    with KnowledgeStore(db) as store:
        stored = store.get(old.id)
    assert stored.title == "Old title"
    assert stored.source.locator == "app.py@old"


def test_cli_ingest_applies_include_and_exclude_to_dirty_check(workdir, monkeypatch, capsys):
    import subprocess

    import loreloop.cli as cli

    subprocess.run(["git", "init"], cwd=workdir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=workdir, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=workdir, check=True)
    (workdir / "app.py").write_text("value = 1\n")
    (workdir / "README.md").write_text("baseline\n")
    (workdir / "upload.avsc").write_text('{"limit": 5}\n')
    subprocess.run(["git", "add", "-A"], cwd=workdir, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=workdir, check=True)
    (workdir / "README.md").write_text("uncommitted docs\n")
    (workdir / "upload.avsc").write_text('{"limit": 8}\n')
    monkeypatch.setattr(cli, "_inference_agent", lambda _name: object())
    monkeypatch.setattr(cli, "reverse_code", lambda *_args, **_kwargs: [])

    assert main(["ingest", "--from", "code", ".", "--include", "*.avsc"]) == 2
    assert "upload.avsc" in capsys.readouterr().err

    (workdir / "upload.avsc").write_text('{"limit": 5}\n')
    assert main(["ingest", "--from", "code", ".", "--exclude", "README.md"]) == 0
    capsys.readouterr()
    policy = next(
        record
        for record in EvidenceChain.for_workdir(workdir).verify()
        if record.event == "code_ingestion_policy_set"
    )
    assert policy.payload["repo_name"] == "."
    assert policy.payload["policy"]["exclude"] == ["README.md"]


def test_cli_show_review_and_list_filters_expose_complete_evidence(workdir, capsys):
    from loreloop.knowledge.model import Channel, Entry, Kind, Source
    from loreloop.knowledge.store import KnowledgeStore

    db = workdir / ".loreloop/knowledge.db"
    db.parent.mkdir(parents=True)
    entry = Entry(
        title="Upload ceiling",
        content="Uploads are limited to 50 MiB.",
        kind=Kind.CONSTRAINT,
        source=Source(
            channel=Channel.CODE,
            locator="api.py@abc",
            snapshot_ref="abc",
            symbol="MAX_UPLOAD_MIB",
            line_start=7,
            line_end=7,
            excerpt="MAX_UPLOAD_MIB = 50",
        ),
    )
    other = Entry(
        title="Manual note",
        content="An operator note.",
        kind=Kind.REQUIREMENT,
        source=Source(channel=Channel.MANUAL, locator="operator:note"),
    )
    with KnowledgeStore(db) as store:
        store.add(entry)
        store.add(other)

    assert main(["knowledge", "show", entry.id[:8]]) == 0
    shown = capsys.readouterr().out
    assert "Uploads are limited to 50 MiB." in shown
    assert "Source locator: api.py@abc" in shown
    assert "Source lines: 7-7" in shown
    assert "Source symbol: MAX_UPLOAD_MIB" in shown
    assert "Source excerpt: MAX_UPLOAD_MIB = 50" in shown

    assert main(["knowledge", "review", "--channel", "code", "--limit", "1"]) == 0
    reviewed = capsys.readouterr().out
    assert "Upload ceiling" in reviewed
    assert "Manual note" not in reviewed
    assert "loreloop knowledge approve" in reviewed

    assert main(["knowledge", "list", "--kind", "requirement", "--offset", "0"]) == 0
    listed = capsys.readouterr().out
    assert "Manual note" in listed
    assert "Upload ceiling" not in listed


def test_cli_stale_review_recommends_replacement_not_reapproval(workdir, capsys):
    import subprocess

    from loreloop.knowledge.model import Channel, Entry, Kind, Source
    from loreloop.knowledge.store import KnowledgeStore

    subprocess.run(["git", "init"], cwd=workdir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=workdir, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=workdir, check=True)
    (workdir / "app.py").write_text("LIMIT = 5\n")
    subprocess.run(["git", "add", "app.py"], cwd=workdir, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=workdir, check=True)
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=workdir,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    entry = Entry(
        title="Old limit",
        content="The limit is 5.",
        kind=Kind.CONSTRAINT,
        source=Source(channel=Channel.CODE, locator=f"app.py@{base}", snapshot_ref=base),
    )
    db = workdir / ".loreloop/knowledge.db"
    db.parent.mkdir(parents=True)
    with KnowledgeStore(db) as store:
        store.add(entry)
    (workdir / "app.py").write_text("LIMIT = 8\n")
    subprocess.run(["git", "add", "app.py"], cwd=workdir, check=True)
    subprocess.run(["git", "commit", "-m", "raise limit"], cwd=workdir, check=True)

    assert main(["knowledge", "review", "--stale"]) == 0
    output = capsys.readouterr().out
    assert "knowledge approve" not in output
    assert "knowledge supersede" in output
    assert "knowledge reject" in output


def test_cli_report_uses_structured_verdict_for_next_step(workdir, capsys):
    trace = write_trace(workdir, run_id="run-verdict-text")
    chain = EvidenceChain.for_workdir(workdir)
    endorse_run(chain, trace.stem, task="Document the phrase Verdict: ACCEPTED")
    record_check(chain, trace.stem, "still failing", passed=False)

    assert main(["report", trace.stem]) == 0

    output = capsys.readouterr().out
    assert "Verdict: NOT ACCEPTED" in output
    assert "Next: loreloop harvest" not in output
    assert "satisfy the missing checks" in output


def test_cli_list_stale_flags_drifted_code_anchors(workdir, capsys):
    import subprocess

    from loreloop.knowledge.model import Channel, Entry, Kind, Source
    from loreloop.knowledge.store import KnowledgeStore

    def git(*args):
        subprocess.run(["git", *args], cwd=workdir, check=True, capture_output=True)

    git("init")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (workdir / "api.py").write_text("def upload(): return 201\n")
    git("add", "api.py")
    git("commit", "-m", "base")
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=workdir, capture_output=True, text=True, check=True
    ).stdout.strip()

    drifting = Entry(
        title="Upload contract",
        content="POST /upload returns 201.",
        kind=Kind.INTERFACE,
        source=Source(channel=Channel.CODE, locator=f"api.py@{base}", snapshot_ref=base),
    )
    db = workdir / ".loreloop/knowledge.db"
    db.parent.mkdir(parents=True)
    with KnowledgeStore(db) as store:
        store.add(drifting)

    assert main(["knowledge", "list", "--stale"]) == 0
    assert "no stale entries" in capsys.readouterr().out

    (workdir / "api.py").write_text("def upload(): return 202\n")
    git("add", "api.py")
    git("commit", "-m", "change")

    assert main(["knowledge", "list", "--stale"]) == 0
    out = capsys.readouterr().out
    assert drifting.id[:8] in out
    assert "[stale: source changed since capture]" in out


def test_cli_verify_rejects_empty_assertion_before_browser(workdir, capsys):
    # Exits 2 with a clean message, before any playwright import or navigation
    assert main(["verify", "run-1", "http://app.local", "contains:"]) == 2
    assert "invalid expectation" in capsys.readouterr().err


def test_cli_verify_rejects_bad_script_before_browser(workdir, capsys):
    script = workdir / "actions.json"
    script.write_text(json.dumps({"version": 1, "base": "http://app.local", "steps": []}))

    assert (
        main(["verify", "run-1", "http://app.local", "contains:ok", "--script", str(script)]) == 2
    )
    assert "invalid action script" in capsys.readouterr().err


def test_cli_verify_rejects_allow_writes_without_script(workdir, capsys):
    assert main(["verify", "run-1", "http://app.local", "contains:ok", "--allow-writes"]) == 2
    assert "--allow-writes requires --script" in capsys.readouterr().err


def test_cli_init_creates_evidence_key(workdir, monkeypatch, capsys):
    import shutil as _shutil

    from loreloop.evidence.chain import key_path_for

    monkeypatch.setattr(_shutil, "which", lambda name: None)
    assert main(["init"]) == 0
    assert key_path_for(workdir).exists()
    out = capsys.readouterr().out
    assert "local trust: ready (managed automatically)" in out
    assert str(key_path_for(workdir)) not in out
    assert "HMAC" not in out


def test_cli_init_rejects_in_project_key_location_without_traceback(workdir, monkeypatch, capsys):
    blocked = workdir / "not-a-directory"
    blocked.write_text("x")
    monkeypatch.setenv("LORELOOP_KEY_DIR", str(blocked / "keys"))

    assert main(["init", "--no-skill"]) == 2

    err = capsys.readouterr().err
    assert "must be outside the project tree" in err
    assert "LORELOOP_KEY_DIR" in err
    assert "Traceback" not in err


def test_cli_init_reports_unwritable_external_key_location_without_traceback(
    workdir, monkeypatch, capsys
):
    blocked = workdir.parent / f"{workdir.name}-not-a-directory"
    blocked.write_text("x")
    monkeypatch.setenv("LORELOOP_KEY_DIR", str(blocked / "keys"))

    assert main(["init", "--no-skill"]) == 2

    err = capsys.readouterr().err
    assert "cannot initialize local trust" in err
    assert "Traceback" not in err


def test_cli_doctor_reports_preflight_checks(workdir, monkeypatch, capsys):
    import shutil as _shutil

    monkeypatch.setattr(_shutil, "which", lambda name: f"/usr/bin/{name}")

    assert main(["doctor"]) == 0

    out = capsys.readouterr().out
    assert "Python" in out
    assert "Git" in out
    assert "coding agent" in out
    assert "local trust directory" in out
    assert "ready" in out.lower()


def test_cli_doctor_reports_invalid_key_boundary_as_failed_check(workdir, monkeypatch, capsys):
    import shutil as _shutil

    monkeypatch.setattr(_shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setenv("LORELOOP_KEY_DIR", str(workdir / ".operator-keys"))

    assert main(["doctor"]) == 1

    captured = capsys.readouterr()
    assert "FAIL  local trust directory" in captured.out
    assert "must be outside the project tree" in captured.out
    assert "NOT READY" in captured.out
    assert captured.err == ""


def test_cli_codex_status_reports_enabled_native_plugin(monkeypatch, capsys):
    import shutil
    import subprocess

    def fake_run(argv, **_kwargs):
        command = argv[1:]
        if command == ["plugin", "marketplace", "list", "--json"]:
            payload = {"marketplaces": [{"name": "loreloop", "root": "/market"}]}
        else:
            assert command == [
                "plugin",
                "list",
                "--json",
                "--available",
                "--marketplace",
                "loreloop",
            ]
            payload = {
                "installed": [
                    {
                        "pluginId": "loreloop@loreloop",
                        "version": "0.1.0",
                        "installed": True,
                        "enabled": True,
                    }
                ],
                "available": [],
            }
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/codex")
    monkeypatch.setattr(subprocess, "run", fake_run)

    assert main(["codex", "status"]) == 0
    out = capsys.readouterr().out
    assert "Codex integration: ready" in out
    assert "loreloop@loreloop 0.1.0" in out
    assert "new Codex thread" in out


def test_cli_codex_install_uses_native_marketplace_commands(monkeypatch, capsys):
    import shutil
    import subprocess

    calls = []

    def fake_run(argv, **_kwargs):
        command = argv[1:]
        calls.append(command)
        payload = {"marketplaces": []} if command[-3:] == ["list", "--json"] else {}
        if command == ["plugin", "marketplace", "list", "--json"]:
            payload = {"marketplaces": []}
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/codex")
    monkeypatch.setattr(subprocess, "run", fake_run)

    assert main(["codex", "install", "--ref", "v0.1.0"]) == 0
    assert calls == [
        ["plugin", "marketplace", "list", "--json"],
        [
            "plugin",
            "marketplace",
            "add",
            "zhangguiping-xydt/loreloop",
            "--ref",
            "v0.1.0",
            "--json",
        ],
        ["plugin", "add", "loreloop@loreloop", "--json"],
    ]
    out = capsys.readouterr().out
    assert "Added Codex marketplace" in out
    assert "Installed and enabled Codex plugin" in out


def test_cli_codex_install_preserves_existing_marketplace_source(monkeypatch, capsys):
    import shutil
    import subprocess

    calls = []

    def fake_run(argv, **_kwargs):
        command = argv[1:]
        calls.append(command)
        payload = (
            {
                "marketplaces": [
                    {
                        "name": "loreloop",
                        "root": "/existing/source",
                        "marketplaceSource": {
                            "sourceType": "local",
                            "source": "/existing/source",
                        },
                    }
                ]
            }
            if command == ["plugin", "marketplace", "list", "--json"]
            else {}
        )
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/codex")
    monkeypatch.setattr(subprocess, "run", fake_run)

    assert main(["codex", "install", "--source", "/different/source"]) == 0
    assert calls == [
        ["plugin", "marketplace", "list", "--json"],
        ["plugin", "add", "loreloop@loreloop", "--json"],
    ]
    assert "Using existing Codex marketplace" in capsys.readouterr().out


def test_cli_codex_failure_is_recoverable_without_traceback(monkeypatch, capsys):
    import shutil

    monkeypatch.setattr(shutil, "which", lambda _name: None)

    assert main(["codex", "status"]) == 2
    err = capsys.readouterr().err
    assert "Codex integration is unavailable" in err
    assert "loreloop codex install" in err
    assert "Traceback" not in err


def test_cli_opencode_install_status_and_uninstall_preserve_modified_files(
    tmp_path, monkeypatch, capsys
):
    config = tmp_path / "opencode"
    monkeypatch.setenv("OPENCODE_CONFIG_DIR", str(config))

    assert main(["opencode", "status"]) == 1
    assert "not ready" in capsys.readouterr().out

    assert main(["opencode", "install"]) == 0
    skill = config / "skills/loreloop/SKILL.md"
    command = config / "commands/loreloop.md"
    assert skill.is_file()
    assert command.is_file()
    assert "/loreloop <request>" in capsys.readouterr().out

    assert main(["opencode", "status"]) == 0
    assert "OpenCode integration: ready" in capsys.readouterr().out

    command.write_text("user customization\n", encoding="utf-8")
    assert main(["opencode", "uninstall"]) == 2
    err = capsys.readouterr().err
    assert "refusing to remove modified" in err
    assert skill.exists()
    assert command.read_text(encoding="utf-8") == "user customization\n"


def test_cli_opencode_install_rejects_symlink(tmp_path, monkeypatch, capsys):
    config = tmp_path / "opencode"
    target = tmp_path / "target.md"
    target.write_text("user file\n", encoding="utf-8")
    command = config / "commands/loreloop.md"
    command.parent.mkdir(parents=True)
    command.symlink_to(target)
    monkeypatch.setenv("OPENCODE_CONFIG_DIR", str(config))

    assert main(["opencode", "install"]) == 2
    assert "refusing symlinked OpenCode integration file" in capsys.readouterr().err
    assert target.read_text(encoding="utf-8") == "user file\n"
    assert not (config / "skills/loreloop/SKILL.md").exists()


def test_cli_claude_install_status_and_uninstall_use_native_commands(monkeypatch, capsys):
    import shutil
    import subprocess

    calls = []
    installed = False
    marketplace = False

    def fake_run(argv, **_kwargs):
        nonlocal installed, marketplace
        command = argv[1:]
        calls.append(command)
        if command == ["plugin", "marketplace", "list", "--json"]:
            payload = [{"name": "loreloop", "source": "local"}] if marketplace else []
        elif command == ["plugin", "list", "--json"]:
            payload = (
                [{"id": "loreloop@loreloop", "version": "0.1.0", "enabled": True}]
                if installed
                else []
            )
        elif command[:3] == ["plugin", "marketplace", "add"]:
            marketplace = True
            payload = {}
        elif command[:2] == ["plugin", "install"]:
            installed = True
            payload = {}
        elif command[:2] == ["plugin", "uninstall"]:
            installed = False
            payload = {}
        elif command[:3] == ["plugin", "marketplace", "remove"]:
            marketplace = False
            payload = {}
        else:
            raise AssertionError(command)
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/claude")
    monkeypatch.setattr(subprocess, "run", fake_run)

    assert main(["claude", "install", "--source", "/checkout/loreloop"]) == 0
    assert calls[:3] == [
        ["plugin", "marketplace", "list", "--json"],
        [
            "plugin",
            "marketplace",
            "add",
            "/checkout/loreloop",
            "--scope",
            "user",
        ],
        ["plugin", "install", "loreloop@loreloop", "--scope", "user"],
    ]
    assert "Installed Claude Code plugin" in capsys.readouterr().out

    assert main(["claude", "status"]) == 0
    assert "Claude Code integration: ready" in capsys.readouterr().out

    assert main(["claude", "uninstall", "--remove-marketplace"]) == 0
    assert ["plugin", "uninstall", "loreloop@loreloop", "--scope", "user"] in calls
    assert ["plugin", "marketplace", "remove", "loreloop"] in calls


def test_cli_comind_install_status_and_uninstall_use_native_commands(monkeypatch, capsys):
    import shutil
    import subprocess

    calls = []
    installed = False
    marketplace = False

    def fake_run(argv, **_kwargs):
        nonlocal installed, marketplace
        command = argv[1:]
        calls.append(command)
        if command == ["plugin", "marketplace", "list", "--json"]:
            payload = [{"name": "loreloop", "source": "local"}] if marketplace else []
        elif command == ["plugin", "list", "--json"]:
            payload = (
                [{"id": "loreloop@loreloop", "version": "0.1.0", "enabled": True}]
                if installed
                else []
            )
        elif command[:3] == ["plugin", "marketplace", "add"]:
            marketplace = True
            payload = {}
        elif command[:2] == ["plugin", "install"]:
            installed = True
            payload = {}
        elif command[:2] == ["plugin", "uninstall"]:
            installed = False
            payload = {}
        elif command[:3] == ["plugin", "marketplace", "remove"]:
            marketplace = False
            payload = {}
        else:
            raise AssertionError(command)
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/co-mind")
    monkeypatch.setattr(subprocess, "run", fake_run)

    assert main(["comind", "install", "--source", "/checkout/loreloop"]) == 0
    assert calls[:3] == [
        ["plugin", "marketplace", "list", "--json"],
        [
            "plugin",
            "marketplace",
            "add",
            "/checkout/loreloop",
            "--scope",
            "user",
        ],
        ["plugin", "install", "loreloop@loreloop", "--scope", "user"],
    ]
    assert "Installed co-mind plugin" in capsys.readouterr().out

    assert main(["comind", "status"]) == 0
    assert "co-mind integration: ready" in capsys.readouterr().out

    assert main(["comind", "uninstall", "--remove-marketplace"]) == 0
    assert ["plugin", "uninstall", "loreloop@loreloop", "--scope", "user"] in calls
    assert ["plugin", "marketplace", "remove", "loreloop"] in calls


def test_cli_comind_install_preserves_existing_marketplace(monkeypatch, capsys):
    import shutil
    import subprocess

    calls = []

    def fake_run(argv, **_kwargs):
        command = argv[1:]
        calls.append(command)
        payload = (
            [{"name": "loreloop", "source": "/existing/source"}]
            if command == ["plugin", "marketplace", "list", "--json"]
            else {}
        )
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/co-mind")
    monkeypatch.setattr(subprocess, "run", fake_run)

    assert main(["comind", "install", "--source", "/different/source"]) == 0
    assert calls == [
        ["plugin", "marketplace", "list", "--json"],
        ["plugin", "install", "loreloop@loreloop", "--scope", "user"],
    ]
    assert "source preserved" in capsys.readouterr().out


def test_cli_init_remembers_custom_trust_location_for_future_sessions(workdir, monkeypatch, capsys):
    import shutil as _shutil

    from loreloop.evidence.chain import EvidenceChain

    custom = workdir.parent / "operator-keys"
    monkeypatch.setenv("LORELOOP_KEY_DIR", str(custom))
    monkeypatch.setattr(_shutil, "which", lambda _name: None)

    assert main(["init", "--no-skill"]) == 0
    EvidenceChain.for_workdir(workdir).append("history", {})
    capsys.readouterr()

    monkeypatch.delenv("LORELOOP_KEY_DIR")
    assert main(["trust", "status"]) == 0
    out = capsys.readouterr().out
    assert "Project trust: ready" in out
    assert "saved project registration" in out
    assert main(["knowledge", "list", "--limit", "1"]) == 0


def test_cli_existing_history_without_credential_never_creates_replacement(
    workdir, monkeypatch, capsys
):
    import shutil as _shutil

    from loreloop.evidence.chain import EvidenceChain, key_path_for

    custom = workdir.parent / "missing-operator-keys"
    monkeypatch.setenv("LORELOOP_KEY_DIR", str(custom))
    monkeypatch.setattr(_shutil, "which", lambda _name: None)
    assert main(["init", "--no-skill"]) == 0
    chain = EvidenceChain.for_workdir(workdir)
    chain.append("history", {})
    key = key_path_for(workdir)
    key.unlink()
    capsys.readouterr()

    monkeypatch.delenv("LORELOOP_KEY_DIR")
    assert main(["knowledge", "list", "--limit", "1"]) == 2
    err = capsys.readouterr().err
    assert "local project trust is unavailable" in err
    assert "No replacement trust was created" in err
    assert "HMAC" not in err
    assert "record 0" not in err
    assert ".loreloop" not in err
    assert not key.exists()


def test_cli_trust_recover_replaces_wrong_location_without_archiving_history(
    workdir, monkeypatch, capsys
):
    import shutil as _shutil
    from pathlib import Path

    from loreloop.evidence.chain import EvidenceChain, key_path_for
    from loreloop.paths import trust_locations_file

    home = workdir.parent / "home"
    monkeypatch.setattr(Path, "home", lambda: home)
    custom = workdir.parent / "original-keys"
    monkeypatch.setenv("LORELOOP_KEY_DIR", str(custom))
    monkeypatch.setattr(_shutil, "which", lambda _name: None)
    assert main(["init", "--no-skill"]) == 0
    EvidenceChain.for_workdir(workdir).append("history", {})
    capsys.readouterr()

    trust_locations_file().unlink()
    monkeypatch.delenv("LORELOOP_KEY_DIR")
    wrong = key_path_for(workdir)
    wrong.parent.mkdir(parents=True)
    wrong.write_bytes(b"w" * 32)

    assert main(["trust", "status"]) == 1
    assert "wrong local trust" in capsys.readouterr().out
    assert main(["trust", "recover", "--from", str(custom)]) == 0
    assert "Project trust: recovered" in capsys.readouterr().out
    assert main(["trust", "status"]) == 0
    assert "Project trust: ready" in capsys.readouterr().out
    assert (workdir / ".loreloop/evidence.jsonl").exists()


def test_cli_trust_reset_requires_confirmation_and_archives_state(workdir, capsys):
    (workdir / ".loreloop").mkdir()
    (workdir / ".loreloop/marker").write_text("history", encoding="utf-8")

    assert main(["trust", "reset"]) == 2
    assert "explicit confirmation required" in capsys.readouterr().err
    assert main(["trust", "reset", "--confirm"]) == 0
    out = capsys.readouterr().out
    assert "Project trust archived" in out
    assert not (workdir / ".loreloop").exists()
    archive = next(workdir.glob(".loreloop.archived-*"))
    assert (archive / "marker").read_text(encoding="utf-8") == "history"


def test_cli_trust_registry_failure_is_clean(workdir, monkeypatch, capsys):
    registry = workdir.parent / "trust-locations.json"
    registry.write_text("not-json", encoding="utf-8")
    monkeypatch.setenv("LORELOOP_TRUST_REGISTRY", str(registry))

    assert main(["trust", "status"]) == 2
    err = capsys.readouterr().err
    assert "trust failed" in err
    assert "cannot read trust-location registry" in err
    assert "Traceback" not in err


def test_cli_surfaces_legacy_key_error_cleanly(workdir, capsys):
    legacy = workdir / ".loreloop/evidence.key"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"k" * 32)
    write_trace(workdir)

    assert main(["report"]) == 2
    err = capsys.readouterr().err
    assert "legacy evidence key" in err
    assert "Traceback" not in err


def test_cli_init_creates_store_and_installs_skill(workdir, monkeypatch, capsys):
    import shutil as _shutil
    import subprocess

    subprocess.run(["git", "init"], cwd=workdir, check=True, capture_output=True)
    monkeypatch.setattr(
        _shutil, "which", lambda name: "/usr/bin/claude" if name == "claude" else None
    )

    assert main(["init", "--skill"]) == 0
    out = capsys.readouterr().out
    assert "initialized .loreloop/" in out
    assert (workdir / ".loreloop/knowledge.db").exists()
    assert ".loreloop/" in (workdir / ".gitignore").read_text()

    skill = workdir / ".claude/skills/loreloop/SKILL.md"
    assert skill.exists()
    text = skill.read_text()
    assert 'Run `loreloop begin "<task>"`' in text
    assert "Do not use `loreloop run` for normal interactive work" in text
    assert "loreloop trust recover --from <directory>" in text
    assert "recommend moving/deleting `.loreloop` manually" in text
    assert "specific, explicit instruction" in text
    assert "name: loreloop" in text

    # idempotent: second run must not duplicate the gitignore line
    assert main(["init", "--skill"]) == 0
    assert (workdir / ".gitignore").read_text().count(".loreloop/") == 1


def test_cli_init_respects_no_skill_and_missing_agents(workdir, monkeypatch, capsys):
    import shutil as _shutil

    monkeypatch.setattr(
        _shutil, "which", lambda name: "/usr/bin/claude" if name == "claude" else None
    )
    assert main(["init", "--no-skill"]) == 0
    assert "skipped skill installation" in capsys.readouterr().out
    assert not (workdir / ".claude").exists()

    monkeypatch.setattr(_shutil, "which", lambda name: None)
    assert main(["init"]) == 0
    assert "skill installation skipped" in capsys.readouterr().out


def test_cli_init_installs_codex_companion_skill(workdir, monkeypatch, capsys):
    import shutil as _shutil

    monkeypatch.setattr(
        _shutil, "which", lambda name: "/usr/bin/codex" if name == "codex" else None
    )

    assert main(["init", "--skill"]) == 0

    skill = workdir / ".agents/skills/loreloop/SKILL.md"
    assert skill.exists()
    text = skill.read_text(encoding="utf-8")
    assert 'Run `loreloop begin "<task>"`' in text
    assert "Keep the user in this host coding-agent session" in text
    assert "loreloop trust recover --from <directory>" in text
    assert "installed companion skill for Codex" in capsys.readouterr().out


def test_cli_init_installs_shared_skills_and_opencode_command_once(workdir, monkeypatch, capsys):
    import shutil as _shutil

    monkeypatch.setattr(_shutil, "which", lambda name: f"/usr/bin/{name}")

    assert main(["init", "--skill"]) == 0

    assert (workdir / ".claude/skills/loreloop/SKILL.md").is_file()
    assert (workdir / ".agents/skills/loreloop/SKILL.md").is_file()
    assert (workdir / ".opencode/commands/loreloop.md").is_file()
    out = capsys.readouterr().out
    assert "installed companion skill for Claude/co-mind" in out
    assert "installed companion skill for Codex/OpenCode" in out
    assert out.count(".claude/skills/loreloop/SKILL.md") == 1
    assert out.count(".agents/skills/loreloop/SKILL.md") == 1
    assert "installed OpenCode command" in out


def test_cli_ingest_web_requires_playwright(workdir):
    try:
        import playwright  # noqa: F401

        pytest.skip("playwright installed; error path not reachable")
    except ImportError:
        pass
    assert main(["ingest", "--from", "web", "http://localhost:3000"]) == 2
