from __future__ import annotations

from loreloop.knowledge.authoritative_detector_typescript import detect_typescript_source


def test_typescript_detector_extracts_express_nest_config_permission_and_dependencies() -> None:
    # Given: common Express, NestJS, environment, authorization, and import shapes.
    source = """
import express from "express";
import { Controller, Get } from "@nestjs/common";
const token = process.env.API_TOKEN;
router.post("/users", createUser);

@Controller("/admin")
export class AdminController {
  @Get("/health")
  health() { return true; }
}

export async function createUser(name: string) {
  if (currentUser.role !== "admin") throw new Error("forbidden");
}
"""

    # When: static detection runs without Node or TypeScript execution.
    report = detect_typescript_source(source, "frontend", "src/app.ts")

    # Then: source-backed contracts are available to SemanticCore.
    assert {(item.method, item.path, item.name) for item in report.interfaces} == {
        ("POST", "/users", "createUser"),
        ("GET", "/admin/health", "health"),
    }
    assert {item.qualified_name for item in report.symbols} >= {
        "AdminController",
        "createUser",
    }
    assert {item.name for item in report.dependencies} == {"express", "@nestjs/common"}
    assert report.configurations[0].key == "API_TOKEN"
    assert report.permissions[0].subject == "currentUser.role"
    assert report.permissions[0].expected == "'admin'"


def test_typescript_detector_reads_only_explicit_sql_template_literals() -> None:
    source = '''
export const schema = `CREATE TABLE users (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL
);`;
const description = "CREATE TABLE is mentioned here, but is not executable DDL";
'''

    report = detect_typescript_source(source, ".", "src/schema.ts")

    assert [table.name for table in report.tables] == ["users"]
    assert [column.name for column in report.tables[0].columns] == ["id", "name"]


def test_typescript_template_scanner_handles_large_unterminated_literal_linearly() -> None:
    source = "const value = `" + ("\\x" * 100_000)

    report = detect_typescript_source(source, ".", "src/generated.ts")

    assert report.tables == ()


def test_typescript_permission_scan_ignores_unbounded_generated_member_chain() -> None:
    source = "const value = root" + (".generated" * 100_000) + ";"

    report = detect_typescript_source(source, ".", "src/generated.ts")

    assert report.permissions == ()


def test_typescript_symbol_inventory_is_limited_to_explicit_exports() -> None:
    source = "function internal() {}\nexport function publicApi() {}\n"

    report = detect_typescript_source(source, ".", "src/api.ts")

    assert [item.qualified_name for item in report.symbols] == ["publicApi"]


def test_typescript_dependency_scan_rejects_generated_expression_fragments() -> None:
    source = '''
import client from "@scope/client";
const generated = "from ') + ' from ',' from ':!1,'";
'''

    report = detect_typescript_source(source, ".", "dist/generated.js")

    assert [item.name for item in report.dependencies] == ["@scope/client"]
