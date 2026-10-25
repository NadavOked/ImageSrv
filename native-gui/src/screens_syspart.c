/* #1896 (Nadav 22/10): an old image with two Windows partitions of the same
 * size, captured before #1875 marked the system partition. The server cannot
 * tell which one is C:, so min-free is "not checked" and the image is never
 * offered (principle 5: no guess). Here, as in the console and on the cloner,
 * the operator marks it: the image is a "לא נבדק" tile in the picker, a click
 * opens this dialog -- one button per candidate (size, used), "ביטול" -- and
 * the choice goes to the server (syspart|<id>|<idx> on the build machine, the
 * console API with the session; lc-syspart|... on the cloner, its screen
 * nonce). The server writes it into the image's manifest; the next state
 * lists the image as a normal one. The dialog is in the style of the question
 * dialog (screens_ask.c): the amber frame, a title, the consequences. */
#include <stdio.h>
#include <string.h>
#include "widgets.h"
#include "lcui.h"

#define SP_W_MAX 640.0
#define SP_PAD   28.0
#define SP_BTN_H 44.0
#define SP_GAP   10.0


static const Image *open_image(const App *a) {
    if (a->sysp_open < 1 || a->sysp_open > a->st.nimages) return NULL;
    const Image *im = &a->st.images[a->sysp_open - 1];
    return im->unchecked ? im : NULL;
}

void restore_pick(App *a, int i) {
    if (i < 0 || i >= a->st.nimages) return;
    if (a->st.images[i].unchecked) { a->sysp_open = i + 1; return; }   /* never restored: marked first */
    a->restore_image_sel = i + 1;
}

static void cand_label(const Image *im, int k, char *out, size_t n) {
    char used[48] = "לא נמדד כמה תפוס";
    if (im->cand[k].used) snprintf(used, sizeof used, "%.1f GB בשימוש", (double)im->cand[k].used / 1e9);
    snprintf(out, n, "מחיצה %d · %.1f GB · %s", im->cand[k].idx, (double)im->cand[k].size / 1e9, used);
}

static void choose(App *a, const Image *im, int k) {
    char line[160];
    snprintf(line, sizeof line, "%s|%s|%d", lc_pull_active(a) ? "lc-syspart" : "syspart", im->id, im->cand[k].idx);
    printf("%s\n\n", line); fflush(stdout);   /* one token, then the record's end -- as lc_pull.c */
    a->sysp_open = 0;
    snprintf(a->toast, sizeof a->toast, "מחיצה %d נשלחה לשרת כמחיצת המערכת — האימג' יופיע כרגיל כשיישמר", im->cand[k].idx);
}

int syspart_modal(App *a, const Event *e) {
    const Image *im = open_image(a);
    if (!im) { a->sysp_open = 0; return 0; }
    const int n = im->ncand <= MAX_SYSP_CANDS ? im->ncand : 0;
    switch (e->type) {
    case UIEV_CHAR:
        if (e->ch >= '1' && e->ch < '1' + n) choose(a, im, e->ch - '1');
        return 1;
    case UIEV_KEY:
        if (e->key == KEYSYM_ESC) a->sysp_open = 0;
        return 1;
    case UIEV_CLICK: {
        a->ptr_x = e->x; a->ptr_y = e->y;
        int id = app_hit(a, e->x, e->y);
        if (id == HIT_SYSP_CANCEL) a->sysp_open = 0;
        else if (id >= HIT_SYSP_BASE && id < HIT_SYSP_BASE + n) choose(a, im, id - HIT_SYSP_BASE);
        return 1; }
    default:
        return 0;
    }
}

void draw_syspart_dialog(App *a, cairo_t *cr, double W, double H) {
    const Image *im = open_image(a);
    if (!im) return;
    const Theme *t = a->theme;
    const int n = im->ncand <= MAX_SYSP_CANDS ? im->ncand : 0;
    double usable = H - N_STATUS_H;
    cairo_rectangle(cr, 0, 0, W, usable);
    draw_set_a(cr, (Rgb){0, 0, 0}, .55);
    cairo_fill(cr);
    a->nhits = 0; a->clip_on = 0;             /* modal: only its buttons are clickable */

    double dw = W - 64 < SP_W_MAX ? W - 64 : SP_W_MAX, inner = dw - 2 * SP_PAD;
    char lead_s[512];
    snprintf(lead_s, sizeof lead_s, n
        ? "ב\"%s\" יש מחיצות Windows באותו גודל, ולא נשמר בקליטה איזו מהן היא מחיצת המערכת. "
          "בחרו את C: — הבחירה נשמרת באימג', ולפיה נבדק המקום הפנוי ונכתב שם המחשב."
        : "ב\"%s\" יש יותר מדי מחיצות לבחירה במסך הזה — סמנו את C: בקונסולה (אימג'ים ← סימון מחיצת המערכת).",
        im->name);
    Text title = rtl_block_make(cr, 22, 600, "איזו מחיצה היא C:?", inner);
    Text lead = rtl_block_make(cr, 14, 400, lead_s, inner);
    double dh = SP_PAD + title.h + 8 + lead.h + 18 + (n + 1) * (SP_BTN_H + SP_GAP) + SP_PAD;
    Rect box = { (W - dw) / 2, (usable - dh) / 2, dw, dh };
    draw_box_shadow(cr, box, t->radius, 10, 30, -8, t->shadow_strong, t->shadow_strong_a);
    draw_fill_rrect(cr, box, t->radius, t->surface);
    draw_border_rrect(cr, box, t->radius, t->warn, 2);
    double right = box.x + box.w - SP_PAD, y = box.y + SP_PAD;
    text_draw_r(cr, &title, right, y, t->ink); y += title.h + 8;
    text_draw_r(cr, &lead, right, y, t->muted); y += lead.h + 18;
    for (int k = 0; k < n; k++) {
        char label[160], keyed[176];
        cand_label(im, k, label, sizeof label);
        snprintf(keyed, sizeof keyed, "%d  ·  %s", k + 1, label);
        Text l = btn_label(cr, keyed);
        draw_btn(a, cr, (Rect){ box.x + SP_PAD, y, inner, SP_BTN_H }, &l, BTN_PLAIN, HIT_SYSP_BASE + k);
        text_free(&l);
        y += SP_BTN_H + SP_GAP;
    }
    Text c = btn_label(cr, "ביטול");
    draw_btn(a, cr, (Rect){ box.x + SP_PAD, y, inner, SP_BTN_H }, &c, BTN_PLAIN, HIT_SYSP_CANCEL);
    text_free(&c); text_free(&title); text_free(&lead);
}
