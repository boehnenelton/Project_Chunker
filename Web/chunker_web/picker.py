#!/usr/bin/env python3
"""
File Name:       picker.py
Relative Path:   Web/chunker_web/picker.py
Description:     Server-side folder/file picker confined to the configured roots (no typed paths needed).
VERSION:         1.8.0
Author:          Elton Boehnen · boehnenelton2024@gmail.com · boehnenelton2024.pages.dev · github.com/boehnenelton
RELATIONAL_ID:   1d9f0440-b3cd-4a49-bbed-68dc929e93dd
"""

from typing import Any, Dict

from .core import ApiError, Ctx

VERSION = "1.8.0"
MAX_ENTRIES = 500


def picker_list(ctx: Ctx, raw: str, files: bool = False, suffix: str = "") -> Dict[str, Any]:
    roots = ctx.roots()
    if not raw:
        return {"ok": True, "path": "", "parent": "", "roots": [str(r) for r in roots], "dirs": [str(r) for r in roots],
                "files": []}
    base = ctx.confine(raw)
    if not base.is_dir():
        raise ApiError("not a folder")
    dirs, found = [], []
    try:
        for entry in sorted(base.iterdir(), key=lambda p: p.name.lower()):
            if entry.is_symlink():
                continue
            if entry.is_dir():
                dirs.append(entry.name)
            elif files and entry.is_file() and (not suffix or entry.name.lower().endswith(suffix.lower())):
                found.append(entry.name)
    except OSError as exc:
        raise ApiError(f"cannot read folder: {exc}")
    parent = ""
    if base not in roots:
        try:
            ctx.confine(str(base.parent))
            parent = str(base.parent)
        except ApiError:
            parent = ""
    return {"ok": True, "path": str(base), "parent": parent, "roots": [str(r) for r in roots],
            "dirs": dirs[:MAX_ENTRIES], "files": found[:MAX_ENTRIES], "truncated": len(dirs) > MAX_ENTRIES}
