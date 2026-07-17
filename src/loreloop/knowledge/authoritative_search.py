"""Replay-verified lexical search over a baseline ZIP or directory package."""

from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass
from pathlib import Path

from ..delegate.context_pack import _terms, rank_entries
from .authoritative_ast import DocumentRowKind
from .authoritative_document_ast import build_document_ast_set
from .authoritative_document_routes import CANONICAL_DOCUMENT_OWNER, SECTION_ROUTES
from .authoritative_capsule_replay import CapsuleReplayError, load_replayed_capsule_export
from .authoritative_semantic_model import SemanticCore
from .model import Channel, Entry, Kind, Source

MAX_SEARCH_RECORDS = 200_000
MAX_SEARCH_TEXT_BYTES = 64 * 1024 * 1024
MAX_SEARCH_LINE_BYTES = 8 * 1024 * 1024
MAX_SEARCH_EXPANSION_CHARS = 4_096
MAX_SEARCH_CHUNK_CHARS = 4_000
MAX_SEARCH_CHUNK_LINES = 24
MAX_SEARCH_SNIPPET_CHARS = 240
_HEADING = re.compile(r"^#{1,6}\s+(?P<title>.+?)\s*$")
_TABLE_SEPARATOR = re.compile(r"^\|(?:\s*:?-{3,}:?\s*\|)+$")
_FENCE = re.compile(r"^```(?P<language>[^`]*)$")
_FILE_QUERY = re.compile(r"(?:[/\\]|\.[A-Za-z0-9]{1,12}(?:\b|$))")
_COVERAGE_QUERY_TERMS = (
    "覆盖",
    "盲区",
    "未解析",
    "没有解析",
    "不支持",
    "文件类型",
    "后缀",
    "解析状态",
    "源码文件",
    "coverage",
    "blind spot",
    "unparsed",
    "unsupported",
    "suffix",
    "extension",
    "detector",
    "file status",
    "inspected",
    "blob",
)


class BaselineSearchError(ValueError):
    """A baseline cannot be verified or searched within production bounds."""


@dataclass(frozen=True, slots=True)
class BaselineSearchHit:
    filename: str
    heading: str
    score: float
    snippet: str
    confidence: str = "medium"
    expanded_only: bool = False


@dataclass(frozen=True, slots=True)
class _SearchBlock:
    heading: str
    heading_path: str
    lines: tuple[tuple[int, str], ...]


def _blocks_from_markdown(filename: str, text: str) -> list[_SearchBlock]:
    """Return visible semantic blocks while preserving their section path."""
    blocks: list[_SearchBlock] = []
    heading_stack: list[tuple[int, str]] = []
    heading = filename.removesuffix(".md")
    heading_path = heading
    current: list[tuple[int, str]] = []
    in_fence = False
    skip_fence = False

    def flush() -> None:
        nonlocal current
        if current:
            blocks.append(_SearchBlock(heading, heading_path, tuple(current)))
            current = []

    for line_number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        fence = _FENCE.fullmatch(line)
        if fence is not None:
            if in_fence:
                in_fence = False
                skip_fence = False
            else:
                flush()
                in_fence = True
                skip_fence = fence.group("language").strip().casefold() == "mermaid"
            continue
        if in_fence and skip_fence:
            continue

        match = _HEADING.match(line)
        if match is not None and not in_fence:
            flush()
            level = len(line) - len(line.lstrip("#"))
            title = match.group("title")
            heading_stack = [item for item in heading_stack if item[0] < level]
            heading_stack.append((level, title))
            heading = title
            heading_path = " › ".join(item[1] for item in heading_stack)
            continue

        if not line:
            flush()
            continue
        if (
            _TABLE_SEPARATOR.fullmatch(line)
            or line.startswith("- [")
            or line.startswith("<summary>")
            or line in {"---", "<details>", "</details>"}
        ):
            continue
        current.append((line_number, line))
    flush()
    return blocks


def _split_block(block: _SearchBlock) -> list[_SearchBlock]:
    """Bound a semantic block without reverting to one-record-per-line."""
    pieces: list[_SearchBlock] = []
    current: list[tuple[int, str]] = []
    current_chars = 0
    for line in block.lines:
        line_chars = len(line[1])
        if current and (
            len(current) >= MAX_SEARCH_CHUNK_LINES
            or current_chars + 1 + line_chars > MAX_SEARCH_CHUNK_CHARS
        ):
            pieces.append(_SearchBlock(block.heading, block.heading_path, tuple(current)))
            current = []
            current_chars = 0
        current.append(line)
        current_chars += line_chars + (1 if current_chars else 0)
    if current:
        pieces.append(_SearchBlock(block.heading, block.heading_path, tuple(current)))
    return pieces


def _search_entries(files: dict[str, bytes], filenames: tuple[str, ...]) -> list[Entry]:
    entries: list[Entry] = []
    total_bytes = 0
    for filename in filenames:
        try:
            text = files[filename].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise BaselineSearchError(f"baseline Markdown is not UTF-8: {filename}") from exc
        for block in _blocks_from_markdown(filename, text):
            for line_number, line in block.lines:
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
            for piece in _split_block(block):
                if len(entries) >= MAX_SEARCH_RECORDS:
                    raise BaselineSearchError(
                        f"baseline searchable record count exceeds {MAX_SEARCH_RECORDS}"
                    )
                content = "\n".join(line for _, line in piece.lines)
                line_start = piece.lines[0][0]
                line_end = piece.lines[-1][0]
                identifier = hashlib.sha256(
                    f"{filename}\0{piece.heading_path}\0{line_start}\0{content}".encode()
                ).hexdigest()
                entries.append(
                    Entry(
                        id=identifier,
                        title=f"{filename} {piece.heading_path}",
                        content=content,
                        kind=Kind.BEHAVIOR,
                        source=Source(
                            Channel.EVIDENCE,
                            filename,
                            symbol=piece.heading,
                            line_start=line_start,
                            line_end=line_end,
                        ),
                    )
                )
    return entries


def _coverage_search_intent(query: str, expansion: str = "") -> bool:
    text = f"{query}\n{expansion}".casefold()
    return _FILE_QUERY.search(text) is not None or any(
        term in text for term in _COVERAGE_QUERY_TERMS
    )


def _search_value_line(key: str, value: object) -> str:
    text = str(value)
    if key == "actions":
        text = " | ".join(item.strip() for item in text.splitlines() if item.strip())
    return f"{key}: {text}"


def _agent_search_entries(core: SemanticCore, *, include_coverage: bool = True) -> list[Entry]:
    """Build the transient Agent view directly from the replayed SemanticCore."""
    document_set = build_document_ast_set(core)
    family_paths = {
        (document.required_family or document.optional_family): document.path
        for document in document_set.documents
    }
    evidence = {item.evidence_id: item for item in core.evidence}
    entries: list[Entry] = []
    total_bytes = 0
    for record in core.records:
        if record.row_kind is DocumentRowKind.SOURCE_COVERAGE and not include_coverage:
            continue
        owner = CANONICAL_DOCUMENT_OWNER[record.row_kind]
        filename = family_paths.get(owner)
        if filename is None:
            raise BaselineSearchError(
                f"Agent record lacks its replay-verified human document owner: {record.record_id}"
            )
        source = evidence.get(record.evidence_id)
        if source is None:
            raise BaselineSearchError(
                f"Agent record lacks replay-verified source evidence: {record.record_id}"
            )
        values = {
            projected.pointer.removeprefix("/"): projected.value for projected in record.values
        }
        preferred = next(
            (
                str(values[key])
                for key in (
                    "statement",
                    "description",
                    "signature",
                    "title",
                    "name",
                    "qualified_name",
                    "path",
                    "table",
                    "external_id",
                    "issue",
                )
                if values.get(key) not in {None, ""}
            ),
            record.row_kind.value,
        )
        lines = [
            f"类型: {record.row_kind.value}",
            f"事实: {preferred}",
            *(_search_value_line(key, value) for key, value in values.items()),
            (f"源码: {source.source.repository_alias}:{source.source.path}#L{source.source.line}"),
        ]
        content = "\n".join(dict.fromkeys(lines))
        total_bytes += len(content.encode("utf-8"))
        if total_bytes > MAX_SEARCH_TEXT_BYTES:
            raise BaselineSearchError(
                f"baseline Agent view exceeds {MAX_SEARCH_TEXT_BYTES} searchable bytes"
            )
        if len(entries) >= MAX_SEARCH_RECORDS:
            raise BaselineSearchError(
                f"baseline Agent view exceeds {MAX_SEARCH_RECORDS} searchable records"
            )
        section = SECTION_ROUTES[record.row_kind][1]
        entries.append(
            Entry(
                id=record.record_id,
                title=f"{filename} Agent视图 {section} {preferred}",
                content=content,
                kind=Kind.BEHAVIOR,
                source=Source(
                    Channel.EVIDENCE,
                    filename,
                    symbol=f"Agent视图 · {section}",
                ),
            )
        )
    return entries


def _best_snippet(content: str, query: str, expansion: str) -> str:
    """Choose the matching visible line instead of truncating a whole section."""
    lines = [
        fragment.strip()
        for line in content.splitlines()
        for fragment in re.split(r"<br\s*/?>", line, flags=re.IGNORECASE)
        if fragment.strip()
    ]
    if not lines:
        return ""
    original_terms = set(_terms(query))
    expansion_terms = set(_terms(expansion))
    query_folded = query.strip().casefold()

    def relevance(line: str) -> tuple[float, int]:
        line_terms = set(_terms(line))
        score = 4.0 * len(line_terms & original_terms)
        score += len(line_terms & expansion_terms)
        if line.startswith("事实:"):
            score += 3.0
        if query_folded and query_folded in line.casefold():
            score += 100.0
        return score, -len(line)

    selected = max(lines, key=relevance)
    cleaned = html.unescape(" ".join(selected.replace("|", " ").replace("`", "").split()))
    if len(cleaned) <= MAX_SEARCH_SNIPPET_CHARS:
        return cleaned

    folded = cleaned.casefold()
    offsets = [folded.find(term.casefold()) for term in [query, *_terms(query), *_terms(expansion)]]
    offsets = [offset for offset in offsets if offset >= 0]
    center = min(offsets) if offsets else 0
    start = max(0, center - MAX_SEARCH_SNIPPET_CHARS // 3)
    end = min(len(cleaned), start + MAX_SEARCH_SNIPPET_CHARS)
    start = max(0, end - MAX_SEARCH_SNIPPET_CHARS)
    snippet = cleaned[start:end].strip()
    if start:
        snippet = "..." + snippet[3:].lstrip()
    if end < len(cleaned):
        snippet = snippet[:-3].rstrip() + "..."
    return snippet


def search_baseline(
    export_path: Path,
    query: str,
    *,
    limit: int = 10,
    expansion: str = "",
) -> tuple[BaselineSearchHit, ...]:
    """Verify a package, then rank its separate SemanticCore Agent view."""
    if limit < 1:
        raise BaselineSearchError("search limit must be at least 1")
    if not query.strip():
        raise BaselineSearchError("search query must not be empty")
    if len(expansion) > MAX_SEARCH_EXPANSION_CHARS:
        raise BaselineSearchError(
            f"search expansion exceeds {MAX_SEARCH_EXPANSION_CHARS} characters"
        )
    if any(ord(character) < 32 and character not in "\t\n\r" for character in expansion):
        raise BaselineSearchError("search expansion contains a control character")
    try:
        bundle = load_replayed_capsule_export(export_path)
    except CapsuleReplayError as exc:
        raise BaselineSearchError(str(exc)) from exc
    entries = _agent_search_entries(
        bundle.core,
        include_coverage=_coverage_search_intent(query, expansion),
    )
    ranked = rank_entries(
        query,
        entries,
        limit=max(limit * 4, limit),
        expansion=expansion.strip(),
    )
    hits: list[BaselineSearchHit] = []
    seen_snippets: set[str] = set()
    for result in ranked:
        entry = result.entry
        snippet = _best_snippet(entry.content, query, expansion)
        snippet_key = snippet.casefold()
        if snippet_key in seen_snippets:
            continue
        seen_snippets.add(snippet_key)
        expanded_only = result.original_score <= 0 < result.expansion_score
        confidence = (
            "low"
            if expanded_only
            else "high"
            if result.original_coverage >= 0.5
            or query.strip().casefold() in entry.content.casefold()
            else "medium"
        )
        hits.append(
            BaselineSearchHit(
                entry.source.locator,
                entry.source.symbol or "-",
                result.adjusted_score,
                snippet,
                confidence,
                expanded_only,
            )
        )
        if len(hits) >= limit:
            break
    return tuple(hits)
