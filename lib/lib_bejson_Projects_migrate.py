"""
File Name:       lib_bejson_Projects_migrate.py
Relative Path:   lib/lib_bejson_Projects_migrate.py
Family:          Projects
Description:     Project DB migrator logic. Config (BEJSON 104a, Dry_Run defaults to true, exported automatically on
                 first use), source-root resolution from the environment, zip scan, a migration ledger (BEJSON 104a),
                 the chunk phase (every valid zip -> track + chunk, zip kept) and the verify phase (re-check zip,
                 registry and snapshot, optionally every file byte-for-byte against the zip, then delete / quarantine /
                 keep the original). Nothing here prompts for paths; front ends (Helper CLI/menu, Flask) call it.
VERSION:         1.8.0
Author:          Elton Boehnen · boehnenelton2024@gmail.com · boehnenelton2024.pages.dev · github.com/boehnenelton
RELATIONAL_ID:   d7c5eb70-63e7-4c2d-b456-545ad3a8b084
"""

import hashlib
import os
import shutil
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Dict, List, Optional, Tuple

from lib_bejson_Core_bejson_core import bejson_core_atomic_write, bejson_core_get_field_map
from lib_bejson_Projects_locks import ProjectsPathLock
from lib_bejson_Core_bejson_core import bejson_core_load_file
from lib_bejson_Core_bejson_env import resolve_path, source_env
from lib_bejson_Core_bejson_validator import validate_bejson
from lib_bejson_Projects_logging import projects_logging_get_logger
from lib_bejson_Projects_filters import FILTERS_CONFIG_NAME, projects_filters_load
from lib_bejson_Projects_paths import StorageLayout
from lib_bejson_Projects_pipeline import RunOptions, projects_pipeline_run_zip
from lib_bejson_Projects_registry import projects_registry_get
from lib_bejson_Projects_snapshot import projects_snapshot_iter_rows, projects_snapshot_load
from lib_bejson_Projects_tracker import IdentityOverride
from lib_bejson_Projects_zip import (
    ProjectsZipError,
    projects_zip_inspect,
    projects_zip_preview_identity,
    projects_zip_project_prefix,
    projects_zip_sha256,
)

VERSION = "1.8.0"
CONFIG_NAME = "migrator_config.json"
LEDGER_NAME = "ledger.bejson"
PROGRESS = Callable[[Dict[str, Any]], None]

# Boolean settings live in the document headers (like Binary_Enabled); the fail-safe default for anything unreadable
# is the SAFE value (Dry_Run true, everything else as listed).
BOOL_DEFAULTS: Dict[str, bool] = {
    "Dry_Run": True,
    "Recursive": True,
    "Include_Hidden": False,
    "Skip_Already_Chunked": True,
    "Binary_Enabled": True,
    "Deep_Verify": True,
    "Stop_On_First_Error": False,
    "Prompt_Project_Name": False,
}
BOOL_HELP = {
    "Dry_Run": "Scan and plan only; chunk and verify phases change nothing (no registry, snapshots, or zips).",
    "Recursive": "Search sub-folders of the source root for zips.",
    "Include_Hidden": "Include hidden folders and files (names starting with a dot).",
    "Skip_Already_Chunked": "Skip zips already chunked with an unchanged SHA-256.",
    "Binary_Enabled": "Store binary files in the chunks. If off, zips containing binaries are only partly captured and are never removed.",
    "Deep_Verify": "Verify phase compares every snapshot file to the zip's own bytes, not just hashes and headers.",
    "Stop_On_First_Error": "Stop a phase at the first failing zip instead of continuing.",
    "Prompt_Project_Name": "Prompt interactively to choose or edit the project name for each zip before chunking.",
}
RADIO_OPTIONS: Dict[str, Tuple[str, ...]] = {
    "Source_Mode": ("project_index_root", "custom"),
    "Original_Disposition": ("delete", "quarantine", "keep"),
    "Log_Level": ("DEBUG", "INFO", "WARNING", "ERROR"),
}
# 1.8.0: Original_Disposition now defaults to "keep". It used to default to "delete", so the first live run on a
# fresh config removed every verified original zip. Deleting or quarantining is now something you switch on.
RADIO_DEFAULTS = {"Source_Mode": "project_index_root", "Original_Disposition": "keep", "Log_Level": "INFO"}
RADIO_HELP = {
    "Source_Mode": "Where to look for zips: the PROJECT_INDEX_ROOT environment location, or a custom folder.",
    "Original_Disposition": "What the verify phase does with an original zip once its chunk is verified.",
    "Log_Level": "Logging threshold.",
}
TEXT_DEFAULTS = {"Custom_Source_Root": "", "Max_File_MB": "100"}
TEXT_HELP = {
    "Custom_Source_Root": "Used when Source_Mode is custom. Environment variables such as ${PROJECTS_ARCHIVE_ROOT} are resolved.",
    "Max_File_MB": "Files larger than this are not chunked (the zip is then kept as partial). 0 = no limit.",
}
# 1.8.0: no longer built on the deprecated ADMIN_ROOT (resolve_path silently fell back to <home>/Admin when it was unset).
DEFAULT_PROJECT_INDEX = "${ADMIN_DATA}/project_db"
ST_CHUNKED, ST_PARTIAL, ST_INVALID = "chunked", "partial", "invalid"
ST_CHUNK_FAILED, ST_VERIFIED, ST_VERIFY_FAILED = "chunk_failed", "verified", "verify_failed"
ST_DELETED, ST_QUARANTINED, ST_KEPT = "deleted", "quarantined", "kept"
VERIFIABLE = (ST_CHUNKED, ST_VERIFY_FAILED)
LEDGER_FIELDS = [{"name": n, "type": "string"} for n in (
    "Source_Root", "Zip_Rel", "Zip_SHA256", "Zip_Bytes", "Project_Name", "Versioned_UUID", "Snapshot_File",
    "Status", "Message", "Updated_At")]
REPORT_FIELDS = [{"name": n, "type": "string"} for n in ("Zip_Rel", "Phase", "Action", "Outcome", "Detail")]
_log = projects_logging_get_logger("migrate")


class ProjectsMigrateError(Exception):
    """Raised for unusable configuration or source root."""


@dataclass
class MigratorConfig:
    flags: Dict[str, bool] = field(default_factory=lambda: dict(BOOL_DEFAULTS))
    radios: Dict[str, str] = field(default_factory=lambda: dict(RADIO_DEFAULTS))
    texts: Dict[str, str] = field(default_factory=lambda: dict(TEXT_DEFAULTS))
    warnings: List[str] = field(default_factory=list)

    @property
    def dry_run(self) -> bool:
        return self.flags["Dry_Run"]

    @property
    def max_file_bytes(self) -> int:
        try:
            return int(float(self.texts["Max_File_MB"]) * 1024 * 1024)
        except ValueError:
            return int(float(TEXT_DEFAULTS["Max_File_MB"]) * 1024 * 1024)

    def as_dict(self) -> Dict[str, Any]:
        return {"flags": dict(self.flags), "radios": dict(self.radios), "texts": dict(self.texts)}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ── configuration ────────────────────────────────────────────────────────────────────────────────────────────
def projects_migrate_config_document(cfg: MigratorConfig) -> Dict[str, Any]:
    doc: Dict[str, Any] = {"Format": "BEJSON", "Format_Version": "104a", "Format_Creator": "Elton Boehnen"}
    doc.update({k: bool(cfg.flags[k]) for k in BOOL_DEFAULTS})
    doc["Records_Type"] = ["MigratorSetting"]
    doc["Fields"] = [{"name": "setting_name", "type": "string"}, {"name": "setting_value", "type": "string"},
                     {"name": "description", "type": "string"}]
    values = [[k, cfg.radios[k], RADIO_HELP[k] + " Options: " + ", ".join(RADIO_OPTIONS[k]) + "."] for k in RADIO_OPTIONS]
    values += [[k, cfg.texts[k], TEXT_HELP[k]] for k in TEXT_DEFAULTS]
    doc["Values"] = values
    return doc


# Validates a candidate config (flags bool, radios within options, Max_File_MB numeric >= 0); returns problem list.
def projects_migrate_config_problems(cfg: MigratorConfig) -> List[str]:
    problems = []
    for key in BOOL_DEFAULTS:
        if not isinstance(cfg.flags.get(key), bool):
            problems.append(f"{key} must be true or false")
    for key, options in RADIO_OPTIONS.items():
        if cfg.radios.get(key) not in options:
            problems.append(f"{key} must be one of {', '.join(options)}")
    try:
        if float(cfg.texts.get("Max_File_MB", "")) < 0:
            problems.append("Max_File_MB must be >= 0")
    except ValueError:
        problems.append("Max_File_MB must be a number")
    if cfg.radios.get("Source_Mode") == "custom" and not str(cfg.texts.get("Custom_Source_Root", "")).strip():
        problems.append("Custom_Source_Root is required when Source_Mode is custom")
    return problems


def projects_migrate_config_save(path: Path, cfg: MigratorConfig) -> None:
    problems = projects_migrate_config_problems(cfg)
    if problems:
        raise ProjectsMigrateError("; ".join(problems))
    doc = projects_migrate_config_document(cfg)
    result = validate_bejson(doc)
    if not result.valid:
        raise ProjectsMigrateError(f"config failed BEJSON validation: {'; '.join(result.errors)}")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    if not bejson_core_atomic_write(str(path), doc):
        raise ProjectsMigrateError(f"could not write {path}")


# Loads the config; anything unreadable or invalid falls back to the safe default for that key (with a warning).
def projects_migrate_config_load(path: Path) -> MigratorConfig:
    cfg = MigratorConfig()
    doc = bejson_core_load_file(str(path)) if Path(path).is_file() else None
    if not isinstance(doc, dict) or doc.get("Records_Type") != ["MigratorSetting"]:
        cfg.warnings.append(f"{path} unreadable or not a migrator config; using safe defaults")
        return cfg
    for key in BOOL_DEFAULTS:
        if key in doc:
            if isinstance(doc[key], bool):
                cfg.flags[key] = doc[key]
            else:
                cfg.warnings.append(f"{key} must be boolean (got {doc[key]!r}); using {BOOL_DEFAULTS[key]}")
    fm = bejson_core_get_field_map(doc)
    for row in doc.get("Values", []):
        name, value = str(row[fm["setting_name"]]).strip(), str(row[fm["setting_value"]]).strip()
        if name in RADIO_OPTIONS:
            if value in RADIO_OPTIONS[name]:
                cfg.radios[name] = value
            else:
                cfg.warnings.append(f"{name}={value!r} is not valid; using {RADIO_DEFAULTS[name]}")
        elif name in TEXT_DEFAULTS:
            cfg.texts[name] = value
    return cfg


# Loads the config, first writing the default one (Dry_Run true) when it does not exist. Returns (config, created).
def projects_migrate_config_ensure(path: Path) -> Tuple[MigratorConfig, bool]:
    path = Path(path)
    if path.is_file():
        return projects_migrate_config_load(path), False
    cfg = MigratorConfig()
    projects_migrate_config_save(path, cfg)
    _log.info("exported default migrator config to %s (Dry_Run=true)", path)
    return cfg, True


# ── source root ──────────────────────────────────────────────────────────────────────────────────────────────
# project_index_root: $PROJECT_INDEX_ROOT (loaded from the environment store when set there) else the
# ${ADMIN_DATA}/project_db; an unresolved variable is an error, never a guessed path. custom: Custom_Source_Root with placeholders resolved. Never prompts.
def projects_migrate_resolve_source(cfg: MigratorConfig) -> Path:
    if cfg.radios["Source_Mode"] == "custom":
        raw = cfg.texts["Custom_Source_Root"].strip()
        if not raw:
            raise ProjectsMigrateError("Source_Mode is custom but Custom_Source_Root is empty")
        return Path(resolve_path(raw))
    try:
        source_env()
    except Exception as exc:                            # environment store is optional
        _log.debug("environment store not loaded: %s", exc)
    resolved = resolve_path(os.environ.get("PROJECT_INDEX_ROOT") or DEFAULT_PROJECT_INDEX)
    if "${" in resolved or "{" in resolved:                 # an unset variable is left in the text by expandvars
        raise ProjectsMigrateError(
            f"cannot resolve the source folder ({resolved}): set PROJECT_INDEX_ROOT or ADMIN_DATA in the "
            "environment, or switch Source_Mode to custom and set Custom_Source_Root")
    return Path(resolved)


# ── scan ─────────────────────────────────────────────────────────────────────────────────────────────────────
@dataclass
class ZipCandidate:
    path: Path
    rel: str
    size: int
    valid: bool
    error: str = ""
    files: int = 0
    ledger_status: str = ""
    locked: bool = False


def _skip_dir(name: str, include_hidden: bool) -> bool:
    return (name.startswith(".chunker_stage_") or name.endswith(".lockdir") or name in ("__pycache__", "_to_delete")
            or (name.startswith(".") and not include_hidden))


# Finds *.zip under source_root (no symlinks), inspects each (magic, central directory, CRC test, safety checks) and
# attaches its ledger status. layout's storage root and the migration folder are never scanned.
def projects_migrate_scan(cfg: MigratorConfig, source_root: Path, layout: StorageLayout,
                          ledger: Optional[Dict[str, Dict[str, str]]] = None,
                          progress: Optional[PROGRESS] = None) -> List[ZipCandidate]:
    source_root = Path(source_root)
    if not source_root.is_dir():
        raise ProjectsMigrateError(f"source root does not exist or is not a folder: {source_root}")
    ledger = ledger or {}
    skip_roots = {layout.storage_root.resolve(), migration_root(layout).resolve()}
    found: List[ZipCandidate] = []
    for root, dirs, files in os.walk(source_root, followlinks=False):
        root_path = Path(root).resolve()
        dirs[:] = sorted(d for d in dirs if not _skip_dir(d, cfg.flags["Include_Hidden"])
                         and (root_path / d).resolve() not in skip_roots and not (root_path / d).is_symlink())
        if not cfg.flags["Recursive"]:
            dirs[:] = []
        for name in sorted(files):
            path = root_path / name
            if not name.lower().endswith(".zip") or path.is_symlink() or \
                    (name.startswith(".") and not cfg.flags["Include_Hidden"]):
                continue
            rel = path.relative_to(source_root.resolve()).as_posix()
            cand = ZipCandidate(path=path, rel=rel, size=path.stat().st_size, valid=False,
                                locked=Path(f"{path}.lockdir").exists())
            try:
                cand.files = sum(1 for i in projects_zip_inspect(path) if not i.is_dir())
                cand.valid = cand.files > 0
                cand.error = "" if cand.valid else "zip contains no files"
            except ProjectsZipError as exc:
                cand.error = str(exc)
            cand.ledger_status = ledger.get(ledger_key(source_root, rel), {}).get("Status", "")
            found.append(cand)
            if progress:
                progress({"event": "scan", "zip": rel, "valid": cand.valid})
    return found


# ── ledger ───────────────────────────────────────────────────────────────────────────────────────────────────
def migration_root(layout: StorageLayout) -> Path:
    return layout.storage_root.parent / "migration"


def ledger_path(layout: StorageLayout) -> Path:
    return migration_root(layout) / LEDGER_NAME


def ledger_key(source_root: Path, rel: str) -> str:
    return f"{Path(source_root).resolve()}|{rel}"


def projects_migrate_ledger_load(layout: StorageLayout) -> Dict[str, Dict[str, str]]:
    path = ledger_path(layout)
    doc = bejson_core_load_file(str(path)) if path.is_file() else None
    if not doc:
        return {}
    fm = bejson_core_get_field_map(doc)
    rows = {}
    for row in doc.get("Values", []):
        rec = {name: ("" if row[idx] is None else str(row[idx])) for name, idx in fm.items()}
        rows[f"{rec['Source_Root']}|{rec['Zip_Rel']}"] = rec
    return rows


def projects_migrate_ledger_update(layout: StorageLayout, source_root: Path, rel: str, **columns: Any) -> None:
    path = ledger_path(layout)
    path.parent.mkdir(parents=True, exist_ok=True)
    with ProjectsPathLock(path):
        rows = projects_migrate_ledger_load(layout)
        key = ledger_key(source_root, rel)
        rec = rows.get(key) or {n["name"]: "" for n in LEDGER_FIELDS}
        rec.update({"Source_Root": str(Path(source_root).resolve()), "Zip_Rel": rel})
        rec.update({k: str(v) for k, v in columns.items()})
        rec["Updated_At"] = _now()
        rows[key] = rec
        doc = {"Format": "BEJSON", "Format_Version": "104a", "Format_Creator": "Elton Boehnen",
               "Ledger_Version": "1", "Records_Type": ["MigrationEntry"], "Fields": [dict(f) for f in LEDGER_FIELDS],
               "Values": [[r[f["name"]] for f in LEDGER_FIELDS] for _k, r in sorted(rows.items())]}
        result = validate_bejson(doc)
        if not result.valid or not bejson_core_atomic_write(str(path), doc):
            raise ProjectsMigrateError(f"could not write ledger: {'; '.join(result.errors) or path}")


# ── report ───────────────────────────────────────────────────────────────────────────────────────────────────
@dataclass
class PhaseResult:
    phase: str
    dry_run: bool
    source_root: Path
    entries: List[Dict[str, str]] = field(default_factory=list)
    counts: Dict[str, int] = field(default_factory=dict)
    report_file: Optional[Path] = None
    stopped_early: bool = False

    def add(self, rel: str, action: str, outcome: str, detail: str = "") -> None:
        self.entries.append({"Zip_Rel": rel, "Phase": self.phase, "Action": action, "Outcome": outcome, "Detail": detail})
        self.counts[outcome] = self.counts.get(outcome, 0) + 1


def _write_report(layout: StorageLayout, result: PhaseResult) -> None:
    folder = migration_root(layout) / "reports"
    folder.mkdir(parents=True, exist_ok=True)
    doc = {"Format": "BEJSON", "Format_Version": "104a", "Format_Creator": "Elton Boehnen",
           "Phase": result.phase, "Dry_Run": result.dry_run, "Source_Root": str(result.source_root),
           "Generated_At": _now(), "Counts": ", ".join(f"{k}={v}" for k, v in sorted(result.counts.items())),
           "Records_Type": ["MigrationReportEntry"], "Fields": [dict(f) for f in REPORT_FIELDS],
           "Values": [[e[f["name"]] for f in REPORT_FIELDS] for e in result.entries]}
    check = validate_bejson(doc)
    if not check.valid:
        raise ProjectsMigrateError(f"report failed BEJSON validation: {'; '.join(check.errors)}")
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    out = folder / f"report-{result.phase}-{stamp}-{uuid.uuid4().hex[:6]}.bejson"      # never overwrite a prior report
    if not bejson_core_atomic_write(str(out), doc):
        raise ProjectsMigrateError(f"could not write report {out}")
    result.report_file = out


def _emit(progress: Optional[PROGRESS], **event: Any) -> None:
    if progress:
        progress(event)


# ── phase 1: chunk ───────────────────────────────────────────────────────────────────────────────────────────
# Dry run: scan and plan, write only the report. Live: every valid, not-yet-chunked zip goes through the zip intake
# pipeline with keep_zip (the zip is never touched here); outcome chunked | partial | invalid | chunk_failed | skipped.
def projects_migrate_chunk_phase(layout: StorageLayout, cfg: MigratorConfig, config_file: Optional[Path] = None,
                                 progress: Optional[PROGRESS] = None,
                                 cancelled: Callable[[], bool] = lambda: False,
                                 filters_file: Optional[Path] = None,
                                 prompt_name_callback: Optional[Callable[[ZipCandidate, str, str, str], Optional[str]]] = None) -> PhaseResult:
    filters = projects_filters_load(filters_file or Path(__file__).resolve().parent.parent / "config" / FILTERS_CONFIG_NAME)
    source = projects_migrate_resolve_source(cfg)
    ledger = projects_migrate_ledger_load(layout)
    result = PhaseResult("chunk", cfg.dry_run, source)
    candidates = projects_migrate_scan(cfg, source, layout, ledger, progress)
    for number, cand in enumerate(candidates, start=1):
        if cancelled():
            result.stopped_early = True
            break
        _emit(progress, event="chunk", zip=cand.rel, index=number, total=len(candidates))
        row = ledger.get(ledger_key(source, cand.rel), {})
        if not cand.valid:
            result.add(cand.rel, "flag", ST_INVALID, cand.error)
            if not cfg.dry_run:
                projects_migrate_ledger_update(layout, source, cand.rel, Status=ST_INVALID, Message=cand.error,
                                               Zip_Bytes=cand.size)
            if cfg.flags["Stop_On_First_Error"]:
                result.stopped_early = True
                break
            continue
        if cand.locked:
            result.add(cand.rel, "skip", "skipped", "zip is locked by another process")
            continue
        if cfg.flags["Skip_Already_Chunked"] and row.get("Status") in (ST_CHUNKED, ST_VERIFIED, ST_DELETED,
                                                                        ST_QUARANTINED, ST_KEPT):
            if row.get("Zip_SHA256") and projects_zip_sha256(cand.path) == row["Zip_SHA256"]:
                result.add(cand.rel, "skip", "skipped", f"already {row['Status']}")
                continue
        if cfg.dry_run:
            result.add(cand.rel, "would chunk", "planned", f"{cand.files} file(s), {cand.size} bytes")
            continue
        target_identity = None
        if prompt_name_callback is not None or cfg.flags.get("Prompt_Project_Name"):
            detected_project_name, detected_ver, detected_pkg = projects_zip_preview_identity(cand.path)
            if prompt_name_callback is not None:
                chosen_project_name = prompt_name_callback(cand, detected_project_name, detected_ver, detected_pkg)
            else:
                import sys
                sys.stdout.write(f"\n[Zip {number}/{len(candidates)}] {cand.rel}\n")
                sys.stdout.write(f"  Detected Project Name: {detected_project_name} (V{detected_ver}-PKG{detected_pkg})\n")
                try:
                    prompt_input = input(f"  Select project name [Enter = accept '{detected_project_name}', 's' = skip, 'q' = quit]: ").strip()
                except (EOFError, KeyboardInterrupt):
                    prompt_input = "q"
                if prompt_input.lower() in ("q", "quit"):
                    chosen_project_name = "STOP"
                elif prompt_input.lower() in ("s", "skip"):
                    chosen_project_name = "SKIP"
                elif prompt_input:
                    chosen_project_name = prompt_input
                else:
                    chosen_project_name = detected_project_name

            if chosen_project_name == "SKIP":
                result.add(cand.rel, "skip", "skipped", "skipped by user selection")
                continue
            elif chosen_project_name == "STOP":
                result.stopped_early = True
                break
            elif chosen_project_name:
                target_identity = IdentityOverride(name=chosen_project_name)
        try:
            zres = projects_pipeline_run_zip(layout, RunOptions(
                target=cand.path, track=True, chunk=True, scoop=True, keep_zip=True,
                identity=target_identity,
                exclude_dirs=filters.dir_patterns if filters.apply_to_zip else (),
                exclude_files=filters.file_patterns if filters.apply_to_zip else (),
                binary_enabled=cfg.flags["Binary_Enabled"], config_file=config_file,
                max_file_bytes=cfg.max_file_bytes))
        except Exception as exc:                        # per-zip isolation: record and carry on
            _log.error("chunk failed for %s: %s", cand.rel, exc)
            result.add(cand.rel, "chunk", ST_CHUNK_FAILED, str(exc))
            projects_migrate_ledger_update(layout, source, cand.rel, Status=ST_CHUNK_FAILED, Message=str(exc),
                                           Zip_Bytes=cand.size)
            if cfg.flags["Stop_On_First_Error"]:
                result.stopped_early = True
                break
            continue
        run = zres.run
        status = ST_CHUNKED if zres.fully_captured else ST_PARTIAL
        message = "" if zres.fully_captured else zres.zip_kept_reason
        result.add(cand.rel, "chunk", status, message or f"{run.stats.files} file(s) -> {run.info.versioned_uuid}")
        projects_migrate_ledger_update(
            layout, source, cand.rel, Zip_SHA256=zres.sha256, Zip_Bytes=cand.size, Project_Name=run.info.project_name,
            Versioned_UUID=run.info.versioned_uuid, Snapshot_File=layout.relative_ref(run.snapshot_file),
            Status=status, Message=message)
    _write_report(layout, result)
    return result


# ── phase 2: verify + remove originals ───────────────────────────────────────────────────────────────────────
# Returns a list of problems; empty means the chunk provably contains the zip's files. Checks: zip present and SHA-256
# equal to the ledger's, registry entry present, snapshot loads/validates and carries the same Source_Zip_SHA256, and
# (deep) every snapshot file equals the zip entry's bytes with identical file sets.
def projects_migrate_verify_one(layout: StorageLayout, zip_path: Path, row: Dict[str, str], deep: bool) -> List[str]:
    problems: List[str] = []
    if not zip_path.is_file():
        return [f"zip missing: {zip_path}"]
    sha = projects_zip_sha256(zip_path)
    if row.get("Zip_SHA256") != sha:
        return ["zip changed since it was chunked (SHA-256 differs)"]
    try:
        entry = projects_registry_get(layout, row.get("Versioned_UUID", ""))
    except Exception as exc:
        return [f"registry entry not found: {exc}"]
    if entry.get("Status") not in ("archived", "restored", "chunked"):
        problems.append(f"registry status is {entry.get('Status')!r}")
    snapshot_file = layout.resolve_ref(row.get("Snapshot_File", "")) if row.get("Snapshot_File") else None
    if snapshot_file is None or not snapshot_file.is_file():
        return problems + ["snapshot file missing"]
    try:
        doc = projects_snapshot_load(snapshot_file)
    except Exception as exc:
        return problems + [f"snapshot invalid: {exc}"]
    if doc.get("Source_Zip_SHA256") != sha:
        problems.append("snapshot was not made from this zip (Source_Zip_SHA256 differs)")
    if doc.get("Versioned_UUID") != row.get("Versioned_UUID"):
        problems.append("snapshot UUID differs from ledger")
    rows, bad_rows, snap_dirs = {}, [], set()
    for snap_row, error in projects_snapshot_iter_rows(doc):
        if error:
            bad_rows.append(f"{snap_row.relative_path}: {error}")
            continue
        if snap_row.is_dir:
            snap_dirs.add(snap_row.relative_path)
            continue
        if hashlib.sha256(snap_row.raw).hexdigest() != snap_row.expected_hash:
            bad_rows.append(f"{snap_row.relative_path}: stored hash does not match its content")
        rows[snap_row.relative_path] = snap_row
    problems += bad_rows[:5]
    if deep and not problems:
        try:
            with zipfile.ZipFile(zip_path) as archive:
                infos = archive.infolist()
                prefix = projects_zip_project_prefix(infos)
                expected, zip_dirs = {}, set()
                for info in infos:
                    name = info.filename.replace("\\", "/").strip("/")
                    name = name[len(prefix) + 1:] if prefix and name.startswith(prefix + "/") else name
                    if info.is_dir():
                        if name and name != prefix:
                            zip_dirs.add(name)
                        continue
                    expected[name] = info
                # Empty directories = directory entries with no descendant entry (files or directories).
                ancestors = set()
                for entry in set(expected) | zip_dirs:
                    ancestors.update(str(p) for p in PurePosixPath(entry).parents)
                zip_empty = zip_dirs - ancestors
                for name in sorted(zip_empty - snap_dirs):
                    problems.append(f"empty directory in zip but not in chunk: {name}")
                for name in sorted(snap_dirs - zip_empty):
                    problems.append(f"empty directory in chunk but not in zip: {name}")
                for name in sorted(set(expected) - set(rows)):
                    problems.append(f"in zip but not in chunk: {name}")
                for name in sorted(set(rows) - set(expected)):
                    problems.append(f"in chunk but not in zip: {name}")
                for name, info in sorted(expected.items()):
                    if name in rows and archive.read(info) != rows[name].raw:
                        problems.append(f"content differs from zip: {name}")
        except (zipfile.BadZipFile, OSError) as exc:
            problems.append(f"zip unreadable during deep verify: {exc}")
    return problems[:10]


def _dispose(layout: StorageLayout, zip_path: Path, sha: str, disposition: str) -> str:
    if disposition == "keep":
        return ST_KEPT
    if disposition == "quarantine":
        target_dir = migration_root(layout) / "quarantine"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{time.strftime('%Y%m%d-%H%M%S', time.gmtime())}-{sha[:8]}-{zip_path.name}"
        shutil.move(str(zip_path), str(target))
        if projects_zip_sha256(target) != sha:
            raise ProjectsMigrateError(f"quarantined copy failed SHA-256 check: {target}")
        return ST_QUARANTINED
    zip_path.unlink()
    return ST_DELETED


# Verifies every chunked ledger entry; applies Original_Disposition only for entries that verify, only when Dry_Run is
# off, and only to zips that still sit inside the source root.
def projects_migrate_verify_phase(layout: StorageLayout, cfg: MigratorConfig, progress: Optional[PROGRESS] = None,
                                  cancelled: Callable[[], bool] = lambda: False) -> PhaseResult:
    source = projects_migrate_resolve_source(cfg)
    ledger = projects_migrate_ledger_load(layout)
    result = PhaseResult("verify", cfg.dry_run, source)
    targets = [r for k, r in sorted(ledger.items()) if r["Source_Root"] == str(source.resolve())
               and r["Status"] in VERIFIABLE]
    for number, row in enumerate(targets, start=1):
        if cancelled():
            result.stopped_early = True
            break
        rel = row["Zip_Rel"]
        _emit(progress, event="verify", zip=rel, index=number, total=len(targets))
        zip_path = (source.resolve() / rel)
        try:
            zip_path.resolve().relative_to(source.resolve())
        except ValueError:
            result.add(rel, "verify", ST_VERIFY_FAILED, "path escapes the source root")
            continue
        problems = projects_migrate_verify_one(layout, zip_path, row, cfg.flags["Deep_Verify"])
        if problems:
            result.add(rel, "verify", ST_VERIFY_FAILED, "; ".join(problems[:3]))
            if not cfg.dry_run:
                projects_migrate_ledger_update(layout, source, rel, Status=ST_VERIFY_FAILED, Message="; ".join(problems[:3]))
            if cfg.flags["Stop_On_First_Error"]:
                result.stopped_early = True
                break
            continue
        disposition = cfg.radios["Original_Disposition"]
        if cfg.dry_run:
            result.add(rel, f"would {disposition}", "planned", "chunk verified" + (" byte-for-byte" if cfg.flags["Deep_Verify"] else ""))
            continue
        try:
            status = _dispose(layout, zip_path, row["Zip_SHA256"], disposition)
        except Exception as exc:
            result.add(rel, disposition, ST_VERIFY_FAILED, f"could not {disposition}: {exc}")
            projects_migrate_ledger_update(layout, source, rel, Status=ST_VERIFY_FAILED, Message=str(exc))
            continue
        projects_migrate_ledger_update(layout, source, rel, Status=status, Message="verified")
        result.add(rel, disposition, status, "verified")
    _write_report(layout, result)
    return result
