"""#3064 (N4, N6, N8): החושים של רשת ההפצה -- מה שאפשר לראות מהשרת ומהמשכפל, **בלי גישה לסוויץ'**.

עיקרון 5 הוא העיקר כאן: "לא נמצא querier" (האזנו, ויכולנו לשמוע) אינו "לא בדקנו" (חלון קצר, חומת אש, סוכן ישן), ו"אין
הצפה" אינו "לא היה זרם". כל חוש נבדק בכל המצבים שלו, והכללים שמונעים לקפל אותם יחד.

- **הרישום:** ``brain_decisions`` ב-``mode=shadow``, פעם אחת לכל **שינוי** מצב, ואף החלטה לא נשענת עליו.
- **ה-hello:** `netwatch` כשדה אח של `probe`, נשמר ב-`machine_probe`, ואינו "גרסה" (מונים של חלון אחד).
- **המאזין של השרת:** המסנן ב-BPF (נבדק במפרש קטן שכאן), הפרוסות, והחוש.
- **ה-`Querier`:** `listening_since` ו-`sense()`.
"""

from __future__ import annotations

import threading

import pytest

pytest.importorskip("fastapi")

from conftest import setup_classroom  # noqa: E402
from server import db  # noqa: E402
from server.agent import probe  # noqa: E402
from server.brain import l2frames, l2sense, memory, veto  # noqa: E402
from server.net import igmp_querier as q, netwatch  # noqa: E402

from test_igmp_querier_1972 import Clock as QClock, ip_packet, make as make_q, run as run_q  # noqa: E402
from test_netwatch_c_3064 import BCAST, ME_NOT, FRAMES, eth, mcast_frame  # noqa: E402

MAC = "b4:2e:99:07:1a:c4"
GROUP = "239.84.82.1"


@pytest.fixture(autouse=True)
def _fresh_state():
    l2sense._last.clear()
    l2sense._seen_sample.clear()
    l2sense.set_server_view(lambda: None)
    veto.forget()
    yield
    l2sense._last.clear()
    l2sense._seen_sample.clear()
    l2sense.set_server_view(lambda: None)
    veto.forget()


def sample(**over) -> dict:
    """דגימה של `imagesrv-netwatch` כפי שה-hello נושא אותה: 140 שניות, promisc, בלי שאילתות ובלי קבוצות."""
    base = {"v": 1, "iface": "eth0", "age_s": 5, "window_s": 140, "promisc": True,
            "igmp": {"queries": 0, "general": 0, "group": 0, "reports": 0, "leaves": 0, "versions": [], "sources": [],
                     "last_query_age_s": None},
            "passed": 0, "kernel_drops": 0, "sampled": 0,
            "classes": dict.fromkeys(l2frames.CLASSES, 0), "groups": [], "groups_more": 0, "macs": [], "macs_more": 0,
            "joined": ["224.0.0.1"]}
    for key, value in over.items():
        if key == "queries":
            base["igmp"].update(queries=value, general=value, sources=["10.0.0.1"] if value else [], versions=[2])
        elif key == "classes":
            base["classes"].update(value)
        else:
            base[key] = value
    return base


# --- N4: querier -------------------------------------------------------------------------------------------------


def test_n4_a_query_heard_is_found_with_its_source():
    s = l2sense.n4_cloner(sample(queries=3))
    assert (s.rule, s.state) == ("N4", l2sense.FOUND)
    assert s.evidence["sources"] == ["10.0.0.1"] and s.evidence["queries"] == 3


def test_n4_a_long_silent_window_is_none_found_and_says_the_server_is_sending():
    quiet = l2sense.n4_cloner(sample())
    assert quiet.state == l2sense.NONE_FOUND and "לא נמצא querier" in quiet.why
    blocked = l2sense.n4_cloner(sample(), server_sending=True)
    assert blocked.state == l2sense.NONE_FOUND and "נעצרות בדרך" in blocked.why, \
        "the server sends and the cloner hears nothing: that is the finding"


def test_n4_a_window_shorter_than_the_query_interval_is_not_checked_never_none_found():
    """querier ששואל כל 125 ש' יכול להישמע רק בחלון ארוך ממנו. חלון של 60 שניות שלא שמע אינו ראיה."""
    for window in (60, l2sense.QUERIER_WINDOW_S - 1):
        assert l2sense.n4_cloner(sample(window_s=window)).state == l2sense.NOT_CHECKED
    assert l2sense.n4_cloner(sample(window_s=l2sense.QUERIER_WINDOW_S)).state == l2sense.NONE_FOUND
    # ושאילתה שנשמעה בחלון קצר עדיין ראיה חיובית
    assert l2sense.n4_cloner(sample(window_s=30, queries=1)).state == l2sense.FOUND


@pytest.mark.parametrize("nw, why", [
    (None, "לא דיווח"),
    ({"pending": True}, "עוד רצה"),
    ({"error": "Operation not permitted"}, "Operation not permitted"),
    ("x", "פגום"),
    ({"v": 2, "igmp": {}, "window_s": 140}, "פגום"),
    ({"v": 1, "window_s": 140}, "פגום"),
], ids=["absent", "pending", "error", "string", "future-version", "no-igmp"])
def test_n4_every_way_of_not_measuring_is_not_checked_with_a_reason(nw, why):
    s = l2sense.n4_cloner(nw)
    assert s.state == l2sense.NOT_CHECKED and why in s.why, s


@pytest.mark.parametrize("bad", [True, -1, "3", None, 2.5])
def test_n4_a_counter_that_is_not_a_count_is_not_zero(bad):
    """דיווח פגום אינו "אפס שאילתות": `True` אינו 1, ו-`None` אינו 0 (עיקרון 5)."""
    nw = sample()
    nw["igmp"]["queries"] = bad
    assert l2sense.n4_cloner(nw).state == l2sense.NOT_CHECKED


def _server(**over):
    args = dict(error=None, sending=False, other=None, other_age_s=None, listening_s=600.0, hears=True)
    args.update(over)
    return l2sense.n4_server(**args)


def test_n4_server_states():
    assert _server(other="10.0.0.254", other_age_s=10).state == l2sense.FOUND
    assert _server(sending=True).state == l2sense.FOUND and "עצמו" in _server(sending=True).why
    assert _server().state == l2sense.NONE_FOUND
    # בלי חומת אש שמקבלת IGMP, או כשלא נבדקה, "לא שמענו" אינו "אין"
    assert _server(hears=False).state == l2sense.NOT_CHECKED
    assert _server(hears=None).state == l2sense.NOT_CHECKED
    assert _server(error="no IPv4").state == l2sense.NOT_CHECKED
    # עוד לא הקשבנו מספיק (רצף שנקטע מתאפס)
    assert _server(listening_s=l2sense.SERVER_LISTEN_S - 1).state == l2sense.NOT_CHECKED
    assert _server(listening_s=None).state == l2sense.NOT_CHECKED
    # querier שנשמע מנצח גם כשההאזנה לא מספיק ארוכה
    assert _server(other="10.0.0.254", listening_s=1.0, hears=None).state == l2sense.FOUND


# --- N6: הצפה לפורט שלא ביקש --------------------------------------------------------------------------------------


def _n6(nw, **over):
    args = dict(engine_group=GROUP, member=False, wave_age_s=1000.0)
    args.update(over)
    return l2sense.n6_cloner(nw, **args)


def flooded_sample(n=500, **over):
    return sample(classes={"mcast_ip": n}, groups=[{"group": GROUP, "n": n}], passed=n, sampled=n, **over)


def test_n6_a_cloner_that_never_joined_and_still_got_the_group_is_flooded():
    s = _n6(flooded_sample())
    assert (s.rule, s.state) == ("N6", l2sense.FLOODED)
    assert s.evidence["frames_to_group"] == 500 and s.evidence["joined"] == ["224.0.0.1"]


def test_n6_no_frames_through_a_whole_wave_is_not_flooded_only_with_everything_in_place():
    clean = _n6(sample())
    assert clean.state == l2sense.NOT_FLOODED
    # כל תנאי שחסר הופך את "אין הצפה" ל"לא נמדד": לא היה זרם שיציף
    assert _n6(sample(), wave_age_s=None).state == l2sense.UNMEASURED                    # אין גל
    assert _n6(sample(), wave_age_s=140 + 5 - 1).state == l2sense.UNMEASURED            # הגל התחיל באמצע החלון
    assert _n6(sample(), wave_age_s=140 + 5).state == l2sense.NOT_FLOODED
    assert _n6(sample(promisc=False)).state == l2sense.UNMEASURED                        # בלי promisc החומרה מסננת
    assert _n6(sample(promisc=None)).state == l2sense.UNMEASURED


def test_n6_who_asked_for_the_group_is_not_a_victim():
    joined = flooded_sample(joined=["224.0.0.1", GROUP])
    assert _n6(joined).state == l2sense.UNMEASURED and "הצטרף" in _n6(joined).why
    assert _n6(flooded_sample(), member=True).state == l2sense.UNMEASURED
    assert _n6(flooded_sample(), member=None).state == l2sense.FLOODED, "membership unread is not 'is a member'"


def test_n6_a_group_that_was_not_read_means_nothing_is_judged():
    # #3173: the only engine is the chunk engine and its group is always known; `None` is "not read" (principle 5)
    s = _n6(flooded_sample(), engine_group=None)
    assert s.state == l2sense.UNMEASURED and "לא נקראה" in s.why


def test_n6_unread_joined_groups_are_not_checked():
    nw = flooded_sample()
    nw["joined"] = None
    assert _n6(nw).state == l2sense.NOT_CHECKED
    assert _n6(None).state == l2sense.NOT_CHECKED


def test_n6_other_groups_are_not_ours_to_judge():
    nw = sample(classes={"mcast_ip": 40}, groups=[{"group": "224.0.0.251", "n": 40}], passed=40, sampled=40)
    assert _n6(nw).state == l2sense.NOT_FLOODED     # אין מסגרת של הקבוצה שלנו, וגל רץ בכל החלון


# --- N8: תעבורה אחרת ----------------------------------------------------------------------------------------------


def _n8(nw, **over):
    return l2sense.n8(nw, engine_group=over.pop("engine_group", GROUP), who="המשכפל")


def test_n8_only_expected_traffic_is_quiet():
    expected = {"arp": 4, "dhcp": 2, "wol": 1, "igmp": 3, "lldp": 1, "l2ctl": 5, "ipv6": 30}
    s = _n8(sample(classes=expected, passed=46, sampled=46))
    assert (s.rule, s.state) == ("N8", l2sense.QUIET), "IPv6 ND/MLD is protocol noise, expected traffic is ours"
    assert s.evidence["classes"]["ipv6"] == 30


@pytest.mark.parametrize("cls", ["bcast_ip", "unicast_other", "other"])
def test_n8_each_foreign_class_is_foreign_seen(cls):
    s = _n8(sample(classes={cls: 7, "arp": 3}, passed=10, sampled=10))
    assert s.state == l2sense.FOREIGN_SEEN and s.evidence["foreign_in_sample"] == 7


def test_n8_our_own_group_is_subtracted_and_other_multicast_is_foreign():
    ours = sample(classes={"mcast_ip": 900}, groups=[{"group": GROUP, "n": 900}], passed=900, sampled=900)
    assert _n8(ours).state == l2sense.QUIET and _n8(ours).evidence["ours_group_frames"] == 900
    mixed = sample(classes={"mcast_ip": 905}, groups=[{"group": GROUP, "n": 900}, {"group": "224.0.0.251", "n": 5}],
                   passed=905, sampled=905)
    s = _n8(mixed)
    assert s.state == l2sense.FOREIGN_SEEN and s.evidence["foreign_in_sample"] == 5


def test_n8_without_a_known_group_multicast_is_unattributed_not_foreign():
    nw = sample(classes={"mcast_ip": 900}, groups=[{"group": "233.254.1.2", "n": 900}], passed=900, sampled=900)
    s = _n8(nw, engine_group=None)
    assert s.state == l2sense.QUIET and s.evidence["mcast_unattributed"] == 900


def test_n8_quiet_requires_that_everything_that_passed_was_read():
    """`quiet` = כל מה שעבר את המסנן נקרא. קראנו 4000 מתוך 80000 ולא ראינו זר -- זה "לא ראינו הכול", לא "שקט"."""
    partial = sample(classes={"mcast_ip": 4000}, groups=[{"group": GROUP, "n": 4000}], passed=80000, sampled=4000)
    assert _n8(partial).state == l2sense.NOT_CHECKED
    assert _n8(sample(passed=None)).state == l2sense.NOT_CHECKED
    assert _n8(sample(passed=l2sense.READ_SLACK, sampled=0)).state == l2sense.QUIET
    assert _n8(sample(passed=l2sense.READ_SLACK + 1, sampled=0)).state == l2sense.NOT_CHECKED
    # ראיה חיובית על זר נשארת ראיה גם בדגימה חלקית
    seen = sample(classes={"bcast_ip": 1}, passed=80000, sampled=4000)
    assert _n8(seen).state == l2sense.FOREIGN_SEEN


@pytest.mark.parametrize("bad", [None, {}, {"classes": "x"}, {"classes": {"arp": 1}},
                                 {"classes": {**dict.fromkeys(l2frames.CLASSES, 0), "arp": -1}}])
def test_n8_unreadable_counts_are_not_checked(bad):
    assert _n8(bad).state == l2sense.NOT_CHECKED


# --- הרישום ------------------------------------------------------------------------------------------------------


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "l2.db")
    c.execute("PRAGMA foreign_keys = OFF")
    return c


def _rows(conn, rule=None, key=MAC):
    return memory.decisions(conn, kind="machine", key=key, rule=rule, limit=50)


def test_a_sense_is_recorded_in_shadow_once_per_change(conn):
    ent = ("machine", MAC)
    first = l2sense.record(conn, l2sense.n4_cloner(sample()), ent)
    assert isinstance(first, int)
    assert l2sense.record(conn, l2sense.n4_cloner(sample(age_s=99)), ent) is None, "same state, same news"
    assert l2sense.record(conn, l2sense.n4_cloner(sample(queries=2)), ent) is not None
    rows = _rows(conn, "N4")
    assert [r["verdict"] for r in rows] == [l2sense.FOUND, l2sense.NONE_FOUND]
    assert {r["mode"] for r in rows} == {"shadow"} and rows[0]["action"] == "SENSE"
    assert rows[0]["outcome"] == l2sense.SHADOW_HE and rows[0]["evidence"][0]["sources"] == ["10.0.0.1"]


def test_each_rule_and_each_entity_has_its_own_last_state(conn):
    a = l2sense.record(conn, l2sense.n4_cloner(sample()), ("machine", MAC))
    b = l2sense.record(conn, l2sense.n4_cloner(sample()), ("machine", "b4:2e:99:07:1a:c5"))
    c = l2sense.record(conn, l2sense.n8(sample(passed=0), engine_group=GROUP, who="x"), ("machine", MAC))
    assert None not in (a, b, c)


def test_nothing_is_recorded_when_the_brain_is_off_and_a_failed_write_is_retried(conn, monkeypatch):
    db.set_setting(conn, veto.SETTING, veto.OFF)
    assert l2sense.record(conn, l2sense.n4_cloner(sample()), ("machine", MAC)) is None
    assert _rows(conn) == []
    db.set_setting(conn, veto.SETTING, veto.SHADOW)
    monkeypatch.setattr(memory, "record", lambda *a, **k: None)             # the write failed
    assert l2sense.record(conn, l2sense.n4_cloner(sample()), ("machine", MAC)) is None
    monkeypatch.undo()
    assert l2sense.record(conn, l2sense.n4_cloner(sample()), ("machine", MAC)) is not None, \
        "a state whose write failed must be written on the next sample, not remembered as done"


def test_note_hello_judges_the_three_senses_and_skips_a_sample_it_already_judged(conn):
    ent_rows = lambda: len(_rows(conn))                                       # noqa: E731
    got = l2sense.note_hello(conn, MAC, sample(), now=2000.0)
    assert [s.rule for s in got] == ["N4", "N6", "N8"] and ent_rows() == 3
    assert l2sense.note_hello(conn, MAC, sample(age_s=60), now=2060.0) == [], "same sample, only older"
    assert ent_rows() == 3
    changed = l2sense.note_hello(conn, MAC, sample(queries=1), now=2100.0)
    assert changed and ent_rows() == 4                                        # only N4 changed state


def test_note_hello_never_raises(conn, monkeypatch):
    monkeypatch.setattr(l2sense, "cloner_context", lambda *a, **k: 1 / 0)
    assert l2sense.note_hello(conn, MAC, sample()) == []


def test_note_hello_tells_the_server_is_sending_to_n4(conn):
    l2sense.set_server_view(lambda: True)
    l2sense.note_hello(conn, MAC, sample(), now=2000.0)
    (row,) = _rows(conn, "N4")
    assert row["verdict"] == l2sense.NONE_FOUND and row["evidence"][0]["server_sending"] is True


def test_the_cloner_context_reads_engine_wave_and_membership(conn):
    # #3173: a server that never chose an engine runs the chunk engine, so its group is known without any setting
    ctx = l2sense.cloner_context(conn, MAC, 2000.0)
    assert ctx["engine_group"] == GROUP and ctx["wave_age_s"] is None and ctx["member"] is False
    # a value saved before #3173 is not read for a decision (`chunksend.engine_of` is always the chunk engine)
    db.set_setting(conn, "stream_engine", "udpcast")
    assert l2sense.cloner_context(conn, MAC, 2000.0)["engine_group"] == GROUP


# --- ה-hello עד ה-DB ----------------------------------------------------------------------------------------------


def _hello(server, **fields):
    return server["anon"].post("/api/v1/agent/hello", json={
        "schema": 2, "mac": MAC, "all_macs": [MAC], "ip": "10.44.12.187", "disks": [], **fields})


def test_hello_netwatch_is_stored_inside_probe_and_is_not_a_version(server):
    setup_classroom(server)
    conn = server["ctx"].conn
    assert _hello(server, probe={}, netwatch=sample(queries=1)).status_code == 200
    assert probe.latest(conn, MAC)["probe"]["netwatch"]["igmp"]["queries"] == 1
    assert _hello(server, probe={}, netwatch=sample(queries=4, age_s=77)).status_code == 200
    assert probe.latest(conn, MAC)["probe"]["netwatch"]["igmp"]["queries"] == 4
    assert len(probe.history(conn, MAC)) == 1, "the counters of one listening window are not a hardware version"


def test_hello_senses_are_recorded_for_a_registered_machine_and_old_agents_are_not_checked(server):
    setup_classroom(server)
    conn = server["ctx"].conn
    assert _hello(server, probe={}).status_code == 200                        # an agent without the binary
    rows = {r["rule"]: r for r in _rows(conn)}
    assert {r["verdict"] for r in rows.values()} == {l2sense.NOT_CHECKED} and set(rows) == {"N4", "N6", "N8"}
    assert _hello(server, probe={}, netwatch=sample()).status_code == 200
    latest = {r["rule"]: r["verdict"] for r in reversed(_rows(conn))}        # newest row of each rule wins
    assert latest == {"N4": l2sense.NONE_FOUND, "N6": l2sense.UNMEASURED, "N8": l2sense.QUIET}


def test_hello_from_an_unregistered_machine_writes_no_brain_rows(server):
    stranger = "aa:aa:aa:aa:aa:01"
    body = {"schema": 2, "mac": stranger, "all_macs": [stranger], "ip": "10.44.12.99", "disks": [],
            "probe": {}, "netwatch": sample()}
    assert server["anon"].post("/api/v1/agent/hello", json=body).status_code == 200
    assert _rows(server["ctx"].conn, key=stranger) == []


def test_hello_netwatch_is_capped_like_the_other_siblings(server):
    setup_classroom(server)
    huge = sample(groups=[{"group": f"239.1.{n // 250}.{n % 250}", "n": 1} for n in range(5000)])
    refused = _hello(server, probe={}, netwatch=huge)
    assert refused.status_code == 413 and "netwatch" in refused.text
    assert _rows(server["ctx"].conn) == [], "a refused hello saves nothing"
    from server.agent import hello
    assert "netwatch" in hello.HELLO_CAPPED_FIELDS
    assert hello.hello_payload_oversized({"netwatch": {"x": "y" * (hello.HELLO_FIELD_MAX_BYTES + 1)}}) is not None


# --- המאזין של השרת -----------------------------------------------------------------------------------------------


def run_bpf(program, frame: bytes) -> int:
    """מפרש מינימלי למסנן שלנו: LD W/H ABS, JEQ K, JA, RET. מחזיר את ה-RET (0 = נזרק)."""
    acc, pc = 0, 0
    while True:
        code, jt, jf, k = program[pc]
        if code == 0x20:
            acc, pc = int.from_bytes(frame[k:k + 4], "big"), pc + 1
        elif code == 0x28:
            acc, pc = int.from_bytes(frame[k:k + 2], "big"), pc + 1
        elif code == 0x15:
            pc += 1 + (jt if acc == k else jf)
        elif code == 0x05:
            pc += 1 + k
        elif code == 0x06:
            return k
        else:
            raise AssertionError(f"opcode {code:#x} is not in our filter")


OURS = bytes.fromhex("aabbccddeeff")


@pytest.mark.parametrize("dst, src, passes", [
    ("aa:bb:cc:dd:ee:ff", ME_NOT, False),            # to us
    (BCAST, "aa:bb:cc:dd:ee:ff", False),             # from us (the multicast we send, 80k/s)
    ("aa:bb:cc:dd:ee:ff", "aa:bb:cc:dd:ee:ff", False),
    (BCAST, ME_NOT, True),
    ("01:00:5e:53:43:01", ME_NOT, True),
    ("aa:bb:cc:dd:ee:00", ME_NOT, True),             # same prefix, another host: flooded unicast
    ("aa:bb:cc:dd:00:ff", ME_NOT, True),
    (ME_NOT, "aa:bb:cc:dd:ee:00", True),
    (ME_NOT, "aa:bb:cc:00:ee:ff", True),
])
def test_the_filter_drops_what_is_to_or_from_this_card_and_nothing_else(dst, src, passes):
    frame = eth(dst, src, 0x0800, b"\0" * 40)
    assert (run_bpf(netwatch.program(OURS), frame) > 0) is passes


def test_the_python_filter_is_the_c_filter():
    """אותה תוכנית, אינסטרוקציה אחר אינסטרוקציה, בשני הקבצים (אחד נפרס על ה-testrunner, השני ב-sh)."""
    from pathlib import Path
    c = (Path(__file__).resolve().parent.parent / "agent" / "netwatch.c").read_text(encoding="utf-8")
    start = c.index("static void prog_b")
    body = c[start:c.index("memcpy(p, t, sizeof t)", start)]
    assert body.count("BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K") == 4 and body.count("BPF_STMT(BPF_LD") == 4
    assert "BPF_STMT(BPF_JMP | BPF_JA, 0)" in body
    assert len(netwatch.program(OURS)) == 11 == body.count("BPF_STMT") + body.count("BPF_JUMP")


class FakeTap:
    """סוקט AF_PACKET מזויף: `recv` מחזיר מסגרת, ואחרי שנגמרו -- timeout. `stats` = (passed, drops) או שגיאה."""

    def __init__(self, frames, stats=(0, 0)):
        self.frames, self.stats, self.closed = list(frames), stats, False

    def settimeout(self, value):
        pass

    def recv(self, size):
        if not self.frames:
            import socket
            raise socket.timeout()
        return self.frames.pop(0)

    def getsockopt(self, level, name, size):
        import struct
        if self.stats is None:
            raise OSError("no statistics")
        return struct.pack("II", *self.stats)

    def close(self):
        self.closed = True


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def make_watch(conn, tap, *, querier=None, group=GROUP, gate=lambda: None):
    clock = Clock()
    stop = threading.Event()
    stop.wait = lambda timeout=None: clock.__setattr__("now", clock.now + (timeout or 0)) or stop.is_set()
    watch = netwatch.Watch(conn, "eth-deploy", gate=gate, querier=querier, engine_group=lambda: group,
                           mac=lambda: OURS, opener=lambda iface, mac: tap, clock=clock)
    return watch, stop, clock


def test_a_slice_counts_foreign_traffic_and_records_n8(conn):
    tap = FakeTap([FRAMES["bcast_ip"]] * 3 + [FRAMES["arp"], mcast_frame(GROUP)], stats=(5, 0))
    watch, stop, _ = make_watch(conn, tap)
    watch.step(stop)
    data = netwatch.combine(list(watch.slices))
    assert data["sampled"] == 5 and data["classes"]["bcast_ip"] == 3 and data["passed"] == 5
    assert data["groups"] == [{"group": GROUP, "n": 1}]
    rows = memory.decisions(conn, kind="network", key="eth-deploy", rule="N8")
    assert [r["verdict"] for r in rows] == [l2sense.FOREIGN_SEEN] and rows[0]["evidence"][0]["foreign_in_sample"] == 3


def test_a_quiet_slice_is_quiet_only_when_the_kernel_counter_was_read(conn):
    watch, stop, _ = make_watch(conn, FakeTap([FRAMES["arp"]], stats=(1, 0)))
    watch.step(stop)
    assert watch.senses()[-1].state == l2sense.QUIET
    watch, stop, _ = make_watch(conn, FakeTap([FRAMES["arp"]], stats=None))
    watch.step(stop)
    assert watch.senses()[-1].state == l2sense.NOT_CHECKED, "a counter that could not be read is not 'nothing passed'"


def test_a_flood_is_read_up_to_the_cap_and_the_rest_is_the_kernels_count(conn):
    flood = [mcast_frame(GROUP)] * (netwatch.MAX_SAMPLE + 500)
    watch, stop, _ = make_watch(conn, FakeTap(flood, stats=(80000, 75000)))
    watch.step(stop)
    (only,) = watch.slices
    assert only.sampled == netwatch.MAX_SAMPLE and only.passed == 80000 and only.drops == 75000
    assert watch.senses()[-1].state == l2sense.NOT_CHECKED          # 4000 of 80000 read: not "quiet"


def test_the_watch_that_cannot_listen_says_not_checked_and_keeps_going(conn, monkeypatch):
    def boom(iface, mac):
        raise PermissionError("Operation not permitted")
    watch, stop, _ = make_watch(conn, None)
    watch.opener = boom
    monkeypatch.setattr(stop, "wait", lambda timeout=None: stop.set() or True)
    watch.run_forever(stop)
    assert watch.error == "Operation not permitted"
    assert watch.senses()[-1].state == l2sense.NOT_CHECKED and "Operation not permitted" in watch.senses()[-1].why
    rows = memory.decisions(conn, kind="network", key="eth-deploy", rule="N8")
    assert rows and rows[0]["verdict"] == l2sense.NOT_CHECKED


def test_a_refused_gate_closes_the_socket_and_says_why(conn):
    tap = FakeTap([FRAMES["bcast_ip"]])
    watch, stop, _ = make_watch(conn, tap, gate=lambda: "הכרטיס אינו כרטיס הפצה")
    watch._tap = tap
    watch.step(stop)
    assert tap.closed and watch.refused and "אינו כרטיס הפצה" in watch.senses()[-1].why


def test_the_slices_are_bounded_and_one_unread_counter_makes_the_sum_unread():
    one, two = netwatch.Slice(60.0), netwatch.Slice(60.0)
    one.passed, one.drops = 10, 0
    two.passed, two.drops = None, None
    assert netwatch.combine([one, two])["passed"] is None, "a partial sum would read as 'few passed'"
    assert netwatch.combine([]) is None
    watch, _, _ = make_watch(None, FakeTap([]))
    assert watch.slices.maxlen == netwatch.KEEP


# --- ה-Querier ----------------------------------------------------------------------------------------------------


def test_the_querier_counts_how_long_it_has_listened_and_a_failure_resets_it(conn, monkeypatch):
    clock = QClock()
    qr = make_q(conn, clock, hears=lambda: True)
    db.set_setting(conn, q.SETTING, "false")                    # the server does not ask: only listens
    assert qr.listening_since is None and qr.sense().state == l2sense.NOT_CHECKED
    run_q(qr, clock, [150, 150])
    assert qr.listening_since == 1000.0
    s = qr.sense()
    assert s.state == l2sense.NONE_FOUND and s.evidence["listening_s"] == 300, s
    qr.opener = lambda *a: (_ for _ in ()).throw(OSError("no raw socket"))
    stop = threading.Event()
    monkeypatch.setattr(stop, "wait", lambda timeout=None: stop.set() or True)
    qr.run_forever(stop)
    assert qr.listening_since is None and qr.sense().state == l2sense.NOT_CHECKED, "a broken listener is not 'nobody asked'"


def test_the_querier_does_not_call_it_none_found_before_it_listened_long_enough(conn):
    clock = QClock()
    qr = make_q(conn, clock, hears=lambda: True)
    db.set_setting(conn, q.SETTING, "false")
    run_q(qr, clock, [100])
    assert qr.sense().state == l2sense.NOT_CHECKED
    qr.hears = lambda: False
    clock.now += 1000
    assert qr.sense().state == l2sense.NOT_CHECKED, "a firewall that drops IGMP makes silence meaningless"


def test_the_querier_that_sends_is_found_until_three_intervals_pass_without_a_send(conn):
    clock = QClock()
    qr = make_q(conn, clock, hears=lambda: True)
    run_q(qr, clock, [1.0])
    assert qr.sending(clock()) and qr.sense().state == l2sense.FOUND and "עצמו" in qr.sense().why
    clock.now += 3 * q.QUERY_INTERVAL
    assert not qr.sending(clock()), "a query that was not sent for three intervals is not 'sending'"


def test_another_querier_that_is_heard_is_found_with_its_address(conn):
    clock = QClock()
    qr = make_q(conn, clock, hears=lambda: True)
    run_q(qr, clock, [150, ip_packet("10.44.12.1", q.query_packet()), 10])
    s = qr.sense()
    assert s.state == l2sense.FOUND and "10.44.12.1" in s.why and s.evidence["other"] == "10.44.12.1"
