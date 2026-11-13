"""#1448 — sgdisk מיישר תחילת מחיצה ל-2048 אלא אם נאמר לו `-a 1`.

על ברזל (16/10): מקור שמחיצה 2 שלו מתחילה ב-10185210 נכתב ליעד
ב-10184704 (2048×4973) — verify_table תפס, והיעד נכשל. כל כניסה שנוצרת
בתחילה מפורשת חייבת לשבת **בדיוק** שם: apply_gpt ובנייה מחדש בהרחבה.
`_move_to_tail` (#1467): התחלה מיושרת וגודל מדויק — נבדק ב-test_agent.

ו-#1449 (מחקר זמני העלייה): ה-LLDP ברקע ירש את הצינור של `$( )`
ב-build_hello, וה-hello הראשון חיכה לו עד 35 שניות.
"""

from __future__ import annotations

import time

from native import requires_native
from test_agent import BASH, fake_machine, posix, sh  # noqa: F401
from test_netprobe import _stub, netprobe_of
from test_restore_evidence import build_box, rc_of, restore_run

pytestmark = requires_native(("bash", BASH))


def _explicit_creates(calls: list[str]) -> list[str]:
    # `-n i:-N:0` (התחילה נגזרת מהסוף) הוא של _move_to_tail — מיושר בכוונה.
    return [c for c in calls if " -n " in f" {c}" and ":-" not in c.split("-n ", 1)[1].split()[0]]


def test_apply_gpt_keeps_every_start_exactly(tmp_path):
    box, run, prelude = build_box(tmp_path)
    out = sh(restore_run(prelude))
    assert rc_of(out) == "rc=0", out
    creates = _explicit_creates((box / "sgdisk.calls").read_text().splitlines())
    assert creates, "apply_gpt did not create any partition"
    for c in creates:  # ההדמיה (#1434) והכתיבה — שתיהן בדיוק
        assert c.startswith("-a 1 "), c


def test_the_expansion_rebuild_keeps_its_start_exactly(tmp_path):
    box, run, prelude = build_box(tmp_path)
    (box / "sys" / "block" / "sda").mkdir(parents=True)
    (box / "sys" / "block" / "sda" / "size").write_text("976773168\n")
    out = sh(prelude + 'apply_gpt sda m.json || { echo "rc=apply"; exit; }; '
             + 'expand_last sda m.json; echo "rc=$?"')
    assert rc_of(out) == "rc=0", out
    calls = (box / "sgdisk.calls").read_text().splitlines()
    rebuilt = [c for c in calls if " -d " in f" {c}" and " -n " in c]
    assert rebuilt, calls
    for c in _explicit_creates(rebuilt):
        assert c.startswith("-a 1 -d "), c


def test_first_hello_does_not_wait_for_the_lldp_listener(fake_machine, tmp_path):
    sysroot = fake_machine["sysroot"]
    run = fake_machine["run"]
    (sysroot / "sys/class/net/eth0/carrier").write_text("1\n")
    sniffer = _stub(tmp_path, "lldpsniff", "sleep 6; printf '{\"unheard\":true,\"waited_s\":6}\n'; exit 2")
    extra = f"export IFACE=eth0 RUN_DIR={posix(run)!r}; "
    t0 = time.monotonic()
    first = netprobe_of(sysroot, extra=extra, lldp=sniffer, fresh=False)
    took = time.monotonic() - t0
    assert first == {"pending": True}
    assert took < 4, f"the first hello waited {took:.1f}s for the background LLDP listener"
    # The background listener (6 s) outlives the hello: the test waits for its end -- the
    # cache stamp it writes last -- instead of leaving it to #1736's orphan guard.
    stamp, end = run / "netprobe.json.at", time.monotonic() + 20
    while not (stamp.is_file() and stamp.read_text().strip()) and time.monotonic() < end:
        time.sleep(0.1)
    assert stamp.is_file() and stamp.read_text().strip(), "the background LLDP listener never finished"
    time.sleep(0.2)   # its subshell exits right after the stamp
