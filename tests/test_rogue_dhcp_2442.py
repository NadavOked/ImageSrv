"""#2442 (א): שרת DHCP/proxyDHCP זר ברשת ההפצה — זיהוי, יומן, שורה אדומה.

אין כאן רשת. הסוקט המסונן (AF_PACKET) מזויף בגבול המערכת: מסגרות Ethernet
אמיתיות עוברות דרך `parse_reply` האמיתי וה-`Watch` האמיתי. מה שנבדק הוא ההחלטה:
מי זר, מתי כותבים ליומן, מתי השורה אדומה, ומתי "לא הצלחנו להאזין" אינו "נקי".
"""

from __future__ import annotations

import re
import socket
import struct
import threading
from datetime import datetime, timedelta, timezone

import pytest

from server.console.journal_he import EVENTS_HE, severity
from server.console.journal_he_detail import he_detail
from server.db import connect, journal
from server.net import dhcp_host, rogue_dhcp as rd

CARD_MAC = "02:00:00:00:00:01"
CARD_IP = "10.66.0.1"
ROGUE_MAC, ROGUE_IP = "02:aa:bb:cc:dd:01", "10.66.0.99"
OUTGOING, BROADCAST = 4, 1
XID = b"\x11\x22\x33\x44"


def reply_frame(*, mac=ROGUE_MAC, ip=ROGUE_IP, msg_type=2, yiaddr="10.66.0.50",
                options=b"", sport=67, dport=68, op=2) -> bytes:
    bootp = (bytes([op, 1, 6, 0]) + XID + bytes(8) + socket.inet_aton(yiaddr) + bytes(216)
             + b"\x63\x82\x53\x63" + b"\x35\x01" + bytes([msg_type]) + options + b"\xff")
    udp = struct.pack("!HHHH", sport, dport, 8 + len(bootp), 0) + bootp
    packet = (bytes([0x45, 0]) + struct.pack("!H", 20 + len(udp)) + bytes(4) + bytes([64, 17])
              + bytes(2) + socket.inet_aton(ip) + socket.inet_aton("255.255.255.255") + udp)
    return b"\xff" * 6 + bytes.fromhex(mac.replace(":", "")) + b"\x08\x00" + packet


PXE_OPTIONS = b"\x3c\x09PXEClient" + b"\x2b\x04\x06\x01\x08\xff"[:6]


def proxy_frame(**kw) -> bytes:
    return reply_frame(yiaddr="0.0.0.0", options=PXE_OPTIONS, **kw)


# --- פענוח המסגרת -----------------------------------------------------------------


def test_a_dhcp_offer_is_read_with_its_source_mac_and_ip():
    assert rd.parse_reply(reply_frame()) == rd.Reply(ROGUE_MAC, ROGUE_IP, "offer")


def test_an_offer_with_no_address_and_pxe_options_is_a_proxy_dhcp():
    assert rd.parse_reply(proxy_frame()) == rd.Reply(ROGUE_MAC, ROGUE_IP, "proxy")


def test_pxe_options_on_an_offer_that_hands_out_an_address_is_still_a_dhcp_server():
    frame = reply_frame(options=PXE_OPTIONS)
    assert rd.parse_reply(frame).kind == "offer"


@pytest.mark.parametrize("frame", [
    reply_frame(msg_type=5),                  # ACK, לא OFFER
    reply_frame(msg_type=1, op=1, sport=68, dport=67),   # DISCOVER של לקוח
    reply_frame(sport=39000, dport=39001),    # מולטיקאסט של סבב
    reply_frame(sport=67, dport=67),
    reply_frame()[:60],                       # קצרה
    b"\x00" * 10,
    b"\xff" * 12 + b"\x08\x06" + b"\x00" * 300,   # ARP
])
def test_anything_that_is_not_a_dhcp_offer_is_ignored(frame):
    assert rd.parse_reply(frame) is None


def test_an_ip_fragment_is_ignored():
    frame = bytearray(reply_frame())
    frame[14 + 6:14 + 8] = struct.pack("!H", 0x0001)
    assert rd.parse_reply(bytes(frame)) is None


# --- הסוקט המזויף ----------------------------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.now = 10_000.0

    def __call__(self) -> float:
        return self.now


class FakeTap:
    """פריט בתסריט: `(frame, packet_type)`, או מספר = שניות שעוברות בלי מסגרת
    (פקיעת הפרוסה). כשהתסריט נגמר — פקיעה."""

    def __init__(self, clock: Clock, script: list, drops: int = 0) -> None:
        self.clock, self.script, self.drops = clock, list(script), drops
        self.timeouts: list[float] = []
        self.recvs = 0
        self.closed = False

    def settimeout(self, value: float) -> None:
        self.timeouts.append(value)

    def recvfrom(self, size: int):
        self.recvs += 1
        if not self.script:
            self.clock.now += self.timeouts[-1]
            raise socket.timeout()
        item = self.script.pop(0)
        if isinstance(item, (int, float)):
            self.clock.now += item
            raise socket.timeout()
        frame, packet_type = item
        return frame, ("eth1", 0x800, packet_type, 1, b"")

    def getsockopt(self, level, name, size):
        assert (level, name) == (dhcp_host.SOL_PACKET, dhcp_host.PACKET_STATISTICS)
        return struct.pack("II", 7, self.drops)

    def close(self) -> None:
        self.closed = True


@pytest.fixture()
def conn(tmp_path):
    return connect(tmp_path / "imagesrv.db")


def make(conn, clock, script=(), *, gate=lambda: None, drops=0, **kw):
    taps: list[FakeTap] = []

    def opener(interface):
        taps.append(FakeTap(clock, script, drops))
        return taps[-1]

    watch = rd.Watch(conn, "eth1", gate=gate, opener=opener, clock=clock,
                     own=lambda: (frozenset({CARD_MAC}), frozenset({CARD_IP})), **kw)
    return watch, taps


def rows(conn):
    return [dict(r) for r in conn.execute(
        "SELECT event, detail FROM journal WHERE event = ?", (rd.EVENT,))]


def run_step(watch):
    watch.step(threading.Event())


# --- זיהוי, יומן, שורה אדומה ---------------------------------------------------------


def test_a_foreign_offer_is_a_journal_event_and_a_red_row(conn):
    clock = Clock()
    watch, _ = make(conn, clock, [(reply_frame(), BROADCAST)])
    run_step(watch)
    assert rows(conn) == [{"event": "foreign_dhcp_seen",
                           "detail": f"mac={ROGUE_MAC} ip={ROGUE_IP} kind=offer nic=eth1"}]
    state, detail = watch.health()
    assert state == "bad", (state, detail)
    assert ROGUE_MAC in detail and ROGUE_IP in detail and "DHCPOFFER" in detail


def test_a_foreign_proxy_dhcp_is_named_as_such(conn):
    watch, _ = make(conn, Clock(), [(proxy_frame(), BROADCAST)])
    run_step(watch)
    assert "kind=proxy" in rows(conn)[0]["detail"]
    assert watch.health()[0] == "bad" and "proxyDHCP" in watch.health()[1]


def test_a_quiet_card_that_is_being_listened_to_is_green_and_says_what_it_cannot_see(conn):
    watch, _ = make(conn, Clock())
    run_step(watch)
    state, detail = watch.health()
    assert state == "ok" and "snooping" in detail
    assert rows(conn) == []


def test_our_own_server_is_not_foreign(conn):
    """"אני" = מסגרת שיצאה מהמארח (הקרנל אומר זאת). ה-MAC וה-IP שבמסגרת — לא (#2442, 05/11)."""
    script = [(reply_frame(mac=CARD_MAC, ip=CARD_IP), OUTGOING),
              (reply_frame(mac=ROGUE_MAC), OUTGOING)]
    watch, _ = make(conn, Clock(), script)
    run_step(watch)
    assert rows(conn) == [] and watch.health()[0] == "ok"


def test_the_same_frames_from_someone_else_are_foreign(conn):
    """בקרה: אותן מסגרות, בלי שלושת סימני "אני" — כולן זרות."""
    script = [(reply_frame(mac="02:aa:bb:cc:dd:02", ip="10.66.0.98"), BROADCAST),
              (reply_frame(mac="02:aa:bb:cc:dd:03", ip="10.66.0.97"), BROADCAST),
              (reply_frame(mac="02:aa:bb:cc:dd:04", ip="10.66.0.96"), BROADCAST)]
    watch, _ = make(conn, Clock(), script)
    run_step(watch)
    assert len(rows(conn)) == 3 and watch.health()[0] == "bad"


def test_the_journal_event_is_once_per_source_per_hour(conn):
    clock = Clock()
    watch, _ = make(conn, clock)
    for _ in range(5):
        watch.observe(reply_frame(), BROADCAST, clock(), frozenset(), frozenset())
        clock.now += 60
    assert len(rows(conn)) == 1
    watch.observe(reply_frame(mac="02:aa:bb:cc:dd:02"), BROADCAST, clock(),
                  frozenset(), frozenset())
    assert len(rows(conn)) == 2                      # מקור אחר — אירוע משלו
    clock.now += rd.JOURNAL_EVERY
    watch.observe(reply_frame(), BROADCAST, clock(), frozenset(), frozenset())
    assert len(rows(conn)) == 3                      # אותו מקור, שעה אחרי


def test_the_red_row_stays_until_the_stranger_is_gone_for_the_hold_time(conn):
    clock = Clock()
    watch, _ = make(conn, clock, [(reply_frame(), BROADCAST)])
    run_step(watch)
    seen = watch.sources[(ROGUE_MAC, ROGUE_IP)].last_seen
    clock.now = seen + rd.HOLD_SECONDS - 1
    watch.slice_done = clock()                        # ההאזנה עדיין חיה
    assert watch.health()[0] == "bad"
    clock.now = seen + rd.HOLD_SECONDS
    watch.slice_done = clock()
    assert watch.health()[0] == "ok"


def test_seeing_the_stranger_again_extends_the_red_row(conn):
    clock = Clock()
    watch, _ = make(conn, clock, [(reply_frame(), BROADCAST)])
    run_step(watch)
    clock.now += rd.HOLD_SECONDS - 100
    watch.observe(reply_frame(), BROADCAST, clock(), frozenset(), frozenset())
    clock.now += rd.HOLD_SECONDS - 100
    assert watch.health()[0] == "bad"


# --- מתי השומר פעיל -------------------------------------------------------------------


def test_with_our_dhcp_off_nothing_is_opened_and_nothing_is_recorded(conn):
    watch, taps = make(conn, Clock(), [(reply_frame(), BROADCAST)],
                       gate=lambda: "ה-DHCP של השרת כבוי")
    stop = threading.Event()
    stop.set()                                        # לא ממתינים GATE_RECHECK אמיתי
    watch.step(stop)
    assert taps == [] and rows(conn) == []
    assert watch.health() == ("off", "כבוי: ה-DHCP של השרת כבוי")


def test_turning_our_dhcp_on_starts_the_listening_and_off_closes_the_socket(conn):
    clock, on = Clock(), {"value": True}
    watch, taps = make(conn, clock, gate=lambda: None if on["value"] else "כבוי")
    run_step(watch)
    assert len(taps) == 1 and not taps[0].closed
    on["value"] = False
    stop = threading.Event()
    stop.set()
    watch.step(stop)
    assert taps[0].closed and watch.health()[0] == "off"


# --- "לא הצלחנו להאזין" אינו "אין שרת זר" (עיקרון 5) -------------------------------------


def run_forever_once(watch, monkeypatch):
    monkeypatch.setattr(rd, "RETRY_SECONDS", 0.01)
    stop = threading.Event()
    real_step = watch.step

    def step_then_stop(event):
        try:
            real_step(event)
        finally:
            stop.set()

    watch.step = step_then_stop
    watch.run_forever(stop)


def test_a_listener_that_cannot_open_is_not_clean(conn, monkeypatch):
    clock = Clock()
    watch = rd.Watch(conn, "eth1", gate=lambda: None, clock=clock,
                     own=lambda: (frozenset(), frozenset()),
                     opener=lambda iface: (_ for _ in ()).throw(PermissionError(1, "denied")))
    run_forever_once(watch, monkeypatch)
    state, detail = watch.health()
    assert state == "warn", (state, detail)
    assert "לא הצלחנו להאזין" in detail and "לא נבדק" in detail


def test_a_card_that_does_not_exist_is_not_clean(conn, monkeypatch):
    def missing():
        raise FileNotFoundError(2, "No such file", "/sys/class/net/eth1/address")

    watch = rd.Watch(conn, "eth1", gate=lambda: None, own=missing, clock=Clock(),
                     opener=lambda iface: pytest.fail("opened a socket for a missing card"))
    run_forever_once(watch, monkeypatch)
    assert watch.health()[0] == "warn" and "לא הצלחנו להאזין" in watch.health()[1]


def test_a_failure_after_a_stranger_was_seen_stays_red_and_says_so(conn, monkeypatch):
    clock = Clock()
    watch, _ = make(conn, clock, [(reply_frame(), BROADCAST)])
    run_step(watch)
    watch.error = "denied"
    state, detail = watch.health()
    assert state == "bad" and "denied" in detail


def test_not_listening_yet_is_unknown_not_green(conn):
    watch, _ = make(conn, Clock())
    assert watch.health()[0] == "unknown"


def test_a_listener_that_stopped_reporting_is_not_green(conn):
    clock = Clock()
    watch, _ = make(conn, clock)
    run_step(watch)
    clock.now += rd.STALE_SECONDS + 1
    assert watch.health()[0] == "warn"


@pytest.mark.parametrize("drops", [3])
def test_frames_the_kernel_dropped_could_have_been_the_offer(conn, drops):
    watch, _ = make(conn, Clock(), drops=drops)
    run_step(watch)
    state, detail = watch.health()
    assert state == "warn" and str(drops) in detail


# --- בלי polling ---------------------------------------------------------------------------


def test_the_listener_blocks_for_the_whole_slice_instead_of_polling(conn):
    clock = Clock()
    script = [(reply_frame(mac=f"02:aa:bb:cc:dd:0{i}"), BROADCAST) for i in range(1, 4)]
    watch, taps = make(conn, clock, script)
    run_step(watch)
    tap = taps[0]
    assert tap.recvs == len(script) + 1               # קריאה לכל מסגרת, ואחת שפקעה
    assert tap.timeouts[0] == pytest.approx(rd.SLICE_SECONDS)
    assert all(t > rd.SLICE_SECONDS - 1 for t in tap.timeouts)   # המתנה חוסמת, לא דגימה


def test_the_socket_stays_open_between_slices_and_closes_on_close(conn):
    watch, taps = make(conn, Clock())
    run_step(watch)
    run_step(watch)
    assert len(taps) == 1 and not taps[0].closed
    watch.close()
    assert taps[0].closed


def test_the_default_listener_is_the_kernel_filtered_one_from_the_2417_guard():
    assert rd.Watch.__init__.__kwdefaults__["opener"] is dhcp_host._open_tap


# --- אתחול השרת ---------------------------------------------------------------------------


def _journal_at(conn, age_seconds, detail):
    journal(conn, rd.EVENT, detail)
    ts = (datetime.now(timezone.utc) - timedelta(seconds=age_seconds)).isoformat(timespec="seconds")
    conn.execute("UPDATE journal SET ts = ? WHERE id = (SELECT MAX(id) FROM journal)", (ts,))
    conn.commit()


def test_a_restart_does_not_wipe_the_red_row(conn):
    _journal_at(conn, 600, f"mac={ROGUE_MAC} ip={ROGUE_IP} kind=offer nic=eth1")
    watch, _ = make(conn, Clock())
    watch.slice_done = watch.clock()
    state, detail = watch.health()
    assert state == "bad" and ROGUE_MAC in detail and "10 דק'" in detail


def test_the_restart_memory_ignores_old_events_and_other_cards(conn):
    _journal_at(conn, rd.HOLD_SECONDS + 60, f"mac={ROGUE_MAC} ip={ROGUE_IP} kind=offer nic=eth1")
    _journal_at(conn, 60, f"mac={ROGUE_MAC} ip=10.1.1.1 kind=offer nic=eth9")
    _journal_at(conn, 60, "not a record")
    watch, _ = make(conn, Clock())
    assert watch.sources == {}


# --- המסך והיומן העברי -----------------------------------------------------------------------


def test_the_journal_line_has_a_hebrew_label_a_severity_and_a_translated_detail():
    assert EVENTS_HE[rd.EVENT] and severity(rd.EVENT) == "err"
    text = he_detail(rd.EVENT, f"mac={ROGUE_MAC} ip={ROGUE_IP} kind=offer nic=eth1")
    assert "DHCPOFFER" in text and ROGUE_MAC in text and ROGUE_IP in text and "eth1" in text
    assert "kind=" not in text
    proxy = he_detail(rd.EVENT, f"mac={ROGUE_MAC} ip={ROGUE_IP} kind=proxy nic=eth1")
    assert "proxyDHCP" in proxy and "kind=" not in proxy


def test_the_health_screen_carries_the_row(tmp_path, images_root, clock, conn):
    pytest.importorskip("fastapi")
    from conftest import TestClient, complete_console_login
    from server.app import create_app
    from server.console import users

    watch, _ = make(conn, Clock(), [(reply_frame(), BROADCAST)])
    run_step(watch)
    app = create_app(tmp_path / "data", images_root, "http://10.44.12.10:8080", now_fn=clock,
                     health_hooks={"interfaces": lambda: [], "rogue_dhcp": watch.health})
    users.create(app.state.ctx.conn, "noc", "admin-pass-123", "admin", by="test",
                 check_policy=False)
    admin = TestClient(app)
    complete_console_login(admin, app.state.ctx.conn, "noc", "admin-pass-123")
    row = {r["id"]: r for r in admin.get("/api/console/health").json()}["rogue_dhcp"]
    assert row["state"] == "bad" and ROGUE_IP in row["detail"] and row["label"] == "שרת DHCP זר"


# --- #2442 (Fable 05/11): הצפה במקורות מזויפים, והתחזות לשרת ---------------------------


def _rows(conn, event):
    return [r["detail"] for r in conn.execute(
        "SELECT detail FROM journal WHERE event = ? ORDER BY id", (event,))]


def _flood(watch, clock, count, *, start=0):
    for i in range(start, start + count):
        mac = "02:%02x:%02x:%02x:00:01" % (i >> 16 & 255, i >> 8 & 255, i & 255)
        ip = f"10.{i >> 16 & 255}.{i >> 8 & 255}.{i & 255}"
        watch.observe(reply_frame(mac=mac, ip=ip), BROADCAST, clock(),
                      frozenset({CARD_MAC}), frozenset({CARD_IP}))


def test_a_flood_of_forged_sources_is_bounded_in_memory_and_in_the_journal(conn):
    """PoC: 20,000 OFFERs, כל אחד מ-MAC+IP אקראי = 20,001 שורות יומן ורשומות."""
    clock = Clock()
    watch, _ = make(conn, clock)
    _flood(watch, clock, 2000)
    assert len(watch.sources) == rd.MAX_SOURCES
    assert len(rows(conn)) == rd.MAX_SOURCES
    summaries = _rows(conn, rd.EVENT_MORE)
    assert len(summaries) <= 1, summaries


def test_the_flood_stays_red_and_says_how_many_more_were_not_kept(conn):
    """עיקרון 5: התקרה אינה משתיקה את הזיהוי."""
    clock = Clock()
    watch, _ = make(conn, clock)
    _flood(watch, clock, rd.MAX_SOURCES + 500)
    watch.slice_done = clock()
    state, detail = watch.health()
    assert state == "bad", (state, detail)
    assert "500" in detail, detail


def test_the_summary_row_counts_what_was_not_journaled_one_by_one(conn):
    clock = Clock()
    watch, _ = make(conn, clock)
    _flood(watch, clock, rd.MAX_SOURCES + 1)          # הראשון מעבר לתקרה — שורת סיכום מיד
    clock.now += 10
    _flood(watch, clock, 300, start=10_000)
    clock.now += rd.SUMMARY_EVERY
    run_step(watch)                                    # סוף פרוסה: מה שנצבר נכתב
    summaries = _rows(conn, rd.EVENT_MORE)
    counts = [int(re.search(r"count=(\d+)", s)[1]) for s in summaries]
    assert sum(counts) == 301 and len(summaries) == 2, summaries
    assert all(s.endswith("nic=eth1") for s in summaries), summaries


def test_only_forged_sources_beyond_the_cap_go_uncounted_by_name_and_the_list_keeps_the_first(conn):
    """מי שנרשם ראשון אינו נדחק החוצה על ידי ההצפה — תוקף אינו מוחק שרת זר אמיתי מהמסך."""
    clock = Clock()
    watch, _ = make(conn, clock)
    watch.observe(reply_frame(), BROADCAST, clock(), frozenset({CARD_MAC}), frozenset({CARD_IP}))
    _flood(watch, clock, 1000)
    assert (ROGUE_MAC, ROGUE_IP) in watch.sources


def test_the_flood_summary_survives_a_restart(conn):
    clock = Clock()
    watch, _ = make(conn, clock)
    _flood(watch, clock, rd.MAX_SOURCES + 5)
    again, _ = make(conn, Clock())
    again.slice_done = again.clock()
    assert len(again.sources) <= rd.MAX_SOURCES
    state, detail = again.health()
    assert state == "bad" and "ועוד" in detail, detail


def test_the_restart_memory_is_capped_too(conn):
    for i in range(rd.MAX_SOURCES + 20):
        _journal_at(conn, 60, f"mac=02:00:00:00:{i >> 8:02x}:{i & 255:02x} ip=10.9.{i >> 8}.{i & 255} "
                              "kind=offer nic=eth1")
    watch, _ = make(conn, Clock())
    assert len(watch.sources) == rd.MAX_SOURCES


@pytest.mark.parametrize("mac, ip", [(ROGUE_MAC, CARD_IP), (CARD_MAC, ROGUE_IP), (CARD_MAC, CARD_IP)],
                         ids=["server-ip", "server-mac", "both"])
def test_an_offer_that_forges_the_servers_address_is_not_the_server(conn, mac, ip):
    """PoC [evasion-ip]/[evasion-mac]: ה-MAC וה-IP שבמסגרת הם מה שהשולח כתב."""
    watch, _ = make(conn, Clock(), [(reply_frame(mac=mac, ip=ip), BROADCAST)])
    run_step(watch)
    assert len(rows(conn)) == 1, rows(conn)
    assert rows(conn)[0]["detail"] == f"mac={mac} ip={ip} kind=offer nic=eth1 as=server"
    state, detail = watch.health()
    assert state == "bad" and "מתחזה לשרת" in detail, detail


def test_our_own_offer_seen_coming_back_is_still_ours(conn):
    """מה שה-dnsmasq שלנו שלח (PACKET_OUTGOING) וחוזר אלינו — לולאה בסוויץ' — אינו זר."""
    ours = reply_frame(mac=CARD_MAC, ip=CARD_IP)
    watch, _ = make(conn, Clock(), [(ours, OUTGOING), (ours, BROADCAST)])
    run_step(watch)
    assert rows(conn) == [] and watch.health()[0] == "ok"


def test_an_echo_of_our_offer_long_after_it_was_sent_is_not_ours(conn):
    ours = reply_frame(mac=CARD_MAC, ip=CARD_IP)
    watch, _ = make(conn, Clock(), [(ours, OUTGOING), rd.ECHO_SECONDS + 1, (ours, BROADCAST)])
    run_step(watch)
    run_step(watch)
    assert len(rows(conn)) == 1 and rows(conn)[0]["detail"].endswith("as=server")


def test_a_different_offer_under_our_address_is_not_ours_even_right_after_we_sent(conn):
    ours = reply_frame(mac=CARD_MAC, ip=CARD_IP)
    forged = reply_frame(mac=CARD_MAC, ip=CARD_IP, yiaddr="10.66.0.77")
    watch, _ = make(conn, Clock(), [(ours, OUTGOING), (forged, BROADCAST)])
    run_step(watch)
    assert len(rows(conn)) == 1


def test_the_impersonation_line_has_a_hebrew_detail_and_the_flood_line_a_label():
    text = he_detail(rd.EVENT, f"mac={CARD_MAC} ip={CARD_IP} kind=offer nic=eth1 as=server")
    assert "מתחזה לשרת" in text and "as=" not in text and CARD_MAC in text
    proxy = he_detail(rd.EVENT, f"mac={CARD_MAC} ip={CARD_IP} kind=proxy nic=eth1 as=server")
    assert "proxyDHCP" in proxy and "מתחזה לשרת" in proxy and "as=" not in proxy
    assert EVENTS_HE[rd.EVENT_MORE] and severity(rd.EVENT_MORE) == "err"
    more = he_detail(rd.EVENT_MORE, f"count=42 last_mac={ROGUE_MAC} last_ip={ROGUE_IP} nic=eth1")
    assert "42" in more and "count=" not in more and ROGUE_MAC in more
