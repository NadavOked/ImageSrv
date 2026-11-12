# diskinfo.sh -- disk collection, identity, probing and hello disk entries
# POSIX sh (busybox ash). SYSROOT/DEVROOT are initialized by sysinfo.sh.

logical_block_size() {
    # $1 = block device path. גודל הסקטור הלוגי כפי שהקרנל מדווח אותו,
    # או **כלום** כשאי אפשר לקרוא אותו. הקוראים מחזיקים נתיב
    # (‏`/dev/sda`) ו-sysfs מפתחו לפי שם (`sda`) — הגישור נעשה כאן.
    #
    # ‏`/sys/block` מכיל כוננים שלמים בלבד: נתיב של **מחיצה**
    # (‏`/dev/sda1`) לא יימצא, ויחזור ריק. זה נכון — סכימה היא תכונה
    # של כונן, ושני הקוראים (`build_disk_entry`, ‏`capture_disk`)
    # מעבירים לכאן שם מ-`list_disks` בלבד.
    _bs=$(cat "$SYSROOT/sys/block/${1##*/}/queue/logical_block_size" 2>/dev/null)
    case "$_bs" in
        ''|*[!0-9]*) return ;;              # חסר, ריק, או לא מספר
    esac
    # ‏512 הוא המינימום שהקרנל מדווח; החלוקה ב-8 היא ה-`bs` של ה-`dd`
    # שקורא את החתימה. ערך שאינו עומד בשניים אינו גודל סקטור — ולא
    # מתקנים אותו בשקט.
    [ "$_bs" -ge 512 ] && [ $((_bs % 8)) -eq 0 ] || return
    printf '%s' "$_bs"
}

disk_scheme() {
    # $1 = block device path. "EFI PART" at LBA1 -> gpt; 55aa at 510 -> mbr.
    #
    # ‏LBA1 הוא **הסקטור הלוגי השני**, לא "בייט 512". על כונן 4Kn
    # (‏`logical_block_size=4096` — נפוץ ב-NVMe ארגוני ובדיסקים גדולים)
    # כותרת ה-GPT יושבת בבייט 4096, וקריאה מקובעת ב-512 נוחתת בתוך
    # ה-MBR המגונן. היא לא מוצאת "EFI PART", והשורה הבאה מוצאת `55aa`
    # — שיש **לכל** דיסק GPT — ומכריזה על כונן GPT תקין כ-`mbr` (#126).
    # לכן ה-offset נגזר מ-sysfs ולא מונח.
    #
    # ה-`55aa` דווקא כן נשאר בבייט 510 המוחלט, בכל גודל סקטור: ה-MBR
    # הוא מבנה קשיח של 512 בייט בראש הכונן, לא "הסקטור הראשון".
    _lbs=$(logical_block_size "$1")
    # בלי גודל סקטור אין לדעת איפה LBA1. ‏512 כברירת מחדל היא בדיוק
    # ההנחה שיצרה את הבאג, ו-`none` הוא טענה חיובית ("אין טבלה")
    # שתסמן כונן מלא נתונים כריק — `has_data:false`. עיקרון 5: "לא
    # הצלחנו לבדוק" הוא מצב משלו.
    [ -n "$_lbs" ] || { echo "unknown"; return; }
    _sig=$(dd if="$1" bs=8 skip=$((_lbs / 8)) count=1 2>/dev/null) || { echo "unknown"; return; }   # #1904: a failed read is not "no signature"
    if [ "$_sig" = "EFI PART" ]; then echo "gpt"; return; fi
    dd if="$1" bs=2 skip=255 count=1 of=/dev/null 2>/dev/null || { echo "unknown"; return; }   # #1904: dd's own rc -- in the pipe below it is tr's
    _mbr=$(dd if="$1" bs=2 skip=255 count=1 2>/dev/null | od -An -tx1 | tr -d ' \n')
    [ "$_mbr" = "55aa" ] && echo "mbr" || echo "none"
}

disk_serial() {
    # $1 = disk name. sysfs has it for NVMe; SATA needs hdparm.
    _s=$(trim "$(cat "$SYSROOT/sys/block/$1/device/serial" 2>/dev/null)")
    if [ -z "$_s" ]; then
        # ‏wwid לפני hdparm: קיים כמעט לכל דיסק — וירטואלי (נגזר ממזהה
        # ה-VHD, מתחלף עם הקובץ כמו serial פיזי, #16) וגם SATA אמיתי
        # (t10.ATA + הדגם וה-serial). ‏hdparm על דיסק וירטואלי מייצר
        # שגיאת קרנל לכל קריאה — וה-hello הקבוע הציף את מסך הבנייה
        # בשורת hv_storvsc כל שתי שניות וקבר את הממשק (#30).
        _s=$(trim "$(cat "$SYSROOT/sys/block/$1/device/wwid" 2>/dev/null)")
    fi
    if [ -z "$_s" ] && command -v hdparm >/dev/null 2>&1 && [ -z "$SYSROOT" ]; then
        _s=$(hdparm -I "$DEVROOT/$1" 2>/dev/null |
             awk -F: '/Serial Number/ { gsub(/^[ \t]+|[ \t]+$/, "", $2); print $2; exit }')
    fi
    printf '%s' "$_s"
}

port_from_path() {
    # $1 = the sysfs path the disk's `device` link resolves to. Prints the
    # physical SATA slot as the kernel numbers it, or nothing at all.
    #
    # ‏‎/sys/block/sdX/device עובר דרך ataN — והוא, ולא סדר הגילוי, החריץ
    # שאליו הכונן מחובר: ata1 הוא הראשון בבקר, במחשבי השיכפול המגירה
    # העליונה (‏#27). ‏NVMe, דיסק וירטואלי או בקר לא-ATA לא מדווחים חריץ,
    # והקונסולה נופלת חזרה לשם ההתקן — עדיף בלי מספר מאשר מספר שגוי.
    case "$1" in
        */ata[0-9]*)
            _p=${1##*/ata}
            _p=${_p%%/*}
            case "$_p" in
                ''|*[!0-9]*) ;;
                *) printf '%s' "$_p"; return ;;
            esac
            ;;
    esac
    # נפילה אחורה ל-hostN: ה-scsi host הראשון של בקר ATA הוא ata1, ולכן
    # hostN הוא חריץ N+1. רק כשהבקר באמת ATA — על VM עם SCSI
    # (‏storvsc/mptspi/virtio) המספור אינו חריצי מגירות.
    case "$1" in
        */host[0-9]*)
            _h=${1##*/host}
            _h=${_h%%/*}
            case "$_h" in ''|*[!0-9]*) return ;; esac
            case "$(cat "$SYSROOT/sys/class/scsi_host/host$_h/proc_name" 2>/dev/null)" in
                ahci|ata_*|sata_*|pata_*) printf '%s' "$((_h + 1))" ;;
            esac
            ;;
    esac
}

disk_port() {
    # $1 = disk name. Empty when the slot cannot be derived -- never a failure.
    port_from_path "$(readlink -f "$SYSROOT/sys/block/$1/device" 2>/dev/null)"
}

build_disk_entry() {
    # $1 = disk name (sda, nvme0n1). Emits one JSON object.
    _name="$1"
    _sectors=$(cat "$SYSROOT/sys/block/$_name/size" 2>/dev/null || echo 0)
    _size=$((_sectors * 512))
    _model=$(trim "$(cat "$SYSROOT/sys/block/$_name/device/model" 2>/dev/null)")
    _serial=$(disk_serial "$_name")
    if disk_internal "$_name"; then _removable=false; else _removable=true; fi   # #1807/#1832: every reader of the field means "internal"
    _scheme=$(disk_scheme "$DEVROOT/$_name")
    _content=""   # #1924: a target says nothing of what is on it; only the build capture screen draws it (via /agent/state)
    [ "${D_ROLE:-}" != build ] || { _has=false; [ "$_scheme" = none ] || _has=true; _content=",\"scheme\":\"$_scheme\",\"has_data\":$_has"; }

    _serial_json="null"
    [ -n "$_serial" ] && _serial_json="\"$(json_escape "$_serial")\""

    _port=$(disk_port "$_name")
    _port_json="null"
    [ -n "$_port" ] && _port_json="$_port"

    # בריאות SMART (#652), **ממטמון בלבד** — smart.sh מריץ probe בצד
    # השחזור ובהמתנת מחשב השיכפול, לא כאן: אחרת כל אתחול של תחנת כיתה
    # שעולה לדיסק מקומי היה משלם שנייה על smartctl מיותר. בלי smart.sh
    # (או בלי ריצת probe) הערך `unchecked` — לא-נבדק, לא נכשל (עיקרון 5).
    _smart="unchecked"; _smart_r=""
    if command -v smart_hello_field >/dev/null 2>&1; then   # #1366: the reason of a fail (F1), else ""
        _smart=$(smart_hello_field "$_name"); case "$_smart" in fail|warn) _smart_r=$(smart_field "$_name" 2) ;; esac   # #1859: and why orange
    fi

    printf '{"dev":"%s","size_bytes":%s,"model":"%s","serial":%s,"removable":%s%s,"port":%s,"smart":"%s","smart_reason":"%s"%s}' \
        "$_name" "$_size" "$(json_escape "$_model")" "$_serial_json" \
        "$_removable" "$_content" "$_port_json" "$_smart" "$(json_escape "$_smart_r")" "$(command -v disk_link_json >/dev/null 2>&1 && disk_link_json "$_name")$(command -v disk_pci_json >/dev/null 2>&1 && disk_pci_json "$_name")$(command -v smart_attrs_hello >/dev/null 2>&1 && smart_attrs_hello "$_name")$(command -v writemark_json >/dev/null 2>&1 && writemark_json "$_name" "$_scheme")$(command -v raid_member_json >/dev/null 2>&1 && raid_member_json "$_name")$(command -v hw_sata_json >/dev/null 2>&1 && hw_sata_json "$_name")"   # #432+#1485; #1859: every SMART attribute; #1798 write_mark; #1808 raid_member; #2848 sata_issues
}

list_disks() {
    # Real disks only, in /dev order. Loop devices, CD, RAM -- out.
    for _d in "$SYSROOT"/sys/block/*; do
        [ -e "$_d" ] || continue
        _n=$(basename "$_d")
        case "$_n" in
            loop*|ram*|sr*|zram*|dm-*|md*|fd*) continue ;;
        esac
        echo "$_n"
    done
}

disk_internal() {
    # $1 = disk. #1807/#1832: `removable` is a MEDIA bit -- a USB-SATA enclosure or an sdhci
    # card says 0. Internal = 0 AND a device path that reads, on no USB/MMC bus (principle 5).
    [ "$(cat "$SYSROOT/sys/block/$1/removable" 2>/dev/null)" = 0 ] && [ -e "$SYSROOT/sys/block/$1/device" ] || return 1
    _dip=$(readlink -f "$SYSROOT/sys/block/$1/device" 2>/dev/null) && [ -n "$_dip" ] || return 1
    case "$_dip/" in */usb[0-9]*|*/mmc_host/*|*/memstick*) return 1 ;; esac
}

sata_ports_implemented() {
    # סוכם את מספר הפורטים ש**מופעלים** לפי כל בקר ahci שהקרנל רשם
    # ‏(‏"N/M ports implemented"). מדפיס ריק — לא `0` — כשאין שורה כזו
    # כלל: "לא הצלחנו לספור" אינו "אפס פורטים" (עיקרון 5). ה-N (מספר
    # המופעלים) הוא מה שנדרש; ה-M (הקיבולת) נספר ולא משמש.
    dmesg 2>/dev/null | awk '
        match($0, /[0-9]+\/[0-9]+ ports implemented/) {
            split(substr($0, RSTART, RLENGTH), _a, "/"); _sum += _a[1]; _seen = 1
        }
        END { if (_seen) print _sum }
    '
}

# ‏#2334: בקר PCI במחלקה 01 בלי `driver`. sysfs בלבד; עץ שלא נקרא = 1 (עיקרון 5).
storage_unbound() {
    for _d in "$SYSROOT"/sys/bus/pci/devices/*; do
        [ -r "$_d/class" ] || continue
        read -r _c < "$_d/class"
        case "$_c" in 0x01*) [ -e "$_d/driver" ] || return 0 ;; esac
    done
    return 1
}

disk_probe() {
    # ‏#402: `מגירות=0` אסור שיאמר שני דברים הפוכים. מכונה בלי דיסקים
    # היא או "חברו כונן" או "אף פורט SATA אינו מופעל בקושחה — כבל לא
    # יעזור", וההבחנה קיימת בקרנל (‏dmesg) — היא פשוט נזרקה. ההבחנה
    # מחושבת רק כשאין דיסק: מכונה עם כונן היא הנתיב החם, ואינה משלמת
    # קריאת dmesg על כל hello.
    [ -n "$(list_disks)" ] && { echo drives; return; }
    storage_unbound && { echo no_driver; return; }   # גובר על no_ports: בקר שלא רואים דרכו
    _impl=$(sata_ports_implemented)
    case "$_impl" in
        '') echo unchecked ;;   # אין שורת ahci — לא נספר
        0)  echo no_ports ;;    # בקר קיים, אפס פורטים מופעלים (קושחה)
        *)  echo no_disks ;;    # יש פורטים, אין כונן מחובר
    esac
}

