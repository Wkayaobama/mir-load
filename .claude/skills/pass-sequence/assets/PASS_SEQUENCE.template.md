# Pass {N} — the sequence, on any surface ({primary surface} shown)

The ordered recipe from the working branch to {outcome of pass N}, with the place to *look* at every
stage. {Runbook} §{section} explains each step; `docs/EXECUTION_SURFACES.md` records how the
surfaces were brought to the same result. Notebook users run the same steps as
`Invoke-Step '<step>'`; terminal users type the same commands. Nothing below is specific to
{primary surface} except {the surface-specific verbs, e.g. the four `cloudshell` verbs}.

## Preconditions on the clone you run from

- Pass {N-1} is complete there: {steps} done, so `{state command}` shows {count} steps done.
- `.env` points at the **sandbox** token and portal `{portal id}` ({env clinic command}).
- {Ledger locality rule: the live steps of pass N-1 ran from this clone, or its ledger was copied here.}
- {No template placeholder in `.env`: a `<…>` value is non-empty and reaches the API; the env clinic flags it.}

## The sequence

```bash
git fetch origin && git checkout {working branch} && git pull
{script} {first step}          # {what it checks}
{script} {step}                # {what it writes: files, tables}

# ── look ──────────────────────────────────────────────────────────────────────────────────
{aligned view command}          # {what it is good for / not good for}

# ── edit (pick one) ───────────────────────────────────────────────────────────────────────
{editor command per surface}
{bulk edit one-liner}           # {exact column it touches; rows it skips}
{download / upload round trip}

{script} {next step}           # {what it persists even in dry mode; what it prints}

# ── look ──────────────────────────────────────────────────────────────────────────────────
{result file viewer}
{status command}

{script} {materialising step}  # THE MATERIALISATION: {what lands in shared truth}; prints {the console recipe}

# ── look, from the terminal ──────────────────────────────────────────────────────────────
{warehouse probe query}
```

{Facts that affect an edit: line endings, quoting, the column index a bulk edit relies on.}

## The branch point after `{materialising step}`

**{Route 1} — {the programmatic loader, one surface}.** {Which existing step does it; why it
already covers the new rows.} {Its one prerequisite and how to obtain it — a read-only call.}

```bash
{route 1 commands}
```

**{Route 2} — {no API}.** {The console/wizard path: the query, Save results, the mapping, the
order when a companion file has rows, and the bookkeeping afterwards (approve=N).}

```sql
{the export query}
```

**Why no {new tool}.** {Where the logic, idempotency and guard live; what a duplicate would fork,
which secret it would need, which data flow it would invert. The one genuinely different option
and what must be verified before relying on it.}

## What each stage produces, and where to look

| Stage | Produces | Look with |
|---|---|---|
| `{step}` | {files / tables / ledger rows} | {commands} |
| any time | position in the sequence | `{state command}` |

{What the state machine says about this order: what stays done, what its "next" hint knows.}

## Evidence — the probe of {date} ({portal}, {surface})

```
{operator's command and output, verbatim}
```

Reading it: {row count and what one row is}; {the rows the new pass changed and how}; {what is
deliberately still empty and why that is correct}; {how it ties to the rehearsal check}.

## Pitfalls

- **{Persisted state only.}** {Which dry outcomes do not appear; which view remains authoritative.}
- **{Generated files are never edited.}** {Where edits go; what overwrites the generated files.}
- **{Bookkeeping after the manual route.}**
- **{Data-quality flag seen in the probe}** and the action before production.
- **{External behaviour still to confirm once}** (e.g. a wizard's auto-match).
