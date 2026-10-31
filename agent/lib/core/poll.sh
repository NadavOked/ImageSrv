# poll.sh -- how long the agent waits between two idle hellos (#136).
# POSIX sh (busybox ash). No bashisms, no job control.
#
# hello is both "I am here" and the poll, and every hello is a DB write on
# the server (net_seen). A flat two seconds meant 600 writes a minute for a
# class of 20 -- at exactly the moment the server is opening a round.
#
# The ladder is 2 -> 5 -> 15 seconds, and it has two hard brakes on it. Both
# were measured in the code, not assumed:
#
# 1. **An open round pins the fast rate.** Deployment is multicast, and the
#    spec (28) sends late joiners to the *next* round -- so a station that
#    notices "running" fifteen seconds late has not been delayed, it has
#    missed the round. While session.state is "open" this is not idle
#    polling, it is the synchronisation itself.
#
# 2. **The ceiling must stay under room.AWAKE_SECONDS (=30).** The cloning
#    room screen calls a machine awake from its last_seen, and that is the
#    only heartbeat reader in the whole server (room.py:_is_awake, spec 29
#    stage 3). A poll slower than 30 seconds would flash the drawers between
#    "on" and "off". The build machine needs no heartbeat at all -- nothing
#    computes awake for it -- so the ceiling is set by the cloners.
#
# **The reset trigger is a change in the server's answer**, not a timer and
# not local activity: cloner machines are powered on together with the build
# machine in an unknown order, so one that came up first has been waiting --
# and a time-based reset would leave it slow at exactly the moment the work
# starts. A changed answer *is* that moment.
#
# The conscious price (Nadav's call): a capture ordered from the console can
# sit unseen for up to POLL_MAX seconds before the build machine asks again.
# That path has no round to pin it fast, and 15 is the accepted ceiling.

POLL_FIRST=2
POLL_MID=5
POLL_MAX=15

#: Signature of the last answer, and the wait currently in force.
POLL_MARK=""
POLL_SLEEP="$POLL_FIRST"

poll_signature() {
    # Only what can change a decision. `joined` and `starts_in_seconds` are
    # meant to move every second, and resetting on them would cancel the
    # ladder outright. Reads the D_* globals that read_answer fills.
    printf '%s|%s|%s|%s|%s|%s' "$D_SCHEMA" "$D_KNOWN" "$D_ROLE" \
        "$D_TASK" "$D_SESSION_STATE" "$D_SESSION_ID"
}

poll_widen() {
    # $1 = the wait in force; prints the next rung, never past the ceiling.
    if [ "$1" -lt "$POLL_MID" ]; then
        echo "$POLL_MID"
    else
        echo "$POLL_MAX"
    fi
}

poll_sleep() {
    # One idle beat. Widening needs two things to be true at once: the answer
    # did not change, and no round is open for this group.
    _sig=$(poll_signature)
    POLL_MARK_CHANGED=0
    if [ "$D_SESSION_STATE" = "open" ] || [ "$_sig" != "$POLL_MARK" ]; then
        [ "$_sig" != "$POLL_MARK" ] && POLL_MARK_CHANGED=1   # #434: a changed answer is activity
        POLL_MARK="$_sig"
        POLL_SLEEP="$POLL_FIRST"
    else
        POLL_SLEEP=$(poll_widen "$POLL_SLEEP")
    fi
    # #434: the idle clock -- only waiting states beat here; inert when idle.sh
    # is not loaded (poll.sh is also sourced alone by the tests)
    command -v idle_check >/dev/null 2>&1 && idle_check
    sleep "$POLL_SLEEP"
}

poll_off_deploy_vlan() {
    # Is this station sitting outside the deployment vlan (#42)? The answer
    # already says so, without a new field: an open round on the deployment
    # vlan waives the login -- that is what saves the 29 sign-ins -- while
    # off the vlan the login is always required. So "a round is open and the
    # server still wants a login" is exactly "not on the deployment vlan".
    # The server enforces the same rule when a pull opens (pull.sh).
    #
    # Positive evidence only: "false", a missing field and "null" all keep
    # today's behaviour, which is to poll. Wrongly stopping a real station
    # is far worse than one more poll.
    [ "$D_REQUIRE_LOGIN" = "true" ]
}
