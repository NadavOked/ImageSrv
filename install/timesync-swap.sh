#!/usr/bin/env bash
# install/timesync-swap.sh — ‏systemd-timesyncd ← chrony בשרת **שמתעדכן** (#1773, #1814).
#
# הכרעת נדב (19/10, PR #1834, אפשרות ב'): ההחלפה היא שלב מפורש ומבוקר של זוג קבוע —
# ‏chrony נכנס, ‏systemd-timesyncd יוצאת — ולא היתר כללי להסרת חבילות. ‏#1803 נשאר עם
# ‏`--no-remove` לכל השאר.
#
#   install/timesync-swap.sh <repo-dir> <pool-dir> [<apt-option> ...]
#
# נקרא מ-install/security-upgrade.sh (שלב `packages` של tools/server-upgrade.sh), אחרי שהמאגר
# החתום של התג הורד, אומת בגיבוב ונפתח ל-<pool-dir>, ואחרי שהחבילות המפגרות עודכנו ממנו.
# ‏<apt-option>: אותן אפשרויות שרואות **רק** את המאגר הזה.
#
# הסדר:
#   1. בדיקות מקדימות — לא נוגעים בדבר: chrony **ו**-systemd-timesyncd במאגר (דרך החזרה),
#      ‏systemd-timesyncd בגרסה של systemd המותקן (אחרת אין לאן לחזור); ‏apt-get -s מראה
#      שההסרה היחידה היא systemd-timesyncd; מקור השעה נקרא: ההגדרה ב-DB, ואם אין —
#      ‏`NTP=` של timesyncd (שרת הזמן של הארגון עובר איתו); האם השעון מסונכרן עכשיו.
#   2. ‏apt-get install chrony (‏--no-install-recommends, ‏#1987) — מסיר את systemd-timesyncd.
#   3. ההגדרה של server/timesync.py (אותו קוד של השרת: makestep 1.0 -1, ‏#1814; המקור),
#      ‏restart, והמתנה ל-`chronyc tracking` מסונכרן (Leap status Normal ומקור `^*`).
#   4. ראיה חיובית: chrony מותקן ו-enabled, ‏systemd-timesyncd אינה מותקנת ואינה active,
#      והמקור שהגיע מ-timesyncd נשמר בהגדרה — אחרת עליית השרת הייתה כותבת "אוטומטי".
#
# כשל בשלבים 2–4 → חזרה **גלויה** ל-systemd-timesyncd מאותו מאגר (מסיר את chrony), ‏enable
# ו-active כראיה. שרת לא נשאר בלי שעון בשקט: גם כשל החזרה נאמר בשמו.
# chrony שלא הסתנכרן אינו כשל כשגם timesyncd לא היה מסונכרן לפני המעבר (רשת סגורה בלי
# שרת זמן) — זה המצב שהיה, והוא נאמר כאזהרה.
#
# ‏0 = הוחלף ונבדק (שורות `אזהרה: ` ב-stdout הן אזהרות). אחרת השורה האחרונה היא הסיבה:
#   1 בדיקה מקדימה נכשלה — לא שונה דבר · 2 ההחלפה נכשלה וחזרנו ל-systemd-timesyncd ·
#   3 ההחלפה נכשלה **וגם** החזרה — השרת בלי שעון מנוהל.
#
# משתני סביבה (בדיקות; בייצור ברירות המחדל): IMAGECTL_DATA_DIR, ‏IMAGECTL_APT,
# ‏IMAGECTL_DPKG_QUERY, ‏IMAGECTL_PYTHON, ‏IMAGECTL_SECURITY_TIMEOUT (שניות ל-apt, 1200),
# ‏IMAGECTL_CLOCK_WAIT (שניות להמתנה לסנכרון, 60), ‏IMAGECTL_CHRONY_ETC (במקום /etc/chrony).
set -uo pipefail

REPO=${1:?usage: timesync-swap.sh <repo-dir> <pool-dir> [apt-option ...]}
POOL=${2:?usage: timesync-swap.sh <repo-dir> <pool-dir> [apt-option ...]}
shift 2
OPTS=("$@")
DB="${IMAGECTL_DATA_DIR:-/var/lib/imagectl}/imagectl.db"
APT="${IMAGECTL_APT:-apt-get}"
DPKG_QUERY="${IMAGECTL_DPKG_QUERY:-dpkg-query}"
PYTHON="${IMAGECTL_PYTHON:-python3}"
APT_TIMEOUT="${IMAGECTL_SECURITY_TIMEOUT:-1200}"
WAIT="${IMAGECTL_CLOCK_WAIT:-60}"
export IMAGECTL_CHRONY_ETC="${IMAGECTL_CHRONY_ETC:-}"

fail() { echo "$2"; exit "$1"; }
last() { grep -v '^\s*$' <<<"$1" | tail -n 1; }

# מותקנת? ‏1 = לא מוכרת (תשובה: לא), ‏0 עם סטטוס אחר = לא; כל קוד אחר = הבדיקה נשברה (2).
installed() {
    local st rc
    st="$("$DPKG_QUERY" -W -f='${Status} ${Version}' "$1" 2>/dev/null)"; rc=$?
    [[ $rc -eq 1 ]] && return 1
    [[ $rc -eq 0 ]] || return 2
    [[ "$st" == "install ok installed "* ]]
}

# --- 1. בדיקות מקדימות: כל כשל כאן — לא שונה דבר ---------------------------------------
pre() { fail 1 "החלפת השעון ל-chrony לא התחילה — לא שונה דבר: $1"; }

idx="$(zcat "$POOL"/dists/trixie/*/binary-*/Packages.gz | awk '/^Package: /{p=$2} /^Version: /{print p, $2}')" \
    && [[ -n "$idx" ]] || pre "אינדקס המאגר לא נקרא"
grep -q '^chrony ' <<<"$idx" || pre "chrony אינו במאגר החתום"
sysd="$("$DPKG_QUERY" -W -f='${Status} ${Version}' systemd 2>&1)" && [[ "$sysd" == "install ok installed "?* ]] \
    || pre "גרסת systemd המותקנת לא נקראה: ${sysd:-אין פלט}"
sysd="${sysd#install ok installed }"
grep -qxF "systemd-timesyncd $sysd" <<<"$idx" \
    || pre "systemd-timesyncd $sysd (הגרסה של systemd המותקן) אינה במאגר החתום — אין דרך חזרה"

sim="$(timeout --kill-after=30 "$APT_TIMEOUT" env DEBIAN_FRONTEND=noninteractive "$APT" "${OPTS[@]}" \
        -s --no-install-recommends install chrony 2>&1)" \
    || pre "apt-get -s install chrony נכשל: $(last "$sim")"
removes="$(sed -n 's/^Remv \([^ ]*\).*/\1/p' <<<"$sim" | sort -u | tr '\n' ' ' | sed 's/ *$//')"
[[ "$removes" == systemd-timesyncd ]] \
    || pre "apt רוצה להסיר '${removes:-כלום}' — מותרת רק systemd-timesyncd"

# מקור השעה: ההגדרה ב-DB גוברת; אם ריקה — `NTP=` האפקטיבי של timesyncd (ראשי + drop-ins,
# בסדר של systemd; השמה ריקה מאפסת). ‏FallbackNTP אינו עובר: זה "אוטומטי".
cat_conf="$(systemd-analyze cat-config systemd/timesyncd.conf 2>&1)" \
    || pre "ההגדרה של systemd-timesyncd לא נקראה (systemd-analyze cat-config): $(last "$cat_conf")"
plan="$("$PYTHON" -I -c '
import os, sys
repo, db = sys.argv[1:3]
sys.path.insert(0, repo)
from server import timesync
from server.db import connect
if not os.path.exists(db):
    sys.exit(f"ה-DB {db} אינו קיים")
try:
    value = timesync.configured(connect(db))
except ValueError as exc:
    sys.exit(f"ההגדרה ntp_servers ב-DB: {exc}")
if value:
    print("db\t" + value); sys.exit(0)
ntp, section = [], None
for raw in sys.stdin.read().splitlines():
    line = raw.strip()
    if not line or line[0] in "#;":
        continue
    if line.startswith("["):
        section = line; continue
    if section == "[Time]" and line.startswith("NTP="):
        words = line[4:].split()
        ntp = ntp + words if words else []
try:
    value = timesync.validate(" ".join(ntp))
except ValueError as exc:
    sys.exit(f"NTP= של systemd-timesyncd: {exc}")
print(("timesyncd\t" if value else "auto\t") + value)
' "$REPO" "$DB" <<<"$cat_conf" 2>&1)" || pre "מקור השעה לא נקרא: $(last "$plan")"
SOURCE="${plan%%$'\t'*}"
VALUE="${plan#*$'\t'}"
[[ "$SOURCE" =~ ^(db|timesyncd|auto)$ ]] || pre "מקור השעה לא נקרא: $plan"

# היה מסונכרן לפני? "לא ידוע" נחשב "כן" — הדרישה המחמירה (עיקרון 5).
was="$(timedatectl show -p NTPSynchronized --value 2>/dev/null)" || was=""
[[ "$was" == no ]] || was=yes
echo "שעון: מחליף את systemd-timesyncd ב-chrony (מקור: ${VALUE:-אוטומטי, שרתי Debian}; מסונכרן לפני: $was)"

# --- כשל מכאן → חזרה גלויה ל-systemd-timesyncd ----------------------------------------
back() {
    local why=$1 out
    echo "שעון: $why — חוזר ל-systemd-timesyncd"
    out="$(timeout --kill-after=30 "$APT_TIMEOUT" env DEBIAN_FRONTEND=noninteractive "$APT" "${OPTS[@]}" \
            -y -q --no-install-recommends -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold \
            install "systemd-timesyncd=$sysd" 2>&1)"; local rc=$?
    printf '%s\n' "$out" | tail -n 10
    local broke=""
    if [[ $rc -ne 0 ]]; then broke="apt-get install systemd-timesyncd נכשל (קוד $rc): $(last "$out")"
    elif ! installed systemd-timesyncd; then broke="systemd-timesyncd אינה מותקנת אחרי apt-get"
    elif ! out="$(systemctl enable systemd-timesyncd 2>&1 && systemctl restart systemd-timesyncd 2>&1)"; then
        broke="systemd-timesyncd לא הופעלה: $(last "$out")"
    elif ! systemctl is-active --quiet systemd-timesyncd; then broke="systemd-timesyncd אינה active"
    # שני שעונים שמושכים את אותו שעון מערכת — ה-prerm של chrony אמור לעצור אותו; ראיה, לא הנחה.
    elif systemctl is-active --quiet chrony && { systemctl stop chrony; systemctl is-active --quiet chrony; }; then
        broke="chrony עדיין רץ ליד systemd-timesyncd"
    fi
    [[ -z "$broke" ]] || fail 3 "החלפת השעון ל-chrony נכשלה: $why — וגם החזרה ל-systemd-timesyncd נכשלה: $broke. השרת בלי שעון מנוהל"
    fail 2 "החלפת השעון ל-chrony נכשלה: $why — חזר ל-systemd-timesyncd"
}

# --- 2. ההחלפה ----------------------------------------------------------------------------
up="$(timeout --kill-after=30 "$APT_TIMEOUT" env DEBIAN_FRONTEND=noninteractive "$APT" "${OPTS[@]}" \
        -y -q --no-install-recommends -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold \
        install chrony 2>&1)"; rc=$?
printf '%s\n' "$up" | tail -n 10
[[ $rc -eq 0 ]] || back "apt-get install chrony נכשל (קוד $rc): $(last "$up")"
installed chrony || back "apt-get יצא 0, אבל chrony אינה מותקנת"
installed systemd-timesyncd; rc=$?
[[ $rc -eq 1 ]] || back "systemd-timesyncd עדיין מותקנת אחרי ההחלפה (או שלא נבדק: $rc)"

# --- 3. ההגדרה שלנו והמתנה לסנכרון --------------------------------------------------------
sync="$("$PYTHON" -I -c '
import os, sys, time
repo, value, wait = sys.argv[1], sys.argv[2], int(sys.argv[3])
sys.path.insert(0, repo)
from pathlib import Path
from server import timesync
etc = os.environ.get("IMAGECTL_CHRONY_ETC")
if etc:
    timesync.MAIN_CONF = Path(etc) / "chrony.conf"
    timesync.CONF_DIR = Path(etc) / "conf.d"
    timesync.CLIENT_CONF = timesync.CONF_DIR / "imagectl.conf"
    timesync.SERVER_CONF = timesync.CONF_DIR / "imagectl-server.conf"
hooks = timesync.default_hooks()
try:
    timesync.apply_server([], hooks)   # port 0 -- what the server writes at start-up
    st = timesync.apply(value, hooks)
except Exception as exc:
    sys.exit(f"ההגדרה של chrony לא הוחלה: {exc}")
deadline = time.monotonic() + wait
while st["synchronized"] is not True and time.monotonic() < deadline:
    time.sleep(1)
    st = timesync.status(hooks)
off = "-" if st["offset_ms"] is None else "%+.3f ms" % st["offset_ms"]
print("%s\t%s\t%s" % (st["synchronized"], st["server"] or "-", off))
' "$REPO" "$VALUE" "$WAIT" 2>&1)" || back "$(last "$sync")"
IFS=$'\t' read -r synced server offset <<<"$(last "$sync")"
if [[ "$synced" != True ]]; then
    [[ "$was" == no ]] || back "chrony לא הסתנכרן תוך $WAIT ש' (systemd-timesyncd היה מסונכרן)"
    echo "אזהרה: השעון אינו מסונכרן — chrony לא הסתנכרן תוך $WAIT ש', וגם systemd-timesyncd לא היה מסונכרן לפני המעבר (שרת זמן נגיש? הגדרות ← כללי ← מקור השעה)"
fi

# --- 4. ראיה חיובית ----------------------------------------------------------------------
[[ "$(systemctl is-enabled chrony 2>&1)" == enabled ]] || back "chrony אינו enabled — לא יעלה באתחול"
if systemctl is-active --quiet systemd-timesyncd; then back "systemd-timesyncd עדיין active"; fi
if [[ "$SOURCE" == timesyncd ]]; then
    saved="$("$PYTHON" -I -c '
import sys
sys.path.insert(0, sys.argv[1])
from server.db import connect, get_setting, journal, set_setting
from server import timesync
conn = connect(sys.argv[2])
set_setting(conn, timesync.SETTING, sys.argv[3])
journal(conn, "timesync_migrated", f"שרת הזמן של systemd-timesyncd עבר ל-chrony: {sys.argv[3]}", "server-upgrade")
print(get_setting(conn, timesync.SETTING))
' "$REPO" "$DB" "$VALUE" 2>&1)" && [[ "$(last "$saved")" == "$VALUE" ]] \
        || back "שרת הזמן ($VALUE) לא נשמר בהגדרות: $(last "$saved")"
fi
echo "שעון: chrony במקום systemd-timesyncd · מקור $server · מסונכרן: $synced · סטייה $offset"
