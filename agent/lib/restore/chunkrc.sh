# chunkrc.sh -- what `chunkcast recv` / `apply` exit codes mean (#2533, #3259). POSIX sh (busybox ash).
#
# The codes are written in agent/chunkcast.c; chunkrecv.sh reads them after every pass.

_chunk_word_rc() {   # the verdict chunkcast wrote in $CHUNK_OUT ("pass <word>"), as the exit code that goes with it
    case "$(sed -n 's/^pass //p' "$CHUNK_OUT" 2>/dev/null | tail -n 1)" in
        ended|complete|applied) return 0 ;; no-stream) return 3 ;; stalled) return 4 ;; no-target) return 5 ;;
        stopped) return 6 ;; another-index) return 7 ;; bad-chunk) return 8 ;; *) return 2 ;;
    esac
}

# $1 = the code of a pass that ended in none of the ways chunkrecv.sh names itself -> the operator's sentence.
# 8 and 2 are two different things (a stream that hurt vs a file or a call that did not read) and are not one "rc=N";
# the number stays at the end, because it is what a technician searches the log for.
_chunk_rc_why() {
    case "$1" in
        8) echo "חתיכה בזרם לא תאמה את האינדקס (XXH64) ולא נכתבה, ואחריה הזרם אינו נסמך: הפגיעה בזרם (הרשת, השולח) או בקובץ האימג' בשרת -- לא בדיסק (rc=8)" ;;
        2) echo "המקלט לא התחיל או לא שמר את מצבו: האינדקס, מפת המחיצה או קובץ המצב לא נקראו או לא נכתבו, או שהארגומנטים שלו שגויים -- הסיבה בשורת chunkcast ביומן הסוכן (rc=2)" ;;
        *) echo "המקלט של מנוע החתיכות נכשל (rc=$1)" ;;
    esac
}
