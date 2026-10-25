#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "screens_install.h"
#include "screens_rounds.h"   /* bidi_isolate */
#include "install_raid.h"     /* #1989 */

/* "ext4, unknown, unknown" -> every exact "unknown" token in Hebrew (#1566) */
static void display_part_types(const char *in,char *out,size_t n){
    size_t used=0;
    out[0]=0;
    while(*in&&used<n){
        const char *end=strstr(in,", ");
        size_t len=end?(size_t)(end-in):strlen(in);
        int unk=len==7&&!strncmp(in,"unknown",7);
        int w=snprintf(out+used,n-used,"%s%.*s",used?", ":"",unk?(int)strlen("לא ידוע"):(int)len,unk?"לא ידוע":in);
        if(w<0)break;
        used+=(size_t)w;
        if(!end)break;
        in=end+2;
    }
}

static void display_disk_contents(const char *raw,char *out,size_t n){
    if(!strcmp(raw,"empty")){snprintf(out,n,"ריק");return;}
    if(!strcmp(raw,"unknown")){snprintf(out,n,"לא ידוע");return;}
    const char *colon=strchr(raw,':'),*parts=colon?strstr(colon+1," partitions"):NULL;
    if(colon&&parts){
        char scheme[32],scheme_i[40],types[128],types_he[160],types_i[168];
        size_t sn=(size_t)(colon-raw);
        if(sn>=sizeof scheme)sn=sizeof scheme-1;
        memcpy(scheme,raw,sn);
        scheme[sn]=0;
        bidi_isolate(scheme,scheme_i,sizeof scheme_i);
        int count=atoi(colon+1);
        const char *open=strchr(parts,'('),*close=open?strrchr(open,')'):NULL;
        if(open&&close&&close>open+1){
            size_t tn=(size_t)(close-open-1);
            if(tn>=sizeof types)tn=sizeof types-1;
            memcpy(types,open+1,tn);
            types[tn]=0;
            display_part_types(types,types_he,sizeof types_he);
            bidi_isolate(types_he,types_i,sizeof types_i);
            snprintf(out,n,"%s:%d מחיצות (%s)",scheme_i,count,types_i);
        }
        else snprintf(out,n,"%s:%d מחיצות",scheme_i,count);
        return;
    }
    if(!strncmp(raw,"filesystem ",11)){char type_i[136];bidi_isolate(raw+11,type_i,sizeof type_i);snprintf(out,n,"מערכת קבצים %s",type_i);return;}
    snprintf(out,n,"%s",raw);
}

static void disk_head(cairo_t *cr,const Theme *t,Rect r){
    draw_fill_rrect(cr,r,0,t->field);
    install_text_r(cr,t,"דגם",12,600,r.x+r.w-54,r.y+9,r.w*.35,t->muted);
    install_text_r(cr,t,"גודל",12,600,r.x+r.w*.58,r.y+9,r.w*.14,t->muted);
    install_text_r(cr,t,"חיבור",12,600,r.x+r.w*.42,r.y+9,r.w*.13,t->muted);
    install_text_r(cr,t,"מה נמצא בדיסק",12,600,r.x+r.w*.27,r.y+9,r.w*.25,t->muted);
}

static void disk_row(App *a,cairo_t *cr,Rect r,int i){
    InstallState *s=&a->install;InstallDisk *d=&s->disks[i];const Theme *t=a->theme;int selected=i==s->disk||(s->disk>=0&&install_raid_member(s,i));   /* #1989: every member is erased; #2147: once a disk is chosen */
    draw_fill_rrect(cr,r,0,d->iso?t->field:(selected?t->danger_soft:t->surface));
    cairo_rectangle(cr,r.x,r.y+r.h-1,r.w,1);draw_set(cr,t->hair);cairo_fill(cr);
    double cx=r.x+r.w-22,cy=r.y+r.h/2;cairo_arc(cr,cx,cy,7,0,6.283);draw_set(cr,d->iso?t->muted:(selected?t->danger:t->hair));cairo_set_line_width(cr,2);cairo_stroke(cr);
    if(selected){cairo_arc(cr,cx,cy,3.5,0,6.283);draw_set(cr,t->danger);cairo_fill(cr);}
    install_text_ltr(cr,d->model[0]?d->model:d->path,13,600,r.x+r.w-285,r.y+9,220,d->iso?t->muted:t->ink);
    install_text_ltr(cr,d->path,11,400,r.x+r.w-285,r.y+31,220,t->muted);
    char size[32];snprintf(size,sizeof size,"%.1f GiB",d->size_bytes/1073741824.0);install_text_ltr(cr,size,12,500,r.x+r.w*.46,r.y+19,r.w*.14,d->iso?t->muted:t->ink);
    if(!strcmp(d->bus,"unknown"))install_text_r(cr,t,"לא ידוע",12,400,r.x+r.w*.42,r.y+19,r.w*.12,t->muted);
    else install_text_ltr(cr,d->bus[0]?d->bus:"—",12,400,r.x+r.w*.30,r.y+19,r.w*.12,t->muted);
    char has[256];display_disk_contents(d->has[0]?d->has:"—",has,sizeof has);
    install_text_r(cr,t,d->iso?"מדיית ההתקנה":has,11,400,r.x+r.w*.28,r.y+(selected?9:14),r.w*.24,t->muted);
    if(selected)install_text_r(cr,t,"יימחק",11,600,r.x+r.w*.28,r.y+33,r.w*.24,t->danger_fg);   /* ui-audit: was r.x+68, on top of "has" at 1024 */
    if(!d->iso)hit_add(a,r,HIT_INSTALL_DISK_BASE+i);
}

/* #2147: off (muted, no hit box) until a disk is chosen */
static void disk_ack(App *a,cairo_t *cr,Rect r,int enabled){
    InstallState *s=&a->install;const Theme *t=a->theme;int on=enabled&&s->disk_ack;draw_fill_rrect(cr,r,t->radius,on?t->danger_soft:(enabled?t->surface:t->field));draw_border_rrect(cr,r,t->radius,on?t->danger:t->hair,1);
    Rect box={r.x+r.w-38,r.y+12,20,20};draw_fill_rrect(cr,box,4,on?t->danger:t->field);draw_border_rrect(cr,box,4,on?t->danger:t->hair,1);
    if(on)install_text_r(cr,t,"✓",13,600,box.x+17,box.y+1,16,t->on_ink);
    install_text_r(cr,t,"אני מבין",13,600,r.x+r.w-50,r.y+13,r.w-64,enabled?t->ink:t->muted);if(enabled)hit_add(a,r,HIT_INSTALL_DISK_ACK);
}

/* #1989: the RAID choices this server allows, as one row of chips (RTL: the
 * first choice on the right); a VM gets the reason instead of a choice. */
static double raid_row(App *a,cairo_t *cr,double x,double y,double w){
    InstallState *s=&a->install;const Theme *t=a->theme;int n=install_raid_suitable(s);
    if(n<2)return y;
    if(s->virt==INSTALL_VIRT_VIRTUAL){install_text_r(cr,t,"שרת וירטואלי: RAID נעשה ברמת המארח (ESXi/Hyper-V), ולכן אינו מוצע כאן",13,500,x+w,y,w,t->muted);return y+30;}
    char cap[160];snprintf(cap,sizeof cap,"RAID מ-%d הכוננים המתאימים",n);install_text_r(cr,t,cap,13,600,x+w,y,w,t->ink);y+=24;
    int shown=0;for(int c=0;c<RAID_CHOICES;c++)shown+=install_raid_allowed(s,c);
    double gap=8,cw=(w-gap*(shown-1))/shown,cx=x+w;
    for(int c=0;c<RAID_CHOICES;c++){if(!install_raid_allowed(s,c))continue;int on=s->raid==c;Rect r={cx-cw,y,cw,38};cx-=cw+gap;
        draw_fill_rrect(cr,r,t->radius_sm,on?t->indigo_soft:t->surface);draw_border_rrect(cr,r,t->radius_sm,on?t->indigo:t->hair,on?2:1);
        Text q=text_make(cr,FONT_SANS,13,on?600:500,RAID_OPTS[c].label,(int)cw-8,DIR_RTL);text_draw(cr,&q,r.x+(r.w-q.w)/2,r.y+(r.h-q.h)/2,on?t->indigo:t->ink);text_free(&q);
        hit_add(a,r,HIT_INSTALL_RAID_BASE+c);}
    y+=46;
    if(s->virt==INSTALL_VIRT_UNKNOWN){install_text_r(cr,t,"לא זוהה אם השרת פיזי או וירטואלי — ברירת המחדל: בלי RAID",12,400,x+w,y,w,t->muted);y+=22;}
    return y+4;
}

void install_draw_disk(App *a,cairo_t *cr,InstallLayout l){
    InstallState *s=&a->install;const Theme *t=a->theme;double x=l.body.x,w=l.body.w,y=l.body.y;
    Rect table={x,y,w,42+s->ndisks*58};install_card(cr,t,table);disk_head(cr,t,(Rect){x+1,y+1,w-2,40});
    for(int i=0;i<s->ndisks;i++){disk_row(a,cr,(Rect){x+1,y+41+i*58,w-2,58},i);}y+=table.h+16;
    y=raid_row(a,cr,x,y,w);
    int selectable=0;for(int i=0;i<s->ndisks;i++)selectable+=!s->disks[i].iso;
    if(s->disk>=0&&s->disk<s->ndisks){char warning[320];const InstallDisk *d=&s->disks[s->disk];if(s->raid!=RAID_NONE)snprintf(warning,sizeof warning,"כל הנתונים ב-%d הכוננים יימחקו, והם יאוחדו למערך %s",install_raid_count(s),RAID_OPTS[s->raid].label);else snprintf(warning,sizeof warning,"כל הנתונים בדיסק %s · %.1f GiB · %s יימחקו",d->model[0]?d->model:"ללא שם",d->size_bytes/1073741824.0,d->path);install_text_r(cr,t,warning,14,600,x+w,y,w,t->danger_fg);y+=30;disk_ack(a,cr,(Rect){x+w-180,y,180,44},1);}   /* #2147: name, size and device */
    else if(selectable){install_text_r(cr,t,"בחר את הדיסק להתקנה — כל הנתונים בו יימחקו",14,600,x+w,y,w,t->ink);y+=30;disk_ack(a,cr,(Rect){x+w-180,y,180,44},0);}   /* #2147: more than one disk, none chosen */
    else{install_text_r(cr,t,"לא נמצא דיסק חוקי להתקנה.",13,600,x+w,y,w,t->danger_fg);y+=48;}
    if(s->field_error[0][0]){install_text_r(cr,t,s->field_error[0],12,600,x+w,y+50,w,t->danger_fg);}
    if(s->existing_disk>=0&&s->existing_disk<s->ndisks){InstallDisk *d=&s->disks[s->existing_disk];Rect card={x,y+58,w,72};install_card(cr,t,card);install_text_r(cr,t,"נמצאה התקנה קיימת — אתחל מהדיסק",14,600,x+w-16,card.y+14,w-220,t->ink);install_text_ltr(cr,d->model,11,400,x+180,card.y+42,w-210,t->muted);Text boot=btn_label(cr,"אתחל מהדיסק");draw_btn(a,cr,(Rect){x+16,card.y+17,150,38},&boot,BTN_PLAIN,HIT_INSTALL_BOOT_LOCAL);text_free(&boot);}
    install_footer(a,cr,l,"הבא",HIT_INSTALL_NEXT,0,s->disk>=0&&s->disk_ack);
}

static void result_box(App *a,cairo_t *cr,Rect r){InstallState *s=&a->install;const Theme *t=a->theme;if(!s->primary_checked)return;Rgb bg=s->primary_ok?t->success_soft:t->danger_soft,line=s->primary_ok?t->success_ink:t->danger;draw_fill_rrect(cr,r,t->radius,bg);draw_border_rrect(cr,r,t->radius,line,1);
    if(s->primary_ok){char top[220],fp[160];snprintf(top,sizeof top,"✓  השרת הראשי ענה · %s · %s",s->primary_name,s->primary_version[0]?s->primary_version:"גרסה לא נקראה");install_text_r(cr,t,top,13,600,r.x+r.w-14,r.y+10,r.w-28,line);snprintf(fp,sizeof fp,"SHA-256  %s",s->primary_fingerprint);install_text_ltr(cr,fp,11,400,r.x+14,r.y+38,r.w-28,t->ink);}else{char failure[380];snprintf(failure,sizeof failure,"התקשרות אל %s בפורט 8443 נכשלה. ודא כי חומת האש שבין האתרים פתוחה",s->primary_url);install_text_r(cr,t,s->primary_error[0]?s->primary_error:failure,13,600,r.x+r.w-14,r.y+12,r.w-28,line);}}

void install_draw_role(App *a,cairo_t *cr,InstallLayout l){InstallState *s=&a->install;const Theme *t=a->theme;double w=l.body.w,x=l.body.x,y=l.body.y;   /* the one column (mockup 10/10) */
    install_text_r(cr,t,"נבחר בתפריט ההתקנה",12,500,x+w,y,w,t->muted);y+=24;
    install_radio(a,cr,(Rect){x,y,w,74},"שרת ראשי","מנהל את הספרייה, השיכפול ושרתי המשנה.",!s->secondary,HIT_INSTALL_PRIMARY);y+=86;
    install_radio(a,cr,(Rect){x,y,w,74},"שרת משנה","שרת באתר נוסף; הראשי יוזם אליו חיבור מאובטח בפורט 8443.",s->secondary,HIT_INSTALL_SECONDARY);y+=96;
    if(s->secondary){install_field_draw(a,cr,(Rect){x,y,w-150,56},"כתובת השרת הראשי",s->primary_url,0,HIT_INSTALL_PRIMARY_URL,1);Text check=btn_label(cr,"בדוק חיבור");Rect cb={x+w-138,y+19,138,36};draw_btn(a,cr,cb,&check,BTN_PLAIN,HIT_INSTALL_CHECK_PRIMARY);install_focus_ring(a,cr,cb,HIT_INSTALL_CHECK_PRIMARY);text_free(&check);y+=60;install_text_r(cr,t,"כתובת ה-IP של השרת הראשי, כפי שהיא נראית מאתר זה",12,400,x+w,y,w-150,t->muted);y+=26;result_box(a,cr,(Rect){x,y,w,70});y+=82;}
    if(s->error[0])install_text_r(cr,t,s->error,13,600,x+w,y,w,t->danger_fg);
    install_footer(a,cr,l,"הבא",HIT_INSTALL_NEXT,1,install_role_ready(s));   /* #1310: no green, no next */
}

static void table_head(cairo_t *cr,const Theme *t,Rect r){draw_fill_rrect(cr,r,0,t->field);const char *h[]={"כרטיס","קישור","כתובת עכשיו","מקור"};double x[]={r.x+r.w-55,r.x+r.w*.57,r.x+r.w*.39,r.x+r.w*.2};double widths[]={r.w*.34,r.w*.16,r.w*.18,r.w*.18};for(int i=0;i<4;i++)install_text_r(cr,t,h[i],12,600,x[i],r.y+9,widths[i],t->muted);}

static void nic_row(App *a,cairo_t *cr,Rect r,int i){InstallState *s=&a->install;InstallNic *n=&s->nics[i];const Theme *t=a->theme;int sel=i==s->nic;draw_fill_rrect(cr,r,0,sel?t->indigo_soft:t->surface);cairo_rectangle(cr,r.x,r.y+r.h-1,r.w,1);draw_set(cr,t->hair);cairo_fill(cr);
    double cx=r.x+r.w-22,cy=r.y+r.h/2;cairo_arc(cr,cx,cy,7,0,6.283);draw_set(cr,sel?t->indigo:t->hair);cairo_set_line_width(cr,2);cairo_stroke(cr);if(sel){cairo_arc(cr,cx,cy,3.5,0,6.283);draw_set(cr,t->indigo);cairo_fill(cr);}
    /* #2096: a name a person reads (the model, or "כרטיס רשת N"), not "VMware 0x07b0"; the
     * kernel name and the MAC on the line under it, each one line (it wrapped onto the MAC) */
    char label[96],ids[96];if(n->model[0])snprintf(label,sizeof label,"%s",n->model);else snprintf(label,sizeof label,"כרטיס רשת %d",i+1);snprintf(ids,sizeof ids,"%s · %s",n->name,n->mac);
    install_text_r(cr,t,label,13,600,r.x+r.w-45,r.y+9,200,t->ink);Text q=text_make(cr,FONT_MONO,11,400,ids,0,DIR_LTR);text_draw(cr,&q,r.x+r.w-45-q.w,r.y+31,t->muted);text_free(&q);
    char link[40];if(!strcmp(n->link,"unknown"))snprintf(link,sizeof link,"לא ידוע");else snprintf(link,sizeof link,"%s",n->link);
    install_text_r(cr,t,link,12,600,r.x+r.w*.56,r.y+20,r.w*.15,!strcmp(link,"מחובר")?t->success_ink:t->muted);
    install_text_ltr(cr,n->current[0]?n->current:"—",12,400,r.x+r.w*.21,r.y+20,r.w*.18,t->ink);
    install_text_r(cr,t,n->source[0]?n->source:"—",11,400,r.x+r.w*.19,r.y+15,r.w*.18,t->muted);if(a->focus==HIT_INSTALL_NIC_BASE+i)draw_border_rrect(cr,(Rect){r.x+3,r.y+3,r.w-6,r.h-7},4,t->indigo,2);hit_add(a,r,HIT_INSTALL_NIC_BASE+i);}

void install_draw_network(App *a,cairo_t *cr,InstallLayout l){InstallState *s=&a->install;const Theme *t=a->theme;double w=l.body.w,x=l.body.x,y=l.body.y;Rect table={x,y,w,42+s->nnics*58};install_card(cr,t,table);table_head(cr,t,(Rect){x+1,y+1,w-2,40});for(int i=0;i<s->nnics;i++)nic_row(a,cr,(Rect){x+1,y+41+i*58,w-2,58},i);y+=table.h+16;
    double half=(w-12)/2;install_radio(a,cr,(Rect){x+half+12,y,half,66},"לקוח DHCP (מומלץ)","הכתובת מגיעה משרת ה-DHCP של המכללה.",!s->static_mode,HIT_INSTALL_DHCP);install_radio(a,cr,(Rect){x,y,half,66},"כתובת סטטית","כשאין DHCP או כשהכתובת חייבת להישאר קבועה.",s->static_mode,HIT_INSTALL_STATIC);y+=79;
    double gap=12,fw=(w-gap)/2;install_field_draw(a,cr,(Rect){x+fw+gap,y,fw,56},"כתובת IP",s->address,0,HIT_INSTALL_ADDRESS,s->static_mode);install_field_draw(a,cr,(Rect){x,y,fw,56},"מסכת רשת",s->netmask,0,HIT_INSTALL_NETMASK,s->static_mode);y+=68;install_field_draw(a,cr,(Rect){x+fw+gap,y,fw,56},"שער (רשות)",s->gateway,0,HIT_INSTALL_GATEWAY,s->static_mode);install_field_draw(a,cr,(Rect){x,y,fw,56},"DNS (רשות)",s->dns,0,HIT_INSTALL_DNS,s->static_mode);
    /* #2096: the message under the field it is about (DNS sat under the gateway) */
    for(int i=3;i<=8;i++)if(s->field_error[i][0]){double right=i==6||i==8?x+fw:x+w,width=i<5?w:fw;install_text_r(cr,t,s->field_error[i],12,600,right,y+62,width,t->danger_fg);break;}
    install_footer(a,cr,l,"הבא",HIT_INSTALL_NEXT,1,s->nic>=0);
}
