#include <errno.h>
#include <limits.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include "state.h"

int lc_state_line(State *s, const char *key, char *val);   /* #1322, lc_state.c */

static char EMPTY[1] = "";

/* "a|b||d" -> f[0..3], missing fields are "" -- never NULL. */
static int split(char *v, char *f[], int max) {
    int n = 0;
    f[n++] = v;
    for (char *p = v; *p && n < max; p++)
        if (*p == '|') { *p = 0; f[n++] = p + 1; }
    for (int i = n; i < max; i++) f[i] = EMPTY;
    return n;
}

static void cp(char *dst, size_t n, const char *src) { snprintf(dst, n, "%s", src); }
/* #2024: cp, and a cut never leaves half a UTF-8 character (it drew as garbage) */
static void cp_utf8(char *dst, size_t n, const char *src) {
    cp(dst, n, src);
    size_t len = strlen(dst);
    if (len == strlen(src)) return;
    while (len && ((unsigned char)dst[len - 1] & 0xC0) == 0x80) len--;
    if (len && ((unsigned char)dst[len - 1] & 0xC0) == 0xC0) len--;
    dst[len] = 0;
}
static int  num(const char *s, int dflt) { return s[0] ? atoi(s) : dflt; }

/* #1331: a drawer's drivers outcome -- only the words the agent writes; anything
 * else is "did not run", never drawn as staged. g = state, detail, boot-state,
 * boot-service, boot-error; the boot part only with one of its four words. */
static int drv_word(const char *s) { return !strcmp(s, "staged") || !strcmp(s, "skipped") || !strcmp(s, "failed"); }
static void drv_boot_fields(char bo[12], char sv[24], char er[160], char *const *g) {
    if (strcmp(g[0], "enabled") && strcmp(g[0], "already") && strcmp(g[0], "skipped") && strcmp(g[0], "failed")) return;
    cp(bo, 12, g[0]); cp(sv, 24, g[1]); cp(er, 160, g[2]);
}

/* #1331 T8: drivers.linux -- only its five words; anything else is "not reported" */
static void drv_linux_fields(char st[12], char mod[12], char det[160], char *const *g) {
    static const char *const w[] = { "generic", "hostonly", "unsupported", "failed", "skipped" };
    for (size_t i = 0; i < sizeof w / sizeof *w; i++)
        if (!strcmp(g[0], w[i])) { cp(st, 12, g[0]); cp(mod, 12, g[1]); cp(det, 160, g[2]); return; }
}

static void apply_tz(const char *tz) {
    static char applied[65];
    const char *value = tz[0] ? tz : "UTC0";
    if (!strcmp(applied, value)) return;
#ifdef _WIN32
    if (_putenv_s("TZ", value) != 0) return;
    _tzset();
#else
    if (setenv("TZ", value, 1) != 0) return;
    tzset();
#endif
    cp(applied, sizeof applied, value);
}

/* A strict non-negative integer: the whole field, digits only, no sign, no
 * leading space, no trailing junk, in range. Returns 0 on any of those so a
 * corrupt state record is rejected rather than folded to a wrong 0/huge value
 * (atoi/strtoull silently accept " -1", "12junk"). */
static int parse_uint(const char *s, unsigned long long *out) {
    if (s[0] < '0' || s[0] > '9') return 0;          /* empty, sign or space */
    errno = 0;
    char *end;
    unsigned long long v = strtoull(s, &end, 10);
    if (errno != 0 || *end != '\0') return 0;
    *out = v;
    return 1;
}

/* Mapping fields distinguish an absent legacy value (0) from an explicit
 * invalid/not-mapped value (-1). */
static int parse_drawer(const char *s) {
    unsigned long long v;
    if (!s[0]) return 0;
    return parse_uint(s, &v) && v >= 1 && v <= MAX_DRAWERS ? (int)v : -1;
}

/* #695: room-grid helpers. */
static Machine *machine_at(State *s, unsigned long long index) {
    if (index >= (unsigned long long)s->nmachines) return NULL;
    return &s->machines[index];
}

/* A legacy agent may send null port as 0. Give it the first unused physical
 * slot so it stays visible and deterministic, without pretending the server
 * identified a SATA port. */
static int room_fallback_port(const Machine *m) {
    for (int port = 1; port <= m->drawer_count; port++) {
        int used = 0;
        for (int i = 0; i < m->nroom_drawers; i++)
            if (m->room_drawers[i].port == port) { used = 1; break; }
        if (!used) return port;
    }
    return 0;
}

void state_parse(State *s, FILE *fp) {
    memset(s, 0, sizeof *s);
    s->pct = -1;                                  /* no total known until said */
    s->elapsed_s = s->eta_s = -1; s->rate_bps = -1;   /* #410: unmeasured until said */
    s->hello_age = -1;
    s->hello_rc = -1;
    s->driver_models_read = -1;                   /* #1269: not read until said */
    int reported_long = 0, reported_disks = 0, reported_machines = 0;
    /* #928: the shrink question is all-or-nothing -- the declared count and
     * a bad row are remembered here and settled after the last line. */
    unsigned long long shrink_declared = 0; int shrink_seen = 0, shrink_bad = 0;
    /* 11/10: the agent's question dialog is all-or-nothing the same way. */
    unsigned long long ask_declared = 0; int ask_seen = 0, ask_bad = 0;
    struct { char dev[32], conn[32]; int drawer, known; } disk_slots[MAX_DISKS];
    int ndisk_slots = 0;
    memset(disk_slots, 0, sizeof disk_slots);
    char line[4096];
    while (fgets(line, sizeof line, fp)) {
        size_t len = strlen(line);
        if (len == 0 || line[len - 1] != '\n') {
            /* Reject the whole physical line. Keeping the prefix can end in
             * half a UTF-8 character, and the remainder would otherwise be
             * parsed as a second bogus key (R44 finding 1). A final line with
             * no newline is rejected by the same complete-record rule. */
            int ch;
            while ((ch = fgetc(fp)) != EOF && ch != '\n') {}
            s->rejected_lines++;
            if (!reported_long) {
                fprintf(stderr, "native-gui: state: unterminated or overlong line rejected\n");
                reported_long = 1;
            }
            continue;
        }
        line[--len] = 0;
        if (len && line[len - 1] == '\r') line[len - 1] = 0;
        if (!line[0] || line[0] == '#') continue;
        char *eq = strchr(line, '=');
        if (!eq) continue;
        *eq = 0;
        const char *key = line;
        char *val = eq + 1, *f[27];   /* #1386 + #1331 + #1742: room_drawer= has up to 27 fields (split writes f[0..max-1]) */
        if (lc_state_line(s, key, val)) continue;   /* #1322: lc_* records */
#define KEY(k) (strcmp(key, k) == 0)
        if (KEY("disk")) {
            if (s->ndisks >= MAX_DISKS) {
                s->hidden_disks++;
                if (!reported_disks) {
                    fprintf(stderr, "native-gui: state: too many disks (max %d); extra rows hidden\n", MAX_DISKS);
                    reported_disks = 1;
                }
                continue;
            }
            split(val, f, 5);
            Disk *d = &s->disks[s->ndisks++];
            cp(d->dev, sizeof d->dev, f[0]); cp(d->model, sizeof d->model, f[1]);
            d->size_bytes = strtoull(f[2], NULL, 10);
            d->has_data = num(f[3], 0); d->removable = num(f[4], 0);
        } else if (KEY("dsmart")) {
            /* 16/10: dsmart=dev|verdict|reason|serial|port -- the SMART of one
             * of this machine's disk= records (guistate.sh, from the agent's
             * own cache). Only fail/warn/failed_last are a warning; anything
             * else -- ok, unchecked, a word this build does not know -- is
             * kept as it came and asks nothing (smart_level 0). */
            split(val, f, 5);
            for (int i = 0; i < s->ndisks; i++) {
                if (strcmp(s->disks[i].dev, f[0])) continue;
                cp(s->disks[i].smart, sizeof s->disks[i].smart, f[1]);
                cp(s->disks[i].smart_reason, sizeof s->disks[i].smart_reason, f[2]);
                cp(s->disks[i].serial, sizeof s->disks[i].serial, f[3]);
                s->disks[i].port = num(f[4], 0);
            }
        } else if (KEY("disk_slot")) {
            if (split(val, f, 3) < 2 || !f[0][0]) continue;
            int at = -1;
            for (int i = 0; i < ndisk_slots; i++) if (!strcmp(disk_slots[i].dev, f[0])) { at = i; break; }
            if (at < 0) {
                if (ndisk_slots >= MAX_DISKS) continue;
                at = ndisk_slots++;
            }
            cp(disk_slots[at].dev, sizeof disk_slots[at].dev, f[0]);
            disk_slots[at].drawer = parse_drawer(f[1]);
            cp(disk_slots[at].conn, sizeof disk_slots[at].conn, f[2]);
            disk_slots[at].known = 1;
        } else if (KEY("folder") && s->nfolders < MAX_FOLDERS) {
            cp(s->folders[s->nfolders++], sizeof s->folders[0], val);
        } else if ((KEY("image") || KEY("image_unchecked")) && s->nimages < MAX_IMAGES) {
            split(val, f, 3);
            Image *im = &s->images[s->nimages++];
            memset(im, 0, sizeof *im);
            cp(im->id, sizeof im->id, f[0]); cp(im->name, sizeof im->name, f[1]); cp(im->folder, sizeof im->folder, f[2]);
            im->unchecked = KEY("image_unchecked");   /* #1896: "לא נבדק" until C: is marked */
        } else if (KEY("image_cand")) {
            /* #1896: <image id>|<idx>|<size bytes>|<used bytes or empty> -- after its image_unchecked= */
            unsigned long long idx, size, used = 0;
            if (split(val, f, 5) != 4 || !parse_uint(f[1], &idx) || idx < 1 || idx > INT_MAX ||
                !parse_uint(f[2], &size) || (f[3][0] && !parse_uint(f[3], &used))) continue;
            for (int i = 0; i < s->nimages; i++) {
                Image *im = &s->images[i];
                if (!im->unchecked || strcmp(im->id, f[0])) continue;
                if (im->ncand < MAX_SYSP_CANDS) {
                    im->cand[im->ncand].idx = (int)idx; im->cand[im->ncand].size = size; im->cand[im->ncand].used = used;
                }
                im->ncand++;   /* past the max: counted, and the picker sends to the console */
                break;
            }
        } else if (KEY("machine")) {
            if (s->nmachines >= MAX_MACHINES) {
                s->hidden_machines++;
                if (!reported_machines) {
                    fprintf(stderr, "native-gui: state: too many machines (max %d); extra rows hidden\n", MAX_MACHINES);
                    reported_machines = 1;
                }
                continue;
            }
            split(val, f, 10);
            Machine *m = &s->machines[s->nmachines++];
            cp(m->name, sizeof m->name, f[0]); cp(m->mac, sizeof m->mac, f[1]);
            m->awake = num(f[2], 0); m->joined = num(f[3], 0); m->fresh_drawers = num(f[4], 0);
            cp(m->state, sizeof m->state, f[5]);
            m->pct = num(f[6], -1); m->moving = num(f[7], 0);
            cp(m->error, sizeof m->error, f[8]);
            m->local_copy = !strcmp(f[9], "1");
        } else if (KEY("machine_drawers")) {
            /* #695: machine-index|drawer_count (separate record so the 9-field
             * machine= format stays compatible). */
            if (split(val, f, 2) < 2) continue;
            unsigned long long mi, count;
            if (!parse_uint(f[0], &mi) || !parse_uint(f[1], &count)) continue;
            Machine *m = machine_at(s, mi);
            if (!m) continue;
            if (count < 1) count = 1;
            if (count > MAX_DRAWERS) count = MAX_DRAWERS;
            m->drawer_count = (int)count;
        } else if (KEY("room_drawer")) {
            /* #695: machine-index|port-or-0|serial|model|size|smart|fresh|selected
             * #1269: |drivers-state|drivers-detail -- optional (an older agent sends eight)
             * #704: |slot-name -- optional, "" = "דיסק N"
             * #432: |link-state -- optional; only "slow" colours the slot
             * #1366: |crc -- optional; the count when over the threshold, "" = not
             * 17/10: |pct|target-state -- this drawer's own progress in the wave
             * #1386: |dev|cancel-text|cancel-done; #1331: |boot-state|boot-service|boot-error; T8: |linux-state|module|error-or-reason
             * #1742: |smart-reason -- why red ("readonly"), only when there is one (24-26 then empty) */
            int nf = split(val, f, 27);
            if (nf < 8) continue;
            unsigned long long mi, port, size;
            if (!parse_uint(f[0], &mi) ||
                !parse_uint(f[1], &port) || port > 32 ||
                !parse_uint(f[4], &size))
                continue;
            Machine *m = machine_at(s, mi);
            if (!m || m->nroom_drawers >= MAX_DRAWERS) continue;
            if (m->drawer_count < 1) m->drawer_count = MAX_DRAWERS;
            int mapped_record = nf >= 14 && f[13][0];
            if (!mapped_record && port > MAX_DRAWERS) continue;
            int actual_port = (int)port;
            if (!mapped_record && actual_port == 0) actual_port = room_fallback_port(m);
            if (!mapped_record && (actual_port < 1 || actual_port > m->drawer_count)) continue;
            RoomDrawer *d = &m->room_drawers[m->nroom_drawers++];
            d->port = actual_port;
            d->present = 1;
            cp(d->serial, sizeof d->serial, f[2]);
            cp(d->model, sizeof d->model, f[3]);
            d->size_bytes = size;
            cp(d->smart, sizeof d->smart, f[5]);
            /* #1235: -1 = no serial (#105, fresh null): "cannot identify",
             * which is neither ready nor written. */
            d->fresh = num(f[6], 0) == 1;
            d->unidentified = num(f[6], 0) < 0;
            d->selected = num(f[7], 0) != 0;
            /* #1269: only the three words the server keeps (well_formed_drivers);
             * anything else is "did not run", never drawn as staged. */
            if (!strcmp(f[8], "staged") || !strcmp(f[8], "skipped") || !strcmp(f[8], "failed")) {
                cp(d->drv_state, sizeof d->drv_state, f[8]);
                cp(d->drv_detail, sizeof d->drv_detail, f[9]);
            }
            /* #704: the drawer's own name (by its slot; NVMe by serial) wins
             * over a room_slot_name= for the same port, in any order. */
            cp(d->slot_name, sizeof d->slot_name, f[10]);
            if (f[10][0] && actual_port >= 1 && actual_port <= MAX_DRAWERS)
                cp(m->slot_names[actual_port - 1], sizeof m->slot_names[0], f[10]);
            /* A port-0 drawer only borrows the slot it is drawn in: that SATA
             * slot's name is not its name. */
            else if (!mapped_record && port == 0) m->slot_names[actual_port - 1][0] = 0;
            d->fallback = mapped_record ? 0 : port == 0;
            d->link_slow = !strcmp(f[11], "slow");
            unsigned long long crc;
            d->crc_suspect = parse_uint(f[12], &crc) && crc > 0 && crc < 100000000ULL ? (long)crc : 0;
            d->drawer = mapped_record ? parse_drawer(f[13]) : 0;
            if (mapped_record) cp(d->conn, sizeof d->conn, f[14]);
            /* 17/10: the drawer's own target. Absent (an older agent) = the
             * machine's progress, as before; present = only its own. */
            d->own_progress = nf >= 17;
            d->pct = nf >= 17 ? num(f[15], -1) : -1;
            if (nf >= 17) cp(d->tstate, sizeof d->tstate, f[16]);
            /* #1386: |dev|cancel-text|cancel-done -- optional (an older agent sends 17) */
            if (nf >= 18) cp(d->dev, sizeof d->dev, f[17]);
            if (nf >= 20) { cp(d->cancel_text, sizeof d->cancel_text, f[18]); d->cancel_done = num(f[19], 0) == 1; }
            /* #1331: fields 21-23 -- drivers.boot, only next to a known drivers state */
            if (nf >= 23 && d->drv_state[0]) drv_boot_fields(d->drv_boot, d->drv_bsvc, d->drv_berr, f + 20);
            if (nf >= 26 && d->drv_state[0]) drv_linux_fields(d->drv_lstate, d->drv_lmod, d->drv_ldet, f + 23);   /* #1331 T8 */
            if (nf >= 27) cp(d->smart_reason, sizeof d->smart_reason, f[26]);   /* #1742 */
        } else if (KEY("room_warn")) {
            /* #432: one line of round.precheck; an empty one is not a warning */
            if (!val[0]) continue;
            if (s->nroom_warn >= MAX_ROOM_NOTES) { s->hidden_room_warn++; continue; }
            cp(s->room_warn[s->nroom_warn++], sizeof s->room_warn[0], val);
        } else if (KEY("room_label")) {
            cp(s->room_label, sizeof s->room_label, val);
        } else if (KEY("room_missing")) {
            /* #65/#1367: mac|name|reason|can_poweroff|text|local_copy.
             * Five fields remain valid for an older agent and mean no copy. */
            int nf = split(val, f, 6);
            if ((nf != 5 && nf != 6) || !f[0][0] || !f[4][0]) continue;
            if (s->nroom_missing >= MAX_ROOM_NOTES) { s->hidden_room_missing++; continue; }
            int i = s->nroom_missing++;
            cp(s->room_missing[i].mac, sizeof s->room_missing[i].mac, f[0]);
            cp(s->room_missing[i].name, sizeof s->room_missing[i].name, f[1]);
            cp(s->room_missing[i].reason, sizeof s->room_missing[i].reason, f[2]);
            s->room_missing[i].can_poweroff = !strcmp(f[3], "1");
            cp(s->room_missing[i].text, sizeof s->room_missing[i].text, f[4]);
            s->room_missing[i].local_copy = nf == 6 && !strcmp(f[5], "1");
        } else if (KEY("room_slot_name")) {
            /* #704: machine-index|port|name -- a SATA slot's name, empty slots too */
            if (split(val, f, 3) != 3 || !f[2][0]) continue;
            unsigned long long mi, port;
            if (!parse_uint(f[0], &mi) || !parse_uint(f[1], &port) ||
                port < 1 || port > MAX_DRAWERS)
                continue;
            Machine *m = machine_at(s, mi);
            if (!m || m->slot_names[port - 1][0]) continue;
            int borrowed = 0;                  /* a port-0 drawer drawn in this slot */
            for (int i = 0; i < m->nroom_drawers; i++)
                if (m->room_drawers[i].fallback && m->room_drawers[i].port == (int)port) borrowed = 1;
            if (borrowed) continue;
            cp(m->slot_names[port - 1], sizeof m->slot_names[0], f[2]);
        } else if (KEY("room_slot")) {
            if (split(val, f, 4) < 3) continue;
            unsigned long long mi, slot;
            if (!parse_uint(f[0], &mi) || !parse_uint(f[1], &slot) ||
                slot < 1 || slot > MAX_DRAWERS) continue;
            Machine *m = machine_at(s, mi);
            if (!m) continue;
            RoomSlot *rs = &m->rslots[slot - 1];
            cp(rs->conn, sizeof rs->conn, f[2]);
            cp(rs->name, sizeof rs->name, f[3]);
            rs->set = 1;
            if ((int)slot > m->nrslots) m->nrslots = (int)slot;
        } else if (KEY("cslot_name")) {
            /* #704: port|name -- the cloner's own slot names (slotnames.sh) */
            if (split(val, f, 2) != 2 || !f[1][0]) continue;
            unsigned long long port;
            if (!parse_uint(f[0], &port) || port < 1 || port > MAX_DRAWERS) continue;
            cp(s->cslot_names[port - 1], sizeof s->cslot_names[0], f[1]);
        } else if (KEY("cdrawer_slot")) {
            if (split(val, f, 3) < 2) continue;
            unsigned long long bay;
            if (!parse_uint(f[0], &bay) || bay < 1 || bay > MAX_DRAWERS) continue;
            ClonerBay *cb = &s->cbays[bay - 1];
            cp(cb->conn, sizeof cb->conn, f[1]);
            cp(cb->name, sizeof cb->name, f[2]);
            cb->set = 1;
            if ((int)bay > s->ncbays) s->ncbays = (int)bay;
        } else if (KEY("coptical") && s->ncopticals < MAX_DRAWERS) {
            if (split(val, f, 3) < 1) continue;
            COptical *o = &s->copticals[s->ncopticals++];
            o->drawer = parse_drawer(f[0]);
            cp(o->conn, sizeof o->conn, f[1]);
            cp(o->model, sizeof o->model, f[2]);
        } else if (KEY("driver_models")) {
            /* #1269: how many models the bridge read; -1 = it could not read them */
            if (!strcmp(val, "-1")) { s->driver_models_read = -1; continue; }
            unsigned long long n;
            if (parse_uint(val, &n) && n <= INT_MAX) s->driver_models_read = (int)n;
        } else if (KEY("driver_model")) {
            /* #1269: vendor|model -- both needed, or the server could not match it */
            if (split(val, f, 3) != 2 || !f[0][0] || !f[1][0]) continue;
            if (s->ndriver_models >= MAX_DRIVER_MODELS) { s->hidden_models++; continue; }
            DriverModel *dm = &s->driver_models[s->ndriver_models++];
            cp(dm->vendor, sizeof dm->vendor, f[0]);
            cp(dm->model, sizeof dm->model, f[1]);
        } else if (KEY("drawer") && s->ndrawers < MAX_DRAWERS) {
            int nf = split(val, f, 20);
            if (nf < 5) continue;                     /* malformed -- no phantom drawer */
            unsigned long long port, bytes, total;
            if (!parse_uint(f[0], &port) || port > INT_MAX) continue;  /* reject, don't fold to 0 */
            if (!parse_uint(f[3], &bytes)) continue;
            if (!parse_uint(f[4], &total)) continue;
            Drawer *d = &s->drawers[s->ndrawers++];
            d->port = (int)port;                      /* 0 = no SATA slot derived */
            cp(d->dev, sizeof d->dev, f[1]);
            cp(d->state, sizeof d->state, f[2]);
            d->bytes = bytes;
            d->total = total;
            cp(d->error, sizeof d->error, f[5]);
            /* #410: fields 7-8 are optional (an older agent sends six);
             * -1 / absent = not measured. */
            d->rate_bps = nf >= 8 && f[6][0] ? strtoll(f[6], NULL, 10) : -1;
            d->eta_s = nf >= 8 ? num(f[7], -1) : -1;
            d->drawer = nf >= 9 ? parse_drawer(f[8]) : 0;
            if (nf >= 10) cp(d->conn, sizeof d->conn, f[9]);
            /* #1386: fields 11-12 -- who asked to cancel ("" = nobody), when it was done */
            if (nf >= 11) cp(d->cancel, sizeof d->cancel, f[10]);
            d->cancel_at = nf >= 12 && f[11][0] ? strtoll(f[11], NULL, 10) : 0;
            /* #1331: fields 13-17 -- the drawer's drivers (drvgui.sh); absent = did not run */
            if (nf >= 17 && drv_word(f[12])) {
                cp(d->drv_state, sizeof d->drv_state, f[12]); cp(d->drv_detail, sizeof d->drv_detail, f[13]);
                drv_boot_fields(d->drv_boot, d->drv_bsvc, d->drv_berr, f + 14);
                if (nf >= 20) drv_linux_fields(d->drv_lstate, d->drv_lmod, d->drv_ldet, f + 17);   /* #1331 T8: fields 18-20 */
            }
        } else if (KEY("drawer_moved")) {
            /* #2055: drawer_moved=dev|epoch -- the bytes of that drawer last moved then (clonergui.sh) */
            if (split(val, f, 2) == 2 && f[1][0])
                for (int i = 0; i < s->ndrawers; i++)
                    if (!strcmp(s->drawers[i].dev, f[0])) s->drawers[i].moved = strtoll(f[1], NULL, 10);
        } else if (KEY("drawer_rb")) {
            /* #1907: drawer_rb=dev|list_not_fetched -- that drawer is done and not
             * verified because the check list did not come down (clonergui.sh) */
            if (split(val, f, 2) == 2 && !strcmp(f[1], "list_not_fetched"))
                for (int i = 0; i < s->ndrawers; i++)
                    if (!strcmp(s->drawers[i].dev, f[0])) s->drawers[i].rb_nolist = 1;
        } else if (KEY("cdisk") && s->nidisks < MAX_DRAWERS) {
            /* cdisk=port|dev|model|size|smart|cause -- the cloner's idle
             * inventory. NOT "disk=" -- that key already holds the build
             * machine's local disk list (s->disks) and would swallow these
             * first (#688). The 5th field (#709) and the 6th (#874: the
             * server's cause for failed_last) are optional so an older agent
             * still parses. */
            int nf = split(val, f, 13);   /* #1495: drawer and connection are fields 10/11; #1798: the mark 12; #1921: hw 13 */
            if (nf < 4) continue;
            unsigned long long port, size;
            if (!parse_uint(f[0], &port) || port > INT_MAX) continue;
            if (!parse_uint(f[3], &size)) continue;
            IdleDisk *id = &s->idisks[s->nidisks++];
            id->port = (int)port;
            cp(id->dev, sizeof id->dev, f[1]);
            cp(id->model, sizeof id->model, f[2]);
            id->size = size;
            /* Only warn/fail/failed_last read as unhealthy; anything else --
             * passed, an absent field, an unknown string -- is treated as
             * unchecked. failed_last (#867/#874): the server remembers the
             * disk (serial) or the slot (machine+port) failed the previous
             * clone; the 6th field says why, as the server classified it. */
            const char *sm = (nf >= 5 && f[4][0]) ? f[4] : "unchecked";
            if (strcmp(sm, "passed") && strcmp(sm, "warn") && strcmp(sm, "fail")
                && strcmp(sm, "failed_last"))
                sm = "unchecked";
            cp(id->smart, sizeof id->smart, sm);
            /* #1366: on fail the 6th field is the SMART reason of the F1 stop */
            const char *cause = (nf >= 6 && (!strcmp(sm, "failed_last") || !strcmp(sm, "fail") || !strcmp(sm, "warn"))) ? f[5] : "";
            if (!strcmp(sm, "warn")) { if (strspn(cause, "abcdefghijklmnopqrstuvwxyz_") != strlen(cause)) cause = ""; }   /* #1859: a reason word (smart.sh), said by smart_reason_text */
            else if (!strcmp(sm, "failed_last") ? strcmp(cause, "cable") && strcmp(cause, "disk") && strcmp(cause, "readonly")   /* #1742 */
                    : strcmp(cause, "health_failed") && strcmp(cause, "pending_sectors") && strcmp(cause, "uncorrectable")
                      && strcmp(cause, "readonly") && strcmp(cause, "media_readonly") && strcmp(cause, "health_unread"))   /* #1742: read-only; #1921: NVMe read-only; #2078 */
                cause = "";
            cp(id->cause, sizeof id->cause, cause);
            /* #432: fields 7/8 (link, port max), "6.0"-shaped or not read --
             * "null", an older agent's absent field and junk all mean unknown */
            for (int k = 0; k < 2; k++) {
                const char *v = nf >= 7 + k ? f[6 + k] : "";
                int ok = strlen(v) == 3 && v[0] >= '0' && v[0] <= '9' && v[1] == '.' && v[2] >= '0' && v[2] <= '9';
                cp(k ? id->link_max : id->link, sizeof id->link, ok ? v : "");
            }
            unsigned long long crc;
            id->crc_suspect = nf >= 9 && parse_uint(f[8], &crc) && crc > 0 && crc < 100000000ULL ? (long)crc : 0;
            id->drawer = nf >= 10 ? parse_drawer(f[9]) : 0;
            if (nf >= 11) cp(id->conn, sizeof id->conn, f[10]);
            /* #1798: the incomplete-write mark on the disk (writemark.sh). Only the two
             * words that say something are kept; clean or absent draws nothing. */
            if (nf >= 12 && (!strcmp(f[11], "incomplete") || !strcmp(f[11], "unchecked")))
                cp(id->mark, sizeof id->mark, f[11]);
            if (nf >= 13) cp(id->hw, sizeof id->hw, f[12]);   /* #1921: said as it is; "" = no note */
        } else if (KEY("cused") && s->ncuseds < MAX_DRAWERS + 4) {
            /* #2030: cused=dev|size|used -- used -1 (or anything unreadable) = not measured */
            if (split(val, f, 3) < 3) continue;
            unsigned long long size, used;
            if (!f[0][0] || !parse_uint(f[1], &size)) continue;
            CUsed *cu = &s->cuseds[s->ncuseds++];
            cp(cu->dev, sizeof cu->dev, f[0]);
            cu->size = size;
            cu->used = parse_uint(f[2], &used) && used <= (unsigned long long)LLONG_MAX ? (long long)used : -1;
        } else if (KEY("disk_probe")) {
            if (!strcmp(val, "drives") || !strcmp(val, "no_disks") ||
                !strcmp(val, "no_ports") || !strcmp(val, "unchecked"))
                cp(s->disk_probe, sizeof s->disk_probe, val);
            else
                cp(s->disk_probe, sizeof s->disk_probe, "unchecked");
        } else if (KEY("cloner")) {
            cp(s->cloner_image, sizeof s->cloner_image, val);
        } else if (KEY("drawer_count")) {
            unsigned long long count;
            if (parse_uint(val, &count) && count >= 1 && count <= MAX_DRAWERS)
                s->drawer_count = (int)count;
        } else if (KEY("server_restarted_wave")) {
            s->server_restarted_wave = !strcmp(val, "1");
        } else if (KEY("smart_prompt")) {
            if (split(val, f, 6) < 4) continue;       /* nonce|port|verdict|reason[|model|serial] (16/10) */
            unsigned long long port;
            if (!parse_uint(f[1], &port) || port > INT_MAX) continue;
            s->smart_pending = 1;
            cp(s->smart_nonce, sizeof s->smart_nonce, f[0]);   /* string, echoed back */
            s->smart_port = (int)port;
            cp(s->smart_verdict, sizeof s->smart_verdict, f[2]);
            cp(s->smart_reason, sizeof s->smart_reason, f[3]);
            cp(s->smart_model, sizeof s->smart_model, f[4]);
            cp(s->smart_serial, sizeof s->smart_serial, f[5]);
        } else if (KEY("shrink_prompt")) {
            /* nonce|count. The nonce is echoed back on the answer, so it is
             * kept only in the shape the bridge accepts: digits and '-'. */
            unsigned long long n;
            int nf = split(val, f, 3);   /* #1171: a third field, "direct", or none */
            if (shrink_seen++ || (nf != 2 && !(nf == 3 && !strcmp(f[2], "direct"))) || !f[0][0] ||
                strspn(f[0], "0123456789-") != strlen(f[0]) ||
                strlen(f[0]) >= sizeof s->shrink_nonce ||
                !parse_uint(f[1], &n) || n < 1 || n > MAX_SHRINK_PARTS) { shrink_bad = 1; continue; }
            cp(s->shrink_nonce, sizeof s->shrink_nonce, f[0]);
            shrink_declared = n; s->shrink_direct = nf == 3;
        } else if (KEY("shrink_partition")) {
            /* idx|old bytes|new bytes -- a shrink makes a partition smaller */
            unsigned long long idx, old_b, new_b;
            if (split(val, f, 4) != 3 || !parse_uint(f[0], &idx) || idx < 1 || idx > INT_MAX ||
                !parse_uint(f[1], &old_b) || !parse_uint(f[2], &new_b) ||
                new_b < 1 || new_b >= old_b || s->nshrink >= MAX_SHRINK_PARTS) { shrink_bad = 1; continue; }
            s->shrink[s->nshrink].idx = (int)idx;
            s->shrink[s->nshrink].old_bytes = old_b;
            s->shrink[s->nshrink].new_bytes = new_b;
            s->nshrink++;
        } else if (KEY("ask_prompt")) {
            /* nonce|kind|count|subject (11/10). The nonce is echoed back, so
             * only digits and '-'; the subject (a disk, or seconds) is drawn,
             * so only [A-Za-z0-9._/-]. growback has 1..MAX_ASK_ROWS rows,
             * capture-end none. */
            unsigned long long n;
            int kind = ASK_NONE;
            if (ask_seen++ || split(val, f, 5) != 4) { ask_bad = 1; continue; }
            if (!strcmp(f[1], "growback")) kind = ASK_GROWBACK;
            else if (!strcmp(f[1], "capture-end")) kind = ASK_CAPTURE_END;
            else if (!strcmp(f[1], "send-end")) kind = ASK_SEND_END;     /* #1560 */
            else if (!strcmp(f[1], "round-end")) kind = ASK_ROUND_END;   /* #1560 */
            else if (!strcmp(f[1], "raid")) kind = ASK_RAID;             /* #1808: exactly one row */
            else if (!strcmp(f[1], "share")) kind = ASK_SHARE;           /* #1898: 1..MAX_ASK_ROWS rows */
            else if (!strcmp(f[1], "badsect")) kind = ASK_BADSECT;       /* #2017: exactly one row */
            if (kind == ASK_NONE || !f[0][0] ||
                strspn(f[0], "0123456789-") != strlen(f[0]) ||
                strlen(f[0]) >= sizeof s->ask_nonce ||
                strspn(f[3], "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._/-") != strlen(f[3]) ||
                strlen(f[3]) >= sizeof s->ask_subject ||
                !parse_uint(f[2], &n) || n > MAX_ASK_ROWS ||
                ((kind == ASK_GROWBACK || kind == ASK_SHARE) && n < 1) || ((kind == ASK_RAID || kind == ASK_BADSECT) && n != 1) ||
                (kind != ASK_GROWBACK && kind != ASK_SHARE && kind != ASK_RAID && kind != ASK_BADSECT && n != 0)) { ask_bad = 1; continue; }
            cp(s->ask_nonce, sizeof s->ask_nonce, f[0]);
            cp(s->ask_subject, sizeof s->ask_subject, f[3]);
            s->ask_kind = kind;
            ask_declared = n;
        } else if (KEY("ask_row") && s->ask_kind == ASK_RAID) {
            /* #1808: port|metadata type -- one word, drawn by name */
            unsigned long long port;
            if (split(val, f, 3) != 2 || !parse_uint(f[0], &port) || port > INT_MAX || !f[1][0] ||
                strspn(f[1], "abcdefghijklmnopqrstuvwxyz_") != strlen(f[1]) || strlen(f[1]) >= sizeof s->ask[0].word ||
                s->nask >= MAX_ASK_ROWS) { ask_bad = 1; continue; }
            memset(&s->ask[s->nask], 0, sizeof s->ask[0]);
            s->ask[s->nask].port = (int)port;
            cp(s->ask[s->nask].word, sizeof s->ask[0].word, f[1]);
            s->nask++;
        } else if (KEY("ask_row") && s->ask_kind == ASK_BADSECT) {
            /* #2017: port|idx|epoch -- the source partition that did not read, and
               when the copy goes on by itself (Nadav 22/10); no epoch, no question */
            unsigned long long port, idx, until;
            if (split(val, f, 4) != 3 || !parse_uint(f[0], &port) || port > INT_MAX ||
                !parse_uint(f[1], &idx) || idx < 1 || idx > INT_MAX ||
                !parse_uint(f[2], &until) || until < 1 || until > LLONG_MAX || s->nask >= MAX_ASK_ROWS) { ask_bad = 1; continue; }
            memset(&s->ask[s->nask], 0, sizeof s->ask[0]);
            s->ask[s->nask].port = (int)port; s->ask[s->nask].idx = (int)idx; s->ask[s->nask].until = (long long)until;
            s->nask++;
        } else if (KEY("ask_row") && s->ask_kind == ASK_SHARE) {
            /* #1898: port|idx|now bytes|after bytes -- the split grows each one */
            unsigned long long port, idx, now_b, after_b;
            if (split(val, f, 5) != 4 || !parse_uint(f[0], &port) || port > INT_MAX ||
                !parse_uint(f[1], &idx) || idx < 1 || idx > INT_MAX || !parse_uint(f[2], &now_b) ||
                !parse_uint(f[3], &after_b) || now_b < 1 || after_b <= now_b || s->nask >= MAX_ASK_ROWS) { ask_bad = 1; continue; }
            memset(&s->ask[s->nask], 0, sizeof s->ask[0]);
            s->ask[s->nask].port = (int)port; s->ask[s->nask].idx = (int)idx;
            s->ask[s->nask].now_bytes = now_b; s->ask[s->nask].orig_bytes = after_b;
            s->nask++;
        } else if (KEY("ask_row")) {
            /* idx|now bytes|orig bytes|table|fs -- the grow-back makes a
             * partition (or its filesystem) bigger again: now < orig */
            unsigned long long idx, now_b, orig_b;
            if (split(val, f, 5) != 4 || !parse_uint(f[0], &idx) || idx < 1 || idx > INT_MAX ||
                !parse_uint(f[1], &now_b) || !parse_uint(f[2], &orig_b) ||
                now_b < 1 || now_b >= orig_b || s->nask >= MAX_ASK_ROWS ||
                (strcmp(f[3], "table") && strcmp(f[3], "fs"))) { ask_bad = 1; continue; }
            s->ask[s->nask].idx = (int)idx;
            s->ask[s->nask].now_bytes = now_b;
            s->ask[s->nask].orig_bytes = orig_b;
            s->ask[s->nask].fs_only = !strcmp(f[3], "fs");
            s->nask++;
        } else if (KEY("class") && s->nclasses < MAX_CLASSES) {
            split(val, f, 3);
            ClassGroup *g = &s->classes[s->nclasses++];
            cp(g->id, sizeof g->id, f[0]); cp(g->label, sizeof g->label, f[1]); g->machines = num(f[2], 0);
        } else if (KEY("task")) {
            s->task = !strcmp(val, "pending") ? TASK_PENDING : !strcmp(val, "running") ? TASK_RUNNING
                    : !strcmp(val, "done")    ? TASK_DONE    : !strcmp(val, "failed")  ? TASK_FAILED : TASK_NONE;
        } else if (KEY("task_direct")) s->task_direct = num(val, 0);   /* #715 */
        else if (KEY("task_name"))  cp(s->task_name, sizeof s->task_name, val);
        else if (KEY("task_disk"))    cp(s->task_disk, sizeof s->task_disk, val);
        else if (KEY("task_error"))   cp(s->task_error, sizeof s->task_error, val);
        else if (KEY("agent_version")) cp(s->agent_version, sizeof s->agent_version, val);
        else if (KEY("pct"))          s->pct = num(val, -1);
        else if (KEY("moving"))       s->moving = num(val, 0);
        else if (KEY("menu_class"))   s->menu_class = num(val, 0) != 0;
        else if (KEY("menu_tools"))   s->menu_tools = num(val, 0) != 0;   /* v1: off */
        else if (KEY("machine_name")) cp(s->machine_name, sizeof s->machine_name, val);   /* #1073 */
        else if (KEY("server_name")) cp(s->server_name, sizeof s->server_name, val);         /* #1252 */
        else if (KEY("server_addr")) cp(s->server_addr, sizeof s->server_addr, val);         /* #1252 */
        else if (KEY("server_version")) cp(s->server_version, sizeof s->server_version, val); /* #1252 */
        else if (KEY("tz")) {
            size_t n = strlen(val);
            if (n >= 1 && n <= 64 && strspn(val, "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789<>+-,./:") == n)
                cp(s->tz, sizeof s->tz, val);
        }
        else if (KEY("hello_age")) {
            unsigned long long v;
            if (parse_uint(val, &v) && v <= INT_MAX) s->hello_age = (int)v;
        }
        else if (KEY("hello_rc")) {
            unsigned long long v;
            if (parse_uint(val, &v) && v <= 999) s->hello_rc = (int)v;
        }
        else if (KEY("partition"))    s->partition = num(val, 0);
        else if (KEY("bytes"))        s->bytes = strtoull(val, NULL, 10);
        else if (KEY("title"))        cp(s->title, sizeof s->title, val);
        else if (KEY("sub"))          cp(s->sub, sizeof s->sub, val);
        else if (KEY("cancel"))       s->can_cancel = !strcmp(val, "1");   /* #1561 */
        else if (KEY("message")) {
            split(val, f, 2);
            cp(s->msg_title, sizeof s->msg_title, f[0]); cp(s->msg_sub, sizeof s->msg_sub, f[1]);
        } else if (KEY("round")) {
            split(val, f, 11);
            s->has_round = 1;
            cp(s->round_image, sizeof s->round_image, f[0]);
            s->wave_number = num(f[1], 0); s->wave_open = num(f[2], 0);
            s->written = num(f[3], 0); s->target = num(f[4], 0);
            s->ready = num(f[5], 0); s->remaining = num(f[6], 0);
            /* #410: an older agent sends seven fields -> "" -> -1 (unmeasured) */
            s->elapsed_s = num(f[7], -1);
            s->rate_bps = f[8][0] ? strtoll(f[8], NULL, 10) : -1;
            s->eta_s = num(f[9], -1);
            cp(s->wave_state, sizeof s->wave_state, f[10]);   /* 17/10; "" = an older agent */
        } else if (KEY("updated")) {
            s->updated = strtoll(val, NULL, 10);                 /* #410 */
        } else if (KEY("idle_poweroff_at")) {
            char *bar = NULL; s->idle_poweroff_at = strtoll(val, &bar, 10);        /* #434 */
            s->idle_poweroff_s = bar && *bar == '|' ? atoi(bar + 1) : 0;   /* mockup 10: an older agent sends none */
        } else if (KEY("end_poweroff")) {
            /* #1560: <epoch>|<kind>|<done>|<targets>|<held>; done/targets
             * may be empty (not said). All or nothing: a record this screen
             * cannot read draws no count. #2024: epoch 0 + held 1 is unlimited;
             * #2198: epoch 0 + held 0 is a successful local clone waiting by choice. */
            unsigned long long at = 0, dn = 0, tot = 0, held = 2; int kind = END_NONE;
            if (split(val, f, 6) == 5 && parse_uint(f[0], &at) && at < (1ULL << 40) &&
                (!f[2][0] || parse_uint(f[2], &dn)) && (!f[3][0] || parse_uint(f[3], &tot)) &&
                dn <= tot && tot < 100 && parse_uint(f[4], &held) && held <= 1 &&
                (at > 0 || held == 1 || (!strcmp(f[1], "localclone") && dn == tot && tot > 0))) { /* #2198: 0 seconds, success held by choice */
                if (!strcmp(f[1], "drawers")) kind = END_DRAWERS;
                else if (!strcmp(f[1], "partial")) kind = END_PARTIAL;   /* some targets failed */
                else if (!strcmp(f[1], "localclone")) kind = END_LOCALCLONE;
                else if (!strcmp(f[1], "failed")) kind = END_FAILED;   /* a cloner round or local clone that failed */
            }
            if (kind == END_NONE) fprintf(stderr, "native-gui: state: end_poweroff malformed; not shown\n");
            else { s->end_poweroff_at = (long long)at; s->end_kind = kind;
                   s->end_done = (int)dn; s->end_total = (int)tot; s->end_held = (int)held; }
        } else if (KEY("end_problem") && s->nend_prob < MAX_DRAWERS) {
            /* #2024: <dev>|<r|o>|<reason> (idle.sh end_problems). A tone that is
             * not "o" draws red: an unread colour is never "only orange". */
            if (split(val, f, 3) != 3 || !f[2][0]) { fprintf(stderr, "native-gui: state: end_problem malformed; not shown\n"); continue; }
            cp(s->end_prob[s->nend_prob].dev, sizeof s->end_prob[0].dev, f[0]);
            cp_utf8(s->end_prob[s->nend_prob].why, sizeof s->end_prob[0].why, f[2]);
            s->end_prob[s->nend_prob++].red = strcmp(f[1], "o") != 0;
        } else if (KEY("power_ask")) {
            /* #1918: <nonce>|<kind>|<can_stop> (powerbtn.sh). All or nothing:
             * a record this screen cannot read asks nothing -- the write goes on. */
            unsigned long long nonce = 0;
            if (split(val, f, 4) == 3 && parse_uint(f[0], &nonce) && nonce > 0 &&
                f[1][0] && strlen(f[1]) < sizeof s->power_kind && (!strcmp(f[2], "0") || !strcmp(f[2], "1"))) {
                snprintf(s->power_nonce, sizeof s->power_nonce, "%llu", nonce);
                cp(s->power_kind, sizeof s->power_kind, f[1]);
                s->power_can_stop = f[2][0] == '1';
            } else fprintf(stderr, "native-gui: state: power_ask malformed; not shown\n");
        } else if (KEY("session")) {
            split(val, f, 7);
            s->has_session = 1;
            cp(s->sess_image, sizeof s->sess_image, f[0]); cp(s->sess_prefix, sizeof s->sess_prefix, f[1]);
            cp(s->sess_group, sizeof s->sess_group, f[2]);
            s->sess_open = num(f[3], 0); s->joined = num(f[4], 0);
            s->expected = num(f[5], 0); s->starts_in = num(f[6], 0);
        } else if (KEY("form_error"))  cp(s->form_error, sizeof s->form_error, val);
        else if (KEY("room_error"))    cp(s->room_error, sizeof s->room_error, val);
        else if (KEY("room_failed"))   cp(s->room_failed, sizeof s->room_failed, val);   /* 17/10 */
        else if (KEY("class_error"))   cp(s->class_error, sizeof s->class_error, val);
        else if (KEY("toast"))         cp(s->toast, sizeof s->toast, val);
        /* #1569: the failure screen (failscreen.sh). fail= opens the block and
         * empties the lists, so a writer that composes the records twice
         * (the kiosk's hello state and a display-only loop) draws them once. */
        else if (KEY("fail")) {
            split(val, f, 5);
            s->fail_warn = !strcmp(f[0], "warn"); s->fail_info = !strcmp(f[0], "inf"); s->fail_hold = num(f[1], 1) != 0; s->fail_cloner = !strcmp(f[4], "cloner");
            cp(s->fail_title, sizeof s->fail_title, f[2]); cp(s->fail_sub, sizeof s->fail_sub, f[3]);
            s->fail_why[0] = s->fail_safe[0] = 0; s->fail_at = 0;
            s->nfail_ctx = s->nfail_rows = s->nfail_steps = s->nfail_code = s->nfail_btns = 0;
        }
        else if (KEY("fail_why"))  cp(s->fail_why, sizeof s->fail_why, val);
        else if (KEY("fail_safe")) cp(s->fail_safe, sizeof s->fail_safe, val);
        else if (KEY("fail_at"))   { unsigned long long v; if (parse_uint(val, &v) && v < (1ULL << 40)) s->fail_at = (long long)v; }
        else if (KEY("fail_ctx")) {
            if (s->nfail_ctx < MAX_FAIL_CTX) { split(val, f, 2);
                cp(s->fail_ctx[s->nfail_ctx].label, sizeof s->fail_ctx[0].label, f[0]);
                cp(s->fail_ctx[s->nfail_ctx].value, sizeof s->fail_ctx[0].value, f[1]); s->nfail_ctx++; }
        }
        else if (KEY("fail_row")) {
            if (s->nfail_rows < MAX_FAIL_ROWS) { split(val, f, 4);
                cp(s->fail_rows[s->nfail_rows].label, sizeof s->fail_rows[0].label, f[0]);
                cp(s->fail_rows[s->nfail_rows].sub, sizeof s->fail_rows[0].sub, f[1]);
                cp(s->fail_rows[s->nfail_rows].kind, sizeof s->fail_rows[0].kind, f[2]);
                cp(s->fail_rows[s->nfail_rows].pill, sizeof s->fail_rows[0].pill, f[3]); s->nfail_rows++; }
        }
        else if (KEY("fail_step")) { if (s->nfail_steps < MAX_FAIL_STEPS) cp(s->fail_steps[s->nfail_steps++], sizeof s->fail_steps[0], val); }
        else if (KEY("fail_code")) { if (s->nfail_code < MAX_FAIL_CODE) cp(s->fail_code[s->nfail_code++], sizeof s->fail_code[0], val); }
        else if (KEY("fail_btn")) {   /* #2068 */
            if (s->nfail_btns < MAX_FAIL_BTNS) { split(val, f, 2);
                cp(s->fail_btns[s->nfail_btns].action, sizeof s->fail_btns[0].action, f[0]);
                cp(s->fail_btns[s->nfail_btns].label, sizeof s->fail_btns[0].label, f[1]); s->nfail_btns++; }
        }
        else fprintf(stderr, "native-gui: state: unknown key '%s' ignored\n", key);
#undef KEY
    }
    for (int i = 0; i < ndisk_slots; i++) {
        if (!disk_slots[i].known) continue;
        for (int j = 0; j < s->ndisks; j++) if (!strcmp(s->disks[j].dev, disk_slots[i].dev)) {
            s->disks[j].slot_known = 1;
            s->disks[j].drawer = disk_slots[i].drawer;
            cp(s->disks[j].conn, sizeof s->disks[j].conn, disk_slots[i].conn);
            break;
        }
    }
    s->shrink_pending = shrink_seen && !shrink_bad && shrink_declared > 0 &&
                        (unsigned long long)s->nshrink == shrink_declared;
    int shrink_broken = (shrink_seen || s->nshrink) && !s->shrink_pending;
    if (shrink_broken) {
        fprintf(stderr, "native-gui: state: shrink question incomplete or malformed; not shown\n");
        s->nshrink = 0; s->shrink_nonce[0] = 0; s->shrink_direct = 0;
    }
    s->ask_pending = ask_seen && !ask_bad && s->ask_kind != ASK_NONE &&
                     (unsigned long long)s->nask == ask_declared;
    int ask_broken = (ask_seen || s->nask) && !s->ask_pending;
    if (ask_broken) {
        fprintf(stderr, "native-gui: state: question incomplete or malformed; not shown\n");
        s->nask = 0; s->ask_nonce[0] = 0; s->ask_subject[0] = 0; s->ask_kind = ASK_NONE;
    }
    int hidden = s->hidden_disks + s->hidden_machines + s->hidden_models;   /* #1269 */
    if (s->rejected_lines && hidden)
        snprintf(s->state_warning, sizeof s->state_warning,
                 "קובץ המצב חלקי: %d שורות נדחו · +%d נוספים, לא מוצגים",
                 s->rejected_lines, hidden);
    else if (s->rejected_lines)
        snprintf(s->state_warning, sizeof s->state_warning,
                 "קובץ המצב חלקי: %d שורות ארוכות או לא שלמות נדחו", s->rejected_lines);
    else if (hidden)
        snprintf(s->state_warning, sizeof s->state_warning,
                 "+%d נוספים, לא מוצגים", hidden);
    /* #928: a question that cannot be drawn whole is said, not hidden --
     * the agent cancels the capture when no answer comes. */
    if (shrink_broken && !s->state_warning[0])
        snprintf(s->state_warning, sizeof s->state_warning,
                 "שאלת הכיווץ הגיעה פגומה ולא מוצגת — הקליטה תבוטל");
    /* 11/10: the same for the question dialog -- no answer is the safe one */
    if (ask_broken && !s->state_warning[0])
        snprintf(s->state_warning, sizeof s->state_warning,
                 "שאלה מהסוכן הגיעה פגומה ולא מוצגת — בלי תשובה נבחרת ברירת המחדל הבטוחה");
}

int state_poll(StateFile *f, State *s) {
    struct stat st;
    if (stat(f->path, &st) != 0) {
        if (!f->complained) { perror(f->path); f->complained = 1; }
        return -1;
    }
    f->complained = 0;
    time_t now = time(NULL);
    int stale = now != (time_t)-1 && now > st.st_mtime && now - st.st_mtime > 10;
    if (f->loaded && st.st_mtime == f->mtime && st.st_size == f->size) {
        if (s->stale != stale) { s->stale = stale; return 1; }
        return 0;
    }
    FILE *fp = fopen(f->path, "r");
    if (!fp) { perror(f->path); return -1; }
    State next;
    state_parse(&next, fp);
    fclose(fp);
    next.stale = stale;
    *s = next;
    apply_tz(s->tz);
    f->mtime = st.st_mtime; f->size = st.st_size; f->loaded = 1;
    return 1;
}
