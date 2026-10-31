"""הגדרות הרשת של השרת — כל מה שנוגע במכונה עצמה (‏#55/#57).

‏`netcfg.py` הוא הלוגיקה הטהורה: מקבל הגדרות, מחזיר טקסט. כאן יושב הצד
המלוכלך — לכתוב את הקובץ ל-`interfaces.d`, להריץ `ifdown`/`ifup`,
ולקרוא בחזרה מה **באמת** קרה. ההפרדה היא גם מה שמאפשר לבדיקות להזריק
hooks ולעולם לא לגעת ברשת האמיתית, בדיוק כמו `dhcp_hooks`.

**שלושה מצבים, לא שניים** — הלב של המשימה:

1. "כתבנו את הקובץ" — ‏`write_conf` הצליח.
2. "ההגדרה הוחלה" — ‏`ifup` יצא באפס.
3. "הכתובת באמת שם" — ‏`ip addr` מראה אותה.

רק השלישי הוא הצלחה. שניים הראשונים נכונים גם כשקובץ `interfaces`
הראשי לא עושה `source interfaces.d/*`, כשהכרטיס מנוהל בפועל בידי משהו
אחר, או כשהמסכה נדחתה בשקט — ובכל אלה המסך היה מציג "נשמר" ומפעיל
היה מאמין לו. ‏`read_state()` היא הראיה החיובית: ‏`checked=False`
פירושו "לא הצלחנו לשאול", ולא "אין".
"""

from __future__ import annotations

import ipaddress
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from server.net import netcfg
from server.core.atomicfile import write_atomic

INTERFACES_DIR = "/etc/network/interfaces.d"
INTERFACES_MAIN = "/etc/network/interfaces"
RESOLV_CONF = "/etc/resolv.conf"
#: איפה יושב סמן "ממתין לאישור" והפירורים שזרוע ההחזרה משאירה (‏#56).
STATE_DIR = "/var/lib/imagesrv/netcfg"
#: היחידה שמחזירה הגדרה שלא אושרה — היא, ולא השרת, היא ההגנה.
ROLLBACK_TIMER = "imagesrv-netrollback.timer"
#: ‏#1264: מצב כל bond כפי שהקרנל מדווח עליו — הראיה, לא ההגדרה.
BONDING_DIR = "/proc/net/bonding"
BOND_MODULE_CONF = "/etc/modules-load.d/imagesrv-bond.conf"
#: ‏#1264: הסקריפט של ifenslave שיוצר bond מתוך `bond-slaves` ב-ifupdown.
#: בלעדיו `ifup bond0` נופל, ורשת ההפצה נשארת בלי כתובת.
IFENSLAVE_HOOK = "/etc/network/if-pre-up.d/ifenslave"


def conf_path(name: str, root: str | Path = INTERFACES_DIR) -> Path:
    return Path(root) / netcfg.conf_name(name)


# --- כתיבה -------------------------------------------------------------------


def read_conf(name: str, root: str | Path = INTERFACES_DIR) -> str | None:
    """הקובץ כפי שהוא עכשיו, או None אם אינו קיים.

    ‏None אינו "ריק": זה מה שנשמר בסמן ההחזרה כדי שהחזרה תמחק את הקובץ
    במקום להשאיר אותו ריק ומנוהל.
    """
    try:
        return conf_path(name, root).read_text(encoding="utf-8")
    except OSError:
        return None


def write_conf(name: str, text: str | None,
               root: str | Path = INTERFACES_DIR) -> str | None:
    """כותב (או מוחק, כש-`text is None`) את הקובץ. מחזיר שגיאה או None."""
    path = conf_path(name, root)
    try:
        if text is None:
            path.unlink(missing_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            write_atomic(path, text)   # ‏#1908
    except OSError as exc:
        return f"לא ניתן לכתוב את {path}: {exc.strerror or exc}"
    return None


def write_resolv(text: str, path: str | Path = RESOLV_CONF) -> str | None:
    try:
        write_atomic(path, text)   # ‏#1908
    except OSError as exc:
        return f"לא ניתן לכתוב את {path}: {exc.strerror or exc}"
    return None


def read_bond_module(path: str | Path = BOND_MODULE_CONF) -> str | None:
    """Snapshot our boot configuration; unreadable must not mean absent."""
    try:
        return Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


def write_bond_module(text: str | None,
                      path: str | Path = BOND_MODULE_CONF) -> str | None:
    """Persist bonding atomically, or remove our file when returning to one NIC."""
    path = Path(path)
    try:
        if text is None:
            path.unlink(missing_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            write_atomic(path, text)
    except OSError as exc:
        return f"לא ניתן לכתוב את {path}: {exc.strerror or exc}"
    return None


def sourced(main: str | Path = INTERFACES_MAIN,
            root: str | Path = INTERFACES_DIR) -> bool | None:
    """האם `/etc/network/interfaces` בכלל טוען את `interfaces.d`.

    בלי זה כל מה שנכתב שם הוא קובץ שאיש לא קורא — הצורה הנקייה ביותר
    של "כתבנו ולא קרה כלום". ‏None = לא הצלחנו לקרוא את הקובץ הראשי,
    וזה **אינו** "כן".
    """
    try:
        text = Path(main).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    marker = str(root).rstrip("/")
    return any(line.strip().startswith("source") and marker in line
               for line in text.splitlines())


#: ‏#1558: מנהלי רשת אחרים — כשהם מנהלים את המכונה, ifupdown (מה שהקונסולה
#: כותבת אליו) אינו מחיל דבר. המתקין שלנו (ISO) מתקין ifupdown בלבד.
NETPLAN_DIR = "/etc/netplan"
NETWORKD_DIR = "/etc/systemd/network"


def foreign_manager(netplan_dir: str | Path = NETPLAN_DIR,
                    networkd_dir: str | Path = NETWORKD_DIR,
                    resolv: str | Path = RESOLV_CONF) -> str | None:
    """‏None = הרשת מנוהלת ב-ifupdown; אחרת — מה מנהל אותה, בעברית.

    שרת חיפה (17/10): netplan + networkd + resolved. הקונסולה כתבה
    ‏`interfaces.d`, ‏`ifup` לא הכיר את הכרטיס, ו-resolv.conf היה symlink
    לקריאה-בלבד — והמפעיל ראה את התסמינים, לא את הסיבה. תיקייה שלא נקראה
    (חוץ מ"לא קיימת") היא "לא הצלחנו לבדוק" — ולכן גם היא תשובה שאינה None.
    """
    for directory, suffix, who in ((netplan_dir, ".yaml", "netplan"),
                                   (networkd_dir, ".network", "systemd-networkd")):
        try:
            found = sorted(n for n in os.listdir(directory) if n.endswith(suffix))
        except FileNotFoundError:
            continue
        except OSError as exc:
            return f"לא ניתן לבדוק אם הרשת מנוהלת ב-{who} ({directory}: {exc.strerror or exc})"
        if found:
            more = f" ועוד {len(found) - 1}" if len(found) > 1 else ""
            return f"הרשת בשרת הזה מנוהלת ב-{who} ({directory}/{found[0]}{more})"
    if Path(resolv).is_symlink():
        try:
            target = os.readlink(resolv)
        except OSError as exc:
            return f"לא ניתן לקרוא את {resolv} ({exc.strerror or exc})"
        if "systemd/resolve" in target:
            return f"‏{resolv} מנוהל ב-systemd-resolved (‏→ {target})"
    return None


# --- קליטת כרטיס מהקובץ הראשי (המתקין, #1088) ---------------------------------


def comment_out_stanzas(main_text: str, name: str) -> tuple[str, bool]:
    """מסיר (בהערה, לא במחיקה) את ההגדרות של `name` מ-`/etc/network/interfaces`.

    שתי הגדרות לאותו כרטיס — של מתקין דביאן בקובץ הראשי ושלנו ב-
    ‏`interfaces.d` — הן שני `ifup` על אותו כרטיס: ‏dhclient כפול, או
    ‏`RTNETLINK: File exists` על כתובת סטטית שכבר שם. ההערה נשארת קריאה
    (`# imagesrv: ...`) כדי שמי שפותח את הקובץ ידע מה היה ולמה.

    מחזיר ‏(הטקסט החדש, האם משהו השתנה). `auto`/`allow-hotplug` שמונים
    כמה כרטיסים — רק השם שלנו מוסר מהם.
    """
    out: list[str] = []
    changed = False
    in_stanza = False
    for line in main_text.splitlines():
        stripped = line.strip()
        words = stripped.split()
        if in_stanza:
            # גוף ה-iface נמשך בשורות מוזחות (או ריקות/הערות ביניהן).
            if stripped and not line[:1].isspace() and not stripped.startswith("#"):
                in_stanza = False
            else:
                if stripped:
                    out.append(f"# imagesrv: {line}")
                    changed = True
                else:
                    out.append(line)
                continue
        if len(words) >= 2 and words[0] in ("auto", "allow-hotplug") and name in words[1:]:
            rest = [w for w in words[1:] if w != name]
            out.append(f"# imagesrv: {line}")
            if rest:
                out.append(f"{words[0]} {' '.join(rest)}")
            changed = True
            continue
        if len(words) >= 2 and words[0] == "iface" and words[1] == name:
            out.append(f"# imagesrv: {line}")
            changed = True
            in_stanza = True
            continue
        out.append(line)
    return "\n".join(out) + ("\n" if main_text.endswith("\n") else ""), changed


def adopt_interface(name: str, text: str, *, main: str | Path = INTERFACES_MAIN,
                    root: str | Path = INTERFACES_DIR,
                    backup_suffix: str | None = None) -> tuple[list[str], str | None]:
    """המתקין (‏#1088) מעביר את כרטיס השרתים לניהול ImageSrv: הקובץ הראשי
    מפסיק להגדיר אותו (בהערה, עם גיבוי), ‏`interfaces.d` נטען אם לא היה,
    והקובץ שלנו נכתב. מחזיר ‏(מה נעשה, שגיאה או None). **לא** מריץ
    ‏`ifup` — המתקין מחובר דרך הכרטיס הזה; ההגדרה נכנסת באתחול הבא, או
    מהקונסולה.
    """
    notes: list[str] = []
    main_path = Path(main)
    try:
        main_text = main_path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        main_text = ""
    except OSError as exc:
        return notes, f"לא ניתן לקרוא את {main_path}: {exc.strerror or exc}"
    new_text, stanza_changed = comment_out_stanzas(main_text, name)
    changed = stanza_changed
    marker = str(root).rstrip("/")
    if not any(l.strip().startswith("source") and marker in l for l in new_text.splitlines()):
        new_text = new_text.rstrip("\n") + f"\n\n# imagesrv (#1088): interfaces.d was not sourced\nsource {marker}/*\n"
        changed = True
        notes.append(f"{main_path}: נוספה שורת source ל-{marker}")
    if changed:
        if main_text:
            from datetime import datetime
            suffix = backup_suffix or datetime.now().strftime("%Y%m%d%H%M%S")
            backup = main_path.with_name(main_path.name + f".pre-imagesrv.{suffix}")
            try:
                # ‏#2160: ‏0600 — הגיבוי הוא קובץ חדש, ו-write_atomic נותן לקובץ חדש 0644;
                # מקור שהמנהל נעל (‏wpa-psk וכו') היה מקבל עותק קריא לכולם.
                write_atomic(backup, main_text, mode=0o600)   # ‏#1908
            except OSError as exc:
                return notes, f"לא ניתן לגבות את {main_path}: {exc.strerror or exc}"
            notes.append(f"{main_path}: גיבוי ב-{backup.name}")
        try:
            main_path.parent.mkdir(parents=True, exist_ok=True)
            write_atomic(main_path, new_text)   # ‏#1908
        except OSError as exc:
            return notes, f"לא ניתן לכתוב את {main_path}: {exc.strerror or exc}"
        if stanza_changed:
            notes.append(f"{main_path}: ההגדרה של {name} הועברה להערה")
    error = write_conf(name, text, root)
    if error is None:
        notes.append(f"נכתב {conf_path(name, root)}")
    return notes, error


# --- החלה --------------------------------------------------------------------


def _run(cmd: list[str], timeout: int = 40) -> tuple[int | None, str]:
    try:
        done = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout, check=False,
                              stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, str(exc)
    return done.returncode, (done.stderr or done.stdout or "").strip()


def apply_interface(name: str) -> str | None:
    """‏`ifdown` ואז `ifup`. מחזיר הודעת שגיאה או None.

    ‏`ifdown` על כרטיס שאינו מוגדר כרגע יוצא בשגיאה לגיטימית ("not
    configured"), ולכן קוד היציאה שלו אינו קובע — אבל הוא גם אינו
    נזרק: הוא נכנס להודעה שחוזרת לקונסולה, כדי ש"הורדנו ולא ירד" לא
    ייעלם. ‏`ifup` הוא זה שכישלון שלו הוא שגיאה.

    ‏**None כאן פירושו "הפקודות רצו", ולא "הכתובת השתנתה".** את זה
    קובעת רק `read_state()`.
    """
    if name == netcfg.BOND_NAME:
        # #1804: ifenslave creates the bond during ifup. It need not exist
        # yet (nor survive ifdown), so do not require ip link up beforehand.
        code, text = _run(["modprobe", "bonding"])
        if code is None:
            return f"{name}: לא ניתן להפעיל את הכרטיס (modprobe bonding: {text})"
        if code != 0:
            return f"{name}: הפעלת הכרטיס נכשלה (modprobe bonding rc={code}: {text[:200]})"
    else:
        link_code, link_text = _run(["ip", "link", "set", "dev", name, "up"])
        if link_code is None:
            return f"{name}: לא ניתן להפעיל את הכרטיס (ip link: {link_text})"
        if link_code != 0:
            return f"{name}: הפעלת הכרטיס נכשלה (ip link rc={link_code}: {link_text[:200]})"
    down_code, down_text = _run(["ifdown", "--force", name])
    up_code, up_text = _run(["ifup", name])
    if up_code is None:
        return f"{name}: לא ניתן להריץ ifup ({up_text})"
    if up_code != 0:
        note = ""
        if down_code not in (0, None):
            note = f" (ה-ifdown שלפניו החזיר {down_code}: {down_text[:120]})"
        return f"{name}: ifup החזיר {up_code}: {up_text[:200]}{note}"
    return None


def down_interface(name: str) -> str | None:
    """‏`ifdown --force` לבדו (‏#1264) — לפני שהכתובת עוברת מכרטיס ל-bond
    (או חזרה), כשהקובץ הישן עוד במקומו ו-ifdown יודע מה להוריד. מחזיר
    הודעה או None; ההצלחה עצמה נקבעת בקריאה החוזרת שאחרי ה-ifup."""
    code, text = _run(["ifdown", "--force", name])
    if code is None:
        return f"{name}: לא ניתן להריץ ifdown ({text})"
    return None if code == 0 else f"{name}: ifdown החזיר {code}: {text[:200]}"


def bond_supported(path: str | Path = IFENSLAVE_HOOK) -> bool:
    """האם ifenslave מותקן (‏#1264). ‏False — גם כשלא ניתן לבדוק: "כנראה
    מותקן" הוא בדיוק הניחוש שמשאיר את רשת ההפצה בלי כתובת."""
    try:
        return Path(path).is_file()
    except OSError:
        return False


def main_defines(name: str, main: str | Path = INTERFACES_MAIN) -> bool | None:
    """האם `/etc/network/interfaces` עצמו מגדיר את הכרטיס (‏`iface <שם>`).
    ‏None = לא נקרא, ואינו "לא". כרטיס שמוגדר שם וגם חבר ב-bond הוא שני
    ‏`ifup` על אותו כרטיס באתחול (‏#1264)."""
    try:
        text = Path(main).read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return False
    except OSError:
        return None
    return any(line.split()[:2] == ["iface", name] for line in text.splitlines())


def timer_active(unit: str = ROLLBACK_TIMER) -> tuple[bool, str]:
    """האם זרוע ההחזרה מותקנת ורצה — ‏(פעילה, מה נקרא).

    שלושה מצבים מקופלים לשניים בכוונה **לצד הבטוח**: כל דבר שאינו
    ‏`active` מדויק, כולל "לא הצלחנו להריץ systemctl", מחזיר False.
    ההגנה שלא הוכחה אינה הגנה, ובלעדיה אסור להחיל שינוי שיכול לנתק.
    """
    code, text = _run(["systemctl", "is-active", unit], timeout=10)
    if code is None:
        return False, f"‏systemctl לא רץ ({text[:120]})"
    return text.strip() == "active", text.strip() or f"קוד {code}"


# --- הראיה: מה באמת מוגדר עכשיו ----------------------------------------------


@dataclass(frozen=True)
class LiveState:
    """מה שנקרא מהמכונה — או למה לא נקרא.

    ‏`checked=False` אינו "אין כתובות". הוא "לא שאלנו", והוא לעולם אינו
    נחשב התאמה.
    """

    checked: bool
    addresses: dict[str, list[str]] = field(default_factory=dict)
    routes: list[str] = field(default_factory=list)
    nameservers: list[str] = field(default_factory=list)
    reason: str = ""


def parse_addr(text: str) -> dict[str, list[str]]:
    """פלט `ip -4 -o addr show` → {כרטיס: ["10.44.9.10/24", …]}."""
    found: dict[str, list[str]] = {}
    for line in text.splitlines():
        fields = line.split()
        # ‎"2: eth0    inet 10.44.9.10/24 brd … scope global eth0"
        if len(fields) < 4 or fields[2] != "inet":
            continue
        found.setdefault(fields[1], []).append(fields[3])
    return found


def parse_routes(text: str) -> list[str]:
    """פלט `ip -4 route show` → שורות מנורמלות, ‏"<יעד> via <שער>".

    ‏`default` נשמר כפי שהוא: זה השער, וזו הצורה שבה מחפשים אותו.
    """
    found = []
    for line in text.splitlines():
        fields = line.split()
        if not fields:
            continue
        destination = fields[0]
        via = fields[fields.index("via") + 1] if "via" in fields else ""
        found.append(f"{destination} via {via}" if via else destination)
    return found


def parse_resolv(text: str) -> list[str]:
    return [line.split()[1] for line in text.splitlines()
            if line.split()[:1] == ["nameserver"] and len(line.split()) > 1]


def read_state(resolv: str | Path = RESOLV_CONF) -> LiveState:
    """‏`ip addr`, ‏`ip route` ו-`/etc/resolv.conf` — המצב בפועל.

    כל שלושת המקורות חייבים להיקרא. אחד שנכשל מחזיר `checked=False`
    עם הסיבה: מצב שנקרא חלקית הוא בדיוק המקום שבו "לא בדקנו" מתחפש
    ל"בדקנו, הכל תקין".
    """
    problems = []
    addr_code, addr_text = _run(["ip", "-4", "-o", "addr", "show"], timeout=10)
    if addr_code != 0:
        problems.append(f"‏ip addr: {addr_text[:120] or 'לא רץ'}")
    route_code, route_text = _run(["ip", "-4", "route", "show"], timeout=10)
    if route_code != 0:
        problems.append(f"‏ip route: {route_text[:120] or 'לא רץ'}")
    try:
        resolv_text = Path(resolv).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        resolv_text = ""
        problems.append(f"{resolv}: {exc.strerror or exc}")
    if problems:
        return LiveState(False, reason=" · ".join(problems))
    return LiveState(True, parse_addr(addr_text), parse_routes(route_text),
                     parse_resolv(resolv_text))


# --- השוואה: מה ביקשנו מול מה שנקרא ------------------------------------------


def _same_address(wanted: str, seen: str) -> bool:
    try:
        return ipaddress.IPv4Interface(wanted) == ipaddress.IPv4Interface(seen)
    except (ipaddress.AddressValueError, ValueError):
        return wanted.strip() == seen.strip()


def mismatches(cfg: netcfg.NetConfig, state: LiveState) -> list[str]:
    """מה שביקשנו ולא נמצא בקריאה החוזרת. רשימה ריקה = ראיה חיובית.

    ‏`checked=False` מחזיר סיבה אחת ולא רשימה ריקה — "לא ידוע" נופל
    לצד הכושל, תמיד.
    """
    if not state.checked:
        return [f"המצב בפועל לא נקרא ({state.reason}) — אי אפשר לאשר שהשינוי תפס"]
    if not cfg.managed:
        return []
    found = []
    live = state.addresses.get(cfg.name, [])
    if cfg.mode == netcfg.MODE_STATIC:
        if not any(_same_address(cfg.cidr, seen) for seen in live):
            found.append(f"‏{cfg.name} אינו נושא את {cfg.cidr} "
                         f"(‏ip addr מראה: {', '.join(live) or 'כלום'})")
        if cfg.gateway and f"default via {cfg.gateway}" not in state.routes:
            found.append(f"שער ברירת המחדל {cfg.gateway} אינו בטבלת הניתוב")
        for route in cfg.routes:
            if f"{route.cidr} via {route.gateway}" not in state.routes:
                found.append(f"הנתיב {route.cidr} דרך {route.gateway} "
                             "אינו בטבלת הניתוב")
    elif not live:
        found.append(f"‏{cfg.name} לא קיבל שום כתובת מ-DHCP")
    for server in cfg.dns:
        if server not in state.nameservers:
            found.append(f"‏{server} אינו ב-{RESOLV_CONF}")
    return found


#: ‏#2094: כמה זמן קריאה חוזרת של DHCP מחכה ל-lease. ‏``dhcpcd -b`` חוזר מיד
#: (‏render), וה-lease הגיע ~7 שניות אחר כך (הצעה + 5 שניות של בדיקת ARP,
#: ESXi 24/10) — קריאה מיידית אמרה "לא אומת" על הפעלה מחדש שהצליחה.
DHCP_LEASE_WAIT_S = 30


def settled_mismatches(cfg: netcfg.NetConfig, read_state, *,
                       wait_s: float = DHCP_LEASE_WAIT_S,
                       sleep=time.sleep, clock=time.monotonic) -> list[str]:
    """``mismatches`` — ובכרטיס DHCP נקרא שוב כל שנייה עד שה-lease מגיע או
    עד ``wait_s``. סטטי נקרא פעם אחת: כתובת סטטית יושבת כשה-ifup חוזר.
    הפסקה בזמן היא עדיין הפער האחרון שנקרא, לא "בסדר" (עיקרון 5)."""
    deadline = clock() + (wait_s if cfg.mode == netcfg.MODE_DHCP else 0)
    while True:
        gaps = mismatches(cfg, read_state())
        if not gaps or clock() >= deadline:
            return gaps
        sleep(1)


# --- ‏#1264: מצב ה-bond -------------------------------------------------------

#: איך הקרנל קורא ל-balance-alb ב-`/proc/net/bonding/<bond>`.
BOND_MODE_TEXT = "adaptive load balancing"


@dataclass(frozen=True)
class BondState:
    """מה שהקרנל מדווח על bond — או למה לא נקרא.

    ‏`checked=False` הוא "לא נבדק", ולעולם לא "פעיל" (עיקרון 5).
    ‏`slaves` הוא {כרטיס: ‏"up"/"down"} — ה-MII של כל חבר, כלומר הקישור."""

    checked: bool
    mode: str = ""
    mii: str = ""
    slaves: dict[str, str] = field(default_factory=dict)
    reason: str = ""


def parse_bonding(text: str) -> BondState:
    """פלט `/proc/net/bonding/<bond>` → ‏BondState. שורות ה-MII שלפני
    ‏`Slave Interface` הראשון הן של ה-bond; כל אחת אחריה — של החבר האחרון."""
    mode = mii = ""
    slaves: dict[str, str] = {}
    current = None
    for line in text.splitlines():
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if key == "Bonding Mode":
            mode = value
        elif key == "Slave Interface":
            current = value
            slaves[current] = ""
        elif key == "MII Status":
            if current is None:
                mii = value
            else:
                slaves[current] = value
    if not mode:
        return BondState(False, reason="אין שורת Bonding Mode — זה אינו פלט של bond")
    return BondState(True, mode, mii, slaves)


def read_bond(name: str, root: str | Path = BONDING_DIR) -> BondState:
    path = Path(root) / name
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return BondState(False, reason=f"{path}: {exc.strerror or exc}")
    return parse_bonding(text)


def bond_mismatches(cfg: netcfg.NetConfig, bond: BondState,
                    state: LiveState) -> list[str]:
    """מה שביקשנו מה-bond ולא נמצא. ריק = ראיה חיובית. **הקישור אינו
    כאן**: כבל שנשלף הוא מצב (מנותק), לא הגדרה שלא תפסה."""
    if not cfg.slaves:
        return []
    if not bond.checked:
        return [f"מצב {cfg.name} לא נקרא ({bond.reason}) — אי אפשר לאשר "
                "ששני הכרטיסים חברים בו"]
    found = []
    if BOND_MODE_TEXT not in bond.mode.lower():
        found.append(f"{cfg.name} במצב '{bond.mode}' ולא {netcfg.BOND_MODE}")
    for slave in cfg.slaves:
        if slave not in bond.slaves:
            found.append(f"{slave} אינו חבר ב-{cfg.name}")
        elif state.checked and state.addresses.get(slave):
            found.append(f"{slave} נושא כתובת משלו "
                         f"({', '.join(state.addresses[slave])}) — מחוץ ל-{cfg.name}")
    return found


def default_hooks() -> dict:
    """מה שמוזרק ב-`create_app`. בבדיקות כל אלה מוחלפים, ולכן שום בדיקה
    אינה כותבת קובץ רשת ואינה מריצה `ifup` — אותה תבנית של `dhcp_hooks`."""
    return {
        "netcfg_read_conf": read_conf,
        "netcfg_write_conf": write_conf,
        "netcfg_write_resolv": write_resolv,
        "netcfg_apply": apply_interface,
        "netcfg_state": read_state,
        "netcfg_sourced": sourced,
        "netcfg_foreign_manager": foreign_manager,   # #1558
        "netcfg_timer_active": timer_active,
        # ‏#1264: רשת הפצה על שני כרטיסים.
        "netcfg_down": down_interface,
        "netcfg_bond_state": read_bond,
        "netcfg_bond_supported": bond_supported,
        "netcfg_read_bond_module": read_bond_module,
        "netcfg_write_bond_module": write_bond_module,
        "netcfg_main_defines": main_defines,
    }
