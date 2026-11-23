"""#3295 (הכרעה ב'), #3064 (K3): ETA חזוי לגל שבו כונן איטי מפספס חתיכות ומשלים אותן -- והתוצאה בפועל, כמספרים.

**נדב 15/11, #3295 ב':** ההפצה רצה בקצב של הדיסקים המהירים. כונן שפיגר בגל קודם (`chunk_rate.write_history`) יוצא
מ-`--write-kbps`, מפספס חתיכות, ומשלים אותן אחר כך -- מהמגירה השכנה (neighborfill) או ב-HTTP. הקצב המתמשך שלו משמש
**רק לחיזוי**. עד כאן ה-ETA של המוח (K3, `linkwarn.py`) חושב רק לפי הקישור.

**החיזוי** (`predict`, פונקציה טהורה), ברגע שהגל יודע את קצב ההתחלה שלו (`ChunkEngine.wave_start`, אחרי `chunk_rate.start`):

- ``T1`` -- המעבר הראשון בקצב של המהירים: הבתים הדחוסים של האימג' (`total_compressed_bytes`) בקצב הזרם. **#3550:** קצב
  הזרם הוא `min(המבוקש, מה שהגל הקודם השיג)` כשהגל הקודם של מנוע החתיכות הסתיים ב-`send_done` (`stream.chunk.achieved_bps` ב-
  `wave_rates`); אין גל כזה -- המבוקש, כמו קודם. ברזל 16/11 (r14+r16, 24 גלים): חיזוי לפי המבוקש (669Mb/s) יצא 222 ש' בכל
  גל מול 335-475 בפועל, כי המנוע משיג 327-461Mb/s ולא את המבוקש.
- לכל כונן איטי, ``s`` = מה שהוא כתב בגל שבו פיגר (MB/s, `write_history`): במעבר הראשון הוא כותב ``s × T1``, וחסרים לו
  ``W − s × T1`` (``W`` = חתיכות האימג' × 4MiB, מה שהוא כותב). ההשלמה רצה בקצב ``min(s, המקור)``: שכנה במשכפל שלו שאינה
  איטית -- `fill.CEILING_BPS` (100MiB/s, M1/M2 ברזל 14/11); אין שכנה -- HTTP, ``R_HTTP_START`` חתיכות/ש' (KT4).
  ``T = T1 + חסר ÷ קצב ההשלמה``.
- ``ETA`` = הגדול מבין ``T1`` וכל ``T`` של כונן איטי. גל בלי כונן איטי הוא ``T1`` בלבד -- גם הוא נרשם, כבסיס.

**מה שלא נמדד הוא "לא נמדד":** גודל שלא נקרא, קצב זרם שאינו מספר חיובי, או הפצה ישירה (אין קובץ להוריד ממנו) -- אין
חיזוי, ושורה אחת שאומרת למה. כונן איטי שלא נמצא לו משכפל -- המקור "לא ידוע", והוא נחזה ב-HTTP (המקור האיטי מבין השניים).
**#3574, RD2-1:** כונן בלי `write_history` כשלכונן אחר בגל יש -- החיזוי נשאר המספר, והראיות נושאות `confidence=low`
ו-`no_history=<serial>`. בלי הסימון זה חיזוי רגיל: "לא ידוע" התקפל לתוכו (ברזל 17/11, RD2-1, 321 ש' מול 1755).

**הרישום** (`brain_decisions`, כלל ``K3``): ``WAVE_ETA`` (``PREDICT``) כשהגל יוצא, עם כל המספרים בראיות; בסוף הגל
(`send_done`) -- ``WAVE_ETA_ACTUAL`` עם ``predicted_s``, ``actual_s``, ``ratio``, ומעבר ראשון חזוי מול בפועל
(`ChunkEngine.rate_outcome`). שני הזמנים מאותו שעון ומאותו רגע (קצב ההתחלה), כדי שאפשר יהיה למדוד התאמה (#3321). גל
שנכשל או נעצר -- "לא נמדד", בלי מספר. **שום דבר לא זז:** החיזוי אינו משנה קצב, גם ב-``enforce``.
המתג הוא ``brain_veto_mode``: ``off`` = לא מחושב ולא נרשם. **לעולם אינו זורק** אל הגל.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Callable

from server.brain import fill, memory

log = logging.getLogger("imagesrv.brain")

RULE, PREDICT, ACTUAL = "K3", "WAVE_ETA", "WAVE_ETA_ACTUAL"
#: #3550: כמה גלים אחרונים ב-`wave_rates` עוברים עליהם כדי למצוא את הגל האחרון של מנוע החתיכות (גל ישן של השולח שהוסר ביניהם -- מדלגים).
PREVIOUS_SCAN = 20
OFF = "off"
#: חתיכה במנוע החתיכות (`chunksend.CHUNK_BYTES`): מה שהכונן כותב.
CHUNK_BYTES = 4 * 1024 * 1024
#: KT4: `r_http` התחלתי -- 42 חתיכות/ש', הנמוך שנמדד על ברזל (13/11, ses_108caf1d). ההשלמה ב-HTTP אינה מהירה מזה בחיזוי.
R_HTTP_START = 42.0
NEIGHBOR, HTTP = "neighbor", "http"

#: #3574: `chunksend` writes the line as `<wave> <serial as a JSON string> check=<N>MB/s wrote=<N>MB/s in=<wave> <state>`. The serial is
#: quoted because it is not a word: `t10.ATA     APS-SL3N-512 ... SICSD40302WL` has spaces, and with `(\S+)` the line did not match, the
#: slow drive was never learned and the fast one took its place in the ETA (ברזל 17/11: -55%).
_BEHIND = re.compile(r'^(\S+) ("(?:[^"\\]|\\.)*") check=(\d+)MB/s wrote=(\d+)MB/s in=(\S+)')


@dataclass(frozen=True)
class Slow:
    serial: str
    wrote_mbps: int                  # MB/s שהכונן כתב בגל שבו פיגר (`write_history`)
    source: str                      # NEIGHBOR / HTTP
    mac: str | None = None


@dataclass(frozen=True)
class Forecast:
    eta_s: float
    t1_s: float
    drives: tuple[tuple[str, float, str, float], ...]     # (serial, wrote MB/s, מקור, T)


def predict(chunks: int, zbytes: int, stream_bps: int, slow: list[Slow]) -> Forecast:
    """(חתיכות האימג', הבתים הדחוסים, קצב הזרם, הכוננים האיטיים) -> החיזוי. טהורה."""
    t1 = zbytes * 8 / stream_bps
    whole = chunks * CHUNK_BYTES
    out = []
    for d in slow:
        rate = d.wrote_mbps * 10 ** 6
        source = fill.CEILING_BPS if d.source == NEIGHBOR else R_HTTP_START * CHUNK_BYTES
        missing = max(whole - rate * t1, 0.0)
        out.append((d.serial, float(d.wrote_mbps), d.source, t1 + missing / min(rate, source)))
    return Forecast(max([t1, *(t for *_rest, t in out)]), t1, tuple(out))


def _wave_serials(sample: dict) -> frozenset[str] | None:
    """#3640: the drives of a recorded chunk wave -- `chunk.drives` (``{mac: {serial: bytes}}``, every drive the first pass counted),
    else `chunk.write_serials`; None = the wave did not record them."""
    drives = sample.get("drives")
    if isinstance(drives, dict) and drives:
        got = {serial for per in drives.values() if isinstance(per, dict) for serial in per}
    else:
        listed = sample.get("write_serials")
        got = set(listed) if isinstance(listed, list) else set()
    return frozenset(got) if got and all(isinstance(x, str) and x for x in got) else None


class WaveEta:
    """המצב בשרת אחד. ``library`` = הספרייה של המנוע (`get`); ``chunk`` = המנוע, מחובר אחרי שנבנה."""

    def __init__(self, conn, *, library=None, chunk=None, mode: Callable[[], str] | None = None,
                 clock: Callable[[], float] = time.monotonic,
                 learned_rate: Callable[[str, int, list[str]], int | None] | None = None):
        self.conn, self.library, self.chunk = conn, library, chunk
        #: #3041: (wave, the stream rate, the wave's cloners) -> the lowest learned capability below that rate, or None.
        #: It records the shadow decision itself (`capability.wave_rate`); here it only gives the ETA at that rate.
        self._learned_rate = learned_rate
        self._mode_fn, self._clock = mode, clock
        self._lock = threading.Lock()
        self._behind: dict[str, dict[str, tuple[int, int]]] = {}
        #: wave -> (row, מתי נחזה, החיזוי)
        self._open: dict[str, tuple[int | None, float, Forecast]] = {}

    def _mode(self) -> str:
        if self._mode_fn is not None:
            return self._mode_fn()
        from server.brain import veto            # כמו ב-`budget.production`: המתג מגיע מהווטו
        return veto.mode(self.conn)

    # -- הכניסות: לעולם אינן זורקות --

    def on_event(self, event: str, detail: str, session_id: str) -> None:
        try:
            if event == "chunk_write_behind":
                self._learn_behind(detail, session_id)
            elif event in ("send_done", "send_failed", "send_stopped"):
                self._finish(session_id, event)
        except Exception:                       # noqa: BLE001 -- המוח אינו מפיל גל
            log.exception("brain: the wave ETA of %s failed on %s", session_id, event)

    def _learn_behind(self, detail: str, session_id: str) -> None:
        """#3574: the drive named in a `chunk_write_behind` line is a slow one of this wave. A line this does not read
        is a log line with the event's name, not a silence: K3 would predict the wave without that drive (#3574 was
        exactly that, for weeks, with nothing to read). It still never raises into the wave (the caller catches)."""
        got = _BEHIND.match(detail or "")
        serial = None
        if got is not None:
            try:
                serial = json.loads(got.group(2))
            except ValueError:
                serial = None
        if got is None or not isinstance(serial, str) or not serial:
            log.warning("brain: chunk_write_behind of %s was not read, so K3 does not know this drive was behind: %r",
                        session_id, (detail or "")[:300])
            return
        if got.group(1) != session_id:
            log.warning("brain: chunk_write_behind of %s names another wave (%s) and was not used: %r",
                        session_id, got.group(1), (detail or "")[:300])
            return
        with self._lock:
            self._behind.setdefault(session_id, {})[serial] = (int(got.group(4)), int(got.group(3)))

    def started(self, session_id: str, bits: object, probes: dict) -> object:
        """`ChunkEngine.wave_start`, אחרי שקצב ההתחלה נקבע: רושם את החיזוי, ומחזיר את ``bits`` כמו שהוא."""
        try:
            if self._mode() != OFF:
                self._predict(session_id, bits, probes)
        except Exception:                       # noqa: BLE001
            log.exception("brain: the wave ETA of %s was not predicted", session_id)
        return bits

    # -- פנים --

    def _image(self, session_id: str) -> tuple[int | None, int | None, str | None]:
        row = self.conn.execute("SELECT image_id FROM sessions WHERE id = ?", (session_id,)).fetchone()
        manifest = self.library.get(row["image_id"]) if row is not None and self.library is not None else None
        if not manifest:
            return None, None, "המניפסט לא נקרא"
        counts = [p.get("readback_chunks") for p in manifest.get("partitions") or [] if p.get("file")]
        size = manifest.get("total_compressed_bytes")
        if not counts or not all(isinstance(c, int) and not isinstance(c, bool) and c > 0 for c in counts):
            return None, None, "מספר החתיכות של האימג' לא ידוע"
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            return None, None, "הגודל הדחוס של האימג' לא ידוע"
        return sum(counts), size, None

    def _achieved_before(self, session_id: str, serials: frozenset[str], asked: int) -> tuple[int, str] | None:
        """#3550 + #3640: (``achieved_bps``, the wave) of the **last chunk-engine wave on the same drives** before this one, or None --
        no prediction from what was achieved. #3640 (J3b, 18/11): the wave before it ran on a Samsung and an APS, and the APS held the
        whole wave to 165Mb/s; J3b ran on two other Samsungs and took 346s, not the 897s K3 predicted from that rate. The rate a wave
        achieved is the rate of ITS drives, so a wave counts only when its drive set is exactly ``serials`` (this wave's: the write-checked
        drives and the ones K3 learned were behind). A wave whose drives are not recorded (`chunk.drives`, else `chunk.write_serials`),
        or an empty ``serials``, cannot be compared: it is not the base either (principle 5: "could not check" is not "same drives").
        A wave of the removed sender carries no `stream.chunk` and a chunk wave on other drives is not this wave's evidence: both are skipped. The last
        wave ON THESE DRIVES that did not end in `send_done` (failed, stopped), or whose `achieved_bps` is not a positive number: None,
        not an older wave -- which wave to take is not guessed.
        #3812 (Zeus 22/11): and only a wave that ASKED for at least ``asked`` (this wave's rate) counts: a lower wave proves nothing about
        a higher rate, and a higher one that ended below it proves the drives could not reach it. A wave whose ``asked_bps`` is not a
        positive number cannot be compared and is skipped like one on other drives (principle 5). 0.31 and 1.96 of 19/11 were a wave
        asked 892 read for one asking 400, and a wave asked 400 read for one asking 600."""
        if not serials:
            return None
        for row in self.conn.execute("SELECT session_id, stream FROM wave_rates WHERE session_id IS NOT ?"
                                     " ORDER BY rowid DESC LIMIT ?", (session_id, PREVIOUS_SCAN)):
            try:
                stream = json.loads(row["stream"]) if row["stream"] else None
            except (TypeError, ValueError):
                continue
            sample = stream.get("chunk") if isinstance(stream, dict) else None
            if not isinstance(sample, dict) or _wave_serials(sample) != serials:
                continue
            was = sample.get("asked_bps")
            if not isinstance(was, (int, float)) or isinstance(was, bool) or was < asked:
                continue                        # #3812: it asked for less than this wave (or not a number): not this wave's evidence
            got = sample.get("achieved_bps")
            if (sample.get("ended") != "send_done" or not isinstance(got, (int, float)) or isinstance(got, bool)
                    or got <= 0):
                return None
            return int(got), row["session_id"]
        return None

    def _rate_cap(self, probes: dict, bits: int, chunks: int, zbytes: int) -> tuple[int | None, str]:
        """#3599: (the most wire bps (compressed) the engine's in-wave feedback can take the stream to, the basis) -- or (None, "unmeasured").
        The same two decisions the engine puts on the command line: `--rate-max` (`chunksend.feedback_bounds`: 2 x the start `--write-kbps`,
        or the maximum K8 learned when that is higher) and, since #3616, `--rate-cap` (`chunksend.feedback_grow_cap`: the sender may GROW
        `--rate-max` up to the wire's capacity while every receiver is healthy, and only when K8 has no learned maximum for these drives).
        Basis `grow`: the ceiling can reach the wire (`bits`), so it binds nothing; `ceiling`: it cannot grow, `--rate-max` is the most.
        `--rate-max` is a rate of UNCOMPRESSED data, and the wire carries the image's compressed share of it (`zbytes` of `chunks` x 4MiB).
        J1 (17/11): 1,792,000 kbps = 224MB/s, a 4.3 ratio, a wire that never went above ~430Mb/s while the prediction said 647.
        This is the start as the write check sets it (or the measurement lock): the learning loop between waves (`chunk_rate.write_rate`)
        can move it, and K3 does not see that move. A caller without a drive floor (a test double), or any failure: (None, "unmeasured") --
        the prediction goes on without the cap, as before."""
        floor = getattr(self.chunk, "write_floor", None)
        if floor is None:
            return None, "unmeasured"
        try:
            from server.rounds import chunksend      # noqa: PLC0415 -- the engine imports the brain, not the other way round
            locked = chunksend._measured_write_kbps()
            write_kbps = locked if locked is not None else chunksend.probe_write_kbps(list(probes.values()), floor())
            ceiling = getattr(self.chunk, "write_ceiling", None)
            learned = ceiling(sorted({serial for _mac, serial in probes})) if ceiling is not None and probes else None
            learned = learned if isinstance(learned, int) and not isinstance(learned, bool) and learned > 0 else None
            high = chunksend.feedback_bounds(0, write_kbps, floor(), learned)[1]
            grow = chunksend.feedback_grow_cap(bits // 1000, learned, zbytes, chunks)
        except Exception:                       # noqa: BLE001 -- a cap that cannot be read is not a reason to lose the prediction
            log.exception("brain: the feedback ceiling of the wave ETA was not read")
            return None, "unmeasured"
        basis, top = ("grow", grow) if grow is not None and write_kbps and grow > high else ("ceiling", high)
        return (int(top * 1000 * zbytes / (chunks * CHUNK_BYTES)), basis) if top > 0 else (None, "unmeasured")

    def _mac_of(self, session_id: str, serial: str, probes: dict) -> str | None:
        for mac, s in probes:
            if s == serial:
                return mac
        from server.rounds import chunkledger    # noqa: PLC0415
        for row in self.conn.execute("SELECT mac FROM session_members WHERE session_id = ?", (session_id,)):
            if any(who[0] == serial for who in chunkledger.disks_of(self.conn, row["mac"]).values()):
                return row["mac"]
        return None

    def _no_history(self, probes: dict, behind: dict) -> tuple[str, ...]:
        """#3574 (RD2-1): serials in this wave with no `write_history`, when another drive has one.

        The engine's `write_history` is the hook that decided who was left out. A drive named in `chunk_write_behind`
        has one: that line is the history. A drive the hook answers None for has none. No hook (not wired): nothing
        was read, so this does not invent a mix. An answer that is not a history and not None was not read -- a log
        line, not "no history"."""
        hook = getattr(self.chunk, "write_history", None)
        if hook is None:
            return ()
        serials = sorted((set(behind) | {serial for _mac, serial in probes}) - {""})
        if len(serials) < 2:
            return ()
        missing: list[str] = []
        has = False
        for serial in serials:
            if serial in behind:
                has = True
                continue
            try:
                got = hook(serial)
            except Exception:                   # noqa: BLE001 -- the brain never raises into the wave
                log.exception("brain: the write history of %s was not read", serial)
                continue
            if got is None:
                missing.append(serial)
                continue
            if (isinstance(got, tuple) and len(got) == 2 and isinstance(got[0], int) and not isinstance(got[0], bool)
                    and got[0] > 0 and isinstance(got[1], str)):
                has = True
                continue
            log.warning("brain: the write history of %s was not a history, so K3 did not count it: %r", serial, got)
        return tuple(missing) if has and missing else ()

    def _predict(self, session_id: str, bits: object, probes: dict) -> None:
        with self._lock:
            behind = dict(self._behind.get(session_id, {}))
            if session_id in self._open:
                return                           # פעם אחת לגל
        no_file = getattr(self.chunk, "no_file", None)
        chunks, zbytes, why = self._image(session_id)
        if no_file is not None and no_file(session_id):
            why = "הפצה ישירה: אין קובץ להשלים ממנו ב-HTTP"
        elif not isinstance(bits, int) or isinstance(bits, bool) or bits <= 0:
            why = "קצב הזרם אינו מספר חיובי"
        if why is not None:
            memory.record(self.conn, mode=self._mode(), rule=RULE, action=PREDICT, verdict="UNPREDICTED",
                          reason=f"אין חיזוי: {why}", round_id=session_id, entity=("round", session_id),
                          outcome="לא נמדד")
            with self._lock:
                self._open[session_id] = (None, self._clock(), Forecast(0.0, 0.0, ()))
            return
        macs = {serial: self._mac_of(session_id, serial, probes) for serial in sorted(behind)}
        drives = set(probes) | {(mac, serial) for serial, mac in macs.items()}
        # שכנה = מגירה אחרת באותו משכפל שאינה איטית בעצמה: היא מסיימת את המעבר הראשון, ויש לה מה לתת
        slow = [Slow(serial, behind[serial][0], NEIGHBOR if mac is not None and any(
            m == mac and other != serial and other not in behind for m, other in drives) else HTTP, mac)
            for serial, mac in macs.items()]
        before = self._achieved_before(session_id, frozenset(serial for _mac, serial in probes) | frozenset(behind), bits)
        gap = self._no_history(probes, behind)                          # #3574: one drive has no write history, another has one
        used = bits if before is None else min(bits, before[0])         # #3550: not faster than the last wave managed
        uncapped = used
        cap, basis = self._rate_cap(probes, bits, chunks, zbytes)
        bound = cap is not None and basis == "ceiling" and cap < used     # `grow`: the ceiling reaches the wire, it binds nothing (and rounds below it)
        if bound:
            used = cap                                                  # #3599: nor than the feedback's own `--rate-max` lets it
        f = predict(chunks, zbytes, used, slow)
        learned = None
        if self._learned_rate is not None:
            members = [r["mac"] for r in self.conn.execute("SELECT mac FROM session_members WHERE session_id = ?",
                                                           (session_id,))]
            learned = self._learned_rate(session_id, bits, members)
        evidence = (f"eta_s={f.eta_s:.0f}", f"t1_s={f.t1_s:.0f}", f"chunks={chunks}", f"zbytes={zbytes}",
                    f"stream_mbps={used / 1e6:.0f}", f"asked_mbps={bits / 1e6:.0f}",
                    f"rate_cap_mbps={'unmeasured' if cap is None else f'{cap / 1e6:.0f}'}", f"t1_cap_bound={int(bound)}", f"rate_cap_basis={basis}",
                    f"achieved_before_mbps={'unmeasured' if before is None else f'{before[0] / 1e6:.0f}'}",
                    f"achieved_before_match={'none' if before is None else 'drives'}",
                    *(() if before is None else (f"achieved_before_wave={before[1]}",)), f"drives={len(drives)}", f"behind={len(slow)}",
                    *(f"no_history={serial}" for serial in gap), *(("confidence=low",) if gap else ()),
                    *(f"drive={serial}:{wrote:.0f}MB/s:{source}:{t:.0f}s" for serial, wrote, source, t in f.drives),
                    *(() if not learned else (f"learned_stream_mbps={learned / 1e6:.0f}",
                                              f"learned_eta_s={predict(chunks, zbytes, learned, slow).eta_s:.0f}")))
        slowest = max(f.drives, key=lambda d: d[3], default=None)
        reason = (f"ETA חזוי {f.eta_s / 60:.0f} דק': המעבר הראשון בקצב המהירים {f.t1_s / 60:.0f} דק'"
                  + (f", והכונן האיטי {slowest[0]} ({slowest[1]:.0f}MB/s) משלים "
                     f"{'מהשכנה' if slowest[2] == NEIGHBOR else 'ב-HTTP'} עד {slowest[3] / 60:.0f} דק'" if slowest else
                     ", ואין כונן שפיגר בגל קודם")
                  + (f". הקצב שהושג בגל הקודם על אותם כוננים {before[0] / 1e6:.0f}Mb/s, לא המבוקש {bits / 1e6:.0f}Mb/s"
                     if before is not None and before[0] < bits else "")
                  + (". אין גל קודם על אותם כוננים: החיזוי לפי הקצב המבוקש, ביטחון נמוך" if before is None else "")
                  + (f". תקרת המשוב (`--rate-max`) מגבילה את הקו ל-{cap / 1e6:.0f}Mb/s, לא ל-{uncapped / 1e6:.0f}Mb/s"
                     if bound else "")
                  + (f". לכונן {', '.join(gap)} אין היסטוריית כתיבה, ולכונן אחר יש: ביטחון נמוך" if gap else "")
                  + (f". בקצב היכולת הנלמדת ({learned / 1e6:.0f}Mb/s) חזוי "
                     f"{predict(chunks, zbytes, learned, slow).eta_s / 60:.0f} דק'" if learned else "")
                  + ". חיזוי בלבד: שום קצב לא זז")
        row = memory.record(self.conn, mode=self._mode(), rule=RULE, action=PREDICT, verdict="PREDICT", reason=reason,
                            evidence=evidence, round_id=session_id, entity=("round", session_id))
        with self._lock:
            self._open[session_id] = (row, self._clock(), f)
        log.info("brain %s", json.dumps({"rule": RULE, "action": PREDICT, "round": session_id, "reason": reason,
                                          "evidence": list(evidence), "say": "חיזוי בלבד"}, ensure_ascii=False))

    def _finish(self, session_id: str, event: str) -> None:
        with self._lock:
            got = self._open.pop(session_id, None)
            self._behind.pop(session_id, None)
        if got is None or got[0] is None:
            return
        row, began, f = got
        if event != "send_done":
            memory.resolve(self.conn, [row], f"הגל הסתיים ב-{event}: ה-ETA בפועל לא נמדד")
            return
        actual = self._clock() - began
        outcome = self.chunk.rate_outcome(session_id) if self.chunk is not None else {}
        first = outcome.get("first_pass_s") if isinstance(outcome, dict) else None
        first = float(first) if isinstance(first, (int, float)) and not isinstance(first, bool) and first > 0 else None
        ratio = actual / f.eta_s if f.eta_s > 0 else None
        evidence = (f"predicted_s={f.eta_s:.0f}", f"actual_s={actual:.0f}",
                    f"ratio={'unmeasured' if ratio is None else f'{ratio:.2f}'}", f"t1_predicted_s={f.t1_s:.0f}",
                    f"t1_actual_s={'unmeasured' if first is None else f'{first:.0f}'}", f"prediction={row}")
        memory.record(self.conn, mode=self._mode(), rule=RULE, action=ACTUAL, verdict="MEASURED", evidence=evidence,
                      reason=f"ETA בפועל {actual:.0f}s מול חזוי {f.eta_s:.0f}s"
                             + (f" (יחס {ratio:.2f})" if ratio is not None else ""),
                      round_id=session_id, entity=("round", session_id), outcome="נמדד")
        memory.resolve(self.conn, [row], f"בפועל {actual:.0f}s מול חזוי {f.eta_s:.0f}s"
                                         + (f" (יחס {ratio:.2f})" if ratio is not None else ""))
