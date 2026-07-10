# Recorded evaluation results

The files in this directory are small, auditable baselines rather than claims
of broad model superiority. The reverse predictions retain claim text and
evidence spans; long excerpts and model transcripts are omitted. Coding-task
transcripts are not committed because an agent may print local environment
variables. The runner redacts secret-shaped values before saving future runs.

Re-score the recorded reverse outputs:

```bash
uv run python eval/run.py reverse \
  --predictions eval/results/reverse-codex-2026-07-10.json
uv run python eval/run.py reverse \
  --predictions eval/results/reverse-claude-2026-07-10.json
```

Reproduce retrieval locally without a model:

```bash
uv run python eval/run.py retrieval
```

Run fresh coding-agent A/B tasks (this invokes the selected local agent CLI):

```bash
uv run python eval/task_runner.py \
  --agent codex \
  --output eval/results/my-codex-run.json
```

Task success requires agent exit code 0, public tests passing, hidden contract
tests passing, and no timeout. Hidden evaluators live outside the repository
copied for the agent.
