/* Keyboard and pointer straight from evdev (/dev/input/event*): no
 * libinput, no xkbcommon. Two fixed keymaps: US, and the Israeli standard
 * layout SI-1452 (#1245). Every character event carries both -- ch is the
 * US character (the keys' shortcuts and the English-only fields use it),
 * he the same key in SI-1452 -- and main.c picks by the typing language,
 * which Alt+Shift switches (KEYSYM_LAYOUT). Keyboards are grabbed
 * (EVIOCGRAB) so the password does not also echo on the text VT. */
#ifndef IMAGECTL_INPUT_H
#define IMAGECTL_INPUT_H

#include <poll.h>

typedef enum { UIEV_NONE = 0, UIEV_CHAR, UIEV_KEY, UIEV_MOVE, UIEV_CLICK, UIEV_SCROLL } EvType;
enum { KEYSYM_TAB = 1, KEYSYM_BACKTAB, KEYSYM_ENTER, KEYSYM_BACKSPACE, KEYSYM_ESC,
       KEYSYM_UP, KEYSYM_DOWN, KEYSYM_LEFT, KEYSYM_RIGHT,
       KEYSYM_LAYOUT /* #1245: Alt+Shift -- switch the typing language */,
       KEYSYM_CAPS,  /* #1203: Caps Lock pressed; Event.caps is the new state */
       KEYSYM_F1, KEYSYM_F2, KEYSYM_F3, KEYSYM_F4, KEYSYM_F5, KEYSYM_F6,   /* #1203: the */
       KEYSYM_F7, KEYSYM_F8, KEYSYM_F9, KEYSYM_F10, KEYSYM_F11, KEYSYM_F12, /* server screen */
       KEYSYM_VT     /* #1362: Alt+Fn / Ctrl+Alt+Fn -- switch to VT Event.vt; the
                      * grab hides the chord from the kernel, so it is asked for here */,
       KEYSYM_DELETE /* #1386: "בטל את דיסק N" -- Del, never Enter */,
       KEYSYM_POWER  /* #1918: the machine's power button (ACPI "Power Button",
                      * or a keyboard's power key) -- a short press */ };

typedef struct {
    EvType type;
    int ch;             /* EV_CHAR: printable ASCII */
    int key;            /* EV_KEY: KEYSYM_* */
    int delta;          /* UIEV_SCROLL: positive is up */
    double x, y;        /* EV_MOVE / EV_CLICK, screen pixels */
    int is_touch;       /* absolute device: no cursor to draw */
    int he;             /* EV_CHAR (#1245): the same key in SI-1452, a Unicode code
                         * point -- a Hebrew letter on the base level; == ch with
                         * Shift and on the keys the two layouts share */
    int caps;           /* #1203: Caps Lock when the event was read (1 = on). ch is
                         * NOT changed by it -- the station screens ignore Caps Lock
                         * as before; the server screen applies it (dcui_token) */
    int vt;             /* KEYSYM_VT: 1..12 */
} Event;

typedef struct Input Input;

/* Opens every keyboard, mouse and touch device it finds. Always returns a
 * usable handle -- even with no device yet: a USB keyboard/mouse can appear a
 * second or two after boot, and input_rescan() picks it up. Returns NULL only
 * on allocation failure. */
Input *input_open(int screen_w, int screen_h);
/* Re-scan /dev/input for devices that appeared since (hot-plug, or a late USB
 * keyboard). Self-throttled to at most once a second; cheap to call every
 * loop. Already-open devices are left untouched. */
void input_rescan(Input *in);
/* #1475: a remote monitor will create its uinput device (agent/monitor.c's
 * UINPUT_NAME) -- do not stop rescanning before it is grabbed too. */
void input_want_remote(Input *in);
int input_fill_pollfds(Input *in, struct pollfd *out, int max);
/* Read whatever is ready; then drain with input_next(). */
void input_pump(Input *in, const struct pollfd *fds, int n);
int input_next(Input *in, Event *out);
/* #1203: Caps Lock now (1 = on), from the LED at open and every press since. */
int input_caps(const Input *in);
/* #1362: on=1 while another VT is in front -- the keyboards are let go so
 * the kernel types into that VT (and sees Alt+F1 back), and whatever is read
 * meanwhile is dropped, never turned into keys here. on=0 grabs them again. */
void input_suspend(Input *in, int on);
void input_close(Input *in);

#endif
