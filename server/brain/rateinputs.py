"""#3977: two inputs of the wave rate (K8) that the removed sender had and the chunk engine did not get (#3173).

The AIMD of `chunk_rate.adjust` moves the rate by the loss of the first pass, the rate the engine pushed and the time it
slept. It never read what each cloner made durable, and a cloner the server lost in a first pass only made that wave
teach nothing -- the next one could climb to the same rate again. Both are rules of K8, and both can only HOLD or LOWER
what the AIMD gave -- never raise it, never under the floor of `choose`.

**The switch** (`mode`, the setting `brain_rate_inputs`; Nadav 21/11: "צל 3 פעמים ואז בלי צל פעם אחת ולבדוק עם Jev אם היה
נכון"). The words are the veto's. ``shadow`` -- **the default** -- every verdict is a row in `brain_decisions` with the rate
the wave WOULD have gone out at (`would_bps`) next to the AIMD's (`aimd_bps`), and the rate that goes out is the AIMD's,
exactly. ``enforce`` -- the verdict moves the rate. ``off`` -- nothing is read, decided or remembered. A fall is remembered
in ``shadow`` as in ``enforce``: it is an observation, and the falls of the shadow waves are the wall of the first wave
that enforces.

**1. Goodput of each cloner** (`goodput_of`, `peak`). What a cloner's drawers made durable in the first passes, over the
seconds of those passes, per drawer -- #1758's definition, in the unit of the rate asked (compressed bytes of the stream).
Before a climb (`chunk_up`), the rates that were tried are compared: a higher rate replaces a lower one only when one
cloner of the wave going out made more than `GOODPUT_NOISE` more durable at it. A rate that was tried above the best and
was not faster: the wave goes out at the best (`chunk_peak`) -- the same time at a higher rate is no reason for more load.
A point counts only when it is evidence about THIS wave: the wave taught, it multicast the same stream (`stream_zbytes`:
goodput in wire units is comparable only over the same image), it ran at the rate it asked (`drive_rates.KEEP_UP`), and
every cloner of the wave going out was measured in it with as many drawers. A point leaves the window after
`GOODPUT_WINDOW` waves that taught, and the rate above is then tried again.

**2. The rate a cloner fell at** (`remember_drops`, `wall_of`, `unsafe`). A cloner the server stopped counting as alive
in a first pass (`rate_outcome.away_macs`) is remembered with the rate the wave asked: a row of the machine in
`brain_decisions`, which outlives the wave. One fall can be a cable: under `UNSAFE_MIN_DROPS` falls there is no wall.
With them, the wall is the second lowest rate of the falls (it stands when one fall was not the rate's), and a wave with
that cloner does not land on it or above it (`chunk_unsafe`). A fall is refuted by a clean wave that taught, with the
cloner in it, at that rate or above, after it; and after `reprobe` clean waves newer than the newest fall the wall is
spent and the loop climbs again -- a fall there is a new fall (#3520: a proof is not a wave's word for ever).
The cloner is never left out of a wave (Nadav 16/11: the brain suggests, a person disqualifies): only the climb stops.

**Not measured is no decision (principle 5).** A wave without `drives`, a cloner that was not measured, one rate tried, a
memory that was not read: `UNDECIDED`, and the rate is exactly what it would have been without this module.

The thresholds are opening constants of the code, NOT settings (Nadav 14/11), each with a row in `catalog.toml`. None was
measured on the chunk engine: the removed sender's, or constants the brain already uses -- estimates, untested-on-metal.
Nothing here raises into a wave.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from server.brain import memory
from server.db import get_setting
from server.rounds import drive_rates

log = logging.getLogger("imagesrv.brain")

#: `brain_decisions.rule`: the rate of a wave (`catalog.toml`, K8) -- `chunk_rate.RULE`
RULE = "K8"
GOODPUT_ACTION, DROP_ACTION, UNSAFE_ACTION = "WAVE_GOODPUT", "CLONER_DROP", "WAVE_UNSAFE_RATE"
PEAK, CLIMB, UNDECIDED = "PEAK", "CLIMB", "UNDECIDED"
DROPPED, WALL, NO_WALL = "DROPPED", "WALL", "NO_WALL"

#: The switch of the two inputs (`mode`): a setting of the console's API (`POST /api/console/settings`), with no row on the
#: settings screen. The words are `veto.MODES`; a value that is missing, unknown or was not read is ``shadow``.
SETTING = "brain_rate_inputs"
LABEL_HE = "המוח: קצב הגל לפי מה שהמשכפלים כתבו ולפי קצב שמשכפל נפל בו"
OFF, SHADOW, ENFORCE = "off", "shadow", "enforce"
DEFAULT = SHADOW
MODES_HE = {OFF: "כבוי", SHADOW: "צל (רושם בלבד)", ENFORCE: "פעיל (משנה את הקצב)"}

#: #1758: a higher rate is "faster" only when goodput rises by more than this share. The removed sender's, measured on ITS
#: goodput (metal 18/10, cloner 2, 19.5GB: the efficiency of three waves at close rates was 92.7% / 90.9% / 90.0%, a
#: spread of 2.7 points) -- for the chunk engine a starting point, not a measurement (untested-on-metal).
GOODPUT_NOISE = 0.03
#: #1758: how many of the newest waves that taught are compared. A rate that was not faster is forgotten after them and
#: tried again. The removed sender's (10 waves: ~1.5% on average for re-trying a peak) -- an estimate for the chunk engine.
GOODPUT_WINDOW = 10
#: how many falls of a cloner that nothing refuted make a wall. `drive_rates.MIN_HELD_SAMPLES`: one sample -- here a
#: pulled cable -- is not a property; two are a pattern. An estimate: the removed sender needed one, but its signal
#: (`Dropping client`) was the rate's by construction, and `away` is a disconnect of any cause (#3141).
UNSAFE_MIN_DROPS = drive_rates.MIN_HELD_SAMPLES
#: how far back a fall counts: the days `brain_decisions` keeps every row (`housekeeping._rules`), the window `recall`
#: reads (`decide.Config.max_age_s`). An estimate.
UNSAFE_AGE_DAYS = 90
#: how many decisions of one machine are read
LIMIT = 500


def setting(conn) -> str:
    """The switch as it is stored -- what the console's API shows. Missing or unknown = ``shadow``."""
    raw = get_setting(conn, SETTING)
    return raw if raw in (OFF, SHADOW, ENFORCE) else DEFAULT


def mode(conn) -> str:
    """``off`` / ``shadow`` / ``enforce`` of the two inputs, as a wave reads it. `brain_veto_mode=off` turns them off like
    every rule of the brain ("as before the brain"). A switch that was not read = ``shadow``: recorded, and the rate
    does not move."""
    try:
        from server.brain import veto            # noqa: PLC0415 -- like `chunk_rate._server_cap`: the brain's switch is the veto's
        return OFF if veto.mode(conn) == veto.OFF else setting(conn)
    except Exception:                       # noqa: BLE001 -- the switch never stops a wave
        log.exception("brain: %s unreadable, using %s", SETTING, DEFAULT)
        return DEFAULT


def _count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0


def _epoch(raw: object) -> float | None:
    try:
        return datetime.fromisoformat(raw).timestamp() if isinstance(raw, str) and raw else None
    except ValueError:
        return None


# --- 1. goodput ---------------------------------------------------------------------------------------------------


def goodput_of(drives: object, seconds: object) -> dict | None:
    """`{mac: {"bps", "drives"}}` -- what each cloner made durable in the first passes (`rate_outcome.drives`: compressed
    bytes per drawer), in bits per second per drawer that was measured, and how many drawers that is. A drawer that was
    not measured (None) is not counted; a cloner with none is not in the answer. None = nothing was measured: no
    `drives`, no seconds. Never 0 for "not measured"."""
    if not isinstance(drives, dict) or not _number(seconds) or seconds <= 0:
        return None
    out = {}
    for mac, got in drives.items():
        took = [n for n in got.values() if _count(n)] if isinstance(got, dict) else []
        if took:
            out[mac] = {"bps": int(sum(took) * 8 / seconds / len(took)), "drives": len(took)}
    return out or None


def _mine(point: dict, macs: dict[str, int]) -> dict[str, int] | None:
    """{mac: bps} of the cloners of the wave going out in one past wave, or None: one of them was not measured in it, or
    had another number of drawers there."""
    got = point.get("goodput")
    if not isinstance(got, dict) or not macs:
        return None
    out = {}
    for mac, drawers in macs.items():
        one = got.get(mac)
        if not (isinstance(one, dict) and _count(one.get("bps")) and one.get("drives") == drawers):
            return None
        out[mac] = one["bps"]
    return out


def peak(taught: list[dict], macs: dict[str, int], noise: float = GOODPUT_NOISE, window: int = GOODPUT_WINDOW) -> dict:
    """The peak search of the wave going out -> `{"verdict", "why", "rate", "tried"}`. Pure.

    `taught`: the waves that taught, newest first (`chunk_rate._taught`: the sample of each, with `session_id`); the
    first is the wave the AIMD stands on. `macs`: {mac: drawers} of the wave going out.

    ``PEAK`` (`rate` = the best rate): a rate above the best was tried and no cloner was faster at it.
    ``CLIMB``: the highest rate tried is the best -- the climb paid, the AIMD goes on.
    ``UNDECIDED``: the last wave is not a point (`last_wave_not_measured`), or only one rate was tried (`one_rate_tried`).
    `tried`: the points compared, lowest rate first -- the evidence."""
    answer = {"verdict": UNDECIDED, "why": "last_wave_not_measured", "rate": None, "tried": []}
    if not taught:
        return answer
    stream = taught[0].get("stream_zbytes")
    newest: dict[int, dict] = {}
    for i, wave in enumerate(taught[:window]):
        asked, sent = wave.get("asked_bps"), wave.get("sent_bps")
        got = _mine(wave, macs)
        usable = (got is not None and _count(stream) and stream > 0 and wave.get("stream_zbytes") == stream
                  and _count(asked) and asked > 0 and _count(sent) and sent >= drive_rates.KEEP_UP * asked)
        if not usable:
            if i == 0:
                return answer
            continue
        newest.setdefault(asked, {"session_id": wave.get("session_id"), "rate": asked, "bps": got})
    tried = sorted(newest.values(), key=lambda p: p["rate"])
    answer["tried"] = tried
    if len(tried) < 2:
        return {**answer, "why": "one_rate_tried"}
    best = tried[0]
    for point in tried[1:]:
        if any(point["bps"][mac] > best["bps"][mac] * (1 + noise) for mac in macs):
            best = point
    if tried[-1]["rate"] > best["rate"]:
        return {**answer, "verdict": PEAK, "why": "higher_rate_not_faster", "rate": best["rate"]}
    return {**answer, "verdict": CLIMB, "why": "goodput_rose", "rate": best["rate"]}


# --- 2. the rate a cloner fell at ---------------------------------------------------------------------------------


def remember_drops(conn, session_id: str, asked_bps: int, macs: list[str], mode_: str) -> None:
    """The cloners the server lost in a first pass of the wave, each with the rate the wave asked: one row a machine in
    `brain_decisions` (K8, `CLONER_DROP`). `mode_`: ``shadow`` or ``enforce`` (`mode`) -- the row says which, and
    `drops_of` reads both: a fall is what happened, whatever the switch was. Never raises (`memory.record` does not)."""
    for mac in macs:
        memory.record(conn, mode=mode_, rule=RULE, point="#3977", action=DROP_ACTION, verdict=DROPPED,
                      reason=f"השרת איבד את המשכפל במעבר ראשון של גל שיצא ב-{asked_bps // 10 ** 6}Mb/s",
                      evidence=[{"asked_bps": asked_bps}], round_id=session_id, entity=("machine", mac),
                      outcome="נרשם בזיכרון של המשכפל")


def drops_of(conn, mac: str, now: float) -> list[tuple[int, float]] | None:
    """[(the rate asked, when)] of the falls of one cloner in the last `UNSAFE_AGE_DAYS`, through `memory.decisions` --
    the one reading point of the memory. None = the memory was not read (not "no falls")."""
    since = datetime.fromtimestamp(now - UNSAFE_AGE_DAYS * 86400, timezone.utc).isoformat(timespec="seconds")
    try:
        rows = memory.decisions(conn, kind="machine", key=mac, rule=RULE, since=since, limit=LIMIT)
    except Exception:                       # noqa: BLE001 -- the memory never stops a wave
        log.exception("brain: the falls of %s were not read", mac)
        return None
    out = []
    for row in rows:
        if row.get("action") != DROP_ACTION or row.get("verdict") != DROPPED:
            continue
        evidence = row.get("evidence")
        rate = evidence[0].get("asked_bps") if evidence and isinstance(evidence[0], dict) else None
        at = _epoch(row.get("at"))
        if _count(rate) and rate > 0 and at is not None:
            out.append((rate, at))
    return out


def wall_of(drops: list[tuple[int, float]], cleans: list[tuple[int, float]], reprobe: int,
            min_drops: int = UNSAFE_MIN_DROPS) -> tuple[int | None, str]:
    """(the rate a cloner must stay under, why) from its falls and the clean waves it was in -- both `[(rate, when)]`. Pure.

    ``no_drop`` / ``refuted`` (every fall has a clean wave at its rate or above after it) / ``few_drops`` (under
    `min_drops` falls stand) / ``spent`` (`reprobe` clean waves after the newest fall) -> no wall. ``wall``: the
    `min_drops`-th lowest rate of the falls that stand."""
    if not drops:
        return None, "no_drop"
    stand = [(rate, at) for rate, at in drops if not any(r >= rate and t > at for r, t in cleans)]
    if not stand:
        return None, "refuted"
    if len(stand) < min_drops:
        return None, "few_drops"
    newest = max(at for _rate, at in stand)
    if sum(1 for _r, t in cleans if t > newest) >= reprobe:
        return None, "spent"
    return sorted(rate for rate, _at in stand)[min_drops - 1], "wall"


def unsafe(conn, macs: list[str], cleans: dict[str, list[tuple[int, float]]], now: float, reprobe: int) -> dict | None:
    """The wall of the wave going out -> `{"verdict", "why", "rate", "mac", "cloners"}`, or None: no cloner of the wave
    has a fall in memory (nothing to decide, and no row). `cleans`: {mac: [(rate, when)]} of the clean waves that taught.

    ``WALL``: `rate` is the lowest wall of the wave's cloners, `mac` whose. ``NO_WALL``: falls are remembered and none
    is a wall. ``UNDECIDED``: the memory of a cloner was not read, and no wall was found in what was -- not "no wall"."""
    cloners, walls, unread = {}, [], []
    for mac in macs:
        drops = drops_of(conn, mac, now)
        if drops is None:
            unread.append(mac)
            continue
        if not drops:
            continue
        rate, why = wall_of(drops, cleans.get(mac, []), reprobe)
        cloners[mac] = {"drops": sorted(r for r, _at in drops), "why": why, "wall": rate}
        if rate is not None:
            walls.append((rate, mac))
    if walls:
        rate, mac = min(walls)
        return {"verdict": WALL, "why": "wall", "rate": rate, "mac": mac, "cloners": cloners, "unread": unread}
    if unread:
        return {"verdict": UNDECIDED, "why": "memory_unread", "rate": None, "mac": None, "cloners": cloners, "unread": unread}
    if not cloners:
        return None
    return {"verdict": NO_WALL, "why": ",".join(sorted({c["why"] for c in cloners.values()})), "rate": None, "mac": None,
            "cloners": cloners, "unread": unread}
