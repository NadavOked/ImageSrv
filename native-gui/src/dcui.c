/* The graphical server screen (#1203) -- the protocol and the loop; the
 * drawing is screens_dcui.c. Contract: dcui.h. */
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/ioctl.h>
#include <linux/vt.h>
#include "backend.h"
#include "dcui.h"

#define MAX_COLS 12
/* #1362: imagesrv-dcui.service runs on TTYPath=/dev/tty1 */
#define DCUI_TTY "/dev/tty1"
#define DCUI_VT 1

static void copy(char *dst, size_t n, const char *src) { snprintf(dst, n, "%s", src ? src : ""); }
#define COPY(dst, src) copy((dst), sizeof (dst), (src))

/* Split on TAB keeping empty fields (strtok would merge them, and a frame has
 * many: an empty pill, no admin chip...). Missing columns read as "". */
static int split(char *line, char **col) {
    int n = 0;
    col[n++] = line;
    for (char *p = line; *p && n < MAX_COLS; p++)
        if (*p == '\t') { *p = 0; col[n++] = p + 1; }
    for (int i = n; i < MAX_COLS; i++) col[i] = "";
    return n;
}

static void row_from(DcuiRow *r, const char *kind, char **c) {
    memset(r, 0, sizeof *r);
    COPY(r->kind, kind);
    if (!strcmp(kind, "fprow")) {               /* fprow label sublabel fp1 fp2 sub pill_kind pill tone */
        COPY(r->label, c[1]); COPY(r->sublabel, c[2]); COPY(r->value, c[3]); COPY(r->value2, c[4]);
        COPY(r->sub, c[5]); COPY(r->pill_kind, c[6]); COPY(r->pill, c[7]); COPY(r->tone, c[8]);
        COPY(r->value_style, "fp");
        return;
    }                                           /* row label label_style value value_style sub pill_kind pill tone */
    COPY(r->label, c[1]); COPY(r->label_style, c[2]); COPY(r->value, c[3]); COPY(r->value_style, c[4]);
    COPY(r->sub, c[5]); COPY(r->pill_kind, c[6]); COPY(r->pill, c[7]); COPY(r->tone, c[8]);
}

static DcuiEl *el_new(DcuiFrame *f, int type) {
    if (f->nel >= DCUI_MAX_EL) return NULL;
    DcuiEl *e = &f->el[f->nel++];
    memset(e, 0, sizeof *e);
    e->type = type;
    return e;
}

int dcui_feed(DcuiFrame *f, char *line) {
    size_t len = strlen(line);
    while (len && (line[len - 1] == '\n' || line[len - 1] == '\r')) line[--len] = 0;
    if (!len) return f->screen[0] ? 1 : 0;      /* the end of a frame (a stray blank is not) */
    char *c[MAX_COLS];
    split(line, c);
    const char *k = c[0];
    if (!strcmp(k, "frame")) { memset(f, 0, sizeof *f); COPY(f->screen, c[1]); return 0; }
    if (!f->screen[0]) return 0;                /* a record before "frame": ignore */
    if (!strcmp(k, "hdr")) {
        COPY(f->role, c[1]); COPY(f->host, c[2]); COPY(f->url, c[3]);
        COPY(f->version, c[4]); COPY(f->clock, c[5]); COPY(f->admin, c[6]);
    } else if (!strcmp(k, "hero")) {
        COPY(f->hero_kind, c[1]); COPY(f->hero_title, c[2]); COPY(f->hero_sub_m, c[3]);
        COPY(f->meta_label, c[4]); COPY(f->meta_value, c[5]);
    } else if (!strcmp(k, "prob") && f->nprobs < DCUI_MAX_PROBS) {
        DcuiProb *p = &f->probs[f->nprobs++];
        COPY(p->sev, c[1]); COPY(p->n, c[2]); COPY(p->title, c[3]); COPY(p->what, c[4]);
        COPY(p->todo_m, c[5]); COPY(p->cap, c[6]); COPY(p->cap_label, c[7]);
    } else if (!strcmp(k, "tile") && f->ntiles < DCUI_MAX_TILES) {
        DcuiTile *t = &f->tiles[f->ntiles++];
        memset(t, 0, sizeof *t);
        COPY(t->title, c[1]); COPY(t->pill_kind, c[2]); COPY(t->pill, c[3]);
    } else if ((!strcmp(k, "row") || !strcmp(k, "fprow")) && f->ntiles) {
        DcuiTile *t = &f->tiles[f->ntiles - 1];
        if (t->nrows < DCUI_MAX_ROWS) row_from(&t->rows[t->nrows++], k, c);
    } else if (!strcmp(k, "prow") && f->nprows < DCUI_MAX_PROWS) {
        row_from(&f->prows[f->nprows++], "row", c);
    } else if (!strcmp(k, "ttl")) {
        COPY(f->ttl, c[1]); COPY(f->ttl_sub, c[2]);
    } else if (!strcmp(k, "item") && f->nitems < DCUI_MAX_ITEMS) {
        DcuiItem *it = &f->items[f->nitems++];
        COPY(it->icon, c[1]); COPY(it->title, c[2]); COPY(it->sub, c[3]); it->selected = atoi(c[4]);
    } else if (!strcmp(k, "na")) {
        COPY(f->na_m, c[1]);
    } else if (!strcmp(k, "pane")) {
        COPY(f->pane_title, c[1]); COPY(f->pane_desc_m, c[2]);
    } else if (!strcmp(k, "pnote") && f->npnotes < DCUI_MAX_NOTES) {
        COPY(f->pnotes[f->npnotes++], c[1]);
    } else if (!strcmp(k, "sb")) {
        COPY(f->dot, c[1]); COPY(f->word, c[2]); COPY(f->rest, c[3]);
    } else if (!strcmp(k, "lang")) {
        COPY(f->lang, c[1]);
    } else if (!strcmp(k, "key") && f->nkeys < DCUI_MAX_KEYS) {
        DcuiKey *kk = &f->keys[f->nkeys++];
        COPY(kk->caps, c[1]); COPY(kk->label, c[2]); kk->lock = atoi(c[3]);
    } else if (!strcmp(k, "notice")) {
        COPY(f->notice_kind, c[1]); COPY(f->notice, c[2]);
    } else if (!strcmp(k, "dlg")) {
        f->dialog = 1; COPY(f->dlg_kind, c[1]); f->dlg_width = atoi(c[2]);
        COPY(f->dlg_title, c[3]); COPY(f->dlg_lead, c[4]);
    } else if (!strncmp(k, "d.", 2)) {
        static const struct { const char *name; int type; } T[] = {
            {"d.p", DEL_P}, {"d.field", DEL_FIELD}, {"d.hint", DEL_HINT}, {"d.note", DEL_NOTE},
            {"d.opt", DEL_OPT}, {"d.radio", DEL_RADIO}, {"d.alert", DEL_ALERT},
            {"d.bullet", DEL_BULLET}, {"d.kv", DEL_KV}, {"d.error", DEL_ERROR}, {"d.btn", DEL_BTN},
            {"d.code", DEL_CODE}, {"d.conn", DEL_CONN}, {"d.addlink", DEL_ADDLINK}, {"d.add", DEL_ADD},
            {"d.foot", DEL_FOOT},
        };
        for (size_t i = 0; i < sizeof T / sizeof T[0]; i++) {
            if (strcmp(k, T[i].name)) continue;
            DcuiEl *e = el_new(f, T[i].type);
            if (!e) break;
            switch (e->type) {
            case DEL_FIELD:  /* label value mode focused en */
                COPY(e->b, c[1]); COPY(e->a, c[2]); COPY(e->c, c[3]); e->f1 = atoi(c[4]); e->f2 = atoi(c[5]); break;
            case DEL_HINT:   /* cap text_m right */
                COPY(e->c, c[1]); COPY(e->a, c[2]); COPY(e->b, c[3]); break;
            case DEL_OPT:    /* title sub selected focused */
            case DEL_RADIO:
                COPY(e->a, c[1]); COPY(e->b, c[2]); e->f1 = atoi(c[3]); e->f2 = atoi(c[4]); break;
            case DEL_KV:     /* label value mono */
                COPY(e->b, c[1]); COPY(e->a, c[2]); e->f1 = atoi(c[3]); break;
            case DEL_BTN:    /* label cap kind focused */
                COPY(e->a, c[1]); COPY(e->c, c[2]); COPY(e->d, c[3]); e->f1 = atoi(c[4]); break;
            case DEL_CONN:   /* #2001: label pci model size tag name placeholder focus(0|1 name|2 switch) active kind */
                COPY(e->c, c[1]); COPY(e->d, c[2]); COPY(e->b, c[3]); COPY(e->e, c[4]); COPY(e->g, c[5]);
                COPY(e->a, c[6]); COPY(e->h, c[7]); e->f1 = atoi(c[8]); e->f2 = atoi(c[9]); COPY(e->k, c[10]); break;
            case DEL_ADDLINK: /* #2001: label hint */
                COPY(e->a, c[1]); COPY(e->b, c[2]); break;
            case DEL_ADD:    /* #2001: type(SATA|NVMe) number name focus(0 type|1 number|2 name) help */
                COPY(e->c, c[1]); COPY(e->d, c[2]); COPY(e->a, c[3]); e->f1 = atoi(c[4]); COPY(e->b, c[5]); break;
            case DEL_FOOT:   /* #2001: status dirty */
                COPY(e->a, c[1]); COPY(e->b, c[2]); break;
            case DEL_CODE:   /* code timer_label timer timer_kind (16/10: F4 on a secondary) */
                COPY(e->a, c[1]); COPY(e->b, c[2]); COPY(e->c, c[3]); COPY(e->d, c[4]); break;
            case DEL_ALERT:  /* text_m kind ("" = danger, "warn" = yellow) */
                COPY(e->a, c[1]); COPY(e->c, c[2]); break;
            default:         /* p note alert bullet error: one text */
                COPY(e->a, c[1]); break;
            }
            break;
        }
    }
    return 0;
}

int dcui_load(DcuiFrame *f, const char *path) {
    FILE *in = fopen(path, "r");
    if (!in) { fprintf(stderr, "native-gui: %s: %s\n", path, strerror(errno)); return -1; }
    char line[4096];
    int done = 0;
    memset(f, 0, sizeof *f);
    while (!done && fgets(line, sizeof line, in)) done = dcui_feed(f, line);
    if (!done && f->screen[0]) done = 1;        /* a file may end without the blank line */
    fclose(in);
    if (!done) { fprintf(stderr, "native-gui: %s: no frame\n", path); return -1; }
    f->valid = 1;
    return 0;
}

int dcui_token(const Event *ev, int caps, char *out, size_t n) {
    if (ev->type == UIEV_CHAR) {
        int ch = ev->ch;                        /* US, never ev->he: English only here */
        if (caps && ch >= 'a' && ch <= 'z') ch -= 32;
        else if (caps && ch >= 'A' && ch <= 'Z') ch += 32;   /* Caps + Shift = lower, like a keyboard */
        if (ch < 0x20 || ch > 0x7e) return 0;
        snprintf(out, n, "CHAR %x", ch);
        return 1;
    }
    if (ev->type != UIEV_KEY) return 0;
    static const struct { int key; const char *name; } K[] = {
        {KEYSYM_F2, "F2"}, {KEYSYM_F4, "F4"}, {KEYSYM_F5, "F5"}, {KEYSYM_F9, "F9"},
        {KEYSYM_F12, "F12"},
        {KEYSYM_ENTER, "ENTER"}, {KEYSYM_ESC, "ESC"}, {KEYSYM_TAB, "TAB"}, {KEYSYM_BACKTAB, "BACKTAB"},
        {KEYSYM_BACKSPACE, "BACKSPACE"}, {KEYSYM_UP, "UP"}, {KEYSYM_DOWN, "DOWN"},
        {KEYSYM_LEFT, "LEFT"}, {KEYSYM_RIGHT, "RIGHT"},
    };
    if (ev->key == KEYSYM_CAPS) { snprintf(out, n, "CAPS %d", ev->caps ? 1 : 0); return 1; }
    for (size_t i = 0; i < sizeof K / sizeof K[0]; i++)
        if (K[i].key == ev->key) { snprintf(out, n, "KEY %s", K[i].name); return 1; }
    return 0;                                   /* Alt+Shift (no Hebrew here), other F keys */
}

static volatile sig_atomic_t stop_now = 0;
static void on_signal(int sig) { (void)sig; stop_now = 1; }
/* #1362: the kernel's VT_PROCESS signals -- handled in the loop, not here */
static volatile sig_atomic_t vt_leave = 0, vt_back = 0;
static void on_vt_leave(int sig) { (void)sig; vt_leave = 1; }
static void on_vt_back(int sig) { (void)sig; vt_back = 1; }

static void present(Backend *b, const DcuiFrame *f, const Theme *t) {
    cairo_t *cr = cairo_create(backend_surface(b));
    dcui_draw(f, t, cr, backend_width(b), backend_height(b));
    cairo_destroy(cr);
    backend_present(b);
}

/* #1362: the screen's own VT must be in front before the modeset, or the
 * GUI would paint over the VT the operator is using (an upgrade restarts
 * this unit while someone may be on tty2). Display managers do the same. */
static int vt_front(const char *tty, int vt, char *err, size_t n) {
    int fd = open(tty, O_RDWR | O_CLOEXEC);
    if (fd < 0) { snprintf(err, n, "%s: %s", tty, strerror(errno)); return -1; }
    struct vt_stat st = { 0 };
    int rc = -1;
    for (int i = 0; i < 40; i++) {              /* 2 s */
        if (ioctl(fd, VT_GETSTATE, &st) < 0) { snprintf(err, n, "VT_GETSTATE: %s", strerror(errno)); break; }
        if (st.v_active == vt) { rc = 0; break; }
        if (i == 0) ioctl(fd, VT_ACTIVATE, vt);
        usleep(50000);
    }
    if (rc && !*err) snprintf(err, n, "tty%d did not come to the front (VT %d is)", vt, st.v_active);
    close(fd);
    return rc;
}

/* Answer the backend once, on stdout, in place of READY (#1362): the
 * Python side shows <why> on the text screen it falls back to. */
static int fail(const char *why) {
    fprintf(stderr, "native-gui: dcui: %s\n", why);
    printf("FAIL %s\n", why);
    fflush(stdout);
    return 1;
}

int dcui_main(const Theme *theme) {
    char err[1024] = "", why[1200];
    if (vt_front(DCUI_TTY, DCUI_VT, err, sizeof err)) {
        snprintf(why, sizeof why, "screen not in front: %s", err);
        return fail(why);
    }
    backend_set_tty(DCUI_TTY);
    Backend *b = backend_open("drm", NULL, err, sizeof err);
    if (!b) {
        fprintf(stderr, "native-gui: dcui: drm: %s\n", err);
        b = backend_open("fbdev", NULL, err, sizeof err);
    }
    if (!b) { snprintf(why, sizeof why, "no screen: %s", err); return fail(why); }
    int W = backend_width(b), H = backend_height(b);
    DcuiFrame *cur = calloc(1, sizeof *cur), *pending = calloc(1, sizeof *pending);
    if (!cur || !pending) { backend_close(b); return 1; }
    present(b, cur, theme);                     /* the empty page until the first frame */
    if (backend_verify(b, err, sizeof err)) {   /* #1362: shown, or say why -- never black */
        backend_close(b); free(cur); free(pending);
        return fail(err);
    }
    Input *in = input_open(W, H);
    signal(SIGINT, on_signal); signal(SIGTERM, on_signal); signal(SIGPIPE, SIG_IGN);
    signal(SIGUSR1, on_vt_leave); signal(SIGUSR2, on_vt_back);
    backend_vt_take(b, SIGUSR1, SIGUSR2);       /* fails loudly; the screen still works */
    setvbuf(stdout, NULL, _IOLBF, 0);
    int caps_sent = input_caps(in);
    printf("READY %dx%d\nCAPS %d\n", W, H, caps_sent);
    fflush(stdout);

    char buf[65536]; size_t used = 0;
    int rc = 0, away = 0;
    struct pollfd fds[32];
    while (!stop_now) {
        if (vt_leave) {                         /* another VT asked for the screen */
            vt_leave = 0;
            input_suspend(in, 1);
            backend_vt_release(b);
            away = 1;
        }
        if (vt_back) {
            vt_back = 0;
            backend_vt_acquire(b);
            input_suspend(in, 0);
            away = 0;
            present(b, cur, theme);             /* the frames that came while away */
            if (caps_sent != input_caps(in)) { caps_sent = input_caps(in); printf("CAPS %d\n", caps_sent); }
        }
        if (in) input_rescan(in);
        fds[0].fd = 0; fds[0].events = POLLIN; fds[0].revents = 0;
        int n = 1 + (in ? input_fill_pollfds(in, fds + 1, 31) : 0);
        int r = poll(fds, n, 1000);
        if (r < 0) { if (errno == EINTR) continue; rc = 1; break; }
        if (fds[0].revents & (POLLIN | POLLHUP)) {
            ssize_t got = read(0, buf + used, sizeof buf - 1 - used);
            if (got <= 0) break;                /* the backend closed the pipe: done */
            used += (size_t)got; buf[used] = 0;
            char *start = buf, *nl;
            int fresh = 0;
            while ((nl = strchr(start, '\n'))) {
                *nl = 0;
                if (dcui_feed(pending, start)) { *cur = *pending; cur->valid = 1; memset(pending, 0, sizeof *pending); fresh = 1; }
                start = nl + 1;
            }
            used = strlen(start);
            memmove(buf, start, used + 1);
            if (used >= sizeof buf - 1) used = 0;   /* a line longer than the buffer: drop it */
            if (fresh && !away) present(b, cur, theme);   /* not master while away */
        }
        if (r > 0 && in) {
            input_pump(in, fds + 1, n - 1);
            Event e;
            while (input_next(in, &e)) {
                if (e.key == KEYSYM_VT && e.type == UIEV_KEY) {
                    if (e.vt != DCUI_VT && backend_vt_activate(b, e.vt) < 0)
                        fprintf(stderr, "native-gui: VT_ACTIVATE %d: %s\n", e.vt, strerror(errno));
                    continue;
                }
                if (caps_sent != e.caps) { printf("CAPS %d\n", e.caps ? 1 : 0); caps_sent = e.caps; }
                char tok[32];
                if (e.key != KEYSYM_CAPS && dcui_token(&e, e.caps, tok, sizeof tok)) printf("%s\n", tok);
            }
            fflush(stdout);
        }
    }
    if (in) input_close(in);
    backend_close(b);
    free(cur); free(pending);
    return rc;
}

int dcui_render_png(const char *prefix, const char *frame_path, int w, int h) {
    DcuiFrame *f = calloc(1, sizeof *f);
    if (!f) return 1;
    if (dcui_load(f, frame_path)) { free(f); return 1; }
    const Theme *themes[2] = { &THEME_LIGHT, &THEME_DARK };
    int rc = 0;
    for (int i = 0; i < 2 && !rc; i++) {
        cairo_surface_t *sf = cairo_image_surface_create(CAIRO_FORMAT_RGB24, w, h);
        cairo_t *cr = cairo_create(sf);
        dcui_draw(f, themes[i], cr, w, h);
        cairo_destroy(cr);
        char path[512];
        snprintf(path, sizeof path, "%s-%s.png", prefix, themes[i]->id);
        cairo_status_t st = cairo_surface_write_to_png(sf, path);
        cairo_surface_destroy(sf);
        if (st != CAIRO_STATUS_SUCCESS) { fprintf(stderr, "native-gui: %s: %s\n", path, cairo_status_to_string(st)); rc = 1; }
        else printf("wrote %s\n", path);
    }
    free(f);
    return rc;
}
