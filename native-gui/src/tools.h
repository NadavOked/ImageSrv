/* #649 (mockup 14, approved by Nadav 31/10): the toolbox's pieces shared by
 * screens_tools.c (the files, the run view and the result), screens_tools_home.c
 * (the entry button, the home's tiles and search, a category's list) and
 * tools_nav.c (the keys and clicks). ui.h has the data. */
#ifndef IMAGESRV_TOOLS_H
#define IMAGESRV_TOOLS_H
#include <cairo.h>
#include "input.h"
#include "text.h"
#include "ui.h"

/* The ten categories of mockup 14 page 2, in its order: the closed vocabulary
 * of the tools file's domain field. A domain the GUI does not know is shown
 * under "other" -- never dropped. */
enum { TCAT_DISK, TCAT_WIPE, TCAT_NET, TCAT_WINDOWS, TCAT_BOOT, TCAT_HW, TCAT_RECOVER,
       TCAT_SEC, TCAT_OTHER, TCAT_COMBO, TOOL_NCATS };
typedef struct { const char *key, *title, *situation; } ToolCat;
extern const ToolCat TOOL_CATS[TOOL_NCATS];
int tool_cat_of(const Tool *t);
int tools_cat_count(const App *a, int cat);
int tools_cat_list(const App *a, int cat, int *out);   /* indices into a->tools, file order */
int tools_search(const App *a, int *out);              /* the tools whose title or description has tool_q */
int tools_list_now(const App *a, int *out);            /* what the list on screen shows: the category or the search */
int tools_tile_step(const App *a, int from, int key);  /* the tile an arrow moves to (5 a row, the first at the right) */
int tools_first_tile(const App *a);                    /* -1 = no category has a tool */

/* The entry (mockup 14 page 1): 0 = no button here, 1 = the button works,
 * 2 = it is drawn grey and F3 does nothing (a write is running). Asked of the
 * screen under the toolbox, so app_route can close it when the write starts. */
int tools_entry(const App *a, Screen under);
/* "כלים F3" in the top bar at x (its left edge); returns its width */
double draw_tools_btn(App *a, cairo_t *cr, double x, int state);
double tools_hbtn_width(cairo_t *cr);

/* .n-btn (38px): the right edge in, the width out; key = its .kbd chip or NULL */
enum { TB_PLAIN, TB_PRIMARY, TB_DANGER, TB_OFF };
double tools_btn(App *a, cairo_t *cr, double x_right, double y, const char *label, const char *key, int kind, int id);
double tools_btn_w(cairo_t *cr, const char *label, const char *key);
/* risk words and the tag box (console .risk) */
const char *tool_risk_word(int level);
double tool_tag(App *a, cairo_t *cr, double x_right, double cy, int level, const char *word);   /* returns its width */
/* the disks a disk:<label> argument picks from: the build machine's disk=, else the
 * local clone's lc_disk=, else the cloner's idle drawers -- whatever this screen has */
typedef struct { char dev[32], label[SLOT_NAME_MAX + 16], model[80], on[200]; unsigned long long size; } ToolDisk;
int tools_disks(const App *a, ToolDisk *out, int max);
/* a one-line text cut with "…" to w px, or a block of at most <lines> lines */
Text tools_line(cairo_t *cr, Font f, double px, int weight, const char *s, double w, int lines);
/* the shared column: x0..x0+cw, the title row (crumb, h1, sub, side) -- returns the y under it */
double tools_col(double W, double *x0, double *cw, int wide);
double tools_title(App *a, cairo_t *cr, double x0, double cw, double y, const char *crumb, const char *h1,
                   const char *sub, int level_tag, int esc_btn);

void tools_home_view(App *a, cairo_t *cr, double W, double H, double head_h);
void tools_cat_view(App *a, cairo_t *cr, double W, double H, double head_h);
/* a list of tools with its pager (a category, or the search's results) */
void tools_list_draw(App *a, cairo_t *cr, double x0, double cw, double y, double bottom, const int *items, int n, int with_cat);

/* tools_nav.c: the toolbox's keys and clicks. What needs the bridge comes back
 * to main.c as an action (it owns the records); the rest is done here. */
enum { TA_NONE, TA_EXIT, TA_OPEN, TA_RUN, TA_AGAIN };
int tools_nav(App *a, const Event *e, int hit);   /* hit = the click's id, -1 for a key */
void tools_enter(App *a);                         /* the toolbox opens: the home, the first tile */
const char *tools_status_keys(const App *a);      /* the status bar's keys for the view on screen */
#endif
