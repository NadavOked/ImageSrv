# tools_carve.sh -- #2804: file recovery that writes what it finds to the
# destination (tooldest.sh): scalpel, foremost, extundelete, magicrescue +
# recoverjpeg. POSIX sh (busybox ash).
#
# Every run: an explicit source (never the stick, never implied), stdin
# closed, a fresh time-stamped folder that is not on the source disk, and
# success only from the folder itself -- the files counted there after the
# run, not the tool's word. A tool's non-zero exit, or a destination that
# filled up, is "not complete" (rc 2), whatever was saved.

tools_carve_list() {
    cat <<'EOF'
scalpel|recover|חילוץ JPG/PDF/ZIP מדיסק|ro|disk:הדיסק לחילוץ|חילוץ לפי חתימה לסוגים נפוצים, להצלבה מול photorec.
foremost|recover|חילוץ קבצים לפי כותרות|ro|disk:הדיסק לחילוץ|חילוץ לפי כותרת/סיומת פנימית, כש-photorec החמיץ סוג.
extundelete|recover|שחזור קבצים מ-Linux|ro|part:מחיצת ext3/ext4|מחזיר קבצים שנמחקו מ-ext3/4, במחשב בנייה או שרת לינוקס.
magicrescue|recover|חילוץ נוסף להצלבה|ro|disk:הדיסק לחילוץ|כלי חילוץ נוסף (במיוחד JPEG) להשוואה מול התוצאות האחרות.
EOF
}

# The magicrescue recipes (JPEG, both magics) and the binary each one calls.
TOOLS_MR_RECIPES=${TOOLS_MR_RECIPES:-/usr/share/magicrescue/recipes}

_tc_means() { printf '\nמה זה אומר: %s\n' "$1"; }

_tc_count() {   # $1 = dir -> the number of files that landed there, audit.txt aside
    # No folder = the tool created nothing = 0. A find that failed is not 0 (rc 1).
    [ -d "$1" ] || { echo 0; return 0; }
    _tcl=$(find "$1" -type f ! -name audit.txt) || return 1
    printf '%s' "$_tcl" | grep -c . || :   # grep's 1 on no line is the answer 0
}

_tc_kb() { du -sk "$1" 2>/dev/null | awk '{ print $1 }'; }

_tc_scalpel_conf() {
    # Debian's /etc/scalpel/scalpel.conf has every type commented out, and
    # scalpel carves nothing without one. JPG, PDF and ZIP (the catalog's
    # title), the lines of that file's own examples.
    cat > "$1" <<'EOF'
	jpg	y	200000000	\xff\xd8\xff\xe0\x00\x10	\xff\xd9
	jpg	y	200000000	\xff\xd8\xff\xe1	\xff\xd9
	pdf	y	5000000	%PDF	%EOF\x0d	REVERSE
	pdf	y	5000000	%PDF	%EOF\x0a	REVERSE
	zip	y	10000000	PK\x03\x04	\x3c\xac
EOF
}

_tc_magicrescue() {
    # $1 = source, $2 = out dir. recoverjpeg first (self-contained), then
    # magicrescue with the JPEG recipes; a recipe whose program (jpegtran)
    # or file is not packed is said and the run is partial -- never skipped quietly.
    mkdir -p "$2/recoverjpeg" "$2/magicrescue" || return 2
    _tcrc=0
    printf '\n--- recoverjpeg ---\n'
    recoverjpeg -o "$2/recoverjpeg" "$1" </dev/null 2>&1 || { printf 'recoverjpeg נכשל (rc %s).\n' $?; _tcrc=2; }
    printf '\n--- magicrescue ---\n'
    _tcr=""
    for _tcx in jpeg-jfif jpeg-exif; do
        [ -f "$TOOLS_MR_RECIPES/$_tcx" ] && _tcr="$_tcr -r $TOOLS_MR_RECIPES/$_tcx"
    done
    if [ -z "$_tcr" ] || ! command -v jpegtran >/dev/null 2>&1; then
        printf 'magicrescue לא הורץ: המתכונים (%s) או jpegtran אינם ארוזים.\n' "$TOOLS_MR_RECIPES"
        return 2
    fi
    # shellcheck disable=SC2086 # _tcr is "-r <path>" pairs, split on purpose
    magicrescue -d "$2/magicrescue" $_tcr "$1" </dev/null 2>&1 || { printf 'magicrescue נכשל (rc %s).\n' $?; return 2; }
    return "$_tcrc"
}

_tc_carve() {
    # $1 = id, $2 = source, $3 = out dir (does not exist yet: scalpel and
    # foremost refuse a folder that is not empty).
    case "$1" in
        scalpel)
            _tcconf="$RUN_DIR/tool-scalpel.conf"; _tc_scalpel_conf "$_tcconf" || return 2
            scalpel -c "$_tcconf" -o "$3" "$2" </dev/null 2>&1 ;;
        foremost) foremost -t all -i "$2" -o "$3" </dev/null 2>&1 ;;
        extundelete)
            # -o: never the implied RECOVERED_FILES/ in the current folder. Its
            # stdout is not a terminal here, so a mounted partition is an error, not a question.
            mkdir -p "$3" || return 2
            extundelete --restore-all -o "$3" "$2" </dev/null 2>&1 ;;
        magicrescue) _tc_magicrescue "$2" "$3" ;;
    esac
}

tools_carve_run() {
    _id=${1:-}; _arg=${2:-}
    case "$_id" in
        scalpel|foremost|extundelete) _tcb=$_id; _tck=disk ;;
        magicrescue) _tcb="magicrescue recoverjpeg"; _tck=disk ;;
        *) return 4 ;;
    esac
    [ "$_id" != extundelete ] || _tck=part
    _out="${RUN_DIR:?}/tool-$_id.out"
    {
        for _tcn in $_tcb; do
            command -v "$_tcn" >/dev/null 2>&1 || { printf 'לא נבדק: הכלי %s חסר.\n' "$_tcn"; _tc_means 'הכלי לא הורץ; אין כאן תוצאה.'; return 2; }
        done
        tooldest_source "$_arg" "$_tck" || { _tc_means 'הכלי לא הורץ; לא נקרא ולא נשמר דבר.'; return 2; }
        tooldest_prepare "$_id/$(tooldest_stamp)" "$TOOLS_DEST_MIN_KB" || { _tc_means 'הכלי לא הורץ; לא נקרא ולא נשמר דבר.'; return 2; }
        _tcdir="$TD_DIR/out"
        printf 'מקור: %s · %s בתים. הכלי רק קורא ממנו.\n\n' "$TD_SRC" "$TD_SRC_BYTES"
        _tc_carve "$_id" "$TD_SRC" "$_tcdir"; _tcrc=$?
        _tcn=$(_tc_count "$_tcdir"); case "$_tcn" in ''|*[!0-9]*) _tcn=-1 ;; esac
        _tckb=$(_tc_kb "$_tcdir"); case "$_tckb" in ''|*[!0-9]*) _tckb=0 ;; esac
        printf '\nנספרו ביעד: %s קבצים, %s KB (%s).\n' "$_tcn" "$_tckb" "$_tcdir"
        if [ "$_tcn" -lt 0 ]; then
            _tc_means 'לא ניתן היה לספור את מה שנשמר ביעד — אין ראיה שמשהו חולץ.'; return 2
        elif tooldest_filled; then
            _tc_means "היעד התמלא והחילוץ נעצר באמצע. מה שנשמר ($_tcn קבצים) חלקי; לא נסרק כל המקור."; return 2
        elif [ "$_tcrc" -ne 0 ]; then
            _tc_means "הכלי לא הסתיים כראוי (rc $_tcrc). מה שנשמר ($_tcn קבצים) חלקי בלבד."; return 2
        elif [ "$_tcn" -eq 0 ]; then
            _tc_means 'הסריקה הסתיימה ולא חולץ אף קובץ. זה לא אומר שאין שם קבצים — רק שהכלי הזה לא זיהה אותם; כדאי לנסות כלי חילוץ אחר.'; return 1
        fi
        _tc_means "חולצו $_tcn קבצים, והם ביעד שלמעלה. קובץ שחולץ אינו בהכרח שלם (ובחילוץ לפי חתימה גם שמו אינו המקורי) — כל קובץ צריך לפתוח ולבדוק. מהמקור לא נכתב דבר."
        return 0
    } > "$_out" 2>&1
}
