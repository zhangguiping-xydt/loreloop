# Contributing to knowhelm

Thanks for your interest. knowhelm is early alpha — small, focused
contributions are the most useful kind right now.

## Setup

```bash
git clone https://github.com/starry-knowhelm/starry-knowhelm
cd starry-knowhelm
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
   separate steps; model output is schema-validated JSON or the batch fails
   (`ExtractionError`). No inline markers, no "the model will comply".
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
7. **Curation stays human.** knowhelm lists, flags and links — it never
   auto-approves, auto-supersedes, or lets a model decide what a curator
   should.

## Pull requests

- Keep PRs single-purpose; tests for every behavior change.
- `pytest -q` and `ruff check .` must pass.
- Explain the *why* in the PR description, especially when touching the
  trust model or the evidence chain.
