# idlegate.sh -- #3268 (security review of #3271, H2/H4): who may touch a drawer the waiting-screen
# write check (idlecheck.sh) may be writing on. POSIX sh (busybox ash).
#
# The check writes 3 x 4MiB and its markers on a disk nobody chose, and puts the originals back
# afterwards. Anything else that reads or writes that disk in between -- a local clone (source or
# target), a capture, a direct send, a round -- either reads the check's pattern as if it were the
# disk, or has its own write overwritten by the put-back. So:
#   hold     -- a task is about to use the drawers: the scanner starts no new check (idle_wc_disk
#               takes its lock first and looks for the hold after, so one of the two always sees the other);
#   ready    -- per disk, before the task touches it: the running check has ended (its lock is gone),
#               no dd is still writing the disk, and -- for a disk that is read (a source) -- no open
#               copy is left on the server for it (put back now if there is one). Not ready = the task
#               refuses that disk by name; it never goes on "probably fine" (principle 5);
#   release  -- the machine is back on the waiting screen (imagesrv-agent, wait_poll).
# The check runs only on a cloner that talks to a server (idle_wc_start); anywhere else nothing of
# it can be on a disk, and every call here is a no-op (the clone stick, usbclone.sh: no server).
#
# Files: $RUN_DIR/idlecheck/hold, busy.<dev>/ (the check's lock), settled.<dev> (= serial|sectors
# of the disk that was found clean of the check -- per insertion, like res.<dev>).

IDLE_WC_KILL_S="${IDLE_WC_KILL_S:-10}"   # the value of today; the brain's (idle_ceilings.kill_wait_s) replaces it through idle_ceilings_refresh (idlecheck.sh), #3672 (A15)

_ic_on() { [ "${D_ROLE:-}" = cloner ] && [ -n "${SERVER:-}" ]; }

idle_wc_hold() { _ic_on || return 0; mkdir -p "$RUN_DIR/idlecheck" && : > "$RUN_DIR/idlecheck/hold"; }
idle_wc_release() { rm -f "$RUN_DIR/idlecheck/hold"; }

_ic_writers() { # $1 = node. The pid of every dd whose argument is exactly of=<node>, from /proc (no pkill in every busybox: srgui.sh). 1 = /proc could not be read
    [ -r /proc/self/cmdline ] || return 1
    for _iw_f in $(grep -lF "of=$1" /proc/[0-9]*/cmdline 2> /dev/null); do   # a process that ended between the glob and the read is not a writer
        _iw_a=$( { tr '\0' '\n' < "$_iw_f"; } 2> /dev/null)
        case "$(printf '%s\n' "$_iw_a" | head -n 2 | tr '\n' ' ')" in dd\ *|*/dd\ *) ;; *) continue ;; esac   # dd itself, or a dd script under an interpreter -- not grep, not a shell
        printf '%s\n' "$_iw_a" | grep -qxF "of=$1" || continue   # of=/dev/sda1 is not of=/dev/sda
        _iw_p=${_iw_f%/cmdline}; echo "${_iw_p#/proc/}"
    done
}

_ic_stop_writers() { # $1 = node. Kills every dd that writes it and waits until /proc shows none. 0 = none left; 1 = one is still there after IDLE_WC_KILL_S (a dd in D state finishes its write), or /proc was not read
    _ik_t=0
    while :; do
        _ik_p=$(_ic_writers "$1") || { log "$1: /proc could not be read -- not known whether a dd still writes the disk"; return 1; }
        [ -n "$_ik_p" ] || return 0
        [ "$_ik_t" -lt $((IDLE_WC_KILL_S * 10)) ] || { log "$1: dd $(echo $_ik_p) still writes the disk ${IDLE_WC_KILL_S}s after kill -9"; return 1; }
        # shellcheck disable=SC2086 # a pid list
        kill -9 $_ik_p 2> /dev/null   # one that ended meanwhile: the next look says so
        sleep 0.1; _ik_t=$((_ik_t + 1))
    done
}

_ic_settled() { # $1 = disk. 0 = this insertion was found clean of the check (settled.<dev>), or it has no serial (no copy could have been made)
    _is_i=$(_ic_ident "$1") || return 0
    [ "$(cat "$RUN_DIR/idlecheck/settled.$1" 2> /dev/null)" = "$_is_i" ]
}

idle_wc_ready() { # $1 = disk, $2 = src (it will be read) | dst (it will be overwritten). 0 = nothing of the check can write it any more (and a source carries none of it); 1 = prints why not
    _ic_on || return 0
    idle_ceilings_refresh; idle_wc_hold; _iy_t=0   # #3672 (A15): the brain's ceilings, not constants of this file
    while [ -e "$RUN_DIR/idlecheck/busy.$1" ]; do   # the check on it is still running: it ends (and puts the disk back) first
        [ "$_iy_t" -lt "$IDLE_WC_BUSY_WAIT_S" ] || { echo "בדיקת הכתיבה מהמסך הממתין עוד רצה על הדיסק — לא נגענו בו"; return 1; }
        sleep 1; _iy_t=$((_iy_t + 1))
    done
    _iy_w=$(_ic_writers "$DEVROOT/$1") || { echo "לא הצלחנו לבדוק אם בדיקת הכתיבה עוד כותבת על הדיסק (/proc) — לא נגענו בו"; return 1; }
    [ -z "$_iy_w" ] || { echo "dd של בדיקת הכתיבה עוד כותב על הדיסק — לא נגענו בו"; return 1; }
    [ "$2" = src ] || return 0   # a target is overwritten whole: an open copy for it is out of date, never put back over the new content (idlerecover.sh)
    _ic_settled "$1" && return 0
    mkdir "$RUN_DIR/idlecheck/busy.$1" 2> /dev/null || { echo "בדיקת הכתיבה התחילה על הדיסק עכשיו — לא נגענו בו"; return 1; }
    idle_wc_recover "$1" < /dev/null > /dev/null; _iy_rc=$?
    rm -rf "$RUN_DIR/idlecheck/busy.$1"
    [ "$_iy_rc" = 0 ] || { echo "לדיסק יש עותק פתוח של בדיקת הכתיבה בשרת, והוא לא הוחזר — לא נקרא ממנו"; return 1; }
}

idle_wc_settle_all() { # a person opened the local copy or the restore from the server: every drawer clean before its inventory is read. A disk that is not stays refused by name at lc_run
    _ic_on || return 0
    idle_wc_hold
    for _ia_d in $(list_drawers); do
        _ia_w=$(idle_wc_ready "$_ia_d" src) || log "$_ia_d: $_ia_w"
    done
}
