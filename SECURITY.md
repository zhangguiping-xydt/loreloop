# Security Policy

## Supported versions

LoreLoop is early alpha. Security fixes are applied to the latest release and
the `main` branch; older alpha releases may require upgrading rather than a
backport.

## Threat model: honest workstation

LoreLoop is local-first and assumes an **honest workstation**: the machine
running LoreLoop, its filesystem, and its OS user are trusted. Within that
boundary, LoreLoop defends against:

- **Prompt injection from explored web pages and knowledge entries.** Page
  content and stored knowledge are untrusted input. Deterministic assertions
  never pass through a model; the LLM judge receives page content only inside
  explicit untrusted-data markers and is instructed to treat imperative text
  in pages as evidence, not commands. Context-pack entries are rendered as
  single-line JSON objects and declared to be data, so embedded newlines,
  Markdown headings, or fake task sections remain string values.
- **After-the-fact tampering with evidence.** The evidence chain is
  HMAC-SHA256 linked, and the latest (index, chain hash) is committed next
  to the key outside the project tree, so deleting trailing records is
  detected too. Appending re-verifies the whole chain first and refuses to
  extend a tampered or truncated one — a later legitimate append can never
  build on a truncated prefix and move the head commitment past the damage.
  A head commitment that is missing or lags the chain (a crash between the
  chain append and the head commit) is healed on the next verification:
  every record carries a valid HMAC from the out-of-tree key, so re-pinning
  the signed tail endorses nothing an agent could have written. The
  residual window — records appended in the instant before such a crash
  are unpinned until the next LoreLoop command re-pins them — requires the
  crash itself and closes at the very next verification. Observations are content-addressed artifacts (SHA-256-named,
  re-hashed on load and cross-checked against the url/snapshot pin recorded
  on the chain; an artifact-bearing check without that pin is itself an
  integrity failure). Run traces under `.loreloop/runs/` are display
  material only: acceptance and harvest key off the chain-endorsed
  `delegation_completed` record (run id, task, context, base commit), so a
  forged `delegation_finished` line or an edited `base_commit` in the trace
  cannot sway a verdict. Checks count only when they postdate that
  completion record on the chain — a check pre-planted while the run was
  still in flight is ignored — and a run id with more than one completion
  record is never accepted. Editing, truncating or swapping evidence breaks
  verification and degrades the acceptance verdict.
- **Trust laundering.** LLM-derived knowledge is born draft/unverified.
  Only human-written, machine-checked, chain-backed assertions are born
  verified. Verification can never be rolled back to unverified. The SQLite
  store sits inside the agent-writable tree, so its trust columns are not
  taken at face value: every approval, verification and supersession is also
  endorsed on the evidence chain, and each trust-raising event binds a
  digest of the entry's content and source. Before injection, `loreloop run`
  recomputes that digest from the current DB row. A DB-only strong bit is
  demoted, but deletion or an unexplained rewrite of a chain-backed row fails
  closed: delegation stops instead of exposing attacker-controlled content as
  a reference. A row whose current digest is chain-endorsed remains strong
  even if the DB cache was flipped back to draft. `knowledge list` applies the
  same replay rules, so SQLite edits can neither launder nor silently suppress
  strong trust. Trust bits are written chain-first
  everywhere (verification, curation, harvest minting):
  a crash between the two writes leaves a draft, never an unendorsed strong
  row. Only human or machine trust acts
  rebind a digest: when harvest re-anchors an endorsed entry to a new
  commit, the old endorsement does NOT follow it (LLM re-extraction can be
  steered via comment pollution), the entry demotes, and harvest tells the
  operator to re-approve. Minting onto an existing row forces the row's
  title and kind to the values derived from the check itself, so the minted
  digest never signs fields the agent pre-planted, and all fields of a
  verify/mint write-back land in a single atomic UPDATE — no crash window
  holds verified trust on a row state the chain never endorsed.
  Supersession and rejection are likewise replayed from the chain: deleting
  the DB links row does not resurrect a retired entry, and flipping a
  rejected row's curation column back in SQLite does not re-inject it — an
  entry rejected on the chain stays out until the operator's own
  reject → draft transition appends a new curation event. Passed web
  verifications always anchor the entry to the
  observed page hash — an anchor-less entry counts as drifted, never as
  fresh.
- **Accidental credential capture.** LoreLoop never automates logins: at a
  login wall it either skips the page or hands the real browser window to
  the human. Observation artifacts may contain post-login page content, so
  they are written 0600 in a 0700 directory. Command evidence redacts common
  secret assignments, labels, and secret-valued environment variables from
  stdout/stderr before storing artifacts or chain details.

- **Agent subprocess capability reduction.** Inference is launched in a
  temporary directory with Claude tools disabled or Codex read-only sandboxing
  and user/project rules ignored. Delegation uses explicit non-bypass workspace
  permissions. LoreLoop removes operator key/registry variables and marks child
  processes so its signing API refuses their append attempts. This is defense
  in depth for cooperative tools, not an OS security boundary: a malicious
  agent binary can ignore conventions, reset environment variables, or attack
  anything the OS user can access.

Explicitly **out of scope**: an attacker with write access to the workstation
(they can replace the binary, the key, and the chain together), a malicious or
compromised coding-agent binary, a model deliberately escaping the provided
CLI capability profile, and confidentiality of local state beyond filesystem
permissions.

## Runtime security contract

These statements are observable behavior, not aspirational documentation:

| Promise | Runtime enforcement | Regression coverage |
|---|---|---|
| Foreign federation is read-only | SQLite opens with `mode=ro`; chain verification never creates or advances a foreign head/key | `tests/test_federation.py`, `tests/test_evidence_chain.py` |
| Trust cannot be raised or silently rewritten by SQLite edits | Current entry digests must replay from HMAC-chain events; missing/unexplained chain-backed rows stop delegation | `tests/test_endorsement.py`, `tests/test_report_and_cli.py` |
| Browser scope cannot escape origin silently | Redirect targets, robots, sitemaps, discovered links, network requests, action steps, and final observations are checked | `tests/test_webexplore.py`, `tests/test_smoke_playwright.py` |
| Scripted writes require operator opt-in | Network interception blocks same-origin non-GET requests without `--allow-writes`; password/destructive controls stay blocked | `tests/test_webexplore.py`, `tests/test_smoke_playwright.py` |
| Agent children do not receive normal signing capability | Key/registry variables are removed; the child marker makes evidence append fail; inference/delegation commands use explicit permission profiles | `tests/test_delegate.py`, `tests/test_evidence_chain.py` |
| Interrupted/failed work is not acceptance | Trace records an explicit failed/interrupted terminal event; only one chain-backed completion can be accepted | `tests/test_delegate.py`, `tests/test_report_and_cli.py` |
| Chain-first harvest survives a DB crash | Signed harvest events carry complete minted rows; a retry restores only missing digest-matching rows without appending a second event | `tests/test_harvest.py` |
| Schema upgrades preserve rollback material | Ordered transaction, pre-upgrade SQLite backup, refusal of newer versions | `tests/test_knowledge_store.py` |
| Expected failures do not leak tracebacks | argparse and runtime failures share `error`/`reason`/`next` output | `tests/test_cli_help.py`, CLI E2E tests |

On POSIX, newly created state, key, trace, database, chain, lock, and artifact
directories/files use `0700`/`0600` as appropriate and reject symlink
substitution at their protected paths.
On Windows, Python's POSIX mode bits are not an ACL mechanism; confidentiality
there relies on the current user's profile/directory ACL. A custom
`LORELOOP_KEY_DIR` remains the operator's security boundary and should not be
shared with other accounts.

## Known limitations (deliberate trade-offs)

- **Local storage is not local inference.** Claude Code and Codex may send
  source snippets, page observations, and prompts to their configured model
  provider. Provider retention, residency, account policy, and transport are
  outside LoreLoop's control. Do not ingest data you are not authorized to
  disclose to that provider.
- **Browser write fencing cannot repair unsafe server semantics.** Cross-origin
  requests and default non-GET requests are blocked, but a same-origin GET can
  still have side effects in a poorly designed application. Run action scripts
  against disposable or staging systems and review scripts before using
  `--allow-writes`.

- **Injection trusts the last verification.** `loreloop run` does not
  re-open a browser to re-check strong web entries before injecting them;
  live pages may have drifted since verification. Re-verification is an
  explicit, human-initiated act (`loreloop knowledge verify`) because a
  silent per-run browser sweep would be slow, break offline use, and hit
  live systems as a side effect. `run` prints a reminder when strong web
  entries are injected.
- **Page snapshot hashes observe a bounded window.** The snapshot hash
  covers title, visible text (truncated at the observation limit) and form
  structure — not links or the full DOM. Including navigation/ad noise
  would flag drift on every page load; the bound is the same one the judge
  actually reads, so what is hashed is what was judged.
- **Operator-vouched checks carry no machine evidence.** `loreloop check`
  records the operator's word (labeled `judge: operator` on the chain).
  Reports call these out, and harvest never mints knowledge from them.

## Key material

The evidence chain HMAC key lives **outside the project tree**, in
`~/.loreloop/keys/` (one key per project, owner-only permissions; override
with `LORELOOP_KEY_DIR`). Coding agents routinely get write access to the
project directory, so the referee's stamp deliberately does not sit inside
the player's sandbox: an agent that rewrites `.loreloop/evidence.jsonl`
cannot re-sign it. This raises the bar from "any process with workdir
access" to "a process that reaches into the operator's home directory" —
it is still not a defense against a local root attacker. Do not commit
`.loreloop/` to a public repository — evidence artifacts can embed page
content from your running application.

## Reporting a vulnerability

Open a private GitHub Security Advisory. Do not open a public issue containing
exploit details, credentials, private source, or sensitive evidence artifacts.
Include affected versions, impact, reproduction steps, and any suggested fix.
We will acknowledge the report as soon as maintainers are available and
coordinate disclosure after a fix is released.
