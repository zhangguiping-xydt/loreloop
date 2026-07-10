#!/usr/bin/env python3
# ruff: noqa: E402
"""Run loreloop's public, reproducible core evaluations.

Examples:
    uv run python eval/run.py retrieval
    uv run python eval/run.py reverse --agent codex --output eval/results/codex.json
    uv run python eval/run.py reverse --predictions eval/results/codex.json

Retrieval scoring is fully offline. Reverse extraction needs a local Claude or
Codex CLI only when ``--agent`` is requested; saved prediction files can always
be rescored without calling a model.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from eval.metrics import evaluate_rankings, evaluate_reverse_predictions
from loreloop.agents import AgentError, AgentRunner
from loreloop.delegate.context_pack import select
from loreloop.knowledge.code_reverse import ExtractionError, reverse_code
from loreloop.knowledge.model import Channel, Entry, Kind, Source

DATASETS = ROOT / "eval/datasets"
AGENT_COMMANDS = {
    "claude": ("claude", "-p"),
    "codex": ("codex", "exec", "-"),
}


def run_retrieval(dataset_path: Path, k: int) -> dict[str, Any]:
    dataset = _read_json(dataset_path)
    entries = [
        Entry(
            id=item["id"],
            title=item["title"],
            content=item["content"],
            kind=Kind(item["kind"]),
            source=Source(channel=Channel.MANUAL, locator=f"eval:{item['id']}"),
        )
        for item in dataset["entries"]
    ]
    variants: dict[str, list[dict[str, Any]]] = {"plain": [], "expanded": []}
    details = []
    for query in dataset["queries"]:
        plain = select(query["query"], entries, limit=k).entry_ids
        expanded = select(
            query["query"], entries, limit=k, expansion=query.get("expansion", "")
        ).entry_ids
        relevant = query["relevant"]
        variants["plain"].append({"relevant": relevant, "ranking": plain})
        variants["expanded"].append({"relevant": relevant, "ranking": expanded})
        details.append(
            {
                "id": query["id"],
                "query": query["query"],
                "relevant": relevant,
                "plain": plain,
                "expanded": expanded,
                "expansion": query.get("expansion", ""),
            }
        )
    return {
        "benchmark": "retrieval",
        "dataset": str(dataset_path.relative_to(ROOT)),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "metrics": {
            name: evaluate_rankings(examples, k=k) for name, examples in variants.items()
        },
        "queries": details,
    }


def run_reverse(
    truth_path: Path,
    *,
    agent: str | None = None,
    predictions_path: Path | None = None,
) -> dict[str, Any]:
    if bool(agent) == bool(predictions_path):
        raise ValueError("choose exactly one of --agent or --predictions")
    truth = _read_json(truth_path)
    model_output: list[dict[str, Any]]
    if predictions_path:
        saved = _read_json(predictions_path)
        model_output = saved.get("predictions", saved) if isinstance(saved, dict) else saved
        model_name = saved.get("model", "saved") if isinstance(saved, dict) else "saved"
    else:
        model_name = str(agent)
        model_output, _ = _run_reverse_agent(str(agent), DATASETS / "reverse_project")
    return {
        "benchmark": "reverse",
        "dataset": str(truth_path.relative_to(ROOT)),
        "model": model_name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "metrics": evaluate_reverse_predictions(
            truth["truths"], model_output, forbidden=truth.get("forbidden", [])
        ),
        "predictions": model_output,
    }


class CountingRunner:
    def __init__(self, runner: AgentRunner) -> None:
        self.runner = runner
        self.calls = 0
        self.input_chars = 0
        self.output_chars = 0

    def run(self, prompt: str) -> str:
        self.calls += 1
        self.input_chars += len(prompt)
        output = self.runner.run(prompt)
        self.output_chars += len(output)
        return output


def _run_reverse_agent(agent: str, fixture: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    command = AGENT_COMMANDS[agent]
    with tempfile.TemporaryDirectory(prefix="loreloop-reverse-eval-") as temp:
        repo = Path(temp) / "project"
        shutil.copytree(fixture, repo)
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "eval@loreloop.local"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "loreloop eval"], cwd=repo, check=True)
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", "evaluation fixture"], cwd=repo, check=True)
        runner = CountingRunner(AgentRunner(command=command, timeout=600))
        started = time.perf_counter()
        entries = reverse_code(runner, repo)
        duration = time.perf_counter() - started
    predictions = [
        {
            "title": entry.title,
            "content": entry.content,
            "kind": entry.kind.value,
            "source": entry.source.locator,
            "evidence": {
                key: value
                for key, value in {
                    "symbol": entry.source.symbol,
                    "line_start": entry.source.line_start,
                    "line_end": entry.source.line_end,
                    "excerpt": entry.source.excerpt,
                }.items()
                if value is not None
            },
        }
        for entry in entries
    ]
    return predictions, {
        "agent_calls": runner.calls,
        "duration_seconds": round(duration, 3),
        "input_chars": runner.input_chars,
        "output_chars": runner.output_chars,
        "input_tokens_estimated": (runner.input_chars + 3) // 4,
        "output_tokens_estimated": (runner.output_chars + 3) // 4,
        "token_estimate_method": "characters / 4; use vendor telemetry for billed tokens",
    }


def run_reverse_matrix(
    matrix_path: Path,
    *,
    agent: str | None = None,
    predictions_path: Path | None = None,
) -> dict[str, Any]:
    if bool(agent) == bool(predictions_path):
        raise ValueError("choose exactly one of --agent or --predictions")
    matrix = _read_json(matrix_path)
    saved = _read_json(predictions_path) if predictions_path else None
    saved_cases = {item["id"]: item for item in saved.get("cases", [])} if saved else {}
    cases = []
    for case in matrix["cases"]:
        fixture = matrix_path.parent / case["fixture"]
        if saved is not None:
            recorded = saved_cases.get(case["id"])
            if recorded is None:
                raise ValueError(f"saved matrix is missing case {case['id']!r}")
            predictions = recorded.get("predictions", [])
            cost = recorded.get("cost", {})
            error = recorded.get("error")
        else:
            started = time.perf_counter()
            error = None
            try:
                predictions, cost = _run_reverse_agent(str(agent), fixture)
            except (AgentError, ExtractionError) as exc:
                predictions = []
                cost = {
                    "duration_seconds": round(time.perf_counter() - started, 3),
                    "agent_calls": None,
                    "input_tokens_estimated": None,
                    "output_tokens_estimated": None,
                    "note": "run failed before complete usage counters could be returned",
                }
                error = f"{exc.__class__.__name__}: {exc}"
        cases.append(
            {
                "id": case["id"],
                "language": case["language"],
                "fixture": str(fixture.relative_to(ROOT)),
                "metrics": evaluate_reverse_predictions(
                    case["truths"], predictions, forbidden=case.get("forbidden", [])
                ),
                "cost": cost,
                "error": error,
                "predictions": predictions,
            }
        )
    truth_count = sum(item["metrics"]["truths"] for item in cases)
    prediction_count = sum(item["metrics"]["predictions"] for item in cases)
    true_positives = sum(item["metrics"]["true_positives"] for item in cases)
    precision = true_positives / prediction_count if prediction_count else 0.0
    recall = true_positives / truth_count if truth_count else 0.0
    return {
        "benchmark": "reverse-matrix",
        "dataset": str(matrix_path.relative_to(ROOT)),
        "model": saved.get("model", "saved") if saved else agent,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "aggregate": {
            "truths": truth_count,
            "predictions": prediction_count,
            "true_positives": true_positives,
            "precision": precision,
            "recall": recall,
            "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
            "failed_cases": sum(item["error"] is not None for item in cases),
            "duration_seconds": round(
                sum(item["cost"]["duration_seconds"] for item in cases), 3
            ),
            "input_tokens_estimated": sum(
                item["cost"]["input_tokens_estimated"] or 0 for item in cases
            ),
            "output_tokens_estimated": sum(
                item["cost"]["output_tokens_estimated"] or 0 for item in cases
            ),
        },
        "cases": cases,
    }


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_result(result: dict[str, Any], output: Path | None) -> None:
    text = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
        print(f"wrote {output}", file=sys.stderr)
    print(text, end="")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="benchmark", required=True)

    retrieval = sub.add_parser("retrieval", help="run the offline retrieval benchmark")
    retrieval.add_argument("--dataset", type=Path, default=DATASETS / "retrieval.json")
    retrieval.add_argument("-k", type=int, default=5)
    retrieval.add_argument("--output", type=Path)

    reverse = sub.add_parser("reverse", help="run or rescore reverse extraction")
    reverse.add_argument("--truth", type=Path, default=DATASETS / "reverse_truth.json")
    source = reverse.add_mutually_exclusive_group(required=True)
    source.add_argument("--agent", choices=sorted(AGENT_COMMANDS))
    source.add_argument("--predictions", type=Path)
    reverse.add_argument("--output", type=Path)

    matrix = sub.add_parser(
        "reverse-matrix", help="benchmark Python, TypeScript, and mixed repositories"
    )
    matrix.add_argument(
        "--matrix", type=Path, default=DATASETS / "reverse_matrix/matrix.json"
    )
    matrix_source = matrix.add_mutually_exclusive_group(required=True)
    matrix_source.add_argument("--agent", choices=sorted(AGENT_COMMANDS))
    matrix_source.add_argument("--predictions", type=Path)
    matrix.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.benchmark == "retrieval":
            result = run_retrieval(args.dataset.resolve(), args.k)
        elif args.benchmark == "reverse":
            result = run_reverse(
                args.truth.resolve(), agent=args.agent, predictions_path=args.predictions
            )
        else:
            result = run_reverse_matrix(
                args.matrix.resolve(),
                agent=args.agent,
                predictions_path=args.predictions.resolve() if args.predictions else None,
            )
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"evaluation failed: {exc}", file=sys.stderr)
        return 2
    _write_result(result, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
