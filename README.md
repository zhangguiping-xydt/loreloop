# LoreLoop

**Reverse the lore. Guide the agent. Feed proof back.**

**Lore** is the accumulated knowledge hidden across a project; **Loop** is the
cycle that reverses it, applies it to real work, and returns only evidence-backed
facts. LoreLoop makes that cycle explicit, local, and governable.

Memory tools remember what your agent did *since you installed them*. LoreLoop
extracts knowledge from what *already exists* — the codebase and the running
app — no session history required. And where memory tools let the agent write
its own memory, LoreLoop puts a trust gate on everything that enters the
knowledge base: an agent cannot pollute the facts that steer its next run.

## The problem

You inherit a project no agent has ever touched: five years of code, a running
app, documentation that stopped being true two refactors ago. Session-memory
tools have nothing to capture — there is no session history. So the agent
starts from zero, and everything it "learns" is whatever it told itself while
working: unreviewed, unverified, and injected into the next run as if it were
fact. Stale memory and self-graded homework compound each other.

LoreLoop is a local-first CLI that closes the loop with a gate at both ends:

1. **Ingest (cold start)** — reverse-engineer knowledge from what already
   exists: the codebase (implementation view) and the running web app
   (behavior view). Day one on a legacy project, you get assertion-level
   facts with structured provenance — not chunks, not summaries.
2. **Run** — delegate a task to `claude -p` or `codex exec` with a context
   pack of relevant, trust-ranked knowledge injected up front. Established
   facts are marked as constraints; unverified references are marked as
   "verify before relying". LoreLoop never writes code itself.
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
written and entered by a human, verdicts come from `loreloop report` reading
the evidence chain, and approve/reject/supersede are human acts. The
companion skill (installed into Claude Code/Codex by `loreloop init`) only makes
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

## Install

Use one of these two supported paths; do not run LoreLoop directly from an
uninstalled source tree.

Source checkout (contributors and unreleased builds):

```bash
git clone https://github.com/loreloop-ai/loreloop
cd loreloop
python -m venv .venv
# POSIX: source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e '.[web]'
python -m playwright install chromium
loreloop doctor
```

Published release (after the first PyPI release):

```bash
pipx install --include-deps 'loreloop[web]'
playwright install chromium
loreloop doctor
```

### Pre-release rename

LoreLoop is a clean break from the former pre-release name. It does not read
`.knowhelm/` or `KNOWHELM_*`. If you used an earlier checkout, archive that
local state and run `loreloop init` to create a fresh LoreLoop trust domain.

## Five-minute quick start

The bundled legacy application is the shortest complete learning path. From a
source checkout with one coding-agent CLI configured:

```bash
loreloop demo --agent codex
```

It creates a disposable Git repository and visibly runs
`init -> ingest -> run -> verify -> report -> harvest`. Use `--agent claude`
if preferred. A credential-free plumbing mode used by CI is also available:

```bash
loreloop demo --offline
```

Offline mode validates product plumbing, not model quality. Replay the checked-in
terminal recording with `asciinema play docs/demo.cast`.

## Use it in your project

The full workflow is intentionally one CLI:

```bash
cd your-project
loreloop doctor                          # preflight Python/Git/agent/key/locking
loreloop init                            # set up .loreloop/; offers to install the
                                         # companion skill into Claude Code/Codex
loreloop ingest --from code .            # implementation view: reverse the codebase
loreloop ingest --from web http://localhost:3000   # behavior view: explore the running app
loreloop knowledge list                  # inspect entries; approve/reject to curate
loreloop knowledge export --output loreloop-knowledge.md  # export knowledge as Markdown
loreloop run "add rate limiting to the upload endpoint"   # delegate with injected knowledge
loreloop verify <run-id> http://localhost:3000/upload \
    "uploading a file larger than the limit shows an error"  # browser-verified check
loreloop verify <run-id> http://localhost:3000 \
    "contains:Cart shows 3 items" --script actions.json      # replayed flow check
loreloop report                          # acceptance report backed by the evidence chain
loreloop harvest <run-id>                # flow knowledge back from the accepted run
loreloop knowledge usage                 # injected count and accepted-run correlation
```

If a step fails, the CLI prints exactly one `error`, its `reason`, and the next
recovery action. See [`docs/troubleshooting.md`](docs/troubleshooting.md) for
agent, browser, evidence-key, schema-upgrade, interrupted-run, and harvest help.

## Architecture and trust boundary

| Stage | What happens | Trust outcome |
|---|---|---|
| Reverse | Code and live behavior become atomic assertions with source spans/snapshots | LLM output is draft and unverified |
| Apply | BM25 + bounded query expansion selects a small trust-ranked context pack | Approved/verified facts constrain; drafts are references |
| Return | Browser/command evidence is chained, reported, then harvested | Only accepted, auditable outcomes mint verified knowledge |

SQLite is the local projection; the out-of-tree HMAC key and evidence chain are
the trust authority. Federation opens foreign SQLite databases read-only and
imports nothing until the operator explicitly copies an entry as a local draft.
The detailed design is in
[`docs/design-and-implementation.md`](docs/design-and-implementation.md).

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
  `.loreloop/evidence/artifacts/` and records its hash on the tamper-evident
  chain. Script checks additionally save the script and replay trace artifacts,
  so verdicts can be re-audited after the live page changes.
- Curation stays human: `knowledge list --stale` shows entries whose anchored
  source changed since capture; `knowledge supersede <new-id> <old-id>` links
  a replacement — the old entry stays in the store as history but is no
  longer injected into runs. LoreLoop never auto-supersedes.

Tests, linters, CLI probes and API test clients can produce re-auditable command
evidence without a shell:

```bash
loreloop check <run-id> "unit tests pass" --command "python -m pytest -q"
```

The command, exit code and bounded output are saved as a content-addressed
artifact. Exit code zero is required to pass; successful command evidence can
flow back as verified acceptance knowledge. Shell operators are rejected.

## Multi-repository and federation

A trust domain may include additional Git repositories, while federation reads
other trust domains without adopting their facts automatically:

```bash
loreloop repo add ../backend --name backend
loreloop repo list

loreloop project add . --id storefront --tag commerce
loreloop knowledge search "upload policy" --all
loreloop run --with-related "update the upload policy"
loreloop knowledge import storefront <entry-id-prefix>
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
| Codex coding tasks, LoreLoop context | 3/3 hidden contracts passed |
| Claude four-way tasks | no memory 0/3; code index 0/3; session memory 3/3; LoreLoop 3/3 |
| Claude multi-language reverse matrix | Precision 0.82, Recall 0.90 across Python, TypeScript, mixed fixtures |
| Retrieval scale, 10k entries / 5 projects | median 417 ms, P95 659 ms; Recall@5 1.00, MRR 1.00 on synthetic scale fixture |
| Evidence chain / no-change harvest, 10k records | median 238 ms / 800 ms on the recorded Linux host |

These are regression baselines, not broad claims of model superiority. The
fixtures are deliberately small and all scoring rules, recorded predictions,
diff outcomes and limitations are published under `eval/`. The 10k retrieval
result is usable but not instant; larger corpora will need a persistent lexical
index. No zero-context human completion rate is claimed yet—the protocol is
published, and the report remains explicitly "awaiting real participants" until
real sessions are recorded.

## License

MIT
