#!/bin/sh
# grub-lock-host.sh <repo-dir> -- #2452: the lock of the server's own GRUB
# menu (#2450) on a server that upgrades. POSIX sh.
#
# #2450 writes the lock in installer/lib/finish.sh, on a fresh install only: a
# server installed before it stayed open (ESC, then e: init=/bin/sh is root).
# tools/server-upgrade.sh runs this from the new tag's tree, after the new
# server is verified. The lock is installer/lib/grublock.sh -- sourced, not
# copied: one lock script and one verdict for the installer and the upgrade.
#
#   1. The password -- made only when there is none. One that is there is
#      never replaced: the stations' menus are locked with it (#2437), and the
#      new server may have made it already on its first start.
#   2. 01_imagesrv_lock, and GRUB_DISABLE_RECOVERY in a drop-in of its own (a
#      server from setup-boot-server.sh keeps its own timeouts).
#   3. The menu is generated to a side file and read back with the
#      installer's verdict first. A menu that is not locked the way #2450
#      means -- above all one whose entries would ask for the password to
#      boot, which would stop the server at a prompt on its next reboot --
#      never reaches /boot/grub/grub.cfg: what this run added comes out again.
#   4. update-grub, and the live grub.cfg read back (principle 5).
#
# The recovery entry of ours (41_imagesrv_recovery, or 41_imagectl_recovery
# from before the rename) stays behind the password (owner, 05/11). A server
# with none (setup-boot-server.sh) gets none from here.
#
# Out: the last line on stdout is "grub-locked: <grub.cfg>" -- the only
# success. A failure exits 1, "grub-lock-host: <code>: <why>" last on stderr.
# IMAGESRV_GRUB_ROOT (tests only): a root to work under instead of /.
set -u

[ "$#" -eq 1 ] || { echo "usage: grub-lock-host.sh <repo-dir>" >&2; exit 2; }
REPO_DIR=$1
TARGET=${IMAGESRV_GRUB_ROOT:-}
# shellcheck disable=SC2034  # both are read by installer/lib/grublock.sh, sourced below
DRY_RUN=0 GRUB_LOCK_RECOVERY_ENTRIES=any
LOCK=/etc/grub.d/01_imagesrv_lock
DEFAULTS=/etc/default/grub.d/imagesrv-lock.cfg
LIVE="$TARGET/boot/grub/grub.cfg"
SIDE="$LIVE.imagesrv-lock"
ADDED_LOCK=""
ADDED_DEFAULTS=""

fail() { echo "grub-lock-host: $1: $2" >&2; exit 1; }

[ -r "$REPO_DIR/installer/lib/grublock.sh" ] \
    || fail grub-lock-source "cannot read $REPO_DIR/installer/lib/grublock.sh"
# shellcheck source=installer/lib/grublock.sh
. "$REPO_DIR/installer/lib/grublock.sh"

# put <mode> <path> <text>: whole or not at all (a file next to it, then mv).
put() {
    mkdir -p "$TARGET${2%/*}" && printf '%s' "$3" > "$TARGET$2.new" && chmod "$1" "$TARGET$2.new" \
        && mv -f "$TARGET$2.new" "$TARGET$2" || fail grub-lock-write "cannot write $2"
}

# What this run added comes out again; a lock that was there before stays.
take_out() {
    rm -f "$SIDE" "$SIDE.new"
    [ -z "$ADDED_LOCK" ] || rm -f "$TARGET$LOCK" || echo "grub-lock-host: could not remove $LOCK" >&2
    [ -z "$ADDED_DEFAULTS" ] || rm -f "$TARGET$DEFAULTS" || echo "grub-lock-host: could not remove $DEFAULTS" >&2
}

# The installer's verdict on <grub.cfg>: nothing when locked, "<code>: <why>"
# when not. Only ever run as $(verdict ...): the fail() it defines stays there.
verdict() {
    # shellcheck disable=SC2034  # read by grub_menu_locked (installer/lib/grublock.sh)
    GRUB_LOCK_CFG=$1
    fail() { echo "$1: $2"; exit 1; }
    grub_menu_locked
}

# 1. The password. 0600 from its first byte (umask), and never over one the
# server wrote meanwhile (noclobber): that one wins.
PASSWORD="$TARGET$GRUB_PASSWORD_FILE"
if [ -e "$PASSWORD" ]; then
    echo "grub-lock-host: $GRUB_PASSWORD_FILE is there -- kept"
else
    NEW=$(new_grub_password) || fail grub-password "could not make the GRUB password from /dev/urandom"
    mkdir -p "${PASSWORD%/*}" || fail grub-password "cannot create ${GRUB_PASSWORD_FILE%/*}"
    if (umask 077; set -C; printf '%s\n' "$NEW" > "$PASSWORD"); then
        echo "grub-lock-host: made $GRUB_PASSWORD_FILE (0600)"
    elif [ ! -e "$PASSWORD" ]; then
        fail grub-password "cannot write $GRUB_PASSWORD_FILE"
    fi
fi

# 2. The lock, and no Debian single-user entries (they boot without the password).
[ -e "$TARGET$LOCK" ] || ADDED_LOCK=1
put 0755 "$LOCK" "$GRUB_LOCK_SCRIPT"
[ -e "$TARGET$DEFAULTS" ] || ADDED_DEFAULTS=1
put 0644 "$DEFAULTS" "# ImageSrv (#2452): Debian's recovery entries boot without the GRUB password.
GRUB_DISABLE_RECOVERY=true
"

# 3. The menu update-grub would write, read back before it is the live one.
rm -f "$SIDE" "$SIDE.new"
if ! grub-mkconfig -o "$SIDE"; then
    take_out
    fail grub-config "grub-mkconfig failed -- the lock was not applied"
fi
if ! WHY=$(verdict "$SIDE"); then
    take_out
    WHY=${WHY:-grub-lock-unchecked: the verdict gave no reason}
    fail "${WHY%%: *}" "${WHY#*: } -- the lock was not applied, $LIVE is not touched"
fi
rm -f "$SIDE"

# 4. The live menu.
update-grub || fail grub-config "update-grub failed (exit $?)"
if ! WHY=$(verdict "$LIVE"); then
    WHY=${WHY:-grub-lock-unchecked: the verdict gave no reason}
    fail "${WHY%%: *}" "${WHY#*: }"
fi
echo "grub-locked: $LIVE"
