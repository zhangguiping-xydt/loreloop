#!/usr/bin/env python3
"""Validate and summarize zero-context usability study records."""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SESSIONS = ROOT / "eval/usability/sessions"


class StudyRecordError(ValueError):
    pass


def validate_session(data: dict[str, Any], source: str = "session") -> dict[str, Any]:
    required = {
        "participant_id": str,
        "study_round": int,
        "completed": bool,
        "time_to_first_success_seconds": (int, float, type(None)),
        "wrong_turns": list,
        "help_lookups": list,
        "recovery_attempts": list,
        "abandoned_step": (str, type(None)),
        "trust_model_explanation": str,
    }
    for field, expected in required.items():
        if field not in data or not isinstance(data[field], expected):
            raise StudyRecordError(f"{source}: field {field!r} has the wrong type or is missing")
    if not data["participant_id"].strip():
        raise StudyRecordError(f"{source}: participant_id must be a pseudonymous non-empty id")
    if data["study_round"] < 1:
        raise StudyRecordError(f"{source}: study_round must be positive")
    elapsed = data["time_to_first_success_seconds"]
    if data["completed"] and (elapsed is None or elapsed < 0):
        raise StudyRecordError(f"{source}: completed sessions need a non-negative success time")
    if not data["completed"] and elapsed is not None:
        raise StudyRecordError(f"{source}: abandoned sessions must use null success time")
    return data


def summarize_sessions(sessions: list[dict[str, Any]]) -> dict[str, Any]:
    if not sessions:
        return {
            "status": "awaiting real participants",
            "sessions": 0,
            "note": "No human outcomes are inferred or simulated.",
        }
    completed = [item for item in sessions if item["completed"]]
    times = [item["time_to_first_success_seconds"] for item in completed]
    return {
        "status": "observed human sessions",
        "sessions": len(sessions),
        "completed": len(completed),
        "completion_rate": len(completed) / len(sessions),
        "time_to_first_success_seconds": {
            "median": statistics.median(times) if times else None,
            "max": max(times) if times else None,
        },
        "wrong_turns": sum(len(item["wrong_turns"]) for item in sessions),
        "help_lookups": sum(len(item["help_lookups"]) for item in sessions),
        "recovery_attempts": sum(len(item["recovery_attempts"]) for item in sessions),
        "abandoned_steps": [item["abandoned_step"] for item in sessions if item["abandoned_step"]],
        "trust_model_explanations": [
            {
                "participant_id": item["participant_id"],
                "text": item["trust_model_explanation"],
            }
            for item in sessions
        ],
    }


def load_sessions(paths: list[Path]) -> list[dict[str, Any]]:
    sessions = []
    seen = set()
    for path in paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise StudyRecordError(f"{path}: record must be a JSON object")
        session = validate_session(data, str(path))
        key = (session["participant_id"], session["study_round"])
        if key in seen:
            raise StudyRecordError(f"{path}: duplicate participant/round {key!r}")
        seen.add(key)
        sessions.append(session)
    return sessions


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", type=Path, nargs="*")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    paths = args.paths or sorted(DEFAULT_SESSIONS.glob("*.json"))
    try:
        result = summarize_sessions(load_sessions(paths))
    except (OSError, json.JSONDecodeError, StudyRecordError) as exc:
        print(f"usability evaluation failed: {exc}")
        return 2
    text = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
