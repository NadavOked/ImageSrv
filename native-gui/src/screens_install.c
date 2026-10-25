#include <math.h>
#include <stdio.h>
#include <string.h>
#include "screens_install.h"
#include "install_raid.h"     /* #1989 */

static const char *STEP_NAMES[]={"דיסק","תפקיד השרת","כתובת השרת","שם השרת","חשבון ניהול","החלה","סיום"};

void install_text_r(cairo_t *cr,const Theme *t,const char *v,double px,int weight,double right,double y,double width,Rgb color){(void)t;Text x=text_make(cr,FONT_SANS,px,weight,v,(int)width,DIR_RTL);text_draw_r(cr,&x,right,y,color);text_free(&x);}
void install_text_ltr(cairo_t *cr,const char *v,double px,int weight,double x,double y,double width,Rgb color){Text q=text_make(cr,FONT_MONO,px,weight,v,(int)width,DIR_LTR);text_draw(cr,&q,x,y,color);text_free(&q);}
void install_card(cairo_t *cr,const Theme *t,Rect r){draw_fill_rrect(cr,r,t->radius,t->surface);draw_border_rrect(cr,r,t->radius,t->hair,1);}

static int current_step(const InstallState *s){if(s->view==INSTALL_PROGRESS||s->view==INSTALL_FAILED)return 6;if(s->view==INSTALL_DONE)return 7;return s->view;}

/* mockup 10/10 ("מתקין"): one column of one width on every step, centred in
 * the page -- it was 1556 / 760 / 720 / 650 wide, step by step. */
#define INSTALL_COL_W 760.0
InstallLayout install_frame(App *a,cairo_t *cr,double W,double H){const Theme *t=a->theme;double rw=W<1500?250:300,cw=W-rw,col=fmin(INSTALL_COL_W,cw-64),cx=(cw-col)/2;InstallLayout l={{0,0,cw,H},{cx,105,col,H-185},{0,H-64,cw,64},{W-rw,0,rw,H},cx+col};
    draw_fill_rrect(cr,(Rect){0,0,W,H},0,t->porcelain);draw_fill_rrect(cr,l.rail,0,t->header);
    draw_brand_lockup_r(cr,l.rail.x+l.rail.w-28,28+(30-brand_symbol_height(5))/2,5,1,0);   /* #1302: the rail is the header colour */
    install_text_r(cr,t,"התקנת השרת",13,400,l.rail.x+l.rail.w-28,72,l.rail.w-56,t->header_text);
    int cur=current_step(&a->install);double y=124;
    /* ui-audit: future steps were t->muted (#565656 in the light theme) on the #25333D rail, 1.8:1; a mix of the rail's own text colour reads on both themes. */
    Rgb dim={t->header_text.r*.62+t->header.r*.38,t->header_text.g*.62+t->header.g*.38,t->header_text.b*.62+t->header.b*.38};
    for(int i=0;i<7;i++,y+=65){int done=i+1<cur,active=i+1==cur,bad=a->install.view==INSTALL_FAILED&&i==5;Rgb c=bad?t->danger:(done?t->success_ink:(active?t->header_text:dim));
        /* mockup 10/10: the current step is a disc in the action colour with
         * its number on it, a done step a green disc with a tick, a future
         * step an outline with its number in the dim rail text (6:1). */
        int filled=active||done||bad;Rgb disc=bad?t->danger:(done?t->success_ink:t->indigo);
        if(filled){cairo_arc(cr,l.rail.x+l.rail.w-45,y,15,0,2*M_PI);draw_set(cr,disc);cairo_fill(cr);}
        else{cairo_arc(cr,l.rail.x+l.rail.w-45,y,14,0,2*M_PI);draw_set(cr,dim);cairo_set_line_width(cr,2);cairo_stroke(cr);}
        char num[8];snprintf(num,sizeof num,done?"✓":"%d",i+1);Text n=text_make(cr,FONT_SANS,12,600,num,0,DIR_RTL);text_draw(cr,&n,l.rail.x+l.rail.w-45-n.w/2,y-n.h/2,filled?t->on_ink:dim);text_free(&n);
        install_text_r(cr,t,STEP_NAMES[i],14,active?600:400,l.rail.x+l.rail.w-70,y-9,l.rail.w-95,c);
        if(i<6){cairo_rectangle(cr,l.rail.x+l.rail.w-46,y+17,2,31);draw_set_a(cr,done?t->success_ink:dim,.6);cairo_fill(cr);}
    }
    const char *titles[]={"","בחירת דיסק להתקנה","תפקיד השרת במערכת","כתובת השרת","שם השרת","חשבון הניהול","סיכום לפני החלה","סיום ההתקנה","מתקין את ImageSrv…","ההתקנה נכשלה"};
    const char *subs[]={"","הדיסק שנבחר יימחק וישמש את שרת ImageSrv.","התפקיד נבחר בתפריט ההתקנה. אפשר לאשר או לשנות לפני ההחלה.","הכרטיס שדרכו מגיעים לקונסולה; רשת ההפצה מוגדרת אחר כך מהקונסולה","השם שיופיע בקונסולה, במסך השרת ובתעודת TLS.","המשתמש הראשון בקונסולה. משתמשים נוספים מוגדרים אחרי ההתקנה.","מה שיוחל על השרת. כל שורה ניתנת לשינוי.","הסר את מדיית ההתקנה והפעל מחדש.","אל תכבה את השרת בזמן ההתקנה.","מה נכשל כתוב למטה; הפלט המלא בפרטים הטכניים."};
    install_text_r(cr,t,titles[a->install.view],26,600,l.right,28,l.body.w,t->ink);install_text_r(cr,t,subs[a->install.view],14,400,l.right,65,l.body.w,t->muted);if(a->install.error[0]&&a->install.view!=INSTALL_FAILED&&a->install.view!=INSTALL_PROGRESS)install_text_r(cr,t,a->install.error,11,600,l.right,87,l.body.w,t->danger_fg);
    cairo_rectangle(cr,0,l.footer.y,l.footer.w,1);draw_set(cr,t->hair);cairo_fill(cr);draw_fill_rrect(cr,l.footer,0,t->surface);return l;}

void install_field_draw(App *a,cairo_t *cr,Rect r,const char *label,const char *value,int mask,int id,int enabled){const Theme *t=a->theme;install_text_r(cr,t,label,12,500,r.x+r.w,r.y,r.w,enabled?t->ink:t->muted);   /* .win .n-f>label: --clr-text, 12px 500 */Rect f={r.x,r.y+19,r.w,36};draw_field_box(cr,t,f,a->focus==id&&enabled);   /* .win .n-in and :focus (border + 3px ring) */
    char shown[300];if(mask&&value[0]){size_t k=0;for(const char *p=value;*p&&k+4<sizeof shown;p++)if((*p&0xc0)!=0x80){memcpy(shown+k,"\xe2\x80\xa2",3);k+=3;}shown[k]=0;}else snprintf(shown,sizeof shown,"%s",value);
    Text text=text_make(cr,FONT_MONO,13,400,shown,(int)f.w-24,DIR_LTR);double ty=f.y+(f.h-text.h)/2;text_draw(cr,&text,f.x+12,ty,enabled?t->ink:t->muted);if(enabled&&a->focus==id){double caret=f.x+12+text.w+1;cairo_rectangle(cr,caret,ty,1,text.h);draw_set(cr,t->indigo);cairo_fill(cr);}text_free(&text);if(enabled)hit_add(a,f,id);}

void install_radio(App *a,cairo_t *cr,Rect r,const char *title,const char *desc,int selected,int id){const Theme *t=a->theme;draw_fill_rrect(cr,r,t->radius,selected?t->indigo_soft:t->surface);draw_border_rrect(cr,r,t->radius,selected?t->indigo:t->hair,selected?2:1);double cx=r.x+r.w-24,cy=r.y+23;cairo_arc(cr,cx,cy,8,0,2*M_PI);draw_set(cr,selected?t->indigo:t->hair);cairo_set_line_width(cr,2);cairo_stroke(cr);if(selected){cairo_arc(cr,cx,cy,4,0,2*M_PI);draw_set(cr,t->indigo);cairo_fill(cr);}install_text_r(cr,t,title,15,600,r.x+r.w-44,r.y+12,r.w-58,t->ink);install_text_r(cr,t,desc,12,400,r.x+r.w-20,r.y+42,r.w-40,t->muted);install_focus_ring(a,cr,r,id);hit_add(a,r,id);}

/* #1206: keyboard focus, drawn outside the control like the console's
 * :focus outline -- a selected radio already has an indigo border, so focus
 * needs a mark of its own to tell "chosen" from "where the arrows are". */
void install_focus_ring(App *a,cairo_t *cr,Rect r,int id){if(a->focus!=id)return;draw_border_rrect(cr,(Rect){r.x-4,r.y-4,r.w+8,r.h+8},a->theme->radius+4,a->theme->indigo,2);}

static void disabled_btn(cairo_t *cr,const Theme *t,Rect r,const char *label){draw_fill_rrect(cr,r,t->radius_sm,t->track);Text q=btn_label(cr,label);   /* .btn:disabled: row-border fill, muted words, no border */text_draw(cr,&q,r.x+(r.w-q.w)/2,r.y+(r.h-q.h)/2,t->muted);text_free(&q);}
void install_footer(App *a,cairo_t *cr,InstallLayout l,const char *primary,int primary_id,int allow_back,int enabled){const Theme *t=a->theme;double y=l.footer.y+(l.footer.h-N_BUTTON_H)/2;Text p=btn_label(cr,primary);double pw=fmax(118,btn_width(&p));Rect pr={l.footer.w-32-pw,y,pw,N_BUTTON_H};if(enabled)draw_btn(a,cr,pr,&p,BTN_PRIMARY,primary_id);else disabled_btn(cr,t,pr,primary);text_free(&p);if(allow_back){Text b=btn_label(cr,"חזור");double bw=btn_width(&b);draw_btn(a,cr,(Rect){pr.x-bw-12,y,bw,N_BUTTON_H},&b,BTN_PLAIN,HIT_INSTALL_BACK);text_free(&b);}}

/* mockup 10/10: the footer says which keys work on this step -- they all
 * did (install.c), and nothing said so; "שלב N מתוך 7" repeated the rail.
 * Right-aligned clear of "הבא" (118) and "חזור" (up to 100), on every step. */
static const char *install_keys(const InstallState *s){
    switch(s->view){
    case INSTALL_DISK:return install_raid_offered(s)?"↑↓  בחירת דיסק    ←→  RAID    רווח  אני מבין    Enter  הבא":"↑↓  בחירת דיסק    רווח  אני מבין    Enter  הבא";
    case INSTALL_ROLE:return "↑↓ / Tab  מעבר    Enter  הבא    Esc  חזור";
    case INSTALL_NETWORK:return "↑↓ / Tab  מעבר    רווח  DHCP / סטטי    Enter  הבא    Esc  חזור";
    case INSTALL_HOSTNAME:case INSTALL_ADMIN:return "Tab / Shift-Tab  מעבר    Enter  הבא    Esc  חזור";
    case INSTALL_SUMMARY:return "Enter  החל התקנה    Esc  חזור";
    case INSTALL_PROGRESS:return s->technical_open?"רווח  פרטים טכניים    ↑↓  גלילה":"רווח  פרטים טכניים";
    case INSTALL_FAILED:return "Enter  נסה שוב    ↑↓  גלילה    Esc  חזרה לבחירת דיסק";
    default:return "Enter  הפעל מחדש    Esc  בטל הפעלה אוטומטית";
    }
}
static void install_keys_draw(App *a,cairo_t *cr,InstallLayout l){
    double right=l.footer.w-32-118-12-100;if(right<120)return;
    Text k=text_make(cr,FONT_SANS,12,400,install_keys(&a->install),(int)(right-32),DIR_RTL);
    pango_layout_set_alignment(k.layout,PANGO_ALIGN_RIGHT);
    text_draw_r(cr,&k,right,l.footer.y+(l.footer.h-k.h)/2,a->theme->muted);text_free(&k);}

void screen_install(App *a,cairo_t *cr,double W,double H){InstallLayout l=install_frame(a,cr,W,H);switch(a->install.view){case INSTALL_DISK:install_draw_disk(a,cr,l);break;case INSTALL_ROLE:install_draw_role(a,cr,l);break;case INSTALL_NETWORK:install_draw_network(a,cr,l);break;case INSTALL_HOSTNAME:case INSTALL_ADMIN:install_draw_forms(a,cr,l);break;default:install_draw_final(a,cr,l);break;}install_keys_draw(a,cr,l);}

/* #1310: the role step in its three states -- green (the demo), not yet
 * checked (next disabled), and a real failure with its reason. */
static void role_variant(InstallState *s,const char *name){if(strstr(name,"-unchosen")){s->disk=-1;s->disk_ack=0;return;}   /* #2147: two disks, none chosen yet */
    if(strstr(name,"-unchecked")){s->primary_checked=s->primary_ok=0;}
    else if(strstr(name,"-failed")){s->primary_checked=1;s->primary_ok=0;snprintf(s->primary_error,sizeof s->primary_error,"אין תשובה מ-192.168.68.250 בפורט 8443 תוך 5 שניות (timeout): חומת אש חוסמת בשקט, או שהכתובת שגויה");}}
int install_render_png(App *a,const char *prefix,int w,int h){static const struct{const char *name;int view;} cards[]={{"install-0-disk",INSTALL_DISK},{"install-0b-disk-unchosen",INSTALL_DISK},{"install-1-role",INSTALL_ROLE},{"install-1b-role-unchecked",INSTALL_ROLE},{"install-1c-role-failed",INSTALL_ROLE},{"install-2-servers-net",INSTALL_NETWORK},{"install-3-hostname",INSTALL_HOSTNAME},{"install-4-admin",INSTALL_ADMIN},{"install-5-summary",INSTALL_SUMMARY},{"install-5c-progress",INSTALL_PROGRESS},{"install-5b-apply-failed",INSTALL_FAILED},{"install-6-done",INSTALL_DONE}};const Theme *themes[]={&THEME_LIGHT,&THEME_DARK};
    for(size_t i=0;i<sizeof cards/sizeof cards[0];i++){for(int t=0;t<2;t++){install_demo(a,cards[i].view);role_variant(&a->install,cards[i].name);a->theme=themes[t];cairo_surface_t *sf=cairo_image_surface_create(CAIRO_FORMAT_RGB24,w,h);cairo_t *cr=cairo_create(sf);double s=ui_scale(h);a->scale=s;cairo_scale(cr,s,s);screen_install(a,cr,w/s,h/s);cairo_destroy(cr);   /* the scale app_draw uses */char path[512];snprintf(path,sizeof path,"%s-%s-%s.png",prefix,cards[i].name,themes[t]->id);cairo_status_t st=cairo_surface_write_to_png(sf,path);cairo_surface_destroy(sf);if(st!=CAIRO_STATUS_SUCCESS){fprintf(stderr,"native-gui: %s: %s\n",path,cairo_status_to_string(st));return 1;}}}return 0;}
