/* chunkcast_head -- libFuzzer harness for the head frame (the partclone header,
 * its bit map and CRC), cc_head_load() in agent/chunkcast.c, and for the map
 * walk that turns a chunk number into device offsets, cc_walk().
 *
 * cc_walk decides WHERE on the disk each verified chunk is written. The harness
 * checks every range it yields against the partition the header describes: a
 * write past total * block_size is a write into the next partition, and aborts.
 *
 * Input: byte 0 flags -- bit 0 set `used` to the map's count, bit 1 recompute
 * the header CRC, bit 2 recompute the map CRC, bit 3 take the index's n and
 * data_bytes from the header (else from bytes 1-4); then the raw head. The
 * harness compresses it and gives the index the frame's length and XXH64, as
 * the server's index would -- so the fuzzer reaches the parser behind them.
 */
#include "chunkcast-nomain.c"

size_t ZSTD_compress(void *dst, size_t dstCapacity, const void *src, size_t srcSize, int level);
size_t ZSTD_compressBound(size_t srcSize);

static char path[] = "/tmp/chunkcast-head-XXXXXX";
static unsigned long ok_count, refused_count;
static int stats_fd = -1;
static uint64_t walk_total;

static void print_stats(void)
{
    dprintf(stats_fd, "chunkcast_head rc counts: 0:%lu refused:%lu\n", ok_count, refused_count);
}

int LLVMFuzzerInitialize(int *argc, char ***argv)
{
    (void)argc;
    (void)argv;
    int fd = mkstemp(path);
    if (fd < 0)
        abort();
    close(fd);
    if (getenv("FUZZ_STATS") && strcmp(getenv("FUZZ_STATS"), "1") == 0) {
        stats_fd = dup(STDERR_FILENO);
        atexit(print_stats);
    }
    return 0;
}

static void put_le(unsigned char *p, uint64_t v, int n)
{
    for (int i = 0; i < n; i++)
        p[i] = (unsigned char)(v >> (8 * i));
}

static int check_range(void *ctx, uint64_t off, uint64_t n, uint64_t pos)
{
    (void)ctx;
    (void)pos;
    uint64_t end = mp.total * mp.bs;
    if (n == 0 || off >= end || n > end - off) {
        fprintf(stderr, "chunkcast_head: cc_walk yields %llu bytes at %llu, the partition is %llu bytes\n",
                (unsigned long long)n, (unsigned long long)off, (unsigned long long)end);
        abort();
    }
    walk_total += n;
    return 0;
}

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size)
{
    if (size < 5)
        return 0;
    unsigned flags = data[0];
    size_t n = size - 5;
    unsigned char *h = malloc(n ? n : 1);
    if (!h)
        abort();
    memcpy(h, data + 5, n);
    memset(&ix, 0, sizeof ix);
    memset(&mp, 0, sizeof mp);
    if (n >= RB_HEAD) {
        uint64_t total = rb_le(h + 60, 8);
        size_t bm_len = total && total <= (1ULL << 34) ? (size_t)((total + 7) / 8) : 0;
        int whole = bm_len && n >= RB_HEAD + bm_len + 4;
        if ((flags & 1) && whole) {
            uint64_t seen = 0;
            for (uint64_t b = 0; b < total; b++)
                seen += rb_bit(h + RB_HEAD, b);
            put_le(h + 76, seen, 8);
        }
        if (flags & 2)
            put_le(h + RB_HEAD - 4, rb_crc32(h, RB_HEAD - 4), 4);
        if ((flags & 4) && whole)
            put_le(h + RB_HEAD + bm_len, rb_crc32(h + RB_HEAD, bm_len), 4);
        if (flags & 8)
            ix.data_bytes = rb_le(h + 76, 8) * rb_le(h + 84, 4);
    }
    if (!(flags & 8))
        ix.data_bytes = (uint64_t)data[1] << 24 | (uint64_t)data[2] << 16 | (uint64_t)data[3] << 8 | data[4];
    ix.n = (ix.data_bytes + CC_CHUNK - 1) / CC_CHUNK;
    /* What cc_index_load guarantees before cc_head_load ever runs. */
    if (ix.n > (1ULL << 26) || n > (1ULL << 20)) {
        free(h);
        return 0;
    }
    size_t cap = ZSTD_compressBound(n);
    unsigned char *z = malloc(cap);
    size_t zn = z ? ZSTD_compress(z, cap, h, n, 1) : 0;
    if (!z || ZSTD_isError(zn))
        abort();
    int fd = open(path, O_WRONLY | O_TRUNC);
    if (fd < 0 || write(fd, z, zn) != (ssize_t)zn)
        abort();
    close(fd);
    ix.head_bytes = n;
    ix.head_zbytes = zn;
    ix.head_xxh = cc_xxh(h, n);
    free(z);
    free(h);
    if (cc_head_load(path) == 0) {
        ok_count++;
        uint64_t want = 0;
        walk_total = 0;
        for (uint64_t k = 0; k < ix.n && k < 4096; k++) {
            want += cc_len(k);
            if (cc_walk(k, cc_len(k), check_range, NULL) != 0)
                abort();   /* the head was accepted: every chunk has its blocks */
        }
        if (walk_total != want)
            abort();       /* every byte of every chunk lands somewhere, once */
        free(mp.start);
        free(mp.bm - RB_HEAD);   /* mp.bm points into the decompressed head */
    } else {
        refused_count++;
        free(mp.start);          /* cc_head_load's refusal after its calloc does not free it: */
    }                            /* harmless in a process that exits on it, a leak in a loop */
    return 0;
}
