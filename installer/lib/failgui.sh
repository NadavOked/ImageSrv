# failgui.sh -- a failure on the GUI, display-only, on the server's own screen (#1741). POSIX sh.
#
# Owner, 17/10: no screen that is not the GUI. Three screens a person stands
# in front of were still text: a first boot that failed (S3,
# tools/iso/firstboot.sh), the live installer's hold (S9,
# installer/imagesrv-installer) and the server going down (S7,
# deploy/imagesrv-shutdown-screen.sh). Each writes the records of the one
# failure screen (#1569, docs/architecture/interfaces.md) with role
# server|installer -- the header then says whose screen it is, not a
# station's -- and runs imagesrv-station-gui on the framebuffer with
# --no-input: it shows, it takes no key. The reason is on it in Hebrew, and
# the log's own English line under it: the GUI never hides the cause.
#
# "Shown" is evidence, not a started process (principle 5, #2517): the GUI
# touches $IMAGESRV_GUI_BEAT after it presented a frame (main.c
# gui_heartbeat). failgui_beat waits for that file while the GUI is alive.
# No binary, no framebuffer, or no beat: the caller's text stays the screen.
#
# The Hebrew lives here and only here: the callers' runtime text is ASCII
# (tests/test_iso_build.py) -- the text console has no Hebrew font.

FAILGUI_WAIT_S="${FAILGUI_WAIT_S:-15}"

failgui_bin() {
    # The first executable file of $@. Prints it; 1 = none.
    for _gb in "$@"; do
        if [ -f "$_gb" ] && [ -x "$_gb" ]; then printf '%s\n' "$_gb"; return 0; fi
    done
    return 1
}

failgui_beat() {
    # $1 = beat file (removed by the caller before the start), $2 = seconds,
    # then the command that says the GUI is alive. 0 = a frame was presented
    # (the beat exists); 1 = it exited without one, or drew nothing in time.
    _gt_f=$1; _gt_n=$2; shift 2
    while :; do
        [ -e "$_gt_f" ] && return 0
        if ! "$@"; then   # gone -- the beat may have landed just before that was seen
            if [ -e "$_gt_f" ]; then return 0; fi
            return 1
        fi
        [ "$_gt_n" -gt 0 ] || return 1
        sleep 1; _gt_n=$((_gt_n - 1))
    done
}

failgui_clean() { printf '%s' "$1" | tr '\r\n|' '   '; }

failgui_ascii() {
    # The log's English line, as it is: printable ASCII, 200 at most (a step is 240 bytes in the GUI).
    printf '%s' "$1" | LC_ALL=C tr -c ' -~' ' ' | cut -c1-200
}

failgui_records() {
    # $1 tone (bad|inf), $2 hold (0|1), $3 role (server|installer), $4 title,
    # $5 sub (the case's English name), $6 what happened, $7 the log's line
    # ("" = none), $8.. what to do. Prints the records; the caller adds fail_ctx.
    printf 'fail=%s|%s|%s|%s|%s\n' "$1" "$2" "$(failgui_clean "$4")" "$(failgui_clean "$5")" "$3"
    printf 'fail_why=%s\n' "$(failgui_clean "$6")"
    _gr_line=$7; shift 7
    for _gr_s in "$@"; do printf 'fail_step=%s\n' "$(failgui_clean "$_gr_s")"; done
    # LRI ... PDI: the English line keeps its own direction inside the Hebrew sentence
    if [ -n "$_gr_line" ]; then printf 'fail_step=השורה ביומן: \342\201\246%s\342\201\251\n' "$(failgui_ascii "$_gr_line")"; fi
}

failgui_firstboot() {
    # $1 = firstboot's state word, $2 = its English message: the S3 screen.
    case "$1" in
        payload-failed)      _gf_w="בניית קובצי האתחול של השרת (שלב א׳) נכשלה, ולכן מחשבים ברשת עוד לא יכולים לעלות ממנו." ;;
        apt-repo-missing)    _gf_w="מאגר החבילות שהמתקין השאיר בשרת חסר או פגום, והגדרת השרת בלי אינטרנט לא יכולה להמשיך." ;;
        wizard-guard-failed) _gf_w="חומת האש שמגינה על אשף ההתקנה לא נטענה, ולכן האשף לא הופעל." ;;
        wizard-failed)       _gf_w="אשף ההתקנה בדפדפן (פורט 8081) לא עלה." ;;
        installer-gui-failed) _gf_w="מסך ההתקנה לא צייר דבר על המסך הזה. אשף ההתקנה בדפדפן (פורט 8081) פועל." ;;
        answers-*)           _gf_w="התשובות שנאספו במתקין חסרות או פגומות, ולכן השרת לא הוגדר מהן." ;;
        hostname-failed)     _gf_w="שם השרת שנבחר במתקין לא נקבע." ;;
        installer-failed)    _gf_w="הגדרת השרת מהתשובות של המתקין נכשלה." ;;
        verify-failed)       _gf_w="בדיקת קובצי האתחול אחרי הגדרת השרת נכשלה." ;;
        *)                   _gf_w="ההכנה של השרת נעצרה בבדיקה שאין לה הסבר כאן." ;;
    esac
    failgui_records bad 1 server "ההכנה הראשונה של השרת נכשלה" "first boot failed · $1" "$_gf_w" "$2" \
        "השרת לא הושלם, והוא נשאר דלוק. ‏Alt+F2 פותח מסוף כניסה." \
        "היומן המלא: ⁦journalctl -u imagesrv-firstboot⁩"
    printf 'fail_ctx=שלב|%s\n' "$(failgui_clean "$1")"
}

failgui_installer() {
    # $1 = the hold's English line: the S9 screen.
    case "$1" in
        "install media is not mounted"*)
            _gi_t="מדיית ההתקנה לא נמצאה"; _gi_s="install media not found"
            _gi_w="מדיית ההתקנה (הדיסק-און-קי או ה-ISO) לא עוגנה, ולכן אין ממה להתקין. לא נכתב דבר לדיסק."
            _gi_d="חברו מחדש את הדיסק-און-קי או את ה-ISO, והפעילו את המחשב מחדש." ;;
        "no keyboard found"*)
            _gi_t="לא נמצאה מקלדת"; _gi_s="no keyboard"
            _gi_w="ההתקנה נענית במקלדת שבמסך הזה, ולא נמצאה מקלדת מחוברת. לא נכתב דבר לדיסק."
            _gi_d="חברו מקלדת והפעילו את המחשב מחדש — ההתקנה תתחיל מההתחלה." ;;
        "GUI bridge"*)
            _gi_t="רכיב של ההתקנה חסר"; _gi_s="installer bridge missing"
            _gi_w="גשר ההתקנה חסר בתמונת ההתקנה הזו, ולכן ההתקנה לא יכולה להתחיל. לא נכתב דבר לדיסק."
            _gi_d="התקינו מתמונת ISO אחרת של ImageSrv." ;;
        "the install GUI exited"*)
            _gi_t="מסך ההתקנה נסגר"; _gi_s="install screen exited"
            _gi_w="מסך ההתקנה נסגר לפני שההתקנה הסתיימה, והמחשב לא הופעל מחדש."
            _gi_d="הפעילו את המחשב מחדש מהמדיה כדי להתקין שוב." ;;
        *)
            _gi_t="ההתקנה נעצרה"; _gi_s="installer stopped"
            _gi_w="ההתקנה נעצרה לפני שהתחילה."
            _gi_d="הפעילו את המחשב מחדש מהמדיה." ;;
    esac
    failgui_records bad 1 installer "$_gi_t" "$_gi_s" "$_gi_w" "$1" "$_gi_d"
}

failgui_shutdown() {
    # $1 = reboot|poweroff: the S7 screen -- not a failure (tone inf), and nothing to hold.
    case "$1" in
        reboot) _gd_t="השרת מאתחל"; _gd_s="server restart"; _gd_a="אתחול"
                _gd_w="השירותים של השרת נעצרים, ואחר כך המחשב יעלה מחדש לבד."
                _gd_d="אין צורך לעשות דבר. כשהשרת יעלה, מסך השרת יחזור לכאן." ;;
        *)      _gd_t="השרת נכבה"; _gd_s="server shutdown"; _gd_a="כיבוי"
                _gd_w="השירותים של השרת נעצרים, ואחר כך המחשב ייכבה."
                _gd_d="אין צורך לעשות דבר. המחשב ייכבה לבד כשכל השירותים ייעצרו." ;;
    esac
    failgui_records inf 0 server "$_gd_t" "$_gd_s" "$_gd_w" "" "$_gd_d"
    printf 'fail_ctx=פעולה|%s\n' "$_gd_a"
    printf 'fail_ctx=התחיל ב|%s\n' "$(date +%H:%M:%S)"
}
