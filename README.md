# LoreLoop

**Reverse the lore. Guide the agent. Feed proof back.**

**Lore** is the accumulated knowledge hidden across a project; **Loop** is the
cycle that reverses it, applies it to real work, and returns only evidence-backed
facts. LoreLoop makes that cycle explicit, local, and governable.

Memory tools remember what your agent did *since you installed them*. LoreLoop
extracts knowledge from what *already exists* — the codebase and the running
app — no session history required. Where memory tools often let the agent write
its own memory, LoreLoop makes the agent-writable database non-authoritative:
facts steer later runs only when the separate evidence chain backs them.

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
2. **Apply** — keep working in the Codex or Claude Code session you already
   use. The companion skill calls `loreloop begin`, which signs the task
   boundary and returns a relevant, trust-ranked context pack without
   launching a nested agent. `loreloop run` remains available for headless
   delegation to `claude -p` or `codex exec`. Established facts are marked as
   constraints; unverified references are marked as "verify before relying".
   LoreLoop never writes code itself.
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
  *current content digest*. If a chain-backed row disappears or changes with
  no recorded re-ingest provenance, delegation fails closed for operator review.
- Knowledge whose anchored source has drifted (the file changed since
  capture) is demoted to reference at injection time, automatically.
- Rejected or superseded entries stay retired even if the database is edited
  back — retirement is replayed from the chain too.

LoreLoop keeps signing material outside the project and strips operator-only
locations from the agent subprocess environment. Inference calls run in an
isolated temporary directory with tools disabled or a read-only sandbox;
delegation uses explicit non-bypass workspace permissions. This is a
cooperative process boundary, not an OS sandbox against a malicious agent
binary. Acceptance and curation remain operator CLI actions, and normal signing
APIs reject LoreLoop-launched agent subprocesses.

## Status

Early alpha. Interfaces will change. Storage, evidence, and orchestration are
local; there are no LoreLoop accounts or telemetry. **Local-first does not mean
local inference:** code snippets, page observations, and prompts sent through
Claude Code or Codex are processed under that provider's terms and settings.
Use `--no-expand` and deterministic assertions to reduce model calls, and do
not ingest material you are not authorized to send to the selected provider.

## Requirements

- [Claude Code](https://code.claude.com) (`claude`) or Codex (`codex`) CLI on your PATH
- For Runtime-only installation: `uv`, `pipx`, or Python 3.11–3.14
- [uv](https://docs.astral.sh/uv/) for source-checkout development
- Optional: Playwright for web exploration and browser-verified acceptance

## Install

### Codex plugin — recommended

Keep Codex as the only user-facing entry point:

```bash
codex plugin marketplace add zhangguiping-xydt/loreloop --ref main
codex plugin add loreloop@loreloop
```

Start a new Codex thread and invoke `$loreloop`. If the Runtime is missing,
the bundled plugin asks for explicit permission, reads the versioned wheel name
from the GitHub Release `SHA256SUMS`, verifies it, and installs it with
`uv` or `pipx`. It never executes a remotely downloaded installer script.

### GitHub Release Runtime — no PyPI required

Linux/macOS:

```bash
curl -fLO https://github.com/zhangguiping-xydt/loreloop/releases/latest/download/install-loreloop.sh
sh install-loreloop.sh --with-web
loreloop doctor
```

Windows PowerShell:

```powershell
Invoke-WebRequest https://github.com/zhangguiping-xydt/loreloop/releases/latest/download/install-loreloop.ps1 -OutFile install-loreloop.ps1
.\install-loreloop.ps1 -WithWeb
loreloop doctor
```

The release workflow publishes the universal versioned wheel plus
`SHA256SUMS`, SBOM, and GitHub provenance.
`--with-web` installs Playwright's Python package; install Chromium separately
only when web exploration or verification is needed.

### Direct GitHub install — pre-release builds

This path installs directly from a tag, branch, or commit without PyPI:

```bash
uv tool install \
  'loreloop[web] @ git+https://github.com/zhangguiping-xydt/loreloop.git@fix/codex-inference-connection'
loreloop doctor
```

### PyPI/pipx — optional ecosystem channel

After a published release:

```bash
pipx install --include-deps 'loreloop[web]'
playwright install chromium
loreloop doctor
```

### Source checkout — contributors

Do not run LoreLoop directly from an uninstalled source tree:

```bash
git clone https://github.com/zhangguiping-xydt/loreloop
cd loreloop
uv sync --frozen --all-extras
uv run --frozen playwright install chromium
uv run --frozen loreloop doctor
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
`init -> ingest -> review/approve -> run -> verify -> report -> harvest -> review/approve/supersede`.
The final output prints the exact `LORELOOP_KEY_DIR` export needed to continue
using the retained workspace. Use `--agent claude`
if preferred. A credential-free plumbing mode used by CI is also available:

```bash
loreloop demo --offline
```

Offline mode validates product plumbing, not model quality. Replay the checked-in
terminal recording with `asciinema play docs/demo.cast`.

## Use it in your project

For interactive work, keep Codex or Claude Code as the user-facing entry point:

```bash
loreloop init --skill
```

Then invoke `$loreloop` in Codex, `/loreloop` in Claude Code, or simply ask:
`Use LoreLoop to add rate limiting to the upload endpoint.` The companion skill
prepares the signed run and injects knowledge into that same conversation.
Under the hood, the current-session lifecycle is:

```bash
loreloop begin "add rate limiting to the upload endpoint"
# Codex or Claude Code implements the task in the current session.
loreloop complete <run-id> --confirm       # only after explicit operator confirmation
loreloop check <run-id> "tests pass" --command "pytest -q"
loreloop report <run-id>
loreloop harvest <run-id>                  # only after explicit operator instruction
```

`begin` is deterministic by default and does not invoke another model. A host
agent may supply additional retrieval-only terms with `--expand`; those terms
widen retrieval but never enter the context pack. `loreloop run` is the
separate-process path for automation and unattended delegation.

The complete CLI surface remains available for operators and automation:

```bash
cd your-project
loreloop doctor                          # preflight Python/Git/agent/key/locking
loreloop init                            # set up .loreloop/; offers to install the
                                         # companion skill into Claude Code/Codex
loreloop ingest --from code .            # implementation view: reverse the codebase
loreloop ingest --from web http://localhost:3000   # behavior view: explore the running app
loreloop knowledge list --status draft --channel code --limit 50
loreloop knowledge show <entry-id>       # full assertion, trust, source span and relations
loreloop knowledge review --stale        # detailed stale/draft curation queue
loreloop knowledge export --output loreloop-knowledge.md  # export knowledge as Markdown
loreloop run "add rate limiting to the upload endpoint"   # optional headless delegation
loreloop verify <run-id> http://localhost:3000/upload \
    "uploading a file larger than the limit shows an error"  # browser-verified check
loreloop verify <run-id> http://localhost:3000 \
    "contains:Cart shows 3 items" --script actions.json      # replayed flow check
loreloop report                          # acceptance report backed by the evidence chain
loreloop harvest <run-id>                # flow knowledge back from the accepted run
loreloop knowledge usage                 # injected count and accepted-run correlation
```

Code ingestion reports each extraction batch and its file count on stderr,
then reports the assertion count before that batch is classified. This keeps
long model calls visibly active without mixing progress into command stdout.

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
  `--headed`; after you finish signing in and press Enter, LoreLoop observes the
  browser's current authenticated page and continues from its links. Without
  `--headed`, login-walled pages are skipped.
- `verify` prefers deterministic assertions — `contains:`, `absent:`,
  `title-contains:` prefixes are checked directly against the page, no model
  involved. Free-form expectations fall back to an LLM judge that treats page
  content strictly as untrusted data.
- `verify --script actions.json` replays a deterministic interaction script
  before checking the final page. The v1 DSL has five actions: `goto`, `click`,
  `fill`, `select`, and `wait`. Scripts are content-addressed; harvested
  acceptance entries use `script:<sha256>` locators, so an interactive state is
  anchored by the path that reaches it, not just by the final URL.
- Script execution is fenced: HTTP(S) only, cross-origin requests and final
  URLs are rejected, password fields are never filled, destructive clicks are
  blocked, and same-origin non-GET requests require `--allow-writes`. This is
  not a transactional browser sandbox: GET endpoints can still have side
  effects in a poorly designed application, so use a disposable or staging
  environment for scripted checks.
- Every check saves the full observation as a content-addressed artifact in
  `.loreloop/evidence/artifacts/` and records its hash on the tamper-evident
  chain. Script checks additionally save the script and replay trace artifacts,
  so verdicts can be re-audited after the live page changes.
- Curation stays human: `knowledge review --stale` shows complete assertions,
  source spans, excerpts and relationships for entries whose anchor changed.
  `knowledge supersede <new-id> <old-id> --yes` links a replacement after DAG
  and active-entry validation; the old entry stays as history but is no longer
  injected. `knowledge unsupersede <new-id> <old-id> --yes` records an explicit
  recovery event. LoreLoop never auto-supersedes.
- Code ingestion prints a coverage manifest with tracked, scanned and skipped
  counts grouped by reason. Common contract files (Proto, GraphQL, JSON,
  Markdown, Dockerfile) are included by default; use `--include`, `--exclude`,
  `--max-file-bytes`, or `--strict` to make coverage policy explicit. The latest
  per-repository policy is signed onto the evidence chain and reused by
  dirty-tree checks and drift detection. Every current-session or delegated
  run pins the complete repository-policy map in signed preparation/completion
  records, so later ingestion cannot retroactively alter that run's harvest
  scope.
- Status filters and review queues use chain-replayed effective curation, never
  the agent-writable SQLite cache. `knowledge show` displays both effective and
  stored curation when they differ. Curation commands also validate transitions
  against the chain and treat SQLite updates only as projection.

Tests, linters, CLI probes and API test clients can produce re-auditable command
evidence without a shell:

```bash
loreloop check <run-id> "unit tests pass" --command "python -m pytest -q"
```

The command, exit code, repository HEAD/working-tree digest, and bounded output
are saved as a content-addressed artifact; stdout and stderr are redacted for
common secret patterns first. Exit code zero is required to pass, and harvest
rejects command evidence if the repository state has changed. Shell operators
are rejected.

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
| Claude four-way tasks | no memory 0/3; code index 0/3; session memory 3/3; LoreLoop 3/3 |
| Claude multi-language reverse matrix | Precision 0.82, Recall 0.90 across Python, TypeScript, mixed fixtures |
| Retrieval scale, 10k entries / 5 projects | median 417 ms, P95 659 ms; Recall@5 1.00, MRR 1.00 on synthetic scale fixture |
| Evidence chain / no-change harvest, 10k records | median 238 ms / 800 ms on the recorded Linux host |

These are regression baselines, not broad claims of model superiority. The
fixtures are deliberately small and all scoring rules, recorded predictions,
raw task runs and limitations are published under `eval/`. The release gate
re-scores them with `python eval/validate_results.py --check-thresholds`; the
summary deliberately omits a historical Codex task comparison because no raw
Codex task result file is checked in. The 10k retrieval
result is usable but not instant; larger corpora will need a persistent lexical
index. No zero-context human completion rate is claimed yet—the protocol is
published, and the report remains explicitly "awaiting real participants" until
real sessions are recorded.

For the product thesis behind these metrics—what Reverse / Apply / Return
actually proves today, where it does not, and the appropriate alpha release
claim—see [Product thesis and evidence](docs/product-thesis-and-evidence.md).

## Project policies

See [Contributing](CONTRIBUTING.md), [Security](SECURITY.md),
[Governance](GOVERNANCE.md), [Support](SUPPORT.md), and
[Releasing](RELEASING.md). Bugs and feature requests use structured issue
templates; vulnerabilities belong in a private GitHub Security Advisory.

## License

MIT
