# expandshare.sh -- where the partitions go when a restore rewrites the table,
# before the first byte: the ones after the one that grows move to the tail
# (_move_to_tail), and every partition capture shrank goes back to its source
# size (#1897), also one before it (#1898). POSIX sh (busybox ash).
#
# Split from expand.sh and grow.sh (#1898) before the 300-line wall, without
# a change of behaviour: expand_last (expand.sh) calls everything here; the
# grow after the stream (grow_expanded, grow.sh) reads the expanded.more it
# writes. Loaded right after grow.sh; needs manifest_plan (restore.sh) and jq.

# --- #1897 (Nadav 04/11, replaces the 22/10 source ratio): a shrunk image with
# more than one data partition (C: + D: + E:). Capture shrinks them all; the
# restore gives EVERY shrunk partition back its source size (shrunk_from_bytes,
# #87/#929) -- D: and E: exactly as they were -- and the one expand_last
# stretches (C:) takes whatever is left: all the extra room on a larger disk;
# on a smaller one it is the only one that gives way, down to its own size in
# the image. Too small even for that, or for C:'s min-free, is refused before
# the first byte (volumes_fit, min_free_check: diskfit.sh). Until 04/11 the
# room was shared in the source ratio; until 22/10 D: arrived shrunk (~2GB
# free) and never grew. expand_last (expand.sh) calls share_tail and
# share_head before the stream; grow_expanded (grow.sh) grows what they
# marked after it. Twins of the plan: cand_room (RETURN_JQ, diskfit.sh) and
# server/library/minfree.py:cand_room.
#
# Who returns: every partition after the candidate that capture shrank, and
# (#1898, Nadav 22/10) one BEFORE the candidate (dual-boot: C: before the
# linux root) -- it grows in place and what follows it moves forward
# (share_head) -- ONLY when the operator approved the split on the cloner
# (SHARE_HEAD=1, targets/<disk>/share.ok, drawergate.sh). A path that does not
# ask (a station, the local clone) keeps the head as it was. A partition that
# was not shrunk (recovery, swap) keeps its size, as before.

#: from[index] = shrunk_from_bytes ("" = not shrunk); the candidate's plan line in c[].
SHARE_FROM_AWK='BEGIN { while ((getline l < src) > 0) { split(l, f, "|"); from[f[1]] = f[2] }
        split(cand, c, "|") }'

_share_src() {
    # $1 disk, $2 manifest. PRINTS the path of index|shrunk_from_bytes per
    # partition -- one file per drawer (PREP_TAG: the tables run at once, #1704).
    _ss="$RUN_DIR/share${PREP_TAG:-}.src"
    jq -r '.partitions[] | "\(.index)|\(.shrunk_from_bytes // "")"' "$2" > "$_ss" 2>> "$LOG_FILE" \
        || { log "$1: the source sizes of the partitions did not read (#1897)" >&2; return 1; }
    echo "$_ss"
}

share_tail() {
    # $1 disk, $2 manifest, $3 the candidate's plan line, $4 the tail plan
    # lines. PRINTS the tail with each shrunk partition's size (field 6) back
    # at its source size, exactly; the candidate takes the rest (`-n i:start:0`).
    # Writes targets/<disk>/expanded.more (idx|fs per partition that grows;
    # SHARE_MORE overrides the path). rc 1 = the sizes did not read.
    _st_src=$(_share_src "$1" "$2") || return 1
    printf '%s\n' "$4" | awk -F'|' -v OFS='|' -v cand="$3" \
        -v more="${SHARE_MORE:-$RUN_DIR/targets/$1/expanded.more}" -v src="$_st_src" "$SHARE_FROM_AWK"'
        $1 != "" { line[NR] = $0 }
        END { printf "" > more
              for (i = 1; i <= NR; i++) {
                  if (!(i in line)) continue
                  n = split(line[i], p, "|")
                  if (from[p[1]] + 0 > p[6] + 0) { p[6] = from[p[1]]; print p[1] "|" p[4] > more }
                  out = p[1]; for (j = 2; j <= n; j++) out = out OFS p[j]; print out
              } }'
}

share_head() {
    # $1 disk, $2 manifest, $3 the candidate's plan line (SHARE_HEAD=1 only).
    # #1898: a shrunk partition BEFORE the candidate grows in place back to its
    # source size; every partition after it up to the candidate moves forward
    # by what grew before it -- safe: the table is written before the first
    # byte, and the stream writes by index. PRINTS the candidate's new start,
    # then each head line that moves or grows, with its new start (field 5) and
    # size (field 6). Appends what grew to expanded.more, after share_tail wrote it.
    _sh_src=$(_share_src "$1" "$2") || return 1
    _sh_plan=$(manifest_plan "$2") || return 1
    printf '%s\n' "$_sh_plan" | awk -F'|' -v OFS='|' -v cand="$3" \
        -v more="${SHARE_MORE:-$RUN_DIR/targets/$1/expanded.more}" -v src="$_sh_src" "$SHARE_FROM_AWK"'
        $1 != "" && $5 + 0 < c[5] + 0 { n++; ln[n] = $0; st[n] = $5 + 0 }
        END { for (i = 1; i <= n; i++) for (j = i + 1; j <= n; j++)
                  if (st[j] < st[i]) { t = st[i]; st[i] = st[j]; st[j] = t; t = ln[i]; ln[i] = ln[j]; ln[j] = t }
              off = 0
              for (i = 1; i <= n; i++) {
                  k = split(ln[i], p, "|"); g = 0
                  if (from[p[1]] + 0 > p[6] + 0) g = int((from[p[1]] - p[6]) / 512)
                  if (off > 0 || g > 0) {
                      # #2016: one that moves starts on a MiB; what follows moves by that too
                      ns = p[5] + off; if (off > 0) { ns = int((ns + 2047) / 2048) * 2048; off = ns - p[5] }
                      p[5] = sprintf("%.0f", ns); p[6] = sprintf("%.0f", p[6] + g * 512)
                      o = p[1]; for (j = 2; j <= k; j++) o = o OFS p[j]; out[++m] = o
                      if (g > 0) print p[1] "|" p[4] >> more
                  }
                  off += g
              }
              printf "%.0f\n", c[5] + off
              for (i = 1; i <= m; i++) print out[i] }'
}

_head_args() {
    # $1 = share_head's lines (final start and size). The sgdisk options: all
    # deleted first, then recreated -- a forward move overlaps its neighbour.
    printf '%s\n' "$1" | awk -F'|' '$1 != "" { printf " -d %s", $1 }'
    printf '%s\n' "$1" | awk -F'|' '$1 != "" {
        printf " -n %s:%s:%.0f -t %s:%s", $1, $5, $5 + $6 / 512 - 1, $1, $2
        if ($10 != "") printf " -u %s:%s", $1, $10
        if ($12 != "") printf " -A %s:=:0x%s", $1, $12 }'
}

_move_to_tail() {
    # $1 = the plan lines that sit after the candidate, in any order (may be
    # empty); $2 = disk sectors. PRINTS the sgdisk options that move them --
    # expand_last runs them in its one call (#1714). Deleted first, then
    # recreated from the disk's last one inwards, so that their order on the
    # platter survives the move. Start aligned down to 2048 (SSD speed), size
    # EXACT (#1467): `-n i:-N:0` let sgdisk align the start and keep the end, so
    # the partition came out up to 2047 sectors larger than the source's.
    # The index is kept: it is what the stream writes by (and mkswap).
    printf '%s\n' "$1" | awk -F'|' '$1 != "" { printf " -d %s", $1 }'
    # הסדר נקבע כאן, לפי start_sector יורד — ולא ב-sort(1), שאינו בשימוש
    # בשום מקום אחר בסוכן. הרשימה שמגיעה לכאן היא בסדר אינדקסים, ובאימג'
    # ענן זה אינו הסדר על הדיסק; מיון שגוי היה מחליף שתי מחיצות בשקט.
    printf '%s\n' "$1" \
        | awk -F'|' '{ a[NR] = $0; k[NR] = $5 + 0 }
             END { for (i = 1; i <= NR; i++) for (j = i + 1; j <= NR; j++)
                       if (k[j] > k[i]) { t = k[i]; k[i] = k[j]; k[j] = t
                                          u = a[i]; a[i] = a[j]; a[j] = u }
                   for (i = 1; i <= NR; i++) print a[i] }' \
        | while IFS='|' read -r _si _sg _sr _sf _ss _sb _sfile _ssha _sexp _su _suuid _sa; do
        [ -n "$_si" ] || continue
        _sx=$(( (${_sx_end:-$(($2 - 34))} - _sb / 512 + 1) / 2048 * 2048 ))
        printf ' -n %s:%s:%s -t %s:%s' "$_si" "$_sx" "$((_sx + _sb / 512 - 1))" "$_si" "$_sg"
        # ה-GUID הייחודי שורד גם את ההזזה: ‏/etc/fstab עשוי להפנות ל-PARTUUID.
        [ -z "$_su" ] || printf ' -u %s:%s' "$_si" "$_su"
        [ -z "$_sa" ] || printf ' -A %s:=:0x%s' "$_si" "$_sa"
        _sx_end=$((_sx - 1))
    done
}

_cand_end() {
    # $1 = _move_to_tail's options, $2 = disk sectors. PRINTS the last sector the
    # candidate gets: one before the lowest start the tail moves to -- read from
    # what will be written, not computed again -- or the last usable one (no
    # tail). #1897: with the shrunk ones back at their source size it can be
    # smaller than the candidate's image; expand_last refuses that table.
    printf '%s\n' "$1" | awk -v e=$(($2 - 34)) '{ for (i = 1; i < NF; i++) if ($i == "-n") {
        split($(i + 1), a, ":"); if (a[2] - 1 < e) e = a[2] - 1 } } END { printf "%.0f\n", e }'
}
