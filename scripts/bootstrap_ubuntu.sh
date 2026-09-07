#!/usr/bin/env bash
# =============================================================================
# mr-load — idempotent Ubuntu bootstrap (22.04 / 24.04).
# Works the same in WSL2, a Docker container, a GCE VM and Cloud Shell.
#
#   bash scripts/bootstrap_ubuntu.sh               # system deps + gcloud + venv + tests + rehearsal
#   bash scripts/bootstrap_ubuntu.sh --system-only # apt + gcloud only (used by the Dockerfile)
#   bash scripts/bootstrap_ubuntu.sh --no-rehearsal
#
# Run from anywhere: if not inside a clone it clones the repo into ~/mir-load
# and checks out the working branch. Safe to re-run.
# =============================================================================
set -euo pipefail
BRANCH="${MRLOAD_BRANCH:-claude/mr-load-library-system-dphpbj}"
REMOTE="${MRLOAD_REMOTE:-https://github.com/Wkayaobama/mir-load.git}"
SYSTEM_ONLY=0; REHEARSAL=1
for a in "$@"; do case "$a" in --system-only) SYSTEM_ONLY=1;; --no-rehearsal) REHEARSAL=0;; esac; done

SUDO=""; [[ $EUID -ne 0 ]] && SUDO="sudo"
export DEBIAN_FRONTEND=noninteractive
say() { printf '\n\033[1;36m▶ %s\033[0m\n' "$*"; }
ok()  { printf '\033[1;32m✔ %s\033[0m\n' "$*"; }

# ── where am I ────────────────────────────────────────────────────────────────
ENV_KIND="ubuntu"
grep -qi microsoft /proc/version 2>/dev/null && ENV_KIND="wsl"
[[ -n "${CLOUD_SHELL:-}" || -n "${DEVSHELL_PROJECT_ID:-}" ]] && ENV_KIND="cloudshell"
[[ -f /.dockerenv ]] && ENV_KIND="docker"
curl -sf -m 2 -H "Metadata-Flavor: Google" http://169.254.169.254/computeMetadata/v1/instance/id >/dev/null 2>&1 && ENV_KIND="gce"
say "environment: $ENV_KIND"

# ── system packages ───────────────────────────────────────────────────────────
say "apt packages"
$SUDO apt-get update -qq
$SUDO apt-get install -y -qq --no-install-recommends \
  python3 python3-venv python3-pip git curl ca-certificates gnupg sqlite3 less procps >/dev/null
ok "python3 $(python3 --version | cut -d' ' -f2), git, curl, sqlite3"

# ── gcloud + bq (apt repo; Cloud Shell already has them) ──────────────────────
if command -v gcloud >/dev/null 2>&1 && command -v bq >/dev/null 2>&1; then
  ok "gcloud/bq present: $(gcloud --version 2>/dev/null | head -1)"
else
  say "google-cloud-cli"
  curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg \
    | $SUDO gpg --dearmor --yes -o /usr/share/keyrings/cloud.google.gpg
  echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" \
    | $SUDO tee /etc/apt/sources.list.d/google-cloud-sdk.list >/dev/null
  $SUDO apt-get update -qq && $SUDO apt-get install -y -qq google-cloud-cli >/dev/null
  ok "gcloud/bq installed"
fi
[[ $SYSTEM_ONLY -eq 1 ]] && { ok "system-only bootstrap done"; exit 0; }

# ── repository ────────────────────────────────────────────────────────────────
if git rev-parse --show-toplevel >/dev/null 2>&1 && [[ -f "$(git rev-parse --show-toplevel)/context/cards/library.yaml" ]]; then
  REPO_ROOT="$(git rev-parse --show-toplevel)"
else
  REPO_ROOT="$HOME/mir-load"
  [[ -d "$REPO_ROOT/.git" ]] || git clone -q "$REMOTE" "$REPO_ROOT"
fi
cd "$REPO_ROOT"
git fetch -q origin "$BRANCH" && git checkout -q "$BRANCH" && git pull -q --ff-only origin "$BRANCH" || true
ok "repo $REPO_ROOT @ $(git rev-parse --short HEAD) ($BRANCH)"

# ── python env ────────────────────────────────────────────────────────────────
say "virtualenv + requirements-dev"
[[ -d .venv ]] || python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q --upgrade pip
pip install -q -r requirements-dev.txt
ok "venv ready: dbt $(dbt --version 2>/dev/null | grep -oE 'installed:\s*\S+' | head -1 | awk '{print $2}')"

# ── proof on this machine (no credentials needed) ────────────────────────────
say "unit tests"
python -m pytest tests -q --ignore=tests/e2e
if [[ $REHEARSAL -eq 1 ]]; then
  say "e2e rehearsal (real code, local mocks, ≈30 s)"
  scripts/e2e_rehearsal.sh | tail -3
fi

# ── operator config ───────────────────────────────────────────────────────────
if [[ ! -f ../.env.mrload && ! -f .env ]]; then
  cp .env.mrload.example .env
  ok "created .env from the template — fill MRLOAD_BQ_PROJECT and HUBSPOT_SANDBOX_TOKEN"
fi

cat <<EOF

Next, in this same shell:
  source .venv/bin/activate
  gcloud init                                   # choose the BigQuery project
  gcloud auth application-default login --no-launch-browser \\
    --scopes=https://www.googleapis.com/auth/drive.readonly,https://www.googleapis.com/auth/cloud-platform
  \$EDITOR .env                                  # MRLOAD_BQ_PROJECT, HUBSPOT_SANDBOX_TOKEN
  scripts/run_pass1.sh preflight
EOF
