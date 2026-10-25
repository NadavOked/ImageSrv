/* One target row of the local clone screen (.tg .bay, screens_lcrun.c), as a
 * shared piece: the local clone draws its targets with it, and since Nadav's
 * 17/10 decision ("גרסה ב׳") the build machine's distribution screen and the
 * cloner's "שיכפול במקביל" draw their drawers with it too -- one look for
 * every screen that writes disks. Also the .met box and the hero (title on
 * the start side, the big number on the end side).
 *
 * The row: the drawer number (big) | the disk's name, size and model, a pill
 * | what is happening, the percent and an 8px bar | an LED. Narrow rows
 * (two machines side by side) shrink the number and LED columns and put the
 * pill under the name. */
#include <math.h>
#include <stdio.h>
#include <string.h>
#include "lcui.h"
#include "screens_rounds.h"

/* #1386: "בטל…" -- small red words with the drawer's key in a chip, no fill
 * (the mockup's .b.danger.xs). Drawn with its inline end at <right>, centred on
 * <cy>; registers its hit. Returns its width. */
static double bay_act(App *a, cairo_t *cr, double left, double cy, const LcBay *g) {
    const Theme *t = a->theme;
    Text l = text_make(cr, FONT_SANS, 12, 500, g->act, 0, DIR_RTL);
    Text k = text_make(cr, FONT_MONO, 11, 500, g->act_key ? g->act_key : "", 0, DIR_LTR);
    double kw = g->act_key ? k.w + 12 + 6 : 0, w = 9 + l.w + kw + 9, h = 28;
    Rect r = { left, cy - h / 2, w, h };
    if (hovered(a, r)) draw_fill_rrect(cr, r, t->radius_sm, t->danger_soft);
    text_draw_r(cr, &l, r.x + r.w - 9, cy - l.h / 2, t->danger_fg);
    if (g->act_key) {
        Rect kr = { r.x + 9, cy - 9, k.w + 12, 18 };
        draw_fill_rrect(cr, kr, 3, t->hover);
        text_draw(cr, &k, kr.x + 6, cy - k.h / 2, t->muted);
    }
    hit_add(a, r, g->act_hit);
    text_free(&l); text_free(&k);
    return w;
}

/* #1331 (mockup v2): the drivers line. Grey = placed or no package, orange =
 * not placed (the restore is done, #856), red = the storage driver of the
 * disk was not made boot-start -- the disk may not boot (0x7B).
 * Linux: lc_bay_linux below (drivers.linux, #1331 T8). */
void lc_bay_drivers(LcBay *y, const char *state, const char *detail, const char *boot,
                    const char *bsvc, const char *berr) {
    char pk[48], sv[32];
    y->drv[0] = 0; y->drv_tone = DRV_NONE;
    if (!state || !state[0]) return;
    snprintf(sv, sizeof sv, "%s%s", bsvc && bsvc[0] ? " " : "", bsvc ? bsvc : "");
    if (!strcmp(state, "staged")) {
        int n = detail[0] ? 1 : 0;
        for (const char *p = detail; (p = strstr(p, ", ")); p += 2) n++;
        if (n == 1) snprintf(pk, sizeof pk, "%s", "חבילה אחת"); else snprintf(pk, sizeof pk, "%d חבילות", n);
        if (boot && !strcmp(boot, "failed")) {
            snprintf(y->drv, sizeof y->drv, "דרייבר האחסון%s לא הוגדר לעלייה — %s · הדיסק עלול לא לעלות (0x7B) — לבדוק לפני שמחזירים לכיתה",
                     sv, berr && berr[0] ? berr : "סיבה לא ידועה");
            y->drv_tone = DRV_BAD;
        } else {
            snprintf(y->drv, sizeof y->drv, "דרייברים הונחו (%s): %s%s%s%s", pk, detail,
                     boot && !strcmp(boot, "enabled") ? " · דרייבר האחסון" : "", boot && !strcmp(boot, "enabled") ? sv : "",
                     boot && !strcmp(boot, "enabled") ? " הוגדר לעלייה" : "");
            y->drv_tone = DRV_OK;
        }
    } else if (!strcmp(state, "failed")) {
        snprintf(y->drv, sizeof y->drv, "דרייברים לא הונחו — %s · המחשב יעלה בלי הדרייברים של הדגם", detail[0] ? detail : "סיבה לא ידועה");
        y->drv_tone = DRV_WARN;
    } else if (!strcmp(detail, "no_model")) {
        snprintf(y->drv, sizeof y->drv, "%s", "דרייברים: לא נבחר דגם למגירה — לא הונחו"); y->drv_tone = DRV_MUTED;
    } else {
        snprintf(y->drv, sizeof y->drv, "דרייברים דולגו%s%s", detail[0] ? " — " : "",
                 !strcmp(detail, "not_windows") ? "האימג' אינו Windows" : detail);
        y->drv_tone = DRV_MUTED;
    }
    if (y->kind == BAY_DONE && y->drv_tone == DRV_BAD) y->pill = "דרייבר האחסון לא הוגדר";
    else if (y->kind == BAY_DONE && y->drv_tone == DRV_WARN) y->pill = "הושלם בלי דרייברים";
}

/* #1331 T8: drivers.linux (docs/interfaces.md section 4/17) -- what the check of the
 * initramfs of a Linux target found. Only "generic" is evidence the disk boots on
 * another bus (grey); hostonly / unsupported / failed are orange ("not checked" is
 * not "fine", principle 5); skipped with unknown_bus is said out loud. The same words
 * as linuxDriversText in console.js (tests/test_linux_drivers_line_1331.py). It
 * replaces the Windows line, which for a Linux image only says "not Windows". */
void lc_bay_linux(LcBay *y, const char *state, const char *module, const char *detail) {
    const char *why = detail && detail[0] ? detail : "סיבה לא ידועה";
    char mod[24] = "";
    if (!state || !state[0]) return;
    if (module && module[0]) snprintf(mod, sizeof mod, " (%s)", module);
    y->drv_tone = DRV_LWARN;
    if (!strcmp(state, "generic"))
        snprintf(y->drv, sizeof y->drv, "לינוקס: קובץ האתחול כולל את מודול הדיסק%s — נבדק, הדיסק אמור לעלות גם על בקר אחר", mod);
    else if (!strcmp(state, "hostonly"))
        snprintf(y->drv, sizeof y->drv, "לינוקס: קובץ האתחול כולל רק את בקר הדיסק של המחשב המקורי — עלול לא לעלות כאן");
    else if (!strcmp(state, "unsupported"))
        snprintf(y->drv, sizeof y->drv, "לינוקס: קובץ האתחול לא נבדק (%s) — לא ידוע אם הדיסק יעלה כאן", why);
    else if (!strcmp(state, "failed"))
        snprintf(y->drv, sizeof y->drv, "לינוקס: בדיקת קובץ האתחול נכשלה — %s · לא ידוע אם הדיסק יעלה כאן", why);
    else if (!strcmp(state, "skipped") && detail && !strcmp(detail, "unknown_bus"))
        snprintf(y->drv, sizeof y->drv, "לינוקס: סוג החיבור של הדיסק לא מוכר — קובץ האתחול לא נבדק");
    else snprintf(y->drv, sizeof y->drv, "לינוקס: בדיקת קובץ האתחול דולגה — %s", why);
    if (!strcmp(state, "generic") || !strcmp(state, "skipped")) y->drv_tone = DRV_LGREY;
}

void lc_bay_draw(App *a, cairo_t *cr, Rect b, const LcBay *g) {
    const Theme *t = a->theme;
    int dwarn = g->kind == BAY_DONE && g->done_warn;   /* #2009: done, orange -- drawn as the SMART question's orange */
    int bad = g->kind == BAY_FAILED, ok = g->kind == BAY_DONE && !dwarn, ask = g->kind == BAY_SMART || dwarn,
        off = g->kind == BAY_SKIPPED || g->kind == BAY_CANCELED, going = g->kind == BAY_CANCELING;   /* #1386 */
    int bootbad = g->drv_tone == DRV_BAD, drvwarn = g->drv_tone == DRV_WARN || g->drv_tone == DRV_LWARN;   /* #1331 */
    int toned = g->toned && (g->kind == BAY_WRITING || g->kind == BAY_WAITING);
    /* an orange/red tone (not the healthy green) also colours the bar */
    int warn = toned && !(g->tone.r == t->led_ok.r && g->tone.g == t->led_ok.g && g->tone.b == t->led_ok.b);
    const char *pill = g->pill ? g->pill : toned ? g->note : NULL;
    Rgb tsoft = { g->tone.r * .15 + t->surface.r * .85, g->tone.g * .15 + t->surface.g * .85, g->tone.b * .15 + t->surface.b * .85 };
    int narrow = b.w < 700;
    double numw = narrow ? 64 : 96, ledw = narrow ? 34 : 48, namew = narrow ? 150 : 220, gap = narrow ? 12 : 22;
    double cy = b.y + b.h / 2;
    const int hl = g->flag;   /* #2024 */
    /* #2050: amplify the existing status colours, including verified success.
     * Red > orange > green; a healthy waiting disk is not a verified result. */
    int health = g->toned && (toned || g->kind == BAY_DONE)
        && !(g->tone.r == t->led_ok.r && g->tone.g == t->led_ok.g && g->tone.b == t->led_ok.b);
    int red = bad || bootbad || hl == 2 || (health && g->tone.r == t->danger.r && g->tone.g == t->danger.g && g->tone.b == t->danger.b);
    int orange = ask || drvwarn || hl == 1 || health || (ok && g->line_warn);
    int emphasis = red || orange || ok;
    Rgb edge = red ? t->danger : orange ? t->warn : ok ? t->led_ok : t->hair;
    Rgb fill = red ? t->danger_soft : orange ? t->warning_soft : ok ? t->success_soft : t->surface;
    Rgb number = red ? t->danger_fg : orange ? t->warning_ink : ok ? t->success_ink : off ? t->muted : t->ink;
    cairo_save(cr); draw_rrect_path(cr, b, 4); cairo_clip(cr);
    draw_set(cr, fill); cairo_paint(cr);
    if (!emphasis) {
        cairo_rectangle(cr, b.x + b.w - numw, b.y, 1, b.h); cairo_rectangle(cr, b.x + ledw, b.y, 1, b.h); draw_set(cr, t->hair); cairo_fill(cr);
    }
    cairo_restore(cr); draw_border_rrect(cr, b, 4, edge, emphasis ? 3 : 1);

    /* the drawer number */
    char num[16]; if (g->drawer > 0) snprintf(num, sizeof num, "%d", g->drawer); else snprintf(num, sizeof num, "%s", "—");
    double big = narrow ? 30 : 40;
    Text nb = text_make(cr, FONT_MONO, big, 600, num, 0, DIR_LTR), nl = text_make(cr, FONT_SANS, 11, 400, "מגירה", 0, DIR_RTL);
    double nr = b.x + b.w - (narrow ? 14 : 18), ny = cy - (big + 6 + nl.h) / 2;
    text_draw_r(cr, &nb, nr, ny + (big - nb.h) / 2, number);
    text_draw_r(cr, &nl, nr, ny + big + 6, t->muted); text_free(&nb); text_free(&nl);

    /* the disk: name, size and model, and its pill */
    double fr = b.x + b.w - numw - 1 - gap, fl = b.x + ledw + 1 + gap;
    if (g->act && g->act_hit) fl += bay_act(a, cr, fl - gap + 6, cy, g) + gap;   /* #1386: the fifth column */
    Text tn = text_make(cr, FONT_SANS, narrow ? 15 : 18, 600, g->name, (int)namew, DIR_RTL);
    Text tsb = text_make(cr, FONT_SANS, 12, 400, g->sub, (int)namew, DIR_RTL);
    pango_layout_set_ellipsize(tsb.layout, PANGO_ELLIPSIZE_END); pango_layout_set_height(tsb.layout, 0);
    Rgb pfg = bad ? t->danger : bootbad ? t->danger_fg : drvwarn ? t->warning_ink : ok ? t->led_ok : ask || going ? t->warning_ink : toned ? g->tone : t->muted;
    Rgb pbg = bad || bootbad ? t->danger_soft : drvwarn ? t->warning_soft : ok ? t->success_soft : ask || going ? t->warning_soft : toned ? tsoft : t->hover;
    if (emphasis) { pfg = t->on_ink; pbg = edge; }
    /* #2030: the third line -- what is on the disk (before) or what was written (after). It needs
     * the row to be tall enough; a crowded cage (many rows squeezed) drops it rather than overlap. */
    Text tsu = { NULL, 0, 0 };
    if (g->used[0] && b.h >= 72) {
        tsu = text_make(cr, FONT_SANS, 12, 400, g->used, (int)namew, DIR_RTL);
        pango_layout_set_ellipsize(tsu.layout, PANGO_ELLIPSIZE_END); pango_layout_set_height(tsu.layout, 0);
    }
    double pill_h = pill && narrow ? (emphasis ? 30 : 24) : 0, used_h = tsu.layout ? 3 + tsu.h : 0;
    double bh = tn.h + 3 + tsb.h + used_h + pill_h;
    text_draw_r(cr, &tn, fr, cy - bh / 2, off ? t->muted : t->ink);
    text_draw_r(cr, &tsb, fr, cy - bh / 2 + tn.h + 3, t->muted);
    if (tsu.layout) { text_draw_r(cr, &tsu, fr, cy - bh / 2 + tn.h + 3 + tsb.h + 3, t->muted); text_free(&tsu); }
    if (pill && narrow) sb_bay_pill(cr, fr, cy - bh / 2 + tn.h + 3 + tsb.h + used_h + pill_h / 2, pill, pfg, pbg, emphasis);
    else if (pill) sb_bay_pill(cr, fr - tn.w - 10, cy - bh / 2 + tn.h / 2, pill, pfg, pbg, emphasis);
    text_free(&tn); text_free(&tsb);

    /* .prog: what is happening, the percent, the bar */
    double px = fl, pw = fr - namew - 16 - fl;
    Text tp = text_make(cr, FONT_MONO, 14, 600, g->pct, 0, DIR_LTR);
    Text tl = text_make(cr, FONT_SANS, 12, bad || ask || going || hl ? 600 : 400, g->line, (int)fmax(10, pw - tp.w - 10), DIR_RTL);
    pango_layout_set_ellipsize(tl.layout, PANGO_ELLIPSIZE_END); pango_layout_set_height(tl.layout, hl ? -2 : 0);
    if (hl) pango_layout_get_pixel_size(tl.layout, NULL, &tl.h);   /* #2024: the reason gets two lines */
    Text td = { NULL, 0, 0 };   /* #1331: the drivers line under the bar */
    if (g->drv[0]) {
        td = text_make(cr, FONT_SANS, 12, g->drv_tone == DRV_WARN || g->drv_tone == DRV_BAD || g->drv_tone == DRV_LWARN ? 600 : 400, g->drv, (int)fmax(10, pw), DIR_RTL);
        pango_layout_set_ellipsize(td.layout, PANGO_ELLIPSIZE_END); pango_layout_set_height(td.layout, 0);
        pango_layout_get_pixel_size(td.layout, NULL, &td.h);   /* one line now, not the wrapped height */
    }
    double ry = cy - (fmax(tl.h, tp.h) + 6 + 8 + (td.layout ? 6 + td.h : 0)) / 2;
    text_draw_r(cr, &tl, px + pw, ry, hl == 2 || bad ? t->danger_fg : hl || ask || going ? t->warning_ink : g->line_warn ? t->warn
                                      : g->kind == BAY_CANCELED ? t->ink : t->muted);
    text_draw(cr, &tp, px, ry, off ? t->muted : t->ink);
    Rect bar = { px, ry + fmax(tl.h, tp.h) + 6, pw, 8 };
    draw_fill_rrect(cr, bar, 4, t->track);
    double share = g->share < 0 ? 0 : fmin(1, g->share);
    if (share > 0) draw_fill_rrect(cr, (Rect){ bar.x + bar.w * (1 - share), bar.y, bar.w * share, 8 }, 4,
                                   bad ? t->danger : ok ? t->led_ok : dwarn ? t->warn : warn ? g->tone : t->led_write);
    if (td.layout) {
        Rgb dc = g->drv_tone == DRV_BAD ? t->danger_fg : g->drv_tone == DRV_WARN || g->drv_tone == DRV_LWARN ? t->warning_ink : g->drv_tone == DRV_OK ? t->ink : t->muted;
        text_draw_r(cr, &td, px + pw, bar.y + 8 + 6, dc); text_free(&td);
    }
    text_free(&tp); text_free(&tl);

    /* the LED */
    double gx = b.x + ledw / 2;
    Rgb led = bad || bootbad ? t->danger : drvwarn ? t->warn : ok ? t->led_ok : ask || going ? t->warn : off ? t->led_idle : toned ? g->tone : t->led_write;
    Rgb soft = bad || bootbad ? t->danger_soft : drvwarn ? t->warning_soft : ok ? t->success_soft : ask || going ? t->warning_soft : toned ? tsoft : t->indigo_soft;
    if (emphasis) { led = edge; soft = fill; }
    sb_led(cr, gx, cy - 15, 10, led, soft, !off, 1);
    for (int k = 0; k < 2; k++) draw_fill_rrect(cr, (Rect){ gx - 10, cy + 2 + k * 14, 20, 3 }, 1.5, t->mark_line);
}

/* .met: a metric box -- label, value, the small line under it */
void lc_met(App *a, cairo_t *cr, Rect r, const char *l, const char *v, const char *h, Rgb hc) {
    const Theme *t = a->theme;
    draw_fill_rrect(cr, r, 4, t->surface); draw_border_rrect(cr, r, 4, t->hair, 1);
    int latin = (unsigned char)v[0] < 0x80;   /* "163 MB/s", "118 / 149 GB": one left-to-right value */
    Text tl = text_make(cr, FONT_SANS, 12, 400, l, 0, DIR_RTL), tv = latin ? text_make_ltr(cr, FONT_SANS, 20, 600, v, 0) : text_make(cr, FONT_SANS, 20, 600, v, 0, DIR_RTL);
    double right = r.x + r.w - 14;
    text_draw_r(cr, &tl, right, r.y + 11, t->muted); text_draw_r(cr, &tv, right, r.y + 11 + tl.h + 1, t->ink);
    if (h && h[0]) { Text th = text_make(cr, FONT_SANS, 11, 400, h, (int)(r.w - 28), DIR_RTL); text_draw_r(cr, &th, right, r.y + 11 + tl.h + 1 + tv.h + 1, hc); text_free(&th); }
    text_free(&tl); text_free(&tv);
}

/* #2054: current states wrap inside the tile. Only the failed count is red. */
void lc_disks_met(App *a, cairo_t *cr, Rect r, const char *value, const char *hint) {
    const Theme *t = a->theme;
    draw_fill_rrect(cr, r, 4, t->surface); draw_border_rrect(cr, r, 4, t->indigo, 1);
    Text label = text_make(cr, FONT_SANS, 12, 400, "דיסקים", 0, DIR_RTL);
    Text v = text_make(cr, FONT_SANS, 19, 600, value, (int)(r.w - 28), DIR_RTL);
    PangoAttrList *attrs = pango_attr_list_new();
    const char *failed = strstr(value, "נכשל");
    if (failed) {
        const char *start = value;
        for (const char *p = value; (p = strstr(p, " · ")) && p < failed; p += strlen(" · ")) start = p + strlen(" · ");
        const char *end = strstr(failed, " · ");
        PangoAttribute *fg = pango_attr_foreground_new(t->danger_fg.r * 65535, t->danger_fg.g * 65535, t->danger_fg.b * 65535);
        fg->start_index = (unsigned)(start - value); fg->end_index = end ? (unsigned)(end - value) : (unsigned)strlen(value);
        pango_attr_list_insert(attrs, fg);
    }
    pango_layout_set_attributes(v.layout, attrs); pango_attr_list_unref(attrs);
    double right = r.x + r.w - 14, y = r.y + 11;
    text_draw_r(cr, &label, right, y, t->muted); y += label.h + 1;
    text_draw_r(cr, &v, right, y, t->ink); y += v.h + 1;
    Text h = text_make(cr, FONT_SANS, 11, 400, hint, (int)(r.w - 28), DIR_RTL);
    text_draw_r(cr, &h, right, y, t->muted);
    text_free(&label); text_free(&v); text_free(&h);
}

/* the hero of the local clone screen: a small label, the title and a line
 * under it at the start; the big number and the line under it at the end.
 * Returns its height. */
double lc_hero(App *a, cairo_t *cr, double x0, double xr, double y, const char *label, const char *h1,
               const char *s2, const char *big, const char *left, int alarm) {
    const Theme *t = a->theme; double cw = xr - x0;
    Text tlb = text_make(cr, FONT_SANS, 12, 500, label, (int)(cw - 260), DIR_RTL), th1 = text_make(cr, FONT_SANS, 26, 600, h1, (int)(cw - 260), DIR_RTL);
    Text ts2 = text_make(cr, FONT_SANS, 13, 400, s2, (int)(cw - 260), DIR_RTL);
    Text tb = text_make(cr, FONT_MONO, 64, 400, big, 0, DIR_LTR), tlt = text_make(cr, FONT_SANS, 13, 400, left, 0, DIR_RTL);
    double hh = fmax(tlb.h + 2 + th1.h + 3 + ts2.h, tb.h + tlt.h);
    double ty = y + hh - (tlb.h + 2 + th1.h + 3 + ts2.h);
    text_draw_r(cr, &tlb, xr, ty, t->muted); ty += tlb.h + 2; text_draw_r(cr, &th1, xr, ty, alarm ? t->danger_fg : t->ink); ty += th1.h + 3; text_draw_r(cr, &ts2, xr, ty, t->muted);
    text_draw(cr, &tb, x0, y + hh - tlt.h - tb.h, t->ink); text_draw(cr, &tlt, x0 + tb.w - tlt.w, y + hh - tlt.h, t->muted);
    text_free(&tlb); text_free(&th1); text_free(&ts2); text_free(&tb); text_free(&tlt);
    return hh;
}

/* a cage of bays with its head line ("יעדים" / "מחשב שיכפול 2" at the start,
 * the count at the end); the bays are drawn by the caller. Returns the y of
 * the first bay. */
double lc_cage(App *a, cairo_t *cr, Rect cage, const char *title, const char *count) {
    const Theme *t = a->theme;
    draw_fill_rrect(cr, cage, 6, t->surface); draw_border_rrect(cr, cage, 6, t->hair, 1);
    Text a1 = text_make(cr, FONT_SANS, 12, 400, title, 0, DIR_RTL), a2 = text_make(cr, FONT_SANS, 12, 400, count, 0, DIR_RTL);
    text_draw_r(cr, &a1, cage.x + cage.w - 14, cage.y + 10, t->muted); text_draw(cr, &a2, cage.x + 14, cage.y + 10, t->muted);
    text_free(&a1); text_free(&a2);
    return cage.y + 10 + 16 + 8;
}
