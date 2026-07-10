#!/usr/bin/env python3
# ruff: noqa: E402
"""Validate checked-in evaluation artifacts and generate a raw-backed summary."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from eval.metrics import evaluate_task_runs
from eval.run import run_retrieval, run_reverse, run_reverse_matrix
from eval.usability import load_sessions, summarize_sessions

RESULTS = ROOT / "eval/results"
DATASETS = ROOT / "eval/datasets"
RECORDED = {
    "reverse_claude": RESULTS / "reverse-claude-2026-07-10.json",
    "reverse_codex": RESULTS / "reverse-codex-2026-07-10.json",
    "reverse_matrix": RESULTS / "reverse-matrix-claude-2026-07-10.json",
    "scale": RESULTS / "scale-2026-07-10.json",
    "tasks": RESULTS / "task-baselines-claude-2026-07-10.json",
}


class ResultValidationError(ValueError):
    pass


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ResultValidationError(f"{path}: cannot read valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ResultValidationError(f"{path}: top-level result must be an object")
    return value


def _matching_row(rows: object, field: str, value: int, label: str) -> dict[str, Any]:
    if not isinstance(rows, list):
        raise ResultValidationError(f"{label}: expected a list")
    matches = [row for row in rows if isinstance(row, dict) and row.get(field) == value]
    if len(matches) != 1:
        raise ResultValidationError(f"{label}: expected exactly one {field}={value} row")
    return matches[0]


def _validate_reverse(path: Path) -> dict[str, Any]:
    raw = _read_object(path)
    predictions = raw.get("predictions")
    if not isinstance(predictions, list) or any(not isinstance(item, dict) for item in predictions):
        raise ResultValidationError(f"{path}: predictions must be an array of objects")
    return run_reverse(
        DATASETS / "reverse_truth.json",
        predictions_path=path,
    )


def _validate_matrix(path: Path) -> dict[str, Any]:
    raw = _read_object(path)
    rescored = run_reverse_matrix(
        DATASETS / "reverse_matrix/matrix.json",
        predictions_path=path,
    )
    recorded = raw.get("aggregate")
    if not isinstance(recorded, dict):
        raise ResultValidationError(f"{path}: aggregate must be an object")
    for field in (
        "truths",
        "predictions",
        "true_positives",
        "precision",
        "recall",
        "f1",
        "failed_cases",
    ):
        if recorded.get(field) != rescored["aggregate"].get(field):
            raise ResultValidationError(
                f"{path}: recorded aggregate {field} does not match rescoring"
            )
    return rescored


def _validate_tasks(path: Path) -> dict[str, Any]:
    raw = _read_object(path)
    runs = raw.get("runs")
    if not isinstance(runs, list) or not runs:
        raise ResultValidationError(f"{path}: runs must be a non-empty array")
    recalculated = evaluate_task_runs(runs)
    if raw.get("metrics") != recalculated:
        raise ResultValidationError(f"{path}: task metrics do not match recorded runs")
    required = {"no_memory", "session_memory", "codebase_index", "loreloop"}
    if set(recalculated) != required:
        raise ResultValidationError(f"{path}: expected four task context variants")
    return {**raw, "metrics": recalculated}


def build_summary() -> dict[str, Any]:
    retrieval = run_retrieval(DATASETS / "retrieval.json", 5)
    reverse_claude = _validate_reverse(RECORDED["reverse_claude"])
    reverse_codex = _validate_reverse(RECORDED["reverse_codex"])
    matrix = _validate_matrix(RECORDED["reverse_matrix"])
    scale = _read_object(RECORDED["scale"])
    if scale.get("benchmark") != "scale":
        raise ResultValidationError(f"{RECORDED['scale']}: benchmark must be 'scale'")
    retrieval_10k = _matching_row(scale.get("retrieval"), "entries", 10_000, "scale retrieval")
    evidence_10k = _matching_row(
        scale.get("evidence_and_harvest"), "records", 10_000, "scale evidence"
    )
    tasks = _validate_tasks(RECORDED["tasks"])
    sessions_dir = ROOT / "eval/usability/sessions"
    usability = summarize_sessions(load_sessions(sorted(sessions_dir.glob("*.json"))))

    return {
        "date": "2026-07-10",
        "status": "small public baseline; every numeric claim below has checked-in raw input",
        "sources": {name: str(path.relative_to(ROOT)) for name, path in RECORDED.items()},
        "reverse": {
            "dataset": reverse_codex["dataset"],
            "truths": reverse_codex["metrics"]["truths"],
            "codex": reverse_codex["metrics"],
            "claude": reverse_claude["metrics"],
        },
        "retrieval": {
            "dataset": retrieval["dataset"],
            "queries": retrieval["metrics"]["plain"]["queries"],
            "k": 5,
            **retrieval["metrics"],
        },
        "reverse_matrix": {
            "agent": matrix["model"],
            "dataset": matrix["dataset"],
            **matrix["aggregate"],
        },
        "scale": {
            "dataset": scale["methodology"]["dataset"],
            "host": scale["machine"],
            "retrieval_10000": retrieval_10k,
            "evidence_10000": evidence_10k,
        },
        "coding_task_four_way": {
            "agent": tasks["agent"],
            "runs_per_variant": tasks["metrics"]["loreloop"]["runs"],
            **tasks["metrics"],
        },
        "usability": {
            **usability,
            "protocol": "docs/usability-study.md",
        },
        "omitted_claims": [
            "No Codex coding-task comparison is summarized because no raw Codex task result "
            "file is checked in.",
            "No human completion claim is made until real participant session files exist.",
        ],
    }


def threshold_failures(summary: dict[str, Any]) -> list[str]:
    failures = []

    def minimum(label: str, actual: float, expected: float) -> None:
        if actual < expected:
            failures.append(f"{label}: {actual:.4f} < required {expected:.4f}")

    minimum(
        "retrieval.expanded.hit_rate_at_k", summary["retrieval"]["expanded"]["hit_rate_at_k"], 1.0
    )
    minimum("retrieval.expanded.recall_at_k", summary["retrieval"]["expanded"]["recall_at_k"], 1.0)
    minimum("reverse.codex.precision", summary["reverse"]["codex"]["precision"], 0.8)
    minimum("reverse.codex.recall", summary["reverse"]["codex"]["recall"], 0.7)
    minimum("reverse.claude.precision", summary["reverse"]["claude"]["precision"], 0.8)
    minimum("reverse.claude.recall", summary["reverse"]["claude"]["recall"], 0.7)
    minimum("reverse_matrix.precision", summary["reverse_matrix"]["precision"], 0.75)
    minimum("reverse_matrix.recall", summary["reverse_matrix"]["recall"], 0.8)
    minimum(
        "coding_task_four_way.loreloop.success_rate",
        summary["coding_task_four_way"]["loreloop"]["success_rate"],
        0.8,
    )
    minimum(
        "scale.retrieval_10000.recall_at_k",
        summary["scale"]["retrieval_10000"]["recall_at_k"],
        0.95,
    )
    if summary["reverse"]["codex"]["forbidden_hits"]:
        failures.append("reverse.codex.forbidden_hits must be empty")
    if summary["reverse"]["claude"]["forbidden_hits"]:
        failures.append("reverse.claude.forbidden_hits must be empty")
    if summary["reverse_matrix"]["failed_cases"]:
        failures.append("reverse_matrix.failed_cases must be zero")
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check-thresholds", action="store_true")
    args = parser.parse_args(argv)
    try:
        summary = build_summary()
        failures = threshold_failures(summary) if args.check_thresholds else []
    except (OSError, json.JSONDecodeError, ResultValidationError, ValueError) as exc:
        print(f"evaluation result validation failed: {exc}", file=sys.stderr)
        return 2
    text = json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    if failures:
        for failure in failures:
            print(f"threshold failed: {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
