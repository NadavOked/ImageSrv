/* chunkcast_packet -- libFuzzer harness for the multicast receive path:
 * the header checks of cc_recv() and cc_data(), which reassembles a chunk's
 * zstd frame from 1440-byte packets in agent/chunkcast.c.
 *
 * Anything on the VLAN can send these packets. cc_data copies each payload to
 * slot + seq * 1440 -- the bounds of that copy are what the harness is for.
 *
 * Input: bytes 0-1 the index -- n chunks (1..8) and the frame-length seed, then
 * packets, each a 2-byte length (bit 15: stamp a valid header of this session,
 * so the fuzzer does not have to find magic, part and session first) and that
 * many bytes, at most a datagram's CC_PKT (recvmmsg truncates to it).
 * Frames that complete go through cc_verify, as the verifier thread would.
 * The checks of cc_recv's loop are repeated here: they are inline in it.
 */
#include "chunkcast-nomain.c"

#define SESSION UINT64_C(0x1122334455667788)

static unsigned char *bufs[CC_SLOTS + CC_VQ + 2];
static unsigned char *verify_out;
static unsigned long data_count, complete_count;
static int stats_fd = -1;

static void print_stats(void)
{
    dprintf(stats_fd, "chunkcast_packet rc counts: 0:%lu data:%lu\n", complete_count, data_count);
}

int LLVMFuzzerInitialize(int *argc, char ***argv)
{
    (void)argc;
    (void)argv;
    for (int i = 0; i < CC_SLOTS + CC_VQ + 2; i++)
        if (!(bufs[i] = malloc(CC_ZMAX)))
            abort();
    if (!(verify_out = malloc(CC_CHUNK)))
        abort();
    if (getenv("FUZZ_STATS") && strcmp(getenv("FUZZ_STATS"), "1") == 0) {
        stats_fd = dup(STDERR_FILENO);
        atexit(print_stats);
    }
    return 0;
}

static void drain(void)
{
    for (int i = 0; i < vq_n; i++) {
        cc_verify(vq[i].k, vq[i].z, verify_out);
        spare[nspare++] = vq[i].z;
        complete_count++;
    }
    vq_n = 0;
}

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size)
{
    if (size < 2)
        return 0;
    memset(&ix, 0, sizeof ix);
    ix.n = 1 + data[0] % 8;
    ix.data_bytes = ix.n * CC_CHUNK;
    ix.zlen = calloc(ix.n, sizeof *ix.zlen);
    ix.xxh = calloc(ix.n, sizeof *ix.xxh);
    for (uint64_t k = 0; k < ix.n; k++) {
        /* Mostly short frames (a few packets, so they complete), now and then a full-size one. */
        ix.zlen[k] = k == 0 && (data[1] & 1) ? CC_ZMAX : (uint32_t)(1 + (data[1] * 977u + k * 1499u) % 6000u);
        ix.xxh[k] = k;
    }
    cc_part = 3;
    ntg = 1;
    tg[0].alive = 1;
    tg[0].have = calloc(1, 2);
    tg[0].pend = calloc(1, 2);
    inflight = calloc(1, 2);
    if (!ix.zlen || !ix.xxh || !tg[0].have || !tg[0].pend || !inflight)
        abort();
    for (int i = 0; i < CC_SLOTS; i++) {
        slots[i].used = 0;
        slots[i].data = bufs[i];
    }
    for (nspare = 0; nspare < CC_VQ + 2; nspare++)
        spare[nspare] = bufs[CC_SLOTS + nspare];
    vq_n = 0;

    static unsigned char pk[CC_PKT];
    for (size_t at = 2; at + 2 <= size;) {
        unsigned word = (unsigned)data[at] << 8 | data[at + 1];
        size_t want = word & 0x7fff, len = want < CC_PKT ? want : CC_PKT;
        at += 2;
        if (len > size - at)
            len = size - at;
        memcpy(pk, data + at, len);
        at += want < size - at ? want : size - at;
        if ((word & 0x8000) && len >= CC_HDR) {
            be_put(pk, CC_MAGIC, 4);
            pk[5] = CC_VERSION;
            be_put(pk + 6, (uint64_t)cc_part, 2);
            be_put(pk + 8, SESSION, 8);
        }
        /* cc_recv's loop, as written there */
        if (len < CC_HDR || be_get(pk, 4) != CC_MAGIC || be_get(pk + 6, 2) != (uint64_t)cc_part ||
            be_get(pk + 8, 8) != SESSION || pk[5] != CC_VERSION)
            continue;
        if (pk[4] == CC_DATA && len > CC_HDR + CC_DATA_HDR) {
            data_count++;
            cc_data(pk, len);
            drain();
        }
    }
    /* What is left in the slots and the queue goes back for the next input. */
    drain();
    free(ix.zlen);
    free(ix.xxh);
    free(tg[0].have);
    free(tg[0].pend);
    free(inflight);
    memset(&ix, 0, sizeof ix);
    tg[0].have = tg[0].pend = inflight = NULL;
    /* cc_data swaps a completed slot's buffer for a spare one: gather them all again. */
    int seen = 0;
    for (int i = 0; i < CC_SLOTS; i++)
        for (int j = 0; j < CC_SLOTS + CC_VQ + 2; j++)
            seen += slots[i].data == bufs[j];
    for (int i = 0; i < nspare; i++)
        for (int j = 0; j < CC_SLOTS + CC_VQ + 2; j++)
            seen += spare[i] == bufs[j];
    if (seen != CC_SLOTS + CC_VQ + 2)
        abort();   /* a frame buffer was lost or doubled: the receiver would run out of them */
    int k = 0;
    for (int i = 0; i < CC_SLOTS; i++)
        bufs[k++] = slots[i].data;
    for (int i = 0; i < nspare; i++)
        bufs[k++] = spare[i];
    return 0;
}
