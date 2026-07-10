"""SQLite persistence for knowledge entries and links."""

from __future__ import annotations

import os
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
from .repos import parse_code_locator
from ..paths import ensure_private_directory, reject_symlink

SCHEMA_VERSION = 1

_CREATE_ENTRIES = """
CREATE TABLE IF NOT EXISTS entries (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    kind TEXT NOT NULL,
    channel TEXT NOT NULL,
    locator TEXT NOT NULL,
    snapshot_ref TEXT,
    source_symbol TEXT,
    source_line_start INTEGER,
    source_line_end INTEGER,
    source_excerpt TEXT,
    curation TEXT NOT NULL,
    verification TEXT NOT NULL,
    verified_at TEXT,
    verified_by TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""
_CREATE_LINKS = """
CREATE TABLE IF NOT EXISTS links (
    from_id TEXT NOT NULL REFERENCES entries(id),
    to_id TEXT NOT NULL REFERENCES entries(id),
    link_type TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (from_id, to_id, link_type)
)
"""


class InvalidTransition(Exception):
    pass


class SchemaVersionError(RuntimeError):
    pass


def migration_backup_path(db_path: str | Path, version: int) -> Path:
    """Stable pre-upgrade backup path that an older release can reopen."""
    path = Path(db_path)
    return path.with_name(f"{path.name}.schema-v{version}.bak")


def _migration_v1(conn: sqlite3.Connection) -> None:
    """Create the current schema or add source-evidence columns to legacy v0."""
    conn.execute(_CREATE_ENTRIES)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_entries_kind ON entries(kind)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_entries_channel ON entries(channel)")
    conn.execute(_CREATE_LINKS)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(entries)").fetchall()}
    additions = {
        "source_symbol": "TEXT",
        "source_line_start": "INTEGER",
        "source_line_end": "INTEGER",
        "source_excerpt": "TEXT",
    }
    for column, sql_type in additions.items():
        if column not in columns:
            conn.execute(f"ALTER TABLE entries ADD COLUMN {column} {sql_type}")


_MIGRATIONS = {1: _migration_v1}


class KnowledgeStore:
    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        ensure_private_directory(self._db_path.parent)
        reject_symlink(self._db_path, label="knowledge database")
        self._conn = sqlite3.connect(str(db_path))
        os.chmod(self._db_path, 0o600)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._migrate()

    @classmethod
    def open_readonly(cls, db_path: str | Path) -> "KnowledgeStore":
        reject_symlink(Path(db_path), label="knowledge database")
        path = Path(db_path).resolve()
        store = cls.__new__(cls)
        store._db_path = path
        store._conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
        store._conn.row_factory = sqlite3.Row
        store._conn.execute("PRAGMA foreign_keys = ON")
        return store

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
                """INSERT INTO entries (
                    id, title, content, kind, channel, locator, snapshot_ref,
                    source_symbol, source_line_start, source_line_end, source_excerpt,
                    curation, verification, verified_at, verified_by, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    entry.id,
                    entry.title,
                    entry.content,
                    entry.kind.value,
                    entry.source.channel.value,
                    entry.source.locator,
                    entry.source.snapshot_ref,
                    entry.source.symbol,
                    entry.source.line_start,
                    entry.source.line_end,
                    entry.source.excerpt,
                    entry.trust.curation.value,
                    entry.trust.verification.value,
                    _iso(entry.trust.verified_at),
                    entry.trust.verified_by,
                    _iso(entry.created_at),
                    _iso(entry.updated_at),
                ),
            )
        return entry

    def add_or_refresh(self, entry: Entry) -> tuple[Entry, bool]:
        """Insert a new assertion or refresh an exact claim's source anchor.

        Re-ingestion is provenance, not a trust act: the stable id and cached
        trust state remain, while locator/snapshot/evidence move to the source
        that was actually read. Chain replay will demote any old endorsement
        until the operator explicitly re-approves the refreshed digest.
        """
        with self._conn:
            self._conn.execute("BEGIN IMMEDIATE")
            existing = self.find_duplicate(entry)
            if existing is None:
                self._conn.execute(
                    """INSERT INTO entries (
                        id, title, content, kind, channel, locator, snapshot_ref,
                        source_symbol, source_line_start, source_line_end, source_excerpt,
                        curation, verification, verified_at, verified_by, created_at, updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        entry.id,
                        entry.title,
                        entry.content,
                        entry.kind.value,
                        entry.source.channel.value,
                        entry.source.locator,
                        entry.source.snapshot_ref,
                        entry.source.symbol,
                        entry.source.line_start,
                        entry.source.line_end,
                        entry.source.excerpt,
                        entry.trust.curation.value,
                        entry.trust.verification.value,
                        _iso(entry.trust.verified_at),
                        entry.trust.verified_by,
                        _iso(entry.created_at),
                        _iso(entry.updated_at),
                    ),
                )
                return entry, False

            if (
                entry.title == existing.title
                and entry.kind is existing.kind
                and entry.source == existing.source
            ):
                return existing, False
            refreshed = Entry(
                id=existing.id,
                title=entry.title,
                content=entry.content,
                kind=entry.kind,
                source=entry.source,
                trust=existing.trust,
                created_at=existing.created_at,
                updated_at=entry.updated_at,
            )
            self._conn.execute(
                """UPDATE entries SET
                    title = ?, kind = ?, locator = ?, snapshot_ref = ?,
                    source_symbol = ?, source_line_start = ?, source_line_end = ?,
                    source_excerpt = ?, updated_at = ?
                   WHERE id = ?""",
                (
                    refreshed.title,
                    refreshed.kind.value,
                    refreshed.source.locator,
                    refreshed.source.snapshot_ref,
                    refreshed.source.symbol,
                    refreshed.source.line_start,
                    refreshed.source.line_end,
                    refreshed.source.excerpt,
                    _iso(refreshed.updated_at),
                    refreshed.id,
                ),
            )
            return refreshed, True

    def _migrate(self) -> None:
        """Apply ordered migrations with a pre-upgrade backup and rollback.

        ``PRAGMA user_version`` is the durable marker. Existing databases are
        copied before the first forward step. Every step then runs inside one
        explicit transaction, so a crash or invalid migration preserves the
        previous schema and data. Newer schemas are refused before any write.
        """
        version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise SchemaVersionError(
                f"knowledge database schema {version} is newer than supported "
                f"schema {SCHEMA_VERSION}; upgrade LoreLoop or restore a compatible backup"
            )
        if version == SCHEMA_VERSION:
            return
        if _has_user_schema(self._conn):
            _backup_once(self._conn, migration_backup_path(self._db_path, version))
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            while version < SCHEMA_VERSION:
                target = version + 1
                migration = _MIGRATIONS.get(target)
                if migration is None:
                    raise SchemaVersionError(f"missing migration for schema {version} -> {target}")
                migration(self._conn)
                self._conn.execute(f"PRAGMA user_version = {target}")
                version = target
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def get(self, entry_id: str) -> Entry | None:
        row = self._conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
        return _to_entry(row) if row else None

    def list(
        self,
        kind: Kind | None = None,
        channel: Channel | None = None,
        curation: Curation | None = None,
    ) -> list[Entry]:
        kind_value = kind.value if kind else None
        channel_value = channel.value if channel else None
        curation_value = curation.value if curation else None
        rows = self._conn.execute(
            """SELECT * FROM entries
               WHERE (? IS NULL OR kind = ?)
                 AND (? IS NULL OR channel = ?)
                 AND (? IS NULL OR curation = ?)
               ORDER BY created_at""",
            (
                kind_value,
                kind_value,
                channel_value,
                channel_value,
                curation_value,
                curation_value,
            ),
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
        self,
        entry_id: str,
        new: Verification,
        verified_by: str,
        now: datetime,
        *,
        snapshot_ref: str | None = None,
        title: str | None = None,
        kind: Kind | None = None,
    ) -> Entry:
        """Record a verification outcome. The optional fields land in the SAME
        statement: a verify or mint that also re-anchors/retitles must never
        leave a half-written row — a crash between two UPDATEs would leave
        verified trust on content whose digest the chain never endorsed."""
        self._require(entry_id)
        if new is Verification.UNVERIFIED:
            raise InvalidTransition("cannot transition back to unverified")
        timestamp = _iso(now)
        kind_value = kind.value if kind else None
        with self._conn:
            self._conn.execute(
                """UPDATE entries
                   SET verification = ?, verified_at = ?, verified_by = ?, updated_at = ?,
                       snapshot_ref = CASE WHEN ? THEN ? ELSE snapshot_ref END,
                       title = CASE WHEN ? THEN ? ELSE title END,
                       kind = CASE WHEN ? THEN ? ELSE kind END
                   WHERE id = ?""",
                (
                    new.value,
                    timestamp,
                    verified_by,
                    timestamp,
                    snapshot_ref is not None,
                    snapshot_ref,
                    title is not None,
                    title,
                    kind_value is not None,
                    kind_value,
                    entry_id,
                ),
            )
        return self._require(entry_id)

    def set_snapshot_ref(
        self,
        entry_id: str,
        snapshot_ref: str,
        now: datetime,
        locator: str | None = None,
        *,
        evidence_source: Source | None = None,
    ) -> Entry:
        """Re-anchor an entry. Code locators embed the anchor commit, so a
        code re-anchor must update both fields to stay consistent."""
        self._require(entry_id)
        with self._conn:
            if evidence_source is not None:
                self._conn.execute(
                    """UPDATE entries SET snapshot_ref = ?, locator = ?,
                       source_symbol = ?, source_line_start = ?, source_line_end = ?,
                       source_excerpt = ?, updated_at = ? WHERE id = ?""",
                    (
                        snapshot_ref,
                        locator or evidence_source.locator,
                        evidence_source.symbol,
                        evidence_source.line_start,
                        evidence_source.line_end,
                        evidence_source.excerpt,
                        _iso(now),
                        entry_id,
                    ),
                )
            elif locator is None:
                self._conn.execute(
                    "UPDATE entries SET snapshot_ref = ?, updated_at = ? WHERE id = ?",
                    (snapshot_ref, _iso(now), entry_id),
                )
            else:
                self._conn.execute(
                    "UPDATE entries SET snapshot_ref = ?, locator = ?, updated_at = ? WHERE id = ?",
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

        This is a convenience view over the SQLite cache. Trust-sensitive
        paths must start from ``list()`` and replay chain curation/supersession
        instead, because SQLite edits must not suppress chain-backed facts."""
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


def _has_user_schema(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' LIMIT 1"
    ).fetchone()
    return row is not None


def _backup_once(conn: sqlite3.Connection, path: Path) -> None:
    if path.exists():
        return
    ensure_private_directory(path.parent)
    reject_symlink(path, label="schema backup")
    backup = sqlite3.connect(str(path))
    try:
        conn.backup(backup)
    finally:
        backup.close()
    os.chmod(path, 0o600)


def _locator_key(channel: Channel, locator: str) -> str | tuple[str, str]:
    """Code locators are file@commit; the same fact re-reversed at a newer
    commit is still the same fact, so only the file part identifies it."""
    if channel is Channel.CODE:
        repo_name, relpath, _ = parse_code_locator(locator)
        return repo_name, relpath
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
            symbol=_row_value(row, "source_symbol"),
            line_start=_row_value(row, "source_line_start"),
            line_end=_row_value(row, "source_line_end"),
            excerpt=_row_value(row, "source_excerpt"),
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


def _row_value(row: sqlite3.Row, column: str):
    return row[column] if column in row.keys() else None
