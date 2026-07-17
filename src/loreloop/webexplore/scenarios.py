"""Governed, replayable Web scenarios and deterministic Playwright export."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urljoin, urlsplit

from ..evidence.artifacts import ArtifactStore
from ..evidence.chain import EvidenceChain, EvidenceRecord
from ..paths import ensure_private_directory, ensure_state_root, reject_symlink, state_path
from .actions import ActionExecution, ActionScript, execute_action_script, parse_action_script
from .browser import Observation, same_origin

WEB_EXPLORATION_EVENT = "web_exploration_captured"
WEB_TEST_APPROVED_EVENT = "web_test_approved"
WEB_TEST_EXECUTED_EVENT = "web_test_executed"
WEB_TEST_TRIALED_EVENT = "web_test_trialed"
MAX_SCENARIO_BYTES = 1024 * 1024
MAX_SCENARIOS = 10_000
_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,95}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ASSERTION_KINDS = {"contains", "absent", "title-contains", "url"}


class WebScenarioError(ValueError):
    """A scenario or scenario lifecycle operation is invalid."""


@dataclass(frozen=True, slots=True)
class ScenarioAssertion:
    kind: Literal["contains", "absent", "title-contains", "url"]
    value: str

    def to_json(self) -> dict[str, str]:
        return {self.kind: self.value}


@dataclass(frozen=True, slots=True)
class WebScenario:
    scenario_id: str
    title: str
    script: ActionScript
    assertions: tuple[ScenarioAssertion, ...]
    risk: Literal["read-only", "writes"] = "read-only"
    preconditions: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    source_artifact: str | None = None
    source_snapshot: str | None = None
    version: Literal[1] = 1

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "version": self.version,
            "id": self.scenario_id,
            "title": self.title,
            "base": self.script.base,
            "risk": self.risk,
            "preconditions": list(self.preconditions),
            "tags": list(self.tags),
            "steps": [step.to_json() for step in self.script.steps],
            "assertions": [assertion.to_json() for assertion in self.assertions],
        }
        if self.source_artifact is not None or self.source_snapshot is not None:
            payload["source"] = {
                "artifact": self.source_artifact,
                "snapshot": self.source_snapshot,
            }
        return payload

    @property
    def canonical_json(self) -> str:
        return json.dumps(
            self.to_json(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.canonical_json.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ScenarioRunResult:
    scenario: WebScenario
    passed: bool
    execution: ActionExecution
    assertions: tuple[dict[str, Any], ...]
    record: EvidenceRecord


@dataclass(frozen=True, slots=True)
class ScenarioTrialResult:
    scenario: WebScenario
    passed: bool
    execution: ActionExecution
    assertions: tuple[dict[str, Any], ...]
    record: EvidenceRecord


def parse_web_scenario(raw: Any) -> WebScenario:
    if not isinstance(raw, dict):
        raise WebScenarioError("Web scenario must be an object")
    allowed = {
        "version",
        "id",
        "title",
        "base",
        "risk",
        "preconditions",
        "tags",
        "steps",
        "assertions",
        "source",
    }
    unknown = set(raw) - allowed
    if unknown:
        raise WebScenarioError(f"Web scenario has unknown fields: {sorted(unknown)}")
    if raw.get("version") != 1:
        raise WebScenarioError("Web scenario version must be 1")
    scenario_id = raw.get("id")
    if not isinstance(scenario_id, str) or _ID.fullmatch(scenario_id) is None:
        raise WebScenarioError("Web scenario id has an invalid shape")
    title = raw.get("title")
    if not isinstance(title, str) or not title.strip() or len(title) > 512:
        raise WebScenarioError("Web scenario title must be 1..512 characters")
    risk = raw.get("risk", "read-only")
    if risk not in {"read-only", "writes"}:
        raise WebScenarioError("Web scenario risk must be read-only or writes")
    preconditions = _string_tuple(raw.get("preconditions", []), "preconditions", 32)
    tags = _string_tuple(raw.get("tags", []), "tags", 32)
    try:
        script = parse_action_script(
            {"version": 1, "base": raw.get("base"), "steps": raw.get("steps")}
        )
    except ValueError as exc:
        raise WebScenarioError(str(exc)) from exc
    assertions_raw = raw.get("assertions")
    if not isinstance(assertions_raw, list) or not assertions_raw:
        raise WebScenarioError("Web scenario needs at least one assertion")
    if len(assertions_raw) > 64:
        raise WebScenarioError("Web scenario has too many assertions")
    assertions: list[ScenarioAssertion] = []
    for index, assertion in enumerate(assertions_raw):
        if not isinstance(assertion, dict) or len(assertion) != 1:
            raise WebScenarioError(f"assertion {index} must contain exactly one condition")
        kind, value = next(iter(assertion.items()))
        if kind not in _ASSERTION_KINDS:
            raise WebScenarioError(f"assertion {index} has unknown condition {kind!r}")
        if not isinstance(value, str) or not value.strip() or len(value) > 4_000:
            raise WebScenarioError(f"assertion {index} value must be 1..4000 characters")
        assertions.append(ScenarioAssertion(kind, value.strip()))
    source_artifact = None
    source_snapshot = None
    source = raw.get("source")
    if source is not None:
        if not isinstance(source, dict) or set(source) - {"artifact", "snapshot"}:
            raise WebScenarioError("Web scenario source is invalid")
        source_artifact = _optional_sha(source.get("artifact"), "source artifact")
        source_snapshot = _optional_sha(source.get("snapshot"), "source snapshot")
    return WebScenario(
        scenario_id,
        title.strip(),
        script,
        tuple(assertions),
        risk,
        preconditions,
        tags,
        source_artifact,
        source_snapshot,
    )


def load_web_scenario(path: Path) -> WebScenario:
    data = _read_regular_file(path, "Web scenario")
    try:
        raw = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WebScenarioError(f"Web scenario is not valid UTF-8 JSON: {path}") from exc
    return parse_web_scenario(raw)


def candidate_directory(workdir: Path) -> Path:
    return state_path(workdir, "web-tests", "candidates")


def approved_directory(workdir: Path) -> Path:
    return workdir / "tests" / "loreloop" / "web"


def list_candidate_scenarios(workdir: Path) -> tuple[tuple[Path, WebScenario], ...]:
    root = candidate_directory(workdir)
    if not root.exists():
        return ()
    _candidate_directory(workdir, create=False)
    paths = tuple(sorted(root.glob("*.json")))
    if len(paths) > MAX_SCENARIOS:
        raise WebScenarioError("Web scenario candidate count exceeds the supported limit")
    return tuple((path, load_web_scenario(path)) for path in paths)


def list_approved_scenarios(workdir: Path) -> tuple[tuple[Path, WebScenario], ...]:
    paths: list[Path] = []
    for _, repository in _repository_targets(workdir).items():
        root = approved_directory(repository)
        if not root.exists():
            continue
        _approved_directory(repository, create=False)
        paths.extend(root.glob("*.json"))
    if len(paths) > MAX_SCENARIOS:
        raise WebScenarioError("approved Web scenario count exceeds the supported limit")
    return tuple((path, load_web_scenario(path)) for path in sorted(paths))


def write_candidate(workdir: Path, scenario: WebScenario) -> Path:
    root = _candidate_directory(workdir, create=True)
    path = root / f"{scenario.scenario_id}.json"
    content = _pretty(scenario)
    reject_symlink(path, label="Web scenario candidate")
    if path.exists():
        data = _read_regular_file(path, "Web scenario candidate")
        try:
            existing = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise WebScenarioError(f"Web scenario candidate is not UTF-8: {path}") from exc
        if existing == content:
            return path
        raise WebScenarioError(f"Web scenario candidate already exists: {path}")
    _write_private(path, content)
    return path


def generate_latest_candidates(
    workdir: Path, chain: EvidenceChain, artifacts: ArtifactStore
) -> tuple[Path, ...]:
    record = next(
        (item for item in reversed(chain.verify()) if item.event == WEB_EXPLORATION_EVENT),
        None,
    )
    if record is None:
        raise WebScenarioError("no captured Web exploration is available")
    pages = record.payload.get("pages")
    if not isinstance(pages, list) or not pages:
        raise WebScenarioError("latest Web exploration contains no captured pages")
    written: list[Path] = []
    for page in pages:
        if not isinstance(page, dict):
            raise WebScenarioError("latest Web exploration page record is invalid")
        artifact = page.get("artifact")
        snapshot = page.get("snapshot")
        if not isinstance(artifact, str) or _SHA256.fullmatch(artifact) is None:
            raise WebScenarioError("latest Web exploration lacks a valid page artifact")
        if not isinstance(snapshot, str) or _SHA256.fullmatch(snapshot) is None:
            raise WebScenarioError("latest Web exploration lacks a valid page snapshot")
        observation = artifacts.load(artifact)
        if observation.get("type") != "page_observation":
            raise WebScenarioError("captured artifact is not a page observation")
        if observation.get("snapshot_hash") != snapshot:
            raise WebScenarioError("captured page snapshot does not match its artifact")
        scenario = _scenario_from_observation(observation, artifact, snapshot)
        written.append(write_candidate(workdir, scenario))
    return tuple(written)


def approve_candidate(
    workdir: Path,
    prefix: str,
    chain: EvidenceChain,
    *,
    repository_alias: str | None = None,
) -> tuple[Path, WebScenario, EvidenceRecord]:
    path, scenario = _resolve(prefix, list_candidate_scenarios(workdir), "candidate")
    alias, repository = _select_repository(workdir, repository_alias)
    destination = _approved_directory(repository, create=True) / f"{scenario.scenario_id}.json"
    _publish_approved(destination, _pretty(scenario))
    locator = _locator(alias, scenario.scenario_id)
    record = chain.append(
        WEB_TEST_APPROVED_EVENT,
        {
            "scenario_id": scenario.scenario_id,
            "scenario_digest": scenario.digest,
            "path": locator,
            "scenario": scenario.to_json(),
        },
    )
    path.unlink(missing_ok=True)
    return destination, scenario, record


def scenario_locator(workdir: Path, path: Path) -> str:
    """Return a stable root/peer locator for an approved scenario path."""
    resolved = path.resolve()
    for alias, repository in _repository_targets(workdir).items():
        try:
            relative = resolved.relative_to(repository.resolve())
        except ValueError:
            continue
        if relative.parts[:3] == ("tests", "loreloop", "web") and len(relative.parts) == 4:
            locator = relative.as_posix()
            return locator if alias == "." else f"repo:{alias}/{locator}"
    raise WebScenarioError(f"approved Web scenario is outside declared repositories: {path}")


def approved_scenario(
    workdir: Path, prefix: str, records: list[EvidenceRecord]
) -> tuple[Path, WebScenario]:
    path, scenario = _resolve(prefix, list_approved_scenarios(workdir), "approved")
    approved: dict[str, str] = {}
    for record in records:
        if record.event == WEB_TEST_APPROVED_EVENT:
            scenario_id, digest = _approval_identity(record, workdir)
            approved[scenario_id] = digest
    if approved.get(scenario.scenario_id) != scenario.digest:
        raise WebScenarioError(
            f"approved Web scenario {scenario.scenario_id} does not match chain authority"
        )
    return path, scenario


def run_scenario(
    scenario: WebScenario,
    browser,
    chain: EvidenceChain,
    artifacts: ArtifactStore,
    *,
    allow_writes: bool = False,
) -> ScenarioRunResult:
    if scenario.risk == "writes" and not allow_writes:
        raise WebScenarioError("write-risk scenario requires --allow-writes")
    execution = execute_action_script(
        browser,
        scenario.script,
        allow_writes=allow_writes,
    )
    script_artifact = artifacts.save_json(
        {
            "type": "interaction_script",
            "script_digest": scenario.script.digest,
            "script": scenario.script.to_json(),
        }
    )[0]
    state_artifacts = tuple(
        artifacts.save_observation(state.observation)[0] for state in execution.states
    )
    trace_artifact = artifacts.save_json(
        execution.trace_artifact_payload(state_artifacts)
    )[0]
    results: list[dict[str, Any]] = []
    observation_artifact = None
    if execution.succeeded and execution.final_observation is not None:
        observation = execution.final_observation
        observation_artifact = (
            state_artifacts[-1]
            if execution.states
            and execution.states[-1].observation.snapshot_hash == observation.snapshot_hash
            else artifacts.save_observation(observation)[0]
        )
        results = [
            _evaluate_assertion(assertion, observation, scenario.script.base)
            for assertion in scenario.assertions
        ]
    passed = execution.succeeded and bool(results) and all(item["passed"] for item in results)
    record = chain.append(
        WEB_TEST_EXECUTED_EVENT,
        {
            "scenario_id": scenario.scenario_id,
            "scenario_digest": scenario.digest,
            "script_digest": scenario.script.digest,
            "script_artifact": script_artifact,
            "trace_artifact": trace_artifact,
            "observation_artifact": observation_artifact,
            "status": "passed" if passed else execution.status if not execution.succeeded else "failed",
            "assertions": results,
            "allow_writes": allow_writes,
            "states": [
                state.to_json(state_artifacts[index])
                for index, state in enumerate(execution.states)
            ],
        },
    )
    return ScenarioRunResult(scenario, passed, execution, tuple(results), record)


def trial_candidate(
    workdir: Path,
    prefix: str,
    browser,
    chain: EvidenceChain,
    artifacts: ArtifactStore,
    *,
    run_id: str,
) -> ScenarioTrialResult:
    """Strictly read-only candidate replay that never grants test authority."""
    _, scenario = _resolve(prefix, list_candidate_scenarios(workdir), "candidate")
    if scenario.risk != "read-only":
        raise WebScenarioError("write-risk candidate cannot run in trial mode")
    execution = execute_action_script(browser, scenario.script, allow_writes=False)
    state_artifacts = tuple(
        artifacts.save_observation(state.observation)[0] for state in execution.states
    )
    trace_artifact = artifacts.save_json(
        execution.trace_artifact_payload(state_artifacts)
    )[0]
    results: list[dict[str, Any]] = []
    observation_artifact = None
    if execution.succeeded and execution.final_observation is not None:
        observation = execution.final_observation
        observation_artifact = (
            state_artifacts[-1]
            if execution.states
            and execution.states[-1].observation.snapshot_hash == observation.snapshot_hash
            else artifacts.save_observation(observation)[0]
        )
        results = [
            _evaluate_assertion(assertion, observation, scenario.script.base)
            for assertion in scenario.assertions
        ]
    passed = execution.succeeded and bool(results) and all(item["passed"] for item in results)
    record = chain.append(
        WEB_TEST_TRIALED_EVENT,
        {
            "run_id": run_id,
            "scenario_id": scenario.scenario_id,
            "scenario_digest": scenario.digest,
            "status": "passed"
            if passed
            else execution.status
            if not execution.succeeded
            else "failed",
            "assertions": results,
            "trace_artifact": trace_artifact,
            "observation_artifact": observation_artifact,
            "states": [
                state.to_json(state_artifacts[index])
                for index, state in enumerate(execution.states)
            ],
            "authoritative": False,
            "risk": scenario.risk,
        },
    )
    return ScenarioTrialResult(scenario, passed, execution, tuple(results), record)


def render_playwright(scenario: WebScenario) -> str:
    lines = [
        'import { test, expect, type Locator } from "@playwright/test";',
        "",
        f"const defaultBase = {json.dumps(scenario.script.base, ensure_ascii=False)};",
        "const base = process.env.LORELOOP_BASE_URL ?? defaultBase;",
        'const allowWrites = process.env.LORELOOP_ALLOW_WRITES === "1";',
        "const allowedOrigin = new URL(base).origin;",
        "const normalizeUrl = (value: string) => {",
        "  const url = new URL(value, base);",
        '  url.hash = "";',
        "  return url.href.replace(/\\/$/, '');",
        "};",
        "",
        "async function guardControl(locator: Locator, action: string) {",
        "  const meta = await locator.evaluate((el: Element) => {",
        "    const node = el as HTMLInputElement;",
        "    const form = node.closest('form');",
        "    return {",
        "      tag: node.tagName.toLowerCase(),",
        "      type: (node.getAttribute('type') ?? '').toLowerCase(),",
        "      method: (form?.getAttribute('method') ?? '').toLowerCase(),",
        "      label: (node.textContent ?? node.getAttribute('aria-label') ?? '').trim(),",
        "    };",
        "  });",
        '  if (meta.type === "password") throw new Error("refused password control");',
        '  if (action === "click" && /delete|remove|pay|unsubscribe|transfer|删除|移除|支付|付款|转账|退订/i.test(meta.label)) {',
        '    throw new Error("refused dangerous control");',
        "  }",
        "  const postWrite = meta.method === 'post' && (",
        "    action !== 'click' || meta.tag === 'button' ||",
        "    (meta.tag === 'input' && ['submit', 'button'].includes(meta.type))",
        "  );",
        "  if (!allowWrites && postWrite) {",
        '    throw new Error(`refused ${action} on POST form without LORELOOP_ALLOW_WRITES=1`);',
        "  }",
        "}",
        "",
        f"test({json.dumps(scenario.title, ensure_ascii=False)}, async ({{ page }}) => {{",
    ]
    if scenario.risk == "writes":
        lines.append(
            '  test.skip(process.env.LORELOOP_ALLOW_WRITES !== "1", "write-risk scenario");'
        )
    lines.extend(
        (
            '  await page.context().route("**/*", async route => {',
            "    const request = route.request();",
            "    const target = new URL(request.url());",
            '    if (!["http:", "https:"].includes(target.protocol)) return route.continue();',
            "    if (target.origin !== allowedOrigin) return route.abort();",
            '    if (!allowWrites && !["GET", "HEAD", "OPTIONS"].includes(request.method())) {',
            "      return route.abort();",
            "    }",
            "    return route.continue();",
            "  });",
        )
    )
    for index, step in enumerate(scenario.script.steps, 1):
        lines.extend(_playwright_step(index, step.op, step.arg))
    for assertion in scenario.assertions:
        value = json.dumps(assertion.value, ensure_ascii=False)
        if assertion.kind == "contains":
            lines.append(
                "  expect((await page.locator('body').innerText()).toLocaleLowerCase())"
                f".toContain({value}.toLocaleLowerCase());"
            )
        elif assertion.kind == "absent":
            lines.append(
                "  expect((await page.locator('body').innerText()).toLocaleLowerCase())"
                f".not.toContain({value}.toLocaleLowerCase());"
            )
        elif assertion.kind == "title-contains":
            lines.append(
                "  expect((await page.title()).toLocaleLowerCase())"
                f".toContain({value}.toLocaleLowerCase());"
            )
        else:
            lines.append("  expect(normalizeUrl(page.url())).toBe(")
            lines.append(f"    normalizeUrl({value}),")
            lines.append("  );")
    lines.extend(("});", ""))
    return "\n".join(lines)


def export_playwright(
    scenarios: tuple[tuple[Path, WebScenario], ...], output: Path, *, force: bool
) -> tuple[Path, ...]:
    reject_symlink(output, label="Playwright export directory")
    try:
        output.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise WebScenarioError(f"cannot create Playwright export directory: {exc}") from exc
    if not output.is_dir():
        raise WebScenarioError("Playwright export path is not a directory")
    written: list[Path] = []
    for _, scenario in scenarios:
        path = output / f"{scenario.scenario_id}.spec.ts"
        reject_symlink(path, label="Playwright test")
        if path.exists() and not force:
            raise WebScenarioError(f"Playwright test already exists: {path}")
        _write_public(path, render_playwright(scenario), replace=force)
        written.append(path)
    return tuple(written)


def _scenario_from_observation(
    observation: dict[str, Any], artifact: str, snapshot: str
) -> WebScenario:
    url = observation.get("url")
    if not isinstance(url, str):
        raise WebScenarioError("page observation lacks a URL")
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise WebScenarioError("page observation URL is invalid")
    base = f"{parsed.scheme}://{parsed.netloc}"
    target = parsed.path or "/"
    if parsed.query:
        target += f"?{parsed.query}"
    if parsed.fragment:
        target += f"#{parsed.fragment}"
    title = observation.get("title") if isinstance(observation.get("title"), str) else ""
    headings = observation.get("headings")
    assertions: list[ScenarioAssertion] = []
    if title.strip():
        assertions.append(ScenarioAssertion("title-contains", title.strip()[:512]))
    if isinstance(headings, list):
        heading = next(
            (item.strip() for item in headings if isinstance(item, str) and item.strip()), None
        )
        if heading:
            assertions.append(ScenarioAssertion("contains", heading[:512]))
    if not assertions:
        text = observation.get("text")
        if not isinstance(text, str) or not text.strip():
            raise WebScenarioError("page observation has no stable assertion candidate")
        assertions.append(ScenarioAssertion("contains", text.strip().splitlines()[0][:512]))
    scenario_id = f"web-{hashlib.sha256(url.encode()).hexdigest()[:10]}-{snapshot[:10]}"
    return WebScenario(
        scenario_id,
        f"Page remains available: {title.strip() or parsed.path or '/'}",
        parse_action_script({"version": 1, "base": base, "steps": [{"goto": target}]}),
        tuple(assertions),
        tags=("generated", "web"),
        source_artifact=artifact,
        source_snapshot=snapshot,
    )


def _evaluate_assertion(
    assertion: ScenarioAssertion, observation: Observation, base: str
) -> dict[str, Any]:
    if assertion.kind == "contains":
        passed = assertion.value.casefold() in observation.text.casefold()
    elif assertion.kind == "absent":
        passed = assertion.value.casefold() not in observation.text.casefold()
    elif assertion.kind == "title-contains":
        passed = assertion.value.casefold() in observation.title.casefold()
    else:
        expected = urljoin(base, assertion.value).split("#", 1)[0].rstrip("/")
        actual = observation.url.split("#", 1)[0].rstrip("/")
        passed = same_origin(expected, actual) and expected == actual
    return {"kind": assertion.kind, "value": assertion.value, "passed": passed}


def _resolve(
    prefix: str,
    items: tuple[tuple[Path, WebScenario], ...],
    label: str,
) -> tuple[Path, WebScenario]:
    matches = [item for item in items if item[1].scenario_id.startswith(prefix)]
    if len(matches) != 1:
        reason = "no scenario matches" if not matches else f"{len(matches)} scenarios match"
        raise WebScenarioError(f"{reason} {label} id prefix {prefix!r}")
    return matches[0]


def _string_tuple(value: Any, label: str, maximum: int) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > maximum:
        raise WebScenarioError(f"Web scenario {label} must be a list of at most {maximum}")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip() or len(item) > 512:
            raise WebScenarioError(f"Web scenario {label} contains an invalid value")
        result.append(item.strip())
    return tuple(result)


def _optional_sha(value: Any, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise WebScenarioError(f"Web scenario {label} must be a SHA-256 digest")
    return value


def _pretty(scenario: WebScenario) -> str:
    return json.dumps(scenario.to_json(), ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def _write_private(path: Path, content: str) -> None:
    root = ensure_private_directory(path.parent)
    reject_symlink(path, label="Web scenario candidate")
    fd, raw = tempfile.mkstemp(prefix=f".{path.name}.", dir=root)
    temporary = Path(raw)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            fd = -1
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if fd >= 0:
            os.close(fd)
        temporary.unlink(missing_ok=True)


def _publish_approved(path: Path, content: str) -> None:
    reject_symlink(path, label="approved Web scenario")
    if path.exists():
        data = _read_regular_file(path, "approved Web scenario")
        try:
            existing = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise WebScenarioError(f"approved Web scenario is not UTF-8: {path}") from exc
        if existing == content:
            return
        raise WebScenarioError(f"approved Web scenario already exists with other content: {path}")
    _write_public(path, content, replace=False)


def _write_public(path: Path, content: str, *, replace: bool) -> None:
    fd, raw = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(raw)
    try:
        os.fchmod(fd, 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            fd = -1
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if replace:
            os.replace(temporary, path)
        else:
            try:
                os.link(temporary, path, follow_symlinks=False)
            except FileExistsError as exc:
                raise WebScenarioError(f"output appeared while publishing: {path}") from exc
            temporary.unlink()
    finally:
        if fd >= 0:
            os.close(fd)
        temporary.unlink(missing_ok=True)


def _candidate_directory(workdir: Path, *, create: bool) -> Path:
    state = ensure_state_root(workdir) if create else state_path(workdir)
    if not state.is_dir():
        raise WebScenarioError(f"LoreLoop state directory is unavailable: {state}")
    web_tests = state / "web-tests"
    candidates = web_tests / "candidates"
    if create:
        ensure_private_directory(web_tests)
        return ensure_private_directory(candidates)
    for path in (web_tests, candidates):
        reject_symlink(path, label="Web scenario candidate directory")
        if not path.is_dir():
            raise WebScenarioError(f"Web scenario candidate directory is invalid: {path}")
    return candidates


def _approved_directory(workdir: Path, *, create: bool) -> Path:
    current = workdir
    for part in ("tests", "loreloop", "web"):
        current = current / part
        reject_symlink(current, label="approved Web scenario directory")
        if create:
            try:
                current.mkdir(exist_ok=True)
            except OSError as exc:
                raise WebScenarioError(
                    f"cannot create approved Web scenario directory {current}: {exc}"
                ) from exc
        if not current.is_dir():
            raise WebScenarioError(f"approved Web scenario directory is invalid: {current}")
    return current


def _read_regular_file(path: Path, label: str) -> bytes:
    reject_symlink(path, label=label)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise WebScenarioError(f"cannot read {label} {path}: {exc}") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise WebScenarioError(f"{label} is not a regular file: {path}")
        if info.st_size > MAX_SCENARIO_BYTES:
            raise WebScenarioError(f"{label} exceeds {MAX_SCENARIO_BYTES} bytes")
        with os.fdopen(fd, "rb") as stream:
            fd = -1
            data = stream.read(MAX_SCENARIO_BYTES + 1)
    finally:
        if fd >= 0:
            os.close(fd)
    if len(data) > MAX_SCENARIO_BYTES:
        raise WebScenarioError(f"{label} exceeds {MAX_SCENARIO_BYTES} bytes")
    return data


def _approval_identity(record: EvidenceRecord, workdir: Path) -> tuple[str, str]:
    raw = record.payload.get("scenario")
    scenario_id = record.payload.get("scenario_id")
    digest = record.payload.get("scenario_digest")
    path = record.payload.get("path")
    try:
        approved = parse_web_scenario(raw)
    except WebScenarioError as exc:
        raise WebScenarioError(f"chain contains an invalid Web-test approval: {exc}") from exc
    valid_paths = {
        _locator(alias, approved.scenario_id) for alias in _repository_targets(workdir)
    }
    if (
        scenario_id != approved.scenario_id
        or digest != approved.digest
        or path not in valid_paths
    ):
        raise WebScenarioError("chain contains a mismatched Web-test approval")
    return approved.scenario_id, approved.digest


def _repository_targets(workdir: Path) -> dict[str, Path]:
    from ..knowledge.repos import load_repos

    targets: dict[str, Path] = {}
    if (workdir / ".git").exists():
        targets["."] = workdir
    targets.update(load_repos(workdir))
    return targets or {".": workdir}


def _select_repository(workdir: Path, alias: str | None) -> tuple[str, Path]:
    targets = _repository_targets(workdir)
    if alias is None:
        if "." in targets:
            return ".", targets["."]
        if len(targets) == 1:
            return next(iter(targets.items()))
        raise WebScenarioError(
            "multiple project repositories are available; choose one with --repo"
        )
    try:
        return alias, targets[alias]
    except KeyError as exc:
        raise WebScenarioError(f"unknown project repository for Web scenario: {alias}") from exc


def _locator(alias: str, scenario_id: str) -> str:
    path = f"tests/loreloop/web/{scenario_id}.json"
    return path if alias == "." else f"repo:{alias}/{path}"


def _playwright_locator(argument: dict[str, Any]) -> str:
    if "label" in argument:
        locator = (
            f"page.getByLabel({json.dumps(argument['label'], ensure_ascii=False)}, "
            "{ exact: true })"
        )
    elif "role" in argument:
        options = (
            f", {{ name: {json.dumps(argument['text'], ensure_ascii=False)}, exact: true }}"
            if "text" in argument
            else ""
        )
        locator = f"page.getByRole({json.dumps(argument['role'])}{options})"
    else:
        locator = f"page.getByText({json.dumps(argument['text'], ensure_ascii=False)}, {{ exact: true }})"
    if "nth" in argument:
        locator += f".nth({argument['nth']})"
    return locator


def _playwright_step(index: int, operation: str, argument: Any) -> list[str]:
    if operation == "goto":
        return [f"  await page.goto(new URL({json.dumps(argument)}, base).toString());"]
    if operation == "wait":
        if "text" in argument:
            return [
                f"  await expect(page.getByText(new RegExp({json.dumps(argument['text'])}))).toBeVisible();"
            ]
        if "url" in argument:
            return [
                "  await page.waitForURL(url => normalizeUrl(url.toString()).startsWith("
                f"normalizeUrl({json.dumps(argument['url'])})));"
            ]
        return [f"  await page.waitForTimeout({argument['ms']});"]
    value_key = "value" if operation == "fill" else "option" if operation == "select" else None
    locator_args = {key: value for key, value in argument.items() if key != value_key}
    locator = _playwright_locator(locator_args)
    variable = f"control{index}"
    if operation == "click":
        return [
            f"  const {variable} = {locator};",
            f'  await guardControl({variable}, "click");',
            f"  await {variable}.click();",
        ]
    if operation == "fill":
        return [
            f"  const {variable} = {locator};",
            f'  await guardControl({variable}, "fill");',
            f"  await {variable}.fill({json.dumps(argument['value'], ensure_ascii=False)});",
        ]
    return [
        f"  const {variable} = {locator};",
        f'  await guardControl({variable}, "select");',
        f"  await {variable}.selectOption({{ label: {json.dumps(argument['option'], ensure_ascii=False)} }});",
    ]
