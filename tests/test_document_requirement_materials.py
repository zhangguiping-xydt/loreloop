from __future__ import annotations

import pytest

from loreloop.knowledge.authoritative_records import DetectionError
from loreloop.knowledge.authoritative_requirements_input import detect_requirement_markdown


def test_requirement_material_parser_reads_tables_bullets_and_acceptance() -> None:
    # Given: a common bilingual requirement document with a table and bullet sections.
    markdown = """
# Account requirements

| ID | 需求描述 | 角色 | 优先级 | 验收标准 |
|---|---|---|---|---|
| REQ-001 | 用户可以创建账号 | administrator | P0 | 返回新账号 ID |

## 功能需求
- 用户可以停用账号

## Acceptance Criteria
- 停用后登录必须被拒绝
"""

    # When: committed Markdown is parsed without an agent.
    report = detect_requirement_markdown(markdown, ".", "docs/requirements.md")

    # Then: requirement and acceptance statements retain exact source lines.
    assert tuple(item.statement for item in report.requirements) == (
        "用户可以创建账号",
        "用户可以停用账号",
    )
    assert report.requirements[0].external_id == "REQ-001"
    assert report.requirements[0].role == "administrator"
    assert tuple(item.statement for item in report.acceptances) == (
        "返回新账号 ID",
        "停用后登录必须被拒绝",
    )
    assert report.acceptances[0].requirement_external_id == "REQ-001"


def test_requirement_material_parser_fails_when_no_contract_is_structured() -> None:
    # Given / When / Then: prose without an explicit requirement shape does not become authority.
    with pytest.raises(DetectionError, match="no structured statements"):
        _ = detect_requirement_markdown(
            "# Notes\n\nMaybe improve accounts someday.\n", ".", "notes.md"
        )
