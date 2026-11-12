# toolbins.sh -- catalog id -> the binaries it needs, and the selection file
# (#1050). POSIX sh (busybox ash); tools/build_initramfs.sh sources it too.
#
# The allow-list is server/tools_catalog.json (Nadav's 25 tools); the
# console writes the chosen ids to tools-selection.json (interfaces.md §23)
# and the builder copies that file into the initrd as
# /etc/imagesrv/tools-selection.json. Both sides read it through here:
# the builder packs tools_bins of every "build" id a module runs
# (tools_has_runner, #1104), the station offers only those ids. An id this table does not know is a stop, by name, on both
# sides -- never a guess from the catalog's display string ("nvme format"
# is not a file name).
#
# Named `toolbins.sh`, not `tools_bins.sh`: tools_load globs tools_*.sh
# as domain modules, and this is a table, not a module.

tools_bins() {
    # $1 = catalog id -> the real binaries, space separated, on stdout. An
    # empty answer is a tool that busybox already covers (dd). rc 1 = not a
    # catalog id.
    case "$1" in
        disk-blkdiscard)      printf 'blkdiscard\n' ;;
        disk-hdparm-erase)    printf 'hdparm\n' ;;
        disk-nvme-format)     printf 'nvme\n' ;;
        nvme-sanitize)        printf 'nvme\n' ;;
        nvme-sanitize-status) printf 'nvme\n' ;;
        zap-partition-table)  printf 'sgdisk\n' ;;
        disk-wipefs-all)      printf 'wipefs\n' ;;
        disk-dd-zero)         printf '\n' ;;                # busybox dd
        nwipe)                printf 'nwipe\n' ;;
        tpm-clear)            printf 'tpm2_clear\n' ;;
        win-users)            printf 'chntpw\n' ;;
        win-blank-password)   printf 'chntpw\n' ;;
        windows-ntfs-cat)     printf 'ntfscat\n' ;;
        ntfs-recover-scan)    printf 'ntfsundelete\n' ;;
        photorec)             printf 'photorec\n' ;;
        scalpel)              printf 'scalpel\n' ;;
        foremost)             printf 'foremost\n' ;;
        extundelete)          printf 'extundelete\n' ;;
        magicrescue)          printf 'magicrescue recoverjpeg\n' ;;
        unsquashfs)           printf 'unsquashfs\n' ;;
        bsdtar)               printf 'bsdtar\n' ;;
        ddrescue-image)       printf 'ddrescue\n' ;;
        safecopy)             printf 'safecopy\n' ;;
        esp-fsck-repair)      printf 'fsck.vfat\n' ;;
        testdisk)             printf 'testdisk\n' ;;
        *) return 1 ;;
    esac
}

tools_has_runner() {
    # $1 = catalog id -> rc 0 when a station module (tools_<domain>.sh) runs
    # it, rc 1 for the ids below. #1104: they are in the catalog, but no
    # module runs them yet -- testdisk/photorec need a wizard, the rest a
    # destination for what they recover (docs/tools/catalog-runners.md).
    # The plan does not pack them: a binary nothing on the station can start
    # is initrd weight. tests/test_tools_packing.py fails when this list and
    # the real modules drift.
    case "$1" in
        testdisk|photorec|scalpel|foremost|extundelete|magicrescue|ddrescue-image|safecopy) return 1 ;;
    esac
    return 0
}

_tools_selection_list() {
    # $1 = file, $2 = key (build / student) -> the ids, one per line. The
    # file is the server's own json.dump of {"schema", "build", "student"}
    # (interfaces.md §23): flat arrays of quoted ids, nothing nested -- so
    # whitespace is dropped and the array body split on commas. jq is not
    # used: this runs before the kiosk has anything else, and in the builder.
    # `|| :` -- grep's 1 on an empty list is an answer, and the builder runs
    # under `set -o pipefail`.
    tr -d ' \n\r\t' < "$1" | sed -n "s/.*\"$2\":\\[\\([^]]*\\)\\].*/\\1/p" \
        | tr ',' '\n' | sed 's/^"//; s/"$//' | grep -v '^$' || :
}

tools_selection_ids() {
    # The "build" ids of the selection file, one per line. rc 1 and a
    # stderr line when there is no file -- "no tools selected" and "the
    # file is missing" are both an empty list, but they are said apart.
    _sf=${TOOLS_SELECTION:-/etc/imagesrv/tools-selection.json}
    [ -f "$_sf" ] || { printf 'tools: no selection file at %s -- no tools offered\n' "$_sf" >&2; return 1; }
    _tools_selection_list "$_sf" build
}

tools_bins_plan() {
    # $1 = selection file -> every binary the "build" ids need, one per
    # line, deduplicated, in catalog order. rc 1 when a build id is not in
    # tools_bins (the id is named on stderr; nothing is printed on stdout
    # so a caller that packs the answer packs nothing). The "student" list
    # is parsed and counted only -- classroom stations are v2. A build id
    # that tools_has_runner refuses is not packed, and named on stderr.
    [ -f "$1" ] || { printf 'tools: selection file not found: %s\n' "$1" >&2; return 1; }
    _plan=""; _bad=""; _norun=""; _n=0
    for _id in $(_tools_selection_list "$1" build); do
        _n=$((_n + 1))
        _b=$(tools_bins "$_id") || { _bad="$_bad $_id"; continue; }
        tools_has_runner "$_id" || { _norun="$_norun $_id"; continue; }
        for _one in $_b; do
            case " $_plan " in *" $_one "*) ;; *) _plan="$_plan $_one" ;; esac
        done
    done
    if [ -n "$_bad" ]; then
        printf 'tools: selection names ids that are not in the catalog table:%s\n' "$_bad" >&2
        return 1
    fi
    [ -z "$_norun" ] ||
        printf 'tools: not packed -- no station module runs them yet (#1104):%s\n' "$_norun" >&2
    _s=$(_tools_selection_list "$1" student | wc -l | tr -d ' ')
    printf 'tools: selection %s: %s build ids, %s student ids (student ignored, v2)\n' "$1" "$_n" "$_s" >&2
    for _one in $_plan; do printf '%s\n' "$_one"; done
}
