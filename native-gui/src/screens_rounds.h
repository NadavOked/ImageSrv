#ifndef IMAGESRV_ROUND_WIDGETS_H
#define IMAGESRV_ROUND_WIDGETS_H
#include "widgets.h"
enum { ROWS_ROOM_SETUP, ROWS_ROOM_LIVE, ROWS_CLASS };

typedef struct {
    Text name, mac, state;
    double h;
    int dim, on;
} Row;

typedef struct {
    Row r[MAX_MACHINES]; int n;
    Text empty;                 /* the <p class="sub"> when there are no rows */
    double h;                   /* the whole .room-machines box */
} RowSet;

typedef struct { Text big, small; double drop, h; } ProgLine;


/* #872 (Nadav, 16/09): three colours, one rule, shared by the room grid and
 * the idle cloner list. failed_last (the on-disk mark of #845 -- it failed
 * the previous clone, proven) is red; fail (SMART overall-health FAILED,
 * what the BIOS shows) is orange and asks; everything else is green --
 * including unchecked, which says so in words, not in colour. warn no
 * longer comes from the agent; an old agent's warn reads as orange.
 * smart_level is the same rule as a number: 0 green, 1 orange, 2 red. */
int smart_level(const char *smart);
Rgb smart_color(const Theme *t, const char *smart);
const char *smart_he(const char *smart);
/* the agent's SMART reason code (smart.sh) in words; "" for none or ok */
const char *smart_reason_text(const char *reason);
const char *smart_he_why(const char *smart, const char *reason);   /* #1742: read-only says so */
const char *red_refusal_he(const char *reason);   /* #1732/#1742: "לא כותבים" on red, by the reason */
const IdleDisk *idle_for_port(const State *s, int port);
/* #1495: drawer mapping. A zero struct field is the legacy port convention. */
int eff_drawer(int drawer, int port);
void conn_text(const char *conn, int port, const char *dev, char *out, size_t n);
void unmapped_label(const char *name, const char *conn, int port, const char *dev, char *out, size_t n);
/* #2030: the NAME on a drawer card. The big number on the card's end is the drawer; the name
 * says the other thing -- the connection ("SATA 0", "NVMe 03:00.0") -- instead of repeating the
 * number as "דיסק N". An operator's slot name (#704) still wins; a disk outside the mapping keeps
 * "(לא ממופה)". Never a device name: "sda" is a log word, not the operator's. */
void card_name(const char *name, int bay, const char *conn, int port, char *out, size_t n);
const CUsed *cused_for_dev(const State *s, const char *dev);   /* #2030: NULL = no size read for it */
int cloner_bay_count(const State *s);
const IdleDisk *idle_for_bay(const State *s, int bay);
const Drawer *drawer_for_bay(const State *s, int bay);
const COptical *optical_for_bay(const State *s, int bay);
void cloner_bay_conn(const State *s, int bay, char *out, size_t n);
/* #704: the cloner's label of SATA slot <port> -- its cslot_name, else
 * "דיסק N". <out> >= SLOT_NAME_MAX + 16 bytes, or a long name is cut. */
void cloner_slot_label(const State *s, int bay, char *out, size_t n);
void cloner_port_label(const State *s, int port, char *out, size_t n);
int room_selectable(const RoomDrawer *d);
int room_pickable(const RoomDrawer *d);   /* #1732: selectable and not red */
/* Nadav 17/10: is this cloner picked as a whole machine (a->room_pick)? */
int room_picked(const App *a, const char *mac);
void room_drawer_label(const Machine *m, const RoomDrawer *d, char *out, size_t n);
int room_rows(const Machine *m);
/* The status span of room.js machineRows / classes.js renderLive, as Pango
 * markup (room-ok / room-bad / room-warn colours); <out> >= 640 bytes. */
void room_status_markup(const App *a, const Machine *m, int mode, char *out, size_t n);
void rows_make(App *, cairo_t *, double, int, const char *, RowSet *);
void rows_draw(App *, cairo_t *, double, double, double, RowSet *);
ProgLine progline_make(cairo_t *, const char *, const char *, double);
/* #410: "עבר 1:02:05 · 52 MB/s · נותרו ~10 דק'" from elapsed/rate/eta
 * (-1 = unmeasured, said in words -- never 0); empty when nothing is
 * measured. latin=1 gives "52 MB/s · ETA 10:10" for a line that is drawn
 * DIR_LTR (the cloner's byte line) -- Hebrew words inside an LTR run come
 * out scrambled. pace_age: "עודכן לפני N שנ'" from the updated= epoch,
 * empty when there is no stamp. */
void pace_text(int elapsed_s, long long rate_bps, int eta_s, int latin, char *out, size_t n);
void pace_age(long long updated, char *out, size_t n);
/* <in> as an isolated bidi run (FSI/PDI) for an RTL line; <out> >= strlen+7. */
void bidi_isolate(const char *in, char *out, size_t n);
/* U+200F RIGHT-TO-LEFT MARK: put first in a line that may start with a Latin
 * value, so auto-dir still resolves the paragraph RTL. */
#define RLM "\xE2\x80\x8F"
/* #1304 pieces the local clone's drawers share (screens_standby.c, #1322):
 * a disk's market size ("500 GB"), the .led dot with its ring, the .pill with
 * its dot at the start, and the hatched face of an empty / blocked slot. */
void disk_size(unsigned long long b, char *out, size_t n);
void sb_led(cairo_t *cr, double cx, double cy, double d, Rgb c, Rgb soft, int ring, double alpha);
double sb_pill(cairo_t *cr, double right, double cy, const char *label, Rgb fg, Rgb bg);
double sb_bay_pill(cairo_t *cr, double right, double cy, const char *label, Rgb fg, Rgb bg, int emphasis);
void sb_hatch(cairo_t *cr, const Theme *t, Rect r);
void progline_draw(cairo_t *, const Theme *, ProgLine *, double, double, double);
void foot_buttons(App *, cairo_t *, Rect, Text *, const int *, const int *, int);
int image_options(const App *, const char **, char (*)[168], int);

/* #1269 (screens_drivers.c), the drawing half -- ui.h has the rest.
 * room_drv_options: the rows of the model list (the library's, nothing else).
 * room_drv_block_*: the switch row and, when on, the model + mode columns,
 * under the setup fields; draw returns the select's rect (w = 0 when none).
 * room_drv_tag_draw: a chosen disk's tag in pick mode. The summary line; a
 * Text with a NULL layout = nothing to draw. The live round's outcome is in
 * each drawer's row (#1331, lc_bay_drivers). */
int room_drv_options(const App *a, const char **opts, char (*buf)[200], int max);
double room_drv_block_height(App *a, cairo_t *cr, double w);
Rect room_drv_block_draw(App *a, cairo_t *cr, double x, double y, double w);
void room_drv_tag_draw(App *a, cairo_t *cr, Rect slot, const Machine *m, int mi, int drawer_index);
Text room_drv_summary_make(App *a, cairo_t *cr, double w);
double room_drv_summary_height(const Text *t);
void room_drv_summary_draw(App *a, cairo_t *cr, double x, double y, double w, Text *t);
#endif
