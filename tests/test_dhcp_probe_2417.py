"""‏#2417: השומר של "DHCP קיים" (#53) רואה את ה-OFFER גם תחת חומת האש של השרת.

אומת על Debian 13 במעבדה (04/11): ה-OFFER משודר ל-255.255.255.255:68, ו-conntrack
אינו משייך אותו ל-DISCOVER שיצא. ‏`policy drop` של `deploy/nftables-rules.sh`
זרק אותו לפני סוקט ה-UDP, והבדיקה החזירה ‏`checked=True, servers=()` — "בדקנו,
אין שרת אחר" — בזמן ששרת ענה פעמיים.

כאן אין רשת ואין nft. הסוקטים מזויפים בגבול המערכת: סוקט ה-UDP אינו מקבל
דבר (זה מה שחומת האש עושה לו), וההאזנה ברמת הקישור (AF_PACKET) רואה מה שיצא
ונכנס בכרטיס. מה שנבדק הוא ההחלטה: מתי מותר לומר "נבדק".
"""

from __future__ import annotations

import errno
import socket
import struct
import types

import pytest

from server.net import dhcp_host

OFFER_FROM = "10.66.0.2"
CARD_MAC = bytes.fromhex("020000000001")
OUTGOING, BROADCAST = 4, 1


def _frame(bootp: bytes, src: str, sport: int, dport: int, ihl_words: int = 5) -> bytes:
    """מסגרת Ethernet/IPv4/UDP סביב גוף BOOTP."""
    options = b"\x00" * ((ihl_words - 5) * 4)
    udp = struct.pack("!HHHH", sport, dport, 8 + len(bootp), 0) + bootp
    ip = (bytes([0x40 | ihl_words, 0]) + struct.pack("!H", 4 * ihl_words + len(udp))
          + b"\x00\x00\x00\x00" + bytes([64, 17]) + b"\x00\x00"
          + socket.inet_aton(src) + socket.inet_aton("255.255.255.255") + options)
    return b"\xff" * 6 + b"\x02\x00\x00\x00\x00\x01" + b"\x08\x00" + ip + udp


def _offer(xid: bytes) -> bytes:
    return (b"\x02\x01\x06\x00" + xid + b"\x00" * 228 + b"\x63\x82\x53\x63"
            + b"\x35\x01\x02\xff")


class FakeNet:
    """כרטיס אחד. ‏`offers`: כמה שרתים עונים; ‏`echo`: האם ה-DISCOVER שלנו
    נראה יוצא בהאזנה (אין קישור/כרטיס אחר = לא); ‏`drops`: מה שהקרנל השליך.

    החוט (`wire`): מה שבאמת יצא. סוקט UDP מקבל מהקרנל כתובת מקור לפי הניתוב
    (‏`udp_source`; על כרטיס בלי כתובת — כתובת של כרטיס **אחר**, כפי שנמדד
    במעבדה 04/11). ‏`strict`: השרת לא עונה ל-DISCOVER שאינו מ-0.0.0.0, כמו
    שרת עם rp_filter=2 (ברירת המחדל בדביאן) או DHCP snooping. ‏`rewrite_source`:
    משהו בדרך שינה את המקור של מסגרת שנשלחה כמו שהיא."""

    def __init__(self, *, offers=(OFFER_FROM,), echo=True, drops=0,
                 tap_error=None, foreign_xid=False, udp_source="10.66.0.1",
                 strict=False, rewrite_source=None):
        self.offers, self.echo, self.drops = offers, echo, drops
        self.tap_error, self.foreign_xid = tap_error, foreign_xid
        self.udp_source, self.strict = udp_source, strict
        self.rewrite_source = rewrite_source
        self.queue: list[tuple[bytes, tuple]] = []
        self.made: list[FakeSocket] = []
        self.wire: list[bytes] = []

    def module(self):
        """מודול `socket` שבו רק `socket.socket` מזויף — לקוד הישן ולחדש כאחד."""
        fake = types.SimpleNamespace(**{k: getattr(socket, k) for k in dir(socket)
                                        if not k.startswith("__")})
        fake.socket = self.factory
        return fake

    def factory(self, family, kind, proto=0):
        sock = FakeSocket(self, family)
        self.made.append(sock)
        if family != socket.AF_INET and self.tap_error is not None:
            raise self.tap_error
        return sock

    def sent(self, data: bytes, raw: bool) -> None:
        frame = data if raw else _frame(data, self.udp_source, 68, 67)
        if raw and self.rewrite_source:
            frame = frame[:26] + socket.inet_aton(self.rewrite_source) + frame[30:]
        self.wire.append(frame)
        xid = frame[14 + 20 + 8 + 4:14 + 20 + 8 + 8]
        if self.echo:
            self.queue.append((frame, ("eth1", 3, OUTGOING, 1, b"")))
        if self.strict and frame[26:30] != bytes(4):
            return                          # השרת לא עונה למקור שאינו בר-מענה
        answer = b"\x00\x00\x00\x00" if self.foreign_xid else xid
        for server in self.offers:
            self.queue.append((_frame(_offer(answer), server, 67, 68),
                               ("eth1", 0x800, BROADCAST, 1, b"")))


class FakeSocket:
    def __init__(self, net: FakeNet, family):
        self.net, self.family, self.closed = net, family, False
        self.options: list[tuple] = []
        self.bound = None

    def setsockopt(self, *args):
        self.options.append(args)

    def bind(self, address):
        self.bound = address

    def settimeout(self, value):
        pass

    def sendto(self, packet, address):
        self.net.sent(packet, raw=self.family != socket.AF_INET)
        return len(packet)

    def getsockname(self):
        return ("eth1", 0, 0, 1, CARD_MAC)

    def recvfrom(self, size):
        if self.family == socket.AF_INET:
            raise socket.timeout()          # חומת האש של השרת: שום OFFER לא מגיע לכאן
        if not self.net.queue:
            raise socket.timeout()
        return self.net.queue.pop(0)

    def getsockopt(self, level, name, size):
        return struct.pack("II", 3, self.net.drops)

    def close(self):
        self.closed = True


def _probe(monkeypatch, net: FakeNet):
    monkeypatch.setattr(dhcp_host, "socket", net.module())
    return dhcp_host.probe_existing_dhcp("eth1", timeout=0.5)


def test_an_offer_the_firewall_drops_before_the_udp_socket_is_still_seen(monkeypatch):
    """הבאג עצמו: סוקט ה-UDP אינו מקבל כלום, ובכרטיס עבר OFFER מ-10.66.0.2."""
    result = _probe(monkeypatch, FakeNet())
    assert result.servers == (OFFER_FROM,), result
    assert result.checked is True


def test_two_servers_are_both_named(monkeypatch):
    result = _probe(monkeypatch, FakeNet(offers=("10.66.0.2", "10.66.0.3")))
    assert result.checked is True and result.servers == ("10.66.0.2", "10.66.0.3")


def test_silence_without_seeing_our_own_discover_is_not_clean(monkeypatch):
    """אין ראיה שהבקשה יצאה ושההאזנה שומעת את הכרטיס (אין קישור, כרטיס
    אחר) — "לא הצלחנו לבדוק", לא "נקי" (עיקרון 5)."""
    result = _probe(monkeypatch, FakeNet(offers=(), echo=False))
    assert result.checked is False, result
    assert result.servers == () and "DHCPDISCOVER" in result.reason


def test_a_link_layer_listener_that_cannot_open_is_not_clean(monkeypatch):
    """בלי root אין AF_PACKET. השליחה לבדה אינה בדיקה."""
    result = _probe(monkeypatch, FakeNet(offers=(), tap_error=PermissionError(1, "denied")))
    assert result.checked is False, result
    assert result.reason == "permission", result


@pytest.mark.parametrize("operation,error,reason", [
    ("sendto", errno.ENETUNREACH, "link_down"),
    ("sendto", errno.ENETDOWN, "link_down"),
    ("bind", errno.EADDRINUSE, "port_busy"),
    ("bind", errno.EACCES, "permission"),
    ("sendto", errno.EPERM, "permission"),
    ("sendto", errno.EACCES, "permission"),
    ("bind", errno.ENODEV, "os_error"),
    ("sendto", errno.EIO, "os_error"),
])
def test_probe_socket_failure_reason_2394(monkeypatch, operation, error, reason):
    def fail(*args):
        raise OSError(error, "injected socket failure")

    monkeypatch.setattr(FakeSocket, operation, fail)
    net = FakeNet(offers=())
    result = _probe(monkeypatch, net)
    assert result.checked is False
    assert result.servers == ()
    assert result.reason == reason, result
    assert net.wire == []
    assert net.made and all(s.closed for s in net.made)


def test_probe_sender_bind_busy_reason_2394(monkeypatch):
    original_bind = FakeSocket.bind

    def bind(sock, address):
        if len(sock.net.made) == 2:
            raise OSError(errno.EADDRINUSE, "injected sender bind failure")
        original_bind(sock, address)

    monkeypatch.setattr(FakeSocket, "bind", bind)
    net = FakeNet(offers=())
    result = _probe(monkeypatch, net)
    assert result.checked is False
    assert result.reason == "port_busy", result
    assert all(s.closed for s in net.made)


def test_silence_while_the_kernel_dropped_frames_is_not_clean(monkeypatch):
    """מסגרת שהקרנל השליך מהחוצץ יכלה להיות ה-OFFER."""
    result = _probe(monkeypatch, FakeNet(offers=(), drops=2))
    assert result.checked is False, result
    assert "2" in result.reason


def test_heard_and_asked_and_nobody_answered_is_clean(monkeypatch):
    """הצד החיובי — עובר גם בלי התיקון: בדיקה שראתה את עצמה ואיש לא ענה."""
    result = _probe(monkeypatch, FakeNet(offers=()))
    assert result.checked is True and result.servers == ()


def test_an_answer_to_someone_elses_discover_is_not_ours(monkeypatch):
    result = _probe(monkeypatch, FakeNet(foreign_xid=True))
    assert result.checked is True and result.servers == ()


def test_a_card_without_an_address_still_gets_an_answer(monkeypatch):
    """הפער מהמעבדה (04/11): על כרטיס בלי IPv4 סוקט ה-UDP יצא עם כתובת של כרטיס
    אחר (192.168.68.61), השרת (rp_filter=2) לא ענה, והתוצאה הייתה
    `checked=True, servers=()`. DISCOVER ממקור 0.0.0.0 נענה."""
    net = FakeNet(udp_source="192.168.68.61", strict=True)
    result = _probe(monkeypatch, net)
    assert result.servers == (OFFER_FROM,), (
        result, [socket.inet_ntoa(f[26:30]) for f in net.wire])


def test_the_discover_on_the_wire_is_a_proper_dhcp_client_frame(monkeypatch):
    """RFC 2131 4.1: מ-0.0.0.0:68 אל 255.255.255.255:67, ל-ff:ff:ff:ff:ff:ff,
    ממקור ה-MAC של הכרטיס — ו-chaddr הוא אותו MAC, כדי שהתשובה תחזור אלינו."""
    net = FakeNet(offers=())
    _probe(monkeypatch, net)
    assert len(net.wire) == 1
    frame = net.wire[0]
    ip, udp = frame[14:34], frame[34:42]
    assert socket.inet_ntoa(ip[12:16]) == "0.0.0.0"
    assert socket.inet_ntoa(ip[16:20]) == "255.255.255.255"
    assert frame[:6] == bytes.fromhex("ffffffffffff")
    assert frame[6:12] == CARD_MAC and frame[12:14] == bytes.fromhex("0800")
    assert struct.unpack("!HH", udp[:4]) == (68, 67)
    assert struct.unpack("!H", ip[2:4])[0] == len(frame) - 14
    assert struct.unpack("!H", udp[4:6])[0] == len(frame) - 34
    words = sum(struct.unpack("!10H", ip))
    while words > 0xFFFF:
        words = (words & 0xFFFF) + (words >> 16)
    assert words == 0xFFFF                                   # IP header checksum
    assert frame[42 + 28:42 + 34] == CARD_MAC                # chaddr


def test_a_discover_seen_leaving_from_another_source_is_not_evidence(monkeypatch):
    """הראיה היא DISCOVER שלנו **ממקור 0.0.0.0** שיצא. מקור אחר — שרת שמסנן
    מקור לא היה עונה, ושקט אינו "נקי"."""
    net = FakeNet(rewrite_source="192.168.68.61", strict=True)
    result = _probe(monkeypatch, net)
    assert result.checked is False, result
    assert "0.0.0.0" in result.reason and "192.168.68.61" in result.reason


def test_every_socket_is_closed(monkeypatch):
    net = FakeNet()
    _probe(monkeypatch, net)
    assert net.made and all(s.closed for s in net.made)


def test_the_listener_is_bound_to_that_card_with_the_filter_attached(monkeypatch):
    net = FakeNet()
    _probe(monkeypatch, net)
    tap = next(s for s in net.made if s.family == dhcp_host.AF_PACKET)
    assert tap.bound == ("eth1", dhcp_host.ETH_P_ALL)
    assert any(o[:2] == (socket.SOL_SOCKET, dhcp_host.SO_ATTACH_FILTER) for o in tap.options)


# --- מסנן ה-BPF, מורץ כאן ולא רק נכתב ----------------------------------------
#
# מסנן שגוי היה זורק גם את ה-DISCOVER שלנו, והבדיקה הייתה נסגרת "לא נבדק"
# לתמיד — בטוח, אבל השומר מת. מפרש קטן לשבע הפקודות שהמסנן משתמש בהן.


def _run_bpf(program, packet: bytes) -> int:
    a = x = pc = 0
    while True:
        code, jt, jf, k = program[pc]
        pc += 1
        if code in (0x28, 0x30, 0x48):                      # ld h/b abs, ld h ind
            off = k + (x if code == 0x48 else 0)
            width = 1 if code == 0x30 else 2
            if off + width > len(packet):
                return 0
            a = int.from_bytes(packet[off:off + width], "big")
        elif code == 0xB1:                                  # ldx 4*([k]&0xf)
            if k >= len(packet):
                return 0
            x = (packet[k] & 0x0F) * 4
        elif code == 0x15:
            pc += jt if a == k else jf
        elif code == 0x45:
            pc += jt if a & k else jf
        elif code == 0x06:
            return k
        else:                                               # pragma: no cover
            raise AssertionError(f"unknown BPF op {code:#x}")


XID = b"\x11\x22\x33\x44"


@pytest.mark.parametrize("packet,accepted", [
    (_frame(_offer(XID), OFFER_FROM, 67, 68), True),                 # OFFER
    (_frame(_offer(XID), OFFER_FROM, 67, 68, ihl_words=6), True),    # עם IP options
    (_frame(b"\x01" + b"\x00" * 243, "0.0.0.0", 68, 67), True),       # ה-DISCOVER שלנו
    (_frame(_offer(XID), OFFER_FROM, 39000, 39001), False),           # מולטיקאסט של סבב
    (_frame(_offer(XID), OFFER_FROM, 53, 68), False),
    (b"\xff" * 12 + b"\x08\x06" + b"\x00" * 28, False),               # ARP
    (b"\x00" * 10, False),                                            # קצרה מדי
])
def test_the_kernel_filter_keeps_dhcp_and_nothing_else(packet, accepted):
    assert bool(_run_bpf(dhcp_host._BPF_DHCP, packet)) is accepted


def test_the_filter_keeps_our_own_discover_frame():
    assert _run_bpf(dhcp_host._BPF_DHCP, dhcp_host._discover_frame(CARD_MAC, XID))


def test_the_filter_drops_ip_fragments():
    frame = bytearray(_frame(_offer(XID), OFFER_FROM, 67, 68))
    frame[14 + 6:14 + 8] = struct.pack("!H", 0x0001)                  # fragment offset 1
    assert _run_bpf(dhcp_host._BPF_DHCP, bytes(frame)) == 0


def test_the_frame_parser_reads_options_and_the_source():
    assert dhcp_host._dhcp_frame(_frame(_offer(XID), OFFER_FROM, 67, 68, 6), XID) == (
        "reply", OFFER_FROM)
    assert dhcp_host._dhcp_frame(_frame(_offer(XID), OFFER_FROM, 67, 68)[:40], XID) is None
