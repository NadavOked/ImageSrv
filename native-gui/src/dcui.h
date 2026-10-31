/* The graphical server screen (#1203): imagesrv-station-gui --screen dcui.
 *
 * This mode only paints and reads the keyboard. The state machine, the
 * password check, the lockouts and every action stay in the root Python
 * process (server/dcui/gui.py) that started it:
 *
 *   stdin   frames of records, one record per line "type<TAB>field...", a
 *           frame ends with an empty line (docs/interfaces.md s31,
 *           server/dcui/view.py). Fields named *_m there are Pango markup.
 *   stdout  "READY WxH" once the screen is open, then one token per key:
 *           "KEY F2|F4|F5|F9|F12|ENTER|ESC|TAB|BACKTAB|BACKSPACE|UP|DOWN|LEFT|RIGHT",
 *           "CHAR <hex>" (always the US layout: every field here is English
 *           only -- the keyboard is forced to English), "CAPS 0|1".
 *           "FAIL <why>" (#1362) instead of READY when the screen could not
 *           be opened or its first frame was not confirmed on the display.
 *   exit 0  stdin closed (the backend is gone); 1 = could not open a screen.
 *
 * The screen lives on tty1 (imagesrv-dcui.service's TTYPath). Alt+Fn and
 * Ctrl+Alt+Fn switch VT (#1362): the keyboards are let go and DRM master is
 * dropped while another VT is in front, and both are taken back on return.
 *
 * Only drm or fbdev: never the "mem" backend -- a server screen drawn into a
 * file is a blank tty1, and the backend must learn that and fall back to text. */
#ifndef IMAGESRV_DCUI_H
#define IMAGESRV_DCUI_H

#include <stddef.h>
#include <cairo.h>
#include "input.h"
#include "theme.h"

#define DCUI_MAX_TILES 4
#define DCUI_MAX_ROWS 7
#define DCUI_MAX_PROBS 3
#define DCUI_MAX_ITEMS 8                /* #2198: USB automatic power-off, before reset */
#define DCUI_MAX_PROWS 8                /* #1962: a row per drawer, up to 8 */
#define DCUI_MAX_NOTES 4
#define DCUI_MAX_KEYS 8
#define DCUI_MAX_EL 28
#define DCUI_MAX_FIELDS 10
#define DCUI_FIELD 768

typedef struct {
    char kind[16];                      /* "row" or "fprow" */
    char label[96], label_style[16], value[256], value_style[16];
    char value2[160];                   /* fprow: the second fingerprint line */
    char sublabel[64];                  /* fprow: "(SHA-256)" */
    char sub[256], pill_kind[8], pill[64], tone[8];
} DcuiRow;

typedef struct {
    char title[96], pill_kind[8], pill[64];
    int nrows; DcuiRow rows[DCUI_MAX_ROWS];
} DcuiTile;

typedef struct { char sev[8], n[4], title[256], what[512], todo_m[768], cap[16], cap_label[64]; } DcuiProb;
typedef struct { char icon[8], title[128], sub[160]; int selected; } DcuiItem;
typedef struct { char caps[24], label[96]; int lock; } DcuiKey;

/* One element of the dialog, in order; a run of opts or btns is one row. */
enum { DEL_P = 1, DEL_FIELD, DEL_HINT, DEL_NOTE, DEL_OPT, DEL_RADIO, DEL_ALERT,
       DEL_BULLET, DEL_KV, DEL_ERROR, DEL_BTN, DEL_CODE,
       /* #2001: the USB stick's connections window (dlg kind "map", screens_dcui.c map_win) --
        * a row per connection, the "הוסף חיבור" link, its form, the footer's status */
       DEL_CONN, DEL_ADDLINK, DEL_ADD, DEL_FOOT };
typedef struct {
    int type;
    char a[DCUI_FIELD];                 /* main text (markup for p/note/alert/bullet/hint) */
    char b[256], c[64], d[64];
    int f1, f2;                         /* selected/focused, en chip, mono ... per type */
    char e[64], g[160], h[64], k[16];   /* #2001 d.conn: size, tag, placeholder, kind */
} DcuiEl;

typedef struct {
    int valid;                          /* a complete frame has arrived */
    char screen[16];                    /* "main" | "settings" */
    char role[64], host[96], url[160], version[64], clock[16], admin[128];
    char hero_kind[8], hero_title[256], hero_sub_m[DCUI_FIELD], meta_label[64], meta_value[64];
    int nprobs; DcuiProb probs[DCUI_MAX_PROBS];
    int ntiles; DcuiTile tiles[DCUI_MAX_TILES];
    char ttl[128], ttl_sub[256];
    int nitems; DcuiItem items[DCUI_MAX_ITEMS];
    char na_m[512];
    char pane_title[128], pane_desc_m[DCUI_FIELD];
    int nprows; DcuiRow prows[DCUI_MAX_PROWS];
    int npnotes; char pnotes[DCUI_MAX_NOTES][512];
    char dot[8], word[128], rest[256], lang[96];
    char lang_chip[8];                  /* #2001: not from the frame -- the station GUI's typing language
                                         * ("עב"/"EN") for the USB stick's name field; "" = "EN" */
    int nkeys; DcuiKey keys[DCUI_MAX_KEYS];
    char notice_kind[8], notice[512];
    int dialog; char dlg_kind[8]; int dlg_width; char dlg_title[256], dlg_lead[512];
    int nel; DcuiEl el[DCUI_MAX_EL];
} DcuiFrame;

/* Feed one line (without its newline). Returns 1 when it completed a frame
 * (the empty line), 0 otherwise. <pending> collects; on 1 the caller copies it. */
int dcui_feed(DcuiFrame *pending, char *line);
/* Read a whole frame file (--png). 0 on success. */
int dcui_load(DcuiFrame *f, const char *path);

void dcui_draw(const DcuiFrame *f, const Theme *t, cairo_t *cr, int W, int H);

/* A key event -> the token the backend reads, or 0 for nothing to send.
 * Characters are the US layout (ev->ch), never ev->he: the keyboard is
 * forced to English on this screen; Caps Lock flips a letter's case. */
int dcui_token(const Event *ev, int caps, char *out, size_t n);

/* #1959: what a click can press, from the last dcui_draw (screen pixels).
 * keys = the tokens above, one per line -- the keys a person would have
 * pressed for the same thing; what = "item:<title>", "btn:<label>",
 * "opt:<title>" or "key:<caps>". */
#define DCUI_MAX_HITS 48                /* #2001: two per connection row, up to 12 */
typedef struct { double x, y, w, h; char what[160]; char keys[192]; } DcuiHit;
int dcui_hits(const DcuiHit **out);
/* 1 and <keys> when a click at x,y (screen pixels) presses something. "KEY LAYOUT"
 * (#2001, the language chip) is no backend token: the caller switches the language. */
int dcui_click(double x, double y, char *keys, size_t n);

int dcui_main(const Theme *theme);
int dcui_render_png(const char *prefix, const char *frame_path, int w, int h);

#endif
