#include <math.h>
#include "screens_install.h"
#include "install_osk.h"

/* #2778 (Nadav 09/11): the keyboard on the screen, for the mouse alone
 * (install.c opens it on a click in a field, or F5). The console's tokens: the
 * page's colour under a hairline and an overlay's shadow, the keys are its
 * buttons, "סיום" and a pressed Shift the action colour. The keys run left to
 * right on the RTL page, as on the keyboard itself.
 * Nadav after the screenshots: "in the middle, and it can be moved". It opens
 * in the middle of the whole screen; the grip strip at its top drags it, never
 * off the screen, and it stays where it was put while the installer runs. It
 * does not jump by itself when it covers the field in focus: the preview row
 * above the keys shows that field as the field shows it (dots for a password). */
#define OSK_KEY_H 42.0
#define OSK_GAP   6.0
#define OSK_PAD   12.0
#define OSK_ROW_U 14.5   /* the widest row: 13 keys and Backspace at 1.5 */
#define OSK_GRIP_H 30.0  /* the strip that drags it */
#define OSK_VIEW_H 48.0  /* the preview row: a 40px box and its gap */

/* row r's keys and their widths in key units, left to right */
static int row_keys(int r, int *ks, double *us) {
    int n = 0, first = 0;
    for (int i = 0; i < r && i < 4; i++) first += (int)strlen(OSK_LOWER[i]);
    if (r == 3) { ks[n] = OSK_SHIFT; us[n++] = 1.5; }
    if (r < 4) for (int i = 0; i < (int)strlen(OSK_LOWER[r]); i++) { ks[n] = first + i; us[n++] = 1; }
    if (r == 0) { ks[n] = OSK_BKSP; us[n++] = 1.5; }
    if (r == 4) { ks[n] = OSK_SPACE; us[n++] = 6; ks[n] = OSK_DONE; us[n++] = 2.5; }
    return n;
}

static void osk_key(App *a, cairo_t *cr, Rect r, int k) {
    const InstallState *s = &a->install;
    char ch[2] = { 0, 0 };
    const char *label = ch;
    int kind = BTN_PLAIN;
    switch (k) {
    case OSK_BKSP: label = "מחיקה"; break;
    case OSK_SHIFT: label = "Shift"; kind = s->osk_shift ? BTN_PRIMARY : BTN_PLAIN; break;
    case OSK_SPACE: label = "רווח"; break;
    case OSK_DONE: label = "סיום"; kind = BTN_PRIMARY; break;
    default: ch[0] = (char)osk_char(k, s->osk_shift);
    }
    int word = k >= OSK_CHARS;
    Text t = text_make(cr, word ? FONT_SANS : FONT_MONO, word ? 13 : 16, 500, label, 0,
                       word && k != OSK_SHIFT ? DIR_RTL : DIR_LTR);
    draw_btn(a, cr, r, &t, kind, HIT_INSTALL_OSK_BASE + k);
    text_free(&t);
}

/* the grip: six dots and its words, in the middle of the strip; a hairline under it */
static void osk_grip(App *a, cairo_t *cr, Rect g) {
    const Theme *t = a->theme;
    Text w = text_make(cr, FONT_SANS, 12, 500, "גרור להזזה", 0, DIR_RTL);
    double dots = 10, x = g.x + (g.w - w.w - dots - 10) / 2, cy = g.y + g.h / 2;
    for (int c = 0; c < 2; c++)
        for (int r = -1; r <= 1; r++) { cairo_arc(cr, x + 2 + c * 6, cy + r * 5, 1.6, 0, 2 * M_PI); draw_set(cr, t->muted); cairo_fill(cr); }
    text_draw(cr, &w, x + dots + 10, cy - w.h / 2, t->muted);
    text_free(&w);
    cairo_rectangle(cr, g.x + 1, g.y + g.h - 1, g.w - 2, 1); draw_set(cr, t->hair); cairo_fill(cr);
    hit_add(a, g, HIT_INSTALL_OSK_GRIP);
}

/* the preview row: the field in focus as it is drawn -- its label at the right, its text LTR */
static void osk_preview(App *a, cairo_t *cr, Rect v) {
    const Theme *t = a->theme; const InstallState *s = &a->install;
    draw_field_box(cr, t, v, 1);
    Text lab = text_make(cr, FONT_SANS, 12, 500, s->osk_label, (int)(v.w * .45), DIR_RTL);
    text_draw_r(cr, &lab, v.x + v.w - 12, v.y + (v.h - lab.h) / 2, t->muted);
    Text val = text_make(cr, FONT_MONO, 14, 400, s->osk_shown, (int)(v.w - lab.w - 48), DIR_LTR);
    double ty = v.y + (v.h - val.h) / 2;
    text_draw(cr, &val, v.x + 12, ty, t->ink);
    cairo_rectangle(cr, v.x + 12 + val.w + 1, ty, 1, val.h); draw_set(cr, t->indigo); cairo_fill(cr);
    text_free(&lab); text_free(&val);
    hit_add(a, v, HIT_INSTALL_OSK_BASE + OSK_PREVIEW);
}

void install_draw_osk(App *a, cairo_t *cr, InstallLayout l) {
    InstallState *s = &a->install;
    if (!s->osk || s->osk != s->view || !s->osk_field) return;
    const Theme *t = a->theme;
    double W = l.rail.x + l.rail.w, H = l.rail.h;
    double keys_h = 2 * OSK_PAD + 5 * OSK_KEY_H + 4 * OSK_GAP, h = OSK_GRIP_H + OSK_VIEW_H + keys_h, w = fmin(760, W - 32);
    double x = s->osk_placed ? s->osk_x : (W - w) / 2, y = s->osk_placed ? s->osk_y : (H - h) / 2;
    x = fmax(0, fmin(x, W - w)); y = fmax(0, fmin(y, H - h));     /* a smaller screen since: still on it */
    s->osk_x = x; s->osk_y = y; s->osk_w = w; s->osk_h = h; s->osk_sw = W; s->osk_sh = H;   /* the drag's frame (install.c) */
    Rect p = { x, y, w, h };
    double u = fmin(46, (p.w - 2 * OSK_PAD - 13 * OSK_GAP) / OSK_ROW_U);
    draw_box_shadow(cr, p, t->radius, 10, 30, -8, t->shadow_strong, t->shadow_strong_a);
    draw_fill_rrect(cr, p, t->radius, t->porcelain);
    draw_border_rrect(cr, p, t->radius, t->hair, 1);
    hit_add(a, p, HIT_INSTALL_OSK_BASE + OSK_KEYS);  /* a click between the keys is the panel's */
    osk_grip(a, cr, (Rect){ x, y, w, OSK_GRIP_H });
    osk_preview(a, cr, (Rect){ x + OSK_PAD, y + OSK_GRIP_H + 8, w - 2 * OSK_PAD, OSK_VIEW_H - 8 });
    double top = y + OSK_GRIP_H + OSK_VIEW_H;
    for (int r = 0; r < 5; r++) {
        int ks[16];
        double us[16], rw = 0;
        int n = row_keys(r, ks, us);
        for (int i = 0; i < n; i++) rw += us[i] * u + (i ? OSK_GAP : 0);
        double kx = p.x + (p.w - rw) / 2, ky = top + OSK_PAD + r * (OSK_KEY_H + OSK_GAP);   /* left to right: the keys are LTR on an RTL page */
        for (int i = 0; i < n; i++) { osk_key(a, cr, (Rect){ kx, ky, us[i] * u, OSK_KEY_H }, ks[i]); kx += us[i] * u + OSK_GAP; }
    }
}
