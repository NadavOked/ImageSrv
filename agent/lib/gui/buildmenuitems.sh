# buildmenuitems.sh -- the gates of the build machine's GUI menu: the class
# card (v2) and the toolbox (v1.1). POSIX sh (busybox ash).
# guistate.sh reads them into menu_class= / menu_tools=, and the GUI hides a
# card on 0, so v1 and v2 differ in ONE place. The text menu that read the
# same gates was removed with the rest of the text screens (owner, 11/10:
# GUI only).

class_deploy_on() {
    # The hello answer is the one place the switch is read from, for the
    # GUI state (guistate.sh). $RESP is the agent's name for it; the kiosk
    # has only $RUN_DIR.
    # #1081: classrooms is a server edition flag (hello /state). Missing =
    # off (principle 1). v2 turns it on. The operator switch sits on top.
    _hello="${RESP:-$RUN_DIR/response.json}"
    _flag=$(json_get "$_hello" ".classrooms")
    if [ "$_flag" != true ] && [ -n "${GUI_DIR:-}" ] && [ -f "$GUI_DIR/station.json" ]; then
        _flag=$(json_get "$GUI_DIR/station.json" ".classrooms")
    fi
    [ "$_flag" = true ] || return 1
    [ "$(json_get "$_hello" ".class_deploy_enabled")" = true ]
}

tools_on() {
    # v1 ships without the toolbox (Nadav, 19/09; v1.1 turns it on). Same
    # source and same fallback as class_deploy_on: `.tools` from the hello,
    # else from /state. Missing = off (principle 1). guistate.sh writes
    # menu_tools= from here, and the GUI hides its tools button on 0.
    # #2804: the USB stick has no server and no hello -- there the toolbox
    # is on when the selection packed in it has a tool to show.
    [ "${IMAGESRV_MODE:-}" != usbclone ] || { tools_usb_on; return; }
    _hello="${RESP:-$RUN_DIR/response.json}"
    _flag=$(json_get "$_hello" ".tools")
    if [ "$_flag" != true ] && [ -n "${GUI_DIR:-}" ] && [ -f "$GUI_DIR/station.json" ]; then
        _flag=$(json_get "$GUI_DIR/station.json" ".tools")
    fi
    [ "$_flag" = true ]
}

tools_usb_on() {
    # On iff tools_list (tools.sh) shows at least one row: the packed
    # selection file exists and names an id a module runs -- and, for the
    # tools that save files, the stick has its data partition. Read once per
    # process: neither changes during a boot, and the state loop asks every
    # second. Not loaded / unreadable = off.
    if [ -z "${TOOLS_USB_ON:-}" ]; then
        TOOLS_USB_ON=0
        if command -v tools_list >/dev/null 2>&1 && [ -n "$(tools_list 2>/dev/null | head -n 1)" ]; then TOOLS_USB_ON=1; fi
    fi
    [ "$TOOLS_USB_ON" = 1 ]
}

tools_menu_record() {   # menu_tools=0|1 for every screen with the button (mockup 14: build, cloner, stick)
    if tools_on; then echo menu_tools=1; else echo menu_tools=0; fi
}
