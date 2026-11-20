# bgfds.sh -- the one place a background job lets go of what it inherited (#3548, #3524). POSIX sh (busybox ash).
#
# A job started from inside `$( )` must not keep that capture open. A redirected call (`fn >&2` inside the
# capture) parks the descriptor it redirected -- the capture's pipe -- on a spare one, fd 10 and up; the job's
# subshell inherits it, and `$( )` reads to EOF: it returns when the last writer is gone, not when the function is.
# `> /dev/null 2>&1 < /dev/null` covers 0-2 only. Call this FIRST inside `( ... ) &`.
#
# Every open descriptor 3..31 is pointed at /dev/null, not closed (`exec N>&-`): ash restores the parked ones
# on the job's way out (dup2(10,1)); a closed one makes that fail, loop, and run the parent's code in the child
# (measured, busybox 1.37). `true`, not `:` -- a redirect error of a special builtin ends the shell. Counting
# down, the probe parks stdout on the lowest free fd >= 10, which `>&N` then finds open: an extra /dev/null, harmless.
# ash (measured); dash cannot name fd >= 10.
bg_detach_fds() {
    _bg_n=31
    while [ "$_bg_n" -ge 3 ]; do
        { true >&"$_bg_n"; } 2>/dev/null && eval "exec $_bg_n>/dev/null"
        _bg_n=$((_bg_n - 1))
    done
}
