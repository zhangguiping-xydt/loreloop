"""Shared deterministic Markdown projection for typed and replayed document ASTs."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import PurePosixPath

from .authoritative_ast import DocumentRowKind
from .authoritative_document_routes import CANONICAL_DOCUMENT_OWNER

Scalar = None | bool | int | str


@dataclass(frozen=True, slots=True)
class MarkdownRow:
    kind: str
    record_id: str
    values: tuple[tuple[str, Scalar], ...]
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EvidenceLocation:
    repository: str
    path: str
    line: int


@dataclass(frozen=True, slots=True)
class MarkdownSection:
    title: str
    rows: tuple[MarkdownRow, ...]


@dataclass(frozen=True, slots=True)
class MarkdownDocument:
    title: str
    family: str
    authority: str
    package_id: str | None
    repository_digest: str
    semantic_record_total: int
    sections: tuple[MarkdownSection, ...]
    evidence: tuple[tuple[str, EvidenceLocation], ...]


_PURPOSES = {
    "capability_catalog": "按仓库和入口域概括源码能够直接证明的系统能力。",
    "requirements": "收录已提交需求材料，以及源码明确表达的权限、状态、错误和配置约束；各类事实不会互相替代。",
    "architecture": "展示多仓库边界、技术依赖、配置和部署证据，不把 import 清单冒充组件设计。",
    "detailed_design": "按仓库与源文件组织实现模块，完整符号事实位于本文的完整知识索引。",
    "user_guide": "仅收录源码或需求材料明确表达的用户界面、命令入口、角色和操作约束。",
    "acceptance": "仅收录已提交验收条款和测试证据，不用接口存在性冒充业务验收。",
    "interface_contract": "列出源码确认的接口，以及能够明确提取的参数、返回类型、权限和错误契约。",
    "database_design": "列出源码确认的表、字段、索引和外键关系。",
}

_FULLY_RENDERED_ROW_KINDS = {
    DocumentRowKind.INTERFACE,
    DocumentRowKind.COMMAND,
    DocumentRowKind.REQUIREMENT,
    DocumentRowKind.CURRENT_DATA,
}

_COLUMN_LABELS = {
    "name": "名称",
    "title": "标题",
    "statement": "陈述",
    "description": "说明",
    "path": "路径",
    "locator": "定位",
    "method": "方法",
    "parameters": "参数",
    "return_type": "返回值",
    "qualified_name": "限定名",
    "signature": "签名",
    "scope": "范围",
    "framework": "框架",
    "case_count": "用例数",
    "cases": "用例",
    "key": "配置项",
    "default": "默认值",
    "record_type": "记录类型",
    "table": "表",
    "data_type": "数据类型",
    "nullable": "可空",
    "primary_key": "主键",
    "columns": "字段",
    "unique": "唯一",
    "referenced_table": "引用表",
    "referenced_columns": "引用字段",
    "role": "角色",
    "priority": "优先级",
    "external_id": "外部 ID",
    "actions": "操作",
    "expression": "表达式",
}


def _cell(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("`", "&#96;")
        .replace("|", "\\|")
        .replace("\r", " ")
        .replace("\n", " ")
    )


def _value(value: Scalar) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "是" if value else "否"
    return str(value)


def _values(row: MarkdownRow) -> dict[str, Scalar]:
    return dict(row.values)


def _locations(document: MarkdownDocument) -> dict[str, EvidenceLocation]:
    return dict(document.evidence)


def _location(row: MarkdownRow, evidence: dict[str, EvidenceLocation]) -> EvidenceLocation | None:
    return next((evidence[item] for item in row.evidence_ids if item in evidence), None)


def _source(location: EvidenceLocation | None) -> str:
    if location is None:
        return "-"
    return f"`{_cell(location.repository)}:{_cell(location.path)}#L{location.line}`"


def _generic_table(
    rows: tuple[MarkdownRow, ...], evidence: dict[str, EvidenceLocation]
) -> list[str]:
    if not rows:
        return ["没有可由当前证据确认的记录。", ""]
    columns = tuple(dict.fromkeys(key for row in rows for key, _ in row.values))
    headers = ("record_id", *columns, "source")
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
    ]
    for row in rows:
        values = _values(row)
        cells = [f"`{row.record_id}`"]
        cells.extend(_cell(_value(values.get(column))) for column in columns)
        cells.append(_source(_location(row, evidence)))
        lines.append("| " + " | ".join(cells) + " |")
    return [*lines, ""]


def _complete_table(rows: list[MarkdownRow], evidence: dict[str, EvidenceLocation]) -> list[str]:
    columns = tuple(dict.fromkeys(key for row in rows for key, _ in row.values))
    headers = (*(_COLUMN_LABELS.get(column, column) for column in columns), "证据")
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
    ]
    for row in rows:
        values = _values(row)
        cells = [_cell(_value(values.get(column))) for column in columns]
        cells.append(_source(_location(row, evidence)))
        lines.append("| " + " | ".join(cells) + " |")
    return lines


def _domain(path: str) -> str:
    parts = tuple(part for part in path.split("/") if part)
    if not parts:
        return path or "-"
    if parts[0].lower() in {"api", "rest", "srv", "v1", "v2", "v3"}:
        return "/" + "/".join(parts[:2])
    return f"/{parts[0]}"


def _capability_summary(
    rows: tuple[MarkdownRow, ...], evidence: dict[str, EvidenceLocation]
) -> list[str]:
    groups: dict[tuple[str, str], list[MarkdownRow]] = defaultdict(list)
    for row in rows:
        values = _values(row)
        path = values.get("path")
        location = _location(row, evidence)
        if isinstance(path, str):
            groups[((location.repository if location else "-"), _domain(path))].append(row)
    if not groups:
        return ["## 源码能力域", "", "未发现可用于归纳能力域的 HTTP 接口。", ""]
    lines = [
        "## 源码能力域",
        "",
        "下表是接口路径的证据化分组，不等同于已经确认的业务功能名称。",
        "",
        "| 仓库 | 入口域 | 接口数 | 方法 | 示例 |",
        "|---|---|---:|---|---|",
    ]
    for (repository, domain), items in sorted(groups.items()):
        methods = sorted(
            {str(_values(item).get("method")) for item in items if _values(item).get("method")}
        )
        examples = sorted(
            {str(_values(item).get("path")) for item in items if _values(item).get("path")}
        )[:3]
        lines.append(
            f"| `{_cell(repository)}` | `{_cell(domain)}` | {len(items)} | "
            + f"{_cell(', '.join(methods) or '-')} | {_cell(', '.join(examples))} |"
        )
    return [*lines, ""]


def _dependency_summary(
    rows: tuple[MarkdownRow, ...], evidence: dict[str, EvidenceLocation]
) -> list[str]:
    groups: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in rows:
        values = _values(row)
        name = values.get("name")
        scope = values.get("scope")
        location = _location(row, evidence)
        if isinstance(name, str):
            groups[(location.repository if location else "-", str(scope or "-"))].add(name)
    if not groups:
        return ["没有可由当前证据确认的依赖。", ""]
    lines = [
        "| 仓库 | 依赖范围 | 唯一依赖数 | 示例 |",
        "|---|---|---:|---|",
    ]
    for (repository, scope), names in sorted(groups.items()):
        examples = ", ".join(sorted(names)[:12])
        lines.append(
            f"| `{_cell(repository)}` | {_cell(scope)} | {len(names)} | {_cell(examples)} |"
        )
    return [*lines, "", "完整依赖记录位于本文的完整知识索引，证明信息保存在 Capsule 中。", ""]


_DOMAIN_MARKERS = (
    "controller",
    "controllers",
    "service",
    "services",
    "application",
    "domain",
    "repository",
    "repositories",
    "views",
    "view",
    "pages",
    "screens",
    "features",
    "modules",
)


def _implementation_domain(path: str) -> str:
    parts = tuple(part for part in PurePosixPath(path).parts if part)
    lowered = tuple(part.lower() for part in parts)
    for marker in _DOMAIN_MARKERS:
        if marker not in lowered:
            continue
        index = lowered.index(marker) + 1
        while index < len(parts) - 1 and lowered[index] in {
            "impl",
            "implementation",
            "controller",
            "service",
            "repository",
        }:
            index += 1
        if index < len(parts) - 1:
            return parts[index]
    return "shared-or-unclassified"


def _implementation_layer(path: str) -> str:
    lowered = path.lower()
    if any(token in lowered for token in ("/controller/", "/interfaces/", "/routes/")):
        return "interface"
    if any(token in lowered for token in ("/application/", "/service/", "/services/")):
        return "application"
    if "/domain/" in lowered:
        return "domain"
    if any(token in lowered for token in ("/repository/", "/infrastructure/", "/dao/")):
        return "infrastructure"
    if any(token in lowered for token in ("/views/", "/pages/", "/screens/", "/components/")):
        return "presentation"
    if "/test" in lowered:
        return "test"
    return "shared"


def _module_summary(
    rows: tuple[MarkdownRow, ...], evidence: dict[str, EvidenceLocation]
) -> list[str]:
    repositories: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: {"domains": set(), "layers": set(), "files": set(), "symbols": set()}
    )
    domains: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: {"repositories": set(), "layers": set(), "files": set(), "symbols": set()}
    )
    for row in rows:
        location = _location(row, evidence)
        values = _values(row)
        name = values.get("qualified_name") or values.get("signature")
        if location is not None and isinstance(name, str):
            domain = _implementation_domain(location.path)
            normalized_domain = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "-", domain).lower()
            layer = _implementation_layer(location.path)
            repository = repositories[location.repository]
            repository["domains"].add(normalized_domain)
            repository["layers"].add(layer)
            repository["files"].add(location.path)
            repository["symbols"].add(name)
            domain_group = domains[normalized_domain]
            domain_group["repositories"].add(location.repository)
            domain_group["layers"].add(layer)
            domain_group["files"].add(f"{location.repository}:{location.path}")
            domain_group["symbols"].add(name)
    if not repositories:
        return ["没有可由当前证据确认的实现模块。", ""]
    lines = [
        "以下内容按仓库、技术域和实现层归纳；完整文件与符号清单位于本文的完整知识索引。",
        "",
        "### 仓库分层概览",
        "",
        "| 仓库 | 技术域数 | 实现层 | 源文件数 | 符号数 |",
        "|---|---:|---|---:|---:|",
    ]
    for repository, items in sorted(repositories.items()):
        lines.append(
            f"| `{_cell(repository)}` | {len(items['domains'])} | "
            f"{_cell(', '.join(sorted(items['layers'])))} | {len(items['files'])} | "
            f"{len(items['symbols'])} |"
        )
    lines.extend(
        [
            "",
            "### 跨仓库技术域",
            "",
            "同名技术域跨仓库合并展示，便于识别接口层、应用层、领域层和基础设施层的协作边界。",
            "",
            "| 技术域 | 涉及仓库 | 实现层 | 源文件数 | 符号数 | 代表符号 |",
            "|---|---|---|---:|---:|---|",
        ]
    )
    for domain, items in sorted(domains.items()):
        representatives = ", ".join(sorted(items["symbols"])[:6])
        lines.append(
            f"| {_cell(domain)} | {_cell(', '.join(sorted(items['repositories'])))} | "
            f"{_cell(', '.join(sorted(items['layers'])))} | {len(items['files'])} | "
            f"{len(items['symbols'])} | {_cell(representatives)} |"
        )
    return [*lines, ""]


def _all_rows(document: MarkdownDocument) -> tuple[MarkdownRow, ...]:
    return tuple(row for section in document.sections for row in section.rows)


def _kind_rows(document: MarkdownDocument, *kinds: str) -> tuple[MarkdownRow, ...]:
    allowed = set(kinds)
    return tuple(row for row in _all_rows(document) if row.kind in allowed)


def _repository(row: MarkdownRow, evidence: dict[str, EvidenceLocation]) -> str:
    location = _location(row, evidence)
    return location.repository if location is not None else "-"


def _example(values: dict[str, Scalar]) -> str:
    for key in (
        "statement",
        "expression",
        "title",
        "name",
        "path",
        "qualified_name",
        "key",
    ):
        value = values.get(key)
        if isinstance(value, str) and value:
            return value
    return "-"


def _ui_area(name: str, source_path: str) -> str:
    domain = _implementation_domain(source_path)
    if domain != "shared-or-unclassified":
        return domain
    words = re.findall(r"[A-Z]+(?=[A-Z][a-z]|\d|$)|[A-Z]?[a-z]+|\d+", name)
    return words[0] if words else name or "Other"


def _reference_group(
    family: str,
    row: MarkdownRow,
    evidence: dict[str, EvidenceLocation],
) -> str:
    location = _location(row, evidence)
    repository = location.repository if location is not None else "-"
    path = location.path if location is not None else ""
    values = _values(row)
    if family == "detailed_design":
        return " · ".join((repository, _implementation_domain(path), _implementation_layer(path)))
    if family == "architecture":
        return f"{repository} · {values.get('scope') or '架构与配置'}"
    if family == "user_guide":
        area = _implementation_domain(path)
        if area == "shared-or-unclassified":
            area = PurePosixPath(path).stem or "入口"
        return f"{repository} · {area}"
    if family == "acceptance":
        return f"{repository} · {_implementation_domain(path)}"
    if family == "interface_contract":
        interface_path = values.get("path") or values.get("locator") or "CLI"
        return f"{repository} · {_domain(str(interface_path))}"
    if family == "database_design":
        return f"{repository} · {values.get('table') or '数据变更'}"
    return repository


def _render_complete_knowledge_index(
    document: MarkdownDocument,
    evidence: dict[str, EvidenceLocation],
) -> list[str]:
    sections: list[tuple[str, dict[str, list[MarkdownRow]]]] = []
    total = 0
    for section in document.sections:
        groups: dict[str, list[MarkdownRow]] = defaultdict(list)
        for row in section.rows:
            try:
                row_kind = DocumentRowKind(row.kind)
            except ValueError as exc:
                raise ValueError(
                    f"searchable row kind lacks a human document owner: {row.kind}"
                ) from exc
            owner = CANONICAL_DOCUMENT_OWNER[row_kind].value
            if owner != document.family:
                continue
            if row_kind in _FULLY_RENDERED_ROW_KINDS:
                continue
            groups[_reference_group(document.family, row, evidence)].append(row)
            total += 1
        if groups:
            sections.append((section.title, groups))
    if not sections:
        return []
    lines = [
        "## 完整知识索引",
        "",
        f"本节完整列出本文档负责的 {total} 条可召回知识。Agent 检索与这里使用同一份内容；折叠只影响阅读，不隐藏知识。",
        "",
    ]
    for section_title, groups in sections:
        lines.extend([f"### {_cell(section_title)}", ""])
        for group, rows in sorted(groups.items()):
            lines.extend(
                [
                    "<details>",
                    f"<summary>{_cell(group)}（{len(rows)} 条）</summary>",
                    "",
                    *_complete_table(rows, evidence),
                    "",
                    "</details>",
                    "",
                ]
            )
    return lines


def _render_project_overview(
    document: MarkdownDocument, evidence: dict[str, EvidenceLocation]
) -> list[str]:
    rows = _all_rows(document)
    repositories = sorted({location.repository for location in evidence.values()})
    counts = Counter(row.kind for row in rows)
    labels = {
        "InterfaceRow": "接口",
        "CommandRow": "命令",
        "UiSurfaceRow": "界面入口",
        "RequirementRow": "需求",
        "PermissionRow": "权限规则",
        "TestRow": "测试套件",
    }
    facts = [f"{labels[kind]} {counts[kind]}" for kind in labels if counts[kind]]
    return [
        "## 项目概览",
        "",
        f"- 覆盖仓库：{len(repositories)}（{_cell('、'.join(repositories) or '无')}）",
        f"- 本文档归纳：{_cell('，'.join(facts) or '当前没有可归纳记录')}。",
        "- 完整可召回事实位于对应人类文档；记录 ID 和逐项证明保存在 `.loreloop-export.json`。",
        "",
    ]


def _render_capability_catalog(
    document: MarkdownDocument, evidence: dict[str, EvidenceLocation]
) -> list[str]:
    lines = _render_project_overview(document, evidence)
    requirements = _kind_rows(document, "RequirementRow")
    if requirements:
        lines.extend(["## 已确认业务能力", ""])
        for row in requirements:
            values = _values(row)
            identifier = values.get("external_id") or row.record_id[:12]
            statement = values.get("statement") or values.get("title") or "-"
            lines.append(f"- **{_cell(str(identifier))}**：{_cell(str(statement))}")
        lines.append("")

    repositories: dict[str, dict[str, object]] = defaultdict(
        lambda: {
            "api_domains": set(),
            "api": 0,
            "ui": [],
            "commands": [],
            "examples": [],
        }
    )
    api_domains: dict[str, dict[str, object]] = defaultdict(
        lambda: {
            "repositories": set(),
            "endpoints": set(),
            "methods": set(),
            "examples": [],
            "source": None,
        }
    )
    for row in _kind_rows(document, "InterfaceRow", "UiSurfaceRow", "CommandRow"):
        values = _values(row)
        location = _location(row, evidence)
        repository = location.repository if location else "-"
        repository_group = repositories[repository]
        if row.kind == "InterfaceRow":
            raw_path = values.get("path") or values.get("locator") or "/"
            domain = _domain(str(raw_path)).lower()
            endpoint = str(raw_path)
            repository_group["api"] = int(repository_group["api"]) + 1
            api_domain_names = repository_group["api_domains"]
            if isinstance(api_domain_names, set):
                api_domain_names.add(domain)
            group = api_domains[domain]
            group_repositories = group["repositories"]
            if isinstance(group_repositories, set):
                group_repositories.add(repository)
            endpoints = group["endpoints"]
            if isinstance(endpoints, set):
                endpoints.add(endpoint)
            methods = group["methods"]
            if isinstance(methods, set):
                methods.add(str(values.get("method") or "-"))
            examples = group["examples"]
            if isinstance(examples, list) and len(examples) < 4:
                examples.append(_example(values))
            if group["source"] is None:
                group["source"] = location
        elif row.kind == "UiSurfaceRow":
            pages = repository_group["ui"]
            if isinstance(pages, list):
                pages.append(_example(values))
        else:
            commands = repository_group["commands"]
            if isinstance(commands, list):
                commands.append(_example(values))
        repository_examples = repository_group["examples"]
        if isinstance(repository_examples, list) and len(repository_examples) < 5:
            repository_examples.append(_example(values))
    lines.extend(
        [
            "## 源码能力域",
            "",
            "以下能力域由提交态入口和页面名称归纳，是技术能力地图；没有需求材料时不冒充业务需求。",
            "",
            "### 仓库入口概览",
            "",
            "| 仓库 | API 域数 | 接口数 | UI 入口数 | 命令数 | 代表入口 |",
            "|---|---:|---:|---:|---:|---|",
        ]
    )
    for repository, group in sorted(repositories.items()):
        domains = group["api_domains"] if isinstance(group["api_domains"], set) else set()
        pages = group["ui"] if isinstance(group["ui"], list) else []
        commands = group["commands"] if isinstance(group["commands"], list) else []
        examples = group["examples"] if isinstance(group["examples"], list) else []
        lines.append(
            f"| `{_cell(repository)}` | {len(domains)} | {group['api']} | {len(pages)} | "
            f"{len(commands)} | {_cell(', '.join(str(item) for item in examples))} |"
        )
    if not repositories:
        lines.append("| - | 0 | 0 | 0 | 0 | 当前没有可确认的用户或系统入口 |")
    lines.extend(
        [
            "",
            "### API 入口域",
            "",
            "同一路径域跨仓库合并，接口明细请查阅《接口契约》。",
            "",
            "| 入口域 | 涉及仓库 | 接口数 | 方法 | 示例 | 证据 |",
            "|---|---|---:|---|---|---|",
        ]
    )
    for domain, group in sorted(api_domains.items()):
        repositories_for_domain = (
            group["repositories"] if isinstance(group["repositories"], set) else set()
        )
        endpoints = group["endpoints"] if isinstance(group["endpoints"], set) else set()
        methods = group["methods"] if isinstance(group["methods"], set) else set()
        examples = group["examples"] if isinstance(group["examples"], list) else []
        source = group["source"] if isinstance(group["source"], EvidenceLocation) else None
        lines.append(
            f"| `{_cell(domain)}` | {_cell(', '.join(sorted(repositories_for_domain)))} | "
            f"{len(endpoints)} | {_cell(', '.join(sorted(methods)))} | "
            f"{_cell(', '.join(str(item) for item in examples))} | {_source(source)} |"
        )
    if not api_domains:
        lines.append("| - | - | 0 | - | 当前没有可确认的 API 入口 | - |")
    ui_repositories = {
        repository: group
        for repository, group in repositories.items()
        if isinstance(group["ui"], list) and group["ui"]
    }
    if ui_repositories:
        lines.extend(["", "### 用户界面入口", ""])
        for repository, group in sorted(ui_repositories.items()):
            pages = group["ui"] if isinstance(group["ui"], list) else []
            lines.append(
                f"- **{_cell(repository)}**：识别 {len(pages)} 个页面/组件入口；"
                f"代表项：{_cell(', '.join(str(item) for item in pages[:12]))}。"
            )
    return [*lines, ""]


def _render_requirements(
    document: MarkdownDocument, evidence: dict[str, EvidenceLocation]
) -> list[str]:
    requirements = _kind_rows(document, "RequirementRow")
    lines = ["## 需求材料状态", ""]
    if not requirements:
        return [
            *lines,
            "当前提交态没有可识别的需求或工作事项材料，因此本文档不会用配置、接口或类名伪造业务需求。",
            "",
            "要形成可交付需求规格，请提交包含角色、场景、业务规则和验收标准的 Markdown，并在导出时通过 `--requirements` 指定。",
            "",
        ]
    lines.extend(
        [
            f"已识别 {len(requirements)} 条提交态需求事实。",
            "",
            "## 功能与业务规则",
            "",
            "| ID | 标题 | 需求陈述 | 角色 | 优先级 | 证据 |",
            "|---|---|---|---|---|---|",
        ]
    )
    for row in requirements:
        values = _values(row)
        location = _location(row, evidence)
        lines.append(
            f"| {_cell(str(values.get('external_id') or row.record_id[:12]))} | "
            f"{_cell(str(values.get('title') or '-'))} | "
            f"{_cell(str(values.get('statement') or '-'))} | "
            f"{_cell(str(values.get('role') or '-'))} | "
            f"{_cell(str(values.get('priority') or '-'))} | {_source(location)} |"
        )
    constraints = _kind_rows(document, "PermissionRow", "StateRow", "ErrorRow")
    if constraints:
        lines.extend(["", "## 约束与异常", ""])
        for row in constraints:
            lines.append(
                f"- {_cell(_example(_values(row)))}（{_source(_location(row, evidence))}）"
            )
    return [*lines, ""]


def _architecture_role(scopes: set[str], dependencies: set[str]) -> str:
    lowered = " ".join((*scopes, *dependencies)).lower()
    if any(token in lowered for token in ("vue", "react", "frontend", "typescript")):
        return "客户端/前端"
    if any(token in lowered for token in ("jvm", "spring", "java")):
        return "服务端应用"
    if any(token in lowered for token in ("database", "sql", "prisma")):
        return "数据服务"
    return "共享或基础组件"


def _render_architecture(
    document: MarkdownDocument, evidence: dict[str, EvidenceLocation]
) -> list[str]:
    dependencies = _kind_rows(document, "DependencyRow")
    configurations = _kind_rows(document, "ConfigurationRow")
    repositories: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: {"scopes": set(), "dependencies": set(), "configs": set()}
    )
    for row in dependencies:
        values = _values(row)
        item = repositories[_repository(row, evidence)]
        item["scopes"].add(str(values.get("scope") or "unknown"))
        item["dependencies"].add(str(values.get("name") or "-"))
    for row in configurations:
        values = _values(row)
        repositories[_repository(row, evidence)]["configs"].add(str(values.get("key") or "-"))
    lines = [
        "## 系统上下文",
        "",
        "当前架构视图只陈述提交态仓库、依赖和配置边界；没有显式调用证据时不虚构服务间连线。",
        "",
        "```mermaid",
        "flowchart LR",
    ]
    for index, (repository, facts) in enumerate(sorted(repositories.items()), 1):
        alias = f"R{index:03d}"
        role = _architecture_role(facts["scopes"], facts["dependencies"])
        lines.append(f'    {alias}["{_cell(repository)}\\n{_cell(role)}"]')
    if not repositories:
        lines.append('    R001["没有可确认的架构组件"]')
    lines.extend(["```", "", "## 仓库与职责", ""])
    lines.extend(
        [
            "| 仓库 | 证据化职责 | 依赖范围 | 依赖数 | 配置数 | 代表技术 |",
            "|---|---|---|---:|---:|---|",
        ]
    )
    for repository, facts in sorted(repositories.items()):
        role = _architecture_role(facts["scopes"], facts["dependencies"])
        examples = ", ".join(sorted(facts["dependencies"])[:10])
        lines.append(
            f"| `{_cell(repository)}` | {_cell(role)} | {_cell(', '.join(sorted(facts['scopes'])))} | "
            f"{len(facts['dependencies'])} | {len(facts['configs'])} | {_cell(examples)} |"
        )
    lines.extend(["", "## 配置与运行边界", ""])
    if configurations:
        by_repo: dict[str, list[str]] = defaultdict(list)
        for row in configurations:
            values = _values(row)
            key = values.get("key")
            default = values.get("default")
            by_repo[_repository(row, evidence)].append(
                f"{key}={default if default is not None else '<required>'}"
            )
        for repository, items in sorted(by_repo.items()):
            lines.append(f"- **{_cell(repository)}**：{_cell('；'.join(items))}")
    else:
        lines.append("没有检测到明确的配置或部署契约。")
    return [*lines, ""]


def _render_user_guide(
    document: MarkdownDocument, evidence: dict[str, EvidenceLocation]
) -> list[str]:
    ui_rows = _kind_rows(document, "UiSurfaceRow")
    groups: dict[tuple[str, str], dict[str, object]] = defaultdict(
        lambda: {"pages": [], "actions": set(), "source": None}
    )
    for row in ui_rows:
        values = _values(row)
        location = _location(row, evidence)
        name = str(values.get("name") or "-")
        area = _ui_area(name, location.path if location else "")
        group = groups[(_repository(row, evidence), area)]
        pages = group["pages"]
        if isinstance(pages, list):
            pages.append(name)
        actions = group["actions"]
        raw_actions = str(values.get("actions") or "")
        if isinstance(actions, set):
            actions.update(item.strip() for item in raw_actions.split(",") if item.strip())
        if group["source"] is None:
            group["source"] = location
    lines = [
        "## 使用边界",
        "",
        "本文件按可识别页面和操作入口提供导航。只有路由、没有运行时页面或操作材料时，不把页面名称伪装成完整操作步骤。",
        "",
        "## 用户界面与操作入口",
        "",
        "| 仓库 | 功能区域 | 页面数 | 代表页面 | 已知操作 | 证据 |",
        "|---|---|---:|---|---|---|",
    ]
    for (repository, area), group in sorted(groups.items()):
        pages = group["pages"] if isinstance(group["pages"], list) else []
        actions = group["actions"] if isinstance(group["actions"], set) else set()
        source = group["source"] if isinstance(group["source"], EvidenceLocation) else None
        lines.append(
            f"| `{_cell(repository)}` | {_cell(area)} | {len(pages)} | "
            f"{_cell(', '.join(pages[:8]))} | {_cell(', '.join(sorted(actions)[:8]) or '-')} | "
            f"{_source(source)} |"
        )
    if not groups:
        lines.append("| - | 当前没有可确认的 UI/CLI 入口 | 0 | - | - | - |")
    behaviors = _kind_rows(document, "RequirementRow")
    if behaviors:
        lines.extend(["", "## 已确认操作与行为", ""])
        for row in behaviors:
            lines.append(
                f"- {_cell(_example(_values(row)))}（{_source(_location(row, evidence))}）"
            )
    return [*lines, ""]


def _render_detailed_design(
    document: MarkdownDocument, evidence: dict[str, EvidenceLocation]
) -> list[str]:
    modules = _kind_rows(document, "ModuleRow")
    lines = ["## 实现结构", "", *_module_summary(modules, evidence)]
    facts = _kind_rows(document, "ImplementationFactRow", "StateRow", "ErrorRow", "AnnotationRow")
    if facts:
        groups: dict[tuple[str, str, str], list[MarkdownRow]] = defaultdict(list)
        for row in facts:
            location = _location(row, evidence)
            groups[
                (
                    _repository(row, evidence),
                    _implementation_domain(location.path if location else ""),
                    row.kind,
                )
            ].append(row)
        lines.extend(
            [
                "## 状态、错误与实现约束",
                "",
                "| 仓库 | 技术域 | 事实类型 | 数量 | 示例 | 证据 |",
                "|---|---|---|---:|---|---|",
            ]
        )
        for (repository, domain, kind), rows in sorted(groups.items()):
            examples = ", ".join(_example(_values(row)) for row in rows[:5])
            lines.append(
                f"| `{_cell(repository)}` | {_cell(domain)} | {_cell(kind)} | {len(rows)} | "
                f"{_cell(examples)} | {_source(_location(rows[0], evidence))} |"
            )
        lines.append("")
    else:
        lines.extend(
            [
                "## 核心流程与异常",
                "",
                "源码中没有形成可确定投影的状态机、核心调用流程或结构化错误契约；本节不使用符号名称臆造流程。",
                "",
            ]
        )
    return lines


def _render_acceptance(
    document: MarkdownDocument, evidence: dict[str, EvidenceLocation]
) -> list[str]:
    criteria = _kind_rows(document, "AcceptanceRow")
    lines = ["## 验收准则", ""]
    if criteria:
        for row in criteria:
            lines.append(
                f"- {_cell(_example(_values(row)))}（{_source(_location(row, evidence))}）"
            )
    else:
        lines.extend(
            [
                "当前没有提交态验收标准；下面的测试只能证明代码中存在测试，不能替代业务验收结论。",
                "",
            ]
        )
    tests = _kind_rows(document, "TestRow")
    groups: dict[tuple[str, str, str, str], dict[str, object]] = defaultdict(
        lambda: {"suites": [], "cases": [], "case_count": 0, "source": None}
    )
    for row in tests:
        values = _values(row)
        location = _location(row, evidence)
        key = (
            _repository(row, evidence),
            _implementation_domain(location.path if location else ""),
            str(values.get("scope") or "unknown"),
            str(values.get("framework") or "unknown"),
        )
        group = groups[key]
        suites = group["suites"]
        if isinstance(suites, list):
            suites.append(str(values.get("name") or "-"))
        cases = group["cases"]
        if isinstance(cases, list):
            cases.extend(
                item.strip() for item in str(values.get("cases") or "").split(",") if item.strip()
            )
        group["case_count"] = int(group["case_count"]) + int(values.get("case_count") or 0)
        if group["source"] is None:
            group["source"] = location
    lines.extend(
        [
            "## 测试证据",
            "",
            "| 仓库 | 技术域 | 测试范围 | 框架 | 测试套件数 | 用例数 | 代表测试套件与用例 | 证据 |",
            "|---|---|---|---|---:|---:|---|---|",
        ]
    )
    for (repository, domain, scope, framework), group in sorted(groups.items()):
        suites = group["suites"] if isinstance(group["suites"], list) else []
        cases = group["cases"] if isinstance(group["cases"], list) else []
        source = group["source"] if isinstance(group["source"], EvidenceLocation) else None
        examples = [*suites[:3], *cases[:5]]
        lines.append(
            f"| `{_cell(repository)}` | {_cell(domain)} | {_cell(scope)} | {_cell(framework)} | "
            f"{len(suites)} | {group['case_count']} | {_cell(', '.join(examples))} | {_source(source)} |"
        )
    if not groups:
        lines.append("| - | - | - | - | 0 | 0 | 当前没有可识别测试 | - |")
    lines.extend(
        [
            "",
            "## 可交付性判断",
            "",
            "只有验收准则、执行结果和对应测试三者能够关联时，本文档才足以支持正式验收。",
            "",
        ]
    )
    return lines


def _render_interfaces(
    document: MarkdownDocument, evidence: dict[str, EvidenceLocation]
) -> list[str]:
    interfaces = _kind_rows(document, "InterfaceRow", "CommandRow")
    groups: dict[tuple[str, str], list[MarkdownRow]] = defaultdict(list)
    for row in interfaces:
        values = _values(row)
        path = values.get("path") or values.get("locator") or "CLI"
        groups[(_repository(row, evidence), _domain(str(path)))].append(row)
    lines = [
        "## 接口域索引",
        "",
        "| 仓库 | 接口域 | 接口数 | 方法 | 完整度 |",
        "|---|---|---:|---|---|",
    ]
    for (repository, domain), rows in sorted(groups.items()):
        methods = sorted({str(_values(row).get("method") or "-") for row in rows})
        complete = sum(
            bool(_values(row).get("parameters")) and bool(_values(row).get("return_type"))
            for row in rows
        )
        lines.append(
            f"| `{_cell(repository)}` | `{_cell(domain)}` | {len(rows)} | "
            f"{_cell(', '.join(methods))} | {complete}/{len(rows)} 含参数和返回结构 |"
        )
    lines.extend(["", "## HTTP 接口", ""])
    for (repository, domain), rows in sorted(groups.items()):
        lines.extend(
            [
                f"### {_cell(repository)} · {_cell(domain)}",
                "",
                "<details>",
                f"<summary>展开 {len(rows)} 个接口</summary>",
                "",
                "| 方法 | 路径 | 处理器 | 参数 | 返回值 | 证据 |",
                "|---|---|---|---|---|---|",
            ]
        )
        for row in sorted(rows, key=lambda item: str(_values(item).get("path") or "")):
            values = _values(row)
            lines.append(
                f"| {_cell(str(values.get('method') or '-'))} | "
                f"`{_cell(str(values.get('path') or values.get('locator') or '-'))}` | "
                f"{_cell(str(values.get('name') or values.get('title') or '-'))} | "
                f"{_cell(str(values.get('parameters') or '-'))} | "
                f"{_cell(str(values.get('return_type') or '-'))} | "
                f"{_source(_location(row, evidence))} |"
            )
        lines.extend(["", "</details>", ""])
    return lines


def _render_web_sections(
    document: MarkdownDocument,
    evidence: dict[str, EvidenceLocation],
    kinds: tuple[tuple[str, str], ...],
) -> list[str]:
    lines: list[str] = []
    for kind, title in kinds:
        rows = _kind_rows(document, kind)
        if not rows:
            continue
        lines.extend([f"## {title}", ""])
        for row in rows:
            values = _values(row)
            heading = values.get("title") or values.get("entry_id") or row.record_id[:12]
            statement = values.get("statement") or values.get("locator") or "-"
            locator = values.get("locator")
            suffix = f"；来源页面：`{_cell(str(locator))}`" if locator else ""
            lines.append(
                f"- **{_cell(str(heading))}**：{_cell(str(statement))}{suffix} "
                f"（{_source(_location(row, evidence))}）"
            )
        lines.append("")
    return lines


def _render_database(
    document: MarkdownDocument, evidence: dict[str, EvidenceLocation]
) -> list[str]:
    rows = _kind_rows(document, "CurrentDataRow")
    by_table: dict[str, list[MarkdownRow]] = defaultdict(list)
    for row in rows:
        table = _values(row).get("table")
        if isinstance(table, str):
            by_table[table].append(row)
    lines: list[str] = []
    for table, items in sorted(by_table.items()):
        lines.extend([f"## 表：`{_cell(table)}`", ""])
        table_row = next(
            (item for item in items if _values(item).get("record_type") == "table"), None
        )
        if table_row is not None:
            lines.append(f"- 主键：`{_cell(str(_values(table_row).get('primary_key') or '-'))}`")
            lines.append(f"- 来源：{_source(_location(table_row, evidence))}")
            lines.append("")
        columns = [item for item in items if _values(item).get("record_type") == "column"]
        if columns:
            lines.extend(
                [
                    "### 字段",
                    "",
                    "| 字段 | 类型 | 可空 | 主键 | 默认值 |",
                    "|---|---|---|---|---|",
                ]
            )
            for row in columns:
                values = _values(row)
                lines.append(
                    f"| `{_cell(str(values.get('name') or '-'))}` | "
                    f"{_cell(str(values.get('data_type') or '-'))} | "
                    f"{_cell(_value(values.get('nullable')))} | "
                    f"{_cell(_value(values.get('primary_key')))} | "
                    f"{_cell(_value(values.get('default')))} |"
                )
            lines.append("")
        indexes = [item for item in items if _values(item).get("record_type") == "index"]
        if indexes:
            lines.extend(["### 索引", "", "| 索引 | 字段 | 唯一 |", "|---|---|---|"])
            for row in indexes:
                values = _values(row)
                lines.append(
                    f"| `{_cell(str(values.get('name') or '-'))}` | "
                    f"{_cell(str(values.get('columns') or '-'))} | "
                    f"{_cell(_value(values.get('unique')))} |"
                )
            lines.append("")
    if not by_table:
        lines.extend(["## 数据模型", "", "当前没有可确认的表结构。", ""])
    return lines


def _relationship_graph(sections: tuple[MarkdownSection, ...]) -> list[str]:
    rows = tuple(
        row
        for section in sections
        for row in section.rows
        if row.kind in {"CurrentDataRow", "RelationRow"}
    )
    table_names: list[str] = []
    relations: list[dict[str, Scalar]] = []
    for row in rows:
        values = _values(row)
        for key in ("table", "referenced_table"):
            name = values.get(key)
            if isinstance(name, str) and name not in table_names:
                table_names.append(name)
        if row.kind == "RelationRow":
            relations.append(values)
    if not table_names:
        return []
    identifiers = {name: f"T{index:03d}" for index, name in enumerate(table_names, 1)}
    lines = [
        "## ER 关系图",
        "",
        "该图仅表达源码中的显式外键方向，不推断业务基数。",
        "",
        "```mermaid",
        "flowchart LR",
        *(f'    {identifiers[name]}["{_cell(name)}"]' for name in table_names),
    ]
    for values in relations:
        table, referenced = values.get("table"), values.get("referenced_table")
        if isinstance(table, str) and isinstance(referenced, str):
            label = f"{_value(values.get('columns'))} → {_value(values.get('referenced_columns'))}"
            lines.append(
                f'    {identifiers[table]} -->|"{_cell(label)}"| {identifiers[referenced]}'
            )
    return [*lines, "```", ""]


def _evidence_summary(document: MarkdownDocument) -> list[str]:
    locations = tuple(location for _, location in document.evidence)
    if not locations:
        return ["## 证据覆盖", "", "本文件没有源记录。", ""]
    evidence_count = Counter(location.repository for location in locations)
    file_count: dict[str, set[str]] = defaultdict(set)
    for location in locations:
        file_count[location.repository].add(location.path)
    lines = [
        "## 证据覆盖",
        "",
        "| 仓库 | 源文件数 | 证据点 |",
        "|---|---:|---:|",
    ]
    for repository in sorted(evidence_count):
        lines.append(
            f"| `{_cell(repository)}` | {len(file_count[repository])} | "
            + f"{evidence_count[repository]} |"
        )
    return [*lines, "", "逐条证据身份和字节范围保存在 Capsule 中。", ""]


def _gap_lines(document: MarkdownDocument) -> list[str]:
    kinds = {row.kind for section in document.sections for row in section.rows}
    gaps: list[str] = []
    if document.family == "capability_catalog" and "RequirementRow" not in kinds:
        gaps.append("缺少已提交的功能/需求材料；接口域只能证明技术入口，不能自动命名业务功能。")
    if document.family == "requirements" and not kinds & {
        "RequirementRow",
        "WebRequirementRow",
    }:
        gaps.append("缺少已提交需求材料；本文件不能作为完整业务需求规格。")
    if document.family == "architecture" and "DeploymentRow" not in kinds:
        gaps.append("缺少部署拓扑与运行时调用证据；当前只能确认仓库、依赖和配置边界。")
    if document.family == "detailed_design":
        if not kinds & {"StateRow", "ErrorRow", "ImplementationFactRow"}:
            gaps.append("缺少状态机、错误路径和核心流程证据；符号清单不等同于完整详细设计。")
    if document.family == "user_guide":
        if not kinds & {"UiSurfaceRow", "CommandRow", "WebBehaviorRow"}:
            gaps.append("缺少 UI/CLI 操作入口与运行时页面证据；无法形成可执行用户操作手册。")
        elif not kinds & {"RequirementRow", "WebRequirementRow"}:
            gaps.append("已识别页面或命令入口，但缺少已提交操作说明；本文件不能替代完整操作步骤。")
    if document.family == "acceptance":
        if not kinds & {"AcceptanceRow", "WebAcceptanceRow"}:
            gaps.append("缺少已提交验收条款；测试存在性不能替代业务验收标准。")
        if "TestRow" not in kinds:
            gaps.append("缺少可识别的测试证据；本文件不能用于正式项目验收。")
    if document.family == "interface_contract":
        interface_rows = tuple(
            row
            for section in document.sections
            for row in section.rows
            if row.kind in {"InterfaceRow", "CommandRow"}
        )
        if any(
            not _values(row).get("parameters") or not _values(row).get("return_type")
            for row in interface_rows
        ):
            gaps.append("部分接口缺少参数或返回结构；不得据此臆造字段、错误码或权限。")
        if "WebInterfaceRow" in kinds:
            gaps.append("Web 接口观察是运行时事实，不等同于完整参数、响应和错误码契约。")
        if "PermissionRow" not in kinds:
            gaps.append("未发现可绑定到接口的明确权限规则；接口存在不代表任意角色均可调用。")
        if "ErrorRow" not in kinds:
            gaps.append("未发现结构化错误码或异常响应契约；调用方仍需核对实现和运行时行为。")
    if document.family == "database_design" and "RelationRow" not in kinds:
        gaps.append("未发现显式外键关系；ER 图只展示表节点。")
    if not gaps:
        gaps.append("未在提交态需求材料或源码中明确表达的业务结论不会被补写。")
    return ["## 证据缺口", "", *(f"- {item}" for item in gaps), ""]


def render_markdown(document: MarkdownDocument, paths: tuple[str, ...]) -> str:
    """Render one normalized document model into deterministic Markdown."""
    evidence = _locations(document)
    document_records = len({row.record_id for section in document.sections for row in section.rows})
    lines = [f"# {_cell(document.title)}", ""]
    lines.extend(
        [
            f"> 权威来源：`{document.authority}` · 本文负责 {document_records} 条证据记录；可召回事实位于正文或完整知识索引，证明位于 Capsule。",
            "",
            "## 文档导航",
            "",
            " · ".join(f"[{path[:-3]}]({path})" for path in paths),
            "",
            "## 文档用途",
            "",
            _PURPOSES.get(document.family, "呈现提交态源码能够直接证明的事实。"),
            "",
        ]
    )
    lines.extend(_gap_lines(document))
    if document.family == "capability_catalog":
        lines.extend(_render_capability_catalog(document, evidence))
    elif document.family == "requirements":
        lines.extend(_render_requirements(document, evidence))
        lines.extend(
            _render_web_sections(
                document,
                evidence,
                (("WebRequirementRow", "Web 需求事实"),),
            )
        )
    elif document.family == "architecture":
        lines.extend(_render_architecture(document, evidence))
        lines.extend(
            _render_web_sections(
                document,
                evidence,
                (
                    ("WebArchitectureRow", "Web 运行架构观察"),
                    ("WebConstraintRow", "Web 运行约束"),
                ),
            )
        )
    elif document.family == "detailed_design":
        lines.extend(_render_detailed_design(document, evidence))
    elif document.family == "user_guide":
        lines.extend(_render_user_guide(document, evidence))
        lines.extend(
            _render_web_sections(
                document,
                evidence,
                (("WebBehaviorRow", "Web 页面与行为观察"),),
            )
        )
    elif document.family == "acceptance":
        lines.extend(_render_acceptance(document, evidence))
        lines.extend(
            _render_web_sections(
                document,
                evidence,
                (("WebAcceptanceRow", "Web 验收事实"),),
            )
        )
    elif document.family == "interface_contract":
        lines.extend(_render_interfaces(document, evidence))
        lines.extend(
            _render_web_sections(
                document,
                evidence,
                (("WebInterfaceRow", "Web 接口观察"),),
            )
        )
    elif document.family == "database_design":
        lines.extend(_relationship_graph(document.sections))
        lines.extend(_render_database(document, evidence))
    else:
        for section in document.sections:
            lines.extend([f"## {_cell(section.title)}", ""])
            lines.extend(_generic_table(section.rows, evidence))
    lines.extend(_render_complete_knowledge_index(document, evidence))
    lines.extend(_evidence_summary(document))
    lines.extend(
        [
            "## 版本与完整性",
            "",
            f"- Package ID：`{document.package_id or '-'}`",
            f"- Repository configuration：`{document.repository_digest}`",
            f"- SemanticCore records：{document.semantic_record_total}",
            f"- 本文归纳记录：{document_records}",
            "",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"
