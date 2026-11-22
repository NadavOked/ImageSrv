/* #649 -- the toolbox's keys and clicks, mockup 14 page 2's table:
 *   arrows       the tiles (five a row, the first at the right) / the rows of a list
 *   Enter        opens the tile, the tool; runs it on the run view
 *   PgUp PgDn    a page of a list (eight tools), or of the tool's text
 *   a letter, /  the search over every category, with the letter typed
 *   Esc          one step back; on the home: out of the toolbox
 *   F3           out of the toolbox from anywhere in it (Nadav 31/10) -- main.c
 * What needs the bridge (the list, a run) goes back to main.c as an action. */
#include <string.h>
#include "tools.h"
#include "utf8.h"

void tools_enter(App *a) {
    a->tools_view = a->tool_running && a->tool_sel >= 0 ? TOOLS_OUTPUT : TOOLS_HOME;   /* a run still going: its card */
    a->tool_searching = 0; a->tool_q[0] = 0; a->tool_row = 0; a->tool_error[0] = 0;
    if (a->tool_tile < 0 || a->tool_tile >= TOOL_NCATS || !tools_cat_count(a, a->tool_tile)) a->tool_tile = tools_first_tile(a);
    a->focus = 0;
}

static void open_cat(App *a, int cat) {
    a->tool_tile = a->tool_cat = cat; a->tools_view = TOOLS_CAT; a->tool_row = 0;
    a->tool_searching = 0; a->tool_q[0] = 0;
}

static void search_start(App *a) { a->tool_searching = 1; a->tool_row = 0; a->focus = HIT_TOOL_SEARCH; a->tools_view = TOOLS_HOME; }
static void search_stop(App *a) { a->tool_searching = 0; a->tool_q[0] = 0; a->tool_row = 0; a->focus = 0; }

/* back from a tool: to the list it was opened from -- the search, or its category */
static void back_to_list(App *a) {
    a->tools_view = a->tool_searching ? TOOLS_HOME : TOOLS_CAT;
    a->tool_error[0] = 0; a->focus = a->tool_searching ? HIT_TOOL_SEARCH : 0;
}

static int list_len(const App *a) { int tmp[MAX_TOOLS]; return tools_list_now(a, tmp); }

static int open_row(App *a) {
    int items[MAX_TOOLS], n = tools_list_now(a, items);
    if (a->tool_row < 0 || a->tool_row >= n) return TA_NONE;
    a->tool_sel = items[a->tool_row];
    return TA_OPEN;
}

static void page(App *a, int dir) {
    int n = list_len(a), per = a->tool_per_page > 0 ? a->tool_per_page : 8;
    int r = (a->tool_row / per + dir) * per;
    if (r < 0) r = 0;
    if (r >= n) return;
    a->tool_row = r;
}

static int arg_kind(const Tool *t) { return !strncmp(t->args, "disk:", 5) ? 1 : !strncmp(t->args, "part:", 5) ? 2 : t->args[0] ? 3 : 0; }

/* ↑ ↓ on the run view walk the disks or the partitions */
static void pick_step(App *a, int step) {
    const Tool *t = &a->tools[a->tool_sel];
    int kind = arg_kind(t);
    if (kind == 1) {
        ToolDisk d[MAX_DISKS + LC_MAX_DISKS]; int n = tools_disks(a, d, MAX_DISKS + LC_MAX_DISKS);
        if (!n) return;
        int s = a->tool_disk_sel < 0 ? (step > 0 ? 0 : n - 1) : a->tool_disk_sel + step;
        a->tool_disk_sel = s < 0 ? 0 : s >= n ? n - 1 : s;
    } else if (kind == 2 && a->tool_parts_ok && a->ntool_parts) {
        int n = a->ntool_parts, s = a->tool_part_sel < 0 ? (step > 0 ? 0 : n - 1) : a->tool_part_sel + step;
        a->tool_part_sel = s < 0 ? 0 : s >= n ? n - 1 : s;
    }
}

static void type_into(char *buf, size_t n, int cp) { utf8_append(buf, n, cp); }
static void erase_last(char *buf) {
    size_t len = strlen(buf);
    while (len > 0) { len--; if ((buf[len] & 0xC0) != 0x80) break; }   /* whole UTF-8 char */
    buf[len] = 0;
}

static int nav_click(App *a, int hit) {
    if (hit >= HIT_TOOL_CAT_BASE && hit < HIT_TOOL_CAT_BASE + TOOL_NCATS) { open_cat(a, hit - HIT_TOOL_CAT_BASE); return TA_NONE; }
    if (hit >= HIT_TOOL_BASE && hit < HIT_TOOL_BASE + MAX_TOOLS) {
        int items[MAX_TOOLS], n = tools_list_now(a, items), i = hit - HIT_TOOL_BASE;
        for (int k = 0; k < n; k++) if (items[k] == i) a->tool_row = k;
        a->tool_sel = i;
        return TA_OPEN;
    }
    if (hit >= HIT_TOOL_DISK_BASE && hit < HIT_TOOL_DISK_BASE + MAX_DISKS + LC_MAX_DISKS) { a->tool_disk_sel = hit - HIT_TOOL_DISK_BASE; return TA_NONE; }
    if (hit >= HIT_TOOL_PART_BASE && hit < HIT_TOOL_PART_BASE + MAX_TOOL_PARTS) { a->tool_part_sel = hit - HIT_TOOL_PART_BASE; return TA_NONE; }
    if (hit >= HIT_TOOL_PAGE_BASE && hit < HIT_TOOL_PAGE_BASE + MAX_TOOLS) {
        int per = a->tool_per_page > 0 ? a->tool_per_page : 8, r = (hit - HIT_TOOL_PAGE_BASE) * per;
        if (r < list_len(a)) a->tool_row = r;
        return TA_NONE;
    }
    switch (hit) {
    case HIT_TOOL_EXIT:   return TA_EXIT;
    case HIT_TOOL_SEARCH: if (!a->tool_searching) search_start(a); break;
    case HIT_TOOL_CLEAR:  search_stop(a); break;
    case HIT_TOOL_ARG:    a->focus = HIT_TOOL_ARG; break;
    case HIT_TOOL_RUN:    return TA_RUN;
    case HIT_TOOL_AGAIN:  return TA_AGAIN;
    case HIT_TOOL_LIST:
        if (a->tools_view == TOOLS_CAT) a->tools_view = TOOLS_HOME;
        else if (!a->tool_running) back_to_list(a);
        break;
    case HIT_TOOL_DOWN:
        if (a->tools_view == TOOLS_OUTPUT) a->tool_scroll += 8; else page(a, 1);
        break;
    case HIT_TOOL_UP:
        if (a->tools_view == TOOLS_OUTPUT) { a->tool_scroll -= 8; if (a->tool_scroll < 0) a->tool_scroll = 0; }
        else page(a, -1);
        break;
    }
    return TA_NONE;
}

static int nav_char(App *a, const Event *e) {
    int cp = a->lang_he ? e->he : e->ch;
    switch (a->tools_view) {
    case TOOLS_HOME: case TOOLS_CAT:
        if (!a->tool_searching) {                 /* a letter or '/' opens the search, with the letter */
            if (e->ch <= ' ') return TA_NONE;
            search_start(a);
            if (e->ch == '/') return TA_NONE;
        }
        type_into(a->tool_q, sizeof a->tool_q, cp);
        a->tool_row = 0;
        break;
    case TOOLS_CONFIRM:                           /* the argument is one English word for a shell tool */
        if (a->focus == HIT_TOOL_ARG) type_into(a->tool_arg, sizeof a->tool_arg, e->ch);
        break;
    default: break;
    }
    return TA_NONE;
}

static int nav_key(App *a, int key) {
    if (key == KEYSYM_LAYOUT) { a->lang_he = !a->lang_he; return TA_NONE; }   /* #1245: Alt+Shift */
    switch (a->tools_view) {
    case TOOLS_HOME:
        if (key == KEYSYM_ESC) { if (a->tool_searching) { search_stop(a); return TA_NONE; } return TA_EXIT; }
        if (a->tool_searching) {
            int n = list_len(a);
            if (key == KEYSYM_BACKSPACE) { erase_last(a->tool_q); a->tool_row = 0; }
            else if (key == KEYSYM_DOWN && a->tool_row + 1 < n) a->tool_row++;
            else if (key == KEYSYM_UP && a->tool_row > 0) a->tool_row--;
            else if (key == KEYSYM_PGDN) page(a, 1);
            else if (key == KEYSYM_PGUP) page(a, -1);
            else if (key == KEYSYM_ENTER) return open_row(a);
            return TA_NONE;
        }
        if (key == KEYSYM_ENTER && a->tool_tile >= 0) open_cat(a, a->tool_tile);
        else if (key == KEYSYM_UP || key == KEYSYM_DOWN || key == KEYSYM_LEFT || key == KEYSYM_RIGHT)
            a->tool_tile = tools_tile_step(a, a->tool_tile, key);
        return TA_NONE;
    case TOOLS_CAT: {
        int n = list_len(a);
        if (key == KEYSYM_ESC) a->tools_view = TOOLS_HOME;
        else if (key == KEYSYM_DOWN && a->tool_row + 1 < n) a->tool_row++;
        else if (key == KEYSYM_UP && a->tool_row > 0) a->tool_row--;
        else if (key == KEYSYM_PGDN) page(a, 1);
        else if (key == KEYSYM_PGUP) page(a, -1);
        else if (key == KEYSYM_ENTER) return open_row(a);
        return TA_NONE;
    }
    case TOOLS_CONFIRM:
        if (key == KEYSYM_ESC) back_to_list(a);
        else if (key == KEYSYM_ENTER) return TA_RUN;
        else if (key == KEYSYM_DOWN) pick_step(a, 1);
        else if (key == KEYSYM_UP) pick_step(a, -1);
        else if (key == KEYSYM_BACKSPACE && a->focus == HIT_TOOL_ARG) erase_last(a->tool_arg);
        return TA_NONE;
    case TOOLS_OUTPUT:
        if (a->tool_running) return key == KEYSYM_ESC ? TA_EXIT : TA_NONE;   /* the run goes on; its card waits */
        if (key == KEYSYM_ESC || key == KEYSYM_ENTER) back_to_list(a);
        else if (key == KEYSYM_PGDN || key == KEYSYM_DOWN) a->tool_scroll += key == KEYSYM_PGDN ? 8 : 1;
        else if (key == KEYSYM_PGUP || key == KEYSYM_UP) { a->tool_scroll -= key == KEYSYM_PGUP ? 8 : 1; if (a->tool_scroll < 0) a->tool_scroll = 0; }
        return TA_NONE;
    }
    return TA_NONE;
}

int tools_nav(App *a, const Event *e, int hit) {
    if (e->type == UIEV_CLICK) return nav_click(a, hit);
    if (e->type == UIEV_CHAR) return nav_char(a, e);
    if (e->type == UIEV_KEY) return nav_key(a, e->key);
    return TA_NONE;
}

/* The status bar's keys -- the keys this file acts on now, nothing it ignores */
const char *tools_status_keys(const App *a) {
    switch (a->tools_view) {
    case TOOLS_HOME:
        return a->tool_searching ? "Esc  ניקוי החיפוש    ↑ ↓  מעבר    PgUp PgDn  עמוד    Enter  פתיחה    Alt+Shift  שפה    F3  יציאה"
                                 : "Esc  חזרה    ← → ↑ ↓  מעבר    Enter  פתיחה    הקלדה  חיפוש    F3  יציאה";
    case TOOLS_CAT:
        return "Esc  חזרה    ↑ ↓  מעבר    PgUp PgDn  עמוד    Enter  פתיחה    /  חיפוש    F3  יציאה";
    case TOOLS_CONFIRM: {
        int k = a->tool_sel >= 0 && a->tool_sel < a->ntools ? arg_kind(&a->tools[a->tool_sel]) : 0;
        return k == 1 || k == 2 ? "Esc  חזרה    ↑ ↓  בחירה    Enter  הרץ    F3  יציאה" : "Esc  חזרה    Enter  הרץ    F3  יציאה";
    }
    case TOOLS_OUTPUT:
        return a->tool_running ? "Esc / F3  יציאה מהארגז — הכלי ממשיך לרוץ"
                               : "Esc / Enter  חזרה לכלים    PgUp PgDn  גלילה    F3  יציאה";
    }
    return "F3  יציאה";
}
