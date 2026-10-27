"""
File Name:       lib_bejson_Projects_pipeline.py
Relative Path:   lib/lib_bejson_Projects_pipeline.py
Family:          Projects
Description:     Orchestration of the track / chunk / scoop jobs and of restore. The CLI and any other front end
                 call this; it owns ordering (detect -> register -> snapshot -> re-verify -> scoop) and mode
                 rules, nothing else. Scoop only ever runs on a snapshot that was reloaded from disk and
                 matched against the source.
VERSION:         1.8.0
Author:          Elton Boehnen · boehnenelton2024@gmail.com · boehnenelton2024.pages.dev · github.com/boehnenelton
RELATIONAL_ID:   d653b09d-b29e-4ec8-aebb-7ed7936f1609
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Tuple

from lib_bejson_Projects_archive import (
    ProjectsArchiveError,
    RestoreResult,
    projects_archive_guard_target,
    ScoopResult,
    projects_archive_restore,
    projects_archive_find_filtered,
    projects_archive_restore_plan,
    projects_archive_scoop,
)
from lib_bejson_Projects_config import projects_config_binary_enabled
from lib_bejson_Projects_logging import projects_logging_get_logger
from lib_bejson_Core_bejson_core import bejson_core_load_file
from lib_bejson_Projects_paths import StorageLayout, projects_paths_sanitize_name
from lib_bejson_Projects_registry import (
    STATUS_ARCHIVED,
    STATUS_CHUNKED,
    STATUS_RESTORED,
    projects_registry_find_uuid,
    projects_registry_get,
    projects_registry_has_uuid,
    projects_registry_register,
    projects_registry_update,
)
from lib_bejson_Projects_snapshot import (
    DEFAULT_EXCLUDE_DIRS,
    DEFAULT_MAX_FILE_BYTES,
    SNAPSHOT_FIELDS,
    SnapshotStats,
    projects_snapshot_build_document,
    projects_snapshot_build_rows,
    projects_snapshot_digest,
    projects_snapshot_load,
    projects_snapshot_remove_legacy,
    projects_snapshot_supersede,
    projects_snapshot_write,
)
from lib_bejson_Projects_tracker import (
    IdentityOverride,
    ProjectsTrackerError,
    TrackerInfo,
    projects_tracker_detect_or_create,
    projects_tracker_filename_identity,
    projects_tracker_is_scaffold_default,
)
from lib_bejson_Projects_zip import (
    ProjectsZipError,
    ProjectsZipLock,
    projects_zip_cleanup,
    projects_zip_inspect,
    projects_zip_sha256,
    projects_zip_stage,
)

VERSION = "1.8.0"
_log = projects_logging_get_logger("pipeline")


class ProjectsPipelineError(Exception):
    """Raised for invalid job requests (bad target, no mode, target inside storage, unknown restore entry)."""


@dataclass
class RunOptions:
    target: Path
    track: bool = False
    chunk: bool = False
    scoop: bool = False
    dry_run: bool = False
    exclude_dirs: Tuple[str, ...] = DEFAULT_EXCLUDE_DIRS
    exclude_files: Tuple[str, ...] = ()         # file-name globs from the external filter config
    extensions: Optional[Tuple[str, ...]] = None
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES
    binary_enabled: Optional[bool] = None       # command-line override; None = use manifest/config/default
    filter_binary: Optional[bool] = None        # binary_enabled switch from config/chunker_filters.json
    config_file: Optional[Path] = None          # legacy config.json (deprecated Binary_Enabled header fallback)
    original_path: Optional[Path] = None        # location recorded as Original_Path (default: target); zip intake sets it
    extra_headers: Optional[dict] = None        # extra snapshot headers (zip provenance)
    keep_zip: bool = False                      # zip mode: never delete the zip
    delete_filtered_keep: Tuple[str, ...] = ()  # filter-matched top-level paths to keep even with delete_filtered
    progress: Optional[Callable[[dict], None]] = None   # receives {"stage": ..., ...} events (web UI)
    delete_filtered: bool = False               # scoop only: ALSO delete filter-matched content that was never archived
    confirm_delete_filtered: bool = False       # explicit confirmation required for delete_filtered on a live run
    identity: Optional[IdentityOverride] = None  # explicit name/version/package: wins over tracker, folder and zip name
    identity_from_filename: bool = False        # zip mode: take V#-PKG# from the zip's file name when the tracker is
                                                # missing or only carries scaffold defaults (1.0.0/100, 100/1)
    filename_identity: Optional[Tuple[str, str, str]] = None   # set by zip intake: (name, ver, pkg) the zip file name carries
    on_conflict: str = "backup"                 # same name+version+package already stored with DIFFERENT content:
                                                # "backup" replaces it and keeps the previous snapshot in superseded/;
                                                # "refuse" stops before writing anything


@dataclass
class RunResult:
    info: TrackerInfo
    mode: str
    dry_run: bool
    registered: bool = False
    tracker_db: Optional[Path] = None
    registry_file: Optional[Path] = None
    snapshot_file: Optional[Path] = None
    stats: Optional[SnapshotStats] = None
    scoop: Optional[ScoopResult] = None
    binary_enabled: bool = True
    binary_source: str = "default"
    warnings: list = field(default_factory=list)
    filtered_matches: dict = field(default_factory=dict)    # {"dirs": [...], "files": [...]} matched by filters (scoop)
    superseded: list = field(default_factory=list)          # previous snapshots kept because this run replaced different content


def _emit(options, **event) -> None:
    if options.progress:
        try:
            options.progress(event)
        except Exception as exc:                             # a progress sink must never break a job
            _log.debug("progress sink failed: %s", exc)


def projects_pipeline_mode(track: bool, chunk: bool, scoop: bool = False) -> str:
    if scoop:
        return "track+chunk+scoop"
    if track and chunk:
        return "track+chunk"
    return "track" if track else "chunk" if chunk else ""


# Runs one job. track: detect tracker, store it, register UUID + original location. chunk: snapshot the directory.
# scoop: implies track+chunk; after the snapshot is written it is reloaded from disk, matched byte-for-byte against
# the source and only then are the source files removed (see lib_bejson_Projects_archive). chunk alone never writes
# tracker/registry data; its snapshot is flagged unregistered unless the UUID is already known. dry_run computes
# everything (including what scoop would remove) and writes/removes nothing.
ON_CONFLICT_CHOICES = ("backup", "refuse")


# Zip intake only: the zip's file name may carry an identity (Name-V230-PKG7.zip) that the manifest inside disagrees
# with (a scaffold manifest still says 1.0.0/100; a zip with no tracker gets the folder defaults). Returns the
# (possibly re-resolved) TrackerInfo and appends a plain-language warning when a disagreement is left in place.
def _apply_filename_identity(options: RunOptions, info: TrackerInfo, target: Path, lookup, warnings: list) -> TrackerInfo:
    fn = options.filename_identity
    if not fn:
        return info
    _, fn_ver, fn_pkg = fn
    # "V100" in a file name is the compact form of version 1.0.0 (dots dropped); compare on that basis.
    if (info.project_version.replace(".", ""), info.package_version) == (fn_ver.replace(".", ""), fn_pkg):
        return info
    if options.identity and (options.identity.version or options.identity.package):
        return info                                         # the caller was explicit; no second-guessing
    untrusted = info.source == "generated" or projects_tracker_is_scaffold_default(info.project_version, info.package_version)
    if untrusted and options.identity_from_filename:
        override = IdentityOverride(name=options.identity.name if options.identity else "", version=fn_ver, package=fn_pkg)
        resolved = projects_tracker_detect_or_create(target, uuid_lookup=lookup, override=override)
        resolved.notes.append("identity taken from the zip file name")
        return resolved
    reason = "no tracker" if info.source == "generated" else ("scaffold-default tracker" if untrusted else "tracker values")
    warnings.append(f"the zip file name says V{fn_ver}-PKG{fn_pkg} but the project resolved to "
                    f"V{info.project_version}-PKG{info.package_version} ({reason}) and is registered as the latter; use "
                    "--identity-from-filename, or --project-version / --package-version, to choose")
    return info


# Existing snapshot files of this exact version, in the slot this run will write, whose content differs from `rows`.
# An existing file that cannot be read is reported too (replacing it is a recovery, never a refusal).
def _snapshot_conflicts(layout: StorageLayout, info: TrackerInfo, rows: list, untracked: bool) -> list:
    existing = layout.snapshot_file(info.project_name, info.project_version, info.package_version, untracked=untracked)
    if not existing.is_file():
        return []
    new_digest = projects_snapshot_digest({"Fields": SNAPSHOT_FIELDS, "Values": rows})
    try:
        old_digest = projects_snapshot_digest(bejson_core_load_file(str(existing)) or {})
    except Exception as exc:                                # unreadable / not a snapshot: keep a copy, then replace
        _log.warning("existing snapshot %s could not be compared (%s)", existing, exc)
        return [(existing, False)]
    return [] if old_digest == new_digest else [(existing, True)]


def projects_pipeline_run(layout: StorageLayout, options: RunOptions) -> RunResult:
    if options.on_conflict not in ON_CONFLICT_CHOICES:
        raise ProjectsPipelineError(f"on_conflict must be one of {', '.join(ON_CONFLICT_CHOICES)}")
    track, chunk = options.track or options.scoop, options.chunk or options.scoop
    mode = projects_pipeline_mode(track, chunk, options.scoop)
    if not mode:
        raise ProjectsPipelineError("nothing to do: enable track and/or chunk (or scoop)")
    target = Path(options.target).resolve()
    if not target.is_dir():
        raise ProjectsPipelineError(f"target is not a directory: {target}")
    if layout.contains(target):
        raise ProjectsPipelineError(f"target lies inside the chunker storage root: {target}")

    if options.delete_filtered and not options.scoop:
        raise ProjectsPipelineError("delete_filtered applies to scoop only")
    if options.delete_filtered and not options.dry_run and not options.confirm_delete_filtered:
        raise ProjectsPipelineError(
            "refusing to delete filtered content without confirmation: it is NOT archived and cannot be restored. "
            "Preview with a dry run, then confirm explicitly (--confirm-delete-filtered)")
    protected = [layout.storage_root, Path(__file__).resolve().parent.parent]   # storage + this project
    if options.scoop:
        try:
            projects_archive_guard_target(target, protected)       # refuse BEFORE writing any tracker/snapshot data
        except ProjectsArchiveError as exc:
            raise ProjectsPipelineError(str(exc)) from exc

    def lookup(name: str, pver: str, pkg: str) -> Optional[str]:
        return projects_registry_find_uuid(layout, name, pver, pkg)

    early_warnings: list = []
    try:
        info = projects_tracker_detect_or_create(target, uuid_lookup=lookup, override=options.identity)
    except ProjectsTrackerError as exc:
        raise ProjectsPipelineError(str(exc)) from exc
    info = _apply_filename_identity(options, info, target, lookup, early_warnings)
    _log.info("%s %s V%s-PKG%s uuid=%s (%s tracker)", mode, info.project_name, info.project_version,
              info.package_version, info.versioned_uuid, info.source)
    result = RunResult(info=info, mode=mode, dry_run=options.dry_run, warnings=early_warnings)
    _emit(options, stage="track", label=f"{info.project_name} V{info.project_version}-PKG{info.package_version} ({info.source} tracker)")
    recorded_original = Path(options.original_path) if options.original_path else target

    conflicts: list = []
    if chunk:
        # The chunker's own storage must never be swallowed into a snapshot of a folder that contains it.
        skip_dirs = [layout.storage_root] if target in layout.storage_root.parents else []
        manifest = info.tracker_data if info.source == "existing" else None
        result.binary_enabled, result.binary_source = projects_config_binary_enabled(
            options.config_file, manifest, options.binary_enabled, options.filter_binary)
        _emit(options, stage="chunk", label="reading files")
        rows, result.stats = projects_snapshot_build_rows(
            target, options.exclude_dirs, options.extensions, skip_dirs, options.max_file_bytes,
            binary_enabled=result.binary_enabled, exclude_files=options.exclude_files)
        if options.scoop and (result.stats.excluded_dirs or result.stats.excluded_files):
            matched = projects_archive_find_filtered(target, options.exclude_dirs, options.exclude_files)
            names = (matched.dirs + matched.files)[:8]
            result.filtered_matches = {"dirs": list(matched.dirs), "files": list(matched.files)}
            if options.delete_filtered:
                result.warnings.append(
                    f"FILTERS ARE ON: {len(matched.dirs)} dir(s) and {len(matched.files)} file(s) matched a filter "
                    f"(e.g. {', '.join(names)}) and are not archived; --delete-filtered will "
                    f"{'show them in this dry run only' if options.dry_run else 'DELETE them permanently'}")
            else:
                result.warnings.append(
                    f"FILTERS ARE ON: {len(matched.dirs)} dir(s) and {len(matched.files)} file(s) matched a filter "
                    f"(e.g. {', '.join(names)}); they are not archived and will be LEFT IN PLACE, so the source "
                    "folder will remain. Deleting them needs --delete-filtered --confirm-delete-filtered")
        if result.stats.ignored_binary:
            result.warnings.append(f"{len(result.stats.ignored_binary)} binary file(s) ignored "
                                   f"(Binary_Enabled=false from {result.binary_source})")
        # Same name+version+package already stored with DIFFERENT content? Decided BEFORE anything is registered or
        # written, so a refusal leaves storage exactly as it was.
        slot_untracked = not (track or projects_registry_has_uuid(layout, info.versioned_uuid))
        conflicts = _snapshot_conflicts(layout, info, rows, slot_untracked)
        differing = [c for c in conflicts if c[1]]           # (path, True) = readable with different content; an
                                                             # unreadable file is only ever backed up and replaced
        if differing and options.on_conflict == "refuse":
            if not options.dry_run:
                raise ProjectsPipelineError(
                    f"{info.project_name} V{info.project_version}-PKG{info.package_version} is already stored with "
                    "different content; nothing was written. Use a new version/package number "
                    "(--project-version / --package-version), or --on-conflict backup to replace it and keep the "
                    "previous snapshot")
            result.warnings.append("WOULD REFUSE: this version is already stored with different content (--on-conflict refuse)")
        elif differing and options.dry_run:
            result.warnings.append("WOULD REPLACE: this version is already stored with different content; the previous "
                                   "snapshot would be kept in superseded/")

    if track and not options.dry_run:
        result.tracker_db, result.registry_file = projects_registry_register(layout, info, original_path=recorded_original)
        result.registered = True

    if chunk:
        result.registered = result.registered or projects_registry_has_uuid(layout, info.versioned_uuid)
        # A chunk whose UUID is not in the registry is "untracked": it is stored in its own folder, apart from tracked chunks.
        untracked = not (result.registered or (track and options.dry_run))
        if untracked:
            result.warnings.append(f"snapshot UUID is not in the registry; stored in {layout.untracked_dir.name}/ "
                                   "(run with track to register it and file it with the tracked chunks)")
        if not options.dry_run:
            _emit(options, stage="write", label="writing snapshot")
            for existing, differs in conflicts:
                kept = projects_snapshot_supersede(layout, existing, info.project_name)
                result.superseded.append(str(kept))
                result.warnings.append(
                    ("replaced an existing snapshot of this version that held DIFFERENT content" if differs
                     else "replaced an existing snapshot of this version that could not be read")
                    + f"; the previous one is kept at {layout.relative_ref(kept)}")
            tracker_ref = layout.relative_ref(result.tracker_db) if result.tracker_db else ""
            result.snapshot_file = projects_snapshot_write(layout, info, rows, tracker_ref, str(recorded_original),
                                                           result.binary_enabled, options.extra_headers,
                                                           untracked=untracked)
            if result.registered:
                projects_registry_update(layout, info.versioned_uuid, Status=STATUS_CHUNKED,
                                         Snapshot_File=layout.relative_ref(result.snapshot_file))
                projects_snapshot_remove_legacy(layout, info)      # replaces an old-style V#-PKG#.bejson of this version

    if options.scoop:
        _emit(options, stage="scoop", label="verifying snapshot against source" if not options.dry_run else "planning scoop")
        if options.dry_run:
            doc = projects_snapshot_build_document(info, rows, binary_enabled=result.binary_enabled)
        else:
            doc = projects_snapshot_load(result.snapshot_file)     # reload from disk: verify what was WRITTEN
        filtered_patterns = (options.exclude_dirs, options.exclude_files) if options.delete_filtered else None
        result.scoop = projects_archive_scoop(doc, target, protected, dry_run=options.dry_run,
                                              delete_filtered=filtered_patterns,
                                              keep_filtered=options.delete_filtered_keep)
        if not options.dry_run:
            projects_registry_update(layout, info.versioned_uuid, Status=STATUS_ARCHIVED)
        if result.scoop.filtered_dirs or result.scoop.filtered_files:
            names = (result.scoop.filtered_dirs + result.scoop.filtered_files)[:8]
            result.warnings.append(
                f"{'WOULD DELETE' if options.dry_run else 'DELETED'} {len(result.scoop.filtered_dirs)} filtered "
                f"dir(s) and {len(result.scoop.filtered_files)} filtered file(s) that were NOT archived and cannot "
                f"be restored (e.g. {', '.join(names)})")
        if result.scoop.filtered_skipped:
            result.warnings.append(f"{len(result.scoop.filtered_skipped)} filtered path(s) were not deleted "
                                   f"(e.g. {result.scoop.filtered_skipped[0]})")
        if result.scoop.residual:
            result.warnings.append(f"{len(result.scoop.residual)} file(s) not in the snapshot were left in place "
                                   f"(e.g. {result.scoop.residual[0]})")
        if result.scoop.changed_since_snapshot:
            result.warnings.append(f"{len(result.scoop.changed_since_snapshot)} file(s) changed during the run "
                                   "and were NOT removed")

    for warning in result.warnings:
        _log.warning(warning)
    return result


@dataclass
class ZipRunResult:
    run: Optional[RunResult]
    zip_path: Path
    sha256: str
    files: int
    dry_run: bool = False
    zip_deleted: bool = False
    zip_kept_reason: str = ""
    fully_captured: bool = False        # every extracted file is in the verified snapshot (zip deletion would lose nothing)
    warnings: list = field(default_factory=list)


# Zip intake job: lock the zip -> confirm it is a complete valid zip -> extract to a staging dir beside it -> verify
# the extraction -> track + chunk + verified scoop of the staging dir (the snapshot is reloaded from disk and matched
# to the extracted files) -> delete the zip, and only if EVERY extracted file made it into the snapshot, the zip's
# SHA-256 is unchanged, and keep_zip is off. Anything else leaves the zip in place and says why. The staging dir is
# always removed. dry_run inspects the zip (magic, CRC test, limits) and writes/extracts/removes nothing.
def projects_pipeline_run_zip(layout: StorageLayout, options: RunOptions) -> ZipRunResult:
    zip_path = Path(options.target)
    if zip_path.is_symlink() or not zip_path.is_file():
        raise ProjectsPipelineError(f"zip target is not a regular file: {zip_path}")
    zip_path = zip_path.resolve()
    if layout.contains(zip_path):
        raise ProjectsPipelineError(f"zip lies inside the chunker storage root: {zip_path}")
    try:
        with ProjectsZipLock(zip_path):
            _emit(options, stage="lock", label="zip locked")
            if options.dry_run:
                infos = projects_zip_inspect(zip_path)
                return ZipRunResult(None, zip_path, projects_zip_sha256(zip_path),
                                    sum(1 for i in infos if not i.is_dir()), dry_run=True,
                                    zip_kept_reason="dry-run")
            _emit(options, stage="extract", label="validating, extracting and verifying the zip")
            staged = projects_zip_stage(zip_path)
            try:
                inner = RunOptions(
                    target=staged.project_dir, track=True, chunk=True, scoop=True, dry_run=False,
                    exclude_dirs=options.exclude_dirs, exclude_files=options.exclude_files,
                    extensions=options.extensions, max_file_bytes=options.max_file_bytes,
                    binary_enabled=options.binary_enabled, filter_binary=options.filter_binary,
                    config_file=options.config_file, original_path=staged.original_dir,
                    progress=options.progress,
                    extra_headers={"Source_Zip": zip_path.name, "Source_Zip_SHA256": staged.sha256,
                                   "Source_Zip_Bytes": staged.size},
                    identity=options.identity, identity_from_filename=options.identity_from_filename,
                    filename_identity=projects_tracker_filename_identity(zip_path.name),
                    on_conflict=options.on_conflict)
                run = projects_pipeline_run(layout, inner)
                out = ZipRunResult(run, zip_path, staged.sha256, staged.files, warnings=list(run.warnings))
                reason = ""
                out.fully_captured = bool(run.scoop and not run.scoop.residual
                                          and not run.scoop.changed_since_snapshot)
                if not out.fully_captured:
                    left = len(run.scoop.residual) + len(run.scoop.changed_since_snapshot) if run.scoop else 0
                    reason = (f"{left} extracted file(s) are not in the snapshot (excluded, oversize or ignored "
                              "binary); deleting the zip would lose them")
                elif projects_zip_sha256(zip_path) != staged.sha256:
                    reason = "zip changed during the job"
                    out.fully_captured = False
                elif options.keep_zip:
                    reason = "--keep-zip"
                if reason:
                    out.zip_kept_reason = reason
                else:
                    _emit(options, stage="delete", label="deleting the zip")
                    zip_path.unlink()
                    out.zip_deleted = True
                return out
            finally:
                projects_zip_cleanup(staged)
    except ProjectsZipError as exc:
        raise ProjectsPipelineError(str(exc)) from exc


# Snapshot file for a registry row: the recorded reference, else the <project>-V<ver>-PKG<pkg> name (then the
# pre-1.7.1 V<ver>-PKG<pkg> name), else (legacy rows with no
# recorded reference or version) a scan of the project's snapshot folder for the file whose header UUID matches.
def _find_snapshot(layout: StorageLayout, row: dict) -> Optional[Path]:
    ref = row.get("Snapshot_File") or ""
    if ref:
        path = layout.resolve_ref(ref)
        return path if path.is_file() else None
    if row.get("Project_Version") and row.get("Package_Version"):
        for path in (layout.snapshot_file(row["Project_Name"], row["Project_Version"], row["Package_Version"]),
                     layout.legacy_snapshot_file(row["Project_Name"], row["Project_Version"], row["Package_Version"])):
            if path.is_file():
                return path
    folder = layout.snapshots_dir / projects_paths_sanitize_name(row["Project_Name"]) / "data"
    for candidate in sorted(folder.glob("*.bejson")) if folder.is_dir() else []:
        doc = bejson_core_load_file(str(candidate))
        if doc and doc.get("Versioned_UUID") == row["Versioned_UUID"]:
            return candidate
    return None


@dataclass
class RestorePipelineResult:
    versioned_uuid: str
    project_name: str
    restore: RestoreResult
    to_original: bool


# Restores one registered version. dest=None deploys to the recorded original location. Marks the registry row
# "restored" only when it went back to the original location (a copy elsewhere leaves the archive status alone).
def projects_pipeline_restore(layout: StorageLayout, selector: str, dest: Optional[Path] = None,
                              overwrite: bool = False, write_tracker: bool = False) -> RestorePipelineResult:
    try:
        row = projects_registry_get(layout, selector)
    except Exception as exc:
        raise ProjectsPipelineError(str(exc)) from exc
    snapshot_path = _find_snapshot(layout, row)
    if snapshot_path is None:
        raise ProjectsPipelineError(f"no snapshot found for {row['Versioned_UUID']}")
    original = row.get("Original_Path") or ""
    destination = Path(dest) if dest else (Path(original) if original else None)
    if destination is None:
        raise ProjectsPipelineError("no destination: entry has no recorded original path; pass one explicitly")
    doc = projects_snapshot_load(snapshot_path)
    try:
        restored = projects_archive_restore(doc, destination, overwrite=overwrite, write_tracker=write_tracker)
    except ProjectsArchiveError as exc:
        raise ProjectsPipelineError(str(exc)) from exc
    to_original = bool(original) and Path(destination).resolve() == Path(original).resolve()
    if to_original:
        projects_registry_update(layout, row["Versioned_UUID"], Status=STATUS_RESTORED)
    return RestorePipelineResult(row["Versioned_UUID"], row["Project_Name"], restored, to_original)


# Read-only preview of projects_pipeline_restore: destination, counts and what would block it.
def projects_pipeline_restore_plan(layout: StorageLayout, selector: str, dest: Optional[Path] = None,
                                   overwrite: bool = False) -> dict:
    try:
        row = projects_registry_get(layout, selector)
    except Exception as exc:
        raise ProjectsPipelineError(str(exc)) from exc
    snapshot_path = _find_snapshot(layout, row)
    if snapshot_path is None:
        raise ProjectsPipelineError(f"no snapshot found for {row['Versioned_UUID']}")
    original = row.get("Original_Path") or ""
    destination = Path(dest) if dest else (Path(original) if original else None)
    if destination is None:
        raise ProjectsPipelineError("no destination: entry has no recorded original path; pass one explicitly")
    try:
        plan = projects_archive_restore_plan(projects_snapshot_load(snapshot_path), destination, overwrite)
    except ProjectsArchiveError as exc:
        raise ProjectsPipelineError(str(exc)) from exc
    plan.update(versioned_uuid=row["Versioned_UUID"], project_name=row["Project_Name"], original_path=original,
                to_original=bool(original) and destination.resolve() == Path(original).resolve())
    return plan
