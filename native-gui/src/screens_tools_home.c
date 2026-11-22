/* #649 -- the toolbox's way in and its two list screens, as mockup 14 draws them
 * (approved by Nadav 31/10):
 *   the entry  -- "כלים F3" in the top bar (page 1): the cloner's wait, the build
 *                 machine after sign-in, the USB stick's local clone; grey while a
 *                 write runs, and F3 then does nothing
 *   the home   -- a tile per category (page 2): how many tools, the situation,
 *                 the first three, how many read / change / destroy; the search
 *                 over all of them, and its empty state (#3032)
 *   a category -- a row per tool, eight to a page (pages 3.x)
 * Only the existing tokens (theme.c); the numbers are the mockup's CSS
 * (_shared.css / _extra.css of mockup 14), cited where they are used. */
#include <math.h>
#include <stdio.h>
#include <string.h>
#include <strings.h>
#include "tools.h"
#include "lcui.h"
#include "localclone.h"
#include "widgets.h"
#include "draw.h"

const ToolCat TOOL_CATS[TOOL_NCATS] = {   /* mockup 14 _build.py CATS: the name, and the situation */
    { "disk",    "דיסק",            "כשהדיסק חשוד" },
    { "wipe",    "מחיקת דיסקים",    "לפני מסירה או מכירה" },
    { "net",     "רשת",             "רשת ומולטיקאסט" },
    { "windows", "Windows",         "Windows או משתמש נעולים" },
    { "boot",    "אתחול",           "כשהמחשב לא עולה" },
    { "hw",      "חומרה",           "חומרה וזיהוי" },
    { "recover", "שחזור קבצים",     "קבצים שנמחקו או דיסק גוסס" },
    { "sec",     "אבטחה",           "אבטחה ופורנזיקה" },
    { "other",   "אחר",             "שונות" },
    { "combo",   "בדיקות משולבות",  "כמה כלים כבדיקה אחת" },
};

int tool_cat_of(const Tool *t) {
    for (int c = 0; c < TOOL_NCATS; c++) if (!strcmp(t->domain, TOOL_CATS[c].key)) return c;
    return TCAT_OTHER;                       /* an unknown domain is shown, under "אחר" */
}

int tools_cat_list(const App *a, int cat, int *out) {
    int n = 0;
    for (int i = 0; i < a->ntools; i++) if (tool_cat_of(&a->tools[i]) == cat) out[n++] = i;
    return n;
}

int tools_cat_count(const App *a, int cat) { int tmp[MAX_TOOLS]; return tools_cat_list(a, cat, tmp); }

int tools_search(const App *a, int *out) {
    int n = 0;
    if (!a->tool_q[0]) return 0;
    for (int c = 0; c < TOOL_NCATS; c++)   /* in the order of the categories, as the home shows them */
        for (int i = 0; i < a->ntools; i++) {
            const Tool *t = &a->tools[i];
            if (tool_cat_of(t) != c) continue;
            if (strcasestr(t->title, a->tool_q) || strcasestr(t->what, a->tool_q)) out[n++] = i;
        }
    return n;
}

int tools_list_now(const App *a, int *out) {
    if (a->tools_view == TOOLS_HOME && a->tool_searching) return tools_search(a, out);
    return tools_cat_list(a, a->tool_cat, out);
}

int tools_first_tile(const App *a) {
    for (int c = 0; c < TOOL_NCATS; c++) if (tools_cat_count(a, c)) return c;
    return -1;
}

/* The tiles are drawn five to a row, the first at the right: Left is the next
 * one, Right the previous one, Up/Down a row (mockup 14 page 2's keyboard). */
int tools_tile_step(const App *a, int from, int key) {
    int vis[TOOL_NCATS], n = 0, at = -1;
    for (int c = 0; c < TOOL_NCATS; c++) if (tools_cat_count(a, c)) { if (c == from) at = n; vis[n++] = c; }
    if (!n) return -1;
    if (at < 0) return vis[0];
    int to = at;
    if (key == KEYSYM_LEFT && at + 1 < n) to = at + 1;
    else if (key == KEYSYM_RIGHT && at > 0) to = at - 1;
    else if (key == KEYSYM_DOWN && at + 5 < n) to = at + 5;
    else if (key == KEYSYM_DOWN && at / 5 < (n - 1) / 5) to = n - 1;   /* a short last row: its last tile */
    else if (key == KEYSYM_UP && at >= 5) to = at - 5;
    return vis[to];
}

/* ---- the entry ------------------------------------------------------------------- */

static int cloner_round_over(const State *s) {
    for (int i = 0; i < s->ndrawers; i++) {
        const char *st = s->drawers[i].state;
        if (strcmp(st, "done") && strcmp(st, "failed") && strcmp(st, "skipped") && strcmp(st, "canceled")) return 0;
    }
    return 1;
}

int tools_entry(const App *a, Screen under) {
    if (!a->st.menu_tools) return 0;                 /* v1 without the toolbox: no button at all */
    switch (under) {
    case SCREEN_STANDBY: return 1;                   /* the cloner, waiting for a round */
    case SCREEN_CLONER:                               /* the cloner in a round: grey until it ends */
        if (!a->force_cloner && !a->force_standby) return 0;   /* #2849: the build machine's direct send */
        return cloner_round_over(&a->st) ? 1 : 2;
    case SCREEN_MENU: return a->signed_in ? 1 : 0;   /* the build machine, after sign-in */
    case SCREEN_LOCAL:                                /* the USB stick: where "הגדרות F4" is, grey while copying */
        if (!a->st.lc.usb) return 0;
        if (lc_view(a) == LCVIEW_PROGRESS) return 2;
        return a->st.lc.screen != LC_SCREEN_USBSET && lc_view(a) == LCVIEW_SOURCE && !a->lcu.confirm ? 1 : 0;
    case SCREEN_TOOLS: return 1;
    default: return 0;                               /* sign-in, the student's station, every other screen */
    }
}

/* the wrench of mockup 14's TOOLS icon (_shared.js), in a 15px box */
static void wrench(cairo_t *cr, double cx, double cy) {
    cairo_save(cr);
    cairo_translate(cr, cx - 7.5, cy - 7.5); cairo_scale(cr, 15 / 24.0, 15 / 24.0);
    cairo_set_line_width(cr, 2.2); cairo_set_line_cap(cr, CAIRO_LINE_CAP_ROUND);
    cairo_new_sub_path(cr); cairo_arc(cr, 15.2, 8.8, 4.6, 0.95 * M_PI, 2.55 * M_PI); cairo_stroke(cr);
    cairo_move_to(cr, 11.8, 12.2); cairo_line_to(cr, 4.2, 19.8); cairo_stroke(cr);
    cairo_restore(cr);
}

double tools_hbtn_width(cairo_t *cr) {
    Text l = text_make(cr, FONT_SANS, 13, 500, "כלים", 0, DIR_RTL), k = text_make(cr, FONT_MONO, 11, 600, "F3", 0, DIR_LTR);
    double w = 8 + k.w + 8 + l.w + 6 + 14 + 10;
    text_free(&l); text_free(&k);
    return w;
}

/* The same button as "הגדרות F4" (screens.c draw_usbset_btn): the key, the
 * word, the icon; state 2 = grey (.n-hbtn[disabled] opacity .45) and no hit. */
double draw_tools_btn(App *a, cairo_t *cr, double x, int state) {
    const Theme *t = a->theme;
    double op = state == 2 ? .45 : 1, h = 28;
    Text l = text_make(cr, FONT_SANS, 13, 500, "כלים", 0, DIR_RTL), k = text_make(cr, FONT_MONO, 11, 600, "F3", 0, DIR_LTR);
    Rect b = { x, (N_HEADER_H - h) / 2, 8 + k.w + 8 + l.w + 6 + 14 + 10, h };
    Rect o = { b.x + .5, b.y + .5, b.w - 1, b.h - 1 };
    if (a->screen == SCREEN_TOOLS) { draw_set_a(cr, t->header_text, .14); draw_rrect_path(cr, b, RADIUS_SM); cairo_fill(cr); }
    draw_set_a(cr, t->header_text, .45 * op); draw_rrect_path(cr, o, RADIUS_SM); cairo_set_line_width(cr, 1); cairo_stroke(cr);
    cairo_push_group(cr);
    text_draw(cr, &k, b.x + 8, b.y + (b.h - k.h) / 2, t->header_text);
    text_draw(cr, &l, b.x + 8 + k.w + 8, b.y + (b.h - l.h) / 2, t->header_text);
    draw_set(cr, t->header_text); wrench(cr, b.x + b.w - 10 - 7, b.y + b.h / 2);
    cairo_pop_group_to_source(cr); cairo_paint_with_alpha(cr, op);
    if (state == 1) hit_add(a, b, HIT_TOOLS);
    text_free(&l); text_free(&k);
    return b.w;
}

/* ---- shared pieces -------------------------------------------------------------- */

Text tools_line(cairo_t *cr, Font f, double px, int weight, const char *s, double w, int lines) {
    Text t = text_make(cr, f, px, weight, s, (int)w, DIR_RTL);
    pango_layout_set_ellipsize(t.layout, PANGO_ELLIPSIZE_END);
    pango_layout_set_height(t.layout, -(lines > 0 ? lines : 1));
    pango_layout_get_pixel_size(t.layout, &t.w, &t.h);
    return t;
}

const char *tool_risk_word(int level) { return level == 0 ? "קריאה בלבד" : level == 1 ? "משנה" : "הרסני"; }

/* .risk: 11.5px 600, radius 999, padding 1px 9px; ro on --hover with a hair
 * border, rw on --warn-soft, destroy on --bad-soft */
double tool_tag(App *a, cairo_t *cr, double x_right, double cy, int level, const char *word) {
    const Theme *t = a->theme;
    Text w = text_make(cr, FONT_SANS, 11.5, 600, word, 0, DIR_RTL);
    Rect r = { x_right - w.w - 18, cy - (w.h + 2) / 2, w.w + 18, w.h + 2 };
    Rgb bg = level == 0 ? t->hover : level == 1 ? t->warning_soft : t->danger_soft;
    Rgb ink = level == 0 ? t->muted : level == 1 ? t->warning_ink : t->danger_fg;
    draw_fill_rrect(cr, r, r.h / 2, bg);
    if (level == 0) draw_border_rrect(cr, r, r.h / 2, t->hair, 1);
    text_draw(cr, &w, r.x + 9, r.y + 1, ink);
    text_free(&w);
    return r.w;
}

/* .n-col: 900px (a list, a tool), 1180px (.xw, the home), centred; 24px a side at least */
double tools_col(double W, double *x0, double *cw, int wide) {
    *cw = fmin(wide ? 1180 : 900, W - 48);
    *x0 = (W - *cw) / 2;
    return *cw;
}

/* .kbd: 600 11px/16px mono, a hair border with a 2px bottom, radius 4, padding 0 5 */
static double kbd(App *a, cairo_t *cr, double x_right, double cy, const char *k, int in_btn) {
    const Theme *t = a->theme;
    Text tk = text_make(cr, FONT_MONO, 11, 600, k, 0, DIR_LTR);
    Rect r = { x_right - tk.w - 10, cy - 9, tk.w + 10, 18 };
    if (in_btn) draw_fill_rrect(cr, r, 4, t->hover);   /* .n-btn .kbd: --hover, no border */
    else {
        draw_fill_rrect(cr, r, 4, t->hair); draw_fill_rrect(cr, (Rect){ r.x + 1, r.y + 1, r.w - 2, r.h - 3 }, 3, t->surface);
    }
    text_draw(cr, &tk, r.x + 5, r.y + (r.h - 2 - tk.h) / 2 + (in_btn ? 1 : 0), in_btn ? t->muted : t->ink);
    text_free(&tk);
    return r.w;
}

/* .n-btn: 38px, 0 18px, 14px 600 -- plain: the action colour on the surface, a
 * hair border; .primary: the action fill; .danger: the danger fill (#1270: the
 * red button); off: the track fill, muted (not clickable). The .kbd first (at
 * the right in RTL), the word after it. */
double tools_btn(App *a, cairo_t *cr, double x_right, double y, const char *label, const char *key, int kind, int id) {
    const Theme *t = a->theme;
    Text l = text_make(cr, FONT_SANS, 14, 600, label, 0, DIR_RTL);
    double kw = 0;
    if (key) { Text tk = text_make(cr, FONT_MONO, 11, 600, key, 0, DIR_LTR); kw = tk.w + 10 + 8; text_free(&tk); }
    Rect b = { x_right - (18 + kw + l.w + 18), y, 18 + kw + l.w + 18, 38 };
    int hov = kind != TB_OFF && hovered(a, b);
    Rgb bg = t->surface, line = hov ? t->indigo : t->hair, fg = t->action_fg;
    if (kind == TB_PRIMARY) { bg = hov ? t->ink_hover : t->indigo; line = bg; fg = t->on_ink; }
    else if (kind == TB_DANGER) { bg = t->danger_fill; line = bg; fg = t->on_ink; }
    else if (kind == TB_OFF) { bg = t->track; line = bg; fg = t->muted; }
    draw_fill_rrect(cr, b, RADIUS_SM, bg);
    draw_border_rrect(cr, b, RADIUS_SM, line, 1);
    double xr = b.x + b.w - 18;
    if (key) xr -= kbd(a, cr, xr, b.y + b.h / 2, key, 1) + 8;
    text_draw_r(cr, &l, xr, b.y + (b.h - l.h) / 2, fg);
    text_free(&l);
    if (id && kind != TB_OFF) hit_add(a, b, id);
    return b.w;
}
static double nbtn(App *a, cairo_t *cr, double x_right, double y, const char *label, const char *key, int id) {
    return tools_btn(a, cr, x_right, y, label, key, TB_PLAIN, id);
}

double tools_btn_w(cairo_t *cr, const char *label, const char *key) {
    Text l = text_make(cr, FONT_SANS, 14, 600, label, 0, DIR_RTL);
    double w = 18 + l.w + 18;
    text_free(&l);
    if (key) { Text k = text_make(cr, FONT_MONO, 11, 600, key, 0, DIR_LTR); w += k.w + 10 + 8; text_free(&k); }
    return w;
}

static double tag_width(cairo_t *cr, const char *word) {
    Text w = text_make(cr, FONT_SANS, 11.5, 600, word, 0, DIR_RTL);
    double r = w.w + 18;
    text_free(&w);
    return r;
}

/* .n-titlerow: the crumb, h1 29px 600, the sub 14.5px muted; .side at the far
 * (left) edge -- the "Esc חזרה" button, or the tool's risk tag */
double tools_title(App *a, cairo_t *cr, double x0, double cw, double y, const char *crumb, const char *h1,
                   const char *sub, int level_tag, int esc_btn) {
    const Theme *t = a->theme;
    double xr = x0 + cw, top = y, side = 0;
    if (esc_btn) side = tools_btn_w(cr, "חזרה", "Esc") + 14;
    else if (level_tag >= 0) side = tag_width(cr, tool_risk_word(level_tag)) + 14;
    Text hc = { NULL, 0, 0 };
    if (crumb) { hc = text_make(cr, FONT_SANS, 12.5, 400, crumb, 0, DIR_RTL); text_draw_r(cr, &hc, xr, y, t->muted); y += hc.h + 2; }
    Text h = tools_line(cr, FONT_SANS, 29, 600, h1, cw - side, 1);
    text_draw_r(cr, &h, xr, y, t->ink); y += h.h;
    if (sub && sub[0]) {
        Text s = text_make(cr, FONT_SANS, 14.5, 400, sub, (int)(cw - side), DIR_RTL);
        text_draw_r(cr, &s, xr, y + 4, t->muted); y += 4 + s.h; text_free(&s);
    }
    if (esc_btn) nbtn(a, cr, x0 + side - 14, top, "חזרה", "Esc", esc_btn);
    else if (level_tag >= 0)
        tool_tag(a, cr, x0 + side - 14, top + 10, level_tag, tool_risk_word(level_tag));   /* .side: at the row's top */
    if (crumb) text_free(&hc);
    text_free(&h);
    return y;
}

/* ---- the icons of the categories (mockup 14 ICON, the 24-unit SVGs) ----------- */

static void cat_icon(cairo_t *cr, int cat, double cx, double cy, double px) {
    cairo_save(cr);
    cairo_translate(cr, cx - px / 2, cy - px / 2); cairo_scale(cr, px / 24, px / 24);
    cairo_set_line_width(cr, 2); cairo_set_line_cap(cr, CAIRO_LINE_CAP_ROUND); cairo_set_line_join(cr, CAIRO_LINE_JOIN_ROUND);
    switch (cat) {
    case TCAT_DISK:
        cairo_save(cr); cairo_translate(cr, 12, 6); cairo_scale(cr, 8, 3); cairo_arc(cr, 0, 0, 1, 0, 2 * M_PI); cairo_restore(cr); cairo_stroke(cr);
        cairo_move_to(cr, 4, 6); cairo_line_to(cr, 4, 18); cairo_move_to(cr, 20, 6); cairo_line_to(cr, 20, 18); cairo_stroke(cr);
        for (int k = 0; k < 2; k++) {
            cairo_save(cr); cairo_translate(cr, 12, k ? 18 : 12); cairo_scale(cr, 8, 3); cairo_arc(cr, 0, 0, 1, 0, M_PI); cairo_restore(cr); cairo_stroke(cr);
        }
        break;
    case TCAT_WIPE:
        cairo_move_to(cr, 4, 7); cairo_line_to(cr, 20, 7);
        cairo_move_to(cr, 9, 7); cairo_line_to(cr, 9, 4); cairo_line_to(cr, 15, 4); cairo_line_to(cr, 15, 7);
        cairo_move_to(cr, 6, 7); cairo_line_to(cr, 7, 20); cairo_line_to(cr, 17, 20); cairo_line_to(cr, 18, 7); cairo_stroke(cr);
        break;
    case TCAT_NET:
        cairo_rectangle(cr, 9, 2, 6, 5); cairo_rectangle(cr, 2, 17, 6, 5); cairo_rectangle(cr, 16, 17, 6, 5);
        cairo_move_to(cr, 12, 7); cairo_line_to(cr, 12, 12);
        cairo_move_to(cr, 5, 17); cairo_line_to(cr, 5, 12); cairo_line_to(cr, 19, 12); cairo_line_to(cr, 19, 17); cairo_stroke(cr);
        break;
    case TCAT_WINDOWS:
        cairo_move_to(cr, 3, 5); cairo_line_to(cr, 11, 4); cairo_line_to(cr, 11, 11); cairo_line_to(cr, 3, 11); cairo_close_path(cr);
        cairo_move_to(cr, 13, 4); cairo_line_to(cr, 21, 3); cairo_line_to(cr, 21, 11); cairo_line_to(cr, 13, 11); cairo_close_path(cr);
        cairo_move_to(cr, 3, 13); cairo_line_to(cr, 11, 13); cairo_line_to(cr, 11, 20); cairo_line_to(cr, 3, 19); cairo_close_path(cr);
        cairo_move_to(cr, 13, 13); cairo_line_to(cr, 21, 13); cairo_line_to(cr, 21, 21); cairo_line_to(cr, 13, 20); cairo_close_path(cr);
        cairo_fill(cr);
        break;
    case TCAT_BOOT:
        cairo_move_to(cr, 12, 3); cairo_line_to(cr, 12, 12);
        cairo_new_sub_path(cr); cairo_arc(cr, 12, 13, 7, -0.28 * M_PI, 1.28 * M_PI); cairo_stroke(cr);
        break;
    case TCAT_HW:
        draw_rrect_path(cr, (Rect){ 6, 6, 12, 12 }, 2);
        for (int k = 0; k < 2; k++) {
            double p = k ? 15 : 9;
            cairo_move_to(cr, p, 2); cairo_line_to(cr, p, 6); cairo_move_to(cr, p, 18); cairo_line_to(cr, p, 22);
            cairo_move_to(cr, 2, p); cairo_line_to(cr, 6, p); cairo_move_to(cr, 18, p); cairo_line_to(cr, 22, p);
        }
        cairo_stroke(cr);
        break;
    case TCAT_RECOVER:
        cairo_new_sub_path(cr); cairo_arc(cr, 12, 12, 8, -0.75 * M_PI, 1.0 * M_PI); cairo_stroke(cr);
        cairo_move_to(cr, 4, 4); cairo_line_to(cr, 4, 8); cairo_line_to(cr, 8, 8); cairo_stroke(cr);
        break;
    case TCAT_SEC:
        cairo_move_to(cr, 12, 3); cairo_line_to(cr, 20, 6); cairo_line_to(cr, 20, 12);
        cairo_curve_to(cr, 20, 16.5, 16.6, 20, 12, 21); cairo_curve_to(cr, 7.4, 20, 4, 16.5, 4, 12);
        cairo_line_to(cr, 4, 6); cairo_close_path(cr); cairo_stroke(cr);
        break;
    case TCAT_OTHER:
        for (int k = 0; k < 3; k++) { cairo_new_sub_path(cr); cairo_arc(cr, 5 + 7 * k, 12, 2, 0, 2 * M_PI); }
        cairo_fill(cr);
        break;
    default:   /* combo: two layers */
        cairo_move_to(cr, 12, 3); cairo_line_to(cr, 21, 8); cairo_line_to(cr, 12, 13); cairo_line_to(cr, 3, 8); cairo_close_path(cr);
        cairo_move_to(cr, 3, 13); cairo_line_to(cr, 12, 18); cairo_line_to(cr, 21, 13); cairo_stroke(cr);
        break;
    }
    cairo_restore(cr);
}

static void search_icon(cairo_t *cr, double cx, double cy, double px) {
    cairo_save(cr);
    cairo_translate(cr, cx - px / 2, cy - px / 2); cairo_scale(cr, px / 24, px / 24);
    cairo_set_line_width(cr, 2); cairo_set_line_cap(cr, CAIRO_LINE_CAP_ROUND);
    cairo_new_sub_path(cr); cairo_arc(cr, 10.5, 10.5, 6.5, 0, 2 * M_PI);
    cairo_move_to(cr, 15.5, 15.5); cairo_line_to(cr, 21, 21); cairo_stroke(cr);
    cairo_restore(cr);
}

/* ---- words ----------------------------------------------------------------------- */

static void count_words(char *out, size_t n, int k, int combo) {
    if (combo) snprintf(out, n, k == 1 ? "בדיקה אחת" : "%d בדיקות", k);
    else snprintf(out, n, k == 1 ? "כלי אחד" : "%d כלים", k);
}

static void mix_counts(const App *a, const int *items, int n, int *ro, int *rw, int *de) {
    *ro = *rw = *de = 0;
    for (int i = 0; i < n; i++) {
        int l = tools_risk_level(a->tools[items[i]].risk);
        if (l == 0) (*ro)++; else if (l == 1) (*rw)++; else (*de)++;
    }
}

/* the chips of a tile: "10 קריאה", "3 משנים", "3 הרסניים" (mockup mix()) */
static int mix_chips(char w[3][32], int lv[3], int ro, int rw, int de) {
    int k = 0;
    if (ro) { snprintf(w[k], 32, "%d קריאה", ro); lv[k++] = 0; }
    if (rw) { snprintf(w[k], 32, rw == 1 ? "%d משנה" : "%d משנים", rw); lv[k++] = 1; }
    if (de) { snprintf(w[k], 32, de == 1 ? "%d הרסני" : "%d הרסניים", de); lv[k++] = 2; }
    return k;
}

/* "10 קריאה בלבד, 3 משנים, 3 הרסניים" (mockup mix_words()) */
static void mix_sentence(char *out, size_t n, int ro, int rw, int de) {
    int k = 0; out[0] = 0;
    if (ro) k += snprintf(out + k, n - k, "%d קריאה בלבד", ro);
    if (rw && (size_t)k < n) k += snprintf(out + k, n - k, rw == 1 ? "%s%d משנה" : "%s%d משנים", k ? ", " : "", rw);
    if (de && (size_t)k < n) snprintf(out + k, n - k, de == 1 ? "%s%d הרסני" : "%s%d הרסניים", k ? ", " : "", de);
}

/* ---- the home ---------------------------------------------------------------------- */

/* .tb-search: 44px, a hair border (2px action when typing), the field colour, 15px;
 * the icon at the start, "/ חיפוש" or the count at the end */
static double search_box(App *a, cairo_t *cr, double x0, double cw, double y, double h, int nres, int ncats) {
    const Theme *t = a->theme;
    Rect r = { x0, y, cw, h };
    int on = a->tool_searching;
    draw_fill_rrect(cr, r, RADIUS_SM, t->field);
    draw_border_rrect(cr, r, RADIUS_SM, on ? t->indigo : t->hair, on ? 2 : 1);
    draw_set(cr, on ? t->ink : t->muted); search_icon(cr, r.x + r.w - 14 - 9, r.y + h / 2, 18);
    double xr = r.x + r.w - 14 - 18 - 10;
    if (on) {
        Text q = text_make(cr, FONT_SANS, 15, 600, a->tool_q[0] ? a->tool_q : " ", 0, DIR_RTL);
        text_draw_r(cr, &q, xr, r.y + (h - q.h) / 2, t->ink);
        double cx = xr - (a->tool_q[0] ? q.w : 0) - 6;
        draw_fill_rrect(cr, (Rect){ cx, r.y + (h - 20) / 2, 2, 20 }, 0, t->indigo);   /* .caret */
        text_free(&q);
        char c[96];
        if (!a->tool_q[0]) snprintf(c, sizeof c, "מקלידים שם של כלי או מה שהוא עושה");
        else if (!nres) snprintf(c, sizeof c, "0 תוצאות");
        else {
            char cats[48];
            if (ncats == 1) snprintf(cats, sizeof cats, "בקטגוריה אחת");
            else if (ncats == 2) snprintf(cats, sizeof cats, "בשתי קטגוריות");
            else snprintf(cats, sizeof cats, "ב-%d קטגוריות", ncats);
            if (nres == 1) snprintf(c, sizeof c, "תוצאה אחת %s", cats);
            else snprintf(c, sizeof c, "%d תוצאות %s", nres, cats);
        }
        Text ct = text_make(cr, FONT_SANS, 12.5, 400, c, 0, DIR_RTL);
        text_draw(cr, &ct, r.x + 14, r.y + (h - ct.h) / 2, t->muted); text_free(&ct);
    } else {
        Text p = tools_line(cr, FONT_SANS, 15, 400, "מקלידים כדי לחפש כלי — בשם או במה שהוא עושה", cw - 160, 1);
        text_draw_r(cr, &p, xr, r.y + (h - p.h) / 2, t->muted); text_free(&p);
        Text w = text_make(cr, FONT_SANS, 12.5, 400, "חיפוש", 0, DIR_RTL);
        text_draw(cr, &w, r.x + 14, r.y + (h - w.h) / 2, t->muted);
        kbd(a, cr, r.x + 14 + w.w + 4 + 18, r.y + h / 2, "/", 0);
        text_free(&w);
    }
    hit_add(a, r, HIT_TOOL_SEARCH);
    return h;
}

/* .tb-cat: one category -- the icon, the name, "N כלים · <situation>", the first
 * three, the chips at the bottom. Focus: a 3px ring in the action colour. */
static void tile_draw(App *a, cairo_t *cr, Rect r, int cat, int focus) {
    const Theme *t = a->theme;
    int items[MAX_TOOLS], n = tools_cat_list(a, cat, items), ro, rw, de;
    mix_counts(a, items, n, &ro, &rw, &de);
    int hov = hovered(a, r);
    draw_box_shadow(cr, r, RADIUS_SM, 4, 14, -8, t->shadow_strong, t->shadow_card_a);
    draw_fill_rrect(cr, r, RADIUS_SM, t->surface);
    if (focus) {
        draw_border_rrect(cr, (Rect){ r.x - 3, r.y - 3, r.w + 6, r.h + 6 }, RADIUS_SM + 3, t->indigo, 3);
        draw_border_rrect(cr, r, RADIUS_SM, t->indigo, 1);
    } else draw_border_rrect(cr, r, RADIUS_SM, hov ? t->indigo : t->hair, 1);
    /* compact (a 960x540 screen, tiles under 190px): the icon at the name's side, 26px; the
     * name 16px; the counts on one line -- the mockup's 960 frame is its 1422 layout scaled */
    int compact = r.h < 190;
    double pad_x = compact ? 12 : 16, pad_y = compact ? 12 : 14, xr = r.x + r.w - pad_x, iw = r.w - 2 * pad_x, y = r.y + pad_y;
    int danger = cat == TCAT_WIPE;
    double isz = compact ? 26 : 34;
    Rect ic = { xr - isz, y, isz, isz };
    draw_fill_rrect(cr, ic, compact ? 6 : 8, danger ? t->danger_soft : t->indigo_soft);
    draw_set(cr, danger ? t->danger : t->indigo); cat_icon(cr, cat, ic.x + isz / 2, ic.y + isz / 2, compact ? 15 : 18);
    if (compact) {
        Text h = tools_line(cr, FONT_SANS, 16, 600, TOOL_CATS[cat].title, iw - isz - 8, 1);
        text_draw_r(cr, &h, xr - isz - 8, y + (isz - h.h) / 2, t->ink); y += isz + 6; text_free(&h);
    } else {
        y += 34 + 6;
        Text h = tools_line(cr, FONT_SANS, 18, 600, TOOL_CATS[cat].title, iw, 1);
        text_draw_r(cr, &h, xr, y, t->ink); y += h.h + 4; text_free(&h);
    }
    char cnt[48], line[200]; count_words(cnt, sizeof cnt, n, cat == TCAT_COMBO);
    snprintf(line, sizeof line, "%s · %s", cnt, TOOL_CATS[cat].situation);
    Text nt = tools_line(cr, FONT_SANS, 12.5, 400, line, iw, compact ? 1 : 2);
    text_draw_r(cr, &nt, xr, y, t->muted); y += nt.h; text_free(&nt);
    /* the chips, wrapped from the right, at the bottom (.mix: margin-top auto) */
    char w[3][32]; int lv[3], nc = mix_chips(w, lv, ro, rw, de);
    double cw[3], chip_h = 0;
    for (int k = 0; k < nc; k++) { Text tw = text_make(cr, FONT_SANS, 11.5, 600, w[k], 0, DIR_RTL); cw[k] = tw.w + 18; chip_h = tw.h + 2; text_free(&tw); }
    int rows = nc ? 1 : 0; double used = 0;
    for (int k = 0; k < nc; k++) { if (used && used + 4 + cw[k] > iw) { rows++; used = 0; } used += (used ? 4 : 0) + cw[k]; }
    double mix_h = rows ? rows * chip_h + (rows - 1) * 4 : 0, mix_y = r.y + r.h - pad_y - mix_h;
    /* .ex: the first three, 12px, as many lines as are left (at most three) */
    int lines = (int)floor((mix_y - 4 - (y + 4)) / 17.4);
    if (lines > 3) lines = 3;
    if (lines > 0 && n) {
        char ex[400]; int k = 0; ex[0] = 0;
        for (int i = 0; i < n && i < 3 && (size_t)k < sizeof ex; i++)
            k += snprintf(ex + k, sizeof ex - k, "%s%s", i ? " · " : "", a->tools[items[i]].title);
        Text et = tools_line(cr, FONT_SANS, 12, 400, ex, iw, lines);
        text_draw_r(cr, &et, xr, y + 4, t->muted); text_free(&et);
    }
    double cx = xr, cy = mix_y + chip_h / 2; used = 0;
    for (int k = 0; k < nc; k++) {
        if (used && used + 4 + cw[k] > iw) { cy += chip_h + 4; cx = xr; used = 0; }
        tool_tag(a, cr, cx, cy, lv[k], w[k]);
        cx -= cw[k] + 4; used += (used ? 4 : 0) + cw[k];
    }
    hit_add(a, r, HIT_TOOL_CAT_BASE + cat);
}

static void legend(App *a, cairo_t *cr, double x0, double cw, double y) {
    const Theme *t = a->theme;
    static const char *what[3] = { "מציג, לא משנה דבר", "משנה משהו הפיך", "מוחק — כפתור אדום, בלי הקלדה" };
    double xr = x0 + cw;
    for (int k = 0; k < 3; k++) {
        double tw = tool_tag(a, cr, xr, y + 9, k, tool_risk_word(k));
        Text w = text_make(cr, FONT_SANS, 12.5, 400, what[k], 0, DIR_RTL);
        text_draw_r(cr, &w, xr - tw - 6, y + 9 - w.h / 2, t->muted);
        xr -= tw + 6 + w.w + 16; text_free(&w);
    }
}

/* #3032: a quiet grey line when there is nothing to do */
static void grey_line(App *a, cairo_t *cr, double x0, double cw, double y, const char *s) {
    Text e = text_make(cr, FONT_SANS, 14, 400, s, (int)cw, DIR_RTL);
    text_draw_r(cr, &e, x0 + cw, y, a->theme->muted); text_free(&e);
}

/* #3032: when there is something to do -- an icon, a short sentence, the button */
static void search_empty(App *a, cairo_t *cr, double x0, double cw, double y, double bottom) {
    const Theme *t = a->theme;
    char h3[160]; snprintf(h3, sizeof h3, "אין כלי שמתאים ל\"%s\"", a->tool_q);
    Text h = text_make(cr, FONT_SANS, 19, 600, h3, (int)(cw - 56), DIR_RTL);
    Text p = text_make(cr, FONT_SANS, 14, 400, "החיפוש עובר על השמות והתיאורים בכל הקטגוריות. נסו מילה אחרת, או חזרו לקטגוריות.",
                       (int)fmin(620, cw - 56), DIR_RTL);
    pango_layout_set_alignment(h.layout, PANGO_ALIGN_CENTER); pango_layout_set_alignment(p.layout, PANGO_ALIGN_CENTER);
    double ch = 28 + 30 + 10 + h.h + 6 + p.h + 18 + 38 + 28;
    if (y + ch > bottom) ch = bottom - y;
    Rect c = { x0, y, cw, ch };
    draw_fill_rrect(cr, c, RADIUS_SM, t->surface); draw_border_rrect(cr, c, RADIUS_SM, t->hair, 1);
    double cy = y + 28;
    draw_fill_rrect(cr, (Rect){ x0 + cw / 2 - 17, cy - 2, 34, 34 }, 8, t->indigo_soft);
    draw_set(cr, t->indigo); search_icon(cr, x0 + cw / 2, cy + 15, 18);
    cy += 30 + 10;
    text_draw(cr, &h, x0 + 28, cy, t->ink); cy += h.h + 6;
    text_draw(cr, &p, x0 + (cw - fmin(620, cw - 56)) / 2, cy, t->muted); cy += p.h + 18;
    nbtn(a, cr, x0 + (cw + tools_btn_w(cr, "נקה חיפוש", NULL)) / 2, cy, "נקה חיפוש", NULL, HIT_TOOL_CLEAR);
    text_free(&h); text_free(&p);
}

void tools_home_view(App *a, cairo_t *cr, double W, double H, double head_h) {
    double x0, cw; tools_col(W, &x0, &cw, 1);
    int compact = H < 700;
    double y = head_h + (compact ? 16 : 30), bottom = H - N_STATUS_H - (compact ? 10 : 16);
    int usb = a->st.lc.usb, total = a->ntools, combos = tools_cat_count(a, TCAT_COMBO), ncats = 0;
    for (int c = 0; c < TOOL_NCATS; c++) if (tools_cat_count(a, c)) ncats++;
    char sub[320];
    if (a->tool_searching) snprintf(sub, sizeof sub, "חיפוש בכל הקטגוריות. Enter פותח את הכלי המסומן.");
    else if (usb) snprintf(sub, sizeof sub, "אבחון ותיקון במחשב הזה, בלי שרת: %d כלים ובדיקות. מה שצריך שרת לא מוצג כאן.", total);
    else if (combos) snprintf(sub, sizeof sub, "אבחון ותיקון במחשב הזה: %d כלים ו-%d בדיקות משולבות ב-%d קטגוריות. כלי שמשנה דיסק נפתח בחלון אישור.",
                              total - combos, combos, ncats);
    else snprintf(sub, sizeof sub, "אבחון ותיקון במחשב הזה: %d כלים ב-%d קטגוריות. כלי שמשנה דיסק נפתח בחלון אישור.", total, ncats);
    y = tools_title(a, cr, x0, cw, y, NULL, "כלים", sub, -1, HIT_TOOL_EXIT) + (compact ? 12 : 20);
    if (!a->tools_loaded || !a->ntools) {   /* #3032: nothing the person can do here -- a grey line */
        grey_line(a, cr, x0, cw, y, !a->tools_loaded ? "טוען את רשימת הכלים…"
                                    : usb ? "אין כלים בדיסק-און-קי הזה." : "לא נבחרו כלים בשרת (תשתית › ארגז כלים).");
        return;
    }
    int res[MAX_TOOLS], nres = 0, rcats = 0;
    if (a->tool_searching) {
        nres = tools_search(a, res);
        int seen[TOOL_NCATS] = { 0 };
        for (int i = 0; i < nres; i++) seen[tool_cat_of(&a->tools[res[i]])] = 1;
        for (int c = 0; c < TOOL_NCATS; c++) rcats += seen[c];
    }
    double sh = compact ? 40 : 44;
    y += search_box(a, cr, x0, cw, y, sh, nres, rcats) + (compact ? 10 : 16);
    if (a->tool_searching) {
        if (!a->tool_q[0]) return;
        if (!nres) { search_empty(a, cr, x0, cw, y, bottom); return; }
        tools_list_draw(a, cr, x0, cw, y, bottom, res, nres, 1);
        return;
    }
    int vis[TOOL_NCATS], nv = 0;
    for (int c = 0; c < TOOL_NCATS; c++) if (tools_cat_count(a, c)) vis[nv++] = c;
    double gap = compact ? 10 : 12, legend_h = compact ? 0 : 14 + 18;
    int rows = (nv + 4) / 5;
    double tw = (cw - 4 * gap) / 5, th = fmin(212, (bottom - legend_h - y - (rows - 1) * gap) / rows);
    for (int k = 0; k < nv; k++) {
        int row = k / 5, col = k % 5;
        Rect r = { x0 + cw - tw - col * (tw + gap), y + row * (th + gap), tw, th };
        tile_draw(a, cr, r, vis[k], a->tool_tile == vis[k]);
    }
    if (!compact) legend(a, cr, x0, cw, y + rows * th + (rows - 1) * gap + 14);
}

/* ---- a list of tools: a category, or the search's results ---------------------- */

void tools_list_draw(App *a, cairo_t *cr, double x0, double cw, double y, double bottom, const int *items, int n, int with_cat) {
    const Theme *t = a->theme;
    double row_h = 62, pitch = row_h + 6, pager_h = 6 + 18;
    int per = (int)floor((bottom - y - pager_h + 6) / pitch);
    if (per > 8) per = 8;              /* mockup 14: eight tools to a page */
    if (per < 1) per = 1;
    a->tool_per_page = per;
    if (a->tool_row >= n) a->tool_row = n - 1;
    if (a->tool_row < 0) a->tool_row = 0;
    int page = a->tool_row / per, first = page * per, last = first + per < n ? first + per : n;
    double xr = x0 + cw;
    for (int i = first; i < last; i++) {
        const Tool *tool = &a->tools[items[i]];
        Rect r = { x0, y, cw, row_h };
        int focus = i == a->tool_row, hov = hovered(a, r), lvl = tools_risk_level(tool->risk);
        draw_fill_rrect(cr, r, RADIUS_SM, focus || hov ? t->indigo_soft : t->surface);
        draw_border_rrect(cr, r, RADIUS_SM, focus || hov ? t->indigo : t->hair, focus ? 2 : 1);
        double tw = tag_width(cr, tool_risk_word(lvl));
        tool_tag(a, cr, r.x + 16 + tw, r.y + r.h / 2, lvl, tool_risk_word(lvl));
        double iw = cw - 32 - tw - 14;
        Text ti = tools_line(cr, FONT_SANS, 15, 600, tool->title, iw, 1);
        Text de = tools_line(cr, FONT_SANS, 12.5, 400, tool->what[0] ? tool->what : " ", iw, 1);
        double block = ti.h + (tool->what[0] ? de.h : 0), ty = r.y + (r.h - block) / 2;
        text_draw_r(cr, &ti, xr - 16, ty, t->ink);
        if (with_cat) {   /* the search: the category after the name, 11.5px muted (.cat) */
            char c[80]; snprintf(c, sizeof c, "· %s", TOOL_CATS[tool_cat_of(tool)].title);
            Text ct = text_make(cr, FONT_SANS, 11.5, 400, c, 0, DIR_RTL);
            text_draw_r(cr, &ct, xr - 16 - ti.w - 6, ty + text_baseline(&ti) - text_baseline(&ct), t->muted);
            text_free(&ct);
        }
        if (tool->what[0]) text_draw_r(cr, &de, xr - 16, ty + ti.h, t->muted);
        text_free(&ti); text_free(&de);
        hit_add(a, r, HIT_TOOL_BASE + items[i]);
        y += pitch;
    }
    /* .tb-pager: "1–8 מתוך 16" at the start, the dots in the middle, what is left at the end;
     * the search's results have none while they fit on one page (mockup 14 page 2) */
    int pages = (n + per - 1) / per;
    if (with_cat && pages == 1) return;
    double py = y;
    char s1[64], s2[64];
    snprintf(s1, sizeof s1, "%d–%d מתוך %d", first + 1, last, n);
    if (last < n) snprintf(s2, sizeof s2, "עוד %d למטה · PgDn", n - last); else snprintf(s2, sizeof s2, "סוף הרשימה");
    Text a1 = text_make(cr, FONT_SANS, 12.5, 400, s1, 0, DIR_RTL), a2 = text_make(cr, FONT_SANS, 12.5, 400, s2, 0, DIR_RTL);
    text_draw_r(cr, &a1, xr - 4, py, t->muted);
    text_draw(cr, &a2, x0 + 4, py, t->muted);
    if (last < n) hit_add(a, (Rect){ x0, py - 4, a2.w + 8, a2.h + 8 }, HIT_TOOL_DOWN);
    double dots_w = 0;
    for (int p = 0; p < pages; p++) dots_w += (p == page ? 18 : 7) + (p ? 5 : 0);
    double dx = x0 + cw / 2 + dots_w / 2, dcy = py + a1.h / 2;
    for (int p = 0; p < pages; p++) {   /* the first page at the right */
        double w = p == page ? 18 : 7;
        Rect d = { dx - w, dcy - 3.5, w, 7 };
        draw_fill_rrect(cr, d, p == page ? 4 : 3.5, p == page ? t->indigo : t->hair);
        hit_add(a, (Rect){ d.x - 2, d.y - 6, d.w + 4, d.h + 12 }, HIT_TOOL_PAGE_BASE + p);
        dx -= w + 5;
    }
    text_free(&a1); text_free(&a2);
}

void tools_cat_view(App *a, cairo_t *cr, double W, double H, double head_h) {
    double x0, cw; tools_col(W, &x0, &cw, 0);
    int compact = H < 700, cat = a->tool_cat;
    double y = head_h + (compact ? 16 : 30), bottom = H - N_STATUS_H - (compact ? 10 : 16);
    int items[MAX_TOOLS], n = tools_cat_list(a, cat, items), ro, rw, de;
    mix_counts(a, items, n, &ro, &rw, &de);
    char cnt[48], mix[160], sub[400];
    count_words(cnt, sizeof cnt, n, cat == TCAT_COMBO); mix_sentence(mix, sizeof mix, ro, rw, de);
    snprintf(sub, sizeof sub, "כלים › %s · %s · %s: %s", TOOL_CATS[cat].title, TOOL_CATS[cat].situation, cnt, mix);
    y = tools_title(a, cr, x0, cw, y, NULL, TOOL_CATS[cat].title, sub, -1, HIT_TOOL_LIST) + (compact ? 12 : 20);
    if (!n) { grey_line(a, cr, x0, cw, y, "אין כלים בקטגוריה הזו."); return; }
    tools_list_draw(a, cr, x0, cw, y, bottom, items, n, 0);
}
