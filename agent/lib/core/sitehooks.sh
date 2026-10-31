# sitehooks.sh -- run the start hooks of a site/lab overlay (#1266).
# POSIX sh (busybox ash). No bashisms.
#
# The product ships no hook. A site or a lab adds files to the initramfs at
# build time (IMAGESRV_EXTRA_OVERLAY in tools/build_initramfs.sh); every
# $SITE_HOOKS_DIR/*.sh among them runs here once, in a subshell, after the
# network is up -- $IFACE, $IP, $RUN_DIR and $LOG_FILE are set, and log()
# from common.sh is there. The product does not know what a hook does.
#
# A hook that fails is named in the log by its exit code and does not stop
# the caller: an overlay is an add-on, and a station that cannot run one
# still does the product's job. Absent directory = no overlay = nothing.

SITE_HOOKS_DIR="${SITE_HOOKS_DIR:-/etc/imagesrv/hooks.d}"

run_site_hooks() {
    for _hook in "$SITE_HOOKS_DIR"/*.sh; do
        # No match leaves the pattern itself in $_hook: not a file, skip.
        [ -f "$_hook" ] || continue
        log "site hook: $_hook"
        ( . "$_hook" )
        _rc=$?
        [ "$_rc" -eq 0 ] || log "WARNING: site hook $_hook exited $_rc"
    done
    unset _hook _rc
}
