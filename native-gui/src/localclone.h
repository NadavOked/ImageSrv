/* #1322: the cloning machine's boot choice and local clone -- what the state
 * file says (LcState, lcgui.sh writes it) and what the person did on the
 * screen (LcUi). The screens decide nothing: every choice goes back to the
 * agent as a record (lc_state.c), and the agent checks it again before a
 * byte is written. Approved mockup: localclone.html (Nadav 13/10). */
#ifndef IMAGECTL_LOCALCLONE_H
#define IMAGECTL_LOCALCLONE_H

#define LC_MAX_DISKS 8
#define LC_MAX_PARTS 16
/* #1437: the speed and the time left are measured over the last LC_RATE_WIN s
 * of the copy, not averaged since the start (which counts the preparation) */
#define LC_RATE_WIN 60
#define LC_RATE_MIN 5                            /* the speed needs this many s of bytes */
#define LC_RATE_MAX 128

/* 16/10: LC_SCREEN_PULL -- "שחזור אימג' מהשרת", the build machine's restore
 * screen with this machine's drawers as the targets (lcpull.sh) */
enum { LC_SCREEN_NONE, LC_SCREEN_BOOT, LC_SCREEN_LOCAL, LC_SCREEN_PULL,
       LC_SCREEN_USBSET };   /* #1914: the USB stick's settings -- drawn by the DCUI (screens_dcui.c) */
/* the verdict of one target against the chosen source (lcinv.sh lc_verdict) */
enum { LCV_NONE, LCV_FITS, LCV_SHRINK, LCV_SHORT, LCV_MEASURING, LCV_UNCHECKED };
/* what the screen shows of the local flow */
enum { LCVIEW_SOURCE, LCVIEW_PROGRESS, LCVIEW_DONE };
/* the person's role for a drawer */
enum { LCR_SKIP, LCR_SOURCE, LCR_TARGET };

/* lc_disk=port|dev|model|serial|size|smart|smart_reason|os|nparts|used|need|usable|why|drawer|conn|mark|hw|name */
typedef struct {
    int port; char dev[16], model[64], serial[64];
    unsigned long long size; char smart[16], smart_reason[48], os[64];
    int nparts; long long used, need; int usable; char why[200];
    int drawer; char conn[32];
    char mark[12];   /* #1798: incomplete|unchecked; "" = clean or not a GPT */
    char hw[240];    /* #1921: the drawer's hardware note (hw-issues.tsv); "" = none -- never the disk's colour */
    char name[104];  /* #2001: the name of its connection (the USB stick's mapping window); "" = "דיסק N" */
} LcDisk;
/* lc_fit=dev|verdict|need|reason */
typedef struct { char dev[16]; int verdict; long long need; char reason[240]; } LcFit;
/* lc_part=idx|label|fs|bytes|size|state|in -- bytes -1 = used not measured (#1440) */
typedef struct { int idx; char label[24], fs[16], state[8]; long long bytes, size, in; } LcPart;
/* lc_tgt=dev|state|bytes|part|ok|grown_idx,grown_bytes,src_bytes,free|error|total|ntfs_boot|ntfs_note|raw,rescued|pulled|grow_deferred */
typedef struct {
    char dev[16], state[16]; long long bytes, total; int part, ok, has_table;
    int ntfs_boot;                               /* #1487: NTFS backup boot sectors written and read back */
    int raw_parts; long long rescued;            /* #2017: partitions copied raw; sectors not read (-1 = none rescued) */
    int grown_idx; long long grown_bytes, src_bytes, free_bytes; char error[400];
    char note[400];                              /* #2009: a "done" that is orange, and why (Fast Startup / dirty); "" = green */
    int pulled;                                  /* #2045: the disk left the system mid-copy -- not a broken disk */
    int grow_deferred;                           /* #2196: the table grew, the filesystem did not -- no "הוגדלה" over it */
} LcTgt;

typedef struct {
    int screen;                                  /* LC_SCREEN_* */
    int round_open;                              /* #1367: lc_round=open -- a round waits on the server */
    long long boot_deadline, boot_now; int boot_paused;
    int local_ok;                                /* 16/10: lc_local=1 -- two disks or more, the local card is offered */
    int usb;                                     /* #1914: lc_usb=1 -- booted from the clone stick, no server */
    char diag[64];                               /* #1914: lc_diag= the diagnostic report kept on the stick */
    int ide;                                     /* #1914: lc_ide=ide -- the drawers' controller is in IDE mode */
    int survey;                                  /* #1914/#1921: lc_survey=1 -- the survey stick keeps each disk's SMART */
    int inv_done; char phase[16];                /* idle|shrinking|copying|verifying|restoring|done|stopped */
    LcDisk disks[LC_MAX_DISKS]; int ndisks;
    char src[16]; char src_measure[12];          /* the source the verdicts are about */
    long long src_copy; char src_copy_why[200];  /* #1443: the bytes the copy moves; -1 = not measured (why), -2 = not yet */
    char unread_dev[16]; long long unread;       /* Nadav 22/10: lc_unread=dev|n -- SMART 197/198 of the source; 0 = say nothing */
    LcFit fits[LC_MAX_DISKS]; int nfits;
    int has_run; char run_src[16]; long long total, copied; int elapsed, shrunk, cur_part;
    char report[12], src_restore[400];
    char shrink_path[12];                        /* #1477: snapshot|refused|"" -- from the agent, never guessed */
    LcPart parts[LC_MAX_PARTS]; int nparts;
    LcTgt tgts[LC_MAX_DISKS]; int ntgts;
} LcState;

typedef struct {
    int boot_focus;                              /* 0 "מהשרת", 1 "שיכפול מקומי", 2 "שחזור אימג' מהשרת" (16/10) */
    int pause_sent; int move_init; double move_x, move_y;
    double frozen;                               /* the bar's share when the count stopped; -1 = not yet */
    char role_dev[LC_MAX_DISKS][16]; int role[LC_MAX_DISKS]; int nroles;
    char measured[16];                           /* the source the last lc-measure asked about */
    char src_ack[16];                            /* 16/10: the warned source the SMART window let through */
    int bay;                                     /* index into st.lc.disks with the keyboard focus */
    int focus;                                   /* LCF_* below */
    int confirm, stop;                           /* the two dialogs */
    int touched;                                 /* #2008: a key or a click on the choice since the roles were set */
    double start_sent;                           /* monotonic s of lc-start; the progress view until the agent answers */
    char last_phase[16];                         /* a run -> idle: the roles start over (#1722) */
    /* #1722: the person's own last lc-start, exactly (dev + serial) -- the only
     * choice drawn again after a stop. ran = a run was seen: no default source
     * after it; keep = it went back to idle without "done" (stopped). */
    char last_src[16], last_src_serial[64];
    char last_tgt[LC_MAX_DISKS][16], last_tgt_serial[LC_MAX_DISKS][64]; int nlast;
    int ran, keep;
    /* #1437: (elapsed, copied) as the agent wrote them, one per reload, from the first byte */
    int rate_e[LC_RATE_MAX]; long long rate_c[LC_RATE_MAX]; int rate_n;
} LcUi;

enum { LCF_BAY, LCF_BACK, LCF_NEXT, LCF_GO, LCF_CANCEL, LCF_DROP, LCF_STOP, LCF_STOP_GO, LCF_STOP_NO, LCF_OFF, LCF_AGAIN, LCF_REBOOT };

#endif
