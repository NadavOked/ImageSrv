# drawerselect.sh -- which drawers a room round writes on this machine (#695).
# POSIX sh (busybox ash). Moved out of clonergui.sh (#2201: it sat at 298 of 300).
#
# ‏#695: קובע את $_disks לרשימת המגירות שנבחרו לסבב. סבב עם בחירה
# (‏session.target_ports הוא מערך) כותב **רק** לפורטים האלה — בלי נפילה ל"כל
# הדיסקים" (עיקרון 4/5/7). null אינו אישור לכתוב לכל המגירות (#1410).
# פורט שממופה ל**שני** דיסקים או יותר — die_local: אסור לנחש לאיזה לכתוב.
# ‏#2201 (הכרעה 03/11): פורט שנבחר ו**אין** בו דיסק (נשלף, הבקר ויתר עליו)
# אינו עמום — הדיסקים האחרים ממופים בבירור. המגירה החסרה נכשלת לבדה (השרת
# יודע עליה ומסמן אותה בשמה), והשאר נכתבות; עד אז היא הפילה את כל המשכפל
# ל-FATAL ואתחול, והמגירה התקינה לא נכתבה (ברזל 25/10, ‏room_edc35340).
# בחירה שאף אחד מפורטיה אינו כאן — die_local, כמו קודם: אין מה לכתוב.
# מריץ בשל המעטפת של הסוכן (לא בתת-מעטפת), כדי ש-die_local יעצור באמת.
resolve_target_drawers() {
    _rt_ports=$(json_get "$RESP" ".session.target_ports")
    if [ "$_rt_ports" = "null" ]; then
        die_local "room target selection is missing"
    fi
    _disks=""; DRAWER_MAP=""   # #2434: disk:port as chosen, for drawers_bound
    _rt_list=$(jq -er '.session.target_ports
        | select(type == "array" and length > 0)
        | .[] | select(type == "number" and floor == .)' "$RESP") \
        || die_local "room target selection is missing or malformed"
    for _rt_port in $_rt_list; do
        _rt_match=""
        for _rt_dev in $(list_drawers); do
            [ "$(disk_port "$_rt_dev")" = "$_rt_port" ] && _rt_match="$_rt_match $_rt_dev"
        done
        # shellcheck disable=SC2086 # word-splitting the match list is intended
        set -- $_rt_match
        case "$#" in
            1) _disks="$_disks $1"; DRAWER_MAP="$DRAWER_MAP $1:$_rt_port" ;;
            0) log "selected drawer $_rt_port has no disk on this machine -- it is not written; the other drawers are (#2201)" ;;
            *) die_local "selected drawer $_rt_port does not map to exactly one disk" ;;
        esac
    done
    [ -n "$_disks" ] || die_local "the room selected no writable drawers"
    # #2448: what the console showed in each chosen slot (the serial and size of this
    # hello's own inventory) -> drawer_shown, port|size_bytes|serial. Absent/null = a
    # server from before #2448: the slot alone, as before. Anything else -- or jq that
    # broke on it -- is not "nothing to compare": die_local, nothing written (principle 5).
    rm -f "$RUN_DIR/drawer_shown"
    case "$(jq -r '.session.target_disks | type' "$RESP" 2>/dev/null)" in
        null) ;;
        array) jq -r '.session.target_disks[] | select(type == "object" and (.port | type) == "number" and (.port | floor) == .port)
                  | "\(.port)|\(.size_bytes // "")|\(.serial // "")"' "$RESP" > "$RUN_DIR/drawer_shown" \
                   || { rm -f "$RUN_DIR/drawer_shown"; die_local "room target disks are malformed"; } ;;
        *) die_local "room target disks are malformed" ;;
    esac
}

drawer_shown() {
    # $1 = disk, $2 = the slot the room chose. #2448: 0 = no drawer_shown (an old server),
    # or what target_init bound is what the console showed in that slot -- the same
    # serial and size. A slot the server sent nothing for, or null, is not "the same".
    [ -f "$RUN_DIR/drawer_shown" ] || return 0
    _ds_b=$(cat "$RUN_DIR/targets/$1/bound" 2>/dev/null); _ds_n=${_ds_b%|*}; _ds_n=${_ds_n##*|}
    case "$_ds_n" in ''|*[!0-9]*) return 1 ;; esac
    while IFS='|' read -r _ds_p _ds_z _ds_v; do
        [ "$_ds_p" = "$2" ] || continue
        [ -n "$_ds_v" ] && [ "$_ds_v" = "$(target_bound_serial "$1")" ] && [ "$_ds_z" = "$((_ds_n * 512))" ]
        return
    done < "$RUN_DIR/drawer_shown"
    return 1
}

drawers_bound() {
    # $1.. = the round's drawers, each through target_init (its `bound`, failmark.sh).
    # PRINTS the ones the round may write: a serial, and still in the slot the room
    # chose (DRAWER_MAP). #2434: the drawer is then written by that identity, and
    # assert_target asks it again before every write. A drawer with no serial has
    # nothing to be asked by -- it fails by name, in the open, and is not written.
    # #2448: nor a drawer with a twin here, nor one that is not the disk the console
    # showed in its slot (drawer_shown) -- the identity target_init bound, compared.
    for _db_d in "$@"; do
        _db_p=""; for _db_m in $DRAWER_MAP; do [ "${_db_m%:*}" != "$_db_d" ] || _db_p=${_db_m##*:}; done
        _db_b=$(cat "$RUN_DIR/targets/$_db_d/bound" 2>/dev/null)
        if [ -z "$_db_b" ]; then
            target_foreign "$_db_d" "זהות המגירה $_db_d לא נרשמה בבחירה — אי אפשר לוודא שזה הדיסק שנבחר, לא נכתב"
        elif [ -z "$(target_bound_serial "$_db_d")" ]; then
            target_foreign "$_db_d" "למגירה $_db_d אין מספר סידורי — אי אפשר לוודא שזה הדיסק שנבחר, לא נכתב"
        elif ! target_unique "$_db_d"; then
            :   # #2448: a twin (the same serial and size) -- failed by name (failmark.sh)
        elif [ -z "$_db_p" ]; then
            target_foreign "$_db_d" "המגירה $_db_d אינה במיפוי החריצים שהחדר בחר — לא נכתב"
        elif [ "${_db_b##*|}" != "$_db_p" ]; then
            target_foreign "$_db_d" "הדיסק $_db_d בחריץ $(_p=${_db_b##*|}; echo "${_p:-לא ידוע}"), לא בחריץ שנבחר ($_db_p) — לא נכתב"
        elif ! drawer_shown "$_db_d" "$_db_p"; then
            target_foreign "$_db_d" "המגירה הוחלפה אחרי שנבחרה: בחריץ $_db_p יש עכשיו דיסק $(target_bound_serial "$_db_d"), לא הדיסק שהקונסולה הציגה — לא נכתב"
        else
            echo "$_db_d"
        fi
    done
}
