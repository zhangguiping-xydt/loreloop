"""Small YAML-line helpers for deterministic platform detection."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from .authoritative_records import ConfigurationRecord, SourceRef
from .authoritative_redaction import redact_default

YAML_FIELD: Final = re.compile(
    r"^(?P<indent>\s*)(?:-\s*)?(?P<key>[\w.-]+):(?:\s*(?P<value>.*))?$"
)
YAML_LIST: Final = re.compile(r"^(?P<indent>\s*)-\s*(?P<value>.+)$")
_ENV_KEY: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True, slots=True)
class YamlLine:
    indent: int
    key: str
    value: str
    line: int
    listed: bool


def unquote(value: str) -> str:
    stripped = value.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in {'"', "'"}:
        return stripped[1:-1]
    return stripped


def is_env_key(value: str) -> bool:
    return _ENV_KEY.fullmatch(value) is not None


def yaml_lines(source: str) -> tuple[YamlLine, ...]:
    records: list[YamlLine] = []
    for number, raw in enumerate(source.splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        match = YAML_FIELD.match(raw)
        if match is not None:
            records.append(
                YamlLine(
                    len(match.group("indent")),
                    match.group("key"),
                    unquote((match.group("value") or "").split(" #", 1)[0]),
                    number,
                    raw.lstrip().startswith("-"),
                )
            )
    return tuple(records)


def configuration(
    alias: str,
    path: str,
    line: int,
    key: str,
    default: str | None,
) -> ConfigurationRecord:
    portable, redacted = redact_default(key, default)
    return ConfigurationRecord(
        key,
        portable,
        default is None,
        redacted,
        SourceRef(alias, path, line),
    )


def yaml_environment(
    lines: tuple[YamlLine, ...], alias: str, path: str
) -> tuple[ConfigurationRecord, ...]:
    records: list[ConfigurationRecord] = []
    env_indent: int | None = None
    pending: tuple[str, int, int] | None = None
    list_mode = False

    def flush(default: str | None = None) -> None:
        nonlocal pending
        if pending is not None:
            key, line, _ = pending
            records.append(configuration(alias, path, line, key, default))
            pending = None

    for item in lines:
        if env_indent is not None and item.indent <= env_indent:
            flush()
            env_indent = None
            list_mode = False
        if item.key in {"environment", "env"} and not item.value:
            flush()
            env_indent = item.indent
            list_mode = False
            continue
        if env_indent is None or item.indent <= env_indent:
            continue
        if item.listed and item.key == "name" and is_env_key(item.value):
            flush()
            list_mode = True
            pending = (item.value, item.line, item.indent)
        elif pending is not None and item.key == "value" and item.indent > pending[2]:
            flush(item.value or None)
        elif pending is not None and item.key == "valueFrom" and item.indent > pending[2]:
            flush()
        elif not list_mode and is_env_key(item.key):
            flush()
            records.append(configuration(alias, path, item.line, item.key, item.value or None))
    flush()
    return tuple(records)
