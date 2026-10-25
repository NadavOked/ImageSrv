#include <math.h>
#include <stdio.h>
#include <string.h>
#include "screens_install.h"
#include "install_raid.h"     /* #1989 */

static void console_guess(const InstallState *s,char *out,size_t n){
    const char *host=s->static_mode&&s->address[0]?s->address:s->hostname;char clean[96];
    if(!host[0]&&s->nic>=0&&s->nic<s->nnics)host=s->nics[s->nic].current;
    snprintf(clean,sizeof clean,"%s",host[0]?host:"imagesrv-server");char *slash=strchr(clean,'/');if(slash)*slash=0;
    snprintf(out,n,"https://%s:8081",clean);
}

static void summary_row(App *a,cairo_t *cr,Rect r,const char *key,const char *value,int row){
    const Theme *t=a->theme;cairo_rectangle(cr,r.x,r.y+r.h-1,r.w,1);draw_set(cr,t->hair);cairo_fill(cr);
    install_text_r(cr,t,key,12,500,r.x+r.w-16,r.y+13,150,t->muted);
    install_text_r(cr,t,value,13,500,r.x+r.w-180,r.y+12,r.w-280,t->ink);
    install_text_r(cr,t,"שינוי",12,600,r.x+75,r.y+13,65,t->indigo);
    hit_add(a,(Rect){r.x+10,r.y+5,85,r.h-10},HIT_INSTALL_SUMMARY_BASE+row);
}

static void summary(App *a,cairo_t *cr,InstallLayout l){
    InstallState *s=&a->install;const Theme *t=a->theme;double x=l.body.x,w=l.body.w,y=l.body.y;Rect card={x,y,w,290};install_card(cr,t,card);char value[400],url[192];
    InstallDisk *d=s->disk>=0&&s->disk<s->ndisks?&s->disks[s->disk]:NULL;
    if(s->raid!=RAID_NONE){char members[INSTALL_MAX_DISKS*48];install_raid_disks(s,members,sizeof members);snprintf(value,sizeof value,"%s · %d כוננים · %s",RAID_OPTS[s->raid].label,install_raid_count(s),members);}   /* #1989 */
    else{snprintf(value,sizeof value,"%s · %s",d&&d->model[0]?d->model:"—",d?d->path:"");}summary_row(a,cr,(Rect){x+1,y+1,w-2,48},"דיסק",value,0);
    if(s->secondary)snprintf(value,sizeof value,"שרת משנה · ראשי: %s",s->primary_url);else snprintf(value,sizeof value,"שרת ראשי");summary_row(a,cr,(Rect){x+1,y+49,w-2,48},"תפקיד",value,1);
    const char *nic=s->nic>=0&&s->nic<s->nnics?s->nics[s->nic].name:"—";snprintf(value,sizeof value,s->static_mode?"%s · %s / %s":"%s · DHCP",nic,s->address,s->netmask);summary_row(a,cr,(Rect){x+1,y+97,w-2,48},"כתובת השרת",value,2);
    snprintf(value,sizeof value,"%s · %s",s->hostname,s->timezone);summary_row(a,cr,(Rect){x+1,y+145,w-2,48},"שם השרת",value,3);snprintf(value,sizeof value,"%s · הסיסמה הוגדרה",s->admin_user);summary_row(a,cr,(Rect){x+1,y+193,w-2,48},"משתמש הניהול",value,4);
    summary_row(a,cr,(Rect){x+1,y+241,w-2,48},"סיסמת root","נקבעה",4);   /* #1632: that it was set, never the value; "שינוי" -> the admin step */
    console_guess(s,url,sizeof url);install_text_r(cr,t,"הקונסולה תהיה ב־",12,500,x+w-16,y+306,160,t->muted);install_text_ltr(cr,url,13,500,x+24,y+306,w-210,t->indigo);y+=340;
    Rect seq={x,y,fmin(760,w),112};install_card(cr,t,seq);install_text_r(cr,t,"מה יקרה בלחיצה על \"החל התקנה\"",14,600,seq.x+seq.w-16,y+14,seq.w-32,t->ink);
    install_text_r(cr,t,"מחיקת הדיסק ← מערכת בסיס ← חבילות ImageSrv ← מנהל אתחול ← הגדרות",13,500,seq.x+seq.w-16,y+50,seq.w-32,t->ink);
    char after[192];snprintf(after,sizeof after,"בסיום תתבקש להסיר את מדיית ההתקנה; השרת יופעל מחדש אוטומטית אחרי %d שניות.",INSTALL_AUTO_REBOOT_S);install_text_r(cr,t,after,12,400,seq.x+seq.w-16,y+78,seq.w-32,t->muted);
    install_footer(a,cr,l,"החל התקנה",HIT_INSTALL_NEXT,1,1);
}

static int phase_index(const char *phase){
    static const char *states[]={"partitioning","bootstrap","packages","bootloader","finishing","done"};
    for(int i=0;i<6;i++){if(!strcmp(phase,states[i]))return i;}return 0;
}

static void phase_rows(App *a,cairo_t *cr,Rect r,int failed){
    const Theme *t=a->theme;const char *states[]={"partitioning","bootstrap","packages","bootloader","finishing"};int now=phase_index(a->install.phase);
    for(int i=0;i<5;i++){Rect row={r.x,r.y+i*46,r.w,45};int done=i<now,run=i==now&&!failed,bad=failed&&i==now;Rgb c=bad?t->danger:(done?t->success_ink:(run?t->indigo:t->muted));
        cairo_arc(cr,row.x+row.w-18,row.y+21,9,0,6.283);draw_set_a(cr,c,(done||run||bad)?1:.3);cairo_fill(cr);
        const char *mark=bad?"×":done?"✓":run?"↻":"";Text m=text_make(cr,FONT_SANS,11,600,mark,0,DIR_RTL);text_draw(cr,&m,row.x+row.w-18-m.w/2,row.y+21-m.h/2,t->on_ink);text_free(&m);
        install_text_r(cr,t,install_phase_label(states[i]),13,run||bad?600:400,row.x+row.w-38,row.y+12,row.w-110,t->ink);
        install_text_r(cr,t,run?"רץ…":done?"הושלם":"",11,400,row.x+72,row.y+13,68,c);
    }
}

static void output_box(App *a,cairo_t *cr,Rect r){
    InstallState *s=&a->install;const Theme *t=a->theme;draw_fill_rrect(cr,r,t->radius,t->sunken);draw_border_rrect(cr,r,t->radius,t->hair,1);
    char copy[INSTALL_OUTPUT_MAX];snprintf(copy,sizeof copy,"%s",s->output);char *lines[64],*save=NULL;int n=0;
    for(char *p=strtok_r(copy,"\n",&save);p&&n<64;p=strtok_r(NULL,"\n",&save))lines[n++]=p;
    int start=s->output_scroll;if(start>n-1)start=n?n-1:0;
    for(int i=start;i<n&&i<start+9;i++)install_text_ltr(cr,lines[i],11,400,r.x+12,r.y+10+(i-start)*20,r.w-48,t->ink);
    install_text_r(cr,t,"▲",12,600,r.x+r.w-10,r.y+12,24,t->muted);install_text_r(cr,t,"▼",12,600,r.x+r.w-10,r.y+r.h-28,24,t->muted);
    hit_add(a,(Rect){r.x+r.w-36,r.y,36,r.h/2},HIT_INSTALL_OUTPUT_UP);hit_add(a,(Rect){r.x+r.w-36,r.y+r.h/2,36,r.h/2},HIT_INSTALL_OUTPUT_DOWN);
}

static void details_toggle(App *a,cairo_t *cr,Rect r){
    InstallState *s=&a->install;const Theme *t=a->theme;install_text_r(cr,t,s->technical_open?"פרטים טכניים  ▲":"פרטים טכניים  ▼",13,600,r.x+r.w,r.y,r.w,t->indigo);hit_add(a,r,HIT_INSTALL_TECHNICAL);
}

static void progress(App *a,cairo_t *cr,InstallLayout l){
    InstallState *s=&a->install;const Theme *t=a->theme;double w=fmin(760,l.body.w),x=l.body.x+l.body.w-w,y=l.body.y;Rect bar={x,y,w,8};draw_fill_rrect(cr,bar,4,t->bar_idle);Rect fill=bar;fill.w=bar.w*(s->progress_pct>100?100:s->progress_pct<0?0:s->progress_pct)/100.0;fill.x=bar.x+bar.w-fill.w;   /* RTL: it fills from the right (mockup 10/10) */
    if(fill.w>0)draw_fill_rrect(cr,fill,4,t->indigo);
    char pct[16];snprintf(pct,sizeof pct,"%d%%",s->progress_pct);install_text_ltr(cr,pct,13,600,x,y+16,60,t->ink);install_text_r(cr,t,install_phase_label(s->phase),18,600,x+w,y+18,w-80,t->ink);y+=58;
    phase_rows(a,cr,(Rect){x,y,w,230},0);y+=246;details_toggle(a,cr,(Rect){x+w-210,y,210,32});y+=38;
    if(s->technical_open){if(s->title[0])install_text_ltr(cr,s->title,11,500,x,y,w,t->muted);if(s->log[0])install_text_ltr(cr,s->log,11,400,x,y+20,w,t->muted);output_box(a,cr,(Rect){x,y+44,w,180});}
    /* The running screen intentionally leaves the footer button area empty. */
}

/* mockup 10/10: the failure is said in Hebrew; the engine's code ("packages-
 * failed: apt returned exit 100") is the second line, not the headline. A
 * message the engine already wrote in Hebrew is shown as it is. Returns 1
 * when the raw text belongs on the second line. */
static int failure_he(const InstallState *s,char *out,size_t n){
    static const struct{const char *code,*he;}KNOWN[]={
        {"partition","חלוקת הדיסק נכשלה."},{"bootstrap","התקנת מערכת הבסיס נכשלה."},
        {"packages","התקנת חבילות ImageSrv נכשלה."},{"bootloader","התקנת מנהל האתחול נכשלה."},
        {"finishing","כתיבת ההגדרות נכשלה."},{"hostname","קביעת שם השרת נכשלה."},
        {"verify","אימות ההתקנה נכשל."},{"validation","ההגדרות לא עברו בדיקה."}};
    const char *e=s->error;
    if(!e[0]){snprintf(out,n,"ההתקנה נכשלה.");return 0;}
    if((unsigned char)e[0]>=0x80){snprintf(out,n,"%s",e);return 0;}      /* already in Hebrew */
    for(size_t i=0;i<sizeof KNOWN/sizeof KNOWN[0];i++)
        if(!strncmp(e,KNOWN[i].code,strlen(KNOWN[i].code))){snprintf(out,n,"%s",KNOWN[i].he);return 1;}
    if(s->phase[0]&&strcmp(s->phase,"failed")){snprintf(out,n,"ההתקנה נכשלה בשלב: %s.",install_phase_label(s->phase));return 1;}
    snprintf(out,n,"ההתקנה נכשלה.");return 1;
}

static void failed(App *a,cairo_t *cr,InstallLayout l){
    InstallState *s=&a->install;const Theme *t=a->theme;double w=fmin(760,l.body.w),x=l.body.x+l.body.w-w,y=l.body.y;Rect note={x,y,w,62};draw_fill_rrect(cr,note,t->radius,t->danger_soft);draw_border_rrect(cr,note,t->radius,t->danger,1);
    char said[320];int raw=failure_he(s,said,sizeof said);
    install_text_r(cr,t,said,14,600,x+w-14,y+(raw?10:20),w-28,t->danger_fg);
    if(raw)install_text_ltr(cr,s->error,11,400,x+14,y+36,w-28,t->muted);
    y+=76;
    install_text_r(cr,t,"פרטים טכניים",13,600,x+w,y,w,t->ink);y+=30;output_box(a,cr,(Rect){x,y,w,250});
    Text retry=btn_label(cr,"נסה שוב");double rw=btn_width(&retry);draw_btn(a,cr,(Rect){l.footer.w-32-rw,l.footer.y+(l.footer.h-N_BUTTON_H)/2,rw,N_BUTTON_H},&retry,BTN_PRIMARY,HIT_INSTALL_RETRY);text_free(&retry);
}

/* #1250: the auto restart, below the "remove the media" card so that line
 * stays the first thing on the screen. "בטל" (or Esc) stops the count and
 * leaves the restart button as it was. */
static void reboot_countdown(App *a,cairo_t *cr,Rect r){
    InstallState *s=&a->install;const Theme *t=a->theme;char line[128];
    if(s->reboot_at<=0){install_text_r(cr,t,"ההפעלה מחדש האוטומטית בוטלה. לחץ 'הפעל מחדש' כשתסיים.",13,500,r.x+r.w-20,r.y+18,r.w-40,t->muted);return;}
    draw_fill_rrect(cr,r,t->radius,t->indigo_soft);draw_border_rrect(cr,r,t->radius,t->indigo,1);
    snprintf(line,sizeof line,"הפעלה מחדש אוטומטית בעוד %d שניות",s->reboot_left);install_text_r(cr,t,line,15,600,r.x+r.w-20,r.y+18,r.w-160,t->ink);
    Text c=btn_label(cr,"בטל");double cw=btn_width(&c);draw_btn(a,cr,(Rect){r.x+10,r.y+10,cw,N_BUTTON_H},&c,BTN_PLAIN,HIT_INSTALL_CANCEL_REBOOT);text_free(&c);
}

static void done(App *a,cairo_t *cr,InstallLayout l){
    InstallState *s=&a->install;const Theme *t=a->theme;double w=fmin(760,l.body.w),x=l.body.x+l.body.w-w,y=l.body.y;Rect hero={x,y,w,92};draw_fill_rrect(cr,hero,t->radius,t->success_soft);
    install_text_r(cr,t,"✓",42,600,x+w-24,y+18,54,t->success_ink);install_text_r(cr,t,"ההתקנה הושלמה.",22,600,x+w-90,y+28,w-115,t->ink);y+=112;
    Rect card={x,y,w,230};install_card(cr,t,card);install_text_r(cr,t,"הסר את מדיית ההתקנה (ISO / כונן USB) ולחץ 'הפעל מחדש'",15,600,x+w-20,y+20,w-40,t->ink);
    install_text_r(cr,t,"כתובת הקונסולה",12,500,x+w-20,y+72,190,t->muted);
    if(s->console_url[0])install_text_ltr(cr,s->console_url,18,600,x+20,y+100,w-40,t->indigo);else install_text_r(cr,t,"הכתובת תוצג במסך השרת אחרי האתחול",14,500,x+w-20,y+100,w-40,t->ink);
    install_text_r(cr,t,"משתמש",12,500,x+w-20,y+148,150,t->muted);install_text_ltr(cr,s->user[0]?s->user:s->admin_user,13,500,x+20,y+148,w-190,t->ink);
    if(s->error[0])install_text_r(cr,t,s->error,12,600,x+w-20,y+188,w-40,t->danger_fg);
    reboot_countdown(a,cr,(Rect){x,y+246,w,58});
    install_footer(a,cr,l,"הפעל מחדש",HIT_INSTALL_NEXT,0,1);
}

void install_draw_final(App *a,cairo_t *cr,InstallLayout l){switch(a->install.view){case INSTALL_SUMMARY:summary(a,cr,l);break;case INSTALL_PROGRESS:progress(a,cr,l);break;case INSTALL_FAILED:failed(a,cr,l);break;default:done(a,cr,l);break;}}
