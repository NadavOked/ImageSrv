"""‏installer/gui-bridge.sh — הדלת היחידה של גואי המתקין אל המנוע (#1190 חלק 2).

הגואי שולח JSON על stdin ופעולה ב-argv, ומקבל key=value. כאן המנוע הוא
shim שרושם מה קיבל ומחזיר מה שהטסט מכתיב — כמו בטסטי הסוכן. הבדיקות:
המלאי ממזג דיסקים וכרטיסים (עם ניסוחי #1183); ‏apply כותב answers ב-0600
ומתחיל את המנוע עם --state; ‏validate מחזיר שגיאה לשדה הנכון מקוד הכשל של
המנוע; כללי הסיסמה; ‏eject שנכשל מדווח ולא נבלע; ‏reboot רק אחרי eject.
דורש sh + jq (‏Linux/Testrunner; בווינדוס אין jq — דילוג מפורש)."""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
BRIDGE = REPO / "installer" / "gui-bridge.sh"
SH = shutil.which("sh")
JQ = shutil.which("jq")
pytestmark = pytest.mark.skipif(SH is None or JQ is None or os.name == "nt",
                                reason="sh + jq on Linux (Testrunner) — the bridge is busybox-sh with jq")


# #1310: curl as the bridge calls it -- `-o BODY -w '%{http_code}\n%{certs}'`.
# The test decides the exit code, stderr, HTTP code, body and certificate;
# the default (exit 7, "no route") is what an unreachable primary gives.
FAKE_CURL = """printf 'curl %s\\n' "$*" >> {calls}
out=
while [ $# -gt 0 ]; do case "$1" in -o) out=$2; shift 2 ;; *) shift ;; esac; done
[ -n "${{FAKE_CURL_ERR:-}}" ] && printf '%s\\n' "$FAKE_CURL_ERR" >&2
[ "${{FAKE_CURL_RC:-7}}" = 0 ] || exit "${{FAKE_CURL_RC:-7}}"
cat "$FAKE_CURL_BODY" > "$out"
printf '%s\\n' "${{FAKE_CURL_CODE:-200}}"
cat "$FAKE_CURL_PEM"
"""


def _machine(tmp_path: Path) -> dict:
    root = tmp_path / "root"
    for nic, mac, oper, carrier in (("ens33", "00:50:56:01:02:01", "up", "1"), ("ens34", "00:50:56:01:02:02", "down", "")):
        d = root / "sys/class/net" / nic; (d / "device").mkdir(parents=True)
        (d / "address").write_text(mac + "\n"); (d / "operstate").write_text(oper + "\n")
        if carrier:
            (d / "carrier").write_text(carrier + "\n")
        (d / "device/vendor").write_text("0x15ad\n"); (d / "device/device").write_text("0x07b0\n")
    run = tmp_path / "run"; (run / "dhcp").mkdir(parents=True)
    (run / "dhcp/ens33").write_text("state=lease\naddress=10.10.10.8/24\ngateway=10.10.10.1\ndns=10.10.10.1\n")
    (run / "dhcp/ens34").write_text("state=no-carrier\n")
    bin_dir = tmp_path / "bin"; bin_dir.mkdir()
    calls = tmp_path / "calls"
    engine = tmp_path / "engine"
    engine.write_text(f"""#!/bin/sh
printf '%s\\n' "$*" >> {calls}
case "$1" in
  --inventory) printf 'disk=/dev/sda|model=VMware Virtual disk|size=64424509440|bus=scsi|removable=0|has=empty|iso=0\\n'; exit 0 ;;
esac
cp "$1" {tmp_path}/answers.seen
if grep -q '^hostname=bad host$' "$1"; then printf 'installer: [invalid-hostname] hostname must be an RFC-1123 label\\n' >&2; exit 2; fi
if [ "$2" = --dry-run ]; then exit 0; fi
printf 'state=partitioning\\npct=5\\ntitle=partitioning\\n' > "$4"
""")
    engine.chmod(engine.stat().st_mode | stat.S_IEXEC)
    for tool, body in (("eject", f"printf 'eject %s\\n' \"$@\" >> {calls}; exit ${{EJECT_RC:-0}}\n"),
                       ("reboot", f"printf 'reboot %s\\n' \"$@\" >> {calls}\n"),
                       ("umount", "exit 0\n"), ("sync", "exit 0\n"),
                       ("ip", "exit 0\n"), ("curl", FAKE_CURL.format(calls=calls))):
        p = bin_dir / tool; p.write_text("#!/bin/sh\n" + body); p.chmod(0o755)
    return {"root": root, "run": run, "bin": bin_dir, "calls": calls, "engine": engine}


def bridge(m: dict, action: str, form: dict | None = None, **env: str) -> tuple[int, dict[str, list[str]], str]:
    e = {**os.environ, "PATH": f"{m['bin']}:{os.environ['PATH']}", "SYSROOT": str(m["root"]),
         "RUN_DIR": str(m["run"]), "ENGINE": str(m["engine"]), "DHCP_DIR": str(m["run"] / "dhcp"),
         "STATE_FILE": str(m["run"] / "install.state"), "ANSWERS": str(m["run"] / "answers"),
         "ENGINE_LOG": str(m["run"] / "install.log"), "MEDIA_FILE": str(m["run"] / "install-media"), **env}
    proc = subprocess.run([SH, str(BRIDGE), action], input=json.dumps(form or {}), capture_output=True,
                          text=True, env=e, stdin=None if False else None) if False else \
        subprocess.run([SH, str(BRIDGE), action], input=json.dumps(form or {}), capture_output=True, text=True, env=e)
    out: dict[str, list[str]] = {}
    for line in proc.stdout.splitlines():
        if "=" in line:
            k, v = line.split("=", 1); out.setdefault(k, []).append(v)
    return proc.returncode, out, proc.stderr


FORM = {"disk": "/dev/sda", "role": "standalone", "primary_url": "", "interface": "ens33", "mode": "dhcp",
        "address": "", "netmask": "", "gateway": "", "dns": "", "hostname": "imagectl-ta",
        "admin_user": "nadav", "password": "Aa123456!", "password_confirm": "Aa123456!",
        "root_password": "R00t!screen", "root_password_confirm": "R00t!screen"}   # #1632


def test_inventory_merges_disks_and_nics_with_the_1183_link_words(tmp_path):
    m = _machine(tmp_path)
    rc, out, err = bridge(m, "inventory")
    assert rc == 0, err
    assert out["disk"] == ["/dev/sda|model=VMware Virtual disk|size=64424509440|bus=scsi|removable=0|has=empty|iso=0"]
    rows = {r.split("|")[0]: r.split("|") for r in out["nic"]}
    assert rows["ens33"][1:6] == ["VMware VMXNET3", "00:50:56:01:02:01", "מחובר", "10.10.10.8/24", "DHCP · שער 10.10.10.1"]
    assert rows["ens33"][6:] == ["10.10.10.8", "255.255.255.0", "10.10.10.1", "10.10.10.1"]
    assert rows["ens34"][3] == "לא מודלק" and rows["ens34"][4] == "—"
    assert out["interface"] == ["ens33"] and out["mode"] == ["dhcp"] and out["admin_user"] == ["admin"]


@pytest.mark.parametrize("vendor,device,model", [
    ("0x15ad", "0x07b0", "VMware VMXNET3"),    # ESXi vmxnet3 -- was "VMware 0x07b0"
    ("0x1414", "0x3", "Hyper-V"),              # Hyper-V synthetic -- was "0x1414 0x3"
    ("0x8086", "0x15b8", "Intel"),             # a known maker, an unknown chip
    ("0x1234", "0x5678", ""),                  # unknown: the screen says "כרטיס רשת N"
])
def test_the_nic_model_is_a_name_a_person_reads_not_pci_numbers(tmp_path, vendor, device, model):
    """#2096 (Hyper-V/ESXi 24/10): the NIC row said 'eth0 · 0x1414 0x3'."""
    m = _machine(tmp_path)
    d = m["root"] / "sys/class/net/ens33/device"
    (d / "vendor").write_text(f"{vendor}\n"); (d / "device").write_text(f"{device}\n")
    rc, out, err = bridge(m, "inventory")
    assert rc == 0, err
    rows = {r.split("|")[0]: r.split("|") for r in out["nic"]}
    assert rows["ens33"][1] == model
    assert not any("0x" in r.split("|")[1] for r in out["nic"])


def test_apply_writes_the_answers_0600_and_starts_the_engine_with_a_state_file(tmp_path):
    m = _machine(tmp_path)
    rc, out, err = bridge(m, "apply", FORM)
    assert rc == 0, err
    assert "job" in out and "errors.password" not in out
    answers = m["run"] / "answers"
    assert stat.S_IMODE(answers.stat().st_mode) == 0o600
    text = answers.read_text()
    assert "admin_user=nadav\n" in text and "admin_pass=Aa123456!\n" in text and "servers_mac=00:50:56:01:02:01\n" in text
    assert "root_pass=R00t!screen\n" in text, "#1632: root's password goes to the engine the way admin's does"
    assert "servers_if=ens33\n" in text and "servers_mode=dhcp\n" in text and "servers_addr=\n" in text
    import time
    for _ in range(40):  # the real run is started in the background; give the shim a moment
        lines = m["calls"].read_text().splitlines()
        if (m["run"] / "install.state").exists() and lines and "--dry-run" not in lines[-1]:
            break
        time.sleep(0.05)
    calls = m["calls"].read_text()
    assert f"--dry-run --state" in calls, "the engine's own validation runs first (dry-run)"
    assert any(l.startswith(str(answers)) and "--state" in l and "--dry-run" not in l for l in calls.splitlines()), "the real run"
    assert (m["run"] / "install.state").read_text().startswith("state=partitioning")


def test_validate_maps_the_engine_failure_code_to_the_field_and_never_touches_a_disk(tmp_path):
    m = _machine(tmp_path)
    rc, out, err = bridge(m, "validate", {**FORM, "hostname": "bad host", "step": "3"})
    assert rc == 0, err
    assert out["errors.hostname"] == ["שם השרת אינו תקין — שם מחשב באנגלית: אותיות לטיניות, ספרות ומקף, עד 15 תווים, בלי מקף בתחילה או בסוף"]
    assert "[invalid-hostname] hostname must be an RFC-1123 label" in err, "the engine's English line stays in the log"
    calls = m["calls"].read_text()
    assert "--dry-run" in calls and all("--dry-run" in l for l in calls.splitlines() if "--inventory" not in l)
    assert not (m["run"] / "answers").exists(), "validate never writes the real answers"


def test_password_rules_live_in_the_bridge(tmp_path):
    m = _machine(tmp_path)
    rc, out, _ = bridge(m, "validate", {**FORM, "password": "short", "password_confirm": "other", "step": "4"})
    assert rc == 0
    assert "לפחות 8 תווים" in out["errors.password"] and "נדרשת לפחות ספרה אחת" in out["errors.password"]
    assert out["errors.password_confirm"] == ["הסיסמאות שהזנת אינן תואמות"]
    rc, out, _ = bridge(m, "apply", {**FORM, "password": "short", "password_confirm": "short"})
    assert "errors.password" in out and "job" not in out and not (m["run"] / "answers").exists()


def test_progress_strips_ansi_and_marks_the_engine_log_lines(tmp_path):
    m = _machine(tmp_path)
    (m["run"] / "install.state").write_text("state=packages\npct=40\ntitle=installing packages\n")
    (m["run"] / "install.log").write_text("\x1b[32m==>\x1b[0m unpacking\nSetting up grub\n")
    rc, out, _ = bridge(m, "progress")
    assert rc == 0 and out["state"] == ["packages"] and out["pct"] == ["40"]
    assert out["output"] == ["==> unpacking", "Setting up grub"]


def test_eject_failure_is_reported_and_reboot_only_after_eject(tmp_path):
    m = _machine(tmp_path)
    (m["run"] / "install-media").write_text("/dev/sr0\n")
    rc, out, _ = bridge(m, "eject", EJECT_RC="1")
    assert rc == 0 and out["ejected"] == ["0"] and "eject /dev/sr0 failed" in out["error"][0]
    rc, out, _ = bridge(m, "boot-local")
    calls = m["calls"].read_text().splitlines()
    assert calls.index("eject /dev/sr0") < calls.index("reboot -f")


# --- #1310: "בדוק חיבור" — ירוק רק על ראיה חיובית, וחוסם את "הבא" ----------

ALIVE = "/api/interserver/v1/alive"
SECONDARY = {**FORM, "role": "secondary", "hostname": "imagectl-haifa", "primary_url": "192.168.68.250"}


def _primary_cert(tmp_path: Path, name: str = "primary") -> tuple[Path, str]:
    """תעודה אמיתית (כמו זהות הראשי) ו-SHA-256 של ה-SPKI שלה."""
    from server import interserver_auth
    cert, _key = interserver_auth.generate_self_signed(name, ip_sans=["127.0.0.1"])
    pem = tmp_path / f"{name}.pem"
    pem.write_bytes(cert)
    return pem, interserver_auth.spki_sha256(cert)


def _answer(m: dict, tmp_path: Path, body, *, code: str = "200", pem: Path | None = None,
            form: dict | None = None) -> tuple[dict, str]:
    """check-primary מול curl שענה 0 עם הגוף/התעודה שהטסט מכתיב."""
    body_file = tmp_path / "body.json"
    body_file.write_text(body if isinstance(body, str) else json.dumps(body))
    if pem is None:
        pem, _ = _primary_cert(tmp_path)
    rc, out, err = bridge(m, "check-primary", form or {"primary_url": "192.168.68.250"},
                          FAKE_CURL_RC="0", FAKE_CURL_CODE=code,
                          FAKE_CURL_BODY=str(body_file), FAKE_CURL_PEM=str(pem))
    assert rc == 0, err
    return out, err


def _curl_urls(m: dict) -> list[str]:
    calls = m["calls"].read_text() if m["calls"].exists() else ""
    return [w for line in calls.splitlines() if line.startswith("curl ")
            for w in line.split() if w.startswith("https://")]


@pytest.mark.parametrize("typed", [
    "192.168.68.250", " 192.168.68.250 ", "192.168.68.250:8443",
    "https://192.168.68.250:8443", "https://192.168.68.250:8443/",
])
def test_check_primary_takes_a_bare_ip_and_asks_the_alive_path_on_8443(tmp_path, typed):
    """נדב 12/10: "למה לכתוב את כל זה ולא רק את ה-IP?" — כל הצורות מגיעות
    לאותה כתובת, וזו השאלה היחידה שהמשני שולח לראשי."""
    m = _machine(tmp_path)
    rc, out, _ = bridge(m, "check-primary", {"primary_url": typed})
    assert rc == 0 and out["ok"] == ["0"]
    assert _curl_urls(m) == ["https://192.168.68.250:8443" + ALIVE]


def test_check_primary_takes_a_host_name_too(tmp_path):
    m = _machine(tmp_path)
    bridge(m, "check-primary", {"primary_url": "imagectl-ta.college.ac.il"})
    assert _curl_urls(m) == ["https://imagectl-ta.college.ac.il:8443" + ALIVE]


@pytest.mark.parametrize("typed", ["", "http://192.168.68.250", "192.168.68.250:9443",
                                   "https://192.168.68.250:8443/x", "192.168 .68.250", "-x"])
def test_an_address_that_is_not_one_is_red_without_asking_anyone(tmp_path, typed):
    m = _machine(tmp_path)
    rc, out, _ = bridge(m, "check-primary", {"primary_url": typed})
    assert rc == 0 and out["ok"] == ["0"] and "כתובת לא תקינה" in out["error"][0]
    assert _curl_urls(m) == []


@pytest.mark.parametrize(("curl_rc", "stderr", "reason"), [
    ("7", "curl: (7) Failed to connect to 192.168.68.250 port 8443: Connection refused", "(refused)"),
    ("7", "curl: (7) Failed to connect to 192.168.68.250 port 8443: No route to host", "אין נתיב"),
    ("28", "curl: (28) Connection timed out after 5001 milliseconds", "(timeout)"),
    ("35", "curl: (35) TLS connect error", "TLS"),
    ("6", "curl: (6) Could not resolve host", "(DNS)"),
    ("52", "curl: (52) Empty reply from server", "סגר את החיבור"),
    ("99", "", "(curl 99)"),
])
def test_a_real_failure_is_red_with_its_reason(tmp_path, curl_rc, stderr, reason):
    m = _machine(tmp_path)
    rc, out, _ = bridge(m, "check-primary", {"primary_url": "192.168.68.250"},
                        FAKE_CURL_RC=curl_rc, FAKE_CURL_ERR=stderr)
    assert rc == 0 and out["ok"] == ["0"], out
    assert reason in out["error"][0], out["error"]
    assert "name" not in out and "fingerprint" not in out
    assert not (m["run"] / "primary-check").exists()


def test_green_shows_name_version_fingerprint_and_records_the_check(tmp_path):
    m = _machine(tmp_path)
    pem, spki = _primary_cert(tmp_path)
    out, _ = _answer(m, tmp_path, {"name": "imagectl-ta", "version": "v0.60.0", "fingerprint": spki},
                     pem=pem)
    assert out["ok"] == ["1"] and out["name"] == ["imagectl-ta"]
    assert out["version"] == ["v0.60.0"] and out["fingerprint"] == [spki]
    record = (m["run"] / "primary-check").read_text()
    assert record == f"url=https://192.168.68.250:8443\nfingerprint={spki}\n", \
        "the record holds what the answers need, nothing the primary chose to say"


def test_a_display_name_in_hebrew_is_still_green(tmp_path):
    """‏`name` הוא השם שבראש העץ בקונסולה — טקסט חופשי, יכול להיות עברית."""
    m = _machine(tmp_path)
    pem, spki = _primary_cert(tmp_path)
    out, _ = _answer(m, tmp_path, {"name": "שרת תל אביב", "version": None, "fingerprint": spki}, pem=pem)
    assert out["ok"] == ["1"] and out["name"] == ["שרת תל אביב"] and out["version"] == [""]


@pytest.mark.parametrize("field", ["name", "version"])
def test_a_line_break_in_name_or_version_cannot_plant_a_fingerprint(tmp_path, field):
    """סקירת אבטחה (#1310): משיב זדוני שם `\\nfingerprint=<שלו>` בשם או
    בגרסה. לפני התיקון השורה המוזרקת נכתבה לרשומה **לפני** הטביעה האמיתית,
    ו-`sed | head -n1` של write_answers בחר בה — המתקין היה מצמיד מפתח של
    התוקף. עכשיו: אדום, אין רשומה, ובתשובות אין טביעה."""
    m = _machine(tmp_path)
    pem, spki = _primary_cert(tmp_path)
    _, planted = _primary_cert(tmp_path, "attacker")
    body = {"name": "imagectl-ta", "version": "v1", "fingerprint": spki}
    body[field] = f"x\nfingerprint={planted}"
    out, _ = _answer(m, tmp_path, body, pem=pem)
    assert out["ok"] == ["0"], out
    assert not (m["run"] / "primary-check").exists()
    bridge(m, "validate", {**SECONDARY, "step": "2"})
    seen = (tmp_path / "answers.seen").read_text()
    assert planted not in seen and "primary_fingerprint=\n" in seen


@pytest.mark.parametrize("record", [
    "url=https://192.168.68.250:8443\nfingerprint={bad}\nfingerprint={good}\n",
    "url=https://192.168.68.250:8443\nname=x\nfingerprint={good}\n",
    "fingerprint={good}\nurl=https://192.168.68.250:8443\n",
    "url=https://192.168.68.250:8443\nfingerprint={good}x\n",
])
def test_a_record_of_any_other_shape_is_not_a_green_check(tmp_path, record):
    """שכבה שנייה: גם רשומה שמישהו אחר כתב (או גרסה ישנה) אינה מכניסה טביעה
    לתשובות אלא אם היא בדיוק url= ואז fingerprint= של 64 hex."""
    m = _machine(tmp_path)
    (m["run"] / "primary-check").write_text(record.format(good="ab" * 32, bad="cd" * 32))
    bridge(m, "validate", {**SECONDARY, "step": "2"})
    assert "primary_fingerprint=\n" in (tmp_path / "answers.seen").read_text()


def test_the_exact_record_is_what_puts_the_fingerprint_in(tmp_path):
    """הצד החיובי של הטסט הקודם — אחרת "אף פעם לא" היה עובר אותו."""
    m = _machine(tmp_path)
    (m["run"] / "primary-check").write_text(f"url=https://192.168.68.250:8443\nfingerprint={'ab' * 32}\n")
    bridge(m, "validate", {**SECONDARY, "step": "2"})
    assert f"primary_fingerprint={'ab' * 32}\n" in (tmp_path / "answers.seen").read_text()


@pytest.mark.parametrize(("code", "body", "reason"), [
    ("404", {"detail": "Not Found"}, "HTTP 404"),
    ("200", {"ok": True, "version": "v0.60.0", "version_error": None}, "לא בתשובת השרת הראשי"),
    ("200", "<html>router login</html>", "לא בתשובת השרת הראשי"),
    ("200", {"name": "", "version": "v1", "fingerprint": "ab" * 32}, "לא בתשובת השרת הראשי"),
    ("200", {"name": "x", "version": "v1", "fingerprint": "ab" * 32, "token": "s"}, "לא בתשובת השרת הראשי"),
    ("200", {"name": "x", "version": "v1", "fingerprint": "AB" * 32}, "לא בתשובת השרת הראשי"),
])
def test_another_product_on_8443_is_red_not_green(tmp_path, code, body, reason):
    m = _machine(tmp_path)
    out, _ = _answer(m, tmp_path, body, code=code)
    assert out["ok"] == ["0"] and reason in out["error"][0], out
    assert not (m["run"] / "primary-check").exists()


def test_a_fingerprint_that_is_not_the_key_tls_showed_is_red(tmp_path):
    """תשובה "נכונה" מכתובת שהציגה מפתח אחר — לא ירוק: הטביעה היא מה
    שהמפעיל ישווה, והיא חייבת להיות של מי שענה."""
    m = _machine(tmp_path)
    _, other_spki = _primary_cert(tmp_path, "someone-else")
    pem, _ = _primary_cert(tmp_path, "the-one-that-answered")
    out, _ = _answer(m, tmp_path, {"name": "imagectl-ta", "version": None, "fingerprint": other_spki},
                     pem=pem)
    assert out["ok"] == ["0"] and "טביעת האצבע" in out["error"][0]


def test_a_certificate_that_cannot_be_read_is_not_verified(tmp_path):
    m = _machine(tmp_path)
    junk = tmp_path / "junk.pem"
    junk.write_text("Cert:\n")
    out, _ = _answer(m, tmp_path, {"name": "x", "version": None, "fingerprint": "ab" * 32}, pem=junk)
    assert out["ok"] == ["0"] and "לא אומת" in out["error"][0]


def test_answers_carry_the_fingerprint_only_from_a_green_check_of_the_same_address(tmp_path):
    """המנוע מסרב למשני בלי טביעה (test_installer_engine). כאן: הגשר כותב
    אותה רק מהבדיקה הירוקה שלו — לא מהטופס — ורק לאותה כתובת מנורמלת."""
    m = _machine(tmp_path)
    seen = tmp_path / "answers.seen"
    bridge(m, "validate", {**SECONDARY, "step": "2"})
    assert "primary_url=https://192.168.68.250:8443\n" in seen.read_text()
    assert "primary_fingerprint=\n" in seen.read_text(), "not checked = no fingerprint"

    pem, spki = _primary_cert(tmp_path)
    out, _ = _answer(m, tmp_path, {"name": "imagectl-ta", "version": "v1", "fingerprint": spki}, pem=pem)
    assert out["ok"] == ["1"]
    bridge(m, "validate", {**SECONDARY, "primary_url": "https://192.168.68.250:8443/", "step": "2",
                           "primary_fingerprint": "ff" * 32})
    assert f"primary_fingerprint={spki}\n" in seen.read_text(), "the check, never the form"

    bridge(m, "validate", {**SECONDARY, "primary_url": "192.168.68.251", "step": "2"})
    assert "primary_fingerprint=\n" in seen.read_text(), "another address was never checked"

    bridge(m, "check-primary", {"primary_url": "192.168.68.250"})   # red now (curl exit 7)
    bridge(m, "validate", {**SECONDARY, "step": "2"})
    assert "primary_fingerprint=\n" in seen.read_text(), "a red check forgets the green one"


def test_standalone_answers_have_no_primary(tmp_path):
    m = _machine(tmp_path)
    bridge(m, "validate", {**FORM, "step": "2"})
    seen = (tmp_path / "answers.seen").read_text()
    assert "primary_url=\n" in seen and "primary_fingerprint=\n" in seen


def test_engine_refusal_lands_on_the_address_field(tmp_path):
    """‏[primary-unverified] מהמנוע → errors.primary_url, כמו כל קוד אחר."""
    m = _machine(tmp_path)
    engine = m["engine"]
    engine.write_text(engine.read_text().replace(
        "if [ \"$2\" = --dry-run ]",
        "if grep -q '^primary_fingerprint=$' \"$1\" && grep -q '^role=secondary$' \"$1\"; then "
        "printf 'installer: [primary-unverified] the primary was not verified\\n' >&2; exit 2; fi\n"
        "if [ \"$2\" = --dry-run ]"))
    rc, out, _ = bridge(m, "validate", {**SECONDARY, "step": "2"})
    assert rc == 0 and out["errors.primary_url"] == ["השרת הראשי לא אומת — הריצו בדיקת חיבור עד שהיא ירוקה"]


def test_check_primary_green_end_to_end_against_the_real_listener(tmp_path):
    """הראשי האמיתי (‏`server.primary_alive` + זהות + uvicorn TLS) על
    כתובת loopback משלו בפורט 8443, ו-curl/openssl/jq **אמיתיים**: ירוק,
    והטביעה היא זהות הראשי."""
    import socket
    import threading
    uvicorn = pytest.importorskip("uvicorn")
    if not shutil.which("openssl"):
        pytest.fail("openssl is part of the installer image; the test host must have it too")
    from server import db, primary_alive, storage_nodes
    host = "127.13.10.8"
    with socket.socket() as probe:
        probe.bind((host, 8443))      # free? a busy port fails here, by name -- not a skip
    conn = db.connect(tmp_path / "p.db")
    ident = storage_nodes.ensure_identity(conn, tmp_path / "data", host_sans=[host])
    app = primary_alive.create_alive_app(name=lambda: "imagectl-ta", version=lambda: "v0.60.0",
                                         fingerprint=ident["server_spki"])
    server = uvicorn.Server(primary_alive.server_config(app, ident, host, 8443))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            thread.join(0.05)
        assert server.started
        m = _machine(tmp_path)
        (m["bin"] / "curl").unlink()          # the real curl from here on
        rc, out, err = bridge(m, "check-primary", {"primary_url": host})
        assert rc == 0, err
        assert out["ok"] == ["1"], out
        assert out["name"] == ["imagectl-ta"] and out["version"] == ["v0.60.0"]
        assert out["fingerprint"] == [ident["server_spki"]]
    finally:
        server.should_exit = True
        thread.join(5)


def test_bridge_is_posix_sh_and_under_the_line_wall():
    text = BRIDGE.read_text(encoding="utf-8")
    assert text.startswith("#!/bin/sh\n")
    code = "\n".join(line.split("#", 1)[0] for line in text.splitlines())
    for bashism in ("[[ ", "function ", "$(( ", "==", "declare ", "local "):
        assert bashism not in code, bashism
    assert len(text.splitlines()) <= 300


def test_validation_before_the_admin_step_does_not_demand_a_password(tmp_path):
    """הגואי מאמת כל מסך בנפרד; לפני מסך 4 הסיסמה ריקה בצדק — המנוע לא אמור
    לענות 'admin_pass must not be empty' על מסך הרשת (נראה ב-ESXi 21/09)."""
    m = _machine(tmp_path)
    rc, out, err = bridge(m, "validate", {**FORM, "password": "", "password_confirm": "", "step": "2",
                                          "root_password": "", "root_password_confirm": ""})
    assert rc == 0, err
    assert not [k for k in out if k.startswith("errors.")], out
    seen = (tmp_path / "answers.seen").read_text()
    assert "admin_pass=not-yet-asked-1!" in seen, "placeholder for the dry-run, never the real answers"
    assert "root_pass=not-yet-asked-1!" in seen, "#1632: the same placeholder for root's"
    assert not (m["run"] / "answers").exists()


# --- #1632 (Nadav 24/10): root's password is chosen on the admin screen ----------------

@pytest.mark.parametrize(("root", "confirm", "field", "message"), [
    ("Ab1!", "Ab1!", "errors.root_password", "לפחות 8 תווים"),
    ("abcdefg!", "abcdefg!", "errors.root_password", "נדרשת לפחות ספרה אחת"),
    ("abcdefg1", "abcdefg1", "errors.root_password", "נדרש לפחות תו מיוחד אחד"),
    ("", "", "errors.root_password", "לפחות 8 תווים"),
    ("R00t!screen", "R00t!screeN", "errors.root_password_confirm", "סיסמאות ה-root שהזנת אינן תואמות"),
])
def test_root_password_has_the_admin_rules_and_apply_refuses_without_it(tmp_path, root, confirm, field, message):
    m = _machine(tmp_path)
    form = {**FORM, "root_password": root, "root_password_confirm": confirm}
    rc, out, err = bridge(m, "validate", form)
    assert rc == 0, err
    assert message in out.get(field, []), out
    assert "errors.password" not in out and "errors.password_confirm" not in out, "the admin's is fine"
    rc, out, err = bridge(m, "apply", form)
    assert rc == 0, err
    assert field in out and "job" not in out, out
    assert not (m["run"] / "answers").exists(), "no answers, no engine, without a valid root password"


def test_a_valid_root_password_has_no_error_and_never_leaves_the_answers_file(tmp_path):
    """The guard against an over-broad rule, and the secrecy: the value is in
    the 0600 answers file only -- not on the bridge's stdout, stderr, or the
    engine's argv."""
    m = _machine(tmp_path)
    rc, out, err = bridge(m, "validate", FORM)
    assert rc == 0, err
    assert not [k for k in out if k.startswith("errors.")], out
    rc, out, err = bridge(m, "apply", FORM)
    assert rc == 0 and "job" in out, (out, err)
    assert "R00t!screen" not in "\n".join(f"{k}={v}" for k, vs in out.items() for v in vs) + err
    import time
    for _ in range(40):
        if (m["run"] / "install.state").exists() and len(m["calls"].read_text().splitlines()) >= 2:
            break
        time.sleep(0.05)
    assert "R00t!screen" not in m["calls"].read_text(), "the engine's argv carries only the answers path"
    assert stat.S_IMODE((m["run"] / "answers").stat().st_mode) == 0o600


def test_an_engine_refusal_of_the_root_password_lands_on_its_field():
    fe = REPO / "installer" / "lib" / "fielderrors.sh"
    for code in ("invalid-root-password", "root-password-missing"):
        got = subprocess.run([SH, "-c", '. "$1"; field_for_code "$2"; printf "|"; he_for_code "$2"', "_", str(fe), code],
                             capture_output=True, text=True, stdin=subprocess.DEVNULL)
        assert got.returncode == 0, got.stderr
        assert got.stdout == "root_password|סיסמת ה-root לא יכולה להיות ריקה", got.stdout
