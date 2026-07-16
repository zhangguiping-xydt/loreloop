from __future__ import annotations

import unicodedata

import pytest

from loreloop.knowledge import authoritative_ids


def test_canon_v4_emits_exact_sorted_minimal_bytes() -> None:
    # Given: a closed canonical value whose insertion order differs from UTF-8 key order.
    value = {"é": [True, None, "\u0001"], "z": -7}

    # When: canon-v4 serializes the value.
    encoded = authoritative_ids.canon_v4(value)

    # Then: bytes are compact, minimally escaped, UTF-8 sorted, and newline-free.
    assert encoded == b'{"z":-7,"\xc3\xa9":[true,null,"\\u0001"]}'


def test_canon_v4_preserves_nested_tuple_and_utf8_key_order_contract() -> None:
    value = {
        "界面": ({"é": "已确认", "z": 2},),
        "array": [False, {"路径": "src/app.py", "line": 7}],
    }

    encoded = authoritative_ids.canon_v4(value)

    assert (
        encoded
        == (
            '{"array":[false,{"line":7,"路径":"src/app.py"}],"界面":[{"z":2,"é":"已确认"}]}'
        ).encode()
    )


@pytest.mark.parametrize(
    "invalid",
    [
        1.5,
        {"x"},
        9_007_199_254_740_992,
        "\ud800",
        unicodedata.normalize("NFD", "é"),
        {1: "not-a-string-key"},
    ],
)
def test_canon_v4_rejects_values_outside_the_closed_domain(
    invalid: float | set[str] | int | str | dict[int, str],
) -> None:
    # Given: a value outside canon-v4's scalar/array/map domain.
    # When / Then: boundary parsing fails closed with the typed contract error.
    with pytest.raises(authoritative_ids.CanonicalValueError):
        _ = authoritative_ids.canon_v4(invalid)


def test_record_id_matches_the_frozen_mod_vector() -> None:
    # Given: the first record identity vector from fixtures/semantic-core.json.
    identity = authoritative_ids.RecordIdentity(
        trust_domain_id="1" * 64,
        repository_config_digest="2" * 64,
        semantic_key={"alias": ".", "path": "app.py", "module_kind": "python_module"},
    )

    # When: the MOD identity is computed.
    record_id = authoritative_ids.record_id("MOD", identity)

    # Then: the exact full SHA-256 prefixed identifier is reproduced.
    assert record_id == "MOD-e00d039cf1e0549764859327f7931d27ca925b7e425e8ce86ad024847722a6f6"


def test_evidence_and_ref_ids_match_frozen_vectors() -> None:
    # Given: frozen evidence and ref formula inputs.
    evidence = authoritative_ids.EvidenceIdentity(
        alias=".",
        path="app.py",
        redacted_blob_sha256="a8afbe3207e1c31fa2c4e3c26717968ca89299ed1239aab0b6d048527b7e0bdd",
        redacted_start=0,
        redacted_end=1073,
    )
    ref = authoritative_ids.RefIdentity(
        kind="data_target",
        relation_or_access_or_null="reads",
        source_record_id="API-80b4f3b4c5e03b010044c0c9da19439408d72aa59ee9e40c57df0a2062d4b522",
        target_signature="users",
        evidence_id="EVD-07087a3ea5b6139ef211cd4b9ba8400e7e0de3543a8e4652801a6fb5cfa9dc15",
        branch_ordinal_or_null=None,
    )

    # When: the identities are computed independently.
    evidence_id = authoritative_ids.evidence_id(evidence)
    ref_id = authoritative_ids.ref_id(ref)

    # Then: both match the frozen source-derived vectors.
    assert evidence_id == "EVD-07087a3ea5b6139ef211cd4b9ba8400e7e0de3543a8e4652801a6fb5cfa9dc15"
    assert ref_id == "REF-6d35623f3aabc95111b517d1810cae50511e45fd58ad9716fe1d41be7d08ebe0"


def test_record_id_rejects_later_level_identity_in_semantic_key() -> None:
    # Given: an L2 module key polluted with a later L3 requirement identity.
    identity = authoritative_ids.RecordIdentity(
        trust_domain_id="1" * 64,
        repository_config_digest="2" * 64,
        semantic_key={
            "alias": ".",
            "path": "app.py",
            "module_kind": "python_module",
            "requirement_id": "REQ-" + "0" * 64,
        },
    )

    # When / Then: semantic-key shape/rank validation blocks the ID.
    with pytest.raises(authoritative_ids.IdentityContractError):
        _ = authoritative_ids.record_id("MOD", identity)


def test_require_unique_ids_rejects_duplicates() -> None:
    # Given: two equal semantic identifiers.
    duplicate = "API-" + "a" * 64

    # When / Then: collision/duplicate validation fails closed.
    with pytest.raises(authoritative_ids.IdentityContractError):
        authoritative_ids.require_unique_ids((duplicate, duplicate))


def test_record_formula_rejects_a_dedicated_non_record_prefix() -> None:
    # Given: a semantic key submitted to the dedicated evidence namespace.
    identity = authoritative_ids.RecordIdentity(
        trust_domain_id="1" * 64,
        repository_config_digest="2" * 64,
        semantic_key={"alias": "."},
    )

    # When / Then: record_id cannot substitute for the evidence formula.
    with pytest.raises(authoritative_ids.IdentityContractError, match="record prefix"):
        _ = authoritative_ids.record_id("EVD", identity)
