"""
File Name:       lib_bejson_Projects_archive.py
Relative Path:   lib/lib_bejson_Projects_archive.py
Family:          Projects
Description:     Archive lifecycle on top of a snapshot: independent verification of a snapshot against its
                 source folder, "scoop" (remove the verified source files once the chunk is proven good), and
                 restore/deploy (write a snapshot back to its original location or anywhere else).
                 Deletion is conservative: nothing is removed unless EVERY snapshot row matches its source
                 byte-for-byte, each file is re-hashed immediately before unlinking, only files that are in
                 the snapshot are touched, and dangerous targets are refused.
VERSION:         1.8.0
Author:          Elton Boehnen · boehnenelton2024@gmail.com · boehnenelton2024.pages.dev · github.com/boehnenelton
RELATIONAL_ID:   e5580165-ec76-4023-9b01-b01bac518ddb
"""

import hashlib
import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from lib_bejson_Core_bejson_path_guard import bejson_safe_join
from lib_bejson_Projects_filters import projects_filters_match
from lib_bejson_Projects_logging import projects_logging_get_logger
from lib_bejson_Projects_snapshot import (
    ProjectsSnapshotError,
    projects_snapshot_iter_rows,
    projects_snapshot_parse_time,
)
from lib_bejson_Projects_tracker import TRACKER_FILENAMES

VERSION = "1.8.0"
_log = projects_logging_get_logger("archive")


class ProjectsArchiveError(Exception):
    """Raised when verification fails or an archive operation is refused. Raised BEFORE anything is changed."""


@dataclass
class SourceCheck:
    verified: List[str] = field(default_factory=list)
    dirs: List[str] = field(default_factory=list)          # captured empty directories confirmed still empty
    problems: List[str] = field(default_factory=list)


@dataclass
class ScoopResult:
    removed: List[str] = field(default_factory=list)
    changed_since_snapshot: List[str] = field(default_factory=list)
    residual: List[str] = field(default_factory=list)      # files still in the folder (not in snapshot / not removed)
    dirs_removed: int = 0
    root_removed: bool = False
    dry_run: bool = False
    filtered_dirs: List[str] = field(default_factory=list)    # filter-matched dirs deleted (or that would be, in a dry run)
    filtered_files: List[str] = field(default_factory=list)   # filter-matched files deleted (or would be)
    filtered_skipped: List[str] = field(default_factory=list) # filter-matched paths NOT deleted (symlink / holds snapshot data)


@dataclass
class FilteredContent:
    dirs: List[str] = field(default_factory=list)             # relative paths, top-most matches only
    files: List[str] = field(default_factory=list)


@dataclass
class RestoreResult:
    destination: Path
    files: int = 0
    dirs: int = 0                                          # captured empty directories recreated
    mtimes_applied: int = 0
    tracker_written: bool = False
    warnings: List[str] = field(default_factory=list)


def _rows(snapshot_doc: Dict[str, Any]):
    """Yields (SnapshotRow, error_or_None) for every row of new- and legacy-schema snapshots."""
    try:
        yield from projects_snapshot_iter_rows(snapshot_doc)
    except ProjectsSnapshotError as exc:
        raise ProjectsArchiveError(str(exc)) from exc


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


# Compares every snapshot row with the disk. File rows: regular file (not a symlink), identical bytes, stored content
# consistent with its recorded hash. Captured-directory rows: a real directory (not a symlink) that is still empty.
# Pure read-only.
def projects_archive_check_against_source(snapshot_doc: Dict[str, Any], target_dir: Path) -> SourceCheck:
    check = SourceCheck()
    base = Path(target_dir).resolve()
    for row, error in _rows(snapshot_doc):
        rel, expected = row.relative_path, row.expected_hash
        try:
            path = Path(bejson_safe_join(str(base), rel))
        except ValueError:
            check.problems.append(f"{rel}: path escapes the target")
            continue
        if error:
            check.problems.append(f"{rel}: {error}")
            continue
        if row.is_dir:
            if path.is_symlink() or not path.is_dir():
                check.problems.append(f"{rel}: not a directory on disk")
            elif any(path.iterdir()):
                check.problems.append(f"{rel}: directory is no longer empty")
            else:
                check.dirs.append(rel)
            continue
        if path.is_symlink() or not path.is_file():
            check.problems.append(f"{rel}: not a regular file on disk")
            continue
        if _sha256(row.raw) != expected:
            check.problems.append(f"{rel}: snapshot content does not match its recorded hash")
            continue
        try:
            if _sha256(path.read_bytes()) != expected:
                check.problems.append(f"{rel}: source differs from snapshot")
                continue
        except OSError as exc:
            check.problems.append(f"{rel}: {exc}")
            continue
        check.verified.append(rel)
    return check


# Refuses targets whose removal would be catastrophic or would cut the tool's own branch: filesystem root; the
# user's home or working directory (or an ancestor of either); and any protected path (project root, storage root)
# - the target may not be one, contain one, or live inside one.
def projects_archive_guard_target(target_dir: Path, protected: Iterable[Path] = ()) -> None:
    target = Path(target_dir).resolve()
    if target == Path(target.anchor):
        raise ProjectsArchiveError("refusing to scoop a filesystem root")
    for path in (Path.home().resolve(), Path.cwd().resolve()):
        if target == path or target in path.parents:
            raise ProjectsArchiveError(f"refusing to scoop {target}: it is or contains {path}")
    for path in (Path(p).resolve() for p in protected):
        if target == path or target in path.parents or path in target.parents:
            raise ProjectsArchiveError(f"refusing to scoop {target}: overlaps protected path {path}")


def _remaining_files(base: Path) -> List[str]:
    found = []
    for root, _dirs, files in os.walk(base):
        for name in files:
            found.append((Path(root) / name).relative_to(base).as_posix())
    return sorted(found)


# Content under base that the chunk filters matched and therefore never archived: top-most matching directories
# (not descended into) and matching files. Symlinks are never listed. Read-only.
def projects_archive_find_filtered(target_dir: Path, dir_patterns: Iterable[str] = (),
                                   file_patterns: Iterable[str] = ()) -> FilteredContent:
    base = Path(target_dir).resolve()
    dir_patterns, file_patterns = tuple(dir_patterns), tuple(file_patterns)
    found = FilteredContent()
    for root, dirs, files in os.walk(base, followlinks=False):
        root_path = Path(root)
        keep = []
        for name in sorted(dirs):
            child = root_path / name
            rel = child.relative_to(base).as_posix()
            if dir_patterns and projects_filters_match(name, rel, dir_patterns):
                if not child.is_symlink():
                    found.dirs.append(rel)
            else:
                keep.append(name)
        dirs[:] = keep
        for name in sorted(files):
            child = root_path / name
            rel = child.relative_to(base).as_posix()
            if file_patterns and projects_filters_match(name, rel, file_patterns) and not child.is_symlink():
                found.files.append(rel)
    return found


# Removes the source files of a snapshot that has just been proven identical, prunes directories left empty, and
# removes the folder itself if nothing remains. Anything not in the snapshot (excluded dirs such as .git, skipped
# or symlinked files) is left untouched and reported as residual. dry_run reports without deleting.
def projects_archive_scoop(snapshot_doc: Dict[str, Any], target_dir: Path,
                           protected: Iterable[Path] = (), dry_run: bool = False,
                           delete_filtered: Optional[Tuple[Iterable[str], Iterable[str]]] = None,
                           keep_filtered: Iterable[str] = ()) -> ScoopResult:
    base = Path(target_dir).resolve()
    projects_archive_guard_target(base, protected)
    check = projects_archive_check_against_source(snapshot_doc, base)
    if check.problems:
        raise ProjectsArchiveError("source does not match snapshot; nothing removed: " + "; ".join(check.problems[:5])
                                   + (f" (+{len(check.problems) - 5} more)" if len(check.problems) > 5 else ""))
    expected = {row.relative_path: row.expected_hash for row, _err in _rows(snapshot_doc) if not row.is_dir}
    result = ScoopResult(dry_run=dry_run)

    for rel in check.verified:
        path = base / rel
        if dry_run:
            result.removed.append(rel)
            continue
        try:                                   # re-hash at the moment of deletion: protects edits made mid-run
            if path.is_symlink() or _sha256(path.read_bytes()) != expected[rel]:
                result.changed_since_snapshot.append(rel)
                continue
            path.unlink()
            result.removed.append(rel)
        except OSError as exc:
            _log.warning("could not remove %s: %s", rel, exc)
            result.changed_since_snapshot.append(rel)

    # Filter-matched content was never archived. It is deleted only when the caller explicitly passes the filter
    # patterns (delete_filtered=(dir_patterns, file_patterns); the caller is responsible for the user's confirmation),
    # only when every snapshot file was removed cleanly, never through a symlink, and never a directory that holds
    # snapshot data.
    filtered_gone = set()
    if delete_filtered is not None:
        found = projects_archive_find_filtered(base, *delete_filtered)
        keep = set(keep_filtered)                                   # top-level matches the user chose to keep
        result.filtered_skipped.extend(r for r in found.dirs + found.files if r in keep)
        found.dirs = [r for r in found.dirs if r not in keep]
        found.files = [r for r in found.files if r not in keep]
        if result.changed_since_snapshot:
            result.filtered_skipped = found.dirs + found.files
        else:
            for rel in found.dirs:
                if any(row_rel == rel or row_rel.startswith(rel + "/") for row_rel in expected):
                    result.filtered_skipped.append(rel)
                    continue
                result.filtered_dirs.append(rel)
            result.filtered_files = list(found.files)
            if not dry_run:
                for rel in list(result.filtered_dirs):
                    try:
                        shutil.rmtree(base / rel)
                    except OSError as exc:
                        _log.warning("could not remove filtered dir %s: %s", rel, exc)
                        result.filtered_dirs.remove(rel)
                        result.filtered_skipped.append(rel)
                for rel in list(result.filtered_files):
                    try:
                        (base / rel).unlink()
                    except OSError as exc:
                        _log.warning("could not remove filtered file %s: %s", rel, exc)
                        result.filtered_files.remove(rel)
                        result.filtered_skipped.append(rel)
            filtered_gone = set(result.filtered_files)
    if dry_run:
        gone_dirs = tuple(d + "/" for d in result.filtered_dirs)
        result.residual = sorted(r for r in set(_remaining_files(base)) - set(result.removed)
                                 if r not in filtered_gone and not r.startswith(gone_dirs))
        return result
    # Prune only what the snapshot accounts for: captured empty dirs and the ancestors of removed files / captured
    # dirs, deepest first, rmdir only (fails harmlessly when not empty). Empty dirs elsewhere (e.g. inside .git or
    # other excluded trees) are never touched.
    candidates = set(check.dirs)
    for rel in list(result.removed) + list(check.dirs):
        parent = Path(rel).parent
        while parent != Path("."):
            candidates.add(parent.as_posix())
            parent = parent.parent
    for rel in sorted(candidates, key=lambda r: (-len(Path(r).parts), r)):
        dir_path = base / rel
        if dir_path.is_symlink() or not dir_path.is_dir():
            continue
        try:
            dir_path.rmdir()
            result.dirs_removed += 1
        except OSError:
            pass                               # not empty: leave it
    result.residual = _remaining_files(base)
    if not result.residual and not any(base.iterdir()):
        base.rmdir()
        result.root_removed = True
    return result


# Writes a snapshot to dest. Refuses to touch any existing path unless overwrite (a file/directory type clash is
# refused even then); verifies every written file against its hash afterwards; recreates captured empty directories;
# restores Modified_Time (files first, then directories deepest-first so child writes do not disturb parent times;
# a failure to set a time is a warning, never an error). write_tracker emits bejson_project.json from the embedded
# tracker when the snapshot has no tracker file of its own.
def projects_archive_restore(snapshot_doc: Dict[str, Any], dest: Path, overwrite: bool = False,
                             write_tracker: bool = False) -> RestoreResult:
    dest = Path(dest).resolve()
    rows = list(_rows(snapshot_doc))
    targets = {}                                # file rows: rel -> (path, raw, digest, modified)
    dir_targets = {}                            # empty-dir rows: rel -> (path, modified)
    for row, error in rows:
        rel = row.relative_path
        if error:
            raise ProjectsArchiveError(f"{rel}: {error}")
        try:
            path = Path(bejson_safe_join(str(dest), rel))
        except ValueError as exc:
            raise ProjectsArchiveError(f"unsafe path in snapshot: {exc}") from exc
        if row.is_dir:
            dir_targets[rel] = (path, row.modified)
            continue
        if _sha256(row.raw) != row.expected_hash:
            raise ProjectsArchiveError(f"{rel}: snapshot content does not match its recorded hash")
        targets[rel] = (path, row.raw, row.expected_hash, row.modified)
    for rel, (path, _raw, _d, _m) in targets.items():
        if path.is_dir() and not path.is_symlink():
            raise ProjectsArchiveError(f"{rel}: a directory already exists where a file must be written")
    for rel, (path, _m) in dir_targets.items():
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            raise ProjectsArchiveError(f"{rel}: a file or link already exists where a directory must be created")
    if not overwrite:
        existing = sorted([rel for rel, (path, _r, _d, _m) in targets.items() if path.exists() or path.is_symlink()]
                          + [rel for rel, (path, _m) in dir_targets.items() if path.exists()])
        if existing:
            raise ProjectsArchiveError(f"{len(existing)} path(s) already exist in {dest} (e.g. {existing[0]}); "
                                       "restore to an empty location or pass overwrite")
    dest.mkdir(parents=True, exist_ok=True)
    for rel, (path, raw, _digest, _m) in targets.items():   # exact bytes: no newline translation, binary intact
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    for rel, (path, _m) in dir_targets.items():
        path.mkdir(parents=True, exist_ok=True)

    bad = [rel for rel, (path, _raw, digest, _m) in targets.items()
           if not path.is_file() or _sha256(path.read_bytes()) != digest]
    bad += [rel for rel, (path, _m) in dir_targets.items() if not path.is_dir()]
    if bad:
        raise ProjectsArchiveError(f"restored paths failed verification: {bad[:5]}")

    result = RestoreResult(destination=dest, files=len(targets), dirs=len(dir_targets))
    stamps = [(path, modified) for path, _r, _d, modified in targets.values()]
    stamps += sorted(((path, modified) for path, modified in dir_targets.values()),
                     key=lambda item: -len(item[0].parts))
    for path, modified in stamps:
        epoch = projects_snapshot_parse_time(modified)
        if epoch is None:
            continue
        try:
            os.utime(path, (epoch, epoch))
            result.mtimes_applied += 1
        except OSError as exc:
            result.warnings.append(f"{path.relative_to(dest).as_posix()}: could not set modified time: {exc}")
            _log.warning("could not set modified time on %s: %s", path, exc)

    has_own_tracker = any(Path(rel).as_posix() in TRACKER_FILENAMES for rel in targets)
    tracker_json = snapshot_doc.get("Tracker_Data")
    if write_tracker and tracker_json and not has_own_tracker:
        tracker_path = dest / TRACKER_FILENAMES[0]
        if tracker_path.exists() and not overwrite:
            raise ProjectsArchiveError(f"{tracker_path.name} already exists in {dest}")
        tracker_path.write_text(json.dumps(json.loads(tracker_json), indent=2) + "\n", encoding="utf-8")
        result.tracker_written = True
    return result


# Read-only preview of projects_archive_restore: what would be written and what would block it.
# {files, dirs, mtimes, collisions: [rel], type_clashes: [rel], blocked: bool, errors: [text]}
def projects_archive_restore_plan(snapshot_doc: Dict[str, Any], dest: Path, overwrite: bool = False) -> Dict[str, Any]:
    dest = Path(dest).resolve()
    plan: Dict[str, Any] = {"files": 0, "dirs": 0, "mtimes": 0, "collisions": [], "type_clashes": [], "errors": [],
                            "destination": str(dest), "blocked": False}
    for row, error in _rows(snapshot_doc):
        rel = row.relative_path
        if error:
            plan["errors"].append(f"{rel}: {error}")
            continue
        try:
            path = Path(bejson_safe_join(str(dest), rel))
        except ValueError as exc:
            plan["errors"].append(f"unsafe path: {exc}")
            continue
        if row.modified:
            plan["mtimes"] += 1
        if row.is_dir:
            plan["dirs"] += 1
            if path.is_symlink() or (path.exists() and not path.is_dir()):
                plan["type_clashes"].append(rel)
            elif path.exists():
                plan["collisions"].append(rel)
            continue
        plan["files"] += 1
        if path.is_dir() and not path.is_symlink():
            plan["type_clashes"].append(rel)
        elif path.exists() or path.is_symlink():
            plan["collisions"].append(rel)
    plan["blocked"] = bool(plan["errors"] or plan["type_clashes"] or (plan["collisions"] and not overwrite))
    return plan
