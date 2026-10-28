#!/usr/bin/env python3
"""
File Name:       project_chunker_menu.py
Relative Path:   Menu/project_chunker_menu.py
Description:     Menu wrapper for the Project Chunker CLI. Exports config/menu_scripts.json (script locations
                 and their relative paths), then locates and runs CLI/project_chunker.py from that config.
                 Targets are chosen with a numbered folder browser - paths are never typed.
                 Commands: chunk+track, track, chunk, archive (chunk + verify + scoop the source), restore a
                 version, list versions, list registry, verify storage.
VERSION:         1.8.0
Author:          Elton Boehnen · boehnenelton2024@gmail.com · boehnenelton2024.pages.dev · github.com/boehnenelton
RELATIONAL_ID:   e3edc3e9-786e-4845-be05-7a1c0e445d48
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

if sys.version_info < (3, 10):
    sys.stderr.write("project_chunker_menu requires Python 3.10+\n")
    raise SystemExit(2)

SCRIPT_PATH = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_PATH.parent
sys.path.insert(0, str(PROJECT_ROOT / "lib"))

from lib_bejson_Projects_browser import projects_browser_pick_directory  # noqa: E402
from lib_bejson_Projects_config import (  # noqa: E402
    SCRIPTS_CONFIG_NAME,
    ProjectsConfigError,
    projects_config_ensure_standard,
    projects_config_export,
    projects_config_is_current,
    projects_config_load,
    projects_config_resolve_script,
)
from lib_bejson_Projects_filters import projects_filters_ensure  # noqa: E402
from lib_bejson_Projects_logging import (  # noqa: E402
    VALID_LEVELS,
    projects_logging_configure,
    projects_logging_get_logger,
)

VERSION = "1.8.0"
CLI_SCRIPT_ID = "project_chunker"
_log = projects_logging_get_logger("menu")

# command id -> (menu label, CLI flags, needs a target directory)
COMMANDS = {
    "chunk-track": ("Chunk + Track a directory", ["--track", "--chunk"], True),
    "track": ("Track a directory", ["--track"], True),
    "chunk": ("Chunk a directory", ["--chunk"], True),
    "scoop": ("Archive: chunk, verify, then remove the source", ["--scoop"], True),
    "restore": ("Restore / deploy a version", ["--restore"], False),
    "versions": ("List versions of a project", ["--versions"], False),
    "list": ("List registry", ["--list"], False),
    "verify": ("Verify storage", ["--verify"], False),
    "intake-zip": ("Intake / convert a .zip archive (prompt for project name)", ["--prompt-name"], True),
}
MENU_ORDER = ["chunk-track", "track", "chunk", "scoop", "restore", "versions", "list", "verify", "intake-zip"]


def _write(text: str) -> None:
    sys.stdout.write(text)
    sys.stdout.flush()


def _read(prompt: str) -> str:
    _write(prompt)
    line = sys.stdin.readline()
    if line == "":
        raise EOFError
    return line.rstrip("\n")


# Loads the exported config, (re)exporting it first when it is missing or points at scripts that moved.
def load_or_export_config(config_path: Path, force_export: bool = False) -> dict:
    scripts = None
    if not force_export and config_path.is_file():
        try:
            scripts = projects_config_load(config_path)
        except ProjectsConfigError as exc:
            _log.warning("config unusable (%s); re-exporting", exc)
    if scripts is None or not projects_config_is_current(PROJECT_ROOT, scripts):
        projects_config_export(PROJECT_ROOT, SCRIPT_PATH, config_path)
        projects_config_ensure_standard(config_path.parent)
        projects_filters_ensure(config_path.parent)
        scripts = projects_config_load(config_path)
    return scripts


# Builds the CLI invocation for a menu command from the config-located script.
def build_cli_command(scripts: dict, command_id: str, target, storage_root, log_level, dry_run, extra=()) -> list:
    if CLI_SCRIPT_ID not in scripts:
        raise ProjectsConfigError(f"config has no '{CLI_SCRIPT_ID}' script")
    cli = projects_config_resolve_script(PROJECT_ROOT, scripts[CLI_SCRIPT_ID])
    _label, flags, needs_target = COMMANDS[command_id]
    cmd = [sys.executable, str(cli)]
    if needs_target:
        cmd.append(str(target))
    cmd += flags + [str(item) for item in extra] + ["--log-level", log_level]
    if storage_root:
        cmd += ["--storage-root", str(storage_root)]
    if dry_run and needs_target:
        cmd.append("--dry-run")
    return cmd


def run_command(scripts: dict, command_id: str, target, args, extra=()) -> int:
    cmd = build_cli_command(scripts, command_id, target, args.storage_root, args.log_level, args.dry_run, extra)
    _log.info("running: %s", " ".join(cmd))
    return subprocess.run(cmd, check=False).returncode


# Registry rows from the CLI (--list --json); [] when empty or unreadable.
def fetch_registry(scripts: dict, args) -> list:
    cmd = build_cli_command(scripts, "list", None, args.storage_root, "ERROR", False) + ["--json"]
    done = subprocess.run(cmd, capture_output=True, text=True, check=False)
    try:
        return json.loads(done.stdout).get("registry", [])
    except ValueError:
        return []


# Numbered choice from `items` (labels); returns the index or None on cancel/EOF. Digits only - nothing typed.
def pick_from_list(title: str, labels: list):
    _write(f"\n{title}\n")
    for number, label in enumerate(labels, start=1):
        _write(f"  {number:>2}) {label}\n")
    _write("   0) cancel\n")
    try:
        choice = _read("> ").strip()
    except EOFError:
        return None
    return int(choice) - 1 if choice.isdigit() and 1 <= int(choice) <= len(labels) else None


def _entry_label(row: dict) -> str:
    return (f"{row['Project_Name']} V{row.get('Project_Version', '')}-PKG{row.get('Package_Version', '')}  "
            f"[{row.get('Status', '')}]  {row['Versioned_UUID'][:8]}")


# Interactive restore: pick a registered version, then original location or a browsed folder.
def interactive_restore(scripts: dict, args, browse_start: Path) -> int:
    rows = fetch_registry(scripts, args)
    if not rows:
        _write("registry is empty\n")
        return 0
    index = pick_from_list("Restore which version?", [_entry_label(r) for r in rows])
    if index is None:
        return 0
    row = rows[index]
    where = pick_from_list("Restore to?", [f"original location ({row.get('Original_Path') or 'not recorded'})",
                                          "another folder (browse)"])
    if where is None:
        return 0
    extra = [row["Versioned_UUID"]]
    if where == 1 or not row.get("Original_Path"):
        dest = projects_browser_pick_directory(browse_start, _read, _write)
        if dest is None:
            return 0
        extra += ["--dest", str(dest)]
    return run_command(scripts, "restore", None, args, extra)


def confirm(question: str) -> bool:
    try:
        return _read(f"{question} [y/N] ").strip().lower() == "y"
    except EOFError:
        return False


def interactive(scripts: dict, args) -> int:
    browse_start = Path(args.browse_root) if args.browse_root else Path.cwd()
    while True:
        _write(f"\nProject Chunker Menu v{VERSION}\n")
        for number, command_id in enumerate(MENU_ORDER, start=1):
            _write(f"  {number}) {COMMANDS[command_id][0]}\n")
        export_number = len(MENU_ORDER) + 1
        _write(f"  {export_number}) Re-export menu config\n  0) Exit\n")
        try:
            choice = _read("> ").strip()
        except EOFError:
            return 0
        if choice == "0":
            return 0
        if choice == str(len(MENU_ORDER) + 1):
            scripts = load_or_export_config(Path(args.config), force_export=True)
            _write(f"exported {args.config}\n")
            continue
        if not (choice.isdigit() and 1 <= int(choice) <= len(MENU_ORDER)):
            _write("?\n")
            continue
        command_id = MENU_ORDER[int(choice) - 1]
        if command_id == "restore":
            code = interactive_restore(scripts, args, browse_start)
            _write(f"[exit {code}]\n")
            continue
        if command_id == "versions":
            rows = fetch_registry(scripts, args)
            names = sorted({r["Project_Name"] for r in rows})
            index = pick_from_list("Which project?", names) if names else None
            if index is None:
                _write("registry is empty\n" if not names else "")
                continue
            _write(f"[exit {run_command(scripts, 'versions', None, args, [names[index]])}]\n")
            continue
        target = None
        if COMMANDS[command_id][2]:
            include_ext = ".zip" if command_id == "intake-zip" else None
            target = projects_browser_pick_directory(browse_start, _read, _write, include_ext=include_ext)
            if target is None:
                continue
            browse_start = target.parent if target.is_file() else target
            _write(f"\ntarget: {target}\n")
            if command_id == "scoop" and not args.dry_run and not confirm(
                    "This verifies the archive and then REMOVES the verified source files. Continue?"):
                continue
        code = run_command(scripts, command_id, target, args)
        _write(f"[exit {code}]\n")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="project_chunker_menu", description="Menu wrapper for the Project Chunker CLI.")
    parser.add_argument("--export-config", action="store_true", help="write the script config and exit")
    parser.add_argument("--run", choices=sorted(COMMANDS), help="run one command non-interactively")
    parser.add_argument("--target", help="target directory for --run chunk-track/track/chunk/scoop")
    parser.add_argument("--uuid", help="registry UUID (or 8+ char prefix) for --run restore")
    parser.add_argument("--project", help="project name for --run versions")
    parser.add_argument("--dest", help="destination for --run restore (default: recorded original path)")
    parser.add_argument("--overwrite", action="store_true", help="--run restore: allow overwriting files")
    parser.add_argument("--write-tracker", action="store_true", help="--run restore: write bejson_project.json")
    parser.add_argument("--config", default=str(PROJECT_ROOT / "config" / SCRIPTS_CONFIG_NAME),
                        help="script config path (default: config/%s)" % SCRIPTS_CONFIG_NAME)
    parser.add_argument("--browse-root", help="folder the browser starts in (default: current directory)")
    parser.add_argument("--storage-root", help="forwarded to the CLI")
    parser.add_argument("--dry-run", action="store_true", help="forwarded to the CLI for target commands")
    parser.add_argument("--log-level", default="INFO", choices=VALID_LEVELS, type=str.upper)
    return parser


def main(argv=None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    projects_logging_configure(args.log_level)
    try:
        scripts = load_or_export_config(Path(args.config), force_export=args.export_config)
        if args.export_config:
            _write(f"exported {args.config}\n")
            return 0
        if args.run:
            if COMMANDS[args.run][2] and not args.target:
                parser.error(f"--run {args.run} requires --target")
            extra = []
            if args.run == "restore":
                if not args.uuid:
                    parser.error("--run restore requires --uuid")
                extra = [args.uuid] + (["--dest", args.dest] if args.dest else []) \
                    + (["--overwrite"] if args.overwrite else []) + (["--write-tracker"] if args.write_tracker else [])
            elif args.run == "versions":
                if not args.project:
                    parser.error("--run versions requires --project")
                extra = [args.project]
            return run_command(scripts, args.run, args.target, args, extra)
        return interactive(scripts, args)
    except ProjectsConfigError as exc:
        _log.error("%s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
