#!/usr/bin/env python3
"""
File Name:       __init__.py
Relative Path:   Web/chunker_web/__init__.py
Description:     App factory for the unified Project Chunker web app. Localhost only: Host-header check, per-process
                 token on every state-changing request, JSON errors, no-store caching, security headers.
VERSION:         1.8.0
Author:          Elton Boehnen · boehnenelton2024@gmail.com · boehnenelton2024.pages.dev · github.com/boehnenelton
RELATIONAL_ID:   4a21562e-14ff-49aa-8bdd-b5373abd0360
"""

import hmac
from pathlib import Path

from flask import Flask, jsonify, request

from lib_bejson_Projects_archive import ProjectsArchiveError
from lib_bejson_Projects_filters import ProjectsFiltersError
from lib_bejson_Projects_logging import projects_logging_get_logger
from lib_bejson_Projects_migrate import ProjectsMigrateError
from lib_bejson_Projects_paths import ProjectsPathError
from lib_bejson_Projects_pipeline import ProjectsPipelineError
from lib_bejson_Projects_registry import ProjectsRegistryError
from lib_bejson_Projects_snapshot import ProjectsSnapshotError
from lib_bejson_Projects_view import ProjectsViewError
from lib_bejson_Projects_zip import ProjectsZipError

from .active import register_active
from .api_data import register_data
from .api_work import register_work
from .core import ApiError, Ctx
from .jobs import JobRunner
from .pages import register_pages

VERSION = "1.8.0"
LIB_ERRORS = (ProjectsArchiveError, ProjectsFiltersError, ProjectsMigrateError, ProjectsPathError,
              ProjectsPipelineError, ProjectsRegistryError, ProjectsSnapshotError, ProjectsViewError, ProjectsZipError)
_log = projects_logging_get_logger("web")


def create_app(project_root, storage_root=None, config_dir=None, data_dir=None, extra_roots=()) -> Flask:
    web_dir = Path(__file__).resolve().parent.parent
    app = Flask(__name__, template_folder=str(web_dir / "templates"), static_folder=str(web_dir / "static"))
    ctx = Ctx(project_root, storage_root, config_dir, data_dir, extra_roots)
    jobs = JobRunner(ctx.data_dir / "web_jobs.bejson", int(ctx.webcfg.get("job_journal_size", "200")))
    app.config["CHUNKER_CTX"], app.config["CHUNKER_JOBS"] = ctx, jobs
    allowed_hosts = {"127.0.0.1", "localhost", "[::1]", ctx.webcfg.get("bind_host", "127.0.0.1")}

    @app.before_request
    def guard():
        host = request.host.rsplit(":", 1)[0] if not request.host.startswith("[") else request.host.split("]")[0] + "]"
        if host not in allowed_hosts:
            return jsonify({"ok": False, "error": "host not allowed"}), 403
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            sent = request.headers.get("X-Chunker-Token", "")
            if not hmac.compare_digest(sent, ctx.token):
                return jsonify({"ok": False, "error": "bad or missing token"}), 403
        return None

    @app.after_request
    def headers(resp):
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'"
        return resp

    @app.errorhandler(ApiError)
    def api_error(exc):
        return jsonify({"ok": False, "error": exc.message, **exc.extra}), exc.status

    for err in LIB_ERRORS:
        app.register_error_handler(err, lambda exc: (jsonify({"ok": False, "error": str(exc)}), 400))

    @app.errorhandler(404)
    def not_found(_exc):
        return jsonify({"ok": False, "error": "not found"}), 404

    @app.errorhandler(Exception)
    def unexpected(exc):
        from werkzeug.exceptions import HTTPException
        if isinstance(exc, HTTPException):
            return jsonify({"ok": False, "error": exc.description}), exc.code
        _log.exception("unhandled error: %s", exc)
        return jsonify({"ok": False, "error": "internal error (see log)"}), 500

    register_pages(app, ctx, VERSION)
    register_active(app, ctx)
    register_work(app, ctx, jobs)
    register_data(app, ctx, jobs, VERSION)
    return app
