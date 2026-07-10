# Product polish backlog

The goal is not merely to make every feature work. Every step should be predictable,
recoverable, clearly explained, and pleasant for a first-time user.

## 1. Installation and first successful run

- [ ] Provide one canonical installation path for a source checkout and one for releases.
- [x] Add a preflight command that checks Python, Git, agent CLI, Playwright, writable state,
      key location, and supported platform before initialization.
- [ ] Make a new user complete `init -> ingest -> run -> verify -> report -> harvest` within
      five minutes using a bundled example project.
- [ ] Verify the complete first-run path on every supported operating system in CI.

Acceptance: a clean machine can follow the README verbatim without guessing or recovering
from undocumented prerequisites.

## 2. CLI structure, help, and error recovery

- [ ] Give every action its own argparse subparser and action-specific positional names.
- [ ] Ensure every expected failure prints one concise error, a reason, and the next action;
      never print a Python traceback for user, agent, Git, browser, or input failures.
- [ ] Add `--help` snapshots and end-to-end tests for all public commands.
- [ ] Make interrupted and failed commands leave no ambiguous or partially trusted state.

Acceptance: a first-time user can discover every workflow from `knowhelm --help` and recover
from common failures without reading source code.

## 3. Release-blocking correctness and safety

- [x] Enforce same-origin after redirects and while fetching robots/sitemap discovery inputs.
- [x] Implement cross-platform evidence-chain locking or explicitly constrain and enforce the
      supported platforms.
- [ ] Add versioned SQLite schema migrations and upgrade/rollback fixtures.
- [ ] Reconcile every security and read-only claim with observable runtime behavior.

Acceptance: security promises, supported platforms, upgrade behavior, and implementation agree
under adversarial tests.

## 4. README, demo, and complete learning path

- [x] Document multi-repository anchors, federation search, import, and `run --with-related`.
- [ ] Add a copy-paste quick start, terminal recording, architecture overview, and troubleshooting.
- [ ] Publish a small legacy-style example application with known facts, drift, and acceptance
      scenarios.
- [ ] Keep README, SECURITY, design documents, CLI help, and release notes synchronized.

Acceptance: a reader understands the problem, differentiation, trust model, and full workflow
before installing.

## 5. Performance benchmarks and real-project evidence

- [ ] Benchmark reverse-engineering cost and quality on representative Python, TypeScript, and
      mixed-language repositories.
- [ ] Measure retrieval Precision@K, Recall@K, latency, and prompt-token cost at 100, 1,000, and
      10,000 entries across multiple projects.
- [ ] Measure evidence-chain verification and harvest latency at increasing chain lengths.
- [ ] Compare task outcomes against no memory, session memory, and codebase-index baselines.

Acceptance: published, reproducible data shows where knowhelm helps, where it does not, and its
operating limits.

## 6. Zero-context usability study

- [ ] Give the project to users who have not seen its design or source code.
- [ ] Record time to first success, wrong turns, help lookups, recovery attempts, and abandoned
      steps without coaching them.
- [ ] Fix the highest-friction point, rerun the study, and repeat until the workflow is stable.
- [ ] Convert every recurring mistake into a product, documentation, or automated-check fix.

Acceptance: unfamiliar users complete the primary workflow reliably and describe the trust
model correctly in their own words.
