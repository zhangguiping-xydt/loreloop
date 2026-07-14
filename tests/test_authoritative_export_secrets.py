from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import run_authoritative_export_qa as qa


ROOT = Path(__file__).resolve().parents[1]
SECRET_FIXTURE = ROOT / ".omo/evidence/authoritative-export-spec-v4/fixtures/secret-cases.json"


def test_portable_secret_fixture_contains_no_raw_equality_oracle() -> None:
    # Given: the current frozen portable secret fixture.
    # When: raw secrets, raw identities, and unkeyed commitments are scanned against capsules.
    report = qa.verify_portable_secret_fixture(SECRET_FIXTURE)

    # Then: every frozen case is clean.
    assert report.case_count == 6
    assert report.leak_count == 0


def test_portable_secret_scan_rejects_a_benign_named_raw_hash(tmp_path: Path) -> None:
    # Given: one frozen case with a raw secret SHA hidden under a benign field name.
    fixture = qa.require_mapping(qa.load_json_value(SECRET_FIXTURE), "secret fixture")
    cases = qa.require_array(fixture.get("cases"), "secret cases")
    first_case = qa.require_mapping(cases[0], "secret case")
    portable = qa.require_mapping(first_case.get("portable_capsule"), "portable capsule")
    mutated_portable = dict(portable)
    mutated_portable["proof"] = qa.require_text(first_case.get("raw_secret_sha256"), "raw hash")
    mutated_case = dict(first_case)
    mutated_case["portable_capsule"] = mutated_portable
    mutated_cases = list(cases)
    mutated_cases[0] = mutated_case
    mutated_fixture = dict(fixture)
    mutated_fixture["cases"] = mutated_cases
    mutated = tmp_path / "secret-cases.json"
    _ = mutated.write_text(json.dumps(mutated_fixture), encoding="utf-8")

    # When / Then: value-based scanning rejects the mutation.
    with pytest.raises(qa.SecretViolation, match="toml-short"):
        _ = qa.verify_portable_secret_fixture(mutated)


def test_portable_secret_scan_handles_a_non_utf8_secret_explicitly(tmp_path: Path) -> None:
    # Given: one binary secret that has no valid UTF-8 text representation.
    fixture = qa.require_mapping(qa.load_json_value(SECRET_FIXTURE), "secret fixture")
    cases = qa.require_array(fixture.get("cases"), "secret cases")
    first_case = qa.require_mapping(cases[0], "secret case")
    portable = qa.require_mapping(first_case.get("portable_capsule"), "portable capsule")
    mutated_portable = dict(portable)
    mutated_portable["body_base64"] = "c2FmZQ=="
    mutated_case = dict(first_case)
    mutated_case["secret_base64"] = "/w=="
    mutated_case["portable_capsule"] = mutated_portable
    mutated_cases = list(cases)
    mutated_cases[0] = mutated_case
    mutated_fixture = dict(fixture)
    mutated_fixture["cases"] = mutated_cases
    mutated = tmp_path / "binary-secret-cases.json"
    _ = mutated.write_text(json.dumps(mutated_fixture), encoding="utf-8")

    # When: the scanner takes the explicit non-text outcome.
    report = qa.verify_portable_secret_fixture(mutated)

    # Then: binary equality is still checked in decoded body bytes without silent handling.
    assert report.case_count == 6
    assert report.leak_count == 0
