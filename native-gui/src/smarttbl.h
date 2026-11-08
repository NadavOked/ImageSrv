/* #1859 (Nadav 07/11, mockup docs/design/mockups-1107b/1859-smart-table.html, "מוצע ב"): every SMART
 * attribute of a disk in Hebrew, in a drawer from the side of the cloner's wait screen. Opened by a
 * click on a drawer (HIT_SMT_BASE + the disk's index) or by S; read only -- no button writes, and the
 * disk's colour never changes from here. The rows are the agent's (csmart= / csmartrow=, smarttable.sh):
 * the GUI knows no table. */
#ifndef IMAGESRV_SMARTTBL_H
#define IMAGESRV_SMARTTBL_H
#include <cairo.h>
#include "input.h"
#include "ui.h"
#include "text.h"

/* the head of <dev>'s table, NULL = the agent wrote none (SMART not read): no line, no drawer */
const SmHead *smt_head(const State *s, const char *dev);
/* "כל מאפייני ה-SMART (21) ›" -- the line under a drawer's name, and the S key beside it */
Text smt_aff_make(cairo_t *cr, const SmHead *h);
void smt_aff_draw(App *a, cairo_t *cr, Text *t, double right, double y);
double smt_aff_width(const Text *t);
/* while open every key, click and scroll is the drawer's -- 1 for those; a pointer move is 0. Closed:
 * S, or a click on a drawer with a table, opens it (1), else 0 */
int smt_handle(App *a, const Event *e);
/* over the screen, under its dialogs and the status bar; nothing when closed */
void smt_draw(App *a, cairo_t *cr, double W, double H, double head_h);
/* the status bar's keys while it is open, "" when closed */
const char *smt_keys(const App *a);
/* --png: open the first table of the state file -- or, with none, the mockup's sample */
void smt_png(App *a);
#endif
