# tailshrink.sh -- #3262 (Nadav 14/11/2026, option A): a target smaller than the
# image by up to 64 MiB takes it when the LAST partition is a small NTFS that is
# not C: (the Windows recovery partition). POSIX sh (busybox ash).
#
# The way (the lab experiment of 14/11, "way 2"): the partition is restored into
# a RAM disk (brd) at its full size, the filesystem is shrunk THERE (ntfsresize
# -s), the shrunk part is written to the short partition, ntfsfix -d, and the
# result is checked. "Way 1" -- cut the tail, fix at the end -- is not here and
# must not come back: the experiment showed the last 439 MiB of a recovery
# partition IN USE (Winre.wim), the cut lands in used blocks, ntfsfix -d and
# ntfsresize -c say 0 and the file gives an I/O error: silent corruption.
#
# Everything else is refused by name (disk_fits): C: last, a partition that does
# not fit in RAM (MemAvailable - TAIL_SHRINK_RESERVE), a gap over 64 MiB, a
# non-NTFS, no free room. The switch (`tail_shrink_enabled`, server/agent/timing.py)
# is OFF by default until a clone boots (#2526). Off = today.
#
# Hooks (each one line, in files that are at the 300-line wall):
#   disk_fits      -> tail_shrink_admit    the decision; writes targets/<d>/tail_shrink
#   apply_gpt      -> tail_shrink_prepare  the RAM node + the manifest with the last partition cut
#   partition_node -> tail_shrink_node     the stream (and the read-back) go to the RAM node
#   finish_grow    -> tail_shrink_finish   after the read-back: shrink in RAM, write, fix, CHECK
# A check that failed = a failed target with the reason (principle 5). The exit
# code of ntfsfix/ntfsresize -c is not the evidence (the experiment: both said 0
# on a broken partition): the bytes written, the filesystem size read back and,
# for a recovery partition, the sha256 of its .wim files against the source are.

TAIL_SHRINK_MAX=67108864          # 64 MiB: the owner's ceiling on the gap
TAIL_SHRINK_HEADROOM=33554432     # 32 MiB left free after the cut (ntfsresize -n, in RAM, is the real arbiter)
TAIL_SHRINK_RESERVE=536870912     # 512 MiB of RAM that stay free for the stream and the agent
TAIL_SHRINK_ERROR=""
TS_MANIFEST=""

tail_shrink_take() {
    # $1 = hello answer. Only a literal true turns it on; a missing field is off.
    case "$(json_get "$1" ".tail_shrink_enabled")" in
        true) echo 1 > "$RUN_DIR/tail_shrink_on" ;;
        *) rm -f "$RUN_DIR/tail_shrink_on" ;;
    esac
}

tail_shrink_on() { [ "$(cat "$RUN_DIR/tail_shrink_on" 2>/dev/null)" = 1 ]; }

tail_shrink_plan() {
    # $1 = manifest, $2 = disk bytes. stdout: ok|<idx>|<cut, whole MiB>|<size>|<role> or no|<why>.
    # Cut = the layout's end + 262144 (required_bytes) - the disk. The last partition must be
    # an NTFS, measured, consistent, NOT the system partition (system_partition, diskfit.sh:
    # "ambiguous" or none = refused), and with the room left.
    _tp_sys=$(system_partition "$1")
    jq -r --argjson h "$2" --argjson max "$TAIL_SHRINK_MAX" --argjson head "$TAIL_SHRINK_HEADROOM" --arg sys "$_tp_sys" '
      (.sector_size // 512) as $ss
      | [.partitions[] | select((.start_sector | type) == "number" and (.size_bytes | type) == "number")] as $p
      | if ($p | length) == 0 or ($p | length) != (.partitions | length) then "no|אין גיאומטריה מלאה במניפסט"
        else ($p | max_by(.start_sector * $ss + .size_bytes)) as $l
          | ($l.start_sector * $ss + $l.size_bytes + 262144 - $h) as $gap
          | if $gap <= 0 then "no|ההפרש אינו חיובי"
            elif $gap > $max then "no|ההפרש \($gap) בייט, מעל 64MiB"
            elif (($l.fs // "") | ascii_downcase) != "ntfs" then "no|המחיצה האחרונה (\($l.index)) אינה NTFS"
            elif $sys == "" or $sys == "ambiguous" then "no|לא ידוע איזו מחיצה היא C:"
            elif ($l.index | tostring) == $sys then "no|המחיצה האחרונה (\($l.index)) היא C:"
            elif $l.ntfs_inconsistent == true then "no|ה-NTFS במחיצה האחרונה (\($l.index)) לא עקבי"
            elif ($l.used_bytes | type) != "number" then "no|used_bytes של המחיצה האחרונה (\($l.index)) לא נמדד"
            else ((($gap + 1048575) / 1048576 | floor) * 1048576) as $cut
              | if ($l.size_bytes - $l.used_bytes) < $cut + $head then "no|במחיצה האחרונה (\($l.index)) אין מקום פנוי להקטנה"
                else "ok|\($l.index)|\($cut)|\($l.size_bytes)|\($l.role // "")" end end end' "$1" 2>> "$LOG_FILE"
}

_ts_slots() { ls -d "$RUN_DIR"/ramslot.* 2> /dev/null | wc -l; }

tail_shrink_admit() {
    # $1 = disk, $2 = manifest, $3 = disk bytes. 0 = admitted: targets/<d>/tail_shrink =
    # "idx|cut|size|role" and a notice that SAYS the disk is shrunk. 1 = the refusal stands,
    # the reason goes to the log. RAM: the partition (x the drawers admitted so far) + the
    # reserve must be inside MemAvailable -- else refused by name.
    tail_shrink_on || return 1
    _ta=$(tail_shrink_plan "$2" "$3")
    case "$_ta" in
        ok\|*) ;;
        no\|*) log "tail shrink: $1 stays refused: ${_ta#no|}"; return 1 ;;
        *) log "tail shrink: $1 stays refused: no plan (jq failed)"; return 1 ;;
    esac
    _ta_r=${_ta#ok|}; _ta_i=${_ta_r%%|*}; _ta_r=${_ta_r#*|}; _ta_c=${_ta_r%%|*}; _ta_r=${_ta_r#*|}; _ta_z=${_ta_r%%|*}
    _ta_av=$(awk '/^MemAvailable:/ { print $2; exit }' "${SYSROOT:-}/proc/meminfo" 2> /dev/null)
    case "$_ta_av" in ''|*[!0-9]*) log "tail shrink: $1 stays refused: MemAvailable unreadable"; return 1 ;; esac
    _ta_n=$(( $(_ts_slots) + 1 ))
    if [ $((_ta_av * 1024)) -lt $((_ta_z * _ta_n + TAIL_SHRINK_RESERVE)) ]; then
        log "tail shrink: $1 stays refused: the partition ($_ta_z bytes) does not fit in RAM (MemAvailable ${_ta_av}KiB, reserve $TAIL_SHRINK_RESERVE)"
        return 1
    fi
    echo "$_ta_i|$_ta_c|$_ta_z|${_ta_r#*|}" > "$RUN_DIR/targets/$1/tail_shrink" || return 1
    _ta_t="הדיסק קטן מהאימג' — מחיצה $_ta_i תוקטן ב-$((_ta_c / 1048576)) MiB: היא נטענת לזיכרון, מערכת הקבצים NTFS מוקטנת שם, נכתבת למחיצה הקצרה ונבדקת"
    echo "$_ta_t" > "$RUN_DIR/targets/$1/notice"
    log "tail shrink: $1: $_ta_t"
}

tail_shrink_node() {
    # $1 = disk, $2 = idx. The RAM node while the partition is restored through RAM; 1 otherwise.
    _tn=$(cat "$RUN_DIR/targets/$1/tail_ram" 2> /dev/null) || return 1
    [ "${_tn%%|*}" = "$2" ] || return 1
    _tn=${_tn#*|}; echo "${_tn%%|*}"
}

_ts_ram_open() {
    # $1 = disk, $2 = idx, $3 = size. brd, one load for the round (rd_nr=16, pages are taken only
    # on write): the first drawer loads it, the others wait for the marker. A brd that is loaded
    # and not ours (the local clone's) is refused, as lcsnap.sh does.
    if mkdir "$RUN_DIR/ram.load" 2> /dev/null; then
        if [ -e "$SYSROOT/sys/module/brd" ] && [ ! -e "$RUN_DIR/ram.ours" ]; then
            echo "brd טעון ולא על ידי השחזור" > "$RUN_DIR/ram.fail"
        elif modprobe brd rd_nr=16 rd_size=$(( ($3 + 1023) / 1024 )) >> "$LOG_FILE" 2>&1; then
            : > "$RUN_DIR/ram.ours"; : > "$RUN_DIR/ram.loaded"
        else echo "המודול brd לא נטען" > "$RUN_DIR/ram.fail"; fi
    fi
    _tw=0
    while [ ! -e "$RUN_DIR/ram.loaded" ] && [ ! -e "$RUN_DIR/ram.fail" ] && [ "$_tw" -lt 150 ]; do sleep 0.1; _tw=$((_tw + 1)); done
    [ -e "$RUN_DIR/ram.loaded" ] || { PLAN_ERROR="מחיצה $2: דיסק הזיכרון לא נוצר ($(cat "$RUN_DIR/ram.fail" 2> /dev/null))"; return 1; }
    _tn=0
    while [ "$_tn" -lt 16 ]; do
        if mkdir "$RUN_DIR/ramslot.$_tn" 2> /dev/null; then
            node_is_block "$DEVROOT/ram$_tn" || { rmdir "$RUN_DIR/ramslot.$_tn"; PLAN_ERROR="מחיצה $2: התקן הזיכרון ram$_tn לא קיים"; return 1; }
            echo "$2|$DEVROOT/ram$_tn|$_tn" > "$RUN_DIR/targets/$1/tail_ram"; return 0
        fi
        _tn=$((_tn + 1))
    done
    PLAN_ERROR="מחיצה $2: אין דיסק זיכרון פנוי"; return 1
}

tail_shrink_prepare() {
    # $1 = disk, $2 = manifest. TS_MANIFEST = a copy with the last partition cut, "" = no plan
    # (the manifest stays). Idempotent: apply_gpt runs twice when the expansion fails.
    TS_MANIFEST=""
    _tp="$RUN_DIR/targets/$1/tail_shrink"
    [ -s "$_tp" ] || return 0
    IFS='|' read -r _tp_i _tp_c _tp_z _tp_r < "$_tp"
    case "$_tp_i$_tp_c$_tp_z" in ''|*[!0-9]*) PLAN_ERROR="תוכנית ההקטנה פגומה"; return 1 ;; esac
    [ -s "$RUN_DIR/targets/$1/tail_ram" ] || _ts_ram_open "$1" "$_tp_i" "$_tp_z" || return 1
    _tp_m="$RUN_DIR/tail_shrink.$1.json"
    jq --arg i "$_tp_i" --argjson c "$_tp_c" \
        '.partitions |= map(if (.index | tostring) == $i then .size_bytes -= $c else . end)' "$2" > "$_tp_m" 2>> "$LOG_FILE" \
        && [ -s "$_tp_m" ] || { PLAN_ERROR="לא הצלחנו לבנות את הטבלה החתוכה"; return 1; }
    TS_MANIFEST="$_tp_m"
}

tail_shrink_fs() {
    # $1 = the node holding the NTFS, $2 = the new size in bytes. 0 only when the size read back
    # after the shrink is a number at or below $2 (the budget first: -n).
    TAIL_SHRINK_ERROR=""
    printf 'y\n' | ntfsresize -n -s "$2" --no-progress-bar "$1" >> "$LOG_FILE" 2>&1; _tf_rc=$?
    [ "$_tf_rc" = 0 ] || { TAIL_SHRINK_ERROR="ntfsresize -n לא אישר את ההקטנה (rc=$_tf_rc)"; return 1; }
    printf 'y\n' | ntfsresize -s "$2" --no-progress-bar "$1" >> "$LOG_FILE" 2>&1; _tf_rc=$?
    [ "$_tf_rc" = 0 ] || { TAIL_SHRINK_ERROR="ntfsresize לא הקטין את מערכת הקבצים (rc=$_tf_rc)"; return 1; }
    _tf_got=$(shrink_fs_bytes "$1") || { TAIL_SHRINK_ERROR="לא הצלחנו לקרוא את גודל מערכת הקבצים אחרי ההקטנה"; return 1; }
    _tf_got=${_tf_got%%|*}
    [ "$_tf_got" -le "$2" ] || { TAIL_SHRINK_ERROR="מערכת הקבצים עדיין $_tf_got בייט, מעל $2"; return 1; }
}

_ts_sums() {
    # $1 = node, $2 = output file. sha256 of every .wim on the NTFS, mounted read-only. 1 = could not tell.
    _ts_m="$RUN_DIR/ts.mnt.$$"; mkdir -p "$_ts_m" || return 1
    ntfs-3g -o ro "$1" "$_ts_m" >> "$LOG_FILE" 2>&1 || { rmdir "$_ts_m"; return 1; }
    ( cd "$_ts_m" && find . -type f -iname '*.wim' -exec sha256sum {} + > "$2.raw" 2>> "$LOG_FILE" ); _ts_rc=$?
    umount "$_ts_m" >> "$LOG_FILE" 2>&1 || _ts_rc=1
    rmdir "$_ts_m" 2> /dev/null
    [ "$_ts_rc" = 0 ] && sort -k2 "$2.raw" > "$2"
}

_ts_head_sha() {
    # $1 = node, $2 = bytes (a multiple of 512). sha256 of the first $2 bytes.
    dd if="$1" bs=512 count=$(($2 / 512)) 2>> "$LOG_FILE" | sha256sum | cut -d' ' -f1
}

_tf_bad() {
    # $1 = disk, $2 = reason. tail_shrink_finish's failure exit; at file level so the kiosk scan and bash agree (#3267).
    rmdir "$RUN_DIR/ramslot.$_tf_slot" 2> /dev/null; fail_written_target "$1" "מחיצה $_tf_i: הקטנת המחיצה נכשלה — $2; הדיסק לא תקין"; return 1
}

tail_shrink_finish() {
    # $1 = disk. After the read-back of the RAM node (it holds the image's bytes, full size).
    # No mapping = nothing to do (0). Any step failing fails the target with its reason.
    [ -s "$RUN_DIR/targets/$1/tail_ram" ] || return 0
    IFS='|' read -r _tf_i _tf_c _tf_z _tf_role < "$RUN_DIR/targets/$1/tail_shrink"
    _tf_ram=$(tail_shrink_node "$1" "$_tf_i") || { fail_written_target "$1" "מחיצה $_tf_i: צומת הזיכרון אבד"; return 1; }
    _tf_slot=$(cut -d'|' -f3 "$RUN_DIR/targets/$1/tail_ram")
    rm -f "$RUN_DIR/targets/$1/tail_ram"          # from here partition_node answers with the real partition
    _tf_real=$(partition_node "$1" "$_tf_i"); _tf_new=$((_tf_z - _tf_c)); _tf_s="$RUN_DIR/ts.$1"
    case "$_tf_role" in recovery) _ts_sums "$_tf_ram" "$_tf_s.src" || { _tf_bad "$1" "לא הצלחנו לחשב sha של קבצי ה-wim במקור"; return 1; } ;; esac
    tail_shrink_fs "$_tf_ram" "$_tf_new" || { _tf_bad "$1" "$TAIL_SHRINK_ERROR"; return 1; }
    _tf_src=$(_ts_head_sha "$_tf_ram" "$_tf_new")
    dd if="$_tf_ram" of="$_tf_real" bs=512 count=$((_tf_new / 512)) >> "$LOG_FILE" 2>&1 || { _tf_bad "$1" "הכתיבה למחיצה הקצרה נכשלה"; return 1; }
    blockdev --flushbufs "$_tf_real" >> "$LOG_FILE" 2>&1 || { _tf_bad "$1" "flush של המחיצה הקצרה נכשל"; return 1; }
    [ -n "$_tf_src" ] && [ "$(_ts_head_sha "$_tf_real" "$_tf_new")" = "$_tf_src" ] || { _tf_bad "$1" "מה שנקרא מהמחיצה הקצרה שונה ממה שנכתב"; return 1; }
    ntfsfix -d "$_tf_real" >> "$LOG_FILE" 2>&1 || { _tf_bad "$1" "ntfsfix -d נכשל"; return 1; }
    ntfsfix -n "$_tf_real" >> "$LOG_FILE" 2>&1 || { _tf_bad "$1" "ntfsfix -n מצא שגיאה"; return 1; }
    ntfsresize -c --no-progress-bar "$_tf_real" >> "$LOG_FILE" 2>&1 || { _tf_bad "$1" "ntfsresize -c מצא שגיאה"; return 1; }
    _tf_got=$(shrink_fs_bytes "$_tf_real") || { _tf_bad "$1" "גודל מערכת הקבצים במחיצה הקצרה לא נקרא"; return 1; }
    _tf_got=${_tf_got%%|*}; _tf_have=$(blockdev --getsize64 "$_tf_real" 2>> "$LOG_FILE")
    case "$_tf_have" in ''|*[!0-9]*) _tf_bad "$1" "גודל המחיצה הקצרה לא נקרא"; return 1 ;; esac
    [ "$_tf_got" -le "$_tf_have" ] || { _tf_bad "$1" "מערכת הקבצים ($_tf_got) גדולה מהמחיצה ($_tf_have)"; return 1; }
    case "$_tf_role" in recovery)
        _ts_sums "$_tf_real" "$_tf_s.dst" || { _tf_bad "$1" "לא הצלחנו לחשב sha של קבצי ה-wim במחיצה הקצרה"; return 1; }
        cmp -s "$_tf_s.src" "$_tf_s.dst" || { _tf_bad "$1" "ה-sha של קבצי ה-wim במחיצה הקצרה שונה מהמקור"; return 1; } ;;
    esac
    rmdir "$RUN_DIR/ramslot.$_tf_slot" 2> /dev/null
    [ "$(_ts_slots)" != 0 ] || { rmmod brd >> "$LOG_FILE" 2>&1; rm -rf "$RUN_DIR/ram.load" "$RUN_DIR/ram.loaded" "$RUN_DIR/ram.fail" "$RUN_DIR/ram.ours"; }
    log "tail shrink: $1: partition $_tf_i shrunk to $_tf_new bytes and checked"
}
