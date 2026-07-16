from __future__ import annotations

import pytest

from loreloop.knowledge.authoritative_detector_tests import (
    MAX_TEST_CASES_FIELD_BYTES,
    detect_test_source,
    is_supported_test_evidence_path,
)
from loreloop.knowledge.authoritative_detector_ui import (
    detect_typescript_ui_surfaces,
    detect_vue_source,
)
from loreloop.knowledge.authoritative_records import DetectionError
from loreloop.knowledge.authoritative_source import (
    SnapshotBlob,
    _test_text,
    _text,
    source_text_encoding,
)


def test_vue_detector_emits_page_actions_with_source_identity() -> None:
    source = """
<template>
  <main><button @click="save">Save</button><form @submit="submitForm"></form></main>
</template>
<script>export default { name: "AccountPage" }</script>
"""

    report = detect_vue_source(source, "frontend", "src/views/account.vue")

    assert len(report.ui_surfaces) == 1
    surface = report.ui_surfaces[0]
    assert surface.name == "AccountPage"
    assert surface.surface_type == "page"
    assert surface.entry == "src/views/account.vue"
    assert surface.actions == ("click:save", "submit:submitForm")
    assert surface.source.repository_alias == "frontend"


def test_typescript_ui_detector_reads_explicit_router_and_screen_entries() -> None:
    router = """
export const routes = [{ path: "/users", component: Users }]
const view = <Stack.Screen name="Settings" component={Settings} />
"""

    records = detect_typescript_ui_surfaces(router, ".", "src/router/index.tsx")

    assert {(item.name, item.entry) for item in records} == {
        ("/users", "/users"),
        ("Settings", "Settings"),
    }


def test_junit_detector_emits_test_rows_without_reading_test_bodies() -> None:
    source = """
import org.junit.Test;
import org.springframework.boot.test.context.SpringBootTest;
@SpringBootTest
public class UserServiceTest {
  @Test
  public void createsUser() { secret("must-not-project"); }
}
"""

    report = detect_test_source(source, "backend", "src/test/java/UserServiceTest.java")

    assert is_supported_test_evidence_path("src/test/java/UserServiceTest.java")
    assert [(item.name, item.framework, item.scope, item.cases) for item in report.tests] == [
        ("UserServiceTest", "junit4", "integration", ("createsUser",))
    ]
    assert report.tests[0].source.line == 6


def test_test_evidence_path_rejects_fixtures_and_generated_snapshots() -> None:
    assert not is_supported_test_evidence_path("tests/fixtures/user_test.py")
    assert not is_supported_test_evidence_path("src/__snapshots__/view.test.ts")


def test_test_source_decoding_accepts_legacy_comments_without_weakening_product_source() -> None:
    data = "// 中文注释\n@Test\npublic void works() {}\n".encode("gb18030")
    blob = SnapshotBlob("backend", "src/test/java/LegacyTest.java", data, "0" * 64)

    assert "public void works" in _test_text(blob)


def test_sql_source_decoding_accepts_gb18030_without_weakening_other_sources() -> None:
    data = "-- 用户表\nCREATE TABLE legacy_users (id INTEGER PRIMARY KEY);\n".encode("gb18030")
    sql = SnapshotBlob("backend", "schema/legacy.sql", data, "0" * 64)
    python = SnapshotBlob("backend", "legacy.py", data, "0" * 64)

    assert source_text_encoding(sql) == "gb18030"
    assert "legacy_users" in _text(sql)
    with pytest.raises(DetectionError, match="UTF-8"):
        _ = _text(python)


def test_sql_source_decoding_rejects_invalid_or_binary_legacy_bytes() -> None:
    blob = SnapshotBlob("backend", "schema/broken.sql", b"\x81\x30\x81\x00", "0" * 64)

    assert source_text_encoding(blob) is None
    with pytest.raises(DetectionError, match="UTF-8 or GB18030"):
        _ = _text(blob)


def test_large_test_file_splits_cases_below_capsule_string_budget() -> None:
    case_count = 16_400
    case_name = "x" * 500
    source = "\n".join(
        f'test("{case_name}{index:05d}", () => true);' for index in range(case_count)
    )

    report = detect_test_source(source, ".", "tests/large.test.js")

    assert len(report.tests) >= 2
    assert sum(len(item.cases) for item in report.tests) == case_count
    assert all(
        len(", ".join(item.cases).encode("utf-8")) <= MAX_TEST_CASES_FIELD_BYTES
        for item in report.tests
    )
    assert report.tests[0].name.startswith("large.test [1/")
