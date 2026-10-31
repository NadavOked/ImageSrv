# decide.sh -- the agent's decision table. Pure logic, no I/O, fully testable.
# POSIX sh (busybox ash).
#
# Input (environment):
#   D_SCHEMA        schema field from the server answer ("1", "2", ...)
#   D_KNOWN         "true" / "false"
#   D_ROLE          "build" / "cloner" / "classroom" / "unknown"
#   D_TASK          "null" or a JSON object (anything that is not "null")
#   D_SESSION_STATE "none" (session was null) / "open" / "running" / "closed"
#   D_MODE          "normal" / "recovery" (from the kernel command line)
#
# Output (stdout, one word):
#   local            reboot to the local disk -- the default for anything unclear
#   unknown          show "MAC not registered" briefly, then local
#   recovery         show the single-station recovery menu
#   task             a direct task was assigned (restore/capture)
#   wait_open        classroom: session open -- show the waiting screen, poll
#   wait_poll        cloner: headless -- report and keep polling
#   restore          session running -- join the stream and write
#
# The guiding rule, same as the GRUB generator: every row that is not an
# explicit, well-understood work order ends in "local".

decide() {
    # An interface version we do not speak: do not guess, boot locally.
    [ "$D_SCHEMA" = "1" ] || { echo "local"; return; }

    # The server does not know this MAC. It offers nothing; neither do we.
    [ "$D_KNOWN" = "true" ] || { echo "unknown"; return; }

    # ESC was pressed at the GRUB menu: the person asked for the menu.
    if [ "$D_MODE" = "recovery" ]; then
        echo "recovery"
        return
    fi

    # A direct task beats session logic: it names this machine, not a group.
    if [ "$D_TASK" != "null" ] && [ -n "$D_TASK" ]; then
        echo "task"
        return
    fi

    case "$D_ROLE" in
        classroom)
            case "$D_SESSION_STATE" in
                open)    echo "wait_open" ;;
                running) echo "restore" ;;
                *)       echo "local" ;;   # none / closed / anything else
            esac
            ;;
        cloner)
            # Cloner machines have no OS of their own -- their drawers are
            # the product. They wait for work instead of booting locally.
            case "$D_SESSION_STATE" in
                running) echo "restore" ;;
                *)       echo "wait_poll" ;;
            esac
            ;;
        build)
            # The build machine network-boots on purpose (flow 13.1): its
            # GRUB entry was chosen by a person, and it has no OS to fall
            # back to while serving as the capture machine. It waits for a
            # capture order instead of bouncing off the local disk.
            echo "build_console"
            ;;
        *)
            echo "local"
            ;;
    esac
}
