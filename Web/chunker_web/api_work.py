#!/usr/bin/env python3
"""
File Name:       api_work.py
Relative Path:   Web/chunker_web/api_work.py
Description:     Action endpoints of the web app: previews (read-only dry runs that mint a plan_id), live runs as
                 background jobs (track, chunk, scoop, zip intake, restore, verify, migrator) and job polling.
                 Destructive runs need a matching plan_id plus typed confirmations.
VERSION:         1.8.0
Author:          Elton Boehnen · boehnenelton2024@gmail.com · boehnenelton2024.pages.dev · github.com/boehnenelton
RELATIONAL_ID:   62de16a9-d11b-4082-a71d-4d0bef061cb7
"""

from pathlib import Path
from typing import Any, Dict

from flask import jsonify, request

from lib_bejson_Projects_edit import (
    ProjectsEditError,
    projects_edit_apply,
    projects_edit_normalize,
    projects_edit_plan,
)
from lib_bejson_Projects_migrate import (
    projects_migrate_chunk_phase,
    projects_migrate_config_ensure,
    projects_migrate_verify_phase,
)
from lib_bejson_Projects_pipeline import (
    projects_pipeline_restore,
    projects_pipeline_restore_plan,
    projects_pipeline_run,
    projects_pipeline_run_zip,
)
from lib_bejson_Projects_verify import projects_verify_storage

from .active import active_follow_rename, active_name, active_resolve
from .core import (
    DELETE_FILTERED_PHRASE,
    ApiError,
    Ctx,
    build_options,
    parse_run_inputs,
    serialize_run,
    serialize_zip,
)
from .jobs import JobRunner

VERSION = "1.8.0"
RUN_OPS = ("track", "chunk", "scoop")


def _body() -> Dict[str, Any]:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _tracker_inputs(ctx: Ctx, body: Dict[str, Any]) -> Dict[str, Any]:
    name = str(body.get("name", "")).strip() or (active_name(ctx) or "")      # default: the active project
    changes = body.get("changes")
    if not name:
        raise ApiError("choose a project, or make one active on the Projects page")
    if not isinstance(changes, dict) or not changes:
        raise ApiError("nothing to change")
    changes = dict(changes)
    if isinstance(changes.get("Original_Path"), str) and changes["Original_Path"].strip():
        changes["Original_Path"] = str(ctx.confine(changes["Original_Path"].strip(), must_exist=False))
    try:
        clean = projects_edit_normalize(changes)
    except ProjectsEditError as exc:
        raise ApiError(str(exc))
    return {"op": "tracker", "name": name, "changes": clean, "update_snapshots": body.get("update_snapshots") is not False}


def _restore_inputs(ctx: Ctx, body: Dict[str, Any]) -> Dict[str, Any]:
    selector = str(body.get("selector", "")).strip()
    if not selector:                                         # default: the active project's newest version
        info = active_resolve(ctx)["active"]
        selector = info["latest"]["Versioned_UUID"] if info else ""
    if not selector:
        raise ApiError("select a version, or make a project active on the Projects page")
    dest = str(ctx.confine(body["dest"], must_exist=False)) if body.get("dest") else ""
    return {"op": "restore", "selector": selector, "dest": dest, "overwrite": body.get("overwrite") is True,
            "write_tracker": body.get("write_tracker") is True}


def _restore_dest(ctx: Ctx, inputs: Dict[str, Any]):
    if inputs["dest"]:
        return Path(inputs["dest"])
    return None


def register_work(app, ctx: Ctx, jobs: JobRunner) -> None:
    @app.post("/api/preview/<op>")
    def api_preview(op):
        body = _body()
        if op in RUN_OPS:
            inputs = parse_run_inputs(ctx, body, op)
            if Path(inputs["target"]).is_file():
                raise ApiError("a file target is a zip: choose Zip file as the source")
            result = serialize_run(projects_pipeline_run(ctx.layout, build_options(ctx, inputs, True)))
            meta = {"project_name": result["project_name"]}
        elif op == "zip":
            inputs = parse_run_inputs(ctx, body, "zip")
            if not Path(inputs["target"]).is_file():
                raise ApiError("pick a .zip file")
            result = serialize_zip(projects_pipeline_run_zip(ctx.layout, build_options(ctx, inputs, True)))
            meta = {"zip": inputs["target"]}
        elif op == "tracker":
            inputs = _tracker_inputs(ctx, body)
            try:
                result = projects_edit_plan(ctx.layout, inputs["name"], inputs["changes"], inputs["update_snapshots"])
            except ProjectsEditError as exc:
                raise ApiError(str(exc))
            meta = {"project_name": inputs["name"]}
        elif op == "restore":
            inputs = _restore_inputs(ctx, body)
            result = projects_pipeline_restore_plan(ctx.layout, inputs["selector"], _restore_dest(ctx, inputs),
                                                    inputs["overwrite"])
            if result.get("destination") and not inputs["dest"]:
                ctx.confine(str(result["destination"]), must_exist=False)
            meta = {"project_name": result["project_name"]}
        else:
            raise ApiError("unknown operation", 404)
        return jsonify({"ok": True, "op": op, "plan_id": ctx.plan_store(op, inputs, meta), "result": result})

    def _start(op, label, fn, cancellable=False, summarize=None):
        job = jobs.start(op, label, fn, cancellable, summarize)
        if job is None:
            raise ApiError("a job is already running", 409)
        return jsonify({"ok": True, "job_id": job["id"]})

    @app.post("/api/run/<op>")
    def api_run(op):
        body = _body()
        if op in RUN_OPS:
            inputs = parse_run_inputs(ctx, body, op)
            if Path(inputs["target"]).is_file():
                raise ApiError("a file target is a zip: choose Zip file as the source")
            confirm_filtered = False
            if op == "scoop":
                meta = ctx.plan_check(op, inputs, str(body.get("plan_id", "")))
                if str(body.get("confirm_name", "")) != meta["project_name"]:
                    raise ApiError("type the project name exactly to confirm", 400)
                if body.get("confirm_backup") is not True:
                    raise ApiError("confirm that the project is backed up", 400)
                if inputs["delete_filtered"]:
                    if body.get("confirm_filtered_text") != DELETE_FILTERED_PHRASE:
                        raise ApiError(f"type {DELETE_FILTERED_PHRASE} to confirm deleting filtered content", 400)
                    confirm_filtered = True
            label = f"{op}: {Path(inputs['target']).name}"
            return _start(op, label, lambda emit, _c: serialize_run(projects_pipeline_run(
                ctx.layout, build_options(ctx, inputs, False, emit, confirm_filtered))),
                summarize=lambda r: f"{r['project_name']} V{r['project_version']}-PKG{r['package_version']}")
        if op == "zip":
            inputs = parse_run_inputs(ctx, body, "zip")
            if not Path(inputs["target"]).is_file():
                raise ApiError("pick a .zip file")
            ctx.plan_check("zip", inputs, str(body.get("plan_id", "")))
            if not inputs["keep_zip"] and body.get("confirm") is not True:
                raise ApiError("confirm that the zip may be deleted after a verified capture", 400)
            return _start("zip", f"zip: {Path(inputs['target']).name}", lambda emit, _c: serialize_zip(
                projects_pipeline_run_zip(ctx.layout, build_options(ctx, inputs, False, emit))),
                summarize=lambda r: ("zip deleted" if r["zip_deleted"] else "zip kept") + f", {r['files']} files")
        if op == "restore":
            inputs = _restore_inputs(ctx, body)
            ctx.plan_check("restore", inputs, str(body.get("plan_id", "")))
            if body.get("confirm") is not True:
                raise ApiError("confirm the restore", 400)

            def work(emit, _c):
                emit({"stage": "restore", "label": "writing files"})
                out = projects_pipeline_restore(ctx.layout, inputs["selector"], _restore_dest(ctx, inputs),
                                                inputs["overwrite"], inputs["write_tracker"])
                r = out.restore
                return {"versioned_uuid": out.versioned_uuid, "project_name": out.project_name,
                        "to_original": out.to_original, "destination": str(r.destination), "files": r.files,
                        "dirs": r.dirs, "mtimes_applied": r.mtimes_applied, "tracker_written": r.tracker_written,
                        "warnings": list(r.warnings)}
            return _start("restore", f"restore: {inputs['selector'][:8]}", work,
                          summarize=lambda r: f"{r['files']} files -> {r['destination']}")
        if op == "tracker":
            inputs = _tracker_inputs(ctx, body)
            ctx.plan_check("tracker", inputs, str(body.get("plan_id", "")))
            if body.get("confirm") is not True:
                raise ApiError("tick the confirmation box to apply the changes", 400)

            def work(emit, _c):
                try:
                    out = projects_edit_apply(ctx.layout, inputs["name"], inputs["changes"], inputs["update_snapshots"], emit)
                    active_follow_rename(ctx, inputs["name"], out["new_name"])      # a renamed project stays active
                    return out
                except ProjectsEditError as exc:
                    raise RuntimeError(str(exc))
            return _start("tracker", f"tracker: {inputs['name']}", work,
                          summarize=lambda r: f"{r['new_name']}: {r['versions_updated']}/{r['versions']} versions, {', '.join(r['fields'])}")
        if op == "verify":
            scope = str(body.get("scope", "all") or "all")           # "all" (default, unchanged) or "active"
            if scope not in ("all", "active"):
                raise ApiError("scope must be all or active")
            scope_project = None
            if scope == "active":
                scope_project = active_name(ctx)
                if not scope_project:
                    raise ApiError("scope is the active project, but none is active: make one active on the Projects page")

            def work(emit, _c):
                emit({"stage": "verify", "label": "checking storage" + (f" (project {scope_project})" if scope_project else "")})
                issues = projects_verify_storage(ctx.layout, scope_project)
                errors = sum(1 for i in issues if i.severity == "error")
                return {"issues": [i.__dict__ for i in issues], "errors": errors, "warnings": len(issues) - errors}
            return _start("verify", "verify " + (scope_project or "storage"), work,
                          summarize=lambda r: f"{r['errors']} error(s), {r['warnings']} warning(s)")
        if op == "migrator":
            phases = [p for p in ("chunk", "verify") if p in (body.get("phases") or [])]
            if not phases:
                raise ApiError("select at least one phase")
            cfg, _ = projects_migrate_config_ensure(ctx.migrator_config)
            if not cfg.dry_run and body.get("confirm") is not True:
                raise ApiError("a live run needs the confirmation box ticked")

            def work(emit, cancelled):
                out = []
                for phase in phases:
                    if cancelled():
                        break
                    run_cfg, _ = projects_migrate_config_ensure(ctx.migrator_config)
                    res = (projects_migrate_chunk_phase(ctx.layout, run_cfg, ctx.legacy_config, emit, cancelled,
                                                        filters_file=ctx.filters_path) if phase == "chunk"
                           else projects_migrate_verify_phase(ctx.layout, run_cfg, emit, cancelled))
                    out.append({"phase": phase, "dry_run": res.dry_run, "counts": res.counts,
                                "entries": res.entries[:200], "report": str(res.report_file or ""),
                                "stopped_early": res.stopped_early})
                return {"phases": out, "dry_run": cfg.dry_run}
            return _start("migrator", "migrator: " + "+".join(phases), work, cancellable=True,
                          summarize=lambda r: ", ".join(f"{p['phase']} {sum(p['counts'].values())}" for p in r["phases"]))
        raise ApiError("unknown operation", 404)

    @app.get("/api/jobs")
    def api_jobs():
        cur = jobs.current()
        return jsonify({"ok": True, "current": jobs.view(cur, False) if cur else None, "history": jobs.history()[::-1]})

    @app.get("/api/jobs/<job_id>")
    def api_job(job_id):
        job = jobs.get(job_id)
        if not job:
            raise ApiError("unknown job (finished jobs survive only in the journal after a restart)", 404)
        return jsonify({"ok": True, "job": jobs.view(job)})

    @app.post("/api/jobs/<job_id>/cancel")
    def api_cancel(job_id):
        if not jobs.cancel(job_id):
            raise ApiError("this job cannot be cancelled", 409)
        return jsonify({"ok": True})
