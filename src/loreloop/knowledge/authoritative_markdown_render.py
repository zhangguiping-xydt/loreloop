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
    "detailed_design": "按模块解释职责边界、实现分层、关键文件和入口符号；完整事实保留在文末证据附录。",
    "user_guide": "仅收录源码或需求材料明确表达的用户界面、命令入口、角色和操作约束。",
    "acceptance": "仅收录已提交验收条款和测试证据，不用接口存在性冒充业务验收。",
    "interface_contract": "列出源码确认的接口，以及能够明确提取的参数、返回类型、权限和错误契约。",
    "database_design": "列出源码确认的表、字段、索引和外键关系。",
}

_DOCUMENT_NAVIGATION = {
    "capability_catalog": ("项目概览", "源码能力域", "代码模块能力", "覆盖缺口与未确认事项"),
    "requirements": ("需求材料状态", "功能与业务规则", "约束与异常", "覆盖缺口与未确认事项"),
    "architecture": ("系统上下文", "仓库与职责", "模块与仓库边界", "配置与运行边界"),
    "detailed_design": ("设计摘要", "模块详细设计", "模块协作视图", "核心流程与异常"),
    "user_guide": ("使用边界", "用户界面与操作入口", "已确认操作与行为", "覆盖缺口与未确认事项"),
    "acceptance": ("验收准则", "测试证据", "可交付性判断", "覆盖缺口与未确认事项"),
    "interface_contract": ("接口域索引", "HTTP 接口", "覆盖缺口与未确认事项"),
    "database_design": ("数据实体总览", "ER 关系图", "表结构详情", "覆盖缺口与未确认事项"),
}

_HUMAN_V2_NAVIGATION = {
    "capability_catalog": ("系统能力概览", "已实现功能清单", "功能详情"),
    "requirements": (
        "文档性质",
        "明确需求材料",
        "源码反构的现状规格",
        "实现约束与权限",
        "需求确认边界",
    ),
    "architecture": (
        "系统上下文",
        "技术栈与运行形态",
        "运行与代码单元",
        "关键单元职责",
    ),
    "detailed_design": ("设计总览", "模块详细设计", "源码解析覆盖缺口"),
    "user_guide": (
        "使用边界",
        "用户入口与可执行操作",
        "关键入口详情",
        "操作说明的可信边界",
    ),
    "acceptance": ("正式验收材料", "源码反构的验收候选", "已存在测试证据"),
    "interface_contract": ("接口域索引", "HTTP 接口"),
    "database_design": ("数据域总览", "核心实体", "核心表字段详情", "全量实体索引"),
}

_HUMAN_V2_WEB_SECTIONS = {
    "requirements": (
        ("WebRequirementRow", "Web 需求事实"),
        ("WebConstraintRow", "Web 运行约束"),
    ),
    "architecture": (("WebArchitectureRow", "Web 运行架构观察"),),
    "user_guide": (("WebBehaviorRow", "Web 页面与行为观察"),),
    "acceptance": (("WebAcceptanceRow", "Web 验收事实"),),
    "interface_contract": (("WebInterfaceRow", "Web 接口观察"),),
}

_GENERIC_DOMAINS = {
    "common",
    "config",
    "dto",
    "entity",
    "model",
    "param",
    "po",
    "shared-or-unclassified",
    "util",
    "utils",
    "vo",
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
    "issue": "解析状态",
    "selected_encoding": "采用解码",
    "replacement_count": "替换字符数",
    "dropped_fact_count": "丢弃候选事实数",
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


def _normalized_domain(path: str) -> str:
    domain = _implementation_domain(path)
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "-", domain).lower()


def _module_inventory(
    rows: tuple[MarkdownRow, ...], evidence: dict[str, EvidenceLocation]
) -> dict[str, dict[str, object]]:
    domains: dict[str, dict[str, object]] = defaultdict(
        lambda: {
            "repositories": set(),
            "layers": set(),
            "files": Counter(),
            "symbols": [],
        }
    )
    for row in rows:
        location = _location(row, evidence)
        values = _values(row)
        name = values.get("qualified_name") or values.get("signature")
        if location is None or not isinstance(name, str):
            continue
        domain = _normalized_domain(location.path)
        group = domains[domain]
        repositories = group["repositories"]
        layers = group["layers"]
        files = group["files"]
        symbols = group["symbols"]
        if isinstance(repositories, set):
            repositories.add(location.repository)
        if isinstance(layers, set):
            layers.add(_implementation_layer(location.path))
        if isinstance(files, Counter):
            files[f"{location.repository}:{location.path}"] += 1
        if isinstance(symbols, list):
            symbols.append((name, str(values.get("signature") or name), location.line))
    return dict(domains)


def _ranked_domains(inventory: dict[str, dict[str, object]], *, limit: int) -> tuple[str, ...]:
    return tuple(
        sorted(
            inventory,
            key=lambda domain: (
                domain in _GENERIC_DOMAINS,
                -len(inventory[domain]["symbols"])
                if isinstance(inventory[domain]["symbols"], list)
                else 0,
                domain,
            ),
        )[:limit]
    )


def _representative_symbols(items: list[tuple[str, str, int]], limit: int = 8) -> list[str]:
    suffix_order = (
        "controller",
        "service",
        "core",
        "repository",
        "rep",
        "handler",
        "manager",
    )

    def rank(item: tuple[str, str, int]) -> tuple[int, int, str]:
        name = item[0]
        lowered = name.lower()
        priority = next(
            (index for index, suffix in enumerate(suffix_order) if lowered.endswith(suffix)),
            len(suffix_order),
        )
        return priority, len(name), name

    return [item[0] for item in sorted(dict.fromkeys(items), key=rank)[:limit]]


def _module_summary(
    rows: tuple[MarkdownRow, ...], evidence: dict[str, EvidenceLocation]
) -> list[str]:
    inventory = _module_inventory(rows, evidence)
    if not inventory:
        return ["## 设计摘要", "", "没有可由当前证据确认的实现模块。", ""]
    selected = _ranked_domains(inventory, limit=12)
    repository_names = sorted(
        {
            repository
            for group in inventory.values()
            for repository in (
                group["repositories"] if isinstance(group["repositories"], set) else set()
            )
        }
    )
    total_files = {
        path
        for group in inventory.values()
        for path in (group["files"] if isinstance(group["files"], Counter) else Counter())
    }
    total_symbols = sum(
        len(group["symbols"]) if isinstance(group["symbols"], list) else 0
        for group in inventory.values()
    )
    lines = [
        "## 设计摘要",
        "",
        "本文先展示最具实现规模的模块，再给出分层视图和完整证据附录。模块名称来自源码目录，职责描述只陈述结构事实。",
        "",
        "| 仓库 | 实现模块 | 源文件 | 类/函数符号 | 重点展开模块 |",
        "|---:|---:|---:|---:|---:|",
        f"| {len(repository_names)} | {len(inventory)} | {len(total_files)} | {total_symbols} | {len(selected)} |",
        "",
        "### 模块总览",
        "",
        "| 模块 | 涉及仓库 | 实现层 | 文件数 | 符号数 | 关键实现 |",
        "|---|---|---|---:|---:|---|",
    ]
    for domain in selected:
        group = inventory[domain]
        repositories = group["repositories"] if isinstance(group["repositories"], set) else set()
        layers = group["layers"] if isinstance(group["layers"], set) else set()
        files = group["files"] if isinstance(group["files"], Counter) else Counter()
        symbols = group["symbols"] if isinstance(group["symbols"], list) else []
        lines.append(
            f"| `{_cell(domain)}` | {_cell(', '.join(sorted(repositories)))} | "
            f"{_cell(', '.join(sorted(layers)))} | {len(files)} | {len(symbols)} | "
            f"{_cell(', '.join(_representative_symbols(symbols, 6)))} |"
        )
    lines.extend(["", "## 模块详细设计", ""])
    for index, domain in enumerate(selected, 1):
        group = inventory[domain]
        repositories = group["repositories"] if isinstance(group["repositories"], set) else set()
        layers = group["layers"] if isinstance(group["layers"], set) else set()
        files = group["files"] if isinstance(group["files"], Counter) else Counter()
        symbols = group["symbols"] if isinstance(group["symbols"], list) else []
        key_files = [path for path, _ in files.most_common(6)]
        signatures = [signature for _, signature, _ in symbols[:6]]
        lines.extend(
            [
                f"### {index}. `{_cell(domain)}`",
                "",
                "| 设计项 | 当前实现证据 |",
                "|---|---|",
                f"| 职责边界 | 该模块由 {_cell(', '.join(sorted(layers)))} 层的 {len(files)} 个源文件组成；业务目标需结合需求材料确认。 |",
                f"| 所属仓库 | {_cell(', '.join(sorted(repositories)))} |",
                f"| 关键文件 | {_cell('；'.join(key_files))} |",
                f"| 关键类/函数 | {_cell('；'.join(_representative_symbols(symbols)))} |",
                f"| 输入输出线索 | {_cell('；'.join(signatures) or '当前签名未提供明确输入输出')} |",
                "| 未确认事项 | 缺少确定调用图时，不把目录分层解释为真实运行时调用顺序。 |",
                "",
            ]
        )
    remaining = tuple(domain for domain in inventory if domain not in selected)
    if remaining:
        lines.extend(
            [
                "<details>",
                f"<summary>其余 {len(remaining)} 个实现模块</summary>",
                "",
                "| 模块 | 仓库 | 文件数 | 符号数 |",
                "|---|---|---:|---:|",
            ]
        )
        for domain in sorted(remaining):
            group = inventory[domain]
            repositories = (
                group["repositories"] if isinstance(group["repositories"], set) else set()
            )
            files = group["files"] if isinstance(group["files"], Counter) else Counter()
            symbols = group["symbols"] if isinstance(group["symbols"], list) else []
            lines.append(
                f"| `{_cell(domain)}` | {_cell(', '.join(sorted(repositories)))} | "
                f"{len(files)} | {len(symbols)} |"
            )
        lines.extend(["", "</details>", ""])
    layer_labels = {
        "presentation": "界面层",
        "interface": "接口层",
        "application": "应用层",
        "domain": "领域层",
        "infrastructure": "基础设施层",
        "shared": "共享实现",
    }
    layer_order = tuple(layer_labels)
    lines.extend(
        [
            "## 模块协作视图",
            "",
            "下图仅表达目录和符号所证明的模块内分层，不代表已确认的运行时调用链。",
            "",
            "```mermaid",
            "flowchart LR",
        ]
    )
    for index, domain in enumerate(selected[:8], 1):
        layers = inventory[domain]["layers"]
        available = [layer for layer in layer_order if isinstance(layers, set) and layer in layers]
        if not available:
            continue
        nodes = []
        for layer_index, layer in enumerate(available, 1):
            node = f"M{index:02d}L{layer_index:02d}"
            nodes.append(node)
            lines.append(f'    {node}["{_cell(domain)}\\n{_cell(layer_labels[layer])}"]')
        if len(nodes) > 1:
            lines.append("    " + " --> ".join(nodes))
    lines.extend(["```", ""])
    return lines


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


def _compact_fact(row: MarkdownRow, location: EvidenceLocation | None) -> str:
    values = _values(row)
    line = f"L{location.line}" if location is not None else "L?"
    if row.kind == "ModuleRow":
        name = str(values.get("qualified_name") or "-")
        signature = str(values.get("signature") or name)
        return f"{name} — {signature} · {line}"
    if row.kind == "DependencyRow":
        name = str(values.get("name") or "-")
        requirement = str(values.get("requirement") or "-")
        scope = str(values.get("scope") or "-")
        return f"{name} {requirement} [{scope}] · {line}"
    facts = "；".join(
        f"{_COLUMN_LABELS.get(key, key)}={_value(value)}" for key, value in row.values
    )
    return f"{facts} · {line}"


def _compact_rows_by_file(
    rows: list[MarkdownRow], evidence: dict[str, EvidenceLocation]
) -> list[str]:
    by_file: dict[tuple[str, str], list[MarkdownRow]] = defaultdict(list)
    without_source: list[MarkdownRow] = []
    for row in rows:
        location = _location(row, evidence)
        if location is None:
            without_source.append(row)
        else:
            by_file[(location.repository, location.path)].append(row)
    lines = [
        "| 源文件 | 本行记录 | 可检索事实（名称、签名、版本与源码行） |",
        "|---|---:|---|",
    ]
    for (repository, path), items in sorted(by_file.items()):
        ordered = sorted(items, key=lambda row: _location(row, evidence).line)  # type: ignore[union-attr]
        for offset in range(0, len(ordered), 16):
            chunk = ordered[offset : offset + 16]
            facts = "<br>".join(
                _cell(_compact_fact(row, _location(row, evidence))) for row in chunk
            )
            source = f"`{_cell(repository)}:{_cell(path)}`" if offset == 0 else "↳ 同上"
            lines.append(f"| {source} | {len(chunk)} | {facts} |")
    if without_source:
        for offset in range(0, len(without_source), 16):
            chunk = without_source[offset : offset + 16]
            facts = "<br>".join(_cell(_compact_fact(row, None)) for row in chunk)
            lines.append(f"| - | {len(chunk)} | {facts} |")
    return lines


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
        "## 证据附录：完整可召回事实",
        "",
        f"本附录保留本文档负责的 {total} 条可召回知识。正文用于评审，附录用于精确检索和逐项核对；两者来自同一份 SemanticCore。",
        "",
    ]
    for section_title, groups in sections:
        lines.extend([f"### {_cell(section_title)}", ""])
        for group, rows in sorted(groups.items()):
            compact = len(rows) >= 24 and all(
                row.kind in {"ModuleRow", "DependencyRow"} for row in rows
            )
            lines.extend(
                [
                    "<details>",
                    f"<summary>{_cell(group)}（{len(rows)} 条）</summary>",
                    "",
                    *(
                        _compact_rows_by_file(rows, evidence)
                        if compact
                        else _complete_table(rows, evidence)
                    ),
                    "",
                    "</details>",
                    "",
                ]
            )
    return lines


def _render_project_overview(
    document: MarkdownDocument,
    evidence: dict[str, EvidenceLocation],
    *,
    separate_agent_view: bool,
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
        (
            "- 本文是人类阅读视图；精确原子事实、记录 ID 和逐项证明位于 "
            "`.loreloop-export.json` 的 Agent 视图。"
            if separate_agent_view
            else "- 完整可召回事实位于对应人类文档；记录 ID 和逐项证明保存在 "
            "`.loreloop-export.json`。"
        ),
        "",
    ]


def _heading_anchor(title: str) -> str:
    normalized = re.sub(r"[^0-9A-Za-z一-鿿\s-]", "", title).strip().lower()
    return re.sub(r"\s+", "-", normalized)


def _render_document_navigation(family: str, *, has_appendix: bool) -> list[str]:
    headings = _DOCUMENT_NAVIGATION.get(family, ())
    if has_appendix:
        headings = (*headings, "证据附录：完整可召回事实")
    headings = (*headings, "证据覆盖", "版本与完整性")
    return [
        "## 本文导航",
        "",
        *(f"- [{title}](#{_heading_anchor(title)})" for title in headings),
        "",
    ]


def _render_capability_catalog(
    document: MarkdownDocument,
    evidence: dict[str, EvidenceLocation],
    *,
    separate_agent_view: bool,
) -> list[str]:
    lines = _render_project_overview(
        document,
        evidence,
        separate_agent_view=separate_agent_view,
    )
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
    module_inventory = _module_inventory(_kind_rows(document, "ModuleRow"), evidence)
    if module_inventory:
        lines.extend(
            [
                "",
                "### 代码模块能力",
                "",
                "没有业务需求材料时，本节只把源码目录和入口符号归纳为技术能力，不替代产品功能定义。",
                "",
                "| 模块 | 仓库 | 实现层 | 文件数 | 代表入口 |",
                "|---|---|---|---:|---|",
            ]
        )
        for domain in _ranked_domains(module_inventory, limit=16):
            group = module_inventory[domain]
            repositories = (
                group["repositories"] if isinstance(group["repositories"], set) else set()
            )
            layers = group["layers"] if isinstance(group["layers"], set) else set()
            files = group["files"] if isinstance(group["files"], Counter) else Counter()
            symbols = group["symbols"] if isinstance(group["symbols"], list) else []
            lines.append(
                f"| `{_cell(domain)}` | {_cell(', '.join(sorted(repositories)))} | "
                f"{_cell(', '.join(sorted(layers)))} | {len(files)} | "
                f"{_cell(', '.join(_representative_symbols(symbols, 6)))} |"
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
    if any(
        token in lowered for token in ("vue", "react", "frontend", "typescript", "presentation")
    ):
        return "客户端/前端"
    if any(
        token in lowered
        for token in ("jvm", "spring", "java", "interface", "application", "domain")
    ):
        return "服务端应用"
    if any(token in lowered for token in ("database", "sql", "prisma", "infrastructure")):
        return "数据服务"
    return "共享或基础组件"


def _render_architecture(
    document: MarkdownDocument, evidence: dict[str, EvidenceLocation]
) -> list[str]:
    dependencies = _kind_rows(document, "DependencyRow")
    configurations = _kind_rows(document, "ConfigurationRow")
    modules = _kind_rows(document, "ModuleRow")
    repositories: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: {
            "scopes": set(),
            "dependencies": set(),
            "configs": set(),
            "domains": set(),
            "layers": set(),
        }
    )
    for row in dependencies:
        values = _values(row)
        item = repositories[_repository(row, evidence)]
        item["scopes"].add(str(values.get("scope") or "unknown"))
        item["dependencies"].add(str(values.get("name") or "-"))
    for row in configurations:
        values = _values(row)
        repositories[_repository(row, evidence)]["configs"].add(str(values.get("key") or "-"))
    domain_repositories: dict[str, set[str]] = defaultdict(set)
    for row in modules:
        location = _location(row, evidence)
        if location is None:
            continue
        domain = _normalized_domain(location.path)
        item = repositories[location.repository]
        item["domains"].add(domain)
        item["layers"].add(_implementation_layer(location.path))
        domain_repositories[domain].add(location.repository)
    repository_nodes = {
        repository: f"R{index:03d}" for index, repository in enumerate(sorted(repositories), 1)
    }
    lines = [
        "## 系统上下文",
        "",
        "当前架构视图只陈述提交态仓库、依赖和配置边界；没有显式调用证据时不虚构服务间连线。",
        "",
        "```mermaid",
        "flowchart LR",
    ]
    for repository, facts in sorted(repositories.items()):
        alias = repository_nodes[repository]
        role = _architecture_role(facts["scopes"] | facts["layers"], facts["dependencies"])
        lines.append(f'    {alias}["{_cell(repository)}\\n{_cell(role)}"]')
    shared_domains = tuple(
        sorted(
            (domain for domain, names in domain_repositories.items() if len(names) > 1),
            key=lambda domain: (-len(domain_repositories[domain]), domain),
        )[:12]
    )
    for index, domain in enumerate(shared_domains, 1):
        domain_node = f"D{index:03d}"
        lines.append(f'    {domain_node}["共享模块\\n{_cell(domain)}"]')
        for repository in sorted(domain_repositories[domain]):
            lines.append(f"    {repository_nodes[repository]} --> {domain_node}")
    if not repositories:
        lines.append('    R001["没有可确认的架构组件"]')
    lines.extend(["```", "", "## 仓库与职责", ""])
    lines.extend(
        [
            "| 仓库 | 证据化职责 | 实现层 | 模块数 | 依赖数 | 配置数 | 代表模块/技术 |",
            "|---|---|---|---:|---:|---:|---|",
        ]
    )
    for repository, facts in sorted(repositories.items()):
        role = _architecture_role(facts["scopes"] | facts["layers"], facts["dependencies"])
        examples = [*sorted(facts["domains"])[:6], *sorted(facts["dependencies"])[:6]]
        lines.append(
            f"| `{_cell(repository)}` | {_cell(role)} | {_cell(', '.join(sorted(facts['layers'])) or '-')} | "
            f"{len(facts['domains'])} | {len(facts['dependencies'])} | {len(facts['configs'])} | "
            f"{_cell(', '.join(examples))} |"
        )
    lines.extend(["", "## 模块与仓库边界", ""])
    if domain_repositories:
        lines.extend(
            [
                "| 模块 | 涉及仓库 | 边界性质 |",
                "|---|---|---|",
            ]
        )
        for domain, names in sorted(
            domain_repositories.items(), key=lambda item: (-len(item[1]), item[0])
        )[:30]:
            boundary = "跨仓库共享边界" if len(names) > 1 else "仓库内部模块"
            lines.append(f"| `{_cell(domain)}` | {_cell(', '.join(sorted(names)))} | {boundary} |")
        lines.append("")
    else:
        lines.extend(["当前没有可确认的实现模块边界。", ""])
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
    lines = _module_summary(modules, evidence)
    annotations = _kind_rows(document, "AnnotationRow")
    source_issues = tuple(
        row
        for row in annotations
        if _values(row).get("issue") in {"lossy_utf8_recovery", "unreadable_text_encoding"}
    )
    if source_issues:
        lines.extend(
            [
                "## 源码解析覆盖缺口",
                "",
                "以下文件的原始字节仍由 Source Snapshot 和 Capsule 绑定；LoreLoop 没有改写源码。"
                "受控恢复仅用于可安全识别的文本，包含替换字符的候选事实不会进入知识基线。",
                "",
                "| 仓库 | 文件 | 状态 | 采用解码 | 替换字符 | 丢弃候选事实 | 证据 |",
                "|---|---|---|---|---:|---:|---|",
            ]
        )
        for row in source_issues:
            values = _values(row)
            issue = values.get("issue")
            status = (
                "轻微 UTF-8 损坏，受控恢复"
                if issue == "lossy_utf8_recovery"
                else "无法安全解码，跳过语义解析"
            )
            lines.append(
                f"| `{_cell(_repository(row, evidence))}` | `{_cell(str(values.get('path') or '-'))}` | "
                f"{status} | {_cell(str(values.get('selected_encoding') or '-'))} | "
                f"{int(values.get('replacement_count') or 0)} | "
                f"{int(values.get('dropped_fact_count') or 0)} | "
                f"{_source(_location(row, evidence))} |"
            )
        lines.append("")
    facts = tuple(
        row
        for row in _kind_rows(
            document,
            "ImplementationFactRow",
            "StateRow",
            "ErrorRow",
            "AnnotationRow",
        )
        if row not in source_issues
    )
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
    lines: list[str] = [
        "## 数据实体总览",
        "",
        "| 表/实体 | 字段数 | 索引数 | 主键 | 证据 |",
        "|---|---:|---:|---|---|",
    ]
    for table, items in sorted(by_table.items()):
        table_row = next(
            (item for item in items if _values(item).get("record_type") == "table"), None
        )
        columns = [item for item in items if _values(item).get("record_type") == "column"]
        indexes = [item for item in items if _values(item).get("record_type") == "index"]
        primary = _values(table_row).get("primary_key") if table_row is not None else "-"
        lines.append(
            f"| `{_cell(table)}` | {len(columns)} | {len(indexes)} | "
            f"{_cell(str(primary or '-'))} | {_source(_location(table_row, evidence) if table_row else None)} |"
        )
    if not by_table:
        lines.append("| - | 0 | 0 | - | 当前没有可确认的表结构 |")
    lines.append("")
    lines.extend(_relationship_graph(document.sections))
    lines.extend(["## 表结构详情", ""])
    for table, items in sorted(by_table.items()):
        lines.extend([f"### 表：`{_cell(table)}`", ""])
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
                    "#### 字段",
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
            lines.extend(["#### 索引", "", "| 索引 | 字段 | 唯一 |", "|---|---|---|"])
            for row in indexes:
                values = _values(row)
                lines.append(
                    f"| `{_cell(str(values.get('name') or '-'))}` | "
                    f"{_cell(str(values.get('columns') or '-'))} | "
                    f"{_cell(_value(values.get('unique')))} |"
                )
            lines.append("")
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


_UNIT_LABELS = {
    "center": "中心端",
    "client": "客户端",
    "server": "服务端 / 节点端",
    "service": "服务接口",
    "web": "Web 应用",
    "db": "数据库",
    "database": "数据库",
    "build": "构建与发布",
    "pipeline": "持续集成",
    "rightdataserver": "正向数据服务",
    "kqtimer": "定时任务",
    "src": "核心源码",
    "app": "应用程序",
    "apps": "应用程序",
    "test": "测试",
    "tests": "测试",
}

_UNIT_SELECTION_ORDER = {
    "center": 0,
    "client": 1,
    "server": 2,
    "service": 3,
    "rightdataserver": 4,
    "kqtimer": 5,
    "web": 6,
    "build": 7,
    "pipeline": 8,
    "db": 9,
    "database": 9,
}

_CAPABILITY_NAMES = {
    "synchronization dept": "部门数据同步",
    "synchronization check user info": "人员信息同步与校验",
    "synchronization data": "基础数据同步",
    "syschronization card no": "卡号同步",
    "synchronization card no": "卡号同步",
    "sysch car no": "车牌号同步",
    "data distribute": "配置与数据分发",
    "data send": "业务数据发送",
    "day data update": "日考勤数据更新",
    "user data change to anther": "用户变更数据传递",
    "frm data done day": "日考勤处理",
    "frm config param": "考勤参数配置",
    "frm report exp": "考勤报表导出",
    "config param": "考勤参数配置",
    "config human": "人员配置",
    "data done day": "日考勤处理",
    "report exp": "考勤报表导出",
    "update net config": "网络配置更新",
    "register trip": "出差登记",
    "object helper": "数据库 Web Service",
    "service 1": "中心服务宿主",
    "frm update net config": "网络配置更新",
    "frm config human": "人员配置",
    "approve over time": "加班审批",
    "approve over time service": "加班审批服务",
    "approve buiness": "业务审批",
    "approve business query": "审批业务查询",
    "atm register trip": "出差登记",
    "my apply": "我的申请",
    "moa approve": "MOA 审批",
    "apply leave": "请假申请",
    "new remedy form": "新建补单",
    "atm immigration app": "出入境申请",
    "approve back home for public": "返乡申请审批",
    "apply back home for public change": "返乡申请变更",
    "apply evection": "出差申请",
    "kq day data js": "日考勤数据计算",
    "approve common service": "通用审批服务",
    "data": "数据访问",
    "data command f": "F 区数据指令（含义未确认）",
    "data command x": "X 区数据指令（含义未确认）",
    "frm net db config": "网络数据库配置",
    "frm config modular": "模块配置",
    "frm input user info": "人员数据导入",
    "kq timer 1": "考勤定时任务",
    "oracle helper": "Oracle 数据访问",
    "apply leave bl": "请假申请服务",
    "apply supplement bl": "补单申请服务",
}

_WORD_LABELS = {
    "sync": "同步",
    "synchronization": "同步",
    "syschronization": "同步",
    "dept": "部门",
    "department": "部门",
    "employee": "人员",
    "user": "用户",
    "info": "信息",
    "check": "校验",
    "data": "数据",
    "send": "发送",
    "distribute": "分发",
    "update": "更新",
    "day": "日",
    "card": "卡号",
    "car": "车牌",
    "leave": "请假",
    "evection": "出差",
    "trip": "出差",
    "overtime": "加班",
    "approve": "审批",
    "approval": "审批",
    "report": "报表",
    "config": "配置",
    "guard": "门岗",
    "attendance": "考勤",
    "kq": "考勤",
    "statistic": "统计",
    "statistics": "统计",
    "recess": "调休",
    "remedy": "补单",
    "manage": "管理",
    "management": "管理",
    "query": "查询",
    "import": "导入",
    "export": "导出",
    "service": "服务",
    "controller": "接口",
    "center": "中心",
    "client": "客户端",
    "server": "服务端",
}

_GENERIC_SYMBOLS = {
    "base",
    "common",
    "get",
    "key",
    "main",
    "object",
    "page_load",
    "select",
    "set",
    "util",
}

_AREA_LABELS = {
    "about-my": "我的考勤",
    "approve-business": "业务审批",
    "base-config": "基础配置",
    "day-data": "日考勤处理",
    "evection": "出差管理",
    "js-or-query": "考勤计算与查询",
    "leave": "请假管理",
    "out-in-manage": "出入境管理",
    "overtime": "加班管理",
    "recess": "调休管理",
    "remedy-form": "补单管理",
    "report": "报表",
    "right-allocate": "权限分配",
    "statistic": "考勤统计",
    "sys-manage": "系统管理",
    "trip-register": "出差登记",
    "web-controls": "公共页面控件",
}


def _identifier_words(value: str) -> tuple[str, ...]:
    normalized = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", value)
    normalized = re.sub(r"[_./#-]+", " ", normalized)
    return tuple(word.lower() for word in normalized.split() if word)


def _human_identifier(value: str) -> str:
    words = list(_identifier_words(value))
    while words and words[0] in {"frm", "form", "uc", "ac", "atm"}:
        words.pop(0)
    while words and words[-1] in {"bll", "dal", "dto", "model", "controller", "service"}:
        words.pop()
    key = " ".join(words)
    if key in _CAPABILITY_NAMES:
        return _CAPABILITY_NAMES[key]
    translated = [_WORD_LABELS.get(word, word) for word in words]
    if translated and all(part != word for part, word in zip(translated, words, strict=True)):
        return "".join(translated)
    return value.replace("_", " ") or "未命名能力"


def _unit_key(path: str) -> str:
    parts = [part for part in PurePosixPath(path).parts if part not in {".", ".."}]
    if not parts:
        return "root"
    if parts[0].lower() in {"src", "source", "app", "apps"} and len(parts) > 1:
        return "/".join(parts[:2])
    return parts[0]


def _unit_label(unit: str) -> str:
    leaf = unit.rsplit("/", 1)[-1]
    return _UNIT_LABELS.get(leaf.lower(), _human_identifier(leaf))


def _unit_priority(unit: str) -> int:
    leaf = unit.rsplit("/", 1)[-1].lower()
    return 0 if leaf in _UNIT_LABELS and leaf not in {"src", "app", "apps", "test", "tests"} else 1


def _unit_selection_rank(unit: str) -> tuple[int, str]:
    leaf = unit.rsplit("/", 1)[-1].lower()
    return _UNIT_SELECTION_ORDER.get(leaf, 100), unit


def _unit_cap(unit: str) -> int:
    leaf = unit.rsplit("/", 1)[-1].lower()
    if leaf == "center":
        return 14
    return 10 if _unit_priority(unit) == 0 else 4


def _file_stem(path: str) -> str:
    name = PurePosixPath(path).name
    for suffix in (".aspx.cs", ".ascx.cs", ".asmx.cs", ".designer.cs", ".cs"):
        if name.lower().endswith(suffix):
            return name[: -len(suffix)]
    return PurePosixPath(name).stem


def _meaningful_name(value: str) -> bool:
    words = _identifier_words(value)
    return bool(words) and not all(word in _GENERIC_SYMBOLS for word in words)


def _human_capabilities(
    document: MarkdownDocument,
    evidence: dict[str, EvidenceLocation],
    *,
    limit: int = 24,
) -> tuple[dict[str, object], ...]:
    groups: dict[tuple[str, str], dict[str, object]] = defaultdict(
        lambda: {
            "unit": "root",
            "title": "未命名能力",
            "symbols": [],
            "reads": set(),
            "writes": set(),
            "hosts": set(),
            "calls": set(),
            "configs": set(),
            "actions": set(),
            "interfaces": set(),
            "source": None,
            "score": 0,
            "priority": False,
        }
    )
    for row in _all_rows(document):
        if row.kind not in {
            "ModuleRow",
            "UiSurfaceRow",
            "InterfaceRow",
            "CommandRow",
            "ImplementationFactRow",
        }:
            continue
        location = _location(row, evidence)
        if location is None:
            continue
        stem = _file_stem(location.path)
        if stem.lower() in {"assemblyinfo", "reference", "designer", "global"}:
            continue
        group = groups[(location.repository, location.path)]
        group["unit"] = _unit_key(location.path)
        group["title"] = _human_identifier(stem)
        if group["source"] is None:
            group["source"] = location
        values = _values(row)
        if row.kind == "ModuleRow":
            name = str(values.get("qualified_name") or values.get("signature") or stem)
            if _meaningful_name(name):
                symbols = group["symbols"]
                if isinstance(symbols, list) and name not in symbols:
                    symbols.append(name)
                group["score"] = int(group["score"]) + 1
        elif row.kind == "UiSurfaceRow":
            group["title"] = _human_identifier(str(values.get("name") or stem))
            actions = group["actions"]
            if isinstance(actions, set):
                actions.update(
                    item.strip() for item in str(values.get("actions") or "").split(",") if item.strip()
                )
            group["score"] = int(group["score"]) + 12
        elif row.kind in {"InterfaceRow", "CommandRow"}:
            interfaces = group["interfaces"]
            if isinstance(interfaces, set):
                interfaces.add(
                    f"{values.get('method') or '-'} {values.get('path') or values.get('name') or '-'}"
                )
            group["score"] = int(group["score"]) + 14
        else:
            predicate = str(values.get("predicate") or "")
            target = str(values.get("object") or "-")
            target_set = {
                "reads": "reads",
                "writes": "writes",
                "hosts": "hosts",
                "calls": "calls",
                "configures": "configs",
                "uses": "calls",
            }.get(predicate)
            if target_set is not None and isinstance(group[target_set], set):
                group[target_set].add(target)
            group["score"] = int(group["score"]) + 10
        stem_words = set(_identifier_words(stem))
        if " ".join(_identifier_words(stem)) in _CAPABILITY_NAMES:
            group["score"] = int(group["score"]) + 40
            group["priority"] = True
        elif stem_words & {
            "approve",
            "apply",
            "config",
            "distribute",
            "report",
            "send",
            "sync",
            "synchronization",
            "syschronization",
            "update",
        }:
            group["score"] = int(group["score"]) + 8
    ranked = sorted(
        groups.values(),
        key=lambda item: (
            _unit_priority(str(item["unit"])),
            _unit_selection_rank(str(item["unit"])),
            -int(item["score"]),
            str(item["title"]),
        ),
    )
    selected: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()
    unit_counts: Counter[str] = Counter()
    by_unit: dict[str, list[dict[str, object]]] = defaultdict(list)
    for item in ranked:
        by_unit[str(item["unit"])].append(item)
    seed_units = sorted(
        by_unit,
        key=lambda unit: (
            _unit_priority(unit),
            _unit_selection_rank(unit),
            -int(by_unit[unit][0]["score"]),
        ),
    )[:6]
    seeded_ids: set[int] = set()
    for item in ranked:
        unit = str(item["unit"])
        if not item["priority"] or unit_counts[unit] >= _unit_cap(unit):
            continue
        identity = (unit, str(item["title"]))
        if identity in seen or not _meaningful_name(str(item["title"])):
            continue
        seen.add(identity)
        selected.append(item)
        unit_counts[unit] += 1
        seeded_ids.add(id(item))
        if len(selected) >= min(limit, 24):
            break
    for unit in seed_units:
        for item in by_unit[unit][:2]:
            identity = (str(item["unit"]), str(item["title"]))
            if identity in seen or not _meaningful_name(str(item["title"])):
                continue
            seen.add(identity)
            selected.append(item)
            unit_counts[unit] += 1
            seeded_ids.add(id(item))
            if len(selected) >= limit:
                return tuple(selected)
    for item in ranked:
        if id(item) in seeded_ids or unit_counts[str(item["unit"])] >= _unit_cap(str(item["unit"])):
            continue
        identity = (str(item["unit"]), str(item["title"]))
        if identity in seen or not _meaningful_name(str(item["title"])):
            continue
        seen.add(identity)
        selected.append(item)
        unit_counts[str(item["unit"])] += 1
        if len(selected) >= limit:
            break
    return tuple(selected)


def _capability_trigger(capability: dict[str, object]) -> str:
    interfaces = capability["interfaces"] if isinstance(capability["interfaces"], set) else set()
    actions = capability["actions"] if isinstance(capability["actions"], set) else set()
    hosts = capability["hosts"] if isinstance(capability["hosts"], set) else set()
    if interfaces:
        return ", ".join(sorted(interfaces)[:3])
    if actions:
        return "用户界面事件：" + ", ".join(sorted(actions)[:4])
    if "Windows Service" in hosts:
        return "Windows Service 后台调度（具体周期以配置为准）"
    return "内部代码路径调用"


def _capability_actor(capability: dict[str, object]) -> str:
    if capability["actions"]:
        return "界面使用者（具体角色未确认）"
    if capability["interfaces"]:
        return "接口调用方"
    if "Windows Service" in capability["hosts"]:
        return "后台服务账户"
    return "内部模块"


def _v2_capability_catalog(
    document: MarkdownDocument, evidence: dict[str, EvidenceLocation]
) -> list[str]:
    capabilities = _human_capabilities(document, evidence, limit=32)
    units: dict[str, list[dict[str, object]]] = defaultdict(list)
    for capability in capabilities:
        units[str(capability["unit"])].append(capability)
    lines = [
        "## 系统能力概览",
        "",
        "本清单描述源码中已经存在的行为能力。它不是原始产品需求；业务目标、优先级和未来范围仍需由需求材料确认。",
        "",
        "| 运行/代码单元 | 已识别能力 | 代表证据 |",
        "|---|---|---|",
    ]
    for unit, items in sorted(units.items(), key=lambda item: (-len(item[1]), item[0])):
        source = items[0]["source"] if isinstance(items[0]["source"], EvidenceLocation) else None
        lines.append(
            f"| **{_cell(_unit_label(unit))}** (`{_cell(unit)}`) | "
            f"{_cell('、'.join(str(item['title']) for item in items[:12]))} | {_source(source)} |"
        )
    if not capabilities:
        lines.append("| - | 当前没有足够证据形成业务能力归纳 | - |")
    lines.extend(
        [
            "",
            "## 已实现功能清单",
            "",
            "| 编号 | 功能 | 所属单元 | 触发入口 | 数据读写 | 状态 | 证据 |",
            "|---|---|---|---|---|---|---|",
        ]
    )
    for index, capability in enumerate(capabilities, 1):
        reads = capability["reads"] if isinstance(capability["reads"], set) else set()
        writes = capability["writes"] if isinstance(capability["writes"], set) else set()
        data = []
        if reads:
            data.append("读 " + ", ".join(sorted(reads)[:5]))
        if writes:
            data.append("写 " + ", ".join(sorted(writes)[:5]))
        source = capability["source"] if isinstance(capability["source"], EvidenceLocation) else None
        lines.append(
            f"| F-{index:02d} | **{_cell(str(capability['title']))}** | "
            f"{_cell(_unit_label(str(capability['unit'])))} | {_cell(_capability_trigger(capability))} | "
            f"{_cell('；'.join(data) or '未提取到显式表级读写')} | 代码已实现 | {_source(source)} |"
        )
    lines.extend(["", "## 功能详情", ""])
    for index, capability in enumerate(capabilities, 1):
        reads = capability["reads"] if isinstance(capability["reads"], set) else set()
        writes = capability["writes"] if isinstance(capability["writes"], set) else set()
        calls = capability["calls"] if isinstance(capability["calls"], set) else set()
        configs = capability["configs"] if isinstance(capability["configs"], set) else set()
        hosts = capability["hosts"] if isinstance(capability["hosts"], set) else set()
        actions = capability["actions"] if isinstance(capability["actions"], set) else set()
        interfaces = (
            capability["interfaces"] if isinstance(capability["interfaces"], set) else set()
        )
        symbols = capability["symbols"] if isinstance(capability["symbols"], list) else []
        source = capability["source"] if isinstance(capability["source"], EvidenceLocation) else None
        integrations = [*sorted(hosts), *sorted(calls), *sorted(interfaces)]
        lines.extend(
            [
                f"### F-{index:02d} {_cell(str(capability['title']))}",
                "",
                f"- **所属单元**：{_cell(_unit_label(str(capability['unit'])))}",
                f"- **使用者/调用方**：{_cell(_capability_actor(capability))}",
                f"- **触发入口**：{_cell(_capability_trigger(capability))}",
                f"- **已确认输入/读取**：{_cell(', '.join(sorted(reads)) or '未提取到显式表级读取')}",
                f"- **已确认输出/写入**：{_cell(', '.join(sorted(writes)) or '未提取到显式表级写入')}",
                f"- **界面操作**：{_cell(', '.join(sorted(actions)[:12]) or '无明确界面事件')}",
                f"- **运行与集成**：{_cell(', '.join(integrations[:12]) or '未提取到明确宿主、外部调用或接口')}",
                f"- **相关配置**：{_cell(', '.join(sorted(configs)[:12]) or '当前文件未提取到直接配置键')}",
                f"- **关键实现**：{_cell(', '.join(symbols[:12]) or _file_stem(source.path if source else ''))}",
                f"- **源码证据**：{_source(source)}",
                "- **确认边界**：以上只描述当前实现能够证明的入口、数据和集成事实；业务目标、完整流程顺序、权限与异常处理仍以明确需求或更直接证据为准。",
                "",
            ]
        )
    return [*lines, ""]


def _v2_requirements(
    document: MarkdownDocument, evidence: dict[str, EvidenceLocation]
) -> list[str]:
    explicit = _kind_rows(document, "RequirementRow")
    capabilities = _human_capabilities(document, evidence, limit=28)
    lines = [
        "## 文档性质",
        "",
        (
            f"当前快照包含 {len(explicit)} 条明确需求材料；下方另列源码反构的已实现行为。"
            if explicit
            else "当前没有明确提交的产品需求材料。本文将源码中已实现的行为整理为“现状规格”，不能替代未来需求决策。"
        ),
        "",
    ]
    if explicit:
        lines.extend(
            [
                "## 明确需求材料",
                "",
                "| ID | 标题 | 需求陈述 | 角色 | 优先级 | 证据 |",
                "|---|---|---|---|---|---|",
            ]
        )
        for row in explicit:
            values = _values(row)
            lines.append(
                f"| {_cell(str(values.get('external_id') or '-'))} | "
                f"{_cell(str(values.get('title') or '-'))} | {_cell(str(values.get('statement') or '-'))} | "
                f"{_cell(str(values.get('role') or '-'))} | {_cell(str(values.get('priority') or '-'))} | "
                f"{_source(_location(row, evidence))} |"
            )
        lines.append("")
    lines.extend(
        [
            "## 源码反构的现状规格",
            "",
            "| 编号 | 已实现行为 | 使用者/调用方 | 触发方式 | 已确认输入 | 已确认输出或副作用 | 证据 |",
            "|---|---|---|---|---|---|---|",
        ]
    )
    for index, capability in enumerate(capabilities, 1):
        reads = capability["reads"] if isinstance(capability["reads"], set) else set()
        writes = capability["writes"] if isinstance(capability["writes"], set) else set()
        source = capability["source"] if isinstance(capability["source"], EvidenceLocation) else None
        lines.append(
            f"| IMP-{index:02d} | **{_cell(str(capability['title']))}** | "
            f"{_cell(_capability_actor(capability))} | {_cell(_capability_trigger(capability))} | "
            f"{_cell(', '.join(sorted(reads)[:6]) or '未提取到显式数据输入')} | "
            f"{_cell(', '.join(sorted(writes)[:6]) or '由实现入口产生行为，具体结果需结合调用方确认')} | "
            f"{_source(source)} |"
        )
    constraints = _kind_rows(document, "PermissionRow", "StateRow", "ErrorRow")
    if constraints:
        lines.extend(["", "## 实现约束与权限", ""])
        for row in constraints:
            values = _values(row)
            statement = (
                values.get("expression")
                or values.get("statement")
                or values.get("description")
                or _example(values)
            )
            lines.append(
                f"- `{_cell(str(statement))}`（{_source(_location(row, evidence))}）"
            )
    lines.extend(
        [
            "",
            "## 需求确认边界",
            "",
            "- “代码已实现”不代表业务仍然需要，也不代表实现正确。",
            "- 未出现于源码或已提交需求材料中的角色、审批规则、状态枚举和性能指标不会补写。",
            "- 新需求开发应在本现状规格之上补充目标状态、变更范围和正式验收准则。",
            "",
        ]
    )
    return lines


def _unit_role(unit: str, rows: list[MarkdownRow]) -> str:
    lowered = unit.lower()
    values = [_values(row) for row in rows]
    hosted = {str(value.get("object")) for value in values if value.get("predicate") == "hosts"}
    if lowered in {"db", "database"} or "sql" in lowered:
        return "数据库结构与脚本"
    if lowered in {"client"}:
        return "桌面客户端"
    if lowered in {"web"}:
        return "Web 应用集合"
    if lowered in {"build", "pipeline"}:
        return "构建与交付"
    if lowered == "server":
        return "Windows 后台服务" if "Windows Service" in hosted else "服务端 / 节点端组件"
    if lowered == "service":
        return "ASMX 服务接口" if "ASMX Web Service" in hosted else "服务端组件"
    if lowered in {"center", "rightdataserver", "kqtimer"} or "Windows Service" in hosted:
        return "Windows 后台服务"
    if "ASMX Web Service" in hosted:
        return "服务接口组件"
    return "代码与运行单元"


def _v2_architecture(
    document: MarkdownDocument, evidence: dict[str, EvidenceLocation]
) -> list[str]:
    units: dict[tuple[str, str], dict[str, object]] = defaultdict(
        lambda: {"rows": [], "files": set(), "dependencies": set(), "configs": set(), "data": set(), "source": None}
    )
    for row in _all_rows(document):
        location = _location(row, evidence)
        if location is None:
            continue
        key = (location.repository, _unit_key(location.path))
        group = units[key]
        rows = group["rows"]
        if isinstance(rows, list):
            rows.append(row)
        files = group["files"]
        if isinstance(files, set):
            files.add(location.path)
        values = _values(row)
        if row.kind == "DependencyRow" and isinstance(group["dependencies"], set):
            group["dependencies"].add(str(values.get("name") or "-"))
        if row.kind == "ConfigurationRow" and isinstance(group["configs"], set):
            group["configs"].add(str(values.get("key") or "-"))
        if row.kind == "ImplementationFactRow" and values.get("predicate") in {"reads", "writes"} and isinstance(group["data"], set):
            group["data"].add(str(values.get("object") or "-"))
        if group["source"] is None:
            group["source"] = location
    ranked = sorted(units.items(), key=lambda item: (-len(item[1]["files"]), item[0]))[:20]
    capabilities_by_unit: dict[str, list[dict[str, object]]] = defaultdict(list)
    for capability in _human_capabilities(document, evidence, limit=64):
        capabilities_by_unit[str(capability["unit"])].append(capability)
    implementation_rows = _kind_rows(document, "ImplementationFactRow")
    host_rows = tuple(
        row for row in implementation_rows if _values(row).get("predicate") == "hosts"
    )
    framework_rows = tuple(
        row
        for row in _kind_rows(document, "ConfigurationRow")
        if _values(row).get("key") == "TargetFrameworkVersion"
    )
    database_links = tuple(
        row
        for row in _kind_rows(document, "DependencyRow")
        if _values(row).get("scope") == "database_link"
    )
    build_targets = tuple(
        row
        for row in implementation_rows
        if _values(row).get("predicate") == "configures"
        and str(_values(row).get("object") or "").startswith("build target:")
    )
    notable_dependencies = tuple(
        row
        for row in _kind_rows(document, "DependencyRow")
        if _values(row).get("scope") == "dotnet_reference"
        and any(
            token in str(_values(row).get("name") or "").lower()
            for token in (
                "ajax",
                "devexpress",
                "nant",
                "newtonsoft",
                "oracle",
                "quartz",
                "thoughtworks",
            )
        )
    )
    lines = [
        "## 系统上下文",
        "",
        "系统边界按源码中的顶层运行/代码单元划分，不再把整个 Git 仓库误画成单一组件。连线只表示已检测到的持久化交互。",
        "",
        "```mermaid",
        "flowchart LR",
        '    DATA[("数据库 / 持久化")]',
    ]
    for index, ((repository, unit), group) in enumerate(ranked, 1):
        node = f"U{index:02d}"
        rows = group["rows"] if isinstance(group["rows"], list) else []
        lines.append(f'    {node}["{_cell(_unit_label(unit))}\\n{_cell(_unit_role(unit, rows))}"]')
        if group["data"]:
            lines.append(f"    {node} --> DATA")
    lines.extend(
        [
            "```",
            "",
            "## 技术栈与运行形态",
            "",
            "本节只汇总项目文件直接声明的宿主、框架、构建依赖和数据库连接，不根据目录名补写技术选型。",
            "",
            "| 类别 | 已确认内容 | 代表证据 |",
            "|---|---|---|",
        ]
    )
    technology_rows: list[tuple[str, str, MarkdownRow]] = []
    seen_technology: set[tuple[str, str]] = set()
    for category, rows, value_key, limit in (
        ("运行宿主", host_rows, "object", 10),
        ("目标框架", framework_rows, "default", 8),
        ("数据库链接", database_links, "name", 12),
        ("构建目标", build_targets, "object", 12),
        ("关键运行依赖", notable_dependencies, "name", 12),
    ):
        category_count = 0
        for row in rows:
            value = str(_values(row).get(value_key) or "-")
            identity = (category, value)
            if identity in seen_technology:
                continue
            seen_technology.add(identity)
            technology_rows.append((category, value, row))
            category_count += 1
            if category_count >= limit:
                break
    for category, value, row in technology_rows:
        lines.append(
            f"| {_cell(category)} | `{_cell(value)}` | {_source(_location(row, evidence))} |"
        )
    if not technology_rows:
        lines.append("| - | 当前没有可识别的运行宿主、框架或构建元数据 | - |")
    lines.extend(
        [
            "",
            "## 运行与代码单元",
            "",
            "| 单元 | 运行形态与已确认能力 | 源文件 | 数据交互 | 代表依赖或配置 | 证据 |",
            "|---|---|---:|---|---|---|",
        ]
    )
    for (repository, unit), group in ranked:
        rows = group["rows"] if isinstance(group["rows"], list) else []
        dependencies = group["dependencies"] if isinstance(group["dependencies"], set) else set()
        configs = group["configs"] if isinstance(group["configs"], set) else set()
        data = group["data"] if isinstance(group["data"], set) else set()
        source = group["source"] if isinstance(group["source"], EvidenceLocation) else None
        capability_titles = [
            str(item["title"]) for item in capabilities_by_unit.get(unit, [])[:12]
        ]
        responsibility = _unit_role(unit, rows)
        if capability_titles:
            responsibility += "；" + "、".join(capability_titles)
        lines.append(
            f"| **{_cell(_unit_label(unit))}** (`{_cell(repository)}:{_cell(unit)}`) | "
            f"{_cell(responsibility)} | {len(group['files'])} | "
            f"{_cell(', '.join(sorted(data)[:8]) or '未提取到显式表级交互')} | "
            f"{_cell(', '.join([*sorted(dependencies)[:5], *sorted(configs)[:5]]) or '-')} | {_source(source)} |"
        )
    lines.extend(["", "## 关键单元职责", ""])
    for (repository, unit), group in ranked[:15]:
        rows = group["rows"] if isinstance(group["rows"], list) else []
        dependencies = group["dependencies"] if isinstance(group["dependencies"], set) else set()
        configs = group["configs"] if isinstance(group["configs"], set) else set()
        data = group["data"] if isinstance(group["data"], set) else set()
        source = group["source"] if isinstance(group["source"], EvidenceLocation) else None
        capability_titles = [
            str(item["title"]) for item in capabilities_by_unit.get(unit, [])[:12]
        ]
        lines.extend(
            [
                f"### {_cell(_unit_label(unit))} (`{_cell(repository)}:{_cell(unit)}`)",
                "",
                f"- **运行形态**：{_cell(_unit_role(unit, rows))}",
                f"- **已确认能力**：{_cell('、'.join(capability_titles) or '尚未归纳出稳定的业务能力名称')}",
                f"- **显式数据交互**：{_cell(', '.join(sorted(data)[:16]) or '未提取到表级读写')}",
                f"- **代表依赖**：{_cell(', '.join(sorted(dependencies)[:12]) or '未提取到外部或项目依赖')}",
                f"- **代表配置**：{_cell(', '.join(sorted(configs)[:12]) or '未提取到配置键')}",
                f"- **源码证据**：{_source(source)}",
                "",
            ]
        )
    return [*lines, ""]


def _v2_detailed_design(
    document: MarkdownDocument, evidence: dict[str, EvidenceLocation]
) -> list[str]:
    capabilities = _human_capabilities(document, evidence, limit=24)
    lines = [
        "## 设计总览",
        "",
        "详细设计按已实现能力组织，而不是按类和方法数量排序。读写关系来自显式源码调用；没有调用图证据时不声明执行先后。",
        "",
        "| 能力 | 单元 | 入口/触发 | 读取 | 写入 | 关键实现 |",
        "|---|---|---|---|---|---|",
    ]
    for capability in capabilities:
        reads = capability["reads"] if isinstance(capability["reads"], set) else set()
        writes = capability["writes"] if isinstance(capability["writes"], set) else set()
        symbols = capability["symbols"] if isinstance(capability["symbols"], list) else []
        lines.append(
            f"| **{_cell(str(capability['title']))}** | {_cell(_unit_label(str(capability['unit'])))} | "
            f"{_cell(_capability_trigger(capability))} | {_cell(', '.join(sorted(reads)[:5]) or '-')} | "
            f"{_cell(', '.join(sorted(writes)[:5]) or '-')} | {_cell(', '.join(symbols[:5]) or '-')} |"
        )
    lines.extend(["", "## 模块详细设计", ""])
    for index, capability in enumerate(capabilities, 1):
        source = capability["source"] if isinstance(capability["source"], EvidenceLocation) else None
        reads = capability["reads"] if isinstance(capability["reads"], set) else set()
        writes = capability["writes"] if isinstance(capability["writes"], set) else set()
        actions = capability["actions"] if isinstance(capability["actions"], set) else set()
        interfaces = capability["interfaces"] if isinstance(capability["interfaces"], set) else set()
        symbols = capability["symbols"] if isinstance(capability["symbols"], list) else []
        lines.extend(
            [
                f"### {index}. {_cell(str(capability['title']))}",
                "",
                f"- **所属单元**：{_cell(_unit_label(str(capability['unit'])))}",
                f"- **调用方/使用者**：{_cell(_capability_actor(capability))}",
                f"- **触发入口**：{_cell(_capability_trigger(capability))}",
                f"- **读取数据**：{_cell(', '.join(sorted(reads)) or '未提取到显式表级读取')}",
                f"- **写入数据**：{_cell(', '.join(sorted(writes)) or '未提取到显式表级写入')}",
                f"- **界面操作**：{_cell(', '.join(sorted(actions)[:10]) or '无明确界面事件')}",
                f"- **接口入口**：{_cell(', '.join(sorted(interfaces)[:10]) or '无明确外部接口')}",
                f"- **关键实现**：{_cell(', '.join(symbols[:10]) or _file_stem(source.path if source else ''))}",
                f"- **源码证据**：{_source(source)}",
                "- **未确认事项**：业务目标、完整异常分支和跨模块调用顺序仍需需求材料、调用图或运行时证据确认。",
                "",
            ]
        )
    issues = tuple(
        row
        for row in _kind_rows(document, "AnnotationRow")
        if _values(row).get("issue") in {"lossy_utf8_recovery", "unreadable_text_encoding"}
    )
    if issues:
        lines.extend(
            [
                "## 源码解析覆盖缺口",
                "",
                "原始文件字节仍由快照和 Capsule 绑定；LoreLoop 没有改写业务源码。损坏行上的候选事实不会进入基线。",
                "",
                "| 文件 | 状态 | 解码 | 替换字符 | 丢弃事实 | 证据 |",
                "|---|---|---|---:|---:|---|",
            ]
        )
        for row in issues:
            values = _values(row)
            status = (
                "轻微 UTF-8 损坏，受控恢复"
                if values.get("issue") == "lossy_utf8_recovery"
                else "无法安全解码，跳过语义解析"
            )
            lines.append(
                f"| `{_cell(str(values.get('path') or '-'))}` | {status} | "
                f"{_cell(str(values.get('selected_encoding') or '-'))} | "
                f"{int(values.get('replacement_count') or 0)} | "
                f"{int(values.get('dropped_fact_count') or 0)} | {_source(_location(row, evidence))} |"
            )
        lines.append("")
    return lines


def _v2_user_guide(
    document: MarkdownDocument, evidence: dict[str, EvidenceLocation]
) -> list[str]:
    rows = _kind_rows(document, "UiSurfaceRow", "CommandRow")
    groups: dict[tuple[str, str, str], list[MarkdownRow]] = defaultdict(list)
    for row in rows:
        location = _location(row, evidence)
        if location is None:
            continue
        domain = _normalized_domain(location.path)
        if domain == "shared-or-unclassified":
            domain = _file_stem(location.path)
        groups[(location.repository, _unit_key(location.path), domain)].append(row)
    lines = [
        "## 使用边界",
        "",
        "本文是源码反构的入口手册：可确认页面、命令和事件，但不会把事件处理器名称扩写成未经验证的完整操作步骤。",
        "",
        "## 用户入口与可执行操作",
        "",
        "| 功能区域 | 页面/命令数 | 代表入口 | 已确认操作 | 证据 |",
        "|---|---:|---|---|---|",
    ]
    ranked_groups = sorted(groups.items(), key=lambda item: (-len(item[1]), item[0]))
    for (_, unit, domain), unit_rows in ranked_groups[:30]:
        examples = [str(_values(row).get("name") or "-") for row in unit_rows[:6]]
        actions = sorted(
            {
                action.strip()
                for row in unit_rows
                for action in str(_values(row).get("actions") or "").split(",")
                if action.strip() and action.strip() != "Page_Load"
            }
        )
        source = _location(unit_rows[0], evidence)
        area = _AREA_LABELS.get(domain, _human_identifier(domain))
        lines.append(
            f"| **{_cell(_unit_label(unit))} / {_cell(area)}** | {len(unit_rows)} | "
            f"{_cell(', '.join(examples))} | {_cell(', '.join(actions[:8]) or '仅识别页面加载')} | {_source(source)} |"
        )
    if not groups:
        lines.append("| - | 当前没有可确认的 UI/CLI 入口 | - | - | - |")
    if groups:
        lines.extend(["", "## 关键入口详情", ""])
        for (_, unit, domain), unit_rows in ranked_groups[:20]:
            area = _AREA_LABELS.get(domain, _human_identifier(domain))
            lines.extend(
                [
                    "<details>",
                    f"<summary><strong>{_cell(_unit_label(unit))} / {_cell(area)}</strong> · {len(unit_rows)} 个入口</summary>",
                    "",
                    "| 页面/命令 | 已确认操作 | 入口 | 证据 |",
                    "|---|---|---|---|",
                ]
            )
            for row in unit_rows[:12]:
                values = _values(row)
                entry = values.get("entry") or values.get("path") or values.get("name") or "-"
                actions = str(values.get("actions") or "-")
                lines.append(
                    f"| **{_cell(str(values.get('name') or entry))}** | {_cell(actions)} | "
                    f"`{_cell(str(entry))}` | {_source(_location(row, evidence))} |"
                )
            lines.extend(["", "</details>", ""])
    lines.extend(
        [
            "",
            "## 操作说明的可信边界",
            "",
            "- 页面存在与事件处理器存在属于已确认事实。",
            "- 操作顺序、角色授权、提示文案和成功结果只有在源码或需求材料明确表达时才可作为正式手册内容。",
            "",
        ]
    )
    return lines


def _v2_acceptance(
    document: MarkdownDocument, evidence: dict[str, EvidenceLocation]
) -> list[str]:
    explicit = _kind_rows(document, "AcceptanceRow")
    capabilities = _human_capabilities(document, evidence, limit=18)
    lines = ["## 正式验收材料", ""]
    if explicit:
        lines.extend(
            f"- {_cell(_example(_values(row)))}（{_source(_location(row, evidence))}）"
            for row in explicit
        )
        lines.append("")
    else:
        lines.extend(["当前没有提交态正式验收条款。", ""])
    lines.extend(
        [
            "## 源码反构的验收候选",
            "",
            "以下场景用于理解和补测试，不代表已经执行或通过。每个 Then 只描述源码能够证明的副作用。",
            "",
        ]
    )
    scenario_index = 0
    for capability in capabilities:
        reads = capability["reads"] if isinstance(capability["reads"], set) else set()
        writes = capability["writes"] if isinstance(capability["writes"], set) else set()
        interfaces = capability["interfaces"] if isinstance(capability["interfaces"], set) else set()
        actions = capability["actions"] if isinstance(capability["actions"], set) else set()
        if not (writes or interfaces or actions):
            continue
        scenario_index += 1
        source = capability["source"] if isinstance(capability["source"], EvidenceLocation) else None
        lines.extend(
            [
                f"### AC-{scenario_index:02d} {_cell(str(capability['title']))}",
                "",
                f"- **Given**：{_cell('、'.join(sorted(reads)[:6]) + ' 可访问' if reads else '相关实现依赖和运行环境可用')}。",
                f"- **When**：通过 {_cell(_capability_trigger(capability))} 触发。",
                f"- **Then**：{_cell('更新 ' + '、'.join(sorted(writes)[:6]) if writes else '进入已识别接口或界面处理路径')}。",
                f"- **证据**：{_source(source)}",
                "",
            ]
        )
    tests = tuple(
        row
        for row in _kind_rows(document, "TestRow")
        if (
            (location := _location(row, evidence)) is not None
            and not {"examples", "example", "samples", "sample", "tools"}
            & {part.lower() for part in PurePosixPath(location.path).parts}
        )
    )
    lines.extend(["## 已存在测试证据", ""])
    if tests:
        for row in tests[:30]:
            values = _values(row)
            lines.append(
                f"- **{_cell(str(values.get('name') or '-'))}**（{_cell(str(values.get('framework') or 'unknown'))}）："
                f"{_cell(str(values.get('cases') or '-'))} "
                f"（{_source(_location(row, evidence))}）"
            )
    else:
        lines.append("当前没有与上述业务场景直接关联的可识别自动化测试。")
    return [*lines, ""]


def _v2_interfaces(
    document: MarkdownDocument, evidence: dict[str, EvidenceLocation]
) -> list[str]:
    lines = _render_interfaces(document, evidence)
    interfaces = _kind_rows(document, "InterfaceRow", "CommandRow", "WebInterfaceRow")
    permissions = _kind_rows(document, "PermissionRow")
    errors = _kind_rows(document, "ErrorRow")
    incomplete = sum(
        not _values(row).get("parameters") or not _values(row).get("return_type")
        for row in interfaces
    )
    lines.extend(
        [
            "## 契约可信边界",
            "",
            f"- {incomplete} 个接口缺少完整参数或返回结构；缺失字段不会由名称猜测。",
            (
                f"- 已识别 {len(permissions)} 条权限规则。"
                if permissions
                else "- 未发现可精确绑定到接口的权限规则；接口存在不代表任意角色均可调用。"
            ),
            (
                f"- 已识别 {len(errors)} 条结构化错误事实。"
                if errors
                else "- 未发现结构化错误码或异常响应契约。"
            ),
            "",
        ]
    )
    return lines


def _database_domain(table: str) -> str:
    leaf = table.split(".")[-1]
    tokens = [token for token in leaf.split("_") if token]
    if len(tokens) >= 2 and tokens[0] in {"AC", "ATM", "BASE", "GUARD", "KQ"}:
        return "_".join(tokens[:2])
    return tokens[0] if tokens else table


def _preferred_table_variant(
    items: list[MarkdownRow], evidence: dict[str, EvidenceLocation]
) -> list[MarkdownRow]:
    variants: dict[str, list[MarkdownRow]] = defaultdict(list)
    for row in items:
        if _values(row).get("record_type") not in {"table", "column"}:
            continue
        key = row.evidence_ids[0] if row.evidence_ids else row.record_id
        variants[key].append(row)
    if not variants:
        return []

    def rank(rows: list[MarkdownRow]) -> tuple[int, int, str]:
        location = _location(rows[0], evidence)
        path = location.path if location is not None else ""
        canonical = 0 if "/db/kqadmin/tables/" in f"/{path.lower()}" else 1
        columns = sum(_values(row).get("record_type") == "column" for row in rows)
        return canonical, -columns, path

    return min(variants.values(), key=rank)


def _v2_database(
    document: MarkdownDocument, evidence: dict[str, EvidenceLocation]
) -> list[str]:
    data_rows = _kind_rows(document, "CurrentDataRow")
    facts = _kind_rows(document, "ImplementationFactRow")
    by_table: dict[str, list[MarkdownRow]] = defaultdict(list)
    for row in data_rows:
        table = _values(row).get("table")
        if isinstance(table, str):
            by_table[table].append(row)
    usage = Counter(
        str(_values(row).get("object"))
        for row in facts
        if _values(row).get("predicate") in {"reads", "writes"}
    )
    ranked = sorted(
        by_table,
        key=lambda table: (
            -usage[table],
            -sum(
                _values(row).get("record_type") == "column"
                for row in _preferred_table_variant(by_table[table], evidence)
            ),
            table,
        ),
    )
    core = ranked[:40]
    domains = Counter(_database_domain(table) for table in by_table)
    lines = [
        "## 数据域总览",
        "",
        "本文件优先展示被业务代码显式读写或结构规模较大的核心实体；全量字段仍保存在 Agent 视图和源码 DDL 中。",
        "",
        "| 数据域 | 实体数 | 代表实体 |",
        "|---|---:|---|",
    ]
    for domain, count in domains.most_common(20):
        examples = [table for table in ranked if _database_domain(table) == domain][:8]
        lines.append(f"| `{_cell(domain)}` | {count} | {_cell(', '.join(examples))} |")
    lines.extend(
        [
            "",
            "## 核心实体",
            "",
            "| 实体 | 业务代码引用 | 字段 | 索引 | 主键 | 证据 |",
            "|---|---:|---:|---:|---|---|",
        ]
    )
    for table in core:
        items = by_table[table]
        variant = _preferred_table_variant(items, evidence)
        table_row = next((row for row in variant if _values(row).get("record_type") == "table"), None)
        columns = sum(_values(row).get("record_type") == "column" for row in variant)
        indexes = len(
            {
                str(_values(row).get("name") or row.record_id)
                for row in items
                if _values(row).get("record_type") == "index"
            }
        )
        primary = _values(table_row).get("primary_key") if table_row is not None else "-"
        lines.append(
            f"| `{_cell(table)}` | {usage[table]} | {columns} | {indexes} | {_cell(str(primary or '-'))} | "
            f"{_source(_location(table_row, evidence) if table_row else None)} |"
        )
    selected = set(core[:30])
    relations = tuple(
        row
        for row in _kind_rows(document, "RelationRow")
        if _values(row).get("table") in selected
        and _values(row).get("referenced_table") in selected
    )
    identifiers = {table: f"T{index:02d}" for index, table in enumerate(core[:30], 1)}
    lines.extend(
        [
            "",
            "## ER 关系图",
            "",
            "该图只表达源码 DDL 中的显式外键；没有外键时仅展示核心实体节点。",
            "",
            "```mermaid",
            "flowchart LR",
            *(f'    {identifiers[table]}["{_cell(table)}"]' for table in core[:30]),
        ]
    )
    for row in relations:
        values = _values(row)
        table = str(values.get("table"))
        referenced = str(values.get("referenced_table"))
        lines.append(
            f'    {identifiers[table]} -->|"{_cell(str(values.get("columns") or "FK"))}"| '
            f"{identifiers[referenced]}"
        )
    lines.extend(["```", "", "## 核心表字段详情", ""])
    for table in core[:20]:
        items = by_table[table]
        variant = _preferred_table_variant(items, evidence)
        columns = [row for row in variant if _values(row).get("record_type") == "column"]
        lines.extend(
            [
                "<details>",
                f"<summary><strong>{_cell(table)}</strong> · {len(columns)} 个字段 · 代码引用 {usage[table]} 次</summary>",
                "",
                "| 字段 | 类型 | 可空 | 主键 | 默认值 |",
                "|---|---|---|---|---|",
            ]
        )
        for row in columns:
            values = _values(row)
            lines.append(
                f"| `{_cell(str(values.get('name') or '-'))}` | {_cell(str(values.get('data_type') or '-'))} | "
                f"{_cell(_value(values.get('nullable')))} | {_cell(_value(values.get('primary_key')))} | "
                f"{_cell(_value(values.get('default')))} |"
            )
        lines.extend(["", "</details>", ""])
    lines.extend(
        [
            "## 全量实体索引",
            "",
            "<details>",
            f"<summary>展开全部 {len(by_table)} 个实体</summary>",
            "",
            "| 实体 | 字段数 | 代码引用 |",
            "|---|---:|---:|",
        ]
    )
    for table in sorted(by_table):
        columns = sum(
            _values(row).get("record_type") == "column"
            for row in _preferred_table_variant(by_table[table], evidence)
        )
        lines.append(f"| `{_cell(table)}` | {columns} | {usage[table]} |")
    lines.extend(["", "</details>", ""])
    return lines


def _v2_evidence_footer(
    document: MarkdownDocument, evidence: dict[str, EvidenceLocation]
) -> list[str]:
    files = {(location.repository, location.path) for location in evidence.values()}
    return [
        "## 证据与可信度",
        "",
        f"- 本文归纳自 {len(files)} 个源码文件、{len(evidence)} 个证据锚点。",
        "- 精确字段、符号、记录身份和原始字节摘要位于 Capsule Agent 视图。",
        f"- Package ID：`{document.package_id or '-'}`",
        "- 标注为“源码反构”或“验收候选”的内容描述当前实现，不等同于正式产品需求或已通过测试。",
        "",
    ]


def _render_human_v2(document: MarkdownDocument, paths: tuple[str, ...]) -> str:
    evidence = _locations(document)
    snapshot_label = "可验证工作树快照" if "working_tree" in document.authority else "干净 Git 提交快照"
    navigation = list(_HUMAN_V2_NAVIGATION.get(document.family, ()))
    kinds = {row.kind for row in _all_rows(document)}
    web_sections = tuple(
        (kind, title)
        for kind, title in _HUMAN_V2_WEB_SECTIONS.get(document.family, ())
        if kind in kinds
    )
    if document.family == "requirements":
        if "RequirementRow" not in kinds:
            navigation.remove("明确需求材料")
        if not kinds & {"PermissionRow", "StateRow", "ErrorRow"}:
            navigation.remove("实现约束与权限")
    if document.family == "detailed_design" and "AnnotationRow" not in kinds:
        navigation.remove("源码解析覆盖缺口")
    if document.family == "user_guide" and not kinds & {"UiSurfaceRow", "CommandRow"}:
        navigation.remove("关键入口详情")
    navigation.extend(title for _, title in web_sections)
    lines = [
        f"# {_cell(document.title)}",
        "",
        f"> 文档性质：证据化人类视图 · 来源：{snapshot_label} · 精确事实由独立 Capsule Agent 视图提供。",
        "",
        "## 快速导航",
        "",
        *(f"- [{_cell(title)}](#{_heading_anchor(title)})" for title in navigation),
        "- [证据与可信度](#证据与可信度)",
        "",
        "## 文档集",
        "",
        " · ".join(f"[{path[:-3]}]({path})" for path in paths),
        "",
    ]
    renderers = {
        "capability_catalog": _v2_capability_catalog,
        "requirements": _v2_requirements,
        "architecture": _v2_architecture,
        "detailed_design": _v2_detailed_design,
        "user_guide": _v2_user_guide,
        "acceptance": _v2_acceptance,
        "interface_contract": _v2_interfaces,
        "database_design": _v2_database,
    }
    renderer = renderers.get(document.family)
    if renderer is None:
        body: list[str] = []
        for section in document.sections:
            body.extend([f"## {_cell(section.title)}", "", *_generic_table(section.rows, evidence)])
    else:
        body = renderer(document, evidence)
    body.extend(_render_web_sections(document, evidence, web_sections))
    lines.extend(body)
    lines.extend(_v2_evidence_footer(document, evidence))
    return "\n".join(lines).rstrip() + "\n"


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
        source_issue_count = sum(
            _values(row).get("issue") in {"lossy_utf8_recovery", "unreadable_text_encoding"}
            for section in document.sections
            for row in section.rows
            if row.kind == "AnnotationRow"
        )
        if source_issue_count:
            gaps.append(
                f"有 {source_issue_count} 个源码文件存在编码损坏或无法安全解码；"
                "原始字节已绑定，但相关覆盖范围必须人工核对。"
            )
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
    return ["## 覆盖缺口与未确认事项", "", *(f"- {item}" for item in gaps), ""]


def render_markdown(
    document: MarkdownDocument,
    paths: tuple[str, ...],
    *,
    include_agent_appendix: bool = False,
    human_view_version: int = 1,
) -> str:
    """Render one normalized document model into deterministic Markdown."""
    if human_view_version not in {1, 2}:
        raise ValueError("unsupported human view version")
    if human_view_version == 2 and not include_agent_appendix:
        return _render_human_v2(document, paths)
    evidence = _locations(document)
    appendix = (
        _render_complete_knowledge_index(document, evidence) if include_agent_appendix else []
    )
    document_records = len({row.record_id for section in document.sections for row in section.rows})
    snapshot_label = (
        "可验证工作树快照" if "working_tree" in document.authority else "干净 Git 提交快照"
    )
    lines = [f"# {_cell(document.title)}", ""]
    lines.extend(
        [
            f"> 反构边界：{snapshot_label} · 证据记录：{document_records} · 完整性证明：Capsule。",
            (
                "> 阅读方式：正文用于理解和评审；文末证据附录保留补充可召回事实，未确认内容不会补写。"
                if appendix
                else "> 阅读方式：本文是人类视图；精确原子事实由 Capsule Agent 视图提供，"
                "两者通过同一 SemanticCore 和 replay 保持一致。"
            ),
            "",
            "## 文档集导航",
            "",
            " · ".join(f"[{path[:-3]}]({path})" for path in paths),
            "",
            "## 文档用途",
            "",
            _PURPOSES.get(document.family, "呈现提交态源码能够直接证明的事实。"),
            "",
        ]
    )
    lines.extend(_render_document_navigation(document.family, has_appendix=bool(appendix)))
    if "working_tree" in document.authority:
        lines.extend(
            [
                "> 快照状态：`working_tree`（可验证工作树快照）· 内容绑定当前 HEAD 及 staged、unstaged、"
                "untracked（未忽略）文件的实际字节；它不是已提交发布态。",
                "",
            ]
        )
    if document.family == "capability_catalog":
        lines.extend(
            _render_capability_catalog(
                document,
                evidence,
                separate_agent_view=not include_agent_appendix,
            )
        )
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
        lines.extend(_render_database(document, evidence))
    else:
        for section in document.sections:
            lines.extend([f"## {_cell(section.title)}", ""])
            lines.extend(_generic_table(section.rows, evidence))
    lines.extend(_gap_lines(document))
    lines.extend(appendix)
    lines.extend(_evidence_summary(document))
    lines.extend(
        [
            "## 版本与完整性",
            "",
            f"- Package ID：`{document.package_id or '-'}`",
            f"- Repository configuration：`{document.repository_digest}`",
            f"- SemanticCore records：{document.semantic_record_total}",
            (
                f"- 本文关联 Agent 记录：{document_records}"
                if not include_agent_appendix
                else f"- 本文归纳记录：{document_records}"
            ),
            "",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"
