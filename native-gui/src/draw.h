/* Cairo primitives that stand in for the CSS box model: rounded boxes,
 * borders, the layered login gradient and an approximation of box-shadow.
 * All coordinates are device pixels -- the CSS was authored at 1x and the
 * station screen is drawn at 1x, so 1 CSS px == 1 pixel here. */
#ifndef IMAGESRV_DRAW_H
#define IMAGESRV_DRAW_H

#include <cairo.h>
#include "theme.h"

typedef struct { double x, y, w, h; } Rect;

static inline int rect_has(Rect r, double px, double py) {
    return px >= r.x && px < r.x + r.w && py >= r.y && py < r.y + r.h;
}

void draw_set(cairo_t *cr, Rgb c);
void draw_set_a(cairo_t *cr, Rgb c, double a);

/* Path only (no fill/stroke). */
void draw_rrect_path(cairo_t *cr, Rect r, double radius);
/* background-color */
void draw_fill_rrect(cairo_t *cr, Rect r, double radius, Rgb c);
/* border: <lw>px solid <c>, drawn inside the rect like CSS border-box. */
void draw_border_rrect(cairo_t *cr, Rect r, double radius, Rgb c, double lw);

/* CSS box-shadow: <0> <dy> <blur> <spread> rgba(c, alpha).
 * Cairo has no gaussian blur; this stacks translucent rounded rects to
 * approximate the falloff. Close enough for a card on a dark gradient. */
void draw_box_shadow(cairo_t *cr, Rect r, double radius, double dy,
                     double blur, double spread, Rgb c, double alpha);

/* body.station background: radial glow + 160deg linear gradient
 * (station.css lines 7-9). */
void draw_station_background(cairo_t *cr, const Theme *t, double w, double h);

/* A small circle with a glow -- the LED on the trays (.tray::after). */
void draw_led(cairo_t *cr, double cx, double cy, double d, Rgb c);

#endif
