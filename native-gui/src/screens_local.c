/* #1322: "שיכפול מקומי" -- choosing the source and the targets (mockup
 * state 2, localclone.html .head/.cage/.bay/.role3) and the confirm dialog
 * (state 3: no typed word, the focus on "חזרה", the SMART warning inside,
 * what each target loses). The drawers are the standby screen's (#1304).
 *
 * Every target says whether it fits before anything is written (owner,
 * 13/10): "נכנס", "נכנס עם כיווץ זמני של המקור", "לא נכנס — חסרים N GB",
 * "בודק אם נכנס…", or "לא נבדק" -- and the last two are not a target. */
#include <math.h>
#include <stdio.h>
#include <string.h>
#include "lcui.h"
#include "screens_rounds.h"

#define LS_W     980.0
#define LS_BAY_H 120.0
#define LS_NUM_W 96.0
#define LS_GRIP  48.0

static void hexc(Rgb c, char *out) {
    snprintf(out, 8, "#%02X%02X%02X", (int)lround(c.r * 255), (int)lround(c.g * 255), (int)lround(c.b * 255));
}

/* ---- the shared pieces ----------------------------------------------------- */

double lc_btn_width(cairo_t *cr, const char *label, const char *key) {
    Text l = text_make(cr, FONT_SANS, 14, 500, label, 0, DIR_RTL);
    double w = 22 + l.w + 22; text_free(&l);
    if (key) { Text k = text_make(cr, FONT_MONO, 11, 500, key, 0, DIR_LTR); w += 8 + k.w + 10; text_free(&k); }
    return w;
}

/* .btn (40px, 0 22px, 14/500) with the .k chip; right edge in, width out. */
double lc_btn(App *a, cairo_t *cr, double right, double y, double h, const char *label, const char *key,
              int kind, int id, int focus) {
    const Theme *t = a->theme;
    double w = lc_btn_width(cr, label, key);
    Rect b = { right - w, y, w, h };
    int hov = kind != BTN_OFF && hovered(a, b);
    Rgb bg = t->surface, line = t->hair, fg = t->action_fg;
    if (kind == BTN_PRIMARY) { bg = hov ? t->ink_hover : t->indigo; line = bg; fg = t->on_ink; }
    else if (kind == BTN_DANGER) { bg = t->danger_fill; line = bg; fg = t == &THEME_DARK ? (Rgb){ .043, .067, .082 } : t->on_ink; }
    else if (kind == BTN_OFF) { bg = t->field; fg = t->muted; }
    else if (hov) { bg = t->hover; line = t->indigo; }
    draw_fill_rrect(cr, b, t->radius_sm, bg); draw_border_rrect(cr, b, t->radius_sm, line, 1);
    if (focus) draw_border_rrect(cr, (Rect){ b.x - 3, b.y - 3, b.w + 6, b.h + 6 }, t->radius_sm + 3, t->indigo, 2);
    Text l = text_make(cr, FONT_SANS, 14, kind == BTN_DANGER ? 600 : 500, label, 0, DIR_RTL);
    double x = b.x + b.w - 22;
    text_draw_r(cr, &l, x, y + (h - l.h) / 2, fg); x -= l.w + 8;
    if (key) {
        Text k = text_make(cr, FONT_MONO, 11, 500, key, 0, DIR_LTR);
        Rect kr = { x - k.w - 10, y + (h - 18) / 2, k.w + 10, 18 };
        draw_set_a(cr, kind == BTN_PRIMARY || kind == BTN_DANGER ? (Rgb){ 1, 1, 1 } : (Rgb){ 0, 0, 0 },
                   kind == BTN_PRIMARY || kind == BTN_DANGER ? .22 : .08);
        draw_rrect_path(cr, kr, 3); cairo_fill(cr);
        text_draw(cr, &k, kr.x + 5, kr.y + (18 - k.h) / 2, fg); text_free(&k);
    }
    text_free(&l);
    if (id && kind != BTN_OFF) hit_add(a, b, id);
    return w;
}

Rect lc_dialog_box(App *a, cairo_t *cr, double W, double H, double w, double h) {
    double usable = H - N_STATUS_H;
    cairo_rectangle(cr, 0, 0, W, usable); draw_set_a(cr, (Rgb){ 0, 0, 0 }, .55); cairo_fill(cr);
    a->nhits = 0; a->clip_on = 0;                   /* modal: only its own buttons */
    Rect box = { (W - w) / 2, (usable - h) / 2, w, h };
    draw_box_shadow(cr, box, 6, 24, 60, -12, (Rgb){ 0, 0, 0 }, .45);
    draw_fill_rrect(cr, box, 6, a->theme->surface); draw_border_rrect(cr, box, 6, a->theme->hair, 1);
    return box;
}

/* .dlg h2 with the round "!" and the .lead under it; *y in/out. */
void lc_dialog_head(App *a, cairo_t *cr, Rect box, const char *title, const char *lead, double *y) {
    const Theme *t = a->theme; double right = box.x + box.w - 28, inner = box.w - 56;
    Text h = text_make(cr, FONT_SANS, 22, 600, title, (int)(inner - 42), DIR_RTL);
    double cy = *y + fmax(30, h.h) / 2;
    cairo_arc(cr, right - 15, cy, 15, 0, 2 * M_PI); draw_set(cr, t->danger_soft); cairo_fill(cr);
    cairo_set_line_width(cr, 2.4); cairo_set_line_cap(cr, CAIRO_LINE_CAP_ROUND); draw_set(cr, t->danger);
    cairo_move_to(cr, right - 15, cy - 6); cairo_line_to(cr, right - 15, cy + 1);
    cairo_move_to(cr, right - 15, cy + 5.5); cairo_line_to(cr, right - 15, cy + 6); cairo_stroke(cr);
    text_draw_r(cr, &h, right - 42, cy - h.h / 2, t->ink); *y += fmax(30, h.h) + 6; text_free(&h);
    if (lead) {
        Text l = text_make(cr, FONT_SANS, 14, 400, lead, (int)(inner - 42), DIR_RTL);
        text_draw_r(cr, &l, right - 42, *y, t->muted); *y += l.h + 16; text_free(&l);
    }
}

/* ---- words ----------------------------------------------------------------------- */

static void sata_of(const LcDisk *d, char *out, size_t n) {
    conn_text(d->conn, d->port, d->dev, out, n);
}

static const char *smart_reason_he(const LcDisk *d) {
    if (!strcmp(d->smart, "failed_last"))
        return !strcmp(d->smart_reason, "cable") ? "נכשל בשיכפול הקודם — סיבה: כבל/חריץ"
             : !strcmp(d->smart_reason, "disk") ? "נכשל בשיכפול הקודם — סיבה: הדיסק"
             : !strcmp(d->smart_reason, "readonly") ? "נכשל בשיכפול הקודם — סיבה: הדיסק במצב קריאה בלבד"   /* #1742 */
             : "נכשל בשיכפול הקודם";
    if (!strcmp(d->smart_reason, "readonly")) return smart_reason_text(d->smart_reason);   /* #1742 */
    /* #1921 (Nadav 20/10): an orange says its sentence (the knowledge table's reason word); NVMe read-only too */
    if ((!strcmp(d->smart, "warn") || !strcmp(d->smart_reason, "media_readonly") || !strcmp(d->smart_reason, "health_unread")) && d->smart_reason[0])   /* #2078 */
        return smart_reason_text(d->smart_reason);
    if (smart_level(d->smart)) return "בדיקת הבריאות של הדיסק (SMART) נכשלה";
    return "";
}

static const char *smart_chip(const LcDisk *d, Rgb *fg, Rgb *bg, const Theme *t) {
    int lvl = smart_level(d->smart);
    if (lvl == 2) { *fg = t->danger; *bg = t->danger_soft;   /* #1580; #1742: read-only says so */
                    return !strcmp(d->smart_reason, "readonly") ? "קריאה בלבד" : "SMART תקול"; }
    if (lvl == 1) { *fg = t->warn; *bg = t->warning_soft; return strcmp(d->smart, "failed_last") ? "אזהרת SMART" : "נכשל בשיכפול הקודם"; }
    if (!strcmp(d->smart, "ok") || !strcmp(d->smart, "passed")) { *fg = t->led_ok; *bg = t->success_soft; return RLM "SMART תקין"; }
    *fg = t->muted; *bg = t->hover; return RLM "SMART לא נבדק";
}

/* #1937: the agent's reason may end a sentence of its own; the line adds ". לא ישמש כיעד." -- never ".." */
static const char *lc_no_dot(const char *s, char *out, size_t n) {
    snprintf(out, n, "%s", s);
    size_t k = strlen(out);
    while (k && (out[k - 1] == '.' || out[k - 1] == ' ')) out[--k] = 0;
    return out;
}

/* The verdict line of a disk that is not the source; color out. */
static void verdict_text(App *a, const LcDisk *d, const LcDisk *src, char *out, size_t n, Rgb *c) {
    const Theme *t = a->theme; const LcFit *f = lc_fit_of(a, d->dev);
    char need[48], have[48], miss[48], why[sizeof f->reason];
    if (f) lc_no_dot(f->reason, why, sizeof why);
    *c = t->muted;
    /* #1732 (Nadav 18/10): a red disk is a source or nothing -- "יעד" is grey (lc_target_ok) */
    if (smart_level(d->smart) == 2) { *c = t->danger; snprintf(out, n, "%s", red_refusal_he(d->smart_reason)); return; }   /* #1742: by the reason */
    if (!f || f->verdict == LCV_MEASURING) { snprintf(out, n, "בודק אם נכנס…"); return; }
    lc_gb(need, sizeof need, (double)f->need); lc_gb(have, sizeof have, (double)d->size);
    switch (f->verdict) {
    case LCV_FITS:
        *c = t->led_ok;
        if (src && (double)d->size - (double)src->need > 1073741824.0) snprintf(out, n, "נכנס · המחיצה האחרונה תוגדל לסוף הדיסק");
        else snprintf(out, n, "נכנס");
        return;
    case LCV_SHRINK:
        *c = t->warn; snprintf(out, n, "נכנס עם כיווץ זמני של המקור · צריך %s, יש %s", need, have); return;
    case LCV_SHORT: {
        *c = t->danger;
        /* #1354: too little room left in the system partition -- the agent says how much. */
        if (f->reason[0]) { snprintf(out, n, "%s. לא ישמש כיעד.", why); return; }
        long long gb = (long long)ceil(((double)f->need - (double)d->size) / 1e9);
        char g1[24]; snprintf(g1, sizeof g1, "%lld GB", gb < 1 ? 1 : gb); bidi_isolate(g1, miss, sizeof miss);
        snprintf(out, n, "לא נכנס — חסרים %s גם אחרי כיווץ המקור. לא ישמש כיעד.", miss); return; }
    default:
        *c = t->danger; snprintf(out, n, "לא נבדק אם נכנס%s%s. לא ישמש כיעד.", why[0] ? " — " : "", why); return;
    }
}

static int blocked(App *a, int i) {
    const LcFit *f = lc_fit_of(a, a->st.lc.disks[i].dev);
    return lc_role_of(a, a->st.lc.disks[i].dev) != LCR_SOURCE && f && (f->verdict == LCV_SHORT || f->verdict == LCV_UNCHECKED);
}

/* ---- one drawer ------------------------------------------------------------------- */

static double role3(App *a, cairo_t *cr, double x, double cy, int i) {
    const Theme *t = a->theme; const LcDisk *d = &a->st.lc.disks[i];
    static const char *lbl[3] = { "מקור", "יעד", "דלג" }; static const int rr[3] = { LCR_SOURCE, LCR_TARGET, LCR_SKIP };
    int cur = lc_role_of(a, d->dev); double bw[3], w = 2;
    Text tx[3];
    for (int k = 0; k < 3; k++) { tx[k] = text_make(cr, FONT_SANS, 13, cur == rr[k] ? 700 : 400, lbl[k], 0, DIR_RTL); bw[k] = fmax(58, tx[k].w + 26); w += bw[k]; }
    Rect box = { x, cy - 18, w, 36 };
    draw_fill_rrect(cr, box, t->radius_sm, t->field); draw_border_rrect(cr, box, t->radius_sm, t->hair, 1);
    double r = box.x + box.w - 1;
    for (int k = 0; k < 3; k++) {
        Rect b = { r - bw[k], box.y + 1, bw[k], 34 };
        int dis = (rr[k] == LCR_TARGET && !lc_target_ok(a, i)) || (rr[k] == LCR_SOURCE && !d->usable);
        Rgb fg = t->ink;
        if (cur == rr[k]) {
            if (rr[k] == LCR_SOURCE) { draw_fill_rrect(cr, b, 0, t->indigo); fg = (Rgb){ 1, 1, 1 }; }
            else if (rr[k] == LCR_TARGET) { draw_fill_rrect(cr, b, 0, t->indigo_soft); draw_border_rrect(cr, b, 0, t->indigo, 1); fg = t->action_fg; }
            else { draw_fill_rrect(cr, b, 0, t->hover); fg = t->muted; }
        }
        if (k) { cairo_rectangle(cr, b.x + b.w, box.y + 1, 1, 34); draw_set(cr, t->hair); cairo_fill(cr); }
        cairo_save(cr); if (dis) cairo_push_group(cr);
        text_draw(cr, &tx[k], b.x + (b.w - tx[k].w) / 2, b.y + (34 - tx[k].h) / 2, dis ? t->muted : fg);
        if (dis) { cairo_pop_group_to_source(cr); cairo_paint_with_alpha(cr, .45); }
        cairo_restore(cr);
        if (!dis) hit_add(a, b, HIT_LC_ROLE_BASE + i * 3 + rr[k]);
        r -= bw[k]; text_free(&tx[k]);
    }
    return w;
}

static void bay(App *a, cairo_t *cr, Rect b, int i, const LcDisk *src) {
    const Theme *t = a->theme; const LcDisk *d = &a->st.lc.disks[i];
    int role = lc_role_of(a, d->dev), blk = blocked(a, i), lvl = smart_level(d->smart), isrc = role == LCR_SOURCE;
    int inc = !strcmp(d->mark, "incomplete");   /* #1798: orange -- never a source, still a target */
    int focus = !a->lcu.confirm && a->lcu.focus == LCF_BAY && a->lcu.bay == i;
    Rgb edge = isrc ? t->indigo : lvl == 2 ? t->danger : lvl || inc ? t->warn : t->hair;
    double cy = b.y + b.h / 2;
    cairo_save(cr); draw_rrect_path(cr, b, 4); cairo_clip(cr);
    draw_set(cr, isrc ? t->indigo_soft : t->surface); cairo_paint(cr);
    if (blk) sb_hatch(cr, t, b);
    cairo_rectangle(cr, b.x + b.w - LS_NUM_W, b.y, 1, b.h); cairo_rectangle(cr, b.x + LS_GRIP, b.y, 1, b.h);
    draw_set(cr, isrc ? t->indigo : t->hair); cairo_fill(cr);
    if (isrc || lvl || inc) { double bw = isrc ? 5 : 4; cairo_rectangle(cr, b.x + b.w - bw, b.y, bw, b.h); draw_set(cr, edge); cairo_fill(cr); }
    cairo_restore(cr);
    draw_border_rrect(cr, b, 4, edge, 1);
    if (focus) draw_border_rrect(cr, (Rect){ b.x - 3.5, b.y - 3.5, b.w + 7, b.h + 7 }, 7, t->indigo, 3);
    hit_add(a, b, HIT_LC_BAY_BASE + i);
    double alpha = blk ? .8 : role == LCR_SKIP ? .55 : 1;
    cairo_push_group(cr);
    int drawer = eff_drawer(d->drawer, d->port);
    char num[16]; if (drawer > 0) snprintf(num, sizeof num, "%d", drawer); else snprintf(num, sizeof num, "%s", "—");
    Text nb = text_make(cr, FONT_MONO, 40, 600, num, 0, DIR_LTR), nl = text_make(cr, FONT_SANS, 11, 400, "מגירה", 0, DIR_RTL);
    double nr = b.x + b.w - 18, ny = cy - (40 + 6 + nl.h) / 2;
    text_draw_r(cr, &nb, nr, ny + (40 - nb.h) / 2, t->ink); text_draw_r(cr, &nl, nr, ny + 46, t->muted);
    text_free(&nb); text_free(&nl);
    /* .face, laid out from the left: the role buttons, the size, the SMART chip, then the name block */
    double fl = b.x + LS_GRIP + 1 + 22, fr = b.x + b.w - LS_NUM_W - 1 - 22;
    cairo_pop_group_to_source(cr); cairo_paint_with_alpha(cr, alpha);
    double rw = role3(a, cr, fl, cy, i);
    cairo_push_group(cr);
    char size[32]; disk_size(d->size, size, sizeof size);
    Text sz = text_make(cr, FONT_MONO, 20, 600, size, 0, DIR_LTR);
    Text md = text_make(cr, FONT_MONO, 12, 400, d->model[0] ? d->model : "—", 0, DIR_LTR);   /* #2030: never the device name */
    double mx = fl + rw + 18, mw = fmax(sz.w, md.w);
    text_draw(cr, &sz, mx, cy - (sz.h + 2 + md.h) / 2, t->ink); text_draw(cr, &md, mx, cy - (sz.h + 2 + md.h) / 2 + sz.h + 2, t->muted);
    text_free(&sz); text_free(&md);
    double wl = mx + mw + 18;
    { Rgb fg, bg; const char *chip = smart_chip(d, &fg, &bg, t);   /* #2017 (Nadav 23/10): the source row too -- the same colours and words */
        Text pv = text_make(cr, FONT_SANS, 12, 600, chip, 0, DIR_RTL); double pw = 10 + 7 + 6 + pv.w + 10; text_free(&pv);
        sb_pill(cr, wl + pw, cy, chip, fg, bg); wl += pw + 18; }
    double ww = fr - wl;
    /* .who */
    char name[LC_LABEL_MAX], has[200], l2[400], l3[300], mk[900], e1[200], e2[400], col[8];
    lc_disk_card_name(d, name, sizeof name); lc_has_text(d, has, sizeof has);   /* #2030: the name IS the connection, so the line under it no longer says it again */
    Text tn = text_make(cr, FONT_SANS, 18, 600, name, 0, DIR_RTL);
    const char *tag = isrc ? "מקור" : role == LCR_TARGET ? "יעד" : NULL;
    if (lvl && !isrc) { hexc(lvl == 2 ? t->danger : t->warn, col); text_escape(smart_reason_he(d), e1, sizeof e1);
        snprintf(mk, sizeof mk, "<span foreground='%s'>%s</span>", col, e1); }
    else mk[0] = 0;
    if (d->hw[0]) {   /* #1921: the drawer's hardware note -- the cable, the drawer, USB 2.0; not the disk's colour */
        char eh[500]; size_t n = strlen(mk); hexc(t->warn, col); text_escape(d->hw, eh, sizeof eh);
        snprintf(mk + n, sizeof mk - n, "%s<span foreground='%s'>%s</span>", n ? " · " : "", col, eh);
    }
    Text t1 = mk[0] ? text_make_markup(cr, FONT_SANS, 12, 400, mk, (int)ww, DIR_RTL) : (Text){ NULL, 0, 0 };
    Rgb c3 = t->muted; l3[0] = 0;
    if (isrc) snprintf(l2, sizeof l2, "%s · יועתק כמו שהוא", has);
    else if (blk) { verdict_text(a, d, src, l2, sizeof l2, &c3); }
    else if (role == LCR_SKIP) { snprintf(l2, sizeof l2, "לא ישתנה"); verdict_text(a, d, src, l3, sizeof l3, &c3); }
    else { snprintf(l2, sizeof l2, "עליו עכשיו: %s — ", has); verdict_text(a, d, src, l3, sizeof l3, &c3); }
    Text t2;
    if (!isrc && !blk && role == LCR_TARGET) { hexc(t->danger_fg, col); text_escape(l2, e2, sizeof e2);
        snprintf(mk, sizeof mk, "%s<span foreground='%s' weight='bold'>יימחק</span>", e2, col);
        t2 = text_make_markup(cr, FONT_SANS, 12, 400, mk, (int)ww, DIR_RTL); }
    else t2 = text_make(cr, FONT_SANS, 12, blk ? 600 : 400, l2, (int)ww, DIR_RTL);
    Text t3 = l3[0] ? text_make(cr, FONT_SANS, 12, 500, l3, (int)ww, DIR_RTL) : (Text){ NULL, 0, 0 };
    double bh = tn.h + 3 + (t1.layout ? t1.h + 3 : 0) + t2.h + (l3[0] ? 3 + t3.h : 0), by = cy - bh / 2;
    text_draw_r(cr, &tn, fr, by, t->ink);
    if (tag) {
        Text tg = text_make(cr, FONT_SANS, 12, 700, tag, 0, DIR_RTL);
        Rect tr = { fr - tn.w - 10 - tg.w - 18, by + (tn.h - 22) / 2, tg.w + 18, 22 };
        draw_fill_rrect(cr, tr, 11, isrc ? t->indigo : t->hover); if (!isrc) draw_border_rrect(cr, tr, 11, t->hair, 1);
        text_draw(cr, &tg, tr.x + 9, tr.y + (22 - tg.h) / 2, isrc ? (Rgb){ 1, 1, 1 } : t->ink); text_free(&tg);
    }
    by += tn.h + 3; if (t1.layout) { text_draw_r(cr, &t1, fr, by, t->muted); by += t1.h + 3; }
    text_draw_r(cr, &t2, fr, by, blk ? c3 : inc ? t->warn : t->muted); by += t2.h + 3;
    if (l3[0]) { text_draw_r(cr, &t3, fr, by, c3); text_free(&t3); }
    text_free(&tn); text_free(&t1); text_free(&t2);
    /* .grip */
    double gx = b.x + LS_GRIP / 2;
    Rgb led = isrc ? t->led_write : blk ? t->danger : lvl == 2 ? t->danger : lvl ? t->warn : t->led_ok;
    Rgb soft = isrc ? t->indigo_soft : blk || lvl == 2 ? t->danger_soft : lvl ? t->warning_soft : t->success_soft;
    sb_led(cr, gx, cy - 15, 10, led, soft, 1, 1);
    for (int k = 0; k < 2; k++) draw_fill_rrect(cr, (Rect){ gx - 10, cy + 2 + k * 14, 20, 3 }, 1.5, t->mark_line);
    cairo_pop_group_to_source(cr); cairo_paint_with_alpha(cr, alpha);
}

/* ---- the source screen --------------------------------------------------------------- */

void lc_draw_source(App *a, cairo_t *cr, double W, double H, double head_h) {
    const Theme *t = a->theme; const LcState *l = &a->st.lc;
    double cw = fmin(LS_W, W - 48), x0 = (W - cw) / 2, xr = x0 + cw, y = head_h + 22;
    int si = lc_source_index(a), idx[LC_MAX_DISKS], nt = lc_targets(a, idx, LC_MAX_DISKS);
    const LcDisk *src = si >= 0 ? &l->disks[si] : NULL;
    Text h1 = text_make(cr, FONT_SANS, 26, 600, "שיכפול מקומי", 0, DIR_RTL);
    const char *rnote = lc_round_note(a);   /* #1367: a round opened on the server -- said in the lead's place */
    Text p = text_make(cr, FONT_SANS, 14, rnote ? 600 : 400, rnote ? rnote : "בחרו איזה דיסק הוא המקור. היעדים יקבלו עותק זהה שלו — כל מה שעליהם יימחק.", (int)(cw - 330), DIR_RTL);
    char s1[400] = "", s2[400], sl[LC_LABEL_MAX];
    if (src && nt) {
        lc_disk_label(src, sl, sizeof sl); int k = snprintf(s1, sizeof s1, "מ%s אל ", sl);
        for (int i = 0; i < nt && k > 0 && (size_t)k < sizeof s1; i++) {
            char tl[LC_LABEL_MAX]; lc_disk_label(&l->disks[idx[i]], tl, sizeof tl);
            k += snprintf(s1 + k, sizeof s1 - k, "%s%s", i ? (i == nt - 1 ? " ו" : ", ") : "", tl);
        }
        char c[300]; lc_copy_text(l, src, c, sizeof c);   /* #1443: measured before the start, not df */
        snprintf(s2, sizeof s2, "%s%s", c, lc_any_shrink(a) ? " · כיווץ על עותק בזיכרון" : "");
    } else if (!src) { snprintf(s1, sizeof s1, "אין מקור"); snprintf(s2, sizeof s2, "בחרו את דיסק המקור"); }   /* #1722: nothing chosen for you */
    else { snprintf(s1, sizeof s1, "אין יעד"); snprintf(s2, sizeof s2, "בחרו לפחות יעד אחד"); }
    Text m1 = text_make(cr, FONT_SANS, 12, 400, s1, 0, DIR_RTL), m2 = text_make(cr, FONT_SANS, 16, 600, s2, 0, DIR_RTL);
    /* #1914 (Nadav 20/10, measured 60-80 vs 134 MB/s), under the lead. #1937 (21/10: "he did not
     * notice it"): an orange banner -- the screen's own warning box (the shrink note of the
     * confirm, a warned drawer): warning_soft, a warn line, the led, ink text. No new design. */
    int ide = l->usb && l->ide;
    Text pi = text_make(cr, FONT_SANS, 14, 600, ide ? "הבקר במצב IDE — שינוי ל-AHCI ב-BIOS יכפיל בערך את המהירות" : " ", (int)(cw - 330 - 44), DIR_RTL);
    double ibh = pi.h + 20;
    /* #1914/#1921 (Nadav 20/10): the survey stick says what it keeps, under the lead */
    int sv = l->usb && l->survey;
    Text ps = text_make(cr, FONT_SANS, 13, 400, sv ? "הדיסק-און-קי הזה שומר נתוני SMART של כל דיסק (בלי מספר סידורי)" : " ", (int)(cw - 330), DIR_RTL);
    /* Nadav 22/10: the source's SMART already counts sectors it could not read -- the same orange
     * banner, above the IDE one. Information only: no question, nothing blocked. */
    char ut[320]; int un = lc_unread_text(a, ut, sizeof ut);
    Text pu = text_make(cr, FONT_SANS, 14, 600, un ? ut : " ", (int)(cw - 330 - 44), DIR_RTL);
    double ubh = pu.h + 20, ib = ide ? 8 + ibh : 0;
    double sh = sv ? 4 + ps.h : 0, ih = ib + (un ? 8 + ubh : 0) + sh;
    double hh = fmax(h1.h + 4 + p.h + ih, m1.h + m2.h);   /* mockup 01 (Nadav 24/10): "הגדרות" is in the top bar now (screens.c) */
    text_draw_r(cr, &h1, xr, y + hh - (h1.h + 4 + p.h + ih), t->ink); text_draw_r(cr, &p, xr, y + hh - p.h - ih, rnote ? t->warn : t->muted);
    if (ide) {
        Rect ar = { xr - (cw - 330), y + hh - ibh - sh, cw - 330, ibh };
        draw_fill_rrect(cr, ar, 3, t->warning_soft); draw_border_rrect(cr, ar, 3, t->warn, 1);
        sb_led(cr, ar.x + ar.w - 16, ar.y + 10 + 8, 10, t->warn, t->warning_soft, 1, 1);
        text_draw_r(cr, &pi, ar.x + ar.w - 32, ar.y + 10, t->ink);
    }
    if (un) {
        Rect ur = { xr - (cw - 330), y + hh - ubh - ib - sh, cw - 330, ubh };
        draw_fill_rrect(cr, ur, 3, t->warning_soft); draw_border_rrect(cr, ur, 3, t->warn, 1);
        sb_led(cr, ur.x + ur.w - 16, ur.y + 10 + 8, 10, t->warn, t->warning_soft, 1, 1);
        text_draw_r(cr, &pu, ur.x + ur.w - 32, ur.y + 10, t->ink);
    }
    if (sv) text_draw_r(cr, &ps, xr, y + hh - ps.h, t->muted);
    text_free(&pi); text_free(&ps); text_free(&pu);
    text_draw(cr, &m1, x0, y + hh - m1.h - m2.h, t->muted); text_draw(cr, &m2, x0, y + hh - m2.h, t->ink);
    text_free(&h1); text_free(&p); text_free(&m1); text_free(&m2);
    y += hh + 16;
    /* .cage */
    int n = l->ndisks; double foot = 16 + 40, chrome = 14 + 16 + 10 + 14;
    double bay_h = LS_BAY_H, room = H - N_STATUS_H - 16 - y - foot - chrome - (n > 1 ? (n - 1) * 10 : 0);
    if (n && n * bay_h > room) bay_h = fmax(76, floor(room / n));
    Rect cage = { x0, y, cw, chrome + (n ? n * bay_h + (n - 1) * 10 : 120) };
    int dark = t == &THEME_DARK;
    draw_box_shadow(cr, cage, 6, 10, 24, -14, dark ? (Rgb){ 0, 0, 0 } : t->header, dark ? .40 : .14);
    draw_fill_rrect(cr, cage, 6, t->surface); draw_border_rrect(cr, cage, 6, t->hair, 1);
    char cnt[64]; snprintf(cnt, sizeof cnt, "%s · מקור אחד", nt == 1 ? "יעד אחד" : nt ? "" : "אין יעד");
    if (nt > 1) snprintf(cnt, sizeof cnt, "%d יעדים · מקור אחד", nt);
    Text lr = text_make(cr, FONT_SANS, 12, 400, "מגירות הדיסקים במחשב — מלמעלה למטה", 0, DIR_RTL), ll = text_make(cr, FONT_SANS, 12, 400, cnt, 0, DIR_RTL);
    text_draw_r(cr, &lr, xr - 18, y + 14, t->muted); text_draw(cr, &ll, x0 + 18, y + 14, t->muted); text_free(&lr); text_free(&ll);
    double by = y + 14 + 16 + 10;
    if (!l->inv_done || !n) {
        Text w = text_make(cr, FONT_SANS, 15, 400, l->inv_done ? "לא נמצאו דיסקים במחשב" : "בודק את הדיסקים במחשב…", 0, DIR_RTL);
        text_draw(cr, &w, x0 + (cw - w.w) / 2, by + 50, t->muted); text_free(&w);
        if (l->inv_done && l->usb && l->diag[0]) {   /* #1914 (college 20/10): something to send back */
            char dm[160]; snprintf(dm, sizeof dm, "דוח אבחון נשמר על הדיסק-און-קי (\xE2\x81\xA8%s\xE2\x81\xA9)", l->diag);
            Text dt = text_make(cr, FONT_SANS, 13, 400, dm, 0, DIR_RTL);
            text_draw(cr, &dt, x0 + (cw - dt.w) / 2, by + 50 + 26, t->muted); text_free(&dt);
        }
    }
    for (int i = 0; l->inv_done && i < n; i++) { bay(a, cr, (Rect){ x0 + 14, by, cw - 28, bay_h }, i, src); by += bay_h + 10; }
    y = cage.y + cage.h + 16;
    /* .foot: the message at the right, "המשך לאישור" at the far left */
    int can = lc_can_go(a); double bx = x0;
    bx += lc_btn(a, cr, bx + lc_btn_width(cr, "המשך לאישור", NULL), y, 40, "המשך לאישור", NULL, can ? BTN_PRIMARY : BTN_OFF, HIT_LC_NEXT,
                 !a->lcu.confirm && a->lcu.focus == LCF_NEXT) + 10;
    if (!l->usb)   /* #1914 (Nadav, metal 20/10): on the stick there is nothing to go back to */
        bx += lc_btn(a, cr, bx + lc_btn_width(cr, "חזרה", "Esc"), y, 40, "חזרה", "Esc", BTN_PLAIN, HIT_LC_BACK,
                     !a->lcu.confirm && a->lcu.focus == LCF_BACK);
    char msg[400] = ""; Rgb mc = t->muted, dot = t->led_ok; int bad = 0, meas = 0;
    for (int i = 0; i < n; i++) {
        if (blocked(a, i) && !bad) {
            char lb[LC_LABEL_MAX], v[260]; Rgb c; lc_disk_label(&l->disks[i], lb, sizeof lb); verdict_text(a, &l->disks[i], src, v, sizeof v, &c);
            snprintf(msg, sizeof msg, "\xE2\x80\x8F%s: %s", lb, v); bad = 1;   /* #1502: RLM -- lb is the raw dev when the port is unknown */
        }
        const LcFit *f = lc_fit_of(a, l->disks[i].dev);
        if (lc_role_of(a, l->disks[i].dev) == LCR_TARGET && (!f || f->verdict == LCV_MEASURING)) meas = 1;
    }
    if (bad) dot = t->danger;
    else if (meas) { snprintf(msg, sizeof msg, "בודק אם הנתונים של המקור נכנסים ליעדים — לא נכתב דבר."); dot = t->led_idle; }
    else snprintf(msg, sizeof msg, "מועתקים רק בלוקים בשימוש, מהמקור אל כל היעדים במקביל.%s",
                  l->usb ? " הדיסק-און-קי שממנו עלה המחשב אינו מוצג ולא ייכתב." : "");   /* #1914 (A) */
    Text mt = text_make(cr, FONT_SANS, 13, 400, msg, (int)(xr - bx - 40), DIR_RTL);
    sb_led(cr, xr - 5, y + 20, 10, dot, bad ? t->danger_soft : t->success_soft, 0, 1);
    text_draw_r(cr, &mt, xr - 18, y + 20 - mt.h / 2, bad ? t->danger_fg : mc); text_free(&mt);
    if (a->lcu.confirm) lc_draw_confirm(a, cr, W, H);
}

/* ---- the confirm dialog ---------------------------------------------------------------- */

static double wrow(App *a, cairo_t *cr, double x, double y, double w, int i, const LcDisk *src, int draw) {
    const Theme *t = a->theme; const LcDisk *d = &a->st.lc.disks[i];
    char num[16], head[256], has[200], sz[32], z[600], v[300], mk[1400], e[600], col[8], sata[32];
    int drawer = eff_drawer(d->drawer, d->port);
    if (drawer > 0) snprintf(num, sizeof num, "%d", drawer); else snprintf(num, sizeof num, "%s", "—");
    char lb[LC_LABEL_MAX]; lc_disk_label(d, lb, sizeof lb); sata_of(d, sata, sizeof sata);
    char model_i[96]; bidi_isolate(d->model[0] ? d->model : d->dev, model_i, sizeof model_i);
    snprintf(head, sizeof head, "%s · %s", lb, model_i);
    lc_has_text(d, has, sizeof has); disk_size(d->size, sz, sizeof sz);
    Rgb vc; verdict_text(a, d, src, v, sizeof v, &vc);
    const LcFit *f = lc_fit_of(a, d->dev);
    hexc(t->danger_fg, col); text_escape(has, e, sizeof e);
    int k = snprintf(z, sizeof z, "עליו עכשיו: %s — <span foreground='%s' weight='bold'>יימחק כולו</span>", e, col);
    if (f && (f->verdict == LCV_SHRINK || strstr(v, "תוגדל")) && k > 0 && (size_t)k < sizeof z) {
        char vv[400]; hexc(vc, col); text_escape(v, vv, sizeof vv);
        snprintf(z + k, sizeof z - k, " · <span foreground='%s'>%s</span>", col, vv);
    }
    double inner = w - 32 - 44 - 28 - 80;
    Text tn = text_make(cr, FONT_MONO, 26, 600, num, 0, DIR_LTR);
    char he[300]; text_escape(head, he, sizeof he);
    snprintf(mk, sizeof mk, "%s  <span font_family='" FONT_MONO_FAMILY "' size='%d' weight='500' foreground='#%02X%02X%02X'>%s</span>",
             he, 13 * PANGO_SCALE, (int)lround(t->muted.r * 255), (int)lround(t->muted.g * 255), (int)lround(t->muted.b * 255), sata);
    Text ta = text_make_markup(cr, FONT_SANS, 15, 600, mk, (int)inner, DIR_RTL);
    Text tz = text_make_markup(cr, FONT_SANS, 13, 400, z, (int)inner, DIR_RTL);
    Text ts = text_make(cr, FONT_MONO, 16, 600, sz, 0, DIR_LTR);
    double h = 13 + ta.h + 4 + tz.h + 13, ah = 0;
    int lvl = smart_level(d->smart); Text tw = { NULL, 0, 0 }, tl = { NULL, 0, 0 };
    if (lvl) {
        char lk[160], am[700], r1[200]; snprintf(lk, sizeof lk, "הוצא את %s מהשיכפול", lb);
        tl = text_make(cr, FONT_SANS, 13, 600, lk, 0, DIR_RTL);
        text_escape(smart_reason_he(d), r1, sizeof r1); hexc(lvl == 2 ? t->danger : t->warn, col);
        snprintf(am, sizeof am, "<span foreground='%s' weight='bold'>%s:</span> %s. הדיסק עלול להיכשל באמצע או אחרי ההתקנה בכיתה.",
                 col, lvl == 2 ? "SMART תקול" : strcmp(d->smart, "failed_last") ? "אזהרת SMART" : "נכשל בשיכפול הקודם", r1);
        tw = text_make_markup(cr, FONT_SANS, 13, 400, am, (int)(inner - tl.w - 30), DIR_RTL);
        ah = 8 + fmax(tw.h, tl.h) + 20; h += ah;
    }
    if (draw) {
        double right = x + w - 16;
        text_draw(cr, &tn, right - 22 - tn.w / 2, y + (h - ah - tn.h) / 2, t->ink);
        text_draw_r(cr, &ta, right - 44 - 14, y + 13, t->ink);
        text_draw_r(cr, &tz, right - 44 - 14, y + 13 + ta.h + 4, t->ink);
        text_draw(cr, &ts, x + 16, y + (h - ah - ts.h) / 2, t->ink);
        if (lvl) {
            Rect ar = { x + 16, y + h - ah + 8 - 13, w - 32 - 44 - 14, ah - 8 };
            Rgb c = lvl == 2 ? t->danger : t->warn;
            draw_fill_rrect(cr, ar, 3, lvl == 2 ? t->danger_soft : t->warning_soft); draw_border_rrect(cr, ar, 3, c, 1);
            sb_led(cr, ar.x + ar.w - 16, ar.y + 10 + 8, 10, c, lvl == 2 ? t->danger_soft : t->warning_soft, 1, 1);
            text_draw_r(cr, &tw, ar.x + ar.w - 32, ar.y + 10, t->ink);
            Rect lr = { ar.x + 12, ar.y + 10, tl.w, tl.h };
            text_draw(cr, &tl, lr.x, lr.y, t->action_fg);
            cairo_rectangle(cr, lr.x, lr.y + lr.h - 2, lr.w, 1); draw_set(cr, t->action_fg); cairo_fill(cr);
            if (a->lcu.focus == LCF_DROP && a->lcu.bay == i) draw_border_rrect(cr, (Rect){ lr.x - 4, lr.y - 3, lr.w + 8, lr.h + 6 }, 3, t->indigo, 2);
            hit_add(a, (Rect){ lr.x - 4, lr.y - 3, lr.w + 8, lr.h + 6 }, HIT_LC_DROP_BASE + i);
        }
    }
    text_free(&tn); text_free(&ta); text_free(&tz); text_free(&ts);
    if (tw.layout) text_free(&tw);
    if (tl.layout) text_free(&tl);
    return h;
}

void lc_draw_confirm(App *a, cairo_t *cr, double W, double H) {
    const Theme *t = a->theme; const LcState *l = &a->st.lc;
    int si = lc_source_index(a), idx[LC_MAX_DISKS], nt = lc_targets(a, idx, LC_MAX_DISKS), shrink = lc_any_shrink(a);
    if (si < 0 || !nt) return;
    const LcDisk *src = &l->disks[si];
    double w = fmin(800, W - 48), inner = w - 56;
    char sl[LC_LABEL_MAX], title[256], lead[200], srow[448], has[200], model_i[96], go[160];
    lc_disk_label(src, sl, sizeof sl);
    if (nt == 1) snprintf(title, sizeof title, "למחוק דיסק אחד ולהעתיק אליו את %s?", sl);
    else snprintf(title, sizeof title, "למחוק %d דיסקים ולהעתיק אליהם את %s?", nt, sl);
    snprintf(lead, sizeof lead, "כל מה שנמצא היום על %s יימחק ולא יהיה אפשר לשחזר אותו.", nt == 1 ? "היעד" : "היעדים");
    lc_has_text(src, has, sizeof has); bidi_isolate(src->model[0] ? src->model : src->dev, model_i, sizeof model_i);
    snprintf(srow, sizeof srow, "%s · %s · %s", sl, model_i, has);
    if (nt == 1) { char tl[LC_LABEL_MAX]; lc_disk_label(&l->disks[idx[0]], tl, sizeof tl); snprintf(go, sizeof go, "מחק והעתק ל%s", tl); }
    else snprintf(go, sizeof go, "מחק והעתק ל-%d דיסקים", nt);
    /* measure first: the dialog's height is its content's */
    Text th = text_make(cr, FONT_SANS, 22, 600, title, (int)(inner - 42), DIR_RTL), tlh = text_make(cr, FONT_SANS, 14, 400, lead, (int)(inner - 42), DIR_RTL);
    double rows = 0; for (int k = 0; k < nt; k++) rows += wrow(a, cr, 0, 0, inner, idx[k], src, 0) + (k ? 1 : 0);
    /* #1477: a local clone shrinks only a RAM view of the source (#1466) -- it is read only either way */
    const char *sn = shrink ? "נקרא בלבד — יכווץ עותק בזיכרון" : "נקרא בלבד, לא ישתנה";
    Text ss = text_make(cr, FONT_SANS, 13, shrink ? 600 : 400, sn, 0, DIR_RTL);
    Text sr = text_make(cr, FONT_SANS, 13, 400, srow, (int)(inner - 28 - ss.w - 80), DIR_RTL);
    Text shr = shrink ? text_make(cr, FONT_SANS, 13, 400, "כדי שהנתונים ייכנסו ליעד הקטן, מחיצות ה-NTFS יכווצו בעותק בזיכרון לפני ההעתקה — המקור נקרא בלבד ולא נכתב. אם אין מספיק זיכרון לעותק, היעד הקטן לא ייכתב, והסיבה תוצג.", (int)(inner - 40), DIR_RTL)
                      : (Text){ NULL, 0, 0 };
    char c[300], meta[600]; lc_copy_text(&a->st.lc, src, c, sizeof c);   /* #1443 */
    snprintf(meta, sizeof meta, "%s · כל יעד ייבדק בסוף מול המקור (xxh64 לכל מחיצה, וטבלת המחיצות נקראת בחזרה).", c);
    Text tm = text_make(cr, FONT_SANS, 12, 400, meta, (int)inner, DIR_RTL);
    double srh = fmax(sr.h, ss.h) + 20, shh = shrink ? shr.h + 20 + 10 : 0;
    double h = 26 + fmax(30, th.h) + 6 + tlh.h + 16 + srh + 10 + shh + rows + 2 + 12 + tm.h + 20 + 44 + 24;
    text_free(&th); text_free(&tlh);
    Rect box = lc_dialog_box(a, cr, W, H, w, h);
    double y = box.y + 26, right = box.x + box.w - 28, x = box.x + 28;
    lc_dialog_head(a, cr, box, title, lead, &y);
    Rect sb = { x, y, inner, srh };
    draw_fill_rrect(cr, sb, 4, t->porcelain); draw_border_rrect(cr, sb, 4, t->hair, 1);
    Text tg = text_make(cr, FONT_SANS, 12, 700, "מקור", 0, DIR_RTL);
    Rect tr = { right - 14 - tg.w - 18, y + (srh - 22) / 2, tg.w + 18, 22 };
    draw_fill_rrect(cr, tr, 11, t->indigo); text_draw(cr, &tg, tr.x + 9, tr.y + (22 - tg.h) / 2, (Rgb){ 1, 1, 1 }); text_free(&tg);
    text_draw_r(cr, &sr, tr.x - 10, y + (srh - sr.h) / 2, t->ink);
    text_draw(cr, &ss, x + 14, y + (srh - ss.h) / 2, shrink ? t->warn : t->muted);
    text_free(&sr); text_free(&ss); y += srh + 10;
    if (shrink) {
        Rect ar = { x, y, inner, shr.h + 20 };
        draw_fill_rrect(cr, ar, 3, t->warning_soft); draw_border_rrect(cr, ar, 3, t->warn, 1);
        sb_led(cr, right - 16, y + 10 + 8, 10, t->warn, t->warning_soft, 1, 1);
        text_draw_r(cr, &shr, right - 32, y + 10, t->ink); text_free(&shr); y += shh;
    }
    Rect wl = { x, y, inner, rows + 2 };
    draw_border_rrect(cr, wl, 4, t->hair, 1);
    double ry = y + 1;
    for (int k = 0; k < nt; k++) {
        if (k) { cairo_rectangle(cr, x + 1, ry, inner - 2, 1); draw_set(cr, t->hair); cairo_fill(cr); ry += 1; }
        ry += wrow(a, cr, x, ry, inner, idx[k], src, 1);
    }
    y += rows + 2 + 12;
    text_draw_r(cr, &tm, right, y, t->muted); y += tm.h + 20; text_free(&tm);
    double gw = lc_btn(a, cr, right, y, 44, go, NULL, BTN_DANGER, HIT_LC_GO, a->lcu.focus == LCF_GO);
    lc_btn(a, cr, right - gw - 10, y, 44, "חזרה", "Esc", BTN_PLAIN, HIT_LC_CANCEL, a->lcu.focus == LCF_CANCEL);
}
