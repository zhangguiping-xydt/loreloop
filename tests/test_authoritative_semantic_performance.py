from __future__ import annotations

from loreloop.knowledge.authoritative_semantic import _line_spans


def test_line_span_index_is_built_once_and_addresses_exact_lines() -> None:
    data = b"first\nsecond\nthird"

    assert _line_spans(data) == ((0, 6), (6, 13), (13, 18))
    assert _line_spans(b"") == ((0, 0),)
