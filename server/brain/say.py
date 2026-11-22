"""#3320 (R1): שורה אחת בעברית לכל החלטה של המוח, בשביל הקונסולה -- "דיסק 7 → שידור חדש: 4 ממתינים".

הצורה: ``<על מי> → <מה הוחלט>: <הסיבה>``, ובסוף "(צל: רק נרשם)" כשההחלטה לא פעלה. **הסיבה היא של הכלל עצמו** (כבר
בעברית, ב-``brain_decisions.reason``) ואינה מנוסחת כאן מחדש: השורה רק אומרת על מי ומה, במילים ולא בקודים.
פעולה, פסק או ישות שאין להם תווית נשארים בשם שלהם (``FOO``) -- לא "לא ידוע", ולא שורה ריקה.

טהור: מ-dict של `memory._decision` לשורה. הקונסולה מקבלת אותה כשדה ``line_he`` (`docs/interfaces.md`).
"""

from __future__ import annotations

KIND_HE = {"machine": "משכפל", "disk": "דיסק", "slot": "חריץ", "model": "דגם", "network": "רשת", "round": "סבב",
           "image": "אימג'", "settings": "הגדרה"}
#: הפעולות שהמוח רושם היום (`brain_decisions.action`), כל הכללים.
ACTION_HE = {
    "ROUND_GATE": "שער לפני פתיחת הסבב",
    "ROUND_OPEN": "פסק פתיחת הסבב",   # B70 (#3662): פסק אחד מכל השערים
    "LIVENESS": "חיות",
    "RX_LIMIT": "מה הגביל את הקצב במקלט",
    "FAIL_WAVE": "הכשלת הגל", "FAIL_DISKS": "הכשלת דיסקים", "EXPIRE_LEDGER": "מחיקת יומן הכתיבה",
    "PARTITION": "תחילת מחיצה", "HTTP_BUDGET": "תקציב ההורדות", "FILL_SOURCE": "השלמת מגירה",
    "FILL_BUDGET": "תקציב ההשלמה", "INTEGRITY": "אימות הדיסק", "FAIL_CAUSE": "סיבת הכשל",
    "LINK_SLOW": "קישור איטי", "ROUND_END": "סוף הסבב", "WAKE": "השכמה (WoL)",
    "WAVE_START": "קצב פתיחת הגל", "WAVE_RATE_LEARN": "קצב נלמד לגל",
    "WAVE_WRITE_RATE": "קצב הכתיבה הנדרש מהדיסקים",   # #3506
    "HTTP_CONC": "מקביליות ההורדות", "RESUME_OR_RESTART": "להמשיך או להתחיל מחדש",
    "FILL_PEER": "השלמה ממשכפל שכן",   # B29
    "PEER_BANNED": "הוצאת משכפל שכן מההשלמה",   # B29
    "SENSE": "חישת רשת",   # #3064 N4/N6/N8: `l2sense.record`, the network senses
    "REPEAT_FAIL": "כשל חוזר של דיסק",   # B61 (#3433)
    "WAVE_NETCAP": "תקרת רשת נלמדת",
    "WAVE_SERVER_CAP": "תקרת הקריאה של דיסק הספרייה",   # #3464
    "CPU_GATE": "כוח העיבוד של השרת", "SERVICES_FOR_ROUND": "שירותי השרת לסבב",   # #3064 (K12, K13)
    "TIME_FORECAST": "חיזוי זמן הגל",
    "BUS_BANNER": "אפיק כרטיס הרשת",
    "FAULT_WHO": "מי אשם בכשל",   # B50
    "CAPTURE_COMPRESSION": "רמת הדחיסה בקליטה", "CAPTURE_SPACE": "מקום בשרת לקליטה",   # #3669 (B6, B7)
    "DIRECT_SILENCE": "המתנה לשרת ששותק בהפצה ישירה",   # #3669 (B8)
    "SEND_LIMIT": "מה הגביל את הקצב", "NET_LOSS": "אבדן ברשת ההפצה",   # B58
    "FEC_POLICY": "שיטת תיקון חבילות (הצעה)",   # K-FEC (#3514)
    "PRECHECK": "אזהרות לפני התחלת הגל",   # B72 (#3662)
    "CABLE_TEST": "בדיקת כבל חיה", "LINK_BANNER": "באנר קישור איטי",   # #3663 (K-CABLE, K-NICBANNER)
    "SLOW_DISK": "דיסק איטי לפני הסבב",   # B62 (#3268)
    "RESTART_GATE": "הפעלה מחדש או עדכון של השרת",   # #3668 (S50, T2)
    "MARK_LOST": "סימון משכפל כאבוד", "STREAM_STALL": "עצירת הזרם", "REWAKE": "WoL חוזר לגל פתוח",   # #3657 (K-LOST, K-STALL, K-REWAKE)
    "SILENT_PULL": "משיכה שקטה", "SILENT_TASK": "משימה שקטה",   # #3657 (K-SILENT)
    "QUERIER": "שאילתת IGMP של השרת (querier)",   # #3663 (K-QUERIER, S49)
    "ASK": "שאלה למשתמש",   # #3654 (K14)
    "DISK_COLOR": "צבע הדיסק",   # K3 (#3666): הסיווג שהקונסולה והגואי מציגים
    "FILL_LIMITS": "גבולות ההשלמה",   # #3659 (K-FILL, A27)
    "IDLE_CEILINGS": "תקרות בדיקת הכתיבה במסך ההמתנה",   # #3672 (K-IDLE, A15)
    "DRAWER_TUNE": "בחירת הסוכן למגירות (פענוח, nice, O_DIRECT)",   # K17 (#3816)
}
VERDICT_HE = {
    "BLOCK": "חסימה",
    "ALIVE": "חי", "SUSPECT": "חשוד (שותק)", "STALLED": "העבודה לא זזה", "LOST": "אבד", "UNKNOWN": "לא ידוע", "FINISHED": "סיים",   # #3836
    "RETURNED": "חזר", "RECOVERED": "חזר משתיקה קצרה", "HOLD": "החזקה", "PROCEED": "בצע", "START": "להתחיל", "HOLD_PARTITION": "להמתין למשלים",
    "NIC_DROPS": "הכרטיס זרק חבילות", "RX_DROPS": "המקלט איבד חתיכות", "CPU_BOUND": "המעבד הגביל", "DISK_BOUND": "הדיסק הגביל",
    "STALLED_PASS": "המעבר נתקע", "NO_LIMIT_SEEN": "לא נראה גורם מגביל", "NO_LIMIT_PARTIAL": "אין אות, אבל לא הכול נמדד", "NOT_MEASURED": "לא נמדד",
    "PARK": "לשמור להשלמה", "CLOSE": "לסגור", "DEFER": "לדחות", "OPEN": "פתוח", "CUT": "הפחתה", "RAISE": "העלאה",
    "CLAMP": "הגבלה", "WARN": "אזהרה", "EXCLUDE": "החרגה", "AT_FLOOR": "על הרצפה: אין מה לחתוך",   # #3596
    "NOT_APPLICABLE": "לא רלוונטי: הזרם אינו בכרטיס השרת",   # #3838 (ג)
    "RESUME": "להמשיך", "RESTART": "להתחיל מחדש",
    "REPEAT_DRIVE": "נכשל שוב באותה סיבה: הכונן", "REPEAT_LINK": "נכשל שוב באותה סיבה: הכבל או המגירה",
    "REPEAT_NETWORK": "נכשל שוב באותה סיבה: הרשת", "REPEAT_SOFTWARE": "נכשל שוב באותה סיבה: הזרם או התוכנה",
    "REPEAT_UNCLASSIFIED": "נכשל שוב, בלי סיבה ידועה",
    "WOULD_CAP": "היה מוגבל", "WITHIN": "בתוך התקרה", "UNPROVEN": "לא הוכח",
    "CAP": "הוגבל לתקרה", "FITS": "בתוך התקרה",   # #3464
    "PROBE": "לפי בדיקת הכתיבה",   # #3506
    "RUN": "להריץ", "NOT_NOW": "לא עכשיו: סבב או משימה פעילים", "TESTED": "כבר נבדק באתחול הזה", "HEALTHY": "תקין", "SHOW": "להציג",   # #3663 (K-CABLE, K-NICBANNER; NONE, NOT_MEASURED are above)
    "WAIT": "להמתין", "UNDECIDED": "לא הוכרע", "READY": "מוכן", "BLOCKED": "חסום",   # #3064 (K12, K13)
    "PREDICT": "חיזוי", "UNPREDICTED": "אין חיזוי",
    "SLOT": "החריץ או הכבל", "DISK": "הדיסק", "LINK_NOW": "קו: כבל או מגע", "HOT": "חם בזמן הכתיבה",   # B50
    "SERVER_PRESSURE": "השרת לחוץ",
    "SLOW_CLONER": "משכפל איטי",
    "JUMP": "קפיצה באבדן",
    "OK": "תקין",
    "UNCHECKED": "לא נבדק (אין בסיס עדיין)",
    "SERVER_DISK": "לא נבדק (דיסק השרת)",
    "NET_GAP": "לא נבדק (ניתוק ברשת)",
    "RED": "אדום: איטי עכשיו או לאורך הגל הקודם", "INFO": "מידע: פיגר בעבר ותקין עכשיו", "NONE": "אין מה לומר",   # B62 (#3268)
    "FUTURE": "חותמת בעתיד (קפיצת שעון)", "UNMEASURED": "לא נמדד",   # #3657 (K-LOST)
    "STREAM_STALLED": "הזרם נעצר", "NOT_STALLED": "הזרם לא נעצר",   # #3657 (K-STALL; NOT_MEASURED is above)
    "SEND": "לשלוח", "FAIL": "להכשיל את הסבב", "CALIBRATE": "לכייל את שעון הגל",   # #3657 (K-REWAKE)
    "KEEP": "להמשיך", "FAIL_SILENT": "שתקה: להכשיל", "FAIL_REPORTED": "המכונה דיווחה כשל: להכשיל",   # #3657 (K-SILENT)
    "YIELD": "מוותר: יש querier אחר עם כתובת נמוכה יותר", "REFUSED": "לא שולח: כבוי או חסום",   # #3663 (K-QUERIER; SEND, WAIT, UNDECIDED are above)
    "ASKED": "נשאלה", "ANSWERED": "נענתה", "EXPIRED": "פקעה בלי תשובה", "CANCELLED": "בוטלה",   # #3654 (K14)
    "SET": "נקבעו",   # #3672 (K-IDLE; UNDECIDED is above)
    "DISK_RED": "אדום", "DISK_YELLOW": "צהוב", "DISK_ORANGE": "כתום", "DISK_GREEN": "ירוק", "DISK_NOT_CHECKED": "לא נבדק",   # K3/DISK_COLOR (#3666): מפתחות משלו -- "RED" הוא של B62 (#3268)
    "NEIGHBORS_THEN_HTTP": "שכנות קודם, ואז HTTP", "HTTP_ONLY": "HTTP בלבד",   # #3659 (K-FILL, A27)
}
#: פעולות שההחלטה שלהן **מוגשת** (הקונסולה והגואי מציגים אותה), גם כשהמתג ``shadow``: אין להן "(צל: רק נרשם)" (#3666).
SERVED = frozenset({"DISK_COLOR", "CABLE_TEST", "LINK_BANNER", "IDLE_CEILINGS"})   # #3663: the agent runs / the GUI draws what the brain said, in shadow too; #3672: the agent applies the idle ceilings, in shadow too
SHADOW_HE = "(צל: רק נרשם)"
#: #3657: כללי "המוח מחליט, השרת מבצע" -- הספים לא השתנו, ולכן ההחלטה מתבצעת גם בצל. "רק נרשם" על שורה כזו היה שקר.
EXECUTED = frozenset({"K-LOST", "K-STALL", "K-REWAKE", "K-SILENT", "K-QUERIER"})
EXECUTED_HE = "(צל: אותה החלטה כמו הקוד שהוחלף, והשרת ביצע אותה)"

# --- #3655 (S3, S4: K-WRITE, K-RATE-BOUNDS) ---------------------------------------------------------------------------------------
ACTION_HE.update({"WRITE_BASE": "הקצב שהדיסקים נדרשים לכתוב", "FEEDBACK_BOUNDS": "גבולות הבקר של השולח"})
VERDICT_HE.update({"PACE": "לפי הדיסק האיטי שנספר", "FLOOR": "הרצפה (אין בדיקה שנמדדה)", "SET": "נקבעו"})
EXECUTED |= frozenset({"K-WRITE", "K-RATE-BOUNDS"})

# --- #3655 (S34: K-FLOOR) ---------------------------------------------------------------------------------------------------
ACTION_HE.update({"WRITE_FLOOR": "הרצפה לכתיבה של הדיסק"})
VERDICT_HE.update({"FROM_SETTING": "לפי ההגדרה", "LEARNED": "נלמד מהגלים של הדיסק", "ESTIMATE": "הערכת פתיחה (אין גל שנמדד)", "REFUSED": "נדחה"})
EXECUTED |= frozenset({"K-FLOOR"})

# --- #3655 (S37: K-LEARN) ---------------------------------------------------------------------------------------------------
ACTION_HE.update({"LEARN_SETTING": "עדכון הגדרה נלמד", "LEARN_RUN": "סבב הלמידה"})
VERDICT_HE.update({"UNCHANGED": "ללא שינוי", "RUN_DONE": "הסבב למד", "RUN_SHORT": "הסבב לא למד (חסרים גלים עם ראיה)"})
EXECUTED |= frozenset({"K-LEARN"})

# --- #3659 (S10, S11, S12: K-REPAIR) ----------------------------------------------------------------------------------------
ACTION_HE.update({"REPAIR_PLAN": "תוכנית ההשלמה אחרי המעבר"})
VERDICT_HE.update({"PLAN": "סבב השלמה מתוכנן", "DONE": "אין מה להשלים", "EXHAUSTED": "המכסה נגמרה: דיסק נכשל"})
EXECUTED |= frozenset({"K-REPAIR"})
ACTION_HE.update({"REPAIR_ANSWER": "מה עונים למשכפל שדיווח על מעבר"})   # #3659 (S14)
VERDICT_HE.update({"ANSWER_RECEIVE": "להאזין למעבר", "ANSWER_FETCH": "להוריד בעצמו", "ANSWER_DONE": "שלם", "ANSWER_WAIT": "להמתין"})

# --- #3977 (K8: what each cloner made durable, and the rate a cloner fell at) -------------------------------------------------
ACTION_HE.update({"WAVE_GOODPUT": "מה שהמשכפלים כתבו מול הקצב", "CLONER_DROP": "משכפל שאבד במעבר ראשון",
                  "WAVE_UNSAFE_RATE": "קצב שמשכפל נפל בו"})
VERDICT_HE.update({"PEAK": "השיא: קצב גבוה יותר לא היה מהיר יותר", "CLIMB": "ממשיכים לטפס", "DROPPED": "אבד",
                   "WALL": "לא מטפסים לקצב הזה", "NO_WALL": "אין חומה"})


def line_he(d: dict) -> str:
    kind, key = d.get("entity_kind"), d.get("entity_key")
    who = f"{KIND_HE.get(kind, kind)} {key}" if kind and key else "המוח (כללי)"
    action = ACTION_HE.get(d.get("action") or "", d.get("action") or "?")
    verdict = VERDICT_HE.get(d.get("verdict") or "", d.get("verdict") or "?")
    reason = (d.get("reason") or "").strip()
    # #3666: DISK_COLOR מוגשת (הקונסולה והגואי מציגים אותה) ולכן אין לה "(צל: רק נרשם)"; #3657: כללי "מוחלט ומבוצע" אומרים זאת
    tail = "" if d.get("mode") != "shadow" or d.get("action") in SERVED else f" {EXECUTED_HE if d.get('rule') in EXECUTED else SHADOW_HE}"
    return f"{who} → {action}: {verdict}" + (f". {reason}" if reason else "") + tail
