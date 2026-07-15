"""Project governed Web-test execution results into authoritative acceptance facts."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from ..evidence.chain import EvidenceRecord
from ..webexplore.scenarios import (
    WEB_TEST_APPROVED_EVENT,
    WEB_TEST_EXECUTED_EVENT,
    WebScenarioError,
    parse_web_scenario,
)
from .authoritative_records import (
    DetectionError,
    DetectionReport,
    SourceRef,
    WebKnowledgeRecord,
)
from .authoritative_source import SnapshotBlob

MAX_WEB_TEST_RESULTS = 10_000
MAX_WEB_TEST_RESULT_BYTES = 1024 * 1024
MAX_WEB_TEST_RESULT_TOTAL_BYTES = 64 * 1024 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ASSERTION_KINDS = {"contains", "absent", "title-contains", "url"}


def build_governed_web_test_results(
    records: tuple[EvidenceRecord, ...] | list[EvidenceRecord],
) -> tuple[DetectionReport, tuple[SnapshotBlob, ...]]:
    """Select each approved scenario's latest matching, chain-verified execution."""
    approvals: dict[str, tuple[str, str]] = {}
    latest: dict[str, EvidenceRecord] = {}
    for record in records:
        if record.event == WEB_TEST_APPROVED_EVENT:
            scenario_id, digest, title = _approval(record)
            approvals[scenario_id] = (digest, title)
            latest.pop(scenario_id, None)
        elif record.event == WEB_TEST_EXECUTED_EVENT:
            scenario_id = record.payload.get("scenario_id")
            digest = record.payload.get("scenario_digest")
            if (
                isinstance(scenario_id, str)
                and isinstance(digest, str)
                and approvals.get(scenario_id, (None, None))[0] == digest
            ):
                latest[scenario_id] = record
    if len(latest) > MAX_WEB_TEST_RESULTS:
        raise DetectionError(f"governed Web-test result count exceeds {MAX_WEB_TEST_RESULTS}")

    result_records: list[WebKnowledgeRecord] = []
    blobs: list[SnapshotBlob] = []
    total = 0
    for scenario_id, execution in sorted(latest.items()):
        digest, title = approvals[scenario_id]
        status = execution.payload.get("status")
        if status not in {"passed", "failed", "blocked"}:
            raise DetectionError(f"Web-test execution has invalid status: {scenario_id}")
        assertions = _assertions(execution.payload.get("assertions"), scenario_id)
        if status == "passed" and (
            not assertions or not all(item["passed"] for item in assertions)
        ):
            raise DetectionError(f"passing Web-test result is inconsistent: {scenario_id}")
        passed = sum(item["passed"] for item in assertions)
        statement = (
            f"场景 `{scenario_id}` 最近一次受治理执行结果："
            f"{'通过' if status == 'passed' else '失败' if status == 'failed' else '已阻止'}；"
            f"断言 {passed}/{len(assertions)} 通过。"
        )
        payload = {
            "scenario_id": scenario_id,
            "scenario_digest": digest,
            "title": title,
            "status": status,
            "assertions": assertions,
            "execution_record": execution.index,
            "execution_chain_hash": execution.chain_hash,
            "trace_artifact": _artifact_ref(
                execution.payload.get("trace_artifact"), scenario_id, "trace"
            ),
            "observation_artifact": _artifact_ref(
                execution.payload.get("observation_artifact"), scenario_id, "observation"
            ),
        }
        data = (
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
        ).encode("utf-8")
        if len(data) > MAX_WEB_TEST_RESULT_BYTES:
            raise DetectionError(f"governed Web-test result is too large: {scenario_id}")
        total += len(data)
        if total > MAX_WEB_TEST_RESULT_TOTAL_BYTES:
            raise DetectionError("governed Web-test results exceed the total byte limit")
        safe_id = hashlib.sha256(scenario_id.encode()).hexdigest()
        path = f"web-tests/results/{safe_id}.json"
        source = SourceRef("@web-tests", path, 1)
        result_records.append(
            WebKnowledgeRecord(
                f"web-test-result:{scenario_id}:{execution.index}",
                "acceptance",
                f"Web 测试结果：{title}",
                statement,
                f"evidence:web_test_executed:{execution.index}",
                execution.chain_hash,
                source,
            )
        )
        blobs.append(
            SnapshotBlob(
                "@web-tests",
                path,
                data,
                hashlib.sha256(data).hexdigest(),
                len(data),
            )
        )
    return DetectionReport(web_knowledge=tuple(result_records)), tuple(blobs)


def _approval(record: EvidenceRecord) -> tuple[str, str, str]:
    scenario_id = record.payload.get("scenario_id")
    digest = record.payload.get("scenario_digest")
    raw = record.payload.get("scenario")
    path = record.payload.get("path")
    if not isinstance(scenario_id, str) or not isinstance(digest, str):
        raise DetectionError("Web-test approval lacks scenario identity")
    try:
        scenario = parse_web_scenario(raw)
    except WebScenarioError as exc:
        raise DetectionError(f"Web-test approval has an invalid scenario: {exc}") from exc
    expected_path = f"tests/loreloop/web/{scenario.scenario_id}.json"
    path_value = path if isinstance(path, str) else ""
    peer_path = re.fullmatch(
        rf"repo:[A-Za-z0-9][A-Za-z0-9._-]{{0,63}}/{re.escape(expected_path)}",
        path_value,
    )
    if (
        scenario.scenario_id != scenario_id
        or scenario.digest != digest
        or (path_value != expected_path and peer_path is None)
    ):
        raise DetectionError(f"Web-test approval identity mismatch: {scenario_id}")
    return scenario_id, digest, scenario.title


def _assertions(value: Any, scenario_id: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > 64:
        raise DetectionError(f"Web-test execution has invalid assertions: {scenario_id}")
    result: list[dict[str, Any]] = []
    for item in value:
        if (
            not isinstance(item, dict)
            or set(item) != {"kind", "value", "passed"}
            or item["kind"] not in _ASSERTION_KINDS
            or not isinstance(item["value"], str)
            or not item["value"]
            or len(item["value"]) > 4_000
            or not isinstance(item["passed"], bool)
        ):
            raise DetectionError(f"Web-test execution has invalid assertions: {scenario_id}")
        result.append(
            {"kind": item["kind"], "value": item["value"], "passed": item["passed"]}
        )
    return result


def _artifact_ref(value: Any, scenario_id: str, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise DetectionError(
            f"Web-test execution has invalid {label} artifact: {scenario_id}"
        )
    return value
