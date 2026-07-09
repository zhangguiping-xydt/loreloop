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

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path

from ..agents import AgentRunner
from ..evidence.artifacts import ArtifactStore
from ..evidence.chain import EvidenceChain, EvidenceRecord
from ..report.acceptance import RunSummary, evaluate_run
from .code_reverse import changed_files, changed_paths, dirty_source_files, repo_head, reverse_code
from .endorsement import chain_superseded_ids, entry_digest, unendorsed_strong_ids
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
    demoted: list[Entry] = field(default_factory=list)
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
    records = chain.verify()
    evaluation = evaluate_run(run, chain, artifacts)
    if not evaluation.accepted:
        raise HarvestError(
            f"run {run.run_id} is not ACCEPTED; only accepted runs feed the knowledge base"
        )
    if any(
        r.event == HARVEST_EVENT and r.payload.get("run_id") == run.run_id for r in records
    ):
        raise HarvestError(f"run {run.run_id} was already harvested")
    # The base commit comes from the chain-endorsed delegation_completed
    # record, never from the trace: the trace lives in the agent-writable
    # tree, and a forged base_commit there would steer which files get
    # re-reversed and which entries get flagged stale.
    base_commit = evaluation.base_commit
    if base_commit:
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
    candidates, unauditable = _mint_verified_checks(evaluation.passed, run.run_id, now, artifacts)
    review = _review_candidates(store, candidates, chain_superseded_ids(records))
    # Chain before trust bits: the minted rows are only COMPUTED here — their
    # digests go on the chain first, and the DB is written after the append
    # succeeds. A failure anywhere in between leaves drafts and re-anchors at
    # worst, never a strong bit without its chain-backed justification.
    minted = _dedupe([_prospective_minted(store, e) for e in candidates])

    reversed_entries: list[Entry] = []
    stale: list[Entry] = []
    head = None
    if base_commit:
        head = repo_head(repo)
        if head != base_commit:
            touched = changed_paths(repo, base_commit)
            if touched:
                raw = reverse_code(runner, repo, files=changed_files(repo, base_commit))
                reversed_entries = _dedupe(
                    [_store_reanchored(store, e, head, now) for e in raw]
                )
                # Staleness after re-anchoring: an entry whose claim was just
                # re-extracted verbatim at head is confirmed, not stale.
                stale = _stale_entries(store, touched, head)

    # Re-anchoring moves the entry's digest away from any prior endorsement,
    # and re-extraction earns no new one (an LLM restating a claim is not a
    # trust act). Strong entries that just lost their endorsement are
    # surfaced so the operator can re-approve or re-verify deliberately.
    demoted = _demoted_by_reanchor(reversed_entries, records)

    # minted carries id -> digest of the row the mint WILL leave: minting
    # endorses verified status bound to that content. reversed digests are
    # provenance only — endorsement replay ignores them (see endorsement
    # module).
    chain.append(
        HARVEST_EVENT,
        {
            "run_id": run.run_id,
            "minted": {e.id: entry_digest(e) for e in minted},
            "reversed": {e.id: entry_digest(e) for e in reversed_entries},
            "stale": [e.id for e in stale],
            "unauditable_checks": unauditable,
            "review": [e.id for e in review],
            "base_commit": base_commit,
            "head_commit": head,
        },
    )
    for e in minted:
        _persist_minted(store, e, run.run_id, now)
    return HarvestResult(
        minted=minted,
        reversed_entries=reversed_entries,
        stale=stale,
        unauditable_checks=unauditable,
        review=review,
        demoted=demoted,
        head_commit=head,
    )


def _demoted_by_reanchor(
    reversed_entries: list[Entry], records: list[EvidenceRecord]
) -> list[Entry]:
    """Re-anchored entries that claim strong trust but whose new digest has
    no chain endorsement — the price of not letting LLM re-extraction move
    endorsements. ``records`` predate this harvest's own event, which is
    correct: that event grants no endorsement anyway."""
    return [
        e
        for e in reversed_entries
        if e.id in unendorsed_strong_ids(reversed_entries, records)
    ]


def _review_candidates(
    store: KnowledgeStore, minted: list[Entry], superseded: set[str]
) -> list[Entry]:
    """Pre-existing strong entries at the locators being minted. A new
    born-verified assertion about the same page may confirm, refine or
    contradict them — a judgment for the curator, never automated here.
    ``superseded`` is the chain-endorsed set: entries the curator already
    retired need no second review, and the chain (not the deletable DB links
    table) decides who those are."""
    locators = {e.source.locator for e in minted}
    if not locators:
        return []
    minted_content = {(e.source.locator, e.content) for e in minted}
    return [
        e
        for e in store.list(channel=Channel.WEB)
        if e.source.locator in locators
        and e.is_strong_evidence()
        and e.id not in superseded
        and (e.source.locator, e.content) not in minted_content
    ]


def _prospective_minted(store: KnowledgeStore, entry: Entry) -> Entry:
    """The row a mint WILL leave, computed without writing. If the identical
    claim already exists for the same page (e.g. a draft from web ingestion),
    the verification lands on that row — recording a real, chain-backed
    browser check on the existing entry is bookkeeping, not laundering; a
    prior CONTRADICTED flips to VERIFIED because the newest evidence says
    the claim holds.

    Every field of the reused row is forced to the canonical mint values
    (title/kind, snapshot on top of the content+locator match that found
    it). The minted digest endorses the WHOLE row: leaving a pre-planted
    title or kind in place would sign whatever the agent parked on the row
    before the run — the digest must cover only what the browser check
    actually vouches for."""
    existing = store.find_duplicate(entry)
    if existing is None:
        return entry
    return replace(
        existing,
        title=entry.title,
        kind=entry.kind,
        source=replace(
            existing.source,
            snapshot_ref=entry.source.snapshot_ref or existing.source.snapshot_ref,
        ),
        trust=entry.trust,
    )


def _persist_minted(store: KnowledgeStore, entry: Entry, run_id: str, now: datetime) -> None:
    """Write a prospective minted row, AFTER its digest went on the chain.
    All fields land in one atomic UPDATE: the chain endorsed the digest of the
    complete row, so a partially-written one must be impossible."""
    if store.get(entry.id) is None:
        store.add(entry)
        return
    store.set_verification(
        entry.id,
        Verification.VERIFIED,
        run_id,
        now,
        snapshot_ref=entry.source.snapshot_ref,
        title=entry.title,
        kind=entry.kind,
    )


def _store_reanchored(
    store: KnowledgeStore, entry: Entry, head: str, now: datetime
) -> Entry:
    """Store a re-reversed entry. If the identical claim already exists for
    the same file, re-anchor the existing entry to head instead of inserting:
    the claim was just re-derived verbatim from the current source, so leaving
    the old anchor would flag it stale/drifted forever despite being fresh.
    Trust columns are untouched, but a strong entry effectively DEMOTES here:
    its chain endorsement was bound to the old anchor's digest, and no event
    moves it — re-approval/re-verification is the human's call, because the
    "verbatim claim" that triggered this could itself be steered LLM output."""
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
