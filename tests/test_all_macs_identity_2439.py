"""#2439 — `all_macs` ב-hello עקף את שער הזהות (#855).

השער בדק רק את `mac`; `registry.lookup` מזהה גם לפי `all_macs` (#524). תחנה
עם חכירה משלה הוסיפה לרשימה MAC של מכונה רשומה אחרת, וקיבלה 200 עם
`known: true`, ה-role, השם וה-`allowed_images` שלה. עכשיו כרטיס רשום מהרשימה
מזהה רק כשהוא אינו נמצא עכשיו במקום אחר (`identity.verify_extra`): חכירה שלו על
כתובת אחרת, או `net_devices` שראה אותו מכתובת אחרת ב-10 הדקות האחרונות.

ו-#524 נשאר: מכונה שרשומה לפי כרטיס שאינו כרטיס האתחול — שאין לו חכירה ולא
נראה מכתובת אחרת לאחרונה — מזוהה כמו תמיד.

בקרה שלילית: עם `server/agent/api.py` ו-`server/agent/identity.py` של הקומיט
הקודם, שני טסטי ההתחזות נופלים (`known: true`); טסטי #524 עוברים בשני המצבים.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from conftest import hello_body, setup_classroom
from test_machine_identity import (MAC1, STATION_IP, identity_server,  # noqa: F401
                                   journal_details)

ATTACKER = "de:ad:be:ef:24:39"
ATTACKER_IP = "10.44.12.66"
SECOND_NIC = "de:ad:be:ef:05:24"      # כרטיס האתחול של מכונה שרשומה לפי כרטיס אחר
SECOND_NIC_IP = "10.44.12.77"


def _lease(server, mac: str, ip: str) -> None:
    with open(server["leases"], "a", encoding="utf-8", newline="\n") as f:
        f.write(f"1789000000 {mac} {ip} X 01:{mac}\n")


def _hello(server, mac: str, ip: str, all_macs: list[str]):
    body = {**hello_body(mac), "all_macs": all_macs}
    response = TestClient(server["app"], client=(ip, 40100)).post("/api/v1/agent/hello", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def _seen(server, mac: str, ip: str, ago_s: int) -> None:
    at = (datetime.now(timezone.utc) - timedelta(seconds=ago_s)).isoformat(timespec="seconds")
    server["ctx"].conn.execute(
        "INSERT OR REPLACE INTO net_devices (mac, ip, first_seen, last_seen) VALUES (?,?,?,?)",
        (mac, ip, at, at))
    server["ctx"].conn.commit()


def _refusals(server) -> list[str]:
    return [d for _u, d in journal_details(server, "identity_refused") if "(hello all_macs)" in d]


def test_the_poc_a_leased_victim_in_all_macs_does_not_identify_the_attacker(identity_server):
    """ה-PoC של 04/11: MAC1 רשום וחכור ל-STATION_IP; התוקף, עם חכירה משלו,
    שולח את ה-MAC שלו ומוסיף את MAC1."""
    setup_classroom(identity_server)
    _lease(identity_server, ATTACKER, ATTACKER_IP)
    answer = _hello(identity_server, ATTACKER, ATTACKER_IP, [ATTACKER, MAC1])
    assert answer["known"] is False and answer["role"] == "unknown"
    assert answer["name"] is None and answer["allowed_images"] == []
    assert _refusals(identity_server) == [
        f"{ATTACKER} from {ATTACKER_IP}: all_macs {MAC1} is leased to {STATION_IP} (hello all_macs)"]


def test_a_victim_seen_recently_from_another_address_does_not_identify_the_attacker(identity_server):
    """בלי חכירה לקורבן (MAC2), אבל ה-hello שלו נרשם לפני דקה מכתובת אחרת."""
    ids = setup_classroom(identity_server)
    _lease(identity_server, ATTACKER, ATTACKER_IP)
    _seen(identity_server, ids["mac2"], "10.44.12.88", ago_s=60)
    answer = _hello(identity_server, ATTACKER, ATTACKER_IP, [ATTACKER, ids["mac2"]])
    assert answer["known"] is False
    assert len(_refusals(identity_server)) == 1


def test_524_a_machine_registered_by_another_nic_is_still_identified(identity_server):
    """בקרה: MAC2 רשום, אין לו חכירה ולא נראה. המכונה עלתה על כרטיס אחר שחכור
    לכתובת שממנה היא פונה — היא מזוהה, בלי סירוב ביומן."""
    ids = setup_classroom(identity_server)
    _lease(identity_server, SECOND_NIC, SECOND_NIC_IP)
    answer = _hello(identity_server, SECOND_NIC, SECOND_NIC_IP, [SECOND_NIC, ids["mac2"]])
    assert answer["known"] is True and answer["name"] == "06"
    assert _refusals(identity_server) == []


def test_524_still_works_when_the_registered_nic_was_seen_long_ago(identity_server):
    """בקרה: אותו דבר, והכרטיס הרשום נראה פעם — לפני יומיים, מכתובת אחרת."""
    ids = setup_classroom(identity_server)
    _lease(identity_server, SECOND_NIC, SECOND_NIC_IP)
    _seen(identity_server, ids["mac2"], "10.44.12.88", ago_s=2 * 86400)
    assert _hello(identity_server, SECOND_NIC, SECOND_NIC_IP,
                  [SECOND_NIC, ids["mac2"]])["known"] is True


def test_524_the_registered_nic_leased_to_the_same_address_identifies(identity_server):
    """בקרה: חכירה של הכרטיס הרשום **על אותה כתובת** — אותה מכונה."""
    setup_classroom(identity_server)
    _lease(identity_server, SECOND_NIC, STATION_IP)
    assert _hello(identity_server, SECOND_NIC, STATION_IP, [SECOND_NIC, MAC1])["known"] is True
