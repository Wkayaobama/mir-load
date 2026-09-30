#!/usr/bin/env python3
"""Polyglot run-sheet generator: the sequential motion as a .dib notebook, from the surfaces spec.

    python3 build_run_sheet.py --spec docs/execution_surfaces.yaml --out notebooks/<project>-run.dib \
        --project mr-load --script scripts/run_pass1.sh --helpers notebooks/mr-load-helpers.ps1 \
        [--branch-after deals-dry --branch-step ledger-export --branch-note "wizard route …" --probe "bq query …"]

Emits the cell pattern that made the run sheet work: one steps table (# · step · gate · what must be true
afterwards), a mermaid flow with LIVE markers, a Switches + helpers cell ($ConfirmLive gates the live cells),
a where-am-I cell (Get-PipelineState), one `Invoke-Step '<step>'` cell per runnable step, optional branch cells
after a step (the pass-N wizard route: a materialising step plus a probe), and the streamlined patterns
(Invoke-NextStep, Invoke-Sequence -UntilLive / -Through). A runnable step is a spec row whose `step` label
starts with a bare sub-command (^[a-z][a-z0-9-]*$ as its first token: "deals-dry", "ledger-export (again)",
"status / where am I"); rollback rows and probe rows appear in the table only.

The helpers file is project-specific (Invoke-Ubuntu, Invoke-Step, Get-PipelineState, Invoke-NextStep,
Invoke-Sequence); this generator only assumes those five names. Review the output: it is a skeleton to keep,
not a document to publish unread.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

RUNNABLE = re.compile(r"^[a-z][a-z0-9-]*$")
META = {"kernelInfo": {"defaultKernelName": "pwsh", "items": [
    {"aliases": [], "languageName": "PowerShell", "name": "pwsh"},
    {"aliases": [], "languageName": "Mermaid", "name": "mermaid"}]}}


def _cmd(label: str) -> str | None:
    """First token of the step label when it is a runner sub-command; None for probe / rollback rows."""
    tok = str(label).split()[0] if str(label).split() else ""
    if not RUNNABLE.match(tok) or "rollback" in str(label).lower():
        return None
    return tok


def cells(spec, a) -> list[str]:
    steps = spec["steps"]
    runnable = [dict(s, cmd=_cmd(s["step"])) for s in steps if _cmd(s["step"])]

    def branch_here(i: int) -> bool:
        """Branch cells go after runnable[i] unless the spec's next row already IS the branch step (no duplicate)."""
        s = runnable[i]
        if not (a.branch_after and a.branch_step and s["cmd"] == a.branch_after):
            return False
        nxt = runnable[i + 1]["cmd"] if i + 1 < len(runnable) else None
        return nxt != a.branch_step
    out = ["#!meta\n\n" + json.dumps(META)]
    md = lambda t: out.append("#!markdown\n\n" + t.rstrip("\n"))
    ps = lambda t: out.append("#!pwsh\n\n" + t.rstrip("\n"))

    table = ["| # | step | gate | what must be true afterwards |", "|---|---|---|---|"]
    for s in steps:
        gate = f"LIVE `{s['gate'].replace('LIVE ', '')}`" if str(s.get("gate") or "").startswith("LIVE") else ""
        table.append(f"| {s['n']} | `{s['step']}` | {gate} | {s.get('artefact', '')} |")
    md(f"""# {a.project} run sheet — the sequence, nothing else

Answers three questions every time you sit down: **where am I, what is next, run it.** One cell per step, in
the only order that is valid. Every step is idempotent (`{a.script}`), records a checkpoint, and the state cell
reads those checkpoints back.

{chr(10).join(table)}

**Rules the sequence enforces**
- A step counts as done only if its last successful run is newer than every step before it. Re-running an
  early step makes the later ones pending again. That is intended.
- A LIVE step first re-runs its dry counterpart, then fires. A LIVE cell is refused until `$ConfirmLive = $true`;
  with it set, `YES_ALL=1` answers the runner's prompts, so the flag is your confirmation.
- Anything that stops the run is recorded as `failed` and stays the next step until it passes.""")

    # mermaid
    nodes, edges, prev = [], [], None
    for i, s in enumerate(runnable):
        nid = "s" + re.sub(r"[^a-z0-9]", "", str(s["n"]) + s["cmd"])
        live = " ⟨LIVE⟩" if str(s.get("gate") or "").startswith("LIVE") else ""
        nodes.append(f'{nid}["{s["n"]} {s["step"]}{live}"]')
        if prev:
            edges.append(f"    {prev} --> {nid}")
        prev = nid
        if branch_here(i):
            edges.append(f'    {nid} --> {nid}b["{a.branch_step} ⟨LIVE⟩\\n{a.branch_note or "branch"}"]')
    out.append("#!mermaid\n\nflowchart LR\n    " + "\n    ".join(nodes) + "\n" + "\n".join(edges))

    md("## 0 · Switches + helpers — run first, every time\n\n`$ConfirmLive` stays `$false` until you decide to fire a LIVE step.")
    ps(f"""# ── switches ────────────────────────────────────────────────────────────────
$Route       = 'wsl'                                   # 'wsl' | 'devcontainer' | 'linux'
$Distro      = 'Ubuntu-24.04'
$RepoWsl     = '~/{a.project}'
$RepoWin     = "$HOME\\{a.project}"
$RepoLinux   = '/workspaces/{a.project}'
$ConfirmLive = $false                                  # $true = LIVE steps fire, prompts answered

# ── load {a.helpers} ──────────────────────────────────
$helpersName = '{Path(a.helpers).name}'
$candidates  = @((Join-Path $RepoWin (Join-Path '{Path(a.helpers).parent.as_posix()}' $helpersName)),
                 (Join-Path $PWD $helpersName), (Join-Path $PWD (Join-Path '{Path(a.helpers).parent.as_posix()}' $helpersName)),
                 (Join-Path $RepoLinux '{a.helpers}'))
if ($Route -eq 'wsl' -and (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {{
    $env:WSL_UTF8 = '1'
    $linuxHome  = (wsl.exe -d $Distro -e bash -lc 'echo $HOME' | Select-Object -First 1).Trim()
    $candidates += "\\\\wsl$\\$Distro" + ((($RepoWsl -replace '^~', $linuxHome) -replace '/', '\\')) + '\\{a.helpers.replace("/", chr(92))}'
}}
$helpers = $candidates | Where-Object {{ $_ -and (Test-Path $_) }} | Select-Object -First 1
if (-not $helpers) {{ throw "$helpersName not found. Looked in: $($candidates -join ' | ')." }}
Invoke-Expression (Get-Content -Raw $helpers)
"helpers: $helpers\"""")

    md("## 1 · Where am I\n\nReads the checkpoints and names the next step.")
    ps("Get-PipelineState")

    md("## 2 · The steps, one cell each")
    for i, s in enumerate(runnable):
        gate = str(s.get("gate") or "")
        title = f"### {s['n']} · {s['step']}" + (f" — LIVE `{gate.replace('LIVE ', '')}`" if gate.startswith("LIVE") else "")
        md(f"{title}\n\n{s.get('artefact', '')}")
        ps(f"Invoke-Step '{s['cmd']}'")
        if branch_here(i):
            note = (a.branch_note or "").strip()
            note = note + ("" if not note or note.endswith((".", "!", "?")) else ".")
            md(f"**Branch (optional).** {note} Skip these cells to continue with the next step.")
            ps(f"Invoke-Step '{a.branch_step}'      # branch: LIVE gate, refused until $ConfirmLive")
            if a.probe:
                ps(f"Invoke-Ubuntu '{a.probe}'")

    numbered = [s for s in runnable if isinstance(s["n"], int) and s["cmd"] != "status"]
    last = (numbered or runnable)[-1]["cmd"] if runnable else ""
    md("""## 3 · Streamlined patterns

**Next only.** One cell, run it again and again: it looks up the next pending step and runs exactly that one.""")
    ps("Invoke-NextStep")
    md("**Everything dry, then decide.** Runs every pending step in order and stops in front of the first LIVE gate.")
    ps("Invoke-Sequence -UntilLive")
    md(f"**Through a step.** With `$ConfirmLive = $true`, runs every pending step up to and including the named one.")
    ps(f"Invoke-Sequence -Through '{last}'")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--spec", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--script", default="scripts/run_pass1.sh")
    ap.add_argument("--helpers", default="notebooks/helpers.ps1", help="repo-relative path of the PowerShell helpers")
    ap.add_argument("--branch-after", help="step after which the optional branch cells are inserted")
    ap.add_argument("--branch-step", help="the branch's runner step (e.g. ledger-export)")
    ap.add_argument("--branch-note", help="one sentence shown above the branch cells")
    ap.add_argument("--probe", help="a bash command run through Invoke-Ubuntu after the branch step")
    a = ap.parse_args(argv)
    spec = yaml.safe_load(Path(a.spec).read_text(encoding="utf-8"))
    text = "\n\n".join(cells(spec, a)) + "\n"
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(text, encoding="utf-8")
    n = text.count("#!pwsh")
    print(f"wrote {a.out}: {n} pwsh cells, {text.count('#!markdown')} markdown cells")
    return 0


if __name__ == "__main__":
    sys.exit(main())
