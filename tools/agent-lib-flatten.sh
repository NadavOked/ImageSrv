#!/bin/bash
# Flatten source libraries for the existing initramfs runtime layout (#2239).
set -euo pipefail

fail() { echo "agent-lib-flatten: $*" >&2; exit 1; }
[ "$#" -eq 2 ] || fail "usage: $0 <src> <dst>"
src=$1
dst=$2
[ -d "$src" ] || fail "source directory missing: $src"

# Materialize find's result so a failed traversal cannot look like success.
manifest=$(mktemp)
trap 'rm -f -- "$manifest"' EXIT
find "$src" -type f -name '*.sh' -print0 > "$manifest"
mapfile -d '' -t files < "$manifest"
found=${#files[@]}
[ "$found" -gt 0 ] || fail "no shell files found in $src"

# Preflight every basename before copying anything.
declare -A names=()
for file in "${files[@]}"; do
    name=${file##*/}
    [ -z "${names[$name]+present}" ] || fail "duplicate basename: $name"
    names[$name]=$file
done
mkdir -p -- "$dst"
shopt -s nullglob
existing=("$dst"/*.sh)
[ "${#existing[@]}" -eq 0 ] || fail "destination already contains shell files: $dst"
for file in "${files[@]}"; do
    install -m 0644 -- "$file" "$dst/${file##*/}"
done

copied=("$dst"/*.sh)
[ "${#copied[@]}" -eq "$found" ] \
    || fail "copy count mismatch: found $found, copied ${#copied[@]}"
for file in "${files[@]}"; do
    target="$dst/${file##*/}"
    [ -f "$target" ] || fail "copied file missing: $target"
    cmp -s -- "$file" "$target" || fail "copied bytes differ: $target"
done
