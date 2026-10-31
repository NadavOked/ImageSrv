/* The last-resort screen (owner, 17/10: "no black screen without a GUI").
 * Rendered once at build time into a raw XRGB8888 block; on the machine,
 * agent/lib/gui/splash.sh centres it on the framebuffer when the GUI is not
 * coming back -- no process of ours is needed to show it. */
#ifndef IMAGESRV_SPLASH_H
#define IMAGESRV_SPLASH_H

/* Writes <w>x<h> pixels, 4 bytes each (cairo RGB24 = XRGB8888 little-endian,
 * the only fbdev format backend.c accepts), row after row with no padding.
 * Every corner pixel is the background, which splash.sh fills the rest of
 * the screen with. 0 on success, 1 on failure (said on stderr). */
int splash_render_raw(const char *path, int w, int h);

#endif
