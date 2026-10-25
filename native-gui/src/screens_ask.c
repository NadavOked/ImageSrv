/* 11/10 (Nadav: "הכול גואי"): the questions the agent used to ask on the
 * text console, as a dialog in the style of the shrink question (#928,
 * screens_shrink.c) -- the approved pattern: the amber frame, a title, the
 * consequences, one filled primary button and a bordered secondary one with
 * a grey line under it. Two questions use it:
 *
 *   growback     a disk left shrunk by an interrupted capture (#926/#929):
 *                grow it back (primary) / leave it (secondary). Was
 *                shrinkoffer.sh:shrink_restore_ask.
 *   capture-end  the end of a capture (#409): power off (primary, the
 *                default) / back to the menu. Was task.sh:capture_end_ask.
 *   send-end, round-end  #1560: the end of a direct send / of a round opened
 *                here -- the same two answers (task.sh:build_end_ask).
 *
 * And draw_end_dialog below: the end-of-task count to power-off (#1560).
 *
 * The agent publishes the question (askgui.sh), guistate.sh copies it into
 * ask_prompt= / ask_row=, and main.c:ask_modal emits ask-<answer>|<nonce>.
 * No answer is the agent's business: it keeps the safe default (leave /
 * power off) when its ceiling passes. Modal: it replaces the hit table. */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include "widgets.h"
#include "lcui.h"   /* #1918: lc_dialog_box / lc_dialog_head / lc_btn -- the local clone's stop window */

#define ASK_W_MAX    760.0
#define ASK_PAD      28.0
#define ASK_BTN_H    44.0
#define ASK_GAP      10.0
#define ASK_HINT_GAP 6.0
#define ASK_HINT_PX  12.0

/* The same unit as the shrink dialog: decimal GB, one digit. */
static void ask_gb(char *out, size_t n, unsigned long long b) {
    snprintf(out, n, "%.1f GB", (double)b / 1e9);
}

/* #1560 (metal 17/10: "20" was fixed text, and read as "it powers off by
 * itself"): the end questions carry the epoch of the power-off (task.sh
 * build_end_ask) and the time left is counted here -- main.c redraws every
 * second while a count is on screen. */
static void left_text(long long at, char *out, size_t n) {
    long long left = at - (long long)time(NULL);
    if (left < 0) left = 0;
    if (left >= 60) snprintf(out, n, "%lld:%02lld דקות", left / 60, left % 60);
    else snprintf(out, n, "%lld שניות", left);
}

/* #1898 (Nadav 22/10): one disk is "הדיסק", more are "הדיסקים" -- the drawers the rows are about
 * (a row without a drawer is the one disk of a single restore). Returns how many disks. */
int ask_share_words(const State *s, char *lead, size_t n, const char **skip, const char **hint) {
    int ports[MAX_ASK_ROWS], np = 0, none = 0;
    for (int i = 0; i < s->nask; i++) {
        int p = s->ask[i].port, seen = 0;
        if (p <= 0) { none = 1; continue; }
        for (int k = 0; k < np; k++) seen |= ports[k] == p;
        if (!seen && np < MAX_ASK_ROWS) ports[np++] = p;
    }
    int disks = np + none, one = disks <= 1;
    snprintf(lead, n, "האימג' כווץ בקליטה. %s מהמקור, והמקום הנוסף יתחלק בין המחיצות לפי היחס ביניהן במקור:",
             one ? "הדיסק גדול" : "הדיסקים גדולים");
    *skip = one ? "דלג — אל תכתוב את הדיסק" : "דלג — אל תכתוב את הדיסקים";
    *hint = one ? "בלי תשובה הדיסק לא ייכתב" : "בלי תשובה הדיסקים לא ייכתבו";
    return disks;
}

static void end_left_text(const App *a, char *out, size_t n) {
    left_text(strtoll(a->st.ask_subject, NULL, 10), out, n);
}

void draw_ask_dialog(App *a, cairo_t *cr, double W, double H) {
    if (!a->st.ask_pending || a->st.shrink_pending) return;
    const Theme *t = a->theme;
    const int grow = a->st.ask_kind == ASK_GROWBACK;
    double usable = H - N_STATUS_H;
    cairo_rectangle(cr, 0, 0, W, usable);
    draw_set_a(cr, (Rgb){0, 0, 0}, .55);
    cairo_fill(cr);
    a->nhits = 0; a->clip_on = 0;             /* modal: only the answers are clickable */

    double dw = W - 2 * 32 < ASK_W_MAX ? W - 2 * 32 : ASK_W_MAX;
    double inner = dw - 2 * ASK_PAD;
    char title_s[256], lead_s[512], left_s[48];
    const char *primary, *secondary, *hint_s;
    int primary_style = BTN_PRIMARY;
    int table_rows = 0;
    for (int i = 0; i < a->st.nask; i++) table_rows += !a->st.ask[i].fs_only;
    const int nrows = a->st.ask_kind == ASK_RAID || a->st.ask_kind == ASK_BADSECT ? 0 : a->st.nask;   /* #1808/#2017: the one row is the title */
    if (grow) {
        snprintf(title_s, sizeof title_s, "דיסק %s כווץ לקליטה ולא הוחזר לגודלו", a->st.ask_subject);
        snprintf(lead_s, sizeof lead_s, "%s",
            "קליטה קודמת הופסקה לפני שהמחיצות הוחזרו (הפסקת חשמל או אתחול). ווינדוס עולה בכל מקרה.");
        primary = table_rows ? "החזר לגודל המקורי (מומלץ)" : "מתח את מערכת הקבצים למחיצה (מומלץ)";
        secondary = "השאר כמו שהוא";
        hint_s = "הרשומה נשארת פתוחה בקונסולה, וקליטה מהדיסק הזה תסורב עד שיוחזר";
    } else if (a->st.ask_kind == ASK_RAID) {
        /* #1808 (Nadav 22/10): a drawer with an old RAID set's metadata -- not
           refused, asked. Writing wipes it: the red button, never the default. */
        const char *w = a->st.nask ? a->st.ask[0].word : "";
        const char *what = !strcmp(w, "isw_raid_member") ? "מערך RAID של Intel"
                         : !strcmp(w, "ddf_raid_member") ? "מערך RAID מסוג DDF"
                         : !strcmp(w, "linux_raid_member") ? "מערך RAID של לינוקס (md)" : "מערך RAID";
        if (a->st.nask && a->st.ask[0].port > 0) snprintf(title_s, sizeof title_s, "דיסק %d: נמצא על הדיסק מידע של %s", a->st.ask[0].port, what);
        else snprintf(title_s, sizeof title_s, "הדיסק %s: נמצא עליו מידע של %s", a->st.ask_subject, what);
        snprintf(lead_s, sizeof lead_s, "הדיסק היה פעם חלק ממערך (%s). כתיבה תמחק את המידע הזה, והדיסק ייכתב כדיסק רגיל.", w);
        primary = "כתוב ומחק את המידע"; primary_style = BTN_DANGER;
        secondary = "דלג על הדיסק";
        hint_s = "בלי תשובה הדיסק לא ייכתב. אם המחשב שהדיסק יוצא אליו מוגדר ב-RAID — העבירו אותו ל-AHCI";
    } else if (a->st.ask_kind == ASK_BADSECT) {
        /* #2017 (Nadav 22/10, "never worse than dd"): the source did not read, raw
           either. Going on is the default (Nadav 22/10): the primary button, with
           the focus (Enter), and by itself when the count ends -- what is not read
           becomes zeros, counted. Only "עצור" fails the copy; the source is only read. */
        const int idx = a->st.nask ? a->st.ask[0].idx : 0;
        if (a->st.nask && a->st.ask[0].port > 0) snprintf(title_s, sizeof title_s, "דיסק %d (המקור): מחיצה %d לא נקראת", a->st.ask[0].port, idx);
        else snprintf(title_s, sizeof title_s, "דיסק המקור (%s): מחיצה %d לא נקראת", a->st.ask_subject, idx);
        left_text(a->st.nask ? a->st.ask[0].until : 0, left_s, sizeof left_s);
        snprintf(lead_s, sizeof lead_s, "הקריאה נכשלה גם בהעתקה גולמית — כנראה סקטורים פגומים. ההעתקה ממשיכה כמו dd conv=noerror: מה שלא ייקרא ייכתב ביעדים כאפסים וייספר, והיעדים לא יהיו זהים למקור. ממשיך אוטומטית בעוד %s.", left_s);
        primary = "המשך בכל זאת";
        secondary = "עצור — השיכפול ייכשל";
        hint_s = "\"עצור\" מכשיל את השיכפול, והמקור לא משתנה בשום מקרה. כדאי להחליף את דיסק המקור";
    } else if (a->st.ask_kind == ASK_SHARE) {
        /* #1898 (Nadav 22/10): C: before the linux root takes its share in
           place, and the root moves forward -- shown, and approved first. */
        snprintf(title_s, sizeof title_s, "%s", "חלוקת המקום הנוסף בין המחיצות");
        ask_share_words(&a->st, lead_s, sizeof lead_s, &secondary, &hint_s);   /* one disk or more */
        primary = "אשר וכתוב";
    } else if (a->st.ask_kind == ASK_CAPTURE_END) {
        /* #1470: the machine is released after the upload; the server re-reads every
           file before the image enters the library -- it is not there yet. */
        snprintf(title_s, sizeof title_s, "%s", "הקליטה הסתיימה — השרת מאמת את האימג'");
        end_left_text(a, left_s, sizeof left_s);
        snprintf(lead_s, sizeof lead_s, "המחשב יכבה בעוד %s אם לא תבחרו. אחרי הכיבוי אפשר לשלוף את הדיסק בבטחה.",
            left_s);
        primary = "כבה";
        secondary = "חזרה לתפריט";
        hint_s = "המחשב נשאר דלוק ומציג את התפריט — האימג' החדש יופיע להפצה כשהאימות בשרת יעבור";
    } else {
        /* #1560 (Nadav 17/10): the direct send and a round opened here end
           with the same question as a capture -- power off, or the menu. */
        snprintf(title_s, sizeof title_s, "%s", a->st.ask_kind == ASK_SEND_END
            ? "ההפצה הישירה הסתיימה — הדיסקים נכתבו" : "הסבב הסתיים");
        end_left_text(a, left_s, sizeof left_s);
        snprintf(lead_s, sizeof lead_s, "המחשב יכבה בעוד %s אם לא תבחרו.", left_s);
        primary = "כבה";
        secondary = "חזרה לתפריט";
        hint_s = "המחשב נשאר דלוק ומציג את התפריט";
    }
    Text title = rtl_block_make(cr, 22, 600, title_s, inner);
    Text lead = rtl_block_make(cr, 14, 400, lead_s, inner);
    Text rows[MAX_ASK_ROWS];
    double rows_h = 0;
    for (int i = 0; i < a->st.nask; i++) {
        char now_s[24], orig_s[24], line[320];
        ask_gb(now_s, sizeof now_s, a->st.ask[i].now_bytes);
        ask_gb(orig_s, sizeof orig_s, a->st.ask[i].orig_bytes);
        if (a->st.ask_kind == ASK_RAID || a->st.ask_kind == ASK_BADSECT) continue;   /* its one row is the title */
        if (a->st.ask_kind == ASK_SHARE) {
            char disk_s[32];
            if (a->st.ask[i].port > 0) snprintf(disk_s, sizeof disk_s, "דיסק %d", a->st.ask[i].port);
            else snprintf(disk_s, sizeof disk_s, "הדיסק");
            snprintf(line, sizeof line, "%s · מחיצה %d: %s במקום %s", disk_s, a->st.ask[i].idx, orig_s, now_s);
        } else if (a->st.ask[i].fs_only)
            snprintf(line, sizeof line, "מחיצה %d: מערכת הקבצים (%s) קטנה מהמחיצה (%s)",
                     a->st.ask[i].idx, now_s, orig_s);
        else
            snprintf(line, sizeof line, "מחיצה %d: עכשיו %s, במקור %s", a->st.ask[i].idx, now_s, orig_s);
        rows[i] = rtl_block_make(cr, 15, 500, line, inner);
        rows_h += rows[i].h + 6;
    }
    Text hint = text_make(cr, FONT_SANS, ASK_HINT_PX, 400, hint_s, (int)inner, DIR_RTL);
    pango_layout_set_alignment(hint.layout, PANGO_ALIGN_CENTER);
    double answers_h = ASK_BTN_H + ASK_GAP + ASK_BTN_H + ASK_HINT_GAP + hint.h;
    double dh = ASK_PAD + title.h + 8 + lead.h + 18 + rows_h + (nrows ? 18 : 0) + answers_h + ASK_PAD;
    if (dh > usable - 24) dh = usable - 24;   /* MAX_ASK_ROWS keeps this from clipping on 768 */
    Rect box = { (W - dw) / 2, (usable - dh) / 2, dw, dh };
    draw_box_shadow(cr, box, t->radius, 10, 30, -8, t->shadow_strong, t->shadow_strong_a);
    draw_fill_rrect(cr, box, t->radius, t->surface);
    draw_border_rrect(cr, box, t->radius, t->warn, 2);

    double right = box.x + box.w - ASK_PAD, y = box.y + ASK_PAD;
    text_draw_r(cr, &title, right, y, t->ink); y += title.h + 8;
    text_draw_r(cr, &lead, right, y, t->muted); y += lead.h + 18;
    for (int i = 0; i < nrows; i++) {
        text_draw_r(cr, &rows[i], right, y, t->ink);
        y += rows[i].h + 6;
        text_free(&rows[i]);
    }
    y = box.y + box.h - ASK_PAD - answers_h;
    Text l = btn_label(cr, primary);
    draw_btn(a, cr, (Rect){ box.x + ASK_PAD, y, inner, ASK_BTN_H }, &l, primary_style, HIT_ASK_PRIMARY);
    if (a->st.ask_kind == ASK_BADSECT) draw_focus_ring(a, cr, (Rect){ box.x + ASK_PAD, y, inner, ASK_BTN_H });   /* Enter = המשך */
    text_free(&l);
    y += ASK_BTN_H + ASK_GAP;
    l = btn_label(cr, secondary);
    draw_btn(a, cr, (Rect){ box.x + ASK_PAD, y, inner, ASK_BTN_H }, &l, BTN_PLAIN, HIT_ASK_SECONDARY);
    text_free(&l);
    y += ASK_BTN_H + ASK_HINT_GAP;
    text_draw(cr, &hint, box.x + ASK_PAD, y, t->muted);
    text_free(&title); text_free(&lead); text_free(&hint);
}

/* #1560 (Nadav 17/10): the end of a task that does not ask -- a cloner after
 * any of its tasks, done or failed. The agent counts to the power-off
 * (idle.sh end_countdown) and this only shows it, counted each second: what
 * ended ("N of M disks written"), the time left, "כבה עכשיו" (Enter). Some
 * targets failed: a second button stops the count to read the results; then
 * (held) the count is a bar over the screen, with the same one button.
 * #2024 (Nadav 22/10): a disk with a problem holds the results with no count
 * (held, epoch 0) -- the bar says why the machine stays on, its one button
 * is "כבה", and each problem target is highlighted on its screen with its
 * reason (end_problem_of: the cloner's drawers, the local clone's targets). */
const char *end_problem_of(const State *s, const char *dev, int *red) {
    if (s->end_kind == END_NONE || !dev || !dev[0]) return NULL;
    for (int i = 0; i < s->nend_prob; i++)
        if (!strcmp(s->end_prob[i].dev, dev)) { if (red) *red = s->end_prob[i].red; return s->end_prob[i].why; }
    return NULL;
}

static void end_left(const App *a, char *out, size_t n) {
    long long left = a->st.end_poweroff_at - (long long)time(NULL);
    if (left < 0) left = 0;
    if (left >= 60) snprintf(out, n, "%lld:%02lld דקות", left / 60, left % 60);
    else snprintf(out, n, "%lld שניות", left);
}

static void draw_end_bar(App *a, cairo_t *cr, double W, double H) {
    const Theme *t = a->theme;
    char left_s[48], msg[256];
    const int unlimited = a->st.end_poweroff_at == 0;   /* #2024 */
    /* #2081 (mockup 03, Nadav 24/10): one power-off button on the screen. When the
     * screen drew its own "כבה את המחשב" (the local clone's result, the cloner's
     * held end) the bar is the sentence only, and names that button. */
    const int own = a->end_off_own;
    end_left(a, left_s, sizeof left_s);
    if (unlimited && own) snprintf(msg, sizeof msg, "המחשב נשאר דלוק כי יש דיסק עם בעיה. כשתסיימו: \"כבה את המחשב\".");
    else if (unlimited) snprintf(msg, sizeof msg, "המחשב נשאר דלוק כי יש דיסק עם בעיה — לחצו 'כבה' כשסיימתם");
    else snprintf(msg, sizeof msg, "המחשב ייכבה בעוד %s", left_s);
    Text l = { 0 };
    double bw = 0;
    if (!own) { l = btn_label(cr, unlimited ? "כבה" : "כבה עכשיו"); bw = l.w + 48; }
    Text v = text_make(cr, FONT_SANS, 15, 600, msg, (int)(W - bw - 88), DIR_RTL);
    double w = v.w + bw + (own ? 40 : 56), h = fmax(own ? 0 : ASK_BTN_H, v.h) + 16;
    Rect b = { (W - w) / 2, H - N_STATUS_H - h - 24, w, h };
    draw_fill_rrect(cr, b, t->radius, t->warning_soft);
    draw_border_rrect(cr, b, t->radius, t->warn, 2);
    text_draw(cr, &v, b.x + b.w - 20 - v.w, b.y + (b.h - v.h) / 2, t->warn);
    if (!own) { draw_btn(a, cr, (Rect){ b.x + 8, b.y + 8, bw, ASK_BTN_H }, &l, BTN_PRIMARY, HIT_END_NOW); text_free(&l); }
    text_free(&v);
}

void draw_end_dialog(App *a, cairo_t *cr, double W, double H) {
    if (a->st.end_kind == END_NONE) return;
    if (a->st.end_held) { draw_end_bar(a, cr, W, H); return; }   /* the results are the screen */
    const Theme *t = a->theme;
    double usable = H - N_STATUS_H;
    cairo_rectangle(cr, 0, 0, W, usable);
    draw_set_a(cr, (Rgb){0, 0, 0}, .55);
    cairo_fill(cr);
    a->nhits = 0; a->clip_on = 0;             /* modal: only its buttons are clickable */

    const int k = a->st.end_kind, can_hold = k == END_PARTIAL || k == END_FAILED;
    char what[200], lead_s[200], left_s[48];
    if (k == END_PARTIAL && a->st.end_total > 0)
        snprintf(what, sizeof what, "השיכפול הסתיים: %d מתוך %d דיסקים נכתבו", a->st.end_done, a->st.end_total);
    else snprintf(what, sizeof what, "%s",
        k == END_LOCALCLONE ? "השיכפול המקומי הושלם — כל הדיסקים נכתבו"
        : k == END_FAILED ? "השיכפול נכשל — אף דיסק לא נכתב"
        : k == END_PARTIAL ? "השיכפול הסתיים — חלק מהדיסקים לא נכתבו"
        : "השיכפול הושלם — הדיסקים נכתבו");
    end_left(a, left_s, sizeof left_s);
    snprintf(lead_s, sizeof lead_s, "המחשב ייכבה בעוד %s.", left_s);
    double dw = W - 2 * 32 < ASK_W_MAX ? W - 2 * 32 : ASK_W_MAX;
    double inner = dw - 2 * ASK_PAD;
    Text title = rtl_block_make(cr, 22, 600, what, inner);
    Text lead = rtl_block_make(cr, 18, 600, lead_s, inner);
    Text hint = text_make(cr, FONT_SANS, ASK_HINT_PX, 400,
        can_hold ? "הסיבה נשמרה בקונסולה. כדי לקרוא כאן מה נכשל — עצרו את הספירה"
                 : "אין צורך לעשות דבר — המחשב יכבה לבד, ואחרי הכיבוי אפשר להחליף דיסקים",
        (int)inner, DIR_RTL);
    pango_layout_set_alignment(hint.layout, PANGO_ALIGN_CENTER);
    double buttons_h = ASK_BTN_H + (can_hold ? ASK_GAP + ASK_BTN_H : 0);
    double dh = ASK_PAD + title.h + 10 + lead.h + 22 + buttons_h + ASK_HINT_GAP + hint.h + ASK_PAD;
    Rect box = { (W - dw) / 2, (usable - dh) / 2, dw, dh };
    draw_box_shadow(cr, box, t->radius, 10, 30, -8, t->shadow_strong, t->shadow_strong_a);
    draw_fill_rrect(cr, box, t->radius, t->surface);
    draw_border_rrect(cr, box, t->radius, t->warn, 2);
    double right = box.x + box.w - ASK_PAD, y = box.y + ASK_PAD;
    text_draw_r(cr, &title, right, y, t->ink); y += title.h + 10;
    text_draw_r(cr, &lead, right, y, t->warn); y += lead.h + 22;
    Text l = btn_label(cr, "כבה עכשיו");
    draw_btn(a, cr, (Rect){ box.x + ASK_PAD, y, inner, ASK_BTN_H }, &l, BTN_PRIMARY, HIT_END_NOW);
    text_free(&l);
    if (can_hold) {
        y += ASK_BTN_H + ASK_GAP;
        l = btn_label(cr, "עצור את הספירה — הצג מה נכשל");
        draw_btn(a, cr, (Rect){ box.x + ASK_PAD, y, inner, ASK_BTN_H }, &l, BTN_PLAIN, HIT_END_HOLD);
        text_free(&l);
    }
    y += ASK_BTN_H + ASK_HINT_GAP;
    text_draw(cr, &hint, box.x + ASK_PAD, y, t->muted);
    text_free(&title); text_free(&lead); text_free(&hint);
}

/* #1918 (Nadav 20/10): the power button pressed in the middle of a write --
 * the local clone's own stop window (screens_lcrun.c lc_draw_stop: the same
 * box, head and buttons), asked by the agent (powerbtn.sh). The write goes
 * on until an answer: "המשך" (Esc), or the red "עצור וכבה" (Del -- never
 * Enter, as cancel_dlg), which stops it by its own stop and then powers off.
 * A write with no stop of its own (can_stop=0) is said so, and only "המשך". */
int power_ask_open(const App *a) {
    return a->st.power_nonce[0] && strcmp(a->power_sent, a->st.power_nonce) != 0;
}

void draw_power_dialog(App *a, cairo_t *cr, double W, double H) {
    if (!power_ask_open(a)) return;
    const char *k = a->st.power_kind;
    const int fem = strcmp(k, "localclone") && strcmp(k, "round") && strcmp(k, "restore");   /* קליטה, כתיבה */
    const char *noun = !strcmp(k, "localclone") || !strcmp(k, "round") ? "שיכפול"
                     : !strcmp(k, "restore") ? "שחזור" : !strcmp(k, "capture") ? "קליטה" : "כתיבה";
    char title[160];
    snprintf(title, sizeof title, a->st.power_can_stop ? "יש %s %s — לעצור ולכבות?" : "יש %s %s — המחשב לא יכובה באמצע",
             noun, fem ? "פעילה" : "פעיל");
    const char *lead = a->st.power_can_stop && !strcmp(k, "capture")
        ? "הקליטה ממשיכה עד שתבחרו. \"עצור וכבה\" מבטל אותה כמו \"ביטול הקליטה\" בקונסולה: האימג׳ לא נשמר, הדיסק מוחזר לגודלו, ואחר כך המחשב כבה."
        : a->st.power_can_stop
        ? "הכתיבה ממשיכה עד שתבחרו. \"עצור וכבה\" עוצר אותה כמו כפתור העצירה: הדיסקים שבכתיבה לא יהיו שמישים, ואחר כך המחשב כבה."
        : "השלב הזה אינו נעצר באמצע, והכתיבה ממשיכה. כבו את המחשב אחרי שיסתיים.";
    double w = W - 48 < 560 ? W - 48 : 560;
    Text th = text_make(cr, FONT_SANS, 22, 600, title, (int)(w - 98), DIR_RTL);
    Text tl = text_make(cr, FONT_SANS, 14, 400, lead, (int)(w - 98), DIR_RTL);
    double h = 26 + (th.h > 30 ? th.h : 30) + 6 + tl.h + 16 + 4 + 44 + 24;
    text_free(&th); text_free(&tl);
    Rect box = lc_dialog_box(a, cr, W, H, w, h);
    double y = box.y + 26, right = box.x + box.w - 28;
    lc_dialog_head(a, cr, box, title, lead, &y);
    y += 4;
    double gw = 0;
    if (a->st.power_can_stop) gw = lc_btn(a, cr, right, y, 44, "עצור וכבה", "Del", BTN_DANGER, HIT_PWR_STOP, 0) + 10;
    lc_btn(a, cr, right - gw, y, 44, "המשך", "Esc", BTN_PLAIN, HIT_PWR_KEEP, 0);
}
