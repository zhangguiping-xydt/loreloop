from __future__ import annotations

import hashlib
import subprocess
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from loreloop.cli import _select_governed_web_entries, main
from loreloop.evidence.chain import EvidenceRecord
from loreloop.knowledge.authoritative_web_input import build_governed_web_input
from loreloop.knowledge.endorsement import TrustProjectionError, entry_digest
from loreloop.knowledge.model import Channel, Entry, Kind, Source


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _repository(path: Path) -> Path:
    path.mkdir()
    _git(path, "init", "-q")
    _git(path, "config", "user.name", "LoreLoop Test")
    _git(path, "config", "user.email", "loreloop@example.invalid")
    (path / "app.py").write_text('@app.get("/health")\ndef health(): return True\n')
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "initial")
    return path


def _record(index: int, event: str, payload: dict[str, object]) -> EvidenceRecord:
    return EvidenceRecord(index, "2026-07-14T00:00:00Z", event, payload, "p", "c", "s")


def _web_entry() -> Entry:
    return Entry(
        id="web-entry-1",
        title="保存按钮会显示成功提示",
        content="用户点击保存后，页面显示保存成功，并保持在当前编辑页。",
        kind=Kind.BEHAVIOR,
        source=Source(
            Channel.WEB,
            "https://app.example.test/settings",
            snapshot_ref="a" * 64,
        ),
    )


def _verified_payload(entry: Entry) -> dict[str, object]:
    return {
        "run_id": "verify-20260714000000",
        "entry_id": entry.id,
        "entry_digest": entry_digest(entry),
        "claim": entry.content,
        "url": entry.source.locator,
        "page_snapshot": entry.source.snapshot_ref,
        "artifact": "b" * 64,
        "judge": "llm",
        "verified_via": "browser",
    }


def _web_entries_all_kinds() -> tuple[Entry, ...]:
    entries = []
    for index, kind in enumerate(Kind):
        entries.append(
            Entry(
                id=f"web-entry-{index}",
                title=f"{kind.value} Web fact",
                content=f"Observed {kind.value} statement from the running application.",
                kind=kind,
                source=Source(
                    Channel.WEB,
                    f"https://app.example.test/{kind.value}",
                    snapshot_ref=f"sha256:{kind.value}",
                ),
            )
        )
    return tuple(entries)


def test_governed_web_selection_requires_approval_and_verification() -> None:
    entry = _web_entry()
    digest = entry_digest(entry)
    approved = _record(
        0,
        "curation_changed",
        {"entry_id": entry.id, "curation": "approved", "entry_digest": digest},
    )
    verified = _record(
        1,
        "entry_verified",
        _verified_payload(entry),
    )

    assert _select_governed_web_entries([entry], [approved]) == ()
    assert _select_governed_web_entries([entry], [verified]) == ()
    assert _select_governed_web_entries([entry], [approved, verified]) == (entry,)
    harvested = _record(
        1,
        "knowledge_harvested",
        {"minted": {entry.id: digest}},
    )
    assert _select_governed_web_entries([entry], [approved, harvested]) == ()
    incomplete_verified = _record(
        2,
        "entry_verified",
        {"entry_id": entry.id, "entry_digest": digest},
    )
    assert _select_governed_web_entries(
        [entry], [approved, incomplete_verified]
    ) == ()
    contradicted = _record(2, "entry_contradicted", {"entry_id": entry.id})
    assert _select_governed_web_entries(
        [entry], [approved, verified, contradicted]
    ) == ()


def test_governed_web_selection_does_not_reuse_verification_after_rejection() -> None:
    entry = _web_entry()
    digest = entry_digest(entry)
    records = [
        _record(
            0,
            "curation_changed",
            {"entry_id": entry.id, "curation": "approved", "entry_digest": digest},
        ),
        _record(1, "entry_verified", _verified_payload(entry)),
        _record(
            2,
            "curation_changed",
            {"entry_id": entry.id, "curation": "rejected", "entry_digest": digest},
        ),
        _record(
            3,
            "curation_changed",
            {"entry_id": entry.id, "curation": "draft", "entry_digest": digest},
        ),
        _record(
            4,
            "curation_changed",
            {"entry_id": entry.id, "curation": "approved", "entry_digest": digest},
        ),
    ]

    assert _select_governed_web_entries([entry], records) == ()


def test_governed_web_selection_rejects_sqlite_content_rewrite() -> None:
    entry = _web_entry()
    digest = entry_digest(entry)
    records = [
        _record(
            0,
            "curation_changed",
            {"entry_id": entry.id, "curation": "approved", "entry_digest": digest},
        ),
        _record(1, "entry_verified", _verified_payload(entry)),
    ]
    rewritten = replace(entry, content="SQLite 中被改写但未重新批准或验证的内容。")

    with pytest.raises(TrustProjectionError, match="unexplained content/source digest"):
        _select_governed_web_entries([rewritten], records)


def test_governed_web_selection_requires_reapproval_after_reingest() -> None:
    original = _web_entry()
    original_digest = entry_digest(original)
    reingested = replace(
        original,
        content="页面现在显示新的保存结果；这份内容尚未重新获得人工批准。",
    )
    current_digest = entry_digest(reingested)
    records = [
        _record(
            0,
            "curation_changed",
            {
                "entry_id": original.id,
                "curation": "approved",
                "entry_digest": original_digest,
            },
        ),
        _record(
            1,
            "entry_reingested",
            {"entry_id": original.id, "entry_digest": current_digest},
        ),
        _record(
            2,
            "entry_verified",
            _verified_payload(reingested),
        ),
    ]

    assert _select_governed_web_entries([reingested], records) == ()

    records.append(
        _record(
            3,
            "curation_changed",
            {
                "entry_id": original.id,
                "curation": "approved",
                "entry_digest": current_digest,
            },
        )
    )
    assert _select_governed_web_entries([reingested], records) == (reingested,)


def test_governed_web_input_binds_content_to_synthetic_evidence() -> None:
    entry = _web_entry()

    report, blobs = build_governed_web_input((entry,))

    assert report.web_knowledge[0].locator == "https://app.example.test/settings"
    assert report.web_knowledge[0].statement == entry.content
    assert blobs[0].repository_alias == "@web"
    assert blobs[0].path == f"web/{hashlib.sha256(entry.id.encode()).hexdigest()}.json"
    assert blobs[0].data is not None and entry.content.encode() in blobs[0].data


def test_package_includes_governed_web_and_can_be_searched_without_a_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = _repository(tmp_path / "demo")
    package = tmp_path / "baseline.zip"
    monkeypatch.chdir(repo)
    monkeypatch.setattr(
        "loreloop.cli._governed_web_entries", lambda _workdir: _web_entries_all_kinds()
    )

    assert main(
        [
            "knowledge",
            "export",
            "--format",
            "package",
            "--output",
            str(package),
            "--include-web",
        ]
    ) == 0
    assert main(["knowledge", "replay", str(package)]) == 0
    with zipfile.ZipFile(package) as archive:
        markdown = {
            name: archive.read(name).decode("utf-8")
            for name in archive.namelist()
            if name.endswith(".md")
        }
    assert any("## Web 需求事实" in text for text in markdown.values())
    assert any("## Web 接口观察" in text for text in markdown.values())
    assert any("## Web 运行架构观察" in text for text in markdown.values())
    assert any("## Web 页面与行为观察" in text for text in markdown.values())
    assert any("## Web 运行约束" in text for text in markdown.values())
    assert any("## Web 验收事实" in text for text in markdown.values())
    _ = capsys.readouterr()

    isolated = tmp_path / "isolated"
    isolated.mkdir()
    monkeypatch.chdir(isolated)
    assert main(
        [
            "knowledge",
            "search",
            "behavior",
            "--package",
            str(package),
            "--limit",
            "3",
        ]
    ) == 0

    output = capsys.readouterr().out
    assert "用户手册.md#Web 页面与行为观察" in output
    assert "Observed behavior statement" in output
    assert not (isolated / ".loreloop").exists()

    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(package) as source, zipfile.ZipFile(tampered, "w") as target:
        for info in source.infolist():
            data = source.read(info.filename)
            if info.filename.endswith("用户手册.md"):
                data += b"\nchanged\n"
            target.writestr(info, data)
    assert main(
        ["knowledge", "search", "behavior", "--package", str(tampered)]
    ) == 2
    assert "baseline search failed" in capsys.readouterr().err
