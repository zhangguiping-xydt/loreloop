"""Deterministic GraphQL SDL detector without schema execution."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .authoritative_records import (
    ContractFieldRecord,
    DetectionError,
    DetectionReport,
    InterfaceRecord,
    ParameterRecord,
    SourceRef,
    SymbolRecord,
)

_BLOCK = re.compile(
    r"\b(?:extend\s+)?(?P<kind>type|interface|input|enum)\s+(?P<name>[_A-Za-z]\w*)[^{}]*\{"
)
_SCALAR = re.compile(r"\b(?P<kind>scalar|union)\s+(?P<name>[_A-Za-z]\w*)")
_SCHEMA = re.compile(r"\bschema\s*\{")
_ROOT_ENTRY = re.compile(r"\b(query|mutation|subscription)\s*:\s*([_A-Za-z]\w*)")
_NAME = re.compile(r"[_A-Za-z]\w*")
_TYPE = re.compile(r"(?:\[\s*)*[_A-Za-z]\w*(?:\s*!?\s*\])*\s*!?")


@dataclass(frozen=True, slots=True)
class _Field:
    name: str
    parameters: tuple[ParameterRecord, ...]
    return_type: str
    offset: int


def _masked(source: str) -> str:
    result = list(source)
    index = 0
    while index < len(source):
        if source.startswith("#", index):
            end = source.find("\n", index)
            end = len(source) if end < 0 else end
            result[index:end] = " " * (end - index)
            index = end
        elif source.startswith('"""', index):
            end = source.find('"""', index + 3)
            if end < 0:
                raise DetectionError("unterminated GraphQL block string")
            end += 3
            for position in range(index, end):
                if result[position] != "\n":
                    result[position] = " "
            index = end
        elif source[index] == '"':
            end = index + 1
            while end < len(source):
                if source[end] == '"' and source[end - 1] != "\\":
                    break
                end += 1
            if end == len(source):
                raise DetectionError("unterminated GraphQL string")
            for position in range(index, end + 1):
                if result[position] != "\n":
                    result[position] = " "
            index = end + 1
        else:
            index += 1
    return "".join(result)


def _closing(source: str, opening: int, left: str = "{", right: str = "}") -> int:
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == left:
            depth += 1
        elif source[index] == right:
            depth -= 1
            if depth == 0:
                return index
    raise DetectionError(f"GraphQL {left} has no matching {right}")


def _validate(source: str) -> None:
    pairs = {"}": "{", ")": "(", "]": "["}
    stack: list[str] = []
    for character in source:
        if character in "{([":
            stack.append(character)
        elif character in pairs and (not stack or stack.pop() != pairs[character]):
            raise DetectionError("unbalanced GraphQL delimiters")
    if stack:
        raise DetectionError("unbalanced GraphQL delimiters")


def _type(source: str, index: int) -> tuple[str, int]:
    match = _TYPE.match(source, index)
    if match is None:
        raise DetectionError("GraphQL field has no valid type")
    return re.sub(r"\s+", "", match.group(0)), match.end()


def _parameters(source: str) -> tuple[ParameterRecord, ...]:
    records: list[ParameterRecord] = []
    index = 0
    while index < len(source):
        match = _NAME.search(source, index)
        if match is None:
            break
        index = match.end()
        while index < len(source) and source[index].isspace():
            index += 1
        if index >= len(source) or source[index] != ":":
            raise DetectionError(f"invalid GraphQL argument {match.group(0)}")
        index += 1
        while index < len(source) and source[index].isspace():
            index += 1
        annotation, index = _type(source, index)
        records.append(ParameterRecord(match.group(0), annotation, annotation.endswith("!")))
        depth = 0
        while index < len(source):
            character = source[index]
            if character in "[{(":
                depth += 1
            elif character in "]})":
                depth -= 1
            elif depth == 0 and (character == "," or character == "\n"):
                index += 1
                break
            index += 1
    return tuple(records)


def _fields(body: str) -> tuple[_Field, ...]:
    fields: list[_Field] = []
    index = 0
    while index < len(body):
        match = _NAME.search(body, index)
        if match is None:
            break
        name = match.group(0)
        index = match.end()
        while index < len(body) and body[index].isspace():
            index += 1
        parameters: tuple[ParameterRecord, ...] = ()
        if index < len(body) and body[index] == "(":
            closing = _closing(body, index, "(", ")")
            parameters = _parameters(body[index + 1 : closing])
            index = closing + 1
            while index < len(body) and body[index].isspace():
                index += 1
        if index >= len(body) or body[index] != ":":
            raise DetectionError(f"GraphQL field {name} has no type separator")
        index += 1
        while index < len(body) and body[index].isspace():
            index += 1
        return_type, index = _type(body, index)
        fields.append(_Field(name, parameters, return_type, match.start()))
        if index < len(body) and body[index] == "=":
            end = body.find("\n", index)
            index = len(body) if end < 0 else end + 1
        while index < len(body) and body[index] == "@":
            directive = _NAME.match(body, index + 1)
            if directive is None:
                raise DetectionError("invalid GraphQL directive")
            index = directive.end()
            while index < len(body) and body[index].isspace():
                index += 1
            if index < len(body) and body[index] == "(":
                index = _closing(body, index, "(", ")") + 1
            while index < len(body) and body[index].isspace():
                index += 1
    return tuple(fields)


def _root_types(masked: str) -> dict[str, str]:
    roots = {"Query": "QUERY", "Mutation": "MUTATION", "Subscription": "SUBSCRIPTION"}
    match = _SCHEMA.search(masked)
    if match is None:
        return roots
    closing = _closing(masked, match.end() - 1)
    for entry in _ROOT_ENTRY.finditer(masked[match.end() : closing]):
        roots[entry.group(2)] = entry.group(1).upper()
    return roots


def _signature(field: _Field) -> str:
    parameters = ", ".join(
        value.name + ":" + (value.annotation or "-") for value in field.parameters
    )
    arguments = f"({parameters})" if parameters else ""
    return f"{field.name}{arguments}:{field.return_type}"


def detect_graphql_source(source: str, repository_alias: str, path: str) -> DetectionReport:
    """Extract GraphQL root operations and declared SDL types from committed text."""
    masked = _masked(source)
    _validate(masked)
    roots = _root_types(masked)
    interfaces: list[InterfaceRecord] = []
    contract_fields: list[ContractFieldRecord] = []
    symbols: list[SymbolRecord] = []
    consumed: list[tuple[int, int]] = []
    for match in _BLOCK.finditer(masked):
        opening = match.end() - 1
        closing = _closing(masked, opening)
        consumed.append((match.start(), closing + 1))
        body = masked[opening + 1 : closing]
        fields = _fields(body) if match.group("kind") != "enum" else ()
        name = match.group("name")
        signature = f"{match.group('kind')} {name}"
        if fields:
            values = "; ".join(_signature(field) for field in fields)
            signature += f" {{ {values} }}"
        symbols.append(
            SymbolRecord(
                "class",
                name,
                signature,
                SourceRef(repository_alias, path, source.count("\n", 0, match.start()) + 1),
            )
        )
        if match.group("kind") != "enum":
            contract_fields.extend(
                ContractFieldRecord(
                    name,
                    field.name,
                    field.return_type,
                    field.return_type.endswith("!"),
                    not field.return_type.endswith("!"),
                    SourceRef(
                        repository_alias,
                        path,
                        source.count("\n", 0, opening + 1 + field.offset) + 1,
                    ),
                )
                for field in fields
            )
        operation = roots.get(name)
        if operation is not None:
            interfaces.extend(
                InterfaceRecord(
                    "http",
                    f"{name}.{field.name}",
                    f"GRAPHQL_{operation}",
                    "/graphql",
                    field.parameters,
                    field.return_type,
                    SourceRef(
                        repository_alias,
                        path,
                        source.count("\n", 0, opening + 1 + field.offset) + 1,
                    ),
                )
                for field in fields
            )
    for match in _SCALAR.finditer(masked):
        if any(start <= match.start() < end for start, end in consumed):
            continue
        symbols.append(
            SymbolRecord(
                "class",
                match.group("name"),
                match.group(0),
                SourceRef(repository_alias, path, source.count("\n", 0, match.start()) + 1),
            )
        )
    if re.search(r"\b(?:type|interface|input|enum|schema)\b", masked) and not symbols:
        raise DetectionError(f"invalid GraphQL SDL source: {path}")
    return DetectionReport(
        interfaces=tuple(interfaces),
        contract_fields=tuple(contract_fields),
        symbols=tuple(symbols),
    )
