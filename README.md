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

Build the first knowledge baseline from code:

```bash
loreloop ingest --from code .
loreloop knowledge review
```

Then stay in your coding-agent session and ask:

```text
Use LoreLoop to add rate limiting to the upload endpoint.
```

The host prepares the task with `loreloop begin`, reads the returned context
pack, and continues the implementation in the same conversation.

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
