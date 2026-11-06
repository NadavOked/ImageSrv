#include <math.h>
#include <stdio.h>
#include <string.h>
#include "widgets.h"

/* ---- hit table ------------------------------------------------------------ */

void hit_add(App *a, Rect r, int id) {
    if (a->clip_on) {
        /* .sbody{overflow-y:auto}: what is drawn outside the body box is
         * not on screen, so it must not be clickable either. */
        double x0 = fmax(r.x, a->clip.x), y0 = fmax(r.y, a->clip.y);
        double x1 = fmin(r.x + r.w, a->clip.x + a->clip.w);
        double y1 = fmin(r.y + r.h, a->clip.y + a->clip.h);
        if (x1 <= x0 || y1 <= y0) return;
        r = (Rect){ x0, y0, x1 - x0, y1 - y0 };
    }
    if (a->nhits < MAX_HITS) { a->hits[a->nhits].r = r; a->hits[a->nhits].id = id; a->nhits++; }
}

int hovered(const App *a, Rect r) {
    double s = a->scale > 0 ? a->scale : 1;       /* the pointer is in screen pixels */
    return a->ptr_visible && rect_has(r, a->ptr_x / s, a->ptr_y / s);
}

double dmax(double x, double y) { return x > y ? x : y; }

/* ---- buttons -------------------------------------------------------------- */

Text btn_label(cairo_t *cr, const char *s) { return text_make(cr, FONT_SANS, N_SUB, 500, s, 0, DIR_RTL); }
double btn_height(cairo_t *cr) { (void)cr; return N_BUTTON_H; }
double btn_width(const Text *label) { return 2 * N_BUTTON_PAD + label->w + 2 * N_BORDER; }

/* console.css .btn (border hair, surface, ink -- ink, not blue: only .primary
 * is blue), .primary (action/on-ink), .btn.danger inside a .win (danger-fill,
 * white), .ghost-dng (danger-fg words, no fill), :disabled (row-border fill,
 * muted words, no border), each with :hover. Radius --r-sm, as the console. */
void draw_btn(App *a, cairo_t *cr, Rect b, Text *label, int kind, int id) {
    const Theme *t = a->theme;
    int hov = kind != BTN_OFF && hovered(a, b);
    int pressed = kind != BTN_OFF && id && a->pressed_id == id;
    Rgb bg, line, fg;
    if (kind == BTN_PRIMARY) {
        bg = hov ? t->ink_hover : t->indigo; line = bg; fg = t->on_ink;
    } else if (kind == BTN_SUCCESS) {
        bg = t->success_btn; line = t->success_btn_line; fg = t->on_ink;
    } else if (kind == BTN_DANGER) {
        bg = t->danger_fill; line = t->danger_fill; fg = t->on_ink;
    } else if (kind == BTN_WARN) {
        bg = hov ? t->warning_soft : t->surface; line = t->warn; fg = t->warn;
    } else if (kind == BTN_GHOST_DANGER) {
        bg = hov ? t->danger_soft : t->porcelain; line = bg; fg = t->danger_fg;
    } else if (kind == BTN_OFF) {
        bg = t->track; line = t->track; fg = t->muted;
    } else {
        bg = hov ? t->btn_hover : t->surface;
        line = t->button_line;                    /* :hover changes the fill only */
        fg = t->ink;
    }
    draw_fill_rrect(cr, b, t->radius_sm, bg);
    draw_border_rrect(cr, b, t->radius_sm, line, 1);
    if (hov && kind == BTN_DANGER) {             /* .btn.danger:hover{filter:brightness(.92)} */
        draw_rrect_path(cr, b, t->radius_sm); draw_set_a(cr, (Rgb){ 0, 0, 0 }, .08); cairo_fill(cr);
    }
    if (pressed) {           /* the press, one frame: darker by a step, the label 1px down */
        int filled = kind == BTN_PRIMARY || kind == BTN_DANGER || kind == BTN_SUCCESS;
        draw_rrect_path(cr, b, t->radius_sm); draw_set_a(cr, (Rgb){ 0, 0, 0 }, filled ? .18 : .08); cairo_fill(cr);
    }
    text_draw(cr, label, b.x + (b.w - label->w) / 2, b.y + (b.h - label->h) / 2 + (pressed ? 1 : 0), fg);
    if (kind != BTN_OFF) hit_add(a, b, id);
}

/* ---- inputs --------------------------------------------------------------- */

double field_height(cairo_t *cr) { (void)cr; return N_FIELD_H; }

/* console .win .n-in:focus{border-color:var(--clr-action);
 * box-shadow:0 0 0 3px color-mix(in srgb,var(--clr-action) 25%,transparent)}
 * -- the box, its 1px border in the action colour, and the 3px ring round it. */
void draw_field_box(cairo_t *cr, const Theme *t, Rect r, int focus) {
    draw_fill_rrect(cr, r, RADIUS_SM, t->field);
    if (focus) {
        Rect ring = { r.x - 1.5, r.y - 1.5, r.w + 3, r.h + 3 };   /* the stroke's centreline */
        draw_rrect_path(cr, ring, RADIUS_SM + 1.5);
        draw_set_a(cr, t->indigo, .25); cairo_set_line_width(cr, 3); cairo_stroke(cr);
        draw_border_rrect(cr, r, RADIUS_SM, t->indigo, 1);
    } else {
        draw_border_rrect(cr, r, RADIUS_SM, t->hair, 1);
    }
}

static void field_box(App *a, cairo_t *cr, Rect r, int focus) {
    draw_field_box(cr, a->theme, r, focus);
}

/* console.css input[type=text|password] + :focus outline; .pw-wrap input */
void draw_field(App *a, cairo_t *cr, Rect r, const char *value, int mask,
                const char *placeholder, int id) {
    const Theme *t = a->theme;
    int focus = a->focus == id;
    field_box(a, cr, r, focus);

    char shown[512];
    int empty = value[0] == 0;
    if (mask && !empty) {                       /* one U+2022 per character */
        size_t n = 0;
        for (const char *p = value; *p && n + 4 < sizeof shown; p++) {
            if ((*p & 0xC0) == 0x80) continue;  /* UTF-8 continuation byte */
            memcpy(shown + n, "\xe2\x80\xa2", 3); n += 3;
        }
        shown[n] = 0;
    } else {
        snprintf(shown, sizeof shown, "%s", empty && placeholder ? placeholder : value);
    }

    /* The login fields are LTR (username/password are always ASCII); every
     * other field is RTL (Hebrew image/folder names). */
    int ltr = (id == HIT_USER || id == HIT_PASS);
    /* padding 10px 13px; the password field keeps 62px clear for the eye
     * button -- on the left in an RTL field, on the right in an LTR one. */
    double pad_eye = id == HIT_PASS ? 62 : 13;
    double pad_left = ltr ? 13 : pad_eye;       /* text starts here */
    double pad_right = ltr ? pad_eye : 13;
    double inner_w = r.w - pad_left - pad_right;
    Text v = text_make_field(cr, shown, (int)inner_w, ltr);
    double tx = r.x + pad_left, ty = r.y + (r.h - v.h) / 2;
    cairo_save(cr);
    draw_rrect_path(cr, r, RADIUS_SM); cairo_clip(cr);
    /* ::placeholder has no rule in the CSS; the browser's default is a mid
     * grey, and muted is the token nearest to it. */
    text_draw(cr, &v, tx, ty, empty && placeholder ? t->muted : t->ink);
    if (focus) {
        PangoRectangle strong;
        pango_layout_get_cursor_pos(v.layout, empty ? 0 : (int)strlen(shown), &strong, NULL);
        double cx = tx + strong.x / (double)PANGO_SCALE;
        /* empty field: caret at the start edge -- left for LTR, right for RTL */
        if (empty) cx = ltr ? tx : tx + inner_w;
        cairo_rectangle(cr, cx, ty + strong.y / (double)PANGO_SCALE, 1, strong.height / (double)PANGO_SCALE);
        draw_set(cr, t->ink); cairo_fill(cr);
    }
    cairo_restore(cr);
    text_free(&v);
    hit_add(a, r, id);
}

/* select: the same box as an input (console.css groups them), with the
 * browser's disclosure arrow at the inline end (left in RTL). */
void draw_select(App *a, cairo_t *cr, Rect r, const char *shown, int id) {
    const Theme *t = a->theme;
    field_box(a, cr, r, a->dd_open == id);
    double inner_w = r.w - 13 - 13 - 14;        /* 14 for the arrow */
    Text v = text_make_field(cr, shown, (int)inner_w, 0);
    cairo_save(cr);
    draw_rrect_path(cr, r, RADIUS_SM); cairo_clip(cr);
    text_draw(cr, &v, r.x + 13 + 14, r.y + (r.h - v.h) / 2, t->ink);
    cairo_restore(cr);
    text_free(&v);
    double cx = r.x + 13 + 4, cy = r.y + r.h / 2;
    cairo_move_to(cr, cx - 4, cy - 2); cairo_line_to(cr, cx, cy + 2); cairo_line_to(cr, cx + 4, cy - 2);
    draw_set(cr, t->ink); cairo_set_line_width(cr, 1.5); cairo_stroke(cr);
    hit_add(a, r, id);
}

/* The list a <select> opens is the browser's own widget, not CSS; it is
 * drawn here in the page's language (surface, hair, r-sm, 14px rows). */
void draw_popup(App *a, cairo_t *cr, Rect anchor, const char *const *opts, int n, int sel) {
    const Theme *t = a->theme;
    if (a->dd_open == HIT_ROOM_IMAGE || a->dd_open == HIT_RESTORE_IMAGE) {
        /* owner, 10/10: folder-first. The cells are the folders (name and
         * count); a folder shows "← כל התיקיות" and then its images. An image
         * cell is option <image index + 1> (option 0 is "בחרו אימג'…"), so the
         * click handling in main.c is the one it always was. */
        (void)opts; (void)n;
        int ffirst[MAX_FOLDERS], fcount[MAX_FOLDERS], items[MAX_IMAGES];
        int nf = image_folders(&a->st, ffirst, fcount, MAX_FOLDERS);
        int in = a->pick_in_folder;
        int ni = in ? folder_images(&a->st, a->pick_folder, items, MAX_IMAGES) : 0;
        int cells = in ? ni + 1 : nf;
        int cols = anchor.w >= N_NARROW_W ? 2 : 1;
        double cw = (anchor.w - (cols - 1) * N_IMAGE_GAP) / cols;
        double row_h = N_FIELD_H + 2 * N_IMAGE_PAD;
        Rect box = {anchor.x, anchor.y, anchor.w,
                    ((cells + cols - 1) / cols) * (row_h + N_IMAGE_GAP)};
        draw_box_shadow(cr, box, RADIUS_R, N_SHADOW_Y, N_SHADOW_BLUR, 0,
                        t->shadow_strong, t->shadow_strong_a);
        draw_fill_rrect(cr, box, RADIUS_R, t->surface);
        const char *sel_folder = sel > 0 && sel <= a->st.nimages ? a->st.images[sel - 1].folder : NULL;
        for (int i = 0; i < cells; i++) {
            Rect cell = {box.x + box.w - cw - (i % cols) * (cw + N_IMAGE_GAP),
                         box.y + (i / cols) * (row_h + N_IMAGE_GAP), cw, row_h};
            char label[200]; int id, active;
            if (!in) {
                const char *f = a->st.images[ffirst[i]].folder;
                snprintf(label, sizeof label, "%d · %s (%d)", i + 1, folder_label(f), fcount[i]);
                id = HIT_PICK_FOLDER_BASE + i; active = sel_folder && !strcmp(sel_folder, f);
            } else if (i == 0) {
                snprintf(label, sizeof label, "← כל התיקיות");
                id = HIT_PICK_FOLDERS_BACK; active = 0;
            } else {
                snprintf(label, sizeof label, "%d · %s", i, a->st.images[items[i - 1]].name);
                id = HIT_OPTION_BASE + items[i - 1] + 1; active = sel == items[i - 1] + 1;
            }
            active = active || hovered(a, cell);
            draw_fill_rrect(cr, cell, RADIUS_SM, active ? t->image_selected : t->image_bg);
            draw_border_rrect(cr, cell, RADIUS_SM, active ? t->image_selected_line : t->image_line, N_BORDER);
            Text name = rtl_block_make(cr, N_IMAGE_TITLE, 600, label, cw - 2*N_IMAGE_PAD);
            text_draw(cr, &name, cell.x + N_IMAGE_PAD, cell.y + (cell.h-name.h)/2, in && i == 0 ? t->action_fg : t->ink);
            text_free(&name);
            hit_add(a, cell, id);
        }
        return;
    }
    double row_h = 8 + text_line_height(cr, FONT_SANS, 14) + 8;
    Rect box = { anchor.x, anchor.y + anchor.h + 2, anchor.w, n * row_h + 2 };
    draw_box_shadow(cr, box, RADIUS_SM, 10, 30, -8, t->shadow_strong, t->shadow_strong_a);
    draw_fill_rrect(cr, box, RADIUS_SM, t->surface);
    draw_border_rrect(cr, box, RADIUS_SM, t->hair, 1);
    cairo_save(cr);
    draw_rrect_path(cr, box, RADIUS_SM); cairo_clip(cr);
    for (int i = 0; i < n; i++) {
        Rect row = { box.x + 1, box.y + 1 + i * row_h, box.w - 2, row_h };
        if (i == sel) draw_fill_rrect(cr, row, 0, t->indigo_soft);
        else if (hovered(a, row)) draw_fill_rrect(cr, row, 0, t->hover);
        Text v = text_make_field(cr, opts[i], (int)(row.w - 26), 0);
        text_draw(cr, &v, row.x + 13, row.y + (row.h - v.h) / 2, t->ink);
        text_free(&v);
        hit_add(a, row, HIT_OPTION_BASE + i);
    }
    cairo_restore(cr);
}

/* ---- text roles ------------------------------------------------------------ */

Text rtl_block_make(cairo_t *cr, double px, int weight,
                    const char *s, double w) {
    Text text = text_make(cr, FONT_SANS, px, weight, s, (int)w, DIR_RTL);
    /* CSS block text inherits the document's RTL start edge: physically right. */
    pango_layout_set_alignment(text.layout, PANGO_ALIGN_RIGHT);
    return text;
}

Text label_make(cairo_t *cr, const char *s, double w) { return rtl_block_make(cr, N_LABEL, 500, s, w); }
Text sub_make(cairo_t *cr, const char *s, double w)   { return text_make(cr, FONT_SANS, N_SUB, 400, s, (int)w, DIR_RTL); }
/* ui-audit: errors are drawn at the field box's left edge (text_draw), so they must be right-aligned
 * inside it like every other RTL block (rtl_block_make); text_make alone left-aligned them. */
Text error_make(cairo_t *cr, const char *s, double w) { return rtl_block_make(cr, 12.5, 400, s, w); }

/* ---- the card ---------------------------------------------------------------- */

Head head_make(cairo_t *cr, const char *h3, const char *p, double inner_w) {
    Head hd;
    hd.h3 = rtl_block_make(cr, N_TITLE, 500, h3, inner_w);
    hd.p = rtl_block_make(cr, N_SUB, 400, p, inner_w);
    hd.logo = 0;
    hd.h = 28 + hd.h3.h + N_TITLE_GAP + hd.p.h + 12 + N_BORDER + N_ACTION_TOP;
    return hd;
}

double card_width(double W) {
    return W;
}

static void head_draw(cairo_t *cr, const Theme *t, Head *hd, Rect card) {
    double y = card.y + 28;
    (void)hd->logo;
    if (!hd->h3.layout) return;                 /* a screen that draws its own head (progress) */
    text_draw(cr, &hd->h3, card.x + N_PAD, y, t->ink);
    text_draw(cr, &hd->p, card.x + N_PAD, y + hd->h3.h + N_TITLE_GAP, t->muted);
    double line_y = y + hd->h3.h + N_TITLE_GAP + hd->p.h + 12;
    cairo_rectangle(cr, card.x + N_PAD, line_y, card.w - 2 * N_PAD, N_BORDER);
    draw_set(cr, t->hair); cairo_fill(cr);
    text_free(&hd->h3); text_free(&hd->p);
}

Rect card_frame(App *a, cairo_t *cr, double W, double H, double head_h, Head *hd,
                double body_h, double foot_h, Rect *body, Rect *foot) {
    (void)body_h;
    const Theme *t = a->theme;
    double cw = W;
    double ch = H - head_h - N_STATUS_H;
    Rect card = { 0, head_h, cw, ch };
    cairo_save(cr);
    cairo_rectangle(cr, card.x, card.y, card.w, card.h); cairo_clip(cr);
    head_draw(cr, t, hd, card);
    *body = (Rect){card.x, card.y + hd->h, cw, fmax(0, ch - hd->h - foot_h - 24)};
    *foot = (Rect){card.x, card.y + ch - foot_h - 24, cw, foot_h};
    return card;
}

void card_end(App *a, cairo_t *cr) { (void)a; cairo_restore(cr); }

void body_clip_begin(App *a, cairo_t *cr, Rect body) {
    cairo_save(cr);
    cairo_rectangle(cr, body.x, body.y, body.w, body.h);
    cairo_clip(cr);
    a->clip = body; a->clip_on = 1;
}

void body_clip_end(App *a, cairo_t *cr) { a->clip_on = 0; cairo_restore(cr); }

double foot_height(cairo_t *cr, double content_h) { (void)cr; return N_ACTION_TOP + content_h; }

/* .sfoot: border-top hair, background sunken, padding 14px 22px */
void foot_draw_bg(cairo_t *cr, const Theme *t, Rect foot) {
    (void)cr; (void)t; (void)foot; /* .native-actions shares the panel background. */
}

/* ---- station.css pieces ---------------------------------------------------------- */

/* .native-logo / .native-message-icon / .native-done-icon. Unit coordinates
 * describe vector paths, not font glyphs; IBM Plex remains the only font. */
void draw_native_icon(cairo_t *cr, const Theme *t, Rect box, int kind) {
    Rgb bg = kind == ICON_WARNING ? t->warning_bg : kind == ICON_SUCCESS ? t->success_bg : t->field;
    Rgb line = kind == ICON_WARNING ? t->warning_line : kind == ICON_SUCCESS ? t->success_line : t->mark_line;
    Rgb ink = kind == ICON_WARNING ? t->warning_ink : kind == ICON_SUCCESS ? t->success_ink : t->mark_line;
    double radius = kind >= ICON_WARNING ? box.w / 2 : RADIUS_SM;
    draw_fill_rrect(cr, box, radius, bg);
    draw_border_rrect(cr, box, radius, line, N_BORDER);
    cairo_save(cr);
    cairo_translate(cr, box.x, box.y); cairo_scale(cr, box.w, box.h);
    draw_set(cr, ink); cairo_set_line_width(cr, .045);
    if (kind == ICON_SUCCESS) {
        cairo_move_to(cr,.27,.50); cairo_line_to(cr,.44,.66); cairo_line_to(cr,.75,.33);
    } else if (kind == ICON_WARNING) {
        cairo_move_to(cr,.50,.25); cairo_line_to(cr,.50,.57); cairo_stroke(cr);
        cairo_arc(cr,.50,.72,.027,0,2*M_PI); cairo_fill(cr);
    } else {
        cairo_rectangle(cr,.25,.25,.5,.5);
        cairo_move_to(cr,.34,.43); cairo_line_to(cr,.66,.43);
        cairo_move_to(cr,.34,.60); cairo_line_to(cr,.49,.60);
    }
    cairo_stroke(cr); cairo_restore(cr);
}

/* An ellipse arc in the icon's 24-unit space. The CTM is restored before the
 * stroke, so the line keeps one width all the way round. */
static void icon_ellipse(cairo_t *cr, double cx, double cy, double rx, double ry,
                         double a1, double a2, int negative) {
    cairo_save(cr);
    cairo_translate(cr, cx, cy); cairo_scale(cr, rx, ry);
    if (negative) cairo_arc_negative(cr, 0, 0, 1, a1, a2);
    else          cairo_arc(cr, 0, 0, 1, a1, a2);
    cairo_restore(cr);
}

/* The SVGs of docs/design/apple-mockups/native.html, path by path:
 * stroke-width 1.6, round caps and joins, viewBox 0 0 24 24. */
void draw_menu_icon(cairo_t *cr, Rect box, int kind, Rgb c) {
    cairo_save(cr);
    cairo_translate(cr, box.x, box.y);
    cairo_scale(cr, box.w / 24.0, box.h / 24.0);
    cairo_new_path(cr);
    draw_set(cr, c);
    cairo_set_line_width(cr, 1.6);
    cairo_set_line_cap(cr, CAIRO_LINE_CAP_ROUND);
    cairo_set_line_join(cr, CAIRO_LINE_JOIN_ROUND);
    if (kind == MENU_ICON_CAPTURE) {
        /* M12 11V3 M8 7l4-4 4 4: the arrow up to the library */
        cairo_move_to(cr, 12, 11); cairo_line_to(cr, 12, 3);
        cairo_move_to(cr, 8, 7); cairo_line_to(cr, 12, 3); cairo_line_to(cr, 16, 7);
        /* the disk: a flat cylinder, its top an ellipse rx 8 ry 2 at y 16 */
        cairo_new_sub_path(cr);
        icon_ellipse(cr, 12, 16, 8, 2, 0, 2 * M_PI, 0);
        cairo_move_to(cr, 4, 16); cairo_line_to(cr, 4, 19);
        icon_ellipse(cr, 12, 19, 8, 2, M_PI, 0, 1);       /* the near half of the bottom */
        cairo_line_to(cr, 20, 16);
        cairo_stroke(cr);
    } else if (kind == MENU_ICON_SERVER) {
        draw_rrect_path(cr, (Rect){ 8, 2, 8, 5 }, 1);      /* the server */
        cairo_move_to(cr, 12, 7); cairo_line_to(cr, 12, 10);
        cairo_move_to(cr, 5, 10); cairo_line_to(cr, 19, 10);
        cairo_move_to(cr, 5, 10); cairo_line_to(cr, 5, 15);
        cairo_move_to(cr, 12, 10); cairo_line_to(cr, 12, 15);
        cairo_move_to(cr, 19, 10); cairo_line_to(cr, 19, 15);
        draw_rrect_path(cr, (Rect){ 3, 15, 4, 6 }, 1);     /* three machines */
        draw_rrect_path(cr, (Rect){ 10, 15, 4, 6 }, 1);
        draw_rrect_path(cr, (Rect){ 17, 15, 4, 6 }, 1);
        cairo_stroke(cr);
    } else if (kind == MENU_ICON_DIRECT) {
        draw_rrect_path(cr, (Rect){ 2, 7, 7, 10 }, 1.5);   /* this disk */
        draw_rrect_path(cr, (Rect){ 15, 7, 7, 10 }, 1.5);  /* the drawer */
        cairo_move_to(cr, 10, 12); cairo_line_to(cr, 14, 12);
        cairo_move_to(cr, 12, 10); cairo_line_to(cr, 14, 12); cairo_line_to(cr, 12, 14);
        cairo_stroke(cr);
        /* M5.5 14.5h.01 / M18.5 14.5h.01 with round caps: two LEDs */
        cairo_arc(cr, 5.5, 14.5, 0.8, 0, 2 * M_PI); cairo_fill(cr);
        cairo_arc(cr, 18.5, 14.5, 0.8, 0, 2 * M_PI); cairo_fill(cr);
    } else {
        /* M3 12a9 9 0 1 0 3-6.7L3 8: from (3,12) the long way round a circle
         * of radius 9 centred on (12,12) to (6,5.3), then the arrow head. */
        cairo_move_to(cr, 3, 12);
        cairo_arc_negative(cr, 12, 12, 9, M_PI, atan2(-6.7, -6.0));
        cairo_line_to(cr, 3, 8);
        cairo_move_to(cr, 3, 3); cairo_line_to(cr, 3, 8); cairo_line_to(cr, 8, 8);
        cairo_new_sub_path(cr);
        cairo_arc(cr, 12, 12, 2.5, 0, 2 * M_PI);           /* the disk it turns round */
        cairo_stroke(cr);
    }
    cairo_restore(cr);
}

void draw_focus_ring(App *a, cairo_t *cr, Rect r) {
    Rect o = { r.x - 3, r.y - 3, r.w + 6, r.h + 6 };
    draw_border_rrect(cr, o, a->theme->radius + 3, a->theme->indigo, 2);
}

/* The approved login illustration, expressed in Cairo rather than SVG: the
 * same 800x900 coordinate system, server, multicast branches and machines. */
void draw_station_art(cairo_t *cr, const Theme *t, Rect box, double opacity) {
    static const int machines[][5] = {
        {88,380,0,0,1},{258,380,0,1,0},{428,380,1,0,0},{578,380,0,0,2},
        {88,560,0,0,-1},{258,560,1,0,0},{428,560,0,0,1},{578,560,0,1,0}
    };
    cairo_save(cr);
    cairo_rectangle(cr, box.x, box.y, box.w, box.h); cairo_clip(cr);
    cairo_translate(cr, box.x, box.y);
    cairo_scale(cr, box.w / 800.0, box.h / 900.0);
    cairo_push_group(cr);
    draw_fill_rrect(cr, (Rect){0,0,800,900}, 0, t->art_bg);
    for (int y = 8; y < 900; y += 90) for (int x = 8; x < 800; x += 100) {
        draw_fill_rrect(cr, (Rect){x,y,84,74}, 3, t->art_a);
        draw_fill_rrect(cr, (Rect){x+10,y+12,64,8}, 2, t->art_b);
        draw_fill_rrect(cr, (Rect){x+10,y+26,64,8}, 2, t->art_b);
        draw_fill_rrect(cr, (Rect){x+10,y+40,40,8}, 2, t->art_c);
    }
    draw_set_a(cr, t->art_bg, .55); cairo_rectangle(cr,0,0,800,900); cairo_fill(cr);
    draw_set_a(cr, t->art_bg, .60);
    cairo_move_to(cr,0,0); cairo_line_to(cr,800,0); cairo_line_to(cr,800,300); cairo_close_path(cr); cairo_fill(cr);
    draw_fill_rrect(cr,(Rect){560,120,120,30},3,t->art_server);
    draw_rrect_path(cr,(Rect){560,156,120,30},3);
    draw_set_a(cr,t->art_server,.7); cairo_fill(cr);
    cairo_arc(cr,572,135,3,0,2*M_PI); draw_set(cr,t->warn); cairo_fill(cr);
    cairo_arc(cr,572,171,3,0,2*M_PI); draw_set(cr,t->art_c); cairo_fill(cr);
    cairo_set_line_width(cr,1.5); draw_set(cr,t->art_line);
    cairo_move_to(cr,620,190); cairo_line_to(cr,620,330); cairo_stroke(cr);
    const double dash[] = {6,8}; cairo_set_dash(cr,dash,2,0);
    cairo_move_to(cr,620,330); cairo_line_to(cr,130,330);
    for (int x = 130; x <= 620; x += (x == 470 ? 150 : 170)) {
        cairo_move_to(cr,x,330); cairo_line_to(cr,x,800);
    }
    cairo_stroke(cr); cairo_set_dash(cr,NULL,0,0);
    for (size_t k=0;k<sizeof machines/sizeof machines[0];k++) {
        int x=machines[k][0], y=machines[k][1];
        draw_fill_rrect(cr,(Rect){x,y,84,74},3,t->art_b);
        for (int i=0;i<3;i++) {
            int state=machines[k][2+i]; if (state<0) continue;
            Rgb c=state==1?t->led_ok:state==2?t->warn:t->art_c;
            draw_rrect_path(cr,(Rect){x+10,y+12+i*14,i==2?40:64,8},2);
            draw_set_a(cr,c,state? .55:1); cairo_fill(cr);
        }
    }
    cairo_pop_group_to_source(cr); cairo_paint_with_alpha(cr,opacity);
    cairo_restore(cr);
}

/* console .tag{padding:3px 7px;border:1px solid;border-radius:var(--r);font-size:var(--t-xs)} --
 * .tag.blue / .green / .warn / .danger are the kinds below. 24px high as before. */
double pill_width(cairo_t *cr, const char *label, int dot) {
    Text v = text_make(cr, FONT_SANS, 11, 500, label, 0, DIR_RTL);
    double w = v.w + 16 + (dot ? 13 : 0);
    text_free(&v); return w;
}

double draw_pill(cairo_t *cr, const Theme *t, double right, double y,
                 const char *label, int kind, int dot) {
    Rgb bg = kind == PILL_OK ? t->success_soft : kind == PILL_WARN ? t->warning_soft
           : kind == PILL_BAD ? t->danger_soft : kind == PILL_INFO ? t->info_soft : t->hover;
    Rgb fg = kind == PILL_OK ? t->success_ink : kind == PILL_WARN ? t->warning_ink
           : kind == PILL_BAD ? t->danger_fg : kind == PILL_INFO ? t->action_fg : t->muted;
    Rgb line = kind == PILL_OK ? t->success_line : kind == PILL_WARN ? t->warning_line
             : kind == PILL_BAD ? t->danger_line : kind == PILL_INFO ? t->indigo : t->hair;
    Text v=text_make(cr,FONT_SANS,11,500,label,0,DIR_RTL);
    double w=v.w+16+(dot?13:0), x=right-w;
    draw_fill_rrect(cr,(Rect){x,y,w,24},t->radius,bg);
    draw_border_rrect(cr,(Rect){x,y,w,24},t->radius,line,1);
    text_draw_r(cr,&v,right-8,y+(24-v.h)/2,fg);
    if(dot){ cairo_arc(cr,x+8+3.5,y+12,3.5,0,2*M_PI); draw_set(cr,fg); cairo_fill(cr); }
    text_free(&v); return w;
}

/* repeating-linear-gradient(110deg, a 0 <p1>, b <p1> <period>) as a cairo
 * pattern; b_alpha 0 makes the second band transparent. */
static void stripes(cairo_t *cr, Rect clip_r, Rgb a, double a_alpha, double p1,
                    Rgb b, double b_alpha, double period) {
    double ang = 110.0 * M_PI / 180.0, dx = sin(ang), dy = -cos(ang);
    cairo_pattern_t *p = cairo_pattern_create_linear(clip_r.x, clip_r.y,
                                                     clip_r.x + dx * period, clip_r.y + dy * period);
    double k = p1 / period;
    cairo_pattern_add_color_stop_rgba(p, 0, a.r, a.g, a.b, a_alpha);
    cairo_pattern_add_color_stop_rgba(p, k, a.r, a.g, a.b, a_alpha);
    cairo_pattern_add_color_stop_rgba(p, k, b.r, b.g, b.b, b_alpha);
    cairo_pattern_add_color_stop_rgba(p, 1, b.r, b.g, b.b, b_alpha);
    cairo_pattern_set_extend(p, CAIRO_EXTEND_REPEAT);
    cairo_rectangle(cr, clip_r.x, clip_r.y, clip_r.w, clip_r.h);
    cairo_set_source(cr, p);
    cairo_fill(cr);
    cairo_pattern_destroy(p);
}

/* console.css .bar (track, overflow hidden) + .bar i (led-write, radius 5);
 * station.css .big-bar (height 14, radius 8); progress.css for the two
 * unknown states. The indeterminate stripe is animated in the browser; here
 * it stands at its starting position. */
void draw_big_bar(cairo_t *cr, const Theme *t, Rect r, int pct, int moving) {
    draw_fill_rrect(cr, r, RADIUS_SM, t->track);
    cairo_save(cr);
    draw_rrect_path(cr, r, RADIUS_SM); cairo_clip(cr);
    if (pct >= 0) {
        double w = r.w * (pct > 100 ? 100 : pct) / 100.0;
        if (w > 0) draw_fill_rrect(cr, (Rect){ r.x, r.y, w, r.h }, RADIUS_SM, t->led_write);
    } else if (moving) {
        stripes(cr, (Rect){ r.x, r.y, r.w * 0.40, r.h }, t->led_write, 1, 8, t->info, 1, 16);
    } else {
        stripes(cr, r, t->bar_idle, 0.25, 2, t->bar_idle, 0, 8);
    }
    cairo_restore(cr);
}

/* .native-bar{height:5px;background:#0b1115;border-radius:2px;overflow:hidden}
 * .native-bar i{background:var(--blue2)} -- the track is the same near-black
 * as .native-progressbar (track); the fill colour is the caller's (blue2 or
 * --yellow for a warn node). Radius 2 = RADIUS_SM - 1, the CSS value. */
void draw_thin_bar(cairo_t *cr, const Theme *t, Rect r, int pct, Rgb fill) {
    draw_fill_rrect(cr, r, 2, t->track);
    if (pct > 0) {
        cairo_save(cr);
        draw_rrect_path(cr, r, 2); cairo_clip(cr);
        draw_fill_rrect(cr, (Rect){ r.x, r.y, r.w * (pct > 100 ? 100 : pct) / 100.0, r.h }, 2, fill);
        cairo_restore(cr);
    }
}

/* console .win .confirm-box{padding:10px 12px;border-radius:var(--r-sm);
 * background:var(--clr-warning-soft);color:var(--clr-text);font-size:var(--t-sm)}
 * -- a soft fill, no border. */
Text alert_make(cairo_t *cr, const char *s, double w) {
    return rtl_block_make(cr, N_ALERT_FONT, 400, s, w - 2 * N_ALERT_PAD_X);
}
double alert_height(const Text *body) { return 2 * N_ALERT_PAD_Y + body->h; }
void draw_alert(cairo_t *cr, const Theme *t, Rect r, Text *body) {
    draw_fill_rrect(cr, r, RADIUS_SM, t->alert_bg);
    text_draw(cr, body, r.x + N_ALERT_PAD_X, r.y + N_ALERT_PAD_Y, t->alert_ink);
}

/* .cls-bars: 4 x (height 3, radius 2, hair; .on ink), gap 5, flex:1 */
void draw_cls_bars(cairo_t *cr, const Theme *t, double x, double y, double w, int current) {
    double bw = (w - 3 * 5) / 4;
    for (int i = 0; i < 4; i++)
        draw_fill_rrect(cr, (Rect){ x + i * (bw + 5), y, bw, 3 }, 2, i < current ? t->ink : t->hair);
}

/* #toast: fixed bottom 22, centred; ink on on-ink; padding 10px 18px;
 * radius 10; 13px; box-shadow 0 10px 30px -8px shadow-strong */
void draw_toast(App *a, cairo_t *cr, double W, double H) {
    const char *message = a->toast[0] ? a->toast :
                          a->st.stale && !a->st.fail_server ? "הסוכן לא מעדכן מצב" : NULL;   /* #1741: written once, by no agent */
    if (!message) return;
    const Theme *t = a->theme;
    Text v = text_make(cr, FONT_SANS, 13, 400, message, (int)(W * 0.8), DIR_RTL);
    Rect b = { (W - (v.w + 36)) / 2, H - 22 - (v.h + 20), v.w + 36, v.h + 20 };
    draw_box_shadow(cr, b, 10, 10, 30, -8, t->shadow_strong, t->shadow_strong_a);
    draw_fill_rrect(cr, b, 10, t->ink);
    text_draw(cr, &v, b.x + 18, b.y + 10, t->on_ink);
    text_free(&v);
}

/* ---- ports of the JS helpers --------------------------------------------------------- */
double metric_height(cairo_t *cr) {
    return 2*N_METRIC_PAD + text_line_height(cr,FONT_SANS,N_METRIC_LABEL)
           + N_LABEL_GAP + text_line_height(cr,FONT_SANS,N_METRIC_VALUE);
}

void draw_metric(cairo_t *cr, const Theme *t, Rect box, const char *label, const char *value) {
    draw_fill_rrect(cr,box,RADIUS_SM,t->metric);     /* .statbox{border-radius:var(--r-sm)} */
    draw_border_rrect(cr,box,RADIUS_SM,t->metric_line,N_BORDER);
    Text l = rtl_block_make(cr,N_METRIC_LABEL,400,label,box.w-2*N_METRIC_PAD);
    Text v = rtl_block_make(cr,N_METRIC_VALUE,600,value,box.w-2*N_METRIC_PAD);
    /* ui-audit: metric_height() is one line of the value. A value that wraps (1024x768, "טרם נקראו בייטים ·
     * הסך לא ידוע" in a third of the card) ran out of its box onto the note below; the same words at 70% fit. */
    if (v.h > text_line_height(cr,FONT_SANS,N_METRIC_VALUE) * 1.5) {
        text_free(&v);
        v = rtl_block_make(cr,N_METRIC_VALUE * 0.7,600,value,box.w-2*N_METRIC_PAD);
    }
    text_draw(cr,&l,box.x+N_METRIC_PAD,box.y+N_METRIC_PAD,t->muted);
    text_draw(cr,&v,box.x+N_METRIC_PAD,box.y+N_METRIC_PAD+l.h+N_LABEL_GAP,t->ink);
    text_free(&l); text_free(&v);
}


void fmt_bytes(char *out, size_t n, double v) {
    static const char *U[] = { "B", "KB", "MB", "GB", "TB" };
    if (!(v > 0)) { snprintf(out, n, "0"); return; }
    int i = 0;
    while (v >= 1024 && i < 4) { v /= 1024; i++; }
    if (v >= 100) snprintf(out, n, "%.0f %s", v, U[i]);
    else          snprintf(out, n, "%.1f %s", v, U[i]);
}

void progress_label(char *out, size_t n, int pct, int moving, int partition) {
    if (pct >= 0) {
        if (partition > 0) snprintf(out, n, "%d%% · מחיצה %d (בלוקים)", pct > 100 ? 100 : pct, partition);
        else               snprintf(out, n, "%d%%", pct > 100 ? 100 : pct);
    } else {
        snprintf(out, n, "%s", moving ? "נקראו בייטים · הסך לא ידוע" : "טרם נקראו בייטים · הסך לא ידוע");
    }
}
