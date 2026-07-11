# Product polish backlog

The goal is not merely to make every feature work. Every step should be predictable,
recoverable, clearly explained, and pleasant for a first-time user.

## 1. Installation and first successful run

- [x] Provide one canonical installation path for a source checkout and one for releases.
- [x] Add a preflight command that checks Python, Git, agent CLI, Playwright, writable state,
      key location, and supported platform before initialization.
- [x] Make a new user complete `init -> ingest -> run -> verify -> report -> harvest` within
      five minutes using a bundled example project.
- [x] Verify the complete first-run path on every supported operating system in CI.

Acceptance: a clean machine can follow the README verbatim without guessing or recovering
from undocumented prerequisites.

## 2. CLI structure, help, and error recovery

- [x] Give every action its own argparse subparser and action-specific positional names.
- [x] Ensure every expected failure prints one concise error, a reason, and the next action;
      never print a Python traceback for user, agent, Git, browser, or input failures.
- [x] Add `--help` snapshots and end-to-end tests for all public commands.
- [x] Make interrupted and failed commands leave no ambiguous or partially trusted state.

Acceptance: a first-time user can discover every workflow from `loreloop --help` and recover
from common failures without reading source code.

## 3. Release-blocking correctness and safety

- [x] Enforce same-origin after redirects and while fetching robots/sitemap discovery inputs.
- [x] Implement cross-platform evidence-chain locking or explicitly constrain and enforce the
      supported platforms.
- [x] Add versioned SQLite schema migrations and upgrade/rollback fixtures.
- [x] Reconcile every security and read-only claim with observable runtime behavior.

Acceptance: security promises, supported platforms, upgrade behavior, and implementation agree
under adversarial tests.

## 4. README, demo, and complete learning path

- [x] Document multi-repository anchors, federation search, import, and `run --with-related`.
- [x] Add a copy-paste quick start, terminal recording, architecture overview, and troubleshooting.
- [x] Publish a small legacy-style example application with known facts, drift, and acceptance
      scenarios.
- [x] Keep README, SECURITY, design documents, CLI help, and release notes synchronized.

Acceptance: a reader understands the problem, differentiation, trust model, and full workflow
before installing.

## 5. Performance benchmarks and public-project evidence

- [x] Benchmark reverse-engineering cost and quality on fixed Python, TypeScript, and
      mixed-language fixtures.
- [x] Measure retrieval Precision@K, Recall@K, latency, and prompt-token cost at 100, 1,000, and
      10,000 entries across multiple projects.
- [x] Measure evidence-chain verification and harvest latency at increasing chain lengths.
- [x] Compare task outcomes against no memory, session memory, and codebase-index baselines.
- [ ] Reproduce the workflow on real public repositories and publish both successful and failed
      cases without extrapolating fixture scores.

Acceptance: published, reproducible data shows where LoreLoop helps, where it does not, and its
operating limits.

## 6. Zero-context usability study

The uncoached protocol, privacy-safe JSON template, validator, aggregator, and
three-platform first-run smoke are complete (`docs/usability-study.md`,
`eval/usability.py`). The four outcome items below deliberately remain open
until real people—not agents or simulated personas—complete the study.

- [ ] Give the project to users who have not seen its design or source code.
- [ ] Record time to first success, wrong turns, help lookups, recovery attempts, and abandoned
      steps without coaching them.
- [ ] Fix the highest-friction point, rerun the study, and repeat until the workflow is stable.
- [ ] Convert every recurring mistake into a product, documentation, or automated-check fix.

Acceptance: unfamiliar users complete the primary workflow reliably and describe the trust
model correctly in their own words.

## 7. Open-source release engineering

- [x] Use a frozen `uv.lock`, tested Python range, coverage gate, package build and Twine checks.
- [x] Run Bandit, dependency audit, Gitleaks, deterministic evaluation thresholds, and browser
      security smoke tests in pinned-action CI.
- [x] Add issue/PR templates, governance, support, conduct, security, and release documentation.
- [x] Add a tag-driven Trusted Publishing workflow with SPDX SBOM and GitHub provenance.
- [ ] Create/reserve the public GitHub repository and PyPI project, then configure the protected
      `pypi` environment and matching Trusted Publisher.
- [ ] Revoke the previously exposed Anthropic credential at the provider and verify no active
      credential remains in Git history or external caches.

Acceptance: local release artifacts and automation are auditable; external publishing is not
considered complete until provider-side and hosting-side configuration is verified.

## 8. Knowledge governance audit follow-up

- [x] Add full entry inspection, filtered/paginated review queues, and complete source evidence.
- [x] Make supersession reversible, confirmation-gated, cycle-safe, and always demoted in views.
- [x] Make ingest refresh and harvest re-anchoring chain-first with signed recovery material.
- [x] Report ingestion coverage, sign per-repository include/exclude/size policy, and pin the
      complete policy map to each run before harvest.
- [x] Rank federated and related-project candidates in one comparable BM25 corpus.
- [x] Preserve demo key continuation instructions and finish the post-harvest curation loop.
- [x] Drive status filters and active-entry decisions from chain-replayed effective curation.
- [x] Authorize curation transitions from chain state and use SQLite only as projection.
- [x] Resume an already signed harvest before considering later acceptance-check changes.
- [ ] Complete the three-person zero-context usability study in section 6; automated agents and
      fixture demos must not be reported as human-participant evidence.
