/* Where pixels go. Two ways onto a screen with no display server, and one
 * for a machine with no screen at all:
 *   drm   -- KMS dumb buffer on /dev/dri/cardN (preferred; R09 Q1)
 *   fbdev -- /dev/fb0, XRGB8888 only (fallback when KMS is unavailable)
 *   mem   -- a 1280x800 framebuffer file (#835) that imagesrv-monitor serves
 *            over RFB; docs/interfaces.md s15 has the layout
 * All draw into an offscreen cairo image and copy it to the scanout on
 * present(); the mapped scanout memory is uncached and slow to draw into
 * directly. There is no "png" backend: main.c renders straight to a cairo
 * image surface for --png. */
#ifndef IMAGESRV_BACKEND_H
#define IMAGESRV_BACKEND_H

#include <stddef.h>
#include <cairo.h>

typedef struct Backend Backend;

/* kind: "auto" (drm, then fbdev, then mem), "drm", "fbdev" or "mem".
 * mem_path: the file for "mem" (NULL = /run/imagesrv/fb.mem). NULL + message
 * on failure -- the message names what was tried, never a silent blank. */
Backend *backend_open(const char *kind, const char *mem_path, char *err, size_t n);
int backend_width(const Backend *b);
int backend_height(const Backend *b);
cairo_surface_t *backend_surface(Backend *b);     /* offscreen RGB24 */
void backend_present(Backend *b);
/* #1475: also write every presented frame into a memory-framebuffer file at
 * `path` (mem's layout and seqlock, the display's size), so imagesrv-monitor
 * reads RAM and sees from `seq` when nothing changed. No-op for mem. 0, or
 * -1 + why -- the display itself is unaffected either way. */
int backend_mirror(Backend *b, const char *path, char *err, size_t n);
/* #1362: 0 when the kernel confirms the last frame is on the screen (DRM:
 * the CRTC scans our framebuffer, DirtyFB accepted); -1 + why otherwise.
 * fbdev/mem have nothing to read back and return 0. */
int backend_verify(Backend *b, char *err, size_t n);
/* #1362: the VT backend_open puts in graphics mode (NULL = /dev/tty0, the
 * one in front). Call before backend_open. */
void backend_set_tty(const char *path);
/* #1362: VT_PROCESS mode -- relsig/acqsig arrive on a switch away / back;
 * answer them with backend_vt_release / backend_vt_acquire. */
int backend_vt_take(Backend *b, int relsig, int acqsig);
int backend_vt_activate(Backend *b, int vt);
void backend_vt_release(Backend *b);
int backend_vt_acquire(Backend *b);
/* Puts the display back as it was (saved CRTC / text-mode VT). */
void backend_close(Backend *b);

#endif
