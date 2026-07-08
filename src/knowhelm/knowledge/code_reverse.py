"""Code channel of knowledge reverse-engineering.

Pipeline: scan repo -> extract assertions (JSON, schema-validated) -> classify
kind (separate step) -> produce Entry objects anchored to the current commit.

Extraction and classification are deliberately separate LLM steps, and both
must return valid JSON or the batch fails; there are no keyword fallbacks.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..agents import AgentRunner
from .model import Channel, Entry, Kind, Source

DEFAULT_EXTENSIONS = (".py", ".ts", ".tsx", ".js", ".go", ".rs", ".java", ".sql")
_MAX_FILE_BYTES = 60_000
_MAX_BATCH_FILES = 8

_EXTRACT_PROMPT = """\
You are extracting project knowledge from source code.

Read the files below. Output a JSON array (and nothing else, no markdown fence).
Each element is one atomic, assertion-level fact a developer would need to know
before changing this project:

  {{"claim": "<one factual sentence>", "title": "<short label>", "file": "<path from the header lines>"}}

Rules:
- Only facts evidenced by the code shown. Never speculate.
- One assertion per element; split compound statements.
- "file" must be exactly one of the paths given.
- 3 to 15 elements per file, fewer if the file is trivial.

{files_block}
"""

_CLASSIFY_PROMPT = """\
Classify each knowledge assertion into exactly one kind:

- requirement: what the product must do for users
- interface: API/route/CLI/function contracts between components
- architecture: module structure, layering, technology choices
- behavior: observable runtime behavior and flows
- constraint: limits, invariants, conventions that must hold
- acceptance: how correctness is checked (tests, criteria)

Input is a JSON array of {{"id": ..., "claim": ...}}. Output a JSON array
(nothing else, no markdown fence) of {{"id": ..., "kind": "<one of the six>"}},
same length, same ids.

{assertions_json}
"""


class ExtractionError(Exception):
    pass


@dataclass(frozen=True)
class RawAssertion:
    claim: str
    title: str
    file: str


def scan_repo(repo: Path, extensions: tuple[str, ...] = DEFAULT_EXTENSIONS) -> list[Path]:
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    return [
        repo / line
        for line in tracked
        if line.endswith(extensions) and (repo / line).stat().st_size <= _MAX_FILE_BYTES
    ]


def repo_head(repo: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


def changed_files(
    repo: Path, base: str, extensions: tuple[str, ...] = DEFAULT_EXTENSIONS
) -> list[Path]:
    """Source files changed between ``base`` and HEAD, filtered like scan_repo.
    Deleted files are excluded — there is nothing left to reverse."""
    lines = subprocess.run(
        ["git", "diff", "--name-only", f"{base}..HEAD"],
        cwd=repo, capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    return [
        repo / line
        for line in lines
        if line.endswith(extensions)
        and (repo / line).exists()
        and (repo / line).stat().st_size <= _MAX_FILE_BYTES
    ]


def extract_assertions(
    runner: AgentRunner, repo: Path, files: list[Path]
) -> list[RawAssertion]:
    valid_paths = {str(f.relative_to(repo)) for f in files}
    blocks = []
    for f in files:
        rel = f.relative_to(repo)
        blocks.append(f"=== FILE: {rel} ===\n{f.read_text(encoding='utf-8', errors='replace')}")
    raw = runner.run(_EXTRACT_PROMPT.format(files_block="\n\n".join(blocks)))
    items = parse_json_array(raw, required_keys={"claim", "title", "file"})
    assertions = []
    for item in items:
        if item["file"] not in valid_paths:
            raise ExtractionError(f"extraction referenced unknown file: {item['file']!r}")
        assertions.append(
            RawAssertion(claim=item["claim"], title=item["title"], file=item["file"])
        )
    return assertions


def classify_claims(runner: AgentRunner, claims: list[str]) -> list[Kind]:
    payload = json.dumps(
        [{"id": i, "claim": c} for i, c in enumerate(claims)], ensure_ascii=False
    )
    raw = runner.run(_CLASSIFY_PROMPT.format(assertions_json=payload))
    items = parse_json_array(raw, required_keys={"id", "kind"})
    if len(items) != len(claims):
        raise ExtractionError(
            f"classification returned {len(items)} items for {len(claims)} assertions"
        )
    kinds: dict[int, Kind] = {}
    for item in items:
        try:
            kinds[int(item["id"])] = Kind(item["kind"])
        except (ValueError, KeyError) as exc:
            raise ExtractionError(f"invalid classification item: {item!r}") from exc
    if set(kinds) != set(range(len(claims))):
        raise ExtractionError("classification ids do not match assertion ids")
    return [kinds[i] for i in range(len(claims))]


def classify_assertions(
    runner: AgentRunner, assertions: list[RawAssertion]
) -> list[Kind]:
    return classify_claims(runner, [a.claim for a in assertions])


def reverse_code(
    runner: AgentRunner, repo: Path, files: list[Path] | None = None
) -> list[Entry]:
    repo = repo.resolve()
    head = repo_head(repo)
    targets = files if files is not None else scan_repo(repo)
    entries: list[Entry] = []
    for start in range(0, len(targets), _MAX_BATCH_FILES):
        batch = targets[start : start + _MAX_BATCH_FILES]
        assertions = extract_assertions(runner, repo, batch)
        if not assertions:
            continue
        kinds = classify_assertions(runner, assertions)
        for assertion, kind in zip(assertions, kinds):
            entries.append(
                Entry(
                    title=assertion.title,
                    content=assertion.claim,
                    kind=kind,
                    source=Source(
                        channel=Channel.CODE,
                        locator=f"{assertion.file}@{head}",
                        snapshot_ref=head,
                    ),
                )
            )
    return entries


def parse_json_array(raw: str, required_keys: set[str]) -> list[dict]:
    text = raw.strip()
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1 or end < start:
        raise ExtractionError(f"agent output is not a JSON array: {text[:200]!r}")
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ExtractionError(f"agent output is invalid JSON: {exc}") from exc
    if not isinstance(data, list):
        raise ExtractionError("agent output is not a list")
    for item in data:
        if not isinstance(item, dict) or not required_keys.issubset(item):
            raise ExtractionError(f"item missing required keys {required_keys}: {item!r}")
    return data
