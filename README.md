# LoreLoop

[English](README.md) | [简体中文](README.zh-CN.md)

Project knowledge your coding agent can actually reuse.

LoreLoop builds a local, reviewable knowledge base from an existing codebase,
a running web app, and accepted development work. You keep working in Codex,
Claude Code, OpenCode, or co-mind; LoreLoop supplies the relevant context and
keeps the evidence trail behind the scenes.

> Early alpha. The workflow works end to end, but interfaces may still change.

## Why

A new coding agent knows nothing about an old project. Session memory is empty,
the docs may be stale, and notes written by the agent itself should not quietly
become project truth.

LoreLoop adds three missing steps:

- **Reverse**: extract assertion-level knowledge from code and observed app
  behavior, with source locations and snapshots.
- **Use**: retrieve a small set of relevant facts for the task, clearly
  separating established constraints from references that still need checking.
- **Return**: record acceptance evidence and feed accepted outcomes back into
  the knowledge base.

It is not another chat UI and it does not replace your coding agent.

## Install

### Let your agent do it

Paste this into the coding agent you already use:

```text
Install and configure LoreLoop for the coding agent running this conversation.

Read the Install section in this README and follow it instead of only summarizing it:
https://github.com/zhangguiping-xydt/loreloop/blob/main/README.md

Detect the current host, install LoreLoop with the matching host option, then run
loreloop doctor and the matching host status command.

Do not ask me to install a separate execution component. Do not edit .loreloop,
host configuration, or marketplace files directly. Do not run trust reset,
complete, harvest, or knowledge curation as part of installation.
```

### From a GitHub Release

Download the installer instead of piping it directly into a shell.

Linux/macOS:

```bash
curl -fLO https://github.com/zhangguiping-xydt/loreloop/releases/latest/download/install-loreloop.sh

# Pick the host for the current session:
sh install-loreloop.sh --codex
sh install-loreloop.sh --claude
sh install-loreloop.sh --opencode
sh install-loreloop.sh --comind
```

Windows PowerShell:

```powershell
Invoke-WebRequest https://github.com/zhangguiping-xydt/loreloop/releases/latest/download/install-loreloop.ps1 -OutFile install-loreloop.ps1

# Pick the host for the current session:
.\install-loreloop.ps1 -Codex
.\install-loreloop.ps1 -Claude
.\install-loreloop.ps1 -OpenCode
.\install-loreloop.ps1 -CoMind
```

Add `--with-web` or `-WithWeb` only if you need browser exploration and
browser-backed acceptance checks.

### Before the first Release

Until a GitHub Release exists, install the current branch directly:

```bash
uv tool install --force \
  'loreloop[web] @ git+https://github.com/zhangguiping-xydt/loreloop.git@main'
```

Then connect one host:

```bash
loreloop codex install --source zhangguiping-xydt/loreloop --ref main
loreloop claude install --source zhangguiping-xydt/loreloop
loreloop opencode install
loreloop comind install --source zhangguiping-xydt/loreloop
```

Remove `[web]` if browser features are not needed. Do not use this mutable
source path to work around a checksum failure on an existing Release.

When developing from a local checkout, install that checkout explicitly so an
older global tool does not shadow the code you are testing:

```bash
uv tool install --force --editable '/absolute/path/to/loreloop[web]'
```

Run `type -a loreloop` and `loreloop ingest --help` if the available agents or
options do not match the checkout.

### Host entry points

| Host | After installation |
|---|---|
| Codex | Start a new thread and invoke `$loreloop`, or ask naturally |
| Claude Code | Start a new session and ask it to use LoreLoop |
| OpenCode | Start a new session and run `/loreloop <request>` |
| co-mind | Start a new session and ask it to use LoreLoop |

Check installation with:

```bash
loreloop doctor
loreloop codex status      # or claude / opencode / comind
```

## First project

Initialize LoreLoop in the repository:

```bash
cd your-project
loreloop init --skill
```

The same command is also the upgrade path for project-level companion Skills.
It is idempotent: rerunning it keeps the trust domain and project files intact
while refreshing managed `.claude/skills` and `.agents/skills` copies from the
currently installed LoreLoop runtime. Host integrations do this once at the
first LoreLoop action in each new session, so older projects do not retain an
obsolete export workflow.

Build the first knowledge baseline from code:

```bash
loreloop ingest --from code .
loreloop knowledge review
```

Generate a directly readable authoritative baseline from clean committed Git
snapshots for onboarding or requirement development. By default this path does
not call an agent or open the SQLite store or a key. A non-Git workspace root is
supported when its member repositories have been declared with
`loreloop repo add`:

```bash
loreloop knowledge export \
  --format docs \
  --output baseline \
  --project-name your-project \
  --requirements docs/requirements.md
```

These are reviewable Markdown files, not Word/DOCX documents. Markdown is the
human view: capability domains, repository boundaries, implementation layers,
UI entry points, tests, interfaces, and schema are grouped for reading instead
of emitted as an atomic-record dump. The Capsule JSON is the complete
machine-verifiable representation used by replay and retrieval.

The readable directory layout is:

```text
baseline/
├── your-project-功能清单.md
├── your-project-需求规格.md
├── your-project-系统架构.md
├── your-project-详细设计.md
├── your-project-用户手册.md
├── your-project-验收规格.md
├── your-project-接口契约.md      # only with explicit interface evidence
├── your-project-数据库设计.md    # only with explicit schema evidence
└── .loreloop-export.json         # SemanticCore, pre-AST digests, and Markdown digests
```

Six core documents are always produced. Interface and database documents are
added only when the source supports them. Deterministic detectors currently
cover Python, TypeScript/JavaScript, Vue SFCs, Java/Kotlin, Go, Rust, C#, legacy
.NET project/build metadata, WinForms/ASP.NET code-behind, ASMX WebMethods, SQL,
SQLAlchemy, Django ORM, Prisma, TypeORM, common migrations, OpenAPI/Swagger,
GraphQL, protobuf, Docker, Compose, and Kubernetes. The CLI prints repository,
detector, fact, document, and unsupported-suffix coverage.
Tests in supported languages project only into acceptance evidence; they do not
become capability or implementation-design facts.
The normalized `--project-name` is part of the SemanticCore identity, so names,
ASTs, Markdown, and package IDs are one deterministic projection rather than
independent labels.

New exports use the evidence-backed split-view Capsule v5 layout. The Markdown
files are a separate human semantic view: deployable units, implemented
capabilities, triggers, roles, data reads/writes, UI areas, acceptance
candidates, contracts, gaps, and evidence links.
The Capsule SemanticCore is the separate Agent view: exact atomic records,
identities, values, and source bindings used for retrieval. Both projections
come from the same SemanticCore; replay verifies every human Markdown digest,
the deterministic pre-AST digests, and the complete Agent record set. LoreLoop
still replays v4 packages with the earlier split-view renderer and v2/v3
packages whose Agent inventory was embedded in human Markdown appendices.

The Capsule can prove the package closure on a machine with no source, database,
or key:

```bash
loreloop knowledge replay baseline
```

Search the verified package directly without extraction or importing it into a
project store. Retrieval ranks the replay-verified SemanticCore Agent view and
maps every hit back to its owning human document family and source evidence:

```bash
loreloop knowledge search "fund ratio" --package baseline
```

The transient BM25 index groups visible paragraphs, lists, and table blocks
within their Markdown sections instead of treating every line as an isolated
fact. Snippets select the matching visible fact, Mermaid syntax is excluded,
and the default path needs no model, vector store, or database.

When the question and the project use different wording, pass bounded
retrieval-only synonyms, translations, abbreviations, or likely identifiers:

```bash
loreloop knowledge search "fund ratio" \
  --package baseline \
  --expand "housing provident fund contribution ratio HPF hpfRatioConfig"
```

Expansion remains a lower-weight candidate hint. When every result depends on
it, the CLI labels the result set as low confidence so the host agent knows to
verify the matched document or source rather than treating the expansion as
project knowledge.

Create a compressed handoff artifact only when one is needed:

```bash
loreloop knowledge export --format package --output baseline.zip
loreloop knowledge replay baseline.zip
```

By default, project documents come from clean commits. During active
development, export the exact current files without committing or changing the
real Git index:

```bash
loreloop knowledge export --format docs --output baseline --working-tree
```

The working-tree baseline includes staged, unstaged, and untracked non-ignored
files. It is bound to the current HEAD plus a separate source tree and is
clearly labeled in every generated document; it does not claim to be a
committed release state. A strict-mode error lists the files that made the
repository dirty and points to this option.

Supported text sources are decoded as strict UTF-8 first, with a deterministic
GB18030 fallback that also covers GBK-compatible files. Predominantly UTF-8
legacy files with bounded byte damage may use an explicit repaired projection;
facts anchored to damaged lines are discarded and the human detailed design
records the coverage gap. Files that cannot be recovered safely are bound as
source gaps rather than inferred from. LoreLoop always preserves and hashes the
original repository bytes, so baseline export never requires bulk transcoding.

Expansion changes ranking only. It is never added to the baseline, rendered as
project knowledge, or allowed to raise the trust of a result. Codex, Claude
Code, OpenCode, and co-mind can derive these terms in the current host session;
LoreLoop does not launch a second agent for package search.

Web exploration remains in the live knowledge store by default. To project
runtime observations back into the deliverable baseline, first approve and
browser-verify the entries, then opt in explicitly:

```bash
loreloop ingest --from web https://app.example.com --headed

# Turn captured pages into reviewable, replayable test candidates.
loreloop web test generate
loreloop web test review
loreloop web test approve <scenario-id>
git add tests/loreloop/web/<scenario-id>.json
git commit -m "add governed web test"

# Replay the exact chain-approved file locally or in CI.
loreloop web test run --all
loreloop web test coverage --format markdown --output web-coverage.md
loreloop web test export --format playwright --output tests/playwright/generated

# Govern extracted Web knowledge separately.
loreloop knowledge review --status draft
loreloop knowledge approve <entry-id>
loreloop knowledge verify <entry-id> --headed
loreloop knowledge export \
  --format docs \
  --output baseline \
  --include-web \
  --force
loreloop knowledge replay baseline
```

Files under `.loreloop/web-tests/candidates/` are private, untrusted review
material. Approval publishes the exact scenario to `tests/loreloop/web/` and
binds its digest to the evidence chain; only that matching file can run.
Generated Playwright specs and coverage reports are derivative output, not the
authority. Scenarios are read-only by default. Recording or replaying a write-risk journey requires
an explicit `--allow-writes`, and password/token/secret/API-key fields are not
recorded. Each replay stores an evidence-backed page state after every successful
step, plus its trace, final observation, assertions, and result on the chain.
`web test coverage` separates observed-only controls, exercised controls, and
write-gated controls; it never treats a visible button as tested, and only a
current scenario digest backed by a chain approval is reported as approved. With
`--include-web`, the latest governed result also appears as an acceptance fact
in the package.

For normal bug fixes and feature work, users stay in Codex, Claude Code,
OpenCode, or co-mind and describe the task in natural language. The companion
workflow runs `begin`, selects impacted tests from the task-start snapshot,
executes provisional self-tests, trials safe Web candidates, and folds the
results and coverage gaps into `report`. The commands remain available for
inspection and CI, but ordinary users do not need to orchestrate them:

```bash
loreloop test select <run-id> --format markdown --output task-test-plan.md
loreloop test run <run-id>
loreloop task summarize <run-id> \
  --analysis "root cause or requirement interpretation" \
  --implementation "implemented changes"
loreloop complete <run-id> --confirm
loreloop test prove <run-id>
loreloop report <run-id>
```

In a non-Git aggregate workspace with more than one declared repository, use
`loreloop web test approve <scenario-id> --repo <repo-name>`; the approved JSON
is written into that repository so its committed snapshot remains authoritative.

`--include-web` reads the local projection and tamper-evident chain. Draft,
approval-only, verification-only, contradicted, rejected, superseded, or
digest-mismatched Web entries are excluded. Eligible observations project into
their requirement, architecture, capability, user-guide, interface, or
acceptance sections and remain bound by the Capsule.

For an optional local trust-chain assertion, export with `--attest` and replay
with `--trusted`:

```bash
loreloop knowledge export --format package --output baseline.zip --attest
loreloop knowledge replay baseline.zip --trusted
```

`--format docs` defaults to the readable `baseline/` directory. Package mode
(`--format package`) defaults to the compressed `baseline.zip` transport. The
default `--format audit` remains the separate single-file, entry-by-entry trust export;
it is not the project document package.

When a requirement document is ready, commit it to any declared repository and
prepare the task in the coding-agent session you already use:

```bash
loreloop begin "implement upload rate limiting from the requirement" \
  --requirements docs/upload-rate-limit.md
```

Use `repo:frontend/docs/requirements.md` for a peer repository. LoreLoop reads
the exact requirement blob from `HEAD` and returns its commit, SHA-256, content,
and relevant governed knowledge to the current Codex, Claude Code, OpenCode, or
co-mind conversation. It does not replace the chat entry point or launch a
nested agent.

When the work is ready, LoreLoop can record deterministic checks and render an
acceptance report:

```bash
loreloop check <run-id> "tests pass" --command "pytest -q"
loreloop report <run-id>
```

Completion, harvest, and knowledge curation remain explicit operator actions.

## What gets stored

Each knowledge entry is a small assertion with:

- its source: code span, Git commit, URL, or page snapshot;
- its review state: draft, approved, or rejected;
- its verification state: unverified, verified, or contradicted;
- drift information when the source changes.

SQLite is only the local projection. Trust-raising actions are replayed from a
tamper-evident evidence chain whose credential lives outside the project tree.
An agent cannot make its own note authoritative by editing the database.

## How it differs

| Tool | Good at | What LoreLoop adds |
|---|---|---|
| Session memory | Remembering recent conversations and preferences | A baseline from the project that existed before the agent arrived |
| Code search / RAG | Finding files and snippets | Assertion-level knowledge with provenance, drift, and review state |
| Coding-agent wrappers | Running models and tools | Evidence-backed acceptance without trusting the agent's self-report |
| Team documentation | Human explanation and decisions | Searchable facts that can be verified, retired, and reused |

LoreLoop is meant to sit beside these tools, not replace them.

## Supported workflows

- Code ingestion across one or more Git repositories
- Six core plus two evidence-driven project documents, no-key Capsule replay,
  and optional local attestation
- Deterministic ORM, contract, container-platform, and multi-language detection
- Same-origin web exploration with optional human login handover
- Current-session use through Codex, Claude Code, OpenCode, and co-mind
- Deterministic command checks and browser-backed acceptance
- Knowledge review, rejection, reopening, supersession, and usage reporting
- Read-only federation across separately trusted projects

OpenCode is supported as an interactive host and for tool-free inference.
Headless `loreloop run --agent opencode` is disabled because its CLI does not
currently expose a verifiable workspace sandbox.

## Evidence, not slogans

The checked-in `eval/` suite measures extraction, retrieval, executable coding
tasks, and scale. The current small baselines include:

- Codex code extraction: precision 1.00 / recall 1.00 on 14 fixed truths
- Claude multi-language extraction: precision 0.82 / recall 0.90
- Frozen query expansion: Hit@5 1.00 / MRR 1.00 on the fixed retrieval set
- LoreLoop task variant: 3/3 on the checked-in Claude task fixture

These are regression fixtures, not claims of general superiority. Raw inputs,
scoring code, limitations, and the still-unfinished real-participant usability
study are all published.

- [Evaluation suite](eval/)
- [Product thesis and evidence](docs/product-thesis-and-evidence.md)
- [Design and implementation](docs/design-and-implementation.md)
- [Security model](SECURITY.md)
- [Troubleshooting](docs/troubleshooting.md)

## Development

```bash
git clone https://github.com/zhangguiping-xydt/loreloop
cd loreloop
uv sync --frozen --all-extras
uv run --frozen pytest -q
```

See [CONTRIBUTING.md](CONTRIBUTING.md) and [RELEASING.md](RELEASING.md).

## License

MIT
