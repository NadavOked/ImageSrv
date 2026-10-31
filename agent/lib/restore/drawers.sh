# drawers.sh -- cloning-room mode: one stream, several drawers, isolated.
# POSIX sh (busybox ash).
#
# The stream arrives once and is fanned out to a per-drawer pipeline. Each
# drawer writes on its own, so a slow or failing drive stops only itself
# (interfaces.md section 7). The isolation lives in `fanout`, which reports
# each drawer by name; this file turns that report into the per-target states
# of interface 4. Where the stream is decompressed -- once for the machine, or
# once per drawer -- is drawerfeed.sh.

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
    # file. zstd -d | partclone for the single restore (#1906); the drawers'
    # pipelines are drawerfeed.sh's (_drawer_pipeline, #1746).
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
            case "$(cat "$RUN_DIR/targets/$_d/state")" in failed|cancelled) continue ;; esac
            if make_swap "$(partition_node "$_d" "$_idx")" "$_uuid"; then
                target_partition_done "$_d"
            else
                fail_written_target "$_d" "מחיצה $_idx: mkswap נכשל"
            fi
        done
        _any_alive "$_disks"
        return $?
    fi

    drawer_tune "$_idx" $_disks; _how="$DRAWER_DECODE"   # drawerfeed.sh: one decoder or one per drawer
    _feeds=""
    for _d in $_disks; do
        _t="$RUN_DIR/targets/$_d"
        case "$(cat "$_t/state")" in failed|cancelled) continue ;; esac   # #1386
        rm -f "$_t/feed" "$_t/feed.bytes" "$_t/pipeline.rc" "$_t/pipeline.pid" \
              "$_t/zstd.rc" "$_t/reader.pid"
        mkfifo "$_t/feed"
        # ההתקדמות של המגירה הזו נמדדת אצל fanout, לא אצל ה-pv שלפניו:
        # ‏pv אחד מודד את הזרם של המכונה — אותו מספר לשלוש המגירות, ולכן
        # אינו מדידה של אף אחת מהן, וכל הפסים נשארו על 0% (#25).
        target_counter "$_d" "$_t/feed.bytes"
        _drawer_pipeline "$_d" "$_fs" "$_idx" "$_how"
        _feeds="$_feeds $_t/feed"
    done
    [ -n "$_feeds" ] || { log "no drawers left"; return 1; }
    _rings=$(echo "$_feeds" | wc -w); [ "$_how" = each ] || _rings=$((_rings + 1))   # + the decoder's
    # shellcheck disable=SC2086 # word splitting of the fifo list is wanted
    drawer_feed_start "$_how" "$(_fanout_buffer "$_rings")" $_feeds

    # sha256 is taken on the compressed bytes as received, once for the
    # machine: every drawer is fed the same bytes, so one check covers them all.
    # #1438: inside fanout (-S hash, -P counter in pv -n format), not
    # pv|tee|sha256sum -- busybox tee copies 1KiB per read. -S is written only
    # at a clean EOF. In the background, watching the counter: a stream that
    # never moved a byte must still end in a report.
    # shellcheck disable=SC2086 # word splitting of the fifo list is wanted
    rm -f "$RUN_DIR/sha.out" "$RUN_DIR/fanout.rc" "$RUN_DIR/source.rc" "$RUN_DIR/bytes.raw"  # #1430: old count != 1st byte
    next_stream_ceiling   # #957: המחיצה הראשונה ממתינה למפעיל, הבאות לחדר
    udp_drops_mark "$_mode"   # #1571: did this machine drop the stream itself?
    (
        # ה-rc של המקור נלכד לחוד: ב-POSIX sh ‏$? של צינור הוא של האחרון
        # בלבד, ולכן זרם שנקטע באמצע (‏udp-receiver שפקע, ‏curl שנפל)
        # הגיע לכאן כ-fanout שיצא 0 על בייטים חלקיים (#73).
        { stream_source "$_mode" "$_server" "$_image" "$_file" "$STREAM_START_CEILING"
          echo "$?" > "$RUN_DIR/source.rc"; } \
            | feed_fanout -S "$RUN_DIR/sha.out" -P "$RUN_DIR/bytes.raw" \
                "$(_fanout_buffer "$_rings")" $FEED_OUT > "$FEED_REPORT" 2>> "$LOG_FILE"
        echo "$?" > "$RUN_DIR/fanout.rc"
    ) &
    _streampid=$!; WAIT_ABORT_FILE="$RUN_DIR/session-closed"   # ברזל 17/10: סגירת הסבב עוצרת את הזרם
    drawer_watch "${FEED_WATCH:-$_streampid}" "$_idx" "$_disks" & _watchpid=$!   # #1598: a dead drawer is out in seconds
    if wait_progress "$_streampid" "$RUN_DIR/bytes.raw" \
            "$STREAM_START_CEILING" "$WAIT_STREAM_STALL_S" \
            "הזרם של מחיצה $_idx ($_file)"; then
        _fanout_rc=$(cat "$RUN_DIR/fanout.rc" 2>/dev/null || echo 1)
    else
        _fanout_rc="$WAIT_TIMED_OUT"
    fi
    udp_drops_log "$_mode" "partition $_idx"
    # shellcheck disable=SC2086 # the disk list is a word list on purpose
    _dec_why=""; [ "$_how" = each ] || drawer_feed_finish "$_idx" $_disks   # one decoder: its exit is every drawer's
    wait_pid "$_watchpid" "$WAIT_HELPER_S" "מעקב המגירות (מחיצה $_idx)" || :; cancel_pending "$@"   # #1386: one it did not finish

    # ממתינים לצינור לפי PID: בלי זה pipeline.rc עוד לא קיים (#20),
    # ו-wait ריק תופס גם את דמון ההתקדמות (#12). תקרה: fifo שלא נפתח
    # (#49) לא ייגמר מעצמו; אחרי פקיעת הזרם התקרה הקצרה.
    # ‏#1598: פקיעת זרם = stream (בלי ראיית ATA → בלי disk_failure); מגירה dead לא ממתינים לה.
    _dwait="$WAIT_DRAWER_S"; _blame=""
    [ "$_fanout_rc" = "$WAIT_TIMED_OUT" ] && { _dwait="$WAIT_HELPER_S"; _blame=stream; }
    for _d in $_disks; do
        _pidf="$RUN_DIR/targets/$_d/pipeline.pid"
        [ -f "$_pidf" ] && [ ! -f "$RUN_DIR/targets/$_d/dead" ] || continue
        wait_pid "$(cat "$_pidf")" "$_dwait" "המגירה $_d (מחיצה $_idx)" && continue
        # בידוד: המגירה הזו לבדה נכשלת, הלולאה ממשיכה לשכנות.
        unblock_fifo "$RUN_DIR/targets/$_d/feed"
        fail_written_target "$_d" \
            "מחיצה $_idx: פג הזמן — המגירה לא סיימה לכתוב" "$_blame"
    done

    # ה-sha התואם הוא הראיה החיובית שהזרם הגיע שלם, ולכן הוא נשאל ראשון:
    # מקור שיצא בקוד מוזר על זרם שאומת אינו כישלון. כשאין ראיה — שואלים
    # **למה**, ולכל סיבה הודעה משלה (#73). עד כאן היה ענף sha אחד שבלע גם
    # את המקרים שבהם הבדיקה כלל לא נעשתה: ‏fanout שנהרג (‏OOM, ‏#21) או
    # מקור שנקטע משאירים בייטים חלקיים, ה-sha בהכרח אינו תואם, וההודעה
    # שיצאה שלחה את הטכנאי לחפש בספרייה אימג' פגום שאינו קיים.
    _src=$(cat "$RUN_DIR/source.rc" 2>/dev/null)
    _got=$(awk '{print $1}' "$RUN_DIR/sha.out" 2>/dev/null)
    _why=""
    # grep: 0 = found, 1 = not found (an answer!), 2 = the check broke.
    grep -q ' ok$' "$RUN_DIR/fanout.out" 2>/dev/null
    _ok_rc=$?
    if [ -n "$_dec_why" ]; then
        _why="$_dec_why"
    elif is_sha256 "$_got" && [ "$_got" = "$_sha" ]; then
        _why=""
    elif [ "$_fanout_rc" = "$WAIT_TIMED_OUT" ]; then
        _why="פג הזמן -- הזרם לא הגיע"; round_closed && _why="$(round_closed_why)"
    elif [ "$_fanout_rc" != "0" ] && [ "$_fanout_rc" != "1" ]; then
        # ‏2 של fanout, ‏137 של OOM (#21) = ההפצה עצמה נפלה.
        _why="הפצת הזרם למגירות נכשלה (fanout rc=${_fanout_rc:-לא נרשם})"
    elif ! is_sha256 "$_got" && { [ "$_fanout_rc" != "1" ] || [ "$_ok_rc" = "0" ]; }; then
        # (rc=1 with no ok line is judged below: fanout stopped before EOF
        # when the last drawer died, so -S wrote nothing by design -- #1438.)
        # אין ערך שנקרא בחזרה, ולכן אין ראיה גם לכך שהבייטים שגויים.
        _why="ה-sha256 לא חושב -- אין ראיה שהזרם הגיע שלם"
    elif [ "$_fanout_rc" = "1" ]; then
        # מגירה נפלה, וה-sha אינו תואם. שאלת ה**ייחוס** (#440) ושאלת
        # ה**שלמות** (#520) נפרדות, וההבחנה ביניהן היא **האם מישהו שרד**:
        #
        #   אין שורת ok  -- כל המגירות מתו, ‏fanout הפסיק לקרוא, המקור
        #                   קיבל 141 **בגללנו**, ואין sha (‏-S רק ב-EOF).
        #                   זו תקלת דיסק, ואין להאשים את הרשת (#440).
        #   יש שורת ok   -- הזרם נקרא עד סופו, והבייטים נמדדו ונמצאו
        #                   שגויים. המגירה ההיא עומדת להיחתם ‏done,
        #                   ואסור בלי ראיה (#520).
        if [ "$_ok_rc" = "0" ]; then
            _why="אי-התאמת sha256 בקובץ $_file"
        elif [ "$_ok_rc" = "1" ]; then
            _why=""
        else
            _why="דוח fanout לא נקרא -- אין ראיה מי מהמגירות שרדה"
        fi
    elif [ "$_src" != "0" ] && [ "$_src" != "141" ]; then
        # fanout ראה EOF. ‏141 = אנחנו סגרנו את stdout, לא הרשת.
        _why="הזרם נקטע לפני סופו (מקור rc=${_src:-לא נרשם})"
    else
        # The bytes themselves were wrong: every drawer got the same bad data.
        _why="אי-התאמת sha256 בקובץ $_file"
    fi
    if [ -n "$_why" ]; then
        log "partition $_idx ($_file): $_why -- failing all drawers"
        for _d in $_disks; do   # ‏#1598: כשל של הזרם, לא של הכונן; dead שומרת את סיבתה
            [ -f "$RUN_DIR/targets/$_d/dead" ] || fail_written_target "$_d" "מחיצה $_idx: $_why" stream
        done
        return 1
    fi

    _read_fanout_report "$_idx" "$_fs" $_disks
    _any_alive "$_disks"
}

run_restore_drawers() {
    # $1 = mode, $2 = server, $3 = image id, $4 = manifest, $5.. = disks
    _mode="$1"; _server="$2"; _image="$3"; _manifest="$4"
    shift 4
    _disks="$*"
    round_closed_fail "$_disks" && { echo "failed" > "$RUN_DIR/state"; return 1; }   # 17/10: before any disk I/O

    echo "writing" > "$RUN_DIR/state"
    # אותה בדיקה 2.7 של התחנה הבודדת, לכל מגירה בנפרד: מגירה קטנה מדי
    # נפסלת **לפני** ה---zap-all של apply_gpt, ובשמה (עד כאן "could not write
    # the partition table" על כונן קטן נראה כמו תקלת חומרה). ‏#1366: ואחריה
    # בדיקת הכתיבה והמהירות (writecheck.sh) — כל ההכרעות לפני הטבלה הראשונה.
    # רק מגירות הסבב מגיעות לכאן (#1275). מגירה שנפסלה אינה עוצרת את השאר.
    # shellcheck disable=SC2086 # the disk list is a word list on purpose
    write_check_round "$_manifest" $_disks
    # #1704: all tables at once, a job per drawer (a writing sgdisk sleeps 1s, gdisk DiskSync:
    # ~11s a drawer in a row, metal 17/10). A job fails only its drawer; PREP_TAG = its own
    # scratch files.
    _tjobs=""
    for _d in $_disks; do
        case "$(cat "$RUN_DIR/targets/$_d/state")" in failed|cancelled) continue ;; esac
        ( PREP_TAG=".$_d"; discard_before_restore "$_d"; [ $? != 2 ] || { fail_written_target "$_d" "ה-TRIM לא הסתיים תוך $DISCARD_MAX_S שניות"; exit 0; }   # #1969
        stage_at "$_d" gpt 2>/dev/null   # #1883: the stage timeline (datareport.sh)
        if ! apply_gpt "$_d" "$_manifest"; then
            fail_written_target "$_d" "${PLAN_ERROR:-לא הצלחנו לכתוב את טבלת המחיצות}"
            exit 0
        fi
        # ההרחבה קורית לפני הזרם, בדיוק כמו בתחנה בודדת: מחיצת swap
        # נגררת עוברת לזנב עכשיו כדי שלא תחסום את מה שלפניה (#46).
        # מגירה שההרחבה נכשלה בה חוזרת לטבלת המניפסט ונכתבת בגודל המקורי.
        stage_at "$_d" expand 2>/dev/null; if ! expand_last "$_d" "$_manifest"; then
            log "WARNING: expansion failed on $_d -- writing at the image's own size"
            apply_gpt "$_d" "$_manifest" \
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
        round_closed_fail "$_disks" && break   # 17/10: the server closed the round -- feed no more
        stage_at "$_disks" "p$_i" 2>/dev/null; restore_partition_drawers "$_mode" "$_server" "$_image" \
            "$_i" "$_fs" "$_f" "$_sha" "$_uuid" $_disks || break
        _written=$((_written + 1))
    done 3< "$_plan"
    if [ "$_expected" -lt 1 ] || [ "$_written" -ne "$_expected" ]; then
        _fail_alive "$_disks" "נכתבו $_written מתוך $_expected מחיצות"
        stage_at "$_disks" end 2>/dev/null; _final=$(_machine_state "$_disks"); echo "$_final" > "$RUN_DIR/state"   # #1386: all cancelled = partial
        [ "$_final" = "failed" ] && return 1; return 0
    fi

    cancel_pending "$@"; echo "verifying" > "$RUN_DIR/state"   # #1386: one asked after the last partition
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
