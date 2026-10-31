# lcsnap.sh -- #1466: shrink a copy-on-write VIEW of the source, never the
# source (owner, 16/10) -- for the local clone (lcplan.sh) and for the
# capture (shrink.sh) alike. POSIX sh (busybox ash).
#
# Each NTFS partition the plan shrinks gets a device-mapper snapshot over it
# (dm-snapshot, transient "N", 4 KiB chunks = NTFS's cluster) whose
# copy-on-write store is a RAM disk (brd, one per view). ntfsresize -s and
# ntfsfix -d run on /dev/mapper/lc-src-<idx>, partclone reads it
# (lc_src_node), and the source partition is only ever read: no ntfsresize,
# no ntfsfix, no sgdisk, no rereadpt on the source disk, and no server record
# (#926) -- there is nothing to put back. The compacted layout reaches the
# targets through the manifest exactly as in place (_shrink_compact). What
# partclone reads is not the snapshot itself but a dm `linear` cut of its
# first <new length> sectors (_snap_cut): ntfsresize shrinks the volume, the
# device keeps the partition's old length, and partclone -c records the
# DEVICE size -- a target partition of the new size then refuses the image
# ("Target partition size is smaller than source", Testrunner 16/10).
#
# The budget, before anything is written to a view: `ntfsresize -n -s <new>`
# on the view (a read-only run; a stray write would land in RAM) prints
# "Needed relocations : N (M MB)". A view needs M + M/10 + LC_SNAP_META_MB
# ($LogFile reset, $MFT/$Bitmap writes, the kernel's exception table). Each
# view's store is (MemAvailable - LC_SNAP_RESERVE_MB) / views, and that is
# the brd device's size: the kernel cannot give a view more, so the store
# cannot run the machine out of memory -- a full store invalidates the view
# (ntfsresize gets an I/O error, loudly). Relocations that were not read, a
# need over the store, a missing dmsetup or module, a table that does not
# read back -- return 3 + LC_SNAP_WHY (Hebrew, with the numbers): the caller
# refuses the smaller target (owner 16/10, option B: never in place). "Could
# not check" is never "fits" (principle 5).
#
# #2208 (metal 26/10, cloner 2: a 870 EVO left in Fast Startup, a 256GB
# target): a view whose NTFS is hibernated has the hibernation dropped on the
# VIEW first -- ntfs_unhibernate (ntfsunhib.sh, #2009's), read back clean --
# then it shrinks. Its budget is LC_SNAP_UNHIB_MB more. The targets' "done" is
# orange ($_sr/src_unhib, lc_unhibernate_target); a drop that fails refuses
# the smaller target by name, and a view whose state does not read is refused.
# Capture uses mode `capture` (#2213): full-size entries need only the drop,
# shrinking entries share the same views; the local clone's `unhib` is unchanged.

LC_SNAP_RESERVE_MB="${LC_SNAP_RESERVE_MB:-1024}"   # the copy's own buffers (fanout, partclone)
LC_SNAP_META_MB="${LC_SNAP_META_MB:-256}"
LC_SNAP_UNHIB_MB="${LC_SNAP_UNHIB_MB:-128}"   # #2208: hiberfil.sys deleted, $LogFile emptied -- 65 MB on metal (cloner 2, 26/10)
LC_SNAP_CHUNK=8   # sectors
LC_SNAP_WHY=""; LC_SNAP_TD=""

# The workspace: the local clone's run directory, or the capture's
# (LC_SNAP_RUN, shrink.sh). Read again at every entry point.
_snap_ws() { _sr="${LC_SNAP_RUN:-${LC:-$RUN_DIR/lc}/run}"; }

# No udev in the initramfs: libdevmapper makes and removes the /dev/mapper
# nodes itself (DM_DISABLE_UDEV), instead of waiting for a udev that never answers.
_snap_dm() { DM_DISABLE_UDEV=1 dmsetup "$@"; }

_lc_relocs() {
    # $1 = ntfsresize output. The MB of every "Needed relocations" line, summed;
    # nothing printed when there is no such line (not measured, not 0).
    sed -n 's/.*Needed relocations *: *[0-9][0-9]* (\([0-9][0-9]*\) MB).*/\1/p' "$1" 2>/dev/null \
        | awk '{ s += $1; n++ } END { if (n) print s }'
}

lc_src_node() {
    # $1 = source, $2 = idx. What partclone reads: this run's shrunk view, or the partition.
    _snap_ws; _sv_n=$(cat "$_sr/snap/$2.node" 2>/dev/null)
    if [ -n "$_sv_n" ]; then echo "$_sv_n"; else partition_node "$1" "$2"; fi
}

_snap_valid() {
    # $1 = view name. 0 + _sv_use (allocated/total sectors of its store) = the
    # view is valid. "Invalid", "Overflow", no answer -- not valid.
    _sv_st=$(_snap_dm status "$1" 2>> "$LOG_FILE") || return 1
    _sv_use=$(printf '%s\n' "$_sv_st" | awk '$3 == "snapshot" && $4 ~ /^[0-9]+\/[0-9]+$/ { print $4; exit }')
    [ -n "$_sv_use" ]
}

lc_snap_teardown() {
    # Every view removed and the RAM disk unloaded -- each read back: the view
    # is not in `dmsetup ls`, brd is not in /sys/module. 0 = nothing of this
    # file is left (or nothing was made); 1 + LC_SNAP_TD = what is still held.
    _snap_ws; _st_d="$_sr/snap"; LC_SNAP_TD=""
    [ -d "$_st_d" ] || return 0
    rm -f "$_st_d"/*.node   # from here no copy reads a view, whatever comes next
    # newest first: a cut (lc-cut-N) holds its snapshot (lc-src-N) open
    for _st_n in $(awk '{ a[NR] = $0 } END { for (i = NR; i >= 1; i--) print a[i] }' "$_st_d/names" 2>/dev/null); do
        _snap_dm remove --retry "$_st_n" >> "$LOG_FILE" 2>&1 || LC_SNAP_TD="$LC_SNAP_TD${LC_SNAP_TD:+; }dmsetup remove $_st_n failed"
    done
    if [ -s "$_st_d/names" ]; then
        if _st_ls=$(_snap_dm ls 2>> "$LOG_FILE"); then
            for _st_n in $(cat "$_st_d/names"); do
                printf '%s\n' "$_st_ls" | awk -v n="$_st_n" '$1 == n { f = 1 } END { exit !f }' \
                    && LC_SNAP_TD="$LC_SNAP_TD${LC_SNAP_TD:+; }$_st_n is still listed"
            done
        else LC_SNAP_TD="$LC_SNAP_TD${LC_SNAP_TD:+; }dmsetup ls failed -- the views could not be checked"; fi
    fi
    if [ -f "$_st_d/brd" ]; then   # ours (lc_snap_shrink refuses a brd it did not load)
        [ ! -e "$SYSROOT/sys/module/brd" ] || rmmod brd >> "$LOG_FILE" 2>&1 || LC_SNAP_TD="$LC_SNAP_TD${LC_SNAP_TD:+; }rmmod brd failed"
        [ ! -e "$SYSROOT/sys/module/brd" ] || LC_SNAP_TD="$LC_SNAP_TD${LC_SNAP_TD:+; }brd is still loaded"
    fi
    if [ -z "$LC_SNAP_TD" ]; then
        rm -rf "$_st_d"; echo ok > "$_sr/snap_teardown"; log "local clone: the RAM views are released (read back)"; return 0
    fi
    printf '%s\n' "$LC_SNAP_TD" > "$_sr/snap_teardown"
    log "WARNING: local clone: the RAM views were not released: $LC_SNAP_TD"
    return 1
}

_snap_back() {
    # $1 = 3 (no view) or 1 (failed). What this file made is undone first; an
    # undo that fails is a failure whatever $1 was, and is said.
    lc_snap_teardown && return "$1"   # 3: the caller refuses and says why
    LC_ERROR="${LC_ERROR:-$LC_SNAP_WHY}; העותק בזיכרון לא שוחרר ($LC_SNAP_TD) — המקור לא נכתב; אתחלו את המחשב לפני שיכפול נוסף"
    return 1
}

_snap_views() {
    # $1 = source, $2 = plan, $3 = KiB per view. One view per plan line, each
    # read back, then the budget. $_sr/snap/views: idx|node|start|size|newsec|name.
    _sw_i=0; _sw_tot=0
    while IFS='|' read -r _sw_idx _sw_g _sw_u _sw_st _sw_sz _sw_cur _sw_new <&3; do
        [ -n "$_sw_idx" ] || continue
        _sw_node=$(partition_node "$1" "$_sw_idx"); _sw_pn=${_sw_node##*/}; _sw_name="lc-src-$_sw_idx"; _sw_ram="ram$_sw_i"
        _sw_len=$(cat "$SYSROOT/sys/class/block/$_sw_pn/size" 2>/dev/null)
        [ "$_sw_len" = "$_sw_sz" ] || { LC_SNAP_WHY="מחיצה $_sw_idx: הקרנל מדווח ${_sw_len:-כלום} סקטורים, והתוכנית $_sw_sz"; return 3; }
        [ "$(cat "$SYSROOT/sys/block/$_sw_ram/size" 2>/dev/null)" = $(($3 * 2)) ] && [ -e "$DEVROOT/$_sw_ram" ] \
            || { LC_SNAP_WHY="ה-RAM disk ‏$_sw_ram לא עלה בגודל $(($3 / 1024)) MB"; return 3; }
        echo "$_sw_name" >> "$_sr/snap/names"   # before create: a half-made view is still removed
        _snap_dm create "$_sw_name" --table "0 $_sw_len snapshot $_sw_node $DEVROOT/$_sw_ram N $LC_SNAP_CHUNK" >> "$LOG_FILE" 2>&1 \
            || { LC_SNAP_WHY="dmsetup create $_sw_name נכשל (מחיצה $_sw_idx)"; return 3; }
        _sw_want="0 $_sw_len snapshot $(cat "$SYSROOT/sys/class/block/$_sw_pn/dev" 2>/dev/null) $(cat "$SYSROOT/sys/block/$_sw_ram/dev" 2>/dev/null) N $LC_SNAP_CHUNK"
        _sw_got=$(_snap_dm table "$_sw_name" 2>> "$LOG_FILE")
        { [ "$_sw_got" = "$_sw_want" ] && [ -e "$DEVROOT/mapper/$_sw_name" ]; } || {
            log "the view of partition $_sw_idx did not read back: '$_sw_got', not '$_sw_want'"
            LC_SNAP_WHY="העותק של מחיצה $_sw_idx לא נקרא בחזרה כפי שנוצר — הפרטים ביומן"; return 3; }
        _sw_h=0; _sw_hs=clean; case "$_sn_u" in unhib|capture) _sw_hs=$(ntfs_state "$DEVROOT/mapper/$_sw_name") ;; esac   # the view, never the source
        if [ "$_sn_u" = capture ] && [ "$_sw_hs" = dirty ]; then LC_SNAP_WHY=$(capture_hibernated_message "$_sw_node"); return 3; fi
        case "$_sw_hs" in clean|dirty) ;; hibernated) _sw_h=1 ;;
            *) LC_SNAP_WHY="מחיצה $_sw_idx: לא הצלחנו לבדוק אם Windows בה כובה עד הסוף (${_sw_hs#unchecked:})"; return 3 ;; esac
        if [ "$_sn_u" = capture ] && [ "$_sw_new" = "$_sw_cur" ]; then _sw_mb=0; else
        ntfsresize -n -s "$_sw_new" --no-progress-bar "$DEVROOT/mapper/$_sw_name" < /dev/null > "$_sr/snap/$_sw_idx.n" 2>&1; _sw_rc=$?
        cat "$_sr/snap/$_sw_idx.n" >> "$LOG_FILE"
        _sw_mb=$(_lc_relocs "$_sr/snap/$_sw_idx.n")
        { [ "$_sw_rc" = 0 ] && [ -n "$_sw_mb" ]; } \
            || { LC_SNAP_WHY="מחיצה $_sw_idx: ‏ntfsresize -n לא אמר כמה צריך להזיז (rc=$_sw_rc)"; return 3; }
        fi
        _sw_need=$((_sw_mb + _sw_mb / 10 + LC_SNAP_META_MB + _sw_h * LC_SNAP_UNHIB_MB))
        [ $((_sw_need * 1024)) -le "$3" ] \
            || { if [ "$_sn_u" = capture ] && [ "$_sw_mb" = 0 ]; then   # #2213: the owner's wording (mockup 13, 30/10)
                LC_SNAP_WHY="מחיצה $_sw_idx: לביטול מצב השינה בעותק נדרשים $_sw_need MB זיכרון, ויש $(($3 / 1024)) MB — לא נקלט. המקור לא נכתב"
                else LC_SNAP_WHY="מחיצה $_sw_idx: להזזת $_sw_mb MB נדרשים $_sw_need MB זיכרון, ויש $(($3 / 1024)) MB"; fi; return 3; }
        _sw_tot=$((_sw_tot + _sw_mb)); _sw_i=$((_sw_i + 1))
        echo "$_sw_idx|$DEVROOT/mapper/$_sw_name|$_sw_st|$_sw_sz|$((_sw_new / 512))|$_sw_name|$_sw_h" >> "$_sr/snap/views"
    done 3< "$2"
    echo "$_sw_tot" > "$_sr/relocations_mb"
}

_snap_unhib() {
    # $1 idx, $2 the view's node, $3 its name. #2208: the hibernation dropped
    # on the view (the source is only read). 0 + $_sr/src_unhib = what was done;
    # else _snap_back's code: 3 = the store filled, 1 + LC_ERROR = the drop failed.
    log "local clone: partition $1 of the source is hibernated (Fast Startup) -- dropping it on the RAM view (#2208)"
    ntfs_unhibernate "$2"; _su_rc=$?
    _snap_valid "$3" || { log "the view of partition $1 is not valid after the drop: ${_sv_st:-no status}"
        LC_SNAP_WHY="העותק של מחיצה $1 אינו תקין אחרי ביטול ההשהיה (המאגר בזיכרון התמלא?) — הפרטים ביומן"; _snap_back 3; return; }
    log "view of partition $1: the drop rc=$_su_rc, its RAM store holds $_sv_use sectors (#2208)"
    if [ "$_su_rc" -ne 0 ]; then
        if [ "$_sn_u" = capture ]; then   # #2213: the owner's wording (mockup 13, 30/10)
        LC_ERROR="המקור במצב שינה של Windows (Fast Startup), וביטול ההשהיה בעותק של מחיצה $1 נכשל — לא נקלט. ב-Windows של המקור: ⁦powercfg /h off⁩, ואז כיבוי — המקור לא נכתב"
        else
        LC_ERROR="המקור במצב שינה של Windows (Fast Startup), וביטול ההשהיה בעותק של מחיצה $1 נכשל — אי אפשר לכווץ אותו לדיסק הזה, שקטן ממנו. ב-Windows של המקור: ⁦powercfg /h off⁩, ואז כיבוי — המקור לא נכתב"
        fi
        _snap_back 1; return; fi
    _su_a=unhibernated; [ "$NTFS_UNHIB_LOGFILE" = 0 ] || _su_a=unhibernated_logfile
    echo "$_su_a" > "$_sr/src_unhib" || { LC_ERROR="לא הצלחנו לרשום שההשהיה בוטלה בעותק — המקור לא נכתב"; _snap_back 1; return; }
}

_snap_cut() {
    # $1 idx, $2 the shrunk view's name, $3 the new length in sectors. A dm
    # linear over the view's first $3 sectors -- the partition at its new
    # size -- read back by table (over the view's own major:minor) and by
    # size (blockdev). _sc_node = its node; 3 + LC_SNAP_WHY otherwise.
    _sc_name="lc-cut-$1"; _sc_node="$DEVROOT/mapper/$_sc_name"
    echo "$_sc_name" >> "$_sr/snap/names"   # before create, as the view
    _snap_dm create "$_sc_name" --table "0 $3 linear $DEVROOT/mapper/$2 0" >> "$LOG_FILE" 2>&1 \
        || { LC_SNAP_WHY="לא הצלחנו להציג את העותק של מחיצה $1 בגודלו החדש (dmsetup create $_sc_name)"; return 3; }
    _sc_mm=$(_snap_dm info -c --noheadings -o major,minor --separator : "$2" 2>> "$LOG_FILE" | tr -d ' ')
    _sc_got=$(_snap_dm table "$_sc_name" 2>> "$LOG_FILE"); _sc_b=$(blockdev --getsize64 "$_sc_node" 2>> "$LOG_FILE")
    [ -n "$_sc_mm" ] && [ "$_sc_got" = "0 $3 linear $_sc_mm 0" ] && [ "$_sc_b" = $(($3 * 512)) ] && return 0
    log "the cut of partition $1 did not read back: table '$_sc_got' (want '0 $3 linear $_sc_mm 0'), size '$_sc_b' (want $(($3 * 512)))"
    LC_SNAP_WHY="העותק של מחיצה $1 לא נקרא בחזרה בגודלו החדש ($(($3 * 512)) בייט) — הפרטים ביומן"; return 3
}

lc_snap_shrink() {
    # $1 source, $2 unhib (#2208) or capture (#2213, permits full-size entries). 0 = views ready,
    # shrunk, parts.txt is the compacted layout (parts.orig.txt the source's),
    # and $_sr/snap/<idx>.node names what partclone reads; 1 + LC_ERROR =
    # failed, the source was not written; 3 + LC_SNAP_WHY = no view could be
    # had, nothing is left behind -- the caller refuses the shrink, and says why.
    _snap_ws; LC_SNAP_WHY=""; _sn_d="$_sr/snap"; _sn_p="$_sr/shrink.plan"; _sn_u=${2:-}
    rm -rf "$_sn_d"; rm -f "$_sr/relocations_mb" "$_sr/src_unhib"
    _sn_k=$(awk 'NF { n++ } END { print n + 0 }' "$_sn_p" 2>/dev/null)
    [ "${_sn_k:-0}" -gt 0 ] || { LC_SNAP_WHY="אין תוכנית כיווץ"; return 3; }
    command -v dmsetup >/dev/null 2>&1 || { LC_SNAP_WHY="dmsetup אינו ב-initramfs הזה"; return 3; }
    [ ! -e "$SYSROOT/sys/module/brd" ] || { LC_SNAP_WHY="RAM disk ‏(brd) כבר טעון, ולא על ידי השיכפול"; return 3; }
    _sn_av=$(awk '/^MemAvailable:/ { print $2; exit }' "$SYSROOT/proc/meminfo" 2>/dev/null)
    case "$_sn_av" in ''|*[!0-9]*) LC_SNAP_WHY="לא הצלחנו לקרוא כמה זיכרון פנוי (MemAvailable)"; return 3 ;; esac
    _sn_kib=$(( (_sn_av - LC_SNAP_RESERVE_MB * 1024) / _sn_k / 4 * 4 ))
    [ "$_sn_kib" -gt 0 ] || { LC_SNAP_WHY="פנויים $((_sn_av / 1024)) MB זיכרון, ו-$LC_SNAP_RESERVE_MB MB שמורים להעתקה עצמה"; return 3; }
    mkdir -p "$_sn_d" && : > "$_sn_d/names" || { LC_SNAP_WHY="לא הצלחנו להכין את תיקיית העותק"; return 3; }
    modprobe dm-snapshot >> "$LOG_FILE" 2>&1 || { LC_SNAP_WHY="המודול dm-snapshot לא נטען"; _snap_back 3; return; }
    : > "$_sn_d/brd"   # before the load: a load that half-worked is still unloaded
    modprobe brd rd_nr="$_sn_k" rd_size="$_sn_kib" >> "$LOG_FILE" 2>&1 || { LC_SNAP_WHY="המודול brd לא נטען"; _snap_back 3; return; }
    _snap_views "$1" "$_sn_p" "$_sn_kib" || { _snap_back 3; return; }
    [ "$(awk 'END { print NR }' "$_sn_d/views" 2>/dev/null)" = "$_sn_k" ] \
        || { LC_SNAP_WHY="בתוכנית $_sn_k מחיצות, ולא נוצר עותק לכל אחת"; _snap_back 3; return; }
    log "local clone: read-only path -- $_sn_k view(s) of the source, $(cat "$_sr/relocations_mb") MB to move, $((_sn_kib / 1024)) MB of RAM each"
    date +%s > "$_sr/resize_at"; : > "$_sn_d/mark"; : > "$_sn_d/cuts"
    while IFS='|' read -r _sn_idx _sn_node _sn_st _sn_sz _sn_new _sn_name _sn_hib <&3; do
        _sn_prev=""; [ "$_sn_u" != capture ] || _sn_prev=$(cat "$_sr/src_unhib" 2>/dev/null)
        [ "$_sn_hib" != 1 ] || _snap_unhib "$_sn_idx" "$_sn_node" "$_sn_name" || return
        [ "$_sn_prev" != unhibernated_logfile ] || echo "$_sn_prev" > "$_sr/src_unhib" || { LC_ERROR="לא הצלחנו לרשום שההשהיה בוטלה בעותק — המקור לא נכתב"; _snap_back 1; return; }
        if [ "$_sn_u" = capture ] && [ "$_sn_new" = "$_sn_sz" ]; then
            echo "$_sn_idx|$_sn_node" >> "$_sn_d/cuts"; continue
        fi   # #2213: full-size view, no resize and no linear cut
        log "shrinking the VIEW of partition $_sn_idx on $1: $((_sn_sz * 512)) -> $((_sn_new * 512)) bytes (the source is not written)"
        # ‏-s בלי -f ועם "y" מפורש, כמו shrink.sh. ‏$? של הצינור הוא של ntfsresize.
        printf 'y\n' | ntfsresize -s "$((_sn_new * 512))" --no-progress-bar "$_sn_node" >> "$LOG_FILE" 2>&1; _sn_rc=$?
        _sn_why=$(tail -n 1 "$LOG_FILE" 2>/dev/null | tr -d '\r'); _sn_fx=0
        [ "$_sn_rc" -ne 0 ] || { ntfsfix -d "$_sn_node" >> "$LOG_FILE" 2>&1; _sn_fx=$?; }
        # A store that filled up is the budget's miss: refused, by name. Anything
        # else is the filesystem's own answer: failed, by name.
        _snap_valid "$_sn_name" || { log "the view of partition $_sn_idx is not valid: ${_sv_st:-no status}"
            LC_SNAP_WHY="העותק של מחיצה $_sn_idx אינו תקין אחרי הכיווץ (המאגר בזיכרון התמלא?) — הפרטים ביומן"; _snap_back 3; return; }
        if [ "$_sn_rc" -ne 0 ]; then log "ntfsresize on the view of partition $_sn_idx failed (rc=$_sn_rc): $_sn_why"
            LC_ERROR="הכיווץ של העותק של מחיצה $_sn_idx נכשל (ntfsresize rc=$_sn_rc) — המקור לא נכתב; הפרטים ביומן"; _snap_back 1; return; fi
        if [ "$_sn_fx" -ne 0 ]; then LC_ERROR="דגל ה-dirty לא נוקה בעותק של מחיצה $_sn_idx (ntfsfix -d rc=$_sn_fx) — המקור לא נכתב"; _snap_back 1; return; fi
        log "view of partition $_sn_idx shrunk; its RAM store holds $_sv_use sectors"
        _snap_cut "$_sn_idx" "$_sn_name" "$_sn_new" || { _snap_back 3; return; }
        echo "$_sn_idx|$_sc_node" >> "$_sn_d/cuts"
        echo "$_sn_idx|$_sn_st|$_sn_sz|$_sn_new" >> "$_sn_d/mark"
    done 3< "$_sn_d/views"
    cp "$_sr/parts.txt" "$RUN_DIR/parts.orig.txt" \
        && _shrink_compact "$_sn_d/mark" "$RUN_DIR/parts.orig.txt" > "$_sr/parts.txt" \
        || { LC_ERROR="לא הצלחנו לכתוב את הפריסה המצומצמת — המקור לא נכתב"; _snap_back 1; return; }
    # Last: only a complete success points the copy at the views -- at their cuts.
    while IFS='|' read -r _sn_idx _sn_node; do echo "$_sn_node" > "$_sn_d/$_sn_idx.node"; done < "$_sn_d/cuts"
}
