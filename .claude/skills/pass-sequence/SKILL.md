---
name: pass-sequence
description: Build the pass-N documentation set for a scripted, gated, multi-surface data pipeline (mr-load style) — always in this order — the ordered PASS{N}_SEQUENCE.md (every command, the look/edit commands at each stage per surface, the branch point, real probe evidence), the EXECUTION_SURFACES timeline (.md) and the execution-surfaces workbook (.xlsx, produced from a YAML spec by the bundled script, LibreOffice/Excel compatible), plus the optional Polyglot notebook run sheet (.dib) as the sequential motion. Use it whenever a new pass, step, surface or branch route is added to such a pipeline, whenever someone asks to reconcile or compare execution layers (IDE/Polyglot notebook vs WezTerm/WSL terminal vs Cloud Shell), asks for a "run sheet", "surfaces workbook", "parity", "timeline", "sequence for pass 2/3", "which step is missing", or wants a probe (bq query, ledger status) written into the record — even if they only say "document the process" or "update the execution surfaces".
---

# pass-sequence

The pattern behind a deployment that reached parity across three execution surfaces (VS Code +
Polyglot notebooks, WezTerm terminal, Cloud Shell) is not the code: it is one script as the
execution surface, gates opened inline after a dry run, checkpoints read back by a state machine, a
local ledger for idempotency, shared truth in the warehouse, and a **documentation set that is
produced in a fixed order** every time a pass is added. This skill produces that set. Read
`references/pattern.md` once for the why; the rest of this file is the how.

Four artefacts, always in this sequence, because each one feeds the next:

| # | Artefact | Role | Source of truth |
|---|---|---|---|
| 1 | `docs/PASS{N}_SEQUENCE.md` | the recipe: commands in order, look/edit at each stage, branch point, evidence | the runner script + what the operator actually ran |
| 2 | `docs/EXECUTION_SURFACES.md` | the record: motions, parity vocabulary, dated timeline of divergence → observation → fix (commit), per-surface operations, probe, open items | git log + operator logs |
| 3 | `docs/EXECUTION_SURFACES.xlsx` from `docs/execution_surfaces.yaml` | the audit: Layers, Pipeline steps, Remediation, Equality checks with live parity formulas | the YAML spec (edit it, rebuild) |
| 4 | `notebooks/<project>-run.dib` (optional motion) | the sequential motion: one cell per step, LIVE cells behind `$ConfirmLive`, branch cells | the same spec's steps |

Docs only. A documentation pass never changes pipeline code to fit the documentation; the only
non-markdown edit it may make is a usage comment (a script header). If a fact is missing (a
step's output, an id, a count), it is obtained by running the step or asking the operator — never
composed. Evidence pasted into the record is verbatim operator output with its date and portal.

## 0 · Collect the execution layer before writing anything

Gather these facts; every artefact below is a projection of them.

- **Steps in order, with gates.** From the runner's header comment and its `case` list
  (`scripts/run_pass1.sh` in mr-load): name, gate variable, dry/live pair, what the step writes.
  A command that is not in the case list does not go in a sequence page (an operator once typed a
  comment line as a command: R01 of the workbook).
- **Surfaces.** For each motion (notebook / terminal / Cloud Shell): how the code is acquired,
  which Ubuntu runs it, what drives a step, where state lives (`.env`, ledger, checkpoints, logs).
- **Shared truth vs local state.** What every surface reads from the same place (warehouse
  project and datasets, the portal of the token, the Drive root) versus what is per clone.
- **What happened.** `git log --oneline` since the previous pass; the operator's pasted logs and
  errors; the probes they ran (a `bq query`, a `status` output). Note the dates.
- **The branch points.** Where an operator chooses between two legitimate routes (API loader
  versus import wizard; live versus dry) — a branch point is documented, not replaced by a new
  tool.
- **The environment's view.** Run the env clinic (or read `.env` with values masked) before documenting
  a pass: a template text such as `<stage id>` is a non-empty value that passes required-field guards
  and produces an error with no field name. Record which surface-specific verbs are route-dependent
  (a browser-terminal messenger versus an ssh session) instead of listing them as "the Cloud Shell way".
- **The state machine's view.** What `pipeline_state.sh` (or its equivalent) says about the order
  you are about to document: does a step run out of the canonical order make others stale? Does
  its "next" hint know the branch route? Say so in the docs instead of changing the hint.

## 1 · The sequence page — `docs/PASS{N}_SEQUENCE.md`

Start from `assets/PASS_SEQUENCE.template.md`. Keep its sections and their order:

1. **Preconditions on the clone you run from** (previous pass complete there, sandbox token and
   portal, ledger present).
2. **The sequence** — one fenced `bash` block, copy-pasteable, with `# ── look ──` and
   `# ── edit ──` inserts after every step that produces a file the operator must read or change.
   A stage without a look command is the gap operators hit first on Cloud Shell. Give the
   terminal forms (`column -s, -t < f | less -S`, `less -S`, `python3 -m json.tool`), the editor
   forms (`cloudshell edit`, `nano`, VS Code), the round trip (`cloudshell download` → ⋮ Upload
   → `mv` back), and the warehouse probe (`bq query …`). State what each command is *not* good for
   (`column` misaligns a quoted comma; a bulk `sed` skips rows with a comma in a free-text column).
3. **The branch point** — both routes, the one prerequisite each has (portal stage ids for the
   API route; column mapping for the wizard), and why no additional loader is needed.
4. **What each stage produces, and where to look** — a table: stage · produces · look with.
5. **Evidence** — the operator's probe output verbatim, dated, with the portal, followed by a
   reading of it (what each column proves; that nothing was created if that is the state).
6. **Pitfalls** — persisted-state-only tables, generated files never edited, approve=N after a
   wizard import, data-quality flags seen in the probe (a working folder created as a company).

Line-ending and quoting facts belong here when they affect an edit (a CSV writer emitting CRLF
that the reader accepts back in any form).

## 2 · The surfaces record — `docs/EXECUTION_SURFACES.md`

Start from `assets/EXECUTION_SURFACES.template.md` if the file does not exist; otherwise append.
The timeline table is append-only history: `When · Divergence · Observed how · Closed by` with the
commit hash for every fix and "**not implemented, awaiting consent**" for proposals the operator
has not approved. Add the pass-N operations per surface as a table (view, edit, bulk approve,
steps, probe, wizard file, parity word), paste the closing probe, refresh **What stays open**.
Keep the parity vocabulary exact: **Parallel** (same mechanism, same result), **Conditional**
(same result under a stated condition: same commit, same identity, same clone for the ledger),
**Divergent** (different mechanism, remediation makes it equivalent).

## 3 · The workbook — `docs/execution_surfaces.yaml` → `docs/EXECUTION_SURFACES.xlsx`

The YAML is the source; the workbook is built from it. Format: `references/surfaces-spec.md`.

```bash
S=.claude/skills/pass-sequence/scripts
# first time only, when a workbook exists but no spec: bootstrap the spec and prove the round trip
python3 $S/dump_surfaces_xlsx.py --xlsx docs/EXECUTION_SURFACES.xlsx --out docs/execution_surfaces.yaml
python3 $S/build_surfaces_xlsx.py --spec docs/execution_surfaces.yaml --out /tmp/roundtrip.xlsx --compare docs/EXECUTION_SURFACES.xlsx
# every pass: edit the YAML, rebuild, verify
python3 $S/build_surfaces_xlsx.py --spec docs/execution_surfaces.yaml --out docs/EXECUTION_SURFACES.xlsx --verify
```

What to add for pass N: a **Layer** per new operator concern (e.g. "view and edit the decisions
file per surface", "warehouse materialisation + probe"), **Pipeline steps** rows for the new
steps and branch routes (use `15b`, `15c` style ids so existing numbering stays stable),
**Remediation** rows for every failure observed in the operator's logs (symptom · layer ·
surfaces · cause · fix · verified), **Equality checks** for what to compare after each new stage,
and an Overview verdict sentence naming the pass and the commits. Parity is decided per row by
the vocabulary above; the producer refuses any other word.

Recalculation: the workbook's formulas are whole-column `COUNTIF` / `SUM` / `COUNTA` and the file
is saved with `fullCalcOnLoad`, so Excel and LibreOffice compute them on open. If the `xlsx`
skill's `recalc.py` is available, run it; when LibreOffice cannot open the file in the sandbox
(it has timed out at seven minutes before), rely on `--verify`, which prints the exact values the
formulas will show, and say so in the hand-over rather than presenting the file as recalculated.

## 4 · The motion — the Polyglot run sheet (optional)

Offer it when the project already has PowerShell helpers (`Invoke-Ubuntu`, `Invoke-Step`,
`Get-PipelineState`, `Invoke-NextStep`, `Invoke-Sequence`) or when the operator drives steps from
VS Code. For a **new** run sheet generate the skeleton from the spec and review it:

```bash
python3 $S/build_run_sheet.py --spec docs/execution_surfaces.yaml --out notebooks/<project>-run.dib \
  --project <project> --script scripts/run_pass1.sh --helpers notebooks/<project>-helpers.ps1 \
  --branch-after deals-dry --branch-step ledger-export \
  --branch-note "wizard route: materialise the import tables, then HubSpot Import from the BigQuery console" \
  --probe "bq query --use_legacy_sql=false \"select … from \\\`project.dataset.table\\\` order by 1\""
```

Pass `--branch-after/--branch-step/--probe` only when the spec has no row for the branch step yet;
when the spec already carries it (a `15b`-style row), the generator emits that row's cell and skips
the duplicate, and the probe cell is written by hand next to it.

For an **existing** run sheet edit in place, preserving its cell grammar: one table row per step
(`| n | \`step\` | LIVE \`GATE\` | what must be true afterwards |`), one mermaid edge for a branch,
one `#!markdown` + `#!pwsh` pair per step, branch cells placed between the dry step and the live
step they sit between, LIVE cells refused until `$ConfirmLive`. A compass/setup notebook, if one
exists, gets the order in one sentence and a mermaid edge, not the cells.

## 5 · Thread the pointers

The reference documents keep their roles; they point at the sequence page instead of repeating
it. Update: the runbook's top sequence line and the pass-N section (a one-line pointer, the new
step in the numbered import path, the loader statement); the index-structure step table (one
"order:" line); the runner's header comment (the step line mentions the new artefact and the
branch); the terminal/IDE guides' surface-specific paragraph (Cloud Shell verbs); the setup
notebook's counts (a rehearsal check count is a favourite stale number).

## 6 · Verify, then hand over

```bash
bash -n scripts/run_pass1.sh                                  # header comment is the only script edit
python3 -m pytest tests -q --ignore=tests/e2e                 # unchanged count
grep -rn "<old branch name>\|<old check count>" docs notebooks scripts   # stale references
python3 $S/build_surfaces_xlsx.py --spec docs/execution_surfaces.yaml --out docs/EXECUTION_SURFACES.xlsx --verify
```

Commit the set on the working branch with a message that lists the four artefacts and names
"documentation only"; push. The hand-over states, in this order: what was produced, the one
limitation if any (recalculation), what stays open awaiting consent. Never commit `.env`.

## Worked example and templates

- `references/worked-example.md` — mr-load pass 2 (2026-09): the produced files, their counts,
  the probe, the commits. Read it to calibrate depth and tone.
- `references/pattern.md` — the ingredients that made the deployment succeed; read before
  proposing any new tool, loader or guardrail.
- `assets/PASS_SEQUENCE.template.md`, `assets/EXECUTION_SURFACES.template.md` — section
  skeletons with placeholders in `{braces}`.
