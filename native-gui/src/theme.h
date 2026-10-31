/* Native mockup tokens: definitions and citations in theme.c. */
#ifndef IMAGESRV_THEME_H
#define IMAGESRV_THEME_H
typedef struct { double r, g, b; } Rgb;
typedef struct {
 const char *id;
 Rgb porcelain;
 Rgb surface;
 Rgb ink;
 Rgb muted;
 Rgb hair;
 Rgb indigo;
 Rgb indigo_soft;
 Rgb led_write;
 Rgb led_ok;
 Rgb led_idle;
 Rgb danger;
 Rgb hover;
 Rgb field;
 Rgb field_line;
 Rgb track;
 Rgb sunken;
 Rgb btn_hover;
 Rgb btn_hover_line;
 Rgb ink_hover;
 Rgb on_ink;
 Rgb danger_line;
 Rgb mark_line;
 Rgb login_a;
 Rgb login_b;
 Rgb login_glow;
 Rgb choice;
 Rgb choice_line;
 Rgb selected_line;
 Rgb button;
 Rgb button_line;
 Rgb danger_bg;
 Rgb warning_bg;
 Rgb warning_line;
 Rgb warning_ink;
 Rgb success_bg;
 Rgb success_line;
 Rgb success_ink;
 Rgb metric;
 Rgb metric_line;
 Rgb status_bg;
 Rgb header;
 Rgb header_text;
 Rgb info;
 Rgb info_soft;
 Rgb success_soft;
 Rgb danger_soft;
 Rgb warning_soft;
 Rgb bar_done;
 Rgb bar_idle;
 Rgb art_bg;
 Rgb art_a;
 Rgb art_b;
 Rgb art_c;
 Rgb art_line;
 Rgb art_server;
 Rgb disk_bg;
 Rgb disk_line;
 Rgb disk_selected;
 Rgb disk_selected_line;
 Rgb clone_line;
 Rgb round_line;
 Rgb image_bg;
 Rgb image_line;
 Rgb image_selected;
 Rgb image_selected_line;
 Rgb alert_bg;
 Rgb alert_line;
 Rgb alert_ink;
 Rgb success_btn;
 Rgb success_btn_line;
 Rgb node_active_line;
 Rgb node_warn_line;
 Rgb warn;
 Rgb brand_accent;
 Rgb action_fg;      /* text and strokes in the action colour (console --clr-action-fg) */
 Rgb danger_fill;    /* a destructive button's fill (console --clr-danger-fill): white text on it */
 Rgb danger_fg;      /* error text on the page (console --clr-danger-fg: 4.5:1 where --clr-danger is not) */
 Rgb shadow_strong; double shadow_strong_a;   /* black; the alpha of --shadow-overlay (menus, toast) */
 double shadow_card_a;                        /* the alpha of --shadow-card's soft layer (cards) */
 double radius;      /* --r */
 double radius_sm;   /* --r-sm */
} Theme;
extern const Theme THEME_LIGHT, THEME_DARK;
#include <stddef.h>
/* #1499 (Nadav 16/10): one saved theme per user (users.theme), shared with the
 * console. theme_saved: "light"/"dark" are that theme; "auto", empty or anything
 * else is <fallback> -- the GUI's default (light). theme_login_answer splits the
 * --auth-cmd output: line 1 the role, line 2 the saved theme (absent = ""). */
const Theme *theme_saved(const char *saved, const Theme *fallback);
void theme_login_answer(const char *out, char *role, size_t rn, char *saved, size_t sn);
/* The console's --r and --r-sm differ per theme (light 9/6px, dark 3/3px), so the
 * radius is read from the theme being drawn -- every drawing function already has
 * it as <t>. Not a constant any more: a constant 3 was the dark value on both. */
#define RADIUS_R (t->radius)
#define RADIUS_SM (t->radius_sm)
extern const double N_PANEL_W;
extern const double N_NARROW_W;
extern const double N_PAD;
extern const double N_CENTER_PAD;
extern const double N_HEADER_H;
extern const double N_HEADER_PAD;
extern const double N_STATUS_H;
extern const double N_STATUS_PAD;
extern const double N_TITLE;
extern const double N_TITLE_GAP;
extern const double N_SUB;
extern const double N_LABEL;
extern const double N_LABEL_GAP;
extern const double N_FORM_GAP;
extern const double N_FORM_TOP;
extern const double N_FIELD_H;
extern const double N_FIELD_PAD;
extern const double N_BUTTON_H;
extern const double N_BUTTON_PAD;
extern const double N_ACTION_GAP;
extern const double N_ACTION_TOP;
extern const double N_CHOICE_GAP;
extern const double N_CHOICE_H;
extern const double N_CHOICE_PAD;
extern const double N_CHOICE_TITLE;
extern const double N_CHOICE_SUB;
extern const double N_CHOICE_ICON;
extern const double N_LOGO;
extern const double N_LOGO_GAP;
extern const double N_MESSAGE_ICON;
extern const double N_MESSAGE_GAP;
extern const double N_DONE_ICON;
extern const double N_DONE_GAP;
extern const double N_PROGRESS_PCT;
extern const double N_BAR_H;
extern const double N_METRIC_PAD;
extern const double N_METRIC_LABEL;
extern const double N_METRIC_VALUE;
extern const double N_METRIC_GAP;
extern const double N_SHADOW_Y;
extern const double N_SHADOW_BLUR;
extern const double N_BORDER;
extern const double N_HEADER_FONT;
extern const double N_BRAND_FONT;
extern const double N_IMAGE_PAD;
extern const double N_IMAGE_GAP;
extern const double N_IMAGE_TITLE;
extern const double N_IMAGE_SUB;
extern const double N_CLONE_GAP;
extern const double N_CLONE_PAD;
extern const double N_CLONE_PCT;
extern const double N_CLONE_TITLE;
extern const double N_ROOM_GAP;
extern const double N_ROOM_PAD;
extern const double N_ROOM_MIN_H;
extern const double N_CLASS_PRIMARY;
extern const double N_CLASS_SECONDARY;
extern const double N_ROUND_PAD;
extern const double N_DIM_ALPHA;
extern const double N_BAR_SM;
extern const double N_ROUND_ID;
extern const double N_ROUND_META;
extern const double N_ALERT_PAD_Y;
extern const double N_ALERT_PAD_X;
extern const double N_ALERT_FONT;
extern const double N_STATUS_DOT;
extern const double N_STATUS_GAP;
extern const double N_BRAND_GAP;
extern const double N_NODE_TITLE;
extern const double N_NODE_SUB;
extern const double N_NODE_DOT;
extern const double N_DISK_ICON_COL;
extern const double N_DISK_PAD;
extern const double N_DISK_GAP;
extern const double N_DISK_COL_GAP;
extern const double N_DISK_TITLE;
extern const double N_DISK_SUB;
extern const double N_DISK_SIDE;
extern const double N_CLONE_SUB;
extern const double N_CLONE_STATUS;
extern const double N_CLONE_STAT_PAD;
extern const double N_CLONE_STAT_LABEL;
extern const double N_CLONE_STAT_VALUE;
extern const double N_CLONE_SOURCE_TITLE;
extern const double N_PROGRESS_TITLE;
extern const double N_ROOM_TITLE;
extern const double N_CLONER_TITLE;
extern const double N_RESTORE_NAME;
#endif
