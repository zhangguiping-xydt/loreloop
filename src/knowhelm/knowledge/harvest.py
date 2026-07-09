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
from .code_reverse import changed_files, changed_paths, dirty_source_files, repo_head, reverse_code
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
    unauditable_checks: list[str] = field(default_factory=list)
    review: list[Entry] = field(default_factory=list)
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
    if run.base_commit:
        dirty = dirty_source_files(repo)
        if dirty:
            # Knowledge must anchor to a reproducible commit. Reversing a
            # dirty tree would stamp entries with a HEAD sha whose content is
            # not what was actually read — a lie in the provenance field.
            raise HarvestError(
                "working tree has uncommitted source changes; commit them so "
                "knowledge anchors to a real commit: " + ", ".join(dirty[:10])
            )

    now = datetime.now(timezone.utc)
    minted, unauditable = _mint_verified_checks(evaluation.passed, run.run_id, now, artifacts)
    review = _review_candidates(store, minted)
    minted = _dedupe([_store_minted(store, e, run.run_id, now) for e in minted])

    reversed_entries: list[Entry] = []
    stale: list[Entry] = []
    head = None
    if run.base_commit:
        head = repo_head(repo)
        if head != run.base_commit:
            touched = changed_paths(repo, run.base_commit)
            if touched:
                raw = reverse_code(runner, repo, files=changed_files(repo, run.base_commit))
                reversed_entries = _dedupe(
                    [_store_reanchored(store, e, head, now) for e in raw]
                )
                # Staleness after re-anchoring: an entry whose claim was just
                # re-extracted verbatim at head is confirmed, not stale.
                stale = _stale_entries(store, touched, head)

    chain.append(
        HARVEST_EVENT,
        {
            "run_id": run.run_id,
            "minted": [e.id for e in minted],
            "reversed": [e.id for e in reversed_entries],
            "stale": [e.id for e in stale],
            "unauditable_checks": unauditable,
            "review": [e.id for e in review],
            "base_commit": run.base_commit,
            "head_commit": head,
        },
    )
    return HarvestResult(
        minted=minted,
        reversed_entries=reversed_entries,
        stale=stale,
        unauditable_checks=unauditable,
        review=review,
        head_commit=head,
    )


def _review_candidates(store: KnowledgeStore, minted: list[Entry]) -> list[Entry]:
    """Pre-existing strong entries at the locators being minted. A new
    born-verified assertion about the same page may confirm, refine or
    contradict them — a judgment for the curator, never automated here."""
    locators = {e.source.locator for e in minted}
    if not locators:
        return []
    minted_content = {(e.source.locator, e.content) for e in minted}
    return [
        e
        for e in store.list(channel=Channel.WEB)
        if e.source.locator in locators
        and e.is_strong_evidence()
        and (e.source.locator, e.content) not in minted_content
    ]


def _store_minted(store: KnowledgeStore, entry: Entry, run_id: str, now: datetime) -> Entry:
    """Store a born-verified assertion. If the identical claim already exists
    for the same page (e.g. a draft from web ingestion), the verification
    that just happened must land on it: this run really did check the claim
    against the live page, chain-backed — recording that on the existing
    entry is bookkeeping of a real event, not trust laundering. Works through
    the normal verification state machine; a prior CONTRADICTED flips to
    VERIFIED because the newest browser evidence says the claim holds."""
    existing = store.find_duplicate(entry)
    if existing is None:
        return store.add(entry)
    updated = store.set_verification(existing.id, Verification.VERIFIED, run_id, now)
    if entry.source.snapshot_ref and updated.source.snapshot_ref != entry.source.snapshot_ref:
        updated = store.set_snapshot_ref(existing.id, entry.source.snapshot_ref, now)
    return updated


def _store_reanchored(
    store: KnowledgeStore, entry: Entry, head: str, now: datetime
) -> Entry:
    """Store a re-reversed entry. If the identical claim already exists for
    the same file, re-anchor the existing entry to head instead of inserting:
    the claim was just re-derived verbatim from the current source, so leaving
    the old anchor would flag it stale/drifted forever despite being fresh.
    Trust state is untouched — the comparison is deterministic string
    equality, not an LLM judgment."""
    existing = store.find_duplicate(entry)
    if existing is None:
        return store.add(entry)
    if existing.source.snapshot_ref == head:
        return existing
    return store.set_snapshot_ref(existing.id, head, now, locator=entry.source.locator)


def _dedupe(entries: list[Entry]) -> list[Entry]:
    seen: set[str] = set()
    unique = []
    for entry in entries:
        if entry.id not in seen:
            seen.add(entry.id)
            unique.append(entry)
    return unique


def _mint_verified_checks(
    passed, run_id: str, now: datetime, artifacts: ArtifactStore | None
) -> tuple[list[Entry], list[str]]:
    """Mint born-verified entries from browser checks. Born-verified is only
    honest when the observation material exists, matches its hash AND matches
    what the chain says was observed (url + page snapshot): a check whose
    artifact is missing, tampered or swapped for a different observation
    cannot be re-audited, so it never mints — it is returned as unauditable
    instead of silently skipped."""
    minted: list[Entry] = []
    unauditable: list[str] = []
    seen: set[tuple[str, str]] = set()
    for rec in passed:
        payload = rec.payload
        if payload.get("verified_via") != "browser":
            continue
        check, url = payload["check"], payload["url"]
        sha = payload.get("artifact")
        if not sha or artifacts is None:
            unauditable.append(check)
            continue
        data = artifacts.load(sha)
        if data.get("url") != url or data.get("snapshot_hash") != payload.get("page_snapshot"):
            unauditable.append(check)
            continue
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
    return minted, unauditable


def _stale_entries(store: KnowledgeStore, touched: list[str], head: str) -> list[Entry]:
    """Staleness is judged against every touched path — including deleted or
    renamed files, where the old entries are the ones most in need of review."""
    touched_set = set(touched)
    stale = []
    for entry in store.list(channel=Channel.CODE):
        if entry.source.snapshot_ref == head:
            continue
        file_part = entry.source.locator.rsplit("@", 1)[0]
        if file_part in touched_set:
            stale.append(entry)
    return stale
