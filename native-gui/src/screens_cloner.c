#include <math.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include "canceldlg.h"
#include "lcui.h"
#include "screens_rounds.h"

/* #1090 / mockup 10/10 ("מסך מחשב השיכפול"): one card per disk this cloner
 * FOUND -- a drawer of the round or a disk of the idle inventory -- not a
 * fixed three: cloner 1 has two SATA ports. Order is the physical SATA port,
 * never kernel device order. A card is as tall as what it has to say. */

typedef struct {
    int port, synthetic;
    const Drawer *drawer;
    const IdleDisk *idle;
} ClonerEntry;

static int entry_drawer(const ClonerEntry *e) {
    return e->drawer ? eff_drawer(e->drawer->drawer, e->port) :
           e->idle ? eff_drawer(e->idle->drawer, e->port) : 0;
}

static int entry_after(const ClonerEntry *a, const ClonerEntry *b) {
    int ad = entry_drawer(a), bd = entry_drawer(b);
    if (ad && bd) return ad > bd;
    if (ad != 0) return 0;
    if (bd != 0) return 1;
    return a->port > b->port;
}

static int cloner_entries(const State *s, ClonerEntry *out) {
    int n = 0;
    if (s->ndrawers > 0) {
        for (int i = 0; i < s->ndrawers; i++)
            out[n++] = (ClonerEntry){ s->drawers[i].port, 0, &s->drawers[i],
                                      idle_for_port(s, s->drawers[i].port) };
    } else {
        for (int i = 0; i < s->nidisks; i++)
            out[n++] = (ClonerEntry){ s->idisks[i].port, 0, NULL, &s->idisks[i] };
    }
    if (s->smart_pending) {
        int found = 0;
        for (int i = 0; i < n; i++) if (out[i].port == s->smart_port) found = 1;
        if (!found && n < MAX_DRAWERS + 1)
            out[n++] = (ClonerEntry){ s->smart_port, 1, NULL, idle_for_port(s, s->smart_port) };   /* 17/10: its model and size too */
    }
    for (int i = 1; i < n; i++) {
        ClonerEntry v = out[i]; int j = i;
        while (j > 0 && entry_after(&out[j - 1], &v)) { out[j] = out[j - 1]; j--; }
        out[j] = v;
    }
    return n;
}

static void entry_label(const State *s, const ClonerEntry *e, char *out, size_t n) {
    int bay = entry_drawer(e);
    if (bay > 0) cloner_slot_label(s, bay, out, n);
    else if (e->synthetic) cloner_slot_label(s, e->port, out, n);
    else {
        const char *name = e->port >= 1 && e->port <= MAX_DRAWERS ? s->cslot_names[e->port - 1] : "";
        const char *conn = e->drawer ? e->drawer->conn : e->idle ? e->idle->conn : "";
        const char *dev = e->drawer ? e->drawer->dev : e->idle ? e->idle->dev : "";
        unmapped_label(name, conn, e->port, dev, out, n);
    }
}

/* #2030: the card's name -- the connection (or the operator's slot name), never "דיסק N" again. */
static void entry_card_name(const State *s, const ClonerEntry *e, char *out, size_t n) {
    int bay = entry_drawer(e);
    const char *conn = e->drawer && e->drawer->conn[0] ? e->drawer->conn : e->idle ? e->idle->conn : "";
    const char *name = "";
    if (bay > 0 && s->ncbays > 0 && bay <= s->ncbays) { name = s->cbays[bay - 1].name; if (!conn[0]) conn = s->cbays[bay - 1].conn; }
    else if (bay > 0 && s->ncbays == 0 && bay <= MAX_DRAWERS) name = s->cslot_names[bay - 1];
    else if (bay <= 0 && e->port >= 1 && e->port <= MAX_DRAWERS) name = s->cslot_names[e->port - 1];
    card_name(name, bay, conn, e->port, out, n);
}

/* Nadav 17/10 ("גרסה ב׳"): the screen is the local clone's progress screen
 * (screens_lcrun.c) -- a hero with the big percent, three metric boxes, and
 * a cage of rows, one per disk, drawn by lcbay.c. A drawer's row says its
 * state in words: writing, waiting for a SMART decision, skipped, failed,
 * done -- the first metal day showed "ממתינה לשידור" while the machine was
 * waiting for an answer, and a failed round said nothing but "אין פעולה". */
static int entry_kind(const App *a, const ClonerEntry *e) {
    const Drawer *d = e->drawer;
    if (a->st.smart_pending && a->st.smart_port == e->port) return BAY_SMART;
    if (!d) return BAY_WAITING;
    if (!strcmp(d->state, "asking")) return BAY_SMART;   /* #1808/#1898: orange, waiting for the operator */
    if (!strcmp(d->state, "cancelled")) return BAY_CANCELED;                    /* #1386 */
    if (!strcmp(d->state, "writing") && d->cancel[0]) return BAY_CANCELING;
    if (!strcmp(d->state, "writing") || !strcmp(d->state, "verifying")) return BAY_WRITING;
    if (!strcmp(d->state, "done")) return BAY_DONE;
    if (!strcmp(d->state, "failed")) return BAY_FAILED;
    if (!strcmp(d->state, "skipped")) return BAY_SKIPPED;
    return BAY_WAITING;
}

static int entry_pct(const ClonerEntry *e) {
    const Drawer *d = e->drawer;
    if (d && !strcmp(d->state, "done")) return 100;
    if (!d || !d->total) return -1;
    int pct = (int)floor(100.0 * d->bytes / d->total);
    return pct > 100 ? 100 : pct;
}

static void entry_bay(const App *a, const ClonerEntry *e, LcBay *y) {
    const Theme *t = a->theme; const Drawer *d = e->drawer; const IdleDisk *idle = e->idle;
    memset(y, 0, sizeof *y);
    y->kind = entry_kind(a, e);
    y->drawer = entry_drawer(e);
    entry_card_name(&a->st, e, y->name, sizeof y->name);   /* #2030: the connection, not "דיסק N" twice */
    /* #2030: the size -- from the cused record (any disk, round or idle), else the idle inventory's;
     * the model when the idle inventory has it. Never the device name. */
    const CUsed *cu = cused_for_dev(&a->st, d ? d->dev : idle ? idle->dev : "");
    unsigned long long size = cu ? cu->size : idle ? idle->size : 0;
    if (size) {
        char sz[32], mi[96]; disk_size(size, sz, sizeof sz);
        if (idle && idle->model[0]) { bidi_isolate(idle->model, mi, sizeof mi); snprintf(y->sub, sizeof y->sub, "%s · %s", sz, mi); }
        else snprintf(y->sub, sizeof y->sub, "%s", sz);
    } else snprintf(y->sub, sizeof y->sub, "%s", d || idle ? "גודל הדיסק לא נקרא" : "לא זוהה דיסק בפורט הזה");
    if (d || idle) {   /* #2030: before the write -- what it will erase; after -- what it wrote */
        int k = y->kind;
        if (k == BAY_DONE) card_used_text(USED_WRITTEN, d ? (long long)d->bytes : -1, y->used, sizeof y->used);
        else if (k == BAY_WRITING || k == BAY_FAILED) card_used_text(USED_SO_FAR, d ? (long long)d->bytes : -1, y->used, sizeof y->used);
        else if (k == BAY_CANCELED || k == BAY_CANCELING) card_used_text(USED_NOW, -1, y->used, sizeof y->used);   /* its table is gone: nothing measured since */
        else card_used_text(USED_NOW, cu ? cu->used : -1, y->used, sizeof y->used);
    }
    /* the SMART the disk reported (#652), in words and in the one colour mapping:
     * green ok/unchecked, orange F1, red failed_last */
    if (idle) { if (!strcmp(idle->cause, "readonly")) snprintf(y->note_buf, sizeof y->note_buf, "%s", smart_he_why(idle->smart, idle->cause));   /* #1742 */
                else snprintf(y->note_buf, sizeof y->note_buf, "SMART %s", smart_he(idle->smart));
                y->toned = 1; y->tone = smart_color(t, idle->smart); y->note = y->note_buf; }
    int pct = entry_pct(e);
    if (pct >= 0 && y->kind != BAY_SKIPPED && y->kind < BAY_CANCELED) { snprintf(y->pct, sizeof y->pct, "%d%%", pct); y->share = pct / 100.0; }
    else { snprintf(y->pct, sizeof y->pct, "%s", "—"); y->share = -1; }
    switch (y->kind) {
    case BAY_SMART:
        if (d && !strcmp(d->state, "asking")) {   /* #1808/#1898: what was found, in the agent's words */
            y->pill = "ממתין להכרעה";
            snprintf(y->line, sizeof y->line, "%s", d->error[0] ? d->error : "ממתין להחלטת המפעיל");
            break;
        }
        y->pill = "ממתין להכרעת SMART";
        snprintf(y->line, sizeof y->line, "%s · מה לעשות עם הדיסק?",
                 a->st.smart_reason[0] ? smart_reason_text(a->st.smart_reason) : smart_he(a->st.smart_verdict));
        break;
    case BAY_WRITING:
        if (!strcmp(d->state, "verifying")) snprintf(y->line, sizeof y->line, "בודק את מה שנכתב…");
        else if (d->rate_bps > 0) { char r[48], ri[64]; snprintf(r, sizeof r, "%.0f MB/s", d->rate_bps / 1e6);
            bidi_isolate(r, ri, sizeof ri); snprintf(y->line, sizeof y->line, "כותב · %s", ri); }
        else if (d->rate_bps == 0) { snprintf(y->line, sizeof y->line, "ממתין לזרם מהשרת · 0 MB/s"); y->line_warn = 1; }   /* #2055: measured still */
        else snprintf(y->line, sizeof y->line, "כותב · הקצב טרם נמדד");
        break;
    case BAY_CANCELED: {
        /* #1386: grey like skipped, the bar empty -- the disk is empty now, not "16%" */
        char at[16] = ""; time_t w = (time_t)d->cancel_at; struct tm tm;
        if (w > 0 && localtime_r(&w, &tm)) strftime(at, sizeof at, " · %H:%M", &tm);
        y->pill = "בוטל"; y->toned = 0;
        snprintf(y->line, sizeof y->line, "בוטל %s%s · %s", d->cancel[0] ? d->cancel : "", at,
                 d->error[0] ? d->error : "טבלת המחיצות נמחקה, הדיסק יוצג ריק");
        y->line_warn = d->error[0] != 0;
        break; }
    case BAY_CANCELING:
        y->pill = "מבטל…"; y->toned = 0; y->share = -1;
        snprintf(y->line, sizeof y->line, "עוצר את הכתיבה ומוחק את טבלת המחיצות…");
        break;
    case BAY_DONE:
        /* #943: done with an error is a warning in the line, not a failed disk */
        y->pill = "הושלם";
        if (d->error[0]) { snprintf(y->line, sizeof y->line, "הושלם — %s", d->error); y->line_warn = 1; }
        /* #1907 (Nadav 22/10): written, not read back -- the server's check list did not come down */
        else if (d->rb_nolist) { snprintf(y->line, sizeof y->line, "הושלם · לא אומת — הרשימה לא הורדה"); y->line_warn = 1; }
        else snprintf(y->line, sizeof y->line, "הושלם · נבדק");
        break;
    case BAY_FAILED:
        y->pill = "נכשל";
        snprintf(y->line, sizeof y->line, "נכשל: %s", d->error[0] ? d->error : "סיבה לא ידועה");
        break;
    case BAY_SKIPPED:
        y->pill = "דולג";
        snprintf(y->line, sizeof y->line, "דולג — %s", d->error[0] ? d->error : "הדיסק לא נכתב");
        break;
    default:
        snprintf(y->line, sizeof y->line, "%s", d && !strcmp(d->state, "replacing") ? "ממתין להחלפה" : "ממתין לשידור");
    }
    /* #1331 (mockup v2): the drawer's drivers, under its bar */
    if (d && d->drv_state[0]) lc_bay_drivers(y, d->drv_state, d->drv_detail, d->drv_boot, d->drv_bsvc, d->drv_berr);
    if (d && d->drv_lstate[0]) lc_bay_linux(y, d->drv_lstate, d->drv_lmod, d->drv_ldet);   /* #1331 T8 */
    /* #2024: this drawer holds the end -- highlighted, and its line is the agent's reason */
    int red = 0; const char *why = d ? end_problem_of(&a->st, d->dev, &red) : NULL;
    if (why) { y->flag = red ? 2 : 1; snprintf(y->line, sizeof y->line, "%s", why); }
}

/* #1386: the drawers "בטל דיסק זה" can name -- writing now, not verifying, not
 * already asked -- in the screen's row order. A row's button is
 * HIT_BAY_CANCEL_BASE + its index here; its key is the drawer's number. */
int cloner_cancel_picks(const App *a, CancelPick *out, int max) {
    ClonerEntry entries[MAX_DRAWERS + 1]; int n = cloner_entries(&a->st, entries), k = 0;
    for (int i = 0; i < n && k < max; i++) {
        const ClonerEntry *e = &entries[i];
        if (entry_kind(a, e) != BAY_WRITING || strcmp(e->drawer->state, "writing")) continue;
        CancelPick *p = &out[k++]; memset(p, 0, sizeof *p);
        snprintf(p->dev, sizeof p->dev, "%s", e->drawer->dev);
        entry_label(&a->st, e, p->label, sizeof p->label);
        p->drawer = entry_drawer(e);
        if (p->drawer > 0) snprintf(p->where, sizeof p->where, "מגירה %d%s%s", p->drawer,
                                    a->st.machine_name[0] ? " · " : "", a->st.machine_name);
        else snprintf(p->where, sizeof p->where, "%s", a->st.machine_name[0] ? a->st.machine_name : "מחשב השיכפול הזה");
        if (e->idle) { snprintf(p->model, sizeof p->model, "%s", e->idle->model); disk_size(e->idle->size, p->size, sizeof p->size); }
        p->pct = entry_pct(e);
    }
    return k;
}

/* A footer button with its key in a chip inside the label (mockup 10/10:
 * "החלף דיסק R"). Returns the width; draws only when draw is set. */
static double key_button(App *a, cairo_t *cr, double x, double y, const char *label,
                         const char *key, int kind, int id, int draw) {
    char chip_bg[8], chip_fg[8], e[160], mk[400];
    Rgb bg = a->theme->porcelain, fg = a->theme->ink;
    snprintf(chip_bg, sizeof chip_bg, "#%02X%02X%02X", (int)(bg.r * 255 + .5), (int)(bg.g * 255 + .5), (int)(bg.b * 255 + .5));
    snprintf(chip_fg, sizeof chip_fg, "#%02X%02X%02X", (int)(fg.r * 255 + .5), (int)(fg.g * 255 + .5), (int)(fg.b * 255 + .5));
    text_escape(label, e, sizeof e);
    snprintf(mk, sizeof mk, "%s  <span font_family=\"" FONT_MONO_FAMILY "\" size=\"smaller\" background=\"%s\" foreground=\"%s\"> %s </span>",
             e, chip_bg, chip_fg, key);
    Text l = text_make_markup(cr, FONT_SANS, N_SUB, 500, mk, 0, DIR_RTL);
    double bw = btn_width(&l);
    if (draw) draw_btn(a, cr, (Rect){ x, y, bw, N_BUTTON_H }, &l, kind, id);
    text_free(&l);
    return bw;
}

/* #2054: a present-tense inventory, not a count of successes before the end. */
static void disk_count(char *out, size_t cap, int count, const char *one, const char *many) {
    if (!count) return;
    size_t used = strlen(out);
    snprintf(out + used, cap - used, "%s%d %s", used > strlen(RLM) ? " · " : "", count, count == 1 ? one : many);
}

void screen_cloner(App *a, cairo_t *cr, double W, double H, double head_h) {
    const Theme *t = a->theme; const State *s = &a->st;
    ClonerEntry entries[MAX_DRAWERS + 1]; int n = cloner_entries(s, entries);
    double cw = fmin(980, W - 48), x0 = (W - cw) / 2, xr = x0 + cw, y = head_h + 22;
    int nw = 0, nd = 0, nf = 0, ns = 0, nc = 0, ncg = 0, pend = 0, sum = 0, known = 0, eta = -1;   /* #1386: cancelled, cancelling */
    int verifying = 0, waiting = 0, asking = 0, unchecked_done = 0;   /* display counts only; entry_kind is unchanged */
    long long rate = -1, moved = 0; unsigned long long most = 0, whole = 0; int stalled = 0;   /* #2055 */
    char names[200] = "", failed_name[SLOT_NAME_MAX + 64] = "", skipped_name[SLOT_NAME_MAX + 64] = "";
    /* #1331: the drivers -- placed, not placed, no model; who may not boot, who boots without them */
    int dv_line = 0, dv_any = 0, dv_ok = 0, dv_no = 0, dv_nomodel = 0, dv_bad = 0, dv_warn = 0;
    char bad_name[SLOT_NAME_MAX + 64] = "", warn_name[SLOT_NAME_MAX + 64] = "";
    for (int i = 0; i < n; i++) {
        const ClonerEntry *e = &entries[i]; int k = entry_kind(a, e), pct = entry_pct(e);
        char nm[SLOT_NAME_MAX + 64]; entry_label(s, e, nm, sizeof nm);
        LcBay dg; entry_bay(a, e, &dg);
        if (dg.drv_tone) dv_line = 1;   /* #1331 T8: the Linux line needs the tall row, not the Windows summary */
        if (dg.drv_tone && dg.drv_tone <= DRV_BAD) {
            dv_any = 1; dv_ok += !strcmp(e->drawer->drv_state, "staged"); dv_no += dg.drv_tone == DRV_WARN;
            dv_nomodel += !strcmp(e->drawer->drv_detail, "no_model") && !strcmp(e->drawer->drv_state, "skipped");
            if (dg.drv_tone == DRV_BAD) { dv_bad++; snprintf(bad_name, sizeof bad_name, "%s", nm); }
            if (dg.drv_tone == DRV_WARN) { dv_warn++; snprintf(warn_name, sizeof warn_name, "%s", nm); }
        }
        if (k == BAY_WRITING) {
            size_t used = strlen(names);
            snprintf(names + used, sizeof names - used, "%s%s", nw ? " ול" : "ל", nm); nw++;
            if (e->drawer->rate_bps > 0 && rate < 0) rate = e->drawer->rate_bps;
            if (e->drawer->eta_s > eta) eta = e->drawer->eta_s;
            stalled += e->drawer->rate_bps == 0;
            if (e->drawer->moved > moved) moved = e->drawer->moved;
        }
        if (k == BAY_DONE) nd++;
        if (k == BAY_DONE && (dg.line_warn || dg.done_warn || dg.flag)) unchecked_done++;
        if (k == BAY_WRITING && !strcmp(e->drawer->state, "verifying")) verifying++;
        if (k == BAY_WAITING) waiting++;
        if (k == BAY_SMART) asking++;
        if (k == BAY_FAILED) { nf++; snprintf(failed_name, sizeof failed_name, "%s", nm); }
        if (k == BAY_SKIPPED) { ns++; snprintf(skipped_name, sizeof skipped_name, "%s", nm); }
        if (k == BAY_SMART) pend = 1;
        if (k == BAY_CANCELED) nc++;
        if (k == BAY_CANCELING) ncg++;
        if (pct >= 0 && (k == BAY_WRITING || k == BAY_DONE)) { sum += pct; known++; }
        if (e->drawer && e->drawer->bytes > most) { most = e->drawer->bytes; whole = e->drawer->total; }
    }
    int ended = s->ndrawers > 0 && !nw && !pend && !ncg && nd + nf + ns + nc == n;

    /* the hero */
    char label[400], title[300], s2[620], big[24], left[96], age[64], image[112], pdisk[SLOT_NAME_MAX + 16];
    /* #2055: while writing, the age of the last progress -- not of the last redraw of this file */
    pace_age(nw && moved > 0 ? moved : s->updated, age, sizeof age);
    if (rate > 0) stalled = 0;            /* one drawer still moving: the stream is not standing */
    /* the name is an isolated run and the line opens with an RLM: a Latin-first
     * name ("Office 2024 — סטנדרט") made auto-dir turn the whole line LTR */
    bidi_isolate(s->cloner_image[0] ? s->cloner_image : "ממתין לשידור מהשרת", image, sizeof image);
    snprintf(label, sizeof label, RLM "שיכפול במקביל · %s%s%s%s", image, dv_any ? " · חבילת דרייברים לפי דגם המגירה" : "",
             age[0] ? " · " : "", age);
    snprintf(big, sizeof big, "%s", "—"); left[0] = 0;
    /* #2081 (mockup 03 section 2, Nadav 24/10: "המשכפל צריך להיראות כמו 1"): a held end of a round
     * is the drawers' list -- a title, one line of what was written, no summary cards, no big number */
    const int held_end = ended && n && s->end_kind != END_NONE && s->end_held;
    if (held_end && !pend) {
        snprintf(title, sizeof title, "הסבב הסתיים — %d מתוך %d נכתבו", nd, n);
        if (whole) snprintf(s2, sizeof s2, RLM "%s · ⁦%.1f GB⁩", image, whole / 1e9);   /* LRI..PDI: "19.5 GB", not "GB 19.5" */
        else snprintf(s2, sizeof s2, RLM "%s", image);
        label[0] = big[0] = 0;
    } else if (pend) {
        cloner_port_label(s, s->smart_port, pdisk, sizeof pdisk);
        snprintf(title, sizeof title, "ממתין להחלטה על %s", pdisk);
        snprintf(s2, sizeof s2, "הכתיבה לדיסק הזה מושהית עד שתחליטו, והוא לא נכתב.");
        snprintf(left, sizeof left, "ממתין לתשובה");
    } else if (!n) {
        snprintf(title, sizeof title, "לא נמצאו דיסקים");
        snprintf(s2, sizeof s2, "המחשב הזה לא דיווח על דיסקים מחוברים. חברו דיסק והפעילו מחדש.");
    } else if (!s->ndrawers) {
        snprintf(title, sizeof title, "ממתין לשידור מהשרת");
        snprintf(s2, sizeof s2, "הדיסקים שלמטה ייכתבו כשהגל יצא.");
    } else if (ended && nf) {
        if (nf == 1) snprintf(title, sizeof title, "השיכפול הסתיים — %s נכשל", failed_name);
        else snprintf(title, sizeof title, "השיכפול הסתיים — %d דיסקים נכשלו", nf);
        snprintf(s2, sizeof s2, "%s", nd ? "הדיסקים שנכשלו אינם שמישים — כותבים אותם שוב בגל הבא." : "אף דיסק לא נכתב.");
        snprintf(big, sizeof big, "%d/%d", nd, n); snprintf(left, sizeof left, "דיסקים תקינים");
    } else if (ended) {
        snprintf(title, sizeof title, "%s", nd ? "השיכפול הסתיים — הדיסקים נכתבו" : "השיכפול הסתיים");
        snprintf(s2, sizeof s2, "%s", ns ? "דיסק שדולג נשאר כפי שהיה." : "כל היעדים נכתבו ונבדקו.");
        if (dv_bad || dv_warn) {   /* #1331: the disk that may not boot first, then the one without drivers */
            char b1[300] = "", b2[300] = "";
            if (dv_bad == 1) snprintf(b1, sizeof b1, "%s: דרייבר האחסון לא הוגדר לעלייה — הדיסק עלול לא לעלות.", bad_name);
            else if (dv_bad) snprintf(b1, sizeof b1, "%d דיסקים: דרייבר האחסון לא הוגדר לעלייה — הם עלולים לא לעלות.", dv_bad);
            if (dv_warn == 1) snprintf(b2, sizeof b2, "%s יעלה בלי דרייברים.", warn_name);
            else if (dv_warn) snprintf(b2, sizeof b2, "%d דיסקים יעלו בלי דרייברים.", dv_warn);
            snprintf(s2, sizeof s2, "%s%s%s", b1, b1[0] && b2[0] ? " " : "", b2);
        }
        if (nd) { snprintf(big, sizeof big, "100%%"); snprintf(left, sizeof left, "הסתיים"); }
    } else {
        if (nw) snprintf(title, sizeof title, "מעתיק %s%s", names, nw > 1 ? " במקביל" : "");
        else snprintf(title, sizeof title, "ממתין לתחילת הזרם");
        if (nc || ncg) snprintf(s2, sizeof s2, "%s", nc ? "דיסק שבוטל — ריק עכשיו, וייכתב רגיל בסבב הבא. השאר ממשיכים."
                                                           : "מבטל דיסק אחד — השאר ממשיכים בלי עצירה.");   /* #1386 */
        else if (ns == 1) snprintf(s2, sizeof s2, "%s דולג. השאר ממשיכים.", skipped_name);
        else if (ns) snprintf(s2, sizeof s2, "%d דיסקים דולגו. השאר ממשיכים.", ns);
        else snprintf(s2, sizeof s2, "הזרם מהשרת נכתב לכל היעדים יחד.");
        if (known) snprintf(big, sizeof big, "%d%%", sum / known);
        if (stalled) snprintf(left, sizeof left, "ממתין לזרם מהשרת");   /* #2055 */
        else if (eta >= 0) {
            if (eta < 60) snprintf(left, sizeof left, "פחות מדקה נותרה");
            else snprintf(left, sizeof left, "כ-%d דק' נותרו", (eta + 30) / 60);
        } else if (nw) snprintf(left, sizeof left, "הזמן שנותר טרם נמדד");
    }
    y += lc_hero(a, cr, x0, xr, y, label, title, s2, big, left, ended && nf && !held_end) + 16;   /* #2081: the held end title is ink, the rows carry the red */

    /* .mets: speed, what was received, the disks -- not on a held end (#2081: no summary cards) */
    double mw = (cw - 24) / 3, mh = held_end ? -12 : 80;
    char v1[48], v2[64], v3[512] = RLM, c3[240];
    if (rate > 0) snprintf(v1, sizeof v1, "%.0f MB/s", rate / 1e6); else snprintf(v1, sizeof v1, "%s", stalled ? "0 MB/s" : nw ? "עוד לא נמדדה" : "—");
    if (whole) snprintf(v2, sizeof v2, "%.1f / %.1f GB", most / 1e9, whole / 1e9);
    else snprintf(v2, sizeof v2, "%s", most ? "" : "—");
    if (!whole && most) snprintf(v2, sizeof v2, "%.1f GB", most / 1e9);
    if (ended) snprintf(v3, sizeof v3, RLM "%d מתוך %d נכתבו", nd, n);
    else {
        disk_count(v3, sizeof v3, nw - verifying, "כותב", "כותבים");
        disk_count(v3, sizeof v3, verifying, "מאמת", "מאמתים");
        disk_count(v3, sizeof v3, waiting, "ממתין", "ממתינים");
        disk_count(v3, sizeof v3, asking, "ממתין להחלטה", "ממתינים להחלטה");
        disk_count(v3, sizeof v3, nd, "הושלם", "הושלמו");
    }
    disk_count(v3, sizeof v3, nf, "נכשל", "נכשלו");
    disk_count(v3, sizeof v3, ns, "דולג", "דולגו");
    disk_count(v3, sizeof v3, nc, "בוטל", "בוטלו");
    disk_count(v3, sizeof v3, ncg, "מבטל", "מבטלים");
    if (!n) snprintf(v3, sizeof v3, "אין דיסקים");
    snprintf(c3, sizeof c3, "%s", !n ? "לא נמצאו דיסקים" : !nd ? (ended ? "אף דיסק לא נכתב" : "עוד לא הסתיים אף דיסק") : "");
    if (ended && unchecked_done) snprintf(c3, sizeof c3, "הכתיבה הסתיימה — ראו את ההערות במגירות");
    else if (ended && nd == 1) {
        for (int i = 0; i < n; i++) if (entry_kind(a, &entries[i]) == BAY_DONE) {
            int bay = entry_drawer(&entries[i]);
            if (bay > 0) snprintf(c3, sizeof c3, "מגירה %d נכתבה ונבדקה", bay);
            else snprintf(c3, sizeof c3, "דיסק אחד נכתב ונבדק");
        }
    } else if (ended && nd > 1) snprintf(c3, sizeof c3, "%d דיסקים נכתבו ונבדקו", nd);
    if (ended && !nf && dv_any) {   /* #1331: only completed outcomes */
        char p1[32] = "", p2[32] = "", p3[32] = "";
        if (dv_ok) snprintf(p1, sizeof p1, "%d הונחו", dv_ok);
        if (dv_no) snprintf(p2, sizeof p2, "%s%d לא", p1[0] ? ", " : "", dv_no);
        if (dv_nomodel) snprintf(p3, sizeof p3, "%s%d בלי דגם", p1[0] || p2[0] ? ", " : "", dv_nomodel);
        snprintf(c3, sizeof c3, "דרייברים: %s%s%s", p1, p2, p3);   /* #2054: "נכתבו" is in the count above */
    }
    if (!held_end) {
    /* A mixed state may wrap; measure it before laying out all three tiles. */
    Text counts = text_make(cr, FONT_SANS, 19, 600, v3, (int)(mw - 28), DIR_RTL);
    Text caption = text_make(cr, FONT_SANS, 11, 400, c3, (int)(mw - 28), DIR_RTL);
    mh = fmax(mh, 30 + counts.h + caption.h + 12);
    text_free(&counts); text_free(&caption);
    lc_met(a, cr, (Rect){ xr - mw, y, mw, mh }, "מהירות", v1, rate > 0 ? "הקצב שנמדד בדיסק הזה" : stalled ? "ממתין לזרם מהשרת" : "",
           stalled ? t->warn : t->muted);   /* #2055: the stream stands -- orange, the write is not stopped */
    lc_met(a, cr, (Rect){ xr - 2 * mw - 12, y, mw, mh }, "התקבלו", v2, whole ? "בזרם הדחוס" : "", t->muted);
    lc_disks_met(a, cr, (Rect){ x0, y, mw, mh }, v3, c3);
    }
    y += mh + 12;

    /* the cage */
    double bar_h = s->smart_pending ? 74 : 0, foot_h = 40;
    double room = H - N_STATUS_H - bar_h - foot_h - 16 - y - (10 + 16 + 8 + 10) - (n > 1 ? (n - 1) * 10 : 0);
    double bh = dv_line ? LC_BAY_DRV_H : LC_BAY_H;   /* #1331: the row with its drivers line */
    if (n && n * bh > room) bh = fmax(56, floor(room / n));
    if (n) {
        Rect cage = { x0, y, cw, 10 + 16 + 8 + n * bh + (n > 1 ? (n - 1) * 10 : 0) + 10 };
        char cnt[96]; snprintf(cnt, sizeof cnt, ended ? "%d מתוך %d נכתבו" : "%d מתוך %d כותבים", ended ? nd : nw, n);   /* #1331 mockup v2 */
        if (nc || ncg) { size_t u = strlen(cnt);    /* #1386: "1 מתוך 2 כותבים · 1 בוטל" */
            if (nc) u += snprintf(cnt + u, sizeof cnt - u, " · %d בוטל", nc);
            if (ncg && u < sizeof cnt) snprintf(cnt + u, sizeof cnt - u, " · %d מבטל", ncg); }
        double by = lc_cage(a, cr, cage, "יעדים", cnt);
        int ci = 0;                                   /* #1386: the row's index among the cancel picks */
        for (int i = 0; i < n; i++) {
            LcBay g; entry_bay(a, &entries[i], &g);
            if (g.kind == BAY_WRITING && !strcmp(entries[i].drawer->state, "writing")) {
                g.act = "בטל…"; g.act_hit = HIT_BAY_CANCEL_BASE + ci++;
                if (g.drawer > 0 && g.drawer < 10) { snprintf(g.act_key_buf, sizeof g.act_key_buf, "%d", g.drawer); g.act_key = g.act_key_buf; }
            } else if (!entries[i].drawer && entries[i].idle) {
                g.act = "איזו מגירה זו?";
                g.act_hit = HIT_IDENTIFY_BASE + (int)(entries[i].idle - s->idisks);   /* #1966: by index -- port 0 = not mapped */
            }
            lc_bay_draw(a, cr, (Rect){ x0 + 10, by, cw - 20, bh }, &g); by += bh + 10;
        }
        y = cage.y + cage.h + 12;
    }

    if (!s->smart_pending) {
        /* the line under the cage. The count to power-off and "כבה עכשיו" at the
         * end of a task are drawn over every screen (fix/end-of-task-power, #1560);
         * here only the words. */
        char note[160];
        if (s->idle_poweroff_at > 0) {
            long long l = s->idle_poweroff_at - (long long)time(NULL); if (l < 0) l = 0;
            snprintf(note, sizeof note, "המחשב יכבה בעוד %lld שניות — כל מקש מבטל.", l);
        } else snprintf(note, sizeof note, "%s", ended ? "הסבב הסתיים במחשב הזה."
                        : cloner_cancel_picks(a, (CancelPick[MAX_DRAWERS + 1]){ 0 }, MAX_DRAWERS + 1)
                        ? "כשל ביעד אחד לא עוצר את האחרים. לבטל דיסק — המקש של המגירה." : "כשל ביעד אחד לא עוצר את האחרים.");
        /* #2081 (mockup 03, Nadav 24/10): a held end (#2024) -- "כבה את המחשב" is the
         * screen's own button, as on the local clone's result; the bar says only why */
        double ow = 0;
        if (s->end_kind != END_NONE && s->end_held) {
            /* #2081 (mockup 03): on the held end the button opens the row under the list (RTL: the right), no note */
            ow = lc_btn(a, cr, held_end ? xr : x0 + lc_btn_width(cr, "כבה את המחשב", "Enter"), y, 40, "כבה את המחשב", "Enter",
                        BTN_PRIMARY, HIT_END_NOW, 0) + 16;
            a->end_off_own = 1;
            if (held_end) return;
        }
        Text fm = text_make(cr, FONT_SANS, 13, 400, note, (int)(cw - ow), DIR_RTL);
        text_draw_r(cr, &fm, xr, y + 8, ended && nf ? t->danger_fg : t->muted); text_free(&fm);
        return;
    }
        /* the question owns the bottom of the screen: a full-width bar, the
         * disk and its trouble at the start, the answers ordered by risk --
         * replace (safe), skip, and "write anyway" as a warning outline, not
         * the primary blue (mockup 10/10; it was the other way round). */
        Rect bar = { 0, H - N_STATUS_H - bar_h, W, bar_h };
        draw_fill_rrect(cr, bar, 0, t->surface);
        cairo_rectangle(cr, 0, bar.y, W, 2); draw_set(cr, t->warn); cairo_fill(cr);
        /* #1580 (Nadav 17/10, "like Clonezilla"): red (SMART fail) gets the button
         * too -- it opens the window, whose red button is the confirmation */
        const char *go = !strcmp(a->st.smart_verdict, "failed_last") ? "נסה שוב…" : "כתוב בכל זאת…";
        char head[SLOT_NAME_MAX + 16 + 160], disk[SLOT_NAME_MAX + 16];
        const char *why = a->st.smart_reason[0] ? smart_reason_text(a->st.smart_reason) : smart_he(a->st.smart_verdict);
        cloner_port_label(&a->st, a->st.smart_port, disk, sizeof disk);   /* #1495 */
        snprintf(head, sizeof head, "%s · %s.", disk, why);
        Text h1 = rtl_block_make(cr, 13, 600, head, W / 2);
        Text h2 = rtl_block_make(cr, 12, 400, "מה לעשות עם הדיסק הזה?", W / 2);
        double ty = bar.y + (bar.h - h1.h - 2 - h2.h) / 2;
        text_draw_r(cr, &h1, W - N_PAD, ty, t->ink);
        text_draw_r(cr, &h2, W - N_PAD, ty + h1.h + 2, t->muted);
        text_free(&h1); text_free(&h2);
        double by = bar.y + (bar.h - N_BUTTON_H) / 2, bw[3], total = 0;
        /* measured first (id 0: nothing is registered), drawn from the inline end */
        bw[0] = key_button(a, cr, 0, by, "החלף דיסק", "R", BTN_PLAIN, 0, 0);
        bw[1] = key_button(a, cr, 0, by, "דלג על הדיסק", "S", BTN_PLAIN, 0, 0);
        bw[2] = key_button(a, cr, 0, by, go, "Enter", BTN_WARN, 0, 0);   /* 16/10: it opens the SMART window */
        for (int i = 0; i < 3; i++) total += bw[i] + (bw[i] > 0 && i ? 10 : 0);
        double x = N_PAD + total;                      /* the group ends at the inline end */
        x -= bw[0]; key_button(a, cr, x, by, "החלף דיסק", "R", BTN_PLAIN, HIT_SMART_REPLACE, 1);
        x -= 10 + bw[1]; key_button(a, cr, x, by, "דלג על הדיסק", "S", BTN_PLAIN, HIT_SMART_SKIP, 1);
        x -= 10 + bw[2]; key_button(a, cr, x, by, go, "Enter", BTN_WARN, HIT_SMART_RESCUE, 1);
}
