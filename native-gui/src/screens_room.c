#include <math.h>
#include <stdio.h>
#include <string.h>
#include "canceldlg.h"
#include "lcui.h"
#include "screens_rounds.h"

/* ---- #695: visual room machine grid ------------------------------------- */

/* nativeRoom .room-node: padding 12, radius 3, border active/warn; .top row
 * = strong 11px name + .room-dot 8px; small 9px status; then the body --
 * the #695 disk slots (setup) or a .native-bar (live). */
typedef struct {
    int cols, rows;
    double gap, card_w, item_h, name_h, small_h, body_h, slot_h, total_h;
} RoomGrid;

static double node_head_h(const RoomGrid *g) { return g->name_h + 2 + g->small_h + N_LABEL_GAP; }

/* Owner, 10/10: a selected slot wears a 3px action ring 2px outside it, so
 * the slots stand SLOT_GAP apart and the stack has SLOT_RING of room above
 * and below -- rings never overlap a neighbour or the status line. */
#define SLOT_RING 5.0           /* 2px gap + 3px ring */
#define SLOT_GAP  (2 * SLOT_RING)

static const RoomDrawer *room_drawer_row(const Machine *m, int row, int *index) {
    int mapped_rows = m->nrslots > 0 ? m->nrslots : m->drawer_count;
    if (mapped_rows < 1) mapped_rows = 1;
    if (mapped_rows > MAX_DRAWERS) mapped_rows = MAX_DRAWERS;
    if (row < mapped_rows) {
        for (int i = 0; i < m->nroom_drawers; i++)
            if (m->room_drawers[i].present &&
                eff_drawer(m->room_drawers[i].drawer, m->room_drawers[i].port) == row + 1) {
                *index = i; return &m->room_drawers[i];
            }
        *index = -1; return NULL;
    }
    int wanted = row - mapped_rows;
    for (int i = 0; i < m->nroom_drawers; i++)
        if (m->room_drawers[i].present &&
            eff_drawer(m->room_drawers[i].drawer, m->room_drawers[i].port) == 0) {
            if (wanted-- == 0) { *index = i; return &m->room_drawers[i]; }
        }
    *index = -1; return NULL;
}

static int room_drawer_after(const RoomDrawer *a, const RoomDrawer *b) {
    int ad = eff_drawer(a->drawer, a->port), bd = eff_drawer(b->drawer, b->port);
    if (ad && bd) return ad > bd;
    if (ad != 0) return 0;
    if (bd != 0) return 1;
    return a->port > b->port;
}

static int room_selection_override(const App *a, const Machine *m, int port,
                                   int initial) {
    for (int i = 0; i < a->nroom_selection; i++)
        if (a->room_selection[i].port == port &&
            strcmp(a->room_selection[i].mac, m->mac) == 0)
            return a->room_selection[i].selected;
    return initial;
}

/* Machines sit side by side, right to left, and wrap to a new row only when
 * the current one is full: two or three cloners are a single row, thirty wrap
 * into several. Card height fits the deepest machine's drawer stack, so the
 * disks read as one comfortable rectangle rather than filling the whole card. */
/* max_slots 0 = the live grid: the body is one .native-bar, not slots. */
static RoomGrid room_grid_make(cairo_t *cr, double w, int n, int max_slots,
                               int wanted_cols, double budget_h) {
    RoomGrid g;
    memset(&g, 0, sizeof g);
    g.gap = N_ROOM_GAP;
    g.name_h = dmax(text_line_height(cr, FONT_SANS, N_NODE_TITLE), N_NODE_DOT);
    g.small_h = text_line_height(cr, FONT_SANS, N_NODE_SUB);
    if (n <= 0) return g;
    g.cols = n < wanted_cols ? n : wanted_cols;
    g.rows = (n + g.cols - 1) / g.cols;
    g.card_w = (w - (g.cols - 1) * g.gap) / g.cols;

    if (max_slots < 1) {                                     /* live: a 5px bar */
        g.body_h = N_LABEL_GAP + N_BAR_SM;
        g.item_h = dmax(N_ROOM_MIN_H, 2*N_ROOM_PAD + node_head_h(&g) + g.body_h);
        g.total_h = g.rows * g.item_h + (g.rows - 1) * g.gap;
        return g;
    }
    g.slot_h = N_PAD;
    g.body_h = max_slots * g.slot_h + (max_slots - 1)*SLOT_GAP + 2*SLOT_RING;
    g.item_h = 2*N_ROOM_PAD + node_head_h(&g) + g.body_h;
    g.total_h = g.rows * g.item_h + (g.rows - 1) * g.gap;
    if (g.total_h > budget_h) {                              /* many rows: shrink slots to fit */
        double avail = (budget_h - (g.rows - 1) * g.gap) / g.rows - 2*N_ROOM_PAD - node_head_h(&g);
        g.slot_h = floor((avail - (max_slots - 1)*SLOT_GAP - 2*SLOT_RING) / max_slots);
        if (g.slot_h < N_ACTION_GAP) g.slot_h = N_ACTION_GAP;
        g.body_h = max_slots * g.slot_h + (max_slots - 1)*SLOT_GAP + 2*SLOT_RING;
        g.item_h = 2*N_ROOM_PAD + node_head_h(&g) + g.body_h;
        g.total_h = g.rows * g.item_h + (g.rows - 1) * g.gap;
    }
    return g;
}

/* Any present disk of the machine that is not green (#872: fail or failed_last;
 * #432: a link slower than its port)? */
static int room_machine_has_warn(const Machine *m) {
    for (int i = 0; i < m->nroom_drawers; i++)
        if (m->room_drawers[i].present && (smart_level(m->room_drawers[i].smart) || room_drawer_red(&m->room_drawers[i]))) return 1;
    return 0;
}

/* #432: the slot's colour -- SMART first (red wins), then a slow link is
 * orange like a SMART warning: it asks, it does not block. */
static Rgb room_drawer_color(const Theme *t, const RoomDrawer *d) {
    if (room_drawer_red(d)) return t->danger;   /* #2848 (Nadav 11/11): a SATA problem or a slow write is red; #2846: a CRC count is not a colour */
    if (d->rule_set) return d->rule_color == DR_YELLOW ? t->yellow : d->rule_color == DR_ORANGE ? t->warn   /* #2848: the server's colour */
                          : d->rule_color == DR_GREEN ? t->led_ok : smart_color(t, d->smart);
    return smart_color(t, d->smart);
}

/* The node's box, its .top row (name, dot) and small line; returns the
 * content rect below them. <name_hit> registers the name as a button. */
static Rect room_node_chrome(App *a, cairo_t *cr, Rect item, const RoomGrid *g,
                             const char *name, int name_fill, int name_hit,
                             Rgb dot, Rgb line, Text *small) {
    const Theme *t = a->theme;
    draw_fill_rrect(cr, item, RADIUS_SM, t->disk_bg);
    draw_border_rrect(cr, item, RADIUS_SM, line, N_BORDER);
    double x = item.x + N_ROOM_PAD, y = item.y + N_ROOM_PAD, w = item.w - 2*N_ROOM_PAD;
    /* .top{display:flex;justify-content:space-between}: name at the start
     * (right), the dot at the end (left) */
    Text nt = text_make(cr, FONT_SANS, N_NODE_TITLE, 600, name, 0, DIR_RTL);
    double pill_w = fmin(nt.w + N_ACTION_GAP, w - N_NODE_DOT - N_ACTION_GAP);
    Rect pill = { x + w - pill_w, y, pill_w, g->name_h };
    if (name_hit) {
        draw_fill_rrect(cr, pill, RADIUS_SM, name_fill ? t->indigo : t->field);
        draw_border_rrect(cr, pill, RADIUS_SM, name_fill ? t->indigo : t->hair, 1);
        hit_add(a, pill, name_hit);
    }
    cairo_save(cr);
    cairo_rectangle(cr, pill.x, pill.y, pill.w, pill.h); cairo_clip(cr);
    text_draw_r(cr, &nt, pill.x + pill.w - N_ACTION_GAP/2, y + (g->name_h - nt.h) / 2,
                name_fill ? t->on_ink : t->ink);
    cairo_restore(cr);
    text_free(&nt);
    cairo_arc(cr, x + N_NODE_DOT/2, y + g->name_h/2, N_NODE_DOT/2, 0, 2*M_PI);
    draw_set(cr, dot); cairo_fill(cr);
    y += g->name_h + 2;
    text_draw(cr, small, x, y, t->muted);
    y += g->small_h + N_LABEL_GAP;
    return (Rect){ x, y, w, item.y + item.h - N_ROOM_PAD - y };
}

/* #714: are all present disks of this machine currently selected? */
static int room_machine_all_selected(const App *a, const Machine *m) {
    int any = 0;
    for (int i = 0; i < m->nroom_drawers; i++) {
        const RoomDrawer *d = &m->room_drawers[i];
        if (!room_pickable(d)) continue;   /* #1732 */
        any = 1;
        if (!room_selection_override(a, m, d->port, d->selected)) return 0;
    }
    return any;
}

int room_picked(const App *a, const char *mac) {
    for (int i = 0; i < a->nroom_pick; i++)
        if (!strcmp(a->room_pick[i], mac)) return 1;
    return 0;
}

static void room_machine_draw(App *a, cairo_t *cr, const Machine *m, int mi,
                              Rect item, const RoomGrid *g) {
    const Theme *t = a->theme;
    const char *name = m->name[0] ? m->name : m->mac;
    /* The name is a button (#714): click it to select/clear every disk of the
     * machine. It fills indigo when they are all selected, so the state shows.
     * Nadav 17/10: an off machine's name is a button too -- it picks the
     * machine itself for "הער", before it has reported a single drawer. */
    int picked = room_picked(a, m->mac);
    int all_sel = picked || (m->awake && room_machine_all_selected(a, m));
    int warn = m->awake && room_machine_has_warn(m);
    int nsel = 0;                                  /* owner, 10/10: say how many are picked */
    for (int i = 0; m->awake && i < m->nroom_drawers; i++)
        if (room_pickable(&m->room_drawers[i]) && room_selection_override(a, m, m->room_drawers[i].port, m->room_drawers[i].selected)) nsel++;
    char st[96];
    if (m->awake && nsel) snprintf(st, sizeof st, "ער · %d מגירות מוכנות · %d נבחרו", m->fresh_drawers, nsel);
    else if (m->awake)    snprintf(st, sizeof st, "ער · %d מגירות מוכנות%s", m->fresh_drawers, picked ? " · נבחר" : "");
    else                  snprintf(st, sizeof st, "%s", picked ? "כבוי · נבחר להערה" : "כבוי");
    Text small = text_make(cr, FONT_SANS, N_NODE_SUB, 400, st, (int)(item.w - 2*N_ROOM_PAD), DIR_RTL);
    Rect card = room_node_chrome(a, cr, item, g, name, all_sel, HIT_ROOM_MACHINE_BASE + mi,
                                 m->awake ? (warn ? t->warn : t->led_ok) : t->hair,
                                 !m->awake ? t->disk_line : warn ? t->node_warn_line : t->node_active_line,
                                 &small);
    text_free(&small);
    if (nsel || picked) draw_border_rrect(cr, item, RADIUS_SM, t->indigo, 2);   /* a picked machine */

    int slots = room_rows(m);
    int mapped_rows = m->nrslots > 0 ? m->nrslots : m->drawer_count;
    if (mapped_rows < 1) mapped_rows = 1;
    if (mapped_rows > MAX_DRAWERS) mapped_rows = MAX_DRAWERS;

    double sx = card.x, sy = card.y + SLOT_RING, sw = card.w;
    for (int row = 0; row < slots; row++) {
        int j = -1, drawer = row + 1, port = drawer;
        const RoomDrawer *d = room_drawer_row(m, row, &j);
        int mapped = row < mapped_rows;
        Rect slot = { sx, sy + row * (g->slot_h + SLOT_GAP), sw, g->slot_h };
        int selected = d && room_pickable(d) && room_selection_override(a, m, d->port, d->selected);   /* #1732 */
        int avail = m->awake && d && room_selectable(d);
        /* owner, 10/10: picked vs not must read at a glance. Picked = the
         * SMART colour solid, a 3px action ring 2px outside and a check at
         * the start. Available but not picked = the same colour lightened
         * (45% surface) inside a border of the colour itself -- the SMART
         * meaning (green ok / orange warn, failed_last / red SMART fail, #1580) stays. */
        Rgb smart = avail ? room_drawer_color(t, d) : t->led_idle;
        Rgb light = { smart.r * .55 + t->surface.r * .45, smart.g * .55 + t->surface.g * .45,
                      smart.b * .55 + t->surface.b * .45 };
        if (avail && selected) {
            draw_fill_rrect(cr, slot, RADIUS_SM, smart);
            Rect ring = { slot.x - SLOT_RING, slot.y - SLOT_RING, slot.w + 2 * SLOT_RING, slot.h + 2 * SLOT_RING };
            draw_border_rrect(cr, ring, RADIUS_SM + SLOT_RING, t->indigo, 3);
        } else if (avail) {
            draw_fill_rrect(cr, slot, RADIUS_SM, light);
            draw_border_rrect(cr, slot, RADIUS_SM, smart, 2);
        } else {
            draw_fill_rrect(cr, slot, RADIUS_SM, t->led_idle);
            draw_border_rrect(cr, slot, RADIUS_SM, t->hair, 1);
        }

        /* #704: the operator's name of the slot -- "name · capacity" on a disk,
         * the name alone (muted) on an empty or unreachable slot. No name =
         * the capacity alone, exactly as before. */
        const char *sname = "";
        if (mapped && m->nrslots > 0) sname = m->rslots[drawer - 1].name;
        else if (mapped) sname = m->slot_names[port - 1];
        const char *dname = d && d->slot_name[0] ? d->slot_name : sname;
        if (!avail && mapped && sname[0] && !(d && m->awake)) {
            Text nt = text_make(cr, FONT_SANS, g->slot_h >= N_CLONE_TITLE ? N_HEADER_FONT : N_IMAGE_SUB,
                                500, sname, 0, DIR_RTL);
            cairo_save(cr);
            cairo_rectangle(cr, slot.x + 2, slot.y + 1, slot.w - 4, slot.h - 2);
            cairo_clip(cr);
            text_draw(cr, &nt, slot.x + (slot.w - nt.w) / 2, slot.y + (slot.h - nt.h) / 2, t->muted);
            cairo_restore(cr);
            text_free(&nt);
        }
        /* #1495: a mapped row says "name · capacity" or the capacity alone, as
         * before (the row is the number); a row not in the mapping says
         * "SATA n (לא ממופה)" -- it cannot be picked. */
        if (d && (m->awake || !mapped)) {
            char capacity[32] = "", named[SLOT_NAME_MAX + 128], label[SLOT_NAME_MAX + 64];
            fmt_bytes(capacity, sizeof capacity, (double)d->size_bytes);
            room_drawer_label(m, d, label, sizeof label);
            if (!mapped) snprintf(named, sizeof named, "%s", label);
            else if (dname[0]) snprintf(named, sizeof named, "%s · %s", dname, capacity);
            else snprintf(named, sizeof named, "%s", capacity);
            Text ct = text_make(cr, FONT_SANS, g->slot_h >= N_CLONE_TITLE ? N_HEADER_FONT : N_IMAGE_SUB,
                                selected ? 700 : 600, named, 0,
                                !mapped || dname[0] ? DIR_RTL : DIR_LTR);
            cairo_save(cr);
            cairo_rectangle(cr, slot.x + 2, slot.y + 1, slot.w - 4, slot.h - 2);
            cairo_clip(cr);
            text_draw(cr, &ct, slot.x + (slot.w - ct.w) / 2,
                      slot.y + (slot.h - ct.h) / 2, selected ? t->on_ink : !avail ? t->muted : t->ink);
            if (selected && slot.h >= 12) {          /* the check at the slot's start (right, RTL) */
                double h = fmin(slot.h * .5, 14), cx = slot.x + slot.w - 8 - h, cy = slot.y + (slot.h - h) / 2;
                cairo_move_to(cr, cx + h * .12, cy + h * .52);
                cairo_line_to(cr, cx + h * .40, cy + h * .80);
                cairo_line_to(cr, cx + h * .90, cy + h * .20);
                cairo_set_line_width(cr, 2.2);
                cairo_set_line_cap(cr, CAIRO_LINE_CAP_ROUND);
                draw_set(cr, t->on_ink); cairo_stroke(cr);
            }
            cairo_restore(cr);
            text_free(&ct);
        }

        /* Empty slots and every slot on an off machine are display-only. */
        if (avail)
            hit_add(a, slot, HIT_ROOM_DISK_BASE + mi * MAX_DRAWERS + j);
        /* #1269: a chosen disk's drivers tag in pick mode -- after the slot, so its hit wins */
        if (avail && selected) room_drv_tag_draw(a, cr, slot, m, mi, j);
    }
}

/* #1235: a drawer with no serial (fresh null, #105). The round counts by
 * serial, so it cannot tell whether that drive was written: say so by slot
 * instead of drawing it as one more drawer (rule 5). One line per drawer;
 * when they do not all fit, the last line says that more were left out --
 * never a silent cut. Text{NULL} when every drawer is identified. */
static Text room_unidentified_make(cairo_t *cr, const State *s, double w) {
    char buf[2048], *line;
    const char *more = "\nועוד כוננים שלא ניתן לזהות — ראו בקונסולה";
    size_t used = 0, cap = sizeof buf - strlen(more) - 1;
    buf[0] = 0;
    for (int i = 0; i < s->nmachines; i++) {
        const Machine *m = &s->machines[i];
        for (int j = 0; j < m->nroom_drawers; j++) {
            const RoomDrawer *d = &m->room_drawers[j];
            if (!d->present || !d->unidentified) continue;
            line = buf + used;
            char label[SLOT_NAME_MAX + 64]; room_drawer_label(m, d, label, sizeof label);
            int k = snprintf(line, cap - used, "%sלא ניתן לזהות את הכונן — %s · %s",
                              used ? "\n" : "", label, m->name[0] ? m->name : m->mac);
            if (k < 0 || (size_t)k >= cap - used) {
                snprintf(line, sizeof buf - used, "%s", more);
                return text_make(cr, FONT_SANS, N_SUB, 600, buf, (int)w, DIR_RTL);
            }
            used += (size_t)k;
        }
    }
    if (!used) return (Text){ NULL, 0, 0 };
    return text_make(cr, FONT_SANS, N_SUB, 600, buf, (int)w, DIR_RTL);
}

/* First machine is physically rightmost. Index increases leftward, then the
 * next visual row begins again at the right. */
static void room_grid_draw(App *a, cairo_t *cr, double x, double y,
                           double w, const RoomGrid *g) {
    if (a->st.nmachines == 0) {
        Text empty = sub_make(cr,
            "אין מכונות שיכפול רשומות — מוסיפים אותן בקונסולה, ואז מעירים אותן.", w);
        text_draw_r(cr, &empty, x + w, y, a->theme->muted);
        text_free(&empty);
        return;
    }
    for (int i = 0; i < a->st.nmachines; i++) {
        int row = i / g->cols, col = i % g->cols;
        double mx = x + w - g->card_w - col * (g->card_w + g->gap);
        double my = y + row * (g->item_h + g->gap);
        room_machine_draw(a, cr, &a->st.machines[i], i,
                          (Rect){ mx, my, g->card_w, g->item_h }, g);
    }
}

/* Nadav 17/10 ("גרסה ב׳"): the live round in the local clone's design -- one
 * cage per cloner of the round, a row per drawer it writes (lcbay.c). Which
 * drawer is a target: its own target state (room_drawer fields 16-17); an
 * older agent has none, and every present drawer takes the machine's. */
static int live_target(const RoomDrawer *d) {
    return d->present && (!d->own_progress || d->tstate[0] || d->selected);
}

static int live_kind(const Machine *m, const RoomDrawer *d) {
    const char *st = d->own_progress ? d->tstate : m->state;
    if (!strcmp(st, "cancelled")) return BAY_CANCELED;                           /* #1386 */
    if (!strcmp(st, "writing") && d->cancel_text[0]) return BAY_CANCELING;
    if (!strcmp(st, "writing") || !strcmp(st, "verifying")) return BAY_WRITING;
    if (!strcmp(st, "done")) return BAY_DONE;
    if (!strcmp(st, "failed")) return BAY_FAILED;
    if (!strcmp(st, "skipped")) return BAY_SKIPPED;
    return BAY_WAITING;
}

static int live_pct(const Machine *m, const RoomDrawer *d) {
    int k = live_kind(m, d);
    if (k == BAY_DONE) return 100;
    if (k == BAY_SKIPPED || k == BAY_CANCELED || k == BAY_CANCELING) return -1;   /* #1386: empty, not "16%" */
    return d->own_progress ? d->pct : m->pct;
}

static void live_bay(const Theme *t, const State *s, const Machine *m, const RoomDrawer *d, LcBay *y) {
    memset(y, 0, sizeof *y);
    y->kind = live_kind(m, d);
    y->drawer = eff_drawer(d->drawer, d->port);
    room_drawer_label(m, d, y->name, sizeof y->name);
    char sz[32] = "", mi[96] = "";
    fmt_bytes(sz, sizeof sz, (double)d->size_bytes);
    if (d->model[0]) bidi_isolate(d->model, mi, sizeof mi);
    snprintf(y->sub, sizeof y->sub, "%s%s%s", sz, mi[0] ? " · " : "", mi);
    /* #432/#1366/#652: the drawer's health, the grid's colour (room_drawer_color: SMART
     * first, then a slow link or a risen CRC); words only when it is not green */
    y->toned = 1; y->tone = room_drawer_color(t, d);
    /* #2848: red / yellow say the server's rule in its words (disk_rule.c, the same sentence as its refusal) */
    y->note = smart_level(d->smart) == 2 ? smart_he_why(d->smart, d->smart_reason)   /* #1742 */
            : d->rule_set && (d->rule_color == DR_RED || d->rule_color == DR_YELLOW) && d->rule_reason[0] ? disk_rule_he(d->rule_reason)
            : d->link_slow ? "קישור SATA איטי" : smart_level(d->smart) ? smart_he_why(d->smart, d->smart_reason) : NULL;
    int pct = live_pct(m, d);
    if (pct >= 0) { snprintf(y->pct, sizeof y->pct, "%d%%", pct > 100 ? 100 : pct); y->share = pct / 100.0; }
    else { snprintf(y->pct, sizeof y->pct, "%s", "—"); y->share = -1; }
    switch (y->kind) {
    case BAY_WRITING:
        if (s->rate_bps > 0) { char r[48], ri[64]; snprintf(r, sizeof r, "%.0f MB/s", s->rate_bps / 1e6);
            bidi_isolate(r, ri, sizeof ri); snprintf(y->line, sizeof y->line, "כותב · %s", ri); }
        else snprintf(y->line, sizeof y->line, "כותב");
        break;
    case BAY_DONE:   y->pill = "הושלם"; snprintf(y->line, sizeof y->line, "הושלם · נבדק"); break;   /* #1331 mockup v2 */
    case BAY_FAILED:
        y->pill = "נכשל";
        {   /* the machine's error is "sdb: <why>; sdc: <why>" -- the why, without the device */
            const char *why = m->error, *colon = strstr(why, ": ");
            if (colon && colon - why < 12) why = colon + 2;
            snprintf(y->line, sizeof y->line, "נכשל%s%s", why[0] ? ": " : "", why);
        }
        break;
    case BAY_SKIPPED: y->pill = "דולג"; snprintf(y->line, sizeof y->line, "דולג לפי הכרעת SMART"); break;
    case BAY_CANCELED:   /* #1386: the server's "בוטל על ידי … · 16:06" */
        y->pill = "בוטל"; y->toned = 0;
        snprintf(y->line, sizeof y->line, "%s · טבלת המחיצות נמחקה, ריק", d->cancel_text[0] ? d->cancel_text : "בוטל");
        break;
    case BAY_CANCELING: y->pill = "מבטל…"; y->toned = 0;
        snprintf(y->line, sizeof y->line, "עוצר את הכתיבה ומוחק את טבלת המחיצות…"); break;
    default: snprintf(y->line, sizeof y->line, "%s", s->wave_open ? "ממתין שהגל יצא" : "ממתין לתחילת הזרם");
    }
    /* #1331 (mockup v2): the drawer's drivers, under its bar */
    if (d->drv_state[0]) lc_bay_drivers(y, d->drv_state, d->drv_detail, d->drv_boot, d->drv_bsvc, d->drv_berr);
    if (d->drv_lstate[0]) lc_bay_linux(y, d->drv_lstate, d->drv_lmod, d->drv_ldet);   /* #1331 T8 */
}

/* #1386: the drawers "בטל דיסק זה" can name on this screen -- writing now (own
 * target state), with a device, not already asked -- in cage and row order. A
 * row's button is HIT_BAY_CANCEL_BASE + its index here. */
static int live_bays(const Machine *m, const RoomDrawer **out);
static int live_cancelable(const RoomDrawer *d) {
    return d->own_progress && !strcmp(d->tstate, "writing") && d->dev[0] && !d->cancel_text[0];
}

int room_cancel_picks(const App *a, CancelPick *out, int max) {
    const State *s = &a->st; int k = 0;
    if (!s->has_round) return 0;
    for (int i = 0; i < s->nmachines; i++) {
        const Machine *m = &s->machines[i]; const RoomDrawer *b[MAX_DRAWERS];
        int nb = live_bays(m, b);
        for (int j = 0; j < nb && k < max; j++) {
            if (!live_cancelable(b[j])) continue;
            CancelPick *p = &out[k++]; memset(p, 0, sizeof *p);
            snprintf(p->mac, sizeof p->mac, "%s", m->mac); snprintf(p->dev, sizeof p->dev, "%s", b[j]->dev);
            room_drawer_label(m, b[j], p->label, sizeof p->label);
            p->drawer = eff_drawer(b[j]->drawer, b[j]->port);
            if (p->drawer > 0) snprintf(p->where, sizeof p->where, "מחשב שיכפול %s · מגירה %d", m->name[0] ? m->name : m->mac, p->drawer);
            else snprintf(p->where, sizeof p->where, "מחשב שיכפול %s", m->name[0] ? m->name : m->mac);
            snprintf(p->model, sizeof p->model, "%s", b[j]->model);
            fmt_bytes(p->size, sizeof p->size, (double)b[j]->size_bytes);
            p->pct = b[j]->pct;
        }
    }
    return k;
}

/* the drawers of a machine that the round writes, in drawer order */
static int live_bays(const Machine *m, const RoomDrawer **out) {
    int n = 0;
    for (int j = 0; j < m->nroom_drawers; j++) if (live_target(&m->room_drawers[j])) {
        const RoomDrawer *v = &m->room_drawers[j]; int k = n;
        while (k > 0 && room_drawer_after(out[k - 1], v)) { out[k] = out[k - 1]; k--; }
        out[k] = v; n++;
    }
    return n;
}

/* mockup 10/10 ("חדר + סבב חי"): "עצור סבב…" stands apart at the start as
 * red words; it opens the #1270 confirm dialog (the red "עצור סבב" is
 * there). The rest sit at the inline end, the primary last. */
/* #989 item 3: a wave that finished with work left -- the next one is open and
 * waiting for replaced disks. Same test as the console (roomWaveProgress). */
static int wave_ended(const State *s) { return s->wave_open && s->wave_number > 1 && s->remaining > 0; }

static void live_footer(App *a, cairo_t *cr, Rect foot, int boot_red) {
    const Theme *t = a->theme; const State *s = &a->st;
    double bh = btn_height(cr), by = foot.y + N_ACTION_TOP;
    Text stop = btn_label(cr, "עצור סבב…");
    double sw = btn_width(&stop), sx = foot.x + foot.w - N_PAD - sw;
    draw_btn(a, cr, (Rect){ sx, by, sw, bh }, &stop, BTN_GHOST_DANGER, HIT_ROOM_CLOSE);
    text_free(&stop);
    if(a->admin){
        Text all=btn_label(cr,"כבה את כל החדר");double aw=btn_width(&all);sx-=aw+8;
        draw_btn(a,cr,(Rect){sx,by,aw,bh},&all,BTN_GHOST_DANGER,HIT_ROOM_POWEROFF_ALL);text_free(&all);
    }
    CancelPick picks[MAX_CANCEL_PICKS];
    int ncancel = room_cancel_picks(a, picks, MAX_CANCEL_PICKS);
    if (ncancel) {   /* #1386: "בטל דיסק…" -- X; the window chooses among the writers */
        Text cx = btn_label(cr, "בטל דיסק…  X"); double cw = btn_width(&cx); sx -= cw + 8;
        draw_btn(a, cr, (Rect){ sx, by, cw, bh }, &cx, BTN_GHOST_DANGER, HIT_CDLG_OPEN); text_free(&cx);
    }
    Text labels[3]; int kinds[3], ids[3], nb = 0;     /* from the inline end: the primary first */
    if (s->wave_open) { labels[nb] = btn_label(cr, "התחל עכשיו"); kinds[nb] = BTN_PRIMARY; ids[nb++] = HIT_ROOM_START; }
    labels[nb] = btn_label(cr, wave_ended(s) ? "הער את מחשבי השיכפול" : "העֵר שוב"); kinds[nb] = BTN_PLAIN; ids[nb++] = HIT_ROOM_WAKE;
    labels[nb] = btn_label(cr, "חזרה");     kinds[nb] = BTN_PLAIN; ids[nb++] = HIT_BACK;
    double x = foot.x + N_PAD;
    for (int i = 0; i < nb; i++) {
        double bw = btn_width(&labels[i]);
        draw_btn(a, cr, (Rect){ x, by, bw, bh }, &labels[i], kinds[i], ids[i]);
        x += bw + 8;
        text_free(&labels[i]);
    }
    double room = sx - 12 - (x + 4);
    if (room > 80) {
        /* #1331: a disk that may not boot outranks the stop hint (and #1386's cancel hint) */
        Text h = rtl_block_make(cr, 12, 400, boot_red ? "הדיסק האדום — לבדוק לפני שמחזירים לכיתה"
                                           : ncancel ? "עצירה באמצע — אחרי אישור בחלון · ביטול דיסק אחד — הגל ממשיך"
                                                     : "עצירה באמצע — אחרי אישור בחלון", room);
        text_draw_r(cr, &h, sx - 12, by + (bh - h.h) / 2, t->muted);
        text_free(&h);
    }
}

/* #432 + #65: what an open wave says before it starts, in one orange box
 * (the standby note's language: warning_soft, a warn line, orange dots).
 *   - round.precheck: a drawer on a slow link or a risen CRC -- a warning,
 *     never a block; "התחל" then asks (CONFIRM_ROOM_START_WARN).
 *   - round.missing: who the wave is waiting for, with "כבה אותו" for one
 *     that still answers, and the other two choices of the spec: "התחל בכל
 *     זאת" (room-start) and "בטל את הסבב" (the #1270 stop dialog).
 * The lines are the server's text_he, the same words as the console.
 * draw = 0 measures only (no hits), so the card can be sized first. */
static double room_pre_block(App *a, cairo_t *cr, double x, double y, double w, int draw) {
    const Theme *t = a->theme; const State *s = &a->st;
    if (!s->wave_open || (!s->nroom_warn && !s->nroom_missing)) return 0;
    double pad = N_ALERT_PAD_X, inner = w - 2 * pad - 14, cy = y + N_ALERT_PAD_Y, bh = btn_height(cr);
    double right = x + w - pad;
    if (draw) {
        double h = room_pre_block(a, cr, x, y, w, 0);
        Rect box = { x, y, w, h };
        draw_fill_rrect(cr, box, RADIUS_SM, t->warning_soft);
        draw_border_rrect(cr, box, RADIUS_SM, t->warn, 1);
    }
    char line[400];
    if (s->nroom_warn) {
        snprintf(line, sizeof line, "לפני ההתחלה — %d %s בגל עם קישור חשוד",
                 s->nroom_warn + s->hidden_room_warn, s->nroom_warn + s->hidden_room_warn == 1 ? "מגירה" : "מגירות");
        Text h = rtl_block_make(cr, N_SUB, 600, line, inner + 14);
        if (draw) text_draw_r(cr, &h, right, cy, t->warning_ink);
        cy += h.h + 6; text_free(&h);
        for (int i = 0; i < s->nroom_warn; i++) {
            char rl[sizeof s->room_warn[0] + 4];     /* RLM: "CLONE-01 · ..." stays an RTL line */
            snprintf(rl, sizeof rl, RLM "%s", s->room_warn[i]);
            Text l = rtl_block_make(cr, N_SUB, 400, rl, inner);
            if (draw) {
                cairo_arc(cr, right - 4, cy + l.h / 2, 4, 0, 2 * M_PI); draw_set(cr, t->warn); cairo_fill(cr);
                text_draw_r(cr, &l, right - 14, cy, t->warning_ink);
            }
            cy += l.h + 4; text_free(&l);
        }
        if (s->hidden_room_warn) {
            snprintf(line, sizeof line, "ועוד %d — ראו בקונסולה", s->hidden_room_warn);
            Text l = rtl_block_make(cr, N_SUB, 400, line, inner);
            if (draw) text_draw_r(cr, &l, right - 14, cy, t->warning_ink);
            cy += l.h + 4; text_free(&l);
        }
        Text f = rtl_block_make(cr, N_SUB, 400, "הגל ייכתב גם כך — אזהרה, לא חסימה. התחלה עם האזהרה נרשמת ביומן.", inner + 14);
        if (draw) text_draw_r(cr, &f, right, cy, t->muted);
        cy += f.h + (s->nroom_missing ? N_ACTION_GAP : 0); text_free(&f);
    }
    if (s->nroom_missing) {
        int n = s->nroom_missing + s->hidden_room_missing;
        if (n == 1) snprintf(line, sizeof line, "חסרים בגל: מחשב אחד — %d מגירות מוכנות, והגל לא יוצא מקוצר מעצמו.", s->ready);
        else snprintf(line, sizeof line, "חסרים בגל: %d מחשבים — %d מגירות מוכנות, והגל לא יוצא מקוצר מעצמו.", n, s->ready);
        Text h = rtl_block_make(cr, N_SUB, 600, line, inner + 14);
        if (draw) text_draw_r(cr, &h, right, cy, t->warning_ink);
        cy += h.h + 6; text_free(&h);
        for (int i = 0; i < s->nroom_missing; i++) {
            Text off = btn_label(cr, "כבה אותו");
            double ow = s->room_missing[i].can_poweroff ? btn_width(&off) : 0;
            char rl[sizeof s->room_missing[0].text + 4];
            snprintf(rl, sizeof rl, RLM "%s", s->room_missing[i].text);
            Text l = rtl_block_make(cr, N_SUB, 400, rl, inner - (ow ? ow + N_ACTION_GAP : 0));
            double row = ow ? fmax(l.h, bh) : l.h;
            if (draw) {
                cairo_arc(cr, right - 4, cy + row / 2, 4, 0, 2 * M_PI); draw_set(cr, t->warn); cairo_fill(cr);
                text_draw_r(cr, &l, right - 14, cy + (row - l.h) / 2, t->warning_ink);
                if (ow) draw_btn(a, cr, (Rect){ x + pad, cy, ow, bh }, &off, BTN_GHOST_DANGER, HIT_ROOM_POWEROFF_BASE + i);
            }
            cy += row + 4; text_free(&l); text_free(&off);
        }
        if (s->hidden_room_missing) {
            snprintf(line, sizeof line, "ועוד %d — ראו בקונסולה", s->hidden_room_missing);
            Text l = rtl_block_make(cr, N_SUB, 400, line, inner);
            if (draw) text_draw_r(cr, &l, right - 14, cy, t->warning_ink);
            cy += l.h + 4; text_free(&l);
        }
        /* the spec's other two choices, at the inline end: start anyway, cancel */
        cy += 4;
        if (draw) {
            Text go = btn_label(cr, "התחל בכל זאת"), no = btn_label(cr, "בטל את הסבב…");
            double gw = btn_width(&go), nw = btn_width(&no);
            draw_btn(a, cr, (Rect){ x + pad, cy, gw, bh }, &go, BTN_WARN, HIT_ROOM_START);
            draw_btn(a, cr, (Rect){ x + pad + gw + 8, cy, nw, bh }, &no, BTN_GHOST_DANGER, HIT_ROOM_CLOSE);
            text_free(&go); text_free(&no);
        }
        cy += bh;
    }
    return cy + N_ALERT_PAD_Y - y;
}

void screen_room(App *a, cairo_t *cr, double W, double H, double head_h) {
    const Theme *t = a->theme;
    const State *s = &a->st;
    double inner = card_width(W) - 2 * N_PAD, in_h = field_height(cr), btn_h = btn_height(cr);
    int ready = 0;
    int room_cols = H / W >= .7 ? 4 : 6;
    for (int i = 0; i < s->nmachines; i++) if (s->machines[i].awake) ready += s->machines[i].fresh_drawers;

    if (!s->has_round) {
        /* ---- renderSetup ---- */
        /* #715: MODE_DIRECT is this screen without the image select and the
         * drive-count field -- the source is this machine's disk, and the
         * target is exactly the drawers ticked in the grid (a single round). */
        int direct = a->mode == MODE_DIRECT;
        if (!a->room_target_set) {                        /* value="${ready || 24}", once per skeleton */
            snprintf(a->room_target, sizeof a->room_target, "%d", ready ? ready : 24);
            a->room_target_set = 1;
        }
        Head hd = direct
            ? head_make(cr, "הפצה מהדיסק הזה למחשבי השיכפול",
                        "הדיסק של המחשב הזה נקרא פעם אחת (קריאה בלבד) ומשודר ישירות למגירות שמסומנות למטה — בלי לשמור אימג' בשרת. סבב יחיד.", inner)
            : head_make(cr, "הפצה למחשבי שיכפול",
                        "בוחרים אימג' ויעד כוננים; החדר מתעורר, וכל גל כותב למגירות שמוכנות.", inner);
        Text l1 = label_make(cr, direct ? "המקור: הדיסק הפנימי של המחשב הזה" : "אימג' לשידור", inner);
        Text l2 = label_make(cr, "כמה כוננים צריך הפעם, סך הכל", inner);
        char rd[96]; snprintf(rd, sizeof rd, "כרגע ערים: %d מגירות מוכנות.", ready);
        Text ready_t = sub_make(cr, rd, inner);
        int max_slots = 1;
        for (int i = 0; i < s->nmachines; i++)
            if (room_rows(&s->machines[i]) > max_slots) max_slots = room_rows(&s->machines[i]);
        RoomGrid grid = room_grid_make(cr, inner, s->nmachines, max_slots,
                                       room_cols, (H-head_h-N_STATUS_H)/2);
        Text err = error_make(cr, s->room_error, inner);
        double err_h = dmax(err.h, ERROR_MIN_H);
        Text unid = room_unidentified_make(cr, s, inner);
        double unid_h = unid.layout ? unid.h + N_LABEL_GAP : 0;
        /* 17/10: the last round failed -- one line on top, the picker stays usable */
        char fbuf[360]; Text failed = { NULL, 0, 0 };
        if (s->room_failed[0]) {
            snprintf(fbuf, sizeof fbuf, "הסבב הקודם נכשל: %s", s->room_failed);
            failed = text_make(cr, FONT_SANS, N_SUB, 600, fbuf, (int)inner, DIR_RTL);
        }
        double failed_h = failed.layout ? failed.h + N_FORM_GAP : 0;
        double grid_h = s->nmachines ? grid.total_h : text_line_height(cr, FONT_SANS, N_SUB);
        double sel_all_h = s->nmachines ? btn_h : 0;
        double ready_line_h = dmax(ready_t.h, sel_all_h);
        double fields_h = direct ? l1.h + N_FORM_GAP : l1.h + 2 + in_h + N_FORM_GAP + l2.h + 2 + in_h + N_FORM_GAP;
        /* #1269: "להוסיף דרייברים" under the fields, its summary under the grid */
        double drv_h = room_drv_block_height(a, cr, inner);
        Text drv_sum = room_drv_summary_make(a, cr, inner);
        double drv_sum_h = room_drv_summary_height(&drv_sum);
        double body_h = N_PAD + failed_h + fields_h + drv_h + ready_line_h + N_ACTION_GAP + grid_h + N_LABEL_GAP + drv_sum_h + unid_h + err_h + N_PAD;
        Text labels[3] = { btn_label(cr, "פתח סבב והער את המחשבים שנבחרו")   /* 17/10: the server wakes only them */, btn_label(cr, "העֵר את המחשבים שנבחרו"), btn_label(cr, "חזרה") };
        /* Nadav 17/10 (over 16/10's grey button, which sent nothing): it wakes
         * the picked cloners -- whole machines and machines with a ticked
         * drawer -- and only them; none picked says so (room_wake, main.c). */
        static const int kinds[3] = { BTN_PRIMARY, BTN_PLAIN, BTN_PLAIN };
        static const int ids[3] = { HIT_ROOM_OPEN, HIT_ROOM_WAKE, HIT_BACK };

        Rect body, foot;
        card_frame(a, cr, W, H, head_h, &hd, body_h, foot_height(cr, btn_h), &body, &foot);
        body_clip_begin(a, cr, body);
        double x = body.x + N_PAD, y = body.y + N_PAD;
        if (failed.layout) { text_draw_r(cr, &failed, x + inner, y, t->danger_fg); y += failed_h; }
        const char *opts[MAX_IMAGES + 1]; char obuf[MAX_IMAGES][168];
        int nopt = image_options(a, opts, obuf, MAX_IMAGES + 1);
        Rect sel_r = { x, y + l1.h + 2, inner, in_h };
        if (direct) {
            text_draw_r(cr, &l1, x + inner, y, t->ink);  y += l1.h + N_FORM_GAP;
        } else {
            text_draw(cr, &l1, x, y, t->muted);              y += l1.h + 2;
            draw_select(a, cr, sel_r, opts[a->image_sel < nopt ? a->image_sel : 0], HIT_ROOM_IMAGE);
            y += in_h + N_FORM_GAP;
            text_draw(cr, &l2, x, y, t->muted);              y += l2.h + 2;
            draw_field(a, cr, (Rect){ x, y, inner, in_h }, a->room_target, 0, NULL, HIT_ROOM_TARGET);
            y += in_h + N_FORM_GAP;
        }
        Rect drv_sel = room_drv_block_draw(a, cr, x, y, inner);   y += drv_h;   /* #1269 */
        /* ready count on the right; a select/clear-all button on the left (#714) */
        if (s->nmachines) {
            Text sa = btn_label(cr, "בחר / נקה הכל");
            double saw = btn_width(&sa);
            draw_btn(a, cr, (Rect){ x, y, saw, btn_h }, &sa, BTN_PLAIN, HIT_ROOM_SELECT_ALL);
            text_free(&sa);
        }
        text_draw_r(cr, &ready_t, x + inner, y + (ready_line_h - ready_t.h) / 2, t->ink);
        y += ready_line_h + N_ACTION_GAP;
        room_grid_draw(a, cr, x, y, inner, &grid);       y += grid_h + N_LABEL_GAP;
        room_drv_summary_draw(a, cr, x, y, inner, &drv_sum);   y += drv_sum_h;
        if (unid.layout) { text_draw_r(cr, &unid, x + inner, y, t->warning_ink); y += unid_h; }
        text_draw(cr, &err, x, y, t->danger_fg);
        body_clip_end(a, cr);
        foot_draw_bg(cr, t, foot);
        foot_buttons(a, cr, foot, labels, kinds, ids, 3);
        card_end(a, cr);
        if (!direct && a->dd_open == HIT_ROOM_IMAGE) draw_popup(a, cr, sel_r, opts, nopt, a->image_sel);   /* outside the card's clip, topmost */
        if (a->dd_open == HIT_ROOM_DRV_MODEL && drv_sel.w > 0) {                                           /* #1269 */
            const char *dopts[MAX_DRIVER_MODELS]; char dbuf[MAX_DRIVER_MODELS][200];
            int dn = room_drv_options(a, dopts, dbuf, MAX_DRIVER_MODELS), dsel = -1;
            for (int i = 0; i < s->ndriver_models; i++)
                if (!strcmp(s->driver_models[i].vendor, a->drv_vendor) && !strcmp(s->driver_models[i].model, a->drv_model)) dsel = i;
            draw_popup(a, cr, drv_sel, dopts, dn, dsel);
        }
        text_free(&l1); text_free(&l2); text_free(&ready_t); text_free(&err); text_free(&unid); text_free(&drv_sum);
        text_free(&failed);
        return;
    }

    /* ---- renderLive: the local clone's design (Nadav 17/10, "גרסה ב׳") ---- */
    double cw = fmin(980, W - 48), x0 = (W - cw) / 2, xr = x0 + cw, y = head_h + 22;
    /* 17/10 (metal): a failed wave said "משדר" and "הגל משדר"; only a running
     * wave (or an older agent's record, which has no wave state) says so. */
    int wave_failed = !strcmp(s->wave_state, "failed");
    int wave_over = !s->wave_open && s->wave_state[0] && strcmp(s->wave_state, "running");
    int ndisk = 0, nmach = 0, nw = 0, nf = 0, nsk = 0, sum = 0, known = 0;
    /* #1331: the drivers -- placed, not placed, no model; the disk that may not boot */
    int dv_line = 0, dv_any = 0, dv_ok = 0, dv_no = 0, dv_nomodel = 0, dv_bad = 0;
    char bad_name[SLOT_NAME_MAX + 160] = "";
    for (int i = 0; i < s->nmachines; i++) {
        const Machine *m = &s->machines[i]; const RoomDrawer *bays[MAX_DRAWERS];
        int nb = live_bays(m, bays); if (nb) nmach++;
        for (int k = 0; k < nb; k++) {
            LcBay dg; live_bay(t, s, m, bays[k], &dg);
            if (dg.drv_tone) dv_line = 1;   /* #1331 T8: the Linux line needs the tall row, not the Windows summary */
            if (dg.drv_tone && dg.drv_tone <= DRV_BAD) {
                dv_any = 1; dv_ok += !strcmp(bays[k]->drv_state, "staged"); dv_no += dg.drv_tone == DRV_WARN;
                dv_nomodel += !strcmp(bays[k]->drv_state, "skipped") && !strcmp(bays[k]->drv_detail, "no_model");
                if (dg.drv_tone == DRV_BAD && !dv_bad++) snprintf(bad_name, sizeof bad_name, "%s במחשב %s", dg.name, m->name[0] ? m->name : m->mac);
            }
            int kind = live_kind(m, bays[k]), pct = live_pct(m, bays[k]); ndisk++;
            nw += kind == BAY_WRITING; nf += kind == BAY_FAILED; nsk += kind == BAY_SKIPPED;
            if (pct >= 0 && (kind == BAY_WRITING || kind == BAY_DONE)) { sum += pct; known++; }
        }
    }
    char label[400], h1[200], s2[700], big[24], left[96], age[64], image[112];
    /* #410: how old this picture is, next to the wave -- a state file that
     * stopped changing shows a growing number, not a current-looking screen. */
    pace_age(s->updated, age, sizeof age);
    bidi_isolate(s->round_image, image, sizeof image);   /* a Latin-first name stays one run */
    /* #1331 (mockup v2): a wave that ended does not say "משדר" -- a failed one still says "נכשל"; a round with drivers says so */
    snprintf(label, sizeof label, RLM "הפצה למחשבי שיכפול · %s%s · גל %d%s%s%s", wave_failed ? "נכשל: " : wave_over ? "" : "משדר: ",
             image, s->wave_number, dv_any ? " · חבילת דרייברים לפי דגם המגירה" : "", age[0] ? " · " : "", age);
    snprintf(big, sizeof big, "%s", "—"); left[0] = 0;
    if (wave_failed) {
        snprintf(h1, sizeof h1, "הגל נכשל");
        snprintf(s2, sizeof s2, "הגל נכשל — הסיבה ביומן ובקונסולה. עוצרים את הסבב ופותחים חדש.");
    } else if (s->wave_open) {
        int need = s->remaining - s->ready; if (need < 0) need = 0;
        snprintf(h1, sizeof h1, "הגל ממתין למגירות");
        snprintf(s2, sizeof s2, "הגל ממתין: %d מגירות מוכנות, צריך עוד %d — או \"התחל עכשיו\". החלפתם מגירות? הדליקו את המכונות והן יצטרפו.%s",
                 s->ready, need, wave_ended(s) ? "\nהגל הסתיים — החליפו דיסקים והעירו את מחשבי השיכפול כדי להמשיך" : "");
        snprintf(left, sizeof left, "הגל עוד לא יצא");
    } else if (wave_over) {
        if (nmach > 1) snprintf(h1, sizeof h1, "הגל הסתיים — %d דיסקים נכתבו ב-%d מחשבי שיכפול", s->written, nmach);
        else snprintf(h1, sizeof h1, "הגל הסתיים");
        snprintf(s2, sizeof s2, "נכתבו %d מתוך %d דיסקים בסבב.", s->written, s->target);
        if (dv_ok || dv_bad) {   /* #1331 */
            char p1[96] = "", p2[400] = "";
            if (dv_ok == 1) snprintf(p1, sizeof p1, "דרייברים הונחו בדיסק אחד"); else if (dv_ok) snprintf(p1, sizeof p1, "דרייברים הונחו ב-%d דיסקים", dv_ok);
            if (dv_bad == 1) snprintf(p2, sizeof p2, "%s עלול לא לעלות (דרייבר האחסון לא הוגדר)", bad_name);
            else if (dv_bad) snprintf(p2, sizeof p2, "%d דיסקים עלולים לא לעלות (דרייבר האחסון לא הוגדר)", dv_bad);
            snprintf(s2, sizeof s2, "%s%s%s.", p1, p1[0] && p2[0] ? "; " : "", p2);
        }
        if (known) { snprintf(big, sizeof big, "%d%%", sum / known); snprintf(left, sizeof left, "הגל הסתיים"); }
    } else {
        if (ndisk == 1) snprintf(h1, sizeof h1, "משדר לדיסק אחד");
        else if (nmach > 1) snprintf(h1, sizeof h1, "משדר ל-%d דיסקים ב-%d מחשבי שיכפול", ndisk, nmach);
        else {   /* one cloner: name it */
            const char *who = "";
            for (int i = 0; i < s->nmachines && !who[0]; i++) { const RoomDrawer *b[MAX_DRAWERS];
                if (live_bays(&s->machines[i], b)) who = s->machines[i].name[0] ? s->machines[i].name : s->machines[i].mac; }
            snprintf(h1, sizeof h1, "משדר ל-%d דיסקים במחשב שיכפול %s", ndisk, who);
        }
        char a1[64] = "", a2[64] = "";
        if (nsk == 1) snprintf(a1, sizeof a1, "דיסק אחד דולג"); else if (nsk) snprintf(a1, sizeof a1, "%d דיסקים דולגו", nsk);
        if (nf == 1) snprintf(a2, sizeof a2, "דיסק אחד נכשל"); else if (nf) snprintf(a2, sizeof a2, "%d דיסקים נכשלו", nf);
        if (a1[0] || a2[0]) snprintf(s2, sizeof s2, "%s%s%s; השאר ממשיכים. מכונה שסיימה — מכבים, מחליפים מגירות, מדליקים.",
                                     a1, a1[0] && a2[0] ? " ו" : "", a2);
        else snprintf(s2, sizeof s2, "הזרם נכתב לכל הדיסקים יחד. מכונה שסיימה — מכבים, מחליפים מגירות, מדליקים.");
        if (known) snprintf(big, sizeof big, "%d%%", sum / known);
        if (s->eta_s >= 0 && s->rate_bps > 0) {
            if (s->eta_s < 60) snprintf(left, sizeof left, "פחות מדקה נותרה");
            else snprintf(left, sizeof left, "כ-%d דק' נותרו", (s->eta_s + 30) / 60);
        } else snprintf(left, sizeof left, "הזמן שנותר טרם נמדד");
    }
    y += lc_hero(a, cr, x0, xr, y, label, h1, s2, big, left, wave_failed) + 16;   /* a failed wave reads as failed */

    /* .mets: speed (#1577: the last 10 s), drives written, time passed */
    double mw = (cw - 24) / 3, mh = 80;
    char v1[48], v2[48], c2[160], v3[32];
    if (s->rate_bps > 0) snprintf(v1, sizeof v1, "%.0f MB/s", s->rate_bps / 1e6); else snprintf(v1, sizeof v1, "%s", wave_over ? "—" : "עוד לא נמדדה");
    snprintf(v2, sizeof v2, RLM "%d מתוך %d", s->written, s->target);   /* Hebrew: digits alone drew "4 / 0" */
    if (s->remaining > 0) {
        if (s->ready > 0) snprintf(c2, sizeof c2, "דיסקים בסבב · נשארו %d · משוער: עוד %d גלים", s->remaining, (s->remaining + s->ready - 1) / s->ready);
        else snprintf(c2, sizeof c2, "דיסקים בסבב · נשארו %d · ממתין לדיסקים חדשים", s->remaining);
    } else if (dv_any) {   /* #1331 */
        char p1[32] = "", p2[32] = "", p3[32] = "";
        if (dv_ok) snprintf(p1, sizeof p1, "%d הונחו", dv_ok);
        if (dv_no) snprintf(p2, sizeof p2, "%s%d לא", p1[0] ? ", " : "", dv_no);
        if (dv_nomodel) snprintf(p3, sizeof p3, "%s%d בלי דגם", p1[0] || p2[0] ? ", " : "", dv_nomodel);
        snprintf(c2, sizeof c2, "דיסקים בסבב · דרייברים: %s%s%s", p1, p2, p3);
    } else snprintf(c2, sizeof c2, "דיסקים בסבב");
    if (s->elapsed_s >= 0) snprintf(v3, sizeof v3, "%02d:%02d", s->elapsed_s / 60, s->elapsed_s % 60); else snprintf(v3, sizeof v3, "—");
    lc_met(a, cr, (Rect){ xr - mw, y, mw, mh }, "מהירות", v1, s->rate_bps > 0 ? "10 השניות האחרונות" : "", t->muted);
    lc_met(a, cr, (Rect){ xr - 2 * mw - 12, y, mw, mh }, "נכתבו", v2, c2, t->muted);
    lc_met(a, cr, (Rect){ x0, y, mw, mh }, "זמן שעבר", v3, "", t->muted);
    y += mh + 12;

    double pre_h = room_pre_block(a, cr, 0, 0, cw, 0);       /* #432/#65 */
    if (pre_h > 0) { room_pre_block(a, cr, x0, y, cw, 1); y += pre_h + 12; }

    /* the cages: one per cloner of the round, two side by side */
    double btn_hh = btn_height(cr), foot_hh = foot_height(cr, btn_hh);
    Text err = error_make(cr, s->room_error, cw);
    Text unid = room_unidentified_make(cr, s, cw);
    /* #1331 (mockup v2): each drawer's drivers are said in its own row -- no list under the cages */
    double notes_h = (unid.layout ? unid.h + N_LABEL_GAP : 0) + dmax(err.h, ERROR_MIN_H);
    int cols = nmach > 1 ? 2 : 1, rows = (nmach + cols - 1) / cols, most = 1;
    for (int i = 0; i < s->nmachines; i++) { const RoomDrawer *b[MAX_DRAWERS]; int nb = live_bays(&s->machines[i], b); if (nb > most) most = nb; }
    double room = H - N_STATUS_H - foot_hh - 8 - y - notes_h - rows * (10 + 16 + 8 + 10) - (rows - 1) * 12;
    double bh = dv_line ? LC_BAY_DRV_H : LC_BAY_H;   /* #1331: the row with its drivers line */
    if (rows * most * bh + rows * (most - 1) * 10 > room) bh = fmax(56, floor((room - rows * (most - 1) * 10) / (rows * most)));
    double cage_w = (cw - (cols - 1) * 12) / cols, cage_h = 10 + 16 + 8 + most * bh + (most - 1) * 10 + 10;
    if (!nmach) {
        Text empty = sub_make(cr, "אין מחשבי שיכפול בסבב הזה — רושמים אותם בקונסולה, במסך המחשבים.", cw);
        text_draw_r(cr, &empty, xr, y, t->muted); y += empty.h + 12; text_free(&empty);
    }
    int c = 0, ci = 0;   /* ci, #1386: the row's index among room_cancel_picks */
    for (int i = 0; i < s->nmachines; i++) {
        const Machine *m = &s->machines[i]; const RoomDrawer *bays[MAX_DRAWERS];
        int nb = live_bays(m, bays); if (!nb) continue;
        int row = c / cols, col = c % cols; c++;
        Rect cage = { xr - cage_w - col * (cage_w + 12), y + row * (cage_h + 12), cage_w, cage_h };
        char title[160], cnt[96]; int writing = 0;
        for (int k = 0; k < nb; k++) writing += live_kind(m, bays[k]) == BAY_WRITING;
        snprintf(title, sizeof title, "מחשב שיכפול %s%s", m->name[0] ? m->name : m->mac,
                 m->awake || m->joined ? "" : " · כבוי");
        int cancelled = 0, written = 0;
        for (int k = 0; k < nb; k++) { cancelled += live_kind(m, bays[k]) == BAY_CANCELED; written += live_kind(m, bays[k]) == BAY_DONE; }
        if (wave_over) snprintf(cnt, sizeof cnt, "%d מתוך %d נכתבו", written, nb);   /* #1331 mockup v2 */
        else snprintf(cnt, sizeof cnt, "%d מתוך %d כותבים", writing, nb);
        if (cancelled) { size_t u = strlen(cnt); snprintf(cnt + u, sizeof cnt - u, " · %d בוטל", cancelled); }   /* #1386 */
        double by = lc_cage(a, cr, cage, title, cnt);
        for (int k = 0; k < nb; k++) {
            LcBay g; live_bay(t, s, m, bays[k], &g);
            if (live_cancelable(bays[k])) { g.act = "בטל…"; g.act_hit = HIT_BAY_CANCEL_BASE + ci++; }   /* #1386 */
            lc_bay_draw(a, cr, (Rect){ cage.x + 10, by, cage.w - 20, bh }, &g); by += bh + 10;
        }
    }
    if (nmach) y += rows * (cage_h + 12);
    if (unid.layout) { text_draw_r(cr, &unid, xr, y, t->warning_ink); y += unid.h + N_LABEL_GAP; }
    text_draw(cr, &err, x0, y, t->danger_fg);
    Rect foot = { x0 - N_PAD, H - N_STATUS_H - foot_hh - 8, cw + 2 * N_PAD, foot_hh };
    live_footer(a, cr, foot, dv_bad > 0);
    text_free(&err); text_free(&unid);
    (void)inner;
}
