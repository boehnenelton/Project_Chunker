#!/usr/bin/env python3
"""
File Name:       project_management_flask.py
Relative Path:   Web/project_management_flask.py
Description:     Project_Management: read-only Flask viewer for the chunker registry. Each project is a collapsible
                 list item; expanding it shows one card per registered version. Logic lives in
                 lib_bejson_Projects_view; this file only serves it.
VERSION:         1.8.0
Author:          Elton Boehnen · boehnenelton2024@gmail.com · boehnenelton2024.pages.dev · github.com/boehnenelton
RELATIONAL_ID:   f15005e7-ffb1-439f-8a04-f89a6f0301fb
"""

import argparse
import sys
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_PATH.parent
LIB_DIR = PROJECT_ROOT / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from flask import Flask, jsonify, render_template_string  # noqa: E402

from lib_bejson_Projects_logging import projects_logging_configure, projects_logging_get_logger  # noqa: E402
from lib_bejson_Projects_paths import projects_paths_resolve_storage_layout  # noqa: E402
from lib_bejson_Projects_view import projects_view_build  # noqa: E402

VERSION = "1.8.0"
_log = projects_logging_get_logger("web")

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Project Management</title>
<style>
:root{--bg:#fff;--fg:#000;--accent:#DE2626}
@media (prefers-color-scheme:dark){:root{--bg:#000;--fg:#fff}}
body{background:var(--bg);color:var(--fg);font:16px/1.4 system-ui,sans-serif;margin:0;padding:16px;max-width:900px;margin-inline:auto}
h1{margin:0 0 4px}.sub{opacity:.7;margin-bottom:16px}
details.project{border:2px solid var(--fg);margin:8px 0}
details.project>summary{cursor:pointer;padding:10px 12px;font-weight:700;list-style:none;display:flex;gap:10px;align-items:center}
details.project>summary::-webkit-details-marker{display:none}
details.project>summary::before{content:"\\25B8";color:var(--accent)}
details[open].project>summary::before{content:"\\25BE"}
details.project>summary:hover{color:var(--accent)}
.count{margin-left:auto;font-weight:400}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:10px;padding:10px;border-top:2px solid var(--fg)}
.card{border:1px solid var(--fg);padding:10px;word-break:break-word}
.card h3{margin:0 0 6px;font-size:1rem}.card:hover{border-color:var(--accent)}
.st{display:inline-block;border:1px solid var(--accent);color:var(--accent);padding:0 6px;font-size:.8rem}
dl{margin:6px 0 0;font-size:.85rem}dt{opacity:.7}dd{margin:0 0 4px}
.empty{padding:24px;border:2px dashed var(--fg)}
footer{margin-top:24px;font-size:.8rem;opacity:.7}footer a{color:var(--accent)}
</style></head><body>
<h1>Project Management</h1>
<div class="sub">{{ projects|length }} project(s) &middot; read-only &middot; {{ total }} version(s)</div>
{% for p in projects %}
<details class="project"><summary><span>{{ p.name }}</span><span class="st">{{ p.latest_status }}</span>
<span class="count">{{ p.version_count }} version{{ '' if p.version_count == 1 else 's' }}</span></summary>
<div class="cards">
{% for v in p.versions %}
<div class="card"><h3>{{ v.Version_Label }} <span class="st">{{ v.Status }}</span></h3>
<dl><dt>UUID</dt><dd>{{ v.Versioned_UUID }}</dd>
<dt>Original location</dt><dd>{{ v.Original_Path or '-' }}</dd>
<dt>Snapshot</dt><dd>{% if v.snapshot_state == 'ok' %}{{ v.snapshot_name }} &middot; {{ v.files }} file(s) &middot; {{ v.snapshot_bytes }} bytes{% else %}{{ v.snapshot_state }}{% endif %}</dd>
{% if v.snapshot_state == 'ok' %}<dt>Snapshot date</dt><dd>{{ v.snapshot_date or '-' }}</dd>
<dt>Binary_Enabled</dt><dd>{{ 'n/a (legacy)' if v.binary_enabled is none else v.binary_enabled }}</dd>{% endif %}
<dt>Updated</dt><dd>{{ v.Updated_At or '-' }}</dd></dl></div>
{% endfor %}
</div></details>
{% else %}<div class="empty">No projects registered. Run the chunker with --track.</div>{% endfor %}
<footer>Elton Boehnen &middot; boehnenelton2024@gmail.com &middot; boehnenelton2024.pages.dev &middot;
<a href="https://github.com/boehnenelton">github.com/boehnenelton</a></footer>
</body></html>"""


def create_app(storage_root=None) -> Flask:
    app = Flask(__name__)
    layout = projects_paths_resolve_storage_layout(PROJECT_ROOT, storage_root)

    @app.get("/")
    def index():
        projects = projects_view_build(layout)
        return render_template_string(PAGE, projects=projects, total=sum(p["version_count"] for p in projects))

    @app.get("/api/projects")
    def api_projects():
        return jsonify(projects_view_build(layout))

    return app


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="project_management_flask", description="Read-only project viewer.")
    parser.add_argument("--storage-root", help="override storage root (default: <project>/storage or env)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5055)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)
    projects_logging_configure(args.log_level.upper())
    _log.info("serving on http://%s:%d", args.host, args.port)
    create_app(args.storage_root).run(host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
