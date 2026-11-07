#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "screens_install.h"
#include "screens_rounds.h"   /* bidi_isolate */

static void kv(App *a,cairo_t *cr,Rect box,const char *key,const char *value,int ltr){const Theme *t=a->theme;install_text_r(cr,t,key,12,500,box.x+box.w-16,box.y+10,box.w*.38,t->muted);if(ltr)install_text_ltr(cr,value,12,400,box.x+16,box.y+10,box.w*.56,t->ink);else install_text_r(cr,t,value,12,400,box.x+box.w*.6,box.y+10,box.w*.55,t->ink);}

static void hostname(App *a,cairo_t *cr,InstallLayout l){InstallState *s=&a->install;const Theme *t=a->theme;double w=l.body.w,x=l.body.x,y=l.body.y;   /* the one column (mockup 10/10) */
    install_field_draw(a,cr,(Rect){x,y,w,56},"שם השרת (hostname)",s->hostname,0,HIT_INSTALL_HOSTNAME,1);y+=65;
    install_text_r(cr,t,"שם מחשב — באנגלית, עד 15 תווים: אותיות, ספרות ומקפים; לא מתחיל ולא מסתיים במקף.",12,400,x+w,y,w,t->muted);y+=36;
    if(s->field_error[9][0]){install_text_r(cr,t,s->field_error[9],12,600,x+w,y,w,t->danger_fg);y+=30;}
    /* #1447 (Nadav 17/10): the server's timezone -- the same field as the name above */
    install_field_draw(a,cr,(Rect){x,y,w,56},"אזור זמן",s->timezone,0,HIT_INSTALL_TIMEZONE,1);y+=65;
    if(s->field_error[14][0]){install_text_r(cr,t,s->field_error[14],12,600,x+w,y,w,t->danger_fg);y+=30;}
    else{install_text_r(cr,t,"שם אזור של tzdata באנגלית, למשל Asia/Jerusalem. השעה במסך השרת ובמסכי התחנות; אפשר לשנות בקונסולה.",12,400,x+w,y,w,t->muted);y+=36;}
    Rect card={x,y,w,190};install_card(cr,t,card);install_text_r(cr,t,"איפה השם יופיע",15,600,x+w-16,y+14,w-32,t->ink);y+=48;
    char hostline[128];snprintf(hostline,sizeof hostline,"Hostname: %s",s->hostname);kv(a,cr,(Rect){x+1,y,w-2,34},"מסך השרת (DCUI)",hostline,1);y+=34;
    kv(a,cr,(Rect){x+1,y,w-2,34},"הקונסולה","כותרת העץ ושורת הסטטוס",0);y+=34;
    kv(a,cr,(Rect){x+1,y,w-2,34},"אצל הראשי","שרתי משנה — אחרי הצימוד",0);y+=34;
    kv(a,cr,(Rect){x+1,y,w-2,34},"תעודת TLS","נכנס ל-SAN יחד עם הכתובת",0);
    install_footer(a,cr,l,"הבא",HIT_INSTALL_NEXT,1,s->hostname[0]!=0&&s->timezone[0]!=0);
}

static void rule(App *a,cairo_t *cr,double right,double y,const char *label,int ok){const Theme *t=a->theme;Rgb c=ok?t->success_ink:t->muted;cairo_arc(cr,right-7,y+8,7,0,6.283);draw_set_a(cr,c,ok?1:.35);cairo_fill(cr);Text mark=text_make(cr,FONT_SANS,11,600,ok?"✓":"×",0,DIR_RTL);text_draw(cr,&mark,right-7-mark.w/2,y+8-mark.h/2,t->on_ink);text_free(&mark);install_text_r(cr,t,label,12,500,right-22,y,180,c);}

static void password_field(App *a,cairo_t *cr,Rect r,const char *label,const char *value,int field_id,int show_id,int shown){const Theme *t=a->theme;install_field_draw(a,cr,r,label,value,!shown,field_id,1);Text eye=text_make(cr,FONT_SANS,12,400,shown?"הסתר":"הצג",0,DIR_RTL);Rect er={r.x+r.w-eye.w-8,r.y+25,eye.w+8,24};text_draw(cr,&eye,er.x+4,er.y+(er.h-eye.h)/2,t->action_fg);hit_add(a,er,show_id);text_free(&eye);}

static void admin_screen(App *a,cairo_t *cr,InstallLayout l){InstallState *s=&a->install;const Theme *t=a->theme;double w=l.body.w,x=l.body.x,y=l.body.y;
    install_field_draw(a,cr,(Rect){x,y,w,56},"שם משתמש",s->admin_user,0,HIT_INSTALL_ADMIN_USER,1);y+=62;
    if(s->field_error[10][0]){install_text_r(cr,t,s->field_error[10],11,600,x+w,y,w,t->danger_fg);y+=22;}
    else{install_text_r(cr,t,"אותיות קטנות באנגלית, ספרות והסימנים . _ -",11,400,x+w,y,w,t->muted);y+=22;}
    if(s->rerun){install_field_draw(a,cr,(Rect){x,y,w,56},"סיסמת admin הקיימת",s->current_password,1,HIT_INSTALL_CURRENT_PASSWORD,1);y+=68;}
    password_field(a,cr,(Rect){x,y,w,56},"סיסמה",s->password,HIT_INSTALL_PASSWORD,HIT_INSTALL_SHOW_PASSWORD,s->show_password);y+=66;
    int letter,digit,special;install_password_rules(s->password,&letter,&digit,&special);
    rule(a,cr,x+w,y,"לפחות 8 תווים",strlen(s->password)>=8);rule(a,cr,x+w-190,y,"אותיות",letter);y+=28;rule(a,cr,x+w,y,"ספרות",digit);rule(a,cr,x+w-190,y,"תו מיוחד",special);y+=42;
    password_field(a,cr,(Rect){x,y,w,56},"אימות הסיסמה",s->confirm,HIT_INSTALL_CONFIRM,HIT_INSTALL_SHOW_CONFIRM,s->show_confirm);y+=63;
    if(s->confirm[0]&&strcmp(s->password,s->confirm))install_text_r(cr,t,"הסיסמאות שהזנת אינן תואמות.",12,600,x+w,y,w,t->danger_fg);
    else if(s->field_error[11][0]||s->field_error[12][0])install_text_r(cr,t,s->field_error[s->field_error[12][0]?12:11],12,600,x+w,y,w,t->danger_fg);
    /* #1632 (Nadav 24/10): root's password -- the local login on the server's
     * own screen -- is chosen here, under the admin's rules; the ISO has none. */
    y+=34;password_field(a,cr,(Rect){x,y,w,56},"סיסמת root (כניסה מקומית למסך השרת)",s->root_password,HIT_INSTALL_ROOT_PASSWORD,HIT_INSTALL_SHOW_ROOT,s->show_root);y+=66;
    install_password_rules(s->root_password,&letter,&digit,&special);
    rule(a,cr,x+w,y,"לפחות 8 תווים",strlen(s->root_password)>=8);rule(a,cr,x+w-190,y,"אותיות",letter);rule(a,cr,x+w-380,y,"ספרות",digit);rule(a,cr,x+w-570,y,"תו מיוחד",special);y+=42;
    password_field(a,cr,(Rect){x,y,w,56},"אימות סיסמת root",s->root_confirm,HIT_INSTALL_ROOT_CONFIRM,HIT_INSTALL_SHOW_ROOT_CONFIRM,s->show_root_confirm);y+=63;
    if(s->root_confirm[0]&&strcmp(s->root_password,s->root_confirm))install_text_r(cr,t,"סיסמאות ה-root שהזנת אינן תואמות.",12,600,x+w,y,w,t->danger_fg);
    else if(s->field_error[15][0]||s->field_error[16][0])install_text_r(cr,t,s->field_error[s->field_error[16][0]?16:15],12,600,x+w,y,w,t->danger_fg);
    install_footer(a,cr,l,"הבא",HIT_INSTALL_NEXT,1,install_password_ready(s)&&(!s->rerun||s->current_password[0]));
}

/* #2144 (mockup 2144-time, Nadav 04/11): step 5, the console's choice (#1773,
 * הגדרות ← מקור השעה) -- automatic or the organisation's server -- and the time
 * the server has now, from its hardware clock; the sync is checked at the end. */
void install_draw_time(App *a,cairo_t *cr,InstallLayout l){InstallState *s=&a->install;const Theme *t=a->theme;double w=l.body.w,x=l.body.x,y=l.body.y;
    install_text_r(cr,t,"שרת זמן (NTP)",12,500,x+w,y,w,t->ink);y+=24;
    install_radio(a,cr,(Rect){x,y,w,74},"אוטומטי (שרתי זמן באינטרנט)","שרתי הזמן של Debian. צריך גישה לאינטרנט ב-UDP 123.",!s->ntp_org,HIT_INSTALL_NTP_AUTO);y+=86;
    install_radio(a,cr,(Rect){x,y,w,74},"שרת זמן של הארגון","כשרשת המכללה חוסמת NTP לאינטרנט.",s->ntp_org,HIT_INSTALL_NTP_ORG);y+=90;
    if(s->ntp_org){install_field_draw(a,cr,(Rect){x,y,w,56},"כתובות (IP או שם, מופרדות ברווח)",s->ntp_servers,0,HIT_INSTALL_NTP_SERVERS,1);y+=62;
        if(s->field_error[17][0]){install_text_r(cr,t,s->field_error[17],12,600,x+w,y,w,t->danger_fg);y+=30;}else y+=8;}
    char when[64],zone[64];install_clock_now(s,when,sizeof when,zone,sizeof zone);
    Rect card={x,y,w,92};install_card(cr,t,card);
    Text lab=text_make(cr,FONT_SANS,12.5,400,"השעה עכשיו בשרת",0,DIR_RTL);text_draw_r(cr,&lab,x+w-16,y+18,t->muted);
    double right=x+w-16-lab.w-12;text_free(&lab);
    Text tm=text_make(cr,FONT_MONO,15,600,when,0,DIR_LTR);text_draw(cr,&tm,right-tm.w,y+15,t->ink);right-=tm.w+12;text_free(&tm);
    Text zn=text_make(cr,FONT_SANS,12.5,400,zone,0,DIR_LTR);text_draw(cr,&zn,fmax(x+16,right-zn.w),y+18,t->muted);text_free(&zn);
    install_text_r(cr,t,"לפי שעון החומרה, עוד בלי סנכרון. הסנכרון נבדק בסוף ההתקנה.",12,400,x+w-16,y+54,w-32,t->muted);
    install_footer(a,cr,l,"הבא",HIT_INSTALL_NEXT,1,install_time_ready(s));
}

/* #1956 (mockup 1956-installer-backup, Nadav 06/11): step 6. No destination: the
 * backups stay on the server's drive, in space kept for them; the hour is the
 * operator's. The rules (20% of the drive, HH:MM) are the engine's -- backup.sh
 * answers through the bridge, and its line takes the hint's place. */
static double note(App *a,cairo_t *cr,double x,double y,double w,const char *markup,Rgb fill,Rgb line,Rgb ink,int mark){const Theme *t=a->theme;double inset=mark?44:16;
    Text m=text_make_markup(cr,FONT_SANS,12.5,400,markup,(int)(w-inset-16),DIR_RTL);double h=m.h+18;Rect box={x,y,w,h};draw_fill_rrect(cr,box,t->radius,fill);draw_border_rrect(cr,box,t->radius,line,1);
    if(mark){cairo_arc(cr,x+w-24,y+h/2,9,0,6.283);draw_set(cr,line);cairo_fill(cr);Text e=text_make(cr,FONT_SANS,12,700,"!",0,DIR_RTL);text_draw(cr,&e,x+w-24-e.w/2,y+h/2-e.h/2,t->on_ink);text_free(&e);}
    text_draw_r(cr,&m,x+w-inset,y+9,ink);text_free(&m);return h;}
static void size_option(App *a,cairo_t *cr,Rect r,const char *title,const char *desc,int selected,int id){const Theme *t=a->theme;draw_fill_rrect(cr,r,t->radius,selected?t->indigo_soft:t->surface);draw_border_rrect(cr,r,t->radius,selected?t->indigo:t->hair,selected?2:1);double cx=r.x+r.w-21,cy=r.y+18;cairo_arc(cr,cx,cy,8,0,2*M_PI);draw_set(cr,selected?t->indigo:t->hair);cairo_set_line_width(cr,2);cairo_stroke(cr);if(selected){cairo_arc(cr,cx,cy,4,0,2*M_PI);draw_set(cr,t->indigo);cairo_fill(cr);}install_text_r(cr,t,title,13,600,r.x+r.w-38,r.y+9,r.w-52,t->ink);install_text_r(cr,t,desc,12,400,r.x+r.w-38,r.y+29,r.w-52,t->muted);install_focus_ring(a,cr,r,id);hit_add(a,r,id);}
void install_draw_backup(App *a,cairo_t *cr,InstallLayout l){InstallState *s=&a->install;const Theme *t=a->theme;double w=l.body.w,x=l.body.x,y=l.body.y,fw=w/2-10;char at[32],when[320];
    bidi_isolate(s->backup_time[0]&&!strpbrk(s->backup_time,"<&>")?s->backup_time:"—",at,sizeof at);
    snprintf(when,sizeof when,"<b>מתי מגבים:</b> פעם ביום ב-%s (אפשר לשנות מתחת), ובנוסף מיד בכל העלאה של אימג׳ חדש ובכל מחיקה של אימג׳ מהשרת.",at);
    y+=note(a,cr,x,y,w,when,t->indigo_soft,t->indigo,t->ink,0)+12;
    int other=s->backup_pick==INSTALL_BACKUP_OTHER;
    size_option(a,cr,(Rect){x,y,w,54},"5 GB",other?"מוצע.":"מוצע. מספיק לגיבויים רבים של המסד, ומשאיר כמעט את כל הכונן לאימג׳ים.",s->backup_pick==0,HIT_INSTALL_BACKUP_BASE);y+=63;
    size_option(a,cr,(Rect){x,y,w,54},"10 GB","יותר היסטוריה.",s->backup_pick==1,HIT_INSTALL_BACKUP_BASE+1);y+=63;
    size_option(a,cr,(Rect){x,y,w,54},"20 GB","היסטוריה ארוכה.",s->backup_pick==2,HIT_INSTALL_BACKUP_BASE+2);y+=63;
    size_option(a,cr,(Rect){x,y,w,54},"גודל אחר","הקלידו מספר ב-GB.",other,HIT_INSTALL_BACKUP_BASE+3);y+=66;
    if(other){install_field_draw(a,cr,(Rect){x+w-fw,y,fw,56},"מקום שמור (GB)",s->backup_other,0,HIT_INSTALL_BACKUP_OTHER_GB,1);y+=68;}
    install_field_draw(a,cr,(Rect){x+w-fw,y,fw,56},"שעת הגיבוי היומי",s->backup_time,0,HIT_INSTALL_BACKUP_TIME,1);y+=62;
    if(s->field_error[19][0])install_text_r(cr,t,s->field_error[19],12,600,x+w,y,w,t->danger_fg);else install_text_r(cr,t,"מוצע 02:00: שעה בלי סבבים. אם רץ סבב באותה שעה, הגיבוי מחכה לסיומו.",12,400,x+w,y,w,t->muted);y+=26;
    const InstallDisk *d=s->disk>=0&&s->disk<s->ndisks?&s->disks[s->disk]:NULL;double disk_gb=d?(double)d->size_bytes/1e9:0,gb=atof(install_backup_gb(s));
    double share=disk_gb>0?fmin(1,fmax(.01,gb/disk_gb)):.01;Rect bar={x,y,w,12};draw_fill_rrect(cr,bar,6,t->track);cairo_rectangle(cr,x+w*(1-.01-share),y,w*share,12);draw_set(cr,t->warning_line);cairo_fill(cr);cairo_rectangle(cr,x+w*.99,y,w*.01,12);draw_set(cr,t->indigo);cairo_fill(cr);y+=20;
    char mine[32],all[32],kept[48],whole[48],room[220];snprintf(mine,sizeof mine,"%s GB",install_backup_gb(s));snprintf(all,sizeof all,"%.0f GB",disk_gb);bidi_isolate(mine,kept,sizeof kept);bidi_isolate(all,whole,sizeof whole);   /* else "5 GB" reads "GB 5" in the Hebrew line */
    snprintf(room,sizeof room,"שמורים לגיבויים %s מתוך %s פנויים בכונן",kept,whole);install_text_r(cr,t,room,12,400,x+w,y,w,t->muted);y+=24;
    if(s->field_error[18][0]){install_text_r(cr,t,s->field_error[18],12,600,x+w,y,w,t->danger_fg);y+=24;}else if(other){install_text_r(cr,t,"מינימום 1 GB. לא יותר מ-20% מהכונן, כדי שיישאר מקום לאימג׳ים.",12,400,x+w,y,w,t->muted);y+=24;}
    note(a,cr,x,y,w,"<b>אותו כונן, לא מגן מתקלת כונן.</b> הגיבוי עוזר נגד מחיקה או שחיתות של המסד, לא נגד מות הדיסק. עותק על כונן אחר אפשר להגדיר אחר כך בקונסולה.",t->warning_bg,t->warning_line,t->warning_ink,1);
    install_footer(a,cr,l,"הבא",HIT_INSTALL_NEXT,1,install_backup_ready(s));
}

void install_draw_forms(App *a,cairo_t *cr,InstallLayout l){if(a->install.view==INSTALL_HOSTNAME)hostname(a,cr,l);else admin_screen(a,cr,l);}
