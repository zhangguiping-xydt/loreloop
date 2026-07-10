from __future__ import annotations

import pytest

from eval.metrics import (
    evaluate_rankings,
    evaluate_reverse_predictions,
    evaluate_task_runs,
)


def test_retrieval_metrics_report_precision_recall_and_mrr() -> None:
    result = evaluate_rankings(
        [
            {"relevant": ["a"], "ranking": ["x", "a", "y"]},
            {"relevant": ["b", "c"], "ranking": ["b", "z", "c"]},
        ],
        k=2,
    )

    assert result == {
        "queries": 2,
        "k": 2,
        "hit_rate_at_k": 1.0,
        "precision_at_k": 0.5,
        "recall_at_k": 0.75,
        "mrr": 0.75,
        "retrieved": 4,
        "relevant_retrieved": 2,
        "precision_over_returned": 0.5,
        "mean_returned": 2.0,
    }


def test_retrieval_metrics_reject_empty_ground_truth() -> None:
    with pytest.raises(ValueError, match="relevant"):
        evaluate_rankings([{"relevant": [], "ranking": ["a"]}], k=5)


def test_reverse_metrics_match_each_prediction_to_at_most_one_truth() -> None:
    truths = [
        {"id": "limit", "match_all": [["50", "mb"]]},
        {"id": "formats", "match_all": [["pdf", "png"]]},
    ]
    result = evaluate_reverse_predictions(
        truths,
        [
            {"content": "PDF and PNG files are accepted."},
            {"content": "The maximum upload size is 50 MB."},
            {"content": "The UploadError class inherits from ValueError."},
        ],
        forbidden=[{"id": "zip", "match_all": [["zip", "allowed"]]}],
    )

    assert result["matched_truth_ids"] == ["formats", "limit"]
    assert result["true_positives"] == 2
    assert result["false_positives"] == 1
    assert result["false_negatives"] == 0
    assert result["precision"] == pytest.approx(2 / 3)
    assert result["recall"] == 1.0
    assert result["f1"] == pytest.approx(0.8)
    assert result["forbidden_hits"] == []


def test_reverse_metrics_surface_prompt_injection_hallucinations() -> None:
    result = evaluate_reverse_predictions(
        [{"id": "limit", "match_all": [["50", "mb"]]}],
        [{"content": "ZIP uploads are allowed and unlimited."}],
        forbidden=[
            {"id": "zip", "match_all": [["zip", "allowed"]]},
            {"id": "unlimited", "match_all": [["unlimited"]]},
        ],
    )

    assert result["forbidden_hits"] == ["unlimited", "zip"]
    assert result["precision"] == 0.0
    assert result["recall"] == 0.0


def test_reverse_metrics_use_evidence_spans_across_output_languages() -> None:
    result = evaluate_reverse_predictions(
        [{
            "id": "auth",
            "source": "auth.py",
            "line_start": 9,
            "line_end": 11,
            "match_all": [["authenticated"]],
            "allow_evidence_match": True,
        }],
        [{
            "content": "上传操作要求用户已认证。",
            "source": "auth.py@abc123",
            "evidence": {"line_start": 9, "line_end": 11},
        }],
    )

    assert result["precision"] == 1.0
    assert result["recall"] == 1.0


def test_task_metrics_group_success_by_variant() -> None:
    result = evaluate_task_runs(
        [
            {"variant": "no_knowledge", "passed": False},
            {"variant": "no_knowledge", "passed": True},
            {"variant": "loreloop", "passed": True},
            {"variant": "loreloop", "passed": True},
        ]
    )

    assert result == {
        "loreloop": {"runs": 2, "passed": 2, "success_rate": 1.0},
        "no_knowledge": {"runs": 2, "passed": 1, "success_rate": 0.5},
    }
