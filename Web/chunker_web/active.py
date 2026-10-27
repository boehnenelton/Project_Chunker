#!/usr/bin/env python3
"""
File Name:       active.py
Relative Path:   Web/chunker_web/active.py
Description:     The persistent "active project" of the web UI. One project is chosen on the Projects page and stays
                 active (server-side, shared by every tab, across restarts) until changed or cleared; every other page
                 targets it by default: Chunk (its source folder), Restore (its versions), Verify (scope), Tracker.
                 The registry stays the source of truth: if the active project no longer exists it is cleared and the
                 UI is told. The routes are GET /api/active, POST /api/active {name}, DELETE /api/active.
VERSION:         1.8.0
Author:          Elton Boehnen · boehnenelton2024@gmail.com · boehnenelton2024.pages.dev · github.com/boehnenelton
RELATIONAL_ID:   0d7a2f5c-91b4-4c6e-a3f8-5b1e8c4d9a26
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import jsonify, request

from lib_bejson_Projects_active import (projects_active_clear, projects_active_get, projects_active_set,
                                        projects_active_valid_name)
from lib_bejson_Projects_logging import projects_logging_get_logger
from lib_bejson_Projects_view import projects_view_build

from .core import ApiError, Ctx

VERSION = "1.8.0"
_log = projects_logging_get_logger("web.active")


def _source_folder(ctx: Ctx, project: Dict[str, Any]) -> Optional[str]:
    """Newest version's recorded Original_Path that still exists as a folder inside the allowed roots, else None."""
    for card in project["versions"]:
        raw = str(card.get("Original_Path") or "").strip()
        if not raw or not Path(raw).is_dir():
            continue
        try:
            return str(ctx.confine(raw))
        except ApiError:
            continue
    return None


def active_resolve(ctx: Ctx, projects: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """{active: {...}|None, was: name|None}. A stored name the registry no longer has is cleared (and returned as `was`)."""
    name = projects_active_get(ctx.active_file)
    if not name:
        return {"active": None, "was": None}
    projects = projects if projects is not None else projects_view_build(ctx.layout)
    project = next((p for p in projects if p["name"] == name), None)
    if project is None:
        projects_active_clear(ctx.active_file)
        _log.info("active project %r is no longer in the registry; cleared", name)
        return {"active": None, "was": name}
    latest = project["versions"][0]
    source = _source_folder(ctx, project)
    return {"active": {
        "name": name, "version_count": project["version_count"], "latest_status": project["latest_status"],
        "latest": {"Versioned_UUID": latest["Versioned_UUID"], "Version_Label": latest["Version_Label"],
                   "Status": latest["Status"]},
        "versions": [{"Versioned_UUID": c["Versioned_UUID"], "Version_Label": c["Version_Label"], "Status": c["Status"]}
                     for c in project["versions"]],
        "source_path": source, "source_exists": source is not None}, "was": None}


def active_name(ctx: Ctx) -> Optional[str]:
    info = active_resolve(ctx)["active"]
    return info["name"] if info else None


def active_source(ctx: Ctx) -> Optional[str]:
    info = active_resolve(ctx)["active"]
    return info["source_path"] if info else None


def active_follow_rename(ctx: Ctx, old: str, new: str) -> None:
    """A tracker edit renamed the project: keep it active under its new name."""
    if old != new and projects_active_get(ctx.active_file) == old:
        projects_active_set(ctx.active_file, new)


def register_active(app, ctx: Ctx) -> None:
    @app.get("/api/active")
    def api_active_get():
        return jsonify({"ok": True, **active_resolve(ctx)})

    @app.post("/api/active")
    def api_active_set():
        body = request.get_json(silent=True)
        name = str((body or {}).get("name", "")).strip() if isinstance(body, dict) else ""
        if not projects_active_valid_name(name):
            raise ApiError("choose a project")
        projects = projects_view_build(ctx.layout)
        if not any(p["name"] == name for p in projects):
            raise ApiError(f"no project named {name!r} in this storage", 404)
        if not projects_active_set(ctx.active_file, name):
            raise ApiError("could not save the active project", 500)
        return jsonify({"ok": True, **active_resolve(ctx, projects)})

    @app.delete("/api/active")
    def api_active_clear():
        if not projects_active_clear(ctx.active_file):
            raise ApiError("could not clear the active project", 500)
        return jsonify({"ok": True, "active": None, "was": None})
