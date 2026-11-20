# reportseq.sh -- #3331 (I14): the order of the progress reports (interfaces.md section 4,
# "report order"). POSIX sh (busybox ash).
#
# A report that was held up on the network (or sent again after a reboot) must not arrive as
# news: the server drops a progress report whose (boot_id, seq) is older than the one it has
# taken (server/agent/reportorder.py). So every progress report says which boot it comes from
# and how far into it:
#
#   "boot_id":"<kernel's id of this boot>"   /proc/sys/kernel/random/boot_id, the form hello sends
#   "seq":<n>                                 hundredths of a second since this boot (/proc/uptime)
#
# seq is the uptime and not a counter in a file, on purpose: a counter that lives in $RUN_DIR
# starts again at 1 when the directory is cleared or the agent restarts inside the same boot, and
# every report after that would look OLD to the server -- a live machine silently dropped. The
# uptime only rises, needs no state, and two reports are never built within the same hundredth.
# It is read with `read` (no fork): a report is built every 2 seconds.
#
# Nothing readable -- no boot id in the kernel's form, or no uptime -- prints NOTHING: the report
# goes without the pair, and the server takes it as before ("no seq"). A seq is never invented.

report_seq_json() {
    # -> ,"boot_id":"...","seq":N   (or nothing)
    _rq_b=$(cat "${SYSROOT:-}/proc/sys/kernel/random/boot_id" 2>/dev/null)
    case "$_rq_b" in ''|*[!0-9a-f-]*) return 0 ;; esac
    [ "${#_rq_b}" -eq 36 ] || return 0
    _rq_u=; read -r _rq_u _rq_r < "${SYSROOT:-}/proc/uptime" 2>/dev/null
    case "$_rq_u" in ''|*[!0-9.]*|*.*.*|.*|*.) return 0 ;; *.*) ;; *) return 0 ;; esac   # NNN.DD, exactly
    _rq_n="${_rq_u%.*}${_rq_u#*.}"
    while [ "${_rq_n#0}" != "$_rq_n" ]; do _rq_n="${_rq_n#0}"; done   # no leading zero: it is not JSON
    [ -n "$_rq_n" ] || return 0
    printf ',"boot_id":"%s","seq":%s' "$_rq_b" "$_rq_n"
}
