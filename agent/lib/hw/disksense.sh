# disksense.sh -- a disk as the brain senses it while it is written (#3064: S54 temperature, S56 insertion to ready,
# S57 present at boot). POSIX sh (busybox ash). Needs smartattrs.sh (smart_temp_after), waits.sh (capped_run),
# diskinfo.sh (list_disks, disk_serial). Each prints a JSON value, `null` when it could not be read.
#
#   ds_temp_tick         every SENSE_TEMP_EVERY_S, ONE writing drawer (round robin): `smartctl -A`, capped at
#                        SENSE_TEMP_MAX_S, into targets/<d>/ss.temp.peak / ss.temp.n. A bound on the cost of the probe:
#                        at most one stalled smartctl per interval, never one per drawer. (No `drivetemp` hwmon in the
#                        initramfs, so smartctl is the only source -- on a drive that does not answer: no sample.)
#   ds_temp_json D       {"before_c","peak_during_c","after_c","samples"}: the first SMART read, the highest sample taken
#                        WHILE writing, and the one right after (temp_after, crcdelta.sh). All null = null.
#   ds_ready_json D      seconds from the kernel's "Attached SCSI disk" of this drive to the first stage mark of its
#                        write (stages, S35): the drive's time from insertion to ready (SMART, the write check, the plan).
#   ss_post_mark         once per boot, from the first hello: the disks that were here (name + serial) and the uptime.
#   ds_boot_json D       true = this very disk was in its drawer when the agent first looked; false = it came later;
#                        null = the first look was not recorded. A disk "at boot" is the one the POST/BIOS saw.

SENSE_TEMP_EVERY_S="${SENSE_TEMP_EVERY_S:-20}"
SENSE_TEMP_MAX_S="${SENSE_TEMP_MAX_S:-5}"
DMESG="${DMESG:-dmesg}"

ds_temp_tick() {
    _dt_now=$(date +%s)
    _dt_at=$(_ss_num "$(cat "$RUN_DIR/senses/temp.at" 2>/dev/null)" 0)
    [ $((_dt_now - _dt_at)) -ge "$SENSE_TEMP_EVERY_S" ] || return 0
    command -v "$SMARTCTL" >/dev/null 2>&1 || return 0
    _dt_w=""
    for _dt_s in "$RUN_DIR"/targets/*/state; do
        [ "$(cat "$_dt_s" 2>/dev/null)" = writing ] || continue
        _dt_k=${_dt_s%/state}; _dt_w="$_dt_w ${_dt_k##*/}"
    done
    set -- $_dt_w
    [ "$#" -gt 0 ] || return 0
    _dt_i=$(_ss_num "$(cat "$RUN_DIR/senses/temp.next" 2>/dev/null)" 0)
    _dt_i=$((_dt_i % $# + 1)); eval "_dt_d=\${$_dt_i}"
    echo "$_dt_now" > "$RUN_DIR/senses/temp.at"; echo "$_dt_i" > "$RUN_DIR/senses/temp.next"
    _dt_o="$RUN_DIR/smart/$_dt_d.during"
    capped_run "$SENSE_TEMP_MAX_S" "$_dt_o" "$SMARTCTL" -A -n standby "$DEVROOT/$_dt_d"; _dt_rc=$?
    [ "$CAPPED_TIMED_OUT" = 0 ] && [ "$((_dt_rc / 2 % 2))" -eq 0 ] || return 0
    _dt_c=$(smart_temp_after "$_dt_o"); case "$_dt_c" in ''|*[!0-9]*) return 0 ;; esac
    _dt_p=$(_ss_num "$(cat "$RUN_DIR/targets/$_dt_d/ss.temp.peak" 2>/dev/null)" 0)
    [ "$_dt_c" -le "$_dt_p" ] || echo "$_dt_c" > "$RUN_DIR/targets/$_dt_d/ss.temp.peak"
    echo $(( $(_ss_num "$(cat "$RUN_DIR/targets/$_dt_d/ss.temp.n" 2>/dev/null)" 0) + 1 )) > "$RUN_DIR/targets/$_dt_d/ss.temp.n"
}

ds_temp_json() {
    _dj_b=$(smart_temp_after "$RUN_DIR/smart/$1.out" 2>/dev/null); case "$_dj_b" in ''|*[!0-9]*) _dj_b=null ;; esac
    _dj_a=$(cat "$RUN_DIR/targets/$1/temp_after" 2>/dev/null); case "$_dj_a" in ''|*[!0-9]*) _dj_a=null ;; esac
    _dj_p=$(cat "$RUN_DIR/targets/$1/ss.temp.peak" 2>/dev/null); case "$_dj_p" in ''|*[!0-9]*) _dj_p=null ;; esac
    _dj_n=$(_ss_num "$(cat "$RUN_DIR/targets/$1/ss.temp.n" 2>/dev/null)" 0)
    [ "$_dj_b$_dj_p$_dj_a" = nullnullnull ] && { printf 'null'; return 0; }
    printf '{"before_c":%s,"peak_during_c":%s,"after_c":%s,"samples":%s}' "$_dj_b" "$_dj_p" "$_dj_a" "$_dj_n"
}

ds_ready_json() {
    _dr_f="$RUN_DIR/targets/$1/ss.ready"
    [ -s "$_dr_f" ] && { cat "$_dr_f"; return 0; }
    _dr_s=$(awk 'NR == 1 && $2 ~ /^[0-9]+(\.[0-9]+)?$/ { print $2 }' "$RUN_DIR/targets/$1/stages" 2>/dev/null)
    [ -n "$_dr_s" ] || { printf 'null'; return 0; }   # the write has not begun: nothing to measure yet
    _dr_l=$("$DMESG" 2>/dev/null) && [ -n "$_dr_l" ] || { printf 'null'; return 0; }   # a log that did not read is tried again next tick
    _dr_a=$(printf '%s\n' "$_dr_l" | awk -v d="[$1]" 'index($0, d) && /Attached SCSI disk/ && match($0, /^\[ *[0-9]+\.[0-9]+\]/) { t = substr($0, RSTART + 1, RLENGTH - 2) + 0; f = 1 } END { if (f) print t }')
    _dr_v=null   # the log read and does not name this drive (NVMe): said once, not asked every tick
    case "$_dr_a" in ''|*[!0-9.]*) ;; *) _dr_v=$(awk -v s="$_dr_s" -v a="$_dr_a" 'BEGIN { if (s + 0 >= a + 0) printf "%d", s - a; else print "null" }') ;; esac
    printf '%s' "$_dr_v" > "$_dr_f"; printf '%s' "$_dr_v"
}

ss_post_mark() {
    _sp_f="$RUN_DIR/senses/post.disks"
    [ -s "$_sp_f" ] && return 0
    mkdir -p "$RUN_DIR/senses" 2>/dev/null || return 0
    _sp_up=$(cut -d' ' -f1 "${SYSROOT:-}/proc/uptime" 2>/dev/null); case "$_sp_up" in ''|*[!0-9.]*) return 0 ;; esac
    { echo "t0 $_sp_up"
      for _sp_d in $(list_disks); do echo "disk $_sp_d $(disk_serial "$_sp_d" | tr -d ' |')"; done; } > "$_sp_f.new" && mv "$_sp_f.new" "$_sp_f"
}

ds_boot_json() {
    [ -s "$RUN_DIR/senses/post.disks" ] || { printf 'null'; return 0; }
    _db_s=$(disk_serial "$1" | tr -d ' |')
    awk -v d="$1" -v s="$_db_s" '$1 == "disk" && $2 == d && ($3 == s || $3 == "" || s == "") { f = 1 } END { print f ? "true" : "false" }' "$RUN_DIR/senses/post.disks"
}
