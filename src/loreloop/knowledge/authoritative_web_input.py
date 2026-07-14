"""Convert governed Web knowledge into bounded SemanticCore input records."""

from __future__ import annotations

import hashlib
import json
from typing import cast
from urllib.parse import urlsplit

from .authoritative_records import (
    DetectionError,
    DetectionReport,
    SourceRef,
    WebKnowledgeKind,
    WebKnowledgeRecord,
)
from .authoritative_source import SnapshotBlob
from .model import Channel, Entry

MAX_WEB_ENTRIES = 10_000
MAX_WEB_ENTRY_BYTES = 1024 * 1024
MAX_WEB_TOTAL_BYTES = 64 * 1024 * 1024


def build_governed_web_input(
    entries: tuple[Entry, ...],
) -> tuple[DetectionReport, tuple[SnapshotBlob, ...]]:
    """Bind reviewed Web assertions to synthetic immutable evidence blobs."""
    if len(entries) > MAX_WEB_ENTRIES:
        raise DetectionError(f"governed Web entry count exceeds {MAX_WEB_ENTRIES}")
    records: list[WebKnowledgeRecord] = []
    blobs: list[SnapshotBlob] = []
    total = 0
    for entry in sorted(entries, key=lambda item: item.id):
        if entry.source.channel is not Channel.WEB:
            raise DetectionError(f"non-Web entry passed to Web baseline input: {entry.id}")
        parsed = urlsplit(entry.source.locator)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise DetectionError(f"governed Web entry has an invalid URL: {entry.id}")
        if not entry.source.snapshot_ref:
            raise DetectionError(f"verified Web entry lacks a snapshot reference: {entry.id}")
        payload = {
            "entry_id": entry.id,
            "kind": entry.kind.value,
            "title": entry.title,
            "statement": entry.content,
            "locator": entry.source.locator,
            "snapshot_ref": entry.source.snapshot_ref,
        }
        data = (
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        if len(data) > MAX_WEB_ENTRY_BYTES:
            raise DetectionError(
                f"governed Web entry exceeds {MAX_WEB_ENTRY_BYTES} bytes: {entry.id}"
            )
        total += len(data)
        if total > MAX_WEB_TOTAL_BYTES:
            raise DetectionError(
                f"governed Web evidence exceeds {MAX_WEB_TOTAL_BYTES} total bytes"
            )
        safe_id = hashlib.sha256(entry.id.encode("utf-8")).hexdigest()
        path = f"web/{safe_id}.json"
        source = SourceRef("@web", path, 1)
        records.append(
            WebKnowledgeRecord(
                entry.id,
                cast(WebKnowledgeKind, entry.kind.value),
                entry.title,
                entry.content,
                entry.source.locator,
                entry.source.snapshot_ref,
                source,
            )
        )
        blobs.append(
            SnapshotBlob(
                "@web",
                path,
                data,
                hashlib.sha256(data).hexdigest(),
                len(data),
            )
        )
    return DetectionReport(web_knowledge=tuple(records)), tuple(blobs)
