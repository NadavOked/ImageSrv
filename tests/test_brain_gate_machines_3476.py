"""#3476, K3, קבוצה ד': חמשת שערי המשכפל לפני פתיחת סבב (`server/brain/gate_machines.py`): AHCI, אתחולים, PXE, כרטיס רשת, קושחה. בצל.

הקלט כאן אינו mock של מה שהשער כותב: כל שורה נכתבת דרך הפונקציה האמיתית של המקור -- `firmware_image.record`/`accept`,
`bootguard.guard`, `loops.note`, `pxe_log.sweep`, `wol.sent`, `probe.record`, ו-hello אמיתי בבדיקת החיבור -- והשער קורא אותה
בחזרה. מה שכן מוחלף (`monkeypatch`) הוא רק כשל של מקור, כדי להגיע לענף ה"לא הצלחנו לבדוק".

מה נבדק, לכל שער:

- **כל פסק שהשער מחזיר** (`OPEN`, `WARN`, `BLOCK` היכן שהוא אפשרי, `UNDECIDED`), והסיבה היא הטקסט הקיים של המקור.
- **"לא נמדד" אינו "נבדק ותקין"** (עיקרון 5): מקור שלא נקרא, אין בו שורה, או שלא נמדד בו דבר -- `UNDECIDED`, לעולם לא `OPEN`.
- **קריאה לכל משכפל**, גם כשהבדיקה של אחד נפלה.

**בקרה שלילית:** ראו את גוף ה-PR (`gate_machines.py` מוחזר לשלד, והטסטים כאן נופלים).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("fastapi")

from console_src import console_js
from conftest import hello_body
from server import db
from server.agent import bootguard, firmware_image, loops as agent_loops, probe, registry
from server.brain import agentsenses, gate_machines, gates, memory
from server.brain.gate_core import BLOCK, OPEN, UNDECIDED, VERDICTS, WARN, name_of
from server.console.journal_he import EVENTS_HE
from server.learn import wiring, wol
from server.net import pxe_log
from server.rounds import prodline
from server.rounds.sessions import SessionStore
from test_learning_2028 import _fleet                                     # noqa: F401 -- ``_fleet`` נשען על fixture
from test_server_room import CLONER1, CLONER2, room_body, room_server    # noqa: F401 -- room_server הוא fixture

MAC1, MAC2, MAC3 = "aa:bb:cc:00:00:21", "aa:bb:cc:00:00:22", "aa:bb:cc:00:00:23"
RID, WAVE = "room_t3476", "ses_wave3476"
GATES = (gate_machines.ahci, gate_machines.boot, gate_machines.pxe, gate_machines.nic, gate_machines.firmware)


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "gate_machines.db")
    for mac, name in ((MAC1, "shich-1"), (MAC2, "shich-2")):
        registry.add_machine(c, mac, name, "grp_CLONERS", "test")
    return c


def add_round(conn, ports=None, *, line=False):
    """שורת סבב כפי ש-`room.open_round` כותב אותה (העמודות שהשערים קוראים), ואם `line` -- גם שורת פס ייצור."""
    ports = {MAC1: [1, 2], MAC2: [1]} if ports is None else ports
    conn.execute("INSERT INTO room_rounds (id, image_id, target_drives, target_slots_json, state, wave_session_id, opened_by, created_at)"
                 " VALUES (?,?,?,?,?,?,?,?)",
                 (RID, "img_x", sum(map(len, ports.values())), json.dumps([{"mac": m, "ports": p} for m, p in ports.items()]),
                  "active", WAVE, "test", db.now_iso()))
    if line:
        conn.execute("INSERT INTO prodline_rounds (round_id, drawers, target, created_at) VALUES (?,?,?,?)", (RID, 3, 12, db.now_iso()))
    conn.commit()


def fw(conn, mac, **fields):
    """hello של מכונה רשומה, דרך המקור: ``firmware_image.record`` כותב תמונה רק כשהיא השתנתה."""
    firmware_image.record(conn, mac, {"firmware": "uefi", "secure_boot": True, "sata_mode": "ahci", "sata_driver": "ahci",
                                      "bios": {"version": "F.10"}, **fields})


def both(gate, conn):
    return dict(zip((MAC1, MAC2), gate(conn, (MAC1, MAC2), RID), strict=True))


# --- חוזה משותף: קריאה לכל משכפל --------------------------------------------------------------------------------------


@pytest.mark.parametrize("gate", GATES, ids=lambda g: g.__name__)
def test_every_gate_reads_every_machine_in_order_and_names_it(conn, gate):
    got = gate(conn, (MAC1, MAC2, MAC3), RID)
    assert [r.mac for r in got] == [MAC1, MAC2, MAC3]
    assert all(r.verdict in VERDICTS and r.reason.strip() for r in got)
    assert got[0].reason.startswith(f"{name_of(conn, MAC1)}:"), "הסיבה נוקבת בשם המשכפל"
    assert got[2].reason.startswith(f"{MAC3}:"), "משכפל לא רשום נקרא בכתובת שלו, ולא נדלג"


@pytest.mark.parametrize("gate", GATES, ids=lambda g: g.__name__)
def test_no_cloners_is_undecided_not_open(conn, gate):
    [r] = gate(conn, (), RID)
    assert r.verdict == UNDECIDED and r.mac is None


def test_a_machine_whose_check_crashed_is_undecided_and_the_other_is_still_read(conn, monkeypatch):
    fw(conn, MAC1)
    fw(conn, MAC2)
    real = firmware_image.changes_for

    def flaky(c, mac):
        if mac == MAC1:
            raise RuntimeError("boom")
        return real(c, mac)

    monkeypatch.setattr(firmware_image, "changes_for", flaky)
    got = both(gate_machines.ahci, conn)
    assert got[MAC1].verdict == UNDECIDED and "RuntimeError" in got[MAC1].reason
    assert got[MAC2].verdict == OPEN


# --- B23: AHCI -------------------------------------------------------------------------------------------------------


def test_ahci_is_open_on_a_snapshot_that_says_ahci_and_undecided_on_a_machine_that_never_reported(conn):
    fw(conn, MAC1)
    got = both(gate_machines.ahci, conn)
    assert got[MAC1].verdict == OPEN
    assert got[MAC1].reason == f"{name_of(conn, MAC1)}: {agentsenses.ahci_gate('ahci', 'ahci')[1]}"
    assert {"mode=ahci", "driver=ahci"} <= set(got[MAC1].evidence)
    # אין תמונה בכלל: "לא נקרא", ולא "תקין" ולא "חוסם"
    assert got[MAC2].verdict == UNDECIDED and agentsenses.ahci_gate(None)[1] in got[MAC2].reason


def test_a_snapshot_without_a_mode_is_undecided_not_open(conn):
    firmware_image.record(conn, MAC1, {"firmware": "uefi"})       # סוכן ישן: בלי sata_mode
    got = both(gate_machines.ahci, conn)[MAC1]
    assert got.verdict == UNDECIDED and "mode=None" in got.evidence


@pytest.mark.parametrize("mode, driver", [("ide", "ata_piix"), ("raid", "none")])
def test_ide_or_raid_warns_in_a_normal_round_and_names_the_driver(conn, mode, driver):
    add_round(conn)
    fw(conn, MAC1, sata_mode=mode, sata_driver=driver)
    got = both(gate_machines.ahci, conn)[MAC1]
    assert got.verdict == WARN
    assert got.reason == f"{name_of(conn, MAC1)}: {agentsenses.ahci_gate(mode, driver)[1]}"
    assert f"driver={driver}" in got.evidence and "production_line=false" in got.evidence


def test_without_ahci_a_production_line_round_is_block_and_the_reason_says_it_is_shadow(conn):
    add_round(conn, line=True)
    fw(conn, MAC1, sata_mode="ide", sata_driver="ata_piix")
    got = both(gate_machines.ahci, conn)[MAC1]
    assert got.verdict == BLOCK and "בלי AHCI אין פס ייצור (צל: לא נחסם)" in got.reason
    assert "production_line=true" in got.evidence
    assert prodline.line_of(conn, RID) is not None, "התנאי המוקדם: הסבב באמת פס ייצור"


def test_a_production_line_that_could_not_be_read_is_never_block_on_a_guess(conn, monkeypatch):
    add_round(conn, line=True)
    fw(conn, MAC1, sata_mode="raid")
    monkeypatch.setattr(prodline, "line_of", lambda *a, **k: 1 / 0)
    got = both(gate_machines.ahci, conn)[MAC1]
    assert got.verdict == WARN and "production_line=unknown" in got.evidence


def test_ahci_reads_the_driver_the_real_hello_stored(room_server):
    ctx, anon = room_server["ctx"], room_server["anon"]
    body = hello_body(CLONER1)
    body.update(sata_mode="ide", sata_driver="ata_piix")
    assert anon.post("/api/v1/agent/hello", json=body).status_code == 200
    got = gate_machines.ahci(ctx.conn, (CLONER1,), "room_none")[0]
    assert got.verdict == WARN and "driver=ata_piix" in got.evidence and "(הדרייבר: ata_piix)" in got.reason


# --- B52: אתחולים ולולאות --------------------------------------------------------------------------------------------

SESSION = {"schema": 1, "known": True, "role": "classroom", "group": {"id": "grp_LAB1", "label": "כיתה LAB1", "suffix": "05"},
           "task": None, "session": {"id": WAVE, "state": "open", "image_id": "img_7f3a91", "prefix": "LAB1",
                                     "expected_clients": 30, "joined": 11, "starts_in_seconds": 134},
           "allowed_images": [], "ui": {"language": "he", "require_login": False}}


def menu_requests(conn, mac, times, session_id=WAVE):
    """בקשות תפריט אמיתיות של השרת: `bootguard.guard` סופר, ומחזיר את התשובה האחרונה."""
    answer = None
    for _ in range(times):
        answer = bootguard.guard(conn, mac, {**SESSION, "session": {**SESSION["session"], "id": session_id}})
    return answer


def reach_the_agent_without_a_reason(conn, mac):
    """hello אמיתי של מחשב שנשלח לדיסק המקומי ובכל זאת הגיע לסוכן: `loops.note` סופר אותו."""
    answer = {"schema": 1, "known": True, "role": "classroom", "group": {"id": "grp_LAB1"}, "task": None, "session": None}
    assert agent_loops.note(conn, SessionStore(conn), mac, answer) == 1


def test_the_console_still_says_the_boot_texts_the_gate_quotes():
    js = console_js()
    assert gate_machines.BLOCKED_HE.format(n="${s.attempts}") in js and gate_machines.REPEATED_HE.format(n="${s.attempts}") in js


def test_boot_is_open_with_the_evidence_when_nothing_was_counted_and_nobody_loops(conn):
    add_round(conn)
    got = both(gate_machines.boot, conn)[MAC1]
    assert got.verdict == OPEN and {"attempts=none", "loops=none"} <= set(got.evidence)
    assert f"{agent_loops.SILENCE_SECONDS // 60} הדקות האחרונות" in got.reason


def test_one_menu_request_is_the_normal_path_and_stays_open(conn):
    add_round(conn)
    menu_requests(conn, MAC1, 1)
    got = both(gate_machines.boot, conn)[MAC1]
    assert got.verdict == OPEN and "attempts=<2" in got.evidence


def test_a_second_boot_into_this_round_is_a_warn_in_the_console_words(conn):
    add_round(conn)
    menu_requests(conn, MAC1, 2)
    got = both(gate_machines.boot, conn)
    assert got[MAC1].verdict == WARN and got[MAC1].reason == f"{name_of(conn, MAC1)}: {gate_machines.REPEATED_HE.format(n=2)}"
    assert got[MAC2].verdict == OPEN, "מכונה אחרת אינה נגררת"


def test_a_machine_the_guard_already_sent_to_the_local_disk_in_this_round_is_block(conn):
    add_round(conn)
    answer = menu_requests(conn, MAC1, bootguard.ATTEMPT_LIMIT + 1)
    assert answer["boot_guard"] == bootguard.EXHAUSTED, "התנאי המוקדם: הקוד הקיים עצמו כבר מסרב"
    got = both(gate_machines.boot, conn)[MAC1]
    assert got.verdict == BLOCK
    assert got.reason == f"{name_of(conn, MAC1)}: {gate_machines.BLOCKED_HE.format(n=bootguard.ATTEMPT_LIMIT + 1)}"
    assert {f"context=session:{WAVE}", "round_context=True"} <= set(got.evidence)


def test_a_machine_exhausted_in_an_earlier_job_is_a_warn_and_never_block(conn):
    add_round(conn)
    menu_requests(conn, MAC1, bootguard.ATTEMPT_LIMIT + 1, session_id="ses_older")
    got = both(gate_machines.boot, conn)[MAC1]
    assert got.verdict == WARN and "round_context=False" in got.evidence


def test_a_repeat_in_an_earlier_job_that_the_guard_did_not_stop_is_not_a_finding(conn):
    add_round(conn)
    menu_requests(conn, MAC1, 2, session_id="ses_older")
    assert both(gate_machines.boot, conn)[MAC1].verdict == OPEN


def test_a_round_that_could_not_be_read_cannot_make_an_exhausted_machine_block(conn):
    menu_requests(conn, MAC1, bootguard.ATTEMPT_LIMIT + 1)          # בלי שורת סבב: ההקשר לא ידוע
    assert both(gate_machines.boot, conn)[MAC1].verdict == WARN


def test_a_machine_that_reaches_the_agent_with_no_reason_is_a_warn_with_the_health_row_text(conn):
    add_round(conn)
    reach_the_agent_without_a_reason(conn, MAC1)
    row = next(r for r in agent_loops.loop_checks(agent_loops.current(conn)) if r["id"] == f"agent_loop:{MAC1}")
    got = both(gate_machines.boot, conn)[MAC1]
    assert got.verdict == WARN and got.reason == f"{name_of(conn, MAC1)}: {EVENTS_HE['agent_loop']} — {row['detail']}"
    assert "loops=in_loop" in got.evidence


def test_the_worst_of_the_two_sources_wins_and_both_texts_are_kept(conn):
    add_round(conn)
    menu_requests(conn, MAC1, bootguard.ATTEMPT_LIMIT + 1)
    reach_the_agent_without_a_reason(conn, MAC1)
    got = both(gate_machines.boot, conn)[MAC1]
    assert got.verdict == BLOCK and "נשלח לדיסק המקומי" in got.reason and EVENTS_HE["agent_loop"] in got.reason


def test_a_loop_list_that_could_not_be_read_is_undecided_not_no_loops(conn, monkeypatch):
    add_round(conn)
    monkeypatch.setattr(agent_loops, "current", lambda *a, **k: 1 / 0)
    got = both(gate_machines.boot, conn)[MAC1]
    assert got.verdict == UNDECIDED and "רשימת הלולאות לא נקראה" in got.reason and "loops=unread" in got.evidence
    menu_requests(conn, MAC1, 2)                                    # ממצא שכן נקרא אינו נבלע בגלל המקור השני
    assert both(gate_machines.boot, conn)[MAC1].verdict == WARN


def test_a_boot_counter_that_could_not_be_read_is_undecided_not_no_repeats(conn, monkeypatch):
    add_round(conn)
    menu_requests(conn, MAC1, 2)
    monkeypatch.setattr(bootguard, "repeats", lambda *a, **k: 1 / 0)
    got = both(gate_machines.boot, conn)[MAC1]
    assert got.verdict == UNDECIDED and "מונה האתחולים לא נקרא" in got.reason and "attempts=unread" in got.evidence


# --- B53: PXE ו-DHCP -------------------------------------------------------------------------------------------------


def stamp(seconds_ago: int) -> str:
    return (datetime.now(timezone.utc).replace(microsecond=0) - timedelta(seconds=seconds_ago)).strftime("%Y-%m-%dT%H:%M:%S+0000")


def dhcp_lines(mac, ip, *, tftp=False):
    lines = [f"{stamp(50)} imagesrv dnsmasq-dhcp[812]: 1 DHCPDISCOVER(eth1) {mac.upper()}",
             f"{stamp(49)} imagesrv dnsmasq-dhcp[812]: 1 DHCPOFFER(eth1) {ip} {mac}",
             f"{stamp(48)} imagesrv dnsmasq-dhcp[812]: DHCPREQUEST(eth1) {ip} {mac}",
             f"{stamp(47)} imagesrv dnsmasq-dhcp[812]: DHCPACK(eth1) {ip} {mac} cloner"]
    if tftp:
        lines.append(f"{stamp(46)} imagesrv dnsmasq-tftp[812]: sent /srv/imagesrv/tftp/bootx64.efi to {ip}")
    return lines + ["-- cursor: s=abc;i=1"]


def sweep(conn, lines):
    assert pxe_log.sweep(conn, lambda cursor: (lines, "x")) >= 0


def agent_crumb(conn, mac, step):
    now = db.now_iso()
    conn.execute("INSERT INTO boot_steps (mac, step, idx, at, first_at) VALUES (?, ?, 8, ?, ?)", (mac, step, now, now))
    conn.commit()


def test_a_machine_that_reached_the_agent_is_open_in_the_pxe_words(conn):
    sweep(conn, dhcp_lines(MAC1, "10.44.12.31", tftp=True))
    agent_crumb(conn, MAC1, "agent-hello")
    got = both(gate_machines.pxe, conn)[MAC1]
    assert got.verdict == OPEN and got.reason == f"{name_of(conn, MAC1)}: {pxe_log.STATES_HE['hello']}" and "state=hello" in got.evidence


def test_dhcp_without_the_boot_file_is_a_warn_in_the_pxe_words(conn):
    sweep(conn, dhcp_lines(MAC1, "10.44.12.31"))
    got = both(gate_machines.pxe, conn)[MAC1]
    assert got.verdict == WARN and got.reason == f"{name_of(conn, MAC1)}: {pxe_log.STATES_HE['no_tftp']}"


def test_the_boot_file_without_the_agent_is_a_warn_in_the_pxe_words(conn):
    sweep(conn, dhcp_lines(MAC1, "10.44.12.31", tftp=True))
    got = both(gate_machines.pxe, conn)[MAC1]
    assert got.verdict == WARN and got.reason == f"{name_of(conn, MAC1)}: {pxe_log.STATES_HE['no_hello']}"


def test_a_readable_log_with_no_event_of_the_machine_is_not_measured_and_not_did_not_boot(conn):
    sweep(conn, dhcp_lines(MAC2, "10.44.12.32", tftp=True))         # הלוג נקרא, אבל על מכונה אחרת
    assert pxe_log.verdict(conn, MAC1)["state"] == "no_dhcp", "התנאי המוקדם: הקוד הקיים היה אומר ״לא שלח DHCP״"
    got = both(gate_machines.pxe, conn)[MAC1]
    assert got.verdict == UNDECIDED and "לא נמדד" in got.reason and pxe_log.STATES_HE["no_dhcp"] not in got.reason
    assert "events=0" in got.evidence


def test_a_log_that_could_not_be_read_is_undecided(conn):
    def broken(cursor):
        raise RuntimeError("journalctl rc=1")

    assert pxe_log.sweep(conn, broken) == -1
    got = both(gate_machines.pxe, conn)[MAC1]
    assert got.verdict == UNDECIDED and got.reason == f"{name_of(conn, MAC1)}: {pxe_log.STATES_HE['unchecked']}"


def test_a_log_that_was_never_read_is_undecided(conn):
    assert both(gate_machines.pxe, conn)[MAC1].verdict == UNDECIDED


def test_only_what_happened_after_the_last_wake_counts(conn):
    sweep(conn, dhcp_lines(MAC1, "10.44.12.31", tftp=True))         # האירועים בני 46-50 שניות
    assert both(gate_machines.pxe, conn)[MAC1].verdict == WARN, "בלי הערה: האירועים נספרים"
    wol.sent(conn, MAC1, now=datetime.now(timezone.utc).timestamp() - 10)
    after = both(gate_machines.pxe, conn)[MAC1]
    assert after.verdict == UNDECIDED and pxe_log.since_last_wake(conn, MAC1) in after.reason


# --- B54: כרטיס רשת, כבל ---------------------------------------------------------------------------------------------


def put_probe(conn, mac, *, crc=0, cable="ok", dup=False, extra=None):
    raw = {"nic": [{"name": "eth0", "speed_mbps": 1000, "duplex": "Full",
                    "stats": {"rx_crc_errors": crc, "rx_dropped": 0, "tx_errors": 0, "collisions": 0},
                    "ip_conflict": {"checked": True, "duplicate": dup}}],
           "netprobe": {"cable": {"status": cable, "pairs": []}} if cable else None, **(extra or {})}
    probe.record(conn, mac, probe.well_formed(raw))
    return probe.latest(conn, mac)["probe"]


def test_a_clean_counter_and_a_cable_that_tested_ok_is_open_and_the_evidence_says_what_was_measured(conn):
    put_probe(conn, MAC1)
    got = both(gate_machines.nic, conn)[MAC1]
    assert got.verdict == OPEN and "measured=crc,cable" in got.evidence
    assert "מוני שגיאות הכרטיס ובדיקת הכבל" in got.reason


def test_a_clean_counter_alone_is_open_but_says_only_the_counter(conn):
    put_probe(conn, MAC1, cable=None)
    got = both(gate_machines.nic, conn)[MAC1]
    assert got.verdict == OPEN and "measured=crc" in got.evidence and "בדיקת הכבל" not in got.reason


def test_crc_errors_are_a_warn_in_the_words_of_probe_verdicts(conn):
    sample = put_probe(conn, MAC1, crc=17)
    [verdict] = [v for v in probe.verdicts(sample) if v["key"] == "crc:eth0"]
    got = both(gate_machines.nic, conn)[MAC1]
    assert got.verdict == WARN and got.reason == f"{name_of(conn, MAC1)}: {verdict['text_he']}" and "finding=crc:eth0" in got.evidence


def test_a_broken_cable_is_a_warn_in_the_words_of_probe_verdicts(conn):
    sample = put_probe(conn, MAC1, cable="open")
    sample["netprobe"]["cable"]["pairs"] = [{"pair": "B", "code": "open", "length_m": 41}]
    probe.record(conn, MAC1, sample)
    got = both(gate_machines.nic, conn)[MAC1]
    assert got.verdict == WARN and "כבל פגום: זוג B פתוח ב-41 מטר" in got.reason and "finding=cable" in got.evidence


def test_a_duplicate_ip_is_a_warn(conn):
    put_probe(conn, MAC1, dup=True)
    got = both(gate_machines.nic, conn)[MAC1]
    assert got.verdict == WARN and "כפילות IP על eth0" in got.reason


def test_findings_the_nic_gate_does_not_own_do_not_leak_in(conn):
    put_probe(conn, MAC1, extra={"power": {"on_battery": True}})
    assert probe.verdicts(probe.latest(conn, MAC1)["probe"])[0]["key"] == "battery", "התנאי המוקדם: ל-probe יש ממצא אחר"
    assert both(gate_machines.nic, conn)[MAC1].verdict == OPEN


def test_a_machine_with_no_probe_is_undecided(conn):
    got = both(gate_machines.nic, conn)[MAC1]
    assert got.verdict == UNDECIDED and "probe=none" in got.evidence


def test_a_probe_where_neither_the_counter_nor_the_cable_was_measured_is_undecided_not_open(conn):
    probe.record(conn, MAC1, probe.well_formed({"nic": [{"name": "eth0", "stats": {"error": "unreadable"}}],
                                                "netprobe": {"cable": {"status": "unchecked", "reason": "unsupported"}}}))
    got = both(gate_machines.nic, conn)[MAC1]
    assert got.verdict == UNDECIDED and "measured=none" in got.evidence


def test_a_probe_with_nothing_in_it_is_undecided(conn):
    probe.record(conn, MAC1, probe.well_formed({}))
    assert both(gate_machines.nic, conn)[MAC1].verdict == UNDECIDED


# --- B56: קושחה וחיווט -----------------------------------------------------------------------------------------------


def test_a_machine_with_no_snapshot_is_undecided(conn):
    got = both(gate_machines.firmware, conn)[MAC1]
    assert got.verdict == UNDECIDED and "אין תמונת קושחה" in got.reason


def test_a_single_snapshot_with_no_reference_is_undecided_not_open(conn):
    fw(conn, MAC1)
    got = both(gate_machines.firmware, conn)[MAC1]
    assert got.verdict == UNDECIDED and "אין הפניה" in got.reason and "ref=None" in got.evidence


def test_a_snapshot_equal_to_the_saved_baseline_is_open(conn):
    fw(conn, MAC1)
    assert firmware_image.accept(conn, MAC1, "noc")
    got = both(gate_machines.firmware, conn)[MAC1]
    assert got.verdict == OPEN and "ההגדרה הנכונה ששמרו" in got.reason and "ref=baseline" in got.evidence and "wiring=no_basis" in got.evidence


def test_a_snapshot_equal_to_the_previous_boot_is_open(conn):
    fw(conn, MAC1)
    fw(conn, MAC1, sata_driver="ahci2")          # הדרייבר אינו שדה שמושווה: תמונה חדשה בלי סטייה
    assert len(firmware_image._rows(conn, MAC1, 5)) == 2, "התנאי המוקדם: יש הדלקה קודמת"
    got = both(gate_machines.firmware, conn)[MAC1]
    assert got.verdict == OPEN and "ההדלקה הקודמת" in got.reason and "ref=previous" in got.evidence


def test_drift_from_the_baseline_is_a_warn_in_the_words_of_the_fw_changed_journal(conn):
    fw(conn, MAC1)
    firmware_image.accept(conn, MAC1, "noc")
    fw(conn, MAC1, sata_mode="ide", bios={"version": "F.20"})
    expected = [c["text"] for c in firmware_image.changes_for(conn, MAC1)["changes"]]
    assert len(expected) == 2, expected
    got = both(gate_machines.firmware, conn)[MAC1]
    assert got.verdict == WARN and got.reason == f"{name_of(conn, MAC1)}: " + " · ".join(expected)
    assert "changed=field:sata_mode" in got.evidence and "changed=field:bios_version" in got.evidence


def test_a_drawer_that_moved_to_another_connection_is_a_warn(conn):
    disks = lambda port: [{"port": port, "serial": "SER-1", "model": "SSD", "size_bytes": 10 ** 11}]       # noqa: E731
    fw(conn, MAC1, disks=disks(1))
    firmware_image.accept(conn, MAC1, "noc")
    fw(conn, MAC1, disks=disks(2))
    got = both(gate_machines.firmware, conn)[MAC1]
    assert got.verdict == WARN and "דיסק SER-1 עבר מ-SATA 0 ל-SATA 1" in got.reason and "changed=moved:" in got.evidence


def test_wiring_advice_that_could_not_be_read_is_undecided_when_the_firmware_is_otherwise_fine(conn, monkeypatch):
    fw(conn, MAC1)
    firmware_image.accept(conn, MAC1, "noc")
    monkeypatch.setattr(wiring, "advice", lambda *a, **k: 1 / 0)
    got = both(gate_machines.firmware, conn)[MAC1]
    assert got.verdict == UNDECIDED and "החיווט" in got.reason and any(e.startswith("wiring=error") for e in got.evidence)
    fw(conn, MAC1, bios={"version": "F.99"})                         # סטייה שכן נקראה אינה נבלעת בגלל החיווט
    drift = both(gate_machines.firmware, conn)[MAC1]
    assert drift.verdict == WARN and any(e.startswith("wiring=error") for e in drift.evidence)


def fleet_with_baselines(room_server):
    _fleet(room_server)                      # שני משכפלים מאותו דגם, 6 גלים: SATA 3 (חיבור 4) רע בשניהם
    conn = room_server["ctx"].conn
    for mac in (CLONER1, CLONER2):
        assert firmware_image.accept(conn, mac, "noc")
    return conn


def add_round_for(conn, ports):
    conn.execute("INSERT INTO room_rounds (id, image_id, target_drives, target_slots_json, state, wave_session_id, opened_by, created_at)"
                 " VALUES (?,?,?,?,?,?,?,?)",
                 (RID, "img_x", 1, json.dumps([{"mac": m, "ports": p} for m, p in ports.items()]), "active", WAVE, "test", db.now_iso()))
    conn.commit()


def test_a_round_that_uses_a_connection_the_wiring_advice_avoids_is_a_warn_in_its_words(room_server):
    conn = fleet_with_baselines(room_server)
    advice = wiring.advice(conn, CLONER1)
    assert advice["enough"] and 4 in advice["avoid"], "התנאי המוקדם: יש בסיס, והחיבור 4 להימנעות"
    add_round_for(conn, {CLONER1: [1, 2, 3, 4], CLONER2: [1, 2, 3]})
    got = dict(zip((CLONER1, CLONER2), gate_machines.firmware(conn, (CLONER1, CLONER2), RID), strict=True))
    assert got[CLONER1].verdict == WARN and advice["text_he"] in got[CLONER1].reason and "avoid_port=4" in got[CLONER1].evidence
    assert got[CLONER2].verdict == OPEN and "wiring=ok" in got[CLONER2].evidence


def test_the_same_advice_does_not_warn_a_round_that_does_not_use_the_avoided_connection(room_server):
    conn = fleet_with_baselines(room_server)
    add_round_for(conn, {CLONER1: [1, 2, 3], CLONER2: [1, 2, 3]})
    got = gate_machines.firmware(conn, (CLONER1,), RID)[0]
    assert got.verdict == OPEN and "wiring=ok" in got.evidence


def test_enough_wiring_data_but_no_round_ports_is_undecided_not_open(room_server):
    conn = fleet_with_baselines(room_server)                          # אין שורת סבב
    got = gate_machines.firmware(conn, (CLONER1,), RID)[0]
    assert got.verdict == UNDECIDED and "wiring=round_ports_unknown" in got.evidence


# --- החיבור לפתיחת סבב אמיתית -----------------------------------------------------------------------------------------


def test_opening_a_real_round_records_a_reading_per_cloner_for_each_of_the_five_gates(room_server):
    ctx, anon, deploy = room_server["ctx"], room_server["anon"], room_server["deploy"]
    for mac, mode, driver, serial in ((CLONER1, "ide", "ata_piix", "S1"), (CLONER2, "ahci", "ahci", "T1")):
        body = hello_body(mac)
        body.update(sata_mode=mode, sata_driver=driver)
        assert anon.post("/api/v1/agent/hello", json=body).status_code == 200
    opened = deploy.post("/api/console/room", json=room_body(room_server, 2, {CLONER1: ["S1"], CLONER2: ["T1"]}))
    assert opened.status_code == 200, opened.text
    assert gates.wait_idle(10.0)           # #3474: the gates run in the background (`gates.start`), after `open_round` returned
    [round_row] = ctx.conn.execute("SELECT id FROM room_rounds").fetchall()
    rows = memory.decisions(ctx.conn, round_id=round_row["id"], limit=200)
    got = {(d["rule"], d["entity_kind"], d["entity_key"]): d for d in rows if d["action"] == "ROUND_GATE" and d["rule"] in
           ("B23", "B52", "B53", "B54", "B56")}
    assert all(d["mode"] == "shadow" for d in got.values())
    # B23: ה-IDE נרשם בשמו; ה-AHCI תקין ומקופל לשורה אחת
    assert got["B23", "machine", CLONER1]["verdict"] == "WARN"
    assert got["B23", "round", round_row["id"]]["verdict"] == "OPEN" and "נבדקו 1 משכפלים" in got["B23", "round", round_row["id"]]["reason"]
    # B52: אין ספירות ואין לולאות, ושני המשכפלים מקופלים
    assert "נבדקו 2 משכפלים" in got["B52", "round", round_row["id"]]["reason"]
    # B53/B54/B56: אין לוג DHCP שנקרא, אין probe, ויש תמונת קושחה אחת בלי הפניה: "לא נמדד", לא "תקין"
    for rule in ("B53", "B54", "B56"):
        for mac in (CLONER1, CLONER2):
            assert got[rule, "machine", mac]["verdict"] == "UNDECIDED", (rule, mac)
