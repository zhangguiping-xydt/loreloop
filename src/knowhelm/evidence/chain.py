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

import fcntl
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


class LegacyKeyError(Exception):
    """A pre-relocation key was found inside the project tree. Refuse to
    proceed rather than mint a new key: the old chain would then fail with
    "signature invalid", which falsely accuses tampering when the truth is
    a key change. The operator decides — never migrate silently, because a
    key that lived in the agent-writable tree cannot be laundered into the
    trusted location as if it had always been there."""

    def __init__(self, legacy: Path, expected: Path) -> None:
        super().__init__(
            f"found a legacy evidence key inside the project tree: {legacy}\n"
            f"Evidence keys now live outside the project (agent-writable trees "
            f"cannot hold the referee's stamp). Choose:\n"
            f"  keep the old chain verifiable:\n"
            f"    mkdir -p -m 700 {expected.parent} && mv {legacy} {expected}\n"
            f"    (note: that key lived inside the project tree, so the old "
            f"chain only ever had in-tree integrity)\n"
            f"  or start fresh:  delete {legacy} and archive the old "
            f".knowhelm/evidence.jsonl"
        )


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
        # Head commitment lives NEXT TO THE KEY, outside the agent-writable
        # tree. A hash chain alone cannot detect tail truncation: every prefix
        # of a valid chain is itself a valid chain. Committing the latest
        # (index, chain_hash) outside the tree closes that hole — deleting the
        # trailing check_failed record now breaks verification.
        self._head_path = key_path.with_suffix(".head")
        self._path.parent.mkdir(parents=True, exist_ok=True)

    @classmethod
    def for_workdir(cls, workdir: Path) -> "EvidenceChain":
        base = workdir / ".knowhelm"
        expected = key_path_for(workdir)
        legacy = base / "evidence.key"
        if legacy.exists() and not expected.exists():
            raise LegacyKeyError(legacy, expected)
        return cls(base / "evidence.jsonl", expected)

    def append(self, event: str, payload: dict[str, Any]) -> EvidenceRecord:
        # Cross-process exclusive lock: read-then-append without it lets two
        # writers mint the same index and fork the chain.
        lock_path = self._path.with_suffix(".lock")
        with lock_path.open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            # Verify BEFORE extending: append is the only writer of the head
            # commitment. Extending whatever is on disk unchecked would let
            # the next legitimate append build on a truncated prefix and then
            # overwrite the head — an honest operation blessing the tampering.
            # Refusing here leaves the old head in place as standing evidence.
            records = self._verify_records(self._read())
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
                fh.flush()
                os.fsync(fh.fileno())
            self._commit_head(record)
        return record

    def verify(self) -> list[EvidenceRecord]:
        """Return all records; raise ChainVerificationError on any tampering."""
        return self._verify_records(self._read())

    def _verify_records(self, records: list[EvidenceRecord]) -> list[EvidenceRecord]:
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
        self._check_head(records)
        return records

    def _commit_head(self, record: EvidenceRecord) -> None:
        # fsync file and directory: a crash right after append must not leave
        # the head commitment pointing at an older record, or the window
        # becomes a licensed truncation.
        head = {"index": record.index, "chain_hash": record.chain_hash}
        tmp = self._head_path.with_suffix(".head.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            fh.write(json.dumps(head))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self._head_path)
        dir_fd = os.open(self._head_path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)

    def _check_head(self, records: list[EvidenceRecord]) -> None:
        """Truncation check: every prefix of a valid chain is itself valid, so
        hash-chain verification alone accepts a chain whose tail was deleted.
        The out-of-tree head commitment pins the record the chain must still
        contain. A missing commitment file is tolerated (pre-upgrade chains);
        it appears on the next append."""
        if not self._head_path.exists():
            return
        head = json.loads(self._head_path.read_text(encoding="utf-8"))
        index, chain_hash = head["index"], head["chain_hash"]
        if len(records) <= index:
            raise ChainVerificationError(
                index,
                f"chain has {len(records)} records but the head commitment "
                f"requires record {index} — the tail was truncated. (If you "
                f"deliberately reset this project, also remove "
                f"{self._head_path})",
            )
        if records[index].chain_hash != chain_hash:
            raise ChainVerificationError(index, "record does not match the head commitment")

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
    try:
        fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        # Two processes raced to create the key; the winner's key is the key.
        return key_path.read_bytes()
    with os.fdopen(fd, "wb") as fh:
        fh.write(key)
    return key
