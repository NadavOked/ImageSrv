/* Application state for every card of the former server/static/station/index.html
 * and the hit-test table that drawing fills in. Layout lives in the
 * screens_*.c files and registers where it put each clickable thing, so
 * the event loop never re-derives geometry.
 *
 * Two kinds of state live here: what the operator did (typed, chose,
 * opened) and what the agent said through the --state file (disks,
 * machines, the running task). The second kind is the State struct and is
 * replaced wholesale on every re-read; the first survives it. */
#ifndef IMAGESRV_UI_H
#define IMAGESRV_UI_H

#include <stddef.h>
#include <cairo.h>
#include "draw.h"
#include "install_state.h"
#include "localclone.h"   /* #1322 */
#include "theme.h"

#define MAX_HITS    640     /* #695: 30 machines x 8 disks need room-disk hit targets;
                               #1269: and a drivers tag on each of them in pick mode */
#define MAX_DISKS     8
#define MAX_FOLDERS  32
#define MAX_IMAGES   64
#define MAX_MACHINES 32
#define MAX_CLASSES  16
#define MAX_DRAWERS   8
#define MAX_ROOM_SELECTIONS (MAX_MACHINES * MAX_DRAWERS)
/* #649: the IT toolbox -- rows of $GUI_DIR/tools, and one tool's output. */
#define MAX_TOOLS      64
#define TOOL_OUT_MAX   32768
#define TOOL_OUT_LINES 512
/* #928: the rows of the capture's shrink question (shrinkgui.sh,
 * SHRINK_GUI_MAX_ROWS). A request with more is not drawn at all. */
#define MAX_SHRINK_PARTS 16
/* 11/10 (GUI only): the rows of a question dialog (askgui.sh, ASK_GUI_MAX_ROWS) --
 * the grow-back of a disk left shrunk. A request with more is not drawn. */
#define MAX_ASK_ROWS 16
/* 11/10: the kinds of question the agent asks through the dialog (ask_prompt=) */
enum { ASK_NONE, ASK_GROWBACK, ASK_CAPTURE_END, ASK_SEND_END, ASK_ROUND_END,   /* #1560: send/round */
       ASK_RAID, ASK_SHARE,     /* #1808/#1898: a cloner drawer's question (drawergate.sh) */
       ASK_BADSECT };           /* #2017: a local clone's source with bad sectors (lcraw.sh) */
/* #1560: what ended before the count to power-off (end_poweroff=<epoch>|<kind>) */
enum { END_NONE, END_DRAWERS, END_PARTIAL, END_LOCALCLONE, END_FAILED };
/* #1269: the models the driver library has (GET /api/console/room/driver-models). */
#define MAX_DRIVER_MODELS 32
/* #1569: the failure screen's lists (failscreen.sh writes at most these) */
#define MAX_FAIL_CTX 4
#define MAX_FAIL_ROWS 8
#define MAX_FAIL_STEPS 4
#define MAX_FAIL_CODE 12
#define MAX_FAIL_BTNS 3   /* #2068: the failure screen's buttons */
/* #432/#65: round.precheck (room_warn=) and round.missing (room_missing=)
 * lines on the live room screen; more than this are said, not cut silently. */
#define MAX_ROOM_NOTES 32
/* #704: the operator's name of a slot -- the server allows 24 characters
 * (server/disks/slot_names.py NAME_MAX); 4 UTF-8 bytes each at most, + NUL. */
#define SLOT_NAME_MAX 100

enum {
    HIT_NONE = 0,
    HIT_THEME,          /* #st-theme */
    HIT_USER,           /* #st-user */
    HIT_PASS,           /* #st-pass */
    HIT_EYE,            /* #st-eye */
    HIT_SUBMIT,         /* .btn.primary "כניסה" */
    HIT_CAPTURE,        /* #st-menu-capture */
    HIT_LOGOUT,         /* menu: local session logout */
    HIT_RESTORE,        /* restore to this machine's disk (#382; no HTML id yet) */
    HIT_ROOM,           /* #st-menu-room */
    HIT_DIRECT,         /* #715: deploy THIS disk directly to the room (native-only) */
    HIT_CLASSES,        /* #st-menu-classes */
    /* #st-pick */
    HIT_NAME,           /* #st-name */
    HIT_DESC,           /* #st-desc */
    HIT_FOLDER,         /* #st-folder (select) */
    HIT_NEWFOLDER,      /* #st-newfolder "+ חדשה" */
    HIT_FOLDER_NEW,     /* #st-folder-new */
    HIT_START,          /* #st-start "התחל קליטה" */
    HIT_BACK,           /* #st-back-capture / #room-back / #cls-back "חזרה" */
    /* #st-done */
    HIT_AGAIN,          /* #st-again "קליטה נוספת" */
    /* #706 restore-to-local-disk screen (native-only) */
    HIT_RESTORE_IMAGE,  /* image picker (select) */
    HIT_RESTORE_START,  /* "התחל שחזור" */
    /* #st-room (room.js) */
    HIT_ROOM_IMAGE,     /* #room-image (select) */
    HIT_ROOM_TARGET,    /* #room-target */
    HIT_ROOM_OPEN,      /* #room-open */
    HIT_ROOM_WAKE,      /* #room-wake */
    HIT_ROOM_START,     /* #room-start */
    HIT_ROOM_CLOSE,     /* #room-close */
    HIT_ROOM_POWEROFF_ALL, /* #1367: shut down every cloner */
    HIT_ROOM_SELECT_ALL,/* #714: select/clear every disk in the room */
    /* #st-class (classes.js) */
    HIT_CLASS_START,    /* #cls-start */
    HIT_CLASS_CLOSE,    /* #cls-close */
    /* the cloner's SMART choice (no HTML id: this screen is native-only) */
    HIT_SMART_REPLACE,
    HIT_SMART_RESCUE,
    HIT_SMART_SKIP,
    /* #928: the capture's shrink question (a modal over the build screens) */
    HIT_SHRINK_CONTINUE,
    HIT_SHRINK_PLAIN,
    HIT_SHRINK_CANCEL,
    /* #649: the toolbox (native-only; build machine's menu) */
    HIT_TOOLS,          /* the "כלים" button at the menu's top-left */
    HIT_TOOL_LIST,      /* back to the list from confirm / output */
    HIT_TOOL_RUN,       /* confirm view: run */
    HIT_TOOL_AGAIN,     /* output view: run again */
    HIT_TOOL_ARG,       /* confirm view: the free-text argument field */
    HIT_TOOL_UP,        /* list / output: scroll */
    HIT_TOOL_DOWN,
    /* #1188: first-boot installer. */
    HIT_INSTALL_BACK,
    HIT_INSTALL_NEXT,
    HIT_INSTALL_PRIMARY,
    HIT_INSTALL_SECONDARY,
    HIT_INSTALL_CHECK_PRIMARY,
    HIT_INSTALL_DHCP,
    HIT_INSTALL_STATIC,
    HIT_INSTALL_PRIMARY_URL,
    HIT_INSTALL_ADDRESS,
    HIT_INSTALL_NETMASK,
    HIT_INSTALL_GATEWAY,
    HIT_INSTALL_DNS,
    HIT_INSTALL_HOSTNAME,
    HIT_INSTALL_PASSWORD,
    HIT_INSTALL_CONFIRM,
    HIT_INSTALL_CURRENT_PASSWORD,
    HIT_INSTALL_RETRY,
    HIT_INSTALL_OUTPUT_UP,
    HIT_INSTALL_OUTPUT_DOWN,
    /* mockup 10/10: Esc on the menu asks before it signs out */
    HIT_LOGOUT_CONFIRM,
    HIT_LOGOUT_CANCEL,
    /* owner, 10/10: image pickers are folder-first -- back to the folders */
    HIT_PICK_FOLDERS_BACK,
    /* #1270 (owner, 10/10): no typed-word confirmation -- a destructive action
     * asks in a dialog: [ביטול] and the red action */
    HIT_CONFIRM_OK,
    HIT_CONFIRM_CANCEL,
    HIT_RESTORE_CANCEL,   /* #1561: "ביטול השחזור" on the restore's progress card (cancel=1) */
    /* #1269: drivers per target disk, on the room / direct setup screen */
    HIT_ROOM_DRV_SWITCH,  /* "להוסיף דרייברים" */
    HIT_ROOM_DRV_MODEL,   /* the model (select) */
    HIT_ROOM_DRV_ALL,     /* "כל הדיסקים שנבחרו" */
    HIT_ROOM_DRV_PICK,    /* "לבחור דיסקים" */
    /* #1245: the typing-language chip in the status bar (EN <-> עב) */
    HIT_LANG,
    /* 11/10 (GUI only): the agent's question dialog (screens_ask.c) */
    HIT_ASK_PRIMARY,
    HIT_ASK_SECONDARY,
    HIT_END_NOW,          /* #1560: "כבה עכשיו" on the end-of-task count */
    HIT_END_HOLD,         /* #1560: "עצור את הספירה" -- read the results (partial/failed) */
    HIT_IDLE_KEEP,        /* mockup 10: the idle window's "השאר דלוק" (Esc) */
    HIT_IDLE_OFF,         /* mockup 10: the idle window's "כבה עכשיו" (Enter) */
    HIT_WAIT_BACK,        /* #2079 (mockup 02): "חזרה למסך הבחירה" on the cloner's wait (Esc) */
    /* #1322: the boot choice and the local clone */
    HIT_LC_BOOT_SERVER, HIT_LC_BOOT_LOCAL, HIT_LC_BACK, HIT_LC_NEXT, HIT_LC_GO, HIT_LC_CANCEL,
    HIT_LC_STOP, HIT_LC_STOP_GO, HIT_LC_STOP_NO, HIT_LC_OFF, HIT_LC_AGAIN,
    HIT_LC_BOOT_PULL,     /* 16/10: "שחזור אימג' מהשרת" */
    /* 16/10 (Nadav): the SMART warning window -- "דלג" and "כתוב בכל זאת" */
    HIT_SDLG_SKIP, HIT_SDLG_GO,
    HIT_FAIL_BTN,         /* #2068: the failure screen's buttons, HIT_FAIL_BTN + i (MAX_FAIL_BTNS of them) */
    HIT_FAIL_BTN1,
    HIT_FAIL_BTN_LAST,
    /* #1386: "בטל דיסק זה" -- the window's two buttons, and "בטל דיסק…" in the foot */
    HIT_CDLG_KEEP, HIT_CDLG_GO, HIT_CDLG_OPEN,
    HIT_LC_REBOOT,        /* #1914 (Nadav 20/10): "הפעל מחדש" on the USB stick's result screen */
    HIT_LC_USBSET,        /* #1914 (Nadav 20/10): "הגדרות" -- top left of the USB stick's source screen */
    HIT_PWR_KEEP, HIT_PWR_STOP,   /* #1918: the power button's question -- "המשך" and the red "עצור וכבה" */
    HIT_SYSP_CANCEL,      /* #1896: "ביטול" in the C: picker */
    HIT_INSTALL_SUMMARY_BASE = 1000,
    HIT_INSTALL_NIC_BASE = 1020,
    /* ranges: base + index */
    HIT_DISK_BASE   = 100,   /* .disk-card[data-dev] */
    HIT_CLASS_BASE  = 200,   /* .menu-card[data-class] */
    HIT_OPTION_BASE = 300,   /* a row of the open <select> list */
    /* #1495 room disk: +(machine_index * MAX_DRAWERS)+room_drawers index */
    HIT_ROOM_DISK_BASE = 400,
    /* #714 machine name: +machine_index -- toggles all that machine's disks */
    HIT_ROOM_MACHINE_BASE = 700,
    /* #649: a tool row (+index into a->tools) and a disk row of the
     * confirm view (+index into st.disks) */
    HIT_TOOL_BASE = 800,
    HIT_TOOL_DISK_BASE = 900,
    /* owner, 10/10: a folder of the image picker (+index from image_folders) */
    HIT_PICK_FOLDER_BASE = 950,
    /* #1495 drivers tag: +(machine_index * MAX_DRAWERS)+room_drawers index */
    HIT_ROOM_DTAG_BASE = 2000,
    /* #65 "כבה אותו" on a missing machine: +index into st.room_missing */
    HIT_ROOM_POWEROFF_BASE = 2400,
    /* #1322: a drawer of the local clone (+disk index), its role buttons
     * (+index * 3 + LCR_*), and "הוצא את דיסק N מהשיכפול" (+index) */
    HIT_LC_BAY_BASE = 3000, HIT_LC_ROLE_BASE = 3100, HIT_LC_DROP_BASE = 3200,
    /* 16/10: a drawer of the cloner's restore screen (+port, 1..MAX_DRAWERS) */
    HIT_PULL_DRAWER_BASE = 3300,
    /* #1386: "בטל…" on a writing drawer's row (+index into the screen's cancel
     * picks, cloner_cancel_picks / room_cancel_picks) and a row of the window's
     * picker (+index) */
    HIT_BAY_CANCEL_BASE = 3400, HIT_CDLG_PICK_BASE = 3500,
    HIT_SYSP_BASE = 3600,  /* #1896: a partition of the C: picker (+index into the image's cand) */
    HIT_IDENTIFY_BASE = 3700,  /* #1966: identify a drawer (+drawer index); not 3600 -- that is #1896's C: picker */
};

typedef struct { Rect r; int id; } Hit;

typedef enum {
    SCREEN_LOGIN, SCREEN_MENU, SCREEN_PICK, SCREEN_PROGRESS, SCREEN_DONE,
    SCREEN_ROOM, SCREEN_CLASS, SCREEN_CLONER, SCREEN_STANDBY, SCREEN_MESSAGE, SCREEN_RESTORE,
    SCREEN_TOOLS,       /* #649 */
    SCREEN_INSTALL,     /* #1188 */
    SCREEN_LCBOOT,      /* #1322: "מהשרת" or "שיכפול מקומי", on a cloner's boot */
    SCREEN_LOCAL,       /* #1322: the local clone -- source, progress, result */
    SCREEN_FAILURE      /* #1569: the one failure screen (fail= records, failscreen.sh) */
} Screen;

/* station.js MODE: null (menu) / "capture" / "room" / "classes"; #706 adds restore */
/* #715: MODE_DIRECT is the room screen without an image picker -- the source
 * is this machine's disk; open emits the chosen drawers as target_slots. */
typedef enum { MODE_MENU, MODE_CAPTURE, MODE_ROOM, MODE_CLASSES, MODE_RESTORE, MODE_DIRECT, MODE_TOOLS } Mode;

typedef enum { TASK_NONE, TASK_PENDING, TASK_RUNNING, TASK_DONE, TASK_FAILED } TaskState;

/* #1270: what the confirm dialog is about to do */
enum { CONFIRM_NONE, CONFIRM_RESTORE, CONFIRM_ROOM_STOP, CONFIRM_CLASS_STOP, CONFIRM_TOOL,
       CONFIRM_ROOM_START_WARN,   /* #432: start a wave that has a precheck warning */
       CONFIRM_ROOM_POWEROFF,     /* #65: power off one missing cloner */
       CONFIRM_ROOM_POWEROFF_ALL,   /* #1367: power off the room */
       CONFIRM_RESTORE_CANCEL };    /* #1561: stop "שחזור לדיסק הזה" and leave the disk empty */

/* 16/10 (Nadav): a write to -- or a read from -- a disk with a SMART warning
 * is allowed, but only after this window: the disk by its number, its colour
 * and why, model and serial; "דלג" (the default: the focus, Esc) and the red
 * "כתוב בכל זאת" (no typed word). Only its OK carries the action out
 * (smartdlg.c); the screen behind it gets nothing while it is open. */
enum { SDLG_NONE, SDLG_CLONER, SDLG_RESTORE, SDLG_CAPTURE, SDLG_LC_TARGET, SDLG_LC_SOURCE };
typedef struct {
    int kind;                   /* SDLG_*; SDLG_NONE = closed */
    int go;                     /* the focus: 0 = "דלג" (the default), 1 = the red button */
    int level;                  /* smart_level of the disk: 1 orange, 2 red */
    int retry;                  /* #1580: failed_last -- the red button is "נסה שוב" */
    int readonly;               /* #1742: the reason is "readonly" -- the refusal says so, not "SMART נכשל" */
    int ref;                    /* the disk: an index into st.disks / st.lc.disks */
    char dev[32];               /* ... and its device: a reload that moves it closes the window */
    char nonce[32];             /* SDLG_CLONER: the request it answers -- a new request closes it */
    char disk[SLOT_NAME_MAX + 64], model[80], serial[80], reason[200];   /* disk: #1495 label, "(לא ממופה)" too */
} SmartDlg;

/* #1386 (Nadav 17/10): "בטל דיסק זה" -- the window over the cloner's screen and
 * the build machine's distribution screen (canceldlg.c). A pick is one writing
 * drawer; the window confirms one of them. "המשך לכתוב" (Esc) is the way out,
 * never a word "ביטול" next to "בטל"; the red "בטל את דיסק N" is Del, not Enter. */
#define MAX_CANCEL_PICKS 32
typedef struct {
    char mac[24], dev[32];      /* what is cancelled: the bridge validates both again */
    char label[SLOT_NAME_MAX + 64], where[160], model[80], size[32]; int pct;
    int drawer;                 /* the drawer number: the cloner's key for it (0 = none) */
} CancelPick;
typedef struct {
    int open, build, picker;    /* build: emits room-drawer-cancel; picker: X, choose among the writers */
    int sel, n;
    CancelPick picks[MAX_CANCEL_PICKS];
} CancelDlg;

typedef struct {
    char dev[32], model[80]; unsigned long long size_bytes; int has_data, removable;
    int slot_known;
    int drawer; char conn[32];
    /* 16/10: dsmart=dev|verdict|reason|serial|port -- the agent's cached SMART of
     * this machine's own disk; "" = not read, never "healthy" */
    char smart[16], smart_reason[48], serial[80]; int port;
} Disk;
/* #1896: an old image whose C: is not known (image_unchecked=) -- shown "לא
 * נבדק" in the picker and never restored; its candidates (image_cand=) are
 * what the C: picker offers. More than MAX_SYSP_CANDS: marked in the console. */
#define MAX_SYSP_CANDS 4
typedef struct { char id[64], name[96], folder[64]; int unchecked, ncand;
                 struct { int idx; unsigned long long size, used; } cand[MAX_SYSP_CANDS]; } Image;
/* #695: one physical slot of a room machine, for the distribute grid. present
 * is separate from size_bytes (a real disk may report 0). port 0 is the
 * legacy/null-port fallback assigned by state_parse(). */
typedef struct {
    int port, present, fresh, selected, unidentified;   /* #1235: no serial */
    int fallback;       /* #704: port 0 -- drawn in a borrowed slot, not that slot's name */
    char serial[80], model[80], smart[16];
    char smart_reason[24];   /* #1742: room_drawer field 24 -- "readonly" says why red; "" = not reported */
    unsigned long long size_bytes;
    /* #1269: drawer_list[].drivers of the live round -- staged / skipped /
     * failed, "" = the step did not run on this drawer. detail = the
     * packages (staged), the error (failed) or the reason (skipped). */
    char drv_state[12], drv_detail[160];
    /* #1331: room_drawer fields 21-23 -- drivers.boot (bootstorage.sh): enabled /
     * already / skipped / failed, the service and the error; "" = not reported */
    char drv_boot[12], drv_bsvc[24], drv_berr[160];
    /* #1331 T8: room_drawer fields 24-26 -- drivers.linux: state, module, error|reason */
    char drv_lstate[12], drv_lmod[12], drv_ldet[160];
    int link_slow;      /* #432: room_drawer field 12 = link_state "slow" -- orange like a SMART warning */
    long crc_suspect;   /* #1366: field 13 -- the CRC count over crc_warn_threshold, orange; 0 = no */
    char slot_name[SLOT_NAME_MAX];
    int drawer; char conn[32];
    /* 17/10: room_drawer fields 16-17 -- the drawer's own target in the wave:
     * pct (-1 = no total) and its state ("" = not a target). own_progress 0 =
     * an older agent: the live grid falls back to the machine's progress. */
    int own_progress, pct; char tstate[16];
    /* #1386: room_drawer fields 18-20 -- the drawer's device (what "בטל דיסק זה"
     * names), the server's "בוטל על ידי …" line ("" = not cancelled) and whether the
     * agent reported it (0 = asked, still "מבטל…") */
    char dev[32], cancel_text[120]; int cancel_done;
} RoomDrawer;
typedef struct { char conn[32], name[SLOT_NAME_MAX]; int set; } RoomSlot;
/* #1269: one model the driver library has a package for, in its spelling. */
typedef struct { char vendor[64], model[128]; } DriverModel;
typedef struct {
    char name[64], mac[24], state[24], error[120];
    int awake, joined, fresh_drawers, drawer_count, local_copy;
    int pct;            /* 0..100, or -1 when the total is unknown (Progress.view) */
    int moving;         /* bytes_written > 0 -- distinguishes the two unknown states */
    RoomDrawer room_drawers[MAX_DRAWERS];
    int nroom_drawers;
    /* #704: [port-1] = the operator's name of that slot (room_drawer field
     * 11, else room_slot_name=); "" = drawn as "דיסק N" as before. */
    char slot_names[MAX_DRAWERS][SLOT_NAME_MAX];
    RoomSlot rslots[MAX_DRAWERS]; int nrslots;
} Machine;
typedef struct { char id[64], label[80]; int machines; } ClassGroup;
/* One tray of a cloning machine. Labelled/sorted by SATA port, not device
 * order (roomflow.sh: sdb is not "disk 2"). port 0 = no slot derived. */
typedef struct {
    int port;
    char dev[32], state[24], error[160];
    unsigned long long bytes, total;
    /* #410: from the kiosk's local counter (clonergui.sh:cloner_gui_pace).
     * rate_bps <= 0 / eta_s < 0 = not measured -- never drawn as 0. */
    long long rate_bps; int eta_s;
    int drawer; char conn[32];
    /* #1386: drawer= fields 11-12 -- who asked to cancel it ("" = nobody, as the
     * screens say it: "על ידי nadav", "ממסך מחשב השיכפול") and when the agent
     * carried it out (epoch; 0 = not yet: "מבטל…") */
    char cancel[120]; long long cancel_at;
    /* #1331: drawer fields 13-17 -- this drawer's drivers outcome
     * (targets/<dev>/drivers.json, drvgui.sh); drv_state "" = did not run */
    char drv_state[12], drv_detail[160], drv_boot[12], drv_bsvc[24], drv_berr[160];
    /* #1331 T8: drawer fields 18-20 -- drivers.linux: state, module, error|reason */
    char drv_lstate[12], drv_lmod[12], drv_ldet[160];
    /* #1907: a drawer_rb= record -- done, not verified: the check list did not come down */
    int rb_nolist;
    /* #2055: a drawer_moved= record -- when its bytes last moved (epoch; 0 = not said) */
    long long moved;
} Drawer;
/* A physically connected disk shown on the cloner's IDLE screen (no round yet),
 * so the operator sees what is plugged in before a broadcast starts. */
typedef struct { int port; char dev[32], model[48]; unsigned long long size; char smart[16];
                 char cause[32]; /* #874: cable|disk|unclassified for failed_last, else ""; #1921: 32 -- reported_uncorrectable */
                 char link[8], link_max[8]; /* #432: "6.0" Gb/s; "" = not read, never "fine" */
                 long crc_suspect; /* #1366: field 9 -- the CRC count over crc_warn_threshold; 0 = not over / not read */
                 int drawer; char conn[32];
                 char mark[12]; /* #1798: field 12 -- incomplete|unchecked; "" = clean or not reported */
                 char hw[240]; /* #1921: field 13 -- the drawer's hardware note (hw-issues.tsv), never the disk's colour */ } IdleDisk;
typedef struct { char conn[32], name[SLOT_NAME_MAX]; int set; } ClonerBay;
/* #2030: cused=dev|size|used -- the size of a disk of this cloner and the bytes
 * in use on it (diskused.sh). used -1 = not measured: said so, never 0. */
typedef struct { char dev[32]; unsigned long long size; long long used; } CUsed;
typedef struct { int drawer; char conn[32], model[48]; } COptical;

/* Everything the --state file can say. See README "The --state file". */
typedef struct State {
    Disk disks[MAX_DISKS];              int ndisks;
    char folders[MAX_FOLDERS][64];      int nfolders;
    Image images[MAX_IMAGES];           int nimages;
    Machine machines[MAX_MACHINES];     int nmachines;
    ClassGroup classes[MAX_CLASSES];    int nclasses;
    Drawer drawers[MAX_DRAWERS];        int ndrawers;
    IdleDisk idisks[MAX_DRAWERS];       int nidisks;   /* cloner idle inventory */
    ClonerBay cbays[MAX_DRAWERS];        int ncbays;
    CUsed cuseds[MAX_DRAWERS + 4];       int ncuseds;   /* #2030: any disk the cloner has, drawers or not */
    COptical copticals[MAX_DRAWERS];     int ncopticals;
    int drawer_count;                    /* configured cloner bays; 0 = legacy default */
    char disk_probe[16];                /* drives / no_disks / no_ports / unchecked */

    /* the build machine's own task (station.js drawProgress / drawDone) */
    TaskState task;
    int task_direct;                    /* #715: task.type == direct_send (sending, not capturing) */
    char task_name[96], task_disk[32], task_error[1024];
    char agent_version[129];   /* #1257; server_version is declared with the #1252 header fields */
    int pct, moving, partition;         /* pct -1 = unknown total */
    unsigned long long bytes;           /* bytes_written */
    char title[128], sub[200];          /* overrides for #st-prog-title / #st-prog-sub */
    int can_cancel;                     /* #1561: cancel=1 -- the write may still be stopped */

    /* message=title|sub forces #st-message (like !state.known) */
    char msg_title[96], msg_sub[200];

    /* room.js round */
    int has_round; char round_image[96];
    char room_label[80];
    int wave_number, wave_open, written, target, ready, remaining;
    /* #410: round=...|elapsed_s|rate_bps|eta_s from the server (room.py
     * _wave_pace); -1 = not measured. updated= is the epoch the agent wrote
     * this snapshot, so the screen can say how old it is (0 = no stamp). */
    int elapsed_s, eta_s; long long rate_bps, updated;
    /* 17/10: round= field 11 -- the wave's own state (running / closed /
     * failed); "" = an older agent. Only "running" may say "הגל משדר". */
    char wave_state[16];
    /* #434: idle_poweroff_at=<epoch> from guistate.sh while the agent counts
     * down to a power-off; 0 = no countdown. Any key press emits "touch". */
    long long idle_poweroff_at; int idle_poweroff_s;   /* mockup 10: field 2 -- the idle time that ran out */
    /* #1560: end_poweroff=<epoch>|<kind> while the agent counts down to the
     * power-off at the end of a task (idle.sh end_countdown); 0 = none.
     * #2024: held with epoch 0 = the results stay, no time limit (end_kind
     * says a record is there, not the epoch). end_problem=<dev>|<r|o>|<reason>:
     * the targets that hold it, drawn highlighted with their reason. */
    long long end_poweroff_at; int end_kind, end_done, end_total, end_held;
    struct { char dev[32], why[400]; int red; } end_prob[MAX_DRAWERS]; int nend_prob;
    /* classes.js live session */
    int has_session; char sess_image[96], sess_prefix[32], sess_group[80];
    int sess_open, joined, expected, starts_in;
    /* #880: menu_class=0|1 from guistate.sh -- the "הפצה לכיתות" card is
     * offered only when the server's switch is on. Absent = 0 (v1 default). */
    int menu_class;
    /* menu_tools=0|1 from guistate.sh -- v1 ships without the toolbox
     * (Nadav, 19/09; v1.1 turns it on): the "כלים" button is drawn only
     * when the server said so. Absent = 0. */
    int menu_tools;
    /* #1073: machine_name=<registered name> from guistate.sh -- the restore
     * screen's typed confirmation (principle 7). Empty = the registry has
     * none, and the screen refuses to start rather than match "" to "". */
    char machine_name[64];
    /* #1252: the server this machine talks to, for the header, the login foot
     * and the standby line -- server_name=/server_version= from GET
     * /api/v1/agent/state, server_addr= from imagesrv.server. Empty = not
     * known, drawn as "לא ידוע" -- never the mockup's numbers. */
    char server_name[64], server_addr[64], server_version[129];
    char tz[65]; /* #1447: POSIX TZ from the server; empty = not known -> UTC. */
    /* #1269: driver_models=<n> and one driver_model=<vendor>|<model> per
     * model, from GET /api/console/room/driver-models. driver_models_read
     * -1 = the list was not read (also when the key is absent); 0 = read,
     * and the library has no package with a model rule. Two reasons the
     * switch is off, said in two different words (principle 5). */
    DriverModel driver_models[MAX_DRIVER_MODELS]; int ndriver_models;
    int driver_models_read, hidden_models;

    /* the cloner screen (clonergui.sh): image name + a pending SMART choice.
     * smart_nonce is the request's identity, drawn with the prompt and echoed
     * back on the click so the bridge binds it to the exact request. */
    char cloner_image[96];
    int server_restarted_wave;       /* #1377: closed report after server restart */
    /* #704: cslot_name=<port>|<name> -- [port-1], "" = "דיסק N" */
    char cslot_names[MAX_DRAWERS][SLOT_NAME_MAX];
    int smart_pending, smart_port;
    char smart_nonce[32], smart_verdict[16], smart_reason[96];
    char smart_model[80], smart_serial[80];   /* 16/10: fields 5-6, "" = not reported */

    /* #928: the capture's shrink question (shrinkgui.sh via guistate.sh):
     * shrink_prompt=<nonce>|<count> and one shrink_partition=<idx>|<old
     * bytes>|<new bytes> per row. Pending only when every row arrived and
     * is valid -- a partial table is never drawn as the whole question. */
    int shrink_pending, nshrink;
    int shrink_direct;                  /* #1171: <nonce>|<count>|direct -- the direct send's words */
    char shrink_nonce[32];
    struct { int idx; unsigned long long old_bytes, new_bytes; } shrink[MAX_SHRINK_PARTS];

    /* 11/10 (GUI only): a question the agent used to ask on the text console
     * (askgui.sh via guistate.sh): ask_prompt=<nonce>|<kind>|<count>|<subject>
     * and, for the grow-back, one ask_row=<idx>|<now bytes>|<orig bytes>|
     * <table|fs> per row. All-or-nothing, like the shrink question.
     * #1808 raid: one ask_row=<port>|<metadata type>; #1898 share:
     * ask_row=<port>|<idx>|<now bytes>|<after bytes> (orig_bytes = after).
     * #2017 badsect: one ask_row=<port>|<idx>|<epoch> -- until = when the copy
     * goes on by itself (Nadav 22/10), counted down on the dialog. */
    int ask_pending, ask_kind, nask;
    char ask_nonce[32], ask_subject[32];
    struct { int idx, fs_only, port; unsigned long long now_bytes, orig_bytes; char word[24]; long long until; } ask[MAX_ASK_ROWS];

    /* #1918: the power button pressed during a write (agent/lib/core/powerbtn.sh):
     * power_ask=<nonce>|<kind>|<can_stop>. Empty nonce = no question. kind:
     * localclone, round, restore, capture, unknown; can_stop 0 = "המשך" only. */
    char power_nonce[24], power_kind[16];
    int power_can_stop;

    /* #1090: positive evidence from the agent's latest hello. -1 means the
     * key was absent, which is different from a measured zero seconds/HTTP 0. */
    int hello_age, hello_rc;

    /* Parser integrity is visible, not only stderr: a partial picture must
     * never look complete to the operator. */
    int hidden_disks, hidden_machines, rejected_lines, stale;
    char state_warning[160];

    char form_error[160], class_error[160], toast[160];
    /* #1269: 256, not 160 -- "הדגם <model> כבר אינו בספריית הדרייברים..."
     * carries a model of up to 127 bytes (DriverModel.model); at 160 gcc
     * warns -Wformat-truncation, and a cut would split a UTF-8 letter. */
    char room_error[256];
    /* 17/10: room_failed=<reason> -- the last round failed. It is not a live
     * round (it never sets has_round): the picker stays, with one line why. */
    char room_failed[300];

    /* #432: round.precheck of an open wave -- room_warn=<text>, one per
     * drawer the wave would write on a slow link or a risen CRC. Orange: a
     * warning before "התחל", never a block. #65: round.missing --
     * room_missing=<mac>|<name>|<reason>|<can_poweroff>|<text>|<local_copy>. The texts
     * come from the server, the same words the console shows. */
    char room_warn[MAX_ROOM_NOTES][320]; int nroom_warn, hidden_room_warn;
    struct { char mac[24], name[64], reason[16], text[320]; int can_poweroff, local_copy; } room_missing[MAX_ROOM_NOTES];
    int nroom_missing, hidden_room_missing;
    /* #1322: the boot choice and the local clone (lcgui.sh) */
    LcState lc;

    /* #1569 (mockup PR #1550): the failure screen, from failscreen.sh --
     * fail=<tone bad|warn>|<hold 0/1>|<title>|<sub> opens a block; the
     * fail_why= / fail_safe= / fail_ctx= / fail_row= / fail_step= / fail_code=
     * lines after it belong to it (a second fail= replaces the block). A
     * non-empty title routes to SCREEN_FAILURE before everything else.
     * fail_at: the epoch of die_local's reboot (0 = the machine holds).
     * fail_info (#1802): tone inf -- not a failure (ui_unknown, mockup case 8). */
    char fail_title[128], fail_sub[160], fail_why[400], fail_safe[240];
    int fail_warn, fail_info, fail_hold, fail_cloner; long long fail_at;   /* fail_cloner: field 5 = cloner (the header's role) */
    struct { char label[48], value[96]; } fail_ctx[MAX_FAIL_CTX]; int nfail_ctx;
    struct { char label[96], sub[240], kind[8], pill[32]; } fail_rows[MAX_FAIL_ROWS]; int nfail_rows;
    char fail_steps[MAX_FAIL_STEPS][240]; int nfail_steps;
    char fail_code[MAX_FAIL_CODE][240]; int nfail_code;   /* not drawn (#2068): the log is in the console's drawer */
    struct { char action[16], label[64]; } fail_btns[MAX_FAIL_BTNS]; int nfail_btns;   /* #2068: fail_btn=<action>|<label> */
} State;

/* #649: one row of $GUI_DIR/tools (id|domain|title|risk|args). risk is
 * ro / rw / destroy; anything else is treated as destroy, like tools.sh. */
typedef struct { char id[48], domain[24], title[120], risk[12], args[96]; } Tool;
typedef enum { TOOLS_LIST, TOOLS_CONFIRM, TOOLS_OUTPUT } ToolsView;
/* A polled file's identity: mtime (with the nanoseconds tmpfs keeps), size
 * and inode -- the bridge unlinks and recreates tool-result per run. */
typedef struct { long sec, nsec, size, ino; int seen; } FileMark;

typedef struct App {
    Screen screen;              /* derived by app_route() from the rest */
    const Theme *theme;
    const Theme *default_theme; /* #1499: before login, on the cloner screens, after logout */

    char title[128];            /* #st-title */
    char mac[40];               /* #st-mac  (dir=ltr, mono) */
    char ip[48];                /* #st-ip */

    /* login */
    char user[64];
    char pass[128];
    int show_pw;                /* #st-eye toggled */
    int focus;                  /* HIT id of the focused text field */
    int lang_he;                /* #1245: typing in Hebrew (SI-1452); Alt+Shift or the chip switch */
    char error[200];            /* #st-login-error */

    /* session */
    int signed_in;              /* station.js SIGNED_IN */
    char signed_user[64];       /* "מחוברים כ־<user>" */
    int admin;                  /* station.js: capture card only for role admin */
    int menu_focus;             /* keyboard focus, -1 = none: 0..n-1 the visible cards,
                                   n "התנתק", n+1 "כלים" (when the menu shows it) */
    int logout_confirming;      /* mockup 10/10: Esc asked "להתנתק?" -- Enter signs out, Esc cancels */
    /* #1270 (owner, 10/10): the open confirm dialog, CONFIRM_*; 0 = none.
     * Enter confirms, Esc cancels; the token the bridge/server still checks
     * (machine name, round image) is filled from the known value. */
    int confirm;
    /* #65: the machine CONFIRM_ROOM_POWEROFF is about -- copied at the click,
     * so a reload that reorders the list cannot retarget the dialog. */
    char poweroff_mac[24], poweroff_name[64]; int poweroff_local_copy;
    Mode mode;
    int force_progress;         /* --screen progress: show it even with no task */
    int force_cloner;           /* --screen cloner: the cloning machine's own screen */
    int force_standby;          /* --screen standby / idle cloner */
    int fixed_clock;            /* --png: header clock is a constant, so two renders
                                   a minute apart are byte-identical (test flake, 19/09) */
    int watching;               /* #st-progress was on screen (drawDone trigger) */
    /* #408: the capture rate, measured here from two state reads (bytes and
     * the agent's updated= epoch). <= 0 = not measured yet -- never drawn as 0.
     * "Is it alive?" needs only the counter; the rate answers it without a
     * denominator, which is exactly what a dirty NTFS (no used_bytes) lacks. */
    unsigned long long rate_prev_bytes; long long rate_prev_updated; double capture_rate_bps;
    int showing_done;           /* stay on #st-done until "קליטה נוספת" */
    char done_title[64], done_sub[1100];   /* holds task_error[1024] whole (gcc -Wformat-truncation) */

    /* #st-pick */
    int chosen_disk;            /* index into st.disks, -1 = none (CHOSEN_DISK) */
    char name[96], desc[160];
    int folder_sel;             /* 0 = none chosen yet (#1251: no "no folder"), i = st.folders[i-1] */
    int newfolder_shown;        /* #st-folder-new visible */
    char folder_new[64];

    /* #st-room */
    int image_sel;              /* 0 = "בחרו אימג'…", i = st.images[i-1] */
    char room_target[8];        /* #room-target, digits */
    int room_target_set;        /* value seeded once per setup skeleton */
    /* #706 restore-to-local-disk: own selection so a room reload never
     * authorizes a local erase, and its own confirm field -- the machine's
     * registered name since #1073 (was the word ERASE). */
    int restore_image_sel;      /* 0 = "בחרו אימג'…", i = st.images[i-1] */
    /* 16/10: the cloner's restore screen -- the drawers ticked as targets, by
     * port ([port], 1..MAX_DRAWERS). Nothing is ticked by itself (#1410). */
    int pull_sel[MAX_DRAWERS + 1];
    /* 16/10: the keyboard's place on that screen -- 0 = the image list (the
     * digits pick folders and images), a port = that drawer has the focus */
    int pull_focus;
    /* #695: per-disk target overrides, keyed by machine identity + physical
     * port -- outside State so a 2 s state reload does not erase clicks. */
    struct { char mac[24]; int port, selected; } room_selection[MAX_ROOM_SELECTIONS];
    int nroom_selection;
    /* Nadav 17/10: whole cloners picked (by MAC) -- an off machine that
     * reported no drawer too. "הער" wakes exactly these plus every machine
     * with a ticked drawer, and nothing else (room_wake in main.c). */
    char room_pick[MAX_MACHINES][24]; int nroom_pick;
    /* #1269: "להוסיף דרייברים" -- off on every entry (room_drv_reset).
     * drv_pick 0 = one model for every chosen disk, 1 = per disk: a disk
     * with no drv_map entry takes the current model, an entry with an empty
     * vendor is "בלי". vendor/model are the library's spelling, so a 2 s
     * reload that reorders the list does not move a choice. There is no
     * "automatic": the cloner's hardware is not the target's. */
    int drv_on, drv_pick;
    char drv_vendor[64], drv_model[128];
    struct { char mac[24]; int port; char vendor[64], model[128]; } drv_map[MAX_ROOM_SELECTIONS];
    int ndrv_map;

    /* #st-class */

    int dd_open;                /* HIT_FOLDER | HIT_ROOM_IMAGE, or 0 */
    /* owner, 10/10: the image pickers (restore cards, room select) open on
     * the folders; pick_in_folder = 1 shows the images of pick_folder. One
     * pair for both pickers -- they are never on screen together; reset when
     * a card is chosen or the menu comes back. */
    char pick_folder[64];
    int pick_in_folder;

    /* #649: the toolbox. The list and the result are two files next to the
     * state file ($GUI_DIR/tools, $GUI_DIR/tool-result), polled like it. */
    char tools_dir[512];        /* dirname of --state; empty = no bridge */
    Tool tools[MAX_TOOLS];      int ntools;
    char tools_machine[64];     /* machine= header: the name to type */
    int tools_loaded;           /* the tools file was read at least once */
    FileMark tools_mark, result_mark;
    ToolsView tools_view;
    int tool_sel;               /* index into tools, -1 = none */
    int tool_disk_sel;          /* confirm view disk picker: index into st.disks, -1 */
    char tool_arg[96], tool_error[160];
    int tool_list_scroll, tool_scroll;
    int tool_running, tool_rc;  /* rc -1 = no result yet */
    long tool_seq;              /* seq of the last result consumed */
    double tool_spin;           /* spinner angle while running */
    char tool_out[TOOL_OUT_MAX];
    int tool_line_off[TOOL_OUT_LINES]; int tool_nlines;

    char toast[160];            /* #toast; empty = hidden */
    double toast_until;         /* monotonic seconds */

    char shrink_sent[32];       /* #928: the nonce already answered -- one answer per question */
    char ask_sent[32];          /* 11/10: the same, for the question dialog */
    char power_sent[24];        /* #1918: the power question already answered (its nonce) */
    int sysp_open;              /* #1896: the C: picker is open on image sysp_open-1; 0 = closed */

    State st;

    LcUi lcu;                   /* #1322: what the person chose on the local clone screens */
    SmartDlg sdlg;              /* 16/10: the SMART warning window (smartdlg.c); kind 0 = closed */
    CancelDlg cdlg;             /* #1386: "בטל דיסק זה" (canceldlg.c); open 0 = closed */

    /* #1188: local UI state; validation and apply remain in wizard/core.py. */
    InstallState install;

    /* pointer */
    double ptr_x, ptr_y;        /* screen pixels (input.c) */
    int ptr_visible;            /* a mouse moved; touch leaves no cursor */
    /* mockup 10/10: the button under a click is drawn pressed for a moment
     * (input.c reports the press, not the release) -- feedback on the frame
     * the click lands, before whatever the click does is visible. */
    int pressed_id;             /* HIT id, 0 = none */
    double pressed_until;       /* monotonic seconds */
    /* mockup 10/10: one scale for the whole screen, clamp(H/800, 1, 1.4),
     * set by app_draw. Layout is in logical pixels (screen / scale); the
     * hit table too, and app_hit / hovered() convert the pointer. */
    double scale;

    Hit hits[MAX_HITS];
    int nhits;
    int end_off_own;            /* #2081: this frame's screen drew its own "כבה את המחשב" -- the end bar is words only */
    Rect clip; int clip_on;     /* .sbody overflow: hits outside are dropped */
} App;

/* Draw the whole screen into <cr> (W x H pixels) and rebuild the hit table. */
void app_draw(App *a, cairo_t *cr, int W, int H);
/* #2001: the USB stick's connections window is up -- F2 is its save there, not the theme */
int app_usbset_window(const App *a);
int app_usbset_here(const App *a);   /* mockup 01: the USB stick's "הגדרות" in the top bar (F4) */
int standby_back_locked(const App *a);   /* #2079: screens_standby.c */
/* The screen's scale (mockup 10/10): clamp(H/800, 1, 1.4) in quarter steps --
 * 1024x768 and 1280x800 stay 1:1, 1280x1024 and 1920x1080 are 1.25. */
double ui_scale(int H);
/* owner, 10/10: the image pickers, folder-first. image_folders lists the
 * distinct folders of st.images in first-seen order (first[k] = an image of
 * folder k, count[k] = how many), at most <max>; an image with no folder is
 * its own group "ללא תיקייה". folder_images lists the indices into st.images
 * of one folder. folder_label is how a folder is named on screen. */
int image_folders(const State *s, int *first, int *count, int max);
int folder_images(const State *s, const char *folder, int *idx, int max);
const char *folder_label(const char *folder);
void app_draw_pointer(App *a, cairo_t *cr);
/* Which registered element is under (x, y); topmost wins. */
int app_hit(const App *a, double x, double y);
/* Decide a->screen from the task, the message, the session and the mode --
 * the order of station.js poll(). Called before every draw. */
void app_route(App *a);
/* #1245: what the status bar's language chip says. LANG_NONE when no text
 * field on this screen has the focus; LANG_EN_ONLY on a field that takes
 * English only (the chip then explains instead of switching). main.c. */
enum { LANG_NONE, LANG_EN_ONLY, LANG_EN, LANG_HE };
int app_typing_lang(const App *a);

/* The screens (one file each group); called by app_draw. */
void screen_login(App *a, cairo_t *cr, double W, double H, double head_h);
void screen_menu(App *a, cairo_t *cr, double W, double H, double head_h);
void screen_pick(App *a, cairo_t *cr, double W, double H, double head_h);
void screen_progress(App *a, cairo_t *cr, double W, double H, double head_h);
void screen_done(App *a, cairo_t *cr, double W, double H, double head_h);
void screen_message(App *a, cairo_t *cr, double W, double H, double head_h);
void screen_room(App *a, cairo_t *cr, double W, double H, double head_h);
void screen_class(App *a, cairo_t *cr, double W, double H, double head_h);
void screen_cloner(App *a, cairo_t *cr, double W, double H, double head_h);
void screen_standby(App *a, cairo_t *cr, double W, double H, double head_h);
/* #1304 (screens_standby.c): the connection the last hello proves --
 * UNKNOWN before the first one finished, OK on a 200 at most 120 s old,
 * OFF otherwise -- and the status bar's IP / MAC / hello, drawn from <left>. */
enum { STANDBY_UNKNOWN, STANDBY_OK, STANDBY_OFF };
int standby_conn(const State *s);
void standby_status_info(App *a, cairo_t *cr, double left, double cy);
void screen_restore(App *a, cairo_t *cr, double W, double H, double head_h);
void screen_tools(App *a, cairo_t *cr, double W, double H, double head_h);
/* #1569 (screens_failure.c): the one failure screen of mockup PR #1550.
 * failure_status_word: the status bar's word and dot for it -- red "נכשל"
 * on a hold the server hears, orange "אין קשר עם השרת" when the last hello
 * says otherwise (principle 5: what the machine knows, not what it hopes). */
void screen_failure(App *a, cairo_t *cr, double W, double H, double head_h);
const char *failure_status_word(const App *a, Rgb *dot);
void screen_install(App *a, cairo_t *cr, double W, double H);
/* #928 (screens_shrink.c): the shrink question, modal over any screen while
 * st.shrink_pending -- it replaces the hit table with its three buttons. */
void draw_shrink_dialog(App *a, cairo_t *cr, double W, double H);
/* 11/10 (screens_ask.c): the agent's question (grow-back / end of capture),
 * modal like the shrink question and drawn in its style; the shrink question
 * wins if both are pending. */
void draw_ask_dialog(App *a, cairo_t *cr, double W, double H);
/* #1898: the split question's words -- "הדיסק" for one disk, "הדיסקים" for more (the rows' drawers) */
int ask_share_words(const State *s, char *lead, size_t n, const char **skip, const char **hint);
/* #1560 (screens_ask.c): the end-of-task count to power-off, one button */
void draw_end_dialog(App *a, cairo_t *cr, double W, double H);
/* #2024: the reason <dev> holds the end (end_problem=), NULL = it does not;
 * *red = 1 red, 0 orange. Only while the end record is on the screen. */
const char *end_problem_of(const State *s, const char *dev, int *red);
/* #1918 (screens_ask.c): the power button pressed during a write -- over
 * every screen while power_ask_open(); the write goes on until an answer. */
int power_ask_open(const App *a);
void draw_power_dialog(App *a, cairo_t *cr, double W, double H);
/* #1270 (screens.c): the confirm dialog of a->confirm, modal like the shrink
 * question -- it replaces the hit table with [ביטול] and the red action. */
void draw_confirm_dialog(App *a, cairo_t *cr, double W, double H);

/* #649 (screens_tools.c): the tools / tool-result files next to --state.
 * tools_poll re-reads what changed; returns 1 when the scene must redraw.
 * tools_parse fills a->tools from text (the same parser, testable by
 * --png with a state file). tools_risk_level: 0 ro, 1 rw, 2 destroy. */
int tools_poll(App *a);
void tools_parse(App *a, const char *text);
int tools_risk_level(const char *risk);
void tools_sample(App *a);
void tool_output_set(App *a, const char *text);

/* #1269 (screens_drivers.c): drivers per target disk on the room / direct
 * setup screen; the drawing half is in screens_rounds.h (it needs Pango,
 * and ui.h is also built without it -- the installer tests).
 * room_drv_active: the switch is on AND the library has models.
 * room_drv_reset: the switch off and no choice -- on every entry.
 * room_drv_after_reload: the chosen model stays one the library has.
 * room_drv_choose: row i of the model list; room_drv_tag_click: a disk's tag.
 * room_drv_encode: "<mac>|<port>|<vendor>|<model>|..." for each chosen disk
 * with a model; returns how many, or -1 with the reason in st.room_error. */
int room_drv_active(const App *a);
void room_drv_reset(App *a);
void room_drv_after_reload(App *a);
void room_drv_choose(App *a, int i);
void room_drv_tag_click(App *a, int mi, int port);
int room_drv_encode(App *a, char *out, size_t n);

#endif
