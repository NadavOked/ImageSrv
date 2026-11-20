/* canary -- a harness with a planted heap overflow behind a 4-byte compare.
 *
 * fuzz.yml runs it first and requires a crash, an ASan "heap-buffer-overflow"
 * report and a crash-* file. If the fuzzer or the sanitizer stopped working,
 * the real harnesses would "run clean" and prove nothing (principle 5).
 */
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size)
{
    if (size >= 4 && memcmp(data, "FUZZ", 4) == 0) {
        volatile size_t past = 4;   /* volatile: the compiler may not see the overflow */
        char *p = malloc(4);
        p[past] = 1;                /* one byte past the end */
        free(p);
    }
    return 0;
}
