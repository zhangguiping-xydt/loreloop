"""Tamper-evident evidence chain.

Append-only JSONL where each record commits to its predecessor via a hash
chain, and each chain hash is HMAC-signed with a local secret. Verification
recomputes the whole chain; any edit, deletion, or reordering breaks it.

The secret lives OUTSIDE the project tree, in ``~/.knowhelm/keys/`` (one key
per project directory, created on first use; override the location with
``KNOWHELM_KEY_DIR``). Coding agents get write access to the project
directory as a matter of course — the referee's stamp must not sit inside
the player's sandbox. This protects against silent tampering by tools or
accidents, not against an attacker who owns the machine and the key — the
honest-workstation threat model is enough for acceptance evidence.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_GENESIS = "genesis"


class ChainVerificationError(Exception):
    def __init__(self, index: int, reason: str) -> None:
        super().__init__(f"evidence chain broken at record {index}: {reason}")
        self.index = index
        self.reason = reason


@dataclass(frozen=True)
class EvidenceRecord:
    index: int
    ts: str
    event: str
    payload: dict[str, Any]
    prev_hash: str
    chain_hash: str
    signature: str


class EvidenceChain:
    def __init__(self, chain_path: Path, key_path: Path) -> None:
        self._path = chain_path
        self._key = _load_or_create_key(key_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    @classmethod
    def for_workdir(cls, workdir: Path) -> "EvidenceChain":
        base = workdir / ".knowhelm"
        return cls(base / "evidence.jsonl", key_path_for(workdir))

    def append(self, event: str, payload: dict[str, Any]) -> EvidenceRecord:
        records = self._read()
        prev_hash = records[-1].chain_hash if records else _GENESIS
        index = len(records)
        ts = datetime.now(timezone.utc).isoformat()
        chain_hash = _chain_hash(prev_hash, index, ts, event, payload)
        record = EvidenceRecord(
            index=index,
            ts=ts,
            event=event,
            payload=payload,
            prev_hash=prev_hash,
            chain_hash=chain_hash,
            signature=self._sign(chain_hash),
        )
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record.__dict__, ensure_ascii=False, sort_keys=True) + "\n")
        return record

    def verify(self) -> list[EvidenceRecord]:
        """Return all records; raise ChainVerificationError on any tampering."""
        records = self._read()
        prev_hash = _GENESIS
        for i, rec in enumerate(records):
            if rec.index != i:
                raise ChainVerificationError(i, f"index mismatch (stored {rec.index})")
            if rec.prev_hash != prev_hash:
                raise ChainVerificationError(i, "prev_hash does not match predecessor")
            expected = _chain_hash(rec.prev_hash, rec.index, rec.ts, rec.event, rec.payload)
            if rec.chain_hash != expected:
                raise ChainVerificationError(i, "payload was modified")
            if not hmac.compare_digest(self._sign(rec.chain_hash), rec.signature):
                raise ChainVerificationError(i, "signature invalid")
            prev_hash = rec.chain_hash
        return records

    def _read(self) -> list[EvidenceRecord]:
        if not self._path.exists():
            return []
        records = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(EvidenceRecord(**json.loads(line)))
        return records

    def _sign(self, chain_hash: str) -> str:
        digest = hmac.new(self._key, chain_hash.encode(), hashlib.sha256).hexdigest()
        return f"hmac-sha256:{digest}"


def _chain_hash(prev_hash: str, index: int, ts: str, event: str, payload: dict[str, Any]) -> str:
    material = json.dumps(
        {"prev": prev_hash, "index": index, "ts": ts, "event": event, "payload": payload},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(material.encode()).hexdigest()


def key_path_for(workdir: Path) -> Path:
    """Per-project key file under the key dir, named by a hash of the
    project's absolute path so unrelated projects never share a key."""
    env = os.environ.get("KNOWHELM_KEY_DIR")
    key_dir = Path(env) if env else Path.home() / ".knowhelm/keys"
    digest = hashlib.sha256(str(workdir.resolve()).encode()).hexdigest()[:16]
    return key_dir / f"{digest}.key"


def _load_or_create_key(key_path: Path) -> bytes:
    if key_path.exists():
        return key_path.read_bytes()
    key_path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(key_path.parent, 0o700)
    key = secrets.token_bytes(32)
    key_path.write_bytes(key)
    os.chmod(key_path, 0o600)
    return key
