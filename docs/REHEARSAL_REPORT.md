# e2e rehearsal record — 2026-09-28 (50 checks on branch pass2-orphan-salvage; 48 before the BigQuery import tables; 41 after the deal layer; first recorded 2026-09-07 with 28)

Produced by `scripts/e2e_rehearsal.sh` in the development container (no Google
or HubSpot credentials available there, see `docs/RUNBOOK_PASS1.md` →
Rehearsal). Real code paths against local stand-ins; wall time ≈ 31 s.

## Clean scenario — full sequence steps 0 → 7 (+ hs-props before dbt, hs-props-verify after)

# mr-load e2e rehearsal — 50/50 checks passed

Real code paths: `googleapiclient` walker → Drive mock · `requests` HubSpot client → HubSpot mock · `bq` stub with schema validation · real dbt models + tests on DuckDB · SQLite ledger.

| # | check | result | detail |
|---|---|---|---|
| 1 | walk: tradeshow subtree pruned | PASS | 50 nodes |
| 2 | walk: 7 company folders (6 Quantum + 1 Photonics) | PASS | 7 |
| 3 | walk: 7 deal candidates (PO/Billing PDFs, case-insensitive, any depth) | PASS | PO ELTA-7.pdf, PO AS-1.pdf, IQM Billing Q2.PDF, PO_4711 Thorlabs.pdf, Billing-2026-03.pdf, PO 2026-042.pdf, Toshiba PO 2026-001.pdf |
| 4 | walk: deal_node_key = self on the level-3 folder, inherited by the level-4 file, absent directly under the company | PASS |  |
| 5 | walk: shortcut classified and excluded from attach | PASS |  |
| 6 | walk: orphan at segment level has no company anchor | PASS |  |
| 7 | walk: no multi-parent / duplicate keys (clean scenario) | PASS |  |
| 8 | walk: 4 files under year-prefixed company-level folders (2021_ELTA, 2022_Aselsan) are self-anchored to a non-company row: not attached, not indexed, salvaged in pass 2 | PASS | 4 |
| 9 | bq: library_hierarchy load validated positionally + by type, row count == CSV | PASS | errors=[] |
| 10 | bq: 4 ledger tables loaded (step 6) | PASS |  |
| 11 | dbt gate: 49 tests, 0 fail/error | PASS | {'pass': 47, 'warn': 2, 'fail': 0, 'error': 0} |
| 12 | dbt gate: orphan check surfaces as WARN (not STOP) | PASS | {'pass': 47, 'warn': 2, 'fail': 0, 'error': 0} |
| 13 | companies: Thorlabs matched by name (pre-existing), 6 created | PASS | {'created': 6, 'matched_by_name': 1, 'matched_by_name_pass2': 1, 'not_in_portal_pass2': 1} |
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
| 31 | deal: Python qualifier agrees with the dbt model for company-folder anchors; the two orphan anchors (Tender, RFP) exist only on the Python side | PASS | 7 |
| 32 | deal: 6 deferred documents beneath anchors (quotes, SOW, gds x2, offer) listed in review/deal_documents.csv, none of them PO/Billing | PASS | 6 |
| 33 | silver: hs_note_id populated for every index row after ledger-export | PASS | 26/26 |
| 34 | silver: hs_company_id populated for all 7 companies | PASS | 7 |
| 35 | silver: orphans model holds the segment-level spreadsheet | PASS | 1 |
| 36 | pass 2: 6 deals created from 7 approved anchors (one per anchor, not per PDF; Aselsan's has no company) | PASS | {'created': 6} |
| 37 | pass 2: 6 deal→company associations; 5 note→deal (PO/Billing notes only; ELTA's PO was never attached — strict pass 1) | PASS | 6/5 |
| 38 | pass 2: hs_deal_id visible on the 5 indexed deal-candidate files through their anchor | PASS | 5 |
| 39 | salvage: ELTA (year-prefixed company folder) found by name in the portal → ledger matched_by_name_pass2 → its deal associated to company 9002, with 0 notes | PASS | ('30 Sales|20 opportunities and customer data|Quantum|2021_ELTA', 'ELTA', '9002', 'matched_by_name_pass2') |
| 40 | salvage: Aselsan not in the portal → ledger not_in_portal_pass2 (null id), no deal created, nothing created in HubSpot for it | PASS | ('30 Sales|20 opportunities and customer data|Quantum|2022_Aselsan', 'Aselsan', None, 'not_in_portal_pass2') |
| 41 | strict pass 1: an attach dry run after the salvage still lists only the company-folder files (ELTA's files stay unattached) | PASS | 26 vs 26 |
| 42 | import: hubspot_deals_import.csv has one row per inferred anchor (7), header == the card-derived column set | PASS | 7 |
| 43 | import: Record ID filled from the ledger on the 6 API-created deals, blank on the Aselsan row; Pipeline / Deal Stage blank everywhere | PASS |  |
| 44 | import: Company Record ID filled on 6 rows (incl. ELTA → 9002); company status resolved×5 / salvaged×1 / missing_in_portal×1 | PASS |  |
| 45 | import: hubspot_companies_import.csv lists exactly the confirmed-missing company (Aselsan) with its Drive folder link | PASS | [{'Company name': 'Aselsan', 'Company Domain Name': '', 'Description': 'Drive folder: https://drive.google.com/drive/folders/m0502022Aselsan', 'mrload_company_node_key': '30 Sales|20 opportunities and customer data|Quantum|2022_Aselsan', 'mrload_legacy_company_id': '3020Q-4164510e', 'mrload_segment': 'Quantum', 'mrload_drive_folder_id': 'm0502022Aselsan', 'mrload_drive_link': 'https://drive.google.com/drive/folders/m0502022Aselsan', 'mrload_asset_count': '2', 'mrload_deal_candidate_count': '1', 'mrload_parked_count': '1', 'mrload_drive_modified_at': '2026-06-15T10:30:00.000Z', 'mrload_resolution_status': ''}] |
| 46 | bq: ledger-export loaded hubspot_deals_import (7 rows) + hubspot_companies_import (1 row) into mrload_raw with --replace, no load errors | PASS | 7/1 rows |
| 47 | bq: the loaded deals table is byte-identical to review/hubspot_deals_import.csv; schema names = snake_case headers in CSV order (23, positional) | PASS | 23 columns, head ['hs_object_id', 'dealname'] |
| 48 | checkpoints: every step run recorded rc=0, in the executed order | PASS | preflight walk bq-init bq-load hs-props hs-props dbt hs-props-verify review companies-dry companies-live attach-dry attach-upload attach-notes attach-notes ledger-export deals-dry deals-live ledger-export |
| 49 | pipeline_state: all 16 steps done, next = none (pass 1 + pass 2 complete, deal ids written back) | PASS | next=None pass 1 complete |
| 50 | dbt final build after write-back: 49 tests, 0 fail/error | PASS | {'pass': 47, 'warn': 2, 'fail': 0, 'error': 0} |

Ledger at the end: companies_resolved created=6 / matched_by_name=1 ·
files_uploaded uploaded=26 · file_notes_posted attached=26 · deals_created created=6 (one per inferred anchor; Aselsan's anchor has no company in the portal) · companies_resolved matched_by_name_pass2=1 (ELTA) / not_in_portal_pass2=1 (Aselsan).

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
silver, the two HubSpot import tables materialised in `mrload_raw` by the same
step (schema-validated positional load, byte-identical to the review files),
pass 2 from an approved decisions file, and the negative path.

Does not prove: Google / HubSpot authentication and quotas, the Drive
sharing step, BigQuery-only SQL behaviour outside the shimmed functions, and
Files API limits on real binaries. `scripts/run_pass1.sh preflight` covers the
first three live before anything is written.
