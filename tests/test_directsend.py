"""#715 — הפצה ישירה ממחשב הבנייה: הסוכן כמקור הגל.

השרת נשאר המתזמר; מחשב הבנייה קורא את הדיסק שלו ומשדר אותו. #3173: הגל הוא של מנוע
החתיכות בלבד (directchunk.sh, test_directchunk_agent_3161.py) -- הדגלים של השולח הישן
והבדיקות שלהם יצאו איתו. מה שנשאר כאן הוא הקופסה שכל בדיקות ההפצה הישירה נשענות עליה
(`direct_run`), והמסלול שלפני הגל ואחריו: קריאה 1 בלי העלאה, המניפסט, ההמתנה לגל, ומה
שהמקור עושה כשהגל לא בא.

`udp-sender` מזויף נשאר בקופסה בכוונה: אם קוד כלשהו עוד מריץ אותו, `udp.argv` נכתב
ובדיקה שבודקת שהוא לא קיים נופלת.
"""

from __future__ import annotations

from test_agent import BASH, posix
from test_timeouts import make_stubs
from native import requires_native
from agentlib import flat_lib

pytestmark = requires_native(("bash", BASH))


# --- direct_send_run: שתי קריאות, מניפסט בלי העלאה, sha על החוט ---------------

import json
import shutil

from test_agent import REPO
from test_capture_refusals import CLEAN_PARTITION, GPT_DISK, ONE_PARTITION
from test_timeouts import run_sh

MAC = "aa:bb:cc:00:00:10"
TASK = "tsk_715"

#: partclone מזויף: מוציא 4096 בייטים שנקבעים ב-$PCL_BYTE; קריאה שנייה
#: יכולה להוציא משהו אחר (PCL_SECOND) כדי לדמות דיסק שהשתנה בין הקריאות.
PARTCLONE = (
    '#!/bin/sh\n'
    'n=$(cat "$PCL_COUNT" 2>/dev/null || echo 0); n=$((n + 1)); echo "$n" > "$PCL_COUNT"\n'
    '[ -z "${PCL_ARGV:-}" ] || echo "partclone $*" >> "$PCL_ARGV"\n'   # #1171: the node each read read
    'b="$PCL_BYTE"; [ "$n" -ge 2 ] && [ -n "$PCL_SECOND" ] && b="$PCL_SECOND"\n'
    'head -c 4096 /dev/zero | tr "\\000" "$b"\n'
    'exit 0\n'
)

#: udp-sender מזויף: רושם argv, בולע את הזרם, ומדפיס את שורת ההתחלה
#: כמו האמיתי — אלא אם UDP_SILENT: אז יוצא 0 בלי לשדר (#438).
UDP_SENDER = (
    '#!/bin/sh\n'
    'printf "%s\n" "$@" >> "$UDP_ARGV"\n'
    'cat > /dev/null\n'
    '[ -n "$UDP_SILENT" ] || echo "Starting transfer: 0000000"\n'
    'exit 0\n'
)

#: curl מזויף: רושם את ה-argv שלו (איזה URL, אילו דגלים) ועונה 0.
CURL_RECORD = (
    '#!/bin/sh\n'
    'printf "%s\n" "$@" >> "$CURL_ARGV"\n'
    'echo "---" >> "$CURL_ARGV"\n'
    'exit 0\n'
)


#: jq אינו מותקן בתחנת הפיתוח (ווינדוס). במקום דילוג שקט — תחליף צר
#: בפייתון, **רק** לביטויים שמסלול ההפצה הישירה מריץ (json_get, manifest_plan,
#: .partitions|length, בלוק המולטיקאסט); כל ביטוי אחר יוצא 5 כמו jq על
#: שגיאה. במעבדה (jq אמיתי ב-PATH) התחליף אינו נכנס לתמונה.
JQ_SHIM_PY = (REPO / "tests" / "jq_shim.py")
JQ_STUB = '#!/bin/sh\nexec python "' + posix(JQ_SHIM_PY) + '" "$@"\n'


def _answer(session_state="running", task=TASK, receivers=2, engine="chunk"):
    """#3173: the chunk engine's task (task.direct.engine); `engine=None` = a server that named none."""
    direct = {"session_id": "ses_1", "session_state": session_state, "receivers": receivers}
    if engine is not None:
        direct["engine"] = engine
    return {"schema": 1, "known": True, "role": "build", "task": {
        "id": task, "type": "direct_send", "disk": "sda", "image_id": "live_aabbcc",
        "token": "t" * 48, "direct": direct}}


#: #3173: the chunk wave in a line, for the tests of what comes before and after it (the wave itself is
#: test_directchunk_agent_3161.py's, with the real directchunk.sh). It is a pass as the real one reads: each
#: streamed partition read once more -- the view read 1 read (lc_src_node), read 1's own flags, its own cut --
#: and hashed against the manifest; bytes that differ fail the source by name, as the real pass does.
CHUNK_RUN = (
    'direct_chunk_run() { manifest_plan "$3" > "$RUN_DIR/dstub.plan" || return 1; '
    '_st=$(json_get "$3" ".total_compressed_bytes"); case "$_st" in ""|*[!0-9]*) _st=0 ;; esac; '
    'target_init "$2" "$_st"; capture_unhib_note "$2" || return 1; echo sending > "$RUN_DIR/state"; : > "$RUN_DIR/chunk.run"; '
    'while IFS="|" read -r _si _sg _sr _sfs _ss _sz _sf _sh _srest <&3; do case "$_sf" in ""|null) continue ;; esac; '
    '_sn=$(lc_src_node "$2" "$_si"); _sp=$(partclone_for_fs "$_sfs"); '
    '_sx=$("$_sp" $(partclone_mode "$_sp" -c) $(capture_pcl_flags "$_sp" "$_sfs") -s "$_sn" -L "$RUN_DIR/targets/$2/partclone.log" 2>/dev/null '
    '| capture_cut "$_sp" "$RUN_DIR/didx.$_si" 2>/dev/null | sha256sum | cut -c1-64); '
    '[ "$_sx" = "$_sh" ] || { _direct_failed "$2" "מחיצה $_si: הבייטים שנשלחו שונים מהמניפסט"; return 1; }; '
    'target_partition_done "$2"; done 3< "$RUN_DIR/dstub.plan"; return 0; }; '
)


#: sgdisk עם שתי מחיצות — ESP ואחריה מחיצת נתונים — לשידור של שני זרמים.
TWO_PARTITIONS = (
    '#!/bin/sh\n'
    'if [ "$1" = "-i" ]; then\n'
    '  case "$2" in\n'
    '    1) echo "Partition GUID code: C12A7328-F81F-11D2-BA4B-00A0C93EC93B (EFI)"\n'
    '       echo "Partition unique GUID: 4C7B1E00-0000-4000-8000-000000000002"\n'
    '       echo "First sector: 2048 (at 1024 KiB)"\n'
    '       echo "Partition size: 204800 sectors (100.0 MiB)"\n'
    '       echo "Attribute flags: 0000000000000000"\n'
    '       echo "Partition name: \'EFI system partition\'" ;;\n'
    '    2) echo "Partition GUID code: EBD0A0A2-B9E5-4433-87C0-68B6B72699C7 (Microsoft basic data)"\n'
    '       echo "Partition unique GUID: 4C7B1E00-0000-4000-8000-000000000003"\n'
    '       echo "First sector: 206848 (at 101.0 MiB)"\n'
    '       echo "Partition size: 204800 sectors (100.0 MiB)"\n'
    '       echo "Attribute flags: 0000000000000000"\n'
    '       echo "Partition name: \'data\'" ;;\n'
    '  esac\n'
    '  exit 0\n'
    'fi\n'
    'echo "Disk identifier (GUID): 4C7B1E00-0000-4000-8000-000000000001"\n'
    'echo "Number  Start (sector)    End (sector)  Size       Code  Name"\n'
    'echo "   1            2048          206847   100.0 MiB   EF00  EFI system"\n'
    'echo "   2          206848          411647   100.0 MiB   0700  data"\n'
)


def direct_run(tmp_path, *, answer=None, env=None, udp_silent=False, partitions=1,
               timeout=45, udp_sender=None, extra_stubs=None, pre="", call="direct_send_run", chunk_run=True):
    """`chunk_run`: the wave is CHUNK_RUN's (default), or the real directchunk.sh's (False)."""
    box = tmp_path / "box"
    dev, run = box / "dev", box / "run"
    dev.mkdir(parents=True); run.mkdir(parents=True)
    (dev / "sda").write_bytes(GPT_DISK + bytes(8192 - len(GPT_DISK)))   # #1808: the RAID gate reads the last 16 sectors
    for n in range(1, partitions + 1):
        (dev / f"sda{n}").write_bytes(CLEAN_PARTITION)   # #1823: a clean FAT when a test says vfat
    queue = box / "sys/block/sda/queue"; queue.mkdir(parents=True)
    (queue / "logical_block_size").write_text("512\n")
    (box / "sys/block/sda/size").write_text("16\n")
    nodes = box / "nodes"
    nodes.write_text("".join(f"{posix(dev)}/sda{s}\n" for s in ["", *range(1, partitions + 1)]),
                     newline="\n")
    (box / "answer.json").write_text(json.dumps(answer or _answer()), encoding="utf-8")
    envs = {"RUN_DIR": posix(run), "DEVROOT": posix(dev), "SYSROOT": posix(box),
            "SERVER": "http://s", "IFACE": "eth0", "MAC": MAC, "TASK_TOKEN": "t" * 48,
            "RESP": posix(box / "resp.json"), "IMAGESRV_TEST": "1",
            "PCL_COUNT": posix(box / "pcl.count"), "PCL_BYTE": "A", "PCL_SECOND": "",
            "UDP_ARGV": posix(box / "udp.argv"), "CURL_ARGV": posix(box / "curl.argv"),
            "UDP_SILENT": "1" if udp_silent else "", "DIRECT_POLL_S": "0",
            **(env or {})}
    exports = "".join(f"export {k}={v!r}; " for k, v in envs.items())
    stubs = {"sgdisk": TWO_PARTITIONS if partitions == 2 else ONE_PARTITION,
             "partclone.dd": PARTCLONE, "udp-sender": udp_sender or UDP_SENDER,
             "curl": CURL_RECORD, **(extra_stubs or {})}
    if shutil.which("jq") is None:
        stubs["jq"] = JQ_STUB
    if shutil.which("blkid") is None:   # #1905: a missing blkid is "unreadable"; the real one says rc 2 on these files
        stubs.setdefault("blkid", "#!/bin/sh\nexit 2\n")
    libs =" ".join(f"[ ! -f {posix(flat_lib())}/{n}.sh ] || . {posix(flat_lib())}/{n}.sh;" if n == "captureview" else f". {posix(flat_lib())}/{n}.sh;" for n in (
        "common", "sysinfo", "diskinfo", "waits", "jsonq", "gptboot", "progress", "failmark", "restore", "manifest",
        "bootca", "hibernation", "ntfsunhib", "captureview", "shrinkplan", "shrink", "lcsnap", "writemark", "diskfit", "capturegate", "capture", "capturesink", "directsend", "directchunk", "directserve", "ui"))   # #87/#929: shrink*.sh לפני capture.sh, כמו בסוכן; #1217: capturesink.sh; #1466: lcsnap.sh (lc_src_node), כמו בסוכן
    out = run_sh(
        make_stubs(box / "stubs", stubs) + exports + libs
        + "raid_member_reason() { :; }; "   # #1808: the RAID gate has its own tests (test_capture_gates.py); these disks are files
        + f' node_is_block() {{ grep -qxF "$1" {posix(nodes)!r} 2>/dev/null; }}; '
        f'send_hello() {{ cp {posix(box / "answer.json")!r} "$RESP"; }}; '
        + (CHUNK_RUN if chunk_run else "")   # #3173: the wave, before `pre` -- a test's own wins
        + pre   # #1171: after the libraries, so it can replace one of their functions
        + f"{call} {TASK} sda > {posix(box)}/direct.out 2>&1; "
        'echo "rc=$?"; '
        f"build_progress '' {MAC} {TASK} > {posix(box)}/progress.json",
        timeout=timeout,
    )
    return box, run, out


def _progress(box):
    return json.loads((box / "progress.json").read_text(encoding="utf-8"))


def test_bytes_that_differ_from_the_manifest_fail_at_the_source_by_name(tmp_path):
    box, run, out = direct_run(tmp_path, env={"PCL_SECOND": "B"})
    assert out.strip().endswith("rc=1"), out
    report = _progress(box)
    assert "הבייטים שנשלחו שונים מהמניפסט" in report["targets"][0]["error"]   # #1503


def test_a_closed_wave_or_a_task_that_is_no_longer_ours_does_not_send(tmp_path):
    for name, answer in (("closed", _answer("closed")), ("other", _answer(task="tsk_x"))):
        box, run, out = direct_run(tmp_path / name, answer=answer)
        assert out.strip().endswith("rc=1"), out
        assert not (box / "udp.argv").exists() and not (run / "chunk.run").exists(), name
        assert "הגל לא התחיל" in _progress(box)["targets"][0]["error"]   # #1503


#: partclone שהזרם שלו הוא שורת הפקודה שקיבל: שתי קריאות עם דגלים שונים
#: מייצרות בייטים שונים -- בדיוק כמו partclone אמיתי עם ובלי -a0 (#1574).
PARTCLONE_ARGV = (
    '#!/bin/sh\n'
    'printf "%s\\n" "$*" >> "$PCL_ARGS"\n'
    'printf "%s\\n" "$*"\n'
    'head -c 4096 /dev/zero\n'
    'exit 0\n'
)


def test_both_reads_run_partclone_with_the_same_flags(tmp_path):
    """#1574, ברזל 17/10: #1472 נתן לקריאה הראשונה (המניפסט) `-a0 -z 524288`
    ולשנייה (השידור) את ברירות המחדל של partclone. partclone עם checksum
    וחוצץ אחרים הוא זרם אחר, ולכן **כל** הפצה ישירה נכשלה במחיצה 1 עם
    "הבייטים שנשלחו שונים מהמניפסט". כאן partclone מזויף מזרים את ה-argv
    שלו -- הקריאות זהות רק אם הדגלים זהים.

    **בקרה שלילית:** על directsend.sh/capture.sh של main rc=1 והשורות שונות."""
    args = tmp_path / "pcl.args"
    box, run, out = direct_run(tmp_path, env={"PCL_ARGS": posix(args)},
                               extra_stubs={"partclone.fat": PARTCLONE_ARGV},
                               pre="_fs_of() { echo vfat; }; ")
    lines = args.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2, lines
    assert lines[0] == lines[1], lines
    assert "-a0" in lines[0].split(), lines   # #1472's flags kept, in both
    assert out.strip().endswith("rc=0"), out + (run / "agent.log").read_text("utf-8", "replace")
