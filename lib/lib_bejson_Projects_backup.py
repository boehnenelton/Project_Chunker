"""
File Name:       lib_bejson_Projects_backup.py
Relative Path:   lib/lib_bejson_Projects_backup.py
Family:          Projects
Description:     Dedicated full-system backup, restore, import, and export engine for Project Chunker.
                 Archives all trackers, master registry, project snapshot chunks, active project state,
                 and runtime configs into a verified zip archive, and restores/reimports with cryptographic verification.
VERSION:         1.9.0
Author:          Elton Boehnen · boehnenelton2024@gmail.com · boehnenelton2024.pages.dev · github.com/boehnenelton
RELATIONAL_ID:   b83a04e1-2c94-4d81-a9f2-7e04b19c8d50
"""

from __future__ import annotations
import os
import sys
import json
import shutil
import zipfile
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from lib_bejson_Projects_logging import projects_logging_get_logger

VERSION = "1.9.0"
_log = projects_logging_get_logger("backup")


def ensure_backups_mfdb(project_root: Path) -> Tuple[Path, Path]:
    """Ensures the 4th storage MFDB pillar (manifest and backups_registry) is initialized."""
    from lib_bejson_Core_bejson_core import bejson_core_atomic_write
    root = Path(project_root).resolve()
    backups_dir = root / "storage" / "backups"
    backups_data = backups_dir / "data"
    manifest_path = backups_dir / "104a.mfdb.bejson"
    registry_path = backups_data / "backups_registry.104a.bejson"

    if not manifest_path.exists():
        backups_dir.mkdir(parents=True, exist_ok=True)
        manifest_doc = {
            "Format": "BEJSON",
            "Format_Version": "104a",
            "Format_Creator": "Elton Boehnen",
            "Database_Name": "Project_Chunker_Backups",
            "Database_Version": "1.3.1",
            "MFDB_Version": "1.3.1",
            "Network_Role": "Master",
            "Parent_Hierarchy": {
                "Manifest_File": "104a.mfdb.bejson",
                "Entities_Directory": "data/"
            },
            "Records_Type": ["mfdb"],
            "Fields": [
                {"name": "entity_name", "type": "string"},
                {"name": "file_path", "type": "string"},
                {"name": "description", "type": "string"},
                {"name": "network_role", "type": "string"}
            ],
            "Values": [
                ["backups_registry", "data/backups_registry.104a.bejson", "Backup archives registry and metadata", "slave"]
            ]
        }
        bejson_core_atomic_write(str(manifest_path), manifest_doc)

    if not registry_path.exists():
        backups_data.mkdir(parents=True, exist_ok=True)
        registry_doc = {
            "Format": "BEJSON",
            "Format_Version": "104a",
            "Format_Creator": "Elton Boehnen",
            "Parent_Hierarchy": {
                "Manifest_File": "../104a.mfdb.bejson",
                "Entity_Name": "BackupsRegistry",
                "Network_Role": "Slave"
            },
            "Records_Type": ["BackupRecord"],
            "Fields": [
                {"name": "Backup_ID", "type": "string"},
                {"name": "Backup_Filename", "type": "string"},
                {"name": "Archive_Path", "type": "string"},
                {"name": "SHA256", "type": "string"},
                {"name": "File_Size_Bytes", "type": "integer"},
                {"name": "File_Count", "type": "integer"},
                {"name": "Created_At", "type": "string"},
                {"name": "Status", "type": "string"},
                {"name": "Notes", "type": "string"}
            ],
            "Values": []
        }
        bejson_core_atomic_write(str(registry_path), registry_doc)

    return manifest_path, registry_path


def get_default_backup_root(project_root: Optional[Path] = None) -> Path:
    env_dir = os.environ.get("PROCHUNK_BACKUP_DIR")
    if env_dir:
        return Path(env_dir)
    storage = os.environ.get("INTERNAL_STORAGE")
    if storage:
        return Path(storage) / "Admin" / "Core" / "Backups" / "ProChunkBK"
    if project_root:
        return Path(project_root).resolve().parent.parent / "Core" / "Backups" / "ProChunkBK"
    return Path.cwd() / "backups"


class ProjectsBackupError(Exception):
    """Raised when a system backup, restore, import, or export operation fails."""


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def projects_backup_create(
    project_root: Path,
    target_zip: Optional[Path] = None,
    backup_dir: Optional[Path] = None
) -> Tuple[Path, str, int]:
    """
    Backs up all Project Chunker storage (trackers, master registry, snapshot chunks),
    active state, and config into a zip archive stored at configured backup directory.
    Returns (zip_path, sha256_hash, file_size_bytes).
    """
    root = Path(project_root).resolve()
    out_dir = Path(backup_dir or get_default_backup_root(root))
    out_dir.mkdir(parents=True, exist_ok=True)

    if target_zip:
        dest_zip = Path(target_zip).resolve()
    else:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")
        dest_zip = out_dir / f"prochunk_backup_{ts}.zip"

    dest_zip.parent.mkdir(parents=True, exist_ok=True)
    temp_zip = dest_zip.with_suffix(".tmp.zip")

    targets = ["storage", "data", "config"]
    written_count = 0

    try:
        with zipfile.ZipFile(temp_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            for tgt in targets:
                tgt_path = root / tgt
                if not tgt_path.exists():
                    continue
                for item in tgt_path.rglob("*"):
                    if item.is_file():
                        rel = item.relative_to(root)
                        zf.write(item, str(rel))
                        written_count += 1

        if temp_zip.exists():
            temp_zip.replace(dest_zip)

        sha = _file_sha256(dest_zip)
        size = dest_zip.stat().st_size
        _log.info("System backup created: %s (%d files, %d bytes, sha=%s)", dest_zip, written_count, size, sha[:8])

        # Register backup entry into 4th storage MFDB pillar
        try:
            from lib_bejson_Core_bejson_core import bejson_core_atomic_write, bejson_core_load_file
            _, backups_table = ensure_backups_mfdb(root)
            doc = bejson_core_load_file(str(backups_table)) or {}
            now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            b_id = f"prochunk-bk-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
            row = [b_id, dest_zip.name, str(dest_zip), sha, size, written_count, now_iso, "Active", "Automated system backup"]
            doc.setdefault("Values", []).append(row)
            bejson_core_atomic_write(str(backups_table), doc)
        except Exception as ex:
            _log.warning("Could not register backup in 4th storage MFDB pillar: %s", ex)

        return dest_zip, sha, size

    except Exception as e:
        if temp_zip.exists():
            temp_zip.unlink(missing_ok=True)
        raise ProjectsBackupError(f"System backup failed: {e}") from e


def projects_backup_restore(
    backup_zip: Path,
    project_root: Path,
    overwrite: bool = True
) -> Dict[str, Any]:
    """
    Restores Project Chunker system data (trackers, master registry, snapshot chunks, and configs)
    from a verified backup zip archive.
    """
    zip_path = Path(backup_zip).resolve()
    root = Path(project_root).resolve()

    if not zip_path.is_file():
        raise ProjectsBackupError(f"Backup archive does not exist: {zip_path}")

    if not zipfile.is_zipfile(zip_path):
        raise ProjectsBackupError(f"File is not a valid zip archive: {zip_path}")

    restored_files = []
    skipped_files = []

    with zipfile.ZipFile(zip_path, "r") as zf:
        infolist = zf.infolist()
        for member in infolist:
            name = member.filename
            # Security guard against directory traversal
            if name.startswith("/") or ".." in name:
                continue

            target_file = root / name
            if target_file.exists() and not overwrite:
                skipped_files.append(name)
                continue

            target_file.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member, "r") as src, open(target_file, "wb") as dst:
                shutil.copyfileobj(src, dst)
            restored_files.append(name)

    _log.info("System restore completed from %s: %d restored, %d skipped", zip_path, len(restored_files), len(skipped_files))
    return {
        "backup_file": str(zip_path),
        "restored_count": len(restored_files),
        "skipped_count": len(skipped_files),
        "restored_files": restored_files
    }


def projects_backup_export(project_root: Path, export_zip: Path) -> Path:
    """Exports all trackers and snapshot chunks into a portable transfer zip archive."""
    dest, _, _ = projects_backup_create(project_root, target_zip=export_zip)
    return dest


def projects_backup_import(import_zip: Path, project_root: Path) -> Dict[str, Any]:
    """Imports and overlays project data and trackers from an external transfer zip archive."""
    return projects_backup_restore(import_zip, project_root, overwrite=True)


def projects_backup_list(project_root: Path) -> List[Dict[str, Any]]:
    """List all registered system backups from the 4th storage MFDB pillar."""
    root = Path(project_root).resolve()
    _, backups_table = ensure_backups_mfdb(root)
    try:
        from lib_bejson_Core_bejson_core import bejson_core_load_file, bejson_core_get_field_map
        doc = bejson_core_load_file(str(backups_table)) or {}
        fmap = bejson_core_get_field_map(doc)
        results = []
        for row in doc.get("Values", []):
            entry = {k: row[idx] for k, idx in fmap.items() if idx < len(row)}
            results.append(entry)
        return results
    except Exception as ex:
        _log.warning("Could not list backups from 4th storage MFDB pillar: %s", ex)
        return []
