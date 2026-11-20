/* #2848 (Nadav 11/11): the one rule -- a disk's colour, and whether it may be a SOURCE and a TARGET.
 * #3666 (Nadav 18/11, epic #3653): the GUI does NOT work it out. The table is one file, agent/lib/data/disk-rule.tsv:
 * the server's brain (server/brain/diskcolor.py, K3/DISK_COLOR) applies it and sends the verdict, and a machine with no
 * server (the local clone, the cloner's waiting screen) gets the same verdict from the agent (agent/lib/smart/diskrule.sh,
 * which reads the same file). This file only takes a verdict that was delivered -- colour, the two permissions, the reason
 * word and the sentence -- and holds it for the screens. There is no table here, no sentence, and no "SMART ok, so green":
 * a disk nobody classified is "not checked" (grey), never green (principle 5).
 *
 *   SMART ok                               green   source yes  target yes
 *   SMART half-good (warn)                 orange  source yes  target yes
 *   failed the last clone / cut write      yellow  source NO   target yes
 *   SMART failed                           red     source yes  target NO
 *   slow in THIS round's write check       red     source yes  target NO
 *   a SATA problem (link reset / gone)     red     source yes  target NO
 *     (#3591, Nadav 17/11: no link SPEED -- 1.5, 3.0, 6.0 -- is a colour or a refusal; it is information)
 *
 * The two permissions are apart: red AND yellow is neither, and is drawn red. Pure C (no cairo): a test builds this file alone. */
#ifndef DISK_RULE_H
#define DISK_RULE_H

enum { DR_GREY, DR_GREEN, DR_ORANGE, DR_YELLOW, DR_RED };

typedef struct {
    int set;                 /* 1 = a verdict was delivered; 0 = none: "not checked" -- grey, and nothing is said about it */
    int color, source, target;
    char reason[24];         /* the colour's reason word (red or yellow); "" = none */
    char he[200];            /* its sentence -- the table's own, shown as it came */
} DiskRule;

/* The verdict as delivered: colour word, "0"/"1" for source and target, the reason word and its sentence. 1 = taken;
 * 0 = junk (an unknown colour, a permission that is not 0 or 1): *r is "no verdict", never a guess. */
int disk_rule_take(DiskRule *r, const char *color, const char *source, const char *target, const char *reason, const char *he);
/* No verdict: grey, allowed as the table's own "not checked" row allows (the server and the agent are the gate), set = 0. */
DiskRule disk_rule_none(void);
const char *disk_rule_color(int color);          /* "red" "yellow" "orange" "green" "grey" */

#endif
