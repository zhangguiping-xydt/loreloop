# knowhelm evaluation suite

This directory makes the three central product claims falsifiable:

1. reverse engineering extracts atomic, high-value facts with source evidence;
2. retrieval finds the right facts without flooding the context pack;
3. injected project knowledge improves executable coding-task outcomes.

All metric code and fixed datasets are offline and dependency-free. Live
reverse and coding-task runs are optional because they invoke a local Claude
or Codex CLI and may consume model quota.

## Quick commands

```bash
# Deterministic retrieval benchmark
uv run python eval/run.py retrieval

# Re-score recorded model outputs without calling a model
uv run python eval/run.py reverse \
  --predictions eval/results/reverse-codex-2026-07-10.json
uv run python eval/run.py reverse \
  --predictions eval/results/reverse-claude-2026-07-10.json

# Produce a fresh reverse run
uv run python eval/run.py reverse --agent codex --output /tmp/reverse-codex.json

# Real coding-agent A/B tasks
uv run python eval/task_runner.py \
  --agent codex \
  --output /tmp/codex-task-results.json
```

## Metrics

Reverse extraction uses a frozen list of durable truths and forbidden claims.
A prediction matches by deterministic text rules or by overlap with a
ground-truth source/evidence span. Matching is one-to-one: a compound claim
that bundles two atomic truths receives credit for one only.

- **Precision**: matched high-value predictions / all predictions.
- **Recall**: matched truths / all ground-truth truths.
- **Forbidden hits**: deprecated or prompt-injected claims that must never be emitted.

Retrieval reports conventional macro Precision@K, Recall@K, Hit@K and MRR.
Because each current query has one relevant entry, Precision@5 has a ceiling of
0.2. `precision_over_returned` additionally shows how much of the variable-size
adaptive result set was relevant.

Coding-task success requires all of the following:

- the agent exits successfully;
- public tests pass;
- hidden contract tests pass;
- the run does not time out.

The no-knowledge and knowhelm variants receive the same repository and task.
Only the knowhelm variant receives the rendered established-facts section.
Hidden evaluators remain outside the repository copied for the agent.

## Safety and limitations

- Task transcripts are redacted and truncated before saving. Recorded public
  baselines omit transcripts because agents may inspect local environment variables.
- The committed benchmark is intentionally small. It proves the harness and
  catches regressions; it does not establish superiority across languages,
  repositories or models.
- Query expansion in the offline retrieval dataset is frozen so ranker changes
  are measured independently of model variance.
- Add new fixtures and truths before inspecting new model output whenever
  possible, and bump prompt/dataset versions when changing scoring semantics.

The recorded 2026-07-10 baseline is in
[`eval/results/2026-07-10-summary.json`](results/2026-07-10-summary.json).
