from __future__ import annotations

from loreloop.knowledge.authoritative_coverage import render_coverage_summary
from loreloop.knowledge.authoritative_records import DetectionReport, InterfaceRecord, SourceRef
from loreloop.knowledge.authoritative_source import SnapshotBlob
from loreloop.knowledge.authoritative_types import (
    GitObjectId,
    RepositorySnapshot,
    SnapshotEntry,
    SourceSnapshot,
)


def test_coverage_summary_distinguishes_inspected_and_unsupported_files() -> None:
    entries = (
        SnapshotEntry("app.py", "100644", GitObjectId.parse("sha1:" + "1" * 40), 1, "2" * 64),
        SnapshotEntry("notes.xyz", "100644", GitObjectId.parse("sha1:" + "3" * 40), 1, "4" * 64),
    )
    snapshot = SourceSnapshot(
        (
            RepositorySnapshot(
                ".",
                "root",
                GitObjectId.parse("sha1:" + "5" * 40),
                GitObjectId.parse("sha1:" + "6" * 40),
                "7" * 64,
                entries,
                "8" * 64,
            ),
        )
    )
    blobs = (
        SnapshotBlob(".", "app.py", b"x", "2" * 64),
        SnapshotBlob(".", "notes.xyz", b"x", "4" * 64),
    )
    report = DetectionReport(
        interfaces=(InterfaceRecord("http", "health", "GET", "/", (), None, SourceRef(".", "app.py", 1)),)
    )

    summary = render_coverage_summary(snapshot, blobs, report, 6)

    assert "repositories: 1; committed blobs: 2; detector-inspected: 1" in summary
    assert "interfaces=1" in summary
    assert ".xyz=1" in summary
