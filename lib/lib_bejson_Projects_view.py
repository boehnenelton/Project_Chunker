"""
File Name:       lib_bejson_Projects_view.py
Relative Path:   lib/lib_bejson_Projects_view.py
Family:          Projects
Description:     Read-only view model for project management front ends. Groups registry rows by project and
                 enriches each version with facts from its snapshot header (file count, size, date, Binary_Enabled).
                 Never writes; a missing or unreadable snapshot yields a card with the problem noted, not an error.
VERSION:         1.8.0
Author:          Elton Boehnen · boehnenelton2024@gmail.com · boehnenelton2024.pages.dev · github.com/boehnenelton
RELATIONAL_ID:   9c739f19-9533-4062-ba7d-f5172e23bcb6
"""

import json
import os
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from lib_bejson_Projects_logging import projects_logging_get_logger
from lib_bejson_Projects_paths import StorageLayout, projects_paths_sanitize_name
from lib_bejson_Projects_pipeline import _find_snapshot
from lib_bejson_Projects_registry import projects_registry_get, projects_registry_list
from lib_bejson_Projects_snapshot import (
    ProjectsSnapshotError,
    projects_snapshot_iter_rows,
    projects_snapshot_load,
    projects_snapshot_parse_time,
)

VERSION = "1.8.0"
_FACTS_CACHE: Dict[tuple, Dict[str, Any]] = {}      # (path, mtime_ns, size) -> facts; a rewritten snapshot misses the cache
_FACTS_CACHE_MAX = 512
_log = projects_logging_get_logger("view")


# Snapshot header facts for one registry row; {"snapshot_state": "missing"|"unreadable"|"ok", ...}.
def projects_view_snapshot_facts(layout: StorageLayout, row: Dict[str, Any]) -> Dict[str, Any]:
    path: Optional[Path] = _find_snapshot(layout, row)
    if path is None:
        return {"snapshot_state": "missing"}
    try:
        st = path.stat()
        key = (str(path), st.st_mtime_ns, st.st_size)
        if key in _FACTS_CACHE:
            return dict(_FACTS_CACHE[key])
        doc = json.loads(path.read_text(encoding="utf-8"))
        names = [f.get("name") for f in doc.get("Fields", [])]
        dir_idx = names.index("Is_Empty_Dir") if "Is_Empty_Dir" in names else None
        values = doc.get("Values", [])
        empty_dirs = sum(1 for r in values if dir_idx is not None and r[dir_idx] is True)
        facts = {"snapshot_state": "ok", "snapshot_name": path.name, "snapshot_bytes": st.st_size,
                 "files": len(values) - empty_dirs, "empty_dirs": empty_dirs, "snapshot_date": doc.get("Snapshot_Date", ""),
                 "binary_enabled": doc.get("Binary_Enabled"), "snapshot_schema": doc.get("Snapshot_Schema", "1")}
        if len(_FACTS_CACHE) >= _FACTS_CACHE_MAX:
            _FACTS_CACHE.clear()
        _FACTS_CACHE[key] = facts
        return dict(facts)
    except (OSError, ValueError) as exc:
        _log.warning("snapshot %s unreadable: %s", path, exc)
        return {"snapshot_state": "unreadable"}


def _sort_key(card: Dict[str, Any]):
    def num(text: str):
        return tuple(int(p) if p.isdigit() else 0 for p in str(text).replace("-", ".").split(".")) if text else ()
    return (num(card["Project_Version"]), num(card["Package_Version"]), card["Updated_At"])


# [{name, version_count, latest_status, versions:[card,...]}] sorted by project name; versions newest first.
def projects_view_build(layout: StorageLayout) -> List[Dict[str, Any]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in projects_registry_list(layout):
        card = dict(row)
        card.update(projects_view_snapshot_facts(layout, row))
        card["Version_Label"] = (f"V{row['Project_Version']}-PKG{row['Package_Version']}"
                                 if row["Project_Version"] else "unversioned")
        grouped.setdefault(row["Project_Name"], []).append(card)
    projects = []
    for name in sorted(grouped, key=str.lower):
        cards = sorted(grouped[name], key=_sort_key, reverse=True)
        projects.append({"name": name, "version_count": len(cards), "latest_status": cards[0]["Status"],
                         "versions": cards})
    return projects


class ProjectsViewError(Exception):
    """Raised when a version, its snapshot or a file inside it cannot be found or read."""


def _load_version(layout: StorageLayout, selector: str):
    try:
        row = projects_registry_get(layout, selector)
    except Exception as exc:
        raise ProjectsViewError(str(exc)) from exc
    path = _find_snapshot(layout, row)
    if path is None:
        raise ProjectsViewError(f"no snapshot found for {row['Versioned_UUID']}")
    try:
        return row, projects_snapshot_load(path)
    except ProjectsSnapshotError as exc:
        raise ProjectsViewError(str(exc)) from exc


# Files and captured empty dirs of one version: {rows:[{path,kind,size,modified,hash}], total, shown, truncated}.
# query filters by case-insensitive substring of the path; read-only.
def projects_view_files(layout: StorageLayout, selector: str, query: str = "", limit: int = 2000) -> Dict[str, Any]:
    _row, doc = _load_version(layout, selector)
    needle = (query or "").strip().lower()
    out: List[Dict[str, Any]] = []
    total = 0
    for snap_row, error in projects_snapshot_iter_rows(doc):
        total += 1
        if needle and needle not in snap_row.relative_path.lower():
            continue
        out.append({"path": snap_row.relative_path, "kind": "dir" if snap_row.is_dir else ("binary" if snap_row.is_binary else "text"),
                    "size": len(snap_row.raw), "modified": snap_row.modified, "hash": snap_row.expected_hash,
                    "error": error or ""})
    out.sort(key=lambda r: r["path"])
    return {"rows": out[:limit], "total": total, "shown": min(len(out), limit), "truncated": len(out) > limit}


# One file of a version: text is returned (truncated to max_bytes of UTF-8), binary only reports its size.
def projects_view_file_preview(layout: StorageLayout, selector: str, rel: str, max_bytes: int = 65536) -> Dict[str, Any]:
    _row, doc = _load_version(layout, selector)
    for snap_row, error in projects_snapshot_iter_rows(doc):
        if snap_row.relative_path != rel:
            continue
        if error:
            raise ProjectsViewError(f"{rel}: {error}")
        if snap_row.is_dir:
            return {"path": rel, "kind": "dir", "size": 0, "modified": snap_row.modified, "text": "", "truncated": False}
        if snap_row.is_binary:
            return {"path": rel, "kind": "binary", "size": len(snap_row.raw), "modified": snap_row.modified,
                    "text": "", "truncated": False}
        raw = snap_row.raw[:max_bytes]
        return {"path": rel, "kind": "text", "size": len(snap_row.raw), "modified": snap_row.modified,
                "text": raw.decode("utf-8", errors="ignore"), "truncated": len(snap_row.raw) > max_bytes}
    raise ProjectsViewError(f"{rel}: not in this version")


# Writes one version out as a zip (member times and empty dirs preserved) into out_dir, atomically; returns the path.
def projects_view_export_zip(layout: StorageLayout, selector: str, out_dir: Path) -> Path:
    row, doc = _load_version(layout, selector)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    label = (f"{projects_paths_sanitize_name(row['Project_Name'])}-V{projects_paths_sanitize_name(row['Project_Version'])}"
             f"-PKG{projects_paths_sanitize_name(row['Package_Version'])}-{row['Versioned_UUID'][:8]}")
    final = out_dir / f"{label}.zip"
    temp = out_dir / f".{label}.zip.part"
    try:
        with zipfile.ZipFile(temp, "w", zipfile.ZIP_DEFLATED) as archive:
            for snap_row, error in projects_snapshot_iter_rows(doc):
                if error:
                    raise ProjectsViewError(f"{snap_row.relative_path}: {error}")
                epoch = projects_snapshot_parse_time(snap_row.modified)
                stamp = time.localtime(epoch) if epoch else time.localtime(time.time())
                info = zipfile.ZipInfo(f"{label}/{snap_row.relative_path}" + ("/" if snap_row.is_dir else ""),
                                       date_time=(max(stamp.tm_year, 1980), stamp.tm_mon, stamp.tm_mday,
                                                  stamp.tm_hour, stamp.tm_min, stamp.tm_sec))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = ((0o40755 if snap_row.is_dir else 0o100644) << 16) | (0x10 if snap_row.is_dir else 0)
                archive.writestr(info, b"" if snap_row.is_dir else snap_row.raw)
        os.replace(temp, final)
    finally:
        if temp.exists():
            temp.unlink()
    return final
