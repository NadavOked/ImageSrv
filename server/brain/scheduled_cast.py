"""Scheduled-Cast for room waves (#3569). Enrollment starts when the wave opens."""

import logging
from datetime import datetime, timedelta

from server.brain import memory, veto
from server.db import get_setting

log = logging.getLogger("imagesrv.brain")
SETTING = "brain_scheduled_cast"
MIN_CLONERS = 2
MAX_WAIT_S = 30


def judge(opened, observed, joined):
    """Compare a membership snapshot with a fixed deadline, at server clock precision."""
    try:
        start = datetime.fromisoformat(opened)
        deadline = start + timedelta(seconds=MAX_WAIT_S)
        if start.tzinfo is None or observed < start or joined is None:
            return "UNDECIDED", None
        expired = observed >= deadline
    except (TypeError, ValueError, OverflowError):
        return "UNDECIDED", None
    if joined >= MIN_CLONERS:
        verdict = "BOTH" if expired else "MINIMUM"
    else:
        verdict = "DEADLINE" if expired else "WAIT"
    return verdict, deadline.isoformat()


def enabled(conn):
    return veto.mode(conn) == veto.ENFORCE and get_setting(conn, SETTING) == "true"


def _joining(conn, store, wave, mac):
    """The cloner whose hello drives this pulse is about to join: its own hello records it right after the pulse."""
    from server.rounds import room_state     # noqa: PLC0415 -- room imports this module
    return (mac is not None and store.in_roster(wave, mac) and not store.is_member(wave["id"], mac)
            and room_state.has_fresh_drawers(conn, mac))


def hold(conn, store, wave, now, joiner=None):
    """True = do not start this wave now. ``joiner``: the hello's MAC, when the pulse runs before its join is recorded."""
    try:
        if enabled(conn):
            verdict, _ = judge(wave["created_at"], now, store.joined_count(wave["id"]))
            # At the deadline the wave goes out with whoever joined; a hello that is joining this very pulse is one of them.
            return verdict == "WAIT" or (verdict == "DEADLINE" and _joining(conn, store, wave, joiner))
    except Exception as exc:
        log.exception("Scheduled-Cast could not evaluate wave %s", wave["id"])
        _undecided(conn, wave, exc)
    return False        # no verdict reached = no hold: the wave starts as before the rule (the switch off), recorded UNDECIDED above


def _undecided(conn, wave, exc):
    """#4144: "could not evaluate" is not "no reason to hold" (principle 5): a row, not only a log line. Never raises."""
    try:
        memory.record(conn, mode=veto.SHADOW, rule="SCHEDULED_CAST", action="WAVE_START", point="hold",
                      entity=("round", wave["id"]), verdict="UNDECIDED",
                      reason="לא נבדק: ההערכה של Scheduled-Cast נכשלה, והגל מתחיל כמו כשהמתג כבוי",
                      evidence=(f"error={type(exc).__name__}: {exc}"[:200],),
                      outcome=memory.na("הכלל לא הוכרע; הגל מתחיל כמו לפני הכלל"))
    except Exception:
        log.exception("Could not record Scheduled-Cast UNDECIDED")


def started(conn, wave_id, event):
    """Called by the winning start transition. Enrollment is now closed."""
    try:
        if veto.mode(conn) == veto.OFF:
            return
        row = conn.execute(
            "SELECT r.id, s.created_at, s.started_at,"
            " (SELECT count(*) FROM session_members WHERE session_id=s.id) AS joined"
            " FROM sessions s JOIN room_rounds r ON r.wave_session_id=s.id WHERE s.id=?",
            (wave_id,)).fetchone()
        if row is None:
            return
        try:
            observed = datetime.fromisoformat(row["started_at"])
        except (TypeError, ValueError):
            observed = None
        verdict, deadline = judge(row["created_at"], observed, row["joined"])
        enforced = event == "session_start_auto" and enabled(conn)
        memory.record(conn, mode=veto.ENFORCE if enforced else veto.SHADOW, rule="SCHEDULED_CAST",
                      action="WAVE_START", point=event, round_id=row["id"], entity=("round", wave_id),
                      verdict=verdict, reason={
                          "MINIMUM": "מספיק משכפלים הצטרפו",
                          "DEADLINE": "זמן ההמתנה הסתיים",
                          "BOTH": "בעת ההתחלה שני התנאים התקיימו",
                          "WAIT": "ממתין לעוד משכפל או לסוף זמן ההמתנה",
                          "UNDECIDED": "זמן ההמתנה או מספר המצטרפים לא נמדד",
                      }[verdict],
                      evidence=(f"joined={row['joined']}", f"minimum={MIN_CLONERS}",
                                f"deadline={deadline or 'NOT_MEASURED'}", f"actual_start={row['started_at']}"),
                      outcome="הגל התחיל")
    except Exception:
        log.exception("Could not record Scheduled-Cast for %s", wave_id)
