/* #1269: drivers per target disk, on the room / direct setup screen.
 *
 * The drawers of a cloner go to OTHER machines, so the hardware the cloner
 * detected says nothing about them (docs/architecture/interfaces.md s17): there is no
 * "automatic" here -- the operator picks a model the driver library has a
 * package for, or "בלי". The mockup is docs/design/apple-mockups/
 * native-full.html (drvBlock / dtag / dmap / drvSummary):
 *   - "להוסיף דרייברים", off on every entry; disabled, with the reason, when
 *     the library has no model or the list was not read (two reasons, two
 *     sentences -- principle 5);
 *   - "כל הדיסקים שנבחרו": one model on every chosen disk; "לבחור דיסקים":
 *     a click on a disk's tag applies "דגם לסימון", a second click "בלי";
 *   - a summary line under the grid;
 *   - on open, one record value for every chosen disk with a model.
 * The live round's per-drawer outcome (drawer_list[].drivers) is said in
 * the drawer's own row, under its bar (#1331, lcbay.c: lc_bay_drivers). */
#include <math.h>
#include <stdio.h>
#include <string.h>
#include "screens_rounds.h"

/* native-full.html: .drv / .drvr / .swt / .drvc / .dseg / .dtag / .drvs */
#define DRV_ROW_H     28.0   /* .drvr min-height */
#define DRV_GAP_BELOW 12.0   /* .drv margin-bottom */
#define DRV_SW_W      34.0   /* .swt width */
#define DRV_SW_H      20.0   /* .swt height */
#define DRV_KNOB      14.0   /* .swt i */
#define DRV_SW_GAP    10.0   /* .drvr gap */
#define DRV_HINT_GAP  16.0   /* gap + .drvh margin-inline-start */
#define DRV_COLS_TOP   8.0   /* .drvc margin-top */
#define DRV_COL_GAP   14.0   /* .drvc gap */
#define DRV_TAG_H     20.0   /* .dtag height */
#define DRV_TAG_PAD    8.0   /* .dtag padding */
#define DRV_TAG_LEFT   5.0   /* .dtag left */
#define DRV_SUM_TOP   10.0   /* .drvs margin-top */
#define DRV_SUM_PADY   6.0   /* .drvs padding */
#define DRV_SUM_PADX  10.0

static const char *const DRV_UNREAD = "רשימת הדגמים לא נקראה מהשרת — אי אפשר להוסיף דרייברים כרגע";
static const char *const DRV_NONE   = "אין בשרת חבילת דרייברים עם דגם מחשב — מוסיפים חבילה בקונסולה";
static const char *const DRV_ON     = "החבילות שבשרת לדגם שנבחר יונחו על הדיסק אחרי השחזור";
static const char *const DRV_OFF    = "כבוי — לא יונחו דרייברים";

/* ---- the choice ----------------------------------------------------------- */

static int drv_same(const char *v1, const char *m1, const char *v2, const char *m2) {
    return !strcmp(v1, v2) && !strcmp(m1, m2);
}

static const DriverModel *drv_find(const State *s, const char *vendor, const char *model) {
    for (int i = 0; i < s->ndriver_models; i++)
        if (drv_same(s->driver_models[i].vendor, s->driver_models[i].model, vendor, model))
            return &s->driver_models[i];
    return NULL;
}

int room_drv_active(const App *a) { return a->drv_on && a->st.ndriver_models > 0; }

void room_drv_reset(App *a) {
    a->drv_on = 0; a->drv_pick = 0; a->ndrv_map = 0;
    a->drv_vendor[0] = a->drv_model[0] = 0;
    if (a->dd_open == HIT_ROOM_DRV_MODEL) a->dd_open = 0;
}

static void drv_set_current(App *a, const DriverModel *dm) {
    char v[sizeof a->drv_vendor], m[sizeof a->drv_model];   /* -Wrestrict: dm is inside *a */
    snprintf(v, sizeof v, "%s", dm->vendor);
    snprintf(m, sizeof m, "%s", dm->model);
    snprintf(a->drv_vendor, sizeof a->drv_vendor, "%s", v);
    snprintf(a->drv_model, sizeof a->drv_model, "%s", m);
}

/* The mockup's default is the first model. A choice the library no longer
 * has is NOT swapped for another one behind the operator's back: it stays on
 * screen, and room_drv_encode refuses it by name. */
void room_drv_after_reload(App *a) {
    if (!a->drv_vendor[0] && a->st.ndriver_models > 0) drv_set_current(a, &a->st.driver_models[0]);
}

void room_drv_choose(App *a, int i) {
    if (i >= 0 && i < a->st.ndriver_models) drv_set_current(a, &a->st.driver_models[i]);
}

static int drv_map_find(const App *a, const char *mac, int port) {
    for (int i = 0; i < a->ndrv_map; i++)
        if (a->drv_map[i].port == port && !strcmp(a->drv_map[i].mac, mac)) return i;
    return -1;
}

/* vendor "" = "בלי" for this disk. */
static void drv_map_set(App *a, const char *mac, int port, const char *vendor, const char *model) {
    char mc[24], v[64], md[128];                           /* -Wrestrict: all inside *a */
    snprintf(mc, sizeof mc, "%s", mac);
    snprintf(v, sizeof v, "%s", vendor);
    snprintf(md, sizeof md, "%s", model);
    int i = drv_map_find(a, mc, port);
    if (i < 0) {
        if (a->ndrv_map >= MAX_ROOM_SELECTIONS) return;
        i = a->ndrv_map++;
        snprintf(a->drv_map[i].mac, sizeof a->drv_map[i].mac, "%s", mc);
        a->drv_map[i].port = port;
    }
    snprintf(a->drv_map[i].vendor, sizeof a->drv_map[i].vendor, "%s", v);
    snprintf(a->drv_map[i].model, sizeof a->drv_map[i].model, "%s", md);
}

/* The model of one chosen disk: 1 with *vendor / *model, 0 = "בלי". In pick
 * mode a disk the operator did not touch takes the current model (the
 * mockup's slotModel). */
static int drv_slot(const App *a, const Machine *m, int port, const char **vendor, const char **model) {
    *vendor = a->drv_vendor; *model = a->drv_model;
    if (a->drv_pick) {
        int i = drv_map_find(a, m->mac, port);
        if (i >= 0) { *vendor = a->drv_map[i].vendor; *model = a->drv_map[i].model; }
    }
    return (*vendor)[0] != 0;
}

/* The mockup's rule: a click applies the current model; a click on a disk
 * that already has it -- "בלי". No hidden cycle through the models. */
void room_drv_tag_click(App *a, int mi, int port) {
    if (!room_drv_active(a) || !a->drv_pick || mi < 0 || mi >= a->st.nmachines) return;
    const Machine *m = &a->st.machines[mi];
    const RoomDrawer *drawer = NULL;
    for (int j = 0; j < m->nroom_drawers; j++)
        if (m->room_drawers[j].port == port) { drawer = &m->room_drawers[j]; break; }
    if (!drawer || !room_pickable(drawer)) return;   /* #1732 */
    const char *v, *md;
    if (drv_slot(a, m, port, &v, &md) && drv_same(v, md, a->drv_vendor, a->drv_model))
        drv_map_set(a, m->mac, port, "", "");
    else
        drv_map_set(a, m->mac, port, a->drv_vendor, a->drv_model);
}

/* The same selection rule as main.c room_effective_selected (#695). */
static int drv_selected(const App *a, const Machine *m, const RoomDrawer *d) {
    for (int i = 0; i < a->nroom_selection; i++)
        if (a->room_selection[i].port == d->port && !strcmp(a->room_selection[i].mac, m->mac))
            return a->room_selection[i].selected;
    return d->selected;
}

int room_drv_encode(App *a, char *out, size_t n) {
    size_t used = 0;
    int chosen = 0, count = 0;
    out[0] = 0;
    for (int mi = 0; mi < a->st.nmachines; mi++) {
        const Machine *m = &a->st.machines[mi];
        for (int j = 0; j < m->nroom_drawers; j++) {
            const RoomDrawer *d = &m->room_drawers[j];
            if (!room_pickable(d) || !drv_selected(a, m, d)) continue;   /* #1732 */
            chosen++;
            const char *v, *md;
            if (!drv_slot(a, m, d->port, &v, &md)) continue;          /* "בלי" */
            if (!drv_find(&a->st, v, md)) {
                snprintf(a->st.room_error, sizeof a->st.room_error,
                         "הדגם %s כבר אינו בספריית הדרייברים — בחרו דגם מחדש", md);
                return -1;
            }
            int k = snprintf(out + used, n - used, "%s%s|%d|%s|%s", used ? "|" : "", m->mac, d->port, v, md);
            if (k < 0 || (size_t)k >= n - used) {                     /* truncated = no order */
                snprintf(a->st.room_error, sizeof a->st.room_error, "יותר מדי דיסקים עם דרייברים לרשומה אחת");
                return -1;
            }
            used += (size_t)k; count++;
        }
    }
    if (!chosen) {
        snprintf(a->st.room_error, sizeof a->st.room_error, "בחרו את דיסקי היעד — דרייברים מונחים רק על דיסקים שנבחרו");
        return -1;
    }
    if (!count) {
        snprintf(a->st.room_error, sizeof a->st.room_error, "לא נבחר דגם לאף דיסק — בחרו דגם, או כבו את \"להוסיף דרייברים\"");
        return -1;
    }
    return count;
}

/* The rows of the model list: the library's models, and nothing else. */
int room_drv_options(const App *a, const char **opts, char (*buf)[200], int max) {
    int n = 0;
    for (int i = 0; i < a->st.ndriver_models && n < max; i++) {
        char *row = buf[n];
        snprintf(row, 200, "%s %s", a->st.driver_models[i].vendor, a->st.driver_models[i].model);
        opts[n++] = row;
    }
    return n;
}

/* ---- the block under the fields --------------------------------------------- */

typedef struct { Text label, hint; double row_h; int enabled; } DrvRow;

static DrvRow drv_row_make(App *a, cairo_t *cr, double w) {
    DrvRow r;
    r.enabled = a->st.ndriver_models > 0;
    const char *hint = !r.enabled ? (a->st.driver_models_read < 0 ? DRV_UNREAD : DRV_NONE)
                     : a->drv_on ? DRV_ON : DRV_OFF;
    r.label = text_make(cr, FONT_SANS, 13, 500, "להוסיף דרייברים", 0, DIR_RTL);
    double rest = w - DRV_SW_W - DRV_SW_GAP - r.label.w - DRV_HINT_GAP;
    r.hint = rtl_block_make(cr, 12, 400, hint, rest > 80 ? rest : 80);
    r.row_h = dmax(DRV_ROW_H, r.hint.h);
    return r;
}

static const char *drv_pick_label(const App *a) {
    return a->drv_pick ? "דגם לסימון — לחיצה על תגית של דיסק מחילה אותו" : "דגם מחשב";
}

double room_drv_block_height(App *a, cairo_t *cr, double w) {
    DrvRow r = drv_row_make(a, cr, w);
    double h = r.row_h;
    if (r.enabled && a->drv_on) {
        double col_w = (w - DRV_COL_GAP) / 2;
        Text l1 = label_make(cr, drv_pick_label(a), col_w), l2 = label_make(cr, "על אילו דיסקים", col_w);
        h += DRV_COLS_TOP + dmax(l1.h, l2.h) + 2 + field_height(cr);
        text_free(&l1); text_free(&l2);
    }
    text_free(&r.label); text_free(&r.hint);
    return h + DRV_GAP_BELOW;
}

/* .dseg: two halves, the first at the inline start (right); the pressed one
 * in the action-soft colour, its words bold in the action colour. */
static void drv_segment_draw(App *a, cairo_t *cr, Rect box) {
    const Theme *t = a->theme;
    static const char *const words[2] = { "כל הדיסקים שנבחרו", "לבחור דיסקים" };
    static const int ids[2] = { HIT_ROOM_DRV_ALL, HIT_ROOM_DRV_PICK };
    double bw = box.w / 2;
    draw_fill_rrect(cr, box, RADIUS_SM, t->field);
    for (int i = 0; i < 2; i++) {
        Rect b = { box.x + box.w - (i + 1) * bw, box.y, bw, box.h };
        int on = (i == 1) == (a->drv_pick != 0);
        if (on) draw_fill_rrect(cr, b, RADIUS_SM, t->indigo_soft);
        else if (hovered(a, b)) draw_fill_rrect(cr, b, RADIUS_SM, t->hover);
        Text wt = text_make(cr, FONT_SANS, 13, on ? 700 : 400, words[i], 0, DIR_RTL);
        cairo_save(cr);
        cairo_rectangle(cr, b.x + 2, b.y, b.w - 4, b.h); cairo_clip(cr);
        text_draw(cr, &wt, b.x + (b.w - wt.w) / 2, b.y + (b.h - wt.h) / 2, on ? t->action_fg : t->ink);
        cairo_restore(cr);
        text_free(&wt);
        hit_add(a, b, ids[i]);
    }
    cairo_rectangle(cr, box.x + bw, box.y + 1, N_BORDER, box.h - 2);
    draw_set(cr, t->hair); cairo_fill(cr);
    draw_border_rrect(cr, box, RADIUS_SM, t->hair, 1);
}

Rect room_drv_block_draw(App *a, cairo_t *cr, double x, double y, double w) {
    const Theme *t = a->theme;
    DrvRow r = drv_row_make(a, cr, w);
    int on = r.enabled && a->drv_on;
    double cy = y + r.row_h / 2;
    /* .swt at the inline start (right), the label after it, the hint last */
    Rect sw = { x + w - DRV_SW_W, cy - DRV_SW_H / 2, DRV_SW_W, DRV_SW_H };
    double label_r = sw.x - DRV_SW_GAP;
    if (!r.enabled) cairo_push_group(cr);                 /* disabled: drawn dim, not clickable */
    draw_fill_rrect(cr, sw, DRV_SW_H / 2, on ? t->indigo : t->field);
    draw_border_rrect(cr, sw, DRV_SW_H / 2, on ? t->indigo : t->hair, 1);
    /* .swt i{right:2px} -> .swt.on i{right:16px}: the knob travels to the left */
    double kx = sw.x + sw.w - (on ? 16 : 2) - DRV_KNOB / 2;
    cairo_arc(cr, kx, cy, DRV_KNOB / 2, 0, 2 * M_PI);
    draw_set(cr, on ? t->on_ink : t->muted); cairo_fill(cr);
    text_draw_r(cr, &r.label, label_r, cy - r.label.h / 2, t->ink);
    if (!r.enabled) { cairo_pop_group_to_source(cr); cairo_paint_with_alpha(cr, N_DIM_ALPHA); }
    text_draw_r(cr, &r.hint, label_r - r.label.w - DRV_HINT_GAP, cy - r.hint.h / 2, t->muted);
    if (r.enabled)
        hit_add(a, (Rect){ label_r - r.label.w, y, x + w - (label_r - r.label.w), r.row_h }, HIT_ROOM_DRV_SWITCH);

    Rect sel = { 0, 0, 0, 0 };
    if (on) {
        double col_w = (w - DRV_COL_GAP) / 2, top = y + r.row_h + DRV_COLS_TOP;
        Text l1 = label_make(cr, drv_pick_label(a), col_w), l2 = label_make(cr, "על אילו דיסקים", col_w);
        double lh = dmax(l1.h, l2.h);
        double rx = x + w - col_w;                        /* the first column is the right one */
        text_draw(cr, &l1, rx, top, t->muted);
        text_draw(cr, &l2, x, top, t->muted);
        sel = (Rect){ rx, top + lh + 2, col_w, field_height(cr) };
        char shown[200];
        if (a->drv_vendor[0]) snprintf(shown, sizeof shown, "%s %s", a->drv_vendor, a->drv_model);
        else snprintf(shown, sizeof shown, "%s", "בחרו דגם…");
        draw_select(a, cr, sel, shown, HIT_ROOM_DRV_MODEL);
        drv_segment_draw(a, cr, (Rect){ x, sel.y, col_w, sel.h });
        text_free(&l1); text_free(&l2);
    }
    text_free(&r.label); text_free(&r.hint);
    return sel;
}

/* .dtag: at the slot's left (the inline end), the model or "בלי"; a click
 * is room_drv_tag_click. Drawn after the slot, so its hit wins. */
void room_drv_tag_draw(App *a, cairo_t *cr, Rect slot, const Machine *m, int mi, int drawer_index) {
    if (!room_drv_active(a) || !a->drv_pick) return;
    if (drawer_index < 0 || drawer_index >= m->nroom_drawers) return;
    const Theme *t = a->theme;
    int port = m->room_drawers[drawer_index].port;
    const char *v, *md;
    int has = drv_slot(a, m, port, &v, &md);
    double h = fmin(DRV_TAG_H, slot.h - 2);
    Text tt = text_make(cr, FONT_SANS, 11, 600, has ? md : "בלי", 0, has ? DIR_LTR : DIR_RTL);
    double tw = fmin(tt.w + 2 * DRV_TAG_PAD, slot.w * .55);
    Rect tag = { slot.x + DRV_TAG_LEFT, slot.y + (slot.h - h) / 2, tw, h };
    draw_fill_rrect(cr, tag, h / 2, t->surface);
    draw_border_rrect(cr, tag, h / 2, has ? t->indigo : t->hair, 1);
    cairo_save(cr);
    cairo_rectangle(cr, tag.x + 4, tag.y, tag.w - 8, tag.h); cairo_clip(cr);
    text_draw(cr, &tt, tag.x + DRV_TAG_PAD, tag.y + (tag.h - tt.h) / 2, has ? t->ink : t->muted);
    cairo_restore(cr);
    text_free(&tt);
    hit_add(a, tag, HIT_ROOM_DTAG_BASE + mi * MAX_DRAWERS + drawer_index);
}

/* ---- the summary ------------------------------------------------------------- */

/* "דרייברים: ThinkCentre M70q Gen 3 ← 2 דיסקים ב-CLONE-01 · בלי ← CLONE-02",
 * the chosen disks of each machine grouped by model. A cut line says so. */
Text room_drv_summary_make(App *a, cairo_t *cr, double w) {
    if (!room_drv_active(a)) return (Text){ NULL, 0, 0 };
    char buf[2048];
    const char *more = " · ועוד";
    size_t cap = sizeof buf - strlen(more) - 1;
    int used = snprintf(buf, sizeof buf, "%s", "דרייברים: "), head = used, cut = 0;
    for (int mi = 0; mi < a->st.nmachines && !cut; mi++) {
        const Machine *m = &a->st.machines[mi];
        const char *name = m->name[0] ? m->name : m->mac;
        const char *gv[MAX_DRAWERS], *gm[MAX_DRAWERS];
        int gn[MAX_DRAWERS], ng = 0;
        for (int j = 0; j < m->nroom_drawers; j++) {
            const RoomDrawer *d = &m->room_drawers[j];
            if (!room_pickable(d) || !drv_selected(a, m, d)) continue;   /* #1732 */
            const char *v, *md;
            if (!drv_slot(a, m, d->port, &v, &md)) v = md = "";
            int k = 0;
            while (k < ng && !drv_same(gv[k], gm[k], v, md)) k++;
            if (k == ng) { gv[k] = v; gm[k] = md; gn[k] = 0; ng++; }
            gn[k]++;
        }
        for (int k = 0; k < ng; k++) {
            char count[32];
            if (gn[k] == 1) snprintf(count, sizeof count, "%s", "דיסק אחד");
            else snprintf(count, sizeof count, "%d דיסקים", gn[k]);
            int r = gv[k][0]
                ? snprintf(buf + used, cap - used, "%s%s ← %s ב-%s", used > head ? " · " : "", gm[k], count, name)
                : snprintf(buf + used, cap - used, "%sבלי ← %s", used > head ? " · " : "", name);
            if (r < 0 || (size_t)r >= cap - (size_t)used) { buf[used] = 0; strcat(buf, more); cut = 1; break; }
            used += r;
        }
    }
    if (used == head) snprintf(buf + used, sizeof buf - used, "%s", "אין דיסקים נבחרים");
    return rtl_block_make(cr, 12, 400, buf, w - 2 * DRV_SUM_PADX);
}

double room_drv_summary_height(const Text *t) {
    return t->layout ? DRV_SUM_TOP + 2 * DRV_SUM_PADY + t->h : 0;
}

void room_drv_summary_draw(App *a, cairo_t *cr, double x, double y, double w, Text *t) {
    if (!t->layout) return;
    const Theme *th = a->theme;
    static const double dash[2] = { 4, 3 };              /* .drvs border: 1px dashed */
    Rect box = { x, y + DRV_SUM_TOP, w, 2 * DRV_SUM_PADY + t->h };
    draw_fill_rrect(cr, box, th->radius_sm, th->surface);
    cairo_save(cr);
    cairo_set_dash(cr, dash, 2, 0);
    draw_border_rrect(cr, box, th->radius_sm, th->hair, 1);
    cairo_restore(cr);
    text_draw(cr, t, box.x + DRV_SUM_PADX, box.y + DRV_SUM_PADY, th->ink);
}
