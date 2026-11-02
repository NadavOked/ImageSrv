#!/usr/bin/env bash
# Download a release boot payload whose hashes are anchored in the signed tag.
# Exit 0 means verified payload installed/current; exit 10 means rebuild, with
# the reason on stdout. Any other exit means the decision mechanism broke.
set -uo pipefail

TAG=${1:?usage: boot-payload-release.sh <tag> <repo> <flags> <payload-dir> <kernel>}
REPO=${2:?}; FLAGS=${3:?}; TARGET=${4:?}; KERNEL=${5:?}
MANIFEST="$REPO/deploy/boot-payload.json"
PY="$REPO/deploy/boot-payload.py"
DATA="${IMAGESRV_DATA_DIR:-/var/lib/imagesrv}"
WORK="$DATA/boot-payload-release"
CURL="${IMAGESRV_CURL:-curl}"
TIMEOUT="${IMAGESRV_BOOT_PAYLOAD_DL_TIMEOUT:-1800}"

current=$(python3 "$PY" decide --source "$REPO" --kernel "$KERNEL" --flags "$FLAGS" \
    --payload-dir "$TARGET" --current-only 2>&1) && current_rc=0 || current_rc=$?
if [[ "$current_rc" == 0 ]]; then
    echo "$current"
    exit 0
elif [[ "$current_rc" != 10 ]]; then
    echo "decision error: $current"
    exit 2
fi
if [[ ! -r "$MANIFEST" ]]; then
    echo "rebuild	release has no signed boot-payload manifest"
    exit 10
fi
if [[ -n "${IMAGESRV_BOOT_PAYLOAD_URL:-}" ]]; then
    base=${IMAGESRV_BOOT_PAYLOAD_URL%/}
else
    origin=$(git -C "$REPO" remote get-url origin 2>&1) || {
        echo "rebuild	cannot read release origin: $origin"; exit 10; }
    origin=${origin%/}; origin=${origin%.git}
    if [[ ! "$origin" =~ ^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]]; then
        echo "rebuild	release origin is not a supported GitHub URL: $origin"
        exit 10
    fi
    base="$origin/releases/download/$TAG"
fi
rm -rf "$WORK" && mkdir -p "$WORK" || { echo "decision error: cannot create $WORK"; exit 2; }
trap 'rm -rf "$WORK"' EXIT
for name in initrd.img initrd.img.gui vmlinuz; do
    if ! out=$($CURL -fsSL --proto '=https,file' --proto-redir '=https' --connect-timeout 20 \
            --speed-limit 10240 --speed-time 60 --max-time "$TIMEOUT" \
            -o "$WORK/$name" "$base/$name" 2>&1); then
        echo "rebuild	release asset $name is missing or could not be downloaded: ${out:-no detail}"
        exit 10
    fi
done
python3 "$PY" decide --source "$REPO" --kernel "$KERNEL" --flags "$FLAGS" \
    --payload-dir "$TARGET" --manifest "$MANIFEST" --prebuilt-dir "$WORK"
