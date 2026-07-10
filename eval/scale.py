#!/usr/bin/env python3
# ruff: noqa: E402
"""Reproducible scalability benchmarks for retrieval, evidence, and harvest."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import statistics
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from eval.metrics import evaluate_rankings
from loreloop.delegate.context_pack import render, select
from loreloop.evidence.chain import EvidenceChain, EvidenceRecord, _chain_hash
from loreloop.knowledge.harvest import harvest_run
from loreloop.knowledge.model import Channel, Entry, Kind, Source
from loreloop.knowledge.store import KnowledgeStore
from loreloop.report.acceptance import RunSummary


QUERIES = [
    ("upload quota", "upload_quota"),
    ("webhook signing", "webhook_signing"),
    ("invoice retention", "invoice_retention"),
    ("session expiry", "session_expiry"),
    ("customer export", "customer_export"),
]


class NoopAgent:
    def run(self, prompt: str) -> str:
        raise AssertionError("the no-change harvest benchmark must not call an agent")


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))]


def _entries(size: int) -> list[Entry]:
    entries = []
    for index in range(size):
        query, key = QUERIES[index % len(QUERIES)]
        cycle = index // len(QUERIES)
        is_target = cycle == 0
        marker = key if is_target else f"archive_{cycle}_{key}"
        content = (
            f"Canonical {query} policy uses marker {marker}."
            if is_target
            else f"Historical project note {cycle} about unrelated {marker} maintenance."
        )
        entries.append(
            Entry(
                id=f"entry-{index:05d}",
                title=f"{query.title()} {'policy' if is_target else f'archive {cycle}'}",
                content=content,
                kind=Kind.CONSTRAINT,
                source=Source(
                    channel=Channel.MANUAL,
                    locator=f"project-{index % 5}:entry-{index:05d}",
                ),
            )
        )
    return entries


def benchmark_retrieval(sizes: list[int], repetitions: int, k: int) -> list[dict[str, Any]]:
    rows = []
    for size in sizes:
        entries = _entries(size)
        timings = []
        examples = []
        token_estimates = []
        for _ in range(repetitions):
            for query, key in QUERIES:
                started = time.perf_counter()
                pack = select(f"canonical {query} marker {key}", entries, limit=k)
                timings.append((time.perf_counter() - started) * 1000)
                examples.append({"relevant": [f"entry-{QUERIES.index((query, key)):05d}"], "ranking": pack.entry_ids})
                token_estimates.append((len(render(pack)) + 3) // 4)
        metrics = evaluate_rankings(examples, k=k)
        rows.append(
            {
                "entries": size,
                "projects": 5,
                "queries": len(examples),
                "k": k,
                "latency_ms": {
                    "median": round(statistics.median(timings), 3),
                    "p95": round(_percentile(timings, 0.95), 3),
                    "max": round(max(timings), 3),
                },
                "prompt_tokens_estimated": {
                    "median": round(statistics.median(token_estimates)),
                    "max": max(token_estimates),
                    "method": "rendered context characters / 4; tokenizer-independent estimate",
                },
                "precision_at_k": metrics["precision_at_k"],
                "recall_at_k": metrics["recall_at_k"],
                "mrr": metrics["mrr"],
                "mean_returned": metrics["mean_returned"],
            }
        )
    return rows


def _seed_chain(chain: EvidenceChain, count: int, run_id: str) -> None:
    records = []
    prev = "genesis"
    fixed_ts = "2026-07-10T00:00:00+00:00"
    filler_count = max(0, count - 2)
    payloads = [("benchmark_filler", {"sequence": index}) for index in range(filler_count)]
    payloads.extend(
        [
            (
                "delegation_completed",
                {
                    "run_id": run_id,
                    "task": "scale benchmark",
                    "context_entries": [],
                    "base_commits": {},
                },
            ),
            ("check_passed", {"run_id": run_id, "check": "noop", "judge": "operator"}),
        ]
    )
    for index, (event, payload) in enumerate(payloads):
        digest = _chain_hash(prev, index, fixed_ts, event, payload)
        record = EvidenceRecord(
            index=index,
            ts=fixed_ts,
            event=event,
            payload=payload,
            prev_hash=prev,
            chain_hash=digest,
            signature=chain._sign(digest),
        )
        records.append(record)
        prev = digest
    chain._path.write_text(
        "".join(json.dumps(record.__dict__, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )
    chain._commit_head(records[-1])


def benchmark_evidence(sizes: list[int], repetitions: int) -> list[dict[str, Any]]:
    rows = []
    for size in sizes:
        verify_times = []
        harvest_times = []
        for repetition in range(repetitions):
            with tempfile.TemporaryDirectory(prefix="loreloop-chain-scale-") as temp:
                workdir = Path(temp) / "project"
                key_dir = Path(temp) / "keys"
                workdir.mkdir()
                previous = os.environ.get("LORELOOP_KEY_DIR")
                os.environ["LORELOOP_KEY_DIR"] = str(key_dir)
                try:
                    chain = EvidenceChain.for_workdir(workdir)
                    run_id = f"scale-{size}-{repetition}"
                    _seed_chain(chain, size, run_id)
                    started = time.perf_counter()
                    assert len(chain.verify()) == size
                    verify_times.append((time.perf_counter() - started) * 1000)

                    run = RunSummary(
                        run_id=run_id,
                        task="scale benchmark",
                        context_entries=[],
                        finished=True,
                        base_commits={},
                    )
                    with KnowledgeStore(workdir / ".loreloop/knowledge.db") as store:
                        started = time.perf_counter()
                        harvest_run(run, chain, store, NoopAgent(), workdir)
                        harvest_times.append((time.perf_counter() - started) * 1000)
                finally:
                    if previous is None:
                        os.environ.pop("LORELOOP_KEY_DIR", None)
                    else:
                        os.environ["LORELOOP_KEY_DIR"] = previous
        rows.append(
            {
                "records": size,
                "verify_latency_ms": {
                    "median": round(statistics.median(verify_times), 3),
                    "max": round(max(verify_times), 3),
                },
                "no_change_harvest_latency_ms": {
                    "median": round(statistics.median(harvest_times), 3),
                    "max": round(max(harvest_times), 3),
                },
                "note": "harvest includes chain verification, acceptance evaluation, and final append",
            }
        )
    return rows


def run_scale(sizes: list[int], repetitions: int, k: int) -> dict[str, Any]:
    return {
        "benchmark": "scale",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "machine": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "processor": platform.processor() or "unknown",
        },
        "methodology": {
            "dataset": "deterministic synthetic scale fixture; not a relevance-diversity benchmark",
            "sizes": sizes,
            "repetitions": repetitions,
            "fixture_digest": hashlib.sha256(repr((QUERIES, sizes, k)).encode()).hexdigest(),
        },
        "retrieval": benchmark_retrieval(sizes, repetitions, k),
        "evidence_and_harvest": benchmark_evidence(sizes, repetitions),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", type=int, nargs="+", default=[100, 1000, 10000])
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("-k", type=int, default=5)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if any(size < 2 for size in args.sizes) or args.repetitions < 1 or args.k < 1:
        parser.error("sizes must be >= 2; repetitions and k must be positive")
    result = run_scale(args.sizes, args.repetitions, args.k)
    text = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
