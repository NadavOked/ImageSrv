"""לשונית "רשת", החצי השני: DHCP לכל כרטיס רשת (אפיון סעיף 24).

console_net.py מראה מה השרת ראה; כאן מגדירים מה השרת *מחלק*. ההגדרות
נשמרות בטבלת settings (מפתח `dhcp:<ממשק>`), ואחרי כל שינוי נכתב קובץ
dnsmasq ו-dnsmasq מופעל מחדש.

שכבות הבטיחות, לפי הסדר שבאפיון:
1. ברירת המחדל לכל ממשק — כבוי. ממשק בלי רשומה = כבוי.
2. הדלקה דורשת `confirm` שווה בדיוק לשם הממשק.
3. אם השרת רואה DHCP קיים על הממשק — סירוב, אלא אם `ignore_existing`.
4. ממשק שסומן trunk דורש גם `confirm_trunk: true`.
"""

from __future__ import annotations

import ipaddress
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Request

from . import auth, capabilities, console_netcfg, deploy_net, dhcp, ports
from .api import ServerContext
from .db import get_setting, journal, set_setting

Hooks = dict[str, Callable]

#: ‏#1121 ס' 4: נעילה סביב ההדלקה הראשונה של DHCP (השלמת ההתקנה) — תהליכית,
#: כי כל הראוטרים באותו תהליך; שתי בקשות מקבילות לא ישלימו פעמיים.
_first_enable_lock = threading.Lock()

#: תיאור חופשי לכרטיס (settings) — "700" בשביל וילן 700, וכדומה.
DESC_PREFIX = "nicdesc:"


# --- ה-render מה-DB — מקום אחד לראוטר, למתג ה-TFTP ולעליית השרת (‏#1013) ---


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
    """מה שאינסטנס ה-proxy רשאי לראות. הכרעת נדב 11/10: ‏proxy שייך לכיתות —
    כשהן כבויות (v1) גם רשומת proxy=true שנשמרה לפני הגידור אינה מוחלת
    (‏#1303), ולא רק ה-PUT מסרב לכתוב חדשה."""
    return configs if capabilities.classrooms() else []


def _proxy_gate() -> None:
    """‏v1 בלי proxy: נתיבי הקריאה שלו אינם קיימים — 404 לכל קורא, לפני
    auth, כמו ארגז הכלים (`console_tools._edition_gate`)."""
    if not capabilities.classrooms():
        raise HTTPException(404, "PXE proxy שייך לכיתות, וכיתות אינן במהדורה זו (v1.2)")


def console_deploy_interface(deploy: "deploy_net.DeployContext | None") -> str | None:
    """כרטיס ההפצה **שהוגדר מהקונסולה** — ורק הוא נכנס לקובץ ה-dnsmasq
    שהקונסולה כותבת (‏`imagectl.conf` של המתקין אינו נושא אותו)."""
    state = deploy.state if deploy is not None else None
    return state.interface if state is not None and state.source == "console" else None


def render_dnsmasq(ctx: ServerContext,
                   deploy: "deploy_net.DeployContext | None") -> tuple[str, str]:
    """‏(הראשי, ה-proxy) מה-DB: הגדרות הכרטיסים, כרטיס ההפצה מהקונסולה,
    ומתג ה-TFTP (‏#1013, ‏`port:tftp`; חסר = דלוק). ‏`tftp_root` מגיע
    מההתקנה (‏`--tftp-root` → ‏`DeployContext`), ובלי deploy — ברירת המחדל
    של המתקין. מעלה ValueError על רשומה פסולה שנשמרה לפני #102."""
    configs = load_configs(ctx.conn)
    # ‏as_posix: זה נתיב בקובץ dnsmasq של השרת (לינוקס) — לא נתיב של המכונה
    # שהקוד רץ עליה (תחנת הפיתוח היא ווינדוס, ו-str(Path) שם נותן לוכסן הפוך).
    root = (Path(deploy.tftp_root).as_posix() if deploy is not None else dhcp.DEFAULT_TFTP_ROOT)         if ports.enabled(ctx.conn, "tftp") else None
    # ‏#1078: רק `cli` נושא `interface=<הפצה>` בקובץ המתקין — בכל מקור אחר
    # הקובץ הזה הוא היחיד, ובלי שם בו TFTP נצמד ל-loopback (`dhcp._pinned`).
    pin = deploy is not None and deploy.state.source != "cli"
    # ‏#705: "אתחול מהרשת" לכל כרטיס — רק כשהמפעיל ערך שירותים (אחרת None,
    # והקובץ זהה לקודם). כרטיס שבו הוא כבוי מוחרג גם כשה-DHCP שלו דלוק.
    gate = getattr(ctx, "nic_services", None)
    netboot = gate.netboot() if gate is not None else None
    per_nic = ({"netboot": netboot[0], "nics": netboot[1]} if netboot is not None else {})
    # ‏#1802: כרטיס ההפצה מכל מקור — `cli` כשהיחידה נושאת ‎--interface‏ (המתקין
    # מעביר אותו כשיש כרטיס הפצה), או מה שנרשם מהקונסולה. בלי שם — כמו היום.
    unregistered_if = (deploy.state.interface
                       if deploy is not None and deploy.state.configured else None)
    return (dhcp.render(configs, tftp_root=root,
                        deploy_interface=console_deploy_interface(deploy), pin_tftp=pin,
                        unregistered_if=unregistered_if, **per_nic),
            dhcp.render_proxy(proxy_configs(configs), tftp_root=root))


def apply_dnsmasq(ctx: ServerContext, hooks: Hooks,
                  deploy: "deploy_net.DeployContext | None",
                  what: str, user_id) -> str | None:
    """מחילים על שני האינסטנסים (‏#36): הראשי מחלק כתובות, וה-proxy רץ
    בתהליך משלו כדי שקפיאה שלו לא תוריד את וילן ההפצה. כשל באחד לא
    מסתיר את השני — שתי ההודעות חוזרות לקונסולה וליומן. מחזיר הודעת
    שגיאה או None."""
    try:
        text, proxy_text = render_dnsmasq(ctx, deploy)
    except ValueError as exc:      # רשומה פסולה שנשמרה לפני #102
        journal(ctx.conn, "dhcp_apply_failed", f"{what} {exc}", user_id)
        return str(exc)
    errors = [
        hooks["apply"](text),
        hooks["apply_proxy"](proxy_text,
                             bool(dhcp.proxy_only(proxy_configs(load_configs(ctx.conn))))),
    ]
    error = " · ".join(e for e in errors if e) or None
    if error:
        journal(ctx.conn, "dhcp_apply_failed", f"{what} {error}", user_id)
    return error


def sync_main_conf(ctx: ServerContext, hooks: Hooks,
                   deploy: "deploy_net.DeployContext | None") -> str | None:
    """עליית השרת (‏#1013): הקובץ הראשי הוא **נגזרת של ה-DB**, כמו
    known-macs (‏#141) — ואם מה שעל הדיסק שונה ממה שה-DB אומר, כותבים
    ומפעילים את dnsmasq מחדש. זה מה שמעלה TFTP בהתקנה טרייה (המתקין
    אינו כותב עוד `enable-tftp`, ו-dnsmasq שלו עלה בלי TFTP) ואחרי הרצת
    המתקין מחדש על שרת שהקובץ שלו נכתב לפני v0.48.

    שני סייגים, בכוונה: רק כשרשת ההפצה **מוגדרת** (‏#1088 — בלעדיה dnsmasq
    נשאר כבוי עד ההדלקה הראשונה, ו-`restart` היה מעלה אותו על כל
    הכרטיסים), ורק כשהתוכן **שונה** — אתחול שגרתי של השרת אינו מפיל את
    dnsmasq. ה-proxy אינו נוגע כאן: היחידה שלו עולה ויורדת עם ההגדרה
    (‏`apply_dnsmasq`), ולעלייה אין מה לשנות בה.
    """
    if deploy is None or not deploy.state.configured:
        return None
    try:
        text, _proxy = render_dnsmasq(ctx, deploy)
    except ValueError as exc:
        journal(ctx.conn, "dhcp_apply_failed", f"startup {exc}")
        return str(exc)
    if hooks["read_active_conf"]() == text:
        return None
    error = hooks["apply"](text)
    if error:
        journal(ctx.conn, "dhcp_apply_failed", f"startup {error}")
    else:
        journal(ctx.conn, "dhcp_synced_at_startup", "TFTP/DHCP conf rewritten from the DB")
    return error


def _checked_name(name: str) -> str:
    """שם כרטיס תקין, או 400 בעברית. הכלל עצמו יושב ב-`dhcp.validate_name`
    ‏— מקום אחד שכל הכותבים עוברים דרכו, ולא עותק שני של הביטוי כאן."""
    try:
        dhcp.validate_name(name)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return name


#: יחידת ה-systemd של האינסטנס הראשי — ‏#762, לצד PROXY_UNIT הקיים.
DNSMASQ_UNIT = "dnsmasq"

#: ניסוח עברי לארבעת מצבי ה-DHCP החי (‏#762). ‏"unknown" הוא ברירת המחדל
#: הבטוחה — לעולם לא "כבוי" כשלא הצלחנו לבדוק (עיקרון 5, הרחבה 5א).
DHCP_LIVE_LABELS = {
    "serving": "משרת",
    "configured_not_running": "מוגדר, השירות אינו פועל",
    "off": "כבוי",
    "unknown": "לא ידוע",
}


def default_hooks() -> Hooks:
    return {
        "interfaces": dhcp.list_interfaces,
        "probe": dhcp.probe_existing_dhcp,
        "apply": dhcp.apply,
        "apply_proxy": dhcp.apply_proxy,
        "dnsmasq_version": dhcp.dnsmasq_version,
        "read_active_conf": dhcp.read_active_conf,
        "service_active": dhcp.service_active,
        # ‏#1263: כרטיס הניהול שהמתקין רשם — אותו hook של ראוטר הכתובות.
        "netcfg_management": console_netcfg.installed_management,
    }


def create_dhcp_router(ctx: ServerContext, hooks: Hooks | None = None,
                       deploy: "deploy_net.DeployContext | None" = None) -> APIRouter:
    """‏`deploy` (‏#1088): מצב רשת ההפצה בריצה הזו. ‏None = אין השלמת
    התקנה מכאן (בדיקות/קוד ישן) — ההדלקה מתנהגת כמו לפני #1088."""
    router = APIRouter(prefix="/api/console/net")
    current_user, admin_only = auth.dependencies(ctx.conn)
    hooks = {**default_hooks(), **(hooks or {})}

    def deploy_state() -> "deploy_net.DeployState | None":
        return deploy.state if deploy is not None else None

    def load(name: str) -> dhcp.InterfaceConfig:
        return dhcp.InterfaceConfig.from_json(
            name, get_setting(ctx.conn, dhcp.SETTING_PREFIX + name)
        )

    def all_configs() -> list[dhcp.InterfaceConfig]:
        return load_configs(ctx.conn)

    def apply_all(what: str, user_id, *, proxy_changed: bool = False) -> str | None:
        error = apply_dnsmasq(ctx, hooks, deploy, what, user_id)
        # #1829: changing/removing proxy mode must also open/close UDP 4011 now.
        if proxy_changed:
            nic = getattr(ctx, "nic_services", None)
            firewall = nic.hooks.get("firewall") if nic is not None else None
            try:
                fw_error = (firewall(nic) if firewall is not None
                            else "אין מנגנון אכיפה לחומת האש בתהליך הזה")
            except Exception as exc:                          # noqa: BLE001
                fw_error = str(exc) or type(exc).__name__
            if fw_error:
                error = " · ".join(e for e in (error, f"חומת אש: {fw_error}") if e)
                journal(ctx.conn, "dhcp_apply_failed", f"{what} {error}", user_id)
        return error

    def dhcp_live_state(name: str, cfg: dhcp.InterfaceConfig,
                        conf_text: str | None, svc_active: bool | None) -> dict:
        """‏#762: מה **באמת** מוגש על הממשק, לא מה שה-DB אומר שרצינו.

        ארבעה מצבים בלבד, ואף אחד מהם אינו ניחוש: קריאת הקובץ או בירור
        השירות שנכשלו מחזירים `unknown` — לעולם לא `off`, כי `off` הוא
        טענה חיובית שבדקנו ולא מצאנו (עיקרון 5, הרחבה 5א).
        """
        if conf_text is None or svc_active is None:
            return {"state": "unknown", "interface_in_conf": False,
                    "service_active": svc_active, "checked": False,
                    "detail": "לא ניתן לקרוא את מצב ה-DHCP הפעיל"
                    if conf_text is None else "לא ניתן לברר את מצב השירות"}
        served = dhcp.parse_served_interfaces(conf_text)
        in_conf = name in served
        if in_conf and svc_active:
            state = "serving"
        elif in_conf and not svc_active:
            state = "configured_not_running"
        else:
            state = "off"
        return {"state": state, "interface_in_conf": in_conf,
                "service_active": svc_active, "checked": True, "detail": ""}

    def view(cfg: dhcp.InterfaceConfig, live: dict | None,
             conf_text: str | None = None, svc_active: bool | None = None) -> dict:
        dhcp_live = dhcp_live_state(cfg.name, cfg, conf_text, svc_active)
        diverged = (dhcp_live["checked"]
                    and (cfg.enabled != dhcp_live["interface_in_conf"]
                         or (cfg.enabled and dhcp_live["state"] != "serving")))
        data = {
            "name": cfg.name, "enabled": cfg.enabled, "proxy": cfg.proxy,
            "trunk": cfg.trunk, "range_start": cfg.range_start,
            "range_end": cfg.range_end, "netmask": cfg.netmask,
            "gateway": cfg.gateway, "dns": cfg.dns, "lease": cfg.lease,
            "server_ip": cfg.server_ip,
            # תיאור חופשי לכרטיס — למשל מספר ה-VLAN שהוא מחובר אליו.
            "description": get_setting(ctx.conn, DESC_PREFIX + cfg.name) or "",
            "state": (live or {}).get("state", "missing"),
            "mac": (live or {}).get("mac", ""),
            "addresses": (live or {}).get("addresses", []),
            "present": live is not None,
            "admin_up": (live or {}).get("admin_up"),
            "link_up": (live or {}).get("link_up"),
            "add_refused_reason": (
                f"הכרטיס {cfg.name} מושבת מנהלית; שמירת רשת ההפצה תנסה להפעיל אותו"
                if live is not None and (live or {}).get("admin_up") is False else None),
            # מהירות הכרטיס — ‏None כשלא נקרא; לעולם לא ניחוש לפי השם (‏#762).
            "speed_mbps": (live or {}).get("speed_mbps"),
            "dhcp_configured": cfg.enabled,
            "dhcp_live": dhcp_live,
            "dhcp_live_label": DHCP_LIVE_LABELS[dhcp_live["state"]],
            "dhcp_diverged": diverged,
        }
        return data

    @router.get("/interfaces")
    def interfaces(user=Depends(current_user)):
        """כל כרטיס רשת במכונה + ההגדרה שלו. כרטיס בלי הגדרה = כבוי."""
        live = {i["name"]: i for i in hooks["interfaces"]()}
        names = sorted(set(live) | {c.name for c in all_configs()})
        # קובץ ה-conf ומצב השירות נקראים פעם אחת לכל הבקשה — לא פעם
        # לכל ממשק — כדי שכל השורות בטבלה יסכימו על אותה תמונה.
        conf_text = hooks["read_active_conf"]()
        svc_active = hooks["service_active"](DNSMASQ_UNIT)
        return [view(load(n), live.get(n), conf_text, svc_active) for n in names]

    @router.get("/interfaces/{name}/probe")
    def probe(name: str, user=Depends(admin_only)):
        """מי כבר עונה ל-DHCP על הממשק. הבדיקה שמונעת את התקלה הגרועה ביותר.

        אין לו עדיין קורא בקונסולה — הוא הצורה הבדוקה של אותה בדיקה
        שההדלקה מריצה, ומאפשר לראות *למה* היא חסמה: `checked=false`
        פירושו שהבדיקה לא רצה, לא שהכרטיס נקי.
        """
        found = hooks["probe"](name)
        return {"interface": name, "checked": found.checked,
                "servers": list(found.servers)}

    @router.get("/interfaces/{name}/leases")
    def leases(name: str, user=Depends(admin_only)):
        """‏#1039: "מי קיבל כתובת" — קובץ החכירות של dnsmasq, מסונן לרשתות
        שעל הכרטיס בפועל. מכונה שקיבלה DHCP ולא הגיעה ל-hello נראית רק כאן.

        עיקרון 5: ‏``checked:false`` + ‏``reason`` + ‏``leases:null`` כשהקובץ
        לא נקרא, כשאין מקור חכירות בריצה הזו (``ctx.leases``, מוזרק כמו
        ``dhcp_hooks``), או כשלכרטיס אין כתובת IPv4 שאפשר לשייך אליה —
        ‏``[]`` רק כשנקרא ואין חכירה ברשת שלו."""
        _checked_name(name)

        def unchecked(reason: str, path: str | None = None) -> dict:
            return {"interface": name, "checked": False, "reason": reason,
                    "path": path, "leases": None}

        if ctx.leases is None:
            return unchecked("אין מקור חכירות בריצה הזו (--dhcp-leases)")
        path = str(ctx.leases.path)
        live = next((i for i in hooks["interfaces"]() if i["name"] == name), None)
        networks = []
        for address in (live or {}).get("addresses") or []:
            try:
                networks.append(ipaddress.IPv4Interface(address).network)
            except ValueError:
                continue
        if not networks:
            return unchecked(f"לכרטיס {name} אין כתובת IPv4 בפועל — אי אפשר "
                             "לשייך אליו חכירות", path)
        try:
            rows = ctx.leases.rows()
        except OSError as exc:
            return unchecked(f"{path}: {exc.strerror or exc}", path)
        mine = [dict(row, expires=_lease_expiry(row["expires"])) for row in rows
                if any(ipaddress.IPv4Address(row["ip"]) in n for n in networks)]
        return {"interface": name, "checked": True, "reason": "", "path": path,
                "leases": mine}

    @router.get("/proxy-support", dependencies=[Depends(_proxy_gate)])
    def proxy_support(user=Depends(admin_only)):
        """מה גרסת ה-dnsmasq המותקנת אומרת על מצב proxy (‏#36).

        המסך מציג את `reason` כלשונו ולא מנסח משלו — כך שההסבר שהמפעיל
        רואה הוא בדיוק זה שה-API יסרב בו, ושניהם לא יכולים להתפצל.
        """
        support = dhcp.proxy_support(hooks["dnsmasq_version"]())
        return {"read": support.read, "version": support.version,
                "verified": support.verified, "broken": support.broken,
                "reason": support.reason()}

    @router.put("/interfaces/{name}")
    async def configure(name: str, request: Request, user=Depends(admin_only)):
        # השם מגיע מנתיב ה-URL, ו-uvicorn מפענח %0A לשורה חדשה. הבדיקה
        # ראשונה בכוונה: עם allow_missing אין שום שלב אחר שרואה את השם
        # לפני שהוא נכתב לקובץ של dnsmasq (‏#102).
        _checked_name(name)
        body = await request.json()
        live = {i["name"]: i for i in hooks["interfaces"]()}
        if name not in live and not body.get("allow_missing"):
            raise HTTPException(404, "כרטיס רשת כזה לא קיים במכונה")

        before = load(name)
        cfg = _build(name, body, before)
        risky_proxy = _gates(name, cfg, before, body)
        return _commit(name, cfg, before, user, live, risky_proxy)

    def _build(name: str, body: dict,
               before: dhcp.InterfaceConfig) -> dhcp.InterfaceConfig:
        return dhcp.InterfaceConfig(
            name=name,
            enabled=bool(body.get("enabled", False)),
            proxy=bool(body.get("proxy", False)),
            trunk=bool(body.get("trunk", before.trunk)),
            range_start=str(body.get("range_start", "")).strip(),
            range_end=str(body.get("range_end", "")).strip(),
            netmask=str(body.get("netmask") or "255.255.255.0").strip(),
            gateway=str(body.get("gateway", "")).strip(),
            dns=[d.strip() for d in _as_list(body.get("dns")) if d.strip()],
            lease=str(body.get("lease") or dhcp.DEFAULT_LEASE).strip(),
            server_ip=str(body.get("server_ip", "")).strip(),
        )

    def _gates(name: str, cfg: dhcp.InterfaceConfig,
               before: dhcp.InterfaceConfig, body: dict) -> str | None:
        """כל השומרים שלפני הכתיבה — זורק HTTPException, או מחזיר את
        גרסת ה-dnsmasq כשה-proxy הודלק על גרסה שלא נבדקה. ‏#1263: נקרא גם
        מ-`PUT /net/config/{שם}/deploy` **לפני** שהכתובת של הכרטיס זזה."""
        if cfg.enabled and cfg.proxy:
            raise HTTPException(400, "DHCP מלא ו-proxy על אותו ממשק סותרים זה את זה")
        # הכרעת נדב 11/10: ‏proxy קיים בשביל תחנות כיתה שמקבלות כתובת מה-DHCP
        # של המכללה — כלומר הוא חלק מהכיתות. כשהן כבויות (v1) השרת מסרב, לא
        # רק הממשק מסתיר. כיבוי (proxy=false) תמיד מותר.
        if cfg.proxy and not capabilities.classrooms():
            raise HTTPException(
                409, "PXE proxy שייך לכיתות, וכיתות אינן במהדורה זו (v1.2)")
        # ‏#1263: כרטיס הניהול (נבחר בהתקנה) הוא לקוח DHCP או כתובת ידנית — לא
        # מחלק כתובות ולא עונה ל-PXE. כיבוי (enabled=false) תמיד מותר.
        if (cfg.enabled or cfg.proxy) and name == console_netcfg.management_interface(
                ctx.conn, hooks["netcfg_management"]):
            raise HTTPException(
                409, f"{name} הוא כרטיס הניהול (נבחר בהתקנה) — אין עליו DHCP "
                     "ואין proxy; הוא לקוח DHCP או כתובת ידנית בלבד")

        # שכבה 5 (‏#36): מצב proxy נשען על תכונה שבורה ב-dnsmasq המותקן.
        # ההגנה היא אישור מפורש, לא כפתור מנוטרל — במעבדה *חייבים* להיות
        # מסוגלים להדליק אותו כדי לבדוק גרסה חדשה, וכפתור אטום בלי דרך
        # חוקית עובר בקלות ל-curl ישיר. גם המסך וגם ה-API עוברים כאן.
        # הבדיקה על המעבר ל-proxy — כולל מעבר מ-DHCP מלא, שאינו "הדלקה".
        risky_proxy = None
        if cfg.proxy and not before.proxy:
            support = dhcp.proxy_support(hooks["dnsmasq_version"]())
            if not support.verified:
                if body.get("confirm_proxy_broken") is not True:
                    raise HTTPException(409, support.reason())
                risky_proxy = support.version or "unknown"
        try:
            dhcp.validate(cfg)
        except ValueError as exc:
            raise HTTPException(400, str(exc))

        turning_on = (cfg.enabled or cfg.proxy) and not (before.enabled or before.proxy)
        if turning_on or (cfg.enabled and not before.enabled):
            # שכבה 2: השם המדויק. לחיצה מקרית לא מדליקה DHCP.
            if body.get("confirm") != name:
                raise HTTPException(
                    409, f"להדלקה יש להקליד את שם הממשק בדיוק: {name}"
                )
            # שכבה 4: trunk. המכללה כולה מאחורי הממשק הזה.
            if cfg.trunk and cfg.enabled and body.get("confirm_trunk") is not True:
                raise HTTPException(
                    409, "הממשק מסומן כמחובר לרשת המכללה — נדרש אישור נוסף (confirm_trunk)"
                )
            # שכבה 3: מישהו כבר מחלק כתובות כאן?
            if cfg.enabled and not body.get("ignore_existing"):
                found = hooks["probe"](name)
                # בדיקה שלא רצה אינה "נקי" — היא "לא יודעים" (‏#53), וברשת
                # המכללה "לא יודעים" עולה רשת שלמה. פורט 68 תפוס הוא בדיוק
                # מה שקורה על כרטיס trunk שמקבל כתובת מהמכללה.
                if not found.checked:
                    raise HTTPException(
                        409, f"לא ניתן לבדוק אם כבר יש DHCP על {name} — הבדיקה "
                        "עצמה לא רצה (נדרשות הרשאות root, ופורט 68 חייב להיות "
                        "פנוי; לקוח DHCP שרץ על הכרטיס תופס אותו). לא נמצא "
                        "שרת אחר — פשוט לא נבדק. אם אתה יודע שהכרטיס אינו "
                        "מחובר לרשת המכללה, הדלק עם ignore_existing.",
                    )
                if found.servers:
                    raise HTTPException(
                        409, "נמצא שרת DHCP פעיל על הממשק: "
                        + ", ".join(found.servers)
                        + ". הדלקת DHCP שני תשבית את הרשת.",
                    )
        if cfg.enabled and not before.enabled:
            _only_the_deploy_nic_or_409(name)
        return risky_proxy

    def _commit(name: str, cfg: dhcp.InterfaceConfig,
                before: dhcp.InterfaceConfig, user, live: dict,
                risky_proxy: str | None) -> dict:
        turning_on = (cfg.enabled or cfg.proxy) and not (before.enabled or before.proxy)
        # ‏#1088: הדלקת DHCP על כרטיס כשרשת ההפצה טרם הוגדרה **משלימה את
        # ההתקנה** — grub.cfg, ‏dnsmasq באתחול, חומת אש, ואתחול השרת עם
        # כתובת ההפצה. השומרים לפני הכתיבה ל-DB: הכרטיס באמת נושא את
        # `server_ip` (אחרת השרת לא יעלה אחרי האתחול — "כתובת → DHCP" הוא
        # הסדר של נדב), והכתובת אינה מוגדרת ביחידה (אז ההגדרה כאן לא
        # הייתה משפיעה על דבר).
        # ‏#1121 ס' 4: שתי הדלקות ראשונות במקביל — רק אחת נכנסת; השנייה
        # רואה כבר `source=console` ונשפטת לפי הכרטיס (409 אם אינו ההפצה).
        # ‏non-blocking: מי שלא קיבל את הנעילה מקבל 409, לא ממתין.
        if not _first_enable_lock.acquire(blocking=False):
            raise HTTPException(409, "הדלקת DHCP אחרת בעיצומה — נסה שוב בעוד רגע")
        try:
            completion = None
            if cfg.enabled and not before.enabled:
                _only_the_deploy_nic_or_409(name)
            if turning_on and cfg.enabled:
                completion = _deploy_completion_or_409(name, cfg.server_ip, live.get(name))
            result = None
            if completion is not None:
                # ‏#1121: ההשלמה רצה **לפני** הכתיבה של `dhcp:<כרטיס>` — apply
                # ואז commit. כשל בה = שום דבר לא נרשם (לא deploy ולא DHCP),
                # אחרת ההדלקה הבאה לא הייתה "הדלקה ראשונה" ולא הייתה מנסה שוב.
                result = deploy_net.complete(deploy, ctx.conn, interface=name,
                                             server_ip=cfg.server_ip)
                if not result["ok"]:
                    detail = " · ".join(result["errors"])
                    journal(ctx.conn, "deploy_net_failed", f"{name} {detail}", user[0])
                    raise HTTPException(
                        500, f"השלמת ההתקנה לרשת ההפצה נכשלה — לא נרשם דבר, "
                             f"תקן והדלק שוב: {detail}")
                deploy.state = deploy_net.DeployState("console", name, result["url"])
                journal(ctx.conn, "deploy_net_set", f"{name} {result['url']}", user[0])

            set_setting(ctx.conn, dhcp.SETTING_PREFIX + name, cfg.to_json())
        finally:
            _first_enable_lock.release()
        state = "on" if cfg.enabled else ("proxy" if cfg.proxy else "off")
        journal(ctx.conn, "dhcp_set", f"{name} {state}", user[0])
        if risky_proxy:
            # מי הדליק proxy על גרסה שלא נבדקה, ועל איזו גרסה — כשמישהו
            # ישאל למה ה-DHCP קפא, זו השורה שעונה.
            journal(ctx.conn, "dhcp_proxy_risk",
                    f"{name} dnsmasq={risky_proxy}", user[0])

        error = apply_all(name, user[0], proxy_changed=cfg.proxy or before.proxy)
        out = {"ok": error is None,
               "interface": view(cfg, live.get(name),
                                 hooks["read_active_conf"](),
                                 hooks["service_active"](DNSMASQ_UNIT)),
               "apply_error": error}
        if result is not None:
            # מכאן הקובץ של הקונסולה נושא `interface=<הפצה>` גם כשה-DHCP
            # עליו יכובה (dhcp.render עם deploy_interface); הכרטיס ממילא
            # בקובץ שנכתב הרגע כמחלק כתובות, ואין מה לכתוב שוב.
            out["deploy"] = result
        return out

    # ‏#1263: השלבים של ההדלקה, לשימוש `PUT /net/config/{שם}/deploy` — אותם
    # שומרים ואותה השלמת התקנה, לא עותק שני שלהם בראוטר הכתובות.
    router.dhcp_steps = {
        "load": load, "build": _build, "gates": _gates, "commit": _commit,
        "live": lambda: {i["name"]: i for i in hooks["interfaces"]()},
        # ‏#1264: הכרטיס השני — אותו "מי כבר עונה" (‏#53) על הכרטיס שמצטרף,
        # אותה השלמת התקנה (‏deploy) ואותו dnsmasq אחרי שה-DHCP עבר ל-bond.
        "probe": lambda name: hooks["probe"](name),
        "deploy": lambda: deploy,
        "apply": apply_all,
    }

    def _only_the_deploy_nic_or_409(name: str) -> None:
        """‏#1121 ס' 4: אחרי שרשת ההפצה הוגדרה מהקונסולה, DHCP **מלא** מותר רק
        על כרטיס ההפצה — כרטיס אחר הוא רשת המכללה, ו-dnsmasq שעונה שם
        הוא התקלה ש-#53 נועד למנוע. ‏proxy אינו נחסם כאן (אינו מחלק כתובות)."""
        state = deploy_state()
        if state is None or state.source != "console" or state.interface == name:
            return
        raise HTTPException(
            409, f"DHCP מלא מותר רק על כרטיס ההפצה ({state.interface}) — {name} אינו "
                 "כרטיס ההפצה. כדי להחליף כרטיס הפצה יש להתקין מחדש; proxy מותר "
                 "על כל כרטיס")

    def _deploy_completion_or_409(name: str, server_ip: str, live_nic: dict | None):
        """האם ההדלקה הזו היא גם הגדרת רשת ההפצה — ואם כן, האם מותר."""
        state = deploy_state()
        if state is None or state.configured:
            return None
        if live_nic is None:
            raise HTTPException(
                409, f"רשת ההפצה מוגדרת כאן בפעם הראשונה, ו-{name} אינו קיים "
                     "במכונה — אי אפשר להפוך כרטיס שאינו שם לרשת ההפצה")
        carried = {a.split("/")[0] for a in live_nic.get("addresses") or []}
        if server_ip not in carried:
            raise HTTPException(
                409, f"רשת ההפצה מוגדרת כאן בפעם הראשונה: לכרטיס {name} אין את "
                     f"הכתובת {server_ip} (יש לו: {', '.join(sorted(carried)) or 'כלום'}). "
                     "הגדר את רשת ההפצה מהלשונית \"רשת הפצה\" — היא קובעת את "
                     "הכתובת ומדליקה DHCP יחד (#1263); אחרת השרת לא יעלה על הכתובת הזו")
        return True

    @router.get("/deploy")
    def deploy_view(user=Depends(current_user)):
        """‏#1088: מקור כתובת ההפצה — יחידה / קונסולה / לא הוגדרה."""
        state = deploy_state()
        if state is None:
            return {"configured": True, "source": "cli", "interface": None,
                    "url": None, "hint": None}
        return state.public()

    @router.post("/interfaces")
    async def add_interface(request: Request, user=Depends(admin_only)):
        """הוספת כרטיס ידנית — תת-ממשק של וילן שעוד לא הוגדר במכונה.

        נוצרת רשומת תצורה כבויה, כדי שהכרטיס יופיע בטבלה ויהיה אפשר
        לתאר אותו ולהגדיר עליו DHCP עוד לפני שהוא קיים פיזית.
        """
        body = await request.json()
        name = _checked_name((body.get("name") or "").strip())
        # כרטיס חי מותר "להוסיף" — זו קליטה שלו: תצורה כבויה + תיאור.
        if get_setting(ctx.conn, dhcp.SETTING_PREFIX + name):
            raise HTTPException(409, "הכרטיס כבר הוגדר")
        set_setting(ctx.conn, dhcp.SETTING_PREFIX + name,
                    dhcp.InterfaceConfig(name).to_json())
        if (body.get("description") or "").strip():
            set_setting(ctx.conn, DESC_PREFIX + name, body["description"].strip())
        journal(ctx.conn, "nic_add", name, user[0])
        return {"name": name}

    @router.put("/interfaces/{name}/description")
    async def describe(name: str, request: Request, user=Depends(admin_only)):
        """תיאור חופשי לכרטיס — למשל איזה VLAN מחובר אליו.

        השם עובר את אותה בדיקה כמו בשני המסלולים האחרים (#102). כאן זו
        אינה הזרקה ל-dnsmasq — ‏`all_configs()` קוראת רק מפתחות
        ‏`dhcp:` — אלא **כישלון שקט**: ‏``nicdesc:eth0 `` (עם רווח) הוא
        מפתח אחר מ-``nicdesc:eth0``, ולכן התיאור נשמר, מוחזר
        ``{"ok": true}``, ולעולם אינו מוצג.
        """
        _checked_name(name)
        body = await request.json()
        set_setting(ctx.conn, DESC_PREFIX + name,
                    (body.get("description") or "").strip())
        journal(ctx.conn, "net_describe",
                f'{name} {body.get("description", "")}', user[0])
        return {"ok": True}

    @router.delete("/interfaces/{name}")
    def forget(name: str, user=Depends(admin_only)):
        """מסיר את מה שנשמר על הכרטיס — הגדרת DHCP ותיאור.

        כרטיס חי חוזר לברירת המחדל (כבוי); כרטיס שכבר לא קיים במכונה
        נעלם מהרשימה. אם היה עליו DHCP פעיל — dnsmasq מתעדכן מיד.

        **כאן אין `_checked_name` בכוונה.** זו המחיקה, והיא הדרך
        היחידה להסיר מפתח שנכתב בשם פסול לפני שהבדיקה נוספה (#130).
        אימות כאן היה נועל את הזבל לתמיד. מחיקה לפי מפתח מדויק אינה
        יכולה לכתוב דבר, ולכן שם פסול פשוט לא מוחק כלום.
        """
        was = load(name)
        ctx.conn.execute("DELETE FROM settings WHERE key IN (?, ?)",
                         (dhcp.SETTING_PREFIX + name, DESC_PREFIX + name))
        ctx.conn.commit()
        journal(ctx.conn, "nic_forget", name, user[0])
        error = (apply_all(name, user[0], proxy_changed=was.proxy)
                 if (was.enabled or was.proxy) else None)
        return {"ok": error is None, "apply_error": error}

    return router


def _lease_expiry(epoch: int) -> str | None:
    """‏dnsmasq כותב שניות epoch; ‏``0`` = חכירה בלי תפוגה → ``None``."""
    if not epoch:
        return None
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="seconds")


def _as_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str):
        return [p for p in value.replace(";", ",").split(",")]
    return []
