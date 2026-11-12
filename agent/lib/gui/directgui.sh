# directgui.sh -- #2849: the build machine's direct send draws the cloner's own screen. POSIX sh.
# Nadav, 11/11: on a direct send the build machine and the cloner show the same
# cards with the same data -- only the title differs. The cloners' drawers come
# from the server's room (GET room, the one the room screen reads: drawer_list
# of the round's targets, from each cloner's reports) and are written as the
# cloner's own records (clonergui.sh): one cloner_group=name|mac per cloner, then
# its drawer=port|dev|state|bytes|total|error|rate|eta|drawer|conn|cancel|
# records. A room that could not be read is direct_targets=unread -- said on the
# screen, never an empty list (principle 5); it never fails the rest of the state.

direct_gui_records() {
    # $GUI_DIR/station.json: this tick's /agent/state. Not a direct send = nothing.
    # jq -e: 1 = not a direct send (an answer); anything else = the check itself broke
    if jq -e '(.task.type? == "direct_send") and (.task.state? == "running" or .task.state? == "pending")' \
        "$GUI_DIR/station.json" > /dev/null 2>&1; then _dg_rc=0; else _dg_rc=$?; fi
    case "$_dg_rc" in 0) ;; 1) return 0 ;; *) printf 'direct_targets=unread\n'; return 0 ;; esac
    # the room screen (mode room|direct) already read room.json this tick (guistate.sh)
    case "$(cat "$GUI_DIR/mode" 2>/dev/null)" in
        room|direct) ;;
        *) gui_get room "$GUI_DIR/room.json" 2> /dev/null || { printf 'direct_targets=unread\n'; return 0; } ;;
    esac
    jq -r '
      def clean: ((. // "") | tostring | gsub("[\\r\\n|]"; " "));
      def conn: if test("^sata:[0-9]+$") then "SATA \((ltrimstr("sata:") | tonumber) - 1)"
        elif startswith("nvme:") then "NVMe \(ltrimstr("nvme:") | ltrimstr("0000:"))" else "" end;
      def num: if type == "number" then floor else -1 end;
      if (.machines | type) != "array" then error("bad room") else
      (if (.round.target_slots? | type) == "array" then [.round.target_slots[].mac] as $t
         | .machines |= map(select(.mac as $m | any($t[]; . == $m))) else . end) |
      "cloner=" + ((.round.image_name? // "") | clean),
      # the size of each drawer (cused=dev|size|-1, the card shows "500 GB") -- only with one cloner: a dev
      # names a disk on one machine, and sdb of two cloners would give one of them the size of the other
      ([.machines[] | select([(.drawer_list // [])[] | select(.state != null or .selected == true)] | length > 0)] as $with
        | if ($with | length) == 1 then $with[0].drawer_list[] | select(.state != null or .selected == true)
            | select((.size_bytes // 0) > 0) | "cused=" + ([.dev, .size_bytes, -1] | map(clean) | join("|")) else empty end),
      (.machines[] | . as $m | [($m.drawer_list // [])[] | select(.state != null or .selected == true)] as $d
        | select($d | length > 0) |
        "cloner_group=" + ([$m.name, $m.mac] | map(clean) | join("|")),
        ($d[] | "drawer=" + ([(.port // 0), .dev, (.state // "waiting"), ((.bytes_written // 0) | num | if . < 0 then 0 else . end),
            ((.bytes_total // 0) | num | if . < 0 then 0 else . end), .error, (.rate_bps | num), (.eta_s | num), (.drawer // ""),
            (if (.port | type) == "number" then "sata:\(.port)" elif (.pci | type) == "string" then "nvme:\(.pci)" else "" end | conn),
            (.cancel.text_he? // ""), ""] | map(clean) | join("|"))))
      end' "$GUI_DIR/room.json" 2> /dev/null > "$GUI_DIR/direct.next" || { printf 'direct_targets=unread\n'; return 0; }
    cat "$GUI_DIR/direct.next"
}
