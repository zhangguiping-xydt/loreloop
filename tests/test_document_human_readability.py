from __future__ import annotations

import subprocess
from pathlib import Path

from loreloop.cli import main


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

    detailed_overview = detailed.partition("## 完整知识索引")[0]
    user_guide_overview = user_guide.partition("## 完整知识索引")[0]
    acceptance_overview = acceptance.partition("## 完整知识索引")[0]
    assert len(detailed_overview.splitlines()) < 100
    assert "zz_deep_symbol_079" in detailed
    assert "record_id" not in "\n".join(
        path.read_text(encoding="utf-8") for path in output.glob("*.md")
    )
    assert "当前提交态没有可识别的需求" in requirements
    assert "VUE_APP_I18N_LOCALE" not in requirements
    assert "VUE_APP_I18N_LOCALE" in architecture
    assert "功能区域" in user_guide
    assert len(user_guide_overview.splitlines()) < 100
    assert "代表测试套件与用例" in acceptance
    assert len(acceptance_overview.splitlines()) < 100
    assert "## 接口域索引" in interface
    assert "### . · /srv/salary" in interface
    assert interface.count("/srv/salary/item-") == 40
    assert '"schema_version":3' in capsule
    assert '"ast":' not in capsule

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
    assert "readable-详细设计.md#模块与符号" in searched
    assert ".loreloop-export.json" not in searched
    assert "<details>" not in searched
