"""SQLite persistence for knowledge entries and links."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from .model import (
    CURATION_TRANSITIONS,
    Channel,
    Curation,
    Entry,
    Kind,
    Link,
    LinkType,
    Source,
    Trust,
    Verification,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    kind TEXT NOT NULL,
    channel TEXT NOT NULL,
    locator TEXT NOT NULL,
    snapshot_ref TEXT,
    curation TEXT NOT NULL,
    verification TEXT NOT NULL,
    verified_at TEXT,
    verified_by TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_entries_kind ON entries(kind);
CREATE INDEX IF NOT EXISTS idx_entries_channel ON entries(channel);
CREATE TABLE IF NOT EXISTS links (
    from_id TEXT NOT NULL REFERENCES entries(id),
    to_id TEXT NOT NULL REFERENCES entries(id),
    link_type TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (from_id, to_id, link_type)
);
"""


class InvalidTransition(Exception):
    pass


class KnowledgeStore:
    def __init__(self, db_path: str | Path) -> None:
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "KnowledgeStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def find_duplicate(self, entry: Entry) -> Entry | None:
        """Exact duplicate: same content, channel and source location (for
        code entries the file path — the anchor commit may differ). Semantic
        near-duplicates are a curation call and deliberately not detected."""
        rows = self._conn.execute(
            "SELECT * FROM entries WHERE content = ? AND channel = ?",
            (entry.content, entry.source.channel.value),
        ).fetchall()
        key = _locator_key(entry.source.channel, entry.source.locator)
        for row in rows:
            if _locator_key(Channel(row["channel"]), row["locator"]) == key:
                return _to_entry(row)
        return None

    def add(self, entry: Entry) -> Entry:
        """Insert the entry, or return the existing exact duplicate unchanged.
        Re-reversing unchanged truths (e.g. every harvest of a hot file) must
        not multiply them. BEGIN IMMEDIATE makes check-then-insert atomic
        across processes: two concurrent adds of the same fact must not both
        pass the duplicate check and insert twins."""
        with self._conn:
            self._conn.execute("BEGIN IMMEDIATE")
            existing = self.find_duplicate(entry)
            if existing is not None:
                return existing
            self._conn.execute(
                "INSERT INTO entries VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    entry.id,
                    entry.title,
                    entry.content,
                    entry.kind.value,
                    entry.source.channel.value,
                    entry.source.locator,
                    entry.source.snapshot_ref,
                    entry.trust.curation.value,
                    entry.trust.verification.value,
                    _iso(entry.trust.verified_at),
                    entry.trust.verified_by,
                    _iso(entry.created_at),
                    _iso(entry.updated_at),
                ),
            )
        return entry

    def get(self, entry_id: str) -> Entry | None:
        row = self._conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
        return _to_entry(row) if row else None

    def list(
        self,
        kind: Kind | None = None,
        channel: Channel | None = None,
        curation: Curation | None = None,
    ) -> list[Entry]:
        clauses, params = [], []
        if kind:
            clauses.append("kind = ?")
            params.append(kind.value)
        if channel:
            clauses.append("channel = ?")
            params.append(channel.value)
        if curation:
            clauses.append("curation = ?")
            params.append(curation.value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(
            f"SELECT * FROM entries {where} ORDER BY created_at", params
        ).fetchall()
        return [_to_entry(r) for r in rows]

    def set_curation(self, entry_id: str, new: Curation, now: datetime) -> Entry:
        entry = self._require(entry_id)
        if new not in CURATION_TRANSITIONS[entry.trust.curation]:
            raise InvalidTransition(f"{entry.trust.curation.value} -> {new.value}")
        with self._conn:
            self._conn.execute(
                "UPDATE entries SET curation = ?, updated_at = ? WHERE id = ?",
                (new.value, _iso(now), entry_id),
            )
        return self._require(entry_id)

    def set_verification(
        self, entry_id: str, new: Verification, verified_by: str, now: datetime
    ) -> Entry:
        self._require(entry_id)
        if new is Verification.UNVERIFIED:
            raise InvalidTransition("cannot transition back to unverified")
        with self._conn:
            self._conn.execute(
                "UPDATE entries SET verification = ?, verified_at = ?, verified_by = ?,"
                " updated_at = ? WHERE id = ?",
                (new.value, _iso(now), verified_by, _iso(now), entry_id),
            )
        return self._require(entry_id)

    def set_title_kind(self, entry_id: str, title: str, kind: Kind, now: datetime) -> Entry:
        self._require(entry_id)
        with self._conn:
            self._conn.execute(
                "UPDATE entries SET title = ?, kind = ?, updated_at = ? WHERE id = ?",
                (title, kind.value, _iso(now), entry_id),
            )
        return self._require(entry_id)

    def set_snapshot_ref(
        self, entry_id: str, snapshot_ref: str, now: datetime, locator: str | None = None
    ) -> Entry:
        """Re-anchor an entry. Code locators embed the anchor commit, so a
        code re-anchor must update both fields to stay consistent."""
        self._require(entry_id)
        with self._conn:
            if locator is None:
                self._conn.execute(
                    "UPDATE entries SET snapshot_ref = ?, updated_at = ? WHERE id = ?",
                    (snapshot_ref, _iso(now), entry_id),
                )
            else:
                self._conn.execute(
                    "UPDATE entries SET snapshot_ref = ?, locator = ?, updated_at = ?"
                    " WHERE id = ?",
                    (snapshot_ref, locator, _iso(now), entry_id),
                )
        return self._require(entry_id)

    def add_link(self, link: Link) -> None:
        for eid in (link.from_id, link.to_id):
            self._require(eid)
        with self._conn:
            # A link is set membership; re-adding the same link is a no-op.
            self._conn.execute(
                "INSERT OR IGNORE INTO links VALUES (?,?,?,?)",
                (link.from_id, link.to_id, link.link_type.value, _iso(link.created_at)),
            )

    def superseded_ids(self) -> set[str]:
        rows = self._conn.execute(
            "SELECT DISTINCT to_id FROM links WHERE link_type = ?",
            (LinkType.SUPERSEDES.value,),
        ).fetchall()
        return {r["to_id"] for r in rows}

    def list_active(self) -> list[Entry]:
        """Entries eligible for injection: not rejected, not superseded.
        Superseded entries stay in the store as history — supersession is a
        link, not a status flag — but they no longer inform new work.

        The links table is a convenience cache inside the agent-writable
        tree: deleting a supersedes row here must not resurrect an entry, so
        trust-sensitive callers additionally filter by the chain-replayed
        superseded set (see ``endorsement.chain_superseded_ids``)."""
        superseded = self.superseded_ids()
        return [
            e
            for e in self.list()
            if e.trust.curation is not Curation.REJECTED and e.id not in superseded
        ]

    def links_for(self, entry_id: str) -> list[Link]:
        rows = self._conn.execute(
            "SELECT * FROM links WHERE from_id = ? OR to_id = ? ORDER BY created_at",
            (entry_id, entry_id),
        ).fetchall()
        return [
            Link(
                from_id=r["from_id"],
                to_id=r["to_id"],
                link_type=LinkType(r["link_type"]),
                created_at=datetime.fromisoformat(r["created_at"]),
            )
            for r in rows
        ]

    def _require(self, entry_id: str) -> Entry:
        entry = self.get(entry_id)
        if entry is None:
            raise KeyError(entry_id)
        return entry


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _locator_key(channel: Channel, locator: str) -> str:
    """Code locators are file@commit; the same fact re-reversed at a newer
    commit is still the same fact, so only the file part identifies it."""
    if channel is Channel.CODE:
        return locator.rsplit("@", 1)[0]
    return locator


def _to_entry(row: sqlite3.Row) -> Entry:
    return Entry(
        id=row["id"],
        title=row["title"],
        content=row["content"],
        kind=Kind(row["kind"]),
        source=Source(
            channel=Channel(row["channel"]),
            locator=row["locator"],
            snapshot_ref=row["snapshot_ref"],
        ),
        trust=Trust(
            curation=Curation(row["curation"]),
            verification=Verification(row["verification"]),
            verified_at=(
                datetime.fromisoformat(row["verified_at"]) if row["verified_at"] else None
            ),
            verified_by=row["verified_by"],
        ),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )
