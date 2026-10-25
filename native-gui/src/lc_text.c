/* #1322: the local clone's words -- amounts, a disk's label and contents,
 * the status bar -- and the --png scenario (the approved mockup's: three
 * drawers, drawer 3 the source). */
#include <math.h>
#include <stdio.h>
#include <string.h>
#include "lcui.h"
#include "screens_rounds.h"
#include "smartdlg.h"   /* #2017: the orange source's notice card */

/* #1437: a share as the screen says it -- floored, so 100% is only the end,
 * and one decimal under 10%: 1 GB of 500 is "0.2%", not "0%". */
void lc_pct(char *out, size_t n, double share) {
    double p = (share < 0 ? 0 : share > 1 ? 1 : share) * 100;
    if (p < 10) snprintf(out, n, "%.1f%%", floor(p * 10 + 1e-9) / 10);
    else snprintf(out, n, "%.0f%%", floor(p + 1e-9));
}

/* #1437: one (elapsed, copied) sample per reload. Time going back is a new
 * run: the window starts over. Bytes going back is a reading between two of
 * the agent's counter files (bytes.<idx>): skipped, the window is kept.
 * Nothing before the first byte is a sample -- the preparation (measure,
 * shrink, the tables) is not copying, and averaging it in made the first
 * minutes say 444 minutes. One sample at least LC_RATE_WIN s old is kept. */
void lc_rate_sample(LcUi *u, int elapsed, long long copied) {
    if (u->rate_n && elapsed < u->rate_e[u->rate_n - 1]) u->rate_n = 0;
    if (copied <= 0 || (u->rate_n && copied < u->rate_c[u->rate_n - 1])) return;
    if (u->rate_n && elapsed == u->rate_e[u->rate_n - 1]) { u->rate_c[u->rate_n - 1] = copied; return; }
    int drop = 0;
    while (u->rate_n - drop >= 2 && elapsed - u->rate_e[drop + 1] >= LC_RATE_WIN) drop++;
    if (u->rate_n - drop >= LC_RATE_MAX) drop++;
    if (drop) {
        memmove(u->rate_e, u->rate_e + drop, (size_t)(u->rate_n - drop) * sizeof u->rate_e[0]);
        memmove(u->rate_c, u->rate_c + drop, (size_t)(u->rate_n - drop) * sizeof u->rate_c[0]);
        u->rate_n -= drop;
    }
    u->rate_e[u->rate_n] = elapsed; u->rate_c[u->rate_n++] = copied;
}

/* bytes/s over the window; *span = the seconds it covers (0 = fewer than two samples) */
double lc_rate(const LcUi *u, int *span) {
    *span = u->rate_n >= 2 ? u->rate_e[u->rate_n - 1] - u->rate_e[0] : 0;
    return *span > 0 ? (double)(u->rate_c[u->rate_n - 1] - u->rate_c[0]) / *span : -1;
}

/* the first partition whose used bytes were not measured (#1440); 0 = all were */
int lc_unmeasured(const LcState *l) {
    for (int i = 0; i < l->nparts; i++) if (l->parts[i].bytes < 0) return l->parts[i].idx;
    return 0;
}

/* An amount as an isolated LTR run (FSI ... PDI): "149 GB" inside a Hebrew
 * sentence stays "149 GB", never "GB 149". */
void lc_gb(char *out, size_t n, double b) {
    double g = b / 1e9;
    if (b < 0) snprintf(out, n, "לא נמדד");
    else if (g >= 100) snprintf(out, n, "\xE2\x81\xA8%.0f GB\xE2\x81\xA9", g);
    else if (g >= 10) snprintf(out, n, "\xE2\x81\xA8%.1f GB\xE2\x81\xA9", g);
    else if (g >= 1) snprintf(out, n, "\xE2\x81\xA8%.2f GB\xE2\x81\xA9", g);
    else snprintf(out, n, "\xE2\x81\xA8%.0f MB\xE2\x81\xA9", b / 1e6);
}

void card_used_text(int mode, long long bytes, char *out, size_t n) {
    const char *lead = mode == USED_NOW ? "תפוס עכשיו" : mode == USED_WRITTEN ? "נכתב" : "נכתב עד כה";
    int none = bytes < 0 || (mode == USED_WRITTEN && bytes == 0);
    char g[64];
    if (none) snprintf(g, sizeof g, "לא נמדד"); else lc_gb(g, sizeof g, (double)bytes);
    snprintf(out, n, RLM "%s: %s", lead, g);
}

/* #1443 (Nadav 17/10: "לפני העתקה אני רוצה מספר מדויק"): the bytes the copy
 * moves, as the agent measured them before it starts -- partclone's used
 * blocks (lcsize.sh), one decimal like partclone's own "203.1 GB". Only for
 * the source the agent measured; not measured says so, with why -- never the
 * inventory's df number under "יועתקו". */
void lc_copy_text(const LcState *l, const LcDisk *src, char *out, size_t n) {
    if (!src || strcmp(l->src, src->dev) || l->src_copy < -1) { snprintf(out, n, "מודד כמה יועתק…"); return; }
    if (l->src_copy < 0) { snprintf(out, n, "כמה יועתק: לא נמדד%s%s", l->src_copy_why[0] ? " — " : "", l->src_copy_why); return; }
    double g = (double)l->src_copy / 1e9;
    if (g >= 1) snprintf(out, n, RLM "\xE2\x81\xA8%.1f GB\xE2\x81\xA9 יועתקו", g);
    else { char u[48]; lc_gb(u, sizeof u, (double)l->src_copy); snprintf(out, n, RLM "%s יועתקו", u); }
}

void lc_disk_card_name(const LcDisk *d, char *out, size_t n) {
    card_name(d->name, eff_drawer(d->drawer, d->port), d->conn, d->port, out, n);   /* #2001: the operator's slot name when it has one */
}

void lc_disk_label(const LcDisk *d, char *out, size_t n) {
    int drawer = eff_drawer(d->drawer, d->port);
    if (drawer > 0 && d->name[0]) snprintf(out, n, "%s", d->name);   /* #2001: the name, as cloner_slot_label */
    else if (drawer > 0) snprintf(out, n, "דיסק %d", drawer);
    else unmapped_label(d->name, d->conn, d->port, d->dev, out, n);
}

void lc_has_text(const LcDisk *d, char *out, size_t n) {
    char used[160], os[96], u[64];
    if (!d->nparts) { snprintf(out, n, "%s", d->why[0] ? d->why : "ריק"); return; }
    /* #1798: a cut write (or a mark that could not be read) -- what is on it is not a Windows to copy */
    if (d->mark[0] && d->why[0]) { snprintf(out, n, "%s", d->why); return; }
    lc_gb(u, sizeof u, (double)d->used);
    bidi_isolate(d->os[0] ? d->os : "בלי מערכת מוכרת", os, sizeof os);
    if (d->used < 0) snprintf(used, sizeof used, "בשימוש: לא נמדד");
    else snprintf(used, sizeof used, "%s בשימוש", u);
    snprintf(out, n, RLM "%s · %d מחיצות · %s", os, d->nparts, used);   /* RLM: Pango's auto-dir does not skip an isolate */
}

static int failed_targets(const App *a) {
    int f = 0;
    for (int i = 0; i < a->st.lc.ntgts; i++) f += !strcmp(a->st.lc.tgts[i].state, "failed");
    return f;
}

/* #1367: the agent's beat heard that a round is open on the server while
 * this machine is here and not in it. The screen says so and what to do;
 * nothing joins by itself -- a person is at the machine. */
const char *lc_round_note(const App *a) {
    if (!a->st.lc.round_open) return NULL;
    if (a->screen == SCREEN_LCBOOT) return "נפתח סבב בשרת. כדי שהמחשב יצטרף אליו — בחרו 'מהשרת'.";
    if (lc_view(a) == LCVIEW_SOURCE) return "נפתח סבב בשרת. כדי שהמחשב יצטרף אליו — 'חזרה' ואז 'מהשרת'.";
    return "נפתח סבב בשרת";
}

/* Nadav 22/10: the source's SMART already counts sectors it could not read (lcraw.sh
 * lc_smart_unread: 197/198, or NVMe media errors). An orange line on the choice, before
 * the copy -- no question, nothing blocked. Only about the source the person chose;
 * SMART not read or 0 = no line, never "0". */
int lc_unread_text(const App *a, char *out, size_t n) {
    const LcState *l = &a->st.lc; int s = lc_source_index(a);
    if (s < 0 || l->unread < 1 || strcmp(l->disks[s].dev, l->unread_dev)) return 0;
    snprintf(out, n, "דיסק המקור כבר נתקל ב-%lld נקודות שלא הצליח לקרוא — ייתכן שבזמן השיכפול תופיע השאלה 'להמשיך?'", l->unread);
    return 1;
}

static const char *lc_status_base(const App *a, Rgb *dot);

const char *lc_status_word(const App *a, Rgb *dot) {
    static char with_round[160]; const char *w = lc_status_base(a, dot);
    /* during the copy and on its result the note rides the status bar (#1367) */
    if (a->screen != SCREEN_LOCAL || lc_view(a) == LCVIEW_SOURCE || !lc_round_note(a)) return w;
    snprintf(with_round, sizeof with_round, "%s · %s", w, lc_round_note(a)); *dot = a->theme->warn;
    return with_round;
}

static const char *lc_status_base(const App *a, Rgb *dot) {
    static char buf[96]; const Theme *t = a->theme; const LcState *l = &a->st.lc;
    *dot = t->led_write;
    if (a->screen == SCREEN_LCBOOT) {
        if (l->boot_paused || a->lcu.pause_sent) { *dot = t->warn; return "ממתין לבחירה"; }
        return "סופר לאחור";
    }
    int view = lc_view(a), nf = failed_targets(a);
    if (view == LCVIEW_SOURCE) { *dot = t->warn; return l->inv_done ? "בוחרים מקור" : "בודק את הדיסקים"; }
    if (view == LCVIEW_DONE) {
        int nw = 0; for (int i = 0; i < l->ntgts; i++) nw += strcmp(l->tgts[i].state, "failed") && l->tgts[i].note[0];
        if (!nf && nw) { *dot = t->warn; return "הסתיים · עם הערה"; }   /* #2009: orange, not green -- the card says why */
        if (!nf) { *dot = t->led_ok; return "הסתיים"; }
        *dot = t->danger;
        if (nf == 1) return "הסתיים · יעד אחד נכשל";
        snprintf(buf, sizeof buf, "הסתיים · %d יעדים נכשלו", nf); return buf;
    }
    if (!strcmp(l->phase, "shrinking")) return "מכין עותק מכווץ בזיכרון";   /* #1477: the source is read only */
    if (!strcmp(l->phase, "verifying")) return "בודק את היעדים";
    if (!strcmp(l->phase, "restoring")) return "מחזיר את המקור לגודלו";
    if (!strcmp(l->phase, "stopped")) { *dot = t->warn; return "נעצר"; }
    int alive = l->ntgts - nf;
    if (nf && alive) { *dot = t->warn; return alive == 1 ? "יעד אחד נכשל — השני ממשיך" : "יעד נכשל — האחרים ממשיכים"; }
    if (alive == 1) return "מעתיק ליעד אחד";
    if (alive == 2) return "מעתיק לשני יעדים";
    snprintf(buf, sizeof buf, "מעתיק ל-%d יעדים", alive); return buf;
}

const char *lc_status_keys(const App *a) {
    if (a->screen == SCREEN_LCBOOT) return a->st.lc.local_ok   /* 16/10: 3 -- restore from the server */
        ? "→ ← ↓  מעבר    Enter  בחירה    1  מהשרת    2  שיכפול מקומי    3  שחזור אימג' מהשרת"
        : "↓ ↑  מעבר    Enter  בחירה    1  מהשרת    3  שחזור אימג' מהשרת";
    int view = lc_view(a);
    if (view == LCVIEW_SOURCE) return a->lcu.confirm ? "Tab / → ←  מעבר    Enter  בחירה    Esc  חזרה"
                                                     : a->st.lc.usb ? "↑ ↓  מגירה    → ←  תפקיד    1 2 3  קבע כמקור    Tab  כפתורים    F4  הגדרות"   /* #1914; mockup 01 */
                                                     : "↑ ↓  מגירה    → ←  תפקיד    1 2 3  קבע כמקור    Tab  כפתורים    Esc  חזרה";
    if (view == LCVIEW_PROGRESS) return a->lcu.stop ? "Tab / → ←  מעבר    Enter  בחירה    Esc  המשך להעתיק" : "Esc  עצירה";
    return "Enter  כיבוי    Tab  מעבר";
}

/* ---- --png: the mockup's scenario ------------------------------------------- */

const char *const LC_PNG_CARDS[] = { "lcboot", "lcboot-paused", "lcsource", "lcsource-measuring", "lcsource-blocked",
                                     "lcsource-shrink", "lcconfirm", "lcconfirm-shrink", "lcprogress", "lcprogress-fail",
                                     "lcstop", "lcdone", "lcdone-ok", "lcboot-round", "lcsource-round",   /* -round: #1367 */
                                     "lcsource-usb", "lcprogress-usb",   /* #2030: the clone stick (lc_usb=1) -- the same drawer cards */
                                     "lcboot-one", "lcboot-pull", "lcpull", "lcpull-confirm", "lcpull-keys",
                                     "lcsource-orange", "lcconfirm-usb", "lcboot-back" };   /* #2079: back from the wait */   /* #2017: an orange source -- the notice, "המשך" alone */   /* 16/10: one disk; restore from the server */
const int LC_PNG_NCARDS = (int)(sizeof LC_PNG_CARDS / sizeof LC_PNG_CARDS[0]);

static void disk(LcState *l, int port, const char *dev, const char *model, unsigned long long size, const char *smart,
                 const char *why, const char *os, int nparts, long long used) {
    LcDisk *d = &l->disks[l->ndisks++];
    d->port = port; snprintf(d->dev, sizeof d->dev, "%s", dev); snprintf(d->model, sizeof d->model, "%s", model);
    d->size = size; snprintf(d->smart, sizeof d->smart, "%s", smart); snprintf(d->smart_reason, sizeof d->smart_reason, "%s", why);
    snprintf(d->os, sizeof d->os, "%s", os); d->nparts = nparts; d->used = used; d->need = (long long)size; d->usable = 1;
}

static void fit(LcState *l, const char *dev, int v, long long need, const char *reason) {
    LcFit *f = &l->fits[l->nfits++];
    snprintf(f->dev, sizeof f->dev, "%s", dev); f->verdict = v; f->need = need; snprintf(f->reason, sizeof f->reason, "%s", reason);
}

static void part(LcState *l, int idx, const char *label, const char *fs, long long bytes, long long size, const char *st, long long in) {
    LcPart *p = &l->parts[l->nparts++];
    p->idx = idx; snprintf(p->label, sizeof p->label, "%s", label); snprintf(p->fs, sizeof p->fs, "%s", fs);
    p->bytes = bytes; p->size = size; snprintf(p->state, sizeof p->state, "%s", st); p->in = in;
}

static void tgt(LcState *l, const char *dev, const char *st, long long bytes, int p, int ok, const char *err) {
    LcTgt *t = &l->tgts[l->ntgts++];
    snprintf(t->dev, sizeof t->dev, "%s", dev); snprintf(t->state, sizeof t->state, "%s", st);
    t->bytes = bytes; t->part = p; t->ok = ok; t->total = 148600000000LL; snprintf(t->error, sizeof t->error, "%s", err);
}

void lc_sample(State *s, const char *card) {
    LcState *l = &s->lc; memset(l, 0, sizeof *l);
    snprintf(s->machine_name, sizeof s->machine_name, "CLONER-02");
    l->round_open = strstr(card, "-round") != NULL;   /* #1367: a round opened on the server meanwhile */
    if (!strncmp(card, "lcpull", 6)) {   /* 16/10: the restore screen with three drawers, two ticked on -confirm */
        l->screen = LC_SCREEN_PULL;
        s->nidisks = 0;
        static const struct { int port; const char *model, *smart; unsigned long long size; } D[3] = {
            { 1, "Samsung SSD 870 EVO", "passed", 500107862016ULL }, { 2, "WDC WD5000AAKX", "fail", 500107862016ULL },
            { 3, "Crucial MX500", "passed", 480103981056ULL } };
        for (int i = 0; i < 3; i++) {
            IdleDisk *d = &s->idisks[s->nidisks++]; memset(d, 0, sizeof *d);
            d->port = D[i].port; d->size = D[i].size;
            snprintf(d->model, sizeof d->model, "%s", D[i].model); snprintf(d->smart, sizeof d->smart, "%s", D[i].smart);
        }
        return;
    }
    if (!strncmp(card, "lcboot", 6)) {
        l->screen = LC_SCREEN_BOOT; l->boot_now = 1000; l->boot_deadline = 1017;
        l->local_ok = strcmp(card, "lcboot-one") != 0;   /* 16/10: one disk -- the local card is off */
        l->boot_paused = !strcmp(card, "lcboot-back") ? 2 : !strcmp(card, "lcboot-paused") || l->round_open;
        return;
    }
    l->screen = LC_SCREEN_LOCAL; l->inv_done = 1; snprintf(l->phase, sizeof l->phase, "idle");
    l->usb = strstr(card, "-usb") != NULL;   /* #2030 */
    disk(l, 1, "sda", "Samsung SSD 870 EVO", 500107862016ULL, "ok", "ok", "Windows 10", 3, 212400000000LL);
    /* #1732 (owner 18/10): a red disk is never a target. The approved mockup's confirm
     * (lcconfirm) writes disk 2 with its SMART alert row and "הוצא את דיסק 2"; the target
     * that still carries that row is orange (a SMART warning), so on that card disk 2 is
     * orange -- the same dialog, the same height; elsewhere it stays red. */
    int keep2 = !strcmp(card, "lcconfirm") || !strcmp(card, "lcconfirm-usb");
    disk(l, 2, "sdb", "WDC WD5000AAKX", 500107862016ULL, keep2 ? "warn" : "fail", "health_failed", "Ubuntu 22.04", 2, 38000000000LL);
    const int orange = !strcmp(card, "lcsource-orange");   /* #2017: 187 > 0 on the source */
    disk(l, 3, "sdc", "Crucial MX500", 480103981056ULL, orange ? "warn" : "ok", orange ? "reported_uncorrectable" : "ok", "Windows 11 Pro", 4, 148600000000LL);
    int blocked = !strcmp(card, "lcsource-blocked"), shrink = !strcmp(card, "lcsource-shrink") || !strcmp(card, "lcconfirm-shrink");
    snprintf(l->src, sizeof l->src, "%s", blocked || shrink ? "sda" : "sdc");
    snprintf(l->src_measure, sizeof l->src_measure, "%s", blocked || shrink ? "done" : "none");
    l->src_copy = blocked || shrink ? 212400000000LL : 148600000000LL;   /* #1443: measured by the agent */
    if (!strcmp(card, "lcsource-measuring")) { snprintf(l->src, sizeof l->src, "sda"); snprintf(l->src_measure, sizeof l->src_measure, "measuring"); l->src_copy = -2; }
    if (blocked) { fit(l, "sdb", LCV_FITS, 500107862016LL, ""); fit(l, "sdc", LCV_SHORT, 490103981056LL, ""); }
    else if (shrink) { fit(l, "sdb", LCV_FITS, 500107862016LL, ""); fit(l, "sdc", LCV_SHRINK, 262400000000LL, ""); }
    else if (!strcmp(card, "lcsource-measuring")) { fit(l, "sdb", LCV_FITS, 500107862016LL, ""); fit(l, "sdc", LCV_MEASURING, 500107862016LL, ""); }
    else { fit(l, "sda", LCV_FITS, 480103981056LL, ""); fit(l, "sdb", LCV_FITS, 480103981056LL, ""); }
    if (strncmp(card, "lcprogress", 10) && strcmp(card, "lcstop") && strncmp(card, "lcdone", 6)) return;
    int fail = !strcmp(card, "lcprogress-fail") || !strcmp(card, "lcdone"), done = !strncmp(card, "lcdone", 6);
    snprintf(l->phase, sizeof l->phase, "%s", done ? "done" : "copying");
    l->has_run = 1; snprintf(l->run_src, sizeof l->run_src, "sdc"); l->total = 148600000000LL;
    l->copied = done ? l->total : fail ? 118400000000LL : 55100000000LL;
    l->elapsed = done ? (fail ? 1065 : 372) : fail ? 728 : 399; l->cur_part = 3;
    snprintf(l->report, sizeof l->report, "%s", done ? "sent" : "");
    long long in3 = l->copied - 32000000LL - 16000000LL;
    part(l, 1, "EFI", "vfat", 32000000LL, 104857600LL, "done", 0);
    part(l, 2, "MSR", "", 16000000LL, 16777216LL, "done", 0);
    part(l, 3, "Windows", "ntfs", 147942000000LL, 446000000000LL, done ? "done" : "copy", done ? 0 : in3);
    part(l, 4, "Recovery", "ntfs", 610000000LL, 650000000LL, done ? "done" : "wait", 0);
    tgt(l, "sda", done ? "done" : "writing", l->copied, done ? 0 : 3, done ? 4 : 2, "");
    if (done) { l->tgts[0].has_table = 1; l->tgts[0].grown_idx = 3; l->tgts[0].grown_bytes = 466000000000LL; l->tgts[0].src_bytes = 446000000000LL; }
    if (!strcmp(card, "lcdone-ok")) return;
    if (fail) tgt(l, "sdb", "failed", 91600000000LL, 3, 2, "מחיצה 3: שגיאת כתיבה אחרי 91.6 GB — fanout: write: Input/output error · partclone rc=1");
    else tgt(l, "sdb", "writing", l->copied, 3, 2, "");
}

int lc_png_setup(App *a, const char *card) {
    if (strncmp(card, "lc", 2)) return 0;
    lc_sample(&a->st, card);
    memset(&a->lcu, 0, sizeof a->lcu);
    a->force_cloner = 1;
    if (!strcmp(card, "lcboot-paused") || !strcmp(card, "lcboot-round")) { a->lcu.boot_focus = 1; a->lcu.pause_sent = 1; }
    if (!strcmp(card, "lcboot-pull")) { a->lcu.boot_focus = 2; a->lcu.pause_sent = 1; }   /* 16/10: the focus on the third card */
    if (a->st.lc.screen == LC_SCREEN_PULL) {             /* 16/10: an image chosen; on -confirm drawers 1 and 3 and the dialog */
        memset(a->pull_sel, 0, sizeof a->pull_sel);
        a->mode = MODE_RESTORE; a->pick_in_folder = 0;
        if (a->st.nimages) a->restore_image_sel = 1;
        a->pull_focus = 0;
        if (!strcmp(card, "lcpull-confirm")) { a->pull_sel[1] = a->pull_sel[3] = 1; a->confirm = CONFIRM_RESTORE; }
        if (!strcmp(card, "lcpull-keys")) { a->pull_sel[1] = 1; a->pull_focus = 3; }   /* the keyboard on drawer 3 */
        return 1;
    }
    if (a->st.lc.screen != LC_SCREEN_LOCAL) return 1;
    lc_after_reload(a);
    LcUi *u = &a->lcu;
    if (a->st.lc.has_run && a->st.lc.elapsed > LC_RATE_WIN) {   /* #1437: a full window at the mockup's own pace */
        const LcState *l = &a->st.lc; long long back = (long long)((double)l->copied * LC_RATE_WIN / l->elapsed);
        u->rate_n = 0; lc_rate_sample(u, l->elapsed - LC_RATE_WIN, l->copied - back); lc_rate_sample(u, l->elapsed, l->copied);
    }
    if (!strcmp(card, "lcsource-blocked") || !strcmp(card, "lcsource-shrink") || !strcmp(card, "lcconfirm-shrink") || !strcmp(card, "lcsource-measuring")) {
        u->nroles = 0;                     /* drawer 1 chosen as the source by the person */
        const char *dev[3] = { "sda", "sdb", "sdc" }; int r[3] = { LCR_SOURCE, LCR_TARGET, LCR_TARGET };
        for (int i = 0; i < 3; i++) { snprintf(u->role_dev[i], sizeof u->role_dev[i], "%s", dev[i]); u->role[i] = r[i]; }
        u->nroles = 3; u->bay = 0;
    }
    if (!strcmp(card, "lcconfirm") || !strcmp(card, "lcconfirm-shrink") || !strcmp(card, "lcconfirm-usb")) {
        for (int i = 0; i < u->nroles; i++) if (!strcmp(u->role_dev[i], "sdb")) u->role[i] = LCR_TARGET;   /* the person kept disk 2 */
        u->confirm = 1; u->focus = LCF_CANCEL;
        if (!strcmp(card, "lcconfirm-usb")) { a->st.lc.usb = 1; u->focus = LCF_GO; }   /* #2008 (Nadav 22/10): the stick opens on "מחק והעתק" */
    }
    if (!strcmp(card, "lcprogress") || !strcmp(card, "lcprogress-fail")) u->focus = LCF_STOP;
    if (!strcmp(card, "lcstop")) { u->stop = 1; u->focus = LCF_STOP_NO; }
    if (!strncmp(card, "lcdone", 6)) u->focus = LCF_OFF;
    if (!strcmp(card, "lcsource-orange")) {   /* #2017: "המשך לאישור" pressed on the orange source (drawer 3) */
        const LcDisk *d = &a->st.lc.disks[2]; char label[48]; lc_disk_label(d, label, sizeof label);
        snprintf(a->st.lc.disks[2].serial, sizeof a->st.lc.disks[2].serial, "2236E6A1B2C3");
        sdlg_open(a, SDLG_LC_SOURCE, 2, d->dev, label, d->smart, d->smart_reason, d->model, d->serial);
    }
    return 1;
}
