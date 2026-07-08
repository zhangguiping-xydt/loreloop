import json

import pytest

from knowhelm.cli import main
from knowhelm.evidence.chain import EvidenceChain
from knowhelm.report.acceptance import load_run, record_check, render_report


@pytest.fixture()
def workdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def write_trace(workdir, run_id="run-20260708-abc123", finished=True):
    runs = workdir / ".knowhelm/runs"
    runs.mkdir(parents=True, exist_ok=True)
    events = [
        {"ts": "t0", "event": "delegation_started", "task": "fix upload", "context_entries": ["e1"]},
    ]
    if finished:
        events.append({"ts": "t1", "event": "delegation_finished", "output_chars": 10})
    path = runs / f"{run_id}.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    return path


def test_report_accepted_when_all_checks_pass(workdir):
    trace = write_trace(workdir)
    chain = EvidenceChain.for_workdir(workdir)
    run = load_run(trace)
    record_check(chain, run.run_id, "upload returns 201", passed=True)
    report = render_report(run, chain)
    assert "Verdict: ACCEPTED" in report
    assert "upload returns 201" in report
    assert "PASS" in report


def test_report_not_accepted_on_failure_or_no_checks(workdir):
    trace = write_trace(workdir)
    chain = EvidenceChain.for_workdir(workdir)
    run = load_run(trace)
    assert "Verdict: NOT ACCEPTED" in render_report(run, chain)
    record_check(chain, run.run_id, "upload works", passed=False, detail="got 500")
    report = render_report(run, chain)
    assert "Verdict: NOT ACCEPTED" in report
    assert "got 500" in report


def test_report_ignores_checks_from_other_runs(workdir):
    trace = write_trace(workdir)
    chain = EvidenceChain.for_workdir(workdir)
    record_check(chain, "run-other", "unrelated", passed=True)
    report = render_report(load_run(trace), chain)
    assert "No acceptance checks were recorded" in report


def test_cli_check_and_report_flow(workdir, capsys):
    trace = write_trace(workdir)
    run_id = trace.stem
    assert main(["check", run_id, "login page loads", "--pass"]) == 0
    assert main(["report", run_id]) == 0
    out = capsys.readouterr().out
    assert "Verdict: ACCEPTED" in out
    assert "login page loads" in out


def test_cli_report_without_runs_errors(workdir, capsys):
    assert main(["report"]) == 2
    assert "no runs found" in capsys.readouterr().err


def test_cli_knowledge_curation_flow(workdir, capsys):
    from knowhelm.knowledge.model import Channel, Entry, Kind, Source
    from knowhelm.knowledge.store import KnowledgeStore

    db = workdir / ".knowhelm/knowledge.db"
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
    assert main(["knowledge", "approve", entry.id]) == 0
    capsys.readouterr()
    assert main(["knowledge", "list"]) == 0
    assert "[strong]" in capsys.readouterr().out


def test_cli_ingest_web_requires_playwright(workdir):
    try:
        import playwright  # noqa: F401
        pytest.skip("playwright installed; error path not reachable")
    except ImportError:
        pass
    with pytest.raises(RuntimeError, match="playwright is not installed"):
        main(["ingest", "--from", "web", "http://localhost:3000"])
