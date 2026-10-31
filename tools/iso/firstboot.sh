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
#   ב. האשף הזמני ב-HTTPS 8081. הוא מקבל את עובדות הכרטיס והתפקיד דרך
#      הקבצים שכתב d-i, והמפעיל מאשר או משנה אותן. רק בלחיצה על "החל"
#      הוא מריץ את setup-boot-server.sh; רשת ההפצה נשארת לקונסולה (#1088).
#
# כרטיס השרתים — **לא heuristic** (R62 §12): ‏late-command.sh כתב
# ל-/etc/imagesrv/installer-nic את הכרטיס ש-d-i עצמו הגדיר (שם + MAC).
# כאן מאמתים שה-MAC עדיין יושב על כרטיס קיים. שלושה מצבים, ולא שניים:
#   FOUND         → ‏--servers-if <הכרטיס שנושא את ה-MAC>
#   UNDECIDABLE   → אין קובץ / d-i לא הגדיר רשת / ה-MAC אינו על אף כרטיס.
#                   **לא מריצים את המתקין בלי הדגל** (הוא נעשה אינטראקטיבי).
#   CHECK_ERROR   → הבדיקה עצמה לא רצה (ip/sysfs). אינו UNDECIDABLE.
# ‏network-nic-undecidable / check-error אינם עוד סיבה לעצור: האשף מציג
# את הרשימה החיה ומחייב בחירה. הם נכתבים ביומן בשם, לא מתקפלים לבחירה.
#
# תפקיד (R60): ‏/etc/imagesrv/installer-role — standalone או secondary.
# secondary בלי כתובת ראשי הוא secondary-needs-primary באשף: השדה נשאר
# ריק ונדרש למלאו; אין כתובת מומצאת ואין חסימה לפני שהמפעיל רואה מסך.
#
# הכישלון גלוי: ‏`systemctl status imagesrv-firstboot` אדום, והיומן
# אומר באיזה שלב. אין Restart ואין `|| true`.

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

log() {
    printf 'imagesrv-firstboot: %s\n' "$*"
    if [[ "$TTY_PROGRESS" == 1 ]]; then
        printf 'ImageSrv: %s\n' "$*" > "$FIRSTBOOT_TTY"
    fi
}
fail() {
    # state=<מצב> ואז הסבר; הקובץ נקרא על ידי האשף/המסך, היומן על ידי אדם.
    local state="$1" message; shift
    message="$*"
    install -d "$ETC"
    printf 'state=%s\ntimestamp=%s\nmessage=%s\n' "$state" "$(date -u +%FT%TZ)" "$message" > "$STATUS"
    printf 'imagesrv-firstboot: [%s] %s\n' "$state" "$message" >&2
    printf '\nImageSrv: first boot failed [%s]\n%s\nPress Alt+F2 to log in on tty2.\n' \
        "$state" "$message" > "$FIRSTBOOT_TTY"
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
    # ‏#1230: הבנייה עצמה — הקרנל (שאינו cloud, #904), ה-epoch (#1125) וה-
    # ‏vmlinuz — ב-tools/boot-payload-build.sh, **אותו קוד** שכפתור העדכון
    # מריץ. ‏--write-flags רושם, רק אחרי שהכול נבנה, את שתי הבניות אל
    # ‏initrd.flags — בלי קרנל ובלי epoch, שנגזרים מחדש בכל עדכון.
    log "preparing the server - Stage A: building boot files (several minutes); details are in $BUILD_LOG"
    payload_out=$(bash "$SRC/tools/boot-payload-build.sh" --iso-default \
        --http-root "$HTTP_ROOT" --manifest "$ETC/iso-release.json" \
        --write-flags "$ETC/initrd.flags" --build-log "$BUILD_LOG" 2>&1) \
        || fail payload-failed "$(printf '%s\n' "$payload_out" | tail -n1) (see $BUILD_LOG)"
    printf '%s\n' "$payload_out"
    for f in initrd.img initrd.img.gui vmlinuz; do
        [[ -s "$HTTP_ROOT/$f" ]] || fail payload-failed "$HTTP_ROOT/$f is empty after the build"
    done
    touch "$STAGE_A_STAMP"
    log "Stage A complete: $(stat -c '%n=%s' "$HTTP_ROOT"/initrd.img "$HTTP_ROOT"/initrd.img.gui "$HTTP_ROOT"/vmlinuz | tr '\n' ' ')"
fi

# ---------------------------------------------------------------------------
# שלב ב' (#1190) — מהתשובות של המתקין החי: הכול כבר נענה על מסך השרת, אין
# אשף. ‏setup-boot-server מהדגלים, אימות המטען, חותמת, ואז ה-DCUI.
# ---------------------------------------------------------------------------
if [[ -f "$ETC/answers" ]]; then
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
    exit 0
fi

# ---------------------------------------------------------------------------
# שלב ב' (ללא תשובות — שרת בלי מסך) — קורא את עובדות ההתקנה, מדווח פערים,
# ומוסר אותן לאשף בדפדפן כברירות מחדל
# ---------------------------------------------------------------------------
log "preparing the server - Stage B: starting the installation wizard"
NIC_FILE="$ETC/installer-nic"
ROLE_FILE="$ETC/installer-role"
if [[ ! -f "$NIC_FILE" ]]; then
    log "[network-nic-undecidable] $NIC_FILE is missing; the wizard will require a live-interface selection"
else
    nic_state=$(sed -n 's/^state=//p' "$NIC_FILE" | head -n1)
    nic_mac=$(sed -n 's/^mac=//p' "$NIC_FILE" | head -n1 | tr 'A-F' 'a-f')
    [[ "$nic_state" == "configured" && -n "$nic_mac" ]] \
        || log "[network-nic-undecidable] installer-nic has no configured interface; the wizard will require a selection"
fi

ROLE=standalone; PRIMARY=""
if [[ -f "$ROLE_FILE" ]]; then
    ROLE=$(sed -n 's/^role=//p' "$ROLE_FILE" | head -n1)
    PRIMARY=$(sed -n 's/^primary=//p' "$ROLE_FILE" | head -n1)
fi
case "$ROLE" in
    standalone) ;;
    secondary) [[ -n "$PRIMARY" ]] || log "[secondary-needs-primary] the wizard will require a primary server address" ;;
    *) log "[check-error] unknown installer-role: $ROLE; the wizard will default to standalone" ;;
esac

# ה-repo המקומי שה-late_command השאיר (grub-pc-bin הוסר על ידי d-i בהתקנת
# UEFI; המתקין מתקין אותו מחדש — בלי אינטרנט זה עובד רק מכאן). ראיה
# חיובית לפני שהמתקין רץ: apt רואה מועמד.
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq || fail apt-repo-missing "apt-get update failed; /var/lib/imagesrv/apt-repo is missing or invalid"
cand=$(apt-cache policy grub-pc-bin | awk '/Candidate:/ {print $2}')
[[ -n "$cand" && "$cand" != "(none)" ]] || fail apt-repo-missing \
    "apt cannot find grub-pc-bin; the ISO repository is missing and offline setup cannot continue"

temporary_dhcp() {
    local nic="$1" carrier="0" address
    if ! ip link set dev "$nic" up; then
        log "nic $nic: link setup failed"
        return 0
    fi
    if [[ ! -r "/sys/class/net/$nic/carrier" ]]; then
        log "nic $nic: carrier check failed"
        return 0
    fi
    for _ in 1 2 3; do
        carrier=$(<"/sys/class/net/$nic/carrier")
        [[ "$carrier" == "1" ]] && break
        sleep 1
    done
    if [[ "$carrier" != "1" ]]; then
        log "nic $nic: no carrier"
        return 0
    fi
    if ! dhcpcd -4 -1 -t 20 "$nic" >>"$BUILD_LOG" 2>&1; then
        log "nic $nic: no dhcp offer"
        return 0
    fi
    if ! address=$(ip -o -4 address show dev "$nic" scope global | awk 'NR == 1 {print $4}'); then
        log "nic $nic: address check failed"
        return 0
    fi
    # 169.254/16 is dhcpcd's link-local fallback, not an offer (seen on the
    # deploy NIC, 21/09) -- naming it "dhcp" would send the operator there.
    if [[ -z "$address" || "$address" == 169.254.* ]]; then
        log "nic $nic: no dhcp offer"
    else
        log "nic $nic: $address (dhcp)"
    fi
}

dhcp_pids=()
for nic_path in /sys/class/net/*; do
    nic=${nic_path##*/}
    [[ "$nic" == "lo" ]] && continue
    temporary_dhcp "$nic" &
    dhcp_pids+=("$!")
done
for dhcp_pid in "${dhcp_pids[@]}"; do
    wait "$dhcp_pid" || fail check-error "A temporary DHCP worker failed"
done

install -m 0644 "$SRC/deploy/imagesrv-wizard.service" /etc/systemd/system/imagesrv-wizard.service
install -m 0644 "$SRC/deploy/imagesrv-wizard-rerun.service" /etc/systemd/system/imagesrv-wizard-rerun.service
install -m 0644 "$SRC/deploy/imagesrv-dcui.service" /etc/systemd/system/imagesrv-dcui.service
install -m 0644 "$SRC/deploy/imagesrv-installer-gui.service" /etc/systemd/system/imagesrv-installer-gui.service
install -d /etc/systemd/system/imagesrv-dcui.service.d
printf '[Service]\nWorkingDirectory=/opt/imagesrv-src\n' \
    > /etc/systemd/system/imagesrv-dcui.service.d/firstboot.conf
printf 'state=wizard-running\ntimestamp=%s\nrole_default=%s\n' "$(date -u +%FT%TZ)" "$ROLE" > "$STATUS"
systemctl daemon-reload || fail check-error "systemctl daemon-reload failed before starting the installer UI"
systemctl mask getty@tty1.service || fail check-error "Could not mask getty@tty1.service"
systemctl enable imagesrv-dcui.service || fail check-error "Could not enable imagesrv-dcui.service"
systemctl start imagesrv-wizard || fail wizard-failed "imagesrv-wizard did not start; see journalctl -u imagesrv-wizard"
systemctl is-active --quiet imagesrv-wizard || fail wizard-failed "imagesrv-wizard is not active after systemctl start"

has_keyboard=0
for event in /dev/input/event*; do
    [[ -e "$event" ]] || continue
    if udevadm info --query=property --name="$event" | grep -qx 'ID_INPUT_KEYBOARD=1'; then
        has_keyboard=1
        break
    fi
done
if [[ -e /dev/fb0 && "$has_keyboard" == 1 ]]; then
    TTY_PROGRESS=0
    systemctl start imagesrv-installer-gui.service \
        || fail check-error "Could not start imagesrv-installer-gui.service"
    systemctl is-active --quiet imagesrv-installer-gui.service \
        || fail check-error "imagesrv-installer-gui.service is not active after start"
    log "installer gui: started on tty1"
else
    TTY_PROGRESS=0
    systemctl restart imagesrv-dcui.service || fail check-error "Could not start imagesrv-dcui.service"
    systemctl is-active --quiet imagesrv-dcui.service || fail check-error "imagesrv-dcui.service is not active after restart"
    log "installer gui: no framebuffer/keyboard, DCUI only"
fi
rm -rf "$TMPDIR"
log "Stage B is ready: use tty1 or the HTTPS wizard on port 8081"
