"""
File Name:       lib_bejson_Projects_active.py
Relative Path:   lib/lib_bejson_Projects_active.py
Family:          Projects
Description:     The "active project" of the Project Chunker web UI: the one project every other page targets until
                 it is changed or cleared. Persisted server-side (data/active_project.json, BEJSON 104a, one row
                 active_project / set_at, zero rows = none) so it survives page reloads, browser restarts and
                 server restarts, and is shared by every tab. Written atomically. Reading never raises: a missing,
                 unreadable or invalid file simply means "no active project". Whether the stored name still exists
                 in the registry is the caller's check (the registry, not this file, is the source of truth).
VERSION:         1.8.0
Author:          Elton Boehnen · boehnenelton2024@gmail.com · boehnenelton2024.pages.dev · github.com/boehnenelton
RELATIONAL_ID:   4b6e0d3a-7c1f-49a2-b8d5-2e9f1a6c7d40
"""

import re
import time
from pathlib import Path
from typing import Optional

from lib_bejson_Core_bejson_core import bejson_core_atomic_write, bejson_core_get_field_map, bejson_core_load_file
from lib_bejson_Core_bejson_validator import validate_bejson
from lib_bejson_Projects_logging import projects_logging_get_logger

VERSION = "1.8.0"
ACTIVE_FILE_NAME = "active_project.json"
RECORDS_TYPE = "ActiveProject"
FIELDS = [{"name": "active_project", "type": "string"}, {"name": "set_at", "type": "string"}]
MAX_NAME_LENGTH = 200
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_log = projects_logging_get_logger("active")


def projects_active_path(data_dir: Path) -> Path:
    return Path(data_dir) / ACTIVE_FILE_NAME


def projects_active_valid_name(name: object) -> bool:
    return isinstance(name, str) and bool(name.strip()) and len(name) <= MAX_NAME_LENGTH and not _CONTROL_RE.search(name)


def _write(path: Path, rows: list) -> bool:
    doc = {"Format": "BEJSON", "Format_Version": "104a", "Format_Creator": "Elton Boehnen",
           "State_Name": "active_project", "Records_Type": [RECORDS_TYPE],
           "Fields": [dict(f) for f in FIELDS], "Values": rows}
    if not validate_bejson(doc).valid:
        return False
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    return bool(bejson_core_atomic_write(str(path), doc))


# The stored project name, or None (no file, unreadable, invalid, wrong shape, or cleared). Never raises.
def projects_active_get(path: Path) -> Optional[str]:
    try:
        if not Path(path).is_file():
            return None
        if not validate_bejson(str(path), is_file=True).valid:
            _log.warning("%s is invalid; no active project", path)
            return None
        doc = bejson_core_load_file(str(path)) or {}
        if doc.get("Records_Type") != [RECORDS_TYPE]:
            return None
        fm = bejson_core_get_field_map(doc)
        if "active_project" not in fm or not doc.get("Values"):
            return None
        name = doc["Values"][0][fm["active_project"]]
        return name if projects_active_valid_name(name) else None
    except Exception as exc:                                    # reading state must never break a page
        _log.warning("could not read %s: %s", path, exc)
        return None


# Makes `name` the active project. False when the name is unusable or the write failed (nothing changed then).
def projects_active_set(path: Path, name: str) -> bool:
    if not projects_active_valid_name(name):
        return False
    return _write(path, [[name, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())]])


# No active project (keeps a valid, empty state file). False only when the write failed.
def projects_active_clear(path: Path) -> bool:
    return _write(path, [])
