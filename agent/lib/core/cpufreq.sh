#!/bin/sh
# CPU frequency setup for the initramfs.  Failure is deliberately non-fatal:
# old or unusual CPUs may have no Linux cpufreq driver at all.

cpufreq_performance() {
    _cpuroot="${SYSROOT:-}/sys/devices/system/cpu"
    _cpufreq="$_cpuroot/cpu0/cpufreq"

    if [ ! -d "$_cpufreq" ]; then
        modprobe acpi-cpufreq 2>/dev/null || :
    fi
    if [ ! -d "$_cpufreq" ]; then
        echo "cpufreq: no driver (CPU stays at firmware clock)"
        return 0
    fi

    _driver=$(cat "$_cpufreq/scaling_driver" 2>/dev/null)
    [ -n "$_driver" ] || _driver=unknown
    _result=performance
    for _dir in "$_cpuroot"/cpu[0-9]*/cpufreq; do
        [ -d "$_dir" ] || continue
        _available=$(cat "$_dir/scaling_available_governors" 2>/dev/null)
        case " $_available " in
            *" performance "*)
                if printf '%s\n' performance 2>/dev/null > "$_dir/scaling_governor"; then
                    _readback=$(cat "$_dir/scaling_governor" 2>/dev/null)
                    [ "$_readback" = performance ] || _result="read-back failed on $(basename "$(dirname "$_dir")")"
                else
                    _result="write failed on $(basename "$(dirname "$_dir")")"
                fi
                ;;
            *)
                _readback=$(cat "$_dir/scaling_governor" 2>/dev/null)
                _result="performance unavailable; governor=${_readback:-unknown}"
                ;;
        esac
    done
    echo "cpufreq: driver=$_driver governor=$_result"
    return 0
}
