#!/usr/bin/env bash
# A release payload is generic. Only the installed, site-personalized initrds
# can be current. Exit 10 requests a rebuild; other errors abort the upgrade.
set -uo pipefail
: "${1:?usage: boot-payload-release.sh <tag> <repo> <flags> <payload-dir> <kernel>}"
REPO=${2:?}; FLAGS=${3:?}; TARGET=${4:?}; KERNEL=${5:?}
DATA="${IMAGESRV_DATA_DIR:-/var/lib/imagesrv}"
ca=$(cd "$REPO" && python3 -m server.agent.tls ca --data-dir "$DATA") || exit 2
exec python3 "$REPO/deploy/boot-payload.py" decide --source "$REPO" --kernel "$KERNEL" \
    --flags "$FLAGS" --agent-ca "${ca%%$'\n'*}" --payload-dir "$TARGET" --current-only
