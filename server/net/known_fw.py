"""מחשבים רשומים בחומת האש של כרטיס ההפצה (הכרעת נדב 14/11, #1079/#2485).

"את רשת ההפצה אני מגן עליה בווילן נפרד, והשרת צריך למנוע ממחשבים לא רשומים
לעבור דרך השרת." חבילה של מחשב לא רשום שמגיעה לשירות שיכפול בשכבת UDP —
המשוב של המקלטים לשולח (פורטי המולטיקאסט) — נזרקת ב-nftables
עצמו, לפני שהיא מגיעה לתהליך (`deploy/nftables-rules.sh`: `ether saddr
@known_macs`). HTTP נאכף בשער של אפליקציית הסוכן (`server/agent/deploy_gate.py`).

הקבוצה `known_macs` היא נגזרת של טבלת המכונות, בדיוק כמו קובץ ה-hostsfile של
dnsmasq (#141, `dhcp.render_known_macs`), ונכתבת באותם רגעים: בעליית השרת ובכל
שינוי ברשימת המכונות. מכונה שנרשמה עכשיו נפתחת מיד, בלי אתחול של השרת.

**הקובץ, ולא רק פקודה:** `/etc/nftables.conf` כולל את `INCLUDE_GLOB`, ולכן כל
טעינה מחדש של חומת האש (שינוי כרטיס הפצה מהקונסולה, #705, אתחול) קוראת את
הרשימה שוב. `flush ruleset` שבראש הקובץ מרוקן את הקבוצה, ובלי ה-include המחשבים
הרשומים היו נשארים בחוץ עד השינוי הבא ברשימה.

**כשל = גלוי** (עיקרון 5): `apply` מחזיר את הסיבה, והקורא כותב אותה ליומן
(`known_macs_firewall_failed`). הכיוון הוא סגירה: קבוצה שלא נטענה משאירה בחוץ
גם מחשב רשום, ושידור אליו נכשל בשמו — לא נפתח לכולם בשקט.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from server.core.atomicfile import write_atomic
from server.net.dhcp import _guard_macs

log = logging.getLogger("imagesrv.known_fw")

#: הקבוצה, כפי ש-`deploy/nftables-rules.sh` מגדיר אותה.
SET = "inet imagesrv known_macs"
#: הקובץ שהשרת כותב. `nftables.conf` כולל את התיקייה בתבנית (`INCLUDE_GLOB`):
#: תבנית שאינה תואמת אף קובץ אינה שגיאה, ולכן התקנה טרייה נטענת גם לפניו.
CONF = "/etc/imagesrv/nft.d/known-macs.nft"
INCLUDE_GLOB = "/etc/imagesrv/nft.d/*.nft"


def render(macs: list[str]) -> str:
    """הקובץ: ריקון הקבוצה, ואז כל MAC רשום. רשימה ריקה = קבוצה ריקה (אף מחשב
    אינו רשום — אף אחד אינו מגיע לשירותי השיכפול), וזה קובץ תקין.

    MAC שאינו קנוני — סירוב (`ValueError`), כמו `render_known_macs`: השורה נקראת
    בידי nft כ-root, ושורה שבורה הייתה מפילה את טעינת חומת האש כולה."""
    _guard_macs(macs)
    lines = ["# ImageSrv -- registered machines on the deploy NIC (Nadav 14/11, #1079/#2485).",
             "# Generated from the machines table: the next add/edit/delete rewrites it.",
             f"flush set {SET}"]
    if macs:
        lines.append(f"add element {SET} {{ {', '.join(macs)} }}")
    return "\n".join(lines) + "\n"


def apply(macs: list[str], conf_path: str | Path = CONF, run=subprocess.run) -> str | None:
    """כותב את הקובץ וטוען אותו (`nft -f`). None = נטען; אחרת הסיבה, בעברית.

    nft טוען קובץ באטומיות (טרנזקציה אחת): ריקון והוספה, או כלום. קבוצה שאינה
    קיימת (חומת אש של גרסה קודמת, או שאינה של ImageSrv) — כשל בשמו."""
    try:
        text = render(macs)
    except ValueError as exc:
        return str(exc)
    path = Path(conf_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(path, text)
    except OSError as exc:
        log.warning("known_fw: writing %s failed: %s", path, exc)
        return f"כתיבת {path} נכשלה — הפרטים ביומן השרת"
    try:
        done = run(["nft", "-f", str(path)], capture_output=True, text=True,
                   timeout=20, check=False, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("known_fw: nft could not run: %s", exc)
        return "nft לא רץ — רשימת המחשבים הרשומים לא נטענה לחומת האש"
    if done.returncode != 0:
        detail = (done.stderr or done.stdout or "").strip()[:300]
        log.warning("known_fw: nft -f %s rc=%s: %s", path, done.returncode, detail)
        return (f"חומת האש לא קיבלה את רשימת המחשבים הרשומים (nft rc={done.returncode})"
                " — אולי חומת האש של גרסה קודמת, בלי הקבוצה known_macs; הפרטים ביומן השרת")
    return None
