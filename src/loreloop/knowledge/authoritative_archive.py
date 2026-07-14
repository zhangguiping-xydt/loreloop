"""Deterministic, atomically published ZIP transport for authoritative exports."""

from __future__ import annotations

import io
import os
import stat
import struct
import tempfile
import zipfile
from collections.abc import Iterable
from pathlib import Path

from . import authoritative_capsule_io as capsule_io
from .authoritative_capsule import CAPSULE_FILENAME
from .authoritative_documents import SourceDocument

MAX_ARCHIVE_FILES = 16
MAX_ARCHIVE_COMPRESSED_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_MEMBER_COMPRESSED_BYTES = 128 * 1024 * 1024
MAX_ARCHIVE_COMPRESSION_RATIO = 200
MAX_ARCHIVE_CENTRAL_DIRECTORY_BYTES = 64 * 1024
_EOCD_SIGNATURE = b"PK\x05\x06"
_EOCD_SIZE = 22
_MAX_ZIP_COMMENT_BYTES = 65_535


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


def _managed_limit(filename: str) -> int:
    return (
        capsule_io.MAX_CAPSULE_BYTES
        if filename == CAPSULE_FILENAME
        else capsule_io.MAX_DOCUMENT_BYTES
    )


def _validate_member(info: zipfile.ZipInfo) -> None:
    _safe_filename(info.filename)
    if info.is_dir() or info.flag_bits & 0x1:
        raise ExportArchiveError(f"archive entry is not a plain file: {info.filename}")
    unix_mode = info.external_attr >> 16
    file_type = stat.S_IFMT(unix_mode)
    if file_type not in {0, stat.S_IFREG}:
        raise ExportArchiveError(f"archive entry is not a regular file: {info.filename}")
    if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
        raise ExportArchiveError(f"archive entry uses unsupported compression: {info.filename}")
    if info.compress_size > MAX_ARCHIVE_MEMBER_COMPRESSED_BYTES:
        raise ExportArchiveError(
            f"archive member exceeds its compressed size limit: {info.filename}"
        )
    if info.file_size and (
        info.compress_size == 0
        or info.file_size > info.compress_size * MAX_ARCHIVE_COMPRESSION_RATIO
    ):
        raise ExportArchiveError(
            f"archive member exceeds the compression ratio limit: {info.filename}"
        )


def _read_member(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    *,
    limit: int,
) -> bytes:
    if info.file_size > limit:
        raise ExportArchiveError(f"archive member exceeds its expanded size limit: {info.filename}")
    with archive.open(info, mode="r") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ExportArchiveError(f"archive member exceeds its expanded size limit: {info.filename}")
    if len(data) != info.file_size:
        raise ExportArchiveError(f"archive entry length mismatch: {info.filename}")
    return data


def _validate_central_directory(snapshot: bytes) -> None:
    """Bound entry count/metadata before ZipFile allocates its central-directory list."""
    archive_size = len(snapshot)
    tail_size = min(archive_size, _EOCD_SIZE + _MAX_ZIP_COMMENT_BYTES)
    tail = snapshot[-tail_size:]
    offset = tail.rfind(_EOCD_SIGNATURE)
    if offset < 0 or len(tail) - offset < _EOCD_SIZE:
        raise ExportArchiveError("archive lacks a bounded end-of-central-directory record")
    (
        _,
        disk_number,
        directory_disk,
        disk_entries,
        total_entries,
        directory_size,
        directory_offset,
        comment_size,
    ) = struct.unpack_from("<4s4H2LH", tail, offset)
    eocd_absolute = archive_size - tail_size + offset
    if (
        disk_number != 0
        or directory_disk != 0
        or disk_entries != total_entries
        or total_entries == 0xFFFF
        or directory_size == 0xFFFFFFFF
        or directory_offset == 0xFFFFFFFF
        or comment_size != 0
        or eocd_absolute + _EOCD_SIZE != archive_size
        or directory_offset + directory_size != eocd_absolute
    ):
        raise ExportArchiveError("archive central directory is unsupported or inconsistent")
    if not 1 <= total_entries <= MAX_ARCHIVE_FILES:
        raise ExportArchiveError("archive file count is outside the supported range")
    if directory_size > MAX_ARCHIVE_CENTRAL_DIRECTORY_BYTES:
        raise ExportArchiveError("archive central directory exceeds its size limit")


def write_export_archive(
    output: Path,
    documents: Iterable[SourceDocument],
    *,
    replace: bool,
) -> None:
    """Write one reproducible archive and atomically replace its destination."""
    output = output.absolute()
    output.parent.mkdir(parents=True, exist_ok=True)
    items = tuple(
        sorted(
            ((document.filename, document.content.encode("utf-8")) for document in documents),
            key=lambda item: item[0],
        )
    )
    filenames = tuple(filename for filename, _ in items)
    if len(filenames) != len(set(filenames)):
        raise ExportArchiveError("archive contains duplicate filenames")
    if not 1 <= len(items) <= MAX_ARCHIVE_FILES:
        raise ExportArchiveError("archive file count is outside the supported range")
    total = 0
    for filename, content in items:
        _safe_filename(filename)
        if len(content) > _managed_limit(filename):
            raise ExportArchiveError(f"archive member exceeds its expanded size limit: {filename}")
        total += len(content)
    if total > capsule_io.MAX_MANAGED_TOTAL_BYTES:
        raise ExportArchiveError("archive expands beyond the managed total size limit")
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
            for filename, content in items:
                info = zipfile.ZipInfo(filename, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                archive.writestr(info, content, compresslevel=9)
        if temporary.stat().st_size > MAX_ARCHIVE_COMPRESSED_BYTES:
            raise ExportArchiveError("archive exceeds the compressed size limit")
        with zipfile.ZipFile(temporary, mode="r", allowZip64=True) as produced:
            for info in produced.infolist():
                _validate_member(info)
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
    """Read a bounded flat ZIP, loading Capsule first and only its exact managed set."""
    if path.is_symlink():
        raise ExportArchiveError(f"export archive must not be a symlink: {path}")
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
        )
        metadata = os.fstat(descriptor)
    except OSError as exc:
        raise ExportArchiveError(f"cannot inspect export archive: {path}") from exc
    if not stat.S_ISREG(metadata.st_mode):
        os.close(descriptor)
        raise ExportArchiveError(f"export archive is not a regular file: {path}")
    if metadata.st_size > MAX_ARCHIVE_COMPRESSED_BYTES:
        os.close(descriptor)
        raise ExportArchiveError("archive exceeds the compressed size limit")
    try:
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            snapshot = stream.read(MAX_ARCHIVE_COMPRESSED_BYTES + 1)
        if len(snapshot) > MAX_ARCHIVE_COMPRESSED_BYTES:
            raise ExportArchiveError("archive exceeds the compressed size limit")
        _validate_central_directory(snapshot)
        with zipfile.ZipFile(io.BytesIO(snapshot), mode="r", allowZip64=True) as archive:
            entries = archive.infolist()
            if not 1 <= len(entries) <= MAX_ARCHIVE_FILES:
                raise ExportArchiveError("archive file count is outside the supported range")
            by_name: dict[str, zipfile.ZipInfo] = {}
            for info in entries:
                _validate_member(info)
                if info.filename in by_name:
                    raise ExportArchiveError(
                        f"archive contains duplicate filename: {info.filename}"
                    )
                by_name[info.filename] = info
            capsule_info = by_name.get(CAPSULE_FILENAME)
            if capsule_info is None:
                raise ExportArchiveError(f"archive is missing {CAPSULE_FILENAME}")
            capsule = _read_member(
                archive,
                capsule_info,
                limit=capsule_io.MAX_CAPSULE_BYTES,
            )
            filenames = capsule_io.managed_document_filenames(capsule)
            expected = {CAPSULE_FILENAME, *filenames}
            missing = expected - set(by_name)
            extras = set(by_name) - expected
            if missing or extras:
                raise ExportArchiveError(
                    f"archive file set mismatch; missing={sorted(missing)}, extra={sorted(extras)}"
                )
            declared_total = capsule_info.file_size + sum(
                by_name[filename].file_size for filename in filenames
            )
            if declared_total > capsule_io.MAX_MANAGED_TOTAL_BYTES:
                raise ExportArchiveError("archive expands beyond the managed total size limit")
            files = {CAPSULE_FILENAME: capsule}
            total = len(capsule)
            for filename in filenames:
                data = _read_member(
                    archive,
                    by_name[filename],
                    limit=capsule_io.MAX_DOCUMENT_BYTES,
                )
                total += len(data)
                if total > capsule_io.MAX_MANAGED_TOTAL_BYTES:
                    raise ExportArchiveError(
                        "archive expands beyond the managed total size limit"
                    )
                files[filename] = data
            return files
    except ExportArchiveError:
        raise
    except capsule_io.CapsuleIoError as exc:
        raise ExportArchiveError(f"archive Capsule is invalid: {exc}") from exc
    except MemoryError as exc:
        raise ExportArchiveError("archive exceeds the available reader memory") from exc
    except (OSError, zipfile.BadZipFile, RuntimeError, EOFError, ValueError) as exc:
        raise ExportArchiveError(f"cannot read export archive: {exc}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
