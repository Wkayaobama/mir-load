# Worked example — mr-load pass 2 (September 2026)

Use this to calibrate depth, tone and numbers. Everything here exists in the repository on branch
`claude/mr-load-library-system-dphpbj`.

## The situation

Pass 1 (Drive walk → BigQuery/dbt silver → HubSpot companies + files as notes) had succeeded in the
sandbox portal from Cloud Shell. Pass 2 (one deal per inferred anchor, associated to its company)
first returned `no_company_resolved: 4` and `failed: 6`. The four were anchors under year-prefixed
company folders (`2021_ELTA`) that pass 1 never resolves; the six were `create_error` (pipeline /
stage ids of another portal). Then the HubSpot-Import-ready CSVs written for the operator turned
out to be unreachable from Cloud Shell.

## What was produced, in order

| # | Artefact | Content | Size / count |
|---|---|---|---|
| code, first | `6f38600` salvage (search by name, never create; strict pass 1) + import CSVs; `765cd01` `ledger-export` loads them as `mrload_raw.hubspot_deals_import` / `hubspot_companies_import` (snake_case, HubSpot internal names on the head columns, `op_*` bookkeeping) | rehearsal 48 → 50 checks, unit tests 47 → 57 |
| 1 | `docs/PASS2_SEQUENCE.md` | preconditions; the sequence with look/edit inserts (`column \| less`, `cloudshell edit`, `nano`, bulk `sed`, download/upload); branch point (API loader = `deals-live`; wizard from the BigQuery console); stage-id `curl`; stage → produces → look table; the probe verbatim; pitfalls | ~170 lines |
| 2 | `docs/EXECUTION_SURFACES.md` | three motions; parity vocabulary; timeline 2026-09-14 → 2026-09-30 with commits (`134f462`, `d4d5dfb`, `953f6f4`, `de03946`, `e9c3a86`, `79b8319`, `797636a`, `6f38600`, `765cd01`); pass-2 operations per surface; the probe and what it proves; what stays open | ~120 lines |
| 3 | `docs/EXECUTION_SURFACES.xlsx` | Layers L14 (decisions file per surface, Conditional), L15 (materialisation + probe, Parallel); Pipeline steps 15b/15c; Remediation R21–R25; four Equality checks; verdict + legend | Layers 16 rows (8/4/4), Pipeline 21 rows (14/7/0) |
| 4 | `notebooks/mr-load-run.dib`, `mr-load-compass.dib` | row 15b, mermaid branch edge, ordered pass-2 text, one `ledger-export` cell and one `bq query` cell between `deals-dry` and `deals-live`; compass count 48 → 50 | 2 cells added |
| pointers | runbook top sequence + §7 + "Three surfaces"; `INDEX_STRUCTURE.md` order line; `WEZTERM_UBUNTU.md` §C Cloud Shell verbs; `run_pass1.sh` header comment | docs only |

Commit `6abaf18`, pushed to the working branch after the code merge (`765cd01` fast-forwarded on
2026-09-29).

## The probe that closed it (2026-09-30, sandbox 49610528, Cloud Shell)

```
bq query --use_legacy_sql=false 'SELECT dealname, company_name, company_hs_object_id, op_company_status, op_approve, hs_object_id
                                 FROM `wisekeybq.mrload_raw.hubspot_deals_import` ORDER BY company_name, dealname'
```

Ten rows, one per inferred anchor; four ELTA rows `salvaged` with `company_hs_object_id =
35988056623` (found by name, never created); six `resolved`; `op_approve = N` and `hs_object_id =
NULL` everywhere — nothing created yet, the correct state before an approval. It also surfaced a
data-quality flag: `Drafts/pre-PO`, a working folder classified as a company folder and created in
the sandbox (R25).

## The question it answered

"Can the salvaged deals be loaded programmatically from the table, by a Cloud Function or a native
script, keeping one surface?" — They already are: `deals-live` creates and associates a salvaged
row like any other, because the salvage put the company id in the ledger. The table is the
mirror and the wizard's input, not a second loader. A Cloud Function would fork the logic, need
its own secret, miss the ledger and invert the data flow. Written into the sequence page's branch
point and the record's timeline instead of built.

## Numbers worth pinning in a new project

- The rehearsal check count (a test asserts it; the setup notebook quotes it — the stale copy is
  always the notebook).
- Rows per parity word per sheet (the Overview formulas show them; `--verify` prints them).
- The probe's row count and its salvaged / resolved split.
