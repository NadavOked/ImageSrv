"""תחליף צר ל-`jq` לתחנת פיתוח בלי jq (ווינדוס) — **לבדיקות בלבד**.

`tests/test_directsend.py` שם אותו ב-PATH בשם `jq` רק כש-`shutil.which("jq")`
ריק. הוא מבין בדיוק את הביטויים שמסלול ההפצה הישירה של הסוכן מריץ:

- `json_get` (jsonq.sh): נתיב פשוט `.a.b.c` — null/חסר מודפס `null`,
  מחרוזת גולמית (`-r`), מספר/בוליאני כפי שהם.
- `manifest_plan` (restore.sh): `.partitions[] | [...] | join("|")`.
- `.partitions | length`.
- בלוק המולטיקאסט (directsend.sh): `.task.direct.multicast | [...] | map(tostring) | join("|")`.

כל ביטוי אחר → קוד יציאה 5, כמו jq על שגיאת קומפילציה — לא פלט ריק
שנראה כמו תשובה (עיקרון 5). זה **לא** jq: אין כאן פרסר של השפה.
"""

from __future__ import annotations

import json
import re
import sys

sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8", newline="\n")   # jq writes LF, not CRLF
sys.stderr.reconfigure(encoding="utf-8")


def _raw(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return json.dumps(value)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _path(doc, expr: str):
    cur = doc
    for key in expr.strip().lstrip(".").split("."):
        if key == "":
            continue
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


PLAN_KEYS = ("index", "type_guid", "role", "fs", "start_sector", "size_bytes",
             "file", "sha256", "expandable")
MC_KEYS = ("portbase", "min_receivers", "max_wait", "start_timeout",
           "max_wait_later", "start_timeout_later", "retries_until_drop")


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("-")]
    if len(args) not in (1, 2):
        sys.stderr.write("jq shim: expected <filter> [file]\n")
        return 2
    expr = args[0]
    try:
        if len(args) == 1:
            doc = json.load(sys.stdin)
        else:
            with open(args[1], encoding="utf-8") as handle:
                doc = json.load(handle)
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"jq shim: {exc}\n")
        return 2
    flat = " ".join(expr.split())
    if flat == ".partitions | length":
        parts = doc.get("partitions")
        print(len(parts) if isinstance(parts, list) else "null")
        return 0
    if flat.startswith(".partitions[] | [.index, .type_guid"):
        for part in doc.get("partitions", []):
            row = [_raw(part.get(k)) for k in PLAN_KEYS]
            row.append(_raw(part.get("unique_guid") or ""))
            row.append(_raw(part.get("uuid") or ""))
            print("|".join(row))
        return 0
    if flat.startswith(".task.direct.multicast | [.portbase"):
        block = _path(doc, ".task.direct.multicast")
        if not isinstance(block, dict):
            sys.stderr.write("jq shim: null multicast\n")
            return 5
        row = [_raw(block.get(k)) for k in MC_KEYS]
        row.append(_raw(block.get("max_bitrate") or ""))
        print("|".join(row))
        return 0
    if re.fullmatch(r"(\.[A-Za-z_][A-Za-z0-9_]*)+", flat):
        print(_raw(_path(doc, flat)))
        return 0
    sys.stderr.write(f"jq shim: unsupported filter {expr!r}\n")
    return 5


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
