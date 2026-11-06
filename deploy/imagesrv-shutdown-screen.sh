#!/bin/sh
# imagesrv-shutdown-screen.sh -- the server going down, on the GUI (#1741, S7). POSIX sh.
#
# The DCUI is stopped first on a reboot or a power-off, and tty1 was then
# black, or systemd's "A stop job is running" in text, until the machine
# went. imagesrv-shutdown-screen@reboot|poweroff.service runs this at the
# start of that transaction: the failure screen's records (tone inf -- this
# is not a failure; role server) and the GUI on the framebuffer, display-only
# (--no-input), exec'd so it stays until systemd's last kill.
#
# Nothing waits for it and nothing claims it drew: the GUI's own stderr is in
# the journal. No library or binary: said in the journal, exit 1 (the unit is
# red), and the text console stays the screen -- as before this unit.
set -u

APP_DIR=${APP_DIR:-/opt/imagesrv}
STATE=${SHUTDOWN_SCREEN_STATE:-/run/imagesrv-shutdown.state}

case "${1:-}" in
    reboot|poweroff) ;;
    *) echo "usage: $0 reboot|poweroff" >&2; exit 2 ;;
esac
LIB="$APP_DIR/installer/lib/failgui.sh"
[ -f "$LIB" ] || { echo "imagesrv-shutdown-screen: $LIB is missing; the text console stays" >&2; exit 1; }
# shellcheck source=installer/lib/failgui.sh
. "$LIB"
# the update button rebuilds the first; the live installer copied the second (#2517)
BIN=$(failgui_bin "$APP_DIR/native-gui/imagesrv-station-gui" /usr/bin/imagesrv-station-gui) \
    || { echo "imagesrv-shutdown-screen: no GUI binary; the text console stays" >&2; exit 1; }
failgui_shutdown "$1" > "$STATE" \
    || { echo "imagesrv-shutdown-screen: cannot write $STATE; the text console stays" >&2; exit 1; }
exec "$BIN" --backend fbdev --no-input --theme light --state "$STATE"
