"""#3162: "שחזור אימג' מהשרת" במשכפל עצמו (lc-pull, 16/10) על מנוע החתיכות, מקצה לקצה.

הטבלה (receiver-parity-1114) כתבה "כן (כנראה)" -- קריאת קוד, לא ראיה. כאן זה רץ:

- **השרת אמיתי** (`python -m server.main`, שער הזהות האמיתי, קובץ חכירות), ו-`chunkcast` אמיתי בשני הצדדים.
- **הפתיחה היא של המשכפל עצמו:** `lc_pull_images`, `lc_pull_ports`, `lc_pull_nonce` ו-`lc_pull_open` של
  `agent/lib/localclone/lcpull.sh` כמו שהם, מול `POST /api/v1/agent/cloner-round` האמיתי. בלי סיסמה: הנוכחות
  ליד המכונה (הכרעת נדב 16/10). המגירות שנבחרו הן 1 ו-3 מתוך שלוש.
- **הסבב שנפתח הוא סבב חדר של מכונה אחת**, והמתג `stream_engine` על `chunk`: הגל חייב לצאת במנוע החתיכות
  (`session.engine == "chunk"` ב-hello, `send_start ... engine=chunk` ביומן), לא ב-udp-sender.
- **הכתיבה** היא `chunk_partition_drawers` האמיתית של הסוכן (`chunkrecv.sh`, `chunkfetch.sh`, `chunkgate.sh`):
  האינדקס מאומת מול המניפסט, כל חתיכה נבדקת ב-XXH64 מול האינדקס **לפני** שהיא נכתבת, מה שאבד נמשך ב-HTTP Range
  (מכונה אחת = אין מעבר מולטיקאסט שני), וה-ledger בשרת נרשם לכל דיסק לפי המספר הסידורי שלו.

מה שמדומה, ורק הוא (כמו test_chunk_e2e_2533.py, שהתשתית שלו כאן): דיסק הוא קובץ (CHUNKCAST_ALLOW_FILE), ולכן
עוזרי החומרה של הסוכן הם stubs; טבלת המחיצות, בדיקת הכתיבה והקריאה החוזרת שעוטפות את הפונקציה במכונה אמיתית אינן
רצות כאן (הקריאה החוזרת נבדקת בקובצי readback_*). הזרם עובר דרך ממסר על loopback (TTL 0), ושם הוא מאבד ומשחית
חבילות לפי זהותן.

שלושה תרחישים: pull מלא של שתי מגירות מתוך שלוש (השלישית לא נכתבת ואין לה ledger); חתיכה שהשחתה בדרך נתפסת לפני
הכתיבה ומושלמת ב-HTTP; והשרת שנעלם באמצע -- המגירות נכשלות **בשם** ("השרת לא ענה"), לא שותקות ולא "הצליחו".

**בקרה שלילית:** ב-PR הזה אין קוד ייצור. המוטציה היא `chunk_wave` ב-`server/app.py` שמחזיר תמיד False (המתג
מתעלם): הגל יוצא ב-udp-sender, ו-`test_lc_pull_is_a_chunk_wave_...` נופל על `session.engine`.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess

import chunkcast_rig as C
from native import requires_native
from test_agent import BASH, posix
from test_chunk_e2e_2533 import AGENT, CLONER_A, LOST, REPO, _sha, stack  # noqa: F401 -- `stack` is the fixture
from test_cloner_pull_agent import run as run_lc_agent
from tools.e2e import harness as H

pytestmark = requires_native("gcc", "zstd", "jq", "curl", ("bash", BASH), posix=True,
                             why="a real server, the real chunkcast and the agent's real shell")

SERIALS = ["LC-A1", "LC-A2", "LC-A3"]       # the cloner's three drawers: sda, sdb, sdc = disk numbers 1, 2, 3
TICKED = "1,3"                              # what the person ticks on the restore screen

#: lcpull.sh as the cloner runs it, up to and including the open: the screen's image list, the ports check,
#: the screen's token, and the open. `list_drawers` and `disk_port` are the only hardware it asks about.
LC_PULL = r'''
IMAGESRV_SERVER="$SERVER"; . agent/lib/core/tls.sh; tls_config || { echo "TLS-CONFIG-FAILED $TLS_WHY"; exit 7; }   # #3580: $SERVER is the certificate name
list_drawers() { echo sda; echo sdb; echo sdc; }
disk_port() { case "$1" in sda) echo 1;; sdb) echo 2;; sdc) echo 3;; *) echo 0;; esac; }
mkdir -p "$LC"; HTTP_TIMEOUT=15
lc_pull_images || { echo IMAGES-FAILED; exit 3; }
grep '^image=' "$LC/pull.images"
_w=$(lc_pull_ports "$PORTS") || { echo PORTS-REFUSED; exit 4; }
lc_pull_nonce || { echo NO-NONCE; exit 5; }
if lc_pull_open "$IMAGE" "$_w"; then echo OPENED; else echo "REFUSED $(cat "$LC/pull.error" 2>/dev/null)"; exit 6; fi
'''

#: the agent of test_chunk_e2e_2533.py with short ceilings: a server that went away is heard in seconds, not minutes
FAST = AGENT.replace("WAIT_STREAM_STALL_S=30; WAIT_DRAWER_S=30; STREAM_START_CEILING=90; CHUNK_ASK_POLL_S=1",
                     "WAIT_STREAM_STALL_S=6; WAIT_DRAWER_S=10; STREAM_START_CEILING=40; CHUNK_ASK_POLL_S=1")
assert FAST != AGENT, "test_chunk_e2e_2533.AGENT changed: the ceilings line is not there any more"


def _stage(stack, tmp_path):
    """A format-2 image in the library with its index derived, the switch on `chunk`, and a cloner with three drawers."""
    image = stack.capture()

    def derived():
        status, manifest = stack.admin.json("GET", f"/api/console/images/{image}")
        assert status == 200, manifest
        parts = {p["index"]: p for p in manifest["partitions"]}
        assert not any(p.get("index_error") for p in parts.values()), parts
        return all(isinstance(parts[n].get("readback_chunks"), int) for n in (1, 3))
    stack._wait(derived, "the server derived the index of both partitions")
    stack.machine(CLONER_A, "01", "grp_CLONERS")
    disks = [H.disk(f"sd{chr(97 + i)}", H.GB256, serial, port=i + 1) for i, serial in enumerate(SERIALS)]
    stack.hello(CLONER_A, disks, engines=["udpcast", "chunk"])
    return image, disks


def _pull_from_the_cloner(stack, tmp_path, image: str) -> str:
    """The cloner's own lcpull.sh opens the round. Returns what it said."""
    (tmp_path / "lc").mkdir()
    out = run_lc_agent(tmp_path / "lc", LC_PULL, MAC=CLONER_A, SERVER=stack.base, IMAGE=image, PORTS=TICKED,
                       **stack.trust_env)
    said = out.stdout + out.stderr
    assert out.returncode == 0 and "OPENED" in out.stdout, f"{said}\nserver:\n{stack.tail()}"
    assert f"image={image}|Chunk E2E|E2E" in out.stdout, f"the cloner's screen did not offer the image\n{said}"
    assert "opened on drawers 1 3" in out.stdout, said
    return said


def _running(stack, disks) -> dict:
    """The cloner's next hello: the round's wave, running."""
    def running():
        answer = stack.hello(CLONER_A, disks, engines=["udpcast", "chunk"])
        return answer if (answer.get("session") or {}).get("state") == "running" else None
    return stack._wait(running, "the wave is running for the cloner")


def _start(stack, tmp_path, rule=None, script=AGENT):
    """Everything up to the agent's run, and the run started. Returns (image, disks, answer, run dir, process)."""
    image, disks = _stage(stack, tmp_path)
    _pull_from_the_cloner(stack, tmp_path, image)
    answer = _running(stack, disks)
    session = answer["session"]
    assert session.get("engine") == "chunk", f"the lc-pull wave is not on the chunk engine: {session}"
    assert session["chunk"]["port"] == stack.portbase + 2, session
    assert session["target_ports"] == [1, 3], session
    manifest = stack.agent.request("GET", f"/api/v1/images/{image}/manifest")[1]
    if rule is not None:
        stack.relay.rule = rule             # before the agent listens: the sender waits for it
    run = tmp_path / "agent"
    (run / "disks").mkdir(parents=True)
    (run / "resp.json").write_text(json.dumps(answer), encoding="utf-8")
    (run / "manifest.json").write_bytes(manifest)
    for d in disks:                         # all three exist as disks; only the ticked two are handed to the agent
        (run / "disks" / f"{d['dev']}.serial").write_text(d["serial"], encoding="ascii")
        stack.esp.blank(run / "disks" / f"{d['dev']}.p1")
        stack.win.blank(run / "disks" / f"{d['dev']}.p3")
    (run / "agent.sh").write_text(script, encoding="utf-8", newline="\n")
    ticked = [d["dev"] for d in disks if d["port"] in (1, 3)]
    with (run / "out").open("wb") as out:
        process = subprocess.Popen(
            [BASH, posix(run / "agent.sh"), posix(run), stack.base, image, CLONER_A, " ".join(ticked)],
            cwd=str(REPO), stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            start_new_session=True, env={**os.environ, "BIN": str(stack.bin), **stack.trust_env})
    stack.agents.append(process)
    return image, disks, answer, run, process


def _wait_agent(stack, run, process, seconds: int = 240) -> tuple[object, list[str], str]:
    try:
        rc = process.wait(seconds)
    except subprocess.TimeoutExpired:
        process.kill()
        rc = "timeout"
    trace = (run / "trace").read_text(encoding="utf-8").splitlines() if (run / "trace").exists() else []
    log = (run / "agent.log").read_text(errors="replace")[-3000:] if (run / "agent.log").exists() else ""
    return rc, trace, log


def _db(stack) -> sqlite3.Connection:
    stack.server.terminate()
    stack.server.wait(15)
    conn = sqlite3.connect(stack.data / "imagesrv.db")
    conn.row_factory = sqlite3.Row
    return conn


def _journal(conn, *events: str) -> list[str]:
    marks = ",".join("?" for _ in events)
    return [f"{r['event']} {r['detail']}" for r in conn.execute(
        f"SELECT event, detail FROM journal WHERE event IN ({marks}) ORDER BY rowid", events)]


def _only_source_or_untouched(target: bytes, expected: bytes) -> None:
    """Every 4KiB block of the target is the source's or what the target held before: nothing else was ever written."""
    untouched = bytes([C.GARBAGE]) * 4096
    assert len(target) == len(expected)
    for at in range(0, len(target), 4096):
        block = target[at:at + 4096]
        assert block == expected[at:at + 4096] or block == untouched[:len(block)], f"block at {at} is neither"


def test_lc_pull_is_a_chunk_wave_on_the_drawers_the_person_ticked(stack, tmp_path):
    """הליבה. המשכפל פותח, השרת משדר במנוע החתיכות, שתי המגירות שסומנו שלמות, והשלישית לא נגעו בה."""
    image, disks, answer, run, process = _start(stack, tmp_path)
    rc, trace, log = _wait_agent(stack, run, process)
    assert rc == 0, f"rc={rc}\ntrace:\n" + "\n".join(trace) + f"\nagent log:\n{log}\nserver:\n{stack.tail()}"
    assert trace[0] == "MODE chunk" and trace[-1] == "OK", trace

    ticked, left = [d for d in disks if d["port"] in (1, 3)], [d for d in disks if d["port"] == 2]
    for d in ticked:
        for img, part in ((stack.esp, 1), (stack.win, 3)):
            assert _sha((run / "disks" / f"{d['dev']}.p{part}").read_bytes()) == _sha(img.expected()), (d["dev"], part)
        assert (run / "targets" / d["dev"] / "ledger" / "durable.3").read_text() == f"0-{stack.win.n - 1}\n"
    for d in left:                          # the unticked drawer was never handed to the agent: byte for byte what it was
        assert (run / "disks" / f"{d['dev']}.p3").read_bytes() == stack.win.expected(set())
        assert (run / "disks" / f"{d['dev']}.p1").read_bytes() == stack.esp.expected(set())

    conn = _db(stack)
    wave = answer["session"]["id"]
    for d in ticked:                        # the server's ledger: one row a partition, for each ticked disk, by serial
        rows = {r["part"]: r for r in conn.execute(
            "SELECT * FROM chunk_ledger WHERE serial = ? AND size_bytes = ?", (d["serial"], d["size_bytes"]))}
        assert set(rows) == {1, 3}, (d["serial"], list(rows))
        assert rows[3]["round_id"] == wave and json.loads(rows[3]["ranges_json"]) == [[0, stack.win.n - 1]]
        assert json.loads(rows[1]["ranges_json"]) == [[0, stack.esp.n - 1]]
    assert conn.execute("SELECT COUNT(*) FROM chunk_ledger WHERE serial = ?", (SERIALS[1],)).fetchone()[0] == 0
    journal = _journal(conn, "room_open_cloner", "chunk_repair", "chunk_disk_failed", "send_failed", "send_start")
    conn.close()
    assert any(j.startswith("room_open_cloner") and "מגירות 1,3" in j for j in journal), journal
    assert any(j.startswith("send_start") and "engine=chunk" in j for j in journal), journal
    assert not [j for j in journal if j.startswith(("send_failed", "chunk_disk_failed"))], journal
    # one machine lacks the chunks: no second multicast pass -- it fetches them by Range, once
    repairs = [j for j in journal if j.startswith("chunk_repair")]
    assert len(repairs) == 1 and "multicast=0 " in repairs[0] and f"'{CLONER_A}': {len(LOST)}" in repairs[0], repairs
    assert stack.passes == {1: 1, 3: 1}
    assert "chunk fetch: 1 ranges: curl rc=0, apply rc=0" in (run / "agent.log").read_text(errors="replace")


def test_a_chunk_damaged_on_the_wire_is_caught_before_it_is_written_and_fetched_whole(stack, tmp_path):
    """חתיכה 1 של מחיצה 3 מגיעה משובשת (בית אחד), וחתיכה 2 אובדת. הראשונה אינה תואמת את האינדקס ולכן אינה
    נכתבת; שתיהן נמשכות ב-HTTP, והדיסקים זהים למקור."""
    def rule(receiver, kind, chunk, seq, packet):
        part = int.from_bytes(packet[6:8], "big")
        if kind == C.ANNOUNCE:
            stack.passes[part] = int.from_bytes(packet[16:20], "big")
        if kind == C.DATA and part == 3 and stack.passes.get(part) == 1 and seq == 0:
            if chunk == 1:
                return [packet[:40] + bytes([packet[40] ^ 0x55]) + packet[41:]]
            if chunk == 2:
                return []
        return [packet]

    _image, disks, _answer, run, process = _start(stack, tmp_path, rule=rule)
    rc, trace, log = _wait_agent(stack, run, process)
    assert rc == 0, f"rc={rc}\ntrace:\n" + "\n".join(trace) + f"\nagent log:\n{log}\nserver:\n{stack.tail()}"
    agent_log = (run / "agent.log").read_text(errors="replace")
    assert "chunk 1 does not match the index" in agent_log, agent_log[-3000:]    # the damage reached the receiver, and was refused
    assert "chunk fetch: 1 ranges: curl rc=0, apply rc=0" in agent_log           # chunks 1-2, one range
    for d in disks:
        if d["port"] not in (1, 3):
            continue
        for img, part in ((stack.esp, 1), (stack.win, 3)):
            assert _sha((run / "disks" / f"{d['dev']}.p{part}").read_bytes()) == _sha(img.expected()), (d["dev"], part)
    conn = _db(stack)
    journal = _journal(conn, "chunk_repair", "chunk_disk_failed", "send_failed")
    conn.close()
    assert not [j for j in journal if not j.startswith("chunk_repair")], journal
    assert len(journal) == 1 and "multicast=0 " in journal[0] and f"'{CLONER_A}': 2" in journal[0], journal


def test_the_server_going_away_mid_pull_fails_the_drawers_by_name_and_writes_nothing_wrong(stack, tmp_path):
    """הרשת (השרת) נעלמת אחרי החתיכה הראשונה של מחיצה 3: אף מגירה אינה "מצליחה" ואינה שותקת. כל מגירה שסומנה
    נכשלת בשמה, מחיצה 1 שהסתיימה לפני הניתוק נשארת שלמה, ובמחיצה 3 כל בלוק הוא של המקור או מה שהיה שם."""
    cut = {"done": False}

    def rule(receiver, kind, chunk, seq, packet):
        if kind == C.DATA and int.from_bytes(packet[6:8], "big") == 3 and chunk >= 1:
            if not cut["done"]:
                cut["done"] = True
                stack.server.kill()          # SIGKILL: no goodbye, the link is simply gone
            return []
        return [packet]

    _image, disks, _answer, run, process = _start(stack, tmp_path, rule=rule, script=FAST)
    rc, trace, log = _wait_agent(stack, run, process, seconds=180)
    assert cut["done"], "the server was never cut: the stream did not reach chunk 1 of partition 3"
    assert rc == 4, f"rc={rc} (4 = the partition failed)\ntrace:\n" + "\n".join(trace) + f"\nagent log:\n{log}"
    assert trace[0] == "MODE chunk" and "OK" not in trace and "PARTITION 3 failed" in trace, trace
    ticked = sorted(d["dev"] for d in disks if d["port"] in (1, 3))
    fails = [line for line in trace if line.startswith("FAIL ")]
    assert sorted(line.split()[1] for line in fails) == ticked and all("השרת לא ענה" in line for line in fails), fails
    assert not [line for line in trace if line.startswith("DONE") and line.endswith("p3")], trace
    for dev in ticked:
        assert (run / "targets" / dev / "state").read_text().strip() == "failed"
        assert _sha((run / "disks" / f"{dev}.p1").read_bytes()) == _sha(stack.esp.expected()), dev
        _only_source_or_untouched((run / "disks" / f"{dev}.p3").read_bytes(), stack.win.expected())
    left = next(d for d in disks if d["port"] == 2)
    assert (run / "disks" / f"{left['dev']}.p3").read_bytes() == stack.win.expected(set())
