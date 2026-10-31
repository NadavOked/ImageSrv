# jsonq.sh -- the single place that touches jq. Everything the agent reads
# out of server JSON goes through here, so tests can fake one function
# instead of shipping jq to every environment.
# POSIX sh (busybox ash).

json_get() {
    # $1 = JSON file, $2 = jq path (e.g. ".session.state").
    # Prints the value; JSON null and a missing key both print "null".
    jq -r "$2" "$1" 2>/dev/null || echo "null"
}

json_get_join() {
    # $1 = JSON file, $2 = jq path to an array. Prints elements one per line.
    jq -r "$2 | .[]?" "$1" 2>/dev/null
}
