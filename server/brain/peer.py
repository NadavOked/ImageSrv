"""B29 (נדב 15/11: "לבנות"): השלמה ממשכפל **שכן** ברשת -- המוח בוחר מאיפה, **במצב צל כברירת מחדל.**

ההרחבה מהתגובות ב-#3181: מגירה שחסרות לה חתיכות יכולה להשלים אותן משלושה מקורות, והמוח בוחר ביניהם **לפי עומס
וקצב, לא לפי סדר קבוע**:

- ``DRAWER`` -- מגירה שכנה **באותו משכפל** (`neighborfill.sh`, K5 ב-`fill.py`): SATA, בלי רשת.
- ``PEER`` -- מגירה של **משכפל אחר** בסבב, שה-ledger העמיד שלה בשרת מחזיק את החתיכות: HTTP Range מ-`chunkcast serve`
  שלו. כיוון השליחה של כרטיס הרשת שלו כמעט ריק (בזמן מולטיקאסט הוא רק מקבל).
- ``HTTP`` -- השרת, כמו היום. דיסק הספרייה ורשת השרת הם הצוואר כשיש הרבה משכפלים.

**ההחלטה** (`choose`, טהורה):

1. המגירות של אותו משכפל מחזיקות את **כל** החסר (וקיים תקציב K5) -> ``DRAWER``: אין רשת ואין שרת.
2. אין שכן כשר -> ``DRAWER`` אם המגירות מחזיקות חלק, אחרת ``HTTP``. שכן כשר = יש לו לפחות חתיכה אחת מהחסר לפי
   ה-ledger של השרת, באותו סבב, אותו אימג' ואותו ``index_xxh64`` של המחיצה; הוא לא נכשל בסבב; כתובת ה-IPv4 שלו
   ידועה; וה-dev של הדיסק שלו ידוע מה-hello. **ותקציב K5 שלו לא קוצץ** -- קיצוץ = Rcvbuf או skipped עלו אצלו,
   כלומר הכתיבה שלו כבר נפגעת, ואסור להוסיף עליה קריאות.
3. **עומס השרת לא נמדד** (אין החלטת K7 עדיין, או שכל אותותיה "לא נמדד") -> ``HTTP``, ובסיבה "לא נמדד". זה מה שהקוד
   עושה היום, ולא "השרת פנוי" (עיקרון 5).
4. אחרת משווים **זמן משוער להשלמה**: ``HTTP`` -- החסר בקצב של תקציב K7. ``PEER`` -- מה שהשכן מחזיק בקצב השכן, והשאר
   ב-HTTP. ``DRAWER`` (כיסוי חלקי) -- באותה צורה. **שרת עמוס** (K7 קיצץ, או שהתקציב שלו ברצפה) = HTTP בקצב הרצפה.
   הזמן הקצר מנצח; בתיקו: ``DRAWER``, ``HTTP``, ``PEER`` (מה שקיים קודם).

**התקרה** (נדב 14/11: "אין אחוזים קבועים"): קצב השכן = המינימום של 100MiB/s (M1/M2 על ברזל, 14/11: מעל ~100MiB/s של
קריאה בסך הכול כל המשכפל האט), של מה שנשאר מגבול הרשת אחרי השידור (``limit_bps`` של K7 -- הגבול העליון שבהגדרות פחות
השידור בפועל), ושל תקציב K5 של המשכפל החסר (הכתיבה שלו). כל אחד מהם נלמד ויורד מעצמו (AIMD) כשהזרם נפגע. השארית של K7
לא נמדדה = הרצפה של K5 (8MiB/s), לא "אין גבול".

**האבטחה** (קודמת לכל השאר; המודל המלא ב-`docs/architecture/interfaces.md`, "השלמה ממשכפל שכן"): השכן אינו נאמן.
המוח כאן רק **בוחר**; את ההוראות לשני הצדדים, עם אסימון לכל שיבוץ, נותן השרת בלבד, ורק ב-``enforce``. חתיכה משכן
עוברת את אותו שער כמו חתיכה מהשרת (``chunkcast apply`` עם האינדקס, העוגן וה-head שהגיעו **מהשרת**).

**הרישום:** שורת ``brain {...}`` בלוגר `imagesrv.brain`, ושורה ב-``brain_decisions`` (``rule="B29"``,
``action="FILL_PEER"``, על הדיסק) **כשההחלטה לדיסק משתנה** -- לא בכל דיווח של 2 שניות.

**המתג** ``brain_peer_mode``: ``off`` / ``shadow`` (ברירת המחדל, וגם לכל ערך לא מוכר) / ``enforce``. ``off`` של המתג
הכללי (``brain_veto_mode``) מכבה גם את זה. **בצל הביצוע נשאר HTTP**, והסוכן לא שומע דבר. היציאה מצל היא החלטה של נדב,
ולפי אגיס (15/11) **לא לפני גיבוב קריפטוגרפי לכל חתיכה** (BLAKE3, #3332): מול שכן זדוני XXH64 אינו מגן (בלוק מזויף עם אותו XXH64 נבנה בפחות
משנייה). הסוכן מסרב ל-P2P בלי רשימת ה-BLAKE3 של המחיצה (`peerfill.sh`).

**A27 (#3659): הרצפה של שכן-משכפל** -- מתחת ל-``fill.PEER_SPEED_KIBS`` (1MiB/s) ל-``fill.PEER_SPEED_S`` (30 ש') הוא נכשל -- היא כבר
אינה קבוע בסוכן (``PF_SPEED_BPS``/``PF_SPEED_S``): ההוראה ``peer_fill`` נושאת אותה (``speed_kibs``, ``speed_s``), והמוח רושם אותה
בשורת ``K-FILL`` (`fill.py`). הוראה בלי השדות (שרת ישן) = הקבוע של הסוכן, והסוכן רושם ביומן שהשתמש בו.

**``enforce`` -- השיבוץ** (`Brain._pair`): השרת לבדו קובע את שני הצדדים. לכל מגירה חסרה -- שכן אחד, הטווחים (מה שחסר לה
וה-ledger של השכן מחזיק), אסימון חדש (`secrets.token_hex(16)`), פורט, קצב ותפוגה. **השכן שומע ראשון** (``peer_serve``
בתשובה שלו); החסר שומע (``peer_fill``) רק אחרי שהשכן קיבל. לכל שכן לכל היותר `MAX_SERVES_PER_PEER` שיבוצים, ויחד לא מעל
100MiB/s. האסימון לא נכתב לשום יומן. **שכן שנכשל** (``targets[].peer_fill_result`` עם ``result: "bad"``) יוצא מכל השיבוצים בגל,
לא משובץ שוב בו, ונרשם ביומן (``chunk_peer_failed``) ובזיכרון (``PEER_BANNED``).
"""

from __future__ import annotations

import ipaddress
import json
import logging
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from server.brain import fill, memory
from server.core import ports as product_ports   # #3871
from server.db import get_setting, journal
from server.rounds import chunkledger

log = logging.getLogger("imagesrv.brain")

RULE = "B29"
ACTION = "FILL_PEER"
SETTING = "brain_peer_mode"
OFF, SHADOW, ENFORCE = "off", "shadow", "enforce"
MODES = (OFF, SHADOW, ENFORCE)
DRAWER, PEER, HTTP = "DRAWER", "PEER", "HTTP"
#: בתיקו בזמן המשוער: מה שקיים קודם.
_TIE_ORDER = (DRAWER, HTTP, PEER)

MIB = fill.MIB
CHUNK_BYTES = fill.CHUNK_BYTES
#: M1/M2, #3181 (ברזל 14/11, משכפל 2): מעל ~100MiB/s של קריאה בסך הכול המשכפל כולו האט. השכן קורא מהדיסק שלו.
CEILING_BPS = fill.CEILING_BPS
#: השארית של K7 לא נמדדה: הרצפה של K5, לא "אין גבול".
UNMEASURED_ROOM_BPS = fill.FLOOR_BPS
#: K7 (`budget.py`) סופר בביטים לשנייה; כאן בבתים.
K7_FLOOR_BYTES = 8_000_000 // 8
_FAILED_MEMBER = ("failed",)
#: ``enforce``: הפורטים של שרתי המסגרות של שכן (`chunkcast serve`), ובכמה מגירות שכן אחד מגיש בבת אחת.
PORT_BASE = product_ports.PEER_FIRST   # server/core/ports.py (#3871)
MAX_SERVES_PER_PEER = product_ports.PEER_COUNT
#: תפוגת שיבוץ: פי 3 ממה שהקצב מאפשר לחתיכות, ועוד 2 דקות (כמו התקרה של neighborfill.sh), ולא יותר מחצי שעה.
UNTIL_SLACK_S = 120
UNTIL_MAX_S = 1800


# --- טווחים --------------------------------------------------------------------------------------------------------


Ranges = tuple[tuple[int, int], ...]


def intersect(a, b) -> Ranges:
    """שני אוספי טווחים סגורים, ממוינים ולא חופפים -> החיתוך שלהם."""
    out, i, j = [], 0, 0
    a, b = list(a), list(b)
    while i < len(a) and j < len(b):
        lo, hi = max(a[i][0], b[j][0]), min(a[i][1], b[j][1])
        if lo <= hi:
            out.append((lo, hi))
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return tuple(out)


def count(ranges) -> int:
    return sum(hi - lo + 1 for lo, hi in ranges)


# --- ההחלטה (טהורה) ------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Peer:
    """מגירה של משכפל אחר שה-ledger של השרת אומר שהיא מחזיקה חלק מהחסר."""
    mac: str
    ip: str | None
    dev: str | None
    serial: str
    ranges: Ranges                  # מה שהיא מחזיקה **מתוך החסר**
    budget_bps: int                 # תקציב K5 של המשכפל שלה (קיצוץ = הכתיבה שלו כבר נפגעת)
    banned: bool = False            # נכשל כשכן בגל הזה (``peer_fill_result``): לא משובץ שוב

    @property
    def covers(self) -> int:
        return count(self.ranges)


@dataclass(frozen=True)
class Load:
    """עומס השרת, מהחלטת K7 האחרונה. ``None`` = לא נמדד (לעולם לא "פנוי")."""
    http_bps: int | None            # תקציב ההגשה ב-HTTP, בבתים לשנייה
    busy: bool | None
    room_bps: int | None            # מה שנשאר מגבול הרשת אחרי השידור, בבתים לשנייה
    why: str


@dataclass(frozen=True)
class Choice:
    source: str                     # DRAWER / PEER / HTTP
    peer: Peer | None
    rate_bps: int                   # הקצב שבו קוראים מהמקור שנבחר (0 = HTTP: התקציב של K7)
    reason: str
    evidence: tuple[str, ...] = ()


def _mib(bps: float) -> str:
    return f"{bps / MIB:.0f}MiB/s"


def load_of(decision) -> Load:
    """ההחלטה האחרונה של K7 (`budget.Decision`, או ``None``) -> `Load`."""
    if decision is None:
        return Load(None, None, None, "אין עדיין החלטה של תקציב הרשת (K7)")
    http = max(int(decision.rate_bps) // 8, 0)
    room = max(int(decision.limit_bps) // 8, 0)
    # #3597: K7 רושם גם את כרטיס המקלטים (`rx_missed`, `rx_no_buffer`). הסף 4 הוא ארבעת החושים של לפני כן (rcvbuf, abandoned,
    # tx_dropped, io_full); שני נוספים לא הופכים "מדד חלק" ל"לא מדד".
    unmeasured = [e for e in decision.evidence if str(e).endswith("=unmeasured")
                  and not str(e).startswith(("rx_missed=", "rx_no_buffer="))]
    if decision.action == "CUT":
        return Load(http, True, room, f"K7 קיצץ: {decision.reason}")
    if http <= K7_FLOOR_BYTES and decision.action not in ("OPEN", "NOT_APPLICABLE"):      # #3838 (ג): הפצה ישירה, הזרם אינו בכרטיס השרת
        return Load(http, True, room, "תקציב ה-HTTP של השרת ברצפה: השידור תופס את הגבול")
    if decision.action == "HOLD" and len(unmeasured) >= 4:
        return Load(http, None, room, "K7 לא מדד אובדן או עומס")
    return Load(http, False, room, f"K7: {decision.action}")


def choose(missing: int, drawer_covers: int, drawer_bps: int, peers: list[Peer], load: Load,
           needy_bps: int) -> Choice:
    """(כמה חסר, כמה מזה המגירות של אותו משכפל מחזיקות, תקציב K5 שלו, השכנים, עומס השרת, תקציב K5 של החסר)
    -> מאיפה להשלים."""
    evidence = [f"missing={missing}", f"drawer_covers={drawer_covers}", f"drawer_budget={_mib(drawer_bps)}",
                f"server={'unmeasured' if load.busy is None else 'busy' if load.busy else 'ok'} ({load.why})",
                f"http_budget={'unmeasured' if load.http_bps is None else _mib(load.http_bps)}",
                f"room={'unmeasured' if load.room_bps is None else _mib(load.room_bps)}"]
    eligible = []
    for p in sorted(peers, key=lambda p: (-p.covers, p.mac, p.serial)):
        why = ("נכשל כשכן בגל הזה: לא משובץ שוב" if p.banned else "אינו מחזיק אף חתיכה מהחסר" if p.covers == 0 else "כתובת ה-IPv4 שלו אינה ידועה" if not p.ip
               else "ה-dev של הדיסק שלו אינו ידוע" if not p.dev
               else f"תקציב K5 שלו קוצץ ל-{_mib(p.budget_bps)}: הכתיבה שלו כבר נפגעת" if p.budget_bps < CEILING_BPS
               else "")
        evidence.append(f"peer {p.mac}/{p.serial} covers={p.covers}" + (f" -- {why}" if why else ""))
        if not why:
            eligible.append(p)
    if drawer_covers >= missing and drawer_bps > 0:
        return Choice(DRAWER, None, drawer_bps, "המגירות של אותו משכפל מחזיקות את כל החסר: בלי רשת", tuple(evidence))
    best = eligible[0] if eligible else None
    if best is None:
        if drawer_covers > 0 and drawer_bps > 0:
            return Choice(DRAWER, None, drawer_bps, "אין משכפל שכן כשר: מה שהמגירות מחזיקות מהן, והשאר ב-HTTP",
                          tuple(evidence))
        return Choice(HTTP, None, 0, "אין משכפל שכן כשר ואין מגירה שכנה: השלמה ב-HTTP", tuple(evidence))
    room = load.room_bps if load.room_bps is not None else UNMEASURED_ROOM_BPS
    peer_bps = max(min(CEILING_BPS, room, needy_bps), 1)
    evidence.append(f"peer_rate={_mib(peer_bps)} (min of {_mib(CEILING_BPS)}, room, needy {_mib(needy_bps)})")
    if load.busy is None or load.http_bps is None:
        return Choice(HTTP, None, 0, "עומס השרת לא נמדד: ההשלמה נשארת ב-HTTP, כמו היום", tuple(evidence))
    http_bps = max(min(load.http_bps, K7_FLOOR_BYTES) if load.busy else load.http_bps, 1)
    rest = lambda covered, bps: (covered * CHUNK_BYTES / bps + (missing - covered) * CHUNK_BYTES / http_bps)  # noqa: E731
    times = {HTTP: missing * CHUNK_BYTES / http_bps, PEER: rest(min(best.covers, missing), peer_bps)}
    if drawer_covers > 0 and drawer_bps > 0:
        times[DRAWER] = rest(min(drawer_covers, missing), drawer_bps)
    evidence.append("eta " + ", ".join(f"{k}={v:.0f}s" for k, v in times.items()))
    pick = min(times, key=lambda k: (round(times[k], 3), _TIE_ORDER.index(k)))
    busy = f" (השרת עמוס: {load.why})" if load.busy else ""
    if pick == PEER:
        return Choice(PEER, best, peer_bps, f"משכפל שכן {best.mac} מחזיק {best.covers} מתוך {missing} החתיכות: "
                      f"מהיר יותר מהשרת{busy}", tuple(evidence))
    if pick == DRAWER:
        return Choice(DRAWER, None, drawer_bps, f"המגירות של אותו משכפל מהירות יותר{busy}", tuple(evidence))
    return Choice(HTTP, None, 0, f"השרת מהיר יותר מהשכן {best.mac}", tuple(evidence))


def mode(conn) -> str:
    """המתג. ``off`` של המתג הכללי מכבה גם את זה; ערך חסר, לא מוכר או שלא נקרא = ``shadow``."""
    from server.brain import veto            # noqa: PLC0415 -- כמו ב-`budget.production`
    if veto.mode(conn) == OFF:
        return OFF
    try:
        raw = get_setting(conn, SETTING)
    except Exception:                        # noqa: BLE001 — המוח אינו מפיל דיווח
        log.exception("brain: %s unreadable, using %s", SETTING, SHADOW)
        return SHADOW
    return raw if raw in MODES else SHADOW


# --- הקריאה מה-DB ----------------------------------------------------------------------------------------------------


def _ipv4(raw) -> str | None:
    try:
        return str(ipaddress.IPv4Address(raw)) if isinstance(raw, str) else None
    except ValueError:
        return None


def _ranges(raw) -> Ranges:
    return tuple((int(lo), int(hi)) for lo, hi in json.loads(raw or "[]"))


class Brain:
    """המצב של B29 בשרת אחד: ההחלטה האחרונה לכל (גל, דיסק), השיבוצים הפתוחים (``enforce``), והשכנים שנכשלו."""

    def __init__(self, wall: Callable[[], float] = time.time, clock: Callable[[], float] = time.monotonic,
                 token: Callable[[], str] = lambda: secrets.token_hex(16)):
        self._wall, self._clock, self._token = wall, clock, token
        self._lock = threading.Lock()
        self._last: dict[tuple[str, str], tuple] = {}
        self._assign: dict[str, Assignment] = {}            # id -> השיבוץ
        self._banned: dict[str, set[str]] = {}               # גל -> המשכפלים שנכשלו כשכנים בו

    def observe(self, conn, round_row, mac: str, body: dict, *, fill_brain, budget=None,
                mode_of: Callable = mode) -> dict:
        """דיווח התקדמות -> מה שהסוכן שומע: כלום בצל; ב-``enforce`` ``peer_serve`` לשכן ו-``peer_fill`` לחסר.
        לעולם אינו זורק."""
        try:
            return self._observe(conn, round_row, mac, body, fill_brain, budget, mode_of(conn))
        except Exception:                    # noqa: BLE001 — מוח שנפל הוא שורת יומן, לא דיווח שנכשל
            log.exception("brain: the peer fill decision for %s failed", mac)
            return {}

    def _observe(self, conn, round_row, mac, body, fill_brain, budget, mode_now) -> dict:
        session = body.get("session_id")
        targets = [t for t in body.get("targets") or [] if isinstance(t, dict)]
        if mode_now == OFF:
            with self._lock:
                self._assign.clear()
            return {}
        self._results(conn, round_row, session, mac, targets)
        needs = [(t, fill.parse_need(t.get("rejoin"))) for t in targets]
        needs = [(t, n) for t, n in needs if n is not None and n.missing > 0 and n.part is not None]
        disks = chunkledger.disks_of(conn, mac)
        load = load_of(getattr(budget, "last", None))
        needy_bps = fill_brain.budget(session, mac)
        rejoining = set()
        for t, need in needs:
            serial, size = disks.get(t.get("dev"), (t.get("serial"), None))
            if not serial or not isinstance(t.get("dev"), str):
                continue
            rejoining.add(serial)
            choice = self._choose(conn, session, mac, serial, size, need, load, needy_bps, fill_brain)
            if choice is None:
                continue
            self._record(conn, round_row, mode_now, mac, serial, need, choice)
            if mode_now == ENFORCE and choice.source == PEER:
                self._pair(conn, session, mac, serial, t["dev"], need, choice)
        return self._tell(session, mac, rejoining) if mode_now == ENFORCE else {}

    # -- ההחלטה --

    def _choose(self, conn, session, mac, serial, size, need, load, needy_bps, fill_brain) -> Choice | None:
        rows = conn.execute("SELECT * FROM chunk_ledger WHERE round_id = ? AND part = ?",
                            (session, need.part)).fetchall()
        twins = {(r["serial"], r["size_bytes"]) for r in conn.execute(
            "SELECT serial, size_bytes FROM chunk_ledger WHERE part = ?", (chunkledger.TWIN,))}
        mine = [r for r in rows if r["serial"] == serial and (size is None or r["size_bytes"] == size)]
        facts = mine[0] if mine else next((r for r in rows), None)
        if facts is None:
            return None                      # אין ledger לאף דיסק במחיצה הזו: אין ממה לבחור, ו-K5 כבר רשם HTTP
        lacking = (tuple(chunkledger.missing(list(_ranges(mine[0]["ranges_json"])), mine[0]["chunks"])) if mine
                   else ((0, facts["chunks"] - 1),))
        same = [r for r in rows if r["image_id"] == facts["image_id"] and r["index_xxh64"] == facts["index_xxh64"]
                and r["serial"] != serial and (r["serial"], r["size_bytes"]) not in twins]
        drawer = set()
        for r in same:
            if r["mac"] == mac:
                drawer.update(_expand(intersect(lacking, _ranges(r["ranges_json"]))))
        with self._lock:
            banned = set(self._banned.get(session, ()))
        peers = []
        for r in same:
            if r["mac"] == mac:
                continue
            member = conn.execute("SELECT state FROM session_members WHERE session_id = ? AND mac = ?",
                                  (session, r["mac"])).fetchone()
            if member is None or member["state"] in _FAILED_MEMBER:
                continue
            dev = next((d for d, who in chunkledger.disks_of(conn, r["mac"]).items()
                        if who == (r["serial"], r["size_bytes"])), None)
            peers.append(Peer(r["mac"], _ip_of(conn, r["mac"]), dev, r["serial"],
                              intersect(lacking, _ranges(r["ranges_json"])), fill_brain.budget(session, r["mac"]),
                              r["mac"] in banned))
        return choose(need.missing, min(len(drawer), need.missing), needy_bps, peers, load, needy_bps)

    def _record(self, conn, round_row, mode_now, mac, serial, need, c: Choice) -> None:
        key = (str(round_row["id"]), serial)
        sig = (c.source, c.peer.mac if c.peer else None, c.peer.serial if c.peer else None)
        with self._lock:
            if self._last.get(key) == sig:
                return
            self._last[key] = sig
        peer = {"peer_mac": c.peer.mac, "peer_disk": c.peer.serial, "peer_covers": c.peer.covers} if c.peer else {}
        enforced = mode_now == ENFORCE and c.source == PEER
        line = "brain " + json.dumps({
            "at": datetime.fromtimestamp(self._wall(), timezone.utc).isoformat(timespec="seconds"),
            "mode": mode_now, "rule": RULE, "action": ACTION, "verdict": c.source, "mac": mac, "disk": serial,
            "missing": need.missing, **peer, "rate_mibps": round(c.rate_bps / MIB, 1), "enforced": enforced,
            "say": "נאכף: השרת משבץ את השכן" if enforced else "מחושב ונרשם בלבד (צל): ההשלמה נשארת ב-HTTP",
            "reason": c.reason, "evidence": list(c.evidence)}, ensure_ascii=False)
        log.info("%s", line)
        memory.record(conn, mode=mode_now, rule=RULE, action=ACTION, verdict=c.source, reason=c.reason,
                      evidence=(f"source={c.source}", f"mac={mac}",
                                *((f"peer={c.peer.mac}", f"peer_disk={c.peer.serial}") if c.peer else ()),
                                *c.evidence),
                      round_id=round_row["id"], entity=("disk", serial),
                      # #3750: צל, או החלטה שאינה שיבוץ שכן: אין מה לסגור. שיבוץ ב-enforce נשאר פתוח עד `peer_fill_result` (שאלה פתוחה ב-#3750)
                      outcome=memory.na("צל: נרשם בלבד, ההשלמה נשארת ב-HTTP") if mode_now != ENFORCE
                      else memory.na(f"לא שובץ שכן: ההשלמה נשארת במקור {c.source}") if c.source != PEER else None)

    # -- השיבוץ (``enforce`` בלבד) --

    def _pair(self, conn, session, mac, serial, dev, need, c: Choice) -> None:
        """שיבוץ אחד: החסר (mac, serial) מהשכן שנבחר. השרת לבדו קובע את שני הצדדים, את הטווחים ואת האסימון."""
        now = self._clock()
        needy_ip = _ip_of(conn, mac)
        with self._lock:
            self._expire(now)
            if any(a.session == session and a.needy_serial == serial for a in self._assign.values()):
                return                       # כבר משובץ: לא שני שכנים לאותה מגירה
            theirs = [a for a in self._assign.values() if a.peer_mac == c.peer.mac]
            used = sum(a.rate_bps for a in theirs)
            ports = {a.port for a in theirs}
        why = ("כתובת ה-IPv4 של המשכפל החסר אינה ידועה" if needy_ip is None
               else f"השכן {c.peer.mac} כבר מגיש ל-{len(theirs)} מגירות" if len(theirs) >= MAX_SERVES_PER_PEER
               else "")
        rate = min(c.rate_bps, CEILING_BPS - used)
        if not why and rate < fill.FLOOR_BPS:
            why = f"לשכן {c.peer.mac} נשארו {_mib(max(rate, 0))} מתוך {_mib(CEILING_BPS)} (התקרה של קריאות המשכפל)"
        if why:
            log.info("brain %s", json.dumps({"rule": RULE, "action": "PEER_PAIR", "verdict": "SKIPPED", "mac": mac,
                                             "disk": serial, "peer_mac": c.peer.mac, "reason": why},
                                            ensure_ascii=False))
            return
        covers = min(c.peer.covers, need.missing)
        until = int(min(UNTIL_MAX_S, covers * CHUNK_BYTES * 3 / rate + UNTIL_SLACK_S))
        a = Assignment(id=secrets.token_hex(8), session=session, needy_mac=mac, needy_ip=needy_ip,
                       needy_serial=serial, needy_dev=dev, peer_mac=c.peer.mac, peer_ip=c.peer.ip,
                       peer_dev=c.peer.dev, peer_serial=c.peer.serial, part=need.part, ranges=c.peer.ranges,
                       rate_bps=int(rate), token=self._token(),
                       port=next(p for p in range(PORT_BASE, PORT_BASE + MAX_SERVES_PER_PEER) if p not in ports),
                       until_s=until, deadline=now + until)
        with self._lock:
            self._assign[a.id] = a
        # the token is never in a log line: only the two members hear it, each in its own answer
        log.info("brain %s", json.dumps({"rule": RULE, "action": "PEER_PAIR", "verdict": "PAIRED", "id": a.id,
                                         "mac": mac, "disk": serial, "peer_mac": a.peer_mac, "peer_disk": a.peer_serial,
                                         "part": a.part, "chunks": count(a.ranges), "rate_mibps": round(rate / MIB, 1),
                                         "until_s": until}, ensure_ascii=False))

    def _expire(self, now: float) -> None:
        for k in [k for k, a in self._assign.items() if a.deadline <= now]:
            del self._assign[k]

    def _tell(self, session, mac, rejoining: set[str]) -> dict:
        """מה כל צד שומע. השכן שומע ראשון; החסר שומע רק אחרי שהשכן קיבל את ההוראה בתשובה שלו."""
        now = self._clock()
        out: dict = {}
        with self._lock:
            self._expire(now)
            for k in [k for k, a in self._assign.items()        # the needy drawer's fill ended: its pairing with it
                      if a.session == session and a.needy_mac == mac and a.needy_serial not in rejoining]:
                del self._assign[k]
            mine = [a for a in self._assign.values() if a.session == session]
            serve = [a for a in mine if a.peer_mac == mac]
            for a in serve:
                a.told_peer = True
            fills = [a for a in mine if a.needy_mac == mac and a.told_peer]
        left = lambda a: max(int(a.deadline - now), 1)          # noqa: E731
        if serve:
            out["peer_serve"] = [{"id": a.id, "dev": a.peer_dev, "part": a.part, "listen": a.peer_ip,
                                  "port": a.port, "allow": a.needy_ip, "token": a.token,
                                  "rate_mibs": max(a.rate_bps // MIB, 1), "until_s": left(a)} for a in serve]
        if fills:
            out["peer_fill"] = {a.needy_serial: {"id": a.id, "dev": a.needy_dev, "part": a.part, "ip": a.peer_ip,
                                                 "port": a.port, "me": a.needy_ip, "token": a.token,
                                                 "ranges": [list(r) for r in a.ranges],
                                                 "rate_mibs": max(a.rate_bps // MIB, 1), "until_s": left(a),
                                                 # A27 (#3659): the floor under which this neighbour failed -- the brain's word
                                                 # (`fill.limits`), not the agent's `PF_SPEED_*`; the K-FILL row says it
                                                 "speed_kibs": fill.PEER_SPEED_KIBS, "speed_s": fill.PEER_SPEED_S}
                                for a in fills}
        return out

    def _results(self, conn, round_row, session, mac, targets) -> None:
        """``targets[].peer_fill_result`` של החסר: שכן שלא מסר חתיכות שלמות יוצא מכל השיבוצים בגל, ונרשם ביומן."""
        for t in targets:
            got = t.get("peer_fill_result")
            if not isinstance(got, dict) or not isinstance(got.get("id"), str):
                continue
            with self._lock:
                a = self._assign.pop(got["id"], None)
            if a is None or a.needy_mac != mac or a.session != session:
                continue                     # not ours, or already gone: nothing to believe in it
            result = got.get("result")
            if result != "bad":
                continue                     # ok, or "none" = this cloner's own failure (no BLAKE3 list, apply did not start)
            with self._lock:
                self._banned.setdefault(session, set()).add(a.peer_mac)
                for k in [k for k, b in self._assign.items() if b.session == session and b.peer_mac == a.peer_mac]:
                    del self._assign[k]
            why = str(got.get("why") or result or "no result")[:200]
            journal(conn, "chunk_peer_failed", f"{a.peer_mac} -> {mac} ({a.needy_serial}): {why}",
                    machine=a.peer_mac, round_id=round_row["id"])
            memory.record(conn, mode=ENFORCE, rule=RULE, action="PEER_BANNED", verdict="BANNED",
                          reason=f"המשכפל השכן {a.peer_mac} לא מסר חתיכות שלמות: {why}. לא ישובץ שוב בגל הזה",
                          evidence=(f"id={a.id}", f"needy={mac}", f"result={result}"),
                          round_id=round_row["id"], entity=("machine", a.peer_mac),
                          outcome="השכן הוצא מכל השיבוצים בגל הזה")      # #3750: זה כבר בוצע למעלה (`_banned`, `_assign`)


@dataclass
class Assignment:
    """שיבוץ אחד שהשרת קבע: מי מגיש, למי, מה, באיזה קצב, ועד מתי. האסימון -- רק בשתי התשובות."""
    id: str
    session: str
    needy_mac: str
    needy_ip: str
    needy_serial: str
    needy_dev: str
    peer_mac: str
    peer_ip: str
    peer_dev: str
    peer_serial: str
    part: int
    ranges: Ranges
    rate_bps: int
    token: str
    port: int
    until_s: int
    deadline: float
    told_peer: bool = False


def _ip_of(conn, mac: str) -> str | None:
    row = conn.execute("SELECT ip FROM net_devices WHERE mac = ?", (mac,)).fetchone()
    return _ipv4(row["ip"]) if row is not None else None


def _expand(ranges):
    for lo, hi in ranges:
        yield from range(lo, hi + 1)
