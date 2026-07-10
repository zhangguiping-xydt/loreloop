# starry-knowhelm

**Turn an existing project into governed knowledge. Steer coding agents with it.
Let only evidence-backed facts flow back in.**

Memory tools remember what your agent did *since you installed them*. knowhelm
extracts knowledge from what *already exists* — the codebase and the running
app — no session history required. And where memory tools let the agent write
its own memory, knowhelm puts a trust gate on everything that enters the
knowledge base: an agent cannot pollute the facts that steer its next run.

## The problem

You inherit a project no agent has ever touched: five years of code, a running
app, documentation that stopped being true two refactors ago. Session-memory
tools have nothing to capture — there is no session history. So the agent
starts from zero, and everything it "learns" is whatever it told itself while
working: unreviewed, unverified, and injected into the next run as if it were
fact. Stale memory and self-graded homework compound each other.

knowhelm is a local-first CLI that closes the loop with a gate at both ends:

1. **Ingest (cold start)** — reverse-engineer knowledge from what already
   exists: the codebase (implementation view) and the running web app
   (behavior view). Day one on a legacy project, you get assertion-level
   facts with structured provenance — not chunks, not summaries.
2. **Run** — delegate a task to `claude -p` or `codex exec` with a context
   pack of relevant, trust-ranked knowledge injected up front. Established
   facts are marked as constraints; unverified references are marked as
   "verify before relying". knowhelm never writes code itself.
3. **Report & harvest (trust gate)** — verify the result in a real browser,
   record every check on a tamper-evident (HMAC-chained) evidence trail, and
   only then let knowledge flow back. Human-written, browser-verified
   acceptance assertions are born verified. LLM re-extractions are born draft
   and earn trust the normal way. The agent's own claims mint nothing.

## What makes the loop different

Every entry carries two explicit trust axes — human curation
(`draft`/`approved`/`rejected`) and machine verification
(`unverified`/`verified`/`contradicted`) — and the SQLite store is treated as
a display cache, not an authority. Real trust is replayed from the evidence
chain, whose signing key lives outside the project tree. Concretely:

- An agent that edits the store to mark its own output "approved" gains
  nothing: strong status counts only when the chain endorses the entry's
  *current content digest*.
- Knowledge whose anchored source has drifted (the file changed since
  capture) is demoted to reference at injection time, automatically.
- Rejected or superseded entries stay retired even if the database is edited
  back — retirement is replayed from the chain too.

The referee stays outside the player's process: acceptance assertions are
written and entered by a human, verdicts come from `knowhelm report` reading
the evidence chain, and approve/reject/supersede are human acts. The
companion skill (installed into Claude Code by `knowhelm init`) only makes
the agent a better citizen — it never verifies, never renders verdicts,
never writes knowledge.

## Status

Early alpha. Interfaces will change. Local-first: everything runs on your
machine, storage is SQLite, no accounts, no telemetry. One CLI is the whole
user surface — no web dashboard, no server.

## Requirements

- Python 3.11+
- [Claude Code](https://code.claude.com) (`claude`) or Codex (`codex`) CLI on your PATH
- Optional: Playwright for web exploration and browser-verified acceptance

## Quick start

Install from source (no PyPI release yet):

```bash
git clone https://github.com/starry-knowhelm/starry-knowhelm
pip install -e './starry-knowhelm[web]' && playwright install chromium

cd your-project
knowhelm doctor                          # preflight Python/Git/agent/key/locking
knowhelm init                            # set up .knowhelm/; offers to install the
                                         # companion skill into Claude Code/Codex
knowhelm ingest --from code .            # implementation view: reverse the codebase
knowhelm ingest --from web http://localhost:3000   # behavior view: explore the running app
knowhelm knowledge list                  # inspect entries; approve/reject to curate
knowhelm knowledge export --output knowhelm-knowledge.md  # export knowledge as Markdown
knowhelm run "add rate limiting to the upload endpoint"   # delegate with injected knowledge
knowhelm verify <run-id> http://localhost:3000/upload \
    "uploading a file larger than the limit shows an error"  # browser-verified check
knowhelm verify <run-id> http://localhost:3000 \
    "contains:Cart shows 3 items" --script actions.json      # replayed flow check
knowhelm report                          # acceptance report backed by the evidence chain
knowhelm harvest <run-id>                # flow knowledge back from the accepted run
knowhelm knowledge usage                 # injected count and accepted-run correlation
```

Notes:
- Retrieval for `run` is deterministic BM25 over ASCII terms and CJK bigrams,
  plus LLM query expansion through your existing agent CLI (bilingual
  synonyms and likely identifiers). Expansion terms only widen retrieval —
  they never enter the delegation prompt — and are recorded in the run trace
  for audit. `--no-expand` skips it. Embeddings are a deliberate non-feature
  at this stage: retrieval stays explainable and testable.
- `--from web` explores same-origin pages only. It thickens browser observation
  with network-idle settling, lazy-scroll, semantic links, headings, buttons,
  nav text and forms, and expands seeds from same-origin links, sitemap/robots
  and static route strings found in code. HTTP 4xx/5xx pages are skipped during
  exploration. When it hits a login form it hands the browser to you with
  `--headed`; without `--headed` login-walled pages are skipped.
- `verify` prefers deterministic assertions — `contains:`, `absent:`,
  `title-contains:` prefixes are checked directly against the page, no model
  involved. Free-form expectations fall back to an LLM judge that treats page
  content strictly as untrusted data.
- `verify --script actions.json` replays a deterministic interaction script
  before checking the final page. The v1 DSL has five actions: `goto`, `click`,
  `fill`, `select`, and `wait`. Scripts are content-addressed; harvested
  acceptance entries use `script:<sha256>` locators, so an interactive state is
  anchored by the path that reaches it, not just by the final URL.
- Script execution is fenced: same-origin only, password fields are never
  filled, destructive clicks are blocked, and write-like form actions require
  `--allow-writes`.
- Every check saves the full observation as a content-addressed artifact in
  `.knowhelm/evidence/artifacts/` and records its hash on the tamper-evident
  chain. Script checks additionally save the script and replay trace artifacts,
  so verdicts can be re-audited after the live page changes.
- Curation stays human: `knowledge list --stale` shows entries whose anchored
  source changed since capture; `knowledge supersede <new-id> <old-id>` links
  a replacement — the old entry stays in the store as history but is no
  longer injected into runs. knowhelm never auto-supersedes.

Tests, linters, CLI probes and API test clients can produce re-auditable command
evidence without a shell:

```bash
knowhelm check <run-id> "unit tests pass" --command "python -m pytest -q"
```

The command, exit code and bounded output are saved as a content-addressed
artifact. Exit code zero is required to pass; successful command evidence can
flow back as verified acceptance knowledge. Shell operators are rejected.

## Multi-repository and federation

A trust domain may include additional Git repositories, while federation reads
other trust domains without adopting their facts automatically:

```bash
knowhelm repo add ../backend --name backend
knowhelm repo list

knowhelm project add . --id storefront --tag commerce
knowhelm knowledge search "upload policy" --all
knowhelm run --with-related "update the upload policy"
knowhelm knowledge import storefront <entry-id-prefix>
```

Related-project entries are rendered in a separate non-constraint section.
Importing is an explicit operator action and creates a local draft. See
[`docs/multi-repo-and-federation.md`](docs/multi-repo-and-federation.md).

## Reproducible evaluation

The public [`eval/`](eval/) suite measures reverse Precision/Recall, retrieval
Precision@K/Recall@K/MRR, and executable coding-task success. The recorded
2026-07-10 small baseline is:

| Benchmark | Result |
|---|---|
| Codex reverse | Precision 1.00, Recall 1.00 on 14 fixed truths |
| Claude reverse | Precision 1.00, Recall 0.71; compound claims lose atomic credit |
| Retrieval, plain BM25 | Hit@5 0.50, MRR 0.42 |
| Retrieval, frozen expansion | Hit@5 1.00, MRR 1.00, 6 relevant / 10 returned |
| Codex coding tasks, no knowledge | 0/3 hidden contracts passed |
| Codex coding tasks, knowhelm context | 3/3 hidden contracts passed |

These are regression baselines, not broad claims of model superiority. The
fixtures are deliberately small and all scoring rules, recorded predictions,
diff outcomes and limitations are published under `eval/`.

## License

MIT
