# mr-load — Inferred Library Datastructure (pass 1, 30 Sales domain)

Methodical study of the Miraex Drive hierarchy (live enumeration 2026-08-13 and
2026-09-03) and the rules the walker, the entity card and the dbt tests
implement. Companion to `context/cards/library.yaml` (the singular map file),
`pipeline/library_files/` and `dbt/`.

## 1. Scope

The present pipeline focuses on the **30 Sales** domain:

```
30 Sales / 20 opportunities and customer data / <segment> / <Company> / **
```

- Walked root: `20 opportunities and customer data` (`1If3SX0GD6FJypMy23na6HizFD6gciw_0`),
  `path_prefix = "30 Sales"` so emitted legacy paths match the legacy convention.
- Segment example: `Quantum` (`1wNn20ijHfDJjUbdssQUdMYWhtEUGpwLq`) — ~50 company-named
  folders beneath it (Thorlabs, Toshiba, Alice & Bob, Bluefors, IQM, CERN, NVidia, …).
- **Folder name = company name.** Every file beneath a company folder is a library
  asset anchored to that company.
- **Out of scope (indexable later):** `70 Tradeshows` and `Events & Tradeshows` —
  pruned at walk time by `scope.exclude_segments`, re-asserted in dbt.

## 2. Node identity

Mirrors ic-load's `unflatten_hierarchy.py`:

| Field | Rule | Example |
|---|---|---|
| `node_key` | `"|".join(legacy path incl. name)` | `30 Sales|20 opportunities and customer data|Quantum|Toshiba` |
| `parent_key` | key of the parent, NULL at depth 1 | `30 Sales|20 opportunities and customer data|Quantum` |
| `depth` | 1-based within the walked root | Quantum = 1, Toshiba = 2, files ≥ 3 |
| `path_code` | ordinal or initial per parent segment | `3020Q` |
| `legacy_library_id` | `path_code-sha1(node_key)[:8]` (default), `pathcode`, or `dr:<sha1>` | `3020Q-00cbe248` |

The bare path code reproduces the legacy example (`3020Q`) but is not unique
across siblings; the hash suffix restores uniqueness.

## 3. Classification grammar

Folder at depth *d*, first match wins:

1. `^(19|20)\d{2}[\s_-]+rest` → **engagement_folder** (deal-shaped; kept for other roots)
2. `^\d+\s` → **taxonomy**
3. *d* ≤ `segment_depth` (1) and no company inherited → **segment**
4. no company inherited yet → **company_folder** — sets `company_node_key`
5. otherwise → **document** folder

Files are **document** leaves inheriting `company_node_key`, plus an
`asset_class` from the card (first match wins):

| class | rule | routing |
|---|---|---|
| `deal_candidate` | extension `pdf` **and** name ~ `(?i)(^|[^a-z0-9])(PO|Billing)([^a-z0-9]|$)` | pass 2: also indexed as a deal (never during the DFS) |
| `parked_for_review` | extension `pdf` | operator review queue before deal processing |
| `shortcut` | Drive shortcut mime | never attached |
| `asset` | default | — |

All non-shortcut classes are attached as notes on the company in pass 1.

## 4. Cardinality contract (the part that was overlooked, now explicit)

| Relation | Type | Policy | Enforced by |
|---|---|---|---|
| Segment → Company | 1:N, unique names | STOP | `assert_company_name_unique_within_segment` |
| Company → Library | 1:N | — | `company_node_key` edge |
| Library → Company | N:1 | REJECT | `silver_library_index.legacy_company_id` not_null + relationships; orphans → `silver_library_orphans` (WARN) |
| Drive node → parent | N:1 (tree) | REJECT | `parents_count = 1` + `assert_no_multi_parent_nodes` (Drive is a DAG; the API `parents` array exposes it, rclone manifests cannot) |
| node_key uniqueness | PK | STOP | `unique` test (duplicate sibling names are counted by the walker too) |
| parent_key → node_key | FK | STOP | `relationships` test |

**BigQuery reality** (verified against dbt docs): only `not_null` is enforced;
`primary_key`/`foreign_key` are declared, not enforced; `unique`/`check` are
unsupported. Enforcement = dbt data tests + singular tests, as in ic-load.

## 5. Silver column contract

First 19 columns are drop-in parity with the icalps silver table
(`--owner-prefix icalps` for byte-parity, default `mirx`):

```
legacy_library_id  legacy_company_id  legacy_contact_id  legacy_deal_id
legacy_case_id     legacy_file_path   legacy_file_name   legacy_file_link
libr_note  libr_type  libr_category  libr_status
libr_created_by  libr_updated_by  libr_created_at  libr_updated_at
mirx_owner_email  mirx_owner_fullname  loaded_at
```

`legacy_company_id` = `legacy_library_id` of the anchoring **company folder**
row (the N:1 edge). The HubSpot company id lives in the runner ledger
(`companies_resolved`) and is joined at attach time. Trailing columns carry
`node_key`, `parent_key`, `company_node_key`, `asset_class`, inference and
Drive metadata.

## 6. Pipeline (steps 0 → 7, scripted in `scripts/run_pass1.sh`, detailed in `docs/RUNBOOK_PASS1.md`)

```
walk           Drive API DFS (parents[], owners, createdTime, webViewLink) → library_hierarchy.csv
bq-init        datasets + empty ledger tables (dbt sources resolve before step 6)
bq-load        hierarchy → mrload_raw.library_hierarchy                       [MRLOAD_APPROVE_BQ_LOAD]
hs-props       HubSpot property DEFINITIONS from the card (StackSync targets; values never written here) [MRLOAD_APPROVE_PROPERTY_CREATE]
dbt run/test   silver_library_company / _index / _deal_candidates / _parked / _orphans + cardinality tests (+ docs generate → catalog)
hs-props-verify definitions vs built silver (catalog) → review/stacksync_mapping.csv (the sheet you map in the StackSync UI)
review-export  operator queues + deal_decisions.csv template (offline)
companies      company folder → HubSpot company (search by name, create)     [MRLOAD_APPROVE_COMPANY_CREATE]
attach         download on demand → POST files → note → associate to company [MRLOAD_APPROVE_FILES_UPLOAD, MRLOAD_APPROVE_FILE_NOTES_POST]
ledger-export  ledger → mrload_raw.{companies_resolved,files_uploaded,file_notes_posted,deals_created} → dbt build
deals          pass 2: approved decisions → deal → company, note → deal      [MRLOAD_APPROVE_DEAL_CREATE]
unmigrate      delete attached notes from the ledger                          [MRLOAD_APPROVE_UNMIGRATE]
```

Orchestration: `scripts/run_pass1.sh` on localhost (Cloud Shell or a laptop); rehearsed end to end by `scripts/e2e_rehearsal.sh` (see `docs/REHEARSAL_REPORT.md`);
every write gate defaults to DRY (Ansible removed 2026-09-07, logic kept in `ansible/README.md`). rclone is no longer on the critical path (no
GCS mirror is needed: binaries stream from Drive to HubSpot on demand); the
`index` sub-command keeps the rclone-manifest source as an offline fallback.

## 7. Deferred

- Pass 2 automation beyond the decisions file (deal properties are operator-supplied per row).
- Contacts: no contact level exists in the tree; contact association is not
  inferred.
- Tradeshows (70): indexable with the same grammar, excluded from this pipeline.
