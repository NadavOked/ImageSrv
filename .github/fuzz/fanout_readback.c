/* fanout_readback -- libFuzzer harness for `fanout --readback` (agent/fanout.c).
 *
 * What it feeds: the partclone v2 header (110 bytes), the bit map and its CRC
 * -- the stream partclone writes and readback() parses before it reads a single
 * block of the disk. A wrong header or map there is a read of the wrong blocks
 * (or past the map) on a machine in a classroom, as root.
 *
 * The product is not changed: agent/fanout.c is included whole, its main()
 * renamed, and readback() called exactly as main() calls it, with the stream on
 * stdin (a pipe) and a 16MiB file as the "device".
 *
 * Input layout:
 *   byte 0     flags -- bit 0: set `used` to the map's real count
 *                       bit 1: recompute the header CRC
 *                       bit 2: recompute the bit map CRC
 *                       bits 3-4: mode (0/3 = sha256, 1 = -x, 2 = -x -s <seed> <permille>)
 *   bytes 1-2  permille for mode 2 (1..1000), the seed is byte 3
 *   bytes 4..  the stream on stdin
 * The fix-ups let the fuzzer reach the map walk behind two CRCs and a count;
 * with the bits clear it still tries the refusals themselves.
 *
 * FUZZ_STATS=1 prints how many inputs ended in each exit code -- the workflow
 * runs the seed alone with it and requires an rc 0, i.e. positive evidence that
 * the harness gets all the way through the parser (principle 5).
 */
#define main fanout_main
#include "../../agent/fanout.c"
#undef main

#include <stddef.h>

#define DEV_BYTES (16u << 20)

static char dev_path[] = "/tmp/fanout-readback-dev-XXXXXX";
static unsigned long rc_count[8];
static int stats_fd = -1;

static void print_stats(void)
{
    if (stats_fd < 0)
        return;
    dprintf(stats_fd, "fanout_readback rc counts: 0:%lu 2:%lu 3:%lu 4:%lu other:%lu\n",
            rc_count[0], rc_count[2], rc_count[3], rc_count[4], rc_count[7]);
}

int LLVMFuzzerInitialize(int *argc, char ***argv)
{
    (void)argc;
    (void)argv;
    int fd = mkstemp(dev_path);
    if (fd < 0 || ftruncate(fd, DEV_BYTES) != 0) {
        perror("fanout_readback: the device file");
        abort();
    }
    close(fd);
    setenv("FANOUT_READBACK_CACHED", "1", 1);   /* /tmp may refuse O_DIRECT */
    setenv("FANOUT_SHA_ENGINE", "builtin", 1);  /* readback never closes the AF_ALG fd: one a run */
    signal(SIGPIPE, SIG_IGN);
    if (getenv("FUZZ_STATS") && strcmp(getenv("FUZZ_STATS"), "1") == 0) {
        stats_fd = dup(STDERR_FILENO);
        atexit(print_stats);
    }
    return 0;
}

static void fix_up(unsigned char *s, size_t n, unsigned flags)
{
    if (n < RB_HEAD)
        return;
    uint64_t total = rb_le(s + 60, 8);
    if (total == 0 || total > (1ULL << 34))
        return;
    size_t bm_len = (size_t)((total + 7) / 8);
    int whole = n >= RB_HEAD + bm_len + 4;
    if ((flags & 1) && whole) {
        const unsigned char *bm = s + RB_HEAD;
        uint64_t seen = 0;
        for (size_t j = 0; j < (size_t)(total >> 3); j++)
            seen += (uint64_t)__builtin_popcount(bm[j]);
        if (total & 7)
            seen += (uint64_t)__builtin_popcount(bm[total >> 3] & ((1u << (total & 7)) - 1));
        for (int i = 0; i < 8; i++)
            s[76 + i] = (unsigned char)(seen >> (8 * i));
    }
    if (flags & 2) {
        uint32_t c = rb_crc32(s, RB_HEAD - 4);
        for (int i = 0; i < 4; i++)
            s[RB_HEAD - 4 + i] = (unsigned char)(c >> (8 * i));
    }
    if ((flags & 4) && whole) {
        uint32_t c = rb_crc32(s + RB_HEAD, bm_len);
        for (int i = 0; i < 4; i++)
            s[RB_HEAD + bm_len + i] = (unsigned char)(c >> (8 * i));
    }
}

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size)
{
    if (size < 4)
        return 0;
    unsigned flags = data[0];
    unsigned permille = 1 + ((unsigned)data[1] << 8 | data[2]) % 1000;
    unsigned seed = data[3];
    size_t n = size - 4;
    unsigned char *s = malloc(n ? n : 1);
    int p[2];
    if (!s || pipe(p) != 0)
        abort();
    memcpy(s, data + 4, n);
    fix_up(s, n, flags);
    if (fcntl(p[1], F_SETPIPE_SZ, 1 << 20) < 0 || (size_t)fcntl(p[1], F_GETPIPE_SZ) < n) {
        perror("fanout_readback: the pipe cannot hold the input (-max_len too large?)");
        abort();
    }
    if (n && write(p[1], s, n) != (ssize_t)n)
        abort();
    free(s);
    close(p[1]);
    if (dup2(p[0], STDIN_FILENO) < 0)
        abort();
    close(p[0]);

    char seed_s[8], permille_s[8];
    snprintf(seed_s, sizeof seed_s, "%u", seed);
    snprintf(permille_s, sizeof permille_s, "%u", permille);
    char *argv[8] = { "fanout", "--readback" };
    int argc = 2;
    switch ((flags >> 3) & 3) {
    case 1:
        argv[argc++] = "-x";
        break;
    case 2:
        argv[argc++] = "-x";
        argv[argc++] = "-s";
        argv[argc++] = seed_s;
        argv[argc++] = permille_s;
        break;
    default:
        break;
    }
    argv[argc++] = dev_path;
    argv[argc] = NULL;
    int rc = readback(argc, argv);
    rc_count[rc >= 0 && rc <= 4 ? rc : 7]++;
    /* fd 0 back to /dev/null: readback closes stdin only once the map arrived,
     * and a free fd 0 would make the next pipe() return it -- the next input
     * would then close its own read end and arrive as nothing. */
    int z = open("/dev/null", O_RDONLY);
    if (z < 0)
        abort();
    if (z != STDIN_FILENO) {
        if (dup2(z, STDIN_FILENO) < 0)
            abort();
        close(z);
    }
    return 0;
}
