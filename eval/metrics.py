"""Deterministic metrics shared by the public evaluation commands.

The functions in this module deliberately know nothing about model vendors or
agent CLIs.  They score saved artifacts, which keeps published results
re-auditable and lets contributors add a new model run without changing the
benchmark implementation.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

_TOKEN = re.compile(r"[a-z0-9_]+|[\u4e00-\u9fff]+", re.IGNORECASE)


def evaluate_rankings(examples: Sequence[Mapping[str, Any]], k: int) -> dict[str, Any]:
    """Return macro Precision@K, Recall@K, Hit@K and MRR.

    Precision uses a denominator of ``k`` even when a ranker returns fewer
    results. This is the conventional Precision@K definition and prevents a
    ranker from looking artificially precise by returning one item only.
    """
    if k < 1:
        raise ValueError("k must be at least 1")
    if not examples:
        raise ValueError("at least one retrieval example is required")

    precision_total = 0.0
    recall_total = 0.0
    reciprocal_total = 0.0
    hits = 0
    retrieved_total = 0
    relevant_retrieved = 0
    for example in examples:
        relevant = set(example.get("relevant", []))
        if not relevant:
            raise ValueError("every retrieval example needs at least one relevant id")
        ranking = list(example.get("ranking", []))
        top = ranking[:k]
        matched = relevant.intersection(top)
        retrieved_total += len(top)
        relevant_retrieved += len(matched)
        precision_total += len(matched) / k
        recall_total += len(matched) / len(relevant)
        hits += bool(matched)
        reciprocal_total += next(
            (1.0 / rank for rank, entry_id in enumerate(ranking, start=1) if entry_id in relevant),
            0.0,
        )

    count = len(examples)
    return {
        "queries": count,
        "k": k,
        "hit_rate_at_k": hits / count,
        "precision_at_k": precision_total / count,
        "recall_at_k": recall_total / count,
        "mrr": reciprocal_total / count,
        "retrieved": retrieved_total,
        "relevant_retrieved": relevant_retrieved,
        "precision_over_returned": _safe_div(relevant_retrieved, retrieved_total),
        "mean_returned": retrieved_total / count,
    }


def evaluate_reverse_predictions(
    truths: Sequence[Mapping[str, Any]],
    predictions: Sequence[Mapping[str, Any] | str],
    *,
    forbidden: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Score high-value knowledge extraction against deterministic matchers.

    ``match_all`` is a list of alternatives; every token in one alternative
    must occur in a prediction. Predictions are matched one-to-one so a broad
    compound statement cannot claim credit for several atomic truths.
    """
    truth_by_id = {_required_id(item): item for item in truths}
    prediction_items = [
        {"content": item} if isinstance(item, str) else dict(item) for item in predictions
    ]
    prediction_texts = [
        str(item.get("content", item.get("claim", ""))) for item in prediction_items
    ]
    unmatched = set(truth_by_id)
    matched: list[str] = []
    true_positives = 0
    for prediction, text in zip(prediction_items, prediction_texts):
        candidates = [
            truth_id
            for truth_id in sorted(unmatched)
            if _prediction_matches(prediction, text, truth_by_id[truth_id])
        ]
        if candidates:
            chosen = candidates[0]
            unmatched.remove(chosen)
            matched.append(chosen)
            true_positives += 1

    false_positives = len(prediction_texts) - true_positives
    false_negatives = len(truth_by_id) - true_positives
    precision = _safe_div(true_positives, true_positives + false_positives)
    recall = _safe_div(true_positives, true_positives + false_negatives)
    f1 = _safe_div(2 * precision * recall, precision + recall)
    forbidden_hits = sorted(
        _required_id(rule)
        for rule in forbidden
        if any(_matches(text, rule.get("match_all", [])) for text in prediction_texts)
    )
    return {
        "truths": len(truth_by_id),
        "predictions": len(prediction_texts),
        "true_positives": true_positives,
        "false_positives": false_positives,
        "false_negatives": false_negatives,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "matched_truth_ids": sorted(matched),
        "missed_truth_ids": sorted(unmatched),
        "forbidden_hits": forbidden_hits,
    }


def evaluate_task_runs(runs: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Group executable coding-task success rates by context variant."""
    grouped: dict[str, list[bool]] = defaultdict(list)
    for run in runs:
        variant = str(run.get("variant", "")).strip()
        if not variant:
            raise ValueError("task run is missing variant")
        passed = run.get("passed")
        if not isinstance(passed, bool):
            raise ValueError("task run passed must be a boolean")
        grouped[variant].append(passed)
    return {
        variant: {
            "runs": len(results),
            "passed": sum(results),
            "success_rate": sum(results) / len(results),
        }
        for variant, results in sorted(grouped.items())
    }


def _matches(text: str, alternatives: Sequence[Sequence[str]]) -> bool:
    normalized = " ".join(_TOKEN.findall(text.casefold()))
    return any(
        all(token.casefold() in normalized for token in alternative) for alternative in alternatives
    )


def _prediction_matches(prediction: Mapping[str, Any], text: str, truth: Mapping[str, Any]) -> bool:
    if _matches(text, truth.get("match_all", [])):
        return True
    truth_source = truth.get("source")
    evidence = prediction.get("evidence")
    if (
        not truth.get("allow_evidence_match")
        or not truth_source
        or not isinstance(evidence, Mapping)
    ):
        return False
    prediction_source = str(prediction.get("source", "")).rsplit("@", 1)[0]
    if prediction_source != truth_source:
        return False
    start = evidence.get("line_start")
    end = evidence.get("line_end")
    truth_start = truth.get("line_start")
    truth_end = truth.get("line_end")
    if not all(isinstance(value, int) for value in (start, end, truth_start, truth_end)):
        return False
    return start <= truth_end and end >= truth_start


def _required_id(item: Mapping[str, Any]) -> str:
    value = item.get("id")
    if not isinstance(value, str) or not value:
        raise ValueError("evaluation rule is missing id")
    return value


def _safe_div(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0
