import subprocess
from pathlib import Path

from loreloop.evidence.artifacts import ArtifactStore
from loreloop.evidence.chain import EvidenceChain
from loreloop.report.acceptance import RunSummary, render_report
from loreloop.workflow.execution import execute_task_test_plan
from loreloop.workflow.impact import create_task_test_plan, render_task_test_plan
from loreloop.workflow.model import TaskIntent
from loreloop.workflow.snapshot import (
    capture_task_source_snapshot,
    compare_task_source_snapshots,
)
from loreloop.workflow.summary import record_task_narrative


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "LoreLoop Test")
    _git(repo, "config", "user.email", "loreloop@example.invalid")
    (repo / "src").mkdir()
    (repo / "tests").mkdir()
    (repo / "src/calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (repo / "tests/test_calc.py").write_text(
        "from src.calc import add\n\ndef test_add():\n    assert add(1, 2) == 3\n",
        encoding="utf-8",
    )
    (repo / "README.md").write_text("initial\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "fixture")
    return repo


def test_task_intent_classifies_bug_and_feature() -> None:
    assert TaskIntent.from_text("修复城市查询报错").kind == "bug"
    assert TaskIntent.from_text("增加批量导入功能").kind == "feature"
    assert TaskIntent.from_text("整理项目结构").kind == "task"


def test_task_snapshot_excludes_preexisting_dirty_changes(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "README.md").write_text("operator change before task\n", encoding="utf-8")
    before = capture_task_source_snapshot(repo)

    (repo / "src/calc.py").write_text(
        "def add(a, b):\n    return int(a) + int(b)\n", encoding="utf-8"
    )
    after = capture_task_source_snapshot(repo)

    changes = compare_task_source_snapshots(before, after)
    assert [(item.path, item.kind) for item in changes] == [("src/calc.py", "modified")]


def test_test_plan_maps_changed_module_and_reports_missing_coverage(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    artifacts = ArtifactStore.for_workdir(repo)
    chain = EvidenceChain.for_workdir(repo)
    before = artifacts.save_json(capture_task_source_snapshot(repo))[0]
    run_id = "run-selection"
    chain.append(
        "delegation_prepared",
        {
            "run_id": run_id,
            "task": "修复计算错误",
            "source_snapshot_artifact": before,
        },
    )
    (repo / "src/calc.py").write_text(
        "def add(a, b):\n    return int(a) + int(b)\n", encoding="utf-8"
    )
    (repo / "src/uncovered.py").write_text("VALUE = 1\n", encoding="utf-8")

    plan, record = create_task_test_plan(
        repo,
        run_id,
        chain.verify(),
        chain,
        artifacts,
    )

    assert record.event == "task_test_plan_created"
    assert [(change.path, change.kind) for change in plan.changes] == [
        ("src/calc.py", "modified"),
        ("src/uncovered.py", "added"),
    ]
    must = [item for item in plan.selections if item.tier == "must"]
    missing = [item for item in plan.selections if item.tier == "missing"]
    assert [(item.path, item.name) for item in must] == [("tests/test_calc.py", "test_calc")]
    assert missing[0].name == "No mapped regression test for src/uncovered.py"
    assert plan.commands[0].argv == (
        "python",
        "-m",
        "pytest",
        "-q",
        "tests/test_calc.py",
    )
    markdown = render_task_test_plan(plan, "markdown")
    assert "## Coverage gaps" in markdown
    assert "src/uncovered.py" in markdown
    report = render_report(
        RunSummary(run_id, "修复计算错误", [], False),
        chain,
        artifacts,
        workdir=repo,
    )
    assert "## Task understanding and change impact" in report
    assert "## Selected tests and rationale" in report
    assert "No mapped regression test for src/uncovered.py" in report

    results = execute_task_test_plan(
        repo,
        run_id,
        chain.verify(),
        chain,
        artifacts,
        timeout=30,
    )
    assert [result.status for result in results] == ["passed"]
    evidence = artifacts.load(results[0].artifact)
    assert evidence["type"] == "command_evidence"
    assert evidence["exit_code"] == 0
    record_task_narrative(
        chain,
        run_id,
        "输入未规范化导致计算行为不一致。",
        "统一把输入转换为整数，并增加回归测试。",
        ("整数和字符串输入结果一致",),
        ("未覆盖浮点数输入",),
    )
    report = render_report(
        RunSummary(run_id, "修复计算错误", [], False),
        chain,
        artifacts,
        workdir=repo,
    )
    assert "## Provisional automated test execution" in report
    assert "provisional evidence" in report
    assert "## Root cause or requirement analysis" in report
    assert "输入未规范化" in report
    assert "## Known risks and limitations" in report


def test_shared_infrastructure_change_recommends_broad_tests(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "src/config.py").write_text("DEBUG = False\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "config")
    artifacts = ArtifactStore.for_workdir(repo)
    chain = EvidenceChain.for_workdir(repo)
    before = artifacts.save_json(capture_task_source_snapshot(repo))[0]
    chain.append(
        "delegation_prepared",
        {
            "run_id": "run-shared",
            "task": "调整公共配置",
            "source_snapshot_artifact": before,
        },
    )
    (repo / "src/config.py").write_text("DEBUG = True\n", encoding="utf-8")

    plan, _ = create_task_test_plan(
        repo,
        "run-shared",
        chain.verify(),
        chain,
        artifacts,
    )

    assert any(
        item.tier == "recommended" and item.path == "tests/test_calc.py" for item in plan.selections
    )


def test_test_plan_respects_configured_pytest_roots(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n', encoding="utf-8"
    )
    shadow_tests = {
        ".omo/evidence/reference/test_shadow.py": "from src.calc import add\ndef test_shadow(): assert add(1, 2) == 3\n",
        "eval/datasets/project/test_shadow.py": "from src.calc import add\ndef test_shadow(): assert add(1, 2) == 3\n",
        "src/example/test_shadow.py": "from src.calc import add\ndef test_shadow(): assert add(1, 2) == 3\n",
    }
    for relative, source in shadow_tests.items():
        candidate = repo / relative
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_text(source, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "test layout")
    artifacts = ArtifactStore.for_workdir(repo)
    chain = EvidenceChain.for_workdir(repo)
    before = artifacts.save_json(capture_task_source_snapshot(repo))[0]
    chain.append(
        "delegation_prepared",
        {
            "run_id": "run-authoritative-tests",
            "task": "修复计算错误",
            "source_snapshot_artifact": before,
        },
    )
    (repo / "src/calc.py").write_text(
        "def add(a, b):\n    return int(a) + int(b)\n", encoding="utf-8"
    )

    plan, _ = create_task_test_plan(
        repo,
        "run-authoritative-tests",
        chain.verify(),
        chain,
        artifacts,
    )

    selected_paths = {item.path for item in plan.selections if item.path is not None}
    assert selected_paths == {"tests/test_calc.py"}
    assert plan.commands[0].argv == (
        "python",
        "-m",
        "pytest",
        "-q",
        "tests/test_calc.py",
    )


def test_test_plan_excludes_non_authoritative_roots_without_pytest_config(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    (repo / "tests/test_calc.py").unlink()
    for relative in (
        ".omo/evidence/test_shadow.py",
        "eval/test_shadow.py",
        "examples/test_shadow.py",
    ):
        candidate = repo / relative
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_text(
            "from src.calc import add\ndef test_shadow(): assert add(1, 2) == 3\n",
            encoding="utf-8",
        )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "non-authoritative tests")
    artifacts = ArtifactStore.for_workdir(repo)
    chain = EvidenceChain.for_workdir(repo)
    before = artifacts.save_json(capture_task_source_snapshot(repo))[0]
    chain.append(
        "delegation_prepared",
        {
            "run_id": "run-fallback-filter",
            "task": "修复计算错误",
            "source_snapshot_artifact": before,
        },
    )
    (repo / "src/calc.py").write_text(
        "def add(a, b):\n    return int(a) + int(b)\n", encoding="utf-8"
    )

    plan, _ = create_task_test_plan(
        repo,
        "run-fallback-filter",
        chain.verify(),
        chain,
        artifacts,
    )

    assert not plan.commands
    assert [item.name for item in plan.selections] == ["No mapped regression test for src/calc.py"]


def test_test_plan_ignores_high_frequency_generic_source_tokens(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "src/calc.py").write_text(
        "def calculate_total(values):\n    return sum(values)\n", encoding="utf-8"
    )
    (repo / "tests/test_calc.py").write_text(
        "from src.calc import calculate_total\n\ndef test_total():\n    assert calculate_total([1, 2]) == 3\n",
        encoding="utf-8",
    )
    for index in range(12):
        (repo / f"tests/test_unrelated_{index}.py").write_text(
            f"def test_unrelated_{index}():\n    source = 'fixture'\n    assert source\n",
            encoding="utf-8",
        )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "larger test suite")
    artifacts = ArtifactStore.for_workdir(repo)
    chain = EvidenceChain.for_workdir(repo)
    before = artifacts.save_json(capture_task_source_snapshot(repo))[0]
    chain.append(
        "delegation_prepared",
        {
            "run_id": "run-discriminative-selection",
            "task": "修复总额计算",
            "source_snapshot_artifact": before,
        },
    )
    (repo / "src/calc.py").write_text(
        "def calculate_total(source):\n    return sum(int(value) for value in source)\n",
        encoding="utf-8",
    )

    plan, _ = create_task_test_plan(
        repo,
        "run-discriminative-selection",
        chain.verify(),
        chain,
        artifacts,
    )

    selected_paths = {item.path for item in plan.selections if item.path is not None}
    assert selected_paths == {"tests/test_calc.py"}


def test_test_plan_matches_declared_routes_but_not_comment_or_path_literals(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    (repo / "src/api.py").write_text(
        '@app.get("/health")\ndef health():\n    return {"status": "ok"}\n',
        encoding="utf-8",
    )
    (repo / "tests/test_healthcheck.py").write_text(
        'def test_ready(client):\n    assert client.get("/ready").status_code == 200\n',
        encoding="utf-8",
    )
    (repo / "tests/test_decoy.py").write_text(
        'def test_decoy():\n    comment = "//"\n    pointer = "/path"\n    assert comment and pointer\n',
        encoding="utf-8",
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "api route")
    artifacts = ArtifactStore.for_workdir(repo)
    chain = EvidenceChain.for_workdir(repo)
    before = artifacts.save_json(capture_task_source_snapshot(repo))[0]
    chain.append(
        "delegation_prepared",
        {
            "run_id": "run-route-selection",
            "task": "修改健康检查路由",
            "source_snapshot_artifact": before,
        },
    )
    (repo / "src/api.py").write_text(
        '@app.get("/ready")\ndef health():\n    return {"status": "ok"}\n',
        encoding="utf-8",
    )

    plan, _ = create_task_test_plan(
        repo,
        "run-route-selection",
        chain.verify(),
        chain,
        artifacts,
    )

    selected_paths = {item.path for item in plan.selections if item.path is not None}
    assert selected_paths == {"tests/test_healthcheck.py"}
    assert plan.selections[0].reason == "test covers changed route /ready"


def test_changed_test_file_does_not_fan_out_through_its_fixture_routes(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    (repo / "tests/test_other.py").write_text(
        'def test_other(client):\n    assert client.get("/health").status_code == 200\n',
        encoding="utf-8",
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "second test")
    artifacts = ArtifactStore.for_workdir(repo)
    chain = EvidenceChain.for_workdir(repo)
    before = artifacts.save_json(capture_task_source_snapshot(repo))[0]
    chain.append(
        "delegation_prepared",
        {
            "run_id": "run-changed-test",
            "task": "增加计算回归测试",
            "source_snapshot_artifact": before,
        },
    )
    (repo / "tests/test_calc.py").write_text(
        'def test_calc_route(client):\n    assert client.get("/health").status_code == 200\n',
        encoding="utf-8",
    )

    plan, _ = create_task_test_plan(
        repo,
        "run-changed-test",
        chain.verify(),
        chain,
        artifacts,
    )

    selected_paths = {item.path for item in plan.selections if item.path is not None}
    assert selected_paths == {"tests/test_calc.py"}
    assert plan.selections[0].reason == "test file changed in this task"
