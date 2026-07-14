"""Shared position-preserving helpers for shallow source detectors."""

from __future__ import annotations

from .authoritative_records import SourceRef


def source_ref(alias: str, path: str, source: str, offset: int) -> SourceRef:
    """Build a one-based source reference for a character offset."""
    return SourceRef(alias, path, source.count("\n", 0, offset) + 1)


def mask_c_like_comments(source: str) -> str:
    """Replace C-like comments with spaces while preserving strings and offsets."""
    result = list(source)
    index = 0
    state = "code"
    quote = ""
    block_depth = 0
    while index < len(source):
        current = source[index]
        following = source[index + 1] if index + 1 < len(source) else ""
        if state == "code":
            single_quoted = current == "'" and (
                (index + 2 < len(source) and source[index + 2] == "'")
                or (index + 3 < len(source) and following == "\\" and source[index + 3] == "'")
            )
            if current in {'"', "`"} or single_quoted:
                state = "string"
                quote = current
            elif current == "/" and following == "/":
                result[index] = result[index + 1] = " "
                state = "line_comment"
                index += 1
            elif current == "/" and following == "*":
                result[index] = result[index + 1] = " "
                state = "block_comment"
                block_depth = 1
                index += 1
        elif state == "string":
            if current == "\\":
                index += 1
            elif current == quote:
                state = "code"
        elif state == "line_comment":
            if current == "\n":
                state = "code"
            else:
                result[index] = " "
        elif current == "/" and following == "*":
            result[index] = result[index + 1] = " "
            block_depth += 1
            index += 1
        elif current == "*" and following == "/":
            result[index] = result[index + 1] = " "
            block_depth -= 1
            if block_depth == 0:
                state = "code"
            index += 1
        elif current != "\n":
            result[index] = " "
        index += 1
    return "".join(result)
