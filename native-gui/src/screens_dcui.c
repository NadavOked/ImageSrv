/* The graphical server screen (#1203) -- drawing. Every number is the
 * approved mockup's CSS (C:\ImageCtl-Lab\design-approved\dcui\dcui.html,
 * Nadav 12/10), cited by its selector; ".sm" is its 1024x768 variant.
 * The colours are theme.c's tokens, which the mockup copied.
 *
 * Text direction: the page is RTL like <html dir="rtl"> -- base direction
 * RTL with auto-direction OFF, so "DHCP ו־TFTP לרשת ההפצה" keeps DHCP at the
 * right as the browser does (text.c's auto-dir would flip that paragraph to
 * LTR on its first letter). Addresses, names and fingerprints are LTR runs. */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "brand.h"
#include "dcui.h"
#include "draw.h"
#include "text.h"

typedef struct { cairo_t *cr; const Theme *t; int sm, dark; } G;

/* #1959: what a click can press, from the last dcui_draw, in screen pixels.
 * A click is the keys a person would have pressed (dcui_click): the backend
 * gets the same tokens either way, so a click never does what a key cannot. */
static DcuiHit hits[DCUI_MAX_HITS];
static int nhits;
static double hit_s = 1;

static void hit_add(Rect r, const char *what, const char *keys) {
    if (nhits >= DCUI_MAX_HITS || !keys[0]) return;
    DcuiHit *h = &hits[nhits++];
    *h = (DcuiHit){ r.x * hit_s, r.y * hit_s, r.w * hit_s, r.h * hit_s, "", "" };
    snprintf(h->what, sizeof h->what, "%s", what);
    snprintf(h->keys, sizeof h->keys, "%s", keys);
}

/* <count> times <tok>, then <last> (or nothing) -- one token per line */
static void keys_rep(char *out, size_t n, const char *tok, int count, const char *last) {
    size_t used = 0; out[0] = 0;
    for (int i = 0; i < count && used < n; i++) used += snprintf(out + used, n - used, "%s\n", tok);
    if (last && used < n) snprintf(out + used, n - used, "%s\n", last);
}

/* a key cap the backend has a token for ("Enter" -> "KEY ENTER"), or "" */
static const char *cap_token(const char *cap) {
    static const struct { const char *cap, *tok; } C[] = {
        { "Enter", "KEY ENTER" }, { "Esc", "KEY ESC" }, { "Tab", "KEY TAB" }, { "F2", "KEY F2" },
        { "F4", "KEY F4" }, { "F5", "KEY F5" }, { "F9", "KEY F9" }, { "F12", "KEY F12" } };
    for (size_t i = 0; i < sizeof C / sizeof C[0]; i++) if (!strcmp(C[i].cap, cap)) return C[i].tok;
    static char one[16];                                        /* #1962: "+" / "-" -- the character's own token */
    if (cap[0] > 0x20 && cap[0] < 0x7f && !cap[1] && !(cap[0] >= 'A' && cap[0] <= 'Z')) {
        snprintf(one, sizeof one, "CHAR %x", cap[0]);
        return one;
    }
    return "";
}

int dcui_hits(const DcuiHit **out) { *out = hits; return nhits; }

int dcui_click(double x, double y, char *keys, size_t n) {
    for (int i = nhits - 1; i >= 0; i--) {
        const DcuiHit *h = &hits[i];
        if (x >= h->x && x < h->x + h->w && y >= h->y && y < h->y + h->h) { snprintf(keys, n, "%s", h->keys); return 1; }
    }
    return 0;
}

static const Rgb DARK_ON = { 11 / 255.0, 17 / 255.0, 21 / 255.0 };   /* #0b1115: text on a filled colour, dark */
static const Rgb WHITE = { 1, 1, 1 };
static Rgb on_fill(const G *g) { return g->dark ? DARK_ON : WHITE; }

/* ---- text ----------------------------------------------------------------- */

#define MIN_PX 11.0   /* text.c TEXT_MIN_PX: nothing below 11px (mockup 10/10) */

static Text mk(G *g, Font font, double px, int weight, const char *s, double width, int ltr, int markup) {
    Text t;
    t.layout = pango_cairo_create_layout(g->cr);
    pango_context_set_base_dir(pango_layout_get_context(t.layout),
                               ltr ? PANGO_DIRECTION_LTR : PANGO_DIRECTION_RTL);
    pango_layout_set_auto_dir(t.layout, FALSE);
    pango_layout_context_changed(t.layout);
    PangoFontDescription *d = pango_font_description_new();
    pango_font_description_set_family(d, font == FONT_MONO ? FONT_MONO_FAMILY : FONT_SANS_FAMILY);
    pango_font_description_set_absolute_size(d, (px < MIN_PX ? MIN_PX : px) * PANGO_SCALE);
    pango_font_description_set_weight(d, weight);
    pango_layout_set_font_description(t.layout, d);
    pango_font_description_free(d);
    if (markup) pango_layout_set_markup(t.layout, s, -1);
    else pango_layout_set_text(t.layout, s, -1);
    if (width > 0) {
        pango_layout_set_width(t.layout, (int)(width * PANGO_SCALE));
        pango_layout_set_wrap(t.layout, PANGO_WRAP_WORD_CHAR);
        /* auto-dir off: LEFT/RIGHT are physical -- RTL text starts at the right */
        pango_layout_set_alignment(t.layout, ltr ? PANGO_ALIGN_LEFT : PANGO_ALIGN_RIGHT);
    }
    pango_layout_get_pixel_size(t.layout, &t.w, &t.h);
    return t;
}
static Text rtl(G *g, double px, int weight, const char *s, double width) { return mk(g, FONT_SANS, px, weight, s, width, 0, 0); }
static Text rtlm(G *g, double px, int weight, const char *s, double width) { return mk(g, FONT_SANS, px, weight, s, width, 0, 1); }
static Text mono(G *g, double px, int weight, const char *s) { return mk(g, FONT_MONO, px, weight, s, 0, 1, 0); }

/* Markup from the backend says <b>…</b>; the mockup's bold is also the ink
 * colour inside muted text (.hero .sub b, .na b, .hint b, .note b). */
static void ink_bold(const G *g, const char *src, char *out, size_t n) {
    char span[96];
    snprintf(span, sizeof span, "<span weight=\"600\" foreground=\"#%02X%02X%02X\">",
             (int)lround(g->t->ink.r * 255), (int)lround(g->t->ink.g * 255), (int)lround(g->t->ink.b * 255));
    size_t o = 0;
    for (const char *p = src; *p && o + 1 < n; ) {
        const char *rep = NULL; size_t skip = 0;
        if (!strncmp(p, "<b>", 3)) { rep = span; skip = 3; }
        else if (!strncmp(p, "</b>", 4)) { rep = "</span>"; skip = 4; }
        if (rep) {
            size_t l = strlen(rep);
            if (o + l >= n) break;
            memcpy(out + o, rep, l); o += l; p += skip;
        } else out[o++] = *p++;
    }
    out[o] = 0;
}
static Text rtlm_ink(G *g, double px, int weight, const char *s, double width) {
    char buf[4096];
    ink_bold(g, s, buf, sizeof buf);
    return mk(g, FONT_SANS, px, weight, buf, width, 0, 1);
}

static void put_r(G *g, Text *t, double right, double y, Rgb c) { text_draw_r(g->cr, t, right, y, c); }
static void put_l(G *g, Text *t, double x, double y, Rgb c) {
    PangoRectangle lg;
    pango_layout_get_pixel_extents(t->layout, NULL, &lg);
    text_draw(g->cr, t, x - lg.x, y, c);
}

/* ---- small pieces ----------------------------------------------------------- */

static Rgb pill_bg(const G *g, const char *k) {
    const Theme *t = g->t;
    if (!strcmp(k, "ok")) return t->success_soft;
    if (!strcmp(k, "warn")) return t->warning_soft;
    if (!strcmp(k, "bad")) return t->danger;
    if (!strcmp(k, "info")) return t->info_soft;
    return t->hover;
}
static Rgb pill_fg(const G *g, const char *k) {
    const Theme *t = g->t;
    if (!strcmp(k, "ok")) return t->led_ok;
    if (!strcmp(k, "warn")) return t->warn;
    if (!strcmp(k, "bad")) return on_fill(g);
    if (!strcmp(k, "info")) return t->info;
    return t->muted;
}

/* .pill: 24px high, 0 10px, radius 12, 12.5px 600, a 7px dot, gap 6 */
static double pill_w(G *g, const char *label) {
    Text l = rtl(g, 12.5, 600, label, 0);
    double w = 10 + 7 + 6 + l.w + 10;
    text_free(&l);
    return w;
}
static double pill(G *g, double x, double cy, const char *kind, const char *label) {
    if (!label[0]) return 0;
    Text l = rtl(g, 12.5, 600, label, 0);
    double w = 10 + 7 + 6 + l.w + 10;
    Rgb fg = pill_fg(g, kind);
    draw_fill_rrect(g->cr, (Rect){ x, cy - 12, w, 24 }, 12, pill_bg(g, kind));
    cairo_arc(g->cr, x + w - 10 - 3.5, cy, 3.5, 0, 2 * M_PI);
    draw_set(g->cr, fg); cairo_fill(g->cr);
    put_r(g, &l, x + w - 10 - 7 - 6, cy - l.h / 2.0, fg);
    text_free(&l);
    return w;
}

/* .kc: 600 11.5px mono, 20 high, min 24 wide, 0 5px, 1px border + 2px bottom, radius 4 */
typedef struct { Rgb bg, line, fg; double h, px; } KcStyle;
static KcStyle kc_plain(const G *g) { return (KcStyle){ g->t->surface, g->t->hair, g->t->ink, 20, 11.5 }; }
static double kc_w(G *g, const char *cap, double px) {
    Text c = mono(g, px, 600, cap);
    double w = fmax(24, c.w + 10);
    text_free(&c);
    return w;
}
static double kc(G *g, double right, double cy, const char *cap, KcStyle s) {
    Text c = mono(g, s.px, 600, cap);
    double w = fmax(24, c.w + 10), x = right - w, y = cy - s.h / 2;
    draw_fill_rrect(g->cr, (Rect){ x, y, w, s.h }, 4, s.line);                 /* the 2px bottom edge */
    draw_fill_rrect(g->cr, (Rect){ x + 1, y + 1, w - 2, s.h - 3 }, 3, s.bg);
    text_draw(g->cr, &c, x + (w - c.w) / 2, y + (s.h - 1 - c.h) / 2, s.fg);
    text_free(&c);
    return w;
}
/* several caps "→ ←": drawn right to left, 6px apart; returns the width */
static double kcs(G *g, double right, double cy, const char *caps, KcStyle s, int draw) {
    char buf[32]; snprintf(buf, sizeof buf, "%s", caps);
    double x = right, w = 0;
    for (char *save = NULL, *tok = strtok_r(buf, " ", &save); tok; tok = strtok_r(NULL, " ", &save)) {
        double cw = draw ? kc(g, x, cy, tok, s) : kc_w(g, tok, s.px);
        if (w > 0) w += 6;
        w += cw; x -= cw + 6;
    }
    return w;
}

static void stroke_style(cairo_t *cr, double lw) {
    cairo_set_line_width(cr, lw);
    cairo_set_line_cap(cr, CAIRO_LINE_CAP_ROUND);
    cairo_set_line_join(cr, CAIRO_LINE_JOIN_ROUND);
}

/* the mockup's lock: viewBox 16, rect 3,7 10x7 rx1.5 + the shackle; stroke 1.8 */
static void icon_lock(G *g, double x, double y, double size, Rgb c) {
    cairo_t *cr = g->cr;
    cairo_save(cr); cairo_translate(cr, x, y); cairo_scale(cr, size / 16, size / 16);
    stroke_style(cr, 1.8);
    draw_rrect_path(cr, (Rect){ 3, 7, 10, 7 }, 1.5);
    cairo_move_to(cr, 5.5, 7); cairo_line_to(cr, 5.5, 5);
    cairo_arc(cr, 8, 5, 2.5, M_PI, 2 * M_PI);
    cairo_line_to(cr, 10.5, 7);
    draw_set(cr, c); cairo_stroke(cr);
    cairo_restore(cr);
}

/* the four settings icons, viewBox 24, stroke 1.7 */
static void icon_menu(G *g, Rect box, const char *kind, Rgb c) {
    cairo_t *cr = g->cr;
    cairo_save(cr); cairo_translate(cr, box.x, box.y); cairo_scale(cr, box.w / 24, box.h / 24);
    stroke_style(cr, 1.7);
    cairo_new_path(cr);
    if (!strcmp(kind, "net")) {
        draw_rrect_path(cr, (Rect){ 3, 4, 18, 12 }, 2);
        cairo_move_to(cr, 8, 20); cairo_line_to(cr, 16, 20);
        cairo_move_to(cr, 12, 16); cairo_line_to(cr, 12, 20);
    } else if (!strcmp(kind, "rnet")) {
        cairo_arc(cr, 12, 12, 8, 0, atan2(6.4 - 12, 17.7 - 12) + 2 * M_PI);
        cairo_move_to(cr, 20, 4); cairo_line_to(cr, 20, 8); cairo_line_to(cr, 16, 8);
    } else if (!strcmp(kind, "svc")) {
        draw_rrect_path(cr, (Rect){ 4, 4, 16, 6 }, 1.5);
        draw_rrect_path(cr, (Rect){ 4, 14, 16, 6 }, 1.5);
        cairo_move_to(cr, 8, 7); cairo_line_to(cr, 8.01, 7);
        cairo_move_to(cr, 8, 17); cairo_line_to(cr, 8.01, 17);
    } else if (!strcmp(kind, "disk")) {                        /* #1988: a drive, its light and label */
        draw_rrect_path(cr, (Rect){ 3, 6, 18, 12 }, 2);
        cairo_move_to(cr, 7, 14); cairo_line_to(cr, 7.01, 14);
        cairo_move_to(cr, 11, 14); cairo_line_to(cr, 17, 14);
    } else {                                                   /* cert: the shield and a tick */
        cairo_move_to(cr, 12, 3); cairo_line_to(cr, 19, 6); cairo_line_to(cr, 19, 11);
        cairo_curve_to(cr, 19, 16, 16, 19, 12, 21);
        cairo_curve_to(cr, 8, 19, 5, 16, 5, 11);
        cairo_line_to(cr, 5, 6); cairo_close_path(cr);
        cairo_move_to(cr, 9, 12); cairo_line_to(cr, 11, 14); cairo_line_to(cr, 15, 10);
    }
    draw_set(cr, c); cairo_stroke(cr);
    cairo_restore(cr);
}

/* .hero .gl: a 56px disc, the 30-unit glyph inside at 30px, stroke 3 */
static void hero_glyph(G *g, double x, double y, const char *kind) {
    cairo_t *cr = g->cr;
    Rgb bg = !strcmp(kind, "bad") ? g->t->danger : !strcmp(kind, "wait") ? g->t->info : g->t->led_ok;
    cairo_arc(cr, x + 28, y + 28, 28, 0, 2 * M_PI); draw_set(cr, bg); cairo_fill(cr);
    cairo_save(cr); cairo_translate(cr, x + 13, y + 13);
    stroke_style(cr, 3);
    cairo_new_path(cr);
    if (!strcmp(kind, "bad")) {
        cairo_move_to(cr, 15, 8); cairo_line_to(cr, 15, 17);
        cairo_move_to(cr, 15, 22); cairo_line_to(cr, 15, 22.5);
    } else if (!strcmp(kind, "wait")) {
        cairo_arc(cr, 15, 15, 9, 0, 2 * M_PI);
        cairo_move_to(cr, 15, 10); cairo_line_to(cr, 15, 15); cairo_line_to(cr, 18.5, 17.5);
    } else {
        cairo_move_to(cr, 8, 15.5); cairo_line_to(cr, 13, 20.5); cairo_line_to(cr, 22.5, 9.5);
    }
    draw_set(cr, on_fill(g)); cairo_stroke(cr);
    cairo_restore(cr);
}

static void hline(G *g, double x, double y, double w, Rgb c) {
    cairo_rectangle(g->cr, x, y, w, 1); draw_set(g->cr, c); cairo_fill(g->cr);
}

/* ---- header (.hd) and status line (.sb) -------------------------------------- */

static void header(G *g, const DcuiFrame *f, double W) {
    cairo_t *cr = g->cr;
    const double H = 48, pad = g->sm ? 16 : 24;
    draw_fill_rrect(cr, (Rect){ 0, 0, W, H }, 0, g->t->header);
    double right = W - pad;
    /* the compact dark lockup, 22px tall (.hd img): 4px cells */
    right = draw_brand_lockup_r(cr, right, (H - brand_symbol_height(4)) / 2, 4, 1, 0);
    if (!f->valid) return;
    const Rgb dim = { 1, 1, 1 };
    if (f->role[0]) {                                           /* .role: margin 18, 22 high, radius 11 */
        right -= 18;
        Text r = rtl(g, 12.5, 600, f->role, 0);
        Rect chip = { right - r.w - 20, (H - 22) / 2, r.w + 20, 22 };
        draw_set_a(cr, dim, .14); draw_rrect_path(cr, chip, 11); cairo_fill(cr);
        put_r(g, &r, right - 10, chip.y + (22 - r.h) / 2, WHITE);
        right = chip.x;
        text_free(&r);
    }
    const char *keys[2] = { "שם", "קונסולה" }, *vals[2] = { f->host, f->url };
    for (int i = 0; i < 2; i++) {                               /* .kv: margin 20, gap 6, 13px */
        if (!vals[i][0] || (i == 1 && g->sm)) continue;         /* .sm hides the address */
        right -= 20;
        Text k = rtl(g, 13, 400, keys[i], 0), v = mono(g, 13, 600, vals[i]);
        cairo_save(cr); cairo_push_group(cr);
        put_r(g, &k, right, (H - k.h) / 2, WHITE);
        cairo_pop_group_to_source(cr); cairo_paint_with_alpha(cr, .7); cairo_restore(cr);
        right -= k.w + 6;
        put_r(g, &v, right, (H - v.h) / 2, WHITE);
        right -= v.w;
        text_free(&k); text_free(&v);
    }
    double x = pad;                                             /* .end: clock, version, admin; gap 18 */
    Text clock = mono(g, 15, 500, f->clock);
    put_l(g, &clock, x, (H - clock.h) / 2, WHITE);
    x += clock.w + 18;
    text_free(&clock);
    Text vv = mono(g, 12, 400, f->version), vk = rtl(g, 12, 400, "גרסה", 0);
    cairo_push_group(cr);
    put_l(g, &vv, x, (H - vv.h) / 2, WHITE);
    put_l(g, &vk, x + vv.w + 4, (H - vk.h) / 2, WHITE);
    cairo_pop_group_to_source(cr); cairo_paint_with_alpha(cr, .7);
    x += vv.w + 4 + vk.w + 18;
    text_free(&vv); text_free(&vk);
    if (f->admin[0]) {                                          /* .adm */
        Text a = rtl(g, 12, 600, f->admin, 0);
        Rect chip = { x, (H - 22) / 2, a.w + 20, 22 };
        draw_set_a(cr, (Rgb){ 225 / 255.0, 179 / 255.0, 79 / 255.0 }, .22);
        draw_rrect_path(cr, chip, 11); cairo_fill(cr);
        put_r(g, &a, chip.x + chip.w - 10, chip.y + (22 - a.h) / 2, (Rgb){ 1, 217 / 255.0, 138 / 255.0 });
        text_free(&a);
    }
}

static Rgb dot_color(const G *g, const char *k) {
    if (!strcmp(k, "warn")) return g->t->warn;
    if (!strcmp(k, "bad")) return g->t->danger;
    if (!strcmp(k, "idle")) return g->t->led_idle;
    if (!strcmp(k, "info")) return g->t->info;
    return g->t->led_ok;
}

/* one key item: caps (right), label, lock (left); returns its width */
static double key_item(G *g, double right, double cy, const DcuiKey *k, int draw) {
    double w = kcs(g, right, cy, k->caps, kc_plain(g), draw);
    Text l = rtl(g, 12.5, 400, k->label, 0);
    if (draw) put_r(g, &l, right - w - 6, cy - l.h / 2.0, g->t->muted);
    w += 6 + l.w;
    text_free(&l);
    if (k->lock) {
        if (draw) icon_lock(g, right - w - 6 - 13, cy - 6.5, 13, g->t->muted);
        w += 6 + 13;
    }
    return w;
}

static void status_line(G *g, const DcuiFrame *f, double W, double H) {
    cairo_t *cr = g->cr;
    const double h = 40, y = H - h, cy = y + h / 2, pad = g->sm ? 16 : 24, gap = g->sm ? 10 : 14;
    draw_fill_rrect(cr, (Rect){ 0, y, W, h }, 0, g->t->status_bg);
    hline(g, 0, y, W, g->t->hair);
    if (!f->valid) return;
    double right = W - pad;                                     /* .w: dot, word, rest; gap 8 */
    cairo_arc(cr, right - 4.5, cy, 4.5, 0, 2 * M_PI); draw_set(cr, dot_color(g, f->dot)); cairo_fill(cr);
    right -= 9 + 8;
    Text w = rtl(g, 12.5, 700, f->word, 0);
    put_r(g, &w, right, cy - w.h / 2.0, g->t->ink);
    right -= w.w + 8;
    text_free(&w);
    Text r = rtl(g, 12.5, 400, f->rest, 0);
    put_r(g, &r, right, cy - r.h / 2.0, g->t->muted);
    text_free(&r);
    /* .keys at the left edge; the first item is the rightmost of the group */
    double total = 0, lang_w = 0;
    KcStyle en = { g->t->surface, g->t->indigo, g->t->action_fg, 20, 11.5 };   /* .lang .kc */
    const char *chip = f->lang_chip[0] ? f->lang_chip : "EN";  /* #2001: the station GUI's typing language */
    if (f->lang[0]) {
        Text lt = rtl(g, 12.5, 400, f->lang, 0);
        lang_w = kc_w(g, chip, 11.5) + 8 + lt.w;
        text_free(&lt);
        total += lang_w;
    }
    for (int i = 0; i < f->nkeys; i++) total += (total > 0 ? gap : 0) + key_item(g, 0, cy, &f->keys[i], 0);
    double x = pad + total;
    if (f->lang[0]) {
        double cw = kc(g, x, cy, chip, en);
        if (f->lang_chip[0]) hit_add((Rect){ x - cw, y, cw, h }, "lang", "KEY LAYOUT\n");   /* a click = Alt+Shift */
        Text lt = rtl(g, 12.5, 400, f->lang, 0);
        put_r(g, &lt, x - cw - 8, cy - lt.h / 2.0, g->t->muted);
        text_free(&lt);
        x -= lang_w + gap;
    }
    for (int i = 0; i < f->nkeys; i++) {
        double kw = key_item(g, x, cy, &f->keys[i], 1);
        char what[128], keys[32] = "";
        snprintf(what, sizeof what, "key:%s", f->keys[i].caps);
        if (cap_token(f->keys[i].caps)[0]) snprintf(keys, sizeof keys, "%s\n", cap_token(f->keys[i].caps));
        hit_add((Rect){ x - kw, y, kw, h }, what, keys);   /* #1959 */
        x -= kw + gap;
    }
}

/* ---- rows (.row) ------------------------------------------------------------- */

typedef struct { double x, w; double label_w; double pad_x; } RowBox;

static double row_draw(G *g, const DcuiRow *r, RowBox b, double y, int first, int draw) {
    const Theme *t = g->t;
    const int fp = !strcmp(r->kind, "fprow");
    const int stacked = fp && g->sm;                            /* .sm .row.fpr: the label on its own line */
    const double pad_y = g->sm ? 5 : 7, right = b.x + b.w;
    double pw = r->pill[0] ? pill_w(g, r->pill) : 0;
    double vx = b.x + (pw > 0 ? pw + 12 : 0);                   /* the value cell's left edge */
    double vright = stacked ? right : right - b.label_w - 12;
    double vw = vright - vx;
    if (vw < 40) vw = 40;
    /* label */
    Text lab = r->label_style[0] ? mono(g, 12.5, 600, r->label) : rtl(g, 13.5, 400, r->label, stacked ? 0 : b.label_w);
    Text sl = { 0 };
    if (fp) {
        if (stacked) {
            char both[160]; snprintf(both, sizeof both, "%s %s", r->label, r->sublabel);
            text_free(&lab); lab = rtl(g, 13.5, 400, both, 0);
        } else sl = rtl(g, 12, 400, r->sublabel, b.label_w);
    }
    double lab_h = lab.h + (sl.layout ? sl.h : 0);
    /* value */
    Text v1 = { 0 }, v2 = { 0 };
    double val_h = 0;
    if (fp) {
        double px = g->sm ? 11 : 12.5;
        v1 = mk(g, FONT_MONO, px, 500, r->value, 0, 1, 0);
        v2 = mk(g, FONT_MONO, px, 500, r->value2, 0, 1, 0);
        val_h = v1.h + v2.h;
    } else if (!strcmp(r->value_style, "strong-mono")) {
        v1 = mk(g, FONT_MONO, 15, 600, r->value, 0, 1, 0); val_h = v1.h;
    } else if (!strcmp(r->value_style, "mono")) {
        v1 = mk(g, FONT_MONO, 13.5, 600, r->value, 0, 1, 0); val_h = v1.h;
    } else {
        v1 = rtl(g, 14, 400, r->value, vw); val_h = v1.h;
    }
    Text sub = { 0 };
    int tone_bad = !strcmp(r->tone, "bad"), tone_warn = !strcmp(r->tone, "warn");
    if (r->sub[0]) {
        sub = (tone_bad || tone_warn) ? rtl(g, 12.5, 600, r->sub, vw) : rtl(g, 12.5, 400, r->sub, vw);
        val_h += 1 + sub.h;
    }
    double body = stacked ? lab.h + 2 + fmax(val_h, 24) : fmax(fmax(lab_h, val_h), r->pill[0] ? 24 : 0);
    double h = pad_y + body + pad_y;
    if (draw) {
        if (tone_bad || tone_warn)
            draw_fill_rrect(g->cr, (Rect){ b.x - b.pad_x, y, b.w + 2 * b.pad_x, h }, 0,
                            tone_bad ? t->danger_soft : t->warning_soft);
        else if (!first) hline(g, b.x, y, b.w, t->hair);
        double top = y + pad_y, cy = top + body / 2;
        if (stacked) {
            put_r(g, &lab, right, top, t->muted);
            top += lab.h + 2; cy = top + fmax(val_h, 24) / 2;
        } else {
            double ly = top + (body - lab_h) / 2;
            put_r(g, &lab, right, ly, r->label_style[0] ? t->ink : t->muted);
            if (sl.layout) put_r(g, &sl, right, ly + lab.h, t->muted);
        }
        double vy = cy - val_h / 2;
        if (fp) {                                               /* .fp: LTR, text-align:left in the cell */
            put_l(g, &v1, vx, vy, t->ink); put_l(g, &v2, vx, vy + v1.h, t->ink);
            vy += v1.h + v2.h;
        } else {
            put_r(g, &v1, vright, vy, t->ink); vy += v1.h;
        }
        if (sub.layout) put_r(g, &sub, vright, vy + 1, tone_bad ? t->danger : tone_warn ? t->warn : t->muted);
        if (r->pill[0]) pill(g, b.x, cy, r->pill_kind, r->pill);
    }
    text_free(&lab); text_free(&sl); text_free(&v1); text_free(&v2); text_free(&sub);
    return h;
}

/* ---- main: hero + tiles ------------------------------------------------------ */

static double problem_draw(G *g, const DcuiProb *p, double x, double w, double y, int draw) {
    const Theme *t = g->t;
    const double px = g->sm ? 12 : 14, py = 9;
    const int warn = !strcmp(p->sev, "warn");
    double kw = 0;
    if (p->cap[0]) {
        Text kl = rtl(g, 12.5, 600, p->cap_label, 0);
        kw = kc_w(g, p->cap, 11.5) + 6 + kl.w;
        text_free(&kl);
    }
    double tw = w - 4 - px - 28 - 12 - px - (kw > 0 ? kw + 12 : 0);
    Text ti = rtl(g, 14.5, 700, p->title, tw), wh = rtl(g, 13, 400, p->what, tw);
    Text td = rtlm_ink(g, 13, 400, p->todo_m, tw);
    double h = py + fmax(26, ti.h + 1 + wh.h + 1 + td.h) + py;
    if (draw) {
        Rgb edge = warn ? t->warn : t->danger;
        draw_fill_rrect(g->cr, (Rect){ x, y, w, h }, 6, t->surface);
        cairo_save(g->cr);                                      /* border-inline-start: 4px, the right edge */
        draw_rrect_path(g->cr, (Rect){ x, y, w, h }, 6); cairo_clip(g->cr);
        cairo_rectangle(g->cr, x + w - 4, y, 4, h); draw_set(g->cr, edge); cairo_fill(g->cr);
        cairo_restore(g->cr);
        double right = x + w - 4 - px, ty = y + py;
        cairo_arc(g->cr, right - 12, ty + 13, 12, 0, 2 * M_PI); draw_set(g->cr, edge); cairo_fill(g->cr);
        Text n = rtl(g, 14, 700, p->n, 0);
        text_draw(g->cr, &n, right - 12 - n.w / 2.0, ty + 13 - n.h / 2.0, on_fill(g));
        text_free(&n);
        right -= 28 + 12;
        put_r(g, &ti, right, ty, t->ink); ty += ti.h + 1;
        put_r(g, &wh, right, ty, t->muted); ty += wh.h + 1;
        put_r(g, &td, right, ty, t->ink);
        if (kw > 0) {                                           /* .is .k: centred, action colour */
            double cy = y + h / 2, kx = x + px + kw;
            double cw = kc(g, kx, cy, p->cap, kc_plain(g));
            Text kl = rtl(g, 12.5, 600, p->cap_label, 0);
            put_r(g, &kl, kx - cw - 6, cy - kl.h / 2.0, t->action_fg);
            text_free(&kl);
        }
    }
    text_free(&ti); text_free(&wh); text_free(&td);
    return h;
}

static double hero(G *g, const DcuiFrame *f, double x, double y, double w, int draw) {
    const Theme *t = g->t;
    const int bad = !strcmp(f->hero_kind, "bad");
    const double ppy = bad ? 16 : (g->sm ? 16 : 20), ppx = bad ? 22 : (g->sm ? 18 : 24);
    const double bw = bad ? 2 : 1;
    double inner = w - 2 * ppx - 2 * bw;
    Text ml = { 0 }, mv = { 0 };
    double meta_w = 0, meta_h = 0;
    if (f->meta_label[0]) {
        ml = rtl(g, 12, 400, f->meta_label, 0); mv = mono(g, 15, 600, f->meta_value);
        meta_w = fmax(ml.w, mv.w); meta_h = 4 + ml.h + 4 + mv.h;
    }
    double tw = inner - 56 - 20 - (meta_w > 0 ? meta_w + 20 : 0);
    Text h1 = rtl(g, bad ? (g->sm ? 22 : 24) : (g->sm ? 22 : 26), 600, f->hero_title, tw);
    Text sub = rtlm_ink(g, 14, 400, f->hero_sub_m, tw);
    double text_h = h1.h + 4 + sub.h, probs_h = 0;
    double pw = inner - 56 - 20;                                /* .iss sits in the text column */
    if (bad && f->nprobs) {
        probs_h = g->sm ? 10 : 12;
        for (int i = 0; i < f->nprobs; i++)
            probs_h += (i ? (g->sm ? 8 : 10) : 0) + problem_draw(g, &f->probs[i], 0, pw, 0, 0);
    }
    double body = fmax(56, fmax(text_h + probs_h, meta_h));
    double h = bw + ppy + body + ppy + bw;
    if (draw) {
        Rect box = { x, y, w, h };
        draw_fill_rrect(g->cr, box, 8, bad ? t->danger : t->hair);
        draw_fill_rrect(g->cr, (Rect){ x + bw, y + bw, w - 2 * bw, h - 2 * bw }, 8 - bw, bad ? t->danger_soft : t->surface);
        double right = x + w - bw - ppx, top = y + bw + ppy;
        hero_glyph(g, right - 56, top, f->hero_kind);
        right -= 56 + 20;
        put_r(g, &h1, right, top, t->ink);
        put_r(g, &sub, right, top + h1.h + 4, t->muted);
        double py = top + text_h + (g->sm ? 10 : 12);
        for (int i = 0; i < f->nprobs && bad; i++)
            py += problem_draw(g, &f->probs[i], right - pw, pw, py, 1) + (g->sm ? 8 : 10);
        if (meta_w > 0) {                                       /* .meta: column, aligned to the left */
            double mx = x + bw + ppx;
            put_l(g, &ml, mx, top + 4, t->muted);
            put_l(g, &mv, mx, top + 4 + ml.h + 4, t->ink);
        }
    }
    text_free(&ml); text_free(&mv); text_free(&h1); text_free(&sub);
    return h;
}

/* #1363: a tile is never cut through a row. Its rows are measured first; when
 * they do not all fit the height it was given (1280x768 with problem cards in
 * the hero), the rows that fit are drawn whole and the last line says how many
 * are not shown and names them with their state -- red when one of them is a
 * failure, orange when a warning (principle 5: "not shown" is not "fine").
 * TILE_PAD_B keeps the last row off the border. */
#define TILE_PAD_B 8

static double tile_head(G *g, const DcuiTile *tl, Text *h2) {
    *h2 = rtl(g, 13, 600, tl->title, 0);
    return fmax(h2->h, tl->pill[0] ? 24 : 0);
}

static double tile_need(G *g, const DcuiTile *tl, double w) {
    Text h2; double y = 1 + 14 + tile_head(g, tl, &h2) + 4;
    text_free(&h2);
    const double px = 18;
    RowBox b = { 0, w - 2 - 2 * px, g->sm ? 118 : 150, px };
    for (int i = 0; i < tl->nrows; i++) y += row_draw(g, &tl->rows[i], b, 0, i == 0, 0);
    return y + TILE_PAD_B + 1;
}

static void tile(G *g, const DcuiTile *tl, Rect r) {
    const Theme *t = g->t;
    draw_fill_rrect(g->cr, r, 8, t->hair);
    draw_fill_rrect(g->cr, (Rect){ r.x + 1, r.y + 1, r.w - 2, r.h - 2 }, 7, t->surface);
    cairo_save(g->cr);
    draw_rrect_path(g->cr, (Rect){ r.x + 1, r.y + 1, r.w - 2, r.h - 2 }, 7); cairo_clip(g->cr);
    const double px = 18;
    double x = r.x + 1 + px, w = r.w - 2 - 2 * px, y = r.y + 1 + 14;
    Text h2;
    double hh = tile_head(g, tl, &h2);
    put_r(g, &h2, x + w, y + (hh - h2.h) / 2, t->muted);
    if (tl->pill[0]) pill(g, x, y + hh / 2, tl->pill_kind, tl->pill);
    text_free(&h2);
    y += hh + 4;
    RowBox b = { x, w, g->sm ? 118 : 150, px };
    const double bottom = r.y + r.h - 1 - TILE_PAD_B;
    double rh[DCUI_MAX_ROWS], sum = 0;
    for (int i = 0; i < tl->nrows; i++) sum += rh[i] = row_draw(g, &tl->rows[i], b, 0, i == 0, 0);
    int shown = tl->nrows;
    Text more = { 0 };
    if (y + sum > bottom) {                                     /* not all fit: room for the line */
        char names[512] = "";
        for (int pass = 0; pass < 2 && shown > 0; pass++) {     /* the line can grow when it wraps */
            double used = 0;
            shown = 0;
            while (shown < tl->nrows && y + used + rh[shown] + (more.layout ? more.h + 8 : 24) <= bottom)
                used += rh[shown++];
            size_t n = 0; names[0] = 0;
            for (int i = shown; i < tl->nrows && n < sizeof names - 1; i++)
                n += snprintf(names + n, sizeof names - n, "%s%s%s%s", i > shown ? " · " : "",
                              tl->rows[i].label, tl->rows[i].pill[0] ? " " : "", tl->rows[i].pill);
            char line[640];
            snprintf(line, sizeof line, "לא נכנסו למסך (%d): %s", tl->nrows - shown, names);
            text_free(&more);
            more = rtl(g, 12.5, 600, line, w);
        }
    }
    for (int i = 0; i < shown; i++) y += row_draw(g, &tl->rows[i], b, y, i == 0, 1);
    if (more.layout) {
        int bad = 0, warn = 0;
        for (int i = shown; i < tl->nrows; i++) {
            bad |= !strcmp(tl->rows[i].tone, "bad"); warn |= !strcmp(tl->rows[i].tone, "warn");
        }
        hline(g, x, y, w, t->hair);
        put_r(g, &more, x + w, y + 6, bad ? t->danger : warn ? t->warn : t->muted);
        text_free(&more);
    }
    cairo_restore(g->cr);
}

static void main_body(G *g, const DcuiFrame *f, Rect page) {
    const double gap = g->sm ? 12 : 16;
    double y = page.y;
    if (f->hero_kind[0]) y += hero(g, f, page.x, y, page.w, 1) + gap;
    double th = page.y + page.h - y, tw = (page.w - gap) / 2, avail = th - gap;
    double rh[2] = { avail / 2, avail / 2 };
    if (f->ntiles > 2) {                                        /* #1363: a row of tiles that needs */
        double need[2] = { 0, 0 };                              /* more takes what the other spares */
        for (int i = 0; i < f->ntiles && i < 4; i++) need[i / 2] = fmax(need[i / 2], tile_need(g, &f->tiles[i], tw));
        for (int k = 0; k < 2; k++)
            if (need[k] > rh[k] && need[!k] < rh[!k]) { rh[k] = fmin(need[k], avail - need[!k]); rh[!k] = avail - rh[k]; }
    }
    for (int i = 0; i < f->ntiles && i < 4; i++) {              /* RTL grid: 0 top-right, 1 top-left ... */
        double tx = (i % 2 == 0) ? page.x + page.w - tw : page.x;
        double ty = y + (i / 2) * (rh[0] + gap);
        tile(g, &f->tiles[i], (Rect){ tx, ty, tw, f->ntiles > 2 ? rh[i / 2] : th });
    }
}

/* ---- settings (.ttl, .cz) ---------------------------------------------------- */

static void settings_body(G *g, const DcuiFrame *f, Rect page) {
    const Theme *t = g->t;
    double right = page.x + page.w, y = page.y;
    Text h1 = rtl(g, 24, 600, f->ttl, 0), p = rtl(g, 13, 400, f->ttl_sub, 0);
    put_r(g, &h1, right, y, t->ink); y += h1.h + 2;
    put_r(g, &p, right, y, t->muted); y += p.h + (g->sm ? 12 : 16);
    text_free(&h1); text_free(&p);
    double lw = g->sm ? 380 : 420, gap = 20, bottom = page.y + page.h;
    double ly = y;
    int sel = 0;                                                /* #1959: the item the keys move from */
    for (int i = 0; i < f->nitems; i++) if (f->items[i].selected) sel = i;
    for (int i = 0; i < f->nitems; i++) {                       /* .it */
        const DcuiItem *it = &f->items[i];
        Text ti = rtl(g, 15, 600, it->title, 0), su = rtl(g, 12.5, 400, it->sub, 0);
        double h = f->nitems == 8 ? 56 : 12 + fmax(38, ti.h + su.h) + 12 + 2; /* #2198: eight USB items fit; server unchanged */
        Rect r = { right - lw, ly, lw, h };
        draw_fill_rrect(g->cr, r, 8, it->selected ? t->indigo : t->hair);
        double bw = it->selected ? 2 : 1;
        draw_fill_rrect(g->cr, (Rect){ r.x + bw, r.y + bw, r.w - 2 * bw, r.h - 2 * bw }, 8 - bw,
                        it->selected ? t->indigo_soft : t->surface);
        double ir = right - 17, cy = ly + h / 2;
        Rect ic = { ir - 38, cy - 19, 38, 38 };
        draw_fill_rrect(g->cr, ic, 8, it->selected ? t->indigo : t->indigo_soft);
        icon_menu(g, (Rect){ ic.x + 9, ic.y + 9, 20, 20 }, it->icon, it->selected ? WHITE : t->action_fg);
        double tx = ic.x - 14, ty = cy - (ti.h + su.h) / 2;
        put_r(g, &ti, tx, ty, t->ink); put_r(g, &su, tx, ty + ti.h, t->muted);
        Text ch = rtl(g, 18, 400, "‹", 0);
        put_l(g, &ch, r.x + 17, cy - ch.h / 2.0, it->selected ? t->action_fg : t->muted);
        text_free(&ti); text_free(&su); text_free(&ch);
        if (!f->dialog) {                                       /* #1959: behind a dialog, nothing to press */
            char what[160], keys[96];
            snprintf(what, sizeof what, "item:%s", it->title);
            keys_rep(keys, sizeof keys, i > sel ? "KEY DOWN" : "KEY UP", abs(i - sel), "KEY ENTER");
            hit_add(r, what, keys);
        }
        ly += h + 8;
    }
    if (f->na_m[0]) {                                           /* .na: dashed */
        ly += 6;
        Text na = rtlm_ink(g, 12.5, 400, f->na_m, lw - 28);
        Rect r = { right - lw, ly, lw, na.h + 20 };
        cairo_save(g->cr);
        double dash[] = { 4, 3 };
        cairo_set_dash(g->cr, dash, 2, 0); cairo_set_line_width(g->cr, 1);
        draw_rrect_path(g->cr, (Rect){ r.x + .5, r.y + .5, r.w - 1, r.h - 1 }, 8);
        draw_set(g->cr, t->hair); cairo_stroke(g->cr);
        cairo_restore(g->cr);
        put_r(g, &na, right - 14, ly + 10, t->muted);
        text_free(&na);
    }
    /* .det: the explanation pane */
    Rect d = { page.x, y, page.w - lw - gap, bottom - y };
    draw_fill_rrect(g->cr, d, 8, t->hair);
    draw_fill_rrect(g->cr, (Rect){ d.x + 1, d.y + 1, d.w - 2, d.h - 2 }, 7, t->surface);
    double dx = d.x + 1 + 24, dw = d.w - 2 - 48, dr = dx + dw, dy = d.y + 1 + 22;
    Text h2 = rtl(g, 20, 600, f->pane_title, dw), desc = rtlm_ink(g, 14, 400, f->pane_desc_m, dw);
    put_r(g, &h2, dr, dy, t->ink); dy += h2.h + 14;
    put_r(g, &desc, dr, dy, t->ink); dy += desc.h + 14;
    text_free(&h2); text_free(&desc);
    if (f->nprows) {                                            /* .cur */
        hline(g, dx, dy, dw, t->hair); dy += 12;
        RowBox b = { dx, dw, g->sm ? 118 : 150, 24 };
        for (int i = 0; i < f->nprows; i++) dy += row_draw(g, &f->prows[i], b, dy, i == 0, 1);
    }
    if (f->npnotes) {                                           /* .note at the bottom (.grow above) */
        Text n[DCUI_MAX_NOTES];
        double nh = 20;
        for (int i = 0; i < f->npnotes; i++) { n[i] = rtlm_ink(g, 12.5, 400, f->pnotes[i], dw - 24); nh += n[i].h + (i ? 3 : 0); }
        double ny = d.y + d.h - 1 - 22 - nh;
        if (ny < dy + 14) ny = dy + 14;
        draw_fill_rrect(g->cr, (Rect){ dx, ny, dw, nh }, 6, t->porcelain);
        ny += 10;
        for (int i = 0; i < f->npnotes; i++) { put_r(g, &n[i], dr - 12, ny, t->muted); ny += n[i].h + 3; text_free(&n[i]); }
    }
}

/* ---- the dialog (.scrim .dlg) ------------------------------------------------- */

typedef struct { G *g; const DcuiFrame *f; double x, w; int danger; } Dlg;

static double btn_row(Dlg *d, int from, int to, double y, int draw) {
    G *g = d->g; const Theme *t = g->t;
    double right = d->x + d->w;
    for (int i = from; i < to; i++) {
        const DcuiEl *e = &d->f->el[i];
        const int pri = !strcmp(e->d, "pri"), dng = !strcmp(e->d, "danger");
        Text l = rtl(g, 14, 500, e->a, 0);
        double kw = e->c[0] ? kc_w(g, e->c, 11) : 0;
        double w = 20 + l.w + (kw > 0 ? 10 + kw : 0) + 20;
        Rect r = { right - w, y, w, 40 };
        if (draw) {                                             /* #1959: the cap's key, or Tab to it and Enter */
            char what[160], keys[96] = "";
            int foc = -1;
            for (int k = from; k < to; k++) if (d->f->el[k].f1) foc = k;
            if (cap_token(e->c)[0]) snprintf(keys, sizeof keys, "%s\n", cap_token(e->c));
            else if (foc >= 0) keys_rep(keys, sizeof keys, "KEY TAB", (i - foc + (to - from)) % (to - from), "KEY ENTER");
            snprintf(what, sizeof what, "btn:%s", e->a);
            hit_add(r, what, keys);
        }
        if (draw) {
            Rgb bg = pri ? t->indigo : dng ? t->danger : t->surface, line = pri ? t->indigo : dng ? t->danger : t->hair;
            Rgb fg = pri ? t->on_ink : dng ? on_fill(g) : t->action_fg;
            if (e->f1) {                                        /* .btn.foc: 2px outline, 2px offset */
                draw_border_rrect(g->cr, (Rect){ r.x - 4, r.y - 4, r.w + 8, r.h + 8 }, 7, dng ? t->danger : t->indigo, 2);
            }
            draw_fill_rrect(g->cr, r, 4, line);
            draw_fill_rrect(g->cr, (Rect){ r.x + 1, r.y + 1, r.w - 2, r.h - 2 }, 3, bg);
            put_r(g, &l, r.x + r.w - 20, y + (40 - l.h) / 2, fg);
            if (kw > 0) {
                KcStyle ks = kc_plain(g);
                ks.h = 18; ks.px = 11;
                if (pri || dng) { ks.bg = fg; ks.line = fg; ks.fg = fg; }
                if (pri || dng) {                               /* rgb(255 255 255 / 18%) on the fill, 40% border */
                    double cw = kc_w(g, e->c, 11), cx = r.x + 20, cy = y + 20;
                    cairo_save(g->cr);
                    draw_set_a(g->cr, WHITE, .40); draw_rrect_path(g->cr, (Rect){ cx, cy - 9, cw, 18 }, 4); cairo_fill(g->cr);
                    draw_fill_rrect(g->cr, (Rect){ cx + 1, cy - 8, cw - 2, 15 }, 3, bg);
                    draw_set_a(g->cr, WHITE, .18); draw_rrect_path(g->cr, (Rect){ cx + 1, cy - 8, cw - 2, 15 }, 3); cairo_fill(g->cr);
                    cairo_restore(g->cr);
                    Text c = mono(g, 11, 600, e->c);
                    text_draw(g->cr, &c, cx + (cw - c.w) / 2, cy - 9 + (17 - c.h) / 2, fg);
                    text_free(&c);
                } else kc(g, r.x + 20 + kw, y + 20, e->c, ks);
            }
        }
        right -= w + 10;
        text_free(&l);
    }
    return 40;
}

static double field_el(Dlg *d, const DcuiEl *e, double fx, double fw, double y, int draw) {
    G *g = d->g; const Theme *t = g->t;
    Text lab = rtl(g, 12.5, 600, e->b, fw);
    double h = lab.h + 6 + 40;
    if (draw) {
        put_r(g, &lab, fx + fw, y, t->ink);
        Rect r = { fx, y + lab.h + 6, fw, 40 };
        const int ro = !strcmp(e->c, "ro") || !strcmp(e->c, "unused"), pw = !strncmp(e->c, "password", 8);
        if (e->f1) draw_border_rrect(g->cr, (Rect){ r.x - 3, r.y - 3, r.w + 6, r.h + 6 }, 5, t->indigo, 2);
        draw_fill_rrect(g->cr, r, 3, e->f1 ? (ro ? t->porcelain : t->field) : t->hair);
        draw_fill_rrect(g->cr, (Rect){ r.x + 1, r.y + 1, r.w - 2, r.h - 2 }, 2, ro ? t->porcelain : t->field);
        double x = r.x + 12, cy = r.y + 20;
        if (pw) {                                               /* the dots, never the password */
            int n = atoi(e->a); if (n > 64) n = 64; if (n < 0) n = 0;
            char dots[64 * 3 + 1] = ""; for (int i = 0; i < n; i++) strcat(dots, "\xe2\x80\xa2");
            char mk_[512]; snprintf(mk_, sizeof mk_, "<span letter_spacing=\"3072\">%s</span>", dots);
            Text dt = mk(g, FONT_MONO, 18, 500, mk_, 0, 1, 1);
            if (n) { put_l(g, &dt, x, cy - dt.h / 2.0, t->ink); x += dt.w + 8; }
            text_free(&dt);
        } else if (e->a[0]) {
            Text v = mk(g, FONT_MONO, 15, 500, e->a, 0, 1, 0);
            put_l(g, &v, x, cy - v.h / 2.0, ro ? t->muted : t->ink); x += v.w + 2;
            text_free(&v);
        }
        const int pick = !strcmp(e->c, "pick");                 /* #1962: a value chosen with Left/Right, not typed */
        if (e->f1 && !pick) { cairo_rectangle(g->cr, x, cy - 10, 2, 20); draw_set(g->cr, t->indigo); cairo_fill(g->cr); }
        if (pick) {
            Text ch = rtl(g, 18, 400, "‹ ›", 0);
            put_r(g, &ch, r.x + r.w - 12, cy - ch.h / 2.0, e->f1 ? t->action_fg : t->muted);
            text_free(&ch);
            int me = -1, foc = -1, k = 0;                       /* Up/Down to it, then Left: its next value */
            for (int i = 0; i < d->f->nel; i++) {
                const DcuiEl *o = &d->f->el[i];
                if (o->type != DEL_FIELD || strcmp(o->c, "pick")) continue;
                if (o == e) me = k;
                if (o->f1) foc = k;
                k++;
            }
            if (me >= 0 && foc >= 0) {
                char what[DCUI_FIELD + 16], keys[96];
                snprintf(what, sizeof what, "field:%s", e->b);
                keys_rep(keys, sizeof keys, me > foc ? "KEY DOWN" : "KEY UP", abs(me - foc), "KEY LEFT");
                hit_add(r, what, keys);
            }
        }
        if (e->f2) {                                            /* .inp .en: the chip at the field's end */
            Text en = mono(g, 11, 700, "EN");
            Rect c = { r.x + r.w - 12 - en.w - 14, cy - 11, en.w + 14, 22 };
            draw_fill_rrect(g->cr, c, 4, t->indigo);
            draw_fill_rrect(g->cr, (Rect){ c.x + 1, c.y + 1, c.w - 2, c.h - 2 }, 3, t->indigo_soft);
            text_draw(g->cr, &en, c.x + 7, cy - en.h / 2.0, t->action_fg);
            text_free(&en);
        }
    }
    text_free(&lab);
    return h;
}

static double opts_row(Dlg *d, int from, int to, double y, int draw) {
    G *g = d->g; const Theme *t = g->t;
    int n = to - from; if (n < 1) return 0;
    double gap = 12, w = (d->w - gap * (n - 1)) / n, h = 0;
    Text ti[4], su[4];
    for (int i = 0; i < n && i < 4; i++) {
        const DcuiEl *e = &d->f->el[from + i];
        ti[i] = rtl(g, 15, 700, e->a, w - 32 - 30);
        su[i] = rtl(g, 12.5, 400, e->b, w - 32 - 30);
        h = fmax(h, 14 + ti[i].h + 2 + su[i].h + 14);
    }
    for (int i = 0; i < n && i < 4; i++) {
        const DcuiEl *e = &d->f->el[from + i];
        double x = d->x + d->w - (i + 1) * w - i * gap;
        if (draw) {
            Rgb sel = d->danger ? t->danger : t->indigo, soft = d->danger ? t->danger_soft : t->indigo_soft;
            Rect r = { x, y, w, h };
            int on = -1;                                        /* #1959: Left/Right to it, as the keys do */
            for (int k = from; k < to; k++) if (d->f->el[k].f1) on = k - from;
            if (on >= 0 && on != i) {
                char what[160], keys[96];
                snprintf(what, sizeof what, "opt:%s", e->a);
                keys_rep(keys, sizeof keys, i > on ? "KEY LEFT" : "KEY RIGHT", abs(i - on), NULL);
                hit_add(r, what, keys);
            }
            if (e->f1 && e->f2) draw_border_rrect(g->cr, (Rect){ r.x - 4, r.y - 4, r.w + 8, r.h + 8 }, 11, t->indigo, 2);
            draw_fill_rrect(g->cr, r, 8, e->f1 ? sel : t->hair);
            double bw = e->f1 ? 2 : 1;
            draw_fill_rrect(g->cr, (Rect){ r.x + bw, r.y + bw, r.w - 2 * bw, r.h - 2 * bw }, 8 - bw, e->f1 ? soft : t->surface);
            double right = x + w - 16, ty = y + 14;
            cairo_arc(g->cr, right - 9, ty + 11, 8, 0, 2 * M_PI);          /* .rd: 18px, 2px border */
            cairo_set_line_width(g->cr, 2); draw_set(g->cr, e->f1 ? sel : t->hair); cairo_stroke(g->cr);
            if (e->f1) { cairo_arc(g->cr, right - 9, ty + 11, 5, 0, 2 * M_PI); draw_set(g->cr, sel); cairo_fill(g->cr); }
            put_r(g, &ti[i], right - 18 - 12, ty, t->ink);
            put_r(g, &su[i], right - 18 - 12, ty + ti[i].h + 2, t->muted);
        }
        text_free(&ti[i]); text_free(&su[i]);
    }
    return h;
}

static double radio_el(Dlg *d, const DcuiEl *e, double y, int draw) {
    G *g = d->g; const Theme *t = g->t;
    Text ti = mk(g, FONT_MONO, 13.5, 600, e->a, 0, 1, 0), su = rtl(g, 12, 400, e->b, d->w - 60);
    double h = 8 + ti.h + 1 + su.h + 8;
    if (draw) {
        Rect r = { d->x, y, d->w, h };
        if (e->f1 && e->f2) draw_border_rrect(g->cr, (Rect){ r.x - 4, r.y - 4, r.w + 8, r.h + 8 }, 9, t->indigo, 2);
        draw_fill_rrect(g->cr, r, 6, e->f1 ? t->indigo : t->hair);
        draw_fill_rrect(g->cr, (Rect){ r.x + 1, r.y + 1, r.w - 2, r.h - 2 }, 5, e->f1 ? t->indigo_soft : t->surface);
        double right = r.x + r.w - 12;
        cairo_arc(g->cr, right - 8, y + h / 2, 7, 0, 2 * M_PI);
        cairo_set_line_width(g->cr, 2); draw_set(g->cr, e->f1 ? t->indigo : t->hair); cairo_stroke(g->cr);
        if (e->f1) { cairo_arc(g->cr, right - 8, y + h / 2, 4, 0, 2 * M_PI); draw_set(g->cr, t->indigo); cairo_fill(g->cr); }
        put_r(g, &ti, right - 16 - 10, y + 8, t->ink);
        put_r(g, &su, right - 16 - 10, y + 8 + ti.h + 1, t->muted);
    }
    text_free(&ti); text_free(&su);
    return h;
}

/* 16/10 (mockup a2): the pairing code, large and centred, with the time left
 * at the box's left end. The code is [A-Z0-9-] only (the backend's alphabet). */
static double code_el(Dlg *d, const DcuiEl *e, double y, int draw) {
    G *g = d->g; const Theme *t = g->t;
    char m[DCUI_FIELD + 64];                  /* e->a whole: no truncation */
    snprintf(m, sizeof m, "<span letter_spacing=\"6144\">%s</span>", e->a);
    Text code = mk(g, FONT_MONO, 44, 700, m, 0, 1, 1);
    Text lab = rtl(g, 12.5, 400, e->b, 0), tm = mono(g, 22, 700, e->c);
    double side = fmax(lab.w, tm.w) + 32, h = fmax(code.h, lab.h + 4 + tm.h) + 32;
    if (draw) {
        Rect r = { d->x, y, d->w, h };
        draw_fill_rrect(g->cr, r, 8, t->indigo);
        draw_fill_rrect(g->cr, (Rect){ r.x + 1, r.y + 1, r.w - 2, r.h - 2 }, 7, t->indigo_soft);
        double cx = r.x + side + (r.w - side - code.w) / 2;
        put_l(g, &code, cx, y + (h - code.h) / 2, t->ink);
        double ty = y + (h - lab.h - 4 - tm.h) / 2;
        put_l(g, &lab, r.x + 16, ty, t->muted);
        put_l(g, &tm, r.x + 16, ty + lab.h + 4, !strcmp(e->d, "warn") ? t->warn : t->ink);
    }
    text_free(&code); text_free(&lab); text_free(&tm);
    return h;
}

static double cons_el(Dlg *d, const DcuiEl *e, double y, int draw) {
    G *g = d->g; const Theme *t = g->t;
    const int hot = e->type == DEL_ALERT, warn = hot && !strcmp(e->c, "warn");
    double pad = hot ? 12 : 0, w = d->w - 2 * pad - 20 - 10;
    Text tx = rtlm(g, 13.5, 400, e->a, w);
    double h = (hot ? 10 : 0) + fmax(22, tx.h) + (hot ? 10 : 0);
    if (draw) {
        if (hot) {                                      /* warn: the yellow of a pill */
            draw_fill_rrect(g->cr, (Rect){ d->x, y, d->w, h }, 6, warn ? t->warn : t->danger);
            draw_fill_rrect(g->cr, (Rect){ d->x + 1, y + 1, d->w - 2, h - 2 }, 5,
                            warn ? t->warning_soft : t->danger_soft);
        }
        double right = d->x + d->w - pad, top = y + (hot ? 10 : 0);
        cairo_arc(g->cr, right - 10, top + 11, 10, 0, 2 * M_PI);
        draw_set(g->cr, hot ? (warn ? t->warn : t->danger) : t->hover); cairo_fill(g->cr);
        Text b = rtl(g, 12, 700, hot ? "!" : "\xe2\x80\xa2", 0);
        text_draw(g->cr, &b, right - 10 - b.w / 2.0, top + 11 - b.h / 2.0, hot ? on_fill(g) : t->muted);
        text_free(&b);
        put_r(g, &tx, right - 20 - 10, top, t->ink);
    }
    text_free(&tx);
    return h;
}

static double dialog_body(Dlg *d, double y0, int draw) {
    G *g = d->g; const Theme *t = g->t; const DcuiFrame *f = d->f;
    double y = y0, right = d->x + d->w;
    Text h2 = rtl(g, 22, 600, f->dlg_title, d->w), lead = rtl(g, 14, 400, f->dlg_lead, d->w);
    if (draw) { put_r(g, &h2, right, y, t->ink); put_r(g, &lead, right, y + h2.h + 6, t->muted); }
    y += h2.h + 6 + lead.h;
    text_free(&h2); text_free(&lead);
    int prev = 0;
    for (int i = 0; i < f->nel; ) {
        const DcuiEl *e = &f->el[i];
        int j = i + 1;
        switch (e->type) {
        case DEL_FIELD: {
            /* a run of three or more plain text fields (the network form's
             * address, prefix, gateway, DNS) goes two to a row, so the form
             * fits a 768px screen; the first of a pair is the right one */
            int run = 0;
            while (i + run < f->nel && f->el[i + run].type == DEL_FIELD
                   && (!strcmp(f->el[i + run].c, "text") || !strcmp(f->el[i + run].c, "unused")
                       || !strcmp(f->el[i + run].c, "pick"))) /* #1962: the drawers, two to a row */ run++;
            if (run >= 3) {
                double hw = (d->w - 16) / 2;
                for (int m = 0; m < run; m += 2) {
                    y += m ? 12 : 18;
                    double h1 = field_el(d, &f->el[i + m], d->x + d->w - hw, hw, y, draw), h2 = 0;
                    if (m + 1 < run) h2 = field_el(d, &f->el[i + m + 1], d->x, hw, y, draw);
                    y += fmax(h1, h2);
                }
                j = i + run;
            } else {
                y += 18; y += field_el(d, e, d->x, d->w, y, draw);
            }
            break;
        }
        case DEL_HINT: {
            y += 8;
            double cw = kc_w(g, e->c, 11.5);
            Text r = rtl(g, 12.5, 400, e->b, 0);
            Text tx = rtlm_ink(g, 12.5, 400, e->a, d->w - cw - 8 - r.w - 16);
            double h = fmax(20, tx.h);
            if (draw) {
                kc(g, right, y + h / 2, e->c, kc_plain(g));
                put_r(g, &tx, right - cw - 8, y + (h - tx.h) / 2, t->muted);
                put_l(g, &r, d->x, y + (h - r.h) / 2, t->muted);
            }
            y += h; text_free(&r); text_free(&tx);
            break;
        }
        case DEL_NOTE: {
            while (j < f->nel && f->el[j].type == DEL_NOTE) j++;
            y += 16;
            double h = 20; Text n[8]; int k = 0;
            for (int m = i; m < j && k < 8; m++, k++) { n[k] = rtlm_ink(g, 12.5, 400, f->el[m].a, d->w - 24); h += n[k].h + (k ? 3 : 0); }
            if (draw) {
                draw_fill_rrect(g->cr, (Rect){ d->x, y, d->w, h }, 6, t->porcelain);
                double ny = y + 10;
                for (int m = 0; m < k; m++) { put_r(g, &n[m], right - 12, ny, t->muted); ny += n[m].h + 3; }
            }
            for (int m = 0; m < k; m++) text_free(&n[m]);
            y += h;
            break;
        }
        case DEL_OPT:
            while (j < f->nel && f->el[j].type == DEL_OPT) j++;
            y += 18; y += opts_row(d, i, j, y, draw);
            break;
        case DEL_RADIO: y += prev == DEL_RADIO ? 6 : 14; y += radio_el(d, e, y, draw); break;
        case DEL_ALERT: case DEL_BULLET:
            y += (prev == DEL_ALERT || prev == DEL_BULLET) ? 8 : 16;
            y += cons_el(d, e, y, draw);
            break;
        case DEL_KV: {
            y += prev == DEL_KV && !e->b[0] ? 2 : 10;
            double lw = 170;
            Text l = rtl(g, 12.5, 400, e->b, lw);
            Text v = e->f1 ? mk(g, FONT_MONO, 13, 500, e->a, d->w - lw - 12, 1, 0) : rtl(g, 13.5, 400, e->a, d->w - lw - 12);
            if (draw) {
                put_r(g, &l, right, y, t->muted);
                if (e->f1) put_l(g, &v, d->x, y, t->ink); else put_r(g, &v, right - lw - 12, y, t->ink);
            }
            y += fmax(l.h, v.h); text_free(&l); text_free(&v);
            break;
        }
        case DEL_ERROR: {
            y += 10;
            Text er = rtl(g, 13, 600, e->a, d->w);
            if (draw) put_r(g, &er, right, y, t->danger_fg);
            y += er.h; text_free(&er);
            break;
        }
        case DEL_P: {
            y += 12;
            Text p = rtlm_ink(g, 14, 400, e->a, d->w);
            if (draw) put_r(g, &p, right, y, t->ink);
            y += p.h; text_free(&p);
            break;
        }
        case DEL_BTN:
            while (j < f->nel && f->el[j].type == DEL_BTN) j++;
            y += 22; y += btn_row(d, i, j, y, draw);
            break;
        case DEL_CODE: y += 18; y += code_el(d, e, y, draw); break;
        }
        prev = e->type;
        i = j;
    }
    return y - y0;
}

/* ---- #2001: the USB stick's connections window -------------------------------------
 * The approved mockup (Nadav 22/10): docs/design/console-redesign/mockups/
 * slot-map-2001.html ?surface=usb, its CSS cited by selector. A frame "dlg map"
 * (usbmapwin.sh): d.alert (.note -- "info" or the red one), a d.conn per row
 * (.map tr), d.addlink (.addrow) or d.add (.addform), d.foot and the d.btn's
 * (.w-foot). The mockup's tokens that theme.c has no name for are here, light/dark. */
typedef struct { G *g; const DcuiFrame *f; double x, w, R; const char *chip; } Mw;
static Rgb mw_faint(const G *g) { return g->dark ? (Rgb){ 0x7c / 255.0, 0x88 / 255.0, 0x91 / 255.0 } : (Rgb){ 0x8A / 255.0, 0x8F / 255.0, 0x94 / 255.0 }; }   /* --clr-faint */
static Rgb mw_hover(const G *g) { return g->dark ? (Rgb){ 0x20 / 255.0, 0x2a / 255.0, 0x31 / 255.0 } : (Rgb){ 0xF3 / 255.0, 0xF9 / 255.0, 0xFC / 255.0 }; }  /* --clr-row-hover */
static Rgb mw_swoff(const G *g) { return g->dark ? (Rgb){ 0x3b / 255.0, 0x48 / 255.0, 0x52 / 255.0 } : (Rgb){ 0xC9 / 255.0, 0xCE / 255.0, 0xD3 / 255.0 }; }  /* --switch-off */
static void mw_hex(Rgb c, char *out) { snprintf(out, 8, "#%02X%02X%02X", (int)lround(c.r * 255), (int)lround(c.g * 255), (int)lround(c.b * 255)); }

/* <b>..</b> of the backend's note, in the note's ink (.note b / .note.err b) */
static Text mw_note_text(G *g, const char *s, Rgb bold, double w) {
    char col[8], span[96], buf[2048]; size_t o = 0;
    mw_hex(bold, col); snprintf(span, sizeof span, "<span weight=\"600\" foreground=\"%s\">", col);
    for (const char *p = s; *p && o + 1 < sizeof buf; ) {
        const char *rep = !strncmp(p, "<b>", 3) ? span : !strncmp(p, "</b>", 4) ? "</span>" : NULL;
        if (rep) { size_t l = strlen(rep); if (o + l >= sizeof buf) break; memcpy(buf + o, rep, l); o += l; p += p[1] == 'b' ? 3 : 4; }
        else buf[o++] = *p++;
    }
    buf[o] = 0;
    return mk(g, FONT_SANS, 13, 400, buf, w, 0, 1);
}

/* .inp: 32px, the value or the placeholder (faint), the caret where the next letter goes,
 * and on the stick the typing language's chip (.inp .lang) -- a click on it is Alt+Shift */
static void mw_input(Mw *m, Rect r, const char *val, const char *ph, int focus, int mono_v, int chip) {
    G *g = m->g; const Theme *t = g->t; const double cy = r.y + r.h / 2;
    if (focus) { cairo_save(g->cr); draw_set_a(g->cr, t->indigo, .22); draw_rrect_path(g->cr, (Rect){ r.x - 3, r.y - 3, r.w + 6, r.h + 6 }, t->radius_sm + 3); cairo_fill(g->cr); cairo_restore(g->cr); }
    draw_fill_rrect(g->cr, r, t->radius_sm, focus ? t->indigo : t->hair);
    draw_fill_rrect(g->cr, (Rect){ r.x + 1, r.y + 1, r.w - 2, r.h - 2 }, t->radius_sm - 1, t->field);
    double right = r.x + r.w - 10, left = r.x + 10;
    if (chip) {
        Text c = mono(g, 10, 700, m->chip);
        Rect cr = { left, cy - (c.h + 4) / 2, c.w + 10, c.h + 4 };
        draw_fill_rrect(g->cr, cr, 4, t->hair);
        draw_fill_rrect(g->cr, (Rect){ cr.x + 1, cr.y + 1, cr.w - 2, cr.h - 2 }, 3, t->porcelain);
        text_draw(g->cr, &c, cr.x + 5, cr.y + 2, t->muted);
        hit_add(cr, "lang", "KEY LAYOUT\n");
        left += cr.w + 8; text_free(&c);
    }
    Text v = mono_v ? mono(g, 14, 400, val[0] ? val : ph) : rtl(g, 14, 400, val[0] ? val : ph, 0);
    if (mono_v) put_l(g, &v, left, cy - v.h / 2.0, val[0] ? t->ink : mw_faint(g));
    else put_r(g, &v, right, cy - v.h / 2.0, val[0] ? t->ink : mw_faint(g));
    if (focus) {                                                /* left of a Hebrew name, right of an English one */
        double cx = mono_v ? left + (val[0] ? v.w + 2 : 0) : right;
        if (!mono_v && val[0]) {
            PangoRectangle lg, strong, weak; int ltr = 0;
            for (const unsigned char *p = (const unsigned char *)val; *p; p++) {
                if ((*p | 32) >= 'a' && (*p | 32) <= 'z') { ltr = 1; break; }
                if (*p >= 0x80) break;
            }
            pango_layout_get_pixel_extents(v.layout, NULL, &lg);
            pango_layout_get_cursor_pos(v.layout, (int)strlen(val), &strong, &weak);
            cx = right - (lg.x + lg.width) + (ltr ? weak.x : strong.x) / (double)PANGO_SCALE;
        }
        cairo_rectangle(g->cr, cx - 1, cy - 8, 2, 16); draw_set(g->cr, t->indigo); cairo_fill(g->cr);
    }
    text_free(&v);
}

/* .sw: 38x22, the knob at the start (right) when off; .dis 45%; .kf the keyboard's ring */
static void mw_switch(Mw *m, double x, double cy, int on, int dis, int kf) {
    G *g = m->g; const Theme *t = g->t;
    Rect r = { x, cy - 11, 38, 22 };
    if (kf) draw_border_rrect(g->cr, (Rect){ r.x - 4, r.y - 4, r.w + 8, r.h + 8 }, 15, t->indigo, 2);
    cairo_save(g->cr);
    if (dis) cairo_push_group(g->cr);
    draw_fill_rrect(g->cr, r, 11, on ? t->indigo : mw_swoff(g));
    double kx = on ? r.x + 2 + 9 : r.x + r.w - 2 - 9;
    cairo_arc(g->cr, kx, cy + .5, 9.5, 0, 2 * M_PI); draw_set_a(g->cr, (Rgb){ 0, 0, 0 }, .18); cairo_fill(g->cr);
    cairo_arc(g->cr, kx, cy, 9, 0, 2 * M_PI); draw_set(g->cr, g->dark ? t->ink : (Rgb){ 1, 1, 1 }); cairo_fill(g->cr);
    if (dis) { cairo_pop_group_to_source(g->cr); cairo_paint_with_alpha(g->cr, .45); }
    cairo_restore(g->cr);
}

/* .btn (32px, 13/600, the key cap after the word); kind pri/plain/ghost. Returns its width. */
static double mw_btn(Mw *m, double left, double y, const DcuiEl *e, int draw) {
    G *g = m->g; const Theme *t = g->t;
    const int pri = !strcmp(e->d, "pri"), ghost = !strcmp(e->d, "ghost");
    Text l = rtl(g, 13, 600, e->a, 0), c = mono(g, 10, 700, e->c);
    double w = 14 + l.w + (e->c[0] ? 8 + c.w + 8 : 0) + 14;
    if (draw) {
        Rect r = { left, y, w, 32 };
        Rgb fg = pri ? (Rgb){ 1, 1, 1 } : ghost ? t->action_fg : t->ink;
        if (!ghost) { draw_fill_rrect(g->cr, r, t->radius_sm, pri ? t->indigo : t->hair);
            draw_fill_rrect(g->cr, (Rect){ r.x + 1, r.y + 1, r.w - 2, r.h - 2 }, t->radius_sm - 1, pri ? t->indigo : t->surface); }
        put_r(g, &l, r.x + r.w - 14, y + (32 - l.h) / 2, fg);
        if (e->c[0]) {
            Rect k = { r.x + 14, y + 16 - (c.h + 2) / 2, c.w + 8, c.h + 2 };
            cairo_save(g->cr); draw_set_a(g->cr, fg, .8); draw_rrect_path(g->cr, k, 3); cairo_set_line_width(g->cr, 1); cairo_stroke(g->cr); cairo_restore(g->cr);
            text_draw(g->cr, &c, k.x + 4, k.y + 1, fg);
        }
        char what[160]; snprintf(what, sizeof what, "btn:%.150s", e->a);
        if (cap_token(e->c)[0]) { char keys[32]; snprintf(keys, sizeof keys, "%s\n", cap_token(e->c)); hit_add(r, what, keys); }
    }
    text_free(&l); text_free(&c);
    return w;
}

/* the conn rows that can take the focus (a CD/DVD drive cannot) */
static int mw_sel(const DcuiEl *e) { return e->type == DEL_CONN && strcmp(e->k, "optical"); }

static double mw_row(Mw *m, const DcuiEl *e, double y, double rh, int draw) {
    G *g = m->g; const Theme *t = g->t; const DcuiFrame *f = m->f;
    if (!draw) return rh;
    const double R = m->R, L = m->x, cy = y + rh / 2;
    const int on = e->f2, opt = !strcmp(e->k, "optical"), manual = !strcmp(e->k, "manual");
    if (manual) draw_fill_rrect(g->cr, (Rect){ L, y, m->w, rh }, 0, t->indigo_soft);              /* .map tr.new */
    if (e->f1) {                                                                                  /* tr.focus */
        draw_fill_rrect(g->cr, (Rect){ L, y, m->w, rh }, 0, mw_hover(g));
        draw_fill_rrect(g->cr, (Rect){ R - 3, y, 3, rh }, 0, t->indigo);
    }
    draw_fill_rrect(g->cr, (Rect){ L, y + rh - 1, m->w, 1 }, 0, t->track);                         /* --clr-row-border */
    /* td.conn: the label (b), the PCI address under it */
    Text cn = rtl(g, 14, 600, e->c, 0), pci = mono(g, 11, 400, e->d);
    double ch = cn.h + (e->d[0] ? pci.h + 1 : 0), ty = cy - ch / 2;
    put_r(g, &cn, R - 22, ty, on ? t->ink : mw_faint(g));
    if (e->d[0]) put_r(g, &pci, R - 22, ty + cn.h + 1, t->muted);
    text_free(&cn); text_free(&pci);
    /* td.now: the model (muted) · the size (mono, 600) -- or "ריק" (faint) and the tag */
    double nr = R - 118 - 10;
    char col[8], colsz[8], mk_[900];
    gchar *esc = g_markup_escape_text(e->b, -1);
    mw_hex(t->muted, col); mw_hex(on ? t->ink : mw_faint(g), colsz);
    if (e->e[0]) snprintf(mk_, sizeof mk_, "<span foreground=\"%s\">%s</span><span foreground=\"%s\"> · </span>"
                          "<span font_family=\"" FONT_MONO_FAMILY "\" weight=\"600\" foreground=\"%s\">\xE2\x81\xA8%s\xE2\x81\xA9</span>",
                          col, esc, colsz, colsz, e->e);
    else { char cf[8]; mw_hex(mw_faint(g), cf); snprintf(mk_, sizeof mk_, "<span foreground=\"%s\">%s</span>", opt ? col : cf, esc); }
    g_free(esc);
    Text now = mk(g, FONT_SANS, 14, 400, mk_, 0, 0, 1);
    put_r(g, &now, nr, cy - now.h / 2.0, t->ink);
    if (e->g[0]) {                                                                                /* .tag */
        Text tg = rtl(g, 11, 400, e->g, 0);
        Rect tr = { nr - now.w - 6 - (tg.w + 14), cy - (tg.h + 2) / 2, tg.w + 14, tg.h + 2 };
        draw_fill_rrect(g->cr, tr, tr.h / 2, t->warning_soft);
        put_r(g, &tg, tr.x + tr.w - 7, tr.y + 1, t->warning_ink);
        text_free(&tg);
    }
    text_free(&now);
    /* td.name: the field, "לא בשימוש" or "לא דיסק" (.na) */
    Rect in = { L + 72 + 10, cy - 16, 216, 32 };
    if (opt || !on) {
        Text na = rtl(g, 13, 400, opt ? "לא דיסק — לא יעד ולא מקור" : "לא בשימוש", 0);
        put_r(g, &na, in.x + in.w, cy - na.h / 2.0, mw_faint(g)); text_free(&na);
    }
    /* the switch, the field's hits: Up/Down to the row, Tab to the column (#1959: the keys) */
    int me = -1, fr = -1, fc = 0, k = 0, tgt_on = on;
    for (int i = 0; i < f->nel; i++) {
        const DcuiEl *o = &f->el[i];
        if (!mw_sel(o)) continue;
        if (o == e) me = k;
        if (o->f1) { fr = k; fc = o->f1 - 1; }
        k++;
    }
    mw_switch(m, L + 22, cy, on, opt, e->f1 == 2);
    if (on && !opt) mw_input(m, in, e->a, e->h, e->f1 == 1, 0, e->f1 == 1);
    if (me >= 0 && fr >= 0) {
        int c2 = tgt_on ? fc : 1;                                   /* Up/Down onto a row that is off: its switch */
        for (int col = 0; col < 2; col++) {
            if (col == 0 && !on) continue;
            char what[96], keys[192];
            keys_rep(keys, sizeof keys, me > fr ? "KEY DOWN" : "KEY UP", abs(me - fr), NULL);
            size_t u = strlen(keys);
            if (col != c2 && u < sizeof keys) u += snprintf(keys + u, sizeof keys - u, "KEY TAB\n");
            if (col == 1 && u < sizeof keys) snprintf(keys + u, sizeof keys - u, "KEY LEFT\n");
            snprintf(what, sizeof what, "%s:%s", col ? "active" : "name", e->c);
            if (col == 0) hit_add(in, what, keys);
            else hit_add((Rect){ L + 18, cy - 15, 46, 30 }, what, keys);
        }
    }
    return rh;
}

/* .addform: [סוג .seg] [מספר 120] [שם 1fr] [הוסף][ביטול], the help under them */
static double mw_add(Mw *m, const DcuiEl *e, double y, int draw) {
    G *g = m->g; const Theme *t = g->t;
    const double x = m->x + 22, w = m->w - 44, R = x + w, pad = 12;
    Text lt = rtl(g, 12, 600, "סוג", 0), help = rtl(g, 12, 400, e->b, w - 2 * pad);
    double h = pad + lt.h + 4 + 32 + 10 + help.h + pad;
    if (!draw) { text_free(&lt); text_free(&help); return h; }
    draw_fill_rrect(g->cr, (Rect){ x, y, w, h }, t->radius, t->indigo);
    draw_fill_rrect(g->cr, (Rect){ x + 1, y + 1, w - 2, h - 2 }, t->radius - 1, t->indigo_soft);
    double fy = y + pad + lt.h + 4, cx = R - pad;
    /* the type: .seg, SATA at the right */
    Text sa = rtl(g, 13, 400, "SATA", 0), nv = rtl(g, 13, 400, "NVMe", 0);
    double sw = 2 + (sa.w + 24) + (nv.w + 24) + 2;
    Rect seg = { cx - sw, fy, sw, 32 };
    if (e->f1 == 0) draw_border_rrect(g->cr, (Rect){ seg.x - 3, seg.y - 3, seg.w + 6, seg.h + 6 }, 10, t->indigo, 2);
    draw_fill_rrect(g->cr, seg, 8, t->hair); draw_fill_rrect(g->cr, (Rect){ seg.x + 1, seg.y + 1, seg.w - 2, seg.h - 2 }, 7, t->porcelain);
    put_r(g, &lt, cx, y + pad, t->muted);
    const int nvme = !strcmp(e->c, "NVMe");
    for (int i = 0; i < 2; i++) {
        Text *o = i ? &nv : &sa; int sel = i == nvme;
        double ow = o->w + 24, ox = i ? seg.x + 2 : seg.x + seg.w - 2 - ow;
        Rect orr = { ox, seg.y + 3, ow, 26 };
        if (sel) draw_fill_rrect(g->cr, orr, 6, g->dark ? t->indigo_soft : t->surface);
        Text s2 = rtl(g, 13, sel ? 600 : 400, i ? "NVMe" : "SATA", 0);
        put_r(g, &s2, ox + ow - 12, orr.y + (26 - s2.h) / 2, sel ? t->ink : t->muted); text_free(&s2);
        char keys[64]; int n = (0 - e->f1 + 3) % 3; keys_rep(keys, sizeof keys, "KEY TAB", n, sel ? NULL : "KEY LEFT");
        char what[32]; snprintf(what, sizeof what, "add:%s", i ? "NVMe" : "SATA");
        hit_add(orr, what, keys);
    }
    text_free(&sa); text_free(&nv);
    cx = seg.x - 12;
    /* the number: 120px */
    Text ln = rtl(g, 12, 600, "מספר", 0); put_r(g, &ln, cx, y + pad, t->muted); text_free(&ln);
    Rect num = { cx - 120, fy, 120, 32 };
    mw_input(m, num, e->d, "", e->f1 == 1, 1, 0);
    { char keys[64]; keys_rep(keys, sizeof keys, "KEY TAB", (1 - e->f1 + 3) % 3, NULL); hit_add(num, "add:number", keys); }
    cx = num.x - 12;
    /* the buttons, at the left: הוסף (pri), ביטול */
    static DcuiEl go = { .type = DEL_BTN, .a = "הוסף", .d = "pri" }, no = { .type = DEL_BTN, .a = "ביטול", .d = "plain" };
    double bw2 = mw_btn(m, 0, 0, &no, 0), bw1 = mw_btn(m, 0, 0, &go, 0);   /* no key caps here (.addform .btn) */
    mw_btn(m, x + pad, fy, &no, 1); mw_btn(m, x + pad + bw2 + 8, fy, &go, 1);
    hit_add((Rect){ x + pad, fy, bw2, 32 }, "add:cancel", "KEY ESC\n");
    hit_add((Rect){ x + pad + bw2 + 8, fy, bw1, 32 }, "add:go", "KEY ENTER\n");
    /* the name: what is left */
    Text lnm = rtl(g, 12, 600, "שם", 0); put_r(g, &lnm, cx, y + pad, t->muted); text_free(&lnm);
    Rect nm = { x + pad + bw2 + 8 + bw1 + 12, fy, cx - (x + pad + bw2 + 8 + bw1 + 12), 32 };
    mw_input(m, nm, e->a, "", e->f1 == 2, 0, 1);
    { char keys[64]; keys_rep(keys, sizeof keys, "KEY TAB", (2 - e->f1 + 3) % 3, NULL); hit_add(nm, "add:name", keys); }
    put_r(g, &help, R - pad, fy + 32 + 10, t->muted);
    text_free(&lt); text_free(&help);
    return h;
}

static void map_win(G *g, const DcuiFrame *f, double W, double H) {
    const Theme *t = g->t;
    cairo_rectangle(g->cr, 0, 48, W, H - 48 - 40);                       /* .usb .scrim */
    if (g->dark) draw_set_a(g->cr, (Rgb){ 5 / 255.0, 9 / 255.0, 14 / 255.0 }, .62);
    else draw_set_a(g->cr, (Rgb){ 37 / 255.0, 51 / 255.0, 61 / 255.0 }, .45);
    cairo_fill(g->cr);
    const double rw = g->dark ? 8 : 14;                                  /* --r-win */
    Rect box = { 22, 66, W - 44, H - 66 - 58 };                          /* .usb .win: inset 22px, 66px 58px */
    draw_box_shadow(g->cr, box, rw, 18, 48, 0, (Rgb){ 0, 0, 0 }, g->dark ? .45 : .22);
    draw_fill_rrect(g->cr, box, rw, t->hair);
    draw_fill_rrect(g->cr, (Rect){ box.x + 1, box.y + 1, box.w - 2, box.h - 2 }, rw - 1, t->surface);
    Mw m = { g, f, box.x, box.w, box.x + box.w, f->lang_chip[0] ? f->lang_chip : "EN" };
    cairo_save(g->cr); draw_rrect_path(g->cr, box, rw); cairo_clip(g->cr);
    /* .w-head */
    Text h2 = rtl(g, 22, 600, f->dlg_title, box.w - 40), sub = rtl(g, 13, 400, f->dlg_lead, box.w - 40);
    double y = box.y + 16;
    put_r(g, &h2, m.R - 20, y, t->ink); y += h2.h + 4;
    put_r(g, &sub, m.R - 20, y, t->muted); y += sub.h + 10;
    text_free(&h2); text_free(&sub);
    /* the footer first: the table gets what is left */
    const DcuiEl *foot = NULL; int nrows = 0; double other = 0;
    for (int i = 0; i < f->nel; i++) {
        const DcuiEl *e = &f->el[i];
        if (e->type == DEL_FOOT) foot = e;
        if (e->type == DEL_CONN) nrows++;
    }
    const double fh = 12 + 32 + 12, ybot = box.y + box.h - fh;
    double ny = y;
    for (int i = 0; i < f->nel; i++) {
        const DcuiEl *e = &f->el[i];
        if (e->type == DEL_ALERT) {
            Text n = mw_note_text(g, e->a, strcmp(e->c, "info") ? t->danger_fg : t->ink, box.w - 44 - 24 - 3);
            other += 9 + n.h + 9 + 10; text_free(&n);
        } else if (e->type == DEL_ADD) other += mw_add(&m, e, 0, 0) + 4;
        else if (e->type == DEL_ADDLINK) other += 40;
    }
    Text th = rtl(g, 12, 600, "חיבור", 0);
    const double thh = 8 + th.h + 8;
    double rh = nrows ? (ybot - ny - other - thh) / nrows : 44;
    if (rh > 44) rh = 44;
    if (rh < 32) rh = 32;
    for (int i = 0; i < f->nel; i++) {
        const DcuiEl *e = &f->el[i];
        if (e->type != DEL_ALERT) continue;                              /* .note */
        const int info = !strcmp(e->c, "info");
        Text n = mw_note_text(g, e->a, info ? t->ink : t->danger_fg, box.w - 44 - 24 - 3);
        Rect r = { box.x + 22, y, box.w - 44, 9 + n.h + 9 };
        draw_fill_rrect(g->cr, r, t->radius_sm, info ? t->indigo_soft : t->danger_soft);
        draw_fill_rrect(g->cr, (Rect){ r.x + r.w - 3, r.y, 3, r.h }, 0, info ? t->indigo : t->danger);
        put_r(g, &n, r.x + r.w - 3 - 12, r.y + 9, t->ink);
        y += r.h + 10; text_free(&n);
    }
    /* thead */
    const char *heads[4] = { "חיבור", "מחובר עכשיו", "שם", "פעיל" };
    const double hx[4] = { m.R - 22, m.R - 118 - 10, m.x + 72 + 236 - 10, 0 };
    for (int i = 0; i < 4; i++) {
        Text c = rtl(g, 12, 600, heads[i], 0);
        if (i < 3) put_r(g, &c, hx[i], y + 8, t->muted); else put_l(g, &c, m.x + 22, y + 8, t->muted);
        text_free(&c);
    }
    text_free(&th);
    y += thh; draw_fill_rrect(g->cr, (Rect){ m.x, y - 1, m.w, 1 }, 0, t->hair);
    for (int i = 0; i < f->nel; i++) {
        const DcuiEl *e = &f->el[i];
        if (e->type == DEL_CONN) y += mw_row(&m, e, y, rh, 1);
        else if (e->type == DEL_ADDLINK) {                                /* .addrow: the link, the hint */
            Text l = rtl(g, 13, 600, e->a, 0), hn = rtl(g, 13, 400, e->b, 0);
            double cy = y + 20;
            put_r(g, &l, m.R - 22, cy - l.h / 2.0, t->action_fg);
            put_r(g, &hn, m.R - 22 - l.w - 10, cy - hn.h / 2.0, mw_faint(g));
            hit_add((Rect){ m.R - 22 - l.w - 4, y + 6, l.w + 8, 28 }, "addlink", "CHAR 2b\n");
            text_free(&l); text_free(&hn); y += 40;
        } else if (e->type == DEL_ADD) y += mw_add(&m, e, y, 1) + 4;
    }
    /* .w-foot: the status at the right, then the buttons from the left -- שמירה first */
    draw_fill_rrect(g->cr, (Rect){ m.x, ybot, m.w, 1 }, 0, t->hair);
    double fy = ybot + 12;
    if (foot) {
        Text s = rtl(g, 13, 400, foot->a, 0), d = rtl(g, 13, 400, foot->b, 0);
        put_r(g, &s, m.R - 22, fy + (32 - s.h) / 2, t->muted);
        if (foot->b[0]) put_r(g, &d, m.R - 22 - s.w - 8, fy + (32 - d.h) / 2, t->warning_ink);
        text_free(&s); text_free(&d);
    }
    double bx = m.x + 22;
    for (int i = f->nel - 1; i >= 0; i--) {
        const DcuiEl *e = &f->el[i];
        if (e->type != DEL_BTN) continue;
        bx += mw_btn(&m, bx, fy, e, 1) + 8;
    }
    cairo_restore(g->cr);
}

static void dialog(G *g, const DcuiFrame *f, double W, double H) {
    const Theme *t = g->t;
    if (!strcmp(f->dlg_kind, "map")) { map_win(g, f, W, H); return; }   /* #2001 */
    cairo_rectangle(g->cr, 0, 48, W, H - 48 - 40);                       /* .scrim */
    draw_set_a(g->cr, (Rgb){ 10 / 255.0, 16 / 255.0, 22 / 255.0 }, .55); cairo_fill(g->cr);
    const int danger = !strcmp(f->dlg_kind, "danger");
    double w = f->dlg_width > 0 ? f->dlg_width : 560;
    if (w > W - 40) w = W - 40;
    Dlg d = { g, f, 0, w - 60, danger };
    double top_border = danger ? 4 : 0;
    double h = top_border + 28 + dialog_body(&d, 0, 0) + 24;
    double x = (W - w) / 2, y = 48 + (H - 48 - 40 - h) / 2;
    if (y < 56) y = 56;
    Rect box = { x, y, w, h };
    draw_box_shadow(g->cr, box, 10, 24, 60, -12, (Rgb){ 0, 0, 0 }, g->dark ? .5 : .18);
    draw_fill_rrect(g->cr, box, 10, t->hair);
    draw_fill_rrect(g->cr, (Rect){ x + 1, y + 1, w - 2, h - 2 }, 9, t->surface);
    if (danger) {                                                         /* .dlg.pw: border-top 4px */
        cairo_save(g->cr);
        draw_rrect_path(g->cr, box, 10); cairo_clip(g->cr);
        cairo_rectangle(g->cr, x, y, w, 4); draw_set(g->cr, t->danger); cairo_fill(g->cr);
        cairo_restore(g->cr);
    }
    d.x = x + 30;
    dialog_body(&d, y + top_border + 28, 1);
}

static void notice(G *g, const DcuiFrame *f, double W, double H) {
    const Theme *t = g->t;
    Text n = rtl(g, 13.5, 500, f->notice, fmin(720, W - 120));
    double w = n.w + 32, h = n.h + 18, x = (W - w) / 2, y = H - 40 - 16 - h;
    draw_box_shadow(g->cr, (Rect){ x, y, w, h }, 8, 6, 16, 0, (Rgb){ 0, 0, 0 }, g->dark ? .4 : .14);
    draw_fill_rrect(g->cr, (Rect){ x, y, w, h }, 8, t->info);
    draw_fill_rrect(g->cr, (Rect){ x + 1, y + 1, w - 2, h - 2 }, 7, t->info_soft);
    put_r(g, &n, x + w - 16, y + 9, t->ink);
    text_free(&n);
}

/* clamp(H/800, 1, 1.4) to a quarter, as screens.c ui_scale: a 1080p screen is
 * the same layout at 1.25, not 1024x768 with more empty space. */
static double scale_for(int H) { double s = floor(H / 800.0 * 4) / 4; return s < 1 ? 1 : s > 1.4 ? 1.4 : s; }

void dcui_draw(const DcuiFrame *f, const Theme *t, cairo_t *cr, int Wpx, int Hpx) {
    double s = scale_for(Hpx), W = Wpx / s, H = Hpx / s;
    G g = { cr, t, W < 1200, t == &THEME_DARK };
    nhits = 0; hit_s = s;                                       /* #1959: this draw's */
    cairo_save(cr);
    cairo_scale(cr, s, s);
    draw_fill_rrect(cr, (Rect){ 0, 0, W, H }, 0, t->porcelain);
    header(&g, f, W);
    if (f->valid) {
        const double py = g.sm ? 18 : 24, px = g.sm ? 20 : 32;
        Rect page = { px, 48 + py, W - 2 * px, H - 48 - 40 - 2 * py };
        cairo_save(cr);
        cairo_rectangle(cr, 0, 48, W, H - 88); cairo_clip(cr);
        if (!strcmp(f->screen, "settings")) settings_body(&g, f, page);
        else main_body(&g, f, page);
        cairo_restore(cr);
        if (f->dialog) dialog(&g, f, W, H);
        if (f->notice[0]) notice(&g, f, W, H);
    }
    status_line(&g, f, W, H);
    cairo_restore(cr);
}
