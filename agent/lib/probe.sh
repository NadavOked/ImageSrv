# probe.sh -- machine health the hello carries (#1049 stage A). POSIX sh.
# Each field: measured | null (tool/file absent — not checked) | {"error":"<why>"} (tried, failed). null is never "ok" (principle 5).
# SYSFS_ROOT/PROC_ROOT default /sys /proc; SYSROOT prefixes both in tests.

SYSFS_ROOT="${SYSFS_ROOT:-${SYSROOT:-}/sys}"
PROC_ROOT="${PROC_ROOT:-${SYSROOT:-}/proc}"; DEVROOT="${DEVROOT:-/dev}"
DMIDECODE="${DMIDECODE:-dmidecode}"; NVME="${NVME:-nvme}"; ARPING="${ARPING:-arping}"
HWCLOCK="${HWCLOCK:-hwclock}"; BLKID="${BLKID:-blkid}"; SMARTCTL="${SMARTCTL:-smartctl}"
_pe() { printf '{"error":"%s"}' "$(json_escape "$1")"; }
_pn() { printf 'null'; }; _js() { printf '"%s"' "$(json_escape "$1")"; }
# Host tools on a fake sysfs are not the initramfs. Stubs: PROBE_STUBS=1.
_have() {
    command -v "$1" >/dev/null 2>&1 || return 1
    { [ -z "${SYSROOT:-}" ] && [ "$SYSFS_ROOT" = "/sys" ]; } || { [ "${PROBE_STUBS:-}" = "1" ] && case "$1" in */*) true ;; *) false ;; esac; }   # stub = explicit path only
}
# 2>/dev/null here is a miss, never an ok — caller maps fail to null/error.
_rf() {
    [ -r "$1" ] || return 1
    _v=$(trim "$(cat "$1" 2>/dev/null)") || return 1
    [ -n "$_v" ] || return 1
    printf '%s' "$_v"
}
_num() { case "$1" in ''|*[!0-9]*) return 1 ;; esac; printf '%s' "$1"; }
_hex() {
    _h=$(tr -d '\n\r ' < "$1" 2>/dev/null | tr 'A-F' 'a-f'); _h=${_h#0x}
    case "$_h" in ''|*[!0-9a-f]*) return 1 ;; esac; printf '%s' "$_h"
}
_jint() {
    printf '%s' "$1" | awk -v k="$2" '{
        p="\"" k "\""; i=index($0,p); if (!i) next
        rest=substr($0,i+length(p))
        if (match(rest,/-?[0-9]+/)) { print substr(rest,RSTART,RLENGTH); exit } }'
}

probe_power() {
    _d="$SYSFS_ROOT/class/power_supply"; [ -d "$_d" ] || { _pn; return; }
    _mains=0; _dis=0; _any=0; _name=""
    for _s in "$_d"/*; do
        [ -e "$_s" ] || continue; _any=1
        _t=$(_rf "$_s/type") || continue; _n=${_s##*/}
        case "$_t" in
            Mains|USB*) _o=$(_rf "$_s/online") || continue
                [ "$_o" = 1 ] && { _mains=1; _name="$_n"; } ;;
            Battery) [ -z "$_name" ] && _name="$_n"
                _st=$(_rf "$_s/status") || continue
                [ "$_st" = "Discharging" ] && _dis=1 ;;
        esac
    done
    [ "$_any" = 1 ] || { _pn; return; }
    [ -n "$_name" ] || { _pe "אין ספק חשמל קריא"; return; }
    _bat=false; [ "$_mains" = 0 ] && [ "$_dis" = 1 ] && _bat=true
    printf '{"on_battery":%s,"supply":%s}' "$_bat" "$(_js "$_name")"
}

probe_rtc() {
    if _hw=$(_rf "$SYSFS_ROOT/class/rtc/rtc0/since_epoch"); then
        _num "$_hw" >/dev/null || { _pe "שעון החומרה (RTC) החזיר ערך שאינו מספר"; return; }
    elif _have "$HWCLOCK"; then
        _raw=$("$HWCLOCK" -r 2>/dev/null) || { _pe "hwclock -r נכשל"; return; }
        _hw=$(date -D '%a %b %e %H:%M:%S %Y' -d "$_raw" +%s 2>/dev/null) \
            || { _pe "הפלט של hwclock אינו זמן epoch"; return; }
    else _pn; return; fi
    _sys=$(date +%s 2>/dev/null) || { _pe "date +%s נכשל"; return; }
    _num "$_sys" >/dev/null || { _pe "זמן המערכת אינו מספר"; return; }
    printf '{"hwclock_epoch":%s,"system_epoch":%s,"skew_seconds":%s}' \
        "$_hw" "$_sys" "$((_hw - _sys))"
}

probe_cpu() {
    _f="$PROC_ROOT/cpuinfo"; [ -r "$_f" ] || { _pn; return; }
    _model=$(trim "$(awk -F': ' '/^model name|^Processor|^Hardware/{print $2; exit}' "$_f")")
    _cores=$(awk '/^processor/{n++} END{print n+0}' "$_f")
    _ucode=$(trim "$(awk -F': ' '/^microcode/{print $2; exit}' "$_f")")
    [ -n "$_model" ] || [ "$_cores" != 0 ] || { _pe "לא ניתן לקרוא את cpuinfo"; return; }
    _mj=null; [ -n "$_model" ] && _mj=$(_js "$_model")
    _uj=null; [ -n "$_ucode" ] && _uj=$(_js "$_ucode")
    _vuln=null
    if [ -d "$SYSFS_ROOT/devices/system/cpu/vulnerabilities" ]; then
        _vuln="{"; _c=0
        for _vf in "$SYSFS_ROOT/devices/system/cpu/vulnerabilities"/*; do
            [ -e "$_vf" ] || continue
            [ "$_c" = 0 ] || _vuln="$_vuln,"; _c=1
            _vuln="$_vuln$(_js "${_vf##*/}"):$(_js "$(trim "$(cat "$_vf" 2>/dev/null)")")"
        done
        _vuln="$_vuln}"
    fi
    printf '{"model":%s,"cores":%s,"microcode":%s,"vulnerabilities":%s}' \
        "$_mj" "$_cores" "$_uj" "$_vuln"
}

probe_memory() {
    _f="$PROC_ROOT/meminfo"; [ -r "$_f" ] || { _pn; return; }
    _kb=$(awk '/^MemTotal:/{print $2; exit}' "$_f")
    _num "$_kb" >/dev/null || { _pe "MemTotal חסר"; return; }
    _dimms=null
    if _have "$DMIDECODE"; then
        _raw=$("$DMIDECODE" -t 17 2>/dev/null) && _dimms=$(printf '%s\n' "$_raw" | awk '
            function emit(){ if(sz=="")return; if(n++)printf ",";
                printf "{\"size_bytes\":%s,\"speed_mts\":%s,\"manufacturer\":%s}",
                sz,(sp==""?"null":sp),(mf==""?"null":"\"" mf "\"") }
            /Memory Device/{emit(); sz=""; sp=""; mf=""}
            /^[[:space:]]*Size:/{ if($2=="No"){sz=""; next}
                sz=$2; if($3~/GB/)sz=sz*1073741824; else if($3~/MB/)sz=sz*1048576 }
            /^[[:space:]]*Speed:/{ if($2~/^[0-9]/) sp=$2 }
            /^[[:space:]]*Manufacturer:/{ mf=$2; for(i=3;i<=NF;i++) mf=mf " " $i }
            END{emit()}') && _dimms="[$_dimms]" || _dimms=$(_pe "dmidecode נכשל")
    fi
    _ecc=null
    if [ -d "$SYSFS_ROOT/devices/system/edac/mc" ]; then
        _ce=0; _ue=0; _n=0; _bad=""
        for _m in "$SYSFS_ROOT/devices/system/edac/mc"/mc*; do
            [ -e "$_m" ] || continue; _n=1
            _c=$(_rf "$_m/ce_count") && _u=$(_rf "$_m/ue_count") \
                && _num "$_c" >/dev/null && _num "$_u" >/dev/null \
                || { _bad=1; break; }
            _ce=$((_ce+_c)); _ue=$((_ue+_u))
        done
        [ -n "$_bad" ] && _ecc=$(_pe "מוני ה-ECC אינם קריאים")
        [ -z "$_bad" ] && [ "$_n" = 1 ] \
            && _ecc=$(printf '{"ce_count":%s,"ue_count":%s}' "$_ce" "$_ue")
    fi
    printf '{"total_bytes":%s,"dimms":%s,"ecc":%s}' "$((_kb*1024))" "$_dimms" "$_ecc"
}

probe_thermal() {
    _d="$SYSFS_ROOT/class/thermal"; [ -d "$_d" ] || { _pn; return; }
    printf '['; _c=0
    for _z in "$_d"/thermal_zone*; do
        [ -e "$_z" ] || continue
        _ty=$(_rf "$_z/type") && _t=$(_rf "$_z/temp") && _num "$_t" >/dev/null || continue
        [ "$_c" = 0 ] || printf ','; _c=1
        printf '{"type":%s,"temp_c":%s}' "$(_js "$_ty")" "$((_t/1000))"
    done
    printf ']'
}

probe_nic() {
    _d="$SYSFS_ROOT/class/net"; [ -d "$_d" ] || { _pn; return; }
    printf '['; _c=0
    for _p in "$_d"/*; do
        [ -e "$_p" ] || continue; _n=${_p##*/}; [ "$_n" = lo ] && continue
        _car=$(_rf "$_p/carrier") || continue; [ "$_car" = 1 ] || continue
        _sp=$(_rf "$_p/speed") || _sp=""; case "$_sp" in ''|*[!0-9]*) _sp=null ;; esac
        _du=$(_rf "$_p/duplex") || _du=""; _dj=null; [ -n "$_du" ] && _dj=$(_js "$_du")
        _st="$_p/statistics"
        _crc=$(_rf "$_st/rx_crc_errors") || _crc=null
        _dr=$(_rf "$_st/rx_dropped") || _dr=null
        _tx=$(_rf "$_st/tx_errors") || _tx=null
        _co=$(_rf "$_st/collisions") || _co=null
        _ip=null
        if _have "$ARPING" && [ -n "${IP:-}" ] && { [ -z "${IFACE:-}" ] || [ "$_n" = "$IFACE" ]; }; then
            "$ARPING" -D -c 2 -I "$_n" "$IP" >/dev/null 2>&1; _rc=$?
            case "$_rc" in
                0) _ip='{"checked":true,"duplicate":false}' ;;
                1) _ip='{"checked":true,"duplicate":true}' ;;
                *) _ip=$(_pe "arping rc $_rc") ;;
            esac
        fi
        [ "$_c" = 0 ] || printf ','; _c=1
        printf '{"name":%s,"speed_mbps":%s,"duplex":%s,"stats":{"rx_crc_errors":%s,"rx_dropped":%s,"tx_errors":%s,"collisions":%s},"ip_conflict":%s}' \
            "$(_js "$_n")" "$_sp" "$_dj" "$_crc" "$_dr" "$_tx" "$_co" "$_ip"
    done
    printf ']'
}

probe_pci_without_driver() {
    _d="$SYSFS_ROOT/bus/pci/devices"; [ -d "$_d" ] || { _pn; return; }
    printf '['; _c=0
    for _e in "$_d"/*; do
        [ -e "$_e" ] || continue; [ -e "$_e/driver" ] && continue
        _cl=$(_hex "$_e/class") && _v=$(_hex "$_e/vendor") && _i=$(_hex "$_e/device") || continue
        [ "$_c" = 0 ] || printf ','; _c=1
        printf '"%s:%s:%s"' "$_v" "$_i" "$_cl"
    done
    printf ']'
}

probe_kernel() {
    _ldj=null; _tnj=null
    if _ld=$(_rf "$SYSFS_ROOT/kernel/security/lockdown"); then _ldj=$(_js "$_ld"); fi
    if _tn=$(_rf "$PROC_ROOT/sys/kernel/tainted"); then
        _num "$_tn" >/dev/null && _tnj="$_tn" || { _pe "ערך ה-taint של הקרנל אינו מספר"; return; }
    fi
    [ "$_ldj" = "null" ] && [ "$_tnj" = "null" ] && { _pn; return; }
    printf '{"lockdown":%s,"taint":%s}' "$_ldj" "$_tnj"
}

probe_pstore() {
    _d="$SYSFS_ROOT/fs/pstore"; [ -d "$_d" ] || { _pn; return; }
    _files="["; _c=0; _ex=null; _cr=false
    for _f in "$_d"/*; do
        [ -f "$_f" ] || continue; _cr=true
        [ "$_c" = 0 ] && _ex=$(_js "$(dd if="$_f" bs=1 count=400 2>/dev/null)")
        [ "$_c" = 0 ] || _files="$_files,"; _c=1
        _files="$_files$(_js "${_f##*/}")"
    done
    printf '{"crashed":%s,"files":%s],"excerpt":%s}' "$_cr" "$_files" "$_ex"
}

probe_oem_key() {
    _f="$SYSFS_ROOT/firmware/acpi/tables/MSDM"; [ -r "$_f" ] || { _pn; return; }
    _k=$(dd if="$_f" bs=1 skip=56 count=29 2>/dev/null) || { _pe "טבלת MSDM אינה קריאה"; return; }
    case "$_k" in ?????-?????-?????-?????-?????) _js "$_k" ;;
        *) _pe "מפתח MSDM אינו ASCII של 29 בייט" ;; esac
}

probe_disks() {
    _d="$SYSFS_ROOT/block"; [ -d "$_d" ] || { _pn; return; }
    printf '['; _c=0
    for _b in "$_d"/sd* "$_d"/nvme*n* "$_d"/mmcblk*; do
        [ -e "$_b" ] || continue; _n=${_b##*/}
        case "$_n" in
            sd[a-z]|sd[a-z][a-z]) ;;
            nvme[0-9]*n[0-9]|nvme[0-9]*n[0-9][0-9]) ;;
            mmcblk[0-9]|mmcblk[0-9][0-9]) ;;
            *) continue ;;
        esac
        _nv=null
        case "$_n" in nvme*)
            if _have "$NVME"; then
                _nf="${TMPDIR:-/tmp}/probe.$$.$_n.nvme"; [ -d "${RUN_DIR:-}" ] && _nf="$RUN_DIR/probe.$_n.nvme"; capped_run "$NVME_MAX_S" "$_nf" "$NVME" smart-log -o json "$DEVROOT/$_n"; _nrc=$?; _jsn=$(cat "$_nf" 2>/dev/null); rm -f "$_nf"; [ "$CAPPED_TIMED_OUT" != 1 ] && [ "$_nrc" = 0 ] && [ -n "$_jsn" ] \
                    && _cw=$(_jint "$_jsn" critical_warning) && _pu=$(_jint "$_jsn" percentage_used) \
                    && _me=$(_jint "$_jsn" media_errors) && _us=$(_jint "$_jsn" unsafe_shutdowns) \
                    && [ -n "$_cw" ] && [ -n "$_pu" ] && [ -n "$_me" ] && [ -n "$_us" ] \
                    && _nv=$(printf '{"critical_warning":%s,"percentage_used":%s,"media_errors":%s,"unsafe_shutdowns":%s}' \
                        "$_cw" "$_pu" "$_me" "$_us") || { [ "$CAPPED_TIMED_OUT" = 1 ] && _nv=$(_pe "nvme smart-log לא ענה") || _nv=$(_pe "nvme smart-log נכשל"); }
            fi ;;
        esac
        _se=null
        if _have "$SMARTCTL"; then
            _sf="${TMPDIR:-/tmp}/probe.$$.$_n.smart"; [ -d "${RUN_DIR:-}" ] && _sf="$RUN_DIR/probe.$_n.smart"; capped_run "$SMARTCTL_MAX_S" "$_sf" "$SMARTCTL" -l error -j "$DEVROOT/$_n"; _rc=$?; _sj=$(cat "$_sf" 2>/dev/null); rm -f "$_sf"   # #1367: capped; #2125: removed after the read
            if [ "$CAPPED_TIMED_OUT" = 1 ]; then _se=$(_pe "smartctl לא ענה תוך ${SMARTCTL_MAX_S} שניות על $_n"); elif [ $((_rc & 2)) -ne 0 ]; then _se=$(_pe "smartctl לא הצליח לפתוח את $_n")
            else
                _cnt=$(_jint "$_sj" count); [ -z "$_cnt" ] && _cnt=$(_jint "$_sj" error_count)
                [ -n "$_cnt" ] && _se=$(printf '{"count":%s}' "$_cnt") \
                    || _se=$(_pe "ב-JSON של smartctl אין מונה")
            fi
        fi
        [ "$_c" = 0 ] || printf ','; _c=1
        printf '{"name":%s,"nvme_smart":%s,"smart_errors":%s}' "$(_js "$_n")" "$_nv" "$_se"
    done
    printf ']'
}

probe_encryption() {
    _have "$BLKID" || { _pn; return; }
    _raw=$("$BLKID" 2>/dev/null); _rc=$?
    [ "$_rc" = 0 ] || [ "$_rc" = 2 ] || { _pe "blkid rc $_rc"; return; }
    printf '['
    printf '%s\n' "$_raw" | awk '
        /TYPE="crypto_LUKS"/ || /TYPE="BitLocker"/ {
            node=$1; sub(/:$/,"",node)
            t="crypto_LUKS"; if ($0 ~ /TYPE="BitLocker"/) t="BitLocker"
            if (n++) printf ","
            printf "{\"node\":\"%s\",\"type\":\"%s\"}", node, t }'
    printf ']'
}

# Prints ,"probe":{...} — the fragment build_hello appends (like inventory_json).
# hello is the poll and stays cheap: arping (~2s) and smartctl per disk run once
# per PROBE_TTL seconds; between runs the last answer is served from $RUN_DIR.
probe_json() {
    _pc="${RUN_DIR:-/run/imagectl}/probe.json"; _now=$(date +%s 2>/dev/null) || _now=0
    [ -s "$_pc" ] && [ -s "$_pc.at" ] && [ $((_now - $(cat "$_pc.at"))) -lt "${PROBE_TTL:-600}" ] && { cat "$_pc"; return; }
    if [ -d "${_pc%/*}" ] && _probe_fresh > "$_pc.tmp" 2>/dev/null; then mv "$_pc.tmp" "$_pc"; printf '%s' "$_now" > "$_pc.at"; cat "$_pc"
    else rm -f "$_pc.tmp"; _probe_fresh; fi   # no RUN_DIR (tests, host) = no cache, never no answer
}
_probe_fresh() {
    _t0=$(date +%s 2>/dev/null) || _t0=""
    _power=$(probe_power); _rtc=$(probe_rtc); _cpu=$(probe_cpu)
    _mem=$(probe_memory); _th=$(probe_thermal); _nic=$(probe_nic)
    _pci=$(probe_pci_without_driver); _kn=$(probe_kernel)
    _pst=$(probe_pstore); _oem=$(probe_oem_key)
    _dk=$(probe_disks); _en=$(probe_encryption); _nt=$(command -v probe_ntfs >/dev/null 2>&1 && probe_ntfs || _pn)   # #980: hibernation.sh
    _t1=$(date +%s 2>/dev/null) || _t1=""
    _sec=null; [ -n "$_t0" ] && [ -n "$_t1" ] && _sec=$((_t1 - _t0))
    printf ',"probe":{"power":%s,"rtc":%s,"cpu":%s,"memory":%s,"thermal":%s,"nic":%s,"pci_without_driver":%s,"kernel":%s,"pstore":%s,"oem_key":%s,"disks":%s,"encryption":%s,"ntfs":%s,"probe_seconds":%s}' \
        "$_power" "$_rtc" "$_cpu" "$_mem" "$_th" "$_nic" "$_pci" \
        "$_kn" "$_pst" "$_oem" "$_dk" "$_en" "$_nt" "$_sec"
}
