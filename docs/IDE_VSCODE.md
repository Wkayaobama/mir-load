# mr-load in an IDE — VS Code (also Cursor), local copy, breakpoints, one-click steps

Same Ubuntu environment as `docs/WEZTERM_UBUNTU.md`, driven from the IDE
instead of a terminal: the repo opens **inside** the Ubuntu container, every
pipeline step is a Task, every runner sub-command is a Debug configuration you
can breakpoint, and the artefacts (`.mrload/*.csv`, the SQLite ledger, dbt's
compiled SQL) open in the editor.

## 1. Open the local copy inside Ubuntu

**Route A — Dev Container (recommended; Windows, macOS, Linux with Docker Desktop)**

1. Install VS Code + the *Dev Containers* extension (`ms-vscode-remote.remote-containers`).
2. `git clone https://github.com/Wkayaobama/mir-load.git`, checkout
   `claude/mr-load-library-system-dphpbj`, open the folder.
   On Windows, do this in Git Bash **after** the one-time LF fix in
   `docs/WEZTERM_UBUNTU.md` ("Windows checkouts") if the clone predates `.gitattributes`.
3. `F1` → **Dev Containers: Reopen in Container**. VS Code builds
   `scripts/dev/Dockerfile` (Ubuntu 24.04 + gcloud/bq + venv at `/opt/venv`),
   mounts the repo, mounts the `mrload-gcloud` volume for credentials, and
   runs `scripts/bootstrap_ubuntu.sh --no-rehearsal` as the post-create step
   (unit tests, `.env` from the template).
4. The status bar shows `Dev Container: mr-load`; the integrated terminal is
   Ubuntu bash, the Python interpreter is `/opt/venv/bin/python`.

**Route B — WSL (Windows, no Docker)**: install the *WSL* extension, open the
clone that lives in `~/mir-load` on the Linux side (`F1` → **WSL: Open Folder
in WSL**), run the Task `mr-load: bootstrap this machine` once. Interpreter is
`${workspaceFolder}/.venv/bin/python` (already in `.vscode/settings.json`).

**Route C — PyCharm**: Settings → Python Interpreter → *On Docker* with
`scripts/dev/Dockerfile` (interpreter `/opt/venv/bin/python`), or *On WSL*
with `~/mir-load/.venv`. Run configurations mirror `.vscode/launch.json`
(module `pipeline.library_files.runner`, arguments per sub-command).

## 2. Credentials from the IDE

Terminal → Run Task → **`mr-load: gauth`** (`scripts/gauth.sh`). It prints a
URL; open it in your normal browser, paste the code back into the task
terminal. Then edit `.env` (created by the bootstrap) → `MRLOAD_BQ_PROJECT`,
`HUBSPOT_SANDBOX_TOKEN`. Every debug configuration reads `.env` through
`envFile`, and the tasks read it through the scripts.

## 3. What the IDE gives you

| you want to | do |
|---|---|
| run a step | `Ctrl+Shift+P` → *Tasks: Run Task* → `mr-load: step <name>` (live gates still ask `YES` in that terminal) |
| watch progress | Task `mr-load: status pane` in a split terminal |
| debug the Drive walk | Run & Debug ▸ `runner ▸ walk (LIVE Drive, depth 1 = the preflight probe)` with a breakpoint in `pipeline/library_files/drive_walker.py` → `dfs_entries` / `ApiDriveLister.list_children` |
| debug without any credentials | `runner ▸ index (OFFLINE, fixture manifest)` — `tests/fixtures/manifest_sample.json` is a small tree with a PO pdf, a parked pdf, a native Slides doc, an orphan and a `70 Tradeshows` subtree |
| reproduce the HubSpot flow offline | compound `debug ▸ mocks + attach LIVE`: starts the Drive and HubSpot mock servers and runs `attach` with both gates open against them; breakpoint in `uploader.py` → `upload_phase` / `attach_phase`; the first upload gets a 429 so you can watch the retry |
| inspect the ledger | open `.mrload/ledger.sqlite` (SQLite Viewer extension): `companies_resolved`, `files_uploaded`, `file_notes_posted`, `deals_created` |
| inspect the index | open `.mrload/library_hierarchy.csv`, `.mrload/review/*.csv` (Rainbow CSV) |
| inspect what dbt ran | `dbt/target/compiled/mr_load_library/**` after any dbt task; `dbt/target/run_results.json` |
| run the tests | Testing side bar (pytest, `tests/` minus `tests/e2e`), or Task `mr-load: unit tests`; `mr-load: e2e rehearsal` for the 30 s full run |

Debug configurations set `justMyCode: false`, so you can step into
`googleapiclient` / `requests` when the failure is on the wire.

## 4. Troubleshooting recipes (the auth issue first)

**"permission error" at Google login.** Do not use
`gcloud auth application-default login --scopes=…drive…` (rejected for
gcloud's client). Run Task `mr-load: gauth`. If it stops at step 4:

- `requires a quota project` → the task already ran `set-quota-project`;
  rerun it; confirm `quota_project_id` appears in
  `/root/.config/gcloud/application_default_credentials.json` (container) or
  `~/.config/gcloud/…` (WSL).
- `insufficientPermissions` / `ACCESS_TOKEN_SCOPE_INSUFFICIENT` → the consent
  screen was not fully accepted for Drive; rerun and tick both scopes.
- `404` on the scope root → the logged-in account cannot see
  `20 opportunities and customer data`; check `gcloud auth list` shows the
  account that owns the Drive.
- `accessNotConfigured` → `gcloud services enable drive.googleapis.com --project=<MRLOAD_BQ_PROJECT>`.

**Walk returns 0 nodes.** Debug `runner ▸ walk (LIVE Drive, depth 1)` with a
breakpoint on the `resp` line in `ApiDriveLister.list_children`: an empty
`files` list with no error means the identity has no access to the folder
(sharing / wrong account), a `403` payload means quota project or API.

**Company / attach failures.** Debug `runner ▸ companies (DRY)` or
`runner ▸ attach (DRY)` against the real token (dry = no writes), breakpoint in
`companies.py` → `resolve_companies` or `uploader.py`; the `requests.HTTPError`
carries HubSpot's JSON body in `exc.response.text`.

**dbt failures.** Task `mr-load: dbt parse (BigQuery target)` for Jinja/YAML
problems without touching BigQuery; the failing test's SQL is in
`dbt/target/compiled/.../tests/`. The DuckDB rehearsal target runs the same
models locally: `MRLOAD_DBT_TARGET=duckdb` (see `dbt/README.md`).

## 5. Files added for the IDE

```
.devcontainer/devcontainer.json   build from scripts/dev/Dockerfile, gcloud volume, post-create bootstrap, extensions
.vscode/tasks.json                one task per pipeline step + gauth, bootstrap, tests, rehearsals, dbt
.vscode/launch.json               runner sub-commands (live / offline / against mocks), mock servers, compounds, pytest
.vscode/settings.json             interpreter (.venv for WSL), pytest, LF line endings
.vscode/extensions.json           recommendations
tests/fixtures/manifest_sample.json   offline tree for `runner ▸ index`
```
