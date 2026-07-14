"""Deterministic shallow detector for Java and Kotlin source files."""

from __future__ import annotations

import re
from typing import Final

from .authoritative_detector_common import mask_c_like_comments, source_ref
from .authoritative_records import (
    ConfigurationRecord,
    DependencyRecord,
    DetectionReport,
    InterfaceRecord,
    ParameterRecord,
    SymbolRecord,
)

_CLASS: Final = re.compile(
    r"(?m)^\s*(?:public\s+|private\s+|protected\s+|internal\s+|open\s+|abstract\s+|"
    + r"sealed\s+|data\s+|final\s+)*(?:class|interface|object|record)\s+"
    + r"(?P<name>[A-Za-z_$][\w$]*)"
)
_JAVA_METHOD: Final = re.compile(
    r"(?m)^\s*(?:@[\w$.]+(?:\([^\n]*\))?\s*)*"
    + r"(?:public|private|protected)\s+(?:static\s+)?(?:final\s+)?"
    + r"(?P<return>[\w$.<>?\[\], ]+)\s+(?P<name>[A-Za-z_$][\w$]*)\s*"
    + r"\((?P<params>[^)]*)\)\s*(?:throws\s+[^{]+)?\{"
)
_KOTLIN_FUNCTION: Final = re.compile(
    r"(?m)^\s*(?:@[\w$.]+(?:\([^\n]*\))?\s*)*"
    + r"(?:public\s+|private\s+|protected\s+|internal\s+|open\s+|override\s+|suspend\s+)*"
    + r"fun\s+(?P<name>[A-Za-z_$][\w$]*)\s*\((?P<params>[^)]*)\)"
    + r"(?:\s*:\s*(?P<return>[^={\n]+))?"
)
_IMPORT: Final = re.compile(r"(?m)^\s*import\s+(?:static\s+)?(?P<name>[\w$.]+)")
_ENV: Final = re.compile(r"\bSystem\.getenv\s*\(\s*\"(?P<key>[A-Za-z_][A-Za-z0-9_]*)\"")
_PREFIX: Final = re.compile(
    r"@RequestMapping\s*\(\s*(?:(?:value|path)\s*=\s*)?\"(?P<path>[^\"]*)\"[^)]*\)"
    + r"(?:(?!\bclass\b).){0,400}\bclass\s+[A-Za-z_$][\w$]*",
    re.DOTALL,
)
_SPRING_ROUTE: Final = re.compile(
    r"@(?P<kind>GetMapping|PostMapping|PutMapping|PatchMapping|DeleteMapping|RequestMapping)"
    + r"\s*(?:\((?P<args>[^)]*)\))?"
)
_ROUTE_PATH: Final = re.compile(r"(?:(?:value|path)\s*=\s*)?\"(?P<path>[^\"]*)\"")
_REQUEST_METHOD: Final = re.compile(r"RequestMethod\.(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)")
_NEXT_FUNCTION: Final = re.compile(
    r"(?:(?:public|private|protected|internal|open|override|suspend|static|final)\s+)*"
    + r"(?:fun\s+|[\w$.<>?\[\], ]+\s+)(?P<name>[A-Za-z_$][\w$]*)\s*\("
)
_JAVA_ROUTE_FUNCTION: Final = re.compile(
    r"(?m)^\s*(?:@[\w$.]+(?:\([^\n]*\))?\s*)*"
    + r"(?:public|private|protected)\s+(?:static\s+)?(?:final\s+)?"
    + r"(?P<return>[\w$.<>?\[\], ]+)\s+(?P<name>[A-Za-z_$][\w$]*)\s*\("
)
_KOTLIN_ROUTE_FUNCTION: Final = re.compile(
    r"(?m)^\s*(?:@[\w$.]+(?:\([^\n]*\))?\s*)*"
    + r"(?:public\s+|private\s+|protected\s+|internal\s+|open\s+|override\s+|"
    + r"suspend\s+)*fun\s+(?P<name>[A-Za-z_$][\w$]*)\s*\("
)
_KTOR_ROUTE: Final = re.compile(
    r"(?m)^\s*(?P<method>get|post|put|patch|delete|head|options)\s*"
    + r"\(\s*\"(?P<path>[^\"]+)\"\s*\)\s*\{"
)
_METHODS: Final = {
    "GetMapping": "GET",
    "PostMapping": "POST",
    "PutMapping": "PUT",
    "PatchMapping": "PATCH",
    "DeleteMapping": "DELETE",
}


def _join(prefix: str, path: str) -> str:
    combined = f"/{prefix.strip('/')}/{path.strip('/')}".replace("//", "/")
    return combined if combined != "" else "/"


def _split_parameters(raw: str) -> tuple[str, ...]:
    items: list[str] = []
    start = 0
    depth = 0
    for index, character in enumerate(raw):
        if character in "<([":
            depth += 1
        elif character in ">)]" and depth:
            depth -= 1
        elif character == "," and depth == 0:
            items.append(raw[start:index].strip())
            start = index + 1
    items.append(raw[start:].strip())
    return tuple(item for item in items if item)


def _parameters(raw: str, *, kotlin: bool) -> tuple[ParameterRecord, ...]:
    parameters: list[ParameterRecord] = []
    for item in _split_parameters(raw):
        required = not bool(
            re.search(r"@Nullable\b|required\s*=\s*false|\?\s*(?:=|$)", item)
        )
        without_annotations = re.sub(r"@[\w$.]+(?:\([^)]*\))?\s*", "", item).strip()
        without_annotations = re.sub(r"\bfinal\s+", "", without_annotations).strip()
        if kotlin and ":" in without_annotations:
            name, annotation = without_annotations.split(":", 1)
            annotation = annotation.split("=", 1)[0].strip()
            parameters.append(ParameterRecord(name.strip(), annotation or None, required))
            continue
        tokens = without_annotations.split()
        if len(tokens) < 2:
            continue
        parameters.append(ParameterRecord(tokens[-1], " ".join(tokens[:-1]), required))
    return tuple(parameters)


def _route_function(
    tail: str, *, kotlin: bool
) -> tuple[str, tuple[ParameterRecord, ...], str | None] | None:
    pattern = _KOTLIN_ROUTE_FUNCTION if kotlin else _JAVA_ROUTE_FUNCTION
    match = pattern.search(tail)
    if match is None or re.search(r"\b(?:class|interface|object|record)\b", tail[: match.start()]):
        return None
    opening = match.end() - 1
    depth = 0
    closing: int | None = None
    for index in range(opening, len(tail)):
        character = tail[index]
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth == 0:
                closing = index
                break
    if closing is None:
        return None
    remainder = tail[closing + 1 : closing + 200]
    if "{" not in remainder:
        return None
    if kotlin:
        return_match = re.match(r"\s*(?::\s*(?P<return>[^={\n]+))?", remainder)
        return_type = (
            (return_match.group("return") if return_match is not None else "") or ""
        ).strip() or None
    else:
        return_type = (match.group("return") or "").strip() or None
    return (
        match.group("name"),
        _parameters(tail[opening + 1 : closing], kotlin=kotlin),
        return_type,
    )


def _spring_interfaces(source: str, alias: str, path: str) -> tuple[InterfaceRecord, ...]:
    records: list[InterfaceRecord] = []
    prefixes = tuple(_PREFIX.finditer(source))
    kotlin = path.lower().endswith(".kt")
    for match in _SPRING_ROUTE.finditer(source):
        tail = source[match.end() : match.end() + 500]
        function = _route_function(tail, kotlin=kotlin)
        if function is None:
            fallback = _NEXT_FUNCTION.search(tail)
            if fallback is None or re.search(
                r"\b(?:class|interface|object|record)\b", tail[: fallback.start()]
            ):
                continue
            function = (fallback.group("name"), (), None)
        function_name, parameters, return_type = function
        args = match.group("args") or ""
        path_match = _ROUTE_PATH.search(args)
        route_path = "" if path_match is None else path_match.group("path")
        kind = match.group("kind")
        method_match = _REQUEST_METHOD.search(args)
        method = _METHODS.get(kind, method_match.group(1) if method_match else "ANY")
        preceding = [candidate for candidate in prefixes if candidate.end() <= match.start()]
        prefix = "" if not preceding else preceding[-1].group("path")
        records.append(
            InterfaceRecord(
                "http",
                function_name,
                method,
                _join(prefix, route_path),
                parameters,
                return_type,
                source_ref(alias, path, source, match.start()),
            )
        )
    return tuple(records)


def _symbols(source: str, alias: str, path: str) -> tuple[SymbolRecord, ...]:
    records = [
        SymbolRecord("class", match.group("name"), match.group("name"), source_ref(alias, path, source, match.start()))
        for match in _CLASS.finditer(source)
    ]
    functions = _KOTLIN_FUNCTION if path.lower().endswith(".kt") else _JAVA_METHOD
    for match in functions.finditer(source):
        name = match.group("name")
        records.append(
            SymbolRecord(
                "function",
                name,
                f"{name}({match.group('params').strip()})",
                source_ref(alias, path, source, match.start()),
            )
        )
    return tuple(records)


def detect_jvm_source(source: str, repository_alias: str, path: str) -> DetectionReport:
    """Extract only explicit JVM symbols, HTTP routes, env reads, and imports."""
    masked = mask_c_like_comments(source)
    interfaces = list(_spring_interfaces(masked, repository_alias, path))
    interfaces.extend(
        InterfaceRecord(
            "http",
            f"{match.group('method')} {match.group('path')}",
            match.group("method").upper(),
            match.group("path"),
            (),
            None,
            source_ref(repository_alias, path, source, match.start()),
        )
        for match in _KTOR_ROUTE.finditer(masked)
    )
    dependencies = tuple(
        DependencyRecord(
            match.group("name"),
            None,
            "jvm_import",
            source_ref(repository_alias, path, source, match.start()),
        )
        for match in _IMPORT.finditer(masked)
    )
    configurations = tuple(
        ConfigurationRecord(
            match.group("key"),
            None,
            True,
            False,
            source_ref(repository_alias, path, source, match.start()),
        )
        for match in _ENV.finditer(masked)
    )
    return DetectionReport(
        interfaces=tuple(interfaces),
        symbols=_symbols(masked, repository_alias, path),
        configurations=configurations,
        dependencies=dependencies,
    )
