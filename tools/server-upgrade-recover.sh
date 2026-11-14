#!/usr/bin/env bash
# #1893: an upgrade whose process died with the machine (power cut, VM off) is
# rolled back on the next boot, before the server starts -- never resumed.
#
# Why back and not on: a resume would run an update that stopped half-written
# without the signature check of #1277 having run on what is on disk now; going
# back runs only what was already installed and verified. The marker
# (server-upgrade.sh write_recovery_marker, fsync + rename) says how far the run
# got, and only what THIS run did is undone:
#   fetch/verify   nothing changed on disk: the tree is still PREV.
#   install..      the tree is checked out back to PREV and its units reinstalled;
#   packages..     dpkg --configure -a first (a cut apt leaves packages unconfigured;
#                  the security updates themselves stay -- they are not undone);
#   payload_saved  the boot payload saved by this run is put back;
#   db_snap_ver    the DB snapshot of this run is put back ONLY if the DB was
#                  migrated past it (the new server started) -- the same rule as
#                  restore_db_if_migrated; a snapshot of an older run never is.
# The outcome is the console's update status (failed, reason rollback) and a
# journal line. A recovery that fails says so there too and keeps the marker:
# "could not roll back" is not "rolled back" (principle 5).
set -euo pipefail

DATA_DIR="${IMAGESRV_DATA_DIR:-/var/lib/imagesrv}"
STATE="${IMAGESRV_UPGRADE_STATE:-$DATA_DIR/server-upgrade.state}"
UNIT_DIR="${IMAGESRV_UNIT_DIR:-/etc/systemd/system}"
# #2795: this script's own copy outside the tree (the unit runs it, not the tree
# a cut checkout leaves half-written); refreshed with the units below.
RECOVER_COPY="${IMAGESRV_RECOVER_COPY:-/usr/local/sbin/imagesrv-upgrade-recover}"
# #2795: the checkout back writes its index and HEAD to disk before renaming them.
export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.fsync GIT_CONFIG_VALUE_0=objects,index,reference
DB="$DATA_DIR/imagesrv.db"
[[ -f "$STATE" ]] || exit 0

# C-SYS-24: logger is a second copy; the echo reaches the journal through the unit either way.
say() { logger -t imagesrv-upgrade-recover "$*" 2>/dev/null || :; echo "upgrade-recover: $*"; }

IFS=$'\t' read -r REPO PREV TAG STAGE HTTP_ROOT DB_SNAP_VER PAYLOAD_SAVED < <(python3 - "$STATE" <<'PYEOF'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
keys = ("repo", "previous", "tag", "stage", "http_root", "db_snap_ver")
print("\t".join([str(d.get(k) or "-") for k in keys] + ["1" if d.get("payload_saved") is True else "0"]))
PYEOF
) || { say "the interrupted-upgrade marker $STATE could not be read -- nothing rolled back"; exit 1; }

# The console's status and the journal, with the code that is on disk now.
report() {   # report <error> <reason>
    python3 - "$REPO" "$DB" "$TAG" "$PREV" "$STAGE" "$1" "$2" <<'PYEOF'
import sys
repo, db, tag, previous, stage, error, reason = sys.argv[1:]
sys.path.insert(0, repo)
from server import update
from server.db import connect, journal, now_iso
conn = connect(db)
update._set_status(conn, {"state": "failed", "tag": tag, "stage": stage, "from": previous,
                          "reason": reason, "error": error, "detail": "עדכון שנקטע",
                          "finished_at": now_iso()})
journal(conn, "update_apply_failed", f"{tag}: {reason} — {error}", "upgrade-recovery")
PYEOF
}
failed() {
    local why="העדכון ל-$TAG נקטע בשלב $STAGE, והחזרה האוטומטית ל-$PREV נכשלה ($1) — נדרשת התערבות; הסמן נשאר ב-$STATE"
    say "$why"
    report "$why" install || say "the update status could not be written either"
    exit 1
}
trap 'failed "שורה $LINENO: $BASH_COMMAND"' ERR
note=""

[[ "$REPO" != - && -d "$REPO/.git" && "$PREV" != - && "$TAG" != - && "$STAGE" != - ]] \
    || failed "סמן פגום: repo=$REPO previous=$PREV tag=$TAG stage=$STAGE"
say "upgrade to $TAG was cut at '$STAGE' -- rolling back to $PREV"
cd "$REPO"

# #2026: the units the interrupted version brings (deploy/*.service and
# *.timer of $TAG -- #1957 added timers). Read from git, not from the marker: a
# marker written by an older server-upgrade.sh has no list, and this is the
# script that recovers from it. "Could not read" is not "nothing was added".
tag_units() {
    local listed
    listed="$(git ls-tree --name-only "$TAG" -- deploy/)" || return 1
    [[ -n "$listed" ]] || return 1
    sed -n 's#^deploy/\([^/]*\.\(service\|timer\)\)$#\1#p' <<<"$listed"
}
# #2795: `install` onto a unit unlinks it and creates a new file -- empty after a
# power cut until its data reaches the disk, and an empty unit is masked (this
# one too). Beside it, fsync, rename over, fsync the directory: old or new, never
# empty. The same function as server-upgrade.sh (this script runs from a copy
# outside the tree, so it cannot source one).
install_atomic() {   # install_atomic <mode> <src> <dest>
    local tmp="${3%/*}/.${3##*/}.tmp"
    install -m "$1" "$2" "$tmp" && sync "$tmp" && mv -f "$tmp" "$3" && sync "${3%/*}" \
        || { rm -f "$tmp"; return 1; }
}
# The units of the tree on disk now ($PREV) -- *.service and *.timer, so a timer
# the interrupted version changed goes back too. No timers = none, not an error.
# The recovery copy first (#2795), as in server-upgrade.sh install_units.
install_prev_units() {
    local prev_units f
    shopt -s nullglob
    prev_units=(deploy/*.service deploy/*.timer)
    shopt -u nullglob
    if [[ -f tools/server-upgrade-recover.sh ]]; then
        install -d "${RECOVER_COPY%/*}" && install_atomic 0755 tools/server-upgrade-recover.sh "$RECOVER_COPY" \
            || failed "העתקת סקריפט השחזור של $PREV ל-$RECOVER_COPY נכשלה"
    fi
    for f in "${prev_units[@]}"; do
        install_atomic 0644 "$f" "$UNIT_DIR/${f##*/}" || failed "התקנת $f ל-$UNIT_DIR נכשלה"
    done
}
# #2795: what was put back is on the disk before anything says it was.
sync_back() {
    local d
    for d in "$REPO" "$UNIT_DIR" "$HTTP_ROOT"; do
        [[ ! -e "$d" ]] || sync -f "$d" || failed "sync -f $d נכשל -- מה שהוחזר שם עלול לא לשרוד חיתוך חשמל"
    done
}

case "$STAGE" in
    fetch|verify) ;;   # nothing on disk was changed yet
    *)
        [[ "$STAGE" == install ]] || dpkg --configure -a
        rm -f .git/index.lock   # a checkout cut mid-way; nothing else runs git at boot
        units="$(tag_units)" || failed "לא נקראו היחידות של $TAG (git ls-tree) -- לא ידוע מה העדכון הוסיף"
        # #2795: -f -- a checkout cut before its files reached the disk leaves them
        # empty, which git reads as local changes and refuses to overwrite. The
        # tree is ours (server-upgrade.sh checks out without -f, so it started
        # clean); every file comes back from the objects, synced before `install`.
        git checkout -q -f --detach "$PREV"
        install_prev_units
        # A unit $PREV does not have (#1957's backup timer) is stopped, disabled and
        # removed -- left on, it runs against a tree without its module. Same rule
        # as server-upgrade.sh drop_new_timers, from git instead of process memory.
        while IFS= read -r unit; do
            [[ -n "$unit" && ! -f "deploy/$unit" && -e "$UNIT_DIR/$unit" ]] || continue
            systemctl disable --now "$unit" \
                || failed "כיבוי $unit, שהעדכון הוסיף ואין ב-$PREV, נכשל -- הוא עדיין מופעל"
            rm -f "$UNIT_DIR/$unit"
            note+=" · $unit (חדש ב-$TAG) כובה והוסר"
        done <<<"$units"
        systemctl daemon-reload
        ;;
esac

if [[ "$PAYLOAD_SAVED" == 1 ]]; then
    save="$HTTP_ROOT/.pre-upgrade"
    while read -r n path; do
        cp -p "$save/$n" "$path.restore"
        mv -f "$path.restore" "$path"
        cmp -s "$save/$n" "$path"
    done < "$save/list"
    sync_back   # #2795: the payload put back is on disk before its saved copy goes
    rm -rf "$save"
    note+=" · מטען האתחול הקודם הוחזר"
fi

if [[ "$DB_SNAP_VER" =~ ^[0-9]+$ && -f "$DB.pre-upgrade" ]]; then
    now=$(python3 -c 'import sqlite3, sys; print(sqlite3.connect(sys.argv[1]).execute("PRAGMA user_version").fetchone()[0])' "$DB")
    if (( now > DB_SNAP_VER )); then
        got=$(python3 - "$DB.pre-upgrade" "$DB" <<'PYEOF'
import sqlite3, sys
source, target = sqlite3.connect(sys.argv[1]), sqlite3.connect(sys.argv[2])
source.backup(target)
print(target.execute("PRAGMA user_version").fetchone()[0])
PYEOF
)
        [[ "$got" == "$DB_SNAP_VER" ]] || failed "ה-DB הוחזר לגרסת סכמה $got ולא $DB_SNAP_VER"
        note+=" · ה-DB עבר מיגרציה ($DB_SNAP_VER→$now) והוחזר לעותק שלפני העדכון; מה שנרשם מאז אבד"
    fi
fi

why="העדכון ל-$TAG נקטע (כיבוי או הפסקת חשמל) בשלב $STAGE, והוחזר באתחול ל-$PREV$note"
sync_back
report "$why" rollback
rm -f "$STATE"
# ‏#2626: מחיקת הסמן שנכתבה לתיקייה; כשל ב-sync נאמר, לא נבלע (והשחזור אידמפוטנטי).
sync -f "$(dirname "$STATE")" || say "sync of the marker's directory failed -- a power cut now may bring $STATE back"
say "$why"
