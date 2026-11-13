"""שלב 9 — חדר השיכפולים: שלוש מגירות, כשל אחד, והגל הבא.

מחשב השיכפול מדווח שלושה דיסקים (מגירות) ב-hello ומקבל את הזרם פעם
אחת. בגל הראשון שתי מגירות נכתבות ואחת נכשלת — הכשל גלוי, המגירה
נשארת "טרייה", והגל השני שנפתח מעצמו משלים אותה עד היעד.

(בידוד הכשל ברמת הצינורות — fanout.c — נבדק ב-tests/test_fanout.py,
שרץ ב-CI על לינוקס. כאן נבדקת ההתנהגות של המערכת סביבו.)
"""

from __future__ import annotations

from .harness import check, wait_until

WAVE1 = [("sda", "done"), ("sdb", "done"),
         ("sdc", "failed", "queue overflow: target lost bytes")]
WAVE2 = [("sda", "done"), ("sdb", "done"), ("sdc", "done")]


def _room(ctx) -> dict:
    """Read-only observation. SimMachine's real hello loop drives the room."""
    if ctx.cloner.error is not None:
        raise SystemExit(f"FAILED: room heartbeat stopped: {ctx.cloner.error}")
    status, view = ctx.deploy.json("GET", "/api/console/room")
    if status != 200:
        raise SystemExit(f"FAILED: מסך החדר החזיר {status}: {view}")
    return view


def run(ctx) -> None:
    print("\n9. חדר השיכפולים — 3 מגירות, מגירה אחת נכשלת")
    ctx.cloner.write_plans = [WAVE1, WAVE2]
    ctx.cloner.start()
    # GET no longer supplies a heartbeat (#446). Keep the simulated agent
    # polling through both waves, including after its final progress report.
    check("cloner heartbeat enabled", not ctx.cloner.silent)

    view = wait_until(lambda: (v := _room(ctx))["machines"]
                      and v["machines"][0]["awake"] and v,
                      "מחשב השיכפול ער ונראה במסך החדר")
    check("שלוש מגירות דווחו", view["machines"][0]["drawers"] == 3,
          str(view["machines"][0]))
    slots = {d["port"]: d["dev"] for d in view["machines"][0]["drawer_list"]}
    check("כל מגירה נושאת את החריץ שלה, לא את סדר הגילוי",
          slots == {1: "sdb", 2: "sdc", 3: "sda"},
          str(view["machines"][0]["drawer_list"]))

    # #1410: בחירה מפורשת — כל שלוש המגירות של המכונה. #2786: עם שם מחשב לכל
    # מגירה, לפי SATA (פורט 1 = SATA 0 = הדיסק הראשון, המרצה).
    names = [{"mac": ctx.cloner.mac, "port": p, "name": n}
             for p, n in ((1, "LAB-INS"), (2, "LAB-01"), (3, "LAB-02"))]
    status, opened = ctx.deploy.json("POST", "/api/console/room",
                                     {"image_id": ctx.image_a, "target_drives": 3,
                                      "target_slots": [{"mac": ctx.cloner.mac, "ports": [1, 2, 3]}],
                                      "hostnames": {"names": names}})
    check("סבב חדר נפתח (יעד: 3 כוננים, שם מחשב לכל מגירה)", status == 200, str(opened))

    # הגל הראשון: המכונה מצטרפת, המגירות הטריות מכסות את היתרה — יוצא.
    wait_until(lambda: (v := _room(ctx))["round"]
               and v["round"]["wave_number"] == 2
               and v["round"]["written_drives"] == 2,
               "גל 1 הסתיים: שתי מגירות נכתבו, הכשל גלוי — נפתח גל 2")
    view = _room(ctx)
    check("המגירה שנכשלה נשארה טרייה ותיכתב שוב",
          view["machines"][0]["fresh_drawers"] == 1, str(view["machines"][0]))
    # sdc נכשלה — והטכנאי צריך לשמוע "המגירה האמצעית", לא אות (#27).
    check("הטכנאי מקבל את החריץ של המגירה שנכשלה (האמצעי)",
          [d["port"] for d in view["machines"][0]["drawer_list"] if d["fresh"]] == [2],
          str(view["machines"][0]["drawer_list"]))
    check("היתרה: כונן אחד", view["round"]["remaining_drives"] == 1,
          str(view["round"]))
    # #2786: כל מגירה שנכתבה קיבלה את השם של החריץ שלה; זו שנכשלה — לא קיבלה שם.
    check("גל 1: שם לכל מגירה לפי החריץ שלה, לא למגירה שנכשלה",
          ctx.cloner.named[:1] == [{"sdb": "LAB-INS", "sda": "LAB-02"}], str(ctx.cloner.named))

    # הגל השני משלים את המגירה — היעד הושג והסבב נסגר מעצמו.
    wait_until(lambda: _room(ctx)["round"] is None,
               "היעד הושג (3/3) והסבב נסגר מעצמו")
    # #2786: דיסק שנכשל שומר את המספר שלו — בגל 2 החריץ האמצעי מקבל את אותו LAB-01.
    check("גל 2: המגירה שנכשלה נכתבת עם השם של החריץ שלה",
          len(ctx.cloner.named) == 2 and ctx.cloner.named[1]["sdc"] == "LAB-01", str(ctx.cloner.named))
    status, overview = ctx.console.json("GET", "/api/console/overview")
    check("לא נשאר סבב פעיל במערכת", overview["session"] is None,
          str(overview["session"]))
