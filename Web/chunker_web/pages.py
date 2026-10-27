#!/usr/bin/env python3
"""
File Name:       pages.py
Relative Path:   Web/chunker_web/pages.py
Description:     Page routes and the sidebar section map of the unified web app (one Chunk page covers track/chunk/scoop/zip).
VERSION:         1.8.0
Author:          Elton Boehnen · boehnenelton2024@gmail.com · boehnenelton2024.pages.dev · github.com/boehnenelton
RELATIONAL_ID:   718f0577-2008-4c51-8d24-78afe3155d22
"""

from flask import redirect, render_template, url_for

from .core import Ctx

VERSION = "1.8.0"
# (group, [(key, label, href, enabled)])
SECTIONS = (
    ("Overview", (("dashboard", "Dashboard", "/", True), ("projects", "Projects", "/projects", True))),
    ("Work", (("chunk", "Chunk", "/chunk", True), ("restore", "Restore", "/restore", True),
              ("migrator", "Migrator", "/migrator", True))),
    ("Maintain", (("verify", "Verify", "/verify", True), ("filters", "Filters", "/filters", True),
                  ("jobs", "Jobs & Log", "/jobs", True))),
    ("App", (("settings", "Settings", "/settings", True), ("about", "About", "/about", True))),
)
# Old per-operation pages now live on the single Chunk page; the URLs redirect to the matching preset.
LEGACY = {"/track": "track", "/scoop": "scoop", "/zip": "zip"}
TITLES = {k: label for _g, items in SECTIONS for k, label, _h, _e in items}


def register_pages(app, ctx: Ctx, version: str) -> None:
    def make(key):
        def view():
            return render_template(f"chunker/{key}.html", page=key, title=TITLES[key], sections=SECTIONS,
                                   token=ctx.token, version=version, theme=ctx.webcfg.get("theme", "dark"))
        view.__name__ = f"page_{key}"
        return view

    for _group, items in SECTIONS:
        for key, _label, href, enabled in items:
            if enabled:
                app.add_url_rule(href, view_func=make(key))

    for path, preset in LEGACY.items():
        app.add_url_rule(path, endpoint=f"legacy_{preset}",
                         view_func=lambda preset=preset: redirect(url_for("page_chunk", mode=preset), code=302))
