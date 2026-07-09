# Security Policy

## Threat model: honest workstation

knowhelm is local-first and assumes an **honest workstation**: the machine
running knowhelm, its filesystem, and its OS user are trusted. Within that
boundary, knowhelm defends against:

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
  are unpinned until the next knowhelm command re-pins them — requires the
  crash itself and closes at the very next verification. Observations are content-addressed artifacts (SHA-256-named,
  re-hashed on load and cross-checked against the url/snapshot pin recorded
  on the chain; an artifact-bearing check without that pin is itself an
  integrity failure). Run traces under `.knowhelm/runs/` are display
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
  digest of the entry's content and source. Before injection, `knowhelm run`
  recomputes that digest from the current DB row — an entry whose strong bit
  has no chain endorsement, or whose content was rewritten after
  endorsement, is demoted to reference; a row whose current digest is
  chain-endorsed remains strong even if the DB cache was flipped back to
  draft. `knowledge list` applies the same rule, so SQLite edits can neither
  launder nor suppress strong trust. Trust bits are written chain-first
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
- **Accidental credential capture.** knowhelm never automates logins: at a
  login wall it either skips the page or hands the real browser window to
  the human. Observation artifacts may contain post-login page content, so
  they are written 0600 in a 0700 directory.

Explicitly **out of scope**: an attacker with write access to the
workstation (they can replace the binary, the key, and the chain together),
a malicious coding agent binary, and confidentiality of the local SQLite
store beyond file permissions.

## Known limitations (deliberate trade-offs)

- **Injection trusts the last verification.** `knowhelm run` does not
  re-open a browser to re-check strong web entries before injecting them;
  live pages may have drifted since verification. Re-verification is an
  explicit, human-initiated act (`knowhelm knowledge verify`) because a
  silent per-run browser sweep would be slow, break offline use, and hit
  live systems as a side effect. `run` prints a reminder when strong web
  entries are injected.
- **Page snapshot hashes observe a bounded window.** The snapshot hash
  covers title, visible text (truncated at the observation limit) and form
  structure — not links or the full DOM. Including navigation/ad noise
  would flag drift on every page load; the bound is the same one the judge
  actually reads, so what is hashed is what was judged.
- **Operator-vouched checks carry no machine evidence.** `knowhelm check`
  records the operator's word (labeled `judge: operator` on the chain).
  Reports call these out, and harvest never mints knowledge from them.

## Key material

The evidence chain HMAC key lives **outside the project tree**, in
`~/.knowhelm/keys/` (one key per project, owner-only permissions; override
with `KNOWHELM_KEY_DIR`). Coding agents routinely get write access to the
project directory, so the referee's stamp deliberately does not sit inside
the player's sandbox: an agent that rewrites `.knowhelm/evidence.jsonl`
cannot re-sign it. This raises the bar from "any process with workdir
access" to "a process that reaches into the operator's home directory" —
it is still not a defense against a local root attacker. Do not commit
`.knowhelm/` to a public repository — evidence artifacts can embed page
content from your running application.

## Reporting a vulnerability

Open a GitHub security advisory (preferred) or an issue marked [security]
without exploit details, and we will follow up privately. Please do not
publish working exploits before a fix is released.
