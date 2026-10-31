#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <sys/ioctl.h>
#include <linux/input.h>
#include "input.h"

#define MAX_DEV 16
#define QUEUE 64
#define PTR_GAIN 3      /* relative-pointer speed multiplier (a 1:1 mouse felt slow) */
#define BITS_PER_LONG (8 * sizeof(long))
#define NBITS(x) (((x) / BITS_PER_LONG) + 1)
#define test_bit(bit, arr) (((arr)[(bit) / BITS_PER_LONG] >> ((bit) % BITS_PER_LONG)) & 1)

/* DEV_ABSPTR (#949): an absolute pointer that is not a touchscreen --
 * ABS_X/ABS_Y + BTN_LEFT, no BTN_TOUCH -- i.e. imagesrv-monitor's uinput
 * device, a tablet, a VM pointer. Position maps 1:1 like touch, but the
 * cursor stays drawn: the person at the screen must see where the remote
 * operator is (owner, again 16/10 in #1475: one shared cursor). */
enum { DEV_KEYBOARD = 1, DEV_MOUSE = 2, DEV_TOUCH = 4, DEV_ABSPTR = 8,
       DEV_POWER = 16 /* #1918: KEY_POWER -- the ACPI "Power Button" (button.ko) is a device of its own */ };

/* #1475: agent/monitor.c's UINPUT_NAME, verbatim. The rescan keeps looking
 * for this device when a monitor serves the screen (input_want_remote). */
#define MONITOR_UINPUT_NAME "ImageSrv remote monitor"

typedef struct {
    int fd, kinds;
    int abs_min_x, abs_max_x, abs_min_y, abs_max_y;
    int shift, alt;                         /* alt: #1245, the Alt+Shift chord */
    int remote;                             /* #1475: MONITOR_UINPUT_NAME */
    char path[32];                          /* so a rescan skips it */
} Dev;

struct Input {
    Dev dev[MAX_DEV]; int ndev;
    int w, h;
    double px, py;                          /* pointer */
    int moved, touch_moved;
    Event q[QUEUE]; int qh, qt;
    time_t last_rescan;
    int scan_done;      /* stop rescanning once a keyboard AND a mouse are here */
    int caps;           /* #1203: Caps Lock -- the grab hides it from the kernel's
                         * keyboard handler, so it is tracked (and its LED set) here */
    int suspended;      /* #1362: another VT is in front -- not grabbed, events dropped */
    int want_remote;    /* #1475: keep scanning until the monitor's device is here too */
    time_t want_until;  /* ...but not for ever: a monitor that never starts */
};

#define WANT_REMOTE_S 180   /* monitor.sh waits up to 60 s for the mirror, then 5 s restarts */

void input_want_remote(Input *in) {
    if (!in) return;
    in->want_remote = 1;
    in->want_until = time(NULL) + WANT_REMOTE_S;
}

/* {code, US plain, US shifted, SI-1452 plain}. The fourth column is the
 * Israeli standard layout's base level (#1245): the 27 Hebrew letters and
 * the five punctuation marks that moved; 0 = the same as US. With Shift
 * SI-1452 gives the Latin capitals and the US symbols, so it has no column
 * of its own. The brackets are left as US (xkb's il mirrors [ ] and ( ),
 * and Pango already mirrors them on screen inside an RTL run). */
static const struct { int code; char n, s; int he; } KEYMAP[] = {
    {KEY_1,'1','!',0},{KEY_2,'2','@',0},{KEY_3,'3','#',0},{KEY_4,'4','$',0},{KEY_5,'5','%',0},
    {KEY_6,'6','^',0},{KEY_7,'7','&',0},{KEY_8,'8','*',0},{KEY_9,'9','(',0},{KEY_0,'0',')',0},
    {KEY_MINUS,'-','_',0},{KEY_EQUAL,'=','+',0},
    {KEY_Q,'q','Q','/'},{KEY_W,'w','W','\''},{KEY_E,'e','E',0x05E7},{KEY_R,'r','R',0x05E8},
    {KEY_T,'t','T',0x05D0},{KEY_Y,'y','Y',0x05D8},{KEY_U,'u','U',0x05D5},{KEY_I,'i','I',0x05DF},
    {KEY_O,'o','O',0x05DD},{KEY_P,'p','P',0x05E4},
    {KEY_LEFTBRACE,'[','{',0},{KEY_RIGHTBRACE,']','}',0},
    {KEY_A,'a','A',0x05E9},{KEY_S,'s','S',0x05D3},{KEY_D,'d','D',0x05D2},{KEY_F,'f','F',0x05DB},
    {KEY_G,'g','G',0x05E2},{KEY_H,'h','H',0x05D9},{KEY_J,'j','J',0x05D7},{KEY_K,'k','K',0x05DC},
    {KEY_L,'l','L',0x05DA},
    {KEY_SEMICOLON,';',':',0x05E3},{KEY_APOSTROPHE,'\'','"',','},{KEY_GRAVE,'`','~',';'},
    {KEY_BACKSLASH,'\\','|',0},
    {KEY_Z,'z','Z',0x05D6},{KEY_X,'x','X',0x05E1},{KEY_C,'c','C',0x05D1},{KEY_V,'v','V',0x05D4},
    {KEY_B,'b','B',0x05E0},{KEY_N,'n','N',0x05DE},{KEY_M,'m','M',0x05E6},
    {KEY_COMMA,',','<',0x05EA},{KEY_DOT,'.','>',0x05E5},{KEY_SLASH,'/','?','.'},
    {KEY_SPACE,' ',' ',0},
    {KEY_KP0,'0','0',0},{KEY_KP1,'1','1',0},{KEY_KP2,'2','2',0},{KEY_KP3,'3','3',0},{KEY_KP4,'4','4',0},
    {KEY_KP5,'5','5',0},{KEY_KP6,'6','6',0},{KEY_KP7,'7','7',0},{KEY_KP8,'8','8',0},{KEY_KP9,'9','9',0},
    {KEY_KPDOT,'.','.',0},{KEY_KPMINUS,'-','-',0},{KEY_KPPLUS,'+','+',0},{KEY_KPASTERISK,'*','*',0},
    {KEY_KPSLASH,'/','/',0},
};

static void push(Input *in, Event e) {
    e.caps = in->caps;
    int next = (in->qt + 1) % QUEUE;
    if (next == in->qh) return;                    /* full: drop, never block */
    in->q[in->qt] = e; in->qt = next;
}

static int is_open(Input *in, const char *path) {
    for (int i = 0; i < in->ndev; i++)
        if (!strcmp(in->dev[i].path, path)) return 1;
    return 0;
}

static int probe(Input *in, const char *path) {
    if (is_open(in, path)) return -1;              /* rescan: already grabbed */
    /* read-write when allowed: the Caps Lock LED is written back (#1203) */
    int fd = open(path, O_RDWR | O_NONBLOCK | O_CLOEXEC);
    if (fd < 0) fd = open(path, O_RDONLY | O_NONBLOCK | O_CLOEXEC);
    if (fd < 0) return -1;
    unsigned long ev[NBITS(EV_MAX)] = { 0 }, key[NBITS(KEY_MAX)] = { 0 };
    unsigned long rel[NBITS(REL_MAX)] = { 0 }, abs_[NBITS(ABS_MAX)] = { 0 };
    if (ioctl(fd, EVIOCGBIT(0, sizeof ev), ev) < 0) { close(fd); return -1; }
    Dev d = { .fd = fd };
    char name[64] = "";
    if (ioctl(fd, EVIOCGNAME(sizeof name - 1), name) >= 0 && !strcmp(name, MONITOR_UINPUT_NAME))
        d.remote = 1;
    if (test_bit(EV_KEY, ev) && ioctl(fd, EVIOCGBIT(EV_KEY, sizeof key), key) >= 0) {
        if (test_bit(KEY_A, key) && test_bit(KEY_Z, key)) d.kinds |= DEV_KEYBOARD;
        if (test_bit(KEY_POWER, key)) d.kinds |= DEV_POWER;   /* #1918 */
        if (test_bit(EV_REL, ev) && ioctl(fd, EVIOCGBIT(EV_REL, sizeof rel), rel) >= 0
            && test_bit(REL_X, rel) && test_bit(BTN_LEFT, key)) d.kinds |= DEV_MOUSE;
        if (test_bit(EV_ABS, ev) && ioctl(fd, EVIOCGBIT(EV_ABS, sizeof abs_), abs_) >= 0
            && (test_bit(ABS_X, abs_) || test_bit(ABS_MT_POSITION_X, abs_))
            && (test_bit(BTN_TOUCH, key) || test_bit(BTN_LEFT, key))) {
            struct input_absinfo ax, ay;
            int cx = test_bit(ABS_MT_POSITION_X, abs_) ? ABS_MT_POSITION_X : ABS_X;
            int cy = test_bit(ABS_MT_POSITION_Y, abs_) ? ABS_MT_POSITION_Y : ABS_Y;
            if (ioctl(fd, EVIOCGABS(cx), &ax) >= 0 && ioctl(fd, EVIOCGABS(cy), &ay) >= 0
                && ax.maximum > ax.minimum && ay.maximum > ay.minimum) {
                d.abs_min_x = ax.minimum; d.abs_max_x = ax.maximum;
                d.abs_min_y = ay.minimum; d.abs_max_y = ay.maximum;
                d.kinds |= test_bit(BTN_TOUCH, key) ? DEV_TOUCH : DEV_ABSPTR;
            }
        }
    }
    if (!d.kinds) { close(fd); return -1; }
    if (d.kinds & DEV_KEYBOARD) {                  /* #1203: start from the LED the kernel set */
        unsigned long led[NBITS(LED_MAX)] = { 0 };
        if (ioctl(fd, EVIOCGLED(sizeof led), led) >= 0 && test_bit(LED_CAPSL, led)) in->caps = 1;
    }
    if (ioctl(fd, EVIOCGRAB, 1) < 0)
        fprintf(stderr, "native-gui: %s: EVIOCGRAB: %s (keys will also reach the text console)\n", path, strerror(errno));
    snprintf(d.path, sizeof d.path, "%s", path);
    in->dev[in->ndev++] = d;
    return 0;
}

Input *input_open(int screen_w, int screen_h) {
    Input *in = calloc(1, sizeof *in);
    in->w = screen_w; in->h = screen_h;
    in->px = screen_w / 2.0; in->py = screen_h / 2.0;
    char path[64];
    for (int i = 0; i < 32 && in->ndev < MAX_DEV; i++) {
        snprintf(path, sizeof path, "/dev/input/event%d", i);
        probe(in, path);
    }
    int kinds = 0;
    for (int i = 0; i < in->ndev; i++) kinds |= in->dev[i].kinds;
    fprintf(stderr, "native-gui: input: %d device(s):%s%s%s%s%s\n", in->ndev,
            kinds & DEV_KEYBOARD ? " keyboard" : "", kinds & DEV_MOUSE ? " mouse" : "",
            kinds & DEV_TOUCH ? " touch" : "", kinds & DEV_ABSPTR ? " abs-pointer" : "",
            kinds & DEV_POWER ? " power-button" : "");   /* #1918: none = a short press is not heard */
    return in;                                     /* ndev may be 0 -- input_rescan grabs a late device */
}

void input_rescan(Input *in) {
    if (!in || in->scan_done || in->suspended) return;   /* stop once kbd+mouse are here;
                                                    * #1362: never grab while another VT is in front */
    time_t now = time(NULL);
    if (now == in->last_rescan) return;            /* at most once a second */
    in->last_rescan = now;
    int before = in->ndev;
    char path[64];
    for (int i = 0; i < 32 && in->ndev < MAX_DEV; i++) {
        snprintf(path, sizeof path, "/dev/input/event%d", i);
        probe(in, path);                           /* probe() skips already-open paths */
    }
    int kinds = 0, remote = 0;
    for (int i = 0; i < in->ndev; i++) { kinds |= in->dev[i].kinds; remote |= in->dev[i].remote; }
    if (in->ndev > before)
        fprintf(stderr, "native-gui: input: device appeared, now %d:%s%s%s%s%s\n", in->ndev,
                kinds & DEV_KEYBOARD ? " keyboard" : "", kinds & DEV_MOUSE ? " mouse" : "",
                kinds & DEV_TOUCH ? " touch" : "", kinds & DEV_ABSPTR ? " abs-pointer" : "",
                kinds & DEV_POWER ? " power-button" : "");
    /* Owner decision: once a keyboard AND a mouse are both present there is
     * nothing left to find, so stop scanning. A machine missing one (a cloner
     * has neither) keeps scanning, so a device plugged in later is still
     * caught; input_pump re-arms the scan if a device is unplugged.
     * #1475: when the remote monitor is expected (input_want_remote), its
     * uinput device is part of "everything": the monitor now starts only
     * after this GUI has written the frame mirror, i.e. after the keyboard
     * and mouse were already found -- stopping there left it never grabbed. */
    if ((kinds & DEV_KEYBOARD) && (kinds & DEV_MOUSE) &&
        (remote || !in->want_remote || now > in->want_until)) {
        in->scan_done = 1;
        fprintf(stderr, "native-gui: input: keyboard+mouse present -- scan stopped\n");
    }
}

int input_fill_pollfds(Input *in, struct pollfd *out, int max) {
    int n = 0;
    for (int i = 0; i < in->ndev && n < max; i++, n++) {
        out[n].fd = in->dev[i].fd; out[n].events = POLLIN; out[n].revents = 0;
    }
    return n;
}

/* The grab keeps the kernel from lighting the LED, so it is set here; a
 * device opened read-only just keeps its LED (the screen still says it). */
static void caps_led(Input *in) {
    for (int i = 0; i < in->ndev; i++) {
        if (!(in->dev[i].kinds & DEV_KEYBOARD)) continue;
        struct input_event ev[2];
        memset(ev, 0, sizeof ev);
        ev[0].type = EV_LED; ev[0].code = LED_CAPSL; ev[0].value = in->caps;
        ev[1].type = EV_SYN; ev[1].code = SYN_REPORT;
        if (write(in->dev[i].fd, ev, sizeof ev) < 0) { /* read-only: no LED, not an error */ }
    }
}

static void key_event(Input *in, Dev *d, int code, int value) {
    int shift = code == KEY_LEFTSHIFT || code == KEY_RIGHTSHIFT;
    if (shift || code == KEY_LEFTALT || code == KEY_RIGHTALT) {
        if (shift) d->shift = value != 0; else d->alt = value != 0;
        /* #1245: Alt+Shift switches the typing language, as on Windows -- once,
         * on the press that completes the chord (either order). A held
         * modifier's autorepeat (value 2) is not a second switch. */
        if (value == 1 && d->shift && d->alt) {
            Event e = { .type = UIEV_KEY, .key = KEYSYM_LAYOUT };
            push(in, e);
        }
        return;
    }
    if (value == 0) return;                          /* release */
    Event e = { 0 };
    if (code == KEY_CAPSLOCK) {
        if (value != 1) return;                      /* autorepeat is not a second toggle */
        in->caps = !in->caps;
        caps_led(in);
        e.type = UIEV_KEY; e.key = KEYSYM_CAPS; push(in, e); return;
    }
    int fn = code >= KEY_F1 && code <= KEY_F10 ? code - KEY_F1 + 1
           : code == KEY_F11 ? 11 : code == KEY_F12 ? 12 : 0;
    if (fn && d->alt) {                              /* #1362: Alt+Fn and Ctrl+Alt+Fn */
        if (value != 1) return;                      /* a held chord is one switch */
        e.type = UIEV_KEY; e.key = KEYSYM_VT; e.vt = fn; push(in, e); return;
    }
    if (code >= KEY_F1 && code <= KEY_F10) { e.type = UIEV_KEY; e.key = KEYSYM_F1 + (code - KEY_F1); push(in, e); return; }
    if (code == KEY_F11 || code == KEY_F12) { e.type = UIEV_KEY; e.key = code == KEY_F11 ? KEYSYM_F11 : KEYSYM_F12; push(in, e); return; }
    switch (code) {
    case KEY_TAB:       e.type = UIEV_KEY; e.key = d->shift ? KEYSYM_BACKTAB : KEYSYM_TAB; push(in, e); return;
    case KEY_ENTER: case KEY_KPENTER:
                        e.type = UIEV_KEY; e.key = KEYSYM_ENTER; push(in, e); return;
    case KEY_BACKSPACE: e.type = UIEV_KEY; e.key = KEYSYM_BACKSPACE; push(in, e); return;
    case KEY_ESC:       e.type = UIEV_KEY; e.key = KEYSYM_ESC; push(in, e); return;
    case KEY_DELETE:    e.type = UIEV_KEY; e.key = KEYSYM_DELETE; push(in, e); return;   /* #1386 */
    case KEY_UP:        e.type = UIEV_KEY; e.key = KEYSYM_UP; push(in, e); return;
    case KEY_DOWN:      e.type = UIEV_KEY; e.key = KEYSYM_DOWN; push(in, e); return;
    case KEY_LEFT:      e.type = UIEV_KEY; e.key = KEYSYM_LEFT; push(in, e); return;    /* the menu grid (mockup 10/10) */
    case KEY_RIGHT:     e.type = UIEV_KEY; e.key = KEYSYM_RIGHT; push(in, e); return;
    }
    for (size_t i = 0; i < sizeof KEYMAP / sizeof KEYMAP[0]; i++)
        if (KEYMAP[i].code == code) {
            e.type = UIEV_CHAR; e.ch = d->shift ? KEYMAP[i].s : KEYMAP[i].n;
            e.he = !d->shift && KEYMAP[i].he ? KEYMAP[i].he : e.ch;     /* #1245 */
            push(in, e); return;
        }
}

static void clamp(Input *in) {
    if (in->px < 0) in->px = 0;
    if (in->px > in->w - 1) in->px = in->w - 1;
    if (in->py < 0) in->py = 0;
    if (in->py > in->h - 1) in->py = in->h - 1;
}

static void read_dev(Input *in, Dev *d) {
    struct input_event ev[32];
    for (;;) {
        ssize_t r = read(d->fd, ev, sizeof ev);
        if (r <= 0) return;                          /* EAGAIN: drained */
        int n = (int)(r / sizeof ev[0]);
        for (int i = 0; i < n; i++) {
            switch (ev[i].type) {
            case EV_KEY:
                if (ev[i].code == BTN_LEFT || ev[i].code == BTN_TOUCH) {
                    if (ev[i].value == 1) {
                        Event e = { .type = UIEV_CLICK, .x = in->px, .y = in->py,
                                    .is_touch = (d->kinds & DEV_TOUCH) != 0 };
                        push(in, e);
                    }
                } else if (ev[i].code == KEY_POWER && (d->kinds & DEV_POWER)) {
                    if (ev[i].value == 1) {          /* #1918: the press; a release or autorepeat is not a second one */
                        Event e = { .type = UIEV_KEY, .key = KEYSYM_POWER };
                        push(in, e);
                    }
                } else if (d->kinds & DEV_KEYBOARD) key_event(in, d, ev[i].code, ev[i].value);
                break;
            case EV_REL:
                if (ev[i].code == REL_WHEEL) {
                    Event e = { .type = UIEV_SCROLL, .delta = ev[i].value };
                    push(in, e);
                }
                /* A raw 1:1 delta feels slow across a 1080p screen; scale it.
                 * Flat gain -- enough to make the pointer usable without the
                 * jumpiness of an acceleration curve. */
                if (ev[i].code == REL_X) { in->px += ev[i].value * PTR_GAIN; in->moved = 1; }
                if (ev[i].code == REL_Y) { in->py += ev[i].value * PTR_GAIN; in->moved = 1; }
                break;
            case EV_ABS:
                /* Inclusive range: absinfo.maximum is the last pixel (w-1),
                 * so a device declared over 0..w-1 lands 1:1 (#949). */
                if (ev[i].code == ABS_X || ev[i].code == ABS_MT_POSITION_X) {
                    in->px = (ev[i].value - d->abs_min_x) * (double)(in->w - 1) / (d->abs_max_x - d->abs_min_x);
                    if (d->kinds & DEV_TOUCH) in->touch_moved = 1; else in->moved = 1;
                }
                if (ev[i].code == ABS_Y || ev[i].code == ABS_MT_POSITION_Y) {
                    in->py = (ev[i].value - d->abs_min_y) * (double)(in->h - 1) / (d->abs_max_y - d->abs_min_y);
                    if (d->kinds & DEV_TOUCH) in->touch_moved = 1; else in->moved = 1;
                }
                break;
            case EV_SYN:
                if (in->moved || in->touch_moved) {
                    clamp(in);
                    Event e = { .type = UIEV_MOVE, .x = in->px, .y = in->py, .is_touch = in->touch_moved && !in->moved };
                    push(in, e);
                    in->moved = in->touch_moved = 0;
                }
                break;
            }
        }
    }
}

void input_pump(Input *in, const struct pollfd *fds, int n) {
    for (int i = 0; i < n; i++) {
        if (!fds[i].revents) continue;
        for (int j = 0; j < in->ndev; j++) {
            if (in->dev[j].fd != fds[i].fd) continue;
            if (fds[i].revents & (POLLERR | POLLHUP | POLLNVAL)) {
                fprintf(stderr, "native-gui: input: %s removed\n", in->dev[j].path);
                ioctl(in->dev[j].fd, EVIOCGRAB, 0); close(in->dev[j].fd);
                in->dev[j] = in->dev[--in->ndev]; /* compact; this slot now holds the last dev */
                in->scan_done = 0;                /* a device left -- look again */
            } else if (fds[i].revents & POLLIN) {
                if (in->suspended) {                 /* #1362: typed on another VT -- not ours */
                    struct input_event drop[32];
                    while (read(in->dev[j].fd, drop, sizeof drop) > 0) {}
                } else read_dev(in, &in->dev[j]);
            }
            break;
        }
    }
}

int input_next(Input *in, Event *out) {
    if (in->qh == in->qt) return 0;
    *out = in->q[in->qh]; in->qh = (in->qh + 1) % QUEUE;
    return 1;
}

int input_caps(const Input *in) { return in ? in->caps : 0; }

void input_suspend(Input *in, int on) {
    if (!in) return;
    in->suspended = on;
    for (int i = 0; i < in->ndev; i++) {
        ioctl(in->dev[i].fd, EVIOCGRAB, on ? 0 : 1);
        in->dev[i].shift = in->dev[i].alt = 0;      /* Alt was down when we left */
        if (!on && (in->dev[i].kinds & DEV_KEYBOARD)) {  /* Caps may have changed meanwhile */
            unsigned long led[NBITS(LED_MAX)] = { 0 };
            if (ioctl(in->dev[i].fd, EVIOCGLED(sizeof led), led) >= 0) in->caps = test_bit(LED_CAPSL, led);
        }
    }
}

void input_close(Input *in) {
    if (!in) return;
    for (int i = 0; i < in->ndev; i++) { ioctl(in->dev[i].fd, EVIOCGRAB, 0); close(in->dev[i].fd); }
    free(in);
}
