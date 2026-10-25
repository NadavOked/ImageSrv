/* 16/10 (Nadav): "שחזור אימג' מהשרת" on the cloner -- the build machine's
 * restore screen (screens_restore.c: the folders, the images, the warning,
 * "מחק ושחזר…", the #1270 confirm dialog), with THIS machine's drawers as the
 * targets instead of the internal disk. What is new is only the target: the
 * drawers as the standby screen knows them (cdisk= records -- disk number by
 * the drawer mapping, SMART colour), ticked by the person, none by default
 * (#1410: no selection is never "all the drawers").
 *
 * The screen decides nothing. The red button sends lc-pull|<image>|<p1,p2>;
 * the bridge and the agent check the ports against what is attached now
 * (lcpull.sh) and the server checks them again (cloner_round.py). No
 * sign-in: being at the machine is the authorization (owner, 16/10).
 *
 * Mouse only, keyboard only or both (owner, 16/10): Tab (or Down / Up) moves
 * the keyboard between the image list -- where the digits keep picking
 * folders and images -- and the drawer row; there Left / Right move between
 * the drawers (RTL: Left is the next disk number), Space ticks the focused
 * one, A ticks every healthy one (SMART green), and the focused drawer
 * carries the focus ring of the other screens (widgets.c). */
#include <stdio.h>
#include <string.h>
#include "lcui.h"
#include "screens_rounds.h"

int lc_pull_active(const App *a) { return a->force_cloner && a->st.lc.screen == LC_SCREEN_PULL; }

int lc_pull_selectable(const App *a, int port) {
    const IdleDisk *d = port >= 1 && port <= MAX_DRAWERS ? idle_for_port(&a->st, port) : NULL;
    return d != NULL && smart_level(d->smart) < 2;   /* #1732: red (SMART fail) is never written */
}

/* The ticked drawers that are still attached, ascending: "1,3" into out (may
 * be NULL); returns how many. */
int lc_pull_ports(const App *a, char *out, size_t n) {
    int k = 0; size_t u = 0;
    if (out && n) out[0] = 0;
    for (int p = 1; p <= MAX_DRAWERS; p++) {
        if (!a->pull_sel[p] || !lc_pull_selectable(a, p)) continue;
        if (out && u < n) { int w = snprintf(out + u, n - u, "%s%d", k ? "," : "", p); if (w > 0) u += (size_t)w; }
        k++;
    }
    return k;
}

/* "דיסק 1, דיסק 3" -- the confirm dialog names what will be erased. */
void lc_pull_names(const App *a, char *out, size_t n) {
    size_t u = 0; int k = 0; out[0] = 0;
    for (int p = 1; p <= MAX_DRAWERS && u < n; p++) {
        if (!a->pull_sel[p] || !lc_pull_selectable(a, p)) continue;
        char label[SLOT_NAME_MAX + 16]; cloner_slot_label(&a->st, p, label, sizeof label);
        int w = snprintf(out + u, n - u, "%s%s", k++ ? ", " : "", label);
        if (w > 0) u += (size_t)w;
    }
}

int lc_pull_ready(App *a) {
    if (a->restore_image_sel <= 0 || a->restore_image_sel > a->st.nimages
        || a->st.images[a->restore_image_sel - 1].unchecked) {   /* #1896: mark C: first */
        snprintf(a->st.form_error, sizeof a->st.form_error, "בחרו אימג' לשחזור");
        return 0;
    }
    if (!lc_pull_ports(a, NULL, 0)) {
        snprintf(a->st.form_error, sizeof a->st.form_error, "סמנו לפחות מגירה אחת לשחזור");
        return 0;
    }
    a->st.form_error[0] = 0;
    return 1;
}

/* The next drawer from <port> in <dir> (+1 = the next disk number), or 0. */
static int pull_step(const App *a, int port, int dir) {
    for (int p = port + dir; p >= 1 && p <= MAX_DRAWERS; p += dir) if (lc_pull_selectable(a, p)) return p;
    return 0;
}

/* After a reload: a drawer that is gone is no longer ticked, nor focused. */
void lc_pull_after_reload(App *a) {
    for (int p = 1; p <= MAX_DRAWERS; p++) if (a->pull_sel[p] && !lc_pull_selectable(a, p)) a->pull_sel[p] = 0;
    if (a->pull_focus && !lc_pull_selectable(a, a->pull_focus)) {
        int p = pull_step(a, a->pull_focus, -1);
        a->pull_focus = p ? p : pull_step(a, 0, 1);
    }
}

static void lc_pull_start(App *a) {
    char ports[64], line[200];
    a->confirm = CONFIRM_NONE;
    if (!lc_pull_ready(a)) return;
    lc_pull_ports(a, ports, sizeof ports);
    snprintf(line, sizeof line, "lc-pull|%s|%s", a->st.images[a->restore_image_sel - 1].id, ports);
    printf("%s\n\n", line); fflush(stdout);
}

static void lc_pull_back(App *a) {
    memset(a->pull_sel, 0, sizeof a->pull_sel); a->pull_focus = 0;
    a->confirm = CONFIRM_NONE; a->pick_in_folder = 0;
    printf("lc-back\n\n"); fflush(stdout);
}

/* The drawers inside the target panel: one cell per attached drawer, by disk
 * number, wrapping. The cell is the image card's (screens_restore.c: surface,
 * indigo-soft and a 2px indigo border when chosen) with the cloner disk
 * card's 3px top edge in the SMART colour (screens_cloner.c). With draw 0 it
 * only measures. Returns the height used. */
double lc_pull_drawers(App *a, cairo_t *cr, Rect area, int draw) {
    const Theme *t = a->theme; const State *s = &a->st;
    double cw = 120, ch = 54, gap = 8;
    int per = (int)((area.w + gap) / (cw + gap)); if (per < 1) per = 1;
    int n = 0;
    for (int i = 0; i < s->nidisks; i++) {
        const IdleDisk *d = &s->idisks[i];
        if (d->port < 1 || d->port > MAX_DRAWERS || idle_for_port(s, d->port) != d) continue;
        int can = lc_pull_selectable(a, d->port);   /* #1732: a red drawer is drawn, dimmed, and takes no click */
        int inc = !smart_level(d->smart) && !strcmp(d->mark, "incomplete");   /* #1798: orange, still a target */
        Rgb col_c = inc ? t->warn : smart_color(t, d->smart);
        int row = n / per, col = n % per; n++;
        if (!draw) continue;
        Rect c = { area.x + area.w - cw - col * (cw + gap), area.y + row * (ch + gap), cw, ch };
        int on = can && a->pull_sel[d->port];
        draw_fill_rrect(cr, c, t->radius, on ? t->indigo_soft : t->surface);
        cairo_save(cr); draw_rrect_path(cr, c, t->radius); cairo_clip(cr);
        cairo_rectangle(cr, c.x, c.y, c.w, 3); draw_set(cr, col_c); cairo_fill(cr);
        cairo_restore(cr);
        draw_border_rrect(cr, c, t->radius, on ? t->indigo : can && hovered(a, c) ? t->indigo : t->hair, on ? 2 : 1);
        /* sub: RLM (3) + size (<32) + " · " (4) + the longest second half, #1732's
         * "SMART נכשל — לא כותבים עליו" (45 bytes) + NUL = 84; 64 cut it (-Wformat-truncation) */
        char label[SLOT_NAME_MAX + 16], sub[96], size[32];
        cloner_slot_label(s, d->port, label, sizeof label);
        disk_size(d->size, size, sizeof size);
        /* #1732: a red drawer says it is not written; #1742: a read-only one says why */
        snprintf(sub, sizeof sub, RLM "%s · %s", size, inc ? "כתיבה לא הושלמה — לא שמיש"
                 : can || !strcmp(d->cause, "readonly") ? smart_he_why(d->smart, d->cause) : "SMART נכשל — לא כותבים עליו");
        Text l1 = rtl_block_make(cr, 14, 600, label, c.w - 20);
        Text l2 = rtl_block_make(cr, 11, 400, sub, c.w - 20);
        text_draw_r(cr, &l1, c.x + c.w - 10, c.y + 9, t->ink);
        text_draw_r(cr, &l2, c.x + c.w - 10, c.y + 31, smart_level(d->smart) || inc ? col_c : t->muted);
        text_free(&l1); text_free(&l2);
        if (can) hit_add(a, c, HIT_PULL_DRAWER_BASE + d->port);
        if (a->pull_focus == d->port && !a->confirm) draw_focus_ring(a, cr, c);
    }
    if (!n) return 0;
    int rows = (n + per - 1) / per;
    return rows * ch + (rows - 1) * gap;
}

/* Keys and clicks of the restore screen that are the cloner's own: a drawer,
 * "חזרה" / Esc (back to the boot choice), "מחק ושחזר…" / Enter and the
 * confirm dialog's red button. Everything else -- the folders and the images
 * -- is the build machine's handling (main.c). 1 = taken. */
int lc_pull_handle(App *a, const Event *e) {
    if (!lc_pull_active(a)) return 0;
    int id = e->type == UIEV_CLICK ? app_hit(a, e->x, e->y) : 0;
    if (a->confirm == CONFIRM_RESTORE) {           /* #1270: modal -- Enter erases, Esc cancels */
        if ((e->type == UIEV_KEY && e->key == KEYSYM_ENTER) || id == HIT_CONFIRM_OK) lc_pull_start(a);
        else if ((e->type == UIEV_KEY && e->key == KEYSYM_ESC) || id == HIT_CONFIRM_CANCEL) a->confirm = CONFIRM_NONE;
        return e->type == UIEV_KEY || e->type == UIEV_CLICK || e->type == UIEV_CHAR;
    }
    if (a->confirm) return 0;
    if (id >= HIT_PULL_DRAWER_BASE + 1 && id <= HIT_PULL_DRAWER_BASE + MAX_DRAWERS) {
        int p = id - HIT_PULL_DRAWER_BASE;
        if (lc_pull_selectable(a, p)) { a->pull_sel[p] = !a->pull_sel[p]; a->pull_focus = p; }
        return 1;
    }
    /* -- the drawer row's keys (owner 16/10): between the image list and the drawer row, and in the row -- */
    if (e->type == UIEV_KEY && (e->key == KEYSYM_TAB || e->key == KEYSYM_BACKTAB ||
                                (e->key == KEYSYM_DOWN && !a->pull_focus) || (e->key == KEYSYM_UP && a->pull_focus))) {
        a->pull_focus = a->pull_focus ? 0 : pull_step(a, 0, 1);
        return 1;
    }
    if (a->pull_focus && e->type == UIEV_KEY && (e->key == KEYSYM_LEFT || e->key == KEYSYM_RIGHT)) {
        int p = pull_step(a, a->pull_focus, e->key == KEYSYM_LEFT ? 1 : -1);   /* RTL: disk 1 is on the right */
        if (p) a->pull_focus = p;
        return 1;
    }
    if (a->pull_focus && e->type == UIEV_CHAR && e->ch == ' ') {
        if (lc_pull_selectable(a, a->pull_focus)) a->pull_sel[a->pull_focus] = !a->pull_sel[a->pull_focus];
        return 1;
    }
    if (e->type == UIEV_CHAR && (e->ch == 'a' || e->ch == 'A')) {   /* every healthy drawer; orange and red stay as they are */
        for (int p = 1; p <= MAX_DRAWERS; p++) {
            const IdleDisk *d = idle_for_port(&a->st, p);
            if (d && lc_pull_selectable(a, p) && !smart_level(d->smart)) a->pull_sel[p] = 1;
        }
        return 1;
    }
    /* -- end of the drawer row's keys -- */
    if (id == HIT_BACK) { lc_pull_back(a); return 1; }
    if (id == HIT_RESTORE_START) { if (lc_pull_ready(a)) a->confirm = CONFIRM_RESTORE; return 1; }
    if (e->type == UIEV_KEY && e->key == KEYSYM_ESC && !a->pick_in_folder && !a->dd_open) { lc_pull_back(a); return 1; }
    if (e->type == UIEV_KEY && e->key == KEYSYM_ENTER) { if (lc_pull_ready(a)) a->confirm = CONFIRM_RESTORE; return 1; }
    return 0;
}
