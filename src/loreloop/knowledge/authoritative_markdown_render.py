"""Shared deterministic Markdown projection for typed and replayed document ASTs."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass

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
    "detailed_design": "按仓库与源文件组织实现模块，完整符号事实保留在 Capsule。",
    "user_guide": "仅收录源码或需求材料明确表达的用户界面、命令入口、角色和操作约束。",
    "acceptance": "仅收录已提交验收条款和测试证据，不用接口存在性冒充业务验收。",
    "interface_contract": "列出源码确认的接口，以及能够明确提取的参数、返回类型、权限和错误契约。",
    "database_design": "列出源码确认的表、字段、索引和外键关系。",
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


def _domain(path: str) -> str:
    parts = tuple(part for part in path.split("/") if part)
    if not parts:
        return path or "-"
    return "/" + "/".join(parts[:2])


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
        "| repository | entry_domain | endpoints | methods | examples |",
        "|---|---|---:|---|---|",
    ]
    for (repository, domain), items in sorted(groups.items()):
        methods = sorted(
            {
                str(_values(item).get("method"))
                for item in items
                if _values(item).get("method")
            }
        )
        examples = sorted(
            {
                str(_values(item).get("path"))
                for item in items
                if _values(item).get("path")
            }
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
        "| repository | scope | unique_dependencies | examples |",
        "|---|---|---:|---|",
    ]
    for (repository, scope), names in sorted(groups.items()):
        examples = ", ".join(sorted(names)[:12])
        lines.append(
            f"| `{_cell(repository)}` | {_cell(scope)} | {len(names)} | {_cell(examples)} |"
        )
    return [*lines, "", "完整依赖记录及其逐项证据保存在 Capsule 中。", ""]


def _module_summary(
    rows: tuple[MarkdownRow, ...], evidence: dict[str, EvidenceLocation]
) -> list[str]:
    groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    for row in rows:
        location = _location(row, evidence)
        values = _values(row)
        name = values.get("qualified_name") or values.get("signature")
        if location is not None and isinstance(name, str):
            groups[(location.repository, location.path)].append(name)
    if not groups:
        return ["没有可由当前证据确认的实现模块。", ""]
    lines = [
        "| repository | source_file | symbols | representative_symbols |",
        "|---|---|---:|---|",
    ]
    for (repository, path), names in sorted(groups.items()):
        representatives = ", ".join(dict.fromkeys(names))
        representatives = ", ".join(representatives.split(", ")[:8])
        lines.append(
            f"| `{_cell(repository)}` | `{_cell(path)}` | {len(names)} | "
            + f"{_cell(representatives)} |"
        )
    return [*lines, "", "完整符号记录、签名和证据位置保存在 Capsule 中。", ""]


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
        "| repository | source_files | evidence_points |",
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
    if document.family == "requirements" and "RequirementRow" not in kinds:
        gaps.append("缺少已提交需求材料；本文件不能作为完整业务需求规格。")
    if document.family == "architecture" and "DeploymentRow" not in kinds:
        gaps.append("缺少部署拓扑与运行时调用证据；当前只能确认仓库、依赖和配置边界。")
    if document.family == "detailed_design":
        if not kinds & {"StateRow", "ErrorRow", "ImplementationFactRow"}:
            gaps.append("缺少状态机、错误路径和核心流程证据；符号清单不等同于完整详细设计。")
    if document.family == "user_guide":
        if not kinds & {"UiSurfaceRow", "CommandRow"}:
            gaps.append("缺少 UI/CLI 操作入口与运行时页面证据；无法形成可执行用户操作手册。")
        elif "RequirementRow" not in kinds:
            gaps.append("已识别页面或命令入口，但缺少已提交操作说明；本文件不能替代完整操作步骤。")
    if document.family == "acceptance":
        if "AcceptanceRow" not in kinds:
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
    document_records = len(
        {row.record_id for section in document.sections for row in section.rows}
    )
    lines = [f"# {_cell(document.title)}", "", "## 文档导航", ""]
    lines.extend(f"- [{path[:-3]}]({path})" for path in paths)
    lines.extend(
        [
            "",
            "## 权威边界",
            "",
            f"- Authority: `{document.authority}`",
            f"- Package ID: `{document.package_id or '-'}`",
            f"- Repository configuration: `{document.repository_digest}`",
            f"- SemanticCore records: {document.semantic_record_total}",
            f"- Document projection records: {document_records}",
            "",
            "## 文档用途",
            "",
            _PURPOSES.get(document.family, "呈现提交态源码能够直接证明的事实。"),
            "",
        ]
    )
    lines.extend(_gap_lines(document))
    if document.family == "database_design":
        lines.extend(_relationship_graph(document.sections))
    if document.family == "capability_catalog":
        interface_rows = tuple(
            row
            for section in document.sections
            for row in section.rows
            if row.kind == "InterfaceRow"
        )
        lines.extend(_capability_summary(interface_rows, evidence))
    for section in document.sections:
        rows = section.rows
        if document.family == "capability_catalog" and rows and rows[0].kind == "InterfaceRow":
            continue
        lines.extend([f"## {_cell(section.title)}", ""])
        if document.family == "architecture" and rows and rows[0].kind == "DependencyRow":
            lines.extend(_dependency_summary(rows, evidence))
        elif document.family == "detailed_design" and rows and rows[0].kind == "ModuleRow":
            lines.extend(_module_summary(rows, evidence))
        else:
            lines.extend(_generic_table(rows, evidence))
    lines.extend(_evidence_summary(document))
    return "\n".join(lines).rstrip() + "\n"
