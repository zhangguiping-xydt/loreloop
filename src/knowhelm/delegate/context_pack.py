"""Select and render a context pack of knowledge for a delegated task.

Selection is deterministic lexical scoring (no embeddings in MVP). Rendering
splits entries into two contract levels:

- strong evidence (approved or machine-verified): facts the agent must respect
- reference only (draft/unverified): hints the agent must re-verify before use

Anchor drift demotes at injection time: a strong entry whose anchored source
changed since capture is offered as reference only, marked as drifted. The
stored trust state is untouched — demotion is a per-injection judgment, not
a curation act.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..knowledge.model import Entry

_WORD = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]{2,}|[一-鿿]{2,}")
_TITLE_WEIGHT = 3.0


def _terms(text: str) -> set[str]:
    return {t.lower() for t in _WORD.findall(text)}


def score(task: str, entry: Entry) -> float:
    task_terms = _terms(task)
    if not task_terms:
        return 0.0
    title_hits = len(task_terms & _terms(entry.title))
    content_hits = len(task_terms & _terms(entry.content))
    return _TITLE_WEIGHT * title_hits + content_hits


@dataclass(frozen=True)
class ContextPack:
    strong: list[Entry]
    reference: list[Entry]
    drifted_ids: frozenset[str] = frozenset()

    @property
    def entry_ids(self) -> list[str]:
        return [e.id for e in self.strong + self.reference]


def select(
    task: str,
    entries: list[Entry],
    limit: int = 20,
    drifted_ids: set[str] | frozenset[str] = frozenset(),
) -> ContextPack:
    scored = [(score(task, e), e) for e in entries]
    relevant = [e for s, e in sorted(scored, key=lambda p: -p[0]) if s > 0][:limit]
    return ContextPack(
        strong=[e for e in relevant if e.is_strong_evidence() and e.id not in drifted_ids],
        reference=[
            e for e in relevant if not e.is_strong_evidence() or e.id in drifted_ids
        ],
        drifted_ids=frozenset(drifted_ids),
    )


def render(pack: ContextPack) -> str:
    if not pack.strong and not pack.reference:
        return ""
    lines = [
        "# Project knowledge (provided by knowhelm)",
        "",
    ]
    if pack.strong:
        lines += [
            "## Established facts — treat as constraints, do not contradict them",
            "",
        ]
        lines += [_render_entry(e) for e in pack.strong]
        lines.append("")
    if pack.reference:
        lines += [
            "## Unverified references — plausible but unconfirmed; verify against the",
            "## source before relying on them",
            "",
        ]
        lines += [_render_entry(e, drifted=e.id in pack.drifted_ids) for e in pack.reference]
        lines.append("")
    return "\n".join(lines)


def _render_entry(e: Entry, drifted: bool = False) -> str:
    note = " [source changed since this was captured]" if drifted else ""
    return f"- [{e.kind.value}] {e.title}: {e.content} (source: {e.source.locator}){note}"
