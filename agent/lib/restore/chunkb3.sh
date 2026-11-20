# chunkb3.sh -- the partition's BLAKE3 list, beside its index (#3332). POSIX sh (busybox ash).
#
# XXH64 finds a damaged chunk and does not stop a made one: a 4MiB block with chosen
# content and a given XXH64 can be made in moments. So the server keeps, beside each
# partition's index, its BLAKE3 list -- 32 bytes a chunk, the BLAKE3 of the chunk's
# decompressed bytes -- and the manifest names the list's own BLAKE3 (`chunk_b3`).
# This file downloads the list and hands it to every chunkcast that WRITES -- recv,
# apply over HTTP, a neighbour's fill, a drawer back in, a neighbour cloner (peerfill)
# -- through CHUNK_IMAGE_ARGS: `--b3-file F --b3-file-hash H`. chunkcast believes the
# list only when its BLAKE3 is H, and writes a chunk only when its BLAKE3 is the list's.
#
# The list is required (Nadav 15/11): a manifest that names none, a name that is not a
# BLAKE3, a list that does not arrive or is not the index's length -- the drawers of the
# partition fail by name. Never XXH64 alone in its place (principle 5).

chunk_b3_fetch() {
    # $1=server $2=image_id $3=partition index $4=the manifest. 0 = CHUNK_IMAGE_ARGS carries the list;
    # 1 = it does not, and CHUNK_B3_WHY says why, in the words the drawer's failure carries.
    _cb3_f="$CHUNK_DIR/p$3.b3"; CHUNK_B3_WHY=""
    _cb3_h=$(jq -r --argjson i "$3" '.partitions[] | select(.index == $i) | .chunk_b3 // empty' "$4" 2>> "$LOG_FILE") \
        || { CHUNK_B3_WHY="המניפסט לא נקרא (רשימת ה-BLAKE3 של המחיצה)"; return 1; }
    [ -n "$_cb3_h" ] || { CHUNK_B3_WHY="למניפסט אין רשימת BLAKE3 למחיצה (chunk_b3) -- בלעדיה המחיצה לא נכתבת"; return 1; }
    case "$_cb3_h" in
        *[!0-9a-f]*) CHUNK_B3_WHY="רשימת ה-BLAKE3 במניפסט אינה 64 ספרות hex"; return 1 ;;
    esac
    [ "${#_cb3_h}" -eq 64 ] || { CHUNK_B3_WHY="רשימת ה-BLAKE3 במניפסט אינה 64 ספרות hex"; return 1; }
    _cb3_n=$(awk 'NR == 1 { print $9 }' "$CHUNK_IDX" 2>> "$LOG_FILE")
    case "$_cb3_n" in ''|*[!0-9]*) CHUNK_B3_WHY="האינדקס של המחיצה לא נקרא"; return 1 ;; esac
    if ! http_get "$1/api/v1/images/$2/b3/$3" > "$_cb3_f" 2>> "$LOG_FILE"; then
        CHUNK_B3_WHY="רשימת ה-BLAKE3 של המחיצה לא הורדה מהשרת"; return 1
    fi
    _cb3_size=$(wc -c < "$_cb3_f" 2>> "$LOG_FILE")
    [ "$_cb3_size" = "$((_cb3_n * 32))" ] || { CHUNK_B3_WHY="רשימת ה-BLAKE3 של המחיצה אינה באורך האינדקס ($_cb3_size בייט, $_cb3_n חתיכות)"; return 1; }
    CHUNK_IMAGE_ARGS="$CHUNK_IMAGE_ARGS --b3-file $_cb3_f --b3-file-hash $_cb3_h"
    log "partition $3: BLAKE3 list fetched ($_cb3_n chunks) -- every chunk is checked against it before it is written"
}
