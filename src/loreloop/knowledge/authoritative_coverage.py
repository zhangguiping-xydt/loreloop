"""Human-readable coverage summary for deterministic authoritative export."""

from __future__ import annotations

from collections import Counter
from pathlib import PurePosixPath

from .authoritative_records import DetectionReport
from .authoritative_source import SnapshotBlob, detector_profile, excluded_semantic_source
from .authoritative_types import SourceSnapshot


def render_coverage_summary(
    snapshot: SourceSnapshot,
    blobs: tuple[SnapshotBlob, ...],
    report: DetectionReport,
    document_count: int,
) -> str:
    """Explain repository/file coverage and facts without implying unsupported semantics."""
    by_repository = Counter(blob.repository_alias for blob in blobs)
    profiles = Counter(
        profile for blob in blobs if (profile := detector_profile(blob)) is not None
    )
    unsupported = Counter(
        PurePosixPath(blob.path).suffix.lower() or "[no extension]"
        for blob in blobs
        if detector_profile(blob) is None and not excluded_semantic_source(blob.path)
    )
    excluded = sum(excluded_semantic_source(blob.path) for blob in blobs)
    lines = [
        "authoritative export coverage:",
        f"  repositories: {len(snapshot.repositories)}; committed blobs: {len(blobs)}; "
        f"detector-inspected: {sum(profiles.values())}; test/generated excluded: {excluded}",
    ]
    roles = {item.alias: item.role for item in snapshot.repositories}
    for alias in (item.alias for item in snapshot.repositories):
        lines.append(f"  - {alias} ({roles[alias]}): {by_repository[alias]} blobs")
    if profiles:
        lines.append(
            "  detector profiles: "
            + ", ".join(f"{name}={count}" for name, count in sorted(profiles.items()))
        )
    lines.append(
        "  facts: "
        + ", ".join(
            (
                f"interfaces={len(report.interfaces)}",
                f"symbols={len(report.symbols)}",
                f"requirements={len(report.requirements)}",
                f"acceptance={len(report.acceptances)}",
                f"permissions={len(report.permissions)}",
                f"configurations={len(report.configurations)}",
                f"dependencies={len(report.dependencies)}",
                f"tables={len(report.tables)}",
                f"indexes={len(report.indexes)}",
            )
        )
    )
    lines.append(f"  documents: {document_count} (6 core + evidence-backed optional)")
    if unsupported:
        summary = ", ".join(
            f"{suffix}={count}" for suffix, count in unsupported.most_common(8)
        )
        lines.append(f"  not semantically parsed (top suffixes): {summary}")
    return "\n".join(lines)
