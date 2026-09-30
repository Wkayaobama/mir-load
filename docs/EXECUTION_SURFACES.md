# Execution surfaces — how the three layers were reconciled (record)

Companion to `docs/EXECUTION_SURFACES.xlsx`, which holds the same material as tables
(*Overview*, *Layers* L0–L15, *Pipeline steps* 1–19 with the pass-2 wizard route 15b–15c,
*Remediation* R01–R25, *Equality checks*). This page is the narrative: what diverged between the
surfaces, how each divergence was observed, what closed it, and the probe that ended the
reconciliation on 2026-09-30. The pass-2 recipe itself is `docs/PASS2_SEQUENCE.md`; the step
reference is `docs/RUNBOOK_PASS1.md`.

## 1. The three motions and the vocabulary

| Motion | Acquire | Ubuntu | Driver | State lives in |
|---|---|---|---|---|
| **A** · Zip or clone → VS Code → Polyglot notebooks (`mr-load-compass.dib`, `mr-load-run.dib`) | ZIP (no `.git`) or VS Code *Git: Clone* | WSL distro or Docker image, provisioned by the compass | PowerShell cells: `Invoke-Step` → `scripts/run_pass1.sh` | the clone the route uses |
| **B** · WezTerm → Ubuntu terminal | `git clone` | the same WSL distro or Docker image as A | `scripts/run_pass1.sh <step>` | the same clone as A when the same distro/folder is used |
| **C** · Cloud Shell | `git clone` in the persistent home | Cloud Shell VM (`gcloud`, `bq` built in) | `scripts/run_pass1.sh <step>` | `~/mir-load/.mrload` |

Parity of a layer or a step is one of three words. **Parallel**: same mechanism, same result on
every surface. **Conditional**: same result when a stated condition holds (same commit, same
Google identity, same clone for the ledger). **Divergent**: a different mechanism, with a
remediation that makes it equivalent. The workbook colours them green, yellow and orange.

## 2. The principle that made reconciliation possible

One script is the execution surface: every notebook cell, terminal line and Cloud Shell line runs
`scripts/run_pass1.sh <step>`, with the gate for a live step opened inline for that step only,
after a dry run and a `YES` (or `$ConfirmLive` in a notebook). Shared truth is what the script
writes to: the BigQuery project and datasets, the HubSpot portal of the token, the Drive scope
root. Local to a clone are `.env`, the Python environment and `.mrload/` (hierarchy CSV, review
files, SQLite ledger, checkpoints, logs). Every reconciliation below either made a step read
from the shared truth instead of a local file, or stated the condition under which a local
artefact may be trusted.

## 3. Timeline — divergence, observation, fix

| When | Divergence | Observed how | Closed by |
|---|---|---|---|
| 2026-09-14 | Steps run out of order on Cloud Shell (`dbt` before `bq-load`). | `Not found: Table …mrload_raw.library_hierarchy` in the shared log. | The sequence rule and its tooling: `scripts/dev/pipeline_state.sh` (done / stale / failed / pending, staleness by order) and the run-sheet notebook (`134f462`); *Remediation* R03. |
| 2026-09-14 | The `dbt` step lost its log and checkpoint on every surface. | `.mrload/logs/dbt-…log: No such file or directory`. | `d4d5dfb` absolutises the state and ledger paths before `cd dbt`; R02. |
| 2026-09-15 | Commit drift between the notebook clone and Cloud Shell. | 6 dbt models instead of 7; `deal_decisions.csv` per PDF instead of per anchor. | Layer L1 "pick the commit": `git fetch origin && git checkout claude/mr-load-library-system-dphpbj && git pull` on every surface, `git log -1` compared; R04. |
| 2026-09-15 | Enforced dbt contract rendered a foreign key to a non-primary-key column. | Identical BigQuery error on every surface. | `953f6f4`: foreign keys reference `company_node_key`; R05. |
| 2026-09-15 | Live HubSpot steps idempotent only through the local ledger. | Reasoning on a second clone with an empty ledger. | The ledger rule (`de03946`): live steps from one surface, or copy `.mrload/ledger.sqlite`; L9, R14. |
| September 2026 | Property definitions absent when StackSync mapping was attempted (`missing: 36`). | `hs-props-verify` output on Cloud Shell. | `hs-props` before `dbt`, `hs-props-verify` after (`e9c3a86`); `scripts/dev/mapping_sheet.sh` to read the mapping sheet from a terminal (`79b8319`). |
| September 2026 | The ZIP motion had no git, no exec bits, no secret guard. | Simulated in the development container. | In-place conversion recipe printed by `scripts/dev/env_clinic.sh`; the safety scripts recognise a non-git folder (`797636a`); L0, L2, L7, R08, R09. |
| September 2026 | "Dry" steps ran live on Cloud Shell because the gates were set globally in `.env`. | The attach log showed uploads during `attach-dry`. | Accepted by the operator as a production-flow choice; the `sed` lines for `MRLOAD_ALLOW_PROD_PORTAL` and `HUBSPOT_SANDBOX_PORTAL_ID=49610528` kept as reference. No guardrail implemented (explicitly withheld pending consent). |
| September 2026 | Pass 1 in the sandbox: files uploaded 59, failed 25. | 26 Drive export `403` on native Slides/Docs, 2 large `.pptx` timeouts (30 s client timeout). | Diagnosed; four fixes proposed (PDF export fallback + link-only note, 10-minute upload timeout, `download_error` label, 403 retry). **Not implemented, awaiting consent.** |
| 2026-09-28 | Pass 2 on Cloud Shell: `no_company_resolved: 4`, `failed: 6`. | `deals-dry` summary. The 4 were anchors under year-prefixed company folders (`2021_ELTA`), never resolved by pass 1; the 6 were `create_error`. | `6f38600`: orphan salvage (search by name, never create; strict pass 1, salvaged companies visible to pass 2 only) and the HubSpot-Import-ready CSVs; rehearsal 48/48; R22. `create_error` → R21 (stage ids of the portal). |
| 2026-09-28 | The import CSVs live on the Cloud Shell VM, out of reach of the HubSpot wizard; each surface would hold its own copy. | Operator observation. | `765cd01`: `ledger-export` regenerates the two files from ledger + `deal_decisions.csv` and loads them as `mrload_raw.hubspot_deals_import` / `hubspot_companies_import` (snake_case, HubSpot internal names on the head columns, `op_*` bookkeeping, card types); rehearsal 50/50; the side branch fast-forwarded into the working branch on 2026-09-29; L15, R23. |
| 2026-09-30 | Was a further loader needed for the salvaged deals (Cloud Function, native script)? | The probe below, run from Cloud Shell. | No. `deals-live` already creates and associates salvaged deals through the API; the table is the mirror and the wizard's input, not a second loader. Recorded in `docs/PASS2_SEQUENCE.md`. |

## 4. Pass 2 on each surface, after the reconciliation

| Operation | A · notebook | B · WezTerm | C · Cloud Shell | Parity |
|---|---|---|---|---|
| View `deal_decisions.csv` | VS Code on the clone (Rainbow CSV); `Invoke-Ubuntu 'column -s, -t < .mrload/review/deal_decisions.csv'` | `column -s, -t < … \| less -S` | same as B; or `cloudshell edit` (read) | Parallel |
| Edit it | VS Code | `nano`, VS Code (WSL remote) | `cloudshell edit .mrload/review/deal_decisions.csv`; `nano`; or `cloudshell download` → edit → ⋮ Upload → `mv ~/deal_decisions.csv ~/mir-load/.mrload/review/` | Conditional: per clone (L9) |
| Bulk approve | — | `sed -i -E '2,$ s/,N,([^,]*)\r?$/,Y,\1/' …` | same | Parallel |
| `deals-dry`, `ledger-export`, `deals-live` | `Invoke-Step '<step>'` | `scripts/run_pass1.sh <step>` | same | Parallel / Conditional (ledger) |
| Probe the import tables | `Invoke-Ubuntu 'bq query …'` | `bq query --use_legacy_sql=false '…'` | same, `bq` preauthenticated | Parallel |
| Wizard file | BigQuery console → Save results | same | same, or `bq query --format=csv … > ~/f.csv && cloudshell download ~/f.csv` | Parallel |

The decisions file stayed local on purpose: it is the operator's approval record, edited where
pass 2 runs. Everything the wizard or another system needs to *read* moved to BigQuery.

## 5. The probe that closed the reconciliation (2026-09-30, sandbox portal 49610528, Cloud Shell)

```
$ bq query --use_legacy_sql=false 'SELECT dealname, company_name, company_hs_object_id, op_company_status, op_approve, hs_object_id
                                   FROM `wisekeybq.mrload_raw.hubspot_deals_import` ORDER BY company_name, dealname'
+-------------------------------------------------------------------------------------------------------+-----------------+----------------------+-------------------+------------+--------------+
|                                               dealname                                                |  company_name   | company_hs_object_id | op_company_status | op_approve | hs_object_id |
+-------------------------------------------------------------------------------------------------------+-----------------+----------------------+-------------------+------------+--------------+
| Drafts/pre-PO - 20211215 PO received from ELTA for Phase 0 - ZXPO E000353172 from IAI to vendor CA325 | Drafts/pre-PO   | 58614964830          | resolved          | N          | NULL         |
| 2021_ELTA - 10 Communication_Meeting                                                                  | ELTA            | 35988056623          | salvaged          | N          | NULL         |
| 2021_ELTA - 20 User Requirement Specifications                                                        | ELTA            | 35988056623          | salvaged          | N          | NULL         |
| 2021_ELTA - 30 Offer, PO, Order Confirmation, Invoice                                                 | ELTA            | 35988056623          | salvaged          | N          | NULL         |
| 2021_ELTA - ELTA - Creation of supplier                                                               | ELTA            | 35988056623          | salvaged          | N          | NULL         |
| Huber-Suhner - Quantum - Huber-Suhner                                                                 | Huber-Suhner    | 58614967557          | resolved          | N          | NULL         |
| MEMQ - memQ-Miraex-PO-041326                                                                          | MEMQ            | 58610718497          | resolved          | N          | NULL         |
| Pixel Photonics - 20251124_PO 99-250335 Miraex SA - Pixel Photonics                                   | Pixel Photonics | 58614940725          | resolved          | N          | NULL         |
| Pixel Photonics - 20260122_PO 99-260043 Miraex SA  - Pixel Photonics                                  | Pixel Photonics | 58614940725          | resolved          | N          | NULL         |
| Quantinuum - references                                                                               | Quantinuum      | 28548361580          | resolved          | N          | NULL         |
+-------------------------------------------------------------------------------------------------------+-----------------+----------------------+-------------------+------------+--------------+
```

What it proves, surface by surface:

- **The salvage works in the real portal.** The four ELTA anchors that were `no_company_resolved`
  on the first Cloud Shell run now carry `35988056623`, the ELTA company found by name; the
  pipeline created nothing.
- **The materialisation is faithful.** Ten rows, one per inferred anchor, with the statuses and
  ids the ledger holds; the rehearsal proves the load byte-identical to the review file (check 47),
  and this query returns the same table from a notebook cell, a WezTerm tab or Cloud Shell,
  because `bq` reads the same dataset.
- **Nothing was created.** `op_approve = N` and `hs_object_id = NULL` on every row: the table
  reflects the state before an approval, which is the correct state for a probe.
- **The loader question is settled.** An approved ELTA row goes through `deals-live` like any
  other; the table needs no consumer of its own.

## 6. What stays open (none of it blocks pass 2)

- The four attach fixes (Drive export `403` fallback, upload timeout, `download_error` label,
  retry) and the two guardrails (portal guard, dry-forces-dry): proposed, **not implemented**,
  awaiting explicit consent.
- `Drafts/pre-PO`: a working folder classified as a company folder and created in the sandbox
  (R25). Exclude it in the card and delete the sandbox company before the production walk.
- Valid sandbox pipeline and stage ids for the API route (R21).
- A `ledger-import` step rebuilding a clone's SQLite ledger from the four `mrload_raw` tables
  would remove the last Conditional on the live steps (L9); not built.
- StackSync as an alternative loader from `hubspot_deals_import` (match on `hs_object_id`):
  possible in principle; whether it writes the deal-to-company association from
  `company_hs_object_id` is to be verified in its UI.
- The HubSpot wizard's auto-match of the internal-name headers: to confirm once in the sandbox.

## 7. Keeping the parity

After each stage, compare the artefact the *Equality checks* sheet names: node count after
`walk`, `numRows` after `bq-load`, model and test counts after `dbt`, anchor counts after
`review`, `*_pass2` ledger statuses after `deals-dry`, the import-table query after
`ledger-export`, `hs_object_id` after `deals-live`. Two surfaces on the same commit, the same
Google identity and the same ledger produce the same numbers; any difference points at one of
the fifteen layers.
