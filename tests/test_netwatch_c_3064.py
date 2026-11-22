"""#3064 (N4, N6, N8): `agent/netwatch.c` -- מה שהפורט של המשכפל שומע, ל-JSON.

מקמפל את `agent/netwatch.c` ומריץ `--from-file` (מסגרות מוכנות, בלי סוקט): ה-parser והסיווג, שהם מה שרואה בייטים
מהרשת. מדולג בווינדוס (`requires_native`, gcc + POSIX). הסוקט האמיתי (AF_PACKET + BPF) דורש הרשאה, ונבדק על
ה-testrunner ב-sudo, לא כאן.

מה נבדק:
- שאילתות IGMP: כללית מול של קבוצה, v2 מול v3, מקורות, ספירה; דוחות ו-leave אינם שאילתות.
- הסיווג של כל מחלקה, **ואותו סיווג כמו `server/brain/l2frames.py`** (שני המימושים על אותן מסגרות).
- מסגרות קצרות ופגומות אינן קורסות ואינן נספרות כשאילתה.
- הצפת קבוצות: מעבר ל-16 קבוצות נספר ב-``groups_more``, לא נעלם.
- קלט שגוי: ממשק לא קיים וחלון לא חוקי = ``{"error"}`` וקוד 3, לא JSON שנראה כמו האזנה.
"""

from __future__ import annotations

import json
import shutil
import socket
import struct
import subprocess
from pathlib import Path

import pytest

from native import requires_native
from server.brain import l2frames

REPO = Path(__file__).resolve().parent.parent
SOURCE = REPO / "agent" / "netwatch.c"

needs_cc = requires_native(("cc", shutil.which("gcc") or shutil.which("cc")), posix=True,
                           why="netwatch נבנה על המעבדה בלבד")

BCAST = "ff:ff:ff:ff:ff:ff"
ME_NOT = "00:11:22:33:44:55"


def _mac(text: str) -> bytes:
    return bytes.fromhex(text.replace(":", ""))


def eth(dst: str, src: str, ethertype: int, payload: bytes = b"") -> bytes:
    return _mac(dst) + _mac(src) + struct.pack("!H", ethertype) + payload


def ipv4(proto: int, dst: str, payload: bytes = b"", src: str = "10.0.0.9") -> bytes:
    header = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(payload), 0, 0, 1, proto, 0,
                         socket.inet_aton(src), socket.inet_aton(dst))
    return header + payload


def udp(sport: int, dport: int, body: bytes = b"\0" * 8) -> bytes:
    return struct.pack("!HHHH", sport, dport, 8 + len(body), 0) + body


def igmp(kind: int, group: str = "0.0.0.0", *, v3: bool = False, resp: int = 100) -> bytes:
    body = struct.pack("!BBH4s", kind, resp, 0, socket.inet_aton(group))
    return body + (b"\x02\x7d\x00\x00" if v3 else b"")


def igmp_frame(kind: int, group: str = "0.0.0.0", *, src: str = "10.0.0.1", v3: bool = False,
               resp: int = 100, dst_ip: str = "224.0.0.1") -> bytes:
    return eth("01:00:5e:00:00:01", ME_NOT, 0x0800, ipv4(2, dst_ip, igmp(kind, group, v3=v3, resp=resp), src=src))


def mcast_frame(group: str, src_mac: str = ME_NOT) -> bytes:
    a, b, c = (int(x) for x in group.split(".")[1:])
    return eth(f"01:00:5e:{a & 0x7f:02x}:{b:02x}:{c:02x}", src_mac, 0x0800, ipv4(17, group, udp(1000, 2000)))


def frames_file(path: Path, frames: list[bytes]) -> Path:
    path.write_bytes(b"".join(struct.pack("!I", len(f)) + f for f in frames))
    return path


@pytest.fixture(scope="module")
def binary(tmp_path_factory):
    out = tmp_path_factory.mktemp("build") / "imagesrv-netwatch"
    build = subprocess.run(["gcc", "-O2", "-Wall", "-Wextra", "-Werror", "-o", str(out), str(SOURCE)],
                           capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
    assert build.returncode == 0, build.stderr
    return out


def run_args(binary: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([str(binary), *args], capture_output=True, text=True, timeout=30,
                          stdin=subprocess.DEVNULL)


def listen(binary: Path, tmp_path: Path, frames: list[bytes]) -> dict:
    done = run_args(binary, "--from-file", str(frames_file(tmp_path / "frames.bin", frames)))
    assert done.returncode == 0, done.stdout + done.stderr
    return json.loads(done.stdout)


@needs_cc
def test_a_general_v2_query_is_counted_with_its_source(binary, tmp_path):
    out = listen(binary, tmp_path, [igmp_frame(0x11, src="10.0.0.1")])
    assert out["igmp"]["queries"] == 1 and out["igmp"]["general"] == 1 and out["igmp"]["group"] == 0
    assert out["igmp"]["sources"] == ["10.0.0.1"] and out["igmp"]["versions"] == [2]


@needs_cc
def test_group_specific_and_v3_queries_are_told_apart(binary, tmp_path):
    out = listen(binary, tmp_path, [igmp_frame(0x11, "239.84.82.1", dst_ip="239.84.82.1"),
                                    igmp_frame(0x11, src="10.0.0.2", v3=True)])
    assert (out["igmp"]["queries"], out["igmp"]["general"], out["igmp"]["group"]) == (2, 1, 1)
    assert out["igmp"]["versions"] == [2, 3]
    assert sorted(out["igmp"]["sources"]) == ["10.0.0.1", "10.0.0.2"]


@needs_cc
def test_reports_and_leaves_are_not_queries(binary, tmp_path):
    """דוח או leave של משכפל אחר אינו ראיה לכך שיש querier -- רק שאילתה היא."""
    out = listen(binary, tmp_path, [igmp_frame(0x16, "239.84.82.1", dst_ip="239.84.82.1"),
                                    igmp_frame(0x22, "239.84.82.1", dst_ip="224.0.0.22"),
                                    igmp_frame(0x17, "239.84.82.1", dst_ip="224.0.0.2")])
    assert out["igmp"]["queries"] == 0 and out["igmp"]["sources"] == []
    assert (out["igmp"]["reports"], out["igmp"]["leaves"]) == (2, 1)
    assert out["igmp"]["last_query_age_s"] is None


@needs_cc
def test_a_query_with_a_bad_header_is_not_counted(binary, tmp_path):
    """IGMP קצר מדי, או IP עם ihl שגוי: לא שאילתה, ולא קריסה."""
    short = eth("01:00:5e:00:00:01", ME_NOT, 0x0800, ipv4(2, "224.0.0.1", bytes([0x11, 100, 0, 0])))
    bad_ihl = bytearray(igmp_frame(0x11))
    bad_ihl[14] = 0x41
    out = listen(binary, tmp_path, [short, bytes(bad_ihl), igmp_frame(0x11)[:20]])
    assert out["igmp"]["queries"] == 0


FRAMES = {
    "arp": eth(BCAST, ME_NOT, 0x0806, b"\0" * 28),
    "dhcp": eth(BCAST, ME_NOT, 0x0800, ipv4(17, "255.255.255.255", udp(68, 67), src="0.0.0.0")),
    "wol": eth(BCAST, ME_NOT, 0x0800, ipv4(17, "255.255.255.255", udp(40000, 9, b"\xff" * 102))),
    "igmp": igmp_frame(0x16, "239.84.82.1", dst_ip="239.84.82.1"),
    "lldp": eth("01:80:c2:00:00:0e", ME_NOT, 0x88CC, b"\0" * 20),
    "l2ctl": eth("01:80:c2:00:00:00", ME_NOT, 38, b"\x42\x42\x03" + b"\0" * 20),     # STP: 802.3 length, LLC
    "bcast_ip": eth(BCAST, ME_NOT, 0x0800, ipv4(17, "10.0.0.255", udp(137, 137))),          # NetBIOS
    "mcast_ip": mcast_frame("239.84.82.1"),
    "ipv6": eth("33:33:00:00:00:01", ME_NOT, 0x86DD, b"\0" * 40),
    "unicast_other": eth("aa:bb:cc:dd:ee:01", ME_NOT, 0x0800, ipv4(6, "10.0.0.50", b"\0" * 20)),
    "other": eth(BCAST, ME_NOT, 0x8035, b"\0" * 20),                                         # RARP
}


@needs_cc
@pytest.mark.parametrize("name", sorted(FRAMES))
def test_every_class_is_counted_as_the_server_counts_it(binary, tmp_path, name):
    """הסיווג בשני המימושים (C כאן, פייתון ב-`l2frames`) על אותה מסגרת: מחלקה אחת, ומחלקה אחת בלבד."""
    out = listen(binary, tmp_path, [FRAMES[name]])
    assert l2frames.classify(FRAMES[name])[0] == name
    assert out["classes"][name] == 1 and sum(out["classes"].values()) == 1, out["classes"]


@needs_cc
def test_a_vlan_tag_is_skipped_and_garbage_is_other(binary, tmp_path):
    tagged = eth(BCAST, ME_NOT, 0x8100, struct.pack("!HH", 5, 0x0806) + b"\0" * 28)
    runt = b"\x01\x02\x03"
    out = listen(binary, tmp_path, [tagged, runt, eth(BCAST, ME_NOT, 0x0800, b"\x65" + b"\0" * 30)])
    assert l2frames.classify(tagged)[0] == "arp" and l2frames.classify(runt)[0] == "other"
    assert out["classes"]["arp"] == 1 and out["classes"]["other"] == 2


@needs_cc
def test_groups_are_listed_by_count_and_overflow_is_counted_not_dropped(binary, tmp_path):
    frames = [mcast_frame("239.84.82.1")] * 5 + [mcast_frame(f"239.1.1.{n}") for n in range(2, 22)]
    out = listen(binary, tmp_path, frames)
    assert out["groups"][0] == {"group": "239.84.82.1", "n": 5}
    assert len(out["groups"]) == 8
    # 21 קבוצות: 16 נשמרו, 5 נוספות נספרו, ו-8 מוצגות -- כולן מחושבות, אף אחת לא נעלמה
    assert out["groups_more"] == 21 - 8
    assert out["classes"]["mcast_ip"] == 25


@needs_cc
def test_the_chattiest_sources_are_listed(binary, tmp_path):
    frames = [FRAMES["bcast_ip"]] * 3 + [eth(BCAST, "aa:aa:aa:aa:aa:aa", 0x0806, b"\0" * 28)]
    out = listen(binary, tmp_path, frames)
    assert out["macs"][0] == {"mac": ME_NOT, "n": 3} and out["macs"][1]["mac"] == "aa:aa:aa:aa:aa:aa"


@needs_cc
def test_an_empty_capture_says_zero_and_not_unread(binary, tmp_path):
    """אפס מסגרות מקובץ שנקרא הוא אפס; הקובץ שלא נקרא הוא שגיאה (עיקרון 5)."""
    out = listen(binary, tmp_path, [])
    assert out["classes"]["other"] == 0 and out["sampled"] == 0 and out["igmp"]["queries"] == 0
    missing = run_args(binary, "--from-file", str(tmp_path / "nope.bin"))
    assert missing.returncode == 3 and "error" in json.loads(missing.stdout)


@needs_cc
def test_a_truncated_record_stops_the_read_without_a_crash(binary, tmp_path):
    path = tmp_path / "cut.bin"
    path.write_bytes(struct.pack("!I", len(FRAMES["arp"])) + FRAMES["arp"] + struct.pack("!I", 500) + b"\0" * 10)
    done = run_args(binary, "--from-file", str(path))
    assert done.returncode == 0 and json.loads(done.stdout)["classes"]["arp"] == 1


@needs_cc
@pytest.mark.parametrize("args", [("no-such-nic0", "5"), ("lo", "0"), ("lo", "9999"), ("e/th0", "5"), ("lo", "abc")])
def test_a_bad_request_is_an_error_with_rc_3_never_a_listening_result(binary, args):
    done = run_args(binary, *args)
    assert done.returncode == 3, done.stdout
    assert set(json.loads(done.stdout)) == {"error"}


@needs_cc
def test_usage_is_rc_2(binary):
    assert run_args(binary).returncode == 2
    assert run_args(binary, "lo", "5", "--noise").returncode == 2
