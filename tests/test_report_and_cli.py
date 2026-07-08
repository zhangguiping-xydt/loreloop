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


def test_report_flags_missing_and_tampered_artifacts(workdir):
    from knowhelm.evidence.artifacts import ArtifactStore
    from knowhelm.webexplore.browser import Observation

    trace = write_trace(workdir)
    chain = EvidenceChain.for_workdir(workdir)
    run = load_run(trace)
    artifacts = ArtifactStore.for_workdir(workdir)
    sha, path = artifacts.save_observation(
        Observation(url="http://a", title="T", text="ok")
    )
    chain.append(
        "check_passed", {"run_id": run.run_id, "check": "page ok", "artifact": sha}
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


def test_cli_supersede_links_and_hides_old_entry(workdir, capsys):
    from knowhelm.knowledge.model import Channel, Entry, Kind, Source
    from knowhelm.knowledge.store import KnowledgeStore

    db = workdir / ".knowhelm/knowledge.db"
    db.parent.mkdir(parents=True)
    old = Entry(
        title="Old upload contract", content="POST /upload returns 200.",
        kind=Kind.INTERFACE, source=Source(channel=Channel.CODE, locator="api.py@abc"),
    )
    new = Entry(
        title="New upload contract", content="POST /upload returns 201.",
        kind=Kind.INTERFACE, source=Source(channel=Channel.CODE, locator="api.py@def"),
    )
    with KnowledgeStore(db) as store:
        store.add(old)
        store.add(new)

    assert main(["knowledge", "supersede", new.id[:8], old.id[:8]]) == 0
    assert "superseded: Old upload contract" in capsys.readouterr().out

    assert main(["knowledge", "list"]) == 0
    out = capsys.readouterr().out
    assert "[superseded]" in out

    with KnowledgeStore(db) as store:
        assert {e.id for e in store.list_active()} == {new.id}


def test_cli_supersede_rejects_ambiguous_or_missing_prefix(workdir, capsys):
    from knowhelm.knowledge.store import KnowledgeStore

    (workdir / ".knowhelm").mkdir()
    KnowledgeStore(workdir / ".knowhelm/knowledge.db").close()
    assert main(["knowledge", "supersede", "deadbeef", "cafebabe"]) == 2
    assert "no entry matches" in capsys.readouterr().err
    assert main(["knowledge", "supersede", "deadbeef"]) == 2


def test_cli_list_stale_flags_drifted_code_anchors(workdir, capsys):
    import subprocess

    from knowhelm.knowledge.model import Channel, Entry, Kind, Source
    from knowhelm.knowledge.store import KnowledgeStore

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
        title="Upload contract", content="POST /upload returns 201.",
        kind=Kind.INTERFACE,
        source=Source(channel=Channel.CODE, locator=f"api.py@{base}", snapshot_ref=base),
    )
    db = workdir / ".knowhelm/knowledge.db"
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


def test_cli_ingest_web_requires_playwright(workdir):
    try:
        import playwright  # noqa: F401
        pytest.skip("playwright installed; error path not reachable")
    except ImportError:
        pass
    with pytest.raises(RuntimeError, match="playwright is not installed"):
        main(["ingest", "--from", "web", "http://localhost:3000"])
