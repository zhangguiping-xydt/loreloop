from eval.validate_results import build_summary, threshold_failures


def test_checked_in_eval_results_rescore_and_meet_release_thresholds():
    summary = build_summary()

    assert threshold_failures(summary) == []
    assert summary["reverse"]["codex"]["predictions"] == 14
    assert summary["coding_task_four_way"]["loreloop"]["passed"] == 3
    assert summary["usability"]["status"] == "awaiting real participants"
    assert "coding_tasks" not in summary


def test_eval_thresholds_report_regressions():
    summary = build_summary()
    summary["retrieval"]["expanded"]["recall_at_k"] = 0.5

    assert threshold_failures(summary) == [
        "retrieval.expanded.recall_at_k: 0.5000 < required 1.0000"
    ]
