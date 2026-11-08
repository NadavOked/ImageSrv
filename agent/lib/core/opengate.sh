# opengate.sh -- positive evidence that a round is open for THIS machine,
# before the first write to a disk (#2657). POSIX sh (busybox ash).
#
# A hello answer that says "restore" names a session; it is not evidence that
# the session is still open. Until #1076 the channel is not authenticated, and
# the agent has no way to tell a current answer from an old one. The real
# server knows -- so it is asked, by name, before anything touches the disk:
# the write check, TRIM, the partition table, the RAID metadata.
#
# The question is the job's first progress report, sent synchronously. The
# answer counts only when it is `ok:true` AND carries the same session id back
# (interfaces.md section 4). Everything else stops here with nothing written:
# `not_open`, `not_member`, a member that already ended, an error, no answer,
# a body that does not parse, `ok:true` without the id or with another id.
# "We could not ask" is not "it is open" (rule 5); the caller boots the local
# disk (rule 1).
#
# The report goes after job_reset/target_init, so what it carries is this
# job's `waiting` -- the same report progress_loop would send first (#1916).

session_open_gate() {
    # $1 = session id, $2 = mac, $3 = server URL. 0 only on that evidence;
    # otherwise 1 with OPEN_GATE_WHY said.
    OPEN_GATE_WHY="round '$1' was not confirmed open for this machine -- nothing was written"
    case "$1" in ''|null) return 1 ;; esac
    rm -f "$RUN_DIR/progress.reply"
    if ! progress_send "$1" "$2" "$3"; then
        OPEN_GATE_WHY="the server did not confirm round $1 (no answer, or refused) -- nothing was written"
        return 1
    fi
    if progress_refused_for_good; then
        OPEN_GATE_WHY="the server says round $1 is not open -- nothing was written"
        return 1
    fi
    [ "$(json_get "$RUN_DIR/progress.reply" ".ok")" = true ] \
        && [ "$(json_get "$RUN_DIR/progress.reply" ".session_id")" = "$1" ] \
        || return 1
    log "round $1: the server confirmed it is open for this machine"
}
