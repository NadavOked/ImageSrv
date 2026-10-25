"""הרצת השרת: python -m server.main --server-url http://10.44.12.10:8080

אותה כתובת משמשת את תפריט ה-GRUB ואת הסוכן — היא מה שנכתב לשורת
הפקודה של הקרנל, ולכן חייבת להיות הכתובת שהלקוחות רואים, לא localhost.
"""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import json
import os
import socket
import subprocess
import sys
import threading
import urllib.parse
from pathlib import Path

from . import deploy_net, drive_rates, identity


def _configure_stdio() -> None:
    """Keep startup messages printable on non-UTF-8 Windows consoles."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="backslashreplace")


class InterfaceDetectionError(RuntimeError):
    """‏`ip` קיים ורץ, אבל אף כרטיס אינו נושא את כתובת ה-server-url — או
    שהבדיקה עצמה נשברה. זהו שרת לינוקס אמיתי שהשידור שלו ייצא לכרטיס הלא
    נכון, ולכן עדיף להיכשל בגלוי מלשדר לרשת הרגילה (#514, #19)."""


def _ip_nics() -> list | None:
    """כרטיסים מ-`ip -json addr`. ‏None רק כשאין `ip` (תחנת פיתוח)."""
    try:
        out = subprocess.run(
            ["ip", "-json", "addr"], capture_output=True, text=True,
            check=True, stdin=subprocess.DEVNULL,
        ).stdout
    except FileNotFoundError:
        return None            # אין `ip` — לא לינוקס (תחנת פיתוח)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise InterfaceDetectionError(f"'ip -json addr' נכשל: {exc}") from exc
    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        raise InterfaceDetectionError(f"פלט 'ip -json addr' אינו JSON: {exc}") from exc


def _interface_for(server_url: str) -> str | None:
    """הכרטיס שנושא את כתובת ה-server-url — הוא ממשק וילן ההפצה.

    השידור (udp-sender) חייב לצאת דווקא ממנו: בלי ‎--interface‏ udpcast
    בוחר את ברירת המחדל של הניתוב, וברשת עם שני כרטיסים זה ה-LAN —
    השידור לא פוגש אף מקבל ונכשל, וגרוע מזה, הוא מדבר ברשת הרגילה (#19).

    מחזיר `None` **רק** כשאין `ip` כלל — תחנת פיתוח (לא לינוקס), שם
    השידור ממילא מזויף. ‏`ip` שקיים ונכשל, או שרץ ולא מצא התאמה, הם שרת
    אמיתי במצב שגוי — ומעלים `InterfaceDetectionError` במקום `None` שקט
    (עיקרון 5: פעולה שלא הצליחה לבדוק נכשלת, לא מוותרת) (#514).
    """
    host = urllib.parse.urlsplit(server_url).hostname
    nics = _ip_nics()
    if nics is None:
        return None
    # ‏--server-url עם שם מארח (ולא IP) לא יתאים ל-addr.local לעולם —
    # פותרים אותו לכתובותיו לפני ההשוואה.
    candidates = {host}
    try:
        candidates |= {info[4][0] for info in socket.getaddrinfo(host, None)}
    except (socket.gaierror, OSError):
        pass
    for nic in nics:
        for addr in nic.get("addr_info") or []:
            if addr.get("local") in candidates:
                return nic.get("ifname")
    raise InterfaceDetectionError(
        f"אף כרטיס אינו נושא את {host} — udp-sender היה משדר לכרטיס הלא "
        f"נכון. העבר ‎--interface‏ במפורש.")


def _address_for_interface(name: str) -> str | None:
    """IPv4 של הכרטיס — כתובת ה-bind של הסוכן/קיוסק (#1074).

    ‏None רק כשאין `ip` (תחנת פיתוח). כרטיס שקיים בלי IPv4, או `ip`
    שנכשל — `InterfaceDetectionError`, לא נפילה ל-0.0.0.0 בשקט.
    """
    nics = _ip_nics()
    if nics is None:
        return None
    for nic in nics:
        if nic.get("ifname") != name:
            continue
        for addr in nic.get("addr_info") or []:
            local = addr.get("local") or ""
            if local.count(".") == 3:
                return local
        raise InterfaceDetectionError(
            f"לכרטיס {name} אין כתובת IPv4 — הסוכן היה נקשר לכל הכרטיסים")
    raise InterfaceDetectionError(f"לא נמצא כרטיס בשם {name}")


def agent_uvicorn_config(app, host: str, port: int):
    """התצורה של מאזין הסוכן (8080) — ‏‎/boot של GRUB ו-API הסוכן.

    ‏#1423: ‏`http=ResetBootOnPeerClose` עונה ב-RST כש-GRUB סוגר חיבור
    ‎/boot. בלעדיו השרת נשאר ב-LAST-ACK על החיבור האחרון, ו-GRUB, שיוצא
    מאותם פורטי מקור בכל אתחול, נתקע עליו באתחול הבא.
    """
    import uvicorn

    from .grub_close import ResetBootOnPeerClose
    return uvicorn.Config(app, host=host, port=port, log_level="info",
                          http=ResetBootOnPeerClose)


def build_parser() -> argparse.ArgumentParser:
    """הדגלים של השרת, בנפרד מההרצה — כדי שאפשר יהיה לפרוס שורת פקודה
    בבדיקה בלי להרים uvicorn (‏#201: ה-e2e מרכיב argv, וצריך ראיה שהוא
    מתפרס כאן ולא נבלע)."""
    parser = argparse.ArgumentParser(description="ImageSrv server + console")
    # ‏#1088: אינו חובה עוד. ריק = הכתובת נקראת ממה שנרשם מהקונסולה
    # (`deploy:url`, בהדלקת DHCP על כרטיס), ואם גם זה חסר — "רשת ההפצה לא
    # הוגדרה": הסוכן על loopback בלבד, והקונסולה אומרת מה לעשות.
    # היחידה מעבירה ``${IMAGECTL_URL}`` גם כשהוא ריק (ארגומנט ריק אחד).
    parser.add_argument("--server-url", default="",
                        help="הכתובת שהלקוחות רואים, http בלבד (כמו במתקין). "
                             "ריק = מה שהוגדר מהקונסולה (#1088)")
    parser.add_argument("--tftp-root", default="/srv/tftp",
                        help="שורש ה-TFTP שהמתקין פרס אליו — לשם נכתב grub.cfg "
                             "כשרשת ההפצה מוגדרת מהקונסולה (#1088)")
    parser.add_argument("--data-dir", default="/var/lib/imagectl")
    parser.add_argument("--images", default="/srv/imagectl/images")
    # ‏#748: עץ הקוד עצמו — משם ``git describe``/``fetch``/``checkout``
    # של כפתור העדכון. ברירת המחדל היא תיקיית הקוד (ImageStore ->
    # server/main.py -> parents[1]), שזהה ל-``WorkingDirectory`` ביחידת
    # systemd (‏/opt/imagectl) בלי לצטט אותה כאן כברירת מחדל שנייה.
    parser.add_argument("--repo-dir", default=None,
                        help="עץ הגיט של השרת; ברירת מחדל: תיקיית הקוד")
    parser.add_argument("--boot-dir", default="/srv/imagectl/boot",
                        help="הקרנל וה-initramfs שהמתקין הניח; מוגש תחת ‎/boot")
    parser.add_argument("--dhcp-leases", default=identity.DEFAULT_LEASES,
                        help="קובץ החכירות של dnsmasq — שומר הזהות (#855) "
                             "משווה אליו את כתובת המקור של hello/progress")
    parser.add_argument("--interface", default=None,
                        help="ממשק השידור; ברירת מחדל: הכרטיס של --server-url")
    parser.add_argument("--extra-cmdline",
                        default=os.environ.get("IMAGECTL_EXTRA_CMDLINE", ""),
                        help="תוספות לשורת הקרנל של הסוכן (למשל קונסולה "
                             "טורית ו-debug במעבדה); גם IMAGECTL_EXTRA_CMDLINE")
    # ‏#1074 (R20-F1): ברירת המחדל היא כתובת כרטיס ההפצה, לא 0.0.0.0.
    # ‏None כאן = "לא ביקשו"; ‏main ממלא מ-`--interface` (או מ-server-url
    # כשאין `ip`). ‏0.0.0.0 רק בבקשה מפורשת, ועם אזהרה ביומן.
    parser.add_argument("--host", default=None,
                        help="כתובת ה-bind של הסוכן. ברירת מחדל: "
                             "כתובת כרטיס ההפצה (--interface). 0.0.0.0 רק "
                             "בבקשה מפורשת (R20-F1)")
    parser.add_argument("--port", type=int, default=8080,
                        help="פורט הסוכן: hello/pulls/progress/images ו-/boot")
    # ‏#731 (tracer 1 של #703): הקונסולה/הניהול מאזינים על פורט **נפרד**
    # מהסוכן, כדי שחומת אש תוכל לחשוף רק אותו לווילן הניהול.
    parser.add_argument("--console-port", type=int, default=8081,
                        help="פורט הקונסולה/הניהול: /api/console ו-/console")
    # ‏#770 (tracer 4 של #703): הקונסולה נקשרת פר-ממשק — לכרטיס הניהול/
    # המשרדים בלבד, ולא ל-`--host` (וילן ההפצה). קשירה ל-`0.0.0.0` חשפה
    # את API הניהול בכל הכרטיסים (כולל ההפצה/כיתות) והתנגשה עם ה-preseed
    # על 8081. ברירת המחדל **fail-closed** היא loopback ולא `0.0.0.0`:
    # בלי כתובת ניהול מפורשת הקונסולה נגישה מקומית בלבד, ואינה תופסת 8081
    # בממשקים אחרים — "הגבול הוא ה-socket" (#703), ועיקרון 5 (השומר עובד
    # כברירת מחדל, לא רק אם המפעיל זכר דגל). המפעיל קובע את כתובת הניהול
    # דרך היחידה (systemctl edit). TLS לקונסולה הוא tracer 5.
    # ‏#1088: הדגל מקבל גם **שם כרטיס** (`eth0`/`ens18`) — כרטיס וילן השרתים
    # הוא לקוח DHCP של המכללה; השרת פותר את הכתובת בעלייה ומאזין מחדש
    # כשהיא משתנה (‏`ifaddr.AddressWatcher`). בלי כתובת על הכרטיס (lease
    # שעוד לא הגיע) הקונסולה על 127.0.0.1 בלבד, עד שתגיע.
    parser.add_argument("--console-host", default="127.0.0.1",
                        help="כתובת ה-bind של הקונסולה — כרטיס הניהול/"
                             "המשרדים בלבד: כתובת, או שם כרטיס (ואז מאזין "
                             "מחדש בהחלפת כתובת, #1088). ברירת מחדל loopback "
                             "(fail-closed); לעולם לא 0.0.0.0 בלי בקשה מפורשת")
    # ‏#703 (tracer 5): הקונסולה מוגשת ב-HTTPS בלבד — תעודה חתומה-עצמית
    # שנוצרת פעם אחת ל-`<data_dir>/console-tls/` ונשארת. ‏HTTP על 8081
    # אינו מוגש (fail-closed, בלי הפניה). ‏`off` מותר **רק** על loopback —
    # הרצת פיתוח ו-`tools/e2e`; על כרטיס ניהול הסיסמה הייתה עוברת גלויה.
    parser.add_argument("--console-tls", choices=("on", "off"), default="on",
                        help="TLS לקונסולה (8081). off מותר רק עם "
                             "--console-host על loopback (127.0.0.1) — "
                             "פיתוח/e2e; ברירת מחדל on")
    # ‏#201: הפורט שהשידור תופס בפועל (וגם portbase+1). בייצור לא נוגעים
    # בו — ‏#1826: הסוכן מקבל אותו ב-`session.portbase`, אבל סוכן ישן מצפה
    # ל-9000 וחומת האש של המתקין פותחת 9000-9001 בלבד (‏`--mcast-ports`).
    # הדגל קיים בשביל מי שמריץ שרת אמיתי במקום שאסור לו להתנגש בהפצה:
    # ‏`tools/e2e/harness.py`. **בכוונה דגל ולא משתנה סביבה** — משתנה
    # סביבה שנשאר תקוע ב-shell או ביחידה משנה את פורט ההפצה של הייצור
    # בשקט, ודווקא כאן זה הכשל שמנסים למנוע. דגל נראה ב-`ps`.
    parser.add_argument("--sender-portbase", type=int, default=None,
                        help="portbase של udp-sender; ברירת המחדל היא 9000 "
                             "(הייצור). לשימוש הסימולציה בלבד")
    parser.add_argument("--sender-simulate", action="store_true",
                        help="שולח מדומה — בלי udp-sender ובלי בדיקת פורטים; "
                             "tools/e2e_simulation.py בלבד, לעולם לא בשרת אמיתי")
    # ‏Storage Nodes (#655/#723): תפקיד ההתקנה של השרת. **תצורת שרת
    # בלבד** — אינה נכנסת לשורת הפקודה של הקרנל (עיקרון 2), רק להגדרות.
    # ‏#732: ברירת המחדל היא ``None`` (לא ``standalone``). ריסטארט בלי
    # הדגל **אינו** כותב תפקיד, ולכן אינו דורס ``secondary`` שמור בשקט
    # (fail-open קריטי — משני שהופך לראשי חושף ניהול+בין-סניפים). התפקיד
    # נקרא ממילא fail-closed; כותבים רק כשהדגל ניתן במפורש.
    parser.add_argument("--storage-role", choices=("standalone", "secondary"),
                        default=None,
                        help="תפקיד השרת: standalone (רשאי להחזיק שרתי משנה) או "
                             "secondary. ללא הדגל — התפקיד השמור נשאר כפי שהוא")
    parser.add_argument("--primary-url", default=None,
                        help="כתובת השרת הראשי — חובה ל-secondary, מתעלמים "
                             "ב-standalone")
    # ‏#740 (tracer 2.1): מאזין ה-enrollment הבין-שרתי — **נפרד** מהסוכן/
    # קונסולה/קיוסק, TLS 1.3 + mTLS בלבד, וללא נתיבי ניהול או עוגיות. עולה
    # רק כשמסופק ‏--interserver-host/port והשרת הוא secondary (‏#1313: משני
    # בלי הדגל — על ‏--console-host שאינו loopback). ‏--interserver-url
    # הוא הכתובת המפורשת שהמשני מציג (מאומתת קפדנית: https+פורט מפורש).
    parser.add_argument("--interserver-host", default=None,
                        help="כתובת ה-bind של מאזין ה-enrollment הבין-שרתי — "
                             "כתובת או שם כרטיס (#1088, כמו --console-host)")
    parser.add_argument("--interserver-port", type=int, default=8443,
                        help="פורט מאזין ה-enrollment הבין-שרתי (mTLS 1.3)")
    parser.add_argument("--interserver-url", default=None,
                        help="הכתובת הבין-שרתית המפורשת ששרת המשנה מציג "
                             "(https://host:port/…, פורט מפורש חובה)")
    return parser


def _deploy_state(args) -> deploy_net.DeployState:
    """‏#1088: מקור כתובת ההפצה — הדגל, מה שנרשם מהקונסולה, או "לא הוגדרה".

    ה-DB נפתח כאן לרגע **רק** כשאין דגל: עם `--server-url` אין מה לקרוא,
    ובדיקות שמזייפות את כל מה שאחרי הפרסור אינן נוגעות בדיסק.
    """
    if args.server_url:
        return deploy_net.DeployState("cli", args.interface or None, args.server_url)
    from .db import connect
    data_dir = Path(args.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    conn = connect(data_dir / "imagectl.db")
    try:
        return deploy_net.resolve(conn, None, None)
    finally:
        conn.close()


def _resolve_bind_host(parser, flag: str, value: str | None) -> tuple[str | None, str | None]:
    """‏#1088: ‏(כתובת, שם-כרטיס) לדגל bind שמקבל כתובת **או** שם כרטיס.

    כתובת → ‏(כתובת, None). שם כרטיס → הכתובת שלו עכשיו (או None כשאין
    lease עדיין) ושמו. ‏`ip` שנכשל, או כרטיס שאינו קיים — ‏`parser.error`:
    כתובת שלא הצלחנו לקרוא אינה "אין כתובת" (עיקרון 5).
    """
    from . import ifaddr
    if not value or not ifaddr.is_interface_name(value):
        return value, None
    try:
        return ifaddr.read_ipv4(value), value
    except ifaddr.AddressLookupError as exc:
        parser.error(f"{flag} {value}: {exc}")
    return None, None                                    # pragma: no cover


class _ServerGroup(list):
    """‏#705: כמה מאזיני mTLS (אחד לכל כרטיס) כאובייקט אחד למנהל המאזינים."""

    @property
    def port(self):
        return self[0].port if self else None

    def stop(self) -> None:
        for server in self:
            server.stop()


def _raise(error: str | None) -> None:
    if error:
        raise RuntimeError(error)


def ifaddr_current(name: str) -> str | None:
    """הכתובת הנוכחית של כרטיס לדוגם — None כשאין או כשלא נקרא (הדוגם ינסה)."""
    from . import ifaddr
    try:
        return ifaddr.read_ipv4(name)
    except ifaddr.AddressLookupError:
        return None


def _nic_baseline(nic, args, runtime, console_iface, console_addr, inter_iface, inter_addr) -> None:
    """‏#705: על איזה כרטיס כל מאזין נקשר **היום** — אותם תנאים בדיוק כמו
    הקשירה למטה (‏#1310 ראשי על כרטיס הקונסולה, ‏#1313 משני בלי הדגל)."""
    from . import storage_nodes
    from .interserver_auth import is_loopback
    if console_iface is None and console_addr and not is_loopback(console_addr):
        nic.base.console = _owner_nic(console_addr)
    if storage_nodes.role(runtime.conn) == "secondary":
        if args.interserver_host:
            nic.base.interserver = inter_iface or _owner_nic(inter_addr or args.interserver_host)
        elif (args.storage_role == "secondary" and args.console_host
              and not is_loopback(args.console_host)):
            nic.base.interserver = nic.base.console
    elif console_iface or (console_addr and not is_loopback(console_addr)):
        nic.base.interserver = nic.base.console


def _owner_nic(address: str | None) -> str | None:
    """הכרטיס שנושא את הכתובת — או None (כולל כשלא ניתן לקרוא)."""
    from . import dhcp, nic_services
    if not address:
        return None
    try:
        found = nic_services.via(address, dhcp.list_interfaces())
    except OSError:
        return None
    return found if found not in (None, "lo") else None


def _primary_alive(runtime, args, hostname: str):
    """‏#1310: יצרן המאזין "אתה שם?" של הראשי — ``None`` במשני (שם 8443 הוא
    ה-mTLS, ובלעדיו אינו נפתח כלל). אחרת פונקציה ``כתובת → (יצרנים,
    כתובות)``, כמו ``console_servers``: ‏#1088 בונה מחדש בחילוף כתובת, ובלי
    כתובת עדיין — אין מאזין, לא loopback (אין מה לשאול מקומית).

    הזהות הבין-שרתית נוצרת כאן אם אין — אותה זהות שה-pairing היה יוצר
    מאוחר יותר, ולכן הטביעה שהמתקין של המשני מציג היא המפתח שהמשני יצמיד."""
    import uvicorn

    from . import primary_alive, storage_nodes
    from .app import create_alive_app
    if storage_nodes.role(runtime.conn) == "secondary":
        return None
    ident = storage_nodes.ensure_identity(runtime.conn, args.data_dir,
                                          host_sans=[h for h in (hostname,) if h])
    app = create_alive_app(runtime, ident["server_spki"])

    def servers(addr: str | None) -> tuple[list, list[str]]:
        if not addr:
            return [], []
        config = primary_alive.server_config(app, ident, addr, args.interserver_port)
        return [lambda: uvicorn.Server(config)], [addr]
    return servers


def _start_igmp_querier(runtime, deploy, interface: str | None) -> None:
    """‏#1972: ה-querier על כרטיס ההפצה — ותמיד שורה במסך הבריאות. כאן בלבד
    (כמו `vm_host`): בבדיקות אין סוקט raw ואין תהליכון."""
    from . import health, ifaddr, igmp_querier, ports
    hooks = getattr(runtime, "health_hooks", None)
    if hooks is None:          # בדיקות של main מחליפות את create_runtime
        return
    if not (deploy.configured and interface):
        hooks["igmp_querier"] = lambda: ("not_applicable", health.NOT_APPLICABLE)
        return
    nic_services = getattr(runtime.ctx, "nic_services", None)
    querier = igmp_querier.Querier(
        runtime.conn, interface, lambda: ifaddr.read_ipv4(interface),
        gate=nic_services.multicast_refused if nic_services is not None else None,
        hears=lambda: igmp_querier.firewall_hears(ports.read_nft_ruleset(), interface))
    hooks["igmp_querier"] = querier.health
    threading.Thread(target=querier.run_forever, daemon=True, name="igmp-querier").start()


def _start_ups_monitor(runtime) -> None:
    """‏#1973: מנטר ה-UPS — יומן בנפילת חשמל, כיבוי מסודר בסוללה נמוכה. כאן
    בלבד: בבדיקות אסור שתהליכון יקרא ל-`systemctl poweroff`."""
    from . import ups
    hooks = getattr(runtime, "health_hooks", None)
    if hooks is None:          # בדיקות של main מחליפות את create_runtime
        return
    ctx = runtime.ctx
    monitor = ups.UpsMonitor(runtime.conn,
                             stop_work=lambda reason: ups.stop_all_work(ctx, reason))
    ctx.ups = monitor
    hooks["ups"] = monitor.health
    threading.Thread(target=monitor.run_forever, daemon=True, name="ups-monitor").start()


def main() -> None:
    _configure_stdio()
    parser = build_parser()
    args = parser.parse_args()

    from . import ifaddr
    deploy = _deploy_state(args)

    if deploy.source == "cli":
        if not args.server_url.startswith("http://"):
            # אותו כלל כמו במתקין: ה-GRUB החתום נבנה בלי TLS.
            parser.error("--server-url חייב להתחיל ב-http://")

        # ‏#498: המארח ב---server-url חייב להיות כתובת IP מספרית, לא שם.
        # ‏`hello.off_deploy_vlan` משווה את ה-sockname של החיבור (תמיד כתובת)
        # ל-hostname של הכתובת הזו; שם מארח לעולם אינו שווה לכתובת, ה-except
        # בלע את ה-ValueError, ו**כל** מכונה סווגה כ"בתוך וילן ההפצה" — כלומר
        # חובת הכניסה מחוץ לווילן (#42) לא נורתה לעולם. "לא הצלחנו לסווג"
        # אינו "בפנים" (עיקרון 5). פתרון DNS פעם אחת בעלייה היה נשבר בשקט
        # כשהרשומה משתנה — ולכן השרת מסרב לעלות ואומר למה. המתקין ממילא גוזר
        # את הכתובת מהכרטיס (install/setup-boot-server.sh).
        host = urllib.parse.urlsplit(args.server_url).hostname
        try:
            ipaddress.ip_address(host)
        except ValueError:
            parser.error(f"--server-url חייב לשאת כתובת IP מספרית ולא שם מארח "
                         f"(התקבל: {host!r}). עם שם, השרת אינו יכול לדעת אם "
                         f"hello הגיע מווילן ההפצה, וחובת הכניסה מחוץ לווילן "
                         f"לא נאכפת (#498, #42)")

    # תצורת ה-Storage Node מאומתת כאן, לפני הרמת השרת: משני בלי כתובת
    # שרת ראשי נכשל בקול (עיקרון 5), ולא מתגלגל ל-standalone בשקט.
    # ‏#732: רק כשהדגל ניתן. בלעדיו התפקיד השמור נשאר, ואין מה לאמת.
    if args.storage_role is not None:
        from .storage_nodes import StorageConfigError, normalize_config
        try:
            normalize_config(args.storage_role, args.primary_url)
        except StorageConfigError as exc:
            parser.error(str(exc))

    # ‏#740: הכתובת הבין-שרתית מאומתת כאן, לפני הרמת השרת — https+פורט מפורש,
    # בלי אישורים/query/fragment (עיקרון 5: downgrade נדחה, לא "מתוקן" בשקט).
    if args.interserver_url is not None:
        from .interserver_auth import (InterserverURLError,
                                       parse_interserver_url)
        try:
            parse_interserver_url(args.interserver_url)
        except InterserverURLError as exc:
            parser.error(str(exc))

    # ‏#1088: ‏--console-host כשם כרטיס — הכתובת נפתרת עכשיו; ‏None = הכרטיס
    # קיים בלי כתובת (lease שעוד לא הגיע), והקונסולה עולה על loopback עד
    # שהדוגם יראה כתובת. אותו דבר ל---interserver-host.
    console_addr, console_iface = _resolve_bind_host(parser, "--console-host",
                                                     args.console_host)
    inter_addr, inter_iface = _resolve_bind_host(parser, "--interserver-host",
                                                 args.interserver_host)
    machine_hostname = socket.gethostname()

    # ‏#703 (tracer 5): TLS לקונסולה — הדגל מאומת כאן (off רק על loopback),
    # והתעודה נוצרת/נטענת **לפני** הרמת השרת: חצי זהות או תיקייה שאי
    # אפשר לכתוב אליה עוצרים בקול, לא קונסולה שעלתה בלי TLS (עיקרון 5).
    # ‏#1088: עם שם כרטיס ה-SAN הוא הכתובת **הנוכחית** + שם המארח; בחילוף
    # כתובת התעודה אינה מתחדשת (הדפדפן כבר אישר אותה) — היא תקפה לפי השם.
    from .console_tls import (ConsoleTLSError, check_tls_flag,
                              ensure_console_cert)
    console_tls = None
    try:
        check_tls_flag(args.console_tls, args.console_host)
        if args.console_tls == "on":
            console_tls = ensure_console_cert(
                args.data_dir, console_addr or "127.0.0.1", hostname=machine_hostname)
    except ConsoleTLSError as exc:
        parser.error(str(exc))
    if console_tls is not None and console_addr and console_addr not in console_tls.sans:
        # תעודה שהמפעיל העלה לעולם אינה מוחלפת אוטומטית.
        print(f"warning: console TLS certificate SANs {list(console_tls.sans)} "
              f"do not include --console-host {console_addr}; upload a matching certificate")

    # ‏#1088: כתובת ההפצה וכרטיסה — מהדגל (המעבדה, שרת שהותקן עם כרטיס
    # הפצה), ממה שנרשם מהקונסולה, או "לא הוגדרה". שלושת המקורות נאמרים.
    if deploy.source == "cli":
        server_url = args.server_url
        if args.interface:
            interface = args.interface
        else:
            try:
                interface = _interface_for(server_url)
            except InterfaceDetectionError as exc:
                # שרת אמיתי שלא ניתן לזהות לו ממשק שידור לא עולה בשקט על
                # הכרטיס הלא נכון — הוא עוצר ומבקש ‎--interface‏ (#514).
                parser.error(str(exc))
    elif deploy.source == "console":
        # הכרטיס שנרשם חייב לשאת את הכתובת שנרשמה — אחרת השרת **אינו**
        # נופל (זה היה משאיר את המפעיל בלי קונסולה) אלא חוזר
        # ל"לא הוגדרה", ואומר זאת ביומן ובמסך הבריאות.
        server_url, interface = deploy.url, deploy.interface
        try:
            found = _interface_for(server_url)
        except InterfaceDetectionError as exc:
            print(f"warning: deploy network {deploy.interface} {deploy.url} — {exc}; "
                  "back to 'not configured'")
            deploy = deploy_net.DeployState("none", None, None)
        else:
            if found not in (None, interface):
                print(f"warning: deploy address {deploy.url} is on {found}, not on "
                      f"{interface} — using {found}")
                interface = found
    if not deploy.configured:
        server_url, interface = deploy_net.deploy_url(deploy_net.UNCONFIGURED_HOST,
                                                      args.port), None
        print("deploy network: not configured — agent on "
              f"{deploy_net.UNCONFIGURED_HOST} only; set it from the console "
              "(network page: address, then DHCP) (#1088)")
    if interface:
        print(f"broadcast interface: {interface}")
    elif deploy.configured:
        print("broadcast interface: none (no `ip` — dev station, "
              "broadcast is simulated)")
    host = urllib.parse.urlsplit(server_url).hostname

    # ‏#1074: הסוכן (8080) נקשר לכתובת כרטיס ההפצה. ‏0.0.0.0
    # רק אם המפעיל ביקש במפורש — ואז נאמר, לא בשקט (R20-F1).
    if args.host is None:
        nic_addr = None
        if interface:
            try:
                nic_addr = _address_for_interface(interface)
            except InterfaceDetectionError as exc:
                parser.error(str(exc))
        args.host = nic_addr or host
    if args.host == "0.0.0.0":
        print("warning: --host 0.0.0.0 — הסוכן מאזין על כל "
              "הכרטיסים (R20-F1). ברירת המחדל היא כתובת כרטיס ההפצה.")

    import uvicorn

    from . import dhcp_host, ports
    from .app import create_agent_app, create_console_app, create_runtime
    from .db import journal

    # ‏#996: מנהל המאזינים — נבנה לפני ה-runtime כי הוא מוזרק אליו
    # (‏health_hooks["port_listeners"], כמו כל hook אחר), ומאוכלס אחרי
    # שהאפליקציות קיימות.
    listeners = ports.Listeners()

    # ‏#1088: מה שהקונסולה צריכה כדי להשלים את ההתקנה בהדלקת DHCP —
    # ומה שמסך הבריאות מציג על רשת ההפצה ועל כתובת כרטיס השרתים.
    primary_ip = None
    if args.primary_url:
        primary_host = urllib.parse.urlsplit(args.primary_url).hostname or ""
        try:
            ipaddress.ip_address(primary_host)
            primary_ip = primary_host
        except ValueError:
            pass
    deploy_ctx = deploy_net.DeployContext(
        state=deploy, agent_port=args.port, tftp_root=Path(args.tftp_root),
        repo_dir=Path(args.repo_dir) if args.repo_dir else Path(__file__).resolve().parents[1],
        servers_interface=console_iface, primary_ip=primary_ip)

    def servers_nic_status():
        if not console_iface:
            return None
        try:
            address = ifaddr.read_ipv4(console_iface)
        except ifaddr.AddressLookupError as exc:
            return ifaddr.servers_nic_status(console_iface, None, None,
                                             lookup_failed=str(exc))
        return ifaddr.servers_nic_status(console_iface, address,
                                         ifaddr.dhclient_lease(console_iface),
                                         hostname=machine_hostname)

    # ‏#731/#738: runtime **אחד** — ‏DB, ספרייה, שולח, ה-sweep החד-פעמי —
    # ושלוש אפליקציות מעליו. תהליך אחד, שלושה מאזינים; לא שלושה תהליכים
    # על אותו SQLite (זה היה נגמר ב-"cannot commit" אקראי, ‏CLAUDE.md).
    #
    # ‏#141: ‏known_macs_hooks מודלק כאן, ורק כאן — במפורש, לא כברירת
    # מחדל בתוך create_runtime, כי הוספה/מחיקה של מכונה מהקונסולה קוראת
    # לאותם hooks בכל בדיקה בחבילה (registry.all_macs → dhcp.render_known_macs
    # → apply_known_macs). ברירת מחדל אמיתית שם הייתה כותבת ל-
    # ‏/etc/imagectl/known-macs ומריצה systemctl reload dnsmasq בכל הרצת
    # ‏pytest.
    runtime = create_runtime(args.data_dir, args.images, server_url,
                             boot_dir=args.boot_dir, interface=interface,
                             sender_portbase=args.sender_portbase,
                             sender_simulate=args.sender_simulate,
                             storage_role=args.storage_role,
                             primary_url=args.primary_url,
                             known_macs_hooks={"apply": dhcp_host.apply_known_macs},
                             # ‏#1013: קובץ ה-dnsmasq הראשי נגזר מה-DB בעלייה —
                             # כאן בלבד, מאותו טעם כמו known_macs_hooks.
                             sync_dnsmasq=True,
                             # הכרעת נדב 15/10: הגג והפתיחה של הקצב לפי מהירות
                             # כרטיס ההפצה — כאן בלבד, כמו sync_dnsmasq.
                             link_speed=drive_rates.read_link,
                             # ‏#855: מותקן כאן תמיד — כמו known_macs_hooks,
                             # ומאותו טעם: ברירת מחדל ב-create_runtime הייתה
                             # קוראת את קובץ המכונה בכל הרצת pytest.
                             identity_hooks={"leases": identity.LeaseFile(args.dhcp_leases)},
                             extra_cmdline=tuple(args.extra_cmdline.split()),
                             repo_dir=args.repo_dir,
                             console_tls=console_tls,
                             health_hooks={"port_listeners": listeners,
                                           "deploy": lambda: deploy_ctx.state,
                                           "servers_nic": servers_nic_status},
                             deploy=deploy_ctx,
                             # ‏#1372: המארח של עדכון ה-VM — שואל, ומכין אם חסר.
                             vm_host=True,
                             # ‏#1494: used_bytes לאימג'ים ישנים מכותרת partclone.
                             backfill_used_bytes=True,
                             # ‏#1805: הגיבוב לקריאה החוזרת מהדיסק אחרי כתיבה.
                             backfill_readback=True,
                             # ‏#1447: אזור הזמן של המערכת = ההגדרה (timedatectl).
                             apply_timezone=True, apply_timesync=True)
    # ‏#1372: עדכון מהכפתור רץ בסקריפט של התג **הישן**, ולכן ההכנה של הגרסה
    # הזו אינה תלויה בו: מארח שאינו מוכן — install/vmupdate-host.sh שלנו, ברקע.
    # ‏getattr: בדיקות של main מחליפות את create_runtime ברשומה משלהן.
    vm_host = getattr(runtime, "vm_host", None)
    if vm_host is not None:
        threading.Thread(target=vm_host.ensure, daemon=True, name="vmhost-ensure").start()
    if console_tls is not None and console_tls.regenerated:
        # ‏#1417: נרשם רק כאן — לפני create_runtime אין runtime, וכל שימוש
        # מוקדם יותר קרס (UnboundLocalError) בעלייה הראשונה אחרי ההחלפה
        journal(runtime.conn, "console_tls_regenerated", console_addr or "127.0.0.1",
                f"old_sha256={console_tls.previous_fingerprint_sha256} "
                f"new_sha256={console_tls.fingerprint_sha256}")
    _start_igmp_querier(runtime, deploy, interface)   # #1972
    _start_ups_monitor(runtime)   # #1973
    agent_app = create_agent_app(runtime)
    console_app = create_console_app(runtime)

    # ‏#996: ה-`Config` נבנה פעם אחת; ה-`Server` נבנה מחדש בכל פתיחה
    # (‏uvicorn.Server אינו רב-פעמי), ולכן המנהל מקבל **יצרנים**.
    agent_config = agent_uvicorn_config(agent_app, args.host, args.port)
    configs: dict[tuple[str, str], object] = {("agent", args.host): agent_config}

    def agent_factory(h: str):
        """‏#705: יצרן לכל כתובת — הסוכן נקשר לכל כרטיס שבו "אתחול מהרשת" דלוק."""
        if ("agent", h) not in configs:
            configs[("agent", h)] = agent_uvicorn_config(agent_app, h, args.port)
        cfg = configs[("agent", h)]
        return lambda: uvicorn.Server(cfg)
    # ‏#770: הקונסולה על כרטיס הניהול (`--console-host`) בלבד — לא `--host`.
    # ‏#703 (tracer 5): ‏`ssl_certfile`/`ssl_keyfile` של uvicorn (stdlib ssl)
    # על **כל** מאזין קונסולה — ורק עליהם; הסוכן נשאר http.
    console_ssl = console_tls.uvicorn_kwargs() if console_tls else {}
    console_scheme = "https" if console_tls else "http"
    from .interserver_auth import is_loopback

    def console_servers(addr: str | None) -> tuple[list, list[str]]:
        """היצרנים והכתובות של מאזין הקונסולה לכתובת נתונה — פונקציה, כי
        ‏#1088 בונה אותם מחדש בכל חילוף כתובת של כרטיס השרתים.

        ‏#904: כשכרטיס הניהול אינו loopback, הקונסולה מאזינה **גם** על
        ‏127.0.0.1 — חלון ה-pairing והפינוי של המשני (`require_local`,
        ‏#740) נאכפים על כתובת ה-peer, ומחיבור לכרטיס הניהול ה-peer לעולם
        אינו loopback: במעבדה `POST /storage-pairing-window` החזיר 403 תמיד,
        והמפעיל נאלץ ל-`ssh -L`. ‏uvicorn 0.32 (דביאן 13) קושר `host` יחיד,
        ולכן זה `Server` שני על אותו loop — כמו 8080/8081. loopback אינו
        מרחיב את החשיפה (אותה מכונה בלבד), ולכן אינו שובר את fail-closed
        של #770. בלי כתובת על הכרטיס (#1088) — loopback בלבד, עד שתגיע.
        """
        hosts = with_loopback([addr] if addr else [])
        return [console_factory(h) for h in hosts], hosts

    def with_loopback(hosts: list[str]) -> list[str]:
        return hosts if any(is_loopback(h) for h in hosts) else [*hosts, "127.0.0.1"]

    def console_factory(h: str):
        if ("console", h) not in configs:
            configs[("console", h)] = uvicorn.Config(
                console_app, host=h, port=args.console_port, log_level="info", **console_ssl)
        cfg = configs[("console", h)]
        return lambda: uvicorn.Server(cfg)

    # ‏#705: מאז שהמפעיל ערך שירותים לכרטיס — הכתובות נגזרות מהשירותים של כל
    # כרטיס. עד אז (`edited` = ‏False) הכול נקשר בדיוק כמו קודם: שדרוג אינו משנה.
    # runtime מזויף (בדיקות של main) אינו נושא אותו — ואז הכול כמו קודם.
    nic = getattr(getattr(runtime, "ctx", None), "nic_services", None)
    if nic is not None:
        _nic_baseline(nic, args, runtime, console_iface, console_addr, inter_iface, inter_addr)
    nic_plan = nic.current_plan() if nic is not None and nic.edited() else None
    if nic_plan is not None:
        agent_hosts = nic_plan["hosts"]["http_boot"] or [deploy_net.UNCONFIGURED_HOST]
        console_hosts = with_loopback(nic_plan["hosts"]["http_console"])
        console_factories = [console_factory(h) for h in console_hosts]
        print("per-NIC services (#705): binds follow the console's choice per interface")
    else:
        agent_hosts = [args.host]
        console_factories, console_hosts = console_servers(console_addr)
    console_binds = " + ".join(f"{console_scheme}://{h}:{args.console_port}"
                               for h in console_hosts)
    listeners.add("http_boot", [agent_factory(h) for h in agent_hosts],
                  port=args.port, hosts=agent_hosts)
    listeners.add("http_console", console_factories,
                  port=args.console_port, hosts=console_hosts)
    print(f"agent on {' + '.join(agent_hosts)}:{args.port}"
          f"  console on {console_binds}")
    if console_iface:
        print(f"console interface: {console_iface} "
              f"({console_addr or 'no address yet — loopback until a lease arrives'})")
    if console_tls is not None:
        # טביעת האצבע מודפסת כדי שהמפעיל ישווה אותה למה שהדפדפן מציג
        # באישור החד-פעמי — ולא יאשר סתם (#703).
        print(f"console TLS: self-signed certificate {console_tls.cert_path}"
              f"  SHA-256 {console_tls.fingerprint_sha256}")
    else:
        print("console TLS: off (loopback only)")

    # ‏#1310: הראשי עונה על 8443 ל"אתה שם?" בלבד — על כרטיס השרתים, שהוא
    # כרטיס הקונסולה. קונסולה על loopback בלבד = אין כרטיס שרתים = אין מאזין.
    alive_servers = None
    if console_iface or (console_addr and not is_loopback(console_addr)) or nic_plan is not None:
        alive_servers = _primary_alive(runtime, args, machine_hostname)
    if alive_servers is not None:
        alive_factories, alive_hosts = alive_servers(console_addr)
        if nic_plan is not None:
            alive_hosts = nic_plan["hosts"]["interserver"]
            alive_factories = [f for h in alive_hosts for f in alive_servers(h)[0]]
        listeners.add("interserver", alive_factories,
                      port=args.interserver_port, hosts=alive_hosts)
        print(f"primary alive (#1310) on "
              f"{alive_hosts[0] if alive_hosts else console_iface}:{args.interserver_port}"
              "  (GET only, one path)")

    watchers = []

    def watch(iface: str, current: str | None, rebind) -> None:
        """‏#1088: דוגם את כתובת הכרטיס; בשינוי — יומן + מאזין מחדש.
        ‏rebind שנכשל מחזיר False כדי שהדוגם ינסה שוב בדגימה הבאה (למשל
        לפני שלולאת האירועים עלתה)."""
        def on_change(old, new):
            label = f"{iface} {old or '-'} → {new or '-'}"
            print(f"address changed on {label}")
            try:
                rebind(new)
            except Exception as exc:                                # noqa: BLE001
                print(f"rebind on {label} failed: {exc}")
                return False
            journal(runtime.conn, "bind_address_changed", label)
            return True

        watchers.append(ifaddr.AddressWatcher(
            iface, current=current, on_change=on_change,
            on_error=lambda msg: print(f"address lookup on {iface} failed: {msg}")).start())

    if console_iface:
        def rebind_console(new: str | None) -> None:
            if nic is not None and nic.edited():   # ‏#705: הכתובות נגזרות מהשירותים
                _raise(apply_listeners(nic))
                return
            factories, hosts = console_servers(new)
            listeners.request_rebind("http_console", hosts=hosts, factories=factories)
            if alive_servers is not None:
                factories, hosts = alive_servers(new)
                listeners.request_rebind("interserver", hosts=hosts, factories=factories)
            if new and console_tls is not None and new not in console_tls.sans:
                journal(runtime.conn, "console_tls_by_name_only",
                        f"הכתובת השתנתה ל-{new} — התעודה תקפה לפי שם "
                        f"{machine_hostname} בלבד")
        watch(console_iface, console_addr, rebind_console)

    # ‏#740: מאזין ה-enrollment הבין-שרתי (mTLS 1.3) עולה רק על משני, וכשניתן
    # ‏--interserver-host (או, ב-#1313, ‏--console-host של משני). הוא threaded
    # (pyOpenSSL terminator) לצד ה-uvicorn —
    # ‏#996: גם הוא במנהל המאזינים, כמתג `interserver` (start/stop של ה-thread).
    from . import storage_nodes
    inter_host = args.interserver_host
    if (not inter_host and args.storage_role == "secondary"
            and args.console_host and not is_loopback(args.console_host)):
        # ‏#1313: משני שהותקן לפני התיקון — ה-override שלו (שהשדרוג אינו
        # כותב מחדש) בלי ‏--interserver-host, והמאזין לא עלה מעולם. המתקין
        # שם את שניהם על כרטיס השרתים, ולכן ‏--console-host הוא הכרטיס הנכון.
        # רק כשה-unit עצמו אומר secondary (לא תפקיד שמור פגום, #746), ולא
        # על loopback — לשם הראשי לעולם לא יגיע.
        inter_host, inter_addr, inter_iface = args.console_host, console_addr, console_iface
        print(f"interserver: no --interserver-host — using --console-host {inter_host} (#1313)")
    interserver_rebind = None
    if inter_host and storage_nodes.role(runtime.conn) == "secondary":
        from . import interserver_api
        if inter_iface:
            host_sans = [h for h in (inter_addr, machine_hostname) if h]
        else:
            host_sans = [inter_host]
        if args.interserver_url:
            from .interserver_auth import parse_interserver_url
            host_sans.append(parse_interserver_url(args.interserver_url)[0])
        ident = storage_nodes.ensure_identity(runtime.conn, args.data_dir,
                                              host_sans=host_sans)

        def make_interserver_start(bind_addr: str | None):
            def start_interserver():
                if not bind_addr:
                    raise OSError(f"interserver: לכרטיס {inter_iface} אין כתובת עדיין")
                server = interserver_api.InterserverTLSServer(
                    runtime.ctx, bind_addr, args.interserver_port,
                    ident["cert_pem"], ident["key_pem"], data_dir=args.data_dir,
                    console_app=console_app).start()   # #1179
                print(f"interserver (mTLS) on {bind_addr}:{server.port}"
                      f"  node_id {ident['node_id']}  spki {ident['server_spki'][:16]}…")
                return server
            return start_interserver

        def make_interserver_group(hosts: list[str]):
            """‏#705: מאזין mTLS לכל כרטיס שבו "בין-שרתים" דלוק."""
            def start_group():
                group = _ServerGroup()
                try:
                    for h in hosts:
                        group.append(make_interserver_start(h)())
                except OSError:
                    group.stop()
                    raise
                return group
            return start_group

        def interserver_rebind(hosts: list[str]) -> None:
            listeners.request_rebind("interserver", hosts=hosts,
                                     start=make_interserver_group(hosts))
        if nic_plan is not None:
            listeners.add_threaded("interserver",
                                   start=make_interserver_group(nic_plan["hosts"]["interserver"]),
                                   stop=lambda server: server.stop(),
                                   port=args.interserver_port,
                                   hosts=nic_plan["hosts"]["interserver"])
        else:
            listeners.add_threaded("interserver", start=make_interserver_start(inter_addr),
                                   stop=lambda server: server.stop(),
                                   port=args.interserver_port,
                                   hosts=[inter_addr or inter_host])
        if inter_iface and nic_plan is None:
            watch(inter_iface, inter_addr, lambda new: listeners.request_rebind(
                "interserver", hosts=[new or inter_iface],
                start=make_interserver_start(new)))

    watched: set[str] = {console_iface} | ({inter_iface} if nic_plan is None else set())
    watched.discard(None)

    def watch_plan(p: dict) -> None:
        """‏#705: כל כרטיס שמאזין נקשר אליו נדגם — כתובת שהתחלפה = אכיפה מחדש."""
        for name in p["nics"]["console"] + p["nics"]["netboot"] + p["nics"]["interserver"]:
            if name not in watched:
                watched.add(name)
                watch(name, ifaddr_current(name),
                      lambda _new: _raise(apply_listeners(nic)))

    def apply_listeners(nic_services) -> str | None:
        """‏#705: שלב האכיפה של המאזינים — כל מאזין נקשר בדיוק לכתובות של
        הכרטיסים שבהם השירות שלו דלוק (מצטבר: כתובת שנשארת אינה נסגרת)."""
        p = nic_services.current_plan()
        hosts = p["hosts"]
        errors = []

        def run(label, call):
            try:
                call()
            except Exception as exc:                                # noqa: BLE001
                errors.append(f"{label}: {exc or type(exc).__name__}")
        run("קונסולה", lambda: listeners.request_retarget(
            "http_console", hosts=with_loopback(hosts["http_console"]),
            factory_for=console_factory))
        run("סוכן", lambda: listeners.request_retarget(
            "http_boot", hosts=hosts["http_boot"] or [deploy_net.UNCONFIGURED_HOST],
            factory_for=agent_factory))
        if "interserver" not in listeners.ids():
            if hosts["interserver"]:
                errors.append("בין-שרתים: אין מאזין 8443 בתהליך הזה — בראשי: אתחול "
                              "השרת יעלה אותו לפי השירותים; בשרת המשנה: נדרש "
                              "‎--interserver-host ביחידה")
        elif alive_servers is not None:
            run("בין-שרתים", lambda: listeners.request_retarget(
                "interserver", hosts=hosts["interserver"],
                factory_for=lambda h: alive_servers(h)[0][0]))
        elif interserver_rebind is not None:
            run("בין-שרתים", lambda: interserver_rebind(hosts["interserver"]))
        watch_plan(p)
        return " · ".join(errors) or None

    if nic is not None:
        nic.hooks["listeners"] = apply_listeners
        nic.hooks["bound"] = lambda: {pid: [h for h, _p in listeners.bound(pid)]
                                      for pid in listeners.ids()}
        deploy_ctx.services = nic.firewall_services
    if nic_plan is not None:
        watch_plan(nic_plan)
    if nic is not None and nic.reverted_at_startup and nic.hooks.get("firewall"):
        # שינוי שפג בזמן שהשרת לא רץ חזר ב-DB; ה-dnsmasq כבר נכתב מחדש בעלייה
        # (`sync_main_conf`), וחומת האש — כאן.
        error = nic.hooks["firewall"](nic)
        if error:
            print(f"per-NIC services: firewall after startup revert failed: {error}")

    # המצב השמור (‏`port:<id>` ב-DB) קובע מה נקשר בעלייה.
    listeners.want_open = lambda port_id: ports.enabled(runtime.conn, port_id)
    try:
        asyncio.run(serve_all(listeners))
    finally:
        for watcher in watchers:
            watcher.stop()


async def serve_all(listeners) -> None:
    """מריץ את כל המאזינים בתהליך אחד, עם כיבוי מתואם — ‏#996: דרך
    `ports.Listeners`, שגם סוגר ופותח אותם בזמן ריצה.

    ‏`listeners.want_open(port_id)` הוא המצב השמור ב-DB: פורט שהמפעיל כיבה אינו
    מופעל כלל בעלייה. ברגע שמאזין יצא **שלא כי ביקשנו** — Ctrl-C או
    חריגה — כל השאר מתבקשים לצאת, ואז ממתינים להם לפני החזרה. חריגה
    (כשל bind בעלייה הוא הנפוץ) מתגלגלת החוצה **אחרי** שכולם נעצרו: שרת
    ניהול שלא הצליח לתפוס את הפורט אינו 'עלה בהצלחה', ואסור שיֵרָאה כך
    בזמן שהסוכן ממשיך לבדו (עיקרון 5). נבדק ב-tests/test_port_listeners.py."""
    # מה שהמפעיל כיבה בדף הפורטים לא עולה — ונאמר, כדי שהשורה
    # "agent on … console on …" למעלה לא תיקרא כאילו הכול מאזין.
    disabled = [p for p in listeners.ids() if not listeners.want_open(p)]
    if disabled:
        print(f"ports disabled by operator (not bound): {', '.join(disabled)}")
    await listeners.serve()


if __name__ == "__main__":
    main()
