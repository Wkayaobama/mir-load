# mr-load — Operator Runbook, steps 0 → 7 (fully scripted)

Companion to `scripts/run_pass1.sh`. Every step below is one sub-command of
that script; every HubSpot / BigQuery write sits behind a gate that the script
opens **inline, for that step only**, after a dry run and a `YES` prompt.
Nothing here is exported globally. `--yes` skips the prompts (CI / re-runs).

```
scripts/run_pass1.sh preflight → walk → bq-init → bq-load → dbt → review
                     → companies-dry → companies-live
                     → attach-dry → attach-upload → attach-notes
                     → ledger-export → (edit deal_decisions.csv) → deals-dry → deals-live
scripts/run_pass1.sh all      # pass 1 end-to-end with a checkpoint before each live gate
scripts/run_pass1.sh status   # ledger + artefact summary at any time
```

Where to run it: any Ubuntu bash — WSL2, Docker, Cloud Shell or a GCE VM over
IAP — see `docs/WEZTERM_UBUNTU.md` for getting that prompt in WezTerm and
`scripts/bootstrap_ubuntu.sh` for the one-shot machine setup. There is no
second orchestrator: the script is the execution surface (see `ansible/README.md`). The container that produced this repo cannot open port 22 and holds
no credentials, so nothing below was executed live there; every command was
smoke-tested offline against fakes and the dbt project parses clean.

---

## A. Your one-time operations (before `preflight`)

### A1. Google service account + Drive sharing (the silent-failure step)

```bash
gcloud config set project <PROJECT_ID>
gcloud services enable drive.googleapis.com bigquery.googleapis.com
gcloud iam service-accounts create mr-load-runner --display-name="mr-load runner"
gcloud iam service-accounts keys create mr-load-sa.json \
  --iam-account=mr-load-runner@<PROJECT_ID>.iam.gserviceaccount.com
gcloud projects add-iam-policy-binding <PROJECT_ID> \
  --member="serviceAccount:mr-load-runner@<PROJECT_ID>.iam.gserviceaccount.com" --role=roles/bigquery.dataEditor
gcloud projects add-iam-policy-binding <PROJECT_ID> \
  --member="serviceAccount:mr-load-runner@<PROJECT_ID>.iam.gserviceaccount.com" --role=roles/bigquery.jobUser
```

Then **in the Drive UI**: right-click `20 opportunities and customer data`
(id `1If3SX0GD6FJypMy23na6HizFD6gciw_0`) → Share → add
`mr-load-runner@<PROJECT_ID>.iam.gserviceaccount.com` as **Viewer**. It lives
in a shared drive, so the SA must be a member of that shared drive or the
folder must be shared explicitly. Without this, `walk` returns **0 nodes and no
error** — `preflight` detects exactly that.

Alternative for a laptop session (no key file, recommended when the Drive is
your own account): leave `GOOGLE_APPLICATION_CREDENTIALS` unset and run
`scripts/gauth.sh`. It performs `gcloud auth login --enable-gdrive-access
--update-adc`, sets the ADC quota project to `MRLOAD_BQ_PROJECT`, enables the
Drive API there, and verifies the scope root is readable. Do not use
`gcloud auth application-default login --scopes=…drive…`: Google rejects
Drive scopes for gcloud's built-in client on that path (documented: it needs
your own OAuth client via `--client-id-file`).

### A2. HubSpot private app — sandbox first

Settings → Integrations → Private Apps → Create. Scopes:
`files`, `crm.objects.companies.read/write`, `crm.objects.notes.write` (engagements),
`crm.objects.deals.read/write` (pass 2 only). Copy the token into
`HUBSPOT_SANDBOX_TOKEN`. The ic-load cutover pattern applies: prove the run on
the sandbox portal, then swap the token for prod and re-run from `companies-dry`.

Note: the Claude HubSpot connector attached to this workspace points at portal
**9201667 (WISeKey SA)** — the ICALPS production portal. `preflight` prints the
portal id the token resolves to so you can confirm it is the one you intend.

### A3. Configuration file

```bash
cp .env.mrload.example ../.env.mrload      # codebase root (shared by worktrees) or ./.env
$EDITOR ../.env.mrload                     # GOOGLE_APPLICATION_CREDENTIALS, MRLOAD_BQ_PROJECT, HUBSPOT_SANDBOX_TOKEN
pip install -r requirements.txt
```

---

## 0. `preflight` — nothing is written

Checks, in order: python deps import; `bq`, `curl`, `dbt` on PATH; env vars;
the SA key exists and prints its email; **a depth-1 walk of the scope root**
(proves sharing + Drive API; dies on 0 nodes); a read-only
`GET /account-info/v3/details` with the HubSpot token (prints the portal id);
`bq ls` on the project.

Expected: five ✔ lines. Any ✖ names the exact fix.

## 0. `walk` — Drive API depth-first walk

```
python -m pipeline.library_files.runner walk --hierarchy-out .mrload/library_hierarchy.csv --silver-preview-out .mrload/silver_preview.csv
```

What happens: one `files.list` per folder (`'<id>' in parents and trashed=false`,
all-drives flags, 1000/page), pre-order, name-sorted. Subtrees whose name
matches the card's `exclude_segments` (`^70\b`, `tradeshow`, `Events & Tradeshows`)
are never entered. Each node becomes one row: `node_key` / `parent_key` / `depth`
(unflatten shape), `libr_category` (segment / company_folder / document),
`company_node_key` (the N:1 anchor), `asset_class`, `parents_count`, md5, size,
owners, created/modified, `webViewLink`.

Read the stats JSON:

| stat | meaning | you want |
|---|---|---|
| `company_folders` | company objects to be created/matched | ≈ your customer folder count |
| `deal_candidates` | PO / Billing PDFs → pass 2 | as expected |
| `parked_for_review` | other PDFs → your review | as expected |
| `multi_parent_nodes` | a file linked into ≥2 folders (DAG violation) | **0** |
| `duplicate_node_keys` | two siblings with the same name | **0** |
| `pruned` | tradeshow subtrees skipped (manifest source only) | ≥ 0 |

Rate: Drive allows ~10 requests/s per client; a few hundred folders take minutes.
Re-runs are free (no writes). The silver preview is the 19-column parity
table rendered locally so you can eyeball the index without BigQuery.

## B. `bq-init` — datasets + empty ledger tables

`bq mk --dataset` for `mrload_raw` and `mrload`; `bq mk --table` for
`companies_resolved`, `files_uploaded`, `file_notes_posted`, `deals_created`
from `pipeline/library_files/sql/ledger/*.schema.json`. Idempotent. The empty
ledger tables exist so the dbt sources resolve before step 6 has run
(the HubSpot id columns are simply NULL until then).

## 1a. `bq-load` — bronze  [gate MRLOAD_APPROVE_BQ_LOAD]

Prints the exact `bq load --replace --source_format=CSV --skip_leading_rows=1
--allow_quoted_newlines mrload_raw.library_hierarchy <csv> <schema>` first
(dry), then asks. `--replace` makes every load a full snapshot, so re-walk +
re-load is the way to refresh. This is what **creates** the library table.

## 2a + 3a. `dbt` — silver + the cardinality gate

`dbt deps` (dbt_utils) → `dbt run` → `dbt test`, target `dev` (OAuth) or `runner`
(SA keyfile, `MRLOAD_DBT_TARGET=runner`). Models built in `mrload`:

| model | one row per | purpose |
|---|---|---|
| `stg_library_hierarchy` | walked node | typed view over bronze |
| `silver_library_company` | company folder | the company object; `hs_company_id` after step 6 |
| `silver_library_index` | file with a company anchor | 19-col icalps parity + HubSpot ids after step 6 |
| `silver_library_deal_candidates` | PO / Billing PDF | pass-2 queue |
| `silver_library_parked` | other PDF | your review queue |
| `silver_library_orphans` | file without company anchor | reported, never attached |

Tests that STOP the run and what each means in Drive terms:

| test | it fails when | your operation |
|---|---|---|
| `unique_stg_library_hierarchy_node_key` | two siblings share a name | rename one folder/file |
| `assert_no_multi_parent_nodes` | a file is in two folders | remove one parent (Drive: "Remove from folder") |
| `assert_company_name_unique_within_segment` | "Toshiba" twice under Quantum | merge folders |
| `assert_no_excluded_scope_nodes` | a tradeshow path leaked | check the card's `exclude_segments` |
| `assert_deal_candidates_are_pdf` | classification bug | report it |
| `relationships parent_key → node_key` | broken tree | re-walk |

`assert_asset_has_company_anchor` is **warn** only: loose files directly under a
segment (e.g. a lead-tracking spreadsheet under Quantum) are listed in
`silver_library_orphans` and skipped by attach.

**Do not open the company-create gate while a STOP test is red.** A company
created from a duplicate folder is the expensive thing to undo.

## `review` — your queues (offline, no network)

Writes `.mrload/review/`: `companies.csv`, `deal_candidates.csv`,
`parked_for_review.csv`, `orphans.csv`, `multi_parent.csv`, and
`deal_decisions.csv` (one line per deal candidate, `approve=N`, `dealname`
pre-filled as `<Company> - <file stem>`, empty `pipeline` / `dealstage` /
`amount`). Review the parked PDFs at your pace; the pipeline never touches them
as deals until you promote a row into `deal_decisions.csv`.

## 1b. `companies-dry` — exact-name search, no creation

For every company folder: ledger hit → `resolved_from_ledger`; else
`POST /crm/v3/objects/companies/search` with `name EQ <folder name>` →
`matched_by_name` (1 hit), `ambiguous_match` (2+), `would_create` (0).

Your operation: ambiguous names must be resolved in HubSpot (merge or rename)
before going live — the script refuses to open the gate while any remain.
Matched companies are reused as-is (no property is written to them).

## 2b. `companies-live`  [gate MRLOAD_APPROVE_COMPANY_CREATE]

Re-runs the dry step, shows the `would_create` count, asks, then creates each
missing company with `name = folder name` and
`description = "Drive folder: <link>"`. Outcome per folder lands in
`ledger.companies_resolved` (`company_node_key → hs_company_id`). Re-runs skip
resolved rows. Verify: open two or three of the new companies in HubSpot; the
description carries the Drive link back to the source folder.

## 3b. `attach-dry`

Lists the rows that *would* be uploaded: files (not folders), `asset_class` in
`asset, deal_candidate, parked_for_review` (shortcuts never), whose
`company_node_key` resolved in the ledger. No download, no REST call.

## 4b. `attach-upload` — phase 1  [gate MRLOAD_APPROVE_FILES_UPLOAD]

Per row: download from Drive on demand into `.mrload/cache/` (`get_media`;
native Google Docs/Sheets/Slides are exported to docx/xlsx/pptx), then
`POST /files/v3/files` (folder `/mrload_library`, access `PRIVATE`). The
`hs_file_id` is written to `ledger.files_uploaded` **before** phase 2 starts,
so a crash never orphans an uploaded file. Retries: 429/5xx with backoff and
`Retry-After`. Re-run to resume — uploaded rows are skipped.

Sizing: one HTTP upload per file; a company with 40 files takes roughly a
minute. Cache can be deleted after phase 2.

## 5b. `attach-notes` — phase 2  [gate MRLOAD_APPROVE_FILE_NOTES_POST]

Per uploaded row: `POST /crm/v3/objects/notes` with `hs_attachment_ids =
<hs_file_id>`, `hs_note_body = "<legacy path>/<file>\n<drive link>"`,
`hs_timestamp = now`; then `PUT /crm/v4/objects/note/{id}/associations/default/company/{hs_company_id}`.
Statuses in `ledger.file_notes_posted`: `attached`, `partial` (note exists,
association failed — re-run converges), `failed`.

Verify in HubSpot: company record → Activities → Notes → the file is the note's
attachment, body shows the legacy path. Rollback at any time:
`scripts/run_pass1.sh unmigrate` deletes exactly the notes in the ledger.

## 6. `ledger-export` — HubSpot ids back into BigQuery  [gate MRLOAD_APPROVE_BQ_LOAD]

Dumps the four ledger tables to `.mrload/ledger_export/*.csv`, `bq load
--replace` into `mrload_raw.*`, then `dbt build`. After this,
`silver_library_company.hs_company_id`, `silver_library_index.hs_file_id /
hs_note_id / attach_status` and `silver_library_deal_candidates.hs_deal_id` are
populated — the join keys the associativity layer needs. Re-run after every
live HubSpot step to keep BigQuery current.

## 7. `deals-dry` / `deals-live` — pass 2  [gate MRLOAD_APPROVE_DEAL_CREATE]

Your operation first: edit `.mrload/review/deal_decisions.csv` — set
`approve=Y`, adjust `dealname`, fill `pipeline` and `dealstage` (portal ids;
or set `MRLOAD_DEAL_PIPELINE` / `MRLOAD_DEAL_STAGE` as defaults) and `amount`.
Rows left at `N` are never touched. Then:

- `deals-dry`: `would_create` per approved row; `no_company_resolved` means
  `companies-live` has not run for that company.
- `deals-live`: `POST /crm/v3/objects/deals` (dealname, dealstage, pipeline,
  amount) → default association **deal → company** → default association
  **note → deal** (the pass-1 note of that PDF, so the document hangs off both
  the company and the deal). Outcome in `ledger.deals_created`; then re-run
  `ledger-export`.

This is deliberately a second pass over the queue, never part of the DFS, and
parked PDFs only enter it by your edit of the decisions file.

---

## Rehearsal — proving the sequence without credentials

`scripts/e2e_rehearsal.sh` runs the whole scripted sequence (steps 0 → 7,
`--yes`, no orchestrator) with the **real** code against local stand-ins for
the three external systems, and cross-checks every artefact:

| external system | stand-in | what is real |
|---|---|---|
| Drive v3 API | `tests/e2e/drive_mock.py` (tree modelled on the live Drive: segments, companies, PO/Billing/other PDFs, native Google docs, shortcut, orphan, `70 Tradeshows`) | `google-api-python-client` walker, pagination, exports, downloads |
| HubSpot API | `tests/e2e/hubspot_mock.py` (search/create company, files, notes, v4 associations, deals; first upload answers 429) | `requests` client, retry/backoff, two-phase uploader, SQLite ledger |
| BigQuery | `scripts/e2e/bin/bq` stub (positional + type validation of every load against the schema JSON) and **dbt on DuckDB** | the dbt models and all 34 tests (dialect shims in `dbt/macros/dialect.sql`) |

```
scripts/e2e_rehearsal.sh                 # clean tree  → .mrload/rehearsal/REPORT.md, 28 checks
SCENARIO=dirty scripts/e2e_rehearsal.sh  # multi-parent file + duplicate company → STOP at the dbt gate
MRLOAD_E2E=1 python -m pytest tests/e2e  # both, as a test
```

The report cross-checks walker CSV ↔ ledger ↔ HubSpot mock state ↔ bq stub ↔
dbt `run_results.json` ↔ DuckDB silver tables, including the cardinality
proof (every note lands on exactly its folder's company) and idempotency (a
second attach run fires zero requests). What it cannot prove: Google/HubSpot
authentication and quotas, BigQuery-only SQL behaviour outside the shimmed
functions, and the Drive sharing step — those are what `preflight` checks live.

## Re-run / refresh semantics

| you changed | re-run from |
|---|---|
| Drive content (new files, renamed folders) | `walk` → `bq-load` → `dbt` → `attach-upload` (new rows only) → `ledger-export` |
| the card (scope, classification) | `walk` onward |
| HubSpot token (sandbox → prod) | `companies-dry` onward with a **fresh ledger** (`MRLOAD_LEDGER_PATH=.mrload/ledger.prod.sqlite`) |
| a failed batch | the same step — every step skips what the ledger already holds |

## Files the pipeline owns

```
.mrload/library_hierarchy.csv   bronze (walk output)          .mrload/ledger.sqlite   idempotency + HubSpot ids
.mrload/silver_preview.csv      offline 19-col parity          .mrload/cache/          downloaded binaries (disposable)
.mrload/review/*.csv            your queues + deal decisions   .mrload/logs/           one log per step run
.mrload/ledger_export/*.csv     step-6 CSVs loaded into BigQuery
```
