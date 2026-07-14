from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from loreloop.knowledge import authoritative_bindings, authoritative_types


def test_portable_git_identity_strips_only_a_validated_sha1_tag() -> None:
    # Given: one valid portable SHA-1 identity.
    identity = authoritative_types.GitObjectId.parse("sha1:" + "a" * 40)

    # When: the value is prepared for a literal git cat-file argv.
    raw_hex = identity.git_sha1_hex()

    # Then: only validated lowercase raw hex crosses the Git boundary.
    assert raw_hex == "a" * 40
    with pytest.raises(FrozenInstanceError):
        setattr(identity, "hex", "b" * 40)


@pytest.mark.parametrize(
    "tagged",
    ["sha1:" + "A" * 40, "sha1:abc", "sha256:" + "0" * 64, "0" * 40],
)
def test_portable_git_identity_rejects_invalid_or_non_sha1_git_inputs(tagged: str) -> None:
    # Given: a malformed or unsupported portable Git identity.
    # When / Then: parsing or raw Git conversion fails closed.
    with pytest.raises(authoritative_types.ContractViolation):
        _ = authoritative_types.GitObjectId.parse(tagged).git_sha1_hex()


def test_all_four_field_binding_variants_are_closed_and_immutable() -> None:
    # Given: one valid instance of every v4 field binding arm.
    source = authoritative_bindings.SourceBinding(
        evidence_id="EVD-" + "1" * 64,
        atom_id="ATM-" + "2" * 64,
        atom_pointer="/payload/name",
        transform=authoritative_bindings.SourceTransform.IDENTITY,
    )
    derived = authoritative_bindings.DerivedBinding(
        rule_id="document-row-v4",
        inputs=(
            authoritative_bindings.DerivedInput(
                bindable_id="API-" + "3" * 64,
                pointer="/id",
            ),
        ),
        projection=authoritative_bindings.DerivedProjection.ORDERED_COPY,
    )
    fixed = authoritative_bindings.FixedBinding(rule_id="fixed-v4", literal=False)
    observed = authoritative_bindings.ObservedBinding(
        observation_kind=authoritative_bindings.ObservationKind.PACKAGE_DERIVATION,
        observation_sha256="4" * 64,
        projection=authoritative_bindings.ObservedProjection.CANONICAL_JSON,
    )

    # When: their discriminators are inspected.
    kinds = tuple(binding.kind for binding in (source, derived, fixed, observed))

    # Then: the union is exhaustive and values cannot be mutated.
    assert kinds == ("source", "derived", "fixed", "observed")
    with pytest.raises(FrozenInstanceError):
        setattr(fixed, "rule_id", "changed")


def test_source_snapshot_seals_into_a_payload_free_package_envelope() -> None:
    # Given: one typed raw snapshot and its separately produced opaque seal.
    object_id = authoritative_types.GitObjectId.parse("sha1:" + "a" * 40)
    snapshot = authoritative_types.SourceSnapshot(
        repositories=(
            authoritative_types.RepositorySnapshot(
                alias=".",
                role="root",
                commit_id=object_id,
                tree_id=authoritative_types.GitObjectId.parse("sha1:" + "b" * 40),
                index_sha256="c" * 64,
                entries=(
                    authoritative_types.SnapshotEntry(
                        path="src/app.py",
                        mode="100644",
                        object_id=authoritative_types.GitObjectId.parse("sha1:" + "d" * 40),
                        byte_length=17,
                        blob_sha256="e" * 64,
                    ),
                ),
            ),
        )
    )
    seal = authoritative_types.SealedSnapshot(
        trust_domain_id="1" * 64,
        repository_config_digest="2" * 64,
        repository_aliases=(".",),
        source_snapshot_hmac="hmac-sha256:" + "3" * 64,
    )
    digests = authoritative_types.PackageDigests(
        semantic_core_sha256="4" * 64,
        package_id="5" * 64,
        post_ast_sha256="6" * 64,
        markdown_map_sha256="7" * 64,
        payload_tree_digest="8" * 64,
        source_snapshot_hmac=seal.source_snapshot_hmac,
    )

    # When: the package boundary consumes the seal instead of the raw snapshot.
    envelope = authoritative_types.PackageEnvelope(
        sealed_snapshot=seal,
        digests=digests,
        local_attestation="hmac-sha256:" + "9" * 64,
    )

    # Then: raw Git/blob identities stay in the producer-side snapshot only.
    assert snapshot.repositories[0].entries[0].path == "src/app.py"
    assert not hasattr(envelope.sealed_snapshot, "repositories")
    assert envelope.digests.source_snapshot_hmac == seal.source_snapshot_hmac


def test_package_envelope_rejects_a_replaced_snapshot_seal() -> None:
    # Given: package digests and an opaque seal with different source HMACs.
    seal = authoritative_types.SealedSnapshot(
        trust_domain_id="1" * 64,
        repository_config_digest="2" * 64,
        repository_aliases=(".",),
        source_snapshot_hmac="hmac-sha256:" + "3" * 64,
    )
    digests = authoritative_types.PackageDigests(
        semantic_core_sha256="4" * 64,
        package_id="5" * 64,
        post_ast_sha256="6" * 64,
        markdown_map_sha256="7" * 64,
        payload_tree_digest="8" * 64,
        source_snapshot_hmac="hmac-sha256:" + "a" * 64,
    )

    # When / Then: a later stage cannot replace the Todo 3 seal.
    with pytest.raises(authoritative_types.ContractViolation, match="seal mismatch"):
        _ = authoritative_types.PackageEnvelope(
            sealed_snapshot=seal,
            digests=digests,
            local_attestation="hmac-sha256:" + "9" * 64,
        )


def test_journal_entry_enforces_state_payload_contract() -> None:
    # Given: a PREPARE_INTENT journal carrying package payload too early.
    # When / Then: constructor parsing rejects the impossible state.
    with pytest.raises(authoritative_types.ContractViolation):
        _ = authoritative_types.JournalEntry(
            version=4,
            txid="a" * 32,
            target_name="export",
            target_sha256="b" * 64,
            parent_device=1,
            parent_inode=2,
            state=authoritative_types.JournalState.PREPARE_INTENT,
            sequence=1,
            package_id="c" * 64,
            semantic_core_sha256=None,
            post_ast_sha256=None,
            markdown_map_sha256=None,
            payload_tree_digest=None,
            created_at="2026-07-13T00:00:00.000000000Z",
            updated_at="2026-07-13T00:00:00.000000000Z",
            abort_reason=None,
            hmac="hmac-sha256:" + "d" * 64,
        )
