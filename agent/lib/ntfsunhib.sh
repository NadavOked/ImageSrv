# ntfsunhib.sh -- #2009: the evidence (ntfs_state) and the one write
# (ntfs_unhibernate) on a Windows left in Fast Startup. POSIX sh (busybox ash).
# Split from grow.sh (298 lines, the wall is 300) when the drop got its second
# try. Loaded right after grow.sh: grow_filesystem (grow.sh) and
# lc_unhibernate_target / _lc_ntfs_probe (hibernation.sh) call these.
# Every caller of ntfs_unhibernate gives a TARGET's partition -- the copy;
# a source is only probed (ntfs_state, read-only).

ntfs_state() {
    # $1 = partition node. ntfs-3g.probe --readwrite (read-only): clean (0) | hibernated (14) |
    # dirty (15) | unchecked:<why> -- never "clean" for a probe that did not answer (principle 5).
    _ns_p="${NTFSPROBE:-ntfs-3g.probe}"
    command -v "$_ns_p" >/dev/null 2>&1 || { echo "unchecked:$_ns_p missing"; return; }
    "$_ns_p" --readwrite "$1" >/dev/null 2>&1 < /dev/null; _ns_rc=$?
    case "$_ns_rc" in 0) echo clean ;; 14) echo hibernated ;; 15) echo dirty ;; *) echo "unchecked:$_ns_p rc $_ns_rc" ;; esac
}

ntfs_unhibernate() {
    # $1 = a TARGET's NTFS partition. Windows' hibernation (Fast Startup) is
    # dropped on it: ntfs-3g -o remove_hiberfile, umount, ntfsfix -d -- then
    # read back: the probe says clean, and hiberfil.sys is gone or no longer
    # starts with hibr/wake. 0 = all of that seen. NTFS_UNHIB_TOUCHED=1 once
    # the volume was written (the caller may have to restore it);
    # NTFS_UNHIB_LOGFILE=1 when the drop needed the journal emptied first.
    NTFS_UNHIB_TOUCHED=0; NTFS_UNHIB_LOGFILE=0; _nu_m="$RUN_DIR/unhib"; mkdir -p "$_nu_m" || return 1
    if ! ntfs-3g -o remove_hiberfile "$1" "$_nu_m" >> "$LOG_FILE" 2>&1; then
        # Metal, 23/10 (cloner 2, a Samsung 870 shut down with Fast Startup):
        # "unclean file system (0, 0). Metadata kept in Windows cache" (rc 14).
        # ntfsfix (no -d) empties $LogFile and exits 1 while the volume is still
        # hibernated -- that 1 is expected, and no rc of it is evidence: only
        # the second remove_hiberfile and the read-back below decide.
        log "WARNING: $1: ntfs-3g -o remove_hiberfile refused -- emptying \$LogFile (ntfsfix) and trying once more (#2009)"
        NTFS_UNHIB_TOUCHED=1
        ntfsfix "$1" >> "$LOG_FILE" 2>&1; _nu_rc=$?
        log "$1: ntfsfix (journal) rc=$_nu_rc -- 1 is expected while hibernated; not the evidence (#2009)"
        ntfs-3g -o remove_hiberfile "$1" "$_nu_m" >> "$LOG_FILE" 2>&1 \
            || { log "WARNING: $1: ntfs-3g -o remove_hiberfile refused again after ntfsfix (#2009)"; return 1; }
        NTFS_UNHIB_LOGFILE=1
    fi
    NTFS_UNHIB_TOUCHED=1; sync
    umount "$_nu_m" >> "$LOG_FILE" 2>&1 || { log "WARNING: $1: umount after remove_hiberfile failed (#2009)"; return 1; }
    ntfsfix -d "$1" >> "$LOG_FILE" 2>&1 || { log "WARNING: $1: ntfsfix -d after remove_hiberfile failed (#2009)"; return 1; }
    _nu_s=$(ntfs_state "$1")
    [ "$_nu_s" = clean ] || { log "WARNING: $1: still $_nu_s after remove_hiberfile (#2009)"; return 1; }
    ntfs-3g -o ro "$1" "$_nu_m" >> "$LOG_FILE" 2>&1 || { log "WARNING: $1: no read-only mount to read hiberfil.sys back (#2009)"; return 1; }
    _nu_h=""; [ ! -e "$_nu_m/hiberfil.sys" ] || _nu_h=$(head -c 4 "$_nu_m/hiberfil.sys" 2>/dev/null | tr -d '\0' | tr 'A-Z' 'a-z')
    umount "$_nu_m" >> "$LOG_FILE" 2>&1 || { log "WARNING: $1: umount after the read-back failed (#2009)"; return 1; }
    case "$_nu_h" in hibr|wake) log "WARNING: $1: hiberfil.sys still says $_nu_h (#2009)"; return 1 ;; esac
    log "$1: hibernation dropped on the copy -- probe clean, hiberfil.sys gone or blank (#2009)"
}
