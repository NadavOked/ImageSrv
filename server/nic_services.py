"""השירותים שהשרת נותן דרך כל כרטיס רשת — **והשרת אוכף אותם** (‏#705).

הכרעת נדב (13/10): לכל כרטיס בוחרים בקונסולה אילו שירותים ניתנים דרכו.
"כבוי" אינו הסתרה בממשק — השרת **לא מאזין ולא משדר** על הכרטיס:

| שירות | מה נאכף |
|---|---|
| ‏`console` | מאזין הקונסולה (8081) נקשר לכתובת הכרטיס או לא; כלל nft |
| ‏`monitor` | ה-WebSocket של המוניטור עובר ב-8081 (אין לו מאזין משלו — ‏`monitor.py`); השרת מסרב לו לפי הכתובת המקומית של החיבור |
| ‏`multicast` | ‏`udp-sender` משדר רק על כרטיס שבו השידור דלוק; אחרת הסבב נכשל בשם; כלל nft |
| ‏`netboot` | ‏dnsmasq (‏`except-interface=`/`interface=`), ערוץ הסוכן 8080 נקשר לכתובת הכרטיס; כללי nft ל-67/69/8080 |
| ‏`interserver` | מאזין 8443 (ה-"אתה שם?" של הראשי / ה-mTLS של המשני); כלל nft |

**ברירת המחדל היא היום** (‏`defaults`): כל עוד המפעיל לא שמר אף כרטיס
(`edited` = ‏False) הקוד הישן קושר הכול בדיוק כמו קודם — שדרוג אינו משנה
כלום. כרטיס שלא נשמר מקבל את מה שהשרת נותן דרכו היום: הניהול — קונסולה,
מוניטור ובין-שרתים; ההפצה — שידור ואתחול; כל השאר — "לא הוגדר", הכול כבוי.

**נעילה עצמית.** אי אפשר לכבות את הקונסולה בכרטיס האחרון שהיא פתוחה בו
(409 ‏`last_console`). כיבוי הקונסולה בכרטיס שדרכו המפעיל מחובר (כשיש
כרטיס אחר איתה) דורש `handoff` — ואז השינוי מוחל אחרי שהתשובה יצאה, וחוזר
לבד תוך `WINDOW_SECONDS` אם לא אושר מהכתובת האחרת. זה אותו חלון ואותה
החלטה (‏`netcfg_rollback.decide`: אתחול לפני פקיעה) של ‏#56. ההבדל המכוון:
ההחזרה כאן היא בתהליך השרת ולא ב-`imagectl-netrollback.service` — היחידה
ההיא אינה נוגעת ב-DB בכוונה, וכל מה שנסגר כאן הוא מאזין **של התהליך הזה**.
אם התהליך מת, אין מאזין ממילא; בעלייה הבאה הסמן ב-DB נקרא **לפני** שמאזין
אחד נקשר (`startup`), ושינוי שפג או ששרד אתחול חוזר מיד.

**עיקרון 5.** החלה שנכשלה (מאזין שלא עלה, dnsmasq, nft) מחזירה את ההגדרה
הקודמת ב-DB, מחילה אותה שוב, ואומרת את שני הדברים. "לא הצלחנו לקבוע דרך
איזה כרטיס הגיעה הבקשה" אינו "לא דרך הכרטיס הזה".
"""

from __future__ import annotations

import ipaddress
import json
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Callable

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from . import dhcp, netcfg_rollback
from .db import get_setting, journal, set_setting, set_settings

SERVICES = ("console", "monitor", "multicast", "netboot", "interserver", "ntp")
SERVICES_HE = {"console": "קונסולה", "monitor": "מוניטור", "multicast": "שידור",
               "netboot": "אתחול מהרשת", "interserver": "בין-שרתים", "ntp": "שרת זמן"}
#: מה שמאזין של התהליך נקשר אליו לכל שירות (`plan`).
LISTENER_OF = {"console": "http_console", "netboot": "http_boot",
               "interserver": "interserver"}

SETTING_PREFIX = "nicsvc:"
#: לא `nicsvc:` — שם כרטיס אינו יכול להתחיל ב-"_", אבל LIKE 'nicsvc:%' לא
#: אמור לראות את הסמנים בכלל.
PENDING_KEY = "nicsvc_pending"
LAST_KEY = "nicsvc_last"
WINDOW_SECONDS = netcfg_rollback.WINDOW_SECONDS
#: כמה זמן אחרי התשובה מוחל שינוי שמנתק את המפעיל — כדי שהתשובה תצא קודם.
APPLY_DELAY = 1.0
LOCKOUT = ("זה הכרטיס היחיד שהקונסולה פתוחה בו — כיבוי ינעל את כולם מחוץ "
           "לשרת. הפעל קונסולה בכרטיס אחר קודם, התחבר משם, ואז חזור לכאן.")
REASON_HE = {**netcfg_rollback.REASON_HE, "operator": "המפעיל החזיר",
             "apply_failed": "ההחלה נכשלה"}

Hooks = dict[str, Callable]


class ServiceError(Exception):
    """סירוב לבקשה — סטטוס, הודעה בעברית וקוד יציב לקונסולה."""

    def __init__(self, status: int, message: str, code: str, **extra) -> None:
        super().__init__(message)
        self.status, self.message, self.code, self.extra = status, message, code, extra


@dataclass
class Baseline:
    """על איזה כרטיס כל דבר מאזין **היום** — ממה ש-`main` קשר בעלייה."""

    console: str | None = None       # ‏--console-host (שם כרטיס, או הכרטיס של הכתובת)
    deploy: str | None = None        # כרטיס ההפצה (‏udp-sender ‎--interface, dnsmasq, 8080)
    interserver: str | None = None   # הכרטיס שמאזין 8443 נקשר אליו, אם יש


# --- הלוגיקה הטהורה ----------------------------------------------------------


def canonical(services) -> list[str]:
    """רשימת שירותים מאומתת בסדר הקבוע. שירות לא מוכר — ValueError."""
    if not isinstance(services, (list, tuple)):
        raise ValueError("services חייב להיות רשימה")
    unknown = [s for s in services if s not in SERVICES]
    if unknown:
        raise ValueError(f"שירות לא מוכר: {', '.join(map(str, unknown))}")
    return [s for s in SERVICES if s in services]


def defaults(name: str, base: Baseline) -> list[str]:
    """מה הכרטיס נותן היום, בלי שהמפעיל נגע בו."""
    found = set()
    if name and name == base.console:
        found |= {"console", "monitor"}
    if name and name == base.deploy:
        found |= {"multicast", "netboot"}
    if name and name == base.interserver:
        found.add("interserver")
    return [s for s in SERVICES if s in found]


def effective(names: list[str], saved: dict[str, list[str]],
              base: Baseline) -> dict[str, list[str]]:
    return {n: list(saved[n]) if n in saved else defaults(n, base) for n in names}


def first_ipv4(nic: dict) -> str | None:
    for cidr in nic.get("addresses") or []:
        address = str(cidr).split("/")[0]
        try:
            if ipaddress.ip_address(address).version == 4:
                return address
        except ValueError:
            continue
    return None


def plan(eff: dict[str, list[str]], addresses: dict[str, str | None]) -> dict[str, dict]:
    """‏`hosts`: לכל מאזין (מזהה הפורט) — הכתובות שהוא נקשר אליהן.
    ‏`nics`: לכל שירות — הכרטיסים שבהם הוא דלוק. שני מרחבי שמות, בכוונה:
    ‏"interserver" הוא גם שירות וגם מזהה מאזין."""
    hosts = {}
    for service, listener in LISTENER_OF.items():
        found = [addresses[n] for n in eff if service in eff[n] and addresses.get(n)]
        hosts[listener] = list(dict.fromkeys(found))
    return {"hosts": hosts,
            "nics": {s: [n for n in eff if s in eff[n]] for s in SERVICES}}


def is_loopback(address: str | None) -> bool:
    try:
        return bool(address) and ipaddress.ip_address(address).is_loopback
    except ValueError:
        return False


def via(address: str | None, interfaces: list[dict]) -> str | None:
    """הכרטיס שדרכו הגיעה הבקשה: שם, ‏"lo" ל-loopback, או None כשלא ניתן
    לקבוע. ‏None **אינו** "לא הכרטיס הזה" — הקורא מניח שזה הכרטיס שלו."""
    if is_loopback(address):
        return "lo"
    for nic in interfaces:
        if any(str(a).split("/")[0] == address for a in nic.get("addresses") or []):
            return nic["name"]
    return None


def _now_iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).astimezone().isoformat(timespec="seconds")


# --- המצב השמור + האכיפה -------------------------------------------------------


class NicServices:
    """השירותים לכל כרטיס: קריאה, שמירה, נעילה עצמית, החלה וחזרה.

    ‏hooks: ‏`interfaces` (כמו לשונית ה-DHCP), ‏`now`, ‏`boot_id`,
    ‏`schedule(seconds, fn)` (טיימר), ושלושת שלבי האכיפה — ‏`listeners`,
    ‏`dnsmasq`, ‏`firewall` — כל אחד ‏`fn(self) → שגיאה | None`. שלב שאינו
    מחובר (‏None) הוא **שגיאה**, לא דילוג: שמירה שאינה נאכפת היא "נשמר"
    שאינו "נכנס לתוקף"."""

    def __init__(self, conn, base: Baseline | None = None, hooks: Hooks | None = None):
        self.conn = conn
        self.base = base or Baseline()
        self.hooks: dict = {
            "interfaces": dhcp.list_interfaces, "now": time.time,
            "boot_id": netcfg_rollback.boot_id, "schedule": _schedule,
            "listeners": None, "dnsmasq": None, "firewall": None,
            "apply_delay": APPLY_DELAY, **(hooks or {})}
        self._lock = threading.RLock()
        self.reverted_at_startup = False

    # -- קריאה --

    def saved(self) -> tuple[dict[str, list[str]], list[str]]:
        """‏(שמור, שמות שלא ניתן לקרוא). רשומה פגומה נופלת לברירת המחדל —
        ונאמרת (`unreadable`) ולא נבלעת."""
        rows = self.conn.execute("SELECT key, value FROM settings WHERE key LIKE ?",
                                 (SETTING_PREFIX + "%",)).fetchall()
        found, bad = {}, []
        for row in rows:
            name = row["key"][len(SETTING_PREFIX):]
            try:
                found[name] = canonical(json.loads(row["value"])["services"])
            except (ValueError, KeyError, TypeError):
                bad.append(name)
        return found, bad

    def edited(self) -> bool:
        return self.conn.execute("SELECT 1 FROM settings WHERE key LIKE ? LIMIT 1",
                                 (SETTING_PREFIX + "%",)).fetchone() is not None

    def interfaces(self) -> list[dict]:
        return list(self.hooks["interfaces"]())

    def state(self, interfaces: list[dict] | None = None) -> tuple[list[dict], dict[str, list[str]]]:
        interfaces = self.interfaces() if interfaces is None else interfaces
        saved, _bad = self.saved()
        names = [n["name"] for n in interfaces] + [n for n in saved if n not in
                                                   {i["name"] for i in interfaces}]
        return interfaces, effective(names, saved, self.base)

    def current_plan(self) -> dict[str, dict]:
        interfaces, eff = self.state()
        return plan(eff, {n["name"]: first_ipv4(n) for n in interfaces})

    def firewall_services(self, deploy_if: str | None = None) -> list | None:
        """‏[(כרטיס, [שירותים])] לחומת האש — או None כשהמפעיל לא ערך (היום)."""
        if not self.edited():
            return None
        base = self.base
        if deploy_if and deploy_if != base.deploy:
            base = Baseline(base.console, deploy_if, base.interserver)
        interfaces = self.interfaces()
        saved, _bad = self.saved()
        eff = effective([n["name"] for n in interfaces], saved, base)
        return [(n, s) for n, s in eff.items()]

    def netboot(self) -> tuple[list[str], list[str]] | None:
        """‏(כרטיסים עם אתחול, כל הכרטיסים) ל-dnsmasq — או None (היום)."""
        if not self.edited():
            return None
        interfaces, eff = self.state()
        return [n for n, s in eff.items() if "netboot" in s], [n["name"] for n in interfaces]

    def pending(self) -> dict | None:
        raw = get_setting(self.conn, PENDING_KEY)
        if not raw:
            return None
        try:
            data = json.loads(raw)
            float(data["deadline"])
            str(data["interface"])
            return data
        except (ValueError, KeyError, TypeError):
            # סמן שאי אפשר לפענח אינו "אין סמן": הקונסולה אומרת אותו.
            return {"corrupt": True, "interface": "", "deadline": 0, "boot": ""}

    def pending_view(self, now: float | None = None) -> dict | None:
        marker = self.pending()
        if marker is None:
            return None
        now = self.hooks["now"]() if now is None else now
        return {"interface": marker.get("interface", ""),
                "previous": marker.get("before", marker.get("previous")) or [],
                "applied": marker.get("applied", []),
                "seconds_left": max(0, int(float(marker["deadline"]) - now)),
                "window_seconds": WINDOW_SECONDS, "corrupt": bool(marker.get("corrupt")),
                "apply_error": marker.get("apply_error")}

    def listening(self, address: str | None, bound: dict | None) -> dict:
        """לכל שירות שיש לו מאזין: האם השרת **באמת** מאזין בכתובת של הכרטיס —
        מהסוקטים (`Listeners.bound`), לא מההגדרה. ‏None = לא נמדד (אין מאזין
        לשירות — שידור; אין מנהל מאזינים בתהליך; כרטיס בלי כתובת)."""
        out = {s: None for s in SERVICES}
        if not bound or not address:
            return out
        for service, listener in (*LISTENER_OF.items(), ("monitor", "http_console")):
            if listener in bound:
                hosts = bound[listener]
                out[service] = address in hosts or "0.0.0.0" in hosts
        return out

    def view(self, local_address: str | None = None, peer: str | None = None) -> dict:
        interfaces = self.interfaces()
        saved, bad = self.saved()
        names = [n["name"] for n in interfaces] + [n for n in saved if n not in
                                                   {i["name"] for i in interfaces}]
        eff = effective(names, saved, self.base)
        present = {n["name"]: n for n in interfaces}
        bound = None
        if self.hooks.get("bound") is not None:
            try:
                bound = self.hooks["bound"]()
            except Exception:                                     # noqa: BLE001
                bound = None                                       # "לא נמדד", לא "לא מאזין"
        rows = []
        for name in names:
            source = "saved" if name in saved else "default"
            address = first_ipv4(present.get(name, {}))
            rows.append({"name": name, "present": name in present, "address": address,
                         "services": eff[name], "source": source,
                         "configured": source == "saved" or bool(eff[name]),
                         "unreadable": name in bad,
                         "listening": self.listening(address, bound)})
        try:
            last = json.loads(get_setting(self.conn, LAST_KEY) or "null")
        except ValueError:
            last = None
        return {"services": list(SERVICES), "edited": self.edited(),
                "you": {"interface": via(local_address, interfaces), "address": local_address,
                        "peer": peer},
                "nics": rows, "pending": self.pending_view(), "last": last,
                "window_seconds": WINDOW_SECONDS}

    # -- שערי זמן-ריצה --

    def refused(self, service: str, local_address: str | None) -> str | None:
        """שער לשירות שאין לו מאזין משלו (המוניטור): הודעת סירוב או None."""
        if not self.edited() or is_loopback(local_address):
            return None
        interfaces, eff = self.state()
        nic = via(local_address, interfaces)
        if nic is None:
            return (f"לא ניתן לקבוע דרך איזה כרטיס הגיע החיבור ({local_address or '?'}) — "
                    f"{SERVICES_HE[service]} נחסם")
        if service not in eff.get(nic, []):
            return f"{SERVICES_HE[service]} כבוי בכרטיס {nic} (רשת ← חיבורים פיזיים ← שירותים)"
        return None

    def multicast_refused(self, interface: str | None) -> str | None:
        """שער השולח: שידור על כרטיס שבו הוא כבוי — סבב שנכשל בשם."""
        if not interface or not self.edited():
            return None
        _interfaces, eff = self.state()
        if "multicast" not in eff.get(interface, []):
            return (f"שידור כבוי בכרטיס {interface} — הפעל אותו ברשת ← חיבורים "
                    "פיזיים ← שירותים, או השתמש בכרטיס אחר")
        return None

    # -- שמירה --

    def set(self, name: str, services, *, user_id="", local_address: str | None = None,
            handoff: bool = False, console_url: Callable[[str], str] | None = None) -> dict:
        try:
            dhcp.validate_name(name)
            wanted = canonical(services)
        except ValueError as exc:
            raise ServiceError(400, str(exc), "invalid") from exc
        with self._lock:
            if self.pending() is not None:
                raise ServiceError(409, "שינוי קודם ברשת ממתין לאישור — אשר או החזר "
                                   "אותו קודם", "pending")
            interfaces = self.interfaces()
            saved, _bad = self.saved()
            present = [n["name"] for n in interfaces]
            if name not in present and name not in saved:
                raise ServiceError(404, f"אין כרטיס {name} במערכת", "unknown_nic")
            names = present + [n for n in saved if n not in present]
            before = effective(names, saved, self.base)
            after = {**before, name: wanted}
            if before[name] == wanted and name in saved:
                return {"ok": True, "changed": False, "nic": name, "services": wanted}
            off_console = "console" in before[name] and "console" not in wanted
            others = [n for n in present if n != name and "console" in after[n]]
            if off_console and not others:
                raise ServiceError(409, LOCKOUT, "last_console")
            path = via(local_address, interfaces)
            risky = off_console and path in (None, name)
            addresses = {n["name"]: first_ipv4(n) for n in interfaces}
            go = [console_url(addresses[n]) if console_url else addresses[n]
                  for n in others if addresses.get(n)]
            if risky and not handoff:
                raise ServiceError(409, f"אתה מחובר דרך {name} — כיבוי הקונסולה בו "
                                   "ינתק אותך; נדרש אישור מסירה", "needs_handoff", go=go)
            previous = saved.get(name)
            detail = f"{name}: {','.join(before[name]) or '-'} → {','.join(wanted) or '-'}"
            self._write(name, wanted)
            journal(self.conn, "nic_services_set", detail, user_id or "")
            if risky:
                now = self.hooks["now"]()
                # ‏previous = הרשומה הגולמית (None = ברירת מחדל) — מה שהחזרה כותבת;
                # ‏before = מה שהכרטיס נתן בפועל — מה שהקונסולה מציגה ("קונסולה כובתה").
                marker = {"interface": name, "previous": previous, "before": before[name],
                          "applied": wanted,
                          "deadline": now + WINDOW_SECONDS, "boot": self.hooks["boot_id"](),
                          "armed_at": _now_iso(now), "by": user_id}
                set_setting(self.conn, PENDING_KEY, json.dumps(marker, ensure_ascii=False))
                journal(self.conn, "nic_services_armed",
                        f"{name} window={WINDOW_SECONDS}s", user_id or "")
                self.hooks["schedule"](self.hooks["apply_delay"], self._apply_later)
                self.hooks["schedule"](WINDOW_SECONDS + 0.5, self.tick)
                return {"ok": True, "changed": True, "nic": name, "services": wanted,
                        "pending": self.pending_view(now), "go": go}
            errors = self.enforce()
            if errors:
                self._write(name, previous)
                again = self.enforce()
                journal(self.conn, "nic_services_apply_failed",
                        f"{detail} errors: {' · '.join(errors)}"
                        + (f" · restore: {' · '.join(again)}" if again else " · restored"),
                        user_id or "")
                raise ServiceError(502, "ההחלה נכשלה והגדרת הכרטיס הקודמת הוחזרה: "
                                   + " · ".join(errors), "apply_failed",
                                   errors=errors, restore_errors=again)
            return {"ok": True, "changed": True, "nic": name, "services": wanted}

    def _write(self, name: str, services: list[str] | None) -> None:
        if services is None:
            set_settings(self.conn, {}, remove=(SETTING_PREFIX + name,))
        else:
            set_setting(self.conn, SETTING_PREFIX + name, json.dumps({"services": services}))

    def enforce(self) -> list[str]:
        """שלושת שלבי האכיפה, כל אחד נאמר בנפרד. ריק = הכול הוחל."""
        errors = []
        for key, label in (("listeners", "מאזינים"), ("dnsmasq", "אתחול מהרשת (dnsmasq)"),
                           ("timesync", "שרת זמן (chrony)"), ("firewall", "חומת אש")):
            step = self.hooks.get(key)
            if step is None:
                errors.append(f"{label}: אין מנגנון אכיפה בתהליך הזה")
                continue
            try:
                error = step(self)
            except Exception as exc:                              # noqa: BLE001
                error = str(exc) or type(exc).__name__
            if error:
                errors.append(f"{label}: {error}")
        return errors

    # -- המסירה: החלה דחויה, אישור, חזרה --

    def _apply_later(self) -> None:
        with self._lock:
            marker = self.pending()
            if marker is None or marker.get("corrupt"):
                return
            errors = self.enforce()
            if errors:
                marker["apply_error"] = " · ".join(errors)
                set_setting(self.conn, PENDING_KEY, json.dumps(marker, ensure_ascii=False))
                self._revert(marker, "apply_failed", None)

    def confirm(self, user_id="") -> dict:
        with self._lock:
            marker = self.pending()
            now = self.hooks["now"]()
            if marker is None:
                raise ServiceError(409, "אין שינוי ברשת שממתין לאישור — ייתכן שהוא "
                                   "כבר חזר. בדוק את היומן.", "none_pending")
            if marker.get("corrupt"):
                raise ServiceError(409, "סמן ההמתנה פגום — השתמש ב'החזר עכשיו'", "corrupt")
            if now >= float(marker["deadline"]):
                raise ServiceError(409, "חלון האישור נסגר — ההגדרה הקודמת חוזרת", "expired")
            set_settings(self.conn, {}, remove=(PENDING_KEY,))
            if self.pending() is not None:
                raise ServiceError(500, "סמן ההמתנה לא נמחק — השינוי לא אושר ויחזור "
                                   "בתום החלון", "not_cleared")
            journal(self.conn, "nic_services_confirmed", marker["interface"], user_id or "")
            self._last("kept", marker["interface"], "", [], now)
            return {"ok": True, "interface": marker["interface"]}

    def revert_now(self, user_id="") -> dict:
        with self._lock:
            marker = self.pending()
            if marker is None:
                raise ServiceError(409, "אין שינוי ברשת שממתין לאישור", "none_pending")
            errors = self._revert(marker, "operator", user_id or "")
            return {"ok": not errors, "interface": marker.get("interface", ""), "errors": errors}

    def tick(self, now: float | None = None) -> str | None:
        """הטיימר: סיבת חזרה (וחוזרים), או None."""
        with self._lock:
            marker = self.pending()
            if marker is None:
                return None
            now = self.hooks["now"]() if now is None else now
            reason = self._decide(marker, now)
            if reason is None:
                return None
            self._revert(marker, reason, None)
            return reason

    def _decide(self, marker: dict, now: float) -> str | None:
        if marker.get("corrupt"):
            return netcfg_rollback.REASON_EXPIRED
        return netcfg_rollback.decide(
            SimpleNamespace(boot=str(marker.get("boot", "")), deadline=float(marker["deadline"])),
            now, self.hooks["boot_id"]())

    def _revert(self, marker: dict, reason: str, user_id, *, apply: bool = True) -> list[str]:
        name = marker.get("interface", "")
        if name:
            self._write(name, marker.get("previous"))
        set_settings(self.conn, {}, remove=(PENDING_KEY,))
        errors = self.enforce() if apply else []
        detail = f"{name or '?'} reason={reason}"
        if marker.get("apply_error"):
            detail += f" apply: {marker['apply_error']}"
        if errors:
            detail += f" errors: {' · '.join(errors)}"
        journal(self.conn, "nic_services_reverted", detail, user_id or "")
        self._last("reverted", name, reason,
                   ([marker["apply_error"]] if marker.get("apply_error") else []) + errors,
                   self.hooks["now"]())
        return errors

    def _last(self, event: str, name: str, reason: str, errors: list[str], now: float) -> None:
        set_setting(self.conn, LAST_KEY, json.dumps(
            {"event": event, "interface": name, "reason": reason,
             "reason_he": REASON_HE.get(reason, ""), "errors": errors,
             "at": _now_iso(now)}, ensure_ascii=False))

    def startup(self) -> str | None:
        """בעליית השרת, **לפני** שמאזין אחד נקשר: שינוי שפג או ששרד אתחול
        חוזר עכשיו (ב-DB בלבד — הקשירה שאחרי כבר תשקף אותו); שינוי שעוד
        בחלון — הטיימר ממשיך מאיפה שעצר."""
        marker = self.pending()
        if marker is None:
            return None
        now = self.hooks["now"]()
        reason = self._decide(marker, now)
        if reason is not None:
            self._revert(marker, reason, None, apply=False)
            self.reverted_at_startup = True
            return reason
        self.hooks["schedule"](max(0.0, float(marker["deadline"]) - now) + 0.5, self.tick)
        return None


def _schedule(seconds: float, fn: Callable[[], object]) -> None:
    timer = threading.Timer(seconds, fn)
    timer.daemon = True
    timer.start()


# --- ה-API (‏/api/console/net/services, ‏admin בלבד) -----------------------------


def create_nic_services_router(ctx):
    """‏`def` ולא `async def`: ההחלה ממתינה למנהל המאזינים, שרץ על לולאת
    האירועים — endpoint אסינכרוני היה חוסם את הלולאה שהוא ממתין לה.
    ‏`Request` מיובא בראש המודול ולא כאן: עם `from __future__ import
    annotations` אנוטציה שמיובאת בתוך פונקציה הופכת כל endpoint ל-422."""
    from . import auth

    router = APIRouter(prefix="/api/console/net/services")
    _current_user, admin_only = auth.dependencies(ctx.conn)

    def service() -> NicServices:
        found = getattr(ctx, "nic_services", None)
        if found is None:
            raise ServiceError(503, "אין ניהול שירותים לכרטיסים בתהליך הזה", "unavailable")
        return found

    def refused(exc: ServiceError) -> JSONResponse:
        return JSONResponse({"detail": exc.message, "code": exc.code, **exc.extra},
                            status_code=exc.status)

    def local(request: Request) -> tuple[str, int]:
        host, port = request.scope.get("server") or ("", 0)
        return str(host or ""), int(port or 0)

    @router.get("")
    def read(request: Request, user=Depends(admin_only)):
        del user
        try:
            peer = request.client.host if request.client else None
            return service().view(local(request)[0], peer)
        except ServiceError as exc:
            return refused(exc)

    @router.post("/confirm")
    def confirm(user=Depends(admin_only)):
        try:
            return service().confirm(user[0])
        except ServiceError as exc:
            return refused(exc)

    @router.post("/revert")
    def revert(user=Depends(admin_only)):
        try:
            return service().revert_now(user[0])
        except ServiceError as exc:
            return refused(exc)

    @router.put("/{name}")
    def put(name: str, body: dict, request: Request, user=Depends(admin_only)):
        address, port = local(request)
        scheme = request.url.scheme

        def console_url(addr: str) -> str:
            return f"{scheme}://{addr}:{port}" if port else f"{scheme}://{addr}"
        try:
            return service().set(name, body.get("services"), user_id=user[0],
                                  local_address=address, handoff=bool(body.get("handoff")),
                                  console_url=console_url)
        except ServiceError as exc:
            return refused(exc)

    return router
