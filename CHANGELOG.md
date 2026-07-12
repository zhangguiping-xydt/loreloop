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
- Raw-backed evaluation summary generation and release thresholds; unsupported
  historical task claims are omitted when their raw result file is absent.
- Pinned-action CI, coverage/build/Twine gates, Bandit, dependency and secret
  scans, Dependabot, community templates, governance/support/release policies,
  and a Trusted Publishing workflow with SPDX SBOM and provenance attestation.
- `knowledge reopen` for an explicit rejected → draft review path.
- Current-session `begin`/`complete` lifecycle for using LoreLoop inside an
  existing Codex or Claude Code conversation without launching a nested agent;
  preparation metadata is signed before work and completion requires explicit
  operator confirmation.
- A distributable Codex plugin and repository marketplace. On first use the
  plugin can, after explicit operator approval, install the Runtime from a
  checksummed GitHub Release without requiring PyPI.
- Bundled POSIX and PowerShell Runtime installers, versioned GitHub Release
  wheel assets, `SHA256SUMS`, SBOM, and provenance coverage for release assets.
- Automatic project-local trust registration across Codex, Claude Code, and
  terminal sessions, plus user-facing `trust status`, verified `trust recover`,
  and explicit archival `trust reset` workflows.
- Native `loreloop codex install/status/uninstall` lifecycle commands and a
  GitHub Release installer `--codex` mode that installs the Runtime and enables
  the marketplace plugin without directly editing Codex configuration files.
- Native OpenCode support through global/project Skills and a `/loreloop`
  command, with `loreloop opencode install/status/uninstall` lifecycle commands.
- Native co-mind support through the Claude-compatible marketplace/plugin
  system, plus inference, restricted delegation, lifecycle commands, and
  release-installer flags for both new hosts.

### Changed

- Renamed the product, Python package, CLI, state directory, and environment
  variables from Knowhelm to LoreLoop. This is an intentional hard cutover:
  `.knowhelm/` and `KNOWHELM_*` are not read or migrated automatically.
- `repo`, `project`, and `knowledge` actions are real argparse subcommands with
  action-specific positional names.
- Code extraction uses prompt version `code-extract-v3` and performs one strict
  repair attempt after deterministic output/evidence validation fails. A repeated
  excerpt-only mismatch is canonicalized from its validated source span on retry.
- Interrupted delegations are explicitly traced and can never look completed.
- Chain-first harvests can resume DB materialization after an interruption,
  while completed harvests remain idempotently protected from duplication.
- Federation verification performs no writes, including no foreign head repair.
- Inference and delegation now use separate least-capability agent profiles;
  the offline demo and tests exercise both without invoking a real model.
- Command evidence binds repository HEAD/working-tree state, redacts output,
  and the latest result for an identical check supersedes older attempts.
- Companion skills now keep Codex, Claude Code, OpenCode, or co-mind as the user-facing entry point,
  use session-native preparation by default, and mediate completion, harvest,
  and curation only after specific operator authorization.
- Existing evidence history never causes a replacement local-trust credential
  to be generated. Normal onboarding hides cryptographic internals and recovery
  reconnects verified operator-owned trust before reset is considered.

### Security

- Trust-raising operations bind current entry digests to the out-of-tree HMAC
  chain. SQLite-only approvals, retirements, or content edits do not gain trust.
- Missing or unexplained rewrites of chain-backed SQLite rows fail closed;
  newer contradictions override older approvals until explicit reapproval.
- Agent subprocesses do not inherit operator key/registry locations, and normal
  signing APIs reject LoreLoop-launched agent processes.
- Git ingestion rejects dirty source, tracked symlinks and non-regular files,
  handles NUL-delimited non-ASCII paths, and enforces file/batch byte limits.
- Browser discovery, redirects, requests, and final observations enforce
  HTTP(S)/same-origin boundaries; JavaScript POSTs require explicit opt-in,
  while password and destructive actions remain blocked.
- State, key, chain, lock, database, trace, and artifact paths use restrictive
  permissions and reject symlink substitution at their trust boundaries.
