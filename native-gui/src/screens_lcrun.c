/* #1322: the local clone running and done -- mockup states 4 and 5
 * (localclone.html .ptop/.mets/.parts/.tg and .res). Every number is what
 * the agent measured (lcgui.sh lc_run/lc_part/lc_tgt): the bytes read from
 * the source, the bytes each target took, the partitions whose xxh64 came
 * back equal. Speed and time left are those bytes over the time passed --
 * there is no estimate before the copy starts. */
#include <math.h>
#include <stdio.h>
#include <string.h>
#include "lcui.h"
#include "screens_rounds.h"

static const LcTgt *tgt_of(const App *a, int i) { return &a->st.lc.tgts[i]; }
static int tfailed(const LcTgt *t) { return !strcmp(t->state, "failed"); }

static void mmss(int s, char *out, size_t n) {
    if (s < 0) s = 0;
    if (s >= 3600) snprintf(out, n, "%d:%02d:%02d", s / 3600, s / 60 % 60, s % 60);
    else snprintf(out, n, "%02d:%02d", s / 60, s % 60);
}

/* #1437/#1440: the time left only from a total that was measured and a full
 * window of the copy's own pace; otherwise it says which of those is missing. */
static void left_text(const LcState *l, int um, double rate, int span, char *out, size_t n) {
    if (um) snprintf(out, n, "הזמן שנותר לא ידוע — מחיצה %d לא נמדדה", um);
    else if (l->total < 0) snprintf(out, n, "הזמן שנותר לא ידוע — הסך לא נמדד");
    else if (span < LC_RATE_WIN) snprintf(out, n, "מחשב את הזמן שנותר…");
    else if (rate <= 0) snprintf(out, n, "הזמן שנותר לא ידוע — ההעתקה לא מתקדמת");
    else {
        double secs = ((double)l->total - (double)l->copied) / rate;
        if (secs < 60) snprintf(out, n, "פחות מדקה נותרה");
        else if (secs < 90) snprintf(out, n, "פחות מ-2 דק' נותרו");
        else snprintf(out, n, "כ-%.0f דק' נותרו", secs / 60);
    }
}

static const LcDisk *disk_of(const App *a, const char *dev) { return lc_disk_by_dev(a, dev); }

static void label_dev(const App *a, const char *dev, char *out, size_t n) {
    const LcDisk *d = disk_of(a, dev); if (d) lc_disk_label(d, out, n); else snprintf(out, n, "%s", dev);
}

#define met lc_met   /* lcbay.c: shared with the room screens (17/10) */

/* a partition's weight in the row: its used bytes, or its size when they were not measured (#1440) */
static long long layout_bytes(const LcPart *p) { return p->bytes >= 0 ? p->bytes : p->size; }

static void part_box(App *a, cairo_t *cr, Rect r, const LcPart *p) {
    const Theme *t = a->theme; int done = !strcmp(p->state, "done"), now = !strcmp(p->state, "copy"), bad = !strcmp(p->state, "failed");
    draw_fill_rrect(cr, r, 4, bad ? t->danger_soft : t->surface);
    draw_border_rrect(cr, r, 4, now ? t->indigo : bad ? t->danger : t->hair, bad ? 3 : 1);
    double share = done ? 1 : now && p->bytes > 0 ? fmin(1, (double)p->in / (double)p->bytes) : 0;
    char head[64], st[48], d[160], b1[48], b2[48];
    snprintf(head, sizeof head, "%d · %s", p->idx, p->label);
    if (done) snprintf(st, sizeof st, "✓ הועתקה");
    else if (now && p->bytes < 0) lc_gb(st, sizeof st, (double)p->in);   /* #1440: no share of an unmeasured whole */
    else if (now) lc_pct(st, sizeof st, share);
    else if (bad) snprintf(st, sizeof st, "נכשלה"); else snprintf(st, sizeof st, "ממתינה");
    lc_gb(b1, sizeof b1, (double)p->bytes); lc_gb(b2, sizeof b2, (double)p->size);
    const char *fs = !strcmp(p->fs, "vfat") ? "FAT32" : !strcmp(p->fs, "ntfs") ? "NTFS" : p->fs[0] && strcmp(p->fs, "unknown") ? p->fs : NULL;
    if (!strcmp(p->fs, "swap")) snprintf(d, sizeof d, "swap · נוצרת מחדש");
    else if (!fs) snprintf(d, sizeof d, "ללא מ״ק · %s · מועתקת מלאה", b2);
    else if (p->bytes < 0) snprintf(d, sizeof d, "%s · %s · בשימוש: לא נמדד", fs, b2);   /* #1440 */
    else if (p->bytes < p->size) snprintf(d, sizeof d, "%s · %s בשימוש מתוך %s", fs, b1, b2);
    else snprintf(d, sizeof d, "%s · %s בשימוש", fs, b1);
    Text th = text_make(cr, FONT_SANS, 12, 600, head, 0, DIR_RTL), ts = text_make(cr, FONT_SANS, 12, 500, st, 0, DIR_RTL);
    Text td = text_make(cr, FONT_SANS, 11, 400, d, (int)(r.w - 24), DIR_RTL);
    pango_layout_set_ellipsize(td.layout, PANGO_ELLIPSIZE_END); pango_layout_set_height(td.layout, 0);
    double right = r.x + r.w - 12;
    text_draw_r(cr, &th, right, r.y + 8, t->ink); text_draw(cr, &ts, r.x + 12, r.y + 8, t->muted);
    text_draw_r(cr, &td, right, r.y + 8 + th.h + 2, t->muted);
    Rect bar = { r.x + 12, r.y + r.h - 9 - 4, r.w - 24, 4 };
    draw_fill_rrect(cr, bar, 2, t->track);
    if (share > 0) draw_fill_rrect(cr, (Rect){ bar.x + bar.w * (1 - share), bar.y, bar.w * share, 4 }, 2, done ? t->led_ok : t->led_write);
    text_free(&th); text_free(&ts); text_free(&td);
}

/* .tg .bay: one target while it is written -- drawn by lcbay.c, the row
 * the room screens share since 17/10 */
static void tbay(App *a, cairo_t *cr, Rect b, const LcTgt *g) {
    const LcDisk *d = disk_of(a, g->dev);
    int bad = tfailed(g), ok = !strcmp(g->state, "done");
    LcBay y; memset(&y, 0, sizeof y);
    y.kind = bad ? BAY_FAILED : ok ? BAY_DONE : BAY_WRITING;
    y.drawer = d ? eff_drawer(d->drawer, d->port) : 0;
    /* #2030: the connection (the big number is the drawer), the size and model -- never the device name -- and what was written */
    if (d) lc_disk_card_name(d, y.name, sizeof y.name); else snprintf(y.name, sizeof y.name, "%s", "חיבור לא מזוהה");
    char sz[32];
    if (d) { disk_size(d->size, sz, sizeof sz); char mi[96]; bidi_isolate(d->model[0] ? d->model : "—", mi, sizeof mi); snprintf(y.sub, sizeof y.sub, "%s · %s", sz, mi); }
    else snprintf(y.sub, sizeof y.sub, "%s", "גודל הדיסק לא נקרא");
    card_used_text(ok ? USED_WRITTEN : USED_SO_FAR, (long long)g->bytes, y.used, sizeof y.used);
    y.pill = bad ? "נכשל" : ok ? (g->rescued > 0 ? "לא זהה למקור" : g->note[0] ? "הושלם" : "זהה למקור") : NULL;   /* #2017, #2009 */
    y.done_warn = ok && (g->note[0] || g->rescued > 0);   /* #2009: orange, not green; #2017: zeros where the source did not read */
    double share = g->total > 0 ? fmin(1, (double)g->bytes / (double)g->total) : 0;
    if (ok) share = 1;
    y.share = share;
    if (ok || g->total > 0) lc_pct(y.pct, sizeof y.pct, share);
    else snprintf(y.pct, sizeof y.pct, "%.1f GB", (double)g->bytes / 1e9);   /* #1440: the total was not measured */
    const LcPart *cp = NULL; for (int i = 0; i < a->st.lc.nparts; i++) if (a->st.lc.parts[i].idx == g->part) cp = &a->st.lc.parts[i];
    if (bad) snprintf(y.line, sizeof y.line, "נכשל: %s", g->error);
    else if (ok && g->note[0]) snprintf(y.line, sizeof y.line, "%s", g->note);   /* #2009 */
    else if (ok) snprintf(y.line, sizeof y.line, "הושלם · נבדק");
    else if (!strcmp(g->state, "verifying")) snprintf(y.line, sizeof y.line, "בודק את טבלת המחיצות שנכתבה…");
    else if (cp) snprintf(y.line, sizeof y.line, "כותב מחיצה %d · %s · xxh64 תואם ב-%d מתוך %d", cp->idx, cp->label, g->ok, a->st.lc.nparts);
    else snprintf(y.line, sizeof y.line, "מכין את טבלת המחיצות…");
    lc_bay_draw(a, cr, b, &y);
}

void lc_draw_progress(App *a, cairo_t *cr, double W, double H, double head_h) {
    const Theme *t = a->theme; const LcState *l = &a->st.lc;
    double cw = fmin(980, W - 48), x0 = (W - cw) / 2, xr = x0 + cw, y = head_h + 22;
    char sl[LC_LABEL_MAX], lb[300], h1[200], s2[200]; const LcDisk *src = disk_of(a, l->run_src);
    label_dev(a, l->run_src, sl, sizeof sl);
    char mi[96]; bidi_isolate(src && src->model[0] ? src->model : l->run_src, mi, sizeof mi);
    snprintf(lb, sizeof lb, "שיכפול מקומי · מקור: %s (%s)", sl, mi);
    int nf = 0, alive = 0; for (int i = 0; i < l->ntgts; i++) { if (tfailed(tgt_of(a, i))) nf++; else alive++; }
    char names[160] = ""; int k = 0;
    for (int i = 0; i < l->ntgts; i++) if (!tfailed(tgt_of(a, i))) { char n2[48]; label_dev(a, l->tgts[i].dev, n2, sizeof n2);
        k += snprintf(names + k, sizeof names - k > 0 ? sizeof names - k : 0, "%s%s", k ? "ול" : "ל", n2); }
    if (!l->has_run) { snprintf(h1, sizeof h1, "מתחיל…"); snprintf(s2, sizeof s2, "בודק שוב את המקור ואת היעדים לפני הכתיבה."); }
    /* #1477: a local clone shrinks only a RAM view (#1466) -- the source is never written, on either path */
    else if (!strcmp(l->phase, "shrinking")) { snprintf(h1, sizeof h1, "מכין עותק מכווץ בזיכרון…"); snprintf(s2, sizeof s2, "המקור נקרא בלבד ולא נכתב."); }
    else if (!strcmp(l->phase, "verifying")) { snprintf(h1, sizeof h1, "בודק את היעדים…"); snprintf(s2, sizeof s2, "טבלת המחיצות נקראת בחזרה מכל יעד."); }
    else if (!strcmp(l->phase, "restoring")) { snprintf(h1, sizeof h1, "מחזיר את המקור לגודלו…"); snprintf(s2, sizeof s2, "ההעתקה הסתיימה."); }
    else if (!strcmp(l->phase, "stopped")) { snprintf(h1, sizeof h1, "השיכפול נעצר"); snprintf(s2, sizeof s2, "היעדים כתובים בחלקם ואינם שמישים."); }
    else if (nf && alive) { snprintf(h1, sizeof h1, "ממשיך להעתיק %s", names);
        char fn[48] = ""; for (int i = 0; i < l->ntgts; i++) if (tfailed(tgt_of(a, i))) label_dev(a, l->tgts[i].dev, fn, sizeof fn);
        snprintf(s2, sizeof s2, "%s נכשל ונעצר. השאר לא הושפעו.", fn); }
    else if (!alive) { snprintf(h1, sizeof h1, "כל היעדים נכשלו"); snprintf(s2, sizeof s2, "המקור לא שונה."); }
    else { snprintf(h1, sizeof h1, "מעתיק %s%s", names, alive > 1 ? " במקביל" : ""); snprintf(s2, sizeof s2, "המקור נקרא פעם אחת ונכתב לכל היעדים יחד."); }
    /* #1440: one partition not measured and the whole is not known -- no percent of it, no time left */
    int um = lc_unmeasured(l), whole = !um && l->total > 0, span;
    double rate = lc_rate(&a->lcu, &span);   /* #1437: the window, not the average since the start */
    char big[24], lt[96];
    if (whole) lc_pct(big, sizeof big, (double)l->copied / l->total); else snprintf(big, sizeof big, "—");
    left_text(l, um, rate, span, lt, sizeof lt);
    y += lc_hero(a, cr, x0, xr, y, lb, h1, s2, big, lt, 0) + 16;   /* lcbay.c */
    /* .mets -- speed (right), copied, time passed (left) */
    double mw = (cw - 24) / 3, mh = 80;
    char v1[48], v2[160], v3[32], c1[48], c2[48], hint[160] = "", ch[96] = "בלוקים בשימוש בלבד";
    if (span >= LC_RATE_MIN && rate >= 0) snprintf(v1, sizeof v1, "%.0f MB/s", rate / 1e6); else snprintf(v1, sizeof v1, "עוד לא נמדדה");
    double gc = l->copied / 1e9, gt = whole ? l->total / 1e9 : gc;
    snprintf(c1, sizeof c1, gt >= 100 ? "%.0f" : gt >= 10 ? "%.1f" : "%.2f", gc); snprintf(c2, sizeof c2, gt >= 100 ? "%.0f" : gt >= 10 ? "%.1f" : "%.2f", gt);
    if (whole) snprintf(v2, sizeof v2, "%s / %s GB", c1, c2); else snprintf(v2, sizeof v2, "%s GB", c1);
    if (um) snprintf(ch, sizeof ch, "הסך לא ידוע — מחיצה %d לא נמדדה", um);
    else if (!whole) snprintf(ch, sizeof ch, "הסך לא ידוע — לא נמדד");
    mmss(l->elapsed, v3, sizeof v3);
    if (nf && alive) snprintf(hint, sizeof hint, "היעד שנכשל יצא מהזרם — הוא כבר לא מאט אותו");
    met(a, cr, (Rect){ xr - mw, y, mw, mh }, "מהירות", v1, hint, t->led_ok);
    met(a, cr, (Rect){ xr - 2 * mw - 12, y, mw, mh }, "הועתקו", v2, ch, um || !whole ? t->warn : t->muted);
    met(a, cr, (Rect){ x0, y, mw, mh }, "זמן שעבר", v3, "", t->muted);
    y += mh + 12;
    /* .parts -- the biggest partition gets 2.4 shares, as the mockup's Windows */
    if (l->nparts) {
        int bigi = 0; for (int i = 1; i < l->nparts; i++) if (layout_bytes(&l->parts[i]) > layout_bytes(&l->parts[bigi])) bigi = i;
        double shares = l->nparts - 1 + 2.4, unit = (cw - 6 * (l->nparts - 1)) / shares, px = xr;
        for (int i = 0; i < l->nparts; i++) {
            double w = unit * (i == bigi ? 2.4 : 1);
            part_box(a, cr, (Rect){ px - w, y, w, 54 }, &l->parts[i]); px -= w + 6;
        }
        y += 54 + 12;
    }
    /* the targets */
    int n = l->ntgts; double bh = 96, room = H - N_STATUS_H - 16 - 40 - 12 - y - (10 + 16 + 8 + 10) - (n > 1 ? (n - 1) * 10 : 0);
    if (n && n * bh > room) bh = fmax(64, floor(room / n));
    Rect cage = { x0, y, cw, 10 + 16 + 8 + n * bh + (n > 1 ? (n - 1) * 10 : 0) + 10 };
    char cnt[64]; snprintf(cnt, sizeof cnt, "%d מתוך %d כותבים", !strcmp(l->phase, "done") ? 0 : alive, n);
    double by = lc_cage(a, cr, cage, "יעדים", cnt);   /* lcbay.c */
    for (int i = 0; i < n; i++) { tbay(a, cr, (Rect){ x0 + 10, by, cw - 20, bh }, &l->tgts[i]); by += bh + 10; }
    y = cage.y + cage.h + 12;
    int live = !strcmp(l->phase, "copying") || !strcmp(l->phase, "shrinking");
    lc_btn(a, cr, x0 + lc_btn_width(cr, "עצור", "Esc"), y, 40, "עצור", "Esc", live ? BTN_PLAIN : BTN_OFF, HIT_LC_STOP, live && !a->lcu.stop && a->lcu.focus == LCF_STOP);
    Text fm = text_make(cr, FONT_SANS, 13, 400, "כשל ביעד אחד לא עוצר את האחרים.", 0, DIR_RTL);
    text_draw_r(cr, &fm, xr, y + 20 - fm.h / 2, t->muted); text_free(&fm);
    if (a->lcu.stop && live) lc_draw_stop(a, cr, W, H);
}

void lc_draw_stop(App *a, cairo_t *cr, double W, double H) {
    double w = fmin(560, W - 48);
    const char *lead = a->st.lc.shrunk ? "היעדים יישארו כתובים בחלקם, ולא יהיה אפשר להפעיל מהם מחשב. המקור יוחזר לגודלו. אפשר לשכפל שוב אחר כך."
                                       : "היעדים יישארו כתובים בחלקם, ולא יהיה אפשר להפעיל מהם מחשב. המקור לא ישתנה. אפשר לשכפל שוב אחר כך.";
    Text th = text_make(cr, FONT_SANS, 22, 600, "לעצור את השיכפול?", (int)(w - 98), DIR_RTL), tl = text_make(cr, FONT_SANS, 14, 400, lead, (int)(w - 98), DIR_RTL);
    double h = 26 + fmax(30, th.h) + 6 + tl.h + 16 + 4 + 44 + 24; text_free(&th); text_free(&tl);
    Rect box = lc_dialog_box(a, cr, W, H, w, h);
    double y = box.y + 26, right = box.x + box.w - 28;
    lc_dialog_head(a, cr, box, "לעצור את השיכפול?", lead, &y);
    y += 4;
    double gw = lc_btn(a, cr, right, y, 44, "עצור את השיכפול", NULL, BTN_DANGER, HIT_LC_STOP_GO, a->lcu.focus == LCF_STOP_GO);
    lc_btn(a, cr, right - gw - 10, y, 44, "המשך להעתיק", "Esc", BTN_PLAIN, HIT_LC_STOP_NO, a->lcu.focus == LCF_STOP_NO);
}

/* ---- the result ----------------------------------------------------------------------- */

static double res_card(App *a, cairo_t *cr, double x, double y, double w, const LcTgt *g, int draw) {
    const Theme *t = a->theme; const LcDisk *d = disk_of(a, g->dev); int bad = tfailed(g);
    char name[LC_LABEL_MAX], h3[300], mono[160], li[6][460]; int mk[6], n = 0;
    label_dev(a, g->dev, name, sizeof name);
    int red = 0; const char *why = end_problem_of(&a->st, g->dev, &red);   /* #2024: this target holds the end */
    if (d) { char sata[24]; conn_text(d->conn, d->port, d->dev, sata, sizeof sata);
        snprintf(mono, sizeof mono, "%s · %s", d->model[0] ? d->model : d->dev, sata); } else snprintf(mono, sizeof mono, "%s", g->dev);
    if (!bad) {
        char gb[48]; lc_gb(gb, sizeof gb, (double)g->grown_bytes);
        if (g->rescued > 0) snprintf(h3, sizeof h3, "%s · הועתק במצב הצלה — לא זהה למקור", name);   /* #2017: zeros where the source did not read */
        else if (g->has_table && g->grown_idx > 0) snprintf(h3, sizeof h3, "%s · זהה למקור · המחיצה האחרונה הוגדלה ל-%s", name, gb);
        else snprintf(h3, sizeof h3, "%s · זהה למקור", name);
        if (why) snprintf(h3, sizeof h3, "%s · הושלם, עם בעיה", name);   /* #2024: not "identical" over its reason */
        if (g->note[0]) { snprintf(li[n], sizeof li[n], "%s", g->note); mk[n++] = 2; }   /* #2009: first -- why it is orange */
        snprintf(li[n], sizeof li[n], "%d מחיצות הועתקו · xxh64 של כל מחיצה תואם למה שנקרא מהמקור", g->ok); mk[n++] = 1;
        snprintf(li[n], sizeof li[n], "טבלת המחיצות נקראה בחזרה מהדיסק — זהה למקור, כולל מזהי GUID"); mk[n++] = 1;
        if (g->ntfs_boot > 0) {   /* #1487: partclone -r does not write it; the agent copies and reads it back */
            if (g->ntfs_boot == 1) snprintf(li[n], sizeof li[n], "סקטור אתחול גיבוי נכתב ואומת (מחיצת NTFS אחת)");
            else snprintf(li[n], sizeof li[n], "סקטור אתחול גיבוי נכתב ואומת (%d מחיצות NTFS)", g->ntfs_boot);
            mk[n++] = 1;
        }
        if (g->has_table && g->grown_idx > 0) {
            char was[48]; lc_gb(was, sizeof was, (double)g->src_bytes);
            snprintf(li[n], sizeof li[n], "מחיצה %d הוגדלה מ-%s ל-%s, עד סוף הדיסק; מה שאחריה הועבר לסוף, בגודלו", g->grown_idx, was, gb); mk[n++] = 0;
        } else if (g->has_table && g->free_bytes > 500000000LL) {
            char fr[48]; lc_gb(fr, sizeof fr, (double)g->free_bytes);
            if (g->note[0]) snprintf(li[n], sizeof li[n], "%s בסוף הדיסק נשארו לא מוקצים — טבלת המחיצות זהה למקור", fr);   /* #2009: why -- the note above */
            else if (g->raw_parts > 0) snprintf(li[n], sizeof li[n], "%s בסוף הדיסק נשארו לא מוקצים — מחיצה שהועתקה גולמית אינה מוגדלת", fr);   /* #2017 */
            else snprintf(li[n], sizeof li[n], "%s בסוף הדיסק נשארו לא מוקצים (פחות מ-1 GiB מעל המקור — אין הגדלה)", fr);
            mk[n++] = 0;
        }
        if (g->error[0]) { snprintf(li[n], sizeof li[n], "%s", g->error); mk[n++] = 2; }   /* the grow was deferred (finish_grow) */
        if (why && strcmp(why, g->error) && strcmp(why, g->note)) { snprintf(li[n], sizeof li[n], "%s", why); mk[n++] = red ? -1 : 2; }
    } else {
        snprintf(h3, sizeof h3, "%s · נכשל", name);
        if (g->ok > 0) { snprintf(li[n], sizeof li[n], g->ok == 1 ? "מחיצה אחת הועתקה ונבדקה" : "%d מחיצות הועתקו ונבדקו", g->ok); mk[n++] = 1; }
        snprintf(li[n], sizeof li[n], "%s", g->error); mk[n++] = -1;
        if (why && strcmp(why, g->error)) { snprintf(li[n], sizeof li[n], "%s", why); mk[n++] = -1; }
    }
    double inner = w - 44 - 54 - 18;
    Text th = text_make(cr, FONT_SANS, 20, 600, h3, (int)(inner - 200), DIR_RTL), tm = text_make(cr, FONT_MONO, 13, 500, mono, 0, DIR_LTR);
    Text tl[6]; double h = 18 + fmax(th.h, tm.h) + 8;
    for (int i = 0; i < n; i++) { tl[i] = text_make(cr, FONT_SANS, 13, 400, li[i], (int)(inner - 22), DIR_RTL); h += tl[i].h + (i ? 4 : 0); }
    const char *todo = !bad ? NULL : g->pulled ? "הדיסק נשלף באמצע — לא נכתב עד הסוף. אפשר לשכפל אליו שוב."   /* #2045: not "broken" */
                                 : g->bytes > 0 || g->part > 0 ? "הדיסק לא יעלה ואינו שמיש. אל תכניסו אותו למחשב בכיתה — בדקו או החליפו אותו ושכפלו שוב."
                                                                 : "הדיסק לא נכתב — מה שהיה עליו נשאר.";
    Text tt = todo ? text_make(cr, FONT_SANS, 13, 400, todo, (int)(inner - 24), DIR_RTL) : (Text){ NULL, 0, 0 };
    if (todo) h += 8 + tt.h + 16;
    h += 18;
    if (draw) {
        Rect r = { x, y, w, h };
        const int wn = !bad && g->note[0];   /* #2009: done, orange -- never the green of a clean one */
        Rgb ac = bad ? t->danger : wn ? t->warn : t->led_ok, acs = bad ? t->danger_soft : wn ? t->warning_soft : t->success_soft;
        Rgb hc = red ? t->danger : t->warn;   /* #2024: a target that holds the end -- tinted, a thick edge */
        draw_fill_rrect(cr, r, 6, why ? (red ? t->danger_soft : t->warning_soft) : acs);
        draw_border_rrect(cr, r, 6, why ? hc : ac, 3);   /* #2050: all four edges, no side tab */
        double right = x + w - 22, icx = right - 27, icy = y + 18 + 27;
        cairo_arc(cr, icx, icy, 27, 0, 2 * M_PI); draw_set(cr, acs); cairo_fill_preserve(cr);
        cairo_set_line_width(cr, 1); draw_set(cr, ac); cairo_stroke(cr);
        cairo_set_line_width(cr, 3); cairo_set_line_cap(cr, CAIRO_LINE_CAP_ROUND); cairo_set_line_join(cr, CAIRO_LINE_JOIN_ROUND);
        if (bad) { cairo_move_to(cr, icx - 6, icy - 6); cairo_line_to(cr, icx + 6, icy + 6); cairo_move_to(cr, icx + 6, icy - 6); cairo_line_to(cr, icx - 6, icy + 6); }
        else { cairo_move_to(cr, icx - 8, icy + .5); cairo_line_to(cr, icx - 2.5, icy + 6); cairo_line_to(cr, icx + 8, icy - 5); }
        cairo_stroke(cr);
        double tx = right - 54 - 18, ty = y + 18;
        text_draw_r(cr, &th, tx, ty, t->ink); text_draw_r(cr, &tm, tx - th.w - 10, ty + (th.h - tm.h) / 2, t->muted);
        ty += fmax(th.h, tm.h) + 8;
        for (int i = 0; i < n; i++) {
            /* the marks are drawn: the font has no ✕, and a box is not an X */
            double mx = tx - 8, my = ty + 9; cairo_set_line_width(cr, 2);
            if (mk[i] == 1) { draw_set(cr, t->led_ok); cairo_move_to(cr, mx - 5, my); cairo_line_to(cr, mx - 2, my + 3); cairo_line_to(cr, mx + 4, my - 4); cairo_stroke(cr); }
            else if (mk[i] == -1) { draw_set(cr, t->danger); cairo_move_to(cr, mx - 4, my - 4); cairo_line_to(cr, mx + 4, my + 4); cairo_move_to(cr, mx + 4, my - 4); cairo_line_to(cr, mx - 4, my + 4); cairo_stroke(cr); }
            else { cairo_arc(cr, mx, my, 1.6, 0, 2 * M_PI); draw_set(cr, mk[i] == 2 ? t->warn : t->muted); cairo_fill(cr); }
            text_draw_r(cr, &tl[i], tx - 22, ty, mk[i] == 2 ? t->warn : t->ink); ty += tl[i].h + 4;
        }
        if (todo) { Rect tr = { x + 22, ty + 4, tx - x - 22, tt.h + 16 };
            draw_fill_rrect(cr, tr, 3, t->danger_soft); text_draw_r(cr, &tt, tx - 12, tr.y + 8, t->ink); }
    }
    text_free(&th); text_free(&tm); for (int i = 0; i < n; i++) text_free(&tl[i]);
    if (tt.layout) text_free(&tt);
    return h;
}

void lc_draw_done(App *a, cairo_t *cr, double W, double H, double head_h) {
    (void)H;
    const Theme *t = a->theme; const LcState *l = &a->st.lc;
    double cw = fmin(980, W - 48), x0 = (W - cw) / 2, xr = x0 + cw, y = head_h + 22;
    int nf = 0, nw = 0; for (int i = 0; i < l->ntgts; i++) { nf += tfailed(tgt_of(a, i)); nw += !tfailed(tgt_of(a, i)) && tgt_of(a, i)->note[0]; }
    char h1[160], p[400], sl[LC_LABEL_MAX], tot[48], el[32], mi[96];
    if (!nf && nw) snprintf(h1, sizeof h1, "השיכפול הסתיים — שימו לב להערה בכתום");   /* #2009 */
    else if (!nf) snprintf(h1, sizeof h1, "השיכפול הסתיים בהצלחה");
    else if (nf == l->ntgts) snprintf(h1, sizeof h1, "השיכפול נכשל — אף יעד לא הושלם");
    else snprintf(h1, sizeof h1, "השיכפול הסתיים — %d מתוך %d הצליחו", l->ntgts - nf, l->ntgts);
    const LcDisk *src = disk_of(a, l->run_src); label_dev(a, l->run_src, sl, sizeof sl);
    lc_gb(tot, sizeof tot, (double)(l->total >= 0 ? l->total : l->copied)); mmss(l->elapsed, el, sizeof el);   /* #1440: what was read */
    bidi_isolate(src && src->model[0] ? src->model : l->run_src, mi, sizeof mi);
    snprintf(p, sizeof p, RLM "%s · %s דקות · מקור: %s (%s)", tot, el, sl, mi);
    Text th = text_make(cr, FONT_SANS, 26, 600, h1, 0, DIR_RTL), tp = text_make(cr, FONT_SANS, 14, 400, p, 0, DIR_RTL);
    text_draw_r(cr, &th, xr, y, t->ink); y += th.h + 4; text_draw_r(cr, &tp, xr, y, t->muted); y += tp.h + 16;
    text_free(&th); text_free(&tp);
    for (int i = 0; i < l->ntgts; i++) y += res_card(a, cr, x0, y, cw, &l->tgts[i], 1) + 10;
    /* the source: read only, or shrunk for the copy and grown back -- or not, said in red */
    char sn[560]; int bad = l->src_restore[0] && strcmp(l->src_restore, "ok");
    if (bad) snprintf(sn, sizeof sn, "%s (המקור): %s", sl, l->src_restore);
    else if (!strcmp(l->shrink_path, "snapshot")) snprintf(sn, sizeof sn, "%s (המקור) נקרא בלבד ולא שונה — הכיווץ נעשה על עותק בזיכרון.", sl);   /* #1477 */
    else if (l->shrunk) snprintf(sn, sizeof sn, "%s (המקור) כווץ זמנית לשיכפול והוחזר לגודלו המקורי.", sl);
    else snprintf(sn, sizeof sn, "%s (המקור) נקרא בלבד ולא שונה.", sl);
    Text ts = text_make(cr, FONT_SANS, 13, bad ? 600 : 400, sn, (int)(cw - 20), DIR_RTL);
    y += 2; sb_led(cr, xr - 5, y + 9, 10, bad ? t->danger : t->led_ok, bad ? t->danger_soft : t->success_soft, 0, 1);
    text_draw_r(cr, &ts, xr - 18, y, bad ? t->danger_fg : t->muted); y += ts.h + 6; text_free(&ts);
    const char *rp = !strcmp(l->report, "usb") ? "שיכפול מדיסק-און-קי, בלי שרת. הדוח מוצג כאן ונשמר על הדיסק-און-קי."   /* #1914 (C) */
                   : !strcmp(l->report, "sent") ? "דווח ליומן השרת (דיווח המחשב — השיכפול לא עבר דרך השרת)."
                   : "לא דווח ליומן השרת — השרת לא ענה. השיכפול עצמו אינו תלוי בשרת.";
    Text tr = text_make(cr, FONT_SANS, 12, 400, rp, (int)(cw - 20), DIR_RTL);
    text_draw_r(cr, &tr, xr - 18, y, t->muted); y += tr.h + 16; text_free(&tr);
    double ow = lc_btn(a, cr, x0 + lc_btn_width(cr, "כבה את המחשב", "Enter"), y, 40, "כבה את המחשב", "Enter", BTN_PRIMARY, HIT_LC_OFF, a->lcu.focus == LCF_OFF);
    a->end_off_own = 1;   /* #2081: the #2024 bar over this screen draws no second one */
    /* #1914 (Nadav 20/10): on the USB stick, "הפעל מחדש" next to "כבה את המחשב", in its style */
    if (l->usb) ow += 10 + lc_btn(a, cr, x0 + ow + 10 + lc_btn_width(cr, "הפעל מחדש", NULL), y, 40, "הפעל מחדש", NULL, BTN_PRIMARY, HIT_LC_REBOOT, a->lcu.focus == LCF_REBOOT);
    lc_btn(a, cr, x0 + ow + 10 + lc_btn_width(cr, "שכפול נוסף", NULL), y, 40, "שכפול נוסף", NULL, BTN_PLAIN, HIT_LC_AGAIN, a->lcu.focus == LCF_AGAIN);
}

void screen_local(App *a, cairo_t *cr, double W, double H, double head_h) {
    int v = lc_view(a);
    if (v == LCVIEW_DONE) lc_draw_done(a, cr, W, H, head_h);
    else if (v == LCVIEW_PROGRESS) lc_draw_progress(a, cr, W, H, head_h);
    else lc_draw_source(a, cr, W, H, head_h);
}
