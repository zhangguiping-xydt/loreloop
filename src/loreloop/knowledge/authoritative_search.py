"""Replay-verified lexical search over a baseline ZIP or directory package."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from ..delegate.context_pack import rank_entries
from .authoritative_capsule_replay import CapsuleReplayError, load_replayed_capsule_export
from .model import Channel, Entry, Kind, Source

MAX_SEARCH_RECORDS = 200_000
MAX_SEARCH_TEXT_BYTES = 64 * 1024 * 1024
MAX_SEARCH_LINE_BYTES = 8 * 1024 * 1024
_HEADING = re.compile(r"^#{1,6}\s+(?P<title>.+?)\s*$")
_TABLE_SEPARATOR = re.compile(r"^\|(?:\s*:?-{3,}:?\s*\|)+$")


class BaselineSearchError(ValueError):
    """A baseline cannot be verified or searched within production bounds."""


@dataclass(frozen=True, slots=True)
class BaselineSearchHit:
    filename: str
    heading: str
    score: float
    snippet: str


def _search_entries(files: dict[str, bytes], filenames: tuple[str, ...]) -> list[Entry]:
    entries: list[Entry] = []
    total_bytes = 0
    for filename in filenames:
        try:
            text = files[filename].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise BaselineSearchError(f"baseline Markdown is not UTF-8: {filename}") from exc
        heading = filename.removesuffix(".md")
        for line_number, raw in enumerate(text.splitlines(), 1):
            line = raw.strip()
            match = _HEADING.match(line)
            if match is not None:
                heading = match.group("title")
                continue
            if (
                not line
                or _TABLE_SEPARATOR.fullmatch(line)
                or line.startswith("- [")
                or line in {"```", "```mermaid"}
            ):
                continue
            encoded = line.encode("utf-8")
            if len(encoded) > MAX_SEARCH_LINE_BYTES:
                raise BaselineSearchError(
                    f"baseline searchable line exceeds {MAX_SEARCH_LINE_BYTES} bytes: "
                    f"{filename}:{line_number}"
                )
            total_bytes += len(encoded)
            if total_bytes > MAX_SEARCH_TEXT_BYTES:
                raise BaselineSearchError(
                    f"baseline searchable text exceeds {MAX_SEARCH_TEXT_BYTES} bytes"
                )
            if len(entries) >= MAX_SEARCH_RECORDS:
                raise BaselineSearchError(
                    f"baseline searchable record count exceeds {MAX_SEARCH_RECORDS}"
                )
            identifier = hashlib.sha256(
                f"{filename}\0{heading}\0{line_number}\0{line}".encode()
            ).hexdigest()
            entries.append(
                Entry(
                    id=identifier,
                    title=f"{filename} {heading}",
                    content=line,
                    kind=Kind.BEHAVIOR,
                    source=Source(Channel.EVIDENCE, filename, symbol=heading),
                )
            )
    return entries


def search_baseline(
    export_path: Path,
    query: str,
    *,
    limit: int = 10,
) -> tuple[BaselineSearchHit, ...]:
    """Verify a package, then rank its Markdown rows without extracting it."""
    if limit < 1:
        raise BaselineSearchError("search limit must be at least 1")
    if not query.strip():
        raise BaselineSearchError("search query must not be empty")
    try:
        bundle = load_replayed_capsule_export(export_path)
    except CapsuleReplayError as exc:
        raise BaselineSearchError(str(exc)) from exc
    files = dict(bundle.files)
    entries = _search_entries(files, bundle.result.documents)
    ranked = rank_entries(query, entries, limit=limit)
    hits: list[BaselineSearchHit] = []
    for result in ranked:
        entry = result.entry
        snippet = " ".join(entry.content.replace("|", " ").replace("`", "").split())
        if len(snippet) > 240:
            snippet = snippet[:237].rstrip() + "..."
        hits.append(
            BaselineSearchHit(
                entry.source.locator,
                entry.source.symbol or "-",
                result.adjusted_score,
                snippet,
            )
        )
    return tuple(hits)
