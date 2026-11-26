"""#3456 (K12): כוח העיבוד של השרת -- כמה גלים הוא מחזיק במקביל, והחלטה בצל לפני פתיחת סבב או גל.

הלב: **מה שלא נקרא הוא "לא נמדד", לא 0** (עיקרון 5). ליבות שלא נקראו, cgroup שאין, PSI שחסר, גל שמרכיב המעבד שלו
חסר -- כל אחד מהם נותן `None` ו-`UNDECIDED`, ולעולם לא "OPEN" או גל "חינם".

**בקרה שלילית:** עם **כל** קובצי הייצור שהשינוי נגע בהם מוחזרים לבסיס (`771ddfe2b`; `server/brain/cpucap.py` נמחק), הטסטים שמסומנים
(neg) נופלים על התנהגות: אין שורת `K12` בפתיחת סבב, הסבב נפתח גם כשהמתג דלוק, ההגדרה נדחית כ"לא מוכרת", אין שורת בריאות, ואין `unit_cpu`
בחלון. הטסטים הטהורים של `cpucap` (`pure`) מדולגים שם כי אין מה לייבא -- וזה **אינו** בקרה שלילית ואינו נספר כאחת.
"""

from __future__ import annotations

import json

import pytest

from server import db
from server.brain import memory, veto
from server.learn import server_senses as S

try:                                         # בקרה שלילית: בבסיס אין את המודול
    from server.brain import cpucap
except ImportError:                          # pragma: no cover
    cpucap = None

#: הטסטים הטהורים של המודול החדש. בבקרה השלילית הם מדולגים, ואלה שבשרת אמיתי (neg) הם שנכשלים.
pure = pytest.mark.skipif(cpucap is None, reason="negative control: server/brain/cpucap.py is absent")

PID = 4242
SETTING = "brain_cpu_gate"
LABEL = "המוח: שער מעבד לפני פתיחת גל"


@pytest.fixture(autouse=True)
def sixteen_cpus(monkeypatch):
    """הזיקה קבועה: מספר הליבות של מכונת הבדיקה אינו חלק מהטסט. (`raising=False`: אין `sched_getaffinity` בווינדוס.)"""
    if cpucap is not None:
        monkeypatch.setattr(cpucap.os, "sched_getaffinity", lambda pid: set(range(16)), raising=False)


def _forget():
    veto.forget()
    if cpucap is not None:
        cpucap.forget()


@pytest.fixture()
def conn(tmp_path):
    _forget()
    c = db.connect(tmp_path / "imagesrv.db")
    yield c
    _forget()


def _proc(root, *, psi=None, cgroup_path="/system.slice/imagesrv-server.service"):
    proc = root / "proc"
    (proc / "pressure").mkdir(parents=True, exist_ok=True)
    (proc / "self").mkdir(exist_ok=True)
    if psi is not None:
        (proc / "pressure" / "cpu").write_text(f"some avg10={psi:.2f} avg60=0.00 avg300=0.00 total=1\n")
    if cgroup_path is not None:
        (proc / "self" / "cgroup").write_text(f"0::{cgroup_path}\n")
    return proc


def _cgroup(root, *, usage=None, max_=None, path="system.slice/imagesrv-server.service"):
    here = root / "cg" / path
    here.mkdir(parents=True, exist_ok=True)
    if usage is not None:
        (here / "cpu.stat").write_text(f"usage_usec {usage}\nuser_usec 1\nsystem_usec 1\n")
    if max_ is not None:
        (here / "cpu.max").write_text(max_ + "\n")
    return root / "cg"


_n = [0]


def _wave(conn, *, unit=None, server=None, children=None, chunkcast=None, mbps=None, seconds=100.0, ended=True):
    """שורת גל ב-`server_io`, כמו ש-`Senses.end` כותב אותה. None = שדה שלא נקרא (null)."""
    _n[0] += 1
    at = f"2026-11-15T10:{_n[0] % 60:02d}:{_n[0] // 60:02d}+00:00"
    part = {"unit_cpu": unit, "server_cpu": server, "children_cpu": children, "chunkcast_cpu": chunkcast,
            "nic_tx_mbps": mbps}
    conn.execute("INSERT INTO server_io (kind, ref, started_at, start_json, ended_at, result_json)"
                 " VALUES ('wave', ?, ?, '{}', ?, ?)",
                 (f"ses_{_n[0]}", at, at if ended else None,
                  json.dumps({"seconds": seconds, "server": part}) if ended else None))
    conn.commit()


def _three(conn, cores=1.0, mbps=500.0):
    for _ in range(3):
        _wave(conn, unit=cores, mbps=mbps)


# --- החושים ---------------------------------------------------------------------------------------------------


@pure
def test_cores_are_the_affinity_or_the_units_quota_whichever_is_lower(tmp_path):
    proc = _proc(tmp_path)
    assert cpucap.cores(proc, _cgroup(tmp_path, max_="max 100000"), affinity=lambda: 8) == (8.0, "affinity")
    assert cpucap.cores(proc, _cgroup(tmp_path, max_="200000 100000"), affinity=lambda: 8) == (2.0, "cpu.max")
    # מכסה מעל הזיקה אינה מוסיפה ליבות
    assert cpucap.cores(proc, _cgroup(tmp_path, max_="900000 100000"), affinity=lambda: 4) == (4.0, "affinity")


@pure
def test_cores_that_were_not_read_are_none_not_zero_and_not_one(tmp_path):
    got, source = cpucap.cores(tmp_path / "nowhere", tmp_path / "nocg", affinity=lambda: None)
    assert got is None and source == "לא נקרא"
    assert cpucap.cores(tmp_path / "nowhere", tmp_path / "nocg", affinity=lambda: 0)[0] is None
    # מכסה פגומה אינה נקראת כ"ליבה אחת": הזיקה נשארת
    assert cpucap.cores(_proc(tmp_path), _cgroup(tmp_path, max_="garbage"), affinity=lambda: 6) == (6.0, "affinity")


@pure
def test_the_unit_cpu_is_read_from_its_cgroup_and_absent_is_none(tmp_path):
    proc = _proc(tmp_path)
    assert cpucap.unit_usage_usec(proc, _cgroup(tmp_path, usage=7_000_000)) == 7_000_000
    assert cpucap.unit_usage_usec(proc, tmp_path / "no-cgroup") is None
    assert cpucap.unit_usage_usec(_proc(tmp_path / "x", cgroup_path=None), _cgroup(tmp_path, usage=1)) is None


@pure
def test_psi_is_the_avg10_fraction_and_a_missing_file_is_none(tmp_path):
    assert cpucap.psi_avg10(_proc(tmp_path, psi=12.5)) == 0.125
    assert cpucap.psi_avg10(_proc(tmp_path / "other")) is None


def test_the_server_senses_window_carries_the_unit_cpu_and_null_when_it_was_not_read(tmp_path):
    """(neg) V16: 4 s of CPU over 2 s of wall = 2 cores; one reading without the cgroup = null, not 0."""
    proc = _proc(tmp_path)
    cg1 = _cgroup(tmp_path, usage=1_000_000)
    def read(cg, at):
        try:
            got = S.reading(root=tmp_path, interface=None, proc=proc, sys_net=tmp_path / "net", pid=PID, cgroup=cg)
        except TypeError as exc:                                      # בבסיס: אין חוש V16
            pytest.fail(f"server_senses does not read the unit's cgroup: {exc}")
        got["at"] = at
        return got
    a = read(cg1, 100.0)
    _cgroup(tmp_path, usage=5_000_000)
    b = read(cg1, 102.0)
    assert S.window(a, b)["unit_cpu"] == 2.0
    c = read(tmp_path / "no-cgroup", 104.0)
    assert S.window(b, c)["unit_cpu"] is None
    assert "unit_cpu" in S.PEAKS


# --- הלמידה ---------------------------------------------------------------------------------------------------


@pure
def test_the_cost_of_a_wave_is_learned_as_the_median_of_the_waves_that_were_measured(conn):
    for cores in (1.0, 2.0, 9.0):
        _wave(conn, unit=cores, mbps=cores * 100, seconds=60 * cores)
    got = cpucap.learn(conn)
    assert (got.waves, got.skipped, got.per_wave, got.per_mbps, got.seconds) == (3, 0, 2.0, 0.01, 120.0)
    assert cpucap.ceiling(8.0, got) == 3                      # floor(8 x 0.75 / 2)


@pure
def test_a_wave_whose_cpu_or_rate_was_not_read_is_skipped_not_counted_as_zero(conn):
    _three(conn, cores=2.0)
    _wave(conn, unit=None, server=1.0, children=None, chunkcast=1.0, mbps=500.0)    # children_cpu לא נקרא
    _wave(conn, unit=0.5, mbps=None)                                                # קצב לא נקרא
    _wave(conn, unit=0.5, mbps=0.0)                                                 # 0 Mb/s אינו קצב שנמדד
    _wave(conn, unit=0.5, mbps=100.0, ended=False)                                  # גל שעוד לא נגמר
    got = cpucap.learn(conn)
    assert (got.waves, got.skipped, got.per_wave) == (3, 3, 2.0)


@pure
def test_without_the_cgroup_the_three_process_counters_are_summed_only_when_all_were_read(conn):
    _wave(conn, server=0.5, children=0.25, chunkcast=1.25, mbps=100.0)
    one = cpucap.learn(conn)
    assert (one.waves, one.per_wave) == (1, 2.0) and cpucap.ceiling(8.0, one) is None     # נספר, אבל פחות מ-3
    assert cpucap.wave_cores({"server": {"server_cpu": 0.5, "children_cpu": 0.25, "chunkcast_cpu": 1.25}}) == 2.0
    assert cpucap.wave_cores({"server": {"server_cpu": 0.5, "children_cpu": None, "chunkcast_cpu": 1.25}}) is None
    assert cpucap.wave_cores({"server": {"unit_cpu": 0.0}}) == 0.0                 # מדידה של אפס היא מדידה
    assert cpucap.wave_cores({}) is None


@pure
def test_the_ceiling_needs_cores_and_at_least_three_waves(conn):
    assert cpucap.ceiling(None, cpucap.learn(conn)) is None
    _wave(conn, unit=1.0, mbps=100.0)
    _wave(conn, unit=1.0, mbps=100.0)
    assert cpucap.ceiling(8.0, cpucap.learn(conn)) is None
    _wave(conn, unit=1.0, mbps=100.0)
    assert cpucap.ceiling(8.0, cpucap.learn(conn)) == 6
    assert cpucap.ceiling(1.0, cpucap.learn(conn)) == 0       # אפילו גל אחד חורג מ-0.75


# --- ההכרעה ---------------------------------------------------------------------------------------------------


def _learned(waves=3, per_wave=1.0, per_mbps=0.002):
    return cpucap.Learned(waves, 0, per_wave, per_mbps, 100.0)


@pure
def test_nothing_measured_is_undecided_and_says_what_is_missing():
    cases = [(dict(n_cores=None, learned=_learned(), psi=0.0), "הליבות"),
             (dict(n_cores=8.0, learned=_learned(waves=2), psi=0.0), "עוד לא נלמד"),
             (dict(n_cores=8.0, learned=_learned(), psi=None), "PSI")]
    for kw, word in cases:
        verdict, reason, _ev = cpucap.judge(source="affinity", live=0, **kw)
        assert verdict == cpucap.UNDECIDED and word in reason, (kw, verdict, reason)


@pure
def test_open_while_there_is_room_and_wait_when_the_ceiling_is_reached():
    # 8 ליבות x 0.75 / 2 = 3 גלים
    kw = dict(n_cores=8.0, source="affinity", learned=_learned(per_wave=2.0), psi=0.05)
    assert cpucap.judge(live=0, **kw)[0] == cpucap.OPEN
    assert cpucap.judge(live=2, **kw)[0] == cpucap.OPEN
    verdict, reason, evidence = cpucap.judge(live=3, eta_s=42.0, **kw)
    assert verdict == cpucap.WAIT and "~3 גלים" in reason and "42" in reason, reason
    assert "ceiling=3" in evidence


@pure
def test_a_busy_cpu_waits_even_when_there_is_no_wave_and_a_single_wave_over_the_ceiling_still_opens():
    base = dict(n_cores=8.0, source="affinity", learned=_learned(per_wave=2.0))
    assert cpucap.judge(psi=0.35, live=0, **base)[0] == cpucap.WAIT
    over = dict(n_cores=1.0, source="affinity", learned=_learned(per_wave=2.0), psi=0.0)
    verdict, reason, _ev = cpucap.judge(live=0, **over)
    assert verdict == cpucap.OPEN and "אין ממה להמתין" in reason
    assert cpucap.judge(live=1, **over)[0] == cpucap.WAIT


@pure
def test_busy_psi_waits_with_no_ceiling():
    verdict, reason, evidence = cpucap.judge(n_cores=8.0, source="affinity", learned=_learned(per_wave=0.0),
                                            psi=cpucap.PSI_BUSY, live=1)
    assert verdict == cpucap.WAIT and "PSI" in reason
    assert "ceiling=None" in evidence


# --- ההחלטה בצל ------------------------------------------------------------------------------------------------


def _ready(conn, tmp_path, *, psi=0.0, cores_max="200000 100000"):
    """3 גלים של ליבה אחת, ושרת עם 2 ליבות (cpu.max): התקרה floor(2 x 0.75 / 1) = 1."""
    _three(conn, cores=1.0)
    return _proc(tmp_path, psi=psi), _cgroup(tmp_path, max_=cores_max)


def _rows(conn):
    return [d for d in memory.decisions(conn, rule="K12", limit=50) if d["action"] == "CPU_GATE"]


@pure
def test_a_decision_is_recorded_in_shadow_with_its_evidence_and_does_not_hold(conn, tmp_path):
    """(neg) בלי `cpucap.gate` אין שורת K12, ו-`open_round` נפתח בלי לשאול."""
    proc, cg = _ready(conn, tmp_path)
    got = cpucap.gate(conn, point="open_round", live=1, proc=proc, cgroup=cg)
    assert got.verdict == cpucap.WAIT and got.hold is False
    (row,) = _rows(conn)
    assert (row["verdict"], row["mode"], row["point"]) == ("WAIT", "shadow", "open_round")
    assert row["outcome"] == cpucap.SHADOW_HE and "ceiling=1" in row["evidence"]
    assert row["line_he"].startswith("המוח (כללי) → כוח העיבוד של השרת: להמתין")


@pure
def test_only_the_gate_switch_holds_and_only_a_wait(conn, tmp_path):
    proc, cg = _ready(conn, tmp_path)
    db.set_setting(conn, cpucap.SETTING, "true")
    held = cpucap.gate(conn, point="open_round", live=1, proc=proc, cgroup=cg)
    assert held.verdict == cpucap.WAIT and held.hold is True and "השרת עמוס" in held.he
    assert _rows(conn)[0]["outcome"] == cpucap.HELD_HE
    # OPEN אינו מוחזק, גם כשהמתג דלוק
    assert cpucap.gate(conn, point="attach_wave", live=0, proc=proc, cgroup=cg).hold is False


@pure
def test_undecided_never_holds_even_with_the_switch_on(conn, tmp_path):
    db.set_setting(conn, cpucap.SETTING, "true")                  # אין גלים, אין PSI, אין cgroup: כלום לא נמדד
    got = cpucap.gate(conn, point="open_round", live=5, proc=tmp_path / "nowhere", cgroup=tmp_path / "nocg")
    assert got.verdict == cpucap.UNDECIDED and got.hold is False
    assert [r["verdict"] for r in _rows(conn)] == ["UNDECIDED"]


@pure
@pytest.mark.parametrize("raw", [None, "false", "True", "1", "on", ""])
def test_the_switch_is_on_only_for_the_exact_string_true(conn, raw):
    if raw is not None:
        db.set_setting(conn, cpucap.SETTING, raw)
    assert cpucap.gate_on(conn) is False


@pure
def test_the_brain_switch_off_computes_and_records_nothing(conn, tmp_path):
    proc, cg = _ready(conn, tmp_path)
    db.set_setting(conn, veto.SETTING, "off")
    got = cpucap.gate(conn, point="open_round", live=1, proc=proc, cgroup=cg)
    assert got.verdict == cpucap.NOT_RUN and got.hold is False and _rows(conn) == []


@pure
def test_the_same_decision_is_recorded_once_per_window_and_again_after_it(conn, tmp_path):
    proc, cg = _ready(conn, tmp_path)
    clock = iter([0.0, 5.0, 31.0])
    for _ in range(3):
        cpucap.gate(conn, point="attach_wave", live=1, proc=proc, cgroup=cg, mono=lambda: next(clock))
    assert len(_rows(conn)) == 2


@pure
def test_a_failure_inside_the_gate_never_raises_into_the_round(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(cpucap, "learn", lambda c: (_ for _ in ()).throw(RuntimeError("boom")))
    got = cpucap.gate(conn, point="open_round", proc=tmp_path, cgroup=tmp_path)
    assert got.verdict == cpucap.UNDECIDED and got.hold is False


@pure
def test_the_eta_of_a_wait_is_the_median_wave_minus_what_has_passed(conn, tmp_path):
    proc, cg = _ready(conn, tmp_path)
    assert cpucap.gate(conn, point="open_round", live=1, proc=proc, cgroup=cg, eta=lambda: 75.0).eta_s == 75.0
    row = _rows(conn)[0]
    assert (row["predicted"], row["unit"]) == (75.0, "s")
    # בלי חיזוי חיצוני ובלי גל חי ב-`server_io`: אין חיזוי, ולא "0 שניות"
    cpucap.forget()
    assert cpucap.gate(conn, point="open_round", live=1, proc=proc, cgroup=cg).eta_s is None


# --- בריאות והגדרות --------------------------------------------------------------------------------------------


@pure
def test_the_summary_says_not_learned_until_three_waves_and_then_how_many_waves_it_holds(conn, tmp_path):
    proc, cg = _proc(tmp_path), _cgroup(tmp_path, max_="400000 100000")
    state, detail = cpucap.summary(conn, proc, cg)
    assert state == "info" and "עדיין לא נלמד" in detail
    _three(conn, cores=1.0)
    state, detail = cpucap.summary(conn, proc, cg)
    assert state == "ok" and "עד ~3 גלים במקביל (נלמד מ-3 גלים)" in detail, detail


@pure
def test_the_summary_when_even_one_wave_is_over_the_ceiling_is_a_warning(conn, tmp_path):
    _three(conn, cores=4.0)
    state, detail = cpucap.summary(conn, _proc(tmp_path), _cgroup(tmp_path, max_="200000 100000"))
    assert state == "warn" and "אפילו גל אחד חורג" in detail


def test_the_switch_is_a_console_setting_off_by_default_with_a_label_and_admin_only(server):
    """(neg) בלי `brain_cpucap.SETTING` ב-`WRITE_SETTINGS` ההגדרה נדחית כ"לא מוכרת"."""
    admin = server["admin"]
    assert admin.get("/api/console/settings").json()[SETTING] in (None, "false")
    assert admin.post("/api/console/settings", json={SETTING: "true"}).status_code == 200
    assert admin.get("/api/console/settings").json()[SETTING] == "true"
    assert db.get_setting(server["ctx"].conn, SETTING) == "true"
    assert admin.post("/api/console/settings", json={SETTING: "on"}).status_code == 400
    assert server["deploy"].post("/api/console/settings", json={SETTING: "false"}).status_code == 403
    texts = [r["text"] for r in admin.get("/api/console/journal", params={"event": "setting_change"}).json()]
    assert f"{LABEL}: פעיל" in texts, texts


def test_every_new_brain_label_has_a_hebrew_line():
    from server.brain import say
    assert "CPU_GATE" in say.ACTION_HE and {"WAIT", "UNDECIDED"} <= set(say.VERDICT_HE)


def test_health_gets_the_cpu_row_only_when_the_hook_is_injected(tmp_path, images_root, clock):
    """(neg) בלי השורה ב-`health.collect` ה-hook לא מגיע לקונסולה."""
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from server.app import create_app
    from server.console import users
    ran = []

    def hook():
        ran.append(1)
        return {"id": "brain_cpu", "label": "כוח העיבוד של השרת (המוח)", "state": "ok",
                "detail": "השרת מחזיק עד ~3 גלים במקביל (נלמד מ-5 גלים)"}

    quiet = {"ss": lambda: "", "ss_tcp": lambda: "", "nft_ruleset": lambda: None, "unit_active": lambda name: "active",
             "http_get": lambda url: 200, "http_size": lambda url: (200, 31_000_000), "interfaces": lambda: [],
             "tftp_root": lambda: tmp_path, "udp_sender_pids": lambda: []}      # בלי ss ו-systemctl אמיתיים
    for hooks, want in ((quiet, False), ({**quiet, "brain_cpu": hook}, True)):
        app = create_app(tmp_path / f"d{want}", images_root, "http://10.44.12.10:7712", now_fn=clock,
                         health_hooks=hooks)
        users.create(app.state.ctx.conn, "noc", "admin-pass-123", "admin", by="test", is_builtin=True,
                     check_policy=False)
        client = TestClient(app)
        client.post("/api/console/login", json={"username": "noc", "password": "admin-pass-123"})
        rows = {r["id"]: r for r in client.get("/api/console/health").json()}
        assert ("brain_cpu" in rows) is want, rows.keys()
        if want:
            assert "עד ~3 גלים במקביל" in rows["brain_cpu"]["detail"] and ran


# --- הנקודות שבהן הסבב נפתח ------------------------------------------------------------------------------------

from test_server_room import CLONER1, room_body, room_server   # noqa: E402,F401 -- room_server הוא fixture


def _busy(monkeypatch):
    if cpucap is not None:                                                  # (בבקרה השלילית אין מה לתקן, והסבב ייפתח)
        monkeypatch.setattr(cpucap, "psi_avg10", lambda *a, **k: 0.5)       # מחסור במעבד, לא תלוי במכונת הבדיקה
    _forget()


def _open(room_server):
    return room_server["deploy"].post("/api/console/room", json=room_body(room_server, 1, {CLONER1: ["S1"]}))


def test_opening_a_round_records_the_decision_and_opens_it_while_the_switch_is_off(room_server, monkeypatch):
    """(neg) בלי הקריאה ב-`open_round` אין שורת K12 בפתיחה."""
    _busy(monkeypatch)
    _three(room_server["ctx"].conn)
    assert _open(room_server).status_code == 200
    (row,) = [d for d in _rows(room_server["ctx"].conn) if d["point"] == "open_round"]
    assert row["verdict"] == "WAIT" and "צל" in row["outcome"] and row["mode"] == "shadow"


def test_opening_a_round_is_refused_with_the_reason_only_when_the_switch_is_on_and_the_verdict_is_wait(room_server, monkeypatch):
    """(neg) על הקוד הישן הסבב נפתח, והטסט נופל על 200."""
    _busy(monkeypatch)
    conn = room_server["ctx"].conn
    _three(conn)
    db.set_setting(conn, SETTING, "true")
    refused = _open(room_server)
    assert refused.status_code == 409 and "השרת עמוס" in refused.json()["detail"], refused.text
    assert conn.execute("SELECT COUNT(*) AS n FROM room_rounds").fetchone()["n"] == 0
    db.set_setting(conn, SETTING, "false")
    _forget()
    assert _open(room_server).status_code == 200


def test_a_round_still_opens_with_the_switch_on_when_nothing_was_measured(room_server):
    """אין גלים שנלמדו: `UNDECIDED`, והמתג אינו מחזיק על "לא יודע"."""
    db.set_setting(room_server["ctx"].conn, SETTING, "true")
    assert _open(room_server).status_code == 200
    assert [d["verdict"] for d in _rows(room_server["ctx"].conn)] == ["UNDECIDED"]


def test_the_next_wave_is_not_opened_while_the_gate_holds_and_is_tried_again_by_the_next_pulse(room_server, monkeypatch):
    """(neg) `_attach_wave`: בלי התנאי הוא היה קורא ל-`store.open` מיד."""
    from server.rounds import room as room_mod
    _busy(monkeypatch)
    conn = room_server["ctx"].conn
    _three(conn)

    class Store:
        opened = 0

        def active_for_group(self, group):
            return None

        def active_pulls(self):
            return []

        def open(self, *a, **k):
            Store.opened += 1
            raise room_mod.SessionError("taken")

    round_row = {"id": "room_t", "image_id": "img", "expand_partition": None, "target_slots_json": None,
                 "wave_session_id": None, "wave_number": 0}
    room_mod._attach_wave(conn, Store(), round_row, 0)               # המתג כבוי: מנסה לפתוח
    assert Store.opened == 1
    db.set_setting(conn, SETTING, "true")
    _forget()
    room_mod._attach_wave(conn, Store(), round_row, 0)               # דלוק ו-WAIT: לא מנסה
    assert Store.opened == 1
    assert [d["point"] for d in _rows(conn)].count("attach_wave") == 2
