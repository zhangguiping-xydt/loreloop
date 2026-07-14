from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from loreloop.knowledge import authoritative_ast, authoritative_bindings


def test_document_ast_is_generic_typed_and_deeply_immutable() -> None:
    # Given: a taxonomy-independent document AST containing one domain row.
    bindings = (
        authoritative_bindings.BindingEntry(
            pointer="/title",
            binding=authoritative_bindings.FixedBinding(rule_id="pre-ast-v4", literal="Title"),
        ),
    )
    coverage = authoritative_ast.Coverage(
        inventory_total=1,
        accounted_total=1,
        candidate_total=1,
        record_total=1,
        bound_leaf_total=1,
        routed_leaf_total=1,
        routed_leaf_expected=1,
        bindings=bindings,
    )
    header = authoritative_ast.AuthorityHeader(
        trust_domain_id="1" * 64,
        repository_config_digest="2" * 64,
        package_id=None,
        coverage=coverage,
        bindings=bindings,
    )
    row = authoritative_ast.AstRow(
        row_kind=authoritative_ast.DocumentRowKind.INTERFACE,
        record_id="API-" + "3" * 64,
        values=(authoritative_ast.ProjectedValue(pointer="/kind", value="API"),),
        refs=(),
        evidence_ids=("EVD-" + "4" * 64,),
        canonical=True,
        anchor="ll-api-" + "3" * 64,
        link_target=None,
        bindings=bindings,
    )

    # When: the document is assembled from tuple-owned children.
    document = authoritative_ast.DocumentAst(
        document_id="document-0",
        path="knowledge.md",
        title="Title",
        header=header,
        sections=(
            authoritative_ast.DocumentSection(
                section_id="contracts",
                title="Contracts",
                rows=(row,),
                bindings=bindings,
            ),
        ),
        evidence_rows=(),
        bindings=bindings,
    )

    # Then: the generic AST retains typed scalar values and cannot be patched.
    assert document.sections[0].rows[0].values[0].value == "API"
    with pytest.raises(FrozenInstanceError):
        setattr(document, "title", "Changed")
    with pytest.raises(authoritative_ast.AstViolation, match="document path"):
        _ = replace(document, path="../outside.md")


def test_coverage_rejects_unknowns_gaps_or_inconsistent_totals() -> None:
    # Given: a ready-package coverage block with an unaccounted item.
    # When / Then: the closed model rejects non-ready coverage.
    with pytest.raises(authoritative_ast.AstViolation):
        _ = authoritative_ast.Coverage(
            inventory_total=2,
            accounted_total=1,
            candidate_total=0,
            record_total=0,
            bound_leaf_total=0,
            routed_leaf_total=0,
            routed_leaf_expected=0,
            unknown_total=0,
            gap_total=0,
            bindings=(),
        )


def test_nested_section_field_address_preserves_stable_ancestry() -> None:
    # Given: stable section, subsection, table, row, and column identifiers.
    address = authoritative_ast.RenderedFieldAddress(
        section_id="detailed-design",
        subsection_ids=("security-permissions",),
        table_id="permission-contracts",
        row_id="PERM-" + "5" * 64,
        column_id="normalized_guard",
    )

    # When: the occurrence address is projected into the rendered AST pointer space.
    pointer = address.json_pointer()
    field = authoritative_ast.RenderedField(
        address=address,
        semantic_pointer="/normalized_guard",
        value="role != <redacted>",
        binding=authoritative_bindings.FixedBinding(
            rule_id="document-row-v4",
            literal="role != <redacted>",
        ),
        evidence_ids=("EVD-" + "6" * 64,),
    )
    subsection = authoritative_ast.DocumentSection(
        section_id="security-permissions",
        title="Security and permissions",
        rows=(),
        bindings=(),
        tables=(
            authoritative_ast.RenderedTable(
                table_id="permission-contracts",
                fields=(field,),
            ),
        ),
    )
    section = authoritative_ast.DocumentSection(
        section_id="detailed-design",
        title="Detailed design",
        rows=(),
        bindings=(),
        subsections=(subsection,),
    )

    # Then: every level of stable ancestry is explicit and reader-facing labels are irrelevant.
    expected_pointer = (
        "/sections/detailed-design/subsections/security-permissions/"
        f"tables/permission-contracts/rows/PERM-{'5' * 64}/normalized_guard"
    )
    assert pointer == expected_pointer
    assert section.subsections[0].tables[0].fields[0].address == address
