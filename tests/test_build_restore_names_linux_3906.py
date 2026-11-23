"""#3906: "שחזור לדיסק הזה" במחשב בנייה כותב את שם המחשב גם בלינוקס.

מסלול התחנה (`do_restore` ב-`imagesrv-agent`) קרא ל-`name_this_machine`;
מסלול מחשב הבנייה (`single_restore_run`) לא — המסך הציג את השלב "שם המכונה",
והדיסק עלה עם שם מכונת המקור (`LABEV etc_hostname=lnxsrc-ext4`).
השם במחשב בנייה הוא השם הרשום שלו (בלי קידומת סבב), אותו שם שהמפעיל
הקליד לאישור (`guibridge.sh`).

* ``name_this_machine`` עם שם מפורש כותב ``/etc/hostname`` ב-ext4: לפני/אחרי.
* ``single_restore_run`` האמיתי קורא לה עם השם הרשום והמניפסט שלו, ושם שלא
  נקרא אינו שקט (שורת יומן) ואינו מפיל את השחזור.

בקרה שלילית: `git checkout <base> -- agent/lib/restore/single_restore.sh
agent/lib/postdeploy/hostname.sh` — הטסטים נופלים על ערך.
"""

from __future__ import annotations

from agentlib import agent_sources
from native import requires_native
from test_captured_stdout import LINUX_PLAN
from test_hostname_linux_dual import box, sh, written
from test_single_restore_progress_gui import restore
from test_smart import BASH, posix

pytestmark = requires_native(("bash", BASH), "jq")

SOURCE_NAME = "lnxsrc-ext4"
SUCCESS = ('run_restore() { echo done > "$RUN_DIR/state"; target_set "$2" done; return 0; }; '
           'name_this_machine() { printf "%s|%s|%s|%s\\n" "$1" "$2" "$3" "$4" >> "$RUN_DIR/named"; }; ')


def test_name_this_machine_writes_the_given_name_into_the_linux_root(tmp_path):
    """לפני: שם המקור. אחרי: השם הרשום של מחשב הבנייה, מהמניפסט שנמסר."""
    script, run = box(tmp_path, [LINUX_PLAN], default_etc=True)
    etc = run / "linux" / "etc"
    etc.mkdir(parents=True)
    (etc / "hostname").write_text(SOURCE_NAME + "\n", encoding="utf-8")
    (run / "response.json").write_text("{}", encoding="utf-8")
    script += (f'{agent_sources("sysinfo", "diskinfo", "progress", "failmark")}'
               f'RESP={posix(run / "response.json")!r}; json_get() {{ echo null; }}; '
               'target_init sda 4096; target_set sda done; ')
    assert written(etc / "hostname") == SOURCE_NAME
    out = sh(script + 'name_this_machine sda ses_1 BLD1117 /dev/null; echo "rc=$?"')
    assert "rc=0" in out.stdout, (out.stdout, out.stderr)
    assert written(etc / "hostname") == "BLD1117", (out.stdout, out.stderr)
    assert "BLD1117" in (etc / "hosts").read_text(encoding="utf-8")


def test_the_build_machines_restore_names_the_machine(tmp_path):
    r = restore(tmp_path, 'echo \'{"name":"BLD1117"}\' > "$RUN_DIR/gui-restore-name.json"; ' + SUCCESS, gui=None)
    assert "RC=0" in r["out"], r["out"]
    assert (r["run"] / "named").exists(), r["log"]
    named = (r["run"] / "named").read_text(encoding="utf-8").strip().splitlines()
    assert len(named) == 1, named
    disk, _ses, name, manifest = named[0].split("|")
    assert (disk, name) == ("sda", "BLD1117")
    assert manifest.endswith("/sr-manifest.json"), manifest


def test_no_registered_name_is_said_and_does_not_fail_the_restore(tmp_path):
    r = restore(tmp_path, SUCCESS, gui=None)
    assert "RC=0" in r["out"], r["out"]
    assert not (r["run"] / "named").exists()
    assert "no registered name" in r["log"], r["log"]
