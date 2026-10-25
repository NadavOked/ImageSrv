# usbset.sh -- #1914: the USB stick's settings, in the kiosk. POSIX sh.
#
# Nadav 20/10: "גם זה יש לנו בשרת -- לא להמציא עיצוב". The screen IS the
# server's settings screen (native-gui/src/screens_dcui.c settings_body and
# dialog); only the data differs. This file is that data: the four items the
# server would have sent (usbclone.sh), as DCUI frame records
# (docs/interfaces.md s31) in $GUI_DIR/usbset.frame, and the DCUI's own key
# tokens ("KEY ENTER", "CHAR 35"...) coming back as lc-usbset|<token>.
#
# A value that is not valid is refused on the dialog (d.error) and is not
# saved. Saved = settings.json on the stick's data partition (usbclone.sh
# usb_settings_path); without one, for this session only, and the screen says so.

USBSET="${LC:-$RUN_DIR/lc}/usbset"

#: id|title|sub|kind|min|max -- kind num (a whole number), rb (one of two, _us_two), map (#1962,
#: usbmap.sh) or reset (#1960)
USBSET_ITEMS='min_free_gb|מקום פנוי מינימלי אחרי שחזור|יעד שיישאר עם פחות מזה יוצג כ'"'"'לא נכנס'"'"'|num|0|500
readback_mode|אימות מלא אחרי הכתיבה|כבוי: מדגם · דלוק: כל הבלוקים (xxh64)|rb||
crc_warn_threshold|אזהרת CRC למגירה|כבל או מגירה חשודים|num|0|1000000
temp_warn_c|סף חום|דיסק חם מעל זה אחרי הכתיבה — כתום|num|30|100
drawer_slots|מיפוי חיבורים|כל החיבורים — שם, ופעיל או לא|map||
cloner_beep|צליל בסיום סבב שיכפול|הצלחה, כשל ומגירה שנשלפה — ברמקול של המחשב|rb||
reset|החזר לברירת המחדל|כל ההגדרות חוזרות לערכים הקבועים|reset||'
USBSET_LAST=6   # the last item's index

_us_text() {
    # $1 = id, $2 = pane|dlg|short|field. The words of the server's settings screen
    # for this item (the approved frames, tests/fixtures/usb-clone-frames).
    _us_q="'"   # the approved frames' ASCII quote, never between two Hebrew letters in this file (test_hebrew_screen_strings)
    case "$1:$2" in
        min_free_gb:pane) echo "יעד שיישאר עם פחות מזה יוצג כ${_us_q}לא נכנס${_us_q}. 0 = הכלל כבוי. 0–500." ;;
        min_free_gb:dlg) echo "יעד שיישאר עם פחות מזה יוצג כ${_us_q}לא נכנס${_us_q}. 0 = הכלל כבוי." ;;
        min_free_gb:short) echo "מקום פנוי מינימלי" ;;
        min_free_gb:field) echo "GB" ;;
        readback_mode:pane) echo "אימות מלא מוסיף כ־5 דק׳ לסבב של 4 מגירות. כבוי (ברירת המחדל): נבדק במדגם — כ־5% מהבלוקים בשימוש." ;;
        readback_mode:dlg) echo "איך נבדק כל יעד אחרי הכתיבה, מול המקור." ;;
        readback_mode:short) echo "אימות" ;;
        crc_warn_threshold:pane) echo "מגירה שמונה ה־CRC שלה מעל זה מסומנת כתום — כבל או מגירה חשודים. 0 = כבוי. 0–1000000." ;;
        crc_warn_threshold:dlg) echo "מגירה שמונה ה־CRC שלה מעל זה מסומנת כתום. 0 = כבוי." ;;
        crc_warn_threshold:short) echo "אזהרת CRC" ;;
        crc_warn_threshold:field) echo "שגיאות" ;;
        temp_warn_c:pane) echo "דיסק שחם מעל זה אחרי הכתיבה מסומן כתום. 30–100." ;;
        temp_warn_c:dlg) echo "דיסק שחם מעל זה אחרי הכתיבה מסומן כתום." ;;
        temp_warn_c:short) echo "סף חום" ;;
        temp_warn_c:field) echo "°C" ;;
        cloner_beep:pane) echo "אותם צלילים כמו במשכפל: הצלחה, כשל (בסוף, באמצע, או שהמקור נכשל) ומגירה שנשלפה. כבוי: שקט — המסך מראה הכול כרגיל." ;;
        cloner_beep:dlg) echo "צליל מהרמקול של המחשב בסיום השיכפול וכשמגירה נשלפת." ;;
        cloner_beep:short) echo "צליל" ;;
    esac
}

_us_two() {   # $1 = an rb item: its two choices, value|label|sub, the first one first on the screen
    case "$1" in
        readback_mode) echo "sample|מדגם|כ־5% מהבלוקים בשימוש, מפוזרים על כל המחיצה"; echo "full|מלא|כל הבלוקים, ‏xxh64 — איטי יותר" ;;
        cloner_beep) echo "true|דלוק|הצלחה, כשל ומגירה שנשלפה"; echo "false|כבוי|שקט — המסך מראה הכול כרגיל" ;;   # #2131
    esac
}

_us_default() {   # $1 = id. usbclone.sh's fixed default (Nadav 20/10).
    case "$1" in min_free_gb) echo "$USB_DEF_MIN_FREE_GB" ;; readback_mode) echo "$USB_DEF_READBACK" ;;
        crc_warn_threshold) echo "$USB_DEF_CRC_WARN" ;; temp_warn_c) echo "$USB_DEF_TEMP_WARN_C" ;; cloner_beep) echo "$USB_DEF_BEEP" ;; esac
}

_us_get() { cat "$USBSET/$1" 2>/dev/null; }
_us_put() { printf '%s\n' "$2" > "$USBSET/$1.next" && mv "$USBSET/$1.next" "$USBSET/$1"; }
_us_item() { printf '%s\n' "$USBSET_ITEMS" | sed -n "$(( $1 + 1 ))p"; }   # $1 = 0..USBSET_LAST
_us_sel() { _us_s=$(_us_get sel); case "$_us_s" in *[!0-9]*|'') _us_s=0 ;; esac; [ "$_us_s" -le "$USBSET_LAST" ] || _us_s=0; echo "$_us_s"; }

_us_iso() { printf '\342\201\250%s\342\201\251' "$1"; }   # FSI..PDI: "20 GB" in a Hebrew line, never "GB 20" (view.iso)

_us_show() {   # $1 = id, $2 = value. As the screen writes it.
    case "$1" in min_free_gb) echo "$2 GB" ;; temp_warn_c) echo "$2 °C" ;;
        readback_mode) [ "$2" = full ] && echo "מלא" || echo "מדגם" ;; cloner_beep) [ "$2" = false ] && echo "כבוי" || echo "דלוק" ;;
        *) echo "$2" ;; esac
}

usbset_now() {   # $1 = id. The value in force now (usb_settings_load's).
    usb_data_state_read; usb_settings_load
    case "$1" in min_free_gb) echo "$USB_MIN_FREE_GB" ;; readback_mode) echo "$USB_READBACK" ;;
        crc_warn_threshold) echo "$USB_CRC_WARN" ;; temp_warn_c) echo "$USB_TEMP_WARN_C" ;; cloner_beep) echo "$USB_BEEP" ;; esac
}

usbset_open() {
    mkdir -p "$USBSET" || return 1
    _us_put sel 0; _us_put dlg 0; _us_put buf ""; _us_put err ""; _us_put opt ""
    echo usbset > "$LC/screen.next" && mv "$LC/screen.next" "$LC/screen"
    usbset_frame
}

usbset_close() { echo local > "$LC/screen.next" && mv "$LC/screen.next" "$LC/screen"; }

usbset_save() {
    # $1 = id, $2 = value (already valid). Into the settings file, then the answer
    # every reader looks at (response.json, min_free_gb) is written again.
    _uv_f=$(usb_settings_path); _uv_j=$(cat "$_uv_f" 2>/dev/null); [ -n "$_uv_j" ] || _uv_j='{}'
    case "$1" in readback_mode) _uv_a="--arg" ;; *) _uv_a="--argjson" ;; esac
    printf '%s' "$_uv_j" | jq "$_uv_a" v "$2" --arg k "$1" '(if type == "object" then . else {} end) | .[$k] = $v' \
        > "$_uv_f.next" 2>/dev/null && mv "$_uv_f.next" "$_uv_f" && sync || return 1
    usb_settings_load && usb_answer && min_free_take "$RUN_DIR/response.json" \
        && printf '%s\n' "$USB_NOTICE" > "$RUN_DIR/usb.notice"
}

usbset_reset() {
    # #1960: the defaults -- usbclone.sh's USB_DEF_*, the very ones in force when there is no
    # file -- written into the settings file, then read back (principle 5): the file says
    # exactly that, and that is what is in force now. Anything else = not reset.
    # #1962: this model's drawer mapping goes too (back to the convention); other
    # models' lists on the stick stay -- they are not on this screen.
    usb_data_state_read; _ur_f=$(usb_settings_path)
    _ur_ds=$(jq -c --arg m "$(usb_model)" 'if (.drawer_slots | type) == "object" then .drawer_slots | del(.[$m]) else {} end' \
        "$_ur_f" 2>/dev/null) || _ur_ds='{}'; [ -n "$_ur_ds" ] || _ur_ds='{}'
    _ur_sn=$(jq -c --arg m "$(usb_model)" 'if (.slot_names | type) == "object" then .slot_names | del(.[$m]) else {} end' \
        "$_ur_f" 2>/dev/null) || _ur_sn='{}'; [ -n "$_ur_sn" ] || _ur_sn='{}'   # #2001: and its names
    _ur_j=$(jq -cn --argjson mf "$USB_DEF_MIN_FREE_GB" --arg rb "$USB_DEF_READBACK" --argjson crc "$USB_DEF_CRC_WARN" \
        --argjson temp "$USB_DEF_TEMP_WARN_C" --argjson beep "$USB_DEF_BEEP" --argjson ds "$_ur_ds" --argjson sn "$_ur_sn" '{min_free_gb: $mf, readback_mode: $rb,
        crc_warn_threshold: $crc, temp_warn_c: $temp, cloner_beep: $beep} + (if ($ds | length) > 0 then {drawer_slots: $ds} else {} end)
        + (if ($sn | length) > 0 then {slot_names: $sn} else {} end)') || return 1
    printf '%s\n' "$_ur_j" > "$_ur_f.next" && mv "$_ur_f.next" "$_ur_f" && sync || return 1
    jq -e --argjson want "$_ur_j" '. == $want' "$_ur_f" > /dev/null 2>&1 || return 1
    usb_settings_load
    [ "$USB_MIN_FREE_GB|$USB_READBACK|$USB_CRC_WARN|$USB_TEMP_WARN_C|$USB_BEEP" \
        = "$USB_DEF_MIN_FREE_GB|$USB_DEF_READBACK|$USB_DEF_CRC_WARN|$USB_DEF_TEMP_WARN_C|$USB_DEF_BEEP" ] || return 1
    if usb_map_json > /dev/null || usb_names_json > /dev/null; then return 1; fi   # #231: never a bare '! cmd'
    usb_answer && min_free_take "$RUN_DIR/response.json" && printf '%s\n' "$USB_NOTICE" > "$RUN_DIR/usb.notice"
}

usbset_key() {
    # $1 = a DCUI token. 0 = taken.
    _uk_s=$(_us_sel)
    IFS='|' read -r _uk_id _uk_t _uk_x _uk_kind _uk_lo _uk_hi <<EOF
$(_us_item "$_uk_s")
EOF
    if [ "$(_us_get dlg)" != 1 ]; then
        case "$1" in
            "KEY UP") [ "$_uk_s" -gt 0 ] && _us_put sel $((_uk_s - 1)) ;;
            "KEY DOWN") [ "$_uk_s" -lt "$USBSET_LAST" ] && _us_put sel $((_uk_s + 1)) ;;
            "KEY ENTER") _us_put dlg 1; _us_put err ""; _us_put foc no   # #1960: the focus starts on "ביטול"
                         if [ "$_uk_kind" = reset ]; then :; elif [ "$_uk_kind" = map ]; then usbset_map_open; elif [ "$_uk_kind" = rb ]; then _us_put opt "$(usbset_now "$_uk_id")"; else _us_put buf "$(usbset_now "$_uk_id")"; fi ;;
            "KEY ESC") usbset_close; return 0 ;;
        esac
        usbset_frame; return 0
    fi
    if [ "$_uk_kind" = map ]; then usbset_map_key "$1"; usbset_frame; return 0; fi   # #1962 (usbmap.sh)
    case "$1:$_uk_kind" in
        "KEY ESC":*) _us_put dlg 0 ;;
        "KEY TAB":reset|"KEY BACKTAB":reset|"KEY LEFT":reset|"KEY RIGHT":reset)
            if [ "$(_us_get foc)" = go ]; then _us_put foc no; else _us_put foc go; fi ;;
        "KEY ENTER":reset)
            if [ "$(_us_get foc)" != go ]; then _us_put dlg 0
            elif usbset_reset; then _us_put dlg 0
            else _us_put err "לא הוחזר: הכתיבה לקובץ ההגדרות או הקריאה החוזרת שלו נכשלה"; fi ;;
        "KEY ENTER":rb) usbset_save "$_uk_id" "$(_us_get opt)" && _us_put dlg 0 || _us_put err "לא נשמר: הכתיבה לקובץ ההגדרות נכשלה" ;;
        "KEY ENTER":num)
            _uk_v=$(_us_get buf)
            if ! _usb_int "$_uk_v" "$_uk_lo" "$_uk_hi"; then _us_put err "${_uk_v:-ריק} אינו בטווח $_uk_lo–$_uk_hi — לא נשמר."
            elif usbset_save "$_uk_id" "$_uk_v"; then _us_put dlg 0
            else _us_put err "לא נשמר: הכתיבה לקובץ ההגדרות נכשלה"; fi ;;
        "KEY LEFT":rb|"KEY RIGHT":rb|"KEY UP":rb|"KEY DOWN":rb|"KEY TAB":rb|"CHAR 20":rb)   # #1963: Space, as the hint says
            _uk_b=$(_us_two "$_uk_id" | sed -n 2p); _uk_a=$(_us_two "$_uk_id" | sed -n 1p)
            if [ "$(_us_get opt)" = "${_uk_b%%|*}" ]; then _us_put opt "${_uk_a%%|*}"; else _us_put opt "${_uk_b%%|*}"; fi ;;
        "KEY BACKSPACE":num) _uk_v=$(_us_get buf); _us_put buf "${_uk_v%?}"; _us_put err "" ;;
        "CHAR "*:num)
            _uk_c=$(printf "\\$(printf '%03o' "0x${1#CHAR }")")
            case "$_uk_c" in [0-9]) _uk_v=$(_us_get buf); [ ${#_uk_v} -ge 7 ] || _us_put buf "$_uk_v$_uk_c"; _us_put err "" ;;
                *) _us_put err "ספרות בלבד" ;; esac ;;
    esac
    usbset_frame
}

_us_rec() { printf '%s' "$1"; shift; for _ur_f in "$@"; do printf '\t%s' "$_ur_f"; done; printf '\n'; }

usbset_frame() {
    # The frame, renamed into place: the GUI never reads half of one.
    _uf_s=$(_us_sel)
    _uf_dlg=$(_us_get dlg); _uf_err=$(_us_get err)
    usb_data_state_read; usb_settings_load
    _uf_saved=לסשן; [ "${USB_DATA_STATE:-none}" = mounted ] && _uf_saved=נשמר
    {
        _us_rec frame settings
        _us_rec hdr "דיסק-און-קי" USB "—" "${AGENT_VERSION:-unknown}" "" "שיכפול מקומי · בלי שרת"   # no clock on the stick (Nadav 20/10)
        _us_rec ttl "הגדרות הדיסק-און-קי" "מה שהשרת היה שולח. נשמר על הדיסק-און-קי ונשאר אחרי הפעלה מחדש."
        _uf_i=0
        printf '%s\n' "$USBSET_ITEMS" | while IFS='|' read -r _uf_id _uf_t _uf_x _uf_k _uf_lo _uf_hi; do
            _ic=svc; [ "$_uf_i" = 0 ] && _ic=net; [ "$_uf_i" = 1 ] && _ic=cert; [ "$_uf_k" = reset ] && _ic=rnet; [ "$_uf_k" = map ] && _ic=net
            _us_rec item "$_ic" "$_uf_t" "$_uf_x" "$([ "$_uf_i" = "$_uf_s" ] && echo 1 || echo 0)"
            _uf_i=$((_uf_i + 1))
        done
        [ "$_uf_dlg" = 1 ] || _us_rec na '<b>לא ניתן לשינוי כאן</b>: ספי ה־SMART הקבועים (5/187/10/196 מעל 0, שחיקת SSD 90%) · "עלה מאז הסבב הקודם" — אין היסטוריה בלי שרת'
        IFS='|' read -r _uf_id _uf_t _uf_x _uf_k _uf_lo _uf_hi <<EOF
$(_us_item "$_uf_s")
EOF
        if [ "$_uf_k" = reset ]; then usbset_reset_frame "$_uf_dlg" "$_uf_saved" "$_uf_err"   # #1960
        elif [ "$_uf_k" = map ]; then usbset_map_frame "$_uf_dlg" "$_uf_saved" "$_uf_err"; else   # #1962
        _uf_v=$(usbset_now "$_uf_id"); _uf_show=$(_us_show "$_uf_id" "$_uf_v")
        _uf_def=""; [ "$_uf_v" != "$(_us_default "$_uf_id")" ] || _uf_def="ברירת המחדל"
        _us_rec pane "$_uf_t" "$(_us_text "$_uf_id" pane)"
        _us_rec prow "עכשיו" "" "$_uf_show" mono "$_uf_def" ok "$_uf_saved" ""
        if [ "$_uf_dlg" = 1 ]; then
            _us_rec dlg plain 640 "$_uf_t" "$(_us_text "$_uf_id" dlg)"
            if [ "$_uf_k" = rb ]; then
                _uf_o=$(_us_get opt)
                _us_two "$_uf_id" | while IFS='|' read -r _uo_v _uo_l _uo_s; do
                    _us_rec d.opt "$_uo_l" "$_uo_s" "$([ "$_uf_o" = "$_uo_v" ] && echo 1 || echo 0)" 0; done
            else
                _us_rec d.field "$(_us_text "$_uf_id" field)" "$(_us_get buf)" text 1 1
                [ -z "$_uf_err" ] || _us_rec d.error "$_uf_err"
                if [ "$_uf_saved" = נשמר ]; then _us_rec d.note "נשמר על הדיסק-און-קי ונשאר אחרי הפעלה מחדש."
                else _us_rec d.note "<b>לסשן הזה בלבד</b> — אין מחיצת נתונים על הדיסק-און-קי, וההגדרה לא תישמר."; fi
            fi
            [ "$_uf_k" != rb ] || [ -z "$_uf_err" ] || _us_rec d.error "$_uf_err"
            _us_rec d.btn "שמירה" Enter pri 0
            _us_rec d.btn "ביטול" Esc plain 0
            [ "$_uf_k" != rb ] || _us_rec key Space "החלפה" 0
            _us_rec key Enter "שמירה" 0
            _us_rec key Esc "ביטול" 0
            if [ -n "$_uf_err" ]; then _us_rec sb warn "ערך לא תקין" "· $(_us_text "$_uf_id" short)"
            else _us_rec sb ok "דיסק-און-קי" "· $(_us_text "$_uf_id" short)"; fi
        else
            if [ "$_uf_saved" = נשמר ]; then _us_rec pnote "<b>נשמר על הדיסק-און-קי</b> (מחיצה IMGSRV_DATA) — נשאר אחרי הפעלה מחדש."
            else _us_rec pnote "<b>לסשן הזה בלבד</b> — אין מחיצת נתונים על הדיסק-און-קי, וההגדרות לא יישמרו."; fi
            _us_rec key "↑ ↓" "בחירה" 0
            _us_rec key Enter "שינוי" 0
            _us_rec key Esc "חזרה לשיכפול" 0
            _us_rec sb ok "דיסק-און-קי" "· הגדרות"
        fi
        fi   # #1960: reset
        printf '\n'
    } > "$GUI_DIR/usbset.frame.next" && mv "$GUI_DIR/usbset.frame.next" "$GUI_DIR/usbset.frame"
}

usbset_reset_frame() {
    # #1960: $1 = dialog open, $2 = נשמר|לסשן, $3 = the error. The pane: each value now and
    # its default; the dialog: the server's confirmation (F12) -- the red button, no typed word.
    _us_rec pane "החזר לברירת המחדל" "ההגדרות חוזרות לערכים הקבועים של הדיסק-און-קי, מיפוי החיבורים ושמותיהם של הדגם הזה נמחקים, וקובץ ההגדרות השמור נכתב מחדש."
    for _rf_id in min_free_gb readback_mode crc_warn_threshold temp_warn_c cloner_beep; do
        _rf_v=$(usbset_now "$_rf_id"); _rf_d=$(_us_default "$_rf_id")
        if [ "$_rf_v" = "$_rf_d" ]; then _rf_sub="ברירת המחדל"; else _rf_sub="ברירת המחדל: $(_us_show "$_rf_id" "$_rf_d")"; fi
        _us_rec prow "$(_us_text "$_rf_id" short)" "" "$(_us_show "$_rf_id" "$_rf_v")" mono "$_rf_sub" "" "" ""
    done
    _rf_map="ברירת המחדל"; if usb_map_json > /dev/null || usb_names_json > /dev/null; then _rf_map="מיפוי שמור לדגם הזה"; fi   # #1962, #2001
    _us_rec prow "מיפוי חיבורים" "" "$_rf_map" "" "ברירת המחדל: כל חיבור שיש בו דיסק, לפי הסדר" "" "" ""
    if [ "$1" = 1 ]; then
        if [ "$2" = נשמר ]; then _rf_l="ההגדרות השמורות על הדיסק-און-קי יוחלפו בערכים האלה:"
        else _rf_l="ההגדרות של הסשן הזה יוחלפו בערכים האלה:"; fi
        _us_rec dlg danger 640 "להחזיר את ברירות המחדל?" "$_rf_l"
        for _rf_id in min_free_gb readback_mode crc_warn_threshold temp_warn_c cloner_beep; do
            _us_rec d.kv "$(_us_text "$_rf_id" short)" "$(_us_iso "$(_us_show "$_rf_id" "$(_us_default "$_rf_id")")")" 0
        done
        _us_rec d.kv "מיפוי חיבורים" "ברירת המחדל — כל חיבור שיש בו דיסק, לפי הסדר" 0   # #1962, #2001
        [ -z "$3" ] || _us_rec d.error "$3"
        _rf_go=0; [ "$(_us_get foc)" = go ] && _rf_go=1
        _us_rec d.btn "ביטול" Esc plain $((1 - _rf_go))
        _us_rec d.btn "החזר לברירת המחדל" "" danger "$_rf_go"
        _us_rec key Tab "מעבר" 0
        _us_rec key Enter "ביצוע" 0
        _us_rec key Esc "ביטול" 0
        if [ -n "$3" ]; then _us_rec sb warn "לא הוחזר" "· ברירת המחדל"; else _us_rec sb ok "דיסק-און-קי" "· ברירת המחדל"; fi
    else
        if [ "$2" = נשמר ]; then _us_rec pnote "<b>נשמר על הדיסק-און-קי</b> (מחיצה IMGSRV_DATA) — נשאר אחרי הפעלה מחדש."
        else _us_rec pnote "<b>לסשן הזה בלבד</b> — אין מחיצת נתונים על הדיסק-און-קי, וההגדרות לא יישמרו."; fi
        _us_rec key "↑ ↓" "בחירה" 0
        _us_rec key Enter "החזרה" 0
        _us_rec key Esc "חזרה לשיכפול" 0
        _us_rec sb ok "דיסק-און-קי" "· הגדרות"
    fi
}
