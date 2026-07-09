import json

import pytest

from knowhelm.evidence.chain import (
    ChainVerificationError,
    EvidenceChain,
    LegacyKeyError,
    key_path_for,
)


@pytest.fixture()
def chain(tmp_path):
    return EvidenceChain.for_workdir(tmp_path)


def test_append_and_verify_roundtrip(chain):
    chain.append("run_started", {"run_id": "run-1", "task": "fix upload"})
    chain.append("check_passed", {"check": "upload returns 201"})
    records = chain.verify()
    assert [r.event for r in records] == ["run_started", "check_passed"]
    assert records[0].prev_hash == "genesis"
    assert records[1].prev_hash == records[0].chain_hash


def test_empty_chain_verifies(chain):
    assert chain.verify() == []


def _rewrite(tmp_path, mutate):
    path = tmp_path / ".knowhelm/evidence.jsonl"
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    mutate(lines)
    path.write_text("\n".join(json.dumps(rec, sort_keys=True) for rec in lines) + "\n")


def test_detects_payload_edit(chain, tmp_path):
    chain.append("check_passed", {"check": "original"})
    _rewrite(tmp_path, lambda ls: ls[0]["payload"].update({"check": "forged"}))
    with pytest.raises(ChainVerificationError, match="record 0.*modified"):
        chain.verify()


def test_detects_deletion(chain, tmp_path):
    chain.append("a", {})
    chain.append("b", {})
    _rewrite(tmp_path, lambda ls: ls.pop(0))
    with pytest.raises(ChainVerificationError, match="record 0"):
        chain.verify()


def test_detects_forged_record_without_key(chain, tmp_path):
    rec = chain.append("check_passed", {"check": "real"})
    forged = {
        "index": 1,
        "ts": rec.ts,
        "event": "check_passed",
        "payload": {"check": "forged"},
        "prev_hash": rec.chain_hash,
        "chain_hash": "0" * 64,
        "signature": "hmac-sha256:" + "0" * 64,
    }
    path = tmp_path / ".knowhelm/evidence.jsonl"
    with path.open("a") as fh:
        fh.write(json.dumps(forged, sort_keys=True) + "\n")
    with pytest.raises(ChainVerificationError, match="record 1"):
        chain.verify()


def test_key_created_once_with_restrictive_mode(tmp_path):
    EvidenceChain.for_workdir(tmp_path)
    key_path = key_path_for(tmp_path)
    assert key_path.exists()
    assert (key_path.stat().st_mode & 0o777) == 0o600
    assert (key_path.parent.stat().st_mode & 0o777) == 0o700
    first = key_path.read_bytes()
    EvidenceChain.for_workdir(tmp_path)
    assert key_path.read_bytes() == first


def test_key_lives_outside_the_project_tree(tmp_path):
    EvidenceChain.for_workdir(tmp_path)
    key_path = key_path_for(tmp_path)
    assert not key_path.is_relative_to(tmp_path)
    assert not (tmp_path / ".knowhelm/evidence.key").exists()


def test_legacy_in_tree_key_refuses_instead_of_accusing_tampering(tmp_path):
    legacy = tmp_path / ".knowhelm/evidence.key"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"k" * 32)
    with pytest.raises(LegacyKeyError, match="legacy evidence key"):
        EvidenceChain.for_workdir(tmp_path)


def test_operator_moved_legacy_key_keeps_old_chain_verifiable(tmp_path):
    # sign a chain the pre-relocation way: key inside the project tree
    legacy = tmp_path / ".knowhelm/evidence.key"
    legacy.parent.mkdir(parents=True)
    old = EvidenceChain(tmp_path / ".knowhelm/evidence.jsonl", legacy)
    old.append("check_passed", {"check": "history"})

    # operator chooses continuity: mv <legacy> <expected>
    expected = key_path_for(tmp_path)
    expected.parent.mkdir(parents=True, exist_ok=True)
    legacy.rename(expected)

    chain = EvidenceChain.for_workdir(tmp_path)
    assert [r.event for r in chain.verify()] == ["check_passed"]


def test_detects_tail_truncation(chain, tmp_path):
    # every prefix of a valid chain is itself a valid chain — only the
    # out-of-tree head commitment catches a deleted trailing record
    chain.append("check_passed", {"check": "looks fine"})
    chain.append("check_failed", {"check": "the one the agent wants gone"})
    _rewrite(tmp_path, lambda ls: ls.pop())
    with pytest.raises(ChainVerificationError, match="truncated"):
        chain.verify()


def test_detects_full_chain_replacement(chain, tmp_path):
    chain.append("check_failed", {"check": "inconvenient"})
    (tmp_path / ".knowhelm/evidence.jsonl").unlink()
    fresh = EvidenceChain.for_workdir(tmp_path)
    with pytest.raises(ChainVerificationError):
        fresh.verify()


def test_head_commitment_lives_outside_project_tree(chain, tmp_path):
    chain.append("check_passed", {})
    head = key_path_for(tmp_path).with_suffix(".head")
    assert head.exists()
    assert not head.is_relative_to(tmp_path)


def test_missing_head_commitment_is_tolerated_for_preupgrade_chains(chain, tmp_path):
    chain.append("check_passed", {})
    key_path_for(tmp_path).with_suffix(".head").unlink()
    assert len(chain.verify()) == 1


def test_key_is_per_project(tmp_path):
    a = tmp_path / "proj-a"
    b = tmp_path / "proj-b"
    a.mkdir()
    b.mkdir()
    EvidenceChain.for_workdir(a)
    EvidenceChain.for_workdir(b)
    assert key_path_for(a) != key_path_for(b)
    assert key_path_for(a).read_bytes() != key_path_for(b).read_bytes()
