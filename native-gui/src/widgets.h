/* The pieces every card is built from -- console.css .btn / input /
 * select / .sheet / .error, station.css .tray / .big-bar / .cls-bars,
 * #toast -- drawn once here, used by the screens_*.c files. Every number
 * is a CSS value, cited where it is used. */
#ifndef IMAGESRV_WIDGETS_H
#define IMAGESRV_WIDGETS_H

#include "ui.h"
#include "text.h"
#include "brand.h"

void hit_add(App *a, Rect r, int id);
int hovered(const App *a, Rect r);
double dmax(double x, double y);

/* ---- console.css .btn / .btn.primary / .btn.danger ---------------------- */
enum { BTN_PLAIN, BTN_PRIMARY, BTN_DANGER, BTN_SUCCESS,      /* .native-btn{,.primary,.danger,.success} */
       BTN_WARN,       /* mockup 10/10: a risky choice that is not the default -- warn outline, not filled */
       BTN_GHOST_DANGER, /* a destructive action before it is confirmed: red words, no fill */
       BTN_OFF };      /* not available yet (the confirmation is not typed): drawn, not clickable */
Text btn_label(cairo_t *cr, const char *s);            /* 13.5px 500 */
double btn_height(cairo_t *cr);                        /* 9 + line + 9 + 2 */
double btn_width(const Text *label);                   /* 15 + w + 15 + 2 */
void draw_btn(App *a, cairo_t *cr, Rect b, Text *label, int kind, int id);

/* ---- console.css input[type=text|password|number], select ---------------- */
double field_height(cairo_t *cr);                      /* 10 + line(14px) + 10 + 2 */
void draw_field(App *a, cairo_t *cr, Rect r, const char *value, int mask,
                const char *placeholder, int id);
void draw_select(App *a, cairo_t *cr, Rect r, const char *shown, int id);
/* The bordered box of an input, shared with the installer's fields: console
 * .win .n-in (field fill, 1px border, --r-sm) and its :focus (action border + 3px ring). */
void draw_field_box(cairo_t *cr, const Theme *t, Rect r, int focus);
/* The open list of a <select>: drawn last so it is topmost; registers
 * HIT_OPTION_BASE + i for each row. */
void draw_popup(App *a, cairo_t *cr, Rect anchor, const char *const *opts, int n, int sel);

/* ---- text roles --------------------------------------------------------- */
Text label_make(cairo_t *cr, const char *s, double w);   /* label: 12px 500 muted */
Text rtl_block_make(cairo_t *cr, double px, int weight, const char *s, double w);  /* right-aligned RTL block */
/* .sub has NO rule in console.css or station.css (only .metric .sub, which
 * is not an ancestor here): a <p class="sub"> in the station page is body
 * text -- 14px, ink. Reproduced as the browser shows it, in one place. */
Text sub_make(cairo_t *cr, const char *s, double w);
Text error_make(cairo_t *cr, const char *s, double w);   /* .error: 12.5px danger */
#define ERROR_MIN_H 18.0                                 /* .error min-height */

/* ---- the card (console.css .sheet / .shead / .sbody / .sfoot) ------------ */
typedef struct { Text h3, p; double h; int logo; } Head;
Head head_make(cairo_t *cr, const char *h3, const char *p, double inner_w);
void head_logo(Head *hd);                                /* .native-logo above the title */
double card_width(double W);                             /* #765: W minus a proportional 12..28px inset */
/* #765: surface + head, clipped to a card that fills the framebuffer below the
 * header (no 92vh cap, no centring). *body is the content box (overflow is
 * clipped, there is no scrolling); *foot the footer box, pinned to the bottom. */
Rect card_frame(App *a, cairo_t *cr, double W, double H, double head_h, Head *hd,
                double body_h, double foot_h, Rect *body, Rect *foot);
void card_end(App *a, cairo_t *cr);
void body_clip_begin(App *a, cairo_t *cr, Rect body);
void body_clip_end(App *a, cairo_t *cr);
double foot_height(cairo_t *cr, double content_h);       /* 1 + 14 + content + 14 */
void foot_draw_bg(cairo_t *cr, const Theme *t, Rect foot);

/* ---- station.css pieces ------------------------------------------------- */
void draw_tray(cairo_t *cr, const Theme *t, double x, double y, int multi);
enum { ICON_DISK, ICON_WARNING, ICON_SUCCESS };
void draw_native_icon(cairo_t *cr, const Theme *t, Rect box, int kind);
/* mockup 10/10: the build menu's four icons, the SVGs' 24-unit paths --
 * capture (a disk, an arrow up to the library), clone from the server (a
 * server to three machines), direct clone (two disks, an arrow), restore
 * (a turning arrow round the disk). Stroked in <c> inside <box>. */
enum { MENU_ICON_CAPTURE, MENU_ICON_SERVER, MENU_ICON_DIRECT, MENU_ICON_RESTORE };
void draw_menu_icon(cairo_t *cr, Rect box, int kind, Rgb c);
/* A visible keyboard-focus ring: 2px in the action colour, 3px outside <r>
 * (the console's outline:2px; outline-offset). */
void draw_focus_ring(App *a, cairo_t *cr, Rect r);
void draw_station_art(cairo_t *cr, const Theme *t, Rect box, double opacity);
enum { PILL_OK, PILL_WARN, PILL_BAD, PILL_INFO, PILL_OFF };
double draw_pill(cairo_t *cr, const Theme *t, double right, double y,
                 const char *label, int kind, int dot);
/* The width draw_pill would give <label> -- to place a pill by its left edge. */
double pill_width(cairo_t *cr, const char *label, int dot);
double metric_height(cairo_t *cr);
void draw_metric(cairo_t *cr, const Theme *t, Rect box, const char *label, const char *value);
/* .bar.big-bar with a Progress.view state: pct 0..100, or -1 unknown
 * (moving = indeterminate stripes at 40%, else unknown-idle at 25%). */
void draw_big_bar(cairo_t *cr, const Theme *t, Rect r, int pct, int moving);
/* .native-bar: the 5px bar of a round card / room node (.native-bar i). */
void draw_thin_bar(cairo_t *cr, const Theme *t, Rect r, int pct, Rgb fill);
/* .native-alert: one paragraph in a warning-tinted box, full width. */
Text alert_make(cairo_t *cr, const char *s, double w);
double alert_height(const Text *body);
void draw_alert(cairo_t *cr, const Theme *t, Rect r, Text *body);
/* .native-status: the green dot + the screen's own word in the status bar. */
void draw_status_word(cairo_t *cr, const Theme *t, double x, double cy, const char *s, Rgb dot);
void draw_cls_bars(cairo_t *cr, const Theme *t, double x, double y, double w, int current);
void draw_toast(App *a, cairo_t *cr, double W, double H);

/* station.js fmtBytes / progress.js Progress.view label, ported. */
void fmt_bytes(char *out, size_t n, double v);
void progress_label(char *out, size_t n, int pct, int moving, int partition);

#endif
