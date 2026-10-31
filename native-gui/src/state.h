/* The --state file: what station.js gets from /api/v1/agent/state,
 * /api/console/room and /api/v1/agent/sessions/active, written by the
 * agent as plain key=value lines (README "The --state file"). Re-read
 * whenever its mtime or size changes -- the agent's `printf > tmp && mv`
 * makes that atomic -- at the 2 s cadence of setInterval(poll, 2000). */
#ifndef IMAGESRV_STATE_H
#define IMAGESRV_STATE_H

#include <stdio.h>
#include <sys/types.h>
#include "ui.h"

typedef struct {
    char path[512];
    time_t mtime; off_t size;
    int loaded, complained;
} StateFile;

/* 1 = the file changed and *s was replaced; 0 = unchanged; -1 = unreadable
 * (said once on stderr; *s keeps its last contents -- an old state is not
 * silently turned into an empty one). */
int state_poll(StateFile *f, State *s);

/* Parse the text of a state file into *s, which is reset first. */
void state_parse(State *s, FILE *fp);

#endif
