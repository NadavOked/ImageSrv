"""#3064 / #3181: הצד שאוסף ורושם של המוח של המצטרף (`server.brain.joiner`). **במצב צל בלבד.**

נקרא מכל דיווח התקדמות של חבר בגל הנוכחי של סבב החדר (`api._round_extras`), אחרי פס הייצור (`prodline`) ואחרי
K5 (`fill`), ו**אינו מוסיף דבר לתשובה**: הסוכן לא שומע עליו, והקוד הקיים פועל כמו היום. המתג הוא של הווטו
(``brain_veto_mode``): ``off`` = לא מחושב כלום; ``shadow`` ו-``enforce`` = מחושב ונרשם. ל-B1–B46 עוד אין מסלול
אכיפה, ולכן גם ב-``enforce`` הרישום הוא ``shadow`` (ונאמר כך בשורה).

**מה נרשם** (``brain_decisions`` דרך `memory.record`, אותו מבנה של K5): שורה **רק כשהפסק משתנה** לאותו
(סבב, ישות, כלל) -- הסוכן מדווח כל 2 שניות, ושורה בכל דיווח הייתה קוברת את ההחלטה. בנוסף:

- **V15** -- תור הממתינים: דיסק שהופיע ב-``waiting`` אחרי שהמשכפל כבר כתב בגל. שורה אחת כשנכנס, ו-`queue`.
- **R4** -- ציר הזמן של מצטרף: נכנס, התקבל, הצטרף, השלים, קריאה חוזרת, נכתב (`timeline` מרכיב אותו).
- **R5** -- "משלים מגירה N: X חתיכות, Y%" בלוגר `imagesrv.brain`, בכל רבע מההשלמה, ובציר הזמן.
- **X1 / X17** -- המדידות שהמוח מכייל מהן (זמן הצטרפות; קצב השלמה): שורת ``MEASURE`` לכל מצטרף, כדי שהלמידה
  תשרוד אתחול של השרת. הקריאה חזרה -- `memory.decisions(rule=...)`, פעם אחת לכל גל.

מה שהשרת לא ראה (אתחול השרת באמצע סבב, שאילתה שנכשלה) הוא "לא נמדד", והפסק שנשען עליו ``UNDECIDED``.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from server.brain import fill, joiner as J, memory
from server.core.logtext import one_line
from server.disks import drawer_slots, model_slots
from server.rounds import prodline, repair

log = logging.getLogger("imagesrv.brain")

OFF, SHADOW = "off", "shadow"
#: שלבים שאחריהם הכונן כותב בגל (הסוכן: ``writing``/``verifying``/``done``).
WROTE = ("writing", "verifying", "done")
#: כמה מדידות קודמות קוראים לכיול (X1, X17).
LEARN_LIMIT = 150


def _int(v) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None


@dataclass
class _Joiner:
    first: float                    # מתי נראה לראשונה עם ``rejoin`` (שעון monotonic)
    missing0: int
    missing: int
    at: float
    moved: float | None = None      # מתי החוסר ירד לראשונה (X1)
    r5: int = -1                    # הרבע האחרון שנאמר ב-R5
    text: str = ""


@dataclass
class _Session:
    post: dict[tuple[str, str], frozenset[int] | None] = field(default_factory=dict)
    wrote: set[str] = field(default_factory=set)                       # משכפלים שכבר כתבו בגל
    seen: set[str] = field(default_factory=set)                        # serials שנראו בגל
    late: set[str] = field(default_factory=set)                        # serials שהופיעו אחרי שהמשכפל כתב
    queue: dict[str, tuple[float, str]] = field(default_factory=dict)  # V15: serial -> (מתי, mac)
    detached: set[str] = field(default_factory=set)
    pulled: set[str] = field(default_factory=set)
    joiners: dict[str, _Joiner] = field(default_factory=dict)
    big: dict[str, float] = field(default_factory=dict)                # B4: mac -> מתי חסר הרבה
    #: (מחיצה, מעבר) -> מתי השרת ראה אותו מתחיל. ``None`` = הוא כבר רץ כשהשרת ראה אותו לראשונה: לא ידוע
    pass_t0: dict[tuple[int, int], float | None] = field(default_factory=dict)
    engine_key: tuple | None = None                                    # (מחיצה, מעבר, מצב) האחרון שנראה
    skipped: dict[tuple[int, int], dict[str, int]] = field(default_factory=dict)   # B21: (part, round) -> serial -> max
    closed: set[tuple[int, int]] = field(default_factory=set)
    bad: dict[str, list[int]] = field(default_factory=dict)            # B21: serial -> [bad, passes]
    stage: dict[str, str] = field(default_factory=dict)                # R4: השלב האחרון שנרשם
    last: dict[tuple[str, str, str], str] = field(default_factory=dict)
    learned: dict[str, list[float]] | None = None


class Watch:
    """המצב של המוח של המצטרף בשרת אחד. כל המפתחות לפי הגל (``session_id``)."""

    def __init__(self, clock: Callable[[], float] = time.monotonic, wall: Callable[[], float] = time.time):
        self._clock, self._wall = clock, wall
        self._lock = threading.Lock()
        self._sessions: dict[str, _Session] = {}

    def queue(self, session_id: str) -> list[dict]:
        """V15: מי ממתין להצטרף עכשיו, לפי סדר ההגעה, וכמה זמן."""
        now = self._clock()
        with self._lock:
            s = self._sessions.get(session_id)
            items = sorted(s.queue.items(), key=lambda kv: (kv[1][0], kv[1][1], kv[0])) if s else []
        return [{"serial": serial, "mac": mac, "waiting_s": round(now - at, 1)} for serial, (at, mac) in items]

    def observe(self, conn, round_row, mac: str, body: dict, *, mode: str, engine: dict | None = None,
                stream_Bps: int | None = None, budget_bps: int | None = None, floor_mbps: int | None = None) -> None:
        """דיווח התקדמות אחד. לעולם אינו זורק ואינו מחזיר דבר לסוכן."""
        try:
            if mode == OFF:
                return
            with self._lock:
                self._observe(conn, round_row, mac, body, engine, stream_Bps, budget_bps, floor_mbps)
        except Exception:                       # noqa: BLE001 -- מוח שנפל הוא שורת יומן, לא דיווח שנכשל
            log.exception("brain: the joiner decisions for %s failed", mac)

    # --- הדיווח --------------------------------------------------------------------------------------------

    def _observe(self, conn, round_row, mac, body, engine, stream_Bps, budget_bps, floor_mbps) -> None:
        session = body.get("session_id")
        if not isinstance(session, str):
            return
        for old in [k for k in self._sessions if k != session]:
            self._sessions.pop(old)             # גל אחד פעיל בחדר: מה שנשאר מגל קודם אינו של אף אחד
        s = self._sessions.setdefault(session, _Session())
        now = self._clock()
        line = prodline.line_of(conn, round_row["id"]) is not None
        targets = prodline.identities(conn, mac, body.get("targets"))
        boot = _boot(conn, mac)
        if (mac, boot) not in s.post:
            # האתחול נראה כאן לראשונה: המלאי הוא של האתחול רק אם המשכפל עוד לא כתב (אחרת השרת עלה באמצע)
            at_start = mac not in s.wrote and all(t.get("state") not in WROTE for t, _s, _p in targets)
            s.post[(mac, boot)] = _ports(conn, mac) if at_start and boot is not None else None
        self._pass(s, engine, now)
        wrote_before = mac in s.wrote
        for t, serial, port in targets:
            state = t.get("state")
            if serial not in s.seen:
                s.seen.add(serial)
                if wrote_before:
                    s.late.add(serial)
            if serial in s.late:
                self._late(conn, round_row, s, mac, t, serial, port, state, line, now, boot)
            tracked = serial in s.late or serial in s.joiners or serial in s.stage
            self._pulls(conn, round_row, s, t, serial, port)
            self._skipped(s, engine, t, serial)
            need = fill.parse_need(t.get("rejoin"))
            if need is not None:
                self._rejoin(conn, round_row, s, mac, t, serial, port, need, targets, engine, stream_Bps,
                             budget_bps, floor_mbps, now)
            elif serial in s.joiners:
                self._joined(conn, round_row, s, serial, now)
            if tracked or need is not None:
                self._stage(conn, round_row, s, serial, state)
        if any(t.get("state") in WROTE for t, _s, _p in targets):
            s.wrote.add(mac)
        self._close(conn, round_row, s, engine)
        if line and len(s.queue) >= 2:
            names = _names_left(conn, round_row["id"])
            call = J.order([(serial, at, m) for serial, (at, m) in s.queue.items()], names)
            self._say(conn, round_row, s, call, ("round", round_row["id"]))

    def _late(self, conn, round_row, s, mac, t, serial, port, state, line, now, boot) -> None:
        """דיסק שהופיע באמצע הגל: V15 (תור), B25 (החריץ באתחול), ובפס ייצור B1 (קבלה)."""
        if state == "waiting":
            if serial not in s.queue:
                s.queue[serial] = (now, mac)
                self._say(conn, round_row, s, J.Call("V15", "QUEUE", "WAITING", f"דיסק {serial} נכנס בחריץ {port}"
                                                     " וממתין להצטרף", (f"mac={mac}", f"port={port}")),
                          ("disk", serial))
        else:
            s.queue.pop(serial, None)
        post_ports = s.post.get((mac, boot))
        post = J.slot_at_post(port, post_ports)
        self._say(conn, round_row, s, post, ("disk", serial))
        if not line:
            return
        pre = t.get("write_preflight")
        check = pre.get("state") if isinstance(pre, dict) else None
        call = J.accept(serial=serial, smart=_smart(conn, serial),
                        writecheck=check if check in ("ok", "slow", "failed", "unchecked") else None,
                        other_round=_other_round(conn, serial, round_row["wave_session_id"]),
                        post=post.verdict, actual=_actual(conn, round_row["id"], serial))
        self._say(conn, round_row, s, call, ("disk", serial))

    def _pulls(self, conn, round_row, s, t, serial, port) -> None:
        if t.get("detached") is True:
            s.detached.add(serial)
        if t.get("removed") is True and serial not in s.pulled:
            s.pulled.add(serial)
            self._say(conn, round_row, s, J.safe_pull(port, serial in s.detached), ("disk", serial))

    # --- B21: חוסר לפי מעבר ---------------------------------------------------------------------------------

    def _pass(self, s, engine, now) -> None:
        """מתי מעבר התחיל -- רק כשהשרת ראה את המעבר **מתחיל** (המצב הקודם של המנוע היה אחר). מעבר שכבר רץ
        בדיווח הראשון שהשרת ראה (השרת עלה באמצע) נרשם ``None``: הזנב שלו "לא נמדד", לא "כל המעבר"."""
        part, rnd = _engine_pass(engine)
        key = (part, rnd, (engine or {}).get("state"))
        if key == s.engine_key:
            return
        if part is not None and key[2] == "sending" and (part, rnd) not in s.pass_t0:
            s.pass_t0[(part, rnd)] = now if s.engine_key is not None else None
        s.engine_key = key

    def _skipped(self, s, engine, t, serial) -> None:
        part, rnd = _engine_pass(engine)
        skipped = _int(t.get("skipped"))
        if part is None or skipped is None:
            return
        cell = s.skipped.setdefault((part, rnd), {})
        cell[serial] = max(cell.get(serial, 0), skipped)

    def _close(self, conn, round_row, s, engine) -> None:
        """B21: כל מעבר שהמנוע כבר עבר ממנו נסגר פעם אחת -- החוסר של כל מגירה מול השכנות שלה במעבר."""
        now_key = _engine_pass(engine)
        if now_key[0] is None:
            return                              # המנוע לא נקרא: לא יודעים איזה מעבר נגמר
        for key in [k for k in s.skipped if k != now_key and k not in s.closed]:
            s.closed.add(key)
            chunks = _part_chunks(conn, round_row["wave_session_id"], key[0])
            if not chunks:
                continue                        # בלי מספר החתיכות אין "חלק מהמחיצה": לא נמדד, לא נשפט
            shares = {serial: n / chunks for serial, n in s.skipped[key].items()}
            for serial, share in shares.items():
                tally = s.bad.setdefault(serial, [0, 0])
                tally[1] += 1
                tally[0] += J.bad_pass(share, [v for k, v in shares.items() if k != serial])
                call = J.http_after(tally[0], tally[1])
                if call is not None:
                    self._say(conn, round_row, s, call, ("disk", serial))

    # --- המצטרף לשידור -------------------------------------------------------------------------------------

    def _rejoin(self, conn, round_row, s, mac, t, serial, port, need, targets, engine, stream_Bps, budget_bps,
                floor_mbps, now) -> None:
        j = s.joiners.get(serial)
        first = j is None
        if first:
            j = s.joiners[serial] = _Joiner(now, need.missing, need.missing, now)
        elif need.missing < j.missing:
            if j.moved is None:
                j.moved = now
            j.missing, j.at = need.missing, now
        learned = self._learned(conn, s)
        x1 = J.x1(learned["X1"])
        part, rnd = _engine_pass(engine)
        on_air = None if part is None or need.part is None else (
            need.part == part and rnd == 1 and (engine or {}).get("state") == "sending")
        whole = J.pass_s(need.chunks, stream_Bps)
        t0 = s.pass_t0.get((part, rnd)) if part is not None else None
        tail = None if whole is None or t0 is None or not on_air else max(whole - (now - t0), 0.0)
        disk = ("disk", serial)
        if first:
            scope = "ALL" if serial in s.late else "CURRENT"
            short = J.short_part(whole, x1) if on_air else None
            call = short or J.join_now(on_air, tail, x1)
            self._say(conn, round_row, s, J.Call(call.rule, call.action, call.verdict, call.reason,
                                                 (*call.evidence, f"scope={scope}")), disk)
        if need.missing > fill.FEW_MAX_CHUNKS:
            s.big[mac] = now
        s.big = {m: at for m, at in s.big.items() if now - at <= fill.WINDOW_S}
        gap = J.big_gap(need.missing, fill.FEW_MAX_CHUNKS, len(s.big))
        if gap is not None:
            self._say(conn, round_row, s, gap, disk)
        if need.missing:
            self._say(conn, round_row, s, J.catchup(need.missing, J.x17(learned["X17"]), tail), disk)
        siblings = [n.missing for o, o_serial, _p in targets if o_serial != serial
                    for n in [fill.parse_need(o.get("rejoin"))] if n is not None and n.missing > 0]
        call = J.sibling(siblings, budget_bps, fill.CEILING_BPS)
        if call is not None:
            self._say(conn, round_row, s, call, disk)
        skipped = _int(t.get("skipped"))
        taken = need.chunks - need.missing if need.chunks is not None else None
        slow = repair.slow_reason(taken, skipped, stream_Bps / 1e6 if stream_Bps else None, floor_mbps) \
            if taken is not None and taken >= 0 else None
        call = J.late_slow(slow)
        if call is not None:
            self._say(conn, round_row, s, call, disk)
        self._r5(s, mac, serial, port, need, conn)

    def _joined(self, conn, round_row, s, serial, now) -> None:
        """ה-``rejoin`` ירד מהיעד: ההשלמה נגמרה. המדידות (X1, X17) נשמרות לכיול של הבאים."""
        j = s.joiners.pop(serial)
        evidence = [f"missing0={j.missing0}"]
        if j.moved is not None:
            evidence.append(f"join_latency_s={j.moved - j.first:.1f}")
        if j.moved is not None and now > j.moved and j.missing0 > 0:
            evidence.append(f"fill_cps={j.missing0 / (now - j.first):.2f}")
        self._say(conn, round_row, s, J.Call("R4", "STAGE", "FILLED", f"דיסק {serial} השלים את מה שחסר לו",
                                             tuple(evidence)), ("disk", serial), force=True)
        if s.learned is not None:
            for key, name in (("X1", "join_latency_s"), ("X17", "fill_cps")):
                value = _evidence_value(evidence, name)
                if value is not None:
                    s.learned[key].append(value)

    def _r5(self, s, mac, serial, port, need, conn) -> None:
        j = s.joiners[serial]
        quarter = 4 if not need.chunks else min(4, int(4 * (need.chunks - need.missing) / need.chunks))
        j.text = J.completing_text(_drawer(conn, mac, port), need.missing, need.chunks)
        if quarter != j.r5:
            j.r5 = quarter
            self._log({"rule": "R5", "mac": mac, "disk": serial, "missing": need.missing, "chunks": need.chunks,
                       "say": j.text})

    def _stage(self, conn, round_row, s, serial, state) -> None:
        """R4: קריאה חוזרת ונכתב, למצטרפים בלבד (שאר הדיסקים כבר בציר של `restore_stages`)."""
        stage = {"verifying": "READBACK", "done": "DONE", "failed": "FAILED"}.get(state)
        if stage is None or s.stage.get(serial) == stage:
            return
        s.stage[serial] = stage
        self._say(conn, round_row, s, J.Call("R4", "STAGE", stage, f"דיסק {serial}: {_STAGE_HE[stage]}"),
                  ("disk", serial), force=True)

    # --- הרישום --------------------------------------------------------------------------------------------

    def _learned(self, conn, s) -> dict[str, list[float]]:
        if s.learned is None:
            s.learned = {"X1": [], "X17": []}
            rows = memory.decisions(conn, rule="R4", limit=LEARN_LIMIT)
            for row in rows:
                ev = row.get("evidence") or []
                for key, name in (("X1", "join_latency_s"), ("X17", "fill_cps")):
                    value = _evidence_value(ev, name)
                    if value is not None:
                        s.learned[key].append(value)
        return s.learned

    def _say(self, conn, round_row, s, call: J.Call, entity, *, force: bool = False) -> None:
        key = (entity[1], call.rule, call.action)
        # B44: אותו פסק עם תור אחר הוא החלטה אחרת
        sig = f"{call.verdict}|{call.evidence[0]}" if call.rule == "B44" and call.evidence else call.verdict
        if not force and s.last.get(key) == sig:
            return
        s.last[key] = sig
        self._log({"rule": call.rule, "action": call.action, "verdict": call.verdict, "entity": list(entity),
                   "reason": call.reason, "evidence": list(call.evidence)})
        # #3750: R4 הוא אירוע שנצפה (דיסק השלים/נכתב/נכשל), לא פעולה; השאר החלטות צל שהמוח לא מבצע. בשני המקרים אין מה לסגור: n/a
        why = "אירוע שנצפה: אין פעולה לסגור" if call.rule == "R4" else "צל: נרשם בלבד, המוח לא ביצע פעולה"
        memory.record(conn, mode=SHADOW, rule=call.rule, action=call.action, verdict=call.verdict, reason=call.reason,
                      evidence=call.evidence, round_id=round_row["id"], entity=entity, outcome=memory.na(why))

    def _log(self, fields: dict) -> None:
        stamp = datetime.fromtimestamp(self._wall(), timezone.utc).isoformat(timespec="seconds")
        log.info("%s", "brain " + json.dumps({"at": stamp, "mode": SHADOW, **fields,
                                               "shadow": "מחושב ונרשם בלבד (צל)"}, ensure_ascii=False))


_STAGE_HE = {"READBACK": "קריאה חוזרת", "DONE": "נכתב", "FAILED": "נכשל"}


# --- קריאות מה-DB: כל כשל = "לא נמדד" ----------------------------------------------------------------------


def _safe(fn):
    def wrapped(*a, **kw):
        try:
            return fn(*a, **kw)
        except Exception:                       # noqa: BLE001
            log.exception("brain: %s could not be read", fn.__name__)
            return None
    return wrapped


@_safe
def _boot(conn, mac: str) -> str | None:
    row = conn.execute("SELECT boot_id FROM net_devices WHERE mac = ?", (mac,)).fetchone()
    return row["boot_id"] if row is not None else None


@_safe
def _ports(conn, mac: str) -> frozenset[int] | None:
    row = conn.execute("SELECT disks_json FROM net_devices WHERE mac = ?", (mac,)).fetchone()
    disks = json.loads(row["disks_json"]) if row is not None and row["disks_json"] else None
    if not isinstance(disks, list):
        return None
    return frozenset(p for d in disks if isinstance(d, dict) for p in [_int(d.get("port"))] if p is not None)


@_safe
def _smart(conn, serial: str) -> str | None:
    row = conn.execute("SELECT verdict FROM disk_events WHERE serial = ? AND verdict != 'unchecked'"
                       " ORDER BY updated_at DESC, rowid DESC LIMIT 1", (serial,)).fetchone()
    return row["verdict"] if row is not None else None


def _other_round(conn, serial: str, session: str) -> str | bool | None:
    try:
        rows = conn.execute("SELECT s.id, m.targets_json FROM session_members m JOIN sessions s ON s.id = m.session_id"
                            " WHERE s.state IN ('open', 'running') AND s.id != ? AND m.targets_json LIKE ?",
                            (session, f'%"{serial}"%')).fetchall()
    except Exception:                           # noqa: BLE001
        log.exception("brain: the other rounds of %s could not be read", serial)
        return None
    for row in rows:
        try:
            found = any(isinstance(x, dict) and x.get("serial") == serial for x in json.loads(row["targets_json"]))
        except (TypeError, ValueError):
            return None
        if found:
            return row["id"]
    return False


@_safe
def _actual(conn, round_id: str, serial: str) -> str:
    row = conn.execute("SELECT state FROM prodline_disks WHERE round_id = ? AND serial = ?",
                       (round_id, serial)).fetchone()
    return f"joined:{row['state']}" if row is not None else "not_joined"


@_safe
def _names_left(conn, round_id: str) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM prodline_disks WHERE round_id = ? AND state IN"
                        f" ({','.join('?' * len(prodline.FREE))})", (round_id, *prodline.FREE)).fetchone()[0]


@_safe
def _part_chunks(conn, session: str, part: int) -> int | None:
    row = conn.execute("SELECT MAX(chunks) FROM chunk_ledger WHERE round_id = ? AND part = ?",
                       (session, part)).fetchone()
    return _int(row[0]) if row is not None else None


@_safe
def _drawer(conn, mac: str, port: int) -> int | None:
    row = conn.execute("SELECT drawer_count, drawer_slots FROM machines WHERE mac = ?", (mac,)).fetchone()
    if row is None:
        return None
    model_map = model_slots.for_mac(conn, mac)
    slots = drawer_slots.effective(row["drawer_count"], row["drawer_slots"], model_map["slots"] if model_map else None)
    return drawer_slots.drawer_of(slots, {"port": port})


def _engine_pass(engine: dict | None) -> tuple[int | None, int | None]:
    if not isinstance(engine, dict):
        return None, None
    return _int(engine.get("partition")), _int(engine.get("round"))


def _evidence_value(evidence, name: str) -> float | None:
    for item in evidence if isinstance(evidence, list) else []:
        if isinstance(item, str) and item.startswith(name + "="):
            try:
                return float(item.split("=", 1)[1])
            except ValueError:
                return None
    return None


# --- R4: ציר הזמן --------------------------------------------------------------------------------------------


def timeline(conn, serial: str, round_id: str | None = None) -> dict:
    """R4: ציר הזמן של דיסק אחד בסבב -- מה שהמוח רשם עליו (`memory.decisions`) ושורות פס הייצור (``prodline``),
    לפי הזמן. בלי ``round_id``: הסבב האחרון שבו המוח רשם משהו על הדיסק. ``None`` בשדה = לא נרשם."""
    rows = memory.decisions(conn, kind="disk", key=serial, round_id=round_id, limit=200)
    if round_id is None and rows:
        round_id = rows[0]["round_id"]           # החדשה ראשונה: הסבב האחרון
        rows = [r for r in rows if r["round_id"] == round_id]
    rows.reverse()                               # לפי סדר הרישום, כך שבאותה שנייה הסדר נשמר
    events = [{"at": r["at"], "source": "brain", "rule": r["rule"], "action": r["action"], "verdict": r["verdict"],
               "text": r["reason"]} for r in rows]
    if round_id is not None:
        try:
            for r in conn.execute("SELECT ts, detail FROM journal WHERE event = 'prodline' AND round_id = ?"
                                  " AND detail LIKE ?", (round_id, f"%serial={serial}%")):
                words = str(r["detail"]).split()
                events.append({"at": r["ts"], "source": "prodline", "rule": None, "action": None,
                               "verdict": words[1] if len(words) > 1 else None, "text": r["detail"]})
        except Exception:                       # noqa: BLE001
            log.exception("brain: the production-line rows of %s could not be read", one_line(serial))
            events.append({"at": None, "source": "prodline", "rule": None, "action": None, "verdict": None,
                           "text": "שורות פס הייצור לא נקראו"})
    events.sort(key=lambda e: (e["at"] or "", e["source"]))
    return {"serial": serial, "round_id": round_id, "events": events}
