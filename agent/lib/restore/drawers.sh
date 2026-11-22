# drawers.sh -- cloning-room mode: one stream, several drawers, isolated.
# POSIX sh (busybox ash).
#
# The stream is the chunk engine's, the only multicast engine (#3173): one
# receiver for the machine, writing every drawer on its own, so a slow or
# failing drive stops only itself (interfaces.md section 7). This file runs the
# per-drawer preparation (fit, write check, table) and hands each partition to
# chunk_partition_drawers (chunkrecv.sh), which turns the receiver's verdict per
# drawer into the per-target states of interface 4.

FANOUT_BUFFER="${FANOUT_BUFFER:-268435456}"     # 256MB per drawer, at most

_fanout_buffer() {
    # $1 = כמה מגירות. 256MB למגירה הוא אידאל, לא הבטחה: על מכונת 512MB
    # שלוש מגירות = 768MB, וה-OOM killer הורג את fanout באמצע הזרם (#21).
    # לוקחים את הזמין פחות רזרבה לצנרת (128MB), מחלקים במגירות, ולא
    # יורדים מתחת ל-2MB — מאגר קטן מ-READ_CHUNK נכשל בשקט (הלקח מ-#12).
    _avail=$(awk '/MemAvailable/ {print $2}' "$SYSROOT/proc/meminfo" 2>/dev/null)
    if [ -z "$_avail" ]; then echo "$FANOUT_BUFFER"; return; fi
    _budget=$(( (_avail - 131072) * 1024 / $1 ))
    [ "$_budget" -gt "$FANOUT_BUFFER" ] && _budget="$FANOUT_BUFFER"
    [ "$_budget" -lt 2097152 ] && _budget=2097152
    echo "$_budget"
}

list_drawers() {
    # Every internal disk (disk_internal: no USB enclosure, no SD card -- #1832)
    # is a drawer. The cloning machines boot from the network, so nothing here
    # is a system disk -- unlike a classroom station, where the internal disk is the target.
    for _n in $(list_disks); do
        disk_internal "$_n" && echo "$_n"
    done
}

restore_pipe() {
    # $1 = target dir (its feed fifo), $2 = partclone, $3 = node, $4 = the pipe's rc
    # file. zstd -d | partclone for the single restore (#1906).
    # ה-rc של zstd נלכד לחוד ל-zstd.rc, כמו `source.rc` וכמו `capture.sh:177`:
    # ב-busybox ash אין `pipefail`, ו-`$?` של צינור הוא של האחרון בלבד. ‏zstd
    # שנפל באמצע ו-partclone שקיבל EOF ויצא 0 נחתמו `done` על כתיבה קצרה (#527).
    # ⚠️ **לא `set -o pipefail`** — אינו קיים ב-busybox ash; עובר בטסט (Git Bash) בלבד.
    read -r _rp_me _ < /proc/self/stat   # this shell's own pid (a subshell's: not $$), for the watcher
    rm -f "$1/writeback.failed"; writeback_watch "$1" "$3" "$_rp_me" > /dev/null 2>&1 & _rp_w=$!   # #1841: EIO mid-partition (writeback.sh)
    # shellcheck disable=SC2016,SC2046,SC2094 # $$ is sh -c's own; fifo opened once; partclone_mode flags are a word list
    { sh -c 'echo "$$" > "$1"; exec zstd -dc' zstd "$1/reader.pid" < "$1/feed" 2>> "$LOG_FILE"   # #1598: whoever reads the fifo (verdict.sh)
      echo "$?" > "$1/zstd.rc"; } \
        | "$2" $(partclone_mode "$2" -r) -s - -O "$3" -L "$1/partclone.log" 2>> "$LOG_FILE"
    _rp_rc=$?; kill "$_rp_w" 2>/dev/null; wait "$_rp_w" 2>/dev/null
    [ ! -f "$1/writeback.failed" ] || _rp_rc=1   # a refused fsync fails it, whatever partclone said
    echo "$_rp_rc" > "$1/$4"
}

restore_partition_drawers() {
    # $1=mode $2=server $3=image_id $4=index $5=fs $6=file $7=sha256 $8=uuid
    # $9..=disks
    _mode="$1"; _server="$2"; _image="$3"; _idx="$4"; _fs="$5"; _file="$6"
    _sha="$7"; _uuid="$8"
    shift 8
    _disks="$*"; cancel_pending "$@"   # #1386: a cancel asked while no stream ran

    # ‏swap אינה משודרת (אפיון סעיף 14): אין קובץ, אין sha256, ואין מה
    # להזרים — ‏fanout על רשומה כזו היה מזין את המגירות בזרם ריק ונכשל על
    # אי-התאמת sha256 (#49). כל מגירה בוראת אותה בעצמה, פעם אחת, על
    # המיקום שההרחבה כבר קבעה בזנב (#46). מגירה שנכשלה בה לא עוצרת את
    # השאר — כמו כל כשל אחר של מגירה בודדת.
    if is_swap_partition "$_fs"; then
        for _d in $_disks; do
            case "$(cat "$RUN_DIR/targets/$_d/state")" in failed|cancelled) continue ;; esac; [ ! -e "$RUN_DIR/targets/$_d/catchup" ] || continue   # drawerback.sh makes its own
            if assert_target "$_d" && make_swap "$(partition_node "$_d" "$_idx")" "$_uuid"; then   # #2434
                target_partition_done "$_d"
            else
                fail_written_target "$_d" "מחיצה $_idx: mkswap נכשל"
            fi
        done
        _any_alive "$_disks"
        return $?
    fi
    chunk_partition_drawers "$_server" "$_image" "$_idx" "$_fs" "$_file" "$_manifest" $_disks   # #2533 (chunkrecv.sh)
}

run_restore_drawers() {
    # $1 = mode, $2 = server, $3 = image id, $4 = manifest, $5.. = disks
    _mode="$1"; _server="$2"; _image="$3"; _manifest="$4"
    shift 4
    _disks="$*"
    round_closed_fail "$_disks" && { echo "failed" > "$RUN_DIR/state"; return 1; }   # 17/10: before any disk I/O
    # #3173: the chunk engine is the only multicast engine. Another mode is refused by name, before any disk I/O
    [ "$_mode" = chunk ] || { _fail_alive "$_disks" "מנוע הזרם '$_mode' אינו מנוע החתיכות -- הדיסק לא שונה"; echo "failed" > "$RUN_DIR/state"; return 1; }

    echo "writing" > "$RUN_DIR/state"
    # אותה בדיקה 2.7 של התחנה הבודדת, לכל מגירה בנפרד: מגירה קטנה מדי
    # נפסלת **לפני** ה---zap-all של apply_gpt, ובשמה (עד כאן "could not write
    # the partition table" על כונן קטן נראה כמו תקלת חומרה). ‏#1366: ואחריה
    # בדיקת הכתיבה והמהירות (writecheck.sh) — כל ההכרעות לפני הטבלה הראשונה.
    # רק מגירות הסבב מגיעות לכאן (#1275). מגירה שנפסלה אינה עוצרת את השאר.
    # shellcheck disable=SC2086 # the disk list is a word list on purpose
    RESUME_REST=$_disks; if command -v resume_round > /dev/null 2>&1; then resume_round "$_mode" "$_server" "$_image" "$_manifest" $_disks; fi; write_check_round "$_manifest" $RESUME_REST   # #2528: a drawer that resumes is not test-written (resume.sh)
    # #1704: all tables at once, a job per drawer (a writing sgdisk sleeps 1s, gdisk DiskSync:
    # ~11s a drawer in a row, metal 17/10). A job fails only its drawer; PREP_TAG = its own
    # scratch files.
    _tjobs=""
    for _d in $_disks; do
        case "$(cat "$RUN_DIR/targets/$_d/state")" in failed|cancelled) continue ;; esac
        ( PREP_TAG=".$_d"; resume_held "$_d" 2>/dev/null || discard_before_restore "$_d"; [ $? != 2 ] || { fail_written_target "$_d" "ה-TRIM לא הסתיים תוך $DISCARD_MAX_S שניות"; exit 0; }   # #1969
        stage_at "$_d" gpt 2>/dev/null   # #1883: the stage timeline (datareport.sh)
        if ! apply_gpt "$_d" "$_manifest"; then
            fail_written_target "$_d" "${PLAN_ERROR:-לא הצלחנו לכתוב את טבלת המחיצות}"
            exit 0
        fi
        # ההרחבה קורית לפני הזרם, בדיוק כמו בתחנה בודדת: מחיצת swap
        # נגררת עוברת לזנב עכשיו כדי שלא תחסום את מה שלפניה (#46).
        # מגירה שההרחבה נכשלה בה חוזרת לטבלת המניפסט -- או נכשלת בשם, אם מחיצות מכווצות היו מגיעות כך (#2405).
        stage_at "$_d" expand 2>/dev/null; if ! expand_last "$_d" "$_manifest"; then
            expand_fallback "$_d" "$_manifest" \
                || fail_written_target "$_d" "${PLAN_ERROR:-לא הצלחנו לכתוב את טבלת המחיצות}"
        fi ) &
        _tjobs="$_tjobs $_d:$!"
    done
    for _j in $_tjobs; do   # a drawer's write ceiling (waits.sh); past it, that drawer alone fails
        wait_pid "${_j#*:}" "$WAIT_DRAWER_S" "טבלת המחיצות של ${_j%%:*}" \
            || fail_written_target "${_j%%:*}" "פג הזמן — טבלת המחיצות לא נכתבה תוך $WAIT_DRAWER_S שניות"
    done
    _any_alive "$_disks" || { echo "failed" > "$RUN_DIR/state"; return 1; }

    # תוכנית לקובץ, לא לצינור: בלי pipefail קוד היציאה של manifest_plan
    # נבלע, לולאה על אפס שורות נראית כמו סיום, והמגירות → done (#426).
    # אותו דפוס כמו run_restore: מספר מול מספר, לא היעדר כישלון.
    _plan="$RUN_DIR/restore.plan"
    if ! manifest_plan "$_manifest" > "$_plan"; then
        _fail_alive "$_disks" "לא הצלחנו לקרוא את תוכנית המחיצות מהמניפסט"
        echo "failed" > "$RUN_DIR/state"
        return 1
    fi
    _expected=$(awk 'END { print NR }' "$_plan")
    _written=0; STREAMED_PARTITIONS=0; udp_drops_reset   # #957: הזרם הראשון של השחזור הזה (וסכום ה-drops שלו, 17/10)
    while IFS='|' read -r _i _g _role _fs _s _sz _f _sha _exp _ug _uuid _attrs <&3; do
        if is_swap_partition "$_fs"; then
            log "partition $_i (swap): recreated on each drawer, not fed"
        else
            log "partition $_i ($_role): feeding $(echo "$_disks" | wc -w) drawers"
        fi
        drawer_back before "$_i" "$_server" "$_image" $_disks; round_closed_fail "$_disks" && break   # drawerback.sh: a drawer back before it; 17/10: the server closed the round -- feed no more
        stage_at "$_disks" "p$_i" 2>/dev/null; restore_partition_drawers "$_mode" "$_server" "$_image" \
            "$_i" "$_fs" "$_f" "$_sha" "$_uuid" $_disks || break
        _written=$((_written + 1))
    done 3< "$_plan"
    if [ "$_expected" -lt 1 ] || [ "$_written" -ne "$_expected" ]; then
        _fail_alive "$_disks" "נכתבו $_written מתוך $_expected מחיצות"
        stage_at "$_disks" end 2>/dev/null; _final=$(_machine_state "$_disks"); echo "$_final" > "$RUN_DIR/state"   # #1386: all cancelled = partial
        [ "$_final" = "failed" ] && return 1; return 0
    fi

    drawer_back all "" "$_server" "$_image" $_disks; cancel_pending $_disks; echo "verifying" > "$RUN_DIR/state"   # drawerback.sh, before the read-back; #1386: one asked after the last partition
    # shellcheck disable=SC2086 # #1805: what each disk holds, before the filesystem grows
    stage_at "$_disks" readback 2>/dev/null; RB_SERVER=$_server RB_IMAGE=$_image readback_drawers "$_manifest" $_disks   # one fails alone; _machine_state below
    [ "$?" -ne 127 ] || _fail_alive "$_disks" "האימות אחרי הכתיבה לא רץ — readback.sh לא נטען"   # missing is not "verified"
    for _d in $_disks; do
        case "$(cat "$RUN_DIR/targets/$_d/state")" in failed|cancelled) continue ;; esac
        stage_at "$_d" grow 2>/dev/null; finish_grow "$_d" "" "$_manifest"
    done

    # שלושה מצבים, לא שניים (#67). ‏`partial` מסיים את הגל כמו `done`
    # ומחזיר 0: המכונה עשתה את חלקה בגל, והצעד הבא בחדר הוא כיבוי
    # והחלפת מגירות. מה שהוא *לא* עושה זה להיספר כהצלחה מלאה — המגירה
    # שנכשלה נשארת טרייה ונכתבת בגל הבא, והמסך מציג "הושלם חלקית"
    # במקום "הסתיים". (מה שמוצג על מסך המכונה עצמה נקבע ב-imagesrv-agent.)
    stage_at "$_disks" end 2>/dev/null; _final=$(_machine_state "$_disks")
    [ "$_final" = "failed" ] && { echo failed > "$RUN_DIR/state"; return 1; }
    return 0   # #1935: still 'verifying' -- done/partial is do_restore_drawers', after the drivers and the mark
}
