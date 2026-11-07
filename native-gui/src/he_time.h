/* #1503: Hebrew time words with the singular where Hebrew has one. "לפני 1 שניות"
 * read as a bug on the cloner's waiting screen. No dependencies: tests compile
 * he_time.c on its own. */
#ifndef HE_TIME_H
#define HE_TIME_H
#include <stddef.h>

/* "שנייה" for 1, else "N שניות" (0 included): a count that stands alone,
 * e.g. after "בעוד". */
void he_secs(long long n, char *out, size_t sz);

/* "לפני שנייה" / "לפני 2 שניות" / "לפני דקה" / "לפני 5 דקות" / "לפני שעה" /
 * "לפני 3 שעות". Negative counts as 0. */
void he_ago(long long age, char *out, size_t sz);
#endif
