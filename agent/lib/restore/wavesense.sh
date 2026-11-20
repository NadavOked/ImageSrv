# wavesense.sh -- what the receiving machine itself felt during a chunk wave, for the brain (#3041, #3125: S46-S50).
# POSIX sh (busybox ash).
#
# The brain learns each cloner's own ceiling from its waves (Nadav 15/11 on #3041): the highest rate at which no
# packet was lost, and how loaded the CPU was then. To tell "the machine could not keep up" from "the disk could
# not", it needs what the ssh measurement of #3034 read by hand. Five senses, from the kernel's own counters:
#
#   S48  per core, /proc/stat:      busy% of the latest window, and the highest window of the wave (busy_max)
#   S49  per core, /proc/stat + /proc/softirqs: irq% and softirq% of the window, and NET_RX softirqs a second
#   S50  the card ($IFACE): rx_missed (sysfs statistics/rx_missed_errors) and rx_no_buffer (`ethtool -S`
#        rx_no_buffer_count) since the wave began, and the RX ring (`ethtool -g`: current and the card's maximum)
#   S46/S47  per drawer, /proc/diskstats: util% (io_ticks), in-flight now and its highest, ms a write (write ticks
#        over writes completed)
#
# When: chunk_loss_mark (a receiving pass begins: chunkloss.sh) calls wave_sense_mark -- the window starts, and on
# the wave's first pass the card's counters and ring are read once. chunk_loss_tell (once a second while the pass
# runs) calls wave_sense_tick, which reads nothing until WAVE_SENSE_S seconds passed: then one window closes. Cheap:
# four small files a window, and ethtool only when it is there.
#
# K9 (#3064): the kernel's own lines about each drawer's ATA port since its target began ("kernel": {"sda": [lines]}). The
# server's cause of a disk it fails mid-wave needs them THEN; failmark.sh's ata_capture runs only once a write failed.
#
# The summary of the wave so far is $RUN_DIR/wsense/senses.json, by a rename; the progress report carries it as
# "wave_senses" (machine_extra_json, datareport.sh) only for the session it was measured in.
#
# Principle 5: a sense that was not read is null, never 0 -- an unreadable /proc/stat is "cpu": null, a drawer with no
# diskstats line is null, a counter that went DOWN (the card was reset) is null, and a missing ethtool is
# "ethtool": false with rx_no_buffer and the ring null: "not available", not "no drops on a 0-slot ring".

WAVE_SENSE_S="${WAVE_SENSE_S:-5}"

_ws() { echo "$RUN_DIR/wsense"; }

_ws_num() { case "$1" in ''|*[!0-9]*) return 1 ;; esac; echo "$1"; }

_ws_delta() {   # $1 = at the wave's start, $2 = now -> the difference; either unread, or a counter that went down = null
    if _ws_num "$1" >/dev/null && _ws_num "$2" >/dev/null && [ "$2" -ge "$1" ]; then echo $(($2 - $1)); else echo null; fi
}

_ws_ethtool() { command -v "${ETHTOOL:-ethtool}" >/dev/null 2>&1; }

_ws_missed() { _ws_num "$(cat "${SYSROOT:-}/sys/class/net/$IFACE/statistics/rx_missed_errors" 2>/dev/null)"; }

_ws_nobuf() {   # rx_no_buffer_count of `ethtool -S` (igb, e1000e); a driver without it = not read
    _ws_ethtool || return 1
    "${ETHTOOL:-ethtool}" -S "$IFACE" 2>/dev/null | awk '$1 == "rx_no_buffer_count:" && $2 ~ /^[0-9]+$/ { print $2; f = 1; exit } END { exit !f }'
}

_ws_ring() {   # -> "<current RX> <maximum RX>" of `ethtool -g`; a value not said = null
    _ws_ethtool || { echo "null null"; return; }
    "${ETHTOOL:-ethtool}" -g "$IFACE" 2>/dev/null | awk '/^Pre-set maximums/ { s = "max" } /^Current hardware settings/ { s = "cur" }
        $1 == "RX:" && $2 ~ /^[0-9]+$/ { if (s == "max" && mx == "") mx = $2; if (s == "cur" && cu == "") cu = $2 }
        END { printf "%s %s\n", (cu == "" ? "null" : cu), (mx == "" ? "null" : mx) }'
}

_ws_disks() {   # the drawers of this restore, by name
    for _wd in "$RUN_DIR"/targets/*; do [ -d "$_wd" ] && printf '%s ' "${_wd##*/}"; done
}

_ws_snap() {   # -> the counters now: "t <uptime>", "c <core> <8 times>", "r <core> <NET_RX>", "d <disk> <writes> <wticks> <inflight> <ioticks>"
    _wsn_t=$(cut -d' ' -f1 "${SYSROOT:-}/proc/uptime" 2>/dev/null)
    case "$_wsn_t" in ''|*[!0-9.]*) ;; *) echo "t $_wsn_t" ;; esac
    awk '$1 ~ /^cpu[0-9]+$/ && NF >= 8 { print "c", substr($1, 4), $2, $3, $4, $5, $6, $7, $8, (NF >= 9 ? $9 : 0) }' "${SYSROOT:-}/proc/stat" 2>/dev/null
    awk 'NR == 1 { for (i = 1; i <= NF; i++) col[i] = substr($i, 4) } $1 == "NET_RX:" { for (i = 2; i <= NF; i++) print "r", col[i - 1], $i }' \
        "${SYSROOT:-}/proc/softirqs" 2>/dev/null
    awk -v want=" $(_ws_disks)" 'NF >= 13 && index(want, " " $3 " ") { print "d", $3, $8, $11, $12, $13 }' "${SYSROOT:-}/proc/diskstats" 2>/dev/null
}

_ws_window() {   # $1 = the window's start, $2 = now, $3 = the peaks so far -> "pc"/"pd" lines (the new peaks), "J cpu ..." and "J disks ..."
    awk -v ws="$(_ws_disks)" '
        function mx(a, b) { if (a == "" || a == "null") return b; if (b == "null") return a; return (a + 0 >= b + 0) ? a : b }
        function pct(x, tot) { x = int(100 * x / tot + 0.5); return x > 100 ? 100 : x }
        FILENAME == ARGV[1] { if ($1 == "t") t0 = $2; else if ($1 == "c") c0[$2] = $0; else if ($1 == "r") r0[$2] = $3; else if ($1 == "d") d0[$2] = $0; next }
        FILENAME == ARGV[2] { if ($1 == "t") t1 = $2; else if ($1 == "c") { c1[$2] = $0; core[++nc] = $2 } else if ($1 == "r") r1[$2] = $3; else if ($1 == "d") d1[$2] = $0; next }
        $1 == "pc" { pb[$2] = $3; ps[$2] = $4; pr[$2] = $5 }
        $1 == "pd" { pu[$2] = $3; pi[$2] = $4; pw[$2] = $5 }
        END {
            dt = (t0 != "" && t1 != "") ? t1 - t0 : 0
            cpu = "null"
            if (nc > 0 && dt > 0) {
                cpu = "["
                for (k = 1; k <= nc; k++) {
                    n = core[k]; busy = "null"; irq = "null"; sirq = "null"; nrx = "null"
                    if (n in c0) {
                        split(c0[n], a, " "); split(c1[n], b, " "); tot = 0
                        for (i = 3; i <= 10; i++) tot += b[i] - a[i]
                        if (tot > 0) {
                            busy = pct(tot - (b[6] - a[6]) - (b[7] - a[7]), tot); irq = pct(b[8] - a[8], tot); sirq = pct(b[9] - a[9], tot)
                        }
                    }
                    if ((n in r0) && (n in r1) && r1[n] + 0 >= r0[n] + 0) nrx = int((r1[n] - r0[n]) / dt + 0.5)
                    pb[n] = mx(pb[n], busy); ps[n] = mx(ps[n], sirq); pr[n] = mx(pr[n], nrx)
                    cpu = cpu (k > 1 ? "," : "") sprintf("{\"core\":%s,\"busy\":%s,\"busy_max\":%s,\"irq\":%s,\"softirq\":%s,\"softirq_max\":%s,\"net_rx_ps\":%s,\"net_rx_ps_max\":%s}", \
                        n, busy, pb[n], irq, sirq, ps[n], nrx, pr[n])
                }
                cpu = cpu "]"
            }
            for (n in pb) printf "pc %s %s %s %s\n", n, (pb[n] == "" ? "null" : pb[n]), (ps[n] == "" ? "null" : ps[n]), (pr[n] == "" ? "null" : pr[n])
            nd = split(ws, dl, " "); disks = "{"
            for (k = 1; k <= nd; k++) {
                n = dl[k]; one = "null"
                if (dt > 0 && (n in d0) && (n in d1)) {
                    split(d0[n], a, " "); split(d1[n], b, " ")
                    w = b[3] - a[3]; wt = b[4] - a[4]; io = b[6] - a[6]
                    if (w >= 0 && wt >= 0 && io >= 0) {
                        util = pct(io, dt * 1000); inf = b[5] + 0; wms = (w > 0) ? sprintf("%.1f", wt / w) : "null"
                        pu[n] = mx(pu[n], util); pi[n] = mx(pi[n], inf); pw[n] = mx(pw[n], wms)
                        one = sprintf("{\"util\":%s,\"util_max\":%s,\"inflight\":%s,\"inflight_max\":%s,\"w_ms\":%s,\"w_ms_max\":%s}", \
                            util, pu[n], inf, pi[n], wms, (pw[n] == "" ? "null" : pw[n]))
                    }
                }
                disks = disks (k > 1 ? "," : "") sprintf("\"%s\":%s", n, one)
            }
            for (n in pu) printf "pd %s %s %s %s\n", n, (pu[n] == "" ? "null" : pu[n]), (pi[n] == "" ? "null" : pi[n]), (pw[n] == "" ? "null" : pw[n])
            print "J cpu " cpu; print "J disks " disks "}"
        }' "$1" "$2" "$3"
}

_ws_kernel() {   # -> {"sda":["ata2.00: ..."],"sdb":null}: each drawer's ATA lines of dmesg since its target began (failmark.sh's ata_capture, <= 20)
    # [] = dmesg was read and says nothing about the drawer's port; null = NOT read (no dmesg, an empty one, or the port is unknown)
    _wk_k="$(_ws)/dmesg"; _wk_f="$(_ws)/klines"
    "${DMESG:-dmesg}" > "$_wk_k" 2>/dev/null && [ -s "$_wk_k" ] || _wk_k=
    _wk_o="{"; _wk_s=
    for _wk_d in $(_ws_disks); do
        _wk_a=; if [ -n "$_wk_k" ] && command -v ata_port_of >/dev/null 2>&1; then   # sysfs only (target_ident may run hdparm: not every 5 s)
            _wk_a=$(ata_port_of "$_wk_d"); [ -n "$_wk_a" ] || { _wk_a=$(cat "$RUN_DIR/targets/$_wk_d/seen" 2>/dev/null); _wk_a=${_wk_a##*|}; }
        fi
        _wk_j=null
        case "$_wk_a" in ''|*[!0-9]*) ;; *)
            awk -v dev="$_wk_d" -v ata="$_wk_a" -v since="$(cat "$RUN_DIR/targets/$_wk_d/since" 2>/dev/null || echo 0)" '
                { ts = -1; if (match($0, /^\[ *[0-9]+\.[0-9]+\]/)) ts = substr($0, RSTART + 1, RLENGTH - 2) + 0
                  if (ts >= 0 && ts < since + 0) next
                  if ($0 ~ /ACPI cmd .* filtered out|configured for UDMA|SATA link up|EH complete/) next
                  io = index($0, " error, dev " dev ",") > 0
                  if (!io && $0 !~ ("ata" ata "(\\.[0-9]+)?:")) next
                  kept[++n] = $0; if (!head && (io || $0 ~ /exception Emask/)) head = n }
                END { if (!head) head = 1; for (i = head; i <= n && i < head + 20; i++) print kept[i] }' "$_wk_k" > "$_wk_f" 2>/dev/null && {
                _wk_j=; _wk_x=
                while IFS= read -r _wk_l; do _wk_j="$_wk_j$_wk_x\"$(json_escape "$_wk_l")\""; _wk_x=","; done < "$_wk_f"
                _wk_j="[$_wk_j]"; } || _wk_j=null ;;
        esac
        _wk_o="$_wk_o$_wk_s\"$_wk_d\":$_wk_j"; _wk_s=","
    done
    printf '%s}' "$_wk_o"
}

_ws_nic_json() {   # the card's part of the summary; no $IFACE = null (nothing to read)
    case "${IFACE:-}" in ''|*[!A-Za-z0-9._-]*) echo null; return ;; esac
    read -r _wj_m0 _wj_b0 < "$(_ws)/nic0" 2>/dev/null || { _wj_m0=null; _wj_b0=null; }
    read -r _wj_rc _wj_rx < "$(_ws)/ring" 2>/dev/null || { _wj_rc=null; _wj_rx=null; }
    _wj_m=$(_ws_missed) || _wj_m=""; _wj_b=$(_ws_nobuf) || _wj_b=""
    if _ws_ethtool; then _wj_et=true; else _wj_et=false; fi
    printf '{"iface":"%s","ethtool":%s,"rx_missed":%s,"rx_no_buffer":%s,"ring_rx":%s,"ring_rx_max":%s}' \
        "$IFACE" "$_wj_et" "$(_ws_delta "$_wj_m0" "$_wj_m")" "$(_ws_delta "$_wj_b0" "$_wj_b")" "$_wj_rc" "$_wj_rx"
}

_ws_write() {   # $1 = cpu json, $2 = disks json -> senses.json, by a rename (the reporter reads it at any moment)
    printf '{"window_s":%s,"windows":%s,"cpu":%s,"nic":%s,"disks":%s,"kernel":%s}\n' "$WAVE_SENSE_S" "$(cat "$(_ws)/windows")" "$1" "$(_ws_nic_json)" "$2" "$(_ws_kernel)" \
        > "$(_ws)/senses.json.new" && mv "$(_ws)/senses.json.new" "$(_ws)/senses.json"
}

wave_sense_mark() {   # a receiving pass begins: a new window. The wave's first pass also reads the card's start and its ring
    _wm=$(_ws)
    if [ "$(cat "$_wm/session" 2>/dev/null)" != "${CHUNK_SESSION:-}" ]; then
        rm -rf "$_wm"; mkdir -p "$_wm" || return 0
        echo "${CHUNK_SESSION:-}" > "$_wm/session"; echo 0 > "$_wm/windows"; : > "$_wm/peak"
        if [ -n "${IFACE:-}" ]; then
            _wm_m=$(_ws_missed) || _wm_m=null; _wm_b=$(_ws_nobuf) || _wm_b=null
            echo "$_wm_m $_wm_b" > "$_wm/nic0"; _ws_ring > "$_wm/ring"
        fi
        _ws_write null null
    fi
    _ws_snap > "$_wm/base"
}

wave_sense_tick() {   # once a second while a pass runs: a window closes every WAVE_SENSE_S seconds, and only then is anything read
    _wt=$(_ws); [ -s "$_wt/base" ] || return 0
    _wt_t0=$(sed -n 's/^t //p' "$_wt/base"); _wt_t=$(cut -d' ' -f1 "${SYSROOT:-}/proc/uptime" 2>/dev/null)
    case "$_wt_t0:$_wt_t" in *[!0-9.:]*|:*|*:) ;; *)
        awk -v a="$_wt_t0" -v b="$_wt_t" -v w="$WAVE_SENSE_S" 'BEGIN { exit !(b - a >= w) }' || return 0 ;;
    esac   # an uptime that cannot be read closes the window at once: the window says null, the wave goes on
    _ws_snap > "$_wt/now"
    _ws_window "$_wt/base" "$_wt/now" "$_wt/peak" > "$_wt/win" || return 0
    grep '^p[cd] ' "$_wt/win" > "$_wt/peak.new"; mv "$_wt/peak.new" "$_wt/peak"
    echo $(($(cat "$_wt/windows") + 1)) > "$_wt/windows"
    _ws_write "$(sed -n 's/^J cpu //p' "$_wt/win")" "$(sed -n 's/^J disks //p' "$_wt/win")"
    mv "$_wt/now" "$_wt/base"
}

wave_sense_json() {   # $1 = the session of the report -> ,"wave_senses":{...} when this machine measured that wave
    [ -n "${1:-}" ] && [ "$(cat "$(_ws)/session" 2>/dev/null)" = "$1" ] && [ -s "$(_ws)/senses.json" ] || return 0
    printf ',"wave_senses":%s' "$(tr -d '\n' < "$(_ws)/senses.json")"
}
