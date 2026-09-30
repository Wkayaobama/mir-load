# Execution surfaces — how the {n} layers were reconciled (record)

Companion to `docs/EXECUTION_SURFACES.xlsx` (Overview, Layers L0–L{k}, Pipeline steps, Remediation
R01–R{m}, Equality checks). This page is the narrative: what diverged between the surfaces, how each
divergence was observed, what closed it, and the probe that ended the reconciliation on {date}. The
pass recipe itself is `docs/PASS{N}_SEQUENCE.md`; the step reference is `{runbook}`.

## 1. The motions and the vocabulary

| Motion | Acquire | Runtime | Driver | State lives in |
|---|---|---|---|---|
| **A** · {notebook motion} | | | | |
| **B** · {terminal motion} | | | | |
| **C** · {remote shell motion} | | | | |

Parity of a layer or a step is one of three words. **Parallel**: same mechanism, same result on
every surface. **Conditional**: same result when a stated condition holds (same commit, same
identity, same clone for the ledger). **Divergent**: a different mechanism, with a remediation
that makes it equivalent.

## 2. The principle that made reconciliation possible

{One script is the execution surface; gates inline; shared truth = …; local = …; every
reconciliation either moved a read to shared truth or stated the condition for trusting a local
artefact.}

## 3. Timeline — divergence, observation, fix

| When | Divergence | Observed how | Closed by |
|---|---|---|---|
| {date} | {what differed between surfaces} | {log line / count / symptom, and where it was seen} | {commit hash + one line; or "**not implemented, awaiting consent**"; or a rule and where it is written} |

(Append-only. Never rewrite a past row; add a new one when the situation changes.)

## 4. Pass {N} on each surface, after the reconciliation

| Operation | A | B | C | Parity |
|---|---|---|---|---|
| View {the decision file} | | | | |
| Edit it | | | | Conditional: per clone |
| Bulk approve | | | | |
| Run the steps | `Invoke-Step` | `{script} <step>` | same | Parallel / Conditional (ledger) |
| Probe {shared truth} | | | | Parallel |
| {Manual-route file} | | | | |

{One sentence on what stayed local on purpose and what moved to shared truth.}

## 5. The probe that closed the reconciliation ({date}, {portal}, {surface})

```
{command and output, verbatim}
```

What it proves, surface by surface: {bullets}.

## 6. What stays open (none of it blocks pass {N})

- {proposal} — **not implemented, awaiting consent**.
- {data-quality flag} — action before production.
- {prerequisite for a route}.
- {structural improvement not built} — would remove {which Conditional}.
- {external behaviour to verify once}.

## 7. Keeping the parity

After each stage compare the artefact the *Equality checks* sheet names: {list}. Two surfaces on
the same commit, identity and ledger produce the same numbers; any difference points at one of the
{k+1} layers.
