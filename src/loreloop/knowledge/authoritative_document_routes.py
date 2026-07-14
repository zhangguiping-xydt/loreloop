"""Closed routing matrix from semantic row kinds to document families."""

from __future__ import annotations

from dataclasses import dataclass

from .authoritative_ast import DocumentRowKind, OptionalDocumentFamily, RequiredDocumentFamily


@dataclass(frozen=True, slots=True)
class DocumentRoute:
    family: RequiredDocumentFamily | OptionalDocumentFamily
    title: str
    row_kinds: frozenset[DocumentRowKind]


DOCUMENT_ROUTES = (
    DocumentRoute(
        RequiredDocumentFamily.CAPABILITY_CATALOG,
        "功能清单",
        frozenset(
            {
                DocumentRowKind.INTERFACE,
                DocumentRowKind.COMMAND,
                DocumentRowKind.REQUIREMENT,
                DocumentRowKind.ACCEPTANCE,
                DocumentRowKind.PERMISSION,
            }
        ),
    ),
    DocumentRoute(
        RequiredDocumentFamily.REQUIREMENTS,
        "需求规格",
        frozenset(
            {
                DocumentRowKind.REQUIREMENT,
                DocumentRowKind.INTERFACE,
                DocumentRowKind.COMMAND,
                DocumentRowKind.PERMISSION,
                DocumentRowKind.CONFIGURATION,
            }
        ),
    ),
    DocumentRoute(
        RequiredDocumentFamily.ARCHITECTURE,
        "系统架构",
        frozenset(
            {
                DocumentRowKind.DEPENDENCY,
                DocumentRowKind.CONFIGURATION,
                DocumentRowKind.INTERFACE,
                DocumentRowKind.COMMAND,
            }
        ),
    ),
    DocumentRoute(
        RequiredDocumentFamily.DETAILED_DESIGN,
        "详细设计",
        frozenset(
            {
                DocumentRowKind.INTERFACE,
                DocumentRowKind.COMMAND,
                DocumentRowKind.MODULE,
                DocumentRowKind.PERMISSION,
                DocumentRowKind.CONFIGURATION,
                DocumentRowKind.DEPENDENCY,
                DocumentRowKind.CURRENT_DATA,
                DocumentRowKind.RELATION,
            }
        ),
    ),
    DocumentRoute(
        RequiredDocumentFamily.USER_GUIDE,
        "用户手册",
        frozenset(
            {
                DocumentRowKind.INTERFACE,
                DocumentRowKind.COMMAND,
                DocumentRowKind.CONFIGURATION,
                DocumentRowKind.REQUIREMENT,
            }
        ),
    ),
    DocumentRoute(
        RequiredDocumentFamily.ACCEPTANCE,
        "验收规格",
        frozenset(
            {
                DocumentRowKind.ACCEPTANCE,
                DocumentRowKind.REQUIREMENT,
                DocumentRowKind.INTERFACE,
                DocumentRowKind.COMMAND,
                DocumentRowKind.CONFIGURATION,
                DocumentRowKind.CURRENT_DATA,
            }
        ),
    ),
    DocumentRoute(
        OptionalDocumentFamily.INTERFACE_CONTRACT,
        "接口契约",
        frozenset({DocumentRowKind.INTERFACE, DocumentRowKind.COMMAND, DocumentRowKind.PERMISSION}),
    ),
    DocumentRoute(
        OptionalDocumentFamily.DATABASE_DESIGN,
        "数据库设计",
        frozenset({DocumentRowKind.CURRENT_DATA, DocumentRowKind.RELATION}),
    ),
)

SECTION_ROUTES = {
    DocumentRowKind.INTERFACE: ("interfaces", "HTTP 接口"),
    DocumentRowKind.COMMAND: ("commands", "命令入口"),
    DocumentRowKind.MODULE: ("modules", "模块与符号"),
    DocumentRowKind.PERMISSION: ("permissions", "权限规则"),
    DocumentRowKind.CONFIGURATION: ("configuration", "配置契约"),
    DocumentRowKind.DEPENDENCY: ("dependencies", "依赖关系"),
    DocumentRowKind.REQUIREMENT: ("requirements", "需求材料"),
    DocumentRowKind.ACCEPTANCE: ("acceptance", "验收准则"),
    DocumentRowKind.CURRENT_DATA: ("data", "数据模型"),
    DocumentRowKind.RELATION: ("relations", "数据关系"),
}
