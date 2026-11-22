/* #649 -- the toolbox's files and its two tool screens, mockup 14 page 4
 * ("תבנית ההרצה", approved by Nadav 31/10):
 *   the run view -- what will happen (the red box of a destructive tool), the
 *                   input (a disk, a partition, or a text field), the run button
 *                   (red for a destructive tool; rw/destroy then ask in the #1270
 *                   dialog -- no typed name)
 *   the result   -- the spinner while it runs, then a card whose colour is the
 *                   tool's rc: 0 green, 2 "לא הצלחנו לבדוק" orange and dashed --
 *                   never green (principle 5), anything else red; the tool's text
 *                   under it, and the save line when the tool says where (dest_rec)
 * The home and a category's list are screens_tools_home.c; the keys tools_nav.c.
 * Everything is drawn with the existing tokens: no new colour.
 * The bridge side is agent/lib/tools/tools.sh (tools_gui_list / tools_gui_run). */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include "screens_rounds.h"
#include "tools.h"
#include "lcui.h"

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

/* One line of a '|' file into at most <max> fields; the missing ones are "". */
static int fields(char *line, char **f, int max) {
    int n = 0;
    f[n++] = line;
    for (char *q = line; *q && n < max; q++) if (*q == '|') { *q = 0; f[n++] = q + 1; }
    for (int i = n; i < max; i++) f[i] = "";
    return n;
}

/* machine=<name> first, then id|domain|title|risk|args[|what] per line -- the
 * sixth field (a one-line description) is optional. A row with an empty id,
 * domain or title is skipped (tools.sh drops those too). */
void tools_parse(App *a, const char *text) {
    a->ntools = 0; a->tools_machine[0] = 0;
    const char *p = text;
    while (*p) {
        const char *nl = strchr(p, '\n');
        size_t len = nl ? (size_t)(nl - p) : strlen(p);
        char line[768];
        if (len >= sizeof line) len = sizeof line - 1;
        memcpy(line, p, len); line[len] = 0;
        line[strcspn(line, "\r")] = 0;
        p = nl ? nl + 1 : p + len;
        if (!line[0] || line[0] == '#') continue;
        if (!strncmp(line, "machine=", 8)) { cp(a->tools_machine, sizeof a->tools_machine, line + 8); continue; }
        if (a->ntools >= MAX_TOOLS) continue;
        char *f[6];
        fields(line, f, 6);
        if (!f[0][0] || !f[1][0] || !f[2][0]) continue;
        Tool *t = &a->tools[a->ntools++];
        cp(t->id, sizeof t->id, f[0]); cp(t->domain, sizeof t->domain, f[1]);
        cp(t->title, sizeof t->title, f[2]); cp(t->risk, sizeof t->risk, f[3][0] ? f[3] : "destroy");
        cp(t->args, sizeof t->args, f[4]); cp(t->what, sizeof t->what, f[5]);
    }
    a->tools_loaded = 1;
    if (a->tool_sel >= a->ntools) a->tool_sel = -1;
}

/* $GUI_DIR/tool-parts: dev|disk|size_bytes|fstype|label per partition */
static void parts_parse(App *a, const char *text) {
    a->ntool_parts = 0;
    const char *p = text;
    while (*p && a->ntool_parts < MAX_TOOL_PARTS) {
        const char *nl = strchr(p, '\n');
        size_t len = nl ? (size_t)(nl - p) : strlen(p);
        char line[256];
        if (len >= sizeof line) len = sizeof line - 1;
        memcpy(line, p, len); line[len] = 0;
        line[strcspn(line, "\r")] = 0;
        p = nl ? nl + 1 : p + len;
        char *f[5];
        fields(line, f, 5);
        if (!f[0][0] || strpbrk(f[0], "/ ")) continue;   /* a device name, nothing else (it is sent as /dev/<it>) */
        ToolPart *t = &a->tool_parts[a->ntool_parts++];
        cp(t->dev, sizeof t->dev, f[0]); cp(t->disk, sizeof t->disk, f[1]);
        t->size = strtoull(f[2], NULL, 10);
        cp(t->fstype, sizeof t->fstype, f[3]); cp(t->label, sizeof t->label, f[4]);
    }
    if (a->tool_part_sel >= a->ntool_parts) a->tool_part_sel = -1;
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
    char *f[4];
    if (fields(line, f, 4) < 4) return 0;
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
        char *text = read_all(path, 128 * 1024);
        if (text) { tools_parse(a, text); free(text); dirty = 1; }
    }
    /* tool-parts is written with the list; missing = "could not read", never "none" */
    snprintf(path, sizeof path, "%s/tool-parts", a->tools_dir);
    int pm = file_mark(path, &a->parts_mark);
    if (pm == 1) {
        char *text = read_all(path, 16 * 1024);
        a->tool_parts_ok = text != NULL;
        if (text) { parts_parse(a, text); free(text); } else a->ntool_parts = 0;
        dirty = 1;
    } else if (pm < 0 && a->tool_parts_ok) {
        a->tool_parts_ok = 0; a->ntool_parts = 0; a->parts_mark.seen = 0; dirty = 1;
    }
    snprintf(path, sizeof path, "%s/tool-result", a->tools_dir);
    if (file_mark(path, &a->result_mark) == 1) {
        char *text = read_all(path, 1024);
        if (text) { dirty |= result_take(a, text); free(text); }
    }
    return dirty;
}

/* ---- the disks a tool can be pointed at ---------------------------------------- */

int tools_disks(const App *a, ToolDisk *out, int max) {
    const State *s = &a->st;
    int n = 0;
    if (s->ndisks) {                                   /* the build machine: disk= (guistate.sh) */
        for (int i = 0; i < s->ndisks && n < max; i++) {
            const Disk *d = &s->disks[i]; ToolDisk *o = &out[n++];
            cp(o->dev, sizeof o->dev, d->dev); cp(o->model, sizeof o->model, d->model[0] ? d->model : "דיסק");
            if (d->slot_known && eff_drawer(d->drawer, 0) > 0) snprintf(o->label, sizeof o->label, "דיסק %d", eff_drawer(d->drawer, 0));
            else cp(o->label, sizeof o->label, o->model);
            o->size = d->size_bytes;
            cp(o->on, sizeof o->on, d->dev);
        }
    } else if (s->lc.ndisks) {                         /* the USB stick / the local clone: lc_disk= */
        for (int i = 0; i < s->lc.ndisks && n < max; i++) {
            const LcDisk *d = &s->lc.disks[i]; ToolDisk *o = &out[n++];
            cp(o->dev, sizeof o->dev, d->dev); cp(o->model, sizeof o->model, d->model);
            char l[LC_LABEL_MAX]; lc_disk_label(d, l, sizeof l); cp(o->label, sizeof o->label, l);
            char h[200]; lc_has_text(d, h, sizeof h); cp(o->on, sizeof o->on, h);
            o->size = d->size;
        }
    } else {                                           /* the cloner's wait: its drawers */
        for (int i = 0; i < s->nidisks && n < max; i++) {
            const IdleDisk *d = &s->idisks[i]; ToolDisk *o = &out[n++];
            cp(o->dev, sizeof o->dev, d->dev); cp(o->model, sizeof o->model, d->model);
            if (eff_drawer(d->drawer, 0) > 0) snprintf(o->label, sizeof o->label, "דיסק %d", eff_drawer(d->drawer, 0));
            else cp(o->label, sizeof o->label, d->model[0] ? d->model : d->dev);
            o->size = d->size; cp(o->on, sizeof o->on, d->dev);
        }
    }
    return n;
}

/* ---- --png: mockup 14's catalogue (tools_sample.c) ------------------------------- */

extern const char TOOLS_SAMPLE[];
void tools_sample(App *a) {
    tools_parse(a, TOOLS_SAMPLE);
    if (!a->tool_parts_ok) {
        parts_parse(a, "sda1|sda|104857600|vfat|EFI\nsda2|sda|16777216||\nsda3|sda|499000000000|ntfs|Windows\n");
        a->tool_parts_ok = 1;
    }
}

/* ---- shared pieces ------------------------------------------------------------ */

static int line_is_ascii(const char *s) { for (; *s; s++) if ((unsigned char)*s >= 0x80) return 0; return 1; }

/* a Latin run kept left-to-right inside a Hebrew line: LRM on both sides, so its edge slashes stay
 * with it ("/dev/sda" was drawn "dev/sda/" in the RTL row; FSI/LRI did not hold it here) */
static void ltr_isolate(const char *in, char *out, size_t n) { snprintf(out, n, "\xE2\x80\x8E%s\xE2\x80\x8E", in); }

static int arg_kind(const Tool *t) { return !strncmp(t->args, "disk:", 5) ? 1 : !strncmp(t->args, "part:", 5) ? 2 : t->args[0] ? 3 : 0; }

/* The target the run is sent to, as the person sees it ("דיסק 2", "מחיצה sda1",
 * the typed value), or "" for a tool that reads the whole machine. */
static void target_label(const App *a, char *out, size_t n) {
    const Tool *t = &a->tools[a->tool_sel];
    out[0] = 0;
    if (arg_kind(t) == 1) {
        ToolDisk d[MAX_DISKS + LC_MAX_DISKS]; int nd = tools_disks(a, d, MAX_DISKS + LC_MAX_DISKS);
        if (a->tool_disk_sel >= 0 && a->tool_disk_sel < nd) snprintf(out, n, "%s", d[a->tool_disk_sel].label);
    } else if (arg_kind(t) == 2) {
        if (a->tool_part_sel >= 0 && a->tool_part_sel < a->ntool_parts) snprintf(out, n, "מחיצה %s", a->tool_parts[a->tool_part_sel].dev);
    } else if (arg_kind(t) == 3) snprintf(out, n, "%s", a->tool_arg);
}

static void crumb_of(const App *a, char *out, size_t n) {
    snprintf(out, n, "כלים › %s ›", TOOL_CATS[tool_cat_of(&a->tools[a->tool_sel])].title);
}

/* .alert.bad / .alert.warn: the soft fill, a 1px line, the round "!" (24px) and
 * a bold first sentence (15px) over the rest (14px) */
static double alert_box(App *a, cairo_t *cr, double x0, double cw, double y, int bad, const char *b, const char *rest) {
    const Theme *t = a->theme;
    double iw = cw - 32 - 24 - 12;
    Text tb = text_make(cr, FONT_SANS, 15, 600, b, (int)iw, DIR_RTL), tr = text_make(cr, FONT_SANS, 14, 400, rest, (int)iw, DIR_RTL);
    double h = 12 + tb.h + tr.h + 12;
    Rect r = { x0, y, cw, h };
    draw_fill_rrect(cr, r, RADIUS_SM, bad ? t->danger_soft : t->warning_soft);
    draw_border_rrect(cr, r, RADIUS_SM, bad ? t->danger : t->warn, 1);
    double xr = r.x + r.w - 16;
    cairo_new_sub_path(cr); cairo_arc(cr, xr - 12, y + 12 + 13, 12, 0, 2 * M_PI); draw_set(cr, bad ? t->danger : t->warn); cairo_fill(cr);
    Text ex = text_make(cr, FONT_SANS, 14, 700, "!", 0, DIR_LTR);
    text_draw(cr, &ex, xr - 12 - ex.w / 2.0, y + 12 + 13 - ex.h / 2.0, t->on_ink); text_free(&ex);
    text_draw_r(cr, &tb, xr - 24 - 12, y + 12, bad ? t->danger_fg : t->warning_ink);
    text_draw_r(cr, &tr, xr - 24 - 12, y + 12 + tb.h, t->ink);
    text_free(&tb); text_free(&tr);
    return h;
}

/* .pick: a radio, "דיסק 2" over its details, the size at the far edge. The
 * chosen row: a 2px line and the soft fill -- red for a destructive tool
 * (.pick.sel), blue otherwise (.pick.bsel). */
static void pick_row(App *a, cairo_t *cr, Rect r, const char *title, const char *detail, const char *size,
                     const char *small, int sel, int red, int id) {
    const Theme *t = a->theme;
    int hov = hovered(a, r);
    Rgb c = red ? t->danger : t->indigo;
    draw_fill_rrect(cr, r, RADIUS_SM, sel ? (red ? t->danger_soft : t->indigo_soft) : t->surface);
    draw_border_rrect(cr, r, RADIUS_SM, sel ? c : hov ? t->indigo : t->hair, sel ? 2 : 1);
    double xr = r.x + r.w - 14, cy = r.y + r.h / 2;
    cairo_new_sub_path(cr);
    if (sel) { cairo_arc(cr, xr - 8, cy, 5.5, 0, 2 * M_PI); cairo_set_line_width(cr, 5); draw_set(cr, c); cairo_stroke(cr); }
    else { cairo_arc(cr, xr - 8, cy, 7, 0, 2 * M_PI); cairo_set_line_width(cr, 2); draw_set(cr, t->hair); cairo_stroke(cr); }
    double tx = xr - 16 - 12, iw = r.w - 28 - 28 - 190;
    Text tt = tools_line(cr, FONT_SANS, 14, 600, title, iw, 1), td = tools_line(cr, FONT_SANS, 12.5, 400, detail, iw, 1);
    double by = cy - (tt.h + td.h) / 2;
    text_draw_r(cr, &tt, tx, by, t->ink); text_draw_r(cr, &td, tx, by + tt.h, t->muted);
    Text ts = text_make(cr, FONT_MONO, 15, 500, size, 0, DIR_LTR), tm = text_make(cr, FONT_MONO, 11.5, 400, small, 0, DIR_LTR);
    pango_layout_set_width(tm.layout, 180 * PANGO_SCALE); pango_layout_set_ellipsize(tm.layout, PANGO_ELLIPSIZE_END);
    pango_layout_get_pixel_size(tm.layout, &tm.w, &tm.h);
    double sy = cy - (ts.h + tm.h) / 2, sx = r.x + 14;   /* .sz: at the far edge, text-align left */
    text_draw(cr, &ts, sx, sy, t->ink); text_draw(cr, &tm, sx, sy + ts.h, t->muted);
    text_free(&tt); text_free(&td); text_free(&ts); text_free(&tm);
    hit_add(a, r, id);
}

/* ---- the run view: what will happen, the input, the button ---------------------- */

static void tools_confirm_view(App *a, cairo_t *cr, double W, double H, double head_h) {
    const Theme *t = a->theme;
    const Tool *tool = &a->tools[a->tool_sel];
    int level = tools_risk_level(tool->risk), kind = arg_kind(tool), compact = H < 700;
    double x0, cw; tools_col(W, &x0, &cw, 0);
    double y = head_h + (compact ? 16 : 30), bottom = H - N_STATUS_H - (compact ? 10 : 16);
    char crumb[160]; crumb_of(a, crumb, sizeof crumb);
    y = tools_title(a, cr, x0, cw, y, crumb, tool->title, tool->what, level, 0) + (compact ? 12 : 20);
    if (level == 2) y += alert_box(a, cr, x0, cw, y, 1, "מה שיימחק לא יחזור.", "ודאו שזה המחשב והדיסק הנכונים לפני שממשיכים.") + 12;
    else if (level == 1) y += alert_box(a, cr, x0, cw, y, 0, "הכלי משנה את המחשב הזה.", "ודאו שזה המחשב הנכון לפני שממשיכים.") + 12;
    double btn_y = bottom - 38, list_bottom = btn_y - 18 - (ERROR_MIN_H + 4);
    if (kind) {   /* the question: the label from args (mockup h3, 14px 600) */
        Text q = tools_line(cr, FONT_SANS, 14, 600, kind == 3 ? tool->args : tool->args + 5, cw, 1);
        text_draw_r(cr, &q, x0 + cw, y, t->ink); y += q.h + 8; text_free(&q);
    }
    double pitch = 62;
    if (kind == 1 || kind == 2) {
        ToolDisk d[MAX_DISKS + LC_MAX_DISKS];
        int n = kind == 1 ? tools_disks(a, d, MAX_DISKS + LC_MAX_DISKS) : a->ntool_parts;
        int sel = kind == 1 ? a->tool_disk_sel : a->tool_part_sel;
        if (kind == 2 && !a->tool_parts_ok) {   /* principle 5: not read is not "none" -- orange */
            n = 0;
            Text e = text_make(cr, FONT_SANS, 14, 600, "לא הצלחנו לקרוא את המחיצות של המחשב הזה — אין ממה לבחור.", (int)cw, DIR_RTL);
            text_draw_r(cr, &e, x0 + cw, y, t->warning_ink); y += e.h + 8; text_free(&e);
        } else if (!n) {
            Text e = text_make(cr, FONT_SANS, 14, 400, kind == 1 ? "לא נמצא דיסק במחשב הזה." : "אין מחיצות במחשב הזה.", (int)cw, DIR_RTL);
            text_draw_r(cr, &e, x0 + cw, y, t->muted); y += e.h + 8; text_free(&e);
        }
        int per = (int)floor((list_bottom - y + 6) / pitch);
        if (per < 1) per = 1;
        int first = sel >= per ? sel - per + 1 : 0;
        for (int i = first; i < n && i < first + per; i++) {
            char sz[32], detail[400], small[96];
            if (kind == 1) {
                fmt_bytes(sz, sizeof sz, (double)d[i].size);
                int named = strcmp(d[i].label, d[i].model) != 0;   /* "דיסק 2" over its model; a model once */
                char m[120], o[240];   /* Latin runs (the model, /dev/sda) isolated: a slash does not jump sides */
                ltr_isolate(named ? d[i].model : "", m, sizeof m);
                if (line_is_ascii(d[i].on)) ltr_isolate(d[i].on, o, sizeof o); else snprintf(o, sizeof o, "%s", d[i].on);
                snprintf(detail, sizeof detail, "%s%s%s", named ? m : "", named && d[i].on[0] ? " · " : "", o);
                snprintf(small, sizeof small, "%s", d[i].model);
                pick_row(a, cr, (Rect){ x0, y, cw, pitch - 6 }, d[i].label, detail, sz, small, i == sel, level == 2, HIT_TOOL_DISK_BASE + i);
            } else {
                const ToolPart *p = &a->tool_parts[i]; char ttl[64];
                fmt_bytes(sz, sizeof sz, (double)p->size);
                snprintf(ttl, sizeof ttl, "מחיצה %s", p->dev);
                snprintf(detail, sizeof detail, "%s%s%s · בדיסק %s", p->fstype[0] ? p->fstype : "סוג לא מזוהה",
                         p->label[0] ? " · " : "", p->label, p->disk[0] ? p->disk : "לא ידוע");
                snprintf(small, sizeof small, "/dev/%s", p->dev);
                pick_row(a, cr, (Rect){ x0, y, cw, pitch - 6 }, ttl, detail, sz, small, i == sel, level == 2, HIT_TOOL_PART_BASE + i);
            }
            y += pitch;
        }
        if (n > per) {
            char more[64]; snprintf(more, sizeof more, "%d–%d מתוך %d · ↑ ↓", first + 1, first + per < n ? first + per : n, n);
            Text m = text_make(cr, FONT_SANS, 12.5, 400, more, 0, DIR_RTL);
            text_draw_r(cr, &m, x0 + cw - 4, y - 2, t->muted); y += m.h; text_free(&m);
        }
    } else if (kind == 3) {
        draw_field(a, cr, (Rect){ x0, y, cw, field_height(cr) }, a->tool_arg, 0, NULL, HIT_TOOL_ARG);
        y += field_height(cr) + 8;
    }
    if (a->tool_error[0]) {
        Text err = error_make(cr, a->tool_error, cw);
        text_draw_r(cr, &err, x0 + cw, fmin(y + 4, list_bottom + 4), t->danger_fg); text_free(&err);
        y += err.h + 4;
    }
    /* .n-btns: the run button opens once what tool_run() needs is there, red for a
     * destructive tool, named after its target; "…" when the #1270 dialog follows */
    int ready = (kind != 1 || a->tool_disk_sel >= 0) && (kind != 2 || (a->tool_parts_ok && a->tool_part_sel >= 0)) &&
                (kind != 3 || a->tool_arg[0]);
    char tl[SLOT_NAME_MAX + 64], run[SLOT_NAME_MAX + 96]; target_label(a, tl, sizeof tl);
    snprintf(run, sizeof run, "הרץ%s%s%s", tl[0] && kind != 3 ? " על " : "", kind != 3 ? tl : "", level ? "…" : "");
    double by = fmin(y + 18, btn_y), xr = x0 + cw;
    xr -= tools_btn(a, cr, xr, by, run, NULL, !ready ? TB_OFF : level == 2 ? TB_DANGER : TB_PRIMARY, HIT_TOOL_RUN) + 10;
    xr -= tools_btn(a, cr, xr, by, "חזרה", "Esc", TB_PLAIN, HIT_TOOL_LIST) + 10;
    if (level) {
        Text n = text_make(cr, FONT_SANS, 12.5, 400, "אחרי הלחיצה נפתח חלון אישור.", 0, DIR_RTL);
        text_draw_r(cr, &n, xr, by + (38 - n.h) / 2, t->muted); text_free(&n);
    }
}

/* ---- the result: the card of the run ----------------------------------------------- */

static void spinner_draw(cairo_t *cr, Rgb c, double cx, double cy, double r, double angle) {
    cairo_set_line_width(cr, 2.5);
    cairo_new_sub_path(cr);
    cairo_arc(cr, cx, cy, r, angle, angle + 1.5 * M_PI);
    draw_set(cr, c); cairo_stroke(cr);
}

/* .wr.ok / .wr.bad / .wr.nc of mockup 14: the fill, the 3px line, the words */
static const char *rc_word(int rc, Rgb *bg, Rgb *line, Rgb *ink, const Theme *t) {
    switch (rc) {
    case 0:  *bg = t->success_soft; *line = t->led_ok;       *ink = t->success_ink; return "✓ הסתיים";
    case 1:  *bg = t->danger_soft;  *line = t->danger;       *ink = t->danger_fg;   return "✕ נמצאה בעיה";
    case 2:  *bg = t->surface;      *line = t->warning_line; *ink = t->warning_ink; return "? לא הצלחנו לבדוק";   /* dashed */
    case 3:  *bg = t->danger_soft;  *line = t->danger;       *ink = t->danger_fg;   return "✕ האישור לא תואם";
    case 4:  *bg = t->danger_soft;  *line = t->danger;       *ink = t->danger_fg;   return "✕ כלי לא מוכר";
    default: *bg = t->danger_soft;  *line = t->danger;       *ink = t->danger_fg;   return "✕ נכשל";
    }
}

/* The save line -- dest=<words>|<path>[|<free>] in the tool's output (mockup 14
 * .dest): the green led, where, the path (mono, LTR), the free space at the end. */
static double dest_box(App *a, cairo_t *cr, double x0, double cw, double y, const char *rec) {
    const Theme *t = a->theme;
    char buf[600]; cp(buf, sizeof buf, rec);
    char *f[3]; fields(buf, f, 3);
    double h = 38, xr = x0 + cw - 14;
    Rect r = { x0, y, cw, h };
    draw_fill_rrect(cr, r, RADIUS_SM, t->surface); draw_border_rrect(cr, r, RADIUS_SM, t->hair, 1);
    draw_led(cr, xr - 5, y + h / 2, 10, t->led_ok); xr -= 10 + 10;
    double free_w = 0;
    if (f[2][0]) {   /* "פנוי: 1.2 TB" -- the number and its unit isolated LTR, as the mockup's <bdi> */
        char iso[200]; size_t k = strcspn(f[2], "0123456789");
        snprintf(iso, sizeof iso, "%.*s\xE2\x81\xA6%s\xE2\x81\xA9", (int)k, f[2], f[2] + k);
        Text fr = text_make(cr, FONT_SANS, 12, 400, iso, 0, DIR_RTL);
        text_draw(cr, &fr, x0 + 14, y + (h - fr.h) / 2, t->muted); free_w = fr.w + 14; text_free(&fr);
    }
    Text w = tools_line(cr, FONT_SANS, 13.5, 400, f[0], (cw - 28 - 20 - free_w) / 2, 1);
    text_draw_r(cr, &w, xr, y + (h - w.h) / 2, t->ink); xr -= w.w + 10;
    if (f[1][0]) {
        Text pth = text_make(cr, FONT_MONO, 11.5, 400, f[1], 0, DIR_LTR);
        pango_layout_set_width(pth.layout, (int)fmax(10, xr - (x0 + 14 + free_w)) * PANGO_SCALE);
        pango_layout_set_ellipsize(pth.layout, PANGO_ELLIPSIZE_START);
        pango_layout_get_pixel_size(pth.layout, &pth.w, &pth.h);
        text_draw(cr, &pth, xr - pth.w, y + (h - pth.h) / 2, t->muted); text_free(&pth);
    }
    return h;
}

/* The save line of a tool's output -> the record dest_box draws, or NULL. Two spellings:
 * "dest=<words>|<path>[|<free>]", and the line agent/lib/tools/tooldest.sh really prints
 * (interfaces.md, the toolbox chapter): "יעד השמירה: <where> · <path> · פנוי: N MB". */
#define DEST_SAYS "יעד השמירה: "
#define DEST_SEP  " · "
static const char *dest_rec(const char *l, char *buf, size_t n) {
    if (!strncmp(l, "dest=", 5)) return l + 5;
    if (strncmp(l, DEST_SAYS, sizeof DEST_SAYS - 1)) return NULL;
    char rest[600]; cp(rest, sizeof rest, l + sizeof DEST_SAYS - 1);
    char *path = strstr(rest, DEST_SEP), *fr = NULL;
    if (path) { *path = 0; path += sizeof DEST_SEP - 1; fr = strstr(path, DEST_SEP); }
    if (fr) { *fr = 0; fr += sizeof DEST_SEP - 1; }
    snprintf(buf, n, "נשמר %s|%s|%s", rest, path ? path : "", fr ? fr : "");
    return buf;
}

static void tools_output_view(App *a, cairo_t *cr, double W, double H, double head_h) {
    const Theme *t = a->theme;
    const Tool *tool = &a->tools[a->tool_sel];
    int level = tools_risk_level(tool->risk), compact = H < 700;
    double x0, cw; tools_col(W, &x0, &cw, 0);
    double y = head_h + (compact ? 16 : 30), bottom = H - N_STATUS_H - (compact ? 10 : 16);
    char crumb[160], tl[SLOT_NAME_MAX + 64], h1[200];
    crumb_of(a, crumb, sizeof crumb); target_label(a, tl, sizeof tl);
    snprintf(h1, sizeof h1, a->tool_running ? "%s…" : "%s", tool->title);
    y = tools_title(a, cr, x0, cw, y, crumb, h1, a->tool_running ? "הכלי רץ. התוצאה תופיע כאן כשהוא יסיים." : tool->what,
                    level, 0) + (compact ? 12 : 20);
    /* the save line is the tool's own record: drawn under the card, not printed as text */
    const char *dest = NULL; int nl = 0; char dest_buf[700];
    for (int i = 0; i < a->tool_nlines; i++) {
        const char *l = a->tool_out + a->tool_line_off[i];
        const char *d = dest_rec(l, dest_buf, sizeof dest_buf);
        if (d) dest = d; else nl++;
    }
    if (a->tool_running) dest = NULL;
    double btn_y = bottom - 38, card_bottom = btn_y - 18 - (dest ? 38 + 10 : 0);
    Rgb bg = t->surface, line = t->hair, ink = t->action_fg;
    const char *word = "רץ…";
    if (!a->tool_running) word = rc_word(a->tool_rc, &bg, &line, &ink, t);
    /* .wr-h: the target in bold, the tool in muted words, the verdict at the end */
    Text wb = tools_line(cr, FONT_SANS, 14, 700, tl[0] ? tl : "המחשב הזה", cw / 3, 1);
    Text ws = text_make(cr, FONT_SANS, 13.5, 600, word, 0, DIR_RTL);
    double head = 12 + fmax(wb.h, ws.h) + 10, ch = head + 2;
    if (!a->tool_running) ch = fmin(card_bottom - y, head + 8 + (nl ? nl * 19.0 : 22) + 10);
    if (ch < head + 2) ch = head + 2;
    Rect r = { x0, y, cw, ch };
    draw_fill_rrect(cr, r, RADIUS_SM, bg);
    if (!a->tool_running && a->tool_rc == 2) { double d[] = { 6, 4 }; cairo_set_dash(cr, d, 2, 0); }   /* "לא נבדק": dashed */
    draw_border_rrect(cr, r, RADIUS_SM, line, a->tool_running ? 1 : 3);
    cairo_set_dash(cr, NULL, 0, 0);
    double xr = x0 + cw - 16, hy = y + 12;
    text_draw_r(cr, &wb, xr, hy, t->ink);
    Text tn = tools_line(cr, FONT_SANS, 12.5, 400, tool->title, cw / 3, 1);
    text_draw_r(cr, &tn, xr - wb.w - 14, hy + text_baseline(&wb) - text_baseline(&tn), t->muted); text_free(&tn);
    if (a->tool_running) {
        spinner_draw(cr, t->indigo, x0 + 16 + 8, hy + ws.h / 2.0, 7, a->tool_spin);
        text_draw(cr, &ws, x0 + 16 + 16 + 8, hy, ink);
    } else text_draw(cr, &ws, x0 + 16, hy, ink);
    text_free(&wb); text_free(&ws);
    int shown_all = 1;
    if (!a->tool_running) {   /* the tool's text, under a line (.wr-d) */
        cairo_rectangle(cr, x0 + 16, y + head, cw - 32, 1); draw_set(cr, t->track); cairo_fill(cr);
        double ly = y + head + 8, inner = cw - 32, lines_bottom = y + ch - 8;
        if (!nl) {
            Text none = text_make(cr, FONT_SANS, 13, 400, "(הכלי לא הדפיס כלום)", 0, DIR_RTL);
            text_draw_r(cr, &none, xr, ly, t->muted); text_free(&none);
        }
        if (a->tool_scroll < 0 || a->tool_scroll >= nl) a->tool_scroll = 0;
        int skip = a->tool_scroll;
        for (int i = 0; i < a->tool_nlines; i++) {
            const char *l = a->tool_out + a->tool_line_off[i];
            char skip_buf[700];
            if (dest_rec(l, skip_buf, sizeof skip_buf)) continue;
            if (skip > 0) { skip--; continue; }
            int ascii = line_is_ascii(l);
            Text tx = ascii ? text_make(cr, FONT_MONO, 12.5, 400, l[0] ? l : " ", (int)inner, DIR_LTR)
                            : text_make(cr, FONT_SANS, 13, 400, l, (int)inner, DIR_RTL);
            if (ly + tx.h > lines_bottom) { text_free(&tx); shown_all = 0; break; }
            if (!ascii) {   /* a Hebrew line is an RTL paragraph even when it starts with a Latin word */
                pango_layout_set_auto_dir(tx.layout, FALSE);
                pango_layout_get_pixel_size(tx.layout, &tx.w, &tx.h);
            }
            if (ascii) text_draw(cr, &tx, x0 + 16, ly, t->ink); else text_draw_r(cr, &tx, xr, ly, t->ink);
            ly += tx.h + 2;
            text_free(&tx);
        }
    }
    y += ch + 10;
    if (dest) y += dest_box(a, cr, x0, cw, y, dest) + 10;
    double by = fmin(y + 8, btn_y), bx = x0 + cw;
    if (!a->tool_running) {
        bx -= tools_btn(a, cr, bx, by, "חזרה לכלים", "Esc", TB_PRIMARY, HIT_TOOL_LIST) + 10;
        bx -= tools_btn(a, cr, bx, by, "הרץ שוב", NULL, TB_PLAIN, HIT_TOOL_AGAIN) + 10;
        if (!shown_all || a->tool_scroll > 0) {   /* the text is longer than the card */
            bx -= tools_btn(a, cr, bx, by, "למטה", "PgDn", shown_all ? TB_OFF : TB_PLAIN, HIT_TOOL_DOWN) + 10;
            tools_btn(a, cr, bx, by, "למעלה", "PgUp", a->tool_scroll > 0 ? TB_PLAIN : TB_OFF, HIT_TOOL_UP);
        }
    } else {
        Text n = text_make(cr, FONT_SANS, 12.5, 400, "אפשר לצאת מהארגז (F3) — הכלי ממשיך לרוץ, והתוצאה תחכה כאן.", 0, DIR_RTL);
        text_draw_r(cr, &n, bx, by + (38 - n.h) / 2, t->muted); text_free(&n);
    }
}

void screen_tools(App *a, cairo_t *cr, double W, double H, double head_h) {
    if ((a->tools_view == TOOLS_CONFIRM || a->tools_view == TOOLS_OUTPUT) && (a->tool_sel < 0 || a->tool_sel >= a->ntools))
        a->tools_view = TOOLS_HOME;
    switch (a->tools_view) {
    case TOOLS_CONFIRM: tools_confirm_view(a, cr, W, H, head_h); break;
    case TOOLS_OUTPUT:  tools_output_view(a, cr, W, H, head_h); break;
    case TOOLS_CAT:     tools_cat_view(a, cr, W, H, head_h); break;
    default:            tools_home_view(a, cr, W, H, head_h); break;
    }
}
