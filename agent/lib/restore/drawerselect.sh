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
    _disks=""
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
            1) _disks="$_disks $1" ;;
            0) log "selected drawer $_rt_port has no disk on this machine -- it is not written; the other drawers are (#2201)" ;;
            *) die_local "selected drawer $_rt_port does not map to exactly one disk" ;;
        esac
    done
    [ -n "$_disks" ] || die_local "the room selected no writable drawers"
}
