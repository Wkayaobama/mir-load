# Pass 2 — the sequence, on any surface (Cloud Shell shown)

The ordered recipe from the working branch to loaded deals, with the place to *look* at every
stage. Runbook §7 explains each step; `docs/EXECUTION_SURFACES.md` records how the three
surfaces (VS Code notebooks, WezTerm terminal, Cloud Shell) were brought to the same result.
Notebook users run the same steps as `Invoke-Step '<step>'`; WezTerm users type the same
commands. Nothing below is specific to Cloud Shell except the four `cloudshell` verbs.

## Preconditions on the clone you run from

- Pass 1 is complete there: `companies-live`, `attach-notes` and `hs-props` done, so
  `scripts/dev/pipeline_state.sh` shows 14 steps done and the `mrload_*` deal properties exist
  in the portal.
- `.env` points at the **sandbox** token and portal `49610528` (`scripts/dev/env_clinic.sh`).
- The live HubSpot steps of pass 1 ran from *this* clone, or its `.mrload/ledger.sqlite` was
  copied here: pass 2 associates through that ledger.

## The sequence

```bash
git fetch origin && git checkout claude/mr-load-library-system-dphpbj && git pull
scripts/run_pass1.sh preflight        # new shell only: tools, .env, portal id = sandbox
scripts/run_pass1.sh review           # offline: deal_decisions.csv (approve=N), deal_anchors.csv, deal_documents.csv,
                                      #          hubspot_deals_import.csv + hubspot_companies_import.csv (Record IDs from the ledger if any)

# ── look ──────────────────────────────────────────────────────────────────────────────────
column -s, -t < .mrload/review/deal_decisions.csv | less -S     # aligned; ← → to scroll, q to quit
column -s, -t < .mrload/review/deal_anchors.csv   | less -S     # a quoted comma misaligns that row: a glance, not a proof

# ── edit (pick one) ───────────────────────────────────────────────────────────────────────
cloudshell edit .mrload/review/deal_decisions.csv               # Cloud Shell Editor pane; set approve=Y, dealname, amount; Ctrl+S
nano .mrload/review/deal_decisions.csv                          # terminal; Ctrl+O Enter Ctrl+X
sed -i -E '2,$ s/,N,([^,]*)\r?$/,Y,\1/' .mrload/review/deal_decisions.csv   # approve EVERY row (approve = 15th column, before operator_note)
cloudshell download .mrload/review/deal_decisions.csv           # or edit on the laptop (VS Code, Rainbow CSV), then ⋮ → Upload
mv ~/deal_decisions.csv ~/mir-load/.mrload/review/              #   the upload lands in $HOME; put it back

scripts/run_pass1.sh deals-dry        # salvage: each orphan company searched by name → salvaged (id) or missing_in_portal,
                                      #          persisted in the ledger even though the step is dry; failed rows print their reason;
                                      #          the two CSVs are refreshed with this run's results

# ── look ──────────────────────────────────────────────────────────────────────────────────
python3 -m json.tool .mrload/deals_dry.json | less              # per anchor: status, company_status, error
scripts/run_pass1.sh status                                     # ledger counts by status (companies_resolved shows *_pass2)
less -S .mrload/review/hubspot_deals_import.csv                 # the refreshed import file, raw

scripts/run_pass1.sh ledger-export    # THE MATERIALISATION: 4 ledger tables + the two import files regenerated from ledger + decisions
                                      #          → mrload_raw.hubspot_deals_import / hubspot_companies_import → dbt build; prints the console query

# ── look, from the terminal (bq is preauthenticated on Cloud Shell) ───────────────────────
bq query --use_legacy_sql=false 'SELECT dealname, company_name, company_hs_object_id, op_company_status, op_approve, hs_object_id
                                 FROM `wisekeybq.mrload_raw.hubspot_deals_import` ORDER BY company_name, dealname'
bq query --use_legacy_sql=false 'SELECT name, description FROM `wisekeybq.mrload_raw.hubspot_companies_import`'
```

Line endings are not a concern: the decisions file is written with CRLF and read back as LF,
CRLF or a mix, so the editor, `nano` and the `sed` line all produce a file `deals-dry` accepts.
The `sed` line flips every row and leaves a row whose `operator_note` contains a comma
untouched; per-row approval is the point of the file, so prefer the editor.

## The branch point after `ledger-export`

**API route — the programmatic loader, one surface.** `deals-live` creates the approved deals
through the API and associates each to its company id, *including the salvaged ones*: a
salvaged row carries `company_hs_object_id`, which is all the step needs. Then `ledger-export`
once more, so `hs_object_id` reaches silver and the tables.

```bash
scripts/run_pass1.sh deals-live
scripts/run_pass1.sh ledger-export
```

The one prerequisite is a valid **pipeline and stage id of the portal the token belongs to**
(sandbox ids differ from production). A `failed` row with `create_error` is that prerequisite
missing. Read the ids once:

```bash
set -a; source .env; set +a
curl -s -H "Authorization: Bearer $HUBSPOT_SANDBOX_TOKEN" https://api.hubapi.com/crm/v3/pipelines/deals \
  | python3 -c 'import json,sys
for p in json.load(sys.stdin)["results"]:
    print(p["id"], p["label"]); [print("   ", s["id"], s["label"]) for s in p["stages"]]'
```

and put them in `deal_decisions.csv` (`pipeline`, `dealstage`) or in `MRLOAD_DEAL_PIPELINE` /
`MRLOAD_DEAL_STAGE`.

**Wizard route — no API.** The BigQuery console is the file transfer: run the query the step
printed, then *Save results* → Google Sheets or CSV → HubSpot **Import**, one file, Deals +
Companies.

```sql
SELECT * EXCEPT(pipeline, dealstage, op_company_status, op_api_status, op_api_error)
FROM `wisekeybq.mrload_raw.hubspot_deals_import`
WHERE op_approve = 'Y'
ORDER BY company_name, dealname
```

Leaving the blank `pipeline` / `dealstage` columns out makes the wizard ask for a default
pipeline and stage instead of failing on blanks. Map once: `hs_object_id` → Deals · Record ID
(blank = create, filled = update), `company_hs_object_id` → Companies · Record ID (the
association), `dealname` / `amount` / `description` / `mrload_*` auto-match on their internal
names, `company_name` → *Don't import*. If `hubspot_companies_import` has rows, import those
companies first, run `deals-dry` again so `company_hs_object_id` fills, `ledger-export` again,
then import the deals. Afterwards set `approve=N` on the rows you imported: the ledger does not
know wizard-created deals and a later `deals-live` would create them again. Without the
console: `bq query --use_legacy_sql=false --format=csv --max_rows=100000 '<the query>' >
~/hubspot_deals_import.csv && cloudshell download ~/hubspot_deals_import.csv`.

**Why no Cloud Function or second script.** The create-and-associate logic, the idempotency and
the sandbox guard live in `deals.py` and the clone's SQLite ledger. A function reading BigQuery
would duplicate the logic, hold its own HubSpot secret, not see the ledger (re-runs could
duplicate deals) and invert the data flow, because the tables are regenerated from
`deal_decisions.csv` on every `ledger-export`. The one genuinely different programmatic option
is StackSync syncing `hubspot_deals_import` to Deals with `hs_object_id` as the match key;
whether it can also write the deal-to-company association from `company_hs_object_id` is to
be verified in its UI first.

## What each stage produces, and where to look

| Stage | Produces | Look with |
|---|---|---|
| `review` | `.mrload/review/deal_decisions.csv`, `deal_anchors.csv`, `deal_documents.csv`, `hubspot_deals_import.csv`, `hubspot_companies_import.csv` | `column … \| less -S`, `cloudshell edit`, `less -S` |
| edit | `approve=Y`, `dealname`, `amount` per row | the same file |
| `deals-dry` | `.mrload/deals_dry.json`; ledger `companies_resolved` rows `matched_by_name_pass2` / `not_in_portal_pass2`; refreshed import CSVs | `python3 -m json.tool`, `scripts/run_pass1.sh status` |
| `ledger-export` | `mrload_raw.{companies_resolved,files_uploaded,file_notes_posted,deals_created}`, `mrload_raw.hubspot_deals_import`, `mrload_raw.hubspot_companies_import`, `.mrload/ledger_export/*.csv` + `*.schema.json`, silver rebuilt | `bq query` (above), BigQuery console |
| `deals-live` | ledger `deals_created`; deals + associations in the portal | `.mrload/deals_live.json`, `status`, then `ledger-export` and the same `bq query` (`hs_object_id` filled, `op_api_status = created`) |
| any time | position in the sequence | `scripts/dev/pipeline_state.sh` |

A `ledger-export` between `deals-dry` and `deals-live` keeps pass 2 *done* in
`pipeline_state.sh` (pass 2 hangs off `review` and `companies-live`, not off the write-back);
its "next" hint still proposes `deals-live`, because it knows only the API route.

## Evidence — the probe of 2026-09-30 (sandbox, from Cloud Shell)

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

Reading it: ten rows, one per inferred anchor. The four `2021_ELTA` anchors, `no_company_resolved`
before the salvage, now carry the id of the ELTA company found by name (`salvaged`); the six
others carry their pass-1 company (`resolved`). `op_approve = N` and `hs_object_id = NULL`
everywhere: nothing is created yet, which is what the table should say before an approval.
The rows are the content of `review/hubspot_deals_import.csv` of the clone that ran
`ledger-export` (rehearsal check 47 loads the file byte-identically).

## Pitfalls

- **The tables hold persisted ledger state.** A `deals-dry` outcome such as `would_create` is not
  in them; `deals-dry` remains the pre-flight view.
- **The import files and tables are generated.** Edits to dealname or amount go in
  `deal_decisions.csv`; the next `ledger-export` or deals run overwrites the generated files.
- **Wizard-imported rows must be set `approve=N`** before any later `deals-live`.
- **`Drafts/pre-PO` is a working folder, not a customer.** Pass 1 classified it as a company
  folder and created it in the sandbox (id `58614964830`); it anchors the first row above. Before
  the production walk: exclude the folder in `context/cards/library.yaml`, delete the sandbox
  company, re-run `walk → bq-load → dbt → review`.
- **The wizard's auto-match** of the internal names is documented HubSpot behaviour, still to be
  confirmed once in the sandbox.
