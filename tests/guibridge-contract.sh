#!/bin/sh
# Local bridge contract tests: no server, GUI, disks, or root required.
# shellcheck disable=SC2034,SC2154  # SERVER/RECOVERY_*/GUI_ROLE are read by the
# sourced agent libs; token/name/desc are set by gui_records before gui_dispatch.
set -eu
ROOT=$(CDPATH='' cd -- "$(dirname "$0")/.." && pwd)
RUN_DIR=$(mktemp -d)
trap 'rm -rf "$RUN_DIR"' EXIT
GUI_DIR=$RUN_DIR/gui
mkdir "$GUI_DIR"
. "$ROOT/agent/lib/core/common.sh"
. "$ROOT/agent/lib/core/jsonq.sh"       # #1560: room_open reads the round id; gui_libs loads it too
. "$ROOT/agent/lib/gui/buildmenuitems.sh"
. "$ROOT/agent/lib/gui/buildmenu.sh"
. "$ROOT/agent/lib/gui/roomflow.sh"    # room_open / room_slots_json, as gui_libs loads them
. "$ROOT/agent/lib/gui/guibridge.sh"
CONSOLE_JAR=$GUI_DIR/console.jar
SERVER=http://invalid.example
RECOVERY_USER=operator RECOVERY_PASS=secret
sleep() { :; }
# Mock only the transport. Production HTTP-code and cookie checks run.
curl() {
    cat >/dev/null
    case "$scenario" in
        good) printf 'localhost\tFALSE\t/\tFALSE\t0\timagesrv_session\tticket\n' > "$CONSOLE_JAR"; printf 200 ;;
        no_cookie) : > "$CONSOLE_JAR"; printf 200 ;;
        empty_cookie) printf 'localhost\tFALSE\t/\tFALSE\t0\timagesrv_session\t\n' > "$CONSOLE_JAR"; printf 200 ;;
        refused) printf 401 ;;
        transport) printf 200; return 7 ;;
    esac
}
scenario=good
console_signin > "$RUN_DIR/out"
for scenario in no_cookie empty_cookie refused transport; do
    if console_signin > "$RUN_DIR/out"; then echo "FAIL accepted $scenario"; exit 1; fi
    [ ! -e "$CONSOLE_JAR" ]
done
echo 'PASS HTTP/cookie rejection, including transport failure with HTTP 200'
# Auth must return the role, then the saved theme (#1499) -- nothing else --
# and must reject role lookup failure. The mocked login answer carries no
# theme, so the second line is "auto" (with or without jq).
gui_role() { [ "$role_ok" = yes ] || return 1; GUI_ROLE='admin'; }
scenario='good' role_ok='yes'
printf 'operator\nsecret\n' | gui_auth > "$RUN_DIR/auth"
[ "$(sed -n 1p "$RUN_DIR/auth")" = admin ] || { echo 'FAIL auth line 1 is not the role'; cat "$RUN_DIR/auth"; exit 1; }
[ "$(sed -n 2p "$RUN_DIR/auth")" = auto ] || { echo 'FAIL auth line 2 is not the theme'; cat "$RUN_DIR/auth"; exit 1; }
[ "$(wc -l < "$RUN_DIR/auth" | tr -d ' ')" = 2 ] || { echo 'FAIL auth printed more than role and theme'; cat "$RUN_DIR/auth"; exit 1; }
role_ok=no
if printf 'operator\nsecret\n' | gui_auth > "$RUN_DIR/auth"; then exit 1; fi
[ ! -s "$RUN_DIR/auth" ] && [ ! -e "$CONSOLE_JAR" ]
echo 'PASS auth stdout and unresolved-role refusal'
# Production record parser: multiline values, no eval, duplicate/EOF refusal.
gui_dispatch() { printf '%s|%s|%s\n' "$token" "$name" "$desc" >> "$RUN_DIR/dispatch"; }
printf 'capture-start\nname=Office 2024\ndesc=$(touch BAD) = literal\n\n' | gui_records
[ "$(cat "$RUN_DIR/dispatch")" = 'capture-start|Office 2024|$(touch BAD) = literal' ]
for record in 'capture-start\nname=a\nname=b\n\n' 'capture-start\nname=a\n'; do
    if printf '%b' "$record" | gui_records; then echo 'FAIL malformed record accepted'; exit 1; fi
done
[ "$(wc -l < "$RUN_DIR/dispatch" | tr -d ' ')" = 1 ]
echo 'PASS record boundaries, duplicate/partial rejection, literal shell text'
# #715 direct-open: ticked drawers -> the same POST the console sends; the
# source disk comes from the server inventory; a malformed value posts nothing.
. "$ROOT/agent/lib/gui/guibridge.sh"   # the real gui_dispatch again (stubbed above)
gui_role() { GUI_ROLE='admin'; }
http_get() { printf '{"disks":[{"dev":"sdz","removable":true},{"dev":"sda","removable":false}]}'; }
jq() { printf 'sda\n'; }
console_post() { cp "$2" "$RUN_DIR/posted.json"; printf 200; }
MAC=b4:2e:99:07:1a:c4
printf 'direct-open\nslots=aa:bb:cc:00:00:21@1,2/aa:bb:cc:00:00:22@3\n\n' | gui_records
[ "$(cat "$RUN_DIR/posted.json")" = '{"source":{"kind":"build_disk","mac":"b4:2e:99:07:1a:c4","disk":"sda"},"target_slots":[{"mac":"aa:bb:cc:00:00:21","ports":[1,2]},{"mac":"aa:bb:cc:00:00:22","ports":[3]}]}' ]
rm -f "$RUN_DIR/posted.json"
for bad in 'direct-open\nslots=aa:bb:cc:00:00:21@1;rm -rf x\n\n' 'direct-open\nslots=\n\n' 'direct-open\nslots=junk\n\n'; do
    printf '%b' "$bad" | gui_records || true
    [ ! -e "$RUN_DIR/posted.json" ] || { echo 'FAIL malformed slots posted'; exit 1; }
done
echo 'PASS direct-open body and malformed-slots refusal'
# #1073 restore-start: the typed confirmation is the machine's REGISTERED
# name, read fresh from the server (never the one the screen showed); a
# wrong name, ERASE, or an empty name on an unnamed machine hands nothing off.
http_get() { printf '%s' "$state_json"; }
jq() { case "$state_json" in *BUILD-01*) printf 'BUILD-01\n' ;; *) return 1 ;; esac; }
state_json='{"name":"BUILD-01","allowed_images":[{"id":"img_1"}]}'
rm -f "$GUI_DIR/handoff"
# The live session goes along with the handoff (guiparent.sh re-checks it).
printf 'localhost\tFALSE\t/\tFALSE\t0\timagesrv_session\tticket\n' > "$CONSOLE_JAR"
# `|| true`: a hand-off is gui_dispatch returning 2, which set -e would read as a failure.
printf 'restore-start\nimage=img_1\nconfirm=BUILD-01\n\n' | gui_records || true
[ "$(cat "$GUI_DIR/handoff")" = 'restore-start
img_1' ] || { echo 'FAIL restore-start with the right name did not hand off'; exit 1; }
[ -s "$GUI_DIR/handoff.jar" ] || { echo 'FAIL restore-start handed off without the session'; exit 1; }
rm -f "$GUI_DIR/handoff" "$GUI_DIR/handoff.jar"
for bad in 'restore-start\nimage=img_1\nconfirm=ERASE\n\n' 'restore-start\nimage=img_1\nconfirm=build-01\n\n' 'restore-start\nimage=img_1\nconfirm=\n\n' 'restore-start\nimage=../x\nconfirm=BUILD-01\n\n'; do
    printf '%b' "$bad" | gui_records || true
    [ ! -e "$GUI_DIR/handoff" ] || { echo 'FAIL restore-start handed off on a wrong confirmation'; exit 1; }
done
state_json='{"name":null,"allowed_images":[{"id":"img_1"}]}'
printf 'restore-start\nimage=img_1\nconfirm=\n\n' | gui_records || true
[ ! -e "$GUI_DIR/handoff" ] || { echo 'FAIL restore-start handed off on an unnamed machine'; exit 1; }
echo 'PASS restore-start hands off only on the registered machine name (#1073)'
# GUI only (11/10): room-open passes the image and the count to room_open as
# arguments -- no prompt adapter, nothing read from stdin.
printf 'room-open\nimage=img_1\ntarget=2\nslots=aa:bb:cc:00:00:21@1\n\n' | gui_records
[ "$(cat "$RUN_DIR/posted.json")" = '{"image_id":"img_1","target_drives":2,"target_slots":[{"mac":"aa:bb:cc:00:00:21","ports":[1]}]}' ] \
    || { echo 'FAIL room-open body'; cat "$RUN_DIR/posted.json"; exit 1; }
echo 'PASS room-open posts image and count as arguments'
# GUI only (11/10): the class round after a pick was a text flow and was
# removed -- the pick is refused and hands nothing off to the agent.
rm -f "$GUI_DIR/handoff"
printf 'class-pick\ngroup=grp_1\n\n' | gui_records || true
[ ! -e "$GUI_DIR/handoff" ] || { echo 'FAIL class-pick handed off to a text flow'; exit 1; }
echo 'PASS class-pick is refused, no hand-off'
