"""#3064, K3, קבוצה ד': שלד שערי פתיחת הסבב (`server/brain/gates.py`), בצל.

מה נבדק (לא את השערים עצמם -- אלה בקובצי הטסט שלהם):

- **הרישום:** עשרה שערים, מזהה אחד לכל אחד, וכל מזהה בקטלוג מצביע על הפונקציה של השער.
- **הרישום ב-`brain_decisions`:** `rule` = מזהה השער, `action` = `ROUND_GATE`, `point` = שם השער, `mode` = `shadow` תמיד
  (גם ב-`enforce`), והישות -- הסבב לשער כללי, או המשכפל.
- **עיקרון 5:** שער שזרק, שלא החזיר כלום, או שהחזיר פסק שאינו אחד מארבעת הפסקים = `UNDECIDED`, בשמו. לעולם לא `OPEN`.
- **`OPEN` של משכפלים מקופל** לשורה אחת לשער; כל פסק אחר נרשם בשם המשכפל.
- **`off`:** אף שער לא נקרא ושום דבר לא נרשם. שער שנפל אינו מפיל את פתיחת הסבב.
- **החיבור:** `room.open_round` מריץ את השערים עם המשכפלים שנבחרו, ו-`line_he` אומר אותם בעברית.

**בקרה שלילית:** ראו את גוף ה-PR.
"""

from __future__ import annotations

import threading
import time
import tomllib
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from server import db
from server.db import set_setting
from server.brain import gate_host, gate_machines, gate_round, gates, memory, say, veto
from server.brain.gate_core import BLOCK, OPEN, UNDECIDED, WARN, Reading
from test_server_room import (  # noqa: F401 -- room_server הוא fixture
    CLONER1, CLONER2, room_body, room_server,
)

#: ה-fixture האוטומטי של conftest מחליף את `gate_core.health_rows` ב-`None` (ה-collect האמיתי של /health מחכה 13 ש' לפסקי זמן בכל
#: פתיחת סבב בטסטים); המודול הזה בודק את המקור האמיתי.
REAL_HEALTH_GATE = True

ROOT = Path(__file__).resolve().parent.parent
MAC1, MAC2 = "aa:bb:cc:00:00:21", "aa:bb:cc:00:00:22"
RID = "room_test0001"


@pytest.fixture()
def conn(tmp_path):
    veto.forget()
    c = db.connect(tmp_path / "gates.db")
    yield c
    veto.forget()


def only_gate(monkeypatch, read, gate_id="B49", name="raid"):
    """רק שער אחד ברישום: שאר השערים (שלדים) לא מפריעים לקריאה של הטסט."""
    monkeypatch.setattr(gates, "GATES", (gates.Gate(gate_id, name, "שער לבדיקה", read),))


def rows(conn, **kw):
    return memory.decisions(conn, round_id=RID, limit=100, **kw)


def test_the_ten_gates_are_registered_once_each():
    ids = [g.id for g in gates.GATES]
    assert len(ids) == 10 and len(set(ids)) == 10, ids
    assert len({g.name for g in gates.GATES}) == 10


def test_every_gate_is_in_the_catalog_and_points_at_its_function():
    items = {it["id"]: it for it in tomllib.loads((ROOT / "server/brain/catalog.toml").read_text(encoding="utf-8"))["item"]}
    modules = {"gate_host": gate_host, "gate_machines": gate_machines, "gate_round": gate_round}
    for g in gates.GATES:
        item = items[g.id]
        if g.id == "B23":          # פריט שקדם לשער: הקטלוג שלו מצביע על `agentsenses.ahci_gate` (שער ה-hello); `gate_machines.ahci` נרשם בו ב-#3476
            continue
        stem = Path(item["file"]).stem
        assert item["file"] == f"server/brain/{stem}.py" and stem in modules, (g.id, item["file"])
        assert getattr(modules[stem], item["symbol"]) is g.read, f"{g.id}: הקטלוג מצביע על {item['symbol']}, והרישום על פונקציה אחרת"
        assert item["mode"] == "shadow" and "K3" in item["rules"], g.id


def test_the_log_line_says_the_action_and_the_blocking_verdict_in_hebrew():
    line = say.line_he({"entity_kind": "round", "entity_key": RID, "action": gates.ACTION, "verdict": BLOCK, "reason": "x", "mode": "shadow"})
    assert line.startswith(f"סבב {RID} → שער לפני פתיחת הסבב: חסימה") and "ROUND_GATE" not in line and "BLOCK" not in line, line


def test_a_round_level_reading_is_recorded_in_shadow_on_the_round(conn, monkeypatch):
    only_gate(monkeypatch, lambda c, m, r: [Reading(WARN, "מערך md0 פגוע", ("array=md0",))])
    got = gates.run(conn, RID, [MAC1])
    assert [(g.id, r.verdict) for g, r in got] == [("B49", WARN)]
    [d] = rows(conn)
    assert (d["rule"], d["action"], d["point"], d["verdict"], d["mode"]) == ("B49", "ROUND_GATE", "raid", "WARN", "shadow")
    assert (d["entity_kind"], d["entity_key"], d["reason"], d["evidence"]) == ("round", RID, "מערך md0 פגוע", ["array=md0"])
    assert d["outcome"] and "צל" in d["outcome"]


def test_enforce_mode_still_only_records(conn, monkeypatch):
    set_setting(conn, veto.SETTING, veto.ENFORCE)
    only_gate(monkeypatch, lambda c, m, r: [Reading(BLOCK, "המערך אינו פעיל")])
    gates.run(conn, RID, [MAC1])
    [d] = rows(conn)
    assert (d["verdict"], d["mode"]) == ("BLOCK", "shadow")


def test_machine_readings_are_recorded_by_name_and_the_open_ones_fold_into_one_row(conn, monkeypatch):
    only_gate(monkeypatch, lambda c, m, r: [Reading(OPEN, "תקין", (), MAC1), Reading(WARN, "כבל חשוד", ("crc=9",), MAC2), Reading(OPEN, "תקין", (), "aa:bb:cc:00:00:23")],
              "B54", "nic")
    gates.run(conn, RID, [MAC1, MAC2, "aa:bb:cc:00:00:23"])
    by = {(d["entity_kind"], d["entity_key"]): d for d in rows(conn)}
    assert set(by) == {("machine", MAC2), ("round", RID)}, by.keys()
    assert by[("machine", MAC2)]["verdict"] == "WARN"
    folded = by[("round", RID)]
    assert folded["verdict"] == "OPEN" and "נבדקו 2 משכפלים" in folded["reason"] and sorted(folded["evidence"]) == [f"mac={MAC1}", "mac=aa:bb:cc:00:00:23"]


def test_the_gate_gets_the_sorted_unique_roster(conn, monkeypatch):
    seen = []
    only_gate(monkeypatch, lambda c, m, r: seen.append((m, r)) or [Reading(OPEN, "ok")])
    gates.run(conn, RID, [MAC2, MAC1, MAC2, "", None])
    assert seen == [((MAC1, MAC2), RID)]


@pytest.mark.parametrize("read", [
    pytest.param(lambda c, m, r: 1 / 0, id="raised"),
    pytest.param(lambda c, m, r: [], id="empty"),
    pytest.param(lambda c, m, r: [Reading("FINE", "הכול טוב")], id="unknown-verdict"),
    pytest.param(lambda c, m, r: [Reading(OPEN, "   ")], id="no-reason"),
    pytest.param(lambda c, m, r: ["OPEN"], id="not-a-reading"),
])
def test_a_gate_that_did_not_answer_is_undecided_never_open(conn, monkeypatch, read):
    only_gate(monkeypatch, read)
    got = gates.run(conn, RID, [MAC1])
    assert [r.verdict for _g, r in got] == [UNDECIDED]
    [d] = rows(conn)
    assert d["verdict"] == "UNDECIDED" and d["reason"].startswith("לא הצלחנו לבדוק") and d["rule"] == "B49"


def test_off_reads_nothing_and_records_nothing(conn, monkeypatch):
    set_setting(conn, veto.SETTING, veto.OFF)
    called = []
    only_gate(monkeypatch, lambda c, m, r: called.append(1) or [Reading(OPEN, "ok")])
    assert gates.run(conn, RID, [MAC1]) == [] and called == [] and rows(conn) == []


def test_a_failing_recorder_does_not_break_the_round(conn, monkeypatch):
    only_gate(monkeypatch, lambda c, m, r: [Reading(OPEN, "ok")])
    monkeypatch.setattr(gates.memory, "record", lambda *a, **k: 1 / 0)
    assert gates.run(conn, RID, [MAC1]) == []


# --- החיבור ל-open_round ------------------------------------------------------------------------------------------


def test_opening_a_round_runs_the_gates_with_the_selected_cloners(room_server, monkeypatch):
    seen = []
    only_gate(monkeypatch, lambda c, m, r: seen.append((m, r)) or [Reading(WARN, "מערך md0 פגוע")])
    deploy, ctx = room_server["deploy"], room_server["ctx"]
    opened = deploy.post("/api/console/room", json=room_body(room_server, 3, {CLONER1: ["S1", "S2"], CLONER2: ["T1"]}))
    assert opened.status_code == 200, opened.text
    assert gates.wait_idle(10.0)
    [round_row] = ctx.conn.execute("SELECT id FROM room_rounds").fetchall()
    assert seen == [((CLONER1, CLONER2), round_row["id"])]
    [d] = memory.decisions(ctx.conn, round_id=round_row["id"], rule="B49", limit=10)
    assert (d["verdict"], d["entity_kind"], d["mode"]) == ("WARN", "round", "shadow")
    assert d["line_he"].endswith("(צל: רק נרשם)") and "שער לפני פתיחת הסבב" in d["line_he"]


def test_a_crashing_gate_does_not_stop_the_round_from_opening(room_server, monkeypatch):
    only_gate(monkeypatch, lambda c, m, r: 1 / 0)
    deploy = room_server["deploy"]
    assert deploy.post("/api/console/room", json=room_body(room_server, 2, {CLONER1: ["S1"]})).status_code == 200



def test_the_health_route_hands_its_own_collect_to_the_health_gate(tmp_path, images_root, clock):
    # B51: אותן שורות ואותם hooks שהמפעיל רואה ב-`/health` -- לא מדידה שנייה בצד. ה-collect נשאר האמיתי; ה-hooks מוזרקים
    # (כרטיסי הרשת של המכונה נחסמים בבדיקות, #113; בלי ss/systemctl/HTTP אמיתיים), כמו ב-`test_brain_cpucap_3456`.
    from fastapi.testclient import TestClient                                       # noqa: PLC0415

    from server.app import create_app                                               # noqa: PLC0415
    from server.brain import gate_core                                              # noqa: PLC0415
    from server.console import users                                                # noqa: PLC0415

    quiet = {"ss": lambda: "", "ss_tcp": lambda: "", "nft_ruleset": lambda: None, "unit_active": lambda name: "active",
             "http_get": lambda url: 200, "http_size": lambda url: (200, 31_000_000), "interfaces": lambda: [],
             "tftp_root": lambda: tmp_path, "udp_sender_pids": lambda: []}
    app = create_app(tmp_path / "data", images_root, "http://10.44.12.10:7712", now_fn=clock, health_hooks=quiet)
    ctx = app.state.ctx
    users.create(ctx.conn, "noc", "admin-pass-123", "admin", by="test", is_builtin=True, check_policy=False)
    admin = TestClient(app)
    admin.post("/api/console/login", json={"username": "noc", "password": "admin-pass-123"})
    rows_ = gate_core.health_rows(ctx.conn)
    assert rows_ and all({"id", "state", "detail"} <= set(r) for r in rows_), rows_
    assert {r["id"] for r in rows_} == {r["id"] for r in admin.get("/api/console/health").json()}


def test_a_health_source_that_was_not_wired_is_none_not_an_empty_list(conn):
    from server.brain import gate_core                                              # noqa: PLC0415

    assert gate_core.health_rows(conn) is None
    gate_core.attach_health(conn, lambda: [{"id": "x", "state": "ok", "detail": "d"}])
    assert gate_core.health_rows(conn) == [{"id": "x", "state": "ok", "detail": "d"}]


def test_opening_a_round_does_not_wait_for_the_gates_and_the_rows_still_arrive(room_server, monkeypatch):
    # השער נתקע על אירוע. אם `open_round` היה מחכה לו (קריאה מסונכרנת), הוא היה חוזר רק אחרי ש-`finished` נקבע
    started, release, finished = threading.Event(), threading.Event(), threading.Event()

    def slow(c, m, r):
        started.set()
        release.wait(10.0)
        finished.set()
        return [Reading(WARN, "מערך md0 פגוע")]

    only_gate(monkeypatch, slow)
    ctx = room_server["ctx"]
    opened = room_server["deploy"].post("/api/console/room", json=room_body(room_server, 2, {CLONER1: ["S1"]}))
    assert opened.status_code == 200, opened.text
    assert started.wait(5.0), "השער לא יצא לדרך"
    assert not finished.is_set(), "open_round חזר רק אחרי שהשער נגמר"
    [round_row] = ctx.conn.execute("SELECT id FROM room_rounds").fetchall()
    assert memory.decisions(ctx.conn, round_id=round_row["id"], rule="B49", limit=5) == []
    release.set()
    assert gates.wait_idle(10.0)
    [d] = memory.decisions(ctx.conn, round_id=round_row["id"], rule="B49", limit=5)
    assert (d["verdict"], d["mode"]) == ("WARN", "shadow")


def test_one_ceiling_for_all_the_gates_and_what_did_not_answer_is_undecided(conn, monkeypatch):
    stuck, release = threading.Event(), threading.Event()
    calls = []

    def hang(c, m, r):
        stuck.set()
        release.wait(10.0)
        return [Reading(OPEN, "מאוחר מדי")]

    monkeypatch.setattr(gates, "GATES", (gates.Gate("B49", "raid", "RAID", hang),
                                         gates.Gate("B60", "library_space", "מקום", lambda c, m, r: calls.append(1) or [Reading(OPEN, "ok")])))
    began = time.monotonic()
    got = gates.run(conn, RID, [MAC1], timeout_s=0.3)
    release.set()
    assert time.monotonic() - began < 5.0 and stuck.is_set()
    assert [(g.id, r.verdict) for g, r in got] == [("B49", UNDECIDED), ("B60", UNDECIDED)]
    assert "לא ענה בזמן" in got[0][1].reason and "לפני שהשער התחיל" in got[1][1].reason
    assert calls == [], "שער שנגמר לו הזמן לא אמור להתחיל"
    assert {d["rule"]: d["verdict"] for d in rows(conn)} == {"B49": "UNDECIDED", "B60": "UNDECIDED"}
