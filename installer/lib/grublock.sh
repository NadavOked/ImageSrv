# grublock.sh -- the server's own GRUB menu is locked (#2450). POSIX sh.
# shellcheck disable=SC2034,SC2154 # sourced module consumes installer state
#
# Without `superusers`, GRUB leaves e (edit an entry) and c (its command line)
# open to whoever stands at the server: init=/bin/sh on the kernel line is a
# root shell, with SSH closed in production. ESC opens the hidden menu
# (finish.sh: GRUB_TIMEOUT_STYLE=hidden, 3s), so hidden is not locked.
#
# The password is the one the stations' menus are locked with (#2437): one
# random line in /etc/imagesrv/grub-password, root-only, made here before the
# first update-grub. server/system/grub_password.py reads this same file on
# its first start and keeps it; an admin sees it in the console (settings,
# "סיסמת GRUB"). Only its hash reaches grub.cfg.
#
# What asks for it: e, c, and the recovery entry (41_imagesrv_recovery, no
# --unrestricted -- owner's decision, 04/11). What does not: every entry
# grub-mkconfig writes (10_linux and the rest), so a normal boot and a kernel
# upgrade never stop at a password prompt.

GRUB_LOCK_USER=imagesrv                        # boot/grub_lock.py GRUB_USER
GRUB_PASSWORD_FILE=/etc/imagesrv/grub-password # server/system/grub_password.py PASSWORD_FILE
GRUB_PASSWORD_ALPHABET=abcdefghjkmnpqrstuvwxyz23456789   # grub_password._ALPHABET

# Four groups of five, the shape grub_password.new_password() makes (about
# 2^99). tr -dc keeps only the alphabet's bytes of /dev/urandom, so each one
# is uniform over the 31.
new_grub_password() {
    _np=$(LC_ALL=C tr -dc "$GRUB_PASSWORD_ALPHABET" < /dev/urandom | head -c 20) || return 1
    [ "${#_np}" -eq 20 ] || return 1
    printf '%s\n' "$_np" | sed 's/^\(.....\)\(.....\)\(.....\)\(.....\)$/\1-\2-\3-\4/'
}

write_grub_password() {
    if [ "$DRY_RUN" -eq 1 ]; then _wg='<generated>'
    else _wg=$(new_grub_password) || fail grub-password "could not make the GRUB password from /dev/urandom"; fi
    write_target 0600 "$GRUB_PASSWORD_FILE" "$_wg
"
}

# /etc/grub.d/01_imagesrv_lock: after 00_header, before 10_linux, on every
# update-grub (kernel upgrades included). Nothing of Debian's is edited:
# 10_linux is a dpkg conffile, and an edited one either stops an upgrade at a
# conffile question or is replaced without --unrestricted -- and then the
# server would stop at a password prompt on every boot. Every entry
# grub-mkconfig writes passes $menuentry_id_option (00_header sets it to --id,
# grub-common 2.12, trixie); GRUB splits an unquoted variable into words, so
# --unrestricted in front of it reaches all of them. The script always exits
# 0: a failure here would fail update-grub, and with it the kernel upgrade.
GRUB_LOCK_SCRIPT='#!/bin/sh
# ImageSrv (#2450): e and c in this server GRUB menu ask for a password.
# Written by the installer (installer/lib/grublock.sh). Without a readable
# password the menu stays locked with no password at all: nobody edits.
# The password was changed by hand? Run update-grub.
LC_ALL=C; export LC_ALL
user=imagesrv
file=/etc/imagesrv/grub-password
echo "set superusers=\"$user\""
hash=
if ! password=$(cat "$file"); then
    echo "$0: cannot read $file -- e and c stay locked, with no password (#2450)" >&2
elif case "$password" in ""|*[!!-~]*) true ;; *) false ;; esac; then
    echo "$0: $file does not hold a password GRUB can take -- e and c stay locked, with no password (#2450)" >&2
else
    hash=$(printf "%s\n%s\n" "$password" "$password" | grub-mkpasswd-pbkdf2 --iteration-count=100000 \
        | sed -n "s/.*\(grub\.pbkdf2\.sha512\.[1-9][0-9]*\.[0-9A-F][0-9A-F]*\.[0-9A-F][0-9A-F]*\).*/\1/p")
    [ -n "$hash" ] || echo "$0: grub-mkpasswd-pbkdf2 gave no hash -- e and c stay locked, with no password (#2450)" >&2
fi
[ -z "$hash" ] || echo "password_pbkdf2 $user $hash"
echo "menuentry_id_option=\"--unrestricted \$menuentry_id_option\""
echo "export menuentry_id_option"
exit 0
'

# The verdict is grub.cfg read back (principle 5), not "update-grub said 0":
# before the first entry, superusers, a password line, and --unrestricted put
# in front of $menuentry_id_option after 00_header set it; the entries that
# boot the server -- the first one (GRUB_DEFAULT=0) and every one of 10_linux's
# ('gnulinux-' ids, the Advanced kernels too) -- pass $menuentry_id_option (or
# --unrestricted), and the recovery entry passes neither. A 10_linux that
# stopped passing it would leave the server at a password prompt on boot --
# that fails the install here, by name. Any other entry without it (os-prober's
# Multiboot entry in grub-common 2.12, a hand-written 40_custom one) asks for
# the password: locked, which is what this lock is for -- not a reason to leave
# the whole menu open (#2452, 05/11; the verdict used to refuse the lock for it
# on every upgrade).
# #2452: deploy/grub-lock-host.sh reads a server that upgrades with this same
# verdict: GRUB_LOCK_CFG names the file it generated, and
# GRUB_LOCK_RECOVERY_ENTRIES=any takes a server with no recovery entry of ours
# (setup-boot-server.sh) or the one written before the rename ("ImageCtl
# recovery", 14/10-31/10). Ours asks for the password; Debian's single-user
# entries (they boot without it) must not be in the menu: GRUB_DISABLE_RECOVERY.
grub_menu_locked() {
    [ "$DRY_RUN" -eq 0 ] || return 0
    _gl_cfg="${GRUB_LOCK_CFG:-$TARGET/boot/grub/grub.cfg}"
    [ -r "$_gl_cfg" ] || fail grub-lock-unchecked "cannot read $_gl_cfg after update-grub"
    _gl_bad=$(awk -v user="$GRUB_LOCK_USER" -v recovery="${GRUB_LOCK_RECOVERY_ENTRIES:-1}" -v linux="'gnulinux-" '
        $0 == "set superusers=\"" user "\"" { if (!n) su = 1 }
        index($0, "password_pbkdf2 " user " grub.pbkdf2.sha512.") == 1 { if (!n) pw = 1 }
        $1 == "menuentry_id_option=\"--id\"" { hdr = NR }
        $0 == "menuentry_id_option=\"--unrestricted $menuentry_id_option\"" { if (!n) ov = NR }
        $1 == "menuentry" || $1 == "submenu" {
            n++
            free = index($0, "$menuentry_id_option") > 0 || index($0, "--unrestricted") > 0
            if (index($0, "ImageSrv recovery") > 0 || index($0, "ImageCtl recovery") > 0) {
                r++; if (free) print "the recovery entry boots without the password"
            }
            else if (index($0, "(recovery mode)") > 0) print "a Debian recovery-mode entry is in the menu: " $0
            else if (free) ok++
            else if (n == 1 || index($0, linux) > 0) print "an entry asks for the password to boot: " $0
        }
        END {
            if (!su) print "no set superusers before the first entry"
            if (!pw) print "no password_pbkdf2 before the first entry"
            if (!ov || ov < hdr) print "no --unrestricted for the generated entries after 00_header"
            if (!ok) print "no entry that boots without a password"
            if (recovery != "any" && r != 1) print "not one recovery entry (" r + 0 ")"
        }' "$_gl_cfg") || fail grub-lock-unchecked "cannot read the entries of $_gl_cfg"
    [ -z "$_gl_bad" ] || fail grub-menu-unlocked "$_gl_cfg: $(printf '%s' "$_gl_bad" | tr '\n' ';')"
}
