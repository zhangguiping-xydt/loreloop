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
                DocumentRowKind.UI_SURFACE,
                DocumentRowKind.REQUIREMENT,
                DocumentRowKind.PERMISSION,
                DocumentRowKind.WEB_INTERFACE,
                DocumentRowKind.WEB_BEHAVIOR,
            }
        ),
    ),
    DocumentRoute(
        RequiredDocumentFamily.REQUIREMENTS,
        "需求规格",
        frozenset(
            {
                DocumentRowKind.REQUIREMENT,
                DocumentRowKind.PERMISSION,
                DocumentRowKind.CONFIGURATION,
                DocumentRowKind.STATE,
                DocumentRowKind.ERROR,
                DocumentRowKind.WEB_REQUIREMENT,
                DocumentRowKind.WEB_CONSTRAINT,
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
                DocumentRowKind.DEPLOYMENT,
                DocumentRowKind.MODULE_REPORT,
                DocumentRowKind.APPLICABILITY,
                DocumentRowKind.WEB_ARCHITECTURE,
                DocumentRowKind.WEB_CONSTRAINT,
            }
        ),
    ),
    DocumentRoute(
        RequiredDocumentFamily.DETAILED_DESIGN,
        "详细设计",
        frozenset(
            {
                DocumentRowKind.MODULE,
                DocumentRowKind.IMPLEMENTATION_FACT,
                DocumentRowKind.STATE,
                DocumentRowKind.ERROR,
                DocumentRowKind.ANNOTATION,
            }
        ),
    ),
    DocumentRoute(
        RequiredDocumentFamily.USER_GUIDE,
        "用户手册",
        frozenset(
            {
                DocumentRowKind.UI_SURFACE,
                DocumentRowKind.COMMAND,
                DocumentRowKind.REQUIREMENT,
                DocumentRowKind.PERMISSION,
                DocumentRowKind.WEB_BEHAVIOR,
            }
        ),
    ),
    DocumentRoute(
        RequiredDocumentFamily.ACCEPTANCE,
        "验收规格",
        frozenset(
            {
                DocumentRowKind.ACCEPTANCE,
                DocumentRowKind.TEST,
                DocumentRowKind.REQUIREMENT,
                DocumentRowKind.WEB_ACCEPTANCE,
            }
        ),
    ),
    DocumentRoute(
        OptionalDocumentFamily.INTERFACE_CONTRACT,
        "接口契约",
        frozenset(
            {
                DocumentRowKind.INTERFACE,
                DocumentRowKind.COMMAND,
                DocumentRowKind.PERMISSION,
                DocumentRowKind.ERROR,
                DocumentRowKind.WEB_INTERFACE,
            }
        ),
    ),
    DocumentRoute(
        OptionalDocumentFamily.DATABASE_DESIGN,
        "数据库设计",
        frozenset(
            {
                DocumentRowKind.CURRENT_DATA,
                DocumentRowKind.HISTORICAL_DATA,
                DocumentRowKind.MIGRATION_OPERATION,
                DocumentRowKind.RELATION,
            }
        ),
    ),
)

ROUTED_ROW_KINDS = frozenset(
    row_kind for route in DOCUMENT_ROUTES for row_kind in route.row_kinds
)

SECTION_ROUTES = {
    DocumentRowKind.INTERFACE: ("interfaces", "HTTP 接口"),
    DocumentRowKind.COMMAND: ("commands", "命令入口"),
    DocumentRowKind.UI_SURFACE: ("ui-surfaces", "用户界面与操作入口"),
    DocumentRowKind.MODULE: ("modules", "模块与符号"),
    DocumentRowKind.MODULE_REPORT: ("module-reports", "模块报告"),
    DocumentRowKind.PERMISSION: ("permissions", "权限规则"),
    DocumentRowKind.CONFIGURATION: ("configuration", "配置契约"),
    DocumentRowKind.DEPENDENCY: ("dependencies", "依赖关系"),
    DocumentRowKind.DEPLOYMENT: ("deployment", "部署与运行环境"),
    DocumentRowKind.STATE: ("states", "状态与生命周期"),
    DocumentRowKind.ERROR: ("errors", "错误与异常契约"),
    DocumentRowKind.TEST: ("tests", "测试证据"),
    DocumentRowKind.WEB_REQUIREMENT: ("web-requirements", "Web 需求事实"),
    DocumentRowKind.WEB_INTERFACE: ("web-interfaces", "Web 接口观察"),
    DocumentRowKind.WEB_ARCHITECTURE: ("web-architecture", "Web 运行架构观察"),
    DocumentRowKind.WEB_BEHAVIOR: ("web-behavior", "Web 页面与行为观察"),
    DocumentRowKind.WEB_CONSTRAINT: ("web-constraints", "Web 运行约束"),
    DocumentRowKind.WEB_ACCEPTANCE: ("web-acceptance", "Web 验收事实"),
    DocumentRowKind.REQUIREMENT: ("requirements", "需求材料"),
    DocumentRowKind.ACCEPTANCE: ("acceptance", "验收准则"),
    DocumentRowKind.CURRENT_DATA: ("data", "数据模型"),
    DocumentRowKind.HISTORICAL_DATA: ("historical-data", "历史数据"),
    DocumentRowKind.MIGRATION_OPERATION: ("migration", "数据迁移"),
    DocumentRowKind.RELATION: ("relations", "数据关系"),
    DocumentRowKind.APPLICABILITY: ("applicability", "适用性"),
    DocumentRowKind.ANNOTATION: ("annotations", "实现注解"),
    DocumentRowKind.IMPLEMENTATION_FACT: ("implementation", "实现事实"),
}
