"""הקצב לפי כרטיס ההפצה — הכרעת נדב 15/10: "הכי מהר שאפשר, והשרת מגביל
לפי כרטיס הרשת שלו".

עד כאן הגל הראשון יצא ב-600m והגג היה 900m — על כל כרטיס, גם 10GbE. מעכשיו,
כשמהירות הקישור נקראה מ-`/sys/class/net/<if>/speed`:

    גג = 90% מהקישור · פתיחה = 60% מהקישור (על 1GbE: בדיוק 900m/600m)

ומהירות שלא נקראה (‏-1, קובץ חסר, שגיאת קריאה, כרטיס וירטואלי) — ההתנהגות
של היום, והסיבה נאמרת (עיקרון 5: "לא נקרא" אינו 1GbE).

שום טסט כאן אינו קורא את ‎/sys של המכונה: עץ מזויף ב-tmp_path, או hook
מוזרק ל-`SenderEngine`/`create_app`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from server import db
from server.rounds import drive_rates, nic_speed
from test_server_room import room_server  # noqa: F401 — fixture של nic_room

FAST_MAC = "aa:bb:cc:00:00:21"


@pytest.fixture()
def conn(tmp_path):
    """מחשב השיכפול של `_drive` מוגדר 10G — כך הטסטים של כרטיס השרת אינם
    נחסמים בהנחת 1Gb/s על המקבלים (‏15/10 ב'); לטסטים של המקבלים — ‏`slow`."""
    c = db.connect(tmp_path / "imagesrv.db")
    nic_speed.set_manual(c, FAST_MAC, 10000)
    return c


def _sys(tmp_path: Path, name: str, speed: str | None = None,
         driver: str | None = None, slaves: list[str] | None = None) -> Path:
    root = tmp_path / "sys" / "class" / "net"
    entry = root / name
    entry.mkdir(parents=True, exist_ok=True)
    if speed is not None:
        (entry / "speed").write_text(speed + "\n")
    if driver is not None:
        (entry / "device").mkdir(exist_ok=True)
        (entry / "device" / "uevent").write_text(f"DRIVER={driver}\nPCI_ID=15AD:07B0\n")
    if slaves is not None:
        (entry / "bonding").mkdir(exist_ok=True)
        (entry / "bonding" / "slaves").write_text(" ".join(slaves) + "\n")
    return root


def _link(mbps):
    return {"interface": "eth1", "speed_mbps": mbps, "why": None}


def _drive(serial):
    return {"serial": serial, "model": "EVO 870", "mac": "aa:bb:cc:00:00:21", "port": 1}


# --- קריאת המהירות --------------------------------------------------------------


@pytest.mark.parametrize("raw, mbps", [("1000", 1000), ("2500", 2500), ("10000", 10000)])
def test_a_readable_speed_is_read(tmp_path, raw, mbps):
    root = _sys(tmp_path, "eth1", raw, driver="igb")
    assert drive_rates.read_link("eth1", root) == {
        "interface": "eth1", "speed_mbps": mbps, "why": None}


@pytest.mark.parametrize("raw", ["-1", "0", "garbage", ""])
def test_minus_one_or_garbage_is_unknown_not_1gbe(tmp_path, raw):
    root = _sys(tmp_path, "eth1", raw)
    got = drive_rates.read_link("eth1", root)
    assert got["speed_mbps"] is None and got["why"] == "unknown"


def test_an_absent_card_or_file_is_absent(tmp_path):
    root = _sys(tmp_path, "eth1")                       # כרטיס בלי קובץ speed
    assert drive_rates.read_link("eth1", root)["why"] == "absent"
    assert drive_rates.read_link("eth9", root)["why"] == "absent"
    assert drive_rates.read_link("eth1", tmp_path / "nowhere")["why"] == "absent"


def test_an_unreadable_file_is_unreadable(tmp_path, monkeypatch):
    """לינוקס מחזיר EINVAL על `speed` כשהקישור למטה — זו שגיאת קריאה, לא 1GbE."""
    root = _sys(tmp_path, "eth1", "1000")
    real = Path.read_text

    def broken(self, *a, **k):
        if self.name == "speed":
            raise OSError(22, "Invalid argument")
        return real(self, *a, **k)
    monkeypatch.setattr(Path, "read_text", broken)
    got = drive_rates.read_link("eth1", root)
    assert got == {"interface": "eth1", "speed_mbps": None, "why": "unreadable"}


def test_no_interface_is_said(tmp_path):
    assert drive_rates.read_link(None, tmp_path)["why"] == "no_interface"


def test_a_virtual_card_is_not_evidence_of_the_wire(tmp_path):
    """‏vmxnet3 מדווח 10000 תמיד, גם כשהמארח מחובר ב-1G (עיקרון 5א)."""
    root = _sys(tmp_path, "ens192", "10000", driver="vmxnet3")
    got = drive_rates.read_link("ens192", root)
    assert got["speed_mbps"] is None and got["why"] == "virtual"


def test_a_bond_is_its_slowest_member(tmp_path):
    """זרם מולטיקאסט אחד יוצא בסלייב אחד — לא בסכום."""
    _sys(tmp_path, "eth1", "10000", driver="ixgbe")
    _sys(tmp_path, "eth2", "1000", driver="igb")
    root = _sys(tmp_path, "bond0", "11000", slaves=["eth1", "eth2"])
    assert drive_rates.read_link("bond0", root)["speed_mbps"] == 1000
    (root / "eth2" / "speed").write_text("-1\n")
    assert drive_rates.read_link("bond0", root)["why"] == "unknown"


# --- הנוסחה ---------------------------------------------------------------------


@pytest.mark.parametrize("mbps, ceiling, start", [
    (1000, "900m", "600m"), (2500, "2250m", "1500m"), (10000, "9000m", "6000m")])
def test_the_ceiling_and_start_follow_the_link(conn, mbps, ceiling, start):
    got = drive_rates.choose(conn, [_drive("S1")], None, link=_link(mbps))
    assert (got["ceiling"], got["start"], got["max_bitrate"]) == (ceiling, start, start)
    assert got["basis"] == "start" and got["link"]["applied"] is True


def test_1gbe_is_exactly_todays_behaviour(conn):
    """הצד החיובי: על 1GbE שום דבר לא זז — אותם מספרים ואותה סיבה."""
    nic_speed.set_manual(conn, FAST_MAC, 1000)
    with_link = drive_rates.choose(conn, [_drive("S1")], None, link=_link(1000))
    today = drive_rates.choose(conn, [_drive("S1")], None)
    keys = ("max_bitrate", "ceiling", "start", "basis", "by", "previous", "drives")
    assert {k: with_link[k] for k in keys} == {k: today[k] for k in keys}
    assert (today["start"], today["ceiling"], today["max_bitrate"]) == ("600m", "900m", "600m")


@pytest.mark.parametrize("why", ["unknown", "absent", "unreadable", "virtual", "not_wired"])
def test_an_unread_speed_keeps_todays_exact_numbers_and_says_why(conn, why):
    link = {"interface": "eth1", "speed_mbps": None, "why": why}
    got = drive_rates.choose(conn, [_drive("S1")], None, link=link)
    assert (got["ceiling"], got["start"], got["max_bitrate"]) == ("900m", "600m", "600m")
    assert got["link"] == {**link, "applied": False}


def test_a_configured_cap_still_caps_a_fast_link(conn):
    got = drive_rates.choose(conn, [_drive("S1")], "200m", link=_link(10000))
    assert (got["ceiling"], got["max_bitrate"]) == ("200m", "200m")
    got = drive_rates.choose(conn, [_drive("S1")], "5g", link=_link(10000))
    assert (got["ceiling"], got["start"]) == ("5000m", "5000m")


def test_a_slow_link_lowers_the_ceiling_under_a_high_cap(conn):
    got = drive_rates.choose(conn, [_drive("S1")], "5g", link=_link(1000))
    assert got["ceiling"] == "900m" and got["max_bitrate"] == "600m"


def test_the_learned_rate_never_goes_above_the_link(conn):
    """גל קודם עם אותם כוננים יצא ב-6000m (כרטיס 10G) ונשאר שם; הכרטיס
    הוחלף ל-1G — הזיכרון אינו גובר על הקישור."""
    assert drive_rates.decide(conn, "ses_1", lambda: [_drive("S1")], None,
                              link=lambda: _link(10000)) == "6000m"
    drive_rates.record_stream(conn, "ses_1", {     # מעט שידורים חוזרים, ומקלט
        "sent_bytes": 10 ** 10, "loss_blocks": 10,  # שלא דיווח (17/10) — נשאר
        "stream_bps": None, "stream_s": None})
    remembered = drive_rates.choose(conn, [_drive("S1")], None, link=_link(10000))
    assert remembered["max_bitrate"] == "6000m" and remembered["basis"] == "unmeasured"
    got = drive_rates.choose(conn, [_drive("S1")], None, link=_link(1000))
    assert got["max_bitrate"] == "900m" and got["basis"] == "ceiling"


def test_the_floor_does_not_grow_with_the_link(conn):
    """כרטיס 10G בשרת אינו אומר שהמקבלים ב-10G: אחרי גל אחרי גל של שידורים
    חוזרים הקצב יורד עד 150m (כמו היום) — לא נעצר ב-1500m."""
    rates = []
    for n in range(16):
        rates.append(drive_rates.decide(conn, f"ses_{n}", lambda: [_drive("S1")], None,
                                        link=lambda: _link(10000)))
        drive_rates.record_stream(conn, f"ses_{n}", {
            "sent_bytes": 10 ** 10, "loss_blocks": 10 ** 6, "stream_bps": None,
            "stream_s": None})
    assert rates[:3] == ["6000m", "4500m", "3375m"]
    assert rates[-1] == "150m"


def test_a_direct_wave_ignores_the_servers_card(conn):
    """גל ממחשב בנייה (#715) יוצא מהכרטיס שלו — לא מזה של השרת."""
    got = drive_rates.choose(conn, [_drive("S1")], None, direct=True, link=_link(10000))
    assert got["max_bitrate"] == "600m" and got["link"]["applied"] is False


def test_the_journal_names_the_link_and_the_ceiling(conn):
    drive_rates.decide(conn, "ses_1", lambda: [_drive("S1")], None, link=lambda: _link(10000))
    drive_rates.decide(conn, "ses_2", lambda: [_drive("S2")], None,
                       link=lambda: {"interface": "br0", "speed_mbps": None, "why": "unknown"})
    lines = [r["detail"] for r in conn.execute(
        "SELECT detail FROM journal WHERE event = 'wave_rate' ORDER BY id")]
    assert lines[0].startswith("ses_1 6000m basis=start")
    assert "link=eth1 10000Mb/s ceiling=9000m" in lines[0]
    assert "link=br0 unknown ceiling=900m" in lines[1]


def test_a_throwing_reader_is_the_start_rate_not_a_dead_wave(conn):
    def broken():
        raise RuntimeError("sysfs exploded")
    assert drive_rates.decide(conn, "ses_1", lambda: [_drive("S1")], None,
                              link=broken) == "600m"


# --- חיווט: השולח, ה-runtime, ‏main ----------------------------------------------


def test_the_sender_without_a_reader_says_not_wired():
    from server.rounds.sender import SenderEngine                  # noqa: PLC0415
    engine = SenderEngine(library=None, interface="eth1", simulate=True)
    assert engine.link() == {"interface": "eth1", "speed_mbps": None, "why": "not_wired"}
    engine.link_speed = lambda iface: _link(2500) | {"interface": iface}
    assert engine.link()["speed_mbps"] == 2500


def test_main_wires_the_real_reader(tmp_path, monkeypatch):
    """הראיה שהיכולת מחוברת בייצור: ‏`server.main` מעביר `read_link`."""
    import asyncio

    import server.app
    import server.main
    uvicorn = pytest.importorskip("uvicorn")

    captured: dict = {}

    def fake_create_runtime(*args, **kwargs):
        captured.update(kwargs)
        return "rt"
    monkeypatch.setattr(server.app, "create_runtime", fake_create_runtime)
    monkeypatch.setattr(server.app, "create_agent_app", lambda rt: "agent")
    monkeypatch.setattr(server.app, "create_console_app", lambda rt: "console")
    monkeypatch.setattr(uvicorn, "Config", lambda app, **kwargs: object())
    monkeypatch.setattr(uvicorn, "Server", lambda config: object())
    monkeypatch.setattr(server.main, "serve_all", lambda servers: None)
    monkeypatch.setattr(asyncio, "run", lambda coro=None: None)
    monkeypatch.setattr(server.main, "_interface_for", lambda url: None)
    monkeypatch.setattr("sys.argv", [
        "server.main", "--server-url", "http://10.44.12.10:8080",
        "--data-dir", str(tmp_path / "data"), "--images", str(tmp_path / "img")])
    server.main.main()
    assert captured["link_speed"] is drive_rates.read_link


# --- קצה-לקצה מול ה-API: udp-sender והמסך ---------------------------------------


@pytest.fixture()
def nic_room(room_server):
    """שרת החדר הרגיל, עם כרטיס הפצה מזויף שהטסט קובע את מהירותו."""
    from test_server_room import CLONER1, CLONER2                    # noqa: PLC0415
    state = {"link": _link(10000)}
    room_server["ctx"].sender.link_speed = lambda iface: dict(state["link"])
    for mac in (CLONER1, CLONER2):         # המקבלים 10G — רק כרטיס השרת חוסם
        nic_speed.set_manual(room_server["ctx"].conn, mac, 10000)
    room_server["link"] = state
    return room_server


def test_a_10g_server_sends_the_first_wave_at_6000m_and_says_so(nic_room):
    from test_room_learned_rate import _open_and_join, _sent_bitrate  # noqa: PLC0415
    from test_server_room import room                                 # noqa: PLC0415
    admin = nic_room["admin"]
    _open_and_join(nic_room)
    projected = room(admin)["round"]["wave_rate"]
    assert projected["max_bitrate"] == "6000m" and projected["ceiling"] == "9000m"
    assert projected["link"]["speed_mbps"] == 10000 and projected["link"]["applied"]
    assert admin.post("/api/console/room/start").status_code == 200
    assert _sent_bitrate(nic_room) == "6000m"
    shown = room(admin)["round"]["wave_rate"]
    assert shown["projected"] is False and shown["link"]["speed_mbps"] == 10000


def test_an_unread_card_sends_todays_600m(nic_room):
    from test_room_learned_rate import _open_and_join, _sent_bitrate  # noqa: PLC0415
    from test_server_room import room                                 # noqa: PLC0415
    nic_room["link"]["link"] = {"interface": "eth1", "speed_mbps": None, "why": "unknown"}
    admin = nic_room["admin"]
    _open_and_join(nic_room)
    assert admin.post("/api/console/room/start").status_code == 200
    assert _sent_bitrate(nic_room) == "600m"
    shown = room(admin)["round"]["wave_rate"]
    assert shown["link"] == {"interface": "eth1", "speed_mbps": None, "why": "unknown",
                             "applied": False}


def test_create_app_passes_the_reader_to_the_sender(tmp_path):
    pytest.importorskip("fastapi")
    from server.app import create_app                       # noqa: PLC0415
    reader = lambda iface: _link(2500)                      # noqa: E731
    app = create_app(tmp_path / "data", tmp_path / "img", "http://10.44.12.10:8080",
                     link_speed=reader)
    assert app.state.ctx.sender.link_speed is reader


# --- 15/10 (ב): מחשבי השיכפול — הכרטיס האיטי בגל ----------------------------------

SLOW_MAC, OTHER_MAC = "aa:bb:cc:00:00:31", "aa:bb:cc:00:00:32"


def _on(mac, serial):
    return {"serial": serial, "model": "EVO 870", "mac": mac, "port": 1}


def _report(conn, mac, nics):
    """דגימת probe כפי שהסוכן שולח ב-hello (‏`probe.nic`)."""
    from server.agent import probe                                # noqa: PLC0415
    probe.record(conn, mac, probe.well_formed({"nic": nics}))


def _nic(speed, name="eth0", checked=True):
    return {"name": name, "speed_mbps": speed, "duplex": "full", "stats": {},
            "ip_conflict": {"checked": True, "duplicate": False} if checked else None}


@pytest.mark.parametrize("sample, expected", [
    (None, (None, "no_probe")),
    ({"nic": None}, (None, "no_nic")),
    ({"nic": []}, (None, "no_nic")),
    ({"nic": [_nic(1000)]}, (1000, None)),
    ({"nic": [_nic(2500, checked=False)]}, (2500, None)),      # כרטיס יחיד — הוא כרטיס ההפצה
    ({"nic": [_nic(None)]}, (None, "unknown")),
    ({"nic": [_nic(-1)]}, (None, "unknown")),
    ({"nic": [_nic(10000, "eth0"), _nic(1000, "eth1", checked=False)]}, (10000, None)),
    ({"nic": [_nic(10000, "eth0", False), _nic(1000, "eth1", False)]}, (None, "ambiguous")),
    ({"nic": [_nic(10000, "eth0"), _nic(1000, "eth1")]}, (None, "ambiguous")),
])
def test_the_reported_speed_is_the_deploy_card_or_nothing(sample, expected):
    assert nic_speed.reported(sample) == expected


def test_manual_wins_then_reported_then_assumed_1g(conn):
    assert nic_speed.view(conn, SLOW_MAC) == {"speed_mbps": 1000, "source": "assumed",
                                              "why": "no_probe"}
    _report(conn, SLOW_MAC, [_nic(2500)])
    assert nic_speed.view(conn, SLOW_MAC) == {"speed_mbps": 2500, "source": "reported", "why": None}
    nic_speed.set_manual(conn, SLOW_MAC, 10000)
    assert nic_speed.view(conn, SLOW_MAC)["source"] == "manual"
    nic_speed.set_manual(conn, SLOW_MAC, None)
    assert nic_speed.view(conn, SLOW_MAC)["source"] == "reported"
    with pytest.raises(ValueError):
        nic_speed.set_manual(conn, SLOW_MAC, 100)


def test_the_slowest_cloner_caps_a_10g_server(conn):
    _report(conn, SLOW_MAC, [_nic(1000)])
    got = drive_rates.choose(conn, [_drive("S1"), _on(SLOW_MAC, "S2")], None, link=_link(10000))
    assert (got["ceiling"], got["start"], got["max_bitrate"]) == ("900m", "600m", "600m")
    assert got["cap_by"] == "cloner"
    assert got["cloner"] == {"mac": SLOW_MAC, "speed_mbps": 1000, "source": "reported",
                             "why": None, "assumed": []}


def test_an_unknown_cloner_is_assumed_1g_and_does_constrain(conn):
    """הכרעת נדב: לא ידוע = הונח 1Gb/s, וחוסם — אבל נאמר כהנחה."""
    got = drive_rates.choose(conn, [_drive("S1"), _on(SLOW_MAC, "S2")], None, link=_link(10000))
    assert (got["ceiling"], got["max_bitrate"]) == ("900m", "600m")
    assert got["cap_by"] == "cloner"
    assert got["cloner"]["mac"] == SLOW_MAC and got["cloner"]["source"] == "assumed"
    assert got["cloner"]["assumed"] == [SLOW_MAC]


def test_the_assumption_is_journaled_as_assumed_1g(conn):
    drive_rates.decide(conn, "ses_1", lambda: [_on(SLOW_MAC, "S2")], None,
                       link=lambda: _link(10000))
    line = conn.execute("SELECT detail FROM journal WHERE event = 'wave_rate'").fetchone()[0]
    assert f"cloner={SLOW_MAC} 1000Mb/s assumed_1g(no_probe) assumed=1 cap_by=cloner" in line


def test_fast_cloners_let_the_server_card_decide(conn):
    _report(conn, SLOW_MAC, [_nic(2500)])
    nic_speed.set_manual(conn, OTHER_MAC, 10000)
    got = drive_rates.choose(conn, [_on(SLOW_MAC, "S1"), _on(OTHER_MAC, "S2")], None,
                             link=_link(10000))
    assert (got["ceiling"], got["start"]) == ("2250m", "1500m") and got["cap_by"] == "cloner"
    assert got["cloner"]["mac"] == SLOW_MAC
    got = drive_rates.choose(conn, [_on(OTHER_MAC, "S2")], None, link=_link(2500))
    assert (got["ceiling"], got["start"]) == ("2250m", "1500m") and got["cap_by"] == "server"


def test_fast_cloners_do_not_raise_an_unread_server_above_today(conn):
    nic_speed.set_manual(conn, SLOW_MAC, 10000)
    link = {"interface": "ens192", "speed_mbps": None, "why": "virtual"}
    got = drive_rates.choose(conn, [_on(SLOW_MAC, "S1")], None, link=link)
    assert (got["ceiling"], got["start"]) == ("900m", "600m") and got["cap_by"] is None


def test_1gbe_everywhere_is_exactly_todays_numbers(conn):
    """שרת 1G ומחשבי שיכפול שמדווחים 1G — בדיוק 900m/600m."""
    _report(conn, SLOW_MAC, [_nic(1000)])
    _report(conn, OTHER_MAC, [_nic(1000)])
    got = drive_rates.choose(conn, [_on(SLOW_MAC, "S1"), _on(OTHER_MAC, "S2")], None,
                             link=_link(1000))
    assert (got["ceiling"], got["start"], got["max_bitrate"], got["basis"]) == (
        "900m", "600m", "600m", "start")


def test_a_slow_cloner_keeps_the_floor_logic(conn):
    """כרטיס 100Mb/s: גג 90m, פתיחה 60m, ורצפה רבע מהפתיחה — אותה נוסחה."""
    _report(conn, SLOW_MAC, [_nic(100)])
    got = drive_rates.choose(conn, [_on(SLOW_MAC, "S1")], None, link=_link(1000))
    assert (got["ceiling"], got["start"]) == ("90m", "60m")
    for n in range(12):
        rate = drive_rates.decide(conn, f"ses_{n}", lambda: [_on(SLOW_MAC, "S1")], None,
                                  link=lambda: _link(1000))
        drive_rates.record_stream(conn, f"ses_{n}", {
            "sent_bytes": 10 ** 10, "loss_blocks": 10 ** 6, "stream_bps": None,
            "stream_s": None})
    assert rate == "15m"


def test_a_direct_wave_is_capped_by_its_receivers(conn):
    _report(conn, SLOW_MAC, [_nic(100)])
    got = drive_rates.choose(conn, [_on(SLOW_MAC, "S1")], None, direct=True,
                             link=_link(10000))
    assert got["max_bitrate"] == "60m" and got["cap_by"] == "cloner"


def test_the_console_sets_the_speed_by_hand_admin_only_and_journaled(nic_room):
    from test_server_room import CLONER1                                # noqa: PLC0415
    admin, deploy, ctx = nic_room["admin"], nic_room["deploy"], nic_room["ctx"]
    url = "/api/console/machines/" + CLONER1
    assert deploy.put(url, json={"nic_speed_mbps": 1000}).status_code == 403
    for bad in (100, "1000", True, 5000):
        assert admin.put(url, json={"nic_speed_mbps": bad}).status_code == 400, bad
    assert admin.put(url, json={"nic_speed_mbps": 2500}).status_code == 200
    row = next(m for m in admin.get("/api/console/machines").json() if m["mac"] == CLONER1)
    assert row["nic_speed"] == {"speed_mbps": 2500, "source": "manual", "why": None}
    assert admin.put(url, json={"nic_speed_mbps": None}).status_code == 200
    row = next(m for m in admin.get("/api/console/machines").json() if m["mac"] == CLONER1)
    assert row["nic_speed"]["source"] == "assumed"
    details = [r["detail"] for r in ctx.conn.execute(
        "SELECT detail FROM journal WHERE event = 'machine_nic_speed' ORDER BY id")]
    assert details == [f"{CLONER1} 2500Mb/s", f"{CLONER1} auto"]


def test_a_10g_server_with_unknown_cloners_sends_600m_and_names_the_assumption(nic_room):
    from test_room_learned_rate import _open_and_join, _sent_bitrate  # noqa: PLC0415
    from test_server_room import CLONER2, room                        # noqa: PLC0415
    admin = nic_room["admin"]
    nic_speed.set_manual(nic_room["ctx"].conn, CLONER2, None)     # חוזר ל"לא ידוע"
    _open_and_join(nic_room)
    assert admin.post("/api/console/room/start").status_code == 200
    assert _sent_bitrate(nic_room) == "600m"
    shown = room(admin)["round"]["wave_rate"]
    assert shown["cap_by"] == "cloner" and shown["cloner"]["mac"] == CLONER2
    assert shown["cloner"]["source"] == "assumed"
