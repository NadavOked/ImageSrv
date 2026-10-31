#!/usr/bin/env python3
"""deploy/apt-repo-consistent.py — המאגר המקומי מפרסם רק מה שיש בו (#1371).

‏`/var/lib/imagesrv/apt-repo` של שרת שהותקן מה-ISO לפני #1371 קיבל את
‏`dists/` של ה-ISO כולו ורק את `pool/*/imagesrv`: האינדקס מנה כ-500 חבילות של
ה-netinst (‏dbus-user-session ועוד) שהקבצים שלהן לא הועתקו, ו-apt
בחר בהן ונפל ב-"File not found" (‏13/10, שרת ת"א: ‏495 מתוך 1134).

הסקריפט מוריד מכל `dists/<suite>/<רכיב>/binary-*/Packages.gz` כל רשומה שה-
‏`Filename` שלה אינו קובץ במאגר, ומחשב מחדש את הגיבובים ב-`Release` לקבצים
שהשתנו — כך apt מוצא את החבילה במקור אחר (האינטרנט, כשחומת האש פותחת 443)
במקום לבחור קובץ שאינו קיים. מאגר עקבי אינו משתנה (אידמפוטנטי).

  python3 deploy/apt-repo-consistent.py /var/lib/imagesrv/apt-repo

‏0 = המאגר עקבי (כבר היה, או תוקן) — השורה האחרונה אומרת כמה. ‏1 = לא נבדק
(אין `dists`, אינדקס לא נקרא, ‏Release חסר): "לא הצלחנו לבדוק" אינו "עקבי".
נקרא מ-deploy/vmupdate-host.sh.
"""

from __future__ import annotations

import gzip
import hashlib
import os
import sys
from pathlib import Path

#: שמות הסעיפים ב-Release → hashlib. ‏make-pool.sh כותב MD5Sum ו-SHA256.
ALGOS = {"MD5Sum": "md5", "SHA1": "sha1", "SHA256": "sha256", "SHA512": "sha512"}


def _filename(stanza: str) -> str | None:
    for line in stanza.splitlines():
        if line.startswith("Filename:"):
            return line.split(":", 1)[1].strip()
    return None


def _write(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def fix_index(repo: Path, index: Path) -> tuple[int, int]:
    """(נשארו, הוסרו). אינדקס שאין בו מה להסיר אינו נכתב מחדש."""
    text = gzip.decompress(index.read_bytes()).decode("utf-8")
    keep, dropped = [], 0
    for stanza in (s.strip("\n") for s in text.split("\n\n")):
        if not stanza.strip():
            continue
        name = _filename(stanza)
        if name and (repo / name).is_file():
            keep.append(stanza)
        else:
            dropped += 1
    if dropped:
        body = ("\n\n".join(keep) + "\n").encode("utf-8") if keep else b""
        _write(index, gzip.compress(body, mtime=0))
    return len(keep), dropped


def rewrite_release(suite: Path, changed: set[str]) -> None:
    """הגיבוב והגודל של כל שורה ב-Release שמתייחסת לקובץ שהשתנה — גם לשם
    הפרוס (‏`…/Packages`, שתוכנו הוא ה-.gz אחרי פריסה; ‏make-pool.sh רושם את שניהם)."""
    release = suite / "Release"
    out, algo = [], None
    for line in release.read_text(encoding="utf-8").splitlines():
        if not line.startswith(" "):
            algo = ALGOS.get(line[:-1]) if line.endswith(":") else None
            out.append(line)
            continue
        parts = line.split()
        if algo is None or len(parts) != 3 or not ({parts[2], parts[2] + ".gz"} & changed):
            out.append(line)
            continue
        path = parts[2]
        data = ((suite / path).read_bytes() if path in changed
                else gzip.decompress((suite / (path + ".gz")).read_bytes()))
        out.append(f" {hashlib.new(algo, data).hexdigest()} {len(data):16d} {path}")
    _write(release, ("\n".join(out) + "\n").encode("utf-8"))
    # חתימה ישנה מעל Release חדש היא שגיאה, לא "לא חתום" (כמו make-pool.sh).
    for stale in ("Release.gpg", "InRelease"):
        (suite / stale).unlink(missing_ok=True)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: apt-repo-consistent.py <repo-dir>")
        return 1
    repo = Path(argv[1])
    suites = sorted(p for p in (repo / "dists").glob("*") if p.is_dir())
    if not suites:
        print(f"apt-repo: אין {repo}/dists — המאגר המקומי לא נבדק")
        return 1
    kept = dropped = 0
    for suite in suites:
        changed: set[str] = set()
        for index in sorted(suite.glob("*/binary-*/Packages.gz")):
            try:
                k, d = fix_index(repo, index)
            except (OSError, EOFError, UnicodeDecodeError, gzip.BadGzipFile) as exc:
                print(f"apt-repo: {index} לא נקרא ({exc}) — המאגר המקומי לא נבדק")
                return 1
            kept, dropped = kept + k, dropped + d
            if d:
                rel = index.relative_to(suite).as_posix()
                changed.add(rel)
                print(f"apt-repo: {d} רשומות בלי קובץ הוסרו מ-{suite.name}/{rel}")
        if changed:
            try:
                rewrite_release(suite, changed)
            except OSError as exc:
                print(f"apt-repo: {suite}/Release לא נכתב מחדש ({exc})")
                return 1
    if kept + dropped == 0:
        print(f"apt-repo: אין אינדקס חבילות ב-{repo}/dists — המאגר המקומי לא נבדק")
        return 1
    print(f"apt-repo: עקבי — {kept} חבילות, לכולן קובץ במאגר"
          + (f" ({dropped} רשומות בלי קובץ הוסרו)" if dropped else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
