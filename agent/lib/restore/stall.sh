# stall.sh -- a drawer whose bytes stopped is said within seconds (#2988).
# POSIX sh (busybox ash).
#
# Nadav 12/11: a pulled disk and a pulled network cable both left the drawer on
# "כותב" with its last speed for ~30 s, and only then came the failure. The
# waits themselves are right (the kernel's retries; a short outage must not end
# a round), so they stay as they are: this file decides NOTHING. It only says,
# within STALL_SHOW_S of the last byte, that the drawer stands and why it seems
# to, and logs when it moves again. The verdict stays with the ceilings that
# were there -- the kernel's evidence (drawer_watch), fanout's evictions, and
# the stream's WAIT_STREAM_STALL_S.
#
#   targets/<dev>/stall     "<kind> <epoch of its last byte> <limit s>" while it stands
#   targets/<dev>/stall.mv  "<epoch> <bytes> <moved 0|1>" -- its counter, this partition
#   $RUN_DIR/stall.src      the same for the stream's own counter (bytes.raw, fanout -P)
#
# kind, on evidence only:
#   waiting_disk  the stream kept coming STALL_GAP_S after this drawer stopped
#                 taking bytes: its own write stands (fanout feeds the siblings
#                 until its ring is full).
#   waiting_net   no network interface has a carrier (a cable pulled), or the
#                 stream itself stopped, no later than the drawer, and no
#                 sibling is waiting_disk (a sibling's full ring holds the
#                 stream too -- that one is named, the others are not).
# Neither yet (the stream stopped a moment after the drawer) = as it was.
# limit = the latest the agent ends it by a rule it holds: the stream's ceiling
# for the network, fanout's no-progress eviction for a disk. The kernel's
# evidence or fanout's 45 s hold (#1796) usually come first.
#
# The chunk engine ticks it too (#3336, chunkstall.sh): its own stream counter, its own
# ceilings and its own "nothing left to take" through STALL_SRC_FN, STALL_*_LIMIT_S and
# STALL_WHOLE_FN. Left unset, every line here is what it was.

STALL_SHOW_S="${STALL_SHOW_S:-3}"
STALL_GAP_S="${STALL_GAP_S:-2}"

_stall_src() { _last_number "$RUN_DIR/bytes.raw"; }   # the stream's own counter, fanout -P; STALL_SRC_FN = another engine's (chunkstall.sh)

_stall_moved() {
    # $1 = the record, $2 = the counter now, $3 = now. Sets _sm = the epoch it last
    # grew in this partition; "" = it has not grown yet (the first byte's wait is not a stall).
    _sm=; _sm_b=; _sm_m=; [ ! -r "$1" ] || read -r _sm _sm_b _sm_m < "$1"
    case "$2" in ''|*[!0-9]*) _sm=; return 0 ;; esac
    case "$_sm:$_sm_b:$_sm_m" in *[!0-9:]*|:*|*::*|*:) echo "$3 $2 0" > "$1"; _sm=; return 0 ;; esac
    if [ "$2" -gt "$_sm_b" ]; then echo "$3 $2 1" > "$1"; _sm=$3
    elif [ "$2" -lt "$_sm_b" ]; then echo "$3 $2 0" > "$1"; _sm=   # a counter that went back = a new start
    elif [ "$_sm_m" != 1 ]; then _sm=; fi
}

_stall_link_down() {
    # 0 = every network interface that reads a carrier reads 0. 1 = one is up, or none was read.
    _sl_r=1
    for _sl in "${SYSFS_ROOT:-${SYSROOT:-}/sys}"/class/net/*; do
        [ "${_sl##*/}" != lo ] || continue
        case "$(cat "$_sl/carrier" 2>/dev/null)" in 1) return 1 ;; 0) _sl_r=0 ;; esac
    done
    return "$_sl_r"
}

_stall_set() {
    # $1 = dev, $2 = kind, $3 = the epoch of its last byte.
    _ss_f="$RUN_DIR/targets/$1/stall"; _ss_k=; [ ! -r "$_ss_f" ] || read -r _ss_k _ss_x < "$_ss_f"
    [ "$_ss_k" != "$2" ] || return 0
    # STALL_NET_LIMIT_S / STALL_DISK_LIMIT_S: the ceilings of a stream that is not a fanout one (chunkstall.sh, #3336)
    if [ "$2" = waiting_net ]; then _ss_l=${STALL_NET_LIMIT_S:-$WAIT_STREAM_STALL_S}; else _ss_l=${STALL_DISK_LIMIT_S:-$((${FANOUT_STALL_EVICT_MS:-120000} / 1000))}; fi
    case "$_ss_l" in ''|*[!0-9]*) _ss_l=- ;; esac
    echo "$2 $3 $_ss_l" > "$_ss_f" || return 0
    log "$1: no bytes for $((_st_now - $3))s -- $2 (shown now; the verdict waits for its ceiling, ${_ss_l}s)"
}

_stall_end() {
    # $1 = dev, $2 = what ended it: `moving`, or words for the log.
    _se_f="$RUN_DIR/targets/$1/stall"; [ -e "$_se_f" ] || return 0
    _se_k=; _se_t=; read -r _se_k _se_t _se_x < "$_se_f"; rm -f "$_se_f"
    case "$_se_t" in ''|*[!0-9]*) _se_n="?" ;; *) _se_n=$((_st_now - _se_t)) ;; esac
    if [ "$2" = moving ]; then log "$1: the bytes moved again after ${_se_n}s ($_se_k) -- a disruption that recovered"
    else log "$1: stood still ${_se_n}s ($_se_k), then $2"; fi
}

drawer_stall_tick() {
    # $1 = the disks. drawer_watch, once a tick. Not on a local copy (its own waits, lccopy.sh).
    _st_now=$(date +%s); case "$_st_now" in ''|*[!0-9]*) return 0 ;; esac
    case "$(cat "${LC:-$RUN_DIR/lc}/phase" 2>/dev/null)" in copying|shrinking|verifying) return 0 ;; esac
    _sm=; [ -e "$RUN_DIR/source.rc" ] || _stall_moved "$RUN_DIR/stall.src" "$("${STALL_SRC_FN:-_stall_src}")" "$_st_now"
    _st_src=$_sm; _st_disk=0; _st_list=
    for _st_d in $1; do
        _st_t="$RUN_DIR/targets/$_st_d"; _st_s=$(cat "$_st_t/state" 2>/dev/null)
        if [ "$_st_s" != writing ]; then _stall_end "$_st_d" "${_st_s:-?}"; rm -f "$_st_t/stall.mv"; continue; fi
        # STALL_WHOLE_FN: a drawer with nothing left to take is not standing, whatever the stream does (chunkstall.sh)
        if [ -n "${STALL_WHOLE_FN:-}" ] && "$STALL_WHOLE_FN" "$_st_d"; then _stall_end "$_st_d" "it holds the whole partition"; rm -f "$_st_t/stall.mv"; continue; fi
        _stall_moved "$_st_t/stall.mv" "$(target_bytes "$_st_d")" "$_st_now"
        if [ -z "$_sm" ] || [ $((_st_now - _sm)) -lt "$STALL_SHOW_S" ]; then _stall_end "$_st_d" moving; continue; fi
        if [ -n "$_st_src" ] && [ $((_st_src - _sm)) -ge "$STALL_GAP_S" ]; then
            _stall_set "$_st_d" waiting_disk "$_sm"; _st_disk=1; continue
        fi
        _st_list="$_st_list $_st_d:$_sm"
    done
    [ -n "$_st_list" ] || return 0
    _st_down=0; _stall_link_down && _st_down=1
    for _st_x in $_st_list; do
        if [ "$_st_down" = 1 ] || { [ "$_st_disk" = 0 ] && [ -n "$_st_src" ] && [ $((_st_now - _st_src)) -ge "$STALL_SHOW_S" ]; }; then
            _stall_set "${_st_x%%:*}" waiting_net "${_st_x#*:}"
        elif [ "$_st_disk" = 1 ]; then _stall_end "${_st_x%%:*}" "a sibling's disk held the stream"; fi
    done
}

drawer_stall_clear() {
    # $1 = the disks. The partition's stream ended: nothing stands any more.
    _st_now=$(date +%s); case "$_st_now" in ''|*[!0-9]*) _st_now=0 ;; esac
    for _st_d in $1; do
        _stall_end "$_st_d" "the partition's stream ended"; rm -f "$RUN_DIR/targets/$_st_d/stall.mv"
    done
    rm -f "$RUN_DIR/stall.src"
}

stall_fields() {
    # $1 = dev. Sets _sf_k (kind), _sf_s (seconds it stands) and _sf_l (limit s, "" = none);
    # 1 = it does not stand (no record, not writing, or a record that is not ours).
    _sf_k=; _sf_s=; _sf_l=
    [ "$(cat "$RUN_DIR/targets/$1/state" 2>/dev/null)" = writing ] && [ -r "$RUN_DIR/targets/$1/stall" ] || return 1
    read -r _sf_k _sf_t _sf_l < "$RUN_DIR/targets/$1/stall"
    case "$_sf_k" in waiting_disk|waiting_net) ;; *) return 1 ;; esac
    case "$_sf_t" in ''|*[!0-9]*) return 1 ;; esac
    _sf_now=$(date +%s); case "$_sf_now" in ''|*[!0-9]*) return 1 ;; esac
    _sf_s=$((_sf_now - _sf_t)); [ "$_sf_s" -ge 0 ] || _sf_s=0
    case "$_sf_l" in ''|*[!0-9]*) _sf_l= ;; esac
}

stall_disk_holds() {
    # #3594 (J1 17/11, cloner 2): 75 s of no bytes -- a drawer's disk (Samsung on a 3.0 link) was behind, its ring full,
    # and fanout holds the stream for every drawer then -- were sounded "network lost" and "back". 0 = a writing
    # drawer is standing for its OWN disk (waiting_disk, above) and no interface has lost its carrier: the stream's
    # standstill is the disk's, not the network's, and roundbeep.sh stays silent. 1 = no such drawer, or the cable is out
    # (the network's own evidence wins over the disk's).
    for _sh_t in "$RUN_DIR"/targets/*; do
        stall_fields "${_sh_t##*/}" || continue
        [ "$_sf_k" = waiting_disk ] || continue
        if _stall_link_down; then return 1; fi
        return 0
    done
    return 1
}

_stall_link_up() {
    # 0 = an interface READS carrier 1. Not "no interface reads 0" (_stall_link_down's other side): a carrier that could not be read is
    # not evidence the cable is in (principle 5).
    for _sl in "${SYSFS_ROOT:-${SYSROOT:-}/sys}"/class/net/*; do
        [ "${_sl##*/}" != lo ] || continue
        [ "$(cat "$_sl/carrier" 2>/dev/null)" != 1 ] || return 0
    done
    return 1
}

stream_still_tick() {
    # #3731 (Nadav 18/11: "ניתוק = זיהוי מיידי, 2 דקות. עיכוב אינו ניתוק"). One still second of wait_progress's stream counter
    # (waits.sh). The network's ceiling counts it -- unless a drawer of THIS machine holds the stream for its own disk
    # (a writing drawer standing as waiting_disk, above) and a carrier is READ as up: then it is the disk's wait, and fanout's
    # eviction (DISK_STALL_S) ends it for that drawer alone. No such evidence, a cable out, a carrier that cannot be read, a
    # record that cannot be read: the timer runs, as before. And not for ever: held for WAIT_DRAWER_S the timer counts again,
    # because a hold the eviction did not end is a wait without a ceiling.
    if [ "${_sh_held:-0}" -lt "${WAIT_DRAWER_S:-300}" ] && _stall_link_up; then
        for _sk_t in "$RUN_DIR"/targets/*; do
            stall_fields "${_sk_t##*/}" || continue
            [ "$_sf_k" = waiting_disk ] || continue
            _sh_held=$((${_sh_held:-0} + WAIT_POLL_S)); return 0
        done
    fi
    _p_still=$((_p_still + WAIT_POLL_S))
}

stall_json() {   # $1 = dev. ,"stall":"waiting_disk","stall_s":7,"stall_limit_s":120 -- nothing when it moves
    stall_fields "$1" || return 0
    printf ',"stall":"%s","stall_s":%s,"stall_limit_s":%s' "$_sf_k" "$_sf_s" "${_sf_l:-null}"
}

# #3270 (metal 14/11, cloner 2): a drawer PULLED mid-write drew red "failed" -- the disk was out for 20 seconds and
# the operator was told the write was lost. A pull is a state of the process, not a verdict (Nadav): the screen's
# drawer record says `pulled` (grey, "waiting for it to come back"), and `returned` once a disk with the serial and
# size the target was bound to (failmark.sh `bound`) is in a drawer again. Red stays for a failure that is real:
# anything but `pulled` (a write error, a refused disk), a foreign disk, a completion that failed (`back_failed`).
# Display only -- targets/<dev>/state stays `failed`, and the server, the beeps and drawer_back/drawer_hold read that.
# #3209: the same gate as the wire's `"pulled": true` (drawerplug.sh, #3288): only while the machine is WRITING. After
# the write (`drawer_back all`, drawers.sh, then `verifying`) nobody looks for the drawer any more, so "waiting for it
# to come back" would be a lie -- it is "failed", red, with its reason. A state that cannot be read is not `writing`.
drawer_gui_word() {   # $1 = dev, $2 = its state -> the word of its drawer= record, on stdout
    _gw_t="$RUN_DIR/targets/$1"
    if [ "$2" != failed ] || [ ! -f "$_gw_t/pulled" ] || [ -f "$_gw_t/foreign" ] || [ -f "$_gw_t/back_failed" ]; then
        printf '%s' "$2"; return 0
    fi
    if [ "$(cat "$RUN_DIR/state" 2>/dev/null)" != writing ]; then printf '%s' "$2"; return 0; fi   # #3209: past the write it is a failure
    _gw_k=$(cat "$_gw_t/bound" 2>/dev/null); _gw_k=${_gw_k%|*}   # serial|sectors
    case "$_gw_k" in ''|'|'*|*'|') printf pulled; return 0 ;; esac   # nothing to know it by: it cannot be seen as back
    for _gw_n in $(list_drawers); do
        [ "$(_target_key "$_gw_n")" != "$_gw_k" ] || { printf returned; return 0; }
    done
    printf pulled
}
