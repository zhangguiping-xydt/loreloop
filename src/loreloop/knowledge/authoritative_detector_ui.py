"""Deterministic UI-surface extraction for Vue and common frontend routers."""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Final

from .authoritative_records import DetectionReport, SourceRef, UiSurfaceRecord

_VUE_NAME: Final = re.compile(r"\bname\s*:\s*(['\"])(?P<name>[^'\"\r\n]{1,256})\1")
_VUE_TEMPLATE: Final = re.compile(r"<template(?:\s[^>]*)?>", re.IGNORECASE)
_VUE_EVENT: Final = re.compile(
    r"@(?P<event>[A-Za-z][\w:-]*)\s*=\s*(['\"])(?P<handler>[^'\"\r\n]{1,512})\2"
)
_ROUTE_PATH: Final = re.compile(
    r"\bpath\s*:\s*(['\"])(?P<path>[^'\"\r\n]{1,512})\1"
)
_SCREEN: Final = re.compile(
    r"<(?:[A-Za-z_$][\w$]*\.)?Screen\b[^>]*\bname\s*=\s*(['\"])(?P<name>[^'\"]+)\1",
    re.IGNORECASE,
)


def _line(source: str, offset: int) -> int:
    return source.count("\n", 0, offset) + 1


def _ref(alias: str, path: str, source: str, offset: int) -> SourceRef:
    return SourceRef(alias, path, _line(source, offset))


def detect_vue_source(source: str, repository_alias: str, path: str) -> DetectionReport:
    """Describe one committed Vue SFC without executing its template or script."""
    name_match = _VUE_NAME.search(source)
    template_match = _VUE_TEMPLATE.search(source)
    name = (
        name_match.group("name")
        if name_match is not None
        else PurePosixPath(path).stem
    )
    actions = tuple(
        dict.fromkeys(
            f"{match.group('event')}:{match.group('handler').strip()}"
            for match in _VUE_EVENT.finditer(source)
        )
    )
    surface_type = "page" if "/views/" in f"/{path.lower()}" else "component"
    offset = (
        template_match.start()
        if template_match is not None
        else (name_match.start() if name_match is not None else 0)
    )
    return DetectionReport(
        ui_surfaces=(
            UiSurfaceRecord(
                name,
                surface_type,
                path,
                actions,
                _ref(repository_alias, path, source, offset),
            ),
        )
    )


def detect_typescript_ui_surfaces(
    source: str, repository_alias: str, path: str
) -> tuple[UiSurfaceRecord, ...]:
    """Extract explicit route and screen registrations from frontend source."""
    lower_path = path.lower()
    records: list[UiSurfaceRecord] = []
    if any(segment in lower_path.split("/") for segment in ("router", "routers", "routes")):
        records.extend(
            UiSurfaceRecord(
                match.group("path"),
                "route",
                match.group("path"),
                (),
                _ref(repository_alias, path, source, match.start()),
            )
            for match in _ROUTE_PATH.finditer(source)
        )
    records.extend(
        UiSurfaceRecord(
            match.group("name"),
            "route",
            match.group("name"),
            (),
            _ref(repository_alias, path, source, match.start()),
        )
        for match in _SCREEN.finditer(source)
    )
    return tuple(records)
