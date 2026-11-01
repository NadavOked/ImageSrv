#!/bin/sh
# Shared initramfs compression selection and inspection. POSIX sh.

initramfs_compressor() {
    # $1 = target kernel version, $2 = config root (default /boot).
    _if_kver=$1; _if_boot=${2:-/boot}; _if_cfg="$_if_boot/config-$_if_kver"
    if [ ! -r "$_if_cfg" ]; then
        echo "initramfs compression: gzip (cannot read $_if_cfg; target zstd support is unknown)" >&2
        printf 'gzip\n'
    elif grep -qx 'CONFIG_RD_ZSTD=y' "$_if_cfg"; then
        echo "initramfs compression: zstd -19 -T0 ($_if_cfg has CONFIG_RD_ZSTD=y)" >&2
        printf 'zstd\n'
    else
        echo "initramfs compression: gzip ($_if_cfg does not enable CONFIG_RD_ZSTD=y)" >&2
        printf 'gzip\n'
    fi
}

initramfs_format() {
    # A failed read or unknown magic is an error, never an uncompressed guess.
    _if_magic=$(od -A n -t x1 -N 4 "$1" 2>/dev/null | tr -d ' \n') || return 1
    case "$_if_magic" in
        1f8b*) printf 'gzip\n' ;;
        28b52ffd*) printf 'zstd\n' ;;
        *) echo "initramfs: unknown compression magic ${_if_magic:-<unreadable>} in $1" >&2; return 1 ;;
    esac
}

initramfs_decompress() {
    _if_fmt=$(initramfs_format "$1") || return 1
    case "$_if_fmt" in
        gzip) gzip -dc "$1" ;;
        zstd) zstd -dc "$1" ;;
        *) echo "initramfs: internal format error: $_if_fmt" >&2; return 1 ;;
    esac
}
