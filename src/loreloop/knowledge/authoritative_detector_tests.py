"""Bounded static extraction of test-case evidence from committed test files."""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Final, Literal

from .authoritative_records import DetectionReport, SourceRef, TestRecord

_NON_PRODUCT_TEST_SEGMENTS: Final = frozenset(
    {"fixtures", "fixture", "snapshots", "__snapshots__", "generated"}
)
_PYTHON_TEST: Final = re.compile(r"(?m)^\s*(?:async\s+)?def\s+(?P<name>test_[A-Za-z0-9_]+)\s*\(")
_JAVASCRIPT_TEST: Final = re.compile(
    r"\b(?:it|test)\s*\(\s*(['\"`])(?P<name>[^'\"`\r\n]{1,512})\1"
)
_GO_TEST: Final = re.compile(r"(?m)^\s*func\s+(?P<name>Test[A-Za-z0-9_]+)\s*\(")
_ANNOTATED_METHOD: Final = re.compile(
    r"(?ms)^\s*\[(?:Fact|Theory|Test|TestMethod)(?:\([^\]\r\n]*\))?\]\s*"
    r"(?:public|private|protected|internal|static|async|virtual|override|sealed|\s)+"
    r"[A-Za-z_$][\w$<>,.?\[\]\s]*\s+(?P<name>[A-Za-z_$][\w$]*)\s*\("
)
_JVM_TEST_ANNOTATION: Final = re.compile(
    r"(?m)^\s*@(?:Test|ParameterizedTest|RepeatedTest)(?:\([^\r\n]*\))?\s*$"
)
_JVM_METHOD: Final = re.compile(
    r"(?m)^\s*(?:(?:public|private|protected|static|final|suspend|open)\s+)*"
    r"(?:fun\s+)?(?:[A-Za-z_$][\w$<>,.?\[\]]*\s+)?"
    r"(?P<name>[A-Za-z_$][\w$]*)\s*\("
)


def is_test_evidence_path(path: str) -> bool:
    """Return whether a path explicitly denotes a test rather than a fixture."""
    pure = PurePosixPath(path.lower())
    if any(part in _NON_PRODUCT_TEST_SEGMENTS for part in pure.parts[:-1]):
        return False
    name = pure.name
    return any(part in {"test", "tests", "__tests__"} for part in pure.parts[:-1]) or bool(
        re.fullmatch(
            r"(?:test_.*\.py|.*_test\.go|.*(?:[._-](?:test|tests|spec))\.[^.]+|.*tests?\.(?:java|kt|cs))",
            name,
            re.IGNORECASE,
        )
    )


def is_supported_test_evidence_path(path: str) -> bool:
    lower = path.lower()
    return is_test_evidence_path(path) and lower.endswith(
        (".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".kt", ".go", ".cs")
    )


def _line(source: str, offset: int) -> int:
    return source.count("\n", 0, offset) + 1


def _scope(source: str) -> Literal["unit", "integration", "unknown"]:
    lowered = source.lower()
    if any(
        marker in lowered
        for marker in (
            "@springboottest",
            "@webmvctest",
            "testserver",
            "integrationtest",
            "integration_test",
        )
    ):
        return "integration"
    return "unit"


def _framework(source: str, path: str) -> str:
    lower = path.lower()
    if lower.endswith((".java", ".kt")):
        if "org.testng" in source:
            return "testng"
        if "org.junit.jupiter" in source:
            return "junit5"
        return "junit"
    if lower.endswith(".py"):
        return "pytest"
    if lower.endswith((".js", ".jsx", ".ts", ".tsx")):
        if "vitest" in source:
            return "vitest"
        return "jest"
    if lower.endswith(".go"):
        return "go-test"
    if "[fact" in source.lower() or "xunit" in source.lower():
        return "xunit"
    if "[testmethod" in source.lower():
        return "mstest"
    return "nunit"


def _jvm_tests(source: str) -> tuple[tuple[int, str], ...]:
    records: list[tuple[int, str]] = []
    for annotation in _JVM_TEST_ANNOTATION.finditer(source):
        window = source[annotation.end() : annotation.end() + 1200]
        method = _JVM_METHOD.search(window)
        if method is not None:
            records.append((annotation.start(), method.group("name")))
    return tuple(records)


def detect_test_source(source: str, repository_alias: str, path: str) -> DetectionReport:
    """Extract test names and framework identity without running the tests."""
    lower = path.lower()
    if lower.endswith((".java", ".kt")):
        matches = _jvm_tests(source)
    else:
        pattern = (
            _PYTHON_TEST
            if lower.endswith(".py")
            else _JAVASCRIPT_TEST
            if lower.endswith((".js", ".jsx", ".ts", ".tsx"))
            else _GO_TEST
            if lower.endswith(".go")
            else _ANNOTATED_METHOD
        )
        matches = tuple((match.start(), match.group("name")) for match in pattern.finditer(source))
    framework = _framework(source, path)
    scope = _scope(source)
    return DetectionReport(
        tests=tuple(
            TestRecord(
                name,
                framework,
                scope,
                SourceRef(repository_alias, path, _line(source, offset)),
            )
            for offset, name in matches
        )
    )
