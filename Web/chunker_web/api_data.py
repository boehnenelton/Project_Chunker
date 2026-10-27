#!/usr/bin/env python3
"""
File Name:       api_data.py
Relative Path:   Web/chunker_web/api_data.py
Description:     Read/config endpoints of the web app: meta, dashboard, folder picker, projects and their files,
                 version export/download, the filter editor, settings and the migrator configuration/plan/ledger.
VERSION:         1.8.0
Author:          Elton Boehnen · boehnenelton2024@gmail.com · boehnenelton2024.pages.dev · github.com/boehnenelton
RELATIONAL_ID:   6b0731fa-a823-417d-ad91-2967e05e5cee
"""

import re
from typing import Any, Dict

from flask import jsonify, request, send_from_directory

from lib_bejson_Projects_filters import (
    projects_filters_match,
    projects_filters_read_rows,
    projects_filters_split,
    projects_filters_write_rows,
)
from lib_bejson_Projects_migrate import (
    BOOL_DEFAULTS,
    BOOL_HELP,
    RADIO_HELP,
    RADIO_OPTIONS,
    TEXT_HELP,
    MigratorConfig,
    ProjectsMigrateError,
    ledger_path,
    projects_migrate_config_ensure,
    projects_migrate_config_problems,
    projects_migrate_config_save,
    projects_migrate_ledger_load,
    projects_migrate_resolve_source,
    projects_migrate_scan,
)
from lib_bejson_Projects_edit import ProjectsEditError, projects_edit_details
from lib_bejson_Projects_view import (
    projects_view_build,
    projects_view_export_zip,
    projects_view_file_preview,
    projects_view_files,
)
from lib_bejson_Projects_webconfig import SETTINGS, projects_webconfig_load, projects_webconfig_save

from .active import active_resolve
from .core import ApiError, Ctx
from .jobs import JobRunner
from .picker import picker_list

VERSION = "1.8.0"
UUID_RE = re.compile(r"^[0-9a-fA-F-]{8,36}$")


def _body() -> Dict[str, Any]:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _uuid(value: str) -> str:
    if not UUID_RE.match(value or ""):
        raise ApiError("bad version id")
    return value


def register_data(app, ctx: Ctx, jobs: JobRunner, version: str) -> None:
    @app.get("/api/meta")
    def api_meta():
        return jsonify({"ok": True, "version": version, "storage_root": str(ctx.layout.storage_root),
                        "roots": [str(r) for r in ctx.roots()], "theme": ctx.webcfg.get("theme", "dark"),
                        "max_file_mb": ctx.webcfg.get("max_file_mb", "25"), "delete_filtered_phrase": "DELETE FILTERED"})

    @app.get("/api/dashboard")
    def api_dashboard():
        projects = projects_view_build(ctx.layout)
        status: Dict[str, int] = {}
        versions = 0
        for proj in projects:
            for card in proj["versions"]:
                versions += 1
                status[card["Status"]] = status.get(card["Status"], 0) + 1
        rows = projects_filters_read_rows(ctx.filters_path)
        migr = projects_migrate_ledger_load(ctx.layout)
        return jsonify({"ok": True, "projects": len(projects), "versions": versions, "status": status,
                        "filters_on": sum(1 for r in rows if r["group"] != "switch" and r["enabled"] is True),
                        "filters_total": sum(1 for r in rows if r["group"] != "switch"),
                        "ledger": len(migr), "storage_root": str(ctx.layout.storage_root),
                        "recent": jobs.history()[::-1][:5], "running": bool(jobs.current()),
                        "active": active_resolve(ctx, projects)["active"]})

    @app.get("/api/browse")
    def api_browse():
        return jsonify(picker_list(ctx, request.args.get("path", ""), request.args.get("files") == "1",
                                   request.args.get("suffix", "")))

    # ── projects ────────────────────────────────────────────────────────────────────────────────────────
    @app.get("/api/projects")
    def api_projects():
        projects = projects_view_build(ctx.layout)
        return jsonify({"ok": True, "projects": projects,
                        "active": (active_resolve(ctx, projects)["active"] or {}).get("name")})

    @app.get("/api/projects/<name>/tracker")
    def api_tracker_details(name):
        try:
            return jsonify({"ok": True, **projects_edit_details(ctx.layout, name)})
        except ProjectsEditError as exc:
            raise ApiError(str(exc), 404)

    @app.get("/api/projects/<uuid>/files")
    def api_files(uuid):
        out = projects_view_files(ctx.layout, _uuid(uuid), request.args.get("q", ""), 2000)
        return jsonify({"ok": True, **out})

    @app.get("/api/projects/<uuid>/file")
    def api_file(uuid):
        out = projects_view_file_preview(ctx.layout, _uuid(uuid), request.args.get("path", ""))
        return jsonify({"ok": True, **out})

    @app.post("/api/projects/<uuid>/export")
    def api_export(uuid):
        path = projects_view_export_zip(ctx.layout, _uuid(uuid), ctx.exports_dir)
        return jsonify({"ok": True, "name": path.name, "size": path.stat().st_size})

    @app.get("/api/download/<name>")
    def api_download(name):
        return send_from_directory(ctx.exports_dir, name, as_attachment=True)

    # ── filters ─────────────────────────────────────────────────────────────────────────────────────────
    @app.get("/api/filters")
    def api_filters():
        return jsonify({"ok": True, "rows": projects_filters_read_rows(ctx.filters_path), "file": str(ctx.filters_path)})

    @app.post("/api/filters")
    def api_filters_save():
        rows = _body().get("rows")
        if not isinstance(rows, list):
            raise ApiError("rows must be a list")
        problems = projects_filters_write_rows(ctx.filters_path, rows)
        if problems:
            return jsonify({"ok": False, "problems": problems}), 400
        return jsonify({"ok": True, "rows": projects_filters_read_rows(ctx.filters_path)})

    @app.post("/api/filters/test")
    def api_filters_test():
        body = _body()
        patterns = projects_filters_split(body.get("patterns", ""))
        results = []
        for path in [str(p) for p in (body.get("paths") or [])][:200]:
            name = path.rstrip("/").rsplit("/", 1)[-1]
            results.append({"path": path, "match": projects_filters_match(name, path, patterns)})
        return jsonify({"ok": True, "results": results})

    @app.post("/api/filters/reset")
    def api_filters_reset():
        if _body().get("confirm") is not True:
            raise ApiError("confirm the reset")
        if ctx.filters_path.exists():
            ctx.filters_path.unlink()
        from lib_bejson_Projects_filters import projects_filters_ensure
        projects_filters_ensure(ctx.config_dir)
        return jsonify({"ok": True, "rows": projects_filters_read_rows(ctx.filters_path)})

    # ── settings ────────────────────────────────────────────────────────────────────────────────────────
    @app.get("/api/settings")
    def api_settings():
        values = projects_webconfig_load(ctx.webcfg_path)
        return jsonify({"ok": True, "values": values, "file": str(ctx.webcfg_path),
                        "spec": {k: {"kind": v[1], "description": v[2]} for k, v in SETTINGS.items()},
                        "restart_needed": ["port_start", "bind_host", "storage_root", "log_level", "open_browser"]})

    @app.post("/api/settings")
    def api_settings_save():
        problems = projects_webconfig_save(ctx.webcfg_path, _body().get("values") or {})
        if problems:
            return jsonify({"ok": False, "problems": problems}), 400
        ctx.webcfg = projects_webconfig_load(ctx.webcfg_path)
        return jsonify({"ok": True, "values": ctx.webcfg})

    # ── migrator ────────────────────────────────────────────────────────────────────────────────────────
    @app.get("/api/migrator/config")
    def api_mig_config():
        cfg, _ = projects_migrate_config_ensure(ctx.migrator_config)
        return jsonify({"ok": True, "config": cfg.as_dict(), "bool_help": BOOL_HELP, "radio_help": RADIO_HELP,
                        "radio_options": RADIO_OPTIONS, "text_help": TEXT_HELP, "bool_keys": list(BOOL_DEFAULTS)})

    @app.post("/api/migrator/config")
    def api_mig_config_save():
        body = _body()
        cfg = MigratorConfig()
        for key in BOOL_DEFAULTS:
            cfg.flags[key] = body.get("flags", {}).get(key, cfg.flags[key])
        for key in RADIO_OPTIONS:
            cfg.radios[key] = body.get("radios", {}).get(key, cfg.radios[key])
        for key in cfg.texts:
            cfg.texts[key] = str(body.get("texts", {}).get(key, cfg.texts[key]))
        problems = projects_migrate_config_problems(cfg)
        if not problems:
            try:
                projects_migrate_config_save(ctx.migrator_config, cfg)
            except ProjectsMigrateError as exc:
                problems = [str(exc)]
        if problems:
            return jsonify({"ok": False, "problems": problems}), 400
        return jsonify({"ok": True, "config": cfg.as_dict()})

    @app.get("/api/migrator/source")
    def api_mig_source():
        cfg, _ = projects_migrate_config_ensure(ctx.migrator_config)
        try:
            src = projects_migrate_resolve_source(cfg)
            return jsonify({"ok": True, "path": str(src), "exists": src.is_dir(), "mode": cfg.radios["Source_Mode"]})
        except ProjectsMigrateError as exc:
            return jsonify({"ok": True, "path": "", "exists": False, "mode": cfg.radios["Source_Mode"], "error": str(exc)})

    @app.get("/api/migrator/plan")
    def api_mig_plan():
        cfg, _ = projects_migrate_config_ensure(ctx.migrator_config)
        src = projects_migrate_resolve_source(cfg)
        cands = projects_migrate_scan(cfg, src, ctx.layout, projects_migrate_ledger_load(ctx.layout))
        return jsonify({"ok": True, "source": str(src), "zips": [
            {"rel": c.rel, "size": c.size, "files": c.files, "valid": c.valid, "error": c.error,
             "status": c.ledger_status, "locked": c.locked} for c in cands]})

    @app.get("/api/migrator/ledger")
    def api_mig_ledger():
        rows = sorted(projects_migrate_ledger_load(ctx.layout).values(), key=lambda r: r["Zip_Rel"])
        return jsonify({"ok": True, "rows": rows, "file": str(ledger_path(ctx.layout))})
