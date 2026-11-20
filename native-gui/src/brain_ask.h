/* #3654 (mockup 18/11, design-ask-1118/native-ask.html): the brain's question on the machine's own screen.
 *
 * The server asks (server/brain/ask.py) and the server's clock ends the question; this screen decides
 * nothing. The agent keeps the hello's `asks` (agent/lib/core/ask.sh ask_keep) and copies them into the
 * state file on every screen (ask_gui_records, through gui_hello_state):
 *
 *   bask=<id>|<state>|<n options>|<columns>|<ttl s>|<deadline epoch>|<safe default>|<answer>|<answered role>|
 *        <answered by>|<closed epoch>|<admin too 0/1>|<title>|<detail>
 *   bask_opt=<id>|<option>|<destructive 0/1>|<label>|<verb>|<consequence>|<outcome>   (n of them, in order)
 *
 * The deadline is this machine's clock: the moment of the hello plus the server's expires_in_s, so a machine
 * whose clock is off still counts the server's minutes. All or nothing per question: a field that does not
 * parse or fit, a missing option, a destructive safe default -- the question is not drawn, and the state
 * line says one arrived broken (no answer = the server's safe default, which is never destructive).
 *
 * What is drawn (screens_brain_ask.c): an open question that was not closed here; after the server says it
 * closed -- answered here, answered in the console, or expired -- its result, for 5 s or until Enter, and
 * then never again. A question that was never on the screen while open does not pop up as a result. The
 * answer goes out as `brain-ask|<id>|<option>` (the bridge posts it: ask_gui_answer); the screen waits
 * for the server's word on what happened, it never draws "registered" on its own. */
#ifndef IMAGESRV_BRAIN_ASK_H
#define IMAGESRV_BRAIN_ASK_H
#include <cairo.h>
#include "input.h"
#include "ui.h"

#define BASK_RESULT_S 5     /* the result closes by itself (mockup n4-n6) */
#define BASK_RESEND_S 8     /* an answer that brought no word from the server may be sent again */

/* state.c: one key=value line; 1 = it was a bask record. bask_settle after the last line: drops what is
 * not whole and returns how many were dropped (st.bask_broken). */
int bask_state_line(State *s, const char *key, char *val);
int bask_settle(State *s);

/* main.c: after every reload (1 = the question on the screen was cancelled -- main toasts it); every
 * wake (1 = the result closed by itself); the keys and clicks (1 = taken). */
int bask_after_reload(App *a);
int bask_tick(App *a);
int bask_modal(App *a, const Event *e);
/* the question on the screen now, NULL = none; visible = and nothing else is in front of it */
const Bask *bask_current(const App *a);
int bask_visible(const App *a);
int bask_open_count(const App *a);
int bask_default_index(const Bask *b);
/* the status bar (screens.c); NULL = no question on the screen */
const char *bask_status_word(const App *a, Rgb *dot, char *buf, size_t n);
const char *bask_status_keys(const App *a, char *buf, size_t n);
/* --png: the cards "brain-ask" (the state's question, else the mockup's n1) and "brain-ask-pick" (its
 * last destructive option chosen -- the red button, n2); every other card shows the state's question too */
void bask_png(App *a, const State *base, const char *card);

/* screens_brain_ask.c -- the dialog, and the status bar's keys as the mockup draws them (a chip, then its word),
 * from <x> at the left end of the bar */
void draw_brain_ask(App *a, cairo_t *cr, double W, double H);
void bask_draw_keys(App *a, cairo_t *cr, double x, double cy);
#endif
