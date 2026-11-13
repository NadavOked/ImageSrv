#!/bin/sh
# ImageSrv — שומר הפורט של אשף האתחול הראשון (#2490).
#
# POSIX sh. מדפיס ruleset של nftables ל-stdout; ‏tools/iso/firstboot.sh
# טוען אותו (nft -f -) וקורא אותו בחזרה **לפני** שהאשף עולה.
#
# למה: ‏`POST /api/wizard/apply` אינו דורש הזדהות, והקריאה הראשונה קובעת את
# סיסמת המנהל. ‏firstboot מבקש DHCP זמני לכל כרטיס עם קישור (גם להפצה),
# והאשף מאזין על 0.0.0.0. בלי השומר, כל מחשב באותו קטע רשת יכול היה
# להקדים את המפעיל.
#
# מה: טבלה אחת, ‏`inet imagesrv_wizard`, שנוגעת רק בפורט האשף:
#   lo                  — פתוח: הגואי על tty1 מדבר עם האשף דרך 127.0.0.1.
#   כרטיס ההתקנה        — פתוח: הכרטיס ש-/etc/imagesrv/installer-nic נוקב בו,
#                         לפי MAC (שם הכרטיס במתקין אינו בהכרח שמו כאן).
#   כל כרטיס אחר        — drop.
# ‏#2518: ‏dnsmasq (‏DNS, 53) ו-rpcbind (‏111) מאזינים בשלב הזה על 0.0.0.0, ואיש
# ברשת אינו צריך אותם לפני ההתקנה (האשף נגיש בכתובת, בלי DNS; אין NFS) —
# ‏drop על כל כרטיס חוץ מ-lo, גם על כרטיס ההתקנה. ‏lo נשאר פתוח לשרת עצמו.
# שאר התעבורה (תשובות DHCP, ה-apt המקומי) אינה עניינה: policy accept.
# אין `flush ruleset` — לא נוגעים בטבלאות של אחרים. חומת האש הקבועה
# (deploy/nftables-rules.sh) כן פותחת ב-flush, ולכן מחליפה את השומר בסוף
# ההתקנה; הוא אינו נשמר לדיסק, ו-firstboot טוען אותו שוב בכל אתחול עד אז.
#
# למה לא bind לכתובת הכרטיס: הגואי המקומי היה נשבר, ולינוקס מקבל חבילה
# לכתובת של כרטיס אחד גם כשנכנסה בכרטיס אחר (weak host). ‏iifname בודק
# את כרטיס הכניסה.
#
# שלושה מצבים, לא שניים (כמו ב-firstboot):
#   FOUND        → lo + הכרטיס.
#   UNDECIDABLE  → אין קובץ / לא configured / ה-MAC אינו על אף כרטיס:
#                  lo בלבד, והסיבה ל-stderr בשם. מצב לא ברור נסגר (עיקרון 1);
#                  האשף עדיין זמין ממסך השרת.
#   CHECK_ERROR  → הבדיקה עצמה לא רצה (sysfs, קובץ לא קריא, שם כרטיס שאינו
#                  בטוח): יציאה 2 בלי פלט. firstboot נכשל ולא מרים את האשף.
#
# שימוש: nftables-wizard.sh --nic-fact FILE [--sys-net DIR] [--port N]

set -eu

NIC_FACT=""
SYS_NET="${IMAGESRV_SYS_NET:-/sys/class/net}"
PORT="8081"

die() { printf 'nftables-wizard: check-error: %s\n' "$*" >&2; exit 2; }

while [ $# -gt 0 ]; do
    case "$1" in
        --nic-fact) NIC_FACT="${2:?}"; shift 2 ;;
        --sys-net)  SYS_NET="${2:?}"; shift 2 ;;
        --port)     PORT="${2:?}"; shift 2 ;;
        *)          die "unknown option: $1" ;;
    esac
done

[ -n "$NIC_FACT" ] || die "--nic-fact is required"
case "$PORT" in
    ""|*[!0-9]*) die "port must be a number (got: $PORT)" ;;
esac
[ -d "$SYS_NET" ] || die "$SYS_NET is not a directory; the installer NIC cannot be looked up"

lower() { printf '%s' "$1" | tr 'A-F' 'a-f'; }

NIC=""
REASON=""
if [ ! -e "$NIC_FACT" ]; then
    REASON="$NIC_FACT is missing"
else
    [ -r "$NIC_FACT" ] || die "$NIC_FACT is not readable"
    state=""
    mac=""
    while IFS= read -r line || [ -n "$line" ]; do
        case "$line" in
            state=*) [ -n "$state" ] || state="${line#state=}" ;;
            mac=*)   [ -n "$mac" ] || mac="${line#mac=}" ;;
        esac
    done < "$NIC_FACT"
    mac=$(lower "$mac")
    if [ "$state" != "configured" ] || [ -z "$mac" ]; then
        REASON="$NIC_FACT names no configured interface (state=${state:-none})"
    else
        for dev in "$SYS_NET"/*; do
            [ -e "$dev/address" ] || continue
            name="${dev##*/}"
            [ "$name" = "lo" ] && continue
            [ -r "$dev/address" ] || die "$dev/address is not readable"
            addr=$(cat "$dev/address") || die "could not read $dev/address"
            if [ "$(lower "$addr")" = "$mac" ]; then
                NIC="$name"
                break
            fi
        done
        [ -n "$NIC" ] || REASON="MAC $mac from $NIC_FACT is on no interface"
    fi
fi

# השם נכנס ל-nft כמחרוזת מצוטטת: תו שיכול לשבור את הציטוט — סירוב, לא ניקוי.
case "$NIC" in
    *[!A-Za-z0-9_.:-]*) die "interface name is not safe for nftables" ;;
esac

if [ -n "$NIC" ]; then
    NIC_RULE="		iifname \"${NIC}\" tcp dport ${PORT} accept"
    SUMMARY="loopback and ${NIC}"
else
    printf 'nftables-wizard: [network-nic-undecidable] %s; port %s stays on loopback only (the wizard is reachable from this server'"'"'s screen)\n' \
        "$REASON" "$PORT" >&2
    NIC_RULE="		# no installer NIC: loopback only"
    SUMMARY="loopback only"
fi

cat <<EOF
#!/usr/sbin/nft -f
# ImageSrv first-boot wizard guard (#2490), from deploy/nftables-wizard.sh.
# Not persistent; the permanent firewall (flush ruleset) replaces it.
# open on: ${SUMMARY}

table inet imagesrv_wizard
delete table inet imagesrv_wizard

table inet imagesrv_wizard {
	chain input {
		type filter hook input priority filter; policy accept;
		iifname "lo" tcp dport ${PORT} accept
${NIC_RULE}
		tcp dport ${PORT} counter drop
		iifname != "lo" udp dport 53 counter drop
		iifname != "lo" tcp dport 53 counter drop
		iifname != "lo" udp dport 111 counter drop
		iifname != "lo" tcp dport 111 counter drop
	}
}
EOF
