"""‏#1269 — הנחת דרייברים על מגירות של מחשב שיכפול (`agent/lib/postdeploy/postdeploy.sh`).

אותו staging של #720 (‏`test_postdeploy.py` בודק את המנגנון עצמו), מנקודת
המבט של המגירה: רק החריצים ש-hello אמר שנבחר להם דגם
(‏`session.driver_ports`), רק מגירה שנכתבה עד הסוף, התוצאה בקובץ של
המגירה (‏`targets/<dev>/drivers.json`) — ובלי לגעת במצב העליון של המכונה.
בלי `driver_ports` הסוכן אינו שואל את השרת דבר.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from native import requires_native
from test_agent import AGENT, BASH, posix
from test_postdeploy import FILES, Box, answer_for

pytestmark = requires_native(("bash", BASH))

M70Q = {"vendor": "LENOVO", "model": "ThinkCentre M70q Gen 3"}

#: ‏curl שרושם כל URL של שאלת הדרייברים, ואחרת כמו ב-test_postdeploy.
CURL = r'''
for a in "$@"; do url="$a"; done
case "$url" in
  *"/api/v1/agent/drivers?"*) echo "$url" >> "$FAKE_SERVER/queries"
      cat "$FAKE_SERVER/answer.json"; exit 0 ;;
  *"/api/v1/agent/drivers/"*"/files/"*)
    rel=${url#*/api/v1/agent/drivers/}; pkg=${rel%%/files/*}; path=${rel#*/files/}
    [ -f "$FAKE_SERVER/pkgs/$pkg/$path" ] || exit 22
    cat "$FAKE_SERVER/pkgs/$pkg/$path" ;;
  *) exit 22 ;;
esac
'''


class Drawers(Box):
    """שתי מגירות — ‏sda בחריץ 1, ‏sdb בחריץ 2 — שנכתבו (`done`)."""

    def __init__(self, tmp_path, *, ports: list[int] | None, chosen=M70Q,
                 packages=FILES, states=("done", "done")):
        answer = {**answer_for(packages), "chosen": chosen}
        answer.pop("inventory")
        super().__init__(tmp_path, packages=packages, answer=answer, stubs={"curl": CURL})
        for dev, state in zip(("sda", "sdb"), states):
            (self.run / "targets" / dev).mkdir(parents=True)
            (self.run / "targets" / dev / "state").write_text(state + "\n")
        (self.run / "state").write_text("partial\n")
        ports_text = "null\n" if ports is None else "".join(f"{p}\n" for p in ports)
        (self.server / "ports").write_text(ports_text, newline="\n")
        (self.server / "chosen").write_text("null\n" if chosen is None else json.dumps(chosen) + "\n")
        self.script += (
            'RESP="$FAKE_SERVER/resp.json"; '
            'disk_port() { case "$1" in sda) echo 1;; sdb) echo 2;; esac; }; '
            'json_get() { case "$2" in ".ok") echo true;; '
            '".chosen") cat "$FAKE_SERVER/chosen";; '
            '*driver_ports*) cat "$FAKE_SERVER/ports";; '
            '".packages[].name") cat "$FAKE_SERVER/names";; '
            '*files*) cat "$FAKE_SERVER/rows";; *) echo null;; esac; }; '
        )

    def stage_drawers(self) -> subprocess.CompletedProcess:
        out = subprocess.run(
            [BASH, "-c", 'export PATH="/usr/bin:$PATH"; ' + self.script
             + 'stage_drawer_drivers ses_1 /dev/null sda sdb; echo "rc=$?"'],
            capture_output=True, text=True, encoding="utf-8",
            cwd=str(AGENT.parent), stdin=subprocess.DEVNULL,
        )
        assert out.returncode == 0, out.stderr
        assert out.stdout.strip().endswith("rc=0"), out.stdout
        return out

    def outcome(self, dev: str) -> dict | None:
        path = self.run / "targets" / dev / "drivers.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def queries(self) -> list[str]:
        path = self.server / "queries"
        return path.read_text().splitlines() if path.exists() else []


def test_only_the_drawer_with_a_chosen_model_is_staged_and_the_machine_state_stays(tmp_path):
    box = Drawers(tmp_path, ports=[2])
    box.stage_drawers()
    # ‏#1331: מגירת SATA — ‏storahci הוא boot-start על הדיסק שיצא מהמגירה.
    assert box.outcome("sdb") == {"state": "staged", "packages": ["intel-nic", "ahci-fix"],
                                  "boot": {"state": "enabled", "service": "storahci"}}
    assert box.outcome("sda") is None, "חריץ 1 לא נבחר — השלב לא רץ עליו"
    assert len(box.queries()) == 1
    assert "mac=b4:2e:99:07:1a:c4&session=ses_1&port=2" in box.queries()[0]
    assert box.staged_files(), "הקבצים על ה-NTFS של המגירה"
    assert box.state() == "partial", "המצב העליון של המכונה אינו נדרס"
    assert not (box.run / "drivers.json").exists(), "הקובץ העליון שייך לתחנה, לא למגירה"


def test_a_round_without_drivers_asks_the_server_nothing(tmp_path):
    box = Drawers(tmp_path, ports=None)
    box.stage_drawers()
    assert box.queries() == []
    assert box.outcome("sda") is None and box.outcome("sdb") is None
    assert box.calls() == [], "אף מחיצה לא עוגנה"


def test_a_drawer_that_was_not_written_in_full_is_not_staged(tmp_path):
    box = Drawers(tmp_path, ports=[1, 2], states=("failed", "done"))
    box.stage_drawers()
    assert box.outcome("sda") is None
    assert box.outcome("sdb")["state"] == "staged"
    assert [q for q in box.queries() if "port=1" in q] == []


def test_a_drawer_whose_port_is_unknown_is_never_staged(tmp_path):
    """חריץ ריק אינו "תואם" לרשימה — אחרת הוא היה נופל למסלול התחנה
    (שאלה לפי המלאי של המשכפל) על דיסק של מכונה אחרת."""
    box = Drawers(tmp_path, ports=[2])
    box.script += 'disk_port() { echo; }; '
    box.stage_drawers()
    assert box.queries() == []
    assert box.outcome("sda") is None and box.outcome("sdb") is None


def test_a_chosen_model_without_packages_is_failed_openly_not_no_match(tmp_path):
    box = Drawers(tmp_path, ports=[2], packages={})
    box.stage_drawers()
    result = box.outcome("sdb")
    assert result["state"] == "failed" and "לדגם שנבחר" in result["error"]
    assert box.staged_files() == {} and box.calls() == []
    assert box.state() == "partial"


def test_a_port_the_server_has_no_model_for_is_skipped_by_name(tmp_path):
    box = Drawers(tmp_path, ports=[2], chosen=None)
    box.stage_drawers()
    assert box.outcome("sdb") == {"state": "skipped", "packages": [], "reason": "no_model"}
    assert box.calls() == []


# --- הדיווח והחיווט ----------------------------------------------------------


def test_the_progress_report_carries_each_drawers_outcome(tmp_path):
    run = tmp_path / "run"
    for dev in ("sda", "sdb"):
        (run / "targets" / dev).mkdir(parents=True)
        for name, value in {"state": "done", "base": "0", "total": "10"}.items():
            (run / "targets" / dev / name).write_text(value + "\n")
    (run / "targets/sdb/drivers.json").write_text('{"state":"staged","packages":["m70q"]}\n')
    (run / "state").write_text("done\n")
    script = (f'export RUN_DIR={posix(run)!r}; . {posix(AGENT)}/lib/core/common.sh; '
              f'. {posix(AGENT)}/lib/core/progress.sh; build_progress ses_1 b4:2e:99:07:1a:c4')
    report = json.loads(subprocess.run([BASH, "-c", script], capture_output=True, text=True,
                                       stdin=subprocess.DEVNULL, cwd=str(AGENT.parent)).stdout)
    by_dev = {t["dev"]: t for t in report["targets"]}
    assert "drivers" not in by_dev["sda"]
    assert by_dev["sdb"]["drivers"] == {"state": "staged", "packages": ["m70q"]}
    assert "drivers" not in report


def test_the_cloner_stages_after_the_write_and_before_the_closing_report():
    main = (AGENT / "imagesrv-agent").read_text(encoding="utf-8")
    body = main[main.index("do_restore_drawers() {"):main.index("do_restore() {")]
    i_write = body.index("run_restore_drawers ")
    i_stage = body.index('stage_drawer_drivers "$2" "$RUN_DIR/manifest.json" $_disks')
    i_report = body.index('report_final "$_ppid" "$2"')
    assert i_write < i_stage < i_report


JQ = shutil.which("jq")


@pytest.mark.skipif(JQ is None, reason="jq — הפילטרים האמיתיים רצים במעבדה וב-CI")
def test_the_real_jq_filters_read_driver_ports_and_chosen(tmp_path):
    def run(doc: dict, path: str) -> str:
        f = tmp_path / "doc.json"
        f.write_text(json.dumps(doc), encoding="utf-8")
        script = f'. {posix(AGENT)}/lib/core/jsonq.sh; json_get {posix(f)!r} {path!r}'
        return subprocess.run([BASH, "-c", 'export PATH="/usr/bin:$PATH"; ' + script],
                              capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout
    ports = ".session.driver_ports // [] | .[]"
    assert run({"session": {"driver_ports": [2, 3]}}, ports).split() == ["2", "3"]
    assert run({"session": {"driver_ports": None}}, ports).strip() == ""
    assert run({"session": {"id": "ses_1"}}, ports).strip() == ""
    assert run({"ok": True, "chosen": None, "packages": []}, ".chosen").strip() == "null"
    assert run({"ok": True, "chosen": M70Q, "packages": []}, ".chosen").strip() != "null"
