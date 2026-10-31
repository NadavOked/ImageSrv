"""הגדרות הרשת של השרת עצמו — הלוגיקה הטהורה (אפיון סעיף 24, ‏#55/#57).

לשונית הרשת טיפלה עד היום בצד אחד בלבד: **מה השרת מחלק** (‏`dhcp.py`).
כאן הצד השני — **איך השרת עצמו מחובר**: כתובת, מסכה, שער, ‏DNS ונתיבים
סטטיים. הצורך התעורר מ-#50: נתיב סטטי שנוסף ב-`ip route add` נעלם
באתחול הבא, ואין דרך להגדיר אותו מהקונסולה.

המודול הזה לא נוגע ב-DB, ב-HTTP ובמכונה: הוא מקבל הגדרות, מאמת קלט,
ומחזיר טקסט `interfaces.d`. מה שנוגע במכונה יושב ב-`netcfg_host.py`,
ההחזרה האוטומטית ב-`netcfg_rollback.py`, והראוטר ב-`console_netcfg.py`.
זו בדיוק ההפרדה של `dhcp.py`/`dhcp_host.py`, ומאותה סיבה: היא מה
שמאפשר לבדיקות להזריק hooks ולעולם לא לגעת ברשת של מכונת הבדיקות.

**כל הבעיות חוזרות יחד, לא בזו אחר זו.** ‏`problems()` מחזיר *רשימה*:
כתובת שמתנגשת בכרטיס אחר וגם טווח DHCP שיצא מהרשת החדשה הם שתי סיבות
לסירוב אחד. מפעיל שמתקן אחת ומגלה את השנייה רק בניסיון הבא לומד שהמסך
לא באמת יודע מה הוא רוצה — ובמסך שמשנה כתובות זה בדיוק הרגע שבו הוא
מנחש ומנתק את עצמו.
"""

from __future__ import annotations

import ipaddress
import json
from dataclasses import asdict, dataclass, field

from server.net import dhcp

SETTING_PREFIX = "netcfg:"

#: שלושת המצבים של כרטיס, ו-`manual` הוא ברירת המחדל: ImageSrv **אינו**
#: מנהל את הכרטיס, ואין לו קובץ ב-`interfaces.d`. שרת קיים ממשיך לעבוד
#: בדיוק כפי שהוא עד שמישהו בוחר לנהל כרטיס מהקונסולה.
MODE_MANUAL = "manual"
MODE_STATIC = "static"
MODE_DHCP = "dhcp"
MODES = (MODE_MANUAL, MODE_STATIC, MODE_DHCP)

MODES_HE = {
    MODE_MANUAL: "לא מנוהל מהקונסולה",
    MODE_STATIC: "כתובת סטטית",
    MODE_DHCP: "לקוח DHCP",
}

#: ‏#1264: רשת הפצה על שני כרטיסים — bond אחד, כתובת אחת (ה-‎.1 של הרשת).
#: ‏balance-alb (‏mode 6): שני הכרטיסים פעילים יחד, failover שקוף, ו**בלי**
#: הגדרה בסוויץ' (‏LACP היה דורש אותה). השם קבוע: יש לכל היותר רשת הפצה אחת.
BOND_NAME = "bond0"
BOND_MODE = "balance-alb"
#: כל כמה מילישניות הקרנל בודק את הקישור של כל כרטיס — ובלי זה כבל שנשלף
#: אינו מזוהה כלל, והתעבורה ממשיכה להישלח לכרטיס מת.
BOND_MIIMON_MS = 100


@dataclass(frozen=True)
class StaticRoute:
    """נתיב סטטי אחד: יעד/מסכה, ודרך מי מגיעים אליו."""

    destination: str = ""
    netmask: str = "255.255.255.0"
    gateway: str = ""

    @property
    def cidr(self) -> str:
        return f"{self.destination}/{_prefix_len(self.netmask)}"

    @classmethod
    def from_any(cls, raw: object) -> "StaticRoute":
        data = raw if isinstance(raw, dict) else {}
        return cls(
            destination=str(data.get("destination", "")).strip(),
            netmask=str(data.get("netmask") or "255.255.255.0").strip(),
            gateway=str(data.get("gateway", "")).strip(),
        )


@dataclass
class NetConfig:
    """ההגדרה של כרטיס אחד, כפי שהיא נשמרת בטבלת settings (JSON).

    ‏`dns` ו-`routes` נשמרים לכרטיס, אבל ‏DNS מגיע בסוף ל-`/etc/resolv.conf`
    אחד ומשותף — ראו `render_resolv`.
    """

    name: str
    mode: str = MODE_MANUAL
    address: str = ""
    netmask: str = "255.255.255.0"
    gateway: str = ""
    dns: list[str] = field(default_factory=list)
    routes: list[StaticRoute] = field(default_factory=list)
    #: ‏#1264: חברי ה-bond, הראשי ראשון. ריק = כרטיס רגיל (וכך כל הגדרה
    #: שנשמרה לפני #1264). לא ריק = `name` הוא ה-bond.
    slaves: list[str] = field(default_factory=list)

    @classmethod
    def from_json(cls, name: str, raw: str | None) -> "NetConfig":
        if not raw:
            return cls(name=name)
        data = json.loads(raw)
        data.pop("name", None)
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        known["routes"] = [StaticRoute.from_any(r) for r in known.get("routes") or []]
        known["dns"] = [str(d) for d in known.get("dns") or []]
        known["slaves"] = [str(s) for s in known.get("slaves") or []]
        return cls(name=name, **known)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @property
    def managed(self) -> bool:
        """האם ImageSrv כותב קובץ לכרטיס הזה בכלל."""
        return self.mode in (MODE_STATIC, MODE_DHCP)

    @property
    def cidr(self) -> str:
        return f"{self.address}/{_prefix_len(self.netmask)}"

    def network(self) -> ipaddress.IPv4Network | None:
        """הרשת שהכרטיס יושב בה, או None אם אין כתובת/מסכה תקינות."""
        if self.mode != MODE_STATIC:
            return None
        try:
            return ipaddress.IPv4Network(f"{self.address}/{self.netmask}",
                                         strict=False)
        except (ipaddress.AddressValueError, ipaddress.NetmaskValueError,
                ValueError):
            return None


# --- אימות -------------------------------------------------------------------


def _prefix_len(netmask: str) -> int:
    try:
        return ipaddress.IPv4Network(f"0.0.0.0/{netmask.strip()}").prefixlen
    except (ipaddress.NetmaskValueError, ValueError):
        return 0


def _valid_ip(value: str) -> bool:
    try:
        ipaddress.IPv4Address(value.strip())
    except (ipaddress.AddressValueError, ValueError):
        return False
    return True


def _valid_mask(value: str) -> bool:
    """מסכה חייבת להיות רצף אחדות. ‏255.0.255.0 נראית כמו מסכה ואינה."""
    try:
        ipaddress.IPv4Network(f"0.0.0.0/{value.strip()}")
    except (ipaddress.NetmaskValueError, ValueError):
        return False
    return True


def deploy_address(network: str, netmask: str) -> tuple[str, list[str]]:
    """כתובת השרת ברשת ההפצה — **נגזרת**, לא נבחרת (‏#1263).

    המפעיל נותן רשת ומסכה, והשרת מקבל את הכתובת הראשונה בה:
    ‏10.0.20.0/255.255.255.0 → ‏10.0.20.1. מחזיר ‏(כתובת, בעיות); כתובת
    ריקה כשיש בעיות.

    כתובת שאינה כתובת הרשת עצמה (‏10.0.20.5/24) **נדחית ואינה מעוגלת**:
    עיגול שקט ל-10.0.20.0 הוא ניחוש לאיזו רשת המפעיל התכוון. ‏/31 ו-/32
    נדחות — אין בהן מקום לשרת ולטווח.
    """
    network, netmask = (network or "").strip(), (netmask or "").strip()
    found = []
    if not _valid_ip(network):
        found.append(f"כתובת הרשת אינה תקינה ({network or 'ריק'})")
    if not _valid_mask(netmask):
        found.append(f"מסכת רשת אינה תקינה ({netmask or 'ריק'})")
    if found:
        return "", found
    net = ipaddress.IPv4Network(f"{network}/{netmask}", strict=False)
    if str(net.network_address) != network:
        return "", [f"{network} אינה כתובת רשת — עם המסכה {netmask} הרשת "
                    f"היא {net.network_address}"]
    if net.num_addresses < 4:
        return "", [f"הרשת {net} קטנה מדי — אין בה מקום לשרת ולטווח כתובות"]
    return str(net.network_address + 1), []


def reachable(address: str, configs: list[NetConfig]) -> bool:
    """האם הכתובת יושבת ברשת של כרטיס סטטי כלשהו.

    זו הבדיקה שמונעת שער או נתיב שאיש לא יכול להגיע אליהם — ולכן היא
    נעשית מול **כל** הכרטיסים ולא רק מול זה שנערך: השער של `eth1`
    יכול להיות לגיטימי דרך `eth0`.
    """
    try:
        target = ipaddress.IPv4Address(address.strip())
    except (ipaddress.AddressValueError, ValueError):
        return False
    return any(net is not None and target in net
               for net in (c.network() for c in configs))


def problems(cfg: NetConfig, others: list[NetConfig],
             dhcp_cfg: dhcp.InterfaceConfig | None = None) -> list[str]:
    """**כל** מה שלא יעבוד בהגדרה, בעברית. רשימה ריקה = מותר לכתוב.

    ‏`others` הן ההגדרות של שאר הכרטיסים כפי שהן יהיו *אחרי* השינוי;
    ‏`dhcp_cfg` היא הגדרת ה-DHCP של אותו כרטיס, אם יש. שתי משפחות
    הבדיקות רצות יחד ומחזירות רשימה אחת (ראו ראש הקובץ).
    """
    found: list[str] = []
    if cfg.mode not in MODES:
        return [f"מצב לא מוכר: {cfg.mode!r}"]
    if cfg.mode == MODE_STATIC:
        found += _static_problems(cfg, others)
    if cfg.mode != MODE_STATIC and (cfg.gateway or cfg.routes):
        found.append("שער ונתיבים סטטיים דורשים כתובת סטטית")
    found += [f"‏DNS: כתובת לא תקינה ({d})" for d in cfg.dns if not _valid_ip(d)]
    found += _route_problems(cfg, others)
    found += _dhcp_problems(cfg, dhcp_cfg)
    found += _bond_problems(cfg)
    return found


def _bond_problems(cfg: NetConfig) -> list[str]:
    """‏#1264: bond הוא בדיוק שני כרטיסים שונים, עם כתובת סטטית."""
    if not cfg.slaves:
        return []
    found = []
    if cfg.mode != MODE_STATIC:
        found.append(f"{cfg.name} מאחד שני כרטיסים וחייב כתובת סטטית")
    if len(cfg.slaves) != 2 or len(set(cfg.slaves)) != 2 or cfg.name in cfg.slaves:
        found.append(f"{cfg.name} מאחד בדיוק שני כרטיסים שונים "
                     f"(התקבל: {', '.join(cfg.slaves)})")
    for slave in cfg.slaves:
        try:
            dhcp.validate_name(slave)
        except ValueError as exc:
            found.append(str(exc))
    return found


def _static_problems(cfg: NetConfig, others: list[NetConfig]) -> list[str]:
    found = []
    if not _valid_ip(cfg.address):
        found.append(f"כתובת הכרטיס אינה תקינה ({cfg.address or 'ריק'})")
    if not _valid_mask(cfg.netmask):
        found.append(f"מסכת רשת אינה תקינה ({cfg.netmask or 'ריק'})")
    network = cfg.network()
    if network is not None:
        if network.prefixlen < 32 and ipaddress.IPv4Address(cfg.address) in (
                network.network_address, network.broadcast_address):
            found.append("כתובת הכרטיס היא כתובת הרשת או הברודקאסט שלה")
        for other in others:
            found += _clash(cfg, network, other)
    if cfg.gateway:
        if not _valid_ip(cfg.gateway):
            found.append(f"השער אינו כתובת תקינה ({cfg.gateway})")
        elif network is not None and ipaddress.IPv4Address(cfg.gateway) not in network:
            found.append(f"השער {cfg.gateway} אינו ברשת של {cfg.name}")
    return found


def _clash(cfg: NetConfig, network: ipaddress.IPv4Network,
           other: NetConfig) -> list[str]:
    if other.name == cfg.name:
        return []
    other_net = other.network()
    if other_net is None:
        return []
    if other.address.strip() == cfg.address.strip():
        return [f"הכתובת {cfg.address} כבר מוגדרת על {other.name}"]
    if network.overlaps(other_net):
        return [f"הרשת {network} חופפת לרשת של {other.name} ({other_net}) — "
                "שני כרטיסים באותה רשת שולחים לפי טבלת ניתוב ולא לפי כוונה"]
    return []


def _route_problems(cfg: NetConfig, others: list[NetConfig]) -> list[str]:
    """נתיבים סטטיים (‏#57). ‏`others` כבר כולל את `cfg` עצמו כשהוא נקרא
    מ-`problems`, ולכן שער שמגיע דרך הכרטיס הנערך נחשב בר-השגה."""
    found, seen = [], set()
    everyone = others if any(o.name == cfg.name for o in others) else [*others, cfg]
    for route in cfg.routes:
        label = f"נתיב {route.destination or 'ללא יעד'}"
        if not _valid_mask(route.netmask):
            found.append(f"{label}: מסכת רשת אינה תקינה ({route.netmask})")
            continue
        if not _valid_ip(route.destination):
            found.append(f"{label}: היעד אינו כתובת תקינה")
            continue
        network = ipaddress.IPv4Network(f"{route.destination}/{route.netmask}",
                                        strict=False)
        if str(network.network_address) != route.destination.strip():
            found.append(f"{label}: היעד אינו רשת — עם המסכה {route.netmask} "
                         f"הרשת היא {network.network_address}")
        if not _valid_ip(route.gateway):
            found.append(f"{label}: השער אינו כתובת תקינה ({route.gateway})")
        elif not reachable(route.gateway, everyone):
            found.append(f"{label}: השער {route.gateway} אינו ברשת של אף "
                         "כרטיס — אי אפשר להגיע אליו")
        key = str(network)
        if key in seen:
            found.append(f"{label}: היעד {key} מופיע פעמיים")
        seen.add(key)
    return found


def _dhcp_problems(cfg: NetConfig,
                   dhcp_cfg: dhcp.InterfaceConfig | None) -> list[str]:
    """כרטיס שמחלק כתובות חייב כתובת סטטית, והיא חייבת להתאים לטווח.

    שתי הבדיקות כאן ולא בשני מקומות: ‏`dhcp.validate()` כבר אוכף
    ש-`server_ip` יושב ברשת של הטווח, אבל הוא אינו יודע דבר על הכתובת
    שהכרטיס באמת יקבל. חיבור השניים הוא מה שמונע כרטיס שמחלק
    ‏10.44.9.50-200 ומקבל כתובת ב-10.44.8.0/24.
    """
    if dhcp_cfg is None or not dhcp_cfg.enabled:
        return []
    if cfg.mode == MODE_DHCP:
        return [f"{cfg.name} מחלק כתובות DHCP — כרטיס כזה אינו יכול להיות "
                "לקוח DHCP בעצמו. כבו את חלוקת הכתובות קודם."]
    if cfg.mode == MODE_MANUAL:
        return [f"{cfg.name} מחלק כתובות DHCP וחייב כתובת סטטית קבועה"]
    found = []
    try:
        dhcp.validate(dhcp_cfg)
    except ValueError as exc:
        found.append(f"הגדרת ה-DHCP על {cfg.name} כבר אינה עקבית: {exc}")
    network = cfg.network()
    if network is None:
        return found
    if dhcp_cfg.server_ip and dhcp_cfg.server_ip.strip() != cfg.address.strip():
        found.append(f"‏DHCP על {cfg.name} מכריז על עצמו בכתובת "
                     f"{dhcp_cfg.server_ip}, והכרטיס יקבל {cfg.address}")
    for value, label in ((dhcp_cfg.range_start, "תחילת הטווח"),
                         (dhcp_cfg.range_end, "סוף הטווח")):
        if value and _valid_ip(value) and ipaddress.IPv4Address(value) not in network:
            found.append(f"{label} שמחולק על {cfg.name} ({value}) יוצא "
                         f"מהרשת החדשה {network}")
    return found


# --- רינדור ------------------------------------------------------------------


def conf_name(name: str) -> str:
    """שם הקובץ ב-`interfaces.d`. תחילית משלנו, כדי שנדע מה שלנו."""
    return f"imagesrv-{name}"


_HEAD = [
    "# ImageSrv -- {name}, generated from the console (spec 24, issue #55).",
    "# Do not edit by hand: the next change in the console rewrites it.",
]


def render(cfg: NetConfig, hostname: str = "") -> str:
    """הקובץ שנכתב ל-`/etc/network/interfaces.d/imagesrv-<שם>`.

    ‏ifupdown, ולא netplan/NetworkManager/systemd-networkd — כך המכונה
    בנויה, ויש כבר תקדים בתיקייה הזו.

    ‏`hostname` (‏#1088): בלקוח DHCP נכתב `hostname <שם>` — ifupdown מעביר
    אותו ל-dhclient כאופציה 12, וכך ה-DNS של המכללה פותר `imagesrv`
    לכתובת שהתקבלה. ריק = לא נכתב (כרטיס סטטי, או קורא שלא מסר שם).

    שתי החלטות ששוות הסבר:

    * ‏`dns-nameservers` **אינו** נכתב. הוא עובד רק דרך resolvconf,
      שאינו מותקן כאן — שורה שנראית כמו הגדרה ואינה עושה כלום היא
      בדיוק "כתבנו את הקובץ" שמתחזה ל"ההגדרה נכנסה". ‏DNS נכתב ישירות
      ל-`/etc/resolv.conf` (‏`render_resolv`), והשורה כאן היא הערה
      שמסבירה איפה הוא באמת יושב.
    * הנתיבים ב-`post-up`/`pre-down` **בלי** `|| true`. ‏`ip route add`
      שנכשל חייב להפיל את ה-ifup, אחרת "הנתיב הוגדר" יהיה נכון על
      הנייר ושקר ב-`ip route` (עיקרון 5).
    """
    lines = [line.format(name=cfg.name) for line in _HEAD]
    if not cfg.managed:
        lines.append(f"# {cfg.name} is not managed from the console.")
        return "\n".join(lines) + "\n"
    lines += ["", f"auto {cfg.name}"]
    if cfg.mode == MODE_DHCP:
        # Debian 13's ifupdown 0.8.44 invokes dhcpcd without -b.  When no
        # lease is available before dhcpcd's timeout it exits, so no client
        # remains to notice that DHCP has returned.  Run the installed client
        # explicitly in background mode: it keeps retrying while ifup can
        # finish, and still leaves a missing address visible in live state.
        lines.append(f"iface {cfg.name} inet manual")
        command = "/usr/sbin/dhcpcd -4 -b"
        if hostname.strip():
            command += f" -h {hostname.strip()}"
        lines += [f"    up {command} {cfg.name}",
                  f"    down /usr/sbin/dhcpcd -4 -k {cfg.name}"]
        return "\n".join(lines) + "\n"
    lines += [
        f"iface {cfg.name} inet static",
        f"    address {cfg.address}",
        f"    netmask {cfg.netmask}",
    ]
    if cfg.slaves:
        # ‏#1264: ‏ifenslave (‏if-pre-up.d) יוצר את ה-bond ומצרף אליו את
        # הכרטיסים. לחברים אין קובץ משלהם — כתובת על חבר הייתה כתובת שנייה
        # באותה רשת, מחוץ ל-bond.
        lines += [f"    bond-slaves {' '.join(cfg.slaves)}",
                  f"    bond-mode {BOND_MODE}",
                  f"    bond-miimon {BOND_MIIMON_MS}"]
    if cfg.gateway:
        lines.append(f"    gateway {cfg.gateway}")
    if cfg.dns:
        lines.append("    # dns " + " ".join(cfg.dns)
                     + "  (written straight to /etc/resolv.conf; no resolvconf here)")
    for route in cfg.routes:
        lines.append(f"    post-up ip route add {route.cidr} via {route.gateway} "
                     f"dev {cfg.name}")
        lines.append(f"    pre-down ip route del {route.cidr} via {route.gateway} "
                     f"dev {cfg.name}")
    return "\n".join(lines) + "\n"


def render_resolv(configs: list[NetConfig]) -> str:
    """`/etc/resolv.conf` — איחוד ה-DNS של כל הכרטיסים, בסדר הכרטיסים.

    קובץ אחד למכונה, ולכן הוא נבנה מכולם ולא מהכרטיס שנערך. כפילויות
    מוסרות תוך שמירת הסדר: הראשון ברשימה הוא הראשון שנשאל.
    """
    servers: list[str] = []
    for cfg in configs:
        for entry in cfg.dns:
            if entry and entry not in servers:
                servers.append(entry)
    lines = ["# ImageSrv -- generated from the console (spec 24, issue #57).",
             "# Do not edit by hand: the next change in the console rewrites it."]
    lines += [f"nameserver {s}" for s in servers[:3]]
    if len(servers) > 3:
        lines.append(f"# ignored, resolv.conf reads three at most: "
                     f"{' '.join(servers[3:])}")
    if not servers:
        lines.append("# no interface declares a DNS server")
    return "\n".join(lines) + "\n"


# --- מה השתנה ----------------------------------------------------------------

#: השדות שהשינוי שלהם יכול לנתק את הקונסולה גם על כרטיס שאינו זה שדרכו
#: הגיעה הבקשה — ולכן הם חמושים באותה החזרה אוטומטית (‏#57).
REACH_FIELDS = ("gateway", "dns", "routes")


def changed(before: NetConfig, after: NetConfig) -> list[str]:
    """שמות השדות שהשתנו. משמש גם לשורת היומן וגם להחלטה על החימוש."""
    names = ("mode", "address", "netmask", "gateway", "dns", "routes", "slaves")
    return [f for f in names if getattr(before, f) != getattr(after, f)]


def summary(cfg: NetConfig) -> str:
    """תיאור קצר לשורת יומן: ‏"eth1 static 10.44.9.10/24"."""
    if cfg.mode == MODE_STATIC:
        bond = f" slaves={'+'.join(cfg.slaves)}" if cfg.slaves else ""
        return f"{cfg.name} {cfg.mode} {cfg.cidr}{bond}"
    return f"{cfg.name} {cfg.mode}"
