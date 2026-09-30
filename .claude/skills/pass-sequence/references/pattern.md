# The pattern — ingredients of a deployment that reached parity across three surfaces

Condensed from mr-load (Drive library → BigQuery/dbt → HubSpot, passes 1 and 2, September 2026).
Each ingredient is stated with the failure it prevents, because that is how to recognise when one
is missing in a new project.

## Execution

1. **One script is the execution surface.** Every notebook cell, terminal line and Cloud Shell
   line runs `scripts/run_pass1.sh <step>`. Parity is then a property of the script, not of the
   operator. Failure it prevents: three slightly different procedures that drift apart.
2. **Gates opened inline, per live step, after a dry run and a YES.** A gate variable is set for
   one command (`MRLOAD_APPROVE_X=1 runner …`), never exported globally. When the gates were put in
   `.env` on Cloud Shell, "dry" steps went live; the operator accepted it, but the rule stands and
   the incident is in the record. A notebook substitutes `$ConfirmLive` for the YES.
3. **Live steps checkpoint their dry counterpart first.** The dry output is the plan; the operator
   reads it before the live run, and the checkpoints file shows both.
4. **Checkpoints read back by a state machine.** `pipeline_state.sh`: done / stale / failed /
   pending, staleness by order (re-running `walk` makes everything after it pending again), a
   "next" hint. It turns "which step am I on?" from memory into a file. Its limits are documented
   (it knows one route through a branch point), not patched to fit the docs.
5. **A local ledger makes live steps idempotent — and is the one Conditional.** Uploads, notes and
   deals are keyed in SQLite on the clone that ran them. Rule: live HubSpot steps from one surface,
   or copy the ledger before switching. Naming this openly is what keeps the other layers Parallel.
6. **Shared truth versus local state, decided per artefact.** Anything another system or surface
   must *read* moves to shared truth (the import files became `mrload_raw.hubspot_*_import`);
   the operator's approval record stays local by design (`deal_decisions.csv`, edited where the
   pass runs). Moving approvals to the warehouse would have inverted the data flow.
7. **Strict pass boundaries.** Pass 2 never changes pass 1: companies salvaged in pass 2 are
   visible to pass 2 only; attach scope unchanged. Enrich, never break what works.
8. **Enrich, never create, when reconciling with an external system.** The salvage searches by
   name and records what the portal *is* (`matched_by_name_pass2`, `not_in_portal_pass2`); the
   missing ones go to an import file for a human. Creates stay behind the gate.

## Evidence

9. **A rehearsal harness with numbered checks is the regression net.** Real code against mocks
   (Drive, HubSpot), a `bq` stub that validates schema positionally and by type, dbt on DuckDB;
   every feature adds checks and a test pins the count (48 → 50). A feature without a check is not
   finished.
10. **The probe closes a reconciliation.** A real query from the least-equipped surface (Cloud
    Shell) pasted verbatim into the record, with date and portal, and read column by column: what
    it proves, what it does not. Invented numbers would defeat the purpose.
11. **Equality checks after each stage.** Node count after walk, `numRows` after load, model and
    test counts after dbt, ledger statuses after the live steps, the table query after the
    materialisation. Two surfaces on the same commit, identity and ledger give the same numbers.

## Operator experience

12. **Look and edit affordances are part of the sequence.** A step that writes a CSV is followed
    by the command that shows it and the command that edits it, per surface (`column | less`,
    `cloudshell edit`, `nano`, download/upload round trip). The gap operators hit first on Cloud
    Shell was exactly this, not a missing step.
13. **Branch points are documented, not tooled away.** Wizard import versus API loader: both
    routes written down with their one prerequisite each. The temptation to add a Cloud Function
    reading the warehouse was declined because it would fork the loader logic, need its own
    secret, lose the ledger's idempotency and invert the data flow.
14. **The sandbox is the only target until told otherwise.** Portal id checked in `preflight`
    (`MRLOAD_ALLOW_PROD_PORTAL` to override, `HUBSPOT_SANDBOX_PORTAL_ID` to pin); all live work in
    the session went to the sandbox, which is why a wrong stage id cost nothing.

## Documentation

15. **Five documents with distinct roles.** Sequence page = recipe; runbook = reference per
    step; surfaces record = history; workbook = audit with live parity counts; run sheet = motion.
    Pointers between them, no duplicated procedure. Produced in that order because each feeds
    the next.
16. **Docs-only changes touch no code.** A usage comment at most. Adapting the pipeline to the
    documentation is a smell; adapting the documentation to a probe is the job.
17. **Commits are cited.** Every "closed by" in the timeline names a hash (`d4d5dfb`,
    `953f6f4`, `6f38600`, `765cd01`). The record is checkable with `git show`.
18. **Side branch per feature, fast-forward into the working branch when the operator says
    "merge".** The record notes the merge date.

## Safety and consent

19. **Secrets never reach git.** `.env` ignored, a pre-commit guard installed by a script, a
    proof script that shows the refusal.
20. **Guardrails that could impede the operator's flow are proposed, not implemented, until
    explicit consent.** The record carries "not implemented, awaiting consent" as a first-class
    status; the proposals do not vanish and are not smuggled in.
