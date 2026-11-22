"""The saved DHCP configs, read from the settings (#1013), and what the proxy instance may see.

Split out of `console/dhcp.py` (#2531): `net/deploy_net.py` reads them too, and importing the
console module for two functions closed an import cycle. `console.dhcp` re-exports both names.
"""
from __future__ import annotations

from server.core import capabilities
from server.net import dhcp


def load_configs(conn) -> list[dhcp.InterfaceConfig]:
    rows = conn.execute(
        "SELECT key, value FROM settings WHERE key LIKE ?",
        (dhcp.SETTING_PREFIX + "%",),
    ).fetchall()
    return [
        dhcp.InterfaceConfig.from_json(r["key"][len(dhcp.SETTING_PREFIX):], r["value"])
        for r in rows
    ]


def proxy_configs(configs: list[dhcp.InterfaceConfig]) -> list[dhcp.InterfaceConfig]:
    """מה שאינסטנס ה-proxy רשאי לראות. הכרעת נדב 11/10: proxy שייך לכיתות —
    כשהן כבויות (v1) גם רשומת proxy=true שנשמרה לפני הגידור אינה מוחלת
    (#1303), ולא רק ה-PUT מסרב לכתוב חדשה."""
    return configs if capabilities.classrooms() else []
