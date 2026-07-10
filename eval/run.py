#!/usr/bin/env python3
# ruff: noqa: E402
"""Run knowhelm's public, reproducible core evaluations.

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
from knowhelm.agents import AgentRunner
from knowhelm.delegate.context_pack import select
from knowhelm.knowledge.code_reverse import reverse_code
from knowhelm.knowledge.model import Channel, Entry, Kind, Source

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
        model_output = _run_reverse_agent(str(agent))
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


def _run_reverse_agent(agent: str) -> list[dict[str, Any]]:
    command = AGENT_COMMANDS[agent]
    fixture = DATASETS / "reverse_project"
    with tempfile.TemporaryDirectory(prefix="knowhelm-reverse-eval-") as temp:
        repo = Path(temp) / "project"
        shutil.copytree(fixture, repo)
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "eval@knowhelm.local"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "knowhelm eval"], cwd=repo, check=True)
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", "evaluation fixture"], cwd=repo, check=True)
        entries = reverse_code(AgentRunner(command=command, timeout=600), repo)
    return [
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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.benchmark == "retrieval":
            result = run_retrieval(args.dataset.resolve(), args.k)
        else:
            result = run_reverse(
                args.truth.resolve(), agent=args.agent, predictions_path=args.predictions
            )
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"evaluation failed: {exc}", file=sys.stderr)
        return 2
    _write_result(result, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
