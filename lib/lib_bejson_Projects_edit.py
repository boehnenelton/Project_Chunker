"""
File Name:       lib_bejson_Projects_edit.py
Relative Path:   lib/lib_bejson_Projects_edit.py
Family:          Projects
Description:     Edit tracker details for ALL versions of one project in a single transaction. A version's details live
                 in three places that must stay in step: its row in the project's tracker database, its registry row,
                 and the tracker copy embedded in its snapshot header (Restore writes that copy back out). One call
                 changes all of them. Version numbers, Versioned_UUID and the global Project_GUID are never editable.
                 Renaming moves the tracker database and snapshot files. Writes are staged to temp files, committed by
                 rename, verified, and rolled back completely if anything fails. Locks: registry + tracker databases.
VERSION:         1.8.0
Author:          Elton Boehnen · boehnenelton2024@gmail.com · boehnenelton2024.pages.dev · github.com/boehnenelton
RELATIONAL_ID:   c5dbf6d7-49ee-4324-8903-4aa2ff7fc83a
"""

import contextlib
import json
import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from lib_bejson_Core_bejson_core import (
    bejson_core_atomic_write,
    bejson_core_get_field_map,
    bejson_core_load_file,
)
from lib_bejson_Core_bejson_validator import validate_bejson
from lib_bejson_Projects_locks import ProjectsPathLock
from lib_bejson_Projects_logging import projects_logging_get_logger
from lib_bejson_Projects_paths import StorageLayout, projects_paths_sanitize_name
from lib_bejson_Projects_pipeline import _find_snapshot
from lib_bejson_Projects_registry import projects_registry_list, projects_registry_now

VERSION = "1.8.0"
TMP_SUFFIX, BAK_SUFFIX = ".edit-tmp", ".editbak"
MAX_SNAPSHOT_REWRITE_BYTES = 256 * 1024 * 1024
SHORT_LIMIT, LONG_LIMIT, NAME_LIMIT = 500, 4000, 100
BACKUP_KEEP = 20
IDENTITY_KEYS = ("Project_Version", "Package_Version", "Versioned_UUID", "Project_GUID")
# Never editable: structure, identity, version-specific facts, registry/snapshot bookkeeping.
PROTECTED = frozenset({
    "Format", "Format_Version", "Format_Creator", "Records_Type", "Fields", "Values", "Project_Version",
    "Package_Version", "Versioned_UUID", "Project_GUID", "Session_Id", "Schema_Version", "Is_Registered",
    "Package_Date", "Git_Commit_Hash", "Archive_Filename", "Package_Checksum_SHA256", "Project_Modified_Date",
    "Change_Log", "Raw_JSON", "Tracker_Data", "Tracker_File", "Snapshot_File", "Status", "Updated_At",
    "Snapshot_Schema", "Snapshot_Date", "Binary_Enabled", "Marker_File_Path", "First_Detected_Date",
    "Last_Seen_Date", "Detected_By_Index_Type",
})
_CUSTOM_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_URL_RE = re.compile(r"^(?:(?:https?|ssh|git)://[^\s]+|git@[^\s:]+:[^\s]+)$")

_log = projects_logging_get_logger("edit")


class ProjectsEditError(Exception):
    """Raised for invalid input, blocked edits, and failed (rolled back) transactions."""


@dataclass(frozen=True)
class EditField:
    key: str
    label: str
    hint: str
    top: str                 # key in the tracker document header ("" = not stored in the tracker)
    row: str                 # field name in the tracker's first Values row ("" = none)
    multiline: bool = False
    kind: str = "text"       # text | url | name | path


FIELDS = (
    EditField("Project_Name", "Project name", "Renames the project everywhere: tracker database, registry and snapshot files.",
              "Project_Name", "Project_Name", kind="name"),
    EditField("Project_Description", "Description", "", "Project_Description", "Description", multiline=True),
    EditField("Author", "Author", "", "Author", "Author"),
    EditField("Remote_Repo_URL", "GitHub / repository URL", "https://github.com/user/repo or git@github.com:user/repo.git",
              "Remote_Repo_URL", "Remote_Repo_URL", kind="url"),
    EditField("Git_Branch", "Git branch", "", "Git_Branch", "Git_Branch"),
    EditField("Main_Executable", "Main executable", "Relative path of the entry point.", "Main_Executable", "Main_Executable"),
    EditField("Runtime_Type", "Runtime type", "For example python3, node, bash.", "Runtime_Type", "Runtime_Type"),
    EditField("Project_Type", "Project type", "For example Standalone_Tool.", "Project_Type", "Project_Type"),
    EditField("Known_Issues", "Known issues", "", "Known_Issues", "Known_Issues", multiline=True),
    EditField("User_Feedback", "User feedback", "", "User_Feedback", "User_Feedback", multiline=True),
    EditField("Original_Path", "Source folder", "Where Restore puts the project back by default (registry and snapshots).",
              "", "", kind="path"),
)
FIELD_BY_KEY = {f.key: f for f in FIELDS}


# ── input validation ───────────────────────────────────────────────────────────────────────────────────────

def _clean_text(label: str, raw: str, limit: int, multiline: bool) -> str:
    value = raw.replace("\r\n", "\n").replace("\r", "\n").strip()
    if _CONTROL_RE.search(value):
        raise ProjectsEditError(f"{label}: control characters are not allowed")
    if not multiline and "\n" in value:
        raise ProjectsEditError(f"{label}: must be a single line")
    if len(value) > limit:
        raise ProjectsEditError(f"{label}: too long ({len(value)} characters, limit {limit})")
    return value


def _clean_value(spec: EditField, raw: str) -> str:
    value = _clean_text(spec.label, raw, LONG_LIMIT if spec.multiline else SHORT_LIMIT, spec.multiline)
    if spec.kind == "name":
        if not value:
            raise ProjectsEditError("Project name: cannot be empty")
        value = _clean_text(spec.label, value, NAME_LIMIT * 4, False)
        return projects_paths_sanitize_name(value)[:NAME_LIMIT].strip("._-") or projects_paths_sanitize_name("")
    if spec.kind == "url" and value and not _URL_RE.match(value):
        raise ProjectsEditError("GitHub / repository URL: use https://, http://, ssh://, git:// or git@host:path")
    if spec.kind == "path":
        if not value or not os.path.isabs(value):
            raise ProjectsEditError("Source folder: must be an absolute path")
    return value


# Validated {key: text}. Known fields are checked per kind; any other key must be a plain custom name that is not
# protected. Raises ProjectsEditError with a user-facing message.
def projects_edit_normalize(changes: Any) -> Dict[str, str]:
    if not isinstance(changes, dict) or not changes:
        raise ProjectsEditError("nothing to change")
    out: Dict[str, str] = {}
    for key, raw in changes.items():
        if not isinstance(key, str):
            raise ProjectsEditError("field names must be text")
        if raw is None:
            raw = ""
        if not isinstance(raw, str):
            raise ProjectsEditError(f"{key}: value must be text")
        spec = FIELD_BY_KEY.get(key)
        if spec:
            out[key] = _clean_value(spec, raw)
            continue
        if key in PROTECTED:
            raise ProjectsEditError(f"{key} cannot be changed here (version, identity and bookkeeping fields are protected)")
        if not _CUSTOM_KEY_RE.match(key):
            raise ProjectsEditError(f"{key!r}: field names use letters, digits and underscores and start with a letter (max 64)")
        out[key] = _clean_text(key, raw, SHORT_LIMIT, False)
    return out


# ── tracker document access (field-map cache; no positional indices) ───────────────────────────────────────

def _txt(value: Any) -> str:
    return "" if value is None else str(value)


def _row0(doc: Dict[str, Any]):
    values, fields = doc.get("Values"), doc.get("Fields")
    if isinstance(values, list) and values and isinstance(values[0], list) and isinstance(fields, list):
        try:
            return values[0], bejson_core_get_field_map(doc), fields
        except (KeyError, TypeError, ValueError):
            return None
    return None


def _read_value(doc: Dict[str, Any], top: str, row_name: str) -> str:
    if top and top in doc and not isinstance(doc[top], (dict, list)):
        return _txt(doc[top])
    found = _row0(doc) if row_name else None
    if found:
        row, fm, _ = found
        if row_name in fm and fm[row_name] < len(row) and not isinstance(row[fm[row_name]], (dict, list)):
            return _txt(row[fm[row_name]])
    return ""


# Applies the patch to a tracker document in place; every place a value already lives is updated, and a header key is
# created when it lives nowhere yet. Returns [{key, old, new}] for values that actually changed.
def _patch_doc(doc: Dict[str, Any], patch: Dict[str, str]) -> List[Dict[str, str]]:
    before = _identity(doc)
    changes: List[Dict[str, str]] = []
    found = _row0(doc)
    for key, new in patch.items():
        spec = FIELD_BY_KEY.get(key)
        if spec and spec.kind == "path":
            continue                                   # Original_Path lives in the registry and snapshot headers only
        top, row_name = (spec.top, spec.row) if spec else (key, key)
        old: Optional[str] = None
        hit = False
        if top in doc:
            if isinstance(doc[top], (dict, list)):
                raise ProjectsEditError(f"{key} holds structured data, not text, and cannot be edited here")
            old, doc[top], hit = _txt(doc[top]), new, True
        if found:
            row, fm, fields = found
            idx = fm.get(row_name)
            if idx is not None and idx < len(row) and fields[idx].get("type", "string") == "string":
                if old is None:
                    old = _txt(row[idx])
                row[idx], hit = new, True
        if not hit:
            old, doc[top] = "", new
        if old != new:
            changes.append({"key": key, "old": old or "", "new": new})
    if _identity(doc) != before:
        raise ProjectsEditError("internal check failed: an identity field would have changed; nothing was written")
    return changes


def _identity(doc: Dict[str, Any]) -> Dict[str, str]:
    return {k: _read_value(doc, k, k) for k in IDENTITY_KEYS}


def _version_label(row: Dict[str, Any]) -> str:
    return f"V{row['Project_Version']}-PKG{row['Package_Version']}" if row.get("Project_Version") else "unversioned"


# ── read side: current values per version ──────────────────────────────────────────────────────────────────

def _load_tracker_docs(layout: StorageLayout, name: str) -> Dict[str, Dict[str, Any]]:
    path = layout.tracker_db(name)
    if not path.is_file():
        return {}
    doc = bejson_core_load_file(str(path))
    if not doc or not isinstance(doc.get("Values"), list):
        return {}
    fm = bejson_core_get_field_map(doc)
    out: Dict[str, Dict[str, Any]] = {}
    for row in doc["Values"]:
        try:
            out[row[fm["Versioned_UUID"]]] = json.loads(row[fm["Tracker_Data"]])
        except (ValueError, TypeError, KeyError, IndexError):
            continue
    return out


def _field_card(spec_key: str, label: str, hint: str, multiline: bool, kind: str, by_version: Dict[str, str],
                order: List[str]) -> Dict[str, Any]:
    distinct: List[str] = []
    for uid in order:
        if by_version.get(uid, "") not in distinct:
            distinct.append(by_version.get(uid, ""))
    return {"key": spec_key, "label": label, "hint": hint, "multiline": multiline, "kind": kind,
            "distinct": distinct[:20], "mixed": len(distinct) > 1, "by_version": by_version}


# Current editable values of one project across all of its versions (read-only).
def projects_edit_details(layout: StorageLayout, name: str) -> Dict[str, Any]:
    rows = [r for r in projects_registry_list(layout) if r["Project_Name"] == name]
    if not rows:
        raise ProjectsEditError(f"unknown project {name!r}")
    docs = _load_tracker_docs(layout, name)
    order = [r["Versioned_UUID"] for r in rows]
    editable = []
    for spec in FIELDS:
        if spec.kind == "path":
            by = {r["Versioned_UUID"]: _txt(r.get("Original_Path")) for r in rows}
        else:
            by = {uid: _read_value(docs[uid], spec.top, spec.row) if uid in docs else "" for uid in order}
        editable.append(_field_card(spec.key, spec.label, spec.hint, spec.multiline, spec.kind, by, order))
    known_tops = {f.top for f in FIELDS if f.top} | {f.row for f in FIELDS if f.row}
    extra_keys: List[str] = []
    for doc in docs.values():
        for key, value in doc.items():
            if (key not in PROTECTED and key not in known_tops and not key.startswith("_") and _CUSTOM_KEY_RE.match(key)
                    and isinstance(value, (str, int, float)) and not isinstance(value, bool) and key not in extra_keys):
                extra_keys.append(key)
    extras = [_field_card(k, k.replace("_", " "), "", False, "text",
                          {uid: _read_value(docs[uid], k, "") if uid in docs else "" for uid in order}, order)
              for k in sorted(extra_keys)]
    return {"name": name, "versions": len(rows), "editable": editable, "extras": extras,
            "missing_trackers": [u for u in order if u not in docs], "snapshot_limit_mb": MAX_SNAPSHOT_REWRITE_BYTES // (1024 * 1024)}


# ── planning (pure: reads, never writes) ───────────────────────────────────────────────────────────────────


# New file name for a chunk when its project is renamed: the project-name prefix follows the rename. A chunk written
# by an older package (no prefix, "V<ver>-PKG<pkg>.bejson") gets the prefix now.
def _renamed_chunk_name(filename: str, old_name: str, new_name: str) -> str:
    old_prefix = f"{projects_paths_sanitize_name(old_name)}-"
    rest = filename[len(old_prefix):] if filename.startswith(old_prefix) else filename
    return f"{projects_paths_sanitize_name(new_name)}-{rest}"

@dataclass
class _SnapJob:
    uuid: str
    label: str
    old_path: Path
    new_path: Path
    bytes: int


@dataclass
class _Plan:
    old_name: str
    new_name: str
    patch: Dict[str, str]
    update_snapshots: bool
    reg_doc: Dict[str, Any]
    reg_rows_old: List[Dict[str, Any]]
    tdoc: Dict[str, Any]
    old_tracker: Path
    new_tracker: Path
    new_tracker_ref: str
    versions: List[Dict[str, Any]]
    snap_jobs: List[_SnapJob]
    warnings: List[str]

    @property
    def renamed(self) -> bool:
        return self.new_name != self.old_name

    @property
    def unchanged(self) -> bool:
        return not self.renamed and not any(v["changes"] for v in self.versions)


def _build(layout: StorageLayout, name: str, patch_raw: Any, update_snapshots: bool) -> _Plan:
    patch = projects_edit_normalize(patch_raw)
    if not layout.registry_file.is_file():
        raise ProjectsEditError("no projects are registered yet")
    reg = bejson_core_load_file(str(layout.registry_file))
    if not reg or not isinstance(reg.get("Values"), list):
        raise ProjectsEditError("the registry is unreadable; run Verify")
    rfm = bejson_core_get_field_map(reg)
    for col in ("Versioned_UUID", "Project_Name", "Tracker_File"):
        if col not in rfm:
            raise ProjectsEditError(f"the registry has no {col} column; run Verify")
    if "Original_Path" in patch and "Original_Path" not in rfm:
        raise ProjectsEditError("the registry is the legacy 3-column format; track or chunk anything once to upgrade it")
    idxs = [i for i, r in enumerate(reg["Values"]) if r[rfm["Project_Name"]] == name]
    if not idxs:
        raise ProjectsEditError(f"unknown project {name!r}")

    new_name = patch.get("Project_Name", name)
    renamed = new_name != name
    warnings: List[str] = []
    old_tracker, new_tracker = layout.tracker_db(name), layout.tracker_db(new_name)
    if renamed:
        update_snapshots = True                       # snapshot headers carry the name; they must follow
        if new_name.lower() == name.lower():
            raise ProjectsEditError("a new name that differs only by letter case is not supported (SD cards ignore case); pick a different name")
        taken = {r[rfm["Project_Name"]].lower() for i, r in enumerate(reg["Values"]) if i not in idxs}
        if new_name.lower() in taken:
            raise ProjectsEditError(f"a project named {new_name!r} already exists; merging projects is not supported")
        if new_tracker.exists() or (layout.snapshots_dir / new_name).exists():
            raise ProjectsEditError(f"storage already holds files named {new_name!r}; choose another name")
    if not old_tracker.is_file():
        raise ProjectsEditError("this project's tracker database is missing; run Verify")
    tdoc = bejson_core_load_file(str(old_tracker))
    if not tdoc or not isinstance(tdoc.get("Values"), list):
        raise ProjectsEditError("this project's tracker database is unreadable; run Verify")
    tfm = bejson_core_get_field_map(tdoc)
    for col in ("Versioned_UUID", "Project_Name", "Tracker_Data"):
        if col not in tfm:
            raise ProjectsEditError(f"the tracker database has no {col} column; run Verify")

    tpatch = {k: v for k, v in patch.items() if k != "Original_Path"}
    new_ref = layout.relative_ref(new_tracker)
    changes_by_uuid: Dict[str, List[Dict[str, str]]] = {}
    for row in tdoc["Values"]:
        uid = row[tfm["Versioned_UUID"]]
        try:
            data = json.loads(row[tfm["Tracker_Data"]])
        except (ValueError, TypeError):
            raise ProjectsEditError(f"{uid[:8]}: its tracker data is not valid JSON; run Verify")
        changes_by_uuid[uid] = _patch_doc(data, tpatch) if tpatch else []
        row[tfm["Tracker_Data"]] = json.dumps(data, ensure_ascii=False)
        if renamed:
            row[tfm["Project_Name"]] = new_name

    versions: List[Dict[str, Any]] = []
    snap_jobs: List[_SnapJob] = []
    old_snap_dir = (layout.snapshots_dir / name / "data").resolve()
    now = projects_registry_now()
    old_rows: List[Dict[str, Any]] = []
    for i in idxs:
        row = reg["Values"][i]
        rd = {n: row[j] for n, j in rfm.items()}
        old_rows.append(rd)
        uid, label = rd["Versioned_UUID"], _version_label(rd)
        changes = list(changes_by_uuid.get(uid, []))
        if uid not in changes_by_uuid and tpatch:
            warnings.append(f"{label}: no tracker record found; only its registry row is updated")
        if renamed and not any(c["key"] == "Project_Name" for c in changes):
            changes.append({"key": "Project_Name", "old": name, "new": new_name})
        if "Original_Path" in patch and _txt(rd.get("Original_Path")) != patch["Original_Path"]:
            changes.append({"key": "Original_Path", "old": _txt(rd.get("Original_Path")), "new": patch["Original_Path"]})
        snap = {"state": "none", "action": "none", "bytes": 0, "name": ""}
        path = _find_snapshot(layout, rd)
        if path is None:
            snap["state"] = "missing"
            if rd.get("Status") in ("chunked", "archived", "restored"):
                warnings.append(f"{label}: status {rd['Status']} but its snapshot file was not found")
        else:
            size = path.stat().st_size
            snap.update(state="ok", bytes=size, name=path.name)
            new_path = path
            if renamed:
                try:
                    inside = path.resolve().parent == old_snap_dir
                except OSError:
                    inside = False
                if inside:
                    new_path = layout.snapshots_dir / projects_paths_sanitize_name(new_name) / "data" / _renamed_chunk_name(
                        path.name, name, new_name)
                else:
                    warnings.append(f"{label}: snapshot is stored outside the standard folder; its header is updated but the file is not moved")
            if size > MAX_SNAPSHOT_REWRITE_BYTES and (update_snapshots or renamed):
                mb = MAX_SNAPSHOT_REWRITE_BYTES // (1024 * 1024)
                if renamed:
                    raise ProjectsEditError(f"{label}: the snapshot is over {mb} MB and cannot be rewritten, so the project cannot be renamed")
                snap.update(state="too_large", action="skipped")
                warnings.append(f"{label}: snapshot is over {mb} MB, so its embedded tracker copy was not updated")
            elif update_snapshots or renamed:
                snap["action"] = "move + rewrite" if new_path != path else "rewrite"
                snap_jobs.append(_SnapJob(uid, label, path, new_path, size))
            else:
                snap["action"] = "unchanged (snapshot copies not updated)"
        versions.append({"uuid": uid, "label": label, "status": rd.get("Status", ""), "changes": changes, "snapshot": snap})
        # registry row
        if renamed:
            row[rfm["Project_Name"]] = new_name
            row[rfm["Tracker_File"]] = new_ref
            ref = _txt(rd.get("Snapshot_File"))
            job = next((j for j in snap_jobs if j.uuid == uid), None)
            if ref and job and job.new_path != job.old_path and "Snapshot_File" in rfm:
                row[rfm["Snapshot_File"]] = layout.relative_ref(job.new_path)
        if "Original_Path" in patch:
            row[rfm["Original_Path"]] = patch["Original_Path"]
        if "Updated_At" in rfm and (changes or renamed):
            row[rfm["Updated_At"]] = now
    return _Plan(name, new_name, patch, update_snapshots, reg, old_rows, tdoc, old_tracker, new_tracker, new_ref,
                 versions, snap_jobs, warnings)


# Preview for the UI: exactly what apply would do, per version. Writes nothing.
def projects_edit_plan(layout: StorageLayout, name: str, patch: Any, update_snapshots: bool = True) -> Dict[str, Any]:
    plan = _build(layout, name, patch, update_snapshots)
    return {"project": plan.old_name, "new_name": plan.new_name, "renamed": plan.renamed,
            "update_snapshots": plan.update_snapshots, "unchanged": plan.unchanged,
            "fields": sorted({c["key"] for v in plan.versions for c in v["changes"]}),
            "versions": plan.versions, "snapshots_to_rewrite": len(plan.snap_jobs),
            "snapshot_bytes": sum(j.bytes for j in plan.snap_jobs), "warnings": plan.warnings}


# ── apply: stage -> commit by rename -> verify -> clean up (or roll everything back) ───────────────────────

def _tmp_for(path: Path) -> Path:
    return path.with_name(path.name + TMP_SUFFIX)


def _stage_doc(path: Path, doc: Dict[str, Any], what: str) -> Path:
    clean = {k: v for k, v in doc.items() if not k.startswith("_")}
    result = validate_bejson(clean)
    if not result.valid:
        raise ProjectsEditError(f"refusing to write an invalid {what}: {'; '.join(result.errors)}")
    tmp = _tmp_for(path)
    tmp.parent.mkdir(parents=True, exist_ok=True)
    if not bejson_core_atomic_write(str(tmp), clean):
        raise ProjectsEditError(f"could not stage {what}")
    return tmp


def _stage_snapshot(job: _SnapJob, plan: _Plan, tpatch: Dict[str, str]) -> Path:
    try:
        doc = json.loads(job.old_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ProjectsEditError(f"{job.label}: snapshot is unreadable ({exc})")
    if doc.get("Versioned_UUID") != job.uuid:
        raise ProjectsEditError(f"{job.label}: snapshot header does not match its registry entry; run Verify")
    if plan.renamed:
        doc["Project_Name"], doc["Tracker_File"] = plan.new_name, plan.new_tracker_ref
    if "Original_Path" in plan.patch:
        doc["Original_Path"] = plan.patch["Original_Path"]
    raw = doc.get("Tracker_Data")
    if raw and tpatch:
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            raise ProjectsEditError(f"{job.label}: the tracker copy inside the snapshot is not valid JSON")
        _patch_doc(data, tpatch)
        doc["Tracker_Data"] = json.dumps(data, ensure_ascii=False)
    return _stage_doc(job.new_path, doc, f"snapshot {job.label}")


def _rollback(done: List[list]) -> None:
    for kind, final, bak, placed in reversed(done):
        try:
            if kind == "put":
                if placed and final.exists():
                    final.unlink()
                if bak:
                    os.replace(bak, final)
            else:
                os.replace(bak, final)
        except OSError as exc:                         # keep going: restore as much as possible
            _log.error("rollback of %s failed: %s", final, exc)


# actions: ("put", final, tmp) | ("rm", final, None). Originals are renamed to <name>.editbak until the very end.
def _commit(actions: List[tuple]) -> List[list]:
    done: List[list] = []
    try:
        for kind, final, tmp in actions:
            final = Path(final)
            bak = final.with_name(final.name + BAK_SUFFIX)
            if kind == "put":
                had = final.exists()
                if had:
                    os.replace(final, bak)
                done.append(["put", final, bak if had else None, False])
                os.replace(tmp, final)
                done[-1][3] = True
            else:
                os.replace(final, bak)
                done.append(["rm", final, bak, True])
    except BaseException:
        _rollback(done)
        raise
    return done


def _backup_files(layout: StorageLayout, name: str, files: List[Path]) -> Path:
    root = layout.storage_root / "edit_backups"
    stem = f"{time.strftime('%Y%m%d-%H%M%S')}-{name}"
    dest, n = root / stem, 1
    while dest.exists():                                   # two edits in one second keep separate backups
        n += 1
        dest = root / f"{stem}-{n}"
    dest.mkdir(parents=True)
    for f in files:
        if f.is_file():
            shutil.copy2(f, dest / f.name)
    for old in sorted(p for p in root.iterdir() if p.is_dir())[:-BACKUP_KEEP]:
        shutil.rmtree(old, ignore_errors=True)
    return dest


def _post_check(layout: StorageLayout, plan: _Plan) -> None:
    reg = bejson_core_load_file(str(layout.registry_file))
    fm = bejson_core_get_field_map(reg)
    rows = {r[fm["Versioned_UUID"]]: r for r in reg["Values"] if r[fm["Project_Name"]] == plan.new_name}
    for old in plan.reg_rows_old:
        row = rows.get(old["Versioned_UUID"])
        if row is None or row[fm["Project_Version"]] != old["Project_Version"] or row[fm["Package_Version"]] != old["Package_Version"]:
            raise ProjectsEditError(f"{old['Versioned_UUID'][:8]}: registry check failed after writing")
    tdoc = bejson_core_load_file(str(plan.new_tracker))
    if not tdoc or len(tdoc.get("Values", [])) != len(plan.tdoc["Values"]):
        raise ProjectsEditError("tracker database check failed after writing")
    for job in plan.snap_jobs:
        if not job.new_path.is_file():
            raise ProjectsEditError(f"{job.label}: snapshot missing after writing")


# Applies the patch to every version of `name`. Returns a summary dict. Raises ProjectsEditError after a complete
# rollback if anything fails; the stored files are then exactly as they were.
def projects_edit_apply(layout: StorageLayout, name: str, patch: Any, update_snapshots: bool = True,
                        emit: Optional[Callable[[Dict[str, Any]], None]] = None) -> Dict[str, Any]:
    emit = emit or (lambda _e: None)
    clean = projects_edit_normalize(patch)
    new_name = clean.get("Project_Name", name)
    lock_paths = sorted({layout.registry_file, layout.tracker_db(name), layout.tracker_db(new_name)}, key=str)
    staged: List[Path] = []
    with contextlib.ExitStack() as stack:
        for p in lock_paths:
            p.parent.mkdir(parents=True, exist_ok=True)
            stack.enter_context(ProjectsPathLock(p))
        plan = _build(layout, name, clean, update_snapshots)      # re-read under the locks
        if plan.unchanged:
            raise ProjectsEditError("nothing to change: every selected value already matches")
        tpatch = {k: v for k, v in plan.patch.items() if k != "Original_Path"}
        backup = _backup_files(layout, name, [layout.registry_file, plan.old_tracker])
        actions: List[tuple] = []
        try:
            total = len(plan.snap_jobs)
            for n, job in enumerate(plan.snap_jobs, 1):
                emit({"stage": "edit", "label": f"updating snapshot {n}/{total}: {job.label}", "index": n, "total": total})
                tmp = _stage_snapshot(job, plan, tpatch)
                staged.append(tmp)
                actions.append(("put", job.new_path, tmp))
                if job.new_path != job.old_path:
                    actions.append(("rm", job.old_path, None))
            emit({"stage": "edit", "label": "writing tracker database and registry", "index": total, "total": total})
            ttmp = _stage_doc(plan.new_tracker, plan.tdoc, "tracker database")
            staged.append(ttmp)
            actions.append(("put", plan.new_tracker, ttmp))
            if plan.renamed:
                actions.append(("rm", plan.old_tracker, None))
            rtmp = _stage_doc(layout.registry_file, plan.reg_doc, "registry")
            staged.append(rtmp)
            actions.append(("put", layout.registry_file, rtmp))
            done = _commit(actions)
            try:
                _post_check(layout, plan)
            except BaseException:
                _rollback(done)
                raise
        except ProjectsEditError:
            raise
        except Exception as exc:
            _log.error("edit of %s failed and was rolled back: %s", name, exc)
            raise ProjectsEditError(f"the edit failed and was rolled back; nothing changed ({exc})")
        finally:
            for tmp in staged:
                with contextlib.suppress(OSError):
                    if tmp.exists():
                        tmp.unlink()
        for kind, final, bak, _placed in done:
            if bak:
                with contextlib.suppress(OSError):
                    bak.unlink()
            if kind == "rm":
                with contextlib.suppress(OSError):
                    final.parent.rmdir()
                    final.parent.parent.rmdir()
    _log.info("edited %s (%d versions, fields %s)", name, len(plan.versions), sorted(plan.patch))
    return {"project": plan.old_name, "new_name": plan.new_name, "renamed": plan.renamed,
            "versions_updated": len([v for v in plan.versions if v["changes"]]), "versions": len(plan.versions),
            "fields": sorted({c["key"] for v in plan.versions for c in v["changes"]}),
            "snapshots_rewritten": len(plan.snap_jobs), "warnings": plan.warnings, "backup": backup.name}
