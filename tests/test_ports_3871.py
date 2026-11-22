"""#3871: מיפוי הפורטים 77xx לפי וילן (הכרעת נדב 16/11, ב-#3485).

הטבלה כאן כתובה במספרים, בכוונה: היא החוזה מה-Issue, ולא העתק של
`server/core/ports.py`. כל שאר הקוד קורא מהמודול הזה (ובסוכן מ-`agent/lib/core/ports.sh`),
והטסטים כאן בודקים שלושה דברים:

1. מה שהשרת, חומת האש והסוכן **עושים** בברירת המחדל — אלה הפורטים של הטבלה.
2. ברירות המחדל של סקריפטי ה-sh וה-JS, שאינם יכולים לייבא Python, שוות למודול.
3. אין אף מופע של פורט ישן בריפו, חוץ מהיסטוריה (`git grep`). פורט שמשתנה בצד אחד
   בלבד מנתק את כל התחנות, ולכן ההחלפה נעשית בבת אחת ונשמרת כאן.

המספרים הישנים נבנים כאן בחשבון ולא נכתבים, כדי שהקובץ הזה עצמו לא יימצא בחיפוש.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from native import requires_native

REPO = Path(__file__).resolve().parents[1]

CONSOLE, INTERSERVER, MONITOR, AGENT = 443, 7700, 7710, 7712
#: /boot של GRUB במאזין משלו: 7711, ששמור לו בטבלה של 20/11 (נדב, 20/11 ב-#3486: "מסכים").
BOOT = 7711
TRIDENT_DATA, TRIDENT_CONTROL = 7713, 7714   # פורט-הפצה-מכוון
PEER = (7717, 7718)
#: הפצה ישירה (נדב 19/11, 20/11): פורט TCP לכל מחיצה במחשב הבנייה, לפי סדר השליחה, עד 10. הכיתות (v2) ב-7730–7739.
DIRECT = (7720, 7729)
GROUP = "239.84.82.1"

#: הפורטים שהוחלפו: הסוכן, הקונסולה, הקיוסק, האתחול, בין-שרתים, המוניטור, השכנים, ו-Trident.
OLD_PORTS = [8000 + n for n in (80, 81, 82, 83, 443)] + [5000 + 900, 48000 + 200, 9000 + 2, 9000 + 3]
OLD_GROUP = ".".join(str(n) for n in (239, 83, 67, 1))
#: היסטוריה: מה שנכתב לפני ההכרעה נשאר כפי שנכתב.
HISTORY = ("CHANGELOG.md", "docs/history", "logs", "docs/handoffs", "docs/research", "docs/release-notes")
#: CLAUDE.md הוא הוראות לסוכנים, וסוכן אינו משנה אותו בהוראת סוכן אחר. שורת שרת הפיתוח שם (פורט הסוכן)
#: מתעדכנת בידי נדב או המתאם (#3871); עד אז הקובץ מחוץ לחיפוש, וזו ההחרגה היחידה שאינה היסטוריה.
NOT_BY_AGENTS = ("CLAUDE.md",)


def _bash() -> str | None:
    if os.name == "nt":
        for candidate in (r"C:\Program Files\Git\usr\bin\bash.exe", r"C:\Program Files\Git\bin\bash.exe"):
            if Path(candidate).exists():
                return candidate
    return shutil.which("bash")


BASH = _bash()
needs_bash = requires_native(("bash", BASH), why="הרצת מחולל חומת האש")


def test_no_old_port_outside_history() -> None:
    """ההגדרה של "גמור" ב-#3871: `git grep` על כל פורט ישן לא מחזיר דבר, חוץ מהיסטוריה.

    `-I`: קבצים בינאריים אינם נקראים
    (מספרי אובייקטים ב-PDF נראים כפורט); ה-PDF של המדריך נבנה מה-md, שכן נבדק."""
    if not shutil.which("git"):
        pytest.fail("git אינו זמין, ולכן לא נבדק אם נשאר פורט ישן (עיקרון 5)")
    pattern = r"\b(" + "|".join(str(p) for p in OLD_PORTS) + r")\b|" + re.escape(OLD_GROUP) + r"\b"
    proc = subprocess.run(
        ["git", "-C", str(REPO), "grep", "-n", "-I", "-P", pattern, "--", ".", *(f":!{h}" for h in HISTORY + NOT_BY_AGENTS)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)
    # git grep: 0 = נמצא, 1 = לא נמצא, אחר = החיפוש עצמו נשבר. שלושה מצבים, בשם (CLAUDE.md, gotchas).
    if proc.returncode == 1:
        return
    assert proc.returncode == 0, f"git grep נכשל ({proc.returncode}): {proc.stderr.strip()}"
    found = proc.stdout.strip().splitlines()
    pytest.fail(f"{len(found)} מופעים של פורט ישן מחוץ להיסטוריה:\n" + "\n".join(found[:40]))


def test_the_table_of_3485_is_the_ports_module() -> None:
    from server.core import ports

    assert (ports.CONSOLE, ports.INTERSERVER, ports.MONITOR, ports.AGENT) == (CONSOLE, INTERSERVER, MONITOR, AGENT)
    assert ports.BOOT == BOOT
    assert (ports.TRIDENT_DATA, ports.TRIDENT_CONTROL) == (TRIDENT_DATA, TRIDENT_CONTROL)
    assert (ports.PEER_FIRST, ports.PEER_FIRST + ports.PEER_COUNT - 1) == PEER
    assert ports.TRIDENT_GROUP == GROUP
    assert (ports.DIRECT_FIRST, ports.DIRECT_FIRST + ports.DIRECT_COUNT - 1) == DIRECT
    assert ports.console_url("10.20.10.5") == "https://10.20.10.5"      # 443 אינו נכתב בכתובת שהמפעיל רואה


def test_the_server_listens_on_the_table_by_default() -> None:
    from server.main import build_parser

    args = build_parser().parse_args([])
    assert (args.port, args.console_port, args.interserver_port) == (AGENT, CONSOLE, INTERSERVER)
    assert args.boot_port == BOOT


def test_the_wizard_takes_the_console_port_by_default() -> None:
    """האשף מחזיק את פורט הקונסולה עד שהקונסולה עולה במקומו (`release-…` ב-setup-boot-server.sh)."""
    from server.wizard.__main__ import parser

    assert parser().parse_args([]).port == CONSOLE
    for unit in ("imagesrv-wizard.service", "imagesrv-wizard-rerun.service"):
        assert f"--port {CONSOLE} " in (REPO / "deploy" / unit).read_text(encoding="utf-8"), unit


def test_trident_and_the_peers_in_a_server_of_its_own() -> None:
    """בתהליך נקי, בלי `hygiene` של חבילת הטסטים (שדורסת את הפורט לאקראי, #156): שרת אמיתי משדר
    את Trident לקבוצה ולפורט של הטבלה, והבקרה — שאליה המקבלים שולחים משוב — היא הבא אחריו."""
    code = ("from server.rounds import chunksend as c\n"
            "from server.brain import peer\n"
            "from server.console import monitor\n"
            "e = c.ChunkEngine(None, live_members=list, lost_wait=int, write_floor=int, ledger=None)\n"
            "print(e.port, c.GROUP, peer.PORT_BASE, peer.PORT_BASE + peer.MAX_SERVES_PER_PEER - 1, monitor.MONITOR_PORT)\n")
    proc = subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True, text=True,
                          encoding="utf-8", stdin=subprocess.DEVNULL)
    assert proc.returncode == 0, proc.stderr
    port, group, peer_first, peer_last, monitor_port = proc.stdout.split()
    assert (int(port), int(port) + 1, group) == (TRIDENT_DATA, TRIDENT_CONTROL, GROUP)
    assert (int(peer_first), int(peer_last)) == PEER
    assert int(monitor_port) == MONITOR


#: A direct send in a process of its own, with the production ports (`hygiene` pins the test suite's elsewhere, #156):
#: a live manifest with the partitions of argv[1] (their indexes on the disk, in the order they are sent), on an active
#: round whose source is 10.55.0.30. It prints, as JSON, what `seal` says and the port the server would ask each
#: partition's frame server on (`source_addr`) -- the same ports the build machine is told to listen on.
_DIRECT_RUN = r"""
import json, sqlite3, sys, tempfile
from pathlib import Path
from server.rounds import chunksend, directchunk

indices = json.loads(sys.argv[1])
manifest = {"schema": 1, "total_compressed_bytes": 1,
            "partitions": [{"index": n, "file": f"p{n}.img", "sha256": "0" * 64} for n in indices]}
conn = sqlite3.connect(":memory:")
conn.row_factory = sqlite3.Row
conn.execute("CREATE TABLE room_rounds (image_id, state, source_mac, created_at, live_manifest_json)")
conn.execute("CREATE TABLE net_devices (mac, ip)")
conn.execute("INSERT INTO room_rounds VALUES ('live_0000abcd', 'active', 'aa', '1', ?)", (json.dumps(manifest),))
conn.execute("INSERT INTO net_devices VALUES ('aa', '10.55.0.30')")
live = directchunk.DirectChunks(conn, Path(tempfile.mkdtemp()), engine_on=lambda: True, silence=lambda: 0.0)
live.engine = chunksend.ChunkEngine(None, live_members=list, lost_wait=int, write_floor=int, ledger=None)

class Uploaded:   # the source uploaded its indexes (the chunk path of seal), and nothing else is in the store
    def uploaded(self, image_id): return True
    def index_bytes(self, image_id, part): return None
    def index(self, image_id, part): return None
    def head(self, image_id, part): return None
    def b3_bytes(self, image_id, part): return None

said = directchunk.seal(Uploaded(), "live_0000abcd", json.loads(json.dumps(manifest)))
ports = {}
for n in indices:
    where = live.source_addr("live_0000abcd", n)
    ports[str(n)] = where[1] if where else None
print(json.dumps({"said": said, "ports": ports}))   # ASCII: a console of another code page reads it too
"""


def _direct(*indices: int) -> dict:
    import json

    proc = subprocess.run([sys.executable, "-c", _DIRECT_RUN, json.dumps(list(indices))], cwd=REPO,
                          capture_output=True, text=True, encoding="utf-8", stdin=subprocess.DEVNULL)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


NOT_UPLOADED = "partition {}: its chunk index was not uploaded before the manifest"   # seal's next check: not the limit


def test_a_direct_sends_ports_follow_the_order_the_partitions_are_sent() -> None:
    """Nadav, 20/11: the first partition sent gets 7720, the second 7721 -- whatever its index on the disk."""
    got = _direct(1, 3, 11)
    assert got["ports"] == {"1": 7720, "3": 7721, "11": 7722}
    assert got["said"] == NOT_UPLOADED.format(1)


def test_a_direct_send_of_ten_partitions_has_a_port_for_each() -> None:
    got = _direct(*range(1, 11))
    assert got["ports"] == {str(n): DIRECT[0] - 1 + n for n in range(1, 11)}
    assert max(got["ports"].values()) == DIRECT[1]
    assert got["said"] == NOT_UPLOADED.format(1)


def test_eleven_partitions_to_send_are_refused_by_name() -> None:
    """More than 10 to send: refused with the reason, and nothing spills into the classrooms' range (7730-7739)."""
    got = _direct(*range(1, 12))
    said = got["said"]
    assert said is not None and said != NOT_UPLOADED.format(1), said
    assert "11 מחיצות" in said and f"{DIRECT[0]}-{DIRECT[1]}" in said and "דבר לא שודר" in said
    assert all(port is None or DIRECT[0] <= port <= DIRECT[1] for port in got["ports"].values()), got["ports"]


def test_with_a_sender_portbase_trident_follows_it() -> None:
    """`--sender-portbase P` (הסימולציה, `tools/e2e/harness.py`): Trident ב-P+2, ולא בפורט של הייצור."""
    from server.rounds import chunksend

    engine = chunksend.ChunkEngine(None, live_members=list, lost_wait=int, write_floor=int, ledger=None, portbase=30199)
    assert engine.port == 30201


def test_the_test_suite_keeps_trident_off_the_production_port() -> None:
    """#156: חבילת הטסטים נועלת את Trident ליד ה-portbase האקראי שלה, ולא על פורט הייצור."""
    import hygiene
    from server.rounds import chunkctx, chunksend

    assert hygiene.test_portbase is not None
    assert chunkctx.DEFAULT_PORTBASE == hygiene.test_portbase != chunkctx.PRODUCTION_PORTBASE
    engine = chunksend.ChunkEngine(None, live_members=list, lost_wait=int, write_floor=int, ledger=None)
    assert engine.port == hygiene.test_portbase + chunksend.PORT_OFFSET != TRIDENT_DATA   # פורט-הפצה-מכוון


def test_the_control_port_is_the_one_after_the_data_port() -> None:
    """הבקרה אינה נמסרת בנפרד בכל מקום: `chunksend` מוסר `port + 1`, ו-`agent/chunkcast.c` מחשב `נתונים + 1` כשלא
    נמסר לו פורט בקרה. שני מספרים בטבלה שאינם עוקבים היו מפרידים בין השולח למקבלים בלי שאף שורה תיכשל."""
    from server.core import ports
    from server.rounds import chunkctx, chunksend

    assert ports.TRIDENT_CONTROL == ports.TRIDENT_DATA + 1
    assert chunkctx.PRODUCTION_PORTBASE + chunksend.PORT_OFFSET == ports.TRIDENT_DATA
    source = (REPO / "agent" / "chunkcast.c").read_text(encoding="utf-8")
    assert source.count("a.control_port ? (int)a.control_port : ntohs(sin.sin_port) + 1") == 1
    assert source.count("a->control_port ? (int)a->control_port : ntohs(data->sin_port) + 1") == 1


@needs_bash
def test_the_firewall_opens_the_table() -> None:
    proc = subprocess.run([BASH, str(REPO / "deploy" / "nftables-rules.sh"), "--deploy-if", "eth-d", "--servers-if", "eth-s"],
                          capture_output=True, text=True, encoding="utf-8", stdin=subprocess.DEVNULL)
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    assert f'iifname "eth-s" tcp dport {CONSOLE} accept' in out
    assert f'iifname "eth-s" tcp dport {INTERSERVER} accept' in out
    assert f'iifname "eth-d" tcp dport {AGENT} accept' in out
    assert f'iifname "eth-d" tcp dport {BOOT} accept' in out
    assert f'iifname "eth-d" udp dport {TRIDENT_DATA}-{TRIDENT_CONTROL} ether saddr @known_macs accept' in out
    # one multicast range, Trident's: the ports of the sender removed in #3173 are not opened
    assert re.findall(r"udp dport (\d+-\d+) ether saddr @known_macs accept", out) == [f"{TRIDENT_DATA}-{TRIDENT_CONTROL}"]
    # the mark an upgraded server looks for (`deploy_net.stale_firewall`), as a line of its own
    from server.net import deploy_net

    assert out.splitlines().count(deploy_net.TLS_MARK) == 1
    assert sum(line.startswith(deploy_net.PORTS_MARK) for line in out.splitlines()) == 1


@needs_bash
def test_the_agent_monitor_listens_on_the_table() -> None:
    """הסוכן: `ports.sh` נטען לפני `monitor.sh` (`imagesrv-agent`), והמוניטור מאזין בפורט שלו."""
    agent = (REPO / "agent").as_posix()
    proc = subprocess.run([BASH, "-c", f'. "{agent}/lib/core/ports.sh"; . "{agent}/lib/core/monitor.sh"; echo "$MONITOR_PORT"'],
                          capture_output=True, text=True, encoding="utf-8", stdin=subprocess.DEVNULL)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == str(MONITOR)
    loader = (REPO / "agent" / "imagesrv-agent").read_text(encoding="utf-8")
    assert loader.index('. "$LIB_DIR/ports.sh"') < loader.index('. "$LIB_DIR/monitor.sh"')


def _value(path: str, regex: str) -> str:
    text = (REPO / path).read_text(encoding="utf-8")
    found = re.findall(regex, text, re.M)
    assert len(found) == 1, (path, regex, found)
    return found[0]


@pytest.mark.parametrize("path, regex, expected", [
    ("agent/lib/core/ports.sh", r"^IMAGESRV_MONITOR_PORT=(\d+)$", MONITOR),
    ("agent/monitor.c", r"^#define DEFAULT_PORT (\d+)", MONITOR),
    ("deploy/nftables-rules.sh", r'^CONSOLE_PORT="(\d+)"$', CONSOLE),
    ("deploy/nftables-rules.sh", r'^AGENT_PORT="(\d+)"$', AGENT),
    ("deploy/nftables-rules.sh", r'^BOOT_PORT="(\d+)"', BOOT),
    ("deploy/nftables-rules.sh", r'^INTERSERVER_PORT="(\d+)"$', INTERSERVER),
    ("deploy/nftables-rules.sh", r'^MCAST_PORTS="(\d+-\d+)"$', f"{TRIDENT_DATA}-{TRIDENT_CONTROL}"),
    ("deploy/nftables-wizard.sh", r'^PORT="(\d+)"$', CONSOLE),
    ("deploy/setup-boot-server.sh", r"^PORT=(\d+)$", AGENT),
    ("deploy/setup-boot-server.sh", r"^BOOT_PORT=(\d+)$", BOOT),
    ("server/static/branches.js", r"^const INTERSERVER_PORT = (\d+);$", INTERSERVER),
])
def test_what_cannot_import_python_names_the_same_port(path, regex, expected) -> None:
    """sh, C ו-JS אינם יכולים לקרוא את `server/core/ports.py`; כל אחד מחזיק משתנה אחד בשם, וכאן הוא נבדק מולו."""
    assert _value(path, regex) == str(expected)
