from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import run_authoritative_export_qa as qa


ROOT = Path(__file__).resolve().parents[1]


def test_requirements_verifier_detects_clause_byte_drift(tmp_path: Path) -> None:
    # Given: a one-clause plan and a traceability map bound to its exact stripped line bytes.
    plan = tmp_path / "plan.md"
    clause = "- [SCOPE-001] Exact requirement."
    _ = plan.write_text(clause + "\n", encoding="utf-8")
    evidence = tmp_path / "evidence.json"
    _ = evidence.write_text("{}\n", encoding="utf-8")
    requirements = tmp_path / "requirements-map.json"
    _ = requirements.write_text(
        json.dumps(
            {
                "schema_version": 4,
                "mapping_role": "traceability_only_not_semantic_proof",
                "evidence_ids": ["contract:evidence.json"],
                "requirements": {
                    "SCOPE-001": {
                        "clause_text_sha256": hashlib.sha256(clause.encode()).hexdigest(),
                        "evidence_ids": ["contract:evidence.json"],
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    # When: the exact bytes are verified, then one clause byte changes.
    summary = qa.verify_requirements_traceability(plan, requirements, tmp_path)
    _ = plan.write_text(clause + " changed\n", encoding="utf-8")

    # Then: the original map passes and the drifted plan fails.
    assert summary.clause_count == 1
    with pytest.raises(qa.RequirementViolation, match="SCOPE-001"):
        _ = qa.verify_requirements_traceability(plan, requirements, tmp_path)


def test_typed_json_boundary_accepts_only_the_closed_recursive_domain() -> None:
    # Given: the complete recursive JSON scalar/container domain.
    accepted: qa.JsonInput = {
        "null": None,
        "boolean": True,
        "integer": 7,
        "string": "é",
        "nested": [{"value": "ok"}],
    }

    # When: the stdlib-decoder boundary is narrowed.
    value = qa.validate_json_value(accepted)

    # Then: nested arrays/maps and every admitted scalar remain typed.
    assert value == accepted


@pytest.mark.parametrize(
    "invalid",
    [
        1.5,
        9_007_199_254_740_992,
        "e\u0301",
        "\ud800",
        {1: "not-a-string-key"},
    ],
)
def test_typed_json_boundary_rejects_values_outside_the_closed_domain(
    invalid: float | int | str | dict[int, qa.JsonInput],
) -> None:
    # Given / When / Then: invalid decoded values fail before contract access.
    with pytest.raises(qa.QaViolation):
        _ = qa.validate_json_value(invalid)


def test_pytest_result_requires_the_exact_accepted_baseline_failure() -> None:
    # Given: a JUnit result with the sole operator-approved release metadata failure.
    results = (
        qa.TestCaseResult(
            nodeid="tests/test_release_metadata.py::test_uv_lock_uses_only_public_pypi_registry",
            outcome=qa.TestOutcome.FAILED,
        ),
        qa.TestCaseResult(
            nodeid="tests/test_paths.py::test_default", outcome=qa.TestOutcome.PASSED
        ),
    )

    # When: the full-suite result is assessed using exit status and the failure map.
    qa.require_pytest_result(
        exit_code=1,
        test_cases=results,
        expected_failures=(
            "tests/test_release_metadata.py::test_uv_lock_uses_only_public_pypi_registry",
        ),
    )

    # Then: a misleading exit-zero status is rejected even with the same printed test names.
    with pytest.raises(qa.QaViolation):
        qa.require_pytest_result(
            exit_code=0,
            test_cases=results,
            expected_failures=(
                "tests/test_release_metadata.py::test_uv_lock_uses_only_public_pypi_registry",
            ),
        )


def test_receipt_wrapper_records_the_real_exit_status_not_success_text(tmp_path: Path) -> None:
    # Given: a command that creates one failing JUnit case, prints PASS, and exits 7.
    receipt = tmp_path / "receipt.json"
    junit = tmp_path / "junit.xml"
    junit_body = "".join(
        (
            '<testsuite tests="1" failures="1" errors="0" skipped="0">',
            '<testcase classname="tests.test_sample" name="test_failure">',
            '<failure message="boom" /></testcase></testsuite>',
        )
    )
    spec_manifest = tmp_path / "spec-manifest.json"
    _ = spec_manifest.write_text("{}\n", encoding="utf-8")
    plan = tmp_path / "plan.md"
    _ = plan.write_text("# plan\n", encoding="utf-8")

    # When: the receipt wrapper runs the misleading child command.
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/run_with_receipt.py"),
            "--task",
            "todo-2",
            "--receipt",
            str(receipt),
            "--spec-manifest",
            str(spec_manifest),
            "--approved-plan",
            str(plan),
            "--junit-xml",
            str(junit),
            "--",
            sys.executable,
            "-c",
            (
                "import sys; from pathlib import Path;"
                f"Path({str(junit)!r}).write_text({junit_body!r}, encoding='utf-8');"
                "print('PASS'); sys.exit(7)"
            ),
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    body = qa.require_mapping(qa.load_json_value(receipt), "receipt")

    # Then: wrapper and receipt expose exit 7 despite the success-looking stdout.
    assert completed.returncode == 7
    assert completed.stdout == "PASS\n"
    assert body["exit_code"] == 7
    assert qa.require_array(body.get("test_cases"), "receipt.test_cases") == [
        {
            "nodeid": "tests.test_sample::test_failure",
            "outcome": "failed",
        }
    ]


def test_receipt_wrapper_rejects_a_preexisting_stale_junit(tmp_path: Path) -> None:
    # Given: an unchanged 48-pass JUnit file from an earlier command.
    receipt = tmp_path / "receipt.json"
    junit = tmp_path / "junit.xml"
    test_cases = "".join(
        f'<testcase classname="tests.test_stale" name="test_{index}" />' for index in range(48)
    )
    stale_bytes = (
        f'<testsuite tests="48" failures="0" errors="0" skipped="0">{test_cases}</testsuite>'
    )
    _ = junit.write_text(stale_bytes, encoding="utf-8")
    spec_manifest = tmp_path / "spec-manifest.json"
    _ = spec_manifest.write_text("{}\n", encoding="utf-8")
    plan = tmp_path / "plan.md"
    _ = plan.write_text("# plan\n", encoding="utf-8")

    # When: an unrelated child only prints success and never creates JUnit.
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/run_with_receipt.py"),
            "--task",
            "todo-2",
            "--receipt",
            str(receipt),
            "--spec-manifest",
            str(spec_manifest),
            "--approved-plan",
            str(plan),
            "--junit-xml",
            str(junit),
            "--",
            sys.executable,
            "-c",
            "print('PASS')",
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )

    # Then: the stale artifact is preserved, the child is not launched, and no receipt is trusted.
    assert completed.returncode == 2
    assert completed.stdout == ""
    assert "JUnit output path must be absent" in completed.stderr
    assert junit.read_text(encoding="utf-8") == stale_bytes
    assert not receipt.exists()
