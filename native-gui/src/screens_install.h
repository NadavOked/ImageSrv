#ifndef IMAGESRV_SCREENS_INSTALL_H
#define IMAGESRV_SCREENS_INSTALL_H

#include "install.h"
#include "widgets.h"

typedef struct { Rect content, body, footer, rail; double right; } InstallLayout;

InstallLayout install_frame(App *a, cairo_t *cr, double W, double H);
void install_card(cairo_t *cr, const Theme *t, Rect r);
void install_text_r(cairo_t *cr, const Theme *t, const char *value, double size,
                    int weight, double right, double y, double width, Rgb color);
void install_text_ltr(cairo_t *cr, const char *value, double size, int weight,
                      double x, double y, double width, Rgb color);
void install_field_draw(App *a, cairo_t *cr, Rect r, const char *label,
                        const char *value, int mask, int id, int enabled);
void install_radio(App *a, cairo_t *cr, Rect r, const char *title,
                   const char *desc, int selected, int id);
void install_focus_ring(App *a, cairo_t *cr, Rect r, int id);
void install_footer(App *a, cairo_t *cr, InstallLayout l, const char *primary,
                    int primary_id, int allow_back, int primary_enabled);
void install_draw_disk(App *a, cairo_t *cr, InstallLayout l);
void install_draw_role(App *a, cairo_t *cr, InstallLayout l);
void install_draw_network(App *a, cairo_t *cr, InstallLayout l);
void install_draw_forms(App *a, cairo_t *cr, InstallLayout l);
void install_draw_final(App *a, cairo_t *cr, InstallLayout l);
int install_render_png(App *a, const char *prefix, int w, int h);

#endif
