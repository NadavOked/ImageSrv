/* imagesrv-netwatch -- what the distribution port hears, for the brain (#3064: N4, N6, N8).
 * Usage: imagesrv-netwatch <iface> <seconds> [--promisc]
 *        imagesrv-netwatch --from-file <path>   (tests, no socket: [u32 BE length][frame] records)
 * One JSON line on stdout. rc 0 = listened (even if it heard nothing); 3 = could not listen.
 *
 * Two AF_PACKET sockets, both with a kernel BPF filter, because a flood must not hide a query:
 *   A: IGMP only. Queries come every 60-125 s; a counter that missed one would say "no querier".
 *   B: everything that is not to or from this machine (so our own traffic is never counted), read only up
 *      to MAX_SAMPLE frames; the kernel's own counter (PACKET_STATISTICS) says how many passed the filter.
 * --promisc adds PACKET_MR_PROMISC to B: only then does B see multicast the NIC's hardware filter would
 * drop, i.e. a flood to a port that never joined the group. It is removed when the socket closes.
 * "heard nothing" is only meaningful next to window_s and passed -- the caller decides, not this tool.
 * libc only. Assumes a little-endian host for /proc/net/igmp (x86; the initramfs).
 */
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/filter.h>
#include <linux/if_ether.h>
#include <linux/if_packet.h>
#include <net/if.h>
#include <poll.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

#define SNAP 256
#define MAX_SAMPLE 4000
#define MAX_GROUPS 16
#define MAX_MACS 16
#define MAX_SRCS 4
#define MAX_JOINED 16
#define TOP_GROUPS 8
#define TOP_MACS 4

struct counts {
    unsigned long arp, dhcp, wol, igmp, lldp, l2ctl, bcast_ip, mcast_ip, ipv6, unicast_other, other;
};
static struct counts c;
static unsigned long sampled, groups_more;
static struct { uint32_t g; unsigned long n; } groups[MAX_GROUPS];
static int ngroups;
static struct { uint8_t m[6]; unsigned long n; } macs[MAX_MACS];
static int nmacs;
static unsigned long macs_more;

static struct {
    unsigned long queries, general, group, reports, leaves;
    int v1, v2, v3;
    uint32_t src[MAX_SRCS];
    int nsrc;
    double last_query;
} ig;

static double now_s(void)
{
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (double)t.tv_sec + (double)t.tv_nsec / 1e9;
}

static void note_group(uint32_t g)
{
    int i;
    for (i = 0; i < ngroups; i++)
        if (groups[i].g == g) { groups[i].n++; return; }
    if (ngroups < MAX_GROUPS) { groups[ngroups].g = g; groups[ngroups++].n = 1; }
    else groups_more++;
}

static void note_mac(const uint8_t *m)
{
    int i;
    for (i = 0; i < nmacs; i++)
        if (memcmp(macs[i].m, m, 6) == 0) { macs[i].n++; return; }
    if (nmacs < MAX_MACS) { memcpy(macs[nmacs].m, m, 6); macs[nmacs++].n = 1; }
    else macs_more++;
}

/* Ethernet frame -> payload pointer, ethertype (skips one 802.1Q tag). 0 = too short. */
static int l3(const uint8_t *f, size_t n, const uint8_t **p, size_t *m, unsigned *et)
{
    if (n < 14) return 0;
    *et = (unsigned)(f[12] << 8 | f[13]);
    *p = f + 14;
    *m = n - 14;
    if (*et == 0x8100 && n >= 18) {
        *et = (unsigned)(f[16] << 8 | f[17]);
        *p = f + 18;
        *m = n - 18;
    }
    return 1;
}

static void igmp_note(const uint8_t *f, size_t n, double at)
{
    const uint8_t *p;
    size_t m, ihl, len;
    unsigned et, type;
    int i, seen = 0;
    uint32_t src;

    if (!l3(f, n, &p, &m, &et) || et != 0x0800 || m < 20 || (p[0] >> 4) != 4 || p[9] != 2) return;
    ihl = (size_t)(p[0] & 15) * 4;
    len = (size_t)(p[2] << 8 | p[3]);
    if (ihl < 20 || m < ihl + 8 || len < ihl + 8) return;
    type = p[ihl];
    if (type == 0x11) {
        ig.queries++;
        if (memcmp(p + ihl + 4, "\0\0\0\0", 4) == 0) ig.general++;
        else ig.group++;
        if (len - ihl >= 12) ig.v3 = 1;
        else if (p[ihl + 1] != 0) ig.v2 = 1;
        else ig.v1 = 1;
        memcpy(&src, p + 12, 4);
        for (i = 0; i < ig.nsrc; i++) if (ig.src[i] == src) seen = 1;
        if (!seen && ig.nsrc < MAX_SRCS) ig.src[ig.nsrc++] = src;
        ig.last_query = at;
    } else if (type == 0x12 || type == 0x16 || type == 0x22) ig.reports++;
    else if (type == 0x17) ig.leaves++;
}

static void classify(const uint8_t *f, size_t n)
{
    static const uint8_t bcast[6] = {255, 255, 255, 255, 255, 255};
    const uint8_t *p;
    size_t m, ihl;
    unsigned et, sport, dport;
    int l2bcast;

    sampled++;
    if (n >= 12) note_mac(f + 6);
    if (!l3(f, n, &p, &m, &et)) { c.other++; return; }
    l2bcast = memcmp(f, bcast, 6) == 0;
    if (et < 0x600 || et == 0x0842 || et == 0x8808) { c.l2ctl++; return; }   /* STP/CDP (802.3 LLC), WoL, pause */
    if (et == 0x0806) { c.arp++; return; }
    if (et == 0x88cc) { c.lldp++; return; }
    if (et == 0x86dd) { c.ipv6++; return; }
    if (et != 0x0800 || m < 20 || (p[0] >> 4) != 4) { c.other++; return; }
    ihl = (size_t)(p[0] & 15) * 4;
    if (ihl < 20) { c.other++; return; }
    if (p[9] == 2) { c.igmp++; return; }
    sport = dport = 0;
    if (p[9] == 17 && m >= ihl + 4) {
        sport = (unsigned)(p[ihl] << 8 | p[ihl + 1]);
        dport = (unsigned)(p[ihl + 2] << 8 | p[ihl + 3]);
    }
    if (p[9] == 17 && (sport == 67 || sport == 68) && (dport == 67 || dport == 68)) { c.dhcp++; return; }
    if (p[16] >= 224 && p[16] <= 239) {
        uint32_t g;
        memcpy(&g, p + 16, 4);
        c.mcast_ip++;
        note_group(g);
        return;
    }
    if (p[9] == 17 && dport == 9 && l2bcast) { c.wol++; return; }       /* the server's wake-on-LAN */
    if (l2bcast) { c.bcast_ip++; return; }
    if (!(f[0] & 1)) { c.unicast_other++; return; }
    c.other++;
}

static void fprint_ip(uint32_t a)
{
    const uint8_t *b = (const uint8_t *)&a;
    printf("\"%u.%u.%u.%u\"", b[0], b[1], b[2], b[3]);
}

static void print_json(const char *iface, int window, int promisc, long passed, long drops, double end)
{
    int i, k, shown;
    char used[MAX_GROUPS] = {0};

    printf("{\"v\":1,\"iface\":\"%s\",\"window_s\":%d,\"promisc\":%s,", iface, window, promisc ? "true" : "false");
    printf("\"igmp\":{\"queries\":%lu,\"general\":%lu,\"group\":%lu,\"reports\":%lu,\"leaves\":%lu,\"versions\":[",
           ig.queries, ig.general, ig.group, ig.reports, ig.leaves);
    printf("%s%s%s", ig.v1 ? "1" : "", ig.v2 ? (ig.v1 ? ",2" : "2") : "",
           ig.v3 ? (ig.v1 || ig.v2 ? ",3" : "3") : "");
    fputs("],\"sources\":[", stdout);
    for (i = 0; i < ig.nsrc; i++) { if (i) putchar(','); fprint_ip(ig.src[i]); }
    if (ig.queries) printf("],\"last_query_age_s\":%d},", (int)(end - ig.last_query));
    else fputs("],\"last_query_age_s\":null},", stdout);
    if (passed < 0) fputs("\"passed\":null,\"kernel_drops\":null,", stdout);
    else printf("\"passed\":%ld,\"kernel_drops\":%ld,", passed, drops);
    printf("\"sampled\":%lu,\"classes\":{\"arp\":%lu,\"dhcp\":%lu,\"wol\":%lu,\"igmp\":%lu,\"lldp\":%lu,"
           "\"l2ctl\":%lu,\"bcast_ip\":%lu,\"mcast_ip\":%lu,\"ipv6\":%lu,\"unicast_other\":%lu,\"other\":%lu},",
           sampled, c.arp, c.dhcp, c.wol, c.igmp, c.lldp, c.l2ctl, c.bcast_ip, c.mcast_ip, c.ipv6,
           c.unicast_other, c.other);
    fputs("\"groups\":[", stdout);
    for (shown = 0; shown < TOP_GROUPS && shown < ngroups; shown++) {
        int best = -1;
        for (k = 0; k < ngroups; k++)
            if (!used[k] && (best < 0 || groups[k].n > groups[best].n)) best = k;
        if (best < 0) break;
        used[best] = 1;
        if (shown) putchar(',');
        fputs("{\"group\":", stdout);
        fprint_ip(groups[best].g);
        printf(",\"n\":%lu}", groups[best].n);
    }
    printf("],\"groups_more\":%lu,\"macs\":[", groups_more + (unsigned long)(ngroups - shown));
    {
        char mu[MAX_MACS] = {0};
        for (shown = 0; shown < TOP_MACS && shown < nmacs; shown++) {
            int best = -1;
            for (k = 0; k < nmacs; k++)
                if (!mu[k] && (best < 0 || macs[k].n > macs[best].n)) best = k;
            if (best < 0) break;
            mu[best] = 1;
            if (shown) putchar(',');
            printf("{\"mac\":\"%02x:%02x:%02x:%02x:%02x:%02x\",\"n\":%lu}", macs[best].m[0], macs[best].m[1],
                   macs[best].m[2], macs[best].m[3], macs[best].m[4], macs[best].m[5], macs[best].n);
        }
    }
    printf("],\"macs_more\":%lu,", macs_more + (unsigned long)(nmacs - shown));
}

/* Groups this machine itself joined on <iface>: /proc/net/igmp. null = could not be read. */
static void print_joined(const char *iface)
{
    char line[256], dev[64];
    FILE *fp = fopen("/proc/net/igmp", "r");
    int mine = 0, n = 0;
    unsigned v;

    fputs("\"joined\":", stdout);
    if (fp == NULL) { fputs("null}\n", stdout); return; }
    putchar('[');
    while (fgets(line, sizeof line, fp) != NULL && n < MAX_JOINED) {
        if (line[0] >= '0' && line[0] <= '9') {
            mine = sscanf(line, "%*u %63s", dev) == 1 && strcmp(dev, iface) == 0;
        } else if (mine && sscanf(line, " %8x", &v) == 1) {
            if (n++) putchar(',');
            printf("\"%u.%u.%u.%u\"", v & 0xff, (v >> 8) & 0xff, (v >> 16) & 0xff, v >> 24);
        }
    }
    fclose(fp);
    fputs("]}\n", stdout);
}

static int fail_listen(const char *why)
{
    const char *s;
    fputs("{\"error\":\"", stdout);
    for (s = why; *s; s++) putchar((*s == '"' || *s == '\\' || (unsigned char)*s < 0x20 || (unsigned char)*s >= 0x80) ? '?' : *s);
    fputs("\"}\n", stdout);
    return 3;
}

static int from_file(const char *path)
{
    FILE *fp = fopen(path, "rb");
    uint8_t len[4], buf[2048];
    uint32_t n;

    if (fp == NULL) return fail_listen(strerror(errno));
    while (fread(len, 1, 4, fp) == 4) {
        n = (uint32_t)len[0] << 24 | (uint32_t)len[1] << 16 | (uint32_t)len[2] << 8 | len[3];
        if (n > sizeof buf || fread(buf, 1, n, fp) != n) break;
        igmp_note(buf, n, 0);
        classify(buf, n);
    }
    fclose(fp);
    print_json("file", 0, 0, -1, -1, 0);
    print_joined("-");
    return 0;
}

static int open_sock(int ifindex, const struct sock_filter *prog, unsigned short len, int rcvbuf, int promisc)
{
    struct sock_fprog fp = {len, (struct sock_filter *)prog};
    struct sockaddr_ll sll;
    struct packet_mreq mr;
    int fd = socket(AF_PACKET, SOCK_RAW, 0);        /* protocol 0: nothing arrives until the filter is attached */

    if (fd < 0) return -1;
    memset(&sll, 0, sizeof sll);
    sll.sll_family = AF_PACKET;
    sll.sll_protocol = htons(ETH_P_ALL);
    sll.sll_ifindex = ifindex;
    if (setsockopt(fd, SOL_SOCKET, SO_ATTACH_FILTER, &fp, sizeof fp) < 0
        || setsockopt(fd, SOL_SOCKET, SO_RCVBUF, &rcvbuf, sizeof rcvbuf) < 0
        || bind(fd, (struct sockaddr *)&sll, sizeof sll) < 0) { close(fd); return -1; }
    if (promisc) {
        memset(&mr, 0, sizeof mr);
        mr.mr_ifindex = ifindex;
        mr.mr_type = PACKET_MR_PROMISC;
        if (setsockopt(fd, SOL_PACKET, PACKET_ADD_MEMBERSHIP, &mr, sizeof mr) < 0) { close(fd); return -1; }
    }
    return fd;
}

static int read_mac(const char *iface, uint8_t *mac)
{
    char path[128], text[64];
    unsigned v[6];
    int i, got;
    FILE *fp;

    snprintf(path, sizeof path, "/sys/class/net/%s/address", iface);
    fp = fopen(path, "r");
    if (fp == NULL) return -1;
    got = fgets(text, sizeof text, fp) != NULL && sscanf(text, "%x:%x:%x:%x:%x:%x", &v[0], &v[1], &v[2], &v[3], &v[4], &v[5]) == 6;
    fclose(fp);
    if (!got) return -1;
    for (i = 0; i < 6; i++) mac[i] = (uint8_t)v[i];
    return 0;
}

/* B = not to and not from this machine (see the header). Instruction indexes in the comments. */
static void prog_b(const uint8_t *mac, struct sock_filter *p)
{
    uint32_t d03 = (uint32_t)mac[0] << 24 | (uint32_t)mac[1] << 16 | (uint32_t)mac[2] << 8 | mac[3];
    uint32_t d45 = (uint32_t)mac[4] << 8 | mac[5];
    struct sock_filter t[] = {
        BPF_STMT(BPF_LD | BPF_W | BPF_ABS, 0),                      /* 0 dst[0..3] */
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, d03, 0, 3),             /* 1 -> 2 | 5 */
        BPF_STMT(BPF_LD | BPF_H | BPF_ABS, 4),                      /* 2 dst[4..5] */
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, d45, 5, 0),             /* 3 -> 9 (drop) | 4 */
        BPF_STMT(BPF_JMP | BPF_JA, 0),                              /* 4 -> 5 */
        BPF_STMT(BPF_LD | BPF_W | BPF_ABS, 6),                      /* 5 src[0..3] */
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, d03, 0, 3),             /* 6 -> 7 | 10 */
        BPF_STMT(BPF_LD | BPF_H | BPF_ABS, 10),                     /* 7 src[4..5] */
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, d45, 0, 1),             /* 8 -> 9 (drop) | 10 */
        BPF_STMT(BPF_RET | BPF_K, 0),                               /* 9 */
        BPF_STMT(BPF_RET | BPF_K, SNAP),                            /* 10 */
    };
    memcpy(p, t, sizeof t);
}

static int sniff(const char *iface, int window, int promisc)
{
    static const struct sock_filter prog_a[] = {
        BPF_STMT(BPF_LD | BPF_H | BPF_ABS, 12),                     /* 0 ethertype */
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, 0x0800, 0, 3),          /* 1 -> 2 | 5 */
        BPF_STMT(BPF_LD | BPF_B | BPF_ABS, 23),                     /* 2 ip protocol */
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, 2, 0, 1),               /* 3 -> 4 | 5 */
        BPF_STMT(BPF_RET | BPF_K, SNAP),                            /* 4 */
        BPF_STMT(BPF_RET | BPF_K, 0),                               /* 5 */
    };
    struct sock_filter prog[11];
    struct pollfd pfd[2];
    struct sockaddr_ll from;
    struct tpacket_stats st;
    socklen_t fl, sl = sizeof st;
    uint8_t mac[6], buf[2048];
    double start, end, t;
    int idx = (int)if_nametoindex(iface), fa, fb, i, active_b = 1;
    ssize_t n;
    long passed = -1, drops = -1;

    for (i = 0; iface[i]; i++)
        if (!((iface[i] >= 'a' && iface[i] <= 'z') || (iface[i] >= 'A' && iface[i] <= 'Z') || (iface[i] >= '0' && iface[i] <= '9')
              || iface[i] == '.' || iface[i] == '_' || iface[i] == '-')) return fail_listen("bad interface name");
    if (idx == 0) return fail_listen("no such interface");
    if (read_mac(iface, mac) < 0) return fail_listen("the interface address could not be read");
    prog_b(mac, prog);
    fa = open_sock(idx, prog_a, 6, 65536, 0);
    if (fa < 0) return fail_listen(strerror(errno));
    fb = open_sock(idx, prog, 11, 131072, promisc);
    if (fb < 0) { i = errno; close(fa); return fail_listen(strerror(i)); }
    start = now_s();
    end = start + window;
    pfd[0].fd = fa; pfd[0].events = POLLIN;
    pfd[1].fd = fb; pfd[1].events = POLLIN;
    while ((t = now_s()) < end) {
        pfd[0].revents = pfd[1].revents = 0;
        pfd[1].fd = active_b ? fb : -1;
        if (poll(pfd, 2, (int)((end - t) * 1000) + 1) < 0 && errno != EINTR) break;
        if (pfd[0].revents & POLLIN) {
            fl = sizeof from;
            n = recvfrom(fa, buf, sizeof buf, MSG_DONTWAIT, (struct sockaddr *)&from, &fl);
            if (n > 0 && from.sll_pkttype != PACKET_OUTGOING) igmp_note(buf, (size_t)n, now_s());
        }
        if (active_b && (pfd[1].revents & POLLIN)) {
            n = recv(fb, buf, sizeof buf, MSG_DONTWAIT);
            if (n > 0) classify(buf, (size_t)n);
            if (sampled >= MAX_SAMPLE) active_b = 0;       /* the rest is counted by the kernel, not read */
        }
    }
    if (getsockopt(fb, SOL_PACKET, PACKET_STATISTICS, &st, &sl) == 0) { passed = (long)st.tp_packets; drops = (long)st.tp_drops; }
    close(fa);
    close(fb);
    print_json(iface, window, promisc, passed, drops, now_s());
    print_joined(iface);
    return 0;
}

int main(int argc, char **argv)
{
    int window;

    if (argc == 3 && strcmp(argv[1], "--from-file") == 0) return from_file(argv[2]);
    if (argc < 3 || argc > 4 || (argc == 4 && strcmp(argv[3], "--promisc") != 0)) {
        fputs("usage: imagesrv-netwatch <iface> <seconds> [--promisc] | --from-file <path>\n", stderr);
        return 2;
    }
    window = atoi(argv[2]);
    if (window < 1 || window > 600) return fail_listen("seconds must be 1..600");
    return sniff(argv[1], window, argc == 4);
}
