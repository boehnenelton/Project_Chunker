"""
File Name:       lib_bejson_Projects_zip.py
Relative Path:   lib/lib_bejson_Projects_zip.py
Family:          Projects
Description:     Zip intake for the chunker. Locks onto the zip (PID lock), confirms it is a complete, valid,
                 unencrypted zip, extracts it into a staging directory beside it with zip-slip / symlink / zip-bomb
                 protection, then re-reads every extracted file from disk and compares size and CRC32 with the
                 archive's own records (no missing, no extra files). Chunking and deletion are the caller's job
                 (lib_bejson_Projects_pipeline.projects_pipeline_run_zip); this module never deletes the zip.
VERSION:         1.8.0
Author:          Elton Boehnen · boehnenelton2024@gmail.com · boehnenelton2024.pages.dev · github.com/boehnenelton
RELATIONAL_ID:   46c8e14b-6d5b-41bb-b72c-4a3d4c6658e7
"""

import hashlib
import os
import shutil
import stat
import tempfile
import time
import zipfile
import zlib
import json
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional, Tuple

from lib_bejson_Projects_locks import ProjectsPathLock
from lib_bejson_Core_bejson_path_guard import bejson_safe_join
from lib_bejson_Projects_logging import projects_logging_get_logger
from lib_bejson_Projects_paths import projects_paths_sanitize_name

VERSION = "1.8.0"
ZIP_MAGICS = (b"PK\x03\x04", b"PK\x05\x06")       # local file header / empty archive
DEFAULT_MAX_ENTRIES = 100_000
DEFAULT_MAX_TOTAL_BYTES = 4 * 1024 ** 3             # uncompressed, declared
LOCK_TIMEOUT_SECONDS = 10
_CHUNK = 1024 * 1024
_log = projects_logging_get_logger("zip")


class ProjectsZipError(Exception):
    """Raised when a zip cannot be locked, is not a complete valid zip, is unsafe, or fails extraction checks."""


@dataclass
class StagedZip:
    zip_path: Path
    sha256: str
    size: int
    stage_parent: Path                 # temp dir created by us; always removed by projects_zip_cleanup
    stage_root: Path                   # stage_parent / <sanitized zip stem>
    project_dir: Path                  # stage_root, or its single wrapper folder
    original_dir: Path                 # where the project would live next to the zip (recorded as Original_Path)
    files: int = 0
    dirs: int = 0
    total_bytes: int = 0
    warnings: List[str] = field(default_factory=list)


class ProjectsZipLock:
    """Context manager: PID-locks the zip for the whole job (lock dir <zip>.lockdir)."""

    def __init__(self, zip_path: Path, timeout: int = LOCK_TIMEOUT_SECONDS):
        self._lock = ProjectsPathLock(zip_path, timeout)
        self._zip = zip_path

    def __enter__(self):
        if not self._lock.acquire():
            raise ProjectsZipError(f"{self._zip} is locked by another process")
        return self

    def __exit__(self, *exc):
        self._lock.release()
        return False


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def projects_zip_sha256(path: Path) -> str:
    return _sha256_file(Path(path))


# Normalised, safe relative posix name for a zip entry; raises on absolute paths, drive letters, "..", empties.
def _safe_name(raw: str) -> str:
    name = raw.replace("\\", "/")
    if name.startswith("/") or (len(name) > 1 and name[1] == ":"):
        raise ProjectsZipError(f"unsafe absolute entry name: {raw!r}")
    parts = [p for p in PurePosixPath(name).parts if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts) or "\x00" in name:
        raise ProjectsZipError(f"unsafe entry name: {raw!r}")
    return "/".join(parts)


# Confirms the file is a complete valid zip and returns its entries. Checks magic bytes, central directory,
# encryption, symlink entries, entry-count and declared-size limits and the archive-wide CRC test (reads everything).
def projects_zip_inspect(zip_path: Path, max_entries: int = DEFAULT_MAX_ENTRIES,
                         max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES) -> List[zipfile.ZipInfo]:
    zip_path = Path(zip_path)
    try:
        with open(zip_path, "rb") as handle:
            head = handle.read(4)
    except OSError as exc:
        raise ProjectsZipError(f"cannot read {zip_path}: {exc}") from exc
    if head not in ZIP_MAGICS or not zipfile.is_zipfile(zip_path):
        raise ProjectsZipError(f"not a zip file: {zip_path}")
    try:
        with zipfile.ZipFile(zip_path) as archive:
            infos = archive.infolist()
            if len(infos) > max_entries:
                raise ProjectsZipError(f"{len(infos)} entries exceeds limit {max_entries}")
            if sum(i.file_size for i in infos) > max_total_bytes:
                raise ProjectsZipError(f"declared uncompressed size exceeds limit {max_total_bytes} bytes")
            seen: Dict[str, str] = {}
            for info in infos:
                name = _safe_name(info.filename)
                if info.flag_bits & 0x1:
                    raise ProjectsZipError(f"encrypted entry not supported: {info.filename!r}")
                if stat.S_ISLNK(info.external_attr >> 16):
                    raise ProjectsZipError(f"symlink entry refused: {info.filename!r}")
                key = name.lower()
                if key in seen and seen[key] != name:
                    raise ProjectsZipError(f"entry names collide ignoring case: {name!r} / {seen[key]!r}")
                if key in seen and not info.is_dir():
                    raise ProjectsZipError(f"duplicate entry: {name!r}")
                seen[key] = name
            bad = archive.testzip()
            if bad is not None:
                raise ProjectsZipError(f"corrupt or truncated archive: CRC failure in {bad!r}")
            return infos
    except zipfile.BadZipFile as exc:
        raise ProjectsZipError(f"invalid zip: {exc}") from exc


# Streams every entry into stage_root, counting actual bytes against the declared size (a lying header cannot
# overrun the limit). Returns (files, dirs, total_bytes).
# Archive member time (zip stores naive local time, 2 s resolution) -> epoch seconds; None when not representable.
def _member_epoch(info: zipfile.ZipInfo) -> Optional[float]:
    try:
        return time.mktime(tuple(info.date_time) + (0, 0, -1))
    except (OverflowError, ValueError):
        return None


def _extract(zip_path: Path, infos: List[zipfile.ZipInfo], stage_root: Path):
    files = dirs = total = 0
    dir_times = []
    with zipfile.ZipFile(zip_path) as archive:
        for info in infos:
            name = _safe_name(info.filename)
            target = Path(bejson_safe_join(str(stage_root), name))
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                dirs += 1
                dir_times.append((target, _member_epoch(info)))
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            written = 0
            with archive.open(info) as src, open(target, "xb") as dst:
                for block in iter(lambda: src.read(_CHUNK), b""):
                    written += len(block)
                    if written > info.file_size:
                        raise ProjectsZipError(f"entry {name!r} is larger than its declared size")
                    dst.write(block)
            epoch = _member_epoch(info)
            if epoch is not None:
                os.utime(target, (epoch, epoch))
            files += 1
            total += written
    # Directory times last, deepest first, so writing children does not disturb them.
    for target, epoch in sorted(dir_times, key=lambda item: -len(item[0].parts)):
        if epoch is not None:
            os.utime(target, (epoch, epoch))
    return files, dirs, total


# Independent post-extraction check against the archive's records: every file entry exists with the declared size
# and CRC32 (re-read from disk), every directory entry exists, and nothing else is present. Returns problem list.
def projects_zip_verify_extraction(infos: List[zipfile.ZipInfo], stage_root: Path) -> List[str]:
    problems: List[str] = []
    expected_files, expected_dirs = {}, set()
    for info in infos:
        name = _safe_name(info.filename)
        if info.is_dir():
            expected_dirs.add(name)
        else:
            expected_files[name] = info
    for name, info in sorted(expected_files.items()):
        path = stage_root / name
        if path.is_symlink() or not path.is_file():
            problems.append(f"missing: {name}")
            continue
        crc, size = 0, 0
        with open(path, "rb") as handle:
            for block in iter(lambda: handle.read(_CHUNK), b""):
                crc = zlib.crc32(block, crc)
                size += len(block)
        if size != info.file_size:
            problems.append(f"size mismatch: {name} ({size} != {info.file_size})")
        elif (crc & 0xFFFFFFFF) != info.CRC:
            problems.append(f"CRC mismatch: {name}")
    for name in sorted(expected_dirs):
        if not (stage_root / name).is_dir():
            problems.append(f"missing directory: {name}")
    implied_dirs = {str(PurePosixPath(n).parent) for n in expected_files} | expected_dirs
    for path in sorted(stage_root.rglob("*")):
        rel = path.relative_to(stage_root).as_posix()
        if path.is_symlink():
            problems.append(f"unexpected symlink: {rel}")
        elif path.is_file() and rel not in expected_files:
            problems.append(f"unexpected file: {rel}")
        elif path.is_dir() and rel not in expected_dirs and not any(
                d == rel or d.startswith(rel + "/") for d in implied_dirs):
            problems.append(f"unexpected directory: {rel}")
    return problems


# Folder prefix that staging strips to find the project directory: the single top-level directory name when the
# archive has exactly one top-level entry and it is a directory, else "" (snapshot paths are relative to it).
def projects_zip_project_prefix(infos: List[zipfile.ZipInfo]) -> str:
    tops = {}
    for info in infos:
        name = _safe_name(info.filename)
        top = name.split("/", 1)[0]
        tops[top] = tops.get(top, False) or "/" in name or info.is_dir()
    if len(tops) == 1:
        top, is_dir = next(iter(tops.items()))
        return top if is_dir else ""
    return ""


# Stages a zip: inspect -> extract into a fresh temp dir beside the zip -> verify extraction. The caller must hold
# the zip lock. On any failure the staging dir is removed and ProjectsZipError is raised; the zip is never modified.
def projects_zip_stage(zip_path: Path, max_entries: int = DEFAULT_MAX_ENTRIES,
                       max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES) -> StagedZip:
    zip_path = Path(zip_path)
    if zip_path.is_symlink():
        raise ProjectsZipError(f"zip is a symlink; pass the real file: {zip_path}")
    zip_path = zip_path.resolve()
    if not zip_path.is_file():
        raise ProjectsZipError(f"not a file: {zip_path}")
    sha_before, size = _sha256_file(zip_path), zip_path.stat().st_size
    infos = projects_zip_inspect(zip_path, max_entries, max_total_bytes)
    stage_parent = Path(tempfile.mkdtemp(prefix=".chunker_stage_", dir=zip_path.parent))
    try:
        stage_root = stage_parent / projects_paths_sanitize_name(zip_path.stem)
        stage_root.mkdir()
        files, dirs, total = _extract(zip_path, infos, stage_root)
        problems = projects_zip_verify_extraction(infos, stage_root)
        if problems:
            raise ProjectsZipError(f"extraction incomplete ({len(problems)} problem(s)): " + "; ".join(problems[:5]))
        if _sha256_file(zip_path) != sha_before:
            raise ProjectsZipError("zip changed while it was being extracted")
        tops = [p for p in stage_root.iterdir()]
        wrapper = tops[0] if len(tops) == 1 and tops[0].is_dir() else None
        project_dir = wrapper if wrapper else stage_root
        staged = StagedZip(zip_path=zip_path, sha256=sha_before, size=size, stage_parent=stage_parent,
                           stage_root=stage_root, project_dir=project_dir,
                           original_dir=zip_path.parent / project_dir.name, files=files, dirs=dirs, total_bytes=total)
        if files == 0:
            raise ProjectsZipError("zip contains no files")
        _log.info("staged %s: %d file(s), %d dir(s), %d bytes", zip_path.name, files, dirs, total)
        return staged
    except BaseException:
        shutil.rmtree(stage_parent, ignore_errors=True)
        raise


# Removes the staging directory (always safe: it is a temp dir this module created).
def projects_zip_cleanup(staged: StagedZip) -> None:
    shutil.rmtree(staged.stage_parent, ignore_errors=True)


# Inspects a zip archive in memory (reads tracker manifest or derives from top folder / file name)
# to preview its identity without extracting the entire archive.
def projects_zip_preview_identity(zip_path: Path) -> Tuple[str, str, str]:
    from lib_bejson_Projects_tracker import (
        DEFAULT_PACKAGE_VERSION,
        DEFAULT_PROJECT_VERSION,
        projects_tracker_derive_from_folder,
        projects_tracker_extract_identity,
        projects_tracker_filename_identity,
    )
    zip_file_path = Path(zip_path)
    if zip_file_path.is_file():
        try:
            with zipfile.ZipFile(zip_file_path, "r") as archive_handle:
                for entry_name in archive_handle.namelist():
                    base_name = PurePosixPath(entry_name).name
                    if base_name in ("bejson_project.json", ".bejson_project.json"):
                        try:
                            manifest_bytes = archive_handle.read(entry_name)
                            manifest_doc = json.loads(manifest_bytes.decode("utf-8"))
                            if isinstance(manifest_doc, dict):
                                extracted_identity = projects_tracker_extract_identity(manifest_doc)
                                if extracted_identity.get("Project_Name"):
                                    return (
                                        extracted_identity["Project_Name"],
                                        extracted_identity.get("Project_Version") or DEFAULT_PROJECT_VERSION,
                                        extracted_identity.get("Package_Version") or DEFAULT_PACKAGE_VERSION,
                                    )
                        except Exception as manifest_read_error:
                            _log.debug("could not parse internal manifest in %s: %s", zip_file_path.name, manifest_read_error)
                infos = archive_handle.infolist()
                single_prefix = projects_zip_project_prefix(infos)
                if single_prefix:
                    return projects_tracker_derive_from_folder(single_prefix)
        except Exception as archive_inspect_error:
            _log.debug("in-memory inspection failed for %s: %s", zip_file_path.name, archive_inspect_error)

    filename_identity = projects_tracker_filename_identity(zip_file_path.name)
    if filename_identity:
        return filename_identity
    return projects_tracker_derive_from_folder(zip_file_path.stem)

