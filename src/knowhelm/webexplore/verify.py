"""Browser-verified acceptance checks.

Observes the live page and asks the agent to judge one expectation against
the observed content only. The verdict, page snapshot hash, and reasoning all
land on the evidence chain, so the acceptance report cites what the browser
actually saw — not what the coding agent claimed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

from ..agents import AgentRunner
from ..evidence.chain import EvidenceChain, EvidenceRecord
from ..knowledge.code_reverse import ExtractionError
from .browser import Browser

_VERIFY_PROMPT = """\
You are verifying an acceptance expectation against an observed web page.

Expectation: {expectation}

Observed page:
URL: {url}
TITLE: {title}
FORMS: {forms}
CONTENT:
{text}

Judge strictly from the observed content. Output a single JSON object
(nothing else, no markdown fence):

  {{"passed": true or false, "reason": "<one sentence citing the observed content>"}}
"""


@dataclass(frozen=True)
class VerifyResult:
    passed: bool
    reason: str
    snapshot: str
    record: EvidenceRecord


def verify_expectation(
    browser: Browser,
    runner: AgentRunner,
    chain: EvidenceChain,
    run_id: str,
    url: str,
    expectation: str,
) -> VerifyResult:
    obs = browser.observe(url)
    raw = runner.run(
        _VERIFY_PROMPT.format(
            expectation=expectation,
            url=obs.url,
            title=obs.title,
            forms=json.dumps(obs.forms),
            text=obs.text[:8000],
        )
    )
    verdict = _parse_verdict(raw)
    record = chain.append(
        "check_passed" if verdict["passed"] else "check_failed",
        {
            "run_id": run_id,
            "check": expectation,
            "detail": verdict["reason"],
            "url": obs.url,
            "page_snapshot": obs.snapshot_hash,
            "verified_via": "browser",
        },
    )
    return VerifyResult(
        passed=verdict["passed"],
        reason=verdict["reason"],
        snapshot=obs.snapshot_hash,
        record=record,
    )


@dataclass(frozen=True)
class EntryVerifyResult:
    passed: bool
    reason: str
    drifted: bool
    record: EvidenceRecord


def verify_entry(
    browser: Browser,
    runner: AgentRunner,
    chain: EvidenceChain,
    store,
    entry,
    run_id: str,
) -> EntryVerifyResult:
    """Verify a web-channel entry's claim against its live source page and
    write the outcome back to the knowledge store (verified/contradicted)."""
    from ..knowledge.model import Channel, Verification

    if entry.source.channel is not Channel.WEB:
        raise ValueError(f"entry {entry.id} is not web-channel (got {entry.source.channel})")

    obs = browser.observe(entry.source.locator)
    drifted = bool(entry.source.snapshot_ref) and obs.snapshot_hash != entry.source.snapshot_ref
    raw = runner.run(
        _VERIFY_PROMPT.format(
            expectation=entry.content,
            url=obs.url,
            title=obs.title,
            forms=json.dumps(obs.forms),
            text=obs.text[:8000],
        )
    )
    verdict = _parse_verdict(raw)
    new_status = Verification.VERIFIED if verdict["passed"] else Verification.CONTRADICTED
    store.set_verification(entry.id, new_status, run_id, datetime.now(timezone.utc))
    record = chain.append(
        "entry_verified" if verdict["passed"] else "entry_contradicted",
        {
            "run_id": run_id,
            "entry_id": entry.id,
            "claim": entry.content,
            "detail": verdict["reason"],
            "url": obs.url,
            "page_snapshot": obs.snapshot_hash,
            "anchor_drifted": drifted,
            "verified_via": "browser",
        },
    )
    return EntryVerifyResult(
        passed=verdict["passed"], reason=verdict["reason"], drifted=drifted, record=record
    )


def _parse_verdict(raw: str) -> dict:
    text = raw.strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ExtractionError(f"verifier output is not a JSON object: {text[:200]!r}")
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ExtractionError(f"verifier output is invalid JSON: {exc}") from exc
    if not isinstance(data.get("passed"), bool) or not isinstance(data.get("reason"), str):
        raise ExtractionError(f"verifier output missing passed/reason: {data!r}")
    return data
