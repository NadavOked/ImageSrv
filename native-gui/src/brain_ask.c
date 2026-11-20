/* #3654: the brain's question on this machine's screen -- the records, which question is on the screen,
 * the keys. The drawing is screens_brain_ask.c; the contract is brain_ask.h. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include "brain_ask.h"

#define LRI "\xE2\x81\xA6"   /* U+2066 LEFT-TO-RIGHT ISOLATE */
#define PDI "\xE2\x81\xA9"   /* U+2069 POP DIRECTIONAL ISOLATE */

/* "a|b||d" -> f[0..n-1]; returns how many fields there were (more than max = max + 1) */
static int fields(char *v, char *f[], int max) {
    int n = 0;
    f[n++] = v;
    for (char *p = v; *p; p++)
        if (*p == '|') { if (n == max) return max + 1; *p = 0; f[n++] = p + 1; }
    return n;
}

/* a whole value or nothing: a cut would draw half a sentence (or half a letter) as if it were all */
static int put(char *dst, size_t n, const char *src) {
    if (strlen(src) >= n) return 0;
    memcpy(dst, src, strlen(src) + 1);
    return 1;
}

static int num(const char *s, long long lo, long long hi, long long *out) {
    char *end;
    if (!*s || strspn(s, "0123456789") != strlen(s) || strlen(s) > 18) return 0;
    long long v = strtoll(s, &end, 10);
    if (*end || v < lo || v > hi) return 0;
    *out = v;
    return 1;
}

/* an option id the server allows ([a-z][a-z0-9_]{0,31}) -- it goes back out in the answer */
static int option_id(const char *s) {
    size_t n = strlen(s);
    return n >= 1 && n <= 32 && *s >= 'a' && *s <= 'z' && strspn(s, "abcdefghijklmnopqrstuvwxyz0123456789_") == n;
}

static Bask *find(State *s, int id) {
    for (int i = 0; i < s->nbask; i++) if (s->bask[i].id == id) return &s->bask[i];
    return NULL;
}

static int parse_head(State *s, char *val) {
    char *f[14];
    long long id, n, cols, ttl, dl = 0, closed = 0, admin;
    if (fields(val, f, 14) != 14 || s->nbask >= MAX_BASKS) return 0;
    if (!num(f[0], 1, 2147483647, &id) || find(s, (int)id)) return 0;
    Bask *b = &s->bask[s->nbask++];
    memset(b, 0, sizeof *b);
    b->id = (int)id; b->bad = 1;   /* until every field is read */
    b->state = !strcmp(f[1], "open") ? BASK_OPEN : !strcmp(f[1], "answered") ? BASK_ANSWERED
             : !strcmp(f[1], "expired") ? BASK_EXPIRED : !strcmp(f[1], "cancelled") ? BASK_CANCELLED : BASK_NONE;
    if (b->state == BASK_NONE || !num(f[2], 2, MAX_BASK_OPTS, &n) || !num(f[3], 1, 2, &cols) ||
        !num(f[4], 1, 7 * 86400, &ttl) || (f[5][0] && !num(f[5], 1, 1LL << 40, &dl)) ||
        (f[10][0] && !num(f[10], 1, 1LL << 40, &closed)) || !num(f[11], 0, 1, &admin) ||
        !option_id(f[6]) || (f[7][0] && !option_id(f[7])) ||
        !put(b->safe, sizeof b->safe, f[6]) || !put(b->answer, sizeof b->answer, f[7]) ||
        !put(b->role, sizeof b->role, f[8]) || !put(b->by, sizeof b->by, f[9]) ||
        !f[12][0] || !put(b->title, sizeof b->title, f[12]) || !put(b->detail, sizeof b->detail, f[13])) return 1;
    b->declared = (int)n; b->columns = (int)cols; b->ttl = ttl; b->deadline = dl; b->closed_at = closed;
    b->admin_too = (int)admin;
    b->bad = 0;
    return 1;
}

static void parse_opt(State *s, char *val) {
    char *f[7];
    long long qid, d;
    if (fields(val, f, 7) != 7 || !num(f[0], 1, 2147483647, &qid)) { s->bask_broken++; return; }
    Bask *b = find(s, (int)qid);
    if (!b) { s->bask_broken++; return; }   /* an option of no question */
    if (b->nopts >= MAX_BASK_OPTS) { b->bad = 1; return; }
    BaskOpt *o = &b->opt[b->nopts++];
    memset(o, 0, sizeof *o);
    for (int i = 0; i < b->nopts - 1; i++) if (!strcmp(b->opt[i].id, f[1])) b->bad = 1;
    if (!option_id(f[1]) || !num(f[2], 0, 1, &d) || !f[3][0] || !put(o->id, sizeof o->id, f[1]) ||
        !put(o->label, sizeof o->label, f[3]) || !put(o->verb, sizeof o->verb, f[4]) ||
        !put(o->cons, sizeof o->cons, f[5]) || !put(o->outcome, sizeof o->outcome, f[6])) { b->bad = 1; return; }
    o->destructive = (int)d;
}

int bask_state_line(State *s, const char *key, char *val) {
    if (!strcmp(key, "bask")) { if (!parse_head(s, val)) s->bask_broken++; return 1; }
    if (!strcmp(key, "bask_opt")) { parse_opt(s, val); return 1; }
    return 0;
}

static const BaskOpt *opt_of(const Bask *b, const char *id) {
    for (int i = 0; i < b->nopts; i++) if (!strcmp(b->opt[i].id, id)) return &b->opt[i];
    return NULL;
}

/* whole: every option, the safe default one of them and never destructive (the server's own rule), a
 * destructive option names its act and its cost, an answer is one of them, an open question has a deadline
 * and a closed one the moment it closed */
static int whole(const Bask *b) {
    const BaskOpt *safe = opt_of(b, b->safe);
    if (b->bad || b->nopts != b->declared || !safe || safe->destructive) return 0;
    for (int i = 0; i < b->nopts; i++) if (b->opt[i].destructive && (!b->opt[i].verb[0] || !b->opt[i].cons[0])) return 0;
    if (b->state == BASK_OPEN) return b->deadline > 0;
    if (b->state == BASK_ANSWERED && (!opt_of(b, b->answer) || !b->role[0])) return 0;
    return b->state == BASK_CANCELLED || b->closed_at > 0;
}

int bask_settle(State *s) {
    int k = 0;
    for (int i = 0; i < s->nbask; i++) {
        if (!whole(&s->bask[i])) {
            fprintf(stderr, "native-gui: state: brain question %d incomplete or malformed; not shown\n", s->bask[i].id);
            s->bask_broken++;
            continue;
        }
        if (k != i) s->bask[k] = s->bask[i];
        k++;
    }
    s->nbask = k;
    return s->bask_broken;
}

/* ---- which question is on the screen -------------------------------------------------------------- */

static double mono_now(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec / 1e9;
}

static int gone(const App *a, int id) {
    for (int i = 0; i < a->nbask_gone; i++) if (a->bask_gone[i] == id) return 1;
    return 0;
}

/* closed here: never shown again (the server keeps a closed question in the hello for 15 minutes) */
static void forget(App *a) {
    if (a->bask_id && !gone(a, a->bask_id)) {
        if (a->nbask_gone == MAX_BASK_GONE) memmove(a->bask_gone, a->bask_gone + 1, sizeof a->bask_gone[0] * (MAX_BASK_GONE - 1));
        else a->nbask_gone++;
        a->bask_gone[a->nbask_gone - 1] = a->bask_id;
    }
    a->bask_id = 0; a->bask_sel = 0; a->bask_sent_id = 0; a->bask_sent_at = 0; a->bask_closed_at = 0;
}

int bask_default_index(const Bask *b) {
    for (int i = 0; i < b->nopts; i++) if (!strcmp(b->opt[i].id, b->safe)) return i;
    return 0;
}

const Bask *bask_current(const App *a) {
    if (!a->bask_id || gone(a, a->bask_id)) return NULL;
    for (int i = 0; i < a->st.nbask; i++) if (a->st.bask[i].id == a->bask_id) return &a->st.bask[i];
    return NULL;
}

int bask_open_count(const App *a) {
    int n = 0;
    for (int i = 0; i < a->st.nbask; i++) n += a->st.bask[i].state == BASK_OPEN && !gone(a, a->st.bask[i].id);
    return n;
}

int bask_after_reload(App *a) {
    int cancelled = 0;
    if (a->bask_id) {
        const Bask *b = bask_current(a);
        if (!b) forget(a);                                       /* the list no longer names it: nothing to show */
        else if (b->state == BASK_CANCELLED) { forget(a); cancelled = 1; }
    }
    if (!a->bask_id)
        for (int i = 0; i < a->st.nbask; i++) {
            const Bask *b = &a->st.bask[i];
            if (b->state != BASK_OPEN || gone(a, b->id)) continue;   /* a result only for a question that was on the screen */
            a->bask_id = b->id; a->bask_sel = bask_default_index(b);
            a->bask_sent_id = 0; a->bask_sent_at = 0; a->bask_closed_at = 0;
            break;
        }
    const Bask *b = bask_current(a);
    if (b && (a->bask_sel < 0 || a->bask_sel >= b->nopts)) a->bask_sel = bask_default_index(b);
    return cancelled;
}

int bask_visible(const App *a) {
    if (!bask_current(a)) return 0;
    if (a->screen == SCREEN_LCBOOT || a->screen == SCREEN_LOCAL || a->screen == SCREEN_INSTALL) return 0;
    /* a window the person is in the middle of is never covered; the question waits behind it */
    return !a->st.shrink_pending && !a->st.ask_pending && !a->confirm && !a->sdlg.kind && !a->cdlg.open &&
           a->st.end_kind == END_NONE && !power_ask_open(a) && !a->sysp_open && !a->logout_confirming;
}

int bask_tick(App *a) {
    const Bask *b = bask_current(a);
    if (!b || b->state == BASK_OPEN || a->bask_closed_at <= 0) return 0;
    if (mono_now() - a->bask_closed_at < BASK_RESULT_S) return 0;
    forget(a);
    bask_after_reload(a);   /* the next open question, if there is one */
    return 1;
}

/* ---- the keys ----------------------------------------------------------------------------------- */

static void answer(App *a, const Bask *b) {
    double now = mono_now();
    if (a->bask_sent_id == b->id && now - a->bask_sent_at < BASK_RESEND_S) return;   /* one answer, until the server says */
    printf("brain-ask|%d|%s\n\n", b->id, b->opt[a->bask_sel].id);
    fflush(stdout);
    a->bask_sent_id = b->id; a->bask_sent_at = now;
}

int bask_modal(App *a, const Event *e) {
    if (!bask_visible(a) || e->type == UIEV_MOVE) return 0;
    const Bask *b = bask_current(a);
    const int open = b->state == BASK_OPEN, n = b->nopts, step = b->columns == 2 ? 2 : 1;
    int hit = -1;
    if (e->type == UIEV_CLICK) { a->ptr_x = e->x; a->ptr_y = e->y; hit = app_hit(a, e->x, e->y); }
    if (!open) {
        if ((e->type == UIEV_KEY && e->key == KEYSYM_ENTER) || hit == HIT_BASK_CLOSE) { forget(a); bask_after_reload(a); }
        return e->type != UIEV_SCROLL;
    }
    int sel = a->bask_sel;
    if (e->type == UIEV_CHAR && e->ch >= '1' && e->ch < '1' + n) sel = e->ch - '1';
    else if (e->type == UIEV_KEY && e->key == KEYSYM_DOWN) sel += step;
    else if (e->type == UIEV_KEY && e->key == KEYSYM_UP) sel -= step;
    else if (e->type == UIEV_KEY && e->key == KEYSYM_LEFT) sel += 1;    /* RTL: the left is the next one */
    else if (e->type == UIEV_KEY && e->key == KEYSYM_RIGHT) sel -= 1;
    else if (hit >= HIT_BASK_OPT_BASE && hit < HIT_BASK_OPT_BASE + n) sel = hit - HIT_BASK_OPT_BASE;
    a->bask_sel = sel < 0 ? 0 : sel >= n ? n - 1 : sel;
    if ((e->type == UIEV_KEY && e->key == KEYSYM_ENTER) || hit == HIT_BASK_GO) answer(a, b);
    return e->type != UIEV_SCROLL;
}

/* ---- the status bar ----------------------------------------------------------------------------- */

const char *bask_status_word(const App *a, Rgb *dot, char *buf, size_t n) {
    if (!bask_visible(a)) return NULL;
    const Bask *b = bask_current(a);
    if (b->state != BASK_OPEN) {
        *dot = a->theme->led_ok;
        return b->state == BASK_EXPIRED ? "השאלה נסגרה · ברירת המחדל" : "השאלה נסגרה";
    }
    int k = bask_open_count(a);
    *dot = a->theme->warn;
    if (k <= 1) return "ממתין להכרעה · שאלה אחת";
    snprintf(buf, n, "ממתין להכרעה · %d שאלות", k);
    return buf;
}

const char *bask_status_keys(const App *a, char *buf, size_t n) {
    if (!bask_visible(a)) return NULL;
    const Bask *b = bask_current(a);
    if (b->state != BASK_OPEN) return "Enter  סגירה";
    /* the arrows and "1–8" as isolated LTR runs (LRI..PDI): in the RTL line they read "↓↑" and "8–1" */
    snprintf(buf, n, LRI "%s" PDI "  בחירה    " LRI "1–%d" PDI "  מספר    Enter  אישור",
             b->columns == 2 ? "↑↓←→" : "↑↓", b->nopts);
    return buf;
}

/* ---- --png -------------------------------------------------------------------------------------- */

static void sample_opt(Bask *b, const char *id, const char *label, int d, const char *verb, const char *cons,
                       const char *outcome) {
    BaskOpt *o = &b->opt[b->nopts++];
    memset(o, 0, sizeof *o);
    snprintf(o->id, sizeof o->id, "%s", id); snprintf(o->label, sizeof o->label, "%s", label);
    snprintf(o->verb, sizeof o->verb, "%s", verb); snprintf(o->cons, sizeof o->cons, "%s", cons);
    snprintf(o->outcome, sizeof o->outcome, "%s", outcome);
    o->destructive = d;
}

/* the mockup's n1: old RAID metadata on a drawer's disk, 24:36 of 30:00 left */
static void sample(State *s) {
    Bask *b = &s->bask[0];
    memset(b, 0, sizeof *b);
    s->nbask = 1;
    b->id = 41; b->state = BASK_OPEN; b->columns = 1; b->ttl = 1800; b->admin_too = 1;
    b->deadline = (s->updated > 0 ? s->updated : (long long)time(NULL)) + 1476;   /* the --png clock (screens_brain_ask.c) */
    snprintf(b->safe, sizeof b->safe, "skip");
    snprintf(b->title, sizeof b->title, "נמצא על הדיסק מידע של מערך RAID ישן");
    snprintf(b->detail, sizeof b->detail, "הדיסק היה פעם חלק ממערך RAID של Intel. כתיבה תמחק את המידע הזה, והדיסק ייכתב כדיסק רגיל.");
    sample_opt(b, "skip", "לדלג על הדיסק", 0, "", "", "הדיסק לא ייכתב");
    sample_opt(b, "wipe", "למחוק את המידע הישן ולכתוב את הדיסק", 1, "מחיקת המידע",
               "המידע הישן יימחק ולא יהיה אפשר לשחזר אותו", "המידע הישן יימחק והדיסק ייכתב");
    b->declared = b->nopts;
}

void bask_png(App *a, const State *base, const char *card) {
    const int own = !strcmp(card, "brain-ask") || !strcmp(card, "brain-ask-pick");
    a->bask_id = a->bask_sel = a->bask_sent_id = a->nbask_gone = 0; a->bask_sent_at = a->bask_closed_at = 0;
    if (own && !base->nbask) sample(&a->st);
    if (!a->st.nbask) return;
    const Bask *b = &a->st.bask[0];   /* closed or open: a --state file draws what it carries */
    a->bask_id = b->id; a->bask_sel = bask_default_index(b);
    if (!strcmp(card, "brain-ask-pick"))
        for (int i = 0; i < b->nopts; i++) if (b->opt[i].destructive) a->bask_sel = i;
}
