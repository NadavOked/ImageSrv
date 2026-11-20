# targetwwn.sh -- #2529: the WWN as one more proof that the disk written is the disk chosen.
# POSIX sh (busybox ash). Loaded after failmark.sh: its target_bind and assert_target call these.
#
# #2434 binds serial|sectors|slot at target_init (`bound`) and asks it again before every
# destructive step (assert_target). The owner's decision (15/11, option B): the WWN only makes
# that check STRICTER -- a disk whose WWN is not the one bound fails by name and is not written.
# It never makes it laxer: twins (one serial, one size) stay refused (target_unique, #2448), a
# WWN does not tell them apart here, and `bound` keeps its format (target_bound_serial).
#
# A side that has no WWN (a VM's loop file, a bridge that does not pass it) is neither stricter
# nor laxer: serial, size and slot decide, as before -- and the log says, once per disk and side,
# that the WWN was not compared (principle 5: "not checked" is not "checked, the same").
#
# The WWN is the one hello sends (diskinfo.sh, #2528): the block device's `wwid` (NVMe), else
# the device's (SATA/SCSI). Kept in targets/<dev>/wwn, beside `bound`.

target_wwn_read() {
    # $1 = disk. PRINTS its WWN, or nothing when sysfs has none.
    trim "$(cat "$SYSROOT/sys/block/$1/wwid" "$SYSROOT/sys/block/$1/device/wwid" 2>/dev/null | head -n 1)"
}

target_wwn_bind() {
    # $1 = disk, from target_bind (after `bound` and `twin`). Keeps the WWN, and says in ONE JSON
    # line of the agent log exactly what was approved for writing. 1 = the WWN could not be kept:
    # `bound` is removed with it, so the disk is not "chosen" and is not written (principle 5).
    _twb_t="$RUN_DIR/targets/$1"; _twb_w=$(target_wwn_read "$1"); _twb_j=null
    rm -f "$_twb_t/wwn" "$_twb_t/wwn_note"
    if [ -n "$_twb_w" ]; then
        printf '%s\n' "$_twb_w" > "$_twb_t/wwn" || {
            rm -f "$_twb_t/bound"; log "WARNING: $1: its WWN could not be kept -- not bound, not written (#2529)" >&2; return 1; }
        _twb_j="\"$(json_escape "$_twb_w")\""
    fi
    _twb_b=$(cat "$_twb_t/bound" 2>/dev/null); _twb_p=${_twb_b##*|}; _twb_b=${_twb_b%|*}   # serial may hold '|'; size and slot do not
    _twb_n=${_twb_b##*|}; _twb_s=${_twb_b%|*}
    case "$_twb_n" in ''|*[!0-9]*) _twb_n=null ;; esac
    case "$_twb_p" in ''|*[!0-9]*) _twb_p=null ;; esac
    _twb_sj=null; [ -z "$_twb_s" ] || _twb_sj="\"$(json_escape "$_twb_s")\""
    _twb_tw=null; [ ! -s "$_twb_t/twin" ] || _twb_tw="\"$(json_escape "$(cat "$_twb_t/twin")")\""
    log "target_bound {\"disk\":\"$(json_escape "$1")\",\"serial\":$_twb_sj,\"sectors\":$_twb_n,\"port\":$_twb_p,\"wwn\":$_twb_j,\"twin\":$_twb_tw}" >&2   # >&2: target_init may run inside $( )
    return 0
}

target_wwn_same() {
    # $1 = disk, from assert_target once serial|sectors|slot matched. 0 = its WWN now is the
    # one bound, or a side has none (said once); 1 = another WWN: it failed by name (foreign).
    _tws_t="$RUN_DIR/targets/$1"; _tws_n=$(target_wwn_read "$1"); _tws_b=$(cat "$_tws_t/wwn" 2>/dev/null)
    if [ -n "$_tws_b" ] && [ -n "$_tws_n" ]; then
        [ "$_tws_n" = "$_tws_b" ] && return 0
        target_foreign "$1" "ה-WWN של הדיסק שבשם $1 אינו של הדיסק שנבחר (נבחר: $_tws_b, עכשיו: $_tws_n) — לא נכתב עליו"
        return 1
    fi
    if [ -z "$_tws_b" ] && [ -z "$_tws_n" ]; then _tws_w="at the choice and now"
    elif [ -z "$_tws_b" ]; then _tws_w="at the choice"; else _tws_w="now"; fi
    [ "$(cat "$_tws_t/wwn_note" 2>/dev/null)" = "$_tws_w" ] && return 0
    printf '%s\n' "$_tws_w" > "$_tws_t/wwn_note" 2>/dev/null
    log "$1: WWN not compared -- none $_tws_w; serial, size and slot only (#2529)" >&2
    return 0
}
