"""Small safe YAML subset parser for committed contract documents."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TypeAlias, cast

from .authoritative_records import DetectionError

YamlScalar: TypeAlias = None | bool | int | float | str
YamlValue: TypeAlias = YamlScalar | list["YamlValue"] | dict[str, "YamlValue"]


@dataclass(frozen=True, slots=True)
class _Line:
    indent: int
    number: int
    content: str


def _ends_quote(text: str, index: int, quote: str) -> bool:
    if quote == "'":
        before = index > 0 and text[index - 1] == "'"
        after = index + 1 < len(text) and text[index + 1] == "'"
        return not before and not after
    backslashes = 0
    position = index - 1
    while position >= 0 and text[position] == "\\":
        backslashes += 1
        position -= 1
    return backslashes % 2 == 0


def _comment(raw: str) -> str:
    quote: str | None = None
    depth = 0
    for index, character in enumerate(raw):
        if quote is not None:
            if character == quote and _ends_quote(raw, index, quote):
                quote = None
        elif character in {'"', "'"}:
            quote = character
        elif character in "[{":
            depth += 1
        elif character in "]}":
            depth -= 1
        elif character == "#" and depth == 0 and (index == 0 or raw[index - 1].isspace()):
            return raw[:index]
    if quote is not None or depth != 0:
        raise DetectionError("unterminated YAML scalar")
    return raw


def _separator(text: str, separator: str = ":") -> int:
    quote: str | None = None
    depth = 0
    for index, character in enumerate(text):
        if quote is not None:
            if character == quote and _ends_quote(text, index, quote):
                quote = None
        elif character in {'"', "'"}:
            quote = character
        elif character in "[{":
            depth += 1
        elif character in "]}":
            depth -= 1
        elif character == separator and depth == 0:
            return index
    return -1


def _parts(text: str) -> tuple[str, ...]:
    parts: list[str] = []
    start = 0
    quote: str | None = None
    depth = 0
    for index, character in enumerate(text):
        if quote is not None:
            if character == quote and _ends_quote(text, index, quote):
                quote = None
        elif character in {'"', "'"}:
            quote = character
        elif character in "[{":
            depth += 1
        elif character in "]}":
            depth -= 1
        elif character == "," and depth == 0:
            parts.append(text[start:index].strip())
            start = index + 1
    parts.append(text[start:].strip())
    return tuple(part for part in parts if part)


def _quoted(text: str) -> str:
    if text.startswith('"'):
        try:
            value = cast(object, json.loads(text))
        except json.JSONDecodeError as exc:
            raise DetectionError("invalid quoted YAML scalar") from exc
        if not isinstance(value, str):
            raise DetectionError("invalid quoted YAML scalar")
        return value
    if not text.endswith("'"):
        raise DetectionError("invalid quoted YAML scalar")
    return text[1:-1].replace("''", "'")


def _scalar(text: str) -> YamlValue:
    value = text.strip()
    if not value:
        return None
    if value[0] in "&*!":
        raise DetectionError("YAML anchors, aliases, and tags are not supported")
    if value[0] in {'"', "'"}:
        return _quoted(value)
    if value.startswith("[") and value.endswith("]"):
        return [_scalar(part) for part in _parts(value[1:-1])]
    if value.startswith("{") and value.endswith("}"):
        result: dict[str, YamlValue] = {}
        for part in _parts(value[1:-1]):
            offset = _separator(part)
            if offset < 1:
                raise DetectionError("invalid YAML flow mapping")
            result[str(_scalar(part[:offset]))] = _scalar(part[offset + 1 :])
        return result
    lowered = value.lower()
    if lowered in {"null", "~"}:
        return None
    if lowered in {"true", "false"}:
        return lowered == "true"
    if re.fullmatch(r"[-+]?\d+", value):
        return int(value)
    if re.fullmatch(r"[-+]?(?:\d+\.\d*|\.\d+)(?:e[-+]?\d+)?", value, re.IGNORECASE):
        return float(value)
    return value


class _Parser:
    def __init__(self, lines: tuple[_Line, ...]) -> None:
        self.lines: tuple[_Line, ...] = lines

    def parse(self) -> YamlValue:
        if not self.lines:
            raise DetectionError("empty YAML document")
        value, index = self._block(0, self.lines[0].indent)
        if index != len(self.lines):
            raise DetectionError(f"invalid YAML indentation at line {self.lines[index].number}")
        return value

    def _block(self, index: int, indent: int) -> tuple[YamlValue, int]:
        if self.lines[index].indent != indent:
            raise DetectionError(f"invalid YAML indentation at line {self.lines[index].number}")
        if self.lines[index].content.startswith("-"):
            return self._sequence(index, indent)
        return self._mapping(index, indent)

    def _mapping(self, index: int, indent: int) -> tuple[YamlValue, int]:
        result: dict[str, YamlValue] = {}
        while index < len(self.lines) and self.lines[index].indent == indent:
            line = self.lines[index]
            if line.content.startswith("-"):
                break
            offset = _separator(line.content)
            if offset < 1:
                raise DetectionError(f"invalid YAML mapping at line {line.number}")
            key = str(_scalar(line.content[:offset]))
            if key in result:
                raise DetectionError(f"duplicate YAML key {key!r} at line {line.number}")
            remainder = line.content[offset + 1 :].strip()
            index += 1
            if remainder in {"|", ">"}:
                value, index = self._multiline(index, indent, remainder)
            elif remainder:
                value = _scalar(remainder)
            elif index < len(self.lines) and self.lines[index].indent > indent:
                value, index = self._block(index, self.lines[index].indent)
            else:
                value = None
            result[key] = value
        return result, index

    def _sequence(self, index: int, indent: int) -> tuple[YamlValue, int]:
        result: list[YamlValue] = []
        while index < len(self.lines) and self.lines[index].indent == indent:
            line = self.lines[index]
            if not line.content.startswith("-"):
                break
            remainder = line.content[1:].strip()
            index += 1
            if not remainder:
                if index >= len(self.lines) or self.lines[index].indent <= indent:
                    raise DetectionError(f"empty YAML sequence item at line {line.number}")
                value, index = self._block(index, self.lines[index].indent)
            elif _separator(remainder) > 0:
                offset = _separator(remainder)
                key = str(_scalar(remainder[:offset]))
                tail = remainder[offset + 1 :].strip()
                if not tail:
                    raise DetectionError(
                        f"empty first mapping value in YAML sequence at line {line.number}"
                    )
                item: dict[str, YamlValue] = {key: _scalar(tail)}
                if index < len(self.lines) and self.lines[index].indent > indent:
                    extra, index = self._mapping(index, self.lines[index].indent)
                    if not isinstance(extra, dict) or set(item) & set(extra):
                        raise DetectionError(f"invalid YAML sequence mapping at line {line.number}")
                    item.update(extra)
                value = item
            else:
                value = _scalar(remainder)
                if index < len(self.lines) and self.lines[index].indent > indent:
                    raise DetectionError(f"scalar sequence item has children at line {line.number}")
            result.append(value)
        return result, index

    def _multiline(self, index: int, parent: int, style: str) -> tuple[str, int]:
        values: list[str] = []
        while index < len(self.lines) and self.lines[index].indent > parent:
            values.append(self.lines[index].content)
            index += 1
        separator = "\n" if style == "|" else " "
        return separator.join(values), index


def parse_yaml_contract(text: str) -> YamlValue:
    """Parse a deterministic, non-executable YAML subset used by contract files."""
    lines: list[_Line] = []
    for number, raw in enumerate(text.splitlines(), 1):
        if "\t" in raw[: len(raw) - len(raw.lstrip())]:
            raise DetectionError(f"tabs are not valid YAML indentation at line {number}")
        content = _comment(raw).rstrip()
        if not content.strip() or content.strip() in {"---", "..."}:
            continue
        if content.lstrip().startswith("%"):
            raise DetectionError(f"YAML directives are not supported at line {number}")
        indent = len(content) - len(content.lstrip(" "))
        lines.append(_Line(indent, number, content.strip()))
    return _Parser(tuple(lines)).parse()
