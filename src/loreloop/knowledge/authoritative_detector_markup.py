"""Deterministic extraction for legacy ASP.NET markup, HTML, RESX, and XML."""

from __future__ import annotations

import html
import re
from pathlib import PurePosixPath
from typing import Final

from .authoritative_records import (
    ConfigurationRecord,
    DetectionReport,
    ImplementationFactRecord,
    SourceRef,
    UiSurfaceRecord,
)

_MARKUP_SUFFIXES: Final = (".aspx", ".ascx", ".master", ".html", ".htm", ".resx", ".xml")
_TAG: Final = re.compile(
    r"<(?P<tag>[A-Za-z][A-Za-z0-9_.:-]*)(?P<attrs>(?:\s+[^<>]*?)?)(?:/?>)",
    re.DOTALL,
)
_ATTRIBUTE: Final = re.compile(
    r"(?P<name>[A-Za-z_:][A-Za-z0-9_.:-]*)\s*=\s*(?P<quote>['\"])(?P<value>.*?)(?P=quote)",
    re.DOTALL,
)
_DIRECTIVE: Final = re.compile(
    r"<%@\s*(?P<kind>Page|Control|Master)\b(?P<attrs>.*?)%>", re.I | re.S
)
_TITLE: Final = re.compile(r"<title\b[^>]*>(?P<value>.*?)</title\s*>", re.I | re.S)
_RESX_DATA: Final = re.compile(r"<data\b(?P<attrs>[^>]*)>(?P<body>.*?)</data\s*>", re.I | re.S)
_RESX_VALUE: Final = re.compile(r"<value\b[^>]*>(?P<value>.*?)</value\s*>", re.I | re.S)
_TEXT: Final = re.compile(r"<[^>]+>")
_INTERACTIVE_TAGS: Final = frozenset(
    {
        "a",
        "button",
        "form",
        "input",
        "select",
        "textarea",
        "asp:button",
        "asp:checkbox",
        "asp:dropdownlist",
        "asp:gridview",
        "asp:hyperlink",
        "asp:imagebutton",
        "asp:linkbutton",
        "asp:listbox",
        "asp:radiobutton",
        "asp:repeater",
        "asp:textbox",
    }
)
_DISPLAY_ATTRIBUTES: Final = ("text", "title", "value", "tooltip", "headertext")
_EVENT_ATTRIBUTES: Final = frozenset(
    {
        "onclick",
        "oncommand",
        "onchange",
        "oncheckedchanged",
        "oninit",
        "onitemcommand",
        "onload",
        "onpageindexchanging",
        "onrowcommand",
        "onselectedindexchanged",
        "onsubmit",
        "ontextchanged",
    }
)
_VALID_ACTION: Final = re.compile(
    r"^(?:javascript:\s*|return\s+)?[A-Za-z_$][A-Za-z0-9_$]*"
    r"(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*(?:\s*\(|\s*=|$)",
    re.I,
)
_DOCUMENTATION_SEGMENTS: Final = frozenset(
    {"doc", "docs", "documentation", "examples", "help", "samples", "third_party", "vendor"}
)


def is_markup_source(path: str) -> bool:
    return path.lower().endswith(_MARKUP_SUFFIXES)


def _line(source: str, offset: int) -> int:
    return source.count("\n", 0, offset) + 1


def _ref(alias: str, path: str, source: str, offset: int) -> SourceRef:
    return SourceRef(alias, path, _line(source, offset))


def _attributes(raw: str) -> dict[str, str]:
    return {
        match.group("name").lower(): html.unescape(match.group("value").strip())
        for match in _ATTRIBUTE.finditer(raw)
    }


def _clean_text(value: str, *, limit: int = 512) -> str | None:
    cleaned = html.unescape(_TEXT.sub(" ", value))
    cleaned = " ".join(cleaned.split())
    if not cleaned or len(cleaned) > limit or "\x00" in cleaned:
        return None
    return cleaned


def _subject(path: str) -> str:
    name = PurePosixPath(path).name
    for suffix in _MARKUP_SUFFIXES:
        if name.lower().endswith(suffix):
            return name[: -len(suffix)]
    return PurePosixPath(path).stem


def _is_documentation_html(path: str) -> bool:
    lower = path.lower()
    return lower.endswith((".html", ".htm")) and bool(
        _DOCUMENTATION_SEGMENTS & {part.lower() for part in PurePosixPath(path).parts[:-1]}
    )


def _resource_report(source: str, alias: str, path: str) -> DetectionReport:
    facts: list[ImplementationFactRecord] = []
    subject = _subject(path)
    for match in _RESX_DATA.finditer(source):
        attrs = _attributes(match.group("attrs"))
        name = attrs.get("name")
        value_match = _RESX_VALUE.search(match.group("body"))
        value = (
            None if value_match is None else _clean_text(value_match.group("value"), limit=1_024)
        )
        if not name or value is None:
            continue
        facts.append(
            ImplementationFactRecord(
                subject,
                "controls",
                f"resource:{name}",
                value,
                _ref(alias, path, source, match.start()),
            )
        )
    return DetectionReport(implementation_facts=tuple(facts))


def _surface_name(source: str, path: str) -> tuple[str, int]:
    directive = _DIRECTIVE.search(source)
    if directive is not None:
        attrs = _attributes(directive.group("attrs"))
        for key in ("title", "inherits", "classname"):
            if attrs.get(key):
                return attrs[key], directive.start()
    title = _TITLE.search(source)
    if title is not None and (value := _clean_text(title.group("value"))) is not None:
        return value, title.start()
    return _subject(path), 0


def _markup_report(source: str, alias: str, path: str) -> DetectionReport:
    actions: list[str] = []
    facts: list[ImplementationFactRecord] = []
    configurations: list[ConfigurationRecord] = []
    subject = _subject(path)
    for match in _TAG.finditer(source):
        tag = match.group("tag")
        lowered_tag = tag.lower()
        attrs = _attributes(match.group("attrs"))
        identifier = attrs.get("id") or attrs.get("name")
        for key, value in attrs.items():
            if key in _EVENT_ATTRIBUTES and value and _VALID_ACTION.match(value):
                actions.append(f"{key[2:] if key.startswith('on') else key}:{value}")
        if identifier and (lowered_tag in _INTERACTIVE_TAGS or lowered_tag.startswith("asp:")):
            detail = next((attrs[key] for key in _DISPLAY_ATTRIBUTES if attrs.get(key)), None)
            facts.append(
                ImplementationFactRecord(
                    subject,
                    "controls",
                    f"{tag}:{identifier}",
                    detail,
                    _ref(alias, path, source, match.start()),
                )
            )
        target = None
        if lowered_tag == "form":
            target = attrs.get("action")
        elif lowered_tag == "a":
            target = attrs.get("href")
        elif lowered_tag == "script":
            target = attrs.get("src")
        if target and not target.lower().startswith(("javascript:", "#")):
            facts.append(
                ImplementationFactRecord(
                    subject,
                    "calls",
                    target,
                    f"{tag} target",
                    _ref(alias, path, source, match.start()),
                )
            )
        key = attrs.get("key") or (attrs.get("name") if lowered_tag in {"setting", "add"} else None)
        value = attrs.get("value")
        if key and value is not None:
            redacted = any(
                token in key.lower() for token in ("password", "secret", "token", "privatekey")
            )
            configurations.append(
                ConfigurationRecord(
                    key,
                    value if not redacted and len(value) <= 1_024 else None,
                    False,
                    redacted,
                    _ref(alias, path, source, match.start()),
                )
            )
    lower = path.lower()
    surfaces: tuple[UiSurfaceRecord, ...] = ()
    if lower.endswith(
        (".aspx", ".ascx", ".master", ".html", ".htm")
    ) and not _is_documentation_html(path):
        name, offset = _surface_name(source, path)
        surface_type = "component" if lower.endswith((".ascx", ".master")) else "page"
        surfaces = (
            UiSurfaceRecord(
                name,
                surface_type,
                path,
                tuple(dict.fromkeys(actions)),
                _ref(alias, path, source, offset),
            ),
        )
    return DetectionReport(
        ui_surfaces=surfaces,
        configurations=tuple(configurations),
        implementation_facts=tuple(facts),
    )


def detect_markup_source(source: str, repository_alias: str, path: str) -> DetectionReport:
    """Extract exact UI controls, event handlers, resource text, links, and XML settings."""
    if path.lower().endswith(".resx"):
        return _resource_report(source, repository_alias, path)
    return _markup_report(source, repository_alias, path)
