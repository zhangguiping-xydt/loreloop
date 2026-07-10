import json

import pytest

from eval.usability import StudyRecordError, summarize_sessions, validate_session


def session(**changes):
    data = {
        "participant_id": "p1",
        "study_round": 1,
        "completed": True,
        "time_to_first_success_seconds": 240,
        "wrong_turns": ["ran init before doctor"],
        "help_lookups": ["knowhelm init --help"],
        "recovery_attempts": ["ran doctor"],
        "abandoned_step": None,
        "trust_model_explanation": "The chain, not SQLite, grants trust.",
    }
    data.update(changes)
    return data


def test_usability_summary_reports_observed_outcomes_without_inference():
    result = summarize_sessions([session(), session(participant_id="p2", completed=False,
                                                     time_to_first_success_seconds=None,
                                                     abandoned_step="verify")])

    assert result["sessions"] == 2
    assert result["completion_rate"] == 0.5
    assert result["time_to_first_success_seconds"]["median"] == 240
    assert result["abandoned_steps"] == ["verify"]


def test_empty_usability_summary_explicitly_awaits_real_people():
    result = summarize_sessions([])
    assert result["status"] == "awaiting real participants"
    assert result["sessions"] == 0


def test_completed_record_requires_success_time():
    with pytest.raises(StudyRecordError, match="success time"):
        validate_session(session(time_to_first_success_seconds=None))


def test_template_is_valid():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    template = json.loads(
        (root / "eval/usability/session-template.json").read_text(encoding="utf-8")
    )
    assert validate_session(template)["participant_id"] == "pseudonym-001"
