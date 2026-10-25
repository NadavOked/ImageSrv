"""מסך בריאות המערכת — הבדיקות שהיו דורשות טרמינל, עכשיו בקונסולה.

הפקודות (ss, systemctl, HTTP) מוזרקות, כך שכל תרחיש — פורט תפוס על ידי
זר, dnsmasq שנפל, שרת שלא עונה — משוחזר כאן בלי מכונה אמיתית.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from server.health import port_owner

try:
    from fastapi.testclient import TestClient
except ImportError:                                   # pragma: no cover
    TestClient = None

SS_DNSMASQ = """State  Recv-Q Send-Q Local Address:Port Peer Address:Port Process
UNCONN 0      0            0.0.0.0:67        0.0.0.0:*     users:(("dnsmasq",pid=612,fd=4))
UNCONN 0      0            0.0.0.0:69        0.0.0.0:*     users:(("dnsmasq",pid=612,fd=6))
"""

SS_STRANGER = """State  Recv-Q Send-Q Local Address:Port Peer Address:Port Process
UNCONN 0      0            0.0.0.0:67        0.0.0.0:*     users:(("isc-dhcp-server",pid=99,fd=4))
"""


def test_port_owner_reads_ss_output():
    assert port_owner(SS_DNSMASQ, 67) == "dnsmasq"
    assert port_owner(SS_DNSMASQ, 69) == "dnsmasq"
    assert port_owner(SS_DNSMASQ, 68) is None
    assert port_owner(SS_STRANGER, 67) == "isc-dhcp-server"


@pytest.fixture()
def health_server(tmp_path: Path, images_root: Path, clock, monkeypatch):
    """שרת עם בדיקות מזויפות שאפשר לסובב תרחיש-תרחיש."""
    if TestClient is None:
        pytest.skip("fastapi is required")
    from server import users
    from server.app import create_app

    # #1817: pytest's private ancestors (including /root on the VM) are not
    # part of this fixture. Model them as traversable without chmod outside it.
    real_stat = Path.stat
    ancestors = {tmp_path, *tmp_path.parents}

    def fixture_stat(path, *args, **kwargs):
        result = real_stat(path, *args, **kwargs)
        if path in ancestors:
            values = list(result)
            values[0] |= stat.S_IXOTH
            return os.stat_result(values)
        return result

    monkeypatch.setattr(Path, "stat", fixture_stat)
    tftp = tmp_path / "boot-data" / "tftp"
    (tftp / "grub").mkdir(parents=True)
    for directory in (tftp.parent, tftp, tftp / "grub"):
        directory.chmod(0o755)
    for name in ("bootx64.efi", "grubx64.efi", "grub/grub.cfg"):
        (tftp / name).write_bytes(b"x")
        (tftp / name).chmod(0o644)

    fake = {"ss": SS_DNSMASQ, "active": "active", "http": 200, "journal": "",
            "interfaces": [{"name": "eth0", "state": "up", "mac": "aa", "addresses": []}],
            # ‏vmlinuz ו-initrd.img כפי שהתחנה מושכת אותם (#333): קוד
            # וגודל, כי 404 של ‎/boot הוא גוף בן תשעה בייטים (#332).
            "assets": {"vmlinuz": (200, 9_000_000),
                       "initrd.img": (200, 31_000_000)},
            # ריק = לא רץ. ‏None = הבדיקה לא רצה. לא סורקים /proc של
            # המכונה שהטסט רץ עליה (#439).
            "udp_sender_pids": [],
            # ‏#1074: ברירת המחדל כאן — טבלה טעונה, כדי ש-all-green יישאר
            # ירוק. שלושת המצבים נבדקים בנפרד עם subprocess מזויף.
            "nft_ruleset": (
                "table inet imagectl {\n"
                "\tchain input {\n"
                "\t\tiifname \"lo\" accept\n"
                "\t\tct state established,related accept\n"
                "\t}\n"
                "}\n")}
    hooks = {
        "ss": lambda: fake["ss"],
        "nft_ruleset": lambda: fake["nft_ruleset"],
        "unit_active": lambda name: fake["active"],
        "dnsmasq_journal": lambda: fake["journal"],
        "http_get": lambda url: fake["http"],
        "http_size": lambda url: fake["assets"].get(url.rsplit("/", 1)[-1],
                                                    (404, 9)),
        "interfaces": lambda: fake["interfaces"],
        "tftp_root": lambda: tftp,
        "udp_sender_pids": lambda: fake["udp_sender_pids"],
    }
    app = create_app(tmp_path / "data", images_root, "http://10.44.12.10:8080",
                     now_fn=clock, health_hooks=hooks)
    users.create(app.state.ctx.conn, "noc", "admin-pass-123", "admin", by="test", is_builtin=True, check_policy=False)
    users.create(app.state.ctx.conn, "labtech", "deploy-pass-1", "deploy", by="test", check_policy=False)
    admin, deploy = TestClient(app), TestClient(app)
    admin.post("/api/console/login", json={"username": "noc", "password": "admin-pass-123"})
    deploy.post("/api/console/login",
                json={"username": "labtech", "password": "deploy-pass-1"})
    return {"admin": admin, "deploy": deploy, "fake": fake, "tftp": tftp}


def by_id(rows):
    return {r["id"]: r for r in rows}


@pytest.mark.parametrize("active", ["failed", "activating", "inactive"])
@pytest.mark.parametrize("nic", ["enx001122334455", "enp2s0.100"])
def test_1816_dnsmasq_names_the_missing_interface(health_server, active, nic):
    health_server["fake"].update(
        active=active,
        journal=f"dnsmasq: unknown interface {nic}\nFAILED to start up\n")
    response = health_server["admin"].get("/api/console/health")
    assert response.status_code == 200
    row = by_id(response.json())["dnsmasq"]
    assert row["state"] == "bad"
    assert row["detail"] == f"dnsmasq down: unknown interface {nic}"


@pytest.mark.parametrize("latest", [
    "started, version 2.91 cachesize 150\nexiting on receipt of SIGTERM\n",
    "failed to create listening socket: Address already in use\nFAILED to start up\n",
])
def test_1816_dnsmasq_does_not_report_an_old_missing_interface(health_server, latest):
    health_server["fake"].update(
        active="failed", journal="unknown interface old0\nFAILED to start up\n" + latest)
    row = by_id(health_server["admin"].get("/api/console/health").json())["dnsmasq"]
    assert row["state"] == "bad"
    assert "unknown interface" not in row["detail"]


def test_1816_missing_journal_keeps_dnsmasq_red_and_says_not_checked(health_server):
    health_server["fake"].update(active="failed", journal="")
    row = by_id(health_server["admin"].get("/api/console/health").json())["dnsmasq"]
    assert row["state"] == "bad"
    assert "cause not checked" in row["detail"]


def test_1816_recovered_dnsmasq_does_not_read_old_errors(health_server):
    class UnreadJournal(str):
        def splitlines(self):
            pytest.fail("active dnsmasq must not parse the journal")

    health_server["fake"].update(
        active="active", journal=UnreadJournal("unknown interface old0\n"))
    row = by_id(health_server["admin"].get("/api/console/health").json())["dnsmasq"]
    assert row["state"] == "ok"


@pytest.mark.parametrize("failure", ["exit", "timeout", "missing"])
def test_1816_journal_command_failure_discards_partial_output(monkeypatch, failure):
    import subprocess
    from server import health

    def failed_run(cmd, **kwargs):
        assert cmd == ["journalctl", "-b", "-u", "dnsmasq.service", "-t", "dnsmasq",
                       "-n", "20", "--no-pager", "--quiet", "-o", "cat"]
        assert kwargs["check"] is True
        assert kwargs["timeout"] == 5
        if failure == "exit":
            raise subprocess.CalledProcessError(1, cmd, output="unknown interface stale0\n")
        if failure == "timeout":
            raise subprocess.TimeoutExpired(cmd, 5)
        raise FileNotFoundError("journalctl")

    monkeypatch.setattr(health.subprocess, "run", failed_run)
    assert health.default_hooks()["dnsmasq_journal"]() == ""


def test_a_healthy_server_is_all_green(health_server):
    rows = by_id(health_server["admin"].get("/api/console/health").json())
    assert rows["dhcp_port"]["state"] == "ok"
    assert rows["tftp_port"]["state"] == "ok"
    assert rows["dnsmasq"]["state"] == "ok"
    assert rows["boot_files"]["state"] == "ok"
    assert rows["server"]["state"] == "ok"
    assert rows["nics"]["state"] == "ok"
    # DHCP עוד לא הודלק מהקונסולה — וזה נאמר, לא מוסתר.
    assert "לא הודלק" in rows["nics"]["detail"]
    # ‏#1266: SSH יצא מהמוצר — אין לו בדיקת בריאות בכלל.
    assert not any(k.startswith("ssh") for k in rows), rows.keys()
    assert rows["udp_sender"]["state"] == "ok"
    assert rows["firewall"]["state"] == "ok"
    assert "פעילה" in rows["firewall"]["detail"]
    assert "כללים לטבלת imagectl" in rows["firewall"]["detail"]


def test_a_stranger_on_port_67_is_red(health_server):
    health_server["fake"]["ss"] = SS_STRANGER
    rows = by_id(health_server["admin"].get("/api/console/health").json())
    assert rows["dhcp_port"]["state"] == "bad"
    assert "isc-dhcp-server" in rows["dhcp_port"]["detail"]
    assert rows["tftp_port"]["state"] == "bad"          # אף אחד לא מגיש TFTP


def test_a_dead_dnsmasq_and_unreachable_server_are_red(health_server):
    health_server["fake"]["active"] = "failed"
    health_server["fake"]["http"] = None
    rows = by_id(health_server["admin"].get("/api/console/health").json())
    assert rows["dnsmasq"]["state"] == "bad"
    assert rows["server"]["state"] == "bad"


def test_missing_boot_files_are_named(health_server):
    (health_server["tftp"] / "grubx64.efi").unlink()
    rows = by_id(health_server["admin"].get("/api/console/health").json())
    assert rows["boot_files"]["state"] == "bad"
    assert f"חסרים ב-{health_server['tftp']}: grubx64.efi" in rows["boot_files"]["detail"]


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX permission bits")
@pytest.mark.parametrize("name", ["bootx64.efi", "grubx64.efi", "grub/grub.cfg"])
def test_1817_private_boot_file_is_red_with_path_and_mode(health_server, name):
    path = health_server["tftp"] / name
    path.chmod(0o600)
    row = by_id(health_server["admin"].get("/api/console/health").json())["boot_files"]
    assert row["state"] == "bad"
    assert f"{path} (mode 0600)" in row["detail"]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX permission bits")
@pytest.mark.parametrize("level", ["ancestor", "root", "grub"])
def test_1817_private_directory_is_red_with_path_and_mode(health_server, level):
    root = health_server["tftp"]
    path = {"ancestor": root.parent, "root": root, "grub": root / "grub"}[level]
    path.chmod(0o700)
    row = by_id(health_server["admin"].get("/api/console/health").json())["boot_files"]
    assert row["state"] == "bad"
    assert f"{path} (mode 0700)" in row["detail"]
    assert stat.S_IMODE(path.stat().st_mode) == 0o700


def test_1817_stat_failure_is_red_with_path(health_server, monkeypatch):
    path = health_server["tftp"] / "grub/grub.cfg"
    real_stat = Path.stat

    def denied(candidate, *args, **kwargs):
        if candidate == path:
            raise PermissionError("permission probe denied")
        return real_stat(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", denied)
    response = health_server["admin"].get("/api/console/health")
    assert response.status_code == 200
    row = by_id(response.json())["boot_files"]
    assert row["state"] == "bad"
    assert str(path) in row["detail"]
    assert "permission probe denied" in row["detail"]


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX symlinks and modes")
def test_1817_symlink_target_directory_must_be_traversable(health_server):
    root = health_server["tftp"]
    private = root.parent / "private"
    private.mkdir(mode=0o700)
    target = private / "shim.efi"
    target.write_bytes(b"x")
    target.chmod(0o644)
    path = root / "bootx64.efi"
    path.unlink()
    path.symlink_to(target)
    row = by_id(health_server["admin"].get("/api/console/health").json())["boot_files"]
    assert row["state"] == "bad"
    assert f"{private} (mode 0700)" in row["detail"]


# --- #333: שרשרת האתחול אינה נגמרת בשורש ה-TFTP -------------------------
#
# ‏shim, ‏GRUB ו-grub.cfg מביאים את התחנה עד התפריט. משם היא מושכת ב-HTTP
# את ‎/boot/vmlinuz ואת ‎/boot/initrd.img מ-/srv/imagectl/boot, ואת
# **אלה** ‎boot_files לא בדק. בהתקנה נקייה (#332) התיקייה נשארה ריקה,
# שניהם החזירו 404, והשורה הייתה ירוקה בשני המקרים.

def test_a_fresh_server_without_the_initrd_is_red(health_server):
    """זה בדיוק מה שנמדד ב-#332: המתקין סיים "מוכן", התיקייה ריקה.

    ‏shim, ‏GRUB והתפריט **כן** על ה-TFTP — ולכן השורה הייתה ירוקה
    למרות ששום תחנה לא יכולה לעלות. הכרעה: `bad`. שרת שאינו יכול
    להגיש אתחול אינו "אזהרה" — אי אפשר לפרוס ממנו.
    """
    health_server["fake"]["assets"] = {}          # שניהם 404, כמו בהתקנה נקייה
    rows = by_id(health_server["admin"].get("/api/console/health").json())
    assert rows["boot_files"]["state"] == "bad"
    assert "initrd.img" in rows["boot_files"]["detail"]
    assert "vmlinuz" in rows["boot_files"]["detail"]


def test_a_truncated_asset_is_red_although_it_answers_200(health_server):
    """גודל בלבד אינו ראיה, וגם 200 בלבד אינו.

    גוף ה-404 של ‎/boot הוא תשעה בייטים — "יש תשובה ויש גודל" הוא בדיוק
    המצב שנראה תקין ב-#332. קובץ שנקטע באמצע העתקה נראה אותו הדבר.
    """
    health_server["fake"]["assets"] = {"vmlinuz": (200, 9_000_000),
                                       "initrd.img": (200, 9)}
    rows = by_id(health_server["admin"].get("/api/console/health").json())
    assert rows["boot_files"]["state"] == "bad"
    assert "initrd.img" in rows["boot_files"]["detail"]


def test_a_boot_asset_that_could_not_be_probed_is_red_not_ok(health_server):
    """‏"לא הצלחנו לבדוק" אינו "בדקנו, הכל תקין" — עיקרון 5.

    אם בדיקה שלא רצה הייתה מחזירה `ok`, היינו חוזרים בדיוק לבאג: מסך
    ירוק על שרת שאינו יכול להגיש אתחול.

    והשורה גם אומרת **מה** לא רצה. "הקובץ החזיר 200 בלי גודל" על שרת
    שלא ענה בכלל שולח לחפש קובץ פגום במקום שרת שקט.
    """
    health_server["fake"]["assets"] = {"vmlinuz": (None, None),
                                       "initrd.img": (None, None)}
    rows = by_id(health_server["admin"].get("/api/console/health").json())
    assert rows["boot_files"]["state"] == "bad"
    assert rows["boot_files"]["state"] != "ok"
    assert "לא רצה" in rows["boot_files"]["detail"]
    assert "השרת לא ענה" in rows["boot_files"]["detail"]
    assert "200" not in rows["boot_files"]["detail"]


def test_the_green_row_says_the_assets_were_actually_pulled(health_server):
    """הצד החיובי. שורה ירוקה שאינה מזכירה את ה-initrd היא שורה שלא
    בדקה אותו — וזה מה שהיה כאן."""
    rows = by_id(health_server["admin"].get("/api/console/health").json())
    assert rows["boot_files"]["state"] == "ok"
    assert "initrd.img" in rows["boot_files"]["detail"]


def test_where_checks_cannot_run_the_light_is_grey_not_red(health_server):
    """על מכונת פיתוח בלי ss/systemctl המסך לא זועק אדום — הוא אומר
    שאי אפשר לבדוק כאן."""
    health_server["fake"]["ss"] = ""
    health_server["fake"]["active"] = ""
    rows = by_id(health_server["admin"].get("/api/console/health").json())
    assert rows["dhcp_port"]["state"] == "off"
    assert rows["tftp_port"]["state"] == "off"
    assert rows["dnsmasq"]["state"] == "off"


def test_health_is_admin_only(health_server):
    assert health_server["deploy"].get("/api/console/health").status_code == 403


# --- #329: סחיפת shim ---------------------------------------------------
#
# המתקין מעתיק את ה-shim פעם אחת ולא מעדכן. מיקרוסופט דוחפת עדכוני
# SBAT שמבטלים shim ישן, ואז כל הצי מפסיק לעלות ב-PXE בבת אחת.
# שלושת המצבים נפרדים: תואם · ישן · לא ניתן לקרוא. השלישי אינו
# "תקין" — זו בדיקה שלא רצה.

def _shim(server, tmp_path, src_bytes, tftp_bytes):
    from server import health
    src = tmp_path / "shimx64.efi.signed"
    if src_bytes is not None:
        src.write_bytes(src_bytes)
    tftp = server["tftp"]
    tftp.joinpath("bootx64.efi").write_bytes(tftp_bytes)
    # אותם fakes כמו ב-fixture: אף בדיקה אינה נוגעת במכונה שמריצה
    # אותה. ‏hostguard תופס בדיוק את זה (#113).
    hooks = {**health.default_hooks(),
             "ss": lambda: SS_DNSMASQ, "unit_active": lambda n: "active",
             "http_get": lambda u: 200,
             "http_size": lambda u: (200, 31_000_000),
             "interfaces": lambda: [{"name": "eth0", "state": "up",
                                     "mac": "aa", "addresses": []}],
             "tftp_root": lambda: tftp, "shim_src": lambda: src}
    ctx = server["admin"].app.state.ctx
    rows = health.collect(ctx, hooks, "http://x")
    return by_id(rows)["shim_fresh"]


def test_a_shim_that_matches_the_system_copy_is_ok(health_server, tmp_path):
    r = _shim(health_server, tmp_path, b"same", b"same")
    assert r["state"] == "ok"


def test_a_stale_shim_in_tftp_is_reported_bad(health_server, tmp_path):
    """זה התרחיש: apt שדרג את המערכת, והעותק ב-TFTP נשאר מאחור."""
    r = _shim(health_server, tmp_path, b"new-after-sbat-revocation", b"old")
    assert r["state"] == "bad"
    assert "הריצו את המתקין" in r["detail"]


def test_a_shim_that_cannot_be_read_is_unknown_and_not_ok(health_server, tmp_path):
    """‏"לא הצלחנו לבדוק" אינו "בדקנו, תואם" — עיקרון 5.

    אם חוסר-קריאה היה מחזיר `ok`, שרת בלי shim-signed מותקן היה
    מדווח שהשרשרת עדכנית. זה בדיוק הכשל שהבדיקה נועדה למנוע.
    """
    r = _shim(health_server, tmp_path, None, b"whatever")
    assert r["state"] == "unknown"
    assert r["state"] != "ok"


# --- #439: udp-sender חי בלי סבב הוא יתום, לא הצלחה שקטה ---------------
#
# הבדיקה מזהה ומראה. היא אינה הורגת — ההריגה שייכת לנתיב הסגירה.

def test_udp_sender_not_running_is_ok(health_server):
    health_server["fake"]["udp_sender_pids"] = []
    rows = health_server["admin"].get("/api/console/health").json()
    assert any(r["id"] == "udp_sender" and r["state"] == "ok"
               and "לא רץ" in r["detail"] for r in rows)


def test_udp_sender_during_a_round_is_a_warning(health_server):
    from server.sender import SendState
    ctx = health_server["admin"].app.state.ctx
    ctx.sender._state = SendState("ses_1", "img_7f3a91")
    ctx.sender._state.state = "sending"
    health_server["fake"]["udp_sender_pids"] = [4321]
    rows = health_server["admin"].get("/api/console/health").json()
    assert any(r["id"] == "udp_sender" and r["state"] == "warn"
               and "4321" in r["detail"] and "סבב" in r["detail"]
               for r in rows)


def test_an_orphan_udp_sender_is_red(health_server):
    health_server["fake"]["udp_sender_pids"] = [4321]
    rows = health_server["admin"].get("/api/console/health").json()
    assert any(r["id"] == "udp_sender" and r["state"] == "bad"
               and "4321" in r["detail"] and "יתום" in r["detail"]
               for r in rows)


def test_a_udp_sender_check_that_could_not_run_is_not_ok(health_server):
    """‏"לא הצלחנו לבדוק" אינו "לא רץ" — ו"לא רץ" הוא הירוק (#439)."""
    health_server["fake"]["udp_sender_pids"] = None
    rows = health_server["admin"].get("/api/console/health").json()
    assert any(r["id"] == "udp_sender" and r["state"] != "ok"
               and "לא הצלחנו לבדוק" in r["detail"] for r in rows)


def test_firewall_loaded_is_ok_with_rule_count(health_server):
    rows = by_id(health_server["admin"].get("/api/console/health").json())
    assert rows["firewall"]["state"] == "ok"
    assert rows["firewall"]["detail"].startswith("פעילה, ")
    assert "כללים לטבלת imagectl" in rows["firewall"]["detail"]


def test_firewall_missing_table_is_not_loaded(health_server):
    health_server["fake"]["nft_ruleset"] = "table inet filter {\n}\n"
    rows = by_id(health_server["admin"].get("/api/console/health").json())
    assert rows["firewall"]["state"] == "off"
    assert rows["firewall"]["detail"] == "לא נטענה"


def test_firewall_unread_is_unknown_not_ok(health_server):
    """None מה-hook = nft נכשל/חסר — לא הצלחנו לבדוק, לא 'לא נטענה'."""
    health_server["fake"]["nft_ruleset"] = None
    rows = by_id(health_server["admin"].get("/api/console/health").json())
    assert rows["firewall"]["state"] == "unknown"
    assert rows["firewall"]["detail"] == "לא הצלחנו לבדוק"
    assert rows["firewall"]["state"] != "ok"


def test_read_nft_ruleset_subprocess_three_outcomes(monkeypatch):
    """שלושת המצבים מול subprocess מזויף — לא מול nft אמיתי."""
    import subprocess as sp
    from server import ports

    def missing(*a, **k):
        raise FileNotFoundError("nft")
    monkeypatch.setattr(ports.subprocess, "run", missing)
    assert ports.read_nft_ruleset() is None
    assert ports.firewall_status(None) == ("unknown", "לא הצלחנו לבדוק")

    def failed(*a, **k):
        return sp.CompletedProcess(["nft"], 1, stdout="", stderr="Permission denied")
    monkeypatch.setattr(ports.subprocess, "run", failed)
    assert ports.read_nft_ruleset() is None

    def empty_ok(*a, **k):
        return sp.CompletedProcess(["nft"], 0, stdout="", stderr="")
    monkeypatch.setattr(ports.subprocess, "run", empty_ok)
    assert ports.read_nft_ruleset() == ""
    assert ports.firewall_status("") == ("off", "לא נטענה")

    loaded = "table inet imagectl {\n\tchain input {\n\t\tiif lo accept\n\t}\n}\n"
    def has_table(*a, **k):
        return sp.CompletedProcess(["nft"], 0, stdout=loaded, stderr="")
    monkeypatch.setattr(ports.subprocess, "run", has_table)
    assert "table inet imagectl" in ports.read_nft_ruleset()
    state, detail = ports.firewall_status(loaded)
    assert state == "ok"
    assert detail == "פעילה, 1 כללים לטבלת imagectl"
