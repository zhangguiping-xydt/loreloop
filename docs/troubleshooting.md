# Troubleshooting

Every expected CLI failure is printed as three lines: `error`, `reason`, and
`next`. A Python traceback is a bug; please report it with the command and the
three lines above it, after removing project paths or page content you consider
sensitive.

## `loreloop doctor` is not ready

- Agent CLI missing: install Claude Code or Codex, authenticate it, and confirm
  `claude -p "reply ok"` or `codex exec -` works outside LoreLoop.
- Project/key directory not writable: use a writable checkout. Set
  `LORELOOP_KEY_DIR` to an owner-controlled directory outside the project when
  the default home directory is unavailable.
- Playwright is informational until web ingestion or verification is needed.
  Install with `python -m pip install 'loreloop[web]'` followed by
  `python -m playwright install chromium`.

## Browser exploration or verification fails

Use `--headed` for a login handover; LoreLoop never types passwords. Check that
the URL is reachable from the same account running LoreLoop. Exploration,
redirects, robots, sitemap discovery, and action scripts remain same-origin.
Write-like script steps are refused unless the reviewed command includes
`--allow-writes`; password fields and destructive controls remain blocked.

## Evidence key or chain error

Never delete only the chain or only its key. The key and head commitment live in
`~/.loreloop/keys/` (or `LORELOOP_KEY_DIR`), outside the agent-writable project.
If a legacy in-tree key is detected, follow the exact move/archive choices in
the error. A tampered or truncated chain is deliberately not auto-repaired.

## Database schema is newer or an upgrade fails

LoreLoop refuses a database whose `PRAGMA user_version` is newer than the
installed binary. Upgrade LoreLoop. Before every legacy schema upgrade, LoreLoop
creates `knowledge.db.schema-v<old>.bak`; failed migrations roll back the active
database transaction. To downgrade, stop all LoreLoop processes, archive the
new database, restore the matching backup, and use the older binary. Never copy
individual SQLite tables between versions.

## A run was interrupted or the agent failed

The trace ends in `delegation_interrupted` or `delegation_failed`; neither state
can be accepted because no chain-backed `delegation_completed` event exists.
Fix the reported agent issue and start a new run. Do not edit or reuse the old
run id.

## Report or harvest is refused

Use the exact run id printed by `loreloop run`. `loreloop report <run-id>` shows
missing, failed, or broken-artifact checks. Harvest additionally requires every
source repository captured by the run to have committed source changes; commit
them first so returned knowledge can anchor to a real Git commit.
If a crash occurred after the signed harvest event but before SQLite finished,
rerun the same harvest command: LoreLoop restores only the signed, digest-matching
minted rows and does not append a duplicate harvest event.

## Federation result is unavailable

`loreloop project list` shows registry reachability. Federation is strictly
read-only: it will not create a foreign key, heal a foreign head, migrate a
foreign database, or import entries automatically. Open the foreign project
locally once if it needs an owner-side migration or head repair.
