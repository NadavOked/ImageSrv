#!/bin/sh
# Read-only capture guards: NTFS fast-startup/hibernation (#651) and
# BitLocker (#671). Both run before a byte moves; capture.sh calls them.

capture_hibernated_message() {
    # $1 = partition node. #1502 (metal 16/10, probe rc=14): with Fast Startup
    # on, the normal Start-menu shutdown IS a hibernation -- say so, and give
    # the one fix that works. Hebrew first: a line that opens with an LTR token
    # ("sda: ...") is laid out LTR on the screen and the Hebrew turns over.
    echo "חלונות במחיצה ${1#/dev/} נכבה ב-Fast Startup — גם כיבוי רגיל משאיר אותו נעול. בחלונות: הריצו כמנהל powercfg /h off, כבו, וקלטו מחדש."
}

capture_ntfs_hibernation_reason() {
    # $1=partition node, $2=private mount point. Empty output means clean.
    # rc 1=hibernated/dirty: #2213 may prepare a verified RAM view; rc 2=not checked, refuse.
    _hn_node="$1"; _hn_mnt="$2"
    command -v ntfs-3g.probe >/dev/null 2>&1 || {
        echo "לא הצלחנו לבדוק מצב שינה ב-$_hn_node: הכלי ntfs-3g.probe חסר"
        return 2
    }

    # Probing read-write mountability is itself read-only; it exposes dirty (15)
    # and hibernated (14), both of which remain mountable read-only.
    ntfs-3g.probe --readwrite "$_hn_node" >/dev/null 2>&1
    _hn_rc=$?
    case "$_hn_rc" in
        0) ;;
        14|15) capture_hibernated_message "$_hn_node"; return 1 ;;
        *) echo "לא הצלחנו לבדוק מצב שינה ב-$_hn_node (ntfs-3g.probe rc=$_hn_rc)"; return 2 ;;
    esac

    # 17/09, measured on the lab build machine: hiberfil.sys (3.36 GB) exists
    # on every Windows with hibernation/fast-startup *enabled*, also after a
    # full shutdown (probe rc=0, ntfsresize clean) -- its presence is not the
    # hibernated state. ntfs-3g.probe reads the file header (hibr/HIBR) and
    # returns 14 exactly when Windows is hibernated or fast-started; that is
    # the whole check. The old "file exists" rule refused every such disk.
    return 0
}

# #980 section 6: the NTFS state the build machine card shows before a capture.
# The same read-only ntfs-3g.probe as the gate above, for every NTFS partition
# blkid sees -- a field of the machine probe (probe.sh, which owns _have/_pn/
# _pe/_js and BLKID). null = a tool is not packed; {"error"} = blkid failed;
# [] = no NTFS; else per partition: clean (0) | hibernated (14) | dirty (15),
# or error with the rc.
NTFSPROBE="${NTFSPROBE:-ntfs-3g.probe}"
probe_ntfs() {
    { _have "$BLKID" && _have "$NTFSPROBE"; } || { _pn; return; }
    _nraw=$("$BLKID" 2>/dev/null); _nrc=$?
    [ "$_nrc" = 0 ] || [ "$_nrc" = 2 ] || { _pe "blkid rc $_nrc"; return; }
    printf '['
    printf '%s\n' "$_nraw" | awk '/TYPE="ntfs"/ { n = $1; sub(/:$/, "", n); print n }' | {
    _nsep=""
    while read -r _nn; do
        "$NTFSPROBE" --readwrite "$_nn" >/dev/null 2>&1 < /dev/null; _nrc=$?
        case "$_nrc" in
            0) _nst='"state":"clean"' ;;
            14) _nst='"state":"hibernated"' ;;
            15) _nst='"state":"dirty"' ;;
            *) _nst="\"error\":\"ntfs-3g.probe rc $_nrc\"" ;;
        esac
        printf '%s{"node":%s,%s}' "$_nsep" "$(_js "$_nn")" "$_nst"; _nsep=","
    done
    }
    printf ']'
}

_bitlocker_refusal() {
    # $1=idx. #671's line first, word for word. #1806: Home's "device
    # encryption", waiting for activation, suspended and a decryption short
    # of 100% all keep -FVE-FS- on the disk -- the operator who believes it is
    # "off" reads his own state here, and the one fix that ends all of them.
    echo "מחיצה $1 מוצפנת ב-BitLocker — כבו את BitLocker לפני הקליטה. זה כולל 'הצפנת מכשיר' ב-Windows Home, הצפנה שממתינה להפעלה, הצפנה מושהית ופענוח שעוד לא הגיע ל-100%: בכולם הדיסק עדיין מוצפן. בחלונות, כמנהל: manage-bde -off C: והמתינו עד ש-manage-bde -status מראה Fully Decrypted."
}

_bitlocker_reason() {
    # $1=idx $2=fs $3=node. FOG -FVE-FS-; empty=ok, text=refuse (#671).
    case "$2" in *[Bb]it[Ll]ocker*) _bitlocker_refusal "$1"; return ;; esac
    for _bltool in dd grep tr; do
        command -v "$_bltool" >/dev/null 2>&1 || {
            echo "לא הצלחנו לבדוק BitLocker במחיצה $1: הכלי $_bltool חסר"
            return
        }
    done
    # ‏LC_ALL=C ו-F: החתימה בייטים קבועים, לא טקסט מקומי. בלי -i — היא
    # תמיד באותיות גדולות, ו-`grep -i` על קלט בינרי תחת locale של UTF-8
    # קורס (SIGABRT) ב-MSYS, מה שהיה הופך "לא נמצא" ל"לא הצלחנו לבדוק".
    _blraw="$RUN_DIR/bitlocker.raw.$$"; _bltext="$RUN_DIR/bitlocker.text.$$"
    dd if="$3" bs=512 count=1 > "$_blraw" 2>> "$LOG_FILE"; _blrc=$?
    if [ "$_blrc" -ne 0 ]; then
        rm -f "$_blraw" "$_bltext"
        echo "לא הצלחנו לבדוק BitLocker במחיצה $1: קריאת הכותרת נכשלה (dd rc=$_blrc)"
        return
    fi
    tr -d '\0' < "$_blraw" > "$_bltext"; _blrc=$?
    if [ "$_blrc" -ne 0 ]; then
        rm -f "$_blraw" "$_bltext"
        echo "לא הצלחנו לבדוק BitLocker במחיצה $1: פענוח הכותרת נכשל (tr rc=$_blrc)"
        return
    fi
    LC_ALL=C grep -qF -- '-FVE-FS-' "$_bltext"; _blrc=$?
    rm -f "$_blraw" "$_bltext"
    case "$_blrc" in
        0) _bitlocker_refusal "$1" ;;
        1) return ;;
        *) echo "לא הצלחנו לבדוק BitLocker במחיצה $1: חיפוש החתימה נכשל (grep rc=$_blrc)" ;;
    esac
}

# #2009 (field 22/10): a source left in Fast Startup (shut down the normal way)
# was copied -- hiberfil.sys of 13.6 GB with it -- and grown, and the copy did
# not boot (Automatic Repair). Nadav: "אם לא, dd יותר טוב מאיתנו" -- never worse
# than dd. Not asked, not stopped. On the COPY only, never the source (which is
# only probed, read-only): the hibernation is dropped on the target
# (ntfs_unhibernate, grow.sh), and it grows as always; when that fails the
# target is made the source's own -- its table, and every partition the grow
# had moved (or the drop had touched) copied raw from the source and compared
# -- so Windows resumes on exactly the volume it left. The "done" is orange.
LC_NTFS_UNHIB="Windows במקור לא כובה עד הסוף (Fast Startup). בעותק בוטלה ההשהיה (עלייה מלאה)"
LC_NTFS_UNHIB_LOGFILE="Windows במקור לא כובה עד הסוף (Fast Startup). ההשהיה בוטלה בעותק (אחרי ריקון היומן) — העותק יעלה כמו אחרי הפעלה מחדש"
LC_NTFS_SAME="Windows במקור לא כובה עד הסוף (Fast Startup), וביטול ההשהיה בעותק נכשל — הועתק בלי הגדלה, זהה למקור. כדי להגדיל: לכבות את המקור עם Shift+כיבוי ולשכפל שוב"
LC_NTFS_SAME_UNCHECKED="לא הצלחנו לבדוק אם Windows במקור כובה עד הסוף — הועתק בלי הגדלה, זהה למקור"
LC_NTFS_DIRTY="דגל ה-dirty של NTFS נשאר על הדיסק — בעלייה הראשונה Windows עשוי להריץ בדיקת דיסק"
LC_NTFS_TGT_UNCHECKED="לא הצלחנו לבדוק את מצב ה-NTFS ביעד — בעלייה הראשונה Windows עשוי להריץ בדיקת דיסק"

_lc_ntfs_probe() {
    # $1 = disk, $2 = manifest, $3 = out file, $4 = "source"/"target". idx|state
    # per NTFS partition of the manifest (the plan beside it, $3.plan), each one
    # a line in the log. A plan that did not read is 1, never "no NTFS here".
    manifest_plan "$2" > "$3.plan" || return 1
    : > "$3" || return 1
    for _ln_i in $(awk -F'|' '$4 == "ntfs" { print $1 }' "$3.plan"); do
        _ln_s=$(ntfs_state "$(partition_node "$1" "$_ln_i")")
        echo "$_ln_i|$_ln_s" >> "$3" || return 1
        case "$_ln_s" in
            hibernated) log "local clone: $4 $1 partition $_ln_i: Windows is hibernated (Fast Startup) -- ntfs-3g.probe rc 14" ;;
            dirty) log "local clone: $4 $1 partition $_ln_i: NTFS dirty flag set -- ntfs-3g.probe rc 15" ;;
            clean) log "local clone: $4 $1 partition $_ln_i: NTFS clean" ;;
            *) log "local clone: $4 $1 partition $_ln_i: NTFS state not checked (${_ln_s#unchecked:})" ;;
        esac
    done
}

lc_ntfs_source() {
    # $1 = source, $2 = manifest. The evidence, before any target's table, in the
    # log (and the USB stick's diag report). Read-only; it decides nothing --
    # each target's own copy is probed again (lc_unhibernate_target).
    _lc_ntfs_probe "$1" "$2" "$LC/run/src.ntfs" source && return 0
    rm -f "$LC/run/src.ntfs"; log "local clone: the source's NTFS state could not be read"
}

lc_unhibernate_target() {
    # $1 = a target that was copied and read back, $2 = manifest. Before the
    # grow. targets/<d>/ntfs.action: unhibernated | same | same_unchecked; none
    # = nothing hibernated. 1 = the target could not be made either -- failed.
    # unhibernated_logfile = dropped, but only after $LogFile was emptied (#2009, metal 23/10).
    _ut_d="$RUN_DIR/targets/$1"; rm -f "$_ut_d/ntfs.action"
    _lc_ntfs_probe "$1" "$2" "$_ut_d/ntfs.before" target || : > "$_ut_d/ntfs.before.bad"
    _ut_hib=$(awk -F'|' '$2 == "hibernated" { print $1 }' "$_ut_d/ntfs.before" 2>/dev/null)
    _ut_bad=$(awk -F'|' '$2 ~ /^unchecked/ { print $1 }' "$_ut_d/ntfs.before" 2>/dev/null)
    [ ! -f "$_ut_d/ntfs.before.bad" ] || _ut_bad="plan"; rm -f "$_ut_d/ntfs.before.bad"
    # #2208: none hibernated because the drop was on the source's RAM view (lcsnap.sh) -- orange all the same
    [ -n "$_ut_hib$_ut_bad" ] || { [ ! -f "$LC/run/src_unhib" ] || cp "$LC/run/src_unhib" "$_ut_d/ntfs.action" || return 2; return 0; }
    _ut_touched=""; _ut_lf=""
    if [ -z "$_ut_bad" ]; then
        for _ut_i in $_ut_hib; do
            ntfs_unhibernate "$(partition_node "$1" "$_ut_i")" && { [ "$NTFS_UNHIB_LOGFILE" = 0 ] || _ut_lf=_logfile; continue; }
            [ "$NTFS_UNHIB_TOUCHED" = 0 ] || _ut_touched="$_ut_touched $_ut_i"
            _ut_bad="$_ut_i"; break
        done
        if [ -z "$_ut_bad" ]; then echo "unhibernated$_ut_lf" > "$_ut_d/ntfs.action"; return 0; fi
        _ut_touched="$_ut_touched $(printf '%s\n' "$_ut_hib" | awk -v b="$_ut_bad" '$1 == b { exit } { print }')"   # dropped before the one that failed
        echo same > "$_ut_d/ntfs.action"
    else
        echo same_unchecked > "$_ut_d/ntfs.action"
    fi
    log "local clone: $1: the copy is made the source's own -- no grow (#2009)"
    _lc_same_as_source "$1" "$2" $_ut_touched || {
        fail_written_target "$1" "Windows במקור לא כובה עד הסוף, והעותק לא הוחזר להיות זהה למקור: $LC_ERROR"; return 1; }
}

_lc_same_as_source() {
    # $1 = target, $2 = manifest, $3.. = partitions the drop wrote to. The
    # source's own table (when the grow had changed it), and raw from the
    # source -- read only -- every partition the grow moved and every one the
    # drop wrote; each compared with the source byte for byte. Like dd.
    _ss_t=$1; _ss_m=$2; shift 2; _ss_raw="$*"; LC_ERROR=""
    _ss_mark="$RUN_DIR/targets/$_ss_t/expanded"; _ss_src=$(cat "$LC/run/src" 2>/dev/null)
    [ -n "$_ss_src" ] || { LC_ERROR="המקור לא ידוע"; return 1; }
    if [ -f "$_ss_mark" ]; then
        _ss_c=$(cut -d'|' -f1 "$_ss_mark")
        manifest_plan "$_ss_m" > "$RUN_DIR/same.plan" || { LC_ERROR="תוכנית המחיצות לא נקראה"; return 1; }
        _ss_cs=$(awk -F'|' -v c="$_ss_c" '$1 == c { print $5 }' "$RUN_DIR/same.plan")
        _ss_raw="$_ss_raw $(awk -F'|' -v s="$_ss_cs" '$5 + 0 > s + 0 { print $1 }' "$RUN_DIR/same.plan")"
        apply_gpt "$_ss_t" "$_ss_m" || { LC_ERROR="טבלת המחיצות של המקור לא נכתבה: ${PLAN_ERROR:-apply_gpt}"; return 1; }
        rm -f "$_ss_mark"
    fi
    for _ss_i in $(printf '%s\n' $_ss_raw | awk 'NF && !seen[$1]++'); do
        _ss_from=$(partition_node "$_ss_src" "$_ss_i"); _ss_to=$(partition_node "$_ss_t" "$_ss_i")
        dd if="$_ss_from" of="$_ss_to" bs=1048576 conv=fsync 2>> "$LOG_FILE" \
            || { LC_ERROR="מחיצה $_ss_i לא הועתקה מהמקור (dd)"; return 1; }
        blockdev --flushbufs "$_ss_to" >> "$LOG_FILE" 2>&1 || { LC_ERROR="מחיצה $_ss_i: ריקון המטמון נכשל"; return 1; }
        cmp "$_ss_from" "$_ss_to" >> "$LOG_FILE" 2>&1 || { LC_ERROR="מחיצה $_ss_i אינה זהה למקור אחרי ההעתקה"; return 1; }
        log "local clone: $_ss_t partition $_ss_i: copied raw from the source and compared (#2009)"
    done
}

lc_ntfs_note() {
    # $1 = a done target, $2 = manifest. After the grow: targets/<d>/ntfs_note =
    # why its "done" is orange; no file = nothing to say, read.
    _nn_d="$RUN_DIR/targets/$1"; rm -f "$_nn_d/ntfs_note"
    _nn_a=$(cat "$_nn_d/ntfs.action" 2>/dev/null); _nn=""
    case "$_nn_a" in
        same) _nn=$LC_NTFS_SAME ;;
        same_unchecked) _nn=$LC_NTFS_SAME_UNCHECKED ;;
    esac
    if [ -z "$_nn" ]; then   # unhibernated or nothing: the target as it is now, after the grow
        _nn_v=unchecked
        _lc_ntfs_probe "$1" "$2" "$_nn_d/ntfs.after" target && _nn_v=$(awk -F'|' '
            $2 == "hibernated" || $2 == "dirty" { d = 1 } $2 ~ /^unchecked/ { u = 1 }
            END { print d ? "dirty" : u ? "unchecked" : "clean" }' "$_nn_d/ntfs.after")
        case "$_nn_v" in clean) ;; dirty) _nn=$LC_NTFS_DIRTY ;; *) _nn=$LC_NTFS_TGT_UNCHECKED ;; esac
        case "$_nn_a" in
            unhibernated) _nn="$LC_NTFS_UNHIB${_nn:+ · $_nn}" ;;
            unhibernated_logfile) _nn="$LC_NTFS_UNHIB_LOGFILE${_nn:+ · $_nn}" ;;
        esac
    fi
    [ -n "$_nn" ] || return 0
    echo "$_nn" > "$_nn_d/ntfs_note"; log "WARNING: $1: done, orange -- $_nn"
}
