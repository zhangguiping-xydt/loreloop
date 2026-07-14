"""Crash-recoverable publication for an authoritative export directory."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import shutil
import stat
import tempfile
import uuid
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import TypeAlias


TreeManifest: TypeAlias = dict[str, str]


class PublicationError(RuntimeError):
    """An export directory cannot be installed without risking mixed output."""


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_json(path: Path, payload: Mapping[str, object]) -> None:
    fd, raw = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(raw)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            fd = -1
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if fd >= 0:
            os.close(fd)
        temporary.unlink(missing_ok=True)


def _tree_manifest(root: Path) -> TreeManifest:
    if root.is_symlink() or not root.is_dir():
        raise PublicationError(f"publication tree is not a real directory: {root}")
    manifest: TreeManifest = {}
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise PublicationError(f"publication tree contains a symlink: {relative}")
        if stat.S_ISDIR(info.st_mode):
            manifest[relative] = "directory"
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise PublicationError(f"publication tree contains an unsafe node: {relative}")
        manifest[relative] = "file:" + hashlib.sha256(path.read_bytes()).hexdigest()
    return manifest


def _manifest_digest(manifest: Mapping[str, str]) -> str:
    digest = hashlib.sha256(b"loreloop-export-tree-v1\0")
    for relative, identity in sorted(manifest.items()):
        if identity == "directory":
            digest.update(b"d\0" + relative.encode() + b"\0")
        else:
            digest.update(b"f\0" + relative.encode() + b"\0")
            digest.update(bytes.fromhex(identity.removeprefix("file:")))
    return digest.hexdigest()


def _tree_digest(root: Path | None) -> str | None:
    if root is None or not root.exists():
        return None
    return _manifest_digest(_tree_manifest(root))


def _validated_manifest(value: object) -> TreeManifest:
    if not isinstance(value, dict):
        raise PublicationError("publication journal has an invalid tree manifest")
    manifest: TreeManifest = {}
    for relative, identity in value.items():
        if (
            not isinstance(relative, str)
            or Path(relative).is_absolute()
            or any(part in {"", ".", ".."} for part in relative.split("/"))
            or not isinstance(identity, str)
            or (
                identity != "directory"
                and (
                    not identity.startswith("file:")
                    or len(identity) != 69
                    or any(character not in "0123456789abcdef" for character in identity[5:])
                )
            )
        ):
            raise PublicationError("publication journal has an invalid tree manifest")
        manifest[relative] = identity
    return manifest


def _tree_has_expected_managed(
    root: Path,
    expected: Mapping[str, str],
    managed_filenames: frozenset[str],
) -> bool:
    if not root.exists():
        return False
    actual = _tree_manifest(root)
    actual_managed = {
        relative: identity
        for relative, identity in actual.items()
        if relative.split("/", 1)[0] in managed_filenames
    }
    expected_managed = {
        relative: identity
        for relative, identity in expected.items()
        if relative.split("/", 1)[0] in managed_filenames
    }
    return actual_managed == expected_managed


def _journal_path(output: Path) -> Path:
    return output.parent / f".{output.name}.loreloop-journal.json"


def _remove_tree(path: Path) -> None:
    if not path.exists():
        return
    if path.is_symlink() or not path.is_dir():
        raise PublicationError(f"refusing to remove unsafe publication stage: {path}")
    shutil.rmtree(path)


def _managed_names(value: object) -> frozenset[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise PublicationError("publication journal has invalid managed filenames")
    names = frozenset(value)
    if any(Path(name).name != name for name in names):
        raise PublicationError("publication journal has invalid managed filenames")
    return names


def _copy_file_no_replace(source: Path, target: Path) -> None:
    source_descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    target_descriptor = -1
    created = False
    try:
        source_info = os.fstat(source_descriptor)
        if not stat.S_ISREG(source_info.st_mode) or source_info.st_nlink != 1:
            raise PublicationError(f"publication stage contains an unsafe node: {source}")
        try:
            target_descriptor = os.open(
                target,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0),
                stat.S_IMODE(source_info.st_mode) & 0o777,
            )
            created = True
        except FileExistsError:
            return
        while data := os.read(source_descriptor, 1024 * 1024):
            view = memoryview(data)
            while view:
                written = os.write(target_descriptor, view)
                view = view[written:]
        os.fsync(target_descriptor)
    except BaseException:
        if created:
            target.unlink(missing_ok=True)
        raise
    finally:
        if target_descriptor >= 0:
            os.close(target_descriptor)
        os.close(source_descriptor)


def _copy_file_replace(source: Path, target: Path) -> None:
    descriptor, raw_temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    temporary = Path(raw_temporary)
    os.close(descriptor)
    try:
        shutil.copyfile(source, temporary)
        os.chmod(temporary, stat.S_IMODE(source.stat().st_mode) & 0o777)
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        _fsync_directory(target.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _preserve_operator_files(
    stage: Path,
    output: Path,
    managed_filenames: frozenset[str],
    expected_tree: Mapping[str, str],
) -> None:
    if not stage.exists():
        return
    stage_manifest = _tree_manifest(stage)
    for relative, identity in sorted(stage_manifest.items()):
        if relative.split("/", 1)[0] in managed_filenames:
            continue
        target = output / relative
        expected_identity = expected_tree.get(relative)
        if identity == "directory":
            if not target.exists() and expected_identity == identity:
                continue
            try:
                target.mkdir()
            except FileExistsError:
                if target.is_symlink() or not target.is_dir():
                    raise PublicationError(
                        f"late operator directory conflicts with publication output: {relative}"
                    )
            continue
        if not target.exists():
            if expected_identity == identity:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            _copy_file_no_replace(stage / relative, target)
            continue
        if target.is_symlink() or not target.is_file():
            raise PublicationError(
                f"late operator file conflicts with publication output: {relative}"
            )
        target_identity = "file:" + hashlib.sha256(target.read_bytes()).hexdigest()
        if target_identity == identity:
            continue
        if target_identity == expected_identity:
            _copy_file_replace(stage / relative, target)
            continue
        if identity == expected_identity:
            continue
        raise PublicationError(
            f"operator file changed on both sides of publication exchange: {relative}"
        )


def recover_publication(output: Path) -> None:
    """Resolve an interrupted install without accepting a mixed document set."""
    journal_path = _journal_path(output)
    if not journal_path.exists():
        return
    if journal_path.is_symlink() or not journal_path.is_file():
        raise PublicationError(f"invalid publication journal: {journal_path}")
    try:
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        stage_name = journal["stage_name"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise PublicationError(f"invalid publication journal: {journal_path}") from exc
    if (
        journal.get("version") not in {1, 2, 3}
        or journal.get("target_name") != output.name
        or not isinstance(stage_name, str)
        or Path(stage_name).name != stage_name
        or not stage_name.startswith(f".{output.name}.loreloop-stage-")
    ):
        raise PublicationError(f"publication journal does not belong to {output}")
    stage = output.parent / stage_name
    if journal["version"] == 3 and journal.get("state") == "staging":
        _remove_tree(stage)
        journal_path.unlink()
        _fsync_directory(output.parent)
        return
    try:
        expected_new = journal["new_digest"]
        expected_old = journal["old_digest"]
    except KeyError as exc:
        raise PublicationError(f"invalid publication journal: {journal_path}") from exc
    if journal["version"] in {2, 3}:
        expected_tree = _validated_manifest(journal.get("new_tree"))
        managed_filenames = _managed_names(journal.get("managed_filenames"))
        if _manifest_digest(expected_tree) != expected_new:
            raise PublicationError("publication journal tree digest does not match its manifest")
        if _tree_has_expected_managed(output, expected_tree, managed_filenames):
            _preserve_operator_files(stage, output, managed_filenames, expected_tree)
            _sync_tree(output)
            _remove_tree(stage)
        elif (
            _tree_digest(stage if stage.exists() else None) == expected_new
            and (
                expected_old is None
                or _tree_digest(output if output.exists() else None) == expected_old
            )
        ):
            _remove_tree(stage)
        else:
            raise PublicationError("publication recovery found an unrecognized target/stage state")
        journal_path.unlink()
        _fsync_directory(output.parent)
        return
    output_digest = _tree_digest(output if output.exists() else None)
    stage_digest = _tree_digest(stage if stage.exists() else None)
    installed = output_digest == expected_new and stage_digest == expected_old
    first_install = output_digest == expected_new and expected_old is None and stage_digest is None
    not_installed = output_digest == expected_old and stage_digest == expected_new
    staged_first = output_digest is None and expected_old is None and stage_digest == expected_new
    if installed:
        _remove_tree(stage)
    elif first_install:
        pass
    elif not_installed or staged_first:
        _remove_tree(stage)
    else:
        raise PublicationError("publication recovery found an unrecognized target/stage state")
    journal_path.unlink()
    _fsync_directory(output.parent)


def _copy_existing(output: Path, stage: Path) -> None:
    if not output.exists():
        return
    _ = _tree_digest(output)
    for source in sorted(output.rglob("*"), key=lambda item: item.relative_to(output).as_posix()):
        relative = source.relative_to(output)
        target = stage / relative
        if source.is_dir():
            target.mkdir(exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            os.chmod(target, stat.S_IMODE(source.stat().st_mode) & 0o777)


def _sync_tree(root: Path) -> None:
    directories = [root]
    for path in sorted(root.rglob("*")):
        if path.is_dir():
            directories.append(path)
            continue
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    for directory in reversed(directories):
        _fsync_directory(directory)


def _renameat2(left: Path, right: Path, flags: int) -> int | None:
    library = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(library, "renameat2", None)
    if renameat2 is None:
        return None
    renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    renameat2.restype = ctypes.c_int
    result = renameat2(-100, os.fsencode(left), -100, os.fsencode(right), flags)
    if result == 0:
        return 0
    error = ctypes.get_errno()
    if error in {errno.ENOSYS, errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP}:
        return None
    return error


def _exchange(left: Path, right: Path) -> bool:
    error = _renameat2(left, right, 2)
    if error == 0:
        return True
    if error is None:
        return False
    raise PublicationError(f"cannot atomically exchange export directories: errno {error}")


def _install_no_replace(stage: Path, output: Path) -> None:
    error = _renameat2(stage, output, 1)
    if error == 0:
        return
    if error in {errno.EEXIST, errno.ENOTEMPTY}:
        raise PublicationError(f"output directory appeared while export was running: {output}")
    if error is None:
        raise PublicationError("atomic no-replace directory installation is unavailable")
    raise PublicationError(f"cannot install export directory without replacement: errno {error}")


def publish_tree(
    output: Path,
    files: Iterable[tuple[str, str]],
    *,
    managed_filenames: Iterable[str] = (),
    expected_output_exists: bool | None = None,
) -> None:
    """Install a complete tree; on Linux, updates switch with one directory exchange."""
    output = output.absolute()
    items = tuple(files)
    published_filenames = tuple(filename for filename, _ in items)
    managed = frozenset((*managed_filenames, *published_filenames))
    output.parent.mkdir(parents=True, exist_ok=True)
    recover_publication(output)
    if expected_output_exists is None:
        expected_output_exists = output.exists()
    if expected_output_exists and not output.exists():
        raise PublicationError(f"output directory disappeared while export was running: {output}")
    stage = output.parent / f".{output.name}.loreloop-stage-{uuid.uuid4().hex}"
    journal = {
        "version": 3,
        "target_name": output.name,
        "stage_name": stage.name,
        "old_digest": None,
        "managed_filenames": sorted(managed),
        "state": "staging",
    }
    _atomic_json(_journal_path(output), journal)
    try:
        stage.mkdir(mode=0o700)
        if expected_output_exists:
            _copy_existing(output, stage)
        for filename in managed:
            if Path(filename).name != filename:
                raise PublicationError(f"invalid managed filename: {filename}")
            stale = stage / filename
            if stale.exists() or stale.is_symlink():
                if stale.is_symlink() or not stale.is_file():
                    raise PublicationError(f"managed output is not a regular file: {filename}")
                stale.unlink()
        for filename, content in items:
            if Path(filename).name != filename:
                raise PublicationError(f"invalid publication filename: {filename}")
            target = stage / filename
            target.write_text(content, encoding="utf-8")
            os.chmod(target, 0o644)
        _sync_tree(stage)
        new_tree = _tree_manifest(stage)
        new_digest = _manifest_digest(new_tree)
        old_digest = (
            _tree_digest(output if output.exists() else None)
            if expected_output_exists
            else None
        )
        if expected_output_exists and old_digest is None:
            raise PublicationError(
                f"output directory disappeared while export was running: {output}"
            )
        journal.update(
            {
                "old_digest": old_digest,
                "new_digest": new_digest,
                "new_tree": new_tree,
                "state": "install_intent",
            }
        )
        _atomic_json(_journal_path(output), journal)
        if old_digest is not None:
            if not _exchange(stage, output):
                raise PublicationError(
                    "atomic directory exchange is unavailable on this filesystem"
                )
        else:
            try:
                _install_no_replace(stage, output)
            except PublicationError:
                recover_publication(output)
                raise
        _fsync_directory(output.parent)
        journal["state"] = "installed"
        _atomic_json(_journal_path(output), journal)
        _preserve_operator_files(stage, output, managed, new_tree)
        _sync_tree(output)
        _remove_tree(stage)
        _journal_path(output).unlink()
        _fsync_directory(output.parent)
    except BaseException:
        journal_path = _journal_path(output)
        if not journal_path.exists():
            _remove_tree(stage)
        else:
            try:
                pending = json.loads(journal_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pending = None
            if isinstance(pending, dict) and pending.get("state") == "staging":
                _remove_tree(stage)
                journal_path.unlink()
                _fsync_directory(output.parent)
        raise
