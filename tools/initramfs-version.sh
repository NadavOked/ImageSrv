#!/bin/sh
# Sourceable build helper. Resolve provenance without inventing a release.
# $1 source tree, $2 explicit version, $3 ISO manifest, $4 initrd root.
stamp_initramfs_version() {
    _iv_version=$2
    if [ -z "$_iv_version" ] && [ -e "$1/.git" ]; then
        _iv_version=$(git -C "$1" describe --tags --always) || _iv_version=""
    elif [ -z "$_iv_version" ] && [ -f "$3" ]; then
        _iv_version=$(jq -er '.imagesrv_tag | select(type == "string" and length > 0)' "$3") || _iv_version=""
    fi
    case "$_iv_version" in
        ''|*[!a-zA-Z0-9._+-]*) _iv_version=unknown ;;
    esac
    [ "${#_iv_version}" -le 128 ] || _iv_version=unknown
    [ "$_iv_version" != unknown ] || printf 'initramfs: source version is unknown\n' >&2
    mkdir -p "$4/etc/imagesrv" || return 1
    printf '%s\n' "$_iv_version" > "$4/etc/imagesrv/agent-version"
}
