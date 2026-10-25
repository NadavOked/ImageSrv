"""ImageCtl — מחולל grub.cfg.

הרכיב הזה הוא נתב, לא תפריט. הוא מקבל את תשובת השרת (ממשק 3) ומחזיר טקסט
grub.cfg שעושה אחד משני דברים: מעלה את הסוכן, או עושה chain לדיסק המקומי.

ברירת המחדל היא תמיד הדיסק המקומי. כל מצב שאינו חד-משמעי — MAC לא מוכר,
schema לא מוכר, JSON פגום, חריגה בקוד — נופל לשם.

המודול הזה טהור: אין בו גישה לרשת, לדיסק או ל-DB. הוא מקבל dict ומחזיר str.
מי שמארח אותו (השרת) אחראי לעשות את ה-lookup ולהגיש את התוצאה ב-HTTP.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from .trace import grub_call, grub_function

__all__ = [
    "GrubConfig",
    "Decision",
    "LOCAL",
    "AGENT",
    "LOCAL_BOOT_PATHS",
    "SUPPORTED_SCHEMA",
    "MENU_NO_TIMEOUT",
    "ROLES_WITH_NORMAL_ENTRY",
    "ROLES_WITH_GUI",
    "normalize_mac",
    "decide",
    "render",
    "render_local_only",
    "render_bootstrap",
]

# ---------------------------------------------------------------------------
# קבועים
# ---------------------------------------------------------------------------

#: הגרסה היחידה של ממשק 3 שהמחולל יודע לפרש. schema גבוה יותר = שינוי שובר,
#: ולכן מצב לא ברור, ולכן דיסק מקומי.
SUPPORTED_SCHEMA = 1

#: ‏GRUB: ערך שלילי ב-timeout = התפריט ממתין לאדם ללא הגבלת זמן. זה מה
#: שנדב הכריע ב-#140 עבור כל מכונה רשומה שאין לה משימה — ראו decide().
MENU_NO_TIMEOUT = -1

#: מצבי סבב שבהם יש למה להעלות את הסוכן.
JOINABLE_SESSION_STATES = frozenset({"open", "running"})

#: התפקידים שערך ה-ImageCtl היחיד שלהם הוא הסוכן במצב **רגיל** (#144).
#: כל תפקיד אחר מקבל את הערך במצב `recovery`, וזה לא שרירותי — זו טבלת
#: ההחלטה של הסוכן עצמו (`agent/lib/decide.sh`) קרואה מהצד השני:
#:
#:   build      → `build_console`  — מסך הקליטה/ההפצה, הדרך היחידה אליו
#:                מאתחול קר (#29). ‏recovery היה מסתיר אותו.
#:   classroom  → `local`, כלומר `die_local` ו-`reboot -f`. ערך "רגיל"
#:                בתחנת כיתה בלי משימה הוא **הערך היחיד שאינו עושה דבר**,
#:                ומאז #140 (תפריט בלי טיימר) הוא גם לולאה: המכונה
#:                מאתחלת וחוזרת לאותו תפריט. זה מה שקרה ללנובו ב-#144.
#:   כל השאר    → `local` באותה שורת ברירת מחדל, ולכן אותו טיפול.
#:
#: מכונה עם משימה או סבב פתוח אינה מגיעה לכאן בכלל — היא במסלול AGENT.
ROLES_WITH_NORMAL_ENTRY = frozenset({"build"})

#: התפקידים שמחובר אליהם מסך שאדם מסתכל בו, ולכן מקבלים את ה-initramfs
#: הגרפי (#32, הכרעת הבעלים 2026-09-04). כל תפקיד אחר מקבל את הטקסטואלי.
#:
#:   build      → מסך הקליטה/ההפצה של מחשב הבנייה.
#:   classroom  → מסך התחנה מול התלמיד.
#:   cloner     → התקדמות גרפית פר-מגירה והכרעות SMART מקומיות (הכרעת
#:                הבעלים, הופכת את "cloner בלי מסך"). הוא **נשאר diskless**
#:                ועולה ישר לסוכן בלי ערך מקומי — #17 לא זז: ההרחבה כאן
#:                נוגעת ב-initramfs שנבחר בלבד, לא בערכי התפריט.
#:
#: הבחירה תלויה ב**תפקיד** ולא במסלול ההחלטה, מאותה סיבה שבה `diskless`
#: תלוי בתפקיד: ‏cloner מגיע ל-AGENT בשלושה מסלולים שונים, ובחירה לפי
#: `code` הייתה מחזירה את ההתנהגות הישנה ברגע שנפתח סבב (#320).
ROLES_WITH_GUI = frozenset({"build", "classroom", "cloner"})

_MAC_CANONICAL = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")
_MAC_STRIP = re.compile(r"[^0-9a-fA-F]")

# GRUB לא מרנדר עברית — אין RTL ואין פונט מלא. כל טקסט שמוצג במסך האתחול
# חייב להיות אנגלית ASCII בלבד.
# הראשון לערכים בשורה אחת (מוחק גם שורות חדשות), השני לקובץ שלם.
_ASCII_SAFE = re.compile(r"[^\x20-\x7e]")
_GRUB_META = re.compile(r"[\s$\"'`\\;{}()<>|&]")
_ASCII_SAFE_MULTILINE = re.compile(r"[^\x20-\x7e\n]")

# ---------------------------------------------------------------------------
# תצורה
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GrubConfig:
    """כתובות ותזמונים. לא חלק מאף ממשק — תצורת פריסה מקומית.

    server_base חייב להיות http (לא https): ה-GRUB החתום של דביאן נבנה בלי
    תמיכת TLS, וה-http module שלו מדבר HTTP פשוט בלבד.
    """

    server_base: str
    kernel_path: str = "/boot/vmlinuz"
    initrd_path: str = "/boot/initrd.img"

    #: ה-initramfs הגרפי, לתפקידים שב-ROLES_WITH_GUI (#32). ‏None פירושו
    #: "אין כזה על השרת הזה", וכל התפקידים נופלים ל-initrd_path.
    #:
    #: מכוון שזה נתיב ולא דגל: המודול הזה טהור ואינו נוגע בדיסק, ולכן
    #: **מי שממלא את השדה הוא מי שווידא שהקובץ קיים** — ראו
    #: `boot.http.gui_initrd_path()`. נתיב שקיים בתצורה אבל לא על הדיסק
    #: הוא 404 מול GRUB בכיתה שלמה, ומכונה שלא עולה גרועה ממכונה בלי
    #: גואי (עיקרון 1). ברירת המחדל היא None ולא נתיב משוער, בדיוק
    #: מהסיבה הזאת.
    gui_initrd_path: str | None = None

    #: אין כאן יותר שדות תזמון. ‏hidden_timeout (2 שניות ל-ESC) ו-menu_timeout
    #: (‏15 שניות בתפריט הגלוי) הוסרו ב-#140: מכונה רשומה מקבלת תפריט גלוי
    #: שממתין לאדם ללא הגבלה, ומכונה שאיננו משרתים עולה מהדיסק מיד. שני
    #: הערכים נגזרים מההחלטה עצמה ואין מה לכוונן בהם.
    #: פרמטרים נוספים לשורת הפקודה של הקרנל, אם צריך לחומרה חריגה.
    extra_cmdline: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        parts = urlsplit(self.server_base)
        if parts.scheme != "http":
            raise ValueError(
                f"server_base must be http:// (GRUB has no TLS), got {self.server_base!r}"
            )
        if not parts.netloc:
            raise ValueError(f"server_base has no host: {self.server_base!r}")
        if _ASCII_SAFE.search(self.server_base):
            raise ValueError("server_base must be plain ASCII")
        # ‏#1127: הכתובת נכנסת לשורת הקרנל ולסקריפט GRUB — תווים שמפרשים
        # שם ($, גרשיים, רווח, ;) אינם כתובת. המקור מקומי (CLI/DB), וזול לסגור.
        if _GRUB_META.search(self.server_base):
            raise ValueError("server_base carries a GRUB/shell metacharacter")

    @property
    def grub_host(self) -> str:
        """ה-netloc בתחביר ההתקן של GRUB: ‎(http,10.44.0.10:8080)."""
        return urlsplit(self.server_base).netloc

    @property
    def base(self) -> str:
        """כתובת הבסיס בלי / בסוף — נמסרת לסוכן ב-imagectl.server."""
        return self.server_base.rstrip("/")


# ---------------------------------------------------------------------------
# החלטה
# ---------------------------------------------------------------------------

LOCAL = "local"
AGENT = "agent"


@dataclass(frozen=True)
class Decision:
    """מה יוגש, ולמה.

    יש כאן שני שדות הסבר במכוון. code הוא אסימון קבוע מרשימה סגורה, והוא
    היחיד שנכתב לתוך הקובץ. reason הוא טקסט חופשי ליומן השרת בלבד ויכול
    להכיל ערכים מתשובת השרת. הפרדה זו היא שמונעת מפרטי משימה לזלוג למסך
    האתחול — ראו את הבדיקה test_agent_config_leaks_nothing_from_the_answer.
    """

    action: str
    code: str
    reason: str = ""
    show_menu: bool = False
    offer_recovery: bool = False
    offer_agent: bool = False
    #: האם הקובץ מכיל בכלל ערך "עלה מהדיסק המקומי" ואת הפונקציה
    #: ‏`chain_local` שמאחוריו. כבוי רק למחשב שיכפול (#320).
    offer_local: bool = True
    #: האם ערך ה-ImageCtl טוען את ה-initramfs הגרפי (#32). נגזר מהתפקיד
    #: בלבד, ולכן אינו יכול להשתנות כשאותה מכונה עוברת מהמתנה לסבב.
    #: ברירת המחדל False: כל החלטה שנוצרת לפני שהתפקיד ידוע — ‏JSON פגום,
    #: schema לא נתמך, MAC לא רשום — היא ממילא מצב לא ברור.
    gui_screen: bool = False
    recovery_mode: bool = False
    extra: dict = field(default_factory=dict)


def normalize_mac(raw: object) -> str | None:
    """מנרמל MAC לתבנית הקנונית של סעיף 6 בממשקים: lowercase עם נקודתיים.

    מקבל את כל הווריאציות שמופיעות באפיון (‎B4:2E:99 / b4-2e-99 / b42e99).
    מחזיר None אם זה לא MAC תקין — והקורא חייב להתייחס לזה כמצב לא ברור.
    """
    if not isinstance(raw, str):
        return None
    hex_only = _MAC_STRIP.sub("", raw).lower()
    if len(hex_only) != 12:
        return None
    canonical = ":".join(hex_only[i : i + 2] for i in range(0, 12, 2))
    return canonical if _MAC_CANONICAL.match(canonical) else None


def decide(answer: object) -> Decision:
    """טבלת ההחלטה. מקבלת תשובת שרת (ממשק 3), מחזירה מה להגיש.

    הסדר קובע — כל בדיקה שנכשלת מפילה לדיסק המקומי ולא ממשיכה הלאה.
    """
    if not isinstance(answer, dict):
        return Decision(LOCAL, "bad-answer", "answer is not an object")

    schema = answer.get("schema", SUPPORTED_SCHEMA)
    if not isinstance(schema, int) or schema > SUPPORTED_SCHEMA:
        return Decision(LOCAL, "bad-schema", f"unsupported contract schema {schema!r}")

    # known: false → כלום. המחשב עולה מהדיסק כאילו לא קרה דבר.
    # ההתראה על MAC לא מוכר עולה בקונסולה, לא במסך של התלמיד.
    if answer.get("known") is not True:
        # ‏#1802 (הכרעת נדב 19/10): חריג אחד, ורק כשהשרת אמר אותו במפורש —
        # מכונה לא רשומה **בווילן ההפצה** מקבלת פעם אחת את הסוכן, שמציג
        # "המחשב הזה לא רשום" עשר שניות ועולה מהדיסק. השרת מדליק את הדגל
        # רק על ראיה חיובית שהבקשה הגיעה לכתובת ההפצה, ורק פעם אחת בחלון
        # (‏`server/unknown_screen.py`) — האתחול הבא של אותו MAC מגיע לכאן
        # בלי הדגל ועולה מהדיסק. ‏`is True` בכוונה: כל ערך אחר הוא כמו היום.
        # שורת הקרנל נשארת נקייה (עיקרון 2): ערך רגיל, בלי `imagectl.mode`.
        if answer.get("unregistered_screen") is True:
            return Decision(AGENT, "unregistered-screen",
                            "mac not registered, deploy vlan: the screen once (#1802)",
                            offer_local=True, gui_screen=True)
        return Decision(LOCAL, "unregistered", "mac not registered")

    role = answer.get("role")
    if not isinstance(role, str):
        role = "unknown"

    # שומר לולאת האתחול (‏#75). השרת ספר שהוא כבר שלח את המכונה הזו
    # לסוכן שוב ושוב עבור אותה עבודה, והיא חזרה בכל פעם — כלומר היא
    # נכשלת לפני שהיא מספיקה לדווח. הסבב אינו מוסר לה יותר, והיא
    # עולה מהדיסק המקומי כמו כל מצב לא ברור אחר. הדגל אינו קופץ מעל
    # התפקיד: מחשב הבנייה עדיין מקבל את התפריט הגלוי שלו (‏#29), ותחנה
    # עדיין מקבלת בו את ערך השחזור — היא בדיוק המכונה שטכנאי צריך
    # להיכנס אליה, וזו הדרך היחידה לתקן מכונה תקועה בשטח.
    guarded = answer.get("boot_guard") == "exhausted"

    # ‏#320: למחשב שיכפול אין דיסק אתחול — יש לו מגירות, והן הסחורה.
    # אתחול מאחת מהן הוא נגיעה בדיסק שאמור להיכתב, ולכן הקובץ שלו יוצא
    # בלי ערך מקומי ובלי `chainloader` בכלל. הדגל תלוי ב**תפקיד** ולא
    # במסלול: ‏cloner מגיע ל-AGENT בשלושה מסלולים שונים (משימה, סבב פתוח,
    # והמתנה), וקובץ בלי דיסק בהמתנה שמצמיח ערך מקומי ברגע שנפתח סבב
    # הוא בדיוק הבור שכיסוי לפי code היה משאיר פתוח.
    diskless = role == "cloner"

    # ‏#32: אותה תבנית בדיוק, ומאותו טעם. ‏gui נגזר מהתפקיד פעם אחת, לפני
    # ההסתעפות, ונמסר לכל החלטה שיכולה לייצר ערך ImageCtl — כדי שמכונה
    # לא תחליף initramfs באמצע היום כשנפתח לה סבב.
    gui = role in ROLES_WITH_GUI

    if not guarded and _is_actionable_task(answer.get("task")):
        return Decision(AGENT, "task-assigned", f"task assigned (role={role})",
                        offer_local=not diskless, gui_screen=gui)

    if not guarded and _is_joinable_session(answer.get("session")):
        state = answer["session"].get("state")
        return Decision(AGENT, "session-joinable", f"session {state} (role={role})",
                        offer_local=not diskless, gui_screen=gui)

    if diskless:
        # למחשב שיכפול אין לעולם מערכת מקומית — המגירות שלו הן הסחורה,
        # לא דיסק אתחול. ברירת המחדל שלו היא מסך ההמתנה של הסוכן
        # (טבלת ההחלטות: cloner בלי סבב = wait_poll). בלי זה, מכונה
        # שעולה קר כשהמגירות שלה לא "טריות" נשלחת לדיסק ריק וכבה (#17).
        # מאותה סיבה גם השומר של #75 אינו חל כאן: אין לאן להפיל אותו,
        # ולכן השרת אינו סופר אותו מלכתחילה (ראו server/bootguard.py).
        return Decision(AGENT, "cloner-wait", f"cloner waits on the agent (role={role})",
                        offer_local=False, gui_screen=gui)

    # task: null וגם session: null — ברירת המחדל של הממשק.
    #
    # ‏#140 (הכרעת נדב, 2026-08-30): כל מכונה רשומה שאין לה משימה מגיעה
    # למסך בחירה **גלוי** בין הדיסק המקומי ל-ImageCtl — תחנת כיתה בדיוק
    # כמו מחשב הבנייה, בלי ESC ובלי טיימר. עד כאן היה הבדל *הצגה* בין
    # התפקידים (‏ROLES_WITH_VISIBLE_MENU); הוא בוטל, והתפקיד אינו קובע
    # יותר **אם** יש תפריט. מ-#144 הוא קובע **מה** יש בו: אותם שני ערכים
    # בדיוק לכל תפקיד, כשהשני נבחר לפי מה ששימושי למכונה הזו.
    #
    # המחיר הוצג לנדב ואושרר: אתחול רגיל של תחנה כבר **אינו** עולה מהדיסק
    # תוך שניות — הוא ממתין לאדם. ‏set default=local נשאר, אבל בלי טיימר
    # הוא רק הערך המסומן ולא נבחר לעולם. מה שעדיין מגן על עיקרון 1 הוא
    # שני השומרים שלמעלה (‏unregistered ו-bad-schema), ששניהם עדיין עולים
    # מהדיסק בלי תפריט, וכיבוי השרת — שם PXE נכשל בקושחה והמכונה לא
    # מגיעה לקובץ הזה בכלל.
    #
    # ‏#144: **ערך ImageCtl אחד** בנוסף לדיסק, ולא שניים. עד כאן הוצגו גם
    # `imagectl` וגם `imagectl-recovery` זה לצד זה, בלי שום דבר על המסך
    # שמסביר את ההבדל — ונדב בחר בלנובו (`role=classroom`) דווקא את זה
    # שמחזיר `local` ומאתחל. איזה מהשניים שימושי נקבע בידי התפקיד, וזה
    # ידוע לשרת; ראו ROLES_WITH_NORMAL_ENTRY. בדיוק אחד מהדגלים דלוק.
    normal = role in ROLES_WITH_NORMAL_ENTRY
    code = "boot-loop-guard" if guarded else "no-task"

    # ‏#641 (הכרעת נדב, 2026-09-10): היפוך #140. מחשב build/classroom רשום
    # שאין לו משימה עולה **ישר ל-ImageCtl, אוטומטי לגמרי** — בלי מסך בחירה,
    # בלי ESC, בלי טיימר. בחירת הדיסק המקומי עברה אל תוך מסך ה-ImageCtl
    # עצמו. זה בדיוק המסלול שבו משימה/סבב פעיל מעלים את הסוכן (AGENT,
    # ‏timeout=0, hidden, default=imagectl); ההבדל היחיד הוא הערך המתאים
    # לתפקיד — ‏build מקבל את הערך הרגיל (build_console, #29), ו-classroom
    # את ה-recovery (מסך הכניסה/בחירה, כי "רגיל" ב-classroom מחזיר local
    # ומאתחל — #144). ‏offer_local נשאר דלוק כמו במסלול המשימה: הערך המקומי
    # קיים בקובץ אך חבוי (timeout=0), והבחירה שאדם רואה היא של מסך ImageCtl.
    #
    # ‏`not guarded` בכוונה: כששומר לולאת האתחול (#75) ירה, המכונה **לא**
    # תעלה אוטומטית אל מה שכבר לולא אותה — היא מקבלת את התפריט הגלוי כדי
    # שטכנאי ייכנס לשחזור (ראו ROLES_WITH_NORMAL_ENTRY והטסטים של #75).
    # תפקיד שאינו ב-ROLES_WITH_GUI (teacher/unknown וכו') אינו משתנה גם
    # הוא ונשאר בתפריט הגלוי של #140.
    if gui and not guarded:
        return Decision(
            AGENT,
            code,
            f"no task, auto-boot imagectl "
            f"(role={role}, entry={'normal' if normal else 'recovery'})",
            offer_local=True,
            gui_screen=gui,
            recovery_mode=not normal,
        )

    return Decision(
        LOCAL,
        code,
        f"no task (role={role}, menu shown, entry={'normal' if normal else 'recovery'})",
        show_menu=True,
        offer_recovery=not normal,
        offer_agent=normal,
        gui_screen=gui,
    )


def _is_actionable_task(task: object) -> bool:
    """משימה נחשבת קיימת אם היא אובייקט לא ריק. אין הצצה לשדות שלה —
    הסוכן מקבל את הפרטים מ-hello, לא מ-GRUB."""
    return isinstance(task, dict) and bool(task)


def _is_joinable_session(session: object) -> bool:
    if not isinstance(session, dict):
        return False
    return session.get("state") in JOINABLE_SESSION_STATES


# ---------------------------------------------------------------------------
# רינדור
# ---------------------------------------------------------------------------


def render(answer: object, config: GrubConfig, *, recovery: bool = False) -> str:
    """הפונקציה היחידה שהשרת צריך לקרוא לה.

    לעולם לא זורקת. כל תקלה מחזירה קובץ שעושה chain לדיסק המקומי — כי מסך
    אתחול שנכשל חייב להשאיר מחשב שעובד, לא מחשב תקוע.
    """
    try:
        decision = decide(answer)
        if recovery:
            # לא בשימוש היום: ה-ESC נבחר במחשב עצמו, לא בשרת. קיים כדי
            # שאפשר יהיה בעתיד לפתוח שחזור מהקונסולה בלי מקש.
            decision = Decision(AGENT, "recovery-requested", recovery_mode=True)
        return _render_decision(decision, config)
    except Exception:  # noqa: BLE001 — כאן זו בדיוק הכוונה
        return render_local_only("generator-error")


def render_local_only(code: str) -> str:
    """קובץ המילוט. עולה מהדיסק המקומי, בלי תפריט ובלי הודעה."""
    return _assemble(
        code=code,
        timeout=0,
        timeout_style="hidden",
        entries=[_entry_local()],
    )


def _render_decision(decision: Decision, config: GrubConfig) -> str:
    if decision.action == AGENT:
        # ‏#320: ‏offer_local כבוי (מחשב שיכפול) = ערך יחיד בקובץ, וגם
        # ה-`chain_local` נשאר בחוץ. הוא לא היה נבחר לעולם תחת
        # ‏timeout=0/hidden, אבל הוא כן היה משאיר `chainloader` בקובץ ואת
        # רשימת נתיבי האתחול שמאחוריו — והדרישה היא שלא תהיה שם דרך אל
        # הכוננים, לא שקשה להגיע אליה.
        entries = [_entry_agent(config, recovery=decision.recovery_mode,
                                gui=decision.gui_screen,
                                fallback_local=decision.offer_local)]
        if decision.offer_local:
            entries.append(_entry_local())
        return _assemble(
            code=decision.code,
            timeout=0,
            timeout_style="hidden",
            default="imagectl",
            entries=entries,
            chain_local=decision.offer_local,
            trace_host=config.grub_host,
        )

    # ערך אחד בלבד בנוסף לדיסק (#144). הדגלים אינם נדלקים יחד ב-decide,
    # ו-`or` כאן אינו מציג שניים גם אם מישהו ידליק בטעות: המצב נקבע לפי
    # offer_recovery, והערך תמיד נקרא `imagectl`.
    entries = [_entry_local()]
    if decision.offer_agent or decision.offer_recovery:
        entries.append(_entry_agent(config, recovery=decision.offer_recovery,
                                    gui=decision.gui_screen,
                                    fallback_local=True))

    # שני מצבים בלבד, ושניהם נגזרים מההחלטה — לא מהתצורה:
    #
    # תפריט גלוי → ‏MENU_NO_TIMEOUT, ממתין לאדם ללא הגבלה (#140).
    #
    # בלי תפריט → אפס. מי שמגיע לכאן בלי תפריט הוא מי שאין לו גם צוהר
    # שחזור (‏unregistered ו-bad-schema, שניהם עם offer_recovery=False),
    # כלומר קובץ עם ערך אחד ואין בו מה ללחוץ עליו. שתי שניות שם היו המתנה
    # חסרת תכלית על מחשב שאיננו משרתים — ולכן הוא מקבל בדיוק את מה
    # ש-render_local_only נותן ל-MAC פגום: חיבור מיידי לדיסק המקומי.
    return _assemble(
        code=decision.code,
        timeout=(MENU_NO_TIMEOUT if decision.show_menu else 0),
        timeout_style=("menu" if decision.show_menu else "hidden"),
        default="local",
        entries=entries,
        # שביל הפירורים מוגדר רק כשיש בקובץ ערך ImageCtl שישתמש בו.
        # קובץ שכולו דיסק מקומי אינו צריך אותו, ופונקציה שאיש אינו
        # קורא לה היא עוד קוד על מסך אתחול.
        trace_host=(config.grub_host if len(entries) > 1 else ""),
    )


def _assemble(
    *,
    code: str,
    timeout: int,
    timeout_style: str,
    entries: list[str],
    default: str = "local",
    chain_local: bool = True,
    trace_host: str = "",
) -> str:
    head = [
        "# Generated by ImageCtl. Do not edit - regenerated on every boot.",
        f"# decision: {_ascii(code)}",
        "",
        "set pager=0",
        f"set default={default}",
        f"set timeout={int(timeout)}",
        f"set timeout_style={timeout_style}",
    ]
    if chain_local:
        # ‏#416: הענף המקומי מדווח פירורים בדיוק כשיש בקובץ את הפונקציה.
        head += [
            "",
            _func_chain_local(traced=bool(trace_host)),
            "",
            _FUNC_TRY_LOCAL,
            "",
        ]
    else:
        head += [""]
    if trace_host:
        head += [grub_function(trace_host), ""]
    # שומר אחרון: שום תו שאינו ASCII לא יוצא מכאן, גם אם מישהו יוסיף
    # מחר מקף עברי או אמוג'י לאחת המחרוזות הקבועות למעלה.
    return _strip_non_ascii("\n".join(head + entries) + "\n")


# --- מקטעים קבועים -------------------------------------------------------

#: נתיבי ה-bootloader שמנסים על הדיסק המקומי, לפי הסדר (אפיון סעיף 9).
#: Windows קודם, אחר כך הפצות לינוקס, ובסוף הנתיב הגנרי. בלינוקס מעדיפים
#: את shim (חתום Microsoft, ולכן עובר אימות תחת כל shim) על grubx64.efi
#: (חתום בידי ההפצה — shim של דביאן לא בהכרח מכיר את המפתח של אובונטו).
#: הפצה שחסרה כאן נופלת ל-bootx64.efi, שרוב המתקינים משאירים גם אותו.
LOCAL_BOOT_PATHS: tuple[str, ...] = (
    "/EFI/Microsoft/Boot/bootmgfw.efi",
    "/EFI/ubuntu/shimx64.efi",
    "/EFI/ubuntu/grubx64.efi",
    "/EFI/debian/shimx64.efi",
    "/EFI/debian/grubx64.efi",
    "/EFI/fedora/shimx64.efi",
    "/EFI/fedora/grubx64.efi",
    "/EFI/centos/shimx64.efi",
    "/EFI/rocky/shimx64.efi",
    "/EFI/almalinux/shimx64.efi",
    "/EFI/opensuse/shim.efi",
    "/EFI/BOOT/bootx64.efi",
)

#: ‏#345: הזנב המשותף לשני מסכי העצירה — מה שרץ אחרי שכבר אין מה להעלות.
#:
#: כאן ישב `halt`, והוא סתר את המשפט שמעליו. ‏`halt` אינו "עצור והשאר את
#: המסך": על EFI הוא ‏ResetSystem עם Shutdown, ועל i386-pc הוא כיבוי דרך
#: ‏APM/ACPI — בשתי הפלטפורמות המכונה **נכבית**, והדקה שהמפעיל ראה היא
#: ה-`sleep` שלפניו. המסך הזה קיים בדיוק כדי שטכנאי יקרא אותו, ומכונה
#: שנכבית לפני שהוא מגיע נראית לו כתקלת חשמל ולא ככישלון אתחול.
#:
#: הלולאה בנויה מ-`[` ומ-`sleep` בלבד — שתי פקודות שכבר מוכחות בקובץ
#: הזה. במכוון לא `while true`: ‏`true` הוא מודול נפרד ב-GRUB, וקריאה
#: לפקודה שלא נטענה מדפיסה שגיאה על מסך שכל תפקידו להיקרא.
#:
#: ‏`--interruptible` נשאר כדי שלחיצת מקש תיקלט (ב-`sleep` רגיל GRUB
#: אינו קורא מהמקלדת כלל, והמסך נראה תקוע). היא אינה יציאה: הלולאה
#: חוזרת אל ה-`sleep` בכל מקרה, ו-`stay_on` נקבע פעם אחת ולא נוגעים בו.
_STAY_POWERED_ON = """    echo "Contact IT. This computer will stay powered on."
    set stay_on=y
    while [ "$stay_on" = "y" ]; do
        sleep --interruptible 60
    done"""

# חיפוש מערכת ההפעלה המקומית. ה-ESP הוא FAT ולכן fat+part_gpt מספיקים;
# מכוון: לא טוענים ntfs, ואז search מדלג על מחיצות הנתונים ומסיים מהר.
# מחשב לינוקס שלא נמצא לו bootloader הוא כשל שקט שקשה לאבחן בשטח, ולכן
# הנתיבים הם רשימה אחת שמנסים אותה עד הסוף — לא if אחד ל-Windows.
# אם אין מערכת בכלל — עוצרים עם הודעה במקום exit, כי exit מחזיר לקושחה
# ש-PXE ראשון בסדר האתחול שלה, וזה ייצור לולאה אינסופית.
#
# ‏#61: "נמצא ונדחה" אינו "לא נמצא". על הלנובו (Secure Boot) ‏bootmgfw.efi
# נמצא, ‏chainloader נדחה בידי shim, והמסך אמר "No operating system found"
# — שני מצבים שקופלו לאחד (עיקרון 5). ‏`boot` רץ רק אם chainloader
# הצליח (ראיה חיובית); הנתיב הראשון שנמצא-ונדחה נשמר ב-chain_refused,
# והמסך הסופי אומר מה נמצא ולמה לא עלה. ‏Secure Boot מוזכר רק כשיש
# ראיה: ‏GRUB מייצא shim_lock=y בדיוק כשהוא רושם את מאמת ה-shim, כלומר
# כשכל chainloader עובר דרכו (grub-core/kern/efi/sb.c). בלי הראיה לא
# מנחשים. את שורת השגיאה המדויקת GRUB עצמו מדפיס לפני המסך הזה
# (‏grub_script_execute_cmdline קורא ל-grub_print_error גם בתוך if).
#
# ‏#416: "אין דיסק" ו"יש דיסק ואין עליו מערכת" הם שני מצבים (עיקרון 5),
# והמסך קיפל אותם לאחד: מחשב הבנייה (05/09) הדפיס "No operating system
# found on the local disk" על דיסק שהקושחה כלל לא ראתה, וההודעה שלחה
# לפרק אימג' תקין. אחרי הלולאה שואלים את GRUB מה הוא **רואה** (5א):
# ‏`probe --driver (hd0)` מצליח בדיוק כשיש התקן דיסק — על EFI ‏(efidisk)
# ועל i386-pc ‏(biosdisk) — ונכשל ב-`no such device` כשאין, ו-`ls` מדפיס
# את רשימת ההתקנים כדי שהטכנאי יקרא אותה ולא ינחש. שני המודולים חלק
# מהתמונה החתומה של דביאן (build-efi-images), כמו search_fs_file.
#
# והענף הזה גם מדווח לשרת (`imagectl_trace local-*`, ‏boot/trace.py):
# עד #416 ערך הדיסק המקומי היה היחיד בלי פירורים — והוא זה שנכשל.
# הפירורים נפלטים רק בקובץ שיש בו את הפונקציה (‏`traced`): בקובץ
# הקבוע שעל ה-TFTP השרת ממילא שקט, ובקובץ של מכונה לא רשומה אין מה
# לרשום (עיקרון 1).


def _trace_line(traced: bool, step: str, indent: int = 4) -> str:
    """שורת פירור אחת, או כלום. עומדת לבדה בשורה — כמו בערך ImageCtl,
    כישלון שלה אינו מפיל את המסלול."""
    return f"\n{' ' * indent}{grub_call(step)}" if traced else ""


def _func_chain_local(traced: bool) -> str:
    return """function try_chain {
    unset espdev
    search --no-floppy --file --set=espdev "$1"
    if [ -n "$espdev" ]; then
        if [ -z "$local_found" ]; then
            set local_found=$1""" + _trace_line(traced, "local-search-ok", 12) + """
        fi
        set root=$espdev
        if chainloader "$1"; then
            boot
        else
            if [ -z "$chain_refused" ]; then set chain_refused=$1; fi
        fi
    fi
}

function chain_local {
    insmod part_gpt
    insmod fat
    insmod chain
    insmod search_fs_file
    insmod probe
    unset chain_refused
    unset local_found""" + _trace_line(traced, "local-entry") + """

    for path in """ + " ".join(LOCAL_BOOT_PATHS) + """; do
        try_chain "$path"
    done

    echo ""
    if [ -n "$chain_refused" ]; then""" + _trace_line(traced, "local-chain-refused", 8) + """
        echo "A boot loader WAS found on the local disk:"
        echo "  $chain_refused"
        echo "but this menu was refused permission to start it (see the error above)."
        if [ "$shim_lock" = "y" ]; then
            echo "Why: Secure Boot is on. Every chainload from this menu is checked"
            echo "by shim, and shim did not accept this file's signature."
            echo "The disk and the operating system are most likely intact."
            echo "To start it now: power off, then boot from the disk directly"
            echo "(firmware boot menu, usually F12), bypassing PXE."
        else
            echo "Why: unknown. GRUB does not report a shim lock, so this is"
            echo "not a Secure Boot refusal. Read the error above."
        fi
    else
        unset local_disk
        probe --set=local_disk --driver (hd0)
        if [ -n "$local_disk" ]; then""" + _trace_line(traced, "local-search-empty", 12) + """
            echo "No operating system found on the local disk."
            echo "A disk IS present (GRUB opened (hd0) via $local_disk), but none of"
            echo "the boot loader paths this menu knows exists on any of its partitions."
            echo "The disk is blank, or holds a system this menu does not know how to start."
        else""" + _trace_line(traced, "local-no-disk", 12) + """
            echo "No disk device found."
            echo "GRUB sees no (hd0) at all - this is not an empty disk, it is no disk."
            echo "Check the drive, its cable and power, and whether the firmware lists it."
        fi
        echo "Devices GRUB can see:"
        ls
    fi
""" + _STAY_POWERED_ON + """
}"""


_FUNC_CHAIN_LOCAL = _func_chain_local(traced=False)

#: ‏#323: השומר שדרכו עוברת **כל** נפילה לדיסק המקומי.
#:
#: הקובץ הסטטי שעל ה-TFTP רץ לפני שהמכונה שאלה על ה-MAC שלה, וכשהשרת
#: שקט אין ממי לשאול — ולכן הוא אינו יכול לדעת שמולו מחשב שיכפול. מה
#: שכן זמין לו הוא `$grub_platform`: מי שעלה עם ליבת ה-i386-pc הוא
#: מחשב Legacy BIOS, כי `server/dhcp.py` מוסר את `grub/i386-pc/core.0`
#: אך ורק ל-`client-arch 0`, וברשת הזאת מחשבי השיכפול הם היחידים שאין
#: להם UEFI (#38, אפיון סעיף 4). זו ראיה חיובית, לא ניחוש.
#:
#: ‏#391: הראיה עונה על "איזו פלטפורמה", ומשתמשים בה כתשובה ל"איזה
#: תפקיד". שתי השאלות מתלכדות **רק בגלל דרישת רכש** — מחשב שיכפול =
#: ‏Legacy BIOS (אפיון סעיף 4) — ולא בגלל תכונה של הקוד. מה נשבר
#: בלעדיה: מחשב שיכפול UEFI שהשרת שקט מולו נכנס ל-`chain_local`, וזה
#: סורק את **כל** הכוננים המחוברים — כלומר את מגירות הסחורה — ומעלה
#: את ה-ESP הראשון שנמצא בהן. הלקוח היה מקבל דיסק שווינדוס כבר עלה
#: ממנו פעם (SID, דרייברים, hiberfil). שום טסט לא היה נופל, כי הקוד
#: נכון לפלטפורמה; מי שקונה מחשב שיכפול UEFI מבטל את ההגנה בשקט.
#:
#: למחשב שיכפול אין "אתחול רגיל": מה שמחובר אליו הן מגירות הסחורה,
#: וחלקן נושאות ESP של Windows. עיקרון 1 שולח מצב לא ברור לדיסק
#: המקומי — ולמכונה הזאת אין כזה, ולכן ברירת המחדל הבטוחה שלה היא
#: **לא לעשות כלום**.
#:
#: וגם בלי הסחורה: כל נתיב ב-LOCAL_BOOT_PATHS הוא בינארי EFI. על
#: i386-pc ‏`chain_local` לא היה מעלה אף אחד מהם ממילא — הוא היה סורק
#: את כל הכוננים המחוברים וקופץ אל תוך PE שהקושחה אינה יודעת להריץ.
_FUNC_TRY_LOCAL = """function try_local {
    if [ "$grub_platform" = "efi" ]; then
        echo "Booting from the local disk."
        chain_local
    fi

    echo ""
    echo "This machine booted in Legacy BIOS mode, which on this network"
    echo "means a cloning machine. It has no local system to start, and the"
    echo "drives attached to it are the payload. This menu will not start them."
    echo "Only the BIOS boot order keeps them from booting: a cloning machine"
    echo "must boot from the network BEFORE any disk, and a dead CMOS battery"
    echo "resets that order on every power-up."
    echo "Nothing will be started."
    echo "Fix the server or the network, then power-cycle this machine."
""" + _STAY_POWERED_ON + """
}"""


def _entry_local() -> str:
    return """menuentry "Boot from local disk" --id local {
    try_local
}"""


def _retry_load(command: str, label: str, *, fallback_local: bool) -> str:
    """GRUB 2.12-compatible retry loop for one HTTP boot payload."""
    failure = """        try_local""" if fallback_local else (
        """        echo "This Legacy BIOS cloning machine has no local system to start."
        echo "Nothing will be started. Fix the server or network, then power-cycle it."
""" + _STAY_POWERED_ON)
    variable = f"imagectl_{label}_loaded"
    return f"""    unset {variable}
    for attempt in 1 2 3; do
        echo "Loading {label} over HTTP (attempt $attempt of 3)..."
        if {command}
        then
            set {variable}=y
            break
        fi
    done
    if [ -z "${variable}" ]; then
        echo "ImageSrv: {label} could not be loaded after 3 attempts."
        echo "Falling back after a short delay."
        sleep --interruptible 3
{failure}
    fi"""


def _entry_agent(config: GrubConfig, *, recovery: bool, gui: bool = False,
                 fallback_local: bool = True) -> str:
    """ערך ה-ImageCtl. יש בדיוק אחד כזה בכל קובץ, ולכן שמו פשוט (#144).

    השם "ImageCtl - recovery / imaging" היה מובן רק כשהוא הופיע ליד
    "ImageCtl": בלעדיו הוא רק שואל את המפעיל שאלה שהוא לא אמור לענות
    עליה. ‏recovery משנה את שורת הפקודה, לא את מה שכתוב על המסך.

    ‏gui (#32) משנה **רק** את שורת ה-initrd. הוא לא נכנס לשורת הפקודה של
    הקרנל: הסוכן מזהה את הקיוסק לפי מה שארוז בו (`/usr/bin/imagectl-kiosk`
    קיים או לא), ופרמטר קרנל היה ממציא ממשק שני שיכול לסתור את הראשון
    (עיקרון 2).
    """
    cmdline = _kernel_cmdline(config, recovery=recovery)
    host = config.grub_host
    # ‏`and` ולא ברירת מחדל: אם אין נתיב גרפי בתצורה — כלומר הקובץ אינו
    # קיים על השרת — גם תפקיד עם מסך נופל לטקסטואלי. ראו gui_initrd_path.
    initrd = (config.gui_initrd_path if gui and config.gui_initrd_path
              else config.initrd_path)
    linux_load = _retry_load(
        f"linux (http,{host}){config.kernel_path} {cmdline}",
        "kernel",
        fallback_local=fallback_local,
    )
    initrd_load = _retry_load(
        f"initrd (http,{host}){initrd}",
        "initrd",
        fallback_local=fallback_local,
    )
    # ‏#400: חמישה פירורים, כל אחד בשורה משל עצמו. הם **בין** הפקודות
    # ולא עוטפים אותן: פקודה שנכשלת בתוך menuentry ממשיכה לבאה אחריה,
    # ולכן שרת שאינו עונה על ‎/boot/step מייצר שגיאה על המסך — ולא
    # מכונה שאינה עולה. מה שקורה כאן הוא בדיוק המקטע שהיה חור שחור:
    # ‏entry (נכנסנו) · http-ok (המודול נטען) · pre-linux (לפני הקרנל) ·
    # ‏pre-initrd (הקרנל נטען) · pre-boot (ה-initramfs נטען).
    #
    # ‏#503/#1405: pre-initrd/pre-boot נשלחים רק אחרי שלולאת הטעינה
    # הצליחה. כשל סופי אינו ממשיך לפירור: הוא נכנס למסלול המילוט הבטוח,
    # שאינו חוזר. הפירור עצמו נשאר פקודה עצמאית, ולכן כשל של ‎/boot/step
    # אינו יכול למנוע את פקודת הטעינה שאחריו.
    return f"""menuentry "ImageSrv" --id imagectl {{
    {grub_call("entry")}
    echo "Loading ImageSrv..."
    insmod http
    {grub_call("http-ok")}
    if [ "$grub_platform" = "efi" ]; then insmod efinet; fi
    {grub_call("pre-linux")}
{linux_load}
    {grub_call("pre-initrd")}
{initrd_load}
    {grub_call("pre-boot")}
}}"""


def _kernel_cmdline(config: GrubConfig, *, recovery: bool) -> str:
    """שורת הפקודה של הקרנל.

    מכוון: לא מועברים כאן role, task, session, image_id או allowed_images.
    כל אלה מגיעים לסוכן מתשובת ה-hello (ממשק 3). אם היינו מעתיקים אותם
    לשורת הפקודה היינו יוצרים ממשק שני, לא מתועד, שיכול לסתור את הראשון.
    מה שכן עובר כאן הוא רק מה שהסוכן לא יכול לדעת לפני שהוא מדבר עם השרת:
    לאן לפנות, ואם המפעיל ביקש שחזור בלחיצת ESC.
    """
    parts = [
        "ip=dhcp",
        f"imagectl.server={config.base}",
        "console=tty0",
        "quiet",
        "loglevel=3",
    ]
    if recovery:
        parts.append("imagectl.mode=recovery")
    parts.extend(config.extra_cmdline)
    return " ".join(_ascii(p) for p in parts)


def _ascii(text: str) -> str:
    """לערך בודד שמוטמע בקובץ: מסיר תווים שאינם ASCII וגם מנטרל את
    התווים שיכולים לשבור את התחביר או להזריק פקודה — מירכאות, לוכסן
    הפוך, וסימן הדולר שגורם ל-GRUB להרחיב משתנה."""
    cleaned = _ASCII_SAFE.sub("", str(text))
    return cleaned.replace('"', "'").replace("\\", "/").replace("$", "")


def _strip_non_ascii(text: str) -> str:
    """לקובץ שלם: מסיר רק תווים שאינם ASCII, ומשאיר את התחביר של GRUB
    ‏(‎$‎ ומירכאות) שלם."""
    return _ASCII_SAFE_MULTILINE.sub("", text)


# ---------------------------------------------------------------------------
# הקובץ הסטטי שיושב על ה-TFTP
# ---------------------------------------------------------------------------


def render_bootstrap(config: GrubConfig) -> str:
    """מייצר את grub.cfg הקבוע שה-GRUB החתום טוען מה-TFTP.

    זה הקובץ היחיד בשרשרת שיושב על דיסק ולא נוצר בכל אתחול. תפקידו אחד:
    למשוך את התפריט הדינמי. הוא מיוצר מכאן ולא נכתב ביד כדי ש-chain_local
    יהיה מוגדר במקום אחד בלבד ולא יסטה בין שני הקבצים.
    """
    # ‏netloc בלי פורט = 80, כמו בכל URL של http.
    port = urlsplit(f"//{config.grub_host}").port or 80
    return _strip_non_ascii(f"""# ImageCtl bootstrap - generated by boot/grub_menu.py:render_bootstrap().
# Lives on the TFTP root as grub/grub.cfg. Regenerate, do not hand-edit.

set pager=0
set timeout=0
set timeout_style=hidden

# efinet exists only on UEFI; the i386-pc netboot core (the cloning
# machines, #38) has its driver built in, and insmod-ing a module that
# does not exist prints an error on the boot screen.
if [ "$grub_platform" = "efi" ]; then insmod efinet; fi
insmod http
insmod tftp

{_FUNC_CHAIN_LOCAL}

{_FUNC_TRY_LOCAL}

# GRUB fills net_default_mac from the interface that PXE-booted, already in
# lowercase-with-colons form -- the canonical format from the interface spec.
if [ -z "$net_default_mac" ]; then
    echo "ImageSrv: no network interface reported."
    sleep --interruptible 3
    try_local
fi

# Deployment address. net_default_server -- the DHCP or PXE-proxy server that
# actually answered this station -- comes first, because it is the only address
# that is right on every network: a station booted by a proxy on a foreign VLAN
# (spec scenario 3) often cannot reach the deployment VLAN address at all. The
# port is not in DHCP, so it comes from the configured address.
# The explicit address stays as a fallback, for debugging and for the case
# where next-server arrives empty or points at a machine without ImageCtl.
set imagectl_fallback={config.grub_host}
if [ -n "$net_default_server" ]; then
    set imagectl_server=${{net_default_server}}:{port}
else
    set imagectl_server=$imagectl_fallback
fi

# Three attempts: GRUB's EFI http stack sometimes fails its very first
# connection on a cold boot while TFTP already works (#31). A retry a
# second later succeeds; only a server that is really down falls through.
# Each attempt also tries the fallback, unless it is the same address.
for attempt in 1 2 3; do
    configfile (http,$imagectl_server){_menu_path()}?mac=$net_default_mac
    if [ "$imagectl_server" != "$imagectl_fallback" ]; then
        configfile (http,$imagectl_fallback){_menu_path()}?mac=$net_default_mac
    fi
    sleep 1
done

# Reached only if every fetch failed -- server down, cable out, DHCP wrong.
# try_local, not chain_local: this file runs before the machine has asked
# about its own MAC, so it cannot know it is a cloning machine -- and with
# the server down there is nobody to ask. $grub_platform is the one thing
# it does know (#323).
echo "ImageSrv: server $imagectl_server unreachable."
sleep --interruptible 3
try_local
""")


def _menu_path() -> str:
    return "/boot/menu"
