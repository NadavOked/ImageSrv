/* Text through Pango. This is where Hebrew comes from:
 *   - Pango itemises the string and runs the Unicode bidirectional
 *     algorithm (UAX#9) through FriBidi (pango/pango-bidi-type.c calls
 *     fribidi_get_par_embedding_levels_ex);
 *   - Pango shapes each run with HarfBuzz (pango/shape.c -> hb_shape);
 *   - Cairo rasterises the glyphs through FreeType.
 * Nothing in this program touches bidi or shaping itself. The base
 * direction is RTL (like <html dir="rtl">), with auto-dir on so a
 * paragraph that starts with Latin -- a MAC address -- resolves LTR,
 * exactly as a browser would. */
#ifndef IMAGESRV_TEXT_H
#define IMAGESRV_TEXT_H

#include <stddef.h>
#include <cairo.h>
#include <pango/pangocairo.h>
#include "theme.h"

/* index.html loads exactly these two families from Google Fonts. */
#define FONT_SANS_FAMILY "IBM Plex Sans Hebrew"
#define FONT_MONO_FAMILY "IBM Plex Mono"

typedef enum { FONT_SANS, FONT_MONO } Font;
typedef enum { DIR_RTL, DIR_LTR } Dir;

typedef struct {
    PangoLayout *layout;
    int w, h;                       /* logical pixel size */
} Text;

/* px = CSS font-size in px; weight = CSS font-weight (400/500/600).
 * width_px > 0 sets the box width (text wraps, start-aligned in <dir>);
 * <= 0 measures the natural single-line size. */
Text text_make(cairo_t *cr, Font font, double px, int weight,
               const char *utf8, int width_px, Dir dir);
/* A left-to-right line whose direction does not follow its first letter
 * (auto-dir off, like CSS direction:ltr): the menu's "a → b → c" flow, where
 * a Hebrew-first value must still read left to right. With a width it is
 * aligned to the right edge of the box (the start of the RTL page). */
Text text_make_ltr(cairo_t *cr, Font font, double px, int weight,
                   const char *utf8, int width_px);
/* Same, from Pango markup -- for a label with <b>…</b> inside it
 * (room.js / classes.js confirm text). Escape values with text_escape. */
Text text_make_markup(cairo_t *cr, Font font, double px, int weight,
                      const char *markup, int width_px, Dir dir);
/* g_markup_escape_text into <out>; returns out. */
char *text_escape(const char *utf8, char *out, size_t n);
/* The value of an <input>: 14px, physically right-aligned in <width_px>,
 * bidi inside the line but no auto-direction (see text.c). */
Text text_make_field(cairo_t *cr, const char *utf8, int width_px, int ltr);
/* Baseline of the first line, in px from the layout's top (align-items:baseline). */
double text_baseline(const Text *t);
/* Draw with the logical box's top-left at (x, y). */
void text_draw(cairo_t *cr, Text *t, double x, double y, Rgb c);
/* Draw with the logical box's top-RIGHT at (x_right, y) -- the RTL default. */
void text_draw_r(cairo_t *cr, Text *t, double x_right, double y, Rgb c);
void text_free(Text *t);
void text_log_mark(const char *card, const char *theme);   /* IMAGESRV_GUI_TEXTLOG (tests): "@@ card-theme" */

/* Height of one line of <font> at <px> -- what CSS calls line-height:normal. */
double text_line_height(cairo_t *cr, Font font, double px);

/* Positive evidence that the fonts index.html asks for are really present.
 * Returns 1 when both families resolve, else 0 and names the missing one
 * in <missing>. A machine that cannot render must say so (R09 s6). */
int text_fonts_present(char *missing, size_t n);

#endif
