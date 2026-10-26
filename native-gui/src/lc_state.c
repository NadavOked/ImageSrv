/* #1322: the boot choice and the local clone -- the state records (lcgui.sh),
 * the roles the person gives the drawers, and the keys and clicks. The
 * drawing is screens_lcboot.c, screens_local.c and screens_lcrun.c.
 *
 * Nothing here writes a disk or decides anything the agent does not check
 * again: a choice leaves as a record on stdout (lc-start|<src>|<t1,t2>|<k>),
 * and localclone.sh measures afresh before a byte moves. The roles stay on
 * the screen side (LcUi) so a 1-second state reload never moves a choice. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include "lcui.h"
#include "screens_rounds.h"
#include "smartdlg.h"   /* 16/10 */
#include "dcui.h"       /* #1914: dcui_token -- the USB settings take the DCUI's keys */

static int lc_split(char *v, char *f[], int max) {
    int n = 0; static char empty[1] = "";
    f[n++] = v;
    for (char *p = v; *p && n < max; p++) if (*p == '|') { *p = 0; f[n++] = p + 1; }
    for (int i = n; i < max; i++) f[i] = empty;
    return n;
}
static void cpy(char *d, size_t n, const char *s) {   /* #2084: a cut never leaves half a letter (it drew a broken character) */
    snprintf(d, n, "%s", s);
    size_t len = strlen(d);
    if (len == strlen(s)) return;
    while (len && ((unsigned char)d[len - 1] & 0xC0) == 0x80) len--;
    if (len && ((unsigned char)d[len - 1] & 0xC0) == 0xC0) len--;
    d[len] = 0;
}
static long long ll(const char *s) { return s[0] ? strtoll(s, NULL, 10) : 0; }

static int verdict_of(const char *v) {
    if (!strcmp(v, "fits")) return LCV_FITS;
    if (!strcmp(v, "shrink")) return LCV_SHRINK;
    if (!strcmp(v, "short")) return LCV_SHORT;
    if (!strcmp(v, "measuring")) return LCV_MEASURING;
    return LCV_UNCHECKED;          /* anything else is "not checked", never "fits" */
}

int lc_state_line(State *s, const char *key, char *val) {
    LcState *l = &s->lc; char *f[18];   /* lc_disk splits into 18 (#1921's hw note, #2001's name) */
    if (strncmp(key, "lc_", 3)) return 0;
    if (!strcmp(key, "lc_round")) l->round_open = !strcmp(val, "open");   /* #1367: anything else draws nothing */
    else if (!strcmp(key, "lc_screen")) l->screen = !strcmp(val, "boot") ? LC_SCREEN_BOOT : !strcmp(val, "local") ? LC_SCREEN_LOCAL
                                                   : !strcmp(val, "pull") ? LC_SCREEN_PULL
                                                   : !strcmp(val, "usbset") ? LC_SCREEN_USBSET : LC_SCREEN_NONE;
    else if (!strcmp(key, "lc_local")) l->local_ok = !strcmp(val, "1");   /* 16/10: anything else = not offered */
    else if (!strcmp(key, "lc_usb")) l->usb = !strcmp(val, "1");          /* #1914: the clone stick */
    else if (!strcmp(key, "lc_diag")) cpy(l->diag, sizeof l->diag, val);   /* #1914: diag-<stamp>.txt */
    else if (!strcmp(key, "lc_ide")) l->ide = !strcmp(val, "ide");        /* #1914: anything else says nothing */
    else if (!strcmp(key, "lc_survey")) l->survey = !strcmp(val, "1");    /* #1914/#1921: the survey stick */
    else if (!strcmp(key, "lc_unread")) {   /* Nadav 22/10: dev|n, n > 0 digits only -- anything else says nothing */
        if (lc_split(val, f, 2) != 2 || !f[0][0] || !f[1][0] || strspn(f[1], "0123456789") != strlen(f[1]) || strlen(f[1]) > 12) return 1;
        cpy(l->unread_dev, sizeof l->unread_dev, f[0]); l->unread = ll(f[1]);
    }
    else if (!strcmp(key, "lc_boot")) { lc_split(val, f, 3); l->boot_deadline = ll(f[0]); l->boot_paused = atoi(f[1]) == 2 ? 2 : atoi(f[1]) == 1; l->boot_now = ll(f[2]); }
    else if (!strcmp(key, "lc_inv")) l->inv_done = !strcmp(val, "done");
    else if (!strcmp(key, "lc_phase")) cpy(l->phase, sizeof l->phase, val);
    else if (!strcmp(key, "lc_disk") && l->ndisks < LC_MAX_DISKS) {
        int nf = lc_split(val, f, 18);   /* #1798: the mark is field 16; #1921: the hw note 17; #2001: the name 18 */
        if (nf < 13) return 1;
        LcDisk *d = &l->disks[l->ndisks++];
        d->port = atoi(f[0]); cpy(d->dev, sizeof d->dev, f[1]); cpy(d->model, sizeof d->model, f[2]);
        cpy(d->serial, sizeof d->serial, f[3]); d->size = strtoull(f[4], NULL, 10);
        cpy(d->smart, sizeof d->smart, f[5]); cpy(d->smart_reason, sizeof d->smart_reason, f[6]);
        cpy(d->os, sizeof d->os, f[7]); d->nparts = atoi(f[8]); d->used = f[9][0] ? ll(f[9]) : -1;
        d->need = f[10][0] ? ll(f[10]) : -1; d->usable = atoi(f[11]) == 1; cpy(d->why, sizeof d->why, f[12]);
        if (nf >= 14 && f[13][0]) {
            char *end = NULL; long drawer = strtol(f[13], &end, 10);
            size_t digits = strspn(f[13], "0123456789");
            d->drawer = digits == strlen(f[13]) && end && *end == 0 &&
                        drawer >= 1 && drawer <= MAX_DRAWERS ? (int)drawer : -1;
        }
        if (nf >= 15) cpy(d->conn, sizeof d->conn, f[14]);
        if (nf >= 16 && (!strcmp(f[15], "incomplete") || !strcmp(f[15], "unchecked")))
            cpy(d->mark, sizeof d->mark, f[15]);   /* #1798: anything else draws nothing */
        if (nf >= 17) cpy(d->hw, sizeof d->hw, f[16]);
        if (nf >= 18) cpy(d->name, sizeof d->name, f[17]);
    } else if (!strcmp(key, "lc_src")) {
        int nf = lc_split(val, f, 4); cpy(l->src, sizeof l->src, f[0]); cpy(l->src_measure, sizeof l->src_measure, f[1]);
        l->src_copy = nf >= 3 && f[2][0] ? ll(f[2]) : -2;   /* #1443: empty, or an agent without it = not yet */
        if (l->src_copy < -2) l->src_copy = -1;
        cpy(l->src_copy_why, sizeof l->src_copy_why, f[3]);
    }
    else if (!strcmp(key, "lc_fit") && l->nfits < LC_MAX_DISKS) {
        lc_split(val, f, 4);
        LcFit *x = &l->fits[l->nfits++];
        cpy(x->dev, sizeof x->dev, f[0]); x->verdict = verdict_of(f[1]); x->need = f[2][0] ? ll(f[2]) : -1;
        cpy(x->reason, sizeof x->reason, f[3]);
    } else if (!strcmp(key, "lc_run")) {
        lc_split(val, f, 9); l->has_run = 1;
        cpy(l->run_src, sizeof l->run_src, f[0]); l->total = !strcmp(f[1], "null") ? -1 : ll(f[1]);
        l->copied = ll(f[2]); l->elapsed = atoi(f[3]); l->shrunk = atoi(f[4]) == 1;
        cpy(l->report, sizeof l->report, f[5]); l->cur_part = atoi(f[6]); cpy(l->src_restore, sizeof l->src_restore, f[7]);
        cpy(l->shrink_path, sizeof l->shrink_path, f[8]);   /* #1477; an older agent sends 8 fields -- "" */
    } else if (!strcmp(key, "lc_part") && l->nparts < LC_MAX_PARTS) {
        lc_split(val, f, 7);
        LcPart *p = &l->parts[l->nparts++];
        p->idx = atoi(f[0]); cpy(p->label, sizeof p->label, f[1]); cpy(p->fs, sizeof p->fs, f[2]);
        p->bytes = ll(f[3]); p->size = ll(f[4]); cpy(p->state, sizeof p->state, f[5]); p->in = ll(f[6]);
    } else if (!strcmp(key, "lc_tgt") && l->ntgts < LC_MAX_DISKS) {
        lc_split(val, f, 13);   /* #2009: the 10th, the note; #2017: the 11th, raw,rescued; #2045: the 12th, pulled; #2196: the 13th, grow deferred -- an agent that sends fewer = none */
        LcTgt *t = &l->tgts[l->ntgts++];
        cpy(t->dev, sizeof t->dev, f[0]); cpy(t->state, sizeof t->state, f[1]); t->bytes = ll(f[2]);
        t->part = atoi(f[3]); t->ok = atoi(f[4]); cpy(t->error, sizeof t->error, f[6]); t->total = ll(f[7]); t->ntfs_boot = atoi(f[8]); cpy(t->note, sizeof t->note, f[9]);
        t->has_table = sscanf(f[5], "%d,%lld,%lld,%lld", &t->grown_idx, &t->grown_bytes, &t->src_bytes, &t->free_bytes) == 4;
        t->rescued = -1; if (sscanf(f[10], "%d,%lld", &t->raw_parts, &t->rescued) != 2) { t->raw_parts = 0; t->rescued = -1; }   /* #2017; an older agent: none */
        t->pulled = !strcmp(f[11], "1");   /* #2045; an older agent: not pulled -- the stricter text */
        t->grow_deferred = !strcmp(f[12], "1");   /* #2196; an older agent: the error line still says it */
    }
    return 1;
}

/* ---- the rules -------------------------------------------------------------- */

static double mono_now(void) { struct timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts); return ts.tv_sec + ts.tv_nsec / 1e9; }

int lc_view(const App *a) {
    const char *ph = a->st.lc.phase;
    if (!strcmp(ph, "done")) return LCVIEW_DONE;
    if (ph[0] && strcmp(ph, "idle")) return LCVIEW_PROGRESS;
    if (a->lcu.start_sent > 0 && mono_now() - a->lcu.start_sent < 6) return LCVIEW_PROGRESS;
    return LCVIEW_SOURCE;
}

const LcDisk *lc_disk_by_dev(const App *a, const char *dev) {
    for (int i = 0; i < a->st.lc.ndisks; i++) if (!strcmp(a->st.lc.disks[i].dev, dev)) return &a->st.lc.disks[i];
    return NULL;
}

static int stored_role(const App *a, const char *dev) {
    for (int i = 0; i < a->lcu.nroles; i++) if (!strcmp(a->lcu.role_dev[i], dev)) return a->lcu.role[i];
    return LCR_SKIP;
}

static void set_stored(App *a, const char *dev, int r) {
    for (int i = 0; i < a->lcu.nroles; i++) if (!strcmp(a->lcu.role_dev[i], dev)) { a->lcu.role[i] = r; return; }
    if (a->lcu.nroles >= LC_MAX_DISKS) return;
    snprintf(a->lcu.role_dev[a->lcu.nroles], sizeof a->lcu.role_dev[0], "%s", dev);
    a->lcu.role[a->lcu.nroles++] = r;
}

int lc_source_index(const App *a) {
    for (int i = 0; i < a->st.lc.ndisks; i++) if (stored_role(a, a->st.lc.disks[i].dev) == LCR_SOURCE) return i;
    return -1;
}

const LcFit *lc_fit_of(const App *a, const char *dev) {
    int s = lc_source_index(a);
    /* a verdict is about the source the agent measured; until it is this one, it is not known */
    if (s < 0 || strcmp(a->st.lc.src, a->st.lc.disks[s].dev)) return NULL;
    for (int i = 0; i < a->st.lc.nfits; i++) if (!strcmp(a->st.lc.fits[i].dev, dev)) return &a->st.lc.fits[i];
    return NULL;
}

int lc_target_ok(const App *a, int i) {
    if (smart_level(a->st.lc.disks[i].smart) == 2) return 0;   /* #1732: red (SMART fail) is never a target */
    const LcFit *f = lc_fit_of(a, a->st.lc.disks[i].dev);
    return !f || f->verdict == LCV_FITS || f->verdict == LCV_SHRINK || f->verdict == LCV_MEASURING;
}

/* The role drawn and acted on: a target the verdict rules out is "דלג". */
int lc_role_of(const App *a, const char *dev) {
    int r = stored_role(a, dev);
    if (r != LCR_TARGET) return r;
    for (int i = 0; i < a->st.lc.ndisks; i++)
        if (!strcmp(a->st.lc.disks[i].dev, dev)) return lc_target_ok(a, i) ? LCR_TARGET : LCR_SKIP;
    return LCR_SKIP;
}

int lc_targets(const App *a, int *idx, int max) {
    int n = 0;
    for (int i = 0; i < a->st.lc.ndisks && n < max; i++)
        if (lc_role_of(a, a->st.lc.disks[i].dev) == LCR_TARGET) idx[n++] = i;
    return n;
}

int lc_can_go(const App *a) {
    int idx[LC_MAX_DISKS], n = lc_targets(a, idx, LC_MAX_DISKS), s = lc_source_index(a);
    if (!n || s < 0 || !a->st.lc.disks[s].usable) return 0;
    for (int k = 0; k < n; k++) {
        const LcFit *f = lc_fit_of(a, a->st.lc.disks[idx[k]].dev);
        if (!f || (f->verdict != LCV_FITS && f->verdict != LCV_SHRINK)) return 0;   /* measured, and allowed */
    }
    return 1;
}

int lc_any_shrink(const App *a) {
    int idx[LC_MAX_DISKS], n = lc_targets(a, idx, LC_MAX_DISKS);
    for (int k = 0; k < n; k++) { const LcFit *f = lc_fit_of(a, a->st.lc.disks[idx[k]].dev); if (f && f->verdict == LCV_SHRINK) return 1; }
    return 0;
}

static void emit_line(const char *s) { printf("%s\n\n", s); fflush(stdout); }

static void measure_source(App *a) {
    int s = lc_source_index(a);
    if (s < 0 || a->fixed_clock) return;          /* --png renders, it does not ask */
    if (!strcmp(a->lcu.measured, a->st.lc.disks[s].dev)) return;
    snprintf(a->lcu.measured, sizeof a->lcu.measured, "%s", a->st.lc.disks[s].dev);
    char line[64]; snprintf(line, sizeof line, "lc-measure|%s", a->lcu.measured);
    emit_line(line);
}

/* SMART that asks before writing (fail, orange) or failed the last clone (red): "דלג" by default. */
static int smart_skip(const LcDisk *d) { return smart_level(d->smart) > 0; }

static void set_source(App *a, int i) {
    if (i < 0 || i >= a->st.lc.ndisks || !a->st.lc.disks[i].usable) return;
    int old = lc_source_index(a);
    if (old == i) return;
    set_stored(a, a->st.lc.disks[i].dev, LCR_SOURCE);
    /* the mockup's swap -- 16/10: a warned disk is a target only through the SMART window */
    if (old >= 0) set_stored(a, a->st.lc.disks[old].dev, smart_skip(&a->st.lc.disks[old]) ? LCR_SKIP : LCR_TARGET);
    measure_source(a);
}

/* 16/10 (Nadav): the SMART warning window about drawer i (smartdlg.c). */
static void smart_ask(App *a, int kind, int i) {
    const LcDisk *d = &a->st.lc.disks[i]; char label[LC_LABEL_MAX];
    lc_disk_label(d, label, sizeof label);
    sdlg_open(a, kind, i, d->dev, label, d->smart, d->smart_reason, d->model, d->serial);
}

/* #1722: a source is proposed only with a system on it -- a partition table
 * and a filesystem the inventory read (os) -- never on "usable" alone. */
static int proposable(const LcDisk *d) { return d->usable && d->os[0]; }

/* #1722: the person's last lc-start, all of it or nothing: every disk of it
 * still here with the serial it had, and the source still a source. */
static void restore_last(App *a) {
    const LcUi *u = &a->lcu; const LcDisk *s = u->last_src[0] ? lc_disk_by_dev(a, u->last_src) : NULL;
    if (!s || !s->usable || strcmp(s->serial, u->last_src_serial)) return;
    for (int k = 0; k < u->nlast; k++) {
        const LcDisk *t = lc_disk_by_dev(a, u->last_tgt[k]);
        if (!t || strcmp(t->serial, u->last_tgt_serial[k])) return;
    }
    set_stored(a, s->dev, LCR_SOURCE);
    for (int k = 0; k < u->nlast; k++) set_stored(a, u->last_tgt[k], LCR_TARGET);
}

static void default_roles(App *a) {
    const LcState *l = &a->st.lc; int src = -1;
    a->lcu.nroles = 0; a->lcu.measured[0] = 0; a->lcu.src_ack[0] = 0;
    if (a->lcu.ran) {
        /* #1722: after a run the drawers are not what was read before it -- a
         * target may be empty, or half a copy. Nothing is proposed: after a
         * stop the person's own last choice, exactly; else no choice at all. */
        if (a->lcu.keep) restore_last(a);
        for (int i = 0; i < l->ndisks; i++)
            if (stored_role(a, l->disks[i].dev) == LCR_SKIP) set_stored(a, l->disks[i].dev, LCR_SKIP);
        src = lc_source_index(a);
    } else {
        for (int i = 0; i < l->ndisks; i++) if (eff_drawer(l->disks[i].drawer, l->disks[i].port) == 3 && proposable(&l->disks[i])) src = i;    /* drawer 3 */
        for (int i = 0; src < 0 && i < l->ndisks; i++) if (proposable(&l->disks[i])) src = i;
        for (int i = 0; i < l->ndisks; i++)
            set_stored(a, l->disks[i].dev, i == src ? LCR_SOURCE : smart_skip(&l->disks[i]) ? LCR_SKIP : LCR_TARGET);
    }
    a->lcu.bay = src >= 0 ? src : 0; a->lcu.focus = LCF_BAY; a->lcu.confirm = a->lcu.stop = 0; a->lcu.touched = 0;
}

/* #2008 (Nadav 22/10, the USB stick: "Enter, Enter"): until the person touches the choice, the
 * focus is on "המשך לאישור" -- the main action below -- as soon as it can go. On the stick the
 * confirmation that follows opens on the red "מחק והעתק" (open_confirm): Nadav 22/10 turned
 * #2008's "a destructive dialog keeps its focus on חזרה" around for the stick, so Enter-Enter
 * starts the copy (Esc is still "חזרה"). The stick only: the cloner's local clone keeps the
 * focus on the drawers, and its confirmation on "חזרה". */
static void usb_focus_next(App *a) {
    if (a->st.lc.usb && !a->lcu.touched && !a->lcu.confirm && !a->lcu.stop && a->lcu.focus == LCF_BAY
        && lc_view(a) == LCVIEW_SOURCE && lc_can_go(a)) a->lcu.focus = LCF_NEXT;
}

void lc_after_reload(App *a) {
    LcState *l = &a->st.lc;
    if (l->screen != LC_SCREEN_BOOT) a->lcu.pause_sent = 0;
    if (l->screen == LC_SCREEN_PULL) lc_pull_after_reload(a);   /* 16/10: a drawer that is gone is not ticked */
    if (l->has_run) lc_rate_sample(&a->lcu, l->elapsed, l->copied); else a->lcu.rate_n = 0;   /* #1437 */
    /* 16/10: the SMART window about a drawer that moved, or off the source view, is closed */
    if ((a->sdlg.kind == SDLG_LC_TARGET || a->sdlg.kind == SDLG_LC_SOURCE) &&
        (l->screen != LC_SCREEN_LOCAL || strcmp(l->phase, "idle") || a->sdlg.ref >= l->ndisks ||
         strcmp(l->disks[a->sdlg.ref].dev, a->sdlg.dev))) a->sdlg.kind = SDLG_NONE;
    if (l->screen == LC_SCREEN_USBSET) return;   /* #1914: the settings keep the source/targets chosen */
    if (l->screen != LC_SCREEN_LOCAL) { a->lcu.nroles = 0; a->lcu.last_phase[0] = 0; a->lcu.start_sent = 0; a->lcu.keep = 0; return; }
    int idle = !strcmp(l->phase, "idle");
    if (l->phase[0] && !idle) a->lcu.ran = 1;     /* #1722: also a run this screen did not start */
    /* #1722: back to choosing after a run -- "שכפול נוסף" after done, or a stop
     * (the agent goes stopped -> idle in a breath: any phase but done). */
    if (idle && a->lcu.last_phase[0] && strcmp(a->lcu.last_phase, "idle")) {
        a->lcu.nroles = 0; a->lcu.keep = strcmp(a->lcu.last_phase, "done") != 0;
    }
    if (!idle) a->lcu.start_sent = 0;
    snprintf(a->lcu.last_phase, sizeof a->lcu.last_phase, "%s", l->phase);
    if (!l->inv_done) return;
    if (!a->lcu.nroles) default_roles(a);
    /* #1722: a source the drawers no longer allow (no table, the layout not read) is not one */
    int s = lc_source_index(a);
    if (s >= 0 && !l->disks[s].usable) set_stored(a, l->disks[s].dev, LCR_SKIP);
    /* a disk that appeared since the roles were set has none stored: "דלג", never a target by surprise */
    if (a->lcu.bay >= l->ndisks) a->lcu.bay = 0;
    if (!strcmp(l->phase, "idle")) measure_source(a);
    usb_focus_next(a);   /* #2008 */
}

/* ---- keys and clicks ---------------------------------------------------------- */

static void start(App *a) {
    if (!lc_can_go(a)) return;
    int idx[LC_MAX_DISKS], n = lc_targets(a, idx, LC_MAX_DISKS), s = lc_source_index(a);
    char line[256]; int k = snprintf(line, sizeof line, "lc-start|%s|", a->st.lc.disks[s].dev);
    for (int i = 0; i < n && k > 0 && (size_t)k < sizeof line; i++)
        k += snprintf(line + k, sizeof line - k, "%s%s", i ? "," : "", a->st.lc.disks[idx[i]].dev);
    if (k <= 0 || (size_t)k + 3 >= sizeof line) return;   /* cut = no order */
    snprintf(line + k, sizeof line - k, "|%d", lc_any_shrink(a));
    LcUi *u = &a->lcu; const LcDisk *sd = &a->st.lc.disks[s];   /* #1722: what the person chose, to draw again after a stop */
    snprintf(u->last_src, sizeof u->last_src, "%s", sd->dev); snprintf(u->last_src_serial, sizeof u->last_src_serial, "%s", sd->serial);
    u->nlast = 0;
    for (int i = 0; i < n && u->nlast < LC_MAX_DISKS; i++, u->nlast++) {
        const LcDisk *td = &a->st.lc.disks[idx[i]];
        snprintf(u->last_tgt[u->nlast], sizeof u->last_tgt[0], "%s", td->dev);
        snprintf(u->last_tgt_serial[u->nlast], sizeof u->last_tgt_serial[0], "%s", td->serial);
    }
    a->lcu.confirm = 0; a->lcu.start_sent = mono_now(); a->lcu.focus = LCF_STOP;
    emit_line(line);
}

static void role_click(App *a, int i, int r) {
    if (i < 0 || i >= a->st.lc.ndisks) return;
    a->lcu.bay = i; a->lcu.focus = LCF_BAY;
    const char *dev = a->st.lc.disks[i].dev;
    if (r == LCR_SOURCE) { set_source(a, i); return; }
    if (stored_role(a, dev) == LCR_SOURCE) return;             /* the source changes by choosing another */
    if (r == LCR_TARGET && !lc_target_ok(a, i)) return;        /* "לא נכנס" / "לא נבדק" cannot be a target */
    /* 16/10: a warned drawer becomes a target only through the SMART window */
    if (r == LCR_TARGET && stored_role(a, dev) != LCR_TARGET && smart_skip(&a->st.lc.disks[i])) { smart_ask(a, SDLG_LC_TARGET, i); return; }
    set_stored(a, dev, r);
}

/* "המשך לאישור": 16/10 -- a source with a SMART warning opens the window first
 * (reading a failing disk is allowed, to rescue it), once per source. #2017 (Nadav
 * 22/10): red (F1) is asked as before; orange (warn, failed_last) is only told --
 * the window is a notice with "המשך" alone (smartdlg.c sdlg_notice). */
static void open_confirm(App *a) {
    int s = lc_source_index(a);
    if (!lc_can_go(a)) return;
    if (s >= 0 && smart_skip(&a->st.lc.disks[s]) && strcmp(a->lcu.src_ack, a->st.lc.disks[s].dev)) { smart_ask(a, SDLG_LC_SOURCE, s); return; }
    a->lcu.confirm = 1; a->lcu.focus = a->st.lc.usb ? LCF_GO : LCF_CANCEL;   /* Nadav 22/10: the stick on "מחק והעתק"; the cloner on "חזרה" */
}

/* The window's answer: only its red button changes anything, and only when
 * the drawer is still the one it was opened for. */
static void smart_answer(App *a, const SmartDlg *d) {
    if (!d->kind || !d->go || d->ref < 0 || d->ref >= a->st.lc.ndisks || strcmp(a->st.lc.disks[d->ref].dev, d->dev)) return;
    if (d->kind == SDLG_LC_TARGET && lc_target_ok(a, d->ref) && stored_role(a, d->dev) != LCR_SOURCE) set_stored(a, d->dev, LCR_TARGET);
    else if (d->kind == SDLG_LC_SOURCE && lc_source_index(a) == d->ref) {
        snprintf(a->lcu.src_ack, sizeof a->lcu.src_ack, "%s", a->st.lc.disks[d->ref].dev);   /* the same name (checked above), same size */
        open_confirm(a);
    }
}

static void cycle_role(App *a, int dir) {
    int i = a->lcu.bay; if (i < 0 || i >= a->st.lc.ndisks) return;
    if (stored_role(a, a->st.lc.disks[i].dev) == LCR_SOURCE) return;
    static const int order[3] = { LCR_SOURCE, LCR_TARGET, LCR_SKIP };
    int cur = lc_role_of(a, a->st.lc.disks[i].dev), at = 0;
    for (int k = 0; k < 3; k++) if (order[k] == cur) at = k;
    for (int n = 0; n < 3; n++) {
        at = (at + dir + 3) % 3;
        if (order[at] == LCR_TARGET && !lc_target_ok(a, i)) continue;
        if (order[at] == LCR_SOURCE && !a->st.lc.disks[i].usable) continue;
        role_click(a, i, order[at]); return;
    }
}

static void back(App *a) { if (a->st.lc.usb) return; a->lcu.nroles = 0; emit_line("lc-back"); }   /* #1914: no back on the stick */

/* 16/10: "שחזור אימג' מהשרת" -- the restore screen starts with nothing chosen */
static void boot_pull(App *a) {
    memset(a->pull_sel, 0, sizeof a->pull_sel); a->pull_focus = 0;
    a->restore_image_sel = 0; a->pick_in_folder = 0; a->pick_folder[0] = 0; a->confirm = 0;
    a->st.form_error[0] = 0;
    emit_line("boot-pull");
}

static void boot_choose(App *a, int focus) {
    if (focus == 2) boot_pull(a);
    else if (focus == 1) { if (a->st.lc.local_ok) emit_line("boot-local"); }   /* one disk: nothing to copy to */
    else emit_line("boot-server");
}

static int boot_handle(App *a, const Event *e) {
    LcUi *u = &a->lcu;
    int touch = e->type == UIEV_CHAR || e->type == UIEV_KEY || e->type == UIEV_CLICK || e->type == UIEV_SCROLL;
    if (e->type == UIEV_MOVE) {                    /* the mockup: a move of more than 4px stops the count */
        if (!u->move_init) { u->move_init = 1; u->move_x = e->x; u->move_y = e->y; }
        else if ((e->x - u->move_x) * (e->x - u->move_x) + (e->y - u->move_y) * (e->y - u->move_y) > 16) touch = 1;
    }
    if (touch && !a->st.lc.boot_paused && !u->pause_sent) { u->pause_sent = 1; emit_line("boot-pause"); }
    /* the focus walks the cards that can be chosen: 0, 1 (two disks or more), 2 */
    int order[3] = { 0, 1, 2 }, n = 3;
    if (!a->st.lc.local_ok) { order[1] = 2; n = 2; if (u->boot_focus == 1) u->boot_focus = 0; }
    if (e->type == UIEV_CHAR) {
        if (e->ch >= '1' && e->ch <= '3') boot_choose(a, e->ch - '1');
        else if (e->ch == ' ') boot_choose(a, u->boot_focus);
    } else if (e->type == UIEV_KEY) {
        if (e->key == KEYSYM_DOWN) u->boot_focus = 2;               /* the restore card is the row below */
        else if (e->key == KEYSYM_UP) { if (u->boot_focus == 2) u->boot_focus = 0; }
        else if (e->key == KEYSYM_LEFT && u->boot_focus == 0 && a->st.lc.local_ok) u->boot_focus = 1;   /* RTL: "שיכפול מקומי" is on the left */
        else if (e->key == KEYSYM_RIGHT && u->boot_focus == 1) u->boot_focus = 0;
        else if (e->key == KEYSYM_TAB || e->key == KEYSYM_BACKTAB) {
            int at = 0, d = e->key == KEYSYM_TAB ? 1 : n - 1;
            for (int i = 0; i < n; i++) if (order[i] == u->boot_focus) at = i;
            u->boot_focus = order[(at + d) % n];
        } else if (e->key == KEYSYM_ENTER) boot_choose(a, u->boot_focus);
    } else if (e->type == UIEV_CLICK) {
        int id = app_hit(a, e->x, e->y);
        if (id == HIT_LC_BOOT_SERVER) boot_choose(a, 0);
        else if (id == HIT_LC_BOOT_LOCAL) boot_choose(a, 1);
        else if (id == HIT_LC_BOOT_PULL) boot_choose(a, 2);
    }
    return 1;
}

static int confirm_stops(const App *a, int *out) {   /* drop links, then the red button, then "חזרה" */
    int idx[LC_MAX_DISKS], n = lc_targets(a, idx, LC_MAX_DISKS), k = 0;
    for (int i = 0; i < n; i++) if (smart_level(a->st.lc.disks[idx[i]].smart)) out[k++] = LCF_DROP * 100 + idx[i];
    out[k++] = LCF_GO; out[k++] = LCF_CANCEL;
    return k;
}

static void focus_step(App *a, const int *stops, int n, int dir) {
    int at = 0, cur = a->lcu.focus == LCF_DROP ? LCF_DROP * 100 + a->lcu.bay : a->lcu.focus;
    for (int i = 0; i < n; i++) if (stops[i] == cur) at = i;
    int next = stops[(at + dir + n) % n];
    if (next >= LCF_DROP * 100) { a->lcu.focus = LCF_DROP; a->lcu.bay = next - LCF_DROP * 100; } else a->lcu.focus = next;
}

static void drop(App *a, int i) {
    if (i < 0 || i >= a->st.lc.ndisks) return;
    set_stored(a, a->st.lc.disks[i].dev, LCR_SKIP);
    int idx[LC_MAX_DISKS];
    if (!lc_targets(a, idx, LC_MAX_DISKS)) { a->lcu.confirm = 0; a->lcu.bay = i; a->lcu.focus = LCF_BAY; }
    else a->lcu.focus = LCF_CANCEL;
}

static int dir_of(const Event *e) {
    return e->key == KEYSYM_TAB || e->key == KEYSYM_LEFT ? 1 : e->key == KEYSYM_BACKTAB || e->key == KEYSYM_RIGHT ? -1 : 0;
}

static int local_handle(App *a, const Event *e) {
    LcUi *u = &a->lcu; int view = lc_view(a), id = e->type == UIEV_CLICK ? app_hit(a, e->x, e->y) : 0;
    if (a->sdlg.kind == SDLG_LC_TARGET || a->sdlg.kind == SDLG_LC_SOURCE) {   /* 16/10: modal */
        SmartDlg d;
        if (sdlg_handle(a, e, &d)) smart_answer(a, &d);
        return 1;
    }
    if (view == LCVIEW_SOURCE && u->confirm) {
        int stops[LC_MAX_DISKS + 2], n = confirm_stops(a, stops);
        if (e->type == UIEV_KEY) {
            if (e->key == KEYSYM_ESC) { u->confirm = 0; u->focus = LCF_NEXT; }
            else if (dir_of(e)) focus_step(a, stops, n, dir_of(e));
            else if (e->key == KEYSYM_ENTER) {
                if (u->focus == LCF_GO) start(a);
                else if (u->focus == LCF_DROP) drop(a, u->bay);
                else { u->confirm = 0; u->focus = LCF_NEXT; }
            }
        } else if (id == HIT_LC_GO) start(a);
        else if (id == HIT_LC_CANCEL) { u->confirm = 0; u->focus = LCF_NEXT; }
        else if (id >= HIT_LC_DROP_BASE && id < HIT_LC_DROP_BASE + LC_MAX_DISKS) drop(a, id - HIT_LC_DROP_BASE);
        return 1;
    }
    if (view == LCVIEW_SOURCE) {
        if (e->type != UIEV_MOVE && id != HIT_LC_USBSET && !(e->type == UIEV_KEY && e->key == KEYSYM_F4)) u->touched = 1;   /* #2008: the person's choice from now on ("הגדרות" is not) */
        if (e->type == UIEV_CHAR && e->ch >= '1' && e->ch <= '9') {
            for (int i = 0; i < a->st.lc.ndisks; i++) if (eff_drawer(a->st.lc.disks[i].drawer, a->st.lc.disks[i].port) == e->ch - '0') { u->bay = i; u->focus = LCF_BAY; set_source(a, i); }
        } else if (e->type == UIEV_KEY) {
            if (e->key == KEYSYM_ESC) back(a);
            else if (e->key == KEYSYM_F4 && a->st.lc.usb) emit_line("lc-usbset");   /* mockup 01: "הגדרות" in the top bar, F4 */
            else if (e->key == KEYSYM_UP && u->bay > 0) { u->bay--; u->focus = LCF_BAY; }
            else if (e->key == KEYSYM_DOWN && u->bay + 1 < a->st.lc.ndisks) { u->bay++; u->focus = LCF_BAY; }
            else if (u->focus == LCF_BAY && (e->key == KEYSYM_LEFT || e->key == KEYSYM_RIGHT)) cycle_role(a, e->key == KEYSYM_LEFT ? 1 : -1);
            else if (e->key == KEYSYM_TAB || e->key == KEYSYM_BACKTAB) {
                int usb = a->st.lc.usb;   /* #1914: no "חזרה" stop on the stick */
                int stops[3] = { LCF_BAY, usb ? LCF_NEXT : LCF_BACK, LCF_NEXT }, n = (lc_can_go(a) ? 3 : 2) - usb;
                u->confirm = 0; focus_step(a, stops, n, e->key == KEYSYM_TAB ? 1 : -1);
            } else if (e->key == KEYSYM_ENTER) {
                if (u->focus == LCF_BACK) back(a);
                else if (u->focus == LCF_NEXT && lc_can_go(a)) open_confirm(a);
                else if (u->focus == LCF_BAY && lc_can_go(a)) u->focus = LCF_NEXT;
            }
        } else if (e->type == UIEV_CLICK) {
            if (id >= HIT_LC_ROLE_BASE && id < HIT_LC_ROLE_BASE + 3 * LC_MAX_DISKS) role_click(a, (id - HIT_LC_ROLE_BASE) / 3, (id - HIT_LC_ROLE_BASE) % 3);
            else if (id >= HIT_LC_BAY_BASE && id < HIT_LC_BAY_BASE + LC_MAX_DISKS) { u->bay = id - HIT_LC_BAY_BASE; u->focus = LCF_BAY; }
            else if (id == HIT_LC_BACK) back(a);
            else if (id == HIT_LC_USBSET && a->st.lc.usb) emit_line("lc-usbset");   /* #1914 */
            else if (id == HIT_LC_NEXT && lc_can_go(a)) open_confirm(a);
        }
        return 1;
    }
    if (view == LCVIEW_PROGRESS) {
        int live = !strcmp(a->st.lc.phase, "copying") || !strcmp(a->st.lc.phase, "shrinking");
        if (!live) u->stop = 0;
        if (u->stop) {
            if (e->type == UIEV_KEY && e->key == KEYSYM_ESC) u->stop = 0;
            else if (e->type == UIEV_KEY && dir_of(e)) u->focus = u->focus == LCF_STOP_GO ? LCF_STOP_NO : LCF_STOP_GO;
            else if ((e->type == UIEV_KEY && e->key == KEYSYM_ENTER && u->focus == LCF_STOP_GO) || id == HIT_LC_STOP_GO) { u->stop = 0; emit_line("lc-stop"); }
            else if ((e->type == UIEV_KEY && e->key == KEYSYM_ENTER) || id == HIT_LC_STOP_NO) u->stop = 0;
            return 1;
        }
        if (live && ((e->type == UIEV_KEY && (e->key == KEYSYM_ESC || (e->key == KEYSYM_ENTER && u->focus == LCF_STOP))) || id == HIT_LC_STOP)) {
            u->stop = 1; u->focus = LCF_STOP_NO;    /* the mockup: the focus on "המשך להעתיק" */
        }
        return 1;
    }
    /* LCVIEW_DONE: "כבה את המחשב" is the default */
    int usb = a->st.lc.usb;   /* #1914: "הפעל מחדש" only on the USB stick */
    if (u->focus != LCF_OFF && u->focus != LCF_AGAIN && !(usb && u->focus == LCF_REBOOT)) u->focus = LCF_OFF;
    if (e->type == UIEV_KEY && dir_of(e))
        u->focus = u->focus == LCF_OFF ? (usb ? LCF_REBOOT : LCF_AGAIN) : u->focus == LCF_REBOOT ? LCF_AGAIN : LCF_OFF;
    else if ((e->type == UIEV_KEY && e->key == KEYSYM_ENTER && u->focus == LCF_OFF) || id == HIT_LC_OFF) emit_line("lc-poweroff");
    else if (usb && ((e->type == UIEV_KEY && e->key == KEYSYM_ENTER && u->focus == LCF_REBOOT) || id == HIT_LC_REBOOT)) emit_line("lc-reboot");
    else if ((e->type == UIEV_KEY && e->key == KEYSYM_ENTER) || id == HIT_LC_AGAIN) { u->focus = LCF_BAY; emit_line("lc-again"); }
    return 1;
}

int lc_handle(App *a, const Event *e) {
    if (a->screen == SCREEN_LOCAL && a->st.lc.screen == LC_SCREEN_USBSET) {   /* #1914: the DCUI's tokens, its own way */
        char tok[48], line[64], keys[192];
        /* #2001: Alt+Shift, or a click on the chip, switches the language of the name field
         * (#1245's a->lang_he) -- no token: a Hebrew letter is sent as its code point */
        if (e->type == UIEV_KEY && e->key == KEYSYM_LAYOUT) { a->lang_he = !a->lang_he; return 1; }
        if (e->type == UIEV_CLICK && dcui_click(e->x, e->y, keys, sizeof keys)) {   /* #1959: the keys it stands for */
            for (char *k = strtok(keys, "\n"); k; k = strtok(NULL, "\n")) {
                if (!strcmp(k, "KEY LAYOUT")) { a->lang_he = !a->lang_he; continue; }
                snprintf(line, sizeof line, "lc-usbset|%s", k); emit_line(line);
            }
        } else if (e->type == UIEV_CHAR && a->lang_he && e->he != e->ch && e->he >= 0x20) {
            snprintf(line, sizeof line, "lc-usbset|CHAR %x", e->he); emit_line(line);   /* SI-1452 (input.c) */
        } else if (dcui_token(e, 0, tok, sizeof tok)) { snprintf(line, sizeof line, "lc-usbset|%s", tok); emit_line(line); }
        return 1;
    }
    if (a->screen == SCREEN_LCBOOT) return boot_handle(a, e);
    if (a->screen == SCREEN_RESTORE && lc_pull_active(a)) return lc_pull_handle(a, e);   /* 16/10 (lc_pull.c) */
    if (a->screen != SCREEN_LOCAL) return 0;
    if (e->type == UIEV_MOVE) return 0;            /* the pointer still moves */
    return local_handle(a, e);
}
