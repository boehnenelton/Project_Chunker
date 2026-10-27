#!/usr/bin/env python3
"""
File Name:       project_migrator_flask.py
Relative Path:   Web/project_migrator_flask.py
Description:     Flask UI for the Project DB Migrator (BECSS Switch Core styling): hamburger side menu, section-based
                 pages (overview, configuration, plan, run, ledger, about), toggle switches for boolean settings,
                 radio boxes for choices, check boxes for phase selection and confirmation. Logic lives in
                 lib_bejson_Projects_migrate; this file serves it, runs one background job at a time, and requires
                 an explicit confirmation for live runs. Localhost only; every state-changing call needs the page token.
VERSION:         1.8.0
Author:          Elton Boehnen · boehnenelton2024@gmail.com · boehnenelton2024.pages.dev · github.com/boehnenelton
RELATIONAL_ID:   2c1dfe5c-ce19-44d9-9e59-c7b6b957544e
"""

import argparse
import hmac
import os
import secrets
import sys
import threading
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_PATH.parent
LIB_DIR = PROJECT_ROOT / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from flask import Flask, abort, jsonify, render_template, request  # noqa: E402

from lib_bejson_Projects_logging import projects_logging_configure, projects_logging_get_logger  # noqa: E402
from lib_bejson_Projects_migrate import (  # noqa: E402
    BOOL_DEFAULTS,
    BOOL_HELP,
    RADIO_HELP,
    RADIO_OPTIONS,
    TEXT_HELP,
    MigratorConfig,
    ProjectsMigrateError,
    ledger_path,
    projects_migrate_chunk_phase,
    projects_migrate_config_ensure,
    projects_migrate_config_problems,
    projects_migrate_config_save,
    projects_migrate_ledger_load,
    projects_migrate_resolve_source,
    projects_migrate_scan,
    projects_migrate_verify_phase,
)
from lib_bejson_Projects_paths import projects_paths_resolve_storage_layout  # noqa: E402

VERSION = "1.8.0"
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "migrator_config.json"
CHUNKER_CONFIG = PROJECT_ROOT / "config" / "config.json"
PHASES = ("chunk", "verify")
MAX_EVENTS = 400
_log = projects_logging_get_logger("migrator_web")

SECTIONS = (("overview", "Overview", "/"), ("config", "Configuration", "/config"), ("plan", "Plan", "/plan"),
            ("run", "Run", "/run"), ("ledger", "Ledger", "/ledger"), ("about", "About", "/about"))


class JobManager:
    """One background job at a time; progress events are kept for polling."""

    def __init__(self):
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self.reset()

    def reset(self):
        self.state, self.phases, self.events, self.results, self.error = "idle", [], [], [], ""
        self.current = {}

    def start(self, target, phases, dry_run):
        with self._lock:
            if self.state == "running":
                return False
            self.reset()
            self.state, self.phases, self.dry_run = "running", list(phases), dry_run
            self._cancel.clear()
        threading.Thread(target=target, daemon=True).start()
        return True

    def event(self, ev):
        self.current = ev
        if ev.get("event") != "scan":
            self.events.append(ev)
            del self.events[:-MAX_EVENTS]

    def cancelled(self):
        return self._cancel.is_set()

    def cancel(self):
        self._cancel.set()

    def snapshot(self):
        return {"state": self.state, "phases": self.phases, "dry_run": getattr(self, "dry_run", True),
                "current": self.current, "events": self.events[-60:], "results": self.results, "error": self.error}


def create_app(storage_root=None, config_file=None) -> Flask:
    app = Flask(__name__)
    app.config["MIGRATOR_TOKEN"] = secrets.token_hex(16)
    config_path = Path(config_file) if config_file else DEFAULT_CONFIG
    layout = projects_paths_resolve_storage_layout(PROJECT_ROOT, storage_root)
    jobs = JobManager()

    def load_cfg() -> MigratorConfig:
        cfg, _created = projects_migrate_config_ensure(config_path)
        return cfg

    def page(name, **ctx):
        cfg = load_cfg()
        return render_template(f"{name}.html", page=name, sections=SECTIONS, cfg=cfg, token=app.config["MIGRATOR_TOKEN"],
                               version=VERSION, **ctx)

    def guard():
        sent = request.headers.get("X-Migrator-Token", "")
        if not hmac.compare_digest(sent, app.config["MIGRATOR_TOKEN"]):
            abort(403)

    @app.after_request
    def no_store(resp):
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.get("/")
    def overview():
        rows = projects_migrate_ledger_load(layout)
        tally = {}
        for row in rows.values():
            tally[row["Status"]] = tally.get(row["Status"], 0) + 1
        return page("overview", tally=tally, total=len(rows), ledger_file=str(ledger_path(layout)))

    @app.get("/config")
    def config_page():
        return page("config", bool_help=BOOL_HELP, radio_help=RADIO_HELP, radio_options=RADIO_OPTIONS,
                    text_help=TEXT_HELP, bool_keys=list(BOOL_DEFAULTS))

    @app.get("/plan")
    def plan_page():
        return page("plan")

    @app.get("/run")
    def run_page():
        return page("run")

    @app.get("/ledger")
    def ledger_page():
        return page("ledger")

    @app.get("/about")
    def about_page():
        return page("about")

    @app.get("/api/config")
    def api_config_get():
        return jsonify(load_cfg().as_dict())

    @app.post("/api/config")
    def api_config_save():
        guard()
        body = request.get_json(silent=True) or {}
        cfg = MigratorConfig()
        for key in BOOL_DEFAULTS:
            cfg.flags[key] = body.get("flags", {}).get(key, cfg.flags[key])
        for key in RADIO_OPTIONS:
            cfg.radios[key] = body.get("radios", {}).get(key, cfg.radios[key])
        for key in cfg.texts:
            cfg.texts[key] = str(body.get("texts", {}).get(key, cfg.texts[key]))
        problems = projects_migrate_config_problems(cfg)
        if problems:
            return jsonify({"ok": False, "problems": problems}), 400
        try:
            projects_migrate_config_save(config_path, cfg)
        except ProjectsMigrateError as exc:
            return jsonify({"ok": False, "problems": [str(exc)]}), 400
        return jsonify({"ok": True, "config": cfg.as_dict()})

    @app.get("/api/source")
    def api_source():
        cfg = load_cfg()
        try:
            source = projects_migrate_resolve_source(cfg)
            return jsonify({"path": str(source), "exists": source.is_dir(), "mode": cfg.radios["Source_Mode"]})
        except ProjectsMigrateError as exc:
            return jsonify({"path": "", "exists": False, "mode": cfg.radios["Source_Mode"], "error": str(exc)})

    @app.get("/api/browse")
    def api_browse():
        raw = request.args.get("path") or ""
        try:
            base = Path(raw).expanduser() if raw else Path(os.environ.get("HOME", "/"))
            base = base.resolve()
            if not base.is_dir():
                return jsonify({"ok": False, "error": "not a folder"}), 400
            dirs = sorted(p.name for p in base.iterdir() if p.is_dir() and not p.is_symlink())[:500]
        except (OSError, PermissionError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify({"ok": True, "path": str(base), "parent": str(base.parent) if base.parent != base else "",
                        "dirs": dirs})

    @app.get("/api/plan")
    def api_plan():
        cfg = load_cfg()
        try:
            source = projects_migrate_resolve_source(cfg)
            cands = projects_migrate_scan(cfg, source, layout, projects_migrate_ledger_load(layout))
        except ProjectsMigrateError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify({"ok": True, "source": str(source), "zips": [
            {"rel": c.rel, "size": c.size, "files": c.files, "valid": c.valid, "error": c.error,
             "status": c.ledger_status, "locked": c.locked} for c in cands]})

    @app.get("/api/ledger")
    def api_ledger():
        rows = sorted(projects_migrate_ledger_load(layout).values(), key=lambda r: (r["Zip_Rel"]))
        return jsonify({"ok": True, "rows": rows})

    @app.post("/api/run")
    def api_run():
        guard()
        body = request.get_json(silent=True) or {}
        phases = [p for p in PHASES if p in (body.get("phases") or [])]
        if not phases:
            return jsonify({"ok": False, "error": "select at least one phase"}), 400
        cfg = load_cfg()
        if not cfg.dry_run and body.get("confirm") is not True:
            return jsonify({"ok": False, "error": "live run needs the confirmation box ticked"}), 400

        def work():
            try:
                for phase in phases:
                    if jobs.cancelled():
                        break
                    run_cfg = load_cfg()
                    result = (projects_migrate_chunk_phase(layout, run_cfg, CHUNKER_CONFIG, jobs.event, jobs.cancelled)
                              if phase == "chunk" else
                              projects_migrate_verify_phase(layout, run_cfg, jobs.event, jobs.cancelled))
                    jobs.results.append({"phase": phase, "dry_run": result.dry_run, "counts": result.counts,
                                         "entries": result.entries[:200], "report": str(result.report_file or ""),
                                         "stopped_early": result.stopped_early})
                jobs.state = "cancelled" if jobs.cancelled() else "done"
            except Exception as exc:                      # surfaced to the UI, never swallowed silently
                _log.error("job failed: %s", exc)
                jobs.error, jobs.state = str(exc), "failed"

        if not jobs.start(work, phases, cfg.dry_run):
            return jsonify({"ok": False, "error": "a job is already running"}), 409
        return jsonify({"ok": True, "dry_run": cfg.dry_run})

    @app.get("/api/job")
    def api_job():
        return jsonify(jobs.snapshot())

    @app.post("/api/cancel")
    def api_cancel():
        guard()
        jobs.cancel()
        return jsonify({"ok": True})

    return app


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="project_migrator_flask", description="Project DB Migrator web UI.")
    parser.add_argument("--storage-root", help="override the chunker storage root")
    parser.add_argument("--config", help="migrator config file (default: config/migrator_config.json)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5056)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)
    projects_logging_configure(args.log_level.upper())
    _log.info("serving on http://%s:%d", args.host, args.port)
    create_app(args.storage_root, args.config).run(host=args.host, port=args.port, threaded=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
