#ifndef IMAGESRV_INSTALL_RAID_H
#define IMAGESRV_INSTALL_RAID_H

/* #1989 (Nadav, 22/10): RAID on the disk step. The installer counts the
 * suitable disks -- not the install medium, not removable, not on USB (the
 * same rule as installer/lib/raid.sh raid_eligible_count) -- and the bridge
 * sends the default for that count (2 -> RAID1, 3 -> RAID5, 4+ -> RAID10);
 * the person may pick another, or no RAID. A virtual server is offered no
 * RAID: the host does it. Static inline, so install.c stays one translation
 * unit for the drivers that #include it (test_native_installer_*.py). */

#include <stdio.h>
#include <string.h>
#include "install_state.h"

enum { RAID_NONE, RAID_1, RAID_1_SPARE, RAID_5, RAID_6, RAID_10, RAID_CHOICES };
enum { INSTALL_VIRT_METAL, INSTALL_VIRT_VIRTUAL, INSTALL_VIRT_UNKNOWN };

typedef struct { const char *level, *label; int spares, min; } RaidOption;
__attribute__((unused)) static const RaidOption RAID_OPTS[RAID_CHOICES] = {
    { "none",   "בלי RAID",       0, 1 },
    { "raid1",  "RAID1",          0, 2 },
    { "raid1",  "RAID1 + רזרבי",  1, 3 },
    { "raid5",  "RAID5",          0, 3 },
    { "raid6",  "RAID6",          0, 4 },
    { "raid10", "RAID10",         0, 4 },
};

static inline int install_disk_suitable(const InstallDisk *d) {
    return !d->iso && !d->removable && strcmp(d->bus, "usb") != 0;
}
static inline int install_raid_suitable(const InstallState *s) {
    int n = 0;
    for (int i = 0; i < s->ndisks; i++) n += install_disk_suitable(&s->disks[i]);
    return n;
}
/* the choice exists at all: two suitable disks and not a virtual server */
static inline int install_raid_offered(const InstallState *s) {
    return s->virt != INSTALL_VIRT_VIRTUAL && install_raid_suitable(s) >= 2;
}
static inline int install_raid_allowed(const InstallState *s, int c) {
    if (c == RAID_NONE) return 1;
    return c > 0 && c < RAID_CHOICES && install_raid_offered(s) && install_raid_suitable(s) >= RAID_OPTS[c].min;
}
static inline int install_raid_from_level(const char *level) {
    for (int c = 1; c < RAID_CHOICES; c++)
        if (!RAID_OPTS[c].spares && !strcmp(level, RAID_OPTS[c].level)) return c;
    return RAID_NONE;
}
/* a disk the array will erase */
static inline int install_raid_member(const InstallState *s, int i) {
    return s->raid != RAID_NONE && i >= 0 && i < s->ndisks && install_disk_suitable(&s->disks[i]);
}
/* RAID needs the chosen (boot) disk to be a member: the first suitable one.
 * #2147: only a disk the person chose is moved -- no choice stays no choice
 * (a RAID default from the bridge must not pick a disk behind their back). */
static inline void install_raid_fix_disk(InstallState *s) {
    if (s->raid == RAID_NONE || s->disk < 0 || (s->disk < s->ndisks && install_disk_suitable(&s->disks[s->disk]))) return;
    for (int i = 0; i < s->ndisks; i++) if (install_disk_suitable(&s->disks[i])) { s->disk = i; return; }
}
static inline void install_raid_set(InstallState *s, int c) {
    if (!install_raid_allowed(s, c) || c == s->raid) return;
    s->raid = c; s->disk_ack = 0; install_raid_fix_disk(s);
}
/* Left/Right on the disk step: the next allowed choice, wrapping */
static inline void install_raid_step(InstallState *s, int dir) {
    for (int k = 1, c = s->raid; k <= RAID_CHOICES; k++) {
        c = (c + dir + RAID_CHOICES) % RAID_CHOICES;
        if (install_raid_allowed(s, c)) { install_raid_set(s, c); return; }
    }
}
/* picking a disk that cannot be a member leaves RAID (one disk, as chosen) */
static inline void install_pick_disk(InstallState *s, int d) {
    if (d < 0 || d >= s->ndisks || s->disks[d].iso) return;
    if (s->raid != RAID_NONE && !install_disk_suitable(&s->disks[d])) s->raid = RAID_NONE;
    if (d != s->disk) s->disk_ack = 0;
    s->disk = d;
}
/* answers: raid_disks is the chosen disk first, then every other member */
static inline void install_raid_disks(const InstallState *s, char *out, size_t n) {
    size_t used = 0; out[0] = 0;
    if (s->raid == RAID_NONE || s->disk < 0 || s->disk >= s->ndisks) return;
    for (int pass = 0; pass < 2; pass++)
        for (int i = 0; i < s->ndisks; i++) {
            if ((pass == 0) != (i == s->disk) || !install_raid_member(s, i)) continue;
            int w = snprintf(out + used, n - used, "%s%s", used ? "," : "", s->disks[i].path);
            if (w < 0 || (size_t)w >= n - used) { out[0] = 0; return; }   /* never a truncated list */
            used += (size_t)w;
        }
}
static inline int install_raid_count(const InstallState *s) {
    int n = 0;
    for (int i = 0; i < s->ndisks; i++) n += install_raid_member(s, i);
    return n;
}

#endif
