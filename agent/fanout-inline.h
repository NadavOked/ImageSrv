/* #2023: the local clone's inline full read-back -- a partclone v2 (-a0) writer
 * and, per target, a reader that lags behind it.
 * Included after readback(): the bitmap, XXH64 and 4MiB chunks of the used-data
 * stream are its own, so the lines are exactly `--readback -x`'s.
 *
 * The writer scatters the stream's used blocks to their offsets and hashes each
 * chunk from the bytes it received -- no second read of the source. Every <lag>
 * bytes it flushes the disk (fdatasync: the page cache AND the drive's write
 * cache) and only then publishes how far is flushed. The reader takes a chunk
 * with O_DIRECT only once it is flushed AND <lag> more bytes were written after
 * it: an O_DIRECT read of dirty pages would write them back first and get them
 * straight from the drive's cache, the very cache the lag is for. The relay's
 * whole-stream XXH64 (lccopy.sh lc_judge) still ties the received bytes to the
 * source's read.
 *
 * Repair before failing (Nadav, 22/10): a chunk that does not match is read
 * again from the SOURCE partition (O_RDONLY -- never written, #1466), checked
 * against the stream's own hash, written over the target's blocks, flushed and
 * read back -- up to IR_TRIES times. Matched now = the target ends "repaired"
 * (orange), never "verified". Still wrong = the reader says so, and the writer
 * stops at its next chunk: the drawer is red mid-copy, the others carry on.
 */
#include <sys/mman.h>
#include <sys/prctl.h>
#include <sys/wait.h>
#include <stdarg.h>

#define IR_TRIES 2                  /* readbackinline.sh RB_INLINE_TRIES */

/* One log line in ONE write(2): every target's writer and reader append to the
 * same agent log (O_APPEND), and stderr's fprintf splits a line at its %s --
 * two readers' lines came out interleaved mid-word (testrunner, 23/10). */
static void ir_say(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static void ir_say(const char *fmt, ...)
{
    char line[1024];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(line, sizeof line, fmt, ap);
    va_end(ap);
    if (n < 0) return;
    if ((size_t)n >= sizeof line) { n = sizeof line - 1; line[n - 1] = '\n'; }
    ssize_t w;
    do { w = write(STDERR_FILENO, line, (size_t)n); } while (w < 0 && errno == EINTR);
}

struct ir_shared {
    uint64_t written, flushed;      /* stream bytes written / covered by a flush */
    uint64_t bad, repaired;         /* the chunk that stayed wrong; chunks rewritten right */
    int done;                       /* 0 copying, 1 the final fsync succeeded, -1 aborted */
    int failed;                     /* 1: a chunk was still wrong after IR_TRIES rewrites */
    uint64_t hashes[];              /* published by written's release store */
};

/* The reader's side: the target (read uncached), the source (read for a repair). */
struct ir_ctx {
    const char *dev, *src;
    const unsigned char *bm;
    uint64_t total, bs, align, salign;
    int fd, sfd, wfd;
    unsigned char *io, *data;
    struct ir_shared *s;
};

static uint64_t ir_load(uint64_t *p) { return __atomic_load_n(p, __ATOMIC_ACQUIRE); }
static int ir_done(struct ir_shared *s) { return __atomic_load_n(&s->done, __ATOMIC_ACQUIRE); }

static uint64_t ir_align(int fd)
{
    int lss = 0;
    if (ioctl(fd, BLKSSZGET, &lss) == 0 && lss >= 512 && lss <= 4096 && !(lss & (lss - 1)))
        return (uint64_t)lss;
    return 4096;
}

/* A contiguous run, limited by the remaining bytes of this data-stream chunk. */
static uint64_t ir_run(const unsigned char *bm, uint64_t total, uint64_t *b,
                       uint64_t left, uint64_t bs)
{
    *b = rb_next(bm, *b, total);
    uint64_t e = *b;
    while (e < total && (e - *b) < left / bs && rb_bit(bm, e)) e++;
    return (e - *b) * bs;
}

/* Moves *b past len bytes of used blocks. -1 = the map ended first. */
static int ir_skip(const unsigned char *bm, uint64_t total, uint64_t *b, uint64_t len, uint64_t bs)
{
    while (len) {
        uint64_t n = ir_run(bm, total, b, len, bs);
        if (!n) return -1;
        *b += n / bs; len -= n;
    }
    return 0;
}

static int ir_write(int fd, const unsigned char *p, uint64_t n, uint64_t off)
{
    while (n) {
        ssize_t w = pwrite(fd, p, (size_t)n, (off_t)off);
        if (w > 0) { n -= (uint64_t)w; off += (uint64_t)w; p += w; }
        else if (!(w < 0 && errno == EINTR)) return -1;
    }
    return 0;
}

/* Writes len bytes of used blocks, from block *b on, out of p (buffered: the
 * writer and a repair share one page cache, so a page two chunks share stays whole). */
static int ir_put(int fd, const unsigned char *bm, uint64_t total, uint64_t bs, uint64_t *b,
                  const unsigned char *p, uint64_t len)
{
    for (uint64_t pos = 0; pos < len;) {
        uint64_t n = ir_run(bm, total, b, len - pos, bs);
        if (!n || ir_write(fd, p + pos, n, *b * bs)) return -1;
        *b += n / bs; pos += n;
    }
    return 0;
}

/* One chunk -- len bytes of used blocks from block b -- of fd, uncached: its
 * XXH64, and the bytes into data unless NULL. -1 = a read error (said on stderr). */
static int ir_get(struct ir_ctx *c, int fd, const char *dev, uint64_t align, uint64_t b,
                  uint64_t len, unsigned char *data, uint64_t *hash)
{
    struct xxh64 h;
    xxh_init(&h);
    for (uint64_t pos = 0; pos < len;) {
        uint64_t n = ir_run(c->bm, c->total, &b, len - pos, c->bs);
        if (!n) return -1;
        long long at = rb_pread(fd, c->io, b * c->bs, n, align, dev);
        if (at < 0) return -1;
        xxh_update(&h, c->io + at, (size_t)n);
        if (data) memcpy(data + pos, c->io + at, (size_t)n);
        b += n / c->bs; pos += n;
    }
    *hash = xxh_digest(&h);
    return 0;
}

/* 1 = chunk k on the target is the stream's chunk k. A read error is not a match. */
static int ir_match(struct ir_ctx *c, uint64_t k, uint64_t b, uint64_t len)
{
    uint64_t h;
    return ir_get(c, c->fd, c->dev, c->align, b, len, NULL, &h) == 0 && h == c->s->hashes[k];
}

/* Chunk k did not match: from the source, onto the target, flushed, read back
 * -- up to IR_TRIES times. 0 = it matches now. */
static int ir_repair(struct ir_ctx *c, uint64_t k, uint64_t b, uint64_t len)
{
    for (int t = 1; t <= IR_TRIES; t++) {
        uint64_t h, at = b;
        const char *why = NULL;
        if (c->sfd < 0 && (c->sfd = open(c->src, O_RDONLY | O_DIRECT | O_CLOEXEC)) >= 0)
            c->salign = ir_align(c->sfd);
        if (c->wfd < 0)
            c->wfd = open(c->dev, O_WRONLY | O_CLOEXEC);   /* never creates or truncates a node */
        if (c->sfd < 0 || c->wfd < 0)
            why = "the source or the target did not open";
        else if (ir_get(c, c->sfd, c->src, c->salign, b, len, c->data, &h) || h != c->s->hashes[k])
            why = "the source did not give the chunk back";
        else if (ir_put(c->wfd, c->bm, c->total, c->bs, &at, c->data, len) || fdatasync(c->wfd))
            why = "the rewrite or its flush failed";
        else if (!ir_match(c, k, b, len))
            why = "read back wrong again";
        ir_say("fanout: inline: %s: chunk %llu rewritten from %s (try %d of %d): %s\n",
                c->dev, (unsigned long long)k, c->src, t, IR_TRIES, why ? why : "matches now");
        if (!why) return 0;
        if (c->sfd < 0 || c->wfd < 0) break;
    }
    return -1;
}

static int ir_reader(struct ir_ctx *c, uint64_t bytes, uint64_t lag)
{
    struct ir_shared *s = c->s;
    uint64_t b = 0, k = 0, concurrent = 0;
    void *mem = NULL;
    int rc = 3;
    c->fd = open(c->dev, O_RDONLY | O_DIRECT | O_CLOEXEC);
    if (c->fd < 0) {
        ir_say("fanout: inline: %s: cannot open uncached: %s\n", c->dev, strerror(errno));
        return 3;
    }
    c->align = ir_align(c->fd);
    if (posix_memalign(&mem, 4096, RB_CHUNK + 8192) != 0 || !(c->data = malloc(RB_CHUNK))) goto out;
    c->io = mem;
    for (uint64_t off = 0; off < bytes; k++) {
        uint64_t len = bytes - off < RB_CHUNK ? bytes - off : RB_CHUNK, start = b;
        for (;;) {
            int done = ir_done(s);
            if (done < 0) goto out;
            if (ir_load(&s->flushed) >= off + len &&
                (done == 1 || ir_load(&s->written) - (off + len) >= lag)) break;
            usleep(10000);
        }
        if (!ir_done(s)) concurrent++;
        if (ir_skip(c->bm, c->total, &b, len, c->bs)) goto out;
        if (!ir_match(c, k, start, len)) {
            if (ir_repair(c, k, start, len)) {
                s->bad = k;
                __atomic_store_n(&s->failed, 1, __ATOMIC_RELEASE);
                rc = 1;
                goto out;
            }
            __atomic_add_fetch(&s->repaired, 1, __ATOMIC_RELEASE);
        }
        off += len;
    }
    /* Even an empty bitmap needs positive evidence of EOF and the final flush. */
    while (!ir_done(s)) usleep(10000);
    if (ir_done(s) == 1) rc = 0;
out:
    ir_say("fanout: inline: %s: %llu chunks checked during copy, %llu rewritten, reader rc=%d\n",
            c->dev, (unsigned long long)concurrent, (unsigned long long)ir_load(&s->repaired), rc);
    free(mem); free(c->data);
    close(c->fd);
    if (c->sfd >= 0) close(c->sfd);
    if (c->wfd >= 0) close(c->wfd);
    return rc;
}

/* CLI: --inline-write <partition> <source-partition> <result-file> <lag-bytes> <tail-timeout-s>.
 * stdout is the SOURCE data's c/n list, exactly like --readback -x.
 * rc is the WRITE verdict (5: stopped, the reader proved a chunk wrong); the
 * result file records the READ verdict on its own: ok | repaired|<chunks> |
 * failed|<chunk> | unchecked|<why>. An open failure, a timeout or a failed
 * flush is unchecked, never a successful read.
 */
static int inline_write(int argc, char **argv)
{
    unsigned char head[RB_HEAD], crc[4], *bm = NULL, *buf = NULL;
    struct ir_shared *s = MAP_FAILED;
    uint64_t total, used, bs, bytes, count, lag, secs, b = 0, seen = 0, off = 0;
    size_t shared_size = 0;
    int fd = -1, rc = 2, reader_rc = 3, status;
    pid_t child = -1;
    FILE *result;
    char *end;
    const char *why = "stream";
    if (argc != 7) return 2;
    pid_t owner = getppid();
    if (prctl(PR_SET_PDEATHSIG, SIGKILL) || getppid() != owner) return 2;
    errno = 0; lag = strtoull(argv[5], &end, 10);
    if (errno || !*argv[5] || *end || argv[5][0] == '-' || lag < RB_CHUNK || lag > (1ULL << 40)) return 2;
    errno = 0; secs = strtoull(argv[6], &end, 10);
    if (errno || !*argv[6] || *end || argv[6][0] == '-' || !secs || secs > 86400) return 2;
    result = fopen(argv[4], "w");
    if (!result) return 2;
    if (rb_read_all(0, head, RB_HEAD) || memcmp(head, "partclone-image", 15) ||
        memcmp(head + 30, "0002", 4) || rb_le(head + 34, 2) != 0xC0DE ||
        rb_le(head + 92, 2) != 2 || rb_le(head + 96, 2) || rb_le(head + 98, 2) ||
        head[105] != 1 || rb_crc32(head, RB_HEAD - 4) != rb_le(head + 106, 4)) goto out;
    total = rb_le(head + 60, 8); used = rb_le(head + 76, 8); bs = rb_le(head + 84, 4);
    if (!total || total > (1ULL << 34) || used > total || bs < 512 || bs > RB_CHUNK ||
        (bs & (bs - 1)) || total > INT64_MAX / bs) goto out;
    bytes = used * bs; count = (bytes + RB_CHUNK - 1) / RB_CHUNK;
    size_t bm_len = (size_t)((total + 7) / 8);
    bm = malloc(bm_len); buf = malloc(RB_CHUNK);
    if (!bm || !buf || rb_read_all(0, bm, bm_len) || rb_read_all(0, crc, 4) ||
        rb_crc32(bm, bm_len) != rb_le(crc, 4)) goto out;
    for (uint64_t j = 0; j < total / 8; j++) seen += __builtin_popcount(bm[j]);
    if (total & 7) seen += __builtin_popcount(bm[total / 8] & ((1u << (total & 7)) - 1));
    if (seen != used || count > (SIZE_MAX - sizeof *s) / sizeof(uint64_t)) goto out;
    fd = open(argv[2], O_WRONLY | O_CLOEXEC);  /* never create or truncate a node */
    uint64_t capacity = 0;
    if (fd < 0 || ioctl(fd, BLKGETSIZE64, &capacity) || capacity < total * bs) goto out;
    shared_size = sizeof *s + (size_t)count * sizeof(uint64_t);
    s = mmap(NULL, shared_size, PROT_READ | PROT_WRITE, MAP_SHARED | MAP_ANONYMOUS, -1, 0);
    if (s == MAP_FAILED) goto out;
    pid_t parent = getpid();
    child = fork();
    if (child < 0) goto out;
    if (!child) {
        /* A killed writer must not leave a reader holding a disk or a pipe. */
        if (prctl(PR_SET_PDEATHSIG, SIGKILL) || getppid() != parent) _exit(3);
        close(fd); close(STDIN_FILENO); close(STDOUT_FILENO); fclose(result);
        struct ir_ctx c = { .dev = argv[2], .src = argv[3], .bm = bm, .total = total, .bs = bs,
                            .fd = -1, .sfd = -1, .wfd = -1, .s = s };
        _exit(ir_reader(&c, bytes, lag));
    }
    for (uint64_t k = 0, flushed = 0; off < bytes; k++) {
        uint64_t len = bytes - off < RB_CHUNK ? bytes - off : RB_CHUNK;
        if (__atomic_load_n(&s->failed, __ATOMIC_ACQUIRE)) {
            ir_say("fanout: inline: %s: stopped after %llu of %llu bytes: chunk %llu stayed wrong\n",
                    argv[2], (unsigned long long)off, (unsigned long long)bytes, (unsigned long long)s->bad);
            why = "readback"; rc = 5;
            goto out;
        }
        if (rb_read_all(0, buf, (size_t)len)) goto out;
        struct xxh64 h;
        xxh_init(&h); xxh_update(&h, buf, (size_t)len);
        s->hashes[k] = xxh_digest(&h);
        if (ir_put(fd, bm, total, bs, &b, buf, len)) { why = "write"; goto out; }
        off += len;
        __atomic_store_n(&s->written, off, __ATOMIC_RELEASE);
        printf("c %llu %016llx\n", (unsigned long long)k, (unsigned long long)s->hashes[k]);
        if (off - flushed >= lag) {
            if (fdatasync(fd)) { why = "flush"; goto out; }
            flushed = off;
            __atomic_store_n(&s->flushed, off, __ATOMIC_RELEASE);
        }
    }
    ssize_t extra;
    do { extra = read(0, buf, 1); } while (extra < 0 && errno == EINTR);
    if (extra != 0) goto out;        /* short AND overlong streams cannot pass */
    why = "flush";
    if (fsync(fd)) goto out;         /* tail: drive cache flushed before release */
    if (close(fd)) { fd = -1; goto out; }
    fd = -1;
    __atomic_store_n(&s->flushed, bytes, __ATOMIC_RELEASE);
    __atomic_store_n(&s->done, 1, __ATOMIC_RELEASE);
    printf("n %llu %llu\n", (unsigned long long)count, (unsigned long long)bytes);
    if (fflush(stdout) || ferror(stdout)) goto out;
    rc = 0; why = "timeout";
    long long deadline = now_ms() + (long long)secs * 1000;
    while (now_ms() < deadline) {
        pid_t w = waitpid(child, &status, WNOHANG);
        if (w == child) {
            child = -1;
            reader_rc = WIFEXITED(status) ? WEXITSTATUS(status) : 3;
            why = reader_rc == 3 ? "reader unavailable" : "reader";
            break;
        }
        if (w < 0 && errno != EINTR) break;
        usleep(10000);
    }
out:
    if (s != MAP_FAILED) __atomic_store_n(&s->done, -1, __ATOMIC_RELEASE);
    if (child > 0) {
        /* A reader that just proved a chunk wrong is still saying so: a moment for
         * it, then SIGKILL. D-state I/O cannot be killed yet: never turn a timeout
         * into an unbounded wait. Its shared mapping survives until it exits. */
        for (int tries = 0; tries < 200; tries++) {
            if (tries == 100) kill(child, SIGKILL);
            if (waitpid(child, &status, WNOHANG) == child) break;
            usleep(10000);
        }
    }
    if (s != MAP_FAILED && __atomic_load_n(&s->failed, __ATOMIC_ACQUIRE))
        fprintf(result, "failed|%llu\n", (unsigned long long)s->bad);
    else if (rc == 0 && reader_rc == 0 && ir_load(&s->repaired))
        fprintf(result, "repaired|%llu\n", (unsigned long long)ir_load(&s->repaired));
    else if (rc == 0 && reader_rc == 0)
        fprintf(result, "ok\n");
    else
        fprintf(result, "unchecked|%s (writer=%d reader=%d)\n", why, rc, reader_rc);
    if (fclose(result) && rc == 0) rc = 2;
    if (fd >= 0) close(fd);
    if (s != MAP_FAILED) munmap(s, shared_size);
    free(buf); free(bm);
    return rc;
}
