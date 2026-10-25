"""רשת ההפצה שמוגדרת **מהקונסולה**, לא במתקין (‏#1088).

הכרעת נדב (18/09): במסירה למכללה רשת ההפצה אינה מוגדרת — כרטיס השרתים
מקבל DHCP, שאר הכרטיסים "לא מוגדר", והמנהל שלו מגדיר את ההפצה מדף
"רשת": בחר כרטיס → כתובת סטטית → "הגדר כרשת הפצה" (הדלקת DHCP, עם השומר
של #53). המסלול הזה הוא **מבחן הקבלה**: הוא חייב להשלים כל מה שהמתקין
היה עושה כשהוא ידע מי כרטיס ההפצה.

מה המתקין כותב שתלוי בכרטיס ההפצה, ומי משלים אותו כאן:

| במתקין | כאן |
|---|---|
| ‏`IMAGECTL_URL` + ‏`--interface` ביחידה | הגדרות `deploy:url` / `deploy:interface` ב-DB — ‏`main` קורא אותן כשאין `--server-url` |
| ‏`grub/grub.cfg` עם כתובת הנפילה | ‏`write_grub_cfg` — אותו `render_bootstrap` |
| ‏`interface=<הפצה>` ב-`imagectl.conf` | ‏`dhcp.render(..., deploy_interface=)` בקובץ שהקונסולה כותבת |
| ‏`systemctl enable dnsmasq` | ‏`enable_dnsmasq` |
| ‏nftables עם `--deploy-if` | ‏`firewall` — המחולל רץ שוב עם שני הכרטיסים |
| — | ‏`restart_server`: השרת עולה מחדש עם כתובת ההפצה (‏`server_base` נקבע בעלייה) |

**מקור הכתובת נאמר, לא מנוחש** (עיקרון 5): `source` הוא `cli` (הדגל
ביחידה — המעבדה, שרת שהותקן עם כרטיס הפצה), `console` (הוגדר מכאן) או
`none` (טרם הוגדר — הסוכן והקיוסק על loopback בלבד, ומסך הבריאות אומר
"רשת ההפצה לא הוגדרה"). כשהמקור הוא `cli` הקונסולה **מסרבת** לרשום
הפצה: ההגדרה שהייתה נכתבת ל-DB לא הייתה משפיעה על כלום, וזה בדיוק
"נשמר" שאינו "נכנס לתוקף".
"""

from __future__ import annotations

import logging
import os
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .atomicfile import fsync_dir, write_atomic
from .db import get_setting, set_settings

SETTING_INTERFACE = "deploy:interface"
SETTING_URL = "deploy:url"

#: הכתובת שהסוכן והקיוסק נקשרים אליה כשרשת ההפצה טרם הוגדרה — loopback,
#: כדי שמסך הבריאות והמתקין (verify-boot-payload) יוכלו לשאול אותו, ואף
#: כרטיס חיצוני לא יראה אותו (fail-closed, כמו הקונסולה ב-#770).
UNCONFIGURED_HOST = "127.0.0.1"

NFTABLES_CONF = "/etc/nftables.conf"
DNSMASQ_UNIT = "dnsmasq"
SERVER_UNIT = "imagectl-server"
#: כמה שניות בין התשובה לקונסולה לאתחול השירות — כדי שהתשובה תצא לפני
#: שהתהליך שכתב אותה נהרג.
RESTART_DELAY_SECONDS = 3

Hooks = dict[str, Callable]


@dataclass(frozen=True)
class DeployState:
    """מה השרת יודע על רשת ההפצה **בריצה הזו**."""

    source: str                       # "cli" | "console" | "none"
    interface: str | None
    url: str | None

    @property
    def configured(self) -> bool:
        return self.source != "none"

    def public(self) -> dict:
        return {"configured": self.configured, "source": self.source,
                "interface": self.interface, "url": self.url,
                "hint": None if self.configured else
                "רשת ההפצה לא הוגדרה — בחר כרטיס בלשונית רשת הפצה והגדר את "
                "הרשת שלו; כתובת השרת נגזרת ממנה (#1263)"}


def stored(conn) -> tuple[str | None, str | None]:
    """‏(כרטיס, כתובת) כפי שנרשמו מהקונסולה, או ‏(None, None)."""
    return (get_setting(conn, SETTING_INTERFACE) or None,
            get_setting(conn, SETTING_URL) or None)


def resolve(conn, cli_url: str | None, cli_interface: str | None) -> DeployState:
    """סדר העדיפות: הדגל ביחידה > מה שנרשם מהקונסולה > לא הוגדר."""
    if cli_url:
        return DeployState("cli", cli_interface, cli_url)
    interface, url = stored(conn)
    if interface and url:
        return DeployState("console", interface, url)
    return DeployState("none", None, None)


def record(conn, interface: str, url: str) -> None:
    """רושם את כרטיס ההפצה וכתובתו — בטרנזאקציה אחת, כמו תצורת האחסון
    (#732): כרטיס בלי כתובת או להפך הוא תצורה שנקראת כתקינה ואינה."""
    set_settings(conn, {SETTING_INTERFACE: interface, SETTING_URL: url})


def deploy_url(server_ip: str, port: int) -> str:
    return f"http://{server_ip}:{port}"


def deploy_ip(server_base: str, state: "DeployState | None") -> str | None:
    """‏#968: הכתובת שהתחנות רואות — המארח של ``server_base`` (מה שב-GRUB
    ובשורת הקרנל). ‏``None`` כשרשת ההפצה טרם הוגדרה: אז ``server_base`` הוא
    ‏loopback, ו-"127.0.0.1" בסקירה היה נתון מומצא (עיקרון 5). ‏``state``
    ‏``None`` = אין הקשר הפצה (בדיקות/קוד ישן) — הכתובת כפי שהיא."""
    if state is not None and not state.configured:
        return None
    from urllib.parse import urlsplit
    return urlsplit(server_base).hostname or None


# --- מה שנוגע במכונה (hooks) --------------------------------------------------


def _run(cmd: list[str], *, stdin_text: str | None = None,
         timeout: float = 30) -> tuple[bool, str, str]:
    """‏(הצליח, stdout, stderr). ‏stdout ו-stderr נפרדים בכוונה: הפלט של
    מחולל חומת האש הוא stdout, ואזהרה ב-stderr אסור שתחליף אותו."""
    kwargs = ({"input": stdin_text} if stdin_text is not None
              else {"stdin": subprocess.DEVNULL})
    try:
        done = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout, check=False, **kwargs)
    except (OSError, subprocess.SubprocessError) as exc:
        # הטקסט הגולמי של מערכת ההפעלה ([WinError 2]…) — ללוג בלבד (#1016).
        logging.getLogger(__name__).warning("deploy_net: %s failed to run: %s", cmd[:3], exc)
        return False, "", f"הפקודה {cmd[0]} לא הצליחה לרוץ — הפרטים ביומן השרת"
    return done.returncode == 0, done.stdout, (done.stderr or "").strip()


def write_grub_cfg(url: str, tftp_root: str | Path) -> str | None:
    """‏`grub/grub.cfg` על שורש ה-TFTP — מאותו מחולל שהמתקין מוטמע ממנו
    (‏`tests/test_installer_matches_generator.py` שומר שהם זהים)."""
    from boot.grub_menu import GrubConfig, render_bootstrap
    path = Path(tftp_root) / "grub" / "grub.cfg"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # ‏#1908: אטומי — grub.cfg חתוך הוא אף מחשב שלא מגיע לתפריט.
        write_atomic(path, render_bootstrap(GrubConfig(server_base=url)), encoding="ascii")
    except (OSError, ValueError) as exc:
        return f"לא ניתן לכתוב את {path}: {exc}"
    return None


def enable_dnsmasq() -> str | None:
    """המתקין לא הפעיל את dnsmasq כשלא ידע מי כרטיס ההפצה; מכאן הוא
    חייב לעלות גם באתחול הבא. ה-restart עצמו — ‏`dhcp_host.apply`."""
    ok, out, err = _run(["systemctl", "enable", DNSMASQ_UNIT])
    return None if ok else f"systemctl enable {DNSMASQ_UNIT} נכשל: {err or out.strip()}"


def firewall_installed(conf: str | Path = NFTABLES_CONF) -> bool | None:
    """האם המתקין כתב חומת אש. ‏None = לא הצלחנו לקרוא (ואינו "לא")."""
    try:
        return "ImageCtl" in Path(conf).read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return False
    except OSError:
        return None


def regenerate_firewall(repo_dir: str | Path, *, deploy_if: str | None, servers_if: str | None,
                        primary_ip: str | None = None,
                        conf: str | Path = NFTABLES_CONF,
                        services: list[tuple[str, list[str]]] | None = None) -> str | None:
    """מריץ שוב את `install/nftables-rules.sh` עם שני הכרטיסים, בודק תחביר
    (`nft -c`), כותב ומטעין. חומת אש שלא הותקנה — לא נוגעים; הותקנה
    וכרטיס השרתים אינו ידוע — שגיאה בשם (הכללים לוילן ההפצה לא ייכתבו).

    ‏`services` (‏#705): ‏[(כרטיס, [שירותים])] — המפעיל בחר שירותים לכל כרטיס,
    והכללים נגזרים מהם בלבד (‏`--per-nic`); אז שני הכרטיסים אינם נדרשים."""
    installed = firewall_installed(conf)
    if installed is None:
        return f"לא ניתן לקרוא את {conf} — חומת האש לא עודכנה"
    if not installed:
        return None
    gen = Path(repo_dir) / "install" / "nftables-rules.sh"
    if services is not None:
        args = ["sh", str(gen), "--per-nic"]
        for nic, names in services:
            args += ["--service", f"{nic}:{','.join(names)}"]
    elif not servers_if:
        return ("כרטיס השרתים אינו ידוע (‎--console-host אינו שם כרטיס) — "
                "הכללים לוילן ההפצה לא נוספו")
    else:
        args = ["sh", str(gen), "--servers-if", servers_if]
        if deploy_if:
            args[2:2] = ["--deploy-if", deploy_if]
    if primary_ip:
        args += ["--primary-ip", primary_ip]
    ok, ruleset, err = _run(args)
    if not ok or not ruleset.strip():
        return f"מחולל חומת האש נכשל: {err or 'פלט ריק'}"
    # ‏#1121: candidate ליד הקובץ → `nft -c` → `nft -f candidate` → ורק אז
    # החלפה אטומית. הקובץ הישן נדרס רק אחרי שהקרנל קיבל את החדש; טעינה
    # שנכשלה מחזירה את ה-ruleset הישן מהקובץ הישן (שעדיין שם), כדי שמה
    # שרץ עכשיו ומה שיעלה אחרי reboot יהיו אותו דבר.
    conf = Path(conf)
    candidate = conf.with_name(conf.name + ".candidate")
    try:
        write_atomic(candidate, ruleset)   # ‏#1908: עם fsync, לפני ה-replace
    except OSError as exc:
        return f"לא ניתן לכתוב את {candidate}: {exc}"
    try:
        ok, _out, err = _run(["nft", "-c", "-f", str(candidate)])
        if not ok:
            return f"תחביר חומת האש אינו תקין (nft -c): {err}"
        ok, _out, err = _run(["nft", "-f", str(candidate)])
        if not ok:
            restored, _o, r_err = _run(["nft", "-f", str(conf)])
            return (f"טעינת חומת האש נכשלה: {err}; {conf} לא שונה, "
                    + ("ה-ruleset הקודם הוחזר" if restored
                       else f"והחזרת ה-ruleset הקודם נכשלה גם היא: {r_err}"))
        try:
            os.replace(candidate, conf)
            fsync_dir(conf.parent)
        except OSError as exc:
            return f"חומת האש נטענה אך {conf} לא הוחלף: {exc}"
        return None
    finally:
        candidate.unlink(missing_ok=True)


def restart_server_later(delay: int = RESTART_DELAY_SECONDS) -> str | None:
    """אתחול השירות **אחרי** שהתשובה יצאה — יחידת systemd חולפת עם
    ‏`--on-active`, כמו העדכון (`update.py`): תהליך-ילד היה נהרג יחד איתנו."""
    # ‏#1121: שם יחידה ייחודי — יחידה חולפת בשם קבוע שעדיין ממתינה (3 ש')
    # הייתה מכשילה השלמה שנייה ב-"Unit already exists"; ‏--collect מנקה.
    # ‏uuid ולא זמן: רזולוציית השעון בווינדוס (הבדיקות) היא מילישניות.
    unit = f"imagectl-deploy-restart-{uuid.uuid4().hex[:12]}"
    ok, out, err = _run(["systemd-run", f"--on-active={delay}",
                         f"--unit={unit}", "--collect",
                         "systemctl", "restart", SERVER_UNIT], timeout=15)
    return None if ok else f"לא ניתן לתזמן אתחול לשרת: {err or out.strip()}"


def default_hooks() -> Hooks:
    return {
        "write_grub_cfg": write_grub_cfg,
        "enable_dnsmasq": enable_dnsmasq,
        "firewall": regenerate_firewall,
        "restart_server": restart_server_later,
    }


@dataclass
class DeployContext:
    """מה שהקונסולה צריכה כדי להשלים את ההתקנה — נבנה ב-`main` ומוזרק
    ל-runtime. ‏`servers_interface` הוא כרטיס הניהול (‏`--console-host`
    כשם), ובלעדיו חומת האש אינה מחוללת מחדש."""

    state: DeployState
    agent_port: int
    tftp_root: Path
    repo_dir: Path
    servers_interface: str | None = None
    primary_ip: str | None = None
    hooks: Hooks | None = None
    #: ‏#705: ‏`services(deploy_if)` → ‏[(כרטיס, [שירותים])] כשהמפעיל ערך שירותים
    #: לכרטיסים, או None (ההתנהגות של היום). בלעדיו ההשלמה הייתה כותבת חומת
    #: אש לפי שני תפקידים — ודורסת בשקט את מה שהמפעיל בחר לכל כרטיס.
    services: Callable[[str], list | None] | None = None


def complete(ctx: DeployContext, conn, *, interface: str, server_ip: str) -> dict:
    """משלים את ההתקנה לכרטיס שהודלק עליו DHCP — **apply לפני commit** (‏#1121).

    הסדר: grub.cfg → dnsmasq באתחול → חומת אש → תזמון אתחול → ורק אז
    ‏`deploy:*` ב-DB. שלב שנכשל עוצר: אין אתחול, אין רישום, ו-`errors`
    נוקב בו — כדי שהמנהל יוכל לתקן ולהדליק שוב (רישום לפני apply היה
    משאיר `source=console` על שרת שחומת האש שלו לא נכתבה, ואתחול היה
    מעלה אותו כך). מה שכבר נכתב לדיסק (grub.cfg) הוא idempotent ונכתב
    שוב בניסיון הבא. מחזיר `ok` רק כשהכול הצליח."""
    hooks = {**default_hooks(), **(ctx.hooks or {})}
    url = deploy_url(server_ip, ctx.agent_port)
    result = {"ok": False, "url": url, "interface": interface,
              "errors": [], "restarting": False, "recorded": False}
    steps = [
        ("grub.cfg", lambda: hooks["write_grub_cfg"](url, ctx.tftp_root)),
        ("dnsmasq", hooks["enable_dnsmasq"]),
    ]
    per_nic = ctx.services(interface) if ctx.services is not None else None
    steps.append(("חומת אש", lambda: hooks["firewall"](
        ctx.repo_dir, deploy_if=interface, servers_if=ctx.servers_interface,
        primary_ip=ctx.primary_ip,
        **({"services": per_nic} if per_nic is not None else {}))))
    for label, call in steps:
        err = call()
        if err:
            result["errors"].append(f"{label}: {err}")
            return result
    restart_error = hooks["restart_server"]()
    if restart_error:
        result["errors"].append(f"אתחול: {restart_error}")
        return result
    record(conn, interface, url)
    result.update(ok=True, restarting=True, recorded=True)
    return result
