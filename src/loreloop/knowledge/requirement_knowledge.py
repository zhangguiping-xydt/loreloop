"""Govern committed requirement materials as durable project knowledge.

Requirement intent and implementation proof are deliberately separate:

* ``begin --requirements`` records deterministic requirement/acceptance entries
  as proposed knowledge, bound to the exact committed Markdown digest.
* ``delegation_completed`` means the operator says the implementation is ready.
* an accepted ``knowledge_harvested`` event verifies the entries for later
  strong retrieval and automatic authoritative-baseline reuse.

SQLite is only the searchable projection.  Lifecycle authority and recovery
material live on the evidence chain, so editing the database cannot promote a
proposal or replace the source document silently.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from ..evidence.chain import EvidenceChain, EvidenceRecord
from .authoritative_records import DetectionError
from .authoritative_requirements_input import detect_requirement_markdown
from .endorsement import entry_digest, entry_from_payload, entry_payload
from .model import Channel, Entry, Kind, Source
from .requirement_context import RequirementContextError, RequirementMaterial
from .store import KnowledgeStore

REQUIREMENT_INGESTED_EVENT = "requirement_material_ingested"


class RequirementLifecycle(StrEnum):
    PROPOSED = "proposed"
    IMPLEMENTED = "implemented"
    VERIFIED = "verified"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class GovernedRequirementMaterial:
    locator: str
    commit: str
    sha256: str
    run_id: str
    entry_ids: tuple[str, ...]


def ingest_requirement_materials(
    chain: EvidenceChain,
    store: KnowledgeStore,
    run_id: str,
    materials: tuple[RequirementMaterial, ...],
) -> tuple[Entry, ...]:
    """Append one recoverable ingestion event, then materialize its DB rows."""
    if not materials:
        return ()
    records = chain.verify()
    reconcile_requirement_projection(store, records)
    prior_states = requirement_lifecycle(records)
    latest = latest_requirement_materials(records)
    prospective: list[Entry] = []
    material_payloads: list[dict[str, object]] = []
    retired: set[str] = set()
    retained_verified: set[str] = set()

    for material in materials:
        candidates = _entries_for_material(material)
        projected: list[Entry] = []
        for candidate in candidates:
            entry, _ = store.plan_add_or_refresh(candidate)
            projected.append(entry)
            if prior_states.get(entry.id) is RequirementLifecycle.VERIFIED and entry_digest(
                entry
            ) == entry_digest(store.get(entry.id) or entry):
                retained_verified.add(entry.id)
        previous = latest.get(material.locator)
        if previous is not None and previous.sha256 != material.sha256:
            retired.update(set(previous.entry_ids) - {entry.id for entry in projected})
        prospective.extend(projected)
        material_payloads.append(
            {
                "locator": material.locator,
                "commit": material.commit,
                "sha256": material.sha256,
                "entry_ids": [entry.id for entry in projected],
            }
        )

    unique = {entry.id: entry for entry in prospective}
    payload = {
        "run_id": run_id,
        "materials": material_payloads,
        "entries": {
            entry_id: {
                "digest": entry_digest(entry),
                "entry": entry_payload(entry),
            }
            for entry_id, entry in sorted(unique.items())
        },
        "retired_entry_ids": sorted(retired),
        "retained_verified_entry_ids": sorted(retained_verified),
    }
    record = chain.append(REQUIREMENT_INGESTED_EVENT, payload)
    _materialize_event(store, record)
    return tuple(unique.values())


def reconcile_requirement_projection(store: KnowledgeStore, records: list[EvidenceRecord]) -> None:
    """Restore or refresh requirement rows from chain snapshots after a crash."""
    for record in records:
        if record.event == REQUIREMENT_INGESTED_EVENT:
            _materialize_event(store, record)


def requirement_lifecycle(records: list[EvidenceRecord]) -> dict[str, RequirementLifecycle]:
    states: dict[str, RequirementLifecycle] = {}
    digests: dict[str, str] = {}
    for record in records:
        payload = record.payload
        if record.event == REQUIREMENT_INGESTED_EVENT:
            retained = set(_string_list(payload.get("retained_verified_entry_ids")))
            raw_entries = payload.get("entries")
            if isinstance(raw_entries, dict):
                for entry_id, raw in raw_entries.items():
                    digest = raw.get("digest") if isinstance(raw, dict) else None
                    if isinstance(entry_id, str) and isinstance(digest, str) and digest:
                        digests[entry_id] = digest
            for entry_id in _event_entry_ids(payload):
                if entry_id not in retained:
                    states[entry_id] = RequirementLifecycle.PROPOSED
            for entry_id in _string_list(payload.get("retired_entry_ids")):
                states[entry_id] = RequirementLifecycle.SUPERSEDED
        elif record.event == "delegation_completed":
            for entry_id in _string_list(payload.get("requirement_entry_ids")):
                if states.get(entry_id) not in {
                    RequirementLifecycle.VERIFIED,
                    RequirementLifecycle.SUPERSEDED,
                    RequirementLifecycle.REJECTED,
                }:
                    states[entry_id] = RequirementLifecycle.IMPLEMENTED
        elif record.event == "knowledge_harvested":
            minted = payload.get("minted")
            for entry_id in _string_list(payload.get("requirement_entries")):
                if (
                    isinstance(minted, dict)
                    and minted.get(entry_id) == digests.get(entry_id)
                    and states.get(entry_id)
                    in {RequirementLifecycle.IMPLEMENTED, RequirementLifecycle.VERIFIED}
                    and states.get(entry_id)
                    not in {
                        RequirementLifecycle.SUPERSEDED,
                        RequirementLifecycle.REJECTED,
                    }
                ):
                    states[entry_id] = RequirementLifecycle.VERIFIED
        elif record.event == "curation_changed":
            entry_id = payload.get("entry_id")
            if not isinstance(entry_id, str) or entry_id not in states:
                continue
            if payload.get("curation") == "rejected":
                states[entry_id] = RequirementLifecycle.REJECTED
            elif (
                payload.get("curation") == "draft"
                and states.get(entry_id) is RequirementLifecycle.REJECTED
            ):
                states[entry_id] = RequirementLifecycle.PROPOSED

    # Standard curation and supersession remain operator authority and override
    # the automatic requirement lifecycle projection.
    from .endorsement import chain_effective_curation, chain_superseded_ids
    from .model import Curation

    for entry_id in chain_superseded_ids(records):
        if entry_id in states:
            states[entry_id] = RequirementLifecycle.SUPERSEDED
    for entry_id, curation in chain_effective_curation(records).items():
        if entry_id in states and curation is Curation.REJECTED:
            states[entry_id] = RequirementLifecycle.REJECTED
    return states


def latest_requirement_materials(
    records: list[EvidenceRecord],
) -> dict[str, GovernedRequirementMaterial]:
    latest: dict[str, GovernedRequirementMaterial] = {}
    for record in records:
        if record.event != REQUIREMENT_INGESTED_EVENT:
            continue
        run_id = record.payload.get("run_id")
        materials = record.payload.get("materials")
        if not isinstance(run_id, str) or not isinstance(materials, list):
            continue
        for raw in materials:
            if not isinstance(raw, dict):
                continue
            locator = raw.get("locator")
            commit = raw.get("commit")
            sha256 = raw.get("sha256")
            entry_ids = _string_list(raw.get("entry_ids"))
            if all(isinstance(value, str) and value for value in (locator, commit, sha256)):
                latest[locator] = GovernedRequirementMaterial(
                    locator, commit, sha256, run_id, tuple(entry_ids)
                )
    return latest


def requirement_materials_for_run(
    records: list[EvidenceRecord], run_id: str
) -> tuple[GovernedRequirementMaterial, ...]:
    materials: list[GovernedRequirementMaterial] = []
    for record in records:
        if record.event != REQUIREMENT_INGESTED_EVENT or record.payload.get("run_id") != run_id:
            continue
        raw_materials = record.payload.get("materials")
        if not isinstance(raw_materials, list):
            continue
        for raw in raw_materials:
            if not isinstance(raw, dict):
                continue
            locator = raw.get("locator")
            commit = raw.get("commit")
            sha256 = raw.get("sha256")
            entry_ids = _string_list(raw.get("entry_ids"))
            if all(isinstance(value, str) and value for value in (locator, commit, sha256)):
                materials.append(
                    GovernedRequirementMaterial(locator, commit, sha256, run_id, tuple(entry_ids))
                )
    return tuple(materials)


def verified_requirement_materials(
    records: list[EvidenceRecord],
) -> tuple[GovernedRequirementMaterial, ...]:
    states = requirement_lifecycle(records)
    return tuple(
        material
        for _, material in sorted(latest_requirement_materials(records).items())
        if material.entry_ids
        and all(
            states.get(entry_id) is RequirementLifecycle.VERIFIED for entry_id in material.entry_ids
        )
    )


def retired_requirement_ids(records: list[EvidenceRecord]) -> set[str]:
    return {
        entry_id
        for entry_id, state in requirement_lifecycle(records).items()
        if state in {RequirementLifecycle.SUPERSEDED, RequirementLifecycle.REJECTED}
    }


def validate_verified_requirement_locators(
    workdir: Path,
    records: list[EvidenceRecord],
    *,
    working_tree: bool,
) -> tuple[str, ...]:
    """Return verified reusable locators only when their source is unchanged."""
    materials = verified_requirement_materials(records)
    _validate_material_sources(workdir, materials, working_tree=working_tree)
    return tuple(material.locator for material in materials)


def validate_requirement_materials_for_run(
    workdir: Path, records: list[EvidenceRecord], run_id: str
) -> tuple[GovernedRequirementMaterial, ...]:
    materials = requirement_materials_for_run(records, run_id)
    _validate_material_sources(workdir, materials, working_tree=False)
    return materials


def _validate_material_sources(
    workdir: Path,
    materials: tuple[GovernedRequirementMaterial, ...],
    *,
    working_tree: bool,
) -> None:
    from .requirement_context import load_requirement_materials, requirement_path

    if not materials:
        return
    current = {
        material.locator: material
        for material in load_requirement_materials(
            workdir, tuple(material.locator for material in materials)
        )
    }
    for governed in materials:
        loaded = current[governed.locator]
        if loaded.sha256 != governed.sha256:
            raise RequirementContextError(
                f"verified requirement material changed since ingestion: {governed.locator}; "
                "start a new task with --requirements before rebuilding the baseline"
            )
        if working_tree:
            _, path, repo = requirement_path(workdir, governed.locator)
            try:
                data = (repo / path).read_bytes()
            except OSError as exc:
                raise RequirementContextError(
                    f"cannot read requirement working-tree file: {governed.locator}"
                ) from exc
            if hashlib.sha256(data).hexdigest() != governed.sha256:
                raise RequirementContextError(
                    f"verified requirement material has uncommitted changes: {governed.locator}; "
                    "commit it and start a new task with --requirements"
                )


def requirement_entry_ids_for_run(records: list[EvidenceRecord], run_id: str) -> tuple[str, ...]:
    ids: list[str] = []
    for record in records:
        if record.event == REQUIREMENT_INGESTED_EVENT and record.payload.get("run_id") == run_id:
            ids.extend(_event_entry_ids(record.payload))
    return tuple(dict.fromkeys(ids))


def _entries_for_material(material: RequirementMaterial) -> tuple[Entry, ...]:
    alias, path = _split_locator(material.locator)
    try:
        report = detect_requirement_markdown(material.text, alias, path)
    except DetectionError as exc:
        raise RequirementContextError(str(exc)) from exc
    lines = material.text.splitlines()
    entries: list[Entry] = []
    for requirement in report.requirements:
        metadata = []
        if requirement.role:
            metadata.append(f"role={requirement.role}")
        if requirement.priority:
            metadata.append(f"priority={requirement.priority}")
        content = requirement.statement
        if metadata:
            content += "\n" + "; ".join(metadata)
        title = requirement.external_id or requirement.title or requirement.statement[:80]
        entries.append(
            _entry(
                material,
                Kind.REQUIREMENT,
                title,
                content,
                requirement.source.line,
                requirement.external_id,
                lines,
            )
        )
    for acceptance in report.acceptances:
        title = (
            f"{acceptance.requirement_external_id} acceptance"
            if acceptance.requirement_external_id
            else acceptance.statement[:80]
        )
        entries.append(
            _entry(
                material,
                Kind.ACCEPTANCE,
                title,
                acceptance.statement,
                acceptance.source.line,
                acceptance.requirement_external_id,
                lines,
            )
        )
    return tuple(entries)


def _entry(
    material: RequirementMaterial,
    kind: Kind,
    title: str,
    content: str,
    line: int,
    symbol: str | None,
    lines: list[str],
) -> Entry:
    identity = "\0".join(
        ("requirement-entry-v1", material.locator, kind.value, symbol or "", content)
    )
    entry_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
    excerpt = lines[line - 1].strip() if 0 < line <= len(lines) else content
    return Entry(
        id=entry_id,
        title=title[:160],
        content=content,
        kind=kind,
        source=Source(
            channel=Channel.MANUAL,
            locator=f"requirement:{material.locator}",
            snapshot_ref=material.sha256,
            symbol=symbol,
            line_start=line,
            line_end=line,
            excerpt=excerpt,
        ),
    )


def _materialize_event(store: KnowledgeStore, record: EvidenceRecord) -> None:
    raw_entries = record.payload.get("entries")
    if not isinstance(raw_entries, dict):
        raise RequirementContextError("recorded requirement ingestion entries are invalid")
    for entry_id, raw in raw_entries.items():
        if not isinstance(entry_id, str) or not isinstance(raw, dict):
            raise RequirementContextError("recorded requirement ingestion entries are invalid")
        digest = raw.get("digest")
        entry = entry_from_payload(raw.get("entry"))
        if entry.id != entry_id or not isinstance(digest, str) or entry_digest(entry) != digest:
            raise RequirementContextError(
                "recorded requirement ingestion entry does not match its signed digest"
            )
        current = store.get(entry.id)
        if current is None:
            store.restore(entry)
        elif entry_digest(current) != digest:
            if current.content != entry.content:
                raise RequirementContextError(f"requirement entry id collision for {entry.id[:8]}")
            store.apply_refresh(entry)


def _event_entry_ids(payload: dict) -> list[str]:
    entries = payload.get("entries")
    if not isinstance(entries, dict):
        return []
    return [entry_id for entry_id in entries if isinstance(entry_id, str)]


def _string_list(value: object) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _split_locator(locator: str) -> tuple[str, str]:
    if locator.startswith("repo:"):
        alias, separator, path = locator[5:].partition("/")
        if not separator:
            raise RequirementContextError(f"invalid requirement locator: {locator}")
        return alias, path
    return ".", locator
