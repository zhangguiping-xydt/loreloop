import json
from datetime import datetime, timezone

import pytest

from knowhelm.agents import AgentError
from knowhelm.delegate.context_pack import render, select
from knowhelm.delegate.runner import DelegateRunner
from knowhelm.knowledge.model import Channel, Curation, Entry, Kind, Source, Trust

NOW = datetime(2026, 7, 8, tzinfo=timezone.utc)


def entry(title, content, strong=False, **kw):
    trust = Trust(curation=Curation.APPROVED) if strong else Trust()
    defaults = dict(
        title=title,
        content=content,
        kind=Kind.INTERFACE,
        source=Source(channel=Channel.CODE, locator="src/api.py@abc"),
        trust=trust,
    )
    defaults.update(kw)
    return Entry(**defaults)


UPLOAD_FACT = entry("Upload endpoint contract", "POST /upload returns 201.", strong=True)
UPLOAD_HINT = entry("Upload size limit", "Upload max size is 50MB.")
UNRELATED = entry("Billing cycle", "Invoices are generated monthly.")


def test_select_ranks_relevant_and_splits_by_trust():
    pack = select("change the upload endpoint", [UNRELATED, UPLOAD_HINT, UPLOAD_FACT])
    assert UPLOAD_FACT in pack.strong
    assert UPLOAD_HINT in pack.reference
    assert UNRELATED not in pack.strong + pack.reference


def test_render_separates_contract_levels():
    pack = select("upload endpoint", [UPLOAD_FACT, UPLOAD_HINT])
    text = render(pack)
    assert text.index("Established facts") < text.index("Unverified references")
    assert "src/api.py@abc" in text


def test_render_empty_pack_is_empty():
    pack = select("nothing matches this", [])
    assert render(pack) == ""


class FakeAgent:
    def __init__(self, output="done", fail=False):
        self.output = output
        self.fail = fail
        self.prompts = []

    def run(self, prompt):
        self.prompts.append(prompt)
        if self.fail:
            raise AgentError("boom")
        return self.output


def test_delegate_injects_pack_and_traces(tmp_path):
    agent = FakeAgent()
    result = DelegateRunner(agent, tmp_path).run(
        "fix the upload endpoint", [UPLOAD_FACT, UPLOAD_HINT]
    )
    prompt = agent.prompts[0]
    assert "Established facts" in prompt
    assert prompt.index("Established facts") < prompt.index("# Task")
    events = [json.loads(line) for line in result.trace_path.read_text().splitlines()]
    assert [e["event"] for e in events] == ["delegation_started", "delegation_finished"]
    assert set(events[0]["context_entries"]) == {UPLOAD_FACT.id, UPLOAD_HINT.id}
    assert result.run_id.startswith("run-")


def test_delegate_without_matches_sends_bare_task(tmp_path):
    agent = FakeAgent()
    DelegateRunner(agent, tmp_path).run("completely unrelated words", [UPLOAD_FACT])
    assert "Established facts" not in agent.prompts[0]


def test_delegate_failure_is_traced_and_reraised(tmp_path):
    agent = FakeAgent(fail=True)
    runner = DelegateRunner(agent, tmp_path)
    with pytest.raises(AgentError):
        runner.run("fix the upload endpoint", [UPLOAD_FACT])
    trace_files = list((tmp_path / ".knowhelm/runs").glob("*.jsonl"))
    events = [json.loads(line) for line in trace_files[0].read_text().splitlines()]
    assert events[-1]["event"] == "delegation_failed"
