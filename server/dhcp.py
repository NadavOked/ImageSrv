"""DHCP לכל כרטיס רשת — הלוגיקה הטהורה (אפיון סעיף 24).

השרת הוא גם שרת ה-DHCP של וילן ההפצה, וההגדרה נעשית מהקונסולה. זו
ההגדרה המסוכנת ביותר במערכת: DHCP שהודלק בטעות על הממשק שמחובר לרשת
המכללה משבית רשת שלמה. לכן כל מה שכאן נבנה סביב "כבוי אלא אם":

- ברירת המחדל לכל ממשק היא כבוי.
- הדלקה דורשת את שם הממשק באישור, וממשק שסומן כ-trunk דורש אישור נוסף.
- לפני הדלקה בודקים אם מישהו אחר כבר עונה ל-DHCP על אותו ממשק.

**שני קבצים, שני תהליכי dnsmasq** ‏(#36): ‏DHCP מלא נכתב לקובץ של
האינסטנס הראשי, ומצב proxy — לקובץ נפרד שרץ ביחידת systemd משלו
(`imagectl-proxy`). ‏dnsmasq 2.91 קופא ‏(100% CPU, מפסיק לענות לכל
הסוקטים) על בקשת PXE לפורט 4011 במצב proxy, ובאינסטנס משותף הקפיאה
הזו הורגת גם את ה-DHCP של וילן ההפצה. ההפרדה אינה מתקנת את הבאג — היא
מונעת ממנו להפיל רשת שלמה, עד שתיבדק גרסת dnsmasq חדשה יותר במעבדה.
וכיוון שהבאג עדיין שם, הדלקת proxy דורשת אישור מפורש ונפרד כל עוד
הגרסה המותקנת לא נבדקה — ‏`proxy_support()` ו-`PROXY_VERIFIED`
ב-`dhcp_host.py` (שם, ולא כאן, כדי שלא יהיה עותק שני שמתיישן).

המודול הזה לא נוגע ב-DB, ב-HTTP ובמכונה: הוא מקבל הגדרות, מאמת קלט
ומחזיר טקסט dnsmasq. מה שנוגע במכונה יושב ב-`dhcp_host.py` (ומיוצא
מכאן, כדי שיישאר מקום אחד לייבא ממנו); הראוטר ב-`console_dhcp.py`.
"""

from __future__ import annotations

import ipaddress
import json
import re
from dataclasses import asdict, dataclass, field

from .dhcp_host import (  # noqa: F401  — ה-API של המודול נשאר `dhcp.<שם>`
    DEFAULT_CONF,
    INSTALLER_CONF,
    KNOWN_MACS_CONF,
    PROXY_CONF,
    PROXY_UNIT,
    ProbeResult,
    ProxySupport,
    apply,
    apply_known_macs,
    apply_proxy,
    dnsmasq_version,
    list_interfaces,
    probe_existing_dhcp,
    proxy_support,
    read_active_conf,
    read_installer_conf,
    service_active,
)

#: שורת conf פעילה שמשרתת ממשק, לעומת `except-interface=` (שוללת) או הערה.
_INTERFACE_LINE = re.compile(r"^\s*interface\s*=\s*(\S+)\s*$")
#: ‏`enable-tftp` פעיל (‏#1013) — בלי הערה, בלי ערך.
_ENABLE_TFTP_LINE = re.compile(r"^\s*enable-tftp\s*$")

SETTING_PREFIX = "dhcp:"
DEFAULT_LEASE = "12h"
#: שורש ה-TFTP שהמתקין פורס אליו (install/setup-boot-server.sh).
DEFAULT_TFTP_ROOT = "/srv/tftp"
#: קובץ חכירות משלו לאינסטנס ה-proxy — שני תהליכים על אותו קובץ מפסידים.
PROXY_LEASES = "/var/lib/imagectl/dnsmasq-proxy.leases"


@dataclass
class InterfaceConfig:
    """ההגדרה של ממשק אחד, כפי שהיא נשמרת בטבלת settings (JSON)."""

    name: str
    enabled: bool = False          # DHCP מלא — מחלק כתובות
    proxy: bool = False            # עונה על PXE בלבד, בלי לחלק כתובות
    trunk: bool = False            # מחובר לרשת המכללה — דורש אישור נוסף
    range_start: str = ""
    range_end: str = ""
    netmask: str = "255.255.255.0"
    gateway: str = ""
    dns: list[str] = field(default_factory=list)
    lease: str = DEFAULT_LEASE
    server_ip: str = ""            # כתובת השרת בוילן — next-server ו-TFTP

    @classmethod
    def from_json(cls, name: str, raw: str | None) -> "InterfaceConfig":
        if not raw:
            return cls(name=name)
        data = json.loads(raw)
        data.pop("name", None)
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(name=name, **known)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


# --- אימות ------------------------------------------------------------------


def _ip(value: str, label: str) -> ipaddress.IPv4Address:
    try:
        return ipaddress.IPv4Address(value.strip())
    except (ipaddress.AddressValueError, ValueError):
        raise ValueError(f"{label}: כתובת לא תקינה ({value!r})")


#: שם כרטיס — **רשימת-היתר**, כמו שם משתמש (‏#111), ומאותה סיבה: על
#: רשימת-איסור צריך לנחש נכון כל תו שדמון שרץ כ-root עושה בו משהו,
#: ועל רשימת-היתר צריך רק לדעת איך נראה שם כרטיס. 15 תווים הם
#: IFNAMSIZ של הקרנל — שם ארוך יותר לא יכול להתאים לכרטיס אמיתי.
_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,14}")

NAME_ERROR = ("שם כרטיס — אותיות/ספרות באנגלית ואחריהן . _ : או -, "
              "עד 15 תווים, למשל eth1.700")


def validate_name(name: str) -> None:
    """זורק ValueError בעברית על שם כרטיס שאסור לכתוב לקובץ dnsmasq.

    זה השדה היחיד בתצורה שנכתב לקובץ כטקסט חופשי, בלי מרכאות ובלי
    בריחה, ולכן שורה חדשה בתוכו מוסיפה לקובץ **הוראה שלמה** — למשל
    ‏`dhcp-range` שני על ממשק שאיש לא הגדיר — ורווח או `#` מפילים את
    dnsmasq בעלייה, כלומר את כל וילן ההפצה (‏#102).

    השם נאמר בחזרה למפעיל כ-`repr`, כדי שתו בלתי-נראה ייראה בהודעה.
    שם פסול אינו מנוקה בשקט: `eth 0` שהופך ל-`eth0` הוא ניחוש איזה
    כרטיס התכוונו אליו.
    """
    if not isinstance(name, str) or not _NAME_RE.fullmatch(name):
        shown = name if isinstance(name, str) else str(name)
        raise ValueError(f"{NAME_ERROR} — התקבל {shown[:40]!r}")


def validate(cfg: InterfaceConfig) -> None:
    """זורק ValueError בעברית על הגדרה שלא תעבוד.

    שדה שנכתב לקובץ נבדק **לפני** היציאה המוקדמת על ממשק כבוי: ממשק
    ב-proxy הוא `proxy and not enabled`, כלומר הוא נכנס ל-`render_proxy`
    בדיוק דרך הענף שדילג על הבדיקות כולן (‏#102). מה שכן נשאר מאחורי
    היציאה הוא רק מה ש-`render` לבדו כותב — טווח, מסכה, שער, חכירה —
    כי לממשק שאינו מחלק כתובות אין להם משמעות.
    """
    validate_name(cfg.name)
    if cfg.server_ip:
        # ‏`render_proxy` כותב אותה כ-`dhcp-range=set:...,<server_ip>,proxy`.
        _ip(cfg.server_ip, "כתובת השרת")
    if not cfg.enabled:
        return
    start = _ip(cfg.range_start, "תחילת הטווח")
    end = _ip(cfg.range_end, "סוף הטווח")
    mask = _ip(cfg.netmask, "מסכת רשת")
    server = _ip(cfg.server_ip, "כתובת השרת")
    if start > end:
        raise ValueError("תחילת הטווח גדולה מסופו")
    network = ipaddress.IPv4Network(f"{start}/{mask}", strict=False)
    if end not in network:
        raise ValueError("סוף הטווח מחוץ לרשת שהמסכה מגדירה")
    if server not in network:
        raise ValueError("כתובת השרת אינה ברשת של הטווח")
    if start <= server <= end:
        raise ValueError("כתובת השרת נמצאת בתוך הטווח שמחולק")
    if cfg.gateway:
        if _ip(cfg.gateway, "שער") not in network:
            raise ValueError("השער אינו ברשת של הטווח")
    for entry in cfg.dns:
        _ip(entry, "DNS")
    lease = cfg.lease.strip().lower()
    if not lease or lease[-1] not in "mhd" or not lease[:-1].isdigit():
        raise ValueError("זמן חכירה: מספר ואחריו m/h/d, למשל 12h")


# --- אמת חיה ל-DHCP (‏#762) --------------------------------------------------


def installer_serves_tftp(text: str) -> bool:
    """האם קובץ המתקין (‏INSTALLER_CONF) עדיין נושא `enable-tftp` פעיל (‏#1013).

    פרסר טהור, כמו `parse_served_interfaces`: שורה פעילה בלבד — הערה
    (`# enable-tftp`) אינה נספרת. ‏True = המתג מהקונסולה **אינו יכול
    לכבות** את TFTP, ו-`/ports` חייב לומר זאת ולא להציג "כבוי" מזויף.
    """
    return any(_ENABLE_TFTP_LINE.match(raw.split("#", 1)[0])
               for raw in (text or "").splitlines())


def parse_served_interfaces(text: str) -> set[str]:
    """אילו ממשקים קובץ ה-conf **הפעיל** מצהיר עליהם `interface=`.

    פרסר טהור: מקבל טקסט, מחזיר קבוצת שמות. קורא רק שורות `interface=`
    פעילות — מתעלם מהערות (`#`), שורות ריקות ומ-`except-interface=` (זו
    שלילה, לא הגשה). אין להסיק מ-`dhcp-range=` בלבד: טווח בלי `interface`
    אינו מגיש דבר על ממשק ספציפי.
    """
    served: set[str] = set()
    for raw_line in (text or "").splitlines():
        line = raw_line.split("#", 1)[0]
        match = _INTERFACE_LINE.match(line)
        if match:
            served.add(match.group(1))
    return served


# --- רינדור dnsmasq ---------------------------------------------------------


def full_dhcp(configs: list[InterfaceConfig]) -> list[InterfaceConfig]:
    """הממשקים שמחלקים כתובות — האינסטנס הראשי."""
    return [c for c in configs if c.enabled]


def proxy_only(configs: list[InterfaceConfig]) -> list[InterfaceConfig]:
    """הממשקים שעונים על PXE בלבד — האינסטנס הנפרד. ‏enabled גובר, כדי
    ששום ממשק לא יופיע בשני הקבצים גם אם ה-DB מכיל את שניהם."""
    return [c for c in configs if c.proxy and not c.enabled]


#: אופציה 93: הקושחה מצהירה מי היא, והטוען נבחר לפי זה — ‏UEFI מקבל את
#: ה-shim החתום, ‏Legacy BIOS ‏(מחשבי השיכפול, ‏#38) מקבל GRUB ‏i386-pc.
#: שניהם קוראים את אותו grub.cfg — מחולל תפריט אחד. נדרש בשני הקבצים.
_ARCH_MATCH = [
    "dhcp-match=set:bios,option:client-arch,0",
    "dhcp-match=set:efi-x86_64,option:client-arch,7",
    "dhcp-match=set:efi-x86_64,option:client-arch,9",
    "",
]

#: ‏#2040 (הכרעת נדב 23/10): ה-vendor class (אופציה 60) שה-udhcpc של הסוכן
#: שולח (‏`-V` ב-`agent/init`). בכרטיס ההפצה רק לקוח עם התג הזה מקבל כתובת:
#: קושחת PXE שולחת `PXEClient:Arch:...` (‏BIOS ו-UEFI, גם בבקשת החידוש),
#: והסוכן שולח את זה. ‏Windows שעלה מהדיסק שולח `MSFT 5.0` ולא נענה —
#: הכרטיס שלו ברשת ההפצה נשאר בלי כתובת. ‏dnsmasq משווה כתת-מחרוזת.
AGENT_VENDOR_CLASS = "ImageSrv-agent"
NETBOOT_TAG = "imagesrv-boot"
_NETBOOT_MATCH = [
    "# #2040: on the deploy NIC only network boot and the agent get an address.",
    f"dhcp-vendorclass=set:{NETBOOT_TAG},PXEClient",
    f"dhcp-vendorclass=set:{NETBOOT_TAG},{AGENT_VENDOR_CLASS}",
    "",
]


def _guard_names(configs: list[InterfaceConfig]) -> None:
    """השער האחרון לפני הקובץ שדמון root קורא.

    ‏`validate` חוסמת בכניסה, וזו אותה בדיקה מאותו מקום — לא כלל שני —
    על מה שנשלף מה-DB. רשומה שנכתבה לפני #102 לא תהפוך פה לקובץ שבור
    בשקט: הרינדור נכשל, והקורא (‏`apply_all`) מדווח למפעיל וליומן.
    """
    for cfg in configs:
        validate_name(cfg.name)


def _head(what: str) -> list[str]:
    return [f"# ImageCtl -- {what}, generated from the console (spec 24).",
            "# Do not edit by hand: the next change in the console rewrites it.",
            ""]


def _tftp_lines(tftp_root: str | None) -> list[str]:
    """‏#1013: המתג. ‏None = כבוי — וגם אז נכתבת שורה, כדי שמי שקורא את
    הקובץ על השרת ידע שזה כיבוי מכוון ולא שורה שנשמטה."""
    if tftp_root:
        return ["# TFTP (#1013): the switch is the ports page in the console.",
                "enable-tftp", f"tftp-root={tftp_root}", ""]
    return ["# TFTP off (#1013): switched off in the console (ports page) -- no PXE.", ""]


def _pinned(lines: list[str], tftp_root: str | None, pin_tftp: bool) -> str:
    """‏#1078-S / R20-F5: dnsmasq בלי אף `interface=` מאזין על **כל**
    הכרטיסים (חוץ מ-`except-interface`). כש-`pin_tftp` (אין קובץ אחר
    שנושא את כרטיס ההפצה) וה-TFTP דלוק בלי שם — loopback בלבד."""
    if pin_tftp and tftp_root and not any(l.startswith("interface=") for l in lines):
        if "bind-interfaces" not in lines:
            lines.append("bind-interfaces")
        lines += ["# No interface is named (#1078): TFTP on loopback only, not every NIC.",
                  "interface=lo", ""]
    return "\n".join(lines) + "\n"


def _netboot_gate(netboot, nics, full, deploy_interface):
    """‏#705: "אתחול מהרשת" כבוי בכרטיס = dnsmasq אינו משרת אותו כלל.

    מחזיר (שורות, ה-DHCP המלא שנשאר, כרטיס ההפצה שנשאר). כרטיס כבוי מקבל
    ‏`except-interface=` — הוא גובר גם על `interface=` בקובץ המתקין, ולכן
    גם הפצה מ-`cli` נסגרת באמת. כרטיס דלוק בלי DHCP מוגדר מקבל
    ‏`interface=` (‏TFTP בלבד; טווח כתובות עדיין רק מלשונית ה-DHCP, ‏#53).
    ותמיד יש לפחות `interface=` אחד — בלעדיו dnsmasq מאזין על **כל** מי
    שלא הוחרג (R20-F5)."""
    allowed = set(netboot)
    named = {c.name for c in full} | ({deploy_interface} if deploy_interface else set())
    shut = sorted((set(nics) | named) - allowed)
    for name in shut:
        validate_name(name)
    full = [c for c in full if c.name in allowed]
    if deploy_interface not in allowed:
        deploy_interface = None
    served = {c.name for c in full} | ({deploy_interface} if deploy_interface else set())
    extra = sorted(allowed - served)
    for name in extra:
        validate_name(name)
    lines = ["# Per-NIC services (#705): network boot is set per interface in the console.",
             "bind-interfaces"]
    lines += [f"except-interface={n}" for n in shut]
    lines += [f"interface={n}" for n in extra]
    if not served and not extra:
        lines += ["# No interface offers network boot -- loopback only, not every NIC.",
                  "interface=lo"]
    return lines + [""], full, deploy_interface


def render(configs: list[InterfaceConfig], tftp_root: str | None = None,
           deploy_interface: str | None = None, pin_tftp: bool = False,
           netboot: list[str] | None = None, nics: list[str] = (),
           unregistered_if: str | None = None) -> str:
    """הקובץ של האינסטנס הראשי — ‏DEFAULT_CONF, בתוך /etc/dnsmasq.d.

    dnsmasq קורא את כל /etc/dnsmasq.d; קובץ ההתקנה (imagectl.conf) נותן את
    הבסיס (port=0, dhcp-hostsfile, כרטיס ההפצה) והקובץ הזה מוסיף ממשקים
    וטווחים — **ואת ה-TFTP** (‏#1013): ‏`tftp_root` הוא המתג. מחרוזת =
    ‏`enable-tftp` + `tftp-root=`, ו-`None` = TFTP כבוי (המפעיל כיבה 69 בדף
    הפורטים). השורות נכתבות **לפני** היציאה המוקדמת כשאין אף DHCP — אחרת
    התקנה טרייה (עם כרטיס הפצה, בלי DHCP עדיין) הייתה נשארת בלי TFTP.
    עד v0.47.5 המתקין כתב את שתי השורות ב-imagectl.conf, ומשם אי-אפשר
    היה לכבות אותן מקובץ אחר (dnsmasq מצטבר) — לכן הן עברו לכאן.

    ממשק במצב proxy לא מופיע כאן בשום צורה (‏#36) — גם לא כ-`interface`
    להגשת TFTP: הוא רץ בתהליך אחר, ושני תהליכים לא יכולים לתפוס את
    אותם פורטים על אותו כרטיס. `except-interface` מוציא אותו מפורשות,
    והוא גובר על כל השאר גם אם מישהו יוסיף `interface` בעתיד.

    ‏`deploy_interface` (‏#1088): כרטיס ההפצה שהוגדר **מהקונסולה** — אז
    ‏`imagectl.conf` של המתקין אינו נושא `interface=`, והשורה שמצמידה את
    ה-TFTP לכרטיס ההפצה בלבד (R20-F1) נכתבת כאן, גם כשאף DHCP אינו דלוק.

    ‏`pin_tftp` (‏#1078): הקובץ הזה הוא היחיד שיכול לשאת `interface=` (מקור
    ההפצה אינו `cli`) — ואם אין בו אף שם, ה-TFTP נצמד ל-loopback (`_pinned`).

    ‏`netboot` (‏#705): הכרטיסים שבהם "אתחול מהרשת" דלוק, כשהמפעיל ערך
    שירותים לכרטיסים (`None` = ההתנהגות של היום, והפלט זהה בית-בבית);
    ‏`nics` — כל הכרטיסים שהשרת רואה, כדי שהכבויים יוחרגו בשם.

    ‏`unregistered_if` (‏#1802): כרטיס ההפצה, מכל מקור (cli או קונסולה) — עליו
    בלבד `dhcp-boot` אינו דורש `tag:known`, כדי שמכונה לא רשומה תגיע לסוכן
    ותראה "המחשב הזה לא רשום" (פעם אחת — `server/unknown_screen.py`). כל
    כרטיס אחר, ו-`None`, נשארים כמו ב-#141. ‏#2040: על אותו כרטיס בלבד
    ‏dnsmasq עונה רק לאתחול רשת (‏`PXEClient`) ולסוכן (‏`AGENT_VENDOR_CLASS`)
    — ‏Windows שעלה מהדיסק במחשב בנייה אינו מקבל כתובת ברשת ההפצה.
    """
    lines = _head("DHCP per interface")
    full, proxied = full_dhcp(configs), proxy_only(configs)
    _guard_names(full + proxied)
    if netboot is not None:
        gate, full, deploy_interface = _netboot_gate(netboot, nics, full, deploy_interface)
        lines += gate
    if deploy_interface and deploy_interface not in {c.name for c in full + proxied}:
        validate_name(deploy_interface)
        lines += ["# Deploy interface, set from the console (#1088): TFTP on it only.",
                  *([] if "bind-interfaces" in lines else ["bind-interfaces"]),
                  f"interface={deploy_interface}", ""]
    lines += _tftp_lines(tftp_root)
    if not full and not proxied:
        lines.append("# No interface has DHCP or proxy enabled.")
        return _pinned(lines, tftp_root, pin_tftp)
    if "bind-interfaces" not in lines:
        lines += ["bind-interfaces", ""]
    if proxied:
        lines.append("# Proxy interfaces belong to the imagectl-proxy instance (#36):")
        lines += [f"except-interface={c.name}" for c in proxied] + [""]
    if not full:
        lines.append("# No interface hands out addresses -- TFTP only.")
        return _pinned(lines, tftp_root, pin_tftp)
    lines += _ARCH_MATCH
    if unregistered_if in {c.name for c in full}:
        lines += _NETBOOT_MATCH
    for cfg in full:
        tag = f"if-{cfg.name}"
        lines.append(f"# --- {cfg.name} ---")
        lines.append(f"interface={cfg.name}")
        lines.append(
            f"dhcp-range=set:{tag},{cfg.range_start},{cfg.range_end},"
            f"{cfg.netmask},{cfg.lease}"
        )
        if cfg.name == unregistered_if:
            # ‏#2040: כרטיס ההפצה בלבד. כרטיס אחר (וילן כיתה שהשרת מחלק בו
            # כתובות) נשאר כמו שהוא — שם Windows כן צריך כתובת.
            # התג הוא **שם הכרטיס**, ש-dnsmasq מדליק בעצמו על כל בקשה, ולא
            # `if-<שם>`: תג של dhcp-range עוד לא קיים כש-dhcp-ignore נבדק.
            # נמדד על dnsmasq 2.91 ב-netns (23/10): עם `tag:if-<שם>` ‏`MSFT 5.0`
            # קיבל DHCPOFFER; עם שם הכרטיס — ignored.
            lines.append(f"dhcp-ignore=tag:{cfg.name},tag:!{NETBOOT_TAG}")
        if cfg.gateway:
            lines.append(f"dhcp-option=tag:{tag},option:router,{cfg.gateway}")
        if cfg.dns:
            lines.append(f"dhcp-option=tag:{tag},option:dns-server,{','.join(cfg.dns)}")
        lines.append(f"dhcp-option=tag:{tag},option:tftp-server,{cfg.server_ip}")
        # ‏#1802 (הכרעת נדב 19/10): בכרטיס ההפצה בלבד גם מכונה לא רשומה
        # מקבלת את קובץ האתחול — התפריט שלה נקבע בשרת (מסך "לא רשום" פעם
        # אחת, ואז דיסק). בכל כרטיס אחר ההערה שלמטה בתוקף כפי שהיא.
        known = "" if cfg.name == unregistered_if else "tag:known,"
        # ‏#141: `tag:known` נוסף לשתי שורות ה-boot — מכונה שאינה ברשימת
        # ה-MAC הרשומים (KNOWN_MACS_CONF, נטענת דרך dhcp-hostsfile
        # ב-imagectl.conf) לא מקבלת את התג, ולכן dnsmasq אינו עונה לה
        # עם dhcp-boot בכלל. הקושחה נכשלת ב-PXE ונופלת לדיסק המקומי —
        # לפני שהיא בכלל שולחת בקשת TFTP, ולא רק בתוך grub.cfg
        # (‏decide() ב-boot/grub_menu.py, שנשאר כרשת ביטחון שנייה: תחנה
        # שמגיעה בכל זאת, למשל מרשת זרה שמפעילה proxy, עדיין נופלת שם
        # ל-unregistered).
        lines.append(
            f"dhcp-boot=tag:{tag},tag:bios,{known}grub/i386-pc/core.0,,{cfg.server_ip}"
        )
        lines.append(
            f"dhcp-boot=tag:{tag},tag:efi-x86_64,{known}bootx64.efi,,{cfg.server_ip}"
        )
        lines.append("")
    return "\n".join(lines) + "\n"


def render_proxy(configs: list[InterfaceConfig],
                 tftp_root: str | None = DEFAULT_TFTP_ROOT) -> str:
    """הקובץ של אינסטנס ה-proxy — ‏PROXY_CONF, מחוץ ל-/etc/dnsmasq.d.
    ‏`tftp_root=None` = המתג של 69 כבוי (‏#1013) — גם כאן, אחרת האינסטנס
    הזה היה ממשיך להאזין על 69 בכרטיסים שלו והמתג היה "כבוי אבל מאזין".

    מצב proxy הוא לרשת שיש בה DHCP קיים ואסור להתנגש בו: עונים על
    שאלות PXE בלבד, כתובות ממשיכות להגיע מהשרת הקיים. כאן זה גם התהליך
    שמותר לו למות: ‏dnsmasq 2.91 קופא על בקשת PXE:4011 ‏(#36).

    הקובץ עומד בפני עצמו — היחידה מריצה אותו עם `--conf-file`, בלי
    `/etc/dnsmasq.conf` ובלי `/etc/dnsmasq.d` — ולכן הוא מביא את הכל
    בעצמו: בלי DNS ‏(`port=0`), ‏TFTP משלו על הכרטיסים שלו בלבד, וקובץ
    חכירות נפרד. `bind-interfaces` בשני הקבצים הוא מה שמאפשר לשני
    התהליכים לחיות על אותה מכונה.
    """
    lines = _head("PXE proxy instance")
    active = proxy_only(configs)
    _guard_names(active)
    if not active:
        lines.append("# No interface is in proxy mode -- the unit stays stopped.")
        return "\n".join(lines) + "\n"
    lines += [
        "port=0",                                     # בלי DNS, בלי שאילתות
        "bind-interfaces",
        f"dhcp-leasefile={PROXY_LEASES}",
        "",
    ]
    lines += _tftp_lines(tftp_root)
    lines += _ARCH_MATCH
    for cfg in active:
        tag = f"if-{cfg.name}"
        lines.append(f"# --- {cfg.name} ---")
        lines.append(f"interface={cfg.name}")
        # ל-pxe-service לא נמסרת כאן כתובת שרת (השדה האחרון, שהוא רשות):
        # כשיש רק שם קובץ, dnsmasq מגיש אותו מה-TFTP שלו עצמו ושם ב-siaddr
        # את כתובתו שלו על הממשק שדרכו הגיעה הבקשה. זו בדיוק הכתובת
        # שהתחנה יכולה להגיע אליה מהרשת שלה — וממנה GRUB ממלא את
        # net_default_server, שהוא הכתובת הראשית ב-grub.cfg (‎#37).
        # כתובת מפורשת כאן הייתה מקבעת את וילן ההפצה ושוברת בדיוק את זה.
        # שם הקובץ נכתב עם הסיומת בכוונה: ל-basename בלי סיומת dnsmasq
        # מוסיף את מספר השכבה (".0"), ולשם מלא — לא.
        subnet = cfg.server_ip or "0.0.0.0"
        lines.append(f"dhcp-range=set:{tag},{subnet},proxy")
        lines.append(
            f'pxe-service=tag:{tag},tag:bios,x86PC,"ImageSrv",grub/i386-pc/core.0'
        )
        lines.append(
            f'pxe-service=tag:{tag},tag:efi-x86_64,x86-64_EFI,"ImageSrv",bootx64.efi'
        )
        lines.append("")
    return "\n".join(lines) + "\n"


# --- מכונות רשומות — dhcp-hostsfile (#141) -----------------------------------
#
# מכונה שאינה ברשימה הזו לא מגיעה לגרסת GRUB בכלל — לא רק שהיא אינה
# מקבלת ערך ImageCtl בקובץ (זה כבר קיים: decide() ב-boot/grub_menu.py
# מחזיר Decision(LOCAL, "unregistered")). ‏dhcp-boot בקובץ הראשי (render()
# למעלה) דורש tag:known, וכאן זה מודלק — לכל MAC רשום, ורק לו. הקובץ הזה
# נגזר מטבלת המכונות ולא מקור: הוא נכתב מחדש בכל שינוי ובכל עליית שרת
# (dhcp_host.apply_known_macs, נקרא מ-console_api ומ-main.py). קובץ שנמחק
# או לא נכתב מעולם משאיר **כל** מכונה בלי dhcp-boot, לא רק את זו
# שהשתנתה — עיקרון 5, ההרחבה על "נגזרת ולא מקור" ב-#141.

#: MAC קנוני — אותה תבנית בדיוק כמו הקנוני של registry.py. השורה הזו
#: נכתבת לקובץ ש-dnsmasq קורא כ-root, בלי בריחה ובלי מרכאות (בדיוק כמו
#: שם הכרטיס, #102) — לכן היא מאומתת כאן שוב ולא רק בכניסה ל-DB.
_MAC_HOSTLINE = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")


def _guard_macs(macs: list[str]) -> None:
    """השער האחרון לפני קובץ ה-host שדמון root קורא — אותה רוח כמו
    ‏_guard_names (#102): רשומה שאינה MAC קנוני לא הופכת לשורת dnsmasq
    שבורה בשקט. ‏render_known_macs נכשלת במקום לכתוב אותה."""
    for mac in macs:
        if not isinstance(mac, str) or not _MAC_HOSTLINE.match(mac):
            raise ValueError(f"MAC לא קנוני בטבלת המכונות: {mac!r}")


def render_known_macs(macs: list[str]) -> str:
    """הקובץ של KNOWN_MACS_CONF — שורה אחת לכל מכונה רשומה.

    כל שורה מדליקה את tag:known בדיוק כמו ``dhcp-host=<mac>,set:known``
    בקובץ הראשי (זה בדיוק התחביר ש-dhcp-hostsfile מצפה לו לשורה, בלי
    התחילית). ‏macs ריק מייצר קובץ תקין וריק — dnsmasq טוען אותו, ואף
    tag:known לא נדלק, כלומר אף מכונה לא מקבלת dhcp-boot (בטוח: זו בדיוק
    ברירת המחדל של "מצב לא ברור" — עיקרון 1).
    """
    _guard_macs(macs)
    lines = [
        "# ImageCtl -- registered MAC addresses, generated from the console (#141).",
        "# Do not edit by hand: the next machine add/edit/delete rewrites it.",
        "",
    ]
    lines += [f"{mac},set:known" for mac in macs]
    return "\n".join(lines) + "\n"
