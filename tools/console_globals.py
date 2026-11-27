#!/usr/bin/env python3
"""Map the console's top-level JavaScript names (#2579, slice C1).

`server/static/console.js` is split by comment headers of the form
`/* ---------- title ---------- */` into regions. For every region this tool
lists the top-level declarations (function / const / let / var / class) and who
uses each one outside its region. The map is generated from the code, not
written by hand: `docs/research/console-globals-map.md` is this tool's output.

    python tools/console_globals.py            # rewrite the map
    python tools/console_globals.py --check    # exit 1 on a duplicate name

The usage scan is an upper bound: strings are kept (inline `onclick="fn()"`
handlers are real uses), so a name that also appears as a word inside a text
literal counts as used. Over-reporting is the safe direction for a split plan.
"""
from __future__ import annotations

import argparse
import bisect
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "server" / "static"
OUT = ROOT / "docs" / "research" / "console-globals-map.md"

_REGEX_AFTER = set("(,=:[!&|?{};+-*%<>~^")
_REGEX_KW = {"return", "typeof", "case", "in", "of", "delete", "void", "throw", "else", "do", "new"}
_DECL = re.compile(r"\b(async\s+function\s*\*?|function\s*\*?|const|let|var|class)\s+([\w$]+)")
_HEADER = re.compile(r"(?m)^/\* -{5,}[ \t]*(.*)")
_IDENT = re.compile(r"(?<![\w$])[A-Za-z_$][\w$]*")
_WORD = re.compile(r"[\w$]+")
_STMT_NOT_END_PREV = set(",=+-*/%&|?:<>!(")
_STMT_NOT_END_NEXT = set(",.=+-*/%&|?:<>)")


def mask(src: str, keep_strings: bool) -> str:
    """Blank comments and regex literals (and strings unless kept); same length."""
    out = list(src)
    n = len(src)
    i = 0
    tpl: list[int] = []  # open brace counters of `${` expressions
    in_tpl = False
    last = ""  # last significant code char, or "w" for a word

    def blank(a: int, b: int) -> None:
        for k in range(a, min(b, n)):
            if out[k] != "\n":
                out[k] = " "

    while i < n:
        c = src[i]
        if in_tpl:
            if c == "\\":
                i += 2
                continue
            if c == "`":
                if not keep_strings:
                    blank(i, i + 1)
                in_tpl, last = False, "`"
            elif c == "$" and src[i + 1:i + 2] == "{":
                tpl.append(0)
                in_tpl = False
                i += 2
                continue
            elif not keep_strings:
                blank(i, i + 1)
            i += 1
            continue
        if c == "/" and src[i + 1:i + 2] == "/":
            j = src.find("\n", i)
            j = n if j < 0 else j
            blank(i, j)
            i = j
        elif c == "/" and src[i + 1:i + 2] == "*":
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            blank(i, j)
            i = j
        elif c in "'\"":
            j = i + 1
            while j < n and src[j] != c and src[j] != "\n":
                j += 2 if src[j] == "\\" else 1
            if not keep_strings:
                blank(i + 1, j)
            i, last = j + 1, c
        elif c == "`":
            in_tpl = True
            if not keep_strings:
                blank(i, i + 1)
            i += 1
        elif c == "/":
            if last == "" or last in _REGEX_AFTER or last in _REGEX_KW:
                j, cls = i + 1, False
                while j < n and src[j] != "\n":
                    if src[j] == "\\":
                        j += 2
                        continue
                    if src[j] == "[":
                        cls = True
                    elif src[j] == "]":
                        cls = False
                    elif src[j] == "/" and not cls:
                        break
                    j += 1
                blank(i, j + 1)
                i, last = j + 1, "re"
            else:
                last = c
                i += 1
        else:
            if c == "{" and tpl:
                tpl[-1] += 1
            elif c == "}" and tpl:
                if tpl[-1] == 0:
                    tpl.pop()
                    in_tpl = True
                    i += 1
                    continue
                tpl[-1] -= 1
            if c.isalnum() or c in "_$":
                m = _WORD.match(src, i)
                last = m.group(0) if m.group(0) in _REGEX_KW else "w"
                i = m.end()
                continue
            if not c.isspace():
                last = c
            i += 1
    return "".join(out)


def _depths(masked: str) -> list[int]:
    d, cur = [], 0
    for ch in masked:
        if ch in ")}]":
            cur -= 1
        d.append(cur)
        if ch in "({[":
            cur += 1
    return d


def _names_in(pattern: str) -> list[str]:
    pattern = pattern.strip()
    if pattern[:1] in ("{", "["):
        return re.findall(r"([A-Za-z_$][\w$]*)\s*(?=[,}\]=]|$)", pattern)
    m = re.match(r"[A-Za-z_$][\w$]*", pattern)
    return [m.group(0)] if m else []


def top_level_decls(src: str) -> list[tuple[str, str, int]]:
    """(kind, name, offset) of every top-level declaration, in source order."""
    masked = mask(src, keep_strings=False)
    depth = _depths(masked)
    n = len(masked)
    res: list[tuple[str, str, int]] = []
    for m in _DECL.finditer(masked):
        s = m.start()
        if depth[s] != 0:
            continue
        j = s - 1
        while j >= 0 and masked[j].isspace():
            j -= 1
        if j >= 0 and masked[j] not in ";}":
            continue
        kind = "function" if "function" in m.group(1) else m.group(1)
        if kind in ("function", "class"):
            res.append((kind, m.group(2), s))
            continue
        # const/let/var: split the declarator list at depth-0 commas.
        k = start = m.start(2)
        parts: list[str] = []
        while k < n:
            ch = masked[k]
            if depth[k] == 0 and ch == ",":
                parts.append(masked[start:k])
                start = k + 1
            elif depth[k] == 0 and ch == ";":
                break
            elif depth[k] == 0 and ch == "\n":
                p = k - 1
                while p >= start and masked[p].isspace():
                    p -= 1
                q = k + 1
                while q < n and masked[q].isspace():
                    q += 1
                cont = (p >= start and masked[p] in _STMT_NOT_END_PREV) or (
                    q < n and masked[q] in _STMT_NOT_END_NEXT)
                if not cont:
                    break
            k += 1
        parts.append(masked[start:k])
        for part in parts:
            for name in _names_in(part):
                res.append((kind, name, s))
    return res


def regions(src: str) -> list[tuple[str, int]]:
    """(title, start offset) per region; region 0 is the code before the first header."""
    out = [("(לפני הכותרת הראשונה)", 0)]
    for m in _HEADER.finditer(src):
        title = re.sub(r"[\u200e\u200f]", "", m.group(1))
        title = re.sub(r"\s*-{3,}\s*\*/\s*$", "", title).strip(" -*/")
        out.append((title or "(ללא שם)", m.start()))
    return out


def find_duplicates(files: dict[str, str]) -> dict[str, list[tuple[str, int, str]]]:
    """name -> [(file, line, kind)] for every top-level name declared more than once."""
    seen: dict[str, list[tuple[str, int, str]]] = {}
    for fname, src in files.items():
        for kind, name, off in top_level_decls(src):
            seen.setdefault(name, []).append((fname, src.count("\n", 0, off) + 1, kind))
    return {k: v for k, v in seen.items() if len(v) > 1}


def _idents(code: str) -> set[str]:
    out = set()
    for m in _IDENT.finditer(code):
        s = m.start()
        if code[s - 1:s] == "." and code[s - 3:s] != "...":
            continue
        out.add(m.group(0))
    return out


def build(files: dict[str, str], main: str = "console.js",
          dup_files: list[str] | None = None) -> dict:
    src = files[main]
    regs = regions(src)
    starts = [s for _, s in regs]
    bounds = starts + [len(src)]
    kept = mask(src, keep_strings=True)
    reg_ids = [_idents(kept[bounds[i]:bounds[i + 1]]) for i in range(len(regs))]
    file_ids = {f: _idents(mask(t, keep_strings=True)) for f, t in files.items() if f != main}
    decls = []
    for kind, name, off in top_level_decls(src):
        r = bisect.bisect_right(starts, off) - 1
        users = [f"R{i}" for i, ids in enumerate(reg_ids) if i != r and name in ids]
        users += [f for f, ids in file_ids.items() if name in ids]
        decls.append({"kind": kind, "name": name, "region": r,
                      "line": src.count("\n", 0, off) + 1, "users": users})
    dup_files = [main] if dup_files is None else dup_files
    return {"regions": regs, "starts": starts, "decls": decls,
            "lines": src.count("\n") + 1, "main": main,
            "region_lines": [src.count("\n", 0, s) + 1 for s in starts],
            "dups": find_duplicates({f: files[f] for f in dup_files})}


def render(info: dict) -> str:
    regs, decls = info["regions"], info["decls"]
    lines = [
        "# מפת הגלובלים של הקונסולה (#2579, פרוסה C1)", "",
        "נוצר אוטומטית: `python tools/console_globals.py`. לא לערוך ידנית.",
        f"מקור: `{info['main']}` ({info['lines']} שורות), {len(regs)} אזורים לפי כותרות "
        "`/* ---------- … ---------- */`. עמודת \"בשימוש ב\" היא גבול עליון "
        "(מחרוזות נספרות, כי `onclick=\"fn()\"` הוא שימוש אמיתי); `Rn` = אזור n, "
        "שם קובץ = קובץ JS אחר ב-`server/static`.", "",
        "## שמות מוצהרים פעמיים בקבצי הקונסולה", "",
    ]
    if info["dups"]:
        for name, locs in sorted(info["dups"].items()):
            lines.append(f"- `{name}`: " + ", ".join(f"{f}:{ln} ({k})" for f, ln, k in locs))
    else:
        lines.append("אין.")
    lines += ["", "## סיכום לפי אזור", "",
              "| אזור | שורה | כותרת | הצהרות | בשימוש מחוץ לאזור |", "|---|---|---|---|---|"]
    for i, (title, _) in enumerate(regs):
        mine = [d for d in decls if d["region"] == i]
        ext = sum(1 for d in mine if d["users"])
        lines.append(f"| R{i} | {info['region_lines'][i]} | {title[:70]} | {len(mine)} | {ext} |")
    lines += ["", "## תלות בין אזורים", ""]
    dep: dict[int, dict[int, int]] = {}
    for d in decls:
        for u in d["users"]:
            if u.startswith("R"):
                row = dep.setdefault(int(u[1:]), {})
                row[d["region"]] = row.get(d["region"], 0) + 1
    for i in sorted(dep):
        lines.append(f"- R{i} משתמש ב: " + ", ".join(f"R{k} ({v})" for k, v in sorted(dep[i].items())))
    for i, (title, _) in enumerate(regs):
        lines += ["", f"## R{i}: {title}", ""]
        mine = [d for d in decls if d["region"] == i]
        if not mine:
            lines.append("אין הצהרות ברמה העליונה.")
            continue
        lines += ["| סוג | שם | שורה | בשימוש ב |", "|---|---|---|---|"]
        for d in mine:
            u = d["users"]
            shown = (", ".join(u[:8]) + (f" (+{len(u) - 8})" if len(u) > 8 else "")) if u else "פנימי"
            lines.append(f"| {d['kind']} | `{d['name']}` | {d['line']} | {shown} |")
    return "\n".join(lines) + "\n"


def load_files() -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(STATIC.glob("*.js"))}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--check", action="store_true", help="only report duplicates; exit 1 if any")
    a = ap.parse_args(argv)
    files = load_files()
    sys.path.insert(0, str(ROOT))
    from tests.console_src import console_files
    info = build(files, dup_files=console_files())
    if a.check:
        for name, locs in sorted(info["dups"].items()):
            print(f"duplicate {name}: " + ", ".join(f"{f}:{ln}" for f, ln, _ in locs))
        return 1 if info["dups"] else 0
    a.out.write_text(render(info), encoding="utf-8", newline="\n")
    print(f"{a.out}: {len(info['decls'])} declarations, {len(info['regions'])} regions, "
          f"{len(info['dups'])} duplicates")
    return 0


if __name__ == "__main__":
    sys.exit(main())
