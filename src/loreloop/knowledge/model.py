"""Knowledge entry model.

Design contract (do not regress):
- ``kind`` is the single classification axis.
- ``Source`` is structured provenance, never a bare string label. ``snapshot_ref``
  anchors freshness: entries are stale when the anchor drifts from current state
  (e.g. ``git diff`` against the recorded commit), not when a stored expiry passes.
- ``Trust`` is two explicit axes: human curation and machine verification.
  Contradiction/supersession are links between entries, not status flags.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone


class Kind(enum.StrEnum):
    REQUIREMENT = "requirement"
    INTERFACE = "interface"
    ARCHITECTURE = "architecture"
    BEHAVIOR = "behavior"
    CONSTRAINT = "constraint"
    ACCEPTANCE = "acceptance"


class Channel(enum.StrEnum):
    CODE = "code"
    WEB = "web"
    IMAGE = "image"
    MANUAL = "manual"
    EVIDENCE = "evidence"


class Curation(enum.StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    REJECTED = "rejected"


class Verification(enum.StrEnum):
    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    CONTRADICTED = "contradicted"


class LinkType(enum.StrEnum):
    SUPERSEDES = "supersedes"
    CONTRADICTS = "contradicts"


CURATION_TRANSITIONS: dict[Curation, frozenset[Curation]] = {
    Curation.DRAFT: frozenset({Curation.APPROVED, Curation.REJECTED}),
    # APPROVED -> APPROVED is re-endorsement: legitimate re-anchoring (e.g.
    # harvest re-anchoring an unchanged claim) leaves an approved entry with
    # no endorsement for its new digest, and only a fresh human approval of
    # the CURRENT row may rebind it.
    Curation.APPROVED: frozenset({Curation.APPROVED, Curation.REJECTED}),
    Curation.REJECTED: frozenset({Curation.DRAFT}),
}


@dataclass(frozen=True)
class Source:
    channel: Channel
    locator: str
    snapshot_ref: str | None = None
    symbol: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    excerpt: str | None = None

    def __post_init__(self) -> None:
        if not self.locator.strip():
            raise ValueError("Source.locator must be non-empty")
        if (self.line_start is None) != (self.line_end is None):
            raise ValueError("Source line_start and line_end must be provided together")
        if self.line_start is not None:
            if self.line_start < 1 or self.line_end < self.line_start:
                raise ValueError("Source line range is invalid")
        if self.symbol is not None and not self.symbol.strip():
            raise ValueError("Source.symbol must be non-empty when provided")
        if self.excerpt is not None and not self.excerpt.strip():
            raise ValueError("Source.excerpt must be non-empty when provided")


@dataclass(frozen=True)
class Trust:
    curation: Curation = Curation.DRAFT
    verification: Verification = Verification.UNVERIFIED
    verified_at: datetime | None = None
    verified_by: str | None = None

    def __post_init__(self) -> None:
        verified = self.verification is not Verification.UNVERIFIED
        if verified and (self.verified_at is None or self.verified_by is None):
            raise ValueError("verified/contradicted requires verified_at and verified_by")
        if not verified and (self.verified_at is not None or self.verified_by is not None):
            raise ValueError("unverified must not carry verified_at/verified_by")


@dataclass(frozen=True)
class Entry:
    title: str
    content: str
    kind: Kind
    source: Source
    trust: Trust = field(default_factory=Trust)
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if not self.title.strip():
            raise ValueError("Entry.title must be non-empty")
        if not self.content.strip():
            raise ValueError("Entry.content must be non-empty")

    def is_strong_evidence(self) -> bool:
        """Context-pack grading: only approved or machine-verified knowledge
        qualifies as strong evidence; everything else is reference-only."""
        return (
            self.trust.curation is Curation.APPROVED
            or self.trust.verification is Verification.VERIFIED
        )


@dataclass(frozen=True)
class Link:
    from_id: str
    to_id: str
    link_type: LinkType
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if self.from_id == self.to_id:
            raise ValueError("Link cannot point at itself")
