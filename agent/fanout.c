/* fanout -- one stream in, several independent writers out.
 *
 * The cloning-room machines receive the multicast stream once and write it
 * to 2-3 drawers at the same time. The obvious `tee` cannot do this: it
 * writes to its outputs in lockstep, so the slowest drive dictates the pace
 * and a stalled one halts every drive on the machine. With 36 drives per
 * round that is not a rare case, it is a nightly one.
 *
 * So each target gets its own bounded queue and is written non-blocking.
 * When any live target's queue is full the stream is too fast for that drive,
 * and fanout blocks the reader until it drains: the stream is paced by the
 * slowest live drawer, and that backpressure is what slows the multicast
 * sender down (#23, #660). A slow drawer is never dropped -- the whole
 * machine follows it, the way udp-sender waits for a slow receiver rather
 * than evicting it (#657). Only a write error -- a reader that has gone,
 * EPIPE -- fails a single target, while the others carry on. "Slow" has a
 * floor, though: a drawer that holds the reader for BLOCK_EVICT_MS while a
 * sibling has room is not slow, it is the one stopping the machine (#1796).
 *
 * The rule that shapes the whole program: never discard a block and let the
 * target continue. Multicast cannot resend, so a target that missed bytes can
 * only be a failed target. A drive that is quietly short a few megabytes in
 * the middle is far worse than one that visibly failed.
 *
 * Usage:  fanout [-H hash-file] [-S sha256-file] [-P progress-file] [-U units-counter]
 *                <bytes-per-target-buffer> <out1> [out2] [out3] ...
 *         fanout --xxh64 | --sha256
 * -H writes XXH64 and -S writes SHA-256 after a clean EOF; -P appends input bytes.
 * -U puts every "<path>.bytes" in the units of another counter (see units_now).
 * SHA-256 runs in the kernel (AF_ALG) when it can, else in-process; stderr says
 * which. FANOUT_SHA_ENGINE=builtin|kernel forces one (#1596).
 * --xxh64 hashes stdin and writes the 16-digit lowercase digest to stdout.
 * --sha256 hashes stdin and writes the 64-digit lowercase digest to stdout.
 *         fanout --readback [-x [-s <seed> <permille>]] <device> [<raw-bytes>]   (#1805)
 * Input:  stdin.
 * Output: one line per finished target on stdout: "<path> ok" | "<path> failed <reason>"
 *         plus, for each target, a running byte count appended to "<path>.bytes".
 * Exit:   0 if every target finished, 1 if any failed, 2 on usage/fatal errors.
 *
 * "ok" means the stream length was accepted -- empty stdin is never ok, and
 * FANOUT_EXPECTED_BYTES if set must match byte-for-byte -- and close() (and
 * fsync on a non-FIFO) succeeded. It is not a disk write confirmation: the
 * counter is bytes handed to the fd, and the shell's pipeline.rc is the
 * evidence that partclone exited 0 (#427).
 *
 * The byte count is here, and not in the shell, because this program is the
 * only place that knows how much of the stream each drawer actually took.
 * The single `pv` in front of it measures the machine's stream, which is the
 * same number for every drawer and therefore says nothing about any one of
 * them (#25). The format is one decimal number per line, appended -- exactly
 * what `pv -n` writes, so the agent's reporter reads both the same way
 * (progress.sh, interfaces.md section 4).
 */

#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <linux/fs.h>
#include <linux/if_alg.h>
#include <time.h>
#include <unistd.h>

/* Every wait in this program has a ceiling, and every ceiling is here.
 *
 *   open()ing a fifo        -> OPEN_RETRY_MS   (and O_NONBLOCK, so the call
 *                                               itself never blocks)
 *   poll() for writability  -> ROOM_POLL_MS, never infinite
 *
 * What has no ceiling in the ordinary case, by design (#660): make_room()
 * blocks the reader for as long as a live drawer keeps draining, so a slow
 * drawer sets the pace and the stream follows it rather than being dropped.
 * The one exception (#674): make_room() evicts a drawer that is full AND has
 * made no write progress for STALL_EVICT_MS. A drawer that accepts any bytes
 * at all keeps resetting that clock, so healthy-but-slow drives are never
 * evicted -- only a consumer that has stopped taking bytes entirely, which a
 * hung disk (I/O timeout, not immediate EIO) is and a slow one is not. The
 * shell watchdog (wait_progress() in agent/lib/waits.sh) and partclone's exit
 * status remain the machine-level ceilings; this one isolates the responsible
 * drawer before they fire, so its healthy siblings are not taken down with it.
 *
 * That clock alone is not enough (#1796, metal 19/10): while make_room()
 * waits, stdin is not read, udp-receiver blocks on its pipe and stops
 * answering, and udp-sender drops the whole machine after ~190s of silence
 * (--retries-until-drop 200). 146s of a held stdin burnt nearly all of that,
 * and the stream never recovered -- both drawers failed for one stopped zstd.
 * And a drawer that trickles (4KiB every 500ms) resets the stall clock with
 * every write, so it held stdin with no ceiling at all. So a second clock
 * runs on the reader itself: once make_room() has held stdin for
 * BLOCK_EVICT_MS, every drawer that is still the reason -- full, having
 * not freed room for one read (READ_CHUNK = 1MiB unless FANOUT_CHUNK) since
 * the clock started -- is evicted, provided a sibling has room and so gains
 * from it. Nothing is queued while make_room() waits, so "still full" IS
 * "freed under one read": the effective floor is 1MiB/45s = ~23KB/s, four
 * orders under the ~210MB/s a cloner drive takes from dd (18/10), and far
 * under any drive that still works. One drawer, or every drawer blocked
 * at once, frees nobody: there the stall clock and the shell stay the
 * ceilings, as before. Not at EOF either -- the sender is done by then.
 */
#define MAX_TARGETS 8
#define READ_CHUNK (1024 * 1024)
#define OPEN_RETRY_MS 5000      /* how long to wait for a reader on a fifo */
#define ROOM_POLL_MS 20
#define STALL_EVICT_MS 150000   /* full drawer, no FIFO write progress; override for tests */
#define BLOCK_EVICT_MS 45000    /* stdin held by full drawers (#1796); FANOUT_BLOCK_EVICT_MS */
#define COUNTER_SUFFIX ".bytes" /* the per-target progress counter, next to the fifo */
#define COUNTER_MS 1000         /* how often it is refreshed (the report goes out every 2s) */
#define PIPE_BYTES (1 << 20)     /* FANOUT_PIPE_BYTES overrides (local clone experiments, 18/10) */

#define OPEN_NO_READER (-2)     /* the fifo is there, nobody ever opened it */

struct target {
    const char *path;
    int fd;
    char *buf;          /* ring buffer */
    size_t cap;
    size_t head;        /* next byte to write out */
    size_t len;         /* bytes queued */
    int alive;
    const char *reason;
    long long taken;    /* bytes handed to this target's pipeline, for the counter */
    long long queued;   /* of those, bytes that waited in the ring (the pipe was full) */
    long long last_progress_ms; /* monotonic ms of the last accepted FIFO write (#674) */
    long long taken_at_block;   /* `taken` when make_room() began holding stdin (#1796) */
    int cfd;            /* the counter file, or -1 when it could not be opened */
    long long shown;    /* -U: the last scaled count written; frozen once the target fails */
};

static struct target targets[MAX_TARGETS];
static int target_count;
static long long stall_evict_ms = STALL_EVICT_MS;
static long long block_evict_ms = BLOCK_EVICT_MS;
static long long expected_bytes;          /* 0 = unknown; FANOUT_EXPECTED_BYTES */
static long long pipe_bytes = PIPE_BYTES; /* FANOUT_PIPE_BYTES */
static char stream_reason[96];            /* short/long stream, one string for all */
static const char *units_path;            /* -U: the counter whose units the targets report in */
static long long stdin_total;             /* bytes read from stdin so far */

static long long now_ms(void);

/* Portable XXH64, seed zero, from the public xxHash algorithm description.
 * Byte assembly keeps it correct on both endian orders and unaligned input. */
struct xxh64 {
    uint64_t total, v1, v2, v3, v4;
    unsigned char tail[32];
    size_t tail_len;
};

#define XXH_P1 UINT64_C(11400714785074694791)
#define XXH_P2 UINT64_C(14029467366897019727)
#define XXH_P3 UINT64_C(1609587929392839161)
#define XXH_P4 UINT64_C(9650029242287828579)
#define XXH_P5 UINT64_C(2870177450012600261)

static uint64_t rotl64(uint64_t x, unsigned n) { return (x << n) | (x >> (64 - n)); }

static uint64_t get64(const unsigned char *p)
{
    uint64_t v = 0;
    for (int i = 7; i >= 0; i--)
        v = (v << 8) | p[i];
    return v;
}

static uint32_t get32(const unsigned char *p)
{
    return (uint32_t)p[0] | (uint32_t)p[1] << 8 |
           (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}

static uint64_t xxh_round(uint64_t acc, uint64_t input)
{
    acc += input * XXH_P2;
    acc = rotl64(acc, 31);
    return acc * XXH_P1;
}

static void xxh_init(struct xxh64 *h)
{
    h->total = h->tail_len = 0;
    h->v1 = XXH_P1 + XXH_P2;
    h->v2 = XXH_P2;
    h->v3 = 0;
    h->v4 = 0 - XXH_P1;
}

static void xxh_block(struct xxh64 *h, const unsigned char *p)
{
    h->v1 = xxh_round(h->v1, get64(p));
    h->v2 = xxh_round(h->v2, get64(p + 8));
    h->v3 = xxh_round(h->v3, get64(p + 16));
    h->v4 = xxh_round(h->v4, get64(p + 24));
}

static void xxh_update(struct xxh64 *h, const void *data, size_t n)
{
    const unsigned char *p = data;
    h->total += n;
    if (h->tail_len) {
        size_t take = 32 - h->tail_len;
        if (take > n) take = n;
        memcpy(h->tail + h->tail_len, p, take);
        h->tail_len += take; p += take; n -= take;
        if (h->tail_len == 32) { xxh_block(h, h->tail); h->tail_len = 0; }
    }
    while (n >= 32) { xxh_block(h, p); p += 32; n -= 32; }
    if (n) { memcpy(h->tail, p, n); h->tail_len = n; }
}

static uint64_t xxh_digest(const struct xxh64 *h)
{
    uint64_t x;
    const unsigned char *p = h->tail, *end = p + h->tail_len;
    if (h->total >= 32) {
        x = rotl64(h->v1, 1) + rotl64(h->v2, 7) +
            rotl64(h->v3, 12) + rotl64(h->v4, 18);
        x ^= xxh_round(0, h->v1); x = x * XXH_P1 + XXH_P4;
        x ^= xxh_round(0, h->v2); x = x * XXH_P1 + XXH_P4;
        x ^= xxh_round(0, h->v3); x = x * XXH_P1 + XXH_P4;
        x ^= xxh_round(0, h->v4); x = x * XXH_P1 + XXH_P4;
    } else {
        x = XXH_P5;
    }
    x += h->total;
    while (p + 8 <= end) { x ^= xxh_round(0, get64(p)); x = rotl64(x, 27) * XXH_P1 + XXH_P4; p += 8; }
    if (p + 4 <= end) { x ^= (uint64_t)get32(p) * XXH_P1; x = rotl64(x, 23) * XXH_P2 + XXH_P3; p += 4; }
    while (p < end) { x ^= (uint64_t)*p++ * XXH_P5; x = rotl64(x, 11) * XXH_P1; }
    x ^= x >> 33; x *= XXH_P2; x ^= x >> 29; x *= XXH_P3; x ^= x >> 32;
    return x;
}

/* Streaming SHA-256 (FIPS 180-4). Whole blocks are compressed directly from
 * the caller's input buffer; only a partial block is copied into tail. The
 * eight ROUND calls per loop keep the dependency chain visible to gcc while
 * avoiding a branch for every round. */
struct sha256 {
    uint64_t total;
    uint32_t h[8];
    unsigned char tail[64];
    size_t tail_len;
};

static const uint32_t sha_k[64] = {
    0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
    0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
    0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
    0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
    0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
    0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
    0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
    0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2
};

#define ROR32(x,n) (((x) >> (n)) | ((x) << (32 - (n))))
#define SHA_ROUND(a,b,c,d,e,f,g,h,i) do { \
    uint32_t t1 = (h) + (ROR32((e),6) ^ ROR32((e),11) ^ ROR32((e),25)) + \
                  (((e) & (f)) ^ (~(e) & (g))) + sha_k[(i)] + w[(i)]; \
    uint32_t t2 = (ROR32((a),2) ^ ROR32((a),13) ^ ROR32((a),22)) + \
                  (((a) & (b)) ^ ((a) & (c)) ^ ((b) & (c))); \
    (d) += t1; (h) = t1 + t2; \
} while (0)

static uint32_t get32be(const unsigned char *p)
{
    return (uint32_t)p[0] << 24 | (uint32_t)p[1] << 16 |
           (uint32_t)p[2] << 8 | (uint32_t)p[3];
}

static void sha_block(struct sha256 *s, const unsigned char *p)
{
    uint32_t w[64], a = s->h[0], b = s->h[1], c = s->h[2], d = s->h[3];
    uint32_t e = s->h[4], f = s->h[5], g = s->h[6], h = s->h[7];
    for (int i = 0; i < 16; i++) w[i] = get32be(p + 4 * i);
    for (int i = 16; i < 64; i++) {
        uint32_t x = w[i - 15], y = w[i - 2];
        w[i] = w[i - 16] + (ROR32(x,7) ^ ROR32(x,18) ^ (x >> 3)) +
               w[i - 7] + (ROR32(y,17) ^ ROR32(y,19) ^ (y >> 10));
    }
    for (int i = 0; i < 64; i += 8) {
        SHA_ROUND(a,b,c,d,e,f,g,h,i);   SHA_ROUND(h,a,b,c,d,e,f,g,i+1);
        SHA_ROUND(g,h,a,b,c,d,e,f,i+2); SHA_ROUND(f,g,h,a,b,c,d,e,i+3);
        SHA_ROUND(e,f,g,h,a,b,c,d,i+4); SHA_ROUND(d,e,f,g,h,a,b,c,i+5);
        SHA_ROUND(c,d,e,f,g,h,a,b,i+6); SHA_ROUND(b,c,d,e,f,g,h,a,i+7);
    }
    s->h[0] += a; s->h[1] += b; s->h[2] += c; s->h[3] += d;
    s->h[4] += e; s->h[5] += f; s->h[6] += g; s->h[7] += h;
}

static void sha_init(struct sha256 *s)
{
    static const uint32_t initial[8] = {
        0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,
        0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19
    };
    memcpy(s->h, initial, sizeof initial);
    s->total = s->tail_len = 0;
}

static void sha_update(struct sha256 *s, const void *data, size_t n)
{
    const unsigned char *p = data;
    s->total += n;
    if (s->tail_len) {
        size_t take = 64 - s->tail_len;
        if (take > n) take = n;
        memcpy(s->tail + s->tail_len, p, take);
        s->tail_len += take; p += take; n -= take;
        if (s->tail_len == 64) { sha_block(s, s->tail); s->tail_len = 0; }
    }
    while (n >= 64) { sha_block(s, p); p += 64; n -= 64; }
    if (n) { memcpy(s->tail, p, n); s->tail_len = n; }
}

static void sha_digest(const struct sha256 *input, unsigned char out[32])
{
    struct sha256 s = *input;
    uint64_t bits = s.total * 8;
    s.tail[s.tail_len++] = 0x80;
    if (s.tail_len > 56) {
        memset(s.tail + s.tail_len, 0, 64 - s.tail_len);
        sha_block(&s, s.tail);
        s.tail_len = 0;
    }
    memset(s.tail + s.tail_len, 0, 56 - s.tail_len);
    for (int i = 0; i < 8; i++) s.tail[63 - i] = (unsigned char)(bits >> (8 * i));
    sha_block(&s, s.tail);
    for (int i = 0; i < 8; i++) {
        out[4*i] = (unsigned char)(s.h[i] >> 24); out[4*i+1] = (unsigned char)(s.h[i] >> 16);
        out[4*i+2] = (unsigned char)(s.h[i] >> 8); out[4*i+3] = (unsigned char)s.h[i];
    }
}

static void sha_hex(const struct sha256 *s, char line[65])
{
    static const char hex[] = "0123456789abcdef";
    unsigned char digest[32];
    sha_digest(s, digest);
    for (int i = 0; i < 32; i++) { line[2*i] = hex[digest[i] >> 4]; line[2*i+1] = hex[digest[i] & 15]; }
    line[64] = '\0';
}

/* The SHA-256 engine (#1596). On a 2-core cloner the hash was ~93% of
 * fanout's CPU (Testrunner, 17/10: -S 7.0s against 0.64s without, per 2GB),
 * and every cycle it does not take goes to zstd and partclone. The kernel's
 * sha256-ssse3/avx (AF_ALG) hashed 346MB/s against 258MB/s for the code above
 * on the same core (17/10), so it is used when present. Same algorithm, same
 * digest -- the tests compare both engines with hashlib, byte for byte -- and
 * which one ran is said on stderr. FANOUT_SHA_ENGINE=builtin forces the code
 * above; =kernel refuses to fall back (so a test can prove the kernel path is
 * the one exercised). A kernel failure mid-stream cannot fall back, because
 * the kernel holds the state: it ends with no digest at all -- "not
 * computed", never a wrong one (principle 5, #73). */
struct sha_engine {
    int kfd;            /* AF_ALG op socket, or -1 = the code above */
    int failed;
    struct sha256 c;
};

static int sha_engine_open(struct sha_engine *e)
{
    const char *want = getenv("FANOUT_SHA_ENGINE");
    struct sockaddr_alg sa;
    int s, saved;
    e->kfd = -1;
    e->failed = 0;
    sha_init(&e->c);
    if (want && strcmp(want, "builtin") == 0) {
        fprintf(stderr, "fanout: sha256 in-process (FANOUT_SHA_ENGINE=builtin)\n");
        return 0;
    }
    if (want && strcmp(want, "kernel") != 0 && strcmp(want, "auto") != 0) {
        fprintf(stderr, "fanout: unknown FANOUT_SHA_ENGINE=%s (builtin|kernel|auto)\n", want);
        return -1;                        /* a typo is not permission to guess */
    }
    memset(&sa, 0, sizeof sa);
    sa.salg_family = AF_ALG;
    memcpy(sa.salg_type, "hash", 5);
    memcpy(sa.salg_name, "sha256", 7);
    s = socket(AF_ALG, SOCK_SEQPACKET | SOCK_CLOEXEC, 0);
    if (s >= 0 && bind(s, (struct sockaddr *)&sa, sizeof sa) == 0)
        e->kfd = accept4(s, NULL, NULL, SOCK_CLOEXEC);
    saved = errno;
    if (s >= 0)
        close(s);
    if (e->kfd >= 0) {
        fprintf(stderr, "fanout: sha256 by the kernel (AF_ALG)\n");
        return 0;
    }
    if (want && strcmp(want, "kernel") == 0) {
        fprintf(stderr, "fanout: FANOUT_SHA_ENGINE=kernel, but AF_ALG sha256 is unavailable: %s\n",
                strerror(saved));
        return -1;
    }
    fprintf(stderr, "fanout: sha256 in-process (kernel AF_ALG unavailable: %s)\n",
            strerror(saved));
    return 0;
}

static void sha_engine_update(struct sha_engine *e, const char *p, size_t n)
{
    if (e->kfd < 0) {
        sha_update(&e->c, p, n);
        return;
    }
    while (n > 0 && !e->failed) {
        ssize_t w = send(e->kfd, p, n, MSG_MORE);
        if (w > 0) {
            p += w;
            n -= (size_t)w;
        } else if (!(w < 0 && errno == EINTR)) {
            fprintf(stderr, "fanout: kernel sha256 failed mid-stream: %s -- no digest\n",
                    w < 0 ? strerror(errno) : "it took no bytes");
            e->failed = 1;
        }
    }
}

/* 0 and the 64-digit digest, or -1: there is no digest to give. */
static int sha_engine_hex(struct sha_engine *e, char line[65])
{
    static const char hex[] = "0123456789abcdef";
    unsigned char d[32];
    ssize_t got;
    if (e->kfd < 0) {
        sha_hex(&e->c, line);
        return 0;
    }
    if (e->failed || send(e->kfd, NULL, 0, 0) < 0)
        return -1;
    do got = read(e->kfd, d, sizeof d); while (got < 0 && errno == EINTR);
    if (got != (ssize_t)sizeof d) {
        fprintf(stderr, "fanout: kernel sha256 gave %zd of 32 digest bytes -- no digest\n", got);
        return -1;
    }
    for (int i = 0; i < 32; i++) { line[2*i] = hex[d[i] >> 4]; line[2*i+1] = hex[d[i] & 15]; }
    line[64] = '\0';
    return 0;
}

static void widen_pipe(int fd, const char *name)
{
    struct stat st;
    if (fstat(fd, &st) != 0 || !S_ISFIFO(st.st_mode) ||
        fcntl(fd, F_SETPIPE_SZ, (int)pipe_bytes) >= 0)
        return;
    /* Refused (#1738, Testrunner 18/10): not root, and above fs.pipe-max-size
     * (1MB by default). The pipe would stay at the kernel's 64KB; it takes the
     * largest an unprivileged process may have instead -- and says so. The
     * agent is root on a cloner, where this never happens. */
    int saved = errno;
    long long ceiling = 0;
    FILE *f = saved == EPERM ? fopen("/proc/sys/fs/pipe-max-size", "r") : NULL;
    if (f) {
        if (fscanf(f, "%lld", &ceiling) != 1)
            ceiling = 0;
        fclose(f);
    }
    if (ceiling > 0 && ceiling < pipe_bytes && fcntl(fd, F_SETPIPE_SZ, (int)ceiling) >= 0) {
        fprintf(stderr, "fanout: pipe %s: %lld refused (%s), set to %lld (fs.pipe-max-size)\n",
                name, pipe_bytes, strerror(saved), ceiling);
        return;
    }
    fprintf(stderr, "fanout: could not enlarge pipe %s: %s\n", name, strerror(saved));
}

static int append_count(int fd, long long count, const char *name)
{
    char line[32];
    int n = snprintf(line, sizeof line, "%lld\n", count);
    if (n <= 0 || write(fd, line, (size_t)n) != n) {
        fprintf(stderr, "fanout: progress counter for %s stopped: %s\n", name, strerror(errno));
        return -1;
    }
    return 0;
}

static int hash_only(int use_sha256)
{
    unsigned char buf[READ_CHUNK];
    struct xxh64 h;
    struct sha_engine s;
    xxh_init(&h);
    if (use_sha256 && sha_engine_open(&s) != 0)
        return 2;
    widen_pipe(STDIN_FILENO, "stdin");
    for (;;) {
        ssize_t n = read(STDIN_FILENO, buf, sizeof buf);
        if (n > 0) {
            if (use_sha256) sha_engine_update(&s, (const char *)buf, (size_t)n);
            else xxh_update(&h, buf, (size_t)n);
            continue;
        }
        if (n == 0) break;
        if (errno == EINTR) continue;
        fprintf(stderr, "fanout: read error: %s\n", strerror(errno));
        return 2;
    }
    char sha_line[65];
    if (use_sha256 && sha_engine_hex(&s, sha_line) != 0)
        return 2;
    if ((use_sha256 ? printf("%s\n", sha_line) :
         printf("%016llx\n", (unsigned long long)xxh_digest(&h))) < 0 || fflush(stdout) != 0)
        return 2;
    return 0;
}

/* --- #1805: read-back verification -------------------------------------------
 *
 * `fanout --readback <device>` answers "what is on the disk now" for a
 * partition this machine has just written. Until #1805 a drawer was `done` on
 * evidence from the STREAM -- the sha256 of the compressed bytes before zstd
 * and partclone, partclone's exit code -- and nothing ever read the disk:
 * RAM without ECC, a zstd/partclone bug, or SSD firmware that acknowledges a
 * write it never made all ended `done` (CLR-01).
 *
 * The evidence is the SHA-256 of the partition's used blocks, in block order,
 * read back from the device with O_DIRECT -- the page cache that partclone
 * just wrote through is not a disk, and a read that it answers proves nothing.
 * The server takes the same digest of the image when it enters the library
 * (server/readbackhash.py): the data section of the partclone image with its
 * header, bitmap and checksums stripped. The two meet only if every used
 * block on the disk holds the image's bytes.
 *
 * Which blocks are used comes from partclone itself: stdin is
 * `partclone.<fs> -c -s <device>`, of which only the 110-byte image_desc_v2
 * header and the bitmap (+ its CRC-32) are read; stdin is then closed and
 * partclone ends on EPIPE. That map is derived from the WRITTEN filesystem's
 * own metadata, so a corrupted bitmap or superblock is a different map, a
 * different set of blocks and a different digest -- never a pass.
 *
 * `fanout --readback <device> <raw-bytes>`: a partition partclone.dd copied
 * whole (no header, no map). The first <raw-bytes> of the device are hashed.
 *
 * Output: "<64 hex> <bytes hashed>\n", exit 0.
 * Exit 3: the map on stdin is not a whole, self-consistent partclone v2
 *         header + bitmap (magic, version, endianness, header CRC, bitmap CRC,
 *         used count) -- the disk was not judged.
 * Exit 4: the device could not be read uncached (O_DIRECT refused, a read
 *         error, the device ended early) -- the disk was not judged either,
 *         and the agent fails the drawer on it all the same (principle 5).
 * Exit 2: usage, memory, or the hash engine gave no digest.
 *
 * FANOUT_READBACK_CACHED=1 lets a TEST whose temporary directory refuses
 * O_DIRECT (tmpfs) read through the cache, and says so on stderr. The agent
 * never sets it (tests/test_readback_1805.py checks that). */
#define RB_HEAD 110
#define RB_WINDOW (8 * 1024 * 1024)   /* data bytes per read */
#define RB_GAP (1024 * 1024)          /* free space read through rather than skipped */

static uint32_t rb_crc_table[256];

/* partclone's CRC-32: reflected 0xEDB88320, seed 0xFFFFFFFF, no final XOR
 * (server/usedbackfill.py; = zlib.crc32(x) ^ 0xFFFFFFFF). */
static uint32_t rb_crc32(const unsigned char *p, size_t n)
{
    uint32_t crc = 0xFFFFFFFFu;
    if (!rb_crc_table[1])
        for (uint32_t i = 0; i < 256; i++) {
            uint32_t c = i;
            for (int k = 0; k < 8; k++)
                c = (c & 1) ? 0xEDB88320u ^ (c >> 1) : c >> 1;
            rb_crc_table[i] = c;
        }
    while (n--)
        crc = rb_crc_table[(crc ^ *p++) & 0xFFu] ^ (crc >> 8);
    return crc;
}

static uint64_t rb_le(const unsigned char *p, int n)
{
    uint64_t v = 0;
    while (n-- > 0)
        v = v << 8 | p[n];
    return v;
}

static int rb_read_all(int fd, unsigned char *p, size_t n)
{
    while (n > 0) {
        ssize_t r = read(fd, p, n);
        if (r > 0) {
            p += r;
            n -= (size_t)r;
        } else if (!(r < 0 && errno == EINTR)) {
            return -1;               /* EOF or an error: it did not arrive whole */
        }
    }
    return 0;
}

static int rb_bit(const unsigned char *bm, uint64_t i)
{
    return (bm[i >> 3] >> (i & 7)) & 1;
}

/* The first used block at or after i, or `limit` when there is none before it. */
static uint64_t rb_next(const unsigned char *bm, uint64_t i, uint64_t limit)
{
    while (i < limit) {
        if ((i & 7) == 0 && bm[i >> 3] == 0) {
            i += 8;
            continue;
        }
        if (rb_bit(bm, i))
            return i;
        i++;
    }
    return limit;
}

/* Reads [off, off + len) of the device into buf from an O_DIRECT-aligned start.
 * Returns how far into buf the first wanted byte is, or -1 (said on stderr). */
static long long rb_pread(int fd, unsigned char *buf, uint64_t off, uint64_t len,
                          uint64_t align, const char *dev)
{
    uint64_t a0 = off & ~(align - 1);
    uint64_t want = (off - a0) + len;
    uint64_t span = (want + align - 1) & ~(align - 1);
    uint64_t got = 0;
    while (got < span) {
        ssize_t r = pread(fd, buf + got, (size_t)(span - got), (off_t)(a0 + got));
        if (r > 0) {
            got += (uint64_t)r;
        } else if (r == 0) {
            break;                   /* the end of the device (or of a test's file) */
        } else if (errno != EINTR) {
            fprintf(stderr, "fanout: readback: read error on %s at byte %llu: %s\n",
                    dev, (unsigned long long)(a0 + got), strerror(errno));
            return -1;
        }
    }
    if (got < want) {
        fprintf(stderr, "fanout: readback: %s ended at byte %llu, before byte %llu\n",
                dev, (unsigned long long)(a0 + got), (unsigned long long)(off + len));
        return -1;
    }
    return (long long)(off - a0);
}

/* Nadav, 20/10 (#1864, measured on cloner 2): sha256 of every used block cost
 * ~8 minutes a round of 4 drawers (176GB at ~390MB/s, the i5-650's two cores).
 * `-x` is the decided form: the used-data stream (the used blocks in order)
 * cut into RB_CHUNK pieces, XXH64 of each -- "c <index> <16 hex>" a line, and
 * "n <chunks> <bytes>" last. The server keeps the same list per partition
 * (server/readbackhash.py), and the agent compares line by line.
 * `-s <seed> <permille>`: only a sample of the chunks -- the first, the middle,
 * the last, and the rest drawn by splitmix64(seed) until permille/1000 of them
 * -- and only their blocks are read. The same seed draws the same chunks. */
#define RB_CHUNK (4ULL << 20)

struct rb_chunks {
    int on;                 /* -x */
    unsigned char *pick;    /* NULL = every chunk */
    uint64_t n, cur, in, picked, want;
    struct xxh64 h;
};

static int rb_selected(const struct rb_chunks *c, uint64_t k)
{
    return !c->pick || ((c->pick[k >> 3] >> (k & 7)) & 1);
}

static void rb_chunk_end(struct rb_chunks *c)
{
    if (rb_selected(c, c->cur)) {
        printf("c %llu %016llx\n", (unsigned long long)c->cur, (unsigned long long)xxh_digest(&c->h));
        c->picked++;
    }
    c->cur++;
    c->in = 0;
    xxh_init(&c->h);
}

/* p == NULL: bytes of chunks nobody asked for -- counted, not read, not hashed. */
static void rb_feed(struct rb_chunks *c, const unsigned char *p, uint64_t n)
{
    while (n > 0) {
        uint64_t take = RB_CHUNK - c->in;
        if (take > n)
            take = n;
        if (p && rb_selected(c, c->cur))
            xxh_update(&c->h, p, (size_t)take);
        if (p)
            p += take;
        c->in += take;
        n -= take;
        if (c->in == RB_CHUNK)
            rb_chunk_end(c);
    }
}

static int rb_pick(struct rb_chunks *c, uint64_t seed, uint64_t permille)
{
    uint64_t want, have = 0, x = seed, fixed[3];
    if (c->n == 0)
        return 0;
    c->pick = calloc((size_t)(c->n / 8 + 1), 1);
    if (!c->pick)
        return -1;
    want = (c->n * permille + 999) / 1000;
    if (want < 3)
        want = 3;
    if (want > c->n)
        want = c->n;
    fixed[0] = 0;
    fixed[1] = c->n / 2;
    fixed[2] = c->n - 1;
    for (int i = 0; i < 3; i++)
        if (!((c->pick[fixed[i] >> 3] >> (fixed[i] & 7)) & 1)) {
            c->pick[fixed[i] >> 3] |= (unsigned char)(1u << (fixed[i] & 7));
            have++;
        }
    c->want = want;
    while (have < want) {               /* splitmix64: the same seed, the same chunks */
        uint64_t z = (x += UINT64_C(0x9E3779B97F4A7C15));
        z = (z ^ (z >> 30)) * UINT64_C(0xBF58476D1CE4E5B9);
        z = (z ^ (z >> 27)) * UINT64_C(0x94D049BB133111EB);
        z ^= z >> 31;
        uint64_t k = z % c->n;
        if (!((c->pick[k >> 3] >> (k & 7)) & 1)) {
            c->pick[k >> 3] |= (unsigned char)(1u << (k & 7));
            have++;
        }
    }
    return 0;
}

static int readback(int argc, char **argv)
{
    const char *dev;
    unsigned char head[RB_HEAD], *bm = NULL, *buf = NULL, *out = NULL;
    uint64_t total = 0, used = 0, bs = 0, raw = 0, hashed = 0, win = RB_WINDOW, align = 4096;
    uint64_t seed = 0, permille = 0;
    struct sha_engine sha = { .kfd = -1 };
    struct rb_chunks ck;
    void *mem = NULL;
    char line[65];
    int fd, lss = 0, rc = 2, ai = 2, sample = 0;

    memset(&ck, 0, sizeof ck);
    xxh_init(&ck.h);
    if (ai < argc && strcmp(argv[ai], "-x") == 0) {
        ck.on = 1;
        ai++;
        if (ai + 2 < argc && strcmp(argv[ai], "-s") == 0) {
            char *e1, *e2;
            errno = 0;
            seed = strtoull(argv[ai + 1], &e1, 10);
            permille = strtoull(argv[ai + 2], &e2, 10);
            if (errno || *e1 || *e2 || e1 == argv[ai + 1] || e2 == argv[ai + 2] ||
                permille < 1 || permille > 1000) {
                fprintf(stderr, "fanout: readback: bad sample '%s %s'\n", argv[ai + 1], argv[ai + 2]);
                return 2;
            }
            sample = 1;
            ai += 3;
        }
    }
    if (argc - ai != 1 && argc - ai != 2) {
        fprintf(stderr, "usage: fanout --readback [-x [-s <seed> <permille>]] <device> [<raw-bytes>]\n");
        return 2;
    }
    dev = argv[ai];
    if (argc - ai == 2) {
        char *end;
        errno = 0;
        unsigned long long v = strtoull(argv[ai + 1], &end, 10);
        if (errno || end == argv[ai + 1] || *end || v == 0) {
            fprintf(stderr, "fanout: readback: bad byte count '%s'\n", argv[ai + 1]);
            return 2;
        }
        raw = v;
    } else {
        if (rb_read_all(STDIN_FILENO, head, RB_HEAD) != 0) {
            fprintf(stderr, "fanout: readback: the partclone header did not arrive whole\n");
            return 3;
        }
        if (memcmp(head, "partclone-image", 15) != 0 || memcmp(head + 30, "0002", 4) != 0 ||
            rb_le(head + 34, 2) != 0xC0DE || rb_le(head + 92, 2) != 2 || head[105] != 1) {
            fprintf(stderr, "fanout: readback: not a partclone v2 image with a bit map\n");
            return 3;
        }
        if (rb_crc32(head, RB_HEAD - 4) != (uint32_t)rb_le(head + RB_HEAD - 4, 4)) {
            fprintf(stderr, "fanout: readback: the partclone header failed its CRC\n");
            return 3;
        }
        total = rb_le(head + 60, 8);
        used = rb_le(head + 76, 8);
        bs = rb_le(head + 84, 4);
        if (bs == 0 || bs > (64u << 20) || used > total || total == 0 ||
            (total + 7) / 8 > (1ULL << 31)) {
            fprintf(stderr, "fanout: readback: implausible map (%llu of %llu blocks of %llu)\n",
                    (unsigned long long)used, (unsigned long long)total, (unsigned long long)bs);
            return 3;
        }
        size_t bm_len = (size_t)((total + 7) / 8);
        unsigned char crc[4];
        bm = malloc(bm_len);
        if (!bm) {
            fprintf(stderr, "fanout: readback: no memory for a %zu-byte map\n", bm_len);
            return 2;
        }
        if (rb_read_all(STDIN_FILENO, bm, bm_len) != 0 || rb_read_all(STDIN_FILENO, crc, 4) != 0) {
            fprintf(stderr, "fanout: readback: the bit map did not arrive whole\n");
            rc = 3;
            goto done;
        }
        close(STDIN_FILENO);         /* partclone is done for: EPIPE on its next write */
        if (rb_crc32(bm, bm_len) != (uint32_t)rb_le(crc, 4)) {
            fprintf(stderr, "fanout: readback: the bit map failed its CRC\n");
            rc = 3;
            goto done;
        }
        uint64_t seen = 0;
        for (size_t j = 0; j < (size_t)(total >> 3); j++)
            seen += (uint64_t)__builtin_popcount(bm[j]);
        if (total & 7)
            seen += (uint64_t)__builtin_popcount(bm[total >> 3] & ((1u << (total & 7)) - 1));
        if (seen != used) {
            fprintf(stderr, "fanout: readback: the map marks %llu blocks, its header %llu\n",
                    (unsigned long long)seen, (unsigned long long)used);
            rc = 3;
            goto done;
        }
        if (bs > win)
            win = bs;
    }
    if (ck.on) {
        uint64_t bytes = raw ? raw : used * bs;
        ck.n = (bytes + RB_CHUNK - 1) / RB_CHUNK;
        if (sample && rb_pick(&ck, seed, permille) != 0) {
            fprintf(stderr, "fanout: readback: no memory for the sample\n");
            goto done;
        }
    }

    fd = open(dev, O_RDONLY | O_DIRECT | O_CLOEXEC);
    if (fd < 0 && errno == EINVAL && getenv("FANOUT_READBACK_CACHED") &&
        strcmp(getenv("FANOUT_READBACK_CACHED"), "1") == 0) {
        fd = open(dev, O_RDONLY | O_CLOEXEC);
        fprintf(stderr, "fanout: readback: %s read THROUGH THE CACHE (FANOUT_READBACK_CACHED=1)"
                " -- a test's evidence, not a disk's\n", dev);
    }
    if (fd < 0) {
        fprintf(stderr, "fanout: readback: cannot open %s uncached: %s\n", dev, strerror(errno));
        rc = 4;
        goto done;
    }
    if (ioctl(fd, BLKSSZGET, &lss) == 0 && lss >= 512 && lss <= 65536 && (lss & (lss - 1)) == 0)
        align = (uint64_t)lss;
    if (posix_memalign(&mem, 4096, (size_t)(win + 2 * align)) == 0)
        buf = mem;
    if (!buf || (!raw && !(out = malloc((size_t)win)))) {
        fprintf(stderr, "fanout: readback: no memory for the read buffers\n");
        close(fd);
        goto done;
    }
    if (!ck.on && sha_engine_open(&sha) != 0) {
        close(fd);
        goto done;
    }

    if (raw) {
        uint64_t step = ck.on ? RB_CHUNK : win;   /* -x: a read never crosses a chunk */
        for (uint64_t off = 0; off < raw; off += step) {
            uint64_t len = raw - off < step ? raw - off : step;
            if (ck.on && !rb_selected(&ck, off / RB_CHUNK)) {
                rb_feed(&ck, NULL, len);
                hashed += len;
                continue;
            }
            long long at = rb_pread(fd, buf, off, len, align, dev);
            if (at < 0) {
                rc = 4;
                break;
            }
            if (ck.on)
                rb_feed(&ck, buf + at, len);
            else
                sha_engine_update(&sha, (const char *)buf + at, (size_t)len);
            hashed += len;
        }
    } else {
        /* -x: windows never cross a chunk of the used-data stream, so a chunk
         * nobody asked for is never read (cb = used blocks a chunk). */
        uint64_t maxblk = win / bs, b = rb_next(bm, 0, total), ord = 0;
        uint64_t cb = (ck.on && RB_CHUNK % bs == 0) ? RB_CHUNK / bs : 0;
        while (b < total) {
            uint64_t s = b, e = b, wo = 0;
            /* A window: used blocks from s, free gaps under RB_GAP read through
             * (one read instead of a seek each), at most maxblk blocks. */
            while (e < total && e - s < maxblk) {
                if (cb && wo && (ord + wo) % cb == 0)
                    break;
                if (rb_bit(bm, e)) {
                    e++;
                    wo++;
                    continue;
                }
                uint64_t n = rb_next(bm, e, total);
                if (n >= total || (n - e) * bs > RB_GAP || n - s >= maxblk)
                    break;
                e = n;
            }
            if (cb && ck.pick && !rb_selected(&ck, ord / cb)) {
                rb_feed(&ck, NULL, wo * bs);     /* a chunk nobody asked for: not read */
                hashed += wo;
                ord += wo;
                b = rb_next(bm, e, total);
                continue;
            }
            long long at = rb_pread(fd, buf, s * bs, (e - s) * bs, align, dev);
            if (at < 0) {
                rc = 4;
                break;
            }
            if (ck.on) {                         /* the used runs, in order, into the chunks */
                for (uint64_t x = s; x < e; ) {
                    if (!rb_bit(bm, x)) {
                        x = rb_next(bm, x, e);
                        continue;
                    }
                    uint64_t y = x;
                    while (y < e && rb_bit(bm, y))
                        y++;
                    rb_feed(&ck, buf + at + (x - s) * bs, (y - x) * bs);
                    hashed += y - x;
                    x = y;
                }
                ord += wo;
                b = rb_next(bm, e, total);
                continue;
            }
            uint64_t r = s;
            while (r < e && rb_bit(bm, r))
                r++;
            if (r == e) {            /* no gap in the window: hash it where it lies */
                sha_engine_update(&sha, (const char *)buf + at, (size_t)((e - s) * bs));
                hashed += e - s;
            } else {                 /* the used runs, side by side, one hash call */
                size_t o = 0;
                for (uint64_t x = s; x < e; ) {
                    if (!rb_bit(bm, x)) {
                        x = rb_next(bm, x, e);
                        continue;
                    }
                    uint64_t y = x;
                    while (y < e && rb_bit(bm, y))
                        y++;
                    memcpy(out + o, buf + at + (x - s) * bs, (size_t)((y - x) * bs));
                    o += (size_t)((y - x) * bs);
                    hashed += y - x;
                    x = y;
                }
                sha_engine_update(&sha, (const char *)out, o);
            }
            ord += wo;
            b = rb_next(bm, e, total);
        }
        if (rc != 4 && hashed != used) {
            fprintf(stderr, "fanout: readback: hashed %llu blocks of %llu\n",
                    (unsigned long long)hashed, (unsigned long long)used);
            rc = 3;
        }
        hashed *= bs;
    }
    close(fd);
    if (rc == 2 && ck.on) {
        if (ck.in > 0)
            rb_chunk_end(&ck);
        if (ck.cur != ck.n) {
            fprintf(stderr, "fanout: readback: %llu chunks of %llu\n",
                    (unsigned long long)ck.cur, (unsigned long long)ck.n);
            rc = 3;
        } else if (ck.pick && ck.picked != ck.want) {
            /* A sample that hashed fewer chunks than it drew read nothing it can
             * vouch for: no list at all, never "sampled" (principle 5). */
            fprintf(stderr, "fanout: readback: the sample hashed %llu of the %llu chunks it drew\n",
                    (unsigned long long)ck.picked, (unsigned long long)ck.want);
            rc = 3;
        } else if (printf("n %llu %llu\n", (unsigned long long)ck.n, (unsigned long long)hashed) < 0 ||
                   fflush(stdout) != 0) {
            fprintf(stderr, "fanout: readback: could not write the list\n");
        } else {
            rc = 0;
        }
    } else if (rc == 2) {
        if (sha_engine_hex(&sha, line) != 0) {
            fprintf(stderr, "fanout: readback: no digest\n");
        } else if (printf("%s %llu\n", line, (unsigned long long)hashed) < 0 || fflush(stdout) != 0) {
            fprintf(stderr, "fanout: readback: could not write the digest\n");
        } else {
            rc = 0;
        }
    }
done:
    free(bm);
    free(buf);
    free(out);
    free(ck.pick);
    return rc;
}

/* Opening a fifo for writing fails with ENXIO until a reader arrives, and
 * the per-drawer pipelines are started moments earlier by the shell. Rather
 * than depend on that race, wait briefly for the reader to show up -- but
 * only briefly, and then say so: "no reader" and "no such path" are two
 * different faults for whoever reads the log, and running out of patience
 * has to be reported as running out of patience. */
static int open_output(const char *path)
{
    struct timespec pause = { 0, 20 * 1000 * 1000 };   /* 20ms */
    for (int waited = 0; waited < OPEN_RETRY_MS; waited += 20) {
        int fd = open(path, O_WRONLY | O_NONBLOCK);
        if (fd >= 0)
            return fd;
        if (errno != ENXIO)
            return -1;
        nanosleep(&pause, NULL);
    }
    return OPEN_NO_READER;
}

static void fail_target(struct target *t, const char *reason)
{
    if (!t->alive)
        return;
    t->alive = 0;
    t->reason = reason;
    if (t->fd >= 0) {
        close(t->fd);
        t->fd = -1;
    }
    free(t->buf);
    t->buf = NULL;
}

/* Queue a block for one target. make_room() has already guaranteed the room,
 * so a target that still does not fit is an internal invariant break, not a
 * slow drive -- said plainly rather than blamed on the disk (#660). */
static int enqueue(struct target *t, const char *data, size_t n)
{
    if (!t->alive)
        return 0;
    if (t->len + n > t->cap) {
        fail_target(t, "internal error (queue capacity invariant)");
        return -1;
    }
    size_t tail = (t->head + t->len) % t->cap;
    size_t first = t->cap - tail;
    if (first > n)
        first = n;
    memcpy(t->buf + tail, data, first);
    if (n > first)
        memcpy(t->buf, data + first, n - first);
    t->len += n;
    return 0;
}

/* Push as much as the pipe will take right now, without blocking. */
static void flush_target(struct target *t)
{
    while (t->alive && t->len > 0) {
        size_t run = t->cap - t->head;
        if (run > t->len)
            run = t->len;
        ssize_t written = write(t->fd, t->buf + t->head, run);
        if (written > 0) {
            t->head = (t->head + (size_t)written) % t->cap;
            t->len -= (size_t)written;
            t->taken += written;
            t->last_progress_ms = now_ms();
            continue;
        }
        if (written < 0 && (errno == EAGAIN || errno == EWOULDBLOCK))
            return;                       /* full for now; try again later */
        if (written < 0 && errno == EINTR)
            continue;
        fail_target(t, "write error");
        return;
    }
}

/* Hand one read chunk to a target (18/10, local clone toward the disk
 * ceiling). With nothing queued, the chunk is written straight from the read
 * buffer, and only what the pipe does not take right now is copied into the
 * ring, behind which everything else then waits, in order. Before this every
 * byte was first copied into the ring: a 256MB ring the copies walk through
 * end to end, so each one landed in memory no cache held -- on the cloner's
 * i5-650, one cache-cold memcpy per byte per fanout (two per local clone
 * target: the source fanout and the target's relay). The ring is unchanged
 * for a drawer that falls behind, and so is every failure rule. */
static void send_chunk(struct target *t, const char *data, size_t n)
{
    while (t->alive && t->len == 0 && n > 0) {
        ssize_t written = write(t->fd, data, n);
        if (written > 0) {
            data += written;
            n -= (size_t)written;
            t->taken += written;
            t->last_progress_ms = now_ms();
            continue;
        }
        if (written < 0 && (errno == EAGAIN || errno == EWOULDBLOCK))
            break;                        /* full for now; the rest queues */
        if (written < 0 && errno == EINTR)
            continue;
        fail_target(t, "write error");
        return;
    }
    if (!t->alive || n == 0)
        return;
    t->queued += (long long)n;
    if (enqueue(t, data, n) == 0)
        flush_target(t);
}

static int alive_count(void)
{
    int n = 0;
    for (int i = 0; i < target_count; i++)
        if (targets[i].alive)
            n++;
    return n;
}

/* Wait (briefly) until at least one queued target can accept more bytes. */
static void wait_writable(int timeout_ms)
{
    struct pollfd fds[MAX_TARGETS];
    int n = 0;
    for (int i = 0; i < target_count; i++) {
        if (targets[i].alive && targets[i].len > 0) {
            fds[n].fd = targets[i].fd;
            fds[n].events = POLLOUT;
            fds[n].revents = 0;
            n++;
        }
    }
    if (n > 0)
        poll(fds, (nfds_t)n, timeout_ms);
}

static long long now_ms(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (long long)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

/* Positive integer from the environment, or fallback. Junk, zero, and
 * overflow (strtoll saturates to LLONG_MAX, ERANGE) are refused out loud
 * and the fallback kept -- never taken on (principle 5, #674, #427). */
static long long env_ll(const char *name, long long fallback)
{
    const char *s = getenv(name);
    char *end;
    long long v;
    if (!s)
        return fallback;
    errno = 0;
    v = strtoll(s, &end, 10);
    if (end != s && *end == '\0' && v > 0 && errno != ERANGE)
        return v;
    fprintf(stderr, "fanout: ignoring invalid %s\n", name);
    return fallback;
}

/* Open "<path>.bytes" for the running count of one target. Truncated on
 * open, so every partition starts from zero -- the shell folds the finished
 * partition into the target's base before the next one begins. */
static int open_counter(const char *path)
{
    size_t size = strlen(path) + sizeof(COUNTER_SUFFIX);
    char *counter = malloc(size);
    int fd;
    if (!counter)
        return -1;
    snprintf(counter, size, "%s%s", path, COUNTER_SUFFIX);
    /* codeql[cpp/path-injection] false positive: argv comes from the agent that runs fanout as root with fifos it created itself (#291) */
    fd = open(counter, O_WRONLY | O_CREAT | O_TRUNC | O_APPEND, 0644);
    if (fd < 0)
        fprintf(stderr, "fanout: no progress counter for %s: %s\n",
                counter, strerror(errno));
    free(counter);
    return fd;
}

/* -U (drawerfeed.sh, one decoder for the whole machine): this fanout sits
 * after zstd, so what it hands a drawer is decompressed -- but the drawer's
 * counter is read against the image's *compressed* total, by the console bar
 * and by the server's drive rates (#25, drive_rates.py). The fanout before
 * zstd counts the compressed bytes it handed the decoder ("<mid>.bytes"); a
 * drawer that took a share of the decompressed stream has written the same
 * share of those. A drawer that took everything shows exactly that counter,
 * whose final value the upstream fanout wrote before it closed the decoder's
 * input -- so at EOF the number is exact, not an estimate.
 * The counter's last whole number, or -1: not read is not zero (principle 5). */
static long long units_now(void)
{
    char tail[64];
    long long v = -1, cur = -1;
    struct stat st;
    /* codeql[cpp/path-injection] false positive: same as open_counter (#291) */
    int fd = open(units_path, O_RDONLY);
    if (fd < 0)
        return -1;
    if (fstat(fd, &st) == 0 && st.st_size > 0) {
        off_t at = st.st_size > (off_t)sizeof tail ? st.st_size - (off_t)sizeof tail : 0;
        ssize_t n = pread(fd, tail, sizeof tail, at);
        for (ssize_t i = 0; i < n; i++) {
            if (tail[i] >= '0' && tail[i] <= '9')
                cur = (cur < 0 ? 0 : cur * 10) + (tail[i] - '0');
            else {
                if (tail[i] == '\n' && cur >= 0)
                    v = cur;              /* only a line that was finished counts */
                cur = -1;
            }
        }
    }
    close(fd);
    return v;
}

/* A drawer's share of `units`. Frozen once the drawer fails (its share of a
 * stream it no longer gets is not progress), and never lower than before:
 * a chunk queued but not yet written dips the ratio for a moment. */
static long long scaled_count(struct target *t, long long units)
{
    if (t->alive) {
        long long s = t->taken >= stdin_total ? units :
            (long long)((double)units * (double)t->taken / (double)stdin_total);
        if (s > t->shown)
            t->shown = s;
    }
    return t->shown;
}

/* Append each target's count. A counter that cannot be written is closed and
 * said so once: the drawer itself is fine, and a drive is never failed over
 * a progress bar -- but the silence is not left unexplained either. */
static void write_counters(int force)
{
    static long long last_written;
    long long now = now_ms(), units = -1;
    if (!force && now - last_written < COUNTER_MS)
        return;
    last_written = now;
    if (units_path && (units = units_now()) < 0)
        return;                           /* nothing in the right units yet: write nothing */
    for (int i = 0; i < target_count; i++) {
        struct target *t = &targets[i];
        char line[32];
        int n;
        if (t->cfd < 0)
            continue;
        n = snprintf(line, sizeof line, "%lld\n", units_path ? scaled_count(t, units) : t->taken);
        if (n > 0 && write(t->cfd, line, (size_t)n) != n) {
            fprintf(stderr, "fanout: progress counter for %s stopped: %s\n",
                    t->path, strerror(errno));
            close(t->cfd);
            t->cfd = -1;
        }
    }
}

/* Block the reader until every live target has room for the next n bytes.
 *
 * This is the whole back-pressure policy (#660): the stream advances only as
 * fast as the slowest live drawer that is still making progress, the way tee
 * paces to its slowest output -- but per-drawer and non-blocking, so a drawer
 * that has already write-errored is out and never waited on. Called with
 * n == cap at EOF, it drains every live ring to empty before the counts are
 * finalised.
 *
 * A drawer that is full AND has taken no bytes for stall_evict_ms is a hung
 * consumer, not a slow one, and is failed here so its siblings are not held
 * behind it forever (#674). Any accepted write in flush_target() refreshes
 * last_progress_ms, so a slow-but-draining drawer never trips this.
 *
 * While `reading` (not the EOF drain), the time stdin is held is a ceiling of
 * its own (#1796, see BLOCK_EVICT_MS above): a drawer still full after
 * block_evict_ms, while a sibling has room, is the one holding the stream. */
static void make_room(size_t n, int reading)
{
    long long blocked_since = -1;
    for (;;) {
        int blocked = 0, roomy = 0;
        for (int i = 0; i < target_count; i++) {
            struct target *t = &targets[i];
            flush_target(t);
            if (t->alive && t->len <= t->cap - n)
                roomy = 1;                /* a sibling an eviction would free */
            if (!(t->alive && t->len > t->cap - n))
                continue;                 /* not live, or has room */
            long long idle = now_ms() - t->last_progress_ms;
            if (idle >= stall_evict_ms) {
                fprintf(stderr,
                        "fanout: %s stalled, no write progress for %lld ms -- evicting\n",
                        t->path, idle);
                fail_target(t, "stalled with no write progress");
                continue;                 /* out; do not wait on it */
            }
            blocked = 1;
        }
        write_counters(0);
        if (!blocked)
            return;
        long long held = blocked_since < 0 ? 0 : now_ms() - blocked_since;
        if (blocked_since < 0) {
            blocked_since = now_ms();
            for (int i = 0; i < target_count; i++)
                targets[i].taken_at_block = targets[i].taken;
        } else if (reading && roomy && held >= block_evict_ms) {
            for (int i = 0; i < target_count; i++) {
                struct target *t = &targets[i];
                if (!(t->alive && t->len > t->cap - n))
                    continue;
                fprintf(stderr,
                        "fanout: %s stalled, took %lld bytes while stdin was held %lld ms -- evicting\n",
                        t->path, t->taken - t->taken_at_block, held);
                fail_target(t, "stalled the stream (too slow to drain)");
            }
            continue;                     /* the siblings take the next read now */
        }
        wait_writable(ROOM_POLL_MS);
    }
}

int main(int argc, char **argv)
{
    const char *hash_path = NULL, *sha_path = NULL, *progress_path = NULL;
    int progress_fd = -1, argi = 1, reached_eof = 0;
    long long progress_last = 0;
    struct xxh64 hash;
    struct sha_engine sha;
    /* Writing to a pipe whose reader has gone raises SIGPIPE, and the default
     * action would kill this process outright -- taking down every healthy
     * drawer because one drive died. Ignoring it turns that into the EPIPE
     * that the write path already reports as a single failed target. */
    signal(SIGPIPE, SIG_IGN);

    if (argc == 2 && (strcmp(argv[1], "--xxh64") == 0 || strcmp(argv[1], "--sha256") == 0))
        return hash_only(strcmp(argv[1], "--sha256") == 0);
    if (argc >= 2 && strcmp(argv[1], "--readback") == 0)
        return readback(argc, argv);
    while (argi < argc && (strcmp(argv[argi], "-H") == 0 || strcmp(argv[argi], "-S") == 0 ||
                           strcmp(argv[argi], "-P") == 0 || strcmp(argv[argi], "-U") == 0)) {
        const char **slot = strcmp(argv[argi], "-H") == 0 ? &hash_path :
                            strcmp(argv[argi], "-S") == 0 ? &sha_path :
                            strcmp(argv[argi], "-U") == 0 ? &units_path : &progress_path;
        if (++argi >= argc) {
            fprintf(stderr, "usage: fanout [-H file] [-S file] [-P file] [-U file] <buffer-bytes> <out> [out...]\n");
            return 2;
        }
        *slot = argv[argi++];
    }

    /* The stall ceilings are production constants; the env vars exist so the
     * negative-control tests can trip eviction in milliseconds instead of
     * minutes (both clocks, #674 and #1796), and so a single reader that
     * legitimately waits longer (udp-sender before its receivers join,
     * directsend.sh, #1444) can leave the ceiling to the shell. FANOUT_EXPECTED_BYTES is the length
     * check that separates an empty stdin, a producer that died mid-stream,
     * and a complete image -- without it those three share one EOF path and
     * all look like ok (#427). */
    stall_evict_ms = env_ll("FANOUT_STALL_EVICT_MS", STALL_EVICT_MS);
    block_evict_ms = env_ll("FANOUT_BLOCK_EVICT_MS", BLOCK_EVICT_MS);
    expected_bytes = env_ll("FANOUT_EXPECTED_BYTES", 0);
    /* Block sizes for measuring on metal (18/10): the pipes fanout widens and
     * the size of one read. Defaults are the measured ones; a chunk is never
     * below READ_CHUNK and never above one target's buffer (make_room). */
    pipe_bytes = env_ll("FANOUT_PIPE_BYTES", PIPE_BYTES);
    if (pipe_bytes > (1LL << 30)) {
        fprintf(stderr, "fanout: ignoring invalid FANOUT_PIPE_BYTES\n");
        pipe_bytes = PIPE_BYTES;
    }
    long long chunk_bytes = env_ll("FANOUT_CHUNK", READ_CHUNK);

    if (argc - argi < 2) {
        fprintf(stderr, "usage: fanout [-H file] [-S file] [-P file] [-U file] <buffer-bytes> <out> [out...]\n");
        return 2;
    }
    long long cap = atoll(argv[argi]);
    if (cap < READ_CHUNK) {
        fprintf(stderr, "fanout: buffer must be at least %d bytes\n", READ_CHUNK);
        return 2;
    }
    if (chunk_bytes < READ_CHUNK || chunk_bytes > cap) {
        fprintf(stderr, "fanout: ignoring invalid FANOUT_CHUNK\n");
        chunk_bytes = READ_CHUNK;
    }
    target_count = argc - argi - 1;
    if (target_count > MAX_TARGETS) {
        fprintf(stderr, "fanout: at most %d targets\n", MAX_TARGETS);
        return 2;
    }

    for (int i = 0; i < target_count; i++) {
        struct target *t = &targets[i];
        t->path = argv[argi + i + 1];
        t->cap = (size_t)cap;
        t->buf = malloc(t->cap);
        t->head = t->len = 0;
        t->alive = 1;
        t->reason = NULL;
        t->taken = 0;
        t->queued = 0;
        t->shown = 0;
        t->cfd = open_counter(t->path);
        if (!t->buf) {
            fprintf(stderr, "fanout: out of memory\n");
            return 2;
        }
        /* codeql[cpp/path-injection] false positive: same as open_counter (#291) */
        int fd = open_output(t->path);
        if (fd < 0) {
            t->fd = -1;
            fail_target(t, fd == OPEN_NO_READER
                        ? "timed out waiting for the writer pipeline"
                        : "could not open");
            continue;
        }
        t->fd = fd;
        widen_pipe(t->fd, t->path);
        t->last_progress_ms = now_ms();   /* stall clock starts at open (#674) */
    }

    widen_pipe(STDIN_FILENO, "stdin");
    xxh_init(&hash);
    if (sha_path && sha_engine_open(&sha) != 0)
        return 2;
    if (progress_path) {
        progress_fd = open(progress_path, O_WRONLY | O_CREAT | O_APPEND, 0644);
        if (progress_fd < 0)
            fprintf(stderr, "fanout: no stream progress counter for %s: %s\n",
                    progress_path, strerror(errno));
    }

    char *chunk = malloc((size_t)chunk_bytes);
    if (!chunk) {
        fprintf(stderr, "fanout: out of memory\n");
        return 2;
    }

    for (;;) {
        if (alive_count() == 0)
            break;                        /* nobody left to write to */
        ssize_t got = read(STDIN_FILENO, chunk, (size_t)chunk_bytes);
        if (got == 0) {
            reached_eof = 1;
            break;                        /* end of stream */
        }
        if (got < 0) {
            if (errno == EINTR)
                continue;
            fprintf(stderr, "fanout: read error: %s\n", strerror(errno));
            free(chunk);
            return 2;
        }
        stdin_total += (long long)got;
        /* Only what was asked for (#1492): the room round and capture pass -S
         * alone, and an XXH64 nobody writes is a second pass over every byte. */
        if (hash_path) xxh_update(&hash, chunk, (size_t)got);
        if (sha_path) sha_engine_update(&sha, chunk, (size_t)got);
        if (progress_fd >= 0 && now_ms() - progress_last >= COUNTER_MS) {
            progress_last = now_ms();
            if (append_count(progress_fd, stdin_total, progress_path) < 0) {
                close(progress_fd);
                progress_fd = -1;
            }
        }
        make_room((size_t)got, 1);
        for (int i = 0; i < target_count; i++)
            send_chunk(&targets[i], chunk, (size_t)got);
        write_counters(0);
    }
    free(chunk);

    if (progress_fd >= 0 && reached_eof) {
        append_count(progress_fd, stdin_total, progress_path);
        close(progress_fd);
        progress_fd = -1;
    }
    if (hash_path && reached_eof) {
        char line[18];
        int fd = open(hash_path, O_WRONLY | O_CREAT | O_TRUNC, 0644);
        int n = snprintf(line, sizeof line, "%016llx\n",
                         (unsigned long long)xxh_digest(&hash));
        if (fd < 0 || write(fd, line, (size_t)n) != n) {
            int saved = errno;
            if (fd >= 0) close(fd);
            fprintf(stderr, "fanout: could not write stream hash %s: %s\n",
                    hash_path, strerror(saved));
            return 2;
        }
        if (close(fd) != 0) {
            fprintf(stderr, "fanout: could not write stream hash %s: %s\n",
                    hash_path, strerror(errno));
            return 2;
        }
    }
    if (sha_path && reached_eof) {
        char line[66];
        int fd;
        /* No digest = no file: the agent reads its absence as "the sha256
         * was not computed", a state of its own (#73). Asked before open(),
         * so not even an empty file is left behind. */
        if (sha_engine_hex(&sha, line) != 0) {
            fprintf(stderr, "fanout: no stream SHA-256 to write to %s\n", sha_path);
            return 2;
        }
        fd = open(sha_path, O_WRONLY | O_CREAT | O_TRUNC, 0644);
        line[64] = '\n'; line[65] = '\0';
        if (fd < 0 || write(fd, line, 65) != 65) {
            int saved = errno;
            if (fd >= 0) close(fd);
            fprintf(stderr, "fanout: could not write stream SHA-256 %s: %s\n",
                    sha_path, strerror(saved));
            return 2;
        }
        if (close(fd) != 0) {
            fprintf(stderr, "fanout: could not write stream SHA-256 %s: %s\n",
                    sha_path, strerror(errno));
            return 2;
        }
    }

    /* EOF: drain every byte still queued, on a slow drawer too. A drawer that
     * has stopped taking bytes entirely for stall_evict_ms is failed by the
     * same health rule rather than blocking every sibling from finishing
     * (#674); a merely slow one keeps draining and is waited on to the end. */
    make_room((size_t)cap, 0);

    /* Length before "ok": empty, short, and complete must not share a path.
     * Expected length from FANOUT_EXPECTED_BYTES (tests, and a future
     * per-file compressed size); without it, empty stdin is still failed
     * because that is one of the three states and needs no number (#427). */
    if (alive_count() > 0) {
        if (expected_bytes > 0 && stdin_total != expected_bytes) {
            snprintf(stream_reason, sizeof stream_reason,
                     "%s stream (got %lld of %lld)",
                     stdin_total < expected_bytes ? "short" : "long",
                     stdin_total, expected_bytes);
            for (int i = 0; i < target_count; i++)
                fail_target(&targets[i], stream_reason);
        } else if (expected_bytes == 0 && stdin_total == 0) {
            for (int i = 0; i < target_count; i++)
                fail_target(&targets[i], "empty stream");
        }
    }

    /* The last word on every counter is the exact total, not whatever the
     * one-second tick happened to catch. */
    write_counters(1);

    int failed = 0;
    for (int i = 0; i < target_count; i++) {
        struct target *t = &targets[i];
        if (t->cfd >= 0)
            close(t->cfd);
        /* How often this target kept up: 0 queued = every byte went straight
         * from the read to its pipe. Read on metal, not by the agent. */
        fprintf(stderr, "fanout: %s took %lld bytes, %lld of them through its queue\n",
                t->path, t->taken, t->queued);
        if (t->alive) {
            /* fsync is for a real file; a FIFO returns EINVAL and is skipped.
             * close() errors were swallowed -- a target that lost its last
             * bytes at close still printed ok (#427). */
            struct stat st;
            if (t->fd >= 0 && fstat(t->fd, &st) == 0 && !S_ISFIFO(st.st_mode)) {
                if (fsync(t->fd) != 0) {
                    fail_target(t, "fsync failed");
                }
            }
        }
        if (t->alive && t->fd >= 0) {
            int fd = t->fd;
            t->fd = -1;
            if (close(fd) != 0) {
                t->alive = 0;
                t->reason = "close failed";
                free(t->buf);
                t->buf = NULL;
            } else {
                free(t->buf);
                t->buf = NULL;
            }
        }
        if (t->alive) {
            printf("%s ok\n", t->path);
        } else {
            failed = 1;
            printf("%s failed %s\n", t->path, t->reason ? t->reason : "unknown");
        }
    }
    fflush(stdout);
    return failed ? 1 : 0;
}
