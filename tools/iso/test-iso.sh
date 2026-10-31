#!/usr/bin/env bash
#
# ImageSrv — בדיקת ISO ההתקנה ב-QEMU + OVMF, בלי מסך (#1139; R25 §4, R57 §5).
#
#   sudo tools/iso/test-iso.sh --iso imagesrv-v0.48.0-amd64.iso [--disk-gb 40] [--timeout 5400]
#
# ⚠️ **טרם הורץ** (19/09): על שרת המעבדה אין qemu-system-x86/ovmf, והוא
# עצמו VM בלי KVM מקונן — התקנה מלאה תחת TCG היא שעות. הסקריפט קיים כדי
# שהריצה הראשונה תהיה על מכונה עם /dev/kvm, לא כדי לטעון שרץ.
#
# מה נבדק, בסדר:
#   1. אתחול UEFI עם **Secure Boot דלוק** (OVMF עם מפתחות Microsoft,
#      ‏OVMF_*_4M.ms.fd) מה-ISO, דיסק ריק → ההתקנה רצה עד סופה. עם
#      ‏-no-reboot יציאת QEMU = d-i ביקש reboot (ראיה חיובית: הדיסק
#      עכשיו מכיל מערכת, נבדק בשלב 2).
#   2. אתחול מהדיסק המותקן (בלי ה-ISO) → האתחול הראשון בונה ומריץ את
#      המתקין. הראיות (R25 §4), כולן דרך פורטים שמועברים מהמארח:
#        GET :8080/boot/vmlinuz   → 200, ‏Content-Length > 1MB
#        GET :8080/boot/initrd.img → 200, ‏Content-Length > 1MB
#        GET :8081/               → 200 (הקונסולה, HTTPS עצמי)
#      ‏`systemctl is-active imagesrv-server` ו-`is-enabled dnsmasq` =
#      ‏disabled **אינם** נבדקים כאן — אין דרך אל תוך ה-VM (בלי sshd
#      בכוונה, הכרעת נדב 19/09). זה נשאר לבדיקה במסך — נאמר בשמו.
#
# "לא ענה" אינו 404 ואינו 200: המתנה שנגמרה היא כישלון (עיקרון 5).

set -euo pipefail

ISO=""
DISK_GB=40
TIMEOUT=5400
MEM_MB=4096
HTTP_PORT=18080
CONSOLE_PORT=18081

usage() {
    cat <<'EOF'
ImageSrv — בדיקת ISO ההתקנה ב-QEMU/OVMF (Secure Boot).

  sudo tools/iso/test-iso.sh --iso FILE [--disk-gb N] [--timeout SEC] [--mem-mb N]

  --iso FILE       ה-ISO שנבנה ב-build-iso.sh
  --disk-gb N      גודל הדיסק הריק (ברירת מחדל 40)
  --timeout SEC    תקרה לכל שלב (ברירת מחדל 5400 = 90 דקות)
  --mem-mb N       זיכרון ל-VM (ברירת מחדל 4096)
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --iso)      ISO="${2:?}"; shift 2 ;;
        --disk-gb)  DISK_GB="${2:?}"; shift 2 ;;
        --timeout)  TIMEOUT="${2:?}"; shift 2 ;;
        --mem-mb)   MEM_MB="${2:?}"; shift 2 ;;
        -h|--help)  usage; exit 0 ;;
        *) printf 'unknown option: %s  (try --help)\n' "$1" >&2; exit 2 ;;
    esac
done

die() { printf 'test-iso: %s\n' "$*" >&2; exit 1; }
say() { printf 'test-iso: %s\n' "$*"; }

[[ -f "$ISO" ]] || { usage >&2; die "אין ISO: ${ISO:-(לא ניתן)}"; }
command -v qemu-system-x86_64 >/dev/null || die "חסר qemu-system-x86_64 (apt-get install qemu-system-x86)"
command -v qemu-img >/dev/null || die "חסר qemu-img (חבילת qemu-utils)"
command -v curl >/dev/null || die "חסר curl"
command -v socat >/dev/null || die "חסר socat (לבחירת ערך התפריט דרך ה-monitor)"

# ‏OVMF עם מפתחות Microsoft (חבילת ovmf, R57 §5א). בלי ".ms" אין Secure Boot
# אמיתי — ואז הבדיקה לא בודקת את מה שהיא מתיימרת לבדוק.
OVMF_CODE=/usr/share/OVMF/OVMF_CODE_4M.ms.fd
OVMF_VARS_TEMPLATE=/usr/share/OVMF/OVMF_VARS_4M.ms.fd
if [[ ! -f "$OVMF_CODE" ]]; then
    OVMF_CODE=/usr/share/OVMF/OVMF_CODE.secboot.fd
    OVMF_VARS_TEMPLATE=/usr/share/OVMF/OVMF_VARS.secboot.fd
fi
[[ -f "$OVMF_CODE" && -f "$OVMF_VARS_TEMPLATE" ]] || die "אין OVMF עם מפתחות Microsoft (apt-get install ovmf)"
[[ -c /dev/kvm ]] || die "אין /dev/kvm — התקנה מלאה תחת TCG היא שעות; הרץ על מארח עם KVM"

TMP=$(mktemp -d "${TMPDIR:-/tmp}/imagesrv-test-iso.XXXXXX")
trap 'rm -rf "$TMP"' EXIT
DISK="$TMP/disk.qcow2"
VARS="$TMP/OVMF_VARS.fd"
qemu-img create -q -f qcow2 "$DISK" "${DISK_GB}G"
cp "$OVMF_VARS_TEMPLATE" "$VARS"

qemu_common=(
    -enable-kvm -cpu host -m "$MEM_MB" -smp 2
    -machine "q35,smm=on"
    -global "driver=cfi.pflash01,property=secure,value=on"
    -drive "if=pflash,format=raw,readonly=on,file=$OVMF_CODE"
    -drive "if=pflash,format=raw,file=$VARS"
    -drive "file=$DISK,format=qcow2,if=virtio"
    -netdev "user,id=n0,hostfwd=tcp:127.0.0.1:$HTTP_PORT-:8080,hostfwd=tcp:127.0.0.1:$CONSOLE_PORT-:8081"
    -device "virtio-net-pci,netdev=n0"
    -display none -serial "file:$TMP/serial.log"
    -no-reboot
)

# --- שלב 1: התקנה מה-ISO ---------------------------------------------------------
# תפריט האתחול ממתין לאדם (בכוונה, ראה preseed.cfg). ב-QEMU בלי מקלדת
# בוחרים את הערך הראשון (ImageSrv) בלחיצת Enter דרך sendkey — ולכן כאן
# ‏monitor על unix socket (socat), ובשלב 2 בלי monitor.
say "שלב 1: התקנה מה-ISO (Secure Boot דלוק, דיסק ריק ${DISK_GB}GB), עד $TIMEOUT שניות"
qemu-system-x86_64 "${qemu_common[@]}" -monitor "unix:$TMP/mon,server,nowait" \
    -cdrom "$ISO" -boot order=d &
qpid=$!
sleep 25
printf 'sendkey ret\n' | socat - "UNIX-CONNECT:$TMP/mon" 2>/dev/null \
    || say "אזהרה: sendkey לא נשלח (socat?) — אם התפריט ממתין, ההתקנה לא תתחיל"
deadline=$(( $(date +%s) + TIMEOUT ))
while kill -0 "$qpid" 2>/dev/null; do
    (( $(date +%s) < deadline )) || { kill "$qpid" || true; die "שלב 1: ההתקנה לא הסתיימה תוך $TIMEOUT שניות (serial: $TMP/serial.log)"; }
    sleep 10
done
wait "$qpid" || die "שלב 1: QEMU יצא בשגיאה"
# ראיה חיובית שמשהו הותקן, לפני שמאתחלים ממנו: הדיסק אינו ריק.
used=$(qemu-img info --output=json "$DISK" | python3 -c 'import json,sys; print(json.load(sys.stdin)["actual-size"])')
(( used > 1024 * 1024 * 1024 )) || die "שלב 1: הדיסק תופס $used בייטים בלבד אחרי 'התקנה' — לא הותקן דבר"
say "שלב 1 הסתיים: הדיסק תופס $((used / 1024 / 1024)) MB"

# --- שלב 2: אתחול מהדיסק, האתחול הראשון ----------------------------------------
say "שלב 2: אתחול מהדיסק המותקן; ממתין לשרת על :$HTTP_PORT ולקונסולה על :$CONSOLE_PORT"
qemu-system-x86_64 "${qemu_common[@]}" -monitor none -boot order=c &
qpid=$!
probe() {  # url min_bytes → 0 רק על 200 עם גודל מוצהר ≥ min
    local url="$1" min="$2" hdr
    hdr=$(curl -sk -m 10 -o /dev/null -D - "$url" 2>/dev/null) || return 1
    printf '%s' "$hdr" | head -n1 | grep -q ' 200' || return 1
    local len; len=$(printf '%s' "$hdr" | tr -d '\r' | awk 'tolower($1)=="content-length:" {print $2}')
    [[ -n "$len" ]] && (( len >= min ))
}
deadline=$(( $(date +%s) + TIMEOUT ))
ok=0
while kill -0 "$qpid" 2>/dev/null && (( $(date +%s) < deadline )); do
    if probe "http://127.0.0.1:$HTTP_PORT/boot/vmlinuz" $((1024 * 1024)) \
       && probe "http://127.0.0.1:$HTTP_PORT/boot/initrd.img" $((1024 * 1024)) \
       && probe "https://127.0.0.1:$CONSOLE_PORT/" 0; then
        ok=1; break
    fi
    sleep 15
done
kill "$qpid" 2>/dev/null || true
wait "$qpid" 2>/dev/null || true
(( ok )) || die "שלב 2: לא התקבלו 200 (>1MB) על /boot/vmlinuz ו-/boot/initrd.img ו-200 על הקונסולה תוך $TIMEOUT שניות (serial: $TMP/serial.log)"
say "עבר: vmlinuz ו-initrd.img מוגשים (>1MB), הקונסולה עונה. לא נבדק כאן: is-enabled dnsmasq=disabled, is-active imagesrv-server — במסך."
