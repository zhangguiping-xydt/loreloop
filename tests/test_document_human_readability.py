from __future__ import annotations

import subprocess
from pathlib import Path

from loreloop.cli import main
from loreloop.knowledge.authoritative_markdown_render import (
    EvidenceLocation,
    MarkdownDocument,
    MarkdownRow,
    MarkdownSection,
    _human_capabilities,
    _human_identifier,
    _merged_user_entries,
)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _repository(tmp_path: Path) -> Path:
    repo = tmp_path / "project"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "LoreLoop Readability")
    _git(repo, "config", "user.email", "loreloop@example.invalid")

    routes = []
    functions = ["from fastapi import FastAPI", "app = FastAPI()"]
    for index in range(40):
        routes.append(f'{{ path: "/salary/page-{index}", component: Page{index:02d} }}')
        functions.extend(
            [
                f'@app.get("/srv/salary/item-{index}")',
                f"def salary_item_{index}(employee_id: str) -> dict:",
                f"    return {{'index': {index}}}",
                "",
            ]
        )
    functions.extend(
        [f"def aa_internal_symbol_{index:03d}(): return {index}" for index in range(80)]
    )
    functions.append("def zz_deep_symbol_079(): return 'search-only'")
    tests = "\n".join(f"def test_salary_case_{index:03d}(): assert True" for index in range(60))
    files = {
        "src/app.py": "\n".join(functions) + "\n",
        "src/router/index.ts": "export const routes = [" + ",".join(routes) + "];\n",
        "tests/test_salary.py": tests + "\n",
        ".env": "VUE_APP_I18N_LOCALE=zh_CN\n",
    }
    for relative, content in files.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "fixture")
    return repo


def test_large_fact_inventory_renders_as_human_views_and_remains_searchable(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    repo = _repository(tmp_path)
    output = tmp_path / "baseline"
    monkeypatch.chdir(repo)

    assert (
        main(
            [
                "knowledge",
                "export",
                "--format",
                "docs",
                "--output",
                str(output),
                "--project-name",
                "readable",
            ]
        )
        == 0
    )
    _ = capsys.readouterr()

    detailed = (output / "readable-详细设计.md").read_text(encoding="utf-8")
    interface = (output / "readable-接口契约.md").read_text(encoding="utf-8")
    requirements = (output / "readable-需求规格.md").read_text(encoding="utf-8")
    architecture = (output / "readable-系统架构.md").read_text(encoding="utf-8")
    user_guide = (output / "readable-用户手册.md").read_text(encoding="utf-8")
    acceptance = (output / "readable-验收规格.md").read_text(encoding="utf-8")
    capsule = (output / ".loreloop-export.json").read_text(encoding="utf-8")

    appendix_heading = "## 证据附录：完整可召回事实"
    assert appendix_heading not in detailed
    assert appendix_heading not in architecture
    assert appendix_heading not in user_guide
    assert appendix_heading not in acceptance
    assert len(detailed.splitlines()) < 500
    assert "## 快速导航" in detailed
    assert "## 设计总览" in detailed
    assert "## 模块详细设计" in detailed
    assert "zz_deep_symbol_079" not in detailed
    assert "record_id" not in "\n".join(
        path.read_text(encoding="utf-8") for path in output.glob("*.md")
    )
    assert "当前没有明确提交的产品需求材料" in requirements
    assert "当前实现规格（As-is）" in requirements
    assert "VUE_APP_I18N_LOCALE" not in requirements
    assert "VUE_APP_I18N_LOCALE" in architecture
    assert "用户入口与可执行操作" in user_guide
    assert "## 使用前准备与故障处理" in user_guide
    assert len(user_guide.splitlines()) < 200
    assert "已存在测试证据" in acceptance
    assert "## 验收环境、数据与判定规则" in acceptance
    assert len(acceptance.splitlines()) < 200
    assert "## 接口域索引" in interface
    assert "### . · /srv/salary" in interface
    assert interface.count("/srv/salary/item-") == 40
    assert '"schema_version":5' in capsule
    assert '"ast":' not in capsule
    assert "zz_deep_symbol_079" in capsule
    assert "证据化人类视图" in detailed
    assert "精确事实由独立 Capsule Agent 视图提供" in detailed
    assert "## 功能关联与交付检查" in (output / "readable-功能清单.md").read_text(encoding="utf-8")
    assert "## 需求追踪与非功能要求" in requirements
    assert "## 集成、部署与运行质量" in architecture
    assert "## 状态、异常与变更验证" in detailed

    assert (
        main(
            [
                "knowledge",
                "search",
                "zz_deep_symbol_079",
                "--package",
                str(output),
                "--limit",
                "3",
            ]
        )
        == 0
    )
    searched = capsys.readouterr().out
    assert "zz_deep_symbol_079" in searched
    assert "readable-详细设计.md#Agent视图 · 模块与符号" in searched
    assert ".loreloop-export.json" not in searched
    assert "<details>" not in searched


def test_human_interface_contract_expands_csharp_request_and_response_models(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    repo = tmp_path / "contract-project"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "LoreLoop Contract")
    _git(repo, "config", "user.email", "loreloop@example.invalid")
    service = repo / "EmployeeService.asmx.cs"
    service.write_text(
        "using System.Web.Services;\nusing Acme.Contracts;\n"
        "public class EmployeeService : WebService {\n"
        "  [WebMethod]\n"
        "  public EmployeeResponse GetEmployee(EmployeeRequest request) {\n"
        "    try { return null; } catch (System.Exception) { return null; }\n"
        "  }\n"
        "}\n",
        encoding="utf-8",
    )
    (repo / "Unrelated.cs").write_text(
        "public class Unrelated {\n  public void GetEmployee() { db.BeginTransaction(); }\n}\n",
        encoding="utf-8",
    )
    models = repo / "EmployeeModels.cs"
    models.write_text(
        "namespace Acme.Contracts {\n"
        "public class EmployeeRequest {\n"
        "  public MessageHeader Header { get; set; }\n"
        "  public string EmployeeId { get; set; }\n"
        "}\n"
        "public class MessageHeader {\n"
        "  public string SourceSystem { get; set; }\n"
        "}\n"
        "public class EmployeeResponse {\n"
        "  public string Status { get; set; }\n"
        "  public Employee Result { get; set; }\n"
        "}\n"
        "public class Employee {\n"
        "  public string Name { get; set; }\n"
        "  public int Age { get; set; }\n"
        "}\n"
        "}\n",
        encoding="utf-8",
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "fixture")
    output = tmp_path / "contract-baseline"
    monkeypatch.chdir(repo)

    assert (
        main(
            [
                "knowledge",
                "export",
                "--format",
                "docs",
                "--output",
                str(output),
                "--project-name",
                "contract",
            ]
        )
        == 0
    )
    _ = capsys.readouterr()

    interface = (output / "contract-接口契约.md").read_text(encoding="utf-8")
    assert "## 契约使用说明" in interface
    assert "request.Header.SourceSystem" in interface
    assert "request.EmployeeId" in interface
    assert "response.Result.Name" in interface
    assert "response.Result.Age" in interface
    assert "业务必填未确认" in interface
    assert "## 契约完整性清单" in interface
    assert "结构联调可用" in interface
    assert "exception-handler:System.Exception" in interface
    assert "transaction:begin" not in interface
    assert "UnusedInternalModel" not in interface
    assert main(["knowledge", "replay", str(output)]) == 0
    _ = capsys.readouterr()
    assert (
        main(
            [
                "knowledge",
                "search",
                "SourceSystem",
                "--package",
                str(output),
            ]
        )
        == 0
    )
    searched = capsys.readouterr().out
    assert "事实: SourceSystem" in searched
    assert "contract-接口契约.md#Agent视图 · 接口数据结构" in searched


def test_human_capability_selection_preserves_core_runtime_features() -> None:
    center = (
        "Synchronization_Dept",
        "Synchronization_CheckUserInfo",
        "Syschronization_CardNo",
        "SyschCarNo",
        "DataDistribute",
        "DataSend",
    )
    web = (
        "ApplyLeave",
        "NewRemedyForm",
        "ApplyEvection",
        "MoaApprove",
        "ApproveBusinessQuery",
        "ApproveBuiness",
        "ApproveOverTime",
        "MyApply",
        "ApproveBackHomeForPublic",
        "ApplyBackHomeForPublicChange",
    )
    rows: list[MarkdownRow] = []
    evidence: list[tuple[str, EvidenceLocation]] = []
    for index, name in enumerate((*center, *web), 1):
        evidence_id = f"E-{index:02d}"
        unit = "Center/Business/Business" if name in center else "Web/ATM/UI/Web"
        rows.append(
            MarkdownRow(
                "ModuleRow",
                f"R-{index:02d}",
                (("qualified_name", name), ("signature", name)),
                (evidence_id,),
            )
        )
        evidence.append((evidence_id, EvidenceLocation(".", f"{unit}/{name}.cs", 1)))
    document = MarkdownDocument(
        "demo 功能清单",
        "capability_catalog",
        "git_snapshot",
        None,
        "digest",
        len(rows),
        (MarkdownSection("模块", tuple(rows)),),
        tuple(evidence),
    )

    selected = _human_capabilities(document, dict(evidence), limit=10)
    titles = [str(item["title"]) for item in selected]

    assert set(titles[:6]) == {
        "部门数据同步",
        "人员信息同步与校验",
        "卡号同步",
        "车牌号同步",
        "配置与数据分发",
        "业务数据发送",
    }


def test_legacy_form_names_are_rendered_as_human_capabilities() -> None:
    assert _human_identifier("FrmNetDbConfig") == "网络数据库配置"
    assert _human_identifier("FrmInputUserInfo") == "人员数据导入"
    assert _human_identifier("frmReportExpDept") == "部门报表导出"
    assert _human_identifier("KQTimer1") == "考勤定时任务"


def test_human_user_guide_merges_markup_and_codebehind_for_one_entry() -> None:
    rows = [
        MarkdownRow(
            "UiSurfaceRow",
            "UI-1",
            (
                ("name", "ZTE.ATM.EmployeePage"),
                ("entry", "Web/Employee.aspx"),
                (
                    "actions",
                    "click:javascript:ShowEmployee(this.id,'txtPhone');\nclick:btnSave_Click",
                ),
            ),
            ("E-1",),
        ),
        MarkdownRow(
            "UiSurfaceRow",
            "UI-2",
            (
                ("name", "EmployeePage"),
                ("entry", "Web/Employee.aspx"),
                ("actions", "Page_Load, btnSave_Click"),
            ),
            ("E-2",),
        ),
    ]
    evidence = {
        "E-1": EvidenceLocation(".", "Web/Employee.aspx", 1),
        "E-2": EvidenceLocation(".", "Web/Employee.aspx.cs", 10),
    }

    entries = _merged_user_entries(rows, evidence)

    assert len(entries) == 1
    assert entries[0]["name"] == "EmployeePage"
    assert entries[0]["actions"] == {
        "click:javascript:ShowEmployee(this.id,'txtPhone');",
        "click:btnSave_Click",
        "Page_Load",
        "btnSave_Click",
    }
