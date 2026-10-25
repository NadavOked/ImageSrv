# progress.sh -- how the server learns what this machine is doing: the
# progress report (interfaces.md section 4).
# Pure sh assembly so it is testable without jq.
# POSIX sh (busybox ash).
#
# A report needs a session id. For a class round it arrives in the hello
# answer; for a unicast pull nothing hands it over and the station asks for
# one -- that request is pull.sh, split out in #562 and sourced right after
# this file, because pull_close ends a job through report_final below.
#
# State lives in files, so the reporter can run as a background process:
#   $RUN_DIR/state                 top-level state word
#   $RUN_DIR/targets/<dev>/state   waiting/writing/verifying/done/failed
#   $RUN_DIR/targets/<dev>/bytes.<n> per-partition counters; bytes.raw points at current
#   $RUN_DIR/targets/<dev>/counter the file the live counter is read from
#   $RUN_DIR/targets/<dev>/total   total compressed bytes for the whole image
#   $RUN_DIR/targets/<dev>/error   error text (only when failed)
#   $RUN_DIR/targets/<dev>/crc_delta  CRC(199) rise over this round (#872; absent = not measured)
#   $RUN_DIR/targets/<dev>/since   uptime at target_init -- the dmesg window of a failure (#874)
#   $RUN_DIR/targets/<dev>/ata_log.json, ident   the kernel lines and serial|port|ata_port
#                                  of a failed target (failmark.sh; absent = no evidence)
#   $RUN_DIR/drivers.json          driver staging outcome (postdeploy.sh, #720; absent = did not run)
#
# The live counter is a *pointer*, not a fixed name, because the two restore
# paths measure in different places. A classroom station has one pv per
# target and writes bytes.raw. A cloning machine has one pv for the whole
# machine -- the same number for all three drawers, which is no measurement
# of any of them (#25) -- so its drawers point this at fanout's per-drawer
# counter instead. Whoever runs the write declares where the truth is; the
# reporter never guesses between two files.

target_init() {
    # $1 = dev, $2 = bytes_total for the full image.
    _t="$RUN_DIR/targets/$1"
    mkdir -p "$_t"
    echo "waiting" > "$_t/state"
    rm -f "$_t/base" "$_t/bytes.raw" "$_t"/bytes.[0-9]*
    : > "$_t/bytes.0"; ln -s "bytes.0" "$_t/bytes.raw"; echo 0 > "$_t/counter.index"
    echo "$_t/bytes.raw" > "$_t/counter"
    echo "$2" > "$_t/total"
    rm -f "$_t/error" "$_t/crc_delta" "$_t/temp_after" "$_t/ata_log" "$_t/ata_log.json" "$_t/ident" "$_t/evidence_only" \
        "$_t/boot_ca_state" "$_t/boot_ca_reason" "$_t/write_check_state" "$_t/write_check_reason" "$_t/write_check_mbps" \
        "$_t/write_started" "$_t/ntfs_boot" "$_t/dead" "$_t/pulled" "$_t/readback" "$_t/readback_reason" "$_t/readback_why" "$_t/stages" "$_t/raid_wipe" "$_t/share.ok"   # #1805; #1883; #1907; #1808/#1898
    target_ident_keep "$1"   # #1602: serial|port|ata while it is still in sysfs (failmark.sh)
    # ‏#874: מאיפה dmesg נספר בכשל -- ריק (אין /proc/uptime) = מההתחלה.
    cut -d' ' -f1 "$SYSROOT/proc/uptime" > "$_t/since" 2>/dev/null
}
job_reset() { echo waiting > "$RUN_DIR/state"; rm -f "$RUN_DIR/stage" "$RUN_DIR/drivers.json" "$RUN_DIR/udp_drops.json" "$RUN_DIR/udp_drops.sum" "$RUN_DIR/cdetail.txt" "$RUN_DIR/cdetail.txt.old"; for _jr in "$RUN_DIR"/targets/*/*; do case "${_jr##*/}" in shrunk*|shrink_id) ;; *) rm -rf "$_jr" ;; esac; done; }   # #1916: before a job's reporter starts, nothing of the last job's report is left (a disk left shrunk is not report: shrinkoffer.sh); udp_drops = the last round's (waits.sh); #1931: cdetail = the last capture's table (capturedetail.sh)

target_write_started() {
    # The serial binds later failure cleanup to the disk this run wrote.
    _tw_s=$(disk_serial "$1")
    [ -n "$_tw_s" ] || log "$1: no serial -- a failure here keeps its GPT (#1427)"   # loop/VM: no wipe without identity
    mkdir -p "$RUN_DIR/targets/$1" && printf '%s\n' "$_tw_s" > "$RUN_DIR/targets/$1/write_started"
}
target_counter() {
    # $2 receives pv-format lines; it points at this partition's stable file.
    _tc_t="$RUN_DIR/targets/$1"; _tc_i=$(cat "$_tc_t/counter.index" 2>/dev/null); _tc_i=${_tc_i:-0}
    rm -f "$2"; ln -s "$_tc_t/bytes.$_tc_i" "$2"
    echo "$2" > "$_tc_t/counter"
}
_counter_of() {
    _c=$(cat "$RUN_DIR/targets/$1/counter" 2>/dev/null)
    [ -n "$_c" ] || _c="$RUN_DIR/targets/$1/bytes.raw"
    echo "$_c"
}

target_set() {
    # $1 = dev, $2 = state, $3 = optional error text.
    _t="$RUN_DIR/targets/$1"
    echo "$2" > "$_t/state"
    [ -n "${3:-}" ] && echo "$3" > "$_t/error"
}

# ‏#100: המונה הוא ה-stderr של `pv`, ולכן הוא מכיל גם את הודעות השגיאה
# שלו — ``pv: write failed: Broken pipe`` נכתב לאותו קובץ בדיוק ברגע
# שה-curl של ההעלאה נופל. ‏`tail -n 1` הרים את השורה הזאת אל
# ‏``$((...))``. מדוד תחת busybox ash: תת-המעטפת של `$( )` מתה והערך
# חזר **ריק**, ומשם שתי תוצאות — ``"bytes_written":,`` שהוא JSON פגום
# שהשרת פוסל כולו, וקובץ `base` ריק שמאפס את הבייטים של כל המחיצות
# שכבר הסתיימו. ‏`|| echo 0` לא היה עוזר: החשבון מת לפני שיש קוד יציאה.
_last_number() {
    # השורה **המספרית** האחרונה. לא השורה האחרונה, ולא `n + 0`:
    # "אין שורה מספרית" ו"אפס" הם שני מצבים שונים. ‏`waits.sh` מבדיל
    # ביניהם כדי לדעת אם הבייט הראשון הגיע, והחזרת 0 קבוע נראתה לו
    # כמו התקדמות — כלומר החליפה את תקרת ההמתנה. הקוראים כאן
    # מוסיפים 0 בעצמם, מיד אחרי הקריאה.
    tr -d '\r' < "$1" 2>/dev/null \
        | awk '/^[0-9]+$/ { n = $0 } END { if (n != "") print n }'
}

target_bytes() {
    # Sum the stable, per-partition counters (#1657).
    _t="$RUN_DIR/targets/$1"; _sum=0; _n=
    for _f in "$_t"/bytes.[0-9]*; do
        [ -f "$_f" ] || continue
        _n=$(_last_number "$_f"); _sum=$((_sum + ${_n:-0}))
    done
    echo "$_sum"
}

target_partition_done() {
    # Point the next writer at a new counter; keep the old one.
    _t="$RUN_DIR/targets/$1"
    _i=$(cat "$_t/counter.index" 2>/dev/null); _i=$((${_i:-0} + 1))
    : > "$_t/bytes.$_i"; _c=$(_counter_of "$1")
    ln -s "$_t/bytes.$_i" "$_c.next" && mv "$_c.next" "$_c"
    echo "$_i" > "$_t/counter.index"
}

_source_progress_json() {
    # $1 = dev. Prints ,"source_progress":{...} for the partition partclone is
    # reading now, or nothing at all when the denominator or the percent is
    # unknown (#435). The axis is the *uncompressed* one -- partclone's own
    # block count -- because the pv counter measures compressed bytes and their
    # total cannot be known before the stream ends. "Unknown" prints nothing on
    # purpose: the console then draws an indeterminate bar, never a measured 0%
    # (עיקרון 5). Only the capture path calls this (build_progress gates on the
    # task id); a round measures compressed bytes and a block axis would wrongly
    # win over them in guistate.sh.
    #
    # ‏Starting to clone (/dev/sdaN) פותח מחיצה ומאפס: פסקת הלוג האחרונה
    # לבדה נספרת, ולכן `Completed:` של מחיצה קודמת אינו נמשך אל החדשה. כל
    # השוואה מספרית עם `+0` — תחת busybox awk השוואת מחרוזת-למספר מכריעה
    # לפי תווים, ו-"754688" > "1665979" יצא אמת.
    # ‏#1254: את שורת `Completed:` partclone מדפיס ל-stderr (`\r` לפני כל
    # עדכון), לא ל-`-L` — capture.sh שומר אותו ב-partclone.err, מאופס לכל
    # מחיצה. ממנו רק האחוז; המחיצה והמכנה מה-`-L`. ‏K מתוך M — מקום המחיצה
    # ב-parts.txt (הטבלה שהקליטה עוברת עליה); בלי הטבלה — בלי K/M.
    _sp_log="$RUN_DIR/targets/$1/partclone.log"; _sp_err="$RUN_DIR/targets/$1/partclone.err"
    [ -f "$_sp_log" ] || return 0
    { tr '\r' '\n' < "$_sp_log"; [ ! -f "$_sp_err" ] || { echo "@@stderr@@"; tr '\r' '\n' < "$_sp_err"; }; } | awk -v pf="$RUN_DIR/parts.txt" '
        $0 == "@@stderr@@" { err = 1; next }
        err { if ($0 ~ /Completed:/) { c = $0; sub(/.*Completed:[ \t]*/, "", c); sub(/%.*/, "", c)
                if (c ~ /^[0-9]+(\.[0-9]+)?$/) epct = c }; next }
        /Starting to clone/ { part=""; total=""; pct="";
            if (match($0, /\/dev\/[A-Za-z0-9]+/)) {
                d = substr($0, RSTART, RLENGTH)
                if (match(d, /[0-9]+$/)) part = substr(d, RSTART) } }
        /Space in use/ { if (match($0, /[0-9]+[ ]*Blocks/)) {
            b = substr($0, RSTART, RLENGTH); gsub(/[^0-9]/, "", b); total = b } }
        /Completed:/ { c = $0; sub(/.*Completed:[ \t]*/, "", c); sub(/%.*/, "", c);
            if (c ~ /^[0-9]+(\.[0-9]+)?$/) pct = c }
        END {
            if (epct != "") pct = epct
            if (part == "" || total == "" || total + 0 <= 0 || pct == "") exit
            r = int(pct / 100.0 * total)
            if (r + 0 > total + 0) r = total; if (r + 0 < 0) r = 0
            while ((getline l < pf) > 0) { split(l, f, "|"); if (f[1] ~ /^[0-9]+$/) { n++; if (f[1] + 0 == part + 0) k = n } }
            km = k ? sprintf(",\"partition_seq\":%d,\"partitions_total\":%d", k, n) : ""
            printf ",\"source_progress\":{\"partition\":%d,\"blocks_read\":%d,\"blocks_total\":%d%s}", \
                part, r, total, km }'
}

build_progress() {
    # $1 = session id, $2 = mac, $3 = task id (capture instead of a round).
    # Exactly one of session/task identifies the work -- see interface 4.
    _state=$(cat "$RUN_DIR/state" 2>/dev/null || echo "waiting")
    _targets=""
    for _d in "$RUN_DIR"/targets/*/; do
        [ -f "$_d/state" ] || continue   # #1916: a target this job never initialised is not this job's
        _dev=$(basename "$_d")
        _tstate=$(cat "$_d/state" 2>/dev/null || echo "waiting")
        _total=$(cat "$_d/total" 2>/dev/null || echo 0)
        _entry=$(printf '{"dev":"%s","bytes_written":%s,"bytes_total":%s,"state":"%s"' \
            "$_dev" "$(target_bytes "$_dev")" "$_total" "$_tstate")
        # ‏#435: רק בקליטה (task id). המכנה בציר הבלוקים, לצד bytes_* הדחוסים.
        [ -n "${3:-}" ] && _entry="$_entry$(_source_progress_json "$_dev")$(cd_json 2>/dev/null)"
        [ -f "$_d/error" ] && _entry="$_entry,\"error\":\"$(json_escape "$(cat "$_d/error")")\""
        [ -f "$_d/crc_delta" ] && _entry="$_entry,\"crc_delta\":$(cat "$_d/crc_delta")"   # ‏#872: רק כשנמדד; חסר = לא נקרא, לא 0
        case "$(cat "$_d/readback" 2>/dev/null)" in verified|sampled|unverified) _entry="$_entry,\"readback\":\"$(cat "$_d/readback")\",\"readback_reason\":\"$(json_escape "$(cat "$_d/readback_reason" 2>/dev/null)")\""; if [ "$(cat "$_d/readback_why" 2>/dev/null)" = list_not_fetched ]; then _entry="$_entry,\"readback_why\":\"list_not_fetched\""; fi ;; esac   # #1805: absent = not read back (yet); #1907
        case "$(cat "$_d/temp_after" 2>/dev/null)" in ''|*[!0-9]*) ;; *) _entry="$_entry,\"temp_c\":$(cat "$_d/temp_after")" ;; esac   # #1859: after the write
        if [ -s "$_d/boot_ca_state" ] && [ -s "$_d/boot_ca_reason" ]; then
            _entry="$_entry,\"boot_ca_preflight\":{\"state\":\"$(json_escape "$(cat "$_d/boot_ca_state")")\",\"reason\":\"$(json_escape "$(cat "$_d/boot_ca_reason")")\"}"
        fi
        _wm=$(cat "$_d/write_check_mbps" 2>/dev/null); case "$_wm" in ''|*[!0-9]*) _wm=null ;; esac   # #1366: null = not measured, never 0
        [ -s "$_d/write_check_state" ] && _entry="$_entry,\"write_preflight\":{\"state\":\"$(json_escape "$(cat "$_d/write_check_state")")\",\"reason\":\"$(json_escape "$(cat "$_d/write_check_reason" 2>/dev/null)")\",\"mbps\":$_wm}"
        # ‏#874: יעד שנכשל נושא סידורי, חריץ ושורות ה-ATA (failmark.sh) -- הראיה, לא סיווג.
        [ -f "$_d/ata_log.json" ] && _entry="$_entry$(failure_json "$_dev")"
        [ -s "$_d/drivers.json" ] && _entry="$_entry,\"drivers\":$(tr -d '\n' < "$_d/drivers.json")"   # #1269
        _entry="$_entry$(target_extra_json "$_dev" 2>/dev/null)}"   # #1883: the stages, once final (datareport.sh)
        [ -n "$_targets" ] && _targets="$_targets,"
        _targets="$_targets$_entry"
    done
    if [ -n "${3:-}" ]; then
        _who=$(printf '"task_id":"%s"' "$3")
    else
        _who=$(printf '"session_id":"%s"' "$1")
    fi
    # #720: driver staging outcome (postdeploy.sh); absent = did not run, not no_match.
    _drivers=""; [ -s "$RUN_DIR/udp_drops.json" ] && _drivers=",\"udp_drops\":$(tr -d '\n' < "$RUN_DIR/udp_drops.json")"   # 17/10 rate policy; absent = not read
    [ -s "$RUN_DIR/drivers.json" ] && _drivers="$_drivers,\"drivers\":$(tr -d '\n' < "$RUN_DIR/drivers.json")"
    [ -n "${3:-}" ] && [ -s "$RUN_DIR/stage" ] && _drivers="$_drivers,\"stage\":\"$(json_escape "$(cat "$RUN_DIR/stage")")\""   # #980 §4: task reports only
    printf '{%s,"mac":"%s","state":"%s","targets":[%s]%s}' \
        "$_who" "$2" "$_state" "$_targets" "$_drivers$(machine_extra_json "$1" "${3:-}" 2>/dev/null)"   # #1885: cpu, once final
}

progress_send() {
    # $1 = session id, $2 = mac, $3 = server URL, $4 = task id (optional).
    # One report, synchronously, and the exit code of the send.
    #
    # The loop below sends and forgets, which is right for a report that will
    # be repeated in two seconds. The *last* report of a job is not repeated:
    # it is what tells the server the work ended. That one gets an answer read.
    build_progress "$1" "$2" "${4:-}" > "$RUN_DIR/progress.json"
    # ‏#557: התשובה נשמרת במקום להיזרק. הקוד היוצא נשאר הקוד היוצא —
    # מי שקורא לזה בלולאה ממשיך להתעלם ממנו — אבל **גוף התשובה זמין
    # למי שכן צריך לדעת**, וזה מה שעוצר לולאה על סבב שנסגר.
    # ‏#855: דיווח על משימה נושא את אסימון המשימה (‏`TASK_TOKEN` מה-hello,
    # ‏#530) — אותה כותרת שההעלאה שולחת. לסבב (session) אין אסימון.
    http_post_json "$3/api/v1/agent/progress" "$RUN_DIR/progress.json" \
        "${4:+X-Imagectl-Task-Token: ${TASK_TOKEN:-}}" \
        > "$RUN_DIR/progress.reply" 2>/dev/null
}

progress_refused_for_good() {
    # האם התשובה האחרונה אומרת שאין טעם לנסות שוב.
    #
    # ⚠️ **רק `not_open`, ורק הוא.** שגיאת רשת, ‏500, או תשובה ריקה
    # הן "לא ידענו" — ולולאה שנעצרת עליהן היא מכונה שמפסיקה לדווח
    # כי השרת אותחל לרגע. **"לא הצלחנו לשאול" אינו "נענינו בלא".**
    # ‏`json_get` מקבל **נתיב קובץ**, לא מחרוזת — העברת התוכן עצמו
    # מחזירה `null` בשקט, וזה נראה בדיוק כמו "אין קוד".
    [ -s "$RUN_DIR/progress.reply" ] || return 1
    case "$(json_get "$RUN_DIR/progress.reply" ".code")" in
        not_open) return 0 ;;
        *)        return 1 ;;
    esac
}

progress_loop() {
    # $1 = session id, $2 = mac, $3 = server URL, $4 = task id (optional).
    #
    # The `|| true` here is the right one and it stays: a report that will be
    # repeated in two seconds owes nobody an answer. What must never travel
    # through this loop is the *last* report -- see report_final.
    while :; do
        progress_send "$1" "$2" "$3" "${4:-}" || true
        # ‏#557: `|| true` נשאר — דיווח שיחזור בעוד שתי שניות אינו חייב
        # תשובה. אבל **סבב שנסגר לא יפתח את עצמו מחדש** (08/09: 50 דקות על
        # סבב סגור). ברזל 17/10: והמסלול הראשי חייב לדעת -- הוא המשיך לכתוב
        # מול שולח שכבר יצא. הסמן ב-$RUN_DIR עוצר אותו (drawers.sh). #1928: והסיבה, לפני הסמן.
        if progress_refused_for_good; then
            _pl_w=$(json_get "$RUN_DIR/progress.reply" ".reason"); log "progress: the server says this session is closed ($_pl_w) -- stopping"
            [ "$_pl_w" = null ] || printf '%s\n' "$_pl_w" > "$RUN_DIR/session-closed.why"; printf '%s\n' "$1" > "$RUN_DIR/session-closed"; return 0
        fi
        cancel_from_reply; sleep "${PROGRESS_INTERVAL_S:-2}"   # #1386: "בטל דיסק זה" (cancel.sh)
    done
}

# --- the last report of a job (#101) -----------------------------------------
#
# Three paths end a job, and until #101 only one of them read the answer. The
# other two stopped the loop and slept: `sleep 6  # let 'done' reach the
# server`. Six seconds is a hope, not evidence -- with --max-time 10 --retry 3
# they may not hold one completed attempt -- and for a class round the cost of
# guessing wrong is not a missing line on a screen. `session_members.done`
# stays 0, the session stays `running`, and the machine's next hello is
# answered with the same restore: it writes the same 40GB again, up to the
# boot guard's budget (#75), and is finally labelled "boot loop" rather than
# "the closing report never arrived".
#
# So the closing report is sent from here: the loop is stopped first (one
# writer on progress.json), the send is synchronous, and the exit code is the
# answer. A report the server refused comes back as 400, and `curl -sfS`
# turns that into a non-zero exit -- so 0 means 200, and nothing else does.
#
# The ceiling counts tries and not seconds, because a try is a whole curl with
# its own retries: a number of seconds here would be a second guess about how
# long one of them takes. Five tries against a server that is not answering
# is minutes of real attempts, which covers a server being restarted; what it
# does not cover is a server that is gone, and that is the caller's business.
FINAL_REPORT_TRIES="${FINAL_REPORT_TRIES:-5}"
FINAL_REPORT_GAP_S="${FINAL_REPORT_GAP_S:-3}"

report_final() {
    # $1 = reporter pid ("" when no loop is running), $2 = session id,
    # $3 = mac, $4 = server URL, $5 = task id (optional).
    # 0 only on positive evidence that the server took the report.
    [ -n "${1:-}" ] && kill "$1" 2>/dev/null
    _f_try=0
    while :; do
        _f_try=$((_f_try + 1))
        if progress_send "$2" "$3" "$4" "${5:-}"; then
            [ "$_f_try" -gt 1 ] && log "the final report got through on try $_f_try"
            return 0
        fi
        if progress_refused_for_good; then
            [ -n "$2" ] && printf '%s\n' "$2" > "$RUN_DIR/server-restarted-wave"
            log "the final report was not repeated: the server says the task is closed"
            return 0
        fi
        [ "$_f_try" -ge "$FINAL_REPORT_TRIES" ] && break
        log "the final report was not accepted (try $_f_try of $FINAL_REPORT_TRIES)"
        sleep "$FINAL_REPORT_GAP_S"
    done
    log "WARNING: the server did not acknowledge the final report after" \
        "$FINAL_REPORT_TRIES tries"
    return 1
}
