#include <math.h>
#include <stdio.h>
#include <string.h>
#include "screens_install.h"
#include "install_raid.h"     /* #1989 */
#include "screens_rounds.h"   /* bidi_isolate, RLM */
#include "he_time.h"          /* #1503 */

/* #2097: under DHCP the address the NIC has now -- https://<name>:8081 works only
 * once the college's DNS knows the new server. The name when there is no address. */
static void console_guess(const InstallState *s,char *out,size_t n){
    const char *now=s->nic>=0&&s->nic<s->nnics?s->nics[s->nic].address:"";
    const char *host=s->static_mode?s->address:now;char clean[96];
    if(!host[0])host=s->hostname;
    snprintf(clean,sizeof clean,"%s",host[0]?host:"imagesrv-server");char *slash=strchr(clean,'/');if(slash)*slash=0;
    snprintf(out,n,"https://%s:8081",clean);
}

static void summary_row(App *a,cairo_t *cr,Rect r,const char *key,const char *value,int row){
    const Theme *t=a->theme;cairo_rectangle(cr,r.x,r.y+r.h-1,r.w,1);draw_set(cr,t->hair);cairo_fill(cr);
    install_text_r(cr,t,key,12,500,r.x+r.w-16,r.y+13,150,t->muted);
    install_text_r(cr,t,value,13,500,r.x+r.w-180,r.y+12,r.w-280,t->ink);
    install_text_r(cr,t,"שינוי",12,600,r.x+75,r.y+13,65,t->indigo);
    hit_add(a,(Rect){r.x+10,r.y+5,85,r.h-10},HIT_INSTALL_SUMMARY_BASE+row);install_focus_ring(a,cr,(Rect){r.x+10,r.y+5,85,r.h-10},HIT_INSTALL_SUMMARY_BASE+row);   /* #2776 */
}

/* #2129: the NIC by the name the network screen gives it, and its MAC -- eth0 is
 * the installer's name, not the server's -- then the address, the gateway and
 * the DNS (mockup install-5-summary). Under DHCP: what the NIC has now. */
static void net_lines(const InstallState *s,char *one,size_t n1,char *two,size_t n2){
    if(s->nic<0||s->nic>=s->nnics){snprintf(one,n1,"—");two[0]=0;return;}
    const InstallNic *c=&s->nics[s->nic];char label[96],mac[48],raw[100],addr[112],gw[64],dns[112],gwl[96],dnsl[128];
    install_nic_label(s,s->nic,label,sizeof label);bidi_isolate(c->mac,mac,sizeof mac);snprintf(one,n1,RLM "%s · %s",label,mac);
    const char *g=s->static_mode?s->gateway:c->gateway,*d=s->static_mode?s->dns:c->dns;
    if(s->static_mode)snprintf(raw,sizeof raw,"%s / %s",s->address,s->netmask);else snprintf(raw,sizeof raw,"%s",c->current[0]?c->current:"—");bidi_isolate(raw,addr,sizeof addr);
    if(g[0]){bidi_isolate(g,gw,sizeof gw);snprintf(gwl,sizeof gwl,"שער %s",gw);}else snprintf(gwl,sizeof gwl,"ללא שער");
    if(d[0]){bidi_isolate(d,dns,sizeof dns);snprintf(dnsl,sizeof dnsl,"DNS %s",dns);}else snprintf(dnsl,sizeof dnsl,"ללא DNS");
    snprintf(two,n2,RLM "%s · %s · %s · %s",s->static_mode?"כתובת סטטית":"DHCP · עכשיו",addr,gwl,dnsl);
}

/* #2869: a clock that needs the internet -- automatic, or an organisation server given by name -- on a
 * management NIC with no gateway or no DNS cannot sync, and updates cannot resolve github.com either.
 * Said before the install, not 50 minutes after it. 1 = warn; the text is in `out`. */
static int clock_warning(const InstallState *s,char *out,size_t n){
    if(s->ntp_manual||s->nic<0||s->nic>=s->nnics)return 0;
    int byname=0;if(s->ntp_org)for(const char *p=s->ntp_servers;*p;p++)if((*p>='g'&&*p<='z')||(*p>='G'&&*p<='Z'))byname=1;   /* a letter beyond hex: a name, not an address */
    if(s->ntp_org&&!byname)return 0;
    const InstallNic *c=&s->nics[s->nic];const char *g=s->static_mode?s->gateway:c->gateway,*d=s->static_mode?s->dns:c->dns;
    if(g[0]&&d[0])return 0;
    snprintf(out,n,"%s, אבל לכרטיס הניהול אין %s — השעון לא יסונכרן ועדכונים לא יעבדו. הוסיפו שער ו-DNS, או בחרו שרת זמן של הארגון לפי כתובת IP.",
             s->ntp_org?"בחרתם שרת זמן לפי שם":"בחרתם שעון אוטומטי מהאינטרנט",!g[0]&&!d[0]?"שער ו-DNS":!g[0]?"שער":"DNS");
    return 1;
}

static void summary(App *a,cairo_t *cr,InstallLayout l){
    InstallState *s=&a->install;const Theme *t=a->theme;double x=l.body.x,w=l.body.w,y=l.body.y;char value[400],url[192];
    /* #2129: the network row is two lines (a third when one wraps); the rows below move down by what it grew */
    char one[200],two[512];net_lines(s,one,sizeof one,two,sizeof two);
    Text m1=text_make(cr,FONT_SANS,13,500,one,(int)(w-282),DIR_RTL),m2=text_make(cr,FONT_SANS,13,500,two,(int)(w-282),DIR_RTL);double nh=fmax(48,12+m1.h+4+m2.h+10),dy=nh-48;
    Rect card={x,y,w,386+dy};install_card(cr,t,card);
    InstallDisk *d=s->disk>=0&&s->disk<s->ndisks?&s->disks[s->disk]:NULL;
    if(s->raid!=RAID_NONE){char members[INSTALL_MAX_DISKS*48];install_raid_disks(s,members,sizeof members);snprintf(value,sizeof value,"%s · %d כוננים · %s",RAID_OPTS[s->raid].label,install_raid_count(s),members);}   /* #1989 */
    else{snprintf(value,sizeof value,"%s · %s",d&&d->model[0]?d->model:"—",d?d->path:"");}summary_row(a,cr,(Rect){x+1,y+1,w-2,48},"דיסק",value,0);
    if(s->secondary)snprintf(value,sizeof value,"שרת משנה · ראשי: %s",s->primary_url);else snprintf(value,sizeof value,"שרת ראשי");summary_row(a,cr,(Rect){x+1,y+49,w-2,48},"תפקיד",value,1);
    summary_row(a,cr,(Rect){x+1,y+97,w-2,nh},"כתובת השרת",one,2);text_draw_r(cr,&m2,x+w-181,y+97+12+m1.h+4,t->ink);text_free(&m1);text_free(&m2);
    summary_row(a,cr,(Rect){x+1,y+145+dy,w-2,48},"שם השרת",s->hostname,3);   /* #2764: the zone is the clock's, below */
    {char src[700],zone[96],when[64];bidi_isolate(s->timezone,zone,sizeof zone);
        if(s->ntp_manual){bidi_isolate(s->manual_time,when,sizeof when);snprintf(src,sizeof src,"ידני, בלי שרת זמן · %s · %s",when,zone);}
        else{char servers[300];bidi_isolate(s->ntp_servers,servers,sizeof servers);snprintf(src,sizeof src,"%s · %s",s->ntp_org?servers:"אוטומטי",zone);}
        summary_row(a,cr,(Rect){x+1,y+193+dy,w-2,48},"שעון השרת",src,4);}   /* #2144: "שינוי" -> step 5 */
    if(s->backup_pick==INSTALL_BACKUP_OFF){Rect r={x+1,y+241+dy,w-2,48};draw_fill_rrect(cr,(Rect){r.x+8,r.y+4,r.w-16,r.h-9},6,t->warning_bg);   /* #2865: no backup -- the row says so, in the note's orange */
        summary_row(a,cr,r,"גיבוי המסד","כבוי",5);Text off=text_make(cr,FONT_SANS,13,500,"כבוי",0,DIR_RTL);install_text_r(cr,t,"· אפשר להדליק בקונסולה",13,500,r.x+r.w-180-off.w-6,r.y+12,r.w-286-off.w,t->warning_ink);text_free(&off);}
    else{char at[32],size[32],kept[48];bidi_isolate(s->backup_time,at,sizeof at);snprintf(size,sizeof size,"%s GB",install_backup_gb(s));bidi_isolate(size,kept,sizeof kept);snprintf(value,sizeof value,RLM "%s שמורים על כונן השרת · יומי ב-%s ובכל שינוי אימג׳",kept,at);summary_row(a,cr,(Rect){x+1,y+241+dy,w-2,48},"גיבוי המסד",value,5);}   /* #1956: "שינוי" -> step 6 */
    snprintf(value,sizeof value,"%s · הסיסמה הוגדרה",s->admin_user);summary_row(a,cr,(Rect){x+1,y+289+dy,w-2,48},"משתמש הניהול",value,6);
    summary_row(a,cr,(Rect){x+1,y+337+dy,w-2,48},"סיסמת root","נקבעה",6);   /* #1632: that it was set, never the value; "שינוי" -> the admin step */
    console_guess(s,url,sizeof url);install_text_r(cr,t,"הקונסולה תהיה ב־",12,500,x+w-16,y+402+dy,160,t->muted);install_text_ltr(cr,url,13,500,x+24,y+402+dy,w-210,t->indigo);y+=436+dy;
    Rect seq={x,y,fmin(760,w),112};install_card(cr,t,seq);install_text_r(cr,t,"מה יקרה בלחיצה על \"החל התקנה\"",14,600,seq.x+seq.w-16,y+14,seq.w-32,t->ink);
    install_text_r(cr,t,"מחיקת הדיסק ← מערכת בסיס ← חבילות ImageSrv ← מנהל אתחול ← הגדרות",13,500,seq.x+seq.w-16,y+50,seq.w-32,t->ink);
    char after[192],secs[32];he_secs(INSTALL_AUTO_REBOOT_S,secs,sizeof secs);snprintf(after,sizeof after,"בסיום תתבקש להסיר את מדיית ההתקנה; השרת יופעל מחדש אוטומטית אחרי %s.",secs);install_text_r(cr,t,after,12,400,seq.x+seq.w-16,y+78,seq.w-32,t->muted);
    {char warn[512];if(clock_warning(s,warn,sizeof warn)){   /* #2869: orange, never blocking */
        Text tw=text_make(cr,FONT_SANS,13,500,warn,(int)(seq.w-32),DIR_RTL);Rect box={x,y+seq.h+12,seq.w,tw.h+20};
        draw_fill_rrect(cr,box,t->radius,t->warning_soft);draw_border_rrect(cr,box,t->radius,t->warn,1);
        text_draw_r(cr,&tw,box.x+box.w-16,box.y+10,t->warning_ink);text_free(&tw);}}
    /* #2097: destructive = red button, no typed word (#1270) */
    install_footer_style(a,cr,l,"החל התקנה",HIT_INSTALL_NEXT,1,1,BTN_DANGER);
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

/* #2332: the arrow is a cairo triangle, not "▲"/"▼" -- the ISO packs IBM Plex
 * alone, and the live installer drew both as an empty box. 10px wide, 6 tall. */
static void arrow(cairo_t *cr,double cx,double cy,int up,Rgb c){
    double tip=up?-3:3;cairo_move_to(cr,cx-5,cy-tip);cairo_line_to(cr,cx+5,cy-tip);cairo_line_to(cr,cx,cy+tip);cairo_close_path(cr);draw_set(cr,c);cairo_fill(cr);
}

static void output_box(App *a,cairo_t *cr,Rect r){
    InstallState *s=&a->install;const Theme *t=a->theme;draw_fill_rrect(cr,r,t->radius,t->sunken);draw_border_rrect(cr,r,t->radius,t->hair,1);
    char copy[INSTALL_OUTPUT_MAX];snprintf(copy,sizeof copy,"%s",s->output);char *lines[64],*save=NULL;int n=0;
    for(char *p=strtok_r(copy,"\n",&save);p&&n<64;p=strtok_r(NULL,"\n",&save))lines[n++]=p;
    int start=s->output_scroll;if(start>n-1)start=n?n-1:0;
    /* #2097: a long line wraps; the next one starts below all of it (it was drawn
     * 20px down, on top of the wrapped part). Still 9 rows of 20px in the box. */
    int used=0;
    for(int i=start;i<n&&used<9;i++){
        Text q=text_make(cr,FONT_MONO,11,400,lines[i],(int)(r.w-48),DIR_LTR);int rows=pango_layout_get_line_count(q.layout);
        if(used&&used+rows>9){text_free(&q);break;}
        text_draw(cr,&q,r.x+12,r.y+10+used*20,t->ink);text_free(&q);used+=rows;
    }
    arrow(cr,r.x+r.w-16,r.y+21,1,t->muted);arrow(cr,r.x+r.w-16,r.y+r.h-19,0,t->muted);
    hit_add(a,(Rect){r.x+r.w-36,r.y,36,r.h/2},HIT_INSTALL_OUTPUT_UP);hit_add(a,(Rect){r.x+r.w-36,r.y+r.h/2,36,r.h/2},HIT_INSTALL_OUTPUT_DOWN);
}

static void details_toggle(App *a,cairo_t *cr,Rect r){
    InstallState *s=&a->install;const Theme *t=a->theme;Text q=text_make(cr,FONT_SANS,13,600,"פרטים טכניים",(int)r.w,DIR_RTL);text_draw_r(cr,&q,r.x+r.w,r.y,t->indigo);
    arrow(cr,r.x+r.w-q.w-13,r.y+q.h/2.0,s->technical_open,t->indigo);text_free(&q);hit_add(a,r,HIT_INSTALL_TECHNICAL);install_focus_ring(a,cr,r,HIT_INSTALL_TECHNICAL);   /* #2332: the arrow after the words, as "  ▲" was */
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
        {"verify","אימות ההתקנה נכשל."},{"validation","ההגדרות לא עברו בדיקה."},
        {"unmount","שחרור דיסק היעד בסוף ההתקנה נכשל."}};   /* #2097: unmount-failed/-unchecked */
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
    Text retry=btn_label(cr,"נסה שוב");double rw=btn_width(&retry);draw_btn(a,cr,(Rect){l.footer.w-32-rw,l.footer.y+(l.footer.h-N_BUTTON_H)/2,rw,N_BUTTON_H},&retry,BTN_PRIMARY,HIT_INSTALL_RETRY);install_focus_ring(a,cr,(Rect){l.footer.w-32-rw,l.footer.y+(l.footer.h-N_BUTTON_H)/2,rw,N_BUTTON_H},HIT_INSTALL_RETRY);text_free(&retry);
}

/* #1250: the auto restart, below the "remove the media" card so that line
 * stays the first thing on the screen. "בטל" (or Esc) stops the count and
 * leaves the restart button as it was. */
static void reboot_countdown(App *a,cairo_t *cr,Rect r){
    InstallState *s=&a->install;const Theme *t=a->theme;char line[128];
    if(s->reboot_at<=0){install_text_r(cr,t,"ההפעלה מחדש האוטומטית בוטלה. לחץ 'הפעל מחדש' כשתסיים.",13,500,r.x+r.w-20,r.y+18,r.w-40,t->muted);return;}
    draw_fill_rrect(cr,r,t->radius,t->indigo_soft);draw_border_rrect(cr,r,t->radius,t->indigo,1);
    {char secs[32];he_secs(s->reboot_left,secs,sizeof secs);snprintf(line,sizeof line,"הפעלה מחדש אוטומטית בעוד %s",secs);}install_text_r(cr,t,line,15,600,r.x+r.w-20,r.y+18,r.w-160,t->ink);
    Text c=btn_label(cr,"בטל");double cw=btn_width(&c);draw_btn(a,cr,(Rect){r.x+10,r.y+10,cw,N_BUTTON_H},&c,BTN_PLAIN,HIT_INSTALL_CANCEL_REBOOT);install_focus_ring(a,cr,(Rect){r.x+10,r.y+10,cw,N_BUTTON_H},HIT_INSTALL_CANCEL_REBOOT);text_free(&c);
}

/* #2144 (mockup 2144-time, Nadav 04/11): what the engine's clock check found
 * -- green with its evidence, orange when the time server did not answer,
 * orange in other words when the check itself gave no answer (principle 5).
 * The automatic restart goes on in all three. Returns the hero's height. */
static double clock_hero(App *a,cairo_t *cr,double x,double y,double w){
    const InstallState *s=&a->install;const Theme *t=a->theme;
    if(s->clock==INSTALL_CLOCK_SYNCED){Rect hero={x,y,w,92};draw_fill_rrect(cr,hero,t->radius,t->success_soft);
        install_text_r(cr,t,"✓",42,600,x+w-24,y+18,54,t->success_ink);install_text_r(cr,t,"ההתקנה הושלמה.",22,600,x+w-90,y+28,w-115,t->ink);return 92;}
    if(s->clock==INSTALL_CLOCK_MANUAL){Rect hero={x,y,w,92};draw_fill_rrect(cr,hero,t->radius,t->success_soft);   /* #2765: the person's own choice -- not an alert */
        install_text_r(cr,t,"✓",42,600,x+w-24,y+18,54,t->success_ink);install_text_r(cr,t,"ההתקנה הושלמה.",22,600,x+w-90,y+16,w-115,t->ink);
        install_text_r(cr,t,"השעה בשרת נקבעה ידנית, בלי שרת זמן. אפשר לשנות בקונסולה: הגדרות ← מקור השעה.",13,400,x+w-90,y+54,w-115,t->muted);return 92;}
    char server[300],line[700];bidi_isolate(s->clock_server[0]?s->clock_server:"—",server,sizeof server);
    const char *h2,*one,*two;
    if(s->clock==INSTALL_CLOCK_UNSYNCED){h2="ההתקנה הושלמה — השעון לא סונכרן";
        snprintf(line,sizeof line,"שרת הזמן (%s) לא ענה עד סוף ההתקנה. עד שיענה, השעה בשרת עלולה להיות שגויה, ואיתה הזמנים ביומן, בסבבים ובבדיקת תוקף התעודה.",server);one=line;
        two="אחרי ההפעלה מחדש: לבדוק שהרשת מאפשרת NTP (UDP 123) אל שרת הזמן, או לשנות בקונסולה: הגדרות ← מקור השעה. עד אז מסך השרת יציג \"השעון לא מסונכרן\".";}
    else{h2="ההתקנה הושלמה — לא הצלחנו לבדוק את השעון";one="chrony לא החזיר תשובה, ולכן לא ידוע אם השעון מסונכרן.";
        two="אחרי ההפעלה מחדש: בקונסולה, הגדרות ← מצב השעון ← \"בדוק עכשיו\".";}
    double tw=w-24-42-18-24;
    Text th=text_make(cr,FONT_SANS,22,600,h2,(int)tw,DIR_RTL),t1=text_make(cr,FONT_SANS,13.5,400,one,(int)tw,DIR_RTL),t2=text_make(cr,FONT_SANS,13.5,400,two,(int)tw,DIR_RTL);
    double h=18+th.h+6+t1.h+6+t2.h+18;Rect hero={x,y,w,h};
    draw_fill_rrect(cr,hero,t->radius,t->warning_soft);draw_border_rrect(cr,hero,t->radius,t->warn,1);
    cairo_arc(cr,x+w-24-21,y+18+21,21,0,2*M_PI);draw_set(cr,t->warn);cairo_fill(cr);
    Text g=text_make(cr,FONT_SANS,24,700,"!",0,DIR_RTL);text_draw(cr,&g,x+w-24-21-g.w/2.0,y+18+21-g.h/2.0,t->on_ink);text_free(&g);
    double right=x+w-24-42-18,ty=y+18;text_draw_r(cr,&th,right,ty,t->warning_ink);ty+=th.h+6;text_draw_r(cr,&t1,right,ty,t->ink);ty+=t1.h+6;text_draw_r(cr,&t2,right,ty,t->muted);
    text_free(&th);text_free(&t1);text_free(&t2);return h;
}

/* the card's "שעון השרת" row: a chip, and when synced the evidence beside it */
static void clock_row(App *a,cairo_t *cr,double x,double y,double w){
    const InstallState *s=&a->install;const Theme *t=a->theme;int manual=s->clock==INSTALL_CLOCK_MANUAL,ok=s->clock==INSTALL_CLOCK_SYNCED;
    install_text_r(cr,t,"שעון השרת",12,500,x+w-20,y,150,t->muted);
    const char *word=manual?"ידני":ok?"מסונכרן":s->clock==INSTALL_CLOCK_UNSYNCED?"לא מסונכרן":"לא נבדק";   /* #2765: "ידני" is information, drawn as the calm chip */
    Text c=text_make(cr,FONT_SANS,12,600,word,0,DIR_RTL);double cx=x+w-190;Rect chip={cx-c.w-20,y-3,c.w+20,22};
    draw_fill_rrect(cr,chip,11,manual?t->indigo_soft:ok?t->success_soft:t->warning_soft);text_draw_r(cr,&c,cx-10,y-3+(22-c.h)/2.0,manual?t->ink:ok?t->success_ink:t->warning_ink);text_free(&c);
    if(manual)install_text_r(cr,t,"בלי שרת זמן · הוגדרה בהתקנה",12,400,chip.x-12,y,chip.x-12-x-20,t->ink);
    if(ok){char server[300],proof[400];bidi_isolate(s->clock_server[0]?s->clock_server:"—",server,sizeof server);
        snprintf(proof,sizeof proof,"%s · סטייה %s ms",server,s->clock_offset[0]?s->clock_offset:"?");install_text_r(cr,t,proof,12,400,chip.x-12,y,chip.x-12-x-20,t->ink);}
}

static void done(App *a,cairo_t *cr,InstallLayout l){
    InstallState *s=&a->install;const Theme *t=a->theme;double w=fmin(760,l.body.w),x=l.body.x+l.body.w-w,y=l.body.y;
    y+=clock_hero(a,cr,x,y,w)+20;
    Rect card={x,y,w,262};install_card(cr,t,card);install_text_r(cr,t,"הסר את מדיית ההתקנה (ISO / כונן USB) ולחץ 'הפעל מחדש'",15,600,x+w-20,y+20,w-40,t->ink);
    install_text_r(cr,t,"כתובת הקונסולה",12,500,x+w-20,y+72,190,t->muted);
    if(s->console_url[0])install_text_ltr(cr,s->console_url,18,600,x+20,y+100,w-40,t->indigo);else install_text_r(cr,t,"הכתובת תוצג במסך השרת אחרי האתחול",14,500,x+w-20,y+100,w-40,t->ink);
    install_text_r(cr,t,"משתמש",12,500,x+w-20,y+148,150,t->muted);install_text_ltr(cr,s->user[0]?s->user:s->admin_user,13,500,x+20,y+148,w-190,t->ink);
    clock_row(a,cr,x,y+184,w);   /* #2144 */
    if(s->error[0])install_text_r(cr,t,s->error,12,600,x+w-20,y+222,w-40,t->danger_fg);
    reboot_countdown(a,cr,(Rect){x,y+278,w,58});
    install_footer(a,cr,l,"הפעל מחדש",HIT_INSTALL_NEXT,0,1);
}

void install_draw_final(App *a,cairo_t *cr,InstallLayout l){switch(a->install.view){case INSTALL_SUMMARY:summary(a,cr,l);break;case INSTALL_PROGRESS:progress(a,cr,l);break;case INSTALL_FAILED:failed(a,cr,l);break;default:done(a,cr,l);break;}}
