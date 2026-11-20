# drawerback.sh -- a drawer pulled mid-round and pushed back in finishes its write (#2533).
# POSIX sh (busybox ash).
#
# Metal, 13/11 21:41 (cloner 2, the chunk engine): drawer 3 was pulled mid-pass
# and pushed back in. The kernel found it by itself, under a NEW name (sdd ->
# sde) with the same WWN -- and the agent left it failed. #3108.
#
# At the round's fixed points -- after each answer of the server inside a
# partition (chunkrecv.sh), before each partition and before the read-back
# (drawers.sh) -- and, since #3181, whenever the KERNEL says a disk was pushed
# in, inside a pass too (drawerplug.sh): a target that failed because it was PULLED (`pulled`,
# verdict.sh) is looked for again by its serial and size (`bound`, failmark.sh),
# under any name and in any slot -- and only when it ANSWERS a read, past the
# page cache (drawer_answers): a drawer whose link went down while its node
# stayed in sysfs (#2045) has the same serial and name, and is not back. Found:
#
#   - its target directory follows it to the new name, and the report keeps the
#     name the round chose it by (`dev`, progress.sh): the server's inventory and
#     the console know the drawer by that name, and see it go back to "writing";
#   - its identity is bound again (target_bind: the slot may be another one);
#   - what it lacks is completed IN THE BACKGROUND (`targets/<d>/catchup`) --
#     since #3181 the pass on the air first, then a neighbour, and HTTP (the
#     repair fetch's own way, _chunk_fetch) for the rest: _db_part, which is
#     drawerfill.sh's. In the background: metal 13/11
#     22:58, room_714f980d -- a completion run inside the partition held the
#     machine 13 minutes, the server waited its 180 s for it to settle on the
#     next partition and closed the round, and the healthy drawer (96%) failed
#     with it. A drawer one target is completing never holds the others: the
#     round's passes leave it out (_chunk_live, _chunk_body), and its completion
#     has a directory of its own (`back.<dev>`) and touches none of the round's
#     files;
#   - it completes the partitions the round has files for (`at`: the last one
#     begun), in order; one that ended while it was out, and the one it was
#     pulled in -- what chunkcast had made durable on it is kept (`p<n>.kept`:
#     the next run of the partition drops the line of a target it is not given),
#     and only the rest is brought. NTFS backup boot sector and the count, as
#     for any drawer;
#   - when it is level with the round at a partition's start, it joins that
#     partition's stream and is a drawer like the others. Behind, it goes on in
#     the background. Before the read-back the round waits for it -- the others
#     are written by then -- and the read-back and the incomplete-write mark are
#     the same code for all;
#   - a completion that does not end whole fails it by name ("the drawer came
#     back, and its completion failed: ..."), once: it is not tried again.
#
# The ranges are the agent's own -- what the index has, minus what chunkcast says
# is durable on the drawer -- and not the server's: the server keeps a disk it
# was told failed out of its plan for that partition, and a partition that ended
# has no plan any more. The evidence is the drawer's line in its state file
# after the fetch, whole -- not the fetch's exit code (principle 5).
#
# Another disk (another serial) pushed into a slot does NOT join a regular round:
# it is said in the log, once (#3064). In a production line it is a new target
# of the round (#3181): prodline.sh, which sees it here and lists it here.


_db_note() { if command -v round_note > /dev/null 2>&1; then round_note "$@"; fi; }   # #3125: the server's journal learns it too (roundnote.sh)

_db_line() { awk -v n="$2" '$1 == n { print; f = 1 } END { exit !f }' "$1" 2>/dev/null; }   # $1 = state file, $2 = node

_db_keep() {   # $1 = index, $2.. = disks: a pulled drawer's line, before the next run of the partition drops it
    _dk_i="$1"; shift
    for _dk_d in "$@"; do
        [ -f "$RUN_DIR/targets/$_dk_d/pulled" ] || continue
        _dk_n=$(partition_node "$_dk_d" "$_dk_i")
        _db_line "$CHUNK_DIR/p$_dk_i.kept" "$_dk_n" > /dev/null && continue
        # not there (or not read) = nothing kept: the whole partition is fetched again, never less
        _db_line "$CHUNK_STATE" "$_dk_n" >> "$CHUNK_DIR/p$_dk_i.kept"
    done
}

_db_strangers() {   # $@ = the round's disks: another disk pushed in mid-round is said once, and does not join (#3064) -- a production line takes it (#3181)
    _ds_p="$CHUNK_DIR/present.$CHUNK_SESSION"
    if [ ! -f "$_ds_p" ]; then   # the first look of the round: what is here is the round's
        for _ds_n in $(list_drawers); do echo "$_ds_n|$(disk_serial "$_ds_n")"; done > "$_ds_p"; return 0
    fi
    for _ds_n in $(list_drawers); do
        _ds_s=$(disk_serial "$_ds_n")
        if cat "$_ds_p" "$_ds_p.said" 2>/dev/null | grep -qxF "$_ds_n|$_ds_s"; then continue; fi   # there when the round began, or said
        for _ds_t in "$@"; do [ "$(target_bound_serial "$_ds_t")" != "$_ds_s" ] || continue 2; done   # a target's own disk
        if command -v prodline_seen > /dev/null 2>&1 && prodline_seen "$_ds_n" "$_ds_s"; then continue; fi   # #3181: the production line's -- seen, and said, there
        echo "$_ds_n|$_ds_s" >> "$_ds_p.said"
        log "דיסק אחר הוכנס באמצע הסבב ($_ds_n, serial ${_ds_s:-לא ידוע}) -- הוא אינו מצטרף לסבב הזה (#3064)"
        _db_note drawer_stranger "$_ds_n|$_ds_s" "דיסק אחר הוכנס באמצע הסבב ($_ds_n, serial ${_ds_s:-לא ידוע}) -- הוא אינו מצטרף לסבב הזה"
    done
}

_db_found() {   # $1 = target -> the name its disk is back under. 1 = not back, or not one to look for
    _df_t="$RUN_DIR/targets/$1"
    if command -v drawer_left_mark > /dev/null 2>&1; then drawer_left_mark "$1" || :; fi   # #3270: failed by a refused write, and its disk is gone since: pulled
    [ "$(cat "$_df_t/state" 2>/dev/null)" = failed ] && [ -f "$_df_t/pulled" ] || return 1
    [ ! -f "$_df_t/foreign" ] && [ ! -f "$_df_t/back_failed" ] || return 1
    _df_b=$(cat "$_df_t/bound" 2>/dev/null) || return 1
    _df_k=${_df_b%|*}   # serial|sectors: the slot may be another one
    case "$_df_k" in '|'*|*'|') return 1 ;; esac   # no serial or no size: nothing to know it by
    for _df_n in $(list_drawers); do
        [ "$(_target_key "$_df_n")" = "$_df_k" ] && drawer_answers "$_df_n" && { printf '%s' "$_df_n"; return 0; }
    done
    return 1
}

drawer_answers() {   # $1 = disk. 0 = its first 4 KiB were read, past the page cache, within the helpers' ceiling
    rm -f "$CHUNK_DIR/answers.rc"
    { dd if="$DEVROOT/$1" bs=4096 count=1 iflag=direct > /dev/null 2>&1; echo "$?" > "$CHUNK_DIR/answers.rc"; } &
    wait_pid "$!" "$WAIT_HELPER_S" "קריאת בדיקה מ-$1" > /dev/null || return 1   # a dying disk holds a read: not for ever
    [ "$(cat "$CHUNK_DIR/answers.rc" 2> /dev/null)" = 0 ]
}


# _db_part -- one partition of a completion: the pass on the air, a neighbour, HTTP -- is drawerfill.sh's (#3181).

_db_catch() {   # $1 = old name, $2 = new name. In the background: each partition it lacks, up to the last one the round began.
    # 0 = level with it. 1 = failed (DB_WHY). 2 = no longer written (failed or cancelled meanwhile, by another's word).
    DB_K=0; DB_WHY=""; _dc_t="$RUN_DIR/targets/$2"
    _dc_done=$(cat "$_dc_t/counter.index" 2> /dev/null)   # how many partitions it ended (target_partition_done)
    case "$_dc_done" in ''|*[!0-9]*) DB_WHY="לא ידוע כמה מחיצות הסתיימו על המגירה"; return 1 ;; esac
    [ -s "$RUN_DIR/restore.plan" ] || { DB_WHY="תוכנית המחיצות לא נמצאה"; return 1; }
    _dc_pos=0
    while IFS='|' read -r _dc_i _ _ _dc_fs _ _ _dc_f _ _ _ _dc_uuid _ <&6; do
        if [ "$_dc_pos" -ge "$_dc_done" ]; then
            # not begun: the drawer joins its stream (drawer_back). Begun since, its first pass on the air: it listens to it (#3181, drawerfill.sh)
            [ "$_dc_pos" -le "$(cat "$_db_main/at" 2> /dev/null || echo -1)" ] || _fl_onair "$_dc_i" || break
            [ "$(cat "$_dc_t/state" 2> /dev/null)" = writing ] || return 2
            if is_swap_partition "$_dc_fs"; then
                assert_target "$2" && make_swap "$(partition_node "$2" "$_dc_i")" "$_dc_uuid" || { DB_WHY="מחיצה $_dc_i: mkswap נכשל"; return 1; }
            else
                _db_part "$1" "$2" "$_dc_i" "$_dc_f" || return 1
                ntfs_boot_target "$2" "$_dc_i" "$_dc_fs" || { DB_WHY="$NTFS_BOOT_ERROR"; return 1; }
            fi
            [ "$(cat "$_dc_t/state" 2> /dev/null)" = writing ] || return 2
            target_partition_done "$2"; log "השלמה: מחיצה $_dc_i שלמה על $2"
        fi
        _dc_pos=$((_dc_pos + 1))
    done 6< "$RUN_DIR/restore.plan"
}

_db_start() {   # $1 = old name, $2 = new name: its completion, in the background -- nobody waits for it but the read-back
    _db_main="$CHUNK_DIR"
    (   CHUNK_BG=1; CHUNK_DIR="$_db_main/back.$2"; mkdir -p "$CHUNK_DIR" || exit 1
        _db_catch "$1" "$2"; _ds_rc=$?
        [ "$_ds_rc" != 1 ] || : > "$RUN_DIR/targets/$2/back_failed"   # once: not tried again
        # failed meanwhile by its own evidence (chunkwatch.sh: pulled again, a write error) keeps that reason
        [ "$_ds_rc" != 1 ] || [ "$(cat "$RUN_DIR/targets/$2/state" 2> /dev/null)" != writing ] || fail_written_target "$2" "המגירה חזרה, וההשלמה שלה נכשלה: $DB_WHY"
        [ "$_ds_rc" != 0 ] || log "השלמה: $DB_K טווחים -- המגירה $2 הגיעה לסבב"
    ) &
    echo "$! $1 $(cat "$RUN_DIR/targets/$2/counter.index" 2> /dev/null)" > "$RUN_DIR/targets/$2/catchup"
}

_db_tick() {   # $@ = the round's disks: each one completing in the background -- at it still, level (it joins), or behind (on it goes)
    _dt_total=$(grep -c . "$RUN_DIR/restore.plan" 2> /dev/null)
    for _dt_d in "$@"; do
        _dt_m="$RUN_DIR/targets/$_dt_d/catchup"; [ -f "$_dt_m" ] || continue
        read -r _dt_pid _dt_old _dt_from < "$_dt_m"
        if kill -0 "$_dt_pid" 2> /dev/null; then continue; fi
        _dt_next=$(cat "$RUN_DIR/targets/$_dt_d/counter.index" 2> /dev/null)
        if [ "$(cat "$RUN_DIR/targets/$_dt_d/state" 2> /dev/null)" != writing ]; then rm -f "$_dt_m"   # failed by name, or cancelled
        elif [ "$_dt_next" = "$_dt_from" ] && [ "$_dt_next" -le "$_db_at" ]; then   # it had work and did none: not tried again
            rm -f "$_dt_m"; : > "$RUN_DIR/targets/$_dt_d/back_failed"; fail_written_target "$_dt_d" "המגירה חזרה, וההשלמה שלה לא התקדמה"
        elif [ "$_dt_next" -ge "$_dt_total" ] && [ "$_db_mode" != in ]; then rm -f "$_dt_m"; log "המגירה $_dt_d הושלמה עד הסוף ברקע"
            # inside a partition it stays out: the round's end of it counts the drawers of ITS state file, and this one is not there
        elif [ "$_db_mode" = before ] && [ "$_dt_next" -eq $((_db_at + 1)) ]; then
            rm -f "$_dt_m"; log "המגירה $_dt_d הושלמה עד מחיצה $_db_idx -- מצטרפת לזרם שלה"
        elif [ "$_dt_next" -le "$_db_at" ]; then _db_start "$_dt_old" "$_dt_d"   # behind the round: on it goes
        fi
    done
}

_db_join() {   # $1 = target, $2 = the name its disk is back under, $3.. = the round's disks. DB_NAME = the target's name after
    _dj_d="$1"; _dj_n="$2"; shift 2; DB_NAME="$_dj_d"; _dj_t="$RUN_DIR/targets/$_dj_d"
    if [ "$_dj_n" != "$_dj_d" ]; then
        case " $* " in *" $_dj_n "*) _dj_busy=1 ;; *) _dj_busy=0 ;; esac   # the name of another target of the round
        if [ "$_dj_busy" = 1 ] || { [ -d "$RUN_DIR/targets/$_dj_n" ] && ! rmdir "$RUN_DIR/targets/$_dj_n" 2>/dev/null; } \
                || ! mv "$_dj_t" "$RUN_DIR/targets/$_dj_n"; then
            : > "$_dj_t/back_failed"
            log "המגירה $_dj_d חזרה בשם $_dj_n, והשם תפוס -- היא נשארת בחוץ"; return 1
        fi
        DB_NAME="$_dj_n"; _dj_t="$RUN_DIR/targets/$_dj_n"
        [ -s "$_dj_t/dev" ] || echo "$_dj_d" > "$_dj_t/dev"   # the name it was chosen by: what the report says (progress.sh)
        _db_rename "$_dj_d" "$_dj_n"
    fi
    _dj_slot=$(disk_port "$_dj_n")
    _dj_say="מגירה ${_dj_slot:-?} חזרה (serial $(target_bound_serial "$_dj_n"), $_dj_n) -- משלימה ברקע, השאר ממשיכות"
    log "$_dj_say"; _db_note drawer_back "$_dj_n@$(date +%s)" "$_dj_say"
    # #3181: how long it was out, for the report (`rejoin`, drawerfill.sh) -- the mark's own time; not read = not said, never 0
    _dj_p=$(stat -c %Y "$_dj_t/pulled" 2> /dev/null) && echo $(($(date +%s) - _dj_p)) > "$_dj_t/away" || rm -f "$_dj_t/away"
    rm -f "$_dj_t/pulled" "$_dj_t/dead" "$_dj_t/error"; drawer_window_open "$_dj_n" || :   # pulled again later = heard again; #3270: the kernel is heard from NOW (drawerplug.sh)
    for _dj_f in ata_log ata_log.json; do [ ! -f "$_dj_t/$_dj_f" ] || mv "$_dj_t/$_dj_f" "$_dj_t/$_dj_f.pulled"; done   # the pull's evidence was reported
    target_counter "$_dj_n" "$_dj_t/feed.bytes"   # its counter's link held the old name
    if ! target_bind "$_dj_n"; then
        : > "$_dj_t/back_failed"; log "המגירה ${_dj_slot:-?} חזרה, והזהות שלה לא נקבעה מחדש -- היא נשארת בחוץ"; return 1
    fi
    target_ident_keep "$_dj_n"; target_set "$_dj_n" writing
    _db_start "$_dj_d" "$_dj_n"
}

_db_rename() {   # $1 = old name, $2 = new: what the round's end reads by name follows the drawer (metal 14/11, room_1838cf62)
    # `_all` (imagesrv-agent): the end's count, screen and power-off (end_tally, end_countdown). Under the old name
    # there is no target any more -- "state not read", the results held until 'כבה' -- as `_disks` is renamed below.
    _dr_l=""; for _dr_a in ${_all:-}; do [ "$_dr_a" != "$1" ] || _dr_a="$2"; _dr_l="$_dr_l $_dr_a"; done; _all="${_dr_l# }"
    # the SMART reading before the write ($RUN_DIR/smart/<d>.*): the CRC delta's baseline (crcdelta.sh), the report's
    # attributes -- the same disk (serial and size, _db_found). Not .hw0: the pull's kernel lines are not its hardware.
    for _dr_f in "$RUN_DIR/smart/$1".*; do
        [ -f "$_dr_f" ] || continue
        case "$_dr_f" in *.hw0) rm -f "$_dr_f" ;; *) mv -f "$_dr_f" "$RUN_DIR/smart/$2.${_dr_f##*/"$1".}" ;; esac
    done
}

drawer_back() {   # $1 = before|in|all, $2 = partition index, $3 = server, $4 = image, $5.. = the round's disks.
    # before: ahead of partition $2 (drawers.sh). in: inside it, between its runs (chunkrecv.sh). all: ahead of the read-back,
    # which waits here for every completion. 0 = a drawer is back under another name, and _disks is the list with it.
    _db_mode="$1"; _db_idx="$2"; _db_srv="$3"; _db_img="$4"; shift 4
    [ "${STREAM_MODE:-}" = chunk ] || return 1
    mkdir -p "$CHUNK_DIR" || return 1
    # `at` = the last partition whose files the round wrote: a completion goes up to it, and never past
    case "$_db_mode" in
        before) _db_at=$(($(_db_pos "$_db_idx" || echo 0) - 1)) ;;
        in)     _db_at=$(_db_pos "$_db_idx") || _db_at=-1; _db_keep "$_db_idx" "$@" ;;
        *)      _db_at=$(($(grep -c . "$RUN_DIR/restore.plan" 2> /dev/null || echo 0) - 1)) ;;
    esac
    echo "$_db_at" > "$CHUNK_DIR/at"
    _db_strangers "$@"
    _db_list=""; _db_rc=1; PL_ADDED=""; if command -v prodline_apply > /dev/null 2>&1; then prodline_apply; fi   # #3181: a new disk that writes joins the list; the target whose slot it took left it
    for _db_d in "$@" $PL_ADDED; do
        [ -d "$RUN_DIR/targets/$_db_d" ] && [ ! -e "$RUN_DIR/targets/$_db_d/unlisted" ] || { _db_rc=0; continue; }
        case " $_db_list " in *" $_db_d "*) continue ;; esac   # listed once, whoever named it
        if _db_n=$(_db_found "$_db_d"); then
            _db_join "$_db_d" "$_db_n" "$@"
            [ "$DB_NAME" = "$_db_d" ] || { _db_d="$DB_NAME"; _db_rc=0; }
        fi
        _db_list="$_db_list $_db_d"
    done
    [ -z "$PL_ADDED" ] || _db_rc=0
    [ "$_db_rc" = 1 ] || _disks="${_db_list# }"
    _db_tick ${_db_list}
    # #3181: a new disk still completing does not hold the drawers that are written -- it ends by itself, after them (prodline_finish)
    if [ "$_db_mode" = all ] && command -v prodline_late > /dev/null 2>&1 && prodline_late ${_db_list}; then _db_rc=0; fi
    # before the read-back: the round has nothing more to write, and waits for each completion to end, one way or another
    while [ "$_db_mode" = all ] && _db_busy; do
        _chunk_beat; sleep 1; _db_tick ${_db_list}
    done
    [ "$_db_mode" != all ] || drawer_plug_stop   # nothing more is written: the kernel's word is not listened for (drawerplug.sh)
    return "$_db_rc"
}

drawer_catchup_stop() {   # $1 = the target's name. #3112: "cancel this disk" met a drawer completing in the background (cancel_drawer)
    # The completion is a subshell (`catchup`: its pid) and the chunkcast apply it runs (back.<d>/apply.pid, chunkfetch.sh), both
    # killed -- the subshell first, or it starts the next partition's fetch -- and waited for: the GPT goes only after the writer
    # has stopped (cancel_drawer), or the apply goes on writing into a disk that no longer looks like a system. A curl left
    # with no reader dies on its next write (SIGPIPE). Not stopped within the helpers' ceiling = said, and the wipe goes on.
    # #3181: the completion now also runs a receiver of its own and a neighbour's fill (drawerfill.sh, neighborfill.sh), with
    # writers this file has no pid of -- so every process below the subshell goes with it (_db_kill_tree).
    _cs_t="$RUN_DIR/targets/$1"; read -r _cs_s _ < "$_cs_t/catchup" 2> /dev/null || return 0
    _cs_a=$(cat "${CHUNK_DIR:-$RUN_DIR/chunk}/back.$1/apply.pid" 2> /dev/null)
    _db_kill_tree "$_cs_s"; [ -z "$_cs_a" ] || kill -9 "$_cs_a" 2> /dev/null
    wait_pid "$_cs_s" "$WAIT_HELPER_S" "השלמת המגירה $1 (ביטול)" || :
    [ -z "$_cs_a" ] || wait_pid "$_cs_a" "$WAIT_HELPER_S" "ההורדה של המגירה $1 (ביטול)" || :
    for _cs_p in $_kt_all; do   # the rest of them: gone already, or waited for like the two above
        if [ "$_cs_p" != "$_cs_s" ] && [ "$_cs_p" != "$_cs_a" ] && kill -0 "$_cs_p" 2> /dev/null; then wait_pid "$_cs_p" "$WAIT_HELPER_S" "תהליך של השלמת המגירה $1 (ביטול)" || :; fi
    done
    rm -f "$_cs_t/catchup" "${CHUNK_DIR:-$RUN_DIR/chunk}/back.$1/apply.pid"   # the pid file: its process did not get to remove it
    log "$1: its completion in the background was stopped before its GPT was wiped (cancel)"
}

_db_kill_tree() {   # $1 = pid: it and every process below it (_kt_all). Each is STOPPED as it is found -- none forks while the list is made -- then all are killed
    _kt_all=" $1 "; _kt_new="$1"
    [ -r /proc/self/stat ] || log "WARNING: /proc is not read -- only the completion's own shell is stopped, what it started is not known"
    while [ -n "$_kt_new" ]; do
        # shellcheck disable=SC2086 # pids: a word list on purpose
        kill -s STOP $_kt_new 2> /dev/null
        _kt_new=$(cat /proc/[0-9]*/stat 2> /dev/null | sed -n 's/^\([0-9][0-9]*\) .*) [A-Za-z] \([0-9][0-9]*\) .*/\1 \2/p' \
            | awk -v all="$_kt_all" '!index(all, " " $1 " ") && index(all, " " $2 " ") { printf "%s ", $1 }')
        _kt_all="$_kt_all$_kt_new"
    done
    # shellcheck disable=SC2086
    kill -9 $_kt_all 2> /dev/null
}

_db_busy() { for _db_b in $_db_list; do [ ! -f "$RUN_DIR/targets/$_db_b/catchup" ] || [ -f "$RUN_DIR/targets/$_db_b/late" ] || return 0; done; return 1; }   # a completion still on (`late`: a new disk's, not waited for -- prodline.sh)
_db_pos() { awk -F'|' -v i="$1" '$1 == i { print NR - 1; f = 1; exit } END { exit !f }' "$RUN_DIR/restore.plan" 2> /dev/null; }   # index -> its place in the plan
