# Project Chunker: Comprehensive System Architecture, Operational Manual & Specification

Author: Elton Boehnen · boehnenelton2024@gmail.com · boehnenelton2024.pages.dev · [github.com/boehnenelton](https://github.com/boehnenelton)  
System Location: `/storage/emulated/0/Admin/System/Project_,Chunker`  
Specification Version: `1.8.0`  
Standard Conformance: BEJSON 104a / Policy §1.1–§11  
Document Classification: Comprehensive Technical Architecture & Field Operations Manual  

---

## 1. Introduction and Scope

Project Chunker is an enterprise-grade, local-first, zero-SQL project management, filesystem snapshot archiving, and database engine designed for Elton Boehnen's BEJSON ecosystem. Built primarily for Termux on Android ARM64 and POSIX Linux environments, it provides deterministic, byte-for-byte verifiable storage of complete software project codebases, metadata trackers, and arbitrary binary assets without relying on heavy external database engines, cloud services, or fragile filesystem symlinks.

This manual serves as the exhaustive reference manual for developers, system administrators, and autonomous AI agents who must ingest large numbers of directory-based projects and `.zip` archives, manage multi-version software portfolios, verify storage against bit rot, perform atomic zero-risk archival ("scooping"), deploy projects back to disk, and administer the web and terminal interfaces.

---

## 2. Architectural Philosophy and Design Principles

### 2.1 The Philosophy of Local-First, Zero-SQL Archiving
Modern software development often relies on distributed object stores, external SQL databases, or monolithic Git repositories to track project versions and snapshots. In lightweight local environments, mobile devices, and sandboxed Termux environments, these tools introduce substantial overhead, daemon vulnerabilities, configuration drift, and complex network dependencies.

Project Chunker operates on the **Local-First Zero-SQL** paradigm:
1. **Self-Contained Persistence**: All database state is stored directly within the filesystem as human-readable and machine-parseable BEJSON 104a files.
2. **Deterministic Immutability**: Once written, a snapshot chunk file is immutable. Any modification to a single byte invalidates the file's SHA-256 integrity check.
3. **Bit-for-Bit Verifiability**: Every file captured into a snapshot carries an independent streaming SHA-256 cryptographic hash. Restorations and scoops must verify 100% of bytes against live data before completing.
4. **Zero Path Assumptions**: Scripts self-locate their execution root via POSIX-compliant script path resolution (`Path(__file__).resolve().parent.parent`), operating portably regardless of mount points.

### 2.2 BEJSON 104a Core Standard & Field Map Caching Compliance
Project Chunker strictly complies with BEJSON 104a format specifications:
- **Decoupled Architecture**: Schema definitions (`Fields`) are declared exactly once at the top of the file, completely decoupled from data rows (`Values`).
- **Field Map Cache Requirement (Policy §6.2)**: Positional array lookups, hardcoded integer offsets, and brittle indexing (e.g. `row[3]`) are strictly prohibited in application logic. All record processing compiles an O(1) field map lookup dictionary (`{field['name']: idx for idx, field in enumerate(fields)}`) before iterating over rows.
- **Atomic Operations**: State transitions and writes are performed via temporary staging files followed by atomic POSIX renames, eliminating file corruption risks during power outages or unexpected process terminations.

### 2.3 Relational Governance: Project GUID vs Session ID vs Versioned UUID
The system strictly enforces the three-tier identity governance defined in Policy §4.6:
1. **`Project_GUID`**: A globally unique identifier assigned exclusively by Elton Boehnen's master project registration tooling (`cli_project_register.py`). Project Chunker **NEVER** auto-generates or mutates a global `Project_GUID`. If unassigned in the source tracker, it remains `"GUID_PLACEHOLDER"`.
2. **`Session_Id`**: A standard UUIDv4 generated per execution session, isolating individual CLI, menu, or web transactions.
3. **`Versioned_UUID`**: A cryptographically random UUIDv4 bound to a specific application version release (`Project_Version` + `Package_Version`). It acts as the relational primary key across the Master Registry, Tracker DB, and Snapshot Chunks.

### 2.4 Dual-MFDB System Architecture & Data Isolation
Project Chunker implements a dual-MFDB architectural pattern:
- **Index Plane**: The Master Registry (`storage/master/data/registry.bejson`) provides fast O(1) lookups mapping `Versioned_UUID` keys to relative tracker file locations and metadata.
- **Historical Tracker Plane**: The Tracker DB (`storage/trackers/data/<project>.bejson`) maintains the complete historical lineage of all `bejson_project.json` manifests for each project across all versions.
- **Snapshot Storage Plane**: The Project Snapshot directory (`storage/project_snapshots/<project>/data/`) stores the physical filesystem payload chunks containing file bodies, Base64 encodings, and directory markers.

---

## 3. Complete Directory Structure and File Taxonomy

The canonical repository layout is structured into dedicated, standardized subdirectories adhering to Policy §1.1:

```
/storage/emulated/0/Admin/System/Project_,Chunker/
├── AGENTS.MD                        # Agent operational constraints and guidelines
├── bejson_project.json              # Canonical project manifest & historical changelog
├── CLI/
│   └── project_chunker.py           # Core CLI entry point, flag parser & pipeline dispatcher
├── Helper/
│   └── project_db_migrator.py       # Batch database migrator for legacy archives
├── Menu/
│   └── project_chunker_menu.py      # Interactive TUI menu wrapper with folder browser
├── Web/
│   ├── project_chunker_flask.py     # Main web application entry point (Flask)
│   ├── project_management_flask.py  # Read-only project visualizer service
│   ├── project_migrator_flask.py    # Dedicated migration web service
│   ├── chunker_web/                 # Modular web application package
│   │   ├── __init__.py
│   │   ├── active.py                # Active project context resolution
│   │   ├── api_data.py              # REST API data endpoints (projects, settings)
│   │   ├── api_work.py              # REST API operational endpoints (run, preview)
│   │   ├── core.py                  # Web application context & option builders
│   │   ├── jobs.py                  # Single-slot background job runner & journal
│   │   ├── pages.py                 # Page route blueprints & navigation map
│   │   └── picker.py                # Web-based directory browser
│   ├── static/
│   │   ├── becss/                   # Policy §8.1 Tri-Color CSS frameworks
│   │   └── chunker/                 # Client-side JavaScript (chunker.js)
│   └── templates/
│       └── chunker/                 # Semantic HTML page templates
├── config/
│   ├── config.json                  # BEJSON 104a script configuration (paths, defaults)
│   ├── chunker_filters.json         # BEJSON 104a file/directory exclusion filter rules
│   ├── menu_scripts.json            # Auto-generated script location registry for Menu
│   ├── migrator_config.json         # Automated batch migration settings
│   └── web_config.json              # Web server port and interface settings
├── data/
│   ├── active_project.json          # BEJSON 104a active working project record
│   └── web_jobs.bejson              # BEJSON 104a execution journal for web jobs
├── dev/
│   ├── change-log.md                # Human-readable development audit log
│   ├── core_lib_hashes.json         # Integrity hashes of frozen core libraries
│   ├── dead_code.md                 # Removed dead code audit ledger
│   ├── security-notes.md            # Security vulnerability audit notes
│   ├── UIPref.md                    # UI branding, layout & interaction directives
│   ├── variable_naming_issues.md    # Naming convention compliance log
│   └── tests/                       # Unit tests & verification suites
├── docs/
│   ├── technical_overview.md        # Technical architecture summary
│   ├── QUICK_COMMAND_LIFECYCLE_GUIDE.md # Immediate CLI reference & workflow guide
│   └── PROJECT_CHUNKER_COMPREHENSIVE_MANUAL.md # This specification document
├── lib/                             # Internal libraries (Core 104 and Projects family)
│   ├── lib_bejson_Core_*            # Frozen BEJSON Core engines (unmodified)
│   └── lib_bejson_Projects_*        # Specialized Projects family subsystems
└── storage/                         # Primary runtime database storage
    ├── master/                      # Master Registry DB
    │   └── data/registry.bejson
    ├── trackers/                    # Historical Tracker DB per project
    │   └── data/<project>.bejson
    ├── project_snapshots/           # Snapshot chunks partitioned by project
    │   └── <project>/data/<project>-V<ver>-PKG<pkg>.bejson
    ├── untracked_chunks/            # Snapshot chunks created without tracking
    └── superseded/                  # Superseded snapshot backups on collision
```

---

## 4. The 3 Data Pillars: In-Depth Schema Specifications

### 4.1 Pillar 1: Master Registry (`storage/master/data/registry.bejson`)
The Master Registry maintains the global ledger of every tracked project version across the system. It enforces relational mapping between the `Versioned_UUID` and the physical filesystem locations.

- **Format**: `BEJSON`
- **Format_Version**: `104a`
- **Records_Type**: `["RegistryRecord"]`
- **Field Definitions**:
  1. `Versioned_UUID` (`string`): Unique UUIDv4 identifying the version package. Primary Key.
  2. `Project_Name` (`string`): Normalized project name.
  3. `Project_Version` (`string`): Semantic or integer version of the project application.
  4. `Package_Version` (`string`): Solid integer package exchange version.
  5. `Tracker_File` (`string`): Relative path to project tracker in `storage/trackers/`.
  6. `Original_Path` (`string`): Absolute filesystem path where project was captured.
  7. `Status` (`string`): State of the project version (`active`, `legacy`, `inactive`).
  8. `Registered_At` (`string`): UTC ISO-8601 timestamp with microseconds.

```json
{
  "Format": "BEJSON",
  "Format_Version": "104a",
  "Format_Creator": "Elton Boehnen",
  "Records_Type": ["RegistryRecord"],
  "Fields": [
    {"name": "Versioned_UUID", "type": "string"},
    {"name": "Project_Name", "type": "string"},
    {"name": "Project_Version", "type": "string"},
    {"name": "Package_Version", "type": "string"},
    {"name": "Tracker_File", "type": "string"},
    {"name": "Original_Path", "type": "string"},
    {"name": "Status", "type": "string"},
    {"name": "Registered_At", "type": "string"}
  ],
  "Values": [
    [
      "9269c32f-b093-4279-8389-bd12e973a726",
      "MockAlpha",
      "1.0.0",
      "100",
      "trackers/data/MockAlpha.bejson",
      "/storage/emulated/0/Admin/Projects/MockAlpha",
      "active",
      "2026-10-27T13:21:12.183042Z"
    ]
  ]
}
```

### 4.2 Pillar 2: Project Tracker Database (`storage/trackers/data/<project>.bejson`)
Each unique project has its own dedicated tracker table in `storage/trackers/data/`. This table preserves the historical evolution of the project's metadata manifests over time.

- **Format**: `BEJSON`
- **Format_Version**: `104a`
- **Records_Type**: `["TrackerRecord"]`
- **Field Definitions**:
  1. `Versioned_UUID` (`string`): Foreign Key matching `RegistryRecord.Versioned_UUID`.
  2. `Project_Name` (`string`): Name of the project.
  3. `Tracker_Data` (`string`): Serialized JSON string containing the full, verbatim `bejson_project.json` manifest captured at this version.

```json
{
  "Format": "BEJSON",
  "Format_Version": "104a",
  "Format_Creator": "Elton Boehnen",
  "Records_Type": ["TrackerRecord"],
  "Fields": [
    {"name": "Versioned_UUID", "type": "string"},
    {"name": "Project_Name", "type": "string"},
    {"name": "Tracker_Data", "type": "string"}
  ],
  "Values": [
    [
      "9269c32f-b093-4279-8389-bd12e973a726",
      "MockAlpha",
      "{\"Format\":\"BEJSON\",\"Format_Version\":\"104a\",\"Project_Name\":\"MockAlpha\",\"Project_Version\":\"1.0.0\",\"Package_Version\":\"100\",\"Versioned_UUID\":\"9269c32f-b093-4279-8389-bd12e973a726\"}"
    ]
  ]
}
```

### 4.3 Pillar 3: Snapshot Chunks (`storage/project_snapshots/...`)
The snapshot chunk represents the physical database payload of the project. It packages every file, directory marker, and permission state into a single portable table.

- **File Path Convention**: `storage/project_snapshots/<project>/data/<project>-V<ver>-PKG<pkg>.bejson`
- **Header Metadata**:
  - `Project_Name`: Name of the captured project.
  - `Project_Version`: Version of the software.
  - `Package_Version`: Package delivery number.
  - `Versioned_UUID`: The version UUID (or `"UNTRACKED"` if created without `--track`).
  - `Tracker_File`: Relative path to the tracker file in `storage/trackers/`.
  - `Original_Path`: Source path where the directory was chunked.
  - `Snapshot_Version`: Schema generation (`2.1`).
  - `Chunk_Digest`: Combined SHA-256 fingerprint of all included files.
- **Records_Type**: `["SnapshotEntity"]`
- **Schema 2.1 Field Definitions**:
  1. `File_Name` (`string`): Basename of the file (e.g. `main.py`).
  2. `File_Extension` (`string`): Extension including dot (e.g. `.py`).
  3. `Relative_Path` (`string`): Normalized POSIX relative path from project root (e.g. `src/main.py`).
  4. `File_Content` (`string`): Full UTF-8 text content for text files; empty string for binaries and empty directories.
  5. `Base64` (`string`): Standard Base64 encoded payload for binary files; empty string for text files and empty directories.
  6. `File_Hash` (`string`): Hexadecimal SHA-256 hash of the unencoded file bytes; empty string for empty directories.
  7. `Flags` (`string`): Serialized JSON array of flags (e.g. `["Is_Binary"]` or `[]`).
  8. `Is_Mounted` (`boolean`): Runtime mounting flag (defaults to `false`).
  9. `Modified_Time` (`string`): ISO-8601 UTC timestamp of file last modified time.
  10. `Is_Empty_Dir` (`boolean`): `true` if the record represents a genuine empty directory; `false` for files.

```json
{
  "Format": "BEJSON",
  "Format_Version": "104a",
  "Format_Creator": "Elton Boehnen",
  "Project_Name": "MockAlpha",
  "Project_Version": "1.0.0",
  "Package_Version": "100",
  "Versioned_UUID": "9269c32f-b093-4279-8389-bd12e973a726",
  "Tracker_File": "trackers/data/MockAlpha.bejson",
  "Original_Path": "/storage/emulated/0/Admin/Projects/MockAlpha",
  "Snapshot_Version": "2.1",
  "Chunk_Digest": "4a7d1ed414474e4033ac29ccb8653d9b139556d3ee8a49c4f1c9c0bc672ca9b1",
  "Records_Type": ["SnapshotEntity"],
  "Fields": [
    {"name": "File_Name", "type": "string"},
    {"name": "File_Extension", "type": "string"},
    {"name": "Relative_Path", "type": "string"},
    {"name": "File_Content", "type": "string"},
    {"name": "Base64", "type": "string"},
    {"name": "File_Hash", "type": "string"},
    {"name": "Flags", "type": "string"},
    {"name": "Is_Mounted", "type": "boolean"},
    {"name": "Modified_Time", "type": "string"},
    {"name": "Is_Empty_Dir", "type": "boolean"}
  ],
  "Values": [
    [
      "main.py",
      ".py",
      "src/main.py",
      "print('hello world')\n",
      "",
      "d7a8fbb307d7809469ca9abcb0082e4f8d5651e46d3cdb762d02d0bf37c9e592",
      "[]",
      false,
      "2026-10-27T13:20:00.000000Z",
      false
    ]
  ]
}
```

### 4.4 Auxiliary Pillar: Superseded Snapshot Archive (`storage/superseded/...`)
When a chunk is generated for a project version that already has an existing snapshot file on disk, Project Chunker calculates the `Chunk_Digest` of both. If the content differs, the existing snapshot is moved into:
`storage/superseded/<project>/<stem>.superseded-<timestamp>.bejson`
- These files are completely decoupled from the Master Registry and `--verify` audits.
- They ensure that accidental version collisions never overwrite prior work silently.
- Administrators can audit or manually purge superseded snapshots when no longer required.

### 4.5 Auxiliary Pillar: Active Project Working Context (`data/active_project.json`)
The active working context is managed via `data/active_project.json`. This file enables the terminal menu and web interfaces to maintain a persistent project selection across invocations.

- **Format**: `BEJSON`
- **Format_Version**: `104a`
- **Records_Type**: `["ActiveProject"]`
- **Fields**: `active_project` (`string`), `set_at` (`string`).
- **Semantics**: Contains exactly one row when an active project is set; contains zero rows when cleared.

---

## 5. Core Engine Algorithms & Internal Workings

### 5.1 Project Identity Detection and Override Resolution Matrix
To support arbitrary directory projects and incoming `.zip` files with varying levels of metadata hygiene, Project Chunker implements a multi-tiered identity resolution engine in `lib_bejson_Projects_tracker.py`.

The identity resolution priority order is strictly evaluated as follows:
```
┌─────────────────────────────────────────────────────────────┐
│ Tier 1: Explicit CLI Overrides                              │
│ (--project-name, --project-version, --package-version)      │
│ Wins unconditionally over all metadata and filenames.       │
└──────────────────────────────┬──────────────────────────────┘
                               │ (if not provided)
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ Tier 2: Internal Tracker File Manifest                      │
│ (bejson_project.json or .bejson_project.json in source)     │
│ Extracts Project_Name, Project_Version, Package_Version.    │
└──────────────────────────────┬──────────────────────────────┘
                               │ (if tracker missing OR scaffold default)
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ Tier 3: Filename Pattern Parsing                            │
│ (Active when --identity-from-filename is supplied)          │
│ Parses patterns: <Name>-V<ver>-PKG<pkg>.zip                 │
│ Example: CLI_AI-V2134-PKG35.zip -> CLI_AI, 2134, 35        │
└──────────────────────────────┬──────────────────────────────┘
                               │ (if filename has no version pattern)
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ Tier 4: Sanitized Directory Basename Fallback               │
│ Basename of directory: spaces -> underscores                │
│ Defaults: Version="100", Package="1"                        │
└─────────────────────────────────────────────────────────────┘
```

#### UUID Retention vs Assignment
- If the source tracker already possesses a valid UUIDv4 `Versioned_UUID`, that UUID is retained.
- If the UUID is absent or contains `"GUID_PLACEHOLDER"` / `"VERSIONED_GUID_PLACEHOLDER"`, the system queries the Master Registry:
  - If the same `Project_Name` + `Project_Version` + `Package_Version` was previously registered, the previously issued `Versioned_UUID` is reused.
  - Otherwise, a new cryptographic `uuid.uuid4()` is minted.
- **Negative Constraint (Policy §7.3)**: `Project_GUID` is **never** auto-generated.

### 5.2 Directory Traversal, Boundary Isolation, and Filtering
Filesystem traversal is implemented via `os.walk` in `lib_bejson_Projects_snapshot.py`, wrapped by the filter engine in `lib_bejson_Projects_filters.py`.

#### Filtering Rules:
1. **Directory Exclusion**: If a directory name matches any active filter pattern (or an explicit `--exclude` argument), the directory is pruned in-place from `dirnames`, preventing the traversal from descending into the subtree.
2. **Path Matching**: If a filter pattern contains a `/` character (e.g. `data/temp/*`), it is evaluated against the normalized relative path of the item from the project root.
3. **Chunk Recursion Guard**: The system automatically excludes its own storage paths and `.bejson` snapshot files, preventing infinite recursion when chunking parents of the storage root.

### 5.3 Streaming File Ingestion, Content Analysis & SHA-256 Hashing
Every file is ingested in binary mode using a 64 KB chunked read buffer.
```python
hasher = hashlib.sha256()
with open(file_path, "rb") as fh:
    while chunk := fh.read(65536):
        hasher.update(chunk)
file_hash = hasher.hexdigest()
```
- Content length is checked against `max_file_mb`. Files exceeding the limit are skipped with an informational log entry.
- File modification times are recorded with microsecond precision: `datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()`.

### 5.4 Binary Detection, Base64 Encoding & Payload Packing
Binary files are distinguished from text files using a robust heuristic:
1. The first 8,192 bytes of the file are inspected.
2. If a null byte (`b'\x00'`) is present, or if decoding the chunk as UTF-8 raises a `UnicodeDecodeError`, the file is flagged as binary.
3. If binary storage is enabled (`--binary` or configuration default `Binary_Enabled=true`):
   - `File_Content` is set to `""`.
   - `Base64` is populated with `base64.b64encode(raw_bytes).decode('ascii')`.
   - `Flags` is populated with `["Is_Binary"]`.
4. If binary storage is disabled (`--no-binary`):
   - The file is skipped entirely from the snapshot.

### 5.5 Empty Directory Capture Semantics & Synthetics
Unlike standard tarballs or zip utilities that often drop empty folders, Project Chunker preserves project directory structure:
- If a folder in the project contains zero files and zero child folders after applying all filters, an entry is written to `SnapshotEntity`:
  - `File_Name`: Directory basename.
  - `Relative_Path`: Directory relative path.
  - `Is_Empty_Dir`: `true`.
  - `File_Content`, `Base64`, `File_Hash`: Set to empty strings.
- **Suppression Rule**: Empty directories resulting solely from filter eliminations (e.g. a directory that only contained `__pycache__`) are not recorded as empty directories.

### 5.6 Snapshot Content Digest Generation & Collision Detection
To detect duplicate chunk requests and protect existing data:
1. The system collects all `Relative_Path` and `File_Hash` pairs from the generated snapshot.
2. The pairs are sorted alphabetically by `Relative_Path` and concatenated into a deterministic canonical digest string.
3. A master `Chunk_Digest` is computed via `hashlib.sha256(canonical_string.encode('utf-8')).hexdigest()`.
4. If a snapshot file already exists for that version:
   - If the existing file's `Chunk_Digest` matches the new digest, the operation reports identical content and exits cleanly.
   - If the digests differ, the conflict resolution strategy (`--on-conflict`) takes over:
     - `backup`: Archives the existing file to `storage/superseded/` and writes the new snapshot.
     - `refuse`: Aborts execution immediately with exit code `1`, writing nothing.

### 5.7 The Zero-Data-Loss Atomic Scoop Verification Algorithm
The `--scoop` command performs safe archiving and physical removal of the source files. Because source deletion carries inherent risk, Project Chunker enforces an atomic, multi-stage verification gate:

```
[ Stage 1: Build Snapshot ]
  Scan source, compute hashes, serialize to storage/project_snapshots/...

[ Stage 2: Verify Write ]
  Flush file buffers to disk. Re-open and parse the snapshot .bejson from disk.

[ Stage 3: Live Byte-by-Byte Verification ]
  For every file recorded in the re-read snapshot:
    1. Verify file exists on live filesystem.
    2. Read live file bytes from filesystem.
    3. If text: verify live_bytes.decode('utf-8') == snapshot.File_Content.
    4. If binary: verify base64.b64encode(live_bytes) == snapshot.Base64.
    5. Compute sha256(live_bytes) and verify == snapshot.File_Hash.
    
  IF ANY CHECK FAILS:
    ABORT IMMEDIATELY. NO FILES ARE DELETED. SNAPSHOT IS RETAINED.

[ Stage 4: Verified Unlinking ]
  ONLY AFTER 100% of files pass byte and hash verification:
    1. Unlink every verified source file.
    2. Prune empty parent directories bottom-up.
    3. Retain root folder if outside temporary staging.
```

### 5.8 Complete Restoration and Deployment Pipeline
Restoring a snapshot via `--restore <UUID>` reverses the chunking process:
1. **Registry Lookup**: Resolves the `Versioned_UUID` (or unique prefix >= 8 characters) in `storage/master/data/registry.bejson`.
2. **Snapshot Resolution**: Locates and validates the corresponding `.bejson` snapshot file.
3. **Destination Resolution**: Targets the original captured path (`Original_Path`) or the path supplied via `--dest`.
4. **Collision Pre-Check**: If destination files exist and `--overwrite` is not passed, restoration halts to prevent accidental file clobbering.
5. **Reconstruction**:
   - Recreates parent directories.
   - If `Is_Empty_Dir` is `true`: creates directory.
   - If `Is_Binary` flag is present: decodes `Base64` and writes binary bytes.
   - Otherwise: writes `File_Content` encoded as UTF-8.
   - Restores recorded file modification timestamps (`Modified_Time`).
6. **Tracker Re-emission (`--write-tracker`)**: Re-creates `bejson_project.json` in the restored root using the `Tracker_Data` stored in the snapshot or Tracker DB.

### 5.9 Automated `.zip` Intake & Staging Pipeline
When a `.zip` archive is supplied as the target argument to `project_chunker.py`:
1. **Safety Audit**: Validates zip integrity and guards against ZipSlip path traversal vulnerabilities (`..` components or absolute path targets).
2. **Isolated Staging**: Extracts the archive into a temporary staging folder `.chunker_stage_<uuid>/` created adjacent to the zip archive.
3. **Nested Directory Unnesting**: If the zip archive extracts to a single top-level folder (e.g. `MyProject-V1/`), the engine transparently identifies the nested root as the project root.
4. **Tracking & Chunking**: Runs `--track` and `--chunk` against the staging directory, honoring `--identity-from-filename` if set.
5. **Verified Scoop of Staging**: Executes a verified scoop on the staging folder, confirming full capture and removing the staging folder.
6. **Archive Retention Policy**: Deletes the source `.zip` archive unless `--keep-zip` was passed.

### 5.10 Storage Integrity Auditing Engine (`projects_verify_storage`)
Invoked via `--verify`, the audit engine traverses the storage root:
- Checks every entry in `storage/master/data/registry.bejson`.
- Confirms the referenced tracker file exists in `storage/trackers/`.
- Verifies that the referenced snapshot file exists in `storage/project_snapshots/`.
- Validates that both tracker and snapshot conform strictly to BEJSON 104a schemas.
- Re-reads every file row in the snapshot, decodes content/Base64, and re-hashes every payload to ensure the SHA-256 matches `File_Hash` exactly, detecting silent disk corruption or manual tampering.

---

## 6. Complete CLI Specification & Operational Reference

### 6.1 Invocation Syntax and Global Flags
```bash
python3 CLI/project_chunker.py [target] [action_flags] [options]
```

| Flag | Argument | Description |
| :--- | :--- | :--- |
| `target` | `PATH` | Target directory to process, or a `.zip` file for intake. Required with `--track`, `--chunk`, or `--scoop`. |
| `--storage-root` | `DIR` | Override the storage root directory. Defaults to `<project>/storage` or `$PROJECT_CHUNKER_STORAGE_ROOT`. |
| `--dry-run` | None | Execute full inspection and plan phases without writing files or modifying storage. |
| `--log-level` | `LEVEL` | Logging verbosity: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`. Default: `INFO`. |
| `--json` | None | Emit structured machine-readable JSON output on `stdout`. Log entries route to `stderr`. |
| `--version` | None | Display program version (`project_chunker 1.8.0`) and exit. |
| `--help` | None | Display command-line options and usage summary. |

### 6.2 Tracking Flags and Identity Configuration
| Flag | Argument | Description |
| :--- | :--- | :--- |
| `--track` | None | Detect or create tracker, record in Tracker DB, and register UUID in Master Registry. |
| `--project-name` | `NAME` | Override project name. Overrides manifest, directory name, and filename. |
| `--project-version`| `VER` | Override `Project_Version` for this run. |
| `--package-version`| `PKG` | Override `Package_Version` for this run. |
| `--identity-from-filename`| None | Ingest `V#-PKG#` from filename (e.g. `App-V200-PKG10.zip`) when tracker is missing or generic. |

### 6.3 Snapshot & Scoop Flags
| Flag | Argument | Description |
| :--- | :--- | :--- |
| `--chunk` | None | Capture project filesystem into a BEJSON 104a snapshot chunk. |
| `--scoop` | None | Implies `--track --chunk`. Byte-verifies snapshot against live data and deletes source files. |
| `--delete-filtered`| None | With `--scoop`: also delete files matched by filters. Requires `--confirm-delete-filtered`. |
| `--confirm-delete-filtered` | None | Mandatory confirmation flag for `--delete-filtered`. |
| `--keep-zip` | None | When target is a `.zip` file: chunk content but do not delete the `.zip` archive. |
| `--on-conflict` | `backup`\|`refuse`| Action when version exists with different content. Default: `backup` (moves old to `superseded/`). |

### 6.4 Restoration & Deployment Flags
| Flag | Argument | Description |
| :--- | :--- | :--- |
| `--restore` | `UUID` | Deploy a registered version (full UUID or unique prefix >= 8 chars). |
| `--dest` | `DIR` | Destination directory for restoration. Default: original captured path. |
| `--overwrite` | None | Allow overwriting existing destination files during restore. |
| `--write-tracker` | None | Re-create `bejson_project.json` in destination from embedded tracker data. |

### 6.5 Inspection, Listing & Auditing Flags
| Flag | Argument | Description |
| :--- | :--- | :--- |
| `--list` | None | Display all registered projects and versions in the Master Registry. |
| `--versions` | `PROJECT` | Display all registered versions for the specified project name. |
| `--verify` | None | Run complete storage integrity audit across all registries, trackers, and snapshots. |
| `--verify-project`| `PROJECT` | With `--verify`: restrict audit reporting to the specified project. |

### 6.6 Exclusion, Filtering & Boundary Limit Flags
| Flag | Argument | Description |
| :--- | :--- | :--- |
| `--binary` | None | Explicitly enable binary file capture (encoded via Base64). |
| `--no-binary` | None | Explicitly ignore binary files during chunking. |
| `--exclude` | `DIRNAME` | Extra directory name to skip while chunking (repeatable). |
| `--filters-config`| `FILE` | Custom path to BEJSON filter configuration file. |
| `--no-filters` | None | Disable all directory and file filters; capture complete raw directory. |
| `--filter` | `ID` | Force a specific filter category on (repeatable). |
| `--no-filter` | `ID` | Force a specific filter category off (repeatable). |
| `--list-filters` | None | Print current filter categories, patterns, and status, then exit. |
| `--ext` | `.EXT` | Restrict chunking exclusively to specified extensions (repeatable, e.g. `--ext .py`). |
| `--max-file-mb` | `FLOAT` | Maximum file size in megabytes to capture. Files larger are skipped. Default: 50.0. `0` disables limit. |

### 6.7 Machine-Readable JSON Output Specification
When invoked with `--json`, the CLI suppresses standard stdout formatting and outputs a validated JSON dictionary:

```json
{
  "project_name": "MockAlpha",
  "project_version": "1.0.0",
  "package_version": "100",
  "versioned_uuid": "9269c32f-b093-4279-8389-bd12e973a726",
  "tracker_file": "trackers/data/MockAlpha.bejson",
  "snapshot_file": "project_snapshots/MockAlpha/data/MockAlpha-V1.0.0-PKG100.bejson",
  "files_chunked": 4,
  "bytes_chunked": 12450,
  "chunk_digest": "4a7d1ed414474e4033ac29ccb8653d9b139556d3ee8a49c4f1c9c0bc672ca9b1",
  "outcome": "tracked+chunked",
  "dry_run": false
}
```

### 6.8 Exit Codes and Process Telemetry
- `0`: Execution succeeded (`EXIT_OK`).
- `1`: Operational failure, verification mismatch, or conflict refusal (`EXIT_FAIL`).
- `2`: Command-line syntax error, missing argument, or Python version < 3.10 (`EXIT_USAGE`).

---

## 7. Menu Subsystem (`Menu/project_chunker_menu.py`)

### 7.1 Architecture & Script Export Mechanism
`Menu/project_chunker_menu.py` provides an interactive terminal user interface (TUI) conforming to Policy §8.4. It prevents human error by ensuring paths are selected via interactive number menus rather than manual typing.

On launch, the menu executes `load_or_export_config()`:
1. Validates `config/menu_scripts.json`.
2. If absent or points to moved files, re-scans the repository and exports relative script descriptors.
3. Spawns CLI commands as isolated subprocesses (`subprocess.run`), preserving terminal signals.

### 7.2 Numbered Directory Browser Subsystem
When an operation requires a target folder, the menu invokes `lib_bejson_Projects_browser.py`:
- Displays numbered subdirectories in the current working directory.
- Supports entering directory numbers to descend, `..` to ascend, or `.` to select the current directory.
- Fully compatible with restricted Android emulated storage paths.

### 7.3 Non-Interactive Batch Execution (`--run`)
The menu can execute operations non-interactively for headless scripts:
```bash
python3 Menu/project_chunker_menu.py --run list
python3 Menu/project_chunker_menu.py --run verify
```

---

## 8. Database Migration Subsystem (`Helper/project_db_migrator.py`)

The database migrator automates high-volume ingestion of legacy archives into Project Chunker.

### 8.1 Automated Configuration Generation
If `config/migrator_config.json` is missing, the migrator automatically scaffolds it:
```json
{
  "Format": "BEJSON",
  "Format_Version": "104a",
  "Source_Mode": "project_index",
  "Custom_Source_Path": "",
  "Dry_Run": true,
  "Auto_Verify": true,
  "Delete_After_Verify": true,
  "Quarantine_Invalid": true,
  "Max_Batch_Size": 100
}
```

### 8.2 Migration Phase 1: Scan & Candidate Classification
Scans the source directory for `.zip` files:
- Inspects archive structure and validates filename syntax (`<Name>-V<ver>-PKG<pkg>.zip`).
- Checks candidate against the migration ledger (`migration/migration_ledger.bejson`).
- Categorizes archives as `valid`, `invalid`, or `already_migrated`.

### 8.3 Migration Phase 2: Batch Chunk Execution
Executes chunking across all valid candidates:
- Extracts archive to temporary staging.
- Chunks content, records tracker, and links to registry.
- Updates migration ledger status to `chunked`.

### 8.4 Migration Phase 3: Integrity Verification & Archive Pruning
- Re-verifies every created snapshot against the original zip archive checksums.
- When `Delete_After_Verify` is true, safely unlinks the original `.zip` archive.
- Marks ledger status as `verified` and `deleted`.

### 8.5 Migration Ledger, Quarantine & Recovery
Archives with corrupted zip tables or unsafe paths are quarantined to `migration/quarantine/` with an error report, preventing data loss while keeping the ingestion pipeline active.

---

## 9. Web Platform Deep Dive (`Web/`)

### 9.1 Flask Architecture and Blueprint Layout
The web interface provides an accessible GUI for desktop and mobile browsers:
- Entry point: `Web/project_chunker_flask.py`
- Framework: Lightweight Flask using standard library templates and BECSS styling.
- Port Resolution: Reads `config/web_config.json`, binding to the first available port in `port_start` (default `5120`).

### 9.2 Unified Chunking Interface (`chunker.js`)
The web UI consolidates Track, Chunk, Scoop, and Zip Intake into a single reactive interface:
- Radio controls for Source (`Directory` vs `Zip File`).
- Action selectors (`Track Only`, `Chunk Only`, `Track + Chunk`, `Scoop (Archive & Wipe)`).
- Filter toggle switches with live pattern previews.

### 9.3 Live CLI Command Generation Engine
As the user manipulates checkboxes, paths, and switches in the web interface, `chunker.js` dynamically generates the exact, copy-pasteable CLI command in real-time, providing an interactive bridge between GUI and CLI.

### 9.4 Tracker Editor & Atomic Snapshot Synchronization
The web platform allows editing tracker metadata (`Description`, `Author`, `Main_Executable`, `Runtime_Type`):
1. **Preview Plan**: Previews field changes and flags snapshot updates.
2. **Atomic Patch**: Updates the Tracker DB table.
3. **Snapshot Sync**: Propagates modified tracker headers into the corresponding snapshot `.bejson` files on disk, maintaining relational consistency.

### 9.5 Asynchronous Job Runner and BEJSON Journal
Long-running chunking, scooping, and migration operations execute in a single-slot background worker thread.
- Progress, logs, and cancellation tokens are recorded into `data/web_jobs.bejson`.
- Web clients poll `/api/jobs/<job_id>` for real-time progress bars and log streams.

### 9.6 Complete REST API Endpoint Specification
| Endpoint | Method | Purpose |
| :--- | :--- | :--- |
| `/api/browse` | GET | List directory contents for web file picker. |
| `/api/preview/<op>` | POST | Generate a read-only execution plan for track, chunk, scoop, or zip intake. |
| `/api/run/<op>` | POST | Dispatch live execution job to the background runner. |
| `/api/jobs` | GET | List active and historical background jobs. |
| `/api/jobs/<id>` | GET | Retrieve progress and logs for a specific job. |
| `/api/jobs/<id>/cancel` | POST | Request cancellation of a running background job. |
| `/api/projects` | GET | Retrieve all registered projects, versions, and file counts. |
| `/api/projects/<uuid>/files` | GET | List all files stored within a specific snapshot chunk. |
| `/api/filters` | GET | Retrieve all filter categories, patterns, and active flags. |
| `/api/filters/test` | POST | Test filter matching rules against arbitrary path strings. |
| `/api/settings` | GET/POST| Read or update server and storage settings. |
| `/api/migrator/plan` | GET | Generate batch migration execution plan. |
| `/api/migrator/run` | POST | Start batch migration job. |

---

## 10. Filter Engine & Configuration (`config/chunker_filters.json`)

### 10.1 Schema & Filter Record Structure
Filters are stored in `config/chunker_filters.json` conforming to BEJSON 104a (`Records_Type: ["FilterRule"]`).
Fields: `Category_Id`, `Category_Name`, `Pattern_List`, `Is_Active`, `Description`.

### 10.2 Filter Categories and Active Status
| Category ID | Name | Default Status | Sample Patterns |
| :--- | :--- | :--- | :--- |
| `git` | Git Metadata | Active | `.git`, `.gitignore`, `.gitattributes` |
| `nodejs` | Node.js Dependencies | Active | `node_modules` |
| `python_cache` | Python Caches | Active | `__pycache__`, `*.pyc`, `.pytest_cache`, `.tox` |
| `virtualenv` | Python Virtual Environments | Active | `.venv`, `venv`, `env` |
| `build_output`| Build Outputs | Active | `dist`, `build`, `out`, `*.egg-info` |
| `chunk_output`| Snapshot Output Chunks | Active | `storage/project_snapshots/*` |
| `chunk_files` | Raw Chunker Tables | Active | `*.bejson` |
| `os_junk` | Operating System Metadata | Active | `.DS_Store`, `Thumbs.db` |
| `temp_files` | Temporary Files | Active | `*.tmp`, `*.temp`, `*~`, `.cache` |
| `lockfiles` | Dependency Locks | Inactive | `package-lock.json`, `poetry.lock` |

### 10.3 Fnmatch Syntax, Relative Paths & Glob Rules
- Matching is evaluated using Python's `fnmatch.fnmatchcase` (case-sensitive).
- Patterns without `/` match against the file or directory name exclusively.
- Patterns containing `/` match against the complete relative path from the project root.

---

## 11. Security, Concurrency & Platform Constraints

### 11.1 Single-Instance Execution Locking
To prevent concurrent writes to the Master Registry or simultaneous scoops on the same project:
- Implemented in `lib_bejson_Projects_locks.py`.
- Acquires POSIX flock / pidfile lock at `<storage_root>/.chunker.lock`.
- If another process holds the lock, execution halts with a clear lock acquisition timeout error.

### 11.2 Path Traversal & Boundary Guarding
Implemented in `lib_bejson_Core_bejson_path_guard.py`:
- All incoming paths from user arguments, zip archives, and web endpoints are resolved to absolute canonical paths via `Path.resolve()`.
- Relative paths are audited to ensure they do not escape their assigned roots (guarding against `../` directory traversal attacks).

### 11.3 Android Termux Runtime & `noexec` Mount Workarounds
On Android devices, `/storage/emulated/0` is mounted with the `noexec` mount option, prohibiting direct execution of binary executables and scripts with shebang lines.
- **Rule**: Never attempt to run scripts directly via `./script.py` or `./script.sh`.
- **Workaround**: Always invoke scripts explicitly via their interpreter:
  - `python3 /storage/emulated/0/.../project_chunker.py`
  - `bash /storage/emulated/0/.../script.sh`

---

## 12. Comprehensive Library API Reference

The `lib/` directory encapsulates all operational logic into decoupled modules:

### 12.1 `lib_bejson_Projects_paths.py`
Manages filesystem paths and storage layout models:
- `projects_paths_resolve_storage_layout(storage_root: Optional[Path]) -> StorageLayout`
- `StorageLayout.snapshot_filename(project_name: str, version: str, package: str) -> str`
- `StorageLayout.snapshot_file(project_name: str, version: str, package: str, untracked: bool = False) -> Path`
- `StorageLayout.tracker_file(project_name: str) -> Path`
- `StorageLayout.registry_file() -> Path`
- `StorageLayout.superseded_dir(project_name: str) -> Path`

### 12.2 `lib_bejson_Projects_tracker.py`
Handles tracker discovery, validation, identity overrides, and manifest parsing:
- `IdentityOverride(name: Optional[str], version: Optional[str], package: Optional[str])`
- `projects_tracker_detect_or_create(target_dir: Path, uuid_lookup: Callable, override: IdentityOverride) -> Tuple[dict, bool]`
- `projects_tracker_filename_identity(filename: str) -> Optional[Tuple[str, str, str]]`
- `projects_tracker_is_scaffold_default(version: str, package: str) -> bool`

### 12.3 `lib_bejson_Projects_snapshot.py`
Core filesystem scanner, binary encoder, and snapshot serializer:
- `projects_snapshot_build(target_dir: Path, options: SnapshotOptions) -> Tuple[dict, SnapshotStats]`
- `projects_snapshot_save(snapshot_doc: dict, target_file: Path) -> None`
- `projects_snapshot_load(snapshot_file: Path) -> dict`
- `projects_snapshot_digest(snapshot_doc: dict) -> str`
- `projects_snapshot_supersede(layout: StorageLayout, existing_path: Path, project_name: str) -> Path`

### 12.4 `lib_bejson_Projects_registry.py`
Manages the Master Registry index table:
- `projects_registry_load(registry_file: Path) -> dict`
- `projects_registry_register(registry_file: Path, record: dict) -> None`
- `projects_registry_list(layout: StorageLayout) -> List[dict]`
- `projects_registry_versions(layout: StorageLayout, project_name: str) -> List[dict]`

### 12.5 `lib_bejson_Projects_pipeline.py`
High-level operational dispatcher coordinating multi-module workflows:
- `projects_pipeline_run(options: RunOptions) -> RunResult`
- `projects_pipeline_run_zip(zip_path: Path, options: RunOptions) -> RunResult`
- `projects_pipeline_restore(uuid: str, dest: Optional[Path], options: RestoreOptions) -> RestoreResult`

### 12.6 `lib_bejson_Projects_verify.py`
Storage integrity auditor:
- `projects_verify_storage(layout: StorageLayout, project: Optional[str] = None) -> VerifyResult`
- `projects_verify_snapshot_payload(snapshot_path: Path) -> List[str]`

### 12.7 `lib_bejson_Projects_archive.py`
Verified scoop execution and source pruning:
- `projects_archive_verify_and_scoop(target_dir: Path, snapshot_doc: dict, options: ScoopOptions) -> ScoopStats`

### 12.8 `lib_bejson_Projects_filters.py`
Filter configuration loader and pattern matcher:
- `projects_filters_load(config_path: Path) -> FilterConfig`
- `projects_filters_evaluate(rel_path: str, is_dir: bool, config: FilterConfig) -> bool`

### 12.9 `lib_bejson_Projects_edit.py`
Tracker editing, manifest patching, and snapshot synchronization:
- `projects_edit_details(layout: StorageLayout, project_name: str) -> dict`
- `projects_edit_plan(layout: StorageLayout, project_name: str, patch: dict, sync_snapshots: bool) -> EditPlan`
- `projects_edit_apply(layout: StorageLayout, project_name: str, patch: dict, sync_snapshots: bool) -> EditResult`

### 12.10 `lib_bejson_Projects_migrate.py`
Batch migration pipeline for legacy project archives:
- `projects_migrate_scan(cfg: dict, source_dir: Path, layout: StorageLayout, ledger: dict) -> List[Candidate]`
- `projects_migrate_chunk_phase(layout: StorageLayout, cfg: dict, chunker_cfg: Path) -> PhaseResult`
- `projects_migrate_verify_phase(layout: StorageLayout, cfg: dict) -> PhaseResult`

### 12.11 `lib_bejson_Projects_active.py`
Active working project state persistence:
- `projects_active_get(path: Path) -> Optional[str]`
- `projects_active_set(path: Path, project_name: str) -> None`
- `projects_active_clear(path: Path) -> None`

### 12.12 `lib_bejson_Projects_locks.py`
Filesystem locking for process synchronization:
- `projects_lock_acquire(lock_path: Path, timeout_seconds: float = 10.0) -> LockContext`
- `projects_lock_release(lock_ctx: LockContext) -> None`

---

## 13. Disaster Recovery & Troubleshooting Manual

### 13.1 Corrupted Master Registry Reconstruction
If `storage/master/data/registry.bejson` is deleted or corrupted:
1. Initialize a clean Master Registry table conforming to Section 4.1.
2. Iterate through every snapshot chunk in `storage/project_snapshots/*/*/*.bejson`.
3. Read the snapshot header (`Versioned_UUID`, `Project_Name`, `Project_Version`, `Package_Version`, `Tracker_File`, `Original_Path`).
4. Re-insert the records into `storage/master/data/registry.bejson` using `lib_bejson_Projects_registry.py`.

### 13.2 Broken/Dangling Tracker Pointers
If `--verify` reports that a registered UUID points to a missing tracker file:
1. Inspect the corresponding snapshot chunk in `storage/project_snapshots/`.
2. Extract the embedded `bejson_project.json` manifest stored in the snapshot metadata.
3. Write a fresh tracker table in `storage/trackers/data/<project>.bejson`.
4. Re-run `--verify` to confirm resolution.

### 13.3 Handling Interrupted Scooping Runs
Because `--scoop` does not unlink a single file until 100% of files have passed verification, an interruption during Stage 1, 2, or 3 leaves the source directory 100% intact.
- If interrupted during Stage 4 (unlinking): simply re-run `--scoop`. The verified snapshot already exists; re-running completes the removal safely.

### 13.4 Hash Mismatches During Verification
If `--verify` reports a hash mismatch for a snapshot record:
1. Do not use the file for restoration.
2. Check `storage/superseded/<project>/` for an earlier verified snapshot of that version.
3. If the live source still exists, re-chunk using `--on-conflict backup` to generate a fresh, verified snapshot.

### 13.5 Resolving File Lock Stalls
If a crash or kill signal left a stale lock file:
```bash
# Check if a live chunker process is running:
pgrep -f project_chunker

# If no process is running, remove the stale lock:
rm -f /storage/emulated/0/Admin/System/Project_,Chunker/storage/.chunker.lock
```

---

## 14. Document Metadata and Change History

- **Current Edition**: Version `1.8.0` (Comprehensive Specification)
- **Author**: Elton Boehnen
- **Contact**: `boehnenelton2024@gmail.com`
- **Canonical Repository**: [github.com/boehnenelton](https://github.com/boehnenelton)
- **Host System**: Android Termux (Linux ARM64 / POSIX)
- **Audit Compliance**: Verified for 100% Zero-Filler Compliance, Strict Typing, and BEJSON 104a Positional Integrity.
