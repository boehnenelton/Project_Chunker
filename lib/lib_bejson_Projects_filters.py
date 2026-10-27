"""
File Name:       lib_bejson_Projects_filters.py
Relative Path:   lib/lib_bejson_Projects_filters.py
Family:          Projects
Description:     External chunking-filter configuration. config/chunker_filters.json (BEJSON 104a) holds one row per
                 switch or filter: setting_id, group (switch | dirs | files), enabled (boolean), patterns
                 (comma-separated, fnmatch globs) and description. No state lives in schema headers. Defaults skip
                 git, Node.js, Python caches, virtualenvs, build output (dist ...), the chunker's own chunk output,
                 temp files and OS junk. The file is exported once and never overwritten; anything unreadable or
                 invalid falls back to the built-in defaults (fail-safe: a bad file never disables filtering silently).
VERSION:         1.8.0
Author:          Elton Boehnen · boehnenelton2024@gmail.com · boehnenelton2024.pages.dev · github.com/boehnenelton
RELATIONAL_ID:   70baecad-fc7f-4117-81ee-435cc65e0364
"""

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from lib_bejson_Core_bejson_core import (
    bejson_core_atomic_write,
    bejson_core_get_field_map,
    bejson_core_load_file,
)
from lib_bejson_Core_bejson_validator import validate_bejson
from lib_bejson_Projects_logging import projects_logging_get_logger

VERSION = "1.8.0"
FILTERS_CONFIG_NAME = "chunker_filters.json"
RECORDS_TYPE = "ChunkerSetting"
GROUP_SWITCH, GROUP_DIRS, GROUP_FILES = "switch", "dirs", "files"
SWITCH_BINARY = "binary_enabled"
SWITCH_ZIP = "apply_to_zip"
FIELDS = [
    {"name": "setting_id", "type": "string"},
    {"name": "group", "type": "string"},
    {"name": "enabled", "type": "boolean"},
    {"name": "patterns", "type": "string"},
    {"name": "description", "type": "string"},
]

# (setting_id, group, enabled, patterns, description)
DEFAULT_SETTINGS: List[Tuple[str, str, bool, str, str]] = [
    (SWITCH_BINARY, GROUP_SWITCH, True, "", "Store binary files (base64 + Is_Binary flag). Off: binary files are ignored and reported."),
    (SWITCH_ZIP, GROUP_SWITCH, False, "", "Apply the filters below to zip intake and the migrator. Off: zips are captured in full so they can be deleted safely; on: filtered content is lost and the zip is kept."),
    ("git", GROUP_DIRS, True, ".git", "Git repository data."),
    ("nodejs", GROUP_DIRS, True, "node_modules,bower_components,.npm,.yarn,.pnpm-store", "Node.js dependency trees."),
    ("python_cache", GROUP_DIRS, True, "__pycache__,.pytest_cache,.mypy_cache,.ruff_cache,.tox,*.egg-info", "Python caches and metadata."),
    ("virtualenv", GROUP_DIRS, True, ".venv,venv", "Python virtual environments."),
    ("build_output", GROUP_DIRS, True, "dist,build,out,.next,.nuxt,.turbo,.parcel-cache,coverage", "Generated build output."),
    ("chunk_output", GROUP_DIRS, True, ".mfdb_lock,project_snapshots,.chunker_stage_*", "The chunker's own storage, locks and staging."),
    ("ide", GROUP_DIRS, False, ".idea,.vscode", "Editor settings folders."),
    ("chunk_files", GROUP_FILES, True, "*.chunk.bejson,*.chunked.bejson,*.mfdb132.bejson", "Chunk files produced by chunking."),
    ("os_junk", GROUP_FILES, True, ".DS_Store,Thumbs.db,desktop.ini", "Operating-system metadata files."),
    ("temp_files", GROUP_FILES, True, "*.tmp,*.swp,*.swo,*~,*.pyc,*.pyo", "Temporary and compiled-bytecode files."),
    ("logs", GROUP_FILES, False, "*.log", "Log files."),
    ("lockfiles", GROUP_FILES, False, "package-lock.json,yarn.lock,pnpm-lock.yaml", "Dependency lockfiles (kept by default: they pin versions)."),
    ("git_meta", GROUP_FILES, False, ".gitignore,.gitattributes,.gitmodules", "Git configuration files that live in the working tree (kept by default)."),
]
_log = projects_logging_get_logger("filters")


class ProjectsFiltersError(Exception):
    """Raised for an invalid per-run override (unknown filter id)."""


@dataclass
class FilterSettings:
    dir_patterns: Tuple[str, ...] = ()
    file_patterns: Tuple[str, ...] = ()
    active: List[str] = field(default_factory=list)          # ids of enabled filters
    inactive: List[str] = field(default_factory=list)
    binary_enabled: Optional[bool] = None                    # config switch; None = not set in the file
    apply_to_zip: bool = False
    source: str = "defaults"                                 # "defaults" | path of the loaded file
    all_disabled: bool = False


def projects_filters_split(patterns: str) -> List[str]:
    return [p.strip() for p in str(patterns or "").split(",") if p.strip()]


def _defaults() -> List[Dict[str, Any]]:
    return [{"setting_id": i, "group": g, "enabled": e, "patterns": p, "description": d}
            for i, g, e, p, d in DEFAULT_SETTINGS]


# Writes the default file when absent. Never overwrites. Returns the path.
def projects_filters_ensure(config_dir: Path) -> Path:
    out = Path(config_dir) / FILTERS_CONFIG_NAME
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "Format": "BEJSON", "Format_Version": "104a", "Format_Creator": "Elton Boehnen",
        "Config_Name": "chunker_filters",
        "Records_Type": [RECORDS_TYPE],
        "Fields": [dict(f) for f in FIELDS],
        "Values": [[i, g, e, p, d] for i, g, e, p, d in DEFAULT_SETTINGS],
    }
    result = validate_bejson(doc)
    if not result.valid:
        raise ProjectsFiltersError(f"default filter config invalid: {'; '.join(result.errors)}")
    if not bejson_core_atomic_write(str(out), doc):
        raise ProjectsFiltersError(f"could not write {out}")
    _log.info("exported default filter config to %s", out)
    return out


def _read_rows(path: Path) -> Optional[List[Dict[str, Any]]]:
    result = validate_bejson(str(path), is_file=True)
    if not result.valid:
        _log.warning("%s invalid (%s); using built-in defaults", path, "; ".join(result.errors[:2]))
        return None
    doc = bejson_core_load_file(str(path)) or {}
    if doc.get("Records_Type") != [RECORDS_TYPE]:
        _log.warning("%s is not a %s file; using built-in defaults", path, RECORDS_TYPE)
        return None
    fm = bejson_core_get_field_map(doc)
    if any(n not in fm for n in ("setting_id", "group", "enabled", "patterns")):
        _log.warning("%s lacks required columns; using built-in defaults", path)
        return None
    rows = []
    for raw in doc.get("Values", []):
        rows.append({"setting_id": str(raw[fm["setting_id"]]).strip(), "group": str(raw[fm["group"]]).strip().lower(),
                     "enabled": raw[fm["enabled"]], "patterns": raw[fm["patterns"]]})
    return rows


# Loads the effective filter set. config_file None/missing/invalid -> built-in defaults. Per-run overrides:
# disable_all switches every dirs/files filter off; enable / disable name filter ids (unknown id -> error).
# A row whose "enabled" is not a boolean is ignored with a warning (its built-in default applies when it has one).
def projects_filters_load(config_file: Optional[Path] = None, disable_all: bool = False,
                          enable: Iterable[str] = (), disable: Iterable[str] = ()) -> FilterSettings:
    rows, source = None, "defaults"
    if config_file and Path(config_file).is_file():
        rows = _read_rows(Path(config_file))
        if rows is not None:
            source = str(config_file)
    if rows is None:
        rows = _defaults()
    builtin_enabled = {i: e for i, _g, e, _p, _d in DEFAULT_SETTINGS}
    settings = FilterSettings(source=source, all_disabled=disable_all)
    known = {r["setting_id"] for r in rows if r["group"] in (GROUP_DIRS, GROUP_FILES)}
    for wanted in list(enable) + list(disable):
        if wanted not in known:
            raise ProjectsFiltersError(f"unknown filter {wanted!r}; known: {', '.join(sorted(known))}")
    dirs: List[str] = []
    files: List[str] = []
    for row in rows:
        sid, group, enabled = row["setting_id"], row["group"], row["enabled"]
        if not isinstance(enabled, bool):
            _log.warning("filter %s: 'enabled' must be a boolean (got %r); ignored", sid, enabled)
            if sid not in builtin_enabled:
                continue
            enabled = builtin_enabled[sid]
        if group == GROUP_SWITCH:
            if sid == SWITCH_BINARY:
                settings.binary_enabled = enabled
            elif sid == SWITCH_ZIP:
                settings.apply_to_zip = enabled
            continue
        if group not in (GROUP_DIRS, GROUP_FILES):
            _log.warning("filter %s: unknown group %r; ignored", sid, group)
            continue
        if sid in set(enable):
            enabled = True
        if sid in set(disable) or disable_all:
            enabled = False
        (settings.active if enabled else settings.inactive).append(sid)
        if enabled:
            (dirs if group == GROUP_DIRS else files).extend(projects_filters_split(row["patterns"]))
    settings.dir_patterns = tuple(dict.fromkeys(dirs))
    settings.file_patterns = tuple(dict.fromkeys(files))
    return settings


# fnmatch (case-sensitive) of a name against patterns; patterns containing "/" are matched against the relative path.
def projects_filters_match(name: str, rel_path: str, patterns: Iterable[str]) -> bool:
    for pattern in patterns:
        target = rel_path if "/" in pattern else name
        if target == pattern or fnmatch.fnmatchcase(target, pattern):
            return True
    return False


BUILTIN_IDS = tuple(i for i, _g, _e, _p, _d in DEFAULT_SETTINGS)


# Rows of the filter config for editing: [{setting_id, group, enabled, patterns, description, builtin}]. A missing or
# invalid file yields the built-in defaults (exactly what projects_filters_load would apply).
def projects_filters_read_rows(config_file: Optional[Path] = None) -> List[Dict[str, Any]]:
    rows = None
    if config_file and Path(config_file).is_file():
        result = validate_bejson(str(config_file), is_file=True)
        doc = bejson_core_load_file(str(config_file)) if result.valid else None
        if doc and doc.get("Records_Type") == [RECORDS_TYPE]:
            fm = bejson_core_get_field_map(doc)
            if all(n in fm for n in ("setting_id", "group", "enabled", "patterns")):
                rows = [{"setting_id": str(r[fm["setting_id"]]).strip(), "group": str(r[fm["group"]]).strip().lower(),
                         "enabled": r[fm["enabled"]], "patterns": str(r[fm["patterns"]] or "").strip(),
                         "description": str(r[fm["description"]]) if "description" in fm else ""}
                        for r in doc.get("Values", [])]
    if rows is None:
        rows = _defaults()
    for row in rows:
        row["builtin"] = row["setting_id"] in BUILTIN_IDS
    return rows


# Validates and atomically writes the rows (same layout as the exported default). Rules: unique non-empty ids,
# group in switch/dirs/files, boolean enabled, every built-in id still present (built-ins can be switched off, not
# removed), the two switches are group "switch". Returns the problem list; nothing is written when it is non-empty.
def projects_filters_write_rows(config_file: Path, rows: List[Dict[str, Any]]) -> List[str]:
    problems: List[str] = []
    seen = set()
    clean: List[List[Any]] = []
    for row in rows:
        sid = str(row.get("setting_id", "")).strip()
        group = str(row.get("group", "")).strip().lower()
        if not sid or any(c in sid for c in ' ,/\\"\''):
            problems.append(f"invalid setting id {sid!r}")
            continue
        if sid in seen:
            problems.append(f"duplicate setting id {sid}")
            continue
        seen.add(sid)
        if group not in (GROUP_SWITCH, GROUP_DIRS, GROUP_FILES):
            problems.append(f"{sid}: group must be switch, dirs or files")
        if not isinstance(row.get("enabled"), bool):
            problems.append(f"{sid}: enabled must be true or false")
        if sid in (SWITCH_BINARY, SWITCH_ZIP) and group != GROUP_SWITCH:
            problems.append(f"{sid}: must stay in group switch")
        patterns = ", ".join(projects_filters_split(row.get("patterns", "")))
        if group in (GROUP_DIRS, GROUP_FILES) and not patterns:
            problems.append(f"{sid}: needs at least one pattern")
        clean.append([sid, group, row.get("enabled"), "" if group == GROUP_SWITCH else patterns.replace(", ", ","),
                      str(row.get("description", ""))])
    for missing in (set(BUILTIN_IDS) - seen):
        problems.append(f"built-in setting {missing} cannot be removed (switch it off instead)")
    if problems:
        return problems
    doc = {"Format": "BEJSON", "Format_Version": "104a", "Format_Creator": "Elton Boehnen",
           "Config_Name": "chunker_filters", "Records_Type": [RECORDS_TYPE],
           "Fields": [dict(f) for f in FIELDS], "Values": clean}
    result = validate_bejson(doc)
    if not result.valid:
        return [f"config failed BEJSON validation: {'; '.join(result.errors)}"]
    Path(config_file).parent.mkdir(parents=True, exist_ok=True)
    if not bejson_core_atomic_write(str(config_file), doc):
        return [f"could not write {config_file}"]
    return []
