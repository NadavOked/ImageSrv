# unattend.sh -- the computer name on a sysprepped Windows (#1976).
# POSIX sh (busybox ash). Called by _write_hostname_windows (hostname.sh)
# while the Windows partition is still mounted.
#
# A generalized image (sysprep /generalize) runs the specialize pass on its
# first boot, and specialize sets the computer name from the answer file:
# ComputerName "*", or no ComputerName at all, is a random DESKTOP-XXXXXXX --
# over the name hostname.sh wrote into the SYSTEM hive. So on such an image
# the name goes into the answer file too. An image that was not sysprepped
# never runs specialize, and nothing here touches it.
#
# The evidence is the image state Windows keeps in the SOFTWARE hive
# (Setup\State\ImageState); "could not read it" is a failure in its own name,
# never "not sysprepped" (principle 5). "Written" is the name read back from
# the file. The edit is minimal: an existing ComputerName gets the name, a
# missing one is added to the specialize pass, and a file is created only
# when there is none at all.

UNATTEND_STATE_KEY='Microsoft\Windows\CurrentVersion\Setup\State'
UNATTEND_ERROR=""

_unattend_file() {
    # $1 = mount point. The answer file Windows Setup reads after sysprep:
    # the first of its implicit search order that exists (Panther\Unattend,
    # Panther, System32\Sysprep). System32\Sysprep\Panther holds sysprep's
    # logs, not an answer file. ntfs-3g is case-sensitive; Windows is not.
    for _ud in Windows/Panther/Unattend Windows/Panther Windows/System32/Sysprep; do
        [ -d "$1/$_ud" ] || continue
        for _un in unattend.xml autounattend.xml; do
            _uf=$(find "$1/$_ud" -maxdepth 1 -type f -iname "$_un" 2>> "$LOG_FILE" | head -n 1)
            [ -n "$_uf" ] && { echo "$_uf"; return 0; }
        done
    done
    return 0
}

_unattend_component() {
    # $1 = name, $2 = processor architecture. One Shell-Setup component.
    printf '    <component name="Microsoft-Windows-Shell-Setup" processorArchitecture="%s" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS">\n      <ComputerName>%s</ComputerName>\n    </component>\n' "$2" "$1"
}

_unattend_arch() {
    # $1 = SYSTEM hive, $2 = control set. amd64 / x86 / arm64, as the answer
    # file spells it; nothing when the hive does not say.
    hivewrite -g "$1" "$2\\Control\\Session Manager\\Environment" PROCESSOR_ARCHITECTURE \
        2>> "$LOG_FILE" | tr 'A-Z' 'a-z' | tr -dc 'a-z0-9'
}

_unattend_insert() {
    # $1 = file, $2 = name, $3 = component text. Adds ComputerName where the
    # specialize pass expects it: inside its Shell-Setup component, else as a
    # new component in the pass, else as a new specialize pass.
    awk -v n="$2" -v comp="$3" '
        /<settings[^>]*pass="specialize"/ { sp = 1 }
        sp && !done && /<\/settings>/ { printf "%s", comp; done = 1 }
        sp && !done && /<component[^>]*"Microsoft-Windows-Shell-Setup"/ { shell = 1 }
        !done && /<\/unattend>/ { printf "  <settings pass=\"specialize\">\n%s  </settings>\n", comp; done = 1 }
        { print }
        shell && !done && />[ \t\r]*$/ { print "      <ComputerName>" n "</ComputerName>"; done = 1 }
        /<\/settings>/ { sp = 0 }
        END { exit done ? 0 : 1 }' "$1" > "$1.imagesrv" && mv "$1.imagesrv" "$1" \
        || { rm -f "$1.imagesrv"; return 1; }
}

sysprep_name() {
    # $1 = mount point, $2 = name, $3 = control set. 0 = not generalized
    # (nothing done) or the name is in the answer file and read back; 1 = the
    # reason is in $UNATTEND_ERROR.
    UNATTEND_ERROR=""
    _ust=$(hivewrite -g "$1/Windows/System32/config/SOFTWARE" "$UNATTEND_STATE_KEY" ImageState 2>> "$LOG_FILE")
    _urc=$?
    if [ "$_urc" -ne 0 ] || [ -z "$_ust" ]; then
        UNATTEND_ERROR="לא הצלחנו לקרוא את מצב ה-sysprep של האימג' (ImageState, rc=$_urc)"
        return 1
    fi
    case "$_ust" in
        IMAGE_STATE_GENERALIZE_RESEAL_TO_*) ;;
        *) log "image state $_ust: specialize will not run -- the answer file is not touched" >&2; return 0 ;;
    esac
    _uf=$(_unattend_file "$1")
    if [ -z "$_uf" ]; then
        _uarch=$(_unattend_arch "$1/Windows/System32/config/SYSTEM" "$3")
        [ -n "$_uarch" ] || { UNATTEND_ERROR="לא הצלחנו לקרוא את ארכיטקטורת המעבד של Windows"; return 1; }
        _uf="$1/Windows/Panther/unattend.xml"
        mkdir -p "$1/Windows/Panther" \
            && printf '<?xml version="1.0" encoding="utf-8"?>\n<unattend xmlns="urn:schemas-microsoft-com:unattend">\n  <settings pass="specialize">\n%s\n  </settings>\n</unattend>\n' \
                "$(_unattend_component "$2" "$_uarch")" > "$_uf" \
            || { UNATTEND_ERROR="לא הצלחנו ליצור את Panther/unattend.xml"; return 1; }
        log "$_ust: no answer file -- created $_uf with ComputerName $2" >&2
    else
        case "$(od -An -tx1 -N2 "$_uf" 2>> "$LOG_FILE" | tr -d ' ')" in
            fffe|feff) UNATTEND_ERROR="קובץ התשובות $(basename "$_uf") שמור ב-UTF-16 — לא נערך"; return 1 ;;
        esac
        if grep -q '<ComputerName' "$_uf"; then
            sed -i -e "s|<ComputerName>[^<]*</ComputerName>|<ComputerName>$2</ComputerName>|g" \
                   -e "s|<ComputerName[ ]*/>|<ComputerName>$2</ComputerName>|g" "$_uf" \
                || { UNATTEND_ERROR="עריכת $(basename "$_uf") נכשלה"; return 1; }
        else
            _uarch=$(_unattend_arch "$1/Windows/System32/config/SYSTEM" "$3")
            [ -n "$_uarch" ] || { UNATTEND_ERROR="לא הצלחנו לקרוא את ארכיטקטורת המעבד של Windows"; return 1; }
            _unattend_insert "$_uf" "$2" "$(_unattend_component "$2" "$_uarch")
" || { UNATTEND_ERROR="לא מצאנו היכן להוסיף ComputerName ב-$(basename "$_uf")"; return 1; }
        fi
        log "$_ust: ComputerName $2 written into $_uf" >&2
    fi
    # The read-back: the name is there, and no other ComputerName value is.
    _ugood=$(grep -c "<ComputerName>$2</ComputerName>" "$_uf")
    _uall=$(grep -o '<ComputerName[^>]*>[^<]*' "$_uf" | grep -vc ">$2\$")
    if [ "${_ugood:-0}" -lt 1 ] || [ "${_uall:-1}" -ne 0 ]; then
        UNATTEND_ERROR="השם לא נקרא בחזרה מ-$(basename "$_uf")"
        return 1
    fi
    return 0
}
