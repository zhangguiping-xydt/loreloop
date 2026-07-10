# LoreLoop evaluation suite

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

# Python / TypeScript / mixed-language reverse quality and cost
uv run python eval/run.py reverse-matrix --agent claude \
  --output /tmp/reverse-matrix.json

# Re-score the checked-in matrix without calling an agent
uv run python eval/run.py reverse-matrix \
  --predictions eval/results/reverse-matrix-claude-2026-07-10.json

# Real coding-agent four-way task comparison
uv run python eval/task_runner.py \
  --agent codex --variant all \
  --output /tmp/codex-task-results.json

# 100 / 1k / 10k retrieval, evidence-chain, and harvest scale
uv run python eval/scale.py --output /tmp/scale.json

# Validate and aggregate real zero-context participant records
uv run python eval/usability.py
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

All task variants receive the same repository, task, and hidden evaluator:

- `no_memory`: no external project fact;
- `session_memory`: the same critical fact as an ephemeral, unverified note;
- `codebase_index`: bounded source snippets only, with no external policy fact;
- `loreloop`: the fact rendered as chain-governable established knowledge.

This separates the value of simply possessing a fact from persistence,
provenance, retrieval, and trust governance. Hidden evaluators remain outside
the repository copied for the agent.

The scale fixture is deterministic and synthetic. It measures algorithmic
behavior and operating limits across five logical projects, not real-world
relevance diversity. Prompt-token cost is explicitly an estimate of rendered
characters divided by four; vendor-billed tokens require vendor telemetry.

## Safety and limitations

- Task transcripts are redacted and truncated before saving. Recorded public
  baselines omit transcripts because agents may inspect local environment variables.
- The committed truth sets and coding tasks remain intentionally small. The
  multi-language matrix and 10k scale run widen coverage but do not establish
  industry-wide superiority across repositories or models.
- Query expansion in the offline retrieval dataset is frozen so ranker changes
  are measured independently of model variance.
- Add new fixtures and truths before inspecting new model output whenever
  possible, and bump prompt/dataset versions when changing scoring semantics.

The recorded 2026-07-10 baseline is in
[`eval/results/2026-07-10-summary.json`](results/2026-07-10-summary.json).
Additional raw results include the
[`multi-language reverse matrix`](results/reverse-matrix-claude-2026-07-10.json)
and [`scale benchmark`](results/scale-2026-07-10.json). The usability summary
reports `awaiting real participants` until actual uncoached session JSON exists.
