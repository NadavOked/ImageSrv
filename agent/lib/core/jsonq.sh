# jsonq.sh -- the single place that touches jq. Everything the agent reads
# out of server JSON goes through here, so tests can fake one function
# instead of shipping jq to every environment.
# POSIX sh (busybox ash).

json_get() {
    # $1 = JSON file, $2 = jq path (e.g. ".session.state").
    # Prints the value; JSON null and a missing key both print "null".
    jq -r "$2" "$1" 2>/dev/null || echo "null"
}

json_disk() {
    # $1 = JSON file, $2 = jq path. #1635: a disk name from the server becomes a
    # path -- $RUN_DIR/targets/<disk>, /dev/<disk> -- so only a kernel name
    # (sda, nvme0n1) is printed. "../x", "a/b", "" and null print nothing: 1.
    _jd=$(json_get "$1" "$2")
    case "$_jd" in ''|null|*[!a-z0-9]*) return 1 ;; esac
    printf '%s\n' "$_jd"
}

json_get_join() {
    # $1 = JSON file, $2 = jq path to an array. Prints elements one per line.
    jq -r "$2 | .[]?" "$1" 2>/dev/null
}
