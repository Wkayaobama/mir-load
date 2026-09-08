# An Ubuntu terminal for mr-load in WezTerm — local or remote

Everything in this repo runs from a bash prompt on Ubuntu: `scripts/run_pass1.sh`,
dbt, `gcloud`/`bq`. Pick one of the four ways below to get that prompt inside
WezTerm, then run **one** command, `bash scripts/bootstrap_ubuntu.sh`, which
installs apt packages + Google Cloud CLI, creates the venv, runs the unit tests
and the e2e rehearsal, and leaves you at the credentials step.

| option | where the shell runs | when to prefer it |
|---|---|---|
| A. WSL2 Ubuntu | your Windows machine | daily driver on Windows; fastest |
| B. Docker Ubuntu | any OS with Docker | macOS/Linux, or a clean room on Windows |
| C. Cloud Shell | Google-hosted, preauthenticated | zero setup, interactive runs only (40 min idle cut) |
| D. GCE runner VM over IAP | your project | long runs, or later scheduling |

## 0. Wire the WezTerm module (once)

```bash
# from any bash: copy the module next to your WezTerm config
mkdir -p ~/.config/wezterm && cp wezterm/mr-load.lua ~/.config/wezterm/
```
```lua
-- ~/.config/wezterm/wezterm.lua  (Windows: %USERPROFILE%\.config\wezterm\wezterm.lua)
local wezterm = require 'wezterm'
local config = wezterm.config_builder()
require('mr-load').apply(config, {
  repo        = '/home/<you>/mir-load',   -- Linux path inside Ubuntu
  wsl_domain  = 'WSL:Ubuntu',             -- Windows only, `wsl -l` shows the name
  vm_ssh_host = 'mrload-vm',              -- option D
})
return config
```
You get: **Ctrl+Shift+L** launcher with the `mr-load ▸ …` entries (local shell,
bootstrap, status pane, Docker, Cloud Shell, VM), **Ctrl+Shift+S** to split a
live status pane to the right, and on Windows every new tab opens in WSL Ubuntu.

## Windows checkouts: line endings

Git for Windows defaults to `core.autocrlf=true`, which rewrites every text
file to CRLF on checkout; a bash script then dies on `set -euo pipefail\r`.
The repo now ships a `.gitattributes` that pins `eol=lf` for scripts and all
parsed text, so **fresh clones are correct on any machine**. An existing clone
made before it must be re-checked-out once, from Git Bash inside the repo:

```bash
git pull
git add --renormalize . && git rm -r -q --cached . && git reset -q --hard   # re-apply .gitattributes
git ls-files -z | xargs -0 grep -lI $'\r' || echo "clean"                    # must print: clean
```
`scripts/ubuntu_shell.sh` refuses to start the container while `scripts/` is
CRLF, and the Dockerfile strips `\r` from the bootstrap before running it, so
the failure you would otherwise get inside Docker is caught on the host side.

## A. WSL2 Ubuntu (Windows)

```powershell
wsl --install -d Ubuntu-24.04          # once, then reboot; `wsl -l -v` must show it as VERSION 2
```
Open WezTerm → the default domain is now WSL → in that bash:
```bash
git clone https://github.com/Wkayaobama/mir-load.git ~/mir-load && cd ~/mir-load
git checkout claude/mr-load-library-system-dphpbj
bash scripts/bootstrap_ubuntu.sh
```
Keep the clone on the Linux side (`~/mir-load`, not `/mnt/c/...`): `.mrload/`
holds the ledger and cache and WSL is 10× slower on NTFS paths.

## B. Docker Ubuntu (macOS, Linux, Windows)

```bash
git clone https://github.com/Wkayaobama/mir-load.git && cd mir-load
git checkout claude/mr-load-library-system-dphpbj
scripts/ubuntu_shell.sh                # builds scripts/dev/Dockerfile once, mounts the repo at /work
# inside the container:
bash scripts/bootstrap_ubuntu.sh       # system part is already baked in; creates venv, runs tests + rehearsal
gcloud auth login --no-launch-browser  # credentials persist in the `mrload-gcloud` volume
```

## C. Cloud Shell (remote, zero setup)

```bash
gcloud cloud-shell ssh --authorize-session     # or the launcher entry
git clone https://github.com/Wkayaobama/mir-load.git && cd mir-load
git checkout claude/mr-load-library-system-dphpbj
bash scripts/bootstrap_ubuntu.sh               # gcloud/bq already there, only the venv is created
```
Cloud Shell's `$HOME` persists (5 GB), the VM does not: rerun the bootstrap
after a long absence. Sessions stop after 40–60 min idle, so this is for
interactive runs, not scheduled ones.

## D. GCE runner VM over IAP (remote; no port 22 exposed)

```bash
PROJECT=<project>; ZONE=europe-west6-a
gcloud compute instances create mr-load-runner-vm --project=$PROJECT --zone=$ZONE \
  --machine-type=e2-small --image-family=ubuntu-2404-lts-amd64 --image-project=ubuntu-os-cloud \
  --scopes=cloud-platform --metadata=enable-oslogin=TRUE
gcloud compute firewall-rules create allow-iap-ssh --project=$PROJECT \
  --direction=INGRESS --action=ALLOW --rules=tcp:22 --source-ranges=35.235.240.0/20
gcloud compute ssh mr-load-runner-vm --zone=$ZONE --tunnel-through-iap --dry-run   # prints the exact ssh line
```
Turn the printed line into a `~/.ssh/config` entry so plain `ssh mrload-vm`
(and the WezTerm launcher / `wezterm connect mrload-vm`) works:
```
Host mrload-vm
  HostName mr-load-runner-vm
  User <the user shown by --dry-run>
  IdentityFile ~/.ssh/google_compute_engine
  ProxyCommand gcloud compute start-iap-tunnel %h 22 --listen-on-stdin --zone=<ZONE> --project=<PROJECT>
```
On the VM: `git clone … && bash scripts/bootstrap_ubuntu.sh`. BigQuery works
through the attached service account; for Drive either run
`gcloud auth application-default login --no-launch-browser --scopes=…drive.readonly`
as yourself, or share the scope root with the VM's service account.

## After the bootstrap (all options)

```bash
source .venv/bin/activate
gcloud init
gcloud auth application-default login --no-launch-browser \
  --scopes=https://www.googleapis.com/auth/drive.readonly,https://www.googleapis.com/auth/cloud-platform
$EDITOR .env                                    # MRLOAD_BQ_PROJECT, HUBSPOT_SANDBOX_TOKEN (sandbox first)
scripts/run_pass1.sh preflight                  # then the sequence in docs/RUNBOOK_PASS1.md
```
`--no-launch-browser` prints a URL to open on your own browser; paste the code
back. Second pane (Ctrl+Shift+S) shows the ledger counts and the latest step
log while the first pane runs the steps.
