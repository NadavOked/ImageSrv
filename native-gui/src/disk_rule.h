/* #2848 (Nadav 11/11): the one rule -- a disk's colour, and whether it may be a SOURCE and a TARGET.
 * The same table as server/disks/disk_rule.py and diskRule() in server/static/console.js;
 * tests/test_disk_rule_parity_2848.py runs the three on the same disks and wants the same answer,
 * the words included. Pure C (no cairo): the test builds this file alone.
 *
 *   SMART ok                               green   source yes  target yes
 *   SMART half-good (warn)                 orange  source yes  target yes
 *   failed the last clone / cut write      yellow  source NO   target yes
 *   SMART failed                           red     source yes  target NO
 *   slow in THIS round's write check       red     source yes  target NO
 *   slow SATA link / a SATA problem        red     source yes  target NO
 *
 * The two permissions are apart: red AND yellow is neither, and is drawn red. */
#ifndef DISK_RULE_H
#define DISK_RULE_H

typedef struct {
    const char *smart;      /* ok|warn|fail|failed_last|unchecked; NULL = unchecked */
    int failed_last;        /* the server's open failure record (the hello's disk_failures) */
    int write_incomplete;   /* #1798: write_mark incomplete */
    int link_slow;          /* the SATA link below its port */
    int sata_issue;         /* link_reset / drawer_gone (hwissues.sh) */
    int write_slow;         /* this round's write check said slow -- never a past round's */
} DiskFacts;

enum { DR_GREY, DR_GREEN, DR_ORANGE, DR_YELLOW, DR_RED };

typedef struct {
    int color, source, target;
    const char *reason;          /* the colour's reason (red or yellow); NULL = none */
    const char *source_reason;   /* why not a source; NULL = allowed */
    const char *target_reason;   /* why not a target; NULL = allowed */
} DiskRule;

DiskRule disk_rule(const DiskFacts *f);
const char *disk_rule_he(const char *reason);    /* the sentence; "" for NULL or unknown */
const char *disk_rule_color(int color);          /* "red" "yellow" "orange" "green" "grey" */

#endif
