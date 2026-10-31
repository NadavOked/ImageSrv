"""#1921 -- the two knowledge tables: SMART (the disk) and hardware (the cable, the drawer).

Nadav, 20/10 (final):
* RED: the disk itself reports FAILED in SMART RETURN STATUS (the BIOS's F1) -- a source,
  never a target. A FAILED smartctl computed from the attributes (no RETURN STATUS) is not
  the F1: information, "not checked by the disk". NVMe: read-only is red; spare,
  temperature and reliability bits are orange (ס1).
* ORANGE: an early sign, the disk works -- with a Hebrew sentence saying why.
* A single attribute at the vendor's threshold on a PASSED disk is information.
* Matching by (id, the name smartctl printed) -- #1922 (agent) and #1923 (server).
* The cable / the drawer / the controller are not the disk: their own table, a note on the
  drawer, never the disk's colour. CRC (199/218/210) is one of them.
* One data file per table, read by the agent (smartjudge.sh, awk) and by the server
  (smart_names.py) -- and this file checks they read the same ones and agree.

The four cloner-2 disks are real captures (C:\\ImageCtl-Lab\\smart-fixtures-1020, 20/10, serials
replaced); the OCZ Intrepid, the Crucial and the NVMe are built to smartctl's format.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import subprocess

from test_smart import AGENT, BASH, PRELUDE, env, posix, preflight, sh

pytestmark = pytest.mark.skipif(BASH is None, reason="bash נדרש לסוכן ה-POSIX")

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "agent" / "lib" / "data"
FIX = Path(__file__).resolve().parent / "fixtures" / "smart-1921"


def make_smartctl(tmp_path: Path, body: str, rc: int = 0) -> str:
    """test_smart.make_smartctl, with every handle given (Windows: an inherited one under
    pytest's capture is WinError 6 -- #14/#573)."""
    p = tmp_path / "smartctl"
    p.write_text("#!/bin/sh\ncat <<'EOF'\n" + body + "\nEOF\nexit " + str(rc) + "\n", newline="\n")
    subprocess.run([BASH, "-c", f"chmod +x {posix(p)!r}"], check=True,
                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return posix(p)


def fixture(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def judge(body: str, tmp_path: Path, crc: int = 500) -> list[list[str]]:
    """smart_judge on an output: its findings, verdict|word|kind|key|name|value split."""
    f = tmp_path / "out.txt"
    f.write_text(body, encoding="utf-8", newline="\n")
    out = sh(f'export SMART_KB={posix(DATA)}; . {posix(AGENT)}/lib/smart/smartjudge.sh; smart_judge {posix(f)!r} {crc}')
    assert out.returncode == 0, out.stderr
    return [line.split("|") for line in out.stdout.splitlines() if line]


def probe(tmp_path: Path, body: str, dev: str = "sda", rc: int = 0, crc: int | None = None,
          extra: str = "") -> tuple[str, list[str], Path]:
    """smart_probe end to end on a fake smartctl (ro=0): (verdict, cached record, run dir)."""
    run = tmp_path / "run"; run.mkdir(exist_ok=True)
    dev_dir = tmp_path / "dev"; dev_dir.mkdir(exist_ok=True)
    (dev_dir / dev).write_bytes(b"\x00")
    blk = tmp_path / "sysro" / "sys" / "block" / dev; blk.mkdir(parents=True, exist_ok=True)
    (blk / "ro").write_text("0\n", newline="\n")
    sc = make_smartctl(tmp_path, body, rc)
    fake = f'json_get() {{ case "$2" in .crc_warn_threshold) echo {crc} ;; *) echo null ;; esac; }}; ' if crc is not None else ""
    out = sh(env(run, dev_dir, sysroot=tmp_path / "sysro", smartctl=sc) + PRELUDE + fake + extra
             + f'smart_probe {dev}')
    assert out.returncode == 0, out.stderr
    return out.stdout.strip(), (run / "smart" / f"{dev}.v").read_text().split(), run


# --- one table, two readers ---------------------------------------------------------------


def test_the_agent_and_the_server_read_the_same_two_files():
    from server.disks import smart_names
    assert smart_names.SMART_TABLE == DATA / "smart-knowledge.tsv"
    assert smart_names.HW_TABLE == DATA / "hw-issues.tsv"
    sh_src = (AGENT / "lib" / "smart" / "smartjudge.sh").read_text(encoding="utf-8")
    assert 'SMART_KB="${SMART_KB:-${LIB_DIR:-/usr/lib/imagesrv}/data}"' in sh_src
    assert '"$SMART_KB/smart-knowledge.tsv"' in sh_src and '"$SMART_KB/hw-issues.tsv"' in sh_src
    build = (REPO / "tools" / "build_initramfs.sh").read_text(encoding="utf-8")
    assert '"$AGENT_DIR/lib/data/$_kb" "$ROOT/usr/lib/imagesrv/data/$_kb"' in build
    assert "for _kb in smart-knowledge.tsv hw-issues.tsv" in build


def test_both_files_are_well_formed_and_no_name_is_in_both():
    from server.disks import smart_names
    smart = {(r["kind"], r["id"], r["name"]) for r in smart_names._load(smart_names.SMART_TABLE)}
    hw = {(r["kind"], r["id"], r["name"]) for r in smart_names._load(smart_names.HW_TABLE)}
    assert not smart & hw
    # CRC is the hardware table's, never the disk's colour (Nadav 20/10)
    for i, n in ((199, "UDMA_CRC_Error_Count"), (199, "CRC_Error_Count"), (218, "CRC_Error_Count"), (210, "SATA_CRC_Error_Count")):
        assert smart_names.ATA[(i, n)]["table"] == "hw" and smart_names.ATA[(i, n)]["verdict"] == "hw"
    assert set(smart_names.HW) == {"link_slow", "link_reset", "drawer_gone", "usb2", "ide", "crc", "icrc_logged"}


def test_every_orange_and_red_word_has_its_sentence_everywhere():
    """A word of the table the agent can store is in SMART_WARN_REASONS (or red), so smart.sh,
    the GUI and the console say it (test_reasons_he_1861 checks the three translators)."""
    from server.disks import smart_names
    sh_src = (AGENT / "lib" / "smart" / "smart.sh").read_text(encoding="utf-8")
    warn = re.search(r'^SMART_WARN_REASONS="([^"]*)"', sh_src, re.M).group(1).split()
    rows = smart_names._load(smart_names.SMART_TABLE)
    assert {r["word"] for r in rows if r["verdict"] == "orange"} <= set(warn)
    assert {r["word"] for r in rows if r["verdict"] == "red"} == {"media_readonly", "health_failed"}


# --- the real cloner-2 disks: CRC moves to the drawer, the disk is green -------------------


@pytest.mark.parametrize("name,crc", [("cloner2-sda.txt", "1752"), ("cloner2-sdb.txt", "1694"),
                                      ("cloner2-sdc.txt", None), ("cloner2-sdd.txt", "667")])
def test_the_cloner2_disks_are_green_and_crc_is_a_drawer_note(tmp_path, name, crc):
    found = judge(fixture(name), tmp_path)
    assert [f for f in found if f[0] in ("red", "orange", "unknown")] == [], found   # every name known
    assert [f[5] for f in found if f[0] == "hw"] == ([crc] if crc else [])
    verdict, rec, run = probe(tmp_path, fixture(name), crc=500)
    assert (verdict, rec[:2]) == ("ok", ["ok", "ok"])
    assert ("crc" in (run / "smart" / "sda.hw").read_text().split()) == bool(crc)


def test_the_server_says_crc_as_a_drawer_note_not_the_disks_colour(tmp_path):
    """Coordinator 20/10: the server works the same way with both tables -- the console's disk view
    carries the hardware note, and the CRC row is the hardware table's, not a disk verdict."""
    from server import db
    from server.disks import smart_history
    conn = db.connect(tmp_path / "t.db")
    db.set_setting(conn, "crc_warn_threshold", "500")
    attrs = {"ata": [[5, 100, 100, 10, 0, "Reallocated_Sector_Ct"], [199, 99, 99, 0, 1752, "CRC_Error_Count"],
                     [198, 0, 0, 0, 48211, "Host_Reads_GiB"]], "nvme": None}
    view = smart_history.describe(conn, {"serial": "S9", "dev": "sda", "smart_attrs": attrs})
    by = {a["id"]: a for a in view["attributes"]}
    assert (by[199]["table"], by[199]["verdict"]) == ("hw", "hw")
    assert (by[5]["table"], by[5]["verdict"]) == ("smart", "ok")
    assert by[198]["verdict"] == "info" and by[198]["critical"] is False, "OCZ's host reads are not 198 (#1922)"
    assert [h["word"] for h in view["hw"]] == ["crc"] and "לא הדיסק" in view["hw"][0]["he"]
    db.set_setting(conn, "crc_warn_threshold", "0")
    assert smart_history.describe(conn, {"serial": "S9", "dev": "sda", "smart_attrs": attrs})["hw"] == []


def test_crc_threshold_0_is_the_note_off(tmp_path):
    """crcwarn.py: 0 = off (the server's default is 500 since 21/10); the agent follows it."""
    assert judge(fixture("cloner2-sda.txt"), tmp_path, crc=0) == []


# --- by (id, name) -----------------------------------------------------------------------


def test_an_ocz_intrepid_is_not_orange_for_the_bytes_it_read(tmp_path):
    """#1922: 198/199 on an OCZ Intrepid are Host_Reads_GiB / Host_Writes_GiB. By the id alone
    the disk was orange forever."""
    assert judge(fixture("ocz-intrepid-3800.txt"), tmp_path) == []
    verdict, rec, _ = probe(tmp_path, fixture("ocz-intrepid-3800.txt"))
    assert (verdict, rec[:2]) == ("ok", ["ok", "ok"])
    assert rec[4] == "0", "198 Host_Reads_GiB is not stored as the uncorrectable counter"


def test_crucial_202_is_life_remaining_on_the_server(tmp_path):
    """#1923: 202 on a Crucial is Percent_Lifetime_Remain -- not "data address mark errors"."""
    from server.disks import smart_names
    he, note, known = smart_names.name(202, "Percent_Lifetime_Remain")
    assert known and "חיים" in he, (he, note)
    assert smart_names.name(202, "Data_Address_Mark_Errs")[0] != he
    assert smart_names.judge_ata(202, "Percent_Lifetime_Remain", 94, 6) == "ok"
    assert smart_names.judge_ata(202, "Percent_Lifetime_Remain", 8, 92) == "orange"
    assert judge(fixture("crucial-mx500.txt"), tmp_path) == []


def test_an_unknown_name_is_unknown_never_fine(tmp_path):
    from server.disks import smart_names
    body = fixture("cloner2-sdc.txt").replace("Power_On_Hours  ", "Totally_New_Attr")
    found = judge(body, tmp_path)
    assert ["unknown", "-", "ata", "9", "Totally_New_Attr", "9487"] in found, found
    assert smart_names.name(9, "Totally_New_Attr") == ("Totally_New_Attr", "לא מוכר", False)
    assert smart_names.judge_ata(9, "Totally_New_Attr", 97, 9487) == "unknown"
    assert smart_names.name(9, None)[2] is False, "an id alone is not matched (#1923)"


def test_the_agent_and_the_server_judge_every_attribute_alike(tmp_path):
    """The same verdict for every attribute of every fixture, from the two readers."""
    from server.disks import smart_names
    for f in sorted(FIX.glob("*.txt")):
        found = {(int(x[3]), x[4]): x[0] for x in judge(f.read_text(encoding="utf-8"), tmp_path) if x[2] == "ata"}
        media = "ssd" if "Solid State" in f.read_text(encoding="utf-8") else None
        for line in f.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*(\d+) (\S+)\s+0x[0-9a-f]{4}\s+(\d+)\s+\d+\s+\d+\s+\S+\s+\S+\s+\S+\s+(\d+)", line)
            if not m:
                continue
            i, n, value, raw = int(m[1]), m[2], int(m[3]), int(m[4])
            server = smart_names.judge_ata(i, n, value, raw, media=media, crc=500)
            agent = found.get((i, n), "ok" if server in ("ok", "info") else None)
            assert agent == server or (server in ("ok", "info") and (i, n) not in found), (f.name, i, n, server, agent)


# --- red, orange, information --------------------------------------------------------------


def test_nvme_read_only_is_red_and_says_so(tmp_path):
    found = judge(fixture("nvme-readonly.txt"), tmp_path)
    assert found == [["red", "media_readonly", "nvmebit", "3", "Critical Warning", "0x08"]]
    verdict, rec, _ = probe(tmp_path, fixture("nvme-readonly.txt"), dev="nvme0n1", rc=8)
    assert (verdict, rec[:2]) == ("fail", ["fail", "media_readonly"])


@pytest.mark.parametrize("cw,word", [("0x01", "spare_exhausted"), ("0x02", "hot_now"), ("0x04", "nvme_reliability")])
def test_nvme_spare_temperature_reliability_bits_are_orange_not_red(tmp_path, cw, word):
    """ס1: smartctl prints FAILED! for any bit; the bit decides, and these three are orange."""
    body = fixture("nvme-readonly.txt").replace("0x08", cw).replace("- media has been placed in read only mode\n", "")
    verdict, rec, _ = probe(tmp_path, body, dev="nvme0n1", rc=8)
    assert (verdict, rec[:2]) == ("warn", ["warn", word])


@pytest.mark.parametrize("cw", ["0x40", "0x41", "0x80"])
def test_an_nvme_bit_the_table_does_not_know_keeps_failed_red(tmp_path, cw):
    """Review 20/10: FAILED! comes down to the bits only when every bit set is known and judged.
    A reserved (or future) bit is "unknown" -- never "fine": the disk's FAILED stays, red."""
    body = fixture("nvme-readonly.txt").replace("0x08", cw).replace("- media has been placed in read only mode\n", "")
    verdict, rec, _ = probe(tmp_path, body, dev="nvme0n1", rc=8)
    assert (verdict, rec[:2]) == ("fail", ["fail", "health_failed"])


def test_nvme_temperature_by_the_disks_own_wctemp(tmp_path):
    """ס4: over the disk's WCTEMP (84 here) is orange; 70 is not -- 60 is SATA's number."""
    base = fixture("nvme-readonly.txt").replace("0x08", "0x00").replace("FAILED!", "PASSED") \
        .replace("- media has been placed in read only mode\n", "")
    assert judge(base.replace("41 Celsius", "70 Celsius"), tmp_path) == []
    assert ["orange", "hot_now", "nvme", "temperature", "Temperature", "85"] in \
        judge(base.replace("41 Celsius", "85 Celsius"), tmp_path)
    no_wc = base.replace("Warning  Comp. Temp. Threshold:     84 Celsius\n", "")
    assert ["unknown", "-", "nvme", "temperature", "Temperature", "85"] in judge(no_wc.replace("41 Celsius", "85 Celsius"), tmp_path)


def test_ata_failed_from_the_attributes_is_not_the_f1(tmp_path):
    """ס2: no RETURN STATUS -- smartctl's FAILED from the thresholds is information: unchecked."""
    body = fixture("cloner2-sdc.txt").replace(
        "SMART overall-health self-assessment test result: PASSED",
        "SMART Status not supported: Incomplete response, ATA output registers missing\n"
        "SMART overall-health self-assessment test result: FAILED!\n"
        "Warning: This result is based on an Attribute check.")
    verdict, rec, _ = probe(tmp_path, body, rc=8)
    assert (verdict, rec[:2]) == ("unchecked", ["unchecked", "no_health"])


def test_the_disks_own_failed_is_red(tmp_path):
    body = fixture("cloner2-sdc.txt").replace("result: PASSED", "result: FAILED!")
    verdict, rec, _ = probe(tmp_path, body, rc=8)
    assert (verdict, rec[:2]) == ("fail", ["fail", "health_failed"])


def test_a_threshold_reached_on_a_passed_disk_is_information(tmp_path):
    """20/10: one attribute at the vendor's threshold, the disk PASSED -- information, no colour."""
    body = fixture("cloner2-sdc.txt").replace(
        "  9 Power_On_Hours          0x0032   098   098   000    Old_age   Always       -       9487",
        "  9 Power_On_Hours          0x0032   000   000   010    Old_age   Always   FAILING_NOW 9487")
    assert "FAILING_NOW" in body
    verdict, _, _ = probe(tmp_path, body, rc=16)
    assert verdict == "ok"


@pytest.mark.parametrize("line,word", [
    ("197 Current_Pending_Sector  0x0032   100   100   000    Old_age   Always       -       4", "pending_sectors"),
    ("183 Runtime_Bad_Block       0x0013   100   100   010    Pre-fail  Always       -       3", "bad_blocks"),
    ("171 Program_Fail_Count_Chip 0x0032   100   100   010    Old_age   Always       -       1", "program_fail"),
    ("194 Temperature_Celsius     0x0032   035   044   000    Old_age   Always       -       65", "hot_now"),
])
def test_an_early_sign_is_orange_with_its_word(tmp_path, line, word):
    body = fixture("cloner2-sdc.txt")
    body = re.sub(rf"^\s*{line.split()[0]} .*$", line, body, flags=re.M) if re.search(
        rf"^\s*{line.split()[0]} ", body, re.M) else body.replace("199 CRC_Error_Count", line + "\n199 CRC_Error_Count")
    verdict, rec, _ = probe(tmp_path, body)
    assert (verdict, rec[:2]) == ("warn", ["warn", word])


def test_183_downshift_on_another_vendor_is_information(tmp_path):
    """11: 183/188 are information; 183 is bad blocks only under Samsung's name, on an SSD (ס3)."""
    body = fixture("cloner2-sdc.txt").replace(
        "199 CRC_Error_Count", "183 SATA_Downshift_Count    0x0032   100   100   000    Old_age   Always       -       9\n199 CRC_Error_Count")
    assert judge(body, tmp_path) == []


def test_logs_unc_and_a_failed_self_test_are_orange(tmp_path):
    base = fixture("cloner2-sdc.txt")
    unc = base + ("\nSMART Extended Comprehensive Error Log Version: 1 (5 sectors)\nError 1 [0] occurred at disk power-on lifetime: 1234 hours\n"
                  "  Error: UNC at LBA = 0x00012345 = 74565\n")
    assert ["orange", "unc_logged", "errlog", "-", "UNC", "1"] in judge(unc, tmp_path)
    icrc = base + "\n  Error: ICRC, ABRT 1 sectors at LBA = 0x00000000 = 0\n"
    # Nadav 21/10: an ICRC in the error log is a note on the drawer (the hardware table), never a colour
    assert judge(icrc, tmp_path) == [["hw", "icrc_logged", "errlog", "-", "ICRC", "1"]]
    verdict, rec, run = probe(tmp_path, icrc, crc=500)
    assert (verdict, rec[:2]) == ("ok", ["ok", "ok"]), "the disk stays green"
    assert (run / "smart" / "sda.hw").read_text().split() == ["icrc_logged"]
    st = base + ("\nSMART Extended Self-test Log Version: 1 (1 sectors)\nNum  Test_Description    Status                  Remaining  LifeTime(hours)  LBA_of_first_error\n"
                 "# 1  Short offline       Completed: read failure       90%     12510         74565\n"
                 "# 2  Short offline       Completed without error       00%     12000         -\n")
    assert ["orange", "selftest_failed", "selftest", "1", "Completed: read failure", "1"] in judge(st, tmp_path)
    old = st.replace("# 1  Short offline       Completed: read failure       90%     12510         74565\n# 2",
                     "# 1  Short offline       Completed without error       00%     12510         -\n# 2")
    assert judge(old, tmp_path) == [], "only the latest test decides"


def test_the_brief_attribute_format_of_x_is_read_too(tmp_path):
    """`smartctl -x` prints the table brief unless -f old; both are read."""
    body = fixture("cloner2-sdc.txt").replace(
        "  5 Reallocated_Sector_Ct   0x0033   100   100   010    Pre-fail  Always       -       0",
        "  5 Reallocated_Sector_Ct   PO--CK   100   100   010    -    7")
    assert ["orange", "reallocated", "ata", "5", "Reallocated_Sector_Ct", "7"] in judge(body, tmp_path)


def test_smart_probe_asks_for_x(tmp_path):
    src = (AGENT / "lib" / "smart" / "smart.sh").read_text(encoding="utf-8")
    assert '"$SMARTCTL" -x -f old -n never "$_sp_dev"' in src
    assert '"$SMARTCTL" -d sat -x -f old -n never "$_sp_dev"' in src


def test_without_the_tables_nothing_is_judged_and_it_says_so(tmp_path):
    """Principle 5: a missing table is never "fine"."""
    verdict, rec, _ = probe(tmp_path, fixture("cloner2-sdc.txt"), extra=f'SMART_KB={posix(tmp_path)}/none; ')
    assert (verdict, rec[:2]) == ("unchecked", ["unchecked", "no_table"])


def test_an_orange_disk_on_the_gate_is_written_without_a_question(tmp_path):
    body = fixture("cloner2-sdc.txt").replace(
        "171 Program_Fail_Count_Chip 0x0032   100   100   010    Old_age   Always       -       0",
        "171 Program_Fail_Count_Chip 0x0032   100   100   010    Old_age   Always       -       2")
    written, run, err = preflight(tmp_path, body)
    assert written == "sda", err
    assert not (run / "asked").exists(), err   # #2042 (Nadav 23/10): until then asked, no answer = written
    assert (run / "targets/sda/smart_note").read_text(encoding="utf-8") == "SMART: הזיכרון של ה-SSD נכשל בכתיבה או במחיקה"


# --- the hardware table: link, dmesg, USB -----------------------------------------------------


def hw_words(dmesg: str, n: int = 3, t0: str = "5.0") -> list[str]:
    """hw_link_words; t0 = the dmesg time the drawer was identified at (#1937)."""
    # %b: the repr's \n are lines (with %s the whole dmesg was one line, and the rules met on it)
    out = sh(f'. {posix(AGENT)}/lib/smart/hwissues.sh; printf "%b" {dmesg!r} | hw_link_words {n} {t0!r}')
    assert out.returncode == 0, out.stderr
    return out.stdout.split()


def test_a_link_reset_and_a_drawer_that_dropped_are_hardware_words():
    up = "[    2.1] ata3: SATA link up 3.0 Gbps (SStatus 123 SControl 300)\n"
    assert hw_words(up) == []
    assert hw_words(up + "[  812.4] ata3: hard resetting link\n") == ["link_reset"]
    assert hw_words(up + "[  900.0] ata3: SATA link down (SStatus 0 SControl 300)\n") == ["drawer_gone"]
    assert hw_words(up + "[  900.0] ata3.00: disable device\n") == ["drawer_gone"]
    assert hw_words("[  812.4] ata13: hard resetting link\n") == [], "ata13 is not ata3"
    assert hw_words("[    1.0] ata3: SATA link down (SStatus 0 SControl 300)\n") == [], "an empty port at boot"


# #1937 (the college, 21/10, HP 8000 in IDE mode): both drawers "dropped" on a machine where
# nothing dropped. The boot probe of ata_piix -- the port's empty slave link goes down right
# after its master came up (libata prints a slave link as ataN.01). Reconstructed from libata:
# the field diag's dmesg was left out (the stick's serial, item 2 of the same issue).
PIIX_BOOT = ("[    1.802] ata3.00: SATA link up 3.0 Gbps (SStatus 123 SControl 300)\n"
             "[    1.810] ata3.01: SATA link down (SStatus 0 SControl 300)\n"
             "[    1.951] ata3.00: ATA-9: SAMSUNG MZ7TE256HMHP-00000, EXT0100Q, max UDMA/133\n"
             "[    9.500] random: crng init done\n")


def test_the_boot_probe_is_not_a_drawer_that_dropped():
    assert hw_words(PIIX_BOOT, t0="9.5") == []
    assert hw_words(PIIX_BOOT, t0="") == [], "identified when: not read -- nothing said"
    assert hw_words(PIIX_BOOT + "[  950.000] ata3.00: SATA link down (SStatus 0 SControl 300)\n", t0="9.5") == ["drawer_gone"]


def _hw_after(tmp_path: Path, later: str, gone: bool = False) -> tuple[list[str], list[str]]:
    """The real smartctl -x of the SAMSUNG MZ7TE256 (COMRESET=3, PhyRdy->PhyNRdy=10 since
    power-on) through smart_probe on the boot dmesg, then hw_refresh again after the write
    with `later` appended: (the words when identified, the words after)."""
    body = fixture("field-hp8000-samsung-mz7te256.txt")
    assert "  3  Device-to-host register FISes sent due to a COMRESET" in body
    assert " 10  Transition from drive PhyRdy to drive PhyNRdy" in body
    dm = tmp_path / "dmesg.txt"; dm.write_text(PIIX_BOOT, newline="\n")
    stub = (f'. {posix(AGENT)}/lib/smart/hwissues.sh; _hw_ata() {{ echo 3; }}; '
            f'dmesg_stub() {{ cat {posix(dm)!r}; }}; DMESG=dmesg_stub; ')
    verdict, _, run = probe(tmp_path, body, extra=stub)
    assert verdict == "ok", verdict   # the field's screen: green
    first = (run / "smart" / "sda.hw").read_text().split()
    dm.write_text(PIIX_BOOT + later, newline="\n")
    rm = f'rm -rf {posix(tmp_path / "sysro" / "sys" / "block" / "sda")!r}; ' if gone else ""
    out = sh(env(run, tmp_path / "dev", sysroot=tmp_path / "sysro") + stub + rm + 'hw_refresh sda; cat "$RUN_DIR/smart/sda.hw"')
    assert out.returncode == 0, out.stderr
    return first, out.stdout.split()


def test_the_samsung_of_the_field_without_a_delta_is_no_drawer_gone(tmp_path):
    first, after = _hw_after(tmp_path, "[  400.000] ata3.00: configured for UDMA/133\n")
    assert "drawer_gone" not in first and "drawer_gone" not in after, (first, after)


def test_a_link_down_after_the_drawer_was_identified_is_drawer_gone(tmp_path):
    first, after = _hw_after(tmp_path, "[  950.000] ata3.00: SATA link down (SStatus 0 SControl 300)\n")
    assert "drawer_gone" not in first and "drawer_gone" in after, (first, after)


def test_a_disk_that_left_sys_block_after_it_was_identified_is_drawer_gone(tmp_path):
    first, after = _hw_after(tmp_path, "", gone=True)
    assert "drawer_gone" not in first and "drawer_gone" in after, (first, after)


def test_the_hw_words_are_read_again_after_the_write():
    src = (AGENT / "lib" / "smart" / "crcdelta.sh").read_text(encoding="utf-8")
    assert 'command -v hw_refresh >/dev/null 2>&1 && hw_refresh "$_cd_d"' in src


def test_the_hw_sentences_come_from_the_table():
    from server.disks import smart_names
    for w in ("link_slow", "link_reset", "drawer_gone", "usb2", "ide", "crc", "icrc_logged"):
        out = sh(f'export SMART_KB={posix(DATA)}; . {posix(AGENT)}/lib/smart/smartjudge.sh; smart_kb_he {w} | od -An -c | tr -d " \\n" | head -c 40')
        assert out.returncode == 0 and out.stdout.strip(), w   # bytes printed (od: no locale in the way)
        he = smart_names.hw_sentence(w)
        assert he and "|" not in he, w
        if w not in ("usb2", "ide"):
            assert "לא הדיסק" in he, (w, he)   # the sentence itself says it is not the disk


def _sata_disk(sysroot: Path, ata: int, host: int, spd: str, caps: str) -> None:
    import shutil
    target = sysroot / f"sys/devices/pci0000_00/ata{ata}/host{host}/target{host}_0_0/{host}_0_0_0"
    target.mkdir(parents=True)
    device = sysroot / "sys/block/sda/device"
    device.parent.mkdir(parents=True, exist_ok=True)
    if device.exists():
        shutil.rmtree(device)
    try:
        device.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlinks are not available here: {exc}")
    link = sysroot / f"sys/class/ata_link/link{ata}"; link.mkdir(parents=True)
    (link / "sata_spd").write_text(spd + "\n")
    shost = sysroot / f"sys/class/scsi_host/host{host}"; shost.mkdir(parents=True)
    (shost / "ahci_host_caps").write_text(caps + "\n")


@pytest.mark.parametrize("caps,disk_ver,slow", [
    ("c734ff45", "SATA 3.1, 6.0 Gb/s", True),     # a 6 Gb/s port and disk, linked at 3.0: the cable
    ("c724ff45", "SATA 3.1, 6.0 Gb/s", False),    # an HP 8100: a SATA II port -- 3.0 is its ceiling
    ("c734ff45", "SATA 2.6, 3.0 Gb/s", False),    # a SATA II disk on a 6 Gb/s port
])
def test_link_slow_is_below_both_the_port_and_the_disk(tmp_path, caps, disk_ver, slow):
    sysroot = tmp_path / "sys0"
    _sata_disk(sysroot, 3, 2, "3.0 Gbps", caps)
    run = tmp_path / "run"; (run / "smart").mkdir(parents=True)
    (run / "smart" / "sda.out").write_text(f"SATA Version is:  {disk_ver} (current: 3.0 Gb/s)\n", newline="\n")
    out = sh(f'export SYSROOT={posix(sysroot)!r} RUN_DIR={posix(run)!r} DMESG=true; '
             f'. {posix(AGENT)}/lib/hw/disklink.sh; . {posix(AGENT)}/lib/smart/hwissues.sh; hw_refresh sda; cat "$RUN_DIR/smart/sda.hw"')
    assert out.returncode == 0, out.stderr
    assert ("link_slow" in out.stdout.split()) == slow, out.stdout
