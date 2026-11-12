/* #2848: the one rule -- see disk_rule.h. Keep the order and the words with disk_rule.py. */
#include <string.h>
#include "disk_rule.h"

static const char *const RED[] = { "smart_fail", "link_slow", "sata_issue", "write_slow" };
static const char *const YELLOW[] = { "failed_last", "write_incomplete" };

static const struct { const char *reason, *he; } HE[] = {
    { "smart_fail", "SMART נכשל — לא כותבים על הדיסק הזה; אפשר להעתיק ממנו או לחלץ לשרת" },
    { "link_slow", "קישור SATA איטי — לא כותבים על הדיסק הזה; אפשר להעתיק ממנו" },
    { "sata_issue", "בעיית SATA (הקישור התאפס או המגירה התנתקה) — לא כותבים על הדיסק הזה; אפשר להעתיק ממנו" },
    { "write_slow", "נפסל כאיטי בבדיקת הכתיבה בסבב הזה — לא כותבים עליו בסבב הזה; אפשר להעתיק ממנו" },
    { "failed_last", "נכשל בשיכפול הקודם — לא מעתיקים ממנו; אפשר לכתוב עליו" },
    { "write_incomplete", "כתיבה לא הושלמה — לא מעתיקים ממנו; אפשר לכתוב עליו" },
};

static int is(const char *a, const char *b) { return a && !strcmp(a, b); }

DiskRule disk_rule(const DiskFacts *f) {
    const char *sm = f->smart ? f->smart : "unchecked";
    int on_red[4] = { is(sm, "fail"), !!f->link_slow, !!f->sata_issue, !!f->write_slow };
    int on_yel[2] = { !!f->failed_last || is(sm, "failed_last"), !!f->write_incomplete };
    DiskRule r = { DR_GREY, 1, 1, NULL, NULL, NULL };
    for (int i = 0; i < 4 && !r.target_reason; i++) if (on_red[i]) r.target_reason = RED[i];
    for (int i = 0; i < 2 && !r.source_reason; i++) if (on_yel[i]) r.source_reason = YELLOW[i];
    r.target = !r.target_reason; r.source = !r.source_reason;
    r.reason = r.target_reason ? r.target_reason : r.source_reason;
    r.color = r.target_reason ? DR_RED : r.source_reason ? DR_YELLOW : is(sm, "warn") ? DR_ORANGE : is(sm, "ok") ? DR_GREEN : DR_GREY;
    return r;
}

const char *disk_rule_he(const char *reason) {
    for (unsigned i = 0; reason && i < sizeof HE / sizeof HE[0]; i++) if (!strcmp(HE[i].reason, reason)) return HE[i].he;
    return "";
}

const char *disk_rule_color(int c) {
    return c == DR_RED ? "red" : c == DR_YELLOW ? "yellow" : c == DR_ORANGE ? "orange" : c == DR_GREEN ? "green" : "grey";
}
