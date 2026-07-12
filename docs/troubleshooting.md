# Troubleshooting

Every expected CLI failure is printed as three lines: `error`, `reason`, and
`next`. A Python traceback is a bug; please report it with the command and the
three lines above it, after removing project paths or page content you consider
sensitive.

## `loreloop doctor` is not ready

- Agent CLI missing: install and authenticate Claude Code, Codex, OpenCode, or
  co-mind, then confirm that host works outside LoreLoop.
- Local trust storage not writable: use a writable checkout and an
  owner-controlled home directory. `LORELOOP_KEY_DIR` remains an advanced
  deployment override, but normal initialization chooses and remembers local
  trust storage automatically. Storage inside the project is rejected even if
  writable.
- Playwright is informational until web ingestion or verification is needed.
  Install with `python -m pip install 'loreloop[web]'` followed by
  `python -m playwright install chromium`.

## LoreLoop installation cannot finish

The bundled installer requires `curl` or `wget` on Linux/macOS and `uv` or
`pipx` on Windows. It downloads `SHA256SUMS` first and accepts only a safe
versioned LoreLoop package name from that manifest. A missing package, malformed
manifest, or checksum mismatch is a hard failure; do not bypass it. Confirm the
GitHub Release is complete, fix proxy/TLS access, and retry. On Linux/macOS the
installer can fall back to a Python 3.11-3.14 virtual environment when neither
`uv` nor `pipx` is available.

## Claude Code integration is not ready

Run `loreloop claude status`. If the marketplace or plugin is absent, run
`loreloop claude install`; this delegates registration and enablement to
Claude Code's native plugin commands and preserves an existing marketplace
source. Start a new Claude Code session after installation.

## Codex integration is not ready

Run `loreloop codex status`. If the marketplace or plugin is absent, run
`loreloop codex install`; this delegates registration and enablement to the
native `codex plugin` commands instead of editing Codex configuration directly.
Start a new Codex thread after installation because plugin Skills are resolved
at the thread boundary. For a source checkout, pass
`--source /absolute/path/to/loreloop`. If an existing marketplace with the same
name points somewhere else, LoreLoop preserves it rather than silently
rewriting user configuration; inspect `codex plugin marketplace list` first.

## OpenCode integration is not ready

Run `loreloop opencode status`, then `loreloop opencode install`. LoreLoop
installs a global Skill and `/loreloop` command without editing `opencode.json`.
If status reports `modified` or `symlink`, LoreLoop preserves that path; move or
reconcile it yourself before retrying. Start a new OpenCode session after
installation. Interactive use and tool-free inference are supported, but
headless `loreloop run --agent opencode` is deliberately refused because the
current CLI has no verifiable workspace sandbox equivalent.

## co-mind integration is not ready

Run `loreloop comind status`, then `loreloop comind install`. LoreLoop uses
co-mind's own `plugin marketplace` and `plugin install` commands and preserves
an existing `loreloop` marketplace source. For a checkout, pass `--source
/absolute/path/to/loreloop`. Start a new co-mind session after installation.

## Browser exploration or verification fails

Use `--headed` for a login handover; LoreLoop never types passwords. Check that
the URL is reachable from the same account running LoreLoop. Exploration,
redirects, robots, sitemap discovery, and action scripts remain same-origin.
After signing in, leave the browser on the authenticated application page and
press Enter in the terminal; LoreLoop observes that current page and continues
from its links. The CLI reports whether the handover resumed and records
`human_handover_completed`, `handover_abandoned`, or `handover_observe_failed`
in the exploration trace.
Write-like script steps are refused unless the reviewed command includes
`--allow-writes`; JavaScript/fetch POSTs are covered by the same network policy,
and password fields and destructive controls remain blocked. Use staging or a
disposable application: a same-origin GET can still have side effects if the
server violates HTTP semantics.

## Provider or data-residency concern

LoreLoop stores state locally but may invoke the configured supported agent
CLI for extraction, optional expansion, headless delegation, and free-form judging. `begin`
itself is deterministic and stays in the host-agent session; the host agent and
any other model-backed action still operate under that provider's terms. Those tools
may send source snippets or page observations to an external provider. Review
the provider/account policy before ingestion, prefer deterministic assertions,
and use `run --no-expand` when query expansion is not needed.

## Local project trust is unavailable or does not match

Run `loreloop trust status` first. Normal initialization manages local trust
automatically and saves the project connection outside the repository. If a
workspace predates automatic registration, lost that saved connection, or
previously used custom trust storage, reconnect it with:

```bash
loreloop trust recover --from <original-trust-directory>
loreloop doctor
```

Recovery verifies the candidate before saving anything; pointing it at the
wrong directory cannot grant trust. Do not move or delete `.loreloop` by hand.
If the original trust material cannot be restored, the operator may explicitly
archive the old domain with `loreloop trust reset --confirm`, then initialize a
new one. This loses continuity with the old evidence history and is never an
automatic agent decision. A tampered or truncated history is deliberately not
auto-repaired.

## Database schema is newer or an upgrade fails

LoreLoop refuses a database whose `PRAGMA user_version` is newer than the
installed binary. Upgrade LoreLoop. Before every legacy schema upgrade, LoreLoop
creates `knowledge.db.schema-v<old>.bak`; failed migrations roll back the active
database transaction. To downgrade, stop all LoreLoop processes, archive the
new database, restore the matching backup, and use the older binary. Never copy
individual SQLite tables between versions.

## A run was interrupted, abandoned, or the agent failed

The trace ends in `delegation_interrupted` or `delegation_failed`; neither state
can be accepted because no chain-backed `delegation_completed` event exists.
Fix the reported agent issue and start a new run. Do not edit or reuse the old
run id. A current-session `begin` intentionally has only a signed
`delegation_prepared` event until the operator confirms
`loreloop complete <run-id> --confirm`; an abandoned preparation cannot be
accepted and needs no cleanup.

## Report or harvest is refused

Use the exact run id printed by `loreloop begin` or `loreloop run`. For a
current-session run, confirm `loreloop complete <run-id> --confirm` before
recording acceptance checks. `loreloop report <run-id>` shows
missing, failed, or broken-artifact checks. Harvest additionally requires every
source repository captured by the run to have committed source changes; commit
them first so returned knowledge can anchor to a real Git commit.
Successful command evidence is bound to the repository state at execution time;
if files or HEAD changed afterward, rerun the command check before harvest.
If a crash occurred after the signed harvest event but before SQLite finished,
rerun the same harvest command: LoreLoop restores only the signed, digest-matching
minted rows and does not append a duplicate harvest event.

## Federation result is unavailable

`loreloop project list` shows registry reachability. Federation is strictly
read-only: it will not create a foreign key, heal a foreign head, migrate a
foreign database, or import entries automatically. Open the foreign project
locally once if it needs an owner-side migration or head repair.
