/* 16/10 (Nadav): the SMART warning window. Writing to an ORANGE disk is
 * allowed, and so is reading from any warned one (a failing disk is worth
 * rescuing data from) -- but only after this window has shown the warning:
 * the disk by its number, its colour, why, the model and the serial. Two
 * buttons, no typed word (#1270): "דלג" -- the default, it has the focus,
 * Esc -- and the red "כתוב בכל זאת". Only the red button carries the action
 * out; the caller does that with the copy sdlg_handle hands back.
 *
 * #1732 (Nadav 18/10): a RED disk (SMART fail) is never written. Opened for a
 * write onto one, the window says so and has one button, "סגור": there is
 * no answer that writes (sdlg_handle never hands back go=1 for it).
 *
 * #2017 (Nadav 22/10: "F1 אדום. באמצע סוף חייו, לפני אדום — כתום"): an ORANGE
 * source of the local clone (warn, failed_last) is not asked -- the window is a
 * notice with one button, "המשך" (Enter, and Esc too: nothing to cancel), and
 * then the confirm as for any source. A red source keeps its question.
 *
 * The pieces are the local clone's dialog (screens_local.c: lc_dialog_box,
 * lc_btn with its focus ring), so the window looks like the other dialogs
 * in both themes. */
#include <math.h>
#include <stdio.h>
#include <string.h>
#include "lcui.h"
#include "screens_rounds.h"
#include "smartdlg.h"

static void cpy(char *d, size_t n, const char *s) { snprintf(d, n, "%s", s ? s : ""); }

/* The reason, in words: what the agent classified (smart.sh), or what the
 * server remembers of the previous clone (#874) for a red disk. */
static void reason_words(const char *verdict, const char *reason, char *out, size_t n) {
    if (!strcmp(verdict, "failed_last")) {
        snprintf(out, n, "%s", !strcmp(reason, "cable") ? "נכשל בשיכפול הקודם — סיבה: כבל/חריץ"
                             : !strcmp(reason, "disk") ? "נכשל בשיכפול הקודם — סיבה: הדיסק"
                             : !strcmp(reason, "readonly") ? "נכשל בשיכפול הקודם — סיבה: הדיסק במצב קריאה בלבד"   /* #1742 */
                             : "נכשל בשיכפול הקודם");
        return;
    }
    const char *r = smart_reason_text(reason);
    if (r[0]) snprintf(out, n, "%s", r);
    else snprintf(out, n, "SMART: %s", smart_he(verdict));
}

void sdlg_open(App *a, int kind, int ref, const char *dev, const char *label, const char *verdict,
               const char *reason, const char *model, const char *serial) {
    SmartDlg *d = &a->sdlg;
    memset(d, 0, sizeof *d);
    d->kind = kind; d->ref = ref; d->go = 0;
    d->level = smart_level(verdict ? verdict : "");
    if (d->level < 1) d->level = 1;                 /* opened only on a warning; never drawn green */
    d->retry = verdict && !strcmp(verdict, "failed_last");
    d->readonly = reason && !strcmp(reason, "readonly");   /* #1742: the refusal says why */
    cpy(d->dev, sizeof d->dev, dev); cpy(d->disk, sizeof d->disk, label);
    cpy(d->model, sizeof d->model, model); cpy(d->serial, sizeof d->serial, serial);
    reason_words(verdict ? verdict : "", reason ? reason : "", d->reason, sizeof d->reason);
    d->go = kind == SDLG_LC_SOURCE && d->level == 1;   /* #2017: the notice's one button has the focus */
}

/* #1732: a write onto a red disk (SMART fail) -- refused, not asked. */
static int red_write(const SmartDlg *d) {
    return d->level == 2 && (d->kind == SDLG_CLONER || d->kind == SDLG_RESTORE || d->kind == SDLG_LC_TARGET);
}

int sdlg_notice(const SmartDlg *d) { return d->kind == SDLG_LC_SOURCE && d->level == 1; }   /* #2017 */

int sdlg_handle(App *a, const Event *e, SmartDlg *done) {
    SmartDlg *d = &a->sdlg;
    memset(done, 0, sizeof *done);
    if (!d->kind) return 0;
    int answer = -1;
    if (e->type == UIEV_MOVE) return 0;
    if (sdlg_notice(d)) {   /* #2017: one answer -- Enter, Esc or the button all go on */
        if ((e->type == UIEV_KEY && (e->key == KEYSYM_ENTER || e->key == KEYSYM_ESC)) ||
            (e->type == UIEV_CLICK && app_hit(a, e->x, e->y) == HIT_SDLG_GO)) answer = 1;
    } else if (e->type == UIEV_KEY) {
        if (e->key == KEYSYM_ESC) answer = 0;
        else if (e->key == KEYSYM_ENTER) answer = d->go;
        else if ((e->key == KEYSYM_TAB || e->key == KEYSYM_BACKTAB ||
                  e->key == KEYSYM_LEFT || e->key == KEYSYM_RIGHT) && !red_write(d)) d->go = !d->go;
    } else if (e->type == UIEV_CLICK) {
        int id = app_hit(a, e->x, e->y);
        if (id == HIT_SDLG_SKIP) answer = 0;
        else if (id == HIT_SDLG_GO) answer = 1;
    }
    if (answer >= 0) {
        *done = *d; done->go = red_write(d) ? 0 : answer;   /* #1732: nothing writes on red */
        d->kind = SDLG_NONE;
    }
    return 1;
}

/* ---- drawing ------------------------------------------------------------------- */

static const char *title_fmt(const SmartDlg *d) {
    if (sdlg_notice(d)) return d->retry ? "המקור (%s) נכשל בשיכפול הקודם" : "המקור (%s) מראה סימני שחיקה";   /* #2017 */
    if (d->kind == SDLG_LC_SOURCE) return "להעתיק מ%s למרות אזהרת ה-SMART?";
    if (d->kind == SDLG_CAPTURE) return "לקלוט מ%s למרות אזהרת ה-SMART?";
    if (red_write(d)) return "לא כותבים על %s";
    if (d->retry) return "לנסות שוב לכתוב על %s?";
    return "לכתוב על %s למרות אזהרת ה-SMART?";
}

static const char *lead_of(const SmartDlg *d) {
    if (sdlg_notice(d)) return "מומלץ להחליף אותו בקרוב. הדיסק רק ייקרא — לא ייכתב עליו דבר, וההעתקה ממשיכה.";   /* #2017 */
    if (d->kind == SDLG_LC_SOURCE || d->kind == SDLG_CAPTURE)
        return "הדיסק רק ייקרא — לא ייכתב עליו דבר. כדאי להציל ממנו נתונים כל עוד הוא קורא, אבל הקריאה עלולה להיכשל באמצע.";
    if (red_write(d)) return red_refusal_he(d->readonly ? "readonly" : "");   /* #1742: by the reason */
    if (d->retry) return "הכתיבה הקודמת על הדיסק הזה נכשלה. אם תיכשל שוב — זה יוצג מיד וירשם.";
    return "הדיסק עלול להיכשל בזמן הכתיבה או אחריה. \"דלג\" משאיר אותו כמו שהוא.";
}

static const char *go_label(const SmartDlg *d) {
    if (d->kind == SDLG_LC_SOURCE) return "העתק ממנו בכל זאת";
    if (d->kind == SDLG_CAPTURE) return "קלוט ממנו בכל זאת";
    return d->retry ? "נסה שוב" : "כתוב בכל זאת";
}

#define SD_ROWS 5

void sdlg_draw(App *a, cairo_t *cr, double W, double H) {
    const SmartDlg *d = &a->sdlg;
    if (!d->kind) return;
    const Theme *t = a->theme;
    Rgb col = d->level == 2 ? t->danger : t->warn, soft = d->level == 2 ? t->danger_soft : t->warning_soft;
    double w = fmin(580, W - 64), inner = w - 56, key_w = 120, val_w = inner - key_w - 12;

    char disk_i[sizeof d->disk + 8], title[sizeof disk_i + 120], state[120];
    bidi_isolate(d->disk[0] ? d->disk : "הדיסק", disk_i, sizeof disk_i);
    snprintf(title, sizeof title, title_fmt(d), disk_i);
    snprintf(state, sizeof state, "%s", d->level == 2 ? "אדום — SMART תקול"
             : d->retry ? "כתום — נכשל בשיכפול הקודם" : "כתום — אזהרת SMART");
    char model_i[100], serial_i[100];
    bidi_isolate(d->model[0] ? d->model : "לא דווח", model_i, sizeof model_i);
    bidi_isolate(d->serial[0] ? d->serial : "לא דווח", serial_i, sizeof serial_i);
    const char *keys[SD_ROWS] = { "דיסק", "מצב", "סיבה", "דגם", "מספר סידורי" };
    const char *vals[SD_ROWS] = { disk_i, state, d->reason, model_i, serial_i };

    /* measure: the head, the lead, the rows, the buttons */
    Text h = text_make(cr, FONT_SANS, 20, 600, title, (int)(inner - 42), DIR_RTL);
    Text l = text_make(cr, FONT_SANS, 14, 400, lead_of(d), (int)(inner - 42), DIR_RTL);
    Text k[SD_ROWS], v[SD_ROWS]; double rows_h = 0, row_h[SD_ROWS];
    for (int i = 0; i < SD_ROWS; i++) {
        k[i] = rtl_block_make(cr, 13, 500, keys[i], key_w);
        v[i] = rtl_block_make(cr, 14, i == 1 ? 600 : 400, vals[i], val_w);
        row_h[i] = fmax(k[i].h, v[i].h); rows_h += row_h[i] + 8;
    }
    double head_h = fmax(30, h.h);
    double bh = 40, dh = 28 + head_h + 6 + l.h + 16 + 14 + rows_h + 14 + 20 + bh + 28;
    Rect box = lc_dialog_box(a, cr, W, H, w, dh);   /* the dim, the shadow, the modal hit table */
    double right = box.x + box.w - 28, y = box.y + 28;

    /* the head: the round "!" in the disk's colour, the question */
    double cy = y + head_h / 2;
    cairo_arc(cr, right - 15, cy, 15, 0, 2 * M_PI); draw_set(cr, soft); cairo_fill(cr);
    cairo_set_line_width(cr, 2.4); cairo_set_line_cap(cr, CAIRO_LINE_CAP_ROUND); draw_set(cr, col);
    cairo_move_to(cr, right - 15, cy - 6); cairo_line_to(cr, right - 15, cy + 1);
    cairo_move_to(cr, right - 15, cy + 5.5); cairo_line_to(cr, right - 15, cy + 6); cairo_stroke(cr);
    text_draw_r(cr, &h, right - 42, cy - h.h / 2, t->ink); y += head_h + 6;
    text_draw_r(cr, &l, right - 42, y, t->muted); y += l.h + 16;

    /* the details: a box edged in the disk's colour, label | value rows */
    Rect det = { box.x + 28, y, inner, 14 + rows_h + 14 - 8 };
    draw_fill_rrect(cr, det, t->radius_sm, soft); draw_border_rrect(cr, det, t->radius_sm, col, 1);
    cairo_rectangle(cr, det.x + det.w - 4, det.y, 4, det.h); draw_set(cr, col); cairo_fill(cr);
    double ry = det.y + 14, kr = det.x + det.w - 18;
    for (int i = 0; i < SD_ROWS; i++) {
        text_draw_r(cr, &k[i], kr, ry, t->muted);
        text_draw_r(cr, &v[i], kr - key_w - 12, ry, i == 1 ? col : t->ink);
        ry += row_h[i] + 8;
        text_free(&k[i]); text_free(&v[i]);
    }
    y = det.y + det.h + 20;

    /* the buttons, from the inline end: "דלג" (the default, Esc) then the red one;
     * #1732: on a write onto a red disk, "סגור" alone */
    double x = right;
    if (sdlg_notice(d)) lc_btn(a, cr, x, y, bh, "המשך", "Enter", BTN_PRIMARY, HIT_SDLG_GO, 1);   /* #2017: one button */
    else {
        x -= lc_btn(a, cr, x, y, bh, red_write(d) ? "סגור" : "דלג", "Esc", BTN_PLAIN, HIT_SDLG_SKIP, !d->go) + 10;
        if (!red_write(d)) lc_btn(a, cr, x, y, bh, go_label(d), NULL, BTN_DANGER, HIT_SDLG_GO, d->go);
    }
    text_free(&h); text_free(&l);
}
