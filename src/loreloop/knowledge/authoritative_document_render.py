"""Markdown sections for source-derived authoritative project documents."""

from __future__ import annotations

from .authoritative_records import DetectionReport, SourceRef
from .authoritative_types import SourceSnapshot


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


def _source(source: SourceRef) -> str:
    return f"`{_cell(source.repository_alias)}:{_cell(source.path)}#L{source.line}`"


def navigation(paths: tuple[str, ...]) -> list[str]:
    return ["## 文档导航", "", *(f"- [{path[:-3]}]({path})" for path in paths), ""]


def repository_section(snapshot: SourceSnapshot) -> list[str]:
    working_tree = any(item.snapshot_kind == "working_tree" for item in snapshot.repositories)
    boundary = (
        "本文档来自绑定当前 HEAD 的可验证工作树快照；它包含未提交文件的实际字节，"
        "但不代表已经提交的发布状态。"
        if working_tree
        else "本文档仅陈述下列干净 Git 提交快照中能够由源码直接证明的事实。"
    )
    lines = [
        "## 源码证据边界",
        "",
        boundary,
        "",
        "| 仓库 | 角色 | 快照 | HEAD Commit | Source Tree | 文件数 |",
        "|---|---|---|---|---|---:|",
    ]
    lines.extend(
        f"| `{item.alias}` | {item.role} | "
        + ("工作树" if item.snapshot_kind == "working_tree" else "提交态")
        + f" | `{item.commit_id.hex}` | `{item.tree_id.hex}` | {len(item.entries)} |"
        for item in snapshot.repositories
    )
    return [*lines, ""]


def interface_section(report: DetectionReport, title: str = "接口与入口") -> list[str]:
    lines = [f"## {title}", ""]
    if not report.interfaces:
        return [*lines, "未发现可由源码确认的 HTTP 或 CLI 入口。", ""]
    lines.extend(
        [
            "| 类型 | 方法 | 路径/命令 | 处理器 | 参数 | 返回类型 | 来源 |",
            "|---|---|---|---|---|---|---|",
        ]
    )
    for item in report.interfaces:
        parameters = ", ".join(
            f"{parameter.name}{':' + parameter.annotation if parameter.annotation else ''}"
            + f"{'*' if parameter.required else ''}"
            for parameter in item.parameters
        )
        lines.append(
            f"| {item.kind} | `{item.method}` | `{_cell(item.path)}` | "
            + f"`{_cell(item.name)}` | {_cell(parameters) or '-'} | "
            + f"`{_cell(item.return_type or '-')}` | {_source(item.source)} |"
        )
    return [*lines, "", "`*` 表示源码签名中的必填参数。", ""]


def symbol_section(report: DetectionReport, title: str = "模块与符号") -> list[str]:
    lines = [f"## {title}", ""]
    if not report.symbols:
        return [*lines, "未发现受支持语言中的类或函数定义。", ""]
    lines.extend(["| 类型 | 限定名称 | 签名 | 来源 |", "|---|---|---|---|"])
    lines.extend(
        f"| {item.kind} | `{_cell(item.qualified_name)}` | "
        + f"`{_cell(item.signature)}` | {_source(item.source)} |"
        for item in report.symbols
    )
    return [*lines, ""]


def dependency_section(report: DetectionReport) -> list[str]:
    lines = ["## 技术栈与依赖", ""]
    if not report.dependencies:
        return [*lines, "未从受支持的导入语句或依赖清单中发现依赖。", ""]
    lines.extend(["| 依赖 | 版本/要求 | 范围 | 来源 |", "|---|---|---|---|"])
    seen: set[tuple[str, str | None, str, str, str]] = set()
    for item in report.dependencies:
        key = (
            item.name,
            item.requirement,
            item.scope,
            item.source.repository_alias,
            item.source.path,
        )
        if key in seen:
            continue
        seen.add(key)
        lines.append(
            f"| `{_cell(item.name)}` | `{_cell(item.requirement or '-')}` | "
            + f"{_cell(item.scope)} | {_source(item.source)} |"
        )
    return [*lines, ""]


def configuration_section(report: DetectionReport) -> list[str]:
    lines = ["## 配置契约", ""]
    if not report.configurations:
        return [*lines, "未发现环境变量配置契约。", ""]
    lines.extend(["| 配置键 | 必填 | 默认值 | 来源 |", "|---|---|---|---|"])
    for item in report.configurations:
        default = "`<redacted>`" if item.redacted else f"`{_cell(item.default or '-')}`"
        lines.append(
            f"| `{_cell(item.key)}` | {'是' if item.required else '否'} | "
            + f"{default} | {_source(item.source)} |"
        )
    return [*lines, ""]


def permission_section(report: DetectionReport) -> list[str]:
    lines = ["## 权限与访问规则", ""]
    if not report.permissions:
        return [*lines, "未发现可由静态源码确认的角色、权限或 scope 判断。", ""]
    lines.extend(["| 主体 | 运算 | 期望值 | 源码表达式 | 来源 |", "|---|---|---|---|---|"])
    lines.extend(
        f"| `{_cell(item.subject)}` | `{item.operator}` | `{_cell(item.expected)}` | "
        + f"`{_cell(item.expression)}` | {_source(item.source)} |"
        for item in report.permissions
    )
    return [*lines, ""]


def _relationship_graph(report: DetectionReport) -> list[str]:
    table_names = list(dict.fromkeys(table.name for table in report.tables))
    for table in report.tables:
        for foreign_key in table.foreign_keys:
            if foreign_key.referenced_table not in table_names:
                table_names.append(foreign_key.referenced_table)
    identifiers = {name: f"T{index:03d}" for index, name in enumerate(table_names, 1)}
    lines = [
        "## ER 关系图",
        "",
        "该图仅表达源码中的外键方向，不推断业务基数。",
        "",
        "```mermaid",
        "flowchart LR",
    ]
    lines.extend(
        f'    {identifiers[name]}["{_cell(name).replace(chr(34), "&quot;")}"]'
        for name in table_names
    )
    for table in report.tables:
        for foreign_key in table.foreign_keys:
            label = (
                f"{', '.join(foreign_key.columns)} → {', '.join(foreign_key.referenced_columns)}"
            )
            lines.append(
                f'    {identifiers[table.name]} -->|"{_cell(label)}"| '
                + f"{identifiers[foreign_key.referenced_table]}"
            )
    return [*lines, "```", ""]


def database_section(report: DetectionReport, *, detailed: bool) -> list[str]:
    lines = ["## 数据模型", ""]
    if not report.tables:
        return [*lines, "未发现显式 CREATE TABLE 源码证据。", ""]
    if detailed:
        lines = [*_relationship_graph(report), *lines]
    for table in report.tables:
        lines.extend(
            [
                f"### `{_cell(table.name)}`",
                "",
                f"来源：{_source(table.source)}",
                "",
                "| 字段 | 类型 | 可空 | 主键 | 默认值 |",
                "|---|---|---|---|---|",
            ]
        )
        lines.extend(
            f"| `{_cell(column.name)}` | `{_cell(column.data_type)}` | "
            + f"{'是' if column.nullable else '否'} | {'是' if column.primary_key or column.name in table.primary_key else '否'} | "
            + f"`{_cell(column.default or '-')}` |"
            for column in table.columns
        )
        if detailed and table.foreign_keys:
            lines.extend(["", "外键："])
            lines.extend(
                f"- `{', '.join(key.columns)}` → `{key.referenced_table}"
                + f"({', '.join(key.referenced_columns)})`"
                for key in table.foreign_keys
            )
        lines.append("")
    if detailed and report.indexes:
        lines.extend(["## 索引", "", "| 索引 | 表 | 字段 | 唯一 | 来源 |", "|---|---|---|---|---|"])
        lines.extend(
            f"| `{item.name}` | `{item.table}` | `{', '.join(item.columns)}` | "
            + f"{'是' if item.unique else '否'} | {_source(item.source)} |"
            for item in report.indexes
        )
        lines.append("")
    return lines


def requirement_section(report: DetectionReport) -> list[str]:
    lines = ["## 可由源码确认的需求", ""]
    requirements: list[tuple[str, str, str, SourceRef]] = [
        (item.external_id or f"MAT-{index:03d}", "需求材料", item.statement, item.source)
        for index, item in enumerate(report.requirements, 1)
    ]
    derived: list[tuple[str, str, SourceRef]] = [
        (
            "接口",
            f"系统提供 `{item.method} {item.path}` 入口，由 `{item.name}` 处理。",
            item.source,
        )
        for item in report.interfaces
    ]
    derived.extend(
        ("配置", f"系统读取配置键 `{item.key}`。", item.source) for item in report.configurations
    )
    derived.extend(
        ("权限", f"系统执行权限判断 `{item.expression}`。", item.source)
        for item in report.permissions
    )
    offset = len(requirements)
    requirements.extend(
        (f"REQ-{offset + index:03d}", kind, text, source)
        for index, (kind, text, source) in enumerate(derived, 1)
    )
    if not requirements:
        return [*lines, "当前源码和需求材料没有提供足够证据来形成用户可见需求。", ""]
    lines.extend(["| ID | 类别 | 需求陈述 | 来源 |", "|---|---|---|---|"])
    lines.extend(
        f"| {_cell(identifier)} | {kind} | {_cell(text)} | {_source(source)} |"
        for identifier, kind, text, source in requirements
    )
    return [*lines, ""]


def acceptance_section(report: DetectionReport) -> list[str]:
    lines = ["## 验收准则", "", "需求材料中的验收项优先；自动派生项仅验证源码合同。", ""]
    criteria: list[tuple[str, str, str, SourceRef]] = [
        (
            item.requirement_external_id or f"MAT-AC-{index:03d}",
            "需求材料",
            item.statement,
            item.source,
        )
        for index, item in enumerate(report.acceptances, 1)
    ]
    derived: list[tuple[str, str, SourceRef]] = [
        ("接口", f"源码中存在 `{item.method} {item.path}`，处理器为 `{item.name}`。", item.source)
        for item in report.interfaces
    ]
    derived.extend(
        ("配置", f"源码或模板声明配置键 `{item.key}`。", item.source)
        for item in report.configurations
    )
    derived.extend(
        ("数据", f"DDL 中存在表 `{item.name}` 及 {len(item.columns)} 个已解析字段。", item.source)
        for item in report.tables
    )
    offset = len(criteria)
    criteria.extend(
        (f"AC-{offset + index:03d}", kind, text, source)
        for index, (kind, text, source) in enumerate(derived, 1)
    )
    if not criteria:
        return [*lines, "没有可从需求材料或当前源码形成的验收准则。", ""]
    lines.extend(["| ID | 类别 | 可验证结果 | 来源 |", "|---|---|---|---|"])
    lines.extend(
        f"| {_cell(identifier)} | {kind} | {_cell(text)} | {_source(source)} |"
        for identifier, kind, text, source in criteria
    )
    return [*lines, ""]


def gap_section() -> list[str]:
    return [
        "## 证据限制",
        "",
        "本文档不推断源码中没有明确表达的业务背景、用户目标、运行时返回值、性能指标或组织权限。",
        "这些内容需要需求材料、运行证据或人工确认后才能成为权威结论。",
        "",
    ]
