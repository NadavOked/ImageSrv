"""‏#416 — "אין דיסק" אינו "אין מערכת הפעלה", וענף הדיסק המקומי מדווח.

מחשב הבנייה (05/09) הדפיס "No operating system found on the local disk"
על דיסק שהקושחה כלל לא ראתה (‏F12 ריק, ‏`no such device` על כל נתיב).
ההודעה שלחה לפרק אימג' תקין במשך שעה. שני מצבים שקופלו לאחד (עיקרון 5),
בדיוק כמו #61 לפניו — ולכן הבדיקות כאן **מריצות** את `chain_local` על
מפרש GRUB זעיר, במקום לספור מחרוזות: מכונה בלי דיסק, מכונה עם דיסק ריק,
ומכונה שמטען האתחול שלה נדחה חייבות להדפיס שלושה מסכים שונים ולהשאיר
שלושה פירורים שונים.

המפרש מכיר רק את מה שהפונקציות האלה משתמשות בו: ‏function/for/if/else/
fi/while, ‏set/unset, ‏echo עם הרחבת משתנים, ‏`[ -n ]`/`[ -z ]`/`[ = ]`,
ו-`search`/`probe`/`chainloader`/`boot`/`ls`/`imagesrv_trace` כפקודות
מדומות. פקודה שאינו מכיר היא שגיאה — טסט שמריץ תחביר חדש בשקט אינו טסט.
"""

from __future__ import annotations

import re

import pytest

from boot import trace
from boot.grub_menu import GrubConfig, render, render_bootstrap, render_local_only

CFG = GrubConfig(server_base="http://10.44.12.10:8080")
WINDOWS = "/EFI/Microsoft/Boot/bootmgfw.efi"


def answer(**overrides) -> dict:
    base = {
        "schema": 1, "known": True, "role": "build",
        "group": {"id": "grp_A", "label": "בנייה", "suffix": "01"},
        "task": None, "session": None, "allowed_images": [], "ui": {},
    }
    base.update(overrides)
    return base


class Grub:
    """מפרש זעיר לתת-הקבוצה של תסריט GRUB שבפונקציות הדיסק המקומי.

    ‏`disks` — האם (hd0) קיים; ‏`files` — הנתיבים שיש להם ESP; ‏`refuse` —
    הנתיבים ש-chainloader דוחה. ‏`screen` הוא מה שהודפס, ‏`steps` הפירורים,
    ו-`booted` הנתיב שעלה (או None).
    """

    def __init__(self, text: str, *, disks: bool, files: set[str] = frozenset(),
                 refuse: set[str] = frozenset(), shim_lock: str = ""):
        self.disks, self.files, self.refuse = disks, set(files), set(refuse)
        self.env: dict[str, str] = {"grub_platform": "efi", "shim_lock": shim_lock}
        self.screen: list[str] = []
        self.steps: list[str] = []
        self.booted: str | None = None
        self.functions: dict[str, list[str]] = {}
        lines = text.splitlines()
        i = 0
        while i < len(lines):
            m = re.fullmatch(r"function (\w+) \{", lines[i])
            if m:
                j = lines.index("}", i)
                self.functions[m.group(1)] = [l.strip() for l in lines[i + 1 : j]]
                i = j
            i += 1

    def expand(self, s: str) -> str:
        return re.sub(r"\$\{?(\w+)\}?", lambda m: self.env.get(m.group(1), ""), s)

    def test(self, expr: str) -> bool:
        expr = expr.strip()
        if expr.startswith("[") and expr.endswith("]"):
            inner = expr[1:-1].strip()
            m = re.fullmatch(r'-n "(.*)"', inner)
            if m:
                return self.expand(m.group(1)) != ""
            m = re.fullmatch(r'-z "(.*)"', inner)
            if m:
                return self.expand(m.group(1)) == ""
            m = re.fullmatch(r'"(.*)" = "(.*)"', inner)
            if m:
                return self.expand(m.group(1)) == self.expand(m.group(2))
            raise ValueError(f"unknown test {expr!r}")
        return self.command(expr) == 0

    def command(self, line: str) -> int:
        """פקודה אחת. קוד 0 = הצליחה. מעלה StopIteration כשהמכונה עצרה."""
        words = line.split()
        name = words[0]
        if name in ("insmod", "sleep"):
            return 0
        if name == "echo":
            self.screen.append(self.expand(line[5:].strip().strip('"')))
            return 0
        if name == "set":
            k, v = line[4:].split("=", 1)
            self.env[k] = self.expand(v)
            return 0
        if name == "unset":
            self.env.pop(words[1], None)
            return 0
        if name == trace.GRUB_FUNCTION_NAME:
            self.steps.append(words[1])
            return 0
        if name == "search":
            var = next(w for w in words if w.startswith("--set=")).split("=", 1)[1]
            path = self.expand(words[-1].strip('"'))
            if path in self.files:
                self.env[var] = "hd0,gpt1"
                return 0
            return 1
        if name == "probe":
            var = next(w for w in words if w.startswith("--set=")).split("=", 1)[1]
            if self.disks:
                self.env[var] = "efidisk"
                return 0
            self.screen.append("error: no such device: hd0.")
            return 1
        if name == "chainloader":
            path = self.expand(words[1].strip('"'))
            self.env["_chain"] = path
            return 1 if path in self.refuse else 0
        if name == "boot":
            self.booted = self.env["_chain"]
            raise StopIteration("booted")
        if name == "ls":
            self.screen.append("(hd0) (hd0,gpt1)" if self.disks else "(memdisk) (proc)")
            return 0
        if name == "while":
            raise StopIteration("stay-on loop")
        if name in self.functions:
            saved = self.env.get("1")
            self.env["1"] = self.expand(words[1].strip('"')) if len(words) > 1 else ""
            self.run(self.functions[name])
            if saved is None:
                self.env.pop("1", None)
            else:
                self.env["1"] = saved
            return 0
        raise ValueError(f"unknown command {line!r}")

    def run(self, lines: list[str]) -> None:
        i = 0
        while i < len(lines):
            line = lines[i]
            if not line:
                i += 1
                continue
            m = re.fullmatch(r"for (\w+) in (.*); do", line)
            if m:
                end = self._block_end(lines, i, "for ", "done")
                for value in m.group(2).split():
                    self.env[m.group(1)] = value
                    self.run(lines[i + 1 : end])
                i = end + 1
                continue
            m = re.fullmatch(r"if (.+); then", line)
            if m:
                end = self._block_end(lines, i, "if ", "fi")
                body = lines[i + 1 : end]
                depth, split = 0, None
                for k, b in enumerate(body):
                    if b.startswith("if ") and not b.endswith("fi"):
                        depth += 1
                    elif b == "fi":
                        depth -= 1
                    elif b == "else" and depth == 0:
                        split = k
                if self.test(m.group(1)):
                    self.run(body[:split] if split is not None else body)
                elif split is not None:
                    self.run(body[split + 1 :])
                i = end + 1
                continue
            m = re.fullmatch(r"if (.+); then (.+); fi", line)
            if m:
                if self.test(m.group(1)):
                    self.command(m.group(2))
                i += 1
                continue
            self.command(line)
            i += 1

    @staticmethod
    def _block_end(lines: list[str], start: int, opener: str, closer: str) -> int:
        depth = 0
        for k in range(start, len(lines)):
            if lines[k].startswith(opener) and not lines[k].endswith(closer):
                depth += 1
            elif lines[k] == closer:
                depth -= 1
                if depth == 0:
                    return k
        raise ValueError(f"unterminated {opener!r} at {start}")

    def chain_local(self) -> "Grub":
        try:
            self.command("chain_local")
        except StopIteration:
            pass
        return self


def dynamic() -> str:
    return render(answer(), CFG)


# --- שלושה מצבים, שלושה מסכים --------------------------------------------------


def test_a_machine_with_no_disk_says_no_disk_not_no_os():
    """הבאג של #416: בלי דיסק בכלל המסך אמר "אין מערכת על הדיסק"."""
    g = Grub(dynamic(), disks=False).chain_local()
    text = "\n".join(g.screen)
    assert "No disk device found." in text
    assert "No operating system found" not in text
    assert g.booted is None
    assert g.steps == ["local-entry", "local-no-disk"]


def test_a_blank_disk_says_no_operating_system_and_that_a_disk_is_present():
    g = Grub(dynamic(), disks=True).chain_local()
    text = "\n".join(g.screen)
    assert "No operating system found on the local disk." in text
    assert "A disk IS present" in text
    assert "No disk device found" not in text
    assert g.booted is None
    assert g.steps == ["local-entry", "local-search-empty"]


def test_the_two_failure_screens_are_different():
    """הבקרה שה-Issue ביקש: דיסק ריק ומכונה בלי דיסק אסור שייראו אותו דבר."""
    none = Grub(dynamic(), disks=False).chain_local()
    blank = Grub(dynamic(), disks=True).chain_local()
    assert none.screen != blank.screen
    assert none.steps != blank.steps


def test_a_refused_loader_is_still_reported_as_refused_with_its_own_breadcrumb():
    """‏#61 נשאר כפי שהיה — ומקבל פירור."""
    g = Grub(dynamic(), disks=True, files={WINDOWS}, refuse={WINDOWS}, shim_lock="y").chain_local()
    text = "\n".join(g.screen)
    assert "A boot loader WAS found on the local disk:" in text and WINDOWS in text
    assert "Secure Boot is on" in text
    assert "No operating system found" not in text and "No disk device found" not in text
    assert g.booted is None
    assert g.steps == ["local-entry", "local-search-ok", "local-chain-refused"]


def test_a_loader_that_starts_leaves_search_ok_as_the_last_breadcrumb():
    g = Grub(dynamic(), disks=True, files={WINDOWS}).chain_local()
    assert g.booted == WINDOWS
    assert g.steps == ["local-entry", "local-search-ok"]
    assert g.screen == []


def test_search_ok_is_sent_once_even_when_several_loaders_are_found_and_refused():
    """שני נתיבים נמצאו ונדחו — פירור אחד, לא שניים: הפירור מתאר את
    המצב ("נמצא"), לא כל איטרציה של הלולאה."""
    ubuntu = "/EFI/ubuntu/shimx64.efi"
    g = Grub(dynamic(), disks=True, files={WINDOWS, ubuntu}, refuse={WINDOWS, ubuntu}).chain_local()
    assert g.steps.count("local-search-ok") == 1
    assert g.steps[-1] == "local-chain-refused"


def test_every_stop_screen_lists_the_devices_grub_sees():
    """‏5א: לפני שמסיקים ממה שהמכונה לא הצליחה, שואלים אותה מה היא רואה."""
    for g in (Grub(dynamic(), disks=False).chain_local(), Grub(dynamic(), disks=True).chain_local()):
        assert "Devices GRUB can see:" in g.screen
        assert g.screen.index("Devices GRUB can see:") == len(g.screen) - 3   # ls, then Contact IT


# --- הפירורים: איפה הם נפלטים ואיפה לא ---------------------------------------


def test_the_local_breadcrumbs_are_the_declared_local_steps():
    text = dynamic()
    sent = sorted(set(re.findall(rf"{trace.GRUB_FUNCTION_NAME} (local-[a-z-]+)", text)))
    assert sent == sorted(trace.LOCAL_STEPS)


def test_each_local_breadcrumb_stands_alone_on_its_line():
    """כמו בערך ImageSrv: לא לפני &&, לא באותה שורה עם פקודת אתחול."""
    for line in dynamic().splitlines():
        if trace.GRUB_FUNCTION_NAME in line and "function " not in line and "cat " not in line:
            assert re.fullmatch(rf"\s*{trace.GRUB_FUNCTION_NAME} [a-z-]+", line), line


def test_files_without_the_trace_function_send_no_local_breadcrumbs():
    """הקובץ הקבוע רץ כשהשרת שקט, ומכונה לא רשומה אינה נרשמת (עיקרון 1).
    קריאה לפונקציה שאינה מוגדרת הייתה מדפיסה שגיאה על מסך שכל תפקידו
    להיקרא."""
    for text in (render_bootstrap(CFG), render_local_only("x"),
                 render(answer(known=False), CFG)):
        assert trace.GRUB_FUNCTION_NAME not in text
        assert "local-entry" not in text


def test_the_untraced_chain_local_still_tells_no_disk_from_no_os():
    """ההבחנה אינה תלויה בפירורים: גם הקובץ הקבוע (שרת כבוי) מבחין."""
    g = Grub(render_bootstrap(CFG), disks=False).chain_local()
    assert "No disk device found." in g.screen
    assert g.steps == []


def test_the_local_disk_screens_are_ascii():
    render(answer(), CFG).encode("ascii")
    render_bootstrap(CFG).encode("ascii")


def test_the_generator_refuses_a_local_step_it_never_declared():
    with pytest.raises(ValueError):
        trace.grub_call("local-nearly")
