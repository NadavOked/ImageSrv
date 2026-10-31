#include <string.h>
#include <strings.h>
#include <stdio.h>
#include <stdlib.h>
#include "text.h"

/* mockup 10/10: no text below 11px -- the 9-10px labels (SMART cells, drawer
 * slots, disk paths) were unreadable on a 1080p screen. */
#define TEXT_MIN_PX 11.0

static PangoFontDescription *font_desc(Font font, double px, int weight) {
    if (px < TEXT_MIN_PX) px = TEXT_MIN_PX;
    PangoFontDescription *d = pango_font_description_new();
    pango_font_description_set_family(d, font == FONT_MONO ? FONT_MONO_FAMILY
                                                           : FONT_SANS_FAMILY);
    /* absolute size = device units, i.e. CSS px at 1x */
    pango_font_description_set_absolute_size(d, px * PANGO_SCALE);
    pango_font_description_set_weight(d, weight);      /* PangoWeight == CSS number */
    return d;
}

static Text layout_new(cairo_t *cr, Font font, double px, int weight, Dir dir) {
    Text t;
    t.layout = pango_cairo_create_layout(cr);
    PangoContext *ctx = pango_layout_get_context(t.layout);
    /* Base direction of the paragraph when it has no strong character
     * (dir="rtl" on <html>); auto-dir lets a Latin-first paragraph go LTR. */
    pango_context_set_base_dir(ctx, dir == DIR_RTL ? PANGO_DIRECTION_RTL
                                                   : PANGO_DIRECTION_LTR);
    pango_layout_set_auto_dir(t.layout, TRUE);
    pango_layout_context_changed(t.layout);

    PangoFontDescription *d = font_desc(font, px, weight);
    pango_layout_set_font_description(t.layout, d);
    pango_font_description_free(d);
    t.w = t.h = 0;
    return t;
}

/* IMAGESRV_GUI_TEXTLOG=<file>: every string laid out is appended, one per line -- what a test
 * asserts on ("what the screen SAYS"), since a PNG cannot be read back. Off unless set. */
static void textlog_line(const char *line) {
    static const char *path; static int init;
    if (!init) { path = getenv("IMAGESRV_GUI_TEXTLOG"); init = 1; }
    if (!path || !path[0]) return;
    FILE *f = fopen(path, "a");
    if (!f) return;
    fprintf(f, "%s\n", line);
    fclose(f);
}
static void textlog(PangoLayout *l) { textlog_line(pango_layout_get_text(l)); }
/* --png: "@@ <card>-<theme>" before a card is drawn, so a test reads one card's words */
void text_log_mark(const char *card, const char *theme) {
    char line[160]; snprintf(line, sizeof line, "@@ %s-%s", card, theme); textlog_line(line);
}

static void layout_finish(Text *t, int width_px) {
    if (width_px > 0) {
        pango_layout_set_width(t->layout, width_px * PANGO_SCALE);
        pango_layout_set_wrap(t->layout, PANGO_WRAP_WORD_CHAR);
        /* With auto-dir on, PANGO_ALIGN_LEFT means "start": Pango swaps
         * LEFT/RIGHT for a line whose resolved direction is RTL
         * (pango-layout.c get_alignment). So this is CSS text-align:start. */
        pango_layout_set_alignment(t->layout, PANGO_ALIGN_LEFT);
    }
    pango_layout_get_pixel_size(t->layout, &t->w, &t->h);
    textlog(t->layout);
}

Text text_make_markup(cairo_t *cr, Font font, double px, int weight,
                      const char *markup, int width_px, Dir dir) {
    Text t = layout_new(cr, font, px, weight, dir);
    pango_layout_set_markup(t.layout, markup, -1);
    layout_finish(&t, width_px);
    return t;
}

char *text_escape(const char *utf8, char *out, size_t n) {
    char *e = g_markup_escape_text(utf8, -1);
    snprintf(out, n, "%s", e);
    g_free(e);
    return out;
}

double text_baseline(const Text *t) {
    return pango_layout_get_baseline(t->layout) / (double)PANGO_SCALE;
}

Text text_make(cairo_t *cr, Font font, double px, int weight,
               const char *utf8, int width_px, Dir dir) {
    Text t = layout_new(cr, font, px, weight, dir);
    pango_layout_set_text(t.layout, utf8, -1);
    layout_finish(&t, width_px);
    return t;
}

Text text_make_ltr(cairo_t *cr, Font font, double px, int weight,
                   const char *utf8, int width_px) {
    Text t = layout_new(cr, font, px, weight, DIR_LTR);
    pango_layout_set_auto_dir(t.layout, FALSE);   /* a Hebrew-first value stays LTR */
    pango_layout_set_text(t.layout, utf8, -1);
    layout_finish(&t, width_px);
    if (width_px > 0) pango_layout_set_alignment(t.layout, PANGO_ALIGN_RIGHT);
    return t;
}

void text_draw(cairo_t *cr, Text *t, double x, double y, Rgb c) {
    cairo_move_to(cr, x, y);
    cairo_set_source_rgb(cr, c.r, c.g, c.b);
    pango_cairo_show_layout(cr, t->layout);
}

Text text_make_field(cairo_t *cr, const char *utf8, int width_px, int ltr) {
    /* An <input>: auto-dir is OFF so text-align:start is a fixed physical edge
     * regardless of the value. RTL fields (Hebrew image/folder names) align
     * right; the login fields pass ltr=1 -- username and password are always
     * ASCII (the password carries digits and symbols), so they read and align
     * left, with the caret at the left edge. */
    Text t;
    t.layout = pango_cairo_create_layout(cr);
    pango_context_set_base_dir(pango_layout_get_context(t.layout),
                               ltr ? PANGO_DIRECTION_LTR : PANGO_DIRECTION_RTL);
    pango_layout_set_auto_dir(t.layout, FALSE);
    pango_layout_context_changed(t.layout);
    PangoFontDescription *d = font_desc(FONT_SANS, 13, 400);        /* .win .n-in: font-size var(--t-base) */
    pango_layout_set_font_description(t.layout, d);
    pango_font_description_free(d);
    pango_layout_set_text(t.layout, utf8, -1);
    pango_layout_set_width(t.layout, width_px * PANGO_SCALE);
    pango_layout_set_alignment(t.layout, ltr ? PANGO_ALIGN_LEFT : PANGO_ALIGN_RIGHT);
    pango_layout_get_pixel_size(t.layout, &t.w, &t.h);
    return t;
}

void text_draw_r(cairo_t *cr, Text *t, double x_right, double y, Rgb c) {
    /* t->w is the text's span, not the box: for a block with a set width
     * whose lines are right-aligned (every RTL block here), Pango's logical
     * rect starts at x = width - span (pango_layout_get_extents_internal,
     * "the union of the horizontal extents of all the lines"), and the
     * glyphs are drawn at that offset from the current point. So the right
     * edge of the drawn text is x + logical.x + logical.width -- use that,
     * else a wrapped block overflows the box by (width - span). */
    PangoRectangle lg;
    pango_layout_get_pixel_extents(t->layout, NULL, &lg);
    text_draw(cr, t, x_right - (lg.x + lg.width), y, c);
}

void text_free(Text *t) {
    if (t->layout) g_object_unref(t->layout);
    t->layout = NULL;
}

double text_line_height(cairo_t *cr, Font font, double px) {
    Text t = text_make(cr, font, px, 400, "Xg\xd7\x9c", 0, DIR_LTR);   /* "Xgל" */
    double h = t.h;
    text_free(&t);
    return h;
}

int text_fonts_present(char *missing, size_t n) {
    PangoFontMap *map = pango_cairo_font_map_get_default();
    PangoFontFamily **fam = NULL;
    int count = 0, have_sans = 0, have_mono = 0;
    pango_font_map_list_families(map, &fam, &count);
    for (int i = 0; i < count; i++) {
        const char *name = pango_font_family_get_name(fam[i]);
        if (strcasecmp(name, FONT_SANS_FAMILY) == 0) have_sans = 1;
        if (strcasecmp(name, FONT_MONO_FAMILY) == 0) have_mono = 1;
    }
    g_free(fam);
    if (have_sans && have_mono) return 1;
    snprintf(missing, n, "%s%s%s",
             have_sans ? "" : FONT_SANS_FAMILY,
             (!have_sans && !have_mono) ? ", " : "",
             have_mono ? "" : FONT_MONO_FAMILY);
    return 0;
}
