/* #st-room and #st-class -- the two cards whose bodies room.js and
 * classes.js build at runtime. The text is theirs; the numbers are
 * station.css (.room-machines / .room-row / .cls-bars / .menu-card). The
 * data comes from the --state file; the setup form (image, target) and the
 * confirm box are the operator's. The classroom wizard's steps 2 and 3
 * (machine grid, image list) are not built -- see README. */
#include <math.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include "screens_rounds.h"

/* ---- .room-machines rows (room.js machineRows / classes.js renderLive) ---- */

static void hex_of(Rgb c, char *out, size_t n) {
    snprintf(out, n, "#%02X%02X%02X", (int)(c.r * 255 + 0.5), (int)(c.g * 255 + 0.5), (int)(c.b * 255 + 0.5));
}

/* #1580 (Nadav 17/10, "like Clonezilla"): SMART says the disk is faulty (fail)
 * = red -- written only after the red button, no answer skips; failed in the
 * previous clone (failed_last) = orange, "נסה שוב" writes; old warn = orange. */
int smart_level(const char *smart) {
    if (!strcmp(smart, "fail")) return 2;
    if (!strcmp(smart, "failed_last") || !strcmp(smart, "warn")) return 1;
    return 0;
}

Rgb smart_color(const Theme *t, const char *smart) {
    int lvl = smart_level(smart);
    return lvl == 2 ? t->danger : lvl == 1 ? t->warn : t->led_ok;
}

const char *smart_he(const char *smart) {
    if (!smart || !smart[0] || !strcmp(smart, "unchecked")) return "לא נבדק";
    if (!strcmp(smart, "pending")) return "ממתין להכרעה";
    if (!strcmp(smart, "passed") || !strcmp(smart, "ok")) return "תקין";   /* #1861: dsmart carries smart.sh's "ok" */
    if (!strcmp(smart, "failed_last")) return "נכשל בשיכפול הקודם";
    if (!strcmp(smart, "fail")) return "SMART תקול";
    if (!strcmp(smart, "warn")) return "אזהרה";
    return smart;
}

/* #1742 (Nadav 18/10): a read-only disk is red with SMART's own red (fail,
 * reason "readonly" -- smart.sh smart_ro_check), and says why: not "SMART תקול". */
/* #1732/#1742: the sentence of the refusal to write on a red disk -- by its
 * reason: a read-only disk did not fail SMART (smart.sh SMART_RO_HE / SMART_RED_HE) */
const char *red_refusal_he(const char *reason) {
    return reason && !strcmp(reason, "readonly") ? "הדיסק במצב קריאה בלבד"
         : "SMART נכשל — לא כותבים על הדיסק הזה; אפשר להעתיק ממנו או לחלץ לשרת";
}

const char *smart_he_why(const char *smart, const char *reason) {
    if (smart && !strcmp(smart, "fail") && reason && !strcmp(reason, "readonly")) return smart_reason_text(reason);
    return smart_he(smart);
}

/* The prompt's reason is the agent's code word (smart.sh: smart_classify /
 * disk_failure_cause); the screen says it in words, and shows an unknown
 * code as-is rather than hiding it. Shared by the cloner's prompt and the
 * SMART warning window (smartdlg.c, 16/10). */
const char *smart_reason_text(const char *reason) {
    if (!reason || !reason[0] || !strcmp(reason, "ok")) return "";
    /* #1742: /sys/block/<d>/ro = 1, or the server's cause of a refused write (EPERM/EROFS) */
    if (!strcmp(reason, "readonly")) return "הדיסק במצב קריאה בלבד";
    /* #1921 (Nadav 20/10): red is the disk's own FAILED -- what stops an HP BIOS on "press F1";
     * 197/198 are orange -- an early sign, the disk still works */
    if (!strcmp(reason, "health_failed")) return "ה-BIOS יעצור ב-F1 — SMART: בדיקת הבריאות נכשלה";
    if (!strcmp(reason, "pending_sectors")) return "סקטורים ממתינים (197) — הדיסק מתקשה לקרוא חלק מהאזורים";
    if (!strcmp(reason, "uncorrectable")) return "סקטורים שלא ניתנים לתיקון (198) — הדיסק מצא אזורים שלא נקראו";
    if (!strcmp(reason, "media_readonly")) return "הבקר נעל את הדיסק לקריאה בלבד (NVMe)";
    /* #2078: smartctl's UNKNOWN! -- the disk refused the SMART read; red, the BIOS may stop on F1 */
    if (!strcmp(reason, "health_unread")) return "ה-SMART לא נקרא — ה-BIOS עלול לעצור ב-F1";
    if (!strcmp(reason, "no_table")) return "טבלת הידע של SMART חסרה — לא נבדק";
    if (!strcmp(reason, "no_health")) return "הדיסק אינו מדווח על בריאות";
    if (!strcmp(reason, "no_smartctl")) return "smartctl חסר ב-initramfs";
    if (!strcmp(reason, "timeout")) return "smartctl לא ענה בזמן — לא נבדק";   /* #1367 */
    /* #1861: the rest of smart.sh's unchecked words -- each said, none passed through */
    if (!strcmp(reason, "open_failed")) return "smartctl לא הצליח לפתוח את הדיסק — לא נבדק";
    if (!strcmp(reason, "no_smart")) return "הדיסק אינו תומך ב-SMART — לא נבדק";
    if (!strcmp(reason, "ro_unread")) return "לא נקרא אם הדיסק במצב קריאה בלבד — לא נבדק";
    if (!strcmp(reason, "cmd_failed")) return "פקודת SMART לדיסק נכשלה — לא נבדק";   /* #1903 */
    if (!strcmp(reason, "no_attrs")) return "הדיסק לא החזיר טבלת מאפייני SMART — לא נבדק";
    if (!strcmp(reason, "pending")) return "ממתין להכרעה";
    /* #1859 (Nadav 20/10): the orange -- smart.sh SMART_WARN_REASONS, the same words as smart_reason_he */
    if (!strcmp(reason, "critical_rose")) return "מאפיין קריטי עלה מאז הסבב הקודם — להחליף את הדיסק";
    if (!strcmp(reason, "downshift_rose")) return "ירידות מהירות SATA (183) עלו מאז הסבב הקודם";
    if (!strcmp(reason, "timeout_rose")) return "פקודות שנתקעו (188) עלו מאז הסבב הקודם";
    if (!strcmp(reason, "hot")) return "הדיסק התחמם בכתיבה בסבב הקודם";
    if (!strcmp(reason, "reported_uncorrectable")) return "שגיאות שלא תוקנו (187)";
    if (!strcmp(reason, "reallocated")) return "סקטורים שהועברו (5)";
    if (!strcmp(reason, "realloc_event")) return "אירועי העברה (196)";
    if (!strcmp(reason, "spin_retry")) return "ניסיונות סיבוב חוזרים (10)";
    if (!strcmp(reason, "ssd_worn")) return "הדיסק שחוק — 90% או יותר";
    if (!strcmp(reason, "spare_exhausted")) return "הרזרבה של הדיסק נגמרה";
    /* #1921: the knowledge table's orange -- smart.sh smart_reason_he, the same sentences */
    if (!strcmp(reason, "hot_now")) return "הדיסק חם מעבר לסף שלו עכשיו — לבדוק אוורור";
    if (!strcmp(reason, "bad_blocks")) return "הדיסק מצא בלוקים פגומים בזמן עבודה (183)";
    if (!strcmp(reason, "unc_logged")) return "ביומן הדיסק יש קריאה שלא תוקנה (UNC)";
    if (!strcmp(reason, "media_errors")) return "הדיסק דיווח על שגיאות נתונים שלא תוקנו (NVMe)";
    if (!strcmp(reason, "program_fail")) return "הזיכרון של ה-SSD נכשל בכתיבה או במחיקה";
    if (!strcmp(reason, "data_path")) return "שגיאות בנתיב הנתונים בתוך הדיסק";
    if (!strcmp(reason, "selftest_failed")) return "הבדיקה העצמית האחרונה של הדיסק נכשלה";
    if (!strcmp(reason, "nvme_reliability")) return "הבקר מדווח שהאמינות של הדיסק נפגעה (NVMe)";
    /* #1503: failed_last carries the server's cause instead (ata_cause.py CAUSES);
     * the words are the console's FAILURE_CAUSE_HE, which this was showing in English */
    if (!strcmp(reason, "cable")) return "סיבה: כבל/חריץ";
    if (!strcmp(reason, "disk")) return "סיבה: הדיסק";
    if (!strcmp(reason, "removed")) return "סיבה: המגירה נשלפה";   /* ata_cause.py, owner 17/10 */
    if (!strcmp(reason, "write_error")) return "סיבה: שגיאת כתיבה לדיסק";   /* #1841: a refused fsync mid-partition */
    if (!strcmp(reason, "unclassified")) return "סיבה: לא סווג";
    return reason;
}

const IdleDisk *idle_for_port(const State *s, int port) {
    for (int i = 0; i < s->nidisks; i++)
        if (s->idisks[i].port == port) return &s->idisks[i];
    return NULL;
}

/* #1495: the drawer the operator sees; zero means it is outside the mapping. */
int eff_drawer(int drawer, int port) {
    return drawer > 0 ? drawer : drawer < 0 ? 0 :
           (port >= 1 && port <= MAX_DRAWERS ? port : 0);
}

void conn_text(const char *conn, int port, const char *dev, char *out, size_t n) {
    if (conn && conn[0]) snprintf(out, n, "%s", conn);
    else if (port >= 1) snprintf(out, n, "SATA %d", port - 1);
    else if (dev && dev[0]) snprintf(out, n, "%s", dev);
    else snprintf(out, n, "%s", "חיבור לא מזוהה");
}

void unmapped_label(const char *name, const char *conn, int port, const char *dev,
                    char *out, size_t n) {
    char what[SLOT_NAME_MAX + 64];
    if (name && name[0]) snprintf(what, sizeof what, "%s", name);
    else conn_text(conn, port, dev, what, sizeof what);
    snprintf(out, n, "%s (לא ממופה)", what);
}

void card_name(const char *name, int bay, const char *conn, int port, char *out, size_t n) {
    if (bay > 0 && name && name[0]) snprintf(out, n, "%s", name);
    else if (bay > 0) conn_text(conn, port, "", out, n);
    else unmapped_label(name, conn, port, "", out, n);
}

const CUsed *cused_for_dev(const State *s, const char *dev) {
    for (int i = 0; dev && dev[0] && i < s->ncuseds; i++)
        if (!strcmp(s->cuseds[i].dev, dev)) return &s->cuseds[i];
    return NULL;
}

int cloner_bay_count(const State *s) {
    if (s->ncbays > 0) return s->ncbays;
    int bays = s->drawer_count > 0 ? s->drawer_count : 3;
    for (int p = bays + 1; p <= MAX_DRAWERS; p++)
        if (s->cslot_names[p - 1][0] || idle_for_port(s, p)) bays = p;
    return bays;
}

const IdleDisk *idle_for_bay(const State *s, int bay) {
    for (int i = 0; i < s->nidisks; i++)
        if (eff_drawer(s->idisks[i].drawer, s->idisks[i].port) == bay) return &s->idisks[i];
    return NULL;
}

const Drawer *drawer_for_bay(const State *s, int bay) {
    for (int i = 0; i < s->ndrawers; i++)
        if (eff_drawer(s->drawers[i].drawer, s->drawers[i].port) == bay) return &s->drawers[i];
    return NULL;
}

const COptical *optical_for_bay(const State *s, int bay) {
    for (int i = 0; i < s->ncopticals; i++)
        if (eff_drawer(s->copticals[i].drawer, 0) == bay) return &s->copticals[i];
    return NULL;
}

void cloner_bay_conn(const State *s, int bay, char *out, size_t n) {
    if (s->ncbays > 0 && bay >= 1 && bay <= s->ncbays && s->cbays[bay - 1].conn[0])
        snprintf(out, n, "%s", s->cbays[bay - 1].conn);
    else if (s->ncbays == 0)
        snprintf(out, n, "SATA %d", bay - 1);
    else
        snprintf(out, n, "%s", "חיבור לא מזוהה");
}

/* The argument is a visible bay, not a wire port. */
void cloner_slot_label(const State *s, int bay, char *out, size_t n) {
    if (s->ncbays > 0 && bay >= 1 && bay <= s->ncbays && s->cbays[bay - 1].name[0])
        snprintf(out, n, "%s", s->cbays[bay - 1].name);
    else if (s->ncbays == 0 && bay >= 1 && bay <= MAX_DRAWERS && s->cslot_names[bay - 1][0])
        snprintf(out, n, "%s", s->cslot_names[bay - 1]);
    else
        snprintf(out, n, "דיסק %d", bay);
}

void cloner_port_label(const State *s, int port, char *out, size_t n) {
    const Drawer *d = NULL;
    const IdleDisk *id = idle_for_port(s, port);
    for (int i = 0; i < s->ndrawers; i++) if (s->drawers[i].port == port) { d = &s->drawers[i]; break; }
    int drawer = d ? eff_drawer(d->drawer, port) : id ? eff_drawer(id->drawer, port) : 0;
    if (drawer > 0) { cloner_slot_label(s, drawer, out, n); return; }
    if (d || id) {
        const char *name = port >= 1 && port <= MAX_DRAWERS ? s->cslot_names[port - 1] : "";
        unmapped_label(name, d ? d->conn : id->conn, port, d ? d->dev : id->dev, out, n);
        return;
    }
    cloner_slot_label(s, port, out, n);
}

int room_selectable(const RoomDrawer *d) {
    return d->present && d->port >= 1 && eff_drawer(d->drawer, d->port) > 0;
}

/* #1732 (Nadav 18/10): a red drawer (SMART fail) is drawn in its colour but is
 * never a target -- never picked, never sent; the server refuses it too (room.py). */
int room_pickable(const RoomDrawer *d) {
    return room_selectable(d) && smart_level(d->smart) < 2;
}

void room_drawer_label(const Machine *m, const RoomDrawer *d, char *out, size_t n) {
    int drawer = eff_drawer(d->drawer, d->port);
    if (drawer > 0) {
        if (d->slot_name[0]) snprintf(out, n, "%s", d->slot_name);
        else if (drawer <= MAX_DRAWERS && m->rslots[drawer - 1].name[0])
            snprintf(out, n, "%s", m->rslots[drawer - 1].name);
        else if (d->port >= 1 && d->port <= MAX_DRAWERS && m->slot_names[d->port - 1][0])
            snprintf(out, n, "%s", m->slot_names[d->port - 1]);
        else snprintf(out, n, "דיסק %d", drawer);
    } else {
        unmapped_label(d->slot_name, d->conn, d->port, "", out, n);
    }
}

int room_rows(const Machine *m) {
    int rows = m->nrslots > 0 ? m->nrslots : m->drawer_count;
    if (rows < 1) rows = 1;
    if (rows > MAX_DRAWERS) rows = MAX_DRAWERS;
    for (int i = 0; i < m->nroom_drawers; i++)
        if (m->room_drawers[i].present && eff_drawer(m->room_drawers[i].drawer,
                                                     m->room_drawers[i].port) == 0) rows++;
    return rows;
}

/* The status span, as Pango markup (room-ok / room-bad / room-warn / sub). */
void room_status_markup(const App *a, const Machine *m, int mode, char *out, size_t n) {
    char ok[8], bad[8], warn[8], e[640], lbl[96];   /* e: 120 bytes escaped, worst case x5 */
    hex_of(a->theme->led_ok, ok, sizeof ok);
    hex_of(a->theme->danger, bad, sizeof bad);
    hex_of(a->theme->warn, warn, sizeof warn);
    text_escape(m->error, e, sizeof e);
    progress_label(lbl, sizeof lbl, m->pct, m->moving, 0);
    if (mode == ROWS_CLASS) {
        if (!strcmp(m->state, "failed"))       snprintf(out, n, "<span foreground=\"%s\">נכשל · %s</span>", bad, e);
        /* #943: done that still carries an error (name not written, grow
         * deferred) is "finished, with a warning" -- orange, not green, and
         * not red: the restore did not fail. Same rule as the console (#856). */
        else if (!strcmp(m->state, "done") && m->error[0]) snprintf(out, n, "<span foreground=\"%s\">הסתיים · עם אזהרה: %s</span>", warn, e);
        else if (!strcmp(m->state, "done"))    snprintf(out, n, "<span foreground=\"%s\">הסתיים</span>", ok);
        else if (!strcmp(m->state, "waiting")) snprintf(out, n, "ממתין לשידור");
        else                                   snprintf(out, n, "%s", lbl);
        return;
    }
    if (mode == ROWS_ROOM_LIVE && m->joined) {
        /* three endings, not two (#67) */
        if (!strcmp(m->state, "failed"))        snprintf(out, n, "<span foreground=\"%s\">נכשל · %s</span>", bad, e);
        else if (!strcmp(m->state, "partial"))  snprintf(out, n, "<span foreground=\"%s\">הושלם חלקית · %s</span>", warn, m->error[0] ? e : "מגירה אחת לא נכתבה");
        else if (!strcmp(m->state, "done") && m->error[0]) snprintf(out, n, "<span foreground=\"%s\">הסתיים · עם אזהרה: %s</span>", warn, e);   /* #943 */
        else if (!strcmp(m->state, "done"))     snprintf(out, n, "<span foreground=\"%s\">הסתיים</span>", ok);
        else if (!m->state[0] || !strcmp(m->state, "waiting")) snprintf(out, n, "מחכה לשידור");
        else if (m->error[0])                   snprintf(out, n, "%s · <span foreground=\"%s\">%s</span>", lbl, bad, e);
        else                                    snprintf(out, n, "%s", lbl);
        return;
    }
    if (m->awake) snprintf(out, n, "<span foreground=\"%s\">ער · %d מגירות מוכנות</span>", ok, m->fresh_drawers);
    else          snprintf(out, n, "כבוי");
}

/* Measure the rows for a box <w> wide. .room-row: padding 10px 14px, gap 10,
 * 13.5px, border-bottom 1 (not on the last); led 8; <b> min-width 90;
 * .dev 12px mono. */
void rows_make(App *a, cairo_t *cr, double w, int mode, const char *empty, RowSet *rs) {
    const State *s = &a->st;
    memset(rs, 0, sizeof *rs);
    rs->n = s->nmachines;
    if (rs->n == 0) {
        rs->empty = sub_make(cr, empty, w - 2);
        pango_layout_set_alignment(rs->empty.layout, PANGO_ALIGN_RIGHT);   /* ui-audit */
        rs->h = 2 + rs->empty.h;
        return;
    }
    double inner = w - 2*N_BORDER - 2*N_FORM_GAP;
    for (int i = 0; i < rs->n; i++) {
        const Machine *m = &s->machines[i];
        Row *r = &rs->r[i];
        char mk[1024];
        r->name = text_make(cr, FONT_SANS, N_SUB, 700, m->name[0] ? m->name : m->mac, 0, DIR_RTL);
        r->mac  = text_make(cr, FONT_MONO, N_SUB, 400, m->mac, 0, DIR_LTR);
        double used = N_ACTION_GAP + N_ACTION_GAP + dmax(N_DONE_ICON, r->name.w) + N_ACTION_GAP + r->mac.w + N_ACTION_GAP;
        room_status_markup(a, m, mode, mk, sizeof mk);
        r->state = text_make_markup(cr, FONT_SANS, N_SUB, 400, mk, (int)dmax(N_MESSAGE_ICON, inner - used), DIR_RTL);
        double line = dmax(dmax(r->name.h, r->mac.h), dmax(r->state.h, N_ACTION_GAP));
        r->h = N_ACTION_GAP + line + N_ACTION_GAP + (i < rs->n - 1 ? 1 : 0);
        r->on  = mode == ROWS_CLASS || m->awake || m->joined;
        r->dim = mode != ROWS_CLASS && !r->on;
        rs->h += r->h;
    }
    rs->h += 2;                                           /* the box's border */
}

void rows_draw(App *a, cairo_t *cr, double x, double y, double w, RowSet *rs) {
    const Theme *t = a->theme;
    Rect box = { x, y, w, rs->h };
    draw_fill_rrect(cr, box, RADIUS_SM, t->choice);
    draw_border_rrect(cr, box, RADIUS_SM, t->choice_line, N_BORDER);
    cairo_save(cr);
    draw_rrect_path(cr, box, RADIUS_SM); cairo_clip(cr);
    if (rs->n == 0) {
        text_draw(cr, &rs->empty, x + 1, y + 1, t->ink);
        text_free(&rs->empty);
        cairo_restore(cr);
        return;
    }
    double ry = y + 1;
    for (int i = 0; i < rs->n; i++) {
        Row *r = &rs->r[i];
        if (r->dim) cairo_push_group(cr);                 /* .room-row.dim{opacity:.55} */
        double cy = ry + (r->h - (i < rs->n - 1 ? 1 : 0)) / 2;
        double lx = x + w - N_BORDER - N_FORM_GAP - N_ACTION_GAP/2;
        if (r->on) draw_led(cr, lx, cy, N_ACTION_GAP, t->led_ok);
        else { cairo_arc(cr, lx, cy, N_ACTION_GAP/2, 0, 2 * M_PI); draw_set(cr, t->hair); cairo_fill(cr); }
        double nx = lx - N_ACTION_GAP/2 - N_ACTION_GAP;
        text_draw_r(cr, &r->name, nx, cy - r->name.h / 2, t->ink);
        double mx = nx - dmax(N_DONE_ICON, r->name.w) - N_ACTION_GAP;        /* right edge of .dev */
        text_draw_r(cr, &r->mac, mx, cy - r->mac.h / 2, t->ink);
        text_draw(cr, &r->state, x + N_BORDER + N_FORM_GAP, cy - r->state.h / 2, t->ink);
        if (r->dim) { cairo_pop_group_to_source(cr); cairo_paint_with_alpha(cr, N_DIM_ALPHA); }
        if (i < rs->n - 1) {
            cairo_rectangle(cr, x, ry + r->h - 1, w, 1);
            draw_set(cr, t->hair); cairo_fill(cr);
        }
        ry += r->h;
        text_free(&r->name); text_free(&r->mac); text_free(&r->state);
    }
    cairo_restore(cr);
}

/* ---- #410: elapsed / rate / ETA / freshness, in words ----------------------- */

static void hms(int secs, char *out, size_t n) {
    if (secs >= 3600) snprintf(out, n, "%d:%02d:%02d", secs / 3600, secs % 3600 / 60, secs % 60);
    else              snprintf(out, n, "%d:%02d", secs / 60, secs % 60);
}

void pace_text(int elapsed_s, long long rate_bps, int eta_s, int latin, char *out, size_t n) {
    /* Each number is printed only when it was measured: a rate of 0 reads
     * as "stopped", and a wave with no measurement yet is not stopped. */
    char el[24], rt[32], eta[32]; size_t len = 0;
    out[0] = 0;
    if (elapsed_s >= 0) {
        hms(elapsed_s, el, sizeof el);
        len += snprintf(out + len, n - len, latin ? "elapsed %s" : "עבר %s", el);
    }
    if (rate_bps > 0) {
        fmt_bytes(rt, sizeof rt, (double)rate_bps);
        len += snprintf(out + len, n - len, latin ? "%s%s/s" : "%s%s/שנ'", len ? " · " : "", rt);
    }
    if (eta_s >= 0 && rate_bps > 0) {
        hms(eta_s, eta, sizeof eta);
        len += snprintf(out + len, n - len, latin ? "%sETA %s" : "%sנותרו ~%s", len ? " · " : "", eta);
    } else if (len) {
        len += snprintf(out + len, n - len, latin ? " · ETA not measured" : " · סיום לא נמדד");
    }
}

void pace_age(long long updated, char *out, size_t n) {
    out[0] = 0;
    if (updated <= 0) return;
    long long age = (long long)time(NULL) - updated;
    if (age < 0) age = 0;
    /* whole words: "שנ'" read as cut off, and its geresh jumped ends in a
     * mixed line (gate-1010f render) */
    if (age == 1)          snprintf(out, n, "עודכן לפני שנייה");
    else if (age < 60)     snprintf(out, n, "עודכן לפני %lld שניות", age);
    else if (age < 120)    snprintf(out, n, "עודכן לפני דקה");
    else if (age < 3600)   snprintf(out, n, "עודכן לפני %lld דקות", age / 60);
    else if (age < 7200)   snprintf(out, n, "עודכן לפני שעה");
    else                   snprintf(out, n, "עודכן לפני %lld שעות", age / 3600);
}

/* An LTR-or-mixed value (an image name, "Office 2024 — סטנדרט") inside an
 * RTL line, as one isolated run: FSI ... PDI (U+2068 / U+2069). Without it
 * the name's own em dash and digits are reordered with the line around it. */
void bidi_isolate(const char *in, char *out, size_t n) {
    snprintf(out, n, "\xE2\x81\xA8%s\xE2\x81\xA9", in);
}

/* ---- the .st-prog-line (b 22px + span.sub, align-items:baseline) --------- */

ProgLine progline_make(cairo_t *cr, const char *big, const char *small, double w) {
    ProgLine p;
    p.big = text_make(cr, FONT_SANS, N_PROGRESS_PCT, 700, big, 0, DIR_RTL);
    p.small = sub_make(cr, small, w / 2);
    p.drop = text_baseline(&p.big) - text_baseline(&p.small);
    p.h = dmax(p.big.h, p.drop + p.small.h);
    return p;
}

void progline_draw(cairo_t *cr, const Theme *t, ProgLine *p, double x, double y, double w) {
    text_draw_r(cr, &p->big, x + w, y, t->ink);
    text_draw(cr, &p->small, x, y + p->drop, t->ink);
    text_free(&p->big); text_free(&p->small);
}

/* ---- the folder-first image pickers (owner, 10/10) ---- */

int image_folders(const State *s, int *first, int *count, int max) {
    int n = 0;
    for (int i = 0; i < s->nimages; i++) {
        int k = 0;
        while (k < n && strcmp(s->images[first[k]].folder, s->images[i].folder)) k++;
        if (k == n) {
            if (n >= max) continue;              /* more folders than the picker holds */
            first[n] = i; count[n] = 0; n++;
        }
        count[k]++;
    }
    return n;
}

int folder_images(const State *s, const char *folder, int *idx, int max) {
    int n = 0;
    for (int i = 0; i < s->nimages && n < max; i++)
        if (!strcmp(s->images[i].folder, folder)) idx[n++] = i;
    return n;
}

const char *folder_label(const char *folder) { return folder[0] ? folder : "ללא תיקייה"; }

/* Lay the footer buttons from the right edge; ids[] / kinds[] in DOM order. */
void foot_buttons(App *a, cairo_t *cr, Rect foot, Text *labels, const int *kinds, const int *ids, int n) {
    double btn_h = btn_height(cr), bx = foot.x + foot.w - N_PAD, by = foot.y + N_ACTION_TOP;
    for (int i = 0; i < n; i++) {
        double bw = btn_width(&labels[i]);
        bx -= bw;
        draw_btn(a, cr, (Rect){ bx, by, bw, btn_h }, &labels[i], kinds[i], ids[i]);
        bx -= 8;
        text_free(&labels[i]);
    }
}

/* ---- #st-room ---------------------------------------------------------------- */

int image_options(const App *a, const char **out, char (*buf)[168], int max) {
    int n = 0;
    out[n++] = "בחרו אימג'…";
    for (int i = 0; i < a->st.nimages && n < max; i++) {
        const Image *im = &a->st.images[i];
        if (im->folder[0]) snprintf(buf[i], sizeof buf[i], "%s / %s", im->folder, im->name);
        else               snprintf(buf[i], sizeof buf[i], "%s", im->name);
        out[n++] = buf[i];
    }
    return n;
}
