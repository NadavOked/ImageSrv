#include <math.h>
#include <stdio.h>
#include <string.h>
#include "screens_install.h"

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

void install_draw_forms(App *a,cairo_t *cr,InstallLayout l){if(a->install.view==INSTALL_HOSTNAME)hostname(a,cr,l);else admin_screen(a,cr,l);}
