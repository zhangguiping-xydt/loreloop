from datetime import datetime, timezone
import sqlite3

import pytest

from loreloop.knowledge.model import (
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
from loreloop.knowledge.store import (
    SCHEMA_VERSION,
    InvalidTransition,
    KnowledgeStore,
    SchemaVersionError,
    migration_backup_path,
)

NOW = datetime(2026, 7, 8, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def store(tmp_path):
    with KnowledgeStore(tmp_path / "kh.db") as s:
        yield s


def make_entry(**kw) -> Entry:
    defaults = dict(
        title="Upload API contract",
        content="POST /upload accepts multipart, returns 201 with file id.",
        kind=Kind.INTERFACE,
        source=Source(channel=Channel.CODE, locator="src/api/upload.py@abc123", snapshot_ref="abc123"),
    )
    defaults.update(kw)
    return Entry(**defaults)


def test_roundtrip(store):
    e = make_entry()
    store.add(e)
    got = store.get(e.id)
    assert got == e


def test_roundtrip_preserves_source_evidence_location(store):
    e = make_entry(
        source=Source(
            channel=Channel.CODE,
            locator="src/api/upload.py@abc123",
            snapshot_ref="abc123",
            symbol="upload",
            line_start=12,
            line_end=18,
            excerpt="def upload(file):\n    ...",
        )
    )

    store.add(e)

    assert store.get(e.id) == e


def test_opening_legacy_database_migrates_without_losing_entries(tmp_path):
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE entries (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, content TEXT NOT NULL,
            kind TEXT NOT NULL, channel TEXT NOT NULL, locator TEXT NOT NULL,
            snapshot_ref TEXT, curation TEXT NOT NULL, verification TEXT NOT NULL,
            verified_at TEXT, verified_by TEXT, created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE links (
            from_id TEXT NOT NULL REFERENCES entries(id),
            to_id TEXT NOT NULL REFERENCES entries(id),
            link_type TEXT NOT NULL, created_at TEXT NOT NULL,
            PRIMARY KEY (from_id, to_id, link_type)
        );
        """
    )
    legacy = make_entry()
    conn.execute(
        "INSERT INTO entries VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            legacy.id, legacy.title, legacy.content, legacy.kind.value,
            legacy.source.channel.value, legacy.source.locator,
            legacy.source.snapshot_ref, legacy.trust.curation.value,
            legacy.trust.verification.value, None, None,
            legacy.created_at.isoformat(), legacy.updated_at.isoformat(),
        ),
    )
    conn.commit()
    conn.close()

    with KnowledgeStore(path) as migrated:
        assert migrated.get(legacy.id) == legacy
        columns = {
            row[1] for row in migrated._conn.execute("PRAGMA table_info(entries)").fetchall()
        }
        assert {"source_symbol", "source_line_start", "source_line_end", "source_excerpt"} <= columns
        assert migrated._conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION

    backup = migration_backup_path(path, 0)
    assert backup.is_file()
    with sqlite3.connect(backup) as old:
        assert old.execute("PRAGMA user_version").fetchone()[0] == 0
        old_columns = {row[1] for row in old.execute("PRAGMA table_info(entries)").fetchall()}
        assert "source_excerpt" not in old_columns
        assert old.execute("SELECT title FROM entries WHERE id = ?", (legacy.id,)).fetchone()[0] == legacy.title


def test_failed_migration_rolls_back_schema_and_version(tmp_path, monkeypatch):
    import loreloop.knowledge.store as store_module

    path = tmp_path / "rollback.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE entries (id TEXT PRIMARY KEY)")
    conn.execute("INSERT INTO entries VALUES ('kept')")
    conn.commit()
    conn.close()

    def broken_migration(conn):
        conn.execute("ALTER TABLE entries ADD COLUMN temporary_value TEXT")
        raise RuntimeError("simulated migration failure")

    monkeypatch.setattr(store_module, "_MIGRATIONS", {1: broken_migration})
    with pytest.raises(RuntimeError, match="simulated migration failure"):
        KnowledgeStore(path)

    with sqlite3.connect(path) as unchanged:
        assert unchanged.execute("PRAGMA user_version").fetchone()[0] == 0
        columns = {row[1] for row in unchanged.execute("PRAGMA table_info(entries)").fetchall()}
        assert "temporary_value" not in columns
        assert unchanged.execute("SELECT id FROM entries").fetchone()[0] == "kept"


def test_newer_schema_is_refused_without_mutation(tmp_path):
    path = tmp_path / "future.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE future_data (value TEXT)")
    conn.execute("INSERT INTO future_data VALUES ('untouched')")
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    conn.commit()
    conn.close()

    with pytest.raises(SchemaVersionError, match="newer than supported"):
        KnowledgeStore(path)

    with sqlite3.connect(path) as unchanged:
        assert unchanged.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION + 1
        assert unchanged.execute("SELECT value FROM future_data").fetchone()[0] == "untouched"
    assert not migration_backup_path(path, SCHEMA_VERSION + 1).exists()


def test_list_filters(store):
    a = make_entry()
    b = make_entry(
        title="Login flow behavior",
        content="Login redirects to /dashboard on success.",
        kind=Kind.BEHAVIOR,
        source=Source(channel=Channel.WEB, locator="http://localhost:3000/login", snapshot_ref="h1"),
    )
    store.add(a)
    store.add(b)
    assert [e.id for e in store.list(kind=Kind.BEHAVIOR)] == [b.id]
    assert [e.id for e in store.list(channel=Channel.CODE)] == [a.id]


def test_curation_state_machine(store):
    e = make_entry()
    store.add(e)
    updated = store.set_curation(e.id, Curation.APPROVED, NOW)
    assert updated.trust.curation is Curation.APPROVED
    with pytest.raises(InvalidTransition):
        store.set_curation(e.id, Curation.DRAFT, NOW)


def test_verification_requires_actor_and_forbids_rollback(store):
    e = make_entry()
    store.add(e)
    updated = store.set_verification(e.id, Verification.VERIFIED, "run-42", NOW)
    assert updated.trust.verified_by == "run-42"
    assert updated.trust.verified_at == NOW
    with pytest.raises(InvalidTransition):
        store.set_verification(e.id, Verification.UNVERIFIED, "run-43", NOW)


def test_set_verification_writes_companion_fields_in_one_statement(store):
    # Round-5 M3: a verify/mint that also re-anchors or retitles must land as
    # ONE row state — the chain endorsed the digest of the complete row, so a
    # crash between split UPDATEs would leave verified trust on content the
    # chain never endorsed.
    e = make_entry()
    store.add(e)
    statements = []
    store._conn.set_trace_callback(
        lambda sql: statements.append(sql) if sql.lstrip().startswith("UPDATE") else None
    )
    updated = store.set_verification(
        e.id, Verification.VERIFIED, "run-42", NOW,
        snapshot_ref="def456", title="Canonical title", kind=Kind.ACCEPTANCE,
    )
    store._conn.set_trace_callback(None)

    assert len(statements) == 1
    assert updated.trust.verification is Verification.VERIFIED
    assert updated.source.snapshot_ref == "def456"
    assert updated.title == "Canonical title"
    assert updated.kind is Kind.ACCEPTANCE


def test_strong_evidence_grading():
    draft = make_entry()
    assert not draft.is_strong_evidence()
    approved = make_entry(trust=Trust(curation=Curation.APPROVED))
    assert approved.is_strong_evidence()
    verified = make_entry(
        trust=Trust(verification=Verification.VERIFIED, verified_at=NOW, verified_by="run-1")
    )
    assert verified.is_strong_evidence()


def test_trust_invariants():
    with pytest.raises(ValueError):
        Trust(verification=Verification.VERIFIED)
    with pytest.raises(ValueError):
        Trust(verified_at=NOW, verified_by="run-1")


def test_add_dedupes_same_claim_same_file_across_commits(store):
    first = make_entry()
    stored = store.add(first)
    assert stored is first

    again = make_entry(
        source=Source(
            channel=Channel.CODE, locator="src/api/upload.py@def456", snapshot_ref="def456"
        )
    )
    deduped = store.add(again)
    assert deduped.id == first.id
    assert store.get(again.id) is None
    assert len(store.list()) == 1


def test_add_keeps_same_claim_from_different_files(store):
    a = make_entry()
    b = make_entry(
        source=Source(
            channel=Channel.CODE, locator="src/api/v2/upload.py@abc123", snapshot_ref="abc123"
        )
    )
    store.add(a)
    store.add(b)
    assert len(store.list()) == 2


def test_add_keeps_same_claim_from_same_path_in_different_repositories(store):
    root = make_entry()
    backend = make_entry(
        source=Source(
            channel=Channel.CODE,
            locator="repo:backend/src/api/upload.py@abc123",
            snapshot_ref="abc123",
        )
    )

    store.add(root)
    store.add(backend)

    assert len(store.list()) == 2


def test_add_dedupes_web_entries_by_full_locator(store):
    a = make_entry(
        source=Source(channel=Channel.WEB, locator="http://x/upload", snapshot_ref="h1")
    )
    same_page = make_entry(
        source=Source(channel=Channel.WEB, locator="http://x/upload", snapshot_ref="h2")
    )
    other_page = make_entry(
        source=Source(channel=Channel.WEB, locator="http://x/v2/upload", snapshot_ref="h1")
    )
    store.add(a)
    assert store.add(same_page).id == a.id
    store.add(other_page)
    assert len(store.list()) == 2


def test_reanchor_updates_locator_with_snapshot(store):
    e = make_entry()
    store.add(e)
    updated = store.set_snapshot_ref(
        e.id, "def456", NOW, locator="src/api/upload.py@def456"
    )
    assert updated.source.snapshot_ref == "def456"
    assert updated.source.locator == "src/api/upload.py@def456"


def test_reanchor_refreshes_source_evidence_location(store):
    e = make_entry(
        source=Source(
            channel=Channel.CODE,
            locator="src/api/upload.py@abc123",
            snapshot_ref="abc123",
            symbol="old_upload",
            line_start=4,
            line_end=8,
            excerpt="old source",
        )
    )
    store.add(e)
    refreshed_source = Source(
        channel=Channel.CODE,
        locator="src/api/upload.py@def456",
        snapshot_ref="def456",
        symbol="upload",
        line_start=20,
        line_end=26,
        excerpt="new source",
    )

    updated = store.set_snapshot_ref(
        e.id,
        "def456",
        NOW,
        locator=refreshed_source.locator,
        evidence_source=refreshed_source,
    )

    assert updated.source == refreshed_source


def test_links(store):
    old = make_entry()
    new = make_entry(title="Upload API v2", content="POST /v2/upload.")
    store.add(old)
    store.add(new)
    store.add_link(Link(from_id=new.id, to_id=old.id, link_type=LinkType.SUPERSEDES))
    links = store.links_for(old.id)
    assert len(links) == 1
    assert links[0].link_type is LinkType.SUPERSEDES
    with pytest.raises(KeyError):
        store.add_link(Link(from_id=new.id, to_id="missing", link_type=LinkType.CONTRADICTS))
    with pytest.raises(ValueError):
        Link(from_id=new.id, to_id=new.id, link_type=LinkType.CONTRADICTS)


def test_list_active_excludes_rejected_and_superseded(store):
    rejected = make_entry(title="Rejected fact", content="Wrong claim.")
    old = make_entry(title="Old contract", content="POST /upload returns 200.")
    new = make_entry(title="New contract", content="POST /upload returns 201.")
    for e in (rejected, old, new):
        store.add(e)
    store.set_curation(rejected.id, Curation.REJECTED, NOW)
    store.add_link(Link(from_id=new.id, to_id=old.id, link_type=LinkType.SUPERSEDES))

    active_ids = {e.id for e in store.list_active()}
    assert active_ids == {new.id}
    assert store.superseded_ids() == {old.id}
    # the superseded entry remains in the store as history
    assert store.get(old.id) is not None


def test_add_link_is_idempotent(store):
    old = make_entry(title="Old", content="a.")
    new = make_entry(title="New", content="b.")
    store.add(old)
    store.add(new)
    link = Link(from_id=new.id, to_id=old.id, link_type=LinkType.SUPERSEDES)
    store.add_link(link)
    store.add_link(link)
    assert len(store.links_for(old.id)) == 1
