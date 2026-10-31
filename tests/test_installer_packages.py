"""רשימת ה-apt של המתקין מכילה את כל מה שהשרת צריך כדי לעבוד (#904).

הסניף המשני הראשון במעבדה (16/09) הותקן מהמתקין, והמוניטור (WebSocket
ב-uvicorn, #690) החזיר 500 עד ``apt-get install python3-websockets``:
‏uvicorn אינו מדבר WebSocket בעצמו, והחבילה לא הייתה ברשימה. הראשי
במעבדה הותקן ידנית ולכן זה לא נראה שם. באותה משפחה: ‏python3-cryptography
ו-python3-openssl לערוץ הבין-שרתי 8443 (#740).

הטסט קורא את מערך ``PKGS`` **מתוך המתקין עצמו** — לא מעתיק את הרשימה —
ומוודא ששלוש החבילות שם, וש-``server/requirements.txt`` (מה שה-CI
מתקין ב-pip) מצמיד את המקבילות שלהן. רשימה שבה חבילה נשרה נופלת כאן,
לא מול סניף חדש.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
INSTALLER = REPO / "deploy" / "setup-boot-server.sh"
REQUIREMENTS = REPO / "server" / "requirements.txt"

#: חבילת apt → שם ההפצה ב-pip (מה ש-requirements.txt מצמיד).
SERVER_APT_TO_PIP = {
    "python3-websockets": "websockets",
    "python3-cryptography": "cryptography",
    "python3-openssl": "pyOpenSSL",
    "python3-qrcode": "qrcode",          # #1150: QR במסך הגדרת ה-MFA
}


def installer_pkgs() -> list[str]:
    """שמות החבילות במערך ``PKGS=(...)`` של המתקין, בלי הערות."""
    text = INSTALLER.read_text(encoding="utf-8")
    body = text.split("\nPKGS=(", 1)[1]
    names: list[str] = []
    for line in body.split("\n"):
        code = line.split("#", 1)[0]     # ההערות עלולות להכיל סוגריים (#690)
        done = ")" in code
        names += code.split(")", 1)[0].split()
        if done:
            break
    return names


def pinned_requirements() -> dict[str, str]:
    """``name==version`` מ-requirements.txt, בלי הערות."""
    pins: dict[str, str] = {}
    for raw in REQUIREMENTS.read_text(encoding="utf-8").split("\n"):
        line = raw.split("#", 1)[0].strip()
        if "==" in line:
            name, version = line.split("==", 1)
            pins[name.strip()] = version.strip()
    return pins


def test_installer_apt_list_has_the_server_runtime_packages() -> None:
    pkgs = installer_pkgs()
    missing = [p for p in SERVER_APT_TO_PIP if p not in pkgs]
    assert not missing, f"חסר ברשימת ה-apt של המתקין: {missing} (יש: {pkgs})"
    assert "dhcpcd-base" in pkgs


def test_requirements_pin_the_same_packages_the_installer_installs() -> None:
    """מה שהמתקין מביא ב-apt מוצמד גם ב-pip — אחרת ה-CI בודק סביבה
    שאינה זו שנפרסת (‏#113 מהצד השני)."""
    pins = pinned_requirements()
    missing = [pip for pip in SERVER_APT_TO_PIP.values() if pip not in pins]
    assert not missing, f"אינו מוצמד ב-requirements.txt: {missing} (יש: {pins})"
