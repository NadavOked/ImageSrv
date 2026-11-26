"""#3125 (S62 in `server/brain/catalog.toml`) — which tunes a machine's speaker played in a round.

**מה מגיע** (`agent/lib/core/roundbeep.sh: _rb_note`, `agent/lib/core/datareport.sh: beeps_json`): בכל דיווח
התקדמות של סבב, ברמת המכונה, `"beeps": [{"at", "kind", "outcome", "why"?}]` — 50 האחרונים של הסבב:
`requested` כשהסוכן ביקש את הצליל, ואחריו `played` (נכתב לרמקול עד הסוף) או `unheard` עם הסיבה (אין רמקול,
כבוי בהגדרות, הכתיבה להתקן נכשלה, חזרה בתוך 5 שניות...). **"לא הושמע" אינו "הושמע"** (עיקרון 5): צליל שביקשו
ואין לו שורת תוצאה — לא ידוע, ולא נספר כנשמע.

**בשרת:** `session_members.beeps_json` (`well_formed`; COALESCE — דיווח בלי השדה אינו מוחק), ובמעבר
הטרמינלי של המכונה שורת יומן אחת, **`round_beeps`** (`note_final`). לעולם אינו זורק.
"""

from __future__ import annotations

import logging

from server.db import journal, SqlConnection

log = logging.getLogger("imagesrv.beeps")

#: roundbeep.sh's tunes (`_rb_play`); failmid: #2067
KINDS = frozenset({"success", "failure", "failmid", "netlost", "netback", "pulled"})
OUTCOMES = frozenset({"requested", "played", "unheard"})
MAX_ITEMS = 100
MAX_WHY = 200


def well_formed(raw: object) -> list[dict] | None:
    """None — השדה חסר או אינו רשימה. אחרת רק הפריטים התקינים, בסדרם; פריט פגום נזנח ולא מתוקן."""
    if not isinstance(raw, list):
        return None
    out = []
    for item in raw[-MAX_ITEMS:]:
        if not isinstance(item, dict):
            continue
        at, kind, outcome = item.get("at"), item.get("kind"), item.get("outcome")
        if not (isinstance(at, int) and not isinstance(at, bool) and at > 0) or kind not in KINDS \
                or outcome not in OUTCOMES:
            continue
        entry = {"at": at, "kind": kind, "outcome": outcome}
        if outcome == "unheard":
            why = item.get("why")
            entry["why"] = why[:MAX_WHY] if isinstance(why, str) else None
        out.append(entry)
    return out


def summary(beeps: list[dict] | None) -> str:
    """"played: success · not heard: failure (no PC speaker (pcspkr)) · asked, outcome not known: netlost"."""
    played, unheard, asked = [], [], []
    open_: dict[str, int] = {}
    for b in beeps or []:
        if b["outcome"] == "requested":
            open_[b["kind"]] = open_.get(b["kind"], 0) + 1
            continue
        if open_.get(b["kind"]):
            open_[b["kind"]] -= 1
        if b["outcome"] == "played":
            played.append(b["kind"])
        else:
            unheard.append(f"{b['kind']} ({b.get('why') or '?'})")
    for kind, n in open_.items():
        asked += [kind] * n
    parts = []
    if played:
        parts.append("played: " + ", ".join(played))
    if unheard:
        parts.append("not heard: " + ", ".join(unheard))
    if asked:
        parts.append("asked, outcome not known: " + ", ".join(asked))
    return " · ".join(parts)


def note_final(conn: SqlConnection, mac: str, session_id: str, beeps: list[dict] | None) -> None:
    """במעבר הטרמינלי של המכונה (`reports.ingest`): שורת יומן אחת כשהסבב נשא צלילים. לעולם אינו זורק."""
    try:
        text = summary(beeps)
        if text:
            journal(conn, "round_beeps", f"{mac} in {session_id}: {text}", machine=mac, round_id=session_id)
    except Exception:             # noqa: BLE001
        log.exception("beep note of %s failed", mac)
