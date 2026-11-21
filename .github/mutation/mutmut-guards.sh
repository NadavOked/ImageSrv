#!/usr/bin/env bash
# mutmut-guards.sh <out-dir> -- mutation testing of the principle-5 guards, for the
# nightly (.github/workflows/mutation.yml). A report: it lists every mutant the
# guards' own tests did not kill.
#
# A surviving mutant is a change to a guard that no test fails on -- a test that
# is missing (#3104). The guards, and the tests that are theirs:
#   server/agent/bootguard.py          the boot-loop budget (#75)
#   server/library/capture.py          sha256 and manifest gates (principle 6)
#   boot/grub_menu.py decide() & co.   local disk vs ImageSrv (principle 1)
# The three lists are the ones of the lab run that opened #3104 (the private
# tools/lab/mutmut-guards.sh); a private test keeps them equal.
#
# The checkout is copied to <out>/tree and a setup.cfg is written there.
# Exit 0 = the run happened (survivors are in <out>/survivors.txt and the summary);
# 1 = mutmut did not run, or ran without positive evidence: fewer than MIN_SELECTED
# mutants, or none killed -- then the tests never ran against a mutant.
set -euo pipefail

MUTMUT_VERSION="3.8.1"
MIN_SELECTED=500   # 934 on 13/11 (#3104)
TARGETS=(server/agent/bootguard.py server/library/capture.py boot/grub_menu.py)
MUTANTS=(
    'server.agent.bootguard.*'
    'server.library.capture.x_validate_manifest_files__*' 'server.library.capture.x_stream_files__*'
    'server.library.capture.x_upload_sha_problem__*' 'server.library.capture.x_reread_problem__*'
    'server.library.capture.x_manifest_problem__*' 'server.library.capture.x__sha256__*'
    'server.library.capture.x__verify__*' 'server.library.capture.x__verify_failed__*'
    'server.library.capture.x_run_verification__*'
    'boot.grub_menu.x_normalize_mac__*' 'boot.grub_menu.x_decide__*'
    'boot.grub_menu.x__is_actionable_task__*' 'boot.grub_menu.x__is_joinable_session__*'
)
TESTS=(
    tests/test_boot_loop.py tests/test_answer_failure_screen_2666.py tests/test_boot_preview_2550.py
    tests/test_agent_trust_1104.py
    tests/test_capture.py tests/test_capture_verify_1470.py tests/test_capture_empty_file_1830.py
    tests/test_capture_ntfs_inconsistent_2972.py tests/test_capture_path_guard.py
    tests/test_capture_upload_loop_1488.py tests/test_capture_room.py tests/test_capture_preflight.py
    tests/test_capture_gates.py
    tests/test_grub_menu.py tests/test_grub_menu_local_disk.py tests/test_visible_boot_menu.py
    tests/test_boot_single_entry.py tests/test_cloner_boot_path.py tests/test_unknown_screen_1802.py
)

die() { echo "::error::$*" >&2; exit 1; }
out=${1:?usage: mutmut-guards.sh <out-dir>}
CHILDREN=${CHILDREN:-4}
root=$(cd "$(dirname "$0")/../.." && pwd)
have=$(python -c 'import importlib.metadata as m; print(m.version("mutmut"))') || die "mutmut is not installed"
[[ "$have" == "$MUTMUT_VERSION" ]] || die "mutmut is $have; the guard lists were checked with $MUTMUT_VERSION"
for f in "${TARGETS[@]}" "${TESTS[@]}"; do [[ -f "$root/$f" ]] || die "$f does not exist: update the lists"; done

mkdir -p "$out"
out=$(cd "$out" && pwd)
[[ ! -e "$out/tree" ]] || die "$out/tree exists: use a fresh directory"
mkdir "$out/tree"
(cd "$root" && tar --exclude=.git -cf - .) | tar -C "$out/tree" -xf -
cd "$out/tree"
[[ ! -e setup.cfg ]] || die "the checkout has a setup.cfg; this tool writes its own"
{
    echo "[mutmut]"
    # forkserver: the capture verifier's threads left forked children stuck on a futex (13/11).
    echo "process_isolation=forkserver"
    echo "source_paths="; echo "    server"; echo "    boot"
    echo "only_mutate="; printf '    %s\n' "${TARGETS[@]}"
    echo "also_copy="
    for e in * .[!.]*; do
        case "$e" in server|boot|tests|mutants|setup.cfg|.git|'.[!.]*') ;; *) echo "    $e" ;; esac
    done
    echo "pytest_add_cli_args="; printf '    %s\n' -p no:cacheprovider -m 'not uinput and not vm_it'
    echo "pytest_add_cli_args_test_selection="; printf '    %s\n' "${TESTS[@]}"
} > "$out/setup.cfg"   # outside the tree: the `for e in *` above reads the tree
mv "$out/setup.cfg" setup.cfg

mkdir -p "$out/tmp"
set +e
TMPDIR="$out/tmp" mutmut run --max-children "$CHILDREN" "${MUTANTS[@]}" > "$out/run.log" 2>&1 < /dev/null
rc=$?
set -e
((rc == 0)) || { tail -30 "$out/run.log" >&2; die "mutmut run exited $rc"; }
mutmut results --all true > "$out/results-all.txt" 2>&1 || die "mutmut results failed"
python3 -I - "$out/results-all.txt" "$out/selected.txt" "${MUTANTS[@]}" <<'EOF'
import fnmatch, re, sys
src, dst, globs = sys.argv[1], sys.argv[2], sys.argv[3:]
rows = [m.groups() for line in open(src, encoding="utf-8")
        if (m := re.match(r"^\s*(\S+__mutmut_\d+): (.+?)\s*$", line))]
sel = [(n, s) for n, s in rows if any(fnmatch.fnmatch(n, g) for g in globs)]
with open(dst, "w", encoding="utf-8") as f:
    f.writelines(f"{n}: {s}\n" for n, s in sel)
EOF
total=$(wc -l < "$out/selected.txt")
killed=$(grep -c ': killed$' "$out/selected.txt" || [[ $? == 1 ]])
((total >= MIN_SELECTED)) || die "only $total mutants selected (at least $MIN_SELECTED expected): the globs or the targets moved"
((killed > 0)) || die "no selected mutant was killed: the tests never ran against a mutant"
grep -vE ': killed$' "$out/selected.txt" > "$out/survivors.txt" || [[ $? == 1 ]]
mkdir -p "$out/survivors"
while IFS=': ' read -r name _; do
    mutmut show "$name" > "$out/survivors/$name.diff" 2>&1 || echo "warning: mutmut show $name failed" >&2
done < "$out/survivors.txt"
{
    echo "## mutmut $MUTMUT_VERSION on the principle-5 guards -- $total mutants, $killed killed, $(wc -l < "$out/survivors.txt") not killed"
    echo
    echo '```'
    awk -F': ' '{print $2}' "$out/selected.txt" | sort | uniq -c
    echo '```'
    echo
    echo "Not killed, by function (each is a missing test -- #3104):"
    echo '```'
    sed -E 's/__mutmut_[0-9]+: / /' "$out/survivors.txt" | sort | uniq -c | sort -rn
    echo '```'
} | tee -a "${GITHUB_STEP_SUMMARY:-/dev/null}"
