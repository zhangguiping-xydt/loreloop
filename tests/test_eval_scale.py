from eval.scale import run_scale


def test_scale_benchmark_is_reproducible_and_reports_all_metric_families():
    result = run_scale([10], repetitions=1, k=3)

    retrieval = result["retrieval"][0]
    assert retrieval["entries"] == 10
    assert retrieval["projects"] == 5
    assert retrieval["recall_at_k"] == 1.0
    assert retrieval["mrr"] == 1.0
    assert retrieval["prompt_tokens_estimated"]["max"] > 0

    evidence = result["evidence_and_harvest"][0]
    assert evidence["records"] == 10
    assert evidence["verify_latency_ms"]["median"] >= 0
    assert evidence["no_change_harvest_latency_ms"]["median"] >= 0
