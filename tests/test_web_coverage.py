from pathlib import Path

import pytest

from loreloop.evidence.artifacts import ArtifactStore
from loreloop.evidence.chain import EvidenceChain
from loreloop.webexplore.actions import (
    ActionExecution,
    PageState,
    StepTrace,
    parse_action_script,
)
from loreloop.webexplore.browser import Observation
from loreloop.webexplore.coverage import build_web_coverage, render_web_coverage
from loreloop.webexplore.scenarios import (
    WEB_EXPLORATION_EVENT,
    ScenarioAssertion,
    WebScenario,
    approve_candidate,
    run_scenario,
    write_candidate,
)


def test_coverage_distinguishes_exercised_observed_and_write_gated_controls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = Observation(
        "https://example.test/ui/#/ratio",
        "Ratio config",
        "Ratio config\nNo filter",
        forms=["input:text:city:城市"],
        buttons=["查询", "新增"],
    )
    after = Observation(
        before.url,
        before.title,
        "Ratio config\nFiltered results: 1 item",
        forms=before.forms,
        buttons=before.buttons,
    )
    artifacts = ArtifactStore.for_workdir(tmp_path)
    chain = EvidenceChain.for_workdir(tmp_path)
    source_artifact = artifacts.save_observation(before)[0]
    chain.append(
        WEB_EXPLORATION_EVENT,
        {
            "start_url": before.url,
            "trace": "explorations/fixture.jsonl",
            "pages": [
                {
                    "url": before.url,
                    "title": before.title,
                    "snapshot": before.snapshot_hash,
                    "artifact": source_artifact,
                }
            ],
        },
    )
    scenario = WebScenario(
        "ratio-search",
        "Search ratio configuration",
        parse_action_script(
            {
                "version": 1,
                "base": "https://example.test",
                "steps": [
                    {"goto": "/ui/#/ratio"},
                    {"click": {"role": "button", "text": "查询"}},
                ],
            }
        ),
        (ScenarioAssertion("contains", "Filtered results: 1 item"),),
        tags=("web",),
        source_artifact=source_artifact,
        source_snapshot=before.snapshot_hash,
    )
    write_candidate(tmp_path, scenario)
    approve_candidate(tmp_path, scenario.scenario_id, chain)
    execution = ActionExecution(
        scenario.script.digest,
        "completed",
        [
            StepTrace(
                0,
                {"goto": "/ui/#/ratio"},
                "completed",
                "ok",
                1,
                before.url,
                before.title,
                before.snapshot_hash,
            ),
            StepTrace(
                1,
                {"click": {"role": "button", "text": "查询"}},
                "completed",
                "ok",
                1,
                after.url,
                after.title,
                after.snapshot_hash,
            ),
        ],
        final_observation=after,
        states=(PageState(0, before), PageState(1, after)),
    )
    monkeypatch.setattr(
        "loreloop.webexplore.scenarios.execute_action_script",
        lambda *args, **kwargs: execution,
    )

    result = run_scenario(scenario, object(), chain, artifacts)
    report = build_web_coverage(tmp_path, chain.verify(), artifacts)

    assert result.passed
    assert report.summary == {
        "pages_observed": 1,
        "pages_tested": 1,
        "pages_trialed": 0,
        "states_observed": 2,
        "controls_observed": 3,
        "controls_exercised": 1,
        "controls_trial_exercised": 0,
        "controls_write_gated": 1,
        "journeys_candidate": 0,
        "journeys_approved": 1,
        "journeys_passed": 1,
        "journeys_failed": 0,
    }
    controls = {
        (item["kind"], item["label"]): item["status"]
        for item in report.pages[0].to_json()["controls"]
    }
    assert controls[("button", "查询")] == "exercised"
    assert controls[("button", "新增")] == "write-gated"
    assert controls[("field", "城市")] == "observed-only"
    markdown = render_web_coverage(report, "markdown")
    assert "- [x] button: 查询" in markdown
    assert "- [ ] button: 新增 — write authorization required" in markdown
    assert "ratio-search" in markdown


def test_coverage_reports_unapproved_journey_as_candidate(tmp_path: Path) -> None:
    scenario = WebScenario(
        "candidate-only",
        "Candidate only",
        parse_action_script(
            {
                "version": 1,
                "base": "https://example.test",
                "steps": [{"goto": "/"}],
            }
        ),
        (ScenarioAssertion("title-contains", "Example"),),
    )
    write_candidate(tmp_path, scenario)

    report = build_web_coverage(
        tmp_path,
        EvidenceChain.for_workdir(tmp_path).verify(),
        ArtifactStore.for_workdir(tmp_path),
    )

    assert report.summary["journeys_candidate"] == 1
    assert report.journeys[0].status == "not-run"
    assert "candidate-only" in render_web_coverage(report, "json")
