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

import re
import subprocess
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path

from ..agents import AgentRunner
from ..evidence.artifacts import ArtifactStore
from ..evidence.chain import EvidenceChain, EvidenceRecord
from ..evidence.repository_state import repository_states_match
from ..report.acceptance import RunSummary, evaluate_run
from .code_reverse import changed_files, changed_paths, dirty_source_files, repo_head, reverse_code
from .endorsement import chain_superseded_ids, entry_digest, unendorsed_strong_ids
from .model import Channel, Entry, Kind, Source, Trust, Verification
from .repos import RepoConfigError, parse_code_locator, resolve_repo
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
    head_commits: dict[str, str] = field(default_factory=dict)
    resumed: bool = False

    @property
    def head_commit(self) -> str | None:
        return self.head_commits.get(".")


def harvest_run(
    run: RunSummary,
    chain: EvidenceChain,
    store: KnowledgeStore,
    runner: AgentRunner,
    workdir: Path,
    artifacts: ArtifactStore | None = None,
) -> HarvestResult:
    workdir = workdir.resolve()
    records = chain.verify()
    evaluation = evaluate_run(run, chain, artifacts)
    if not evaluation.accepted:
        raise HarvestError(
            f"run {run.run_id} is not ACCEPTED; only accepted runs feed the knowledge base"
        )
    for check in evaluation.passed:
        if check.payload.get("judge") != "command":
            continue
        matches, reason = repository_states_match(check.payload.get("repository_states"), workdir)
        if not matches:
            raise HarvestError(
                f"command evidence for {check.payload.get('check')!r} is stale: {reason}; "
                "rerun the command check against the current repository state"
            )
    prior = next(
        (
            record
            for record in records
            if record.event == HARVEST_EVENT and record.payload.get("run_id") == run.run_id
        ),
        None,
    )
    if prior is not None:
        return _resume_harvest(run, store, prior, records)
    # The base commit comes from the chain-endorsed delegation_completed
    # record, never from the trace: the trace lives in the agent-writable
    # tree, and a forged base_commit there would steer which files get
    # re-reversed and which entries get flagged stale.
    base_commits = evaluation.base_commits
    repos: dict[str, Path] = {}
    for name in base_commits:
        try:
            repo = resolve_repo(workdir, name)
        except RepoConfigError as exc:
            raise HarvestError(
                f"repository {name!r} from the run cannot be resolved: {exc}"
            ) from exc
        if not repo.is_dir() or not (repo / ".git").exists():
            raise HarvestError(f"repository {name!r} from the run is not a git root: {repo}")
        repos[name] = repo
        try:
            dirty = dirty_source_files(repo)
        except subprocess.CalledProcessError as exc:
            raise HarvestError(f"cannot inspect repository {name!r}: git status failed") from exc
        if dirty:
            raise HarvestError(
                f"repository {name!r} has uncommitted source changes; commit them so "
                "knowledge anchors to a real commit: " + ", ".join(dirty[:10])
            )

    now = datetime.now(timezone.utc)
    candidates, unauditable = _mint_verified_checks(evaluation.passed, run.run_id, now, artifacts)
    # Chain before trust bits: the minted rows are only COMPUTED here — their
    # digests go on the chain first, and the DB is written after the append
    # succeeds. A failure anywhere in between leaves drafts and re-anchors at
    # worst, never a strong bit without its chain-backed justification.
    minted = _dedupe([_prospective_minted(store, e) for e in candidates])

    reversed_entries: list[Entry] = []
    touched_by_repo: dict[str, list[str]] = {}
    head_commits: dict[str, str] = {}
    plans: dict[str, tuple[Path, str, list[str], list[Path]]] = {}
    for name, base in base_commits.items():
        repo = repos[name]
        try:
            head = repo_head(repo)
        except subprocess.CalledProcessError as exc:
            raise HarvestError(f"cannot inspect repository {name!r}: HEAD is unavailable") from exc
        head_commits[name] = head
        if head == base:
            continue
        try:
            touched = changed_paths(repo, base)
            files = changed_files(repo, base)
        except subprocess.CalledProcessError as exc:
            raise HarvestError(
                f"cannot inspect repository {name!r}: base commit {base!r} is invalid"
            ) from exc
        touched_by_repo[name] = touched
        plans[name] = (repo, head, touched, files)
    for name, (repo, head, touched, files) in plans.items():
        if not touched:
            continue
        raw = reverse_code(
            runner,
            repo,
            files=files,
            repo_name=name,
        )
        reversed_entries.extend(_store_reanchored(store, entry, head, now) for entry in raw)
    reversed_entries = _dedupe(reversed_entries)
    stale = _stale_entries(store, touched_by_repo, head_commits)
    review = _review_candidates(
        store,
        [*minted, *reversed_entries],
        chain_superseded_ids(records),
    )

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
            "minted_entries": [_serialize_minted(entry) for entry in minted],
            "reversed": {e.id: entry_digest(e) for e in reversed_entries},
            "stale": [e.id for e in stale],
            "unauditable_checks": unauditable,
            "review": [e.id for e in review],
            "injected_entries": list(evaluation.completed.payload.get("context_entries", [])),
            "base_commits": base_commits,
            "head_commits": head_commits,
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
        head_commits=head_commits,
    )


def _resume_harvest(
    run: RunSummary,
    store: KnowledgeStore,
    record: EvidenceRecord,
    records: list[EvidenceRecord],
) -> HarvestResult:
    """Finish DB materialization after a crash that followed the chain append.

    New harvest events carry complete minted rows plus their digests. The chain
    is the recovery log: no agent call, source scan, or second event is needed.
    Older events do not carry enough material and retain the historical
    already-harvested behavior.
    """
    raw_rows = record.payload.get("minted_entries")
    expected = record.payload.get("minted")
    if not isinstance(raw_rows, list) or not isinstance(expected, dict):
        raise HarvestError(f"run {run.run_id} was already harvested")
    try:
        minted = [_deserialize_minted(item) for item in raw_rows]
    except (KeyError, TypeError, ValueError) as exc:
        raise HarvestError("recorded harvest recovery data is invalid") from exc
    if {entry.id for entry in minted} != set(expected) or any(
        entry_digest(entry) != expected[entry.id] for entry in minted
    ):
        raise HarvestError("recorded harvest recovery data does not match its signed digests")

    complete = all(
        (stored := store.get(entry.id)) is not None and entry_digest(stored) == expected[entry.id]
        for entry in minted
    )
    if complete:
        raise HarvestError(f"run {run.run_id} was already harvested")
    for entry in minted:
        stored = store.get(entry.id)
        if stored is None or entry_digest(stored) != expected[entry.id]:
            _persist_minted(
                store,
                entry,
                entry.trust.verified_by or run.run_id,
                entry.trust.verified_at or datetime.now(timezone.utc),
            )
        restored = store.get(entry.id)
        if restored is None or entry_digest(restored) != expected[entry.id]:
            raise HarvestError(
                f"cannot restore minted entry {entry.id[:8]} to its chain-endorsed content"
            )

    reversed_entries = _entries_from_ids(store, record.payload.get("reversed", {}))
    stale = _entries_from_ids(store, record.payload.get("stale", []))
    review = _entries_from_ids(store, record.payload.get("review", []))
    demoted = _demoted_by_reanchor(reversed_entries, records[: record.index])
    return HarvestResult(
        minted=minted,
        reversed_entries=reversed_entries,
        stale=stale,
        unauditable_checks=list(record.payload.get("unauditable_checks", [])),
        review=review,
        demoted=demoted,
        head_commits=dict(record.payload.get("head_commits", {})),
        resumed=True,
    )


def _entries_from_ids(store: KnowledgeStore, values) -> list[Entry]:
    ids = values.keys() if isinstance(values, dict) else values if isinstance(values, list) else []
    return [entry for entry_id in ids if (entry := store.get(entry_id)) is not None]


def _serialize_minted(entry: Entry) -> dict:
    return {
        "id": entry.id,
        "title": entry.title,
        "content": entry.content,
        "kind": entry.kind.value,
        "source": {
            "channel": entry.source.channel.value,
            "locator": entry.source.locator,
            "snapshot_ref": entry.source.snapshot_ref,
        },
        "trust": {
            "verification": entry.trust.verification.value,
            "verified_at": entry.trust.verified_at.isoformat(),
            "verified_by": entry.trust.verified_by,
        },
        "created_at": entry.created_at.isoformat(),
        "updated_at": entry.updated_at.isoformat(),
    }


def _deserialize_minted(data: dict) -> Entry:
    source = data["source"]
    trust = data["trust"]
    return Entry(
        id=data["id"],
        title=data["title"],
        content=data["content"],
        kind=Kind(data["kind"]),
        source=Source(
            channel=Channel(source["channel"]),
            locator=source["locator"],
            snapshot_ref=source.get("snapshot_ref"),
        ),
        trust=Trust(
            verification=Verification(trust["verification"]),
            verified_at=datetime.fromisoformat(trust["verified_at"]),
            verified_by=trust["verified_by"],
        ),
        created_at=datetime.fromisoformat(data["created_at"]),
        updated_at=datetime.fromisoformat(data["updated_at"]),
    )


def _demoted_by_reanchor(
    reversed_entries: list[Entry], records: list[EvidenceRecord]
) -> list[Entry]:
    """Re-anchored entries that claim strong trust but whose new digest has
    no chain endorsement — the price of not letting LLM re-extraction move
    endorsements. ``records`` predate this harvest's own event, which is
    correct: that event grants no endorsement anyway."""
    return [e for e in reversed_entries if e.id in unendorsed_strong_ids(reversed_entries, records)]


def _review_candidates(
    store: KnowledgeStore, candidates: list[Entry], superseded: set[str]
) -> list[Entry]:
    """Return strong entries that may duplicate or conflict with new claims.

    Same-source changes use a permissive lexical threshold (numbers often
    carry the contradiction); cross-source candidates require much stronger
    similarity. Nothing is merged or superseded automatically.
    """
    if not candidates:
        return []
    candidate_ids = {entry.id for entry in candidates}
    review = []
    for existing in store.list():
        if (
            existing.id in candidate_ids
            or existing.id in superseded
            or not existing.is_strong_evidence()
        ):
            continue
        for candidate in candidates:
            if existing.content == candidate.content:
                continue
            same_scope = _same_source_scope(existing, candidate)
            threshold = 0.50 if same_scope else 0.78
            if same_scope or _claim_similarity(existing.content, candidate.content) >= threshold:
                review.append(existing)
                break
    return _dedupe(review)


def _same_source_scope(left: Entry, right: Entry) -> bool:
    if left.source.channel is not right.source.channel:
        return False
    if left.source.channel is Channel.CODE:
        try:
            left_repo, left_path, _ = parse_code_locator(left.source.locator)
            right_repo, right_path, _ = parse_code_locator(right.source.locator)
        except RepoConfigError:
            return False
        return (left_repo, left_path) == (right_repo, right_path)
    return left.source.locator == right.source.locator


def _claim_similarity(left: str, right: str) -> float:
    left_tokens = set(re.findall(r"[a-z0-9_]+|[一-鿿]", left.casefold()))
    right_tokens = set(re.findall(r"[a-z0-9_]+|[一-鿿]", right.casefold()))
    union = left_tokens | right_tokens
    return len(left_tokens & right_tokens) / len(union) if union else 1.0


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


def _store_reanchored(store: KnowledgeStore, entry: Entry, head: str, now: datetime) -> Entry:
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
    return store.set_snapshot_ref(
        existing.id,
        head,
        now,
        locator=entry.source.locator,
        evidence_source=entry.source,
    )


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
        verified_via = payload.get("verified_via")
        if verified_via not in {"browser", "command"}:
            continue
        check = payload["check"]
        sha = payload.get("artifact")
        if not sha or artifacts is None:
            unauditable.append(check)
            continue
        data = artifacts.load(sha)
        if verified_via == "browser":
            url = payload["url"]
            locator = payload.get("script_locator") or url
            channel = Channel.WEB
            snapshot_ref = payload.get("page_snapshot")
            if data.get("url") != url or data.get("snapshot_hash") != snapshot_ref:
                unauditable.append(check)
                continue
        else:
            locator = f"command:{sha}"
            channel = Channel.EVIDENCE
            snapshot_ref = sha
            if (
                data.get("type") != "command_evidence"
                or data.get("argv") != payload.get("command")
                or data.get("exit_code") != 0
                or payload.get("exit_code") != 0
                or data.get("timed_out")
            ):
                unauditable.append(check)
                continue
        if (check, locator) in seen:
            continue
        seen.add((check, locator))
        minted.append(
            Entry(
                title=check[:80],
                content=check,
                kind=Kind.ACCEPTANCE,
                source=Source(
                    channel=channel,
                    locator=locator,
                    snapshot_ref=snapshot_ref,
                ),
                trust=Trust(
                    verification=Verification.VERIFIED,
                    verified_at=now,
                    verified_by=run_id,
                ),
            )
        )
    return minted, unauditable


def _stale_entries(
    store: KnowledgeStore,
    touched_by_repo: dict[str, list[str]],
    head_commits: dict[str, str],
) -> list[Entry]:
    """Staleness is judged against every touched path — including deleted or
    renamed files, where the old entries are the ones most in need of review."""
    touched_sets = {name: set(paths) for name, paths in touched_by_repo.items()}
    stale = []
    for entry in store.list(channel=Channel.CODE):
        repo_name, relpath, _ = parse_code_locator(entry.source.locator)
        if repo_name not in touched_sets:
            continue
        if entry.source.snapshot_ref == head_commits.get(repo_name):
            continue
        if relpath in touched_sets[repo_name]:
            stale.append(entry)
    return stale
