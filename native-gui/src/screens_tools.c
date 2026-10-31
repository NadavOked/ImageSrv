/* #649 domain 1: the IT toolbox screen of the build machine (native-only,
 * no HTML card). Three views on one Screen:
 *   list    -- $GUI_DIR/tools grouped by domain, a risk tag per row
 *   confirm -- what will happen, the argument (a disk row or a text field),
 *              and the typed machine name for rw/destroy (principle 7)
 *   output  -- "רץ…" with a spinner, then the tool's text and its verdict
 *              (rc 2 = "לא הצלחנו לבדוק", orange, never green -- principle 5)
 * Everything is drawn with the mockup's existing tokens: no new colour.
 * The bridge side is agent/lib/tools/tools.sh (tools_gui_list / tools_gui_run). */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include "screens_rounds.h"

/* ---- the files next to --state ------------------------------------------------ */

/* 1 = the file is there and differs from the mark (mark updated), 0 = same,
 * -1 = not there. An unchanged mark on an unreadable file is "same", never
 * "empty": a vanished list must not erase what was shown. */
static int file_mark(const char *path, FileMark *m) {
    struct stat st;
    if (stat(path, &st) != 0) return -1;
    long sec = (long)st.st_mtim.tv_sec, nsec = (long)st.st_mtim.tv_nsec;
    if (m->seen && m->sec == sec && m->nsec == nsec && m->size == (long)st.st_size && m->ino == (long)st.st_ino)
        return 0;
    m->seen = 1; m->sec = sec; m->nsec = nsec; m->size = (long)st.st_size; m->ino = (long)st.st_ino;
    return 1;
}

static char *read_all(const char *path, size_t max) {
    FILE *fp = fopen(path, "r");
    if (!fp) return NULL;
    char *buf = malloc(max + 1);
    if (!buf) { fclose(fp); return NULL; }
    size_t n = fread(buf, 1, max, fp);
    fclose(fp);
    buf[n] = 0;
    return buf;
}

/* A deliberate cut: at most n-1 bytes, never inside a UTF-8 character (a
 * Hebrew title cut mid-letter drew a replacement box). No snprintf, so gcc's
 * -Wformat-truncation has nothing to guess about. */
static void cp(char *dst, size_t n, const char *src) {
    if (!n) return;
    size_t len = strlen(src);
    if (len > n - 1) {
        len = n - 1;
        while (len && ((unsigned char)src[len] & 0xC0) == 0x80) len--;   /* back to a character start */
    }
    memcpy(dst, src, len);
    dst[len] = 0;
}

int tools_risk_level(const char *risk) {
    if (!strcmp(risk, "ro")) return 0;
    if (!strcmp(risk, "rw")) return 1;
    return 2;                                 /* destroy, and every unknown word (tools.sh) */
}

/* machine=<name> first, then id|domain|title|risk|args per line. A row with
 * an empty id, domain or title is skipped (tools.sh drops those too). */
void tools_parse(App *a, const char *text) {
    a->ntools = 0; a->tools_machine[0] = 0;
    const char *p = text;
    while (*p) {
        const char *nl = strchr(p, '\n');
        size_t len = nl ? (size_t)(nl - p) : strlen(p);
        char line[512];
        if (len >= sizeof line) len = sizeof line - 1;
        memcpy(line, p, len); line[len] = 0;
        line[strcspn(line, "\r")] = 0;
        p = nl ? nl + 1 : p + len;
        if (!line[0] || line[0] == '#') continue;
        if (!strncmp(line, "machine=", 8)) { cp(a->tools_machine, sizeof a->tools_machine, line + 8); continue; }
        if (a->ntools >= MAX_TOOLS) continue;
        char *f[5]; int n = 0;
        f[n++] = line;
        for (char *q = line; *q && n < 5; q++) if (*q == '|') { *q = 0; f[n++] = q + 1; }
        for (int i = n; i < 5; i++) f[i] = "";
        if (!f[0][0] || !f[1][0] || !f[2][0]) continue;
        Tool *t = &a->tools[a->ntools++];
        cp(t->id, sizeof t->id, f[0]); cp(t->domain, sizeof t->domain, f[1]);
        cp(t->title, sizeof t->title, f[2]); cp(t->risk, sizeof t->risk, f[3][0] ? f[3] : "destroy");
        cp(t->args, sizeof t->args, f[4]);
    }
    a->tools_loaded = 1;
    if (a->tool_sel >= a->ntools) a->tool_sel = -1;
    if (a->tool_list_scroll >= a->ntools) a->tool_list_scroll = 0;
}

void tool_output_set(App *a, const char *text) {
    cp(a->tool_out, sizeof a->tool_out, text ? text : "");
    a->tool_nlines = 0;
    char *p = a->tool_out;
    while (*p && a->tool_nlines < TOOL_OUT_LINES) {
        a->tool_line_off[a->tool_nlines++] = (int)(p - a->tool_out);
        char *nl = strchr(p, '\n');
        if (!nl) break;
        *nl = 0; if (nl > p && nl[-1] == '\r') nl[-1] = 0;
        p = nl + 1;
    }
    a->tool_scroll = 0;
}

/* The result record: id|rc|<path>|<seq>. Only a record for the tool that was
 * asked, with a seq not yet consumed, ends the run -- a stale file from an
 * earlier session is ignored, and so is another tool's. */
static int result_take(App *a, const char *text) {
    char line[600]; cp(line, sizeof line, text); line[strcspn(line, "\r\n")] = 0;
    char *f[4]; int n = 0; f[n++] = line;
    for (char *q = line; *q && n < 4; q++) if (*q == '|') { *q = 0; f[n++] = q + 1; }
    if (n < 4) return 0;
    long seq = strtol(f[3], NULL, 10);
    if (seq <= a->tool_seq) return 0;
    if (a->tool_sel < 0 || strcmp(f[0], a->tools[a->tool_sel].id)) return 0;
    a->tool_seq = seq;
    a->tool_rc = atoi(f[1]);
    char *out = read_all(f[2], TOOL_OUT_MAX);
    /* an unreadable output file is said, not shown as "printed nothing" */
    tool_output_set(a, out ? out : "(לא הצלחנו לקרוא את קובץ הפלט של הכלי)");
    free(out);
    a->tool_running = 0;
    return 1;
}

int tools_poll(App *a) {
    if (!a->tools_dir[0]) return 0;
    int dirty = 0; char path[600];
    snprintf(path, sizeof path, "%s/tools", a->tools_dir);
    if (file_mark(path, &a->tools_mark) == 1) {
        char *text = read_all(path, 64 * 1024);
        if (text) { tools_parse(a, text); free(text); dirty = 1; }
    }
    snprintf(path, sizeof path, "%s/tool-result", a->tools_dir);
    if (file_mark(path, &a->result_mark) == 1) {
        char *text = read_all(path, 1024);
        if (text) { dirty |= result_take(a, text); free(text); }
    }
    return dirty;
}

/* --png sample: three domains, every risk level, one tool with a disk arg. */
void tools_sample(App *a) {
    tools_parse(a,
        "machine=BUILD-01\n"
        "sysinfo|hw|זיהוי המחשב: יצרן, דגם, מספר סידורי, BIOS|ro|\n"
        "smart-health|disk|בריאות SMART של כל דיסק|ro|\n"
        "chkdsk|disk|בדיקת NTFS ותיקון שגיאות|rw|disk:הדיסק לבדיקה\n"
        "wipe|disk|מחיקה מלאה של דיסק|destroy|disk:הדיסק למחיקה\n"
        "ping|net|בדיקת קישוריות לשרת|ro|כתובת או שם מחשב\n"
        "bootfix|boot|תיקון מטען האתחול של Windows|rw|\n");
}

/* ---- shared pieces -------------------------------------------------------------- */

static const char *domain_title(const char *d) {
    if (!strcmp(d, "disk"))    return "דיסקים";
    if (!strcmp(d, "net"))     return "רשת";
    if (!strcmp(d, "windows")) return "Windows";
    if (!strcmp(d, "boot"))    return "אתחול";
    if (!strcmp(d, "hw"))      return "חומרה";
    return d;
}
static const char *DOMAIN_ORDER[] = { "disk", "net", "windows", "boot", "hw" };

/* The risk tag: grey "קריאה בלבד" / orange "משנה דיסק" / red "הרסני", drawn
 * from the metric / warning / danger token triples of the mockup. */
static const char *risk_word(int level) { return level == 0 ? "קריאה בלבד" : level == 1 ? "משנה דיסק" : "הרסני"; }
static void risk_colors(const Theme *t, int level, Rgb *bg, Rgb *line, Rgb *ink) {
    if (level == 0)      { *bg = t->metric;     *line = t->metric_line;  *ink = t->muted; }
    else if (level == 1) { *bg = t->warning_bg; *line = t->warning_line; *ink = t->warning_ink; }
    else                 { *bg = t->danger_bg;  *line = t->danger_line;  *ink = t->danger_fg;}
}
static double tag_draw(cairo_t *cr, const Theme *t, double x, double cy, const char *word, Rgb bg, Rgb line, Rgb ink) {
    /* The triple is chosen by the caller; the box is the console's .tag:
     * padding 3px 7px, 1px border, radius --r, font-size --t-xs (11px). */
    Text w = text_make(cr, FONT_SANS, 11, 400, word, 0, DIR_RTL);
    Rect r = { x, cy - (w.h + 8) / 2, w.w + 16, w.h + 8 };
    draw_fill_rrect(cr, r, t->radius, bg);
    draw_border_rrect(cr, r, t->radius, line, N_BORDER);
    text_draw(cr, &w, r.x + 8, r.y + 4, ink);
    text_free(&w);
    return r.w;
}

static int arg_is_disk(const Tool *t) { return !strncmp(t->args, "disk:", 5); }

/* ---- the list ---------------------------------------------------------------------- */

/* Flattened: -1-k = the heading of DOMAIN_ORDER[k] (or of an unlisted
 * domain, -100-i for the first tool i carrying it), i >= 0 = tool i. */
static int list_items(const App *a, int *items, int max) {
    int n = 0, placed[MAX_TOOLS] = {0};
    for (size_t k = 0; k < sizeof DOMAIN_ORDER / sizeof DOMAIN_ORDER[0]; k++) {
        int first = 1;
        for (int i = 0; i < a->ntools && n < max - 1; i++) {
            if (placed[i] || strcmp(a->tools[i].domain, DOMAIN_ORDER[k])) continue;
            if (first) { items[n++] = -1 - (int)k; first = 0; }
            items[n++] = i; placed[i] = 1;
        }
    }
    for (int i = 0; i < a->ntools && n < max - 1; i++) {
        if (placed[i]) continue;
        items[n++] = -100 - i;
        for (int j = i; j < a->ntools && n < max; j++)
            if (!placed[j] && !strcmp(a->tools[j].domain, a->tools[i].domain)) { items[n++] = j; placed[j] = 1; }
    }
    return n;
}

static const char *item_heading(const App *a, int item) {
    if (item <= -100) return domain_title(a->tools[-100 - item].domain);
    return domain_title(DOMAIN_ORDER[-1 - item]);
}

static void tools_list_view(App *a, cairo_t *cr, double W, double H, double head_h) {
    const Theme *t = a->theme;
    double inner = card_width(W) - 2 * N_PAD, btn_h = btn_height(cr);
    char sub[240];
    if (a->tools_machine[0])
        snprintf(sub, sizeof sub, "כלי אבחון ותיקון לאיש ה-IT. פעולה שמשנה דיסק נשאלת בחלון אישור — %s.", a->tools_machine);
    else
        snprintf(sub, sizeof sub, "כלי אבחון ותיקון לאיש ה-IT. פעולה שמשנה דיסק נשאלת בחלון אישור.");
    Head hd = head_make(cr, "כלים", sub, inner);
    int items[MAX_TOOLS + 16], nitems = list_items(a, items, MAX_TOOLS + 16);
    int cols = inner < N_NARROW_W ? 1 : 2;
    double cw = (inner - (cols - 1) * N_CHOICE_GAP) / cols;
    double row_h = 2 * N_LABEL_GAP + 2 * N_ACTION_GAP + text_line_height(cr, FONT_SANS, N_CHOICE_TITLE);
    double head_row = text_line_height(cr, FONT_SANS, N_LABEL) + N_LABEL_GAP + N_ACTION_GAP;

    Text labels[3]; int kinds[3], ids[3], nb = 0;
    labels[nb] = btn_label(cr, "חזרה"); kinds[nb] = BTN_PLAIN; ids[nb++] = HIT_BACK;
    /* body: fill the card; the frame clips what does not fit, the scroll
     * buttons in the footer move the window (no wheel in input.c). */
    Rect body, foot;
    card_frame(a, cr, W, H, head_h, &hd, H, foot_height(cr, btn_h), &body, &foot);
    body_clip_begin(a, cr, body);
    double x0 = body.x + N_PAD, y0 = body.y + N_PAD, avail = body.h - N_PAD;
    if (!a->tools_loaded || nitems == 0) {
        Text empty = sub_make(cr, a->tools_loaded ? "לא נבחרו כלים בשרת (תשתית › ארגז כלים)." : "טוען את רשימת הכלים…", inner);
        text_draw_r(cr, &empty, x0 + inner, y0, t->muted);
        text_free(&empty);
    }
    if (a->tool_list_scroll < 0 || a->tool_list_scroll >= nitems) a->tool_list_scroll = 0;
    int i = a->tool_list_scroll, shown_all = 1;
    for (int c = 0; c < cols && i < nitems; c++) {
        double x = x0 + inner - cw - c * (cw + N_CHOICE_GAP), y = y0;
        while (i < nitems) {
            double h = items[i] < 0 ? head_row : row_h;
            if (y + h > y0 + avail) { shown_all = 0; break; }
            if (items[i] < 0) {
                Text hl = label_make(cr, item_heading(a, items[i]), cw);
                text_draw(cr, &hl, x, y + N_ACTION_GAP, t->muted);
                text_free(&hl);
            } else {
                const Tool *tool = &a->tools[items[i]];
                int level = tools_risk_level(tool->risk);
                Rect r = { x, y, cw, row_h - N_LABEL_GAP };
                int hov = hovered(a, r);
                draw_fill_rrect(cr, r, RADIUS_SM, hov ? t->indigo_soft : t->choice);
                draw_border_rrect(cr, r, RADIUS_SM, hov ? t->selected_line : t->choice_line, N_BORDER);
                Rgb bg, line, ink; risk_colors(t, level, &bg, &line, &ink);
                double tw = tag_draw(cr, t, r.x + N_ACTION_GAP, r.y + r.h / 2, risk_word(level), bg, line, ink);
                Text ti = rtl_block_make(cr, N_CHOICE_TITLE, 500, tool->title, cw - 3 * N_ACTION_GAP - tw);
                text_draw(cr, &ti, r.x + 2 * N_ACTION_GAP + tw, r.y + (r.h - ti.h) / 2, t->ink);
                text_free(&ti);
                hit_add(a, r, HIT_TOOL_BASE + items[i]);
            }
            y += h; i++;
        }
    }
    body_clip_end(a, cr);
    if (!shown_all || a->tool_list_scroll > 0) {
        labels[nb] = btn_label(cr, "למטה"); kinds[nb] = BTN_PLAIN; ids[nb++] = HIT_TOOL_DOWN;
        labels[nb] = btn_label(cr, "למעלה"); kinds[nb] = BTN_PLAIN; ids[nb++] = HIT_TOOL_UP;
    }
    foot_draw_bg(cr, t, foot);
    foot_buttons(a, cr, foot, labels, kinds, ids, nb);
    card_end(a, cr);
}

/* ---- confirm: argument + the machine name ---------------------------------------- */

static void tools_confirm_view(App *a, cairo_t *cr, double W, double H, double head_h) {
    const Theme *t = a->theme; const State *s = &a->st;
    const Tool *tool = &a->tools[a->tool_sel];
    int level = tools_risk_level(tool->risk);
    double inner = card_width(W) - 2 * N_PAD, in_h = field_height(cr), btn_h = btn_height(cr);
    char sub[200];
    snprintf(sub, sizeof sub, "%s · %s", domain_title(tool->domain), risk_word(level));
    Head hd = head_make(cr, tool->title, sub, inner);

    Text warn = { NULL, 0, 0 }; double warn_h = 0;
    if (level) {
        warn = alert_make(cr, level == 1 ? "הפעולה משנה את הדיסק של המחשב הזה. ודאו שזה המחשב הנכון לפני שממשיכים."
                                         : "פעולה הרסנית: מה שיימחק לא יחזור. ודאו שזה המחשב והדיסק הנכונים.", inner);
        warn_h = alert_height(&warn) + N_ACTION_TOP;
    }
    int disk = arg_is_disk(tool), has_arg = tool->args[0] != 0;
    Text l_arg = { NULL, 0, 0 }; double arg_h = 0, drow = 0;
    if (has_arg) {
        l_arg = label_make(cr, disk ? tool->args + 5 : tool->args, inner);
        drow = 2 * N_LABEL_GAP + 2 * N_ACTION_GAP + text_line_height(cr, FONT_SANS, N_CHOICE_TITLE);
        arg_h = l_arg.h + N_LABEL_GAP + (disk ? (s->ndisks ? s->ndisks : 1) * drow : in_h) + N_FORM_GAP;
    }
    Text err = error_make(cr, a->tool_error, inner);
    double body_h = N_PAD + warn_h + arg_h + dmax(err.h, ERROR_MIN_H);

    /* one destructive pattern: the run button opens only once what tool_run()
     * needs is there (disk, argument), red only for a destructive tool, named
     * after the tool; rw/destroy then ask in the #1270 confirm dialog (no
     * typed machine name any more). */
    int ready = (!disk || (a->tool_disk_sel >= 0 && a->tool_disk_sel < s->ndisks)) &&
                (disk || !has_arg || a->tool_arg[0]);
    char run_label[160];
    if (level == 2 && strlen(tool->title) < 60) snprintf(run_label, sizeof run_label, "הרץ: %s…", tool->title);
    else snprintf(run_label, sizeof run_label, level ? "הרץ…" : "הרץ");
    Text labels[2]; int kinds[2], ids[2], nb = 0;
    labels[nb] = btn_label(cr, run_label); kinds[nb] = !ready ? BTN_OFF : level == 2 ? BTN_DANGER : BTN_PRIMARY; ids[nb++] = HIT_TOOL_RUN;
    labels[nb] = btn_label(cr, "חזרה"); kinds[nb] = BTN_PLAIN; ids[nb++] = HIT_TOOL_LIST;

    Rect body, foot;
    card_frame(a, cr, W, H, head_h, &hd, body_h, foot_height(cr, btn_h), &body, &foot);
    body_clip_begin(a, cr, body);
    double x = body.x + N_PAD, y = body.y + N_PAD;
    if (level) { draw_alert(cr, t, (Rect){ x, y, inner, alert_height(&warn) }, &warn); y += warn_h; }
    if (has_arg) {
        text_draw(cr, &l_arg, x, y, t->muted); y += l_arg.h + N_LABEL_GAP;
        if (disk) {
            if (!s->ndisks) {
                Text none = sub_make(cr, "לא נמצא דיסק במחשב הזה.", inner);
                text_draw_r(cr, &none, x + inner, y, t->danger_fg); text_free(&none); y += drow;
            }
            for (int i = 0; i < s->ndisks; i++) {
                const Disk *d = &s->disks[i];
                Rect r = { x, y, inner, drow - N_LABEL_GAP };
                int sel = a->tool_disk_sel == i, hov = hovered(a, r);
                draw_fill_rrect(cr, r, RADIUS_SM, sel ? t->disk_selected : hov ? t->indigo_soft : t->disk_bg);
                draw_border_rrect(cr, r, RADIUS_SM, sel ? t->disk_selected_line : t->disk_line, N_BORDER);
                char sz[32], info[160]; fmt_bytes(sz, sizeof sz, (double)d->size_bytes);
                snprintf(info, sizeof info, "%s · %s · %s", d->model[0] ? d->model : "דיסק", sz, d->dev);
                Text ti = rtl_block_make(cr, N_CHOICE_TITLE, sel ? 700 : 500, info, inner - 2 * N_ACTION_GAP);
                text_draw(cr, &ti, r.x + N_ACTION_GAP, r.y + (r.h - ti.h) / 2, t->ink);
                text_free(&ti);
                hit_add(a, r, HIT_TOOL_DISK_BASE + i);
                y += drow;
            }
        } else {
            draw_field(a, cr, (Rect){ x, y, inner, in_h }, a->tool_arg, 0, NULL, HIT_TOOL_ARG);
            y += in_h;
        }
        y += N_FORM_GAP;
    }
    text_draw(cr, &err, x, y, t->danger_fg);
    body_clip_end(a, cr);
    foot_draw_bg(cr, t, foot);
    foot_buttons(a, cr, foot, labels, kinds, ids, nb);
    card_end(a, cr);
    if (level) text_free(&warn);
    if (has_arg) text_free(&l_arg);
    text_free(&err);
}

/* ---- output: spinner, verdict, the text ------------------------------------------- */

static void spinner_draw(cairo_t *cr, Rgb c, double cx, double cy, double r, double angle) {
    cairo_set_line_width(cr, 2.5);
    cairo_arc(cr, cx, cy, r, angle, angle + 1.5 * M_PI);
    draw_set(cr, c); cairo_stroke(cr);
}

static const char *rc_word(int rc, Rgb *bg, Rgb *line, Rgb *ink, const Theme *t) {
    switch (rc) {
    case 0:  *bg = t->success_bg; *line = t->success_line; *ink = t->success_ink; return "הסתיים";
    case 1:  *bg = t->danger_bg;  *line = t->danger_line;  *ink = t->danger_fg;     return "נמצאה בעיה";
    case 2:  *bg = t->warning_bg; *line = t->warning_line; *ink = t->warning_ink; return "לא הצלחנו לבדוק";
    case 3:  *bg = t->danger_bg;  *line = t->danger_line;  *ink = t->danger_fg;     return "האישור לא תואם";
    case 4:  *bg = t->danger_bg;  *line = t->danger_line;  *ink = t->danger_fg;     return "כלי לא מוכר";
    default: *bg = t->danger_bg;  *line = t->danger_line;  *ink = t->danger_fg;     return "נכשל";
    }
}

static int line_is_ascii(const char *s) { for (; *s; s++) if ((unsigned char)*s >= 0x80) return 0; return 1; }

static void tools_output_view(App *a, cairo_t *cr, double W, double H, double head_h) {
    const Theme *t = a->theme;
    const Tool *tool = &a->tools[a->tool_sel];
    int level = tools_risk_level(tool->risk);
    double inner = card_width(W) - 2 * N_PAD, btn_h = btn_height(cr);
    char sub[200];
    snprintf(sub, sizeof sub, "%s · %s", domain_title(tool->domain), risk_word(level));
    Head hd = head_make(cr, tool->title, sub, inner);
    Text labels[4]; int kinds[4], ids[4], nb = 0;
    if (!a->tool_running) { labels[nb] = btn_label(cr, "הרץ שוב"); kinds[nb] = BTN_PRIMARY; ids[nb++] = HIT_TOOL_AGAIN; }
    labels[nb] = btn_label(cr, "חזרה"); kinds[nb] = BTN_PLAIN; ids[nb++] = HIT_TOOL_LIST;
    Rect body, foot;
    card_frame(a, cr, W, H, head_h, &hd, H, foot_height(cr, btn_h), &body, &foot);
    body_clip_begin(a, cr, body);
    double x = body.x + N_PAD, y = body.y + N_PAD, xr = x + inner, avail_end = body.y + body.h - N_PAD;
    double status_h = text_line_height(cr, FONT_SANS, N_LABEL) + 2 * N_LABEL_GAP;
    if (a->tool_running) {
        Text w = text_make(cr, FONT_SANS, N_SUB, 500, "רץ…", 0, DIR_RTL);
        text_draw_r(cr, &w, xr, y + (status_h - w.h) / 2, t->ink);
        spinner_draw(cr, t->led_write, xr - w.w - N_ACTION_GAP - status_h / 2, y + status_h / 2, status_h / 2 - 3, a->tool_spin);
        text_free(&w);
    } else {
        Rgb bg, line, ink;
        const char *word = rc_word(a->tool_rc, &bg, &line, &ink, t);
        Text w = text_make(cr, FONT_SANS, N_LABEL, 500, word, 0, DIR_RTL);
        double tw = w.w + 2 * N_ACTION_GAP;
        tag_draw(cr, t, xr - tw, y + status_h / 2, word, bg, line, ink);
        text_free(&w);
    }
    y += status_h + N_ACTION_TOP;
    int shown_all = 1;
    if (!a->tool_running && a->tool_nlines == 0) {
        Text none = sub_make(cr, "(הכלי לא הדפיס כלום)", inner);
        text_draw_r(cr, &none, xr, y, t->muted); text_free(&none);
    }
    if (a->tool_scroll < 0 || a->tool_scroll >= a->tool_nlines) a->tool_scroll = 0;
    for (int i = a->tool_scroll; i < a->tool_nlines && !a->tool_running; i++) {
        const char *line = a->tool_out + a->tool_line_off[i];
        Text tl;
        if (line_is_ascii(line)) tl = text_make(cr, FONT_MONO, N_SUB, 400, line[0] ? line : " ", (int)inner, DIR_LTR);
        else                     tl = rtl_block_make(cr, N_SUB, 400, line, inner);
        if (y + tl.h > avail_end) { text_free(&tl); shown_all = 0; break; }
        text_draw(cr, &tl, x, y, t->ink);
        y += tl.h + 2;
        text_free(&tl);
    }
    body_clip_end(a, cr);
    if (!shown_all || a->tool_scroll > 0) {
        labels[nb] = btn_label(cr, "למטה"); kinds[nb] = BTN_PLAIN; ids[nb++] = HIT_TOOL_DOWN;
        labels[nb] = btn_label(cr, "למעלה"); kinds[nb] = BTN_PLAIN; ids[nb++] = HIT_TOOL_UP;
    }
    foot_draw_bg(cr, t, foot);
    foot_buttons(a, cr, foot, labels, kinds, ids, nb);
    card_end(a, cr);
}

void screen_tools(App *a, cairo_t *cr, double W, double H, double head_h) {
    if (a->tool_sel < 0 || a->tool_sel >= a->ntools) a->tools_view = TOOLS_LIST;
    switch (a->tools_view) {
    case TOOLS_CONFIRM: tools_confirm_view(a, cr, W, H, head_h); break;
    case TOOLS_OUTPUT:  tools_output_view(a, cr, W, H, head_h); break;
    default:            tools_list_view(a, cr, W, H, head_h); break;
    }
}
