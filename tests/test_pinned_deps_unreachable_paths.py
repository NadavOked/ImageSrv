"""נתיבי הקוד הפגיעים בהתראות Dependabot של cryptography/pyOpenSSL — אינם בשימוש.

שמונה התראות על ``server/requirements.txt`` נסגרות בטענה אחת: "הקוד שלנו לא
מגיע לנתיב הפגיע" (‏``SECURITY.md``, סעיף "התראות Dependabot על תלויות
מוצמדות"). טענה כזו היא **ציטוט** עד שמישהו בודק אותה — והיא נשברת בשקט ביום
שמישהו מוסיף ``x509.verification`` "כי זה נוח". הטסט הזה הופך אותה לראיה:
הוא קורא את עץ ``server/`` ונופל כשנתיב מהרשימה מופיע, ואז מי שהוסיף אותו
חייב לחזור להתראה ולהחליט.

| התראה | נתיב פגיע | מה נאסר כאן |
|---|---|---|
| cryptography CVE-2026-69249 | בניית שרשרת ב-``x509.verification`` | כל שימוש במודול |
| cryptography CVE-2026-34073 | name constraints ב-``x509.verification`` | כל שימוש במודול |
| cryptography CVE-2026-26007 | נקודות על עקומות SECT | ‏``ec.SECT*`` |
| pyOpenSSL CVE-2026-27459 | ‏``set_cookie_generate_callback`` (DTLS) | הקריאה לה |
| pyOpenSSL CVE-2026-27448 | ‏``set_tlsext_servername_callback`` | הקריאה לה |
| cryptography CVE-2026-69248 | wildcard מול name constraints ב-``x509.verification`` | כל שימוש במודול |

#1975 הרחיב את אותה טענה לחולשות **שפתוחות ב-Debian 13** בחבילות שהשרת
מקבל מ-apt (‏``tools/security-recheck.py`` מוצא אותן; כאן נעול שהקוד שלנו
לא מגיע אליהן):

| חבילה (trixie) | חולשות פתוחות | מה נאסר כאן |
|---|---|---|
| python3-python-multipart 0.0.20 (Recommends של starlette) | CVE-2026-40347, ‏42561, ‏53537–53540 | ‏``Form``/``File``/``UploadFile`` של FastAPI ו-``request.form()`` — רק הם מפעילים את המפענח |
| python3-h2 / python3-hpack | CVE-2025-57804, ‏CVE-2026-71554, ‏CVE-2026-59980 | ייבוא ``h2``/``hpack`` (‏uvicorn אינו מדבר HTTP/2) |
| python3-anyio 4.8.0 | CVE-2026-63349, ‏63374, ‏64847 | ייבוא ישיר של ``anyio`` (‏run_process/to_process/connect_tcp) |
| python3.13 | CVE-2026-19445, ‏19553, ‏15308, ‏18503, ‏15806 | ‏``sni_callback``, ‏``wrap_bio``, ‏``HTMLParser``, ‏``Sniffer``, ‏``HTTPPasswordMgr*`` |

התראות ה-OpenSSL שבתוך ה-wheels (‏GHSA-537c, ‏CVE-2024-12797 ועוד) אינן כאן:
הן לא עניין של קוד אלא של **מאיפה הספרייה מגיעה** — בשרת היא ``apt``
שמקשר ל-libssl של המערכת (‏``Depends: libssl3t64``).

מגבלה, במפורש: הסריקה היא AST על קוד פייתון ב-``server/``. שימוש דינמי
(‏``importlib``, ‏``getattr`` עם מחרוזת) אינו נתפס, וקוד מחוץ ל-``server/``
לא נסרק — ‏``agent/`` הוא sh, ו-``tools/`` אינו רץ בשרת הייצור.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SERVER = REPO / "server"

#: שמות ששימוש בהם (קריאה, ייבוא, גישה לתכונה) פותח את אחד הנתיבים הפגיעים.
FORBIDDEN_NAMES = {
    "set_cookie_generate_callback": "pyOpenSSL CVE-2026-27459 (DTLS cookie)",
    "set_cookie_verify_callback": "pyOpenSSL CVE-2026-27459 (DTLS cookie)",
    "set_tlsext_servername_callback": "pyOpenSSL CVE-2026-27448 (servername)",
    "set_servername_callback": "pyOpenSSL CVE-2026-27448 (servername)",
    "PolicyBuilder": "cryptography CVE-2026-69249/34073 (x509.verification)",
    "ClientVerifier": "cryptography CVE-2026-69249/34073 (x509.verification)",
    "ServerVerifier": "cryptography CVE-2026-69249/34073 (x509.verification)",
    # #1975: open in Debian 13 python3.13 (3.13.5-2+deb13u5, 22/10/2026).
    "sni_callback": "python3.13 CVE-2026-19445 (SSLContext.sni_callback)",
    "wrap_bio": "python3.13 CVE-2026-19553 (wrap_bio without server_hostname)",
    "HTMLParser": "python3.13 CVE-2026-15308 (html.parser)",
    "Sniffer": "python3.13 CVE-2026-18503 (csv.Sniffer)",
    "HTTPPasswordMgr": "python3.13 CVE-2026-15806 (urllib HTTPPasswordMgr)",
    "HTTPPasswordMgrWithDefaultRealm": "python3.13 CVE-2026-15806 (urllib HTTPPasswordMgr)",
    "HTTPPasswordMgrWithPriorAuth": "python3.13 CVE-2026-15806 (urllib HTTPPasswordMgr)",
    "UploadFile": "python-multipart CVE-2026-40347/42561/5353x (multipart parser)",
}
#: ‏#1975: מודולים שאסור לייבא — אין לנו שימוש בהם, וחולשותיהם פתוחות ב-Debian 13.
FORBIDDEN_MODULES = {
    "multipart": "python-multipart CVE-2026-40347/42561/5353x",
    "python_multipart": "python-multipart CVE-2026-40347/42561/5353x",
    "h2": "python-h2 CVE-2025-57804/CVE-2026-71554 (HTTP/2)",
    "hpack": "python-hpack CVE-2026-59980 (HTTP/2)",
    "anyio": "python-anyio CVE-2026-63349/63374/64847",
}
#: ‏FastAPI מפעיל את מפענח ה-multipart רק לנקודת קצה שמצהירה עליהם.
FASTAPI_FORM_PARAMS = {"Form", "File", "UploadFile"}
SECT_CURVE = re.compile(r"^SECT\d+[KR]\d$")
VERIFICATION_MODULE = "cryptography.x509.verification"


def find_forbidden(source: str) -> list[str]:
    """שורות ``<שורה>: <סיבה>`` לכל שימוש אסור במקור פייתון אחד."""
    hits: list[str] = []
    for node in ast.walk(ast.parse(source)):
        line = getattr(node, "lineno", 0)
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(VERIFICATION_MODULE):
                    hits.append(f"{line}: import {alias.name}")
                top = alias.name.split(".")[0]
                if top in FORBIDDEN_MODULES:
                    hits.append(f"{line}: import {alias.name} — {FORBIDDEN_MODULES[top]}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.startswith(VERIFICATION_MODULE):
                hits.append(f"{line}: from {module} import ...")
            top = module.split(".")[0]
            if node.level == 0 and top in FORBIDDEN_MODULES:
                hits.append(f"{line}: from {module} import ... — {FORBIDDEN_MODULES[top]}")
            if node.level == 0 and top == "fastapi":
                hits += [f"{line}: from {module} import {a.name} — python-multipart (form parsing)"
                         for a in node.names if a.name in ("Form", "File")]
            if module == "cryptography.x509" and any(
                    a.name == "verification" for a in node.names):
                hits.append(f"{line}: from cryptography.x509 import verification")
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "form"):
            hits.append(f"{line}: .form() — python-multipart (Starlette Request.form)")
        if (isinstance(node, ast.Attribute) and node.attr in FASTAPI_FORM_PARAMS
                and isinstance(node.value, ast.Name) and node.value.id == "fastapi"):
            hits.append(f"{line}: fastapi.{node.attr} — python-multipart (form parsing)")
        names: list[str] = []
        if isinstance(node, ast.Attribute):
            names.append(node.attr)
        elif isinstance(node, ast.Name):
            names.append(node.id)
        elif isinstance(node, ast.ImportFrom):
            names += [a.name for a in node.names]
        for name in names:
            if name in FORBIDDEN_NAMES:
                hits.append(f"{line}: {name} — {FORBIDDEN_NAMES[name]}")
            elif SECT_CURVE.match(name):
                hits.append(f"{line}: {name} — cryptography CVE-2026-26007 (SECT)")
    return hits


def server_sources() -> list[Path]:
    # ``static/`` הוא vendor של JS — אין שם פייתון, והסריקה לא צריכה לעבור עליו.
    return [p for p in sorted(SERVER.rglob("*.py")) if "static" not in p.parts]


def test_scan_actually_covers_the_server_tree() -> None:
    """סריקה שלא קראה דבר "עוברת" — עיקרון 5. מוודאים שהיא ראתה את הקבצים
    שמשתמשים בספריות הנבדקות, ולא רק שלא מצאה כלום."""
    names = {p.name for p in server_sources()}
    assert len(names) > 20, f"הסריקה ראתה רק {len(names)} קבצים"
    for must in ("interserver_auth.py", "interserver_api.py", "console_tls.py"):
        assert must in names, f"{must} לא נסרק (יש: {sorted(names)[:10]}...)"


def test_server_code_does_not_use_the_vulnerable_paths() -> None:
    offenders: dict[str, list[str]] = {}
    for path in server_sources():
        hits = find_forbidden(path.read_text(encoding="utf-8"))
        if hits:
            offenders[str(path.relative_to(REPO))] = hits
    assert not offenders, (
        "נתיב פגיע מהתראות Dependabot נכנס לקוד השרת — חוזרים להתראה "
        f"(SECURITY.md) ומחליטים לפני שממשיכים: {offenders}")


def test_the_only_curve_we_generate_is_p256() -> None:
    """ראיה חיובית לצד השלילית: מה שכן בשימוש הוא SECP256R1 (לא SECT)."""
    text = (SERVER / "interserver_auth.py").read_text(encoding="utf-8")
    assert "ec.generate_private_key(ec.SECP256R1())" in text


def test_scanner_catches_each_forbidden_path() -> None:
    """בקרה שלילית של הסורק עצמו: על קוד שמכיל כל נתיב — הוא חייב לצעוק.
    בלי זה, ‏``find_forbidden`` שמחזיר תמיד ``[]`` היה "מוכיח" הכול."""
    cases = {
        "import cryptography.x509.verification": "verification",
        "from cryptography.x509.verification import PolicyBuilder": "verification",
        "from cryptography.x509 import verification": "verification",
        "from cryptography.x509 import verification as v\nv.PolicyBuilder()": "PolicyBuilder",
        "ctx.set_cookie_generate_callback(cb)": "set_cookie_generate_callback",
        "ctx.set_tlsext_servername_callback(cb)": "set_tlsext_servername_callback",
        "from cryptography.hazmat.primitives.asymmetric import ec\n"
        "ec.generate_private_key(ec.SECT571R1())": "SECT571R1",
        # #1975
        "from fastapi import Form": "Form",
        "from fastapi import File, UploadFile": "File",
        "def f(x: UploadFile): ...": "UploadFile",
        "import fastapi\ndef f(x=fastapi.Form()): ...": "fastapi.Form",
        "async def f(request):\n    data = await request.form()": ".form()",
        "import python_multipart": "python_multipart",
        "from multipart.multipart import parse_options_header": "multipart",
        "import h2.connection": "h2",
        "import anyio\nanyio.run(main)": "anyio",
        "from anyio import to_process": "anyio",
        "ctx.sni_callback = pick": "sni_callback",
        "ctx.wrap_bio(i, o)": "wrap_bio",
        "from html.parser import HTMLParser": "HTMLParser",
        "csv.Sniffer().sniff(sample)": "Sniffer",
        "urllib.request.HTTPPasswordMgrWithDefaultRealm()": "HTTPPasswordMgr",
    }
    for source, needle in cases.items():
        hits = find_forbidden(source)
        assert hits and any(needle in h for h in hits), (source, hits)


def test_scanner_is_quiet_on_what_we_do_use() -> None:
    clean = (
        "from cryptography import x509\n"
        "from cryptography.x509.oid import NameOID\n"
        "from cryptography.hazmat.primitives.asymmetric import ec\n"
        "from OpenSSL import SSL\n"
        "key = ec.generate_private_key(ec.SECP256R1())\n"
        "ctx = SSL.Context(SSL.TLS_METHOD)\n"
        "ctx.set_verify(SSL.VERIFY_PEER, cb)\n"
        # #1975: what the server does use, next to the forbidden names.
        "from fastapi import FastAPI, Request, Body\n"
        "runtime.form = {}\n"
        "x = runtime.form['interface']\n"
        "def run_process(cmd): ...\n"
        "from .multipart_helpers import x\n"
    )
    assert find_forbidden(clean) == []


# --- #1975: busybox ב-initramfs (‏busybox-static 1:1.37.0-6+b9) ------------------
#
# ב-trixie פתוחות 24 חולשות ב-busybox. רובן ב-applets שהסוכן לא מריץ — וזה
# נעול כאן, כי הסוכן רץ ברשת ההפצה ו-applet שנכנס "כי הוא כבר שם" פותח אותן:
#   udhcpc6 CVE-2026-29004 · ‏wget CVE-2025-60876 · ‏ssl_client (TLS)
#   CVE-2026-88830 · ‏httpd CVE-2026-88829/88831/88834/88837 · ‏unzip CVE-2026-26157
# ‏udhcpc (IPv4) **כן** בשימוש, ו-CVE-2026-88833 שבו נגיש — ראו SECURITY.md.

AGENT = REPO / "agent"
BUSYBOX_FORBIDDEN = re.compile(r"(?<![\w./-])(udhcpc6|wget|ssl_client|httpd|unzip)(?![\w.-])")


def busybox_forbidden(text: str) -> list[str]:
    hits = []
    for n, line in enumerate(text.splitlines(), 1):
        code = line.split("#", 1)[0] if not line.lstrip().startswith("#!") else ""
        m = BUSYBOX_FORBIDDEN.search(code)
        if m:
            hits.append(f"{n}: {m.group(1)}")
    return hits


def agent_shell_sources() -> list[Path]:
    return [p for p in sorted(AGENT.rglob("*"))
            if p.is_file() and p.suffix not in (".c", ".h")]


def test_agent_does_not_run_busybox_applets_with_open_cves() -> None:
    files = agent_shell_sources()
    texts = {p: p.read_text(encoding="utf-8") for p in files}
    # ראיה חיובית שהסריקה קראה את הסוכן: ה-udhcpc שבו נמצא.
    assert any(re.search(r"(?<![\w-])udhcpc -i", t) for t in texts.values()), \
        f"udhcpc לא נמצא באף קובץ ({len(files)} קבצים) — הסריקה לא קראה את הסוכן"
    offenders = {str(p.relative_to(REPO)): h for p, t in texts.items()
                 if (h := busybox_forbidden(t))}
    assert not offenders, (
        "applet של busybox עם חולשה פתוחה ב-trixie נכנס לסוכן — חוזרים ל-#1975 "
        f"ומחליטים לפני שממשיכים: {offenders}")


def test_busybox_scanner_catches_and_ignores() -> None:
    for line in ("udhcpc6 -i eth0", "x=$(wget -qO- http://s/)", "busybox httpd -p 80",
                 "ssl_client -s 3", "  unzip -o a.zip"):
        assert busybox_forbidden(line), line
    for line in ("udhcpc -i eth0 -n -q", "# wget was here", "curl -fsS http://s/wget",
                 "log 'httpd-style'", "/etc/imagectl/udhcpc.script"):
        assert not busybox_forbidden(line), line
