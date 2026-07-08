"""Post-acceptance knowledge harvest — the last arc of the flywheel.

After a delegated run is ACCEPTED, two kinds of knowledge flow back:

1. Browser-verified acceptance checks become behavior assertions that are
   born verified: the expectation was written by a human, checked against a
   real page, and is backed by a chain record with an artifact. No LLM is
   involved in minting, so there is no trust laundering.
2. Files changed since the run's base commit are re-reversed through the
   normal code channel. Those entries are born draft/unverified — LLM
   extraction earns no trust exemption just because the run was accepted.

Existing code entries anchored to a pre-run commit and located in changed
files are reported as stale for human curation. Matching old to new
assertions is deliberately NOT automated: an LLM judging "this supersedes
that" is exactly the kind of call that belongs to the curator.

Harvesting is idempotent via the chain: a ``knowledge_harvested`` record is
appended on success and re-harvesting the same run is refused.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..agents import AgentRunner
from ..evidence.artifacts import ArtifactStore
from ..evidence.chain import EvidenceChain
from ..report.acceptance import RunSummary, evaluate_run
from .code_reverse import changed_files, repo_head, reverse_code
from .model import Channel, Entry, Kind, Source, Trust, Verification
from .store import KnowledgeStore

HARVEST_EVENT = "knowledge_harvested"


class HarvestError(Exception):
    pass


@dataclass(frozen=True)
class HarvestResult:
    minted: list[Entry]
    reversed_entries: list[Entry]
    stale: list[Entry] = field(default_factory=list)
    head_commit: str | None = None


def harvest_run(
    run: RunSummary,
    chain: EvidenceChain,
    store: KnowledgeStore,
    runner: AgentRunner,
    repo: Path,
    artifacts: ArtifactStore | None = None,
) -> HarvestResult:
    repo = repo.resolve()
    evaluation = evaluate_run(run, chain, artifacts)
    if not evaluation.accepted:
        raise HarvestError(
            f"run {run.run_id} is not ACCEPTED; only accepted runs feed the knowledge base"
        )
    if any(
        r.event == HARVEST_EVENT and r.payload.get("run_id") == run.run_id
        for r in chain.verify()
    ):
        raise HarvestError(f"run {run.run_id} was already harvested")

    now = datetime.now(timezone.utc)
    minted = _mint_verified_checks(evaluation.passed, run.run_id, now)

    reversed_entries: list[Entry] = []
    stale: list[Entry] = []
    head = None
    if run.base_commit:
        head = repo_head(repo)
        if head != run.base_commit:
            changed = changed_files(repo, run.base_commit)
            if changed:
                reversed_entries = reverse_code(runner, repo, files=changed)
                stale = _stale_entries(store, repo, changed, head)

    for entry in [*minted, *reversed_entries]:
        store.add(entry)

    chain.append(
        HARVEST_EVENT,
        {
            "run_id": run.run_id,
            "minted": [e.id for e in minted],
            "reversed": [e.id for e in reversed_entries],
            "stale": [e.id for e in stale],
            "base_commit": run.base_commit,
            "head_commit": head,
        },
    )
    return HarvestResult(
        minted=minted, reversed_entries=reversed_entries, stale=stale, head_commit=head
    )


def _mint_verified_checks(passed, run_id: str, now: datetime) -> list[Entry]:
    minted: list[Entry] = []
    seen: set[tuple[str, str]] = set()
    for rec in passed:
        payload = rec.payload
        if payload.get("verified_via") != "browser":
            continue
        check, url = payload["check"], payload["url"]
        if (check, url) in seen:
            continue
        seen.add((check, url))
        minted.append(
            Entry(
                title=check[:80],
                content=check,
                kind=Kind.ACCEPTANCE,
                source=Source(
                    channel=Channel.WEB,
                    locator=url,
                    snapshot_ref=payload.get("page_snapshot"),
                ),
                trust=Trust(
                    verification=Verification.VERIFIED,
                    verified_at=now,
                    verified_by=run_id,
                ),
            )
        )
    return minted


def _stale_entries(
    store: KnowledgeStore, repo: Path, changed: list[Path], head: str
) -> list[Entry]:
    changed_rel = {str(p.relative_to(repo)) for p in changed}
    stale = []
    for entry in store.list(channel=Channel.CODE):
        if entry.source.snapshot_ref == head:
            continue
        file_part = entry.source.locator.rsplit("@", 1)[0]
        if file_part in changed_rel:
            stale.append(entry)
    return stale
