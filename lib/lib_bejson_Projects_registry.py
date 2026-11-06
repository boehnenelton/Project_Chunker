"""
File Name:       lib_bejson_Projects_registry.py
Relative Path:   lib/lib_bejson_Projects_registry.py
Family:          Projects
Description:     Tracker database + master registry persistence. Each project gets
                 storage/trackers/data/<project>.bejson (one record per Versioned_UUID, upserted, history kept);
                 every UUID is keyed to its tracker file in storage/master/data/registry.bejson, together with
                 version, original source location, snapshot file and lifecycle status
                 (tracked -> chunked -> archived -> restored). Legacy 3-field registries migrate on first write.
                 Writes are PID-locked, validated, and atomic.
VERSION:         1.9.0
Author:          Elton Boehnen · boehnenelton2024@gmail.com · boehnenelton2024.pages.dev · github.com/boehnenelton
RELATIONAL_ID:   59f519b1-6bf6-4654-8856-9cb79580c963
"""

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from lib_bejson_Core_bejson_core import (
    bejson_core_atomic_write,
    bejson_core_get_field_map,
    bejson_core_load_file,
)
from lib_bejson_Core_bejson_validator import validate_bejson
from lib_bejson_Projects_locks import ProjectsPathLock
from lib_bejson_Projects_logging import projects_logging_get_logger
from lib_bejson_Projects_paths import StorageLayout, projects_paths_sanitize_name
from lib_bejson_Projects_tracker import TrackerInfo

VERSION = "1.9.0"
TRACKER_RECORDS_TYPE = "TrackerRecord"
REGISTRY_RECORDS_TYPE = "RegistryRecord"
STATUS_TRACKED, STATUS_CHUNKED, STATUS_ARCHIVED, STATUS_RESTORED = "tracked", "chunked", "archived", "restored"
VALID_STATUSES = (
    STATUS_TRACKED, STATUS_CHUNKED, STATUS_ARCHIVED, STATUS_RESTORED,
    "development", "shelved", "active", "inactive", "archived", "legacy",
    "Development", "Shelved", "Active", "Inactive", "Archived", "Legacy"
)
MIN_UUID_PREFIX = 8

TRACKER_FIELDS = [
    {"name": "Versioned_UUID", "type": "string"},
    {"name": "Project_Name", "type": "string"},
    {"name": "Tracker_Data", "type": "string"},
]
LEGACY_REGISTRY_FIELDS = [
    {"name": "Versioned_UUID", "type": "string"},
    {"name": "Project_Name", "type": "string"},
    {"name": "Tracker_File", "type": "string"},
]
REGISTRY_FIELDS = LEGACY_REGISTRY_FIELDS + [
    {"name": "Project_Version", "type": "string"},
    {"name": "Package_Version", "type": "string"},
    {"name": "Original_Path", "type": "string"},
    {"name": "Snapshot_File", "type": "string"},
    {"name": "Status", "type": "string"},
    {"name": "Updated_At", "type": "string"},
    {"name": "Type", "type": "string"},
    {"name": "Official_Path", "type": "string"},
]
# Values given to the extra columns when a legacy 3-field row is migrated (v1 -> v4).
_MIGRATION_FILL = ["", "", "", "", STATUS_TRACKED, "", "", ""]

_log = projects_logging_get_logger("registry")


class ProjectsRegistryError(Exception):
    """Raised when a registry/tracker database is corrupt, ambiguous, or cannot be written."""


def projects_registry_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _new_doc(records_type: str, fields: List[Dict[str, str]]) -> Dict[str, Any]:
    return {
        "Format": "BEJSON",
        "Format_Version": "104a",
        "Format_Creator": "Elton Boehnen",
        "Records_Type": [records_type],
        "Fields": [dict(f) for f in fields],
        "Values": [],
    }


def _field_names(doc: Dict[str, Any]) -> List[str]:
    return [f.get("name") for f in doc.get("Fields", [])]


# A legacy 3-field, 9-field, or 10-field registry is upgraded in memory: columns appended, rows padded.
def _migrate_registry(doc: Dict[str, Any]) -> Dict[str, Any]:
    existing_names = _field_names(doc)
    target_names = [f["name"] for f in REGISTRY_FIELDS]
    if existing_names == target_names:
        return doc
    if existing_names == [f["name"] for f in LEGACY_REGISTRY_FIELDS]:
        doc["Fields"] = [dict(f) for f in REGISTRY_FIELDS]
        doc["Values"] = [list(row) + list(_MIGRATION_FILL) for row in doc["Values"]]
        doc.pop("_bejson_field_map", None)
        _log.info("migrated legacy 3-field registry to v4 (%d rows)", len(doc["Values"]))
        return doc
    v2_names = [f["name"] for f in LEGACY_REGISTRY_FIELDS] + [
        "Project_Version", "Package_Version", "Original_Path", "Snapshot_File", "Status", "Updated_At"
    ]
    if existing_names == v2_names:
        orig_idx = v2_names.index("Original_Path")
        doc["Fields"] = [dict(f) for f in REGISTRY_FIELDS]
        doc["Values"] = [list(row) + ["", row[orig_idx] if orig_idx < len(row) else ""] for row in doc["Values"]]
        doc.pop("_bejson_field_map", None)
        _log.info("migrated v2 registry to v4 (added Type and Official_Path fields, %d rows)", len(doc["Values"]))
        return doc
    v3_names = [f["name"] for f in LEGACY_REGISTRY_FIELDS] + [
        "Project_Version", "Package_Version", "Original_Path", "Snapshot_File", "Status", "Updated_At", "Type"
    ]
    if existing_names == v3_names:
        orig_idx = v3_names.index("Original_Path")
        doc["Fields"] = [dict(f) for f in REGISTRY_FIELDS]
        doc["Values"] = [list(row) + [row[orig_idx] if orig_idx < len(row) else ""] for row in doc["Values"]]
        doc.pop("_bejson_field_map", None)
        _log.info("migrated v3 registry to v4 (added Official_Path field, %d rows)", len(doc["Values"]))
        return doc
    if all(name in target_names for name in existing_names):
        field_map = bejson_core_get_field_map(doc)
        new_values = []
        for row in doc["Values"]:
            new_row = []
            for f in REGISTRY_FIELDS:
                fname = f["name"]
                if fname in field_map and field_map[fname] < len(row):
                    new_row.append(row[field_map[fname]])
                elif fname == "Status":
                    new_row.append(STATUS_TRACKED)
                elif fname == "Official_Path" and "Original_Path" in field_map and field_map["Original_Path"] < len(row):
                    new_row.append(row[field_map["Original_Path"]])
                else:
                    new_row.append("")
            new_values.append(new_row)
        doc["Fields"] = [dict(f) for f in REGISTRY_FIELDS]
        doc["Values"] = new_values
        doc.pop("_bejson_field_map", None)
        _log.info("migrated partial registry schema to REGISTRY_FIELDS (%d rows)", len(doc["Values"]))
        return doc
    return doc


# Loads an existing database or returns a fresh one. An existing file that is unreadable, invalid, or has a
# different record type raises instead of being silently overwritten.
def _load_or_new(path: Path, records_type: str, fields: List[Dict[str, str]]) -> Dict[str, Any]:
    if not path.exists():
        return _new_doc(records_type, fields)
    doc = bejson_core_load_file(str(path))
    if doc is None:
        raise ProjectsRegistryError(f"{path} exists but cannot be parsed; refusing to overwrite")
    result = validate_bejson(doc)
    if not result.valid:
        raise ProjectsRegistryError(f"{path} failed BEJSON validation: {'; '.join(result.errors)}")
    if doc.get("Records_Type") != [records_type]:
        raise ProjectsRegistryError(f"{path} has Records_Type {doc.get('Records_Type')}, expected [{records_type!r}]")
    if records_type == REGISTRY_RECORDS_TYPE:
        doc = _migrate_registry(doc)
    names = _field_names(doc)
    if names != [f["name"] for f in fields]:
        raise ProjectsRegistryError(f"{path} has unexpected Fields {names}")
    return doc


# Merge-upsert keyed by Versioned_UUID. Columns named in `changes` are overwritten; on an existing row every other
# column is preserved; on a new row the remaining columns come from `new_defaults` (else ""). Returns added/updated.
def _upsert_row(doc: Dict[str, Any], changes: Dict[str, Any], new_defaults: Optional[Dict[str, Any]] = None) -> str:
    field_map = bejson_core_get_field_map(doc)
    uuid_idx = field_map["Versioned_UUID"]
    for i, row in enumerate(doc["Values"]):
        if row[uuid_idx] == changes["Versioned_UUID"]:
            merged = list(row)
            for name, value in changes.items():
                merged[field_map[name]] = value
            doc["Values"][i] = merged
            return "updated"
    new_row: List[Any] = [""] * len(doc["Fields"])
    for name, value in {**(new_defaults or {}), **changes}.items():
        new_row[field_map[name]] = value
    doc["Values"].append(new_row)
    return "added"


# Validates then atomically writes (caller holds the lock).
def _write_validated(path: Path, doc: Dict[str, Any]) -> None:
    clean = {k: v for k, v in doc.items() if not k.startswith("_")}
    result = validate_bejson(clean)
    if not result.valid:
        raise ProjectsRegistryError(f"refusing to write invalid BEJSON to {path}: {'; '.join(result.errors)}")
    if not bejson_core_atomic_write(str(path), clean):
        raise ProjectsRegistryError(f"atomic write failed for {path}")


def _locked_upsert(path: Path, records_type: str, fields: List[Dict[str, str]], changes: Dict[str, Any],
                   new_defaults: Optional[Dict[str, Any]] = None, must_exist: bool = False) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with ProjectsPathLock(path):
        doc = _load_or_new(path, records_type, fields)
        if must_exist:
            idx = bejson_core_get_field_map(doc)["Versioned_UUID"]
            if not any(r[idx] == changes["Versioned_UUID"] for r in doc["Values"]):
                raise ProjectsRegistryError(f"{changes['Versioned_UUID']} is not in {path.name}")
        action = _upsert_row(doc, changes, new_defaults)
        _write_validated(path, doc)
    return action


# Pillar 1: upsert this tracker into the project's tracker database. Returns the database path.
def projects_registry_store_tracker(layout: StorageLayout, info: TrackerInfo) -> Path:
    db_path = layout.tracker_db(info.project_name)
    action = _locked_upsert(db_path, TRACKER_RECORDS_TYPE, TRACKER_FIELDS, {
        "Versioned_UUID": info.versioned_uuid,
        "Project_Name": info.project_name,
        "Tracker_Data": json.dumps(info.tracker_data, ensure_ascii=False),
    })
    _log.info("tracker record %s in %s", action, db_path.name)
    return db_path


# Returns the canonical Official_Path recorded in the registry for a project, or None.
def projects_registry_get_official_path(layout: StorageLayout, project_name: str) -> Optional[str]:
    if not layout.registry_file.exists():
        return None
    safe = projects_paths_sanitize_name(project_name)
    rows = projects_registry_list(layout)
    for r in reversed(rows):
        if r.get("Project_Name") == safe and r.get("Official_Path"):
            return r["Official_Path"]
    for r in reversed(rows):
        if r.get("Project_Name") == safe and r.get("Original_Path"):
            return r["Original_Path"]
    return None


# Pillar 2: key the UUID to its tracker file and record version + original location + official path.
# Status and Snapshot_File are only defaulted for a brand-new row so re-tracking never rewinds an archived/chunked entry.
def projects_registry_register_uuid(layout: StorageLayout, info: TrackerInfo, tracker_db: Path,
                                    original_path: Optional[Path] = None,
                                    official_path: Optional[str] = None) -> Path:
    changes = {
        "Versioned_UUID": info.versioned_uuid,
        "Project_Name": info.project_name,
        "Tracker_File": layout.relative_ref(tracker_db),
        "Project_Version": info.project_version,
        "Package_Version": info.package_version,
        "Type": info.project_type or "",
        "Updated_At": projects_registry_now(),
    }
    if original_path is not None:
        resolved_orig = str(Path(original_path).resolve())
        changes["Original_Path"] = resolved_orig
    else:
        resolved_orig = ""

    existing_official = projects_registry_get_official_path(layout, info.project_name)
    if official_path:
        chosen_official = str(Path(official_path).resolve())
    elif existing_official:
        chosen_official = existing_official
    else:
        chosen_official = resolved_orig

    if chosen_official:
        changes["Official_Path"] = chosen_official

    if existing_official and resolved_orig and Path(resolved_orig).resolve() != Path(existing_official).resolve():
        _log.warning("DUAL REPO WARNING for %s: target '%s' differs from official repo '%s'",
                     info.project_name, resolved_orig, existing_official)

    initial_status = info.status if info.status else STATUS_TRACKED
    action = _locked_upsert(layout.registry_file, REGISTRY_RECORDS_TYPE, REGISTRY_FIELDS, changes,
                            new_defaults={"Status": initial_status})
    _log.info("registry entry %s for %s", action, info.versioned_uuid)
    return layout.registry_file


# Both pillars in order: tracker database first, registry second.
def projects_registry_register(layout: StorageLayout, info: TrackerInfo,
                               original_path: Optional[Path] = None,
                               official_path: Optional[str] = None) -> Tuple[Path, Path]:
    tracker_db = projects_registry_store_tracker(layout, info)
    registry = projects_registry_register_uuid(layout, info, tracker_db, original_path, official_path=official_path)
    return tracker_db, registry


# Updates columns of an existing registry row. Unknown keys and invalid statuses are rejected.
def projects_registry_update(layout: StorageLayout, versioned_uuid: str, **columns: str) -> None:
    allowed = {"Status", "Snapshot_File", "Original_Path", "Type", "Project_Version", "Package_Version", "Official_Path"}
    unknown = set(columns) - allowed
    if unknown:
        raise ProjectsRegistryError(f"cannot update columns {sorted(unknown)}")
    if "Status" in columns and columns["Status"] not in VALID_STATUSES:
        raise ProjectsRegistryError(f"invalid status {columns['Status']!r}")
    _locked_upsert(layout.registry_file, REGISTRY_RECORDS_TYPE, REGISTRY_FIELDS,
                   {"Versioned_UUID": versioned_uuid, "Updated_At": projects_registry_now(), **columns},
                   must_exist=True)


# Sets the Official_Path for all rows of a project matching selector (UUID, prefix, or Project_Name).
def projects_registry_set_official_path(layout: StorageLayout, selector: str, official_path: str) -> int:
    resolved_path = str(Path(official_path).resolve())
    if not layout.registry_file.exists():
        raise ProjectsRegistryError("registry file does not exist")
    with ProjectsPathLock(layout.registry_file):
        doc = _load_or_new(layout.registry_file, REGISTRY_RECORDS_TYPE, REGISTRY_FIELDS)
        field_map = bejson_core_get_field_map(doc)
        uuid_idx = field_map["Versioned_UUID"]
        name_idx = field_map["Project_Name"]
        off_idx = field_map["Official_Path"]
        upd_idx = field_map["Updated_At"]

        sel_lower = selector.strip().lower()
        target_name = None
        for row in doc["Values"]:
            if row[name_idx].strip().lower() == sel_lower:
                target_name = row[name_idx]
                break
        if not target_name:
            for row in doc["Values"]:
                r_uuid = row[uuid_idx].lower()
                if r_uuid == sel_lower or (len(sel_lower) >= MIN_UUID_PREFIX and r_uuid.startswith(sel_lower)):
                    target_name = row[name_idx]
                    break
        if not target_name:
            raise ProjectsRegistryError(f"no registry entries match selector {selector!r}")

        count = 0
        now_ts = projects_registry_now()
        for row in doc["Values"]:
            if row[name_idx] == target_name:
                row[off_idx] = resolved_path
                row[upd_idx] = now_ts
                count += 1
        _write_validated(layout.registry_file, doc)
        _log.info("updated Official_Path to '%s' for project '%s' across %d row(s)", resolved_path, target_name, count)
        return count


# UUID previously issued for the same project name + version + package, or None. Lets re-runs on a tracker-less
# folder stay idempotent instead of minting a new UUID every time. Corrupt databases are treated as "no match".
def projects_registry_find_uuid(layout: StorageLayout, project_name: str,
                                project_version: str, package_version: str) -> Optional[str]:
    db_path = layout.tracker_db(projects_paths_sanitize_name(project_name))
    if not db_path.exists():
        return None
    doc = bejson_core_load_file(str(db_path))
    if not doc or not isinstance(doc.get("Values"), list):
        return None
    field_map = bejson_core_get_field_map(doc)
    if "Versioned_UUID" not in field_map or "Tracker_Data" not in field_map:
        return None
    for row in doc["Values"]:
        try:
            data = json.loads(row[field_map["Tracker_Data"]])
        except (ValueError, TypeError, IndexError):
            continue
        if (str(data.get("Project_Version", "")).strip() == str(project_version)
                and str(data.get("Package_Version", "")).strip() == str(package_version)):
            return row[field_map["Versioned_UUID"]]
    return None


# Registry rows as dicts keyed by field name (empty list when no registry exists yet). Legacy files are
# presented in v3 shape without being rewritten. Supports filtering by project_type and status.
def projects_registry_list(layout: StorageLayout,
                           project_type: Optional[str] = None,
                           status: Optional[str] = None) -> List[Dict[str, Any]]:
    if not layout.registry_file.exists():
        return []
    doc = _load_or_new(layout.registry_file, REGISTRY_RECORDS_TYPE, REGISTRY_FIELDS)
    field_map = bejson_core_get_field_map(doc)
    rows = [{name: row[idx] for name, idx in field_map.items()} for row in doc["Values"]]
    if project_type:
        pt = str(project_type).strip().lower()
        rows = [r for r in rows if str(r.get("Type", "")).strip().lower() == pt]
    if status:
        st = str(status).strip().lower()
        rows = [r for r in rows if str(r.get("Status", "")).strip().lower() == st]
    return rows


# True when the master registry already holds this Versioned_UUID.
def projects_registry_has_uuid(layout: StorageLayout, versioned_uuid: str) -> bool:
    try:
        return any(row["Versioned_UUID"] == versioned_uuid for row in projects_registry_list(layout))
    except ProjectsRegistryError:
        return False


# One row by full UUID or unique prefix (>= 8 chars). Not found / ambiguous -> ProjectsRegistryError.
def projects_registry_get(layout: StorageLayout, selector: str) -> Dict[str, Any]:
    selector = (selector or "").strip().lower()
    rows = projects_registry_list(layout)
    exact = [r for r in rows if r["Versioned_UUID"].lower() == selector]
    if exact:
        return exact[0]
    if len(selector) >= MIN_UUID_PREFIX:
        matches = [r for r in rows if r["Versioned_UUID"].lower().startswith(selector)]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ProjectsRegistryError(f"UUID prefix {selector!r} is ambiguous ({len(matches)} matches)")
    raise ProjectsRegistryError(f"no registry entry matches {selector!r}")


# Every registered version of one project, oldest registration first (file order), with optional filters.
def projects_registry_versions(layout: StorageLayout, project_name: str,
                               project_type: Optional[str] = None,
                               status: Optional[str] = None) -> List[Dict[str, Any]]:
    safe = projects_paths_sanitize_name(project_name)
    return [r for r in projects_registry_list(layout, project_type=project_type, status=status)
            if r["Project_Name"] == safe]
