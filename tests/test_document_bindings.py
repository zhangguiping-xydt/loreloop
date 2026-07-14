from __future__ import annotations

import pytest

from loreloop.knowledge import authoritative_bindings


def test_binding_module_exports_closed_semantic_validator() -> None:
    # Given: the newly authorized binding responsibility module.
    # When: its closed enums and validator are inspected.
    bindings = authoritative_bindings

    # Then: all four arms and semantic policy types are present.
    assert {member.value for member in bindings.BindingKind} == {
        "source",
        "derived",
        "fixed",
        "observed",
    }
    assert bindings.BindingPolicy
    assert bindings.validate_binding_semantics


def test_binding_semantics_rejects_source_to_fixed_authority_downgrade() -> None:
    # Given: a source-owned API field and a same-valued fixed binding substitution.
    bindings = authoritative_bindings
    policy = bindings.BindingPolicy(
        owner_kind="API",
        owner_pointer="/method",
        consumer_ordinal=40,
        binding_kind=bindings.BindingKind.SOURCE,
        rule_id=None,
        projection=None,
        observation_kind=None,
    )
    downgraded = bindings.FixedBinding(rule_id="source-record-v4", literal="GET")

    # When / Then: equal bytes cannot replace truthful producer provenance.
    with pytest.raises(bindings.BindingViolation, match="binding kind"):
        bindings.validate_binding_semantics(downgraded, policy, ())


def test_derived_binding_rejects_same_or_later_producer() -> None:
    # Given: an EDGE binding that incorrectly depends on another EDGE at the same ordinal.
    bindings = authoritative_bindings
    producer_id = "EDGE-" + "7" * 64
    derived = bindings.DerivedBinding(
        rule_id="derived-record-v4",
        inputs=(bindings.DerivedInput(bindable_id=producer_id, pointer="/id"),),
        projection=bindings.DerivedProjection.RECORD_ID,
    )
    policy = bindings.BindingPolicy(
        owner_kind="EDGE",
        owner_pointer="/id",
        consumer_ordinal=100,
        binding_kind=bindings.BindingKind.DERIVED,
        rule_id="derived-record-v4",
        projection=bindings.DerivedProjection.RECORD_ID,
        observation_kind=None,
    )
    producers = (
        bindings.ProducerBindingInfo(
            bindable_id=producer_id,
            owner_kind="EDGE",
            construction_ordinal=100,
            pointers=frozenset({"/id"}),
        ),
    )

    # When / Then: producer order fails closed.
    with pytest.raises(bindings.BindingViolation, match="earlier"):
        bindings.validate_binding_semantics(derived, policy, producers)


def test_source_binding_rejects_a_policy_transform_substitution() -> None:
    # Given: valid earlier source producers but a transform not authorized for the field.
    bindings = authoritative_bindings
    evidence_id = "EVD-" + "1" * 64
    atom_id = "ATM-" + "2" * 64
    source = bindings.SourceBinding(
        evidence_id=evidence_id,
        atom_id=atom_id,
        atom_pointer="/payload/name",
        transform=bindings.SourceTransform.NFC,
    )
    policy = bindings.BindingPolicy(
        owner_kind="API",
        owner_pointer="/name",
        consumer_ordinal=40,
        binding_kind=bindings.BindingKind.SOURCE,
        rule_id=None,
        projection=None,
        observation_kind=None,
        source_atom_kind="python_ast",
        source_transform=bindings.SourceTransform.IDENTITY,
    )
    producers = (
        bindings.ProducerBindingInfo(
            bindable_id=evidence_id,
            owner_kind="evidence",
            construction_ordinal=10,
            pointers=frozenset({""}),
        ),
        bindings.ProducerBindingInfo(
            bindable_id=atom_id,
            owner_kind="python_ast",
            construction_ordinal=20,
            pointers=frozenset({"/payload/name"}),
            source_evidence_id=evidence_id,
        ),
    )

    # When / Then: source bytes cannot change the frozen projection semantics.
    with pytest.raises(bindings.BindingViolation, match="source transform"):
        bindings.validate_binding_semantics(source, policy, producers)


def test_source_binding_rejects_a_different_valid_evidence_atom_pair() -> None:
    # Given: two valid evidence producers and one atom owned by only the first evidence.
    bindings = authoritative_bindings
    expected_evidence_id = "EVD-" + "1" * 64
    wrong_evidence_id = "EVD-" + "2" * 64
    atom_id = "ATM-" + "3" * 64
    source = bindings.SourceBinding(
        evidence_id=wrong_evidence_id,
        atom_id=atom_id,
        atom_pointer="/payload/name",
        transform=bindings.SourceTransform.IDENTITY,
    )
    policy = bindings.BindingPolicy(
        owner_kind="API",
        owner_pointer="/name",
        consumer_ordinal=40,
        binding_kind=bindings.BindingKind.SOURCE,
        rule_id=None,
        projection=None,
        observation_kind=None,
        source_atom_kind="python_ast",
        source_transform=bindings.SourceTransform.IDENTITY,
    )
    producers = (
        bindings.ProducerBindingInfo(
            bindable_id=expected_evidence_id,
            owner_kind="evidence",
            construction_ordinal=10,
            pointers=frozenset({""}),
        ),
        bindings.ProducerBindingInfo(
            bindable_id=wrong_evidence_id,
            owner_kind="evidence",
            construction_ordinal=10,
            pointers=frozenset({""}),
        ),
        bindings.ProducerBindingInfo(
            bindable_id=atom_id,
            owner_kind="python_ast",
            construction_ordinal=20,
            pointers=frozenset({"/payload/name"}),
            source_evidence_id=expected_evidence_id,
        ),
    )

    # When / Then: independently valid producers cannot form a false provenance pair.
    with pytest.raises(bindings.BindingViolation, match="evidence/atom pair"):
        bindings.validate_binding_semantics(source, policy, producers)
