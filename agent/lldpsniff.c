/* imagesrv-lldpsniff -- one LLDP frame to JSON for hello (#1048).
 * Usage: imagesrv-lldpsniff <iface> [timeout_s]
 *        imagesrv-lldpsniff --from-file <path>   (tests, no socket)
 * rc 0 = heard; 2 = timeout; 3 = could not listen. libc only.
 */
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/if_packet.h>
#include <net/if.h>
#include <poll.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

#define ETH_P_LLDP 0x88CC
#define STR_MAX 200
#define DEFAULT_WAIT 35

static char chassis[STR_MAX], portid[STR_MAX], portdesc[STR_MAX], sysname[STR_MAX];
static int ttl = -1;

static void json_str(const char *s)
{
    if (s == NULL || s[0] == '\0') { fputs("null", stdout); return; }
    putchar('"');
    for (; *s; s++) {
        unsigned char c = (unsigned char)*s;
        if (c == '"' || c == '\\') { putchar('\\'); putchar((char)c); }
        else if (c == '\n') fputs("\\n", stdout);
        /* non-ASCII too: one odd byte in a switch name must not make the
         * whole hello invalid UTF-8 (the server would reject all of it) */
        else if (c < 0x20 || c >= 0x80) fprintf(stdout, "\\u%04x", c);
        else putchar((char)c);
    }
    putchar('"');
}

static int fail_listen(const char *why)
{
    fputs("{\"error\":", stdout); json_str(why); fputs("}\n", stdout);
    return 3;
}

static int unheard(int waited)
{
    printf("{\"unheard\":true,\"waited_s\":%d}\n", waited);
    return 2;
}

static void copy_str(char *dst, const uint8_t *p, size_t n)
{
    size_t i, o = 0;
    for (i = 0; i < n && o + 1 < STR_MAX; i++) {
        if (p[i] == '\0') break;
        dst[o++] = (char)p[i];
    }
    dst[o] = '\0';
}

static void fmt_mac(char *dst, const uint8_t *m)
{
    snprintf(dst, STR_MAX, "%02x:%02x:%02x:%02x:%02x:%02x",
             m[0], m[1], m[2], m[3], m[4], m[5]);
}

static void parse_tlvs(const uint8_t *p, size_t n)
{
    while (n >= 2) {
        unsigned type = p[0] >> 1;
        unsigned tlen = ((p[0] & 1u) << 8) | p[1];
        if (2u + tlen > n) break;
        if (type == 0) break;
        if (type == 1 && tlen >= 1) {
            if (p[2] == 4 && tlen == 7) fmt_mac(chassis, p + 3);
            else copy_str(chassis, p + 3, tlen - 1);
        } else if (type == 2 && tlen >= 1) {
            if (p[2] == 3 && tlen == 7) fmt_mac(portid, p + 3);
            else copy_str(portid, p + 3, tlen - 1);
        } else if (type == 3 && tlen >= 2) {
            ttl = (p[2] << 8) | p[3];
        } else if (type == 4 && tlen >= 1) {
            copy_str(portdesc, p + 2, tlen);
        } else if (type == 5 && tlen >= 1) {
            copy_str(sysname, p + 2, tlen);
        }
        p += 2 + tlen;
        n -= 2 + tlen;
    }
}

static void skip_eth(const uint8_t **p, size_t *n)
{
    if (*n >= 14) {
        unsigned et = ((*p)[12] << 8) | (*p)[13];
        if (et == 0x8100 && *n >= 18) { *p += 18; *n -= 18; }
        else if (et == ETH_P_LLDP) { *p += 14; *n -= 14; }
    }
}

static int emit_heard(void)
{
    const char *sw = sysname[0] ? sysname : (chassis[0] ? chassis : NULL);
    fputs("{\"switch\":", stdout); json_str(sw);
    fputs(",\"chassis\":", stdout); json_str(chassis[0] ? chassis : NULL);
    fputs(",\"port\":", stdout); json_str(portid[0] ? portid : NULL);
    fputs(",\"port_desc\":", stdout); json_str(portdesc[0] ? portdesc : NULL);
    if (ttl < 0) fputs(",\"ttl\":null}\n", stdout);
    else printf(",\"ttl\":%d}\n", ttl);
    return 0;
}

static int from_file(const char *path)
{
    uint8_t buf[2048];
    const uint8_t *p;
    ssize_t n;
    size_t got;
    int fd = open(path, O_RDONLY);
    if (fd < 0) return fail_listen(strerror(errno));
    n = read(fd, buf, sizeof buf);
    close(fd);
    if (n < 0) return fail_listen(strerror(errno));
    if (n == 0) return unheard(0);
    p = buf;
    got = (size_t)n;
    skip_eth(&p, &got);
    parse_tlvs(p, got);
    return emit_heard();
}

static int sniff(const char *iface, int wait_s)
{
    struct sockaddr_ll addr;
    struct pollfd pfd;
    uint8_t buf[2048];
    const uint8_t *p;
    ssize_t n;
    int fd, idx, pr;
    size_t got;

    idx = (int)if_nametoindex(iface);
    if (idx == 0) return fail_listen("no such interface");
    fd = socket(AF_PACKET, SOCK_RAW, htons(ETH_P_LLDP));
    if (fd < 0) return fail_listen(strerror(errno));
    memset(&addr, 0, sizeof addr);
    addr.sll_family = AF_PACKET;
    addr.sll_protocol = htons(ETH_P_LLDP);
    addr.sll_ifindex = idx;
    if (bind(fd, (struct sockaddr *)&addr, sizeof addr) < 0) {
        int e = errno;
        close(fd);
        return fail_listen(strerror(e));
    }
    pfd.fd = fd;
    pfd.events = POLLIN;
    pr = poll(&pfd, 1, wait_s * 1000);
    if (pr == 0) { close(fd); return unheard(wait_s); }
    if (pr < 0) {
        int e = errno;
        close(fd);
        return fail_listen(strerror(e));
    }
    n = recv(fd, buf, sizeof buf, 0);
    close(fd);
    if (n <= 0) return fail_listen(n < 0 ? strerror(errno) : "empty frame");
    p = buf;
    got = (size_t)n;
    skip_eth(&p, &got);
    parse_tlvs(p, got);
    return emit_heard();
}

int main(int argc, char **argv)
{
    int wait_s = DEFAULT_WAIT;
    if (argc >= 3 && strcmp(argv[1], "--from-file") == 0)
        return from_file(argv[2]);
    if (argc < 2)
        return fail_listen("usage: imagesrv-lldpsniff <iface> [timeout_s]");
    if (argc >= 3) {
        wait_s = atoi(argv[2]);
        if (wait_s <= 0) wait_s = DEFAULT_WAIT;
    }
    return sniff(argv[1], wait_s);
}
