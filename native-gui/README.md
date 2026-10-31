# native-gui — the station screens without a browser

The build machine's operator screens were authored in HTML
(`server/static/station/`, removed in #1247 once this GUI replaced it). Displaying them costs a browser in a RAM-resident
initramfs: 120 MB (WPE WebKit) to 315 MB (Chromium) for 57 KB of HTML
(`docs/research/R03-network-gui.md`, `R09-hebrew-ui-without-a-browser.md`).
R09's finding is that the browser was chosen for **Hebrew** — bidi and
shaping — and those come from two small libraries, not from a browser.

This directory is **every card of `index.html`**, redrawn natively, with the
shared header, light and dark themes, RTL. It is additive: `server/`,
`agent/` and the HTML are untouched. The login and menu compile on Debian 13
and the Hebrew smoke test passes there; **the six cards added after them have
not been compiled yet** — see "What I could not verify".

| card (`index.html` id) | native screen | login | what it shows |
|---|---|---|---|
| `#st-login` | `SCREEN_LOGIN` | — | כניסה: user, password + eye, error, כניסה |
| `#st-menu` | `SCREEN_MENU` | gated | מה עושים? four cards, #1244 order: capture (admin-only, first) · room · direct · restore (#382/#706); keys 1–4 follow the order on screen; the class card only with `menu_class=1` (v2), last |
| `#st-pick` | `SCREEN_PICK` | gated | קליטת אימג' חדש: the internal disks, then name / description / folder + "+ חדשה", התחל קליטה, חזרה, the hint |
| `#st-progress` | `SCREEN_PROGRESS` | **open** | קולט: … / big bar / percent + bytes / "אל תכבו את המחשב…". Also the cloner's and the class receiver's display |
| `#st-done` | `SCREEN_DONE` | open | הקליטה הושלמה / נכשלה + קליטה נוספת |
| `#st-room` | `SCREEN_ROOM` | gated | הפצה למחשבי שיכפול: `room.js` setup (image, target, who is awake, machine rows) and the live round (count, bar, hint, rows, stop-confirm) |
| `#st-class` | `SCREEN_CLASS` | gated, **open when a round is live** | הפצה לכיתות: `classes.js` step 1 (the classes) and the live round (joined, hint, rows, stop-confirm) |
| `#st-message` | `SCREEN_MESSAGE` | **open** | title + text: "המחשב אינו רשום", "חסר מזהה מכונה", … |

"Gated" = the login card is shown first. "Open" = reachable with no operator:
the message and the running task come from the `--state` file and take the
screen regardless of login (the `poll()` order of `station.js`), and a live
classroom round keeps `#st-class` visible without a session (#34) — its
actions then toast "החיבור פג — נדרשת כניסה מחדש." and do nothing, exactly as
`classes.js authed()` does on a 401.

## Toolkit: Pango + HarfBuzz + FriBidi + Cairo, straight onto KMS

**Chosen:** R09's primary candidate — Pango for text layout, drawing with
Cairo into a DRM/KMS dumb buffer (fbdev as fallback), plain C, no display
server. **Why, in one paragraph:** the only requirement that has already
eliminated toolkits is correct mixed Hebrew/Latin/digit text, and Pango is the
one small stack whose Hebrew is not a hope but a published dependency — its
README lists *"HarfBuzz for complex text shaping"* and *"fribidi for
bidirectional text handling"*, and Debian's `libpango-1.0-0` package literally
depends on `libfribidi0` ("Free Implementation of the Unicode BiDi algorithm")
and `libharfbuzz0b`. It is the text engine of GTK, i.e. of every Hebrew GNOME
desktop; it is not going to render `/dev/sda` inside a Hebrew sentence
differently from Chromium, which links the same two libraries. Size: the
`.deb`s for pango+pangocairo+harfbuzz+fribidi+cairo+pixman+freetype+
fontconfig+glib+libdrm sum to **≈ 3.9 MB compressed** (per-package figures in
`deps-debian.txt`, from packages.debian.org/trixie, 2026-09-09) before
transitive dependencies — measured properly by `tools/measure-size.sh`, not
estimated. And it is C, the language the project already builds
(`agent/fanout.c`), with gcc already in the initramfs build.

**Not chosen — Slint** (~3 MB single binary, bundles its own text stack). Its
Hebrew is unproven and the evidence points the wrong way: the `Text` element
documentation does not mention bidirectional text, RTL, or shaping at all
(docs.slint.dev, reference/elements/text, read 2026-09-09); the issue tracker
has **#7267 "Word order gets messed up and cursor location problem" — open**,
and #1317 asks for a text-direction property that LineEdit/TextEdit still lack.
R09 §2.1 says "supports UTF-8" is not a claim we accept; Slint today is at that
level for Hebrew. It also needs a Rust or C++ toolchain the project does not
have.

## How bidi and shaping are obtained (the R09 question)

| step | who does it | where it is visible |
|---|---|---|
| Unicode bidirectional algorithm (UAX#9): embedding levels, run order | **FriBidi** (`libfribidi0`) | Pango calls `fribidi_get_par_embedding_levels_ex()` in `pango/pango-bidi-type.c` (`pango_log2vis_fill_embedding_levels`, read from gitlab.gnome.org 2026-09-09). The smoke test also calls `fribidi_log2vis()` directly and prints the levels, so the claim is checkable without Pango. |
| Paragraph direction, itemisation into runs | **Pango** | `pango_context_set_base_dir(RTL)` + `pango_layout_set_auto_dir(TRUE)` = `<html dir="rtl">` with per-paragraph first-strong resolution, the browser's behaviour. `src/text.c`. |
| Shaping (Hebrew glyphs, marks, kerning) | **HarfBuzz** (`libharfbuzz0b`) | Pango ≥ 1.44 shapes exclusively through HarfBuzz; the smoke test prints the face each run was shaped with and fails on any `PANGO_GLYPH_UNKNOWN_FLAG` glyph (tofu). |
| Rasterisation | **Cairo + FreeType** | `pango_cairo_show_layout()` into an image surface, copied to the scanout. |

Nothing in `native-gui/` reorders characters or picks glyphs itself. Text
with mixed weight (the stop-confirm label's `<b>image name</b>`) and the
coloured status spans of the machine rows go through Pango **markup**, which
is the same layout path; values are escaped with `g_markup_escape_text`.

Two deliberate details of RTL that the browser handles and that were
reproduced on purpose: (1) `PANGO_ALIGN_LEFT` under auto-dir means *start*
(Pango swaps it for an RTL line — `text.c` says so) — that is CSS
`text-align:start`; (2) an `<input dir=rtl>` keeps its text at the physical
right edge even when the value is Latin, so fields use auto-dir **off** and
`PANGO_ALIGN_RIGHT` (`text_make_field`).

## The smoke test — run this first

```sh
cd native-gui
tools/run-smoke.sh            # builds and runs; exit 0 only when every check passes
```

`smoke/hebrew_bidi.c` renders three lines to `hebrew-bidi.png` **and asserts
the visual order from the layout itself** (principle 5: positive evidence,
not "no error"): `שלום Office 2024 — כיתה 12`, `הכונן /dev/sda במחשב
3C:52:82:A1:00:1F מוכן`, and — new with the round screens — the `#st-room`
subtitle `משדר: Office 2024 — סטנדרט · גל 2`: `משדר` at the far right, then
`Office 2024` as one LTR run, `סטנדרט`, `גל`, and the wave number at the far
left. It prints the FriBidi embedding levels and the
pango/harfbuzz/fribidi/cairo versions in use.

**If the smoke test fails, stop: that decides the toolkit.**

## What is here

```
native-gui/
  README.md            this file
  Makefile             make | make smoke | make png [STATE=file]
  deps-debian.txt      packages, versions, .deb sizes (cited)
  fonts.conf           minimal fontconfig for the initramfs (FONTCONFIG_FILE=)
  smoke/hebrew_bidi.c  the Hebrew bidi/shaping smoke test
  src/theme.{h,c}      colour tokens from console.css, + the 5 hard-coded CSS colours
  src/draw.{h,c}       rounded boxes, borders, the 160deg+radial background, box-shadow
  src/text.{h,c}       Pango: RTL layout, markup, fields, baseline, font presence check
  src/widgets.{h,c}    .btn / input / select+list / .sheet frame / .tray / .big-bar / .cls-bars / #toast,
                       fmtBytes and Progress.view ported
  src/ui.h             App state, the State the agent feeds, hit table, Screen/Mode enums
  src/screens.c        header, #st-login, #st-menu, the poll() routing rule, draw entry
  src/screens_capture.c  #st-pick, #st-progress, #st-done, #st-message
  src/screens_rounds.c   #st-room, #st-class (room.js / classes.js bodies)
  src/screens_tools.c    #649: the IT toolbox -- list / confirm / output views, and the tools + tool-result file pollers
  src/state.{h,c}      the --state file: key=value parser, mtime polling
  src/backend.{h,c}    DRM dumb buffer / fbdev / mem (a framebuffer file, #835), VT to graphics mode and back
  src/input.{h,c}      evdev keyboard (US map), mouse, touch; keyboards grabbed
  src/main.c           event loop, --auth-cmd, stdout records, --png renderer
  src/dcui.{h,c}       #1203: --screen dcui, the server's own screen -- frames in, keys out;
                       the state machine stays in server/dcui (Python, root)
  src/screens_dcui.c   #1203: its drawing, from the approved mockup (design-approved/dcui)
  tools/run-smoke.sh   build + run the smoke test
  tools/measure-size.sh  the initramfs cost, measured the way build_initramfs.sh packs
```

## The server screen (#1203)

`--screen dcui` is a different program in the same binary: it paints the
frames `server/dcui/gui.py` writes to its stdin and writes one token per key
to its stdout (`dcui.h`; the records are docs/interfaces.md s31). It opens
`drm`, then `fbdev`, never `mem`, and every character it sends is the US
layout -- the fields there are English only. `--png PREFIX --state FRAME`
renders one frame file in both themes; `python3 -m server.dcui --gui-frames DIR`
writes the mockup's states as frame files (`server/dcui/samples.py`).

## Contract with the agent (POSIX sh)

```
imagesrv-station-gui --mac AA:.. --ip 10.. --auth-cmd 'imagesrv-login' --state /run/imagesrv/station.txt
```

* `--auth-cmd CMD` — on כניסה, CMD receives `<user>\n<pass>\n` on stdin; exit 0
  = signed in, first stdout line = role (`admin` shows the capture card,
  anything else hides it, as `station.js afterLogin` does), second line = the
  user's saved theme (`light`/`dark`; `auto` or none = the default; #1499). A curl to
  `/api/console/login` piped through `jq -r .role` fits here; it is not written
  because `agent/` is out of scope.
* `--state FILE` — the data `station.js` polls for, as `key=value` lines
  (below). Re-read every 2 s when its mtime or size changed; write it with
  `printf > tmp && mv tmp FILE`. Without it the screens have no disks,
  machines or task — they still render.
* `--screen progress` — start on `#st-progress` with no login (a cloning
  machine, a class receiver); the texts come from `title=` / `sub=` in the
  state file, else the HTML's defaults ("קולט…"). `--screen class` — start in
  the classes mode: with a live `session=` the round is shown read-only
  without login; without one, the login card (as `classes.js` does).
* `--signed-in USER [--role admin|deploy]` — start signed in at the menu, for
  an agent that restarts the GUI mid-flow.
* `--demo` accepts any non-empty user name (viewing only). **Without
  `--auth-cmd` every login fails** (closed by default). **Without `--mac`
  the message card "חסר מזהה מכונה" is shown**, as `index.html` does without
  `?mac=` — pass `--mac` (the `make png` and the recipe below do).
* exit 0 after `restore` (the agent takes the disk); exit 1: could not open a
  display, the fonts are missing, no input device, or stopped by a signal —
  with the reason on stderr.

### stdout: one record per operator action

The process **keeps running** across screens — the agent reads records from
its stdout (`| while read -r line`) and answers through the state file. A
record is a token line, zero or more `key=value` lines, and an **empty line**;
stdout is line-buffered so a record arrives as it happens.

| token | from | detail lines | meaning |
|---|---|---|---|
| `capture` | menu | — | the capture card was chosen; `#st-pick` is now showing (feed `disk=`/`folder=`) |
| `restore` | menu | — | the #382/#706 card: `#st-restore` is showing (feed `image=` from `allowed_images`, `machine_name=`) |
| `room` | menu | — | `#st-room` is showing (feed `machine=`/`image=`, then `round=`) |
| `direct` | menu | — | #715: the room screen **without** the image picker — the source is this machine's disk (feed `machine=`/`machine_drawers=`/`room_drawer=`, no `image=`) |
| `classes` | menu | — | `#st-class` is showing (feed `class=`, or `session=` for a live round) |
| `back` | חזרה on pick / room / class | — | back at the menu; stop feeding that screen |
| `again` | קליטה נוספת | — | back at the menu after `#st-done` |
| `capture-start` | התחל קליטה | `dev=`, `name=`, `desc=`, `folder=`, `folder_new=yes\|no` | POST `/api/console/tasks/capture` (`resolveFolder`: with `folder_new=yes` create the folder first). `folder=` is never empty (#1251: no "ללא תיקייה" -- the screen refuses before emitting, and the server answers 400 anyway). The screen stays on `#st-pick` until the file says `task=pending`/`running`; a failure goes back as `form_error=` |
| `room-open` | פתח סבב והער את המחשבים שנבחרו (17/10: the server wakes only the cloners in `target_slots`) | `image=<id>`, `target=<n>`; with "להוסיף דרייברים" on also `slots=` (as `direct-open`) and `drivers=` (#1269) | POST `/api/console/room`; answer with `round=` or `room_error=`. #1269: with drivers the body adds `target_slots` and `driver_models` — the server binds a model only to a disk chosen as a target; switch off = the body as before |
| `direct-open` | פתח סבב והער את המחשבים שנבחרו | `slots=<mac>@<port>,<port>/<mac>@<port>` | #715: POST `/api/console/room` with `source: {kind: build_disk, mac, disk}` + `target_slots`; the disk is the first non-removable one in the server inventory. Only ticked, present, fresh drawers are listed; none → `room_error=` locally. #1269: with drivers on, also `drivers=` → `driver_models` in the same body |
| `room-drawer-cancel` | "בטל…" on a writing drawer's row · "בטל דיסק…" / `X` (a choice among the writers) — #1386, after the window: "המשך לכתוב" (`Esc`) / the red "בטל את דיסק N" (`Del`, never `Enter`) | `cloner=<mac>`, `dev=<dev>` | POST `/api/console/room/drawer/cancel {mac, dev}` with the signed-in session (`roomflow.sh:room_drawer_cancel`); a refusal's reason is the toast |
| `drawer-cancel\|<dev>` | the cloner's own screen (#1386): the drawer's number key or its "בטל…", then the same window | — | no sign-in, like the SMART choice: the bridge (`cancel.sh:cancel_request`) takes only a drawer of this machine that is writing now, and writes its request; the agent carries it out in the stream |
| `room-wake` | העֵר את המחשבים שנבחרו (picker) · העֵר שוב (live round) | `cloner=<mac>[,<mac>...]` — Nadav 17/10: the picked cloners (a whole machine, off ones too, or one with a ticked drawer); in a live round, the round's cloners. None picked → a toast, nothing sent | POST `/api/console/room/wake {macs}` — only those wake; a record without `cloner=` is refused (`roomflow.sh`), never the whole room; answer with `toast=` |
| `room-start` | התחל עכשיו (with `room_warn=` lines: after the orange "התחל בכל זאת" of the confirm dialog, #432) · "התחל בכל זאת" of the missing-machines box (#65) | — | POST `/api/console/room/start` |
| `room-poweroff` | "כבה אותו" on a line of the missing-machines box → the confirm dialog's "כבה אותו" (#65, #1270) | `cloner=<mac>`, `confirm=<machine name>` (both from `room_missing=`, not typed) | `room_poweroff` (`roomflow.sh`): POST `/api/console/room/missing/<mac>/poweroff {confirm_name}`; the server checks again that the machine is missing, can hear and the wave is open; a refusal → `room_error=` |
| `room-close` | עצור סבב… → the confirm dialog's "עצור סבב" (#1270) | `confirm=<round image>` (filled from `round=`, not typed) | POST `/api/console/room/close {confirm_name}`; the server decides (#533), answer with `room_error=` or drop `round=` |
| `class-pick` | a class card | `group=<id>` | the operator chose a class. **Steps 2–3 of the wizard (machine grid, image list) are not built** — see "Not built" |
| `class-start` | התחל עכשיו | — | POST `/api/console/sessions/<id>/start` |
| `class-close` | עצור סבב… → the confirm dialog (#1270) | `confirm=<session image>` (filled, not typed) | POST `/api/console/sessions/<id>/close {confirm_name}` (#581) |
| `restore-start` | מחק ושחזר… → the confirm dialog (#1270) | `image=<id>`, `confirm=<machine_name>` (filled, not typed) | #706/#1073: hand-off to the agent (`handoff` file) — the bridge re-reads the machine's registered name from `GET /api/v1/agent/state` and accepts only a `confirm` equal to it; the agent re-validates the image against fresh `allowed_images`, then `single_restore_run` erases the internal disk and reboots |
| `shrink-continue\|<nonce>` · `shrink-plain\|<nonce>` · `shrink-cancel\|<nonce>` | the shrink dialog (#928): כווץ וקלוט / קלוט בלי כיווץ / בטל קליטה, or keys 1 / 2 / 3, Esc = cancel | — (the token carries the nonce of the question on screen) | `gui_shrink_dispatch` (`agent/lib/shrink/shrinkgui.sh`) writes `nonce\|<answer>` to `$GUI_DIR/shrink-decision` only for the nonce of the active `shrink-request`; no console session is needed. One answer per question; Enter answers nothing |
| `tool-list` | the "כלים" button of the build menu (#649) | — | the bridge writes `$GUI_DIR/tools` (`tools_gui_list` in `agent/lib/tools/tools.sh`) |
| `tool-run\|<id>\|<arg>\|<confirm>` | הרץ on a tool's confirm view, or a `ro` row with no argument (#649) | — (the token carries the fields; `\|` and newlines never travel) | `tools_gui_run`: the framework refuses `rw`/`destroy` unless `confirm` equals the machine's registered name, runs the domain module, and writes `$GUI_DIR/tool-result` |

#1269 `drivers=<mac>|<port>|<vendor>|<model>|<mac>|<port>|…` — four fields
per chosen disk that has a model (a disk set to "בלי" is not there). `|` is
safe as the separator because no state value carries one (`guistate.sh`
replaces it with a space), and vendor/model are sent in the library's spelling
from `driver_model=`. There is no "automatic": the hardware the cloner detected
is not the target's (docs/interfaces.md §17). Refused locally, with the reason
in `room_error`: no disk chosen, no disk with a model, or a model the library no
longer lists; the bridge (`room_drivers_json`, `roomflow.sh`) posts nothing on
a malformed value, and the server checks every model again.

Local validation stays local, as in the HTML: "בחרו כונן ותנו שם לאימג'" and
"בחרו אימג' וקבעו יעד כוננים" are shown without a record.

### The `--state` file

Plain text, `key=value`, `|` between fields, one line per item; blank lines
and `#` comments ignored; an unknown key is reported on stderr and ignored. A
value must not contain `|` or a newline. **Every read replaces the whole
State**, so the file is always the complete picture (like one `/api/…`
response), not a diff.

```
# #st-pick (state.disks; removable ones are filtered out, as drawDisks does)
disk=<dev>|<model>|<size_bytes>|<has_data 0/1>|<removable 0/1>
disk_slot=<dev>|<drawer>|<conn>     # #1495: that local disk's number by the drawer mapping (slotnames.sh
                                    # disk_drawer); the capture list prefixes "דיסק N" -- or, drawer 0,
                                    # "<conn> (לא ממופה)". No record for a dev = drawn as before
folder=<name>                       # /api/console/folders, one per line

# #st-progress / #st-done (state.task)
task=pending|running|done|failed
task_name=<name>  task_disk=<dev>  task_error=<text>
task_direct=<0/1>                   # #715: 1 = a direct_send task (titles say "משדר", not "קולט")
pct=<0..100 | -1 unknown>  moving=<0/1>  bytes=<bytes_written>  partition=<n>
title=<text>  sub=<text>            # override the head texts (cloner / receiver display)

# forces #st-message (the !state.known / wrong-role cases)
message=<title>|<sub>

# #1569: the one failure screen (agent/lib/gui/failscreen.sh; mockup PR #1550).
# fail= opens the block; a non-empty title routes to it before everything.
fail=<tone bad|warn|inf>|<hold 0/1>|<title>|<sub>|<role>   # all Hebrew; the English line is in fail_code only
                                    # inf (#1802): not a failure -- ui_unknown, "המחשב הזה לא רשום" (blue, an i)
fail_why=<text>  fail_safe=<text>   # "מה קרה" / "מה בטוח"
fail_ctx=<label>|<value>            # 4: machine · disk/drawers · image · stage; a capitals/digits value is mono
fail_row=<label>|<sub>|<ok|wn|bad|off>|<pill>   # up to 8, one per target
fail_step=<text>                    # up to 4: the sentence, then muted lines; fail_code=<line> up to 12 (not drawn, #2068)
fail_btn=<action>|<label>            # up to 3 (#2068): the first is Enter; a press writes fail.action (hold.sh)
fail_at=<epoch>                     # die_local and ui_unknown: the reboot moment (the pill counts)

# #st-menu: the "הפצה לכיתות" card only when the edition flag AND the
# operator switch are on (#1081 classrooms + #880 class_deploy_enabled
# in hello /state). Missing = 0.
menu_class=<0/1>

# #1073: the machine's registered name (GET /api/v1/agent/state .name) -- the
# confirm= the restore sends after its dialog (#1270: no typed name; the
# bridge still compares). Always written; empty = no start.
machine_name=<name>

# #1252: the server this machine talks to -- the header ("שרת", "גרסה"), the
# login foot and the standby line. name/version from GET /api/v1/agent/state
# (.server_name / .server_version, null -> empty); the address is the host of
# imagesrv.server ($SERVER), written also in the failure snapshot and by
# clonergui.sh. Empty = drawn as "לא ידוע", never a made-up value.
server_name=<name>
server_addr=<host>
server_version=<git tag>

# #928: the capture's shrink question (shrinkgui.sh -> guistate.sh). A modal
# dialog over every screen; drawn only when every declared row arrived and is
# valid (new < old) -- otherwise a warning, and the agent cancels on its timeout.
shrink_prompt=<nonce>|<count 1..16>
shrink_partition=<idx>|<old bytes>|<new bytes>   # one per row, <count> rows

# #1090 standby heartbeat (positive evidence from the latest hello). Written by
# guistate.sh and, since #1304, by clonergui.sh too (gui_hello_state) -- the
# cloner's standby screen draws "connected" / "server not answering" from them.
# A hello.state the reader rejects writes neither line on the cloner: the
# screen then says "not known yet", and the drawers keep updating.
hello_age=<seconds>                  # age of the last completed hello
hello_rc=<HTTP status>               # response code observed for that hello

# #st-room (GET /api/console/room) and #st-class live rows
image=<id>|<name>|<folder>          # /api/console/images
machine=<name>|<mac>|<awake>|<joined>|<fresh_drawers>|<state>|<pct>|<moving>|<error>
room_drawer=<machine-index>|<port or 0>|<serial>|<model>|<size>|<smart>|<fresh>|<selected>|<drivers>|<detail>
                                    # #1235: fresh 1 = ready, 0 = written, -1 = no serial (GET /room
                                    # fresh:null, #105) -- shown as "לא ניתן לזהות את הכונן בחריץ N"
                                    # #1269: drivers = staged / skipped / failed from drawer_list[].drivers,
                                    # empty = the step did not run; detail = packages / error / reason.
                                    # Optional: an older agent's eight fields read as "did not run"
                                    # #704: |<slot name> -- an 11th field, drawer_list[].slot_name (the
                                    # operator's name of the slot; NVMe by serial). Empty / absent = "דיסק N"
                                    # #432: |<link state> -- a 12th field, drawer_list[].link_state:
                                    # ok / slow / unknown; only "slow" colours the slot orange (SMART red wins)
                                    # #1366: |<crc> -- a 13th field, the CRC count when drawer_list[].crc_suspect
                                    # #1386: |<dev>|<cancel text>|<cancel done> -- fields 18-20: the device the
                                    # "בטל…" button names, drawer_list[].cancel.text_he ("בוטל על ידי nadav ·
                                    # 16:06", empty = not cancelled), 1 = the agent reported `cancelled`
                                    # #1331: |<boot state>|<boot service>|<boot error> -- fields 21-23,
                                    # drawer_list[].drivers.boot (guistate.sh); empty = not reported
                                    # (over crc_warn_threshold); empty = not over / not read. Orange like slow
                                    # #1495: |<drawer>|<conn> -- fields 14-15, drawer_list[].drawer (null -> 0
                                    # = not in the mapping) and the connection ("SATA 2", "NVMe 03:00.0").
                                    # The grid places a drawer by this number, never by port; a 0 is an extra
                                    # row "<conn> (לא ממופה)" that cannot be picked. Absent = by port, as before
                                    # 17/10: |<pct>|<target state> -- fields 16-17, this drawer's OWN target in
                                    # the wave (drawer_list[].bytes_written/bytes_total/state): -1 = no total,
                                    # "" = not a target (no bar: "לא נבחרה" / "ממתינה"). Absent = an older
                                    # agent: the live grid draws the machine's progress on every drawer, as before
                                    # #1742: |<smart reason> -- field 27, drawer_list[].smart_reason, sent only
                                    # when there is one (fields 24-26 are then padded empty if there is no linux
                                    # object). "readonly" = red because the disk is read-only: the note says
                                    # "הדיסק במצב קריאה בלבד", not "SMART תקול". Absent = as before
room_slot=<machine-index>|<drawer>|<conn>|<name>
                                    # #1495: one per drawer of machines[].drawer_slots, empty ones too -- the
                                    # grid's rows 1..N and the name of an empty one. Selection stays by port
room_warn=<text>                    # #432: one line of round.precheck (an open wave only): a drawer the wave
                                    # would write on a link below its port, or with a risen CRC. Orange box
                                    # above the grid; "התחל" asks first. The server's text_he, as the console
room_missing=<mac>|<name>|<reason>|<can_poweroff 0/1>|<text>
                                    # #65: one line of round.missing -- who the open wave waits for (off /
                                    # not_joined / drawers). "כבה אותו" only with can_poweroff 1
room_slot_name=<machine-index>|<port>|<name>
                                    # #704: the name of a SATA slot (GET /room slot_names "sata:<port>"),
                                    # empty slots too. A drawer's own field-11 name wins. Label only:
                                    # selection and slots= stay by port
driver_models=<n>                   # #1269: models read from GET /api/console/room/driver-models; -1 = not read
                                    # (absent = -1). 0 and -1 both disable "להוסיף דרייברים", with two reasons
driver_model=<vendor>|<model>       # one per model, the library's spelling (only models with a package)
round=<image_name>|<wave_number>|<open 0/1>|<written>|<target>|<ready>|<remaining>|<elapsed_s>|<rate_bps>|<eta_s>|<wave_state>
                                    # #410: the last three from GET /room .round (-1 = not measured, never 0);
                                    # a seven-field record from an older agent reads as all three unmeasured
                                    # 17/10: |<wave_state> -- running / closed / failed ("" = an older agent).
                                    # Only running (or "") says "הגל משדר"; failed draws a red line.
                                    # A round whose own state is failed is never round= -- see room_failed=.
                                    # A live round's machine= rows are only the cloners in round.target_slots
room_failed=<reason>                # 17/10: the last round failed (GET /room .round.state failed). Not a live
                                    # round: the picker stays usable, with one red line "הסבב הקודם נכשל: <reason>"
updated=<epoch seconds>             # #410: when the agent wrote this snapshot -- the room and cloner heads say
                                    # "עודכן לפני N שניות" (דקות, שעות) and keep counting when the file stops changing

# #st-cloner (clonergui.sh, the kiosk's local truth)
drawer=<port>|<dev>|<state>|<bytes>|<total>|<error>|<rate_bps>|<eta_s>|<drawer>|<conn>
                                    # #410: fields 7-8 measured by the kiosk on the local counter; -1 / absent
                                    # (a six-field record) = not measured -- the line shows bytes only
                                    # #1495: fields 9-10 = slotnames.sh disk_drawer: the drawer by the mapping
                                    # (0 = not in it: "SATA n (לא ממופה)", sorted last) and the connection.
                                    # Absent = an older agent: drawer = port, as before
                                    # #1386: |<cancel>|<cancelled_at> -- fields 11-12: who asked to cancel
                                    # the drawer as the screen says it ("על ידי nadav", "ממסך מחשב השיכפול";
                                    # empty = nobody) and when the agent carried it out (epoch; empty = not
                                    # yet: "מבטל…"). State `cancelled` = grey row, "בוטל", an empty bar
                                    # #1331: |<state>|<detail>|<boot state>|<boot service>|<boot error> --
                                    # fields 13-17, targets/<dev>/drivers.json (drvgui.sh); absent = did not run
                                    # #2055: <rate_bps> 0 = measured still -- a writing drawer whose bytes did not
                                    # move for CLONER_STALL_S (10 s): "ממתין לזרם מהשרת · 0 MB/s", orange.
                                    # Display only: the write goes on (a 30 s cut came back on metal, 23/10)
drawer_moved=<dev>|<epoch>          # #2055: after that drawer's drawer=, while it writes: when its bytes last
                                    # moved. The cloner head's "עודכן לפני N" counts from the newest of these,
                                    # not from updated= (which is rewritten every tick); absent = updated=
cdisk=<port>|<dev>|<model>|<size>|<smart>|<cause>|<link>|<link_max>|<crc>|<drawer>|<conn>|<mark>|<hw>
                                    # the idle inventory (no round targets) -- see the #1304 table below;
                                    # #1495: fields 10-11 as drawer= fields 9-10
                                    # #1859: <cause> also carries why a `warn` disk is orange (a smart.sh
                                    # SMART_WARN_REASONS word, said by smart_reason_text)
cused=<dev>|<size>|<used>           # #2030: size and bytes in use of a disk (diskused.sh), round or idle;
                                    # used -1 = not measured -> "לא נמדד", never 0. See docs/interfaces.md
cdrawer_slot=<drawer>|<conn>|<name> # #1495: one per drawer of the hello's drawer_slots, empty ones too. With
                                    # them the cloner screens lay out drawers 1..N of the mapping (the cage
                                    # size is no longer a guess); without them -- an older server, no answer
                                    # yet -- the convention drawer k = port k, as before
coptical=<drawer>|<conn>|<model>    # #1495: a CD/DVD drive (/sys/block/sr*, only with sr_mod loaded):
                                    # "כונן CD/DVD" in its drawer, or an extra row when not mapped -- never
                                    # a disk, never "ריק", never a target
cslot_name=<port>|<name>            # #704: the operator's name of SATA slot <port>, from the last hello's
                                    # slot_names (slotnames.sh). Drawn instead of "דיסק N" on the cloner
                                    # cards, its SMART question and the standby list; absent = "דיסק N"
drawer_count=<1..8>                 # #1565: the hello's drawer_count (#1315, clonergui.sh). Without a
                                    # cdrawer_slot= mapping the cage has this many bays instead of the
                                    # legacy 3; a disk in a later port still adds its bay. Absent or out
                                    # of range = the legacy 3

# #st-class
class=<id>|<label>|<machines>       # /api/console/groups, role classroom
session=<image_name>|<prefix>|<group_label>|<open 0/1>|<joined>|<expected>|<starts_in_seconds>

form_error=  room_error=  class_error=   # the .error line of each card
toast=<text>                             # shown for 2.6 s on every reload that carries it
```

Every physical record must end in `\n`. An unterminated or overlong record is
rejected whole (never kept as a possibly broken UTF-8 prefix), reported once on
stderr, and flagged on screen. Rows beyond `MAX_DISKS` / `MAX_MACHINES` are
also counted and shown as “+N more, not shown”. A loaded state file older than
10 seconds remains the last snapshot but is marked stale with the toast
“הסוכן לא מעדכן מצב”.

`machine=` rows serve both the room (`awake`/`fresh_drawers`/`joined`/`state`
per `room.js machineRows`, states `writing verifying waiting done failed
partial`) and a live class (`classes.js renderLive`, states `waiting done
failed` or a percentage). `pct` -1 with `moving` 1 is "נקראו בייטים · הסך לא
ידוע", -1 with 0 is "טרם נקראו בייטים · הסך לא ידוע" — `progress.js
Progress.view`, ported in `widgets.c`.

### #649: the toolbox files (`$GUI_DIR/tools`, `$GUI_DIR/tool-result`)

The IT toolbox is a fourth screen of the **build machine only** -- the
"כלים" button at the menu card's top-left (the RTL inline end), not one of
the choice cards; the cloner and class screens route before the menu and
never show it. Its data is not in the state file: two files **next to** it
(the directory of `--state` is `$GUI_DIR`), polled by mtime/size/inode like
the state file itself.

```
# $GUI_DIR/tools -- written by the bridge on tool-list
machine=<the name the operator must type for rw/destroy>
<id>|<domain>|<title>|<risk>|<args>      # one row per tool, tools_list order

# $GUI_DIR/tool-result -- unlinked at the start of a run, written at its end
<id>|<rc>|<path of the output text>|<seq>
```

* `domain` groups the list under דיסקים / רשת / Windows / אתחול / חומרה
  (`disk` / `net` / `windows` / `boot` / `hw`; an unknown domain is its own
  heading, named by the word). `risk` draws the tag: `ro` grey "קריאה
  בלבד", `rw` orange "משנה דיסק", `destroy` red "הרסני" -- and any other
  word is treated as `destroy`, as `tools.sh` does.
* `args` is the argument's label. **`disk:<label>`** makes the confirm view a
  disk picker over the `disk=` records (the chosen `dev` is sent); any other
  non-empty text is a free field (no spaces, no `|`). Empty = no argument.
* A `ro` tool with no argument runs on the click; everything else passes the
  confirm view -- the warning and the argument -- and `rw`/`destroy` then
  ask in the confirm dialog (#1270, owner 10/10: no typed word); the GUI
  sends the machine's name from `machine=`, which the bridge compares before
  any module runs.
* `rc` words: 0 הסתיים (success tokens) · 1 נמצאה בעיה · **2 לא הצלחנו
  לבדוק (warning tokens, never green -- principle 5)** · 3 האישור לא תואם ·
  4 כלי לא מוכר. `seq` increases per run so an identical result of a second
  run is still seen as new; a record for another tool, or with a seq already
  consumed, is ignored. While waiting, the output view shows "רץ…" with a
  spinner (the loop wakes every 120 ms only then).
* No wheel in `input.c`: the list and the output scroll with למעלה/למטה
  buttons in the footer, shown only when something is clipped.
* Empty list after the file was read: "לא נבחרו כלים בשרת (תשתית › ארגז כלים)" (#1050: the
  station offers only the selection saved in the console); before
  the bridge answered: "טוען את רשימת הכלים…" -- two states, not one.
* `--png` cards `tools`, `tools-confirm`, `tools-output` use a built-in
  sample list (`tools_sample`); a real `$GUI_DIR/tools` is read only when the
  live process runs with `--state`.

### Routing (the state machine)

`app_route()` in `screens.c` is `station.js poll()` in its order, run before
every draw:

0. `fail=` with a title → the failure screen (#1569), whatever else is there.
1. `message=` present → `#st-message`.
2. `task=pending|running` (or `--screen progress`) → `#st-progress`; it
   remembers that it was watching.
3. Watching and `task=done|failed` → `#st-done` with `drawDone`'s texts, until
   קליטה נוספת.
4. Mode classes and (signed in or `session=`) → `#st-class`.
5. Not signed in → `#st-login`.
6. Mode room → `#st-room`; capture → `#st-pick`; tools → the toolbox (#649);
   else `#st-menu`.

The menu's four cards set the mode (capture → pick, room → room, classes →
class) and emit their word; `restore` exits. Restore has no confirm or pick
screen because the HTML has none — the #382 card hands the whole flow to the
agent, as before.

### `--backend mem`: a machine with no screen (#835)

When no display is attached (every connector down, so i915 creates no
`/dev/fb0` — cloner 2), the screens are drawn into a 1280×800 framebuffer
file that `imagesrv-monitor --fb <file>` serves over RFB, so the console's
monitor page shows the machine anyway. `--backend mem [--fb-file PATH]`
(default `/run/imagesrv/fb.mem`); `auto` falls back to it after drm and
fbdev. The kiosk (`agent/lib/gui/guibridge.sh`) picks `mem` exactly when
`/dev/fb0` is not a character device, and `fbdev` otherwise, as before. The
file layout — a 4096-byte header with magic, geometry and a frame seqlock,
then XRGB8888 rows — is the contract in `docs/interfaces.md` §15.

### `--png`: every card, for comparing with the HTML

```sh
make png                      # out/station-<card>-{light,dark}.png, 48 files, sample data
make png STATE=/tmp/state.txt # the same cards from a real state file
```

Cards: `login menu pick progress done room room-live class class-live
cloner standby message restore direct tools tools-confirm tools-output failure
shrink logout restore-folder room-images restore-confirm room-drivers pick-hebrew` (`logout` is the menu with the question Esc opens; `restore-folder`/`room-images` are the folder-first image pickers inside a folder and open; `restore-confirm` is the #1270 confirm dialog; `room-drivers` (#1269) is the room setup with "להוסיף דרייברים" on, in pick mode, one disk set to "בלי"; `pick-hebrew` (#1245) is the capture form with the description focused and the typing-language chip on "עב"). Since the
10/10 mockup every screen is laid out in logical pixels and drawn at
`ui_scale(H)` = clamp(H/800, 1, 1.4) in quarter steps: 1024x768 and 1280x800
are 1:1, 1280x1024 and 1920x1080 are 1.25 -- a test that measures pixels at
those sizes multiplies the mockup's numbers by it. No text is drawn below
11px (`TEXT_MIN_PX`, text.c). The renderer puts the App into the state that *routes* to each
card and fails if the route landed elsewhere — so `make png` also exercises
the routing rule. The sample data has two disks (one chosen, the form open
with a name typed), two folders, two images, three cloner machines (one
writing at 42%, one done, one off), three classes (one with no machines,
drawn dim), a wave-2 round 7/24 and a class round 3/14 opening in 4:32.

### #1304: the cloner's standby — mockup A, "חזית המארז"

`src/screens_standby.c`, built 1:1 from `docs/design/native/standby-a/`
(`standby-a.html`, `?f=empty|two|off|two-dark`, and the four PNGs). One
status line, the drawer cage (drawer 1 on top), IP / MAC / hello in the
status bar, no background art. What each part is drawn from:

| on screen | source |
|---|---|
| green dot "מחובר. ממתין לסבב." / red X "השרת לא עונה" / grey dot "ממתין לתשובה מהשרת" | `hello_rc`/`hello_age`: 200 at most 120 s old / anything else / no hello yet |
| "מחובר אל imagesrv-ta" | `server_name=`, else `server_addr=` (#1252) |
| "מאז 12:41" (server down) | only when the last code was a 200 that went stale -- `hello.state` keeps the last *attempt*, so after a failed one the last success is not known and the line is left out |
| bay mapping and name source (#1495; since #2030 the card's name is the connection, see below) | the bay is drawer N of the mapping (`cdrawer_slot=`, #1495); its name is the slot name there (or `cslot_name=` without a mapping, #704), else "דיסק N"; the connection is that record's `conn`. A disk whose `cdisk=` drawer is 0 is an extra row after the bays: "—" / "לא ממופה" in the number box, "SATA n (לא ממופה)" as its name. A `coptical=` drive is "כונן CD/DVD" in its bay (or an extra row), never "ריק" |
| size, model | `cdisk=` fields 3–4; the size in market units (500107862016 → "500 GB") |
| the card's name (#2030) | the connection -- "SATA 0", "NVMe 03:00.0" -- not "דיסק N" next to the big drawer number; the operator's slot name still wins (#704); a disk outside the mapping is "<conn> (לא ממופה)". Never a device name ("sda"). Size: `cused=` (round and idle) else `cdisk=` field 4 |
| the card's third line (#2030) | before the write "תפוס עכשיו: X" (`cused=` used: what it will erase), writing "נכתב עד כה: X", done "נכתב: X" (`drawer=` field 4); no measurement, a cancelled write, or no `cused=` record at all is "לא נמדד" -- never 0. Dropped in a crowded cage (rows under 72px) |
| SMART chip, its colour, the bar on the bay, the orange/red line under the cage | `cdisk=` field 5 by the #872 rule: `failed_last` red, `fail` (and an old agent's `warn`) orange, the rest green; `unchecked` says "SMART לא נבדק" |
| the reason under the name | `fail` → "ה-BIOS יעצור ב-F1 — SMART: …" by field 6, the SMART reason (#1366: `health_failed` / `pending_sectors` 197 / `uncorrectable` 198); `fail` with `readonly` (#1742) → "הדיסק במצב קריאה בלבד"; `failed_last` → field 6, the server's cause (כבל/חריץ, הדיסק) |
| the hardware note (#1921) | `cdisk=` field 13: the sentence of `agent/lib/data/hw-issues.tsv` (a link reset, a drawer that dropped, USB 2.0 -- CRC and the link keep fields 9/7-8). Drawn like the CRC line: the bay's bar orange, the sentence under the name and under the cage; the SMART chip stays SMART -- never the disk's colour. `lc_disk=` field 17 is the same note on the local/USB screen, after the SATA line |
| the CRC line (#1366) | `cdisk=` field 9: the CRC count when over the hello's `crc_warn_threshold` (empty = not over, not read, or an older server). The bay orange like a slow link, "כבל או מגירה חשודים — N שגיאות" under the name (when SMART and the link have no reason of their own), and a line under the cage |
| the link, "· 6.0 Gb/s" after SATA N-1, and its orange line (#432) | `cdisk=` fields 7–8 (`disk_link`: the speed and the port's maximum, `null` = not read → "קישור לא ידוע", never blank and never green). Below the maximum: the bay's bar and border orange, "קישור 1.5 מתוך 6.0 Gb/s — לבדוק כבל" under the name (when SMART has no reason of its own), and a line under the cage. The SMART chip and LED stay SMART |
| IP, MAC | `--ip` / `--mac` (the kiosk's) |

Not in the data, so not drawn: the mockup's sample reason "12 סקטורים
הועברו (5)" (the agent sends the verdict, not the counters -- and since
#872 the counters do not colour a disk). The number of drawers is the
mapping's length (`cdrawer_slot=`, #1495); only without a mapping (an older
server) is the cage still three bays (#1170) or up to the highest port with a
disk or a slot name.

### #1090: the approved mockup and console tokens are the design source

For #1090 the approved source is
`docs/design/native-gui-mockup-2026-09-18.html`. Its colours
are the console tokens from `server/static/design-tokens.css`; every colour and
size in `theme.c` cites its mockup rule (`css: selector | prop | index`), and
`tests/test_native_gui_theme.py` reads both light `:root` and dark
`:root[data-theme="dark"]` values and fails on drift. Neither theme is derived.
The status-bar word ("ממתין לאימות", "כותב 3 דיסקים במקביל") is derived from
the current screen, not a state key. 1 CSS px = 1 framebuffer pixel.

### What matches the HTML, and where it does not

The default theme is light (owner decision 16/10, #1499; the 18/09 mockup's
default was dark). The 48px header remains dark in both themes, like
the console app shell; the body uses the selected console tokens. The login
screen has no app header and uses the approved split form/illustration layout.
The only aspect-ratio branches are the room's 6/4 columns, the cloner's 88/64px
percentage, and the restore image grid's 2/1 columns.

1. **Theme button glyph.** `station.js` sets `☾`/`☀`; IBM Plex has neither
   code point and the initramfs carries no other font, so the button would
   show a tofu box. The moon and sun are drawn as vectors instead.
2. **`box-shadow` blur.** Cairo has no gaussian blur; shadows are 14 stacked
   translucent layers. Same colour, same offset, softer edge.
3. **Theme persistence.** Since #1499 (owner 16/10) the theme is the signed-in
   user's saved preference (`users.theme`, shared with the console): the login
   answer carries it, and a switch while signed in (the corner button or F2)
   is saved with `PUT /api/console/me/theme` through the bridge. Before login
   and on the cloner screens the switch is for the screen only; sign-out goes
   back to the default (`--theme dark` starts dark).
4. **Unavailable measurements.** The current state protocol has no per-disk
   rate/ETA or SMART attributes 5/197/199, so those cells show `—`; Health uses
   the available SMART verdict. Likewise, a room node cannot accept the
   per-machine “המשך?” answer until `machine=` carries a nonce and response
   target (the approved mockup records this protocol gap explicitly).
5. **The `<select>` list.** The browser's dropdown is a native widget outside
   the CSS; here it is a list in the page's language (surface, hair border,
   14px rows, indigo-soft for the chosen one) below the box, closed by a
   click elsewhere or Esc. The box itself, with its arrow at the inline end,
   follows the input rule the CSS gives `select`.
6. **`::placeholder`** has no rule in the CSS; the browser's default grey is
   approximated with the muted token.
7. **The indeterminate bar** is animated in the browser (`progress-unknown`);
   here the striped 40% block stands at its starting position.
8. **No scrolling.** `.sbody{overflow-y:auto}` — a body taller than 92vh is
   clipped, and what is clipped is not clickable. Eight disks or thirty
   machines fit a 1280×800 screen; a longer list would need a wheel, which
   `input.c` does not read.
9. **`<b>` is 700.** `.menu-card b` / `.disk-card b` / `.room-row b` /
   `.st-prog-line b` are `<b>` elements — `font-weight: bolder` of 400 is 700,
   which IBM Plex Sans Hebrew Bold provides. The menu previously used 600;
   fixed here.
10. **`.sub` is body text.** There is no `.sub` rule in `console.css` or
    `station.css` (only `.metric .sub`, which is not an ancestor on this
    page), so `<p class="sub">` renders as 14px ink in the browser. That is
    reproduced (`sub_make`, one place to change) — it is probably a missing
    CSS rule in the HTML, and that is a finding for its own Issue, not fixed
    here.
11. **Drawer chips.** `room.js drawerLine` renders a chip per drawer under a
    machine row ("מגירה 1 sdb · נכתבה"); the state file has no per-drawer
    data yet and the chips are not drawn.

Fonts are checked positively at start (`text_fonts_present`): if IBM Plex Sans
Hebrew or IBM Plex Mono do not resolve, the program exits 1 and says which,
instead of rendering in a fallback face nobody would notice was wrong.

### Not built, on purpose

* **`classes.js` steps 2 and 3** — the machine grid (`.machgrid`) and the
  image list grouped by folder (`.img-group` / `.img-row`) with the prefix
  field. The task scoped `#st-class` to the class list and the live view;
  `class-pick` is where the wizard would continue.
* **The student / off-network flows (#396)** — not in the HTML, not designed.
* ~~**Hebrew typing.**~~ Built in #1245: `input.c` carries every key in US
  and in the Israeli standard layout (SI-1452, base level; Shift stays US),
  Alt+Shift or the status-bar chip switches, and `utf8.h` stores the
  letter as UTF-8. English only: user, password, the room count, a tool's
  argument — and the installer's fields; the chip says so on those fields.

## Build recipe (Debian 13, the lab VM)

```sh
apt-get install -y --no-install-recommends gcc make pkg-config \
    libpango1.0-dev libcairo2-dev libdrm-dev libfribidi-dev libharfbuzz-dev
# fonts-ibm-plex is in contrib (build_initramfs.sh already handles that, #120)
apt-get install -y --no-install-recommends fonts-ibm-plex

cd native-gui
tools/run-smoke.sh           # 1. Hebrew first. Stop here if it fails.
make png                     # 2. out/station-*-{light,dark}.png: compare to the HTML
tools/measure-size.sh        # 3. the real initramfs cost, uncompressed and zstd'd
# 4. on a VM console (root, no compositor running):
FONTCONFIG_FILE=$PWD/fonts.conf ./imagesrv-station-gui --demo --mac 00:15:5D:01:02:03 --state /tmp/state.txt
```

For the initramfs (a later change to `tools/build_initramfs.sh`, not made
here): `copy_bin imagesrv-station-gui` pulls the library closure exactly as it
does for every other binary; add the six font files (the faces `index.html`
loads: Sans Hebrew 400/500/600/700, Mono 400/500 — `measure-size.sh` names
them) under `/usr/share/fonts/truetype/ibm-plex`, `fonts.conf` as
`/etc/imagesrv/fonts.conf`, and export `FONTCONFIG_FILE` in the kiosk
wrapper. With that file, `/etc/fonts` and `/usr/share/fontconfig` need not
be packed. Kernel side: a KMS driver for the target must be in
`REQUIRED_MODULES` — `simpledrm` covers any UEFI machine from the firmware
framebuffer without a GPU driver, `hyperv_drm` the Gen2 lab VMs; both need
verifying. `cage`, `chromium`, `seatd`, `libinput`, `xkb-data` and the Mesa
DRI drivers all go away.

### Expected size — what is known and what is not

Known (packages.debian.org, trixie amd64, `.deb` = compressed = bytes over
the wire): the ten runtime libraries in `deps-debian.txt` total ≈ 3.9 MB, of
which glib is 1.5 MB. Not yet counted: their transitive dependencies
(`libthai0`/`libdatrie1` behind pango, `libpcre2-8-0`/`libffi8`/`libmount1`
behind glib, `libpng16-16`/`libbrotli`/`libbz2` behind freetype, and — from
memory, not from a fetched page — Debian's `libcairo2` is built with its X11
backends and so drags in `libx11-6`/`libxcb1`/`libxrender1`/`libxext6`
although nothing here opens a display). The six font files are a few hundred
kB each (not measured). **The honest number is the output of
`tools/measure-size.sh`**, which packs the closure the way the builder does
and zstd-compresses it. The expectation is single-digit MB against 120–315 MB
for a browser; the script will say.

## What I could NOT verify (written on Windows, without a C compiler)

* **The six new cards compile.** The login and menu did, on the VM; the
  files added since (`widgets.c`, `screens_capture.c`, `screens_rounds.c`,
  `state.c`, the new `main.c`) were written against the same APIs and
  re-read once. Expect the first `make` to surface typos.
* **They look like the HTML.** `make png` gives 20 files to put next to
  browser screenshots of each card (`room.js`/`classes.js` bodies need the
  server to render; the sample data mirrors their fields). The CSS margin
  rhythm around inputs inside labels (14 + 6, plus the inline-block
  baseline) and the `.folder-row` with the select's margin inside it were
  worked out by hand and may be a few pixels off.
* **Line C of the smoke test** passes — same mechanism as A and B, new
  assertion.
* **The state file loop** — atomic rewrite + mtime polling + the record stream
  on stdout — was not run against a real agent; `state_parse` was checked by
  reading, not by a test. A unit test in C for the parser is the obvious next
  addition.
* **KMS on the actual targets**, **input** (evdev, US keymap), and **sizes**
  — as before: not exercised on hardware.

## Next step

On the Debian 13 VM: `make` → fix what the compiler says → `tools/run-smoke.sh`
(line C included) → `make png` and compare the 20 PNGs to the HTML cards →
then wire the agent: `--auth-cmd`, the state file writer, and a reader for
the stdout records.
