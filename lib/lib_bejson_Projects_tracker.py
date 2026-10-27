"""
File Name:       lib_bejson_Projects_tracker.py
Relative Path:   lib/lib_bejson_Projects_tracker.py
Family:          Projects
Description:     Tracker detection, identity extraction and auto-generation for a target directory.
                 Reads an existing bejson_project.json (or legacy .bejson_project.json); otherwise derives
                 a tracker from the folder name (spaces -> "_", V#-PKG# honoured). Never writes into the
                 target directory and never invents a global Project_GUID.
                 1.8.0: an explicit identity override (name / version / package) wins over the tracker and the folder
                 name, and the identity a zip's own file name carries (Name-V#-PKG#.zip) is exposed so the pipeline can
                 use it or at least flag a disagreement with the manifest.
VERSION:         1.8.0
Author:          Elton Boehnen · boehnenelton2024@gmail.com · boehnenelton2024.pages.dev · github.com/boehnenelton
RELATIONAL_ID:   0ca0e561-017e-45fc-994c-d02ba3a97f2f
"""

import json
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from lib_bejson_Core_bejson_core import bejson_core_get_field_map
from lib_bejson_Projects_logging import projects_logging_get_logger
from lib_bejson_Projects_paths import projects_paths_sanitize_name

VERSION = "1.8.0"
TRACKER_FILENAMES = ("bejson_project.json", ".bejson_project.json")
DEFAULT_PROJECT_VERSION = "100"
DEFAULT_PACKAGE_VERSION = "1"
GUID_PLACEHOLDER = "GUID_PLACEHOLDER"
_FOLDER_VERSION_RE = re.compile(r"V(\d+)-PKG(\d+)", re.IGNORECASE)
_FOLDER_SUFFIX_RE = re.compile(r"[-_ ]*V\d+-PKG\d+.*$", re.IGNORECASE)

_log = projects_logging_get_logger("tracker")

# Canonical Project Type Ledger & Standard Project Types
DEFAULT_PROJECT_TYPES = (
    "python", "bash", "react", "website", "app", "skill", "powershell", "angular", "android_app", "library"
)
ENV_PROJECT_TYPE_LEDGER = "PROJECT_TYPE_LEDGER"

PROJECT_STATUS_ACTIVE = "Active"
PROJECT_STATUS_INACTIVE = "Inactive"
PROJECT_STATUS_ARCHIVED = "Archived"
PROJECT_STATUS_LEGACY = "Legacy"
VALID_PROJECT_STATUSES = (PROJECT_STATUS_ACTIVE, PROJECT_STATUS_INACTIVE, PROJECT_STATUS_ARCHIVED, PROJECT_STATUS_LEGACY)

# Version/package pairs that mean "nobody ever set a real version": the chunker's own folder defaults and the
# 1.0.0 / 100 a fresh project scaffold ships with. A tracker carrying one of these is not trusted over a zip file name.
SCAFFOLD_DEFAULT_IDENTITIES = ((DEFAULT_PROJECT_VERSION, DEFAULT_PACKAGE_VERSION), ("1.0.0", "100"))
_IDENT_VALUE_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._-]{0,31}$")


class ProjectsTrackerError(Exception):
    """Raised for an unusable identity override."""


def projects_tracker_resolve_ledger_path() -> Optional[Path]:
    """Resolves the Canonical Project Type Ledger dynamically from environment variables."""
    import os
    env_path = os.environ.get(ENV_PROJECT_TYPE_LEDGER)
    if env_path and Path(env_path).is_file():
        return Path(env_path)
    storage = os.environ.get("INTERNAL_STORAGE")
    if storage:
        candidate = Path(storage) / "Admin" / "Dev" / "Policy" / "project_type_ledger.104a.bejson"
        if candidate.is_file():
            return candidate
    return None


def projects_tracker_get_ledger_types(ledger_path: Optional[Path] = None) -> List[str]:
    """Retrieves standardized types from the canonical Project Type Ledger, with fallback to built-in types."""
    path = ledger_path or projects_tracker_resolve_ledger_path()
    if path and path.is_file():
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(doc, dict) and isinstance(doc.get("Values"), list):
                field_map = bejson_core_get_field_map(doc)
                type_idx = field_map.get("Type_Name", 0)
                types = [row[type_idx] for row in doc["Values"] if len(row) > type_idx and row[type_idx]]
                if types:
                    return types
        except Exception as exc:
            _log.debug("could not load type ledger from %s: %s", path, exc)
    return list(DEFAULT_PROJECT_TYPES)


def projects_tracker_detect_type(target_dir: Path) -> Optional[str]:
    """Auto-detects project type based on filesystem signatures matching canonical type ledger rules."""
    target = Path(target_dir)
    if not target.is_dir():
        return None
    # 1. Agent Skill
    if (target / "SKILL.md").is_file():
        return "skill"
    # 2. React / Angular / App
    pkg_json = target / "package.json"
    if pkg_json.is_file():
        try:
            pkg_data = json.loads(pkg_json.read_text(encoding="utf-8"))
            deps = {**pkg_data.get("dependencies", {}), **pkg_data.get("devDependencies", {})}
            if "react" in deps or (target / "vite.config.ts").is_file() or (target / "src" / "App.tsx").is_file():
                return "react"
            if "@angular/core" in deps or (target / "angular.json").is_file():
                return "angular"
        except Exception:
            pass
        return "app"
    # 3. Android App
    if (target / "AndroidManifest.xml").is_file() or (target / "app" / "src" / "main" / "AndroidManifest.xml").is_file():
        return "android_app"
    # 4. Python
    if list(target.glob("*.py")) or (target / "requirements.txt").is_file() or (target / "pyproject.toml").is_file() or (target / "src").is_dir() and list((target / "src").glob("*.py")):
        if any(target.glob("lib_bejson_*.py")) and not (target / "app.py").is_file() and not (target / "main.py").is_file():
            return "library"
        return "python"
    # 5. Bash
    if list(target.glob("*.sh")) or list(target.glob("*.bash")):
        return "bash"
    # 6. PowerShell
    if list(target.glob("*.ps1")):
        return "powershell"
    # 7. Website
    if (target / "index.html").is_file():
        return "website"
    return None


@dataclass
class IdentityOverride:
    """Explicit identity for a run. Empty fields are left alone; set fields replace what the tracker/folder gave."""
    name: str = ""
    version: str = ""
    package: str = ""
    project_type: str = ""
    status: str = ""

    def active(self) -> bool:
        return bool(self.name or self.version or self.package or self.project_type or self.status)

    def validate(self) -> None:
        for label, value in (("version", self.version), ("package", self.package)):
            if value and not _IDENT_VALUE_RE.match(value):
                raise ProjectsTrackerError(
                    f"identity {label} {value!r} must be 1-32 letters, digits, '.', '_' or '-' (starting with a "
                    "letter or digit)")
        if self.project_type:
            allowed = [t.lower() for t in projects_tracker_get_ledger_types()]
            if self.project_type.lower() not in allowed:
                raise ProjectsTrackerError(
                    f"invalid project type {self.project_type!r}; must match canonical type ledger ({', '.join(allowed)})")
        if self.status:
            allowed_stat = [s.lower() for s in VALID_PROJECT_STATUSES]
            if self.status.lower() not in allowed_stat:
                raise ProjectsTrackerError(
                    f"invalid project status {self.status!r}; must be one of {', '.join(VALID_PROJECT_STATUSES)}")


def projects_tracker_is_scaffold_default(version: str, package: str) -> bool:
    return (str(version).strip(), str(package).strip()) in SCAFFOLD_DEFAULT_IDENTITIES


# (name, version, package) carried by a file name like "My Tool-V230-PKG7.zip"; None when it has no V#-PKG# part.
def projects_tracker_filename_identity(file_name: str) -> Optional[Tuple[str, str, str]]:
    stem = file_name[:-4] if file_name.lower().endswith(".zip") else file_name
    if not _FOLDER_VERSION_RE.search(stem):
        return None
    return projects_tracker_derive_from_folder(stem)

# (project_name, project_version, package_version) -> previously issued Versioned_UUID or None
UuidLookup = Callable[[str, str, str], Optional[str]]


@dataclass
class TrackerInfo:
    project_name: str
    project_version: str
    package_version: str
    versioned_uuid: str
    tracker_data: Dict[str, Any]
    source: str                      # "existing" | "generated"
    tracker_path: Optional[str] = None
    notes: list = field(default_factory=list)
    project_type: Optional[str] = None
    status: str = "Active"


# First tracker file present in target_dir (current name wins over legacy), else None.
def projects_tracker_find_file(target_dir: Path) -> Optional[Path]:
    for name in TRACKER_FILENAMES:
        candidate = Path(target_dir) / name
        if candidate.is_file():
            return candidate
    return None


# Parses a tracker file; returns None (with a warning) when unreadable or not a JSON object.
def projects_tracker_read_file(path: Path) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _log.warning("Tracker %s unreadable (%s); falling back to generated tracker", path, exc)
        return None
    if not isinstance(data, dict):
        _log.warning("Tracker %s is not a JSON object; falling back to generated tracker", path)
        return None
    return data


# (name, version, package) parsed from a folder name like "My Tool-V12-PKG3"; defaults when no V#-PKG# present.
def projects_tracker_derive_from_folder(folder_name: str) -> tuple:
    name = projects_paths_sanitize_name(_FOLDER_SUFFIX_RE.sub("", folder_name.replace(" ", "_")))
    match = _FOLDER_VERSION_RE.search(folder_name)
    if match:
        return name, match.group(1), match.group(2)
    return name, DEFAULT_PROJECT_VERSION, DEFAULT_PACKAGE_VERSION


def _is_uuid(value: Any) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, AttributeError, TypeError):
        return False


def _clean(value: Any) -> str:
    return "" if value is None else str(value).strip()


# Pulls identity values from a tracker document: top-level headers first, then the first Values row through the
# field-map cache (no positional indices). Missing values come back as "".
def projects_tracker_extract_identity(tracker_doc: Dict[str, Any]) -> Dict[str, str]:
    keys = ("Project_Name", "Project_Version", "Package_Version", "Versioned_UUID", "Type", "Status")
    identity = {key: _clean(tracker_doc.get(key)) for key in keys}
    if not identity["Type"] and tracker_doc.get("Project_Type"):
        identity["Type"] = _clean(tracker_doc.get("Project_Type"))
    values = tracker_doc.get("Values")
    if isinstance(values, list) and values and isinstance(values[0], list):
        field_map = bejson_core_get_field_map(tracker_doc)
        for key in keys:
            if not identity[key] and key in field_map and field_map[key] < len(values[0]):
                identity[key] = _clean(values[0][field_map[key]])
        if not identity["Type"] and "Project_Type" in field_map and field_map["Project_Type"] < len(values[0]):
            identity["Type"] = _clean(values[0][field_map["Project_Type"]])
    return identity


# Detects the tracker in target_dir or builds one. A Versioned_UUID that is absent or not a real UUID (e.g. the
# template placeholder) is replaced by a previously issued UUID for the same name+version (lookup) or a fresh uuid4.
def projects_tracker_detect_or_create(target_dir: Path,
                                      uuid_lookup: Optional[UuidLookup] = None,
                                      override: Optional[IdentityOverride] = None) -> TrackerInfo:
    target_dir = Path(target_dir)
    folder_name, folder_ver, folder_pkg = projects_tracker_derive_from_folder(target_dir.name)
    override = override if override and override.active() else None
    if override:
        override.validate()

    tracker_path = projects_tracker_find_file(target_dir)
    doc = projects_tracker_read_file(tracker_path) if tracker_path else None

    if doc is not None:
        identity = projects_tracker_extract_identity(doc)
        name = projects_paths_sanitize_name(identity["Project_Name"] or folder_name)
        pver = identity["Project_Version"] or folder_ver
        pkg = identity["Package_Version"] or folder_pkg
        ptype = identity["Type"] or projects_tracker_detect_type(target_dir) or ""
        pstatus = identity["Status"] or PROJECT_STATUS_ACTIVE
        notes = []
        if override:
            name = projects_paths_sanitize_name(override.name) if override.name else name
            pver, pkg = override.version or pver, override.package or pkg
            if override.project_type:
                ptype = override.project_type.lower()
            if override.status:
                pstatus = override.status.capitalize()
            doc["Project_Name"], doc["Project_Version"], doc["Package_Version"] = name, pver, pkg
            doc["Type"], doc["Status"] = ptype, pstatus
            notes.append(f"identity overridden: {name} V{pver}-PKG{pkg} type={ptype} status={pstatus}")
        else:
            if not doc.get("Type") and ptype:
                doc["Type"] = ptype
            if not doc.get("Status") and pstatus:
                doc["Status"] = pstatus
        v_uuid = identity["Versioned_UUID"]
        if not _is_uuid(v_uuid):
            v_uuid = (uuid_lookup(name, pver, pkg) if uuid_lookup else None) or str(uuid.uuid4())
            notes.append("tracker had no valid Versioned_UUID; assigned one")
        doc["Versioned_UUID"] = v_uuid
        return TrackerInfo(name, pver, pkg, v_uuid, doc, "existing", str(tracker_path), notes,
                           project_type=ptype, status=pstatus)

    if override:
        folder_name = projects_paths_sanitize_name(override.name) if override.name else folder_name
        folder_ver, folder_pkg = override.version or folder_ver, override.package or folder_pkg
        gen_type = override.project_type.lower() if override.project_type else (projects_tracker_detect_type(target_dir) or "")
        gen_status = override.status.capitalize() if override.status else PROJECT_STATUS_ACTIVE
    else:
        gen_type = projects_tracker_detect_type(target_dir) or ""
        gen_status = PROJECT_STATUS_ACTIVE

    v_uuid = (uuid_lookup(folder_name, folder_ver, folder_pkg) if uuid_lookup else None) or str(uuid.uuid4())
    generated = {
        "Format": "BEJSON",
        "Format_Version": "104a",
        "Format_Creator": "Elton Boehnen",
        "Project_Name": folder_name,
        "Project_Version": folder_ver,
        "Package_Version": folder_pkg,
        "Versioned_UUID": v_uuid,
        "Project_GUID": GUID_PLACEHOLDER,
        "Type": gen_type or None,
        "Status": gen_status,
        "Records_Type": ["RootPackage"],
        "Fields": [{"name": "change_log", "type": "string"}],
        "Values": [],
    }
    gen_notes = ["no tracker in target; generated from folder name"]
    if gen_type:
        gen_notes.append(f"detected project type: {gen_type}")
    if override:
        gen_notes.append(f"identity overridden: {folder_name} V{folder_ver}-PKG{folder_pkg} type={gen_type} status={gen_status}")
    return TrackerInfo(folder_name, folder_ver, folder_pkg, v_uuid, generated, "generated", None, gen_notes,
                       project_type=gen_type, status=gen_status)
