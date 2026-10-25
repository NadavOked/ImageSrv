/* #1304 (Nadav 12/10): the cloner's standby screen -- mockup A "חזית המארז",
 * docs/design/native/standby-a/ (standby-a.html + the four PNGs). One status
 * line on top, then the drawer cage as it stands in the machine (drawer 1 at
 * the top), and IP / MAC / hello in the status bar. No background art.
 *
 * Every value comes from the state file (clonergui.sh): cdisk= (port, model,
 * size, SMART verdict, the server's failure cause), cslot_name= (#704) and
 * hello_age= / hello_rc= (guistate.sh gui_hello_state). What the agent does
 * not send is not drawn -- see the README section of this screen. */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include "lcui.h"   /* #2030: card_used_text */
#include "screens_rounds.h"

#define SB_CAGE_W   980.0   /* .A .st / .cage / .note width */
#define SB_BAY_H    132.0   /* .A .bay height */
#define SB_BAY_MIN  76.0    /* a fourth drawer and up shrink the bays to fit, not past this */
#define SB_BAY_GAP  10.0
#define SB_NUM_W    110.0   /* .A .num */
#define SB_GRIP_W   56.0    /* .A .grip */
#define SB_FRESH_S  120     /* a 200 older than this is not "connected" (the menu pill's rule) */

int standby_conn(const State *s) {
    if (s->hello_age < 0) return STANDBY_UNKNOWN;          /* no hello has finished yet */
    if (s->hello_rc == 200 && s->hello_age <= SB_FRESH_S) return STANDBY_OK;
    return STANDBY_OFF;                                     /* no answer, an error, or a stale 200 */
}

/* Market size, as on the disk's label: 500107862016 -> "500 GB". */
void disk_size(unsigned long long b, char *out, size_t n) {
    double v = (double)b;
    if (v >= 999.5e9) snprintf(out, n, "%.3g TB", v / 1e12);
    else              snprintf(out, n, "%.3g GB", v / 1e9);
}

static void ago(int s, char *out, size_t n) {
    if (s < 120) snprintf(out, n, "לפני %d שניות", s);
    else if (s < 7200) snprintf(out, n, "לפני %d דקות", s / 60);
    else snprintf(out, n, "לפני %d שעות", s / 3600);
}

/* The wall-clock time <age> seconds ago; --png counts back from the header's 12:45. */
static void clock_ago(const App *a, int age, char *out, size_t n) {
    if (a->fixed_clock) {
        int t = ((12 * 3600 + 45 * 60 - age) % 86400 + 86400) % 86400;
        snprintf(out, n, "%02d:%02d", t / 3600, t / 60 % 60);
        return;
    }
    time_t then = time(NULL) - age; struct tm tmv;
#ifdef _WIN32
    if (localtime_s(&tmv, &then) == 0) { strftime(out, n, "%H:%M", &tmv); return; }
#else
    if (localtime_r(&then, &tmv)) { strftime(out, n, "%H:%M", &tmv); return; }
#endif
    snprintf(out, n, "--:--");
}

/* .led: a dot with a 4px ring in the soft tone; ring 0 = the idle dot. */
void sb_led(cairo_t *cr, double cx, double cy, double d, Rgb c, Rgb soft, int ring, double alpha) {
    if (ring) { cairo_arc(cr, cx, cy, d / 2 + 4, 0, 2 * M_PI); draw_set_a(cr, soft, alpha); cairo_fill(cr); }
    cairo_arc(cr, cx, cy, d / 2, 0, 2 * M_PI); draw_set_a(cr, c, alpha); cairo_fill(cr);
}

/* "השרת לא עונה": a red X where the green dot stood. */
static void sb_x(cairo_t *cr, double cx, double cy, Rgb c, Rgb soft) {
    cairo_arc(cr, cx, cy, 12, 0, 2 * M_PI); draw_set(cr, soft); cairo_fill(cr);
    cairo_set_line_width(cr, 2.6); cairo_set_line_cap(cr, CAIRO_LINE_CAP_ROUND);
    cairo_move_to(cr, cx - 5, cy - 5); cairo_line_to(cr, cx + 5, cy + 5);
    cairo_move_to(cr, cx + 5, cy - 5); cairo_line_to(cr, cx - 5, cy + 5);
    draw_set(cr, c); cairo_stroke(cr);
}

static Rgb soft_of(const Theme *t, int lvl) {
    return lvl == 2 ? t->danger_soft : lvl == 1 ? t->warning_soft : t->success_soft;
}

/* What the chip says, by the #1580 rule (smart_level, Nadav 17/10): fail red
 * (written only after the red button; no answer skips), failed_last and an old
 * agent's warn orange -- a question comes before writing -- the rest green. */
static const char *chip_he(const char *smart) {
    if (!strcmp(smart, "passed")) return RLM "SMART תקין";   /* RLM: a Latin-first label stays RTL */
    if (!strcmp(smart, "failed_last")) return "נכשל בשיכפול הקודם";
    if (!strcmp(smart, "fail")) return "SMART תקול";
    if (smart_level(smart) == 1) return "אזהרת SMART";
    return RLM "SMART לא נבדק";
}

/* The line under the drawer's name: why the chip is not green. fail is only
 * ever health_failed (smart.sh smart_classify); failed_last carries the
 * server's cause (#874, the console's FAILURE_CAUSE_HE words). */
static const char *reason_he(const IdleDisk *d) {
    /* #1366: fail is what stops an HP BIOS on "press F1"; #1580 (17/10): red */
    if (!strcmp(d->smart, "fail"))
        return !strcmp(d->cause, "readonly") ? smart_reason_text(d->cause)   /* #1742 */
             : !strcmp(d->cause, "media_readonly") ? smart_reason_text(d->cause)   /* #1921: NVMe */
             : !strcmp(d->cause, "health_unread") ? smart_reason_text(d->cause)   /* #2078: SMART not read -- not "health failed" */
             /* an older agent's red 197/198 (before #1921 they are orange): said in words, not as the F1 */
             : !strcmp(d->cause, "pending_sectors") || !strcmp(d->cause, "uncorrectable") ? smart_reason_text(d->cause)
             : "ה-BIOS יעצור ב-F1 — SMART: בדיקת הבריאות נכשלה";
    if (!strcmp(d->smart, "warn") && d->cause[0]) return smart_reason_text(d->cause);   /* #1859: why orange */
    if (!strcmp(d->smart, "failed_last"))
        return !strcmp(d->cause, "cable") ? "סיבה: כבל/חריץ" : !strcmp(d->cause, "disk") ? "סיבה: הדיסק"
             : !strcmp(d->cause, "readonly") ? "סיבה: הדיסק במצב קריאה בלבד" : "סיבה: לא סווג";   /* #1742 */
    return NULL;
}

/* #432: the drawer negotiated below its port's speed. Both read, or it is
 * not "slow" -- and not "fine" either: the sub line says "לא ידוע". */
static int link_slow(const IdleDisk *d) {
    return d->link[0] && d->link_max[0] && atof(d->link) < atof(d->link_max);
}

/* .pill: 24px, fully round, 12/600, a 7px dot at the start (the right). Right edge in, width out. */
double sb_bay_pill(cairo_t *cr, double right, double cy, const char *label, Rgb fg, Rgb bg, int emphasis) {
    Text v = text_make(cr, FONT_SANS, emphasis ? 13 : 12, 600, label, 0, DIR_RTL);
    double pad = emphasis ? 12 : 10, dot = emphasis ? 8 : 7, h = emphasis ? 28 : 24;
    double w = pad + dot + 6 + v.w + pad;
    Rect r = { right - w, cy - h / 2, w, h };
    draw_fill_rrect(cr, r, h / 2, bg);
    cairo_arc(cr, right - pad - dot / 2, cy, dot / 2, 0, 2 * M_PI); draw_set(cr, fg); cairo_fill(cr);
    text_draw_r(cr, &v, right - pad - dot - 6, cy - v.h / 2.0, fg);
    text_free(&v); return w;
}

double sb_pill(cairo_t *cr, double right, double cy, const char *label, Rgb fg, Rgb bg) {
    return sb_bay_pill(cr, right, cy, label, fg, bg, 0);
}

/* The empty slot: repeating-linear-gradient(135deg, slot 0 10px, sunken 10px 20px). */
void sb_hatch(cairo_t *cr, const Theme *t, Rect r) {
    int dark = t == &THEME_DARK;
    cairo_save(cr);
    cairo_rectangle(cr, r.x, r.y, r.w, r.h); cairo_clip(cr);
    draw_set(cr, t->art_bg); cairo_paint(cr);
    double d = 20 / sqrt(2.0);
    cairo_pattern_t *p = cairo_pattern_create_linear(r.x, r.y, r.x + d, r.y + d);
    Rgb s = dark ? (Rgb){ 0, 0, 0 } : t->art_a;
    double a = dark ? .18 : .35;
    cairo_pattern_add_color_stop_rgba(p, 0, s.r, s.g, s.b, a);
    cairo_pattern_add_color_stop_rgba(p, .5, s.r, s.g, s.b, a);
    cairo_pattern_add_color_stop_rgba(p, .5, s.r, s.g, s.b, 0);
    cairo_pattern_add_color_stop_rgba(p, 1, s.r, s.g, s.b, 0);
    cairo_pattern_set_extend(p, CAIRO_EXTEND_REPEAT);
    cairo_set_source(cr, p); cairo_paint(cr);
    cairo_pattern_destroy(p);
    cairo_restore(cr);
}

/* #2050: how loud a drawer is -- 2 red, 1 orange, 0 plain. The colour rules are the ones
 * that were here (SMART first, then a slow link / CRC / hardware note, then a cut write). */
static int sb_accent(const IdleDisk *d) {
    int lvl = d ? smart_level(d->smart) : 0;
    if (lvl || !d) return lvl;
    return link_slow(d) || d->crc_suspect || d->hw[0] || !strcmp(d->mark, "incomplete");
}

static void sb_bay(App *a, cairo_t *cr, Rect b, int bay,
                   const IdleDisk *d, const COptical *o) {
    const Theme *t = a->theme; const State *s = &a->st;
    int unmapped = bay == 0;
    int lvl = d ? smart_level(d->smart) : 0;
    Rgb col = d ? smart_color(t, d->smart) : t->led_idle;
    int slow = d && !lvl && (link_slow(d) || d->crc_suspect || d->hw[0]);   /* #432/#1366/#1921: the drawer's note, below a SMART warning */
    int inc = d && !lvl && !strcmp(d->mark, "incomplete");      /* #1798: a cut write -- orange, not usable */
    int acc = sb_accent(d);
    Rgb acol = lvl ? col : t->warn;
    Rgb line = t == &THEME_DARK ? t->art_line : t->art_c;   /* --mark-line: #9AADB8 / #4A5D6B */
    double cy = b.y + b.h / 2;

    cairo_save(cr);
    draw_rrect_path(cr, b, 4); cairo_clip(cr);
    draw_set(cr, acc ? (acc == 2 ? t->danger_soft : t->warning_soft) : d || o ? t->surface : t->porcelain); cairo_paint(cr);
    Rect face = { b.x + SB_GRIP_W + 1, b.y, b.w - SB_GRIP_W - SB_NUM_W - 2, b.h };
    if (!d && !o) sb_hatch(cr, t, face);
    cairo_rectangle(cr, b.x + b.w - SB_NUM_W, b.y, 1, b.h);   /* .num border-inline-end */
    cairo_rectangle(cr, b.x + SB_GRIP_W, b.y, 1, b.h);        /* .grip border-inline-start */
    if (!acc) { draw_set(cr, t->hair); cairo_fill(cr); } else cairo_new_path(cr);
    cairo_restore(cr);
    draw_border_rrect(cr, b, 4, acc ? acol : t->hair, acc ? 3 : 1);

    /* .num: the drawer's number, big, and "מגירה" */
    char num[16]; if (unmapped) snprintf(num, sizeof num, "%s", "—"); else snprintf(num, sizeof num, "%d", bay);
    Text nb = text_make(cr, FONT_MONO, 44, 600, num, 0, DIR_LTR);
    Text nl = text_make(cr, FONT_SANS, 11, 400, unmapped ? "לא ממופה" : "מגירה", 0, DIR_RTL);
    double nh = 44 + 6 + nl.h, ny = cy - nh / 2, nr = b.x + b.w - 20;
    text_draw_r(cr, &nb, nr, ny + (44 - nb.h) / 2.0, acc == 2 ? t->danger_fg : acc ? t->warning_ink : t->ink);
    text_draw_r(cr, &nl, nr, ny + 50, t->muted);
    text_free(&nb); text_free(&nl);

    /* .grip: the drawer's LED and the two handle lines */
    double gx = b.x + SB_GRIP_W / 2, gy = cy - 20;
    sb_led(cr, gx, gy + 5, 10, acc ? acol : d ? col : t->led_idle, soft_of(t, acc), d != NULL, 1);   /* #2050: the LED says the frame's colour */
    for (int i = 0; i < 2; i++) {
        draw_fill_rrect(cr, (Rect){ gx - 11, gy + 22 + i * 15, 22, 3 }, 1.5, line);
    }

    /* .face, from the right: the name block, the SMART chip; the size and
     * model (or "ריק") at the left end */
    double fr = face.x + face.w - 26, fl = face.x + 26;
    char name[SLOT_NAME_MAX + 16], sub[SLOT_NAME_MAX + 48];
    char lk[48] = "", slow_why[96] = "", conn[64];   /* #432: the link on the sub line, the slow reason */
    if (unmapped && d) {
        const char *slot_name = d->port >= 1 && d->port <= MAX_DRAWERS ? s->cslot_names[d->port - 1] : "";
        unmapped_label(slot_name, d->conn, d->port, "", name, sizeof name);   /* #2030: never the device name */
        if (d->link[0]) snprintf(sub, sizeof sub, "%s Gb/s", d->link); else sub[0] = 0;
    } else if (unmapped && o) {
        snprintf(name, sizeof name, "%s", "כונן CD/DVD");
        conn_text(o->conn, 0, "", sub, sizeof sub);
    } else {
        cloner_bay_conn(s, bay, conn, sizeof conn);
        if (d && d->link[0]) snprintf(lk, sizeof lk, "%s Gb/s", d->link);
        else if (d) snprintf(lk, sizeof lk, "קישור לא ידוע");
        int named = s->ncbays > 0 ? s->cbays[bay - 1].name[0] != 0 : s->cslot_names[bay - 1][0] != 0;
        if (o) {
            snprintf(name, sizeof name, "%s", "כונן CD/DVD");
            if (named) snprintf(sub, sizeof sub, "דיסק %d · %s", bay, conn); else snprintf(sub, sizeof sub, "%s", conn);
        }
        else if (named) {   /* the operator's own name for the drawer (#704) */
            cloner_slot_label(s, bay, name, sizeof name);
            snprintf(sub, sizeof sub, "דיסק %d · %s%s%s", bay, conn, lk[0] ? " · " : "", lk);
        } else {   /* #2030: the connection is the name -- the big number already says which drawer */
            snprintf(name, sizeof name, "%s", conn); snprintf(sub, sizeof sub, "%s", lk);
        }
    }
    const char *why = d ? reason_he(d) : NULL;
    Rgb why_c = col;
    if (!why && inc) { why = "כתיבה לא הושלמה — לא שמיש"; why_c = t->warn; }   /* #1798 */
    if (!why && slow) {
        if (link_slow(d)) snprintf(slow_why, sizeof slow_why, "קישור %s מתוך %s Gb/s — לבדוק כבל", d->link, d->link_max);
        else if (d->crc_suspect) snprintf(slow_why, sizeof slow_why, "כבל או מגירה חשודים — %ld שגיאות", d->crc_suspect);   /* #1366 */
        why = d->hw[0] && !slow_why[0] ? d->hw : slow_why; why_c = t->warn;   /* #1921: a reset, a drawer that dropped, USB 2.0 */
    }
    /* #1798: a mark that could not be read is "not checked" -- said, never drawn as clean */
    if (!why && d && !strcmp(d->mark, "unchecked")) { why = "לא נבדק אם הכתיבה האחרונה הושלמה"; why_c = t->muted; }
    Text tn = text_make(cr, FONT_SANS, 18, 600, name, 0, DIR_RTL);
    Text ts = sub[0] ? text_make(cr, FONT_SANS, 12, 400, sub, 0, DIR_RTL) : (Text){ NULL, 0, 0 };
    Text tw = why ? text_make(cr, FONT_SANS, 12, 400, why, 0, DIR_RTL) : (Text){ NULL, 0, 0 };
    double bh = tn.h + (ts.layout ? 3 + ts.h : 0) + (why ? 3 + tw.h : 0), by = cy - bh / 2;
    text_draw_r(cr, &tn, fr, by, t->ink); by += tn.h + 3;
    if (ts.layout) { text_draw_r(cr, &ts, fr, by, t->muted); by += ts.h + 3; }
    if (why) text_draw_r(cr, &tw, fr, by, why_c);
    double names_w = fmax(tn.w, fmax(ts.w, tw.w));
    text_free(&tn); text_free(&ts); if (why) text_free(&tw);

    if (o) {
        Text md = o->model[0] ? text_make(cr, FONT_MONO, 13, 400, o->model, 0, DIR_LTR)
                              : (Text){ NULL, 0, 0 };
        Text no = text_make(cr, FONT_SANS, 13, 400, "לא יעד לשיכפול", 0, DIR_RTL);
        double oh = (md.layout ? md.h + 2 : 0) + no.h, oy = cy - oh / 2;
        if (md.layout) { text_draw(cr, &md, fl, oy, t->muted); oy += md.h + 2; text_free(&md); }
        text_draw(cr, &no, fl, oy, t->muted); text_free(&no);
        return;
    }
    if (!d) {
        Text e = text_make(cr, FONT_SANS, 15, 400, "ריק — אפשר להכניס דיסק", 0, DIR_RTL);
        text_draw(cr, &e, fl, cy - e.h / 2.0, t->muted); text_free(&e);
        return;
    }
    sb_bay_pill(cr, fr - names_w - 22, cy, lvl ? chip_he(d->smart) : inc ? "לא שמיש" : slow ? "אזהרה" : chip_he(d->smart),
                acc ? t->on_ink : col, acc ? acol : soft_of(t, lvl), acc);
    char size[32]; disk_size(d->size, size, sizeof size);
    Text sz = text_make(cr, FONT_MONO, 22, 600, size, 0, DIR_LTR);
    Text md = d->model[0] ? text_make(cr, FONT_MONO, 13, 400, d->model, 0, DIR_LTR)
                          : text_make(cr, FONT_SANS, 13, 400, "דגם לא דווח", 0, DIR_RTL);
    /* #2030: what is on it now -- what the next round will erase. "לא נמדד" when diskused.sh has no number */
    const CUsed *cu = cused_for_dev(s, d->dev); char ub[96];
    card_used_text(USED_NOW, cu ? cu->used : -1, ub, sizeof ub);
    Text ud = text_make(cr, FONT_SANS, 12, 400, ub, 0, DIR_RTL);
    double mh = sz.h + 2 + md.h + 3 + ud.h, my = cy - mh / 2;
    text_draw(cr, &sz, fl, my, t->ink);
    text_draw(cr, &md, fl, my + sz.h + 2, t->muted);
    text_draw(cr, &ud, fl, my + sz.h + 2 + md.h + 3, t->muted);
    text_free(&sz); text_free(&md); text_free(&ud);
}

void screen_standby(App *a, cairo_t *cr, double W, double H, double head_h) {
    const Theme *t = a->theme; const State *s = &a->st;
    int conn = standby_conn(s), bays = cloner_bay_count(s), present = 0, unmapped_disks = 0, unmapped_opticals = 0;
    for (int p = 1; p <= bays; p++) {
        const IdleDisk *d = idle_for_bay(s, p);
        present += d != NULL;
    }
    for (int i = 0; i < s->nidisks; i++) unmapped_disks += eff_drawer(s->idisks[i].drawer, s->idisks[i].port) == 0;
    for (int i = 0; i < s->ncopticals; i++) unmapped_opticals += eff_drawer(s->copticals[i].drawer, 0) == 0;
    int n = bays + unmapped_disks + unmapped_opticals;
    double cw = fmin(SB_CAGE_W, W - 2 * N_PAD), x0 = (W - cw) / 2, xr = x0 + cw;
    double y = head_h + 34;

    /* .st: the one status line */
    const char *h1 = conn == STANDBY_OK ? "מחובר. ממתין לסבב." : conn == STANDBY_OFF ? "השרת לא עונה" : "ממתין לתשובה מהשרת";
    const char *p1 = conn == STANDBY_OFF ? "המחשב ממשיך לנסות לבד. בדקו שכבל הרשת מחובר ושהשרת דולק."
                                         : "המחשב יצטרף לבד כשייפתח סבב — אין צורך לגעת בו.";
    const char *srv = s->server_name[0] ? s->server_name : s->server_addr[0] ? s->server_addr : "לא ידוע";
    char since[48] = "";
    if (conn == STANDBY_OFF && s->hello_rc == 200) {   /* a stale 200: when it last answered is known */
        char hm[16]; clock_ago(a, s->hello_age, hm, sizeof hm); snprintf(since, sizeof since, "מאז %s", hm);
    }
    Text to1 = text_make(cr, FONT_SANS, 12, 400, conn == STANDBY_OK ? "מחובר אל" : conn == STANDBY_OFF ? "אין תשובה מ" : "מתחבר אל", 0, DIR_RTL);
    Text to2 = text_make(cr, FONT_MONO, 15, 600, srv, 0, DIR_LTR);
    Text to3 = since[0] ? text_make(cr, FONT_SANS, 12, 400, since, 0, DIR_RTL) : (Text){ NULL, 0, 0 };
    double to_w = fmax(to1.w, fmax(to2.w, to3.w)), to_h = to1.h + to2.h + to3.h;
    double tw = cw - 32 - to_w - 24;
    Text th = text_make(cr, FONT_SANS, 30, 600, h1, (int)tw, DIR_RTL);
    Text tp = text_make(cr, FONT_SANS, 14, 400, p1, (int)tw, DIR_RTL);
    /* the CSS line boxes (h1 30px/1.15, p 14px/1.3), not Pango's natural
     * line height -- which is taller and pushed the cage 6px down */
    double hh = th.h * 34.5 / text_line_height(cr, FONT_SANS, 30), ph = tp.h * 18.2 / text_line_height(cr, FONT_SANS, 14);
    double block = hh + 4 + ph, row = fmax(block, to_h), cy = y + row / 2;
    if (conn == STANDBY_OFF) sb_x(cr, xr - 8, cy, t->danger, t->danger_soft);
    else {
        double alpha = 1;   /* .live: breathe 2.4s -- a still frame under --png */
        struct timespec ts;
        if (conn == STANDBY_OK && !a->fixed_clock && timespec_get(&ts, TIME_UTC) == TIME_UTC)
            alpha = 1 - .55 * (.5 - .5 * cos((ts.tv_sec % 600 + ts.tv_nsec / 1e9) * 2 * M_PI / 2.4));
        sb_led(cr, xr - 8, cy, 16, conn == STANDBY_OK ? t->led_ok : t->led_idle, t->success_soft, conn == STANDBY_OK, alpha);
    }
    double ty = cy - block / 2;
    text_draw_r(cr, &th, xr - 32, ty + (hh - th.h) / 2, conn == STANDBY_OFF ? t->danger : t->ink);
    text_draw_r(cr, &tp, xr - 32, ty + hh + 4 + (ph - tp.h) / 2, t->muted);
    double oy = cy - to_h / 2;
    text_draw(cr, &to1, x0, oy, t->muted); oy += to1.h;
    text_draw(cr, &to2, x0, oy, t->ink); oy += to2.h;
    if (to3.layout) text_draw(cr, &to3, x0, oy, t->muted);
    text_free(&to1); text_free(&to2); if (to3.layout) text_free(&to3); text_free(&th); text_free(&tp);
    y = floor(y + row + 26 + .5);

    /* .note lines under the cage, measured first: the bays shrink to leave them room */
    Text notes[2 * MAX_DRAWERS]; Rgb note_c[2 * MAX_DRAWERS]; int note_l[2 * MAX_DRAWERS];
    int nn = 0; double notes_h = 0;
    const IdleDisk *disk_rows[MAX_DRAWERS]; int ndisk_rows = 0;
    for (int p = 1; p <= bays && ndisk_rows < MAX_DRAWERS; p++) {
        const IdleDisk *d = idle_for_bay(s, p); if (d) disk_rows[ndisk_rows++] = d;
    }
    for (int i = 0; i < s->nidisks && ndisk_rows < MAX_DRAWERS; i++)
        if (eff_drawer(s->idisks[i].drawer, s->idisks[i].port) == 0) disk_rows[ndisk_rows++] = &s->idisks[i];
    for (int kind = 0; kind < 2; kind++)
    for (int i = 0; i < ndisk_rows && nn < 2 * MAX_DRAWERS; i++) {
        const IdleDisk *d = disk_rows[i];
        if (kind == 0 ? !smart_level(d->smart) : !link_slow(d) && !d->crc_suspect && !d->hw[0]) continue;
        char label[SLOT_NAME_MAX + 16], msg[SLOT_NAME_MAX + 120], lmsg[SLOT_NAME_MAX + 300];   /* #1921: + the hw sentence */
        int bay = eff_drawer(d->drawer, d->port);
        if (bay > 0) cloner_slot_label(s, bay, label, sizeof label);
        else {
            const char *slot_name = d->port >= 1 && d->port <= MAX_DRAWERS ? s->cslot_names[d->port - 1] : "";
            unmapped_label(slot_name, d->conn, d->port, d->dev, label, sizeof label);
        }
        if (kind == 0) snprintf(msg, sizeof msg, RLM "%s: %s — לפני כתיבה אליו תופיע שאלה.", label, chip_he(d->smart));
        else if (link_slow(d)) snprintf(lmsg, sizeof lmsg, RLM "%s: קישור SATA %s מתוך %s Gb/s — לבדוק כבל או מגירה. לפני התחלת סבב תופיע אזהרה.",
                      label, d->link, d->link_max);
        else if (d->crc_suspect) snprintf(lmsg, sizeof lmsg, RLM "%s: כבל או מגירה חשודים — %ld שגיאות. לפני התחלת סבב תופיע אזהרה.",
                      label, d->crc_suspect);   /* #1366 */
        else snprintf(lmsg, sizeof lmsg, RLM "%s: %s", label, d->hw);   /* #1921: the hardware table's sentence */
        notes[nn] = text_make(cr, FONT_SANS, 13, 400, kind == 0 ? msg : lmsg, (int)(cw - 18), DIR_RTL);
        note_c[nn] = kind == 0 ? smart_color(t, d->smart) : t->warn;
        note_l[nn] = kind == 0 ? smart_level(d->smart) : 1;
        notes_h += (nn ? 6 : 14) + notes[nn].h; nn++;
    }

    /* .cage */
    Text lr = text_make(cr, FONT_SANS, 12, 400, "מגירות הדיסקים במחשב — מלמעלה למטה", 0, DIR_RTL);
    int troubled = 0;   /* #2050 (mockup 07): "4 מתוך 4 עם דיסק · 2 עם בעיה" -- the same disks the frames mark */
    for (int i = 0; i < s->nidisks; i++) troubled += sb_accent(&s->idisks[i]) > 0;
    char cnt[96]; snprintf(cnt, sizeof cnt, "%d מתוך %d עם דיסק", present, bays);
    if (troubled) { size_t u = strlen(cnt); snprintf(cnt + u, sizeof cnt - u, " · " RLM "%d עם בעיה", troubled); }
    Text ll = text_make(cr, FONT_SANS, 12, 400, cnt, 0, DIR_RTL);
    double lh = 16;   /* .lbl: 12px/1.3, on whole pixels so every hairline below is one crisp row */
    double chrome = 1 + 14 + lh + 10 + 14 + 1, bay_h = SB_BAY_H;
    double room = H - N_STATUS_H - 24 - notes_h - y - chrome - (n - 1) * SB_BAY_GAP;
    if (n * bay_h > room) bay_h = fmax(SB_BAY_MIN, floor(room / n));
    Rect cage = { x0, y, cw, chrome + n * bay_h + (n - 1) * SB_BAY_GAP };
    if (conn == STANDBY_OFF) cairo_push_group(cr);   /* [data-conn=off] .cage{opacity:.72} */
    int dark = t == &THEME_DARK;
    draw_box_shadow(cr, cage, 6, 10, 24, -14, dark ? (Rgb){ 0, 0, 0 } : t->header, dark ? .40 : .14);
    draw_fill_rrect(cr, cage, 6, t->surface); draw_border_rrect(cr, cage, 6, t->hair, 1);
    double by = cage.y + 15;
    text_draw_r(cr, &lr, cage.x + cage.w - 18, by + (lh - lr.h) / 2, t->muted);
    text_draw(cr, &ll, cage.x + 18, by + (lh - ll.h) / 2, t->muted);
    by += lh + 10;
    for (int p = 1; p <= bays; p++) {
        const IdleDisk *d = idle_for_bay(s, p);
        sb_bay(a, cr, (Rect){ cage.x + 15, by, cage.w - 30, bay_h }, p,
               d, d ? NULL : optical_for_bay(s, p));
        by += bay_h + SB_BAY_GAP;
    }
    for (int i = 0; i < s->nidisks; i++) if (eff_drawer(s->idisks[i].drawer, s->idisks[i].port) == 0) {
        sb_bay(a, cr, (Rect){ cage.x + 15, by, cage.w - 30, bay_h }, 0, &s->idisks[i], NULL);
        by += bay_h + SB_BAY_GAP;
    }
    for (int i = 0; i < s->ncopticals; i++) if (eff_drawer(s->copticals[i].drawer, 0) == 0) {
        sb_bay(a, cr, (Rect){ cage.x + 15, by, cage.w - 30, bay_h }, 0, NULL, &s->copticals[i]);
        by += bay_h + SB_BAY_GAP;
    }
    if (conn == STANDBY_OFF) { cairo_pop_group_to_source(cr); cairo_paint_with_alpha(cr, .72); }
    text_free(&lr); text_free(&ll);

    y = cage.y + cage.h;
    for (int i = 0; i < nn; i++) {
        y += i ? 6 : 14;
        sb_led(cr, xr - 5, y + notes[i].h / 2.0, 10, note_c[i], soft_of(t, note_l[i]), 1, 1);
        text_draw_r(cr, &notes[i], xr - 18, y, note_c[i]);
        y += notes[i].h; text_free(&notes[i]);
    }
    /* #2079 (mockup 02, Nadav 24/10): back to the boot choice -- a local clone or an image from
     * the server without a reboot (guibridge.sh choice-back). Not while a round is open for it. */
    y += 16;
    if (y + 34 > H - N_STATUS_H - 12) return;   /* no room under four drawers at 4:3 -- Esc still goes back */
    int locked = standby_back_locked(a);
    char why[200]; const char *img = s->sess_image[0] ? s->sess_image : s->cloner_image;
    if (locked) snprintf(why, sizeof why, "נפתח סבב למחשב הזה%s%s%s — אי אפשר לחזור עד שהסבב ייסגר.",
                         img[0] ? " (" : "", img, img[0] ? ")" : "");
    else snprintf(why, sizeof why, "שיכפול מקומי או שחזור אימג׳, בלי לאתחל.");
    double bw = lc_btn_width(cr, "חזרה למסך הבחירה", "Esc");
    if (locked) cairo_push_group(cr);
    lc_btn(a, cr, xr, y, 34, "חזרה למסך הבחירה", "Esc", BTN_PLAIN, locked ? HIT_NONE : HIT_WAIT_BACK, 0);
    if (locked) { cairo_pop_group_to_source(cr); cairo_paint_with_alpha(cr, .45); }
    Text wt = text_make(cr, FONT_SANS, 13, 400, why, (int)(cw - bw - 14), DIR_RTL);
    text_draw_r(cr, &wt, xr - bw - 14, y + (34 - wt.h) / 2, t->muted); text_free(&wt);
}

/* #2079: a round is open for this machine -- the way back waits for it to close */
int standby_back_locked(const App *a) { return a->st.has_session || a->st.has_round; }

/* #2079: Esc or "חזרה למסך הבחירה" on the wait says choice-back (guibridge.sh). 1 = taken. */
int standby_handle(App *a, const Event *e) {
    if (a->screen != SCREEN_STANDBY || standby_back_locked(a)) return 0;
    if (!((e->type == UIEV_KEY && e->key == KEYSYM_ESC) || (e->type == UIEV_CLICK && app_hit(a, e->x, e->y) == HIT_WAIT_BACK))) return 0;
    printf("choice-back\n\n"); fflush(stdout);
    return 1;
}

/* The status bar's left end on this screen: IP · MAC · the last hello, in
 * place of the key list (the screen takes no keys). Drawn from <left>. */
void standby_status_info(App *a, cairo_t *cr, double left, double cy) {
    const Theme *t = a->theme; const State *s = &a->st;
    char hello[160], when[48];
    if (s->hello_age < 0) snprintf(hello, sizeof hello, "טרם הסתיים hello ראשון");   /* RLM below: Latin-first lines stay RTL */
    else {
        ago(s->hello_age, when, sizeof when);
        if (s->hello_rc == 200 && standby_conn(s) == STANDBY_OK) snprintf(hello, sizeof hello, "השרת ענה hello 200 · %s", when);
        else if (s->hello_rc == 200) { char hm[16]; clock_ago(a, s->hello_age, hm, sizeof hm); snprintf(hello, sizeof hello, RLM "hello אחרון שנענה: %s", hm); }
        else if (s->hello_rc >= 0) snprintf(hello, sizeof hello, "השרת ענה hello %d · %s", s->hello_rc, when);
        else snprintf(hello, sizeof hello, RLM "hello אחרון %s · ללא תשובה", when);
    }
    Text h = text_make(cr, FONT_SANS, 12, 400, hello, 0, DIR_RTL);
    text_draw(cr, &h, left, cy - h.h / 2.0, t->muted); left += h.w + 18;
    const char *k[2] = { "MAC", "IP" }, *v[2] = { a->mac[0] ? a->mac : "—", a->ip[0] ? a->ip : "—" };
    for (int i = 0; i < 2; i++) {
        Text tv = text_make(cr, FONT_MONO, 12, 400, v[i], 0, DIR_LTR);
        Text tk = text_make(cr, FONT_SANS, 12, 400, k[i], 0, DIR_LTR);
        text_draw(cr, &tv, left, cy - tv.h / 2.0, t->ink); left += tv.w + 5;
        text_draw(cr, &tk, left, cy - tk.h / 2.0, t->muted); left += tk.w + 18;
        text_free(&tv); text_free(&tk);
    }
    text_free(&h);
}
