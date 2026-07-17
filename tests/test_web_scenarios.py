from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from loreloop.evidence.artifacts import ArtifactStore
from loreloop.evidence.chain import EvidenceChain
from loreloop.knowledge.authoritative_detector_tests import (
    detect_test_source,
    is_supported_test_evidence_path,
    is_web_scenario_path,
)
from loreloop.knowledge.authoritative_records import DetectionError
from loreloop.knowledge.repos import save_repos
from loreloop.knowledge.authoritative_web_test_input import build_governed_web_test_results
from loreloop.webexplore.actions import ActionExecution, PageState, StepTrace, parse_action_script
from loreloop.webexplore.browser import Observation
from loreloop.webexplore.recorder import _event_step
from loreloop.webexplore.scenarios import (
    WEB_EXPLORATION_EVENT,
    ScenarioAssertion,
    WebScenario,
    WebScenarioError,
    approve_candidate,
    approved_scenario,
    export_playwright,
    generate_latest_candidates,
    load_web_scenario,
    list_approved_scenarios,
    parse_web_scenario,
    render_playwright,
    run_scenario,
    scenario_locator,
    trial_candidate,
    write_candidate,
)


def _scenario(*, risk: str = "read-only") -> WebScenario:
    return WebScenario(
        "upload-filter",
        "Filter uploaded files",
        parse_action_script(
            {
                "version": 1,
                "base": "https://example.test",
                "steps": [
                    {"goto": "/uploads"},
                    {"click": {"role": "button", "text": "Filter"}},
                ],
            }
        ),
        (
            ScenarioAssertion("title-contains", "Uploads"),
            ScenarioAssertion("contains", "1 item"),
        ),
        risk,  # type: ignore[arg-type]
        tags=("web",),
    )


def test_scenario_schema_is_strict_and_digest_is_canonical() -> None:
    scenario = _scenario()
    reordered = json.loads(scenario.canonical_json)
    reordered = {key: reordered[key] for key in reversed(tuple(reordered))}

    assert parse_web_scenario(reordered).digest == scenario.digest
    reordered["unexpected"] = True
    with pytest.raises(WebScenarioError, match="unknown fields"):
        parse_web_scenario(reordered)


def test_latest_exploration_generates_private_candidate(tmp_path: Path) -> None:
    chain = EvidenceChain.for_workdir(tmp_path)
    artifacts = ArtifactStore.for_workdir(tmp_path)
    observation = Observation(
        "https://example.test/uploads",
        "Uploads",
        "Uploads\n1 item",
        headings=["Uploads"],
    )
    artifact = artifacts.save_observation(observation)[0]
    chain.append(
        WEB_EXPLORATION_EVENT,
        {
            "start_url": observation.url,
            "trace": "web/traces/explore.json",
            "pages": [
                {
                    "url": observation.url,
                    "title": observation.title,
                    "snapshot": observation.snapshot_hash,
                    "artifact": artifact,
                }
            ],
        },
    )

    paths = generate_latest_candidates(tmp_path, chain, artifacts)

    assert len(paths) == 1
    assert paths[0].stat().st_mode & 0o777 == 0o600
    generated = load_web_scenario(paths[0])
    assert generated.script.steps[0].to_json() == {"goto": "/uploads"}
    assert generated.source_artifact == artifact
    assert generated.source_snapshot == observation.snapshot_hash


def test_generated_candidate_preserves_spa_hash_route(tmp_path: Path) -> None:
    chain = EvidenceChain.for_workdir(tmp_path)
    artifacts = ArtifactStore.for_workdir(tmp_path)
    observation = Observation(
        "https://example.test/ui/#/manage/hpf-ratio-config",
        "Ratio config",
        "Ratio config\nTotal 27 rows",
    )
    artifact = artifacts.save_observation(observation)[0]
    chain.append(
        WEB_EXPLORATION_EVENT,
        {
            "start_url": observation.url,
            "trace": "web/traces/explore.json",
            "pages": [
                {
                    "url": observation.url,
                    "title": observation.title,
                    "snapshot": observation.snapshot_hash,
                    "artifact": artifact,
                }
            ],
        },
    )

    generated = load_web_scenario(generate_latest_candidates(tmp_path, chain, artifacts)[0])

    assert generated.script.steps[0].to_json() == {
        "goto": "/ui/#/manage/hpf-ratio-config"
    }


def test_candidate_approval_binds_exact_committed_file_to_chain(tmp_path: Path) -> None:
    chain = EvidenceChain.for_workdir(tmp_path)
    candidate = write_candidate(tmp_path, _scenario())

    path, scenario, record = approve_candidate(tmp_path, "upload", chain)

    assert not candidate.exists()
    assert path == tmp_path / "tests/loreloop/web/upload-filter.json"
    assert record.payload["scenario_digest"] == scenario.digest
    assert approved_scenario(tmp_path, "upload", chain.verify()) == (path, scenario)

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["title"] = "tampered"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(WebScenarioError, match="does not match chain authority"):
        approved_scenario(tmp_path, "upload", chain.verify())


def test_approval_rejects_intermediate_symlink(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "tests").symlink_to(outside, target_is_directory=True)
    chain = EvidenceChain.for_workdir(tmp_path)
    write_candidate(tmp_path, _scenario())

    with pytest.raises(Exception, match="symlinked approved Web scenario directory"):
        approve_candidate(tmp_path, "upload", chain)
    assert tuple(outside.rglob("*.json")) == ()


def test_aggregate_project_requires_and_records_repository_owner(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    frontend = tmp_path / "frontend"
    backend = tmp_path / "backend"
    for repository in (frontend, backend):
        repository.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    save_repos(workspace, {"frontend": frontend, "backend": backend})
    chain = EvidenceChain.for_workdir(workspace)
    write_candidate(workspace, _scenario())

    with pytest.raises(WebScenarioError, match="choose one with --repo"):
        approve_candidate(workspace, "upload", chain)

    path, scenario, record = approve_candidate(
        workspace,
        "upload",
        chain,
        repository_alias="frontend",
    )
    assert path == frontend / "tests/loreloop/web/upload-filter.json"
    assert record.payload["path"] == "repo:frontend/tests/loreloop/web/upload-filter.json"
    assert scenario_locator(workspace, path) == record.payload["path"]
    assert approved_scenario(workspace, scenario.scenario_id, chain.verify()) == (
        path,
        scenario,
    )


def test_unapproved_committed_file_is_not_runnable(tmp_path: Path) -> None:
    path = tmp_path / "tests/loreloop/web/upload-filter.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(_scenario().to_json()), encoding="utf-8")
    chain = EvidenceChain.for_workdir(tmp_path)

    with pytest.raises(WebScenarioError, match="does not match chain authority"):
        approved_scenario(tmp_path, "upload", chain.verify())


def test_run_records_assertions_and_requires_write_authorization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observation = Observation(
        "https://example.test/uploads",
        "Uploads - LoreLoop",
        "Filtered results: 1 item",
    )
    execution = ActionExecution(
        _scenario().script.digest,
        "completed",
        [StepTrace(0, {"goto": "/uploads"}, "completed", "ok", 1, observation.url)],
        final_observation=observation,
    )
    monkeypatch.setattr(
        "loreloop.webexplore.scenarios.execute_action_script",
        lambda *args, **kwargs: execution,
    )
    chain = EvidenceChain.for_workdir(tmp_path)
    artifacts = ArtifactStore.for_workdir(tmp_path)

    result = run_scenario(_scenario(), object(), chain, artifacts)

    assert result.passed
    assert [item["passed"] for item in result.assertions] == [True, True]
    assert result.record.event == "web_test_executed"
    assert result.record.payload["status"] == "passed"
    assert artifacts.load(result.record.payload["trace_artifact"])["status"] == "completed"

    with pytest.raises(WebScenarioError, match="requires --allow-writes"):
        run_scenario(_scenario(risk="writes"), object(), chain, artifacts)


def test_candidate_trial_is_read_only_non_authoritative_and_keeps_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = write_candidate(tmp_path, _scenario())
    observation = Observation(
        "https://example.test/uploads",
        "Uploads - LoreLoop",
        "Filtered results: 1 item",
    )
    execution = ActionExecution(
        _scenario().script.digest,
        "completed",
        [StepTrace(0, {"goto": "/uploads"}, "completed", "ok", 1, observation.url)],
        final_observation=observation,
        states=(PageState(0, observation),),
    )
    monkeypatch.setattr(
        "loreloop.webexplore.scenarios.execute_action_script",
        lambda *args, **kwargs: execution,
    )
    chain = EvidenceChain.for_workdir(tmp_path)
    artifacts = ArtifactStore.for_workdir(tmp_path)

    result = trial_candidate(
        tmp_path,
        "upload",
        object(),
        chain,
        artifacts,
        run_id="run-1",
    )

    assert result.passed
    assert candidate.exists()
    assert result.record.event == "web_test_trialed"
    assert result.record.payload["authoritative"] is False
    assert result.record.payload["states"][0]["artifact"]
    assert list_approved_scenarios(tmp_path) == ()

    writes_dir = tmp_path / "writes"
    writes_dir.mkdir()
    write_candidate(writes_dir, _scenario(risk="writes"))
    with pytest.raises(WebScenarioError, match="write-risk candidate"):
        trial_candidate(
            writes_dir,
            "upload",
            object(),
            EvidenceChain.for_workdir(writes_dir),
            ArtifactStore.for_workdir(writes_dir),
            run_id="run-1",
        )


def test_latest_chain_verified_execution_becomes_acceptance_fact(tmp_path: Path) -> None:
    scenario = _scenario()
    chain = EvidenceChain.for_workdir(tmp_path)
    chain.append(
        "web_test_approved",
        {
            "scenario_id": scenario.scenario_id,
            "scenario_digest": scenario.digest,
            "path": "tests/loreloop/web/upload-filter.json",
            "scenario": scenario.to_json(),
        },
    )
    chain.append(
        "web_test_executed",
        {
            "scenario_id": scenario.scenario_id,
            "scenario_digest": scenario.digest,
            "status": "passed",
            "assertions": [
                {"kind": "contains", "value": "1 item", "passed": True}
            ],
            "trace_artifact": "1" * 64,
            "observation_artifact": "2" * 64,
        },
    )

    report, blobs = build_governed_web_test_results(chain.verify())

    assert len(blobs) == 1
    assert report.web_knowledge[0].kind == "acceptance"
    assert "执行结果：通过" in report.web_knowledge[0].statement
    assert report.web_knowledge[0].snapshot_ref == chain.verify()[-1].chain_hash


def test_playwright_export_is_deterministic_and_does_not_overwrite(tmp_path: Path) -> None:
    scenario = _scenario()
    first = render_playwright(scenario)
    assert first == render_playwright(scenario)
    assert "LORELOOP_BASE_URL" in first
    assert "LORELOOP_ALLOW_WRITES" in first
    assert 'page.context().route("**/*"' in first
    assert "refused password control" in first
    assert "getByRole" in first

    output = tmp_path / "playwright"
    paths = export_playwright(((Path("ignored"), scenario),), output, force=False)
    assert paths[0].read_text(encoding="utf-8") == first
    paths[0].write_text("operator content\n", encoding="utf-8")
    with pytest.raises(WebScenarioError, match="already exists"):
        export_playwright(((Path("ignored"), scenario),), output, force=False)
    assert paths[0].read_text(encoding="utf-8") == "operator content\n"


def test_candidate_does_not_overwrite_existing_operator_content(tmp_path: Path) -> None:
    path = write_candidate(tmp_path, _scenario())
    path.write_text("{}\n", encoding="utf-8")

    with pytest.raises(WebScenarioError, match="already exists"):
        write_candidate(tmp_path, _scenario())
    assert path.read_text(encoding="utf-8") == "{}\n"


def test_scenario_loader_rejects_symlink_fifo_and_oversize(tmp_path: Path) -> None:
    regular = tmp_path / "scenario.json"
    regular.write_text(json.dumps(_scenario().to_json()), encoding="utf-8")
    symlink = tmp_path / "link.json"
    symlink.symlink_to(regular)
    with pytest.raises(Exception, match="symlinked"):
        load_web_scenario(symlink)

    fifo = tmp_path / "fifo.json"
    os.mkfifo(fifo)
    with pytest.raises(WebScenarioError, match="not a regular file"):
        load_web_scenario(fifo)

    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b" " * (1024 * 1024 + 1))
    with pytest.raises(WebScenarioError, match="exceeds"):
        load_web_scenario(oversized)


def test_recorder_event_conversion_is_bounded_to_action_dsl() -> None:
    assert _event_step({"op": "click", "locator": {"text": "Filter"}}) == {
        "click": {"text": "Filter"}
    }
    assert _event_step({"op": "fill", "locator": {"label": "Query"}, "value": "one"}) == {
        "fill": {"label": "Query", "value": "one"}
    }
    assert _event_step({"op": "script", "value": "alert(1)"}) is None
    assert (
        _event_step(
            {"op": "fill", "locator": {"label": "API token"}, "value": "secret"}
        )
        is None
    )
    assert (
        _event_step(
            {"op": "fill", "locator": {"label": "Query"}, "value": "x" * 513}
        )
        is None
    )


def test_only_strict_web_scenario_namespace_is_detected_as_json_test() -> None:
    source = json.dumps(_scenario().to_json())
    assert is_web_scenario_path("tests/loreloop/web/upload-filter.json")
    assert is_supported_test_evidence_path("tests/loreloop/web/upload-filter.json")
    assert not is_web_scenario_path("tests/fixtures/upload-filter.json")
    report = detect_test_source(source, ".", "tests/loreloop/web/upload-filter.json")
    assert report.tests[0].framework == "loreloop-web"
    assert report.tests[0].scope == "integration"
    assert "assert contains: 1 item" in report.tests[0].cases

    invalid = json.loads(source)
    invalid["steps"] = [{"javascript": "alert(1)"}]
    with pytest.raises(DetectionError, match="committed Web scenario is invalid"):
        detect_test_source(
            json.dumps(invalid), ".", "tests/loreloop/web/upload-filter.json"
        )
