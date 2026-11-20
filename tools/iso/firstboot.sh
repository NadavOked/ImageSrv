#!/usr/bin/env bash
#
# ImageSrv — האתחול הראשון אחרי התקנה מה-ISO (#1139; R25 §2.4, R62, R60).
#
# ‏late-command.sh מתקין אותו כ-/usr/local/sbin/imagesrv-firstboot, ו-
# ‏imagesrv-firstboot.service מריץ אותו. מה שהמתקין (d-i) לא יכול לעשות
# מתוך ה-chroot — לבנות initrd מול הקרנל המותקן ולהרים שירותים — קורה
# כאן, על המערכת האמיתית.
#
# שני שלבים, בסדר הזה בכוונה:
#   א. ‏initrd.img + initrd.img.gui + vmlinuz אל /srv/imagesrv/boot —
#      מתוך /opt/imagesrv-src, דרך tools/boot-payload-build.sh (#1230), עם
#      --skip-apt (החבילות כבר מותקנות מה-ISO; packages.txt הוא האיחוד,
#      ו-tests/test_iso_build.py שומר). הבניות נרשמות ב-initrd.flags.
#      גם שרת **משני** בונה בעצמו (R60): הסיבה לא לבנות — קרנל cloud —
#      אינה תקפה כשה-ISO מביא linux-image-amd64.
#   ב. הגדרת השרת מהתשובות שהמתקין החי כתב (/etc/imagesrv/answers):
#      firstboot-answers.sh מריץ את setup-boot-server.sh, מאמת את המטען,
#      חותם, ומדליק את ה-DCUI. אין אשף, והכרטיס נקבע מהתשובות — לא heuristic.
#
# #2763 (נדב, 15/11/2026): שרת מותקן רק מה-ISO או מה-USB. מערכת שאין בה
# /etc/imagesrv/answers לא הותקנה ככה, ולכן אין לה מסלול: לא אשף בדפדפן ולא
# מתקין. הבדיקה היא הראשונה, לפני שלב א' (דקות של בנייה בשביל שרת שלא ישלים),
# והמסך מציג הודעה בלבד — answers-missing, "יש להתקין מחדש מה-ISO או מה-USB".
#
# הכישלון גלוי: ‏`systemctl status imagesrv-firstboot` אדום, והיומן
# אומר באיזה שלב. אין Restart ואין `|| true`. על tty1 הוא נכתב כשורה, ו-#1741
# (‏S3) מצייר אותו גם בגואי לתצוגה בלבד מעליה — בלי framebuffer, בלי בינארי,
# או בלי פריים מוכח (ה-beat), השורה היא המסך.

set -euo pipefail

STAMP_DIR=/var/lib/imagesrv
STAMP="$STAMP_DIR/.firstboot-done"
STAGE_A_STAMP="$STAMP_DIR/.firstboot-payload-done"
ETC=/etc/imagesrv
STATUS="$ETC/firstboot.status"
SRC=/opt/imagesrv-src
HTTP_ROOT=/srv/imagesrv/boot
BUILD_LOG=/var/log/imagesrv-firstboot-build.log
FIRSTBOOT_TTY=/dev/tty1
TTY_PROGRESS=1
# Owner 17/10: no screen that is not the GUI. When the boot screen is up
# (server/system/bootsplash.py wrote its marker), progress stays in the journal and
# the picture stays on tty1; a failure (fail) is still printed there.
SPLASH_MARKER=${IMAGESRV_SPLASH_MARKER:-/run/imagesrv-boot-splash.on}
if [[ -e "$SPLASH_MARKER" ]]; then TTY_PROGRESS=0; fi
# #1741 (S3): a failure is also drawn on the GUI, display-only (installer/lib/failgui.sh).
# The binary is the live installer's copy (#2517, bootstrap.sh), or the one stage A built.
FAILGUI_LIB="$SRC/installer/lib/failgui.sh"
FAILGUI_WAIT_S=${FAILGUI_WAIT_S:-20}
FB_DEV=/dev/fb0
GUI_BINS="/usr/bin/imagesrv-station-gui $SRC/native-gui/imagesrv-station-gui"
FAIL_GUI_STATE=/run/imagesrv-firstboot-failure.state
FAIL_GUI_BEAT=/run/imagesrv-firstboot-failure.beat

log() {
    printf 'imagesrv-firstboot: %s\n' "$*"
    if [[ "$TTY_PROGRESS" == 1 ]]; then
        printf 'ImageSrv: %s\n' "$*" > "$FIRSTBOOT_TTY"
    fi
}
tty1_taken() {
    # 0 = the DCUI or the installer GUI holds tty1, or systemctl gave no
    # answer: never two GUIs on one framebuffer. 1 = both are down.
    local unit state
    for unit in imagesrv-dcui.service imagesrv-installer-gui.service; do
        state=$(systemctl is-active "$unit") || [[ -n "$state" ]] || return 0
        case "$state" in inactive|failed) ;; *) return 0 ;; esac
    done
    return 1
}
gui_drew() {
    # $1 = beat file, $2 = the unit that runs the GUI. 0 = it presented a
    # frame (main.c gui_heartbeat) while the unit ran -- not is-active alone (#2517).
    [[ -f "$FAILGUI_LIB" ]] || { printf 'imagesrv-firstboot: %s is missing\n' "$FAILGUI_LIB" >&2; return 1; }
    # shellcheck source=installer/lib/failgui.sh
    . "$FAILGUI_LIB" || return 1
    failgui_beat "$1" "$FAILGUI_WAIT_S" systemctl is-active --quiet "$2"
}
fail_gui() {
    # #1741 (S3): $1 state, $2 message on the GUI, display-only. 0 = a frame
    # is on tty1; 1 = not shown, said on stderr -- the text line stays the screen.
    local bin
    [[ -e "$FB_DEV" ]] || { printf 'imagesrv-firstboot: failure screen: no framebuffer (%s), text only\n' "$FB_DEV" >&2; return 1; }
    [[ -f "$FAILGUI_LIB" ]] || { printf 'imagesrv-firstboot: failure screen: %s is missing, text only\n' "$FAILGUI_LIB" >&2; return 1; }
    # shellcheck source=installer/lib/failgui.sh
    . "$FAILGUI_LIB" || return 1
    # shellcheck disable=SC2086 # GUI_BINS is a list of paths
    bin=$(failgui_bin $GUI_BINS) || { printf 'imagesrv-firstboot: failure screen: no GUI binary (%s), text only\n' "$GUI_BINS" >&2; return 1; }
    if tty1_taken; then printf 'imagesrv-firstboot: failure screen: tty1 belongs to the DCUI or the installer GUI, text only\n' >&2; return 1; fi
    failgui_firstboot "$1" "$2" > "$FAIL_GUI_STATE" || return 1
    rm -f "$FAIL_GUI_BEAT"
    # its own unit: firstboot's cgroup is stopped when this oneshot exits
    systemd-run --unit=imagesrv-firstboot-failure --collect --quiet --setenv=IMAGESRV_GUI_BEAT="$FAIL_GUI_BEAT" \
        -- "$bin" --backend fbdev --no-input --theme light --state "$FAIL_GUI_STATE" \
        || { printf 'imagesrv-firstboot: failure screen: systemd-run failed, text only\n' >&2; return 1; }
    gui_drew "$FAIL_GUI_BEAT" imagesrv-firstboot-failure.service \
        || { printf 'imagesrv-firstboot: failure screen: the GUI drew no frame (journalctl -u imagesrv-firstboot-failure), text only\n' >&2; return 1; }
}
fail() {
    # state=<מצב> ואז הסבר; הקובץ נקרא על ידי האשף/המסך, היומן על ידי אדם.
    local state="$1" message; shift
    message="$*"
    install -d "$ETC"
    printf 'state=%s\ntimestamp=%s\nmessage=%s\n' "$state" "$(date -u +%FT%TZ)" "$message" > "$STATUS"
    printf 'imagesrv-firstboot: [%s] %s\n' "$state" "$message" >&2
    # #2634: outside the recovery entry getty@ runs only with this mark (deploy/getty-recovery-only.conf)
    : > /run/imagesrv-login-open || printf 'imagesrv-firstboot: could not open the tty2 login (/run)\n' >&2
    printf '\nImageSrv: first boot failed [%s]\n%s\nPress Alt+F2 to log in on tty2.\n' \
        "$state" "$message" > "$FIRSTBOOT_TTY"
    # #1741 (S3): the same failure on the GUI, over that line; without it the line is the screen
    if fail_gui "$state" "$message"; then printf 'imagesrv-firstboot: failure screen: on the GUI (tty1)\n'; fi
    exit 1
}
unexpected_failure() {
    local line="$1" rc="$2"
    trap - ERR
    fail check-error "Unexpected failure at line $line (exit $rc)"
}
trap 'unexpected_failure "$LINENO" "$?"' ERR

[[ -f "$STAMP" ]] && { log "Already completed ($STAMP exists); nothing to do"; exit 0; }
[[ $EUID -eq 0 ]] || fail check-error "Root privileges are required"
[[ -f "$SRC/server/main.py" ]] || fail check-error "Source is missing from $SRC; late-command.sh did not copy it"
install -d "$STAMP_DIR" "$ETC"
# #2763: positive evidence that the ISO/USB installer ran -- its answers file. Not "no failure seen":
# a system without it has no install path here, and the screen says so (failgui.sh, answers-missing).
[[ -f "$ETC/answers" ]] || fail answers-missing "$ETC/answers is missing: this system was not installed by the ISO or USB installer; reinstall from the ISO or the USB"
touch "$BUILD_LOG"
chmod 0600 "$BUILD_LOG"

# ‏/tmp בדביאן 13 הוא tmpfs; עץ ה-initramfs לפני הדחיסה הוא מאות MB.
export TMPDIR=/var/tmp/imagesrv-firstboot
install -d "$TMPDIR"

# ---------------------------------------------------------------------------
# שלב א' — הקרנל וה-initrd
# ---------------------------------------------------------------------------
if [[ -f "$STAGE_A_STAMP" ]]; then
    log "Stage A already completed; continuing to stage B"
else
    # Materialize the same flags that the normal builder records.  The verifier
    # normalizes output paths before comparing them with the ISO manifest.
    cat > "$ETC/initrd.flags.new" <<EOF
# ImageSrv initrd builds: one build_initramfs.sh run per line.
--skip-apt --output $HTTP_ROOT/initrd.img
--skip-apt --with-gui --output $HTTP_ROOT/initrd.img.gui
EOF
    mv -f "$ETC/initrd.flags.new" "$ETC/initrd.flags"
    KVER=$(find "${IMAGESRV_MODULES_DIR:-/lib/modules}" -mindepth 1 -maxdepth 1 -printf '%f\n' | awk '$0 !~ /cloud/' | sort -V | tail -n1) \
        || fail payload-failed "Could not inspect installed kernels"
    [[ -n "$KVER" ]] || fail payload-failed "No installed non-cloud kernel was found"
    decision_out=$(python3 "$SRC/deploy/boot-payload.py" decide --source "$SRC" \
        --kernel "$KVER" --flags "$ETC/initrd.flags" --payload-dir "$HTTP_ROOT" \
        --manifest /var/lib/imagesrv/iso-boot-payload/boot-payload.json \
        --prebuilt-dir /var/lib/imagesrv/iso-boot-payload 2>&1) && decision_rc=0 || decision_rc=$?
    if [[ "$decision_rc" == 0 ]]; then
        printf '%s\n' "$decision_out"
        case "$decision_out" in
            prebuilt$'\t'*) log "boot payload: prebuilt from ISO, verified" ;;
            current$'\t'*) log "boot payload: inputs unchanged, current payload verified" ;;
            *) fail payload-failed "Boot payload decision returned an unknown success: $decision_out" ;;
        esac
        touch "$STAGE_A_STAMP"
        log "Stage A complete: $(stat -c '%n=%s' "$HTTP_ROOT"/initrd.img "$HTTP_ROOT"/initrd.img.gui "$HTTP_ROOT"/vmlinuz | tr '\n' ' ')"
    elif [[ "$decision_rc" == 10 ]]; then
        log "boot payload: prebuilt from ISO not used: ${decision_out#*$'\t'}"
    # ‏#1230: הבנייה עצמה — הקרנל (שאינו cloud, #904), ה-epoch (#1125) וה-
    # ‏vmlinuz — ב-tools/boot-payload-build.sh, **אותו קוד** שכפתור העדכון
    # מריץ. ‏--write-flags רושם, רק אחרי שהכול נבנה, את שתי הבניות אל
    # ‏initrd.flags — בלי קרנל ובלי epoch, שנגזרים מחדש בכל עדכון.
    log "preparing the server - Stage A: building boot files (several minutes); details are in $BUILD_LOG"
    payload_out=$(bash "$SRC/tools/boot-payload-build.sh" --iso-default \
        --http-root "$HTTP_ROOT" --manifest "$ETC/iso-release.json" \
        --write-flags "$ETC/initrd.flags" --build-log "$BUILD_LOG" 2>&1) \
        || { rm -f "$ETC/initrd.flags"; fail payload-failed "$(printf '%s\n' "$payload_out" | tail -n1) (see $BUILD_LOG)"; }
    printf '%s\n' "$payload_out"
    for f in initrd.img initrd.img.gui vmlinuz; do
        [[ -s "$HTTP_ROOT/$f" ]] || fail payload-failed "$HTTP_ROOT/$f is empty after the build"
    done
    touch "$STAGE_A_STAMP"
    log "Stage A complete: $(stat -c '%n=%s' "$HTTP_ROOT"/initrd.img "$HTTP_ROOT"/initrd.img.gui "$HTTP_ROOT"/vmlinuz | tr '\n' ' ')"
    else
        fail payload-failed "Boot payload decision failed: $decision_out"
    fi
fi

# #2533: chunkcast on the server itself -- the sender of the experimental chunk
# engine (behind a switch that is off). On a prebuilt payload too: the initrd
# carries the receiver, the server needs the sender. A failure here does not
# stop the installation; it is said, and with the switch on a wave without a
# sender fails by name.
# The reason is the script's own last line, and it is said here -- not swallowed: whoever
# reads this log later is told why the switch in the console refuses to turn on.
if ! tools_said=$(bash "$SRC/tools/server-tools-build.sh" --source "$SRC" 2>&1); then
    log "chunkcast (the experimental chunk engine's sender) was NOT built on this server: ${tools_said##*$'\n'}"
    log "the 'stream engine' switch will refuse to turn on until it is built (tools/server-tools-build.sh)"
fi

# ---------------------------------------------------------------------------
# שלב ב' (#1190) — מהתשובות של המתקין החי: הכול כבר נענה על מסך השרת, אין
# אשף. ‏setup-boot-server מהדגלים, אימות המטען, חותמת, ואז ה-DCUI.
# ---------------------------------------------------------------------------
log "preparing the server - Stage B: applying installer answers and starting the DCUI"
[[ -f "$SRC/tools/iso/firstboot-answers.sh" ]] || fail check-error "$ETC/answers exists but $SRC/tools/iso/firstboot-answers.sh is missing"
# shellcheck source=tools/iso/firstboot-answers.sh
. "$SRC/tools/iso/firstboot-answers.sh"
firstboot_from_answers
install -m 0644 "$SRC/deploy/imagesrv-dcui.service" /etc/systemd/system/imagesrv-dcui.service
rm -f /etc/systemd/system/imagesrv-dcui.service.d/firstboot.conf
systemctl daemon-reload || fail check-error "systemctl daemon-reload failed before starting the DCUI"
systemctl mask getty@tty1.service || fail check-error "Could not mask getty@tty1.service"
systemctl enable imagesrv-dcui.service || fail check-error "Could not enable imagesrv-dcui.service"
TTY_PROGRESS=0
systemctl restart imagesrv-dcui.service || fail check-error "Could not start imagesrv-dcui.service"
rm -rf "$TMPDIR"
log "Stage B complete from the installer's answers: the DCUI is on tty1, the console on 8081"
