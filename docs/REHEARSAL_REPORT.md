# e2e rehearsal record — 2026-09-07

Produced by `scripts/e2e_rehearsal.sh` in the development container (no Google
or HubSpot credentials available there, see `docs/RUNBOOK_PASS1.md` →
Rehearsal). Real code paths against local stand-ins; wall time ≈ 31 s.

## Clean scenario — full sequence steps 0 → 7

# mr-load e2e rehearsal — 28/28 checks passed

Real code paths: `googleapiclient` walker → Drive mock · `requests` HubSpot client → HubSpot mock · `bq` stub with schema validation · real dbt models + tests on DuckDB · SQLite ledger.

| # | check | result | detail |
|---|---|---|---|
| 1 | walk: tradeshow subtree pruned | PASS | 32 nodes |
| 2 | walk: 7 company folders (6 Quantum + 1 Photonics) | PASS | 7 |
| 3 | walk: 4 deal candidates (PO/Billing PDFs, case-insensitive, any depth) | PASS | IQM Billing Q2.PDF, PO_4711 Thorlabs.pdf, Billing-2026-03.pdf, Toshiba PO 2026-001.pdf |
| 4 | walk: shortcut classified and excluded from attach | PASS |  |
| 5 | walk: orphan at segment level has no company anchor | PASS |  |
| 6 | walk: no multi-parent / duplicate keys (clean scenario) | PASS |  |
| 7 | walk: every attachable file anchors to an existing company folder | PASS | 20 attachable |
| 8 | bq: library_hierarchy load validated positionally + by type, row count == CSV | PASS | errors=[] |
| 9 | bq: 4 ledger tables loaded (step 6) | PASS |  |
| 10 | dbt gate: 34 tests, 0 fail/error | PASS | {'pass': 33, 'warn': 1, 'fail': 0, 'error': 0} |
| 11 | dbt gate: orphan check surfaces as WARN (not STOP) | PASS | {'pass': 33, 'warn': 1, 'fail': 0, 'error': 0} |
| 12 | companies: Thorlabs matched by name (pre-existing), 6 created | PASS | {'created': 6, 'matched_by_name': 1} |
| 13 | companies: every ledger hs_company_id exists in HubSpot | PASS |  |
| 14 | attach p1: 20 files uploaded, none failed | PASS | {'uploaded': 20} |
| 15 | attach p1: 429 retried once (uploads requested == files + 1) | PASS | requests=21 files=20 |
| 16 | attach p1: native Google docs exported before upload | PASS |  |
| 17 | attach p2: 20 notes attached, none partial/failed | PASS | {'attached': 20} |
| 18 | attach p2: one note→company association per note | PASS |  |
| 19 | cardinality: every note is associated to exactly its folder's company (Library→Company N:1) | PASS | mismatches=[] |
| 20 | idempotency: re-running attach fired zero new uploads/notes/associations | PASS |  |
| 21 | silver: index rows == attachable files | PASS | 20 |
| 22 | silver: hs_note_id populated for every index row after ledger-export | PASS | 20/20 |
| 23 | silver: hs_company_id populated for all 7 companies | PASS | 7 |
| 24 | silver: orphans model holds the segment-level spreadsheet | PASS | 1 |
| 25 | pass 2: 4 deals created from approved decisions | PASS | {'created': 4} |
| 26 | pass 2: deal→company and note→deal associations per deal | PASS |  |
| 27 | pass 2: hs_deal_id visible in silver_library_deal_candidates | PASS | 4 |
| 28 | dbt final build after write-back: 34 tests, 0 fail/error | PASS | {'pass': 33, 'warn': 1, 'fail': 0, 'error': 0} |

Ledger at the end: companies_resolved created=6 / matched_by_name=1 ·
files_uploaded uploaded=20 · file_notes_posted attached=20 · deals_created created=4.

## Dirty scenario — the cardinality gate must stop the run

Tree variant with one file linked into two company folders and a second
`Toshiba` folder under `Quantum`. Walker stats: `multi_parent_nodes: 2`,
`duplicate_node_keys: 1`. `dbt test` result: PASS=23 WARN=1 **ERROR=10**, the
script exited at the `dbt` step, `companies-dry` never ran, the HubSpot mock
recorded 0 writes and `companies_resolved` holds 0 rows. Failing tests:

- `5 of 34 FAIL 1 assert_company_name_unique_within_segment ....................... [FAIL 1 in 0.02s]`
- `8 of 34 FAIL 2 assert_no_multi_parent_nodes .................................... [FAIL 2 in 0.03s]`
- `10 of 34 FAIL 2 dbt_utils_expression_is_true_stg_library_hierarchy_parents_count___1  [FAIL 2 in 0.05s]`
- `28 of 34 FAIL 1 unique_silver_library_company_company_node_key ................. [FAIL 1 in 0.03s]`
- `29 of 34 FAIL 1 unique_silver_library_company_legacy_company_id ................ [FAIL 1 in 0.02s]`
- `30 of 34 FAIL 1 unique_silver_library_deal_candidates_legacy_library_id ........ [FAIL 1 in 0.02s]`
- `31 of 34 FAIL 7 unique_silver_library_index_legacy_library_id .................. [FAIL 7 in 0.02s]`
- `32 of 34 FAIL 7 unique_silver_library_index_node_key ........................... [FAIL 7 in 0.03s]`
- `33 of 34 FAIL 3 unique_silver_library_parked_legacy_library_id ................. [FAIL 3 in 0.02s]`
- `34 of 34 FAIL 1 unique_stg_library_hierarchy_node_key .......................... [FAIL 1 in 0.05s]`

## What this does and does not prove

Proves: the DFS grammar and classification on a realistic tree, the bronze
contract accepted by a schema-validating `bq load`, the dbt models and all 34
tests on a real SQL engine, company resolution (match vs create), the
two-phase upload → note → association flow with retry and idempotent re-runs,
the Library → Company N:1 cardinality end to end, step-6 write-back into
silver, pass 2 from an approved decisions file, and the negative path.

Does not prove: Google / HubSpot authentication and quotas, the Drive
sharing step, BigQuery-only SQL behaviour outside the shimmed functions, and
Files API limits on real binaries. `scripts/run_pass1.sh preflight` covers the
first three live before anything is written.
