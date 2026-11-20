/* chunkcast -- the chunk engine's sender and receiver (#2533, stage 3).
 *
 * udpcast sends a slice, waits for every receiver to acknowledge it and only
 * then goes on: a slow receiver slows the room, a silent one is dropped, and
 * nothing that has passed can be asked for again ("the 90% drop", #656).
 * Here the sender never waits for acknowledgements. Trident feedback adjusts
 * its bounded rate in-wave; each receiver writes what arrived whole and tells the server what it
 * holds; the server then repairs only what is missing (server/rounds/repair.py)
 * -- another multicast pass for a chunk two receivers lack, an HTTP Range
 * request for a chunk one receiver lacks.
 *
 * The unit is the image format's chunk (handoffs/format-spec-2533.md, format 2):
 * 4MiB of the partition's data stream, one independent zstd frame each, with
 * its offset, length and XXH64 in the partition's index file. The index is the
 * trusted side: the agent downloads it over HTTP and this program refuses it
 * unless its own XXH64 is the manifest's `index_xxh64`.
 *
 * XXH64 finds a damaged chunk; it does not stop a made one -- it is not a
 * cryptographic hash, and a 4MiB block with chosen content and a given XXH64 can
 * be made in moments (#3332). So beside the index there is the partition's BLAKE3
 * list (--b3-file: 32 bytes a chunk, the BLAKE3 of the chunk's decompressed
 * bytes, the server's `<file>.b3`), believed only when its own BLAKE3 is the
 * manifest's `chunk_b3` (--b3-file-hash). With it, a chunk from the multicast
 * VLAN, an HTTP answer or a neighbour is written only when its BLAKE3 is the
 * list's. The agent never writes without it (agent/lib/restore/chunkb3.sh: no list
 * = the drawers fail by name, Nadav 15/11); called without it -- a test's rig --
 * this program checks XXH64 alone, and says so once, in those words.
 *
 *   chunkcast send  --to ADDR:PORT --session HEX16 --part N --pass N
 *                   --index FILE --file FILE [--iface IF] [--ttl N] [--kbps N] [--write-kbps N]
 *                   [--chunks-file FILE] [--lead-ms N] [--tail-ms N]
 *                   [--feedback on|off] [--control-port PORT] [--rate-min KBPS] [--rate-max KBPS] [--rate-cap KBPS]
 *                   Feedback defaults on; control defaults to data port + 1 (portbase + 3).
 *                   Bounds enclose the start rate, defaulting to it. With --write-kbps, feedback
 *                   adjusts decompressed writes and --kbps remains the independent wire ceiling.
 *                   #3599: --rate-cap (>= --rate-max, default = --rate-max: no widening) lets --rate-max
 *                   itself grow, 10% at a time, after every CC_FB_WIDEN_TICKS decisions that sat on it
 *                   with every receiver healthy; the first decrease puts it back to the last ceiling that
 *                   proved healthy, and it does not widen again in this pass (a decrease at an unwidened ceiling changes nothing). Nothing else moves.
 *                   #3535: CHUNKCAST_BATCH=1..64 caps send batches (default 64).
 *                   CHUNKCAST_PACE=legacy|smooth defaults to legacy. Smooth uses one limiter,
 *                   pacing each batch by the larger of its wire and decompressed-write times,
 *                   with batches sized for about 250 us, up to CHUNKCAST_BATCH.
 *                   #3535: CHUNKCAST_READER=inline|thread defaults to inline. Thread reads the --file in the send order
 *                   (also --order mixed and --chunks-file) on a thread of its own, into CHUNKCAST_READQ chunks (2..64,
 *                   default 8, the one being sent among them); no fadvise, so not with CHUNKCAST_AHEAD, --stdin or --device.
 *                   A chunk it cannot read whole fails the pass by name, after the chunks before it and with none of its own.
 *                   FILE holds "A-B,C-D": a repair pass sends only those. A file, not an
 *                   argument: 20% of a large partition missing in scattered runs is more
 *                   than one argv entry may hold (E2BIG).
 *   chunkcast send  ... --index FILE --stdin ...      (#3161: a direct send -- the source is a disk, not a file)
 *                   The partition file arrives ONCE on stdin, in order, as chunkcut writes it
 *                   while the disk is read; the index is the one the FIRST read of that disk
 *                   wrote, and the receivers already hold it. Every frame must begin and end
 *                   where that index says: a stream that does not is another disk's, and
 *                   nothing more of it is sent (exit 9). With --chunks-file the frames that
 *                   are not in the list are read and dropped -- a repair pass is another read.
 *   chunkcast send  ... --index FILE --device NODE [--head FILE] --level N --chunks-file FILE ...   (#3209)
 *                   A repair of a direct send WITHOUT that read: each listed chunk is read from its
 *                   own place on the device (through the bit map of the head frame, as the writer
 *                   writes it), checked against the index's XXH64 -- a disk that changed since it
 *                   was indexed is refused there (exit 9) -- and compressed as chunkcut compressed it.
 *                   The frame must come out at the length the index holds, or it is refused too.
 *   chunkcast serve --listen ADDR:PORT --token-file FILE [--allow IP] --index FILE --device NODE [--head FILE] --level N
 *                   The same chunks, to one that asks: a minimal HTTP/1.1 server that answers
 *                   "Range: bytes=A-B" where A..B are whole frames of the index (format-spec 8) --
 *                   what a receiver's fetch asks the image server for, when the image is a disk
 *                   in this machine. 206 and the frames, 416 for any other range. A request answers
 *                   only when it carries the round's token (X-Imagesrv-Serve-Token, compared in
 *                   constant time; 403 otherwise). The token is read from --token-file -- never an
 *                   argument, which ps shows -- and a serve without one does not start. --allow
 *                   narrows it further to one peer (the image server), and is never enough alone.
 *                   One request, read within SV_READ_MS from the connection's start, one answer.
 *   chunkcast serve ... --until-s N --read-mibs M     (B29: a cloner that serves a neighbour CLONER)
 *                   --until-s: the server ends by itself N seconds after it began -- its own clock, not
 *                   a shell's kill -- and dies with the shell that started it (PR_SET_PDEATHSIG).
 *                   --read-mibs: at most M MiB/s read from the device, the whole server long (4MiB a
 *                   chunk, paced before each read): the reads of the cloner that serves are ITS ceiling,
 *                   kept here and not by the one that asks. Either one without the other is refused,
 *                   and with them --allow is required and --listen may not be 0.0.0.0.
 *   chunkcast recv  --listen ADDR:PORT --session HEX16 --part N --pass N
 *                   --index FILE --index-xxh64 HEX16 (--head FILE | --raw)
 *                   [--b3-file FILE --b3-file-hash HEX64]
 *                   --state FILE [--iface IF] [--start-s N] [--stall-s N]
 *                   [--sync-ms N] [--from ADDR] [--feed FIFO --feed-chunks FILE] <target> [target...]
 *                   [--feedback on|off] [--control-port PORT] (before targets)
 *                   --from: only packets whose source is ADDR -- the server's, named in
 *                   the wave's instruction -- are taken. Any other source is counted, said
 *                   once by name, and dropped before its header is read: it starts no
 *                   stream, resets no stall clock and its ANNOUNCE contradicts nothing
 *                   (Nadav 14/11, #1079/#2485). A source address is not a signature; the
 *                   VLAN and the switch are what stop a forged one. Without --from every
 *                   source is taken, as before, and no `stray` line is said (not read, not 0).
 *                   --feed FIFO --feed-chunks FILE (B5, #3064): HTTP BESIDE the stream, into the SAME writers.
 *                   FIFO carries the frames of the chunks in FILE ("A-B,C-D"), in ascending order, as apply's
 *                   stdin does; the agent's curl writes them when the server's answer is fetch_parallel (B5's
 *                   decision, which comes AFTER this receiver said `listening`): so FILE is read when the first
 *                   byte reaches the FIFO -- the agent writes it whole before curl starts -- and the FIFO is
 *                   opened read-write, at the start. A thread of its own reads each frame -- ALL of its bytes, also
 *                   when the chunk is not needed (the next frame is where the index says) -- checks it as
 *                   any chunk (index, XXH64, BLAKE3: a forged one is refused, the feed ends, the stream
 *                   goes on), and hands it to the writers of the drawers that lack it. One writer a drawer
 *                   stays: the feeder never touches a disk. A chunk is claimed (`inflight`) before it is
 *                   read into a buffer, so the stream and the feeder never write the same chunk twice, and
 *                   it enters a drawer's queue only while that queue is under half full -- the stream always
 *                   has room. After the rxstats line: `feed <word> <taken> <skipped> <bad>`; word: ended (the list is
 *                   done), idle (no byte ever came), bad-chunk, bad-list (FILE is not a list), stalled (no byte for
 *                   --stall-s once it had begun), stopped (the pass ended first).
 *                   #3518: the frames are verified (zstd, XXH64, BLAKE3) by VERIFIER THREADS -- CHUNKCAST_VERIFIERS of them
 *                   (1..8), else half the cores this process may run on, 1..4 -- from one queue of 4 frames a thread plus 4
 *                   (at most 32, and at most a sixteenth of MemAvailable). A whole chunk that finds the queue full is NOT
 *                   dropped: it waits in its assembly slot, counted (`deferred`) and in the feedback's backlog, until the queue
 *                   has room; only when every slot holds one does the next chunk's first packet take the oldest's place
 *                   (`busy`, counted as before). After the rxstats line: `verifier threads=N queue=Q deferred=D work_ms=W
 *                   wait_ms=B` (rxstats keeps its twelve keys).
 *   chunkcast apply --part N --index FILE --index-xxh64 HEX16 (--head FILE | --raw) [--b3-file FILE --b3-file-hash HEX64]
 *                   --state FILE (--chunks-file FILE | --first K --count N) [--sync-ms N]
 *                   <target> [target...]
 *                   stdin = the frames of those chunks, in ascending order, one after the
 *                   other -- as HTTP Range requests on one connection gave them. ONE
 *                   process for the whole list: a scattered repair is tens of thousands of
 *                   ranges, and a process, a state load, an fsync and a state write for
 *                   each of them was the whole cost of it. The targets are written by the
 *                   same writers as recv's, flushed every --sync-ms, and the state file
 *                   follows each flush -- so whoever watches it sees the fetch advance.
 *   chunkcast --version   "chunkcast <protocol version>", exit 0: it is installed and it runs
 *   chunkcast b3list      (#3332) stdin -> stdout: the BLAKE3 of every 4MiB of it, 32 bytes each (the last
 *                         piece short) -- the image server's BLAKE3 list, from the data stream it derives
 *                         the index from (server/agent/readbackhash.py). Exit 0, or 1: stdin or stdout failed.
 *   chunkcast b3sum       (#3332) stdin -> "<64 hex>\n", the BLAKE3 of all of it: the list's own, for the manifest
 *
 * The sender holds TWO rates, and the slower one rules at every moment:
 * --kbps is what goes on the wire (the wave's bitrate, as udp-sender's
 * --max-bitrate), and --write-kbps is what the receivers must WRITE -- the
 * chunks' decompressed bytes. udpcast needed no second number: a receiver that
 * could not write held the sender back. Nothing holds this sender back, and a
 * run of zeroes is one packet a 4MiB chunk -- a gigabyte of writes in a
 * millisecond of stream, which no drive follows and no repair pass would fix.
 *
 * A chunk reaches a disk only this way, in `recv` and in `apply` alike:
 *   1. its frame is whole, decompresses to exactly the chunk's length, the
 *      XXH64 of those bytes is the index's, and -- with --b3-file -- their BLAKE3
 *      is the list's: BEFORE a byte is written. A chunk that fails is not
 *      written; it stays missing (principle 4).
 *   2. it is written at its place: through the partclone bit map of the head
 *      frame, or at k * 4MiB of a raw partition.
 *   3. the target is flushed (fdatasync), and only chunks written before that
 *      flush BEGAN count for it (format-spec 9.1, rule 1). The flush runs on a
 *      thread of its own, so the writer is not held while the drive empties its
 *      cache -- as writeback.sh's periodic fsync runs beside partclone today.
 * Only then is the chunk "durable" and enters the state file -- the cumulative
 * ranges the agent reports to the server. A flush that FAILS fails the target,
 * by name (principle 5), and adds nothing.
 *
 * What this program does NOT do is read the disk back. That is readback.sh's
 * job after the partition, as today (#1805, sample or full), and the chunk
 * path does not skip it: re-reading every chunk here, between flushes, costs
 * the drive a second pass (stream <= W*R/(W+R), half a drive's speed) and is
 * not what a stream paced for the slowest drive can afford.
 *
 * The state file ("chunkcast-state 1 <index xxh> <part> <chunks>", then a line
 * a target: "<path> ok|failed <durable> <ranges|-> [reason]") is rewritten
 * atomically; a later pass, or `apply`, starts from it. It lives in the agent's
 * run directory (RAM), never on a disk being written (Nadav, 12/11).
 *
 * recv exits: 0 the pass ended (END seen) or every live target is complete;
 * 3 no stream within --start-s; 4 the stream went quiet for --stall-s; 5 no
 * target is left; 6 stopped by a signal; 7 the stream announced another index
 * and not one chunk of it matched ours;
 * 2 usage or setup. The state file is written on every one of them from 0 to 7.
 * apply exits: 0, 5, or 8 = a fetched chunk was short or did not match.
 * send exits: 0 the pass went out whole; 1 the file or the socket failed; 6 stopped by a signal;
 * 9 (--stdin) the stream is not the file its index describes, or it ended early or late.
 * send says on stdout, once a second, `sent <chunks> <bytes>` and then `paced <wire_ms> <disk_ms>` -- how long
 * it has slept for --kbps and for --write-kbps (#3125, V4: which of the two held it back) -- and at the end
 * `paced ...` once more and `done <chunks> <bytes> <ms>`.
 *
 * One drive that stops taking writes fails alone, as in fanout (#674, #1796): a
 * target whose write or flush has not returned for CC_WRITE_STALL_MS is failed
 * by name and no longer waited for -- its neighbours finish the pass, and the
 * receiver still ends and says what is durable on each.
 *
 * The map helpers, XXH64 and CRC-32 are fanout.c's own, by
 * inclusion: the writer here is the mirror of `fanout --readback`, and two
 * copies of "which block is chunk k" would be two answers. fanout.c is not
 * edited for it. No line of udpcast or partclone (GPL) is used. From filecast
 * (github.com/gistrec/filecast, MIT -- read, not copied; THIRD_PARTY_NOTICES.md):
 * the packet shape (announce / data / end, a session id in every packet); a
 * stall clock that only OUR packets reset (its refreshDeadline, Receiver.cpp);
 * and an ANNOUNCE that contradicts what we hold is ignored, not obeyed
 * ("ignoring conflicting announcement", Receiver.cpp) -- one forged packet
 * must not be able to stop a receiver whose every chunk is checked anyway.
 * What is deliberately NOT filecast's: its receivers ask for every missing
 * part over UDP, back to back, each on its own (Receiver.cpp checkParts);
 * here nobody asks for retransmission over UDP -- the server reads the ledgers.
 */
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wunused-function"
#define main fanout_main
#include "fanout.c"
#undef main
#pragma GCC diagnostic pop

#include "blake3/blake3.h"          /* blake3/blake3_one.c and its two .S files, built beside this one (#3332) */

#include <arpa/inet.h>
#include <net/if.h>
#include <netinet/in.h>
#include <poll.h>
#include <pthread.h>

/* libzstd by its file name (-l:libzstd.so.1), and its two calls declared here
 * rather than taken from zstd.h -- as chunkcut.c does (stage 2). The header
 * comes with libzstd-dev, a package a server in the field does not carry when
 * it rebuilds this image offline; the library itself is on every Debian (apt
 * needs it). Both calls are libzstd's stable API since 1.0. */
size_t ZSTD_decompress(void *dst, size_t dstCapacity, const void *src, size_t compressedSize);
unsigned ZSTD_isError(size_t code);
/* #3209: and chunkcut's own three -- a chunk read from the device is compressed here exactly as there */
typedef struct ZSTD_CCtx_s ZSTD_CCtx;
ZSTD_CCtx *ZSTD_createCCtx(void);
size_t ZSTD_CCtx_setParameter(ZSTD_CCtx *cctx, int param, int value);
size_t ZSTD_compress2(ZSTD_CCtx *cctx, void *dst, size_t cap, const void *src, size_t n);

#define CC_CHUNK RB_CHUNK               /* 4MiB: the format's, and fanout --readback -x's */
#define CC_ZMAX (CC_CHUNK + (64u << 10)) /* a frame longer than this is not a 4MiB chunk's */
#define CC_MAGIC 0x49534331u            /* "ISC1" */
#define CC_VERSION 1                    /* the wire protocol; a packet of another version is not ours */
#define CC_HDR 16                       /* magic, type, version, partition, session */
#define CC_DATA_HDR 16                  /* chunk, frame length, packet number */
#include "chunkcast_fec.h"
#define CC_PKT (CC_HDR + CC_DATA_HDR + CC_PAYLOAD)
#define CC_BATCH 64                     /* packets per recvmmsg / sendmmsg */
#define CC_SLOTS 4                      /* chunks being assembled at once */
#define CC_VQ 4                         /* whole frames waiting for ONE verifier -- and the least the queue ever holds */
#define CC_VER_MAX 8                    /* verifier threads, at most (#3518) */
#define CC_VQ_MAX 32                    /* whole frames waiting for all of them, at most */
#define CC_QCAP 8                       /* verified chunks waiting for one target */
#define CC_QCAP_MAX 256
#define CC_QCAP_BYTES_MAX (2ULL << 30)
static int qcap = CC_QCAP;              /* #3522: opt-in experiment, unchanged default */
#define CC_RCVBUF (16 << 20)            /* asked for, read back and said (#2994) */
#define CC_WRITE_STALL_MS 120000        /* fanout's STALL_EVICT_MS; CHUNKCAST_WRITE_STALL_MS in tests */
enum { CC_ANNOUNCE = 1, CC_DATA = 2, CC_END = 3, CC_PARITY = 4 };

struct cc_index {
    int raw;
    uint64_t head_bytes, head_zbytes, head_xxh, data_bytes, n, file_zbytes, self_xxh;
    uint64_t *off, *xxh;
    uint32_t *zlen, zmax;
    unsigned char *b3;          /* #3332: 32 bytes a chunk, the BLAKE3 list; NULL = XXH64 alone, and said */
};

struct cc_map {                 /* a partclone partition: where chunk k begins on the device */
    unsigned char *bm;
    uint64_t total, bs, *start; /* start[k] = the bit of the used block holding byte k*4MiB */
};

struct cc_buf {                 /* one verified, decompressed chunk */
    uint64_t k;
    uint32_t len;
    int refs;
    unsigned char *data;
};

struct cc_tgt {
    const char *path;
    int wfd, alive, stop, hung;
    uint64_t nhave, skipped;
    uint64_t written, writing;          /* feedback: completed writes and bytes in the current write */
    long long busy_ms;                  /* when the write or flush now running began; 0 = none is */
    unsigned char *have, *pend, *snap;  /* durable; written, not flushed; the flush's own copy */
    char reason[96];
    pthread_t th, flusher;
    pthread_mutex_t mu;
    pthread_cond_t cv;
    struct cc_buf *q[CC_QCAP_MAX];
    int qh, qn;
};

static struct cc_index ix;
static struct cc_map mp;
static struct cc_tgt tg[MAX_TARGETS];
static int ntg, cc_part;
static const char *state_path;
static long long sync_ms = 2000;
static pthread_mutex_t st_mu = PTHREAD_MUTEX_INITIALIZER;   /* have, pend, alive, inflight */
static unsigned char *inflight;                             /* chunk k is in the pipeline */
static int state_dirty;
static unsigned long long n_flushes, n_state_writes;
static volatile sig_atomic_t cc_stop;

static void cc_on_signal(int sig) { (void)sig; cc_stop = 1; }
/* apply waits in read() for the next frame: closing its input is what ends that wait. */
static void cc_on_signal_apply(int sig) { (void)sig; cc_stop = 1; close(STDIN_FILENO); }

static uint64_t be_get(const unsigned char *p, int n)
{
    uint64_t v = 0;
    for (int i = 0; i < n; i++) v = v << 8 | p[i];
    return v;
}

static void be_put(unsigned char *p, uint64_t v, int n)
{
    while (n-- > 0) { p[n] = (unsigned char)v; v >>= 8; }
}

static uint64_t cc_xxh(const void *p, size_t n)
{
    struct xxh64 h;
    xxh_init(&h);
    xxh_update(&h, p, n);
    return xxh_digest(&h);
}

/* A decimal with no sign and no leading zero, ended by `end`. */
static int cc_dec(const char **s, char end, uint64_t *v)
{
    const char *p = *s;
    uint64_t x = 0;
    if (*p < '0' || *p > '9' || (*p == '0' && p[1] >= '0' && p[1] <= '9')) return -1;
    for (; *p >= '0' && *p <= '9'; p++) {
        if (x > (UINT64_MAX - 9) / 10) return -1;
        x = x * 10 + (uint64_t)(*p - '0');
    }
    if (*p != end) return -1;
    *s = p + 1;
    *v = x;
    return 0;
}

static int cc_hex16(const char **s, char end, uint64_t *v)
{
    const char *p = *s;
    uint64_t x = 0;
    for (int i = 0; i < 16; i++, p++) {
        if (*p >= '0' && *p <= '9') x = x << 4 | (uint64_t)(*p - '0');
        else if (*p >= 'a' && *p <= 'f') x = x << 4 | (uint64_t)(*p - 'a' + 10);
        else return -1;
    }
    if (*p != end) return -1;
    *s = p + 1;
    *v = x;
    return 0;
}

static uint64_t cc_len(uint64_t k)
{
    return k + 1 == ix.n ? ix.data_bytes - k * CC_CHUNK : CC_CHUNK;
}

/* The whole of a file, NUL-ended. NULL = it did not arrive whole (said). */
static char *cc_slurp(const char *path, size_t limit, size_t *len)
{
    struct stat st;
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    char *buf = NULL;
    if (fd < 0 || fstat(fd, &st) || st.st_size <= 0 || (uint64_t)st.st_size > limit ||
        !(buf = malloc((size_t)st.st_size + 1)) || rb_read_all(fd, (unsigned char *)buf, (size_t)st.st_size)) {
        ir_say("chunkcast: %s cannot be read whole: %s\n", path, fd < 0 ? strerror(errno) : "size or read");
        free(buf);
        buf = NULL;
    } else {
        buf[st.st_size] = 0;
        *len = (size_t)st.st_size;
    }
    if (fd >= 0) close(fd);
    return buf;
}

/* The index, with every consistency rule of format-spec 4. `want`: the
 * manifest's index_xxh64 -- the file is not believed before it matches. */
static int cc_index_load(const char *path, const char *want)
{
    size_t len = 0;
    char *text = cc_slurp(path, 256u << 20, &len);
    const char *p = text, *why = "the header line";
    uint64_t v, at;
    if (!text) return -1;
    ix.self_xxh = cc_xxh(text, len);
    if (want) {
        const char *w = want;
        if (cc_hex16(&w, 0, &v) || v != ix.self_xxh) {
            ir_say("chunkcast: %s is not the manifest's index (%016llx, not %s) -- nothing is believed\n",
                   path, (unsigned long long)ix.self_xxh, want);
            goto bad;
        }
    }
    if (strlen(text) != len || strncmp(p, "IMAGESRV-IDX 2 ", 15)) goto refuse;
    p += 15;
    if (!strncmp(p, "partclone ", 10)) p += 10;
    else if (!strncmp(p, "raw ", 4)) { p += 4; ix.raw = 1; }
    else goto refuse;
    if (cc_dec(&p, ' ', &v) || v != CC_CHUNK || cc_dec(&p, ' ', &ix.head_bytes) ||
        cc_dec(&p, ' ', &ix.head_zbytes)) goto refuse;
    if (ix.raw ? (p[0] != '-' || p[1] != ' ') : cc_hex16(&p, ' ', &ix.head_xxh)) goto refuse;
    if (ix.raw) p += 2;
    if (cc_dec(&p, ' ', &ix.data_bytes) || cc_dec(&p, ' ', &ix.n) || cc_dec(&p, '\n', &ix.file_zbytes)) goto refuse;
    why = "the header's own sums";
    if (ix.raw != (ix.head_bytes == 0) || ix.raw != (ix.head_zbytes == 0) || ix.n > (1ULL << 26) ||
        ix.data_bytes > ix.n * CC_CHUNK || (ix.n && ix.data_bytes <= (ix.n - 1) * CC_CHUNK) ||
        ix.head_bytes > (1ULL << 32)) goto refuse;
    ix.off = calloc((size_t)ix.n + 1, sizeof *ix.off);
    ix.xxh = calloc((size_t)ix.n + 1, sizeof *ix.xxh);
    ix.zlen = calloc((size_t)ix.n + 1, sizeof *ix.zlen);
    if (!ix.off || !ix.xxh || !ix.zlen) goto bad;
    why = "a chunk line";
    at = ix.head_zbytes;
    for (uint64_t k = 0; k < ix.n; k++) {
        if (cc_dec(&p, ' ', &ix.off[k]) || cc_dec(&p, ' ', &v) || cc_hex16(&p, '\n', &ix.xxh[k]) ||
            ix.off[k] != at || v == 0 || v > CC_ZMAX) goto refuse;
        ix.zlen[k] = (uint32_t)v;
        if (ix.zlen[k] > ix.zmax) ix.zmax = ix.zlen[k];
        at += v;
    }
    why = "its end";
    if (*p || at != ix.file_zbytes) goto refuse;
    free(text);
    return 0;
    /* semgrep's double-free (3x in this file) is a false positive: the `free` above is followed by `return`,
     * and every `goto` to the labels below comes before it, so no path frees twice (#3891). */
refuse:
    ir_say("chunkcast: %s is not a format-2 index (%s) -- refused\n", path, why);
bad:
    free(text);   /* nosemgrep: c.lang.security.double-free.double-free */
    return -1;
}

/* #3332: the partition's BLAKE3 list, after the index: exactly 32 bytes for each of its chunks, and
 * believed only when its own BLAKE3 is `want` -- the manifest's chunk_b3, 64 lowercase hex digits. */
static int cc_b3_load(const char *path, const char *want)
{
    static const char hex[] = "0123456789abcdef";
    unsigned char got[32], need[32];
    size_t len = 0;
    for (int i = 0; i < 64; i++) {
        const char *d = want[i] ? strchr(hex, want[i]) : NULL;
        if (!d) {
            ir_say("chunkcast: --b3-file-hash is not 64 lowercase hex digits\n");
            return -1;
        }
        need[i / 2] = (unsigned char)(i % 2 ? need[i / 2] | (d - hex) : (d - hex) << 4);
    }
    if (want[64]) {
        ir_say("chunkcast: --b3-file-hash is not 64 lowercase hex digits\n");
        return -1;
    }
    struct stat st;
    char *list = ix.n ? cc_slurp(path, (size_t)ix.n * 32 + 1, &len)
               : !stat(path, &st) && !st.st_size ? calloc(1, 1) : NULL;     /* no chunks: an empty list, and it is there */
    if (!list) {
        if (!ix.n) ir_say("chunkcast: %s is not the empty list of a partition with no chunks\n", path);
        return -1;
    }
    blake3_hasher h;
    blake3_hasher_init(&h);
    blake3_hasher_update(&h, list, len);
    blake3_hasher_finalize(&h, got, 32);
    if (memcmp(got, need, 32)) {
        ir_say("chunkcast: %s is not the manifest's BLAKE3 list -- nothing is believed\n", path);
        free(list);
        return -1;
    }
    if (len != (size_t)ix.n * 32) {
        ir_say("chunkcast: %s holds %zu bytes, the index has %llu chunks of 32 -- refused\n", path, len,
               (unsigned long long)ix.n);
        free(list);
        return -1;
    }
    ix.b3 = (unsigned char *)list;
    return 0;
}

/* #3332: both, or neither -- and neither is said, once, in the words the agent's log carries. */
static int cc_b3_setup(const char *file, const char *hash)
{
    if (!file != !hash) {
        ir_say("chunkcast: --b3-file and --b3-file-hash go together\n");
        return -1;
    }
    if (file) return cc_b3_load(file, hash);
    ir_say("chunkcast: no BLAKE3 list (--b3-file): the chunks are checked against XXH64 alone\n");
    return 0;
}

/* The head frame: the partclone header, the bit map and its CRC. It is
 * decompressed here and believed only when its XXH64 is the index's. */
static int cc_head_load(const char *path)
{
    size_t zlen = 0, bm_len;
    unsigned char *z = (unsigned char *)cc_slurp(path, 1u << 30, &zlen), *head = NULL;
    const char *why = "its length is not the index's";
    uint64_t used, seen = 0, j = 0, k = 0;
    size_t got;
    if (!z) return -1;
    if (zlen != ix.head_zbytes || !(head = malloc((size_t)ix.head_bytes))) goto refuse;
    why = "it does not decompress to the index's head";
    got = ZSTD_decompress(head, (size_t)ix.head_bytes, z, zlen);
    if (ZSTD_isError(got) || got != ix.head_bytes || cc_xxh(head, got) != ix.head_xxh) goto refuse;
    why = "not a partclone v2 header with a bit map and no checksums (-a0)";
    if (ix.head_bytes < RB_HEAD + 4 || memcmp(head, "partclone-image", 15) || memcmp(head + 30, "0002", 4) ||
        rb_le(head + 34, 2) != 0xC0DE || rb_le(head + 92, 2) != 2 || rb_le(head + 96, 2) ||
        rb_le(head + 98, 2) || head[105] != 1 || rb_crc32(head, RB_HEAD - 4) != rb_le(head + 106, 4)) goto refuse;
    mp.total = rb_le(head + 60, 8);
    used = rb_le(head + 76, 8);
    mp.bs = rb_le(head + 84, 4);
    why = "an implausible map";
    if (!mp.bs || mp.bs > (64u << 20) || !mp.total || mp.total > (1ULL << 34) || used > mp.total) goto refuse;
    bm_len = (size_t)((mp.total + 7) / 8);
    if (ix.head_bytes != RB_HEAD + bm_len + 4 || used * mp.bs != ix.data_bytes) goto refuse;
    mp.bm = head + RB_HEAD;
    why = "the bit map failed its CRC or its count";
    if (rb_crc32(mp.bm, bm_len) != rb_le(mp.bm + bm_len, 4)) goto refuse;
    mp.start = calloc((size_t)ix.n + 1, sizeof *mp.start);
    if (!mp.start) goto refuse;
    for (uint64_t b = rb_next(mp.bm, 0, mp.total); b < mp.total; b = rb_next(mp.bm, b + 1, mp.total), seen++)
        for (; k < ix.n && j == seen; j = ++k * CC_CHUNK / mp.bs)
            mp.start[k] = b;
    if (seen != used || k != ix.n) goto refuse;
    free(z);
    return 0;                           /* head stays: mp.bm points into it */
refuse:
    ir_say("chunkcast: the head frame %s is refused: %s\n", path, why);
    free(z);   /* nosemgrep: c.lang.security.double-free.double-free */
    free(head);
    return -1;
}

/* Every contiguous run of the device that chunk k's `len` bytes live on, in
 * stream order: io(ctx, device offset, bytes, offset in the chunk). A chunk may
 * begin inside a block (format-spec 2: block_size need not divide 4MiB). */
static int cc_walk(uint64_t k, uint64_t len, int (*io)(void *, uint64_t, uint64_t, uint64_t), void *ctx)
{
    if (ix.raw) return io(ctx, k * CC_CHUNK, len, 0);
    uint64_t within = (k * CC_CHUNK) % mp.bs, b = mp.start[k], pos = 0;
    while (pos < len) {
        b = rb_next(mp.bm, b, mp.total);
        if (b >= mp.total) return -1;   /* the map ended before the chunk did */
        uint64_t e = b + 1, have = mp.bs - within;
        while (have < len - pos && e < mp.total && rb_bit(mp.bm, e)) { e++; have += mp.bs; }
        uint64_t n = have < len - pos ? have : len - pos;
        if (io(ctx, b * mp.bs + within, n, pos)) return -1;
        pos += n;
        b = e;
        within = 0;
    }
    return 0;
}

struct cc_io { struct cc_tgt *t; const unsigned char *data; };

static int cc_io_write(void *ctx, uint64_t off, uint64_t n, uint64_t pos)
{
    struct cc_io *c = ctx;
    return ir_write(c->t->wfd, c->data + pos, n, off);
}

static int cc_bit(const unsigned char *bits, uint64_t k) { return (bits[k >> 3] >> (k & 7)) & 1; }

static void cc_fail(struct cc_tgt *t, const char *reason)
{
    pthread_mutex_lock(&st_mu);
    if (t->alive) {
        t->alive = 0;
        snprintf(t->reason, sizeof t->reason, "%s", reason);
        ir_say("chunkcast: %s failed: %s\n", t->path, reason);
    }
    state_dirty = 1;
    pthread_mutex_unlock(&st_mu);
}

/* 0 = the target is open for writing and is large enough. A regular file is a target only in a test (CHUNKCAST_ALLOW_FILE=1):
 * on a machine it is a node that is not a disk, and writing "the partition"
 * into it would pass every hash (#51). */
static int cc_open(struct cc_tgt *t)
{
    struct stat st;
    uint64_t need = ix.raw ? ix.data_bytes : mp.total * mp.bs, cap = 0;
    const char *allow = getenv("CHUNKCAST_ALLOW_FILE");
    t->wfd = open(t->path, O_WRONLY | O_CLOEXEC);          /* never creates or truncates a node */
    if (t->wfd < 0 || fstat(t->wfd, &st)) return -1;
    if (S_ISBLK(st.st_mode)) { if (ioctl(t->wfd, BLKGETSIZE64, &cap)) return -1; }
    else if (S_ISREG(st.st_mode) && allow && !strcmp(allow, "1")) cap = (uint64_t)st.st_size;
    else return -1;
    return cap < need ? -1 : 0;
}

static int cc_live(struct cc_tgt *t)
{
    pthread_mutex_lock(&st_mu);
    int alive = t->alive;
    pthread_mutex_unlock(&st_mu);
    return alive;
}

static void cc_write(struct cc_tgt *t, uint64_t k, const unsigned char *data, uint64_t len)
{
    struct cc_io c = { .t = t, .data = data };
    __atomic_store_n(&t->busy_ms, now_ms(), __ATOMIC_RELEASE);
    int failed = cc_walk(k, len, cc_io_write, &c);
    __atomic_store_n(&t->busy_ms, 0, __ATOMIC_RELEASE);
    if (failed) {
        char why[96];
        snprintf(why, sizeof why, "write error at chunk %llu: %s", (unsigned long long)k, strerror(errno));
        cc_fail(t, why);
        return;
    }
    pthread_mutex_lock(&st_mu);
    t->pend[k >> 3] |= (unsigned char)(1u << (k & 7));
    t->written++;
    pthread_mutex_unlock(&st_mu);
}

/* Flush; the chunks written BEFORE the flush began become durable when it
 * succeeds. One written while it ran waits for the next. `last`: fsync. */
static void cc_sync(struct cc_tgt *t, int last)
{
    size_t bytes = (size_t)(ix.n / 8 + 1);
    int any = 0;
    pthread_mutex_lock(&st_mu);
    memcpy(t->snap, t->pend, bytes);
    memset(t->pend, 0, bytes);
    for (size_t i = 0; i < bytes && !any; i++) any = t->snap[i] != 0;
    any = any && t->alive;
    pthread_mutex_unlock(&st_mu);
    if (!any) return;
    __atomic_store_n(&t->busy_ms, now_ms(), __ATOMIC_RELEASE);
    int failed = last ? fsync(t->wfd) : fdatasync(t->wfd);
    __atomic_store_n(&t->busy_ms, 0, __ATOMIC_RELEASE);
    if (failed) {
        cc_fail(t, "the flush to the disk failed");
        return;
    }
    pthread_mutex_lock(&st_mu);
    n_flushes++;
    for (uint64_t k = 0; k < ix.n; k++)
        if (cc_bit(t->snap, k) && !cc_bit(t->have, k)) {
            t->have[k >> 3] |= (unsigned char)(1u << (k & 7));
            t->nhave++;
        }
    state_dirty = 1;
    pthread_mutex_unlock(&st_mu);
}

static void cc_ranges(FILE *f, const struct cc_tgt *t)
{
    int any = 0;
    for (uint64_t k = 0; k < ix.n; k++) {
        if (!cc_bit(t->have, k)) continue;
        uint64_t e = k;
        while (e + 1 < ix.n && cc_bit(t->have, e + 1)) e++;
        fprintf(f, "%s%llu-%llu", any++ ? "," : "", (unsigned long long)k, (unsigned long long)e);
        k = e;
    }
    if (!any) fputc('-', f);
}

/* 0 = the state file now says what is durable. Atomic: a reader never sees half. */
static int cc_state_write(void)
{
    char tmp[4096];
    FILE *f;
    if (snprintf(tmp, sizeof tmp, "%s.tmp", state_path) >= (int)sizeof tmp || !(f = fopen(tmp, "w"))) return -1;
    pthread_mutex_lock(&st_mu);
    state_dirty = 0;
    n_state_writes++;
    fprintf(f, "chunkcast-state 1 %016llx %d %llu\n", (unsigned long long)ix.self_xxh, cc_part,
            (unsigned long long)ix.n);
    for (int i = 0; i < ntg; i++) {
        fprintf(f, "%s %s %llu ", tg[i].path, tg[i].alive ? "ok" : "failed", (unsigned long long)tg[i].nhave);
        cc_ranges(f, &tg[i]);
        fprintf(f, "%s%s\n", tg[i].alive ? "" : " ", tg[i].alive ? "" : tg[i].reason);
    }
    pthread_mutex_unlock(&st_mu);
    if (ferror(f) | fclose(f) || rename(tmp, state_path)) {
        ir_say("chunkcast: the state file %s could not be written: %s\n", state_path, strerror(errno));
        return -1;
    }
    return 0;
}

/* An earlier pass's state: what each target already holds. A state of another
 * index, partition or chunk count is refused -- never merged. No file = a first pass. */
static int cc_state_load(void)
{
    size_t len = 0;
    struct stat st;
    if (stat(state_path, &st) && errno == ENOENT) return 0;
    char *text = cc_slurp(state_path, 64u << 20, &len), *line, *save = NULL;
    char want[96];
    if (!text) return -1;
    snprintf(want, sizeof want, "chunkcast-state 1 %016llx %d %llu", (unsigned long long)ix.self_xxh, cc_part,
             (unsigned long long)ix.n);
    line = strtok_r(text, "\n", &save);
    if (!line || strcmp(line, want)) {
        ir_say("chunkcast: the state file %s is of another image or partition -- refused\n", state_path);
        free(text);
        return -1;
    }
    while ((line = strtok_r(NULL, "\n", &save))) {
        char *sp = strchr(line, ' '), *verdict, *ranges, *rest;
        struct cc_tgt *t = NULL;
        if (!sp) goto bad;
        *sp = 0;
        for (int i = 0; i < ntg; i++) if (!strcmp(tg[i].path, line)) t = &tg[i];
        verdict = sp + 1;
        if (!(sp = strchr(verdict, ' ')) || !(ranges = strchr(sp + 1, ' '))) goto bad;
        *sp = 0;
        ranges++;
        if ((rest = strchr(ranges, ' '))) *rest++ = 0;
        if (!t) continue;                       /* a target that is not ours this time */
        if (!strcmp(verdict, "failed")) {
            t->alive = 0;
            snprintf(t->reason, sizeof t->reason, "%s", rest ? rest : "failed in an earlier pass");
        } else if (strcmp(verdict, "ok")) goto bad;
        for (const char *p = ranges; strcmp(ranges, "-") && *p;) {
            uint64_t a, b;
            if (cc_dec(&p, '-', &a)) goto bad;
            const char *q = p;
            if (cc_dec(&q, ',', &b)) { q = p; if (cc_dec(&q, 0, &b)) goto bad; q--; }
            if (a > b || b >= ix.n) goto bad;
            for (uint64_t k = a; k <= b; k++)
                if (!cc_bit(t->have, k)) { t->have[k >> 3] |= (unsigned char)(1u << (k & 7)); t->nhave++; }
            p = q;
        }
    }
    free(text);
    return 0;
bad:
    ir_say("chunkcast: the state file %s is damaged -- refused\n", state_path);
    free(text);   /* nosemgrep: c.lang.security.double-free.double-free */
    return -1;
}

/* One frame -> the chunk, into `out` (4MiB). 0 only when its length and XXH64 are the index's -- and,
 * with a BLAKE3 list, its BLAKE3 is the list's (#3332): XXH64 can be matched on purpose, BLAKE3 cannot. */
static int cc_verify(uint64_t k, const unsigned char *z, unsigned char *out)
{
    size_t got = ZSTD_decompress(out, CC_CHUNK, z, ix.zlen[k]);
    const char *why = ZSTD_isError(got) ? "the frame is damaged" : got != cc_len(k) ? "wrong length"
                    : cc_xxh(out, got) != ix.xxh[k] ? "wrong XXH64" : NULL;
    if (!why && ix.b3) {
        unsigned char sum[32];
        blake3_hasher h;
        blake3_hasher_init(&h);
        blake3_hasher_update(&h, out, got);
        blake3_hasher_finalize(&h, sum, 32);
        if (memcmp(sum, ix.b3 + k * 32, 32)) why = "wrong BLAKE3";
    }
    if (why) {
        ir_say("chunkcast: chunk %llu does not match the index (%s) -- not written\n", (unsigned long long)k, why);
        return -1;
    }
    return 0;
}

static int cc_alive(void)
{
    int n = 0;
    pthread_mutex_lock(&st_mu);
    for (int i = 0; i < ntg; i++) n += tg[i].alive;
    pthread_mutex_unlock(&st_mu);
    return n;
}

/* 1 = a live target exists and every live target holds every chunk. */
static int cc_complete(void)
{
    int live = 0, whole = 1;
    pthread_mutex_lock(&st_mu);
    for (int i = 0; i < ntg; i++)
        if (tg[i].alive) { live++; whole &= tg[i].nhave == ix.n; }
    pthread_mutex_unlock(&st_mu);
    return live && whole;
}

/* --- the shared setup of recv and apply ------------------------------------ */

struct cc_args {
    long long rate_min, rate_max, rate_cap, control_port;
    int feedback;
    const char *addr, *iface, *session, *index, *index_xxh, *head, *file, *chunks, *device, *allow, *token_file, *from;
    const char *b3_file, *b3_hash;          /* #3332: recv and apply check with them; send and serve take them and leave it to the receiver */
    long long part, pass, first, count, start_s, stall_s, ttl, kbps, write_kbps, lead_ms, tail_ms, level;
    long long until_s, read_mibs;           /* serve (B29): its own end, and its own ceiling on what it reads */
    const char *feed, *feed_chunks;         /* recv (B5): a FIFO of frames, and the list of the chunks they are */
    int fec, raw, argi, from_stdin, mixed;
};

static int cc_num(const char *s, long long lo, long long hi, long long *v)
{
    uint64_t x;
    if (!s || cc_dec(&s, 0, &x) || x > (uint64_t)hi || (long long)x < lo) return -1;
    *v = (long long)x;
    return 0;
}

/* #3522: an invalid experiment must not silently run the defaults. */
static int cc_knob(const char *name, long long def, long long lo, long long hi, long long *out, int *was_set)
{
    const char *v = getenv(name);
    if (was_set) *was_set = v != NULL;
    if (!v) { *out = def; return 0; }
    if (!cc_num(v, lo, hi, out)) return 0;
    ir_say("chunkcast: %s=%s is not a whole number from %lld to %lld -- nothing is started\n", name, v, lo, hi);
    return -1;
}

/* #3535: only an explicit smooth value changes the pacing mode. */
static int cc_pace_mode(int *smooth)
{
    const char *v = getenv("CHUNKCAST_PACE");
    *smooth = 0;
    if (!v || !strcmp(v, "legacy")) return 0;
    if (!strcmp(v, "smooth")) { *smooth = 1; return 0; }
    ir_say("chunkcast: CHUNKCAST_PACE=%s is not legacy or smooth -- nothing is started\n", v);
    return -1;
}

/* #3535: only an explicit thread value moves the source reads to a thread. */
static int cc_reader_mode(int *thread)
{
    const char *v = getenv("CHUNKCAST_READER");
    *thread = 0;
    if (!v || !strcmp(v, "inline")) return 0;
    if (!strcmp(v, "thread")) { *thread = 1; return 0; }
    ir_say("chunkcast: CHUNKCAST_READER=%s is not inline or thread -- nothing is started\n", v);
    return -1;
}

static int cc_parse(int argc, char **argv, struct cc_args *a)
{
    *a = (struct cc_args){ .part = -1, .pass = -1, .first = -1, .count = -1, .start_s = 600, .stall_s = 180,
                           .ttl = 1, .lead_ms = 1000, .tail_ms = 2000, .feedback = 1 };
    int i = 2;
    for (; i < argc && !strncmp(argv[i], "--", 2); i++) {
        const char *o = argv[i] + 2, *v = i + 1 < argc ? argv[i + 1] : NULL;
        int bad = 0;
        if (!strcmp(o, "fec")) { a->fec = 1; continue; }
        if (!strcmp(o, "raw")) { a->raw = 1; continue; }
        if (!strcmp(o, "stdin")) { a->from_stdin = 1; continue; }
        if (!strcmp(o, "to") || !strcmp(o, "listen")) a->addr = v;
        else if (!strcmp(o, "iface")) a->iface = v;
        else if (!strcmp(o, "from")) a->from = v;
        else if (!strcmp(o, "session")) a->session = v;
        else if (!strcmp(o, "index")) a->index = v;
        else if (!strcmp(o, "index-xxh64")) a->index_xxh = v;
        else if (!strcmp(o, "b3-file")) a->b3_file = v;
        else if (!strcmp(o, "b3-file-hash")) a->b3_hash = v;
        else if (!strcmp(o, "head")) a->head = v;
        else if (!strcmp(o, "file")) a->file = v;
        else if (!strcmp(o, "device")) a->device = v;
        else if (!strcmp(o, "allow")) a->allow = v;
        else if (!strcmp(o, "token-file")) a->token_file = v;
        else if (!strcmp(o, "level")) bad = cc_num(v, 1, 19, &a->level);
        else if (!strcmp(o, "order")) {
            if (v && !strcmp(v, "seq")) a->mixed = 0;
            else if (v && !strcmp(v, "mixed")) a->mixed = 1;
            else bad = 1;
        }
        else if (!strcmp(o, "chunks-file")) a->chunks = v;
        else if (!strcmp(o, "state")) state_path = v;
        else if (!strcmp(o, "part")) bad = cc_num(v, 0, 65535, &a->part);
        else if (!strcmp(o, "pass")) bad = cc_num(v, 1, 1000000, &a->pass);
        else if (!strcmp(o, "first")) bad = cc_num(v, 0, 1LL << 26, &a->first);
        else if (!strcmp(o, "count")) bad = cc_num(v, 1, 1LL << 26, &a->count);
        else if (!strcmp(o, "start-s")) bad = cc_num(v, 1, 86400, &a->start_s);
        else if (!strcmp(o, "stall-s")) bad = cc_num(v, 1, 86400, &a->stall_s);
        else if (!strcmp(o, "sync-ms")) bad = cc_num(v, 1, 600000, &sync_ms);
        else if (!strcmp(o, "ttl")) bad = cc_num(v, 0, 255, &a->ttl);
        else if (!strcmp(o, "kbps")) bad = cc_num(v, 0, 100000000, &a->kbps);
        else if (!strcmp(o, "write-kbps")) bad = cc_num(v, 0, 100000000, &a->write_kbps);
        else if (!strcmp(o, "rate-min")) bad = cc_num(v, 1, 100000000, &a->rate_min);
        else if (!strcmp(o, "rate-max")) bad = cc_num(v, 1, 100000000, &a->rate_max);
        else if (!strcmp(o, "rate-cap")) bad = cc_num(v, 1, 100000000, &a->rate_cap);
        else if (!strcmp(o, "control-port")) bad = cc_num(v, 1, 65535, &a->control_port);
        else if (!strcmp(o, "feedback")) {
            if (v && !strcmp(v, "on")) a->feedback = 1;
            else if (v && !strcmp(v, "off")) a->feedback = 0;
            else bad = 1;
        }
        else if (!strcmp(o, "lead-ms")) bad = cc_num(v, 0, 600000, &a->lead_ms);
        else if (!strcmp(o, "tail-ms")) bad = cc_num(v, 0, 600000, &a->tail_ms);
        else if (!strcmp(o, "feed")) a->feed = v;
        else if (!strcmp(o, "feed-chunks")) a->feed_chunks = v;
        else if (!strcmp(o, "until-s")) bad = cc_num(v, 1, 86400, &a->until_s);
        else if (!strcmp(o, "read-mibs")) bad = cc_num(v, 1, 100000, &a->read_mibs);
        else bad = 1;
        if (bad || !v) {
            ir_say("chunkcast: bad option '%s %s'\n", argv[i], v ? v : "");
            return -1;
        }
        i++;
    }
    a->argi = i;
    return 0;
}

/* "ADDR:PORT" -> sin. 0 on success. */
static int cc_addr(const char *s, struct sockaddr_in *sin)
{
    char host[64];
    const char *colon = s ? strrchr(s, ':') : NULL;
    long long port;
    if (!colon || (size_t)(colon - s) >= sizeof host || cc_num(colon + 1, 1, 65535, &port)) return -1;
    memcpy(host, s, (size_t)(colon - s));
    host[colon - s] = 0;
    memset(sin, 0, sizeof *sin);
    sin->sin_family = AF_INET;
    sin->sin_port = htons((uint16_t)port);
    return inet_pton(AF_INET, host, &sin->sin_addr) == 1 ? 0 : -1;
}

/* The index, the map and the targets of recv / apply. 0, or the exit code. */
static int cc_setup(const struct cc_args *a, int argc, char **argv)
{
    long long cap;
    int was_set;
    if (cc_knob("CHUNKCAST_QCAP", CC_QCAP, 8, CC_QCAP_MAX, &cap, &was_set)) return 2;
    qcap = (int)cap;
    if (was_set) {                       /* #3522: only opt-in queues incur this memory check */
        int targets = argc - a->argi;
        uint64_t need = (uint64_t)targets * (uint64_t)qcap * CC_CHUNK;
        long long kb = -1;
        char line[256];
        FILE *f = fopen("/proc/meminfo", "r");
        if (f) {
            while (fgets(line, sizeof line, f) && sscanf(line, "MemAvailable: %lld kB", &kb) != 1) kb = -1;
            fclose(f);
        }
        if (need > CC_QCAP_BYTES_MAX || (kb >= 0 && need > (uint64_t)kb * 1024 / 2)) {
            ir_say("chunkcast: CHUNKCAST_QCAP=%d for %d targets needs %llu bytes; limit %llu bytes, MemAvailable %lld kB"
                   " (at most half) -- nothing is started\n", qcap, targets, (unsigned long long)need,
                   (unsigned long long)CC_QCAP_BYTES_MAX, kb);
            return 2;
        }
    }
    if (a->part < 0 || !a->index || !a->index_xxh || !state_path || !a->raw == !a->head ||
        a->argi >= argc || argc - a->argi > MAX_TARGETS) {
        ir_say("chunkcast: usage: see the head of agent/chunkcast.c\n");
        return 2;
    }
    cc_part = (int)a->part;
    if (cc_index_load(a->index, a->index_xxh)) return 2;
    if (ix.raw != a->raw) {
        ir_say("chunkcast: the index is of a %s partition, the call is for the other kind\n", ix.raw ? "raw" : "partclone");
        return 2;
    }
    if (!ix.raw && cc_head_load(a->head)) return 2;
    if (cc_b3_setup(a->b3_file, a->b3_hash)) return 2;
    inflight = calloc((size_t)(ix.n / 8 + 1), 1);
    if (!inflight) return 2;
    for (int i = a->argi; i < argc; i++, ntg++) {
        struct cc_tgt *t = &tg[ntg];
        t->path = argv[i];
        t->wfd = -1;
        t->alive = 1;
        t->have = calloc((size_t)(ix.n / 8 + 1), 1);
        t->pend = calloc((size_t)(ix.n / 8 + 1), 1);
        t->snap = calloc((size_t)(ix.n / 8 + 1), 1);
        pthread_mutex_init(&t->mu, NULL);
        pthread_cond_init(&t->cv, NULL);
        if (!t->have || !t->pend || !t->snap) return 2;
    }
    if (cc_state_load()) return 2;
    for (int i = 0; i < ntg; i++)
        if (tg[i].alive && cc_open(&tg[i]))
            cc_fail(&tg[i], "the target did not open for writing, is not a disk, or is too small");
    return 0;
}

static int cc_report(int rc, const char *word)
{
    if (cc_state_write() && rc == 0) rc = 2;    /* a state nobody can read is not a finished pass */
    for (int i = 0; i < ntg; i++)
        printf("%s %s %llu %llu%s%s\n", tg[i].path, tg[i].alive ? "ok" : "failed",
               (unsigned long long)tg[i].nhave, (unsigned long long)ix.n, tg[i].alive ? "" : " ",
               tg[i].alive ? "" : tg[i].reason);
    /* #3141/#3170: what each drive was too slow to take in this run -- apart from what never arrived. The
     * server tells a slow drive from a lost network by it (chunk-pass `skipped`); apply waits, so its 0 */
    for (int i = 0; i < ntg; i++)
        printf("skipped %s %llu\n", tg[i].path, (unsigned long long)tg[i].skipped);
    printf("pass %s\n", word);
    if (fflush(stdout) && rc == 0) rc = 2;
    return rc;
}

/* --- recv -------------------------------------------------------------------- */

#define CC_GROUPS ((CC_ZMAX + CC_PAYLOAD * FEC_K - 1) / (CC_PAYLOAD * FEC_K))
struct cc_parity {
    unsigned char present[CC_GROUPS], repaired[CC_GROUPS], bytes[CC_GROUPS][CC_PAYLOAD];
};

struct cc_slot {
    struct cc_parity *parity;
    unsigned recovered;
    int used, ready;                    /* ready: whole, and waiting for room in the verifiers' queue (#3518) */
    uint64_t k;
    uint32_t count, got;
    long long touched;
    unsigned char *data, seen[CC_ZMAX / CC_PAYLOAD / 8 + 2];
};

/* Sparse loss evidence. Keep exact totals even when the bounded HTTP detail fills. */
#define LOSS_DETAIL 128
static struct {
    unsigned char *counted;
    uint64_t chunks, packets, lost, groups;
    unsigned longest, rows;
    struct { uint64_t chunk; unsigned group, mask; } row[LOSS_DETAIL];
} loss;

static void cc_loss(const struct cc_slot *s)
{
    if (!loss.counted || cc_bit(loss.counted, s->k)) return;
    loss.counted[s->k >> 3] |= 1u << (s->k & 7);
    loss.chunks++;
    loss.packets += s->count;
    if (s->got == s->count) return;
    unsigned run = 0, mask = 0;
    for (unsigned i = 0; i < s->count; i++) {
        if (cc_bit(s->seen, i)) run = 0;
        else {
            loss.lost++;
            if (++run > loss.longest) loss.longest = run;
            mask |= 1u << (i % FEC_K);
        }
        if (i % FEC_K == FEC_K - 1 || i + 1 == s->count) {
            if (mask) {
                loss.groups++;
                if (loss.rows < LOSS_DETAIL) {
                    loss.row[loss.rows].chunk = s->k;
                    loss.row[loss.rows].group = i / FEC_K;
                    loss.row[loss.rows++].mask = mask;
                }
            }
            mask = 0;
        }
    }
}

static void cc_loss_summary(void)
{
    if (!loss.counted) return;
    printf("loss_shape {\"k\":%u,\"chunks\":%llu,\"packets\":%llu,\"lost\":%llu,"
           "\"longest\":%u,\"groups\":%llu,\"masks\":[", FEC_K, (unsigned long long)loss.chunks,
           (unsigned long long)loss.packets, (unsigned long long)loss.lost, loss.longest,
           (unsigned long long)loss.groups);
    for (unsigned i = 0; i < loss.rows; i++)
        printf("%s[%llu,%u,%u]", i ? "," : "", (unsigned long long)loss.row[i].chunk,
               loss.row[i].group, loss.row[i].mask);
    puts("]}");
}

static struct cc_slot slots[CC_SLOTS];
static pthread_mutex_t vq_mu = PTHREAD_MUTEX_INITIALIZER, pool_mu = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t vq_cv = PTHREAD_COND_INITIALIZER, pool_cv = PTHREAD_COND_INITIALIZER;
static struct { uint64_t k; unsigned char *z; } vq[CC_VQ_MAX];
static int vq_n, vq_stop, vq_busy, npool, nspare;       /* vq_busy: verifiers working on a frame right now */
static int nver = 1, vq_cap = CC_VQ;                    /* #3518: verifier threads, and frames the queue holds (cc_verifiers) */
static unsigned char *spare[CC_SLOTS + CC_VQ_MAX + CC_VER_MAX + 2]; /* frame buffers not in a slot or the queue */
static struct cc_buf **pool;
static struct { uint64_t pkts, foreign, stray, bad_pkt, dup, evicted, busy, bad_chunk, good, skipped, conflict, version; } stat_rx;
/* #3518: not in `rxstats` (its twelve keys are the agent's contract, rxsense.sh) but in the `verifier` line after it.
 * deferred: complete chunks that found the queue full and waited in their slot -- the pressure, before any drop. */
static uint64_t stat_deferred, ver_work_ns, ver_wait_ns;
static int rx_from;                                     /* --from was given: `stray` is counted, so it is said */

/* #3064 (G7): the chunks this pass gave up half-received (`evicted`), on stdout for the agent's progress
   report (agent/lib/restore/chunkloss.sh) -- once listening, whenever the count grew (at most once a second),
   and once more at the end. Until this line the count reached only the log, after the pass.
   #1079/#2485: with --from, `stray <N>` beside it -- the packets of another source, dropped (the agent tells the
   server: chunkgate.sh). No --from: no line, "not read", never 0. */
static void cc_say_abandoned(void)
{
    printf("abandoned %llu\n", (unsigned long long)stat_rx.evicted);
    if (rx_from) printf("stray %llu\n", (unsigned long long)stat_rx.stray);
    fflush(stdout);
}

/* #3064 (S41): every counter of the pass, on stdout once the pass is over -- `abandoned` and `stray` were the only
   two that reached the agent. `stray` is `-` without --from (not counted, so not 0). The agent adds the passes
   up (agent/lib/restore/rxsense.sh); a pass that does not say this line is "not read" there, never "all zero". */
static void cc_say_rx(void)
{
    printf("rxstats pkts=%llu foreign=%llu stray=", (unsigned long long)stat_rx.pkts, (unsigned long long)stat_rx.foreign);
    if (rx_from) printf("%llu", (unsigned long long)stat_rx.stray);
    else printf("-");
    printf(" bad_pkt=%llu dup=%llu evicted=%llu busy=%llu bad_chunk=%llu good=%llu skipped=%llu conflict=%llu version=%llu\n",
           (unsigned long long)stat_rx.bad_pkt, (unsigned long long)stat_rx.dup, (unsigned long long)stat_rx.evicted,
           (unsigned long long)stat_rx.busy, (unsigned long long)stat_rx.bad_chunk, (unsigned long long)stat_rx.good,
           (unsigned long long)stat_rx.skipped, (unsigned long long)stat_rx.conflict, (unsigned long long)stat_rx.version);
    fflush(stdout);
}

/* #3518: the verifiers of the pass, on stdout after `rxstats` (whose twelve keys stay the agent's contract): how many, the
 * queue, the chunks that waited in their slot for room in it (`deferred` -- the pressure, before a drop), and what the
 * verifying took, summed over the threads: `work_ms` in cc_verify (zstd, XXH64, BLAKE3) and `wait_ms` for a buffer the
 * writers had not given back. work_ms close to threads x the pass's seconds = the verifiers were the limit. */
static void cc_say_verifier(void)
{
    printf("verifier threads=%d queue=%d deferred=%llu work_ms=%llu wait_ms=%llu\n", nver, vq_cap,
           (unsigned long long)stat_deferred, (unsigned long long)(ver_work_ns / 1000000),
           (unsigned long long)(ver_wait_ns / 1000000));
    fflush(stdout);
}

static void cc_buf_put(struct cc_buf *b)
{
    if (__atomic_sub_fetch(&b->refs, 1, __ATOMIC_ACQ_REL)) return;
    pthread_mutex_lock(&st_mu);
    inflight[b->k >> 3] &= (unsigned char)~(1u << (b->k & 7));
    pthread_mutex_unlock(&st_mu);
    pthread_mutex_lock(&pool_mu);
    pool[npool++] = b;
    pthread_cond_signal(&pool_cv);
    pthread_mutex_unlock(&pool_mu);
}

static void *cc_writer(void *arg)
{
    struct cc_tgt *t = arg;
    for (;;) {
        struct cc_buf *b = NULL;
        struct timespec until;
        clock_gettime(CLOCK_REALTIME, &until);
        until.tv_nsec += 100 * 1000000L;
        if (until.tv_nsec >= 1000000000L) { until.tv_sec++; until.tv_nsec -= 1000000000L; }
        pthread_mutex_lock(&t->mu);
        if (!t->qn && !t->stop) pthread_cond_timedwait(&t->cv, &t->mu, &until);
        if (t->qn) { b = t->q[t->qh]; t->qh = (t->qh + 1) % qcap; t->qn--; t->writing = b->len; }
        int done = t->stop && !b;
        pthread_mutex_unlock(&t->mu);
        if (done) return NULL;
        if (!b) continue;
        if (cc_live(t)) cc_write(t, b->k, b->data, b->len);
        pthread_mutex_lock(&t->mu);
        t->writing = 0;
        pthread_mutex_unlock(&t->mu);
        cc_buf_put(b);
    }
}

/* The periodic flush, beside the writer and never in its way. */
static void *cc_flusher(void *arg)
{
    struct cc_tgt *t = arg;
    for (long long at = now_ms();;) {
        pthread_mutex_lock(&t->mu);
        int stop = t->stop;
        pthread_mutex_unlock(&t->mu);
        if (stop) return NULL;
        if (now_ms() - at < sync_ms) { usleep(20000); continue; }
        cc_sync(t, 0);
        at = now_ms();
    }
}

static long long cc_now_ns(void)
{
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec * 1000000000LL + t.tv_nsec;
}

/* #3518: nver of these, one queue. The frames in it are in no order that matters: a chunk is written at its own place.
 * Why nver of them do not race over what is durable (#3519, 16/11): a verifier shares nothing with its neighbours but locked
 * state. The frame `z` and the buffer `b` are its own until it hands `b` to the writers; `ZSTD_decompress` and the BLAKE3
 * hasher are per call; the counters it moves are atomic (`good`, `bad_chunk`, the times) or under `vq_mu` (the spare frames).
 * `have` and `pend` change only under `st_mu`, in the writers and the flusher, and the verifier only READS them (to ask who
 * needs the chunk). Between that read and the writer's `pend` the chunk is `inflight` -- set by `cc_hand` under `st_mu`,
 * cleared by `cc_buf_put` when the LAST holder lets go, which is after the write -- so no other copy of it is taken and
 * no moment exists in which it is neither inflight nor written. A chunk that fails its hash also clears `inflight`, and
 * only that one. Every drop the verifiers' side makes is counted (`busy`, `evicted`, `skipped`); a chunk that is missing
 * with none of them counted never reached its slot whole -- on a loaded test host, a datagram the kernel dropped (the two
 * incompressible chunks of test_chunkcast_2533.py are ~2,900 packets each). The repeat test of
 * test_chunkcast_verifier_pool_3518.py runs a damaged-chunk pass many times over short chunks, where that cannot happen. */
static void *cc_verifier(void *arg)
{
    (void)arg;
    for (;;) {
        pthread_mutex_lock(&vq_mu);
        while (!vq_n && !vq_stop) pthread_cond_wait(&vq_cv, &vq_mu);
        if (!vq_n) { pthread_mutex_unlock(&vq_mu); return NULL; }
        uint64_t k = vq[0].k;
        unsigned char *z = vq[0].z;
        vq_busy++;
        memmove(&vq[0], &vq[1], (size_t)--vq_n * sizeof vq[0]);
        pthread_mutex_unlock(&vq_mu);

        long long t0 = cc_now_ns();
        pthread_mutex_lock(&pool_mu);
        while (!npool) pthread_cond_wait(&pool_cv, &pool_mu);   /* the writers always give back */
        struct cc_buf *b = pool[--npool];
        pthread_mutex_unlock(&pool_mu);
        b->k = k;
        b->len = (uint32_t)cc_len(k);
        b->refs = 1;
        long long t1 = cc_now_ns();
        int ok = cc_verify(k, z, b->data) == 0;
        __atomic_add_fetch(&ver_wait_ns, (uint64_t)(t1 - t0), __ATOMIC_RELAXED);
        __atomic_add_fetch(&ver_work_ns, (uint64_t)(cc_now_ns() - t1), __ATOMIC_RELAXED);
        pthread_mutex_lock(&vq_mu);
        spare[nspare++] = z;
        pthread_mutex_unlock(&vq_mu);
        __atomic_add_fetch(ok ? &stat_rx.good : &stat_rx.bad_chunk, 1, __ATOMIC_RELAXED);
        for (int i = 0; ok && i < ntg; i++) {
            struct cc_tgt *t = &tg[i];
            pthread_mutex_lock(&st_mu);
            int need = t->alive && !cc_bit(t->have, k) && !cc_bit(t->pend, k);
            pthread_mutex_unlock(&st_mu);
            if (!need) continue;
            pthread_mutex_lock(&t->mu);
            if (t->qn < qcap) {
                __atomic_add_fetch(&b->refs, 1, __ATOMIC_ACQ_REL);
                t->q[(t->qh + t->qn++) % qcap] = b;
                pthread_cond_signal(&t->cv);
            } else {
                t->skipped++;           /* this drive is behind: the chunk stays missing for it alone */
            }
            pthread_mutex_unlock(&t->mu);
        }
        cc_buf_put(b);
        pthread_mutex_lock(&vq_mu);
        vq_busy--;
        pthread_mutex_unlock(&vq_mu);
    }
}

/* --- what recv and apply share: the buffers, the threads, the ceiling, the drain --- */

/* Every target can hold a full queue, each verifier one more and the feeder one: a drive that
 * stops taking chunks can never starve its neighbours of buffers. NULL in
 * `pool` = no memory. */
static void cc_pool(void)
{
    npool = ntg * qcap + nver + 1;
    pool = calloc((size_t)npool, sizeof *pool);
    for (int i = 0; pool && i < npool; i++)
        if (!(pool[i] = calloc(1, sizeof **pool)) || !(pool[i]->data = malloc(CC_CHUNK))) pool = NULL;
}

/* A writer and a flusher a target. 0 = all of them run. */
static int cc_threads(void)
{
    for (int i = 0; i < ntg; i++)
        if (pthread_create(&tg[i].th, NULL, cc_writer, &tg[i]) ||
            pthread_create(&tg[i].flusher, NULL, cc_flusher, &tg[i])) return -1;
    return 0;
}

/* A drive that stopped taking writes fails alone: its write or its flush has
 * not returned for `stall_ms`. Its neighbours are not made to wait for it. */
static void cc_watch(long long stall_ms)
{
    long long now = now_ms();
    for (int i = 0; i < ntg; i++) {
        long long began = __atomic_load_n(&tg[i].busy_ms, __ATOMIC_ACQUIRE);
        if (began && now - began > stall_ms && !tg[i].hung) {
            tg[i].hung = 1;
            cc_fail(&tg[i], "the disk has not taken a write or a flush for too long -- it is not waited for");
        }
    }
}

/* The writers are told to stop, given the same ceiling to empty their queues,
 * and what the last periodic flush did not cover is fsynced. */
static void cc_drain(long long stall_ms)
{
    for (int i = 0; i < ntg; i++) {
        pthread_mutex_lock(&tg[i].mu);
        tg[i].stop = 1;
        pthread_cond_signal(&tg[i].cv);
        pthread_mutex_unlock(&tg[i].mu);
    }
    for (int i = 0; i < ntg; i++) {
        /* A drive that never returns holds its two threads for ever: they are left
         * behind, and the verdict is written without them. A drive that hangs only
         * now, in this drain, is given the same ceiling. */
        for (long long began = now_ms(); !tg[i].hung;) {
            pthread_mutex_lock(&tg[i].mu);
            int idle = !tg[i].qn;
            pthread_mutex_unlock(&tg[i].mu);
            long long busy = __atomic_load_n(&tg[i].busy_ms, __ATOMIC_ACQUIRE);
            if (idle && !busy) break;
            if ((busy && now_ms() - busy > stall_ms) || now_ms() - began > stall_ms + 60000) {
                tg[i].hung = 1;
                cc_fail(&tg[i], "the disk has not taken a write or a flush for too long -- it is not waited for");
            }
            usleep(20000);
        }
        if (tg[i].hung) continue;
        pthread_join(tg[i].th, NULL);
        pthread_join(tg[i].flusher, NULL);
        cc_sync(&tg[i], 1);                     /* what the last periodic flush did not cover */
    }
}

/* A list of chunk ranges ("A-B,C-D", a file: thousands of ranges are more than
 * an argument holds) -> a bit a chunk. NULL = it is not such a list. */
static unsigned char *cc_pick(const char *path)
{
    size_t len = 0;
    char *list = cc_slurp(path, 64u << 20, &len);
    unsigned char *pick;
    if (!list) return NULL;
    while (len && (list[len - 1] == '\n' || list[len - 1] == ' ')) list[--len] = 0;
    pick = calloc((size_t)(ix.n / 8 + 1), 1);
    for (const char *p = list; pick && *p;) {
        uint64_t lo, hi;
        const char *q;
        if (cc_dec(&p, '-', &lo)) { pick = NULL; break; }
        q = p;
        if (cc_dec(&q, ',', &hi)) { q = p; if (cc_dec(&q, 0, &hi)) { pick = NULL; break; } q--; }
        if (lo > hi || hi >= ix.n) { pick = NULL; break; }
        for (uint64_t k = lo; k <= hi; k++) pick[k >> 3] |= (unsigned char)(1u << (k & 7));
        p = q;
    }
    free(list);
    return pick;
}

/* --- apply: chunks fetched over HTTP ---------------------------------------- */

/* The state file follows each flush on its own, not the stream: while apply
 * waits for the next frame, whoever watches the state still sees what the last
 * flush made durable -- that is how the agent knows a fetch advances. */
static volatile int keeper_stop;

/* #3149: how many chunks apply has TAKEN -- checked against the index and handed to the writers -- flushed or not.
 * A flush on a slow drive (a thin vmdk on a hard disk, the VM lab 14/11) takes minutes, and the state file -- what
 * is durable -- stands still all that time: 2,569 chunks taken from the stream in 342 seconds, and four state
 * writes. The agent tells the server THIS number as the fetch's proof of life, so a download that is moving is not
 * taken for one that is not. Its own file, renamed into place, next to the state's: the state's format is not
 * touched, and a number that could not be written is a number nobody tells. */
static unsigned long long n_taken;
static void cc_taken_write(unsigned long long n)
{
    char path[4096], tmp[4096];
    FILE *f;
    if (snprintf(path, sizeof path, "%s.taken", state_path) >= (int)sizeof path ||
        snprintf(tmp, sizeof tmp, "%s.tmp", path) >= (int)sizeof tmp || !(f = fopen(tmp, "w"))) return;
    fprintf(f, "%llu\n", n);
    if (ferror(f) | fclose(f) || rename(tmp, path)) unlink(tmp);
}

static void *cc_state_keeper(void *arg)
{
    unsigned long long told = 0;
    (void)arg;
    while (!keeper_stop) {
        usleep(100000);
        pthread_mutex_lock(&st_mu);
        int dirty = state_dirty;
        pthread_mutex_unlock(&st_mu);
        if (dirty) cc_state_write();
        unsigned long long took = __atomic_load_n(&n_taken, __ATOMIC_ACQUIRE);
        if (took != told) { cc_taken_write(took); told = took; }
    }
    unsigned long long took = __atomic_load_n(&n_taken, __ATOMIC_ACQUIRE);
    if (took != told) cc_taken_write(took);
    return NULL;
}

/* The chunk goes to every live target that lacks it. Unlike a stream, a fetch
 * can wait: a full queue is waited for, not skipped -- up to the ceiling at
 * which that drive fails alone. */
static void cc_feed(struct cc_buf *b, long long stall_ms)
{
    for (int i = 0; i < ntg; i++) {
        struct cc_tgt *t = &tg[i];
        for (;;) {
            pthread_mutex_lock(&st_mu);
            int need = t->alive && !cc_bit(t->have, b->k) && !cc_bit(t->pend, b->k);
            pthread_mutex_unlock(&st_mu);
            if (!need || t->hung) break;
            pthread_mutex_lock(&t->mu);
            int room = t->qn < qcap;
            if (room) {
                __atomic_add_fetch(&b->refs, 1, __ATOMIC_ACQ_REL);
                t->q[(t->qh + t->qn++) % qcap] = b;
                pthread_cond_signal(&t->cv);
            }
            pthread_mutex_unlock(&t->mu);
            if (room) break;
            cc_watch(stall_ms);
            usleep(2000);
        }
    }
}

static int cc_apply(int argc, char **argv)
{
    struct cc_args a;
    unsigned char *pick, *z;
    uint64_t asked = 0, applied = 0;
    int rc;
    if (cc_parse(argc, argv, &a)) return 2;
    if ((rc = cc_setup(&a, argc, argv))) return rc;
    ir_say("chunkcast: apply: knobs qcap=%d feed_q=%d\n", qcap, qcap / 2);
    if (a.chunks) {
        if (!(pick = cc_pick(a.chunks))) {
            ir_say("chunkcast: apply: %s is not a list of chunk ranges\n", a.chunks);
            return 2;
        }
    } else {
        if (a.first < 0 || a.count < 1 || (uint64_t)(a.first + a.count) > ix.n) {
            ir_say("chunkcast: apply: --first/--count are outside the index\n");
            return 2;
        }
        if (!(pick = calloc((size_t)(ix.n / 8 + 1), 1))) return 2;
        for (uint64_t k = (uint64_t)a.first; k < (uint64_t)(a.first + a.count); k++)
            pick[k >> 3] |= (unsigned char)(1u << (k & 7));
    }
    for (uint64_t k = 0; k < ix.n; k++) asked += (uint64_t)cc_bit(pick, k);
    signal(SIGTERM, cc_on_signal_apply);
    signal(SIGINT, cc_on_signal_apply);
    prctl(PR_SET_PDEATHSIG, SIGTERM);           /* the agent's shell died: stop, flush, say so */
    cc_pool();
    z = malloc(CC_ZMAX);
    pthread_t keeper;
    if (!pool || !z || cc_threads() || pthread_create(&keeper, NULL, cc_state_keeper, NULL)) {
        ir_say("chunkcast: apply: no memory, or no thread\n");
        return 2;
    }
    long long stall_ms = env_ll("CHUNKCAST_WRITE_STALL_MS", CC_WRITE_STALL_MS);
    const char *word = "applied";
    rc = 0;
    for (uint64_t k = 0; k < ix.n && rc == 0; k++) {
        if (!cc_bit(pick, k)) continue;
        if (cc_stop) { rc = 6; word = "stopped"; break; }
        if (rb_read_all(STDIN_FILENO, z, ix.zlen[k])) {
            if (cc_stop) { rc = 6; word = "stopped"; break; }
            ir_say("chunkcast: apply: the frame of chunk %llu did not arrive whole\n", (unsigned long long)k);
            rc = 8;
            break;
        }
        pthread_mutex_lock(&pool_mu);
        while (!npool) pthread_cond_wait(&pool_cv, &pool_mu);   /* the writers always give back */
        struct cc_buf *b = pool[--npool];
        pthread_mutex_unlock(&pool_mu);
        b->k = k;
        b->len = (uint32_t)cc_len(k);
        b->refs = 1;
        if (cc_verify(k, z, b->data)) rc = 8;   /* what is not the index's is not written, and the stream after it is not trusted */
        else { cc_feed(b, stall_ms); applied++; __atomic_store_n(&n_taken, applied, __ATOMIC_RELEASE); }
        cc_buf_put(b);
        cc_watch(stall_ms);
        if (rc == 0 && !cc_alive()) rc = 5;
    }
    cc_drain(stall_ms);
    keeper_stop = 1;
    pthread_join(keeper, NULL);
    if (rc == 8) word = "bad-chunk";
    if ((rc == 0 || rc == 8) && !cc_alive()) rc = 5;
    if (rc == 5) word = "no-target";
    ir_say("chunkcast: apply: %llu of %llu chunks taken from the stream, %llu flushes, %llu state writes -- %s\n",
           (unsigned long long)applied, (unsigned long long)asked, n_flushes, n_state_writes + 1, word);
    return cc_report(rc, word);
}

/* #3518: how many verifiers, and how long their queue. CHUNKCAST_VERIFIERS (1..CC_VER_MAX; another value is the
 * default) or half the cores this process may run on, 1..4 -- the recv loop, a writer and a flusher a drive and the
 * network's softirq need the rest. The queue holds CC_VQ frames a verifier and CC_VQ more, at most CC_VQ_MAX, and its
 * frames may take at most a sixteenth of the memory that is free. MemAvailable not read = the small queue of CC_VQ, said. */
static const char *cc_verifiers(void)
{
    long ncpu = 0, half;
    cpu_set_t set;
    long long kb = -1, want, fit;
    char line[128];
    FILE *f;
    if (!sched_getaffinity(0, sizeof set, &set)) ncpu = CPU_COUNT(&set);
    if (ncpu < 1) ncpu = sysconf(_SC_NPROCESSORS_ONLN);
    half = ncpu / 2 < 1 ? 1 : ncpu / 2 > 4 ? 4 : ncpu / 2;
    want = env_ll("CHUNKCAST_VERIFIERS", half);
    nver = (int)(want > CC_VER_MAX ? CC_VER_MAX : want);
    vq_cap = CC_VQ * nver + CC_VQ > CC_VQ_MAX ? CC_VQ_MAX : CC_VQ * nver + CC_VQ;
    if ((f = fopen("/proc/meminfo", "r"))) {
        while (fgets(line, sizeof line, f) && sscanf(line, "MemAvailable: %lld kB", &kb) != 1) kb = -1;
        fclose(f);
    }
    if (kb < 0) { vq_cap = CC_VQ; return " (MemAvailable was not read: the smallest queue)"; }
    fit = kb * 1024 / 16 / CC_ZMAX;
    if (fit < vq_cap) vq_cap = fit < CC_VQ ? CC_VQ : (int)fit;
    return "";
}

/* #3518: the whole chunk in slot `s` into the verifiers' queue. 0 = the queue is full: the chunk is NOT dropped for it, it
 * stays in its slot (`ready`) and cc_ready tries again; 1 = it left the slot. */
static int cc_hand(struct cc_slot *s)
{
    pthread_mutex_lock(&vq_mu);
    if (vq_n >= vq_cap) {
        pthread_mutex_unlock(&vq_mu);
        return 0;
    }
    pthread_mutex_lock(&st_mu);
    int fed = cc_bit(inflight, s->k);           /* B5: the HTTP feeder claimed it while this one came in: written once */
    if (!fed) inflight[s->k >> 3] |= (unsigned char)(1u << (s->k & 7));
    pthread_mutex_unlock(&st_mu);
    if (fed) {
        stat_rx.skipped++;
    } else {
        vq[vq_n].k = s->k;
        vq[vq_n++].z = s->data;
        s->data = spare[--nspare];
        pthread_cond_signal(&vq_cv);
    }
    pthread_mutex_unlock(&vq_mu);
    s->used = s->ready = 0;
    return 1;
}

/* The chunks that were whole while the queue was full, oldest first, as far as the queue has room. Returns how many
 * still wait. Only the recv thread calls it (it owns the slots). */
static int cc_ready(void)
{
    for (;;) {
        struct cc_slot *oldest = NULL;
        int n = 0;
        for (int i = 0; i < CC_SLOTS; i++)
            if (slots[i].used && slots[i].ready) {
                n++;
                if (!oldest || slots[i].touched < oldest->touched) oldest = &slots[i];
            }
        if (!oldest || !cc_hand(oldest)) return n;
    }
}

/* 1 = some live target lacks chunk k: not durable, not written and waiting for
 * its flush, and not already on its way through the verifier. */
static int cc_needed(uint64_t k)
{
    int need = 0;
    pthread_mutex_lock(&st_mu);
    if (!cc_bit(inflight, k))
        for (int i = 0; i < ntg; i++)
            need |= tg[i].alive && !cc_bit(tg[i].have, k) && !cc_bit(tg[i].pend, k);
    pthread_mutex_unlock(&st_mu);
    return need;
}

static size_t cc_group_bytes(uint32_t zlen, unsigned group)
{
    size_t left = zlen - (size_t)group * FEC_K * CC_PAYLOAD;
    return left < FEC_K * CC_PAYLOAD ? left : FEC_K * CC_PAYLOAD;
}

static void cc_data(const unsigned char *p, size_t len)
{
    static uint64_t skip = UINT64_MAX;
    uint64_t k = be_get(p + 16, 8);
    uint32_t zlen = (uint32_t)be_get(p + 24, 4), seq = (uint32_t)be_get(p + 28, 4);
    struct cc_slot *s = NULL, *idle = NULL, *oldest = NULL, *held = NULL;
    if (k >= ix.n || zlen != ix.zlen[k]) { stat_rx.bad_pkt++; return; }
    uint32_t count = (zlen + CC_PAYLOAD - 1) / CC_PAYLOAD;
    int parity = p[4] == CC_PARITY;
    unsigned group = parity ? seq : seq / FEC_K;
    size_t payload = len - CC_HDR - CC_DATA_HDR;
    if (parity ? (group >= (count + FEC_K - 1) / FEC_K || payload != CC_PAYLOAD)
               : (seq >= count || payload != (seq + 1 == count ? zlen - seq * CC_PAYLOAD : CC_PAYLOAD))) {
        stat_rx.bad_pkt++;
        return;
    }
    for (int i = 0; i < CC_SLOTS; i++) {
        if (slots[i].used && slots[i].k == k) s = &slots[i];
        if (!slots[i].used) idle = &slots[i];
        else if (slots[i].ready) { if (!held || slots[i].touched < held->touched) held = &slots[i]; }
        else if (!oldest || slots[i].touched < oldest->touched) oldest = &slots[i];
    }
    if (!s) {
        if (k == skip) return;
        if (!cc_needed(k)) { skip = k; stat_rx.skipped++; return; }
        s = idle ? idle : oldest ? oldest : held;   /* #3518: a whole chunk is the last to go */
        if (s->used) {
            if (s->ready) stat_rx.busy++;       /* no room anywhere for it: dropped, counted, repaired later */
            else { cc_loss(s); stat_rx.evicted++; }             /* a chunk that never completed: it stays missing */
        }
        s->used = 1;
        s->ready = 0;
        s->k = k;
        s->count = count;
        s->got = s->recovered = 0;
        if (s->parity) {
            memset(s->parity->present, 0, sizeof s->parity->present);
            memset(s->parity->repaired, 0, sizeof s->parity->repaired);
        }
        memset(s->seen, 0, sizeof s->seen);
    }
    s->touched = now_ms();
    if (s->ready) { stat_rx.dup++; return; }
    if (parity) {
        if (s->parity->present[group]) { stat_rx.dup++; return; }
        memcpy(s->parity->bytes[group], p + CC_HDR + CC_DATA_HDR, CC_PAYLOAD);
        s->parity->present[group] = 1;
    } else {
        if (cc_bit(s->seen, seq)) { stat_rx.dup++; return; }
        if (s->parity && s->parity->repaired[group] == seq % FEC_K + 1) {
            s->recovered--;
            s->parity->repaired[group] = 0;
        }
        memcpy(s->data + (size_t)seq * CC_PAYLOAD, p + CC_HDR + CC_DATA_HDR, payload);
        s->seen[seq >> 3] |= (unsigned char)(1u << (seq & 7));
        s->got++;
    }
    if (s->parity && s->parity->present[group] && !s->parity->repaired[group]) {
        unsigned first = group * FEC_K, missing = 0, holes = 0;
        for (unsigned i = first; i < first + FEC_K && i < count; i++) {
            if (!cc_bit(s->seen, i)) { missing = i - first; holes++; }
        }
        if (holes == 1) {
            fec_decode(s->data + (size_t)first * CC_PAYLOAD, cc_group_bytes(zlen, group),
                       missing, s->parity->bytes[group]);
            s->parity->repaired[group] = missing + 1;
            s->recovered++;
        }
    }
    if (s->got + s->recovered < s->count) return;
    cc_loss(s);
    if (!cc_hand(s)) {                          /* the queue is full: it waits here, and the sender hears of it (feedback) */
        s->ready = 1;
        stat_deferred++;
    }
}

/* --- B5: HTTP beside the stream, into the same writers (#3064) ------------------------------------------ */

#define CC_FEED_Q (qcap / 2)         /* the feeder fills a drawer's queue to here; the stream has the rest */

static unsigned char *feed_pick;        /* the chunks of --feed-chunks, read when the first frame comes */
static const char *feed_list;
static volatile int feed_stop;
static long long feed_stall_ms;
static struct { uint64_t taken, skipped, bad; const char *word; } feed_st = { 0, 0, 0, "ended" };

/* 1 = some live target lacks chunk k and nobody holds it: k is now the feeder's (`inflight`, as the stream's verifier
 * sets it) -- the stream's copy of it is dropped at its end (cc_data), and it is written once. */
static int cc_claim(uint64_t k)
{
    int need = 0;
    pthread_mutex_lock(&st_mu);
    if (!cc_bit(inflight, k)) {
        for (int i = 0; i < ntg; i++) need |= tg[i].alive && !cc_bit(tg[i].have, k) && !cc_bit(tg[i].pend, k);
        if (need) inflight[k >> 3] |= (unsigned char)(1u << (k & 7));
    }
    pthread_mutex_unlock(&st_mu);
    return need;
}

/* n bytes from the FIFO. 0 = all of them. -1 = told to stop. -2 = no byte came for the stall ceiling: the HTTP side is gone
 * (the FIFO is opened read-write, so its end is never an EOF -- the clock is what ends a wait). */
static int cc_feed_read(int fd, unsigned char *p, size_t n)
{
    long long last = now_ms();
    while (n) {
        struct pollfd pf = { .fd = fd, .events = POLLIN };
        if (feed_stop || cc_stop) return -1;
        if (poll(&pf, 1, 100) > 0) {
            ssize_t g = read(fd, p, n);
            if (g > 0) { p += g; n -= (size_t)g; last = now_ms(); continue; }
            if (g < 0 && errno != EINTR && errno != EAGAIN) return -2;
        }
        if (now_ms() - last > feed_stall_ms) return -2;
    }
    return 0;
}

/* The chunk to every live target that lacks it -- but only into a queue that is under half full, so the stream always
 * has room (its verifier SKIPS into a full queue). Waits for that room, up to the drive's own failure (cc_watch, the
 * receiver's loop) or the end of the feed. */
static void cc_feed_half(struct cc_buf *b)
{
    for (int i = 0; i < ntg; i++) {
        struct cc_tgt *t = &tg[i];
        for (;;) {
            pthread_mutex_lock(&st_mu);
            int need = t->alive && !cc_bit(t->have, b->k) && !cc_bit(t->pend, b->k);
            pthread_mutex_unlock(&st_mu);
            if (!need || t->hung || feed_stop || cc_stop) break;
            pthread_mutex_lock(&t->mu);
            int room = t->qn < CC_FEED_Q;
            if (room) {
                __atomic_add_fetch(&b->refs, 1, __ATOMIC_ACQ_REL);
                t->q[(t->qh + t->qn++) % qcap] = b;
                pthread_cond_signal(&t->cv);
            }
            pthread_mutex_unlock(&t->mu);
            if (room) break;
            usleep(2000);
        }
    }
}

/* Frame by frame in ascending index order, as cc_apply reads them: all of its bytes (also for a chunk that is skipped),
 * then claimed, checked (cc_verify: length, XXH64 and BLAKE3) and fed. A chunk that fails ends the feed -- the frames
 * after it are not trusted -- and the stream goes on alone; so does a feed that stalls or is told to stop. */
static void *cc_feeder(void *arg)
{
    int fd = (int)(intptr_t)arg;
    unsigned char *z = malloc(CC_ZMAX);
    for (;;) {                                  /* nothing is read, and nothing is timed, until the agent's curl begins */
        struct pollfd pf = { .fd = fd, .events = POLLIN };
        if (feed_stop || cc_stop) { feed_st.word = "idle"; free(z); z = NULL; break; }
        if (poll(&pf, 1, 100) > 0) break;
    }
    if (z && !(feed_pick = cc_pick(feed_list))) {
        ir_say("chunkcast: recv: --feed-chunks %s is not a list of chunk ranges -- the HTTP feed ends\n", feed_list);
        feed_st.word = "bad-list";
        free(z);
        z = NULL;
    }
    for (uint64_t k = 0; z && k < ix.n; k++) {
        struct cc_buf *b;
        int r;
        if (!cc_bit(feed_pick, k)) continue;
        if ((r = cc_feed_read(fd, z, ix.zlen[k]))) { feed_st.word = r == -1 ? "stopped" : "stalled"; break; }
        if (!cc_claim(k)) { feed_st.skipped++; continue; }
        pthread_mutex_lock(&pool_mu);
        while (!npool) pthread_cond_wait(&pool_cv, &pool_mu);   /* the writers always give back */
        b = pool[--npool];
        pthread_mutex_unlock(&pool_mu);
        b->k = k;
        b->len = (uint32_t)cc_len(k);
        b->refs = 1;
        if (cc_verify(k, z, b->data)) { feed_st.bad++; feed_st.word = "bad-chunk"; cc_buf_put(b); break; }
        cc_feed_half(b);
        feed_st.taken++;
        cc_buf_put(b);
    }
    if (!z && !strcmp(feed_st.word, "ended")) feed_st.word = "no-memory";
    free(z);
    return NULL;
}

/* Trident feedback v1: fixed length, network byte order, independent of the data protocol.
 * Report the slowest live drawer, including a write already dequeued by its writer.
 * Completion is write completion, NOT durability; only the existing ledger claims durability. */
#define CC_FB_SIZE 56
#define CC_FB_MAGIC 0x54524631u /* TRF1 */
static uint64_t fb_low = 2ULL * CC_CHUNK;
static uint64_t fb_high = 6ULL * CC_CHUNK;
#define CC_FB_PEERS 1024
#define CC_FB_WIDEN_TICKS 20    /* #3599: decisions pinned on the ceiling, all receivers healthy, before it grows by 10% (~2 s) */
#define CC_FB_FRESH_MS 300      /* a report this young is current: reports and decisions are two clocks of ~100 ms */
static void cc_feedback_report(int s, const struct sockaddr_in *to, uint64_t session,
                               uint32_t pass, uint64_t id)
{
    unsigned char p[CC_FB_SIZE] = {0};
    uint64_t written = UINT64_MAX, backlog = 0, drops = stat_rx.evicted + stat_rx.busy;
    int live = 0;
    for (int i = 0; i < ntg; i++) {
        struct cc_tgt *t = &tg[i];
        pthread_mutex_lock(&st_mu);
        int alive = t->alive;
        uint64_t count = t->written;
        pthread_mutex_unlock(&st_mu);
        pthread_mutex_lock(&t->mu);
        uint64_t queued = t->writing;
        for (int j = 0; j < t->qn; j++) queued += t->q[(t->qh + j) % qcap]->len;
        drops += t->skipped;
        pthread_mutex_unlock(&t->mu);
        if (!alive) continue;
        live++;
        if (count < written) written = count;
        if (queued > backlog) backlog = queued;
    }
    if (!live) return; /* no live disk is not a healthy report */
    pthread_mutex_lock(&vq_mu);
    backlog += (uint64_t)(vq_n + vq_busy) * CC_CHUNK;
    pthread_mutex_unlock(&vq_mu);
    for (int i = 0; i < CC_SLOTS; i++)          /* #3518: whole, and waiting for room in the queue -- the same pressure */
        if (slots[i].used && slots[i].ready) backlog += CC_CHUNK;
    be_put(p, CC_FB_MAGIC, 4); p[4] = 1;
    be_put(p + 6, cc_part, 2); be_put(p + 8, session, 8);
    be_put(p + 16, pass, 4); be_put(p + 24, id, 8);
    be_put(p + 32, written, 8); be_put(p + 40, backlog, 8);
    be_put(p + 48, drops, 8);   /* CUMULATIVE: a datagram the network ate must not take its drops with it */
    (void)!sendto(s, p, sizeof p, MSG_DONTWAIT, (const struct sockaddr *)to, sizeof *to);
}

static int cc_recv(int argc, char **argv)
{
    struct cc_args a;
    struct sockaddr_in sin;
    struct in_addr from = { 0 };
    uint64_t session;
    const char *sp;
    int rc, s, one = 1, want = CC_RCVBUF, got = 0, started = 0, ended = 0;
    socklen_t gl = sizeof got;
    pthread_t verifier[CC_VER_MAX], feeder;
    int feed_fd = -1;
    int feedback_s = -1;
    struct sockaddr_in feedback_to = {0};
    long long feedback_at = 0;
    uint64_t feedback_id = 0;
    if (cc_parse(argc, argv, &a)) return 2;
    sp = a.session;
    if (!sp || cc_hex16(&sp, 0, &session) || a.pass < 1 || cc_addr(a.addr, &sin)) {
        ir_say("chunkcast: recv: --listen ADDR:PORT, --session HEX16 and --pass are required\n");
        return 2;
    }
    if (a.from && inet_pton(AF_INET, a.from, &from) != 1) {
        ir_say("chunkcast: recv: --from '%s' is not an IPv4 address\n", a.from);
        return 2;
    }
    rx_from = a.from != NULL;
    if (!a.feed != !a.feed_chunks) {
        ir_say("chunkcast: recv: --feed and --feed-chunks go together\n");
        return 2;
    }
    if (a.fec && !a.b3_file) { ir_say("chunkcast: תיקון חבילות דורש רשימת BLAKE3 מאומתת\n"); return 2; }
    if ((rc = cc_setup(&a, argc, argv))) return rc;
    if (a.feedback) {
        int random = open("/dev/urandom", O_RDONLY | O_CLOEXEC);
        if (random < 0 || read(random, &feedback_id, sizeof feedback_id) != (ssize_t)sizeof feedback_id) return 2;
        close(random);
        if (!feedback_id) feedback_id = 1;
        int port = a.control_port ? (int)a.control_port : ntohs(sin.sin_port) + 1;
        if (port > 65535) return 2;
        feedback_to.sin_family = AF_INET;
        feedback_to.sin_port = htons((uint16_t)port);
        feedback_s = socket(AF_INET, SOCK_DGRAM | SOCK_CLOEXEC | SOCK_NONBLOCK, 0);
        if (feedback_s < 0) return 2;
    }
    if (a.feed) {                               /* B5: the FIFO is checked before anything listens; the list, with the first frame */
        struct stat fs;
        if ((feed_fd = open(a.feed, O_RDWR | O_CLOEXEC)) < 0 || fstat(feed_fd, &fs) || !S_ISFIFO(fs.st_mode)) {
            ir_say("chunkcast: recv: --feed %s is not a FIFO\n", a.feed);
            return 2;
        }
        feed_list = a.feed_chunks;
        feed_stall_ms = a.stall_s * 1000;
    }
    signal(SIGTERM, cc_on_signal);
    signal(SIGINT, cc_on_signal);
    prctl(PR_SET_PDEATHSIG, SIGTERM);           /* the agent's shell died: stop, flush, say so */

    /* Every target can hold a full queue and each verifier one more: a drive that
     * stops taking chunks can never starve its neighbours of buffers. */
    const char *vq_note = cc_verifiers();
    cc_pool();
    if (a.fec)
        for (int i = 0; i < CC_SLOTS; i++)
            if (!(slots[i].parity = malloc(sizeof *slots[i].parity))) return 2;
    loss.counted = calloc((ix.n + 7) / 8, 1);
    if (!loss.counted) ir_say("chunkcast: מדידת צורת האובדן אינה זמינה\n");
    for (int i = 0; i < CC_SLOTS; i++) if (!(slots[i].data = malloc(CC_ZMAX))) pool = NULL;
    for (nspare = 0; nspare < vq_cap + nver + 1; nspare++) if (!(spare[nspare] = malloc(CC_ZMAX))) pool = NULL;
    if (!pool) { ir_say("chunkcast: recv: no memory for %d chunks of 4MiB\n", npool); return 2; }

    s = socket(AF_INET, SOCK_DGRAM | SOCK_CLOEXEC, 0);
    if (s < 0 || setsockopt(s, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one) ||
        bind(s, (struct sockaddr *)&sin, sizeof sin)) {
        ir_say("chunkcast: recv: cannot listen on %s: %s\n", a.addr, strerror(errno));
        return 2;
    }
    setsockopt(s, SOL_SOCKET, SO_RCVBUF, &want, sizeof want);
    getsockopt(s, SOL_SOCKET, SO_RCVBUF, &got, &gl);
    if (IN_MULTICAST(ntohl(sin.sin_addr.s_addr))) {
        struct ip_mreqn mr = { .imr_multiaddr = sin.sin_addr };
        if (a.iface && !(mr.imr_ifindex = (int)if_nametoindex(a.iface))) {
            ir_say("chunkcast: recv: no interface '%s'\n", a.iface);
            return 2;
        }
        if (setsockopt(s, IPPROTO_IP, IP_ADD_MEMBERSHIP, &mr, sizeof mr)) {
            ir_say("chunkcast: recv: cannot join %s: %s\n", a.addr, strerror(errno));
            return 2;
        }
    }
    ir_say("chunkcast: recv: listening on %s%s%s%s%s, receive buffer %d bytes (asked %d), %llu chunks, %d targets\n",
           a.addr, a.iface ? " on " : "", a.iface ? a.iface : "", a.from ? ", only from " : "", a.from ? a.from : "",
           got, want, (unsigned long long)ix.n, ntg);

    ir_say("chunkcast: recv: %d verifier%s, a queue of %d frames%s\n", nver, nver == 1 ? "" : "s", vq_cap, vq_note);
    ir_say("chunkcast: recv: knobs qcap=%d feed_q=%d\n", qcap, qcap / 2);
    for (int i = 0; i < nver; i++)
        if (pthread_create(&verifier[i], NULL, cc_verifier, NULL)) return 2;
    if (cc_threads()) return 2;
    if (feed_fd >= 0 && pthread_create(&feeder, NULL, cc_feeder, (void *)(intptr_t)feed_fd)) {
        ir_say("chunkcast: recv: no thread for the HTTP feed\n");
        return 2;
    }
    printf("listening\n");
    if (fflush(stdout)) return 2;
    cc_say_abandoned();
    printf("rcvbuf %d\n", got);                 /* #3064 (G7): what the kernel gave, for the agent's progress report */
    if (fflush(stdout)) return 2;
    printf("knobs qcap=%d feed_q=%d\n", qcap, qcap / 2);
    if (fflush(stdout)) return 2;

    static unsigned char pk[CC_BATCH][CC_PKT];
    struct mmsghdr msgs[CC_BATCH];
    struct iovec iov[CC_BATCH];
    struct sockaddr_in src[CC_BATCH];
    long long t0 = now_ms(), last = t0, stall_ms = env_ll("CHUNKCAST_WRITE_STALL_MS", CC_WRITE_STALL_MS);
    long long said_at = t0;
    uint64_t said = 0;
    const char *word = "ended";
    rc = -1;
    while (rc < 0) {
        struct pollfd pfd = { .fd = s, .events = POLLIN };
        int waiting = cc_ready();               /* #3518: whole chunks that found the queue full, as it has room */
        int n = poll(&pfd, 1, waiting ? 5 : 100) > 0 ? 1 : 0;
        if (n) {
            memset(msgs, 0, sizeof msgs);
            for (int i = 0; i < CC_BATCH; i++) {
                iov[i] = (struct iovec){ pk[i], CC_PKT };
                msgs[i].msg_hdr.msg_iov = &iov[i];
                msgs[i].msg_hdr.msg_iovlen = 1;
                msgs[i].msg_hdr.msg_name = &src[i];
                msgs[i].msg_hdr.msg_namelen = sizeof src[i];
            }
            n = recvmmsg(s, msgs, CC_BATCH, MSG_DONTWAIT, NULL);
        }
        for (int i = 0; i < n; i++) {
            const unsigned char *p = pk[i];
            size_t len = msgs[i].msg_len;
            if (a.from && (msgs[i].msg_hdr.msg_namelen < sizeof src[i] || src[i].sin_family != AF_INET ||
                           src[i].sin_addr.s_addr != from.s_addr)) {
                if (!stat_rx.stray++) {         /* said once; the count is in the pass's last line */
                    char who[INET_ADDRSTRLEN] = "?";
                    if (msgs[i].msg_hdr.msg_namelen >= sizeof src[i])
                        inet_ntop(AF_INET, &src[i].sin_addr, who, sizeof who);
                    ir_say("chunkcast: recv: a packet from %s, not the server's %s -- dropped; every other one is "
                           "counted\n", who, a.from);
                }
                continue;
            }
            if (len < CC_HDR || be_get(p, 4) != CC_MAGIC || be_get(p + 6, 2) != (uint64_t)cc_part ||
                be_get(p + 8, 8) != session) { stat_rx.foreign++; continue; }
            if (p[5] != CC_VERSION) {           /* our session, another protocol: a sender of another release */
                if (!stat_rx.version++)
                    ir_say("chunkcast: recv: a packet of protocol version %d (this is %d) -- not taken; the server "
                           "and this agent image are of different releases\n", p[5], CC_VERSION);
                continue;
            }
            stat_rx.pkts++;
            /* Learn the sender only from an announcement of THIS pass and index. */
            if (a.feedback && p[4] == CC_ANNOUNCE && len == CC_HDR + 20 &&
                be_get(p + 16, 4) == (uint64_t)a.pass && be_get(p + 20, 8) == ix.n &&
                be_get(p + 28, 8) == ix.self_xxh)
                feedback_to.sin_addr = src[i].sin_addr;
            started = 1;
            last = now_ms();
            if ((p[4] == CC_DATA || (a.fec && p[4] == CC_PARITY)) && len > CC_HDR + CC_DATA_HDR) cc_data(p, len);
            else if (p[4] == CC_END && len >= CC_HDR + 4 && be_get(p + 16, 4) == (uint64_t)a.pass) ended = 1;
            else if (p[4] == CC_ANNOUNCE && len >= CC_HDR + 20 &&
                     (be_get(p + 20, 8) != ix.n || be_get(p + 28, 8) != ix.self_xxh) && !stat_rx.conflict++)
                ir_say("chunkcast: recv: an ANNOUNCE of this session names another index -- ignored; "
                       "every chunk is still checked against ours\n");
        }
        pthread_mutex_lock(&st_mu);
        int dirty = state_dirty;
        pthread_mutex_unlock(&st_mu);
        if (dirty) cc_state_write();
        long long now = now_ms();
        if (a.feedback && feedback_to.sin_addr.s_addr && now - feedback_at >= 100) {
            feedback_at = now;
            cc_feedback_report(feedback_s, &feedback_to, session, (uint32_t)a.pass, feedback_id);
        }
        if (stat_rx.evicted != said && now - said_at >= 1000) { said = stat_rx.evicted; said_at = now; cc_say_abandoned(); }
        cc_watch(stall_ms);
        if (rc >= 0) break;
        if (cc_stop) { rc = 6; word = "stopped"; }
        else if (!cc_alive()) { rc = 5; word = "no-target"; }
        else if (ended) rc = 0;
        else if (cc_complete()) { rc = 0; word = "complete"; }
        else if (!started && now - t0 > a.start_s * 1000) { rc = 3; word = "no-stream"; }
        else if (started && now - last > a.stall_s * 1000) { rc = 4; word = "stalled"; }
    }
    close(s);
    if (feedback_s >= 0) close(feedback_s);
    if (feed_fd >= 0) {                         /* it feeds the writers: it ends before they are told to */
        feed_stop = 1;
        pthread_join(feeder, NULL);
        close(feed_fd);
    }
    for (long long until = now_ms() + (cc_stop ? 0 : 5000); cc_ready() && now_ms() < until;) usleep(5000);
    for (int i = 0; i < CC_SLOTS; i++)          /* what still has no room is dropped now, and counted, not forgotten */
        if (slots[i].used && slots[i].ready) { stat_rx.busy++; slots[i].used = slots[i].ready = 0; }
    pthread_mutex_lock(&vq_mu);
    vq_stop = 1;
    pthread_cond_broadcast(&vq_cv);
    pthread_mutex_unlock(&vq_mu);
    for (int i = 0; i < nver; i++) pthread_join(verifier[i], NULL);
    cc_drain(stall_ms);
    if (rc == 0 && !cc_alive()) { rc = 5; word = "no-target"; }
    else if (rc == 0 && cc_complete()) word = "complete";
    else if ((rc == 0 || rc == 4) && stat_rx.conflict && !stat_rx.good) { rc = 7; word = "another-index"; }
    cc_say_abandoned();
    for (int i = 0; i < CC_SLOTS; i++)
        if (slots[i].used && !slots[i].ready) cc_loss(&slots[i]);
    cc_loss_summary();
    cc_say_rx();
    cc_say_verifier();
    if (feed_fd >= 0) {                         /* B5: what the HTTP feed did, said once -- not said = there was none */
        printf("feed %s %llu %llu %llu\n", feed_st.word, (unsigned long long)feed_st.taken,
               (unsigned long long)feed_st.skipped, (unsigned long long)feed_st.bad);
        fflush(stdout);
    }
    for (int i = 0; i < ntg; i++)
        ir_say("chunkcast: recv: %s: %llu of %llu durable, %llu skipped (the drive was behind)\n",
               tg[i].path, (unsigned long long)tg[i].nhave, (unsigned long long)ix.n,
               (unsigned long long)tg[i].skipped);
    ir_say("chunkcast: recv: pass %lld %s: %llu packets, %llu not ours, %llu from another source, %llu malformed, "
           "%llu duplicate, %llu chunks abandoned half-received, %llu dropped (the verifier was busy), %llu failed the "
           "index, %llu already held\n",
           a.pass, word, (unsigned long long)stat_rx.pkts, (unsigned long long)stat_rx.foreign,
           (unsigned long long)stat_rx.stray, (unsigned long long)stat_rx.bad_pkt, (unsigned long long)stat_rx.dup, (unsigned long long)stat_rx.evicted,
           (unsigned long long)stat_rx.busy, (unsigned long long)stat_rx.bad_chunk, (unsigned long long)stat_rx.skipped);
    return cc_report(rc, word);
}

/* --- send -------------------------------------------------------------------- */

static void cc_head(unsigned char *p, int type, uint64_t session)
{
    be_put(p, CC_MAGIC, 4);
    p[4] = (unsigned char)type;
    p[5] = CC_VERSION;
    be_put(p + 6, (uint64_t)cc_part, 2);
    be_put(p + 8, session, 8);
}

/* ANNOUNCE (who this stream is) or END (the pass is over), `times` every `gap_ms`. */
static int cc_say(int s, int type, uint64_t session, long long pass, uint64_t sent, int times, int gap_ms)
{
    unsigned char p[CC_HDR + 20];
    cc_head(p, type, session);
    be_put(p + 16, (uint64_t)pass, 4);
    be_put(p + 20, type == CC_END ? sent : ix.n, 8);
    be_put(p + 28, ix.self_xxh, 8);
    for (int i = 0; i < times && !cc_stop; i++) {
        if (send(s, p, sizeof p, 0) < 0 && errno != ENOBUFS && errno != EAGAIN && errno != ECONNREFUSED) return -1;
        if (gap_ms) usleep((useconds_t)gap_ms * 1000);
    }
    return 0;
}

/* One of the sender's two limits (--kbps, --write-kbps): where its schedule is, when its last step ended,
 * how long it slept (#3125, V4), and how much of the lag it is behind its schedule it may still make up. */
struct cc_clock {
    long long next, last;           /* CLOCK_MONOTONIC, ns */
    long long waited_ns;
    long long forgiven_n, forgiven_ns;      /* #3522: observation only, never part of the schedule */
    long long owed_ns;              /* #3034: time the sender was held reading its source, not yet made up */
};

#define CC_LAG_FORGIVEN_NS 20000000LL       /* a lag past this is not made up -- unless the reads owe it */
#define CC_OWED_MAX_NS 2000000000LL         /* at most this much of a read's time is made up */
#define CC_CATCHUP_PCT 150                  /* the ceiling while it is made up: 1.5 times the limit, never more */
#define CC_AHEAD 4                          /* chunks of the file asked ahead of the one sent (posix_fadvise) */

struct cc_feedback_peer {
    uint64_t id, written, backlog, last_drops;
    uint32_t address;
    long long seen;
    int drops, high, left;     /* drops: this interval saw a rise of the receiver's cumulative count */
};
static struct {
    int fd, on;
    uint64_t session, malformed;
    uint32_t pass;
    long long rate, min, max, step, tick;
    long long cap, safe;       /* #3599: the most max may grow to (0 = never), and the last max that was healthy for a whole window */
    int sat, locked;           /* decisions pinned on max so far; 1 = a decrease put max back: no more widening in this pass */
    struct cc_feedback_peer peers[CC_FB_PEERS];
} feedback;

static void cc_feedback_tick(void)
{
    if (!feedback.on) return;
    long long now = now_ms();
    /* Bounded drain: a noisy control port cannot starve the data path. */
    for (int n = 0; n < 256; n++) {
        unsigned char p[CC_FB_SIZE + 1];
        struct sockaddr_in src;
        socklen_t len = sizeof src;
        ssize_t got = recvfrom(feedback.fd, p, sizeof p, MSG_DONTWAIT, (struct sockaddr *)&src, &len);
        if (got < 0) break;
        if (got >= 5 && be_get(p, 4) == CC_FB_MAGIC && p[4] != 1) continue;
        if (got != CC_FB_SIZE || be_get(p, 4) != CC_FB_MAGIC || p[5] || be_get(p + 20, 4) ||
            !be_get(p + 16, 4) || be_get(p + 16, 4) > 1000000 ||
            !be_get(p + 24, 8) || be_get(p + 32, 8) > ix.n ||
            be_get(p + 40, 8) > (qcap + CC_VQ_MAX + CC_VER_MAX + CC_SLOTS + 2ULL) * CC_CHUNK) {
            feedback.malformed++; continue;
        }
        if (be_get(p + 6, 2) != (uint64_t)cc_part || be_get(p + 8, 8) != feedback.session ||
            be_get(p + 16, 4) != feedback.pass) continue;
        struct cc_feedback_peer *peer = NULL, *empty = NULL;
        uint64_t id = be_get(p + 24, 8);
        for (int j = 0; j < CC_FB_PEERS; j++) {
            struct cc_feedback_peer *q = &feedback.peers[j];
            if (!q->id && !empty) empty = q;
            if (q->id == id && q->address == src.sin_addr.s_addr) { peer = q; break; }
        }
        if (!peer) peer = empty;
        if (!peer) { feedback.malformed++; continue; }
        uint64_t written = be_get(p + 32, 8), drops = be_get(p + 48, 8);
        if (peer->id && (written < peer->written || drops < peer->last_drops)) { feedback.malformed++; continue; }
        peer->id = id; peer->address = src.sin_addr.s_addr;
        peer->written = written; peer->backlog = be_get(p + 40, 8);
        if (drops > peer->last_drops) peer->drops = 1;      /* kept until the next decision, not lost to a later report */
        peer->last_drops = drops;
        peer->seen = now;
    }
    if (now - feedback.tick < 100) return;
    feedback.tick = now;
    int active = 0, healthy = 0, high = 0, bad = 0, missing = 0;
    for (int j = 0; j < CC_FB_PEERS; j++) {
        struct cc_feedback_peer *q = &feedback.peers[j];
        if (!q->id || q->left) continue;
        if (now - q->seen >= 2000) { q->high = 0; q->drops = 0; continue; }
        active++;
        if (now - q->seen >= CC_FB_FRESH_MS) { missing++; continue; }   /* no increase on it; its streak is kept */
        if (q->backlog < fb_low && !q->drops) healthy++;
        if (q->backlog > fb_high) high++;
        if (q->backlog > fb_high || q->drops) bad++;
    }
    for (int j = 0; j < CC_FB_PEERS; j++) {
        struct cc_feedback_peer *q = &feedback.peers[j];
        if (!q->id || q->left || now - q->seen >= 2000) continue;
        if (now - q->seen < CC_FB_FRESH_MS) {
            if (q->backlog > fb_high && healthy && healthy + high == active - missing) { if (!missing) q->high++; }
            else q->high = 0;
        }
        if (q->high >= 10) {
            q->left = 1; active--; bad--;
            char host[INET_ADDRSTRLEN];
            struct in_addr address = { .s_addr = q->address };
            inet_ntop(AF_INET, &address, host, sizeof host);
            printf("left-behind %016llx %s %u\n", (unsigned long long)q->id, host, feedback.pass);
        }
        q->drops = 0;
    }
    const char *reason = "hold";
    if (bad) {
        feedback.rate = feedback.rate * 8 / 10; reason = "decrease";
        if (feedback.max > feedback.safe) { feedback.max = feedback.safe; reason = "retract"; feedback.locked = 1; }   /* #3599: back to the last healthy ceiling, for good */
    }
    else if (active && healthy == active && !missing) { feedback.rate += feedback.step; reason = "increase"; }
    else if (!active) reason = "no-feedback";
    else if (missing) reason = "missing";
    if (feedback.rate < feedback.min) feedback.rate = feedback.min;
    if (feedback.rate > feedback.max) feedback.rate = feedback.max;
    /* #3599: pinned on the ceiling for a whole window with every receiver healthy -- the ceiling was not what the drives allow, only what
     * was asked: it grows by 10%, up to --rate-cap. Anything but a healthy increase on the ceiling starts the window again. */
    if (reason[0] == 'i' && feedback.rate >= feedback.max && !feedback.locked && feedback.max < feedback.cap) {
        if (++feedback.sat >= CC_FB_WIDEN_TICKS) {
            feedback.safe = feedback.max;
            feedback.max += feedback.max / 10 > 0 ? feedback.max / 10 : 1;
            if (feedback.max > feedback.cap) feedback.max = feedback.cap;
            feedback.sat = 0; reason = "widen";
        }
    } else feedback.sat = 0;
    printf("rate %lld %s\n", feedback.rate, reason);
    printf("feedback malformed=%llu\n", (unsigned long long)feedback.malformed);
    fflush(stdout);
}

static int cc_feedback_start(const struct cc_args *a, uint64_t session, const struct sockaddr_in *data)
{
    if (!a->feedback) return 0;
    long long start = a->write_kbps ? a->write_kbps : a->kbps;
    if (!start) return (a->rate_min || a->rate_max) ? -1 : 0;
    feedback.min = a->rate_min ? a->rate_min : start;
    feedback.max = a->rate_max ? a->rate_max : start;
    if (feedback.min > start || feedback.max < start) return -1;
    if (a->rate_cap && a->rate_cap < feedback.max) return -1;
    feedback.cap = a->rate_cap; feedback.safe = feedback.max; feedback.sat = feedback.locked = 0;
    int port = a->control_port ? (int)a->control_port : ntohs(data->sin_port) + 1;
    if (port > 65535) return -1;
    struct sockaddr_in local = { .sin_family = AF_INET, .sin_port = htons((uint16_t)port),
                                .sin_addr = { .s_addr = htonl(INADDR_ANY) } };
    feedback.fd = socket(AF_INET, SOCK_DGRAM | SOCK_CLOEXEC | SOCK_NONBLOCK, 0);
    if (feedback.fd < 0 || bind(feedback.fd, (struct sockaddr *)&local, sizeof local)) return -1;
    feedback.rate = start; feedback.step = start / 50;
    if (!feedback.step) feedback.step = 1;
    feedback.session = session; feedback.pass = (uint32_t)a->pass;
    feedback.tick = now_ms(); feedback.on = 1;
    return 0;
}

static void cc_sleep_until(long long at)
{
    /* Keep the loop alive during disk pacing (one highly compressed chunk can take seconds). */
    while (feedback.on && !cc_stop && at - cc_now_ns() > 100000000LL) {
        cc_feedback_tick();
        struct timespec slice = { 0, 100000000L };
        nanosleep(&slice, NULL);
    }
    cc_feedback_tick();
    struct timespec t = { (time_t)(at / 1000000000LL), (long)(at % 1000000000LL) };
    while (clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &t, NULL) == EINTR && !cc_stop) {}
}

static void cc_clock_start(struct cc_clock *c)
{
    c->next = c->last = cc_now_ns();
    c->waited_ns = c->owed_ns = 0;
    c->forgiven_n = c->forgiven_ns = 0;
}

/* #3535: sleeps until the schedule, moved on by `ns`. Time lost to the other
 * limiter, or to a stall of the network, is not made up in a burst afterwards: a lag past
 * CC_LAG_FORGIVEN_NS starts the schedule again from now. #3034: time lost READING the source is
 * different -- the partition's pass is otherwise 10-14% short of its rate (Hephaestus on metal,
 * 15/11) -- so up to `owed_ns` of the lag is kept and made up in the next steps, each of them no
 * shorter than its time at CC_CATCHUP_PCT of the limit. */
static void cc_pace_ns(struct cc_clock *c, long long ns)
{
    long long now = cc_now_ns();
    long long keep = c->owed_ns < CC_OWED_MAX_NS ? c->owed_ns : CC_OWED_MAX_NS;
    c->next += ns;
    if (now - c->next > CC_LAG_FORGIVEN_NS + keep) {
        c->forgiven_n++;
        c->forgiven_ns += (now - c->next) - keep;
        c->next = now - keep;
    }
    long long at = c->next;
    if (c->next < now) {            /* behind, by what the reads owe at most: made up -- but not faster than the ceiling */
        c->owed_ns = now - c->next < keep ? now - c->next : keep;     /* only a read's time stays owed */
        long long floor = c->last + ns * 100 / CC_CATCHUP_PCT;
        at = floor > now ? floor : now;
    } else {
        c->owed_ns = 0;             /* on time: nothing is owed */
    }
    if (at > now) {
        c->waited_ns += at - now;
        cc_sleep_until(at);
    }
    c->last = at > now ? at : now;
}

/* #3535: preserve the legacy conversion and schedule. */
static void cc_pace(struct cc_clock *c, uint64_t bytes, long long kbps)
{
    long long ns = (long long)(bytes * 8000000ULL / (uint64_t)kbps);
    cc_pace_ns(c, ns);
}

static long long cc_wire_kbps(const struct cc_args *a)
{
    return a->kbps ? (feedback.on && !a->write_kbps ? feedback.rate : a->kbps) : 0;
}

static long long cc_write_kbps(const struct cc_args *a)
{
    return a->write_kbps ? (feedback.on ? feedback.rate : a->write_kbps) : 0;
}

#define CC_SMOOTH_SLICE_NS 250000LL       /* #3535: target time of one smooth batch */

static int cc_slice(const struct cc_args *a, uint64_t k, uint32_t zlen, int batch)
{
    long long wire_kbps = cc_wire_kbps(a), write_kbps = cc_write_kbps(a);
    uint64_t ns_wire = wire_kbps ? CC_PKT * 8000000ULL / (uint64_t)wire_kbps : 0;
    uint64_t ns_write = write_kbps && zlen ?
        (cc_len(k) * CC_PAYLOAD / zlen) * 8000000ULL / (uint64_t)write_kbps : 0;
    uint64_t pkt_ns = ns_write > ns_wire ? ns_write : ns_wire;
    uint64_t cap = pkt_ns ? (uint64_t)CC_SMOOTH_SLICE_NS / pkt_ns : (uint64_t)batch;
    if (cap < 1) cap = 1;
    if (cap > (uint64_t)batch) cap = (uint64_t)batch;
    return (int)cap;
}

/* --- a chunk straight from the device (#3209) ----------------------------------------------------
 * The mirror of the writer: cc_walk gives the runs of the device that chunk k lives on, and here they
 * are READ. What was read is believed only when its XXH64 is the index's (the disk was indexed by an
 * earlier read, and may have changed), and the frame it compresses to only when its length is the
 * index's -- the receivers take a packet of another frame length for a foreign one. chunkcut's three
 * parameters, one call a chunk: the same bytes as the read that made the index, on the same libzstd. */
static struct { int fd; ZSTD_CCtx *c; unsigned char *raw; } csrc;

static int cs_io_read(void *ctx, uint64_t off, uint64_t n, uint64_t pos)
{
    (void)ctx;
    for (uint64_t got = 0; got < n;) {
        ssize_t r = pread(csrc.fd, csrc.raw + pos + got, (size_t)(n - got), (off_t)(off + got));
        if (r > 0) got += (uint64_t)r;
        else if (!(r < 0 && errno == EINTR)) return -1;
    }
    return 0;
}

/* After the index is loaded: the head (a partclone partition's map), the device and the compressor. */
static int cs_open(const struct cc_args *a)
{
    if (a->level < 1 || (!ix.raw && (!a->head || cc_head_load(a->head)))) {
        ir_say("chunkcast: --device needs --level (chunkcut's) and, for a partclone partition, --head\n");
        return -1;
    }
    csrc.fd = open(a->device, O_RDONLY | O_CLOEXEC);
    csrc.c = ZSTD_createCCtx();
    csrc.raw = malloc(CC_CHUNK);
    if (csrc.fd < 0 || !csrc.c || !csrc.raw || ZSTD_isError(ZSTD_CCtx_setParameter(csrc.c, 100, (int)a->level)) ||
        ZSTD_isError(ZSTD_CCtx_setParameter(csrc.c, 200, 1)) || ZSTD_isError(ZSTD_CCtx_setParameter(csrc.c, 201, 1))) {
        ir_say("chunkcast: cannot read %s, or no compressor: %s\n", a->device, strerror(errno));
        return -1;
    }
    return 0;
}

/* The frame of chunk k into z (CC_ZMAX bytes). 0, or -1 with the reason said: nothing wrong goes out. */
static int cs_frame(uint64_t k, unsigned char *z)
{
    uint64_t len = cc_len(k);
    size_t zn;
    if (cc_walk(k, len, cs_io_read, NULL)) {
        ir_say("chunkcast: chunk %llu could not be read from the device: %s\n", (unsigned long long)k, strerror(errno));
        return -1;
    }
    if (cc_xxh(csrc.raw, (size_t)len) != ix.xxh[k]) {
        ir_say("chunkcast: chunk %llu on the device is not the chunk its index describes -- the disk changed"
               " since it was indexed; nothing of it is sent\n", (unsigned long long)k);
        return -1;
    }
    zn = ZSTD_compress2(csrc.c, z, CC_ZMAX, csrc.raw, (size_t)len);
    if (ZSTD_isError(zn) || zn != ix.zlen[k]) {
        ir_say("chunkcast: chunk %llu compressed to %llu bytes and its index holds %u -- another level or another"
               " libzstd than the read that made the index; nothing of it is sent\n", (unsigned long long)k,
               ZSTD_isError(zn) ? 0ULL : (unsigned long long)zn, ix.zlen[k]);
        return -1;
    }
    return 0;
}

/* The stream this sender has announced, for the time it sends nothing. A receiver's stall clock is reset
 * by OUR packets only, and a sender can be busy without sending: a repair pass of a direct send reads the
 * whole partition to send a few chunks of it, and a read can wait for the disk. So the ANNOUNCE goes on,
 * once a second, from every place that takes time -- between chunks, through the chunks that are read and
 * not sent, and inside a read that has nothing yet (review of #3216: a receiver that heard nothing for
 * --stall-s left before the one chunk it lacked, near the end of a large partition, was reached).
 * 0, or -1 when the socket refused the packet. */
static struct { int on, s; uint64_t session; long long pass, last; } cc_tx;

static int cc_keepalive(void)
{
    long long now;
    if (!cc_tx.on || (now = now_ms()) - cc_tx.last < 1000) return 0;
    cc_tx.last = now;
    return cc_say(cc_tx.s, CC_ANNOUNCE, cc_tx.session, cc_tx.pass, 0, 1, 0);
}

/* --stdin (#3161): `n` bytes of the stream into `z`, or read and dropped when `z` is NULL.
 * -1 = the stream ended, or failed, before them. */
static int cc_take(unsigned char *z, uint64_t n)
{
    static unsigned char drop[1 << 16];
    while (n && !cc_stop) {
        size_t want = z ? (size_t)n : (n < sizeof drop ? (size_t)n : sizeof drop);
        struct pollfd pf = { .fd = STDIN_FILENO, .events = POLLIN };
        /* a packet the socket refused here is refused again at the next one the caller sends, and said there */
        (void)cc_keepalive();
        if (cc_tx.on && poll(&pf, 1, 250) == 0) continue;     /* nothing to read yet: alive, and look again */
        ssize_t r = read(STDIN_FILENO, z ? z : drop, want);
        if (r > 0) {
            n -= (uint64_t)r;
            if (z) z += r;
        } else if (!(r < 0 && errno == EINTR)) return -1;
    }
    return n ? -1 : 0;
}

/* A zstd frame begins here (RFC 8878): the cheapest sign that the stream and the index agree on
 * where chunk k is. What the frame HOLDS is the receivers' to check, against the same index. */
static int cc_frame_starts(const unsigned char *z, uint64_t n)
{
    return n >= 4 && z[0] == 0x28 && z[1] == 0xb5 && z[2] == 0x2f && z[3] == 0xfd;
}

/* #3522: ties have a fixed order, independent of qsort's implementation. */
static int cc_order_index(const void *a, const void *b)
{
    uint64_t x = *(const uint64_t *)a, y = *(const uint64_t *)b;
    return (x > y) - (x < y);
}

static int cc_order_size(const void *a, const void *b)
{
    uint64_t x = *(const uint64_t *)a, y = *(const uint64_t *)b;
    if (ix.zlen[x] != ix.zlen[y]) return ix.zlen[x] > ix.zlen[y] ? 1 : -1;
    return cc_order_index(a, b);
}

static struct { long long read_ns, enobufs, eagain, eintr, refused, over_ms; } sendstats;
/* #3535: successful sends only. A 1 ms window is `over` past 150% (CC_CATCHUP_PCT) of the wire limit -- the most a
 * catch-up step may go; a schedule held at the limit lands 4 or 5 slices in a window, so the limit itself is noise. */
static struct { long long window; uint64_t bytes, ceiling; } sendwindow = { .window = -1 };

static void cc_send_window(long long window, uint64_t bytes, long long kbps)
{
    if (!kbps) return;
    if (window != sendwindow.window) {
        if (sendwindow.bytes > sendwindow.ceiling) sendstats.over_ms++;
        sendwindow.window = window;
        sendwindow.bytes = 0;
    }
    sendwindow.bytes += bytes;
    sendwindow.ceiling = (uint64_t)kbps / 8 * CC_CATCHUP_PCT / 100;
}

/* #3535: CHUNKCAST_READER=thread -- the source file is read by a thread of its own, in the order the pass sends (the
 * same `ord` and `pick` as the loop), into a ring of CHUNKCAST_READQ slots; the send loop takes a chunk when one is
 * whole and puts the slot back when it has sent it, so a slot is either whole or not taken: a half-read chunk is never
 * seen. The slot being sent is one of the READQ. A chunk the thread cannot read whole stops it, and the loop meets that
 * failure at that chunk -- after the whole chunks before it, never with a part of it. */
#define CC_READQ 8
#define CC_READQ_MAX 64
static struct {
    int on, started, stop, done, cap, head, tail, ready, held;     /* head: next slot to fill; tail: next to take */
    struct { unsigned char *z; uint64_t k; } slot[CC_READQ_MAX];
    uint64_t err_k;                         /* with `failed`: the chunk the file did not give whole */
    int failed;
    long long read_ns, full_ns, empty_ns;   /* the thread's reads; the thread's waits for a slot; the loop's waits for a chunk */
    int fd;
    const uint64_t *ord;
    uint64_t nord;
    const unsigned char *pick;
    pthread_t th;
    pthread_mutex_t mu;
    pthread_cond_t not_empty, not_full;
} rq;

static void *cc_readq_run(void *arg)
{
    (void)arg;
    for (uint64_t oi = 0; oi < (rq.ord ? rq.nord : ix.n); oi++) {
        uint64_t k = rq.ord ? rq.ord[oi] : oi;
        uint32_t zlen = ix.zlen[k];
        if (rq.pick && !cc_bit(rq.pick, k)) continue;
        long long waited = 0;
        pthread_mutex_lock(&rq.mu);
        while (rq.ready + rq.held >= rq.cap && !rq.stop) {
            long long t = cc_now_ns();
            pthread_cond_wait(&rq.not_full, &rq.mu);
            waited += cc_now_ns() - t;
        }
        rq.full_ns += waited;
        int stop = rq.stop;
        unsigned char *z = rq.slot[rq.head].z;
        pthread_mutex_unlock(&rq.mu);
        if (stop) return NULL;
        long long t0 = cc_now_ns();
        uint32_t got = 0;
        while (got < zlen) {
            ssize_t r = pread(rq.fd, z + got, zlen - got, (off_t)(ix.off[k] + got));
            if (r > 0) got += (uint32_t)r;
            else if (!(r < 0 && errno == EINTR)) break;
        }
        pthread_mutex_lock(&rq.mu);
        rq.read_ns += cc_now_ns() - t0;
        if (got < zlen) {
            rq.failed = 1;
            rq.err_k = k;
            rq.done = 1;
            pthread_cond_broadcast(&rq.not_empty);
            pthread_mutex_unlock(&rq.mu);
            return NULL;
        }
        rq.slot[rq.head].k = k;
        rq.head = (rq.head + 1) % rq.cap;
        rq.ready++;
        pthread_cond_signal(&rq.not_empty);
        pthread_mutex_unlock(&rq.mu);
    }
    pthread_mutex_lock(&rq.mu);
    rq.done = 1;
    pthread_cond_broadcast(&rq.not_empty);
    pthread_mutex_unlock(&rq.mu);
    return NULL;
}

/* 0 = started (or already). -1 = said why not. Signals stay with the loop: the thread blocks them. */
static int cc_readq_start(int cap, int fd, const uint64_t *ord, uint64_t nord, const unsigned char *pick)
{
    pthread_condattr_t at;
    sigset_t all, old;
    int err;
    rq.cap = cap;
    rq.fd = fd;
    rq.ord = ord;
    rq.nord = nord;
    rq.pick = pick;
    for (int i = 0; i < cap; i++)
        if (!(rq.slot[i].z = malloc(CC_ZMAX))) { ir_say("chunkcast: send: no memory for %d chunks of reader queue\n", cap); return -1; }
    pthread_condattr_init(&at);
    pthread_condattr_setclock(&at, CLOCK_MONOTONIC);
    pthread_mutex_init(&rq.mu, NULL);
    pthread_cond_init(&rq.not_empty, &at);
    pthread_cond_init(&rq.not_full, NULL);
    pthread_condattr_destroy(&at);
    sigemptyset(&all);
    sigaddset(&all, SIGTERM);
    sigaddset(&all, SIGINT);
    pthread_sigmask(SIG_BLOCK, &all, &old);
    err = pthread_create(&rq.th, NULL, cc_readq_run, NULL);
    pthread_sigmask(SIG_SETMASK, &old, NULL);
    if (err) { ir_say("chunkcast: send: cannot start the reader thread: %s\n", strerror(err)); return -1; }
    rq.started = 1;
    return 0;
}

/* Ends the thread and waits for it: at the end of a pass, on a failure and on a signal alike. Safe to call twice. */
static void cc_readq_stop(void)
{
    if (!rq.started) return;
    pthread_mutex_lock(&rq.mu);
    rq.stop = 1;
    pthread_cond_broadcast(&rq.not_full);
    pthread_mutex_unlock(&rq.mu);
    pthread_join(rq.th, NULL);
    rq.started = 0;
}

/* The next chunk, whole, in *z. 0 = taken. 1 = the reader ended before it (rq.failed says which chunk). 2 = a signal.
 * -1 = the keepalive could not be sent (errno). While it waits the stream is kept alive and the feedback is read. */
static int cc_readq_take(unsigned char **z, uint64_t k)
{
    long long t0 = 0;
    for (;;) {
        pthread_mutex_lock(&rq.mu);
        if (!rq.ready && !rq.done && !cc_stop) {
            struct timespec at;
            if (!t0) t0 = cc_now_ns();
            clock_gettime(CLOCK_MONOTONIC, &at);
            at.tv_nsec += 50000000L;
            if (at.tv_nsec >= 1000000000L) { at.tv_sec++; at.tv_nsec -= 1000000000L; }
            pthread_cond_timedwait(&rq.not_empty, &rq.mu, &at);
        }
        if (rq.ready) {
            int i = rq.tail;
            rq.tail = (rq.tail + 1) % rq.cap;
            rq.ready--;
            rq.held = 1;
            *z = rq.slot[i].z;
            int right = rq.slot[i].k == k;
            pthread_mutex_unlock(&rq.mu);
            if (t0) rq.empty_ns += cc_now_ns() - t0;
            if (!right) { rq.failed = 1; rq.err_k = k; return 1; }
            return 0;
        }
        int done = rq.done;
        pthread_mutex_unlock(&rq.mu);
        if (done || cc_stop) {
            if (t0) rq.empty_ns += cc_now_ns() - t0;
            return done ? 1 : 2;
        }
        cc_feedback_tick();
        if (cc_keepalive()) return -1;
    }
}

static void cc_readq_release(void)
{
    pthread_mutex_lock(&rq.mu);
    rq.held = 0;
    pthread_cond_signal(&rq.not_full);
    pthread_mutex_unlock(&rq.mu);
}

static int cc_send_run(int argc, char **argv)
{
    struct cc_args a;
    struct sockaddr_in sin;
    struct stat st;
    uint64_t session, sent = 0, zsent = 0;
    const char *sp;
    unsigned char *pick = NULL, *z;
    int s, fd, snd = 4 << 20;
    if (cc_parse(argc, argv, &a)) return 2;
    long long ahead, cap, low, high, batch, readq;
    int ahead_set, smooth, reader_thread;
    uint64_t *ord = NULL, nord = 0, ahead_pos = 0;
    if (cc_knob("CHUNKCAST_AHEAD", CC_AHEAD, 1, 64, &ahead, &ahead_set) ||
        cc_knob("CHUNKCAST_QCAP", CC_QCAP, 8, CC_QCAP_MAX, &cap, NULL) ||
        cc_knob("CHUNKCAST_FB_LOW", 2, 1, 254, &low, NULL) ||
        cc_knob("CHUNKCAST_FB_HIGH", 6, 2, 255, &high, NULL) ||
        cc_knob("CHUNKCAST_BATCH", CC_BATCH, 1, CC_BATCH, &batch, NULL) ||
        cc_knob("CHUNKCAST_READQ", CC_READQ, 2, CC_READQ_MAX, &readq, NULL) ||
        cc_pace_mode(&smooth) || cc_reader_mode(&reader_thread)) return 2;
    if (low >= high || high >= cap) {
        ir_say("chunkcast: CHUNKCAST_FB_LOW=%lld CHUNKCAST_FB_HIGH=%lld CHUNKCAST_QCAP=%lld"
               " must satisfy LOW < HIGH < QCAP -- nothing is started\n", low, high, cap);
        return 2;
    }
    qcap = (int)cap;
    fb_low = (uint64_t)low * CC_CHUNK;
    fb_high = (uint64_t)high * CC_CHUNK;
    if (a.mixed && a.from_stdin) {
        ir_say("chunkcast: send: --order mixed: a stream on stdin cannot be reordered\n");
        return 2;
    }
    if (reader_thread && (a.from_stdin || a.device || ahead_set)) {
        ir_say("chunkcast: send: CHUNKCAST_READER=thread reads a --file, and is not run with --stdin, --device or"
               " CHUNKCAST_AHEAD (no read-ahead advice with it) -- nothing is started\n");
        return 2;
    }
    sp = a.session;
    if (!sp || cc_hex16(&sp, 0, &session) || a.part < 0 || a.pass < 1 || !a.index ||
        (a.file != NULL) + (a.from_stdin != 0) + (a.device != NULL) != 1 || cc_addr(a.addr, &sin) || a.argi != argc) {
        ir_say("chunkcast: send: --to ADDR:PORT, --session HEX16, --part, --pass, --index and one of --file and"
               " --stdin are required (or --device, for a repair)\n");
        return 2;
    }
    cc_part = (int)a.part;
    if (cc_index_load(a.index, a.index_xxh)) return 2;
    if (cc_feedback_start(&a, session, &sin)) {
        ir_say("chunkcast: send: invalid feedback bounds/control port or cannot bind control socket\n");
        return 2;
    }
    if (a.device && cs_open(&a)) return 2;
    fd = a.file ? open(a.file, O_RDONLY | O_CLOEXEC) : STDIN_FILENO;
    if (a.file && (fd < 0 || fstat(fd, &st) || (uint64_t)st.st_size != ix.file_zbytes)) {
        ir_say("chunkcast: send: %s is not the %llu bytes its index describes\n", a.file,
               (unsigned long long)ix.file_zbytes);
        return 2;
    }
    if (a.chunks && !(pick = cc_pick(a.chunks))) {     /* a repair pass: only these */
        ir_say("chunkcast: send: %s is not a list of chunk ranges\n", a.chunks);
        return 2;
    }
    if (a.mixed || (ahead_set && a.file)) {
        /* #3522: only selected chunks occupy positions, including in a repair pass. */
        for (uint64_t k = 0; k < ix.n; k++) if (!pick || cc_bit(pick, k)) nord++;
        ord = malloc((size_t)(nord ? nord : 1) * sizeof *ord);
        if (!ord) { ir_say("chunkcast: send: no memory for send order\n"); return 2; }
        uint64_t at = 0;
        for (uint64_t k = 0; k < ix.n; k++) if (!pick || cc_bit(pick, k)) ord[at++] = k;
        if (a.mixed) {
            uint64_t *idx = malloc((size_t)(nord ? nord : 1) * sizeof *idx), light = nord / 2;
            if (!idx) { free(ord); ir_say("chunkcast: send: no memory for mixed order\n"); return 2; }
            memcpy(idx, ord, (size_t)nord * sizeof *idx);
            qsort(idx, (size_t)nord, sizeof *idx, cc_order_size);
            qsort(idx, (size_t)light, sizeof *idx, cc_order_index);
            qsort(idx + light, (size_t)(nord - light), sizeof *idx, cc_order_index);
            at = 0;
            for (uint64_t j = 0; j < nord - light; j++) {
                ord[at++] = idx[light + j];
                if (j < light) ord[at++] = idx[j];
            }
            free(idx);
        }
    }
    s = socket(AF_INET, SOCK_DGRAM | SOCK_CLOEXEC, 0);
    if (s < 0) return 2;
    setsockopt(s, SOL_SOCKET, SO_SNDBUF, &snd, sizeof snd);
    if (IN_MULTICAST(ntohl(sin.sin_addr.s_addr))) {
        unsigned char ttl = (unsigned char)a.ttl;
        struct ip_mreqn mr = { 0 };
        if (a.iface && !(mr.imr_ifindex = (int)if_nametoindex(a.iface))) {
            ir_say("chunkcast: send: no interface '%s'\n", a.iface);
            return 2;
        }
        if (setsockopt(s, IPPROTO_IP, IP_MULTICAST_TTL, &ttl, sizeof ttl) ||
            (a.iface && setsockopt(s, IPPROTO_IP, IP_MULTICAST_IF, &mr, sizeof mr))) {
            ir_say("chunkcast: send: cannot set the multicast options: %s\n", strerror(errno));
            return 2;
        }
    }
    if (connect(s, (struct sockaddr *)&sin, sizeof sin) || !(z = malloc(CC_ZMAX))) {
        ir_say("chunkcast: send: cannot send to %s: %s\n", a.addr, strerror(errno));
        return 2;
    }
    /* --stdin: a read that waits for the disk is not left waiting for a signal -- as apply's */
    signal(SIGTERM, a.from_stdin ? cc_on_signal_apply : cc_on_signal);
    signal(SIGINT, a.from_stdin ? cc_on_signal_apply : cc_on_signal);

    static unsigned char pk[CC_BATCH][CC_PKT];
    struct mmsghdr msgs[CC_BATCH];
    struct iovec iov[CC_BATCH];
    struct cc_clock wire, disk;             /* #3125 (V4): how long each limit held the sender back; #3034: what it owes */
    long long t0, told;
    uint64_t ahead_k = 0, ahead_n = 0;      /* #3034: the last chunk asked of the file ahead of time, and how many are */
    if (a.from_stdin && ix.head_zbytes) {
        /* the head frame comes first and is not sent: the receivers took it over HTTP, checked. Read before
         * the stream is announced -- partclone reads the whole bit map before its first byte. */
        unsigned char first[4];
        if (cc_take(first, sizeof first) || !cc_frame_starts(first, sizeof first) || cc_take(NULL, ix.head_zbytes - 4)) {
            if (cc_stop) goto stopped;
            ir_say("chunkcast: send: the stream on stdin does not begin with the head its index describes\n");
            return 9;
        }
    }
    printf("knobs ahead=%lld readahead=%s order=%s fb_low=%lld fb_high=%lld qcap=%d pace=%s batch=%d reader=%s readq=%lld\n",
           ahead, ahead_set || a.mixed ? "on" : "legacy", a.mixed ? "mixed" : "seq", low, high, qcap,
           smooth ? "smooth" : "legacy", (int)batch, reader_thread ? "thread" : "inline", readq);
    if (fflush(stdout)) return 2;
    if (reader_thread) {
        /* before the announce: the first chunks are read while the lead-in goes out */
        if (cc_readq_start((int)readq, fd, ord, nord, pick)) return 2;
        rq.on = 1;
    }
    t0 = told = now_ms();
    if (cc_say(s, CC_ANNOUNCE, session, a.pass, 0, (int)(a.lead_ms / 200) + 1, a.lead_ms ? 200 : 0)) goto broken;
    cc_tx.s = s;
    cc_tx.session = session;
    cc_tx.pass = a.pass;
    cc_tx.last = now_ms();
    cc_tx.on = 1;                             /* announced: from here the stream is kept alive */
    cc_clock_start(&wire);
    disk = wire;
    for (uint64_t oi = 0; oi < (ord ? nord : ix.n) && !cc_stop; oi++) {
        uint64_t k = ord ? ord[oi] : oi;
        int picked = !pick || cc_bit(pick, k);
        uint32_t zlen = ix.zlen[k], count = (zlen + CC_PAYLOAD - 1) / CC_PAYLOAD;
        if (cc_keepalive()) goto broken;        /* before the chunk, sent or not: a skipped one is time too */
        long long read_at = cc_now_ns();        /* #3034: what reading the source holds the sender, the pacing makes up */
        if (a.from_stdin) {
            /* every frame is taken, in order -- also the ones a repair pass does not send */
            if (cc_take(z, zlen) || !cc_frame_starts(z, zlen)) {
                if (cc_stop) break;
                ir_say("chunkcast: send: the stream on stdin is not the file its index describes (chunk %llu of %llu)"
                       " -- nothing more is sent\n", (unsigned long long)k, (unsigned long long)ix.n);
                return 9;
            }
        }
        if (picked && a.device && cs_frame(k, z)) return 9;       /* read from its own place, and checked (#3209) */
        if (picked && rq.on) {
            /* #3535: the thread read it; what this loop waits for is the time the source held it */
            int got = cc_readq_take(&z, k);
            if (got < 0) goto broken;
            if (got == 2) break;
            if (got == 1) {
                ir_say("chunkcast: send: %s ended inside chunk %llu\n", a.file, (unsigned long long)(rq.failed ? rq.err_k : k));
                return 1;
            }
        }
        for (uint32_t got = a.file && picked && !rq.on ? 0 : zlen; got < zlen;) {
            ssize_t r = pread(fd, z + got, zlen - got, (off_t)(ix.off[k] + got));
            if (r > 0) got += (uint32_t)r;
            else if (!(r < 0 && errno == EINTR)) {
                ir_say("chunkcast: send: %s ended inside chunk %llu\n", a.file, (unsigned long long)k);
                return 1;
            }
        }
        if (rq.on) {
            /* #3535: the thread reads; nothing is advised */
        } else if (a.file && ord) {
            /* #3522: advise each future send position once; the default keeps #3523's legacy path. */
            if (ahead_pos < oi + 1) ahead_pos = oi + 1;
            while (ahead_pos < nord && ahead_pos <= oi + (uint64_t)ahead) {
                uint64_t next = ord[ahead_pos++];
                posix_fadvise(fd, (off_t)ix.off[next], (off_t)ix.zlen[next], POSIX_FADV_WILLNEED);
            }
        } else if (a.file) {
            /* #3034: the next CC_AHEAD chunks this pass sends are asked of the file now, so their read is not
             * one more wait of the sender: the kernel reads them while this one goes out */
            if (ahead_k <= k) { ahead_k = k; ahead_n = 0; }
            else if (picked && ahead_n) ahead_n--;
            while (ahead_n < (uint64_t)ahead && ahead_k + 1 < ix.n) {
                ahead_k++;                      /* #3523: always step; inside the `||` below it was skipped when pick is NULL */
                if (!pick || cc_bit(pick, ahead_k)) {
                    posix_fadvise(fd, (off_t)ix.off[ahead_k], (off_t)ix.zlen[ahead_k], POSIX_FADV_WILLNEED);
                    ahead_n++;
                }
            }
        }
        long long read_ns = cc_now_ns() - read_at;
        if (!rq.on) sendstats.read_ns += read_ns;   /* with the thread: its own reads, at the end */
        wire.owed_ns += read_ns;
        disk.owed_ns += read_ns;
        if (!picked) continue;
        cc_feedback_tick();
        uint64_t pay = 0;                       /* #3535: payload bytes of this chunk sent so far */
        uint32_t wire_count = count + (a.fec ? (count + FEC_K - 1) / FEC_K : 0);
        for (uint32_t seq = 0; seq < wire_count && !cc_stop;) {
            int n = 0;
            int slice = smooth ? cc_slice(&a, k, zlen, (int)batch) : (int)batch;
            uint64_t bytes = 0, pay_before = pay;
            memset(msgs, 0, sizeof msgs);
            for (; n < slice && seq < wire_count; n++, seq++) {
                uint32_t len = seq + 1 == count ? zlen - seq * CC_PAYLOAD : CC_PAYLOAD;
                int parity = seq >= count;
                cc_head(pk[n], parity ? CC_PARITY : CC_DATA, session);
                be_put(pk[n] + 16, k, 8);
                be_put(pk[n] + 24, zlen, 4);
                be_put(pk[n] + 28, parity ? seq - count : seq, 4);
                unsigned char *body = pk[n] + CC_HDR + CC_DATA_HDR;
                if (parity) {
                    unsigned group = seq - count;
                    fec_encode(body, z + (size_t)group * FEC_K * CC_PAYLOAD, cc_group_bytes(zlen, group));
                } else {
                    memcpy(body, z + (size_t)seq * CC_PAYLOAD, len);
                    pay += len;
                }
                iov[n] = (struct iovec){ pk[n], CC_HDR + CC_DATA_HDR + len };
                msgs[n].msg_hdr.msg_iov = &iov[n];
                msgs[n].msg_hdr.msg_iovlen = 1;
                bytes += CC_HDR + CC_DATA_HDR + len;
            }
            for (int done = 0; done < n && !cc_stop;) {
                int w = sendmmsg(s, msgs + done, (unsigned)(n - done), 0);
                if (w > 0) {
                    long long window = cc_now_ns() / 1000000;
                    uint64_t sent_bytes = 0;
                    for (int i = done; i < done + w; i++) sent_bytes += iov[i].iov_len;
                    cc_send_window(window, sent_bytes, cc_wire_kbps(&a));
                    done += w;
                } else {
                    int err = errno;          /* #3522: count retries without changing their control flow */
                    if (err == ENOBUFS || err == EAGAIN || err == EINTR) {
                        if (err == ENOBUFS) sendstats.enobufs++;
                        else if (err == EAGAIN) sendstats.eagain++;
                        else sendstats.eintr++;
                        usleep(1000);
                    } else if (err == ECONNREFUSED) sendstats.refused++; /* a unicast receiver that left */
                    else goto broken;
                }
            }
            cc_feedback_tick();
            if (smooth) {
                /* #3535: telescoping payload shares account for every decompressed byte once. */
                uint64_t data_bytes = zlen ? cc_len(k) * pay / zlen - cc_len(k) * pay_before / zlen : 0;
                long long wire_kbps = cc_wire_kbps(&a), write_kbps = cc_write_kbps(&a);
                long long ns_wire = wire_kbps ? (long long)(bytes * 8000000ULL / (uint64_t)wire_kbps) : 0;
                long long ns_write = write_kbps ? (long long)(data_bytes * 8000000ULL / (uint64_t)write_kbps) : 0;
                if (wire_kbps || write_kbps) {
                    long long waited = wire.waited_ns;
                    cc_pace_ns(&wire, ns_write > ns_wire ? ns_write : ns_wire);
                    if (ns_write > ns_wire) {
                        disk.waited_ns += wire.waited_ns - waited;
                        wire.waited_ns = waited;
                    }
                }
            } else if (a.kbps) cc_pace(&wire, bytes, cc_wire_kbps(&a));
        }
        if (!smooth && a.write_kbps) cc_pace(&disk, cc_len(k), cc_write_kbps(&a));
        sent++;
        zsent += zlen;
        if (picked && rq.on) cc_readq_release();
        long long now = now_ms();
        if (now - told >= 1000) {
            told = now;
            printf("sent %llu %llu\n", (unsigned long long)sent, (unsigned long long)zsent);
            printf("paced %lld %lld\n", wire.waited_ns / 1000000, disk.waited_ns / 1000000);   /* its own line: sent and done are matched whole */
            fflush(stdout);
        }
    }
    if (cc_stop) {
stopped:
        ir_say("chunkcast: send: stopped by a signal after %llu chunks\n", (unsigned long long)sent);
        return 6;
    }
    if (a.from_stdin) {
        /* the stream ends where the index does: a byte more is a file the index does not describe.
         * The end can be slow to come (the tools before us are still closing): the stream stays alive. */
        unsigned char more;
        ssize_t r;
        struct pollfd pf = { .fd = STDIN_FILENO, .events = POLLIN };
        while (!cc_stop && poll(&pf, 1, 250) == 0)
            if (cc_keepalive()) goto broken;
        do r = read(STDIN_FILENO, &more, 1); while (r < 0 && errno == EINTR);
        if (cc_stop) goto stopped;
        if (r != 0) {
            ir_say("chunkcast: send: the stream on stdin goes on past the %llu bytes its index describes\n",
                   (unsigned long long)ix.file_zbytes);
            return 9;
        }
    }
    if (cc_say(s, CC_END, session, a.pass, sent, (int)(a.tail_ms / 100) + 1, a.tail_ms ? 100 : 0)) goto broken;
    cc_readq_stop();                        /* before ord goes, and before its counters are read */
    if (rq.on) sendstats.read_ns = rq.read_ns;
    free(ord);
    if (sendwindow.bytes > sendwindow.ceiling) sendstats.over_ms++;
    printf("sendstats read_ms=%lld lag_forgiven_wire=%lld lag_forgiven_disk=%lld forgiven_ms_wire=%lld"
           " forgiven_ms_disk=%lld enobufs=%lld eagain=%lld eintr=%lld refused=%lld over_ms=%lld"
           " readq_empty_ms=%lld readq_full_ms=%lld\n",
           sendstats.read_ns / 1000000, wire.forgiven_n, disk.forgiven_n, wire.forgiven_ns / 1000000,
           disk.forgiven_ns / 1000000, sendstats.enobufs, sendstats.eagain, sendstats.eintr, sendstats.refused,
           sendstats.over_ms, rq.empty_ns / 1000000, rq.full_ns / 1000000);
    printf("paced %lld %lld\n", wire.waited_ns / 1000000, disk.waited_ns / 1000000);
    printf("done %llu %llu %lld\n", (unsigned long long)sent, (unsigned long long)zsent, now_ms() - t0);
    return fflush(stdout) ? 2 : 0;
broken:
    ir_say("chunkcast: send: the socket refused a packet: %s\n", strerror(errno));
    return 1;
}

static int cc_send(int argc, char **argv)
{
    int rc = cc_send_run(argc, argv);
    cc_readq_stop();                        /* #3535: every way out of a pass, the thread ends and is waited for */
    return rc;
}

/* --- serve (#3209) --------------------------------------------------------------------------- */

static int sv_all(int c, const void *buf, size_t n)
{
    const unsigned char *p = buf;
    while (n) {
        ssize_t w = send(c, p, n, MSG_NOSIGNAL);
        if (w > 0) { p += w; n -= (size_t)w; }
        else if (!(w < 0 && errno == EINTR)) return -1;
    }
    return 0;
}

#define SV_READ_MS 10000                /* the whole request, from the connection's start -- not each recv */
#define SV_TOKEN_MAX 128
#define SV_TOKEN_HDR "\nx-imagesrv-serve-token:"
static char sv_token[SV_TOKEN_MAX + 1];
static size_t sv_toklen;
static long long sv_read_ms = SV_READ_MS;     /* CHUNKCAST_SERVE_READ_MS: a test's, so that a slow reader costs seconds, not a quarter minute */
static long long sv_until_ms;                 /* B29: 0 = no end of its own; else the now_ms() at which it ends */
static long long sv_read_mibs, sv_next;      /* B29: the read ceiling, and when the next chunk may be read */

/* B29: wait until reading one more chunk keeps the server within --read-mibs -- no credit for the time it was idle,
   so a request after a quiet minute is paced like any other. -1 = it must end first (stop, --until-s) */
static int sv_pace(void)
{
    if (!sv_read_mibs) return 0;
    if (sv_next < now_ms()) sv_next = now_ms();
    while (now_ms() < sv_next) {
        if (cc_stop || (sv_until_ms && now_ms() >= sv_until_ms)) return -1;
        usleep(20000);
    }
    sv_next += 4000 / sv_read_mibs;                             /* 4MiB a chunk at M MiB/s, in milliseconds */
    return 0;
}

/* The round's token, from a file (not argv: ps shows that). 0 = loaded; -1 = absent, unreadable, or too short to be one. */
static int sv_token_load(const char *path)
{
    FILE *f = path ? fopen(path, "r") : NULL;
    size_t n;
    if (!f) return -1;
    n = fread(sv_token, 1, SV_TOKEN_MAX, f);
    fclose(f);
    while (n && (sv_token[n - 1] == '\n' || sv_token[n - 1] == '\r' || sv_token[n - 1] == ' ')) n--;
    sv_token[n] = 0;
    for (size_t i = 0; i < n; i++)
        if ((unsigned char)sv_token[i] <= ' ' || (unsigned char)sv_token[i] >= 0x7f) return -1;
    sv_toklen = n;
    return n >= 16 ? 0 : -1;
}

/* 1 = the request carries the token. Every byte is looked at whatever the first difference: the time says nothing. */
static int sv_token_ok(const char *req)
{
    const char *v = strcasestr(req, SV_TOKEN_HDR);
    size_t n = 0;
    unsigned char diff;
    if (!v) return 0;
    v += sizeof SV_TOKEN_HDR - 1;
    while (*v == ' ' || *v == '\t') v++;
    while (v[n] && v[n] != '\r' && v[n] != '\n') n++;
    while (n && (v[n - 1] == ' ' || v[n - 1] == '\t')) n--;
    diff = (unsigned char)(n != sv_toklen);
    for (size_t i = 0; i < sv_toklen; i++) diff |= (unsigned char)((i < n ? v[i] : 0) ^ sv_token[i]);
    return !diff;
}

/* The request's bytes, until its blank line -- all of them within SV_READ_MS of `t0`. 0 = got them; -1 = close it. */
static int sv_read(int c, char *req, size_t cap, long long t0)
{
    size_t got = 0;
    while (!memmem(req, got, "\r\n\r\n", 4)) {
        struct pollfd pf = { .fd = c, .events = POLLIN };
        long long left = sv_read_ms - (now_ms() - t0);
        ssize_t r;
        if (cc_stop || left <= 0 || poll(&pf, 1, (int)left) <= 0) return -1;
        r = recv(c, req + got, cap - 1 - got, 0);
        if (r <= 0 || (got += (size_t)r) >= cap - 1) return -1;
    }
    *(char *)memmem(req, got, "\r\n\r\n", 4) = 0;                   /* the headers only */
    return 0;
}

/* The one request of a connection, and its one answer. 0 = answered whole; -1 = refused or cut. */
static int sv_one(int c, unsigned char *z, long long t0)
{
    char req[8192], head[160];
    const char *range;
    uint64_t from, to, lo = 0, hi = 0, total = 0;
    int found = 0;
    static const char no403[] = "HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n";
    if (sv_read(c, req, sizeof req, t0)) return -1;
    if (!sv_token_ok(req)) {            /* not the image server of THIS round: nothing is read from the disk for it */
        sv_all(c, no403, sizeof no403 - 1);
        return -1;
    }
    range = strcasestr(req, "\nrange: bytes=");
    if (range && !strncmp(req, "GET ", 4) && (range += 14, !cc_dec(&range, '-', &from))) {
        const char *end = range;
        while (*end >= '0' && *end <= '9') end++;
        if (end > range && !cc_dec(&range, *end, &to))
            for (uint64_t k = 0; k < ix.n && !found; k++) {
                if (ix.off[k] == from) lo = k;
                if (ix.off[k] + ix.zlen[k] - 1 == to && ix.off[lo] == from && lo <= k) { hi = k; found = 1; }
            }
    }
    if (!found) {       /* not whole frames of this index: nothing here can answer it */
        static const char no[] = "HTTP/1.1 416 Range Not Satisfiable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n";
        sv_all(c, no, sizeof no - 1);
        return -1;
    }
    for (uint64_t k = lo; k <= hi; k++) total += ix.zlen[k];
    snprintf(head, sizeof head, "HTTP/1.1 206 Partial Content\r\nContent-Type: application/octet-stream\r\n"
             "Content-Length: %llu\r\nConnection: close\r\n\r\n", (unsigned long long)total);
    if (sv_all(c, head, strlen(head))) return -1;
    for (uint64_t k = lo; k <= hi && !cc_stop; k++) {
        if (sv_until_ms && now_ms() >= sv_until_ms) return -1;        /* past its end: a short body, which the asker sees */
        if (sv_pace() || cs_frame(k, z) || sv_all(c, z, ix.zlen[k])) return -1;     /* a short body: the asker sees it failed */
    }
    return cc_stop ? -1 : 0;
}

static int cc_serve(int argc, char **argv)
{
    struct cc_args a;
    struct sockaddr_in sin, peer, only = { 0 };
    struct timeval patience = { 30, 0 };
    unsigned char *z;
    int ls, one = 1;
    if (cc_parse(argc, argv, &a)) return 2;
    if (!a.index || !a.device || cc_addr(a.addr, &sin) || a.argi != argc ||
        (a.allow && inet_pton(AF_INET, a.allow, &only.sin_addr) != 1)) {
        ir_say("chunkcast: serve: --listen ADDR:PORT, --token-file, --index, --device and --level are required\n");
        return 2;
    }
    sv_read_ms = env_ll("CHUNKCAST_SERVE_READ_MS", SV_READ_MS);
    if (!a.until_s != !a.read_mibs ||
        (a.until_s && (!a.allow || sin.sin_addr.s_addr == htonl(INADDR_ANY)))) {
        /* B29: a server for another cloner has an end, a ceiling, one asker and one card -- all four, or it does not start */
        ir_say("chunkcast: serve: --until-s and --read-mibs go together, and with them --allow and a --listen that is not 0.0.0.0\n");
        return 2;
    }
    if (sv_token_load(a.token_file)) {      /* no token, no server: "who may ask" is never answered with "anyone" */
        ir_say("chunkcast: serve: --token-file is missing, unreadable, or holds no token (16 printable characters at least)\n");
        return 2;
    }
    if (cc_index_load(a.index, a.index_xxh) || cs_open(&a)) return 2;
    signal(SIGTERM, cc_on_signal);
    signal(SIGINT, cc_on_signal);
    if (a.until_s) {
        /* the agent's shell died: nobody else would stop it. Asked before the socket listens, and then
         * checked: a shell that died before the ask leaves us to init, and no signal will ever come */
        prctl(PR_SET_PDEATHSIG, SIGTERM);
        if (getppid() == 1) {
            ir_say("chunkcast: serve: the shell that started it is gone -- it does not start\n");
            return 2;
        }
        sv_until_ms = now_ms() + a.until_s * 1000;
        sv_read_mibs = a.read_mibs;
    }
    ls = socket(AF_INET, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (ls < 0 || setsockopt(ls, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one) ||
        bind(ls, (struct sockaddr *)&sin, sizeof sin) || listen(ls, 8)) {
        ir_say("chunkcast: serve: cannot listen on %s: %s\n", a.addr, strerror(errno));
        return 2;
    }
    /* after the early returns above, so none of them leaves it behind (cppcheck memleak, #3891) */
    if (!(z = malloc(CC_ZMAX))) return 2;
    printf("serving\n");
    fflush(stdout);
    while (!cc_stop) {
        struct pollfd pf = { .fd = ls, .events = POLLIN };
        socklen_t len = sizeof peer;
        if (sv_until_ms && now_ms() >= sv_until_ms) {
            ir_say("chunkcast: serve: --until-s reached -- the server ends\n");
            break;
        }
        long long t0;
        int c;
        if (poll(&pf, 1, 500) <= 0) continue;
        c = accept4(ls, (struct sockaddr *)&peer, &len, SOCK_CLOEXEC);
        if (c < 0) continue;
        t0 = now_ms();
        if (a.allow && peer.sin_addr.s_addr != only.sin_addr.s_addr) {      /* not the one that may ask */
            close(c);
            continue;
        }
        setsockopt(c, SOL_SOCKET, SO_RCVTIMEO, &patience, sizeof patience);
        setsockopt(c, SOL_SOCKET, SO_SNDTIMEO, &patience, sizeof patience);
        sv_one(c, z, t0);
        close(c);
    }
    return 0;
}

/* #3332: `b3list` (whole = 0) and `b3sum` (whole = 1), stdin to stdout. 0, or 1 when a read or a write failed. */
static int cc_b3_stream(int whole)
{
    static unsigned char buf[1 << 20];
    unsigned char sum[32];
    uint64_t in_piece = 0;
    blake3_hasher h;
    blake3_hasher_init(&h);
    for (;;) {
        ssize_t r = read(STDIN_FILENO, buf, sizeof buf);
        if (r < 0 && errno == EINTR) continue;
        if (r < 0) { ir_say("chunkcast: b3: stdin: %s\n", strerror(errno)); return 1; }
        if (r == 0) break;
        for (size_t at = 0; at < (size_t)r;) {
            size_t take = (size_t)r - at;
            if (!whole && take > CC_CHUNK - in_piece) take = (size_t)(CC_CHUNK - in_piece);
            blake3_hasher_update(&h, buf + at, take);
            at += take;
            in_piece += take;
            if (!whole && in_piece == CC_CHUNK) {
                blake3_hasher_finalize(&h, sum, 32);
                if (fwrite(sum, 1, 32, stdout) != 32) return 1;
                blake3_hasher_init(&h);
                in_piece = 0;
            }
        }
    }
    blake3_hasher_finalize(&h, sum, 32);
    if (whole) {
        for (int i = 0; i < 32; i++) printf("%02x", sum[i]);
        printf("\n");
    } else if (in_piece && fwrite(sum, 1, 32, stdout) != 32) {
        return 1;
    }
    return fflush(stdout) || ferror(stdout) ? 1 : 0;
}

int main(int argc, char **argv)
{
    signal(SIGPIPE, SIG_IGN);
    if (argc == 2 && !strcmp(argv[1], "b3list")) return cc_b3_stream(0);
    if (argc == 2 && !strcmp(argv[1], "b3sum")) return cc_b3_stream(1);
    if (argc == 2 && !strcmp(argv[1], "--version")) {       /* "it is installed and it runs", for the server */
        printf("chunkcast %d\n", CC_VERSION);
        return fflush(stdout) ? 2 : 0;
    }
    if (argc >= 2 && !strcmp(argv[1], "send")) return cc_send(argc, argv);
    if (argc >= 2 && !strcmp(argv[1], "recv")) return cc_recv(argc, argv);
    if (argc >= 2 && !strcmp(argv[1], "apply")) return cc_apply(argc, argv);
    if (argc >= 2 && !strcmp(argv[1], "serve")) return cc_serve(argc, argv);
    ir_say("usage: chunkcast send|recv|apply|serve|b3list|b3sum ... (see the head of agent/chunkcast.c)\n");
    return 2;
}
