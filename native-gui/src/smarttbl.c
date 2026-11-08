/* #1859 (Nadav 07/11): every SMART attribute of a disk, in a drawer from the side of the cloner's wait
 * screen -- mockup docs/design/mockups-1107b/1859-smart-table.html, "מוצע ב" (1024x768, 16 rows).
 *
 *  - read only: nothing here writes, and the disk's colour is not touched (the rules of 20/10 stand);
 *  - "קריטי" is a neutral tag; orange is the row's background and a tag, only where the agent's table
 *    judged orange (5/187/10/196 over 0, a worn SSD, ...);
 *  - "בסף היצרן" stays RED (Nadav 07/11: "if I have to press F1, it is red") -- the mockup drew it grey;
 *  - a name the tables do not know is the vendor's, with "לא מוכר" -- never fine, never a colour;
 *  - the "עלה" column exists only when the server compared with a previous round (head cmp '1');
 *    without one the banner says "אין עדיין השוואה" -- never "יציב".
 * The rows come from the state file (csmart= / csmartrow=, agent/lib/smart/smarttable.sh). */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "screens_rounds.h"
#include "smarttbl.h"

#define SM_W    600.0   /* .sm-drawer width */
#define SM_ROW  29.0    /* .sm-tbl td height */
#define SM_TH   28.0    /* .sm-tbl th height */
#define SM_FOOT 30.0    /* .sm-foot: 6 + 17 + 6 + the hairline */
#define KBD_W   18.0

const char *standby_chip_he(const char *smart);   /* screens_standby.c */

const SmHead *smt_head(const State *s, const char *dev) {
    for (int i = 0; i < s->nsmheads; i++) if (!strcmp(s->smheads[i].dev, dev)) return &s->smheads[i];
    return NULL;
}

static const IdleDisk *disk_of(const State *s, const char *dev) {
    for (int i = 0; i < s->nidisks; i++) if (!strcmp(s->idisks[i].dev, dev)) return &s->idisks[i];
    return NULL;
}

/* the indexes into st.smrows of <dev>'s rows, in the agent's order (by id, NVMe after) */
static int rows_of(const State *s, const char *dev, int *idx, int max) {
    int n = 0;
    for (int i = 0; i < s->nsmrows && n < max; i++) if (!strcmp(s->smrows[i].dev, dev)) idx[n++] = i;
    return n;
}

static int has(const SmRow *r, char f) { return strchr(r->flags, f) != NULL; }
/* a rise that says "replace the disk": a critical attribute -- but not 199, which is the cable (#872) */
static int rose_critical(const SmRow *r) { return r->rose > 0 && has(r, 'c') && !has(r, 'k'); }

static void fmt_ll(long long v, char *out, size_t n) {
    char raw[32]; snprintf(raw, sizeof raw, "%lld", v);
    size_t len = strlen(raw), o = 0;
    for (size_t i = 0; i < len && o + 2 < n; i++) {
        if (i && (len - i) % 3 == 0) out[o++] = ',';
        out[o++] = raw[i];
    }
    out[o] = 0;
}

/* ---- the line under a drawer's name ---------------------------------------------------- */

Text smt_aff_make(cairo_t *cr, const SmHead *h) {
    char s[96]; snprintf(s, sizeof s, "כל מאפייני ה-SMART (%d) ›", h->total);
    return text_make(cr, FONT_SANS, 12.5, 600, s, 0, DIR_RTL);
}

double smt_aff_width(const Text *t) { return t->w + 8 + KBD_W; }

/* the text from <right>, then the S key to its left (.sm-aff: gap 8) */
void smt_aff_draw(App *a, cairo_t *cr, Text *t, double right, double y) {
    const Theme *th = a->theme;
    text_draw_r(cr, t, right, y, th->action_fg);
    Rect k = { right - t->w - 8 - KBD_W, y + (t->h - 18) / 2.0, KBD_W, 18 };
    draw_fill_rrect(cr, k, 4, th->surface); draw_border_rrect(cr, k, 4, th->hair, 1);
    Text kt = text_make(cr, FONT_MONO, 11, 600, "S", 0, DIR_LTR);
    text_draw(cr, &kt, k.x + (k.w - kt.w) / 2.0, k.y + (k.h - kt.h) / 2.0 - 1, th->ink);
    text_free(&kt);
}

/* ---- open, close, keys ----------------------------------------------------------------- */

static int open_dev(App *a, const char *dev) {
    if (!smt_head(&a->st, dev)) return 0;
    snprintf(a->smt.dev, sizeof a->smt.dev, "%s", dev);
    a->smt.open = 1; a->smt.top = 0;
    return 1;
}

/* S: the first drawer that is not green and has a table, else the first with one */
static int open_first(App *a) {
    const State *s = &a->st; int pick = -1;
    for (int i = 0; i < s->nidisks && pick < 0; i++)
        if (smt_head(s, s->idisks[i].dev) && smart_level(s->idisks[i].smart)) pick = i;
    for (int i = 0; i < s->nidisks && pick < 0; i++) if (smt_head(s, s->idisks[i].dev)) pick = i;
    return pick >= 0 && open_dev(a, s->idisks[pick].dev);
}

static void next_disk(App *a, int dir) {
    const State *s = &a->st; int at = -1, n = s->nidisks;
    for (int i = 0; i < n; i++) if (!strcmp(s->idisks[i].dev, a->smt.dev)) at = i;
    for (int k = 1; k <= n && at >= 0; k++) {
        const char *dev = s->idisks[((at + dir * k) % n + n) % n].dev;
        if (smt_head(s, dev)) { open_dev(a, dev); return; }
    }
}

static int many(const State *s) {
    int c = 0;
    for (int i = 0; i < s->nidisks; i++) c += smt_head(s, s->idisks[i].dev) != NULL;
    return c > 1;
}

int smt_handle(App *a, const Event *e) {
    SmTbl *m = &a->smt;
    if (a->screen != SCREEN_STANDBY) { m->open = 0; return 0; }
    if (!m->open) {
        if (e->type == UIEV_CHAR && (e->ch == 's' || e->ch == 'S')) return open_first(a);
        if (e->type == UIEV_CLICK) {
            int id = app_hit(a, e->x, e->y);
            if (id >= HIT_SMT_BASE && id < HIT_SMT_BASE + MAX_DRAWERS && id - HIT_SMT_BASE < a->st.nidisks) {
                a->ptr_x = e->x; a->ptr_y = e->y;
                return open_dev(a, a->st.idisks[id - HIT_SMT_BASE].dev);
            }
        }
        return 0;
    }
    if (!smt_head(&a->st, m->dev) || !disk_of(&a->st, m->dev)) { m->open = 0; return 0; }   /* the disk went */
    if (e->type == UIEV_MOVE) return 0;
    if (e->type == UIEV_CLICK) {
        a->ptr_x = e->x; a->ptr_y = e->y;
        if (app_hit(a, e->x, e->y) == HIT_SMT_SCRIM) m->open = 0;
    } else if (e->type == UIEV_SCROLL) m->top -= e->delta > 0 ? 3 : -3;
    else if (e->type == UIEV_KEY) {
        if (e->key == KEYSYM_ESC) m->open = 0;
        else if (e->key == KEYSYM_UP) m->top--;
        else if (e->key == KEYSYM_DOWN) m->top++;
        else if (e->key == KEYSYM_TAB || e->key == KEYSYM_BACKTAB) next_disk(a, e->key == KEYSYM_TAB ? 1 : -1);
    }
    if (m->top < 0) m->top = 0;   /* the upper bound is the drawer's, in smt_draw */
    return 1;
}

const char *smt_keys(const App *a) {
    if (!a->smt.open) return "";
    return many(&a->st) ? "Tab  מגירה הבאה    ↑ ↓  גלילה    Esc  סגירה" : "↑ ↓  גלילה    Esc  סגירה";
}

/* ---- drawing --------------------------------------------------------------------------- */

enum { CH_CRIT, CH_WARN, CH_RED, CH_PLAIN };

/* a tag, from <right> to the left; returns its width. 11/600, 18 high (.sm-tbl .chip) */
static double chip(cairo_t *cr, const Theme *t, double right, double cy, const char *label, int kind) {
    Text x = text_make(cr, FONT_SANS, 11, 600, label, 0, DIR_RTL);
    int dot = kind == CH_WARN || kind == CH_RED;
    double w = 8 + (dot ? 11 : 0) + x.w + 8, h = 18;
    Rect r = { right - w, cy - h / 2, w, h };
    Rgb fg = kind == CH_CRIT ? t->action_fg : kind == CH_PLAIN ? t->muted : t->on_ink;
    Rgb bg = kind == CH_CRIT ? t->indigo_soft : kind == CH_WARN ? t->warn : kind == CH_RED ? t->danger_fill : t->hover;
    draw_fill_rrect(cr, r, h / 2, bg);
    if (kind == CH_PLAIN) draw_border_rrect(cr, r, h / 2, t->hair, 1);
    if (dot) { cairo_arc(cr, right - 8 - 3, cy, 3, 0, 2 * M_PI); draw_set(cr, fg); cairo_fill(cr); }
    text_draw_r(cr, &x, right - 8 - (dot ? 11 : 0), cy - x.h / 2.0, fg);
    text_free(&x);
    return w;
}

static double chip_w(cairo_t *cr, const char *label, int dot) {
    Text x = text_make(cr, FONT_SANS, 11, 600, label, 0, DIR_RTL);
    double w = 8 + (dot ? 11 : 0) + x.w + 8; text_free(&x); return w;
}

/* text that must fit <maxw>: the size, then a smaller one, then cut with "…" at a whole character */
static Text fit(cairo_t *cr, Font f, double px, const char *s, double maxw, Dir d) {
    Text t = text_make(cr, f, px, 400, s, 0, d);
    if (t.w <= maxw) return t;
    text_free(&t);
    t = text_make(cr, f, px - 1.5, 400, s, 0, d);
    char buf[200]; snprintf(buf, sizeof buf, "%s", s);
    size_t len = strlen(buf);
    while (t.w > maxw && len > 1) {
        len--; while (len && ((unsigned char)buf[len] & 0xC0) == 0x80) len--;
        buf[len] = 0;
        char cut[220]; snprintf(cut, sizeof cut, "%s…", buf);
        text_free(&t); t = text_make(cr, f, px - 1.5, 400, cut, 0, d);
    }
    return t;
}

/* the banner under the title: a rise of a critical attribute (orange), or what is not known (blue).
 * warn = 1 orange. Never "stable": the three cmp words say what the server knows. */
static void banner_words(const State *s, const SmHead *h, const int *idx, int n, int *warn,
                         char *title, size_t tn, char *detail, size_t dn) {
    *warn = 0; detail[0] = 0;
    if (h->cmp == '0') {
        snprintf(title, tn, "אין עדיין השוואה");
        snprintf(detail, dn, "זה הסבב הראשון של הדיסק הזה אצלנו, ולכן אין עמודת \"עלה\".");
        return;
    }
    if (h->cmp != '1') {
        snprintf(title, tn, "ההשוואה לסבב הקודם לא התקבלה מהשרת");
        snprintf(detail, dn, "אין עמודת \"עלה\". זה לא אומר שדבר לא עלה.");
        return;
    }
    int crit = 0; size_t u = 0;
    for (int i = 0; i < n; i++) {
        const SmRow *r = &s->smrows[idx[i]];
        if (!rose_critical(r)) continue;
        if (++crit <= 3 && u + 80 < dn) {
            const char *nm = r->name[0] ? r->name : r->id;
            if (r->raw >= 0) u += snprintf(detail + u, dn - u, "%s%s: מ-%lld ל-%lld", u ? " · " : "", nm, r->raw - r->rose, r->raw);
            else u += snprintf(detail + u, dn - u, "%s%s: עלה ב-%lld", u ? " · " : "", nm, r->rose);
        }
    }
    if (crit > 3 && u + 24 < dn) snprintf(detail + u, dn - u, " · ועוד %d", crit - 3);
    if (crit) { *warn = 1; snprintf(title, tn, "עלה מאז הסבב הקודם — להחליף את הדיסק"); return; }
    snprintf(title, tn, "הושווה לסבב הקודם");
    snprintf(detail, dn, "אף מאפיין קריטי לא עלה מאז.");
}

void smt_draw(App *a, cairo_t *cr, double W, double H, double head_h) {
    SmTbl *m = &a->smt;
    if (!m->open) return;
    const State *s = &a->st; const Theme *t = a->theme;
    const IdleDisk *d = disk_of(s, m->dev); const SmHead *h = smt_head(s, m->dev);
    if (a->screen != SCREEN_STANDBY || !d || !h) { m->open = 0; return; }
    int idx[MAX_SMROWS]; int n = rows_of(s, m->dev, idx, MAX_SMROWS);
    double usable = H - N_STATUS_H, ph = usable - head_h, pw = fmin(SM_W, W * .62), right = pw - 20;
    Rect panel = { 0, head_h, pw, ph };

    /* the dim over the screen (the header stays), then the drawer; only the drawer's own hits */
    draw_set_a(cr, t == &THEME_DARK ? (Rgb){ 0, 0, 0 } : t->header, t == &THEME_DARK ? .55 : .45);
    cairo_rectangle(cr, 0, head_h, W, ph); cairo_fill(cr);
    a->nhits = 0; a->clip_on = 0;
    hit_add(a, (Rect){ 0, head_h, W, ph }, HIT_SMT_SCRIM);
    hit_add(a, panel, HIT_SMT_PANEL);
    draw_box_shadow(cr, panel, 0, 8, 24, 0, (Rgb){ 0, 0, 0 }, .18);
    draw_fill_rrect(cr, panel, 0, t->surface);

    /* the head: the drawer, the model, the disk's chip as the cage says it */
    char title[SLOT_NAME_MAX + 48], label[SLOT_NAME_MAX + 16];
    int bay = eff_drawer(d->drawer, d->port);
    if (bay > 0) snprintf(label, sizeof label, "מגירה %d", bay);
    else unmapped_label(d->port >= 1 && d->port <= MAX_DRAWERS ? s->cslot_names[d->port - 1] : "", d->conn, d->port, d->dev, label, sizeof label);
    snprintf(title, sizeof title, "מאפייני SMART · %s", label);
    double y = head_h + 14;
    Text th = text_make(cr, FONT_SANS, 18, 600, title, (int)(pw - 40), DIR_RTL);
    text_draw_r(cr, &th, right, y, t->ink); y += th.h + 3;
    Text tm = text_make(cr, FONT_SANS, 12.5, 400, d->model[0] ? d->model : "דגם לא דווח", 0, d->model[0] ? DIR_LTR : DIR_RTL);
    text_draw_r(cr, &tm, right, y + 2, t->muted);
    int lvl = smart_level(d->smart);
    Rgb col = smart_color(t, d->smart), soft = lvl == 2 ? t->danger_soft : lvl == 1 ? t->warning_soft : t->success_soft;
    double chip_h = 22, cy = y + chip_h / 2;
    const char *chip_s = standby_chip_he(d->smart);
    sb_pill(cr, right - tm.w - 10, cy, chip_s, lvl ? t->on_ink : col, lvl ? col : soft);
    y += fmax(tm.h + 2, chip_h) + 10;
    text_free(&th); text_free(&tm);

    /* the banner */
    char bt[160], bd[400]; int warn;
    banner_words(s, h, idx, n, &warn, bt, sizeof bt, bd, sizeof bd);
    double bw = pw - 40, bpad = 9;
    Text b1 = text_make(cr, FONT_SANS, 13, 600, bt, (int)(bw - 24 - 32), DIR_RTL);
    Text b2 = text_make(cr, FONT_SANS, 12.5, 400, bd, (int)(bw - 24 - 32), DIR_RTL);
    double bh = bpad + b1.h + (bd[0] ? b2.h : 0) + bpad;
    Rect br = { 20, y, bw, bh };
    Rgb bcol = warn ? t->warn : t->indigo;
    draw_fill_rrect(cr, br, t->radius_sm, warn ? t->warning_soft : t->indigo_soft);
    draw_border_rrect(cr, br, t->radius_sm, bcol, 1);
    cairo_arc(cr, br.x + br.w - 12 - 10, br.y + bpad + 10, 10, 0, 2 * M_PI); draw_set(cr, bcol); cairo_fill(cr);
    Text ic = text_make(cr, FONT_SANS, 12, 600, warn ? "!" : "i", 0, DIR_LTR);
    text_draw(cr, &ic, br.x + br.w - 22 - ic.w / 2.0, br.y + bpad + 10 - ic.h / 2.0, t->on_ink); text_free(&ic);
    text_draw_r(cr, &b1, br.x + br.w - 12 - 20 - 10, br.y + bpad, warn ? t->warning_ink : t->ink);
    if (bd[0]) text_draw_r(cr, &b2, br.x + br.w - 12 - 20 - 10, br.y + bpad + b1.h, t->ink);
    y += bh + 10; text_free(&b1); text_free(&b2);

    /* the table: columns from the right -- id, name, raw, rose (only when compared), the marks */
    double id_w = 52, raw_w = 110, mark_w = 190, rose_w = h->cmp == '1' ? 46 : 0;
    double c_id = pw, c_name = pw - id_w, c_raw = mark_w + rose_w + raw_w, c_rose = mark_w + rose_w, c_mark = mark_w;
    double foot_y = head_h + ph - SM_FOOT;
    cairo_rectangle(cr, 0, y, pw, 1); draw_set(cr, t->hair); cairo_fill(cr);
    const char *heads[5] = { "מזהה", "שם", "גולמי", "עלה", "סימון" };
    double hx[5] = { c_id, c_name, c_raw, c_rose, c_mark };
    for (int i = 0; i < 5; i++) {
        if (i == 3 && !rose_w) continue;
        Text x = text_make(cr, FONT_SANS, 11.5, 600, heads[i], 0, DIR_RTL);
        text_draw_r(cr, &x, hx[i] - 10, y + (SM_TH - x.h) / 2.0, t->muted); text_free(&x);
    }
    y += SM_TH;
    cairo_rectangle(cr, 0, y, pw, 1); draw_set(cr, t->hair); cairo_fill(cr);
    y += 1;
    int vis = (int)floor((foot_y - y) / SM_ROW);
    if (vis < 1) vis = 1;
    int maxtop = n > vis ? n - vis : 0;
    if (m->top > maxtop) m->top = maxtop;
    m->vis = vis;

    cairo_save(cr);
    cairo_rectangle(cr, 0, y, pw, foot_y - y); cairo_clip(cr);
    for (int i = m->top; i < n && i < m->top + vis; i++) {
        const SmRow *r = &s->smrows[idx[i]];
        double ry = y + (i - m->top) * SM_ROW, rcy = ry + SM_ROW / 2;
        int red = has(r, 'R') || has(r, 'f'), orange = has(r, 'o');
        if (red || orange) { cairo_rectangle(cr, 0, ry, pw, SM_ROW); draw_set(cr, red ? t->danger_soft : t->warning_soft); cairo_fill(cr); }
        cairo_rectangle(cr, 0, ry + SM_ROW - 1, pw, 1); draw_set_a(cr, t->hair, .5); cairo_fill(cr);

        Text id = text_make(cr, FONT_MONO, 12, 600, strspn(r->id, "0123456789") == strlen(r->id) ? r->id : "—", 0, DIR_LTR);
        text_draw_r(cr, &id, c_id - 10, rcy - id.h / 2.0, t->muted); text_free(&id);

        /* the name: Hebrew from the tables; a name they do not know is the vendor's, in its own letters, "לא מוכר" beside it */
        double nmax = c_name - c_raw - 20;
        if (has(r, 'u')) {
            const char *tag = r->name[0] ? "לא מוכר" : "לא מוכר — שם היצרן לא התקבל";
            double cw = chip_w(cr, tag, 0), shown = nmax;
            char nm[SLOT_NAME_MAX + 16]; if (r->name[0]) snprintf(nm, sizeof nm, "%s", r->name); else snprintf(nm, sizeof nm, "מאפיין %s", r->id);
            Text x = fit(cr, r->name[0] ? FONT_MONO : FONT_SANS, 12, nm, shown - cw - 8, r->name[0] ? DIR_LTR : DIR_RTL);
            text_draw_r(cr, &x, c_name - 10, rcy - x.h / 2.0, t->ink);
            chip(cr, t, c_name - 10 - x.w - 8, rcy, tag, CH_PLAIN);
            text_free(&x);
        } else {
            Text x = fit(cr, FONT_SANS, 12.5, r->name, nmax, DIR_RTL);
            text_draw_r(cr, &x, c_name - 10, rcy - x.h / 2.0, t->ink); text_free(&x);
        }

        char num[40];
        if (r->raw >= 0) fmt_ll(r->raw, num, sizeof num); else snprintf(num, sizeof num, "—");
        Text rw = text_make(cr, FONT_MONO, 12, 400, num, 0, DIR_LTR);
        text_draw_r(cr, &rw, c_raw - 10, rcy - rw.h / 2.0, r->raw >= 0 ? t->ink : t->muted); text_free(&rw);
        if (rose_w) {
            if (r->rose > 0) { char p[48]; fmt_ll(r->rose, num, sizeof num); snprintf(p, sizeof p, "+%s", num);
                Text x = text_make(cr, FONT_MONO, 12, rose_critical(r) ? 600 : 400, p, 0, DIR_LTR);
                text_draw_r(cr, &x, c_rose - 10, rcy - x.h / 2.0, rose_critical(r) ? t->warning_ink : t->muted); text_free(&x); }
            else { Text x = text_make(cr, FONT_SANS, 12, 400, "—", 0, DIR_RTL);
                text_draw_r(cr, &x, c_rose - 10, rcy - x.h / 2.0, t->muted); text_free(&x); }
        }

        double mx = c_mark - 10;   /* the tags, from the right: critical, the colour, the threshold, the cable */
        if (has(r, 'c')) mx -= chip(cr, t, mx, rcy, "קריטי", CH_CRIT) + 6;
        if (has(r, 'R')) mx -= chip(cr, t, mx, rcy, "אדום", CH_RED) + 6;
        if (orange) mx -= chip(cr, t, mx, rcy, "כתום", CH_WARN) + 6;
        if (has(r, 'f')) mx -= chip(cr, t, mx, rcy, "בסף היצרן", CH_RED) + 6;   /* RED: the BIOS stops there (Nadav 07/11) */
        if (has(r, 'h')) mx -= chip(cr, t, mx, rcy, "מגירה/כבל, לא הדיסק", CH_PLAIN) + 6;
    }
    cairo_restore(cr);

    /* the foot: how many are on show, and the way out */
    cairo_rectangle(cr, 0, foot_y, pw, 1); draw_set(cr, t->hair); cairo_fill(cr);
    int shown = n < vis ? n : vis;
    char cnt[64]; snprintf(cnt, sizeof cnt, "מוצגות %d מתוך %d", shown, h->total);
    Text fc = text_make(cr, FONT_SANS, 12, 400, cnt, 0, DIR_RTL);
    text_draw_r(cr, &fc, pw - 14, foot_y + 1 + (SM_FOOT - 1 - fc.h) / 2.0, t->muted); text_free(&fc);
    Text fk = text_make(cr, FONT_SANS, 12, 400, n > vis ? "↑ ↓ גלילה · Esc סגירה" : "Esc סגירה", 0, DIR_RTL);
    text_draw(cr, &fk, 14, foot_y + 1 + (SM_FOOT - 1 - fk.h) / 2.0, t->muted); text_free(&fk);
}

/* ---- --png ------------------------------------------------------------------------------ */

/* the mockup's disk: a MZ7LN256 with 21 attributes, 5 up from 9 to 12 -- tests/fixtures/smart-1921/cloner2-sdc.txt
 * with the mockup's three changes. Only when the state file has no table of its own. */
static void sample(State *s) {
    static const struct { const char *id, *fl; long long raw, rose; const char *name; } R[] = {
        { "5", "co", 12, 3, "סקטורים שהועברו" }, { "9", "", 9487, 31, "שעות עבודה" }, { "12", "", 1077, 2, "מחזורי הפעלה" },
        { "170", "", 0, -1, "מקום רזרבי פנוי" }, { "171", "", 0, -1, "כשלונות כתיבה (SSD)" }, { "172", "", 0, -1, "כשלונות מחיקה (SSD)" },
        { "173", "", 75, -1, "שחיקה" }, { "174", "", 369, 1, "כיבויים פתאומיים" }, { "178", "", 0, -1, "רזרבה בשבב הגרוע" },
        { "180", "", 614, -1, "רזרבה פנויה (סה״כ)" }, { "184", "", 0, -1, "שגיאות בנתיב הנתונים" },
        { "187", "c", 0, -1, "שגיאות שלא תוקנו" }, { "194", "", 28, -1, "טמפרטורה" },
        { "199", "ckh", 163, -1, "שגיאות CRC" }, { "213", "u", 0, -1, "Unknown_Attribute" },
        { "233", "", 15728639, -1, "מדד שחיקה" }, { "241", "", 18961, 412, "סה״כ נכתב" }, { "242", "", 13588, 260, "סה״כ נקרא" },
        { "249", "", 28930, 330, "כתיבות לזיכרון" }, { "250", "", 39765357082LL, -1, "ניסיונות קריאה חוזרים" },
        { "251", "", 60672221888LL, -1, "מידע יצרן (251)" } };
    s->nidisks = 2; s->nsmheads = 1; s->nsmrows = 0;   /* sdb first: the card opens the first head */
    s->idisks[0] = (IdleDisk){ .port = 1, .dev = "sda", .model = "WDC WD5000AAKX", .size = 500107862016ULL, .smart = "passed", .drawer = 1, .conn = "SATA 0" };
    s->idisks[1] = (IdleDisk){ .port = 2, .dev = "sdb", .model = "SAMSUNG MZ7LN256HAJQ", .size = 256060514304ULL, .smart = "warn",
                               .cause = "reallocated", .drawer = 2, .conn = "SATA 1" };
    s->smheads[0] = (SmHead){ .dev = "sdb", .total = (int)(sizeof R / sizeof *R), .cmp = '1' };
    for (size_t i = 0; i < sizeof R / sizeof *R; i++) {
        SmRow *r = &s->smrows[s->nsmrows++];
        snprintf(r->dev, sizeof r->dev, "sdb"); snprintf(r->id, sizeof r->id, "%s", R[i].id);
        snprintf(r->flags, sizeof r->flags, "%s", R[i].fl); snprintf(r->name, sizeof r->name, "%s", R[i].name);
        r->raw = R[i].raw; r->rose = R[i].rose;
    }
    s->smheads[s->nsmheads++] = (SmHead){ .dev = "sda", .total = 3, .cmp = '0' };   /* the first drawer: a short table, so both show the line */
    for (int i = 0; i < 3; i++) {
        SmRow *r = &s->smrows[s->nsmrows++]; const int k = i == 0 ? 0 : i == 1 ? 1 : 12;
        snprintf(r->dev, sizeof r->dev, "sda"); snprintf(r->id, sizeof r->id, "%s", R[k].id);
        snprintf(r->flags, sizeof r->flags, "%s", i == 0 ? "c" : ""); snprintf(r->name, sizeof r->name, "%s", R[k].name);
        r->raw = i == 0 ? 0 : R[k].raw; r->rose = -1;
    }
}

void smt_png(App *a) {
    if (!a->st.nsmheads) sample(&a->st);
    memset(&a->smt, 0, sizeof a->smt);
    open_dev(a, a->st.smheads[0].dev);
}
