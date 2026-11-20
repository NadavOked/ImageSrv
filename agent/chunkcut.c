/* chunkcut -- one partition's stream in, independent zstd frames out (#2533).
 *
 * Until #2533 a partition file was ONE zstd stream of partclone's output:
 * to reach byte 100GB of it, 100GB had to be decompressed. Nothing could ask
 * for "the piece I missed" (#2533), go on from where a write was cut (#2528)
 * or check one piece by itself. Here the same bytes are cut into pieces that
 * stand alone -- image_format 2, the contract is handoffs/format-spec-2533.md
 * and docs/architecture/interfaces.md section 7:
 *
 *   frame 0      the head: partclone's image_desc_v2 header (110 bytes), the
 *                bit map and its CRC-32. Not there with --raw (partclone.dd).
 *   frame k+1    chunk k: bytes [k*4MiB, (k+1)*4MiB) of the DATA stream -- the
 *                used blocks in order, exactly what `fanout --readback` and
 *                server/agent/readbackhash.py call the data stream (#1805).
 *
 * Every frame is a whole standard zstd frame (its content size in its header,
 * its checksum at its end), so the file as a whole is still what `zstd -dc`
 * turns back into partclone's stream, byte for byte: the pipe of today
 * (udp-sender -> zstd -dc -> partclone -r) reads it unchanged.
 *
 * Usage:  chunkcut [-l <level 1..19>] [-T <threads, 0 = every core>] [--raw] -i <index-file>
 * Input:  stdin  -- `partclone.<fs> -c -a0` (or partclone.dd with --raw).
 * Output: stdout -- the frames; <index-file> -- one line a chunk (offset in the
 *         output, compressed length, XXH64 of the chunk's own bytes) under a
 *         header line; <index-file>.xxh64 -- the XXH64 of the index file itself,
 *         the manifest's index_xxh64; <index-file>.b3 (#3332) -- the BLAKE3 of
 *         every chunk's own bytes, 32 each, in order: the partition's BLAKE3 list,
 *         which a direct send uploads beside its index (the image server derives
 *         its own for an image in the library). All written, through a rename, only
 *         after a clean end, the index last: no index is the evidence that the
 *         stream was not cut whole.
 * Exit:   0 the whole stream was cut; 1 the stream is not what it must be (no
 *         partclone v2 header, checksums inside it, a bit map that disagrees
 *         with its header, an early or a late end), or stdout went away;
 *         2 usage, memory, libzstd.
 *
 * A chunk is compressed by itself in one call, so the output does not depend
 * on how many threads ran: a direct send reads the disk twice and its
 * receivers hash pass 2 against pass 1 (directsend.sh, #1574).
 *
 * libzstd: the shared library that every Debian carries (apt itself needs it)
 * is linked by its file name, and the eight calls used are declared below
 * instead of taken from zstd.h. That header comes with libzstd-dev, a package
 * a server in the field does not have and cannot fetch when its update
 * rebuilds this image offline (tools/boot-payload-build.sh). The calls are
 * libzstd's stable API since 1.4.0; an older library is refused at start.
 *
 * XXH64 is fanout.c's, seed zero: an index line is a `--readback -x` line.
 * BLAKE3 is the official C implementation in blake3/, its portable code only, by
 * inclusion -- the build line stays one file; chunkcast has the SSE4.1 one.
 */

#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define BLAKE3_NO_SSE2
#define BLAKE3_NO_SSE41
#define BLAKE3_NO_AVX2
#define BLAKE3_NO_AVX512
#include "blake3/blake3.c"
#include "blake3/blake3_dispatch.c"
#include "blake3/blake3_portable.c"

typedef struct ZSTD_CCtx_s ZSTD_CCtx;
ZSTD_CCtx *ZSTD_createCCtx(void);
size_t ZSTD_freeCCtx(ZSTD_CCtx *cctx);
size_t ZSTD_CCtx_setParameter(ZSTD_CCtx *cctx, int param, int value);
size_t ZSTD_compress2(ZSTD_CCtx *cctx, void *dst, size_t cap, const void *src, size_t n);
size_t ZSTD_compressBound(size_t n);
unsigned ZSTD_isError(size_t code);
const char *ZSTD_getErrorName(size_t code);
unsigned ZSTD_versionNumber(void);
#define ZSTD_c_compressionLevel 100
#define ZSTD_c_contentSizeFlag 200
#define ZSTD_c_checksumFlag 201
#define ZSTD_MIN_VERSION 10400      /* 1.4.0: ZSTD_compress2 and the parameters are stable */

#define CHUNK ((size_t)4 << 20)     /* fanout.c RB_CHUNK, readbackhash.py CHUNK_BYTES: the same number */
#define HEAD 110                    /* image_desc_v2 */
#define MAX_BITMAP (1ULL << 31)     /* bytes; fanout.c's own ceiling */
#define MAX_BLOCK (64ULL << 20)
#define MAX_THREADS 32

/* --- XXH64, seed zero (fanout.c) ------------------------------------------- */

#define XXH_P1 UINT64_C(11400714785074694791)
#define XXH_P2 UINT64_C(14029467366897019727)
#define XXH_P3 UINT64_C(1609587929392839161)
#define XXH_P4 UINT64_C(9650029242287828579)
#define XXH_P5 UINT64_C(2870177450012600261)

static uint64_t rotl64(uint64_t x, unsigned n) { return (x << n) | (x >> (64 - n)); }

static uint64_t le(const unsigned char *p, int n)
{
    uint64_t v = 0;
    for (int i = n - 1; i >= 0; i--)
        v = (v << 8) | p[i];
    return v;
}

static uint64_t xxh_round(uint64_t acc, uint64_t input)
{
    return rotl64(acc + input * XXH_P2, 31) * XXH_P1;
}

static uint64_t xxh64(const unsigned char *p, size_t n)
{
    const unsigned char *end = p + n;
    uint64_t x;
    if (n >= 32) {
        uint64_t v1 = XXH_P1 + XXH_P2, v2 = XXH_P2, v3 = 0, v4 = 0 - XXH_P1;
        for (; p + 32 <= end; p += 32) {
            v1 = xxh_round(v1, le(p, 8));
            v2 = xxh_round(v2, le(p + 8, 8));
            v3 = xxh_round(v3, le(p + 16, 8));
            v4 = xxh_round(v4, le(p + 24, 8));
        }
        x = rotl64(v1, 1) + rotl64(v2, 7) + rotl64(v3, 12) + rotl64(v4, 18);
        x ^= xxh_round(0, v1); x = x * XXH_P1 + XXH_P4;
        x ^= xxh_round(0, v2); x = x * XXH_P1 + XXH_P4;
        x ^= xxh_round(0, v3); x = x * XXH_P1 + XXH_P4;
        x ^= xxh_round(0, v4); x = x * XXH_P1 + XXH_P4;
    } else {
        x = XXH_P5;
    }
    x += n;
    for (; p + 8 <= end; p += 8) { x ^= xxh_round(0, le(p, 8)); x = rotl64(x, 27) * XXH_P1 + XXH_P4; }
    if (p + 4 <= end) { x ^= le(p, 4) * XXH_P1; x = rotl64(x, 23) * XXH_P2 + XXH_P3; p += 4; }
    for (; p < end; p++) { x ^= (uint64_t)*p * XXH_P5; x = rotl64(x, 11) * XXH_P1; }
    x ^= x >> 33; x *= XXH_P2; x ^= x >> 29; x *= XXH_P3; x ^= x >> 32;
    return x;
}

/* --- plain I/O --------------------------------------------------------------- */

/* Up to n bytes: fewer only at the end of stdin. -1 = a read error. */
static long long read_full(unsigned char *p, size_t n)
{
    size_t got = 0;
    while (got < n) {
        ssize_t r = read(STDIN_FILENO, p + got, n - got);
        if (r > 0) { got += (size_t)r; continue; }
        if (r == 0) break;
        if (errno == EINTR) continue;
        return -1;
    }
    return (long long)got;
}

static int write_full(int fd, const unsigned char *p, size_t n)
{
    while (n) {
        ssize_t w = write(fd, p, n);
        if (w > 0) { p += w; n -= (size_t)w; continue; }
        if (w < 0 && errno == EINTR) continue;
        return -1;
    }
    return 0;
}

/* --- the jobs ---------------------------------------------------------------- */

enum { FREE, READY, BUSY, DONE };

struct job {
    unsigned char *src, *dst;
    size_t n, z;
    uint64_t xxh;
    unsigned char b3[32];           /* #3332 */
    int state;
};

static struct job *jobs;
static int njobs, level = 3;
static size_t bound;
static uint64_t next_in, next_take, next_out;   /* chunk numbers: read, taken by a worker, written */
static int at_end, failed;
static char why[160];
static pthread_mutex_t mu = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t cv = PTHREAD_COND_INITIALIZER;

static char *lines;                 /* the index's chunk lines, in order */
static size_t lines_len, lines_cap;
static unsigned char *b3s;          /* #3332: the BLAKE3 list, 32 bytes a chunk, in order */
static size_t b3s_len, b3s_cap;
static uint64_t out_bytes;          /* bytes written to stdout so far */

static void fail(const char *reason)
{
    pthread_mutex_lock(&mu);
    if (!failed) { failed = 1; snprintf(why, sizeof why, "%s", reason); }
    pthread_cond_broadcast(&cv);
    pthread_mutex_unlock(&mu);
}

static ZSTD_CCtx *new_cctx(void)
{
    ZSTD_CCtx *c = ZSTD_createCCtx();
    if (!c) return NULL;
    if (ZSTD_isError(ZSTD_CCtx_setParameter(c, ZSTD_c_compressionLevel, level)) ||
        ZSTD_isError(ZSTD_CCtx_setParameter(c, ZSTD_c_contentSizeFlag, 1)) ||
        ZSTD_isError(ZSTD_CCtx_setParameter(c, ZSTD_c_checksumFlag, 1))) {
        ZSTD_freeCCtx(c);
        return NULL;
    }
    return c;
}

static void *worker(void *arg)
{
    (void)arg;
    ZSTD_CCtx *c = new_cctx();
    if (!c) { fail("libzstd gave no compression context"); return NULL; }
    for (;;) {
        pthread_mutex_lock(&mu);
        while (!failed && next_take == next_in && !at_end)
            pthread_cond_wait(&cv, &mu);
        if (failed || next_take == next_in) { pthread_mutex_unlock(&mu); break; }
        struct job *j = &jobs[next_take++ % (uint64_t)njobs];
        j->state = BUSY;
        pthread_mutex_unlock(&mu);

        j->xxh = xxh64(j->src, j->n);
        blake3_hasher h;
        blake3_hasher_init(&h);
        blake3_hasher_update(&h, j->src, j->n);
        blake3_hasher_finalize(&h, j->b3, 32);
        j->z = ZSTD_compress2(c, j->dst, bound, j->src, j->n);
        if (ZSTD_isError(j->z)) {
            char msg[120];
            snprintf(msg, sizeof msg, "libzstd: %s", ZSTD_getErrorName(j->z));
            fail(msg);
            break;
        }
        pthread_mutex_lock(&mu);
        j->state = DONE;
        pthread_cond_broadcast(&cv);
        pthread_mutex_unlock(&mu);
    }
    ZSTD_freeCCtx(c);
    return NULL;
}

/* The frames leave in chunk order, whatever order the workers finished in. */
static void *writer(void *arg)
{
    (void)arg;
    for (;;) {
        pthread_mutex_lock(&mu);
        struct job *j = &jobs[next_out % (uint64_t)njobs];
        while (!failed && !(next_out < next_in && j->state == DONE) && !(at_end && next_out == next_in))
            pthread_cond_wait(&cv, &mu);
        if (failed || next_out == next_in) { pthread_mutex_unlock(&mu); return NULL; }
        pthread_mutex_unlock(&mu);

        if (lines_len + 64 > lines_cap) {
            size_t cap = lines_cap ? lines_cap * 2 : 1 << 16;
            char *grown = realloc(lines, cap);
            if (!grown) { fail("no memory for the index"); return NULL; }
            lines = grown; lines_cap = cap;
        }
        if (b3s_len + 32 > b3s_cap) {
            size_t cap = b3s_cap ? b3s_cap * 2 : 1 << 15;
            unsigned char *grown = realloc(b3s, cap);
            if (!grown) { fail("no memory for the BLAKE3 list"); return NULL; }
            b3s = grown; b3s_cap = cap;
        }
        memcpy(b3s + b3s_len, j->b3, 32);
        b3s_len += 32;
        lines_len += (size_t)snprintf(lines + lines_len, 64, "%llu %zu %016llx\n",
                                      (unsigned long long)out_bytes, j->z, (unsigned long long)j->xxh);
        if (write_full(STDOUT_FILENO, j->dst, j->z) != 0) {
            char msg[120];
            snprintf(msg, sizeof msg, "writing the frames: %s", strerror(errno));
            fail(msg);
            return NULL;
        }
        out_bytes += j->z;
        pthread_mutex_lock(&mu);
        j->state = FREE;
        next_out++;
        pthread_cond_broadcast(&cv);
        pthread_mutex_unlock(&mu);
    }
}

/* Hands the next chunk to the workers. 0 = handed, -1 = the cut has failed. */
static int feed(size_t n, long long *got)
{
    pthread_mutex_lock(&mu);
    while (!failed && next_in - next_out >= (uint64_t)njobs)
        pthread_cond_wait(&cv, &mu);
    if (failed) { pthread_mutex_unlock(&mu); return -1; }
    struct job *j = &jobs[next_in % (uint64_t)njobs];
    pthread_mutex_unlock(&mu);

    *got = read_full(j->src, n);       /* the slot is ours until next_in moves */
    if (*got < 0) { fail("reading the stream failed"); return -1; }
    if (*got == 0) return 0;
    pthread_mutex_lock(&mu);
    j->n = (size_t)*got;
    j->state = READY;
    next_in++;
    pthread_cond_broadcast(&cv);
    pthread_mutex_unlock(&mu);
    return 0;
}

/* --- the head ---------------------------------------------------------------- */

/* Reads and judges partclone's header and bit map, and writes them as frame 0.
 * Returns the length of the data stream that must follow, or -1 (why is set). */
static long long cut_head(uint64_t *head_bytes, uint64_t *head_z, uint64_t *head_xxh)
{
    unsigned char h[HEAD];
    if (read_full(h, HEAD) != HEAD) { snprintf(why, sizeof why, "the stream ended inside the header"); return -1; }
    if (memcmp(h, "partclone-image", 15) != 0 || memcmp(h + 30, "0002", 4) != 0 || le(h + 34, 2) != 0xC0DE) {
        snprintf(why, sizeof why, "no partclone v2 header");
        return -1;
    }
    uint64_t total = le(h + 60, 8), used = le(h + 76, 8), bs = le(h + 84, 4);
    if (le(h + 92, 2) != 2 || h[105] != 1) {
        snprintf(why, sizeof why, "image version %u, bitmap mode %u -- not a v2 bit map",
                 (unsigned)le(h + 92, 2), (unsigned)h[105]);
        return -1;
    }
    if (le(h + 96, 2) != 0) {
        snprintf(why, sizeof why, "the stream carries partclone checksums (mode %u) -- capture reads with -a0",
                 (unsigned)le(h + 96, 2));
        return -1;
    }
    uint64_t bm = (total + 7) / 8;
    if (bs == 0 || bs > MAX_BLOCK || bm > MAX_BITMAP || used > total) {
        snprintf(why, sizeof why, "implausible header: %llu blocks of %llu bytes, %llu used",
                 (unsigned long long)total, (unsigned long long)bs, (unsigned long long)used);
        return -1;
    }
    size_t n = HEAD + (size_t)bm + 4;
    unsigned char *head = malloc(n), *z = malloc(ZSTD_compressBound(n));
    ZSTD_CCtx *c = new_cctx();
    long long data = -1;
    if (!head || !z || !c) { snprintf(why, sizeof why, "no memory for the bit map"); goto out; }
    memcpy(head, h, HEAD);
    if (read_full(head + HEAD, (size_t)bm + 4) != (long long)bm + 4) {
        snprintf(why, sizeof why, "the stream ended inside the bit map");
        goto out;
    }
    uint64_t marked = 0;
    for (uint64_t i = 0; i < bm; i++) {
        unsigned char b = head[HEAD + i];
        if (i == bm - 1 && total % 8) b &= (unsigned char)((1u << (total % 8)) - 1);   /* past the last block */
        marked += (uint64_t)__builtin_popcount(b);
    }
    if (marked != used) {
        snprintf(why, sizeof why, "the bit map marks %llu blocks, the header %llu",
                 (unsigned long long)marked, (unsigned long long)used);
        goto out;
    }
    size_t zn = ZSTD_compress2(c, z, ZSTD_compressBound(n), head, n);
    if (ZSTD_isError(zn)) { snprintf(why, sizeof why, "libzstd: %s", ZSTD_getErrorName(zn)); goto out; }
    if (write_full(STDOUT_FILENO, z, zn) != 0) { snprintf(why, sizeof why, "writing the head: %s", strerror(errno)); goto out; }
    *head_bytes = n; *head_z = zn; *head_xxh = xxh64(head, n);
    out_bytes = zn;
    data = (long long)(used * bs);
out:
    free(head); free(z);
    if (c) ZSTD_freeCCtx(c);
    return data;
}

/* One small file, whole or not at all: written beside itself and renamed. */
static int write_whole(const char *path, const char *data, size_t n)
{
    char tmp[4096];
    if (snprintf(tmp, sizeof tmp, "%s.tmp", path) >= (int)sizeof tmp) return -1;
    FILE *f = fopen(tmp, "w");
    if (!f) return -1;
    int ok = fwrite(data, 1, n, f) == n && fflush(f) == 0 && fsync(fileno(f)) == 0;
    if (fclose(f) != 0) ok = 0;
    if (!ok || rename(tmp, path) != 0) { unlink(tmp); return -1; }
    return 0;
}

/* The index, and beside it "<index>.xxh64": the XXH64 of the index file as it
 * is -- the manifest's index_xxh64. The digest first: an index without it is
 * not yet the evidence the shell looks for. */
static int write_index(const char *path, int raw, uint64_t head_bytes, uint64_t head_z,
                       uint64_t head_xxh, uint64_t data_bytes, uint64_t chunks)
{
    char head_hex[17] = "-", sum_path[4096], b3_path[4096], sum[18];
    if (snprintf(sum_path, sizeof sum_path, "%s.xxh64", path) >= (int)sizeof sum_path) return -1;
    if (snprintf(b3_path, sizeof b3_path, "%s.b3", path) >= (int)sizeof b3_path) return -1;
    if (!raw) snprintf(head_hex, sizeof head_hex, "%016llx", (unsigned long long)head_xxh);
    char *text = malloc(lines_len + 256);
    if (!text) return -1;
    size_t n = (size_t)snprintf(text, 256, "IMAGESRV-IDX 2 %s %zu %llu %llu %s %llu %llu %llu\n",
                                raw ? "raw" : "partclone", CHUNK,
                                (unsigned long long)head_bytes, (unsigned long long)head_z, head_hex,
                                (unsigned long long)data_bytes, (unsigned long long)chunks,
                                (unsigned long long)out_bytes);
    if (lines_len) memcpy(text + n, lines, lines_len);
    n += lines_len;
    snprintf(sum, sizeof sum, "%016llx\n", (unsigned long long)xxh64((const unsigned char *)text, n));
    int rc = write_whole(b3_path, (const char *)(b3s ? b3s : (unsigned char *)""), b3s_len) == 0 &&
             write_whole(sum_path, sum, 17) == 0 && write_whole(path, text, n) == 0 ? 0 : -1;
    free(text);
    return rc;
}

int main(int argc, char **argv)
{
    const char *index_path = NULL;
    int raw = 0, threads = 0;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--raw")) raw = 1;
        else if (!strcmp(argv[i], "-i") && i + 1 < argc) index_path = argv[++i];
        else if (!strcmp(argv[i], "-l") && i + 1 < argc) level = atoi(argv[++i]);
        else if (!strcmp(argv[i], "-T") && i + 1 < argc) threads = atoi(argv[++i]);
        else index_path = NULL, i = argc;
    }
    if (!index_path || level < 1 || level > 19 || threads < 0) {
        fprintf(stderr, "usage: chunkcut [-l <level 1..19>] [-T <threads>] [--raw] -i <index-file>\n");
        return 2;
    }
    if (ZSTD_versionNumber() < ZSTD_MIN_VERSION) {
        fprintf(stderr, "chunkcut: libzstd %u is older than 1.4.0\n", ZSTD_versionNumber());
        return 2;
    }
    if (threads == 0) threads = (int)sysconf(_SC_NPROCESSORS_ONLN);
    if (threads < 1) threads = 1;
    if (threads > MAX_THREADS) threads = MAX_THREADS;
    signal(SIGPIPE, SIG_IGN);           /* a reader that left is a write error with a name, not a silent death */
    char old_sum[4096];                 /* an index of an earlier cut is not this one's evidence */
    unlink(index_path);
    if (snprintf(old_sum, sizeof old_sum, "%s.xxh64", index_path) < (int)sizeof old_sum) unlink(old_sum);
    if (snprintf(old_sum, sizeof old_sum, "%s.b3", index_path) < (int)sizeof old_sum) unlink(old_sum);

    uint64_t head_bytes = 0, head_z = 0, head_xxh = 0;
    long long data = -1;                /* raw: unknown until the end */
    if (!raw && (data = cut_head(&head_bytes, &head_z, &head_xxh)) < 0) {
        fprintf(stderr, "chunkcut: %s\n", why);
        return 1;
    }

    njobs = threads + 2;                /* a chunk a worker, and two read ahead: ~8MiB each */
    bound = ZSTD_compressBound(CHUNK);
    jobs = calloc((size_t)njobs, sizeof *jobs);
    pthread_t tid[MAX_THREADS + 1];
    if (!jobs) { fprintf(stderr, "chunkcut: no memory\n"); return 2; }
    for (int i = 0; i < njobs; i++) {
        jobs[i].src = malloc(CHUNK);
        jobs[i].dst = malloc(bound);
        if (!jobs[i].src || !jobs[i].dst) { fprintf(stderr, "chunkcut: no memory for %d chunks\n", njobs); return 2; }
    }
    for (int i = 0; i < threads; i++)
        if (pthread_create(&tid[i], NULL, worker, NULL) != 0) { fprintf(stderr, "chunkcut: no thread\n"); return 2; }
    if (pthread_create(&tid[threads], NULL, writer, NULL) != 0) { fprintf(stderr, "chunkcut: no thread\n"); return 2; }

    uint64_t total = 0;
    for (;;) {
        size_t want = CHUNK;
        long long got = 0;
        if (!raw && (uint64_t)data - total < want) want = (size_t)((uint64_t)data - total);
        if (want == 0 || feed(want, &got) != 0 || got == 0) break;
        total += (uint64_t)got;
        if ((size_t)got < want) break;      /* a short read is the end of stdin */
    }
    if (!failed && !raw) {
        unsigned char extra;
        if (total != (uint64_t)data) {
            char msg[120];
            snprintf(msg, sizeof msg, "the stream ended %llu bytes early", (unsigned long long)((uint64_t)data - total));
            fail(msg);
        } else if (read_full(&extra, 1) != 0) {
            fail("bytes after the last block");
        }
    }
    if (!failed && raw && total == 0) fail("an empty stream");

    pthread_mutex_lock(&mu);
    at_end = 1;
    pthread_cond_broadcast(&cv);
    pthread_mutex_unlock(&mu);
    for (int i = 0; i <= threads; i++) pthread_join(tid[i], NULL);

    if (failed) {
        fprintf(stderr, "chunkcut: %s\n", why);
        return 1;
    }
    if (write_index(index_path, raw, head_bytes, head_z, head_xxh, total, next_out) != 0) {
        fprintf(stderr, "chunkcut: the index was not written: %s\n", strerror(errno));
        return 2;
    }
    fprintf(stderr, "chunkcut: %llu chunks, %llu data bytes in, %llu bytes out (zstd -%d, %d threads, libzstd %u)\n",
            (unsigned long long)next_out, (unsigned long long)total, (unsigned long long)out_bytes,
            level, threads, ZSTD_versionNumber());
    return 0;
}
