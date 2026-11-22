#!/bin/sh
# Integration test: feed to sh inside a booted test initramfs (real LVM2).
# dumpconfig reads the effective configuration without scanning a PV.
set -eu
check_setting() {
    value=$(/usr/bin/lvm dumpconfig --typeconfig current "$1") || {
        rc=$?
        printf 'cannot read %s from packed LVM (rc=%s)\n' "$1" "$rc" >&2
        exit "$rc"
    }
    [ "$value" = "${1##*/}=$2" ] || {
        printf 'packed LVM setting %s: expected %s, got %s\n' "$1" "$2" "$value" >&2
        exit 1
    }
}
check_setting devices/obtain_device_list_from_udev 0
check_setting devices/external_device_info_source '"none"'
check_setting devices/use_devicesfile 0
check_setting activation/udev_sync 0
check_setting activation/udev_rules 0
check_setting activation/monitoring 0
