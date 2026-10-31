# drvgui.sh -- the drivers outcome of one drawer, for the cloner's screen. POSIX sh.
# #1331 (Nadav's mockup v2, 17/10): every drawer row of "שיכפול במקביל" says
# under its bar what happened to its drivers -- placed, not placed, no model,
# and whether the storage driver was made boot-start (bootstorage.sh). The
# cloner wrote that outcome itself, to targets/<dev>/drivers.json
# (stage_drawer_drivers, postdeploy.sh); the screen gets it as fields 13-17
# of the drawer= record:
#   |state|detail|boot-state|boot-service|boot-error
# and, only for a Linux target (drivers.linux, #1331 T8), fields 18-20:
#   |linux-state|linux-module|linux-error-or-reason
# No linux object = the record stays seventeen fields.
# detail = the packages ("a, b") when staged, the error when failed, the
# reason when skipped. The caller asks only when the file exists: no file =
# the step did not run, and the record stays twelve fields, as before -- never
# "staged". A file jq cannot read fails the call (rc 1): the snapshot is not
# published, as for every other record (principle 5).

drivers_gui_fields() {
    # $1 = drivers.json
    jq -r 'def clean: tostring | gsub("[\\r\\n|]"; " ");
        [(.state // ""),
         (if .state == "staged" then (.packages // [] | join(", "))
          elif .state == "failed" then (.error // "") else (.reason // "") end),
         (.boot.state // ""), (.boot.service // ""), (.boot.error // .boot.reason // "")]
        + (if (.linux | type) == "object" then [(.linux.state // ""), (.linux.module // ""), (.linux.error // .linux.reason // "")] else [] end)
        | map(clean) | "|" + join("|")' "$1"
}
