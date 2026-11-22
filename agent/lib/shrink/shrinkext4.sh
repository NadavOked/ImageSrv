# ext4 geometry from the superblock. No journal replay on the source.
ext4_geometry() {
    _eg_head=$(LC_ALL=C dumpe2fs -h "$1" 2>> "$LOG_FILE") || return 1
    printf '%s\n' "$_eg_head" | awk -v field="$2" '
        /^Block count:/ && NF == 3 && $3 ~ /^[0-9]+$/ { blocks=$3; b++ }
        /^Free blocks:/ && NF == 3 && $3 ~ /^[0-9]+$/ { free=$3; f++ }
        /^Block size:/ && NF == 3 && $3 ~ /^[0-9]+$/ { size=$3; s++ }
        END {
            if (b != 1 || f != 1 || s != 1 || blocks <= 0 || size < 1024 || free+0 > blocks+0) exit 1
            if (field == "used") printf "%.0f\n", (blocks-free)*size
            else if (field == "block") print size
            else if (field == "bytes") printf "%.0f\n", blocks*size
            else exit 1
        }'
}

shrink_ext4() {
    # $1 is the verified snapshot, $2 the planned length in bytes.
    _ex_rc=0
    e2fsck -f -y "$1" >> "$LOG_FILE" 2>&1 || _ex_rc=$?
    case "$_ex_rc" in 0|1) ;; *) LC_ERROR="$1: e2fsck rc=$_ex_rc"; return 1 ;; esac
    resize2fs -M "$1" >> "$LOG_FILE" 2>&1 || { LC_ERROR="$1: resize2fs -M rc=$?"; return 1; }
    _ex_min=$(ext4_geometry "$1" bytes) && _ex_block=$(ext4_geometry "$1" block) || {
        LC_ERROR="$1: dumpe2fs לא מדד את מערכת הקבצים אחרי הכיווץ"; return 1; }
    _ex_blocks=$(($2 / _ex_block))
    _ex_bytes=$((_ex_blocks * _ex_block))
    [ "$_ex_min" -le "$_ex_bytes" ] || { LC_ERROR="$1: הגאומטריה של ext4 ($_ex_min) גדולה מ-$2"; return 1; }
    if [ "$_ex_min" -lt "$_ex_bytes" ]; then
        resize2fs "$1" "$_ex_blocks" >> "$LOG_FILE" 2>&1 || { LC_ERROR="$1: resize2fs rc=$?"; return 1; }
    fi
    _ex_actual=$(ext4_geometry "$1" bytes) && [ "$_ex_actual" = "$_ex_bytes" ] || {
        LC_ERROR="$1: הגאומטריה של ext4 נקראה בחזרה $_ex_actual, ולא $_ex_bytes"; return 1; }
}
