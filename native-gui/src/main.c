/* imagectl-station-gui -- the build machine's screens, drawn natively
 * (Pango/Cairo -> KMS), replacing cage+chromium for the cards of
 * the former server/static/station/index.html (removed in #1247).
 *
 * Contract with the agent (POSIX sh) -- README "Contract with the agent":
 *   --auth-cmd CMD   login: CMD gets "<user>\n<pass>\n" on stdin; exit 0
 *                    means signed in, and its first stdout line is the
 *                    role ("admin" shows the capture card, anything else
 *                    hides it, like station.js afterLogin); the second is
 *                    the user's saved theme (light|dark|auto, #1499).
 *   --state FILE     key=value lines the agent rewrites (disks, machines,
 *                    the running task...); re-read every 2 s when changed.
 *   stdout           one record per operator action: a token line
 *                    (capture | restore | room | classes | back | again |
 *                    capture-start | room-open | room-wake | room-start |
 *                    room-close | room-poweroff (#65/#1367, cloner= confirm=
 *                    [local_copy=1]) | room-poweroff-all (#1367, confirm=
 *                    [local_copy=1]) |
 *                    class-pick | class-start | class-close |
 *                    smart-replace | smart-rescue | smart-skip |
 *                    (16/10: smart-rescue only from the SMART warning window) |
 *                    shrink-continue|<nonce> | shrink-plain|<nonce> |
 *                    shrink-cancel|<nonce>  (#928) |
 *                    tool-list | tool-run|<id>|<arg>|<confirm>  (#649) |
 *                    touch  (#434: a key press -- the idle clock's activity) |
 *                    logout  (11/10: sign-out; the bridge drops the session) |
 *                    theme-light | theme-dark  (#1499: a signed-in switch, saved) |
 *                    ask-<answer>|<nonce>  (11/10: the question dialog --
 *                    ask-restore / ask-leave, ask-poweroff / ask-menu) |
 *                    drawer-cancel|<dev>  (#1386: the cloner's own drawer) |
 *                    room-drawer-cancel  (#1386: cloner=<mac> dev=<dev>)),
 *                    then key=value detail lines, then an empty line.
 *   exit 0           after "restore" (the agent takes the disk);
 *   exit 1           could not start / render, or stopped by a signal.
 * Without --auth-cmd every login fails (closed by default); --demo accepts
 * any non-empty user name, for looking at the screens only. */
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <sys/stat.h>
#include <sys/wait.h>

/* #1946: the GUI's own proof of life for its keepers (guiparent.sh gui_keep,
 * srgui.sh): this file's mtime, set after every presented frame and every turn
 * of the event loop (which wakes at least every 2 s). A stopped or wedged GUI
 * still answers kill -0; it does not move this. */
static void gui_heartbeat(void) {
    const char *path = getenv("IMAGECTL_GUI_BEAT");
    if (!path || !*path) path = "/run/imagectl/gui/beat";
    int fd = open(path, O_WRONLY | O_CREAT | O_CLOEXEC, 0644);
    if (fd >= 0) { futimens(fd, NULL); close(fd); }
}
#include "backend.h"
#include "dcui.h"
#include "input.h"
#include "install.h"
#include "state.h"
#include "text.h"
#include "ui.h"
#include "utf8.h"
#include "lcui.h"   /* #1322 */
#include "screens_rounds.h"   /* 16/10: smart_level, cloner_port_label, idle_for_port */
#include "smartdlg.h"        /* 16/10: the SMART warning window */
#include "splash.h"         /* 17/10: the last-resort screen */
#include "canceldlg.h"       /* #1386: "בטל דיסק זה" */

static volatile sig_atomic_t stop_now = 0;
static void on_signal(int sig) { (void)sig; stop_now = 1; }

static const char *USAGE =
"usage: imagectl-station-gui [--mac AA:BB:CC:DD:EE:FF] [--ip 10.0.0.5] [--title T]\n"
"                            [--theme light|dark] [--backend auto|drm|fbdev|mem] [--fb-file PATH]\n"
"                            [--auth-cmd CMD | --demo] [--state FILE] [--no-input]\n"
"                            [--screen progress|class|cloner|standby|install|dcui] [--signed-in USER [--role admin|deploy]]\n"
"                            [--install-cmd CMD] [--install-cwd DIR] (legacy: --wizard-cmd/--wizard-cwd)\n"
"       imagectl-station-gui --png PREFIX [--size WxH] [--state FILE] [--mac ..] [--ip ..] [--user NAME]\n"
"  --png renders every card, both themes, to PREFIX-<card>-{light,dark}.png and exits\n"
"  (no display needed). Without --state it uses built-in sample data.\n"
"  --screen dcui is the server's own screen (#1203): frames on stdin, keys on stdout\n"
"  (dcui.h); with --png it renders the --state frame file in both themes.\n"
"  --backend mem draws into --fb-file (default /run/imagectl/fb.mem) for the remote\n"
"  monitor when the machine has no display (#835); auto falls back to it last.\n"
"  --fb-mirror PATH also writes every frame shown on a display into PATH in the\n"
"  same format, so the monitor reads RAM instead of /dev/fb0 (#1475).\n"
"       imagectl-station-gui --splash-raw PATH --size WxH\n"
"  writes the last-resort screen as raw XRGB8888 rows (build time; agent/lib/splash.sh).\n";

/* ---- login through the agent's command ------------------------------------- */

/* #1499: the answer is "<role>\n<saved theme>\n"; read to EOF (the bridge may
 * write the two lines in two writes), then split them (theme.c). */
static int run_auth(const char *cmd, const char *user, const char *pass, char *role, size_t n,
                    char *saved, size_t sn) {
    int in_p[2], out_p[2];
    if (pipe(in_p) || pipe(out_p)) return -1;
    pid_t pid = fork();
    if (pid < 0) return -1;
    if (pid == 0) {
        signal(SIGPIPE, SIG_DFL);                    /* #2108: the GUI ignores it; the command must not inherit that */
        dup2(in_p[0], 0); dup2(out_p[1], 1);
        close(in_p[0]); close(in_p[1]); close(out_p[0]); close(out_p[1]);
        execl("/bin/sh", "sh", "-c", cmd, (char *)NULL);
        _exit(127);
    }
    close(in_p[0]); close(out_p[1]);
    FILE *w = fdopen(in_p[1], "w");
    if (w) { fprintf(w, "%s\n%s\n", user, pass); fclose(w); } else close(in_p[1]);
    char buf[256] = "";
    size_t got = 0;
    for (;;) {                                       /* to EOF; past a full buffer, drained */
        char sink[64];
        int full = got >= sizeof buf - 1;
        ssize_t r = read(out_p[0], full ? sink : buf + got, full ? sizeof sink : sizeof buf - 1 - got);
        if (r > 0) { if (!full) got += (size_t)r; continue; }
        if (r < 0 && errno == EINTR) continue;
        break;                                       /* EOF or error */
    }
    buf[got] = 0;
    close(out_p[0]);
    int status = 0;
    waitpid(pid, &status, 0);
    theme_login_answer(buf, role, n, saved, sn);
    return WIFEXITED(status) && WEXITSTATUS(status) == 0 ? 0 : -1;
}

static void do_login(App *a, const char *auth_cmd, int demo) {
    char role[64] = "", saved[16] = "";
    int ok;
    if (auth_cmd)     ok = run_auth(auth_cmd, a->user, a->pass, role, sizeof role, saved, sizeof saved) == 0;
    else if (demo)    { ok = a->user[0] != 0; snprintf(role, sizeof role, "admin"); }
    else {
        ok = 0;
        fprintf(stderr, "native-gui: login attempted but no --auth-cmd was given (and no --demo)\n");
    }
    if (!ok) {
        snprintf(a->error, sizeof a->error, "שם משתמש או סיסמה שגויים");   /* station.js */
        return;
    }
    a->error[0] = 0;
    snprintf(a->signed_user, sizeof a->signed_user, "%s", a->user);
    a->admin = strcmp(role, "admin") == 0;
    a->signed_in = 1;
    /* #1499 (Nadav 16/10): the user's saved theme wins; "auto" or none = the
     * default. A switch made before login was for that screen only. */
    a->theme = theme_saved(saved, a->default_theme);
    memset(a->pass, 0, sizeof a->pass);
    a->menu_focus = -1; a->logout_confirming = 0;
    a->mode = MODE_MENU;                            /* afterLogin: both roles start at the menu */
}

/* Metal 18/10 (rc6, cloner 2): the agent's count on the boot choice starts at
 * its first frame on the display, not when the agent wrote it -- the display
 * came up after the 20 s were gone. Called after each present: the first frame
 * of SCREEN_LCBOOT leaves <GUI_DIR>/lcboot.shown = the second it was shown
 * (localclone.sh/lcwait.sh lc_boot_due). Written whole (renamed into place) and
 * once: a GUI started again finds it there. A stamp that cannot be written is
 * said once; the agent's own ceiling (LC_SHOWN_WAIT_S) starts the count. */
static void lcboot_mark_shown(const App *a) {
    static int failed;
    if (failed || a->screen != SCREEN_LCBOOT || !a->tools_dir[0]) return;
    char path[600], next[620];
    snprintf(path, sizeof path, "%s/lcboot.shown", a->tools_dir);
    if (access(path, F_OK) == 0) return;            /* this choice's first frame is stamped */
    snprintf(next, sizeof next, "%s.next", path);
    FILE *f = fopen(next, "w");
    int ok = f && fprintf(f, "%lld\n", (long long)time(NULL)) > 0;
    if (f && fclose(f) != 0) ok = 0;
    if (ok && rename(next, path) == 0) return;
    fprintf(stderr, "native-gui: cannot stamp the boot choice as shown (%s): %s\n", path, strerror(errno));
    unlink(next); failed = 1;
}

/* #1918: the power button -- a request file next to the state file, as the
 * local clone's screens leave theirs (lcgui.sh): power.press when it was
 * pressed, power.answer = <nonce>|stop|keep for the question. A file and not
 * a stdout record: the restore's and the failure's GUI have no bridge, and
 * the agent's power_watch (powerbtn.sh) reads the same place under all of
 * them. Written whole (renamed into place); 0 = not written, and the caller
 * says so on the screen. */
static int power_drop(const App *a, const char *name, const char *text) {
    if (!a->tools_dir[0]) return 0;
    char path[600], next[620];
    snprintf(path, sizeof path, "%s/%s", a->tools_dir, name);
    snprintf(next, sizeof next, "%s.next", path);
    FILE *f = fopen(next, "w");
    int ok = f && fprintf(f, "%s\n", text) > 0;
    if (f && fclose(f) != 0) ok = 0;
    if (ok && rename(next, path) == 0) return 1;
    fprintf(stderr, "native-gui: power button: cannot write %s: %s\n", path, strerror(errno));
    unlink(next);
    return 0;
}

/* ---- stdout records --------------------------------------------------------- */

static void emit(const char *token) { printf("%s\n", token); }
static void emit_kv(const char *k, const char *v) { printf("%s=%s\n", k, v); }
static void emit_end(void) { printf("\n"); fflush(stdout); }
static void emit1(const char *token) { emit(token); emit_end(); }

/* A SMART choice echoes the nonce of the request it was drawn for, so the
 * bridge binds the click to that exact request. The port alone cannot: the
 * same disk can be prompted again (a new request, a new nonce), and a stale
 * click would still match the port. The nonce is unique per request. */
static void emit_smart(const App *a, const char *action) {
    char line[64];
    snprintf(line, sizeof line, "%s|%s", action, a->st.smart_nonce);
    emit1(line);
}

/* #928: the shrink answer echoes the question's nonce, as the SMART choice
 * does. One answer per question: a double click would only reach a bridge
 * whose request is already gone. */
static void emit_shrink(App *a, const char *action) {
    if (!a->st.shrink_pending || !strcmp(a->shrink_sent, a->st.shrink_nonce)) return;
    char line[64];
    snprintf(line, sizeof line, "%s|%s", action, a->st.shrink_nonce);
    snprintf(a->shrink_sent, sizeof a->shrink_sent, "%s", a->st.shrink_nonce);
    emit1(line);
}

/* 11/10: the question dialog's answer echoes its nonce, one per question,
 * exactly like the shrink answer. */
static void emit_ask(App *a, const char *answer) {
    if (!a->st.ask_pending || !strcmp(a->ask_sent, a->st.ask_nonce)) return;
    char line[64];
    snprintf(line, sizeof line, "ask-%s|%s", answer, a->st.ask_nonce);
    snprintf(a->ask_sent, sizeof a->ask_sent, "%s", a->st.ask_nonce);
    emit1(line);
}

/* 11/10: modal like the shrink question. 1 = the primary answer, 2 = the
 * secondary one, Esc = the safe default (leave the disk / power off), Enter
 * nothing -- except badsect (#2017, Nadav 22/10), whose default is to go on:
 * Enter is "המשך" (its focus), Esc "עצור". The shrink question, if both are
 * pending, is the one on screen. */
static int ask_modal(App *a, const Event *e) {
    if (!a->st.ask_pending || a->st.shrink_pending) return 0;
    const int grow = a->st.ask_kind == ASK_GROWBACK;
    const int drawer = a->st.ask_kind == ASK_RAID || a->st.ask_kind == ASK_SHARE;   /* #1808/#1898: no answer = no write */
    const int bad = a->st.ask_kind == ASK_BADSECT;   /* #2017: no answer = the copy goes on (Nadav 22/10) */
    const char *first = grow ? "restore" : a->st.ask_kind == ASK_RAID ? "wipe" : a->st.ask_kind == ASK_SHARE ? "split" : bad ? "continue" : "poweroff";
    const char *second = grow ? "leave" : drawer ? "skip" : bad ? "stop" : "menu";
    const char *safe = grow ? "leave" : drawer ? "skip" : bad ? "stop" : "poweroff";
    switch (e->type) {
    case UIEV_CHAR:
        if (e->ch == '1') emit_ask(a, first);
        else if (e->ch == '2') emit_ask(a, second);
        return 1;
    case UIEV_KEY:
        if (e->key == KEYSYM_ESC) emit_ask(a, safe);
        else if (e->key == KEYSYM_ENTER && bad) emit_ask(a, first);
        return 1;
    case UIEV_CLICK:
        a->ptr_x = e->x; a->ptr_y = e->y;
        switch (app_hit(a, e->x, e->y)) {
        case HIT_ASK_PRIMARY:   emit_ask(a, first);  break;
        case HIT_ASK_SECONDARY: emit_ask(a, second); break;
        }
        return 1;
    default:
        return 0;                                 /* pointer moves still move the cursor */
    }
}

/* #928: while the shrink question is open it is modal. Only its answers get
 * through -- 1/2/3, Esc = cancel, or a click on one of its buttons -- and a
 * stray key or click never reaches the screen behind it. Enter answers
 * nothing: "shrink" is never the default. Returns 1 when it took the event. */
static int shrink_modal(App *a, const Event *e) {
    if (!a->st.shrink_pending) return 0;
    switch (e->type) {
    case UIEV_CHAR:
        if (e->ch == '1') emit_shrink(a, "shrink-continue");
        else if (e->ch == '2') emit_shrink(a, "shrink-plain");
        else if (e->ch == '3') emit_shrink(a, "shrink-cancel");
        return 1;
    case UIEV_KEY:
        if (e->key == KEYSYM_ESC) emit_shrink(a, "shrink-cancel");
        return 1;
    case UIEV_CLICK:
        a->ptr_x = e->x; a->ptr_y = e->y;
        switch (app_hit(a, e->x, e->y)) {
        case HIT_SHRINK_CONTINUE: emit_shrink(a, "shrink-continue"); break;
        case HIT_SHRINK_PLAIN:    emit_shrink(a, "shrink-plain");    break;
        case HIT_SHRINK_CANCEL:   emit_shrink(a, "shrink-cancel");   break;
        }
        return 1;
    default:
        return 0;                                 /* pointer moves still move the cursor */
    }
}

/* ---- small helpers ------------------------------------------------------------ */

static double now_s(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec / 1e9;
}

static void toast(App *a, const char *text) {          /* station.js toast(): 2600 ms */
    snprintf(a->toast, sizeof a->toast, "%s", text);
    a->toast_until = now_s() + 2.6;
}

static void trim(char *s) {
    size_t n = strlen(s);
    while (n && (s[n - 1] == ' ' || s[n - 1] == '\t')) s[--n] = 0;
    size_t i = 0;
    while (s[i] == ' ' || s[i] == '\t') i++;
    if (i) memmove(s, s + i, n - i + 1);
}

/* #1245: <cp> is a code point -- a Hebrew letter is two bytes of UTF-8 --
 * stored whole or not at all (utf8.h). */
static void field_append(char *buf, size_t n, int cp) {
    utf8_append(buf, n, cp);
}

static void field_backspace(char *buf) {
    size_t len = strlen(buf);
    while (len > 0) { len--; if ((buf[len] & 0xC0) != 0x80) break; }   /* whole UTF-8 char */
    buf[len] = 0;
}

/* The text buffer behind a field id, or NULL. */
static char *field_buf(App *a, int id, size_t *n) {
    switch (id) {
    case HIT_USER:          *n = sizeof a->user;          return a->user;
    case HIT_PASS:          *n = sizeof a->pass;          return a->pass;
    case HIT_NAME:          *n = sizeof a->name;          return a->name;
    case HIT_DESC:          *n = sizeof a->desc;          return a->desc;
    case HIT_FOLDER_NEW:    *n = sizeof a->folder_new;    return a->folder_new;
    case HIT_ROOM_TARGET:   *n = sizeof a->room_target;   return a->room_target;
    case HIT_TOOL_ARG:      *n = sizeof a->tool_arg;      return a->tool_arg;        /* #649 */
    default: *n = 0; return NULL;
    }
}

/* The fields present on the current screen, in Tab order. */
static int fields_on(const App *a, int *out) {
    int n = 0;
    switch (a->screen) {
    case SCREEN_LOGIN: out[n++] = HIT_USER; out[n++] = HIT_PASS; break;
    case SCREEN_PICK:
        if (a->chosen_disk >= 0) {
            out[n++] = HIT_NAME; out[n++] = HIT_DESC;
            if (a->newfolder_shown) out[n++] = HIT_FOLDER_NEW;
        }
        break;
    case SCREEN_ROOM:
        if (!a->st.has_round && a->mode != MODE_DIRECT) out[n++] = HIT_ROOM_TARGET;   /* #715: no count field */
        break;
    case SCREEN_TOOLS:                                   /* #649: the confirm view only */
        if (a->tools_view == TOOLS_CONFIRM && a->tool_sel >= 0 && a->tool_sel < a->ntools) {
            const Tool *t = &a->tools[a->tool_sel];
            if (t->args[0] && strncmp(t->args, "disk:", 5)) out[n++] = HIT_TOOL_ARG;
        }
        break;
    default: break;
    }
    return n;
}

static int field_visible(const App *a, int id) {
    int ids[8], n = fields_on(a, ids);
    for (int i = 0; i < n; i++) if (ids[i] == id) return 1;
    return 0;
}

/* #1245 (owner, 10/10: Hebrew in every field that can take it). English
 * only: the console account -- user and password, ASCII on the server --
 * the room's target count (digits), and a tool's argument (handed to a
 * shell tool as one word). The image name, description and new folder take
 * Hebrew (images.DISPLAY_NAME_RE allows א-ת). The installer's fields are
 * all English and never read Event.he. */
static int field_english_only(int id) {
    return id == HIT_USER || id == HIT_PASS || id == HIT_ROOM_TARGET || id == HIT_TOOL_ARG;
}

int app_typing_lang(const App *a) {
    if (!field_visible(a, a->focus)) return LANG_NONE;
    if (field_english_only(a->focus)) return LANG_EN_ONLY;
    return a->lang_he ? LANG_HE : LANG_EN;
}

/* owner, 10/10: the folder-first image pickers. Open folder k of
 * image_folders(), or go back to the folder list. */
static void pick_open_folder(App *a, int k) {
    int first[MAX_FOLDERS], count[MAX_FOLDERS], n = image_folders(&a->st, first, count, MAX_FOLDERS);
    if (k < 0 || k >= n) return;
    char f[64]; snprintf(f, sizeof f, "%s", a->st.images[first[k]].folder);   /* both inside *a (-Wrestrict) */
    snprintf(a->pick_folder, sizeof a->pick_folder, "%s", f);
    a->pick_in_folder = 1;
}

/* A digit in a folder-first picker: folder k at the top, image k inside a
 * folder. Returns the chosen image's index into st.images, or -1. */
static int pick_digit(App *a, int k) {
    if (!a->pick_in_folder) { pick_open_folder(a, k); return -1; }
    int idx[MAX_IMAGES], n = folder_images(&a->st, a->pick_folder, idx, MAX_IMAGES);
    return k >= 0 && k < n ? idx[k] : -1;
}

/* Leaving a sub-screen for the menu (st-back-capture / room-back / cls-back). */
static void go_menu(App *a) {
    a->pick_in_folder = 0; a->pick_folder[0] = 0;
    a->mode = MODE_MENU;
    a->chosen_disk = -1; a->dd_open = 0;
    a->newfolder_shown = 0;
    a->room_target_set = 0; a->confirm = CONFIRM_NONE;
    a->menu_focus = -1;
    a->tools_view = TOOLS_LIST; a->tool_sel = -1;          /* #649 */
}

/* An action of a live classroom round without a session: classes.js
 * authed() -> 401 -> toast, and the live view stays (#34). */
static int class_gate(App *a) {
    if (a->signed_in) return 1;
    toast(a, "החיבור פג — נדרשת כניסה מחדש.");
    return 0;
}

/* ---- events --------------------------------------------------------------------- */

/* Same visibility rule as screens.c, in MENU order (#1244): capture for
 * admin first, then room, direct, restore; the class card follows
 * the server's switch (#880, pinned off in v1). Returns how many. */
static int visible_card_ids(const App *a, int ids[5]) {
    int n = 0;
    if (a->admin) ids[n++] = HIT_CAPTURE;
    ids[n++] = HIT_ROOM;
    ids[n++] = HIT_DIRECT;
    ids[n++] = HIT_RESTORE;
    if (a->st.menu_class) ids[n++] = HIT_CLASSES;   /* #1081: v1 off, v1.2 on */
    return n;
}

static void sign_out(App *a) {
    a->signed_in = 0; a->mode = MODE_MENU; a->focus = HIT_USER; a->logout_confirming = 0;
    a->theme = a->default_theme;   /* #1499: the next person starts at the default */
    emit1("logout");   /* 11/10: the bridge drops the console session too */
}

/* #1499 (Nadav 16/10): the corner switch (screens.c) or F2, on every screen.
 * Signed in, the switch is the user's saved theme -- the bridge PUTs it
 * (guibridge.sh, theme-light|theme-dark); before login and on the cloner
 * screens nobody is signed in, and it changes this screen only. */
static void toggle_theme(App *a) {
    a->theme = a->theme == &THEME_DARK ? &THEME_LIGHT : &THEME_DARK;
    if (a->signed_in) emit1(a->theme == &THEME_DARK ? "theme-dark" : "theme-light");
}

/* mockup 10/10: while "להתנתק?" is open it is modal -- Enter or its button
 * signs out, Esc or "ביטול" closes it, nothing else reaches the menu. */
static int logout_modal(App *a, const Event *e) {
    if (!a->logout_confirming || a->screen != SCREEN_MENU) return 0;
    if (e->type == UIEV_KEY) {
        if (e->key == KEYSYM_ENTER) sign_out(a);
        else if (e->key == KEYSYM_ESC) a->logout_confirming = 0;
        return 1;
    }
    if (e->type == UIEV_CLICK) {
        a->ptr_x = e->x; a->ptr_y = e->y;
        int id = app_hit(a, e->x, e->y);
        if (id == HIT_LOGOUT_CONFIRM) sign_out(a);
        else if (id == HIT_LOGOUT_CANCEL) a->logout_confirming = 0;
        return 1;
    }
    return e->type == UIEV_CHAR;
}

/* The menu's keyboard stops, in order: the visible cards, "התנתק", and
 * "כלים" when the menu shows it (screens.c draws a ring on the same index). */
static int menu_stops(const App *a, int ncards) { return ncards + 1 + (a->st.menu_tools ? 1 : 0); }

/* Arrows walk the two-column grid as it is drawn: card 1 at the right, so
 * Left is the next card and Right the previous one; Up/Down are a row. From
 * outside the grid (nothing, "התנתק", "כלים") any arrow lands on card 1. */
static void menu_arrow(App *a, int key, int ncards) {
    int f = a->menu_focus;
    if (f < 0 || f >= ncards) { a->menu_focus = 0; return; }
    if (key == KEYSYM_LEFT && f % 2 == 0 && f + 1 < ncards) f++;
    else if (key == KEYSYM_RIGHT && f % 2 == 1) f--;
    else if (key == KEYSYM_DOWN && f + 2 < ncards) f += 2;
    else if (key == KEYSYM_UP && f - 2 >= 0) f -= 2;
    a->menu_focus = f;
}

/* Returns 1 when the process should exit (after "restore"). */
static int choose_card(App *a, int id) {
    switch (id) {
    case HIT_CAPTURE: a->mode = MODE_CAPTURE; a->chosen_disk = -1; a->newfolder_shown = 0; emit1("capture"); break;
    case HIT_RESTORE: a->mode = MODE_RESTORE; a->restore_image_sel = 0; a->pick_in_folder = 0; emit1("restore"); break;
    case HIT_ROOM:    a->mode = MODE_ROOM; a->room_target_set = 0; a->image_sel = 0; a->pick_in_folder = 0; a->nroom_selection = 0; a->nroom_pick = 0; room_drv_reset(a); emit1("room"); break;   /* #1275: ticks are targets; #1269: drivers off */
    case HIT_DIRECT:  a->mode = MODE_DIRECT; a->room_target_set = 0; a->nroom_selection = 0; a->nroom_pick = 0; room_drv_reset(a); emit1("direct"); break;   /* #715; #1269: drivers off */
    case HIT_CLASSES: a->mode = MODE_CLASSES; emit1("classes"); break;
    case HIT_TOOLS:                                       /* #649: ask the bridge for the list */
        a->mode = MODE_TOOLS; a->tools_view = TOOLS_LIST; a->tool_sel = -1;
        a->tool_error[0] = 0; a->tool_list_scroll = 0;
        emit1("tool-list");
        break;
    }
    return 0;
}

/* ---- #649: the toolbox ---------------------------------------------------------- */

/* What tool_run sends, checked before the confirm dialog opens: the disk or
 * the argument. Returns 1 when the run can go out; else says why. */
static int tool_ready(App *a, char *arg, size_t n) {
    if (a->tool_sel < 0 || a->tool_sel >= a->ntools) return 0;
    const Tool *t = &a->tools[a->tool_sel];
    arg[0] = 0;
    if (!strncmp(t->args, "disk:", 5)) {
        if (a->tool_disk_sel < 0 || a->tool_disk_sel >= a->st.ndisks) {
            snprintf(a->tool_error, sizeof a->tool_error, "בחרו דיסק"); return 0;
        }
        snprintf(arg, n, "%s", a->st.disks[a->tool_disk_sel].dev);
    } else if (t->args[0]) {
        snprintf(arg, n, "%s", a->tool_arg); trim(arg);
        if (!arg[0] || strpbrk(arg, "|\r\n ")) {
            snprintf(a->tool_error, sizeof a->tool_error, "מלאו את השדה: %s (בלי רווחים)", t->args); return 0;
        }
    }
    /* #1270: rw/destroy are confirmed in a dialog; the bridge (tools.sh) still
     * checks the machine's name, and the name is the one tools= sent us */
    if (tools_risk_level(t->risk) && (!a->tools_machine[0] || strpbrk(a->tools_machine, "|\r\n"))) {
        snprintf(a->tool_error, sizeof a->tool_error, "שם המחשב אינו ידוע — לא ניתן לאשר"); return 0;
    }
    a->tool_error[0] = 0;
    return 1;
}

/* Send tool-run|<id>|<arg>|<confirm>; the bridge (tools.sh) checks the name
 * again before any module runs. '|' and newlines cannot travel in the record
 * and are refused (tool_ready). Called after the confirm dialog for rw/destroy. */
static void tool_run(App *a) {
    char arg[96], confirm[96];
    if (!tool_ready(a, arg, sizeof arg)) return;
    const Tool *t = &a->tools[a->tool_sel];
    snprintf(confirm, sizeof confirm, "%s", tools_risk_level(t->risk) ? a->tools_machine : "");
    char line[400];
    snprintf(line, sizeof line, "tool-run|%s|%s|%s", t->id, arg, confirm);
    emit1(line);
    a->tool_running = 1; a->tool_rc = -1; a->tool_spin = 0;
    tool_output_set(a, "");
    a->tools_view = TOOLS_OUTPUT;
}

/* A row was clicked: ro without an argument runs at once; anything else
 * goes through the confirm view (the warning, a disk or an argument), and
 * rw/destroy then through the confirm dialog (#1270: no typed name). */
static void tool_open(App *a, int i) {
    if (i < 0 || i >= a->ntools) return;
    a->tool_sel = i; a->tool_disk_sel = -1; a->tool_arg[0] = a->tool_error[0] = 0;
    const Tool *t = &a->tools[i];
    if (!tools_risk_level(t->risk) && !t->args[0]) { tool_run(a); return; }
    a->tools_view = TOOLS_CONFIRM;
    a->focus = t->args[0] && strncmp(t->args, "disk:", 5) ? HIT_TOOL_ARG : 0;
}

/* "הרץ" on the confirm view: ro runs, rw/destroy ask first. */
static void tool_go(App *a) {
    char arg[96];
    if (!tool_ready(a, arg, sizeof arg)) return;
    if (tools_risk_level(a->tools[a->tool_sel].risk)) a->confirm = CONFIRM_TOOL;
    else tool_run(a);
}

/* 16/10 (Nadav): a disk of THIS machine with a SMART warning (dsmart=) is
 * written or read only after the SMART warning window -- named as the disk
 * list names it (#1495): "דיסק N" by the drawer mapping, "<conn> (לא ממופה)",
 * or the device when the mapping was not reported. */
static int disk_smart_ask(App *a, int kind, int i) {
    if (i < 0 || i >= a->st.ndisks || !smart_level(a->st.disks[i].smart)) return 0;
    const Disk *d = &a->st.disks[i];
    char label[SLOT_NAME_MAX + 64];
    if (!d->slot_known) snprintf(label, sizeof label, "%s", d->dev);
    else if (eff_drawer(d->drawer, 0) > 0) snprintf(label, sizeof label, "דיסק %d", eff_drawer(d->drawer, 0));
    else unmapped_label("", d->conn, 0, d->dev, label, sizeof label);
    sdlg_open(a, kind, i, d->dev, label, d->smart, d->smart_reason, d->model, d->serial);
    return 1;
}

/* smart_seen: the SMART warning window was answered "קלוט ממנו בכל זאת" */
static void start_capture(App *a, int smart_seen) {
    char name[96]; snprintf(name, sizeof name, "%s", a->name); trim(name);
    if (a->chosen_disk < 0 || !name[0]) {
        snprintf(a->st.form_error, sizeof a->st.form_error, "בחרו כונן ותנו שם לאימג'");
        return;
    }
    char typed[64]; snprintf(typed, sizeof typed, "%s", a->newfolder_shown ? a->folder_new : ""); trim(typed);
    const char *folder = typed[0] ? typed : a->folder_sel > 0 && a->folder_sel <= a->st.nfolders ? a->st.folders[a->folder_sel - 1] : "";
    if (!folder[0]) {                     /* #1251: a new image always goes into a folder */
        snprintf(a->st.form_error, sizeof a->st.form_error, "בחרו תיקיית יעד, או צרו חדשה ב-\"+ חדשה\"");
        return;
    }
    a->st.form_error[0] = 0;
    if (!smart_seen && disk_smart_ask(a, SDLG_CAPTURE, a->chosen_disk)) return;   /* 16/10: a source with a warning */
    emit("capture-start");
    emit_kv("dev", a->st.disks[a->chosen_disk].dev);
    emit_kv("name", a->name);
    emit_kv("desc", a->desc);
    emit_kv("folder", folder);
    emit_kv("folder_new", typed[0] ? "yes" : "no");     /* resolveFolder: a typed one is created first */
    emit_end();
}

/* #706: write a library image onto THIS machine's disk. The agent rediscovers
 * the internal disk before writing -- the GUI never supplies a device. */
static int restore_ready(App *a) {
    if (a->restore_image_sel <= 0 || a->restore_image_sel > a->st.nimages
        || a->st.images[a->restore_image_sel - 1].unchecked) {   /* #1896: mark C: first */
        snprintf(a->st.form_error, sizeof a->st.form_error, "בחרו אימג' לשחזור");
        return 0;
    }
    /* #1073: the bridge compares confirm= with this machine's registered name
     * (it re-reads it from the server before handing off). #1270: nobody types
     * it any more -- the dialog asked -- so it is filled from machine_name=.
     * No name in the registry -> nothing the bridge will accept -> refuse. */
    if (!a->st.machine_name[0]) {
        snprintf(a->st.form_error, sizeof a->st.form_error, "למחשב הזה אין שם במרשם השרת — לא ניתן לאשר");
        return 0;
    }
    a->st.form_error[0] = 0;
    return 1;
}

static void start_restore(App *a) {
    if (!restore_ready(a)) return;
    emit("restore-start");
    emit_kv("image", a->st.images[a->restore_image_sel - 1].id);
    emit_kv("confirm", a->st.machine_name);
    emit_end();
}

/* #1270 "מחק ושחזר" was confirmed. 16/10: the disk the agent will write --
 * the first non-removable one (pick_internal_disk) -- with a SMART warning
 * asks the SMART warning window, and only its red button restores. */
static void restore_confirmed(App *a) {
    for (int i = 0; i < a->st.ndisks; i++)
        if (!a->st.disks[i].removable) { if (disk_smart_ask(a, SDLG_RESTORE, i)) return; break; }
    start_restore(a);
}

/* 16/10: "כתוב בכל זאת" on the cloner's SMART prompt opens the window; the
 * window answers the request it was opened for (the nonce), or nothing. */
static void cloner_smart_ask(App *a) {
    if (!a->st.smart_pending) return;   /* #1580: red too -- the window and its red button are the confirmation */
    char label[SLOT_NAME_MAX + 64];
    cloner_port_label(&a->st, a->st.smart_port, label, sizeof label);   /* #1495: by the drawer mapping */
    const IdleDisk *id = idle_for_port(&a->st, a->st.smart_port);
    sdlg_open(a, SDLG_CLONER, a->st.smart_port, "", label, a->st.smart_verdict, a->st.smart_reason,
              a->st.smart_model[0] ? a->st.smart_model : id ? id->model : "", a->st.smart_serial);
    snprintf(a->sdlg.nonce, sizeof a->sdlg.nonce, "%s", a->st.smart_nonce);
}

/* The window's answer. "דלג" on the cloner is the prompt's own skip; on this
 * machine's disk it only closes the window -- nothing was sent. */
static int smart_modal(App *a, const Event *e) {
    SmartDlg d;
    if (!sdlg_handle(a, e, &d)) return 0;
    if (d.kind == SDLG_CLONER) {
        if (a->st.smart_pending && !strcmp(a->st.smart_nonce, d.nonce)) emit_smart(a, d.go ? "smart-rescue" : "smart-skip");
    } else if (d.kind == SDLG_RESTORE && d.go) start_restore(a);
    else if (d.kind == SDLG_CAPTURE && d.go) start_capture(a, 1);
    if (e->type == UIEV_CLICK) { a->ptr_x = e->x; a->ptr_y = e->y; }
    return 1;
}

/* #1386: the window's answer -- the cloner cancels its own drawer by device (no
 * sign-in, like the SMART choice); the build machine names the cloner and the
 * drawer, and the bridge posts it with the signed-in user's session. */
static int cancel_modal(App *a, const Event *e) {
    CancelPick d;
    if (!cdlg_handle(a, e, &d)) return 0;
    if (d.dev[0] && a->cdlg.build) { emit("room-drawer-cancel"); emit_kv("cloner", d.mac); emit_kv("dev", d.dev); emit_end(); }
    else if (d.dev[0]) { char tok[48]; snprintf(tok, sizeof tok, "drawer-cancel|%s", d.dev); emit1(tok); }
    if (e->type == UIEV_CLICK) { a->ptr_x = e->x; a->ptr_y = e->y; }
    return 1;
}

static int room_effective_selected(const App *a, const Machine *m, const RoomDrawer *d);

/* #715: the chosen drawers, as the bridge's record value:
 * "<mac>@<port>,<port>/<mac>@<port>". Only present, fresh, awake drawers can
 * be selected (room_toggle_selected), so what is here is what the server
 * validates as target_slots. Returns the number of drawers, 0 when none, and
 * -1 when the value did not fit (#1275: never read as "none" = every drawer). */
static int room_selection_encode(const App *a, char *out, size_t n) {
    size_t used = 0; int drawers = 0;
    out[0] = 0;
    for (int mi = 0; mi < a->st.nmachines; mi++) {
        const Machine *m = &a->st.machines[mi];
        int first = 1;
        for (int i = 0; i < m->nroom_drawers; i++) {
            const RoomDrawer *d = &m->room_drawers[i];
            if (!room_pickable(d)) continue;   /* #1732 */
            if (!room_effective_selected(a, m, d)) continue;
            int k = snprintf(out + used, n - used, "%s%s%s%d",
                             first ? (drawers ? "/" : "") : ",",
                             first ? m->mac : "", first ? "@" : "", d->port);
            if (k < 0 || (size_t)k >= n - used) return -1;  /* truncated = no order */
            used += (size_t)k; first = 0; drawers++;
        }
    }
    return drawers;
}

/* #1269: "<mac>|<port>|<vendor>|<model>|..." -- up to every drawer of the room. */
static char drivers[MAX_ROOM_SELECTIONS * 232];

static void open_direct(App *a) {
    char slots[MAX_ROOM_SELECTIONS * 8 + MAX_MACHINES * 24];
    int drawers = room_selection_encode(a, slots, sizeof slots);
    if (drawers <= 0) {
        snprintf(a->st.room_error, sizeof a->st.room_error, "יש לסמן לפחות דיסק אחד");
        return;
    }
    int with_drivers = room_drv_active(a);              /* #1269: the switch, and models to pick */
    if (with_drivers && room_drv_encode(a, drivers, sizeof drivers) < 0) return;   /* the reason is in room_error */
    a->st.room_error[0] = 0;
    emit("direct-open");
    emit_kv("slots", slots);
    if (with_drivers) emit_kv("drivers", drivers);
    emit_end();
}

static void open_round(App *a) {
    if (a->mode == MODE_DIRECT) { open_direct(a); return; }   /* #715: no image, the disk */
    int target = atoi(a->room_target);
    if (a->image_sel <= 0 || a->image_sel > a->st.nimages || target <= 0) {
        snprintf(a->st.room_error, sizeof a->st.room_error, "בחרו אימג' וקבעו יעד כוננים");
        return;
    }
    /* #1275: the drawers ticked in the grid are the round's targets
     * (target_slots). Owner 11/10: none ticked is refused -- never sent as
     * "every drawer"; "בחר / נקה הכל" ticks a whole room. A selection that
     * did not fit (-1) is refused too, never dropped. */
    char slots[MAX_ROOM_SELECTIONS * 8 + MAX_MACHINES * 24];
    int drawers = room_selection_encode(a, slots, sizeof slots);
    if (drawers <= 0) {
        snprintf(a->st.room_error, sizeof a->st.room_error, "%s",
                 drawers < 0 ? "בחירת המגירות לא נקראה — הסבב לא נפתח" : "יש לסמן לפחות דיסק אחד");
        return;
    }
    /* #1269: drivers go only on chosen disks -- the server binds a model
     * only to a disk chosen as a target. Switch off = no drivers= field. */
    int with_drivers = room_drv_active(a);
    if (with_drivers && room_drv_encode(a, drivers, sizeof drivers) < 0) return;   /* the reason is in room_error */
    a->st.room_error[0] = 0;
    emit("room-open");
    emit_kv("image", a->st.images[a->image_sel - 1].id);
    emit_kv("target", a->room_target);
    emit_kv("slots", slots);
    if (with_drivers) emit_kv("drivers", drivers);
    emit_end();
}

/* Stop a round (#533/#581: the server decides). #1270: the operator answered
 * the confirm dialog; the server still compares confirm_name with the name
 * of the round (round_label = the image the screen shows), so that name goes. */
static void stop_round(const char *token, const char *name) {
    emit(token); emit_kv("confirm", name); emit_end();
}

/* #432: "התחל" on a wave with a precheck warning asks first -- orange, a
 * question and not a block; a clean wave starts at once, as before. */
static void room_start(App *a) {
    if (a->st.has_round && a->st.wave_open && a->st.nroom_warn) a->confirm = CONFIRM_ROOM_START_WARN;
    else emit1("room-start");
}

/* #65: is the machine the power-off dialog was opened for still missing,
 * still answering, and the wave still open? Anything else closes it. */
static int poweroff_target_live(App *a) {
    if (!a->st.has_round || !a->st.wave_open) return 0;
    for (int i = 0; i < a->st.nroom_missing; i++)
        if (!strcmp(a->st.room_missing[i].mac, a->poweroff_mac)) {
            a->poweroff_local_copy = a->st.room_missing[i].local_copy;
            return a->st.room_missing[i].can_poweroff;
        }
    return 0;
}

static int room_local_copies(const State *s) {
    int n = 0; for (int i = 0; i < s->nmachines; i++) n += s->machines[i].local_copy;
    return n;
}

/* #1270: what the confirm dialog's OK does. */
static void confirm_do(App *a) {
    int what = a->confirm;
    a->confirm = CONFIRM_NONE;
    if (what == CONFIRM_RESTORE) restore_confirmed(a);
    else if (what == CONFIRM_ROOM_START_WARN && a->st.has_round && a->st.wave_open) emit1("room-start");
    else if (what == CONFIRM_ROOM_POWEROFF && poweroff_target_live(a)) {
        /* the server checks again: still missing, wave open, the machine's name */
        emit("room-poweroff"); emit_kv("cloner", a->poweroff_mac); emit_kv("confirm", a->poweroff_name);
        if (a->poweroff_local_copy) emit_kv("local_copy", "1");
        emit_end();
    }
    else if (what == CONFIRM_ROOM_POWEROFF_ALL && a->st.room_label[0]) {
        emit("room-poweroff-all"); emit_kv("confirm", a->st.room_label);
        if (room_local_copies(&a->st)) emit_kv("local_copy", "1");
        emit_end();
    }
    else if (what == CONFIRM_ROOM_STOP && a->st.has_round) stop_round("room-close", a->st.round_image);
    else if (what == CONFIRM_CLASS_STOP && a->st.has_session && class_gate(a)) stop_round("class-close", a->st.sess_image);
    else if (what == CONFIRM_TOOL) tool_run(a);
    else if (what == CONFIRM_RESTORE_CANCEL && a->st.can_cancel) emit1("restore-cancel");   /* #1561 */
}

/* #1270: the dialog is modal -- Enter confirms, Esc cancels, its two buttons
 * click; nothing else reaches the screen behind it. */
static int confirm_modal(App *a, const Event *e) {
    if (!a->confirm) return 0;
    if (e->type == UIEV_KEY) {
        if (e->key == KEYSYM_ENTER) confirm_do(a);
        else if (e->key == KEYSYM_ESC) a->confirm = CONFIRM_NONE;
        return 1;
    }
    if (e->type == UIEV_CLICK) {
        a->ptr_x = e->x; a->ptr_y = e->y;
        int id = app_hit(a, e->x, e->y);
        if (id == HIT_CONFIRM_OK) confirm_do(a);
        else if (id == HIT_CONFIRM_CANCEL) a->confirm = CONFIRM_NONE;
        return 1;
    }
    return e->type == UIEV_CHAR;
}

/* #695: the effective target state of a room disk -- an operator override if
 * one exists, else the server's `selected`. */
static int room_effective_selected(const App *a, const Machine *m,
                                   const RoomDrawer *d) {
    for (int i = 0; i < a->nroom_selection; i++)
        if (a->room_selection[i].port == d->port &&
            strcmp(a->room_selection[i].mac, m->mac) == 0)
            return a->room_selection[i].selected;
    return d->selected;
}

static void room_toggle_selected(App *a, int mi, int port) {
    if (mi < 0 || mi >= a->st.nmachines || port < 1 || port > 32) return;
    Machine *m = &a->st.machines[mi];
    RoomDrawer *d = NULL;
    for (int i = 0; i < m->nroom_drawers; i++)
        if (m->room_drawers[i].present && m->room_drawers[i].port == port) {
            d = &m->room_drawers[i];
            break;
        }
    if (!m->awake || !d || !room_pickable(d)) return;   /* #1732 */
    for (int i = 0; i < a->nroom_selection; i++)
        if (a->room_selection[i].port == port &&
            strcmp(a->room_selection[i].mac, m->mac) == 0) {
            a->room_selection[i].selected = !a->room_selection[i].selected;
            return;
        }
    if (a->nroom_selection < MAX_ROOM_SELECTIONS) {
        int i = a->nroom_selection++;
        char mac[24];                            /* local copy: m->mac and the dest are both inside *a (-Wrestrict) */
        snprintf(mac, sizeof mac, "%s", m->mac);
        snprintf(a->room_selection[i].mac, sizeof a->room_selection[i].mac, "%s", mac);
        a->room_selection[i].port = port;
        a->room_selection[i].selected = !room_effective_selected(a, m, d);
    }
}

/* #714: set (not toggle) one disk's override. */
static void room_set_selected(App *a, const Machine *m, int port, int val) {
    const RoomDrawer *drawer = NULL;
    for (int j = 0; j < m->nroom_drawers; j++)
        if (m->room_drawers[j].port == port) { drawer = &m->room_drawers[j]; break; }
    if (!drawer || !room_pickable(drawer)) return;   /* #1732 */
    for (int i = 0; i < a->nroom_selection; i++)
        if (a->room_selection[i].port == port &&
            strcmp(a->room_selection[i].mac, m->mac) == 0) {
            a->room_selection[i].selected = val;
            return;
        }
    if (a->nroom_selection < MAX_ROOM_SELECTIONS) {
        int i = a->nroom_selection++;
        char mac[24];                            /* -Wrestrict: both inside *a */
        snprintf(mac, sizeof mac, "%s", m->mac);
        snprintf(a->room_selection[i].mac, sizeof a->room_selection[i].mac, "%s", mac);
        a->room_selection[i].port = port;
        a->room_selection[i].selected = val;
    }
}

/* #714: are all present disks in [begin,end) machines already selected? At
 * least one present disk is required, so an empty room is "not all selected".
 * Nadav 17/10: and every machine of the range picked as a whole -- an off
 * machine counts, by its pick alone. */
static int room_range_all_selected(const App *a, int begin, int end) {
    int any = 0;
    for (int mi = begin; mi < end; mi++) {
        const Machine *m = &a->st.machines[mi];
        any = 1;
        if (!room_picked(a, m->mac)) return 0;
        if (!m->awake) continue;
        for (int j = 0; j < m->nroom_drawers; j++) {
            const RoomDrawer *d = &m->room_drawers[j];
            if (!room_pickable(d)) continue;   /* #1732 */
            if (!room_effective_selected(a, m, d)) return 0;
        }
    }
    return any;
}

/* Nadav 17/10: pick or drop a whole cloner (by MAC, so a reload that
 * reorders the list keeps it). */
static void room_pick_set(App *a, const char *mac, int val) {
    for (int i = 0; i < a->nroom_pick; i++)
        if (!strcmp(a->room_pick[i], mac)) {
            if (!val) memmove(a->room_pick[i], a->room_pick[i + 1], (size_t)(--a->nroom_pick - i) * sizeof a->room_pick[0]);
            return;
        }
    if (val && a->nroom_pick < MAX_MACHINES) snprintf(a->room_pick[a->nroom_pick++], sizeof a->room_pick[0], "%s", mac);
}

/* #714: select every present disk in [begin,end) machines, or clear them if
 * they are already all selected (one button that toggles). Nadav 17/10: the
 * machines themselves too, off ones included -- they are what "הער" wakes. */
static void room_select_range(App *a, int begin, int end) {
    int val = !room_range_all_selected(a, begin, end);
    for (int mi = begin; mi < end; mi++) {
        Machine *m = &a->st.machines[mi];
        char mac[24];                            /* -Wrestrict: both inside *a */
        snprintf(mac, sizeof mac, "%s", m->mac);
        room_pick_set(a, mac, val);
        if (!m->awake) continue;
        for (int j = 0; j < m->nroom_drawers; j++) {
            const RoomDrawer *d = &m->room_drawers[j];
            if (room_pickable(d)) room_set_selected(a, m, d->port, val);   /* #1732 */
        }
    }
}

/* Nadav 17/10: "הער" wakes the picked cloners and nothing else -- a whole
 * machine (off ones too) or one with a ticked drawer; in a live round, the
 * round's cloners (the grid shows only them). The 16/10 button was grey and
 * sent nothing; the server woke the whole room on any wake. None picked = a
 * toast, never "everyone". */
static void room_wake(App *a) {
    char macs[MAX_MACHINES * 24]; size_t used = 0; int n = 0;
    macs[0] = 0;
    for (int mi = 0; mi < a->st.nmachines; mi++) {
        const Machine *m = &a->st.machines[mi];
        int want = a->st.has_round || room_picked(a, m->mac);
        for (int j = 0; !want && j < m->nroom_drawers; j++)
            want = room_pickable(&m->room_drawers[j]) && room_effective_selected(a, m, &m->room_drawers[j]);
        if (!want) continue;
        int k = snprintf(macs + used, sizeof macs - used, "%s%s", n ? "," : "", m->mac);
        if (k < 0 || (size_t)k >= sizeof macs - used) break;
        used += (size_t)k; n++;
    }
    if (!n) { toast(a, "סמנו מחשב שיכפול אחד לפחות — רק המחשבים שנבחרו מתעוררים"); return; }
    emit("room-wake"); emit_kv("cloner", macs); emit_end();
}

/* #434: a key press or click is activity for the agent's idle clock. Sent at
 * most once per 10 s -- and always while a countdown shows, since that is
 * exactly the press that cancels it. */
static void emit_touch(App *a, int demo) {
    static time_t last = 0; time_t now = time(NULL);
    if (demo) return;
    if (a->st.idle_poweroff_at <= 0 && now - last < 10) return;
    last = now; emit("touch"); emit_end();
}

/* #1560: a count to a power-off is on the screen -- the idle warning (#434),
 * the end-of-task count, or an end question (its subject is the epoch). The
 * loop then wakes on every second, so the number goes 20, 19, 18 -- the 2 s
 * wake made it skip. #2017: the bad-sector question counts to its going on. */
static int counting(const App *a) {
    const int end_ask = a->st.ask_pending && (a->st.ask_kind == ASK_CAPTURE_END ||
        a->st.ask_kind == ASK_SEND_END || a->st.ask_kind == ASK_ROUND_END || a->st.ask_kind == ASK_BADSECT);
    return a->st.idle_poweroff_at > 0 || a->st.end_poweroff_at > 0 || end_ask;
}

/* #1560: while the end-of-task count shows (screens_ask.c draw_end_dialog) it
 * is modal over every screen, the local clone's included: Enter or "כבה
 * עכשיו" says end-poweroff -- the agent powers off now. Some targets failed:
 * Esc or "עצור את הספירה" says end-hold, and the count becomes a bar over
 * the results (held) -- the screen behind it is the screen again, and the
 * bar's one button (or Enter) still powers off. */
static int end_modal(App *a, const Event *e) {
    if (a->st.end_kind == END_NONE) return 0;     /* #2024: a held end has epoch 0 */
    if (e->type == UIEV_MOVE || e->type == UIEV_CLICK) { a->ptr_x = e->x; a->ptr_y = e->y; }
    if (e->type == UIEV_MOVE) a->ptr_visible = !e->is_touch;
    const int hit = e->type == UIEV_CLICK ? app_hit(a, e->x, e->y) : -1;
    if ((e->type == UIEV_KEY && e->key == KEYSYM_ENTER) || hit == HIT_END_NOW) { emit1("end-poweroff"); return 1; }
    if (a->st.end_held) return 0;                  /* the bar: the rest is the screen's */
    if (a->st.end_kind == END_PARTIAL || a->st.end_kind == END_FAILED) {
        if ((e->type == UIEV_KEY && e->key == KEYSYM_ESC) || hit == HIT_END_HOLD) emit1("end-hold");
    }
    return 1;
}

/* #1918: the question's answer, once per question (its nonce). */
static void power_answer(App *a, const char *answer) {
    char line[64];
    snprintf(line, sizeof line, "%s|%s", a->st.power_nonce, answer);
    if (power_drop(a, "power.answer", line)) snprintf(a->power_sent, sizeof a->power_sent, "%s", a->st.power_nonce);
    else toast(a, "התשובה לא נמסרה למחשב — נסו שוב");
}

/* #1918: the power button. A press goes to the agent, which decides: off
 * when nothing writes, else the question (screens_ask.c draw_power_dialog).
 * The question is modal over every screen, the local clone's included: Esc,
 * Enter or "המשך" keep the write going; Del or the red "עצור וכבה" stop it
 * and power off -- only where it has a stop (can_stop). */
static int power_modal(App *a, const Event *e) {
    if (e->type == UIEV_KEY && e->key == KEYSYM_POWER) {
        if (!power_drop(a, "power.press", "pressed")) toast(a, "כפתור ההפעלה: הלחיצה לא נמסרה למחשב");
        return 1;
    }
    if (!power_ask_open(a) || e->type == UIEV_MOVE) return 0;
    int hit = -1;
    if (e->type == UIEV_CLICK) { a->ptr_x = e->x; a->ptr_y = e->y; hit = app_hit(a, e->x, e->y); }
    if ((e->type == UIEV_KEY && (e->key == KEYSYM_ESC || e->key == KEYSYM_ENTER)) || hit == HIT_PWR_KEEP)
        power_answer(a, "keep");
    else if (a->st.power_can_stop && ((e->type == UIEV_KEY && e->key == KEYSYM_DELETE) || hit == HIT_PWR_STOP))
        power_answer(a, "stop");
    return 1;
}

/* mockup 10: the idle window (screens.c draw_idle_countdown) -- Enter or "כבה עכשיו" powers off
 * now (idle-poweroff, idle.sh), and is not a touch; any other key or click keeps it on (touch). */
static int idle_modal(App *a, const Event *e, int demo) {
    if (a->st.idle_poweroff_at <= 0 || demo) return 0;
    int off = e->type == UIEV_KEY && e->key == KEYSYM_ENTER;
    if (e->type == UIEV_CLICK) { a->ptr_x = e->x; a->ptr_y = e->y; off = app_hit(a, e->x, e->y) == HIT_IDLE_OFF; }
    if (!off) { if (e->type == UIEV_MOVE) return 0; emit_touch(a, demo); return 1; }   /* the key keeps it on, and does nothing under it */
    emit1("idle-poweroff");
    return 1;
}

static int handle(App *a, const Event *e, const char *auth_cmd, int demo) {
    if (idle_modal(a, e, demo)) return 0;          /* mockup 10: before the touch that would cancel it */
    if (e->type == UIEV_CLICK || e->type == UIEV_CHAR || e->type == UIEV_KEY) emit_touch(a, demo);
    /* #1499: the theme switch first -- every screen has it, modals and the
     * local clone's own key handling included. */
    if ((e->type == UIEV_KEY && e->key == KEYSYM_F2 && !app_usbset_window(a)) ||   /* #2001: F2 saves there */
        (e->type == UIEV_CLICK && app_hit(a, e->x, e->y) == HIT_THEME)) {
        toggle_theme(a);
        return 0;
    }
    if (power_modal(a, e)) return 0;               /* #1918: before every screen's own keys */
    if (end_modal(a, e)) return 0;                 /* #1560: before the local clone's own keys */
    if (a->screen == SCREEN_LCBOOT || a->screen == SCREEN_LOCAL) {   /* #1322: its own keys and clicks */
        if (e->type == UIEV_MOVE || e->type == UIEV_CLICK) { a->ptr_x = e->x; a->ptr_y = e->y; }
        if (e->type == UIEV_MOVE) a->ptr_visible = !e->is_touch;
        lc_handle(a, e);
        return 0;
    }
    if (smart_modal(a, e)) return 0;               /* 16/10: the SMART warning window */
    if (!demo && standby_handle(a, e)) return 0;   /* #2079 (mockup 02): back to the boot choice */
    if (a->screen == SCREEN_FAILURE) {             /* #2068: its buttons -- Enter is the first; the agent acts (hold.sh) */
        if (e->type == UIEV_MOVE || e->type == UIEV_CLICK) { a->ptr_x = e->x; a->ptr_y = e->y; }
        if (e->type == UIEV_MOVE) a->ptr_visible = !e->is_touch;
        int b = -1, h = e->type == UIEV_CLICK ? app_hit(a, e->x, e->y) : -1;
        if (e->type == UIEV_KEY && e->key == KEYSYM_ENTER) b = 0;
        else if (h >= HIT_FAIL_BTN && h <= HIT_FAIL_BTN_LAST) b = h - HIT_FAIL_BTN;   /* HIT_FAIL_BTN_LAST - HIT_FAIL_BTN + 1 == MAX_FAIL_BTNS */
        if (b >= 0 && b < a->st.nfail_btns && !demo) {
            if (power_drop(a, "fail.action", a->st.fail_btns[b].action)) toast(a, "בוצע — המחשב מטפל בבקשה");
            else toast(a, "הבקשה לא נמסרה למחשב — נסו שוב");
        }
        return 0;
    }
    if (cancel_modal(a, e)) return 0;              /* #1386: "בטל דיסק זה" */
    if (a->sysp_open && syspart_modal(a, e)) return 0;   /* #1896: the C: picker, over the restore screen */
    /* 16/10: the cloner's restore screen -- its drawers, its way back, its red
     * button; the folders and the images below are the build machine's */
    if (a->screen == SCREEN_RESTORE && lc_pull_active(a)) {
        if (e->type == UIEV_CLICK) { a->ptr_x = e->x; a->ptr_y = e->y; }
        if (lc_pull_handle(a, e)) return 0;
    }
    if (shrink_modal(a, e)) return 0;              /* #928 */
    if (ask_modal(a, e)) return 0;                 /* 11/10 */
    if (logout_modal(a, e)) return 0;              /* mockup 10/10 */
    if (confirm_modal(a, e)) return 0;             /* #1270 */
    switch (e->type) {
    case UIEV_MOVE:
        a->ptr_x = e->x; a->ptr_y = e->y; a->ptr_visible = !e->is_touch;
        break;
    case UIEV_CLICK: {
        a->ptr_x = e->x; a->ptr_y = e->y;
        int id = app_hit(a, e->x, e->y);
        /* the folder cells and "כל התיקיות" belong to the open list too */
        if (id == HIT_PICK_FOLDERS_BACK) { a->pick_in_folder = 0; break; }
        if (id >= HIT_PICK_FOLDER_BASE && id < HIT_PICK_FOLDER_BASE + MAX_FOLDERS) { pick_open_folder(a, id - HIT_PICK_FOLDER_BASE); break; }
        if (a->dd_open && !(id >= HIT_OPTION_BASE)) {   /* click outside the open list closes it */
            a->dd_open = 0;
            break;
        }
        /* #695 room-disk toggle. Bounded ranges below are essential: the
         * base ids overlap in value order (300<400), so an unbounded
         * `>= HIT_OPTION_BASE` would swallow room-disk clicks. */
        if (id >= HIT_ROOM_DISK_BASE &&
            id < HIT_ROOM_DISK_BASE + MAX_ROOM_SELECTIONS) {
            int encoded = id - HIT_ROOM_DISK_BASE;
            int mi = encoded / MAX_DRAWERS, j = encoded % MAX_DRAWERS;
            if (mi < a->st.nmachines && j < a->st.machines[mi].nroom_drawers)
                room_toggle_selected(a, mi, a->st.machines[mi].room_drawers[j].port);
            break;
        }
        /* #1269: a disk's drivers tag (pick mode): the current model, or "בלי" */
        if (id >= HIT_ROOM_DTAG_BASE &&
            id < HIT_ROOM_DTAG_BASE + MAX_ROOM_SELECTIONS) {
            int encoded = id - HIT_ROOM_DTAG_BASE;
            int mi = encoded / MAX_DRAWERS, j = encoded % MAX_DRAWERS;
            if (mi < a->st.nmachines && j < a->st.machines[mi].nroom_drawers)
                room_drv_tag_click(a, mi, a->st.machines[mi].room_drawers[j].port);
            break;
        }
        /* #65 "כבה אותו": asks first (#1270); the machine's name is the confirmation */
        if (id >= HIT_BAY_CANCEL_BASE && id < HIT_BAY_CANCEL_BASE + MAX_CANCEL_PICKS) {   /* #1386: a row's "בטל…" */
            cdlg_open(a, a->screen == SCREEN_ROOM, id - HIT_BAY_CANCEL_BASE, 0);
            break;
        }
        if (id >= HIT_IDENTIFY_BASE && id < HIT_IDENTIFY_BASE + MAX_DRAWERS) {   /* #1966: the agent reads it */
            int k = id - HIT_IDENTIFY_BASE;
            if (k < a->st.nidisks && a->st.idisks[k].dev[0])
                toast(a, power_drop(a, "identify-request", a->st.idisks[k].dev)
                         ? "נורית המגירה תהבהב כ-15 שניות" : "הבקשה לא נמסרה למחשב — נסו שוב");
            break;
        }
        if (id >= HIT_ROOM_POWEROFF_BASE && id < HIT_ROOM_POWEROFF_BASE + MAX_ROOM_NOTES) {
            int i = id - HIT_ROOM_POWEROFF_BASE;
            if (i < a->st.nroom_missing && a->st.room_missing[i].can_poweroff) {
                snprintf(a->poweroff_mac, sizeof a->poweroff_mac, "%s", a->st.room_missing[i].mac);
                snprintf(a->poweroff_name, sizeof a->poweroff_name, "%s",
                         a->st.room_missing[i].name[0] ? a->st.room_missing[i].name : a->st.room_missing[i].mac);
                a->poweroff_local_copy = a->st.room_missing[i].local_copy;
                a->confirm = CONFIRM_ROOM_POWEROFF;
            }
            break;
        }
        /* #714: machine name toggles all of that machine's disks. */
        if (id >= HIT_ROOM_MACHINE_BASE &&
            id < HIT_ROOM_MACHINE_BASE + MAX_MACHINES) {
            int mi = id - HIT_ROOM_MACHINE_BASE;
            room_select_range(a, mi, mi + 1);
            break;
        }
        /* #649: a tool row, or a disk row of the confirm view */
        if (id >= HIT_TOOL_BASE && id < HIT_TOOL_BASE + MAX_TOOLS) { tool_open(a, id - HIT_TOOL_BASE); break; }
        if (id >= HIT_TOOL_DISK_BASE && id < HIT_TOOL_DISK_BASE + MAX_DISKS) { a->tool_disk_sel = id - HIT_TOOL_DISK_BASE; break; }
        if (id >= HIT_OPTION_BASE && id < HIT_OPTION_BASE + MAX_IMAGES + 1) {
            int i = id - HIT_OPTION_BASE;
            if (a->dd_open == HIT_FOLDER) a->folder_sel = i + 1;   /* #1251: 1-based, no "no folder" row */
            else if (a->dd_open == HIT_ROOM_IMAGE) a->image_sel = i;
            else if (a->dd_open == HIT_RESTORE_IMAGE) a->restore_image_sel = i;
            else if (a->dd_open == HIT_ROOM_DRV_MODEL) room_drv_choose(a, i);   /* #1269 */
            else if (a->screen == SCREEN_RESTORE && i < a->st.nimages) restore_pick(a, i);   /* #1896: unchecked = the C: picker */
            a->dd_open = 0;
            break;
        }
        if (id >= HIT_CLASS_BASE && id < HIT_CLASS_BASE + MAX_CLASSES) {
            int i = id - HIT_CLASS_BASE;
            if (i < a->st.nclasses) {
                if (!a->st.classes[i].machines) { toast(a, "לכיתה הזו אין עדיין מחשבים רשומים."); break; }
                emit("class-pick"); emit_kv("group", a->st.classes[i].id); emit_end();
            }
            break;
        }
        if (id >= HIT_DISK_BASE && id < HIT_DISK_BASE + MAX_DISKS) {
            a->chosen_disk = id - HIT_DISK_BASE;
            a->focus = HIT_NAME;                         /* $("#st-name").focus() */
            break;
        }
        switch (id) {
        case HIT_LOGOUT: sign_out(a); break;     /* the button is the explicit choice; Esc asks */
        case HIT_USER: case HIT_PASS: case HIT_NAME: case HIT_DESC: case HIT_FOLDER_NEW:
        case HIT_ROOM_TARGET: case HIT_TOOL_ARG:
            a->focus = id; break;
        case HIT_EYE:    a->show_pw = !a->show_pw; break;
        case HIT_LANG: a->lang_he = !a->lang_he; break;   /* #1245: the status bar chip; the focus stays */
        case HIT_SUBMIT: do_login(a, auth_cmd, demo); break;
        case HIT_CAPTURE: case HIT_RESTORE: case HIT_ROOM: case HIT_DIRECT: case HIT_CLASSES: case HIT_TOOLS: return choose_card(a, id);
        case HIT_FOLDER: case HIT_ROOM_IMAGE: case HIT_RESTORE_IMAGE: case HIT_ROOM_DRV_MODEL: a->dd_open = id; break;
        /* #1269: "להוסיף דרייברים" and its two modes */
        case HIT_ROOM_DRV_SWITCH: a->drv_on = !a->drv_on; room_drv_after_reload(a); break;
        case HIT_ROOM_DRV_ALL:    a->drv_pick = 0; break;
        case HIT_ROOM_DRV_PICK:   a->drv_pick = 1; break;
        case HIT_NEWFOLDER:
            a->newfolder_shown = !a->newfolder_shown;
            if (a->newfolder_shown) a->focus = HIT_FOLDER_NEW;
            break;
        case HIT_START:  start_capture(a, 0); break;
        case HIT_RESTORE_START: if (restore_ready(a)) a->confirm = CONFIRM_RESTORE; break;   /* #1270: asks first */
        case HIT_RESTORE_CANCEL: if (a->st.can_cancel) a->confirm = CONFIRM_RESTORE_CANCEL; break;   /* #1561: asks first */
        case HIT_BACK:   go_menu(a); emit1("back"); break;
        case HIT_AGAIN:  a->showing_done = 0; go_menu(a); emit1("again"); break;
        case HIT_ROOM_SELECT_ALL: room_select_range(a, 0, a->st.nmachines); break;
        case HIT_ROOM_OPEN:  open_round(a); break;
        case HIT_ROOM_WAKE:  room_wake(a); break;   /* Nadav 17/10: the picked cloners only */
        case HIT_CDLG_OPEN:  cdlg_open(a, 1, 0, 1); break;   /* #1386: "בטל דיסק…", the choice among the writers */
        case HIT_ROOM_START: if (a->st.has_round) room_start(a); break;   /* #432: asks first on a warning */
        case HIT_ROOM_CLOSE: if (a->st.has_round) a->confirm = CONFIRM_ROOM_STOP; break;   /* #1270 */
        case HIT_ROOM_POWEROFF_ALL: if (a->admin && a->st.room_label[0]) a->confirm = CONFIRM_ROOM_POWEROFF_ALL; break;
        case HIT_CLASS_START: if (class_gate(a)) emit1("class-start"); break;
        case HIT_CLASS_CLOSE: if (class_gate(a)) a->confirm = CONFIRM_CLASS_STOP; break;   /* #1270 */
        case HIT_SMART_REPLACE: emit_smart(a, "smart-replace"); break;
        case HIT_SMART_RESCUE:  cloner_smart_ask(a); break;   /* 16/10: the window first */
        case HIT_SMART_SKIP:    emit_smart(a, "smart-skip");    break;
        /* #649 */
        case HIT_TOOL_LIST:  if (!a->tool_running) { a->tools_view = TOOLS_LIST; a->tool_error[0] = 0; } break;
        case HIT_TOOL_RUN:   tool_go(a); break;
        case HIT_TOOL_AGAIN:
            if (a->tool_running || a->tool_sel < 0) break;
            /* an argument, or the confirmation of rw/destroy, is asked again -- never replayed */
            if (tools_risk_level(a->tools[a->tool_sel].risk) || a->tools[a->tool_sel].args[0])
                 a->tools_view = TOOLS_CONFIRM;
            else tool_run(a);
            break;
        case HIT_TOOL_DOWN:
            if (a->tools_view == TOOLS_OUTPUT) a->tool_scroll += 8; else a->tool_list_scroll += 6;
            break;
        case HIT_TOOL_UP:
            if (a->tools_view == TOOLS_OUTPUT) { a->tool_scroll -= 8; if (a->tool_scroll < 0) a->tool_scroll = 0; }
            else { a->tool_list_scroll -= 6; if (a->tool_list_scroll < 0) a->tool_list_scroll = 0; }
            break;
        }
        break;
    }
    case UIEV_CHAR: {
        if (a->screen == SCREEN_MENU && e->ch >= '1' && e->ch <= '5') {
            int ids[5], n = visible_card_ids(a, ids), k = e->ch - '1';
            if (k < n) a->menu_focus = k;
            break;
        }
        /* owner, 10/10: folder-first -- 1-9 picks a folder, then an image in it */
        if (a->screen == SCREEN_RESTORE && e->ch >= '1' && e->ch <= '9') {
            int image = pick_digit(a, e->ch - '1');
            if (image >= 0) restore_pick(a, image);   /* #1896 */
            break;
        }
        if (a->screen == SCREEN_ROOM && a->dd_open == HIT_ROOM_IMAGE && e->ch >= '1' && e->ch <= '9') {
            int image = pick_digit(a, e->ch - '1');
            if (image >= 0) { a->image_sel = image + 1; a->dd_open = 0; }
            break;
        }
        /* [ ] walk the images of the open folder */
        if (a->screen == SCREEN_RESTORE && a->pick_in_folder && (e->ch == '[' || e->ch == ']')) {
            int idx[MAX_IMAGES], n = folder_images(&a->st, a->pick_folder, idx, MAX_IMAGES), at = -1;
            if (!n) break;
            for (int i = 0; i < n; i++) if (idx[i] + 1 == a->restore_image_sel) at = i;
            if (at < 0) at = 0;
            for (int step = 0; step < n; step++) {   /* #1896: an unchecked image is never the one restored */
                at = e->ch == ']' ? (at + 1) % n : (at + n - 1) % n;
                if (!a->st.images[idx[at]].unchecked) { a->restore_image_sel = idx[at] + 1; break; }
            }
            break;
        }
        /* W wakes the picked cloners (Nadav 17/10); N acts on a round -- without one it sends nothing */
        if (a->screen == SCREEN_ROOM && (e->ch == 'w' || e->ch == 'W')) { room_wake(a); break; }
        if (a->screen == SCREEN_ROOM && (e->ch == 'n' || e->ch == 'N')) { if (a->st.has_round) room_start(a); break; }
        /* #1386: X on the build machine -- the drawer numbers repeat between cloners, so one key
         * and a choice; on the cloner the drawer's own number opens it on that drawer */
        if (a->screen == SCREEN_ROOM && (e->ch == 'x' || e->ch == 'X')) { if (a->st.has_round) cdlg_open(a, 1, 0, 1); break; }
        if (a->screen == SCREEN_CLONER && !a->st.smart_pending && e->ch >= '1' && e->ch <= '9') {
            CancelPick p[MAX_CANCEL_PICKS]; int n = cloner_cancel_picks(a, p, MAX_CANCEL_PICKS);
            for (int i = 0; i < n; i++) if (p[i].drawer == e->ch - '0') { cdlg_open(a, 0, i, 0); break; }
            break;
        }
        if (a->screen == SCREEN_CLONER && a->st.smart_pending) {
            if (e->ch == 's' || e->ch == 'S') { emit_smart(a, "smart-skip"); break; }
            if (e->ch == 'r' || e->ch == 'R') { emit_smart(a, "smart-replace"); break; }
        }
        size_t n; char *buf = field_buf(a, a->focus, &n);
        if (!buf || !field_visible(a, a->focus)) break;
        if (a->focus == HIT_ROOM_TARGET && (e->ch < '0' || e->ch > '9')) break;   /* type=number */
        /* #1245: the shortcuts above go by the physical key (e->ch, US) in
         * either language; only the text typed into a field follows it */
        field_append(buf, n, a->lang_he && !field_english_only(a->focus) ? e->he : e->ch);
        break;
    }
    case UIEV_KEY: {
        if (e->key == KEYSYM_LAYOUT) { a->lang_he = !a->lang_he; break; }   /* #1245: Alt+Shift */
        if (e->key == KEYSYM_ESC) {
            /* inside a folder of an image picker, Esc goes back to the folders */
            if (a->pick_in_folder && (a->dd_open == HIT_ROOM_IMAGE || (a->screen == SCREEN_RESTORE && !a->dd_open))) {
                a->pick_in_folder = 0; break;
            }
            if (a->dd_open) { a->dd_open = 0; break; }
            if (a->screen == SCREEN_MENU) { a->logout_confirming = 1; break; }   /* asks first (mockup 10/10) */
            /* the live round: Esc opens the stop question (#1270: a dialog; Esc there cancels) */
            if (a->screen == SCREEN_ROOM && a->st.has_round) { a->confirm = CONFIRM_ROOM_STOP; break; }
            if (a->screen == SCREEN_CLASS && !a->st.has_session) { go_menu(a); emit1("back"); break; }   /* like its "חזרה" */
            if (a->screen == SCREEN_RESTORE || a->screen == SCREEN_PICK || a->screen == SCREEN_ROOM || a->screen == SCREEN_TOOLS) { go_menu(a); emit1("back"); break; }
            break;
        }
        if (a->screen == SCREEN_MENU) {
            int ids[5], n = visible_card_ids(a, ids), stops = menu_stops(a, n);
            if (e->key == KEYSYM_TAB)          a->menu_focus = (a->menu_focus + 1) % stops;
            else if (e->key == KEYSYM_BACKTAB) a->menu_focus = a->menu_focus <= 0 ? stops - 1 : a->menu_focus - 1;
            else if (e->key == KEYSYM_UP || e->key == KEYSYM_DOWN ||
                     e->key == KEYSYM_LEFT || e->key == KEYSYM_RIGHT) menu_arrow(a, e->key, n);
            else if (e->key == KEYSYM_ENTER && a->menu_focus >= 0 && a->menu_focus < n)
                return choose_card(a, ids[a->menu_focus]);
            else if (e->key == KEYSYM_ENTER && a->menu_focus == n) sign_out(a);
            else if (e->key == KEYSYM_ENTER && a->menu_focus == n + 1 && a->st.menu_tools)
                return choose_card(a, HIT_TOOLS);
            break;
        }
        if (e->key == KEYSYM_BACKSPACE && a->dd_open == HIT_ROOM_IMAGE) { a->pick_in_folder = 0; break; }
        int ids[8], n = fields_on(a, ids);
        if ((e->key == KEYSYM_TAB || e->key == KEYSYM_BACKTAB) && n) {
            int k = -1;                              /* Shift-Tab walks back (mockup 10/10) */
            for (int i = 0; i < n; i++) if (ids[i] == a->focus) k = i;
            a->focus = e->key == KEYSYM_TAB ? ids[(k + 1) % n] : ids[k <= 0 ? n - 1 : k - 1];
        } else if (e->key == KEYSYM_ENTER) {
            if (a->screen == SCREEN_LOGIN) do_login(a, auth_cmd, demo);
            else if (a->screen == SCREEN_RESTORE && a->restore_image_sel > 0) {
                if (restore_ready(a)) a->confirm = CONFIRM_RESTORE;    /* #1270: Enter asks, Enter again erases */
            }
            else if (a->screen == SCREEN_CLONER && a->st.smart_pending) {
                cloner_smart_ask(a);                          /* 16/10: Enter opens the window, it never writes */
            }
            else if (a->screen == SCREEN_PICK) start_capture(a, 0);
            else if (a->screen == SCREEN_DONE) { a->showing_done = 0; go_menu(a); emit1("again"); }
            else if (a->screen == SCREEN_CLASS && a->st.has_session) { if (class_gate(a)) emit1("class-start"); }
        } else if (e->key == KEYSYM_BACKSPACE) {
            size_t bn; char *buf = field_buf(a, a->focus, &bn);
            int has_text = buf && field_visible(a, a->focus) && buf[0];
            /* an empty field (or none): Backspace leaves the folder; text is edited first */
            if (!has_text && a->pick_in_folder &&
                (a->dd_open == HIT_ROOM_IMAGE || a->screen == SCREEN_RESTORE)) a->pick_in_folder = 0;
            else if (buf && field_visible(a, a->focus)) field_backspace(buf);
        }
        break;
    }
    default: break;
    }
    return 0;
}

/* ---- the state file --------------------------------------------------------------- */

/* After a reload: the things station.js does on a fresh state. */
static void after_reload(App *a) {
    if (!a->mac[0]) {                                 /* index.html opened without ?mac= */
        snprintf(a->st.msg_title, sizeof a->st.msg_title, "חסר מזהה מכונה");
        snprintf(a->st.msg_sub, sizeof a->st.msg_sub, "הדף נפתח בלי ?mac= — הסוכן אמור לספק אותו.");
    }
    if (a->st.toast[0]) toast(a, a->st.toast);
    if (a->st.stale) toast(a, "הסוכן לא מעדכן מצב");
    if (a->chosen_disk >= a->st.ndisks) a->chosen_disk = -1;
    if (a->folder_sel > a->st.nfolders) a->folder_sel = 0;
    if (a->image_sel > a->st.nimages) a->image_sel = 0;
    /* #1270: a question about a round that is gone is closed */
    if ((a->confirm == CONFIRM_ROOM_STOP && !a->st.has_round) ||
        (a->confirm == CONFIRM_CLASS_STOP && !a->st.has_session) ||
        (a->confirm == CONFIRM_ROOM_START_WARN && (!a->st.has_round || !a->st.wave_open)) ||   /* #432 */
        (a->confirm == CONFIRM_ROOM_POWEROFF && !poweroff_target_live(a)) ||   /* #65 */
        (a->confirm == CONFIRM_RESTORE_CANCEL && !a->st.can_cancel)) a->confirm = CONFIRM_NONE;   /* #1561: the write ended */
    /* 16/10: the SMART window about a request that is gone, or a disk that moved, is closed */
    if (a->sdlg.kind == SDLG_CLONER && (!a->st.smart_pending || strcmp(a->st.smart_nonce, a->sdlg.nonce))) a->sdlg.kind = SDLG_NONE;
    if ((a->sdlg.kind == SDLG_RESTORE || a->sdlg.kind == SDLG_CAPTURE) &&
        (a->sdlg.ref >= a->st.ndisks || strcmp(a->st.disks[a->sdlg.ref].dev, a->sdlg.dev))) a->sdlg.kind = SDLG_NONE;
    cdlg_after_reload(a);                                 /* #1386: a drawer that stopped writing closes it */
    room_drv_after_reload(a);                             /* #1269: the first model is the default */
    lc_after_reload(a);                                   /* #1322: default roles, the source to measure */
    /* #408: rate from consecutive reads; a counter that went backwards
     * (new task) or a clock that did not move resets, not divides. */
    if (a->st.updated > a->rate_prev_updated && a->st.bytes >= a->rate_prev_bytes && a->rate_prev_updated > 0)
        a->capture_rate_bps = (double)(a->st.bytes - a->rate_prev_bytes) / (double)(a->st.updated - a->rate_prev_updated);
    else if (a->st.bytes < a->rate_prev_bytes) a->capture_rate_bps = 0;
    a->rate_prev_bytes = a->st.bytes; a->rate_prev_updated = a->st.updated;
}

/* ---- --png: every card, both themes, to files -------------------------------------- */

/* #1569: the failure screen's sample -- mockup PR #1550 case 6 (a capture). Also
 * the "failure" cards of a --state file that carries no fail= block (tests). */
static void sample_failure(State *s) {
    snprintf(s->fail_title, sizeof s->fail_title, "הקליטה נכשלה");
    snprintf(s->fail_sub, sizeof s->fail_sub, "capture did not complete");
    snprintf(s->fail_why, sizeof s->fail_why, "הקליטה לא הסתיימה: ההעלאה לשרת לא הסתיימה (HTTP 503). האימג׳ לא נכנס לספרייה.");
    snprintf(s->fail_safe, sizeof s->fail_safe, "הדיסק של המחשב הזה לא שונה — קליטה רק קוראת ממנו. אפשר להתחיל מחדש.");
    s->fail_hold = 1; s->nfail_ctx = 3;
    snprintf(s->fail_ctx[0].label, 48, "מכונה"); snprintf(s->fail_ctx[0].value, 96, "BUILD-01");
    snprintf(s->fail_ctx[1].label, 48, "אימג׳"); snprintf(s->fail_ctx[1].value, 96, "office365-חדש");
    snprintf(s->fail_ctx[2].label, 48, "יעדים"); snprintf(s->fail_ctx[2].value, 96, "1 מתוך 1 נכשלו או דולגו");
    s->nfail_steps = 1;   /* #2068: one sentence, and the buttons that do it */
    snprintf(s->fail_steps[0], 240, "ההעלאה לשרת נעצרה. הדיסק של המחשב הזה לא שונה.");
    s->nfail_btns = 2;
    snprintf(s->fail_btns[0].action, 16, "retry"); snprintf(s->fail_btns[0].label, 64, "נסה שוב");
    snprintf(s->fail_btns[1].action, 16, "off"); snprintf(s->fail_btns[1].label, 64, "כבה את המחשב");
    s->nfail_code = 4;
    snprintf(s->fail_code[0], 240, "targets/sda/state failed");
    snprintf(s->fail_code[1], 240, "targets/sda/error upload rc=22 (HTTP 503) -> manifest upload failed");
    snprintf(s->fail_code[2], 240, "[agent] ui_error_hold: capture did not complete");
    snprintf(s->fail_code[3], 240, "task 52 · mac 3C:52:82:A1:00:10");
}

static void sample_state(State *s) {
    memset(s, 0, sizeof *s);
    s->ndisks = 2;
    s->disks[0] = (Disk){ .dev = "/dev/sda", .model = "Samsung SSD 870 EVO 500GB", .size_bytes = 500107862016ULL, .has_data = 1 };
    s->disks[1] = (Disk){ .dev = "/dev/nvme0n1", .model = "WD Blue SN580 1TB", .size_bytes = 1000204886016ULL };
    s->nfolders = 2;
    snprintf(s->folders[0], sizeof s->folders[0], "Office");
    snprintf(s->folders[1], sizeof s->folders[1], "הנדסאים");
    s->nimages = 2;
    s->images[0] = (Image){ .id = "img_1", .name = "Office 2024 — סטנדרט", .folder = "Office" };
    s->images[1] = (Image){ .id = "img_2", .name = "SolidWorks 2025", .folder = "הנדסאים" };
    s->nmachines = 3;
    s->machines[0] = (Machine){
        .name = "CLONE-01", .mac = "3C:52:82:A1:00:21", .state = "writing",
        .awake = 1, .joined = 1, .fresh_drawers = 3, .drawer_count = 3,
        .pct = 42, .moving = 1, .nroom_drawers = 3,
        .room_drawers = {
            { .port = 1, .present = 1, .fresh = 1, .selected = 1,
              .size_bytes = 256060514304ULL, .smart = "passed" },
            { .port = 2, .present = 1, .fresh = 1,
              .size_bytes = 500107862016ULL, .smart = "passed" },
            { .port = 3, .present = 1, .fresh = 1,
              .size_bytes = 128035676160ULL, .smart = "warn" } } };
    s->machines[1] = (Machine){
        .name = "CLONE-02", .mac = "3C:52:82:A1:00:22", .state = "done",
        .awake = 1, .joined = 1, .fresh_drawers = 2, .drawer_count = 3,
        .pct = 100, .moving = 1, .nroom_drawers = 2,
        .room_drawers = {
            { .port = 1, .present = 1, .fresh = 1,
              .size_bytes = 256060514304ULL, .smart = "passed" },
            { .port = 2, .present = 1, .fresh = 1,
              .size_bytes = 256060514304ULL, .smart = "passed" } } };
    s->machines[2] = (Machine){
        .name = "CLONE-03", .mac = "3C:52:82:A1:00:23",
        .awake = 0, .drawer_count = 3, .pct = -1 };
    /* #1269: the library's models (room-drivers card) and what the round did
     * with the drivers of CLONE-02's two drawers (room-live card) */
    s->driver_models_read = 2; s->ndriver_models = 2;
    s->driver_models[0] = (DriverModel){ "HP", "HP EliteDesk 800 G6" };
    s->driver_models[1] = (DriverModel){ "LENOVO", "ThinkCentre M70q Gen 3" };
    snprintf(s->machines[1].room_drawers[0].drv_state, sizeof s->machines[1].room_drawers[0].drv_state, "staged");
    snprintf(s->machines[1].room_drawers[0].drv_detail, sizeof s->machines[1].room_drawers[0].drv_detail, "m70q-chipset, m70q-nic");
    snprintf(s->machines[1].room_drawers[1].drv_state, sizeof s->machines[1].room_drawers[1].drv_state, "skipped");
    snprintf(s->machines[1].room_drawers[1].drv_detail, sizeof s->machines[1].room_drawers[1].drv_detail, "no_model");
    s->menu_class = 1;                        /* the sample shows every card (#880) */
    s->menu_tools = 1;                        /* and the toolbox button (v1.1) */
    s->nclasses = 3;
    s->classes[0] = (ClassGroup){ "grp_b12", "כיתה 12", 14 };
    s->classes[1] = (ClassGroup){ "grp_b7", "כיתה 7", 30 };
    s->classes[2] = (ClassGroup){ "grp_lab", "מעבדת רשתות", 0 };
    s->task = TASK_RUNNING;
    snprintf(s->task_name, sizeof s->task_name, "Office 2024 — סטנדרט");
    snprintf(s->task_disk, sizeof s->task_disk, "/dev/sda");
    s->pct = 42; s->moving = 1; s->bytes = 23089744896ULL;
    snprintf(s->round_image, sizeof s->round_image, "Office 2024 — סטנדרט");
    s->wave_number = 2; s->wave_open = 1; s->written = 7; s->target = 24; s->ready = 5; s->remaining = 17;
    s->elapsed_s = 3725; s->rate_bps = 52000000; s->eta_s = 610;      /* #410 */
    s->updated = (long long)time(NULL) - 3;
    snprintf(s->sess_image, sizeof s->sess_image, "Office 2024 — סטנדרט");
    snprintf(s->sess_prefix, sizeof s->sess_prefix, "B12");
    snprintf(s->sess_group, sizeof s->sess_group, "כיתה 12");
    s->sess_open = 1; s->joined = 3; s->expected = 14; s->starts_in = 272;
    s->ndrawers = 3;
    s->drawers[0] = (Drawer){ 1, "sda", "writing", "", 44814663680ULL, 85899345920ULL, 98000000, 419, 0, "", "", 0, "", "", "", "", "", "", "", "", 0, 0 };   /* #410 */
    s->drawers[1] = (Drawer){ 2, "sdb", "failed", "", 0, 85899345920ULL, -1, -1, 0, "", "", 0, "", "", "", "", "", "", "", "", 0, 0 };
    s->drawers[2] = (Drawer){ 3, "sdc", "waiting", "", 0, 85899345920ULL, -1, -1, 0, "", "", 0, "", "", "", "", "", "", "", "", 0, 0 };
    s->cuseds[0] = (CUsed){ "sda", 500107862016ULL, 214748364800LL };   /* #2030: size, bytes in use (a failed write: not measured) */
    s->cuseds[1] = (CUsed){ "sdb", 500107862016ULL, -1 };
    s->cuseds[2] = (CUsed){ "sdc", 240057409536ULL, 61234567890LL };
    s->ncuseds = 3;
    snprintf(s->cloner_image, sizeof s->cloner_image, "Office 2024 — סטנדרט");
    s->smart_pending = 1; s->smart_port = 2;
    snprintf(s->smart_nonce, sizeof s->smart_nonce, "1-2");
    snprintf(s->smart_verdict, sizeof s->smart_verdict, "fail");
    snprintf(s->smart_reason, sizeof s->smart_reason, "health_failed");   /* smart.sh vocabulary */
    snprintf(s->smart_model, sizeof s->smart_model, "WDC WD5000AAKX-08U6AA0");   /* 16/10: the window's details */
    snprintf(s->smart_serial, sizeof s->smart_serial, "WD-WCC2EVC71234");
    /* #928: the capture's shrink question -- drawn on the "shrink" card only */
    s->shrink_pending = 1; s->nshrink = 2;
    snprintf(s->shrink_nonce, sizeof s->shrink_nonce, "3-0");
    s->shrink[0].idx = 3; s->shrink[0].old_bytes = 499000000000ULL; s->shrink[0].new_bytes = 146000000000ULL;
    s->shrink[1].idx = 4; s->shrink[1].old_bytes = 1000000000ULL;   s->shrink[1].new_bytes = 700000000ULL;
    /* 11/10: the grow-back question -- drawn on the "growback" card only
     * ("capture-end" sets its own kind in png_setup) */
    s->ask_pending = 1; s->ask_kind = ASK_GROWBACK; s->nask = 2;
    snprintf(s->ask_nonce, sizeof s->ask_nonce, "4-0");
    snprintf(s->ask_subject, sizeof s->ask_subject, "sda");
    s->ask[0].idx = 3; s->ask[0].fs_only = 0; s->ask[0].now_bytes = 146000000000ULL; s->ask[0].orig_bytes = 499000000000ULL;
    s->ask[1].idx = 4; s->ask[1].fs_only = 1; s->ask[1].now_bytes = 700000000ULL;   s->ask[1].orig_bytes = 1000000000ULL;
    snprintf(s->msg_title, sizeof s->msg_title, "המחשב אינו רשום");
    snprintf(s->msg_sub, sizeof s->msg_sub, "רשמו אותו בקונסולה כמחשב בניית אימג'ים ורעננו.");
    snprintf(s->machine_name, sizeof s->machine_name, "BUILD-01");   /* #1073: the restore card's confirm field */
    /* #1252: --png sample only -- a real screen reads these from the state file */
    snprintf(s->server_name, sizeof s->server_name, "imagesrv-ta");
    snprintf(s->server_addr, sizeof s->server_addr, "10.44.0.1");
    snprintf(s->server_version, sizeof s->server_version, "v0.59.2");
    s->hello_age = 4; s->hello_rc = 200;
    sample_failure(s);
}

static const char *PNG_CARDS[] = { "login", "menu", "pick", "progress", "done", "room", "room-live",
                                   "class", "class-live", "cloner", "standby", "message", "restore", "direct",
                                   "tools", "tools-confirm", "tools-output", "shrink", "logout",
                                   "restore-folder", "room-images", "restore-confirm", "room-drivers", "pick-hebrew",
                                   "room-start-warn", "room-poweroff", "room-poweroff-local-copy",
                                   "room-poweroff-all",
                                   "growback", "capture-end", "send-end", "round-end", "end-countdown",   /* #1560 */
                                   "cloner-end",   /* #2024 */
                                   "cloner-smart", "restore-smart", "pick-smart",   /* 16/10 */
                                   "failure",   /* #1569; #2068: no technical lines, buttons */
                                   "cloner-cancel", "cloner-canceled", "room-cancel", "room-canceled",   /* #1386 */
                                   "cloner-raid", "cloner-share", "cloner-badsect",   /* #1808/#1898; #2017 */
                                   "restore-syspart" };   /* #1896: the C: picker over the restore screen */

static int png_builtin;   /* #1386: --png on the built-in sample, not a --state file */

/* #1808/#1898: a cloner drawer's question -- the state file's own, or the
 * sample: drawer 2 orange ("asking") with old Intel RAID metadata; the split
 * of a dual-boot image whose C: comes before the linux root. */
static void png_drawer_ask(App *a, const State *base, const char *card) {
    const int kind = !strcmp(card, "cloner-raid") ? ASK_RAID : !strcmp(card, "cloner-badsect") ? ASK_BADSECT : ASK_SHARE;
    a->force_cloner = 1; a->st.smart_pending = 0;
    if (!base->ndrawers && !base->cloner_image[0])
        snprintf(a->st.cloner_image, sizeof a->st.cloner_image, "Office 2024 — סטנדרט");
    if (base->ask_pending && base->ask_kind == kind) { a->st.ask_pending = 1; return; }
    a->st.ask_pending = 1; a->st.ask_kind = kind;
    snprintf(a->st.ask_nonce, sizeof a->st.ask_nonce, "6-0");
    snprintf(a->st.ask_subject, sizeof a->st.ask_subject, "sdb");
    memset(a->st.ask, 0, sizeof a->st.ask);
    if (kind == ASK_BADSECT) {   /* #2017: the source, disk 1, partition 3; going on in 45 s */
        a->st.nask = 1; a->st.ask[0].port = 1; a->st.ask[0].idx = 3; a->st.ask[0].until = (long long)time(NULL) + 45;
    } else if (kind == ASK_RAID) {
        a->st.nask = 1; a->st.ask[0].port = 2;
        snprintf(a->st.ask[0].word, sizeof a->st.ask[0].word, "isw_raid_member");
        for (int i = 0; i < a->st.ndrawers; i++)
            if (!strcmp(a->st.drawers[i].dev, "sdb")) {
                snprintf(a->st.drawers[i].state, sizeof a->st.drawers[i].state, "asking");
                snprintf(a->st.drawers[i].error, sizeof a->st.drawers[i].error, "מידע RAID ישן על הדיסק (isw_raid_member) — ממתין לאישור");
            }
    } else {
        a->st.nask = 4;
        a->st.ask[0].port = 1; a->st.ask[0].idx = 3; a->st.ask[0].now_bytes = 21474836480ULL; a->st.ask[0].orig_bytes = 166429982720ULL;
        a->st.ask[1].port = 1; a->st.ask[1].idx = 5; a->st.ask[1].now_bytes = 32212254720ULL; a->st.ask[1].orig_bytes = 104152760320ULL;
        a->st.ask[2].port = 2; a->st.ask[2].idx = 3; a->st.ask[2].now_bytes = 21474836480ULL; a->st.ask[2].orig_bytes = 166429982720ULL;
        a->st.ask[3].port = 2; a->st.ask[3].idx = 5; a->st.ask[3].now_bytes = 32212254720ULL; a->st.ask[3].orig_bytes = 104152760320ULL;
    }
}

/* #1386: the cancel cards -- the state's own writing drawers when it has them
 * (a --state file from a test), else the mockup's: cloner disks 1 and 2 writing
 * (63% / 41%); the build machine's two cloners writing. */
static void png_cancel_sample(App *a, const char *card) {
    CancelPick p[MAX_CANCEL_PICKS];
    int room = !strncmp(card, "room-", 5);
    a->st.smart_pending = 0;
    if (room) {
        a->mode = MODE_ROOM; a->st.has_round = 1; a->st.wave_open = 0; a->st.wave_number = 1;
        if (png_builtin || !room_cancel_picks(a, p, MAX_CANCEL_PICKS))   /* a state without a live round: the sample's */
            for (int i = 0; i < 2 && i < a->st.nmachines; i++) {
                Machine *m = &a->st.machines[i]; snprintf(m->state, sizeof m->state, "writing");
                for (int j = 0; j < m->nroom_drawers; j++) {
                    RoomDrawer *d = &m->room_drawers[j];
                    d->own_progress = 1; d->pct = j ? 81 : 37; d->selected = 1;
                    snprintf(d->tstate, sizeof d->tstate, "%s", j < 2 ? "writing" : "");
                    snprintf(d->dev, sizeof d->dev, "sd%c", 'a' + j);
                    snprintf(d->model, sizeof d->model, "%s", j ? "Crucial MX500" : "Samsung SSD 870 EVO");
                }
            }
        if (!strcmp(card, "room-canceled") && a->st.nmachines) {
            RoomDrawer *d = &a->st.machines[0].room_drawers[0];
            snprintf(d->tstate, sizeof d->tstate, "cancelled"); d->cancel_done = 1;
            snprintf(d->cancel_text, sizeof d->cancel_text, "בוטל ממחשב הבנייה (admin) · 16:06");
        } else cdlg_open(a, 1, 0, 1);
        return;
    }
    a->force_cloner = 1;
    if (png_builtin || !cloner_cancel_picks(a, p, MAX_CANCEL_PICKS)) {   /* the mockup's two drawers, with their models */
        a->st.ndrawers = 2; a->st.nidisks = 2;
        snprintf(a->st.machine_name, sizeof a->st.machine_name, "CLONER-02");
        a->st.idisks[0] = (IdleDisk){ .port = 1, .dev = "sda", .model = "Samsung SSD 870 EVO", .size = 500107862016ULL, .smart = "ok" };
        a->st.idisks[1] = (IdleDisk){ .port = 2, .dev = "sdb", .model = "WDC WD5000AAKX", .size = 500107862016ULL, .smart = "ok" };
        a->st.drawers[0] = (Drawer){ 1, "sda", "writing", "", 12285713408ULL, 19500000000ULL, 68000000, 120, 1, "SATA 0", "", 0, "", "", "", "", "", "", "", "", 0, 0 };
        a->st.drawers[1] = (Drawer){ 2, "sdb", "writing", "", 7995000000ULL, 19500000000ULL, 68000000, 240, 2, "SATA 1", "", 0, "", "", "", "", "", "", "", "", 0, 0 };
    }
    int n = cloner_cancel_picks(a, p, MAX_CANCEL_PICKS);
    if (!strcmp(card, "cloner-canceled")) {
        for (int i = 0; i < a->st.ndrawers; i++)
            if (n && !strcmp(a->st.drawers[i].dev, p[n - 1].dev)) {
                Drawer *d = &a->st.drawers[i];
                snprintf(d->state, sizeof d->state, "cancelled"); d->bytes = 0;
                snprintf(d->cancel, sizeof d->cancel, "ממסך מחשב השיכפול"); d->cancel_at = 1792249560;
            }
    } else if (n) cdlg_open(a, 0, n - 1, 0);
}

/* Put the App in the state that routes to <card>; the flags that override
 * the route (message, task, round, session, done) are cleared first so a
 * user-supplied --state file renders every card, not just its own. */
static void png_setup(App *a, const State *base, const char *card) {
    a->st = *base;
    memset(&a->st.lc, 0, sizeof a->st.lc); memset(&a->lcu, 0, sizeof a->lcu);   /* #1322: its own cards below */
    a->st.msg_title[0] = a->st.msg_sub[0] = 0;
    a->st.fail_title[0] = 0;   /* #1569: on its card only */
    a->st.task = TASK_NONE; a->st.has_round = 0; a->st.has_session = 0;
    a->showing_done = a->watching = a->force_progress = a->force_cloner = a->force_standby = 0;
    a->signed_in = 1; a->admin = 1; a->mode = MODE_MENU; a->logout_confirming = 0; a->menu_focus = -1;
    a->chosen_disk = -1; a->confirm = CONFIRM_NONE; a->dd_open = 0;
    memset(&a->sdlg, 0, sizeof a->sdlg);                                       /* 16/10: on its three cards only */
    memset(&a->cdlg, 0, sizeof a->cdlg);                                       /* #1386: on its cards only */
    a->pick_in_folder = 0; a->pick_folder[0] = 0;
    room_drv_reset(a); room_drv_after_reload(a);                               /* #1269: off, but for one card */
    a->focus = HIT_USER; a->lang_he = 0;   /* #1245: every card starts in English (HIT_USER: what main() sets) */
    a->st.shrink_pending = !strcmp(card, "shrink") && base->shrink_pending;   /* #928: one card shows it */
    /* 11/10: the question dialog on its two cards only. A state file that
     * carries a question of its own is shown on its card; the sample's
     * grow-back is turned into the end-of-capture question for that card. */
    a->st.ask_pending = 0; a->ask_sent[0] = 0; a->sysp_open = 0;
    if (!strcmp(card, "growback") && base->ask_pending && base->ask_kind == ASK_GROWBACK) a->st.ask_pending = 1;
    /* #1560: the direct send's and the round's end ask the same way */
    const int end_ask = !strcmp(card, "capture-end") ? ASK_CAPTURE_END : !strcmp(card, "send-end") ? ASK_SEND_END
                      : !strcmp(card, "round-end") ? ASK_ROUND_END : ASK_NONE;
    if (end_ask != ASK_NONE && base->ask_pending) {
        a->st.ask_pending = 1;
        if (base->ask_kind != end_ask) {
            a->st.ask_kind = end_ask; a->st.nask = 0;
            snprintf(a->st.ask_subject, sizeof a->st.ask_subject, "%lld", (long long)time(NULL) + 300);   /* #1560: the epoch */
        }
    }
    /* #1560: the end-of-task count on its card only; the sample counts 20 s.
     * #2024: "cloner-end" -- the end over the cloner's drawers, highlighted: the
     * state file's record and end_problem=, or held with every failed drawer red. */
    a->st.end_poweroff_at = 0; a->st.end_kind = END_NONE; a->st.end_held = 0; a->st.nend_prob = 0;
    if (!strcmp(card, "end-countdown") || !strcmp(card, "cloner-end")) {
        a->st.end_poweroff_at = base->end_kind ? base->end_poweroff_at : (long long)time(NULL) + 20;
        a->st.end_kind = base->end_kind ? base->end_kind : END_DRAWERS;
        a->st.end_held = base->end_held; a->st.end_done = base->end_done; a->st.end_total = base->end_total;
        a->st.nend_prob = base->nend_prob;
    }
    if (!strcmp(card, "cloner-end")) {
        a->force_cloner = 1;   /* no cloner data in the state file: the "cloner" card's sample (#1090) */
        if (!base->ndrawers && !base->cloner_image[0] && !base->smart_pending)
            snprintf(a->st.cloner_image, sizeof a->st.cloner_image, "Office 2024 — סטנדרט");
        if (!base->end_kind) {
            a->st.end_kind = END_PARTIAL; a->st.end_held = 1; a->st.end_poweroff_at = 0;
            for (int i = 0; i < a->st.ndrawers && a->st.nend_prob < MAX_DRAWERS; i++) {
                if (strcmp(a->st.drawers[i].state, "failed")) continue;
                snprintf(a->st.end_prob[a->st.nend_prob].dev, sizeof a->st.end_prob[0].dev, "%s", a->st.drawers[i].dev);
                snprintf(a->st.end_prob[a->st.nend_prob].why, sizeof a->st.end_prob[0].why, "%s",
                         a->st.drawers[i].error[0] ? a->st.drawers[i].error : "סיבה לא ידועה");
                a->st.end_prob[a->st.nend_prob++].red = 1;
            }
        }
    }
    snprintf(a->signed_user, sizeof a->signed_user, "%s", a->user[0] ? a->user : "admin");
    if (!strcmp(card, "login"))         { a->signed_in = 0; }
    else if (!strcmp(card, "pick"))     { a->mode = MODE_CAPTURE; if (base->ndisks) { a->chosen_disk = 0; snprintf(a->name, sizeof a->name, "Office 2024 — סטנדרט"); a->folder_sel = base->nfolders ? 1 : 0; } }
    else if (!strcmp(card, "pick-hebrew")) { a->mode = MODE_CAPTURE;   /* #1245: typing Hebrew into the description */
                                             if (base->ndisks) { a->chosen_disk = 0; snprintf(a->name, sizeof a->name, "Office 2024");
                                                                 snprintf(a->desc, sizeof a->desc, "תמונת בסיס למעבדה");
                                                                 a->folder_sel = base->nfolders ? 1 : 0; }
                                             a->focus = HIT_DESC; a->lang_he = 1; }
    else if (!strcmp(card, "progress")) { a->st.task = base->task == TASK_PENDING ? TASK_PENDING : TASK_RUNNING; }
    else if (!strcmp(card, "shrink"))   { a->st.task = TASK_RUNNING; }   /* #928: the question comes mid-capture */
    else if (!strcmp(card, "capture-end")) { a->st.task = TASK_RUNNING; }   /* 11/10: over the finished capture */
    else if (!strcmp(card, "done"))     { a->st.task = TASK_DONE; a->watching = 1; }
    else if (!strcmp(card, "room"))     { a->mode = MODE_ROOM; a->room_target_set = 0; a->nroom_selection = 0; a->nroom_pick = 0; }
    else if (!strcmp(card, "room-live")){ a->mode = MODE_ROOM; a->st.has_round = 1; }
    else if (!strcmp(card, "direct"))   { a->mode = MODE_DIRECT; a->room_target_set = 0; a->nroom_selection = 0; a->nroom_pick = 0; }   /* #715 */
    else if (!strcmp(card, "room-drivers")) {                                    /* #1269 */
        /* every awake disk chosen, "לבחור דיסקים", the first machine's second disk "בלי" */
        a->mode = MODE_ROOM; a->room_target_set = 0; a->nroom_selection = 0; a->nroom_pick = 0;
        for (int i = 0; i < a->st.nmachines; i++)
            for (int j = 0; a->st.machines[i].awake && j < a->st.machines[i].nroom_drawers; j++)
                if (room_selectable(&a->st.machines[i].room_drawers[j]))
                    room_set_selected(a, &a->st.machines[i], a->st.machines[i].room_drawers[j].port, 1);
        a->drv_on = 1; a->drv_pick = 1;
        for (int i = 0; i < a->st.nmachines; i++)
            if (a->st.machines[i].awake && a->st.machines[i].nroom_drawers > 1) {
                room_drv_tag_click(a, i, a->st.machines[i].room_drawers[1].port);
                break;
            }
    }
    else if (!strcmp(card, "class"))    { a->mode = MODE_CLASSES; }
    else if (!strcmp(card, "class-live")){ a->mode = MODE_CLASSES; a->st.has_session = 1; }
    else if (!strcmp(card, "cloner"))   { a->force_cloner = 1;
                                          /* #1090: an idle cloner routes to SCREEN_STANDBY (app_route), so
                                           * a state file with no cloner data -- a build machine's -- gets
                                           * sample cloner data here: the mirror of "standby" clearing it. */
                                          if (!base->ndrawers && !base->cloner_image[0] && !base->smart_pending)
                                              snprintf(a->st.cloner_image, sizeof a->st.cloner_image, "Office 2024 — סטנדרט"); }
    else if (!strcmp(card, "standby"))  { a->force_standby = 1; a->force_cloner = 1; a->st.ndrawers = 0; a->st.cloner_image[0] = 0; a->st.smart_pending = 0; }
    else if (!strcmp(card, "message"))  { snprintf(a->st.msg_title, sizeof a->st.msg_title, "%s", base->msg_title);
                                          snprintf(a->st.msg_sub, sizeof a->st.msg_sub, "%s", base->msg_sub); }
    else if (!strcmp(card, "failure")) {   /* #1569: the state file's block, or the sample */
        if (base->fail_title[0]) snprintf(a->st.fail_title, sizeof a->st.fail_title, "%s", base->fail_title);
        else sample_failure(&a->st);   /* a state file without the block still renders the card */
    }
    else if (!strcmp(card, "logout"))   { a->logout_confirming = 1; }   /* mockup 10/10: what Esc opens */
    else if (!strcmp(card, "restore-folder")) { a->mode = MODE_RESTORE; if (base->nimages) a->restore_image_sel = 1;
                                                pick_open_folder(a, 0); }   /* owner 10/10: inside a folder */
    else if (!strcmp(card, "restore-confirm")) { a->mode = MODE_RESTORE; if (base->nimages) a->restore_image_sel = 1;
                                                 a->confirm = CONFIRM_RESTORE; }   /* #1270 */
    else if (!strcmp(card, "room-start-warn")) { a->mode = MODE_ROOM; a->st.has_round = 1; a->st.wave_open = 1;
                                                 a->confirm = CONFIRM_ROOM_START_WARN; }   /* #432 */
    else if (!strcmp(card, "room-poweroff") || !strcmp(card, "room-poweroff-local-copy")) { /* #65/#1367 */
        a->mode = MODE_ROOM; a->st.has_round = 1; a->st.wave_open = 1;
        const char *mac = base->nroom_missing ? base->room_missing[0].mac : "3c:52:82:a1:00:22";
        const char *name = base->nroom_missing && base->room_missing[0].name[0] ? base->room_missing[0].name : "CLONE-02";
        snprintf(a->poweroff_mac, sizeof a->poweroff_mac, "%s", mac);
        snprintf(a->poweroff_name, sizeof a->poweroff_name, "%s", name);
        a->poweroff_local_copy = !strcmp(card, "room-poweroff-local-copy");
        a->confirm = CONFIRM_ROOM_POWEROFF;
    }
    else if (!strcmp(card, "room-poweroff-all")) {                                /* #1367 */
        a->mode = MODE_ROOM;
        if (!a->st.room_label[0]) snprintf(a->st.room_label, sizeof a->st.room_label, "מחשבי שיכפול");
        if (!a->st.nmachines) a->st.nmachines = 1;
        if (!room_local_copies(&a->st)) a->st.machines[0].local_copy = 1;
        a->confirm = CONFIRM_ROOM_POWEROFF_ALL;
    }
    else if (!strcmp(card, "room-images")) { a->mode = MODE_ROOM; a->room_target_set = 0; a->nroom_selection = 0; a->nroom_pick = 0;
                                             a->dd_open = HIT_ROOM_IMAGE; }   /* the picker, on the folders */
    else if (!strcmp(card, "restore"))  { a->mode = MODE_RESTORE; if (base->nimages) a->restore_image_sel = 1; }
    else if (!strcmp(card, "cloner-smart")) {                                    /* 16/10: Enter on "כתוב בכל זאת" */
        a->force_cloner = 1;
        if (!a->st.smart_pending) {
            a->st.smart_pending = 1; a->st.smart_port = 2;
            snprintf(a->st.smart_nonce, sizeof a->st.smart_nonce, "1-2");
            snprintf(a->st.smart_verdict, sizeof a->st.smart_verdict, "fail");
            snprintf(a->st.smart_reason, sizeof a->st.smart_reason, "pending_sectors");
        }
        cloner_smart_ask(a);
    }
    else if (!strcmp(card, "restore-smart") || !strcmp(card, "pick-smart")) {    /* 16/10: this machine's disk */
        int pick = !strcmp(card, "pick-smart");
        if (!a->st.ndisks) { a->st.ndisks = 1; a->st.disks[0] = (Disk){ .dev = "sda", .model = "Samsung SSD 870 EVO 500GB", .size_bytes = 500107862016ULL, .has_data = 1 }; }
        Disk *d = &a->st.disks[0];
        if (!smart_level(d->smart)) {
            snprintf(d->smart, sizeof d->smart, "fail"); snprintf(d->smart_reason, sizeof d->smart_reason, "pending_sectors");
            if (!d->serial[0]) snprintf(d->serial, sizeof d->serial, "S5Y2NX0R123456");
            if (!d->slot_known) { d->slot_known = 1; d->drawer = 1; }   /* #1495: "דיסק 1" */
        }
        if (pick) { a->mode = MODE_CAPTURE; a->chosen_disk = 0; snprintf(a->name, sizeof a->name, "Office 2024 — סטנדרט");
                    a->folder_sel = base->nfolders ? 1 : 0; disk_smart_ask(a, SDLG_CAPTURE, 0); }
        else { a->mode = MODE_RESTORE; if (base->nimages) a->restore_image_sel = 1; disk_smart_ask(a, SDLG_RESTORE, 0); }
    }
    else if (!strncmp(card, "cloner-cancel", 13) || !strncmp(card, "room-cancel", 11)) png_cancel_sample(a, card);   /* #1386 */
    else if (!strcmp(card, "cloner-raid") || !strcmp(card, "cloner-share") || !strcmp(card, "cloner-badsect")) png_drawer_ask(a, base, card);   /* #1808/#1898; #2017 */
    else if (!strcmp(card, "restore-syspart")) {                                  /* #1896 */
        a->mode = MODE_RESTORE;
        int at = -1;
        for (int i = 0; i < a->st.nimages && at < 0; i++) if (a->st.images[i].unchecked) at = i;
        if (at < 0 && a->st.nimages < MAX_IMAGES) {   /* the sample: Vm, two 223 GB Windows partitions */
            Image *im = &a->st.images[at = a->st.nimages++];
            memset(im, 0, sizeof *im);
            snprintf(im->id, sizeof im->id, "img_5790e4"); snprintf(im->name, sizeof im->name, "Vm");
            snprintf(im->folder, sizeof im->folder, "%s", a->st.nimages > 1 ? a->st.images[0].folder : "");
            im->unchecked = 1; im->ncand = 2;
            im->cand[0].idx = 1; im->cand[0].size = 223392075264ULL; im->cand[0].used = 880222208ULL;
            im->cand[1].idx = 2; im->cand[1].size = 223392075264ULL; im->cand[1].used = 203075014656ULL;
        }
        if (at >= 0) { a->pick_in_folder = 1; snprintf(a->pick_folder, sizeof a->pick_folder, "%s", a->st.images[at].folder); a->sysp_open = at + 1; }
    }
    else if (!strncmp(card, "tools", 5)) {                                       /* #649 */
        a->mode = MODE_TOOLS; a->tools_view = TOOLS_LIST; a->tool_sel = -1; a->tool_running = 0;
        if (!a->ntools) tools_sample(a);
        int wipe = -1, smart = -1;
        for (int i = 0; i < a->ntools; i++) {
            if (!strcmp(a->tools[i].id, "wipe")) wipe = i;
            if (!strcmp(a->tools[i].id, "smart-health")) smart = i;
        }
        if (!strcmp(card, "tools-confirm")) {
            a->tool_sel = wipe >= 0 ? wipe : 0; a->tools_view = TOOLS_CONFIRM; a->tool_disk_sel = base->ndisks ? 0 : -1;
            a->focus = 0;
        } else if (!strcmp(card, "tools-output")) {
            a->tool_sel = smart >= 0 ? smart : 0; a->tools_view = TOOLS_OUTPUT; a->tool_rc = 2;
            tool_output_set(a, "/dev/sda (Samsung SSD 870 EVO 500GB): תקין -- SMART overall-health PASSED\n"
                                "/dev/nvme0n1 (WD Blue SN580 1TB): לא הצלחנו לבדוק -- smartctl לא הצליח לקרוא את הדיסק (rc 2). זה לא \"תקין\".\n"
                                "\nמה זה אומר: PASSED הוא הדיווח של הדיסק על עצמו, לא ערובה; כשל ב-SMART הוא ראיה מספקת להחלפה.\n");
        }
    }
}

static int render_png(App *a, const State *base, const char *prefix, int w, int h) {
    const Theme *themes[2] = { &THEME_LIGHT, &THEME_DARK };
    a->fixed_clock = 1;   /* renders must not differ by the wall clock (tests diff them) */
    int ncards = (int)(sizeof PNG_CARDS / sizeof PNG_CARDS[0]);
    for (int c = 0; c < ncards; c++) for (int t = 0; t < 2; t++) {
        a->theme = themes[t];
        png_setup(a, base, PNG_CARDS[c]);
        cairo_surface_t *sf = cairo_image_surface_create(CAIRO_FORMAT_RGB24, w, h);
        cairo_t *cr = cairo_create(sf);
        text_log_mark(PNG_CARDS[c], themes[t]->id);
        app_draw(a, cr, w, h);
        cairo_destroy(cr);
        char path[512];
        snprintf(path, sizeof path, "%s-%s-%s.png", prefix, PNG_CARDS[c], themes[t]->id);
        cairo_status_t st = cairo_surface_write_to_png(sf, path);
        cairo_surface_destroy(sf);
        if (st != CAIRO_STATUS_SUCCESS) { fprintf(stderr, "native-gui: %s: %s\n", path, cairo_status_to_string(st)); return 1; }
        /* the route must have landed on the card we asked for -- positive evidence */
        static const Screen want[] = { SCREEN_LOGIN, SCREEN_MENU, SCREEN_PICK, SCREEN_PROGRESS, SCREEN_DONE,
                                       SCREEN_ROOM, SCREEN_ROOM, SCREEN_CLASS, SCREEN_CLASS, SCREEN_CLONER, SCREEN_STANDBY, SCREEN_MESSAGE,
                                       SCREEN_RESTORE,
                                       SCREEN_ROOM, /* "direct" (#715): the room screen in MODE_DIRECT */
                                       SCREEN_TOOLS, SCREEN_TOOLS, SCREEN_TOOLS, /* #649: list, confirm, output */
                                       SCREEN_PROGRESS, /* "shrink" (#928): the dialog over the capture */
                                       SCREEN_MENU, /* "logout": the Esc question over the menu (mockup 10/10) */
                                       SCREEN_RESTORE, /* "restore-folder": the images of the first folder */
                                       SCREEN_ROOM, /* "room-images": the image select open on the folders */
                                       SCREEN_RESTORE, /* "restore-confirm": the #1270 dialog */
                                       SCREEN_ROOM, /* "room-drivers" (#1269): the room setup, drivers on, pick mode */
                                       SCREEN_PICK, /* "pick-hebrew" (#1245): the language chip on "עב" */
                                       SCREEN_ROOM, /* "room-start-warn" (#432): the orange start question */
                                       SCREEN_ROOM, /* "room-poweroff" (#65): "כבה אותו" asks first */
                                       SCREEN_ROOM, /* "room-poweroff-local-copy" (#1367): disk-break warning */
                                       SCREEN_ROOM, /* "room-poweroff-all" (#1367): plural disk-break warning */
                                       SCREEN_MENU, /* "growback" (11/10): the dialog over the menu, at boot */
                                       SCREEN_PROGRESS, /* "capture-end" (11/10): the dialog over the capture */
                                       SCREEN_MENU, SCREEN_MENU, /* "send-end", "round-end" (#1560): the same question */
                                       SCREEN_MENU, /* "end-countdown" (#1560): the count to power-off, over any screen */
                                       SCREEN_CLONER, /* "cloner-end" (#2024): the held end, problem drawers highlighted */
                                       SCREEN_CLONER,  /* "cloner-smart" (16/10): the SMART warning window */
                                       SCREEN_RESTORE, /* "restore-smart" (16/10): the same, before a restore */
                                       SCREEN_PICK,    /* "pick-smart" (16/10): the same, before a capture */
                                       SCREEN_FAILURE, /* "failure" (#1569) */
                                       SCREEN_CLONER, SCREEN_CLONER, /* #1386: the window; a drawer cancelled */
                                       SCREEN_ROOM, SCREEN_ROOM,   /* #1386: X and its choice; cancelled */
                                       SCREEN_CLONER, SCREEN_CLONER, SCREEN_CLONER,   /* #1808/#1898: the drawer's question; #2017: badsect */
                                       SCREEN_RESTORE };   /* #1896: "restore-syspart" */
        /* One entry per card, or want[c] reads past the table -- that is exactly
         * what the lab saw when "direct" was added to PNG_CARDS alone. */
        _Static_assert(sizeof want / sizeof want[0] == sizeof PNG_CARDS / sizeof PNG_CARDS[0],
                       "want[] must have one Screen per PNG_CARDS entry");
        if (a->screen != want[c]) { fprintf(stderr, "native-gui: %s routed to screen %d, not %d\n", path, a->screen, want[c]); return 1; }
        printf("wrote %s\n", path);
    }
    /* #1322: the boot choice and the local clone, one card per mockup state.
     * A --state file that carries lc_ records is drawn as it is (tests). */
    for (int c = 0; c < LC_PNG_NCARDS; c++) for (int t = 0; t < 2; t++) {
        a->theme = themes[t];
        png_setup(a, base, LC_PNG_CARDS[c]);
        if (base->lc.screen) { a->st.lc = base->lc; a->force_cloner = 1; memset(&a->lcu, 0, sizeof a->lcu); lc_after_reload(a); }
        else lc_png_setup(a, LC_PNG_CARDS[c]);
        cairo_surface_t *sf = cairo_image_surface_create(CAIRO_FORMAT_RGB24, w, h);
        cairo_t *cr = cairo_create(sf);
        text_log_mark(LC_PNG_CARDS[c], themes[t]->id);
        app_draw(a, cr, w, h);
        cairo_destroy(cr);
        char path[512];
        snprintf(path, sizeof path, "%s-%s-%s.png", prefix, LC_PNG_CARDS[c], themes[t]->id);
        cairo_status_t st = cairo_surface_write_to_png(sf, path);
        cairo_surface_destroy(sf);
        if (st != CAIRO_STATUS_SUCCESS) { fprintf(stderr, "native-gui: %s: %s\n", path, cairo_status_to_string(st)); return 1; }
        Screen want = a->st.lc.screen == LC_SCREEN_BOOT ? SCREEN_LCBOOT
                    : a->st.lc.screen == LC_SCREEN_PULL ? SCREEN_RESTORE : SCREEN_LOCAL;   /* 16/10 */
        if (a->screen != want) { fprintf(stderr, "native-gui: %s routed to screen %d, not %d\n", path, a->screen, want); return 1; }
        printf("wrote %s\n", path);
    }
    return 0;
}

/* ---- main ------------------------------------------------------------------------ */

int main(int argc, char **argv) {
    App a;
    memset(&a, 0, sizeof a);
    a.theme = &THEME_LIGHT;                         /* owner decision 16/10 (#1499): light by default --
                                                       it replaced the 18/09 mockup's dark default */
    a.focus = HIT_USER;
    a.menu_focus = -1;
    a.chosen_disk = -1;
    a.st.pct = -1;
    a.st.hello_age = -1;
    a.st.hello_rc = -1;
    snprintf(a.title, sizeof a.title, "מחשב בניית אימג'ים");
    const char *auth_cmd = NULL, *png = NULL, *splash = NULL, *backend = "auto", *fb_file = NULL, *fb_mirror = NULL, *screen = NULL, *role = "deploy";
    InstallBridge wizard = { "python3 -m server.wizard.bridge", "/opt/imagectl-src", 0 };
    int install_screen = 0, dcui_screen = 0;
    StateFile sf_state; memset(&sf_state, 0, sizeof sf_state);
    int demo = 0, no_input = 0, pw = 1280, ph = 800;

    for (int i = 1; i < argc; i++) {
        const char *o = argv[i], *v = i + 1 < argc ? argv[i + 1] : NULL;
        if (!strcmp(o, "--demo")) demo = 1;
        else if (!strcmp(o, "--no-input")) no_input = 1;
        else if (!strcmp(o, "--help") || !strcmp(o, "-h")) { fputs(USAGE, stdout); return 0; }
        else if (!v) { fputs(USAGE, stderr); return 2; }
        else if (!strcmp(o, "--mac")) snprintf(a.mac, sizeof a.mac, "%s", argv[++i]);
        else if (!strcmp(o, "--ip")) snprintf(a.ip, sizeof a.ip, "%s", argv[++i]);
        else if (!strcmp(o, "--title")) snprintf(a.title, sizeof a.title, "%s", argv[++i]);
        else if (!strcmp(o, "--user")) snprintf(a.user, sizeof a.user, "%s", argv[++i]);
        else if (!strcmp(o, "--theme")) { a.theme = !strcmp(argv[++i], "dark") ? &THEME_DARK : &THEME_LIGHT; }
        else if (!strcmp(o, "--backend")) backend = argv[++i];
        else if (!strcmp(o, "--fb-file")) fb_file = argv[++i];
        else if (!strcmp(o, "--fb-mirror")) fb_mirror = argv[++i];
        else if (!strcmp(o, "--auth-cmd")) auth_cmd = argv[++i];
        else if (!strcmp(o, "--install-cmd") || !strcmp(o, "--wizard-cmd")) wizard.cmd = argv[++i];
        else if (!strcmp(o, "--install-cwd") || !strcmp(o, "--wizard-cwd")) wizard.cwd = argv[++i];
        else if (!strcmp(o, "--state")) snprintf(sf_state.path, sizeof sf_state.path, "%s", argv[++i]);
        else if (!strcmp(o, "--screen")) screen = argv[++i];
        else if (!strcmp(o, "--signed-in")) { snprintf(a.signed_user, sizeof a.signed_user, "%s", argv[++i]); a.signed_in = 1; }
        else if (!strcmp(o, "--role")) role = argv[++i];
        else if (!strcmp(o, "--png")) png = argv[++i];
        else if (!strcmp(o, "--splash-raw")) splash = argv[++i];
        else if (!strcmp(o, "--size")) { if (sscanf(argv[++i], "%dx%d", &pw, &ph) != 2 || pw < 320 || ph < 240) { fputs(USAGE, stderr); return 2; } }
        else { fprintf(stderr, "unknown option %s\n%s", o, USAGE); return 2; }
    }
    a.default_theme = a.theme;                      /* #1499: what login "auto" and logout return to */
    a.admin = a.signed_in && strcmp(role, "admin") == 0;
    if (screen) {
        if (!strcmp(screen, "progress"))   a.force_progress = 1;
        else if (!strcmp(screen, "class")) a.mode = MODE_CLASSES;
        else if (!strcmp(screen, "cloner")) a.force_cloner = 1;
        else if (!strcmp(screen, "standby")) { a.force_standby = 1; a.force_cloner = 1; }
        else if (!strcmp(screen, "install")) install_screen = 1;
        else if (!strcmp(screen, "dcui")) dcui_screen = 1;
        else { fprintf(stderr, "native-gui: --screen must be progress, class, cloner, standby, install or dcui\n%s", USAGE); return 2; }
    }
    setvbuf(stdout, NULL, _IOLBF, 0);               /* records reach the agent's pipe as they happen */
    signal(SIGPIPE, SIG_IGN);                       /* #2108: as in dcui.c; before install_start, whose first bridge call is below */

    /* The fonts index.html asks for must really be there; a fallback face
     * would render, and nobody would notice the wrong font until a
     * classroom did. Positive evidence, not absence of an error. */
    char missing[128];
    if (!text_fonts_present(missing, sizeof missing)) {
        fprintf(stderr, "native-gui: font family not found: %s (FONTCONFIG_FILE=%s)\n",
                missing, getenv("FONTCONFIG_FILE") ? getenv("FONTCONFIG_FILE") : "(unset)");
        if (dcui_screen && !png) {                  /* #1364: the text screen shows this, not "exit 1" */
            printf("FAIL font family not found: %s\n", missing);
            fflush(stdout);
        }
        return 1;
    }
    if (splash) return splash_render_raw(splash, pw, ph);   /* 17/10: build time, no display */

    if (dcui_screen) {                              /* #1203: its own loop, see dcui.h */
        if (!png) return dcui_main(a.theme);
        if (!sf_state.path[0]) { fputs("native-gui: --screen dcui --png needs --state FRAME\n", stderr); return 2; }
        return dcui_render_png(png, sf_state.path, pw, ph);
    }
    if (png) {
        if (install_screen) {
            install_demo(&a, INSTALL_ROLE);
            return install_render_png(&a, png, pw, ph);
        }
        State base;
        if (sf_state.path[0]) {
            if (state_poll(&sf_state, &base) < 0) return 1;
        } else {
            sample_state(&base); png_builtin = 1;
        }
        return render_png(&a, &base, png, pw, ph);
    }

    a.tool_sel = -1; a.tool_disk_sel = -1; a.tool_rc = -1;
    if (install_screen) {
        install_start(&a, &wizard, demo);
    } else if (sf_state.path[0]) {
        if (state_poll(&sf_state, &a.st) < 0) return 1;   /* a missing file at start is a wrong invocation, say so */
        after_reload(&a);
        /* #649: the toolbox files live next to the state file ($GUI_DIR) */
        snprintf(a.tools_dir, sizeof a.tools_dir, "%s", sf_state.path);
        char *slash = strrchr(a.tools_dir, '/');
        if (slash) *slash = 0; else snprintf(a.tools_dir, sizeof a.tools_dir, ".");
        tools_poll(&a);
    } else {
        after_reload(&a);
    }

    char err[1024];
    Backend *b = backend_open(backend, fb_file, err, sizeof err);
    if (!b) { fprintf(stderr, "native-gui: cannot open a display: %s\n", err); return 1; }
    if (fb_mirror && backend_mirror(b, fb_mirror, err, sizeof err) < 0)
        fprintf(stderr, "native-gui: no frame mirror for the remote monitor: %s\n", err);
    int W = backend_width(b), H = backend_height(b);
    fprintf(stderr, "native-gui: display %dx%d\n", W, H);

    /* A missing input device is NOT fatal: a USB keyboard/mouse often
     * enumerates a second or two after boot. The build machine used to come
     * up to a text login because the one startup scan ran before the keyboard
     * was ready. Keep the display up; input_rescan() in the loop grabs a
     * device the moment it appears (and a hot-plugged mouse too). */
    Input *in = no_input ? NULL : input_open(W, H);
    if (fb_mirror || fb_file) input_want_remote(in);   /* #1475: a monitor serves this screen */

    signal(SIGINT, on_signal); signal(SIGTERM, on_signal);
    cairo_surface_t *sf = backend_surface(b);
    /* Scene cache: app_draw() is Pango-heavy and was re-run every frame, so a
     * mere pointer move re-laid-out the whole screen -- the mouse felt laggy.
     * Now app_draw writes into this cache only when the scene actually changes
     * (state reload, screen/focus/typing/click, toast, or a hover-highlight
     * crossing); a pointer-only move just blits the cache and overlays the
     * cursor. */
    cairo_surface_t *scene = cairo_image_surface_create(CAIRO_FORMAT_RGB24, W, H);
    int done = 0, scene_dirty = 1, need_present = 1, last_hover = -1;
    long long last_age_tick = 0;                    /* #410 */
    struct pollfd fds[32];
    while (!stop_now && !done) {
        if (a.toast[0] && now_s() >= a.toast_until) { a.toast[0] = 0; scene_dirty = 1; }
        if (a.pressed_id && now_s() >= a.pressed_until) { a.pressed_id = 0; scene_dirty = 1; }
        if (scene_dirty) {
            cairo_t *cr = cairo_create(scene);
            app_draw(&a, cr, W, H);
            cairo_destroy(cr);
            scene_dirty = 0; need_present = 1;
        }
        if (need_present) {
            cairo_t *cr = cairo_create(sf);
            cairo_set_source_surface(cr, scene, 0, 0);
            cairo_paint(cr);
            app_draw_pointer(&a, cr);
            cairo_destroy(cr);
            backend_present(b);
            gui_heartbeat();
            need_present = 0;
            lcboot_mark_shown(&a);                  /* the boot choice's count starts now */
        }

        /* setInterval(poll, 2000): wake at least every 2 s for the state
         * file, sooner to take a toast down. */
        int timeout = 2000;
        if (install_polling(&a) && timeout > 1000) timeout = 1000;
        if (a.toast[0]) { int left = (int)((a.toast_until - now_s()) * 1000) + 1; if (left < timeout) timeout = left > 0 ? left : 1; }
        if (a.pressed_id) { int left = (int)((a.pressed_until - now_s()) * 1000) + 1; if (left < timeout) timeout = left > 0 ? left : 1; }
        if (a.tool_running) { timeout = 120; a.tool_spin += 0.4; scene_dirty = 1; }   /* #649: the spinner turns */
        if (a.screen == SCREEN_STANDBY && timeout > 500) { timeout = 500; scene_dirty = 1; }
        if (a.screen == SCREEN_LCBOOT && timeout > 200) { timeout = 200; scene_dirty = 1; }    /* #1322: the count */
        if (a.screen == SCREEN_LOCAL && timeout > 1000) { timeout = 1000; scene_dirty = 1; }
        if (counting(&a)) {   /* #1560: wake just after the next wall-clock second */
            struct timespec ts; clock_gettime(CLOCK_REALTIME, &ts);
            int left = 1000 - (int)(ts.tv_nsec / 1000000) + 5;
            if (left < timeout) timeout = left;
        }
        if (in) input_rescan(in);               /* pick up a late/hot-plugged keyboard or mouse */
        int n = in ? input_fill_pollfds(in, fds, 32) : 0;
        int r = poll(n ? fds : NULL, n, timeout);
        if (r < 0) { if (errno == EINTR) continue; break; }
        gui_heartbeat(); /* a static scene still proves the event loop is running */
        if (r > 0 && in) {
            input_pump(in, fds, n);
            Event e; int moved = 0;
            while (!done && input_next(in, &e)) {
                if (e.type == UIEV_MOVE) moved = 1;
                else scene_dirty = 1;           /* char/key/click change the scene */
                if (e.type == UIEV_CLICK) {     /* mockup 10/10: the pressed frame, ~150 ms */
                    a.pressed_id = app_hit(&a, e.x, e.y);
                    a.pressed_until = now_s() + 0.15;
                }
                done = a.install.active ? install_handle(&a, &wizard, &e)
                                        : handle(&a, &e, auth_cmd, demo);
            }
            if (moved) {
                need_present = 1;               /* the cursor moved */
                int h = a.ptr_visible ? app_hit(&a, a.ptr_x, a.ptr_y) : -1;
                if (h != last_hover) { last_hover = h; scene_dirty = 1; }  /* a hover highlight changed */
            }
        }
        if (!a.install.active && sf_state.path[0] && state_poll(&sf_state, &a.st) > 0) { after_reload(&a); scene_dirty = 1; }
        if (!a.install.active && tools_poll(&a)) scene_dirty = 1;                    /* #649 */
        if (install_tick(&a, &wizard)) scene_dirty = 1;
        /* #410: "עודכן לפני N שנ'" must keep counting when the state file
         * stops changing -- that is the whole point of the stamp. One
         * redraw per wake (2 s) while a stamp is on screen. */
        if ((a.st.updated > 0 || counting(&a) || a.st.fail_at > 0) && (long long)time(NULL) != last_age_tick) { last_age_tick = (long long)time(NULL); scene_dirty = 1; }   /* #1569: the reboot count */
    }
    if (in) input_close(in);
    cairo_surface_destroy(scene);
    backend_close(b);
    return done ? 0 : 1;                            /* 1: stopped by a signal */
}
