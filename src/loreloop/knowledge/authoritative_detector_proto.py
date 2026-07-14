"""Static protobuf schema and RPC detector."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .authoritative_records import (
    DetectionError,
    DetectionReport,
    InterfaceRecord,
    ParameterRecord,
    SourceRef,
    SymbolRecord,
)

_PACKAGE = re.compile(r"\bpackage\s+([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*;")
_BLOCK = re.compile(r"\b(?P<kind>message|enum|service)\s+(?P<name>[A-Za-z_]\w*)\s*\{")
_FIELD = re.compile(
    r"\b(?:(?P<label>repeated|optional|required)\s+)?"
    + r"(?P<type>map\s*<\s*[\w.]+\s*,\s*[\w.]+\s*>|[.A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)"
    + r"\s+(?P<name>[A-Za-z_]\w*)\s*=\s*(?P<number>\d+)\s*(?:\[[^;]*\])?\s*;"
)
_ENUM_VALUE = re.compile(r"\b([A-Za-z_]\w*)\s*=\s*(-?\d+)\s*(?:\[[^;]*\])?\s*;")
_RPC = re.compile(
    r"\brpc\s+(?P<name>[A-Za-z_]\w*)\s*\(\s*(?P<input_stream>stream\s+)?"
    + r"(?P<input>[.A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*\)\s+returns\s*\(\s*"
    + r"(?P<output_stream>stream\s+)?(?P<output>[.A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*\)",
)


@dataclass(frozen=True, slots=True)
class _Block:
    kind: str
    name: str
    start: int
    opening: int
    closing: int


def _masked(source: str) -> str:
    result = list(source)
    index = 0
    while index < len(source):
        if source.startswith("//", index):
            end = source.find("\n", index)
            end = len(source) if end < 0 else end
        elif source.startswith("/*", index):
            end = source.find("*/", index + 2)
            if end < 0:
                raise DetectionError("unterminated protobuf block comment")
            end += 2
        elif source[index] in {'"', "'"}:
            quote = source[index]
            end = index + 1
            while end < len(source):
                if source[end] == quote and source[end - 1] != "\\":
                    break
                end += 1
            if end == len(source):
                raise DetectionError("unterminated protobuf string")
            end += 1
        else:
            index += 1
            continue
        for position in range(index, end):
            if result[position] != "\n":
                result[position] = " "
        index = end
    return "".join(result)


def _closing(source: str, opening: int) -> int:
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return index
    raise DetectionError("protobuf block has no closing brace")


def _blocks(source: str) -> tuple[_Block, ...]:
    blocks: list[_Block] = []
    for match in _BLOCK.finditer(source):
        opening = match.end() - 1
        blocks.append(
            _Block(match.group("kind"), match.group("name"), match.start(), opening, _closing(source, opening))
        )
    return tuple(blocks)


def _qualified(package: str | None, name: str) -> str:
    return name if package is None else f"{package}.{name}"


def _symbols(
    masked: str,
    source: str,
    alias: str,
    path: str,
    package: str | None,
    blocks: tuple[_Block, ...],
) -> tuple[SymbolRecord, ...]:
    records: list[SymbolRecord] = []
    for block in blocks:
        if block.kind == "service":
            continue
        body = masked[block.opening + 1 : block.closing]
        if block.kind == "message":
            fields = tuple(_FIELD.finditer(body))
            stray = re.search(r"\b(?:repeated|optional|required)\b[^;{}]*;", body)
            if stray is not None and not any(item.start() <= stray.start() < item.end() for item in fields):
                raise DetectionError(f"invalid protobuf field in message {block.name}")
            values = ", ".join(
                f"{item.group('name')}:{item.group('type').replace(' ', '')}"
                + f"{'[]' if item.group('label') == 'repeated' else ''}={item.group('number')}"
                for item in fields
            )
            signature = f"message {block.name}({values})"
        else:
            values = ", ".join(f"{item.group(1)}={item.group(2)}" for item in _ENUM_VALUE.finditer(body))
            signature = f"enum {block.name}({values})"
        records.append(
            SymbolRecord(
                "class",
                _qualified(package, block.name),
                signature,
                SourceRef(alias, path, source.count("\n", 0, block.start) + 1),
            )
        )
    return tuple(records)


def _interfaces(
    masked: str,
    source: str,
    alias: str,
    path: str,
    package: str | None,
    blocks: tuple[_Block, ...],
) -> tuple[InterfaceRecord, ...]:
    records: list[InterfaceRecord] = []
    for block in blocks:
        if block.kind != "service":
            continue
        body = masked[block.opening + 1 : block.closing]
        matches = tuple(_RPC.finditer(body))
        if len(matches) != len(re.findall(r"\brpc\b", body)):
            raise DetectionError(f"invalid protobuf RPC in service {block.name}")
        service = _qualified(package, block.name)
        for match in matches:
            terminator = match.end()
            while terminator < len(body) and body[terminator].isspace():
                terminator += 1
            if terminator >= len(body) or body[terminator] not in ";{":
                raise DetectionError(f"invalid protobuf RPC in service {block.name}")
            if body[terminator] == "{":
                _ = _closing(body, terminator)
            input_type = match.group("input")
            if match.group("input_stream"):
                input_type = f"stream {input_type}"
            output_type = match.group("output")
            if match.group("output_stream"):
                output_type = f"stream {output_type}"
            records.append(
                InterfaceRecord(
                    "http",
                    f"{service}.{match.group('name')}",
                    "RPC",
                    f"/{service}/{match.group('name')}",
                    (ParameterRecord("request", input_type, True),),
                    output_type,
                    SourceRef(
                        alias,
                        path,
                        source.count("\n", 0, block.opening + 1 + match.start()) + 1,
                    ),
                )
            )
    return tuple(records)


def detect_proto_source(source: str, repository_alias: str, path: str) -> DetectionReport:
    """Extract protobuf message, enum, and service RPC contracts without protoc."""
    masked = _masked(source)
    if masked.count("{") != masked.count("}"):
        raise DetectionError(f"invalid protobuf braces: {path}")
    package_match = _PACKAGE.search(masked)
    package = None if package_match is None else package_match.group(1)
    blocks = _blocks(masked)
    if re.search(r"\b(?:message|enum|service)\b", masked) and not blocks:
        raise DetectionError(f"invalid protobuf source: {path}")
    return DetectionReport(
        interfaces=_interfaces(masked, source, repository_alias, path, package, blocks),
        symbols=_symbols(masked, source, repository_alias, path, package, blocks),
    )
