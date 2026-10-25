/* Shared chrome and the #1090 login/menu screens (standby: screens_standby.c). The remaining
 * flows keep their existing state machine but use the same full-screen frame
 * from widgets.c. */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include "screens_rounds.h"
#include "lcui.h"   /* #1322 */
#include "smartdlg.h"   /* 16/10 */
#include "canceldlg.h"  /* #1386 */
#include "dcui.h"       /* #1914: the USB stick's settings are the DCUI's settings screen */

/* #1252: a value the state file did not carry is said as unknown -- the
 * header once showed the mockup's server, address and version
 * on a server that was TA, 10.44.0.1, v0.59.2 (principle 5). */
static const char *known(const char *v) { return v[0] ? v : "לא ידוע"; }

/* #1453: "X.Y.Z" or git describe's "X.Y.Z-N-gSHA" (N commits after the tag). */
static int version_parse(const char *v,long o[4]){
    char *e; o[3]=0;
    for(int i=0;i<3;i++){
        if(*v<'0'||*v>'9')return 0;
        o[i]=strtol(v,&e,10); v=e;
        if(i<2){if(*v!='.')return 0;v++;}
    }
    if(!*v)return 1;
    if(*v!='-'||v[1]<'0'||v[1]>'9')return 0;
    o[3]=strtol(v+1,&e,10);
    return e[0]=='-'&&e[1]=='g';
}
/* -1 the agent is older than the server, 1 newer, 0 they differ but cannot be ordered
 * (the same position with another hash, or a version this does not parse). */
static int version_order(const char *av,const char *sv){
    long a[4],s[4];
    if(!version_parse(av,a)||!version_parse(sv,s))return 0;
    for(int i=0;i<4;i++)if(a[i]!=s[i])return a[i]<s[i]?-1:1;
    return 0;
}

/* "name · address" when both are known, the one that is known, or unknown. */
static void server_label(const State *s, char *out, size_t n) {
    if (s->server_name[0] && s->server_addr[0]) snprintf(out, n, "%s · %s", s->server_name, s->server_addr);
    else snprintf(out, n, "%s", s->server_name[0] ? s->server_name : known(s->server_addr));
}

/* #1499 (Nadav 16/10): the theme switch in the top-left corner of every
 * screen -- a click or F2 (main.c: toggle_theme). On the header (dark in both
 * themes) it is an outline, not a filled pill: the header stays the header
 * colour across its text row (test_native_gui_fills_the_screen measures that
 * row; a filled 58px pill took it from 0.71 to 0.599 at 1024 wide). Narrower
 * on a narrow screen. On the login screen, a field-like box. The glyph is what
 * a click turns it into, like the console's #theme-toggle: the sun on dark,
 * the moon on light. IBM Plex has neither code point, so they are vectors
 * (as before #1090). */
#define THEME_BTN_H 28.0
static double theme_btn_w(double W){return W<1200?46:56;}
static void draw_theme_toggle(App *a,cairo_t *cr,double W,double head_h){
    const Theme *t=a->theme; int dark=t==&THEME_DARK;
    Rect b={head_h>0?N_HEADER_PAD:16,head_h>0?(N_HEADER_H-THEME_BTN_H)/2:16,theme_btn_w(W),THEME_BTN_H};
    Rgb ink=head_h>0?t->header_text:t->ink;
    if(head_h>0){   /* a 1px outline on the pixel grid: two columns of the text row, not 56 */
        Rect o={b.x+.5,b.y+.5,b.w-1,b.h-1};
        draw_set_a(cr,t->header_text,.45);draw_rrect_path(cr,o,RADIUS_SM);cairo_set_line_width(cr,1);cairo_stroke(cr);
    }else{draw_fill_rrect(cr,b,RADIUS_SM,t->surface);draw_border_rrect(cr,b,RADIUS_SM,t->hair,1);}
    double cx=b.x+14,cy=b.y+b.h/2;
    if(dark){                                    /* the sun: back to light */
        cairo_arc(cr,cx,cy,3.2,0,2*M_PI);draw_set(cr,ink);cairo_fill(cr);
        cairo_set_line_width(cr,1.4);
        for(int k=0;k<8;k++){double an=k*M_PI/4;
            cairo_move_to(cr,cx+5.0*cos(an),cy+5.0*sin(an));cairo_line_to(cr,cx+7.2*cos(an),cy+7.2*sin(an));}
        cairo_stroke(cr);
    }else{                                       /* the moon: to dark */
        cairo_save(cr);                          /* a disc minus a disc: clip the second one out */
        cairo_rectangle(cr,cx-10,cy-10,20,20);cairo_new_sub_path(cr);cairo_arc(cr,cx+3.0,cy-1.6,5.2,0,2*M_PI);
        cairo_set_fill_rule(cr,CAIRO_FILL_RULE_EVEN_ODD);cairo_clip(cr);
        cairo_arc(cr,cx,cy,6,0,2*M_PI);draw_set(cr,ink);cairo_fill(cr);
        cairo_restore(cr);
    }
    Text k=text_make(cr,FONT_MONO,11,600,"F2",0,DIR_LTR);
    text_draw(cr,&k,b.x+b.w-k.w-7,b.y+(b.h-k.h)/2,ink);text_free(&k);
    hit_add(a,b,HIT_THEME);
}

/* mockup 01 section 2 (Nadav 24/10): the USB stick's "הגדרות" moved from the content's corner
 * (#1914) to the top bar, next to the theme switch, with F4. The fixed buttons sit left to right
 * in the order of their keys -- F2, (F3 "כלים", #649, not yet), F4 -- so a tools button goes in
 * between. Only where "הגדרות" was: the stick's source screen. */
int app_usbset_here(const App *a){
    return a->screen==SCREEN_LOCAL&&a->st.lc.usb&&a->st.lc.screen!=LC_SCREEN_USBSET&&lc_view(a)==LCVIEW_SOURCE&&!a->lcu.confirm;
}
static double draw_usbset_btn(App *a,cairo_t *cr,double x){
    const Theme *t=a->theme;
    Text l=text_make(cr,FONT_SANS,13,500,"הגדרות",0,DIR_RTL), k=text_make(cr,FONT_MONO,11,600,"F4",0,DIR_LTR);
    Rect b={x,(N_HEADER_H-THEME_BTN_H)/2,8+k.w+8+l.w+6+14+10,THEME_BTN_H};
    Rect o={b.x+.5,b.y+.5,b.w-1,b.h-1};
    draw_set_a(cr,t->header_text,.45);draw_rrect_path(cr,o,RADIUS_SM);cairo_set_line_width(cr,1);cairo_stroke(cr);
    text_draw(cr,&k,b.x+8,b.y+(b.h-k.h)/2,t->header_text);
    text_draw(cr,&l,b.x+8+k.w+8,b.y+(b.h-l.h)/2,t->header_text);
    double cx=b.x+b.w-10-7,cy=b.y+b.h/2;            /* the gear: a ring and eight teeth */
    draw_set(cr,t->header_text);cairo_set_line_width(cr,1.4);
    cairo_new_sub_path(cr);cairo_arc(cr,cx,cy,2.2,0,2*M_PI);cairo_stroke(cr);
    for(int i=0;i<8;i++){double an=i*M_PI/4;cairo_move_to(cr,cx+4.0*cos(an),cy+4.0*sin(an));cairo_line_to(cr,cx+6.4*cos(an),cy+6.4*sin(an));}
    cairo_stroke(cr);
    hit_add(a,b,HIT_LC_USBSET);
    text_free(&l);text_free(&k);
    return b.w;
}

static double draw_header(App *a, cairo_t *cr, double W) {
    const Theme *t=a->theme;
    Rect bar={0,0,W,N_HEADER_H}; draw_fill_rrect(cr,bar,0,t->header);
    double right=W-N_HEADER_PAD;
    /* #1302: the compact dark lockup (lockup-dark-32.png: 5px cells) on the header in both
     * themes -- always: an uploaded college logo is no longer drawn (Nadav 12/10) */
    right=draw_brand_lockup_r(cr,right,(N_HEADER_H-brand_symbol_height(5))/2,5,1,0)-22;

    const int clone=a->force_cloner||a->force_standby||a->screen==SCREEN_CLONER||a->screen==SCREEN_STANDBY||a->screen==SCREEN_LCBOOT||a->screen==SCREEN_LOCAL
        ||(a->screen==SCREEN_FAILURE&&a->st.fail_cloner);   /* #1569: the failure record says whose failure it is */
    const char *role=clone?"מחשב שיכפול":"מחשב בנייה";   /* #1252: the "2" was the mockup's */
    Text rt=text_make(cr,FONT_SANS,12,400,role,0,DIR_RTL);
    Rect pill={right-rt.w-18,(N_HEADER_H-20)/2,rt.w+18,20};
    draw_set_a(cr,t->header_text,.14); draw_rrect_path(cr,pill,10); cairo_fill(cr);
    text_draw_r(cr,&rt,right-9,pill.y+(pill.h-rt.h)/2,t->header_text); right=pill.x-22;
    text_free(&rt);

    /* #1252: every value from the state file; unknown is said, not invented.
     * #1257: the versions (agent · server) are drawn on their own line below. */
    char srv[160]; server_label(&a->st,srv,sizeof srv);
    const char *keys[]={"מכונה","שרת"};
    const int local=a->screen==SCREEN_LOCAL;   /* #1322: no server in this flow -- said, in the server's place */
    /* #1437: the name is the server's; with no hello this boot, the cloner says the MAC it knows itself */
    const char *vals[]={local&&!a->st.machine_name[0]&&a->mac[0]?a->mac:known(a->st.machine_name),srv};
    for(int i=0;i<(local?1:2);i++){
        Text k=text_make(cr,FONT_SANS,13,400,keys[i],0,DIR_RTL);
        Text v=text_make(cr,FONT_MONO,13,400,vals[i],0,DIR_LTR);
        text_draw_r(cr,&k,right,(N_HEADER_H-k.h)/2,(Rgb){1,1,1});
        right-=k.w+5;
        text_draw_r(cr,&v,right,(N_HEADER_H-v.h)/2,(Rgb){.8,.84,.87});
        right-=v.w+18;
        text_free(&k);text_free(&v);
    }
    int survey=a->st.lc.usb&&a->st.lc.survey;   /* #1914/#1921: the survey stick, said on every screen of it */
    if(local||survey){
        Text m=text_make(cr,FONT_SANS,12,600,survey?"דיסק-און-קי · איסוף נתונים":"שיכפול מקומי · בלי שרת",0,DIR_RTL);
        Rect mp={right-m.w-18,(N_HEADER_H-20)/2,m.w+18,20};
        draw_fill_rrect(cr,mp,10,survey?(Rgb){.42,.25,.68}:(Rgb){.76,.33,0});
        text_draw_r(cr,&m,right-9,mp.y+(20-m.h)/2,(Rgb){1,1,1});right=mp.x-22;text_free(&m);
    }
    const char *av=a->st.agent_version, *sv=a->st.server_version;
    /* empty, "unknown" (the agent's stamp) and "לא ידוע" are all "not known" */
    int ak=av[0]&&strcmp(av,"unknown")&&strcmp(av,"לא ידוע"), sk=sv[0]&&strcmp(sv,"unknown")&&strcmp(sv,"לא ידוע");
    if(av[0]=='v')av++;
    if(sv[0]=='v')sv++;
    int mismatch=ak&&sk&&strcmp(av,sv);
    char versions[360];
    if(local&&!sk)   /* #1437: no server answered -- not an unknown server version */
        snprintf(versions,sizeof versions,a->st.lc.usb?"סוכן %s%s":"סוכן %s%s · שרת: לא מחובר (שיכפול מקומי)",   /* #1914: the stick never has one */
                 ak?"v":"",ak?av:"לא ידוע");
    else snprintf(versions,sizeof versions,"סוכן %s%s · שרת %s%s",
             ak?"v":"",ak?av:"לא ידוע",sk?"v":"",sk?sv:"לא ידוע");
    double vw=right-90-theme_btn_w(W)-12; if(vw<100)vw=100;   /* #1499: the clock moved over by the switch */
    Text vt=text_make(cr,FONT_SANS,11,400,versions,vw,DIR_RTL);
    text_draw_r(cr,&vt,right,mismatch?5:(N_HEADER_H-vt.h)/2,t->header_text);
    text_free(&vt);
    if(mismatch){
        int ord=version_order(av,sv);   /* #1453: say what was compared, not "old" for every difference */
        Text hint=text_make(cr,FONT_SANS,11,600,ord<0?"ה-initrd ישן מהשרת":ord>0?"ה-initrd חדש מהשרת":"ה-initrd לא תואם לשרת",vw,DIR_RTL);
        text_draw_r(cr,&hint,right,N_HEADER_H-hint.h-4,(Rgb){1,.78,.35});
        text_free(&hint);
    }
    time_t now=time(NULL); struct tm tmv; char clock_s[16]="--:--";
    if(a->fixed_clock) snprintf(clock_s,sizeof clock_s,"12:45");   /* the mockup's clock; --png only */
#ifdef _WIN32
    else if(now!=(time_t)-1 && localtime_s(&tmv,&now)==0) strftime(clock_s,sizeof clock_s,a->st.tz[0]?"%H:%M":"%H:%M UTC",&tmv);
#else
    else if(now!=(time_t)-1 && localtime_r(&now,&tmv)) strftime(clock_s,sizeof clock_s,a->st.tz[0]?"%H:%M":"%H:%M UTC",&tmv);
#endif
    /* #1914 (Nadav, college 20/10): no clock on the stick -- a Windows machine keeps its RTC
       in local time, there is no server to set it by, and a wrong hour is worse than none */
    if(a->st.lc.usb){if(app_usbset_here(a))draw_usbset_btn(a,cr,N_HEADER_PAD+theme_btn_w(W)+8);return N_HEADER_H;}   /* mockup 01 */
    Text clock=text_make(cr,FONT_MONO,14,400,clock_s,0,DIR_LTR);
    text_draw(cr,&clock,N_HEADER_PAD+theme_btn_w(W)+12,(N_HEADER_H-clock.h)/2,t->header_text); text_free(&clock);   /* #1499: after the corner switch */
    return N_HEADER_H;
}

static const char *status_word(const App *a,char *buf,size_t n,Rgb *dot){
    const State *s=&a->st; *dot=a->theme->led_ok;
    if(s->shrink_pending){*dot=a->theme->warn;return "ממתין להחלטת כיווץ";}   /* #928: the modal owns the screen */
    if(s->ask_pending){*dot=a->theme->warn;return s->ask_kind==ASK_GROWBACK||s->ask_kind==ASK_RAID?"ממתין להחלטה על הדיסק"
                                                   :s->ask_kind==ASK_SHARE?"ממתין לאישור חלוקת המקום":s->ask_kind==ASK_BADSECT?"ממתין להחלטה על דיסק המקור":"הקליטה הסתיימה";}   /* 11/10; #1808/#1898 */
    if(a->screen==SCREEN_LCBOOT||a->screen==SCREEN_LOCAL)return lc_status_word(a,dot);   /* #1322 */
    switch(a->screen){
    case SCREEN_LOGIN:return "ממתין לאימות";
    case SCREEN_MENU:return a->logout_confirming?"אישור התנתקות":"בחירת פעולה";
    case SCREEN_STANDBY:   /* #1304: what the last hello proves (screens_standby.c) */
        if(standby_conn(s)==STANDBY_OFF){*dot=a->theme->danger;return "אין קשר לשרת";}
        if(standby_conn(s)==STANDBY_UNKNOWN)*dot=a->theme->led_idle;
        return "ממתין לסבב";
    case SCREEN_MESSAGE:*dot=a->theme->warn;return s->msg_title;
    case SCREEN_FAILURE:return failure_status_word(a,dot);   /* #1569 */
    case SCREEN_PICK:return "בחירת דיסק";
    case SCREEN_PROGRESS:return s->task==TASK_PENDING?"ממתין לסוכן":"משימה פעילה";
    case SCREEN_DONE:if(s->task==TASK_FAILED){*dot=a->theme->danger;return "נכשל";}return "הושלם";
    case SCREEN_CLASS:return s->has_session?(s->sess_open?"ממתין להצטרפות":"משדר"):"בחירת כיתה";
    case SCREEN_ROOM:
        if(s->has_round){snprintf(buf,n,"גל %d %s",s->wave_number,s->wave_open?"ממתין":   /* #1331 mockup v2: an ended wave says so */
            s->wave_state[0]&&strcmp(s->wave_state,"running")&&strcmp(s->wave_state,"failed")?"הסתיים":"משדר");return buf;}
        {int awake=0;for(int i=0;i<s->nmachines;i++)awake+=s->machines[i].awake;snprintf(buf,n,"%d יעדים מחוברים",awake);return buf;}
    case SCREEN_CLONER:
        if(s->smart_pending){*dot=a->theme->warn;return "ממתין להכרעת SMART";}
        {int w=0,e=0;for(int i=0;i<s->ndrawers;i++){w+=!strcmp(s->drawers[i].state,"writing");
            e+=!strcmp(s->drawers[i].state,"done")||!strcmp(s->drawers[i].state,"failed")||!strcmp(s->drawers[i].state,"skipped");}
         if(s->ndrawers&&e==s->ndrawers)return "שיכפול במקביל · הסתיים";   /* #1331 mockup v2 */
         snprintf(buf,n,"כותב %d דיסקים במקביל",w);return buf;}
    case SCREEN_RESTORE:*dot=a->theme->warn;return "בחירת אימג' לשחזור";
    case SCREEN_TOOLS:return a->tool_running?"מריץ כלי":"ארגז הכלים";
    case SCREEN_INSTALL:return "אשף התקנה";
    case SCREEN_LCBOOT: case SCREEN_LOCAL: break;   /* #1322: answered above */
    }
    return "מוכן";
}

/* mockup 10/10: the keys a screen lists are the keys main.c handle() acts on
 * there -- nothing it ignores. */
static const char *status_keys(const App *a,char *buf,size_t n){
    if(a->st.idle_poweroff_at>0)return "Esc  השאר דלוק    Enter  כבה עכשיו";   /* mockup 10: the idle window, over every screen */
    if(a->sdlg.kind)return sdlg_notice(&a->sdlg)?"Enter / Esc  המשך"   /* #2017: the orange source's notice */
                          :"Tab / ← →  מעבר    Enter  הכפתור המסומן    Esc  דלג";   /* 16/10: the SMART window */
    if(a->st.shrink_pending)return a->st.shrink_direct?"1  כווץ ושלח    2  שלח בלי כיווץ"   /* #1171 */
                                                      :"1  כווץ וקלוט    2  קלוט בלי כיווץ    3 / Esc  בטל קליטה";   /* #928 */
    if(a->st.ask_pending)return a->st.ask_kind==ASK_GROWBACK?"1  החזר לגודל    2 / Esc  השאר"
                               :a->st.ask_kind==ASK_RAID?"1  כתוב ומחק את המידע    2 / Esc  דלג על הדיסק"   /* #1808 */
                               :a->st.ask_kind==ASK_SHARE?"1  אשר וכתוב    2 / Esc  דלג"                     /* #1898 */
                               :a->st.ask_kind==ASK_BADSECT?"Enter / 1  המשך בכל זאת    2 / Esc  עצור"     /* #2017: Enter = the default */
                               :"1 / Esc  כבה    2  חזרה לתפריט";   /* 11/10 */
    if(a->confirm)return "Enter  אישור    Esc  ביטול";   /* #1270: the confirm dialog */
    if(a->screen==SCREEN_LCBOOT||a->screen==SCREEN_LOCAL)return lc_status_keys(a);   /* #1322 */
    if(a->cdlg.open)return a->cdlg.picker?"↑ ↓  בחירת דיסק    Del  בטל את הדיסק    Esc  המשך לכתוב"
                                         :"Del  בטל את הדיסק    Esc  המשך לכתוב";   /* #1386 */
    switch(a->screen){
    case SCREEN_LOGIN:return "Tab  מעבר    Enter  כניסה";
    case SCREEN_MENU:
        if(a->logout_confirming)return "Enter  התנתק    Esc  ביטול";
        snprintf(buf,n,"1–%d  בחירה    Enter  אישור    Esc  התנתק",
                 (a->admin?4:3)+(a->st.menu_class?1:0));
        return buf;
    case SCREEN_ROOM:
        if(!a->st.has_round&&a->dd_open==HIT_ROOM_IMAGE)
            return a->pick_in_folder?"1–9  בחירת אימג'    Esc / Backspace  לתיקיות":"1–9  בחירת תיקייה    Esc  סגירה";
        if(!a->st.has_round)return a->mode==MODE_DIRECT?"W  העֵר את המחשבים שנבחרו    Esc  חזרה":"Tab  מעבר    W  העֵר את המחשבים שנבחרו    Esc  חזרה";   /* 17/10: W wakes the pick; N: status_keys_off */
        if(a->st.wave_open)return "W  העֵר שוב    N  התחל עכשיו    Esc  עצור סבב…";
        { CancelPick p[MAX_CANCEL_PICKS];   /* #1386 */
          return room_cancel_picks(a,p,MAX_CANCEL_PICKS)?"X  בטל דיסק…    W  העֵר שוב    Esc  עצור סבב…":"W  העֵר שוב    Esc  עצור סבב…"; }
    case SCREEN_CLONER:   /* mockup 10/10: the SMART keys only while a question is open */
        if(a->st.server_restarted_wave)return "השרת אותחל באמצע הגל — המחשב יצטרף לבד לגל הבא";
        if(!a->st.smart_pending){   /* #1386: the drawers' own numbers cancel them */
            CancelPick p[MAX_CANCEL_PICKS]; int k=cloner_cancel_picks(a,p,MAX_CANCEL_PICKS); size_t u=0; buf[0]=0;
            for(int i=0;i<k&&u+8<n;i++)if(p[i].drawer>0&&p[i].drawer<10)u+=snprintf(buf+u,n-u,"%s%d",u?" / ":"",p[i].drawer);
            if(!u)return a->st.end_kind!=END_NONE&&a->st.end_held?"Enter  כיבוי":"אין פעולה במסך הזה";   /* #2081: the held end's button */
            snprintf(buf+u,n-u,"  בטל דיסק…");
            return buf;
        }
        return !strcmp(a->st.smart_verdict,"failed_last")   /* #1580: orange, retry */
        ? "R  החלף דיסק    S  דלג על הדיסק    Enter  נסה שוב…"
        : "R  החלף דיסק    S  דלג על הדיסק    Enter  כתוב בכל זאת…";   /* 16/10: "…" -- the SMART window asks first */
    case SCREEN_RESTORE:
        if(lc_pull_active(a)){   /* 16/10: the drawers by keyboard too */
            if(a->pull_focus)return "→ ←  מגירה    רווח  סימון    A  כל התקינות    Tab  לאימג'ים    Enter  מחק ושחזר…    Esc  חזרה";
            return a->pick_in_folder
            ? "1–9 / [ ]  בחירת אימג'    Esc / Backspace  לתיקיות    Tab  למגירות    A  כל התקינות    Enter  מחק ושחזר…"
            : "1–9  בחירת תיקייה    Tab  למגירות    A  כל התקינות    Enter  מחק ושחזר…    Esc  חזרה";
        }
        return a->pick_in_folder   /* owner 10/10: folder-first */
        ? "1–9 / [ ]  בחירת אימג'    Esc / Backspace  לתיקיות    Enter  מחק ושחזר…"
        : "1–9  בחירת תיקייה    Enter  מחק ושחזר…    Esc  חזרה";
    case SCREEN_STANDBY:return standby_back_locked(a)?"אין פעולה במסך הזה":"Esc  חזרה למסך הבחירה";   /* #2079 */   /* "דיסקים: 1 · 2 · 3 SMART" sat where keys go */
    case SCREEN_PICK:return a->chosen_disk>=0?"Tab / Shift-Tab  מעבר    Alt+Shift  שפה    Enter  התחלה    Esc  חזרה":"Esc  חזרה";   /* #1245 */
    case SCREEN_CLASS:return a->st.has_session?"Enter  התחל עכשיו":"Esc  חזרה";
    case SCREEN_DONE:return "Enter  קליטה נוספת";
    case SCREEN_PROGRESS:return "אין פעולה במסך הזה";   /* mockup 10/10: it said "Esc חזרה" and Esc did nothing */
    case SCREEN_TOOLS:return a->tools_view==TOOLS_CONFIRM?"Tab / Shift-Tab  מעבר    Esc  חזרה":"Esc  חזרה";
    case SCREEN_INSTALL:return "Tab / Shift-Tab  מעבר    Enter  אישור    Esc  חזרה";
    case SCREEN_FAILURE:{static char k[96];if(!a->st.nfail_btns)return "";snprintf(k,sizeof k,"Enter  %s",a->st.fail_btns[0].label);return k;}   /* #2068: the first button */
    default:return "אין פעולה במסך הזה";   /* message: there is no way out to offer */
    }
}

/* #1245: the typing language, at the start of the keys -- only while a text
 * field has the focus. "עב" filled in the action colour, "EN" outlined; a
 * click on either switches, as Alt+Shift does. On a field that takes English
 * only it is the reason, not a switch (owner, 10/10: a field that cannot take
 * Hebrew says why). Returns the width it took, 0 when nothing is drawn. */
static double draw_lang_chip(App *a,cairo_t *cr,double x,Rect bar){
    const Theme *t=a->theme;int lang=app_typing_lang(a);
    if(lang==LANG_NONE)return 0;
    if(lang==LANG_EN_ONLY){
        Text n=text_make(cr,FONT_SANS,12,400,"EN · שדה באנגלית בלבד",0,DIR_RTL);
        text_draw(cr,&n,x,bar.y+(bar.h-n.h)/2,t->muted);double w=n.w+3*N_STATUS_GAP;text_free(&n);return w;
    }
    int he=lang==LANG_HE;
    Text l=text_make(cr,FONT_SANS,12,600,he?"עב":"EN",0,he?DIR_RTL:DIR_LTR);
    Rect c={x,bar.y+(bar.h-22)/2,l.w+20,22};
    if(he)draw_fill_rrect(cr,c,RADIUS_SM,t->indigo);else draw_border_rrect(cr,c,RADIUS_SM,t->hair,1);
    text_draw(cr,&l,c.x+(c.w-l.w)/2,c.y+(c.h-l.h)/2,he?t->on_ink:t->ink);
    hit_add(a,c,HIT_LANG);double w=c.w+3*N_STATUS_GAP;text_free(&l);return w;
}

/* Nadav 16/10: keys that are on this screen but act on nothing now -- the
 * room's W and N before a round is open. They stay in the line, lighter than
 * the rest, so they are seen and known to be off (main.c sends nothing). */
static const char *status_keys_off(const App *a){
    if(a->sdlg.kind||a->confirm||a->st.shrink_pending||a->st.ask_pending)return NULL;
    if(a->screen==SCREEN_ROOM&&!a->st.has_round&&a->dd_open!=HIT_ROOM_IMAGE)return "N  התחל עכשיו";
    return NULL;
}

static void draw_status(App *a,cairo_t *cr,double W,double H){
    const Theme *t=a->theme; Rect bar={0,H-N_STATUS_H,W,N_STATUS_H};
    draw_fill_rrect(cr,bar,0,t->status_bg); cairo_rectangle(cr,0,bar.y,W,1);draw_set(cr,t->hair);cairo_fill(cr);
    char b[96];Rgb dot;const char *word=status_word(a,b,sizeof b,&dot);
    Text w=text_make(cr,FONT_SANS,12,400,word,0,DIR_RTL);
    double rx=W-N_STATUS_PAD;
    cairo_arc(cr,rx-N_STATUS_DOT/2,bar.y+bar.h/2,N_STATUS_DOT/2,0,2*M_PI);draw_set(cr,dot);cairo_fill(cr);
    text_draw_r(cr,&w,rx-N_STATUS_DOT-N_STATUS_GAP,bar.y+(bar.h-w.h)/2,t->ink);text_free(&w);
    if(a->screen==SCREEN_STANDBY){standby_status_info(a,cr,N_STATUS_PAD,bar.y+bar.h/2);return;}   /* #1304: IP · MAC · hello */
    double kx=N_STATUS_PAD+draw_lang_chip(a,cr,N_STATUS_PAD,bar);   /* #1245 */
    char kb[200];const char *on=status_keys(a,kb,sizeof kb),*off=status_keys_off(a);Text keys;
    if(a->screen==SCREEN_FAILURE){   /* #1569: the key, then IP · MAC · the last hello -- the evidence the server hears the hold */
        keys=text_make(cr,FONT_SANS,12,400,on,0,DIR_RTL);text_draw(cr,&keys,kx,bar.y+(bar.h-keys.h)/2,t->muted);
        standby_status_info(a,cr,kx+keys.w+3*N_STATUS_GAP,bar.y+bar.h/2);text_free(&keys);return;
    }
    if(off){                                       /* the off keys at the end of the line, in the paused grey */
        char e1[600],e2[200],m[1000],col[8];Rgb g=t->mark_line;
        snprintf(col,sizeof col,"#%02X%02X%02X",(int)lround(g.r*255),(int)lround(g.g*255),(int)lround(g.b*255));
        snprintf(m,sizeof m,"%s    <span foreground=\"%s\">%s</span>",text_escape(on,e1,sizeof e1),col,text_escape(off,e2,sizeof e2));
        keys=text_make_markup(cr,FONT_SANS,12,400,m,0,DIR_RTL);
    }else keys=text_make(cr,FONT_SANS,12,400,on,0,DIR_RTL);
    text_draw(cr,&keys,kx,bar.y+(bar.h-keys.h)/2,t->muted);text_free(&keys);
}

/* The owner, 10/10: the login fields as the console draws them -- the label
 * above, a bordered input (draw_field: the radius/border/focus ring of every
 * other input; user and password are LTR, the text from the left edge).
 * Returns the label's height + gap, where the box starts. */
#define LOGIN_LABEL_GAP 4.0
static double login_label(App *a,cairo_t *cr,double x,double y,double w,const char *label){
    Text l=rtl_block_make(cr,12,500,label,w);text_draw_r(cr,&l,x+w,y,a->theme->muted);
    double h=l.h+LOGIN_LABEL_GAP;text_free(&l);return h;
}

void screen_login(App *a,cairo_t *cr,double W,double H,double head_h){
    (void)head_h; const Theme *t=a->theme; double usable=H-N_STATUS_H;
    double pane=fmin(600,fmax(420,W*.32)); Rect pr={W-pane,0,pane,usable}, ar={0,0,W-pane,usable};
    draw_fill_rrect(cr,pr,0,t->surface);draw_station_art(cr,t,ar,1);
    cairo_rectangle(cr,pr.x,0,1,usable);draw_set(cr,t->hair);cairo_fill(cr);
    draw_brand_lockup_r(cr,W-64,56,6,t==&THEME_DARK,1);   /* #1302: the full lockup, as on the console's login */
    double fw=fmin(400,pane-128),x=pr.x+(pane-fw)/2;
    Text title=rtl_block_make(cr,24,600,"כניסה",fw);
    char lead_s[128];snprintf(lead_s,sizeof lead_s,"מחשב הבנייה %s",known(a->st.machine_name));   /* #1252 */
    Text lead=rtl_block_make(cr,13,400,lead_s,fw);
    double lab=text_line_height(cr,FONT_SANS,12)+LOGIN_LABEL_GAP;
    double content=title.h+6+lead.h+28+lab+N_FIELD_H+18+lab+N_FIELD_H+22+N_BUTTON_H+(a->error[0]?24:0);
    double y=(usable-content)/2;
    text_draw_r(cr,&title,x+fw,y,t->ink);y+=title.h+6;
    text_draw_r(cr,&lead,x+fw,y,t->muted);y+=lead.h+28;
    y+=login_label(a,cr,x,y,fw,"שם משתמש");
    draw_field(a,cr,(Rect){x,y,fw,N_FIELD_H},a->user,0,NULL,HIT_USER);y+=N_FIELD_H+18;
    y+=login_label(a,cr,x,y,fw,"סיסמה");
    Rect pf={x,y,fw,N_FIELD_H};
    draw_field(a,cr,pf,a->pass,!a->show_pw,NULL,HIT_PASS);   /* keeps 62px clear at the right (widgets.c) */
    /* only "הצג" inside the field, at its right end: action-colour words, no box */
    Text eye=text_make(cr,FONT_SANS,12,500,a->show_pw?"הסתר":"הצג",0,DIR_RTL);
    Rect er={pf.x+pf.w-eye.w-24,pf.y+(pf.h-24)/2,eye.w+16,24};
    text_draw(cr,&eye,er.x+8,er.y+(er.h-eye.h)/2,t->action_fg);hit_add(a,er,HIT_EYE);text_free(&eye);
    y+=N_FIELD_H+10;
    /* the error only after a failed attempt (do_login sets it), in its own row */
    if(a->error[0]){Text e=rtl_block_make(cr,13,400,a->error,fw);text_draw_r(cr,&e,x+fw,y,t->danger_fg);text_free(&e);y+=24;}
    y+=12;
    Text go=btn_label(cr,"כניסה");draw_btn(a,cr,(Rect){x,y,fw,N_BUTTON_H},&go,BTN_PRIMARY,HIT_SUBMIT);text_free(&go);
    char srv[160];server_label(&a->st,srv,sizeof srv);   /* #1252: was the mockup's fixed line */
    char foot_s[320];snprintf(foot_s,sizeof foot_s,"%s   ·   %s",srv,known(a->st.server_version));   /* 160 + 129 + separators */
    Text foot=text_make(cr,FONT_MONO,12,400,foot_s,0,DIR_LTR);text_draw(cr,&foot,pr.x+64,usable-28-foot.h,t->muted);text_free(&foot);
    text_free(&title);text_free(&lead);
}

/* mockup 10/10 (docs/design/apple-mockups/native.html, "תפריט מחשב הבנייה"):
 * a card is a decision -- icon, title, one line of what it does, and the
 * flow ("a → b → c", the three how[] steps) -- at the height of its content,
 * two to a row. warn is said after desc in the danger colour. */
typedef struct{const char *title,*desc,*warn,*how[3];int id,icon,admin,cls;}MenuItem;
#define MENU_N 5
static const MenuItem MENU[MENU_N]={
 { "קליטת אימג' חדש","הדיסק של המחשב הזה נקרא, מכווץ ומועלה לספרייה.",NULL,{"SMART","sha256","שם · תיאור · תיקייה"},HIT_CAPTURE,MENU_ICON_CAPTURE,1,0},
 { "שיכפול מהשרת","אימג' מהספרייה משודר במולטיקאסט לכל חדר השיכפול, בגלים.",NULL,{"אימג' + יעד","החדר מתעורר","גל כותב"},HIT_ROOM,MENU_ICON_SERVER,0,0},
 { "שיכפול ישיר","הדיסק של המחשב הזה משודר למגירות שנבחרו, בלי לשמור אימג'.",NULL,{"קריאה בלבד","סבב יחיד","מי שנבחר"},HIT_DIRECT,MENU_ICON_DIRECT,0,0},
 { "שחזור לדיסק הזה","אימג' מהספרייה נכתב על הדיסק הפנימי.","הדיסק נמחק.",{"אימג'","שם המכונה","כתיבה"},HIT_RESTORE,MENU_ICON_RESTORE,0,0},
 /* #1081: classes are v1.2 -- drawn only when the state file says menu_class=1 (absent = 0). */
 { "הפצה לכיתות","בוחרים כיתה, מחשבים ואימג' — הסבב מעיר את הכיתה ורץ בשרת.",NULL,{"כיתה + אימג'","הכיתה מתעוררת","הסבב רץ בשרת"},HIT_CLASSES,MENU_ICON_SERVER,0,1}
};

static void hex_rgb(Rgb c,char *out,size_t n){
    snprintf(out,n,"#%02X%02X%02X",(int)(c.r*255+.5),(int)(c.g*255+.5),(int)(c.b*255+.5));
}

/* mockup 10/10: the server pill says what the agent's last hello says
 * (hello_rc / hello_age from guistate.sh) -- never a fixed "connected": the
 * old pill was green on a server that was down. No hello yet is its own
 * words, not a colour (principle 5). Returns the pill's width. */
static double menu_server_pill(App *a,cairo_t *cr,double right,double y){
    const State *s=&a->st;char srv[160],label[240];int kind=PILL_OFF;
    server_label(s,srv,sizeof srv);
    if(s->hello_rc==200&&s->hello_age>=0&&s->hello_age<=120){snprintf(label,sizeof label,"שרת %s מחובר",srv);kind=PILL_OK;}
    else if(s->hello_rc==200){snprintf(label,sizeof label,"שרת %s · ענה לפני %d שנ'",srv,s->hello_age);kind=PILL_WARN;}
    else if(s->hello_rc>=0){snprintf(label,sizeof label,"שרת %s לא מחובר",srv);kind=PILL_BAD;}
    else snprintf(label,sizeof label,"שרת %s · החיבור טרם נבדק",srv);
    return draw_pill(cr,a->theme,right,y,label,kind,kind!=PILL_OFF);
}

/* The cloners, counted from the machine= records (machines[].awake). The
 * menu's own state carries none -- guistate.sh writes them in the room
 * modes only -- and then there is nothing true to say, so no pill. */
static void menu_cloner_pill(App *a,cairo_t *cr,double right,double y){
    const State *s=&a->st;int awake=0,ready=0;
    if(!s->nmachines)return;
    for(int i=0;i<s->nmachines;i++)if(s->machines[i].awake){awake++;ready+=s->machines[i].fresh_drawers;}
    char label[120];snprintf(label,sizeof label,"%d מחשבי שיכפול ערים · %d מגירות מוכנות",awake,ready);
    draw_pill(cr,a->theme,right,y,label,PILL_INFO,0);
}

/* One card's text, measured before anything is drawn: the cards of a row
 * share the row's height (the mockup's grid), so heights come first. */
typedef struct{Text title,desc,flow,key,pill;double h,tw;}MenuCard;

static MenuCard menu_card_make(App *a,cairo_t *cr,const MenuItem *m,int number,double cw){
    MenuCard k;char key[12],flow[160],e[400],mk[700],bad[8];
    snprintf(key,sizeof key,"%d",number);
    k.key=text_make(cr,FONT_MONO,12,400,key,0,DIR_LTR);
    k.pill=m->admin?text_make(cr,FONT_SANS,12,600,"admin",0,DIR_RTL):(Text){NULL,0,0};
    /* padding 20 | icon 48 | gap 14 | text | gap 14 | key chip | padding 20 */
    k.tw=cw-20-48-14-14-(k.key.w+12)-20;
    double pill_w=m->admin?k.pill.w+20+8:0;
    k.title=rtl_block_make(cr,18,600,m->title,k.tw-pill_w);
    text_escape(m->desc,e,sizeof e);
    if(m->warn){char w[120];text_escape(m->warn,w,sizeof w);hex_rgb(a->theme->danger,bad,sizeof bad);
        snprintf(mk,sizeof mk,"%s <span foreground=\"%s\" weight=\"bold\">%s</span>",e,bad,w);}
    else snprintf(mk,sizeof mk,"%s",e);
    k.desc=text_make_markup(cr,FONT_SANS,13,400,mk,(int)k.tw,DIR_RTL);
    pango_layout_set_alignment(k.desc.layout,PANGO_ALIGN_RIGHT);
    snprintf(flow,sizeof flow,"%s → %s → %s",m->how[0],m->how[1],m->how[2]);
    k.flow=text_make_ltr(cr,FONT_SANS,11,400,flow,(int)k.tw);
    k.h=18+dmax(48,k.title.h+4+k.desc.h+8+k.flow.h)+18;
    if(k.h<132)k.h=132;                       /* .act{min-height:132px} */
    return k;
}

static void menu_card_draw(App *a,cairo_t *cr,const MenuItem *m,MenuCard *k,Rect c,int focused){
    const Theme *t=a->theme;int hov=hovered(a,c);
    draw_fill_rrect(cr,c,t->radius,t->surface);
    draw_border_rrect(cr,c,t->radius,focused||hov?t->indigo:t->hair,focused?2:1);
    if(focused)draw_focus_ring(a,cr,c);
    double right=c.x+c.w-20,y=c.y+18;
    Rect ic={right-48,y,48,48};draw_fill_rrect(cr,ic,6,t->indigo_soft);
    draw_menu_icon(cr,(Rect){ic.x+12,ic.y+12,24,24},m->icon,t->action_fg);   /* on action-soft: the -fg tone reads */
    Rect kb={c.x+20,y,k->key.w+12,20};draw_fill_rrect(cr,kb,3,t->field);draw_border_rrect(cr,kb,3,t->hair,1);
    text_draw(cr,&k->key,kb.x+6,kb.y+(kb.h-k->key.h)/2,t->ink);
    double tr=ic.x-14;
    text_draw_r(cr,&k->title,tr,y,t->ink);
    if(m->admin){PangoRectangle lg;pango_layout_get_pixel_extents(k->title.layout,NULL,&lg);
        draw_pill(cr,t,tr-lg.width-8,y+1,"admin",PILL_WARN,0);}   /* next to the name: it never wraps the title */
    y+=k->title.h+4;
    text_draw_r(cr,&k->desc,tr,y,t->muted);y+=k->desc.h+8;
    text_draw_r(cr,&k->flow,tr,y,t->muted);
    text_free(&k->title);text_free(&k->desc);text_free(&k->flow);text_free(&k->key);text_free(&k->pill);
}

/* Esc on the menu signs out only after a yes (mockup 10/10: a stray Esc
 * dropped the session). Modal like the shrink question: it replaces the
 * hit table with its two buttons. */
static void draw_logout_confirm(App *a,cairo_t *cr,double W,double H){
    const Theme *t=a->theme;double usable=H-N_STATUS_H;
    cairo_rectangle(cr,0,0,W,usable);draw_set_a(cr,(Rgb){0,0,0},.55);cairo_fill(cr);
    a->nhits=0;a->clip_on=0;
    double dw=fmin(460,W-64),inner=dw-56;
    Text h=rtl_block_make(cr,20,600,"להתנתק?",inner);
    Text p=rtl_block_make(cr,13,400,"המסך יחזור לכניסה. משימה שכבר רצה ממשיכה לרוץ.",inner);
    double dh=28+h.h+6+p.h+24+N_BUTTON_H+28;
    Rect box={(W-dw)/2,(usable-dh)/2,dw,dh};
    draw_fill_rrect(cr,box,t->radius,t->surface);draw_border_rrect(cr,box,t->radius,t->hair,1);
    double right=box.x+box.w-28,y=box.y+28;
    text_draw_r(cr,&h,right,y,t->ink);y+=h.h+6;
    text_draw_r(cr,&p,right,y,t->muted);
    Text go=btn_label(cr,"התנתק"),no=btn_label(cr,"ביטול");
    double gw=btn_width(&go),nw=btn_width(&no),by=box.y+box.h-28-N_BUTTON_H;
    /* the approved window: the action at the edge, cancel next to it */
    draw_btn(a,cr,(Rect){box.x+28,by,gw,N_BUTTON_H},&go,BTN_PRIMARY,HIT_LOGOUT_CONFIRM);
    draw_btn(a,cr,(Rect){box.x+28+gw+N_ACTION_GAP,by,nw,N_BUTTON_H},&no,BTN_PLAIN,HIT_LOGOUT_CANCEL);
    text_free(&h);text_free(&p);text_free(&go);text_free(&no);
}

void screen_menu(App *a,cairo_t *cr,double W,double H,double head_h){
    const Theme *t=a->theme;double right=W-N_PAD,y=head_h+28,inner=W-2*N_PAD;
    int show[MENU_N] = {1, 1, 1, 1, 1};
    Text title=rtl_block_make(cr,24,600,"מה לעשות עם המחשב הזה?",inner);
    char ss[160];snprintf(ss,sizeof ss,"מחוברים כ-%s",a->signed_user);
    Text sub=rtl_block_make(cr,13,400,ss,inner);
    text_draw_r(cr,&title,right,y,t->ink);text_draw_r(cr,&sub,right,y+title.h+3,t->muted);
    double py=y+title.h+3+sub.h+10;
    double pw=menu_server_pill(a,cr,right,py);
    menu_cloner_pill(a,cr,right-pw-10,py);
    double line_y=py+24+12;cairo_rectangle(cr,N_PAD,line_y,inner,1);draw_set(cr,t->hair);cairo_fill(cr);
    /* #649: the toolbox entry -- a secondary 38px button at the title row's
     * inline end (left, next to the pills), bottom-aligned to the title block
     * like the mockup's .ph (align-items:flex-end; its padding-bottom is the
     * 12 above line_y), NOT one of the choice cards: it is
     * the IT person's drawer, not an operator flow. Build menu only (cloner
     * and class route before it). Registered before the body clip. v1 ships
     * without the toolbox (Nadav, 19/09): drawn only when the state file says
     * menu_tools=1 (absent = 0); v1.1 turns it on. */
    if (a->st.menu_tools) {
        Text tools_l = btn_label(cr, "כלים");
        double tools_w = btn_width(&tools_l);
        Rect tools_r = { N_PAD, line_y - 12 - N_BUTTON_H, tools_w, N_BUTTON_H };
        draw_btn(a, cr, tools_r, &tools_l, BTN_PLAIN, HIT_TOOLS);
        text_free(&tools_l);
        /* keyboard order: the cards, "התנתק", then "כלים" (main.c menu_stops) */
        if (a->menu_focus == (a->admin ? 4 : 3) + (a->st.menu_class ? 1 : 0) + 1) draw_focus_ring(a, cr, tools_r);
    }
    /* the cards: two columns (.acts grid 1fr 1fr, gap 16), card 1 at the
     * right, each row as tall as its tallest card -- not the screen */
    int idx[MENU_N],count=0,cols=2;double gap=16,gy=line_y+17,bottom=H-N_STATUS_H-24-N_BUTTON_H-18;
    for(int i=0;i<MENU_N;i++)
        if(show[i]&&!(MENU[i].admin&&!a->admin)&&!(MENU[i].cls&&!a->st.menu_class))idx[count++]=i;
    double cw=(inner-(cols-1)*gap)/cols;
    MenuCard cards[MENU_N];
    for(int k=0;k<count;k++)cards[k]=menu_card_make(a,cr,&MENU[idx[k]],k+1,cw);
    Rect body={0,line_y+1,W,bottom-line_y-1};   /* .sbody: the card grid, clipped */
    body_clip_begin(a, cr, body);
    double row_y=gy;
    for(int k=0;k<count;k+=cols){
        double row_h=cards[k].h;
        for(int j=k+1;j<k+cols&&j<count;j++)row_h=dmax(row_h,cards[j].h);
        for(int j=k;j<k+cols&&j<count;j++){
            Rect c={right-cw-(j-k)*(cw+gap),row_y,cw,row_h};
            menu_card_draw(a,cr,&MENU[idx[j]],&cards[j],c,a->menu_focus==j);
            hit_add(a,c,MENU[idx[j]].id);
        }
        row_y+=row_h+gap;
    }
    body_clip_end(a, cr);
    Text lo=btn_label(cr,"התנתק");double lw=btn_width(&lo);Rect lr={N_PAD,H-N_STATUS_H-24-N_BUTTON_H,lw,N_BUTTON_H};
    draw_btn(a,cr,lr,&lo,BTN_PLAIN,HIT_LOGOUT);text_free(&lo);
    if(a->menu_focus==count)draw_focus_ring(a,cr,lr);
    text_free(&title);text_free(&sub);
    if(a->logout_confirming)draw_logout_confirm(a,cr,W,H);
}

static void draw_cursor(cairo_t *cr,double x,double y){
    static const double P[][2]={{0,0},{0,16},{4,12},{7,19},{9.5,18},{6.5,11},{11.5,11}};
    cairo_move_to(cr,x,y);for(int i=1;i<7;i++)cairo_line_to(cr,x+P[i][0],y+P[i][1]);cairo_close_path(cr);
    cairo_set_source_rgb(cr,1,1,1);cairo_fill_preserve(cr);cairo_set_source_rgb(cr,0,0,0);cairo_set_line_width(cr,1);cairo_stroke(cr);
}

void app_route(App *a){
    const State *s=&a->st;
    if(a->install.active){a->screen=SCREEN_INSTALL;return;}
    if(s->fail_title[0]){a->screen=SCREEN_FAILURE;return;}   /* #1569: the agent's own failure, before everything */
    /* #1322: the boot choice and the local clone own a cloner's screen while the agent says so */
    /* 16/10: lc_screen=pull is the build machine's restore screen, its targets the drawers (lc_pull.c) */
    if((a->force_cloner||a->force_standby)&&s->lc.screen==LC_SCREEN_PULL){a->mode=MODE_RESTORE;a->screen=SCREEN_RESTORE;return;}
    if((a->force_cloner||a->force_standby)&&s->lc.screen){a->screen=s->lc.screen==LC_SCREEN_BOOT?SCREEN_LCBOOT:SCREEN_LOCAL;return;}
    if(a->force_standby){a->screen=SCREEN_STANDBY;return;}
    if(a->force_cloner){a->screen=(s->ndrawers||s->cloner_image[0]||s->smart_pending)?SCREEN_CLONER:SCREEN_STANDBY;return;}
    if(s->msg_title[0]){a->screen=SCREEN_MESSAGE;return;}
    if(a->force_progress||s->task==TASK_PENDING||s->task==TASK_RUNNING){a->screen=SCREEN_PROGRESS;a->watching=1;return;}
    if(a->watching&&(s->task==TASK_DONE||s->task==TASK_FAILED)){
        a->watching=0;a->showing_done=1;
        if(s->task==TASK_DONE&&s->task_direct){snprintf(a->done_title,sizeof a->done_title,"ההפצה הישירה הושלמה");snprintf(a->done_sub,sizeof a->done_sub,"הדיסק %s שודר למגירות שנבחרו. שום דבר לא נשמר בשרת.",s->task_disk);}
        else if(s->task==TASK_DONE){snprintf(a->done_title,sizeof a->done_title,"הקליטה הושלמה");snprintf(a->done_sub,sizeof a->done_sub,"\"%s\" נכנס לספרייה.",s->task_name);}
        else{snprintf(a->done_title,sizeof a->done_title,"המשימה נכשלה");snprintf(a->done_sub,sizeof a->done_sub,RLM "%s",s->task_error[0]?s->task_error:"ראו את היומן בקונסולה.");}   /* #1502: the server sends "sda: ..." */
    }
    if(a->showing_done){a->screen=SCREEN_DONE;return;}
    if(a->mode==MODE_CLASSES&&(a->signed_in||s->has_session)){a->screen=SCREEN_CLASS;return;}
    if(!a->signed_in){a->screen=SCREEN_LOGIN;return;}
    switch(a->mode){
    case MODE_ROOM: case MODE_DIRECT: a->screen = SCREEN_ROOM; break;
    case MODE_CAPTURE: a->screen = SCREEN_PICK; break;
    case MODE_RESTORE: a->screen = SCREEN_RESTORE; break;
    case MODE_TOOLS:   a->screen = SCREEN_TOOLS; break;     /* #649 */
    default:           a->screen = SCREEN_MENU; break;
    }
}

static void draw_state_warning(App *a,cairo_t *cr,double W,double H){
    if(!a->st.state_warning[0])return;
    const Theme *t=a->theme;
    Text v=text_make(cr,FONT_SANS,12,600,a->st.state_warning,0,DIR_RTL);
    Rect b={(W-v.w-32)/2,H-N_STATUS_H-36,v.w+32,28};draw_fill_rrect(cr,b,t->radius,t->warning_soft);draw_border_rrect(cr,b,t->radius,t->warn,1);text_draw(cr,&v,b.x+16,b.y+(b.h-v.h)/2,t->warn);text_free(&v);
}

/* mockup 10 (#1858, Nadav 24/10): a cloner drawer whose disk is known to stop the BIOS on F1 --
 * SMART red because its health failed or could not be read (#2078). Nothing else is a reason to
 * name F1: "מאושר רק אם אנחנו יודעים שיש F1 לדיסק". 1 = found, its drawer and model in d. */
static int app_f1_disk(const App *a,const IdleDisk **d){
    for(int i=0;i<a->st.nidisks;i++){const IdleDisk *x=&a->st.idisks[i];
        if(strcmp(x->smart,"fail"))continue;
        if(!x->cause[0]||!strcmp(x->cause,"health_failed")||!strcmp(x->cause,"health_unread")){if(d)*d=x;return 1;}}
    return 0;
}

/* #434 / mockup 10: the power-off window, over every screen: the agent found nobody here for
 * IDLE_POWEROFF_S. Any key keeps the machine on (the GUI emits "touch"); Enter or "כבה עכשיו"
 * powers off now (idle-poweroff). A drawer with a disk that stops the BIOS on F1 says so. */
static void draw_idle_countdown(App *a,cairo_t *cr,double W,double H){
    if(a->st.idle_poweroff_at<=0)return;
    const Theme *t=a->theme;
    long long left=a->fixed_clock?30:a->st.idle_poweroff_at-(long long)time(NULL); if(left<0)left=0;
    cairo_rectangle(cr,0,N_HEADER_H,W,H-N_HEADER_H-N_STATUS_H);draw_set_a(cr,(Rgb){0,0,0},.35);cairo_fill(cr);
    char ttl[96],sub[160],f1[400];
    snprintf(ttl,sizeof ttl,"המחשב ייכבה בעוד %lld שניות",left);
    int m=a->st.idle_poweroff_s/60;
    if(m>0)snprintf(sub,sizeof sub,"%d דקות בלי פעילות. כל מקש משאיר אותו דלוק.",m);
    else snprintf(sub,sizeof sub,"אין פעילות במחשב. כל מקש משאיר אותו דלוק.");
    const IdleDisk *fd=NULL;int f1on=app_f1_disk(a,&fd);
    double bw=fmin(560,W-48),in=bw-2*24;
    Text tt=text_make(cr,FONT_SANS,18,600,ttl,(int)in,DIR_RTL),ts=text_make(cr,FONT_SANS,13,400,sub,(int)in,DIR_RTL);
    Text fh={0},fb={0};
    if(f1on){
        char drw[32];if(fd->drawer>0)snprintf(drw,sizeof drw,"במגירה %d",fd->drawer);else snprintf(drw,sizeof drw,"במחשב");
        snprintf(f1,sizeof f1,"%s יש דיסק שה-BIOS עוצר עליו: \u2066%s\u2069, \u200fSMART אדום. הוציאו אותו לפני ההדלקה הבאה, או לחצו F1 ליד המחשב כשהוא נדלק.",drw,fd->model[0]?fd->model:"דיסק");
        fh=text_make(cr,FONT_SANS,13,700,"בהדלקה הבאה ה-BIOS יעצור על F1",(int)(in-24-36),DIR_RTL);
        fb=text_make(cr,FONT_SANS,12.5,400,f1,(int)(in-24-36),DIR_RTL);
    }
    double fbh=f1on?12+fh.h+4+fb.h+12:0;
    double h=24+tt.h+6+ts.h+(f1on?14+fbh:0)+18+38+22;
    Rect b={(W-bw)/2,N_HEADER_H+(H-N_HEADER_H-N_STATUS_H-h)/2,bw,h};
    draw_fill_rrect(cr,b,t->radius,t->surface);draw_border_rrect(cr,b,t->radius,t->hair,1);
    double y=b.y+24,xr=b.x+b.w-24;
    text_draw_r(cr,&tt,xr,y,t->ink);y+=tt.h+6;text_draw_r(cr,&ts,xr,y,t->muted);y+=ts.h;
    if(f1on){
        y+=14;Rect fr={b.x+24,y,in,fbh};
        draw_fill_rrect(cr,fr,RADIUS_SM,t->warning_soft);draw_border_rrect(cr,fr,RADIUS_SM,t->warn,1);
        double cx=fr.x+fr.w-12-10,cy=fr.y+12+10;
        cairo_arc(cr,cx,cy,10,0,2*M_PI);draw_set(cr,t->warn);cairo_fill(cr);
        Text ex=text_make(cr,FONT_SANS,13,700,"!",0,DIR_LTR);text_draw(cr,&ex,cx-ex.w/2,cy-ex.h/2,t->on_ink);text_free(&ex);
        text_draw_r(cr,&fh,fr.x+fr.w-12-24,fr.y+12,t->warn);text_draw_r(cr,&fb,fr.x+fr.w-12-24,fr.y+12+fh.h+4,t->ink);
        y+=fbh;text_free(&fh);text_free(&fb);
    }
    y+=18;   /* the buttons at the end (left): "השאר דלוק" the primary, "כבה עכשיו" beside it */
    double kw=lc_btn_width(cr,"השאר דלוק","Esc"),ow=lc_btn_width(cr,"כבה עכשיו","Enter");
    double r=b.x+24+ow+10+kw;
    lc_btn(a,cr,r,y,38,"השאר דלוק","Esc",BTN_PRIMARY,HIT_IDLE_KEEP,0);
    lc_btn(a,cr,b.x+24+ow,y,38,"כבה עכשיו","Enter",BTN_PLAIN,HIT_IDLE_OFF,0);
    text_free(&tt);text_free(&ts);
}

/* #1270 (owner, 10/10): no typed-word confirmation anywhere. A destructive
 * action asks here: the question, what will happen, [ביטול] (bordered) and
 * the red action; Enter confirms, Esc cancels (main.c confirm_modal). */
void draw_confirm_dialog(App *a,cairo_t *cr,double W,double H){
    if(!a->confirm)return;
    const Theme *t=a->theme;const State *s=&a->st;double usable=H-N_STATUS_H;
    char title[200],what[1400],ok[120];
    if(a->confirm==CONFIRM_ROOM_START_WARN){
        /* #432: orange -- a question, not a block; the server's own lines */
        snprintf(title,sizeof title,"להתחיל את הגל למרות האזהרה?");
        size_t u=0;what[0]=0;
        for(int i=0;i<s->nroom_warn&&i<4;i++){
            int k=snprintf(what+u,sizeof what-u,"%s" RLM "%s",u?"\n":"",s->room_warn[i]);
            if(k<0||(size_t)k>=sizeof what-u)break;
            u+=(size_t)k;
        }
        if(s->nroom_warn>4&&u<sizeof what)snprintf(what+u,sizeof what-u,"\nועוד %d מגירות",s->nroom_warn-4);
        u=strlen(what);
        snprintf(what+u,sizeof what-u,"\nקישור איטי או CRC שעלה הם בדרך כלל כבל או מגירה. ההתחלה תירשם ביומן עם האזהרה.");
        snprintf(ok,sizeof ok,"התחל בכל זאת");
    }else if(a->confirm==CONFIRM_ROOM_POWEROFF){
        char name_i[100];bidi_isolate(a->poweroff_name,name_i,sizeof name_i);
        snprintf(title,sizeof title,"לכבות את %s?",name_i);
        if(a->poweroff_local_copy){
            snprintf(what,sizeof what,"המחשב באמצע שיכפול מקומי — כיבוי עכשיו ישאיר את דיסק היעד שבור ולא שמיש. לכבות בכל זאת?");
            snprintf(ok,sizeof ok,"כבה בכל זאת");
        }else{
            snprintf(what,sizeof what,"המחשב נכבה דרך הסוכן ויוצא מהגל הפתוח — כדי להחליף בו מגירות. אחרי ההחלפה מדליקים אותו (או מעירים), והוא מצטרף לגל לבד.");
            snprintf(ok,sizeof ok,"כבה אותו");
        }
    }else if(a->confirm==CONFIRM_ROOM_POWEROFF_ALL){
        int n=0;for(int i=0;i<s->nmachines;i++)n+=s->machines[i].local_copy;
        snprintf(title,sizeof title,"לכבות את כל החדר?");
        if(n)snprintf(what,sizeof what,"%d מחשבים באמצע שיכפול מקומי — הדיסקים שלהם יישארו שבורים. לכבות בכל זאת?",n);
        else snprintf(what,sizeof what,"כל מחשבי השיכפול שממתינים ייכבו דרך הסוכן.");
        snprintf(ok,sizeof ok,"%s",n?"כבה בכל זאת":"כבה את כל החדר");
    }else if(a->confirm==CONFIRM_RESTORE&&lc_pull_active(a)){
        /* 16/10: the cloner's drawers, by disk number; the round they go into, and its end */
        const char *img=a->restore_image_sel>0&&a->restore_image_sel<=s->nimages?s->images[a->restore_image_sel-1].name:"";
        char names[400],mach_i[120],img_i[200];
        lc_pull_names(a,names,sizeof names);
        bidi_isolate(known(s->machine_name),mach_i,sizeof mach_i);
        bidi_isolate(img,img_i,sizeof img_i);
        snprintf(title,sizeof title,"למחוק את המגירות ולשחזר?");
        snprintf(what,sizeof what,"כל התוכן של %s במחשב %s יימחק, והאימג' \"%s\" ייכתב עליהן. "
                 "המחשב יצטרף לסבב בשרת ויכבה בסוף, להחלפת המגירות. אי־אפשר לבטל אחרי שהתחיל.",names,mach_i,img_i);
        snprintf(ok,sizeof ok,"מחק ושחזר");
    }else if(a->confirm==CONFIRM_RESTORE){
        const Disk *d=NULL;for(int i=0;i<s->ndisks;i++)if(!s->disks[i].removable){d=&s->disks[i];break;}
        const char *img=a->restore_image_sel>0&&a->restore_image_sel<=s->nimages?s->images[a->restore_image_sel-1].name:"";
        snprintf(title,sizeof title,"למחוק את הדיסק ולשחזר?");
        /* the disk, the machine and the image are Latin runs inside an RTL line:
         * isolated (FSI/PDI); "model · /dev/sda" -- no brackets, which this renderer mirrors */
        char disk[200],disk_i[220],mach_i[120],img_i[200];
        snprintf(disk,sizeof disk,"%s · %s",d&&d->model[0]?d->model:"הדיסק הפנימי",d?d->dev:"—");
        bidi_isolate(disk,disk_i,sizeof disk_i);
        bidi_isolate(known(s->machine_name),mach_i,sizeof mach_i);
        bidi_isolate(img,img_i,sizeof img_i);
        snprintf(what,sizeof what,"כל התוכן של %s במחשב %s יימחק, והאימג' \"%s\" ייכתב עליו. אי־אפשר לבטל אחרי שהתחיל.",
                 disk_i,mach_i,img_i);
        snprintf(ok,sizeof ok,"מחק ושחזר");
    }else if(a->confirm==CONFIRM_RESTORE_CANCEL){
        /* #1561 (Nadav 17/10): the write stops and the table goes -- an empty disk, not a half one */
        snprintf(title,sizeof title,"לבטל את השחזור?");
        snprintf(what,sizeof what,"הכתיבה תיעצר וטבלת המחיצות תימחק (הראשית והגיבוי): הדיסק יישאר ריק ולא יעלה ממנו דבר. אחר כך המחשב יכבה.");
        snprintf(ok,sizeof ok,"בטל את השחזור");
    }else if(a->confirm==CONFIRM_ROOM_STOP||a->confirm==CONFIRM_CLASS_STOP){
        snprintf(title,sizeof title,"לעצור את הסבב?");
        snprintf(what,sizeof what,"השידור של \"%s\" ייעצר באמצע. מגירות שלא סיימו לא יהיו שמישות ויש לכתוב אותן מחדש.",
                 a->confirm==CONFIRM_ROOM_STOP?s->round_image:s->sess_image);
        snprintf(ok,sizeof ok,"עצור סבב");
    }else{
        const Tool *tl=a->tool_sel>=0&&a->tool_sel<a->ntools?&a->tools[a->tool_sel]:NULL;
        int lvl=tl?tools_risk_level(tl->risk):2;
        snprintf(title,sizeof title,"להריץ את \"%s\"?",tl?tl->title:"הכלי");
        snprintf(what,sizeof what,lvl==2?"פעולה הרסנית על המחשב %s: מה שיימחק לא יחזור.":"הפעולה משנה את הדיסק של המחשב %s.",
                 known(a->tools_machine));
        snprintf(ok,sizeof ok,"הרץ");
    }
    cairo_rectangle(cr,0,0,W,usable);draw_set_a(cr,(Rgb){0,0,0},.55);cairo_fill(cr);
    a->nhits=0;a->clip_on=0;                       /* modal: only its two buttons */
    double dw=fmin(520,W-64),inner=dw-56;
    Text h=rtl_block_make(cr,20,600,title,inner);
    Text p=rtl_block_make(cr,13,400,what,inner);
    double dh=28+h.h+8+p.h+24+N_BUTTON_H+28;
    Rect box={(W-dw)/2,(usable-dh)/2,dw,dh};
    draw_fill_rrect(cr,box,t->radius,t->surface);draw_border_rrect(cr,box,t->radius,t->hair,1);
    double right=box.x+box.w-28,y=box.y+28;
    text_draw_r(cr,&h,right,y,t->ink);y+=h.h+8;
    text_draw_r(cr,&p,right,y,t->muted);
    Text go=btn_label(cr,ok),no=btn_label(cr,"ביטול");
    double gw=btn_width(&go),nw=btn_width(&no),by=box.y+box.h-28-N_BUTTON_H;
    /* the approved window: the action at the inline end, cancel next to it */
    /* #432: the start question is a warning (orange, BTN_WARN); every destructive OK stays red */
    if(a->confirm==CONFIRM_ROOM_START_WARN)draw_btn(a,cr,(Rect){box.x+28,by,gw,N_BUTTON_H},&go,BTN_WARN,HIT_CONFIRM_OK);
    else draw_btn(a,cr,(Rect){box.x+28,by,gw,N_BUTTON_H},&go,BTN_DANGER,HIT_CONFIRM_OK);
    draw_btn(a,cr,(Rect){box.x+28+gw+N_ACTION_GAP,by,nw,N_BUTTON_H},&no,BTN_PLAIN,HIT_CONFIRM_CANCEL);
    text_free(&h);text_free(&p);text_free(&go);text_free(&no);
}

/* clamp(H/800, 1, 1.4), taken down to a quarter: a 1px line then lands on
 * a quarter pixel at most (1080p = 1.25, 60px header, 45px status bar) --
 * 1.35 smeared every hairline over two rows. */
double ui_scale(int H){double s=floor(H/800.0*4)/4;return s<1?1:s>1.4?1.4:s;}

/* The screen in logical pixels: W x H = the framebuffer / a->scale. */
static void app_draw_logical(App *a,cairo_t *cr,double W,double H){
    a->nhits=0;a->clip_on=0;a->end_off_own=0;app_route(a);draw_station_background(cr,a->theme,W,H);
    if(a->screen==SCREEN_INSTALL){screen_install(a,cr,W,H);return;}
    double head_h=a->screen==SCREEN_LOGIN?0:draw_header(a,cr,W);
    switch(a->screen){
    case SCREEN_LOGIN:    screen_login(a, cr, W, H, head_h); break;
    case SCREEN_MENU:     screen_menu(a, cr, W, H, head_h); break;
    case SCREEN_PICK:     screen_pick(a, cr, W, H, head_h); break;
    case SCREEN_PROGRESS: screen_progress(a, cr, W, H, head_h); break;
    case SCREEN_DONE:     screen_done(a, cr, W, H, head_h); break;
    case SCREEN_ROOM:     screen_room(a, cr, W, H, head_h); break;
    case SCREEN_CLASS:    screen_class(a, cr, W, H, head_h); break;
    case SCREEN_CLONER:   screen_cloner(a, cr, W, H, head_h); break;
    case SCREEN_STANDBY:  screen_standby(a, cr, W, H, head_h); break;
    case SCREEN_MESSAGE:  screen_message(a, cr, W, H, head_h); break;
    case SCREEN_RESTORE:  screen_restore(a, cr, W, H, head_h); break;
    case SCREEN_TOOLS:    screen_tools(a, cr, W, H, head_h); break;     /* #649 */
    case SCREEN_LCBOOT:   screen_lcboot(a, cr, W, H, head_h); break;   /* #1322 */
    case SCREEN_LOCAL:    screen_local(a, cr, W, H, head_h); break;
    case SCREEN_FAILURE:  screen_failure(a, cr, W, H, head_h); break;   /* #1569 */
    case SCREEN_INSTALL:  break;
    }
    draw_state_warning(a,cr,W,H);draw_idle_countdown(a,cr,W,H);
    draw_shrink_dialog(a,cr,W,H);   /* #928: modal, over every screen but the status bar */
    draw_ask_dialog(a,cr,W,H);      /* 11/10: the agent's question, the same way */
    if(a->screen==SCREEN_RESTORE)draw_syspart_dialog(a,cr,W,H);   /* #1896: the C: picker */
    draw_confirm_dialog(a,cr,W,H);  /* #1270 */
    sdlg_draw(a,cr,W,H);            /* 16/10: the SMART warning window -- over all of them */
    cdlg_draw(a,cr,W,H);            /* #1386: "בטל דיסק זה" */
    draw_end_dialog(a,cr,W,H);      /* #1560: the end-of-task count to power-off -- the last word */
    draw_power_dialog(a,cr,W,H);    /* #1918: the power button during a write -- over all of them */
    draw_status(a,cr,W,H);draw_toast(a,cr,W,H);
    draw_theme_toggle(a,cr,W,head_h);   /* #1499: last -- over every screen and dialog, and its hit wins */
}

/* mockup 10/10 / audit finding 4: one factor for the whole screen, so a
 * 1080p screen is not a 1024x768 layout with more empty space. */
static DcuiFrame usbset_f;   /* #1914: the USB stick's settings frame, as last drawn */
int app_usbset_window(const App *a){
    return a->screen==SCREEN_LOCAL&&a->st.lc.screen==LC_SCREEN_USBSET&&usbset_f.valid&&usbset_f.dialog&&!strcmp(usbset_f.dlg_kind,"map");
}
void app_draw(App *a,cairo_t *cr,int W,int H){
    app_route(a);   /* #1914: this frame's screen, before the choice below */
    if(a->screen==SCREEN_LOCAL&&a->st.lc.screen==LC_SCREEN_USBSET&&a->tools_dir[0]){
        /* #1914 (Nadav 20/10): the USB stick's settings ARE the server's settings screen (screens_dcui.c
           settings_body/dialog); only the data differs -- <GUI_DIR>/usbset.frame, written by lcgui.sh */
        DcuiFrame *f=&usbset_f;char path[600];snprintf(path,sizeof path,"%s/usbset.frame",a->tools_dir);
        if(dcui_load(f,path)==0){a->nhits=0;
            snprintf(f->lang_chip,sizeof f->lang_chip,"%s",a->lang_he?"עב":"EN");   /* #2001: the name's typing language (Alt+Shift) */
            dcui_draw(f,a->theme,cr,W,H);return;}
    }
    double s=ui_scale(H);a->scale=s;
    cairo_save(cr);cairo_scale(cr,s,s);
    app_draw_logical(a,cr,W/s,H/s);
    cairo_restore(cr);
}

void app_draw_pointer(App *a,cairo_t *cr){if(a->ptr_visible)draw_cursor(cr,a->ptr_x,a->ptr_y);}
int app_hit(const App *a,double x,double y){double s=a->scale>0?a->scale:1;x/=s;y/=s;   /* screen pixels in, logical hits */
    for(int i=a->nhits-1;i>=0;i--){if(rect_has(a->hits[i].r,x,y))return a->hits[i].id;}return HIT_NONE;}
