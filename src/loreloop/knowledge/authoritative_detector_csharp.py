"""Deterministic shallow detector for C# source files."""

from __future__ import annotations

import re
from typing import Final

from .authoritative_detector_common import mask_c_like_comments, source_ref
from .authoritative_records import (
    ConfigurationRecord,
    DependencyRecord,
    DetectionReport,
    ImplementationFactRecord,
    InterfaceRecord,
    ParameterRecord,
    SymbolRecord,
    UiSurfaceRecord,
)

_TYPE: Final = re.compile(
    r"(?m)^\s*(?:public\s+|private\s+|protected\s+|internal\s+|abstract\s+|"
    + r"sealed\s+|static\s+|partial\s+)*(?:class|interface|record|struct)\s+"
    + r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
)
_METHOD: Final = re.compile(
    r"(?m)^\s*(?:\[[^\n]+\]\s*)*(?:public|private|protected|internal)\s+"
    + r"(?:static\s+|virtual\s+|override\s+|abstract\s+|sealed\s+|async\s+)*"
    + r"(?P<return>[A-Za-z_][\w.<>,?\[\] ]*)\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
    + r"\s*\((?P<params>[^)]*)\)"
)
_MINIMAL_ROUTE: Final = re.compile(
    r"\b[A-Za-z_][\w.]*\.Map(?P<method>Get|Post|Put|Patch|Delete)\s*"
    + r"\(\s*\"(?P<path>[^\"]+)\"\s*,\s*(?P<handler>[A-Za-z_][\w.]*)"
)
_HTTP_ATTRIBUTE: Final = re.compile(
    r"\[\s*Http(?P<method>Get|Post|Put|Patch|Delete|Head|Options)"
    + r"(?:\s*\(\s*\"(?P<path>[^\"]*)\"[^)]*\))?\s*\]"
)
_NEXT_METHOD: Final = re.compile(
    r"(?:(?:public|private|protected|internal|static|virtual|override|async|sealed)\s+)+"
    + r"[A-Za-z_][\w.<>,?\[\] ]*\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\("
)
_CONTROLLER_PREFIX: Final = re.compile(
    r"\[\s*Route\s*\(\s*\"(?P<path>[^\"]*)\"\s*\)\s*\]"
    + r"(?:(?!\bclass\b).){0,400}\bclass\s+(?P<class>[A-Za-z_][A-Za-z0-9_]*)",
    re.DOTALL,
)
_ENV: Final = re.compile(
    r"\bEnvironment\.GetEnvironmentVariable\s*\(\s*"
    + r"\"(?P<key>[A-Za-z_][A-Za-z0-9_]*)\""
)
_USING: Final = re.compile(
    r"(?m)^\s*(?:global\s+)?using\s+(?:static\s+)?(?P<name>[A-Za-z_][\w.]*)\s*;"
)
_WEB_METHOD: Final = re.compile(r"\[\s*WebMethod(?:Attribute)?(?:\s*\([^]]*\))?\s*\]", re.I)
_APPSETTING: Final = re.compile(
    r"\b(?:ConfigurationManager|ConfigurationSettings)\.AppSettings\s*\[\s*"
    r'"(?P<key>[A-Za-z_][A-Za-z0-9_.:-]*)"\s*\]'
)
_KEY_LITERAL: Final = re.compile(
    r'(?m)^\s*(?:const\s+)?string\s+key[A-Za-z0-9_]*\s*=\s*"(?P<key>[A-Za-z_][A-Za-z0-9_.:-]*)"\s*;'
)
_UI_CLASS: Final = re.compile(
    r"(?m)^\s*(?:public\s+|private\s+|protected\s+|internal\s+|sealed\s+|partial\s+)*"
    r"class\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*:\s*"
    r"(?P<base>[A-Za-z_][\w.]*(?:Form|Page|UserControl))\b"
)
_DATA_CALL: Final = re.compile(
    r'\b(?P<receiver>[A-Za-z_][\w.]*)\.(?P<method>Select|Insert|Update|Delete|WriteTable)'
    r'\s*\(\s*"(?P<table>[A-Za-z_][A-Za-z0-9_$#.]{1,127})"',
    re.I,
)
_WEB_SERVICE_BASE: Final = re.compile(
    r"\bclass\s+[A-Za-z_][A-Za-z0-9_]*\s*:\s*[A-Za-z_][\w.]*WebService\b"
)


def _parameters(raw: str) -> tuple[ParameterRecord, ...]:
    records: list[ParameterRecord] = []
    for item in raw.split(","):
        text = re.sub(r"\[[^]]+\]\s*", "", item).strip()
        if not text:
            continue
        words = text.split()
        if len(words) < 2:
            continue
        name = words[-1].lstrip("@")
        annotation = " ".join(words[:-1]).replace("ref ", "").replace("out ", "").strip()
        records.append(ParameterRecord(name, annotation or None, "=" not in text))
    return tuple(records)


def _web_methods(source: str, alias: str, path: str) -> tuple[InterfaceRecord, ...]:
    records: list[InterfaceRecord] = []
    for attribute in _WEB_METHOD.finditer(source):
        method = _METHOD.search(source, attribute.end())
        if method is None or method.start() - attribute.end() > 800:
            continue
        records.append(
            InterfaceRecord(
                "http",
                method.group("name"),
                "SOAP",
                f"/{path.removesuffix('.cs')}#{method.group('name')}",
                _parameters(method.group("params")),
                method.group("return").strip(),
                source_ref(alias, path, source, attribute.start()),
            )
        )
    return tuple(records)


def _ui_surfaces(source: str, alias: str, path: str) -> tuple[UiSurfaceRecord, ...]:
    match = _UI_CLASS.search(source)
    lowered = path.lower()
    if match is None and not lowered.endswith((".aspx.cs", ".ascx.cs")):
        return ()
    name = match.group("name") if match is not None else path.rsplit("/", 1)[-1].split(".", 1)[0]
    actions = tuple(
        dict.fromkeys(
            method.group("name")
            for method in _METHOD.finditer(source)
            if method.group("name") == "Page_Load"
            or method.group("name").lower().startswith(("btn", "menu", "grid"))
            or method.group("name").lower().endswith(
                ("_click", "_command", "_changed", "_selectedindexchanged")
            )
        )
    )
    entry = path[:-3] if lowered.endswith((".aspx.cs", ".ascx.cs")) else path
    return (
        UiSurfaceRecord(
            name,
            "page",
            entry,
            actions,
            source_ref(alias, path, source, match.start() if match is not None else 0),
        ),
    )


def _implementation_facts(
    source: str, alias: str, path: str
) -> tuple[ImplementationFactRecord, ...]:
    subject = path.rsplit("/", 1)[-1].removesuffix(".cs")
    records: list[ImplementationFactRecord] = []
    for match in _DATA_CALL.finditer(source):
        receiver = match.group("receiver").lower()
        if not any(token in receiver for token in ("db", "database", "ado")):
            continue
        method = match.group("method").lower()
        predicate = "reads" if method == "select" else "writes"
        records.append(
            ImplementationFactRecord(
                subject,
                predicate,
                match.group("table").upper(),
                f"{match.group('receiver')}.{match.group('method')}",
                source_ref(alias, path, source, match.start()),
            )
        )
    if "ServiceBase" in source:
        offset = source.index("ServiceBase")
        records.append(
            ImplementationFactRecord(
                subject,
                "hosts",
                "Windows Service",
                None,
                source_ref(alias, path, source, offset),
            )
        )
    if path.lower().endswith(".asmx.cs") or _WEB_SERVICE_BASE.search(source) is not None:
        offset = source.find("WebService")
        records.append(
            ImplementationFactRecord(
                subject,
                "hosts",
                "ASMX Web Service",
                None,
                source_ref(alias, path, source, max(offset, 0)),
            )
        )
    return tuple(records)


def _join(prefix: str, suffix: str) -> str:
    combined = f"/{prefix.strip('/')}/{suffix.strip('/')}".replace("//", "/")
    return combined or "/"


def _interfaces(source: str, alias: str, path: str) -> tuple[InterfaceRecord, ...]:
    prefixes = tuple(_CONTROLLER_PREFIX.finditer(source))
    records = [
        InterfaceRecord(
            "http",
            match.group("handler"),
            match.group("method").upper(),
            match.group("path"),
            (),
            None,
            source_ref(alias, path, source, match.start()),
        )
        for match in _MINIMAL_ROUTE.finditer(source)
    ]
    for match in _HTTP_ATTRIBUTE.finditer(source):
        tail = source[match.end() : match.end() + 400]
        method = _NEXT_METHOD.search(tail)
        if method is None:
            continue
        preceding = [candidate for candidate in prefixes if candidate.end() <= match.start()]
        prefix = "" if not preceding else preceding[-1].group("path")
        if preceding:
            controller = preceding[-1].group("class")
            prefix = prefix.replace("[controller]", re.sub(r"Controller$", "", controller))
        records.append(
            InterfaceRecord(
                "http",
                method.group("name"),
                match.group("method").upper(),
                _join(prefix, match.group("path") or ""),
                (),
                None,
                source_ref(alias, path, source, match.start()),
            )
        )
    records.extend(_web_methods(source, alias, path))
    return tuple(records)


def detect_csharp_source(source: str, repository_alias: str, path: str) -> DetectionReport:
    """Extract explicit C# symbols, ASP.NET routes, env reads, and namespace uses."""
    masked = mask_c_like_comments(source)
    symbols = [
        SymbolRecord(
            "class",
            match.group("name"),
            match.group("name"),
            source_ref(repository_alias, path, source, match.start()),
        )
        for match in _TYPE.finditer(masked)
    ]
    symbols.extend(
        SymbolRecord(
            "function",
            match.group("name"),
            f"{match.group('name')}({match.group('params').strip()})",
            source_ref(repository_alias, path, source, match.start()),
        )
        for match in _METHOD.finditer(masked)
    )
    configurations = tuple(
        ConfigurationRecord(
            match.group("key"),
            None,
            True,
            False,
            source_ref(repository_alias, path, source, match.start()),
        )
        for match in (*tuple(_ENV.finditer(masked)), *tuple(_APPSETTING.finditer(masked)), *tuple(_KEY_LITERAL.finditer(masked)))
    )
    dependencies = tuple(
        DependencyRecord(
            match.group("name"),
            None,
            "csharp_using",
            source_ref(repository_alias, path, source, match.start()),
        )
        for match in _USING.finditer(masked)
    )
    return DetectionReport(
        interfaces=_interfaces(masked, repository_alias, path),
        symbols=tuple(symbols),
        ui_surfaces=_ui_surfaces(masked, repository_alias, path),
        configurations=configurations,
        dependencies=dependencies,
        implementation_facts=_implementation_facts(masked, repository_alias, path),
    )
