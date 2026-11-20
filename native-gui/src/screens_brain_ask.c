/* #3654 (mockup approved by Nadav 18/11, lab/design-ask-1118/native-ask.html, frames n1-n8): the brain's
 * question on this machine's screen. The amber frame and the full-width button with the sentence under it
 * are screens_ask.c's; the count (a mono number and a bar) is the boot choice's (screens_lcboot.c); the
 * header, the status bar and the tokens are the local clone's. Every number below is the mockup's CSS:
 *
 *   .ask      760 wide at most (100% - 64), 2px amber frame, 28 inside; .ask.cmp (a low screen) 20/24
 *   .over     the "שאלה מהמוח" pill and the machine's name, 8 under it the title (22/600), the detail (14)
 *   .cd       150 wide at the start of the top row, 18 from the title: mm:ss mono 22/600, "עד ברירת המחדל",
 *             a 6px bar that empties toward the left; 60 s and less in the warning colour
 *   .opt      52 high at least (cmp 36), 8 apart (cmp 6), 18 under the top (cmp 12); a number in a circle,
 *             the label (15/500) and its small line, the radio; the chosen one blue with its edge and ring;
 *             two columns when the server says so (`columns`), option 1 on the right
 *   .go       44 high (cmp 40), 20 under the options (cmp 14): "אישור", or the red verb of the destructive
 *             option chosen (verb_he); the hint 8 under it, 12px, centred
 *   .sum      after the question closed: the icon, what happened and when, the pill; "סגירה"; it closes
 *             by itself after 5 s
 *
 * The mockup's "CLONE-02 · מגירה 2 · WDC WD5000AAKX" is data a question about a machine does not carry
 * (entity = the machine; no drawer or model in the schema): the line names the machine, as the header does. */
#include <math.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include "brain_ask.h"
#include "he_time.h"
#include "screens_rounds.h"   /* RLM, bidi_isolate */

typedef struct { double pad_y, pad_x, top, gap, min_h, opt_y, opt_x, label, num, num_px, go_top, go_h; } Lay;
static const Lay FULL = { 28, 28, 18, 8, 52, 8, 14, 15, 26, 13, 20, 44 };
static const Lay CMP  = { 20, 24, 12, 6, 36, 5, 12, 14, 22, 12, 14, 40 };
#define BA_W_MAX 760.0
#define BA_EDGE  2.0
#define BA_CD_W  150.0
#define BA_CD_GAP 18.0

static double mono_now(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec / 1e9;
}

/* the count's clock: the wall clock; --png counts from the state's own updated= (renders do not differ by when
 * they ran -- the boot choice's rule, screens_lcboot.c lb_left) */
static long long wall_now(const App *a) {
    return a->fixed_clock && a->st.updated > 0 ? a->st.updated : (long long)time(NULL);
}

static double rad(const Theme *t) { return t == &THEME_DARK ? 3 : 4; }   /* the mockup's --rad */

static void hex(Rgb c, char out[8]) {
    snprintf(out, 8, "#%02X%02X%02X", (int)lround(c.r * 255), (int)lround(c.g * 255), (int)lround(c.b * 255));
}

static void clock_text(long long s, char *out, size_t n) {   /* "24:36"; an hour and more "1:02:05" */
    if (s < 0) s = 0;
    if (s >= 3600) snprintf(out, n, "%lld:%02lld:%02lld", s / 3600, s / 60 % 60, s % 60);
    else snprintf(out, n, "%02lld:%02lld", s / 60, s % 60);
}

static void at_text(long long epoch, char *out, size_t n) {   /* "14:31:07", isolated in the sentence */
    time_t w = (time_t)epoch; struct tm tm; char hms[16] = "--:--:--";
    if (epoch > 0 && localtime_r(&w, &tm)) strftime(hms, sizeof hms, "%H:%M:%S", &tm);
    bidi_isolate(hms, out, n);
}

static const BaskOpt *opt_of(const Bask *b, const char *id) {
    for (int i = 0; i < b->nopts; i++) if (!strcmp(b->opt[i].id, id)) return &b->opt[i];
    return &b->opt[0];
}

/* .pill: 22 high, 12/600, 10 inside; the dot at the start. Right edge in, width out. */
static double pill(cairo_t *cr, double right, double cy, const char *label, Rgb fg, Rgb bg, int dot) {
    Text v = text_make(cr, FONT_SANS, 12, 600, label, 0, DIR_RTL);
    double w = 10 + (dot ? 13 : 0) + v.w + 10;
    draw_fill_rrect(cr, (Rect){ right - w, cy - 11, w, 22 }, 11, bg);
    if (dot) { cairo_arc(cr, right - 13.5, cy, 3.5, 0, 2 * M_PI); draw_set(cr, fg); cairo_fill(cr); }
    text_draw_r(cr, &v, right - 10 - (dot ? 13 : 0), cy - v.h / 2.0, fg);
    text_free(&v);
    return w;
}

/* .btn.go: the whole width, the label and its "Enter" chip centred together */
static void button(App *a, cairo_t *cr, Rect b, const char *label, int kind, int id) {
    const Theme *t = a->theme; const int dark = t == &THEME_DARK, hov = hovered(a, b);
    Rgb bg = hov ? t->hover : t->surface, line = hov ? t->indigo : t->hair, fg = t->action_fg;
    if (kind == BTN_PRIMARY) { bg = line = hov ? t->ink_hover : t->indigo; fg = t->on_ink; }
    if (kind == BTN_DANGER) { bg = line = t->danger; fg = dark ? (Rgb){ .043, .067, .082 } : t->on_ink; }
    draw_fill_rrect(cr, b, rad(t), bg); draw_border_rrect(cr, b, rad(t), line, 1);
    Text l = text_make(cr, FONT_SANS, 14, kind == BTN_DANGER ? 600 : 500, label, 0, DIR_RTL);
    Text k = text_make(cr, FONT_MONO, 11, 500, "Enter", 0, DIR_LTR);
    double kw = k.w + 10, w = l.w + 8 + kw, right = b.x + (b.w + w) / 2;
    text_draw_r(cr, &l, right, b.y + (b.h - l.h) / 2, fg);
    Rect kr = { right - w, b.y + (b.h - 18) / 2, kw, 18 };
    if (kind == BTN_PLAIN) draw_set_a(cr, (Rgb){ 0, 0, 0 }, .08);
    else if (kind == BTN_DANGER && dark) draw_set_a(cr, (Rgb){ 0, 0, 0 }, .18);
    else draw_set_a(cr, (Rgb){ 1, 1, 1 }, .22);
    draw_rrect_path(cr, kr, 3); cairo_fill(cr);
    text_draw(cr, &k, kr.x + 5, kr.y + (18 - k.h) / 2, fg);
    text_free(&l); text_free(&k);
    hit_add(a, b, id);
}

/* .over: the pill and the machine (the header's name), 22 high */
static void over(App *a, cairo_t *cr, double right, double y) {
    const Theme *t = a->theme;
    double w = pill(cr, right, y + 11, "שאלה מהמוח", t->action_fg, t->indigo_soft, 0);
    if (!a->st.machine_name[0]) return;
    char nm[160]; bidi_isolate(a->st.machine_name, nm, sizeof nm);
    Text v = text_make(cr, FONT_SANS, 12, 400, nm, 0, DIR_RTL);
    text_draw_r(cr, &v, right - w - 8, y + 11 - v.h / 2.0, t->muted);
    text_free(&v);
}

/* .cd: what is left until the safe default. Height out; draws when <draw>. */
static double count(App *a, cairo_t *cr, double x, double y, const Bask *b, int draw) {
    const Theme *t = a->theme;
    long long left = b->deadline - wall_now(a);
    if (left < 0) left = 0;
    const int low = left <= 60;
    char s[24]; clock_text(left, s, sizeof s);
    Text n = text_make(cr, FONT_MONO, 22, 600, s, 0, DIR_LTR);
    Text l = text_make(cr, FONT_SANS, 12, low ? 600 : 400, "עד ברירת המחדל", 0, DIR_RTL);
    double h = 2 + n.h + l.h + 6 + 6;
    if (draw) {
        text_draw_r(cr, &n, x + BA_CD_W, y + 2, low ? t->warning_ink : t->ink);
        text_draw_r(cr, &l, x + BA_CD_W, y + 2 + n.h, low ? t->warning_ink : t->muted);
        double by = y + 2 + n.h + l.h + 6, share = b->ttl > 0 ? (double)left / (double)b->ttl : 0;
        if (share > 1) share = 1;
        draw_fill_rrect(cr, (Rect){ x, by, BA_CD_W, 6 }, 3, t->track);
        if (share > 0) draw_fill_rrect(cr, (Rect){ x, by, BA_CD_W * share, 6 }, 3, low ? t->warn : t->indigo);
    }
    text_free(&n); text_free(&l);
    return h;
}

/* one .opt: the number at the start, the label and its line, the radio at the end */
static void option(App *a, cairo_t *cr, Rect r, int i, const Lay *L, Text *lab, Text *sm, Rgb smc, int on) {
    const Theme *t = a->theme; const double R = rad(t);
    const int hov = hovered(a, r);
    draw_fill_rrect(cr, r, R, on ? t->indigo_soft : hov ? t->hover : t->surface);
    if (on) {   /* box-shadow: inset -4px 0 0 -- the edge at the start */
        cairo_save(cr); draw_rrect_path(cr, r, R); cairo_clip(cr);
        cairo_rectangle(cr, r.x + r.w - 5, r.y, 5, r.h); draw_set(cr, t->indigo); cairo_fill(cr);
        cairo_restore(cr);
    }
    draw_border_rrect(cr, r, R, on ? t->indigo : t->hair, 1);
    if (on) draw_border_rrect(cr, (Rect){ r.x - 3, r.y - 3, r.w + 6, r.h + 6 }, R + 3, t->indigo, 2);   /* .foc */
    double cy = r.y + r.h / 2, nx = r.x + r.w - 1 - L->opt_x - L->num / 2;
    cairo_arc(cr, nx, cy, L->num / 2, 0, 2 * M_PI); draw_set(cr, t->indigo_soft); cairo_fill(cr);
    char d[12]; snprintf(d, sizeof d, "%d", i + 1);
    Text k = text_make(cr, FONT_MONO, L->num_px, 600, d, 0, DIR_LTR);
    text_draw(cr, &k, nx - k.w / 2.0, cy - k.h / 2.0, t->action_fg); text_free(&k);
    double tr = r.x + r.w - 1 - L->opt_x - L->num - 12, ch = lab->h + (sm ? 1 + sm->h : 0), ty = cy - ch / 2;
    text_draw_r(cr, lab, tr, ty, t->ink);
    if (sm) text_draw_r(cr, sm, tr, ty + lab->h + 1, smc);
    double rx = r.x + 1 + L->opt_x + 9;
    cairo_arc(cr, rx, cy, 9, 0, 2 * M_PI); draw_set(cr, t->field); cairo_fill(cr);
    cairo_set_line_width(cr, 2); cairo_arc(cr, rx, cy, 8, 0, 2 * M_PI); draw_set(cr, on ? t->indigo : t->mark_line); cairo_stroke(cr);
    if (on) { cairo_arc(cr, rx, cy, 5, 0, 2 * M_PI); draw_set(cr, t->indigo); cairo_fill(cr); }
    hit_add(a, r, HIT_BASK_OPT_BASE + i);
}

/* the icons of .sum (24-unit strokes, 2.4 wide, in an 18px box) */
static void icon(cairo_t *cr, double cx, double cy, int kind, Rgb c) {
    const double s = 18.0 / 24.0, x0 = cx - 9, y0 = cy - 9;
    cairo_save(cr);
    cairo_set_line_width(cr, 2.4 * s); cairo_set_line_cap(cr, CAIRO_LINE_CAP_ROUND); cairo_set_line_join(cr, CAIRO_LINE_JOIN_ROUND);
    draw_set(cr, c);
    if (kind == 0) { cairo_move_to(cr, x0 + 5 * s, y0 + 12.5 * s); cairo_line_to(cr, x0 + 9.5 * s, y0 + 17 * s); cairo_line_to(cr, x0 + 19 * s, y0 + 7.5 * s); }
    else if (kind == 1) { cairo_move_to(cr, x0 + 12 * s, y0 + 11 * s); cairo_line_to(cr, x0 + 12 * s, y0 + 17 * s);
                          cairo_move_to(cr, x0 + 12 * s, y0 + 7.5 * s); cairo_line_to(cr, x0 + 12 * s, y0 + 7.51 * s); }
    else { cairo_new_sub_path(cr); cairo_arc(cr, cx, cy, 8 * s, 0, 2 * M_PI);
           cairo_move_to(cr, x0 + 12 * s, y0 + 8 * s); cairo_line_to(cr, x0 + 12 * s, y0 + 12.5 * s); cairo_line_to(cr, x0 + 15 * s, y0 + 14.3 * s); }
    cairo_stroke(cr);
    cairo_restore(cr);
}

static void draw_question(App *a, cairo_t *cr, const Bask *b, const Lay *L, double W, double usable, int cmp) {
    const Theme *t = a->theme;
    const double dw = fmin(BA_W_MAX, W - 64), inner = dw - 2 * (L->pad_x + BA_EDGE), txw = inner - BA_CD_W - BA_CD_GAP;
    const int n = b->nopts, two = b->columns == 2, rows = two ? (n + 1) / 2 : n;
    const BaskOpt *sel = &b->opt[a->bask_sel], *def = opt_of(b, b->safe);
    char buf[1400], e1[400], e2[400], ink[8];
    snprintf(buf, sizeof buf, RLM "%s", b->title);
    Text title = rtl_block_make(cr, 22, 600, buf, txw);
    Text lead = { 0 };
    const int has_lead = b->detail[0] && !cmp;
    if (has_lead) { snprintf(buf, sizeof buf, RLM "%s", b->detail); lead = rtl_block_make(cr, 14, 400, buf, txw); }
    double tx_h = 22 + 8 + title.h + (has_lead ? 8 + lead.h : 0), cd_h = count(a, cr, 0, 0, b, 0);
    double top_h = fmax(tx_h, cd_h);
    const double colw = two ? (inner - L->gap) / 2 : inner, tw = colw - 2 - 2 * L->opt_x - L->num - 12 - 12 - 18;
    Text lab[MAX_BASK_OPTS], sm[MAX_BASK_OPTS]; int has_sm[MAX_BASK_OPTS]; Rgb smc[MAX_BASK_OPTS];
    double row_h[MAX_BASK_OPTS] = { 0 }, opts_h = 0;
    for (int i = 0; i < n; i++) {
        const BaskOpt *o = &b->opt[i]; const int on = i == a->bask_sel;
        snprintf(buf, sizeof buf, RLM "%s", o->label);
        lab[i] = rtl_block_make(cr, L->label, 500, buf, tw);
        const char *line = o->destructive ? (on ? o->cons : "פעולה הרסנית — לא תתבצע בלי התשובה הזו")
                         : !strcmp(o->id, b->safe) ? "ברירת המחדל כשאין תשובה" : NULL;
        has_sm[i] = line != NULL; smc[i] = o->destructive && on ? t->danger : t->muted;
        if (line) { snprintf(buf, sizeof buf, RLM "%s", line); sm[i] = rtl_block_make(cr, 12, o->destructive && on ? 600 : 400, buf, tw); }
        double h = fmax(L->min_h, 2 + 2 * L->opt_y + lab[i].h + (line ? 1 + sm[i].h : 0));
        int r = two ? i / 2 : i;
        if (h > row_h[r]) row_h[r] = h;
    }
    for (int r = 0; r < rows; r++) opts_h += row_h[r] + (r ? L->gap : 0);
    /* the hint: the default by name, and when the count is low how soon */
    long long left = b->deadline - wall_now(a);
    if (left < 0) left = 0;
    hex(t->ink, ink);
    text_escape(def->label, e1, sizeof e1);
    const char *console = b->admin_too ? " אפשר לענות גם מהקונסולה." : "";
    if (left <= 60) {
        char secs[32]; he_secs(left, secs, sizeof secs); text_escape(secs, e2, sizeof e2);
        snprintf(buf, sizeof buf, "<span weight=\"600\" foreground=\"%s\">בעוד %s</span> תבוצע ברירת המחדל: "
                 "<span weight=\"600\" foreground=\"%s\">«%s»</span>.%s", ink, e2, ink, e1, console);
    } else snprintf(buf, sizeof buf, "בלי תשובה תבוצע ברירת המחדל: <span weight=\"600\" foreground=\"%s\">«%s»</span>.%s",
                    ink, e1, console);
    Text hint = text_make_markup(cr, FONT_SANS, 12, 400, buf, (int)inner, DIR_RTL);
    pango_layout_set_alignment(hint.layout, PANGO_ALIGN_CENTER);
    double dh = 2 * BA_EDGE + 2 * L->pad_y + top_h + L->top + opts_h + L->go_top + L->go_h + 8 + hint.h;
    if (dh > usable - 24) dh = usable - 24;   /* .ask max-height; n8 at 640x480 fits (cmp) */
    Rect box = { (W - dw) / 2, (usable - dh) / 2, dw, dh };
    draw_box_shadow(cr, box, rad(t), 24, 60, -12, (Rgb){ 0, 0, 0 }, .45);
    draw_fill_rrect(cr, box, rad(t), t->surface);
    draw_border_rrect(cr, box, rad(t), t->warn, BA_EDGE);
    cairo_save(cr); draw_rrect_path(cr, box, rad(t)); cairo_clip(cr);
    const double x0 = box.x + BA_EDGE + L->pad_x, xr = x0 + inner;
    double y = box.y + BA_EDGE + L->pad_y;
    over(a, cr, xr, y);
    text_draw_r(cr, &title, xr, y + 30, t->ink);
    if (has_lead) text_draw_r(cr, &lead, xr, y + 30 + title.h + 8, t->muted);
    count(a, cr, x0, y, b, 1);
    y += top_h + L->top;
    for (int r = 0, i = 0; r < rows; r++) {
        for (int c = 0; c < (two ? 2 : 1) && i < n; c++, i++) {
            Rect o = { c == 0 ? xr - colw : x0, y, colw, row_h[r] };
            option(a, cr, o, i, L, &lab[i], has_sm[i] ? &sm[i] : NULL, smc[i], i == a->bask_sel);
        }
        y += row_h[r] + L->gap;
    }
    y += L->go_top - L->gap;
    button(a, cr, (Rect){ x0, y, inner, L->go_h }, sel->destructive ? sel->verb : "אישור",
           sel->destructive ? BTN_DANGER : BTN_PRIMARY, HIT_BASK_GO);
    text_draw(cr, &hint, x0, y + L->go_h + 8, t->muted);
    cairo_restore(cr);
    for (int i = 0; i < n; i++) { text_free(&lab[i]); if (has_sm[i]) text_free(&sm[i]); }
    text_free(&title); if (has_lead) text_free(&lead); text_free(&hint);
}

static void draw_result(App *a, cairo_t *cr, const Bask *b, const Lay *L, double W, double usable) {
    const Theme *t = a->theme;
    const double dw = fmin(BA_W_MAX, W - 64), inner = dw - 2 * (L->pad_x + BA_EDGE);
    const int expired = b->state == BASK_EXPIRED, here = !expired && !strcmp(b->role, "machine");
    const BaskOpt *o = expired ? opt_of(b, b->safe) : opt_of(b, b->answer);
    char buf[1400], first[600], at[48], who[160], ttl[24], iso[48], out[440];
    at_text(b->closed_at, at, sizeof at);
    if (o->outcome[0]) snprintf(out, sizeof out, " %s.", o->outcome); else out[0] = 0;
    if (expired) {   /* n6: the server's clock ended it -- the safe default, which never erases */
        snprintf(first, sizeof first, "לא נענתה בזמן: בוצעה ברירת המחדל: «%s»", o->label);
        clock_text(b->ttl, ttl, sizeof ttl); bidi_isolate(ttl, iso, sizeof iso);
        snprintf(buf, sizeof buf, "עברו %s בלי תשובה (%s).%s לא בוצעה שום פעולה שמוחקת.", iso, at, out);
    } else if (here) {   /* n4: answered at this screen, as the server registered it */
        snprintf(first, sizeof first, "התשובה נרשמה: «%s»", o->label);
        snprintf(buf, sizeof buf, "נרשמה ב-%s.%s", at, out);
    } else {   /* n5: answered in the console, by whom */
        bidi_isolate(b->by[0] ? b->by : "מנהל", who, sizeof who);
        snprintf(first, sizeof first, "נענתה בקונסולה על ידי %s: «%s»", who, o->label);
        snprintf(buf, sizeof buf, "נענתה ב-%s.%s המסך ממשיך לפי התשובה.", at, out);
    }
    const Rgb soft = expired ? t->warning_soft : here ? t->success_soft : t->indigo_soft;
    const Rgb fg = expired ? t->warn : here ? t->led_ok : t->action_fg;
    double pw; { Text p = text_make(cr, FONT_SANS, 12, 600, expired ? "פקעה" : "נסגרה", 0, DIR_RTL); pw = 10 + 13 + p.w + 10; text_free(&p); }
    const double sw = inner - 2 - 32 - 34 - 12 - 12 - pw;   /* .sum: 16 inside, the icon, 12, the text, 12, the pill */
    Text s1 = rtl_block_make(cr, 15, 500, first, sw);
    Text sub = rtl_block_make(cr, 12, 400, buf, sw);
    const double sum_h = 2 + 28 + fmax(34, s1.h + 2 + sub.h);
    snprintf(buf, sizeof buf, RLM "%s", b->title);
    Text title = rtl_block_make(cr, 22, 600, buf, inner);
    long long close_in = (long long)ceil(BASK_RESULT_S - (mono_now() - a->bask_closed_at));
    if (close_in < 1) close_in = 1;
    if (close_in > BASK_RESULT_S) close_in = BASK_RESULT_S;
    char secs[32]; he_secs(close_in, secs, sizeof secs);
    const int round = a->screen == SCREEN_CLONER || (a->screen == SCREEN_ROOM && a->st.has_round);
    snprintf(buf, sizeof buf, "החלון נסגר לבד בעוד %s%s", secs, round ? ", והמסך חוזר לסבב" : "");
    Text hint = text_make(cr, FONT_SANS, 12, 400, buf, (int)inner, DIR_RTL);
    pango_layout_set_alignment(hint.layout, PANGO_ALIGN_CENTER);
    double dh = 2 * BA_EDGE + 2 * L->pad_y + 30 + title.h + 18 + sum_h + 20 + 44 + 8 + hint.h;
    if (dh > usable - 24) dh = usable - 24;
    Rect box = { (W - dw) / 2, (usable - dh) / 2, dw, dh };
    draw_box_shadow(cr, box, rad(t), 24, 60, -12, (Rgb){ 0, 0, 0 }, .45);
    draw_fill_rrect(cr, box, rad(t), t->surface);
    draw_border_rrect(cr, box, rad(t), t->warn, BA_EDGE);
    cairo_save(cr); draw_rrect_path(cr, box, rad(t)); cairo_clip(cr);
    const double x0 = box.x + BA_EDGE + L->pad_x, xr = x0 + inner;
    double y = box.y + BA_EDGE + L->pad_y;
    over(a, cr, xr, y);
    text_draw_r(cr, &title, xr, y + 30, t->ink);
    y += 30 + title.h + 18;
    Rect s = { x0, y, inner, sum_h };
    draw_fill_rrect(cr, s, rad(t), t->porcelain); draw_border_rrect(cr, s, rad(t), t->hair, 1);
    double cy = s.y + s.h / 2, ix = s.x + s.w - 1 - 16 - 17;
    cairo_arc(cr, ix, cy, 17, 0, 2 * M_PI); draw_set(cr, soft); cairo_fill(cr);
    icon(cr, ix, cy, expired ? 2 : here ? 0 : 1, fg);
    double tr = ix - 17 - 12, th = s1.h + 2 + sub.h;
    text_draw_r(cr, &s1, tr, cy - th / 2, t->ink);
    text_draw_r(cr, &sub, tr, cy - th / 2 + s1.h + 2, t->muted);
    pill(cr, s.x + 1 + 16 + pw, cy, expired ? "פקעה" : "נסגרה", fg, soft, 1);
    y += sum_h + 20;
    button(a, cr, (Rect){ x0, y, inner, 44 }, "סגירה", BTN_PLAIN, HIT_BASK_CLOSE);
    text_draw(cr, &hint, x0, y + 44 + 8, t->muted);
    cairo_restore(cr);
    text_free(&s1); text_free(&sub); text_free(&title); text_free(&hint);
}

void draw_brain_ask(App *a, cairo_t *cr, double W, double H) {
    if (!bask_visible(a)) return;
    const Bask *b = bask_current(a);
    const double usable = H - N_STATUS_H;
    cairo_rectangle(cr, 0, 0, W, usable);
    draw_set_a(cr, (Rgb){ 0, 0, 0 }, .55);
    cairo_fill(cr);
    a->nhits = 0; a->clip_on = 0;              /* modal: only the question is clickable */
    const int cmp = (b->nopts > 4 && usable < 470) || usable < 400;   /* the mockup's .cmp: a low screen */
    const Lay *L = cmp ? &CMP : &FULL;
    if (b->state == BASK_OPEN) { draw_question(a, cr, b, L, W, usable, cmp); return; }
    if (a->bask_closed_at <= 0) a->bask_closed_at = mono_now();   /* the 5 s start when the result is first seen */
    draw_result(a, cr, b, L, W, usable);
}

/* .sb .keys (mockup): a chip (mono 11/500, ink on porcelain, a hair border, 5 inside) and its word (12, muted) 5 to
 * its left, 16 between the pairs; the first pair at the right. Drawn from the bar's left end. */
void bask_draw_keys(App *a, cairo_t *cr, double x, double cy) {
    const Theme *t = a->theme; const Bask *b = bask_current(a);
    if (!b) return;
    char range[16]; snprintf(range, sizeof range, "1–%d", b->nopts);
    const char *k[3] = { "Enter", range, b->columns == 2 ? "↑↓←→" : "↑↓" };
    const char *w[3] = { b->state == BASK_OPEN ? "אישור" : "סגירה", "מספר", "בחירה" };
    for (int i = 0; i < (b->state == BASK_OPEN ? 3 : 1); i++) {
        Text v = text_make(cr, FONT_SANS, 12, 400, w[i], 0, DIR_RTL);
        Text c = text_make(cr, FONT_MONO, 11, 500, k[i], 0, DIR_LTR);
        text_draw(cr, &v, x, cy - v.h / 2.0, t->muted);
        x += v.w + 5;
        Rect r = { x, cy - 9, c.w + 10, 18 };
        draw_fill_rrect(cr, r, 3, t->porcelain); draw_border_rrect(cr, r, 3, t->hair, 1);
        text_draw(cr, &c, r.x + 5, cy - c.h / 2.0, t->ink);
        x += r.w + 16;
        text_free(&v); text_free(&c);
    }
}
