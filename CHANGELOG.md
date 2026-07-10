# Changelog

## Unreleased

### Added

- Public `eval/` scoring for reverse Precision/Recall, retrieval
  Precision@K/Recall@K/MRR, executable coding-task outcomes, multi-language
  reverse cost, 100/1k/10k retrieval scale, long evidence chains, and harvest.
- A bundled legacy-upload application with real and deterministic-offline
  five-minute `init -> ingest -> run -> verify -> report -> harvest` modes.
- `loreloop doctor`, multi-repository anchors, read-only federation, query
  expansion, command evidence, interaction scripts, usage correlation, and
  Claude/Codex companion skills.
- Versioned SQLite migrations with pre-upgrade backups and transactional
  rollback; Linux/macOS/Windows CI and evidence locking.
- Reviewed help snapshots, unified recoverable errors, troubleshooting, and a
  zero-context usability protocol that refuses to invent participant results.

### Changed

- Renamed the product, Python package, CLI, state directory, and environment
  variables from Knowhelm to LoreLoop. This is an intentional hard cutover:
  `.knowhelm/` and `KNOWHELM_*` are not read or migrated automatically.
- `repo`, `project`, and `knowledge` actions are real argparse subcommands with
  action-specific positional names.
- Code extraction uses prompt version `code-extract-v3` and performs one strict
  repair attempt after deterministic output/evidence validation fails.
- Interrupted delegations are explicitly traced and can never look completed.
- Chain-first harvests can resume DB materialization after an interruption,
  while completed harvests remain idempotently protected from duplication.
- Federation verification performs no writes, including no foreign head repair.

### Security

- Trust-raising operations bind current entry digests to the out-of-tree HMAC
  chain. SQLite-only approvals, retirements, or content edits do not gain trust.
- Browser discovery and redirects enforce same-origin; scripted writes require
  explicit opt-in, while password and destructive actions remain blocked.
