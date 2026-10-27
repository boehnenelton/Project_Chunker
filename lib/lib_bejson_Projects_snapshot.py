"""
File Name:       lib_bejson_Projects_snapshot.py
Relative Path:   lib/lib_bejson_Projects_snapshot.py
Family:          Projects
Description:     Directory -> snapshot chunk. Walks a project deterministically and stores one row per file:
                 text in File_Content, binary (images etc.) base64-encoded in the Base64 field, a Flags array
                 (JSON array text, e.g. ["Is_Binary"]; BEJSON 104a has no array column type and 104 has no
                 custom headers, so the array is stored as JSON text and parsed on read) and a real SHA-256. Binary storage is switchable (Binary_Enabled);
                 when off, binary files are ignored and reported. Writes
                 storage/project_snapshots/<project>/data/<project>-V<ver>-PKG<pkg>.bejson keyed to the registry UUID;
                 chunks made without tracking go to storage/untracked_chunks/<project>-V<ver>-PKG<pkg>.bejson.
                 Schema 2.1 adds Modified_Time (ISO-8601 UTC, microseconds) on every row and an Is_Empty_Dir
                 boolean: genuinely empty directories are captured as rows (no content, no hash).
                 Legacy snapshots (Is_Binary boolean, base64 in File_Content; 2.0 without the 2.1 fields) remain readable.
VERSION:         1.8.0
Author:          Elton Boehnen · boehnenelton2024@gmail.com · boehnenelton2024.pages.dev · github.com/boehnenelton
RELATIONAL_ID:   19d2b773-34da-465b-9b72-b4168e56fb67
"""

import base64
import hashlib
import json
import os
import shutil
import time
from datetime import datetime, timezone
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

from lib_bejson_Core_bejson_chunking import bejson_core_chunking_hash_file_bytes
from lib_bejson_Core_bejson_core import (
    bejson_core_atomic_write,
    bejson_core_get_field_map,
    bejson_core_load_file,
)
from lib_bejson_Core_bejson_validator import validate_bejson
from lib_bejson_Projects_filters import projects_filters_match
from lib_bejson_Projects_logging import projects_logging_get_logger
from lib_bejson_Projects_paths import StorageLayout, projects_paths_sanitize_name
from lib_bejson_Projects_tracker import TrackerInfo

VERSION = "1.8.0"
SNAPSHOT_RECORDS_TYPE = "SnapshotEntity"
SNAPSHOT_SCHEMA_VERSION = "2.1"
FLAG_IS_BINARY = "Is_Binary"
DEFAULT_EXCLUDE_DIRS = (".git", "__pycache__", "node_modules", ".mfdb_lock")
DEFAULT_MAX_FILE_BYTES = 25 * 1024 * 1024
SNAPSHOT_FIELDS = [
    {"name": "File_Name", "type": "string"},
    {"name": "File_Extension", "type": "string"},
    {"name": "Relative_Path", "type": "string"},
    {"name": "File_Content", "type": "string"},     # UTF-8 text; "" for flagged-binary rows
    {"name": "Base64", "type": "string"},           # base64 of the raw bytes for Is_Binary rows; "" otherwise
    {"name": "File_Hash", "type": "string"},        # SHA-256 of the raw bytes
    {"name": "Flags", "type": "string"},            # JSON array text, e.g. ["Is_Binary"] (104a forbids array fields)
    {"name": "Is_Mounted", "type": "boolean"},
    {"name": "Modified_Time", "type": "string"},    # ISO-8601 UTC with microseconds, e.g. 2020-09-13T12:26:40.123456+00:00
    {"name": "Is_Empty_Dir", "type": "boolean"},    # true: row is a genuinely empty directory (no content / hash)
]

_log = projects_logging_get_logger("snapshot")


class ProjectsSnapshotError(Exception):
    """Raised when a snapshot cannot be built, read or written."""


@dataclass
class SnapshotStats:
    files: int = 0
    empty_dirs: int = 0
    excluded_dirs: int = 0                                     # directories pruned by name/glob filters
    excluded_files: int = 0                                    # files dropped by file-name filters
    binary_files: int = 0
    total_bytes: int = 0
    skipped: List[str] = field(default_factory=list)          # "<relpath>: <reason>"
    ignored_binary: List[str] = field(default_factory=list)   # binary files left out because Binary_Enabled is off


@dataclass
class SnapshotRow:
    relative_path: str
    raw: bytes                  # decoded file bytes (None-safe: empty file -> b"")
    expected_hash: str
    is_binary: bool
    flags: List[str]
    modified: str = ""          # ISO-8601 UTC, "" when the snapshot predates schema 2.1
    is_dir: bool = False        # captured empty directory (raw == b"", expected_hash == "")


# mtime (epoch seconds) <-> stored text. Parse returns None for "" or anything unparseable.
def projects_snapshot_format_time(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="microseconds")


def projects_snapshot_parse_time(value: Any) -> Optional[float]:
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


# Flags <-> storage form. Stored as JSON array text; a native list (future 104/MFDB layout) is accepted too, and a
# plain comma-separated string is tolerated.
def projects_snapshot_encode_flags(flags: Iterable[str]) -> str:
    return json.dumps(sorted(set(flags)))


def projects_snapshot_decode_flags(value: Any) -> List[str]:
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return [str(f) for f in value]
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return [str(f) for f in parsed]
    except (TypeError, ValueError):
        pass
    return [part.strip() for part in str(value).split(",") if part.strip()]


# Decodes every row of a snapshot document, new or legacy schema. Yields (SnapshotRow, None) or
# (partial SnapshotRow with raw=b"", error text) when a row's content cannot be decoded. Field access goes through
# the field-map cache.
def projects_snapshot_iter_rows(doc: Dict[str, Any]) -> Iterator[Tuple[SnapshotRow, Optional[str]]]:
    fm = bejson_core_get_field_map(doc)
    required = ("Relative_Path", "File_Content", "File_Hash")
    missing = [n for n in required if n not in fm]
    if missing or ("Flags" not in fm and "Is_Binary" not in fm):
        raise ProjectsSnapshotError(f"snapshot lacks required fields {missing or ['Flags or Is_Binary']}")
    for row in doc.get("Values", []):
        if "Flags" in fm:
            flags = projects_snapshot_decode_flags(row[fm["Flags"]])
            is_binary = FLAG_IS_BINARY in flags
            encoded = row[fm["Base64"]] if "Base64" in fm else ""
        else:                                                   # legacy: Is_Binary bool, base64 in File_Content
            is_binary = bool(row[fm["Is_Binary"]])
            flags = [FLAG_IS_BINARY] if is_binary else []
            encoded = row[fm["File_Content"]]
        rel, expected = row[fm["Relative_Path"]], row[fm["File_Hash"]]
        modified = (row[fm["Modified_Time"]] or "").strip() if "Modified_Time" in fm else ""
        if "Is_Empty_Dir" in fm and row[fm["Is_Empty_Dir"]] is True:
            yield SnapshotRow(rel, b"", "", False, flags, modified, True), None
            continue
        try:
            raw = (base64.b64decode(encoded, validate=True) if is_binary
                   else (row[fm["File_Content"]] or "").encode("utf-8"))
        except (ValueError, TypeError) as exc:                  # binascii.Error is a ValueError
            yield SnapshotRow(rel, b"", expected, is_binary, flags, modified), f"content does not decode: {exc}"
            continue
        yield SnapshotRow(rel, raw, expected, is_binary, flags, modified), None


# Walks target_dir in sorted order and returns (rows, stats). A directory is captured (Is_Empty_Dir) only when its raw
# listing is empty - no files, no subdirectories, no symlinks - it is not the root and not inside an excluded or
# skipped tree; a directory merely emptied by the extension/size/binary filters is NOT empty and is not captured.
# Every file row carries its Modified_Time. Excluded: directory names in exclude_dirs, any
# directory in skip_dirs (e.g. the chunker's own storage root), symlinks, files over max_file_bytes (0 = no
# limit) and, when extensions is given, files whose suffix is not listed. A file that is not valid UTF-8 is binary:
# stored base64 in the Base64 field with the Is_Binary flag when binary_enabled, otherwise ignored (and listed in
# stats.ignored_binary). Nothing is skipped silently.
def projects_snapshot_build_rows(target_dir: Path,
                                 exclude_dirs: Iterable[str] = DEFAULT_EXCLUDE_DIRS,
                                 extensions: Optional[Iterable[str]] = None,
                                 skip_dirs: Iterable[Path] = (),
                                 max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
                                 binary_enabled: bool = True,
                                 exclude_files: Iterable[str] = ()):
    target_dir = Path(target_dir).resolve()
    excluded_names = tuple(exclude_dirs)                     # names or fnmatch globs
    excluded_files = tuple(exclude_files)                    # file-name globs (a pattern with "/" matches the path)
    skipped_paths = {Path(p).resolve() for p in skip_dirs}
    wanted_ext = {e.lower() if e.startswith(".") else f".{e.lower()}" for e in extensions} if extensions else None
    rows: List[List[Any]] = []
    stats = SnapshotStats()

    for root, dirs, files in os.walk(target_dir, followlinks=False):
        root_path = Path(root)
        if not dirs and not files and root_path != target_dir:
            try:
                rel_dir = root_path.relative_to(target_dir).as_posix()
                rows.append([root_path.name, "", rel_dir, "", "", "", projects_snapshot_encode_flags([]), False,
                             projects_snapshot_format_time(root_path.stat().st_mtime), True])
                stats.empty_dirs += 1
            except OSError as exc:
                stats.skipped.append(f"{root_path.relative_to(target_dir).as_posix()}: {exc}")
            continue
        kept = []
        for d in dirs:
            child = root_path / d
            if projects_filters_match(d, child.relative_to(target_dir).as_posix(), excluded_names):
                stats.excluded_dirs += 1
            elif child.resolve() not in skipped_paths and not child.is_symlink():
                kept.append(d)
        dirs[:] = sorted(kept)
        for name in sorted(files):
            path = root_path / name
            rel = path.relative_to(target_dir).as_posix()
            if excluded_files and projects_filters_match(name, rel, excluded_files):
                stats.excluded_files += 1
                continue
            if path.is_symlink():
                stats.skipped.append(f"{rel}: symlink")
                continue
            if wanted_ext is not None and path.suffix.lower() not in wanted_ext:
                continue
            try:
                st = path.stat()
                size = st.st_size
                if max_file_bytes and size > max_file_bytes:
                    stats.skipped.append(f"{rel}: {size} bytes exceeds limit {max_file_bytes}")
                    continue
                raw = path.read_bytes()
            except OSError as exc:
                stats.skipped.append(f"{rel}: {exc}")
                continue
            try:
                text, is_binary = raw.decode("utf-8"), False
            except UnicodeDecodeError:
                text, is_binary = "", True
            if is_binary and not binary_enabled:
                stats.ignored_binary.append(rel)
                continue
            rows.append([name, path.suffix, rel,
                         text,
                         base64.b64encode(raw).decode("ascii") if is_binary else "",
                         bejson_core_chunking_hash_file_bytes(raw),
                         projects_snapshot_encode_flags([FLAG_IS_BINARY] if is_binary else []),
                         False,
                         projects_snapshot_format_time(st.st_mtime),
                         False])
            stats.files += 1
            stats.binary_files += int(is_binary)
            stats.total_bytes += len(raw)

    rows.sort(key=lambda r: r[2])
    for line in stats.skipped:
        _log.warning("skipped %s", line)
    if stats.excluded_dirs or stats.excluded_files:
        _log.info("filters excluded %d director(ies) and %d file(s)", stats.excluded_dirs, stats.excluded_files)
    if stats.ignored_binary:
        _log.info("Binary_Enabled is off: ignored %d binary file(s)", len(stats.ignored_binary))
    return rows, stats


# Assembles the snapshot BEJSON 104a document (custom headers carry the registry/tracker linkage and the effective
# Binary_Enabled setting).
def projects_snapshot_build_document(info: TrackerInfo, rows: List[List[Any]], tracker_ref: str = "",
                                     original_path: str = "", binary_enabled: bool = True,
                                     extra_headers: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    doc = {
        "Format": "BEJSON",
        "Format_Version": "104a",
        "Format_Creator": "Elton Boehnen",
        "Snapshot_Schema": SNAPSHOT_SCHEMA_VERSION,
        "Project_Name": info.project_name,
        "Project_Version": info.project_version,
        "Package_Version": info.package_version,
        "Versioned_UUID": info.versioned_uuid,
        "Tracker_File": tracker_ref,
        "Original_Path": original_path,
        "Binary_Enabled": bool(binary_enabled),
        # The tracker travels inside its chunk (as a JSON string) so it can be edited and redeployed later.
        "Tracker_Data": json.dumps(info.tracker_data, ensure_ascii=False),
        "Snapshot_Date": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "Records_Type": [SNAPSHOT_RECORDS_TYPE],
        "Fields": [dict(f) for f in SNAPSHOT_FIELDS],
        "Values": rows,
    }
    for key, value in (extra_headers or {}).items():       # e.g. Source_Zip provenance; never overrides core headers
        doc.setdefault(key, value)
    return doc


# Validates and atomically writes the snapshot. Re-chunking the same version replaces that version's file.
# untracked=True stores it in layout.untracked_dir (chunk made without tracking) instead of the tracked folder.
def projects_snapshot_write(layout: StorageLayout, info: TrackerInfo, rows: List[List[Any]],
                            tracker_ref: str = "", original_path: str = "", binary_enabled: bool = True,
                            extra_headers: Optional[Dict[str, Any]] = None, untracked: bool = False) -> Path:
    doc = projects_snapshot_build_document(info, rows, tracker_ref, original_path, binary_enabled, extra_headers)
    result = validate_bejson(doc)
    if not result.valid:
        raise ProjectsSnapshotError(f"snapshot failed BEJSON validation: {'; '.join(result.errors)}")
    out_path = layout.snapshot_file(info.project_name, info.project_version, info.package_version, untracked=untracked)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not bejson_core_atomic_write(str(out_path), doc):
        raise ProjectsSnapshotError(f"atomic write failed for {out_path}")
    return out_path


# Re-chunking a version that an older package stored under the pre-1.7.1 name (V<ver>-PKG<pkg>.bejson) replaces that
# file: once the new, project-prefixed file is written the old one is removed, but only when its header proves it is
# the same version (same Versioned_UUID). Returns the removed path, or None when there was nothing to remove.
def projects_snapshot_remove_legacy(layout: StorageLayout, info: TrackerInfo) -> Optional[Path]:
    legacy = layout.legacy_snapshot_file(info.project_name, info.project_version, info.package_version)
    current = layout.snapshot_file(info.project_name, info.project_version, info.package_version)
    if legacy == current or not legacy.is_file() or not current.is_file():
        return None
    try:
        header = json.loads(legacy.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(header, dict) or header.get("Versioned_UUID") != info.versioned_uuid:
        return None
    try:
        legacy.unlink()
    except OSError as exc:
        _log.warning("could not remove superseded chunk %s: %s", legacy, exc)
        return None
    _log.info("replaced old-style chunk %s with %s", legacy.name, current.name)
    return legacy


# Content fingerprint of a snapshot (or of freshly built rows wrapped as {"Fields": SNAPSHOT_FIELDS, "Values": rows}):
# SHA-256 over every entry's relative path and stored file hash (an empty directory counts as such). Two captures with
# the same files and bytes give the same digest regardless of row order or modified times; nothing is decoded.
def projects_snapshot_digest(doc: Dict[str, Any]) -> str:
    fm = bejson_core_get_field_map(doc)
    if "Relative_Path" not in fm or "File_Hash" not in fm:
        raise ProjectsSnapshotError("snapshot lacks Relative_Path / File_Hash fields")
    lines = []
    for row in doc.get("Values", []):
        empty_dir = "Is_Empty_Dir" in fm and row[fm["Is_Empty_Dir"]] is True
        lines.append(f"{row[fm['Relative_Path']]}\t{'<dir>' if empty_dir else row[fm['File_Hash']]}")
    return hashlib.sha256("\n".join(sorted(lines)).encode("utf-8")).hexdigest()


# Copies `existing` (a snapshot about to be replaced) to <storage>/superseded/<project>/<stem>.superseded-<UTC>.bejson
# and returns the copy's path. Copied, not moved: the live file stays until the atomic replace has succeeded.
def projects_snapshot_supersede(layout: StorageLayout, existing: Path, project_name: str) -> Path:
    dest_dir = layout.superseded_dir / projects_paths_sanitize_name(project_name)
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    dest, n = dest_dir / f"{existing.stem}.superseded-{stamp}{existing.suffix}", 1
    while dest.exists():
        dest = dest_dir / f"{existing.stem}.superseded-{stamp}-{n}{existing.suffix}"
        n += 1
    shutil.copy2(str(existing), str(dest))
    return dest


# Loads and validates a snapshot file; raises ProjectsSnapshotError when unreadable or invalid.
def projects_snapshot_load(snapshot_file: Path) -> Dict[str, Any]:
    result = validate_bejson(str(snapshot_file), is_file=True)
    if not result.valid:
        raise ProjectsSnapshotError(f"{snapshot_file} is not valid BEJSON: {'; '.join(result.errors)}")
    doc = bejson_core_load_file(str(snapshot_file))
    if not doc or doc.get("Records_Type") != [SNAPSHOT_RECORDS_TYPE]:
        raise ProjectsSnapshotError(f"{snapshot_file} is not a {SNAPSHOT_RECORDS_TYPE} snapshot")
    return doc
