"""‏Wake-on-LAN — קבוצה 7 בתוכנית הבדיקות (`docs/lab-test-plan.md`).

עד כאן ל-`server/net/wol.py` לא היה קובץ טסטים משלו: מה שנבדק (חבילת
הקסם, ההחרגה של הפותח) נבדק דרך `test_station.py`, כלומר דרך פתיחת
סבב. כאן נבדקת היחידה עצמה — בניית החבילה, מאיפה מגיעים ה-MACים,
לאן החבילה יוצאת, ומה קורה כששליחה נכשלת.

**לעולם לא יוצאת מכאן חבילה אמיתית.** השליחה מוזרקת (`send`), והשקע
מוזרק (`socket_factory`) — כמו שה-DHCP מזריק `dhcp_hooks` ולא נוגע
במכונה. הבדיקות שכן דורשות רשת (7.2 על tcpdump, 7.7-7.8 על חומרה)
מתועדות בתוכנית ולא רצות כאן.
"""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from server.net import wol
from server.db import connect
from server.net.wol import (LINK_DOWN, LINK_UNKNOWN, LINK_UP, broadcast_sender,
                        link_state, magic_packet, wake_group)

MAC1 = "b4:2e:99:07:1a:c4"
MAC2 = "b4:2e:99:07:1a:c5"
CLONER = "aa:bb:cc:00:00:21"
STRANGER = "de:ad:be:ef:00:99"


# --- 7.1 — החבילה נבנית נכון -------------------------------------------------


def test_the_packet_is_six_ff_then_the_mac_sixteen_times():
    packet = magic_packet(MAC1)
    raw = bytes.fromhex("b42e99071ac4")
    assert len(packet) == 102                      # 6 + 16*6
    assert packet[:6] == b"\xff" * 6
    assert packet[6:] == raw * 16
    # שש-עשרה פעמים בדיוק — לא חמש-עשרה ולא שבע-עשרה.
    assert packet[6:].count(raw) == 16
    assert packet.count(b"\xff" * 6) == 1          # אין ריפוד FF נוסף


@pytest.mark.parametrize(
    "written",
    ["b4:2e:99:07:1a:c4", "B4:2E:99:07:1A:C4", "b4-2e-99-07-1a-c4",
     "B4-2E-99-07-1A-C4", "  b4:2E:99:07:1a:C4  "],
)
def test_every_way_of_writing_the_same_mac_gives_the_same_packet(written):
    """שלוש הווריאציות של סעיף 10 — הטבלה קנונית, אבל מי שיקרא לפונקציה
    עם מה שהודבק בקונסולה לא אמור לקבל כיתה ישנה."""
    assert magic_packet(written) == magic_packet(MAC1)


@pytest.mark.parametrize(
    "bad",
    ["not-a-mac", "", "b4:2e:99:07:1a", "b4:2e:99:07:1a:c4:c5",
     "zz:2e:99:07:1a:c4", "b42e99071ac", None, 42],
)
def test_a_bad_mac_raises_instead_of_sending_nonsense(bad):
    with pytest.raises(ValueError):
        magic_packet(bad)


# --- לאן החבילה יוצאת (הבסיס ל-7.2) ------------------------------------------


class FakeSocket:
    """שקע מזויף שרושם כל מה שנעשה עליו. שום בייט לא נוגע ברשת."""

    def __init__(self, family, type_):
        self.family, self.type = family, type_
        self.options: list[tuple] = []
        self.sent: list[tuple[bytes, tuple]] = []
        self.closed = False

    def setsockopt(self, level, option, value):
        self.options.append((level, option, value))

    def sendto(self, packet, address):
        self.sent.append((packet, address))
        return len(packet)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True


@pytest.fixture()
def sockets():
    made: list[FakeSocket] = []

    def factory(family, type_):
        made.append(FakeSocket(family, type_))
        return made[-1]

    return made, factory


def fake_sysfs(root: Path, interface: str, *, carrier: str | None = "1",
               operstate: str | None = "up") -> Path:
    """‏/sys/class/net מזויף. ‏None לקובץ = הקובץ לא קיים; במקומו נוצרת
    תיקייה כשצריך קובץ ש**קיים ואי אפשר לקרוא** — בדיוק מה ש-`carrier`
    של ממשק כבוי עושה בקרנל (EINVAL), ומה שגם root לא יכול לעקוף."""
    root.mkdir(parents=True, exist_ok=True)
    entry = root / interface
    entry.mkdir(parents=True, exist_ok=True)
    for name, value in (("carrier", carrier), ("operstate", operstate)):
        if value == "!unreadable":
            (entry / name).mkdir()
        elif value is not None:
            (entry / name).write_text(value + "\n")
    return root


@pytest.fixture()
def live_net(tmp_path: Path) -> Path:
    """ממשק שה-sysfs שלו אומר במפורש שיש קישור."""
    return fake_sysfs(tmp_path / "sys", "eth1.44")


def test_the_packet_goes_out_as_udp_broadcast_to_port_9(sockets):
    made, factory = sockets
    broadcast_sender(socket_factory=factory)(magic_packet(MAC1))
    sock = made[0]
    assert (sock.family, sock.type) == (socket.AF_INET, socket.SOCK_DGRAM)
    assert (socket.SOL_SOCKET, socket.SO_BROADCAST, 1) in sock.options
    assert sock.sent == [(magic_packet(MAC1), ("255.255.255.255", 9))]
    assert sock.closed


@pytest.mark.skipif(not hasattr(socket, "SO_BINDTODEVICE"),
                    reason="SO_BINDTODEVICE is Linux-only")
def test_a_named_interface_is_pinned_so_the_packet_cannot_leak(sockets, live_net):
    """‏7.2 בקוד: החבילה נכפית על ממשק וילן ההפצה. הלכידה ב-tcpdump
    על שני הממשקים היא הצד שנשאר למעבדה."""
    made, factory = sockets
    broadcast_sender("eth1.44", socket_factory=factory,
                     sysfs=live_net)(magic_packet(MAC1))
    assert (socket.SOL_SOCKET, socket.SO_BINDTODEVICE, b"eth1.44\x00") \
        in made[0].options


def test_without_so_bindtodevice_a_pinned_send_fails_instead_of_leaking(
        sockets, monkeypatch):
    """אין דרך לכפות ממשק → לא משדרים לכל הרשתות "ליתר ביטחון"."""
    made, factory = sockets
    monkeypatch.delattr(socket, "SO_BINDTODEVICE", raising=False)
    send = broadcast_sender("eth1.44", socket_factory=factory)
    with pytest.raises(OSError):
        send(magic_packet(MAC1))
    assert made == []                              # שקע בכלל לא נפתח


# --- #74: הממשק חייב להוכיח שהוא חי --------------------------------------
#
# ‏`sendto` על ממשק ב-NO-CARRIER מצליח ושולח אפס חבילות (אומת במעבדה
# ‏29-08-2026 ב-tcpdump). היעדר החריגה אינו ראיה, ולכן נדרשת ראיה
# חיובית מ-sysfs לפני השליחה — ואבחנה בין "אין קישור" ל"לא בדקנו".


def test_a_missing_carrier_is_a_visible_failure_not_a_silent_success(
        sockets, tmp_path):
    """הבאג עצמו: הכבל מנותק, ‏`sendto` לא מתלונן, ואפס חבילות יוצאות."""
    made, factory = sockets
    sysfs = fake_sysfs(tmp_path / "sys", "eth0", carrier="0", operstate="down")
    send = broadcast_sender("eth0", socket_factory=factory, sysfs=sysfs)
    with pytest.raises(OSError):
        send(magic_packet(MAC1))
    assert made == []               # לא נפתח שקע ולא "נשלחה" חבילה


@pytest.mark.parametrize("operstate", ["up", "down", "lowerlayerdown"])
def test_the_failure_says_what_is_wrong_and_what_to_check(tmp_path, operstate):
    """"נכשל" שולח את הטכנאי ל-BIOS של 12 מחשבים. ההודעה חייבת לומר
    איזה ממשק, מה מצבו, ומה לבדוק — הכבל.

    הפרמטרים הם מה שנמדד במעבדה: כרטיס שהכבל שלו נשלף מדווח
    ‏`carrier=0` יחד עם ‏operstate שאינו up (‏veth: lowerlayerdown,
    ‏NIC פיזי: down). אם ‏operstate ייבדק ראשון, התשובה תהיה "הממשק
    כבוי, הרץ ip link set up" — עצה שגויה לכבל מנותק."""
    sysfs = fake_sysfs(tmp_path / "sys", "eth0", carrier="0",
                       operstate=operstate)
    state, why = link_state("eth0", sysfs=sysfs)
    assert state == LINK_DOWN
    assert "eth0" in why and "carrier" in why and "כבל" in why
    assert f"operstate={operstate}" in why      # הראיה הגולמית נשארת


def test_an_interface_that_is_administratively_down_is_caught_too(tmp_path):
    """‏`carrier` של ממשק כבוי מחזיר EINVAL — כלומר הקובץ קיים ואינו
    נקרא. ‏`operstate` הוא הראיה החיובית שנשארת, ואסור שהמצב הזה
    ייפול ל"לא ידוע" ויישלח לשום מקום."""
    sysfs = fake_sysfs(tmp_path / "sys", "eth0",
                       carrier="!unreadable", operstate="down")
    state, why = link_state("eth0", sysfs=sysfs)
    assert state == LINK_DOWN and "eth0" in why


def test_an_interface_absent_from_a_readable_sysfs_fails(tmp_path):
    """שם ממשק שגוי בהגדרות — ‏sysfs נקרא ואומר שאין חיה כזאת."""
    sysfs = fake_sysfs(tmp_path / "sys", "eth1")
    state, why = link_state("eth0", sysfs=sysfs)
    assert state == LINK_DOWN and "eth0" in why


def test_a_carrier_that_says_one_is_the_positive_evidence_we_send_on(tmp_path):
    sysfs = fake_sysfs(tmp_path / "sys", "eth0", carrier="1", operstate="up")
    assert link_state("eth0", sysfs=sysfs) == (LINK_UP, "")


# --- והבאג ההפוך: "לא הצלחנו לבדוק" אינו "אין carrier" -----------------------


@pytest.mark.skipif(not hasattr(socket, "SO_BINDTODEVICE"),
                    reason="SO_BINDTODEVICE is Linux-only")  # issue-312-windows-env
def test_no_sysfs_at_all_is_unknown_and_still_sends(sockets, tmp_path):
    """מכונה שאינה Linux / קונטיינר בלי ‏/sys: הבדיקה לא זמינה. חסימת
    WoL כאן הייתה הופכת תקלה באבחון לכיתה שלמה שלא מתעוררת."""
    made, factory = sockets
    absent = tmp_path / "no-sysfs-here"
    assert link_state("eth0", sysfs=absent)[0] == LINK_UNKNOWN
    broadcast_sender("eth0", socket_factory=factory,
                     sysfs=absent)(magic_packet(MAC1))
    assert made[0].sent                          # החבילה כן יצאה


@pytest.mark.skipif(not hasattr(socket, "SO_BINDTODEVICE"),
                    reason="SO_BINDTODEVICE is Linux-only")  # issue-312-windows-env
def test_an_unreadable_link_state_is_unknown_and_still_sends(sockets, tmp_path):
    """ממשק וירטואלי / קבצים שלא נקראים: ‏operstate=unknown ו-carrier
    שאינו נקרא. לא ראינו קישור — אבל גם לא ראינו את היעדרו."""
    made, factory = sockets
    sysfs = fake_sysfs(tmp_path / "sys", "eth0",
                       carrier="!unreadable", operstate="unknown")
    state, why = link_state("eth0", sysfs=sysfs)
    assert state == LINK_UNKNOWN and "eth0" in why
    broadcast_sender("eth0", socket_factory=factory,
                     sysfs=sysfs)(magic_packet(MAC1))
    assert made[0].sent


def test_operstate_up_alone_is_enough_when_carrier_cannot_be_read(tmp_path):
    """ראיה חיובית אחת מספיקה: אם ‏operstate אומר up, אין צורך ב-carrier."""
    sysfs = fake_sysfs(tmp_path / "sys", "eth0",
                       carrier="!unreadable", operstate="up")
    assert link_state("eth0", sysfs=sysfs)[0] == LINK_UP


def test_a_virtual_interface_with_carrier_one_is_up_despite_unknown_operstate(
        tmp_path):
    """‏veth/tap מדווחים operstate=unknown לנצח — ובמעבדה הווירטואלית
    זה הממשק היחיד שיש. ‏carrier=1 הוא הראיה."""
    sysfs = fake_sysfs(tmp_path / "sys", "eth0", carrier="1",
                       operstate="unknown")
    assert link_state("eth0", sysfs=sysfs)[0] == LINK_UP


def test_no_interface_pinned_means_nothing_to_check(sockets, tmp_path):
    """בלי ממשק כפוי הניתוב בוחר, ואין ממשק לבדוק — לא ממציאים כשל."""
    made, factory = sockets
    broadcast_sender(socket_factory=factory,
                     sysfs=tmp_path / "nope")(magic_packet(MAC1))
    assert made[0].sent


# --- הראיה השנייה: מה ש-sendto דיווח ----------------------------------------


def test_a_partial_sendto_is_a_failure_not_a_success(tmp_path):
    """‏`sendto` שמחזיר פחות בייטים ממה שנתנו לו לא שלח חבילת קסם —
    שלד של חבילה לא מעיר אף מחשב."""
    class ShortSocket(FakeSocket):
        def sendto(self, packet, address):
            super().sendto(packet, address)
            return len(packet) - 1

    send = broadcast_sender(socket_factory=lambda f, t: ShortSocket(f, t))
    with pytest.raises(OSError):
        send(magic_packet(MAC1))


# --- הקבוצה: 7.3, 7.4, 7.5 ---------------------------------------------------


@pytest.fixture()
def conn(tmp_path: Path):
    """טבלת MAC בלבד — שום מכונה לא "דיברה" עם השרת מעולם."""
    conn = connect(tmp_path / "wol.db")
    # ‏grp_CLONERS נזרעת ב-connect (קבוצה קבועה) — רק הכיתה נוספת כאן.
    conn.execute("INSERT INTO groups (id, label, role) VALUES (?, ?, ?)",
                 ("grp_LAB1", "כיתה LAB1", "classroom"))
    for mac, suffix, group in ((MAC1, "05", "grp_LAB1"),
                               (MAC2, "06", "grp_LAB1"),
                               (CLONER, "01", "grp_CLONERS")):
        conn.execute(
            "INSERT INTO machines (mac, suffix, group_id, added_at)"
            " VALUES (?, ?, ?, '2026-08-28T00:00:00Z')", (mac, suffix, group))
    conn.commit()
    return conn


def macs_of(packets: list[bytes]) -> set[str]:
    """מהחבילות חזרה ל-MACים — כדי לבדוק *למי* נשלח, לא רק כמה."""
    return {":".join(f"{b:02x}" for b in packet[6:12]) for packet in packets}


def test_waking_a_class_sends_to_every_mac_in_it(conn):
    """‏7.3 — הערת קבוצה שלמה, לא מכונה אחת."""
    packets: list[bytes] = []
    assert wake_group(conn, "grp_LAB1", send=packets.append) == 2
    assert macs_of(packets) == {MAC1, MAC2}


def test_the_mac_table_is_the_source_not_who_talked_to_the_server(conn):
    """‏7.4 — העיקרון. אף מכונה בטבלה לא עשתה hello מעולם (‏net_devices
    ריקה), והן חייבות לקבל חבילה: מכונה כבויה היא בדיוק המקרה."""
    assert conn.execute("SELECT COUNT(*) AS n FROM net_devices"
                        ).fetchone()["n"] == 0
    packets: list[bytes] = []
    wake_group(conn, "grp_LAB1", send=packets.append)
    assert macs_of(packets) == {MAC1, MAC2}


def test_an_unknown_mac_gets_nothing(conn):
    """‏7.5 — מי שלא בטבלה לא מקבל חבילה, גם לא "ליתר ביטחון"."""
    packets: list[bytes] = []
    wake_group(conn, "grp_LAB1", send=packets.append)
    assert STRANGER not in macs_of(packets)
    # וגם קבוצה ריקה/לא קיימת אינה מעירה איש ואינה קורסת.
    assert wake_group(conn, "grp_NOPE", send=packets.append) == 0


def test_a_group_wakes_only_its_own_machines(conn):
    packets: list[bytes] = []
    assert wake_group(conn, "grp_CLONERS", send=packets.append) == 1
    assert macs_of(packets) == {CLONER}


def test_the_opener_is_not_woken_however_its_mac_is_written(conn):
    packets: list[bytes] = []
    assert wake_group(conn, "grp_LAB1", exclude_mac="B4-2E-99-07-1A-C4",
                      send=packets.append) == 1
    assert macs_of(packets) == {MAC2}


def test_a_chosen_roster_wakes_only_the_chosen(conn):
    packets: list[bytes] = []
    assert wake_group(conn, "grp_LAB1", only={MAC2},
                      send=packets.append) == 1
    assert macs_of(packets) == {MAC2}


# --- כשל שליחה לא נבלע -------------------------------------------------------


def test_one_failed_send_does_not_stop_the_rest_but_is_recorded(conn):
    """מוטב 29 ערות ואחת לא — אבל לא בשקט: הכישלון נרשם ביומן,
    אחרת הוא נראה בדיוק כמו WoL כבוי ב-BIOS."""
    tried: list[bytes] = []

    def flaky(packet):
        tried.append(packet)
        if len(tried) == 1:
            raise OSError("network is unhappy")

    assert wake_group(conn, "grp_LAB1", send=flaky) == 1     # נספרה רק ההצלחה
    assert len(tried) == 2                                   # השנייה כן נוסתה
    row = conn.execute(
        "SELECT event, detail FROM journal WHERE event = 'wol_failed'"
    ).fetchone()
    assert row is not None and "failed=1" in row["detail"] and MAC1 in row["detail"]


def test_a_fully_failed_wake_is_still_visible_in_the_journal(conn):
    """הבליעה המסוכנת: כל השליחות נכשלו, `sent` הוא 0, ו-app.py רושם
    ביומן רק כש-`sent` גדול מאפס — בלי השורה הזאת לא נשאר שום סימן."""
    def dead(packet):
        raise OSError("no route to host")

    assert wake_group(conn, "grp_LAB1", send=dead) == 0
    row = conn.execute(
        "SELECT detail FROM journal WHERE event = 'wol_failed'").fetchone()
    assert row is not None and "failed=2" in row["detail"]


@pytest.mark.skipif(not hasattr(socket, "SO_BINDTODEVICE"),
                    reason="SO_BINDTODEVICE is Linux-only")  # issue-312-windows-env
def test_the_journal_carries_the_reason_and_not_only_the_count(conn, tmp_path):
    """‏#74 מקצה לקצה ביחידה: כבל אחד מנותק → אפס נספרות, והיומן אומר
    *למה*. בלי המשפט הזה השורה אומרת "12 נכשלו" ושולחת את הטכנאי
    לחפש WoL ב-BIOS — בדיוק התסמין שהבאג ייצר."""
    sysfs = fake_sysfs(tmp_path / "sys", "eth0", carrier="0", operstate="up")
    send = broadcast_sender("eth0", socket_factory=lambda f, t: FakeSocket(f, t),
                            sysfs=sysfs)

    assert wake_group(conn, "grp_LAB1", send=send) == 0
    detail = conn.execute(
        "SELECT detail FROM journal WHERE event = 'wol_failed'").fetchone()["detail"]
    assert "failed=2" in detail
    assert "carrier" in detail and "eth0" in detail and "כבל" in detail
    # הסיבה נרשמת פעם אחת, לא פעם לכל מחשב.
    assert detail.count("no-carrier") == 1


def test_a_clean_wake_writes_no_failure_line(conn):
    wake_group(conn, "grp_LAB1", send=lambda packet: None)
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM journal WHERE event = 'wol_failed'"
    ).fetchone()["n"] == 0


def test_the_module_default_never_touches_the_network_in_tests():
    """שמירה על הטסטים עצמם: אם מישהו ישכח להזריק `send`, זה ייראה
    כאן — ברירת המחדל היא שולח אמיתי."""
    assert wol._send_broadcast.__qualname__.startswith("broadcast_sender")


# --- 7.6 — הכפתור בקונסולה ---------------------------------------------------


@pytest.fixture()
def server(tmp_path: Path, images_root, classrooms_on):
    """שרת אמיתי עם WoL נתפס, ושתי קבוצות: כיתה ומחשבי שיכפול."""
    from fastapi.testclient import TestClient

    from server.console import users
    from server.app import create_app

    woken: list[bytes] = []
    app = create_app(tmp_path / "data", images_root, "http://10.44.12.10:8080",
                     wol_send=woken.append)
    ctx = app.state.ctx
    users.create(ctx.conn, "noc", "admin-pass-123", "admin", by="test", is_builtin=True, check_policy=False)
    users.create(ctx.conn, "labtech", "deploy-pass-1", "deploy", by="test", check_policy=False)

    admin, deploy = TestClient(app), TestClient(app)
    admin.post("/api/console/login",
               json={"username": "noc", "password": "admin-pass-123"})
    deploy.post("/api/console/login",
                json={"username": "labtech", "password": "deploy-pass-1"})
    admin.post("/api/console/groups",
               json={"id": "grp_LAB1", "label": "כיתה LAB1", "role": "classroom"})
    for mac, name in ((MAC1, "05"), (MAC2, "06")):
        assert admin.post("/api/console/machines", json={
            "mac": mac, "name": name, "group_id": "grp_LAB1"}).status_code == 200
    assert admin.post("/api/console/machines", json={
        "mac": CLONER, "name": "shich-1", "group_id": "grp_CLONERS",
    }).status_code == 200
    yield {"admin": admin, "deploy": deploy, "anon": TestClient(app),
           "woken": woken, "ctx": ctx}
    ctx.sender.stop()


def test_the_console_wake_button_wakes_the_room_and_nothing_else(server):
    """‏7.6 — הקבוצה אינה נבחרת מהדפדפן. בקשה שמנסה להכתיב קבוצה אחרת אינה
    מעירה דבר (17/10: גוף בלי `macs` הוא 400, לא "כל החדר"); בלי גוף — מחשבי
    השיכפול בלבד."""
    result = server["deploy"].post("/api/console/room/wake",
                                   json={"group_id": "grp_LAB1"})
    assert result.status_code == 400 and server["woken"] == []
    result = server["deploy"].post("/api/console/room/wake")
    assert result.status_code == 200 and result.json()["sent"] == 1
    assert macs_of(server["woken"]) == {CLONER}      # לא הכיתה


def test_the_wake_button_is_closed_to_anonymous(server):
    assert server["anon"].post("/api/console/room/wake").status_code in (401, 403)
    assert server["woken"] == []


def test_a_deploy_user_may_wake_and_an_admin_may_too(server):
    """הערה היא חלק מהפעלת סבב — משתמש הפצה מורשה (‏5.7), ולא נופל
    ל-403 שנועד לניהול."""
    assert server["deploy"].post("/api/console/room/wake").status_code == 200
    assert server["admin"].post("/api/console/room/wake").status_code == 200
    assert len(server["woken"]) == 2                 # פעם לכל קריאה


def test_the_wake_is_written_to_the_journal_with_the_user(server):
    server["deploy"].post("/api/console/room/wake")
    row = server["ctx"].conn.execute(
        "SELECT user, detail FROM journal WHERE event = 'wol_sent'").fetchone()
    assert row["user"] == "labtech" and "count=1" in row["detail"]


def test_the_real_server_pins_wol_to_the_deployment_vlan(tmp_path, monkeypatch):
    """‏#44 הוסיף את היכולת לכפות ממשק, ואיש לא חיבר אותה למסלול האמיתי.

    הטסטים האחרים מזריקים שולח מזויף, ולכן עברו בעוד שהשרת האמיתי שידר
    לפי טבלת הניתוב — במעבדה (בדיקה 7.2) יצאו 12 חבילות על הרשת הרגילה
    ואפס על וילן ההפצה. כאן נבדק מה ש-`create_app` בונה **כשאיש לא
    מזריק**: יכולת שקיימת ואינה מחוברת נראית בדיוק כמו יכולת שעובדת.
    """
    from server import app as app_module

    pinned = []

    def fake_sender(interface=None, **kwargs):
        pinned.append(interface)
        return lambda packet: None

    monkeypatch.setattr(app_module.wol, "broadcast_sender", fake_sender)
    app_module.create_app(tmp_path / "d", tmp_path / "i",
                          "http://10.44.0.1:8080", interface="eth0")
    assert pinned == ["eth0"], "השרת לא כפה את ממשק וילן ההפצה על WoL"


# --- הסיבה מגיעה למסך, לא רק ליומן (#74) -------------------------------------


def test_the_counter_still_counts_and_carries_the_reason_with_it(conn):
    """‏WakeResult הוא `int` — כל מי שסופר אותו ממשיך לעבוד בלי לדעת."""
    def dead(_packet):
        raise OSError("no-carrier: אין carrier על eth0")

    result = wake_group(conn, "grp_LAB1", send=dead)
    assert result == 0                      # נספר כמו קודם
    assert int(result) + 1 == 1             # ומתנהג כמספר
    assert result.failed                    # ונושא את מי שנכשל
    assert result.reasons == ["no-carrier: אין carrier על eth0"]


def test_one_cable_gives_one_reason_and_not_twelve(conn):
    """כשכבל אחד מנותק כל הכשלים הם אותו משפט — פעם אחת."""
    def dead(_packet):
        raise OSError("no-carrier: אין carrier על eth0")

    result = wake_group(conn, "grp_LAB1", send=dead)
    assert len(result.failed) > 1
    assert len(result.reasons) == 1


def test_a_successful_wake_carries_no_reason(conn):
    """שקט הוא שקט — אין מה להציג על המסך כשהכול נשלח."""
    result = wake_group(conn, "grp_LAB1", send=lambda packet: None)
    assert result > 0 and result.failed == [] and result.reasons == []


def test_the_wake_endpoint_hands_the_reason_to_the_console(server):
    """‏"0 מחשבים" בלי סיבה שולח את הטכנאי ל-12 BIOSים.

    הראיה שהמסך *יכול* לומר למה: השדות יוצאים מה-API. הניסוח עצמו
    ב-`room.js` אינו מכוסה — אין JS test runner בריפו.
    """
    body = server["deploy"].post("/api/console/room/wake").json()
    assert set(body) >= {"sent", "failed", "reasons"}
    assert body["failed"] == 0 and body["reasons"] == []


def test_the_wake_endpoint_reports_sent_not_woken(server):
    """‏#528 — המודול מונה `sent` (חבילות שיצאו), וה-API החזיר `woken`
    (מכונות שהתעוררו). חבילת WoL היא UDP broadcast בלי ACK: `sendto`
    שהצליח פירושו שהקרנל קיבל 102 בייט, ולא שמכונה קמה. השם `woken`
    טען טענה שלא נמדדה (עיקרון 5), ולכן החזרתו חייבת להפיל את הטסט."""
    body = server["deploy"].post("/api/console/room/wake").json()
    assert "sent" in body            # השם שהמודול באמת מדד
    assert "woken" not in body       # השם שטען התעוררות שלא נמדדה
