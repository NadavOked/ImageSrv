/* rescuecat.c -- #2527: the source reader of a rescue copy (a local clone of a disk that is dying).
 * Linux, one file, no libraries.
 *
 *   rescuecat read  -s SRC -b UNIT -m MAP [-n TRIES] [-L MAXRUN]            > stream
 *   rescuecat retry -s SRC -b UNIT -m MAP -n ROUNDS [-T SECONDS] [-P PROGRESS] -t TARGET [-t TARGET...]
 *
 * read:  the whole of SRC to stdout, in order, exactly its size. 1MiB blocks, O_DIRECT. A block
 *        that does not read whole is read again by UNIT (a sector), TRIES times each (default 1: a
 *        dying disk is not asked twice in the first pass); a unit that still fails is zeros in the
 *        stream and a range in MAP. A disk that stopped answering (a device error, or MAXRUN bytes
 *        in a row -- default 64MiB) ends the read with 4 and a MAP that says "aborted": the stream
 *        is short, never a copy of zeros. Exit 0 = the stream is complete; MAP says what is zeros.
 * retry: after the stream, the unread units again, ROUNDS times over all of them (never one sector
 *        many times in a row; odd rounds forward, even backward), for at most SECONDS (default 600).
 *        A unit that reads is written to every TARGET at the same offset (a raw copy) and leaves MAP.
 *        stdout: "failed <target> <errno>" for a target that stopped taking writes.
 *        PROGRESS: "X N" -- the round that starts, of N -- written whole at each round (the screen says it).
 * MAP: "# complete" ends a map that is whole; no such line is no evidence. Lines: start length errno
 *      (bytes from the start of SRC). Not ddrescue's format -- the idea only (no code from exhume).
 * Exit: 0 done, 1 usage/IO, 2 stdout gone, 3 SRC not opened, 4 the disk stopped answering. */
#define _GNU_SOURCE
#define _FILE_OFFSET_BITS 64
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#define CHUNK (1 << 20)

struct rg { off_t at, len; int err; };
static struct rg *rg; static size_t nrg, caprg;
static off_t size, maxrun = 64LL << 20;
static size_t unit = 4096;
static const char *mapf, *srcf, *progf;

static void die(int rc, const char *fmt, ...)
{
    va_list ap;
    fputs("rescuecat: ", stderr);
    va_start(ap, fmt); vfprintf(stderr, fmt, ap); va_end(ap);
    fputc('\n', stderr);
    exit(rc);
}

static void add(struct rg **l, size_t *n, size_t *cap, off_t at, off_t len, int err)
{
    if (*n && (*l)[*n - 1].at + (*l)[*n - 1].len == at) { (*l)[*n - 1].len += len; return; }
    if (*n == *cap) {
        *cap = *cap ? *cap * 2 : 1024;
        if (!(*l = realloc(*l, *cap * sizeof **l))) die(1, "out of memory");
    }
    (*l)[(*n)++] = (struct rg){ at, len, err };
}

static int fatal(int e)   /* the disk is gone or the request is wrong -- not a bad sector */
{
    return e == ENODEV || e == ENXIO || e == ESHUTDOWN || e == ENOMEDIUM || e == EINVAL || e == EBADF || e == EFAULT;
}

static void write_map(const char *aborted)
{
    char tmp[4096]; FILE *f;
    snprintf(tmp, sizeof tmp, "%s.tmp", mapf);
    if (!(f = fopen(tmp, "w"))) die(1, "%s: %s", tmp, strerror(errno));
    fprintf(f, "# rescuecat map v1 -- unread ranges of %s, bytes from its start: start length errno\n# size %lld\n# unit %zu\n",
            srcf, (long long)size, unit);
    for (size_t i = 0; i < nrg; i++) fprintf(f, "%lld %lld %d\n", (long long)rg[i].at, (long long)rg[i].len, rg[i].err);
    if (aborted) fprintf(f, "# aborted %s\n", aborted); else fputs("# complete\n", f);
    if (fflush(f) || fsync(fileno(f)) || fclose(f) || rename(tmp, mapf)) die(1, "%s: %s", mapf, strerror(errno));
}

static void give_up(const char *why)
{
    write_map(why);
    die(4, "the disk stopped answering: %s -- the stream is short, the map says where", why);
}

static ssize_t rd(int fd, void *b, size_t n, off_t at)
{
    ssize_t r;
    int again = 0;
    while ((r = pread(fd, b, n, at)) < 0 && errno == EINTR && ++again < 100) ;
    return r;
}

static int unit_ok(int fd, char *b, off_t at, int tries, int *err)
{
    for (int t = 0; t < tries; t++) {
        ssize_t n = rd(fd, b, unit, at);
        if (n == (ssize_t)unit) return 1;
        *err = n < 0 ? errno : EIO;
        if (fatal(*err)) return 0;
    }
    return 0;
}

static void emit(const char *b, size_t n)
{
    while (n) {
        ssize_t w = write(1, b, n);
        if (w < 0 && errno == EINTR) continue;
        if (w <= 0) die(2, "stdout: %s", w < 0 ? strerror(errno) : "closed");
        b += w; n -= (size_t)w;
    }
}

static void do_read(int fd, int tries)
{
    char *b; off_t run = 0;
    if (posix_memalign((void **)&b, 4096, CHUNK)) die(1, "out of memory");
    for (off_t off = 0; off < size; off += CHUNK) {
        size_t len = size - off < CHUNK ? (size_t)(size - off) : CHUNK, good = 0;
        ssize_t n = rd(fd, b, len, off);
        if (n == (ssize_t)len) good = len;
        else if (n > 0) good = (size_t)n - (size_t)n % unit;
        else if (n < 0 && fatal(errno)) give_up(strerror(errno));
        if (good) run = 0;
        for (size_t p = good; p < len; p += unit) {
            int e = EIO;
            if (unit_ok(fd, b + p, off + p, tries, &e)) { run = 0; continue; }
            if (fatal(e)) give_up(strerror(e));
            memset(b + p, 0, unit); add(&rg, &nrg, &caprg, off + p, unit, e);
            if ((run += unit) > maxrun) give_up("too many unread bytes in a row");
        }
        emit(b, len);
    }
    write_map(NULL);
    off_t bad = 0; for (size_t i = 0; i < nrg; i++) bad += rg[i].len;
    fprintf(stderr, "rescuecat: read %lld bytes, unread_sectors=%lld ranges=%zu\n", (long long)size, (long long)(bad / 512), nrg);
}

static int cmp(const void *a, const void *b)
{
    off_t x = ((const struct rg *)a)->at, y = ((const struct rg *)b)->at;
    return x < y ? -1 : x > y;
}

static void load_map(void)
{
    char line[256]; long long a, l; int e; FILE *f = fopen(mapf, "r");
    if (!f) die(1, "%s: %s", mapf, strerror(errno));
    while (fgets(line, sizeof line, f))
        if (line[0] != '#' && sscanf(line, "%lld %lld %d", &a, &l, &e) == 3 && l > 0) add(&rg, &nrg, &caprg, a, l, e);
    fclose(f);
}

static void say_round(int r, int rounds)
{
    char tmp[4096]; FILE *f;
    if (!progf) return;
    snprintf(tmp, sizeof tmp, "%s.tmp", progf);
    if (!(f = fopen(tmp, "w"))) return;   /* the screen's line only: the retry itself does not depend on it */
    fprintf(f, "%d %d\n", r + 1, rounds);
    if (fclose(f) == 0) rename(tmp, progf);
}

static void do_retry(int fd, char **tg, int ntg, int rounds, int secs)
{
    int tf[64], alive = 0; char *b; struct timespec t0, t;
    if (ntg > 64) die(1, "at most 64 targets");
    if (posix_memalign((void **)&b, 4096, unit)) die(1, "out of memory");
    load_map();
    for (int i = 0; i < ntg; i++) {
        if ((tf[i] = open(tg[i], O_WRONLY)) < 0) { printf("failed %s %d\n", tg[i], errno); continue; }
        alive++;
    }
    clock_gettime(CLOCK_MONOTONIC, &t0);
    for (int r = 0; r < rounds && nrg && alive; r++) {
        struct rg *nx = NULL; size_t nn = 0, cap = 0; int out = 0;
        say_round(r, rounds);
        for (size_t k = 0; k < nrg; k++) {
            struct rg *g = &rg[r % 2 ? nrg - 1 - k : k];
            for (off_t u = 0; u < g->len; u += unit) {
                int e = g->err;
                clock_gettime(CLOCK_MONOTONIC, &t);
                if (out || t.tv_sec - t0.tv_sec > secs) { out = 1; add(&nx, &nn, &cap, g->at + u, unit, g->err); continue; }
                if (!unit_ok(fd, b, g->at + u, 1, &e)) {
                    if (fatal(e)) die(4, "the disk stopped answering: %s -- the map is as it was", strerror(e));
                    add(&nx, &nn, &cap, g->at + u, unit, e); continue;
                }
                for (int i = 0; i < ntg; i++) {
                    if (tf[i] < 0) continue;
                    if (pwrite(tf[i], b, unit, g->at + u) != (ssize_t)unit) { printf("failed %s %d\n", tg[i], errno); close(tf[i]); tf[i] = -1; alive--; }
                }
            }
        }
        free(rg); rg = nx; nrg = nn; caprg = cap;
        if (rg) qsort(rg, nrg, sizeof *rg, cmp);   /* a round that read every unit leaves rg NULL; qsort's base is nonnull (#3891) */
        for (size_t i = 1; i < nrg; ) {   /* sorted: a backward round's pieces meet again */
            if (rg[i - 1].at + rg[i - 1].len == rg[i].at) { rg[i - 1].len += rg[i].len; memmove(&rg[i], &rg[i + 1], (nrg - i - 1) * sizeof *rg); nrg--; }
            else i++;
        }
        if (out) { fputs("rescuecat: the retry time budget ended -- the rest stays unread\n", stderr); break; }
    }
    for (int i = 0; i < ntg; i++) if (tf[i] >= 0 && fdatasync(tf[i])) { printf("failed %s %d\n", tg[i], errno); }
    write_map(NULL);
    off_t bad = 0; for (size_t i = 0; i < nrg; i++) bad += rg[i].len;
    fprintf(stderr, "rescuecat: retry done, unread_sectors=%lld ranges=%zu\n", (long long)(bad / 512), nrg);
}

int main(int argc, char **argv)
{
    int c, tries = 1, rounds = 3, secs = 600, ntg = 0, fd; char *tg[64];
    if (argc < 2 || (strcmp(argv[1], "read") && strcmp(argv[1], "retry"))) die(1, "usage: rescuecat read|retry -s SRC -b UNIT -m MAP ...");
    int rd_mode = !strcmp(argv[1], "read");
    optind = 1; argv++; argc--;
    while ((c = getopt(argc, argv, "s:b:m:n:L:T:P:t:")) != -1) switch (c) {
        case 's': srcf = optarg; break;
        case 'b': unit = (size_t)atol(optarg); break;
        case 'm': mapf = optarg; break;
        case 'n': tries = rounds = atoi(optarg); break;
        case 'L': maxrun = atoll(optarg); break;
        case 'T': secs = atoi(optarg); break;
        case 'P': progf = optarg; break;
        case 't': if (ntg < 64) tg[ntg++] = optarg; break;
        default: die(1, "bad option");
    }
    if (!srcf || !mapf || (unit != 512 && unit != 4096) || tries < 1 || (!rd_mode && !ntg)) die(1, "usage: rescuecat read|retry -s SRC -b 512|4096 -m MAP ...");
    signal(SIGPIPE, SIG_IGN);
    if ((fd = open(srcf, O_RDONLY | O_DIRECT)) < 0) die(3, "%s: %s", srcf, strerror(errno));
    if ((size = lseek(fd, 0, SEEK_END)) <= 0) die(3, "%s: no size", srcf);
    if (size % (off_t)unit) { unit = 512; if (size % 512) die(1, "%s: size %lld is not whole sectors", srcf, (long long)size); }
    if (rd_mode) do_read(fd, tries); else do_retry(fd, tg, ntg, rounds, secs);
    return 0;
}
