from __future__ import annotations

import json
from pathlib import Path

import pytest

from loreloop.knowledge import authoritative_publish


def _residue(parent: Path, name: str) -> tuple[Path, ...]:
    return tuple(parent.glob(f".{name}.loreloop-*"))


def test_publish_tree_switches_complete_directory_and_preserves_operator_files(
    tmp_path: Path,
) -> None:
    output = tmp_path / "export"
    authoritative_publish.publish_tree(
        output,
        (("one.md", "old\n"), ("optional.md", "old optional\n")),
        managed_filenames=("one.md", "optional.md"),
    )
    (output / "keep.txt").write_text("operator\n", encoding="utf-8")

    authoritative_publish.publish_tree(
        output,
        (("one.md", "new\n"),),
        managed_filenames=("one.md", "optional.md"),
    )

    assert (output / "one.md").read_text(encoding="utf-8") == "new\n"
    assert not (output / "optional.md").exists()
    assert (output / "keep.txt").read_text(encoding="utf-8") == "operator\n"
    assert _residue(tmp_path, output.name) == ()


def test_recovery_finishes_cleanup_after_crash_immediately_after_exchange(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "export"
    authoritative_publish.publish_tree(output, (("doc.md", "old\n"),))
    real_write = authoritative_publish._atomic_json
    calls = 0

    def crash_on_installed(path: Path, payload: dict[str, object]) -> None:
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RuntimeError("simulated crash")
        real_write(path, payload)

    monkeypatch.setattr(authoritative_publish, "_atomic_json", crash_on_installed)
    with pytest.raises(RuntimeError, match="simulated crash"):
        authoritative_publish.publish_tree(output, (("doc.md", "new\n"),))

    assert (output / "doc.md").read_text(encoding="utf-8") == "new\n"
    monkeypatch.setattr(authoritative_publish, "_atomic_json", real_write)
    authoritative_publish.recover_publication(output)
    assert (output / "doc.md").read_text(encoding="utf-8") == "new\n"
    assert _residue(tmp_path, output.name) == ()


def test_recovery_finishes_cleanup_after_stage_was_partially_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "export"
    authoritative_publish.publish_tree(
        output,
        (("doc.md", "old\n"), ("stale.md", "old stale\n")),
    )
    real_remove = authoritative_publish._remove_tree

    def crash_during_cleanup(path: Path) -> None:
        if path.exists() and path.name.startswith(".export.loreloop-stage-"):
            (path / "doc.md").unlink(missing_ok=True)
            raise RuntimeError("simulated cleanup crash")
        real_remove(path)

    monkeypatch.setattr(authoritative_publish, "_remove_tree", crash_during_cleanup)
    with pytest.raises(RuntimeError, match="cleanup crash"):
        authoritative_publish.publish_tree(output, (("doc.md", "new\n"),))

    assert (output / "doc.md").read_text(encoding="utf-8") == "new\n"
    monkeypatch.setattr(authoritative_publish, "_remove_tree", real_remove)
    authoritative_publish.recover_publication(output)
    assert (output / "doc.md").read_text(encoding="utf-8") == "new\n"
    assert _residue(tmp_path, output.name) == ()


def test_first_install_does_not_replace_an_output_that_appears_during_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "export"
    real_install = authoritative_publish._install_no_replace

    def collide(stage: Path, destination: Path) -> None:
        destination.mkdir()
        (destination / "operator.txt").write_text("operator\n", encoding="utf-8")
        real_install(stage, destination)

    monkeypatch.setattr(authoritative_publish, "_install_no_replace", collide)
    with pytest.raises(authoritative_publish.PublicationError, match="appeared"):
        authoritative_publish.publish_tree(output, (("doc.md", "new\n"),))

    assert (output / "operator.txt").read_text(encoding="utf-8") == "operator\n"
    assert not (output / "doc.md").exists()
    assert _residue(tmp_path, output.name) == ()


@pytest.mark.parametrize("timing", ["before", "after"])
def test_update_preserves_operator_file_arriving_around_directory_exchange(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, timing: str
) -> None:
    output = tmp_path / "export"
    authoritative_publish.publish_tree(output, (("doc.md", "old\n"),))
    real_exchange = authoritative_publish._exchange
    real_write = authoritative_publish._atomic_json
    journal_writes = 0

    def exchange_with_late_file(stage: Path, destination: Path) -> bool:
        if timing == "before":
            (destination / "late.txt").write_text("operator\n", encoding="utf-8")
        exchanged = real_exchange(stage, destination)
        if timing == "after":
            (destination / "late.txt").write_text("operator\n", encoding="utf-8")
        return exchanged

    def crash_after_exchange(path: Path, payload: dict[str, object]) -> None:
        nonlocal journal_writes
        journal_writes += 1
        if journal_writes == 3:
            raise RuntimeError("simulated post-exchange crash")
        real_write(path, payload)

    monkeypatch.setattr(authoritative_publish, "_exchange", exchange_with_late_file)
    monkeypatch.setattr(authoritative_publish, "_atomic_json", crash_after_exchange)
    with pytest.raises(RuntimeError, match="post-exchange crash"):
        authoritative_publish.publish_tree(output, (("doc.md", "new\n"),))

    assert (output / "doc.md").read_text(encoding="utf-8") == "new\n"
    monkeypatch.setattr(authoritative_publish, "_atomic_json", real_write)
    authoritative_publish.recover_publication(output)
    assert (output / "late.txt").read_text(encoding="utf-8") == "operator\n"
    assert _residue(tmp_path, output.name) == ()


@pytest.mark.parametrize("timing", ["before", "after"])
def test_update_preserves_operator_file_modified_around_directory_exchange(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, timing: str
) -> None:
    output = tmp_path / "export"
    authoritative_publish.publish_tree(output, (("doc.md", "old\n"),))
    (output / "keep.txt").write_text("baseline\n", encoding="utf-8")
    real_exchange = authoritative_publish._exchange

    def exchange_with_operator_edit(stage: Path, destination: Path) -> bool:
        if timing == "before":
            (destination / "keep.txt").write_text("before\n", encoding="utf-8")
        exchanged = real_exchange(stage, destination)
        if timing == "after":
            (destination / "keep.txt").write_text("after\n", encoding="utf-8")
        return exchanged

    monkeypatch.setattr(authoritative_publish, "_exchange", exchange_with_operator_edit)

    authoritative_publish.publish_tree(output, (("doc.md", "new\n"),))

    assert (output / "doc.md").read_text(encoding="utf-8") == "new\n"
    assert (output / "keep.txt").read_text(encoding="utf-8") == f"{timing}\n"
    assert _residue(tmp_path, output.name) == ()


def test_publish_rejects_symlinks_in_preserved_operator_tree(tmp_path: Path) -> None:
    output = tmp_path / "export"
    output.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    try:
        (output / "redirect").symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable")

    with pytest.raises(authoritative_publish.PublicationError, match="symlink"):
        authoritative_publish.publish_tree(output, (("doc.md", "new\n"),))

    assert outside.read_text(encoding="utf-8") == "outside\n"
    assert not (output / "doc.md").exists()
    assert _residue(tmp_path, output.name) == ()


def test_preflight_absence_remains_no_replace_until_first_install(tmp_path: Path) -> None:
    output = tmp_path / "export"
    output.mkdir()
    (output / "operator.txt").write_text("operator\n", encoding="utf-8")

    with pytest.raises(authoritative_publish.PublicationError, match="appeared"):
        authoritative_publish.publish_tree(
            output,
            (("doc.md", "new\n"),),
            expected_output_exists=False,
        )

    assert (output / "operator.txt").read_text(encoding="utf-8") == "operator\n"
    assert not (output / "doc.md").exists()
    assert _residue(tmp_path, output.name) == ()


def test_recovery_discards_partial_stage_created_before_install_intent(tmp_path: Path) -> None:
    output = tmp_path / "export"
    stage = tmp_path / ".export.loreloop-stage-interrupted"
    stage.mkdir()
    (stage / "partial.md").write_text("partial\n", encoding="utf-8")
    journal = tmp_path / ".export.loreloop-journal.json"
    journal.write_text(
        json.dumps(
            {
                "version": 3,
                "target_name": "export",
                "stage_name": stage.name,
                "old_digest": None,
                "managed_filenames": ["doc.md"],
                "state": "staging",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    authoritative_publish.recover_publication(output)

    assert not stage.exists()
    assert not journal.exists()


def test_recovery_rejects_extra_managed_file_after_exchange(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "export"
    authoritative_publish.publish_tree(
        output,
        (("doc.md", "old\n"), ("stale.md", "old stale\n")),
        managed_filenames=("doc.md", "stale.md"),
    )
    real_write = authoritative_publish._atomic_json
    calls = 0

    def crash_after_exchange(path: Path, payload: dict[str, object]) -> None:
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RuntimeError("simulated post-exchange crash")
        real_write(path, payload)

    monkeypatch.setattr(authoritative_publish, "_atomic_json", crash_after_exchange)
    with pytest.raises(RuntimeError, match="post-exchange crash"):
        authoritative_publish.publish_tree(
            output,
            (("doc.md", "new\n"),),
            managed_filenames=("doc.md", "stale.md"),
        )
    (output / "stale.md").write_text("racing managed file\n", encoding="utf-8")
    monkeypatch.setattr(authoritative_publish, "_atomic_json", real_write)

    with pytest.raises(authoritative_publish.PublicationError, match="unrecognized"):
        authoritative_publish.recover_publication(output)

    assert (tmp_path / ".export.loreloop-journal.json").exists()


def test_recovery_rejects_extra_managed_file_after_identical_exchange(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "export"
    authoritative_publish.publish_tree(
        output,
        (("doc.md", "same\n"),),
        managed_filenames=("doc.md", "stale.md"),
    )
    real_write = authoritative_publish._atomic_json
    calls = 0

    def crash_after_exchange(path: Path, payload: dict[str, object]) -> None:
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RuntimeError("simulated post-exchange crash")
        real_write(path, payload)

    monkeypatch.setattr(authoritative_publish, "_atomic_json", crash_after_exchange)
    with pytest.raises(RuntimeError, match="post-exchange crash"):
        authoritative_publish.publish_tree(
            output,
            (("doc.md", "same\n"),),
            managed_filenames=("doc.md", "stale.md"),
        )
    (output / "stale.md").write_text("racing managed file\n", encoding="utf-8")
    monkeypatch.setattr(authoritative_publish, "_atomic_json", real_write)

    with pytest.raises(authoritative_publish.PublicationError, match="unrecognized"):
        authoritative_publish.recover_publication(output)

    assert (tmp_path / ".export.loreloop-journal.json").exists()
