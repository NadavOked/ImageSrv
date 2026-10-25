/* #1322: the local clone's functions -- lc_state.c (the records, the roles,
 * the keys and clicks) and the three drawing files. ui.h has the data. */
#ifndef IMAGECTL_LCUI_H
#define IMAGECTL_LCUI_H
#include <cairo.h>
#include "input.h"
#include "ui.h"

/* one key=value record of the state file; 1 = it was an lc_ one */
int lc_state_line(State *s, const char *key, char *val);
/* after every reload: the default roles, the source to measure */
void lc_after_reload(App *a);
/* every input event while a local clone screen is up; 1 = taken */
int lc_handle(App *a, const Event *e);
int standby_handle(App *a, const Event *e);   /* #2079 (screens_standby.c): Esc / the button on the wait -> choice-back */
/* the shared rules (drawing and keys use the same ones) */
int lc_view(const App *a);
int lc_role_of(const App *a, const char *dev);
int lc_source_index(const App *a);
const LcFit *lc_fit_of(const App *a, const char *dev);
int lc_target_ok(const App *a, int i);            /* disk i may be a target now */
int lc_targets(const App *a, int *idx, int max);  /* the chosen targets, by index */
int lc_can_go(const App *a);                      /* >= 1 target, every one allowed */
int lc_any_shrink(const App *a);
const LcDisk *lc_disk_by_dev(const App *a, const char *dev);
void lc_gb(char *out, size_t n, double bytes);    /* "149 GB", "610 MB" */
/* #2030: a drawer card's third line. USED_NOW: what is on the disk now (what the write will
 * erase); WRITTEN: what a finished write put there (0 = not counted -> "לא נמדד"); SO_FAR: a
 * write in flight, where 0 is a real count. bytes < 0 = not measured. Never 0 for "no measurement". */
enum { USED_NOW, USED_WRITTEN, USED_SO_FAR };
void card_used_text(int mode, long long bytes, char *out, size_t n);
void lc_disk_card_name(const LcDisk *d, char *out, size_t n);   /* #2030: "SATA 0" -- the connection, not "דיסק N" */
void lc_pct(char *out, size_t n, double share);   /* #1437: "0.2%", "37%" -- floored */
void lc_rate_sample(LcUi *u, int elapsed, long long copied);   /* #1437: one per reload */
double lc_rate(const LcUi *u, int *span);         /* bytes/s over the window, -1 = none */
int lc_unmeasured(const LcState *l);              /* #1440: a partition not measured, 0 = none */
void lc_disk_label(const LcDisk *d, char *out, size_t n);   /* visible drawer, or an unmapped label */
#define LC_LABEL_MAX (SLOT_NAME_MAX + 16)  /* #2001: a buffer for lc_disk_label -- a name of 24 Hebrew letters is 48 bytes */
void lc_has_text(const LcDisk *d, char *out, size_t n);     /* "Windows · 3 מחיצות · 212 GB בשימוש" */
void lc_copy_text(const LcState *l, const LcDisk *src, char *out, size_t n);   /* #1443: "203.1 GB יועתקו" */
void lc_sample(State *s, const char *card);       /* --png: the mockup's scenario */
int lc_png_setup(App *a, const char *card);       /* 1 = card is a local clone card */
extern const char *const LC_PNG_CARDS[];
extern const int LC_PNG_NCARDS;

/* 16/10: "שחזור אימג' מהשרת" -- the restore screen on the cloner (lc_pull.c) */
int lc_pull_active(const App *a);                 /* force_cloner and lc_screen=pull */
int lc_pull_selectable(const App *a, int port);   /* an attached drawer with that disk number */
int lc_pull_ports(const App *a, char *out, size_t n);   /* the ticked ones, "1,3"; how many */
void lc_pull_names(const App *a, char *out, size_t n);  /* "דיסק 1, דיסק 3" */
int lc_pull_ready(App *a);                        /* an image and a drawer; else form_error says which */
void lc_pull_after_reload(App *a);
double lc_pull_drawers(App *a, cairo_t *cr, Rect area, int draw);
int lc_pull_handle(App *a, const Event *e);
/* #1896 (screens_syspart.c): an old image's C: -- the picker over the restore
 * screen (build machine and cloner). restore_pick: a tile or digit chose image
 * i -- restored, or the picker for an unchecked one. syspart_modal: its keys and
 * clicks (1 = taken); it sends syspart|<id>|<idx> (lc-syspart| on the cloner). */
void draw_syspart_dialog(App *a, cairo_t *cr, double W, double H);
void restore_pick(App *a, int i);
int syspart_modal(App *a, const Event *e);       /* 1 = taken */

/* drawing */
void screen_lcboot(App *a, cairo_t *cr, double W, double H, double head_h);
void screen_local(App *a, cairo_t *cr, double W, double H, double head_h);
void lc_draw_source(App *a, cairo_t *cr, double W, double H, double head_h);
void lc_draw_confirm(App *a, cairo_t *cr, double W, double H);
void lc_draw_progress(App *a, cairo_t *cr, double W, double H, double head_h);
void lc_draw_stop(App *a, cairo_t *cr, double W, double H);
void lc_draw_done(App *a, cairo_t *cr, double W, double H, double head_h);
const char *lc_status_word(const App *a, Rgb *dot);
const char *lc_round_note(const App *a);          /* #1367: a round is open on the server; NULL = say nothing */
int lc_unread_text(const App *a, char *out, size_t n);   /* Nadav 22/10: the source's SMART 197/198; 0 = say nothing */
const char *lc_status_keys(const App *a);
/* lcbay.c (Nadav 17/10, "גרסה ב׳"): the local clone's target row, metric box,
 * hero and cage -- shared with the build machine's distribution screen and
 * the cloner's "שיכפול במקביל" */
enum { BAY_WAITING, BAY_WRITING, BAY_SMART, BAY_SKIPPED, BAY_FAILED, BAY_DONE,
       BAY_CANCELED,    /* #1386: grey like skipped, an empty bar -- the disk is empty now, not "16%" */
       BAY_CANCELING }; /* #1386: the seconds in between -- an orange LED, the bar emptied */
typedef struct {
    int kind, drawer;            /* BAY_*; the drawer number, 0 = "—" */
    char name[SLOT_NAME_MAX + 64], sub[200];
    const char *pill;            /* NULL = none */
    char pct[24];                /* "37%", "—", "12.4 GB" */
    double share;                /* the bar, 0..1; < 0 = empty */
    char line[460];              /* what is happening */
    /* the disk's health on a writing/waiting row (#652/#432): the words in a
     * pill and the colour of the LED -- smart_color() and room_drawer_color(),
     * the one mapping (green ok, orange F1/slow/CRC, red failed_last). An
     * orange or red tone colours the bar too: it asks, it does not stop. */
    const char *note; int toned; Rgb tone; char note_buf[64];
    int line_warn;               /* #943: a done disk that carries an error -- its line in orange */
    int done_warn;               /* #2009: done, but orange -- its pill, LED and bar (Fast Startup, dirty, not checked) */
    int flag;                    /* #2024: holds the end (end_problem_of): 1 orange, 2 red -- the row tinted, its edge thick, the line is the reason */
    /* #1386: the fifth column -- "בטל…" on a writing row, red words, its key in a chip
     * (NULL key = none); act_hit 0 = no button */
    const char *act, *act_key; int act_hit; char act_key_buf[8];
    /* #1331 (mockup v2, 17/10): what happened to the disk's drivers, a line
     * under the bar; DRV_BAD (the storage driver was not made boot-start --
     * it may not boot) reds the edge and the LED, DRV_WARN (not placed: it
     * boots without them, #856) the LED. "" = the step did not run. */
    char drv[460]; int drv_tone;
    char used[96];               /* #2030: "תפוס עכשיו: 120 GB" / "נכתב: 85 GB" / "...: לא נמדד"; "" = no line */
} LcBay;
/* DRV_LGREY / DRV_LWARN (#1331 T8): the Linux initramfs line -- kept apart from
 * the Windows tones so the screens'' "N placed / N without" summaries never count it */
enum { DRV_NONE, DRV_OK, DRV_MUTED, DRV_WARN, DRV_BAD, DRV_LGREY, DRV_LWARN };
#define LC_BAY_H 86
#define LC_BAY_DRV_H 104             /* the row with its drivers line (mockup v2: 86 + 18) */
void lc_bay_draw(App *a, cairo_t *cr, Rect b, const LcBay *g);
/* #1331: the words and the tone of a drawer's drivers outcome (drivers.state,
 * its detail, drivers.boot state / service / error) -- the same on the
 * cloner and the build screen; a done row also takes its pill. */
void lc_bay_drivers(LcBay *y, const char *state, const char *detail, const char *boot,
                    const char *bsvc, const char *berr);
void lc_bay_linux(LcBay *y, const char *state, const char *module, const char *detail);
void lc_met(App *a, cairo_t *cr, Rect r, const char *l, const char *v, const char *h, Rgb hc);
void lc_disks_met(App *a, cairo_t *cr, Rect r, const char *value, const char *hint);
double lc_hero(App *a, cairo_t *cr, double x0, double xr, double y, const char *label, const char *h1,
               const char *s2, const char *big, const char *left, int alarm);   /* alarm: the title in red */
double lc_cage(App *a, cairo_t *cr, Rect cage, const char *title, const char *count);
/* shared pieces (screens_local.c) */
double lc_btn(App *a, cairo_t *cr, double right, double y, double h, const char *label, const char *key,
              int kind, int id, int focus);
double lc_btn_width(cairo_t *cr, const char *label, const char *key);
void lc_dialog_head(App *a, cairo_t *cr, Rect box, const char *title, const char *lead, double *y);
Rect lc_dialog_box(App *a, cairo_t *cr, double W, double H, double w, double h);
#endif
