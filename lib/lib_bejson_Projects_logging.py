"""
File Name:       lib_bejson_Projects_logging.py
Relative Path:   lib/lib_bejson_Projects_logging.py
Family:          Projects
Description:     Shared logging setup for every Projects-family tool (CLI and menu). Wraps the standard
                 logging module; no print() logging anywhere in the family.
VERSION:         1.8.0
Author:          Elton Boehnen · boehnenelton2024@gmail.com · boehnenelton2024.pages.dev · github.com/boehnenelton
RELATIONAL_ID:   cd882d4e-fc7b-4cba-a6f5-69266f031643
"""

import logging
import sys

VERSION = "1.8.0"
LOGGER_NAME = "project_chunker"
VALID_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
_LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"


# Returns the family logger; child names (e.g. "tracker") resolve under the family root so one handler serves all.
def projects_logging_get_logger(child: str = "") -> logging.Logger:
    return logging.getLogger(f"{LOGGER_NAME}.{child}" if child else LOGGER_NAME)


# Configures the family root logger once per call (idempotent: replaces its own handler). Logs go to stderr so
# stdout stays reserved for machine-readable / user-facing output.
def projects_logging_configure(level: str = "INFO") -> logging.Logger:
    level_name = str(level).upper()
    if level_name not in VALID_LEVELS:
        level_name = "INFO"
    root = logging.getLogger(LOGGER_NAME)
    for handler in list(root.handlers):
        if getattr(handler, "_projects_family", False):
            root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    handler._projects_family = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    root.setLevel(getattr(logging, level_name))
    root.propagate = False
    return root
