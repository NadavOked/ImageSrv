# splash.sh -- the last-resort screen, when the native GUI is not coming back.
# POSIX sh (busybox ash).
#
# Owner, 17/10: "a black screen is not supposed to exist without a GUI". The
# GUI is started again first (guiparent.sh); when that fails too, this paints
# a static screen in our design straight onto the framebuffer -- the ImageSrv
# symbol and "ImageSrv — מתאתחל…" -- not text and not black. No process of
# ours has to stay up to show it.
#
# The picture is rendered at build time by the GUI binary itself
# (imagesrv-station-gui --splash-raw, build_initramfs.sh) as one raw XRGB8888
# block, splash-<w>x<h>.raw. Here it is centred on a frame of the
# framebuffer's own size and stride, the rest filled with the block's corner
# pixel (the design's background) -- so it fits every resolution, and a
# padded stride, without a file per resolution.

SPLASH_DIR="${SPLASH_DIR:-/usr/share/imagesrv}"

splash_draw() {
    # 0 only on positive evidence: the whole frame was written to the
    # framebuffer. Anything else is 1 with the reason in the journal.
    _sp_fb=${FB_DEV:-/dev/fb0}; _sp_sys="$SYSROOT/sys/class/graphics/${_sp_fb##*/}"
    [ -w "$_sp_fb" ] || { log "splash: no framebuffer at $_sp_fb"; return 1; }
    set -- "$SPLASH_DIR"/splash-*x*.raw
    [ -f "$1" ] || { log "splash: no prebuilt screen in $SPLASH_DIR"; return 1; }
    _sp_raw=$1; _sp_bw=${1##*/splash-}; _sp_bw=${_sp_bw%.raw}; _sp_bh=${_sp_bw#*x}; _sp_bw=${_sp_bw%x*}
    IFS=, read -r _sp_w _sp_h < "$_sp_sys/virtual_size" && read -r _sp_st < "$_sp_sys/stride" &&
        read -r _sp_bpp < "$_sp_sys/bits_per_pixel" || { log "splash: cannot read the mode of $_sp_fb"; return 1; }
    case "$_sp_bw.$_sp_bh.$_sp_w.$_sp_h.$_sp_st" in
        *[!0-9.]*|*..*|.*|*.) log "splash: unreadable sizes ($_sp_bw x $_sp_bh on $_sp_w x $_sp_h, stride $_sp_st)"; return 1 ;;
    esac
    # backend.c draws XRGB8888 only; another depth would be a wrong picture.
    [ "$_sp_bpp" = 32 ] || { log "splash: ${_sp_bpp}bpp framebuffer, only 32 is drawn"; return 1; }
    [ "$_sp_bw" -le "$_sp_w" ] && [ "$_sp_bh" -le "$_sp_h" ] && [ "$_sp_st" -ge $((_sp_w * 4)) ] ||
        { log "splash: ${_sp_bw}x$_sp_bh does not fit ${_sp_w}x$_sp_h"; return 1; }
    [ "$(wc -c < "$_sp_raw")" -eq $((_sp_bw * _sp_bh * 4)) ] || { log "splash: $_sp_raw is not ${_sp_bw}x$_sp_bh"; return 1; }

    # The frame: the background pixel, doubled up to stride x height.
    _sp_f="$RUN_DIR/splash.frame"; _sp_total=$((_sp_st * _sp_h)); _sp_n=4
    head -c 4 "$_sp_raw" > "$_sp_f" || { log "splash: cannot build the frame"; return 1; }
    while [ "$_sp_n" -lt "$_sp_total" ]; do
        _sp_m=$_sp_n; [ $((_sp_n * 2)) -le "$_sp_total" ] || _sp_m=$((_sp_total - _sp_n))
        head -c "$_sp_m" "$_sp_f" > "$_sp_f.part" && cat "$_sp_f.part" >> "$_sp_f" ||
            { rm -f "$_sp_f" "$_sp_f.part"; log "splash: cannot build the frame"; return 1; }
        _sp_n=$((_sp_n + _sp_m))
    done
    rm -f "$_sp_f.part"
    # The block, one row at a time, centred. dd says "1+0 records" on stderr
    # every row; its exit code is the evidence.
    _sp_x=$(((_sp_w - _sp_bw) / 2 * 4)); _sp_y=$(((_sp_h - _sp_bh) / 2)); _sp_r=0
    while [ "$_sp_r" -lt "$_sp_bh" ]; do
        dd if="$_sp_raw" of="$_sp_f" bs=$((_sp_bw * 4)) skip="$_sp_r" count=1 \
            seek=$(((_sp_y + _sp_r) * _sp_st + _sp_x)) oflag=seek_bytes conv=notrunc 2>/dev/null ||
            { rm -f "$_sp_f"; log "splash: cannot place row $_sp_r"; return 1; }
        _sp_r=$((_sp_r + 1))
    done
    [ "$(wc -c < "$_sp_f")" -eq "$_sp_total" ] || { rm -f "$_sp_f"; log "splash: the frame is not $_sp_total bytes"; return 1; }

    # The kernel console must stop drawing on top of it (its cursor, our own
    # log lines, a kernel error): unbind it from the framebuffer.
    for _sp_vc in "$SYSROOT"/sys/class/vtconsole/vtcon*; do
        grep -q 'frame buffer' "$_sp_vc/name" 2>/dev/null || continue
        echo 0 > "$_sp_vc/bind" || log "splash: the text console stays bound -- it may draw over the screen"
    done
    cat "$_sp_f" > "$_sp_fb" || { rm -f "$_sp_f"; log "splash: writing $_sp_fb failed"; return 1; }
    rm -f "$_sp_f"
    : > "$RUN_DIR/splash.on"   # before the log line, so log() keeps it off the screen
    log "splash: the static screen is up (${_sp_w}x$_sp_h)"
}
