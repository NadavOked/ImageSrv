/*
 * #1475: what one scan of a 1920x1080x32 frame costs, measured with the
 * monitor's own scan_tiles() -- monitor.c is compiled in as it is, its main
 * renamed. Compared with what the code before #1475 did on every tick: a
 * memcpy of the whole frame (and a full-screen mark, which then went out as
 * 8.3 MB of Raw). Built and run by tests/test_monitor_native.py
 * (test_scan_cost_at_1920x1080_is_measured); prints key=value per line.
 *
 * These are RAM-to-RAM numbers -- fb.mem, the path the GUI mirror gives the
 * monitor. /dev/fb0 is write-combined video memory and reads from it are
 * many times slower; that is why the mirror exists, and it is not measured
 * here.
 */
#define main imagesrv_monitor_main
#include "../agent/monitor.c"
#undef main

#define BENCH_W 1920u
#define BENCH_H 1080u
#define REPS 200

static double since_us(const struct timespec *start) {
    struct timespec now;

    clock_gettime(CLOCK_MONOTONIC, &now);
    return (now.tv_sec - start->tv_sec) * 1e6 +
           (now.tv_nsec - start->tv_nsec) / 1e3;
}

int main(void) {
    size_t bytes = (size_t)BENCH_W * BENCH_H * 4;
    unsigned cols = (BENCH_W + TILE_W - 1) / TILE_W;
    unsigned rows = (BENCH_H + TILE_H - 1) / TILE_H;
    uint8_t *source = malloc(bytes), *copy = malloc(bytes);
    unsigned char *dirty = calloc((size_t)cols * rows, 1);
    struct timespec start;
    double copy_us, unchanged_us, one_tile_us, all_us;
    size_t i;
    int rep;

    if (!source || !copy || !dirty)
        return 1;
    for (i = 0; i < bytes; i++)
        source[i] = (uint8_t)(i * 2654435761u >> 24);
    memcpy(copy, source, bytes);

    clock_gettime(CLOCK_MONOTONIC, &start);
    for (rep = 0; rep < REPS; rep++) {
        memcpy(copy, source, bytes);
        __asm__ volatile("" ::: "memory");
    }
    copy_us = since_us(&start) / REPS;

    clock_gettime(CLOCK_MONOTONIC, &start);
    for (rep = 0; rep < REPS; rep++) {
        scan_tiles(source, (size_t)BENCH_W * 4, copy, BENCH_W, BENCH_H, dirty, cols);
        __asm__ volatile("" ::: "memory");
    }
    unchanged_us = since_us(&start) / REPS;
    for (i = 0; i < (size_t)cols * rows; i++)
        if (dirty[i])
            return 2;                    /* nothing changed, nothing flagged */

    clock_gettime(CLOCK_MONOTONIC, &start);
    for (rep = 0; rep < REPS; rep++) {
        source[((size_t)500 * BENCH_W + 900) * 4] ^= 1;   /* a pointer-sized change */
        scan_tiles(source, (size_t)BENCH_W * 4, copy, BENCH_W, BENCH_H, dirty, cols);
        __asm__ volatile("" ::: "memory");
    }
    one_tile_us = since_us(&start) / REPS;
    if (!dirty[(500 / TILE_H) * cols + 900 / TILE_W])
        return 3;

    clock_gettime(CLOCK_MONOTONIC, &start);
    for (rep = 0; rep < REPS; rep++) {
        for (i = 0; i < bytes; i += 4)
            source[i] ^= 1;                             /* every pixel */
        scan_tiles(source, (size_t)BENCH_W * 4, copy, BENCH_W, BENCH_H, dirty, cols);
        __asm__ volatile("" ::: "memory");
    }
    all_us = since_us(&start) / REPS;     /* includes the loop that changes them */

    printf("frame=%ux%ux32\n", BENCH_W, BENCH_H);
    printf("copy_us=%.1f\n", copy_us);
    printf("scan_unchanged_us=%.1f\n", unchanged_us);
    printf("scan_one_tile_us=%.1f\n", one_tile_us);
    printf("scan_all_changed_us=%.1f\n", all_us);
    free(source);
    free(copy);
    free(dirty);
    return 0;
}
