/* screens_failure.c -- #1569: the one failure screen of mockup PR #1550
 * (docs/design/native/failure-screen/failure-screen.html, approved 17/10).
 *
 * Four fixed regions, in every case the same: the title row (badge, title,
 * the English line under it, the "waiting" pill), the context strip, two
 * cards -- "מה קרה" with the target's own reason and one row per target, and
 * "מה אפשר לעשות": a sentence and the buttons that do it (#2068, mockup 09,
 * Nadav 24/10 -- no technical-details box; the log is in the console's drawer).
 * Enter is the first button (main.c). Every text comes from the state file
 * (agent/lib/failscreen.sh); nothing here is a sample. Numbers are the
 * mockup's CSS (.wrap 940, .badge 60, .ttl h1 30/600, .ctx 11/15, .cols
 * 1.12fr 1fr gap 14, .why 17/500, .steps 15, .det summary 44, .code 12). */
#include <math.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include "screens_rounds.h"

#define F_WRAP 940.0
_Static_assert(HIT_FAIL_BTN_LAST - HIT_FAIL_BTN + 1 == MAX_FAIL_BTNS, "one hit id per failure button (ui.h)");

/* The badge: a circle with an X (a failure), a triangle with a bar (the
 * server was not told), or a circle with an i (#1802, not a failure: the
 * unregistered machine) -- the mockup's 24-unit SVGs, stroked at 2.2. */
static void badge(cairo_t *cr, const Theme *t, Rect b, int warn, int info) {
    Rgb soft = info ? t->indigo_soft : warn ? t->warning_soft : t->danger_soft;
    Rgb line = info ? t->indigo : warn ? t->warn : t->danger;
    draw_fill_rrect(cr, b, 8, soft); draw_border_rrect(cr, b, 8, line, 1);
    cairo_save(cr);
    cairo_translate(cr, b.x + (b.w - 30) / 2, b.y + (b.h - 30) / 2); cairo_scale(cr, 30 / 24.0, 30 / 24.0);
    draw_set(cr, line); cairo_set_line_width(cr, 2.2); cairo_set_line_cap(cr, CAIRO_LINE_CAP_ROUND); cairo_set_line_join(cr, CAIRO_LINE_JOIN_ROUND);
    if (info) {
        cairo_arc(cr, 12, 12, 9.5, 0, 2 * M_PI); cairo_stroke(cr);
        cairo_move_to(cr, 12, 11); cairo_line_to(cr, 12, 16.5); cairo_stroke(cr);
        cairo_arc(cr, 12, 7.7, 1.2, 0, 2 * M_PI); cairo_fill(cr);
    } else if (warn) {
        cairo_move_to(cr, 12, 3.5); cairo_line_to(cr, 21.5, 20); cairo_line_to(cr, 2.5, 20); cairo_close_path(cr); cairo_stroke(cr);
        cairo_move_to(cr, 12, 10); cairo_line_to(cr, 12, 14.5); cairo_stroke(cr);
        cairo_arc(cr, 12, 17.3, 1.2, 0, 2 * M_PI); cairo_fill(cr);
    } else {
        cairo_arc(cr, 12, 12, 9.5, 0, 2 * M_PI); cairo_stroke(cr);
        cairo_move_to(cr, 8.5, 8.5); cairo_line_to(cr, 15.5, 15.5); cairo_move_to(cr, 15.5, 8.5); cairo_line_to(cr, 8.5, 15.5); cairo_stroke(cr);
    }
    cairo_restore(cr);
}

/* .safe svg: the shield with a tick, 16px, in the success colour */
static void shield(cairo_t *cr, const Theme *t, double x, double y) {
    cairo_save(cr); cairo_translate(cr, x, y); cairo_scale(cr, 16 / 24.0, 16 / 24.0);
    draw_set(cr, t->led_ok); cairo_set_line_width(cr, 2.4); cairo_set_line_cap(cr, CAIRO_LINE_CAP_ROUND); cairo_set_line_join(cr, CAIRO_LINE_JOIN_ROUND);
    cairo_move_to(cr, 12, 3); cairo_line_to(cr, 20, 6); cairo_line_to(cr, 20, 12);
    cairo_curve_to(cr, 20, 17, 16.5, 20, 12, 21); cairo_curve_to(cr, 7.5, 20, 4, 17, 4, 12); cairo_line_to(cr, 4, 6); cairo_close_path(cr); cairo_stroke(cr);
    cairo_move_to(cr, 8.5, 12); cairo_line_to(cr, 11, 14.5); cairo_line_to(cr, 15.5, 9.5); cairo_stroke(cr);
    cairo_restore(cr);
}

/* .ctx b.mono: a value of capitals, digits, : . - is drawn mono and LTR (a name, a MAC) */
static int mono_value(const char *v) {
    if (!v[0]) return 0;
    return strspn(v, "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789:.-") == strlen(v);
}

static int pill_kind(const char *k) {
    return !strcmp(k, "ok") ? PILL_OK : !strcmp(k, "wn") ? PILL_WARN : !strcmp(k, "bad") ? PILL_BAD : PILL_OFF;
}

const char *failure_status_word(const App *a, Rgb *dot) {
    const State *s = &a->st; const Theme *t = a->theme;
    if (standby_conn(s) == STANDBY_OFF) { *dot = t->warn; return "אין קשר עם השרת"; }   /* the hold's heartbeat is not heard */
    if (s->fail_at > 0) { *dot = t->indigo; return s->fail_info ? "עולה מהדיסק המקומי" : "מאתחל"; }
    if (s->fail_warn) { *dot = t->warn; return "ממתין לאישור השרת"; }
    *dot = t->danger; return "נכשל · המחשב נשאר דלוק";   /* #2068 */
}

/* .pill at the title row's far end: what the machine does now */
static void hold_pill(App *a, cairo_t *cr, double left, double y) {
    const State *s = &a->st; const Theme *t = a->theme; char buf[96];
    const char *label; int kind;
    if (s->fail_at > 0) {
        long long n = a->fixed_clock ? 10 : s->fail_at - (long long)time(NULL); if (n < 0) n = 0;
        snprintf(buf, sizeof buf, "%s %lld שניות", s->fail_info ? "עולה מהדיסק בעוד" : "מאתחל בעוד", n);
        label = buf; kind = PILL_INFO;
    } else if (s->fail_hold && s->end_poweroff_at <= 0) { label = "המחשב נשאר דלוק — לא ייכבה לבד"; kind = PILL_WARN; }   /* #2068 */   /* not while #1560 counts to power-off */
    else return;
    draw_pill(cr, t, left + pill_width(cr, label, 1), y, label, kind, 1);
}

/* #2068 .n-btn: the first is primary and carries its key chip (Enter) at its start, the
 * rest are plain. Returns the width; draws only when draw. */
static double fail_button(App *a, cairo_t *cr, double x, double y, const char *label, int primary, int i, int draw) {
    Text l;
    if (primary) {
        char e[160], mk[400], bg[8], fg[8]; Rgb b = a->theme->porcelain, f = a->theme->ink;
        snprintf(bg, sizeof bg, "#%02X%02X%02X", (int)(b.r * 255 + .5), (int)(b.g * 255 + .5), (int)(b.b * 255 + .5));
        snprintf(fg, sizeof fg, "#%02X%02X%02X", (int)(f.r * 255 + .5), (int)(f.g * 255 + .5), (int)(f.b * 255 + .5));
        text_escape(label, e, sizeof e);
        snprintf(mk, sizeof mk, "\xE2\x80\x8F" "<span font_family=\"" FONT_MONO_FAMILY "\" size=\"smaller\" background=\"%s\" foreground=\"%s\"> Enter </span>  %s", bg, fg, e);   /* RLM first: an RTL line, the chip at its start (right) */
        l = text_make_markup(cr, FONT_SANS, 14, 600, mk, 0, DIR_RTL);
    } else l = text_make(cr, FONT_SANS, 14, 600, label, 0, DIR_RTL);
    double w = btn_width(&l);
    if (draw) draw_btn(a, cr, (Rect){ x, y, w, N_BUTTON_H }, &l, primary ? BTN_PRIMARY : BTN_PLAIN, HIT_FAIL_BTN + i);
    text_free(&l);
    return w;
}

void screen_failure(App *a, cairo_t *cr, double W, double H, double head_h) {
    (void)H;   /* #2068: nothing is placed by the bottom any more -- the details box went */
    const Theme *t = a->theme; const State *s = &a->st;
    double ww = fmin(F_WRAP, W - 2 * N_PAD), x0 = (W - ww) / 2, xr = x0 + ww;
    double y = head_h + 26;
    Rgb tone = s->fail_info ? t->ink : s->fail_warn ? t->warn : t->danger;   /* .tone-inf .ttl h1: ink */

    /* ---- .ttl: badge · title + the English line · the pill ------------------------------ */
    Text h1 = rtl_block_make(cr, 30, 600, s->fail_title, ww - 60 - 18 - 300);
    Text sub = s->fail_sub[0] ? text_make(cr, FONT_SANS, 14, 400, s->fail_sub, (int)(ww - 60 - 18 - 300), DIR_RTL) : (Text){ NULL, 0, 0 };
    double th = h1.h + (sub.layout ? 5 + sub.h : 0), row = fmax(60, th);
    badge(cr, t, (Rect){ xr - 60, y + (row - 60) / 2, 60, 60 }, s->fail_warn, s->fail_info);
    text_draw_r(cr, &h1, xr - 60 - 18, y + (row - th) / 2, tone);
    if (sub.layout) { text_draw_r(cr, &sub, xr - 60 - 18, y + (row - th) / 2 + h1.h + 5, t->muted); text_free(&sub); }
    hold_pill(a, cr, x0, y + (row - 24) / 2);
    text_free(&h1);
    y += row + 20;

    /* ---- .ctx: the strip of labelled values ---------------------------------------------- */
    if (s->nfail_ctx) {
        double cw = ww / s->nfail_ctx, ch = 11 + 14 + 2 + 19 + 11;
        Rect card = { x0, y, ww, ch };
        draw_fill_rrect(cr, card, RADIUS_R, t->surface); draw_border_rrect(cr, card, RADIUS_R, t->hair, N_BORDER);
        for (int i = 0; i < s->nfail_ctx; i++) {
            double cr_x = xr - i * cw;   /* the cell's right edge; the first cell is at the right (RTL) */
            if (i) { cairo_rectangle(cr, cr_x, y + 1, 1, ch - 2); draw_set(cr, t->hair); cairo_fill(cr); }
            Text l = text_make(cr, FONT_SANS, 11, 400, s->fail_ctx[i].label, 0, DIR_RTL);
            int mono = mono_value(s->fail_ctx[i].value);
            Text v = text_make(cr, mono ? FONT_MONO : FONT_SANS, mono ? 14 : 15, 600, s->fail_ctx[i].value, (int)(cw - 32), mono ? DIR_LTR : DIR_RTL);
            pango_layout_set_ellipsize(v.layout, PANGO_ELLIPSIZE_END); pango_layout_set_height(v.layout, -1);
            pango_layout_set_alignment(v.layout, PANGO_ALIGN_RIGHT);
            text_draw_r(cr, &l, cr_x - 16, y + 11, t->muted);
            text_draw(cr, &v, cr_x - cw + 16, y + 11 + l.h + 2, t->ink);
            text_free(&l); text_free(&v);
        }
        y += ch + 14;
    }

    /* ---- .cols: "מה קרה" (right, 1.12fr) · "מה עושים עכשיו" (left, 1fr) ------------------ */
    double gap = 14, w1 = (ww - gap) * 1.12 / 2.12, w2 = ww - gap - w1, pad = 18, in1 = w1 - 2 * pad, in2 = w2 - 2 * pad;
    Text hA = text_make(cr, FONT_SANS, 12, 600, "מה קרה", 0, DIR_RTL), hB = text_make(cr, FONT_SANS, 12.5, 600, "מה אפשר לעשות", 0, DIR_RTL);
    Text why = rtl_block_make(cr, 17, 500, s->fail_why[0] ? s->fail_why : s->fail_title, in1);
    Text safe = s->fail_safe[0] ? rtl_block_make(cr, 13, 400, s->fail_safe, in1 - 24) : (Text){ NULL, 0, 0 };
    double hA_body = 16 + hA.h + 8 + why.h + (safe.layout ? 10 + fmax(safe.h, 16) : 0);
    /* .rows: one per target, measured first */
    Text rl[MAX_FAIL_ROWS], rs[MAX_FAIL_ROWS]; double rh[MAX_FAIL_ROWS], rows_h = 0;
    for (int i = 0; i < s->nfail_rows; i++) {
        double pw = pill_width(cr, s->fail_rows[i].pill, 0), tw = in1 - 24 - 26 - 12 - pw - 12;
        rl[i] = rtl_block_make(cr, 13, 600, s->fail_rows[i].label, tw);
        rs[i] = s->fail_rows[i].sub[0] ? rtl_block_make(cr, 12, 400, s->fail_rows[i].sub, tw) : (Text){ NULL, 0, 0 };
        rh[i] = 16 + fmax(26, rl[i].h + (rs[i].layout ? rs[i].h : 0)); rows_h += rh[i] + (i ? 1 : 0);
    }
    if (s->nfail_rows) hA_body += 12 + rows_h + 2;
    /* #2068 (.fx-box): the first line 14.5/1.5 ink, the rest 13 muted; then the buttons, 10 apart */
    Text st[MAX_FAIL_STEPS]; double steps_h = 0;
    for (int i = 0; i < s->nfail_steps; i++) {
        st[i] = rtl_block_make(cr, i ? 13 : 14.5, 400, s->fail_steps[i], in2);
        if (!i) pango_layout_set_line_spacing(st[i].layout, 1.3);
        steps_h += st[i].h + (i == s->nfail_steps - 1 ? 14 : 10);
    }
    double bt_w[MAX_FAIL_BTNS], bt_rows = 0, rx = 0;
    for (int i = 0; i < s->nfail_btns; i++) {
        bt_w[i] = fail_button(a, cr, 0, 0, s->fail_btns[i].label, i == 0, i, 0);
        if (!i || rx + 10 + bt_w[i] > in2) { bt_rows++; rx = bt_w[i]; } else rx += 10 + bt_w[i];
    }
    double hB_body = 16 + hB.h + 8 + steps_h + bt_rows * (N_BUTTON_H + 10);
    double cols_h = fmax(hA_body, hB_body) + 16;
    Rect cA = { xr - w1, y, w1, cols_h }, cB = { x0, y, w2, cols_h };
    draw_fill_rrect(cr, cA, RADIUS_R, t->surface); draw_border_rrect(cr, cA, RADIUS_R, t->hair, N_BORDER);
    draw_fill_rrect(cr, cB, RADIUS_R, t->surface); draw_border_rrect(cr, cB, RADIUS_R, t->hair, N_BORDER);
    double ya = y + 16, xa = xr - pad;
    text_draw_r(cr, &hA, xa, ya, t->muted); ya += hA.h + 8;
    text_draw_r(cr, &why, xa, ya, t->ink); ya += why.h;
    if (safe.layout) {
        ya += 10; shield(cr, t, xa - 16, ya + 2);
        text_draw_r(cr, &safe, xa - 24, ya, t->muted); ya += fmax(safe.h, 16); text_free(&safe);
    }
    if (s->nfail_rows) {
        ya += 12; Rect box = { cA.x + pad, ya, in1, rows_h + 2 };
        draw_fill_rrect(cr, box, RADIUS_SM, t->porcelain); draw_border_rrect(cr, box, RADIUS_SM, t->hair, N_BORDER);
        double ry = ya + 1;
        for (int i = 0; i < s->nfail_rows; i++) {
            if (i) { cairo_rectangle(cr, box.x + 1, ry, in1 - 2, 1); draw_set(cr, t->hair); cairo_fill(cr); ry += 1; }
            double cy = ry + rh[i] / 2; char n[8]; snprintf(n, sizeof n, "%d", i + 1);
            Rect nb = { xa - 12 - 26, cy - 13, 26, 26 };
            draw_fill_rrect(cr, nb, 4, t->surface); draw_border_rrect(cr, nb, 4, t->hair, N_BORDER);
            Text nt = text_make(cr, FONT_MONO, 13, 600, n, 0, DIR_LTR);
            text_draw(cr, &nt, nb.x + (26 - nt.w) / 2, nb.y + (26 - nt.h) / 2, t->ink); text_free(&nt);
            double tx = nb.x - 12, tt = rl[i].h + (rs[i].layout ? rs[i].h : 0), ty = cy - tt / 2;
            text_draw_r(cr, &rl[i], tx, ty, t->ink);
            if (rs[i].layout) { text_draw_r(cr, &rs[i], tx, ty + rl[i].h, t->muted); text_free(&rs[i]); }
            text_free(&rl[i]);
            draw_pill(cr, t, box.x + 12 + pill_width(cr, s->fail_rows[i].pill, 0), cy - 12, s->fail_rows[i].pill, pill_kind(s->fail_rows[i].kind), 0);
            ry += rh[i];
        }
    }
    double yb = y + 16, xb = cB.x + w2 - pad;
    text_draw_r(cr, &hB, xb, yb, t->muted); yb += hB.h + 8;
    for (int i = 0; i < s->nfail_steps; i++) {
        text_draw_r(cr, &st[i], xb, yb, i ? t->muted : t->ink);
        yb += st[i].h + (i == s->nfail_steps - 1 ? 14 : 10); text_free(&st[i]);
    }
    double bx = xb;   /* RTL: the first button at the right, the next to its left; a row that is full wraps */
    for (int i = 0; i < s->nfail_btns; i++) {
        if (i && bx - 10 - bt_w[i] < cB.x + pad) { bx = xb; yb += N_BUTTON_H + 10; }
        else if (i) bx -= 10;
        fail_button(a, cr, bx - bt_w[i], yb, s->fail_btns[i].label, i == 0, i, 1);
        bx -= bt_w[i];
    }
    text_free(&hA); text_free(&hB); text_free(&why);
}
