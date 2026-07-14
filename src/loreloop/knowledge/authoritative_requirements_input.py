"""Parse committed Markdown requirement materials without model inference."""

from __future__ import annotations

import re
from typing import Protocol

from .authoritative_records import (
    AcceptanceRecord,
    DetectionError,
    DetectionReport,
    RequirementRecord,
    SourceRef,
    merge_reports,
)


class RequirementBlob(Protocol):
    @property
    def repository_alias(self) -> str: ...

    @property
    def path(self) -> str: ...

    @property
    def data(self) -> bytes | None: ...


_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_BULLET = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)(.+?)\s*$")
_EXPLICIT = re.compile(
    r"^(?P<id>(?:REQ|FR|NFR)-[A-Za-z0-9._-]+)\s*[:：-]\s*(?P<text>.+)$", re.IGNORECASE
)
_TABLE_SEPARATOR = re.compile(r"^:?-{3,}:?$")


def _cells(line: str) -> tuple[str, ...]:
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return ()
    return tuple(cell.strip() for cell in stripped[1:-1].split("|"))


def _header_key(value: str) -> str:
    normalized = re.sub(r"[\s_-]+", "", value).lower()
    aliases = {
        "id": "id",
        "编号": "id",
        "需求id": "id",
        "requirementid": "id",
        "需求": "requirement",
        "需求描述": "requirement",
        "requirement": "requirement",
        "description": "requirement",
        "验收": "acceptance",
        "验收标准": "acceptance",
        "acceptance": "acceptance",
        "acceptancecriteria": "acceptance",
        "优先级": "priority",
        "priority": "priority",
        "角色": "role",
        "role": "role",
        "标题": "title",
        "title": "title",
    }
    return aliases.get(normalized, normalized)


def _table(
    lines: list[str],
    start: int,
    alias: str,
    path: str,
) -> tuple[list[RequirementRecord], list[AcceptanceRecord], int] | None:
    headers = _cells(lines[start])
    separators = _cells(lines[start + 1]) if start + 1 < len(lines) else ()
    if (
        not headers
        or len(headers) != len(separators)
        or not all(_TABLE_SEPARATOR.fullmatch(cell) for cell in separators)
    ):
        return None
    keys = tuple(_header_key(header) for header in headers)
    if "requirement" not in keys and "acceptance" not in keys:
        return None
    requirements: list[RequirementRecord] = []
    acceptances: list[AcceptanceRecord] = []
    index = start + 2
    while index < len(lines):
        values = _cells(lines[index])
        if len(values) != len(keys):
            break
        row = dict(zip(keys, values, strict=True))
        source = SourceRef(alias, path, index + 1)
        external_id = row.get("id") or None
        statement = row.get("requirement", "").strip()
        if statement:
            requirements.append(
                RequirementRecord(
                    external_id,
                    row.get("title") or None,
                    statement,
                    row.get("priority") or None,
                    row.get("role") or None,
                    source,
                )
            )
        acceptance = row.get("acceptance", "").strip()
        if acceptance:
            acceptances.append(AcceptanceRecord(external_id, acceptance, source))
        index += 1
    return requirements, acceptances, index


def _category(heading: str) -> str | None:
    lowered = heading.lower()
    if "验收" in heading or "acceptance" in lowered:
        return "acceptance"
    if any(token in heading for token in ("需求", "功能", "约束")) or any(
        token in lowered for token in ("requirement", "feature", "constraint")
    ):
        return "requirement"
    return None


def detect_requirement_markdown(text: str, alias: str, path: str) -> DetectionReport:
    """Extract requirement and acceptance statements from common Markdown shapes."""
    lines = text.splitlines()
    requirements: list[RequirementRecord] = []
    acceptances: list[AcceptanceRecord] = []
    heading: str | None = None
    category: str | None = None
    index = 0
    while index < len(lines):
        parsed_table = _table(lines, index, alias, path)
        if parsed_table is not None:
            table_requirements, table_acceptances, index = parsed_table
            requirements.extend(table_requirements)
            acceptances.extend(table_acceptances)
            continue
        heading_match = _HEADING.match(lines[index])
        if heading_match is not None:
            heading = heading_match.group(2).strip()
            category = _category(heading_match.group(2).strip())
            index += 1
            continue
        raw = lines[index].strip()
        explicit = _EXPLICIT.match(raw)
        bullet = _BULLET.match(lines[index])
        statement = (
            explicit.group("text")
            if explicit is not None
            else (bullet.group(1) if bullet is not None else None)
        )
        if statement is not None and (category is not None or explicit is not None):
            source = SourceRef(alias, path, index + 1)
            if category == "acceptance":
                acceptances.append(
                    AcceptanceRecord(
                        explicit.group("id") if explicit is not None else None,
                        statement,
                        source,
                    )
                )
            else:
                requirements.append(
                    RequirementRecord(
                        explicit.group("id") if explicit is not None else None,
                        heading,
                        statement,
                        None,
                        None,
                        source,
                    )
                )
        index += 1
    if not requirements and not acceptances:
        raise DetectionError(f"requirement material contains no structured statements: {path}")
    return DetectionReport(requirements=tuple(requirements), acceptances=tuple(acceptances))


def _locator(locator: str) -> tuple[str, str]:
    if locator.startswith("repo:"):
        alias, separator, path = locator[5:].partition("/")
        if not separator or not alias or not path:
            raise DetectionError(f"invalid requirement locator: {locator}")
        return alias, path
    if not locator or locator.startswith("/"):
        raise DetectionError(f"invalid requirement locator: {locator}")
    return ".", locator


def detect_requirement_materials(
    blobs: tuple[RequirementBlob, ...],
    locators: tuple[str, ...],
) -> DetectionReport:
    """Resolve explicit locators against exact snapshot blobs and parse each once."""
    by_key = {(blob.repository_alias, blob.path): blob for blob in blobs}
    reports: list[DetectionReport] = []
    for locator in locators:
        alias, path = _locator(locator)
        blob = by_key.get((alias, path))
        if blob is None:
            raise DetectionError(f"requirement material is not in the Git snapshot: {locator}")
        if blob.data is None:
            raise DetectionError(f"requirement material exceeds semantic loading limits: {locator}")
        try:
            text = blob.data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise DetectionError(f"requirement material is not UTF-8: {locator}") from exc
        reports.append(detect_requirement_markdown(text, alias, path))
    return merge_reports(*reports)
