"""#4116: הסימולציה האמיתית נכשלת (יציאה 1) כשיש פגם אמיתי בשרת — לא רק כשמדמים שלב שנכשל.

`test_e2e_simulation_exit_3546.py` מחליף את ה-harness והשלבים בדמה ובודק את חוזה קוד היציאה. כאן **אין דמה**:
`tools.e2e_simulation.main()` מרים את השרת האמיתי (`harness.start_server`, תהליך נפרד) ומריץ מולו את שלבי
ההקמה, הקליטה והקצוות (0, 1-2, 3-5) בלבד — בלי `chunkcast`, בלי gcc, בלי root ובלי נגיעה במארח (#3953,
ראו `tools/e2e/simhost.py`). בכל ריצה מוזרק לתהליך השרת פגם אחד לפני שה-`main` שלו עולה (קידומת ל-`-c`
של `start_server`, כמו ש-`simsend` מוזרק), והסימולציה חייבת לצאת 1 ולנקוב בבדיקה שנפלה:

- **hello**: מכונה לא מוכרת מקבלת `known: true` (השרת מציע אימג'ים למי שאינו רשום).
- **זהות**: שומר ה-DHCP (#855) מאשר כל MAC, גם בלי חכירה.
- **כתיבה**: קליטה שמקבלת את הבייטים, מגבבת אותם ו**אינה כותבת אותם לדיסק** (`done` בלי כתיבה).

ריצת הבקרה — אותה ריצה בלי פגם — חייבת לצאת 0: אחרת "יציאה 1" לא מוכיחה כלום.
המחיר: ארבע עליות שרת (כ-10 שניות כל אחת).
"""

from __future__ import annotations

import importlib
import subprocess
from types import SimpleNamespace

import pytest

e2e = importlib.import_module("tools.e2e_simulation")
from tools.e2e import harness  # noqa: E402

#: הבדיקות שרצות בשלושת השלבים האלה (נמדד 65, Windows 3.12, 26/11). פחות מזה = שלב שלא רץ, ולא ריצה ירוקה.
FLOOR = 60

#: פגם → הבדיקה שהסימולציה אמורה לנקוב בה. כל פגם הוא שורת קוד שרצה בתהליך השרת לפני `main()`.
FAULTS = {
    "unknown mac answered known": (
        "import server.agent.hello as h; o = h._unknown; h._unknown = lambda *a, **k: {**o(*a, **k), 'known': True}",
        "MAC לא מוכר"),
    "identity guard approves everything": (
        "import server.agent.identity as i; i.verify = lambda *a, **k: i.Verdict(ok=True)",
        "MAC בלי חכירת DHCP"),
    "capture accepts the bytes and writes nothing": (
        "import server.library.capture as c; c._write_and_hash = lambda handle, digest, data: digest.update(data)",
        "המניפסט התקבל"),
}


@pytest.fixture()
def simulation(monkeypatch):
    """`main()` האמיתי של הסימולציה, בלי שלבי הכיתה/החדר/השידור/היומן וללא בניית `chunkcast`."""
    for name in ("class", "room", "direct", "chunk", "journal"):
        monkeypatch.setattr(e2e, f"phase_{name}", SimpleNamespace(run=lambda ctx: None))
    monkeypatch.setattr(harness, "build_chunkcast", lambda workdir: None)
    monkeypatch.setattr(harness, "passed", 0)
    monkeypatch.setattr(e2e, "MIN_CHECKS", FLOOR)
    real_start = harness.start_server

    def run(fault: str | None = None) -> int:
        def start(workdir):
            real_popen = subprocess.Popen

            def popen(cmd, **kwargs):
                cmd = list(cmd)
                if fault is not None:
                    at = cmd.index("-c") + 1
                    cmd[at] = fault + "; " + cmd[at]
                # בווינדוס, בריצה רב-קבצית של pytest, stdin יורש נשבר תחת capture (WinError 50/6; CLAUDE.md).
                kwargs.setdefault("stdin", subprocess.DEVNULL)
                return real_popen(cmd, **kwargs)

            monkeypatch.setattr(subprocess, "Popen", popen)
            try:
                return real_start(workdir)
            finally:
                monkeypatch.setattr(subprocess, "Popen", real_popen)

        monkeypatch.setattr(harness, "start_server", start)
        monkeypatch.setattr(harness, "passed", 0)
        return e2e.main()

    return run


def test_the_control_run_without_a_fault_closes_the_loop(simulation, capsys):
    assert simulation() == 0
    out = capsys.readouterr().out
    assert "הלולאה נסגרה" in out and "✗" not in out
    print("PASSED_COUNT", harness.passed)
    assert harness.passed >= FLOOR, f"רצו {harness.passed} בדיקות — הרף {FLOOR} אינו מדויק"


@pytest.mark.parametrize("fault", list(FAULTS))
def test_a_real_server_defect_fails_the_simulation_and_names_the_check(simulation, capsys, fault):
    code, label = FAULTS[fault]
    assert simulation(code) == 1, f"הסימולציה יצאה ירוקה למרות הפגם: {fault}"
    out = capsys.readouterr().out
    assert "הלולאה נסגרה" not in out
    failed = [line for line in out.splitlines() if "✗" in line or "FAILED:" in line]
    assert any(f"✗ {label}" in line for line in failed) and any("FAILED:" in line for line in failed), failed or out[-1500:]
