from __future__ import annotations

import subprocess
import sys

import pytest

from loreloop.knowledge import authoritative_ast


def _document(
    ordinal: int,
    required_family: authoritative_ast.RequiredDocumentFamily | None = None,
    optional_family: authoritative_ast.OptionalDocumentFamily | None = None,
) -> authoritative_ast.DocumentAst:
    coverage = authoritative_ast.Coverage(
        inventory_total=0,
        accounted_total=0,
        candidate_total=0,
        record_total=0,
        bound_leaf_total=0,
        routed_leaf_total=0,
        routed_leaf_expected=0,
        bindings=(),
    )
    return authoritative_ast.DocumentAst(
        document_id=f"document-{ordinal}",
        path=f"neutral-{ordinal}.md",
        title=f"Neutral {ordinal}",
        header=authoritative_ast.AuthorityHeader(
            trust_domain_id="1" * 64,
            repository_config_digest="2" * 64,
            package_id=None,
            coverage=coverage,
            bindings=(),
        ),
        sections=(
            authoritative_ast.DocumentSection(
                section_id=f"section-{ordinal}",
                title=f"Section {ordinal}",
                rows=(),
                bindings=(),
            ),
        ),
        evidence_rows=(),
        bindings=(),
        required_family=required_family,
        optional_family=optional_family,
    )


def test_document_set_accepts_six_fixed_plus_only_interface_database_optionals() -> None:
    # Given: six fixed documents plus both permitted optional families.
    documents = tuple(
        _document(ordinal, required_family=family)
        for ordinal, family in enumerate(authoritative_ast.RequiredDocumentFamily)
    ) + (
        _document(
            6,
            optional_family=authoritative_ast.OptionalDocumentFamily.INTERFACE_CONTRACT,
        ),
        _document(7, optional_family=authoritative_ast.OptionalDocumentFamily.DATABASE_DESIGN),
    )
    applicability = (
        authoritative_ast.OptionalDocumentApplicability(
            family=authoritative_ast.OptionalDocumentFamily.INTERFACE_CONTRACT,
            status=authoritative_ast.ApplicabilityStatus.PRESENT,
            reason_ids=(),
        ),
        authoritative_ast.OptionalDocumentApplicability(
            family=authoritative_ast.OptionalDocumentFamily.DATABASE_DESIGN,
            status=authoritative_ast.ApplicabilityStatus.PRESENT,
            reason_ids=(),
        ),
    )

    # When: the package-neutral document set is closed.
    document_set = authoritative_ast.DocumentSet(documents=documents, applicability=applicability)

    # Then: N=8 and no deployment/security document family exists.
    assert len(document_set.documents) == 8
    assert {member.value for member in authoritative_ast.OptionalDocumentFamily} == {
        "interface_contract",
        "database_design",
    }


def test_document_set_rejects_unknown_or_count_outside_six_to_eight() -> None:
    # Given: a diagnostic unknown and only five fixed documents.
    applicability = (
        authoritative_ast.OptionalDocumentApplicability(
            family=authoritative_ast.OptionalDocumentFamily.INTERFACE_CONTRACT,
            status=authoritative_ast.ApplicabilityStatus.UNKNOWN,
            reason_ids=(),
        ),
        authoritative_ast.OptionalDocumentApplicability(
            family=authoritative_ast.OptionalDocumentFamily.DATABASE_DESIGN,
            status=authoritative_ast.ApplicabilityStatus.NO_EXPLICIT_MARKER,
            reason_ids=(),
        ),
    )

    # When / Then: unknown and N=5 both block readiness.
    with pytest.raises(authoritative_ast.AstViolation):
        _ = authoritative_ast.DocumentSet(
            documents=tuple(
                _document(ordinal, required_family=family)
                for ordinal, family in enumerate(
                    tuple(authoritative_ast.RequiredDocumentFamily)[:5]
                )
            ),
            applicability=applicability,
        )


def test_document_set_rejects_a_duplicate_required_family() -> None:
    # Given: all six fixed families plus a seventh duplicate fixed document.
    families = tuple(authoritative_ast.RequiredDocumentFamily) + (
        authoritative_ast.RequiredDocumentFamily.CAPABILITY_CATALOG,
    )
    applicability = (
        authoritative_ast.OptionalDocumentApplicability(
            family=authoritative_ast.OptionalDocumentFamily.INTERFACE_CONTRACT,
            status=authoritative_ast.ApplicabilityStatus.NO_EXPLICIT_MARKER,
            reason_ids=(),
        ),
        authoritative_ast.OptionalDocumentApplicability(
            family=authoritative_ast.OptionalDocumentFamily.DATABASE_DESIGN,
            status=authoritative_ast.ApplicabilityStatus.NO_EXPLICIT_MARKER,
            reason_ids=(),
        ),
    )

    # When / Then: count alone cannot satisfy the fixed taxonomy.
    with pytest.raises(authoritative_ast.AstViolation, match="required document families"):
        _ = authoritative_ast.DocumentSet(
            documents=tuple(
                _document(ordinal, required_family=family)
                for ordinal, family in enumerate(families)
            ),
            applicability=applicability,
        )


@pytest.mark.parametrize(
    "constructor",
    [
        (
            "OptionalDocumentApplicability("
            "family=OptionalDocumentFamily.INTERFACE_CONTRACT,"
            "status='unknown',reason_ids=())"
        ),
        (
            "OptionalDocumentApplicability("
            "family='interface_contract',"
            "status=ApplicabilityStatus.PRESENT,reason_ids=())"
        ),
    ],
)
def test_optional_applicability_rejects_raw_tagged_strings(constructor: str) -> None:
    # Given: a caller bypassing static typing with a raw serialized enum value.
    program = f"from loreloop.knowledge.authoritative_ast import *;{constructor}"

    # When: the public dataclass constructor receives the raw string.
    completed = subprocess.run(
        [sys.executable, "-c", program],
        text=True,
        capture_output=True,
        check=False,
    )

    # Then: construction fails at the runtime boundary before DocumentSet readiness.
    assert completed.returncode != 0
    assert "AstViolation" in completed.stderr
