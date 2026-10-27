#!/usr/bin/env python3
"""
File Name:       core.py
Relative Path:   Web/chunker_web/core.py
Description:     Shared context for the unified Project Chunker web app: paths and config, picker-root confinement,
                 request parsing into chunker run options, preview->run plan coupling (plan_id), result serializers.
VERSION:         1.8.0
Author:          Elton Boehnen · boehnenelton2024@gmail.com · boehnenelton2024.pages.dev · github.com/boehnenelton
RELATIONAL_ID:   0aa63618-ecf9-4db5-8092-5a3993cd8055
"""

import hashlib
import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from lib_bejson_Core_bejson_env import resolve_path, source_env
from lib_bejson_Projects_filters import (
    ProjectsFiltersError,
    projects_filters_ensure,
    projects_filters_load,
)
from lib_bejson_Projects_active import projects_active_path
from lib_bejson_Projects_logging import projects_logging_get_logger
from lib_bejson_Projects_paths import projects_paths_resolve_storage_layout
from lib_bejson_Projects_pipeline import RunOptions
from lib_bejson_Projects_tracker import IdentityOverride, ProjectsTrackerError
from lib_bejson_Projects_webconfig import projects_webconfig_ensure, projects_webconfig_load

VERSION = "1.8.0"
PLAN_TTL_SECONDS = 30 * 60
DELETE_FILTERED_PHRASE = "DELETE FILTERED"
ROOT_ENV_KEYS = ("INTERNAL_STORAGE", "SD_CARD", "ADMIN", "ADMIN_ROOT", "PROJECT_ROOT")
_log = projects_logging_get_logger("web.core")


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400, **extra: Any):
        super().__init__(message)
        self.message, self.status, self.extra = message, status, extra


class Ctx:
    """Everything the routes share; one instance per app."""

    def __init__(self, project_root, storage_root=None, config_dir=None, data_dir=None, extra_roots=()):
        self.project_root = Path(project_root).resolve()
        self.config_dir = Path(config_dir).resolve() if config_dir else self.project_root / "config"
        self.data_dir = Path(data_dir).resolve() if data_dir else self.project_root / "data"
        self.legacy_config = self.config_dir / "config.json"
        self.filters_path = self.config_dir / "chunker_filters.json"
        self.migrator_config = self.config_dir / "migrator_config.json"
        try:
            projects_filters_ensure(self.config_dir)
        except Exception as exc:                                  # filters then fall back to built-in defaults
            _log.warning("could not export filter config: %s", exc)
        self.webcfg_path = projects_webconfig_ensure(self.config_dir)
        self.webcfg = projects_webconfig_load(self.webcfg_path)
        chosen = storage_root or self.webcfg.get("storage_root") or None
        self.layout = projects_paths_resolve_storage_layout(self.project_root, chosen)
        self.extra_roots = [str(r) for r in extra_roots]
        self.token = secrets.token_hex(16)
        self.exports_dir = self.data_dir / "web_exports"
        self.active_file = projects_active_path(self.data_dir)
        self._plans: Dict[str, Dict[str, Any]] = {}
        self._plan_lock = threading.Lock()

    # ── folder-picker roots ──────────────────────────────────────────────────────────────────────────────
    def roots(self) -> List[Path]:
        try:
            source_env()
        except Exception as exc:
            _log.debug("source_env failed: %s", exc)
        raw: List[Optional[str]] = [os.environ.get(k) for k in ROOT_ENV_KEYS]
        raw += [str(self.project_root), os.environ.get("HOME")] + self.extra_roots
        for item in (self.webcfg.get("picker_roots") or "").split(","):
            if item.strip():
                raw.append(resolve_path(item.strip()))
        out: List[Path] = []
        for item in raw:
            if not item:
                continue
            try:
                path = Path(item).expanduser().resolve()
            except OSError:
                continue
            if path.is_dir() and path not in out:
                out.append(path)
        return out

    def confine(self, raw: str, must_exist: bool = True) -> Path:
        if not raw or not str(raw).strip():
            raise ApiError("a path is required")
        try:
            path = Path(str(raw)).expanduser().resolve()
        except OSError as exc:
            raise ApiError(f"bad path: {exc}")
        for root in self.roots():
            if path == root or root in path.parents:
                if must_exist and not path.exists():
                    raise ApiError(f"not found: {path}", 404)
                return path
        raise ApiError("path is outside the allowed folder roots (add one under Settings > picker_roots)", 403)

    # ── preview -> run coupling ──────────────────────────────────────────────────────────────────────────
    @staticmethod
    def plan_hash(kind: str, inputs: Dict[str, Any]) -> str:
        blob = json.dumps({"k": kind, "i": inputs}, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]

    def plan_store(self, kind: str, inputs: Dict[str, Any], meta: Dict[str, Any]) -> str:
        pid = self.plan_hash(kind, inputs)
        now = time.time()
        with self._plan_lock:
            self._plans = {k: v for k, v in self._plans.items() if now - v["at"] < PLAN_TTL_SECONDS}
            self._plans[pid] = {"at": now, "meta": meta}
        return pid

    def plan_check(self, kind: str, inputs: Dict[str, Any], plan_id: str) -> Dict[str, Any]:
        if not plan_id or plan_id != self.plan_hash(kind, inputs):
            raise ApiError("no matching preview: run Preview with these exact settings first", 409)
        with self._plan_lock:
            plan = self._plans.get(plan_id)
        if not plan or time.time() - plan["at"] >= PLAN_TTL_SECONDS:
            raise ApiError("preview expired: run Preview again", 409)
        return plan["meta"]


def _bool(body: Dict[str, Any], key: str, default: bool = False) -> bool:
    value = body.get(key, default)
    return value if isinstance(value, bool) else default


def _list(body: Dict[str, Any], key: str) -> List[str]:
    value = body.get(key) or []
    if isinstance(value, str):
        value = value.split(",")
    return sorted({str(v).strip() for v in value if str(v).strip()})


def parse_run_inputs(ctx: Ctx, body: Dict[str, Any], op: str) -> Dict[str, Any]:
    """Normalises a request body into a JSON-able dict (also the plan_id input)."""
    raw_target = str(body.get("target", "") or "").strip()
    if not raw_target:                                        # no target given: the active project's source folder
        from .active import active_source                      # (imported here: active.py imports this module)
        raw_target = active_source(ctx) or ""
        if not raw_target:
            raise ApiError("no target given and there is no active project with a source folder on disk: "
                           "choose a target, or make a project active on the Projects page")
    target = ctx.confine(raw_target)
    binary = body.get("binary", "inherit")
    try:
        mb = float(body.get("max_file_mb", ctx.webcfg.get("max_file_mb", "25")))
    except (TypeError, ValueError):
        raise ApiError("max_file_mb must be a number")
    apply_zip = body.get("apply_filters_to_zip")
    return {
        "op": op, "target": str(target),
        "track": op == "track" or (op == "chunk" and _bool(body, "track")) or op == "scoop",
        "chunk": op in ("chunk", "scoop"), "scoop": op == "scoop",
        "binary": {"true": True, "false": False}.get(str(binary).lower()),
        "use_filters": _bool(body, "use_filters", True),
        "filter_on": _list(body, "filter_on"), "filter_off": _list(body, "filter_off"),
        "extra_excludes": _list(body, "extra_excludes"), "ext": _list(body, "ext"),
        "max_file_mb": max(0.0, mb),
        "delete_filtered": _bool(body, "delete_filtered"), "keep_filtered": _list(body, "keep_filtered"),
        "keep_zip": _bool(body, "keep_zip"),
        "apply_filters_to_zip": apply_zip if isinstance(apply_zip, bool) else None,
        "identity_name": str(body.get("project_name", "") or "").strip(),
        "identity_version": str(body.get("project_version", "") or "").strip(),
        "identity_package": str(body.get("package_version", "") or "").strip(),
        "identity_from_filename": _bool(body, "identity_from_filename"),
        "on_conflict": str(body.get("on_conflict", "backup") or "backup"),
    }


def build_options(ctx: Ctx, inputs: Dict[str, Any], dry_run: bool, progress=None, confirm_filtered=False) -> RunOptions:
    try:
        filters = projects_filters_load(ctx.filters_path, disable_all=not inputs["use_filters"],
                                        enable=inputs["filter_on"], disable=inputs["filter_off"])
    except ProjectsFiltersError as exc:
        raise ApiError(str(exc))
    if inputs["on_conflict"] not in ("backup", "refuse"):
        raise ApiError("on_conflict must be backup or refuse")
    identity = IdentityOverride(inputs["identity_name"], inputs["identity_version"], inputs["identity_package"])
    try:
        identity.validate()
    except ProjectsTrackerError as exc:
        raise ApiError(str(exc))
    common = dict(binary_enabled=inputs["binary"], filter_binary=filters.binary_enabled, config_file=ctx.legacy_config,
                  identity=identity if identity.active() else None, on_conflict=inputs["on_conflict"],
                  extensions=tuple(inputs["ext"]) or None, max_file_bytes=int(inputs["max_file_mb"] * 1024 * 1024),
                  dry_run=dry_run, progress=progress)
    target = Path(inputs["target"])
    if target.is_file():
        apply_zip = filters.apply_to_zip if inputs["apply_filters_to_zip"] is None else inputs["apply_filters_to_zip"]
        return RunOptions(target=target, track=True, chunk=True, scoop=True, keep_zip=inputs["keep_zip"],
                          identity_from_filename=inputs["identity_from_filename"],
                          exclude_dirs=(tuple(filters.dir_patterns) if apply_zip else ()) + tuple(inputs["extra_excludes"]),
                          exclude_files=tuple(filters.file_patterns) if apply_zip else (), **common)
    return RunOptions(target=target, track=inputs["track"], chunk=inputs["chunk"], scoop=inputs["scoop"],
                      exclude_dirs=tuple(filters.dir_patterns) + tuple(inputs["extra_excludes"]),
                      exclude_files=tuple(filters.file_patterns), delete_filtered=inputs["delete_filtered"],
                      delete_filtered_keep=tuple(inputs["keep_filtered"]),
                      confirm_delete_filtered=confirm_filtered, **common)


def _cap(items, n=300):
    items = list(items or [])
    return {"items": items[:n], "count": len(items), "truncated": len(items) > n}


def serialize_run(result) -> Dict[str, Any]:
    info, stats, scoop = result.info, result.stats, result.scoop
    return {
        "mode": result.mode, "dry_run": result.dry_run, "project_name": info.project_name,
        "project_version": info.project_version, "package_version": info.package_version,
        "versioned_uuid": info.versioned_uuid, "tracker_source": info.source, "registered": result.registered,
        "snapshot_file": str(result.snapshot_file) if result.snapshot_file else None,
        "stats": None if not stats else {
            "files": stats.files, "empty_dirs": stats.empty_dirs, "excluded_dirs": stats.excluded_dirs,
            "excluded_files": stats.excluded_files, "binary_files": stats.binary_files, "total_bytes": stats.total_bytes,
            "skipped": _cap(stats.skipped, 100), "ignored_binary": _cap(stats.ignored_binary, 100)},
        "binary_enabled": result.binary_enabled, "binary_source": result.binary_source,
        "filtered_matches": {"dirs": list(result.filtered_matches.get("dirs", [])),
                             "files": list(result.filtered_matches.get("files", []))},
        "scoop": None if not scoop else {
            "dry_run": scoop.dry_run, "removed": _cap(scoop.removed), "dirs_removed": scoop.dirs_removed,
            "root_removed": scoop.root_removed, "residual": _cap(scoop.residual, 100),
            "changed_since_snapshot": _cap(scoop.changed_since_snapshot, 100),
            "filtered_dirs": list(scoop.filtered_dirs), "filtered_files": list(scoop.filtered_files),
            "filtered_skipped": list(scoop.filtered_skipped)},
        "superseded": list(result.superseded), "identity_notes": list(info.notes),
        "warnings": list(result.warnings),
    }


def serialize_zip(zres) -> Dict[str, Any]:
    return {"dry_run": zres.dry_run, "zip": str(zres.zip_path), "sha256": zres.sha256, "files": zres.files,
            "zip_deleted": zres.zip_deleted, "zip_kept_reason": zres.zip_kept_reason or "",
            "fully_captured": zres.fully_captured, "warnings": list(zres.warnings),
            "run": serialize_run(zres.run) if zres.run else None}
