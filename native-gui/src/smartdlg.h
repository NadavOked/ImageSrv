/* 16/10 (Nadav): the SMART warning window -- SmartDlg in ui.h says what it
 * holds. One window for every place that writes to, or reads from, a disk
 * with a SMART warning: the cloner's prompt, the restore onto this machine's
 * disk, the capture, and the local clone's target and source. */
#ifndef IMAGECTL_SMARTDLG_H
#define IMAGECTL_SMARTDLG_H
#include <cairo.h>
#include "input.h"
#include "ui.h"

/* Open the window. <verdict>/<reason> are the agent's words (smart.sh:
 * fail/warn/failed_last, health_failed/pending_sectors/...); <label> is the
 * disk's number by the mapping ("דיסק 2") or its device. "" model/serial
 * are said as "לא דווח". The focus starts on "דלג". */
void sdlg_open(App *a, int kind, int ref, const char *dev, const char *label, const char *verdict,
               const char *reason, const char *model, const char *serial);
/* While the window is open every key, click, character and scroll is its
 * own -- returns 1 for those; a pointer move returns 0 (the pointer still
 * moves). When the person decided, the window closes and *done is a copy
 * of it with done->go = 1 for "כתוב בכל זאת", 0 for "דלג"; otherwise
 * done->kind is SDLG_NONE. Enter takes the focused button, Esc is "דלג". */
int sdlg_handle(App *a, const Event *e, SmartDlg *done);
/* #2017 (Nadav 22/10): an orange source of the local clone -- a notice, one button
 * "המשך" (Enter/Esc/click all hand back go=1). A red source keeps the question. */
int sdlg_notice(const SmartDlg *d);
/* Over the screen and its dialogs, under the status bar; nothing when closed. */
void sdlg_draw(App *a, cairo_t *cr, double W, double H);
#endif
