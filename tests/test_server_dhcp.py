"""DHCP לכל כרטיס רשת — ההגדרה המסוכנת ביותר במערכת (אפיון סעיף 24).

הבדיקות כאן הן על שכבות הבטיחות לפני שהן על התכונה: כבוי כברירת מחדל,
אישור בשם הממשק, זיהוי DHCP קיים, ואישור נוסף ל-trunk. הקובץ שנכתב
ל-dnsmasq נבדק כטקסט, וההחלה עצמה (כתיבה + restart) מוחלפת ב-hook.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from server.net import dhcp
from server.net.dhcp import InterfaceConfig, render, render_proxy, validate

try:
    from fastapi.testclient import TestClient
except ImportError:                                   # pragma: no cover
    TestClient = None


GOOD = dict(
    enabled=True, range_start="10.44.9.50", range_end="10.44.9.200",
    netmask="255.255.255.0", gateway="10.44.9.1", dns=["10.44.0.5"],
    lease="12h", server_ip="10.44.9.10",
)


# --- הלוגיקה הטהורה ----------------------------------------------------------


def test_a_disabled_interface_needs_no_fields():
    validate(InterfaceConfig("eth0"))


@pytest.mark.parametrize(
    ("change", "fragment"),
    [
        ({"range_start": "10.44.9.300"}, "תחילת הטווח"),
        ({"range_end": "10.44.9.20"}, "גדולה מסופו"),
        ({"range_end": "10.44.10.200"}, "מחוץ לרשת"),
        ({"server_ip": "10.44.9.100"}, "בתוך הטווח"),
        ({"server_ip": "10.44.8.10"}, "אינה ברשת"),
        ({"gateway": "10.44.8.1"}, "השער"),
        ({"lease": "soon"}, "חכירה"),
    ],
)
def test_bad_configs_are_refused_in_hebrew(change, fragment):
    with pytest.raises(ValueError) as err:
        validate(InterfaceConfig("eth0", **{**GOOD, **change}))
    assert fragment in str(err.value)


MIXED = [
    InterfaceConfig("eth0", **GOOD),
    InterfaceConfig("eth1.101", proxy=True, server_ip="10.44.101.10"),
    InterfaceConfig("eth2"),                           # כבוי — לא מופיע
]


def test_render_puts_each_interface_under_its_own_tag():
    text = render(MIXED)
    assert "interface=eth0" in text
    assert "interface=eth2" not in text
    assert "dhcp-range=set:if-eth0,10.44.9.50,10.44.9.200,255.255.255.0,12h" in text
    assert "option:router,10.44.9.1" in text
    assert "dhcp-boot=tag:if-eth0,tag:efi-x86_64,tag:known,bootx64.efi,,10.44.9.10" in text
    assert "bind-interfaces" in text


def test_the_main_instance_never_touches_a_proxy_interface():
    """‏#36: ממשק ב-proxy מקפיא את dnsmasq 2.91 על בקשת PXE:4011. הוא רץ
    באינסטנס נפרד, ולכן אסור שיופיע בקובץ הראשי בשום צורה — לא כטווח,
    לא כ-pxe-service, ואפילו לא כ-interface להגשת TFTP: שני התהליכים
    היו נלחמים על אותם פורטים. except-interface מוציא אותו מפורשות."""
    text = render(MIXED)
    live = [ln for ln in text.splitlines() if ln and not ln.startswith("#")]
    assert not any("proxy" in ln for ln in live)
    assert not any("pxe-service" in ln for ln in live)
    assert not any(ln == "interface=eth1.101" for ln in live)
    assert "except-interface=eth1.101" in live


def test_a_proxy_only_setup_still_leaves_the_main_instance_serving_tftp():
    """כשאין DHCP מלא בכלל, האינסטנס הראשי נשאר TFTP על כל הכרטיסים
    חוץ מזה של ה-proxy — אין לו טווח, אז הוא לא מחלק כלום."""
    text = render([InterfaceConfig("eth1.101", proxy=True, server_ip="10.44.101.10")])
    assert "bind-interfaces" in text and "except-interface=eth1.101" in text
    assert "dhcp-range" not in text


def test_the_proxy_file_is_a_standalone_instance():
    """קובץ ה-proxy אינו ב-/etc/dnsmasq.d — הוא נטען ביחידה משלו, ולכן
    חייב להביא איתו הכל: בלי DNS, ‏TFTP משלו, ו-leasefile נפרד כדי ששני
    התהליכים לא יכתבו לאותו קובץ חכירות."""
    text = render_proxy(MIXED)
    assert "port=0" in text and "bind-interfaces" in text
    assert "interface=eth1.101" in text
    assert "interface=eth0" not in text                # ה-DHCP המלא נשאר בראשי
    assert "enable-tftp" in text and "tftp-root=/srv/tftp" in text
    assert "dhcp-leasefile=" in text
    assert "dhcp-range=set:if-eth1.101,10.44.101.10,proxy" in text
    assert "option:router" not in text


def test_the_proxy_file_is_empty_when_no_interface_is_in_proxy_mode():
    text = render_proxy([InterfaceConfig("eth0", **GOOD), InterfaceConfig("eth2")])
    assert "interface=" not in text and "dhcp-range" not in text


def test_proxy_answers_legacy_bios_as_well_as_uefi():
    """‏#40: בענף ה-proxy הייתה תשובת PXE ל-UEFI בלבד. מחשבי השיכפול הם
    Legacy BIOS ‏(#38) — בלי שורת x86PC הם לא מקבלים תשובה כלל."""
    text = render_proxy(MIXED)
    assert 'pxe-service=tag:if-eth1.101,tag:bios,x86PC,"ImageSrv",grub/i386-pc/core.0' in text
    assert ('pxe-service=tag:if-eth1.101,tag:efi-x86_64,x86-64_EFI,"ImageSrv",bootx64.efi'
            in text)
    assert "dhcp-match=set:bios,option:client-arch,0" in text


def test_proxy_lets_dnsmasq_answer_with_its_own_address():
    """‏#37: ב-pxe-service אין שדה כתובת שרת — ואסור שיהיה. בלעדיו dnsmasq
    מגיש מה-TFTP שלו ושם ב-siaddr את כתובתו על הממשק שענה, וזו הכתובת
    שהתחנה באמת יכולה להגיע אליה. משם GRUB לוקח את net_default_server."""
    text = render_proxy([InterfaceConfig("eth1.101", proxy=True, server_ip="10.44.101.10")])
    lines = [ln for ln in text.splitlines() if ln.startswith("pxe-service=")]
    assert len(lines) == 2
    for line in lines:
        assert line.endswith(("bootx64.efi", "grub/i386-pc/core.0")), (
            f"נוספה כתובת שרת מפורשת ל-pxe-service, וזה מקבע וילן אחד: {line}"
        )


def test_render_splits_the_boot_loader_by_client_arch():
    """‏#38: מחשבי השיכפול הם Legacy BIOS — אופציה 93 בוחרת את הטוען.
    ‏BIOS ‏(arch 0) מקבל GRUB i386-pc, ‏UEFI ‏(7/9) את ה-shim החתום,
    באותה רשת. שניהם ממשיכים לאותו grub.cfg."""
    text = render([InterfaceConfig("eth0", **GOOD)])
    assert "dhcp-match=set:bios,option:client-arch,0" in text
    assert "dhcp-match=set:efi-x86_64,option:client-arch,7" in text
    assert "dhcp-boot=tag:if-eth0,tag:bios,tag:known,grub/i386-pc/core.0,,10.44.9.10" in text
    assert "dhcp-boot=tag:if-eth0,tag:efi-x86_64,tag:known,bootx64.efi,,10.44.9.10" in text
    # אין שורת boot חסרת-תג שתתפוס קושחות לא מזוהות — ברירת המחדל
    # למי שאינו מוכר היא כלום, לא טוען שגוי.
    assert "dhcp-boot=tag:if-eth0,bootx64.efi" not in text


def test_dhcp_boot_requires_the_known_tag():
    """‏#141: מכונה שאינה ב-KNOWN_MACS_CONF לא מקבלת dhcp-boot בכלל —
    לא רק ערך GRUB בלי ImageSrv. ‏tag:known חייב להופיע על **כל** שורת
    boot, לא רק חלק מהן (כשל חלקי כאן משאיר מסלול פתוח ל-arch אחד)."""
    text = render([InterfaceConfig("eth0", **GOOD)])
    boot_lines = [ln for ln in text.splitlines() if ln.startswith("dhcp-boot=")]
    assert boot_lines, "לא נמצאה אף שורת dhcp-boot לבדוק"
    assert all(",tag:known," in ln for ln in boot_lines)


def test_render_with_nothing_enabled_is_an_empty_comment():
    text = render([InterfaceConfig("eth0"), InterfaceConfig("eth1")])
    assert "interface=" not in text and "dhcp-range" not in text


# --- ‏#1013: TFTP הוא מתג, והוא בקובץ שהשרת מרנדר -----------------------------


def _active(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if ln and not ln.startswith("#")]


def test_render_writes_tftp_even_when_no_interface_has_dhcp():
    """הגדרת ה"גמור" של #1013: התקנה טרייה — כרטיס הפצה מהמתקין, אף DHCP
    עדיין — חייבת TFTP, כי המתקין אינו כותב עוד enable-tftp. לכן השורות
    יושבות **לפני** היציאה המוקדמת."""
    text = render([], tftp_root="/srv/tftp")
    assert "enable-tftp" in _active(text) and "tftp-root=/srv/tftp" in _active(text)
    assert "dhcp-range" not in text
    # ועם DHCP — פעם אחת, לא פעמיים
    text = render([InterfaceConfig("eth0", **GOOD)], tftp_root="/srv/tftp")
    assert _active(text).count("enable-tftp") == 1 and "dhcp-range=set:if-eth0" in text


def test_render_without_tftp_root_is_tftp_off_and_says_so():
    """‏`tftp_root=None` = המפעיל כיבה 69: אין שורה פעילה, ויש הערה שאומרת
    שזה כיבוי מכוון — מי שקורא את הקובץ על השרת לא יחשוב ששורה נשמטה."""
    for configs in ([], [InterfaceConfig("eth0", **GOOD)]):
        text = render(configs, tftp_root=None)
        assert "enable-tftp" not in _active(text)
        assert not any(ln.startswith("tftp-root=") for ln in _active(text))
        assert "TFTP off" in text


def test_tftp_root_comes_from_the_install_not_a_constant():
    text = render([], tftp_root="/data/tftp")
    assert "tftp-root=/data/tftp" in _active(text)


def test_the_proxy_instance_follows_the_same_tftp_switch():
    """אחרת המתג היה "כבוי אבל מאזין": אינסטנס ה-proxy מאזין על 69 בכרטיסים
    שלו, ו-ss היה מראה dnsmasq על 69 אחרי שהמפעיל כיבה."""
    proxy = [InterfaceConfig("eth1.101", proxy=True, server_ip="10.44.101.10")]
    assert "enable-tftp" in _active(render_proxy(proxy))              # ברירת המחדל: דלוק
    off = render_proxy(proxy, tftp_root=None)
    assert "enable-tftp" not in _active(off) and "TFTP off" in off
    assert "pxe-service" in off                                       # ה-proxy עצמו נשאר


def test_installer_serves_tftp_reads_only_an_active_line():
    """קובץ המתקין הישן (עד v0.47.5) נושא `enable-tftp` — ואז אין מתג.
    הערה אינה שורה פעילה; ריק/None אינם "כן"."""
    old = "port=0\ninterface=eth1\nbind-interfaces\n\nenable-tftp\ntftp-root=/srv/tftp\n"
    assert dhcp.installer_serves_tftp(old) is True
    assert dhcp.installer_serves_tftp("  enable-tftp   # kept\n") is True
    new = "port=0\ninterface=eth1\n# enable-tftp moved to imagesrv-dhcp.conf (#1013)\n"
    assert dhcp.installer_serves_tftp(new) is False
    assert dhcp.installer_serves_tftp("enable-tftp-secure\n") is False
    assert dhcp.installer_serves_tftp("") is False
    assert dhcp.installer_serves_tftp(None) is False


def test_read_installer_conf_has_three_states(tmp_path):
    """עיקרון 5: "אין קובץ" (ראיה חיובית) ≠ "לא נקרא" (לא הצלחנו לבדוק)."""
    missing = tmp_path / "imagesrv.conf"
    assert dhcp.read_installer_conf(missing) == ""
    missing.write_text("enable-tftp\n", encoding="utf-8")
    assert dhcp.read_installer_conf(missing) == "enable-tftp\n"
    assert dhcp.read_installer_conf(tmp_path) is None          # תיקייה — OSError שאינו "אין"


def test_list_interfaces_reads_sysfs_and_skips_loopback(tmp_path):
    for name, state in (("lo", "unknown"), ("eth0", "up"), ("eth1", "down")):
        d = tmp_path / name
        d.mkdir()
        (d / "operstate").write_text(state)
        (d / "address").write_text("b4:2e:99:07:1a:c4\n")
        (d / "flags").write_text("0x1003\n" if name == "eth0" else "0x1002\n")
        (d / "carrier").write_text("1\n" if name == "eth0" else "0\n")
    found = dhcp.list_interfaces(tmp_path)
    assert [i["name"] for i in found] == ["eth0", "eth1"]
    assert found[0]["state"] == "up" and found[0]["mac"] == "b4:2e:99:07:1a:c4"
    assert found[0]["admin_up"] is True and found[0]["link_up"] is True
    assert found[1]["admin_up"] is False and found[1]["link_up"] is False


def test_apply_reports_an_unwritable_path_instead_of_raising(tmp_path):
    # קובץ בשם התיקייה → הכתיבה נכשלת → הודעה, לא חריגה.
    blocker = tmp_path / "blocked"
    blocker.write_text("")
    assert dhcp.apply("x", blocker / "conf") is not None
    # כתיבה שנכשלת חוזרת לפני כל systemctl — בדיקות לא נוגעות במכונה.
    assert dhcp.apply_proxy("x", True, blocker / "conf") is not None


def test_a_probe_result_is_never_falsey():
    """‏#53: כל עוד "לא בדקנו" ו"בדקנו ושקט" הם שניהם falsey, `if found:`
    מקפל אותם לאחד — ומדליק DHCP על רשת המכללה כשהבדיקה רק נכשלה. תוצאה
    אמיתית תמיד מפילה `if` מקרי לצד החוסם."""
    assert dhcp.ProbeResult(False)
    assert dhcp.ProbeResult(True)
    assert dhcp.ProbeResult(False).checked is False
    assert dhcp.ProbeResult(False).servers == ()


def test_a_probe_that_cannot_open_the_socket_says_so_instead_of_reporting_silence():
    """בלי root, בלי SO_BINDTODEVICE (ווינדוס) או על ממשק שאינו קיים —
    הסוקט לא נפתח. התשובה היא "לא נבדק", לא "שקט"."""
    result = dhcp.probe_existing_dhcp("imagesrv-no-such-nic0", timeout=0.1)
    assert result.checked is False and result.servers == ()


def test_the_proxy_conf_lives_outside_the_directory_dnsmasq_reads():
    """‏#36 כולו תלוי בזה: קובץ ב-/etc/dnsmasq.d נטען לאינסטנס הראשי,
    וההפרדה מתבטלת בשקט."""
    assert "/etc/dnsmasq.d" not in dhcp.PROXY_CONF
    assert dhcp.DEFAULT_CONF.startswith("/etc/dnsmasq.d")


# --- #141: רשימת המכונות הרשומות (dhcp-hostsfile) ----------------------------


def test_known_macs_conf_is_not_auto_loaded_from_dnsmasq_d():
    """כמו PROXY_CONF (#36): הקובץ נטען במפורש דרך dhcp-hostsfile
    ב-imagesrv.conf, לא דרך הסריקה האוטומטית של /etc/dnsmasq.d."""
    assert "/etc/dnsmasq.d" not in dhcp.KNOWN_MACS_CONF


def test_render_known_macs_lists_one_line_per_registered_mac():
    text = dhcp.render_known_macs(["aa:bb:cc:dd:ee:01", "aa:bb:cc:dd:ee:02"])
    assert "aa:bb:cc:dd:ee:01,set:known" in text
    assert "aa:bb:cc:dd:ee:02,set:known" in text
    assert text.count(",set:known") == 2


def test_render_known_macs_on_an_empty_registry_is_a_valid_empty_file():
    """אף מכונה רשומה = קובץ תקין בלי אף שורת MAC — dnsmasq טוען אותו
    בלי להתלונן, ואף tag:known לא נדלק (עיקרון 1: מצב לא ברור/ריק = כלום)."""
    text = dhcp.render_known_macs([])
    assert ",set:known" not in text
    assert text.strip() != ""            # עדיין קובץ עם כותרת, לא ריק לגמרי


def test_render_known_macs_refuses_a_non_canonical_mac():
    """שורה שאינה MAC קנוני נכתבת בלי בריחה לקובץ ש-dnsmasq קורא כ-root
    (אותה סכנה בדיוק כמו שם כרטיס, #102) — השער חוסם לפני הכתיבה."""
    with pytest.raises(ValueError) as err:
        dhcp.render_known_macs(["aa:bb:cc:dd:ee:01\ndhcp-range=10.0.0.1,10.0.0.9"])
    assert "MAC לא קנוני" in str(err.value)


def test_apply_known_macs_writes_then_reloads_not_restarts(tmp_path, monkeypatch):
    """‏#141: reload (SIGHUP) ולא restart — #36 הוא התקדים למה restart על
    הגדרה הזו אסור. הבדיקה מוודאת גם את הכתיבה בפועל וגם את שם הפעולה."""
    from server.net import dhcp_host

    calls = []
    monkeypatch.setattr(
        dhcp_host, "_systemctl",
        lambda action, unit: calls.append((action, unit)) or None,
    )
    conf = tmp_path / "known-macs"
    error = dhcp.apply_known_macs("aa:bb:cc:dd:ee:01,set:known\n", conf)
    assert error is None
    assert conf.read_text(encoding="utf-8") == "aa:bb:cc:dd:ee:01,set:known\n"
    assert calls == [("reload", "dnsmasq")]


# --- דרך הקונסולה ------------------------------------------------------------


@pytest.fixture()
def dhcp_server(tmp_path: Path, images_root: Path, clock):
    """שרת עם שני כרטיסים מזויפים, גלאי DHCP מזויף, והחלה שרק רושמת."""
    if TestClient is None:
        pytest.skip("fastapi is required")
    from server.console import users
    from server.app import create_app

    fake = {
        "interfaces": [
            {"name": "eth0", "state": "up", "mac": "aa:aa:aa:aa:aa:00", "addresses": ["10.44.9.10/24"]},
            {"name": "eth1", "state": "up", "mac": "aa:aa:aa:aa:aa:01", "addresses": ["10.44.1.10/24"]},
        ],
        "existing": [],                 # מה ה-probe "רואה"
        "probe_checked": True,          # האם הבדיקה בכלל הצליחה לרוץ (#53)
        "applied": [],                  # מה נכתב לאינסטנס הראשי
        "proxy_applied": [],            # מה נכתב לאינסטנס ה-proxy: (טקסט, פעיל)
        "apply_error": None,
        "proxy_error": None,
        # ‏#36: הגרסה שה-API "רואה". אף בדיקה לא מריצה dnsmasq אמיתי,
        # ובברירת המחדל זו הגרסה של המעבדה — זו שהקפיאה שוחזרה בה.
        "dnsmasq_version": "Dnsmasq version 2.91  Copyright (c) 2000-2024\n",
        # ‏#762: אמת חיה ל-DHCP. ‏conf_readable/service_readable שולטים אם
        # הקריאה "מצליחה" בכלל — ברירת המחדל היא הצלחה, כדי שהבדיקות
        # הקיימות (שלא עוסקות ב-#762) לא ייפגעו.
        "active_conf": "",
        "service_active": True,
        "conf_readable": True,
        "service_readable": True,
    }
    hooks = {
        "interfaces": lambda: fake["interfaces"],
        "dnsmasq_version": lambda: fake["dnsmasq_version"],
        # ה-probe המזויף חייב להיות מסוגל להחזיר גם "לא הצלחתי לבדוק" —
        # זה המצב שלא היה כאן, ולכן #53 לא נתפס.
        "probe": lambda name: dhcp.ProbeResult(
            fake["probe_checked"], tuple(fake["existing"])),
        "apply": lambda text: (fake["applied"].append(text), fake["apply_error"])[1],
        "apply_proxy": lambda text, active: (
            fake["proxy_applied"].append((text, active)), fake["proxy_error"])[1],
        "read_active_conf": lambda: (
            fake["active_conf"] if fake["conf_readable"] else None),
        "service_active": lambda unit: (
            fake["service_active"] if fake["service_readable"] else None),
    }
    app = create_app(tmp_path / "data", images_root, "http://10.44.12.10:8080",
                     now_fn=clock, dhcp_hooks=hooks,
                     nic_hooks={"firewall": lambda nic: None})  # #1829: no host firewall in tests
    users.create(app.state.ctx.conn, "noc", "admin-pass-123", "admin", by="test", is_builtin=True, check_policy=False)
    users.create(app.state.ctx.conn, "labtech", "deploy-pass-1", "deploy", by="test", check_policy=False)
    admin = TestClient(app)
    admin.post("/api/console/login", json={"username": "noc", "password": "admin-pass-123"})
    deploy = TestClient(app)
    deploy.post("/api/console/login", json={"username": "labtech", "password": "deploy-pass-1"})
    return {"admin": admin, "deploy": deploy, "fake": fake, "ctx": app.state.ctx}


def test_a_nic_gets_a_description_and_can_be_forgotten(dhcp_server):
    """שורת כרטיס כמו כל שורה: תיאור חופשי (למשל וילן), והסרה שמאפסת."""
    admin, fake = dhcp_server["admin"], dhcp_server["fake"]

    assert admin.put("/api/console/net/interfaces/eth0/description",
                     json={"description": "700"}).status_code == 200
    rows = {r["name"]: r for r in admin.get("/api/console/net/interfaces").json()}
    assert rows["eth0"]["description"] == "700"
    assert rows["eth1"]["description"] == ""

    # מדליקים DHCP, ואז מסירים — הכרטיס חוזר לכבוי ו-dnsmasq מתעדכן.
    assert admin.put("/api/console/net/interfaces/eth0",
                     json={**GOOD, "confirm": "eth0"}).status_code == 200
    applied_before = len(fake["applied"])
    assert admin.delete("/api/console/net/interfaces/eth0").json()["ok"] is True
    rows = {r["name"]: r for r in admin.get("/api/console/net/interfaces").json()}
    assert rows["eth0"]["enabled"] is False and rows["eth0"]["description"] == ""
    assert len(fake["applied"]) == applied_before + 1
    assert "interface=eth0" not in fake["applied"][-1]

    # שתי הפעולות — admin בלבד.
    deploy = dhcp_server["deploy"]
    assert deploy.put("/api/console/net/interfaces/eth0/description",
                      json={"description": "x"}).status_code == 403
    assert deploy.delete("/api/console/net/interfaces/eth0").status_code == 403


def test_a_vlan_subinterface_can_be_added_by_hand(dhcp_server):
    """כרטיס שעוד לא קיים במכונה נוסף ידנית, מופיע כבוי ומסומן חסר."""
    admin = dhcp_server["admin"]
    assert admin.post("/api/console/net/interfaces",
                      json={"name": "eth1.700", "description": "וילן 700"},
                      ).status_code == 200
    rows = {r["name"]: r for r in admin.get("/api/console/net/interfaces").json()}
    row = rows["eth1.700"]
    assert row["enabled"] is False and row["present"] is False
    assert row["description"] == "וילן 700"

    assert admin.post("/api/console/net/interfaces",
                      json={"name": "eth1.700"}).status_code == 409
    assert admin.post("/api/console/net/interfaces",
                      json={"name": "לא באנגלית"}).status_code == 400

    # כרטיס חי שעוד לא הוגדר — "הוספה" קולטת אותו: תצורה כבויה + תיאור.
    assert admin.post("/api/console/net/interfaces",
                      json={"name": "eth1", "description": "רשת המכללה"},
                      ).status_code == 200
    rows = {r["name"]: r for r in admin.get("/api/console/net/interfaces").json()}
    assert rows["eth1"]["present"] is True
    assert rows["eth1"]["description"] == "רשת המכללה"
    assert dhcp_server["deploy"].post("/api/console/net/interfaces",
                                      json={"name": "eth9"}).status_code == 403


def test_every_interface_starts_off(dhcp_server):
    rows = dhcp_server["admin"].get("/api/console/net/interfaces").json()
    assert [r["name"] for r in rows] == ["eth0", "eth1"]
    assert all(r["enabled"] is False and r["proxy"] is False for r in rows)
    assert rows[0]["addresses"] == ["10.44.9.10/24"] and rows[0]["state"] == "up"


def test_turning_on_needs_the_interface_name_typed(dhcp_server):
    admin = dhcp_server["admin"]
    r = admin.put("/api/console/net/interfaces/eth0", json=GOOD)
    assert r.status_code == 409 and "eth0" in r.json()["detail"]
    r = admin.put("/api/console/net/interfaces/eth0", json={**GOOD, "confirm": "eth1"})
    assert r.status_code == 409
    assert dhcp_server["fake"]["applied"] == []     # שום דבר לא נכתב

    r = admin.put("/api/console/net/interfaces/eth0", json={**GOOD, "confirm": "eth0"})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    assert "interface=eth0" in dhcp_server["fake"]["applied"][-1]
    rows = admin.get("/api/console/net/interfaces").json()
    assert rows[0]["enabled"] is True and rows[0]["range_start"] == "10.44.9.50"


def test_an_existing_dhcp_server_blocks_the_switch(dhcp_server):
    """הסיכון מנספח ב': DHCP שני על רשת שכבר יש בה אחד."""
    dhcp_server["fake"]["existing"] = ["10.44.9.1"]
    admin = dhcp_server["admin"]
    r = admin.put("/api/console/net/interfaces/eth0", json={**GOOD, "confirm": "eth0"})
    assert r.status_code == 409 and "10.44.9.1" in r.json()["detail"]
    assert dhcp_server["fake"]["applied"] == []
    # מי שיודע מה הוא עושה יכול לעקוף — במפורש.
    r = admin.put("/api/console/net/interfaces/eth0",
                  json={**GOOD, "confirm": "eth0", "ignore_existing": True})
    assert r.status_code == 200


def test_a_check_that_could_not_run_blocks_the_switch_too(dhcp_server):
    """‏#53 — הבאג עצמו: `None` (לא הצלחנו לבדוק) נספר כ"נקי".

    ‏None חוזר בדיוק כשאין הרשאות או כשפורט 68 תפוס — המצב הרגיל של
    כרטיס trunk שמקבל כתובת מרשת המכללה. כלומר: על הכרטיס המסוכן ביותר,
    שכבת הבטיחות שאמורה לעצור פשוט לא רצה, ודיווחה "נקי".
    """
    dhcp_server["fake"]["probe_checked"] = False
    admin = dhcp_server["admin"]
    r = admin.put("/api/console/net/interfaces/eth0", json={**GOOD, "confirm": "eth0"})
    assert r.status_code == 409, "בדיקה שלא רצה פתחה את ההדלקה"
    assert dhcp_server["fake"]["applied"] == []      # שום דבר לא הגיע ל-dnsmasq

    # ההודעה חייבת להבדיל: מפעיל שיקרא "נמצא שרת DHCP" יחפש שרת שאינו קיים.
    detail = r.json()["detail"]
    assert "לא ניתן לבדוק" in detail
    assert "נמצא שרת DHCP פעיל" not in detail
    assert "ignore_existing" in detail               # מה עושים מכאן


def test_an_unrunnable_check_is_still_overridable_on_purpose(dhcp_server):
    """המעקף המפורש נשאר מעקף — מי שיודע שהכרטיס מבודד מדליק בכל זאת,
    ולא נתקע בלי דרך קדימה."""
    dhcp_server["fake"]["probe_checked"] = False
    r = dhcp_server["admin"].put(
        "/api/console/net/interfaces/eth0",
        json={**GOOD, "confirm": "eth0", "ignore_existing": True})
    assert r.status_code == 200, r.text
    assert "interface=eth0" in dhcp_server["fake"]["applied"][-1]


def test_the_probe_endpoint_tells_the_three_states_apart(dhcp_server):
    """אותה הבחנה גם בדיווח לקונסולה: "לא נבדק" אינו "נקי"."""
    admin, fake = dhcp_server["admin"], dhcp_server["fake"]
    assert admin.get("/api/console/net/interfaces/eth0/probe").json() == {
        "interface": "eth0", "checked": True, "servers": []}

    fake["existing"] = ["10.44.9.1"]
    assert admin.get("/api/console/net/interfaces/eth0/probe").json()["servers"] \
        == ["10.44.9.1"]

    fake["existing"], fake["probe_checked"] = [], False
    body = admin.get("/api/console/net/interfaces/eth0/probe").json()
    assert body["checked"] is False and body["servers"] == []


def test_the_trunk_needs_a_second_confirmation(dhcp_server):
    admin = dhcp_server["admin"]
    # סימון trunk לבד (כבוי) לא דורש אישור — הוא רק מסמן.
    r = admin.put("/api/console/net/interfaces/eth1", json={"trunk": True})
    assert r.status_code == 200
    assert admin.get("/api/console/net/interfaces").json()[1]["trunk"] is True

    body = {**GOOD, "range_start": "10.44.1.50", "range_end": "10.44.1.200",
            "gateway": "10.44.1.1", "server_ip": "10.44.1.10", "confirm": "eth1"}
    r = admin.put("/api/console/net/interfaces/eth1", json=body)
    assert r.status_code == 409 and "confirm_trunk" in r.json()["detail"]
    r = admin.put("/api/console/net/interfaces/eth1", json={**body, "confirm_trunk": True})
    assert r.status_code == 200


def test_proxy_mode_does_not_hand_out_addresses(dhcp_server, classrooms_on):
    admin, fake = dhcp_server["admin"], dhcp_server["fake"]
    r = admin.put("/api/console/net/interfaces/eth1",
                  json={"proxy": True, "server_ip": "10.44.1.10",
                        "confirm": "eth1", "confirm_proxy_broken": True})
    assert r.status_code == 200, r.text
    text, active = fake["proxy_applied"][-1]
    assert active is True
    assert "dhcp-range=set:if-eth1,10.44.1.10,proxy" in text
    assert "option:router" not in text


def test_proxy_runs_in_its_own_instance_so_a_freeze_spares_the_main_dhcp(dhcp_server, classrooms_on):
    """‏#36: ה-DHCP של וילן ההפצה וה-proxy הם שני תהליכים. הקובץ הראשי
    לא מכיל את ממשק ה-proxy, וכיבוי ה-proxy עוצר את היחידה שלו."""
    admin, fake = dhcp_server["admin"], dhcp_server["fake"]
    assert admin.put("/api/console/net/interfaces/eth0",
                     json={**GOOD, "confirm": "eth0"}).status_code == 200
    assert admin.put("/api/console/net/interfaces/eth1",
                     json={"proxy": True, "server_ip": "10.44.1.10",
                           "confirm": "eth1",
                           "confirm_proxy_broken": True}).status_code == 200

    main_text = fake["applied"][-1]
    assert "dhcp-range=set:if-eth0," in main_text      # ההפצה ממשיכה לחלק
    assert "except-interface=eth1" in main_text
    assert "proxy" not in "".join(
        ln for ln in main_text.splitlines() if not ln.startswith("#"))
    assert "interface=eth1" in fake["proxy_applied"][-1][0]

    # כיבוי ה-proxy: היחידה נעצרת, ההפצה לא זזה.
    assert admin.put("/api/console/net/interfaces/eth1",
                     json={"proxy": False}).status_code == 200
    assert fake["proxy_applied"][-1][1] is False
    assert "dhcp-range=set:if-eth0," in fake["applied"][-1]


def test_a_proxy_failure_is_reported_next_to_the_main_one(dhcp_server, classrooms_on):
    dhcp_server["fake"]["proxy_error"] = "imagesrv-proxy לא הגיב ל-restart"
    r = dhcp_server["admin"].put("/api/console/net/interfaces/eth1",
                                 json={"proxy": True, "server_ip": "10.44.1.10",
                                       "confirm": "eth1",
                                       "confirm_proxy_broken": True})
    assert r.json()["ok"] is False and "imagesrv-proxy" in r.json()["apply_error"]


def test_bad_values_never_reach_dnsmasq(dhcp_server):
    admin = dhcp_server["admin"]
    r = admin.put("/api/console/net/interfaces/eth0",
                  json={**GOOD, "range_end": "10.44.9.20", "confirm": "eth0"})
    assert r.status_code == 400 and "גדולה" in r.json()["detail"]
    assert dhcp_server["fake"]["applied"] == []


def test_turning_off_needs_no_confirmation_and_rewrites_the_file(dhcp_server):
    admin = dhcp_server["admin"]
    admin.put("/api/console/net/interfaces/eth0", json={**GOOD, "confirm": "eth0"})
    r = admin.put("/api/console/net/interfaces/eth0", json={"enabled": False})
    assert r.status_code == 200
    assert "interface=eth0" not in dhcp_server["fake"]["applied"][-1]


def test_apply_failure_is_reported_not_hidden(dhcp_server):
    dhcp_server["fake"]["apply_error"] = "dnsmasq לא עלה: bad option"
    r = dhcp_server["admin"].put("/api/console/net/interfaces/eth0",
                                 json={**GOOD, "confirm": "eth0"})
    assert r.status_code == 200
    assert r.json()["ok"] is False and "dnsmasq" in r.json()["apply_error"]
    events = [row["event"] for row in dhcp_server["ctx"].conn.execute(
        "SELECT event FROM journal").fetchall()]
    assert "dhcp_apply_failed" in events


def test_dhcp_is_admin_only(dhcp_server):
    deploy = dhcp_server["deploy"]
    assert deploy.put("/api/console/net/interfaces/eth0",
                      json={**GOOD, "confirm": "eth0"}).status_code == 403
    assert deploy.get("/api/console/net/interfaces/eth0/probe").status_code == 403


def test_an_unknown_interface_is_refused(dhcp_server):
    r = dhcp_server["admin"].put("/api/console/net/interfaces/wlan9", json={"trunk": True})
    assert r.status_code == 404


# --- #762: אמת חיה ל-DHCP — parse_served_interfaces (טהור, בלי שרת) ---------


def test_parse_served_interfaces_reads_only_active_directives():
    text = (
        "# comment: interface=eth9\n"
        "interface=br0\n"
        "\n"
        "except-interface=eth2\n"
        "interface=eth0  \n"
        "dhcp-range=10.44.9.50,10.44.9.200\n"
    )
    assert dhcp.parse_served_interfaces(text) == {"br0", "eth0"}


def test_parse_served_interfaces_ignores_a_range_with_no_interface_line():
    assert dhcp.parse_served_interfaces(
        "dhcp-range=10.44.9.50,10.44.9.200\n") == set()


def test_parse_served_interfaces_on_empty_text():
    assert dhcp.parse_served_interfaces("") == set()
    assert dhcp.parse_served_interfaces(None) == set()


# --- #762: אמת חיה ל-DHCP — דרך הקונסולה, hooks מוזרקים בלבד ----------------


def _row(rows: list[dict], name: str) -> dict:
    return {r["name"]: r for r in rows}[name]


def test_dhcp_live_serving_when_conf_lists_it_and_service_is_active(dhcp_server):
    fake = dhcp_server["fake"]
    fake["active_conf"] = "interface=eth0\ndhcp-range=10.44.9.50,10.44.9.200\n"
    fake["service_active"] = True
    row = _row(dhcp_server["admin"].get("/api/console/net/interfaces").json(), "eth0")
    assert row["dhcp_live"]["state"] == "serving"
    assert row["dhcp_live"]["checked"] is True
    assert row["dhcp_live_label"] == "משרת"


def test_dhcp_live_configured_not_running_when_service_is_down(dhcp_server):
    fake = dhcp_server["fake"]
    fake["active_conf"] = "interface=eth0\n"
    fake["service_active"] = False
    row = _row(dhcp_server["admin"].get("/api/console/net/interfaces").json(), "eth0")
    assert row["dhcp_live"]["state"] == "configured_not_running"
    assert row["dhcp_live_label"] == "מוגדר, השירות אינו פועל"


def test_dhcp_live_off_when_conf_and_service_read_but_interface_absent(dhcp_server):
    fake = dhcp_server["fake"]
    fake["active_conf"] = "interface=eth1\n"
    fake["service_active"] = True
    row = _row(dhcp_server["admin"].get("/api/console/net/interfaces").json(), "eth0")
    assert row["dhcp_live"]["state"] == "off"
    assert row["dhcp_live_label"] == "כבוי"


def test_dhcp_live_is_unknown_never_off_when_conf_read_fails(dhcp_server):
    """הליבה של #762: קריאה שנכשלה אינה 'כבוי'. חסימה זו היא הבדיקה שהכי
    חשוב שלא תיפול לצד השקט — אחרת מפעיל שרואה 'כבוי' עשוי לחשוב שהוא
    בטוח לפעולה מסוכנת, כשבפועל פשוט לא בדקנו כלום (עיקרון 5, הרחבה 5א)."""
    fake = dhcp_server["fake"]
    fake["conf_readable"] = False
    fake["service_active"] = True
    row = _row(dhcp_server["admin"].get("/api/console/net/interfaces").json(), "eth0")
    assert row["dhcp_live"]["state"] == "unknown"
    assert row["dhcp_live"]["checked"] is False
    assert row["dhcp_live_label"] == "לא ידוע"
    assert row["dhcp_live"]["state"] != "off"


def test_dhcp_live_is_unknown_when_service_check_fails(dhcp_server):
    fake = dhcp_server["fake"]
    fake["active_conf"] = "interface=eth0\n"
    fake["service_readable"] = False
    row = _row(dhcp_server["admin"].get("/api/console/net/interfaces").json(), "eth0")
    assert row["dhcp_live"]["state"] == "unknown"
    assert row["dhcp_live"]["checked"] is False


def test_a_comment_and_except_interface_never_count_as_serving(dhcp_server):
    fake = dhcp_server["fake"]
    fake["active_conf"] = "# interface=eth0\nexcept-interface=eth0\n"
    fake["service_active"] = True
    row = _row(dhcp_server["admin"].get("/api/console/net/interfaces").json(), "eth0")
    assert row["dhcp_live"]["state"] == "off"


def test_dhcp_diverged_when_stored_disabled_but_live_serving(dhcp_server):
    fake = dhcp_server["fake"]
    fake["active_conf"] = "interface=eth0\n"
    fake["service_active"] = True
    row = _row(dhcp_server["admin"].get("/api/console/net/interfaces").json(), "eth0")
    assert row["enabled"] is False
    assert row["dhcp_diverged"] is True


def test_dhcp_diverged_when_stored_enabled_but_live_off(dhcp_server):
    admin, fake = dhcp_server["admin"], dhcp_server["fake"]
    assert admin.put("/api/console/net/interfaces/eth0",
                     json={**GOOD, "confirm": "eth0"}).status_code == 200
    fake["active_conf"] = ""       # השירות פעיל, אבל הממשק לא בקובץ
    fake["service_active"] = True
    row = _row(admin.get("/api/console/net/interfaces").json(), "eth0")
    assert row["enabled"] is True
    assert row["dhcp_diverged"] is True


def test_dhcp_not_diverged_when_stored_matches_live(dhcp_server):
    admin, fake = dhcp_server["admin"], dhcp_server["fake"]
    assert admin.put("/api/console/net/interfaces/eth0",
                     json={**GOOD, "confirm": "eth0"}).status_code == 200
    fake["active_conf"] = "interface=eth0\n"
    fake["service_active"] = True
    row = _row(admin.get("/api/console/net/interfaces").json(), "eth0")
    assert row["dhcp_diverged"] is False


def test_dhcp_live_endpoint_is_console_only(dhcp_server):
    """#762 לא פותח שום דבר חדש להרשאות — GET רגיל לכל משתמש מחובר **לקונסולה**;
    ‏#1073: deploy אינו כזה (403 `deploy_no_console`)."""
    r = dhcp_server["deploy"].get("/api/console/net/interfaces")
    assert r.status_code == 403 and "מחשב הבנייה" in r.json()["detail"]
    r = dhcp_server["admin"].get("/api/console/net/interfaces")
    assert r.status_code == 200
    assert "dhcp_live" in r.json()[0]
