import json

import pytest

from knowhelm.evidence.chain import ChainVerificationError, EvidenceChain


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
    lines = [json.loads(l) for l in path.read_text().splitlines()]
    mutate(lines)
    path.write_text("\n".join(json.dumps(l, sort_keys=True) for l in lines) + "\n")


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
    key_path = tmp_path / ".knowhelm/evidence.key"
    assert key_path.exists()
    assert (key_path.stat().st_mode & 0o777) == 0o600
    first = key_path.read_bytes()
    EvidenceChain.for_workdir(tmp_path)
    assert key_path.read_bytes() == first
