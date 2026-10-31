#ifndef IMAGESRV_INSTALL_H
#define IMAGESRV_INSTALL_H

#include "ui.h"
#include "input.h"

typedef struct {
    const char *cmd;
    const char *cwd;
    double next_poll;
} InstallBridge;

/* Installer-only hit ids live outside ui.h so the station UI contract is unchanged. */
#define HIT_INSTALL_ADMIN_USER     1100
#define HIT_INSTALL_SHOW_PASSWORD  1101
#define HIT_INSTALL_SHOW_CONFIRM   1102
#define HIT_INSTALL_DISK_ACK       1103
#define HIT_INSTALL_BOOT_LOCAL     1104
#define HIT_INSTALL_TECHNICAL      1105
#define HIT_INSTALL_CANCEL_REBOOT  1106
#define HIT_INSTALL_TIMEZONE       1107   /* #1447 */
#define HIT_INSTALL_ROOT_PASSWORD  1108   /* #1632 */
#define HIT_INSTALL_ROOT_CONFIRM   1109
#define HIT_INSTALL_SHOW_ROOT      1110
#define HIT_INSTALL_SHOW_ROOT_CONFIRM 1111
#define HIT_INSTALL_DISK_BASE      1120
#define HIT_INSTALL_RAID_BASE      1140   /* #1989: + RAID_* */

void install_demo(App *a, int view);
int install_start(App *a, InstallBridge *bridge, int demo);
int install_handle(App *a, InstallBridge *bridge, const Event *event);
int install_tick(App *a, InstallBridge *bridge);
int install_polling(const App *a);
int install_password_ready(const InstallState *s);
/* The password rules, one definition for the screen and the gate: a special
 * character is ANY character that is not an ASCII letter or digit -- the
 * bridge's rule (gui-bridge.sh pw_errors), so `-` and `_` count (rc1, 24/10). */
void install_password_rules(const char *pw, int *letter, int *digit, int *special);
/* #1310: "next" on the role screen -- a secondary only after a GREEN check */
int install_role_ready(const InstallState *s);
const char *install_phase_label(const char *phase);
void screen_install(App *a, cairo_t *cr, double W, double H);
int install_render_png(App *a, const char *prefix, int w, int h);

#endif
