#!/usr/bin/env bash
# deploy/vmupdate-host.sh — מכין את המארח ל"עדכון אימג' ב-VM" (#1334):
# החבילות מ-deploy/vmupdate-packages.txt (המקור היחיד), ומשתמש המערכת
# imagesrv-vm (QEMU רץ תחתיו; חומת האש של ה-VM מגבילה את ה-uid שלו).
#
# נקרא משלושה מקומות: setup-boot-server.sh (התקנה), tools/server-upgrade.sh
# (העדכון מהכפתור — שרת שהותקן לפני #1334 מקבל את הכול כאן), והשרת עצמו
# בעלייה כשחסר משהו (‏#1372, ‏server/vm/vmhost.py — עדכון שרץ בסקריפט של התג
# הישן לא הגיע לכאן). אידמפוטנטי.
#
# ‏0 = הכול מותקן **ונבדק בחזרה** (dpkg-query לכל חבילה, ‏id למשתמש).
# אחרת: השורה האחרונה ב-stdout היא הסיבה, וקוד היציאה אומר איזה שלב:
#   2 רשימה ריקה/לא נקראה · 3 apt-get נכשל · 4 apt "הצליח" וחבילה אינה
#   מותקנת · 5 המשתמש לא נוצר · 6 מקורות apt של שרת מה-ISO לא הוכנו.
#   "apt יצא 0" אינו ראיה (עיקרון 5).
#
# ‏#1371/#1411/#1459 — שרת מה-ISO (יש sources.list.d/imagesrv-iso.list): המקור
# היחיד שלו הוא המאגר המקומי, **ואין מקור apt חי** (הכרעת נדב 15/10: עדכוני
# Debian מגיעים רק בגרסאות החתומות). ISO ישן אינו נושא את חבילות ה-VM כלל. לכן:
#   * המאגר המקומי מפרסם רק מה שיש בו (deploy/apt-repo-consistent.py) —
#     שרת מ-ISO של לפני #1371 פרסם כ-500 חבילות בלי הקבצים.
#   * שרת שכבר קיבל את מקור deb.debian.org מנקה אותו.
#   * dpkg-query קודם: חבילה שכבר מותקנת לא נוגעת ב-apt בכלל; apt (מהמאגר
#     המקומי) רק למה שחסר. חבילה שחסרה גם אחריו = יציאה 3/4 עם שמה, לא הורדה.
#
# בדיקות מחליפות את הכלים: IMAGESRV_APT, ‏IMAGESRV_DPKG_QUERY,
# ‏IMAGESRV_USERADD, ‏IMAGESRV_ID, ‏IMAGESRV_PYTHON; ואת הנתיבים:
# ‏IMAGESRV_APT_ETC (‏/etc/apt), ‏IMAGESRV_APT_REPO (‏/var/lib/imagesrv/apt-repo).
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIST="${IMAGESRV_VMUPDATE_PACKAGES:-$HERE/vmupdate-packages.txt}"
APT="${IMAGESRV_APT:-apt-get}"
DPKG_QUERY="${IMAGESRV_DPKG_QUERY:-dpkg-query}"
USERADD="${IMAGESRV_USERADD:-useradd}"
ID="${IMAGESRV_ID:-id}"
PYTHON="${IMAGESRV_PYTHON:-python3}"
APT_ETC="${IMAGESRV_APT_ETC:-/etc/apt}"
LOCAL_REPO="${IMAGESRV_APT_REPO:-/var/lib/imagesrv/apt-repo}"
VM_USER=imagesrv-vm

pkgs=()
if [[ -r "$LIST" ]]; then
    while IFS= read -r line || [[ -n "$line" ]]; do
        line="${line%%#*}"
        for word in $line; do pkgs+=("$word"); done
    done < "$LIST"
fi
if [[ ${#pkgs[@]} -eq 0 ]]; then
    echo "רשימת החבילות של עדכון ה-VM לא נקראה ($LIST)"
    exit 2
fi

if [[ -e "$APT_ETC/sources.list.d/imagesrv-iso.list" ]]; then
    if ! repo_out="$("$PYTHON" "$HERE/apt-repo-consistent.py" "$LOCAL_REPO" 2>&1)"; then
        printf '%s\n' "$repo_out"
        echo "המאגר המקומי $LOCAL_REPO לא נבדק: $(printf '%s\n' "$repo_out" | tail -n 1)"
        exit 6
    fi
    printf '%s\n' "$repo_out"
    # שרת שקיבל את המקור החי בגרסה קודמת (#1459) — מסירים, ומוודאים שנעלם.
    rm -f "$APT_ETC/sources.list.d/imagesrv-debian.list" "$APT_ETC/preferences.d/imagesrv-debian.pref"
    if [[ -e "$APT_ETC/sources.list.d/imagesrv-debian.list" || -e "$APT_ETC/preferences.d/imagesrv-debian.pref" ]]; then
        echo "מקור Debian חי לא הוסר מ-$APT_ETC"
        exit 6
    fi
    echo "מקורות: המאגר המקומי בלבד (אין מקור apt חי)"
fi

# מה שכבר מותקן לא נוגע ב-apt (#1459): כל עדכון הריץ update+install גם כשהכול שם.
need=()
for pkg in "${pkgs[@]}"; do
    status="$("$DPKG_QUERY" -W -f='${Status}' "$pkg" 2>/dev/null)" || status=""
    [[ "$status" == "install ok installed" ]] || need+=("$pkg")
done

if [[ ${#need[@]} -gt 0 ]]; then
    # רשימות עדכניות של המקורות שהמערכת כבר מכירה (המאגר המקומי) — לא מוסיפים מקור.
    upd_note=""
    upd_out="$(env DEBIAN_FRONTEND=noninteractive "$APT" update -qq -o Acquire::Retries=0 2>&1)" \
        || upd_note="apt-get update נכשל: $(printf '%s\n' "$upd_out" | grep -v '^\s*$' | tail -n 1)"
    [[ -z "$upd_note" ]] || echo "$upd_note"
    echo "מתקין (מהמאגר המקומי): ${need[*]}"
    if ! out="$(env DEBIAN_FRONTEND=noninteractive "$APT" install -y -qq --no-install-recommends "${need[@]}" 2>&1)"; then
        printf '%s\n' "$out" | tail -n 5
        echo "apt-get install נכשל: $(printf '%s\n' "$out" | grep -v '^\s*$' | tail -n 1)${upd_note:+ · $upd_note}"
        exit 3
    fi
fi

missing=()
for pkg in "${pkgs[@]}"; do
    status="$("$DPKG_QUERY" -W -f='${Status}' "$pkg" 2>/dev/null)" || status=""
    [[ "$status" == "install ok installed" ]] || missing+=("$pkg")
done
if [[ ${#missing[@]} -gt 0 ]]; then
    echo "apt-get יצא 0, אבל לא מותקנות: ${missing[*]}"
    exit 4
fi

if ! "$ID" -u "$VM_USER" >/dev/null 2>&1; then
    "$USERADD" --system --no-create-home --home-dir /nonexistent \
        --shell /usr/sbin/nologin "$VM_USER" \
        || { echo "יצירת משתמש המערכת $VM_USER נכשלה"; exit 5; }
fi
if ! "$ID" -u "$VM_USER" >/dev/null 2>&1; then
    echo "משתמש המערכת $VM_USER אינו קיים אחרי useradd"
    exit 5
fi
echo "עדכון ב-VM: ${#pkgs[@]} חבילות מותקנות, המשתמש $VM_USER קיים"
