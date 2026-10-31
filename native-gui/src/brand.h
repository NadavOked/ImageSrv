/* The ImageSrv logo (#1302, option K "bit by bit", approved 12/10): a 3x3
 * pixel pattern and an identical copy of it, then the word ImageSrv. Drawn
 * with Cairo from the approved SVGs (C:\ImageCtl-Lab\brand-final), not from
 * a bitmap, so it is sharp at every size and on every background. */
#ifndef IMAGESRV_BRAND_H
#define IMAGESRV_BRAND_H

#include <cairo.h>
#include "draw.h"

/* The symbol on whole pixels: <cell> px squares. The gaps follow from the
 * cell exactly as in symbol-white-{24,32,64}.png. */
double brand_symbol_width(int cell);
double brand_symbol_height(int cell);
void draw_brand_symbol(cairo_t *cr, double x, double y, int cell, Rgb color);

/* Symbol + ImageSrv ("Image" in the ink, "Srv" in the brand blue), the
 * colours of logo-lockup.svg (on_dark=0) or logo-lockup-dark.svg (on_dark=1).
 * tagline=1 adds the Hebrew line under the word, as in the full lockup.
 * The right edge is at <right>, the symbol's top at <top> (with the tagline
 * the word starts .5 symbol-height above it, as in the SVG). Returns the left
 * edge. A logo is not mirrored in RTL: the symbol stays on the left. */
double brand_lockup_width(int cell);
double draw_brand_lockup_r(cairo_t *cr, double right, double top, int cell, int on_dark, int tagline);

#endif
