"""#3131 (the touch map, installer): what the disk step says about each disk's health, and about the log.

C-DSK-11: the engine's inventory already carries ``smart=passed|failed|unknown`` (#3007), but the
screen said something only for "failed". A disk that passed and a disk that was never tested
looked the same -- silence. Now every verdict is a sentence (principle 5: "tested and fine" and
"not tested" are two different states); an older bridge (no field) or a value that is not one of
the three says nothing rather than "fine".

C-SYS-06: the install log lives in the live system's RAM and is not copied to the server. The
failure screen says so next to "פרטים טכניים", so the details are read before the restart.

The C function is run: a driver includes screens_install_setup.c (smart_words is static) and links the
rest of the GUI, as test_native_installer_gui._disk_contents does.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from test_native_installer_gui import GUI, NATIVE

DRIVER = r'''
#include "%(setup_c)s"
/* argv: the smart= values -- "-" is the empty one (an older bridge). One line each: tone|words */
int main(int argc, char **argv) {
    for (int i = 1; i < argc; i++) {
        InstallDisk d; memset(&d, 0, sizeof d);
        snprintf(d.smart, sizeof d.smart, "%%s", strcmp(argv[i], "-") ? argv[i] : "");
        int tone = -1; const char *w = smart_words(&d, &tone);
        printf("%%d|%%s\n", w ? tone : -1, w ? w : "");
    }
    return 0;
}
'''


def _words(tmp_path: Path, *values: str) -> list[str]:
    driver = tmp_path / "smart_words.c"
    driver.write_text(DRIVER % {"setup_c": (GUI / "src" / "screens_install_setup.c").as_posix()},
                      encoding="utf-8", newline="\n")
    binary = tmp_path / "smart-words"
    command = (
        'set -e; cd "$1"; flags="-O2 -Wall -Wextra -std=c11 -D_GNU_SOURCE $(pkg-config --cflags pangocairo cairo libdrm)"; '
        'srcs=$(make -s -f Makefile print-gui-src | tr " " "\\n" | grep -v -x -e src/main.c -e src/screens_install_setup.c); '
        'cc $flags -Dmain=imagesrv_gui_main -c src/main.c -o "$3/gui-main.o"; '
        'cc $flags -o "$2" "$4" "$3/gui-main.o" $srcs $(pkg-config --libs pangocairo cairo libdrm) -lm'
    )
    proc = subprocess.run(["bash", "-c", command, "_", str(GUI), str(binary), str(tmp_path), str(driver)],
                          capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    assert proc.returncode == 0, proc.stderr
    run = subprocess.run([str(binary), *values], capture_output=True, text=True, encoding="utf-8",
                         timeout=60, stdin=subprocess.DEVNULL)
    assert run.returncode == 0, run.stderr
    return run.stdout.splitlines()


@NATIVE
def test_every_verdict_is_a_sentence_and_tested_is_not_untested(tmp_path):
    got = _words(tmp_path, "passed", "failed", "unknown", "-", "ok", "PASSED")
    assert got == [
        "1|SMART תקין",
        "2|SMART נכשל",
        "0|SMART לא נבדק",
        "-1|",        # an older bridge: nothing, never "fine"
        "-1|",        # not a verdict the engine speaks
        "-1|",        # exact words only
    ]


def test_the_row_draws_it_for_every_disk_but_the_install_medium():
    source = (GUI / "src" / "screens_install_setup.c").read_text(encoding="utf-8")
    assert "const char *smart=d->iso?NULL:smart_words(d,&tone);" in source
    assert 'if(!strcmp(d->smart,"failed"))install_text_r(cr,t,"SMART נכשל"' not in source


def test_the_failure_screen_says_where_the_log_is():
    source = (GUI / "src" / "screens_install_final.c").read_text(encoding="utf-8")
    assert "היומן נשמר בזיכרון בלבד ויימחק בהפעלה מחדש" in source
