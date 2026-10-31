/* #1386 (Nadav 17/10): "בטל דיסק זה" -- CancelDlg in ui.h says what it holds.
 * One window for the cloner's "שיכפול במקביל" and the build machine's
 * distribution screen, after the approved mockup
 * (docs/design/native/cancel-drawer-1386/mockup.html). */
#ifndef IMAGESRV_CANCELDLG_H
#define IMAGESRV_CANCELDLG_H
#include <cairo.h>
#include "input.h"
#include "ui.h"

/* The writing drawers each screen offers (screens_cloner.c / screens_room.c):
 * fills out[], returns how many. A row's "בטל…" is HIT_BAY_CANCEL_BASE + its
 * index here, so a click and the window name the same drawer. */
int cloner_cancel_picks(const App *a, CancelPick *out, int max);
int room_cancel_picks(const App *a, CancelPick *out, int max);

/* Open over the screen: <sel> is the drawer already chosen (a row's button,
 * the cloner's drawer key); <picker> shows the choice among the writers (the
 * build machine's X). Nothing to cancel = stays closed, returns 0. */
int cdlg_open(App *a, int build, int sel, int picker);
/* While open every key, click and character is its own (returns 1); a pointer
 * move returns 0. Del (or the red button) confirms: *done is the chosen
 * drawer and the window closes. Esc / "המשך לכתוב" closes with done->dev "". */
int cdlg_handle(App *a, const Event *e, CancelPick *done);
/* After a state reload: the chosen drawer stopped writing = the window closes
 * (a click a second later must not cancel a disk that already finished). */
void cdlg_after_reload(App *a);
void cdlg_draw(App *a, cairo_t *cr, double W, double H);
#endif
