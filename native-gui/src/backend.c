#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <linux/fb.h>
#include <linux/kd.h>
#include <linux/vt.h>
#include <xf86drm.h>
#include <xf86drmMode.h>
#include "backend.h"

enum { KIND_DRM, KIND_FBDEV, KIND_MEM };

/* ---- mem: a framebuffer file for the remote monitor (#835) -------------------
 * No display attached (every connector down, so i915 creates no /dev/fb0 --
 * cloner 2): the screens are drawn into a file that imagesrv-monitor mmaps
 * and serves over RFB. Layout, docs/interfaces.md s15: a 4096-byte header,
 * then XRGB8888 rows of `stride` bytes. `seq` is a seqlock -- odd while a
 * frame is being copied in -- so the reader can skip a torn frame instead of
 * showing half a screen. The header's constants are repeated verbatim in
 * agent/monitor.c; tests/test_headless_framebuffer.py pins the two equal. */
#define MEMFB_MAGIC "IMCTLFB1"
#define MEMFB_HEADER_BYTES 4096
#define MEMFB_FORMAT_XRGB8888 1
#define MEMFB_W 1280
#define MEMFB_H 800
#define MEMFB_DEFAULT_PATH "/run/imagesrv/fb.mem"

struct memfb_header {
    char magic[8];
    uint32_t width, height, stride, format, seq;
};

struct Backend {
    int kind, fd, w, h;
    uint32_t pitch;
    uint8_t *map; size_t map_len;
    uint8_t *pix;                               /* first pixel row inside map */
    cairo_surface_t *off;
    /* drm */
    uint32_t fb_id, handle, conn_id, crtc_id;
    drmModeModeInfo mode;
    drmModeCrtc *saved;
    int dirty_rc;                               /* #1362: drmModeDirtyFB of the last present */
    /* vt */
    int tty_fd, tty_mode;                       /* KDGETMODE fills an int */
    int vt_process;                             /* #1362: VT_SETMODE VT_PROCESS is ours */
    /* fbdev flush */
    struct fb_var_screeninfo fbvar;             /* for FBIOPAN_DISPLAY */
    /* #1475: a memory-framebuffer copy of every frame, for the monitor */
    uint8_t *mir_map; size_t mir_len; int mir_fd;
};

/* ---- VT: keep the kernel console from drawing over us --------------------- */

/* #1362: the server screen names its VT (tty1, the unit's TTYPath) instead of
 * /dev/tty0, which is whichever VT is in front when the GUI starts -- after an
 * upgrade restart that can be the operator's tty2. NULL = /dev/tty0. */
static const char *tty_path = NULL;
void backend_set_tty(const char *path) { tty_path = path; }

static void vt_graphics(Backend *b) {
    const char *path = tty_path ? tty_path : "/dev/tty0";
    b->tty_fd = open(path, O_RDWR | O_CLOEXEC);
    if (b->tty_fd < 0) { fprintf(stderr, "native-gui: %s: %s (console may draw over the screen)\n", path, strerror(errno)); return; }
    if (ioctl(b->tty_fd, KDGETMODE, &b->tty_mode) < 0) b->tty_mode = KD_TEXT;
    if (ioctl(b->tty_fd, KDSETMODE, KD_GRAPHICS) < 0)
        fprintf(stderr, "native-gui: KDSETMODE KD_GRAPHICS: %s (console may draw over the screen)\n", strerror(errno));
}

static void vt_restore(Backend *b) {
    if (b->tty_fd < 0) return;
    if (b->vt_process) {                        /* the kernel switches VTs on its own again */
        struct vt_mode m = { .mode = VT_AUTO };
        ioctl(b->tty_fd, VT_SETMODE, &m);
    }
    ioctl(b->tty_fd, KDSETMODE, b->tty_mode);
    close(b->tty_fd);
    b->tty_fd = -1;
}

/* ---- DRM / KMS ----------------------------------------------------------------- */

static int drm_try(Backend *b, const char *path, char *err, size_t n) {
    int fd = open(path, O_RDWR | O_CLOEXEC);
    if (fd < 0) { snprintf(err, n, "%s: %s", path, strerror(errno)); return -1; }
    drmModeRes *res = drmModeGetResources(fd);
    if (!res) { snprintf(err, n, "%s: no KMS resources (%s)", path, strerror(errno)); close(fd); return -1; }

    drmModeConnector *conn = NULL;
    for (int i = 0; i < res->count_connectors && !conn; i++) {
        drmModeConnector *c = drmModeGetConnector(fd, res->connectors[i]);
        if (!c) continue;
        if (c->connection == DRM_MODE_CONNECTED && c->count_modes > 0) conn = c;
        else drmModeFreeConnector(c);
    }
    if (!conn) { snprintf(err, n, "%s: no connected display", path); drmModeFreeResources(res); close(fd); return -1; }

    uint32_t crtc = 0;
    if (conn->encoder_id) {
        drmModeEncoder *e = drmModeGetEncoder(fd, conn->encoder_id);
        if (e) { crtc = e->crtc_id; drmModeFreeEncoder(e); }
    }
    for (int i = 0; i < conn->count_encoders && !crtc; i++) {
        drmModeEncoder *e = drmModeGetEncoder(fd, conn->encoders[i]);
        if (!e) continue;
        for (int j = 0; j < res->count_crtcs; j++)
            if (e->possible_crtcs & (1u << j)) { crtc = res->crtcs[j]; break; }
        drmModeFreeEncoder(e);
    }
    if (!crtc) { snprintf(err, n, "%s: no CRTC for connector %u", path, conn->connector_id); drmModeFreeConnector(conn); drmModeFreeResources(res); close(fd); return -1; }

    b->mode = conn->modes[0];                       /* preferred mode is first */
    b->w = b->mode.hdisplay; b->h = b->mode.vdisplay;
    b->conn_id = conn->connector_id; b->crtc_id = crtc;
    drmModeFreeConnector(conn);
    drmModeFreeResources(res);

    struct drm_mode_create_dumb creq = { 0 };
    creq.width = b->w; creq.height = b->h; creq.bpp = 32;
    if (drmIoctl(fd, DRM_IOCTL_MODE_CREATE_DUMB, &creq) < 0) { snprintf(err, n, "%s: create dumb buffer: %s", path, strerror(errno)); close(fd); return -1; }
    b->pitch = creq.pitch; b->handle = creq.handle; b->map_len = creq.size;
    if (drmModeAddFB(fd, b->w, b->h, 24, 32, creq.pitch, creq.handle, &b->fb_id)) { snprintf(err, n, "%s: drmModeAddFB: %s", path, strerror(errno)); close(fd); return -1; }
    struct drm_mode_map_dumb mreq = { 0 };
    mreq.handle = creq.handle;
    if (drmIoctl(fd, DRM_IOCTL_MODE_MAP_DUMB, &mreq) < 0) { snprintf(err, n, "%s: map dumb buffer: %s", path, strerror(errno)); close(fd); return -1; }
    b->map = mmap(NULL, b->map_len, PROT_READ | PROT_WRITE, MAP_SHARED, fd, mreq.offset);
    if (b->map == MAP_FAILED) { snprintf(err, n, "%s: mmap: %s", path, strerror(errno)); close(fd); return -1; }
    memset(b->map, 0, b->map_len);
    b->pix = b->map;

    b->saved = drmModeGetCrtc(fd, crtc);
    if (drmModeSetCrtc(fd, crtc, b->fb_id, 0, 0, &b->conn_id, 1, &b->mode)) {
        snprintf(err, n, "%s: drmModeSetCrtc: %s (is another program DRM master?)", path, strerror(errno));
        munmap(b->map, b->map_len); close(fd); return -1;
    }
    b->fd = fd; b->kind = KIND_DRM;
    return 0;
}

static int drm_open(Backend *b, char *err, size_t n) {
    static const char *paths[] = { "/dev/dri/card0", "/dev/dri/card1", "/dev/dri/card2" };
    char why[3][160];
    for (int i = 0; i < 3; i++)
        if (drm_try(b, paths[i], why[i], sizeof why[i]) == 0) return 0;
    snprintf(err, n, "no usable KMS device: %s; %s; %s", why[0], why[1], why[2]);
    return -1;
}

/* ---- fbdev ------------------------------------------------------------------------- */

static int fb_open(Backend *b, char *err, size_t n) {
    const char *path = "/dev/fb0";
    int fd = open(path, O_RDWR | O_CLOEXEC);
    if (fd < 0) { snprintf(err, n, "%s: %s", path, strerror(errno)); return -1; }
    struct fb_var_screeninfo vi; struct fb_fix_screeninfo fi;
    if (ioctl(fd, FBIOGET_VSCREENINFO, &vi) < 0 || ioctl(fd, FBIOGET_FSCREENINFO, &fi) < 0) {
        snprintf(err, n, "%s: FBIOGET_*SCREENINFO: %s", path, strerror(errno)); close(fd); return -1;
    }
    /* cairo RGB24 is XRGB8888 little-endian; anything else is refused, not
     * mis-coloured (R09 s6: say so, do not show a wrong screen). */
    if (vi.bits_per_pixel != 32 || vi.red.offset != 16 || vi.green.offset != 8 || vi.blue.offset != 0) {
        snprintf(err, n, "%s: %ubpp with R@%u G@%u B@%u; only XRGB8888 is supported",
                 path, vi.bits_per_pixel, vi.red.offset, vi.green.offset, vi.blue.offset);
        close(fd); return -1;
    }
    b->map_len = fi.smem_len;
    b->map = mmap(NULL, b->map_len, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (b->map == MAP_FAILED) { snprintf(err, n, "%s: mmap: %s", path, strerror(errno)); close(fd); return -1; }
    b->w = vi.xres; b->h = vi.yres; b->pitch = fi.line_length;
    b->pix = b->map;
    b->fbvar = vi;                              /* kept for the FBIOPAN_DISPLAY flush */
    b->fd = fd; b->kind = KIND_FBDEV;
    return 0;
}

/* ---- mem ---------------------------------------------------------------------------- */

static void mem_seq_step(Backend *b) {
    struct memfb_header *h = (struct memfb_header *)b->map;
    __atomic_add_fetch(&h->seq, 1, __ATOMIC_SEQ_CST);
}

static int mem_open(Backend *b, const char *path, char *err, size_t n) {
    if (!path || !*path) path = MEMFB_DEFAULT_PATH;
    /* No O_TRUNC: a monitor that already maps this inode would take SIGBUS
     * while the file is shrunk; ftruncate to the size it already has is a
     * no-op, and the monitor keeps seeing our frames through the same inode. */
    int fd = open(path, O_RDWR | O_CREAT | O_CLOEXEC, 0600);
    if (fd < 0) { snprintf(err, n, "%s: %s", path, strerror(errno)); return -1; }
    b->w = MEMFB_W; b->h = MEMFB_H; b->pitch = MEMFB_W * 4;
    b->map_len = MEMFB_HEADER_BYTES + (size_t)b->pitch * (size_t)b->h;
    if (ftruncate(fd, (off_t)b->map_len) < 0) { snprintf(err, n, "%s: ftruncate: %s", path, strerror(errno)); close(fd); return -1; }
    b->map = mmap(NULL, b->map_len, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (b->map == MAP_FAILED) { snprintf(err, n, "%s: mmap: %s", path, strerror(errno)); close(fd); return -1; }
    b->pix = b->map + MEMFB_HEADER_BYTES;
    struct memfb_header *h = (struct memfb_header *)b->map;
    /* A previous GUI that died mid-frame left seq odd; make it even before
     * the geometry is published, so the first frame the monitor sees is ours. */
    if (__atomic_load_n(&h->seq, __ATOMIC_SEQ_CST) & 1u) mem_seq_step(b);
    h->width = (uint32_t)b->w; h->height = (uint32_t)b->h; h->stride = b->pitch;
    h->format = MEMFB_FORMAT_XRGB8888;
    b->fd = fd; b->kind = KIND_MEM;
    mem_seq_step(b);
    memset(b->pix, 0, (size_t)b->pitch * (size_t)b->h);
    memcpy(h->magic, MEMFB_MAGIC, sizeof h->magic);   /* last: the file is valid only now */
    mem_seq_step(b);
    fprintf(stderr, "native-gui: no display -- drawing into %s for the remote monitor\n", path);
    return 0;
}

/* ---- mirror (#1475) ------------------------------------------------------------------
 * With a display, imagesrv-monitor used to read /dev/fb0 itself: a full
 * 1920x1080 read from write-combined video memory on every tick, changed or
 * not -- 60% of a hardware thread on cloner 2 during a local clone (i5-650,
 * owner, 16/10). The GUI already holds each frame in RAM, so it also writes
 * it into the same memory-framebuffer file the headless path uses (layout and
 * seqlock as mem above, at the display's own size). The monitor then reads
 * RAM, and an unchanged `seq` tells it nothing changed -- no read at all. */
int backend_mirror(Backend *b, const char *path, char *err, size_t n) {
    if (b->kind == KIND_MEM) return 0;          /* the frame already is the file */
    int fd = open(path, O_RDWR | O_CREAT | O_CLOEXEC, 0600);
    if (fd < 0) { snprintf(err, n, "%s: %s", path, strerror(errno)); return -1; }
    size_t len = MEMFB_HEADER_BYTES + (size_t)b->w * 4 * (size_t)b->h;
    /* No O_TRUNC, and grow only: a monitor may still map a larger file
     * from an earlier run, and a shrink under it is SIGBUS (see mem_open). */
    struct stat st;
    if (fstat(fd, &st) < 0) { snprintf(err, n, "%s: fstat: %s", path, strerror(errno)); close(fd); return -1; }
    if ((size_t)st.st_size < len && ftruncate(fd, (off_t)len) < 0) {
        snprintf(err, n, "%s: ftruncate: %s", path, strerror(errno)); close(fd); return -1;
    }
    uint8_t *map = mmap(NULL, len, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (map == MAP_FAILED) { snprintf(err, n, "%s: mmap: %s", path, strerror(errno)); close(fd); return -1; }
    struct memfb_header *h = (struct memfb_header *)map;
    if (__atomic_load_n(&h->seq, __ATOMIC_SEQ_CST) & 1u) __atomic_add_fetch(&h->seq, 1, __ATOMIC_SEQ_CST);
    h->width = (uint32_t)b->w; h->height = (uint32_t)b->h; h->stride = (uint32_t)b->w * 4;
    h->format = MEMFB_FORMAT_XRGB8888;
    memcpy(h->magic, MEMFB_MAGIC, sizeof h->magic);   /* last: the file is valid only now */
    b->mir_map = map; b->mir_len = len; b->mir_fd = fd;
    fprintf(stderr, "native-gui: mirroring frames into %s for the remote monitor\n", path);
    return 0;
}

/* ---- common ------------------------------------------------------------------------ */

Backend *backend_open(const char *kind, const char *mem_path, char *err, size_t n) {
    Backend *b = calloc(1, sizeof *b);
    b->fd = -1; b->tty_fd = -1; b->mir_fd = -1;
    int ok = -1;
    char e1[512] = "", e2[512] = "", e3[512] = "";
    if (strcmp(kind, "drm") == 0)        ok = drm_open(b, err, n);
    else if (strcmp(kind, "fbdev") == 0) ok = fb_open(b, err, n);
    else if (strcmp(kind, "mem") == 0)   ok = mem_open(b, mem_path, err, n);
    else if (strcmp(kind, "auto") == 0) {
        ok = drm_open(b, e1, sizeof e1);
        if (ok) ok = fb_open(b, e2, sizeof e2);
        if (ok) ok = mem_open(b, mem_path, e3, sizeof e3);
        if (ok) snprintf(err, n, "drm: %s / fbdev: %s / mem: %s", e1, e2, e3);
    } else snprintf(err, n, "unknown backend '%s' (auto|drm|fbdev|mem)", kind);
    if (ok) { free(b); return NULL; }

    b->off = cairo_image_surface_create(CAIRO_FORMAT_RGB24, b->w, b->h);
    if (cairo_surface_status(b->off) != CAIRO_STATUS_SUCCESS) {
        snprintf(err, n, "cairo: cannot create %dx%d surface", b->w, b->h);
        backend_close(b); return NULL;
    }
    if (b->kind != KIND_MEM) vt_graphics(b);   /* mem: nothing on a VT to protect */
    return b;
}

int backend_width(const Backend *b)  { return b->w; }
int backend_height(const Backend *b) { return b->h; }
cairo_surface_t *backend_surface(Backend *b) { return b->off; }

void backend_present(Backend *b) {
    cairo_surface_flush(b->off);
    const uint8_t *src = cairo_image_surface_get_data(b->off);
    int stride = cairo_image_surface_get_stride(b->off);
    size_t row = (size_t)b->w * 4;
    if (b->kind == KIND_MEM) mem_seq_step(b);   /* odd: a frame is being written */
    for (int y = 0; y < b->h; y++)
        memcpy(b->pix + (size_t)y * b->pitch, src + (size_t)y * stride, row);
    if (b->kind == KIND_MEM) mem_seq_step(b);   /* even: the frame is whole */
    if (b->mir_map) {                           /* #1475: same seqlock, RAM to RAM */
        struct memfb_header *mh = (struct memfb_header *)b->mir_map;
        uint8_t *mpix = b->mir_map + MEMFB_HEADER_BYTES;
        __atomic_add_fetch(&mh->seq, 1, __ATOMIC_SEQ_CST);     /* odd: being written */
        for (int y = 0; y < b->h; y++)
            memcpy(mpix + (size_t)y * row, src + (size_t)y * stride, row);
        __atomic_add_fetch(&mh->seq, 1, __ATOMIC_SEQ_CST);     /* even: whole */
    }
    /* On a modern i915 (Gen9+, e.g. the build laptop's Coffee Lake) the CPU's
     * writes to the mmap'd /dev/fb0 do not reach the scanout on their own: the
     * fb is write-combined and the display engine uses framebuffer compression,
     * so without a frontbuffer invalidate the panel keeps showing the last
     * frame (the build machine froze on a console frame while /dev/fb0 already
     * held the GUI). FBIOPAN_DISPLAY -> fb_pan_display calls the driver even at
     * yoffset 0 (kernel 6.12) and triggers intel_frontbuffer_invalidate -- that
     * is what makes the frame appear. An older i915 that scans /dev/fb0 out
     * directly (the IvyBridge cloner) does not need it, but the pan is cheap.
     * msync is kept only for a hypothetical driver whose fbdev is a
     * deferred-io shadow; on i915 there is no such shadow and it just returns
     * EINVAL, harmlessly. */
    if (b->kind == KIND_FBDEV) {
        msync(b->map, b->map_len, MS_SYNC);     /* no-op on i915; flushes a defio shadow elsewhere */
        ioctl(b->fd, FBIOPAN_DISPLAY, &b->fbvar);
    }
    /* #1362: a virtual GPU does not scan the dumb buffer out -- the host
     * copies it to the screen only for the damage it is told about. vmwgfx
     * (ESXi's SVGA) kept showing the black buffer of drmModeSetCrtc while
     * every frame sat in memory; virtio-gpu, qxl and udl work the same way.
     * A driver that does scan the buffer out has no dirty hook: -ENOSYS. */
    if (b->kind == KIND_DRM) b->dirty_rc = drmModeDirtyFB(b->fd, b->fb_id, NULL, 0);
}

/* #1362: principle 5 -- the screen is "up" on positive evidence read back
 * from the kernel, not because nothing returned an error: the CRTC scans out
 * our framebuffer in a valid mode, and the last frame's damage was accepted
 * (or the driver needs none). fbdev and mem have nothing to read back. */
int backend_verify(Backend *b, char *err, size_t n) {
    if (b->kind != KIND_DRM) return 0;
    drmModeCrtc *c = drmModeGetCrtc(b->fd, b->crtc_id);
    if (!c) { snprintf(err, n, "drm: CRTC %u not readable: %s", b->crtc_id, strerror(errno)); return -1; }
    int ok = c->mode_valid && c->buffer_id == b->fb_id;
    if (!ok) snprintf(err, n, "drm: CRTC %u shows fb %u, not ours (%u)", b->crtc_id, c->buffer_id, b->fb_id);
    drmModeFreeCrtc(c);
    if (!ok) return -1;
    if (b->dirty_rc && b->dirty_rc != -ENOSYS) {
        snprintf(err, n, "drm: DirtyFB refused: %s", strerror(-b->dirty_rc));
        return -1;
    }
    return 0;
}

/* ---- #1362: VT switching while the screen is ours ---------------------------------
 * The keyboard is grabbed (input.c), so the kernel never sees Alt+F2: the GUI
 * asks for the switch itself (backend_vt_activate). And a DRM master keeps the
 * CRTC even on tty2 -- fbcon cannot restore its mode while a master exists. So
 * the VT is put in VT_PROCESS mode: the kernel asks before switching away
 * (relsig -> drop master, release) and says when it is back (acqsig). */
int backend_vt_take(Backend *b, int relsig, int acqsig) {
    if (b->tty_fd < 0) return -1;
    struct vt_mode m = { .mode = VT_PROCESS, .relsig = (short)relsig, .acqsig = (short)acqsig };
    if (ioctl(b->tty_fd, VT_SETMODE, &m) < 0) {
        fprintf(stderr, "native-gui: VT_SETMODE VT_PROCESS: %s (Alt+Fn cannot leave this screen)\n", strerror(errno));
        return -1;
    }
    b->vt_process = 1;
    return 0;
}

int backend_vt_activate(Backend *b, int vt) {
    if (b->tty_fd < 0) return -1;
    return ioctl(b->tty_fd, VT_ACTIVATE, vt);
}

void backend_vt_release(Backend *b) {
    if (b->kind == KIND_DRM) drmDropMaster(b->fd);     /* fbcon may set its own mode now */
    if (b->tty_fd >= 0) ioctl(b->tty_fd, VT_RELDISP, 1);
}

int backend_vt_acquire(Backend *b) {
    int rc = 0;
    if (b->tty_fd >= 0) ioctl(b->tty_fd, VT_RELDISP, VT_ACKACQ);
    if (b->kind == KIND_DRM) {
        if (drmSetMaster(b->fd)) { fprintf(stderr, "native-gui: drmSetMaster: %s\n", strerror(errno)); rc = -1; }
        else if (drmModeSetCrtc(b->fd, b->crtc_id, b->fb_id, 0, 0, &b->conn_id, 1, &b->mode)) {
            fprintf(stderr, "native-gui: drmModeSetCrtc on return: %s\n", strerror(errno)); rc = -1;
        }
    }
    return rc;
}

void backend_close(Backend *b) {
    if (!b) return;
    vt_restore(b);
    if (b->off) cairo_surface_destroy(b->off);
    if (b->map && b->map != MAP_FAILED) munmap(b->map, b->map_len);
    if (b->mir_map) munmap(b->mir_map, b->mir_len);
    if (b->mir_fd >= 0) close(b->mir_fd);
    if (b->kind == KIND_DRM && b->fd >= 0) {
        if (b->saved) {
            drmModeSetCrtc(b->fd, b->saved->crtc_id, b->saved->buffer_id, b->saved->x, b->saved->y,
                           &b->conn_id, 1, &b->saved->mode);
            drmModeFreeCrtc(b->saved);
        }
        drmModeRmFB(b->fd, b->fb_id);
        struct drm_mode_destroy_dumb dreq = { 0 };
        dreq.handle = b->handle;
        drmIoctl(b->fd, DRM_IOCTL_MODE_DESTROY_DUMB, &dreq);
    }
    if (b->fd >= 0) close(b->fd);
    free(b);
}
