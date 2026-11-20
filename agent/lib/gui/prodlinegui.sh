# prodlinegui.sh -- the cloner screen's production line records (#3181).
# POSIX sh (busybox ash). Runs in the kiosk (cloner_gui_state); display only.
#
# A production line (Nadav 14/11) is a round that asks for more disks than
# the drawers. It is a regular round; three things on the cloner's screen
# are its own: "נכתבו X מתוך Y", "אפשר להוציא" on a drawer the server
# counted removable (a safe detach succeeded), and "הכנס דיסק" with the next
# name on an empty drawer. The server says them (interfaces.md, "פס ייצור"):
# `production_line` in the progress reply ($RUN_DIR/progress.reply, every
# report), and in the hello's session until the first reply.
#
# The hello decides first: a regular round (null, missing, not an object)
# writes nothing, whatever an older reply holds -- a regular round's screen
# does not change. Records (state.c reads them, a record not in shape is
# not said):
#   pline=<written>|<target>|<remaining>|<1 = the round has names, 0 = none>
#   pline_ready=<port>                      one per removable slot
#   pline_insert=<port>|<drawer>|<name>     one per empty slot that waits

gui_prodline_records() {
    [ "$(json_get "$RUN_DIR/response.json" '.session.production_line | type')" = object ] || return 0
    _pl_f="$RUN_DIR/response.json"; _pl_p='.session.production_line'
    if [ "$(json_get "$RUN_DIR/progress.reply" '.production_line | type')" = object ]; then
        _pl_f="$RUN_DIR/progress.reply"; _pl_p='.production_line'
    fi
    # one token per field; a name keeps its spaces (the last field), never a '|' or a line break
    json_get "$_pl_f" "$_pl_p"' |
        (if ([.written, .target, .remaining] | all(type == "number" and . >= 0 and . == floor))
         then "c \(.written)|\(.target)|\(.remaining)|\(if ((.names | type) == "object" and (.names | length) > 0)
                                                        or (.next_name | type) == "string" then 1 else 0 end)"
         else empty end),
        (.removable | if type == "array" then .[] | select(type == "number" and . >= 1 and . == floor) | "r \(.)" else empty end),
        (.insert | if type == "object" then to_entries[] | select((.key | test("^[1-9][0-9]{0,2}$")) and (.value | type) == "string"
                                                                 and (.value | length) > 0)
                                        | "i \(.key) \(.value | gsub("[|\r\n]"; " "))" else empty end)' |
    while read -r _pl_k _pl_a _pl_b; do
        case "$_pl_k" in
            c) printf 'pline=%s\n' "$_pl_a" ;;
            r) printf 'pline_ready=%s\n' "$_pl_a" ;;
            i) _pl_d=$(disk_drawer "" "$_pl_a"); printf 'pline_insert=%s|%s|%s\n' "$_pl_a" "${_pl_d%%|*}" "$_pl_b" ;;
        esac
    done
}
