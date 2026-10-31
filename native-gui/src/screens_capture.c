/* The capture path: #st-pick (the disks and the form), #st-progress,
 * #st-done -- and #st-message, the card for "nothing to do here". Text is
 * index.html's / station.js's; numbers are station.css / console.css. */
#include <math.h>
#include <stdio.h>
#include <string.h>
#include "screens_rounds.h"

/* ---- #st-pick -------------------------------------------------------------- */

/* The options of #st-folder: the folders (loadFolders) and nothing else --
 * #1251 (Nadav, 10/10): a new image always goes into a folder, so there is
 * no "no folder" option. folder_sel is 1-based; 0 = nothing chosen yet. */
static int folder_options(const App *a, const char **out, int max) {
    int n = 0;
    for (int i = 0; i < a->st.nfolders && n < max; i++) out[n++] = a->st.folders[i];
    return n;
}

/* What the closed select shows: the chosen folder, or what to do next. */
static const char *folder_shown(const App *a) {
    if (a->folder_sel > 0 && a->folder_sel <= a->st.nfolders) return a->st.folders[a->folder_sel - 1];
    return a->st.nfolders ? "בחרו תיקייה" : "אין תיקיות — צרו חדשה ב-\"+ חדשה\"";
}

void screen_pick(App *a, cairo_t *cr, double W, double H, double head_h) {
    const Theme *t = a->theme;
    const State *s = &a->st;
    double inner = card_width(W) - 2 * N_PAD;
    Head hd = head_make(cr, "בחר דיסק לקליטת אימג׳", "בחר את דיסק המקור שממנו ייווצר האימג'.", inner);

    /* drawDisks: internal disks only (removable filtered out) */
    int idx[MAX_DISKS], n = 0;
    for (int i = 0; i < s->ndisks; i++) if (!s->disks[i].removable) idx[n++] = i;
    for (int i = 1; i < n; i++) {
        int v = idx[i], vd = s->disks[v].slot_known ? eff_drawer(s->disks[v].drawer, 0) : 0;
        int j = i;
        while (j > 0) {
            const Disk *p = &s->disks[idx[j - 1]];
            int pd = p->slot_known ? eff_drawer(p->drawer, 0) : 0;
            if (!vd || (pd && pd <= vd)) break;
            idx[j] = idx[j - 1]; j--;
        }
        idx[j] = v;
    }

    /* nativePick .disk-row: grid 44px | 1fr | auto, gap 12, padding 12, radius 3;
     * .disk-main strong 12px + small 9px muted ("/dev/nvme0n1 · 1.0 TB");
     * .disk-side 10px at the inline end (physically left). The side column of
     * the mockup is SMART + used bytes; the state file has neither, so it
     * carries what disk= does say: "יש מערכת על הכונן" / "ריק". */
    Text db[MAX_DISKS], ds[MAX_DISKS], dd[MAX_DISKS];
    double dh[MAX_DISKS], text_w[MAX_DISKS];
    double body_h = N_PAD;
    Text none = { NULL, 0, 0 };
    if (n == 0) {
        none = sub_make(cr, "עוד לא דווחו כוננים — המידע מגיע כשהמחשב עולה ב-PXE.", inner);
        pango_layout_set_alignment(none.layout, PANGO_ALIGN_RIGHT);   /* ui-audit: was drawn at the physical left of an RTL page */
        body_h += none.h;
    }
    for (int k = 0; k < n; k++) {
        const Disk *d = &s->disks[idx[k]];
        char size[32], small[256], label[128];
        fmt_bytes(size, sizeof size, (double)d->size_bytes);
        if (!d->slot_known) snprintf(small, sizeof small, "%s · %s", d->dev, size);
        else if (eff_drawer(d->drawer, 0) > 0)
            snprintf(small, sizeof small, "דיסק %d · %s · %s", eff_drawer(d->drawer, 0), d->dev, size);
        else {
            unmapped_label("", d->conn, 0, d->dev, label, sizeof label);
            snprintf(small, sizeof small, "%s · %s · %s", label, d->dev, size);
        }
        dd[k] = text_make(cr, FONT_SANS, N_DISK_SIDE, 400, d->has_data ? "יש מערכת על הכונן" : "ריק", 0, DIR_RTL);
        text_w[k] = inner - 2 * N_DISK_PAD - N_DISK_ICON_COL - 2 * N_DISK_COL_GAP - dd[k].w;
        db[k] = rtl_block_make(cr, N_DISK_TITLE, 700, d->model[0] ? d->model : "כונן", text_w[k]);
        ds[k] = rtl_block_make(cr, N_DISK_SUB, 400, small, text_w[k]);
        dh[k] = N_DISK_PAD + dmax(N_CHOICE_ICON, db[k].h + 2 + ds[k].h) + N_DISK_PAD;
        body_h += dh[k] + (k < n - 1 ? N_DISK_GAP : 0);
    }

    /* #st-form shows once a disk is chosen (drawDisks click handler) */
    int form = a->chosen_disk >= 0;
    Text l1 = { NULL, 0, 0 }, l2 = l1, l3 = l1, nf = l1, err = l1;
    double in_h = field_height(cr), btn_h = btn_height(cr), err_h = 0;
    if (form) {
        l1 = label_make(cr, "שם האימג'", inner);
        l2 = label_make(cr, "תיאור (לא חובה)", inner);
        l3 = label_make(cr, "תיקיית יעד", inner);
        nf = btn_label(cr, "+ חדשה");
        err = error_make(cr, s->form_error, inner);
        err_h = dmax(err.h, ERROR_MIN_H);
        /* last disk card's margin-bottom (10) collapses with the label's
         * margin-top (6); an input inside its label keeps 14 + 6 below it;
         * the folder row is select (margin-bottom 14) + 6 to .error. */
        body_h += (n ? N_DISK_GAP : 0) + l1.h + 2 + in_h + N_FORM_GAP + l2.h + 2 + in_h + N_FORM_GAP + l3.h + N_LABEL_GAP + in_h + N_FORM_GAP
                + (a->newfolder_shown ? in_h + N_FORM_GAP : 0) + N_LABEL_GAP + err_h;
    }
    body_h += N_PAD;

    /* .native-actions: "קלוט מהדיסק הנבחר" primary (when chosen), "ביטול" --
     * the mockup's words for what were "התחל קליטה" / "חזרה"; the hint keeps
     * its place at the inline end */
    Text bs = form ? btn_label(cr, "קלוט מהדיסק הנבחר") : (Text){ NULL, 0, 0 };
    Text bb = btn_label(cr, "ביטול");
    double btns_w = btn_width(&bb) + (form ? N_ACTION_GAP + btn_width(&bs) : 0);
    Text hint = sub_make(cr, "הקליטה קוראת רק בלוקים תפוסים ואינה משנה את הכונן.", inner - btns_w - N_ACTION_GAP);
    double foot_h = foot_height(cr, dmax(btn_h, hint.h));

    Rect body, foot;
    card_frame(a, cr, W, H, head_h, &hd, body_h, foot_h, &body, &foot);
    body_clip_begin(a, cr, body);
    double x = body.x + N_PAD, y = body.y + N_PAD;
    if (n == 0) { text_draw(cr, &none, x, y, t->ink); y += none.h; text_free(&none); }
    for (int k = 0; k < n; k++) {
        Rect c = { x, y, inner, dh[k] };
        int sel = a->chosen_disk == idx[k], hov = hovered(a, c);
        draw_fill_rrect(cr, c, RADIUS_SM, sel ? t->disk_selected : t->disk_bg);
        draw_border_rrect(cr, c, RADIUS_SM, sel || hov ? t->disk_selected_line : t->disk_line, N_BORDER);
        /* the 44px icon column, then main, then side -- right to left */
        double col = c.x + c.w - N_DISK_PAD - N_DISK_ICON_COL;
        draw_native_icon(cr,t,(Rect){col+(N_DISK_ICON_COL-N_CHOICE_ICON)/2,c.y+(c.h-N_CHOICE_ICON)/2,N_CHOICE_ICON,N_CHOICE_ICON},ICON_DISK);
        double block = db[k].h + 2 + ds[k].h, by = c.y + (c.h - block) / 2;
        text_draw(cr, &db[k], col - N_DISK_COL_GAP - text_w[k], by, t->ink);
        text_draw(cr, &ds[k], col - N_DISK_COL_GAP - text_w[k], by + db[k].h + 2, t->muted);
        text_draw(cr, &dd[k], c.x + N_DISK_PAD, c.y + (c.h - dd[k].h) / 2, t->muted);
        hit_add(a, c, HIT_DISK_BASE + idx[k]);
        text_free(&db[k]); text_free(&ds[k]); text_free(&dd[k]);
        y += dh[k] + N_DISK_GAP;
    }

    Rect sel_r = { 0, 0, 0, 0 };
    if (form) {
        text_draw(cr, &l1, x, y, t->muted);                 y += l1.h + 2;
        draw_field(a, cr, (Rect){ x, y, inner, in_h }, a->name, 0, "למשל: Office 2024 — סטנדרט", HIT_NAME);
        y += in_h + N_FORM_GAP;
        text_draw(cr, &l2, x, y, t->muted);                 y += l2.h + 2;
        draw_field(a, cr, (Rect){ x, y, inner, in_h }, a->desc, 0, NULL, HIT_DESC);
        y += in_h + N_FORM_GAP;
        text_draw(cr, &l3, x, y, t->muted);                 y += l3.h + N_LABEL_GAP;
        /* .folder-row: flex, gap 8, align-items:center; select flex:1. The
         * select keeps its margin-bottom 14 inside the row, so the row is
         * that much taller and the button is centred against it. */
        double bw = btn_width(&nf), row_h = in_h + N_FORM_GAP;
        sel_r = (Rect){ x + bw + N_ACTION_GAP, y, inner - bw - N_ACTION_GAP, in_h };
        draw_select(a, cr, sel_r, folder_shown(a), HIT_FOLDER);
        draw_btn(a, cr, (Rect){ x, y + (row_h - btn_h) / 2, bw, btn_h }, &nf, BTN_PLAIN, HIT_NEWFOLDER);
        y += row_h;
        if (a->newfolder_shown) {
            draw_field(a, cr, (Rect){ x, y, inner, in_h }, a->folder_new, 0, "שם התיקייה החדשה", HIT_FOLDER_NEW);
            y += in_h + N_FORM_GAP;
        }
        y += N_LABEL_GAP;
        text_draw(cr, &err, x, y, t->danger_fg);
        text_free(&l1); text_free(&l2); text_free(&l3); text_free(&nf); text_free(&err);
    }
    body_clip_end(a, cr);

    foot_draw_bg(cr, t, foot);
    double bx = foot.x + foot.w - N_PAD, byy = foot.y + N_ACTION_TOP;
    if (form) {
        bx -= btn_width(&bs);
        draw_btn(a, cr, (Rect){ bx, byy, btn_width(&bs), btn_h }, &bs, BTN_PRIMARY, HIT_START);
        bx -= N_ACTION_GAP;
        text_free(&bs);
    }
    bx -= btn_width(&bb);
    draw_btn(a, cr, (Rect){ bx, byy, btn_width(&bb), btn_h }, &bb, BTN_PLAIN, HIT_BACK);
    text_free(&bb);
    /* .st-card .sfoot{align-items:center} .sub{margin-inline-start:auto} */
    text_draw(cr, &hint, foot.x + N_PAD, foot.y + N_ACTION_TOP + (dmax(btn_h, hint.h) - hint.h) / 2, t->ink);
    text_free(&hint);

    card_end(a, cr);
    /* the open <select> list: after card_end, so the card's clip does not
     * cut it, and last, so it is on top of everything */
    if (a->dd_open == HIT_FOLDER && form) {
        const char *opts[MAX_FOLDERS];
        int nopt = folder_options(a, opts, MAX_FOLDERS);
        draw_popup(a, cr, sel_r, opts, nopt, a->folder_sel - 1);
    }
}

/* ---- #st-progress ----------------------------------------------------------- */

/* nativeProgress: .native-progress-top (label / title 23px / subtitle at the
 * start; the 44px percentage + "הושלם" 11px at the end), .native-progressbar
 * 14px, .native-metrics, then the "אל תכבו" note. The mockup's four metrics
 * are נכתב / סה״כ / קצב / זמן נותר; the state file carries bytes and pct, so
 * three are shown -- the total is derived from those two -- and rate/ETA
 * are not invented. */
void screen_progress(App *a, cairo_t *cr, double W, double H, double head_h) {
    const Theme *t = a->theme;
    const State *s = &a->st;
    double inner = card_width(W) - 2 * N_PAD;

    /* drawProgress, unless the agent set the texts itself (a cloning
     * machine or a class receiver shows this card with no operator). The
     * old "קולט: <name>" head is now label "קליטת אימג׳" + the name. */
    char title[160], sub[1100], pct[96], bytes[64] = "—", total[64] = "—", rate[64] = "—";   /* sub: RLM + task_error[1024] whole, like done_sub (#1502) */
    const char *label = s->title[0] ? "משימה" : s->task_direct ? "הפצה ישירה" : "קליטת אימג׳";
    if (s->title[0])              snprintf(title, sizeof title, "%s", s->title);
    else if (s->task_direct)      snprintf(title, sizeof title, "משדר מהדיסק %s למחשבי השיכפול", s->task_disk);   /* #715 */
    else if (s->task_name[0])     snprintf(title, sizeof title, "%s", s->task_name);
    else                          snprintf(title, sizeof title, "קולט…");          /* index.html default */
    if (s->task_error[0])         snprintf(sub, sizeof sub, "\xE2\x80\x8F%s", s->task_error);   /* #1502: RLM -- "sda: ..." would lay the line out LTR */
    else if (s->sub[0])           snprintf(sub, sizeof sub, "%s", s->sub);
    else if (s->task == TASK_PENDING) snprintf(sub, sizeof sub, "ממתין לסוכן — ודאו שהמחשב עלה ב-PXE");
    else if (s->task_disk[0])     snprintf(sub, sizeof sub, "כונן המקור: %s", s->task_disk);
    else                          sub[0] = 0;
    progress_label(pct, sizeof pct, s->pct, s->moving, s->partition);
    if (s->bytes) {
        fmt_bytes(bytes, sizeof bytes, (double)s->bytes);
        if (s->pct > 0) fmt_bytes(total, sizeof total, (double)s->bytes * 100.0 / s->pct);
    }
    /* #408: the rate is what says "alive" when there is no denominator; it is
     * shown only once measured (two reads), never as 0. */
    if (a->capture_rate_bps > 0) { char r[32]; fmt_bytes(r, sizeof r, a->capture_rate_bps); snprintf(rate, sizeof rate, "%s/s", r); }
    /* mockup 10/10 ("מסך קליטה"): the big number is what is KNOWN. A
     * known total gives "42%" + "הושלם"; without one it is the bytes read
     * ("23.1 GB" + "נקראו"), and before the first byte only words -- never
     * "--" in 88px over "הושלם" on a capture that is still running. */
    char big[32] = "", unit[16] = "";
    const char *cap_s;
    if (s->pct >= 0) { snprintf(big, sizeof big, "%d%%", s->pct > 100 ? 100 : s->pct); cap_s = "הושלם"; }
    else if (s->bytes) {
        char rd[32]; fmt_bytes(rd, sizeof rd, (double)s->bytes);
        char *sp = strchr(rd, ' ');                       /* "23.1 GB": the number, then its unit */
        if (sp) { *sp = 0; snprintf(unit, sizeof unit, "%s", sp + 1); }
        snprintf(big, sizeof big, "%s", rd);
        cap_s = s->title[0] ? "נכתבו" : "נקראו";
    } else cap_s = s->task == TASK_PENDING ? "ממתין לסוכן" : "טרם נקראו בייטים";

    Text tp = big[0] ? text_make(cr,FONT_MONO,s->pct >= 0 ? N_PROGRESS_PCT : 72,400,big,0,DIR_LTR) : (Text){ NULL, 0, 0 };
    Text un = unit[0] ? text_make(cr,FONT_MONO,28,400,unit,0,DIR_LTR) : (Text){ NULL, 0, 0 };
    Text cap = text_make(cr,FONT_SANS,big[0] ? N_LABEL : N_SUB,big[0] ? 400 : 600,cap_s,0,DIR_RTL);
    double num_w = tp.w + (un.layout ? 6 + un.w : 0);
    double end_w = dmax(num_w, cap.w), start_w = inner - end_w - N_ACTION_TOP;   /* gap 20 ~ N_ACTION_TOP */
    Text lb = label_make(cr, label, start_w);
    Text ti = rtl_block_make(cr, N_PROGRESS_TITLE, 500, title, start_w);
    Text su = rtl_block_make(cr, N_SUB, 400, sub, start_w);
    double start_h = lb.h + N_LABEL_GAP + ti.h + N_TITLE_GAP + su.h, end_h = tp.h + cap.h;
    double top_h = dmax(start_h, end_h);
    /* what is not known is said in words, under the bar */
    Text unknown = s->pct < 0 ? rtl_block_make(cr, N_LABEL, 400, "הסך לא ידוע — מוצג מה שכבר נקרא; האחוז יופיע כשהסך יידע.", inner)
                              : (Text){ NULL, 0, 0 };
    double unknown_h = unknown.layout ? N_LABEL_GAP + 6 + unknown.h : 0;
    Text note = sub_make(cr,"אל תכבו את המחשב. אפשר לעקוב גם מהקונסולה.",inner);
    pango_layout_set_alignment(note.layout, PANGO_ALIGN_RIGHT);       /* ui-audit: same edge as the title above it */
    double mh = metric_height(cr), mw = (inner - 2*N_METRIC_GAP) / 3;
    double body_h = top_h + N_ACTION_TOP + N_BAR_H + unknown_h + N_METRIC_GAP + mh + N_ACTION_TOP + note.h
                  + (s->can_cancel ? N_ACTION_TOP + N_BUTTON_H : 0);   /* #1561: "ביטול השחזור" under the note */   /* card_frame adds the bottom N_PAD */

    Head hd = { {NULL,0,0}, {NULL,0,0}, N_PAD, 0 };     /* the head is the progress-top itself */
    Rect body,foot;
    card_frame(a,cr,W,H,head_h,&hd,body_h,0,&body,&foot);
    body_clip_begin(a,cr,body);
    double x=body.x+N_PAD, y=body.y, xr = x + inner;
    /* align-items:flex-end: both blocks sit on the row's bottom */
    double sy = y + top_h - start_h, ey = y + top_h - end_h;
    text_draw_r(cr,&lb,xr,sy,t->muted);                  sy += lb.h + N_LABEL_GAP;
    text_draw_r(cr,&ti,xr,sy,t->ink);                    sy += ti.h + N_TITLE_GAP;
    text_draw_r(cr,&su,xr,sy,t->muted);
    if (tp.layout) {
        text_draw(cr,&tp,x,ey,t->ink);
        /* the unit on the number's baseline, after it (the line is LTR) */
        if (un.layout) text_draw(cr,&un,x+tp.w+6,ey+text_baseline(&tp)-text_baseline(&un),t->muted);
        ey += tp.h;
    }
    text_draw(cr,&cap,x,ey,big[0] ? t->muted : t->ink);
    y += top_h + N_ACTION_TOP;
    draw_big_bar(cr,t,(Rect){x,y,inner,N_BAR_H},s->pct,s->moving);
    y += N_BAR_H;
    if (unknown.layout) { text_draw_r(cr,&unknown,xr,y+N_LABEL_GAP+6,t->muted); y += unknown_h; }
    y += N_METRIC_GAP;
    draw_metric(cr,t,(Rect){xr-mw,y,mw,mh},s->title[0] ? "נכתב" : "נקראו",bytes);
    /* #408: without a denominator the middle metric is the rate -- "10.7 GB
     * read at 93 MB/s" is a whole answer; "— of —" was a frozen screen. */
    if (s->pct >= 0) draw_metric(cr,t,(Rect){xr-2*mw-N_METRIC_GAP,y,mw,mh},"סה״כ (משוער)",total);
    else             draw_metric(cr,t,(Rect){xr-2*mw-N_METRIC_GAP,y,mw,mh},"קצב",rate);
    /* the unknown total is said under the bar; this box then names the partition being read */
    if (s->pct >= 0) draw_metric(cr,t,(Rect){x,y,mw,mh},"התקדמות",pct);
    else { char part[16] = "—"; if (s->partition > 0) snprintf(part, sizeof part, "%d", s->partition);
           draw_metric(cr,t,(Rect){x,y,mw,mh},"מחיצה",part); }
    y += mh+N_ACTION_TOP;
    text_draw(cr,&note,x,y,t->muted);
    if (s->can_cancel) {   /* #1561: "שחזור לדיסק הזה" can still be stopped -- it asks first (#1270) */
        Text cb = btn_label(cr, "ביטול השחזור");
        double cw2 = btn_width(&cb);
        draw_btn(a, cr, (Rect){ xr - cw2, y + note.h + N_ACTION_TOP, cw2, N_BUTTON_H }, &cb, BTN_PLAIN, HIT_RESTORE_CANCEL);
        text_free(&cb);
    }
    body_clip_end(a,cr);card_end(a,cr);
    text_free(&tp);text_free(&un);text_free(&cap);text_free(&lb);text_free(&ti);text_free(&su);text_free(&note);text_free(&unknown);
}

/* ---- #st-done ---------------------------------------------------------------- */

/* nativeDone: .native-done grid (112px icon | text), label / title / subtitle,
 * .native-summary metrics, actions: "קליטה נוספת" .success. The old
 * "הקליטה הושלמה" head is the label; the image name is the title. Of the
 * mockup's four metrics (משך/נכתב/אימות/אתחול) the state file has bytes and
 * the source disk; the rest are not invented. "אתחל עכשיו" has no record. */
void screen_done(App *a, cairo_t *cr, double W, double H, double head_h) {
    const Theme *t=a->theme; const State *s=&a->st;
    int failed = s->task == TASK_FAILED;
    int warning = !failed && s->task_error[0]; /* #2213: dropped Fast Startup on the RAM copy */
    double cw=card_width(W), inner=cw-2*N_PAD;
    double tw=inner-N_DONE_ICON-N_DONE_GAP;
    Text label=label_make(cr,a->done_title,tw);
    Text title=rtl_block_make(cr,N_TITLE,500,s->task_name[0] ? s->task_name : a->done_title,tw);
    Text sub=rtl_block_make(cr,N_SUB,400,a->done_sub,tw);
    Text button=btn_label(cr,"קליטה נוספת");
    char bytes[32]="—"; if (s->bytes) fmt_bytes(bytes,sizeof bytes,(double)s->bytes);
    double mh=metric_height(cr), mw=(tw-N_METRIC_GAP)/2;
    double block=label.h+N_LABEL_GAP+title.h+N_TITLE_GAP+sub.h+N_LOGO_GAP+mh+N_ACTION_TOP+N_BUTTON_H;
    double ch=2*N_PAD+dmax(N_DONE_ICON,block);
    Rect panel={(W-cw)/2,head_h+(H-head_h-N_STATUS_H-ch)/2,cw,ch};
    draw_box_shadow(cr,panel,RADIUS_R,N_SHADOW_Y,N_SHADOW_BLUR,0,t->shadow_strong,t->shadow_card_a);
    draw_fill_rrect(cr,panel,RADIUS_R,t->surface);
    draw_border_rrect(cr,panel,RADIUS_R,t->hair,N_BORDER);
    double x=panel.x+N_PAD,y=panel.y+N_PAD;
    /* align-items:center: the icon is centred against the text block */
    draw_native_icon(cr,t,(Rect){panel.x+cw-N_PAD-N_DONE_ICON,y+(dmax(block,N_DONE_ICON)-N_DONE_ICON)/2,N_DONE_ICON,N_DONE_ICON},
                     failed || warning ? ICON_WARNING : ICON_SUCCESS);
    text_draw(cr,&label,x,y,failed ? t->danger : warning ? t->warn : t->muted);   y += label.h+N_LABEL_GAP;
    text_draw(cr,&title,x,y,t->ink);                          y += title.h+N_TITLE_GAP;
    text_draw(cr,&sub,x,y,t->muted);                          y += sub.h+N_LOGO_GAP;
    draw_metric(cr,t,(Rect){x+tw-mw,y,mw,mh},"נקראו",bytes);
    draw_metric(cr,t,(Rect){x,y,mw,mh},"כונן המקור",s->task_disk[0] ? s->task_disk : "—");
    y += mh+N_ACTION_TOP;
    double bw=btn_width(&button);
    draw_btn(a,cr,(Rect){x+tw-bw,y,bw,N_BUTTON_H},&button,failed || warning ? BTN_PRIMARY : BTN_SUCCESS,HIT_AGAIN);
    text_free(&label);text_free(&title);text_free(&sub);text_free(&button);
}

/* ---- #st-message ------------------------------------------------------------- */

void screen_message(App *a, cairo_t *cr, double W, double H, double head_h) {
    const Theme *t = a->theme;
    double cw = fmin(N_NARROW_W, card_width(W)), inner = cw - 2*N_PAD;
    Text title = rtl_block_make(cr,N_TITLE,500,a->st.msg_title,inner);
    Text sub = rtl_block_make(cr,N_SUB,400,a->st.msg_sub,inner);
    pango_layout_set_alignment(title.layout,PANGO_ALIGN_CENTER);
    pango_layout_set_alignment(sub.layout,PANGO_ALIGN_CENTER);
    double ch = 2*N_PAD + N_MESSAGE_ICON + N_MESSAGE_GAP + title.h + N_TITLE_GAP + sub.h;
    Rect panel = {(W-cw)/2,head_h+(H-head_h-N_STATUS_H-ch)/2,cw,ch};
    draw_box_shadow(cr,panel,RADIUS_R,N_SHADOW_Y,N_SHADOW_BLUR,0,t->shadow_strong,t->shadow_card_a);
    draw_fill_rrect(cr,panel,RADIUS_R,t->surface);
    draw_border_rrect(cr,panel,RADIUS_R,t->hair,N_BORDER);
    double y = panel.y + N_PAD;
    draw_native_icon(cr,t,(Rect){(W-N_MESSAGE_ICON)/2,y,N_MESSAGE_ICON,N_MESSAGE_ICON},ICON_WARNING);
    y += N_MESSAGE_ICON + N_MESSAGE_GAP;
    text_draw(cr,&title,panel.x+N_PAD,y,t->ink); y += title.h + N_TITLE_GAP;
    text_draw(cr,&sub,panel.x+N_PAD,y,t->muted);
    text_free(&title); text_free(&sub);
}
