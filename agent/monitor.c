/*
 * imagectl-monitor -- expose /dev/fb0 over standard RFB using LibVNCServer.
 * With --fb FILE (a regular file) it serves the memory framebuffer that
 * native-gui --backend mem draws when the machine has no display (#835).
 *
 * View-only unless --input is supplied. In input mode this process creates
 * its own uinput keyboard + absolute pointer. native-gui's periodic
 * input_rescan() then discovers and EVIOCGRABs that event device.
 *
 * The pointer is absolute (EV_ABS, #949): RFB PointerEvent carries the
 * framebuffer position, and the device declares ABS_X/ABS_Y over exactly
 * that range (0..width-1, 0..height-1), so native-gui maps it 1:1. The
 * earlier relative device (EV_REL deltas) let the GUI's own accumulated
 * cursor drift from the browser's -- "the mouse jumps" on the build
 * machine, 17/09.
 */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/fb.h>
#include <linux/input.h>
#include <linux/uinput.h>
#include <netinet/in.h>
#include <rfb/keysym.h>
#include <rfb/rfb.h>
#include <signal.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <sys/types.h>
#include <time.h>
#include <unistd.h>

#include "hmac_sha256.h"

#define DEFAULT_PORT 5900
#define DEFAULT_FPS 10
#define UINPUT_NAME "ImageSrv remote monitor"
#define ARRAY_LEN(a) (sizeof(a) / sizeof((a)[0]))

/*
 * #1475: the scan. Each tick compares the framebuffer with the copy the
 * clients were last given, TILE_W x TILE_H at a time, and marks only the
 * tiles that changed -- a moved pointer is a few KB on the wire, not a
 * whole 1920x1080x4 Raw frame (8.3 MB) every tick. The rate is --fps while
 * nobody touches the screen and ACTIVE_FPS for ACTIVE_MS after the last
 * key or pointer event from the console, so the pointer the operator sees
 * follows the hand; a screen that merely changes (a progress bar) does not
 * raise the rate -- it would hold the fast rate for the whole clone.
 */
#define ACTIVE_FPS 25
#define ACTIVE_MS 2000
#define TORN_RETRY_US 2000      /* fb.mem mid-frame: look again soon, not a tick later */
#define TORN_RETRIES 10         /* then back to the tick: a GUI that died mid-frame */
#define TILE_W 64
#define TILE_H 32
#define STATS_US 60000000LL     /* one cost line a minute while a client watches */

static volatile sig_atomic_t stopping;
static int input_fd = -1;
static long long active_until_us;
/* Framebuffer size, the ABS range of the uinput pointer; set in main
 * before create_uinput. */
static int fb_width;
static int fb_height;
static int previous_buttons;
/* Keys this process currently holds down through uinput (#839): released
 * when the client that pressed them goes away mid-press. */
static unsigned char key_down[KEY_MAX + 1];

/*
 * The session secret (#839, #1077). 5900 is bound to the distribution
 * address, so two gates sit in front of the RFB loop: (1) after accept,
 * only the ImageCtl server IP (--allow-from, from imagectl.server) is
 * kept -- everyone else is closed before the handshake; (2) RFB security
 * type 2 framing, 16-byte challenge, 16-byte response. The response is
 * HMAC-SHA256(secret, challenge) truncated to 16 bytes, not the secret
 * itself and not DES. The secret is drawn on the machine and reported in
 * hello; every successful authentication moves it on (rekey_secret) so a
 * captured response cannot be replayed. The browser never sees it.
 */
#define SECRET_LEN 16
static unsigned char secret[SECRET_LEN];
static char secret_path[512];
static struct in_addr allow_from;
static unsigned long auth_failures;
static unsigned long peer_rejects;

static void stop_handler(int signo) {
    (void)signo;
    stopping = 1;
}

static void fatal(const char *what) {
    fprintf(stderr, "imagectl-monitor: FATAL: %s: %s\n", what, strerror(errno));
    exit(EXIT_FAILURE);
}

static long long now_us(void) {
    struct timespec ts;

    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (long long)ts.tv_sec * 1000000LL + ts.tv_nsec / 1000;
}

/* This process's CPU time, all threads (#1475 cost line). */
static long long cpu_us(void) {
    struct timespec ts;

    clock_gettime(CLOCK_PROCESS_CPUTIME_ID, &ts);
    return (long long)ts.tv_sec * 1000000LL + ts.tv_nsec / 1000;
}

/* Input mode only: a key or pointer event from the console (#1475). */
static void input_seen(void) {
    active_until_us = now_us() + ACTIVE_MS * 1000LL;
}

static int parse_int(const char *text, int minimum, int maximum,
                     const char *name) {
    char *end = NULL;
    long value;

    errno = 0;
    value = strtol(text, &end, 10);
    if (errno || !end || *end || value < minimum || value > maximum) {
        fprintf(stderr, "imagectl-monitor: invalid %s: %s\n", name, text);
        exit(EXIT_FAILURE);
    }
    return (int)value;
}

/*
 * Runs inside LibVNCServer's client callbacks. A short write or EAGAIN on
 * the non-blocking uinput fd drops this one event and says so -- it must
 * never exit(): a burst of pointer events from the console is not a reason
 * to take the monitor down (#839, was fatal()).
 */
static void emit_event(unsigned short type, unsigned short code, int value) {
    static unsigned long dropped;
    struct input_event event = {
        .type = type,
        .code = code,
        .value = value,
    };

    if (input_fd < 0)
        return;
    if (write(input_fd, &event, sizeof(event)) == (ssize_t)sizeof(event))
        return;
    dropped++;
    if (dropped <= 5 || dropped % 1000 == 0)
        fprintf(stderr, "imagectl-monitor: dropped input event %lu: %s\n",
                dropped, strerror(errno));
}

static void sync_input(void) {
    emit_event(EV_SYN, SYN_REPORT, 0);
}

static void enable_key(int fd, int code) {
    if (ioctl(fd, UI_SET_KEYBIT, code) < 0)
        fatal("UI_SET_KEYBIT");
}

static void enable_abs(int fd, int code, int maximum) {
    struct uinput_abs_setup abs_setup = {
        .code = (unsigned short)code,
        .absinfo = { .minimum = 0, .maximum = maximum },
    };

    if (ioctl(fd, UI_SET_ABSBIT, code) < 0 ||
        ioctl(fd, UI_ABS_SETUP, &abs_setup) < 0)
        fatal("UI_ABS_SETUP");
}

static int create_uinput(void) {
    static const int keys[] = {
        KEY_A, KEY_B, KEY_C, KEY_D, KEY_E, KEY_F, KEY_G, KEY_H, KEY_I,
        KEY_J, KEY_K, KEY_L, KEY_M, KEY_N, KEY_O, KEY_P, KEY_Q, KEY_R,
        KEY_S, KEY_T, KEY_U, KEY_V, KEY_W, KEY_X, KEY_Y, KEY_Z,
        KEY_0, KEY_1, KEY_2, KEY_3, KEY_4, KEY_5, KEY_6, KEY_7, KEY_8,
        KEY_9, KEY_ENTER, KEY_BACKSPACE, KEY_TAB, KEY_ESC, KEY_SPACE,
        KEY_LEFTSHIFT, KEY_RIGHTSHIFT, KEY_LEFTCTRL, KEY_RIGHTCTRL,
        KEY_LEFTALT, KEY_RIGHTALT, KEY_LEFTMETA, KEY_RIGHTMETA,
        KEY_UP, KEY_DOWN, KEY_LEFT, KEY_RIGHT, KEY_HOME, KEY_END,
        KEY_PAGEUP, KEY_PAGEDOWN, KEY_INSERT, KEY_DELETE,
        KEY_MINUS, KEY_EQUAL, KEY_LEFTBRACE, KEY_RIGHTBRACE,
        KEY_SEMICOLON, KEY_APOSTROPHE, KEY_GRAVE, KEY_BACKSLASH,
        KEY_COMMA, KEY_DOT, KEY_SLASH,
        KEY_F1, KEY_F2, KEY_F3, KEY_F4, KEY_F5, KEY_F6,
        KEY_F7, KEY_F8, KEY_F9, KEY_F10, KEY_F11, KEY_F12,
        BTN_LEFT
    };
    struct uinput_setup setup = {
        .id = {
            .bustype = BUS_VIRTUAL,
            .vendor = 0x1d6b,
            .product = 0x690,
            .version = 1,
        },
    };
    size_t i;
    int fd = open("/dev/uinput", O_WRONLY | O_NONBLOCK | O_CLOEXEC);

    if (fd < 0)
        fatal("open /dev/uinput");
    if (ioctl(fd, UI_SET_EVBIT, EV_KEY) < 0 ||
        ioctl(fd, UI_SET_EVBIT, EV_ABS) < 0)
        fatal("configure /dev/uinput");
    /* absinfo.maximum is inclusive: the last pixel, not one past it. */
    enable_abs(fd, ABS_X, fb_width - 1);
    enable_abs(fd, ABS_Y, fb_height - 1);

    for (i = 0; i < ARRAY_LEN(keys); i++)
        enable_key(fd, keys[i]);

    snprintf(setup.name, sizeof(setup.name), "%s", UINPUT_NAME);
    if (ioctl(fd, UI_DEV_SETUP, &setup) < 0 ||
        ioctl(fd, UI_DEV_CREATE) < 0)
        fatal("create uinput device");

    fprintf(stderr, "imagectl-monitor: input enabled through %s\n",
            UINPUT_NAME);
    return fd;
}

struct key_pair {
    rfbKeySym symbol;
    int code;
};

static const struct key_pair special_keys[] = {
    {XK_Return, KEY_ENTER}, {XK_BackSpace, KEY_BACKSPACE},
    {XK_Tab, KEY_TAB}, {XK_Escape, KEY_ESC}, {XK_space, KEY_SPACE},
    {XK_Shift_L, KEY_LEFTSHIFT}, {XK_Shift_R, KEY_RIGHTSHIFT},
    {XK_Control_L, KEY_LEFTCTRL}, {XK_Control_R, KEY_RIGHTCTRL},
    {XK_Alt_L, KEY_LEFTALT}, {XK_Alt_R, KEY_RIGHTALT},
    {XK_Meta_L, KEY_LEFTMETA}, {XK_Meta_R, KEY_RIGHTMETA},
    {XK_Super_L, KEY_LEFTMETA}, {XK_Super_R, KEY_RIGHTMETA},
    {XK_Up, KEY_UP}, {XK_Down, KEY_DOWN},
    {XK_Left, KEY_LEFT}, {XK_Right, KEY_RIGHT},
    {XK_Home, KEY_HOME}, {XK_End, KEY_END},
    {XK_Page_Up, KEY_PAGEUP}, {XK_Page_Down, KEY_PAGEDOWN},
    {XK_Insert, KEY_INSERT}, {XK_Delete, KEY_DELETE},
    {XK_F1, KEY_F1}, {XK_F2, KEY_F2}, {XK_F3, KEY_F3},
    {XK_F4, KEY_F4}, {XK_F5, KEY_F5}, {XK_F6, KEY_F6},
    {XK_F7, KEY_F7}, {XK_F8, KEY_F8}, {XK_F9, KEY_F9},
    {XK_F10, KEY_F10}, {XK_F11, KEY_F11}, {XK_F12, KEY_F12},
};

static int ascii_key(rfbKeySym symbol) {
    static const int letters[] = {
        KEY_A, KEY_B, KEY_C, KEY_D, KEY_E, KEY_F, KEY_G, KEY_H, KEY_I,
        KEY_J, KEY_K, KEY_L, KEY_M, KEY_N, KEY_O, KEY_P, KEY_Q, KEY_R,
        KEY_S, KEY_T, KEY_U, KEY_V, KEY_W, KEY_X, KEY_Y, KEY_Z
    };
    static const int digits[] = {
        KEY_0, KEY_1, KEY_2, KEY_3, KEY_4,
        KEY_5, KEY_6, KEY_7, KEY_8, KEY_9
    };

    if (symbol >= 'a' && symbol <= 'z')
        return letters[symbol - 'a'];
    if (symbol >= 'A' && symbol <= 'Z')
        return letters[symbol - 'A'];
    if (symbol >= '0' && symbol <= '9')
        return digits[symbol - '0'];

    switch (symbol) {
    case ')': return KEY_0;
    case '!': return KEY_1;
    case '@': return KEY_2;
    case '#': return KEY_3;
    case '$': return KEY_4;
    case '%': return KEY_5;
    case '^': return KEY_6;
    case '&': return KEY_7;
    case '*': return KEY_8;
    case '(': return KEY_9;
    case '-': case '_': return KEY_MINUS;
    case '=': case '+': return KEY_EQUAL;
    case '[': case '{': return KEY_LEFTBRACE;
    case ']': case '}': return KEY_RIGHTBRACE;
    case ';': case ':': return KEY_SEMICOLON;
    case '\'': case '"': return KEY_APOSTROPHE;
    case '`': case '~': return KEY_GRAVE;
    case '\\': case '|': return KEY_BACKSLASH;
    case ',': case '<': return KEY_COMMA;
    case '.': case '>': return KEY_DOT;
    case '/': case '?': return KEY_SLASH;
    default: return -1;
    }
}

static int keycode_for(rfbKeySym symbol) {
    size_t i;
    int code = ascii_key(symbol);

    if (code >= 0)
        return code;
    for (i = 0; i < ARRAY_LEN(special_keys); i++)
        if (special_keys[i].symbol == symbol)
            return special_keys[i].code;
    return -1;
}

static void keyboard_event(rfbBool down, rfbKeySym symbol,
                           rfbClientPtr client) {
    int code;
    (void)client;

    if (input_fd < 0)
        return;
    input_seen();
    code = keycode_for(symbol);
    if (code < 0) {
        if (down)
            fprintf(stderr,
                    "imagectl-monitor: unmapped RFB keysym 0x%lx\n",
                    (unsigned long)symbol);
        return;
    }
    key_down[code] = down ? 1 : 0;
    emit_event(EV_KEY, (unsigned short)code, down ? 1 : 0);
    sync_input();
}

/*
 * A client that disconnects mid-press (browser tab closed while holding
 * Shift, proxy cut during a drag) must not leave the key or the button held
 * in the kernel: the next operator would inherit a stuck modifier (#839).
 */
static int write_secret_file(const unsigned char *bytes) {
    char tmp[sizeof(secret_path) + 4];
    char hex[SECRET_LEN * 2 + 2];
    int i, fd;
    ssize_t wrote;

    for (i = 0; i < SECRET_LEN; i++)
        snprintf(hex + 2 * i, 3, "%02x", bytes[i]);
    hex[SECRET_LEN * 2] = '\n';
    hex[SECRET_LEN * 2 + 1] = '\0';
    snprintf(tmp, sizeof(tmp), "%s.tmp", secret_path);
    fd = open(tmp, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
    if (fd < 0)
        return -1;
    wrote = write(fd, hex, SECRET_LEN * 2 + 1);
    if (wrote == SECRET_LEN * 2 + 1 && fsync(fd) == 0) {
        close(fd);
        if (rename(tmp, secret_path) == 0)
            return 0;
    } else {
        close(fd);
    }
    unlink(tmp);
    return -1;
}

/*
 * #1837: every successful authentication moves the secret on, to
 * HMAC-SHA256(secret, REKEY_LABEL || challenge) truncated to 16 bytes.
 * Derived, not drawn: the server's proxy holds the same secret and saw the
 * same challenge, so it derives the same next secret as soon as it reads
 * SecurityResult OK, and the next connection authenticates on the first
 * try with no hello in between. Until #1837 a random secret was drawn when
 * the session ended, the server learned it only from the next hello, and
 * every reconnect before that hello was "bad HMAC". Replay protection is
 * unchanged: a captured response was keyed with a secret that is gone, and
 * the next secret cannot be computed without it. A failed file write keeps
 * the old secret and says so; the server re-learns it from hello.
 */
static const char REKEY_LABEL[] = "imagectl-monitor-rekey";

static void rekey_secret(const unsigned char *challenge) {
    unsigned char message[sizeof(REKEY_LABEL) - 1 + SECRET_LEN];
    unsigned char next[32];

    memcpy(message, REKEY_LABEL, sizeof(REKEY_LABEL) - 1);
    memcpy(message + sizeof(REKEY_LABEL) - 1, challenge, SECRET_LEN);
    hmac_sha256(secret, SECRET_LEN, message, sizeof(message), next);
    if (write_secret_file(next) < 0) {
        fprintf(stderr, "imagectl-monitor: could not write the next session "
                "secret; keeping the old one until the next hello\n");
        return;
    }
    memcpy(secret, next, SECRET_LEN);
    fprintf(stderr, "imagectl-monitor: rekeyed session secret\n");
}

static void client_gone(rfbClientPtr client) {
    int code, released = 0;

    fprintf(stderr, "imagectl-monitor: client %s gone\n",
            client->host ? client->host : "?");
    if (input_fd >= 0) {
        for (code = 0; code <= KEY_MAX; code++) {
            if (!key_down[code])
                continue;
            key_down[code] = 0;
            emit_event(EV_KEY, (unsigned short)code, 0);
            released++;
        }
        if (previous_buttons & 1) {
            emit_event(EV_KEY, BTN_LEFT, 0);
            released++;
        }
        previous_buttons = 0;
        if (released)
            sync_input();
    }
}

static int peer_is_server(rfbClientPtr client) {
    struct sockaddr_in addr;
    socklen_t len = sizeof(addr);

    if (getpeername(client->sock, (struct sockaddr *)&addr, &len) < 0)
        return 0;
    if (addr.sin_family != AF_INET)
        return 0;
    return addr.sin_addr.s_addr == allow_from.s_addr;
}

static enum rfbNewClientAction new_client(rfbClientPtr client) {
    if (!peer_is_server(client)) {
        peer_rejects++;
        if (peer_rejects <= 10 || peer_rejects % 100 == 0)
            fprintf(stderr, "imagectl-monitor: rejected peer %s "
                    "(not the server, %lu so far)\n",
                    client->host ? client->host : "?", peer_rejects);
        return RFB_CLIENT_REFUSE;
    }
    client->clientGoneHook = client_gone;
    return RFB_CLIENT_ACCEPT;
}

/*
 * The passwordCheck of the VNC Authentication framing (see `secret` above):
 * `response` is HMAC-SHA256(secret, challenge) truncated to 16 bytes.
 * Constant-time compare; a wrong length is a plain reject. Failures are
 * counted and logged sparsely -- a scanner on the VLAN must not fill the
 * agent log on tmpfs. The raw secret as a response is a reject.
 */
static rfbBool secret_check(rfbClientPtr client, const char *response,
                            int len) {
    unsigned char expected[32];
    unsigned char diff = 0;
    int i;

    if (len != SECRET_LEN) {
        diff = 1;
    } else {
        hmac_sha256(secret, SECRET_LEN,
                    (const unsigned char *)client->authChallenge, SECRET_LEN,
                    expected);
        for (i = 0; i < SECRET_LEN; i++)
            diff |= (unsigned char)response[i] ^ expected[i];
    }
    if (!diff) {
        rekey_secret((const unsigned char *)client->authChallenge);
        return TRUE;
    }
    auth_failures++;
    if (auth_failures <= 10 || auth_failures % 100 == 0)
        fprintf(stderr, "imagectl-monitor: rejected client %s "
                "(bad HMAC, %lu so far)\n",
                client->host ? client->host : "?", auth_failures);
    return FALSE;
}

static int hex_nibble(int c) {
    if (c >= '0' && c <= '9')
        return c - '0';
    if (c >= 'a' && c <= 'f')
        return c - 'a' + 10;
    return -1;
}

/* 32 lowercase hex characters, optionally newline-terminated. Anything
 * else is a refusal to start: an unauthenticated 5900 is never the
 * fallback (the agent's supervisor logs the reason and retries). */
static void load_secret(const char *path) {
    char text[SECRET_LEN * 2 + 2];
    ssize_t got;
    int i, fd = open(path, O_RDONLY | O_CLOEXEC);

    if (fd < 0)
        fatal("open secret file");
    got = read(fd, text, sizeof(text));
    close(fd);
    if (got < 0)
        fatal("read secret file");
    if (got == SECRET_LEN * 2 + 1 && text[SECRET_LEN * 2] == '\n')
        got--;
    if (got != SECRET_LEN * 2) {
        fprintf(stderr, "imagectl-monitor: FATAL: %s: expected %d hex "
                "characters, got %lld bytes\n", path, SECRET_LEN * 2,
                (long long)got);
        exit(EXIT_FAILURE);
    }
    for (i = 0; i < SECRET_LEN; i++) {
        int high = hex_nibble(text[2 * i]), low = hex_nibble(text[2 * i + 1]);
        if (high < 0 || low < 0) {
            fprintf(stderr, "imagectl-monitor: FATAL: %s: not lowercase hex\n",
                    path);
            exit(EXIT_FAILURE);
        }
        secret[i] = (unsigned char)(high << 4 | low);
    }
}

/*
 * View-only (#833): LibVNCServer calls kbdAddEvent/ptrAddEvent without a
 * NULL check, so a NULL callback is a jump to address 0 -- SIGSEGV on the
 * first KeyEvent/PointerEvent, which the console client sends right after
 * the handshake. The cloner invariant is "no uinput, no injection", and
 * these no-ops keep it: the events arrive and are dropped, nothing else.
 */
static void keyboard_event_ignored(rfbBool down, rfbKeySym symbol,
                                   rfbClientPtr client) {
    (void)down;
    (void)symbol;
    (void)client;
}

static void pointer_event_ignored(int buttons, int x, int y,
                                  rfbClientPtr client) {
    (void)buttons;
    (void)x;
    (void)y;
    (void)client;
}

/* RFB x,y are the framebuffer position. Clamped to the declared ABS
 * range (#850): a position past the edge lands on the edge, never
 * outside the screen; evdev itself drops a repeated value, so a client
 * that resends the same position costs nothing. */
static void pointer_event(int buttons, int x, int y, rfbClientPtr client) {
    int left;
    (void)client;

    if (input_fd < 0)
        return;
    input_seen();

    if (x < 0)
        x = 0;
    if (x >= fb_width)
        x = fb_width - 1;
    if (y < 0)
        y = 0;
    if (y >= fb_height)
        y = fb_height - 1;
    emit_event(EV_ABS, ABS_X, x);
    emit_event(EV_ABS, ABS_Y, y);

    left = !!(buttons & 1);
    if (left != !!(previous_buttons & 1))
        emit_event(EV_KEY, BTN_LEFT, left);
    previous_buttons = buttons;
    sync_input();
}

/*
 * Power control (#781). The remote monitor toolbar replaced Ctrl+Alt+Del
 * with Reboot/Power-off. The browser sends the request as an RFB
 * ClientCutText carrying an exact token; LibVNCServer hands it here. Power
 * is deliberately independent of --input (view-only cloners must reboot
 * too): it is not a screen keystroke. The gate is the RFB security
 * handshake above (#839) -- LibVNCServer delivers no client message,
 * this one included, before secret_check() has passed, and only the
 * server's proxy holds the secret. The admin-only WebSocket is the gate in
 * front of the proxy; it was never a gate in front of this port.
 * Any other cut text is an ordinary clipboard paste and is ignored.
 */
#define POWER_REBOOT "imagectl-power:reboot"
#define POWER_POWEROFF "imagectl-power:poweroff"

static void run_power(char *const argv[]) {
    pid_t pid;

    sync();
    pid = fork();
    if (pid == 0) {
        execvp(argv[0], argv);
        _exit(127);          /* exec failed -- never return to the RFB loop. */
    }
    /* Parent does not wait: the machine is going down under it. A failed
     * fork leaves the machine up, which is the safe direction. A failed
     * exec is reaped by the kernel -- SIGCHLD is SIG_IGN in main() (#839),
     * so no zombie survives it. */
}

static void text_event(char *str, int len, rfbClientPtr client) {
    (void)client;

    if (len == (int)strlen(POWER_REBOOT) && !memcmp(str, POWER_REBOOT, (size_t)len)) {
        char *argv[] = {(char *)"reboot", (char *)"-f", NULL};
        fprintf(stderr, "imagectl-monitor: remote reboot requested\n");
        run_power(argv);
    } else if (len == (int)strlen(POWER_POWEROFF) &&
               !memcmp(str, POWER_POWEROFF, (size_t)len)) {
        char *argv[] = {(char *)"/bin/sh", (char *)"-c",
                (char *)". /usr/lib/imagectl/common.sh && safe_poweroff 'monitor: remote request'", NULL};
        fprintf(stderr, "imagectl-monitor: remote poweroff requested\n");
        run_power(argv);
    }
}

/*
 * Memory framebuffer (#835). A machine with no display attached has no
 * /dev/fb0 (i915: "Cannot find any crtc or sizes"), and the monitor exists
 * for exactly that machine. native-gui --backend mem then draws into a
 * regular file, and --fb names it here: a 4096-byte header, then XRGB8888
 * rows of `stride` bytes. `seq` is the GUI's seqlock -- odd while a frame is
 * being copied in -- so a torn frame is skipped, never served. The layout is
 * docs/interfaces.md s15; the constants are repeated verbatim in
 * native-gui/src/backend.c and tests/test_headless_framebuffer.py pins them.
 */
#define MEMFB_MAGIC "IMCTLFB1"
#define MEMFB_HEADER_BYTES 4096
#define MEMFB_FORMAT_XRGB8888 1

struct memfb_header {
    char magic[8];
    uint32_t width, height, stride, format, seq;
};

/* Fills var/fix from the file's header so the fbdev checks and the copy
 * loop below run unchanged. Positive evidence only: magic, format and a
 * size that really holds every row -- a file that merely exists is not a
 * framebuffer (principle 5). */
static void memfb_describe(int fd, off_t size, struct fb_var_screeninfo *var,
                           struct fb_fix_screeninfo *fix) {
    struct memfb_header header;

    if (pread(fd, &header, sizeof(header), 0) != (ssize_t)sizeof(header))
        fatal("read memory framebuffer header");
    if (memcmp(header.magic, MEMFB_MAGIC, sizeof(header.magic)) ||
        header.format != MEMFB_FORMAT_XRGB8888 ||
        header.width == 0 || header.height == 0 ||
        header.stride < (uint64_t)header.width * 4 ||   /* no u32 wrap (#850) */
        (uint64_t)size < MEMFB_HEADER_BYTES +
            (uint64_t)header.stride * header.height) {
        fprintf(stderr,
                "imagectl-monitor: FATAL: not a memory framebuffer: "
                "%ux%u, stride %u, format %u, %lld bytes\n",
                header.width, header.height, header.stride, header.format,
                (long long)size);
        exit(EXIT_FAILURE);
    }
    memset(var, 0, sizeof(*var));
    memset(fix, 0, sizeof(*fix));
    var->xres = header.width;
    var->yres = header.height;
    var->bits_per_pixel = 32;
    var->red.length = var->green.length = var->blue.length = 8;
    var->red.offset = 16;           /* XRGB8888, interfaces.md s15 */
    var->green.offset = 8;
    var->blue.offset = 0;
    fix->line_length = header.stride;
    fix->smem_len = (uint32_t)size;
    fprintf(stderr, "imagectl-monitor: serving a memory framebuffer, %ux%u\n",
            header.width, header.height);
}

/*
 * #850: the pixel format is read from the framebuffer, not assumed. The
 * copy loop moves 4-byte pixels untouched, so the format is served as-is:
 * 32 bpp, three 8-bit channels on distinct byte boundaries, and those
 * offsets become serverFormat's shifts in main(). LibVNCServer's own
 * default (red at bit 0 on little-endian) is not what fbdev XRGB8888 or
 * the memory framebuffer hold. Anything else is refused by name -- a
 * guessed format is a picture in the wrong colours (principle 5).
 */
static const char *pixel_format_error(const struct fb_var_screeninfo *var) {
    const struct fb_bitfield *channel[3] = { &var->red, &var->green,
                                             &var->blue };
    unsigned int used = 0;
    int i;

    if (var->bits_per_pixel != 32)
        return "only 32 bpp is served";
    for (i = 0; i < 3; i++) {
        if (channel[i]->length != 8 || channel[i]->msb_right)
            return "a colour channel is not 8 bits, msb left";
        if (channel[i]->offset % 8 || channel[i]->offset > 24)
            return "a colour channel is not on a byte boundary";
        if (used & (1u << (channel[i]->offset / 8)))
            return "two colour channels share a byte";
        used |= 1u << (channel[i]->offset / 8);
    }
    return NULL;
}

/*
 * #850: every byte the copy loop reads lies inside the mapping. It reads
 * rows yoffset..yoffset+yres-1, bytes xoffset*4..(xoffset+xres)*4 of each,
 * pixel_offset into the file. All arithmetic in 64 bits: xres*4 in __u32
 * wraps. The size cap is RFB's -- ServerInit carries width and height as
 * U16, and rfbGetScreen takes int.
 */
static const char *layout_error(const struct fb_var_screeninfo *var,
                                const struct fb_fix_screeninfo *fix,
                                uint64_t pixel_offset) {
    uint64_t row_end = ((uint64_t)var->xoffset + var->xres) * 4;
    uint64_t last_byte;

    if (!var->xres || !var->yres || var->xres > 65535 || var->yres > 65535)
        return "width and height must be 1..65535 (RFB U16)";
    if (row_end > fix->line_length)
        return "(xoffset + xres) * 4 is past the stride";
    last_byte = pixel_offset +
                ((uint64_t)var->yoffset + var->yres - 1) * fix->line_length +
                row_end;
    if (last_byte > fix->smem_len)
        return "(yoffset + yres) rows are past the mapped length";
    return NULL;
}

/*
 * One pass of the #1475 scan. A row that matches costs one memcmp; a row
 * that does not is compared tile by tile and only the changed tiles are
 * copied and flagged in `dirty` (rows x cols, one byte a tile). The flags
 * are a pending set, cleared only by flush_tiles(): a pass that turns out
 * torn (fb.mem seqlock moved) keeps them, so the tiles it already copied
 * -- now equal to the source and invisible to the next memcmp -- are still
 * sent once a whole frame is in.
 */
static void scan_tiles(const uint8_t *source, size_t source_stride,
                       uint8_t *copy, unsigned width, unsigned height,
                       unsigned char *dirty, unsigned cols) {
    size_t row_bytes = (size_t)width * 4;
    unsigned x_tile, y;

    for (y = 0; y < height; y++) {
        const uint8_t *from = source + (size_t)y * source_stride;
        uint8_t *to = copy + (size_t)y * row_bytes;
        unsigned char *flags = dirty + (size_t)(y / TILE_H) * cols;

        if (!memcmp(from, to, row_bytes))
            continue;
        for (x_tile = 0; x_tile < cols; x_tile++) {
            size_t offset = (size_t)x_tile * TILE_W * 4;
            size_t length = row_bytes - offset < (size_t)TILE_W * 4
                                ? row_bytes - offset : (size_t)TILE_W * 4;

            if (memcmp(from + offset, to + offset, length)) {
                memcpy(to + offset, from + offset, length);
                flags[x_tile] = 1;
            }
        }
    }
}

/* Marks each horizontal run of flagged tiles as one rectangle and clears
 * the flags; returns how many tiles went out. A full-screen change is one
 * rectangle per tile row, not one per tile. */
static unsigned long flush_tiles(rfbScreenInfoPtr screen, unsigned char *dirty,
                                 unsigned cols, unsigned rows,
                                 unsigned width, unsigned height) {
    unsigned long tiles = 0;
    unsigned row, col;

    for (row = 0; row < rows; row++) {
        unsigned char *flags = dirty + (size_t)row * cols;
        int y1 = (int)(row * TILE_H);
        int y2 = (int)((row + 1) * TILE_H < height ? (row + 1) * TILE_H : height);

        col = 0;
        while (col < cols) {
            unsigned start = col;
            int x2;

            if (!flags[col]) {
                col++;
                continue;
            }
            while (col < cols && flags[col]) {
                flags[col++] = 0;
                tiles++;
            }
            x2 = (int)(col * TILE_W < width ? col * TILE_W : width);
            rfbMarkRectAsModified(screen, (int)(start * TILE_W), y1, x2, y2);
        }
    }
    return tiles;
}

static void usage(const char *program) {
    fprintf(stderr,
            "usage: %s --bind ADDRESS --secret-file FILE --allow-from ADDRESS\n"
            "          [--port PORT] [--fps FPS] [--input]\n"
            "          [--fb DEVICE | --fb MEMFB-FILE | --fb FILE --geometry WxH]\n",
            program);
    exit(EXIT_FAILURE);
}

int main(int argc, char **argv) {
    const char *bind_address = NULL;
    const char *secret_arg = NULL;
    const char *allow_arg = NULL;
    const char *fb_path = "/dev/fb0";
    const char *geometry = NULL;
    struct fb_var_screeninfo var;
    struct fb_fix_screeninfo fix;
    struct stat fb_stat;
    rfbScreenInfoPtr screen;
    uint8_t *mapped;
    size_t mapped_length;
    size_t pixel_offset = 0;             /* MEMFB_HEADER_BYTES for a file */
    const uint32_t *frame_seq = NULL;    /* the GUI's seqlock, files only */
    char *rfb_framebuffer;
    const char *refusal;
    int port = DEFAULT_PORT;
    int fps = DEFAULT_FPS;
    int input_enabled = 0;
    int fb_fd;
    int i;
    unsigned tile_cols, tile_rows;
    unsigned char *dirty;
    long long last_scan = 0, stats_since = 0, stats_cpu = 0, scan_cost = 0;
    unsigned long scans = 0, skipped = 0, tiles_sent = 0;
    uint32_t served_seq = 0;             /* fb.mem seq of the last whole scan */
    int served = 0;
    int retry = 0;

    for (i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--bind") && i + 1 < argc)
            bind_address = argv[++i];
        else if (!strcmp(argv[i], "--port") && i + 1 < argc)
            port = parse_int(argv[++i], 1, 65535, "port");
        else if (!strcmp(argv[i], "--fps") && i + 1 < argc)
            fps = parse_int(argv[++i], 1, 30, "fps");
        else if (!strcmp(argv[i], "--input"))
            input_enabled = 1;
        else if (!strcmp(argv[i], "--fb") && i + 1 < argc)
            fb_path = argv[++i];
        else if (!strcmp(argv[i], "--geometry") && i + 1 < argc)
            geometry = argv[++i];
        else if (!strcmp(argv[i], "--secret-file") && i + 1 < argc)
            secret_arg = argv[++i];
        else if (!strcmp(argv[i], "--allow-from") && i + 1 < argc)
            allow_arg = argv[++i];
        else
            usage(argv[0]);
    }
    if (!bind_address || !secret_arg || !allow_arg)
        usage(argv[0]);
    if (strlen(secret_arg) >= sizeof(secret_path)) {
        fprintf(stderr, "imagectl-monitor: FATAL: secret path too long\n");
        return EXIT_FAILURE;
    }
    memcpy(secret_path, secret_arg, strlen(secret_arg) + 1);
    if (inet_pton(AF_INET, allow_arg, &allow_from) != 1) {
        fprintf(stderr, "imagectl-monitor: invalid --allow-from: %s\n",
                allow_arg);
        return EXIT_FAILURE;
    }
    load_secret(secret_path);

    fb_fd = open(fb_path, O_RDONLY | O_CLOEXEC);
    if (fb_fd < 0)
        fatal("open framebuffer");
    if (fstat(fb_fd, &fb_stat) < 0)
        fatal("fstat framebuffer");
    if (S_ISREG(fb_stat.st_mode)) {
        /* Two kinds of regular file, told apart by positive evidence:
         * the IMCTLFB1 magic (#835, native-gui --backend mem) is
         * self-describing; anything else is the raw-pixel test harness
         * (#833/#838) and must say its geometry. There are no FBIOGET_*
         * ioctls to ask either way. */
        char magic[sizeof(MEMFB_MAGIC) - 1];

        if (pread(fb_fd, magic, sizeof(magic), 0) == (ssize_t)sizeof(magic) &&
            !memcmp(magic, MEMFB_MAGIC, sizeof(magic))) {
            memfb_describe(fb_fd, fb_stat.st_size, &var, &fix);
            pixel_offset = MEMFB_HEADER_BYTES;
        } else {
            /* A plain file stands in for /dev/fb0: the geometry is given
             * explicitly and the pixels are packed XRGB8888 rows, no
             * padding. Sizes in 64 bits: WxHx4 wrapped __u32 (#850). */
            uint64_t need;

            memset(&var, 0, sizeof(var));
            memset(&fix, 0, sizeof(fix));
            if (!geometry ||
                sscanf(geometry, "%ux%u", &var.xres, &var.yres) != 2 ||
                !var.xres || !var.yres) {
                fprintf(stderr,
                        "imagectl-monitor: --fb FILE is not a memory framebuffer "
                        "(no " MEMFB_MAGIC " header) and no --geometry WxH was given\n");
                return EXIT_FAILURE;
            }
            var.bits_per_pixel = 32;
            var.red.length = var.green.length = var.blue.length = 8;
            var.red.offset = 16;
            var.green.offset = 8;
            var.blue.offset = 0;
            need = (uint64_t)var.xres * var.yres * 4;
            if ((uint64_t)fb_stat.st_size < need) {
                fprintf(stderr,
                        "imagectl-monitor: FATAL: %s holds %lld bytes, "
                        "geometry needs %llu\n",
                        fb_path, (long long)fb_stat.st_size,
                        (unsigned long long)need);
                return EXIT_FAILURE;
            }
            /* xres*4 wraps only past 2^30, which layout_error() refuses by
             * the RFB cap; a length past __u32 is clamped, never wrapped
             * into a small one, so layout_error() refuses it too. */
            fix.line_length = var.xres * 4;
            fix.smem_len = (uint32_t)(need > UINT32_MAX ? UINT32_MAX : need);
        }
    } else if (ioctl(fb_fd, FBIOGET_VSCREENINFO, &var) < 0 ||
               ioctl(fb_fd, FBIOGET_FSCREENINFO, &fix) < 0) {
        fatal("FBIOGET_*SCREENINFO");
    }
    refusal = pixel_format_error(&var);
    if (refusal) {
        fprintf(stderr,
                "imagectl-monitor: FATAL: unsupported pixel format (%s): "
                "%u bpp, R %u@%u G %u@%u B %u@%u (length@offset)\n",
                refusal, var.bits_per_pixel,
                var.red.length, var.red.offset,
                var.green.length, var.green.offset,
                var.blue.length, var.blue.offset);
        return EXIT_FAILURE;
    }
    refusal = layout_error(&var, &fix, pixel_offset);
    if (refusal) {
        fprintf(stderr,
                "imagectl-monitor: FATAL: framebuffer layout out of bounds "
                "(%s): %ux%u at +%u+%u, stride %u, %u bytes mapped\n",
                refusal, var.xres, var.yres, var.xoffset, var.yoffset,
                fix.line_length, fix.smem_len);
        return EXIT_FAILURE;
    }

    mapped_length = fix.smem_len;
    mapped = mmap(NULL, mapped_length, PROT_READ, MAP_SHARED, fb_fd, 0);
    if (mapped == MAP_FAILED)
        fatal("mmap framebuffer");
    if (pixel_offset)
        frame_seq = (const uint32_t *)(mapped +
                                       offsetof(struct memfb_header, seq));

    rfb_framebuffer = calloc((size_t)var.xres * var.yres, 4);
    if (!rfb_framebuffer)
        fatal("allocate RFB framebuffer");
    tile_cols = (var.xres + TILE_W - 1) / TILE_W;
    tile_rows = (var.yres + TILE_H - 1) / TILE_H;
    dirty = calloc((size_t)tile_cols * tile_rows, 1);
    if (!dirty)
        fatal("allocate tile flags");

    screen = rfbGetScreen(&argc, argv, var.xres, var.yres, 8, 3, 4);
    if (!screen)
        fatal("rfbGetScreen");
    screen->desktopName = "ImageSrv monitor";
    screen->frameBuffer = rfb_framebuffer;
    /* The pixels are copied untouched, so the format the clients are told
     * is the framebuffer's own (#850; pixel_format_error() vetted it). */
    screen->serverFormat.redShift = (uint8_t)var.red.offset;
    screen->serverFormat.greenShift = (uint8_t)var.green.offset;
    screen->serverFormat.blueShift = (uint8_t)var.blue.offset;
    screen->alwaysShared = TRUE;
    /* #1475: send the moment a scan marks a change and a request is open.
     * The library's default (5 ms) only starts the clock inside
     * rfbProcessEvents and sends on the *next* call -- with a loop that
     * sleeps a whole tick there, every frame left one tick late. */
    screen->deferUpdateTime = 0;
    /* #1221: rfbGetScreen() installs its own 8x7 black-and-white "X"
     * software cursor (screen->cursor). rfbShowCursor()/rfbHideCursor()
     * bake it straight into the outgoing framebuffer, at cl->cursorX/Y,
     * for any client that has not negotiated a cursor pseudo-encoding --
     * ours never do (monitor.js and the test client both ask for
     * CopyRect/Raw/DesktopSize only). cursorX/Y start at (0,0) and are
     * never moved: view-only mode's ptrAddEvent drops every pointer event,
     * and input mode's injects into uinput instead of updating the
     * client's own cursor position -- so every client saw a permanent X
     * baked into the top-left corner. NULL disables the soft-cursor draw
     * outright: no cursor is ever composited into the frame, for anyone. */
    screen->cursor = NULL;
    /* #839: a non-NULL authPasswdData makes LibVNCServer offer security
     * type 2 *instead of* None; passwordCheck is what decides. The pointer
     * itself is never dereferenced by the library once passwordCheck is
     * ours -- it is the switch, not a list of passwords. */
    screen->authPasswdData = secret;
    screen->passwordCheck = secret_check;
    screen->newClientHook = new_client;
    screen->port = port;
    screen->ipv6port = 0;
    screen->listenInterface = inet_addr(bind_address);
    if (screen->listenInterface == INADDR_NONE) {
        fprintf(stderr, "imagectl-monitor: invalid bind address: %s\n",
                bind_address);
        return EXIT_FAILURE;
    }

    if (input_enabled) {
        fb_width = (int)var.xres;
        fb_height = (int)var.yres;
        input_fd = create_uinput();
        screen->kbdAddEvent = keyboard_event;
        screen->ptrAddEvent = pointer_event;
    } else {
        /* Absolute cloner invariant: no uinput fd, and callbacks that drop
         * every event. Never NULL: the library does not check (#833). */
        screen->kbdAddEvent = keyboard_event_ignored;
        screen->ptrAddEvent = pointer_event_ignored;
        fprintf(stderr, "imagectl-monitor: view-only mode; input disabled\n");
    }

    /* Power control travels over ClientCutText, independent of --input. */
    screen->setXCutText = text_event;

    signal(SIGINT, stop_handler);
    signal(SIGTERM, stop_handler);
    signal(SIGCHLD, SIG_IGN);       /* run_power(): the kernel reaps (#839) */
    rfbInitServer(screen);
    if (!rfbIsActive(screen)) {
        fprintf(stderr,
                "imagectl-monitor: FATAL: RFB server did not start on %s:%d\n",
                bind_address, port);
        return EXIT_FAILURE;
    }
    fprintf(stderr, "imagectl-monitor: RFB listening on %s:%d, %ux%u@%dfps\n",
            bind_address, port, var.xres, var.yres, fps);

    while (!stopping && rfbIsActive(screen)) {
        const uint8_t *source =
            mapped + pixel_offset + (size_t)var.yoffset * fix.line_length +
            (size_t)var.xoffset * 4;
        uint32_t seq_before = 0;
        long long now = now_us(), period, began;
        int whole = 1;

        /* Nobody watching: no scan at all -- the monitor of a cloner that
         * no one has open costs nothing (#1475; it used to copy the whole
         * framebuffer every tick regardless). The first scan after a
         * client arrives finds every changed tile. */
        if (!screen->clientHead) {
            stats_since = 0;
            scans = skipped = tiles_sent = 0;
            scan_cost = 0;
            rfbProcessEvents(screen, 1000000 / fps > 999999 ? 999999 : 1000000 / fps);
            continue;
        }
        if (retry && retry <= TORN_RETRIES)
            period = TORN_RETRY_US;
        else if (now < active_until_us && fps < ACTIVE_FPS)
            period = 1000000LL / ACTIVE_FPS;
        else
            period = 1000000LL / fps;
        if (now - last_scan < period) {
            long long idle_us = last_scan + period - now;
            /* select() takes tv_usec < 1 s; an input event wakes it early
             * and the period is recomputed at the active rate. */
            rfbProcessEvents(screen, idle_us > 999999 ? 999999 : (long)idle_us);
            continue;
        }
        last_scan = began = now;

        /* The measurement for the cloner's CPU budget (#1475), once a
         * minute while a client watches: this process's own CPU time (the
         * RFB sends included), how often the frame was scanned, what a
         * scan cost, how often an unchanged seq made the tick free. */
        if (!stats_since) {
            stats_since = began;
            stats_cpu = cpu_us();
        } else if (began - stats_since >= STATS_US) {
            long long cpu = cpu_us();

            fprintf(stderr, "imagectl-monitor: in %llds: cpu %lld ms (%lld%% of one "
                    "thread), %lu scans avg %lld us, %lu unchanged ticks, "
                    "%lu tiles of %dx%d sent\n",
                    (began - stats_since) / 1000000LL, (cpu - stats_cpu) / 1000,
                    (cpu - stats_cpu) * 100 / (began - stats_since), scans,
                    scans ? scan_cost / (long long)scans : 0LL, skipped,
                    tiles_sent, TILE_W, TILE_H);
            scans = skipped = tiles_sent = 0;
            scan_cost = 0;
            stats_since = began;
            stats_cpu = cpu;
        }

        /* Memory framebuffer: odd seq = the GUI is mid-frame; a seq that
         * moved during the scan = we read a torn frame. Either way the
         * tiles stay pending and we look again in TORN_RETRY_US -- the GUI
         * rewrites the whole frame on every pointer move, so a full tick
         * of backoff was a visible stall exactly while the mouse moved. */
        if (frame_seq) {
            seq_before = __atomic_load_n(frame_seq, __ATOMIC_ACQUIRE);
            if (seq_before & 1u)
                whole = 0;
            /* #1475: the GUI has not presented since the last whole scan --
             * the frame is the one the clients already have. One atomic
             * load is the whole cost of an idle tick. */
            else if (served && seq_before == served_seq && !retry) {
                skipped++;
                rfbProcessEvents(screen, 0);
                continue;
            }
        }
        if (whole) {
            scan_tiles(source, fix.line_length, (uint8_t *)rfb_framebuffer,
                       var.xres, var.yres, dirty, tile_cols);
            scans++;
            scan_cost += now_us() - began;
            /* Seqlock reader (#850): an acquire load orders what comes
             * after it, not the scan before it; the fence keeps every pixel
             * read ahead of the re-check, so a torn copy cannot pass for
             * whole. */
            if (frame_seq) {
                __atomic_thread_fence(__ATOMIC_ACQUIRE);
                if (__atomic_load_n(frame_seq, __ATOMIC_ACQUIRE) != seq_before)
                    whole = 0;
            }
        }
        retry = whole ? 0 : retry > TORN_RETRIES ? retry : retry + 1;
        if (whole) {
            tiles_sent += flush_tiles(screen, dirty, tile_cols, tile_rows,
                                      var.xres, var.yres);
            served_seq = seq_before;
            served = 1;
        }
        /* Send now (deferUpdateTime 0), not after the next wait. */
        rfbProcessEvents(screen, 0);
    }

    /* Clients first, uinput second (#839): rfbScreenCleanup stops the
     * client threads that call emit_event; destroying the device while one
     * of them was still injecting was a write to a closed fd. */
    rfbScreenCleanup(screen);
    if (input_fd >= 0) {
        ioctl(input_fd, UI_DEV_DESTROY);
        close(input_fd);
        input_fd = -1;
    }
    munmap(mapped, mapped_length);
    close(fb_fd);
    free(dirty);
    free(rfb_framebuffer);
    return EXIT_SUCCESS;
}
