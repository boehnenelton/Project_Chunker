#!/usr/bin/env python3
"""
File Name:       project_chunker_flask.py
Relative Path:   Web/project_chunker_flask.py
Description:     Launcher for the unified Project Chunker web app (dashboard, projects, one Chunk page for track/chunk/scoop/zip,
                 restore, migrator, verify, filters, jobs, settings).
                 Binds to localhost, picks the first free port from web_config.json port_start.
VERSION:         1.8.0
Author:          Elton Boehnen · boehnenelton2024@gmail.com · boehnenelton2024.pages.dev · github.com/boehnenelton
RELATIONAL_ID:   2906be2f-a606-43ea-bb3d-ee890bf91853
"""

import argparse
import socket
import sys
import webbrowser
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_PATH.parent
for sub in ("lib", "Web"):
    if str(PROJECT_ROOT / sub) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT / sub))

from chunker_web import create_app  # noqa: E402
from lib_bejson_Projects_logging import projects_logging_configure, projects_logging_get_logger  # noqa: E402
from lib_bejson_Projects_webconfig import projects_webconfig_ensure, projects_webconfig_load  # noqa: E402

VERSION = "1.8.0"
_log = projects_logging_get_logger("web.launcher")


def _free_port(host: str, start: int) -> int:
    for port in range(start, start + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            if sock.connect_ex((host, port)) != 0:
                return port
    raise SystemExit(f"no free port in {start}-{start + 19}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="project_chunker_flask", description="Project Chunker web UI.")
    parser.add_argument("--storage-root", help="override the chunker storage root")
    parser.add_argument("--host", help="bind address (default from config/web_config.json)")
    parser.add_argument("--port", type=int, help="port (default: first free from port_start)")
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args(argv)
    cfg = projects_webconfig_load(projects_webconfig_ensure(PROJECT_ROOT / "config"))
    projects_logging_configure((cfg.get("log_level") or "INFO").upper())
    host = args.host or cfg.get("bind_host", "127.0.0.1")
    port = args.port or _free_port(host, int(cfg.get("port_start", "5120")))
    if host not in ("127.0.0.1", "localhost"):
        _log.warning("bound to %s: the UI is reachable from the network", host)
    app = create_app(PROJECT_ROOT, args.storage_root)
    _log.info("serving on http://%s:%d", host, port)
    if args.open_browser or cfg.get("open_browser") == "true":
        webbrowser.open(f"http://{host}:{port}/")
    app.run(host=host, port=port, threaded=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
