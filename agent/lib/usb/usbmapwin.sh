# usbmapwin.sh -- #2001: the connections window of the clone stick. POSIX sh.
#
# The approved mockup (Nadav 22/10): docs/design/console-redesign/mockups/
# slot-map-2001.html ?surface=usb -- one window, a row per connection: the
# connection, what is on it now, its name, a switch "פעיל". This file is the
# data and the keys; the DCUI's map window (screens_dcui.c map_win) draws it
# from the records below. usbmap.sh keeps the list and the names.
#
# Keys (the mockup's status line, Nadav's decision 5): Up/Down a connection;
# Tab its name / its switch; Space (or Left/Right) flips the switch; a letter
# starts the name, Enter ends it; "+" opens "הוסף חיבור" (a type and a number,
# no PCI address -- decision 4); F2 saves, F5 is the default, Esc leaves.
# Hebrew: Alt+Shift (lc_state.c sends the letter's code point).

USBMAP_SORT='def rsort: sort_by(if (.k | startswith("sata:")) then [0, (.k | ltrimstr("sata:") | tonumber), ""]
    elif (.k | startswith("nvme#")) then [2, (.k | ltrimstr("nvme#") | tonumber), ""] else [1, 0, .k] end);'
#: jq: the next row that is not a CD/DVD drive, $d away from $r (wrapping or not)
USBMAP_NAV='def nav($r; $d; $wrap): [to_entries[] | select(.value.kind != "optical") | .key] as $s | ($s | length) as $n
    | if $n == 0 then $r else ((($s | index([$r])) // 0) + $d) as $j
      | if $wrap then $s[(($j % $n) + $n) % $n] else $s[([([$j, 0] | max), $n - 1] | min)] end end;'

_um_rows() { cat "$USBSET/rows.json" 2>/dev/null || echo '[]'; }
_um_put() { printf '%s\n' "$1" > "$USBSET/rows.json.next" && mv "$USBSET/rows.json.next" "$USBSET/rows.json"; }
_um_q() { _uq_j=$1; shift; printf '%s' "$_uq_j" | jq "$@"; }   # $1 = JSON, then jq's arguments

_um_build() {
    # $1 = saved | default. The rows, [{k, n, a, kind, m}]: every connection the machine shows,
    # then the saved ones it does not; SATA by port, NVMe by address, then the ones added by
    # hand. Active = in this model's saved list -- else (the default) a connection with a disk.
    _ub_c=$(usb_conns_json); _ub_l=null; _ub_n='{}'
    if [ "$1" = saved ]; then _ub_l=$(usb_map_json) || _ub_l=null; _ub_n=$(usb_names_json) || _ub_n='{}'; fi
    jq -cn --argjson c "$_ub_c" --argjson l "$_ub_l" --argjson n "$_ub_n" --argjson max "$USBMAP_ROWS" "$USBMAP_RESOLVE$USBMAP_SORT"'
        [$c[].k | select(startswith("nvme:"))] as $d | (($l // []) + ($n | keys)) as $t
        | (($l // []) | map(rkey($d; $t))) as $L | ($n | with_entries(.key |= rkey($d; $t))) as $N
        | ([$c[].k] + $L + ($N | keys)) | reduce .[] as $k ([]; if index([$k]) then . else . + [$k] end)
        | map(. as $k | ($c | map(select(.k == $k)) | .[0]) as $x
            | {k: $k, n: ($N[$k] // ""), kind: ($x.kind // "missing"), m: false}
            | .a = (.kind != "optical" and (if $l == null then .kind == "disk" else ($L | index([$k])) != null end)))
        | rsort | .[:$max]'
}

_um_isact() { [ "$(_um_q "$1" -r --argjson r "$2" '.[$r] | .a and .kind != "optical"')" = true ]; }

usbset_map_open() {
    _um_put "$(_um_build saved)"; cp "$USBSET/rows.json" "$USBSET/rows0.json"
    _us_put mrow "$(_um_q "$(_um_rows)" "$USBMAP_NAV"' nav(0; 0; false)')"; _us_put mcol 1; _us_put madd 0
    [ -n "$(usb_model)" ] || _us_put err "<b>לא נשמר:</b> לא זוהה דגם המחשב (DMI) — אין לפי מה לשמור את המיפוי."
}

_um_cp_ok() {   # $1 = a code point. One a name may have (USBMAP_NAME_RE).
    case "$1" in 32|35|40|41|45|46|47|95) return 0 ;; esac
    { [ "$1" -ge 48 ] && [ "$1" -le 57 ]; } || { [ "$1" -ge 65 ] && [ "$1" -le 90 ]; } \
        || { [ "$1" -ge 97 ] && [ "$1" -le 122 ]; } || { [ "$1" -ge 1488 ] && [ "$1" -le 1514 ]; }
}

_um_type() {   # $1 = the name now, $2 = a code point. The name with it -- or 1, and why in err.
    if ! _um_cp_ok "$2"; then _us_put err "בשם: אותיות, ספרות, רווח ו־- . # ( ) / בלבד"; return 1; fi
    if [ "$(jq -n --arg s "$1" '$s | length')" -ge 24 ]; then _us_put err "עד 24 תווים בשם"; return 1; fi
    jq -nr --arg s "$1" --argjson cp "$2" '$s + ([$cp] | implode)'
}

_um_chop() { jq -nr --arg s "$1" '$s | explode | .[:-1] | implode'; }   # a whole letter, never half of one

usbset_map_key() {
    # $1 = a DCUI token, in the window.
    _mk_j=$(_um_rows); _mk_r=$(_us_get mrow); case "$_mk_r" in ''|*[!0-9]*) _mk_r=0 ;; esac
    _mk_c=$(_us_get mcol); [ "$_mk_c" = 0 ] || _mk_c=1
    _mk_cp=""; case "$1" in "CHAR "*) _mk_h=${1#CHAR }; case "$_mk_h" in ''|*[!0-9a-f]*|???????*) return 0 ;; esac
        _mk_cp=$((0x$_mk_h)) ;; esac
    _us_put err ""
    if [ "$(_us_get madd)" = 1 ]; then usbset_map_add_key "$1" "$_mk_cp" "$_mk_j"; return 0; fi
    case "$1" in
        "KEY ESC") _us_put dlg 0 ;;
        "KEY F2") usbset_map_save "$_mk_j" ;;
        "KEY F5") _um_put "$(_um_build default)"; _us_put mrow "$(_um_q "$(_um_rows)" "$USBMAP_NAV"' nav(0; 0; false)')"; _us_put mcol 1 ;;
        "KEY UP"|"KEY DOWN")
            _mk_r=$(_um_q "$_mk_j" "$USBMAP_NAV"" nav($_mk_r; $([ "$1" = "KEY UP" ] && echo -1 || echo 1); false)"); _us_put mrow "$_mk_r"
            _um_isact "$_mk_j" "$_mk_r" || _us_put mcol 1 ;;
        "KEY TAB"|"KEY BACKTAB") if [ "$_mk_c" = 1 ] && _um_isact "$_mk_j" "$_mk_r"; then _us_put mcol 0; else _us_put mcol 1; fi ;;
        "KEY ENTER") _us_put mcol 1 ;;   # the name is done
        "KEY LEFT"|"KEY RIGHT"|"CHAR 20")
            if [ "$_mk_c" = 0 ] && [ "$1" = "CHAR 20" ]; then _mk_n=$(_um_type "$(_um_q "$_mk_j" -r --argjson r "$_mk_r" '.[$r].n')" 32) \
                && _um_put "$(_um_q "$_mk_j" -c --argjson r "$_mk_r" --arg v "$_mk_n" '.[$r].n = $v')"
            elif [ "$_mk_c" = 1 ]; then _um_put "$(_um_q "$_mk_j" -c --argjson r "$_mk_r" 'if .[$r].kind == "optical" then . else .[$r].a |= not end')"; fi ;;
        "KEY BACKSPACE") [ "$_mk_c" = 1 ] || _um_put "$(_um_q "$_mk_j" -c --argjson r "$_mk_r" --arg v "$(_um_chop "$(_um_q "$_mk_j" -r --argjson r "$_mk_r" '.[$r].n')")" '.[$r].n = $v')" ;;
        "CHAR 2b") usbset_map_add_open "$_mk_j" ;;
        "CHAR "*)
            if ! _um_isact "$_mk_j" "$_mk_r"; then _us_put err "החיבור לא פעיל — הפעל אותו (רווח) כדי לתת לו שם"
            elif _mk_n=$(_um_type "$(_um_q "$_mk_j" -r --argjson r "$_mk_r" '.[$r].n')" "$_mk_cp"); then
                _um_put "$(_um_q "$_mk_j" -c --argjson r "$_mk_r" --arg v "$_mk_n" '.[$r].n = $v')"; _us_put mcol 0
            else _us_put mcol 0; fi ;;
    esac
    return 0
}

usbset_map_save() {
    # $1 = the rows. F2: 1-8 active; the default itself is saved as no list (usbmap.sh).
    _ms_a=$(_um_q "$1" '[.[] | select(.a and .kind != "optical")] | length')
    _ms_d=$(_um_build default)
    if [ -z "$(usb_model)" ]; then _us_put err "<b>לא נשמר:</b> לא זוהה דגם המחשב (DMI) — אין לפי מה לשמור את המיפוי."; return 0; fi
    if [ "$_ms_a" -lt 1 ]; then _us_put err "<b>לא נשמר:</b> אין אף חיבור פעיל — צריך לפחות אחד."; return 0; fi
    if [ "$_ms_a" -gt 8 ]; then _us_put err "<b>לא נשמר:</b> $_ms_a חיבורים פעילים — עד 8."; return 0; fi
    if [ "$(_um_q "$1" -c 'map({k, a, n})')" = "$(_um_q "$_ms_d" -c 'map({k, a, n})')" ]; then usb_map_save ""; else usb_map_save "$1"; fi
    case $? in
        0) _us_put dlg 0 ;;
        2) _us_put err "<b>לא נשמר:</b> הכתיבה לדיסק-און-קי נכשלה. השינויים עדיין כאן — נסה שוב, ואם זה חוזר, החלף דיסק-און-קי." ;;
        *) _us_put err "<b>לא נשמר:</b> הכתיבה לדיסק-און-קי הצליחה, אבל הקריאה החוזרת לא החזירה את אותו מיפוי. השינויים עדיין כאן — נסה שוב, ואם זה חוזר, החלף דיסק-און-קי." ;;
    esac
}

usbset_map_add_open() {   # $1 = the rows. The form: SATA, the next port's number, the number field.
    if [ "$(_um_q "$1" length)" -ge "$USBMAP_ROWS" ]; then _us_put err "אין עוד מקום — עד $USBMAP_ROWS חיבורים"; return 0; fi
    _us_put madd 1; _us_put atype SATA; _us_put aname ""; _us_put afoc 1
    _us_put anum "$(_um_q "$1" '[.[].k | select(startswith("sata:")) | ltrimstr("sata:") | tonumber] | max // 0')"
}

usbset_map_add_key() {
    # $1 = token, $2 = its code point, $3 = rows. Tab between the fields; Enter adds, Esc closes.
    _ak_f=$(_us_get afoc); case "$_ak_f" in 0|1|2) ;; *) _ak_f=1 ;; esac
    _ak_t=$(_us_get atype); _ak_v=$(_us_get anum); _ak_nm=$(_us_get aname)
    case "$1" in
        "KEY ESC") _us_put madd 0 ;;
        "KEY TAB") _us_put afoc $(( (_ak_f + 1) % 3 )) ;;
        "KEY BACKTAB") _us_put afoc $(( (_ak_f + 2) % 3 )) ;;
        "KEY ENTER") usbset_map_add "$3" "$_ak_t" "$_ak_v" "$_ak_nm" ;;
        "KEY LEFT"|"KEY RIGHT"|"CHAR 20")
            if [ "$_ak_f" = 0 ]; then
                if [ "$_ak_t" = SATA ]; then _us_put atype NVMe
                    _us_put anum $(( $(_um_q "$3" '[.[].k | select(startswith("nvme"))] | length') + 1 ))
                else _us_put atype SATA; _us_put anum "$(_um_q "$3" '[.[].k | select(startswith("sata:")) | ltrimstr("sata:") | tonumber] | max // 0')"; fi
            elif [ "$_ak_f" = 2 ] && [ "$1" = "CHAR 20" ]; then _ak_nm=$(_um_type "$_ak_nm" 32) && _us_put aname "$_ak_nm"; fi ;;
        "KEY BACKSPACE") [ "$_ak_f" = 1 ] && _us_put anum "${_ak_v%?}"; [ "$_ak_f" = 2 ] && _us_put aname "$(_um_chop "$_ak_nm")" ;;
        "CHAR "*)
            if [ "$_ak_f" = 1 ]; then case "$2" in 4[89]|5[0-7]) [ ${#_ak_v} -ge 2 ] || _us_put anum "$_ak_v$(($2 - 48))" ;;
                *) _us_put err "מספר — ספרות בלבד" ;; esac
            elif [ "$_ak_f" = 2 ]; then _ak_nm=$(_um_type "$_ak_nm" "$2") && _us_put aname "$_ak_nm"; fi ;;
    esac
}

usbset_map_add() {
    # $1 rows, $2 SATA|NVMe, $3 its number, $4 the name. SATA n = sata:<n+1>; NVMe n = nvme#<n>,
    # n past the NVMe the machine shows -- the address is filled in when a disk is seen there.
    case "$3" in ''|*[!0-9]*) _us_put err "מספר — ספרות בלבד"; return 0 ;; esac
    if [ "$2" = SATA ]; then
        [ "$3" -le 31 ] || { _us_put err "SATA — מ־0 עד 31"; return 0; }
        _ad_k="sata:$(( $3 + 1 ))"
    else
        _ad_s=$(_um_q "$1" '[.[].k | select(startswith("nvme:"))] | length')
        { [ "$3" -ge 1 ] && [ "$3" -le 9 ]; } || { _us_put err "NVMe — מ־1 עד 9"; return 0; }
        [ "$3" -gt "$_ad_s" ] || { _us_put err "NVMe $3 כבר נמצא במחשב"; return 0; }
        _ad_k="nvme#$3"
    fi
    if [ "$(_um_q "$1" --arg k "$_ad_k" 'map(.k) | index($k)')" != null ]; then _us_put err "$2 $3 כבר ברשימה"; return 0; fi
    _ad_j=$(_um_q "$1" -c --arg k "$_ad_k" --arg n "$4" "$USBMAP_SORT"' . + [{k: $k, n: $n, a: true, kind: "missing", m: true}] | rsort')
    _um_put "$_ad_j"; _us_put madd 0; _us_put mcol 1
    _us_put mrow "$(_um_q "$_ad_j" --arg k "$_ad_k" 'map(.k) | index($k)')"
}

_um_view() {
    # $1 = rows, $2 = conns, $3 = the focused row, $4 = its column (0 name, 1 switch; -1 none).
    # "label|pci|model|size|tag|name|placeholder|focus|active|kind" per row -- d.conn's fields.
    _um_q "$1" -r --argjson cs "$2" --argjson r "$3" --argjson c "$4" '. as $rows
        | [$rows[].k | select(startswith("nvme:"))] as $nv
        | range(0; length) as $i | $rows[$i] as $x | ($cs | map(select(.k == $x.k)) | .[0]) as $o
        | ($x.a and $x.kind != "optical") as $on
        | ([$rows[:$i + 1][] | select(.a and .kind != "optical")] | length) as $d
        | [ (if ($x.k | startswith("sata:")) then "SATA \(($x.k | ltrimstr("sata:") | tonumber) - 1)"
             elif ($x.k | startswith("nvme#")) then "NVMe \($x.k | ltrimstr("nvme#"))"
             else "NVMe \(($nv | index([$x.k])) + 1)" end),
            (if ($x.k | startswith("nvme:")) then ($x.k | ltrimstr("nvme:") | ltrimstr("0000:")) else "" end),
            (if $o.kind == "disk" then $o.m elif $o.kind == "optical" then "כונן CD/DVD · ⁨\($o.m)⁩" else "ריק" end),
            (if $o.kind == "disk" then $o.s else "" end),
            (if $x.m then "נוסף ידנית · לא נמצא במחשב" elif $o == null then "לא נמצא במחשב" else "" end),
            $x.n, (if $on then "דיסק \($d)" else "" end),
            (if $i == $r and $c >= 0 then $c + 1 else 0 end), (if $on then 1 else 0 end),
            (if $x.m then "manual" else ($o.kind // "missing") end) ] | join("|")'
}

usbset_map_frame() {
    # $1 = window open, $2 = נשמר|לסשן, $3 = the error. Closed: the drawers now (the settings
    # pane). Open: the mockup's window -- dlg map, a d.conn per row, the form or "הוסף חיבור",
    # d.foot and the three buttons.
    _mf_m=$(usb_model); _mf_c=$(usb_conns_json)
    _mf_where="נשמר על הדיסק-און-קי לדגם הזה"; [ "$2" = נשמר ] || _mf_where="לסשן הזה בלבד — אין מחיצת נתונים על הדיסק-און-קי"
    if [ "$1" != 1 ]; then
        _us_rec pane "מיפוי חיבורים" "כל החיבורים של המחשב — לכל אחד שם, ופעיל או לא. נשמר לפי דגם המחשב, לא לפי הדיסקים."
        _um_view "$(_um_build saved)" "$_mf_c" 0 -1 | while IFS='|' read -r _mf_l _mf_p _mf_o _mf_s _mf_t _mf_n _mf_h _mf_f _mf_a _mf_k; do
            [ "$_mf_a" = 1 ] || continue
            _us_rec prow "${_mf_n:-$_mf_h}" "" "$_mf_l" mono "$_mf_o${_mf_s:+ · $_mf_s}" "" "" ""
        done
        if ! usb_map_json > /dev/null; then _us_rec pnote "<b>ברירת המחדל</b> — אין מיפוי שמור לדגם הזה: כל חיבור שיש בו דיסק פעיל, \"דיסק 1, 2, 3…\" לפי הסדר."
        else _us_rec pnote "<b>$_mf_where</b> — חל בכל מחשב מאותו דגם."; fi
        _us_rec key "↑ ↓" "בחירה" 0; _us_rec key Enter "שינוי" 0; _us_rec key Esc "חזרה לשיכפול" 0
        _us_rec sb ok "דיסק-און-קי" "· הגדרות"
        return 0
    fi
    _mf_j=$(_um_rows); _mf_r=$(_us_get mrow); case "$_mf_r" in ''|*[!0-9]*) _mf_r=0 ;; esac
    _mf_x=$(_us_get mcol); [ "$_mf_x" = 0 ] || _mf_x=1; _mf_add=$(_us_get madd); [ "$_mf_add" != 1 ] || _mf_x=-1
    _us_rec dlg map 0 "מיפוי חיבורים" "$([ -n "$_mf_m" ] && _us_iso "$_mf_m" || echo "דגם לא ידוע") · $_mf_where"
    _mf_saved=0; if usb_map_json > /dev/null; then _mf_saved=1; fi
    _mf_d=$(_um_q "$_mf_j" --argjson o "$(cat "$USBSET/rows0.json" 2>/dev/null || echo '[]')" \
        '[.[] as $r | ($o | map(select(.k == $r.k)) | .[0]) as $p | select($p == null or $p.a != $r.a or $p.n != $r.n)] | length')
    if [ -n "$3" ]; then _us_rec d.alert "$3" ""
    elif [ "$_mf_saved" = 0 ]; then _us_rec d.alert "<b>אין מיפוי שמור</b> — זו ברירת המחדל: כל חיבור שיש בו דיסק פעיל, והשמות \"דיסק 1, 2, 3…\" לפי סדר החיבורים. שינוי ושמירה יוצרים מיפוי." info; fi
    _um_view "$_mf_j" "$_mf_c" "$_mf_r" "$_mf_x" | while IFS='|' read -r _mf_l _mf_p _mf_o _mf_s _mf_t _mf_n _mf_h _mf_f _mf_a _mf_k; do
        _us_rec d.conn "$_mf_l" "$_mf_p" "$_mf_o" "$_mf_s" "$_mf_t" "$_mf_n" "$_mf_h" "$_mf_f" "$_mf_a" "$_mf_k"
    done
    if [ "$_mf_add" = 1 ]; then
        _us_rec d.add "$(_us_get atype)" "$(_us_get anum)" "$(_us_get aname)" "$(_us_get afoc)" \
            "לחיבור שהמחשב לא מציג עכשיו — למשל חריץ M.2 ריק, או מגירה שהבקר שלה כבוי. השורה תסומן \"לא נמצא במחשב\" עד שיופיע בו דיסק."
    elif [ "$(_um_q "$_mf_j" length)" -lt "$USBMAP_ROWS" ]; then _us_rec d.addlink "+ הוסף חיבור" "לחיבור שהמחשב לא מציג"; fi
    _mf_a=$(_um_q "$_mf_j" '[.[] | select(.a and .kind != "optical")] | length')
    _mf_dt=""; [ "$_mf_d" = 0 ] || _mf_dt="· $_mf_d שינויים לא נשמרו"; [ "$_mf_d" != 1 ] || _mf_dt="· שינוי אחד לא נשמר"
    _mf_st="$_mf_a פעילים"; [ "$_mf_saved$_mf_d" != 00 ] || _mf_st="$_mf_st · ברירת המחדל"
    _us_rec d.foot "$_mf_st" "$_mf_dt"
    [ "$_mf_saved" = 0 ] || _us_rec d.btn "החזר לברירת המחדל" F5 ghost 0
    _us_rec d.btn "ביטול" Esc plain 0
    _us_rec d.btn "שמירה" F2 pri 0
    if [ "$_mf_add" = 1 ]; then _us_rec key Tab "שדה" 0; _us_rec key Enter "הוסף" 0; _us_rec key Esc "ביטול" 0
    else _us_rec key "↑ ↓" "חיבור" 0; _us_rec key Tab "שם / פעיל" 0; _us_rec key "רווח" "פעיל" 0
        _us_rec key "+" "הוסף חיבור" 0; _us_rec key "Alt+Shift" "עב/EN" 0; _us_rec key F2 "שמירה" 0; fi
    case "$3" in '') _us_rec sb ok "דיסק-און-קי" "· מיפוי חיבורים" ;; *"לא נשמר"*) _us_rec sb bad "לא נשמר" "· מיפוי חיבורים" ;;
        *) _us_rec sb warn "ערך לא תקין" "· מיפוי חיבורים" ;; esac
}
