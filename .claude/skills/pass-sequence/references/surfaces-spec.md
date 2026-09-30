# The surfaces spec (`docs/execution_surfaces.yaml`)

One YAML file is the source of the workbook; `scripts/build_surfaces_xlsx.py` renders it, and
`scripts/dump_surfaces_xlsx.py` recovers it from a workbook built by hand. Long strings are
written as block scalars (`|`) so diffs stay readable.

```yaml
title: "<project> — three execution surfaces, one pipeline"        # Overview A1
verdict: |                                                          # Overview A2 (merged, wrapped)
  Verdict: … one paragraph; add a sentence per pass naming the commits and the probe.
legend: |                                                           # Overview last row
  Legend: Parity column colours — green Parallel, yellow Conditional, orange Divergent. Layers L0–L15 …
  Remediation … (R01–R25) … Source: the repository on branch <branch> at <date> (<commit>), …
surfaces:                                                           # column headers reused by every sheet
  A: "A · Zip / clone → VS Code → Polyglot notebook (PowerShell cells)"
  B: "B · WezTerm → Ubuntu terminal (WSL or Docker)"
  C: "C · Cloud Shell terminal"
motions:                                                            # Overview rows: one per motion
  - motion: "A · Zip / clone → VS Code → Polyglot notebook"
    acquire: …
    ubuntu: …
    driver: …
    state: …
    assessment: …
layers:                                                             # sheet "Layers", L0…
  - id: L14
    layer: "Pass-2 decisions: view and edit review/deal_decisions.csv"
    A: "VS Code on the clone (Rainbow CSV); Invoke-Ubuntu 'column -s, -t < …'"
    B: "column -s, -t < … | less -S; nano"
    C: "cloudshell edit …; nano; cloudshell download → edit → ⋮ Upload → mv …"
    parity: Conditional                                             # Parallel | Conditional | Divergent — nothing else
    diverges: "The file is per clone (L9): edit it where the pass runs …"
    remediation: "None needed. Bulk approve: sed -i -E … (approve = 15th column)"
    verified: "Cloud Shell run of 2026-09-30 …"
steps:                                                              # sheet "Pipeline steps"
  - n: 15                                                           # int for runner steps; "15b", "15c" for branch/probe rows
    step: deals-dry                                                 # bare sub-command → the run-sheet generator makes a cell for it
    gate: ""                                                        # "" or "LIVE <GATE_NAME>"
    A: "Invoke-Step 'deals-dry'"
    B: "scripts/run_pass1.sh deals-dry"
    C: "scripts/run_pass1.sh deals-dry"
    parity: Parallel
    artefact: "would_create per approved anchor; …"                 # "identical artefact to compare" / run-sheet "what must be true afterwards"
    notes: "Reads deal_decisions.csv of that clone …"
remediation:                                                        # sheet "Remediation", R01…
  - id: R21
    symptom: "deals-dry / deals-live: failed ×N, error create_error"
    layer: L8
    surfaces: "A B C"
    cause: "dealstage / pipeline value is not a STAGE / PIPELINE ID of the portal the token belongs to"
    fix: "Read the ids once: … GET /crm/v3/pipelines/deals …"
    verified: "sandbox pass 2 (6 failed rows)"
equality_checks:                                                    # sheet "Equality checks"
  - stage: after ledger-export
    compare: the import tables (the wizard's input)
    A: "Invoke-Ubuntu 'bq query …'"
    BC: "bq query --use_legacy_sql=false '…'"
    expected: "one row per inferred anchor; … identical from every surface"
parity_meanings:                                                    # optional override of the Overview meaning column
  Parallel: same mechanism, same result on every surface
  Conditional: same result when the stated condition holds (same commit, same identity, same clone for the ledger)
  Divergent: different mechanism; the remediation makes it equivalent
```

## How parity is decided, per row

- **Parallel** when the command, the code path and the artefact are the same on every surface
  and nothing local can change the outcome (`walk`, `dbt`, `review`, a `bq query`).
- **Conditional** when the outcome is the same only under a condition the row states: same
  commit (L1), same Google identity (L5), same clone for the ledger (L9, every live HubSpot step
  and `ledger-export`), a file that lives on one clone (`deal_decisions.csv`).
- **Divergent** when the mechanism differs (a ZIP has no git; the guard exists only where `.git`
  exists) and the remediation column says how the surfaces are made equivalent.

The Overview counts these words live (`COUNTIF` over the Parity columns) and checks that the
counts add up to the row counts (`COUNTA`), so a typo in a parity cell shows as a mismatch.
`build_surfaces_xlsx.py` refuses any word outside the three at build time.

## Producer conventions (fixed, not in the spec)

Arial 10 body, Arial 14 bold title, navy `1F3864` header rows with white bold text, thin grey
borders, wrap + top alignment, parity fills `E2F0D9` / `FFF2CC` / `F8CBAD` with bold text, column
widths per sheet, frozen header rows (Overview at row 5), verdict and legend merged across A:F with
a height derived from their length, `fullCalcOnLoad` on. Ten formulas on the Overview; none
elsewhere, so a LibreOffice recalculation is a nicety, not a requirement: `--verify` prints the
values they will show.
