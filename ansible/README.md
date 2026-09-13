# ansible/ — removed 2026-09-07, logic preserved here

The playbook was removed because, for this pipeline, it added nothing:

- it ran on `localhost` only and every task was a `command` shelling out to the
  same `python -m pipeline.library_files.runner …` and `dbt …` calls that
  `scripts/run_pass1.sh` runs;
- everything that makes the pipeline safe lives below the orchestrator —
  idempotency and crash recovery in the SQLite ledger, the cardinality gate in
  dbt tests, the write gates in env vars read by the runner;
- Ansible's Google collection has no BigQuery load-job module, so BigQuery
  steps were `command: bq load` either way;
- the shell script had already overtaken it (preflight probes, `bq-init`,
  dry → count → `YES` → live prompts, per-step logs, `status`, `unmigrate`).

Single execution surface today: **`scripts/run_pass1.sh <step> [--yes]`**
(steps documented in `docs/RUNBOOK_PASS1.md`).

## When to bring it back

Exactly one of these, nothing less:

1. a **second host** — e.g. a scheduled GCE runner VM alongside the laptop /
   Cloud Shell seat — so an inventory and a remote connection actually exist;
2. **AWX / scheduling** — the run must be triggered, logged and permissioned
   outside a human shell session.

## The core logic to resurrect (do not re-implement the steps)

Wrap the script, one task per step, so the playbook is the *ordering* and the
script remains the *behaviour*:

```yaml
# ansible/playbook.yml (skeleton)
- name: mr-load library pass 1
  hosts: runners                # inventory: [runners] with the VM(s); localhost for a laptop seat
  gather_facts: false
  vars_files: [group_vars/all.yml]
  environment:                  # secrets come from the host's .env.mrload, never from vars
    MRLOAD_LEDGER_PATH: "{{ ledger_path }}"
  tasks:
    - name: "{{ item }}"
      command: "scripts/run_pass1.sh {{ item }} --yes"
      args: { chdir: "{{ repo_root }}" }
      loop: "{{ steps }}"
      register: out
      failed_when: out.rc not in [0, 1]     # 1 = business outcome (ambiguous match, partial), 2+ = error
```

```yaml
# ansible/group_vars/all.yml (skeleton)
repo_root: /opt/mr-load
ledger_path: /opt/mr-load/.mrload/ledger.sqlite
steps:                          # the DAG order; the join point is dbt before companies-live
  - preflight
  - walk
  - bq-init
  - bq-load
  - dbt
  - review
  - companies-dry
  - companies-live
  - attach-dry
  - attach-upload
  - attach-notes
  - ledger-export
# pass 2 is operator-driven (deal_decisions.csv) and stays out of the unattended loop:
#  - deals-dry
#  - deals-live
```

```ini
# ansible/inventory.ini (skeleton)
[runners]
mr-load-runner-vm ansible_host=<internal-ip> ansible_user=<user>
# laptop / Cloud Shell seat:
# localhost ansible_connection=local
```

Rules carried over from the removed playbook:

- gates are **never** set globally; `run_pass1.sh` opens each one inline for
  its own step and `--yes` replaces the interactive `YES`;
- `rc == 1` is a business outcome to read in the log, not a failure of the play;
- the ledger file is per target portal — a sandbox → prod swap needs a fresh
  `MRLOAD_LEDGER_PATH`;
- the original task list (walk → bq-load → dbt deps/run/test → companies →
  attach → review-export → ledger-export → dbt build → deals) is now the
  `steps` list above, in the corrected order (review before companies-live).
