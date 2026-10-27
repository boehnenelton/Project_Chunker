#!/usr/bin/env python3
"""
File Name:       project_db_migrator.py
Relative Path:   Helper/project_db_migrator.py
Description:     Automated Project DB migrator helper. On every run it exports the default config
                 (config/migrator_config.json, Dry_Run=true) when absent and runs the chunk phase automatically per
                 that config (a dry run by default: scan + plan + report, nothing changed). Then a menu offers
                 Start (chunk every zip), Verify and remove originals, plan, settings and the web UI. Paths come from
                 the environment (PROJECT_INDEX_ROOT) or the config; this tool never asks for a path.
VERSION:         1.8.0
Author:          Elton Boehnen · boehnenelton2024@gmail.com · boehnenelton2024.pages.dev · github.com/boehnenelton
RELATIONAL_ID:   1e0b78cb-16b2-40f1-8046-5d44feee3f4e
"""

import argparse
import json
import sys
from pathlib import Path

if sys.version_info < (3, 10):
    sys.stderr.write("project_db_migrator requires Python 3.10+\n")
    raise SystemExit(2)

SCRIPT_PATH = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_PATH.parent
for _folder in (PROJECT_ROOT / "lib", PROJECT_ROOT / "Web"):
    if str(_folder) not in sys.path:
        sys.path.insert(0, str(_folder))

from lib_bejson_Projects_logging import (  # noqa: E402
    VALID_LEVELS,
    projects_logging_configure,
    projects_logging_get_logger,
)
from lib_bejson_Projects_migrate import (  # noqa: E402
    ProjectsMigrateError,
    PhaseResult,
    ledger_path,
    projects_migrate_chunk_phase,
    projects_migrate_config_ensure,
    projects_migrate_config_save,
    projects_migrate_ledger_load,
    projects_migrate_resolve_source,
    projects_migrate_scan,
    projects_migrate_verify_phase,
)
from lib_bejson_Projects_paths import projects_paths_resolve_storage_layout  # noqa: E402

VERSION = "1.8.0"
CONFIG_FILE = PROJECT_ROOT / "config" / "migrator_config.json"
CHUNKER_CONFIG = PROJECT_ROOT / "config" / "config.json"
EXIT_OK, EXIT_FAIL = 0, 1
_log = projects_logging_get_logger("migrator")


def _write(text: str) -> None:
    sys.stdout.write(text)
    sys.stdout.flush()


def _summarise(result: PhaseResult) -> str:
    counts = ", ".join(f"{k}={v}" for k, v in sorted(result.counts.items())) or "nothing to do"
    lines = [f"[{result.phase}{' DRY RUN' if result.dry_run else ' LIVE'}] source: {result.source_root}", f"  {counts}"]
    for entry in result.entries[:40]:
        lines.append(f"  {entry['Outcome']:<13} {entry['Zip_Rel']}  {entry['Detail']}")
    if len(result.entries) > 40:
        lines.append(f"  ... {len(result.entries) - 40} more in the report")
    if result.stopped_early:
        lines.append("  stopped early")
    if result.report_file:
        lines.append(f"  report: {result.report_file}")
    return "\n".join(lines) + "\n"


def run_phase(layout, cfg, phase: str) -> PhaseResult:
    if phase == "chunk":
        return projects_migrate_chunk_phase(layout, cfg, CHUNKER_CONFIG)
    return projects_migrate_verify_phase(layout, cfg)


def show_plan(layout, cfg) -> str:
    source = projects_migrate_resolve_source(cfg)
    ledger = projects_migrate_ledger_load(layout)
    cands = projects_migrate_scan(cfg, source, layout, ledger)
    lines = [f"source: {source}", f"{len(cands)} zip(s), {sum(c.valid for c in cands)} valid"]
    lines += [f"  {'ok ' if c.valid else 'BAD'} {c.ledger_status or '-':<13} {c.rel}  {c.error}" for c in cands[:60]]
    return "\n".join(lines) + "\n"


def show_ledger(layout) -> str:
    rows = projects_migrate_ledger_load(layout)
    tally = {}
    for row in rows.values():
        tally[row["Status"]] = tally.get(row["Status"], 0) + 1
    return (f"ledger: {ledger_path(layout)}\n  " + (", ".join(f"{k}={v}" for k, v in sorted(tally.items())) or "empty") + "\n")


def _confirm(question: str) -> bool:
    return input(f"{question} Type 'yes' to continue: ").strip().lower() == "yes"


def launch_web(args) -> int:
    from project_migrator_flask import main as web_main
    return web_main(["--storage-root", args.storage_root] if args.storage_root else [])


def menu(layout, cfg, args, config_file: Path) -> int:
    while True:
        mode = "DRY RUN" if cfg.dry_run else "LIVE"
        _write(f"\nProject DB Migrator  [{mode}]  disposition={cfg.radios['Original_Disposition']}\n"
               "  1) Start: chunk every zip in the project DB\n"
               "  2) Verify chunks, then remove original zips\n"
               "  3) Show plan (scan only)\n"
               "  4) Toggle Dry_Run\n"
               "  5) Show ledger summary\n"
               "  6) Launch web UI\n"
               "  0) Exit\n")
        try:
            choice = input("Select: ").strip()
        except EOFError:
            return EXIT_OK
        try:
            if choice == "0":
                return EXIT_OK
            if choice in ("1", "2"):
                if not cfg.dry_run and not _confirm("LIVE run: chunks will be written"
                                                    + ("" if choice == "1" else f" and original zips will be {cfg.radios['Original_Disposition']}d" if cfg.radios['Original_Disposition'] != 'keep' else "")
                                                    + "."):
                    continue
                _write(_summarise(run_phase(layout, cfg, "chunk" if choice == "1" else "verify")))
            elif choice == "3":
                _write(show_plan(layout, cfg))
            elif choice == "4":
                cfg.flags["Dry_Run"] = not cfg.flags["Dry_Run"]
                projects_migrate_config_save(config_file, cfg)
                _write(f"Dry_Run is now {str(cfg.flags['Dry_Run']).lower()}\n")
            elif choice == "5":
                _write(show_ledger(layout))
            elif choice == "6":
                return launch_web(args)
            else:
                _write("Unknown option.\n")
        except (ProjectsMigrateError, OSError) as exc:
            _log.error("%s", exc)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="project_db_migrator", description="Automated project DB zip migrator.")
    parser.add_argument("--phase", choices=("chunk", "verify", "plan", "none"), default="chunk",
                        help="phase to run automatically on start (default: chunk; a dry run unless config says live)")
    parser.add_argument("--no-menu", action="store_true", help="run the phase and exit (no menu)")
    parser.add_argument("--storage-root", help="override the chunker storage root")
    parser.add_argument("--config", help="migrator config file (default: config/migrator_config.json)")
    parser.add_argument("--json", action="store_true", dest="as_json", help="machine-readable output")
    parser.add_argument("--log-level", choices=VALID_LEVELS, type=str.upper)
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    args = parser.parse_args(argv)
    config_file = Path(args.config) if args.config else CONFIG_FILE

    try:
        cfg, created = projects_migrate_config_ensure(config_file)
        projects_logging_configure(args.log_level or cfg.radios["Log_Level"])
        for warning in cfg.warnings:
            _log.warning(warning)
        layout = projects_paths_resolve_storage_layout(PROJECT_ROOT, args.storage_root)
        if created:
            _write(f"exported default config: {config_file}  (Dry_Run=true)\n")
        result = None
        if args.phase == "plan":
            _write(show_plan(layout, cfg))
        elif args.phase != "none":
            result = run_phase(layout, cfg, args.phase)
            if args.as_json:
                _write(json.dumps({"phase": result.phase, "dry_run": result.dry_run, "source": str(result.source_root),
                                   "counts": result.counts, "entries": result.entries,
                                   "report": str(result.report_file) if result.report_file else None}, indent=2) + "\n")
            else:
                _write(_summarise(result))
        if args.no_menu or args.as_json or not sys.stdin.isatty():
            return EXIT_OK
        return menu(layout, cfg, args, config_file)
    except (ProjectsMigrateError, OSError) as exc:
        _log.error("%s", exc)
        return EXIT_FAIL


if __name__ == "__main__":
    raise SystemExit(main())
