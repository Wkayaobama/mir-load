#!/usr/bin/env bash
# Open an Ubuntu bash in Docker with this repo mounted at /work and gcloud
# credentials persisted in a named volume (survives container restarts).
#   scripts/ubuntu_shell.sh            # interactive shell
#   scripts/ubuntu_shell.sh <command>  # run one command, e.g. scripts/run_pass1.sh status
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Guard: a CRLF checkout (Git for Windows autocrlf) breaks every script mounted into the container.
if grep -qI $'\r' "$REPO_ROOT/scripts/run_pass1.sh" 2>/dev/null; then
  echo "CRLF line endings detected in scripts/. Fix the checkout first:" >&2
  echo "  git add --renormalize . && git rm -r -q --cached . && git reset -q --hard    # applies .gitattributes (eol=lf)" >&2
  exit 2
fi
docker image inspect mrload-ubuntu >/dev/null 2>&1 || docker build -t mrload-ubuntu -f "$REPO_ROOT/scripts/dev/Dockerfile" "$REPO_ROOT"
MOUNT_SRC="$REPO_ROOT"
case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*)
    MOUNT_SRC="$(cygpath -w "$REPO_ROOT")"      # Docker Desktop wants C:\... here
    export MSYS_NO_PATHCONV=1                   # stop Git Bash rewriting /work into C:\...\work
    ;;
esac
# If Docker answers "the input device is not a TTY" (mintty), prefix the call with: winpty
exec docker run -it --rm \
  -v "$MOUNT_SRC":/work -w /work \
  -v mrload-gcloud:/root/.config/gcloud \
  -e MRLOAD_BQ_PROJECT -e HUBSPOT_SANDBOX_TOKEN \
  mrload-ubuntu "${@:-bash}"
