"""Deterministic source-change to test-impact selection."""

from __future__ import annotations

import configparser
import json
import re
import shlex
import tomllib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from ..evidence.artifacts import ArtifactStore
from ..evidence.chain import EvidenceChain, EvidenceRecord
from ..knowledge.authoritative_detector_tests import (
    detect_test_source,
    is_supported_test_evidence_path,
    is_web_scenario_path,
)
from ..webexplore.scenarios import parse_web_scenario
from .model import SourceChange, TaskIntent, TaskTestPlan, TestCommand, TestSelection
from .snapshot import capture_task_source_snapshot, compare_task_source_snapshots

TASK_TEST_PLAN_EVENT = "task_test_plan_created"
_ROUTE_CALL = re.compile(
    r"""(?ix)
    (?:
        (?:@[A-Za-z_$][A-Za-z0-9_$]*\.)?
        (?:get|post|put|patch|delete|options|head|route|request|
           getmapping|postmapping|putmapping|patchmapping|deletemapping|
           httpget|httppost|httpput|httppatch|httpdelete)
        |
        [A-Za-z_$][A-Za-z0-9_$]*\.
        (?:get|post|put|patch|delete|options|head|request|getasync|postasync)
    )
    \s*[\(\[]\s*['\"`](?P<route>/[^'\"`\s]{1,200})['\"`]
    """
)
_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_-]{2,}")
_SOURCE_SYMBOL = re.compile(
    r"\b(?:def|class|function|interface|type|const|let|var|func)\s+"
    r"(?P<name>[A-Za-z_$][A-Za-z0-9_$-]{2,})"
)
_SHARED = re.compile(
    r"(^|/)(auth|router|routing|middleware|config|configuration|client|database|db|"
    r"common|shared|base|conftest|package|pyproject|requirements)([._/-]|$)|"
    r"(^|/)(package-lock|pnpm-lock|yarn\.lock|uv\.lock)$",
    re.IGNORECASE,
)
_IGNORED_TOKENS = {
    "src",
    "test",
    "tests",
    "spec",
    "index",
    "main",
    "app",
    "code",
    "module",
    "utils",
    "util",
}
_NON_AUTHORITATIVE_TEST_ROOTS = frozenset(
    {".omo", "artifacts", "baseline", "baselines", "eval", "example", "examples"}
)


@dataclass(frozen=True, slots=True)
class _InventoryTest:
    repository: str
    root: Path
    path: str
    name: str
    framework: str
    source: str
    scenario_id: str | None = None


def create_task_test_plan(
    workdir: Path,
    run_id: str,
    records: list[EvidenceRecord],
    chain: EvidenceChain,
    artifacts: ArtifactStore,
) -> tuple[TaskTestPlan, EvidenceRecord]:
    preparation = _preparation(records, run_id)
    before_sha = preparation.payload.get("source_snapshot_artifact")
    if not isinstance(before_sha, str):
        raise ValueError(
            "prepared run predates task source snapshots; start a new run with `loreloop begin`"
        )
    before = artifacts.load(before_sha)
    after = capture_task_source_snapshot(workdir)
    after_sha = artifacts.save_json(after)[0]
    changes = compare_task_source_snapshots(before, after)
    inventory = _test_inventory(after)
    intent = TaskIntent.from_text(str(preparation.payload.get("task", "")))
    selections = _select(changes, inventory, after)
    commands = _commands(selections, inventory)
    plan = TaskTestPlan(run_id, intent, changes, selections, commands)
    artifact = artifacts.save_json(plan.to_json())[0]
    record = chain.append(
        TASK_TEST_PLAN_EVENT,
        {
            "run_id": run_id,
            "plan_artifact": artifact,
            "source_snapshot_before": before_sha,
            "source_snapshot_after": after_sha,
            "changes": len(changes),
            "must": sum(item.tier == "must" for item in selections),
            "recommended": sum(item.tier == "recommended" for item in selections),
            "missing": sum(item.tier == "missing" for item in selections),
        },
    )
    return plan, record


def latest_task_test_plan(
    run_id: str, records: list[EvidenceRecord], artifacts: ArtifactStore
) -> TaskTestPlan | None:
    record = next(
        (
            item
            for item in reversed(records)
            if item.event == TASK_TEST_PLAN_EVENT and item.payload.get("run_id") == run_id
        ),
        None,
    )
    if record is None or not isinstance(record.payload.get("plan_artifact"), str):
        return None
    return _parse_plan(artifacts.load(record.payload["plan_artifact"]))


def render_task_test_plan(plan: TaskTestPlan, format_name: str) -> str:
    if format_name == "json":
        return json.dumps(plan.to_json(), ensure_ascii=False, indent=2) + "\n"
    if format_name == "markdown":
        return _render_markdown(plan)
    if format_name != "summary":
        raise ValueError(f"unsupported task test-plan format: {format_name}")
    lines = [
        f"Task test plan: {plan.run_id} [{plan.intent.kind}]",
        f"changes: {len(plan.changes)}",
    ]
    for tier in ("must", "recommended", "missing"):
        selected = [item for item in plan.selections if item.tier == tier]
        lines.append(f"{tier.upper()} ({len(selected)})")
        for item in selected:
            location = f"{item.repository}:{item.path}" if item.path else item.repository
            lines.append(f"- {item.name} [{location}] — {item.reason}")
    if plan.commands:
        lines.append("COMMANDS")
        for command in plan.commands:
            lines.append(f"- [{command.repository}] {shlex.join(command.argv)}")
    return "\n".join(lines) + "\n"


def _preparation(records: list[EvidenceRecord], run_id: str) -> EvidenceRecord:
    matches = [
        item
        for item in records
        if item.event in {"delegation_prepared", "delegation_completed"}
        and item.payload.get("run_id") == run_id
        and isinstance(item.payload.get("source_snapshot_artifact"), str)
    ]
    if not matches:
        raise ValueError(f"no source-bound prepared run found for {run_id}")
    return matches[0]


def _test_inventory(snapshot: dict[str, Any]) -> tuple[_InventoryTest, ...]:
    repositories = snapshot.get("repositories")
    if not isinstance(repositories, dict):
        return ()
    inventory: list[_InventoryTest] = []
    for alias, data in sorted(repositories.items()):
        if not isinstance(alias, str) or not isinstance(data, dict):
            continue
        root_raw = data.get("root")
        files = data.get("files")
        if not isinstance(root_raw, str) or not isinstance(files, dict):
            continue
        root = Path(root_raw)
        pytest_roots = _pytest_test_roots(root)
        for path in sorted(files):
            if not isinstance(path, str) or not is_supported_test_evidence_path(path):
                continue
            if not _is_authoritative_test_path(path, pytest_roots):
                continue
            candidate = root / path
            try:
                source = _read_text(candidate)
                report = detect_test_source(source, alias, path)
            except (OSError, ValueError):
                continue
            scenario_id = None
            if is_web_scenario_path(path):
                try:
                    scenario_id = parse_web_scenario(json.loads(source)).scenario_id
                except (ValueError, json.JSONDecodeError):
                    scenario_id = None
            for test in report.tests:
                inventory.append(
                    _InventoryTest(
                        alias,
                        root,
                        path,
                        test.name,
                        test.framework,
                        source,
                        scenario_id,
                    )
                )
    return tuple(inventory)


def _is_authoritative_test_path(path: str, pytest_roots: tuple[str, ...]) -> bool:
    pure = PurePosixPath(path)
    parts = tuple(part.casefold() for part in pure.parts)
    if not parts:
        return False
    if pure.suffix.casefold() == ".py" and pytest_roots:
        return any(_is_within_test_root(pure, root) for root in pytest_roots)
    return parts[0] not in _NON_AUTHORITATIVE_TEST_ROOTS


def _is_within_test_root(path: PurePosixPath, root: str) -> bool:
    root_path = PurePosixPath(root)
    if root_path.is_absolute() or ".." in root_path.parts:
        return False
    path_parts = tuple(part.casefold() for part in path.parts)
    root_parts = tuple(part.casefold() for part in root_path.parts if part not in {"", "."})
    return bool(root_parts) and path_parts[: len(root_parts)] == root_parts


def _pytest_test_roots(root: Path) -> tuple[str, ...]:
    readers = (
        (root / "pytest.ini", "pytest"),
        (root / "pyproject.toml", "pyproject"),
        (root / "tox.ini", "pytest"),
        (root / "setup.cfg", "tool:pytest"),
    )
    for path, section in readers:
        if not path.is_file():
            continue
        try:
            if section == "pyproject":
                raw = tomllib.loads(path.read_text(encoding="utf-8"))
                value = (
                    raw.get("tool", {}).get("pytest", {}).get("ini_options", {}).get("testpaths")
                )
            else:
                parser = configparser.ConfigParser(interpolation=None)
                parser.read(path, encoding="utf-8")
                value = parser.get(section, "testpaths", fallback=None)
        except (
            AttributeError,
            OSError,
            TypeError,
            UnicodeError,
            tomllib.TOMLDecodeError,
            configparser.Error,
        ):
            continue
        roots = _normalize_test_roots(value)
        if roots:
            return roots
    return ()


def _normalize_test_roots(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        candidates = value.split()
    elif isinstance(value, list) and all(isinstance(item, str) for item in value):
        candidates = value
    else:
        return ()
    roots: list[str] = []
    for candidate in candidates:
        candidate_path = PurePosixPath(candidate)
        if candidate_path.is_absolute() or ".." in candidate_path.parts:
            continue
        normalized = candidate_path.as_posix().strip("/")
        if normalized and normalized != "." and normalized not in roots:
            roots.append(normalized)
    return tuple(roots)


def _select(
    changes: tuple[SourceChange, ...],
    inventory: tuple[_InventoryTest, ...],
    snapshot: dict[str, Any],
) -> tuple[TestSelection, ...]:
    selected: dict[tuple[str, str, str], TestSelection] = {}
    mapped_changes: set[tuple[str, str]] = set()
    for change in changes:
        if is_supported_test_evidence_path(change.path):
            for test in inventory:
                if test.repository != change.repository or test.path != change.path:
                    continue
                key = (test.repository, test.path, test.name)
                selected[key] = TestSelection(
                    "must",
                    test.repository,
                    test.path,
                    test.name,
                    test.framework,
                    "test file changed in this task",
                )
            continue
        changed_source = _source(snapshot, change.repository, change.path)
        changed_tokens = _path_tokens(change.path) | _source_tokens(changed_source)
        changed_tokens = _discriminative_tokens(changed_tokens, inventory, change.repository)
        changed_routes = _route_literals(changed_source)
        shared = bool(_SHARED.search(change.path))
        for test in inventory:
            if test.repository != change.repository:
                continue
            tier, reason = _match_test(change, changed_tokens, changed_routes, shared, test)
            if tier is None:
                continue
            mapped_changes.add((change.repository, change.path))
            key = (test.repository, test.path, test.name)
            candidate = TestSelection(
                tier,
                test.repository,
                test.path,
                test.name,
                test.framework,
                reason,
            )
            previous = selected.get(key)
            if previous is None or (previous.tier == "recommended" and tier == "must"):
                selected[key] = candidate
    for change in changes:
        if is_supported_test_evidence_path(change.path):
            continue
        if (change.repository, change.path) not in mapped_changes:
            key = (change.repository, change.path, "coverage gap")
            selected[key] = TestSelection(
                "missing",
                change.repository,
                None,
                f"No mapped regression test for {change.path}",
                None,
                "changed source has no deterministic test mapping",
            )
    return tuple(
        sorted(
            selected.values(),
            key=lambda item: (
                {"must": 0, "recommended": 1, "missing": 2}[item.tier],
                item.repository,
                item.path or "",
                item.name,
            ),
        )
    )


def _match_test(
    change: SourceChange,
    changed_tokens: set[str],
    changed_routes: set[str],
    shared: bool,
    test: _InventoryTest,
) -> tuple[str | None, str]:
    change_stem = _normalized_stem(change.path)
    test_stem = _normalized_stem(test.path)
    if change_stem and change_stem == test_stem:
        return "must", f"test name matches changed module {change_stem}"
    route_match = next(iter(sorted(changed_routes & _route_literals(test.source))), None)
    if route_match:
        return "must", f"test covers changed route {route_match}"
    referenced = sorted(changed_tokens & _identifier_tokens(test.source))
    if referenced:
        return "must", f"test references changed module token {referenced[0]}"
    if shared:
        return "recommended", f"shared infrastructure changed: {change.path}"
    if _same_area(change.path, test.path):
        return "recommended", f"test is in the same feature area as {change.path}"
    return None, ""


def _commands(
    selections: tuple[TestSelection, ...], inventory: tuple[_InventoryTest, ...]
) -> tuple[TestCommand, ...]:
    by_key = {(item.repository, item.path, item.name): item for item in inventory}
    groups: dict[tuple[str, str], list[_InventoryTest]] = {}
    for selection in selections:
        if selection.tier == "missing" or selection.path is None:
            continue
        item = by_key.get((selection.repository, selection.path, selection.name))
        if item is not None:
            groups.setdefault((item.repository, item.framework), []).append(item)
    commands: list[TestCommand] = []
    for (repository, framework), tests in sorted(groups.items()):
        unique_paths = tuple(sorted({item.path for item in tests}))
        covers = tuple(sorted({item.name for item in tests}))
        argv: tuple[str, ...] | None
        if framework == "pytest":
            argv = ("python", "-m", "pytest", "-q", *unique_paths)
        elif framework == "vitest":
            argv = ("npx", "vitest", "run", *unique_paths)
        elif framework == "jest":
            argv = ("npx", "jest", *unique_paths)
        elif framework == "go-test":
            directories = tuple(
                sorted({f"./{PurePosixPath(path).parent.as_posix()}" for path in unique_paths})
            )
            argv = ("go", "test", *directories)
        elif framework == "loreloop-web":
            for item in tests:
                if item.scenario_id:
                    commands.append(
                        TestCommand(
                            repository,
                            ("loreloop", "web", "test", "run", item.scenario_id),
                            (item.name,),
                        )
                    )
            argv = None
        else:
            argv = None
        if argv is not None:
            commands.append(TestCommand(repository, argv, covers))
    return tuple(commands)


def _source(snapshot: dict[str, Any], alias: str, path: str) -> str:
    repositories = snapshot.get("repositories")
    data = repositories.get(alias) if isinstance(repositories, dict) else None
    root = data.get("root") if isinstance(data, dict) else None
    if not isinstance(root, str):
        return ""
    candidate = Path(root) / path
    try:
        return _read_text(candidate)
    except OSError:
        return ""


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("gb18030")


def _path_tokens(path: str) -> set[str]:
    pure = PurePosixPath(path)
    subjects = [pure.stem]
    if pure.stem.casefold() in {"__init__", "index", "main", "app"} and pure.parent.name:
        subjects.append(pure.parent.name)
    return {
        token.casefold()
        for token in _TOKEN.findall(" ".join(subjects))
        if token.casefold() not in _IGNORED_TOKENS
    }


def _source_tokens(source: str) -> set[str]:
    tokens = {
        match.group("name").casefold()
        for match in _SOURCE_SYMBOL.finditer(source)
        if not match.group("name").startswith("_")
    }
    return {token for token in tokens if len(token) >= 4 and token not in _IGNORED_TOKENS}


def _identifier_tokens(source: str) -> set[str]:
    return {token.casefold() for token in _TOKEN.findall(source)}


def _route_literals(source: str) -> set[str]:
    return {
        route.casefold()
        for match in _ROUTE_CALL.finditer(source)
        if (route := match.group("route")) not in {"/", "//"} and not route.startswith(("//", "/*"))
    }


def _discriminative_tokens(
    tokens: set[str], inventory: tuple[_InventoryTest, ...], repository: str
) -> set[str]:
    repository_tests = [test for test in inventory if test.repository == repository]
    if not repository_tests:
        return tokens
    frequency: Counter[str] = Counter()
    for test in repository_tests:
        frequency.update(tokens & _identifier_tokens(test.source))
    maximum_occurrences = max(3, (len(repository_tests) + 9) // 10)
    return {token for token in tokens if frequency[token] <= maximum_occurrences}


def _normalized_stem(path: str) -> str:
    stem = PurePosixPath(path).stem.casefold()
    stem = re.sub(r"^(test_|spec_)", "", stem)
    stem = re.sub(r"(_test|_tests|_spec|\.test|\.spec)$", "", stem)
    return stem.replace("-", "_")


def _same_area(source: str, test: str) -> bool:
    source_parts = [part.casefold() for part in PurePosixPath(source).parts[:-1]]
    test_parts = [part.casefold() for part in PurePosixPath(test).parts[:-1]]
    ignored = {"src", "test", "tests", "unit", "integration", "__tests__"}
    source_area = [part for part in source_parts if part not in ignored]
    test_area = [part for part in test_parts if part not in ignored]
    return bool(set(source_area) & set(test_area))


def _render_markdown(plan: TaskTestPlan) -> str:
    lines = [
        f"# Task Test Plan — {plan.run_id}",
        "",
        f"- Task type: {plan.intent.kind}",
        f"- Source changes: {len(plan.changes)}",
        "",
        "## Changed sources",
        "",
    ]
    if not plan.changes:
        lines.append("- No source changes detected since `loreloop begin`.")
    for change in plan.changes:
        lines.append(f"- {change.kind}: `{change.repository}:{change.path}`")
    for tier, title in (
        ("must", "Must run"),
        ("recommended", "Recommended"),
        ("missing", "Coverage gaps"),
    ):
        lines.extend(["", f"## {title}", ""])
        items = [item for item in plan.selections if item.tier == tier]
        if not items:
            lines.append("- None")
        for item in items:
            mark = " " if tier == "missing" else "x"
            location = f"{item.repository}:{item.path}" if item.path else item.repository
            lines.append(f"- [{mark}] {item.name} (`{location}`) — {item.reason}")
    lines.extend(["", "## Suggested commands", ""])
    if not plan.commands:
        lines.append("- No deterministic command could be derived.")
    for command in plan.commands:
        lines.append(f"- `{shlex.join(command.argv)}`")
    return "\n".join(lines) + "\n"


def _parse_plan(raw: dict[str, Any]) -> TaskTestPlan:
    intent_raw = raw.get("intent") if isinstance(raw, dict) else None
    if not isinstance(intent_raw, dict):
        raise ValueError("task test plan has no intent")
    intent = TaskIntent(str(intent_raw.get("text", "")), str(intent_raw.get("kind", "task")))  # type: ignore[arg-type]
    changes = tuple(
        SourceChange(item["repository"], item["path"], item["kind"])
        for item in raw.get("changes", [])
        if isinstance(item, dict)
    )
    selections = tuple(
        TestSelection(
            item["tier"],
            item["repository"],
            item.get("path"),
            item["name"],
            item.get("framework"),
            item["reason"],
        )
        for item in raw.get("selections", [])
        if isinstance(item, dict)
    )
    commands = tuple(
        TestCommand(item["repository"], tuple(item["argv"]), tuple(item.get("covers", [])))
        for item in raw.get("commands", [])
        if isinstance(item, dict)
    )
    return TaskTestPlan(str(raw.get("run_id", "")), intent, changes, selections, commands)
