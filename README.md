# starry-knowhelm

**Steer coding agents with governed knowledge. Verify their work with evidence.**

Coding agents like Claude Code and Codex are excellent at writing code — but they start
every session knowing nothing about your project beyond what fits in a prompt, and they
grade their own homework. knowhelm sits above the coding agent as a local-first cockpit:

1. **Ingest** — reverse-engineer knowledge from what already exists: your codebase
   (implementation view) and your running web app (behavior view). Every knowledge entry
   carries a confidence status (`confirmed` / `inferred` / `contradicted`), not just text.
2. **Run** — delegate a task to `claude -p` or `codex exec`, with a context pack of
   relevant, trust-ranked knowledge injected up front. knowhelm never writes code itself;
   it governs what the coding agent knows and records what it does.
3. **Report** — verify the result in a real browser and produce an acceptance report
   backed by a tamper-evident (HMAC-chained) evidence trail. Not the agent's claim that
   it worked — proof that it did.

## Why not just another agent orchestrator?

Most orchestration tools focus on running more agents in parallel. knowhelm focuses on
the two ends the agents don't cover:

- **Upstream:** project knowledge as a governed corpus — confidence scoring, contradiction
  detection, decay — instead of a pile of markdown.
- **Downstream:** acceptance as evidence — browser-verified checks chained with HMAC —
  instead of "the tests passed, trust me."

## Status

Early alpha. Interfaces will change. Local-first: everything runs on your machine,
storage is SQLite, no accounts, no telemetry.

## Requirements

- Python 3.11+
- [Claude Code](https://code.claude.com) (`claude`) or Codex (`codex`) CLI on your PATH
- Optional: Playwright (`pip install knowhelm[web]`) for web exploration and
  browser-verified acceptance

## Quick start

```bash
pip install 'knowhelm[web]' && playwright install chromium

cd your-project
knowhelm ingest --from code .            # implementation view: reverse the codebase
knowhelm ingest --from web http://localhost:3000   # behavior view: explore the running app
knowhelm knowledge list                  # inspect entries; approve/reject to curate
knowhelm run "add rate limiting to the upload endpoint"   # delegate with injected knowledge
knowhelm verify <run-id> http://localhost:3000/upload \
    "uploading a file larger than the limit shows an error"  # browser-verified check
knowhelm report                          # acceptance report backed by the evidence chain
```

Notes:
- `--from web` explores same-origin pages only. When it hits a login form it
  hands the browser to you (`--headed`) instead of automating credentials.
- `verify` records what the browser actually observed — verdict, reasoning,
  and page snapshot hash — on the tamper-evident chain.

## License

MIT
