# Recorded evaluation results

The files in this directory are small, auditable baselines rather than claims
of broad model superiority. The reverse predictions retain claim text and
evidence spans. Coding-task transcripts are bounded and redacted before being
saved because an agent may print local environment variables; never commit an
unredacted temporary run.

Re-score the recorded reverse outputs:

```bash
uv run python eval/run.py reverse \
  --predictions eval/results/reverse-codex-2026-07-10.json
uv run python eval/run.py reverse \
  --predictions eval/results/reverse-claude-2026-07-10.json
uv run python eval/run.py reverse-matrix \
  --predictions eval/results/reverse-matrix-claude-2026-07-10.json
```

Reproduce retrieval locally without a model:

```bash
uv run python eval/run.py retrieval
uv run python eval/scale.py
uv run python eval/usability.py
```

Run fresh four-way coding-agent tasks (this invokes the selected local agent CLI):

```bash
uv run python eval/task_runner.py \
  --agent codex --variant all \
  --output eval/results/my-codex-run.json
```

Task success requires agent exit code 0, public tests passing, hidden contract
tests passing, and no timeout. Hidden evaluators live outside the repository
copied for the agent.
