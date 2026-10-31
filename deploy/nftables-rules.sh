#!/bin/sh
# ImageSrv — מחולל /etc/nftables.conf לפי כרטיסי וילן (R20-F1 / #1074).
#
# POSIX sh. מדפיס קובץ שלם ל-stdout. המתקין בודק תחביר (nft -c -f)
# ואז מניח ב-/etc/nftables.conf ומפעיל את השירות.
#
# מודל האתר: שלושה וילנים. הכיתות (v2) אינן נפתחות כאן כלל.
#   הפצה  — DHCP/TFTP/סוכן/מולטיקאסט. WoL יוצא ב-output (לא נחסם).
#   שרתים — קונסולה HTTPS. 8443: במשני רק מכתובת הראשי; בראשי "אתה שם?" (#1310).
#   אין כלל TCP 22 בשום מצב (#1266).
#   כיתות — כלום.
#
# ‏#705 — מצב "לפי כרטיס" (‎--per-nic / ‎--service): המפעיל בחר בקונסולה
# אילו שירותים השרת נותן דרך כל כרטיס, והכללים נגזרים מזה בלבד — במקום
# שני התפקידים הקבועים למעלה. בלי הדגלים הפלט זהה בית-בבית לקודם.
#   console     — tcp קונסולה (המוניטור עובר בה, אין לו פורט משלו)
#   netboot     — udp 67 + udp 69 + tcp סוכן; proxy mode also opens udp 4011
#   multicast   — udp טווח udpcast
#   interserver — tcp 8443 (במשני: רק מכתובת הראשי)
#
# שימוש:
#   nftables-rules.sh --deploy-if <if> --servers-if <if> \
#     [--primary-ip <ip>] [--mcast-ports a-b] \
#     [--console-port 8081] [--agent-port 8080] \
#     [--interserver-port 8443]
#   nftables-rules.sh --per-nic [--service IF:svc,svc]... [options]

set -eu

DEPLOY_IF=""
SERVERS_IF=""
PRIMARY_IP=""
MCAST_PORTS="9000-9001"
CONSOLE_PORT="8081"
AGENT_PORT="8080"
INTERSERVER_PORT="8443"
PER_NIC=""
SERVICE_SPECS=""
PROXY_IFS=" "

die() { printf '%s\n' "nftables-rules: $*" >&2; exit 2; }

usage() {
    cat <<'EOF'
Usage: nftables-rules.sh --servers-if IF [--deploy-if IF] [options]

  --deploy-if IF          כרטיס וילן ההפצה; בלעדיו (#1088: טרם הוגדר מהקונסולה)
                          נכתבים כללי וילן השרתים בלבד, וההפצה סגורה
  --servers-if IF         כרטיס וילן השרתים (חובה)
  --primary-ip IP         כתובת הראשי — רק במשני; פותח 8443 ממנה בלבד
  --proxy-if IF           PXE proxy enabled on IF (repeatable); opens UDP 4011
                          only on --deploy-if or a per-NIC netboot interface
  --mcast-ports A-B       טווח UDP של udpcast (ברירת מחדל 9000-9001)
  --console-port N        קונסולה (ברירת מחדל 8081)
  --agent-port N          סוכן (ברירת מחדל 8080)
  --interserver-port N    בין-שרתים (ברירת מחדל 8443)
  --per-nic               (#705) כללים לפי כרטיס בלבד — בלי --deploy-if/--servers-if
  --service IF:LIST       (#705) מה הכרטיס נותן: console,monitor,multicast,netboot,interserver,ntp
                          (חוזר לכל כרטיס; מפעיל --per-nic)
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --deploy-if)        DEPLOY_IF="${2:?}"; shift 2 ;;
        --servers-if)       SERVERS_IF="${2:?}"; shift 2 ;;
        --primary-ip)       PRIMARY_IP="${2:?}"; shift 2 ;;
        --proxy-if)
            proxy_if="${2:?}"
            case "$proxy_if" in
                *[!A-Za-z0-9_.:-]*) die "proxy interface name is not safe for nftables" ;;
            esac
            PROXY_IFS="${PROXY_IFS}${proxy_if} "; shift 2 ;;
        --mcast-ports)      MCAST_PORTS="${2:?}"; shift 2 ;;
        --console-port)     CONSOLE_PORT="${2:?}"; shift 2 ;;
        --agent-port)       AGENT_PORT="${2:?}"; shift 2 ;;
        --interserver-port) INTERSERVER_PORT="${2:?}"; shift 2 ;;
        --per-nic)          PER_NIC=1; shift ;;
        --service)          SERVICE_SPECS="${SERVICE_SPECS}${2:?}
"; PER_NIC=1; shift 2 ;;
        -h|--help)          usage; exit 0 ;;
        *)                  die "unknown option: $1" ;;
    esac
done

[ -n "$SERVERS_IF" ] || [ -n "$PER_NIC" ] || die "--servers-if is required"

# שמות כרטיסים נכנסים ל-nft כמחרוזת מצוטטת. תו שיכול לשבור את הציטוט
# או את הדקדוק — סירוב, לא "ניקוי" בשקט.
case "$DEPLOY_IF$SERVERS_IF" in
    *[!A-Za-z0-9_.:-]*|*"'"*|*"\""*) die "interface name is not safe for nftables" ;;
esac
case "$MCAST_PORTS" in
    [0-9]*-[0-9]*) ;;
    *) die "--mcast-ports must be A-B (got: $MCAST_PORTS)" ;;
esac

for p in "$CONSOLE_PORT" "$AGENT_PORT" "$INTERSERVER_PORT"; do
    case "$p" in
        *[!0-9]*|"") die "port must be a number (got: $p)" ;;
    esac
done

if [ -n "$PRIMARY_IP" ]; then
    case "$PRIMARY_IP" in
        [0-9]*.[0-9]*.[0-9]*.[0-9]*) ;;
        *) die "--primary-ip must be an IPv4 address (got: $PRIMARY_IP)" ;;
    esac
fi

# ‏#705: הכללים לפי כרטיס. כל שם כרטיס נבדק כמו --deploy-if, וכל שירות
# חייב להיות מוכר — שירות שלא זוהה הוא סירוב, לא כלל שנשמט בשקט.
PER_NIC_RULES="		# --- לפי כרטיס (#705): מה שהשרת נותן דרך כל כרטיס, מהקונסולה ---"
add_rule() { PER_NIC_RULES="${PER_NIC_RULES}
		$1"; }
if [ -n "$PER_NIC" ]; then
    specs_left="$SERVICE_SPECS"
    any_spec=""
    while [ -n "$specs_left" ]; do
        spec="${specs_left%%
*}"
        specs_left="${specs_left#*
}"
        [ -n "$spec" ] || continue
        case "$spec" in
            *:*) ;;
            *) die "--service must be IF:LIST (got: $spec)" ;;
        esac
        nic="${spec%%:*}"
        list="${spec#*:}"
        case "$nic" in
            ""|*[!A-Za-z0-9_.:-]*) die "interface name is not safe for nftables" ;;
        esac
        case "$list" in
            *[!a-z,]*) die "--service list may hold only service names (got: $list)" ;;
        esac
        any_spec=1
        add_rule "# ${nic}: ${list:-אף שירות}"
        old_ifs="$IFS"; IFS=","
        for svc in $list; do
            case "$svc" in
                console)
                    add_rule "iifname \"${nic}\" tcp dport ${CONSOLE_PORT} accept" ;;
                monitor)
                    add_rule "# monitor: rides the console port (${CONSOLE_PORT}) -- no rule of its own" ;;
                netboot)
                    add_rule "iifname \"${nic}\" udp dport 67 accept"
                    add_rule "iifname \"${nic}\" udp dport 69 accept"
                    case "$PROXY_IFS" in
                        *" ${nic} "*) add_rule "iifname \"${nic}\" udp dport 4011 accept" ;;
                    esac
                    add_rule "iifname \"${nic}\" tcp dport ${AGENT_PORT} accept" ;;
                multicast)
                    add_rule "iifname \"${nic}\" udp dport ${MCAST_PORTS} accept"
                    # ‏#1972: שאילתות IGMP של querier אחר — בלעדיהן השרת לא שומע אותו.
                    add_rule "iifname \"${nic}\" ip protocol igmp accept" ;;
                interserver)
                    if [ -n "$PRIMARY_IP" ]; then
                        add_rule "iifname \"${nic}\" ip saddr ${PRIMARY_IP} tcp dport ${INTERSERVER_PORT} accept"
                    else
                        add_rule "iifname \"${nic}\" tcp dport ${INTERSERVER_PORT} accept"
                    fi ;;
                ntp)
                    add_rule "iifname \"${nic}\" udp dport 123 accept" ;;
                "") ;;
                *) IFS="$old_ifs"; die "unknown service: $svc" ;;
            esac
        done
        IFS="$old_ifs"
    done
    [ -n "$any_spec" ] || add_rule "# no interface offers any service -- everything is closed except loopback"
fi

# ‏#1088: כללי וילן ההפצה רק כשיש כרטיס הפצה. בלעדיו — הערה במקומם, וההפצה
# סגורה עד שהקונסולה תריץ את המחולל שוב עם --deploy-if (server/net/deploy_net.py).
if [ -n "$DEPLOY_IF" ]; then
    DEPLOY_RULES="		# --- וילן ההפצה (${DEPLOY_IF}) ---
		# DHCP: מקור ראשוני עשוי להיות 0.0.0.0 — לא דורשים כתובת קיימת.
		iifname \"${DEPLOY_IF}\" udp dport 67 accept
		# TFTP: קריאת shim/GRUB. העברת הנתונים היא related אם nf_conntrack_tftp טעון.
		iifname \"${DEPLOY_IF}\" udp dport 69 accept
		# סוכן (hello/boot/images) — HTTP על כרטיס ההפצה בלבד.
		iifname \"${DEPLOY_IF}\" tcp dport ${AGENT_PORT} accept
		# מולטיקאסט udpcast: portbase ו-portbase+1 (ברירת מחדל 9000-9001, server/rounds/sender.py).
		iifname \"${DEPLOY_IF}\" udp dport ${MCAST_PORTS} accept
		# IGMP (#1972): השרת הוא ה-querier, ושומע querier אחר לפי השאילתות שלו.
		iifname \"${DEPLOY_IF}\" ip protocol igmp accept
		# WoL (UDP 9) הוא יוצא — chain output ב-accept, אין כלל כניסה."
    case "$PROXY_IFS" in
        *" ${DEPLOY_IF} "*) DEPLOY_RULES="${DEPLOY_RULES}
		iifname \"${DEPLOY_IF}\" udp dport 4011 accept" ;;
    esac
else
    DEPLOY_RULES="		# --- וילן ההפצה: טרם הוגדר (#1088) ---
		# אין כרטיס הפצה — DHCP/TFTP/סוכן/מולטיקאסט סגורים. הקונסולה
		# כותבת את הקובץ הזה מחדש עם הכרטיס כשמודלק עליו DHCP."
fi

# משני: 8443 נכנס רק מכתובת הראשי (mTLS). ראשי (#1310): 8443 על וילן השרתים,
# ושם מאזין "אתה שם?" בלבד — הראשי אינו יודע מראש את כתובות המשניים, ו-FW
# האתר הוא שמגביל את המקור לאתרים המשניים.
if [ -n "$PRIMARY_IP" ]; then
    INTERSERVER_RULE="		# משני בלבד: 8443 נכנס רק מוילן השרתים ורק מכתובת הראשי.
		# הראשי יוזם; תשובות הן established. mTLS עדיין חובה מעל זה.
		iifname \"${SERVERS_IF}\" ip saddr ${PRIMARY_IP} tcp dport ${INTERSERVER_PORT} accept"
else
    INTERSERVER_RULE="		# ראשי: 8443 עונה רק ל\"אתה שם?\" של מתקין משני (#1310) — GET אחד,
		# בלי הזדהות ובלי פעולות. כל השאר ביוזמת הראשי.
		iifname \"${SERVERS_IF}\" tcp dport ${INTERSERVER_PORT} accept"
fi

if [ -z "$PER_NIC" ]; then
    MIDDLE="${DEPLOY_RULES}

		# --- וילן השרתים (${SERVERS_IF}) ---
		# קונסולה HTTPS — לא על כרטיס ההפצה.
		iifname \"${SERVERS_IF}\" tcp dport ${CONSOLE_PORT} accept
${INTERSERVER_RULE}"
else
    MIDDLE="${PER_NIC_RULES}"
fi

cat <<EOF
#!/usr/sbin/nft -f
# ImageSrv — נוצר על ידי deploy/nftables-rules.sh. אל תערוך ביד.
# מדיניות לפי וילן (R20 §4 / #1074): input drop, output accept, forward drop.
# השרת אינו נתב — forward סגור כדי שכרטיס הפצה לא יהפוך לגשר לכיתות.

flush ruleset

table inet imagesrv {
	chain input {
		type filter hook input priority filter; policy drop;

		# loopback — pairing מקומי, קונסולה על 127.0.0.1, health עצמי.
		iifname "lo" accept

		# תשובות לחיבורים שאנחנו יזמנו, וחיבורים related (TFTP helper אם נטען).
		ct state established,related accept
		ct state invalid drop

		# ICMP/ICMPv6: echo לאבחון, ND ל-IPv6, ו-PMTU כדי ש-TCP לא ייחסם.
		icmp type { echo-request, echo-reply, destination-unreachable, time-exceeded } accept
		icmpv6 type { echo-request, echo-reply, destination-unreachable, packet-too-big, time-exceeded, nd-router-solicit, nd-router-advert, nd-neighbor-solicit, nd-neighbor-advert } accept

${MIDDLE}

		# כל השאר: יומן מצומצם ואז drop (ה-policy). כיתות = v2, לא נפתח כאן.
		# ‏level info: לא warning — אחרת השורה מודפסת על הקונסולה מעל ה-DCUI (#1195)
		log prefix "imagesrv-drop " level info limit rate 5/minute
	}

	chain output {
		type filter hook output priority filter; policy accept;
	}

	chain forward {
		type filter hook forward priority filter; policy drop;
	}
}
EOF
