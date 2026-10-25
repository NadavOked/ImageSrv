/* #1322: "איך להפעיל את המחשב?" -- the cloner's boot choice, mockup state 1
 * (localclone.html .boot). Two cards: "מהשרת" (the default, a 20 s count
 * that the AGENT keeps -- this screen only draws its deadline) and "שיכפול
 * מקומי". Any touch stops the count (lc_state.c sends boot-pause).
 *
 * 16/10 (Nadav): a third choice, "שחזור אימג' מהשרת" (key 3), as a card
 * across the width under the two -- the two keep the mockup's place. It is
 * the same card (surface, hairline, the key chip, the indigo-soft icon
 * tile); only its icon is new, drawn from the other two's pieces (a server
 * over a disk, the arrow between). With one disk the local clone card stays
 * but is off: there is nothing to copy to (lc_local=0). */
#include <math.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include "lcui.h"
#include "screens_rounds.h"

#define LB_W      880.0   /* .choices width */
#define LB_GAP    18.0
#define LB_CARD_H 228.0   /* .ch min-height */
#define LB_PAD    26.0
#define LB_PULL_H 124.0   /* 16/10: the third card, across the width */

/* The two icons of the mockup, 24-unit SVG paths stroked at 28px; 2 = the
 * restore card's (16/10): a server above, a disk below, the arrow down. */
static void lb_icon(cairo_t *cr, Rect box, int local, Rgb c) {
    cairo_save(cr);
    cairo_translate(cr, box.x, box.y); cairo_scale(cr, box.w / 24.0, box.h / 24.0);
    cairo_set_line_width(cr, 1.7); cairo_set_line_cap(cr, CAIRO_LINE_CAP_ROUND); cairo_set_line_join(cr, CAIRO_LINE_JOIN_ROUND);
    draw_set(cr, c);
    if (local == 2) {
        draw_rrect_path(cr, (Rect){ 4, 2, 16, 6 }, 1.5); cairo_move_to(cr, 8, 5); cairo_line_to(cr, 8.01, 5);
        draw_rrect_path(cr, (Rect){ 3, 16, 18, 5 }, 1);
        cairo_move_to(cr, 12, 8); cairo_line_to(cr, 12, 15);
        cairo_move_to(cr, 9.5, 12.5); cairo_line_to(cr, 12, 15); cairo_line_to(cr, 14.5, 12.5);
    } else if (!local) {   /* two servers, their LEDs, the link */
        draw_rrect_path(cr, (Rect){ 4, 3, 16, 7 }, 1.5); draw_rrect_path(cr, (Rect){ 4, 14, 16, 7 }, 1.5);
        cairo_move_to(cr, 8, 6.5); cairo_line_to(cr, 8.01, 6.5); cairo_move_to(cr, 8, 17.5); cairo_line_to(cr, 8.01, 17.5);
        cairo_move_to(cr, 12, 10); cairo_line_to(cr, 12, 14);
    } else {        /* two disks, an arrow from the top one down to the other */
        draw_rrect_path(cr, (Rect){ 3, 4, 18, 5 }, 1); draw_rrect_path(cr, (Rect){ 3, 15, 18, 5 }, 1);
        cairo_move_to(cr, 12, 9); cairo_line_to(cr, 12, 15);
        cairo_move_to(cr, 9.5, 12.5); cairo_line_to(cr, 12, 15); cairo_line_to(cr, 14.5, 12.5);
    }
    cairo_stroke(cr); cairo_restore(cr);
}

/* What is left of the count, in seconds; --png counts from the state's own clock. */
static double lb_left(const App *a) {
    const LcState *l = &a->st.lc;
    if (l->boot_deadline <= 0) return 20;              /* the agent has not started it yet */
    double now;
    if (a->fixed_clock) now = (double)l->boot_now;
    else { struct timespec ts; clock_gettime(CLOCK_REALTIME, &ts); now = ts.tv_sec + ts.tv_nsec / 1e9; }
    double left = (double)l->boot_deadline - now;
    return left < 0 ? 0 : left > 20 ? 20 : left;
}

static void lb_card(App *a, cairo_t *cr, Rect r, int local, int paused, int focus) {
    const Theme *t = a->theme; int dark = t == &THEME_DARK;
    int off = local && !a->st.lc.local_ok;          /* 16/10: one disk -- nothing to copy to */
    draw_box_shadow(cr, r, 6, 10, 24, -16, dark ? (Rgb){ 0, 0, 0 } : t->header, dark ? .40 : .18);
    draw_fill_rrect(cr, r, 6, t->surface);
    draw_border_rrect(cr, r, 6, !local || (!off && hovered(a, r)) ? t->indigo : t->hair, 1);   /* .ch.def / :hover */
    if (focus) draw_border_rrect(cr, (Rect){ r.x - 3.5, r.y - 3.5, r.w + 7, r.h + 7 }, 9, t->indigo, 3);
    if (!off) hit_add(a, r, local ? HIT_LC_BOOT_LOCAL : HIT_LC_BOOT_SERVER);
    if (off) cairo_push_group(cr);
    /* .kk: the key, at the top-left corner */
    Text k = text_make(cr, FONT_MONO, 12, 500, local ? "2" : "1", 0, DIR_LTR);
    Rect kr = { r.x + 18, r.y + 18, k.w + 14, 22 };
    draw_fill_rrect(cr, kr, 3, t->field); draw_border_rrect(cr, kr, 3, t->hair, 1);
    text_draw(cr, &k, kr.x + 7, kr.y + (22 - k.h) / 2.0, t->ink); text_free(&k);
    double right = r.x + r.w - LB_PAD, inner = r.w - 2 * LB_PAD, y = r.y + LB_PAD;
    Rect ic = { right - 52, y, 52, 52 };
    draw_fill_rrect(cr, ic, 8, t->indigo_soft); lb_icon(cr, (Rect){ ic.x + 12, ic.y + 12, 28, 28 }, local, t->action_fg);
    y += 52 + 10;
    Text tt = text_make(cr, FONT_SANS, 22, 600, local ? "שיכפול מקומי" : "מהשרת", 0, DIR_RTL);
    text_draw_r(cr, &tt, right, y, t->ink);
    if (!local) sb_pill(cr, right - tt.w - 10, y + tt.h / 2.0, "ברירת מחדל", t->action_fg, t->indigo_soft);
    y += tt.h + 10; text_free(&tt);
    Text ds = text_make(cr, FONT_SANS, 14, 400, local ? "מעתיקים דיסק אחד שבמחשב לדיסקים האחרים שבו — בלי שרת."
                                                      : a->st.lc.boot_paused == 2 ? "המחשב חוזר להמתנה לסבב. כך הוא עובד בכל יום."   /* #2079 */
                                                      : "המחשב מתחבר לשרת ומחכה לסבב. כך הוא עובד בכל יום.", (int)inner, DIR_RTL);
    text_draw_r(cr, &ds, right, y, t->muted); text_free(&ds);
    double bottom = r.y + r.h - 22;
    if (local) {
        Text w = text_make(cr, FONT_SANS, 12, 500, off ? "במחשב יש דיסק אחד — אין לאן להעתיק."
                                                        : "הדיסקים שיוגדרו כיעד יימחקו. לפני כן יוצג אישור.", (int)inner, DIR_RTL);
        text_draw_r(cr, &w, right, bottom - w.h, off ? t->muted : t->warn); text_free(&w);
        if (off) { cairo_pop_group_to_source(cr); cairo_paint_with_alpha(cr, .5); }
        return;
    }
    if (a->st.lc.boot_paused == 2) return;   /* #2079 (mockup 02): back from the wait -- there is no count to show */
    /* .cd: the count -- the words, the seconds, the bar that empties toward the left */
    double left = lb_left(a), share = left / 20.0;
    if (paused) {
        if (a->lcu.frozen <= 0) a->lcu.frozen = share > 0 ? share : 0.001;
        share = a->lcu.frozen;
    }
    Rect bar = { r.x + LB_PAD, bottom - 6, inner, 6 };
    draw_fill_rrect(cr, bar, 3, t->track);
    draw_fill_rrect(cr, (Rect){ bar.x, bar.y, inner * share, 6 }, 3, paused ? t->mark_line : t->indigo);
    Text l1 = text_make(cr, FONT_SANS, 13, 400, paused ? "הספירה נעצרה" : "נבחר אוטומטית בעוד", 0, DIR_RTL);
    double row = bar.y - 7 - 26;
    text_draw_r(cr, &l1, right, row + 26 - l1.h - 2, t->muted); text_free(&l1);
    if (!paused) {
        char sec[16]; snprintf(sec, sizeof sec, "0:%02d", (int)ceil(left));
        Text n = text_make(cr, FONT_MONO, 22, 600, sec, 0, DIR_LTR);
        text_draw(cr, &n, r.x + LB_PAD, row + 26 - n.h, t->ink); text_free(&n);
    }
}

/* 16/10: "שחזור אימג' מהשרת" -- the same card, laid out across: the icon
 * tile at the start, the title, what it does and what it erases beside it. */
static void lb_pull_card(App *a, cairo_t *cr, Rect r, int focus) {
    const Theme *t = a->theme; int dark = t == &THEME_DARK;
    draw_box_shadow(cr, r, 6, 10, 24, -16, dark ? (Rgb){ 0, 0, 0 } : t->header, dark ? .40 : .18);
    draw_fill_rrect(cr, r, 6, t->surface);
    draw_border_rrect(cr, r, 6, hovered(a, r) ? t->indigo : t->hair, 1);
    if (focus) draw_border_rrect(cr, (Rect){ r.x - 3.5, r.y - 3.5, r.w + 7, r.h + 7 }, 9, t->indigo, 3);
    hit_add(a, r, HIT_LC_BOOT_PULL);
    Text k = text_make(cr, FONT_MONO, 12, 500, "3", 0, DIR_LTR);
    Rect kr = { r.x + 18, r.y + 18, k.w + 14, 22 };
    draw_fill_rrect(cr, kr, 3, t->field); draw_border_rrect(cr, kr, 3, t->hair, 1);
    text_draw(cr, &k, kr.x + 7, kr.y + (22 - k.h) / 2.0, t->ink); text_free(&k);
    double right = r.x + r.w - LB_PAD, y = r.y + LB_PAD;
    Rect ic = { right - 52, y, 52, 52 };
    draw_fill_rrect(cr, ic, 8, t->indigo_soft); lb_icon(cr, (Rect){ ic.x + 12, ic.y + 12, 28, 28 }, 2, t->action_fg);
    double tr = right - 52 - 18, inner = tr - (kr.x + kr.w + 18);
    Text tt = text_make(cr, FONT_SANS, 22, 600, "שחזור אימג' מהשרת", 0, DIR_RTL);
    text_draw_r(cr, &tt, tr, y - 2, t->ink); y += tt.h + 4; text_free(&tt);
    Text ds = text_make(cr, FONT_SANS, 14, 400, "בוחרים אימג' מספריית השרת ומגירות במחשב הזה. המחשב נכתב בסבב מהשרת, ונכבה בסוף.",
                        (int)inner, DIR_RTL);
    text_draw_r(cr, &ds, tr, y, t->muted); y += ds.h + 6; text_free(&ds);
    Text w = text_make(cr, FONT_SANS, 12, 500, "המגירות שיסומנו יימחקו. לפני כן יוצג אישור.", (int)inner, DIR_RTL);
    text_draw_r(cr, &w, tr, y, t->warn); text_free(&w);
}

void screen_lcboot(App *a, cairo_t *cr, double W, double H, double head_h) {
    const Theme *t = a->theme;
    int paused = a->st.lc.boot_paused || a->lcu.pause_sent;
    if (!paused) a->lcu.frozen = 0;
    double cw = fmin(LB_W, W - 48), x0 = (W - cw) / 2, cardw = (cw - LB_GAP) / 2;
    double total = 34.5 + 8 + 18.2 + 34 + LB_CARD_H + 20 + 20;
    double y = head_h + (H - N_STATUS_H - head_h - 20 - total) / 2;
    Text h1 = text_make(cr, FONT_SANS, 30, 600, "איך להפעיל את המחשב?", 0, DIR_RTL);
    text_draw(cr, &h1, (W - h1.w) / 2, y + (34.5 - h1.h) / 2, t->ink); y += 34.5 + 8; text_free(&h1);
    const char *rnote = lc_round_note(a);   /* #1367: a round opened on the server -- said in the lead's place */
    Text lead = text_make(cr, FONT_SANS, 14, rnote ? 600 : 400, rnote ? rnote : a->st.lc.boot_paused == 2 ? "חזרתם ממסך ההמתנה. בחרו אחת מהאפשרויות."   /* #2079 */
                                                         : paused ? "הספירה נעצרה. בחרו אחת מהאפשרויות."
                                                         : "אם לא תבחרו — המחשב יעבוד מהשרת, כרגיל.", 0, DIR_RTL);
    text_draw(cr, &lead, (W - lead.w) / 2, y + (18.2 - lead.h) / 2, rnote ? t->warn : t->muted); y += 18.2 + 34; text_free(&lead);
    lb_card(a, cr, (Rect){ x0 + cw - cardw, y, cardw, LB_CARD_H }, 0, paused, a->lcu.boot_focus == 0);
    lb_card(a, cr, (Rect){ x0, y, cardw, LB_CARD_H }, 1, paused, a->lcu.boot_focus == 1);
    y += LB_CARD_H + LB_GAP;
    lb_pull_card(a, cr, (Rect){ x0, y, cw, LB_PULL_H }, a->lcu.boot_focus == 2);   /* 16/10 */
    y += LB_PULL_H + 20;
    if (!paused) {
        Text note = text_make(cr, FONT_SANS, 13, 400, "כל מקש, לחיצה או תזוזת עכבר עוצרים את הספירה.", 0, DIR_RTL);
        text_draw(cr, &note, (W - note.w) / 2, y + (20 - note.h) / 2, t->muted); text_free(&note);
    }
}
