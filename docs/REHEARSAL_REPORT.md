# e2e rehearsal record — 2026-09-13 (41 checks on branch walker-deal-depth3; 34 on the main working branch; first recorded 2026-09-07 with 28)

Produced by `scripts/e2e_rehearsal.sh` in the development container (no Google
or HubSpot credentials available there, see `docs/RUNBOOK_PASS1.md` →
Rehearsal). Real code paths against local stand-ins; wall time ≈ 31 s.

## Clean scenario — full sequence steps 0 → 7 (+ hs-props before dbt, hs-props-verify after)

# mr-load e2e rehearsal — 41/41 checks passed

Real code paths: `googleapiclient` walker → Drive mock · `requests` HubSpot client → HubSpot mock · `bq` stub with schema validation · real dbt models + tests on DuckDB · SQLite ledger.

| # | check | result | detail |
|---|---|---|---|
| 1 | walk: tradeshow subtree pruned | PASS | 42 nodes |
| 2 | walk: 7 company folders (6 Quantum + 1 Photonics) | PASS | 7 |
| 3 | walk: 5 deal candidates (PO/Billing PDFs, case-insensitive, any depth) | PASS | IQM Billing Q2.PDF, PO_4711 Thorlabs.pdf, Billing-2026-03.pdf, PO 2026-042.pdf, Toshiba PO 2026-001.pdf |
| 4 | walk: deal_node_key = self on the level-3 folder, inherited by the level-4 file, absent directly under the company | PASS |  |
| 5 | walk: shortcut classified and excluded from attach | PASS |  |
| 6 | walk: orphan at segment level has no company anchor | PASS |  |
| 7 | walk: no multi-parent / duplicate keys (clean scenario) | PASS |  |
| 8 | walk: every attachable file anchors to an existing company folder | PASS | 26 attachable |
| 9 | bq: library_hierarchy load validated positionally + by type, row count == CSV | PASS | errors=[] |
| 10 | bq: 4 ledger tables loaded (step 6) | PASS |  |
| 11 | dbt gate: 49 tests, 0 fail/error | PASS | {'pass': 48, 'warn': 1, 'fail': 0, 'error': 0} |
| 12 | dbt gate: orphan check surfaces as WARN (not STOP) | PASS | {'pass': 48, 'warn': 1, 'fail': 0, 'error': 0} |
| 13 | companies: Thorlabs matched by name (pre-existing), 6 created | PASS | {'created': 6, 'matched_by_name': 1} |
| 14 | companies: every ledger hs_company_id exists in HubSpot | PASS |  |
| 15 | attach p1: 26 files uploaded, none failed | PASS | {'uploaded': 26} |
| 16 | attach p1: 429 retried once (uploads requested == files + 1) | PASS | requests=27 files=26 |
| 17 | attach p1: native Google docs exported before upload | PASS |  |
| 18 | attach p2: 26 notes attached, none partial/failed | PASS | {'attached': 26} |
| 19 | attach p2: one note→company association per note | PASS |  |
| 20 | cardinality: every note is associated to exactly its folder's company (Library→Company N:1) | PASS | mismatches=[] |
| 21 | idempotency: re-running attach fired zero new uploads/notes/associations | PASS |  |
| 22 | properties: all 36 declared definitions exist in HubSpot after hs-props, in group mrload_library | PASS | 36/36 |
| 23 | properties: pre-existing companies.mrload_drive_link reused, never modified | PASS |  |
| 24 | properties: second hs-props run created nothing (idempotent) | PASS | 35 |
| 25 | properties verify: mapping sheet has 36 property rows + 3 match keys, every silver column found in the DuckDB catalog | PASS | {'ok'} |
| 26 | silver: index rows == attachable files | PASS | 26 |
| 27 | deal: silver_library_deal = 2 folder anchors + 3 file anchors | PASS | 2026 Quantum sensor RFQ[folder], IQM Billing Q2.PDF[file], PO_4711 Thorlabs.pdf[file], Submissions[folder], Toshiba PO 2026-001.pdf[file] |
| 28 | deal: the RFQ folder qualifies with pdf=2, PO/Billing=1, files=4; Submissions qualifies via its Billing PDF | PASS |  |
| 29 | deal: exhibition folder (excluded realm) and PDF-less 'Site survey' are NOT deals | PASS |  |
| 30 | deal: legacy_deal_id set on the 6 files beneath the two folder anchors + the 3 self-anchored PDFs, NULL elsewhere | PASS | 9 |
| 31 | deal: Python qualifier (review/deal_anchors.csv) agrees with the dbt model, name by name | PASS | 5 |
| 32 | deal: 4 deferred documents beneath anchors (quote, SOW, gds x2) listed in review/deal_documents.csv, none of them PO/Billing | PASS | 4 |
| 33 | silver: hs_note_id populated for every index row after ledger-export | PASS | 26/26 |
| 34 | silver: hs_company_id populated for all 7 companies | PASS | 7 |
| 35 | silver: orphans model holds the segment-level spreadsheet | PASS | 1 |
| 36 | pass 2: 5 deals created from approved decisions (one per inferred anchor, not per PDF) | PASS | {'created': 5} |
| 37 | pass 2: 5 deal→company associations; 5 note→deal (only the PO/Billing notes; the quote and SOW deferred) | PASS | 5/5 |
| 38 | pass 2: hs_deal_id visible on all 5 deal-candidate files through their anchor | PASS | 5 |
| 39 | checkpoints: every step run recorded rc=0, in the executed order | PASS | preflight walk bq-init bq-load hs-props hs-props dbt hs-props-verify review companies-dry companies-live attach-dry attach-upload attach-notes attach-notes ledger-export deals-dry deals-live ledger-export |
| 40 | pipeline_state: all 16 steps done, next = none (pass 1 + pass 2 complete, deal ids written back) | PASS | next=None pass 1 complete |
| 41 | dbt final build after write-back: 49 tests, 0 fail/error | PASS | {'pass': 48, 'warn': 1, 'fail': 0, 'error': 0} |

Ledger at the end: companies_resolved created=6 / matched_by_name=1 ·
files_uploaded uploaded=26 · file_notes_posted attached=26 · deals_created created=5 (one per inferred anchor).

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
