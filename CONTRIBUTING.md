# Contributing to LoreLoop

Thanks for your interest. LoreLoop is early alpha — small, focused
contributions are the most useful kind right now.

## Setup

```bash
git clone https://github.com/loreloop-ai/loreloop
cd loreloop
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'          # add ,web for playwright-backed features
pytest -q && ruff check .
```

The suite is hermetic: no network, no real LLM calls (agents are faked), no
real browser except `tests/test_smoke_playwright.py`, which auto-skips when
playwright is absent.

## Design contract

These are load-bearing decisions, not conventions. PRs that regress them
will be declined:

1. **Fix causes, not symptoms.** No keyword filters, regex fallbacks, or
   silent except-blocks papering over a failure downstream. Find the
   upstream cause.
2. **Never trust LLM output format.** Extraction and classification are
   separate steps. Model output must pass JSON, source-path, line, symbol, and
   verbatim-excerpt validation. One repair call may retry the same strict
   contract; a second failure rejects the batch (`ExtractionError`). Validation
   messages are untrusted data too. No inline markers, no "the model will comply".
3. **Trust is two explicit axes.** Human curation (draft/approved/rejected)
   and machine verification (unverified/verified/contradicted). Nothing is
   born strong unless a human wrote it and a machine checked it against
   reality — LLM-derived entries always start as drafts.
4. **Evidence over claims.** Acceptance verdicts rest on the HMAC evidence
   chain plus content-addressed artifacts. A check that cannot be re-audited
   (missing/tampered artifact) never mints knowledge and degrades the verdict.
5. **Freshness is anchored, never stored.** Entries anchor to a commit sha or
   page-content hash; staleness is judged at read time by diffing against the
   anchor. No expiry timestamps.
6. **Page content is untrusted input.** Deterministic assertions
   (`contains:` etc.) are preferred; the LLM judge only ever sees page
   content inside the UNTRUSTED-PAGE-CONTENT markers.
7. **Curation stays human.** LoreLoop lists, flags and links — it never
   auto-approves, auto-supersedes, or lets a model decide what a curator
   should.

## Architecture: one engine, shells around it

Users see a single CLI. Internally the layering is deliberate, and the
trust boundary never moves with the shells:

```
┌─ companion skill (installed by `loreloop init`) ───────────────┐
│  teaches the host agent to read context packs, draft suggested │
│  assertions for human approval, remind about verification      │
├─ CLI (human-driven) ───────────────────────────────────────────┤
│  verify / report / harvest / curation — adjudication lives here│
├─ engine (loreloop core library) ───────────────────────────────┤
│  knowledge store + evidence chain + artifacts + drift + minting│
└─────────────────────────────────────────────────────────────────┘
```

Invariants across all shells (these are the product, not implementation
detail):

1. Formal acceptance assertions are written and entered by humans; an LLM
   may draft, never enter.
2. Verdicts come from the on-chain report, never from an agent's account
   of its own work.
3. Minting (harvest) only runs through the human-driven CLI.
4. Agents read knowledge; approve/reject/supersede are human acts.

**Decided against an MCP server.** Context packs push knowledge at
delegation time; hosts that need an ad-hoc lookup can shell out to
`loreloop knowledge list`. An MCP layer would wrap an existing interface in
a resident process for hosts we don't target. Revisit only if a host that
cannot execute shell commands needs to integrate.

## Pull requests

- Keep PRs single-purpose; tests for every behavior change.
- `pytest -q` and `ruff check .` must pass.
- Explain the *why* in the PR description, especially when touching the
  trust model or the evidence chain.
