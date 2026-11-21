"""#3064, שלב 1: צילום מצב קפוא של גל — מה שהמוח מחליט עליו. **קורא בלבד.**

הצילום אוסף, לכל גל, את המשכפלים, המגירות שלהם, וכל **ראיית חיים** שהשרת כבר מחזיק, עם הזמן שבו
נראתה (זמן הקבלה בשרת, לעולם לא השעון של המשכפל):

- ``progress`` — דיווח ההתקדמות האחרון (`session_members.updated_at`, כל ~2 ש').
- ``ledger`` — הכתיבה האחרונה ליומן החתיכות של המשכפל בגל הזה (`chunk_ledger.updated_at`).
- ``listening`` / ``ended`` — הודעת הבקרה האחרונה של המשכפל במנוע החתיכות (`_Mark.at`).
- ``fetched`` — הורדה ב-HTTP שהתקדמה (`_Wave.progress`: היומן גדל, או ש-`fetched` של #3112 גדל).
- ``served`` — **V6** (שלב 2): בייטים שהשרת עצמו הגיש למשכפל ב-HTTP של קובצי האימג' (`server.brain.served`).
  לא דורש דבר מהסוכן, ומתאר מי **מושך** גם כשהוא שותק בכל ערוץ אחר.

**חוש שלא הצלחנו לקרוא אינו ברירת מחדל.** שאילתה שנכשלה נרשמת ב-`unknown` של המשכפל (או של הצילום
כולו), וההחלטה רואה "לא ידוע" — לא "מת" ולא "חי" (עיקרון 5).

**`now` מוזרק תמיד.** אותו קלט נותן אותו צילום ואותו `digest()`, ולכן אותה החלטה (I1).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Iterable, Mapping

PROGRESS, LEDGER, LISTENING, ENDED, FETCHED, SERVED = "progress", "ledger", "listening", "ended", "fetched", "served"
#: ראיית **עבודה**: משהו נכתב או הורד. השאר הם ראיית חיים בלבד (המשכפל מדבר).
WORK = frozenset({LEDGER, FETCHED, SERVED})
#: מצבי מגירה שהמוח מכיר כסופיים; כל מצב אחר הוא "פתוח".
DONE, FAILED = "done", "failed"
#: `reports.LOST`: מצב החבר אחרי `room._mark_lost`.
LOST_STATE = "lost"


@dataclass(frozen=True)
class Evidence:
    kind: str
    seen_at: float          # שניות epoch, זמן הקבלה בשרת
    source: str             # מאיפה נקרא: טבלה ועמודה, או שדה במנוע


@dataclass(frozen=True)
class Drawer:
    mac: str
    dev: str
    serial: str | None
    state: str              # כפי שהמשכפל דיווח (waiting/writing/verifying/done/failed...), או "unknown"


@dataclass(frozen=True)
class Cloner:
    mac: str
    state: str              # `session_members.state`, או "unknown"
    evidence: tuple[Evidence, ...]
    unknown: tuple[str, ...] = ()   # חושים שלא נקראו


@dataclass(frozen=True)
class Proposal:
    """פעולה הרסנית שהקוד הקיים עומד לבצע. המוח מזהה אותה ושופט אותה, הוא לא יוזם אותה."""
    action: str             # FAIL_WAVE / FAIL_DISKS / EXPIRE_LEDGER
    rule: str               # הכלל הקיים בקטלוג (D15, D16, D18, D19, D21)
    targets: tuple[str, ...]        # המשכפלים שהפעולה נשענת על השתיקה שלהם
    reason: str
    drawers: tuple[tuple[str, str], ...] = ()   # (mac, serial) שהפעולה פוגעת בהם
    own_evidence: str | None = None             # כשל עם ראיה חיובית משלו (readback, SMART, D14, D12)
    held_s: float = 0.0                         # כמה זמן הפעולה הזו כבר מוחזקת


@dataclass(frozen=True)
class Recall:
    """#3064 שלב 5: מה הזיכרון אומר על משכפל אחד -- **עבר, לא ראיה של עכשיו** (`server.brain.recall.load`).

    ``returns``: פעמים שהמשכפל שתק עד שהמוח קבע LOST, **וחזר** באותו סבב: ``(id, מתי, שניות שתיקה לפחות, סבב)``.
    ``losses``: סבבים שבהם המוח קבע LOST ולא נרשמה חזרה: ``(id של ההחלטה, מתי, סבב)``.
    ``unread``: הזיכרון לא נקרא -- "לא ידוע", לא "ריק" (עיקרון 5)."""
    mac: str
    returns: tuple[tuple[int, float, float, str], ...] = ()
    losses: tuple[tuple[int, float, str], ...] = ()
    unread: bool = False


@dataclass(frozen=True)
class Snapshot:
    now: float
    round_id: str
    part: int | None
    cloners: tuple[Cloner, ...]
    drawers: tuple[Drawer, ...]
    proposals: tuple[Proposal, ...] = ()
    unknown: tuple[str, ...] = ()   # חושים שלא נקראו לאף משכפל
    memory: tuple[Recall, ...] = ()  # #3064 שלב 5: הזיכרון על המשכפלים שהפעולות נשענות על השתיקה שלהם
    up_at: float | None = None       # #3330 (KT5): רגע עליית השרת (epoch). None = לא נמדד: אין חלון חסד

    def cloner(self, mac: str) -> Cloner | None:
        return next((c for c in self.cloners if c.mac == mac), None)

    def recall(self, mac: str) -> Recall | None:
        return next((r for r in self.memory if r.mac == mac), None)

    def digest(self) -> str:
        """hash של הצילום כולו, לשורת היומן: אותו צילום → אותו hash (I1)."""
        body = asdict(self)
        if body["up_at"] is None:
            del body["up_at"]       # לא נמדד: אותו hash כמו לפני #3330 (הקלטות קיימות נשארות קריאות)
        raw = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def epoch(ts: object) -> float | None:
    """ISO 8601 של ה-DB → שניות epoch. ערך שאינו נקרא = None (אין ראיה), לא אפס."""
    if not isinstance(ts, str) or not ts:
        return None
    try:
        return datetime.fromisoformat(ts).timestamp()
    except ValueError:
        return None


def _drawers(mac: str, raw: object) -> tuple[list[Drawer], bool]:
    """המגירות מ-`targets_json`. False = לא נקרא (אז אין לדעת מה פתוח)."""
    if raw is None:
        return [], True
    try:
        targets = json.loads(raw) if isinstance(raw, str) else None
    except ValueError:
        targets = None
    if not isinstance(targets, list):
        return [], False
    out = [Drawer(mac, str(t.get("dev", "?")), t.get("serial") if isinstance(t.get("serial"), str) else None,
                  str(t.get("state") or "unknown"))
           for t in targets if isinstance(t, dict)]
    return out, True


def build(*, now: float, round_id: str, members: Iterable[Mapping], ledger: Iterable[Mapping] = (),
          engine: Iterable[tuple[str, str, float, str]] = (), part: int | None = None,
          proposals: Iterable[Proposal] = (), unknown: Iterable[str] = (),
          served: Iterable[tuple[str, str, float, str]] = (), memory: Iterable[Recall] = (),
          up_at: float | None = None) -> Snapshot:
    """פונקציה טהורה: מה שנקרא → צילום קפוא. הסדר של הקלט אינו משנה את הצילום.

    `engine` הוא ``(mac, kind, age_s, source)`` מהמנוע (שעון monotonic), ומומר כאן לזמן הקבלה: ``now - age``.
    `served` באותה צורה (V6, `served.Served.attribute`), אבל **רק למי שחבר בגל**: משכפל אחר שמושך עכשיו אינו
    ראיה על חברי הגל הזה (I3), ואינו מוסיף שורת K1 לצילום."""
    found: dict[str, list[Evidence]] = {}
    blind: dict[str, list[str]] = {}
    states: dict[str, str] = {}
    drawers: list[Drawer] = []
    for m in members:
        mac = str(m["mac"])
        states[mac] = str(m["state"])
        # חבר שסומן `lost`: `updated_at` שלו הוא רגע הסימון (`room._mark_lost`), לא דיווח שלו
        if states[mac] != LOST_STATE:
            at = epoch(m["updated_at"])
            if at is not None:
                found.setdefault(mac, []).append(Evidence(PROGRESS, at, "session_members.updated_at"))
            else:
                blind.setdefault(mac, []).append("session_members.updated_at")
        mine, read = _drawers(mac, m["targets_json"] if "targets_json" in m.keys() else None)
        drawers += mine
        if not read:
            blind.setdefault(mac, []).append("session_members.targets_json")
    for row in ledger:
        at = epoch(row["updated_at"])
        if at is not None:
            found.setdefault(str(row["mac"]), []).append(Evidence(LEDGER, at, "chunk_ledger.updated_at"))
    for mac, kind, age, source in engine:
        found.setdefault(mac, []).append(Evidence(kind, round(now - age, 3), source))
    for mac, kind, age, source in served:
        if mac in states:
            found.setdefault(mac, []).append(Evidence(kind, round(now - age, 3), source))
    macs = sorted(set(states) | set(found) | set(blind))
    cloners = tuple(
        Cloner(mac, states.get(mac, "unknown"),
               tuple(sorted(found.get(mac, []), key=lambda e: (e.seen_at, e.kind, e.source))),
               tuple(sorted(blind.get(mac, []))))
        for mac in macs)
    return Snapshot(now=now, round_id=round_id, part=part, cloners=cloners,
                    drawers=tuple(sorted(drawers, key=lambda d: (d.mac, d.dev, d.serial or ""))),
                    proposals=tuple(proposals), unknown=tuple(sorted(set(unknown))),
                    memory=tuple(sorted(memory, key=lambda r: r.mac)), up_at=up_at)


def from_db(conn, round_id: str, now: float, **kw) -> Snapshot:
    """הצילום של גל מה-DB, ב-SELECT בלבד. שאילתה שנכשלה = החוש שלה `unknown`, לא ריק."""
    unknown: list[str] = []
    try:
        members = conn.execute("SELECT mac, state, updated_at, targets_json FROM session_members"
                               " WHERE session_id = ?", (round_id,)).fetchall()
    except sqlite3.Error:
        members, unknown = [], [*unknown, "session_members"]
    try:
        ledger = conn.execute("SELECT mac, MAX(updated_at) AS updated_at FROM chunk_ledger"
                              " WHERE round_id = ? GROUP BY mac", (round_id,)).fetchall()
    except sqlite3.Error:
        ledger, unknown = [], [*unknown, "chunk_ledger"]
    return build(now=now, round_id=round_id, members=members, ledger=ledger,
                 unknown=[*unknown, *kw.pop("unknown", ())], **kw)


def wave_evidence(wave, clock_now: float) -> list[tuple[str, str, float, str]]:
    """הראיות שמנוע החתיכות מחזיק בזיכרון לגל (`chunksend._Wave`), כ-``(mac, kind, age_s, source)``.

    נקרא **תחת הנעילה של המנוע**: `report` משנה את המילונים האלה מתהליכוני הבקשות."""
    out = [(mac, mk.state if mk.state in (LISTENING, ENDED) else LISTENING, max(clock_now - mk.at, 0.0),
            f"chunk-pass p{part} pass {mk.round}")
           for (part, mac), mk in wave.marks.items()]
    out += [(mac, FETCHED, max(clock_now - at, 0.0), f"fetching p{part}")
            for (part, mac), at in wave.progress.items()]
    return out
