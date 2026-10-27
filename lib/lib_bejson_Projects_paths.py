"""
File Name:       lib_bejson_Projects_paths.py
Relative Path:   lib/lib_bejson_Projects_paths.py
Family:          Projects
Description:     Project-root and storage-layout resolution plus filesystem-safe name sanitizing.
                 Nothing is hardcoded: roots resolve from the calling script's location, an explicit
                 override, or the PROJECT_CHUNKER_STORAGE_ROOT environment variable. Tracked chunks and
                 untracked chunks have separate folders; every chunk file name is prefixed with its project name.
VERSION:         1.8.0
Author:          Elton Boehnen · boehnenelton2024@gmail.com · boehnenelton2024.pages.dev · github.com/boehnenelton
RELATIONAL_ID:   c29898b5-8c51-4bdc-a00a-5ef4016c5a0b
"""

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

from lib_bejson_Core_bejson_path_guard import bejson_safe_join

VERSION = "1.8.0"
ENV_STORAGE_ROOT = "PROJECT_CHUNKER_STORAGE_ROOT"
DEFAULT_STORAGE_DIRNAME = "storage"
UNTRACKED_DIRNAME = "untracked_chunks"
FALLBACK_PROJECT_NAME = "unnamed_project"
_NAME_STRIP_RE = re.compile(r"[^A-Za-z0-9._-]+")


class ProjectsPathError(Exception):
    """Raised when a path or name cannot be resolved safely."""


# Project root = the folder that holds CLI/, Menu/, lib/ and storage/. entry_file is the calling script (__file__),
# which lives exactly one level below the root.
def projects_paths_resolve_project_root(entry_file: Union[str, Path]) -> Path:
    return Path(entry_file).resolve().parent.parent


# Turns any user/tracker supplied label into a safe single path segment: whitespace -> "_", anything outside
# [A-Za-z0-9._-] dropped, no leading dots (blocks "..", hidden files), never empty.
def projects_paths_sanitize_name(raw: object) -> str:
    text = _NAME_STRIP_RE.sub("_", str(raw if raw is not None else "").strip())
    text = re.sub(r"_+", "_", text).strip("._-")
    return text or FALLBACK_PROJECT_NAME


@dataclass(frozen=True)
class StorageLayout:
    """The three MFDB pillars under one storage root."""
    storage_root: Path

    @property
    def trackers_dir(self) -> Path:
        return self.storage_root / "trackers" / "data"

    @property
    def master_dir(self) -> Path:
        return self.storage_root / "master" / "data"

    @property
    def snapshots_dir(self) -> Path:
        return self.storage_root / "project_snapshots"

    @property
    def registry_file(self) -> Path:
        return self.master_dir / "registry.bejson"

    # Tracker database file for one project; the name is sanitized and the result is containment-checked.
    def tracker_db(self, project_name: str) -> Path:
        safe = projects_paths_sanitize_name(project_name)
        return Path(bejson_safe_join(str(self.trackers_dir), f"{safe}.bejson"))

    # Chunks made WITHOUT tracking (UUID not in the registry) live apart from tracked chunks.
    @property
    def untracked_dir(self) -> Path:
        return self.storage_root / UNTRACKED_DIRNAME

    # 1.8.0: previous snapshots of a version that was re-chunked with DIFFERENT content are kept here, outside every
    # directory the verifier and the registry read, so nothing a snapshot held is ever silently lost.
    @property
    def superseded_dir(self) -> Path:
        return self.storage_root / "superseded"

    # Chunk file name: <Project>-V<ver>-PKG<pkg>.bejson. The project name is always the prefix so a chunk can be
    # told apart from every other project's chunk by its name alone.
    def snapshot_filename(self, project_name: str, project_version: str, package_version: str) -> str:
        safe = projects_paths_sanitize_name(project_name)
        label = f"V{projects_paths_sanitize_name(project_version)}-PKG{projects_paths_sanitize_name(package_version)}"
        return f"{safe}-{label}.bejson"

    # Tracked chunk: project_snapshots/<name>/data/<name>-V<ver>-PKG<pkg>.bejson
    # Untracked chunk (untracked=True): untracked_chunks/<name>-V<ver>-PKG<pkg>.bejson
    def snapshot_file(self, project_name: str, project_version: str, package_version: str,
                      untracked: bool = False) -> Path:
        filename = self.snapshot_filename(project_name, project_version, package_version)
        if untracked:
            return Path(bejson_safe_join(str(self.untracked_dir), filename))
        safe = projects_paths_sanitize_name(project_name)
        return Path(bejson_safe_join(str(self.snapshots_dir), safe, "data", filename))

    # Pre-1.7.1 tracked chunk name (no project-name prefix): project_snapshots/<name>/data/V<ver>-PKG<pkg>.bejson.
    # Kept so chunks written by older packages are still found, replaced and renamed correctly.
    def legacy_snapshot_file(self, project_name: str, project_version: str, package_version: str) -> Path:
        safe = projects_paths_sanitize_name(project_name)
        label = f"V{projects_paths_sanitize_name(project_version)}-PKG{projects_paths_sanitize_name(package_version)}"
        return Path(bejson_safe_join(str(self.snapshots_dir), safe, "data", f"{label}.bejson"))

    # Registry-style relative reference (posix, relative to the storage root's parent so the default
    # layout yields "storage/trackers/data/<name>.bejson").
    def relative_ref(self, path: Path) -> str:
        return Path(path).resolve().relative_to(self.storage_root.parent.resolve()).as_posix()

    # Inverse of relative_ref with traversal protection.
    def resolve_ref(self, ref: str) -> Path:
        return Path(bejson_safe_join(str(self.storage_root.parent), ref))

    # True when `path` is the storage root or lives inside it.
    def contains(self, path: Union[str, Path]) -> bool:
        resolved = Path(path).resolve()
        root = self.storage_root.resolve()
        return resolved == root or root in resolved.parents


# Storage root precedence: explicit override > environment variable > <project_root>/storage.
def projects_paths_resolve_storage_layout(project_root: Union[str, Path],
                                          override: Optional[Union[str, Path]] = None) -> StorageLayout:
    chosen = override or os.environ.get(ENV_STORAGE_ROOT)
    root = Path(chosen).expanduser() if chosen else Path(project_root) / DEFAULT_STORAGE_DIRNAME
    return StorageLayout(storage_root=root.resolve())
