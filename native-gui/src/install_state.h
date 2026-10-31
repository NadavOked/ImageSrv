#ifndef IMAGESRV_INSTALL_STATE_H
#define IMAGESRV_INSTALL_STATE_H

#define INSTALL_MAX_NICS 16
#define INSTALL_MAX_DISKS 16
#define INSTALL_OUTPUT_MAX 100000
/* #1250: the done screen restarts by itself after this many seconds, like
 * the ESXi installer. One name, so changing it is one line. */
#define INSTALL_AUTO_REBOOT_S 90

enum {
    INSTALL_DISK = 1, INSTALL_ROLE, INSTALL_NETWORK, INSTALL_HOSTNAME, INSTALL_ADMIN,
    INSTALL_SUMMARY, INSTALL_DONE, INSTALL_PROGRESS, INSTALL_FAILED
};

typedef struct {
    char name[32], model[80], mac[32], link[32], current[48], source[96];
    char address[48], netmask[48], gateway[48], dns[96];
} InstallNic;

typedef struct {
    char path[48], model[96], bus[24], has[160];
    unsigned long long size_bytes;
    int removable, iso;
} InstallDisk;

typedef struct {
    int active, demo, view, rerun, ndisks, disk, disk_ack, existing_disk;
    int nnics, nic, secondary, static_mode, primary_ok, primary_checked;
    int progress_pct, output_scroll, technical_open, show_password, show_confirm;
    int show_root, show_root_confirm;   /* #1632 */
    int virt, raid;   /* #1989: INSTALL_VIRT_*, RAID_* (install_raid.h) */
    char primary_url[192], hostname[64], password[128], confirm[128], current_password[128];
    char admin_user[64];
    char root_password[128], root_confirm[128];   /* #1632: root's, chosen here (Nadav 24/10) */
    char timezone[64];   /* #1447: tzdata zone, default Asia/Jerusalem */
    char address[48], netmask[48], gateway[48], dns[96];
    char primary_name[96], primary_version[32], primary_fingerprint[128], primary_error[256];
    char phase[48], title[160], log[256], job[64], error[256], console_url[192], user[64];
    double reboot_at;   /* #1250: monotonic deadline of the auto restart; 0 = not counting (cancelled or fired) */
    int reboot_left;    /* seconds shown on the done screen */
    char field_error[17][256];   /* 14 = timezone (#1447), 15/16 = root_password/_confirm (#1632) */
    char output[INSTALL_OUTPUT_MAX];
    InstallDisk disks[INSTALL_MAX_DISKS];
    InstallNic nics[INSTALL_MAX_NICS];
} InstallState;

#endif
