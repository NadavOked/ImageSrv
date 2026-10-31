# verdict.sh -- turning the evidence a drawer left behind into its state.
# POSIX sh (busybox ash).
#
# `drawers.sh` runs the stream; this file answers "so did it work?". The
# split is deliberate: the evidence a drawer leaves is three separate
# records -- `fanout`'s report line, `zstd.rc`, and `pipeline.rc` -- and
# every bug this file has carried came from folding two of them into one.
# ‏#440 (a dead disk reported as the network), #527 (a lost zstd exit
# code), #520 (a sha waived by a dead sibling): all of them are the same
# shape, and all of them lived here.
#
# Loaded by drawers.sh. Nothing else sources it.

_write_fail() {
    # $1=dev $2=idx $3=סיבת fanout. הקבצים היו על הדיסק ולא נקראו —
    # לכן דיסק מת דווח כרשת (#440). חסר ≠ דיסק תקין ≠ רשת.
    _t="$RUN_DIR/targets/$1"
    _prc=$(cat "$_t/pipeline.rc" 2>/dev/null)
    _plog=$(tail -n 1 "$_t/partclone.log" 2>/dev/null | tr -d '\r')
    [ ! -f "$_t/writeback.failed" ] || _plog="periodic fsync failed (write_error)"
    fail_written_target "$1" \
        "partition $2: הכתיבה לדיסק נכשלה (fanout: ${3:-אין דיווח}; partclone rc=${_prc:-לא נרשם}${_plog:+; $_plog})"
}

_read_fanout_report() {
    # דוח fanout + קודי הצינור → מצב המגירה. ‏done רק כשכולם הסכימו.
    # $1 = index, $2 = fs, $3.. = disks.
    _idx="$1"; _fs="$2"; shift 2
    for _d in "$@"; do
        _t="$RUN_DIR/targets/$_d"
        case "$(cat "$_t/state")" in failed|cancelled) continue ;; esac   # #1386
        _line=$(grep -F "$_t/feed " "$RUN_DIR/fanout.out" 2>/dev/null)
        case "$_line" in
            *" ok")
                # שלושה מסכימים, לא שניים: ‏fanout מסר את הבייטים,
                # ‏zstd פרס אותם, ו-partclone כתב. ‏zstd שנפל בשקט
                # משאיר partclone שיוצא 0 על קלט חלקי (#527).
                _rc=$(cat "$_t/pipeline.rc" 2>/dev/null)
                _zrc=$(cat "$_t/zstd.rc" 2>/dev/null)
                if [ "$_rc" = "0" ] && [ "$_zrc" = "0" ]; then
                    # #1487: partclone -r leaves the NTFS backup boot sector unwritten.
                    if ntfs_boot_target "$_d" "$_idx" "$_fs"; then target_partition_done "$_d"
                    else fail_written_target "$_d" "$NTFS_BOOT_ERROR"; fi
                elif [ "$_rc" = "0" ] && [ -z "$_zrc" ]; then
                    # אין קובץ = לא נמדד. זה אינו "zstd הצליח" ואינו
                    # כישלון של partclone — מצב שלישי בשם (עיקרון 5).
                    _write_fail "$_d" "$_idx" "ok (zstd rc לא נרשם)"
                else
                    _write_fail "$_d" "$_idx" "ok"
                fi
                ;;
            *" failed empty stream"|*" failed short stream"*|*" failed long stream"*)
                # #1728: fanout's length verdict is the stream's -- every drawer got
                # the same bytes. No ata_log, no disk_failure on a healthy disk.
                fail_written_target "$_d" \
                    "partition $_idx: הזרם הגיע ריק או חלקי (fanout: ${_line#* failed })" stream
                ;;
            *" failed "*)
                _write_fail "$_d" "$_idx" "${_line#* failed }"
                ;;
            *)
                _write_fail "$_d" "$_idx" "אין דיווח"
                ;;
        esac
    done
}

_any_alive() {
    for _d in $1; do
        case "$(cat "$RUN_DIR/targets/$_d/state")" in failed|cancelled) ;; *) return 0 ;; esac
    done
    return 1
}

_fail_alive() {
    # $1 = disks, $2 = reason. מגירות שכבר failed לא נדרסות.
    for _d in $1; do
        case "$(cat "$RUN_DIR/targets/$_d/state" 2>/dev/null)" in failed|cancelled) continue ;; esac
        fail_written_target "$_d" "$2"
    done
}

_machine_state() {
    # $1 = disks. המצב של המחשב כולו לפי המגירות: הכול נכתב, חלק נכתב,
    # או כלום. עד #67 היו כאן שני מצבים בלבד — `_any_alive` אמת גם על
    # מגירה אחת ששרדה מתוך שלוש, ומחשב שאיבד מגירה דיווח "done" בדיוק
    # כמו מחשב שכל מגירותיו נכתבו. ברמת המגירה הכשל היה גלוי כל הזמן
    # (‏targets_json), וברמת המחשב הוא נבלע — וזה מה שהמפעיל רואה.
    # ‏#1386: בוטל אינו נכתב ואינו נכשל -- לא נספר באף צד. הכול בוטל =
    # המכונה סיימה את חלקה בלי לכתוב (partial), לא "נכשלה".
    _alive=0; _dead=0; _off=0
    for _d in $1; do
        case "$(cat "$RUN_DIR/targets/$_d/state" 2>/dev/null)" in
            failed) _dead=$((_dead + 1)) ;;
            cancelled) _off=$((_off + 1)) ;;
            *) _alive=$((_alive + 1)) ;;
        esac
    done
    if [ "$_alive" -eq 0 ] && [ "$_dead" -eq 0 ] && [ "$_off" -gt 0 ]; then echo "partial"
    elif [ "$_alive" -eq 0 ]; then echo "failed"
    elif [ "$_dead" -eq 0 ]; then echo "done"
    else echo "partial"; fi
}

# --- #1598: a drawer that died mid-stream is out within seconds ---------------
# fanout drops a drawer at once only on EPIPE; a drawer that stops taking bytes
# holds the whole machine's stream first: 45s (#1796) or 150s (#660, #674).
# A pulled drawer is not even stopped at first: partclone writes into the page
# cache and "succeeds", so the bar climbs. On metal (17/10, cloner 2) sdb was
# deleted at 10:57:52, evicted ~150s later, by then the sender had lost the
# machine, and the healthy sda failed with "the stream did not arrive". So the
# kernel is asked every tick -- and only positive evidence counts: the disk left
# sysfs, sysfs says offline, or the kernel failed a WRITE. Silence is not death.

DRAWER_GONE_WHY="הכונן נעלם מהמערכת (נשלף או נותק)"   # #2045: also `pulled` -- the local clone's card says so

drawer_dead_reason() {
    # $1 = disk, $2 = 1 if /sys/block/$1 existed when the stream began,
    # $3 = this tick's dmesg. Prints the reason; 1 = no evidence of death.
    _dr_sys="$SYSROOT/sys/block/$1"
    if [ "$2" = 1 ] && [ ! -e "$_dr_sys" ]; then echo "$DRAWER_GONE_WHY"; return 0; fi
    if [ "$(cat "$_dr_sys/device/state" 2>/dev/null)" = offline ]; then echo "הקרנל סימן את הכונן offline"; return 0; fi
    _dr_l=$(awk -v dev="$1" -v since="$(cat "$RUN_DIR/targets/$1/since" 2>/dev/null)" '
        match($0, /^\[ *[0-9]+\.[0-9]+\]/) && substr($0, RSTART + 1, RLENGTH - 2) + 0 < since + 0 { next }
        index($0, " error, dev " dev ",") && /\(WRITE\)/ { print; exit }' "$3" 2>/dev/null)
    [ -n "$_dr_l" ] || return 1
    # #2045 (metal 23/10): a pulled drawer's WRITE fails while its node is still in sysfs --
    # ~12 s of link retries first. Its port says why: then it is "pulled", in Hebrew.
    if drawer_port_gone "$1" "$3"; then echo "$DRAWER_GONE_WHY"; return 0; fi
    echo "הקרנל דחה כתיבה לכונן: ${_dr_l#*] }"
}

drawer_port_gone() {
    # $1 = disk, $2 = this tick's dmesg. 0 = its ata port (kept at target_init: `seen`,
    # the node may be gone) said, since the drawer's `since`, that the disk left: DevExch
    # (server/ata_cause.py's "removed"), or "SATA link down" with no "link up" after it --
    # and the disk itself reported no media error. Line noise (10B8B, "ATA bus error") does
    # not count against it: a yanked SATA cable prints it too (R07-R08). The server still
    # classifies the round's ata_log by its own order -- this is the operator's sentence.
    _pg_a=$(cut -d'|' -f3 "$RUN_DIR/targets/$1/seen" 2>/dev/null)
    case "$_pg_a" in ''|*[!0-9]*) return 1 ;; esac
    awk -v ata="$_pg_a" -v since="$(cat "$RUN_DIR/targets/$1/since" 2>/dev/null)" '
        match($0, /^\[ *[0-9]+\.[0-9]+\]/) && substr($0, RSTART + 1, RLENGTH - 2) + 0 < since + 0 { next }
        $0 !~ (" ata" ata "(\\.[0-9]+)?: ") { next }
        /[{ ]UNC[ }]|media error/ { media = 1 }
        /DevExch/ { gone = 1 }
        /SATA link down/ { down = 1 }
        /SATA link up/ { down = 0 }
        END { exit !((gone || down) && !media) }' "$2" 2>/dev/null
}

drawer_watch() {
    # $1 = the stream's pid, $2 = partition index, $3 = the disks. In the
    # background while the stream runs. A dead drawer gets `dead`, the reader of
    # its fifo (reader.pid: its partclone, or its zstd -- drawerfeed.sh) is
    # killed -- fanout's next write is EPIPE and the drawer is out -- and it is
    # failed by name, with its kernel evidence.
    _dw_seen=" "; _dw_k="$RUN_DIR/dmesg.watch"; _dw_blind=0
    for _dw_d in $3; do [ -e "$SYSROOT/sys/block/$_dw_d" ] && _dw_seen="$_dw_seen$_dw_d "; done
    while kill -0 "$1" 2>/dev/null; do
        _nap "$1"   # #1705: the stream's end is seen at once, not up to a second later
        if ! dmesg > "$_dw_k" 2>/dev/null; then
            : > "$_dw_k"   # not "no errors": said once, and sysfs is still watched
            [ "$_dw_blind" = 1 ] || log "WARNING: dmesg unreadable -- kernel write errors on drawers are not watched"
            _dw_blind=1
        fi
        for _dw_d in $3; do
            _dw_t="$RUN_DIR/targets/$_dw_d"
            case "$(cat "$_dw_t/state" 2>/dev/null)" in failed|cancelled) continue ;; esac
            [ -f "$_dw_t/cancel" ] && { cancel_drawer "$_dw_d" live || :; continue; }   # #1386
            case "$_dw_seen" in *" $_dw_d "*) _dw_s=1 ;; *) _dw_s=0 ;; esac
            _dw_why=$(drawer_dead_reason "$_dw_d" "$_dw_s" "$_dw_k") || continue
            echo "$_dw_why" > "$_dw_t/dead"; [ "$_dw_why" != "$DRAWER_GONE_WHY" ] || : > "$_dw_t/pulled"
            if [ "$_dw_why" = "$DRAWER_GONE_WHY" ]; then event_beep pulled; else event_beep failure; fi
            log "$_dw_d: $_dw_why -- out of the stream now, the other drawers carry on"
            kill -9 "$(cat "$_dw_t/reader.pid" 2>/dev/null)" 2>/dev/null \
                || log "WARNING: $_dw_d: its fifo's reader was not stopped -- fanout drops it on its own stall ceiling"
            fail_written_target "$_dw_d" "מחיצה $2: $_dw_why"
        done
    done
    rm -f "$_dw_k"
}

# ברזל 17/10: progress_loop (progress.sh) משאיר את הסמן כשהשרת עונה not_open --
# הסבב נסגר והשולח כבר איננו. עד אז המסלול הראשי המשיך לטבלה ולזרם מול
# שולח שיצא, והמכונה נשארה "כותבת" עם דיסק חצי-מוכן.
ROUND_CLOSED_WHY="השרת סגר את הסבב -- הכתיבה הופסקה"
round_closed() { [ -e "$RUN_DIR/session-closed" ]; }
round_closed_why() {   # #1928 (S4): the server's reason when it gave one -- "the image file is missing", not just "closed"
    _rw=$(head -n 1 "$RUN_DIR/session-closed.why" 2>/dev/null)
    if [ -n "$_rw" ]; then echo "השרת סגר את הסבב: $_rw -- הכתיבה הופסקה"; else echo "$ROUND_CLOSED_WHY"; fi
}
round_closed_fail() {   # $1 = disks. 0 = closed, and every live drawer failed by name
    round_closed || return 1
    _fail_alive "$1" "$(round_closed_why)"; return 0
}

# --- #1386: "בטל דיסק זה" -- a drawer leaves the stream on request ----------
# The request is targets/<dev>/cancel (cancel.sh writes it, with who asked).
# Here because every drawer path loads this file.

cancel_drawer() {
    # $1 = dev, $2 = `live` while its stream runs (the watcher). Its fifo's
    # reader (reader.pid) is killed only then -- between partitions its pid file names a process that
    # is gone, and a reused pid is someone else's. `dead` makes the stream's own
    # waits skip it (#1603); the GPT goes only after the writer has stopped.
    _cd_t="$RUN_DIR/targets/$1"
    [ -f "$_cd_t/cancel" ] || return 1
    case "$(cat "$_cd_t/state" 2>/dev/null)" in failed|cancelled) return 1 ;; esac
    echo "בוטל באמצע הכתיבה" > "$_cd_t/dead"
    log "$1: cancelled $(cat "$_cd_t/cancel") -- out of the stream, the other drawers carry on"
    if [ "${2:-}" = live ]; then
        _cd_z=$(cat "$_cd_t/reader.pid" 2>/dev/null)
        { [ -n "$_cd_z" ] && kill -9 "$_cd_z" 2>/dev/null; }             || log "WARNING: $1: its fifo's reader was not stopped -- fanout drops it on its own stall ceiling"
        _cd_p=$(cat "$_cd_t/pipeline.pid" 2>/dev/null)
        [ -z "$_cd_p" ] || wait_pid "$_cd_p" "$WAIT_HELPER_S" "המגירה $1 (ביטול)" || :
    fi
    # Principle 5: a GPT that was not wiped is said so, never "wiped".
    if wipe_written_gpt "$1" "cancel"; then rm -f "$_cd_t/error"
    else echo "טבלת המחיצות לא נמחקה — הדיסק לא זוהה בוודאות או שהמחיקה נכשלה" > "$_cd_t/error"; fi
    date +%s > "$_cd_t/cancelled_at" 2>/dev/null
    target_set "$1" cancelled
}

cancel_pending() {
    # $@ = disks. A request that came while no stream ran (before the first
    # partition, between two, after the last), or that the watcher did not
    # finish before its ceiling: carried out now, without killing anything.
    for _cp_d in "$@"; do cancel_drawer "$_cp_d" || :; done
}
