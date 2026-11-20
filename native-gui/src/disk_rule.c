/* #3666: takes a disk's verdict as the server's brain / the agent delivered it -- see disk_rule.h. No table here. */
#include <stdio.h>
#include <string.h>
#include "disk_rule.h"

static const char *const NAMES[] = { "grey", "green", "orange", "yellow", "red" };

DiskRule disk_rule_none(void) {
    DiskRule r = { 0, DR_GREY, 1, 1, "", "" };
    return r;
}

int disk_rule_take(DiskRule *r, const char *color, const char *source, const char *target, const char *reason, const char *he) {
    int c = -1;
    *r = disk_rule_none();
    for (int k = 0; k < 5; k++) if (color && !strcmp(color, NAMES[k])) c = k;
    if (c < 0 || !source || !target || (strcmp(source, "0") && strcmp(source, "1")) || (strcmp(target, "0") && strcmp(target, "1"))) return 0;
    r->set = 1; r->color = c; r->source = source[0] == '1'; r->target = target[0] == '1';
    snprintf(r->reason, sizeof r->reason, "%s", reason ? reason : "");
    snprintf(r->he, sizeof r->he, "%s", he ? he : "");
    return 1;
}

const char *disk_rule_color(int c) {
    return c >= 0 && c < 5 ? NAMES[c] : "grey";
}
