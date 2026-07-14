from __future__ import annotations

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
        if calls == 2:
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
