# guiearly.sh -- the GUI before the first hello (#1709). POSIX sh (busybox ash).
#
# Owner, 18/10: no black screen, and a second saved is worth building. Until
# #1709 the GUI came up only after the first hello -- the module load, the
# link, DHCP and hello's own probe went by on a blank or text screen. Now:
#   1. init starts gui_early_boot in the background before the module load.
#      The moment a framebuffer exists it starts the GUI's keeper (gui_keep,
#      guiparent.sh) on the boot screen: "מתחבר לשרת…" and its three stages
#      (network, server, task), drawn by the bridge's gui_early_loop.
#   2. The agent adopts that keeper (gui_early_adopt): gui_parent finds it
#      alive and starts no second GUI. The first gui_parent after the hello
#      hands the role over (gui_early_role, $RUN_DIR/gui.screen) and the bridge
#      switches its state writer to that role's -- the same GUI process, no
#      restart.
#   3. A path that draws no GUI -- an unknown or unregistered machine (it boots
#      from its disk, principle 1), a station's restore, a task with no screen
#      -- closes it first (gui_early_settle), so that path's own screen shows.
# No GPU framebuffer by the end of the module load (a cloner with no screen):
# nothing starts here, and gui_parent starts the GUI after the hello, as before.

#: How long the early start waits for a framebuffer, in seconds -- a ceiling
#: only: it also ends the moment init says the modules are loaded.
GUI_EARLY_WAIT_S="${GUI_EARLY_WAIT_S:-60}"
#: How long closing waits for a GUI that is still starting to name its pids --
#: a ceiling only: it ends the moment the bridge writes station.pid.
GUI_EARLY_STOP_WAIT_S="${GUI_EARLY_STOP_WAIT_S:-5}"

gui_early_boot() {
    # init, in the background: wait for the framebuffer, then start the keeper.
    # ui.sh: the keeper's gui_beat_fresh (#1946). Without it every look was "not
    # found" -- read as a stale beat -- and the early GUI was killed a tick after
    # it started, and again, up to the static screen (#2275).
    . "$LIB_DIR/common.sh"; . "$LIB_DIR/waits.sh"; . "$LIB_DIR/ui.sh"; . "$LIB_DIR/splash.sh"; . "$LIB_DIR/guiparent.sh"
    parse_cmdline
    # #1190: the installer owns the display; no server = the agent's die_local, as before
    [ "$IMAGESRV_MODE" != installer ] && [ -n "$IMAGESRV_SERVER" ] || return 0
    [ -x "$GUI_KIOSK" ] && [ -x "$GUI_STATION" ] || return 0
    _ge_fb=${FB_DEV:-/dev/fb0}; _ge_t=0
    # Only the GPU driver's framebuffer. The firmware's (simpledrm, efifb,
    # vesafb: "simpledrmdrmfb", "EFI VGA", "VESA VGA") is there from the
    # first second and is torn down when i915/amdgpu loads -- a GUI drawing on
    # it would leave the screen black. Not there by the end of the module load
    # (no GPU driver, or a firmware one only): gui_parent, after the hello.
    while :; do
        _ge_done=0; [ ! -e "$RUN_DIR/modules.done" ] || _ge_done=1
        if [ -c "$_ge_fb" ] && read -r _ge_name < "$SYSROOT/sys/class/graphics/${_ge_fb##*/}/name" 2>/dev/null; then
            case "$_ge_name" in *simple*|*VGA*) ;; *) break ;; esac
        fi
        [ "$_ge_done" = 0 ] || return 0
        [ "$_ge_t" -lt $((GUI_EARLY_WAIT_S * 10)) ] || { log "native GUI: no GPU framebuffer within ${GUI_EARLY_WAIT_S}s of init -- started after the hello"; return 0; }
        poll_tick; _ge_t=$((_ge_t + _tk))
    done
    # gui_parent repeats this check after the hello, and says what failed there
    "$GUI_STATION" --help >> "$LOG_FILE" 2>&1 || return 0
    SERVER=$IMAGESRV_SERVER
    case "$SERVER" in http://*|https://*) ;; *) SERVER="http://$SERVER" ;; esac
    MAC=; IP=; GUI_SCREEN=boot
    export SERVER MAC IP RUN_DIR LIB_DIR GUI_SCREEN
    gui_keep &
    echo "$!" > "$RUN_DIR/gui.pid"   # log() keeps off the screen from here (common.sh)
    # the boot mark init sends with agent-net (gui:<uptime>, boot/trace.py INIT_MARKS)
    cut -d' ' -f1 "${UPTIME_FILE:-/proc/uptime}" > "$RUN_DIR/gui.early.up" 2>/dev/null || rm -f "$RUN_DIR/gui.early.up"
    log "native GUI started before the hello (#1709), on $_ge_fb"
}

gui_early_adopt() {
    # imagesrv-agent, before the first hello: init's keeper is this agent's GUI.
    { read -r _ge_p < "$RUN_DIR/gui.pid" && kill -0 "$_ge_p"; } 2>/dev/null || return 0
    _gui_pid=$_ge_p; _gui_early=1
    log "native GUI: up since init (#1709) -- adopted"
}

gui_early_role() {
    # gui_parent, with the adopted GUI alive: the hello named the role. Once.
    [ ! -s "$RUN_DIR/gui.screen" ] || return 0
    case "$D_ROLE" in classroom) _ge_r=class ;; cloner) _ge_r=cloner ;; *) _ge_r=menu ;; esac
    printf '%s\n%s\n%s\n' "$_ge_r" "$MAC" "$IP" > "$RUN_DIR/gui.screen.next" &&
        mv "$RUN_DIR/gui.screen.next" "$RUN_DIR/gui.screen" ||
        log "native GUI: the role could not be handed over -- the boot screen stays"
}

gui_early_settle() {
    # $1 = decide's answer. An adopted GUI still on the boot screen, on a path
    # that never calls gui_parent, is closed before that path draws its own.
    [ "${_gui_early:-0}" = 1 ] && [ ! -s "$RUN_DIR/gui.screen" ] || return 0
    case "$1" in
        local|unknown|bad_answer) ;;   # #2666: bad_answer dies like local -- the same screen path
        restore) case "$D_ROLE" in classroom|cloner) return 0 ;; esac ;;
        task) [ "$(json_get "$RESP" ".task.type")" != capture ] || return 0 ;;
        *) return 0 ;;
    esac
    gui_early_stop "$1"
}

gui_early_stop() {
    # The keeper first (SIGKILL: it holds nothing), so nothing starts the GUI
    # again; then the GUI process, which gives the display back to the text
    # console on SIGTERM -- and its bridge and state writer end with it.
    # A GUI still starting has no station.pid yet: killing only the keeper then
    # left the bridge it had started to bring the GUI up anyway (round 20, 31/10).
    # So while the keeper lives, wait for the bridge to name its GUI (a ceiling).
    _ge_t=0
    until read -r _ge_s _ge_w < "$RUN_DIR/gui/station.pid" 2>/dev/null; do
        _ge_s=; _ge_w=
        kill -0 "$_gui_pid" 2>/dev/null && [ "$_ge_t" -lt $((GUI_EARLY_STOP_WAIT_S * 10)) ] || break
        poll_tick; _ge_t=$((_ge_t + _tk))
    done
    kill -9 "$_gui_pid" 2>/dev/null
    for _ge_p in $_ge_s $_ge_w; do
        kill "$_ge_p" 2>/dev/null && wait_pid "$_ge_p" "$WAIT_HELPER_S" "early native GUI $_ge_p" || :
    done
    rm -f "$RUN_DIR/gui.pid"; _gui_pid=; _gui_early=0
    log "native GUI closed: the '$1' path draws none of its own (#1709)"
}

# --- the bridge's side (imagesrv-kiosk, gui_main with GUI_SCREEN=boot) --------

gui_early_state() {
    # The boot screen's records: the stage now, the server from the kernel line,
    # and a failure screen that die_local asked for meanwhile (failscreen.sh).
    if [ -s "$RUN_DIR/gui.screen" ]; then _ge_n=3; _ge_w=משימה
    elif [ -s "$RUN_DIR/net.conf" ]; then _ge_n=2; _ge_w=שרת
    else _ge_n=1; _ge_w=רשת; fi
    printf 'message=מתחבר לשרת…|רשת ← שרת ← משימה  ·  שלב %s מתוך 3: %s\nscreen=boot\n' "$_ge_n" "$_ge_w"
    gui_server_addr
    if [ -s "$RUN_DIR/fail.state" ]; then cat "$RUN_DIR/fail.state"; fi
}

gui_early_env() {
    # The role, MAC and IP the bridge was started without, from gui_early_role.
    { IFS= read -r _ge_r && IFS= read -r _ge_m && IFS= read -r _ge_i; } < "$RUN_DIR/gui.screen" 2>/dev/null || return 1
    case "$_ge_r" in menu) GUI_SCREEN= ;; class|cloner) GUI_SCREEN=$_ge_r ;; *) return 1 ;; esac
    MAC=$_ge_m; IP=$_ge_i
    export GUI_SCREEN MAC IP
}

gui_early_loop() {
    # The state writer while the role is not known; then that role's writer.
    # Written when the stage moves, else every 2 s (the GUI calls a file older
    # than 10 s stale) -- not every tick: each write is a redraw, and the
    # modules are still loading.
    _ge_last=; _ge_k=0
    until gui_early_env; do
        _ge_now=$(gui_early_state) || return 1
        if [ "$_ge_now" != "$_ge_last" ] || [ "$_ge_k" -ge 20 ]; then
            printf '%s\n' "$_ge_now" > "$GUI_DIR/state.next" && mv "$GUI_DIR/state.next" "$GUI_DIR/state" || return 1
            _ge_last=$_ge_now; _ge_k=0
        fi
        poll_tick; _ge_k=$((_ge_k + _tk))
    done
    [ "$GUI_SCREEN" != class ] || printf 'classes\n' > "$GUI_DIR/mode" || return 1
    gui_early_state > "$GUI_DIR/state.next" && mv "$GUI_DIR/state.next" "$GUI_DIR/state" || return 1
    if [ "$GUI_SCREEN" = cloner ]; then cloner_gui_state_loop; else gui_state_loop; fi
}
