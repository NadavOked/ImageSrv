# probedisplay.sh -- the screen the GUI draws on, as a probe field (#2849). POSIX sh.
# The work's cloner sat behind a KVM switch: no EDID, a fallback mode, and the
# drawers did not fit -- and nothing on the server said what resolution it had.
# Uses probe.sh's helpers (_rf, _js, _pn, SYSFS_ROOT); probe.sh calls it by name.
#   {"fb":"<driver>","size":"WxH","connectors":[{"name":..,"status":..,"edid_bytes":N,"mode":"WxH"|null}]}
# null = no framebuffer and no DRM connector (not checked); edid_bytes 0 = the
# monitor sent none -- the KVM switch's signature.
probe_display() {
    _pd_fb="$SYSFS_ROOT/class/graphics/fb0"; _pd_c=0; _pd_cs="["
    for _pd_d in "$SYSFS_ROOT"/class/drm/card*-*; do
        [ -r "$_pd_d/status" ] || continue
        _pd_st=$(_rf "$_pd_d/status") || _pd_st=unknown
        _pd_e=$(wc -c < "$_pd_d/edid" 2>/dev/null | tr -d ' ') || _pd_e=
        case "$_pd_e" in ''|*[!0-9]*) _pd_e=null ;; esac
        _pd_m=null; IFS= read -r _pd_mm < "$_pd_d/modes" 2>/dev/null && [ -n "$_pd_mm" ] && _pd_m=$(_js "$_pd_mm")
        [ "$_pd_c" = 0 ] || _pd_cs="$_pd_cs,"; _pd_c=1
        _pd_cs="$_pd_cs{\"name\":$(_js "${_pd_d##*/}"),\"status\":$(_js "$_pd_st"),\"edid_bytes\":$_pd_e,\"mode\":$_pd_m}"
    done
    _pd_n=null; _pd_s=null
    if _pd_v=$(_rf "$_pd_fb/name"); then _pd_n=$(_js "$_pd_v"); fi
    if _pd_v=$(_rf "$_pd_fb/virtual_size"); then _pd_s=$(_js "$(printf '%s' "$_pd_v" | tr ',' 'x')"); fi
    [ "$_pd_c" = 1 ] || [ "$_pd_n$_pd_s" != nullnull ] || { _pn; return; }
    printf '{"fb":%s,"size":%s,"connectors":%s]}' "$_pd_n" "$_pd_s" "$_pd_cs"
}
