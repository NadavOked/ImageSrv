#include <stdio.h>
#include <string.h>
#include "screens_rounds.h"
#include "lcui.h"   /* 16/10: on the cloner the targets are its drawers (lc_pull.c) */

/* #706 / #1090: the destructive local restore is deliberately a single,
 * visible page. Images are cards, not a transient select popup, so the
 * operator can see the source and the physical target at the same time. */

static void restore_arrow(cairo_t *cr, Rgb c, double cx, double cy, double w) {
    /* RTL reading order: source is on the right, target on the left. */
    cairo_set_line_width(cr, 2);
    cairo_move_to(cr, cx + w / 2, cy);
    cairo_line_to(cr, cx - w / 2, cy);
    cairo_move_to(cr, cx - w / 2 + w * .28, cy - w * .28);
    cairo_line_to(cr, cx - w / 2, cy);
    cairo_line_to(cr, cx - w / 2 + w * .28, cy + w * .28);
    draw_set(cr, c);
    cairo_stroke(cr);
}

static void restore_title(cairo_t *cr, const Theme *t, double W, double *y, int pull) {
    Text h = rtl_block_make(cr, N_TITLE, 600, pull ? "שחזור אימג' מהשרת" : "שחזור אימג' למחשב הזה", W - 2 * N_PAD);
    Text p = rtl_block_make(cr, 13, 400,
        pull ? "בוחרים מקור, מסמנים את המגירות שייכתבו ומאשרים בחלון."
             : "בוחרים מקור, בודקים את הדיסק הפנימי ומאשרים בחלון.",
        W - 2 * N_PAD);
    text_draw_r(cr, &h, W - N_PAD, *y, t->ink);
    *y += h.h + 4;
    text_draw_r(cr, &p, W - N_PAD, *y, t->muted);
    *y += p.h + 12;
    cairo_rectangle(cr, N_PAD, *y, W - 2 * N_PAD, 1);
    draw_set(cr, t->hair);
    cairo_fill(cr);
    *y += 18;
    text_free(&h);
    text_free(&p);
}

static void disabled_button(cairo_t *cr, const Theme *t, Rect r, Text *label) {
    draw_fill_rrect(cr, r, t->radius_sm, t->track);     /* .btn:disabled: row-border fill, no border */
    text_draw(cr, label, r.x + (r.w - label->w) / 2,
              r.y + (r.h - label->h) / 2, t->muted);
}

void screen_restore(App *a, cairo_t *cr, double W, double H, double head_h) {
    const Theme *t = a->theme;
    const State *s = &a->st;
    double y = head_h + 22;
    int pull = lc_pull_active(a);
    restore_title(cr, t, W, &y, pull);

    const Disk *target = NULL;
    for (int i = 0; i < s->ndisks; i++) {
        if (!s->disks[i].removable) {
            target = &s->disks[i];
            break;
        }
    }

    double avail_w = W - 2 * N_PAD;
    double arrow_w = 58;
    double panel_gap = 14;
    double panel_w = (avail_w - arrow_w - 2 * panel_gap) / 2;
    double panel_h = 150;
    double pad = 22;
    /* 16/10: the drawers go under the target's two lines; the panels grow to hold them */
    Rect cells = {N_PAD + pad, y + pad + 56, panel_w - 2 * pad, 0};
    double cells_h = pull ? lc_pull_drawers(a, cr, cells, 0) : 0;
    if (pull && 56 + cells_h + 2 * pad > panel_h) panel_h = 56 + cells_h + 2 * pad;
    int has_target = pull ? cells_h > 0 : target != NULL;
    Rect source = {W - N_PAD - panel_w, y, panel_w, panel_h};
    Rect dest = {N_PAD, y, panel_w, panel_h};
    draw_fill_rrect(cr, source, t->radius, t->surface);
    draw_border_rrect(cr, source, t->radius, t->hair, 1);
    draw_fill_rrect(cr, dest, t->radius, t->surface);
    draw_border_rrect(cr, dest, t->radius, has_target ? t->hair : t->danger, 1);
    restore_arrow(cr, t->indigo, N_PAD + panel_w + panel_gap + arrow_w / 2,
                  y + panel_h / 2, 30);

    Text sh = rtl_block_make(cr, 15, 600, "מקור", source.w - 2 * pad);
    Text sl = rtl_block_make(cr, 12, 400, "ספריית ImageSrv", source.w - 2 * pad);
    const char *selected = "טרם נבחר אימג'";
    if (a->restore_image_sel > 0 && a->restore_image_sel <= s->nimages)
        selected = s->images[a->restore_image_sel - 1].name;
    Text sv = rtl_block_make(cr, 18, 600, selected, source.w - 2 * pad);
    double sy = source.y + pad;
    text_draw_r(cr, &sh, source.x + source.w - pad, sy, t->ink); sy += sh.h + 10;
    text_draw_r(cr, &sl, source.x + source.w - pad, sy, t->muted); sy += sl.h + 8;
    text_draw_r(cr, &sv, source.x + source.w - pad, sy,
                a->restore_image_sel ? t->ink : t->muted);

    char target_name[128], target_info[128];
    if (pull) {
        int k = lc_pull_ports(a, NULL, 0);
        if (!has_target) snprintf(target_name, sizeof target_name, "לא נמצאו מגירות");
        else if (!k) snprintf(target_name, sizeof target_name, "סמנו מגירות");
        else if (k == 1) snprintf(target_name, sizeof target_name, "מגירה אחת");
        else snprintf(target_name, sizeof target_name, "%d מגירות", k);
        target_info[0] = 0;
    } else if (target) {
        char size[32];
        fmt_bytes(size, sizeof size, (double)target->size_bytes);
        snprintf(target_name, sizeof target_name, "%s",
                 target->model[0] ? target->model : "דיסק פנימי");
        snprintf(target_info, sizeof target_info, "%s · %s", size, target->dev);
    } else {
        snprintf(target_name, sizeof target_name, "לא נמצא דיסק פנימי");
        snprintf(target_info, sizeof target_info, "השחזור חסום");
    }
    Text dh = rtl_block_make(cr, 15, 600, "יעד", dest.w - 2 * pad);
    Text dl = rtl_block_make(cr, 12, 400, pull ? "מגירות המחשב — לחצו כדי לסמן" : "הדיסק המקומי", dest.w - 2 * pad);
    Text dv = rtl_block_make(cr, 18, 600, target_name, dest.w - 2 * pad);
    Text di = text_make(cr, FONT_MONO, 12, 400, target_info, 0, DIR_LTR);
    double dy = dest.y + pad;
    text_draw_r(cr, &dh, dest.x + dest.w - pad, dy, t->ink); dy += dh.h + 10;
    text_draw_r(cr, &dl, dest.x + dest.w - pad, dy, t->muted); dy += dl.h + 8;
    if (pull) {
        /* the count where the disk's name is, then the drawers themselves */
        Text dc = text_make(cr, FONT_SANS, 13, 600, target_name, 0, DIR_RTL);
        text_draw(cr, &dc, dest.x + pad, dest.y + pad, has_target ? t->ink : t->danger_fg);
        text_free(&dc);
        lc_pull_drawers(a, cr, cells, 1);
    } else {
        text_draw_r(cr, &dv, dest.x + dest.w - pad, dy, target ? t->ink : t->danger_fg);
        dy += dv.h + 5;
        text_draw_r(cr, &di, dest.x + dest.w - pad, dy, t->muted);
    }

    y += panel_h + 14;
    Text warning = alert_make(cr, pull
        ? "אזהרה: השחזור יחליף את כל תוכן המגירות שסומנו. אי־אפשר לבטל את הפעולה לאחר שהחלה."
        : "אזהרה: השחזור יחליף את כל תוכן דיסק היעד. אי־אפשר לבטל את הפעולה לאחר שהחלה.",
        avail_w);
    draw_alert(cr, t, (Rect){N_PAD, y, avail_w, alert_height(&warning)}, &warning);
    y += alert_height(&warning) + 17;

    /* owner, 10/10: folder-first. The folders (name, how many images) with
     * keys 1-9; a folder opens its images, with the way back next to the
     * heading ("כל התיקיות", Esc / Backspace). */
    int ffirst[MAX_FOLDERS], fcount[MAX_FOLDERS];
    int nf = image_folders(s, ffirst, fcount, MAX_FOLDERS);
    int in = a->pick_in_folder, items[MAX_IMAGES], shown;
    shown = in ? folder_images(s, a->pick_folder, items, MAX_IMAGES) : nf;
    char head[160];
    if (in) snprintf(head, sizeof head, "תיקיות › %s", folder_label(a->pick_folder));
    else    snprintf(head, sizeof head, "בחירת אימג' — תיקייה");
    double head_row = 32;
    Text gh = rtl_block_make(cr, 15, 600, head, avail_w - (in ? 180 : 0));
    text_draw_r(cr, &gh, W - N_PAD, y + (head_row - gh.h) / 2, t->ink);
    if (in) {
        Text bk = btn_label(cr, "כל התיקיות");
        draw_btn(a, cr, (Rect){N_PAD, y, btn_width(&bk), head_row}, &bk, BTN_PLAIN, HIT_PICK_FOLDERS_BACK);
        text_free(&bk);
    }
    y += head_row + 10;
    int cols = H / W >= .7 ? 1 : 2;
    double grid_gap = 14;
    double image_w = (avail_w - (cols - 1) * grid_gap) / cols;
    double image_h = 56;
    double grid_bottom = H - N_STATUS_H - 128;
    const char *sel_folder = a->restore_image_sel > 0 && a->restore_image_sel <= s->nimages
                           ? s->images[a->restore_image_sel - 1].folder : NULL;
    for (int i = 0; i < shown; i++) {
        int row = i / cols, col = i % cols;
        Rect box = {W - N_PAD - image_w - col * (image_w + grid_gap),
                    y + row * (image_h + grid_gap), image_w, image_h};
        if (box.y + box.h > grid_bottom) break;
        const char *line1, *line2; char count[48];
        int selected_now, unchecked = 0;
        if (in) {
            line1 = s->images[items[i]].name; line2 = folder_label(a->pick_folder);
            selected_now = a->restore_image_sel == items[i] + 1;
            /* #1896: an old image whose C: is not known -- "לא נבדק", and a click marks it */
            if ((unchecked = s->images[items[i]].unchecked)) line2 = "לא נבדק — לחצו כדי לסמן איזו מחיצה היא C:";
        } else {
            const char *f = s->images[ffirst[i]].folder;
            line1 = folder_label(f);
            if (fcount[i] == 1) snprintf(count, sizeof count, "אימג' אחד");
            else snprintf(count, sizeof count, "%d אימג'ים", fcount[i]);
            line2 = count;
            selected_now = sel_folder && !strcmp(sel_folder, f);
        }
        draw_fill_rrect(cr, box, t->radius,
                        selected_now ? t->indigo_soft : t->surface);
        draw_border_rrect(cr, box, t->radius,
                          selected_now ? t->indigo : unchecked ? t->warn : t->hair,
                          selected_now || unchecked ? 2 : 1);
        Text name = rtl_block_make(cr, 14, 600, line1, box.w - 32);
        Text sub = rtl_block_make(cr, 11, 400, line2, box.w - 32);
        text_draw_r(cr, &name, box.x + box.w - 16, box.y + 10, t->ink);
        text_draw_r(cr, &sub, box.x + box.w - 16, box.y + 31, unchecked ? t->warn : t->muted);
        if (i < 9) {
            char key[4]; snprintf(key, sizeof key, "%d", i + 1);
            Text kt = text_make(cr, FONT_MONO, 11, 500, key, 0, DIR_LTR);
            Rect kb = {box.x + 10, box.y + 10, kt.w + 14, 21};
            draw_fill_rrect(cr, kb, 3, t->field);
            draw_border_rrect(cr, kb, 3, t->hair, 1);
            text_draw(cr, &kt, kb.x + 7, kb.y + (kb.h - kt.h) / 2, t->ink);
            text_free(&kt);
        }
        text_free(&name);
        text_free(&sub);
        hit_add(a, box, in ? HIT_OPTION_BASE + items[i] : HIT_PICK_FOLDER_BASE + i);
    }
    if (shown == 0) {
        Text none = rtl_block_make(cr, 13, 400, "אין אימג'ים זמינים לשחזור.", avail_w);
        text_draw_r(cr, &none, W - N_PAD, y, t->muted);
        text_free(&none);
    }
    int rows = (shown + cols - 1) / cols;
    if (rows < 1) rows = 1;
    y += rows * (image_h + grid_gap) + 3;

    double controls_y = H - N_STATUS_H - 94;
    if (y < controls_y) y = controls_y;
    /* #1270 (owner, 10/10): no typed name -- "מחק ושחזר" opens a confirm
     * dialog once an image is chosen and there is a disk to write to */
    int ready = (pull ? lc_pull_ports(a, NULL, 0) > 0 : target != NULL)
             && a->restore_image_sel > 0 && a->restore_image_sel <= s->nimages
             && !s->images[a->restore_image_sel - 1].unchecked;   /* #1896: never an image whose C: is unknown */
    Text start = btn_label(cr, "מחק ושחזר…");
    double sw = btn_width(&start);
    Rect start_box = {N_PAD, y, sw, N_BUTTON_H};
    if (ready) draw_btn(a, cr, start_box, &start, BTN_DANGER, HIT_RESTORE_START);
    else disabled_button(cr, t, start_box, &start);
    Text back = btn_label(cr, "חזרה");
    double bw = btn_width(&back);
    draw_btn(a, cr, (Rect){N_PAD + sw + 10, y, bw, N_BUTTON_H}, &back, BTN_PLAIN, HIT_BACK);
    Text label = rtl_block_make(cr, 12, 400, ready ? "השחזור יתחיל אחרי אישור בחלון."
                                           : pull ? "בחרו אימג' וסמנו מגירה אחת לפחות כדי להמשיך."
                                                  : "בחרו אימג' כדי להמשיך.", avail_w - sw - bw - 40);
    text_draw_r(cr, &label, W - N_PAD, y + (N_BUTTON_H - label.h) / 2, t->muted);
    if (s->form_error[0]) {
        Text err = error_make(cr, s->form_error, avail_w);
        text_draw_r(cr, &err, W - N_PAD, y + N_BUTTON_H + 6, t->danger_fg);
        text_free(&err);
    }

    text_free(&sh); text_free(&sl); text_free(&sv);
    text_free(&dh); text_free(&dl); text_free(&dv); text_free(&di);
    text_free(&warning); text_free(&gh); text_free(&label);
    text_free(&start); text_free(&back);
}
