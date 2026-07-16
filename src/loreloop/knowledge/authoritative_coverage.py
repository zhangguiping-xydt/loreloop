"""Human-readable coverage summary for deterministic authoritative export."""

from __future__ import annotations

from collections import Counter
from pathlib import PurePosixPath

from .authoritative_records import DetectionReport
from .authoritative_source import (
    SnapshotBlob,
    _semantic_path_candidate,
    detector_profile,
    excluded_semantic_source,
    source_text_encoding,
)
from .authoritative_detector_tests import is_supported_test_evidence_path
from .authoritative_types import SourceSnapshot


def render_coverage_summary(
    snapshot: SourceSnapshot,
    blobs: tuple[SnapshotBlob, ...],
    report: DetectionReport,
    document_count: int,
) -> str:
    """Explain repository/file coverage and facts without implying unsupported semantics."""
    by_repository = Counter(blob.repository_alias for blob in blobs)
    decode_gaps = tuple(
        blob
        for blob in blobs
        if blob.data is not None
        and _semantic_path_candidate(blob.path)
        and not excluded_semantic_source(blob.path)
        and source_text_encoding(blob) is None
    )
    decode_gap_keys = {(blob.repository_alias, blob.path) for blob in decode_gaps}
    profiles = Counter(profile for blob in blobs if (profile := detector_profile(blob)) is not None)
    unsupported = Counter(
        PurePosixPath(blob.path).suffix.lower() or "[no extension]"
        for blob in blobs
        if detector_profile(blob) is None
        and not excluded_semantic_source(blob.path)
        and (blob.repository_alias, blob.path) not in decode_gap_keys
    )
    excluded = sum(
        excluded_semantic_source(blob.path) and not is_supported_test_evidence_path(blob.path)
        for blob in blobs
    )
    encodings = Counter(
        encoding
        for blob in blobs
        if detector_profile(blob) is not None
        and (encoding := source_text_encoding(blob)) is not None
    )
    lines = [
        "authoritative export coverage:",
        f"  repositories: {len(snapshot.repositories)}; committed blobs: {len(blobs)}; "
        f"detector-inspected: {sum(profiles.values())}; fixture/generated excluded: {excluded}",
    ]
    roles = {item.alias: item.role for item in snapshot.repositories}
    for alias in (item.alias for item in snapshot.repositories):
        lines.append(f"  - {alias} ({roles[alias]}): {by_repository[alias]} blobs")
    if profiles:
        lines.append(
            "  detector profiles: "
            + ", ".join(f"{name}={count}" for name, count in sorted(profiles.items()))
        )
    if encodings:
        lines.append(
            "  source encodings: "
            + ", ".join(f"{name}={count}" for name, count in sorted(encodings.items()))
            + " (original blob bytes preserved)"
        )
    if decode_gaps:
        examples = ", ".join(f"{blob.repository_alias}:{blob.path}" for blob in decode_gaps[:5])
        suffix = "" if len(decode_gaps) <= 5 else f", ... +{len(decode_gaps) - 5}"
        lines.append(
            f"  source decode gaps: {len(decode_gaps)} "
            f"(original bytes preserved; semantic parsing skipped): {examples}{suffix}"
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
                f"ui_surfaces={len(report.ui_surfaces)}",
                f"test_suites={len(report.tests)}",
                f"governed_web={len(report.web_knowledge)}",
                f"configurations={len(report.configurations)}",
                f"dependencies={len(report.dependencies)}",
                f"implementation_facts={len(report.implementation_facts)}",
                f"tables={len(report.tables)}",
                f"indexes={len(report.indexes)}",
                f"source_issues={len(report.source_issues)}",
            )
        )
    )
    lines.append(f"  documents: {document_count} (6 core + evidence-backed optional)")
    if unsupported:
        summary = ", ".join(f"{suffix}={count}" for suffix, count in unsupported.most_common(8))
        lines.append(f"  not semantically parsed (top suffixes): {summary}")
    return "\n".join(lines)
