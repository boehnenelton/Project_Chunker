"""
File Name:       lib_bejson_Projects_config.py
Relative Path:   lib/lib_bejson_Projects_config.py
Family:          Projects
Description:     Menu configuration export/load. Discovers the project's runnable scripts (CLI/, Menu/) and
                 writes config/menu_scripts.json (BEJSON 104a): each script's location relative to the project
                 root and relative to the menu, so the menu can locate and wrap the CLI wherever the project
                 folder lives. Also writes the standard config/config.json when absent.
VERSION:         1.8.0
Author:          Elton Boehnen · boehnenelton2024@gmail.com · boehnenelton2024.pages.dev · github.com/boehnenelton
RELATIONAL_ID:   074d81dc-7418-48fe-a27f-1466b55e8ff0
"""

import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from lib_bejson_Core_bejson_core import (
    bejson_core_atomic_write,
    bejson_core_get_field_map,
    bejson_core_load_file,
)
from lib_bejson_Core_bejson_path_guard import bejson_safe_join
from lib_bejson_Core_bejson_validator import validate_bejson
from lib_bejson_Projects_logging import projects_logging_get_logger

VERSION = "1.8.0"
BINARY_KEY = "Binary_Enabled"
SCRIPTS_CONFIG_NAME = "menu_scripts.json"
STANDARD_CONFIG_NAME = "config.json"
SCRIPT_DIRS = (("CLI", "cli"), ("Menu", "menu"), ("Helper", "helper"), ("Web", "web"))
SCRIPT_FIELDS = [
    {"name": "script_id", "type": "string"},
    {"name": "kind", "type": "string"},
    {"name": "script_path", "type": "string"},
    {"name": "relative_to_menu", "type": "string"},
    {"name": "description", "type": "string"},
]
_DESC_RE = re.compile(r"^Description:\s*(.+(?:\n[ \t]+\S.*)*)", re.MULTILINE)
_log = projects_logging_get_logger("config")


class ProjectsConfigError(Exception):
    """Raised when the menu configuration is missing, invalid, or points outside the project."""


def _describe(script: Path) -> str:
    try:
        head = script.read_text(encoding="utf-8", errors="replace")[:4096]
    except OSError:
        return ""
    match = _DESC_RE.search(head)
    if not match:
        return ""
    text = " ".join(match.group(1).split())
    return text.split(". ")[0].rstrip(".") + "." if ". " in text else text


# Every public .py script directly inside CLI/ and Menu/ -> rows (paths posix, relative).
def projects_config_discover_scripts(project_root: Path, menu_dir: Path) -> List[List[str]]:
    rows: List[List[str]] = []
    for dirname, kind in SCRIPT_DIRS:
        folder = Path(project_root) / dirname
        if not folder.is_dir():
            continue
        for script in sorted(folder.glob("*.py")):
            if script.name.startswith("_"):
                continue
            rows.append([
                script.stem, kind,
                script.relative_to(project_root).as_posix(),
                Path(os.path.relpath(script, menu_dir)).as_posix(),
                _describe(script),
            ])
    return rows


# Writes config/menu_scripts.json (validated, atomic) and returns its path.
def projects_config_export(project_root: Path, menu_dir: Path, config_path: Optional[Path] = None) -> Path:
    project_root = Path(project_root).resolve()
    out = Path(config_path) if config_path else project_root / "config" / SCRIPTS_CONFIG_NAME
    rows = projects_config_discover_scripts(project_root, Path(menu_dir).resolve())
    if not any(r[1] == "cli" for r in rows):
        raise ProjectsConfigError(f"no CLI scripts found under {project_root / 'CLI'}")
    doc = {
        "Format": "BEJSON",
        "Format_Version": "104a",
        "Format_Creator": "Elton Boehnen",
        "Config_Name": "menu_scripts",
        "Exported_At": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "Paths_Relative_To": "project_root",
        "Records_Type": ["MenuScript"],
        "Fields": [dict(f) for f in SCRIPT_FIELDS],
        "Values": rows,
    }
    result = validate_bejson(doc)
    if not result.valid:
        raise ProjectsConfigError(f"exported config invalid: {'; '.join(result.errors)}")
    if not bejson_core_atomic_write(str(out), doc):
        raise ProjectsConfigError(f"could not write {out}")
    _log.info("exported %d script(s) to %s", len(rows), out)
    return out


# Loads the exported config as {script_id: {field: value}}.
def projects_config_load(config_path: Path) -> Dict[str, Dict[str, Any]]:
    path = Path(config_path)
    if not path.is_file():
        raise ProjectsConfigError(f"config not found: {path}")
    result = validate_bejson(str(path), is_file=True)
    if not result.valid:
        raise ProjectsConfigError(f"config invalid: {'; '.join(result.errors)}")
    doc = bejson_core_load_file(str(path)) or {}
    if doc.get("Records_Type") != ["MenuScript"]:
        raise ProjectsConfigError(f"{path} is not a menu script config")
    fm = bejson_core_get_field_map(doc)
    return {row[fm["script_id"]]: {name: row[idx] for name, idx in fm.items()} for row in doc["Values"]}


# Absolute script location from a config row, containment-checked against the project root.
def projects_config_resolve_script(project_root: Path, row: Dict[str, Any]) -> Path:
    try:
        script = Path(bejson_safe_join(str(project_root), row["script_path"]))
    except ValueError as exc:
        raise ProjectsConfigError(str(exc)) from exc
    if not script.is_file():
        raise ProjectsConfigError(f"script missing: {row['script_path']}")
    return script


# True when every configured script still exists at its recorded path (stale config -> re-export).
def projects_config_is_current(project_root: Path, scripts: Dict[str, Dict[str, Any]]) -> bool:
    try:
        for row in scripts.values():
            projects_config_resolve_script(project_root, row)
    except ProjectsConfigError:
        return False
    return True


# Standard config/config.json (BEJSON 104a ScriptConfig) created only when absent; never overwritten.
def projects_config_ensure_standard(config_dir: Path) -> Path:
    out = Path(config_dir) / STANDARD_CONFIG_NAME
    if out.exists():
        return out
    doc = {
        "Format": "BEJSON",
        "Format_Version": "104a",
        "Format_Creator": "Elton Boehnen",
        "Records_Type": ["ScriptConfig"],
        "Fields": [
            {"name": "setting_name", "type": "string"},
            # Policy template says "any", but the frozen Core validator accepts only string/integer/number/
            # boolean/array/object; every setting value here is a string, so "string" validates cleanly.
            {"name": "setting_value", "type": "string"},
            {"name": "description", "type": "string"},
        ],
        "Values": [
            ["local_lib_directory", "lib/", "Relative path to local dependency directory."],
            # Placeholder (resolved by lib_bejson_Core_bejson_env.resolve_path) instead of a hardcoded absolute path.
            ["master_lib_source", "{ADMIN_ROOT}/libraries", "Fallback if local lib is unpopulated."],
            ["log_level", "INFO", "Default logging module threshold."],
        ],
    }
    if not bejson_core_atomic_write(str(out), doc):
        raise ProjectsConfigError(f"could not write {out}")
    return out


def _header_bool(doc: Optional[Dict[str, Any]], origin: str) -> Optional[bool]:
    if not isinstance(doc, dict) or BINARY_KEY not in doc:
        return None
    if isinstance(doc[BINARY_KEY], bool):
        return doc[BINARY_KEY]
    _log.warning("%s: %s must be a boolean (got %r); ignored", origin, BINARY_KEY, doc[BINARY_KEY])
    return None


# Effective Binary_Enabled and where it came from. Precedence: explicit override (CLI) > the project's own manifest
# header (local override, only a tracker that exists in the target) > the external filter config's binary_enabled row
# (config/chunker_filters.json, passed as filter_switch) > the DEPRECATED Binary_Enabled header of config/config.json
# (still honoured, with a warning, for existing installs) > default True.
def projects_config_binary_enabled(config_file: Optional[Path], project_manifest: Optional[Dict[str, Any]] = None,
                                   override: Optional[bool] = None,
                                   filter_switch: Optional[bool] = None) -> Tuple[bool, str]:
    if override is not None:
        return bool(override), "command line"
    local = _header_bool(project_manifest, "project manifest")
    if local is not None:
        return local, "project manifest"
    if filter_switch is not None:
        return bool(filter_switch), "filter config"
    if config_file and Path(config_file).is_file():
        configured = _header_bool(bejson_core_load_file(str(config_file)), "chunker config")
        if configured is not None:
            _log.warning("%s: the Binary_Enabled header is deprecated; set binary_enabled in chunker_filters.json",
                         config_file)
            return configured, "chunker config (deprecated header)"
    return True, "default"
