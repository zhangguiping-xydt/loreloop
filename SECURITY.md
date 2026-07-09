# Security Policy

## Threat model: honest workstation

knowhelm is local-first and assumes an **honest workstation**: the machine
running knowhelm, its filesystem, and its OS user are trusted. Within that
boundary, knowhelm defends against:

- **Prompt injection from explored web pages.** Page content is untrusted
  input. Deterministic assertions never pass through a model; the LLM judge
  receives page content only inside explicit untrusted-data markers and is
  instructed to treat imperative text in pages as evidence, not commands.
- **After-the-fact tampering with evidence.** The evidence chain is
  HMAC-SHA256 linked, and the latest (index, chain hash) is committed next
  to the key outside the project tree, so deleting trailing records is
  detected too. Observations are content-addressed artifacts (SHA-256-named,
  re-hashed on load and cross-checked against the url/snapshot recorded on
  the chain). Editing, truncating or swapping evidence breaks verification
  and degrades the acceptance verdict.
- **Trust laundering.** LLM-derived knowledge is born draft/unverified.
  Only human-written, machine-checked, chain-backed assertions are born
  verified. Verification can never be rolled back to unverified. The SQLite
  store sits inside the agent-writable tree, so its trust columns are not
  taken at face value: every approval, verification and supersession is also
  endorsed on the evidence chain, and `knowhelm run` demotes any entry whose
  strong status lacks chain endorsement to reference before injection.
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
