"""Deterministic, atomically published ZIP transport for authoritative exports."""

from __future__ import annotations

import os
import stat
import tempfile
import zipfile
from collections.abc import Iterable
from pathlib import Path

from .authoritative_documents import SourceDocument

MAX_ARCHIVE_FILES = 16
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024


class ExportArchiveError(ValueError):
    """An authoritative export archive cannot be written or read safely."""


def is_archive_output(path: Path) -> bool:
    return path.suffix.lower() == ".zip"


def ensure_archive_output_ready(output: Path, *, force: bool) -> None:
    if output.is_symlink():
        raise ExportArchiveError(f"output archive must not be a symlink: {output}")
    if output.exists():
        if not output.is_file():
            raise ExportArchiveError(f"output archive path is not a regular file: {output}")
        if not force:
            raise ExportArchiveError(f"output archive already exists: {output}")


def _safe_filename(filename: str) -> None:
    if (
        not filename
        or Path(filename).name != filename
        or filename in {".", ".."}
        or "\\" in filename
        or "\x00" in filename
    ):
        raise ExportArchiveError(f"invalid archive filename: {filename!r}")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_export_archive(
    output: Path,
    documents: Iterable[SourceDocument],
    *,
    replace: bool,
) -> None:
    """Write one reproducible archive and atomically replace its destination."""
    output = output.absolute()
    output.parent.mkdir(parents=True, exist_ok=True)
    items = tuple(sorted(documents, key=lambda document: document.filename))
    filenames = tuple(document.filename for document in items)
    if len(filenames) != len(set(filenames)):
        raise ExportArchiveError("archive contains duplicate filenames")
    if not 1 <= len(items) <= MAX_ARCHIVE_FILES:
        raise ExportArchiveError("archive file count is outside the supported range")
    for filename in filenames:
        _safe_filename(filename)
    descriptor, raw_temporary = tempfile.mkstemp(
        prefix=f".{output.name}.loreloop-stage-", suffix=".zip", dir=output.parent
    )
    temporary = Path(raw_temporary)
    os.close(descriptor)
    try:
        with zipfile.ZipFile(
            temporary,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
            allowZip64=True,
        ) as archive:
            for document in items:
                info = zipfile.ZipInfo(document.filename, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                archive.writestr(info, document.content.encode("utf-8"), compresslevel=9)
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        if replace:
            os.replace(temporary, output)
        else:
            try:
                os.link(temporary, output, follow_symlinks=False)
            except FileExistsError as exc:
                raise ExportArchiveError(
                    f"output archive appeared while export was running: {output}"
                ) from exc
            temporary.unlink()
        _fsync_directory(output.parent)
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise ExportArchiveError(f"cannot publish export archive: {exc}") from exc
    finally:
        temporary.unlink(missing_ok=True)


def read_export_archive(path: Path) -> dict[str, bytes]:
    """Read a bounded flat ZIP without accepting links, paths, or duplicate entries."""
    if path.is_symlink():
        raise ExportArchiveError(f"export archive must not be a symlink: {path}")
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        metadata = os.fstat(descriptor)
    except OSError as exc:
        raise ExportArchiveError(f"cannot inspect export archive: {path}") from exc
    if not stat.S_ISREG(metadata.st_mode):
        os.close(descriptor)
        raise ExportArchiveError(f"export archive is not a regular file: {path}")
    files: dict[str, bytes] = {}
    try:
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            with zipfile.ZipFile(stream, mode="r", allowZip64=True) as archive:
                entries = archive.infolist()
                if not 1 <= len(entries) <= MAX_ARCHIVE_FILES:
                    raise ExportArchiveError("archive file count is outside the supported range")
                total = 0
                for info in entries:
                    _safe_filename(info.filename)
                    if info.filename in files:
                        raise ExportArchiveError(
                            f"archive contains duplicate filename: {info.filename}"
                        )
                    if info.is_dir() or info.flag_bits & 0x1:
                        raise ExportArchiveError(
                            f"archive entry is not a plain file: {info.filename}"
                        )
                    unix_mode = info.external_attr >> 16
                    file_type = stat.S_IFMT(unix_mode)
                    if file_type not in {0, stat.S_IFREG}:
                        raise ExportArchiveError(
                            f"archive entry is not a regular file: {info.filename}"
                        )
                    total += info.file_size
                    if total > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
                        raise ExportArchiveError(
                            "archive expands beyond the supported size limit"
                        )
                    data = archive.read(info)
                    if len(data) != info.file_size:
                        raise ExportArchiveError(
                            f"archive entry length mismatch: {info.filename}"
                        )
                    files[info.filename] = data
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise ExportArchiveError(f"cannot read export archive: {exc}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return files
