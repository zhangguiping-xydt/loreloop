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


def endorse_run(chain, run_id, task="fix upload", context=None, base_commit=None):
    """The chain record cmd_run appends — the acceptance authority."""
    return chain.append("delegation_completed", {
        "run_id": run_id, "task": task,
        "context_entries": context or ["e1"], "base_commit": base_commit,
    })


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


def test_cli_report_without_runs_errors(workdir, capsys):
    assert main(["report"]) == 2
    assert "no runs found" in capsys.readouterr().err


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

    path = workdir / ".knowhelm/evidence.jsonl"
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    lines[0]["payload"]["task"] = "forged task"
    path.write_text("\n".join(json.dumps(line, sort_keys=True) for line in lines) + "\n")

    assert main(["report", run.run_id]) == 2
    err = capsys.readouterr().err
    assert "evidence chain broken" in err
    assert "Traceback" not in err


def test_cli_report_bad_trace_json_exits_cleanly(workdir, capsys):
    runs = workdir / ".knowhelm/runs"
    runs.mkdir(parents=True)
    (runs / "run-bad.jsonl").write_text("{not json\n", encoding="utf-8")

    assert main(["report", "run-bad"]) == 2
    err = capsys.readouterr().err
    assert "invalid run trace" in err
    assert "line 1 is not JSON" in err
    assert "Traceback" not in err


def test_cli_harvest_trace_without_started_exits_cleanly(workdir, capsys):
    runs = workdir / ".knowhelm/runs"
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
    assert main(["knowledge", "approve", entry.id[:8]]) == 0
    capsys.readouterr()
    assert main(["knowledge", "list"]) == 0
    assert "[strong]" in capsys.readouterr().out

    # the approval is endorsed on the evidence chain, not just the DB,
    # and bound to the entry's content digest
    from knowhelm.knowledge.endorsement import entry_digest

    records = EvidenceChain.for_workdir(workdir).verify()
    assert records[-1].event == "curation_changed"
    assert records[-1].payload == {
        "entry_id": entry.id,
        "curation": "approved",
        "entry_digest": entry_digest(entry),
    }


def test_cli_curation_rejects_missing_or_ambiguous_prefix(workdir, capsys):
    from knowhelm.knowledge.store import KnowledgeStore

    (workdir / ".knowhelm").mkdir()
    KnowledgeStore(workdir / ".knowhelm/knowledge.db").close()

    assert main(["knowledge", "approve", "deadbeef"]) == 2
    assert "no entry matches" in capsys.readouterr().err
    assert main(["knowledge", "approve"]) == 2
    assert EvidenceChain.for_workdir(workdir).verify() == []


def test_cli_invalid_curation_transition_exits_cleanly(workdir, capsys):
    from knowhelm.knowledge.model import Channel, Entry, Kind, Source
    from knowhelm.knowledge.store import KnowledgeStore

    db = workdir / ".knowhelm/knowledge.db"
    db.parent.mkdir(parents=True)
    entry = Entry(
        title="T", content="C", kind=Kind.INTERFACE,
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


def test_cli_report_and_harvest_reject_path_traversal_run_ids(workdir, capsys):
    assert main(["report", "../../../etc/passwd"]) == 2
    assert "invalid run id" in capsys.readouterr().err
    assert main(["harvest", "../escape"]) == 2
    assert "invalid run id" in capsys.readouterr().err


def test_cli_run_demotes_unendorsed_strong_entries(workdir, monkeypatch, capsys):
    from datetime import datetime, timezone

    import knowhelm.cli as cli
    from knowhelm.knowledge.model import (
        Channel, Entry, Kind, Source, Trust, Verification,
    )
    from knowhelm.knowledge.store import KnowledgeStore

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
    db = workdir / ".knowhelm/knowledge.db"
    db.parent.mkdir(parents=True)
    with KnowledgeStore(db) as store:
        store.add(laundered)

    agent = FakeAgent()
    monkeypatch.setattr(cli, "_agent", lambda name: agent)
    assert main(["run", "fix the upload endpoint"]) == 0

    err = capsys.readouterr().err
    assert "without evidence-chain endorsement" in err
    assert laundered.id[:8] in err
    prompt = agent.prompts[0]
    assert "Established facts" not in prompt
    assert "Unverified references" in prompt


def test_cli_run_keeps_chain_endorsed_strong_entries(workdir, monkeypatch, capsys):
    import knowhelm.cli as cli
    from knowhelm.knowledge.model import Channel, Entry, Kind, Source
    from knowhelm.knowledge.store import KnowledgeStore

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
    db = workdir / ".knowhelm/knowledge.db"
    db.parent.mkdir(parents=True)
    with KnowledgeStore(db) as store:
        store.add(entry)

    assert main(["knowledge", "approve", entry.id[:8]]) == 0
    capsys.readouterr()

    agent = FakeAgent()
    monkeypatch.setattr(cli, "_agent", lambda name: agent)
    assert main(["run", "fix the upload endpoint"]) == 0

    assert "without evidence-chain endorsement" not in capsys.readouterr().err
    assert "Established facts" in agent.prompts[0]


def test_cli_run_keeps_chain_approved_entry_after_db_curation_flip(workdir, monkeypatch, capsys):
    import knowhelm.cli as cli
    from knowhelm.knowledge.model import Channel, Entry, Kind, Source
    from knowhelm.knowledge.store import KnowledgeStore

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
    db = workdir / ".knowhelm/knowledge.db"
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
    assert main(["run", "fix the upload endpoint"]) == 0
    assert "Established facts" in agent.prompts[0]
    assert "POST /upload returns 201." in agent.prompts[0]

    assert main(["knowledge", "list"]) == 0
    line = next(li for li in capsys.readouterr().out.splitlines() if entry.id[:8] in li)
    assert "[strong]" in line
    assert "[chain-backed" in line


def test_cli_run_does_not_claim_drifted_chain_backed_entry_as_established(
    workdir, monkeypatch, capsys
):
    import subprocess

    import knowhelm.cli as cli
    from knowhelm.knowledge.model import Channel, Entry, Kind, Source
    from knowhelm.knowledge.store import KnowledgeStore

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
    db = workdir / ".knowhelm/knowledge.db"
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
    assert main(["run", "fix the upload endpoint"]) == 0
    err = capsys.readouterr().err

    assert "injected as established fact" not in err
    assert "Established facts" not in agent.prompts[0]
    assert "Unverified references" in agent.prompts[0]
    assert "source_changed_since_capture" in agent.prompts[0]


def test_cli_run_demotes_entry_whose_content_changed_after_endorsement(workdir, monkeypatch, capsys):
    # H2 end-to-end: approve, then rewrite the row's content by SQL. The
    # endorsement is bound to the old digest, so cmd_run injects as reference.
    import knowhelm.cli as cli
    from knowhelm.knowledge.model import Channel, Entry, Kind, Source
    from knowhelm.knowledge.store import KnowledgeStore

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
    db = workdir / ".knowhelm/knowledge.db"
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
    assert main(["run", "fix the upload endpoint"]) == 0

    err = capsys.readouterr().err
    assert "without evidence-chain endorsement" in err
    assert "Established facts" not in agent.prompts[0]
    assert "Unverified references" in agent.prompts[0]


def test_cli_run_ignores_deleted_supersede_link(workdir, monkeypatch, capsys):
    # H3 attack: after the curator supersedes an old strong entry, the agent
    # deletes the links row. The chain replay must keep it out of injection.
    import knowhelm.cli as cli
    from knowhelm.knowledge.model import Channel, Entry, Kind, Source
    from knowhelm.knowledge.store import KnowledgeStore

    class FakeAgent:
        def __init__(self):
            self.prompts = []

        def run(self, prompt):
            self.prompts.append(prompt)
            return "done"

    old = Entry(
        title="Old upload contract", content="POST /upload returns 200.",
        kind=Kind.INTERFACE, source=Source(channel=Channel.CODE, locator="api.py@abc"),
    )
    new = Entry(
        title="New upload contract", content="POST /upload returns 201.",
        kind=Kind.INTERFACE, source=Source(channel=Channel.CODE, locator="api.py@def"),
    )
    db = workdir / ".knowhelm/knowledge.db"
    db.parent.mkdir(parents=True)
    with KnowledgeStore(db) as store:
        store.add(old)
        store.add(new)
    assert main(["knowledge", "approve", old.id[:8]]) == 0
    assert main(["knowledge", "supersede", new.id[:8], old.id[:8]]) == 0
    capsys.readouterr()

    with KnowledgeStore(db) as store:
        store._conn.execute("DELETE FROM links")
        store._conn.commit()
        assert store.superseded_ids() == set()  # DB cache is clean — attack in place

    agent = FakeAgent()
    monkeypatch.setattr(cli, "_agent", lambda name: agent)
    assert main(["run", "fix the upload contract"]) == 0

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

    import knowhelm.cli as cli
    from knowhelm.knowledge.endorsement import entry_digest
    from knowhelm.knowledge.model import Channel, Entry, Kind, Source
    from knowhelm.knowledge.store import KnowledgeStore

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
    db = workdir / ".knowhelm/knowledge.db"
    db.parent.mkdir(parents=True)
    with KnowledgeStore(db) as store:
        store.add(entry)
    chain = EvidenceChain.for_workdir(workdir)
    chain.append(
        "entry_verified", {"entry_id": entry.id, "entry_digest": entry_digest(entry)}
    )
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
    assert main(["run", "fix the upload endpoint"]) == 0
    assert "POST /upload returns 201." not in agent.prompts[0]

    # the list view stays honest too
    assert main(["knowledge", "list"]) == 0
    line = next(
        li for li in capsys.readouterr().out.splitlines() if entry.id[:8] in li
    )
    assert "[rejected]" in line
    assert "[strong]" not in line


def test_cli_knowledge_list_flags_unendorsed_strong_as_ref(workdir, capsys):
    # Round-4 F1: the DB is agent-writable, so a strong bit UPDATEd straight
    # into it must not render as [strong] in the list view either — list
    # informs curation decisions and applies the same endorsement rules as
    # injection.
    from datetime import datetime, timezone

    from knowhelm.knowledge.model import (
        Channel, Entry, Kind, Source, Trust, Verification,
    )
    from knowhelm.knowledge.store import KnowledgeStore

    laundered = Entry(
        title="Laundered fact", content="Agent says this is verified.",
        kind=Kind.BEHAVIOR,
        source=Source(channel=Channel.WEB, locator="http://app.local/x"),
        trust=Trust(
            verification=Verification.VERIFIED,
            verified_at=datetime.now(timezone.utc), verified_by="forged",
        ),
    )
    db = workdir / ".knowhelm/knowledge.db"
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
    from knowhelm.evidence.artifacts import ArtifactStore
    from knowhelm.webexplore.browser import Observation

    trace = write_trace(workdir)
    chain = EvidenceChain.for_workdir(workdir)
    run = load_run(trace)
    endorse_run(chain, run.run_id)
    artifacts = ArtifactStore.for_workdir(workdir)
    obs = Observation(url="http://a", title="T", text="ok")
    sha, path = artifacts.save_observation(obs)
    chain.append("check_passed", {
        "run_id": run.run_id, "check": "page ok", "artifact": sha,
        "url": obs.url, "page_snapshot": obs.snapshot_hash,
    })

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
    from knowhelm.evidence.artifacts import ArtifactStore
    from knowhelm.webexplore.browser import Observation

    trace = write_trace(workdir)
    chain = EvidenceChain.for_workdir(workdir)
    run = load_run(trace)
    endorse_run(chain, run.run_id)
    artifacts = ArtifactStore.for_workdir(workdir)
    sha, _ = artifacts.save_observation(Observation(url="http://a", title="T", text="ok"))
    chain.append(
        "check_passed", {"run_id": run.run_id, "check": "page ok", "artifact": sha}
    )

    report = render_report(run, chain, artifacts)
    assert "Verdict: NOT ACCEPTED" in report
    assert "no url/page_snapshot pin" in report


def test_report_flags_artifact_swapped_for_a_different_observation(workdir):
    from knowhelm.evidence.artifacts import ArtifactStore
    from knowhelm.webexplore.browser import Observation

    trace = write_trace(workdir)
    chain = EvidenceChain.for_workdir(workdir)
    run = load_run(trace)
    endorse_run(chain, run.run_id)
    artifacts = ArtifactStore.for_workdir(workdir)
    real = Observation(url="http://app.local/upload", title="Upload", text="Max 50MB.")
    sha, _ = artifacts.save_observation(real)
    chain.append("check_passed", {
        "run_id": run.run_id, "check": "page ok", "artifact": sha,
        "url": real.url, "page_snapshot": real.snapshot_hash,
    })
    assert "Verdict: ACCEPTED" in render_report(run, chain, artifacts)

    # swap: another hash-valid artifact of a DIFFERENT page replaces the ref
    other = Observation(url="http://evil.local/", title="Other", text="whatever")
    other_sha, _ = artifacts.save_observation(other)
    swapped = EvidenceChain.for_workdir(workdir)
    swapped.append("check_passed", {
        "run_id": run.run_id, "check": "swapped", "artifact": other_sha,
        "url": real.url, "page_snapshot": real.snapshot_hash,
    })
    report = render_report(run, swapped, artifacts)
    assert "Verdict: NOT ACCEPTED" in report
    assert "does not match the chain record" in report


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
    record_check(
        chain, run.run_id, "cell | breaker", passed=False, detail="line1\nline2 | x"
    )
    report = render_report(run, chain)
    row = next(line for line in report.splitlines() if "cell" in line and line.startswith("|"))
    assert "cell \\| breaker" in row
    assert "\n" not in row


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

    # supersession silences an entry — endorsed on the chain like curation
    records = EvidenceChain.for_workdir(workdir).verify()
    assert records[-1].event == "entry_superseded"
    assert records[-1].payload == {"new_id": new.id, "old_id": old.id}

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


def test_cli_init_creates_evidence_key(workdir, monkeypatch, capsys):
    import shutil as _shutil

    from knowhelm.evidence.chain import key_path_for

    monkeypatch.setattr(_shutil, "which", lambda name: None)
    assert main(["init"]) == 0
    assert key_path_for(workdir).exists()
    assert "evidence signing key" in capsys.readouterr().out


def test_cli_surfaces_legacy_key_error_cleanly(workdir, capsys):
    legacy = workdir / ".knowhelm/evidence.key"
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
    monkeypatch.setattr(_shutil, "which", lambda name: "/usr/bin/claude" if name == "claude" else None)

    assert main(["init", "--skill"]) == 0
    out = capsys.readouterr().out
    assert "initialized .knowhelm/" in out
    assert (workdir / ".knowhelm/knowledge.db").exists()
    assert ".knowhelm/" in (workdir / ".gitignore").read_text()

    skill = workdir / ".claude/skills/knowhelm/SKILL.md"
    assert skill.exists()
    text = skill.read_text()
    assert "Never run `knowhelm harvest`" in text
    assert "name: knowhelm" in text

    # idempotent: second run must not duplicate the gitignore line
    assert main(["init", "--skill"]) == 0
    assert (workdir / ".gitignore").read_text().count(".knowhelm/") == 1


def test_cli_init_respects_no_skill_and_missing_agents(workdir, monkeypatch, capsys):
    import shutil as _shutil

    monkeypatch.setattr(_shutil, "which", lambda name: "/usr/bin/claude" if name == "claude" else None)
    assert main(["init", "--no-skill"]) == 0
    assert "skipped skill installation" in capsys.readouterr().out
    assert not (workdir / ".claude").exists()

    monkeypatch.setattr(_shutil, "which", lambda name: None)
    assert main(["init"]) == 0
    assert "skill installation skipped" in capsys.readouterr().out


def test_cli_ingest_web_requires_playwright(workdir):
    try:
        import playwright  # noqa: F401
        pytest.skip("playwright installed; error path not reachable")
    except ImportError:
        pass
    with pytest.raises(RuntimeError, match="playwright is not installed"):
        main(["ingest", "--from", "web", "http://localhost:3000"])
