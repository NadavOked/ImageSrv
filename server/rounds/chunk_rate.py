"""Network-rate learning for waves of the chunk engine (#3163, #3174).

The sender that was removed (#3173) learned its rate from its own counters. The chunk engine
(`chunksend.py`) multicasts at a constant rate and completes what is missing afterwards, so it has no
retransmission counter. This module learns the rate of the multicast stream (`--kbps`) from what the engine
itself measures at the end of a wave, in the AIMD shape the brain's network budget uses (`brain/budget.py`, K7):

    record  -- at the end of every wave: the rate asked, the rate the engine pushed, the chunks that did not
               arrive in the first pass, the number of cloners. Stored in `wave_rates.stream["chunk"]`.
    adjust  -- before the next wave: the rate asked by the last wave that TAUGHT, one step up if it was clean,
               cut if it lost chunks, the same if it proved nothing. Inside the network budget, never above.

A wave teaches only when its first pass shows the rate and nothing else:

    - it ended in `send_done` (a failed or stopped wave proves no rate);
    - no machine was lost while the server waited for the first pass (`chunk_net_gap` because of a
      disconnect, #3141: the repair that follows is the cable's time, not the rate's);
    - the library disk did not hold the stream back (`server_io.bound`, #1884);
    - the first pass was long and big enough to measure (`drive_rates.MIN_SAMPLE_S`, `MIN_SAMPLE_BYTES`).

Loss is the chunks that did not arrive in the first pass and that the drive's own `skipped` does not explain
(`repair.Plan.lost_in_net`); the worst machine counts. Not measured (an agent that did not report `skipped`)
is not "none lost": the rate then holds. #3164's metric, when it exists, goes into `lost_chunks` of
`ChunkEngine.rate_outcome` and nothing here changes.

No history means today's rate (`drive_rates.choose`): `adjust` returns what it was given. Learning switched off
(`learned_rate_enabled`) records nothing and changes nothing -- not the mistake of #3046.

The thresholds are opening constants of the code, not settings (Nadav 14/11): the removed sender's (`LOSS_DOWN_RATIO`,
`STEP_UP`, `DOWN_FACTOR`), measured on its counters -- for the chunk engine a starting point, not a
measurement (untested-on-metal). Never above the network budget: the lowest of the wave's ceiling (card, slowest
cloner, `IMAGESRV_MAX_BITRATE`) and the brain's ceiling (`brain_net_ceiling_mbps`) minus what HTTP keeps.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import TypeGuard

from server.brain import budget, memory, netcap, rateinputs, receiverplan, wavesense, writerate
from server.db import _write_lock, journal, SqlConnection, writing
from server.learn import server_cap, server_io
from server.rounds import drive_rates

log = logging.getLogger("imagesrv.chunk_rate")

#: where a wave's observation lives: `wave_rates.stream[STREAM_KEY]`
STREAM_KEY = "chunk"

#: the AIMD of the wave rate: the loss that cuts (permille), the step up (Mbps), the rate after a cut (percent of the
#: last). Opening constants, NOT settings (Nadav 14/11: technical thresholds are the brain's, which may calibrate them
#: from its memory later). They are the removed sender's (`LOSS_DOWN_RATIO`, `STEP_UP`, `DOWN_FACTOR`), measured on its
#: counters -- for the chunk engine a starting point, not a measurement (untested-on-metal).
LOSS_PERMILLE = round(drive_rates.LOSS_DOWN_RATIO * 1000)
STEP_MBPS = drive_rates.STEP_UP // 10 ** 6
CUT_PERCENT = round(drive_rates.DOWN_FACTOR * 100)
#: #3269: when a learned start is old (hours), how far a fresh write check may be from the one it was learned with
#: (percent). Opening constants, NOT settings (Nadav 14/11: technical thresholds are the brain's, which may calibrate them from its
#: memory later). Reasoned guesses, not measurements (untested-on-metal): the spiral of 13-14/11 was a day old, its
#: drive memory 21MB/s against a write check of 179-209MB/s (-88%), and its server at avg10=46%.
STALE_HOURS = 12
PROBE_DIFF_PERCENT = 50
#: Maximum upward retry without a network measurement. Reuse the AIMD step as an initial estimate.
PROBE_RAISE_MBPS = STEP_MBPS
#: #3498: how many of the newest waves after the one that cut the rate must ALL show that the library disk, not the rate,
#: was the limit, for the cut to be dropped (`server_release`). One wave can be a pulse of the disk (14/11: one busy
#: wave); two in a row are a pattern. Opening constant, NOT a setting (Nadav 14/11) -- a reasoned guess, untested-on-metal.
RELEASE_WAVES = 2
#: #3506: the closed loop of `--write-kbps` (`write_rate`). The step up (percent of the rate the last wave went out at), the margin
#: under what a drive was measured to write when it fell behind (`writerate.MARGIN`, the same 10% the write check gets), and
#: the share of the first pass the sender must have slept for `--write-kbps` for the cap to be what held it (`paced.disk`):
#: `100 - CUT_PERCENT`, the share of #3498. Opening constants, NOT settings (Nadav 14/11) -- reasoned guesses, untested-on-metal.
WRITE_STEP_PERCENT = 10
WRITE_HELD_PERCENT = 100 - CUT_PERCENT
#: #3520 (metal 16/11, round 11): what a drive was "proven" to write (`sustained`) is no longer a wave's word for ever. (1) A wave teaches
#: it only when its own loss was measured and not above `LOSS_PERMILLE` -- the loss that cuts the wave rate: over it, the bytes the drive
#: made durable are short because the chunks did not arrive (ses_7516fee9: an unbounded sender, 32% lost, "the drive wrote 175MB/s"),
#: not because the drive was slow. (2) After `REPROBE_WAVES` waves newer than the proof in which the drive kept up at the ceiling or above,
#: the proof is spent and the loop climbs from there: if the drive falls behind again, that wave is the new proof. One more than
#: `RELEASE_WAVES` ("two in a row are a pattern"): a ceiling comes from a measurement, so it asks for more than the dropping of a cut.
#: Opening constant, NOT a setting (Nadav 14/11) -- a reasoned guess, untested-on-metal.
REPROBE_WAVES = RELEASE_WAVES + 1
#: #3526 (metal 16/11, r12, one drive, six waves): a drive the receiver skipped chunks for ("the drive was behind") is not a slow drive.
#: Skipped chunks are completed over HTTP; 4 of 6 waves at 118-173MB/s skipped none, b1 skipped 3 of ~18,250 chunks of the pass (0.16 permille),
#: a1 skipped 25 of ~18,250 (1.37 permille) -- and each of the two cost the next wave a flat 25%. Under `BEHIND_PERMILLE` of the chunks of the
#: passes the drive was behind in the lag is a `HOLD` (`behind_negligible`); from it the cut is graded by the share (`_lag`). The threshold is
#: `LOSS_PERMILLE`, the share the loop already takes as "enough chunks missing from the stream to cut the rate": a chunk skipped for a drive is
#: the same kind of gap in the same stream, and it puts the two numbers of the metal on opposite sides (3 below, 25 above). Opening constant,
#: NOT a setting (Nadav 14/11) -- a reasoned guess, untested-on-metal. `CHUNK_BYTES` is `chunksend.CHUNK_BYTES` (this module does not import
#: the engine; a test pins the two).
BEHIND_PERMILLE = LOSS_PERMILLE
CHUNK_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class Config:
    loss_permille: int = LOSS_PERMILLE
    step_bps: int = STEP_MBPS * 10 ** 6
    cut_percent: int = CUT_PERCENT
    stale_hours: int = STALE_HOURS
    probe_diff_percent: int = PROBE_DIFF_PERCENT
    release_waves: int = RELEASE_WAVES
    write_step_percent: int = WRITE_STEP_PERCENT
    write_margin: float = writerate.MARGIN
    write_held_percent: int = WRITE_HELD_PERCENT
    behind_permille: int = BEHIND_PERMILLE
    probe_raise_bps: int = PROBE_RAISE_MBPS * 10 ** 6


def config(conn: SqlConnection) -> Config:
    """The thresholds in force: the opening constants, read when called (the brain's hook to calibrate them; `conn`
    is where it will read its memory). Not settings -- Nadav 14/11."""
    del conn
    return Config(loss_permille=LOSS_PERMILLE, step_bps=STEP_MBPS * 10 ** 6, cut_percent=CUT_PERCENT,
                  stale_hours=STALE_HOURS, probe_diff_percent=PROBE_DIFF_PERCENT,
                  probe_raise_bps=PROBE_RAISE_MBPS * 10 ** 6,
                  release_waves=RELEASE_WAVES, write_step_percent=WRITE_STEP_PERCENT, write_margin=writerate.MARGIN,
                  write_held_percent=WRITE_HELD_PERCENT, behind_permille=BEHIND_PERMILLE)


# --- the pure part ------------------------------------------------------------------------------------------------


def _count(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _number(value: object) -> TypeGuard[int | float]:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0


def _fmt(bits: int) -> str:
    return f"{max(1, bits // 10 ** 6)}m"


def sample_of(asked: int | None, outcome: dict) -> dict:
    """The observation of one wave, from the rate it asked and `ChunkEngine.rate_outcome`.

    ``sent_bps`` is what the engine put on the wire in its first passes: ``stream_zbytes`` (#3302: the compressed
    bytes of the index's frames -- what `chunkcast --kbps` paces, the unit of ``asked_bps``) over the seconds, and
    None when the engine did not know the sizes -- not ``bytes`` (chunks x 4MiB, before compression): over it
    ses_43eebdbc (LabRoot 14/11) logged sent=494Mb/s at asked=150m, and nearly every wave "kept up". ``achieved_bps``
    is that times the share that arrived, and only when the loss was measured; ``loss_permille`` is None when it
    was not. #3296: ``stream_zbytes`` and ``drives`` pass through as the engine gave them -- what the drive memory
    learns from (`drive_rates.record_wave`); None = not measured. #3977: ``goodput`` -- what each cloner made durable
    in the first passes, per drawer and second (`rateinputs.goodput_of`; None = not measured), and ``away_macs`` --
    the cloners the server lost in a first pass (None = the engine did not say who)."""
    away = outcome.get("away_macs")
    paced = outcome.get("paced_ms")        # #3498: how long the sender slept for --kbps / --write-kbps; None = not measured
    sends = outcome.get("sendstats")       # #3553: how long the sender waited for the library file (`chunklog.sendstats_of`); None = not measured
    wrote, serials = outcome.get("write_kbps"), outcome.get("write_serials")    # #3506: --write-kbps and whose check set it
    chunks, lost = outcome.get("chunks"), outcome.get("lost_chunks")
    seconds, size, wire = outcome.get("first_pass_s"), outcome.get("bytes"), outcome.get("stream_zbytes")
    loss = 1000 * lost / chunks if _count(lost) and _count(chunks) and chunks > 0 else None
    sent = int(wire * 8 / seconds) if _count(wire) and _number(seconds) and seconds > 0 else None
    return {"asked_bps": asked, "sent_bps": sent,
            "achieved_bps": None if sent is None or loss is None else int(sent * (1 - loss / 1000)),
            "loss_permille": loss, "lost_chunks": lost if _count(lost) else None,
            "chunks": chunks if _count(chunks) else None,
            "first_pass_s": seconds if _number(seconds) else None,
            "bytes": size if _count(size) else None,
            "cloners": outcome.get("cloners") if _count(outcome.get("cloners")) else None,
            "repair_rounds": outcome.get("repair_rounds") if _count(outcome.get("repair_rounds")) else None,
            # #3978: what the repair rounds sent again, and the machines lost during a first pass -- `/room.round.network`
            # after the wave (`room_stream`)
            "multicast_chunks": outcome.get("multicast_chunks") if _count(outcome.get("multicast_chunks")) else None,
            "fetched_chunks": outcome.get("fetched_chunks") if _count(outcome.get("fetched_chunks")) else None,
            "away": outcome.get("away") if _count(outcome.get("away")) else None,
            "stream_zbytes": wire if _count(wire) else None,
            "drives": outcome.get("drives") if isinstance(outcome.get("drives"), dict) else None,
            "goodput": rateinputs.goodput_of(outcome.get("drives"), seconds),
            "away_macs": sorted(away) if isinstance(away, list) and all(isinstance(m, str) for m in away) else None,
            "behind": outcome.get("behind") if isinstance(outcome.get("behind"), dict) else None,
            "paced_ms": paced if isinstance(paced, dict) and _count(paced.get("wire")) and _count(paced.get("disk")) else None,
            "sendstats": sends if isinstance(sends, dict) and _count(sends.get("read_ms")) and sends.get("reader") in ("inline", "thread")
            and (sends.get("readq_empty_ms") is None or _count(sends["readq_empty_ms"])) else None,   # #3553
            "write_kbps": wrote if _count(wrote) and wrote > 0 else None,
            "write_serials": (sorted(serials) if isinstance(serials, list) and serials
                              and all(isinstance(x, str) for x in serials) else None),
            "ended": outcome.get("ended")}


def next_rate(last: dict, cloners: int, cfg: Config) -> tuple[int, str]:
    """(the rate after a wave that taught, the basis) -- AIMD.

    Lost more than the threshold: cut. Lost nothing worth a cut, the engine kept its pace (`KEEP_UP`) and the
    loss was measured: one step up -- but not for more cloners than the wave proved it for (a rate that two
    machines took is not proof for six). Anything else holds."""
    asked, loss, sent = last["asked_bps"], last.get("loss_permille"), last.get("sent_bps")
    if loss is not None and loss > cfg.loss_permille:
        return int(asked * cfg.cut_percent / 100), "chunk_down"
    if loss is None or sent is None or sent < drive_rates.KEEP_UP * asked:
        return asked, "chunk_hold"
    if cloners > (last.get("cloners") or 0):
        return asked, "chunk_hold"
    return asked + cfg.step_bps, "chunk_up"


def cap_of(conn: SqlConnection, chosen: dict) -> tuple[int, str]:
    """(the most a chunk wave may ask, who sets it). The wave's own ceiling (`choose`: card, slowest cloner,
    `IMAGESRV_MAX_BITRATE`), and the brain's ceiling for the distribution network minus what HTTP keeps
    (`budget.FLOOR_BPS`; half of a very small ceiling), whichever is lower."""
    ceiling = drive_rates.parse_bitrate(chosen.get("ceiling"))
    total, _source = budget.ceiling_bps(conn, chosen.get("link"))
    room = max(total - budget.FLOOR_BPS, total // 2)
    if ceiling is not None and ceiling <= room:
        return ceiling, "ceiling"
    return room, "budget"


def server_release(conn: SqlConnection, last: dict, cfg: Config) -> dict:
    """#3498: does the cut of `last` (the newest wave that TAUGHT, `chunk_down`) still stand on evidence? The answer is
    `{"verdict", "why", "asked_bps", "waves"}`; ``RELEASE`` only when BOTH hold for each of the newest `release_waves`
    waves after `last` (every one of them taught nothing, so `last_sample` keeps returning the cut):

    1. the limit that was measured is the library disk -- `skip == "server_disk"` (`server_io.bound`), not a lost
       machine (`chunk_net_gap`), a failed wave or a short one, which are the network's or nobody's -- AND the sender
       itself was not held by something else: it slept for its own pacing (`--kbps`, `--write-kbps`: `paced_ms`) for no more
       than `100 - cut_percent` percent of the first pass. The label alone is not trusted: on LabRoot (15/11) the new
       engine's waves were labelled `server_disk` (util 30-34%, await 15.5ms over the whole wave) while the library disk
       was idle 64% of the stream and the sender slept 74% of it, in `hrtimer_nanosleep` -- held by `--write-kbps`;
    2. what the engine actually sent (`sent_bps`) is far from the ceiling of the wave (`asked_bps`): under `cut_percent`
       of it. The threshold is relative to the measurement and made of the rule's own constant: a cut to `cut_percent`
       of the rate asked cannot change a stream that does not even reach that share of what it was asked -- the stream
       is not held by the rate, so the cut proves nothing now. On LabRoot (15/11) the waves asked 669m and sent ~312Mb/s.

    What is still missing, and is not guessed: the sender's own count of the time it WAITED for the read (it counts only its
    sleeps for the two rates, `paced`). "Not asleep for its pacing" is the evidence there is; a read-wait counter in
    `chunkcast send` would be the positive one.

    A wave that did not measure the rate (`sent_bps` / `asked_bps`) or the sender's sleep (`paced_ms`, or the wave's
    seconds) leaves the rule ``UNDECIDED``, and
    ``KEEP`` -- the cut stays -- is every other answer (too few waves, another limit, a rate near the ceiling): principle 5,
    "not measured" is not "far from the ceiling". The loss of a `server_disk` wave is not read here: that is the server's,
    which is what `server_disk` says. `adjust`, its only caller, never raises."""
    waves, seen = [], conn.execute("SELECT session_id, stream FROM wave_rates ORDER BY rowid DESC LIMIT ?",
                                   (drive_rates.MEMORY_SCAN,))
    for row in seen:
        if row["session_id"] == last["session_id"]:
            break
        try:
            got = (json.loads(row["stream"]) if row["stream"] else {}).get(STREAM_KEY)
        except (TypeError, ValueError, AttributeError):
            continue
        if isinstance(got, dict):
            waves.append({"session_id": row["session_id"], "skip": got.get("skip"), "teaches": got.get("teaches"),
                          "asked_bps": got.get("asked_bps"), "sent_bps": got.get("sent_bps"),
                          "first_pass_s": got.get("first_pass_s"), "paced_ms": got.get("paced_ms")})
        if len(waves) == cfg.release_waves:
            break
    answer = {"verdict": "KEEP", "why": "few_waves", "asked_bps": None, "waves": waves}
    if len(waves) < cfg.release_waves:
        return answer
    if any(w["teaches"] is not False or w["skip"] != "server_disk" for w in waves):
        return {**answer, "why": "not_server_disk"}
    if any(not _count(w["sent_bps"]) or not _count(w["asked_bps"]) or w["asked_bps"] <= 0 for w in waves):
        return {**answer, "verdict": "UNDECIDED", "why": "rate_not_measured"}
    if any(not isinstance(w["paced_ms"], dict) or not _number(w["first_pass_s"]) or w["first_pass_s"] <= 0 for w in waves):
        return {**answer, "verdict": "UNDECIDED", "why": "sender_not_measured"}
    if any((w["paced_ms"]["wire"] + w["paced_ms"]["disk"]) * 100 > (100 - cfg.cut_percent) * w["first_pass_s"] * 1000
           for w in waves):
        return {**answer, "why": "sender_paced"}       # asleep for its own pacing: the stream is held by that, not the read
    if any(w["sent_bps"] * 100 >= cfg.cut_percent * w["asked_bps"] for w in waves):
        return {**answer, "why": "rate_near_ceiling"}
    return {**answer, "verdict": "RELEASE", "why": "server_disk_far_from_ceiling", "asked_bps": waves[0]["asked_bps"]}


# --- reading and writing ------------------------------------------------------------------------------------------


def _stream_of(conn: SqlConnection, session_id: str) -> dict | None:
    row = conn.execute("SELECT stream FROM wave_rates WHERE session_id = ?", (session_id,)).fetchone()
    if row is None:
        return None
    try:
        got = json.loads(row["stream"]) if row["stream"] else {}
    except (TypeError, ValueError):
        return {}
    return got if isinstance(got, dict) else {}


def _unlearned(conn: SqlConnection, session_id: str, asked: int | None, sample: dict, outcome: dict) -> str | None:
    """Why the wave teaches nothing, or None -- it does."""
    if sample["ended"] != "send_done":
        return f"ended={sample['ended']}"
    if asked is None:
        return "no_decision"
    if outcome.get("away"):
        return "chunk_net_gap"          # a machine was lost during the first pass: a disconnect, not the rate
    if not sample["chunks"] or not sample["first_pass_s"]:
        return "no_first_pass"
    if sample["first_pass_s"] < drive_rates.MIN_SAMPLE_S or (sample["bytes"] or 0) < drive_rates.MIN_SAMPLE_BYTES:
        return "too_short"
    return server_pressure(conn, session_id)


def server_pressure(conn: SqlConnection, session_id: str) -> str | None:
    """#3269: why the wave proves nothing about the network or the drives because of the server, or None.

    `server_disk` -- the library disk held the stream back (`server_io.bound`): the disk the library sits on was busy and
    slow to answer (util and await, #3365). On 14/11 such a wave taught the drives 21MB/s and took the next waves to the
    floor. #3367: there is no second rule on the system-wide IO pressure (PSI `some`, ≥ 25%): on LabRoot (15/11) the
    root disk made it 9-38% while the library disk was idle, and K8 -- the one rule that changes a wave's rate -- read
    it as the library's. Not measured is None: the evidence is positive only (as `bound`), and the other guards of
    the learning still hold."""
    return "server_disk" if server_io.bound(conn, session_id) else None


def record(conn: SqlConnection, session_id: str, outcome: dict) -> None:
    """Called when a chunk wave ended (`send_done`, `send_failed`, `send_stopped`): the observation of the wave,
    and whether it teaches. Never raises."""
    try:
        decision = drive_rates.decision(conn, session_id) or {}
        asked = drive_rates.parse_bitrate(decision.get("max_bitrate"))
        sample = sample_of(asked, outcome)
        receiverplan.observe(conn, session_id, decision, sample)
        if not drive_rates.enabled(conn):
            return
        sample["skip"] = _unlearned(conn, session_id, asked, sample, outcome)
        sample["teaches"] = sample["skip"] is None
        with _write_lock, writing(conn):
            stream = _stream_of(conn, session_id)
            if stream is not None:
                conn.execute("UPDATE wave_rates SET stream = ? WHERE session_id = ?",
                             (json.dumps({**stream, STREAM_KEY: sample}), session_id))
        loss, sent = sample["loss_permille"], sample["sent_bps"]
        journal(conn, "chunk_rate_recorded",
                f"{session_id} asked={_fmt(asked) if asked else '?'}"
                f" sent={'?' if sent is None else f'{sent / 1e6:.0f}Mb/s'}"
                f" loss={'?' if loss is None else f'{loss:.2f}permille'}"
                f" cloners={sample['cloners']} "
                + ("teaches" if sample["teaches"] else f"teaches nothing: {sample['skip']}"),
                round_id=session_id)
        if asked is not None and sample["away_macs"]:
            # #3977: the cloners lost in a first pass, with the rate asked -- what `adjust` reads before the next waves.
            # In shadow too (an observation); `off` remembers nothing
            inputs_mode = rateinputs.mode(conn)
            if inputs_mode != rateinputs.OFF:
                rateinputs.remember_drops(conn, session_id, asked, sample["away_macs"], inputs_mode)
        if str(sample["skip"]).startswith("server_"):
            # #3269: the server held the wave back -- `unlearned`, with the reason, where the brain keeps its decisions
            memory.record(conn, mode="enforce", rule=RULE, point="#3269", action="WAVE_RATE_LEARN",
                          verdict="UNLEARNED", reason=sample["skip"], evidence=[sample], round_id=session_id,
                          entity=("round", session_id), outcome="applied")
    except Exception as exc:      # noqa: BLE001 -- the learning never stops a wave
        log.exception("recording the chunk rate of %s failed", session_id)
        _failed(conn, f"{session_id} record -- {exc}")


def last_sample(conn: SqlConnection) -> dict | None:
    """The newest wave that taught -- with its `session_id` -- or None: no history."""
    for row in conn.execute("SELECT session_id, stream FROM wave_rates ORDER BY rowid DESC LIMIT ?",
                            (drive_rates.MEMORY_SCAN,)):
        try:
            got = (json.loads(row["stream"]) if row["stream"] else {}).get(STREAM_KEY)
        except (TypeError, ValueError, AttributeError):
            continue
        if isinstance(got, dict) and got.get("teaches") is True and _count(got.get("asked_bps")) and got["asked_bps"] > 0:
            return {**got, "session_id": row["session_id"]}
    return None


def _taught(conn: SqlConnection) -> list[dict]:
    """#3977: the samples of the waves that taught, newest first, each with its `session_id` and `at` (the last `MEMORY_SCAN`
    waves) -- the first is `last_sample`'s."""
    out = []
    for row in conn.execute("SELECT session_id, at, stream FROM wave_rates ORDER BY rowid DESC LIMIT ?",
                            (drive_rates.MEMORY_SCAN,)):
        try:
            got = (json.loads(row["stream"]) if row["stream"] else {}).get(STREAM_KEY)
        except (TypeError, ValueError, AttributeError):
            continue
        if isinstance(got, dict) and got.get("teaches") is True and _count(got.get("asked_bps")) and got["asked_bps"] > 0:
            out.append({**got, "session_id": row["session_id"], "at": row["at"]})
    return out


def _cleans(taught: list[dict]) -> dict[str, list[tuple[int, float]]]:
    """#3977: {mac: [(the rate asked, when)]} of the waves that taught with a loss that was measured and not above
    `LOSS_PERMILLE` (`_clean`), for every cloner that made a drawer durable in their first passes (`goodput`): the waves
    that say a cloner took a rate (`rateinputs.wall_of`)."""
    out: dict[str, list[tuple[int, float]]] = {}
    for got in taught:
        at = _epoch(got.get("at"))
        if at is None or not _clean(got) or not isinstance(got.get("goodput"), dict):
            continue
        for mac in got["goodput"]:
            out.setdefault(mac, []).append((got["asked_bps"], at))
    return out


def _inputs(conn: SqlConnection, session_id: str | None, wave: list[dict], wanted: int, basis: str | None, cfg: Config,
            now: float, floor: int, mode_: str) -> tuple[int, str | None, dict | None, dict | None]:
    """#3977: (the rate, the basis, the goodput verdict, the unsafe-rate verdict) after the two inputs the AIMD does not read
    (`rateinputs`): what each cloner made durable, and the rate a cloner fell at. Each can only hold or lower `wanted`; a
    verdict that is not ``PEAK`` / ``WALL`` leaves the rate exactly as it came (not measured is no decision). With a
    `session_id`, every verdict is a row in `brain_decisions` (K8: `WAVE_GOODPUT`, `WAVE_UNSAFE_RATE`).

    `mode_` (`rateinputs.mode`; the caller does not come here in ``off``). ``enforce``: as above. ``shadow``: the same
    verdicts, computed the same way, and `wanted` / `basis` come back exactly as they came. In both, each verdict carries
    `mode`, `aimd_bps` (the rate without the inputs) and `would_bps` (the rate with them) -- both over `floor`, both before
    the budget of `adjust`, which can only lower them (`chunk_cap`)."""
    macs: Counter[str] = Counter(d["mac"] for d in wave if d.get("mac"))
    taught = _taught(conn)
    was, was_basis, live = wanted, basis, mode_ == rateinputs.ENFORCE
    goodput = None
    if basis == "chunk_up":
        goodput = rateinputs.peak(taught, dict(macs))
        if goodput["verdict"] == rateinputs.PEAK:
            wanted, basis = goodput["rate"], "chunk_peak"
    if goodput is not None:
        goodput = {**goodput, "mode": mode_, "aimd_bps": max(was, floor), "would_bps": max(wanted, floor)}
    unsafe = rateinputs.unsafe(conn, sorted(macs), _cleans(taught), now, REPROBE_WAVES)
    held = unsafe is not None and unsafe["verdict"] == rateinputs.WALL and wanted >= unsafe["rate"]
    if held and unsafe is not None:
        wanted, basis = unsafe["rate"] - cfg.step_bps, "chunk_unsafe"
    if unsafe is not None:
        unsafe = {**unsafe, "mode": mode_, "aimd_bps": max(was, floor), "would_bps": max(wanted, floor)}
    outcome = "applied" if live else "shadow"
    if session_id is not None:
        if goodput is not None:
            goes = (f"הגל יוצא בו ולא ב-{_fmt(was)}" if live
                    else f"צל: הגל היה יוצא בו, והוא יוצא ב-{_fmt(max(was, floor))}")
            say = {rateinputs.PEAK: f"קצב מעל {_fmt(goodput['rate'] or 0)} נוסה ואף משכפל לא כתב בו יותר: {goes}",
                   rateinputs.CLIMB: "מה שהמשכפלים כתבו עלה עם הקצב: ממשיכים לטפס"}.get(
                       goodput["verdict"], f"לא נמדד ({goodput['why']}): הקצב כמו בלי המדידה")
            memory.record(conn, mode=mode_, rule=RULE, point="#3977", action=rateinputs.GOODPUT_ACTION,
                          verdict=goodput["verdict"], reason=say, evidence=[goodput], round_id=session_id,
                          entity=("round", session_id), outcome=outcome)
        if unsafe is not None:
            goes = ("הקצב כבר מתחתיו" if not held else "הגל יוצא מתחתיו" if live
                    else f"צל: הגל היה יוצא ב-{_fmt(max(wanted, floor))}, והוא יוצא ב-{_fmt(max(was, floor))}")
            say = {rateinputs.WALL: f"המשכפל {unsafe['mac']} נפל {rateinputs.UNSAFE_MIN_DROPS} פעמים לפחות עד {_fmt(unsafe['rate'] or 0)}: {goes}",
                   rateinputs.NO_WALL: f"יש נפילות בזיכרון ואין חומה ({unsafe['why']})"}.get(
                       unsafe["verdict"], "הזיכרון של משכפל לא נקרא: הקצב כמו בלי הזיכרון")
            memory.record(conn, mode=mode_, rule=RULE, point="#3977", action=rateinputs.UNSAFE_ACTION,
                          verdict=unsafe["verdict"], reason=say, evidence=[unsafe], round_id=session_id,
                          entity=("round", session_id), outcome=outcome)
    return (wanted, basis, goodput, unsafe) if live else (was, was_basis, goodput, unsafe)


def adjust(conn: SqlConnection, chosen: dict, wave: list[dict], session_id: str | None = None,
           now: float | None = None) -> dict:
    """`choose`'s answer for a chunk wave, with the learned rate in it when there is one. Never raises: on any
    failure the answer is returned as it came. #3977: `session_id` -- the wave, for the rows of `_inputs`; the two inputs
    move the rate only when `rateinputs.mode` is ``enforce`` (``shadow``, the default: rows only; ``off``: not read)."""
    try:
        if chosen.get("basis") in ("disabled", "bad_ceiling") or chosen.get("direct"):
            return chosen
        cfg = config(conn)
        start = drive_rates.parse_bitrate(chosen.get("start")) or drive_rates.START_BITRATE
        floor = int(min(start, drive_rates.START_BITRATE) * drive_rates.FLOOR_RATIO)
        now = time.time() if now is None else now
        inputs_mode = rateinputs.mode(conn)
        last = last_sample(conn)
        if last is None:
            # no history: the rate of today -- unless a cloner of the wave fell at it before (#3977: the falls are the
            # machine's memory, and a wave that loses a cloner never teaches, so there may be no history at all)
            today = drive_rates.parse_bitrate(chosen.get("max_bitrate"))
            if today is None or inputs_mode == rateinputs.OFF:
                return chosen
            wanted, basis, _goodput, unsafe = _inputs(conn, session_id, wave, today, chosen.get("basis"), cfg, now,
                                                      floor, inputs_mode)
            if basis != "chunk_unsafe" or unsafe is None:
                return chosen
            return {**chosen, "max_bitrate": _fmt(max(wanted, floor)), "basis": basis, "by": None,
                    "previous": {"session_id": None, "max_bitrate": chosen.get("max_bitrate"), "rule": "chunk",
                                 "loss_blocks": None, "mac": unsafe["mac"], "drawers": None, "unsafe": unsafe}}
        wanted, basis = next_rate(last, len({d.get("mac") for d in wave}), cfg)
        release = None
        if basis == "chunk_down":
            # #3498: the cut stands on a wave that taught; the waves after it taught nothing (`server_disk`) and so never
            # replace it. When they show the library disk, not the rate, was the limit, the cut is dropped: one step up from
            # the rate the newest wave went out at (`next_rate`'s own step), gradually
            release = server_release(conn, last, cfg)
            if release["verdict"] == "RELEASE":
                wanted, basis = release["asked_bps"] + cfg.step_bps, "chunk_release"
        goodput = unsafe = None
        if inputs_mode != rateinputs.OFF:
            wanted, basis, goodput, unsafe = _inputs(conn, session_id, wave, wanted, basis, cfg, now, floor, inputs_mode)
        cap, capped_by = cap_of(conn, chosen)
        bits = min(max(wanted, floor), cap)
        if bits < wanted:
            basis = "chunk_cap"
        previous = {"session_id": last["session_id"], "max_bitrate": _fmt(last["asked_bps"]), "rule": "chunk",
                    "loss_blocks": last.get("lost_chunks"),      # `decide` prints it as the previous wave's loss
                    "mac": None, "drawers": None, "cloners": last.get("cloners"),
                    "loss_permille": last.get("loss_permille"), "sent_bps": last.get("sent_bps"),
                    "achieved_bps": last.get("achieved_bps"), "capped_by": capped_by if bits < wanted else None,
                    **({"release": release} if release else {}),
                    **({"goodput": goodput} if goodput else {}), **({"unsafe": unsafe} if unsafe else {})}
        return {**chosen, "max_bitrate": _fmt(bits), "basis": basis, "by": None, "previous": previous}
    except Exception as exc:      # noqa: BLE001 -- the learning never stops a wave
        log.exception("the learned chunk rate failed; using the rate of today")
        _failed(conn, f"adjust -- {exc}")
        return chosen


# --- #3269: where a wave starts ---------------------------------------------------------------------------------

#: the bases of `choose` / `adjust` that come from a past wave (or a drive's memory) -- not from today's numbers
LEARNED = frozenset({"learned", "climb", "peak", "back", "held", "up", "down", "drawers", "unmeasured", "fleet",
                     "chunk_up", "chunk_hold", "chunk_down", "chunk_cap", "chunk_release", "chunk_peak", "chunk_unsafe"})
#: `brain_decisions.rule`: the rate of a wave (`catalog.toml`, K8)
RULE = "K8"


def _epoch(raw: object) -> float | None:
    try:
        return datetime.fromisoformat(raw).timestamp() if isinstance(raw, str) else None
    except ValueError:
        return None


def _roster(decision: dict | None) -> list[dict]:
    return [d for d in (decision or {}).get("roster") or [] if isinstance(d, dict)]


def _evidence(conn: SqlConnection, decision: dict) -> dict:
    """What the learned start stands on: the wave (`session_id`, `at`, its roster), the cloner it is about (`mac`,
    None = the whole wave), the drive (`serial`, for a drive's memory) and the write rate it was learned with (`ref_mbps`)."""
    by, previous = decision.get("by"), decision.get("previous") or {}
    if decision.get("basis") == "learned" and isinstance(by, dict) and by.get("serial"):
        # a drive's memory (`_limit_of`): its newest sample, and the rate the drive WROTE then -- the same kind of
        # number as the write check, so a fresh check far from it says the memory is not this drive today
        row = conn.execute("SELECT session_id, at FROM drive_rates WHERE serial = ? AND model IS ?"
                           " ORDER BY id DESC LIMIT 1", (by["serial"], by.get("model"))).fetchone()
        sid, at = (row["session_id"], row["at"]) if row else (None, None)
        rate = by.get("rate_bps")
        ref = rate / 1e6 if _number(rate) and rate > 0 else None
        return {"session_id": sid, "at": at, "mac": by.get("mac"), "serial": by["serial"], "ref_mbps": ref,
                "roster": _roster(drive_rates.decision(conn, sid))}
    sid = previous.get("session_id")
    row = conn.execute("SELECT at FROM wave_rates WHERE session_id = ?", (sid,)).fetchone() if sid else None
    mac = previous.get("mac")
    speeds = conn.execute("SELECT MIN(mbps) AS m FROM model_speeds WHERE session_id = ?"
                          + (" AND mac = ?" if mac else ""), (sid, mac) if mac else (sid,)).fetchone()
    return {"session_id": sid, "at": row["at"] if row else None, "mac": mac, "serial": None,
            "ref_mbps": speeds["m"] if speeds and _number(speeds["m"]) and speeds["m"] > 0 else None,
            "roster": _roster(drive_rates.decision(conn, sid))}


def _why_not(ev: dict, roster: list[dict], probe: float, now: float, cfg: Config) -> list[str]:
    """Why the learned start is not this wave's (empty = it is fresh and matching): `stale` (older than
    `STALE_HOURS`, or no time to tell), `cloner` (another cloner), `drawers` (another number of drawers),
    `probe` (the fresh write check differs from the one it was learned with by more than `PROBE_DIFF_PERCENT`),
    `no_reference` (nothing to compare the write check with). What cannot be checked is not "matching" (principle 5)."""
    why = []
    at = _epoch(ev["at"])
    if at is None or now - at > cfg.stale_hours * 3600:
        why.append("stale")
    then_macs, now_macs = Counter(d.get("mac") for d in ev["roster"]), Counter(d.get("mac") for d in roster)
    if ev["mac"] is not None:
        # the cloner the start is about (for a drive's memory: the one the drive sat in) was in that wave, with as many drawers
        owners = {d.get("mac") for d in ev["roster"] if not ev["serial"] or d.get("serial") == ev["serial"]}
        if ev["mac"] not in owners:
            why.append("cloner")
        elif then_macs[ev["mac"]] != now_macs.get(ev["mac"]):
            why.append("drawers")
    elif set(then_macs) != set(now_macs):
        why.append("cloner")
    elif then_macs != now_macs:
        why.append("drawers")
    ref = ev["ref_mbps"]
    if ref is None:
        why.append("no_reference")
    elif abs(probe - ref) * 100 > cfg.probe_diff_percent * max(probe, ref):
        why.append("probe")
    return why


def _server_cap(conn: SqlConnection, session_id: str, bits: int, floor: int) -> int:
    """#3464: the rate of a chunk wave against the read ceiling of the library disk (`server_cap.learn`), and the rate it
    goes out at. The decision is a SHADOW decision of K8 (`WAVE_SERVER_CAP`: `CAP` -- the rate asked is above the ceiling,
    `FITS` -- it is not). No ceiling learned yet is no decision and no row: "not learned" is not "fits", and an empty
    row per wave would only fill K8's count. `bits` comes back as it came, and only `brain_server_cap`
    (`server_cap.SETTING`, off by default) lets `CAP` cut it, never under `floor`. `brain_veto_mode=off`: nothing is
    computed or recorded. Never raises: on any failure, `bits`."""
    try:
        from server.brain import veto            # like `budget.production`: the switch comes from the veto
        if veto.mode(conn) == veto.OFF:
            return bits
        cap = server_cap.learn(conn)
        if cap is None:
            return bits
        on = server_cap.enabled(conn)
        cut = cap.bps < bits
        held = min(bits, max(cap.bps, floor)) if cut and on else bits
        tail = f"הוגבל ל-{_fmt(held)}" if held != bits else "הרצפה מחזיקה, הקצב לא שונה" if on else "צל, הקצב לא שונה"
        reason = f"{cap.text_he()}; הקצב {_fmt(bits)} " + (f"גבוה מהתקרה ({_fmt(cap.bps)}): {tail}" if cut else "בתוכה")
        memory.record(conn, mode="shadow", rule=RULE, point="#3464", action="WAVE_SERVER_CAP",
                      verdict="CAP" if cut else "FITS", reason=reason, round_id=session_id,
                      entity=("round", session_id), outcome="applied" if held != bits else "shadow",
                      evidence=[{"asked_bps": bits, "held_bps": held, "enabled": on, "cap_bps": cap.bps,
                                 "read_mbps": cap.read_mbps, "device": cap.device, "waves": list(cap.waves)}])
        if held != bits:
            journal(conn, "wave_rate", f"{session_id} {_fmt(held)} basis=server_cap was={_fmt(bits)}"
                    f" cap={_fmt(cap.bps)} device={cap.device}", round_id=session_id)
        return held
    except Exception as exc:      # noqa: BLE001 -- the learning never stops a wave
        log.exception("the server cap of %s failed; keeping %s", session_id, bits)
        _failed(conn, f"{session_id} server_cap -- {exc}")
        return bits


def _raise_cap(decision: dict, bits: int, cfg: Config) -> tuple[int, str]:
    """Bound a stale-start retry by the measured-loss AIMD decision, or one step without it."""
    loss = (decision.get("previous") or {}).get("loss_permille")
    if str(decision.get("basis") or "").startswith("chunk_") and _number(loss):
        return bits, "net_learned"
    return bits + cfg.probe_raise_bps, "raise_step"


def start(conn: SqlConnection, session_id: str, bits: int, probes: dict, now: float | None = None) -> int:
    """#3269: the rate a chunk wave goes out at, once the write check of its drawers is known (`ChunkEngine.wave_start`,
    after the drawers of the first partition listened). Never raises: on any failure, `bits` as it came.

    A learned start that is stale or not this wave's is replaced by what was measured now: the lowest write check of
    the wave's drawers, up to the most the wave may ask (`cap_of`: its ceiling and the network budget), never under the
    floor of `choose` (`basis: "probe"`) -- and, metal 21/11, never over `_raise_cap`: the write check is a ceiling of the
    drives, not a network rate, so a start the AIMD learned from a measured wave is not raised by it, and any other start
    by at most one step. A learned start that is fresh and matching, a start that was not learned, a write check that
    is missing for one drawer -- the rate as chosen.
    Either way the decision is kept in `wave_rates.decision.start_check` and in `brain_decisions` (K8); a changed rate
    is a second `wave_rate` line in the journal. #3464: the rate that comes out of this is then held against the read
    ceiling of the library disk (`_server_cap`) -- a shadow decision, which changes nothing unless `brain_server_cap` is on."""
    try:
        decision = drive_rates.decision(conn, session_id)
        if (not drive_rates.enabled(conn) or decision is None or decision.get("direct")
                or decision.get("basis") in ("disabled", "bad_ceiling")):
            return bits
        measured = list(probes.values())
        probe = min(measured) if measured and all(_count(v) and v > 0 for v in measured) else None
        basis, cfg = decision.get("basis"), config(conn)
        if basis not in LEARNED:
            why, ev = [f"basis={basis}"], None
        elif probe is None:
            why, ev = ["no_probe"], None
        else:
            ev = _evidence(conn, decision)
            own = [v for (_mac, serial), v in probes.items() if serial == ev["serial"]] if ev["serial"] else []
            why = _why_not(ev, _roster(decision), min(own) if own else probe,
                           time.time() if now is None else now, cfg)
        cap, capped_by = cap_of(conn, decision)
        floor = int(min(drive_rates.parse_bitrate(decision.get("start")) or drive_rates.START_BITRATE,
                        drive_rates.START_BITRATE) * drive_rates.FLOOR_RATIO)
        retry = ev is not None and bool(why)
        new = max(min(int(probe * 8 * 10 ** 6), cap), floor) if retry and probe is not None else bits
        # #3977: a start replaced by the write check still does not land on a rate a cloner of the wave fell at (`adjust`).
        # The wave was decided in a mode (`previous.unsafe.mode`): in shadow the wall only says what it would have done
        wall = (decision.get("previous") or {}).get("unsafe") or {}
        walled = None
        if retry and wall.get("verdict") == rateinputs.WALL and _count(wall.get("rate")) and new >= wall["rate"]:
            walled = {"mode": wall.get("mode"), "would": _fmt(max(wall["rate"] - cfg.step_bps, floor))}
            if wall.get("mode") == rateinputs.ENFORCE:
                new, why = max(wall["rate"] - cfg.step_bps, floor), [*why, "unsafe_wall"]
        raise_cap, raise_by = _raise_cap(decision, bits, cfg)
        limited = retry and new > raise_cap
        if limited:
            why.append(raise_by)
            new = raise_cap
        change = new != bits
        held = _server_cap(conn, session_id, new, floor)
        check = {"was": _fmt(bits), "was_basis": basis, "why": why or ["fresh_matching"], "probe_mbps": probe,
                 "cap": _fmt(cap), "capped_by": capped_by,
                 **({"raise_cap": _fmt(raise_cap), "raise_by": raise_by} if limited else {}),
                 "from": None if ev is None else {k: ev[k] for k in ("session_id", "at", "mac", "serial", "ref_mbps")},
                 **({"unsafe_wall": walled} if walled else {})}
        release = (decision.get("previous") or {}).get("release")
        if release:
            check["release"] = release          # #3498: why the cut was kept, dropped, or could not be decided
        stored = {**decision, "start_check": check,
                  **({"max_bitrate": _fmt(new), "basis": "probe"} if change else {}),
                  **({"max_bitrate": _fmt(held), "basis": "server_cap"} if held != new else {})}
        with _write_lock, writing(conn):
            conn.execute("UPDATE wave_rates SET decision = ? WHERE session_id = ?",
                         (json.dumps(stored, ensure_ascii=False), session_id))
        if change and ev is not None:
            journal(conn, "wave_rate",
                    f"{session_id} {_fmt(new)} basis=probe was={_fmt(bits)}({basis}) why={','.join(why)}"
                    f" probe={probe}MB/s cap={_fmt(cap)} from={ev['session_id']}", round_id=session_id)
        memory.record(conn, mode="enforce", rule=RULE, point="#3269", action="WAVE_START",
                      verdict="PROBE" if change else "KEEP",
                      reason=f"{_fmt(bits)}({basis}) -> {_fmt(new)}: {','.join(why or ['fresh_matching'])}"
                      + (f" [release={release['verdict']}: {release['why']}]" if release else ""),
                      evidence=[check], round_id=session_id, entity=("round", session_id), outcome="applied")
        netcap.shadow(conn, session_id, decision, new, cfg.loss_permille)     # #3462: the learned cap, shadow only
        return held
    except Exception as exc:      # noqa: BLE001 -- the learning never stops a wave
        log.exception("the start rate of %s failed; keeping %s", session_id, bits)
        _failed(conn, f"{session_id} start -- {exc}")
        return bits


# --- #3295: what a drive writes for the whole wave ----------------------------------------------------------------


def _clean(got: dict) -> bool:
    """#3520: the wave's loss was measured and not above `LOSS_PERMILLE` (not measured is not "none lost")."""
    loss = got.get("loss_permille")
    return _number(loss) and loss <= LOSS_PERMILLE


def _was_in(got: dict, serial: str) -> bool:
    """#3520: the drive took part in the wave -- the check set `--write-kbps` for it, or a machine reported it durable."""
    drives = got.get("drives")
    return (serial in (got.get("write_serials") or [])
            or (isinstance(drives, dict) and any(isinstance(d, dict) and serial in d for d in drives.values())))


def _sender_limited(got: dict) -> bool | None:
    """#3530: was the sender of the wave held by the wire rate (`--kbps`) for more than `server_io.SENDER_ASLEEP_MAX` of the first pass
    (`paced_ms.wire` over `first_pass_s`)? Then it offered what the wire allowed, and that may be less than `--write-kbps`. None = not
    measured (`paced_ms` or `first_pass_s` missing or not whole): not "free" -- principle 5. The sleep for `--write-kbps` itself
    (`paced_ms.disk`) is NOT a limit: it is the cap whose ceiling `sustained` learns, and a sender asleep for it offered the held rate
    (LabRoot: 74% of the first pass, #3498)."""
    paced, seconds = got.get("paced_ms"), got.get("first_pass_s")
    if not (isinstance(paced, dict) and _count(paced.get("wire")) and _count(paced.get("disk")) and _number(seconds) and seconds > 0):
        return None
    return paced["wire"] > server_io.SENDER_ASLEEP_MAX * seconds * 1000


def _lag_proof(conn: SqlConnection, session_id: str, serial: str, mine: dict, got: dict) -> tuple[str, str | None]:
    """#3530: what a wave in which the receiver skipped chunks for `serial` (`mine`: `rate_outcome.behind[serial]`) says about its
    ceiling -> ``(state, why)``, `why` only for ``unmeasured``:

    - ``kept_up``: the chunks skipped are under `BEHIND_PERMILLE` of the chunks of the passes it was behind in (`_behind_share`, the
      threshold #3526 gave the cut): a hiccup, and the bytes in `behind` are nearly the whole offer (b1 of the metal 16/11: 3 of
      18,247) -- not what the drive writes. The drive kept up in the wave.
    - ``none``: over the threshold, but the sender was held by the wire (`_sender_limited`): the bytes the drive made durable are
      the sender's rate, not the drive's.
    - ``proof``: over the threshold and the sender measured and not held.
    - ``unmeasured``: the share is not known (`skipped_not_counted`: no `chunk_pass_counters` row said it, a count of 0 contradicting
      `behind`, or the read failed) or the sender's sleep is not (`sender_not_measured`). "Not measured" is not a proof of a ceiling
      (principle 5): it neither teaches nor counts as the drive keeping up."""
    share = _behind_share(mine, _skipped_in_first_passes(conn, session_id).get(serial))
    if share is None:
        return "unmeasured", "skipped_not_counted"
    if share * 1000 < BEHIND_PERMILLE:
        return "kept_up", None
    limited = _sender_limited(got)
    if limited is None:
        return "unmeasured", "sender_not_measured"
    return ("none" if limited else "proof"), None


def sustained(conn: SqlConnection, serial: str, notes: list | None = None) -> tuple[int, str] | None:
    """#3295: (the MB/s the drive wrote in the first passes of the newest chunk wave it was behind in, that wave), or
    None -- no such wave in the last `MEMORY_SCAN`, or one too short to tell (`MIN_SAMPLE_BYTES`, `MIN_SAMPLE_S`).

    The evidence is positive only: the receiver skipped chunks for the drive ("the drive was behind",
    `rate_outcome.behind`), so what it made durable over those seconds is what it can write -- not the rate it was
    offered. A drive that kept up teaches nothing here: that proves at least the offer, not a ceiling.

    #3520, two limits on that proof. A wave whose loss was not measured, or was above `LOSS_PERMILLE`, is not proof (`_clean`): the
    drive's durable bytes were short because the chunks were lost. And the proof is spent (None) once `REPROBE_WAVES` clean waves
    newer than it ended in `send_done` with the drive in them at `--write-kbps` of the proof's ceiling or more: it kept up there, so
    the loop may climb again -- and a wave in which it falls behind again is a new proof.

    #3530, the same limits on what "behind" is (`_lag_proof`): a wave in which the chunks skipped for the drive are under
    `BEHIND_PERMILLE` of its passes' chunks is a wave the drive kept up in; one in which the sender was held by the wire is no proof
    at all; and one whose skipped chunks or sender's sleep were not measured is no proof and not counted -- any of them would have
    taught the rate of the wave as the drive's ceiling and lowered it: a slowness lock (#3520, #3525). `notes`, when given, gets
    ``{"session_id", "serial", "why"}`` for every wave left out as not measured: the evidence of the decision that used it."""
    held: list[int] = []           # `--write-kbps` of the clean waves newer than the proof the drive took part in and kept up
    for row in conn.execute("SELECT session_id, stream FROM wave_rates ORDER BY rowid DESC LIMIT ?",
                            (drive_rates.MEMORY_SCAN,)):
        try:
            got = (json.loads(row["stream"]) if row["stream"] else {}).get(STREAM_KEY) or {}
            mine = (got.get("behind") or {}).get(serial)
            clean = _clean(got)
            was_in = _was_in(got, serial)
        except (TypeError, ValueError, AttributeError):
            continue
        if not clean:
            continue
        if isinstance(mine, dict):
            lag, why = _lag_proof(conn, row["session_id"], serial, mine, got)
            if lag == "unmeasured":
                if notes is not None:
                    notes.append({"session_id": row["session_id"], "serial": serial, "why": why})
                continue
            if lag == "none":
                continue
            if lag == "kept_up":
                mine = None
        if not isinstance(mine, dict):
            if was_in and got.get("ended") == "send_done" and _count(got.get("write_kbps")):
                held.append(got["write_kbps"])
            continue
        size, seconds = mine.get("bytes"), mine.get("seconds")
        if (_count(size) and _number(seconds) and seconds >= drive_rates.MIN_SAMPLE_S
                and size >= drive_rates.MIN_SAMPLE_BYTES):
            wrote = int(size / seconds / 10 ** 6)
            at = int(wrote * 8000 * writerate.MARGIN)
            return None if sum(1 for kbps in held if kbps >= at) >= REPROBE_WAVES else (wrote, row["session_id"])
    return None


def write_history(conn: SqlConnection, serial: str, notes: list | None = None) -> tuple[int, str] | None:
    """#3295: `ChunkEngine.write_history` -- `sustained`, or None when the automatic rate is switched off
    (`learned_rate_enabled`) or on any failure (never raises).

    Nadav 15/11: "ההפצה בקצב של הדיסקים המהירים. האיטי מפספס חתיכות ומשלים אותן אחר כך." A drive with a history
    is left out of `--write-kbps` (`ChunkEngine._write_kbps`): this is NOT a ceiling for the stream. What it
    sustained is kept for the prediction (ETA, K3, the brain) -- no consumer reads it yet. The source of the problem:
    the write check is 4MiB in three places (`agent/lib/restore/writecheck.sh`) and reads the drive's cache -- on
    14/11 (#3034, cloner 2) an APS-SL3N-512 checked at 209MB/s and wrote ~55MB/s in the stream."""
    try:
        return sustained(conn, serial, notes) if drive_rates.enabled(conn) else None
    except Exception as exc:      # noqa: BLE001 -- the learning never stops a wave
        log.exception("the write history of %s failed; it counts", serial)
        _failed(conn, f"write_history {serial} -- {exc}")
        return None


# --- #3506: `--write-kbps`, a loop between the waves --------------------------------------------------------------


def last_wave(conn: SqlConnection, session_id: str) -> dict | None:
    """#3506: the sample of the newest chunk wave before `session_id` -- with its `session_id` and `at` -- if it ended in
    `send_done`, or None (no wave, a failed one, a row that cannot be read). One wave, not the newest that qualifies: a
    wave in between that proved nothing about `--write-kbps` is not skipped over."""
    for row in conn.execute("SELECT session_id, at, stream FROM wave_rates ORDER BY rowid DESC LIMIT ?",
                            (drive_rates.MEMORY_SCAN,)):
        if row["session_id"] == session_id:
            continue
        try:
            got = (json.loads(row["stream"]) if row["stream"] else {}).get(STREAM_KEY)
        except (TypeError, ValueError, AttributeError):
            continue
        if isinstance(got, dict):
            return {**got, "session_id": row["session_id"], "at": row["at"]} if got.get("ended") == "send_done" else None
    return None


def _behind_kbps(mine: object, cfg: Config) -> int | None:
    """What a drive wrote while the receiver skipped chunks for it (`rate_outcome.behind`: bytes, seconds), as `--write-kbps`
    (kbit/s of uncompressed bytes) under the margin, or None -- no number, or a pass too short to tell (`MIN_SAMPLE_S`)."""
    if not isinstance(mine, dict):
        return None
    size, seconds = mine.get("bytes"), mine.get("seconds")
    if not (_count(size) and _number(seconds) and seconds >= drive_rates.MIN_SAMPLE_S and size >= drive_rates.MIN_SAMPLE_BYTES):
        return None
    return int(size / seconds * 8 / 1000 * cfg.write_margin)


def _behind_share(mine: object, skipped: object) -> float | None:
    """#3526: the share (0..1) of the chunks of the passes a drive was behind in that the receiver skipped for it
    (`rate_outcome.behind[serial]`, and the chunks skipped for it in those passes, `_skipped_in_first_passes`), or None -- one of
    them is not there, or nothing was skipped (the receiver said it was behind, so a count of 0 contradicts it: not measured). The
    chunks of those passes are the ones it made durable (`bytes`, `CHUNK_BYTES` each) and the ones skipped."""
    if not (isinstance(mine, dict) and _count(mine.get("bytes")) and _count(skipped) and skipped > 0):
        return None
    return skipped / (mine["bytes"] // CHUNK_BYTES + skipped)


def _lag(mine: object, skipped: object, held: int, cfg: Config) -> tuple[str, int | None]:
    """#3526: what one drive that fell behind in the wave asks of `--write-kbps` (`held`: what the wave went out at) ->
    ``("negligible", None)`` | ``("graded", kbps)`` | ``("unmeasured", kbps)``.

    - The share of the chunks skipped is known: under `behind_permille`, ``negligible`` -- the drive made durable nearly all it was
      offered, a hiccup and not a rate. From it, ``graded``: it made `1 - share` of what was offered durable, so the next wave asks
      `held x (1 - share)` under the margin. `behind` is NOT what the drive wrote when the share is small: its bytes are nearly the
      whole pass (metal 16/11: 76.5GB in 379s = 201MB/s, over the 157MB/s asked).
    - The share is not known (a wave without the counters of its skipped chunks): what it wrote x the margin (`_behind_kbps`) when
      that is under `held` -- the old rule -- and with no usable number, ``unmeasured``: ONE step down (`write_step_percent`, the
      size of a step up). A number nobody measured is not made up as a quarter of the rate."""
    share = _behind_share(mine, skipped)
    if share is not None:
        if share * 1000 < cfg.behind_permille:
            return "negligible", None
        return "graded", max(1, int(held * (1 - share) * cfg.write_margin))
    wrote = _behind_kbps(mine, cfg)
    if wrote is not None and wrote < held:
        return "graded", wrote
    return "unmeasured", held * (100 - cfg.write_step_percent) // 100


def write_next(prev: dict | None, base: int, serials: list[str], ceiling: int | None, cfg: Config, now: float) -> dict:
    """#3506: `--write-kbps` of a partition -> `{"verdict", "why", "kbps"}`, from `base` (the write check x the margin, the
    slowest of `serials`, the drives it counted) and what the newest wave showed (`prev`, `last_wave`).

    ``PROBE`` (= `base`, as before the loop) unless the evidence is about THIS wave's drives: `prev` ended well, is not older
    than `stale_hours`, its `--write-kbps` was set by the check of exactly these serials, and the check of today is not
    far below it (`probe_diff_percent`). Then, from `prev`'s own `--write-kbps` (never from the check):

    - ``CUT``: one of these drives fell behind in it (the receiver skipped chunks for it, `behind`), graded by `_lag` -- by the
      share of the chunks it skipped (`prev["skipped"]`, serial -> chunks: `_skipped_in_first_passes`), the deepest of the drives;
      #3526: a drive that skipped under `behind_permille` of them is ``HOLD`` ``behind_negligible``, not a cut;
    - ``HOLD``: not measured -- a machine that did not tell what its drives skipped (`lost_chunks` is None), or a sender whose
      sleep was not read (`paced_ms`) -- or the cap was not what held the sender (it slept for `--write-kbps` less than
      `write_held_percent` of the first pass), or the rate already is the most a drive of these was proven to write;
    - ``RAISE``: the drives kept up AND the cap held the sender: one step (`write_step_percent`), up to what a drive of these
      was proven to write when it fell behind (`ceiling`, `write_history`) x the margin.

    A ``ceiling`` under the rate of any outcome caps it: a ``RAISE`` stops at it, anything else is a ``CUT`` to it -- a drive that
    wrote that much when it fell behind will not write more now (`sustained`, the wave that taught it)."""
    def answer(verdict: str, why: str, kbps: int) -> dict:
        if ceiling is not None and kbps > ceiling:       # a step up stays a step up (to the ceiling); anything else is a cut to it
            return {"verdict": verdict if verdict == "RAISE" else "CUT", "why": f"{why}+sustained", "kbps": ceiling}
        return {"verdict": verdict, "why": why, "kbps": kbps}

    if prev is None:
        return answer("PROBE", "no_previous_wave", base)
    at, held = _epoch(prev.get("at")), prev.get("write_kbps")
    if at is None or now - at > cfg.stale_hours * 3600:
        return answer("PROBE", "stale", base)
    if not _count(held) or held <= 0 or prev.get("write_serials") != sorted(serials):
        return answer("PROBE", "other_drives", base)
    if base * 100 < held * (100 - cfg.probe_diff_percent):
        return answer("PROBE", "check_far_below", base)
    behind_of, skipped_of = prev.get("behind"), prev.get("skipped")
    behind = behind_of if isinstance(behind_of, dict) else {}
    skipped = skipped_of if isinstance(skipped_of, dict) else {}
    lagged = [serial for serial in serials if serial in behind]
    if lagged:
        lags = [_lag(behind[serial], skipped.get(serial), held, cfg) for serial in lagged]
        cuts = [(kbps, kind) for kind, kbps in lags if kbps is not None]
        if not cuts:
            return answer("HOLD", "behind_negligible", held)
        kbps, kind = min(cuts)
        return answer("CUT", "drive_behind" if kind == "graded" else "drive_behind_unmeasured", kbps)
    if not _count(prev.get("lost_chunks")):
        return answer("HOLD", "kept_up_not_measured", held)
    paced, seconds = prev.get("paced_ms"), prev.get("first_pass_s")
    if not (isinstance(paced, dict) and _count(paced.get("disk")) and _number(seconds) and seconds > 0):
        return answer("HOLD", "sender_not_measured", held)
    if paced["disk"] * 100 < cfg.write_held_percent * seconds * 1000:
        return answer("HOLD", "cap_did_not_hold_sender", held)
    if ceiling is not None and held >= ceiling:
        return answer("HOLD", "at_proven_ceiling", held)
    return answer("RAISE", "kept_up_and_held_by_cap", held * (100 + cfg.write_step_percent) // 100)


def _skipped_in_first_passes(conn: SqlConnection, session_id: str) -> dict[str, int]:
    """#3526: {serial: chunks the receivers skipped for it in the first passes of `session_id`} -- `chunk_pass_counters`, pass 1 of every
    partition and machine, what `behind` was built from. A serial that any of its rows did not count (None) is left out: not
    measured is not 0. No row that said anything: {}. Never raises: on any failure, {}."""
    try:
        total: dict[str, int] = {}
        unsaid: set[str] = set()
        for row in conn.execute("SELECT skipped_json FROM chunk_pass_counters WHERE session_id = ? AND pass = 1", (session_id,)):
            for serial, n in (json.loads(row["skipped_json"]) if row["skipped_json"] else {}).items():
                if _count(n):
                    total[serial] = total.get(serial, 0) + n
                else:
                    unsaid.add(serial)
        return {serial: n for serial, n in total.items() if serial not in unsaid}
    except Exception:             # noqa: BLE001 -- the learning never stops a wave
        log.exception("the skipped chunks of %s were not read", session_id)
        return {}


def _ceiling(conn: SqlConnection, serials: list[str], cfg: Config, notes: list | None = None) -> int | None:
    """The most `--write-kbps` that the slowest of `serials` was proven to write (`write_history`: what it wrote in a wave it fell
    behind in) x the margin, or None -- none of them has such a wave."""
    wrote = [got[0] for serial in serials if (got := write_history(conn, serial, notes)) is not None]
    return int(min(wrote) * 8000 * cfg.write_margin) if wrote else None


def write_ceiling(conn: SqlConnection, serials: list[str]) -> int | None:
    """#3511/#3512: the most `--write-kbps` these drives were proven to write (`_ceiling`), for the in-wave feedback's
    `--rate-max` -- or None: no such proof, learning switched off, or any failure. Never raises."""
    try:
        if not drive_rates.enabled(conn):
            return None
        return _ceiling(conn, serials, config(conn))
    except Exception:             # noqa: BLE001 -- the learning never stops a wave
        log.exception("the write ceiling of %s failed; the feedback keeps its fallback", serials)
        return None


def _receiver_w_ms(conn: SqlConnection, session_id: str) -> float | None:
    """The highest `disk_w_ms_max` the receivers of a wave reported (`wave_senses`), or None -- not read. Evidence kept with the
    decision, which does not stand on it: no threshold on the write time of a drawer has been measured."""
    try:
        got = [wavesense.numbers(row["senses_json"]).get("disk_w_ms_max")
               for row in conn.execute("SELECT senses_json FROM wave_senses WHERE session_id = ?", (session_id,))]
    except sqlite3.Error:
        return None
    heard = [v for v in got if v is not None]
    return max(heard) if heard else None


def write_rate(conn: SqlConnection, session_id: str, part: int, base: int, serials: list[str],
               now: float | None = None) -> tuple[int, str | None]:
    """#3506: `ChunkEngine.write_rate` -- (the `--write-kbps` of a partition, a label of what moved it or None). K8, in `enforce`
    like the rest of the rate of a wave: every decision, with what it stood on, is a row in `brain_decisions`
    (`WAVE_WRITE_RATE`: `PROBE`, `HOLD`, `RAISE`, `CUT`). `PROBE` is the write check of the drives, the start, when nothing else was
    measured. Learning switched off (`learned_rate_enabled`), a direct wave, or any failure: `base`, as before. Never raises."""
    try:
        if not drive_rates.enabled(conn) or (drive_rates.decision(conn, session_id) or {}).get("direct"):
            return base, None
        cfg = config(conn)
        prev = last_wave(conn, session_id)
        if prev is not None:
            prev["skipped"] = _skipped_in_first_passes(conn, prev["session_id"])
        unmeasured: list[dict] = []
        ceiling = _ceiling(conn, serials, cfg, unmeasured)
        got = write_next(prev, base, serials, ceiling, cfg, time.time() if now is None else now)
        kbps = got["kbps"]
        seen = None
        if prev is not None:
            seen = {k: prev.get(k) for k in ("session_id", "at", "write_kbps", "write_serials", "lost_chunks", "paced_ms",
                                             "first_pass_s")}
            seen["behind"] = {s: v for s, v in (prev.get("behind") or {}).items() if s in serials}
            seen["skipped"] = {s: v for s, v in prev["skipped"].items() if s in serials}
            seen["receiver_w_ms_max"] = _receiver_w_ms(conn, prev["session_id"])
        evidence = {"part": part, "serials": sorted(serials), "check_kbps": base, "kbps": kbps, "ceiling_kbps": ceiling,
                    "ceiling_unmeasured": unmeasured[:10], "previous": seen}
        memory.record(conn, mode="enforce", rule=RULE, point="#3506", action="WAVE_WRITE_RATE", verdict=got["verdict"],
                      reason=f"part {part} {base / 8000:g}MB/s -> {kbps / 8000:g}MB/s: {got['why']}", evidence=[evidence],
                      round_id=session_id, entity=("round", session_id), outcome="applied")
        return kbps, None if got["verdict"] == "PROBE" and kbps == base else f"{got['verdict']} {got['why']}"
    except Exception as exc:      # noqa: BLE001 -- the learning never stops a wave
        log.exception("the write rate of %s part %s failed; keeping %s", session_id, part, base)
        _failed(conn, f"{session_id} write_rate -- {exc}")
        return base, None


def _failed(conn: SqlConnection, detail: str) -> None:
    try:
        journal(conn, "chunk_rate_failed", " ".join(detail.split())[:200])
    except Exception:             # noqa: BLE001 -- even the journal does not stop a wave
        log.exception("chunk_rate failure was not journaled")
