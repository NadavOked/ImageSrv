#!/usr/bin/env python3
"""deploy/security-pool-lock.py — קורא את deploy/security-pool.json (#1411).

הקובץ הוא החוליה בין מאגר עדכוני האבטחה של Debian לבין הגרסה החתומה: הוא
יושב **בעץ של התג**, ולכן חתימת התג (#1277) חותמת גם על הגיבוב שבו. המאגר
עצמו (קובץ tar) מוצמד לגרסה ב-GitHub Release, ומי שמוריד אותו משווה לגיבוב
הזה — מאגר שאינו תואם נדחה.

  python3 -I deploy/security-pool-lock.py deploy/security-pool.json

פלט (שורה אחת, מופרדת ברווחים): ‏`<asset> <sha256> <size> <date>`.
כל שדה נבדק בצורתו; קובץ שלא נקרא או שדה שאינו תקין = יציאה 2 עם הסיבה
ב-stderr. "לא הצלחנו לקרוא" אינו "אין מאגר" (עיקרון 5).

נקרא משני הצדדים, כדי שתהיה רק הגדרה אחת לצורת הקובץ:
‏tools/git/release-sign.sh (לפני הפרסום) ו-deploy/security-upgrade.sh (בשרת).
"""

from __future__ import annotations

import json
import re
import sys
from datetime import date

ASSET = re.compile(r"^imagesrv-security-pool-\d{4}-\d{2}-\d{2}\.tar$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


def parse(text: str) -> tuple[str, str, int, str]:
    doc = json.loads(text)
    if not isinstance(doc, dict):
        raise ValueError("not a JSON object")
    asset, sha, size, day = doc.get("asset"), doc.get("sha256"), doc.get("size"), doc.get("date")
    if not isinstance(asset, str) or not ASSET.match(asset):
        raise ValueError(f"asset: {asset!r}")
    if not isinstance(sha, str) or not SHA256.match(sha):
        raise ValueError(f"sha256: {sha!r}")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise ValueError(f"size: {size!r}")
    if not isinstance(day, str):
        raise ValueError(f"date: {day!r}")
    date.fromisoformat(day)                     # YYYY-MM-DD, or ValueError
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) or day not in asset:
        raise ValueError(f"date {day!r} does not match asset {asset!r}")
    return asset, sha, size, day


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: security-pool-lock.py <security-pool.json>", file=sys.stderr)
        return 2
    try:
        with open(argv[1], encoding="utf-8") as handle:
            asset, sha, size, day = parse(handle.read())
    except (OSError, ValueError) as exc:     # JSONDecodeError is a ValueError
        print(f"security-pool.json is not valid: {exc}", file=sys.stderr)
        return 2
    print(f"{asset} {sha} {size} {day}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
