"""כתובת השרת בתפריט נגזרת מהחיבור עצמו (issue #39).

תחנה מחוץ לווילן ההפצה (תרחיש 3 באפיון, בדיקות 3.4–3.5) מושכת את התפריט
דרך ‎$net_default_server — הכתובת שבאמת ענתה לה. עד #39 התפריט שהיא קיבלה
הפנה את linux/initrd ואת imagesrv.server לכתובת ההפצה הקבועה, שלא נגישה
מהרשת שלה: משיכת הקרנל נכשלה והמסלול נפל ל-chain_local.

הבדיקות כאן מריצות את אפליקציית ה-ASGI ישירות, כי מה שנבדק הוא בדיוק
scope["server"] — ה-sockname של החיבור שהתקבל, שאותו uvicorn ממלא.
מכוון: לא כותרת Host, שנשלטת בידי הלקוח.
"""

import asyncio

import pytest

from boot.grub_menu import GrubConfig
from boot.http import create_boot_asgi

CONFIGURED = "10.44.12.10:8080"
CONFIG = GrubConfig(server_base=f"http://{CONFIGURED}")

#: תשובת שרת (ממשק 3) שמייצרת רשומת סוכן — שם מופיעות שורות
#: ה-linux/initrd וגם imagesrv.server, כלומר כל שלוש הכתובות.
ANSWER = {"schema": 1, "known": True, "role": "classroom", "task": {"type": "deploy"}}


class _Absent:
    """מבדיל בין "השרת לא מילא server" לבין "מילא None"."""


#: מפתח scope["server"] חסר לגמרי.
_ABSENT = _Absent()


def fetch_menu(config: GrubConfig = CONFIG, server=("10.44.12.10", 8080)) -> str:
    """מריץ בקשת ‎/boot/menu אחת מול האפליקציה הגולמית ומחזיר את הגוף."""
    app = create_boot_asgi(resolve=lambda mac, ip: dict(ANSWER), config=config)

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/menu",
        "root_path": "",
        "query_string": b"mac=b4:2e:99:07:1a:c4",
        "headers": [],
        "client": ("10.10.10.31", 51321),
    }
    if server is not _ABSENT:
        scope["server"] = server

    received = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        received.append(message)

    asyncio.run(app(scope, receive, send))
    body = b"".join(
        m.get("body", b"") for m in received if m["type"] == "http.response.body"
    )
    return body.decode("ascii")


def test_menu_points_at_the_address_the_station_actually_reached():
    text = fetch_menu(server=("10.10.10.8", 8080))

    assert "linux (http,10.10.10.8:8080)/boot/vmlinuz" in text
    assert "initrd (http,10.10.10.8:8080)/boot/initrd.img" in text
    assert "imagesrv.server=http://10.10.10.8:8080" in text
    # שום שריד של כתובת ההפצה, שמהרשת הזו לא נגישה בכלל.
    assert CONFIGURED not in text


def test_deployment_vlan_output_is_unchanged():
    """הווילן הרגיל: ה-scope תואם לתצורה, ולכן הפלט זהה לקוד הישן."""
    assert fetch_menu(server=("10.44.12.10", 8080)) == fetch_menu(server=_ABSENT)


@pytest.mark.parametrize(
    "server",
    [
        _ABSENT,                    # השרת לא מילא כלום
        None,
        ("2001:db8::1", 8080),      # IPv6 — התחביר של GRUB לא סובל אותו כאן
        ("testserver", 80),         # שם ולא כתובת
        ("10.10.10.8", None),       # פורט חסר
        ("10.10.10.8",),            # לא זוג
        "10.10.10.8:8080",          # לא tuple בכלל
        ("999.1.1.1", 8080),        # לא כתובת תקינה
        ("10.10.10.8", 0),          # פורט מחוץ לתחום
        ("10.10.10.8", "8080"),     # פורט כמחרוזת
    ],
    ids=["absent", "none", "ipv6", "hostname", "no-port", "short", "string",
         "bad-ip", "port-zero", "port-string"],
)
def test_anything_unusable_falls_back_to_the_configured_address(server):
    text = fetch_menu(server=server)

    assert f"linux (http,{CONFIGURED})/boot/vmlinuz" in text
    assert f"imagesrv.server=http://{CONFIGURED}" in text


def test_only_the_server_address_is_replaced():
    """kernel_path, initrd_path ו-extra_cmdline נשארים מהתצורה."""
    config = GrubConfig(
        server_base=f"http://{CONFIGURED}",
        kernel_path="/boot/vmlinuz-lab",
        initrd_path="/boot/initrd-lab.img",
        extra_cmdline=("console=ttyS0,115200",),
    )
    text = fetch_menu(config=config, server=("10.10.10.8", 8080))

    assert "linux (http,10.10.10.8:8080)/boot/vmlinuz-lab" in text
    assert "initrd (http,10.10.10.8:8080)/boot/initrd-lab.img" in text
    assert "console=ttyS0,115200" in text


def test_derived_menu_keeps_the_kernel_command_line_clean():
    """הכתובת מהחיבור לא פותחת פתח לפרטי משימה בשורת הקרנל."""
    text = fetch_menu(server=("10.10.10.8", 8080))

    cmdline = next(line for line in text.splitlines() if " linux (http," in line)
    assert "deploy" not in cmdline
    assert "classroom" not in cmdline
    assert "imagesrv.mode=recovery" not in cmdline
    assert text.isascii()
