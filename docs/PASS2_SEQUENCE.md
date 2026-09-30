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
- `MRLOAD_DEAL_PIPELINE` / `MRLOAD_DEAL_STAGE` in `.env` hold ids of *this* portal or are empty — never a
  template text such as `<stage id>`: any non-empty value is sent to HubSpot and rejected with a 400 that
  names no field. The clinic flags `<…>` values as placeholders; run it before pass 2.
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
nano .mrload/review/deal_decisions.csv                          # terminal, any route; Ctrl+O Enter Ctrl+X
cloudshell edit .mrload/review/deal_decisions.csv               # BROWSER TERMINAL ONLY: opens the Cloud Shell Editor pane; Ctrl+S
sed -i -E '2,$ s/,N,([^,]*)\r?$/,Y,\1/' .mrload/review/deal_decisions.csv   # approve EVERY row (approve = 15th column, before operator_note)
cloudshell download .mrload/review/deal_decisions.csv           # BROWSER TERMINAL ONLY: laptop round trip (VS Code, Rainbow CSV), then ⋮ → Upload
mv ~/deal_decisions.csv ~/mir-load/.mrload/review/              #   the upload lands in $HOME; put it back
#   from a WezTerm `gcloud cloud-shell ssh` session the cloudshell verbs fail ("Cannot send messages to client"); the
#   ssh-native pair — NOT YET VERIFIED on this project — is:
#   gcloud cloud-shell scp cloudshell:~/mir-load/.mrload/review/deal_decisions.csv localhost:.        (laptop side)
#   gcloud cloud-shell scp localhost:deal_decisions.csv cloudshell:~/mir-load/.mrload/review/         (back)

MRLOAD_APPROVE_DEAL_CREATE=0 scripts/run_pass1.sh deals-dry      # a TRUE dry run even when the gate sits in .env (process env wins)
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
missing — or, as on 2026-09-30, a template text left in `.env`. Read the ids once:

```bash
set -a; source .env; set +a
curl -s -H "Authorization: Bearer $HUBSPOT_SANDBOX_TOKEN" https://api.hubapi.com/crm/v3/pipelines/deals \
  | python3 -c 'import json,sys
for p in json.load(sys.stdin)["results"]:
    print(p["id"], p["label"]); [print("   ", s["id"], s["label"]) for s in p["stages"]]'
```

and put them in `deal_decisions.csv` (`pipeline`, `dealstage`) or in `MRLOAD_DEAL_PIPELINE` /
`MRLOAD_DEAL_STAGE`. For the sandbox the defaults used on 2026-09-30 were the **Miraex** pipeline:

```bash
sed -i 's|^MRLOAD_DEAL_PIPELINE=.*|MRLOAD_DEAL_PIPELINE=938985861|; s|^MRLOAD_DEAL_STAGE=.*|MRLOAD_DEAL_STAGE=1445448859|' .env
scripts/dev/env_clinic.sh        # no placeholder left
```

| Miraex pipeline `938985861` | stage id |
|---|---|
| 01 - Identification (default for inferred deals) | `1445448859` |
| 02 - Qualifiée | `1445448860` |
| 03 - Evaluation technique | `1445448861` |
| 04 - Construction propositions | `1445448862` |
| 05 - Négociations | `1445448863` |
| Design Win | `1445448864` |
| Closed Won | `1445448865` |
| Closed Dead | `1445448866` |

Per-row overrides go in the `dealstage` column; this one-liner sends received POs to Closed Won and
withdraws the working folder (a business choice, shown as run, not as a rule):

```bash
python3 - <<'EOF'
import csv, re
p = ".mrload/review/deal_decisions.csv"
rows = list(csv.DictReader(open(p, encoding="utf-8-sig"))); cols = rows[0].keys()
for r in rows:
    if re.search(r"\bPO\b|Billing", r["deal_name"], re.I): r["dealstage"] = "1445448865"
    if r["company_name"].startswith("Drafts/pre-PO"):      r["approve"]   = "N"
w = csv.DictWriter(open(p, "w", encoding="utf-8", newline=""), fieldnames=cols); w.writeheader(); w.writerows(rows)
EOF
```

`\bPO\b` does not match `_PO` (underscore is a word character), so `20251124_PO 99-250335` stayed at
Identification on 2026-09-30; widen the pattern or set the stage in the editor.

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

## Evidence 2 — the API route, 2026-09-30 (sandbox, from Cloud Shell)

First attempt, `deals-dry` (live, the gate being in `.env`), ten approved rows:

```
{'failed': 10}
  !! ELTA - 10 Communication_Meeting              failed               salvaged           create_error: 400 Client Error: Bad Request for url: https://api.hubapi.com/crm/v3/objects/deals
  … (ten identical lines)
  → create_error: HubSpot refused the deal — a dealstage/pipeline value must be the STAGE/PIPELINE ID of THIS portal (GET /crm/v3/pipelines/deals)
```

Cause, found with the three look commands: `grep -n "^MRLOAD_DEAL_" .env` printed
`MRLOAD_DEAL_PIPELINE=<pipeline id>` and `MRLOAD_DEAL_STAGE=<stage id>`; the approved rows carried
`('', '', '')` for pipeline, stage and amount; `GET /crm/v3/pipelines/deals` listed eleven pipelines,
among them `938985861 Miraex` with the stages of the table above. A `<…>` value is non-empty, so the
runner's "dealname and dealstage are required" guard let it through; the error body that would have
named the field is not kept by the client (a 400 is not a scope problem — that would be a 403).
`set -a; source .env` also fails on such a line (`<` is a redirection to bash).

After the `.env` fix and the per-row overrides: `deals-live` created nine deals in the Miraex pipeline,
confirmed in the portal's deal list — two at *Closed Won* with the close date of the run (MEMQ, ELTA -
30 Offer, PO, Order Confirmation, Invoice), seven at *01 - Identification* (Pixel Photonics ×2,
Quantinuum - references, Huber-Suhner, ELTA ×3), no owner, no amount; `Drafts/pre-PO` absent
(approve=N). The post-run `ledger-export` and the table probe with `hs_object_id` filled were not
captured in this record; run the probe query above to complete it.

## Pitfalls

- **A template text in `.env` is a value.** `<stage id>` passes the required-field guard and HubSpot
  answers 400 without a visible reason. Empty is safe; `scripts/dev/env_clinic.sh` flags `<…>`.
- **The `cloudshell` verbs need the browser terminal.** From a `gcloud cloud-shell ssh` session they end
  with "Cannot send messages to client"; `nano` works everywhere, `gcloud cloud-shell scp` is the ssh
  pair (to verify). A disconnected browser tab gives the same message: reload it.
- **A gate kept in `.env` makes the dry step live on every surface**, notebook included. Prefix the
  command with `MRLOAD_APPROVE_<GATE>=0` for a true dry run.

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
