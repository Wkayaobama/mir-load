#!/usr/bin/env bash
# Open an Ubuntu bash in Docker with this repo mounted at /work and gcloud
# credentials persisted in a named volume (survives container restarts).
#   scripts/ubuntu_shell.sh            # interactive shell
#   scripts/ubuntu_shell.sh <command>  # run one command, e.g. scripts/run_pass1.sh status
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
docker image inspect mrload-ubuntu >/dev/null 2>&1 || docker build -t mrload-ubuntu -f "$REPO_ROOT/scripts/dev/Dockerfile" "$REPO_ROOT"
exec docker run -it --rm \
  -v "$REPO_ROOT":/work -w /work \
  -v mrload-gcloud:/root/.config/gcloud \
  -e MRLOAD_BQ_PROJECT -e HUBSPOT_SANDBOX_TOKEN \
  mrload-ubuntu "${@:-bash}"
