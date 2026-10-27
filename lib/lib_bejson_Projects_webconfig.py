"""
File Name:       lib_bejson_Projects_webconfig.py
Relative Path:   lib/lib_bejson_Projects_webconfig.py
Family:          Projects
Description:     Settings of the Project Chunker web UI: config/web_config.json (BEJSON 104a rows setting_name /
                 setting_value / description; no state in headers). Exported on first run, never overwritten;
                 unreadable or invalid values fall back to the built-in defaults, per setting.
VERSION:         1.8.0
Author:          Elton Boehnen · boehnenelton2024@gmail.com · boehnenelton2024.pages.dev · github.com/boehnenelton
RELATIONAL_ID:   2a32ef34-c046-4814-980c-4019b8a8fa93
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from lib_bejson_Core_bejson_core import bejson_core_atomic_write, bejson_core_get_field_map, bejson_core_load_file
from lib_bejson_Core_bejson_validator import validate_bejson
from lib_bejson_Projects_logging import projects_logging_get_logger

VERSION = "1.8.0"
WEB_CONFIG_NAME = "web_config.json"
RECORDS_TYPE = "WebSetting"
FIELDS = [{"name": "setting_name", "type": "string"}, {"name": "setting_value", "type": "string"},
          {"name": "description", "type": "string"}]
# name -> (default, kind, description); kind: int | bool | text | choice:a|b
SETTINGS: Dict[str, Tuple[str, str, str]] = {
    "port_start": ("5120", "int", "First port tried; the next free port up to +19 is used."),
    "bind_host": ("127.0.0.1", "text", "Bind address. Anything other than 127.0.0.1 exposes the UI to the network."),
    "open_browser": ("false", "bool", "Open the default browser on start."),
    "theme": ("dark", "choice:dark|light", "Colour theme."),
    "max_file_mb": ("25", "int", "Default per-file size limit for Chunk (0 = no limit)."),
    "log_level": ("INFO", "choice:DEBUG|INFO|WARNING|ERROR", "Logging threshold."),
    "storage_root": ("", "text", "Chunker storage root override (empty = default / PROJECT_CHUNKER_STORAGE_ROOT)."),
    "picker_roots": ("", "text", "Extra folder-picker roots, comma-separated (placeholders like {INTERNAL_STORAGE} work)."),
    "job_journal_size": ("200", "int", "Finished jobs kept in the journal."),
}
_log = projects_logging_get_logger("webconfig")


def _valid(kind: str, value: str) -> bool:
    if kind == "int":
        return value.isdigit()
    if kind == "bool":
        return value in ("true", "false")
    if kind.startswith("choice:"):
        return value in kind.split(":", 1)[1].split("|")
    return True


def projects_webconfig_defaults() -> Dict[str, str]:
    return {k: v[0] for k, v in SETTINGS.items()}


def _write(path: Path, values: Dict[str, str]) -> bool:
    doc = {"Format": "BEJSON", "Format_Version": "104a", "Format_Creator": "Elton Boehnen",
           "Config_Name": "web_config", "Records_Type": [RECORDS_TYPE], "Fields": [dict(f) for f in FIELDS],
           "Values": [[k, values.get(k, SETTINGS[k][0]), SETTINGS[k][2]] for k in SETTINGS]}
    if not validate_bejson(doc).valid:
        return False
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    return bool(bejson_core_atomic_write(str(path), doc))


# Exports the defaults when the file is absent. Never overwrites. Returns the path.
def projects_webconfig_ensure(config_dir: Path) -> Path:
    out = Path(config_dir) / WEB_CONFIG_NAME
    if not out.exists():
        _write(out, projects_webconfig_defaults())
    return out


# Effective settings: defaults overlaid by the file; an invalid value for a setting falls back to its default.
def projects_webconfig_load(path: Optional[Path]) -> Dict[str, str]:
    values = projects_webconfig_defaults()
    if not path or not Path(path).is_file():
        return values
    if not validate_bejson(str(path), is_file=True).valid:
        _log.warning("%s invalid; using defaults", path)
        return values
    doc = bejson_core_load_file(str(path)) or {}
    if doc.get("Records_Type") != [RECORDS_TYPE]:
        return values
    fm = bejson_core_get_field_map(doc)
    if "setting_name" not in fm or "setting_value" not in fm:
        return values
    for row in doc.get("Values", []):
        name, value = str(row[fm["setting_name"]]).strip(), str(row[fm["setting_value"]]).strip()
        if name in SETTINGS:
            if _valid(SETTINGS[name][1], value):
                values[name] = value
            else:
                _log.warning("web setting %s=%r is invalid; default %r used", name, value, SETTINGS[name][0])
    return values


# Validates and saves a full or partial settings dict; returns the problem list (nothing written when non-empty).
def projects_webconfig_save(path: Path, updates: Dict[str, Any]) -> List[str]:
    values = projects_webconfig_load(path)
    problems = []
    for name, raw in updates.items():
        if name not in SETTINGS:
            problems.append(f"unknown setting {name}")
            continue
        value = "true" if raw is True else "false" if raw is False else str(raw).strip()
        if not _valid(SETTINGS[name][1], value):
            problems.append(f"{name}: invalid value {value!r}")
        else:
            values[name] = value
    if problems:
        return problems
    return [] if _write(Path(path), values) else [f"could not write {path}"]
