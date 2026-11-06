/* #1503: see he_time.h. */
#include <stdio.h>
#include "he_time.h"

void he_secs(long long n, char *out, size_t sz) {
    if (n == 1) snprintf(out, sz, "שנייה");
    else snprintf(out, sz, "%lld שניות", n);
}

void he_ago(long long age, char *out, size_t sz) {
    if (age < 0) age = 0;
    if (age == 1)          snprintf(out, sz, "לפני שנייה");
    else if (age < 60)     snprintf(out, sz, "לפני %lld שניות", age);
    else if (age < 120)    snprintf(out, sz, "לפני דקה");
    else if (age < 3600)   snprintf(out, sz, "לפני %lld דקות", age / 60);
    else if (age < 7200)   snprintf(out, sz, "לפני שעה");
    else                   snprintf(out, sz, "לפני %lld שעות", age / 3600);
}
