# writeback.sh -- #1841: a target that rejects its writes is found during the
# partition, not at its end. POSIX sh (busybox ash).
#
# partclone writes through the page cache: write() succeeds and an EIO from the
# disk comes back only at writeback -- at partclone's own fsync, after the whole
# partition (FI D4, 19/10: 18.8 of 19.5 GB "written" into a dead drawer, ~6
# minutes; 30+ on a big image). Every WRITE_FSYNC_INTERVAL_S an fsync of the
# partition node asks the kernel for the writeback errors since then (errseq).
# A refused fsync is the evidence: the stream into this target is cut (its
# fifo's reader, reader.pid, as drawer_watch does -- fanout drops the drawer)
# and the partition fails as write_error (verdict.sh / restore.sh).
# The price: one fsync per partition per interval -- what the kernel writes back
# anyway, plus a cache flush (measured in the PR of #1841).

WRITE_FSYNC_INTERVAL_S="${WRITE_FSYNC_INTERVAL_S:-10}"

writeback_watch() {
    # $1 = target dir (reader.pid), $2 = partition node, $3 = the pid of the shell
    # that runs the pipe. In the background; the pipe (restore_pipe, drawers.sh;
    # _drawer_pipeline, drawerfeed.sh -- both shapes, #1746) kills it at its
    # end, and it leaves by itself once $3 is gone (a pipe killed on a ceiling,
    # waits.sh) -- never an orphan fsyncing a drawer to the next boot.
    # Its own output goes nowhere: an orphaned `sleep` must not hold a pipe (#14).
    [ -n "$3" ] || { log "WARNING: $2: no pid for the fsync watcher -- a write error shows at the partition's end only"; return 0; }
    while sleep "$WRITE_FSYNC_INTERVAL_S" && kill -0 "$3" 2>/dev/null; do
        dd if=/dev/null of="$2" bs=512 count=0 conv=notrunc,fsync 2> "$1/writeback.err" && continue
        echo "periodic fsync failed (write_error): $(tail -n 1 "$1/writeback.err" 2>/dev/null)" > "$1/writeback.failed"
        log "$2: periodic fsync failed -- the disk rejects its writes; this target's stream is cut"
        kill -9 "$(cat "$1/reader.pid" 2>/dev/null)" 2>/dev/null \
            || log "$2: its reader was not stopped -- the partition fails at its end all the same"
        return 0
    done
}

