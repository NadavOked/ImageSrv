"""לשונית "רשת", הצד השני של הכרטיס: איך השרת עצמו מחובר (‏#55–#57).

‏`console_dhcp.py` מגדיר מה השרת **מחלק**; כאן מגדירים את הכתובת שלו
עצמו, את השער, את ה-DNS ואת הנתיבים הסטטיים — ל-`interfaces.d`, כלומר
ששורדים אתחול. זה מה ש-#50 ביקש: `ip route add` שלא נעלם.

שלוש שכבות, בדיוק כפי שה-PRD מונה אותן:

1. **תצוגה מקדימה** — הקובץ שייכתב, לפני ואחרי, לפני שנוגעים במשהו.
2. **הקלדת שם הכרטיס** — שינוי כתובת הוא הרסני-בפועל (עיקרון 7).
3. **החזרה אוטומטית** כשנוגעים במה שיכול לנתק את הקונסולה — לא
   תהליכון כאן אלא יחידת systemd (‏`netcfg_rollback.py`), כי שינוי
   שהפיל את הרשת מפיל לפעמים גם את השרת.

ו**ההצלחה נקבעת בקריאה חוזרת בלבד** (עיקרון 5): ‏`ip addr`, ‏`ip route`
ו-`/etc/resolv.conf`, ולא ההגדרה ששמרנו. ‏"כתבנו את הקובץ", "ההגדרה
הוחלה" ו"הכתובת השתנתה" הם שלושה מצבים שונים.
"""

from __future__ import annotations

import socket
import time
from pathlib import Path
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Request

from server.console import auth
from server.net import deploy_net, dhcp, netcfg, netcfg_host, netcfg_rollback
from server.agent.api import ServerContext
from server.db import get_setting, journal, set_setting, set_settings

Hooks = dict[str, Callable]


#: ‏#1263: איפה המתקין רושם את כרטיס הניהול (‏`servers_if=`, ‏server/wizard).
FIRSTBOOT_STATUS = Path("/etc/imagesrv/firstboot.status")
#: ההגדרה שמסך השרת (DCUI) כותב כשהמפעיל מאשר שם כרטיס ניהול.
MANAGEMENT_SETTING = "dcui:management_interface"


def installed_management(status_file: Path = FIRSTBOOT_STATUS) -> str:
    """כרטיס הניהול שנבחר בהתקנה, או ריק כשלא נרשם/לא נקרא.

    ריק אינו "אין כרטיס ניהול" — הוא "לא ידוע", והקונסולה מציגה אותו
    כך ואינה מנחשת לפי איזה כרטיס נושא כתובת (עיקרון 5)."""
    try:
        lines = status_file.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    for line in lines:
        key, _, value = line.partition("=")
        if key.strip() == "servers_if":
            return value.strip()
    return ""


def management_interface(conn, installed: Callable[[], str]) -> str:
    """כרטיס הניהול (‏#1263): מה שמסך השרת אישר, אחרת מה שהמתקין רשם.
    ריק = לא ידוע. מקום אחד — ראוטר הכתובות וראוטר ה-DHCP קוראים לזה."""
    try:
        recorded = installed() or ""
    except Exception:                                  # noqa: BLE001 — כוונה
        recorded = ""
    return get_setting(conn, MANAGEMENT_SETTING) or recorded


def default_hooks() -> Hooks:
    return {
        "netcfg_management": installed_management,
        **netcfg_host.default_hooks(),
        # אותה רשימת כרטיסים של לשונית ה-DHCP — שני הצדדים של אותו
        # כרטיס חייבים להסכים מי קיים במכונה.
        "interfaces": dhcp.list_interfaces,
        "netcfg_now": time.time,
        "netcfg_boot_id": netcfg_rollback.boot_id,
        # ‏#1088: השם שלקוח DHCP שולח (אופציה 12) — כדי שה-DNS של המכללה
        # יפתור `imagesrv` לכתובת שהתקבלה.
        "hostname": socket.gethostname,
        # מאיזו כתובת של השרת הגיעה הבקשה. ‏uvicorn שם ב-scope את
        # ה-sockname של החיבור — כלומר הכתובת שהקונסולה באמת מדברת
        # אליה, ולא זו שהוגדרה בשורת הפקודה.
        "netcfg_local_address": lambda request: (request.scope.get("server")
                                                 or ("", 0))[0],
    }


# --- הפירורים של זרוע ההחזרה → שורות יומן ------------------------------------


def drain_crumbs(ctx, state_dir, hooks: Hooks | None = None) -> int:
    """ממיר כל פירור שזרוע ההחזרה השאירה לשורת יומן, ומוחק אותו.

    זו ההשלמה של #56: ההחזרה בעלייה רצה **כשהשרת לא רץ**, ולכן אין לה
    חיבור ל-DB. היא משאירה פירור, וכאן הוא הופך לעברית — עם **זמן
    ההחזרה** ולא זמן הקריאה. בלי זה מגלים שהשינוי לא נתפס רק כשמשהו
    מפסיק לעבוד, וזה עלול להיות חודשים אחר כך.

    נקרא בעליית השרת וגם בכל קריאת מצב, כדי שהחזרה שקרתה בזמן שהשרת
    חי תגיע ליומן מיד ולא רק בהפעלה הבאה.
    """
    crumbs = netcfg_rollback.read_crumbs(state_dir)
    for crumb in crumbs:
        if crumb is None or not crumb.get("at"):
            journal(ctx.conn, "net_rollback_unreadable", "")
            continue
        detail = (f"{crumb.get('interface') or '?'} at={crumb['at']} "
                  f"reason={crumb.get('reason') or '?'}")
        errors = crumb.get("errors") or []
        if errors:
            detail += f" errors={len(errors)}: {' · '.join(str(e) for e in errors)[:200]}"
        journal(ctx.conn, "net_rollback", detail)
        # ה-DB חייב לחזור יחד עם המכונה: הגדרה שמוצגת בקונסולה ואינה
        # זו שעל הכרטיס היא בדיוק אותו שקר בכיוון ההפוך.
        if crumb.get("setting") is not None and crumb.get("interface"):
            set_setting(ctx.conn, netcfg.SETTING_PREFIX + crumb["interface"],
                        crumb["setting"])
    if crumbs:
        netcfg_rollback.clear_crumbs(state_dir)
    return len(crumbs)


def create_netcfg_router(ctx: ServerContext, state_dir,
                         hooks: Hooks | None = None,
                         dhcp_steps: dict | None = None) -> APIRouter:
    """‏`dhcp_steps` (‏#1263): שלבי ההדלקה של ראוטר ה-DHCP
    (‏`create_dhcp_router(...).dhcp_steps`) — בלעדיהם אין `…/deploy`."""
    router = APIRouter(prefix="/api/console/net/config")
    _current_user, admin_only = auth.dependencies(ctx.conn)
    hooks = {**default_hooks(), **(hooks or {})}

    def load(name: str) -> netcfg.NetConfig:
        return netcfg.NetConfig.from_json(
            name, get_setting(ctx.conn, netcfg.SETTING_PREFIX + name))

    def all_configs() -> list[netcfg.NetConfig]:
        rows = ctx.conn.execute(
            "SELECT key, value FROM settings WHERE key LIKE ?",
            (netcfg.SETTING_PREFIX + "%",)).fetchall()
        return [netcfg.NetConfig.from_json(
            r["key"][len(netcfg.SETTING_PREFIX):], r["value"]) for r in rows]

    def dhcp_of(name: str) -> dhcp.InterfaceConfig:
        return dhcp.InterfaceConfig.from_json(
            name, get_setting(ctx.conn, dhcp.SETTING_PREFIX + name))

    def marker():
        return netcfg_rollback.read_pending(state_dir)

    def management() -> str:
        return management_interface(ctx.conn, hooks["netcfg_management"])

    def active_interface(request: Request, live: dict) -> str | None:
        """הכרטיס שדרכו הגיעה הבקשה — או None כשלא ניתן לקבוע.

        ‏None אינו "לא הכרטיס הזה". הקורא מתייחס אליו כאל *כל* כרטיס
        (‏`touches_console`), כי מי שלא יודע דרך מה הוא מחובר חייב
        להניח שהוא מנתק את עצמו.
        """
        try:
            address = hooks["netcfg_local_address"](request)
        except Exception:                              # noqa: BLE001 — כוונה
            return None
        if not address:
            return None
        for nic in live.values():
            if any(a.split("/")[0] == address for a in nic.get("addresses") or []):
                return nic["name"]
        return None

    def touches_console(name: str, before: netcfg.NetConfig,
                        after: netcfg.NetConfig, active: str | None) -> bool:
        """האם השינוי יכול לנתק את הקונסולה — ולכן חייב החזרה אוטומטית.

        שער, ‏DNS ונתיבים נחשבים "נגיעה בכרטיס הפעיל" גם על כרטיס אחר
        (‏#57): כולם משנים דרך מי הקונסולה נענית.
        """
        if active is None or active == name:
            return True
        return any(f in netcfg.REACH_FIELDS
                   for f in netcfg.changed(before, after))

    def bond_of(name: str) -> netcfg.NetConfig | None:
        """‏#1264: ה-bond שהכרטיס חבר בו, או None."""
        return next((c for c in all_configs() if name in c.slaves), None)

    def gaps_of(cfg: netcfg.NetConfig, state: netcfg_host.LiveState,
                bond: netcfg_host.BondState | None = None) -> list[str]:
        """הקריאה החוזרת: הכתובת, ול-bond (‏#1264) גם מה שהקרנל מדווח עליו."""
        gaps = netcfg_host.mismatches(cfg, state)
        if cfg.slaves:
            bond = bond if bond is not None else hooks["netcfg_bond_state"](cfg.name)
            gaps += netcfg_host.bond_mismatches(cfg, bond, state)
        return gaps

    def bond_view(cfg: netcfg.NetConfig, bond: netcfg_host.BondState) -> dict:
        """‏#1264: מצב רשת ההפצה על שני כרטיסים, לפי `/proc/net/bonding`.

        לכל חבר: ‏`active` (חבר, ויש קישור) · ‏`down` (חבר, הכבל/הפורט נפל)
        · ‏`missing` (אינו חבר) · ‏`unchecked` (לא נקרא — **לא** "פעיל")."""
        members = []
        for index, slave in enumerate(cfg.slaves):
            if not bond.checked:
                member = "unchecked"
            elif slave not in bond.slaves:
                member = "missing"
            else:
                member = "active" if bond.slaves[slave] == "up" else "down"
            members.append({"name": slave, "role": index + 1, "state": member})
        states = [m["state"] for m in members]
        up = states.count("active")
        overall = ("unchecked" if not bond.checked else
                   "missing" if "missing" in states else
                   "ok" if up == len(states) else "degraded" if up else "down")
        return {"name": cfg.name, "mode": netcfg.BOND_MODE, "checked": bond.checked,
                "reason": bond.reason, "state": overall, "members": members}

    def state_view(cfg: netcfg.NetConfig, live: dict | None,
                   state: netcfg_host.LiveState,
                   bond: netcfg_host.BondState | None = None) -> dict:
        return {
            "name": cfg.name, "mode": cfg.mode, "mode_he": netcfg.MODES_HE[cfg.mode],
            "address": cfg.address, "netmask": cfg.netmask,
            "gateway": cfg.gateway, "dns": cfg.dns,
            "routes": [r.__dict__ for r in cfg.routes],
            "slaves": cfg.slaves,
            "present": live is not None,
            "state": (live or {}).get("state", "missing"),
            # הראיה, ולא ההגדרה: מה ש-`ip addr` מראה עכשיו.
            "live_addresses": state.addresses.get(cfg.name, []) if state.checked else [],
            "mismatches": gaps_of(cfg, state, bond),
        }

    def rollback_view(now: float) -> dict:
        pending, armed = marker(), hooks["netcfg_timer_active"]()
        return {
            "armed": bool(armed[0]), "armed_detail": armed[1],
            "unit": netcfg_host.ROLLBACK_TIMER,
            "window_seconds": netcfg_rollback.WINDOW_SECONDS,
            "pending": pending is not None,
            "interface": pending.interface if pending else "",
            "seconds_left": max(0, int(pending.deadline - now)) if pending else 0,
            "expired": netcfg_rollback.expired(pending, now),
            "corrupt": netcfg_rollback.corrupt_pending(state_dir),
        }

    @router.get("")
    def read(request: Request, user=Depends(admin_only)):
        """ההגדרה של כל כרטיס, ולצידה מה שנקרא בחזרה מהמכונה."""
        drain_crumbs(ctx, state_dir, hooks)
        live = {i["name"]: i for i in hooks["interfaces"]()}
        state = hooks["netcfg_state"]()
        configs = {c.name: c for c in all_configs()}
        names = sorted(set(live) | set(configs))
        mgmt = management()
        # ‏#1264: bond אחד לכל היותר (רשת ההפצה), ומצבו נקרא פעם אחת לבקשה.
        bonded = next((c for c in configs.values() if c.slaves), None)
        bond = hooks["netcfg_bond_state"](bonded.name) if bonded else None
        member_of = {s: bonded.name for s in bonded.slaves} if bonded else {}
        return {
            "interfaces": [{**state_view(load(n), live.get(n), state,
                                         bond if bonded and n == bonded.name else None),
                            "management": n == mgmt,
                            "member_of": member_of.get(n)} for n in names],
            "management_interface": mgmt or None,
            # ‏#1556: הכרטיס שדרכו הגיעה הבקשה (‏None = לא נקבע) — הקונסולה לא
            # מציעה אותו כברירת מחדל לרשת ההפצה, גם כשכרטיס הניהול לא ידוע.
            "console_interface": active_interface(request, live),
            "deploy_bond": bond_view(bonded, bond) if bonded else None,
            "live": {"checked": state.checked, "reason": state.reason,
                     "routes": state.routes, "nameservers": state.nameservers},
            "sourced": hooks["netcfg_sourced"](),
            "resolv_path": netcfg_host.RESOLV_CONF,
            "rollback": rollback_view(hooks["netcfg_now"]()),
        }

    def build(name: str, body: dict) -> netcfg.NetConfig:
        return netcfg.NetConfig(
            name=name,
            mode=str(body.get("mode") or netcfg.MODE_MANUAL).strip(),
            address=str(body.get("address", "")).strip(),
            netmask=str(body.get("netmask") or "255.255.255.0").strip(),
            gateway=str(body.get("gateway", "")).strip(),
            dns=[d.strip() for d in _as_list(body.get("dns")) if d.strip()],
            routes=[netcfg.StaticRoute.from_any(r)
                    for r in (body.get("routes") or [])],
        )

    def check(cfg: netcfg.NetConfig,
              dhcp_cfg: dhcp.InterfaceConfig | None = None) -> list[str]:
        """‏`dhcp_cfg` (‏#1263): ה-DHCP **שייכתב** יחד עם הכתובת — רשת
        הפצה משנה את שניהם בבקשה אחת, וההשוואה היא מול מה שיהיה."""
        others = [c for c in all_configs() if c.name != cfg.name] + [cfg]
        return netcfg.problems(cfg, others, dhcp_cfg or dhcp_of(cfg.name))

    def checked_name(name: str) -> None:
        """‏#1628: שם הכרטיס נכנס לנתיב הקובץ ול-`iface`/`post-up` שרצים
        כ-root, ולכן רשימת היתר **לפני** כל קריאה וכל כתיבה — לא רק אחרי
        שנמצא ברשימת הכרטיסים החיה (‏`allow_missing` עוקף אותה)."""
        try:
            dhcp.validate_name(name)
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    @router.post("/{name}/preview")
    async def preview(name: str, request: Request, user=Depends(admin_only)):
        """הקובץ שייכתב, לפני ואחרי — ואילו בעיות יעצרו אותו.

        אותה תבנית של "קובץ dnsmasq שנוצר", ובאותה כוונה: מי שרואה את
        הטקסט לפני ההחלה תופס טעות כשהיא עדיין טקסט.
        """
        checked_name(name)
        cfg = build(name, await request.json())
        cfg.slaves = load(name).slaves              # ‏#1264: נערכים רק מרשת ההפצה
        after = [c for c in all_configs() if c.name != name] + [cfg]
        return {
            "path": str(netcfg_host.conf_path(name)),
            "before": hooks["netcfg_read_conf"](name, netcfg_host.INTERFACES_DIR) or "",
            "after": netcfg.render(cfg, hostname=hooks["hostname"]()),
            "resolv_path": netcfg_host.RESOLV_CONF,
            "resolv_after": netcfg.render_resolv(sorted(after, key=lambda c: c.name)),
            "problems": check(cfg),
            "changed": netcfg.changed(load(name), cfg),
        }

    @router.put("/{name}")
    async def configure(name: str, request: Request, user=Depends(admin_only)):
        checked_name(name)
        body = await request.json()
        live = {i["name"]: i for i in hooks["interfaces"]()}
        if name not in live and not body.get("allow_missing"):
            raise HTTPException(404, "כרטיס רשת כזה לא קיים במכונה")
        member = bond_of(name)
        if member is not None:
            raise HTTPException(
                409, f"{name} הוא כרטיס ברשת ההפצה (חבר ב-{member.name}) — מסירים "
                     "אותו בחלון רשת ההפצה, ורק אז מגדירים אותו כאן")
        before, cfg = load(name), build(name, body)
        # ‏#1264: חברי ה-bond אינם שדה בטופס הזה — הם נשמרים כפי שהם (נתיב
        # סטטי על bond0, למשל), וקובעים אותם רק מ-`/second`.
        cfg.slaves = before.slaves
        found = check(cfg)
        if found:
            raise HTTPException(400, " · ".join(found))
        return _configure(name, before, cfg, body, request, user, live)

    def _configure(name: str, before: netcfg.NetConfig, cfg: netcfg.NetConfig,
                   body: dict, request: Request, user, live: dict) -> dict:
        """מכאן — אחרי שהקלט נבדק: הקלדת השם, זרוע ההחזרה, כתיבה, קריאה חוזרת."""
        # ‏#1558: הקונסולה כותבת ל-ifupdown. כשמשהו אחר מנהל את הרשת (שרת
        # חיפה: netplan) — סירוב בשם **לפני** הזרוע ולפני כל קובץ, ולא
        # "resolv.conf: Read-only" ו-"ifup: unknown interface" אחרי שנכתבו.
        foreign = hooks["netcfg_foreign_manager"]()
        if foreign:
            raise HTTPException(
                409, f"{foreign}. הקונסולה מגדירה כרטיסי רשת רק בשרת שמנוהל "
                     "ב-ifupdown (כמו שרת שהותקן מה-ISO) — לא נכתב ולא הוחל דבר. "
                     "בשרת הזה מגדירים את הכרטיס בכלי שמנהל את הרשת.")
        # עיקרון 7: שינוי כתובת הוא הרסני-בפועל — הוא מנתק מחשבים, ועל
        # הכרטיס הפעיל גם את המפעיל עצמו. הקלדת השם, כמו בהדלקת DHCP.
        if body.get("confirm") != name:
            raise HTTPException(
                409, f"לשינוי הגדרות הרשת יש להקליד את שם הכרטיס בדיוק: {name}")
        now = hooks["netcfg_now"]()
        if netcfg_rollback.read_pending(state_dir) is not None:
            raise HTTPException(
                409, "שינוי רשת קודם עדיין ממתין לאישור — אשרו אותו או "
                "המתינו שיוחזר, לפני שינוי נוסף")
        arm = touches_console(name, before, cfg, active_interface(request, live))
        if arm:
            ready, detail = hooks["netcfg_timer_active"]()
            if not ready:
                raise HTTPException(
                    409, f"השינוי הזה יכול לנתק את הקונסולה, וההחזרה "
                    f"האוטומטית ({netcfg_host.ROLLBACK_TIMER}) אינה פעילה: "
                    f"{detail}. בלי הזרוע הזו אין דרך חזרה — התקינו אותה "
                    "לפי docs/server-install.md.")
            error = _arm(name, before, cfg, now)
            if error:
                raise HTTPException(409, error)
        set_setting(ctx.conn, netcfg.SETTING_PREFIX + name, cfg.to_json())
        journal(ctx.conn, "net_config",
                f"{netcfg.summary(cfg)} changed={','.join(netcfg.changed(before, cfg))}",
                user[0])
        return _apply(name, cfg, arm, now, user[0], live.get(name))

    def _arm(name: str, before: netcfg.NetConfig, after: netcfg.NetConfig,
             now: float) -> str | None:
        """כותב את סמן ההחזרה **לפני** שנוגעים בקובץ כלשהו.

        הסדר הוא ההגנה: מכונה שמתה בין הכתיבה להחלה חייבת להשאיר סמן
        פתוח. סמן שנכתב אחרי השינוי היה משאיר חלון שבו השינוי כבר
        חי ואין מה שיחזיר אותו.
        """
        marker = netcfg_rollback.Pending(
            interface=name,
            deadline=now + netcfg_rollback.WINDOW_SECONDS,
            armed_at=_iso(now),
            boot=hooks["netcfg_boot_id"](),
            files=[{"name": name,
                    "text": hooks["netcfg_read_conf"](
                        name, netcfg_host.INTERFACES_DIR)}],
            resolv=_read_resolv(),
            setting=before.to_json(),
        )
        error = netcfg_rollback.write_pending(state_dir, marker)
        if error:
            return (f"{error}. בלי סמן ההחזרה השינוי הזה הוא חד-כיווני, "
                    "ולכן הוא לא בוצע.")
        journal(ctx.conn, "net_rollback_armed",
                f"{name} window={netcfg_rollback.WINDOW_SECONDS}s")
        return None

    def _read_resolv() -> str | None:
        try:
            with open(netcfg_host.RESOLV_CONF, encoding="utf-8") as handle:
                return handle.read()
        except OSError:
            return None

    def _apply(name: str, cfg: netcfg.NetConfig, armed: bool, now: float,
               who: str, live: dict | None) -> dict:
        """כותב, מחיל, ואז **קורא בחזרה**. ‏ok נקבע רק מהקריאה."""
        configs = sorted([c for c in all_configs() if c.name != name] + [cfg],
                         key=lambda c: c.name)
        errors = [hooks["netcfg_write_conf"](name,
                                             netcfg.render(cfg, hostname=hooks["hostname"]()),
                                             netcfg_host.INTERFACES_DIR),
                  hooks["netcfg_write_resolv"](netcfg.render_resolv(configs),
                                               netcfg_host.RESOLV_CONF)]
        error = " · ".join(e for e in errors if e) or None
        if error is None:
            error = hooks["netcfg_apply"](name)
        state = hooks["netcfg_state"]()
        gaps = gaps_of(cfg, state)
        verified = not gaps
        if error or not verified:
            journal(ctx.conn, "net_config_unverified",
                    f"{name} {error or ' · '.join(gaps)[:200]}", who)
        if not armed:
            # ‏#2076: כאן מגיע רק סמן שאינו ניתן לפענוח (סמן תקין נדחה ב-`_configure`).
            # ‏`clear_pending` בולע כישלון מחיקה, ולכן — כמו באישור (#291/#1226) —
            # "נוקה" נקבע לפי ראיה שהסמן איננו, ומה שנשאר נאמר בשמו.
            netcfg_rollback.clear_pending(state_dir)
            stuck = netcfg_rollback.pending_path(state_dir)
            if stuck.exists():
                left = (f"ההגדרה הוחלה, אבל סמן ההחזרה הישן ({stuck}) לא נמחק — "
                        "הוא יישאר עד שיימחק ידנית")
                journal(ctx.conn, "net_rollback_marker_stuck", f"{name} {stuck}", who)
                error = f"{error} · {left}" if error else left
        return {"ok": error is None and verified, "verified": verified,
                "apply_error": error, "mismatches": gaps,
                "interface": state_view(cfg, live, state),
                "rollback": rollback_view(now)}

    @router.put("/{name}/deploy")
    async def configure_deploy(name: str, request: Request, user=Depends(admin_only)):
        """רשת ההפצה (‏#1263): רשת + מסכה, וכתובת השרת **נגזרת** — הראשונה
        ברשת (‏`netcfg.deploy_address`). ה-DHCP של ההפצה (טווח, חכירה) נשמר
        באותה בקשה, על אותו כרטיס.

        למה בקשה אחת ולא שתיים מהמסך: כתובת חדשה מול DHCP ישן (או להפך) היא
        מצב ששני המסלולים הקיימים מסרבים לו — ובצדק — ולכן אין סדר של שתי
        קריאות שעובר בלי להשאיר כרטיס שמכריז על כתובת שאינה שלו.

        הסדר: כל הבדיקות של שני הצדדים (‏`check` עם ה-DHCP **החדש**, ושומרי
        ההדלקה של ראוטר ה-DHCP — trunk, ‏"מי כבר עונה", כרטיס ההפצה היחיד)
        **לפני** שנוגעים במשהו; אז הכתובת (עם אותה זרוע החזרה וקריאה
        חוזרת), ורק אם היא **אומתה** ואינה ממתינה לאישור — ה-DHCP. כתובת
        שלא אומתה עוצרת: DHCP שמכריז על כתובת שהכרטיס אינו נושא אינו
        "חצי הצלחה".
        """
        if dhcp_steps is None:
            raise HTTPException(501, "שלבי ה-DHCP לא חוברו לראוטר הזה")
        checked_name(name)
        body = await request.json()
        live = {i["name"]: i for i in hooks["interfaces"]()}
        if name not in live:
            raise HTTPException(404, "כרטיס רשת כזה לא קיים במכונה")
        if name == management():
            raise HTTPException(
                409, f"{name} הוא כרטיס הניהול (נבחר בהתקנה) — הוא יכול להיות "
                     "לקוח DHCP או כתובת ידנית, לא רשת ההפצה")
        member = bond_of(name)
        if member is not None:
            raise HTTPException(
                409, f"{name} חבר ב-{member.name} — רשת ההפצה נערכת על {member.name}")
        netmask = str(body.get("netmask") or "255.255.255.0").strip()
        network = str(body.get("network", "")).strip()
        address, found = netcfg.deploy_address(network, netmask)
        if found:
            raise HTTPException(400, " · ".join(found))
        before = load(name)
        # השער וה-DNS של **הכרטיס** ריקים בכוונה: הם של המחשבים (ב-DHCP),
        # ושער ברירת מחדל שני על השרת היה מתחרה בזה של כרטיס הניהול.
        cfg = netcfg.NetConfig(name=name, mode=netcfg.MODE_STATIC, address=address,
                               netmask=netmask, routes=before.routes,
                               slaves=before.slaves)            # ‏#1264
        dhcp_before = dhcp_steps["load"](name)
        # שער ו-DNS **למחשבים**: הקונסולה אינה מציעה אותם ברשת ההפצה (רשת
        # סגורה — dnsmasq ב-port=0, השרת אינו מנתב; נדב 10/10). מי שלא שולח
        # אותם שומר את מה שכבר נשמר — הגדרה קיימת אינה נמחקת בשקט.
        dhcp_body = {
            "enabled": True, "proxy": False, "trunk": dhcp_before.trunk,
            "range_start": body.get("range_start", ""), "range_end": body.get("range_end", ""),
            "netmask": netmask,
            "gateway": body["gateway"] if "gateway" in body else dhcp_before.gateway,
            "dns": body["dns"] if "dns" in body else dhcp_before.dns,
            "lease": body.get("lease") or "",
            "server_ip": address, "confirm": body.get("confirm"),
            "confirm_trunk": body.get("confirm_trunk") is True,
            "ignore_existing": bool(body.get("ignore_existing")),
        }
        dhcp_cfg = dhcp_steps["build"](name, dhcp_body, dhcp_before)
        found = check(cfg, dhcp_cfg)
        if found:
            raise HTTPException(400, " · ".join(found))
        risky = dhcp_steps["gates"](name, dhcp_cfg, dhcp_before, dhcp_body)
        address_result = None
        if netcfg.changed(before, cfg):
            address_result = _configure(name, before, cfg, body, request, user, live)
            if not address_result["ok"] or address_result["rollback"]["pending"]:
                why = ("ממתינה לאישור שהקונסולה עדיין נגישה — אשרו, ואז שמרו "
                       "שוב את רשת ההפצה" if address_result["ok"] else
                       "לא אומתה מול המצב בפועל")
                return {"ok": False, "server_ip": address, "address": address_result,
                        "dhcp": None, "apply_error": f"הכתובת {address} על {name} {why} "
                        "— ה-DHCP לא שונה"}
        dhcp_result = dhcp_steps["commit"](name, dhcp_cfg, dhcp_before, user,
                                           dhcp_steps["live"](), risky)
        journal(ctx.conn, "net_deploy_saved",
                f"{name} {network}/{netcfg._prefix_len(netmask)} server={address} "
                f"range={dhcp_cfg.range_start}-{dhcp_cfg.range_end}", user[0])
        out = {"ok": dhcp_result["ok"], "server_ip": address, "address": address_result,
               "dhcp": dhcp_result, "apply_error": dhcp_result["apply_error"]}
        if "deploy" in dhcp_result:
            out["deploy"] = dhcp_result["deploy"]
        return out

    # --- ‏#1264: כרטיס שני ברשת ההפצה (bond, ‏balance-alb) -------------------

    @router.put("/{name}/second")
    async def configure_second(name: str, request: Request, user=Depends(admin_only)):
        """כרטיס שני לרשת ההפצה: ‏`{"second": "eth2"}` מוסיף, ‏`{"second": ""}` מסיר.

        שני הכרטיסים פעילים יחד על **אותה** כתובת (ה-‎.1 של הרשת) — bond
        במצב ‏balance-alb, בלי הגדרה בסוויץ'. ‏`name` הוא רשת ההפצה כפי שהיא
        עכשיו: הכרטיס (בהוספה) או ה-bond (בהסרה).

        כל מה שקשור לשם כרטיס ההפצה עובר לשם החדש: הכתובת (‏`interfaces.d`),
        ה-DHCP (‏`dhcp:<שם>` → ‏`interface=` ב-dnsmasq), ‏`deploy:interface`,
        חומת האש (‏`--deploy-if`), ו-udp-sender (‏`--interface`, בעליית השרת
        שאחרי) — אותה השלמה של #1088, לשם החדש.

        הסדר: כל הבדיקות **לפני** שנוגעים במשהו; אז ההעברה על המכונה עם
        קריאה חוזרת (הכתובת, ול-bond גם ‏`/proc/net/bonding`), וכשל בה **מחזיר
        מיד** את הקבצים הקודמים — רשת הפצה בלי כתובת אינה "חצי הצלחה". רק אחרי
        שהמכונה אומתה — ה-DB, ההשלמה ו-dnsmasq. אין כאן זרוע החזרה של #56:
        שינוי שמגיע דרך כרטיס ברשת ההפצה (או מכרטיס לא ידוע) **נדחה**, ומכרטיס
        הניהול הקונסולה אינה מתנתקת.
        """
        if dhcp_steps is None:
            raise HTTPException(501, "שלבי ה-DHCP לא חוברו לראוטר הזה")
        checked_name(name)
        body = await request.json()
        second = str(body.get("second") or "").strip()
        current, deploy = load(name), dhcp_steps["deploy"]()
        _deploy_network_or_409(name, current, deploy)
        live = {i["name"]: i for i in hooks["interfaces"]()}
        touched: list[str] | None = None            # None = המכונה כבר במצב הזה
        if current.slaves and second == current.slaves[1]:
            new = current                           # ניסיון חוזר אחרי השלמה שנכשלה
        elif current.slaves and second:
            raise HTTPException(
                409, f"ברשת ההפצה כבר יש כרטיס שני ({current.slaves[1]}) — הסירו אותו "
                     f"קודם, ואז הוסיפו את {second}")
        elif current.slaves:
            new = netcfg.NetConfig(name=current.slaves[0], mode=netcfg.MODE_STATIC,
                                   address=current.address, netmask=current.netmask,
                                   routes=current.routes)
            touched = [name, *current.slaves]
        elif second:
            _second_card_or_409(second, name, live, body)
            new = netcfg.NetConfig(name=netcfg.BOND_NAME, mode=netcfg.MODE_STATIC,
                                   address=current.address, netmask=current.netmask,
                                   routes=current.routes, slaves=[name, second])
            touched = [name, second, netcfg.BOND_NAME]
        elif deploy.state.interface != name:
            new = current                           # ניסיון חוזר אחרי הסרה שהשלמתה נכשלה
        else:
            raise HTTPException(409, f"לרשת ההפצה על {name} אין כרטיס שני להסרה")
        if touched is not None:
            active = active_interface(request, live)
            if active is None or active in touched:
                raise HTTPException(
                    409, "הבקשה הגיעה דרך כרטיס ברשת ההפצה, או שלא ניתן לקבוע דרך איזה "
                         "כרטיס — הוספת כרטיס שני או הסרתו מורידה את רשת ההפצה לרגע, "
                         "ואין לה החזרה אוטומטית. היכנסו דרך כרטיס הניהול.")
            dhcp_new = dhcp.InterfaceConfig.from_json(new.name, dhcp_of(name).to_json())
            others = [c for c in all_configs() if c.name not in touched] + [new]
            found = netcfg.problems(new, others, dhcp_new)
            if found:
                raise HTTPException(400, " · ".join(found))
            error = _switch(current, new, touched)
            if error:
                journal(ctx.conn, "net_deploy_bond_failed",
                        f"{name}->{new.name} {error[:200]}", user[0])
                return {"ok": False, "interface": name, "server_ip": current.address,
                        "apply_error": error, "deploy": None, "bond": None}
            set_settings(ctx.conn,
                         {netcfg.SETTING_PREFIX + new.name: new.to_json(),
                          dhcp.SETTING_PREFIX + new.name: dhcp_new.to_json()},
                         remove=[p + n for n in touched if n != new.name
                                 for p in (netcfg.SETTING_PREFIX, dhcp.SETTING_PREFIX)])
            journal(ctx.conn, "net_deploy_bond", f"{name}->{netcfg.summary(new)}", user[0])
        return _finish_second(new, deploy, user)

    def _deploy_network_or_409(name: str, current: netcfg.NetConfig, deploy) -> None:
        """שומרים שאינם תלויים בכרטיס השני: רשת ההפצה הוגדרה מהקונסולה והיא
        `name`, אין סבב (ההעברה מורידה את רשת ההפצה ומאתחלת את השרת), ואין
        שינוי רשת שממתין לאישור."""
        state = deploy.state if deploy is not None else None
        if state is None or state.source != "console":
            why = ("מוגדרת ביחידת השרת (--server-url) ולא מהקונסולה"
                   if state is not None and state.source == "cli"
                   else "עוד לא הוגדרה — הגדירו אותה קודם על כרטיס אחד")
            raise HTTPException(409, f"רשת ההפצה {why}; כרטיס שני נקבע רק לרשת "
                                     "הפצה שהוגדרה מהקונסולה")
        if state.interface not in (name, netcfg.BOND_NAME, *current.slaves):
            raise HTTPException(409, f"{name} אינו רשת ההפצה (היא על {state.interface})")
        if current.mode != netcfg.MODE_STATIC or not dhcp_of(name).enabled:
            raise HTTPException(409, f"רשת ההפצה על {name} אינה מוגדרת (כתובת סטטית "
                                     "ו-DHCP) — שמרו אותה קודם")
        running = ctx.conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE state IN ('open', 'running')").fetchone()[0]
        if running:
            raise HTTPException(409, "יש סבב פעיל — הוספת כרטיס או הסרתו מורידה את רשת "
                                     "ההפצה לרגע ומאתחלת את השרת. נסו כשאין סבב.")
        if netcfg_rollback.read_pending(state_dir) is not None:
            raise HTTPException(409, "שינוי רשת קודם עדיין ממתין לאישור — אשרו אותו או "
                                     "המתינו שיוחזר, לפני שינוי נוסף")

    def _second_card_or_409(second: str, primary: str, live: dict, body: dict) -> None:
        """הכרטיס השני: קיים, אינו כרטיס הניהול, **אינו מוגדר לשום דבר**, ואיש
        אינו מחלק עליו DHCP (‏#53 — אחרת ה-DHCP של ההפצה היה יוצא גם לשם)."""
        try:
            dhcp.validate_name(second)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        if second in (primary, netcfg.BOND_NAME):
            raise HTTPException(400, f"הכרטיס השני חייב להיות כרטיס אחר מ-{primary}")
        if second not in live:
            raise HTTPException(404, "כרטיס רשת כזה לא קיים במכונה")
        if second == management():
            raise HTTPException(409, f"{second} הוא כרטיס הניהול (נבחר בהתקנה) — הוא "
                                     "לא יכול להיות כרטיס ברשת ההפצה")
        mode, offer = load(second).mode, dhcp_of(second)
        taken = (netcfg.MODES_HE[mode] if mode != netcfg.MODE_MANUAL else
                 "מחלק DHCP" if offer.enabled else "PXE proxy" if offer.proxy else "")
        if taken:
            raise HTTPException(409, f"{second} מוגדר היום ({taken}) — כרטיס שני חייב "
                                     "להיות כרטיס שאינו מוגדר לשום דבר אחר")
        if offer.trunk:
            raise HTTPException(409, f"{second} מסומן כמחובר לרשת המכללה — ה-DHCP של "
                                     "ההפצה היה יוצא גם דרכו")
        carried = live[second].get("addresses") or []
        if carried:
            raise HTTPException(409, f"{second} נושא כתובת ({', '.join(carried)}) — הוא "
                                     "בשימוש, ולכן אינו יכול להצטרף לרשת ההפצה")
        defined = hooks["netcfg_main_defines"](second)
        if defined is not False:
            raise HTTPException(409, (
                f"{second} מוגדר ב-{netcfg_host.INTERFACES_MAIN} — הסירו את ההגדרה שם "
                "(שני ifup על אותו כרטיס באתחול)") if defined else (
                f"לא ניתן לקרוא את {netcfg_host.INTERFACES_MAIN} — אי אפשר לוודא "
                f"ש-{second} אינו מוגדר שם"))
        if not hooks["netcfg_bond_supported"]():
            raise HTTPException(409, f"ifenslave אינו מותקן ({netcfg_host.IFENSLAVE_HOOK} "
                                     "חסר) — בלעדיו אין bond. התקינו: apt install ifenslave")
        if body.get("ignore_existing"):
            return
        found = dhcp_steps["probe"](second)
        if not found.checked:
            raise HTTPException(409, f"לא ניתן לבדוק אם כבר יש DHCP על {second} — הבדיקה "
                                     "עצמה לא רצה. לא נמצא שרת אחר — פשוט לא נבדק. אם "
                                     "הכרטיס מחובר רק לרשת ההפצה, שלחו ignore_existing.")
        if found.servers:
            raise HTTPException(409, f"נמצא שרת DHCP פעיל על {second}: "
                                     f"{', '.join(found.servers)} — חיבורו לרשת ההפצה "
                                     "היה מחלק כתובות גם לרשת ההיא")

    def _switch(old: netcfg.NetConfig, new: netcfg.NetConfig,
                touched: list[str]) -> str | None:
        """מעביר את כתובת ההפצה מ-`old` ל-`new` על המכונה, וקורא בחזרה.

        ‏`ifdown` של הישן **לפני** שהקובץ שלו נמחק (אחרת ifdown אינו יודע מה
        להוריד), קבצים, ‏`ifup` של החדש. כשל — ‏ifdown לחדש, הקבצים הקודמים
        חוזרים (‏`netcfg_rollback.restore`, אותו מסלול של הזרוע) והישן עולה
        שוב; גם ההחזרה נקראת בחזרה. מחזיר None רק כשהחדש **אומת**."""
        before = [{"name": n, "text": hooks["netcfg_read_conf"](n, netcfg_host.INTERFACES_DIR)}
                  for n in touched]
        try:
            module_before = hooks["netcfg_read_bond_module"]()
        except (OSError, UnicodeError) as exc:
            return f"לא ניתן לקרוא את {netcfg_host.BOND_MODULE_CONF}: {exc}"
        down = hooks["netcfg_down"](old.name)
        text = netcfg.render(new, hostname=hooks["hostname"]())
        errors = [hooks["netcfg_write_conf"](n, text if n == new.name else None,
                                             netcfg_host.INTERFACES_DIR) for n in touched]
        errors.append(hooks["netcfg_write_bond_module"]("bonding\n" if new.slaves else None))
        error = " · ".join(e for e in errors if e) or hooks["netcfg_apply"](new.name)
        gaps = [] if error else gaps_of(new, hooks["netcfg_state"]())
        if not error and not gaps:
            return None
        why = error or " · ".join(gaps)
        if down:
            why += f" (לפני כן: {down})"
        hooks["netcfg_down"](new.name)
        module_error = hooks["netcfg_write_bond_module"](module_before)
        restored = netcfg_rollback.restore(netcfg_rollback.Pending(
            interface=old.name, deadline=0, armed_at="", boot="", files=before,
            resolv=None), hooks)
        if module_error:
            restored.append(module_error)
        back = restored or gaps_of(old, hooks["netcfg_state"]())
        return (f"{new.name} לא אומת: {why}. " + (
            f"ההגדרה הקודמת על {old.name} הוחזרה ואומתה" if not back else
            f"וגם ההחזרה ל-{old.name} לא אומתה: {' · '.join(back)}"))

    def _finish_second(cfg: netcfg.NetConfig, deploy, user) -> dict:
        """ההשלמה של #1088 לשם החדש — grub.cfg, ‏dnsmasq באתחול, חומת אש עם
        ‏`--deploy-if` החדש, אתחול השרת (udp-sender), ורישום ‏`deploy:interface`
        ‏— ואז dnsmasq מה-DB. ‏dnsmasq מוחל גם כשההשלמה נכשלה: הכתובת כבר
        עברה, וה-DHCP חייב ללכת אחריה."""
        result = deploy_net.complete(deploy, ctx.conn, interface=cfg.name,
                                     server_ip=cfg.address)
        if result["ok"]:
            deploy.state = deploy_net.DeployState("console", cfg.name, result["url"])
            journal(ctx.conn, "deploy_net_set", f"{cfg.name} {result['url']}", user[0])
        else:
            journal(ctx.conn, "deploy_net_failed",
                    f"{cfg.name} {' · '.join(result['errors'])}", user[0])
        dhcp_error = dhcp_steps["apply"](cfg.name, user[0])
        errors = [*result["errors"], *([f"dnsmasq: {dhcp_error}"] if dhcp_error else [])]
        return {"ok": not errors, "interface": cfg.name, "server_ip": cfg.address,
                "apply_error": " · ".join(errors) or None, "deploy": result,
                "bond": (bond_view(cfg, hooks["netcfg_bond_state"](cfg.name))
                         if cfg.slaves else None)}

    @router.post("/confirm")
    async def confirm(request: Request, user=Depends(admin_only)):
        """"אני עדיין רואה את הקונסולה" — הראיה החיובית שהשינוי שרד.

        היעדר ניתוק אינו אישור: בלי הבקשה הזאת ההגדרה הקודמת חוזרת.
        אישור **אחרי** שהחלון נסגר נדחה — ההחזרה כבר קרתה או עומדת
        לקרות, ו-"ביטול" שלה היה משאיר מצב שאיש לא בחר בו.
        """
        body = await request.json()
        now = hooks["netcfg_now"]()
        pending = marker()
        if pending is None:
            drain_crumbs(ctx, state_dir, hooks)
            raise HTTPException(409, "אין שינוי רשת שממתין לאישור — ייתכן "
                                "שהוא כבר הוחזר. בדקו את היומן ואת לשונית הרשת.")
        if body.get("interface") and body["interface"] != pending.interface:
            raise HTTPException(409, f"הממתין לאישור הוא {pending.interface}")
        if netcfg_rollback.expired(pending, now):
            raise HTTPException(409, f"חלון האישור על {pending.interface} נסגר. "
                                "ההגדרה הקודמת חוזרת, ואי אפשר לבטל את זה מכאן.")
        netcfg_rollback.clear_pending(state_dir)
        # ‏#291: ‏`clear_pending` בולע כישלון מחיקה. "אושר" נקבע לפי ראיה
        # שהסמן איננו — אחרת הזרוע מחזירה את מה שהמפעיל ראה "מאושר".
        if netcfg_rollback.pending_path(state_dir).exists():
            raise HTTPException(
                500, f"לא הצלחנו למחוק את סמן ההחזרה "
                     f"({netcfg_rollback.pending_path(state_dir)}) — השינוי "
                     f"על {pending.interface} לא אושר ויוחזר בתום החלון.")
        journal(ctx.conn, "net_confirmed", pending.interface, user[0])
        return {"ok": True, "interface": pending.interface,
                "rollback": rollback_view(now)}

    return router


def _iso(epoch: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(epoch, timezone.utc).astimezone().isoformat(
        timespec="seconds")


def _as_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str):
        return value.replace(";", ",").split(",")
    return []
