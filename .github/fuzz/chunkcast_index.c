/* chunkcast_index -- libFuzzer harness for the format-2 index parser,
 * cc_index_load() in agent/chunkcast.c.
 *
 * The index arrives over HTTP and is believed only once its XXH64 is the
 * manifest's -- but it is PARSED to get that far, and a wrong count or offset in
 * it decides which chunk is written where. Input: byte 0 bit 0 = pass the
 * file's own XXH64 as the manifest's (the path the receiver takes), then the
 * index text. build.sh compiles agent/chunkcast.c with its main() renamed
 * (chunkcast-nomain.c); the parser is called as cc_setup() calls it.
 */
#include "chunkcast-nomain.c"

static char path[] = "/tmp/chunkcast-index-XXXXXX";
static unsigned long ok_count, refused_count;
static int stats_fd = -1;

static void print_stats(void)
{
    dprintf(stats_fd, "chunkcast_index rc counts: 0:%lu refused:%lu\n", ok_count, refused_count);
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

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size)
{
    if (size < 1)
        return 0;
    int fd = open(path, O_WRONLY | O_TRUNC);
    if (fd < 0 || (size > 1 && write(fd, data + 1, size - 1) != (ssize_t)(size - 1)))
        abort();
    close(fd);
    char want[17];
    snprintf(want, sizeof want, "%016llx", (unsigned long long)cc_xxh(data + 1, size - 1));
    int rc = cc_index_load(path, (data[0] & 1) ? want : NULL);
    if (rc == 0)
        ok_count++;
    else
        refused_count++;
    free(ix.off);
    free(ix.xxh);
    free(ix.zlen);
    memset(&ix, 0, sizeof ix);
    return 0;
}
