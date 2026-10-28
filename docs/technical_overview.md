# Project Chunker — Technical Overview (V1.5.0-PKG105)

Author: Elton Boehnen · boehnenelton2024@gmail.com · boehnenelton2024.pages.dev · [github.com/boehnenelton](https://github.com/boehnenelton)

## Layout
```
bejson_project.json        tracker (Tier 4, single)
CLI/project_chunker.py     flag parsing + reporting only
Menu/project_chunker_menu.py   menu wrapper (reads config, runs CLI)
config/config.json         standard ScriptConfig
config/menu_scripts.json   exported script locations (generated)
lib/lib_bejson_Core_*      frozen Core BEJSON libraries (unmodified)
lib/lib_bejson_Projects_*  reusable Projects family
storage/                   runtime data (3 pillars)
dev/                       checklist, ledgers, tests
```

## Storage pillars (all BEJSON 104a, validated before every write)
| Pillar | Path | Content |
|---|---|---|
| Tracker DB | `storage/trackers/data/<project>.bejson` | `TrackerRecord`: Versioned_UUID, Project_Name, Tracker_Data (JSON string). One row per UUID; history kept. |
| Master registry | `storage/master/data/registry.bejson` | `RegistryRecord`: Versioned_UUID → Tracker_File (relative). |
| Snapshots | `storage/project_snapshots/<project>/data/<project>-V<ver>-PKG<pkg>.bejson` | `SnapshotEntity` rows: path, content (UTF-8 or base64), SHA-256, Is_Binary. Headers carry Versioned_UUID and Tracker_File. |

Chunks made without tracking (UUID not in the registry) are stored apart: `storage/untracked_chunks/<project>-V<ver>-PKG<pkg>.bejson`. Chunks from pre-1.7.1 packages (`V<ver>-PKG<pkg>.bejson`) remain readable.

## Job semantics
- `--track`: find `bejson_project.json` (or `.bejson_project.json`) in the target, else derive a tracker from the folder name (spaces → `_`, `V#-PKG#` honoured, default `V100-PKG1`). Store in tracker DB, register UUID. The target is never modified.
- `--chunk`: snapshot the directory. Without `--track` nothing is registered; the chunk goes to `untracked_chunks/` unless the UUID is already known (then it is a tracked chunk).
- `--track --chunk`: tracker, registry, then snapshot keyed to the registered UUID.
- UUID rules: valid tracker UUID kept; absent/placeholder UUID → previously issued UUID for the same name+version+package, else new uuid4. `Project_GUID` is never generated.
- `--list`, `--verify` are read-only. `--verify` validates every file, checks registry → tracker linkage, and recomputes every snapshot hash.

## Menu and config
`Menu/project_chunker_menu.py --export-config` writes `config/menu_scripts.json`: per script `script_id`, `kind`, `script_path` (relative to project root), `relative_to_menu`, description. The menu loads it (re-exporting if missing or stale), resolves the CLI through it, and runs it as a subprocess. Folders are chosen with a numbered browser; no path is ever typed.

## Roots
Project root derives from the script location. Storage root: `--storage-root` > `$PROJECT_CHUNKER_STORAGE_ROOT` > `<project>/storage`.

## Testing
`python3 -m unittest discover -s dev/tests -v`

## V1.1.0 — project manager
`--track` is the line between a simple chunker and a project manager: it writes the tracker DB and registers the version (UUID, original path, status). `--chunk` alone writes only a snapshot and registers nothing.
- **Lifecycle**: tracked → chunked → archived (scoop) → restored.
- **Scoop**: chunk, reload snapshot from disk, byte-compare to source, re-hash each file, unlink only snapshot files, prune empty dirs.
- **Restore**: `--restore UUID` to the original location or `--dest`; `--write-tracker` re-emits `bejson_project.json` from the embedded `Tracker_Data` (basis for the later tracker-edit/redeploy system).
- **Snapshot schema 2.1 columns**: File_Name, File_Extension, Relative_Path, File_Content, Base64, File_Hash, Flags, Is_Mounted, Modified_Time, Is_Empty_Dir. `Flags` is JSON array text (e.g. `["Is_Binary"]`). `Modified_Time` is ISO-8601 UTC with microseconds. `Is_Empty_Dir` true marks a captured genuinely empty directory (File_Content/Base64/File_Hash empty, never hashed); empty dirs are not captured when inside excluded trees, when only emptied by filters, or for the root. Schema 2.0 and legacy snapshots remain readable.
- **Binary_Enabled** precedence: CLI > project manifest header > `config/config.json` header > true.
- Storage root: `--storage-root` > `$PROJECT_CHUNKER_STORAGE_ROOT` > `<project>/storage`.

## V1.2.0
- `Web/project_management_flask.py`: read-only viewer (collapsible projects, version cards); logic in `lib_bejson_Projects_view`.
- Core chunking 1.11.0: `File_Hash` verified post-chunk and on unchunk.

## V1.3.0
- **Zip mode**: `project_chunker.py <file.zip> --track|--chunk|--scoop [--keep-zip]`. Flow: lock -> inspect (CRC, safety) -> stage -> verify extraction -> track+chunk+verified scoop of staging -> delete zip if fully captured. Staging dir `.chunker_stage_*` sits beside the zip and is always removed.
- **Migrator**: `Helper/project_db_migrator.py` (auto config + dry run, menu) and `Web/project_migrator_flask.py` (UI, port 5056). Config `config/migrator_config.json`; data under `<storage root parent>/migration/` (ledger, reports, quarantine). Ledger statuses: chunked, partial, invalid, chunk_failed, verified, verify_failed, deleted, quarantined, kept.

## Filters 
`config/chunker_filters.json` (BEJSON 104a, rows) controls directory/file-name filters and the `binary_enabled` / `apply_to_zip` switches. Patterns are case-sensitive fnmatch globs matched on the directory or file name (a pattern containing `/` matches the relative path). Per-run overrides: `--no-filters`, `--filter ID`, `--no-filter ID`. See `lib_bejson_Projects_filters.py`.

## Web app (V1.7.0)
`Web/project_chunker_flask.py` launches `Web/chunker_web/` (`core` context/options/serializers, `jobs` single-slot runner + BEJSON journal, `picker`, `pages` sidebar map (one Chunk page; /track, /scoop, /zip redirect to /chunk?mode=...), `api_work` previews/runs/jobs, `api_data` projects/filters/settings/migrator). Templates `Web/templates/chunker/`, assets `Web/static/chunker/chunker.js` and `Web/static/becss/becss-chunker.css`. Port: first free from `web_config.json` `port_start` (default 5120). API: `/api/preview/<op>` (track|chunk|scoop|zip|restore, read-only, returns `plan_id`), `/api/run/<op>` (+verify, migrator; starts a job), `/api/jobs[/id][/cancel]`, `/api/projects[/uuid/files|file|export]`, `/api/download/<name>`, `/api/filters[/test|/reset]`, `/api/settings`, `/api/migrator/{config,source,plan,ledger}`, `/api/browse`, `/api/meta`, `/api/dashboard`.

### Chunk page (V1.6.0)
`chunker.js / initChunk (root, state, source, action, binary, useFilters, deleteFiltered, keepZip, applyZip)`: one form replaces the Track, Chunk, Scoop and Zip Intake pages. Radios: Source (folder|zip), Action (track|chunk|track+chunk|scoop), Binary (inherit|on|off), zip filter policy. Checkboxes: Use filters, each filter, Also delete filtered, Keep zip. Fields: max MB, extensions, extra excluded dirs. Mapping to API ops: track->`track`, chunk and track+chunk->`chunk` (`track` flag), scoop->`scoop`, zip source->`zip`. A live "equivalent CLI command" line mirrors the selection. API unchanged.

### Tracker editing (V1.7.0)
`lib_bejson_Projects_edit.py / projects_edit_details (layout, name)`, `projects_edit_plan (layout, name, patch, update_snapshots)`, `projects_edit_apply (layout, name, patch, update_snapshots, emit)`, `projects_edit_normalize (changes)`, `_build (layout, name, patch_raw, update_snapshots)`, `_patch_doc (doc, patch)`, `_stage_doc (path, doc, what)`, `_stage_snapshot (job, plan, tpatch)`, `_commit (actions)`, `_rollback (done)`, `_post_check (layout, plan)`.
`chunker.js / initProjects (root, cards, open)`: `buildCard (p, idx)`, `fill (c)`, `buildEditor (c, d, box)`, `sync (projects)`, `reloadAfter (oldName, newName, msg)`.
`api_work.py / _tracker_inputs (ctx, body)`: preview/run op `tracker`. `api_data.py / api_tracker_details (name)`.


lib_bejson_Projects_paths / StorageLayout.snapshot_filename (project_name, project_version, package_version)
lib_bejson_Projects_paths / StorageLayout.snapshot_file (project_name, project_version, package_version, untracked)
lib_bejson_Projects_paths / StorageLayout.legacy_snapshot_file (project_name, project_version, package_version)
lib_bejson_Projects_snapshot / projects_snapshot_remove_legacy (layout, info)
lib_bejson_Projects_edit / _renamed_chunk_name (filename, old_name, new_name)

## V1.8.0 — flaw remediation and active project
lib_bejson_Projects_tracker / IdentityOverride (name, version, package) — explicit identity; `.validate()`, `.active()`
lib_bejson_Projects_tracker / projects_tracker_detect_or_create (target_dir, uuid_lookup, override)
lib_bejson_Projects_tracker / projects_tracker_filename_identity (file_name) — (name, ver, pkg) or None
lib_bejson_Projects_tracker / projects_tracker_is_scaffold_default (version, package)
lib_bejson_Projects_snapshot / projects_snapshot_digest (doc) — content fingerprint (paths + file hashes)
lib_bejson_Projects_snapshot / projects_snapshot_supersede (layout, existing, project_name) — copy to superseded/
lib_bejson_Projects_paths / StorageLayout.superseded_dir
lib_bejson_Projects_pipeline / RunOptions + identity, identity_from_filename, filename_identity, on_conflict ("backup"|"refuse")
lib_bejson_Projects_pipeline / RunResult.superseded
lib_bejson_Projects_verify / projects_verify_storage (layout, project=None); projects_verify_filter (layout, issues, project)
lib_bejson_Projects_migrate / projects_migrate_resolve_source (cfg) — raises ProjectsMigrateError when unresolvable
lib_bejson_Projects_active / projects_active_path (data_dir); projects_active_get (path); projects_active_set (path, name); projects_active_clear (path); projects_active_valid_name (name)
chunker_web/active / active_resolve (ctx, projects); active_name (ctx); active_source (ctx); active_follow_rename (ctx, old, new); register_active (app, ctx)
CLI project_chunker / --project-name, --project-version, --package-version, --identity-from-filename, --on-conflict {backup,refuse}, --verify-project

### Storage additions
- `<storage>/superseded/<project>/<stem>.superseded-<UTC>.bejson` — previous snapshots replaced by different content (never read by verify or the registry; delete by hand when no longer wanted).
- `<data_dir>/active_project.json` — BEJSON 104a, Records_Type ActiveProject, fields active_project, set_at; zero rows = none.

## V1.8.0-PKG113 — interactive zip project name selection
lib_bejson_Projects_zip / projects_zip_preview_identity (zip_path) -> Tuple[str, str, str] — in-memory preview of zip identity without extraction
lib_bejson_Projects_migrate / projects_migrate_chunk_phase (layout, cfg, config_file, progress, cancelled, filters_file, prompt_name_callback) — supports interactive project name selection and callbacks
lib_bejson_Projects_browser / projects_browser_pick_directory (start, read_choice, write, include_ext) — browser picker supporting matching file selection
CLI project_chunker / --prompt-name (-i) — interactive prompt to select/confirm project name for each zip or target
Helper project_db_migrator / --prompt-name, Prompt_Project_Name menu toggle
Menu project_chunker_menu / intake-zip command


