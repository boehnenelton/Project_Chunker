#!/usr/bin/env python3
"""
File Name:       project_chunker.py
Relative Path:   CLI/project_chunker.py
Description:     Project Chunker & Tracker CLI. Thin front end: parses flags, wires logging, calls the
                 lib_bejson_Projects family and reports results. Dual-MFDB-style storage: tracker database,
                 master registry, per-project snapshot chunks. All logic lives in lib/.
                 Modes: --track, --chunk, or both; --scoop (chunk, re-verify, then remove the source files);
                 --restore (deploy a version to its original or another location); --list, --versions, --verify.
VERSION:         1.8.0
Author:          Elton Boehnen · boehnenelton2024@gmail.com · boehnenelton2024.pages.dev · github.com/boehnenelton
RELATIONAL_ID:   97a401bf-03f3-4ff7-a327-4defc16c6a38
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

if sys.version_info < (3, 10):
    sys.stderr.write("project_chunker requires Python 3.10+\n")
    raise SystemExit(2)

SCRIPT_PATH = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_PATH.parent
LIB_DIR = PROJECT_ROOT / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from lib_bejson_Projects_logging import (  # noqa: E402
    VALID_LEVELS,
    projects_logging_configure,
    projects_logging_get_logger,
)
from lib_bejson_Projects_paths import projects_paths_resolve_storage_layout  # noqa: E402
from lib_bejson_Projects_archive import ProjectsArchiveError  # noqa: E402
from lib_bejson_Projects_filters import (  # noqa: E402
    FILTERS_CONFIG_NAME,
    ProjectsFiltersError,
    projects_filters_ensure,
    projects_filters_load,
)
from lib_bejson_Projects_pipeline import (  # noqa: E402
    ProjectsPipelineError,
    RunOptions,
    projects_pipeline_restore,
    projects_pipeline_run,
    projects_pipeline_run_zip,
)
from lib_bejson_Projects_registry import (  # noqa: E402
    ProjectsRegistryError,
    projects_registry_list,
    projects_registry_versions,
)
from lib_bejson_Projects_snapshot import (  # noqa: E402
    DEFAULT_MAX_FILE_BYTES,
    ProjectsSnapshotError,
)
from lib_bejson_Projects_tracker import IdentityOverride  # noqa: E402
from lib_bejson_Projects_verify import projects_verify_storage  # noqa: E402

VERSION = "1.8.0"
EXIT_OK, EXIT_FAIL, EXIT_USAGE = 0, 1, 2
_log = projects_logging_get_logger("cli")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="project_chunker",
        description="Hybrid Project Chunker & Tracker (tracker DB + master registry + snapshot chunks).")
    parser.add_argument("target", nargs="?",
                        help="directory to track/chunk, or a .zip file (zip mode: lock, validate, extract to staging, "
                             "verify, track+chunk, then delete the zip; required with --track/--chunk/--scoop)")
    parser.add_argument("--track", action="store_true", help="detect/create tracker, store it, register its UUID")
    parser.add_argument("--chunk", action="store_true", help="snapshot the directory into a chunk file")
    parser.add_argument("--scoop", action="store_true",
                        help="implies --track --chunk; then reload the snapshot, verify it byte-for-byte against the "
                             "source and remove the verified source files (records the original location)")
    parser.add_argument("--delete-filtered", action="store_true",
                        help="--scoop only: ALSO delete content matched by the filters (never archived, not "
                             "restorable). Needs --confirm-delete-filtered on a live run")
    parser.add_argument("--confirm-delete-filtered", action="store_true",
                        help="explicit confirmation for --delete-filtered (preview first with --dry-run)")
    parser.add_argument("--project-name", "--name", dest="project_name", metavar="NAME",
                        help="identity override: register under this project name (wins over tracker, folder and zip name)")
    parser.add_argument("--project-version", "--ver", "-V", dest="project_version", metavar="VER",
                        help="identity override: Project_Version for this run")
    parser.add_argument("--package-version", "--pkg", "--package", "-P", dest="package_version", metavar="PKG",
                        help="identity override: Package_Version for this run")
    parser.add_argument("--type", dest="project_type", metavar="TYPE",
                        help="identity override: project type (e.g. python, bash, react, skill, website, app, library; checked against canonical ledger)")
    parser.add_argument("--status", dest="project_status", metavar="STATUS",
                        help="identity override: project status (Active, Inactive, Archived, Legacy)")
    parser.add_argument("--filter-type", metavar="TYPE",
                        help="with --list or --versions: filter results by project type")
    parser.add_argument("--filter-status", metavar="STATUS",
                        help="with --list or --versions: filter results by status (active, inactive, archived, etc.)")
    parser.add_argument("--set-status", metavar="STATUS",
                        help="update status for an existing registered project UUID (e.g. Active, Inactive, Archived)")
    parser.add_argument("--set-type", metavar="TYPE",
                        help="update type for an existing registered project UUID (e.g. python, bash)")
    parser.add_argument("--set-version", metavar="VER",
                        help="update Project_Version for an existing registered project UUID (manual version definition)")
    parser.add_argument("--set-pkg", metavar="PKG",
                        help="update Package_Version for an existing registered project UUID (manual pkg definition)")
    parser.add_argument("--identity-from-filename", action="store_true",
                        help="zip intake: take V#-PKG# from the zip's file name (Name-V230-PKG7.zip) when the tracker "
                             "inside is missing or only carries scaffold defaults (1.0.0/100, 100/1)")
    parser.add_argument("--on-conflict", choices=("backup", "refuse"), default="backup",
                        help="a version already stored with DIFFERENT content: backup (default) replaces it and keeps "
                             "the previous snapshot under <storage>/superseded/; refuse stops and writes nothing")
    parser.add_argument("--verify-project", metavar="PROJECT",
                        help="with --verify: report only the findings that concern this project")
    parser.add_argument("--keep-zip", action="store_true",
                        help="zip target only: chunk the zip's contents but do not delete the zip afterwards")
    parser.add_argument("--restore", metavar="UUID", help="deploy a registered version (full UUID or unique prefix of "
                                                          "8+ chars) to its original location or --dest")
    parser.add_argument("--dest", metavar="DIR", help="destination for --restore (default: recorded original path)")
    parser.add_argument("--overwrite", action="store_true", help="--restore: allow overwriting existing files")
    parser.add_argument("--write-tracker", action="store_true",
                        help="--restore: write bejson_project.json from the tracker stored in the snapshot")
    parser.add_argument("--versions", metavar="PROJECT", help="list every registered version of a project and exit")
    parser.add_argument("--list", action="store_true", dest="list_registry", help="list registry entries and exit")
    parser.add_argument("--verify", action="store_true", help="audit storage integrity and exit")
    parser.add_argument("--dry-run", action="store_true", help="compute everything, write nothing")
    parser.add_argument("--storage-root", help="override storage root (default: <project>/storage, "
                                               "or $PROJECT_CHUNKER_STORAGE_ROOT)")
    binary = parser.add_mutually_exclusive_group()
    binary.add_argument("--binary", dest="binary_enabled", action="store_const", const=True, default=None,
                        help="store binary files (base64, Is_Binary flag); overrides Binary_Enabled in manifest/config")
    binary.add_argument("--no-binary", dest="binary_enabled", action="store_const", const=False,
                        help="ignore binary files; overrides Binary_Enabled in manifest/config")
    parser.add_argument("--exclude", action="append", default=[], metavar="DIRNAME",
                        help="extra directory name to skip while chunking (repeatable; added to the filter config)")
    parser.add_argument("--filters-config", metavar="FILE",
                        help=f"filter/switch config (default: config/{FILTERS_CONFIG_NAME}, exported on first run)")
    parser.add_argument("--no-filters", action="store_true",
                        help="this run only: switch every directory/file filter off (capture everything)")
    parser.add_argument("--filter", action="append", default=[], dest="enable_filters", metavar="ID",
                        help="this run only: force a filter on (repeatable; see --list-filters)")
    parser.add_argument("--no-filter", action="append", default=[], dest="disable_filters", metavar="ID",
                        help="this run only: force a filter off (repeatable)")
    parser.add_argument("--list-filters", action="store_true", help="show the effective filters and switches and exit")
    parser.add_argument("--ext", action="append", default=None, metavar=".EXT",
                        help="only chunk files with this extension (repeatable; default: all files)")
    parser.add_argument("--max-file-mb", type=float, default=DEFAULT_MAX_FILE_BYTES / (1024 * 1024),
                        help="skip files larger than this many MB; 0 disables (default: %(default)s)")
    parser.add_argument("--log-level", default="INFO", choices=VALID_LEVELS, type=str.upper)
    parser.add_argument("--json", action="store_true", dest="as_json", help="emit machine-readable JSON on stdout")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    return parser


def _emit(payload: dict, as_json: bool, lines: list) -> None:
    sys.stdout.write((json.dumps(payload, indent=2) if as_json else "\n".join(lines)) + "\n")


def _cmd_list(layout, as_json: bool, filter_type: Optional[str] = None, filter_status: Optional[str] = None) -> int:
    rows = projects_registry_list(layout, project_type=filter_type, status=filter_status)
    _emit({"registry": rows}, as_json,
          [f"{r['Versioned_UUID']}  {r['Project_Name']:<20}  type={r.get('Type') or '-':<10}  status={r.get('Status') or '-':<9}  {r['Tracker_File']}" for r in rows]
          or ["registry is empty" if not (filter_type or filter_status) else f"no entries match filter (type={filter_type}, status={filter_status})"])
    return EXIT_OK


def _cmd_versions(layout, project: str, as_json: bool, filter_type: Optional[str] = None, filter_status: Optional[str] = None) -> int:
    rows = projects_registry_versions(layout, project, project_type=filter_type, status=filter_status)
    _emit({"versions": rows}, as_json,
          [f"V{r['Project_Version']}-PKG{r['Package_Version']}  type={r.get('Type') or '-':<10}  {r['Status']:<9}  {r['Versioned_UUID']}  "
           f"{r['Original_Path'] or '-'}" for r in rows] or [f"no versions registered for {project}"])
    return EXIT_OK if rows else EXIT_FAIL


def _cmd_update_entry(layout, selector: str, status: Optional[str] = None, ptype: Optional[str] = None,
                      version: Optional[str] = None, pkg: Optional[str] = None, as_json: bool = False) -> int:
    from lib_bejson_Projects_registry import projects_registry_get, projects_registry_update
    target_row = projects_registry_get(layout, selector)
    real_uuid = target_row["Versioned_UUID"]
    updates = {}
    if status:
        updates["Status"] = status
    if ptype:
        updates["Type"] = ptype
    if version:
        updates["Project_Version"] = version
    if pkg:
        updates["Package_Version"] = pkg
    projects_registry_update(layout, real_uuid, **updates)
    _emit({"updated": True, "uuid": real_uuid, **updates}, as_json,
          [f"updated {target_row['Project_Name']} ({real_uuid[:8]}): " + ", ".join(f"{k}={v}" for k, v in updates.items())])
    return EXIT_OK


def _cmd_restore(layout, args) -> int:
    result = projects_pipeline_restore(layout, args.restore, Path(args.dest) if args.dest else None,
                                       overwrite=args.overwrite, write_tracker=args.write_tracker)
    payload = {"restored": True, "versioned_uuid": result.versioned_uuid, "project_name": result.project_name,
               "destination": str(result.restore.destination), "files": result.restore.files,
               "empty_dirs": result.restore.dirs, "mtimes_applied": result.restore.mtimes_applied,
               "to_original_location": result.to_original, "tracker_written": result.restore.tracker_written}
    _emit(payload, args.as_json,
          [f"[restore] {result.project_name} {result.versioned_uuid}",
           f"destination: {result.restore.destination}  files={result.restore.files} "
           f"empty_dirs={result.restore.dirs} mtimes={result.restore.mtimes_applied}"
           f"{'  (original location)' if result.to_original else ''}"
           f"{'  tracker written' if result.restore.tracker_written else ''}"])
    return EXIT_OK


def _cmd_verify(layout, as_json: bool, project: Optional[str] = None) -> int:
    issues = projects_verify_storage(layout, project)
    errors = [i for i in issues if i.severity == "error"]
    _emit({"ok": not errors, "issues": [i.__dict__ for i in issues]}, as_json,
          [f"{i.severity.upper()}  {i.path}  {i.message}" for i in issues]
          + [f"storage verify: {len(errors)} error(s), {len(issues) - len(errors)} warning(s)"])
    return EXIT_FAIL if errors else EXIT_OK


def _cmd_zip(layout, args, options) -> int:
    zres = projects_pipeline_run_zip(layout, options)
    run = zres.run
    payload = {"mode": "zip", "dry_run": zres.dry_run, "zip": str(zres.zip_path), "zip_sha256": zres.sha256,
               "files": zres.files, "zip_deleted": zres.zip_deleted, "zip_kept_reason": zres.zip_kept_reason or None,
               "project_name": run.info.project_name if run else None,
               "project_version": run.info.project_version if run else None,
               "package_version": run.info.package_version if run else None,
               "versioned_uuid": run.info.versioned_uuid if run else None,
               "snapshot_file": str(run.snapshot_file) if run and run.snapshot_file else None,
               "superseded": list(run.superseded) if run else [], "identity_notes": list(run.info.notes) if run else [],
               "warnings": zres.warnings}
    if zres.dry_run:
        lines = [f"[zip dry-run] valid zip, {zres.files} file(s), sha256={zres.sha256}; nothing extracted or changed"]
    else:
        info = run.info
        lines = [f"[zip] {info.project_name} V{info.project_version}-PKG{info.package_version} uuid={info.versioned_uuid}",
                 f"snapshot   : {run.snapshot_file}  files={run.stats.files} empty_dirs={run.stats.empty_dirs} "
                 f"binary={run.stats.binary_files}",
                 f"zip        : {'DELETED' if zres.zip_deleted else 'kept'} {zres.zip_path}"
                 + (f"  ({zres.zip_kept_reason})" if zres.zip_kept_reason else ""),
                 f"restore    : --restore {info.versioned_uuid}  (add --dest DIR to choose a location)"]
    lines += [f"superseded : {p}" for p in payload["superseded"]]
    lines += [f"warning    : {w}" for w in zres.warnings]
    _emit(payload, args.as_json, lines)
    return EXIT_OK


def _filters_path(args) -> Path:
    return Path(args.filters_config) if args.filters_config else PROJECT_ROOT / "config" / FILTERS_CONFIG_NAME


# Loads the external filter config. The default file is exported on first use unless this is a dry run or the
# user pointed at their own file (a missing user-supplied file falls back to the built-in defaults).
def _load_filters(args, dry_run: bool):
    path = _filters_path(args)
    if not args.filters_config and not dry_run:
        projects_filters_ensure(path.parent)
    return projects_filters_load(path, disable_all=args.no_filters, enable=args.enable_filters,
                                 disable=args.disable_filters)


def _cmd_list_filters(args) -> int:
    filters = _load_filters(args, dry_run=True)
    payload = {"source": filters.source, "active": filters.active, "inactive": filters.inactive,
               "dir_patterns": list(filters.dir_patterns), "file_patterns": list(filters.file_patterns),
               "binary_enabled": filters.binary_enabled, "apply_to_zip": filters.apply_to_zip}
    _emit(payload, args.as_json,
          [f"source        : {filters.source}",
           f"active        : {', '.join(filters.active) or '-'}",
           f"inactive      : {', '.join(filters.inactive) or '-'}",
           f"dir patterns  : {', '.join(filters.dir_patterns) or '-'}",
           f"file patterns : {', '.join(filters.file_patterns) or '-'}",
           f"binary_enabled: {filters.binary_enabled}   apply_to_zip: {filters.apply_to_zip}"])
    return EXIT_OK


def _cmd_run(layout, args) -> int:
    filters = _load_filters(args, args.dry_run)
    common = dict(binary_enabled=args.binary_enabled, filter_binary=filters.binary_enabled,
                  config_file=PROJECT_ROOT / "config" / "config.json",
                  extensions=tuple(args.ext) if args.ext else None,
                  max_file_bytes=int(args.max_file_mb * 1024 * 1024), on_conflict=args.on_conflict)
    identity = IdentityOverride(args.project_name or "", args.project_version or "", args.package_version or "",
                                project_type=args.project_type or "", status=args.project_status or "")
    if identity.active():
        common["identity"] = identity
    if args.identity_from_filename:
        if not Path(args.target).is_file():
            raise ProjectsPipelineError("--identity-from-filename applies to a zip target only")
        common["identity_from_filename"] = True
    if Path(args.target).is_file():
        # Zip mode captures everything (so the zip can be deleted safely) unless apply_to_zip is on in the config.
        zip_dirs = (tuple(filters.dir_patterns) if filters.apply_to_zip else ()) + tuple(args.exclude)
        zip_files = tuple(filters.file_patterns) if filters.apply_to_zip else ()
        return _cmd_zip(layout, args, RunOptions(
            target=Path(args.target), track=True, chunk=True, scoop=True, dry_run=args.dry_run,
            exclude_dirs=zip_dirs, exclude_files=zip_files, keep_zip=args.keep_zip, **common))
    options = RunOptions(
        target=Path(args.target), track=args.track, chunk=args.chunk, scoop=args.scoop, dry_run=args.dry_run,
        exclude_dirs=tuple(filters.dir_patterns) + tuple(args.exclude),
        exclude_files=tuple(filters.file_patterns), delete_filtered=args.delete_filtered,
        confirm_delete_filtered=args.confirm_delete_filtered, **common)
    result = projects_pipeline_run(layout, options)
    info = result.info
    payload = {
        "mode": result.mode, "dry_run": result.dry_run, "project_name": info.project_name,
        "project_version": info.project_version, "package_version": info.package_version,
        "project_type": info.project_type, "status": info.status,
        "versioned_uuid": info.versioned_uuid, "tracker_source": info.source, "registered": result.registered,
        "tracker_db": str(result.tracker_db) if result.tracker_db else None,
        "registry_file": str(result.registry_file) if result.registry_file else None,
        "snapshot_file": str(result.snapshot_file) if result.snapshot_file else None,
        "files": result.stats.files if result.stats else None,
        "empty_dirs": result.stats.empty_dirs if result.stats else None,
        "excluded_dirs": result.stats.excluded_dirs if result.stats else None,
        "excluded_files": result.stats.excluded_files if result.stats else None,
        "binary_files": result.stats.binary_files if result.stats else None,
        "binary_enabled": result.binary_enabled if result.stats else None,
        "binary_enabled_source": result.binary_source if result.stats else None,
        "ignored_binary": result.stats.ignored_binary if result.stats else [],
        "skipped": result.stats.skipped if result.stats else [],
        "scoop": ({"removed": len(result.scoop.removed), "dirs_removed": result.scoop.dirs_removed,
                   "root_removed": result.scoop.root_removed, "residual": result.scoop.residual,
                   "changed_since_snapshot": result.scoop.changed_since_snapshot,
                   "dry_run": result.scoop.dry_run, "filtered_dirs_deleted": result.scoop.filtered_dirs,
                   "filtered_files_deleted": result.scoop.filtered_files,
                   "filtered_skipped": result.scoop.filtered_skipped} if result.scoop else None),
        "superseded": list(result.superseded), "identity_notes": list(info.notes),
        "warnings": result.warnings,
    }
    lines = [f"[{result.mode}{' dry-run' if result.dry_run else ''}] {info.project_name} "
             f"V{info.project_version}-PKG{info.package_version} type={info.project_type or '-'} status={info.status} "
             f"uuid={info.versioned_uuid} ({info.source} tracker)"]
    if result.tracker_db:
        lines.append(f"tracker db : {result.tracker_db}")
        lines.append(f"registry   : {result.registry_file}")
    if result.stats:
        lines.append(f"snapshot   : {result.snapshot_file or '(dry-run)'}  "
                     f"files={result.stats.files} empty_dirs={result.stats.empty_dirs} binary={result.stats.binary_files} "
                     f"filtered={result.stats.excluded_dirs}d/{result.stats.excluded_files}f skipped={len(result.stats.skipped)} "
                     f"ignored_binary={len(result.stats.ignored_binary)}  [Binary_Enabled={str(result.binary_enabled).lower()} "
                     f"via {result.binary_source}]")
    if result.scoop:
        lines.append(f"scoop      : {'would remove' if result.scoop.dry_run else 'removed'} {len(result.scoop.removed)} "
                     f"file(s), {result.scoop.dirs_removed} empty dir(s)"
                     f"{', source folder removed' if result.scoop.root_removed else ''}; "
                     f"{len(result.scoop.residual)} left in place")
        lines.append(f"original   : {args.target}  (restore with --restore {info.versioned_uuid})")
    lines += [f"superseded : {p}" for p in payload["superseded"]]
    lines += [f"warning    : {w}" for w in result.warnings]
    _emit(payload, args.as_json, lines)
    return EXIT_OK


def main(argv=None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    projects_logging_configure(args.log_level)

    needs_target = args.track or args.chunk or args.scoop or args.set_status or args.set_type or args.set_version or args.set_pkg
    if not (needs_target or args.list_registry or args.verify or args.restore or args.versions
            or args.list_filters):
        parser.error("choose at least one of --track, --chunk, --scoop, --restore, --versions, --list, --verify, "
                     "--list-filters, --set-status, --set-type, --set-version, --set-pkg")
    if needs_target and not args.target:
        parser.error("a target directory or UUID is required with --track/--chunk/--scoop/--set-* commands")
    if args.restore and (args.track or args.chunk or args.scoop):
        parser.error("--restore cannot be combined with --track/--chunk/--scoop")
    if (args.dest or args.overwrite or args.write_tracker) and not args.restore:
        parser.error("--dest/--overwrite/--write-tracker apply to --restore only")
    if args.delete_filtered and not args.scoop:
        parser.error("--delete-filtered applies to --scoop only")
    if args.confirm_delete_filtered and not args.delete_filtered:
        parser.error("--confirm-delete-filtered needs --delete-filtered")
    if args.max_file_mb < 0:
        parser.error("--max-file-mb must be >= 0")
    if (args.project_name or args.project_version or args.package_version or args.project_type
            or args.project_status or args.identity_from_filename) and not (args.track or args.chunk or args.scoop or (args.target and Path(args.target).is_file())):
        parser.error("--project-name/--project-version/--package-version/--type/--status/--identity-from-filename apply to a run "
                     "(--track/--chunk/--scoop or a zip target) only")
    if args.verify_project and not args.verify:
        parser.error("--verify-project needs --verify")

    layout = projects_paths_resolve_storage_layout(PROJECT_ROOT, args.storage_root)
    try:
        if args.set_status or args.set_type or args.set_version or args.set_pkg:
            return _cmd_update_entry(layout, args.target, status=args.set_status, ptype=args.set_type,
                                     version=args.set_version, pkg=args.set_pkg, as_json=args.as_json)
        if args.list_filters:
            return _cmd_list_filters(args)
        if args.list_registry:
            return _cmd_list(layout, args.as_json, filter_type=args.filter_type, filter_status=args.filter_status)
        if args.versions:
            return _cmd_versions(layout, args.versions, args.as_json, filter_type=args.filter_type, filter_status=args.filter_status)
        if args.verify:
            return _cmd_verify(layout, args.as_json, args.verify_project)
        if args.restore:
            return _cmd_restore(layout, args)
        return _cmd_run(layout, args)
    except (ProjectsPipelineError, ProjectsFiltersError, ProjectsRegistryError, ProjectsSnapshotError, ProjectsArchiveError, OSError) as exc:
        _log.error("%s", exc)
        return EXIT_FAIL


if __name__ == "__main__":
    raise SystemExit(main())
