"""#3268 (Nadav 18/11) -- the waiting-screen write check, the sustained part.

A cheap SSD takes the 12MiB of the short check in its write cache (APS-SL3N-512: 209 MB/s there, 43-55 MB/s
written for minutes), so it passed the floor and stayed green. The check now also writes ``WRITECHECK_LONG_MIB``
in a row and takes the rate from the last half -- with a copy on the server and a verified put-back, like the short one.

The drive is modelled: a ``dd`` shim advances ``/proc/uptime`` for every write to a disk image (``model`` = "cache
in MiB, MB/s inside it, MB/s after it"), so the check measures what a real drive would give, with the clock the
agent really reads. A ``curl`` shim is a file-based server with the contract of ``server/disks/write_backup.py``
(its ``/long`` route, which test_write_backup_long_3268.py runs for real). Proved from outside the code under test:
the image's own bytes, before and after.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
from test_idlecheck_3268 import DRIVER, LIBS, MIB, _make, _res, _sha, _writes
from test_smart import BASH, posix  # noqa: F401 -- the same bash, declared skip without it

from agentlib import flat_lib
from native import requires_native

pytestmark = requires_native(("bash", BASH), ("jq", shutil.which("jq")), ("sha256sum", shutil.which("sha256sum")),
                             ("awk", shutil.which("awk")))

LONG = 64          # MiB the sustained part writes in these tests (the default is 256: the disks here are small)
DISK = 160         # MiB: the sustained part needs twice what it writes

DD_SHIM = r'''#!/bin/bash
args=(); out=""; src=""; bs=512; cnt=1
for a in "$@"; do
    case "$a" in iflag=direct|oflag=direct) continue ;; of=*) out=${a#of=} ;; if=*) src=${a#if=} ;; bs=*) bs=${a#bs=} ;; count=*) cnt=${a#count=} ;; conv=fsync) a=conv=fsync,notrunc ;; esac
    args+=("$a")
done
case "$out" in "$DEVROOT"/*)
    echo "$out" >> "$SHIM_LOG/writes"; n=$((bs * cnt)); echo "$n" >> "$SHIM_LOG/wbytes"
    if [ -e "$SHIM_LOG/failafter" ] && [ "${src##*/}" = pat ]; then   # the sustained write: the disk takes N chunks, then refuses
        left=$(cat "$SHIM_LOG/failafter")
        [ "$left" -gt 0 ] || { echo "dd: error writing '$out': Input/output error" >&2; exit 1; }
        echo $((left - 1)) > "$SHIM_LOG/failafter"
    fi
    if [ -f "$SHIM_LOG/model" ]; then   # the drive: the first CACHE MiB written at FAST MB/s, the rest at SLOW MB/s; the clock moves by it
        read -r cache fast slow < "$SHIM_LOG/model"
        cum=$(cat "$SHIM_LOG/cum" 2> /dev/null || echo 0); u=$(cat "$SHIM_LOG/uptime_f" 2> /dev/null || echo 100)
        u=$(awk -v c="$cum" -v n="$n" -v cache=$((cache * 1048576)) -v f="$fast" -v s="$slow" -v u="$u" 'BEGIN {
            fb = cache - c; if (fb < 0) fb = 0; if (fb > n) fb = n; printf "%.6f", u + fb / (f * 1e6) + (n - fb) / (s * 1e6) }')
        echo "$u" > "$SHIM_LOG/uptime_f"; echo $((cum + n)) > "$SHIM_LOG/cum"
        printf '%.2f %.2f\n' "$u" "$u" > "$SYSROOT/proc/uptime"
    fi ;;
esac
exec "$REAL_DD" "${args[@]}"
'''

CURL_SHIM = r'''#!/bin/bash
M=GET; BODY=""; OUT=""; URL=""
while [ $# -gt 0 ]; do
    case "$1" in -X) M=$2; shift ;; --data-binary) BODY=${2#@}; shift ;; -T) [ "$2" = - ] && { BODY=$SHIM_LOG/stdin.body; cat > "$BODY"; }; shift ;;
        -o) OUT=$2; shift ;; --max-time|-H) shift ;; -*) ;; *) URL=$1 ;; esac
    shift
done
q=${URL#*\?}; path=${URL%%\?*}
param() { printf '%s' "$q" | tr '&' '\n' | sed -n "s/^$1=//p"; }
S=$(param serial); D=$FAKE_SRV
ID=0123456789abcdef0123456789abcdef
if [ "$M" = POST ] && [ "${path%/stale}" != "$path" ]; then
    [ -e "$D/$S.meta" ] || exit 22
    param blocks > "$D/$S.stale"; printf '{"ok":true}'
elif [ "$M" = POST ] && [ "${path%/long}" != "$path" ]; then
    echo "$S" >> "$SHIM_LOG/longposts"
    [ ! -e "$D/refuse_long" ] || exit 22
    [ ! -e "$D/$S.bin" ] || exit 22
    sha=$(sha256sum "$BODY" | cut -d' ' -f1); [ "$sha" = "$(param sha256)" ] || exit 22
    len=$(param regions | cut -d: -f2); pat=$(param pattern)
    [ "$(stat -c %s "$BODY")" = $((len + pat)) ] || exit 22   # the server counts the bytes: a read that came up short is refused
    cp "$BODY" "$D/$S.bin"; echo "$(param size) $(param regions) $sha - long $pat" > "$D/$S.meta"
    printf '{"ok":true,"id":"%s","sha256":"%s"}' "$ID" "$sha"
elif [ "$M" = POST ]; then
    echo "$S" >> "$SHIM_LOG/posts"
    [ ! -e "$D/refuse" ] || exit 22
    [ ! -e "$D/$S.bin" ] || exit 22
    sha=$(sha256sum "$BODY" | cut -d' ' -f1)
    [ "$sha" = "$(param sha256)" ] || exit 22
    cp "$BODY" "$D/$S.bin"; echo "$(param size) $(param regions) $sha $(param expect)" > "$D/$S.meta"
    printf '{"ok":true,"id":"%s","sha256":"%s"}' "$ID" "$sha"
elif [ "$M" = DELETE ]; then
    rm -f "$D/$S.bin" "$D/$S.meta" "$D/$S.stale"; printf '{"ok":true}'
elif [ "${path%/blob}" != "$path" ]; then
    if [ -n "$OUT" ]; then cp "$D/$S.bin" "$OUT"; else cat "$D/$S.bin"; fi
elif [ -e "$D/$S.meta" ]; then
    read -r size regions sha x kind pat < "$D/$S.meta"; e=""; [ "$x" = - ] || [ -z "$x" ] || e=",\"expect\":\"$x\""
    [ -z "$kind" ] || e="$e,\"kind\":\"$kind\",\"pattern\":$pat"
    printf '{"ok":true,"backups":[{"id":"%s","size":%s,"regions":"%s","sha256":"%s"%s}]}' "$ID" "$size" "$regions" "$sha" "$e"
else
    printf '{"ok":true,"backups":[]}'
fi
'''


def _long(tmp_path: Path, model: str | None = None, size_mib: int = DISK) -> Path:
    img = _make(tmp_path, size_mib=size_mib)
    for name, body in (("dd", DD_SHIM), ("curl", CURL_SHIM)):
        p = tmp_path / "bin" / name
        p.write_text(body, newline="\n")
        p.chmod(p.stat().st_mode | stat.S_IEXEC)
    (tmp_path / "proc/meminfo").write_text("MemAvailable:    8000000 kB\n")
    if model:
        (tmp_path / "model").write_text(model)
    return img


def _run(t: Path, script: str, long_mib: int = LONG) -> subprocess.CompletedProcess:
    driver = DRIVER % {"libs": " ".join((*LIBS, "idlelong")), "lib": posix(flat_lib())}
    driver = driver.replace("set +e\n", f"export WRITECHECK_LONG_MIB={long_mib} IDLE_WC_LONG_WAIT_S=60 IDLE_WC_LONG_NET_S=30\nset +e\n")
    return subprocess.run([BASH, "-c", driver, "x", posix(t), script], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, timeout=240)


def _written(t: Path) -> int:
    f = t / "wbytes"
    return sum(int(x) for x in f.read_text().split()) if f.exists() else 0


def _field(t: Path) -> str:
    return _run(t, "idle_wc_field sdx").stdout.strip()


def test_a_drive_with_a_short_cache_is_slow_before_the_round_and_the_disk_is_back_byte_for_byte(tmp_path):
    # 52MiB at 200 MB/s, then 40 MB/s: the 12MiB of the short check is all cache -- 200 MB/s, green, before #3268
    img = _long(tmp_path, model="52 200 40")
    before = _sha(img)
    out = _run(tmp_path, "idle_wc_disk sdx")
    assert out.returncode == 0, out.stderr
    r = _res(tmp_path)
    assert r["state"] == "ok", (r, out.stderr)
    assert 38 <= int(r["mbps"]) <= 41, f"the rate is the last half's, not the cache's: {r}"
    first = int(r["reason"].split("בחצי האחרון (")[1].split(" ")[0])
    assert first > int(r["mbps"]) + 30, f"the first half is what the cache took, and the words say so: {r['reason']}"
    assert f"{LONG}MiB" in r["reason"]
    field = _field(tmp_path)
    assert re.fullmatch(r"slow\|איטי — (3[89]|4[01]) MB/s, מתחת לסף 74 MB/s", field), field   # red, the number measured, the screen's own words
    assert _written(tmp_path) >= LONG * MIB, "the check did not write the sustained part: the verdict above would prove nothing"
    assert _sha(img) == before, "the original bytes are not back"
    assert not list((tmp_path / "srv").iterdir()), "the copy is still on the server after a verified restore"
    assert not (tmp_path / "run/idlecheck/long.sdx").exists(), "the work dir (the pattern) is left in RAM"


def test_a_drive_that_holds_its_speed_stays_green_and_a_guard_for_the_new_check(tmp_path):
    img = _long(tmp_path, model="1000 200 200")
    before = _sha(img)
    assert _run(tmp_path, "idle_wc_disk sdx").returncode == 0
    r = _res(tmp_path)
    assert r["state"] == "ok" and int(r["mbps"]) >= 190, r
    assert _field(tmp_path) == "ok|"
    assert _sha(img) == before


def test_no_confirmation_for_the_sustained_copy_means_no_sustained_write(tmp_path):
    img = _long(tmp_path, model="52 200 40")
    before = _sha(img)
    (tmp_path / "srv" / "refuse_long").write_text("")
    assert _run(tmp_path, "idle_wc_disk sdx").returncode == 0
    r = _res(tmp_path)
    assert r["state"] == "unchecked" and "השרת לא אישר שמירת עותק של הכתיבה המתמשכת" in r["reason"], r
    assert _written(tmp_path) < 40 * MIB, "it wrote the sustained part without a stored copy"
    assert _sha(img) == before
    assert _field(tmp_path).startswith("unchecked|"), "a disk whose sustained check did not run must never be green"


def test_a_disk_that_stops_taking_writes_in_the_middle_fails_by_name_and_is_put_back(tmp_path):
    img = _long(tmp_path, model="1000 200 200")
    before = _sha(img)
    (tmp_path / "failafter").write_text("3")   # three chunks of 8MiB, then I/O error
    assert _run(tmp_path, "idle_wc_disk sdx").returncode == 0
    r = _res(tmp_path)
    assert r["state"] == "failed" and "כתיבה מתמשכת" in r["reason"] and "Input/output error" in r["reason"], r
    assert _sha(img) == before, "the half-written region was not put back"
    assert not list((tmp_path / "srv").iterdir())


def test_a_put_back_that_could_not_be_verified_is_failed_by_name_and_the_copy_stays(tmp_path):
    img = _long(tmp_path, model="1000 200 200")
    before = _sha(img)
    assert _run(tmp_path, "_il_putback() { return 1; }; idle_wc_disk sdx").returncode == 0
    r = _res(tmp_path)
    assert r["state"] == "failed" and "לא אומת" in r["reason"], r
    assert _sha(img) != before, "the fixture did not leave the pattern on the disk"
    assert len(list((tmp_path / "srv").glob("*.bin"))) == 1, "the copy was dropped although the disk was not put back"
    assert not (tmp_path / "run/idlecheck/settled.sdx").exists(), "a disk with the pattern on it was called clean"


def test_a_cut_in_the_sustained_write_is_put_back_at_the_next_boot(tmp_path):
    img = _long(tmp_path, model="1000 200 200")
    before = _sha(img)
    (tmp_path / "failafter").write_text("3")   # the region is half the pattern, half the original
    assert _run(tmp_path, "_il_putback() { return 1; }; idle_wc_disk sdx").returncode == 0
    assert _sha(img) != before
    (tmp_path / "failafter").unlink()
    out = _run(tmp_path, "idle_wc_recover sdx; echo RC=$?")   # the next boot: a fresh shell, nothing remembered but the server's copy
    assert "RC=0" in out.stdout, (out.stdout, out.stderr)
    assert _sha(img) == before, "the recovery did not bring the original bytes back"
    assert not list((tmp_path / "srv").iterdir())
    assert "WRITE CHECK RECOVERY" in (tmp_path / "run" / "agent.log").read_text()
    assert (tmp_path / "run/idlecheck/settled.sdx").exists()


def test_a_recovery_puts_back_only_what_is_ours_and_keeps_the_copy_when_something_else_is_there(tmp_path):
    img = _long(tmp_path, model="1000 200 200")
    before = img.read_bytes()
    assert _run(tmp_path, "_il_putback() { return 1; }; idle_wc_disk sdx").returncode == 0   # the whole region is the pattern now
    foreign = os.urandom(4096)   # an image written since: not the original, not the pattern
    at = (DISK // 2 + 10) * MIB + 8192
    with open(img, "r+b") as f:
        f.seek(at)
        f.write(foreign)
    assert _run(tmp_path, "idle_wc_recover sdx; echo RC=$?").returncode == 0
    now = img.read_bytes()
    assert now[at:at + 4096] == foreign, "the recovery wrote over something that was neither the original nor the pattern"
    assert now[:at] == before[:at] and now[at + 4096:] == before[at + 4096:], "the rest was not put back"
    assert (tmp_path / "srv" / "SER1.stale").read_text().strip() == "1", "the copy was not marked stale"
    assert len(list((tmp_path / "srv").glob("*.bin"))) == 1, "a copy with a block that is not ours was dropped"


def test_a_recovery_that_cannot_hold_the_copy_in_ram_puts_nothing_back(tmp_path):
    img = _long(tmp_path, model="1000 200 200")
    before = _sha(img)
    assert _run(tmp_path, "_il_putback() { return 1; }; idle_wc_disk sdx").returncode == 0
    after_cut = _sha(img)
    (tmp_path / "proc/meminfo").write_text("MemAvailable:      70000 kB\n")
    out = _run(tmp_path, "idle_wc_recover sdx; echo RC=$?")
    assert "RC=1" in out.stdout, (out.stdout, out.stderr)
    assert _sha(img) == after_cut and after_cut != before, "it wrote without room to hold the copy"
    assert len(list((tmp_path / "srv").glob("*.bin"))) == 1
    (tmp_path / "proc/meminfo").unlink()   # not read = not known: the same refusal, never "enough"
    assert "RC=1" in _run(tmp_path, "idle_wc_recover sdx; echo RC=$?").stdout


def test_a_disk_too_small_for_the_sustained_part_says_it_was_short_only(tmp_path):
    img = _long(tmp_path, size_mib=64)
    before = _sha(img)
    assert _run(tmp_path, "idle_wc_disk sdx", long_mib=256).returncode == 0
    r = _res(tmp_path)
    assert r["state"] == "ok" and "בדיקה קצרה בלבד" in r["reason"], r
    assert not (tmp_path / "longposts").exists() and _sha(img) == before


def test_zero_switches_the_sustained_part_off(tmp_path):
    _long(tmp_path, model="52 200 40")
    assert _run(tmp_path, "idle_wc_disk sdx", long_mib=0).returncode == 0
    assert _res(tmp_path)["state"] == "ok" and not (tmp_path / "longposts").exists()
    assert _written(tmp_path) < 40 * MIB


def test_the_drawers_are_checked_side_by_side_not_one_after_the_other(tmp_path):
    _long(tmp_path, size_mib=16)
    for d in ("sdy", "sdz"):
        (tmp_path / "dev" / d).write_bytes(b"")
        shutil.copytree(tmp_path / "sys/block/sdx", tmp_path / "sys/block" / d, dirs_exist_ok=True)
        (tmp_path / "sys/block" / d / "device/serial").write_text("SER" + d[-1])
    stub = ('idle_wc_disk() { echo "s $1 $(date +%s.%N)" >> $SHIM_LOG/t; sleep 2; echo "e $1 $(date +%s.%N)" >> $SHIM_LOG/t; }; '
            "idle_wc_scan; wait")
    assert _run(tmp_path, stub).returncode == 0
    rows = [ln.split() for ln in (tmp_path / "t").read_text().splitlines()]
    starts = [float(r[2]) for r in rows if r[0] == "s"]
    ends = [float(r[2]) for r in rows if r[0] == "e"]
    assert len(starts) == 3 and len(ends) == 3, rows
    assert max(starts) < min(ends), f"one disk was checked after another finished: {rows}"


def test_the_screen_says_it_is_measuring_speed_not_just_checking():
    src = (Path(__file__).resolve().parent.parent / "native-gui/src/screens_standby.c").read_text(encoding="utf-8")
    assert 'why = "בודק מהירות…"' in src and 'why = "נבדק…"' not in src
