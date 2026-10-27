# Project Chunker: Quick Command Reference & Lifecycle Guide

Author: Elton Boehnen · boehnenelton2024@gmail.com · boehnenelton2024.pages.dev · [github.com/boehnenelton](https://github.com/boehnenelton)  
System: `/storage/emulated/0/Admin/System/Project_,Chunker`  
Format: BEJSON 104a Conforming  
Version: 1.8.0  

---

## 1. Executive Summary: What Is This Tool?

If you have just arrived at this system and have a massive collection of project directories, legacy codebases, and `.zip` archives to ingest into a structured database without losing a single byte of code, history, or metadata: **Project Chunker is your automated, zero-SQL project management, archiving, and snapshot database engine.**

### The Core Problem It Solves
Standard zip archives and raw filesystem folders are fragile: they lack relational tracking, cannot be audited for tamper-evident bit rot without unpacking, duplicate massive amounts of cache files (`node_modules`, `__pycache__`), and lack unified version registries. 

**Project Chunker** solves this by converting arbitrary project directories or `.zip` files into single-file **BEJSON 104a Snapshot Chunks** linked to a **Master Version Registry** and a historical **Tracker Database**. It offers **atomic verified scooping** (archiving the project and safely removing the source directory only after a 100% byte-for-byte SHA-256 verification pass) and instant, lossless deployment back to the filesystem.

---

## 2. The Mental Model: 4 Core Operations

Everything in Project Chunker revolves around four fundamental verbs:

```
┌──────────────┐     --track      ┌───────────────────────────────────┐
│ Source Code  ├─────────────────►│ 1. Tracker DB & Master Registry  │
│  Directory   │                  │    (Assigns UUID, indexes state)  │
│   or .zip    │     --chunk      ├───────────────────────────────────┤
│              ├─────────────────►│ 2. Snapshot Chunk File (.bejson) │
│              │                  │    (UTF-8 / Base64 file payloads) │
│              │     --scoop      ├───────────────────────────────────┤
│              ├─────────────────►│ 3. Verified Atomic Archive & Wipe │
│              │                  │    (Chunk -> Byte-Check -> Purge) │
└──────────────┘                  └─────────────────┬─────────────────┘
       ▲                                            │
       │                     --restore <UUID>       │
       └────────────────────────────────────────────┘
```

1. **`--track`**: Inspects the target project, locates or creates its `bejson_project.json` tracker, records historical metadata into the Tracker DB (`storage/trackers/data/<project>.bejson`), and registers its `Versioned_UUID` in the Master Registry (`storage/master/data/registry.bejson`). **The source project is never modified.**
2. **`--chunk`**: Scans the target project directory, filters out junk/build caches, calculates SHA-256 checksums, encodes binary assets to Base64, captures empty directories, and serializes the entire filesystem tree into a single portable BEJSON 104a snapshot file (`storage/project_snapshots/<project>/data/<project>-V<ver>-PKG<pkg>.bejson`).
3. **`--scoop`**: The ultimate archiving tool. Combines `--track` and `--chunk`, writes the snapshot to disk, re-reads the snapshot from disk, performs a **byte-for-byte SHA-256 comparison** against every live file on the filesystem, and—only after 100% verified match—deletes the source files and prunes empty folders. **Zero data loss guaranteed.**
4. **`--restore <UUID>`**: Reverses the process. Reads the snapshot chunk corresponding to the UUID, recreates every folder and file with identical permissions and timestamps, and optionally re-emits `bejson_project.json` from the captured tracker data.

---

## 3. Storage Architecture: The 3 Pillars

All persistence in Project Chunker uses strict, schema-validated BEJSON 104a tables with Field Map Caching:

| Pillar | Storage Location | Schema & Contents |
| :--- | :--- | :--- |
| **Pillar 1: Master Registry** | `storage/master/data/registry.bejson` | `RegistryRecord`: Maps each unique `Versioned_UUID` to its Project Name, Version, Package, relative Tracker File path, Original Path, Status (`active`), and Timestamp. |
| **Pillar 2: Tracker DB** | `storage/trackers/data/<project>.bejson` | `TrackerRecord`: Historical record of all versions of a project. Contains `Versioned_UUID`, `Project_Name`, and the raw serialized JSON payload of `bejson_project.json`. |
| **Pillar 3: Snapshots** | `storage/project_snapshots/<project>/data/<project>-V<ver>-PKG<pkg>.bejson` | `SnapshotEntity`: High-density table containing one row per file: `File_Name`, `File_Extension`, `Relative_Path`, `File_Content`, `Base64`, `File_Hash` (SHA-256), `Flags` (`["Is_Binary"]`), `Modified_Time`, and `Is_Empty_Dir`. |
| **Safety: Superseded Backups** | `storage/superseded/<project>/<stem>.superseded-<timestamp>.bejson` | Safe isolation directory for previous snapshots replaced by new content under the same version tag when `--on-conflict backup` is active. |
| **Session: Active Project** | `data/active_project.json` | Stores current working project context for menu and web workflows. |

---

## 4. Immediate "Get Up & Go" Quick Command Reference

The primary CLI entry point is `CLI/project_chunker.py`. Always execute using Python 3.10+ (via `python3`).

### 4.1 Ingesting a Single Directory

#### Track Only (Register metadata, leave files untouched)
```bash
python3 /storage/emulated/0/Admin/System/Project_,Chunker/CLI/project_chunker.py \
  /path/to/MyProject --track
```

#### Chunk Only (Create snapshot without global registration)
```bash
python3 /storage/emulated/0/Admin/System/Project_,Chunker/CLI/project_chunker.py \
  /path/to/MyProject --chunk
```

#### Track + Chunk (Recommended standard directory ingestion)
```bash
python3 /storage/emulated/0/Admin/System/Project_,Chunker/CLI/project_chunker.py \
  /path/to/MyProject --track --chunk
```

#### Scoop (Archive and remove source directory with zero-risk verification)
```bash
python3 /storage/emulated/0/Admin/System/Project_,Chunker/CLI/project_chunker.py \
  /path/to/MyProject --scoop
```

---

### 4.2 Ingesting Single & Bulk `.zip` Files

#### Ingest a `.zip` File (Extracts, tracks, chunks, scoops staging, deletes `.zip`)
```bash
python3 /storage/emulated/0/Admin/System/Project_,Chunker/CLI/project_chunker.py \
  /storage/emulated/0/Download/CoolApp-V100-PKG101.zip --chunk --identity-from-filename
```

#### Ingest a `.zip` File but Keep the Original Zip
```bash
python3 /storage/emulated/0/Admin/System/Project_,Chunker/CLI/project_chunker.py \
  /storage/emulated/0/Download/CoolApp-V100-PKG101.zip --chunk --identity-from-filename --keep-zip
```

#### Override Identity on Ingestion
If a zip or directory has generic names or missing trackers, specify the identity explicitly:
```bash
python3 /storage/emulated/0/Admin/System/Project_,Chunker/CLI/project_chunker.py \
  /path/to/source_or_zip --track --chunk \
  --project-name "CustomProject" \
  --project-version "1.2.0" \
  --package-version "105"
```

---

### 4.3 Ingesting a Ton of Projects in Bulk

#### Bulk Ingestion of All Directories in a Folder
```bash
TARGET_FOLDER="/storage/emulated/0/Admin/Projects_To_Ingest"

for dir in "$TARGET_FOLDER"/*/; do
  [ -d "$dir" ] || continue
  echo ">>> Ingesting project directory: $dir"
  python3 /storage/emulated/0/Admin/System/Project_,Chunker/CLI/project_chunker.py \
    "$dir" --track --chunk
done
```

#### Bulk Ingestion of All `.zip` Archives in a Download Directory
```bash
ZIPS_FOLDER="/storage/emulated/0/Download"

for zipfile in "$ZIPS_FOLDER"/*.zip; do
  [ -f "$zipfile" ] || continue
  echo ">>> Ingesting zip package: $zipfile"
  python3 /storage/emulated/0/Admin/System/Project_,Chunker/CLI/project_chunker.py \
    "$zipfile" --chunk --identity-from-filename --keep-zip
done
```

#### High-Capacity Automated Ingestion via `project_db_migrator.py`
For migrating hundreds of archives with automated ledger tracking, reports, and quarantine:
```bash
# 1. Run dry-run scan to inspect candidates without making changes:
python3 /storage/emulated/0/Admin/System/Project_,Chunker/Helper/project_db_migrator.py --plan

# 2. Execute automated chunk phase:
python3 /storage/emulated/0/Admin/System/Project_,Chunker/Helper/project_db_migrator.py --chunk

# 3. Verify chunks and prune original archives:
python3 /storage/emulated/0/Admin/System/Project_,Chunker/Helper/project_db_migrator.py --verify-phase
```

---

### 4.4 Inspecting, Listing & Querying the Database

#### List All Registered Projects in Master Registry
```bash
python3 /storage/emulated/0/Admin/System/Project_,Chunker/CLI/project_chunker.py --list
```

#### List Every Registered Version of a Specific Project
```bash
python3 /storage/emulated/0/Admin/System/Project_,Chunker/CLI/project_chunker.py --versions "MyProject"
```

#### Emit Machine-Readable JSON Output (For Automation / Scripts)
```bash
python3 /storage/emulated/0/Admin/System/Project_,Chunker/CLI/project_chunker.py --list --json
```

---

### 4.5 Full Storage Integrity Auditing

#### Run Full Storage Audit Across All Projects
Checks registry-to-tracker linkage, validates all BEJSON schemas, recomputes all snapshot file SHA-256 hashes, and flags bit rot or dangling trackers:
```bash
python3 /storage/emulated/0/Admin/System/Project_,Chunker/CLI/project_chunker.py --verify
```

#### Audit Only a Specific Project
```bash
python3 /storage/emulated/0/Admin/System/Project_,Chunker/CLI/project_chunker.py \
  --verify --verify-project "MyProject"
```

---

### 4.6 Restoring & Deploying Projects

#### Restore to Original Captured Location
```bash
python3 /storage/emulated/0/Admin/System/Project_,Chunker/CLI/project_chunker.py \
  --restore 9269c32f-b093-4279-8389-bd12e973a726
```

#### Restore to a Specific Target Directory
```bash
python3 /storage/emulated/0/Admin/System/Project_,Chunker/CLI/project_chunker.py \
  --restore 9269c32f-b093-4279-8389-bd12e973a726 \
  --dest /storage/emulated/0/Admin/RestoredProjects/MyProject
```

#### Restore with Overwrite & Re-emit `bejson_project.json` Tracker
```bash
python3 /storage/emulated/0/Admin/System/Project_,Chunker/CLI/project_chunker.py \
  --restore 9269c32f-b093-4279-8389-bd12e973a726 \
  --dest /storage/emulated/0/Admin/RestoredProjects/MyProject \
  --overwrite --write-tracker
```

---

## 5. Filtering, Exclusions & Binary Control

Project Chunker comes with smart defaults to avoid archiving transient artifacts, caches, and enormous binaries.

### Default Active Filters
Configured in `config/chunker_filters.json`:
- `git`: `.git`, `.gitignore`, `.gitattributes`
- `nodejs`: `node_modules`
- `python_cache`: `__pycache__`, `*.pyc`, `.pytest_cache`, `.tox`
- `virtualenv`: `.venv`, `venv`, `env`
- `build_output`: `dist`, `build`, `out`, `*.egg-info`
- `temp_files`: `*.tmp`, `*.temp`, `*~`, `.cache`
- `os_junk`: `.DS_Store`, `Thumbs.db`
- `chunk_output` & `chunk_files`: avoids recursive chunking of `.bejson` snapshot trees

### CLI Filter Overrides

```bash
# Capture EVERYTHING (turn off all filters):
python3 project_chunker.py /path/to/project --track --chunk --no-filters

# Add extra directories to exclude on this run:
python3 project_chunker.py /path/to/project --track --chunk --exclude "heavy_assets" --exclude "scratch"

# Restrict chunking to specific file extensions:
python3 project_chunker.py /path/to/project --track --chunk --ext .py --ext .json --ext .sh

# Exclude files larger than 10 MB (0 disables limit):
python3 project_chunker.py /path/to/project --track --chunk --max-file-mb 10.0

# Explicitly enable or disable binary file capture (Base64):
python3 project_chunker.py /path/to/project --track --chunk --binary     # Force binary on
python3 project_chunker.py /path/to/project --track --chunk --no-binary  # Exclude binary files
```

---

## 6. Full Project Management Lifecycle

Here is the exact progression of a project from raw development to permanent archival and redemption:

```
[ Active Development ]
         │
         ▼
[ Stage 1: Identification & Tracker Inspection ]
         │  • Checks for bejson_project.json or .bejson_project.json
         │  • Resolves Project_Name, Project_Version, Package_Version
         │  • Derives fallback name from directory or zip filename
         │  • Retains existing Versioned_UUID or creates new UUIDv4
         ▼
[ Stage 2: Registration & Tracker Staging ]
         │  • Updates storage/trackers/data/<project>.bejson
         │  • Writes row to storage/master/data/registry.bejson
         ▼
[ Stage 3: Snapshot Compilation ]
         │  • Traverses tree applying chunker_filters.json
         │  • Detects text (UTF-8) vs binary (Base64)
         │  • Computes streaming SHA-256 hash per file
         │  • Captures empty directory markers (Is_Empty_Dir = true)
         │  • Writes storage/project_snapshots/<project>/data/...
         ▼
[ Stage 4: Atomic Verification & Verified Scoop ] (Optional: --scoop)
         │  • Reloads the freshly written snapshot from disk
         │  • Compares every file payload byte-for-byte with live file
         │  • Verifies live SHA-256 against recorded File_Hash
         │  • Only when 100% matched: unlinks live files & prunes dirs
         ▼
[ Stage 5: Cold Storage & Integrity Auditing ]
         │  • Periodic auditing via --verify
         │  • Verifies registry pointers and snapshot SHA-256 hashes
         │  • Conflicting versions safely moved to storage/superseded/
         ▼
[ Stage 6: Restoration & Redeployment ]
            • --restore <UUID> re-creates folder hierarchy
            • Decodes Base64 binaries and UTF-8 source files
            • Re-emits bejson_project.json via --write-tracker
```

---

## 7. Interactive Terminal Menu & Web GUI

When you don't want to construct CLI commands manually, Project Chunker provides two dedicated interaction layers:

### 1. Terminal Interactive Menu
```bash
python3 /storage/emulated/0/Admin/System/Project_,Chunker/Menu/project_chunker_menu.py
```
- **Zero Path Typing**: Uses a numbered folder browser to navigate the Android/Linux filesystem.
- Offers interactive options for `Chunk+Track`, `Track`, `Chunk`, `Scoop`, `Restore`, `Versions`, `List`, and `Verify`.
- Exports and maintains [`config/menu_scripts.json`](file:///storage/emulated/0/Admin/System/Project_,Chunker/config/menu_scripts.json).

### 2. Modern Web GUI (Flask + BECSS)
```bash
python3 /storage/emulated/0/Admin/System/Project_,Chunker/Web/project_chunker_flask.py
```
- Launches local web service on first free port starting at `5120` (e.g. `http://localhost:5120`).
- Unified **Chunk Page**: Single reactive interface for Track, Chunk, Scoop, and Zip Intake.
- **Live CLI Command Generator**: Displays the exact equivalent CLI command in real-time as you toggle switches.
- **Visual Tracker Editor**: Inspect and edit tracker metadata with atomic patch rollback safety.
- **Real-Time Job Journal**: Tracks background chunking and migration tasks.

---

## 8. Common Troubleshooting & FAQs

### Q: Why did `--chunk` put my file in `untracked_chunks/`?
**A**: You ran `--chunk` without `--track`. Without tracking, the system does not create a tracker entry or register the UUID in the master registry. To fix, always use `--track --chunk` (or use `--track` first).

### Q: What happens if I chunk a project version that already exists?
**A**: Project Chunker compares the content digest (SHA-256 of all file hashes). If the content is identical, it reports that the version is already captured. If the content differs, `--on-conflict backup` (default) safely archives the old snapshot into `storage/superseded/<project>/` and writes the new one. If `--on-conflict refuse` is passed, it halts without touching storage.

### Q: How do I handle permission errors on Android Termux?
**A**: Android mounts `/storage/emulated/0` with the `noexec` flag. Always invoke scripts with their interpreter (`python3 /path/to/script.py` or `bash /path/to/script.sh`).
