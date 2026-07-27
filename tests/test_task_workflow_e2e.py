import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from loreloop.cli import main
from loreloop.evidence.chain import EvidenceChain
from loreloop.knowledge.endorsement import curate
from loreloop.knowledge.model import Channel, Curation, Entry, Kind, Source
from loreloop.knowledge.store import KnowledgeStore


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def test_natural_language_bug_flows_to_selected_tests_and_complete_report(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    repo = tmp_path / "project"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "LoreLoop E2E")
    _git(repo, "config", "user.email", "loreloop@example.invalid")
    (repo / "src").mkdir()
    (repo / "tests").mkdir()
    (repo / "src/city.py").write_text(
        "def normalize_city(value):\n    return value\n", encoding="utf-8"
    )
    (repo / "tests/test_city.py").write_text(
        "from src.city import normalize_city\n\n"
        "def test_city_whitespace_is_removed():\n"
        "    assert normalize_city(' 深圳 ') == '深圳'\n",
        encoding="utf-8",
    )
    (repo / "README.md").write_text("fixture\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "fixture")
    monkeypatch.chdir(repo)
    assert main(["init", "--no-skill"]) == 0
    capsys.readouterr()
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    with KnowledgeStore(repo / ".loreloop/knowledge.db") as store:
        entry = store.add(
            Entry(
                title="城市查询输入规范",
                content="城市名称进入查询前必须移除首尾空格。",
                kind=Kind.CONSTRAINT,
                source=Source(
                    channel=Channel.CODE,
                    locator=f"src/city.py@{head}",
                    snapshot_ref=head,
                    symbol="normalize_city",
                    line_start=1,
                    line_end=2,
                    excerpt="def normalize_city(value): return value",
                ),
            )
        )
        curate(
            store,
            EvidenceChain.for_workdir(repo),
            entry.id,
            Curation.APPROVED,
            datetime.now(timezone.utc),
        )

    # Existing operator work must not be attributed to this bug task.
    (repo / "README.md").write_text("operator draft before task\n", encoding="utf-8")
    assert main(["begin", "修复城市名称包含空格时查询失败的问题"]) == 0
    begun = capsys.readouterr()
    match = re.search(r"Run ID: (run-[A-Za-z0-9-]+)", begun.out)
    assert match is not None
    run_id = match.group(1)
    assert "Task kind: bug" in begun.out
    assert "城市名称进入查询前必须移除首尾空格" in begun.out
    assert "Established facts" in begun.out

    (repo / "src/city.py").write_text(
        "def normalize_city(value):\n    return value.strip()\n", encoding="utf-8"
    )

    assert main(["test", "select", run_id]) == 0
    selected = capsys.readouterr()
    assert "tests/test_city.py" in selected.out
    assert "README.md" not in selected.out
    assert "MUST (1)" in selected.out

    assert main(["test", "run", run_id, "--timeout", "30"]) == 0
    provisional = capsys.readouterr().out
    assert "1 passed, 0 failed" in provisional
    assert "provisional evidence" in provisional

    assert (
        main(
            [
                "task",
                "bug",
                run_id,
                "--id",
                "BUG-001",
                "--test-step",
                "以带首尾空格的城市名称调用 normalize_city",
                "--test-step",
                "检查返回值是否移除空格",
                "--description",
                "城市名称保留首尾空格，导致查询键不一致。",
                "--root-cause",
                "normalize_city 原样返回输入，没有执行规范化。",
                "--recommendation",
                "在查询前统一执行 strip，并保留回归测试。",
                "--fix-solution",
                "将 normalize_city 修改为返回 value.strip()。",
                "--modified-file",
                "src/city.py",
                "--test-proof",
                "tests/test_city.py 的回归用例通过。",
            ]
        )
        == 0
    )
    capsys.readouterr()

    assert (
        main(
            [
                "task",
                "summarize",
                run_id,
                "--analysis",
                "城市名称未去除首尾空格，导致查询键不一致。",
                "--implementation",
                "在城市名称进入查询前统一执行 strip，并保留回归测试。",
                "--acceptance",
                "带首尾空格的城市名称可以正常查询",
                "--risk",
                "尚未覆盖全角空格",
            ]
        )
        == 0
    )
    capsys.readouterr()

    assert main(["complete", run_id, "--confirm"]) == 0
    capsys.readouterr()
    assert main(["test", "prove", run_id, "--timeout", "30"]) == 0
    capsys.readouterr()

    assert main(["report", run_id]) == 0
    report = capsys.readouterr().out
    assert "## Verdict: ACCEPTED" in report
    assert "## Root cause or requirement analysis" in report
    assert "城市名称未去除首尾空格" in report
    assert "## Task understanding and change impact" in report
    assert "src/city.py" in report
    assert "## Selected tests and rationale" in report
    assert "tests/test_city.py" in report
    assert "## Provisional automated test execution" in report
    assert "## Checks (1 passed / 0 failed)" in report
    assert "## Knowledge context and source coverage" in report
    report_dir = repo / "workspace/change" / run_id
    acceptance = (report_dir / "acceptance-report.md").read_text(encoding="utf-8")
    tests = (report_dir / "test-report.md").read_text(encoding="utf-8")
    bugs = (report_dir / "bug-findings.md").read_text(encoding="utf-8")
    fixes = (report_dir / "fix-report.md").read_text(encoding="utf-8")
    assert "## Verdict: ACCEPTED" in acceptance
    assert "最终验收：通过" in tests
    assert "## BUG-001" in bugs
    assert "### 测试步骤" in bugs
    assert "### 问题描述" in bugs
    assert "### 根因分析" in bugs
    assert "### 修改建议" in bugs
    assert "## BUG-001" in fixes
    assert "### 根因确认" in fixes
    assert "### 修复方案" in fixes
    assert "`src/city.py`" in fixes
    assert "### 测试证明" in fixes
