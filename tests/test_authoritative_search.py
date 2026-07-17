from __future__ import annotations

from loreloop.knowledge.authoritative_search import (
    _best_snippet,
    _coverage_search_intent,
    _search_entries,
    _search_value_line,
)


def test_markdown_search_groups_visible_content_by_section() -> None:
    files = {
        "demo-详细设计.md": (
            "# demo 详细设计\n\n"
            "## 订单模块\n\n"
            "订单模块负责创建和取消订单。\n"
            "取消前必须检查订单状态。\n\n"
            "| 接口 | 约束 |\n"
            "|---|---|\n"
            "| cancel_order | 仅待支付订单可取消 |\n"
            "| refund_order | 已支付订单进入退款流程 |\n"
        ).encode()
    }

    entries = _search_entries(files, ("demo-详细设计.md",))

    assert len(entries) == 2
    assert entries[0].source.symbol == "订单模块"
    assert "订单模块负责创建和取消订单" in entries[0].content
    assert "取消前必须检查订单状态" in entries[0].content
    assert "cancel_order" in entries[1].content
    assert "refund_order" in entries[1].content


def test_markdown_search_ignores_mermaid_but_keeps_collapsed_facts() -> None:
    files = {
        "demo-系统架构.md": (
            "# demo 系统架构\n\n"
            "## 模块协作视图\n\n"
            "```mermaid\n"
            "flowchart LR\n"
            "A[GenericGraphNoise] --> B[OtherNoise]\n"
            "```\n\n"
            "<details>\n"
            "<summary>其余模块</summary>\n\n"
            "legacy_settlement_handler 处理历史结算。\n"
            "</details>\n"
        ).encode()
    }

    entries = _search_entries(files, ("demo-系统架构.md",))
    searchable = "\n".join(entry.content for entry in entries)

    assert "GenericGraphNoise" not in searchable
    assert "legacy_settlement_handler" in searchable
    assert "<details>" not in searchable
    assert "<summary>" not in searchable


def test_best_snippet_prefers_the_matching_line_inside_a_section_chunk() -> None:
    content = "\n".join(
        [
            "订单模块包含多种状态转换。",
            "创建订单时记录客户和金额。",
            "refund_order 仅允许财务管理员执行部分退款。",
            "取消订单会释放库存。",
        ]
    )

    snippet = _best_snippet(content, "refund_order", "")

    assert snippet.startswith("refund_order")
    assert "财务管理员" in snippet


def test_best_snippet_selects_one_fact_from_a_compact_html_row() -> None:
    content = (
        "| `repo:Dto.java` | 3 | getName — getName() · L1<br>"
        "setRecentAcquisitionCodeHis — setRecentAcquisitionCodeHis(String value) · L2<br>"
        "getStatus — getStatus() · L3 |"
    )

    snippet = _best_snippet(content, "setRecentAcquisitionCodeHis", "")

    assert snippet == (
        "setRecentAcquisitionCodeHis — setRecentAcquisitionCodeHis(String value) · L2"
    )
    assert "<br>" not in snippet


def test_best_snippet_marks_expansion_match_without_copying_expansion() -> None:
    content = "Maximum file size is 50MB."

    snippet = _best_snippet(content, "附件容量阈值", "upload limit")

    assert snippet == content


def test_best_snippet_prefers_matching_action_details_over_generic_fact_label() -> None:
    content = "\n".join(
        [
            "事实: EmployeeConfig",
            "actions: Page_Load btnSave_Click ShowEmployee txtPhone",
        ]
    )

    snippet = _best_snippet(content, "EmployeeConfig btnSave_Click txtPhone", "")

    assert snippet.startswith("actions:")


def test_coverage_inventory_only_participates_for_coverage_or_file_queries() -> None:
    assert not _coverage_search_intent("人员配置 保存按钮", "EmployeeConfig btnSave")
    assert _coverage_search_intent("哪些文件没有解析")
    assert _coverage_search_intent("coverage blind spots")
    assert _coverage_search_intent("Web/Employee.aspx")
    assert _coverage_search_intent("检查 .resx")


def test_agent_action_values_remain_one_searchable_line() -> None:
    assert _search_value_line("actions", "btnSave_Click\nShowEmployee('txtPhone')\n") == (
        "actions: btnSave_Click | ShowEmployee('txtPhone')"
    )
