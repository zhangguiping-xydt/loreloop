from __future__ import annotations

from loreloop.knowledge.authoritative_records import (
    ConfigurationRecord,
    DependencyRecord,
    DetectionReport,
    SourceRef,
)
from loreloop.knowledge.authoritative_report_normalize import normalize_detection_report


def test_normalization_keeps_one_real_source_for_repeated_semantic_fact() -> None:
    first = SourceRef(".", "a.ts", 1)
    second = SourceRef(".", "b.ts", 2)
    report = DetectionReport(
        dependencies=(
            DependencyRecord("react", "^19", "typescript_import", first),
            DependencyRecord("react", "^19", "typescript_import", second),
        ),
        configurations=(
            ConfigurationRecord("API_URL", None, True, False, first),
            ConfigurationRecord("API_URL", None, True, False, second),
        ),
    )

    normalized = normalize_detection_report(report)

    assert normalized.dependencies == report.dependencies[:1]
    assert normalized.configurations == report.configurations[:1]
