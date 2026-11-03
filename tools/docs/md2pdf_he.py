"""Render a Hebrew (RTL) Markdown document to HTML and, optionally, to PDF (#1474).

Stdlib only -- the station has no `markdown` package, and the subset used by
docs/guides/admin-guide-he.md is small: headings, paragraphs, tables, lists,
blockquotes, fenced code, `code`, **bold**, [links](url), [[badge]], ---,
and `<!-- pagebreak -->`.

Every `code` span is wrapped as an LTR isolate (<code dir="ltr">), so ports,
addresses and ranges such as `8081/tcp` or `9000-9001` keep their order inside
right-to-left sentences.

Usage:
    python tools/docs/md2pdf_he.py docs/guides/admin-guide-he.md --pdf      # -> docs/guides/admin-guide-he.pdf
    python tools/docs/md2pdf_he.py docs/guides/admin-guide-he.md --html x.html   # HTML only, to look at
"""

from __future__ import annotations

import argparse
import html
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

CSS = """
@page { size: A4; margin: 15mm 14mm 16mm 14mm; }
:root { --ink:#1b1f24; --muted:#57606a; --line:#d0d7de; --head:#f3f5f8; --accent:#0b5cad;
        --warn-bg:#fff4d6; --warn-ink:#7a4b00; --v1-bg:#e6f0ff; --v1-ink:#0b4a8f; }
html { background:#fff; }
body { direction:rtl; font-family:"Segoe UI","Arial","Noto Sans Hebrew",sans-serif;
       color:var(--ink); font-size:10.5pt; line-height:1.5; margin:0; background:#fff; }
h1 { font-size:22pt; color:var(--accent); margin:0 0 6pt; }
h2 { font-size:15pt; color:var(--accent); border-bottom:2px solid var(--accent);
     padding-bottom:2pt; margin:18pt 0 6pt; break-after:avoid; }
h3 { font-size:12pt; margin:12pt 0 4pt; break-after:avoid; }
h4 { font-size:10.5pt; margin:10pt 0 3pt; break-after:avoid; }
p { margin:4pt 0 6pt; }
code { direction:ltr; unicode-bidi:isolate; font-family:Consolas,"Courier New",monospace;
       font-size:9pt; background:#f0f2f5; border-radius:3px; padding:0 2px; white-space:nowrap; }
pre { direction:ltr; text-align:left; background:#f6f8fa; border:1px solid var(--line);
      padding:6pt; font-size:8.5pt; white-space:pre-wrap; break-inside:avoid; }
pre code { background:none; white-space:pre-wrap; }
td code { white-space:normal; }
table { border-collapse:collapse; width:100%; margin:6pt 0 10pt; font-size:8.6pt; }
thead { display:table-header-group; }
tr { break-inside:avoid; }
th, td { border:1px solid var(--line); padding:3pt 4pt; vertical-align:top; text-align:right; }
th { background:var(--head); }
blockquote { margin:6pt 0; padding:4pt 8pt; border-right:4px solid var(--accent);
             background:#f6f9fd; }
ul, ol { margin:3pt 0 6pt; padding-right:18pt; padding-left:0; }
li { margin:1pt 0; }
hr { border:0; border-top:1px solid var(--line); margin:10pt 0; }
a { color:var(--accent); text-decoration:none; }
.badge { display:inline-block; font-size:8pt; font-weight:600; border-radius:3px;
         padding:0 4px; background:var(--warn-bg); color:var(--warn-ink); white-space:nowrap; }
.badge.v1 { background:var(--v1-bg); color:var(--v1-ink); }
.pagebreak { break-after:page; }
.box { display:inline-block; width:9pt; height:9pt; border:1px solid var(--ink);
       margin-left:4pt; vertical-align:-1pt; }
"""

_INLINE = [
    (re.compile(r"\*\*(.+?)\*\*"), r"<strong>\1</strong>"),
    (re.compile(r"\[\[(לפי עיצוב [^\]]*)\]\]"), r'<span class="badge v1">\1</span>'),
    (re.compile(r"\[\[(.+?)\]\]"), r'<span class="badge">\1</span>'),
]
_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")

#: A run of Latin words ("MAC address changes = Accept", "Windows Update") is
#: isolated as LTR, so the bidi algorithm does not scatter its pieces across a
#: right-to-left line. Commas end a run on purpose: "DHCP, TFTP" stays a
#: Hebrew-ordered list.
_LATIN_RUN = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:[ .=/:_+\-]+[A-Za-z0-9]+)*")


def _isolate_latin(text: str) -> str:
    out, pos = [], 0
    for m in _LATIN_RUN.finditer(text):
        out.append(html.escape(text[pos:m.start()], quote=False))
        out.append(f'<bdi dir="ltr">{html.escape(m.group(0), quote=False)}</bdi>')
        pos = m.end()
    out.append(html.escape(text[pos:], quote=False))
    return "".join(out)


def inline(text: str) -> str:
    """Escape, then apply inline markup. Code spans and links are cut out
    first so that nothing inside them is touched."""
    parts = re.split(r"(`[^`]+`|\[[^\]\[]+\]\([^)]+\)|<br>)", text)
    out = []
    for part in parts:
        if part == "<br>":                      # line break inside a table cell
            out.append(part)
            continue
        if part.startswith("`") and part.endswith("`") and len(part) > 1:
            out.append(f'<code dir="ltr">{html.escape(part[1:-1])}</code>')
            continue
        link = _LINK.fullmatch(part)
        if link:
            out.append(f'<a href="{html.escape(link.group(2))}">{_isolate_latin(link.group(1))}</a>')
            continue
        s = _isolate_latin(part)
        for pattern, repl in _INLINE:
            s = pattern.sub(repl, s)
        out.append(s)
    return "".join(out)


def _cells(row: str) -> list[str]:
    row = row.strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|"):
        row = row[:-1]
    return [c.strip() for c in re.split(r"(?<!\\)\|", row)]


def convert(md: str) -> str:
    lines = md.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        s = line.strip()
        if not s:
            i += 1
            continue
        if s == "<!-- pagebreak -->":
            out.append('<div class="pagebreak"></div>')
            i += 1
            continue
        if s.startswith("<!--"):
            while i < len(lines) and "-->" not in lines[i]:
                i += 1
            i += 1
            continue
        if s.startswith("```"):
            i += 1
            buf = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                buf.append(lines[i])
                i += 1
            out.append("<pre><code>" + html.escape("\n".join(buf)) + "</code></pre>")
            i += 1
            continue
        m = re.match(r"^(#{1,4})\s+(.*)$", s)
        if m:
            n = len(m.group(1))
            out.append(f"<h{n}>{inline(m.group(2))}</h{n}>")
            i += 1
            continue
        if re.fullmatch(r"-{3,}", s):
            out.append("<hr>")
            i += 1
            continue
        if s.startswith("|") and i + 1 < len(lines) and re.fullmatch(r"\|?[\s:|-]+\|?", lines[i + 1].strip()):
            head = _cells(s)
            i += 2
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(_cells(lines[i]))
                i += 1
            t = ["<table><thead><tr>"] + [f"<th>{inline(c)}</th>" for c in head] + ["</tr></thead><tbody>"]
            for r in rows:
                t.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>")
            t.append("</tbody></table>")
            out.append("".join(t))
            continue
        if s.startswith(">"):
            buf = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                buf.append(lines[i].strip()[1:].strip())
                i += 1
            out.append("<blockquote>" + convert("\n".join(buf)) + "</blockquote>")
            continue
        lm = re.match(r"^([-*]|\d+\.)\s+", s)
        if lm:
            tag = "ol" if lm.group(1)[0].isdigit() else "ul"
            # One nesting level: an indented marker under an item is a sub-item.
            items: list[list] = []          # [text, [sub-items]]
            while i < len(lines):
                cur = lines[i]
                cm = re.match(r"^(\s*)([-*]|\d+\.)\s+(.*)$", cur)
                if cm and not cm.group(1):
                    items.append([cm.group(3), []])
                elif cm and items:
                    items[-1][1].append(cm.group(3))
                elif cur.strip() and cur.startswith("  ") and items:
                    if items[-1][1]:
                        items[-1][1][-1] += " " + cur.strip()
                    else:
                        items[-1][0] += " " + cur.strip()
                else:
                    break
                i += 1
            lis = []
            for it, subs in items:
                box = ""
                if it.startswith("[ ] "):
                    box, it = '<span class="box"></span>', it[4:]
                sub = ("<ul>" + "".join(f"<li>{inline(x)}</li>" for x in subs) + "</ul>") if subs else ""
                lis.append(f"<li>{box}{inline(it)}{sub}</li>")
            style = ' style="list-style:none;padding-right:4pt"' if all(x[0].startswith("[ ] ") for x in items) else ""
            out.append(f"<{tag}{style}>" + "".join(lis) + f"</{tag}>")
            continue
        buf = [s]
        i += 1
        while i < len(lines) and lines[i].strip() and not re.match(r"^(#|\||>|```|[-*]\s|\d+\.\s|<!--|-{3,}$)", lines[i].strip()):
            buf.append(lines[i].strip())
            i += 1
        out.append("<p>" + inline(" ".join(buf)) + "</p>")
    return "\n".join(out)


def page(md: str, title: str) -> str:
    return (f'<!doctype html><html lang="he" dir="rtl"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>{html.escape(title)}</title><style>{CSS}</style></head>"
            f"<body>{convert(md)}</body></html>")


def find_browser() -> str | None:
    for cand in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                 r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
                 r"C:\Program Files\Google\Chrome\Application\chrome.exe"):
        if Path(cand).exists():
            return cand
    for name in ("msedge", "chromium", "chromium-browser", "google-chrome", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("source", type=Path)
    ap.add_argument("--pdf", action="store_true", help="also print to PDF with headless Edge/Chrome")
    ap.add_argument("--html", type=Path, default=None,
                    help="where to write the HTML (default: a temp file; not committed)")
    ap.add_argument("--title", default="מדריך מנהל IT")
    args = ap.parse_args(argv)
    md = args.source.read_text(encoding="utf-8")
    out_html = args.html or Path(tempfile.mkdtemp(prefix="md2pdf-")) / (args.source.stem + ".html")
    out_html.write_text(page(md, args.title), encoding="utf-8")
    print(f"html: {out_html}")
    if not args.pdf:
        return 0
    browser = find_browser()
    if browser is None:
        print("no Edge/Chrome found -- PDF not written", file=sys.stderr)
        return 2
    out_pdf = args.source.with_suffix(".pdf")
    if out_pdf.exists():
        out_pdf.unlink()
    done = subprocess.run([browser, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                           f"--print-to-pdf={out_pdf.resolve()}", out_html.resolve().as_uri()],
                          stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120)
    # Positive evidence: the browser's exit code is not enough -- the file must exist and be a PDF.
    if not out_pdf.exists() or not out_pdf.read_bytes()[:5] == b"%PDF-":
        print(f"PDF not written (rc={done.returncode}): {done.stderr.strip()[-300:]}", file=sys.stderr)
        return 3
    pages = len(re.findall(rb"/Type\s*/Page[^s]", out_pdf.read_bytes()))
    print(f"pdf: {out_pdf} ({pages} pages)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
