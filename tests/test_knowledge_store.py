from datetime import datetime, timezone

import pytest

from knowhelm.knowledge.model import (
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
from knowhelm.knowledge.store import InvalidTransition, KnowledgeStore

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
