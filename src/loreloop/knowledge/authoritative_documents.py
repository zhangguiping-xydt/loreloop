"""Build and publish the flat six-plus-two authoritative Markdown set."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .authoritative_document_render import (
    acceptance_section,
    configuration_section,
    database_section,
    dependency_section,
    gap_section,
    interface_section,
    navigation,
    permission_section,
    repository_section,
    requirement_section,
    symbol_section,
)
from .authoritative_records import DetectionReport
from .authoritative_publish import PublicationError, publish_tree
from .authoritative_types import SourceSnapshot

_UNSAFE_NAME = re.compile(r"[^0-9A-Za-z._\-一-鿿]+")
_DOCUMENT_FAMILIES = (
    "功能清单",
    "需求规格",
    "系统架构",
    "详细设计",
    "用户手册",
    "验收规格",
    "接口契约",
    "数据库设计",
)


class SourceDocumentError(ValueError):
    """A source-derived document set cannot be built or published safely."""


@dataclass(frozen=True, slots=True)
class SourceDocument:
    filename: str
    content: str


def _project_name(value: str) -> str:
    safe = _UNSAFE_NAME.sub("-", value.strip()).strip(".-_")[:80]
    return safe or "project"


def source_document_filenames(project_name: str) -> tuple[str, ...]:
    project = _project_name(project_name)
    return tuple(f"{project}-{family}.md" for family in _DOCUMENT_FAMILIES)


def _content(title: str, paths: tuple[str, ...], *sections: list[str]) -> str:
    lines = [f"# {title}", "", *navigation(paths)]
    for section in sections:
        lines.extend(section)
    return "\n".join(lines).rstrip() + "\n"


def build_source_documents(
    project_name: str,
    snapshot: SourceSnapshot,
    report: DetectionReport,
) -> tuple[SourceDocument, ...]:
    """Build six fixed documents plus source-supported interface/database documents."""
    project = _project_name(project_name)
    families = [(family, family) for family in _DOCUMENT_FAMILIES[:6]]
    if report.interface_document_applicable:
        families.append(("接口契约", "接口契约"))
    if report.database_document_applicable:
        families.append(("数据库设计", "数据库设计"))
    paths = tuple(f"{project}-{filename}.md" for filename, _ in families)
    evidence = repository_section(snapshot)
    common_gap = gap_section()
    contents = {
        "功能清单": _content(
            f"{project} 功能清单",
            paths,
            evidence,
            interface_section(report, "用户可见能力"),
            symbol_section(report, "源码能力清单"),
            common_gap,
        ),
        "需求规格": _content(
            f"{project} 需求规格",
            paths,
            evidence,
            requirement_section(report),
            permission_section(report),
            configuration_section(report),
            common_gap,
        ),
        "系统架构": _content(
            f"{project} 系统架构",
            paths,
            evidence,
            dependency_section(report),
            symbol_section(report, "组件与实现入口"),
            configuration_section(report),
            common_gap,
        ),
        "详细设计": _content(
            f"{project} 详细设计",
            paths,
            evidence,
            symbol_section(report, "类与函数设计"),
            interface_section(report),
            permission_section(report),
            database_section(report, detailed=False),
            common_gap,
        ),
        "用户手册": _content(
            f"{project} 用户手册",
            paths,
            evidence,
            interface_section(report, "可操作入口"),
            configuration_section(report),
            common_gap,
        ),
        "验收规格": _content(
            f"{project} 验收规格",
            paths,
            evidence,
            acceptance_section(report),
            common_gap,
        ),
        "接口契约": _content(
            f"{project} 接口契约",
            paths,
            evidence,
            interface_section(report, "接口明细"),
            permission_section(report),
            common_gap,
        ),
        "数据库设计": _content(
            f"{project} 数据库设计",
            paths,
            evidence,
            database_section(report, detailed=True),
            common_gap,
        ),
    }
    return tuple(
        SourceDocument(path, contents[family])
        for path, (_, family) in zip(paths, families, strict=True)
    )


def ensure_source_output_ready(output: Path, *, force: bool) -> None:
    if output.is_symlink():
        raise SourceDocumentError(f"output directory must not be a symlink: {output}")
    if output.exists() and not output.is_dir():
        raise SourceDocumentError(f"output path is not a directory: {output}")
    if output.exists() and any(output.iterdir()) and not force:
        raise SourceDocumentError(f"output directory is not empty: {output}")


def write_source_documents(
    output: Path,
    documents: tuple[SourceDocument, ...],
    *,
    managed_filenames: tuple[str, ...] = (),
) -> None:
    """Publish the complete document set as one crash-recoverable tree transaction."""
    try:
        publish_tree(
            output,
            ((document.filename, document.content) for document in documents),
            managed_filenames=managed_filenames,
        )
    except PublicationError as exc:
        raise SourceDocumentError(str(exc)) from exc
