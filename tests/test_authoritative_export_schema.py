from __future__ import annotations

import dataclasses

import pytest

from loreloop.knowledge import authoritative_ast, authoritative_bindings, authoritative_types


def test_foundation_enum_arms_match_the_closed_v4_schema() -> None:
    # Given: the foundation discriminated unions available before detector records exist.
    # When: enum values are enumerated.
    binding_kinds = {member.value for member in authoritative_bindings.BindingKind}
    ref_kinds = {member.value for member in authoritative_types.RefKind}
    journal_states = {member.value for member in authoritative_types.JournalState}

    # Then: no open-ended or worker-selected variant exists.
    assert binding_kinds == {"source", "derived", "fixed", "observed"}
    assert ref_kinds == {"import_target", "call_target", "data_target", "requirement_subject"}
    assert journal_states == {
        "PREPARE_INTENT",
        "STAGING",
        "STAGED",
        "INSTALL_INTENT",
        "INSTALLED",
        "CLEANUP_INTENT",
        "ABORTED",
    }


def test_foundation_models_are_frozen_slotted_dataclasses() -> None:
    # Given: every exported foundation contract model.
    model_types = authoritative_types.FOUNDATION_MODEL_TYPES + (
        authoritative_bindings.SourceBinding,
        authoritative_bindings.DerivedInput,
        authoritative_bindings.DerivedBinding,
        authoritative_bindings.FixedBinding,
        authoritative_bindings.ObservedBinding,
        authoritative_bindings.BindingEntry,
        authoritative_ast.ProjectedValue,
        authoritative_ast.RenderedFieldAddress,
        authoritative_ast.RenderedField,
        authoritative_ast.RenderedTable,
        authoritative_ast.AstRow,
        authoritative_ast.Coverage,
        authoritative_ast.AuthorityHeader,
        authoritative_ast.DocumentSection,
        authoritative_ast.DocumentAst,
        authoritative_ast.OptionalDocumentApplicability,
        authoritative_ast.DocumentSet,
    )

    # When / Then: each model is a frozen dataclass with slots.
    for model_type in model_types:
        assert dataclasses.is_dataclass(model_type)
        assert hasattr(model_type, "__slots__")
        assert "__setattr__" in model_type.__dict__


def test_derived_binding_rejects_empty_inputs_and_bad_json_pointer() -> None:
    # Given: malformed binding inputs.
    # When / Then: both schema-shape errors fail at construction.
    with pytest.raises(authoritative_bindings.BindingViolation):
        _ = authoritative_bindings.DerivedBinding(
            rule_id="candidate-v4",
            inputs=(),
            projection=authoritative_bindings.DerivedProjection.SEMANTIC_KEY,
        )
    with pytest.raises(authoritative_bindings.BindingViolation):
        _ = authoritative_bindings.BindingEntry(
            pointer="not/a/pointer",
            binding=authoritative_bindings.FixedBinding(rule_id="fixed", literal=None),
        )
