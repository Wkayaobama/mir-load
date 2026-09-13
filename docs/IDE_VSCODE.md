# mr-load in an IDE — VS Code (also Cursor), local copy, breakpoints, one-click steps

Same Ubuntu environment as `docs/WEZTERM_UBUNTU.md`, driven from the IDE
instead of a terminal: the repo opens **inside** the Ubuntu container, every
pipeline step is a Task, every runner sub-command is a Debug configuration you
can breakpoint, and the artefacts (`.mrload/*.csv`, the SQLite ledger, dbt's
compiled SQL) open in the editor.

## 0. The compass notebook — `notebooks/mr-load-compass.dib`

One Polyglot Notebook that walks **both** routes (A · WSL, B · Dev Container/Docker) cell by
cell, with the explanation next to every step, and doubles as the `.env` troubleshooting
console: masked inspection, masked entry, CRLF/placeholder detection, and a git-safety proof
that force-stages `.env` and expects the pre-commit guard to refuse the commit. It runs on the
Windows host (PowerShell kernel, bundled by the extension) and reaches Ubuntu through
`wsl.exe -d <distro> -e bash -lc …` or `docker run --rm -v <clone>:/work … bash -lc …`;
opened *inside* the Dev Container it runs with `$Route = 'linux'` (the container now installs
the .NET SDK feature for that).

Prerequisites on the host: VS Code, the extension `ms-dotnettools.dotnet-interactive-vscode`
(*Polyglot Notebooks*, in `.vscode/extensions.json`), and the .NET SDK the extension names
on first open — `winget install Microsoft.DotNet.SDK.9` (or `.SDK.8`; the extension's
`minimumDotNetSdkVersion` setting tells you which). Some builds show a deprecation-style
banner; the notebook still runs. Open the `.dib`, run the **Switches** cell (pick `$Route`,
distro, clone path), then top to bottom. `.dib` files store no outputs, and every script the
notebook calls prints masked values only, so the file is safe to keep in git.

| section | cells | needs a terminal? |
|---|---|---|
| 1 host prerequisites | `wsl -l -v`, docker, dotnet, git | no |
| A WSL | install distro, clone on the Linux side, `crlf_check.sh`, verify, `code --remote wsl+…` | bootstrap only (sudo password) |
| B Dev Container | clone, `docker build`, `crlf_check.sh` + guarded LF fix, verify, *Reopen in Container* | no |
| 4 `.env` clinic | `env_clinic.sh --fix`, `Set-EnvKey` (masked → `env_set.sh`), `env_clinic.sh`, `install_git_hooks.sh`, `guard_proof.sh` | no |
| 5 Google auth | `gauth.sh`, then `auth_check.sh` | `gauth.sh` only (OAuth code) |
| 6–7 pipeline | every `run_pass1.sh` step incl. `hs-props` / `hs-props-verify` (HubSpot property definitions → StackSync mapping sheet); LIVE steps blocked until `$ConfirmLive = $true` | no |
| 8 rehearsals | unit tests, e2e clean, e2e dirty (must stop at the dbt gate) | no |
| 9 troubleshooting | `last_log.sh`, `dbt_failures.sh`, `status`, `unmigrate` | no |

**Second notebook — `notebooks/mr-load-run.dib`, the run sheet.** Same helpers (shared in
`notebooks/mr-load-helpers.ps1`), no setup material: the 16 steps in order, one cell each,
a *where am I* cell (`Get-PipelineState`, from `.mrload/checkpoints.tsv`), `Invoke-NextStep`
for the next pending step, and `Invoke-Sequence -UntilLive`, which runs every pending DRY
step in order and stops in front of the first LIVE gate. Use the compass once per machine,
the run sheet every day.

The same helpers are Tasks (`mr-load: env clinic`, `… auth check`, `… crlf check`,
`… git guard proof`, `… dbt failures`, `… last log`) for people who prefer the palette.

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
notebooks/mr-load-compass.dib     Polyglot Notebook: both routes + .env clinic + every step (section 0)
scripts/dev/env_clinic.sh         masked .env inspection, placeholders, CRLF, mode, git safety (--fix repairs)
scripts/dev/env_set.sh            set one .env key from stdin / a silent prompt, LF + mode 600, prints length only
scripts/dev/install_git_hooks.sh  pre-commit guard: refuses env files and token-shaped content
scripts/dev/guard_proof.sh        proves the guard (force-stage .env → commit refused → unstaged)
scripts/dev/auth_check.sh         gcloud account/project, ADC quota project, Drive root GET, bq, HubSpot portal
scripts/dev/crlf_check.sh         tracked files must be LF; prints the renormalize fix
scripts/dev/dbt_failures.sh       non-passing dbt nodes + compiled SQL paths from run_results.json
scripts/dev/last_log.sh           newest .mrload/logs/* and its tail
```
