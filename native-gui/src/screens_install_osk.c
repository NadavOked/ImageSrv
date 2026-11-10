#include <math.h>
#include "screens_install.h"
#include "install_osk.h"

/* #2778 (Nadav 09/11): the keyboard on the screen, for the mouse alone
 * (install.c opens it on a click in a field). The console's tokens: the
 * page's colour under a hairline and an overlay's shadow, the keys are its
 * buttons, "סיום" and a pressed Shift the action colour. The keys run left to
 * right on the RTL page, as on the keyboard itself. It sits above the footer
 * ("הבא"/"חזור" stay clickable) and never over the field typed in: at the
 * bottom of the page, and above the field when the field is down there. */
#define OSK_KEY_H 42.0
#define OSK_GAP   6.0
#define OSK_PAD   12.0
#define OSK_ROW_U 14.5   /* the widest row: 13 keys and Backspace at 1.5 */

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

void install_draw_osk(App *a, cairo_t *cr, InstallLayout l) {
    const InstallState *s = &a->install;
    if (!s->osk || s->osk != s->view || s->osk_fh <= 0) return;
    const Theme *t = a->theme;
    double h = 2 * OSK_PAD + 5 * OSK_KEY_H + 4 * OSK_GAP, y = l.footer.y - 8 - h;
    if (y < s->osk_fy + s->osk_fh + 8) {             /* the field is down there: above it */
        y = s->osk_fy - 8 - h;
        if (y < 0) y = s->osk_fy + s->osk_fh + 8;     /* no room above either (a tiny screen): below it */
    }
    Rect p = { l.body.x, y, l.body.w, h };
    double u = fmin(46, (p.w - 2 * OSK_PAD - 13 * OSK_GAP) / OSK_ROW_U);
    draw_box_shadow(cr, p, t->radius, 10, 30, -8, t->shadow_strong, t->shadow_strong_a);
    draw_fill_rrect(cr, p, t->radius, t->porcelain);
    draw_border_rrect(cr, p, t->radius, t->hair, 1);
    hit_add(a, p, HIT_INSTALL_OSK_BASE + OSK_KEYS);  /* a click between the keys is the panel's */
    for (int r = 0; r < 5; r++) {
        int ks[16];
        double us[16], w = 0;
        int n = row_keys(r, ks, us);
        for (int i = 0; i < n; i++) w += us[i] * u + (i ? OSK_GAP : 0);
        double x = p.x + (p.w - w) / 2, ky = p.y + OSK_PAD + r * (OSK_KEY_H + OSK_GAP);
        for (int i = 0; i < n; i++) { osk_key(a, cr, (Rect){ x, ky, us[i] * u, OSK_KEY_H }, ks[i]); x += us[i] * u + OSK_GAP; }
    }
}
