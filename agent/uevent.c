/* uevent.c -- notify ImageSrv about whole-disk kernel uevents (#1971).
 *
 * The kernel's own uevents (NETLINK_KOBJECT_UEVENT, group 1 -- there is no udev
 * in the initramfs). A record is NUL-separated KEY=VALUE strings; a whole-disk
 * add/remove/change touches FILE, which wakes the cloner's state loop
 * (clonergui.sh) at once instead of at its next poll. The buffer always ends
 * in a NUL of ours, so a record cut short is never read past its end. Events
 * dropped by a full socket buffer (ENOBUFS) may have been disks: FILE is
 * touched for those too, and the listener goes on. */
#include <errno.h>
#include <linux/netlink.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

static int disk_event(const char *buf, size_t n) {
    int block = 0, disk = 0, action = 0;
    for (size_t p = 0; p < n;) {
        const char *s = buf + p; size_t z = strnlen(s, n - p);
        if (!strcmp(s, "SUBSYSTEM=block")) block = 1;
        if (!strcmp(s, "DEVTYPE=disk")) disk = 1;
        if (!strcmp(s, "ACTION=add") || !strcmp(s, "ACTION=remove") || !strcmp(s, "ACTION=change")) action = 1;
        p += z + 1;
    }
    return block && disk && action;
}

static int notify(const char *path) {
    FILE *f = fopen(path, "w");
    if (!f) return -1;
    int ok = fprintf(f, "1\n") > 0 && fclose(f) == 0;
    return ok ? 0 : -1;
}

int main(int argc, char **argv) {
    if (argc == 3 && !strcmp(argv[1], "--parse")) {
        char b[4096]; size_t n = fread(b, 1, sizeof b - 1, stdin); b[n] = 0;
        return disk_event(b, n) ? notify(argv[2]) != 0 : 2;
    }
    if (argc != 2) { fprintf(stderr, "usage: imagesrv-uevent FILE\n"); return 2; }
    int fd = socket(AF_NETLINK, SOCK_DGRAM | SOCK_CLOEXEC, NETLINK_KOBJECT_UEVENT);
    if (fd < 0) { perror("uevent socket"); return 1; }
    struct sockaddr_nl a = { .nl_family = AF_NETLINK, .nl_pid = (unsigned)getpid(), .nl_groups = 1 };
    if (bind(fd, (struct sockaddr *)&a, sizeof a) < 0) { perror("uevent bind"); close(fd); return 1; }
    for (;;) {
        char b[8192]; ssize_t n = recv(fd, b, sizeof b - 1, 0);
        if (n < 0 && errno == EINTR) continue;
        if (n < 0 && errno == ENOBUFS) { fprintf(stderr, "uevent: events were dropped -- refreshing\n"); if (notify(argv[1]) != 0) perror("uevent notify"); continue; }
        if (n < 0) { perror("uevent recv"); break; }
        b[n] = 0;
        if (disk_event(b, (size_t)n) && notify(argv[1]) != 0) perror("uevent notify");
    }
    close(fd); return 1;
}
