# Changelog

> Unreleased / Fixed (#1793): a drawer with a server-recorded `disk_failure`
> is excluded from the goodput denominator and holds the learned rate. Stream
> losses still count as zero; an unclassified failure is `unmeasured`.

כל שינוי שראוי לדעת עליו, לפי תג. הפורמט הוא
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), והגרסאות הן
[semver](https://semver.org/lang/he/) בתגים — הכלל לגזירתן כתוב
בהנחיות הפרויקט.

הכותרות הן הקטגוריות של Keep a Changelog בעברית: **נוסף** (Added),
**שונה** (Changed), **תוקן** (Fixed), **אבטחה** (Security). נוספה קטגוריה
אחת, **תיעוד**, לשינויים שכולם במסמכים — הפרויקט מתעד החלטות בקומיטים
נפרדים, ולקפל אותם ל"שונה" היה מסתיר אותם.

**ההיסטוריה כאן מתחילה ב-`v0.10.5`** — התג הראשון של יום העבודה של
2026-08-29, שבו יצאו 19 תגים (`v0.10.5` עד `v0.15.1`) וזה מה שהוליד את
הצורך בקובץ הזה. לתגים שלפניו יש `git log`, ולא נכתבו כאן בדיעבד.

---

## [Unreleased]

### v0.95.0 (14/11)

v0.94.3 plus nine PRs (a minor release: #2973 adds a button the operator sees, "מקלדת מסך" in the installer): free scanners and bots for the public repository, local scans for the private one, lab analysis tools and wave 1 of the touch map (#3095), and three fixes (#2973, #2977, #3069). Every new public workflow runs only in the public repository (a `github.repository` guard on each job), and none of them has run on GitHub yet; they run for the first time with this release.

- **Free scanners in the public CI** (#3093, Added). OpenSSF Scorecard, zizmor, OSV-Scanner, actionlint (blocking) and Lighthouse on the console and the monitor page, each new action pinned to a full SHA. `tests.yml` uploads coverage to Codecov from the 3.13 shard, with `continue-on-error`, so it never colours `pytest (3.12)`/`pytest (3.13)`. The existing workflows are not pinned yet (#3087).
- **lychee and first-interaction in the public repository** (#3097, Added). A weekly link check of `README.md` and `docs/**/*.md` (the report is in the run summary; three hosts that answer 403 to bots are excluded by name), and a bilingual greeting on a first issue only. No published workflow uses `pull_request_target`, and a test holds it.
- **A nightly scan of the private main on the testrunner, and a pre-commit hook** (#3101, Added). gitleaks, Semgrep CE, zizmor, actionlint, Grype, ruff and shellcheck on a fresh clone of main at 03:00, deferred while a gate holds the lock; the summary says new, existing and resolved, and a tool that broke is "not scanned", never "resolved". `tools/git/install-hooks.sh` turns on `tools/git/hooks/pre-commit`: gitleaks on the staged diff (missing gitleaks blocks the commit), placeholder markers in files that would be published, ruff and `shellcheck -s busybox` on `agent/`.
- **Sanitizers and cppcheck on the agent's C, and shellcheck with the busybox dialect** (#3102, Added). A public-only job builds `fanout.c`, `lldpsniff.c`, `hivewrite.c`, `monitor.c` and `uevent.c` with ASan and UBSan through a gcc wrapper and runs the tests that use them; cppcheck 2.13.0 fails on a finding outside the baseline. `shellcheck.yml` uses the official 0.11.0 binary (sha256 checked) with `-s busybox` for the agent's files. The product build is not changed.
- **mutmut, AFL++ and Syft/Grype/debsecan scripts for the testrunner** (#3103, Added). Lab tools only: mutation testing of the guards, a fuzz harness for the LLDP parser, and a scan of the packages an ISO installs. Nothing in the product calls them.
- **Touch map wave 1: failures that were swallowed now leave a line** (#3134, Fixed; closes #3121, refs #3122). The agent's DHCP hook (`udhcpc.script`) wrote nothing when `ip`/`ifconfig`/`route` failed; every failure is now a line in `agent.log` with the exit code and output, and a new address is one line. The hook still exits 0. 38 other swallowed results get a log line or a comment that says why swallowing is right, and `server/core/cleanup.py` (`remove_tree`) reads back what a tree removal left. Three changes of behaviour: `setup-boot-server.sh` keeps an existing machine id instead of writing a random one on every run; **`build_initramfs.sh` stops the build when `dpkg-query -S` fails** (exit 1, "no owner", is still an answer; any other code stops the build by name, so a build that passed with a broken dpkg-query now stops); the attended-power beat writes one line when the answer stops and one when it comes back. Not run on the metal.
- **The installer hides its own install medium from the disk list, and the screen keyboard opens only from F5 or its button** (#2973, Fixed; closes #2968, #2969). The medium the installer booted from (`iso=1`) is not offered as an install target; when no other disk is left the screen says so instead of an empty table. A click on a field only moves the focus; the on-screen keyboard opens from F5 or from a new "מקלדת מסך" button in the footer, on the screens that have a field. v0.94.0 took this fix out because its own tests did not build on its head; the test drivers are fixed and the fix is back.
- **The GRUB stay-on screen probes `sleep` once instead of on every round** (#2977, Fixed; closes #1734). When the `sleep` module did not load, the loop of the stay-on screen tried to load it from TFTP again on every round, without end. `boot/grub_menu.py` and the copy in `deploy/setup-boot-server.sh` now run `sleep 0` once; on failure the screen says `GRUB could not load its sleep command. This screen is held without it.` and holds without loading anything. With `sleep` loaded nothing changes. The cause on the cloner was inferred from the code, not measured.
- **The remote-mode phone mockup, approved** (#3069, docs). `docs/design/mockups-1113/remote-phone.html`, private.
- Not in this release, from #3095: #3085 (merge the gate PR on publish), #3082 (`dependabot.yml` private only), #3087 (pin the existing actions) and #3076 have no PR yet.

### v0.94.3 (13/11)

v0.94.2 plus one line in the test workflow. The product is the same as v0.94.2. v0.94.2 reached the public repository, but its CI was red, so v0.94.3 is the first public release with a green CI.

- **The public CI installs xorriso** (Fixed). `tests/test_iso_sbom_2999.py` (#2999, in v0.94.0) builds a small real ISO with xorriso, and `.github/workflows/tests.yml` did not install it. The lab runs, where xorriso is installed, were green. The public CI of v0.94.2 runs with `IMAGESRV_REQUIRE_NATIVE=1`, so the missing tool was 4 setup errors, not skips, in shards 1/4 and 4/4. That is the guard doing its job. `xorriso` is now in the workflow's `apt-get install` line. No product file changed.

### v0.94.2 (13/11)

v0.94.1 plus one file. v0.94.1 stayed internal: its publish was refused before any push, and that refusal is what this version fixes.

- **The Debian security pool of the release is rebuilt and carries smbclient** (Fixed). `tools/iso/packages.txt` gained `smbclient` in v0.94.0 (#3011, #2382), but `deploy/security-pool.json` still named the pool of 2026-11-04, built before it. `publish-to-public.sh` refused the release by name (`missing packages required by tools/iso/packages.txt: smbclient`). Had it gone out, the update button would have failed on every server and returned it to the previous tag. The new pool is `imagesrv-security-pool-2026-11-13.tar` (1019 packages, 125 of them from trixie-security). `make-pool.sh --security` built it from the v0.94.1 tree, and `deploy/security-pool.json` names it. No other file changed.

### v0.94.1 (13/11)

v0.94.0 plus two PRs (#3081, #3089). v0.94.0 was not published to the public repository, so v0.94.1 is the first public release with the v0.94.0 changes below, the security fixes among them.

- **The public repository gets a code of conduct** (#3089, Added). Owner's decision, 13/11. `docs/public/CODE_OF_CONDUCT.md` reaches the public tree as `CODE_OF_CONDUCT.md` through `public-overlay.sh`, and it is scanned there like the other overlay files. The private one stays private.
- **A bug report waiting for details is closed only after it was asked and nobody answered** (#3089, Added). `.github/workflows/stale.yml` runs in the public repository only, on issues labelled `needs-info` only: a notice after 14 days with no reply, and a close 7 days later. The message says to reply and the report is reopened. A reply resets the count. PRs and confirmed bugs are never touched.
- **`docs/README.md`: one entry page for the documentation** (#3089, Added). One line for each main document under `docs/`.

- **THIRD_PARTY_NOTICES.md is verified, not a draft, and the publish tool refuses an unfinished document** (#3074, Fixed). The draft block at the top of the file survived #2950. The licences were checked against the package pool of v0.94.0 (`pool_debs` in `imagesrv-iso.json`), and the block is now one line naming the version and the date of that check. `tools/git/placeholder-check.sh` stops `publish-to-public.sh` when a file to be published still carries an unfinished-document marker (the list is in the script). Negative control in the PR.
- **The public repository takes bug reports through one bilingual form** (#3076, Added). Owner's decision, 13/11: the public repository accepts bug reports from outside only, through `.github/ISSUE_TEMPLATE/bug.yml`. Each field is in Hebrew with English below it: version, kind of machine, what was expected, what happened, log or screenshot. Blank issues are off, and `config.yml` points vulnerabilities to `SECURITY.md`. The form comes from `docs/public/ISSUE_TEMPLATE/` through `public-overlay.sh`. README and `docs/public/CONTRIBUTING.md` say the same.
- **Security fixes (entered in v0.94.0, first public here).** Nine PRs, summarised without reproduction detail. The public Security Advisory of this release has the severity of each.
  - The first wave of a round writes only the disks the console showed when they were chosen. A drawer swapped between the choice and the next hello fails by name instead of being written (#3017).
  - The secondary-server port caps the request head, the read time and the number of connections before authentication. Several agent-side endpoints are tightened: a source gate, no name taken from the request body, response caps and journal caps per source (#3023).
  - Console and authentication. The MFA login body has the same cap as login. A disabled or deleted user's session cookie does not come back to life. Non-finite numbers in a request are a 400 instead of a server error. The VM viewer re-checks admin rights. Every console response carries security headers (CSP frame-ancestors, nosniff, referrer policy) (#3005).
  - Raw exception text (paths, library errors, command output) no longer reaches the operator in any channel. It goes to the server log, and the operator sees our own sentence (#3006).
  - Agent hardening when reading and writing inside an image: regular files only, `nodev,nosuid` mounts, a strict argument for the ESP repair, and twin-disk and identity checks on capture and cancel (#3013).
  - The DHCP client line gets the hostname only when it is a valid name. The TFTP bootstrap menu is rewritten on every start, so a changed boot-menu password takes effect (#3014).
  - No partition name or partition GUID of a source disk leaves the machine in logs or reports (#3015).
  - The admin guide and the console's deploy-network page warn that deploy-network isolation, port security and DHCP snooping are part of the security model (#3018).
  - iSCSI discovery says openly that CHAP applies to the login only. Before, it took the CHAP secret and dropped it in silence (#3019).

### v0.94.0 (release/v0.94.0, 12/11)

- **release/v0.94.0: one static `?v=` for every include** (Changed). PRs of this release moved `?v=13.08` to `13.09` (#2975, #2983, #2997), `13.10` (#3005, #3011), `13.11` (#3019), `13.12` (#3018), `13.13` (#3012), `13.15` (#3033) and `13.21` (#3045); the release sets every include in `index.html` and `monitor.html`, and every pin in `tests/test_console_static.py` and `tests/test_usb_stick_static_1920.py`, to `13.24`.
- **Schema 17 and 18 in one release** (Changed). #2983 (`boot_marks`, #2830) and #3033 (`users.view`, #3025) each took `SCHEMA_VERSION = 17`; the release keeps 17 = `boot_marks` and makes 18 = `users.view`, with the fingerprint of each in `tests/test_schema_version_1894.py`. The two touch different tables (a new table; a column added through `ADDED_COLUMNS`).
- **A disk failed as slow by the write check is now in the journal** (#2956, Fixed). On 11/11 at work one disk (96 MB/s against 209 for the others) was failed before the stream and got 0 bytes, but the verdict existed only in `session_members.targets_json`; the journal had the CRC warning alone, so the operator could not see why the drawer was not written. `reports.ingest` now writes one `disk_write_slow` line per disk, on the transition (the report repeats every 2 s), in red, and the console reads it in Hebrew: the drawer (the number the cloner's screen shows, from the same `drawer_slots` mapping -- not the ATA port, #2945; "דיסק" with no number when there is no mapping), the model and serial, the measured speed, the agent's own words for the floor or the ratio to the other disks of its kind, and the speeds of the others measured in the wave. Only `slow` is journaled here (`failed` already goes to `disk_failure`). The floor and the ratio are the agent's verdict, not recomputed by the server. No agent or wire change. Metal verification not run.
- **Boot timings from the initramfs (`up=`) are stored and shown in the console** (#2830, Added). `agent/init` has always sent `up=init:..,modules:..,nic:..,link:..,dhcp:..[,gui:..]` with the `agent-net` crumb, but the server only wrote it to the uvicorn log, so without SSH or the server's disk nobody could read it. Now every boot with a valid `up=` is one row in the new `boot_marks` table (seconds of `/proc/uptime` as the machine measured them; NULL = the point was not reached, never 0; a malformed value drops the whole list; a crumb refused by the identity check leaves no marks; the same list twice is one row; 20 per machine, 90 days). `GET /api/console/machines/{mac}/data` returns `boot_marks` (`net_s` = when DHCP answered, `took` = each stage from the previous mark), shown in the machine drawer as "זמני עלייה (initramfs)". No journal event: one line per boot of a whole class would flood it. Time to hello is not in `up=` and is still `boot_step_times`. Static `?v=` 13.08 -> 13.09.
- **udp-receiver asks for a 16 MiB receive buffer, and the round report says what the socket got** (#2994, #2555, Changed). udpcast's receiver sets no SO_RCVBUF on Linux without `--rcvbuf`, so its socket had rmem_default (212992 bytes). On 12/11 cloner 2 dropped 3.94-3.96% of every wave in its own kernel (RcvbufErrors, #2028) and the learned rate stayed at 600m. Every multicast stream now passes `--rcvbuf 16777216`; rmem_max is raised to it once per boot (never lowered) and read back before every stream; a cut is a WARNING in the agent log and `udp_drops.rcvbuf_bytes` in the report (null = not read). **Not measured on the metal yet** -- whether it removes the drops is #2994's measurement.
- **console: every button that waits for the server is disabled with a spinner, and a second click is swallowed** (Fixed, #2868, #2840). `withBusy`/`guarded` in `console-core.js` give `openModalContent`'s ok button (add machine, rename, move, mapping, open round), `vmuAct` and 24 direct `onclick` actions the mechanism `sheet()` has had since #2860: disabled + `aria-busy` + the existing `.btn.busy` spinner, released in `finally`. A double click on "add machine" was two `POST /machines`, on "duplicate rule" two copies, on "verify library" two full sha256 passes. `DEPLOY.busy` stays (the footer sync reads it). No server change. `?v=13.21`.
- **No bidirectional control characters in any `.py` file, and the lint gate holds it** (#199, Changed; no behaviour change). 14,434 RLM/LRM marks in comments and docstrings across 836 files are removed (counted on origin/main b605ee54, 12/11; the first count, 13,909 in 772 files, was on f4104495, 07/11); the 379 inside real string literals (SQL in `server/db.py`, UI text, test data) are spelled as `‏`-style escapes, so every string keeps its value. Checked per file: the AST is identical (docstrings compared with the marks stripped) and the file still parses. `.github/workflows/lint.yml` gains a blocking `ruff --select PLE2502` step over the whole repo including `tests/` (ruff 0.16.6 on origin/main f4104495: 10,663 findings on f4104495, 0 after on the rebased branch -- not re-measured on b605ee54: ruff is not installed on this workstation); `tests/test_lint_workflow_315.py` fails on a raw mark in any `.py`. Nadav's decision, triage of 07/09: remove the characters and turn ruff on.
- Merged in this release without a line of their own here: #2880, #2892, #2894 (mockups and skills, no product code), #2950, #2975, #2997, #3005, #3006, #3009, #3010, #3011, #3012, #3013, #3014, #3015, #3016, #3017, #3018, #3019, #3022, #3023, #3024, #3027, #3033 -- see each PR.

### קודם

- **release/v0.93.0: one static `?v=` for every include** (Changed). #2947 moved the includes to `13.07`, while #2789, #2823 and #2878 changed static files on `13.05`, and #2823 added `console-core.js` on `13.05`; the release branch sets one new value, `13.08`, in `index.html`, `monitor.html` and the tests that lock it (`test_console_static.py`, `test_usb_stick_static_1920.py`). `monitor.js` keeps its own `3.42`.
- **Computer name per disk in a cloner-room round** (#2786, Added; approved mockup PR #2788). The "התחל סבב" window gets a "שמות מחשב" section: a switch (off = the image's name stays), "תבנית" (prefix, optional instructor disk `-INS`, students `-01`…) or "פרטני" (a name per disk), and a live preview in one order: cloner by the number in its name, then SATA 0, 1, 2. The round does not open on a name longer than 15, a character Windows and Linux do not both accept, an all-digit name, or an empty per-disk field; a refused character never enters the field (the short line says why), and the prefix is capped while typing to 15 minus the longest ending of the round. An image with Windows and Linux asks which side gets the name. The server validates the same rule (`hostname_rule.computer_name_refusal`, 400 in Hebrew), stores the name per `(mac, port)` on the round (`room_rounds.hostnames_json`), and hands each cloner `session.hostnames` / `session.hostname_os`; the agent (`name_drawers`, `drawernames.sh`) writes each done drawer's name through the existing `write_hostname` (#62/#89/#107), so a failed disk keeps its number. Names are refused for a round of more than one wave and for a direct round. New settings tab "שמות מחשב": padding (default 2) and instructor suffix (default `INS`). Static `?v=12.99`.
- **A toolbox id that no station module runs is no longer packed into the initrd** (#1104, Fixed). Eight catalog ids -- `testdisk`, `photorec`, `scalpel`, `foremost`, `extundelete`, `magicrescue`, `ddrescue-image`, `safecopy` -- have no runner yet (they need a wizard or a recovery destination, `docs/tools/catalog-runners.md`), so the station never offered them, but `tools_bins_plan` still packed their binaries when the console selection named them. `tools_has_runner` in `agent/lib/tools/toolbins.sh` now keeps them out of the plan, and the builder names them on stderr (`tools: not packed -- no station module runs them yet (#1104): ...`); the selection stays valid (rc 0). `tests/test_tools_packing.py` checks the list against what the real modules show. Implementing or removing the eight is still open.
- **On a direct send the build machine shows the cloners' drawers: the cloner's own card** (#2849, Fixed). At work Nadav stood at the build machine. Its screen showed only the overall read progress: not which drawer, how far each one got, or that drawer 3 failed ("איטי"). Nadav's decision (11/11): the two screens are the same, with the same data. While a `direct_send` runs, the build machine's kiosk reads `GET /room` and writes the cloners' drawers as the cloner's own `drawer=` records, one `cloner_group=` per cloner. The screen is the cloner's card: a bar, percent, speed and "נכתב עד כה" per drawer, a failed drawer in red with its reason, and speed and received bytes at the top. The title says "הפצה ישירה · משדר מהדיסק sda", there is one cage per cloner, and there is no "בטל…" (the build machine cancels none of a cloner's drawers). A room that was not read is said on the screen, never as "no disks". A direct send with no drawer records keeps the progress card as before.
- **The cloner's drawers fit a small screen** (#2849, Fixed). The work's cloner sat behind a KVM switch, which passes no EDID, so it ran at a fallback mode: at 800x600 each drawer lost its "נכתב עד כה" line, and at 640x480 the third drawer was drawn under the status bar. Under 700px of height the cloner's round screen, the local clone's progress and the standby screen now drop the summary tiles (and the partition strip), keep the speed in the label and tighten the spacing. A row under 72px shows "נכתב עד כה" in place of the size and model line, and a row under 48px (more than three drawers) keeps the name, the percent and the bar. 768px and up are unchanged. The hello's probe now carries `display`: the size of fb0 and, for each DRM connector, its status, how many EDID bytes it gave (0 = none) and its first mode.
- **release/v0.92.0: one static `?v=` for every include** (Changed). PRs of this release moved `?v=13.00` to `13.01` (#2873, #2865, #2866) or `13.03` (#2843), with different content behind the same value, and the #2848 branches that did not enter hold `13.02`/`13.04`; the release branch sets one new value, `13.05`, in `index.html`, `monitor.html` and the tests that lock it (`test_console_static.py`, `test_usb_stick_static_1920.py`). `monitor.js` keeps its own `3.42`.
- **A DHCP save whose `restart` failed is shown as diverged, not "serving"** (#2913, Fixed). `_apply_checked` restores the old file when `systemctl restart dnsmasq` fails, but the DB keeps the new record, and `dhcp_diverged` only compared "is the card in the file" -- so a changed range, gateway, lease or `server_ip` looked fine while clients got the old values. The card's block in the active file is now compared with the block the DB renders (`dhcp.interface_block`); a render that cannot run is `dhcp_live.content_checked=false`, not "no divergence". The DB record is not rolled back: the failure text cannot tell a restored file from a failed proxy or deploy step, and the console already shows the "does not match" pill. The proxy file is not compared.
- **"Keep the change" before the deferred apply no longer cancels it** (#2914, Fixed). A risky per-NIC services change (console off on the card the operator is on) writes the DB and the pending marker, and applies one second later so the answer leaves first. `confirm` inside that second deleted the marker, `_apply_later` found none and did not run: the DB said "console off" while the socket kept listening, and the next start dropped it with no 60 s window. The marker now carries `applied_at` once enforcement succeeded; `confirm` before that is `409 not_applied` ("ההחלה עוד לא רצה -- נסו שוב בעוד רגע") and leaves the marker, so the apply still runs. A marker that survives a restart inside the window is marked applied (listeners bind from the DB then). The 1 s delay is unchanged.
- **תוקן: מסך הכשל של מחשב הבנייה נשאר כשהשרת לא עונה** (#2912, תוקן). `gui_state` הוסיף את `fail.state` לקובץ המצב רק אחרי שהצליח לשאול את השרת; כשהשרת נפל או שהרשת נותקה באמצע קליטה, ה-snapshot החלופי של `gui_state_loop` נשא `message=` בלבד, הגואי עבר ל"לא ניתן לאמת את מצב התחנה", והכפתורים אתחל/כבה נעלמו בזמן שהסוכן המתין ל-`fail.action`. עכשיו `gui_fail_local` (`agent/lib/gui/guistate.sh`) מוסיף לאותו snapshot את רשומות הכשל, את גיל ה-hello ותשובתו (שורת המצב אומרת "אין קשר עם השרת") ושורה אחת שאומרת שמצב התחנה לא אומת; `message=` נשאר. לולאת המצב של המשכפל לא שונתה. לא נבדק על ברזל.
- **release/v0.91.11: one static `?v=` for every include** (Changed). Several PRs of this release each moved `?v=12.98` to `12.99`; the release branch sets one new value, `13.00`, in `index.html`, `monitor.html` and the tests that lock it (`test_console_static.py`, `test_usb_stick_static_1920.py`). `monitor.js` keeps its own `3.42`.
- **שיכפול ישיר אינו מופיע עוד בדף האימג'ים כאילו אימג' עולה לשרת** (#2845, תוקן). `GET /api/console/tasks` מחזיר כל סוג משימה, והקונסולה לא סיננה לפי `type`: משימת `direct_send` (`image_id=live_…`, שום דבר לא נשמר בשרת) הוצגה כשורת קליטה בטבלת הספרייה, בלשונית "קליטות" ובמספר שלה, כאזהרת קליטה, ובפעילות בדף הבית כ"קליטת אימג'". עכשיו כל אלה מציגים `type == "capture"` בלבד (`capturesOnly`); שיכפול ישיר נשאר סבב. אין שינוי ב-API. Static `?v=` 12.98 -> 12.99. נבדק רק ב-vm של node, לא בדפדפן.
- **A cloner question names the disk by its drawer, as the cards do** (#2677, Fixed). The split window ("חלוקת המקום"), and the RAID and bad-sector titles, built "דיסק N" from the wire port; on cloner 2 (`drawer_slots` sata:1,2,3,5) port 5 is drawer 4, so the window asked about "דיסק 5" while the card and the title behind it said 4. The row now takes the card's name (`cloner_slot_label` of the drawer of that port, or a local clone's `lc_disk_label`); a port that is no drawer keeps "דיסק <port>".
- **תוקן: העלאת אימג' מהגואי של מחשב הבנייה בשם בעברית נחסמה בשקט** (#2842, תוקן). `build_name_ok` בסוכן (`agent/lib/gui/buildmenu.sh`) דחה כל תו שאינו ASCII לפי הכרעת 30/08, בזמן שהשרת מתיר עברית מאז #327 (`validate_display_name`) — ו-`gui_capture` החזיר 1 בלי הודעה, ולכן לא נוצרה משימה ולא נראתה סיבה. הבדיקה בסוכן נמחקה (השרת הוא הפוסק, לשם אימג' ולשם תיקייה), וסירוב של השרת (400/409) מגיע עכשיו לשורת השגיאה של המסך במילותיו (`detail`). לא נבדק על מסך אמיתי.
- **One deploy-network save = one apply** (#2840, Fixed). On the work server one save became 18 `PUT /net/config/enp2s0/deploy` from the browser and 15 dnsmasq restarts in four seconds. The console window now sends one request per submit (the button is disabled while it runs); the server refuses a deploy save while another one is running (409) and while an earlier network change still awaits confirmation (409, as `/second` already did) — before, the address of the first save was already stored, so later saves skipped that guard and turned the DHCP on over an address the rollback removed a minute later. A failed `ifup` now carries what ifup itself wrote (from the unit's journal), not just "code 1". While a save runs the window says so ("שומר ומחיל…", and after 5 s what the server is doing), and the server keeps answering: the DHCP check, the install completion and ifdown/ifup run off the event loop (`asyncio.to_thread`) — on the work server they froze the whole console, which is why the clicks queued. `?v=` 12.98 → 12.99.
- **A network rollback of a card that had no file goes back to unmanaged, without ifup** (#2855, Fixed). On the work server the #56 rollback of `enp2s0` (the deploy card, no file in `interfaces.d` after an ISO install) deleted the file and then ran `ifup enp2s0` on a card ifupdown no longer knew: "ifup: unknown interface", a failure every time, and the address was not necessarily removed. Now `ifdown` runs while the stanza still exists, the file is deleted, the card's `imagesrv-ifup-` unit is stopped and `ip -4 addr flush` takes the address off, with no `ifup`. Success only from a readback that ran and shows no IPv4 address on the card; one that did not run, or an address that stayed, is a rollback error by name. A card defined in `/etc/network/interfaces` itself (or a main file that could not be read) keeps the old `ifup` path.
- **The first deploy-network save after an ISO install no longer fails with 409 on a disabled card** (#2805, Fixed). `PUT /net/config/{nic}/deploy` ran "who already answers DHCP" (#53) before the code that runs `ip link set up`, so on an admin-down card the probe said `link_down` and the save was refused, while `GET /net/interfaces` promised the save would raise the card. Now, on the deploy path only, a card that was `admin_up: false` is raised (link only, no address) inside that gate, after `confirm` and trunk; the server waits up to 10 s for carrier and probes again on the live link. A failed `ip link`, no carrier, an unchecked probe or a server that answers is a 409 as before, and every refusal before the address is applied sets the card back down. `PUT /net/interfaces/{nic}` is unchanged.
- **A display hotplug right after the headless GUI said `READY` is no longer lost until the 30 s fallback** (#2802, Fixed; follow-up of #2768). `dcui.c` opened the kernel's uevent socket after `READY` and after it had looked for a display, so a hotplug in between waited for the fallback look -- and the public CI of v0.91.8 went red on it (a test sends the hotplug at once after `READY`). The socket is now opened before the display is looked at, and so before `READY`; it is closed at once when a display is there. The test helper that sent the event no longer passes over a send nobody takes (`ConnectionRefusedError` -> the test fails and says the socket is not open). A new test holds the netlink back in the stub and checks that nothing is on stdout while it is held -- decided by the order of two writes, not by load.
- **"Manual -- no time server": a server with no internet can have its time set by hand, and the clock is information, not a problem** (#2765, Added; the zone moves with it, #2764, Fixed). A third source next to automatic and the organisation's server, in the installer (step 5) and in the console (settings -> time source). Chosen, the health row `time_sync` is the new state `info` ("שעה ידנית (בלי שרת זמן) · הוגדרה לאחרונה <date> · MFA codes and the TLS certificate's validity lean on this clock"), not counted in "needs attention", and the server screen (DCUI) shows no orange tag and no problem card. Nothing is measured and the row says so; from 30 days it says how old the time is, still as information. **A time server that was chosen and does not answer is still a warning**, and MFA's skew check does not depend on the source. The settings `ntp_manual`/`ntp_manual_at` are written in one transaction, and only after the clock was set and read back. The installer sets the clock and the hardware clock inside the target's chroot (the time typed on step 5 plus what passed since), and a failure there fails the installation by name -- unlike an unanswered time server. Answers key `clock_manual` (`docs/architecture/interfaces.md`). #2764: the "אזור זמן" field moved from step 4 (server name) to step 5 (server clock), where the time it explains is; the table "איפה השם יופיע" has one right edge for every value, the `Hostname:` line included. Static `?v=12.95` -> `12.98` (round/1108j: one value with #2781, 12.97). The keyboard on the screen (#2778) opens on the clock step's fields too -- the zone, the organisation's servers and the manual time.
- **שרת שעלה בלי מסך מחובר מציג את הגואי במסך הראשון שמחברים, בלי אתחול** (#2768, תוקן). בלי מסך באתחול נוצר `/dev/dri/card0` אבל לא `/dev/fb0` (הגדרת ה-fbdev של DRM ממתינה למחבר; נמדד ב-QEMU עם virtio-gpu ו-`video=Virtual-1:d`), וה-GUI נכשל — עם הסיבה של fbdev בלבד — ומסך הטקסט לא בדק שוב. עכשיו, כשיש כרטיס KMS ואף מחבר אינו מחובר, ה-GUI עולה בזיכרון (`backend` מסוג `none`, בלי לצייר) וממתין לאירוע DRM hotplug מהקרנל (netlink, בלי udev); רק על אירוע הוא קורא את סטטוס המחברים בלי probe (`drmModeGetConnectorCurrent`), וכגיבוי — כל 30 שניות (נדב 09/11: לא כל 2 שניות). המסך הראשון שמחובר מקבל את הפריים באותו תהליך (QEMU: ‏0.32 שנייה מאירוע, 27.5 שניות בגיבוי בלי אירוע; קריאה ‏≈21 מיקרו-שניות CPU). בלי מה לחכות לו, `FAIL` נושא את הסיבה של DRM ושל fbdev יחד, ו-`FAIL` אחרי `READY` הוא הסיבה שמסך הטקסט מציג. גיבוי: מסך הטקסט של אתחול רגיל חוזר לגואי כשחומרת התצוגה משתנה (כרטיס, fb0, סטטוס מחבר) — נבדק על uevent ו-כל 30 שניות; בלי שינוי הוא נשאר. ב-Hyper-V וב-ESXi המחבר הווירטואלי מחובר תמיד והמסלול לא משתנה. ברזל (i915) לא נבדק. ‏round/1108j: הסיבה שהגואי כותב (`FAIL ...`) רגע לפני שהוא יוצא כבר לא אובדת. לפני כן, כשהשורה הגיעה יחד עם סוף הצינור, או כשהפריים הבא נכתב לצינור סגור, מסך הטקסט הציג "graphic screen stopped (exit 1)" במקום הסיבה (`gui._gone`).
- **A capture ordered after a failed one is no longer asked under the old failure screen** (#2743, #2783, Fixed). The hold that yielded to the next task (#1575) left `fail.state`, so the build GUI kept "הקליטה נכשלה" with the shrink question drawn over it, and the failure screen took that question's click and `1` (measured on the real GUI: no `shrink-continue`; Enter left `fail.action=retry` for the next hold). The yield now removes `fail.state` and `fail.action`, a question over the failure screen takes the input first, and the capture banner and the hold's FAILED lines are not written to the console while a screen of ours is up (owner 17/10). The text the physical screen showed on 09/11 is not explained by this: see #2783.
- **A finished write-check of a disk whose hello has no `model` (or no `serial`) is journaled instead of dropped silently** (#2532, Fixed). `learn_models.record_wave` skipped such a disk (`model_speeds.model`/`serial` are NOT NULL) with no trace, so the next round's pre-check showed "not measured" for a disk that had been measured. It now writes `model_speed_dropped` to the journal (machine, round, `dev serial=... N MB/s: no model|no serial`; orange, with a Hebrew label) and still returns only the rows it stored. It does not store an empty model: that would be one "model" for all anonymous disks and would pollute `judge` and the learning round's `_model_base`. A failing journal write no longer undoes the rows already stored.
- **Dead failure reason `bitlocker` removed from the VM-save failure list in `vmupdate.js`** (#2713, Fixed; refs #2713, follow-up of #2712). Nothing in the server or agent sends it as a VM-save reason any more (`DiskError` reasons in `server/vm/` have no `bitlocker`). Static `?v=12.93` -> `12.94`.
- **The clone stick reads `server-sync/` only after it verified it, and the "קישור" (3/6 Gb/s) column is drawn** (#2694, Added; side of the stick of #1920, refs #1920). `usb_sync_load` (`agent/lib/usb/usbsync.sh`, called from `usbclone_main` right after the data partition is mounted and before the boot diag) checks, on RAM copies, that `server-sync/` is a real directory with a `manifest.json` (the server writes it last), that every file is a regular file under 1 MiB, the manifest schema/kind/exact key set, each file's size and sha256, `rates.json`'s schema and every entry, and that `smart_table_version` is what the two tables hash to. Anything wrong is named (file or field) in the log and on the stick's screen notice, nothing from `server-sync/` is used, and the stick keeps its own tables. The server's SMART tables take over (`SMART_KB`) only when the server's version is not older than the stick's; an older server is a finding (the stick's tables stay), and versions that cannot be compared are a failure, not a guess (principle 5). `rates.json` is verified and kept, but nothing on the stick reads it yet. The clone report kept on the stick gets `link_gbps` (the slowest SATA link among the clone's disks: 1.5/3.0/6.0, `null` when any of them could not be read; `clone_link_gbps` in `disklink.sh`), the server's schema accepts it (any other value rejects the record), and the page draws the column between the controller and the size, as in the approved mockup. A record imported before this keeps its `record_id` (the link joins the hash only when there is one). Not stored in the DB; `SCHEMA_VERSION` unchanged. Static `?v=12.90` -> `12.93` (round/1108a: one value with #2708, #2714, #2715).
- **BitLocker follow-ups for local clone and VM save** (#2690, Fixed; refs #1840). Local clone carries the full recovery-key warning in its raw manifest reason and orange done-note. VM save copies matching BitLocker volumes raw, without mounting, shrinking or growing them, and reports the warning per partition. Filesystem changes and unreadable checks still refuse. Removed the obsolete `source_bitlocker` failure-screen mapping; old persisted codes use the generic fallback.

- **`test_the_stream_rate_is_the_bytes_moved_over_the_time_they_moved` no longer depends on the wall clock** (#2689, test only). It slept 0.3/0.5/0.2 s and asserted `0.3 <= stream_s <= 1.5`; in full run 4 of round/1107b (load 35 on 14 cores) the sampler woke late and `stream_s` was 2.6. `time.monotonic` in `server/rounds/sender.py` is now a table keyed by the counter the sampler just read (waiting-for-receivers sample 4.0 s, 1 MB at 10.0 s, 6 MB at 10.5 s), so the test asserts exactly `stream_s == 0.5` and `stream_bps == 10_000_000`; it also waits until the sampler consumed each counter before writing the next. No production change.
- **A RAID server boots without its first disk; the health row and the upgrade marker tell the truth** (#2622, #2624, Fixed; #2623 refs). `/boot/efi` carries `nofail` on a RAID install (single-disk unchanged), so the array no longer stops in emergency mode when the first member's ESP is gone; a missing drive during resync/reshape/repair is red, only `recovery` stays orange; the upgrade recovery marker and the DB snapshot fsync their directory after `os.replace`.
- **A manual install no longer falls back to admin/admin** (#2593, Security). `deploy/setup-boot-server.sh` called `users.ensure_admin(conn)` when no `--admin-pass`/`--admin-pass-fd` was given, so a manual install had a known password until the forced change. It now stops (exit 1, before touching anything) when there is no password and no admin in `<data>/imagesrv.db`; when the DB exists but cannot be checked it also stops rather than assuming. A re-run (`--force`) on a server that already has an admin continues without a password and does not touch that admin. The ISO path is unchanged (`answers.sh` already rejects an empty password). `ensure_admin` itself stays for server start on an empty DB (tests, simulation): an existing admin/admin server stays reachable and keeps the forced-change 403. Docs: `server-install.md`, `interfaces.md` accounts section, the Haifa runbook command.
- **New passwords use 600,000 PBKDF2 iterations, and an older record is upgraded on a successful login** (#2591, Security). `users._ITERATIONS` 200,000 -> 600,000 (OWASP, PBKDF2-HMAC-SHA256); measured on the testrunner (i7-10700, 07/11/2026): 82 ms -> 252 ms per hash. `users.verify` re-hashes a record with fewer iterations after the password matched, with `UPDATE ... WHERE pw_hash = <old>` so a concurrently changed password is not overwritten; `auth_epoch` and `must_change_password` are untouched, a higher stored count is never lowered, a wrong password changes nothing, and a failed write is logged and does not fail the login. No user is locked out: `verify` already read the parameters from the record.

- **`test_room_pace` no longer fails when its two reports straddle a second boundary** (#2637, test only). `_ago(12)` then `_ago(0)` read the wall clock twice and `isoformat(timespec="seconds")` truncates, so a boundary between the calls made the gap 13 s, not 12: 900 MB / 13 s = 69230769 instead of 75 000 000 (nightly 07/11, run 42348661231). The test now reads one clock.
- **The update buttons are named as the console shows them** (#2637, docs only). README, `docs/guides/server-install.md` and `docs/lab/release-signing.md` said "בדוק עדכון", which the console does not have; the buttons are "בדוק עכשיו" and "החל עדכון" under Settings, "עדכונים ותחזוקה".
- **`lab-run-tests.sh` and `lab-work-checkout.sh` have no default lab** (#2637, Changed). `--host` is required, and `lab-work-checkout.sh` also requires `--source` (the repo on the lab). The old defaults, `root@10.10.10.8` and `/root/ImageCtl`, no longer exist and a wrong target looked like a connection problem. Without them: exit 2, a message, and no `ssh` call.
- **"Watch screen" on the monitor page shows the machine again** (#2642, Fixed). #1633 sent `X-Frame-Options: DENY` on every console response, which also blocked the console's own iframe (`monitor.html` inside `console.js`, `iframe.mon-frame`), so the card showed a broken file; "open in a separate window" was unaffected. `server/console/fetch_guard.py` now sends `X-Frame-Options: SAMEORIGIN`: the same-origin iframe loads, a page on another origin is still refused. The console has no other iframe, and its source is a relative path (also for a secondary server's machine, which goes through `monitor.html?node=`). `tests/test_console_csrf_1633.py` pins the header value and the iframe source.
- **The local clone says, on the screen, that the source is in Fast Startup and that the copy is cleaned** (#2069, #2084, Added/Fixed). #2208 already drops the hibernation on the source's RAM view and shrinks it, but the selection screen said nothing, as mockup 09 section 3 (approved 24/10) shows it. Now the measure (`lcinv.sh`) leaves `$LC/measure/<src>.hib` when `ntfs-3g.probe` says 14, and the kiosk bridge (`lcgui.sh`) appends `|hibernated` to `lc_src` only then (a clean, dirty or unreadable source, and an older agent, send the line as it was). The native screen draws, on the drawer that needs the shrink, "המקור במצב שינה — ההשהיה תבוטל בעותק" in front of the shrink line, and in the foot, in orange and not as a refusal, "המקור במצב שינה של Windows. בעותק ההשהיה מבוטלת, והמקור עצמו לא משתנה." A target that takes the source whole gets no note. Also tests for what #2084 fixed without them: `lc_state.c cpy` never cuts a reason inside a 2-, 3- or 4-byte letter (the broken character), and the refusal in the mockup's words stays inside its card at 960x540 and 1024x768 and names no device and no capture. Interface: `lc_src` field 5, `docs/architecture/interfaces.md`. Metal (a Windows boot from the copy, the 870 still HIBR) is not covered.
- **The cloner's wait screen warns when a SATA connection moved, and "זה מכוון" saves it; the agent sends the controller mode and the BIOS** (#2080, refs, mockup 06 section 2, Nadav 25/10). A cloner whose disk moved to another SATA connection, or vanished, since the last power-on (or since the image "this is the right setting" saved) gets `fw_warning` in its hello answer; the wait screen draws an orange frame "החיבורים השתנו מאז ההדלקה הקודמת" with the text and a "זה מכוון" button, and the status bar says so. The button posts `POST /api/v1/agent/firmware-accept` (a registered cloner, only for itself): the image becomes the right one and the journal says "אושר במשכפל <name>"; the agent counts it done only on the server's 200. A cloner's own controller mode is not its warning -- it writes disks for other machines -- so only connections warn there; the console is as it was. **The agent now sends `sata_mode` and `bios`** (`agent/lib/hw/fwimage.sh`, by the PCI class of the storage controllers and DMI): until now the server expected them and nothing sent them, so a controller-mode change was never seen on a real machine. A mode that cannot be said as one value (AHCI next to a VMD) or a class that cannot be read is left out, never guessed. Not done, still on #2080: the image match before a round ("IDE controller, image captured in AHCI") needs the capture to record the source's mode. Not verified on metal: the PCI class on the HP 8100 in both modes, and the screen on a real cloner. See `docs/architecture/interfaces.md` section 41.
- **The result of a local clone -- on the stick, the cloner and the build machine -- is the list of drawers** (#2131, refs, mockup 03 section 1, approved 24/10). `lc_draw_done` (`native-gui/src/screens_lcrun.c`) drew a summary card per target and the source's sentence loose under them. It now draws one list, "יעדים ומקור" with "N מתוך M יעדים הצליחו", a row for every drawer in the machine's order, the source among them as "דיסק 3 · מקור", each row in its own 3px green/orange/red frame (mockup 07) and the drawer's number big at its start, like the rows of the round. Every line the cards said (#2009, #2017, #2196, #1487) is still in its row; a source the inventory has no record of keeps its sentence under the list and gets no invented row. The stick keeps its "הפעל מחדש". New `--png` card `lcdone-usb`. The rest of #2131 part 3 is in flight elsewhere (mockup 09: #2628, mockup 11: #2618). No agent change.
- **The machine disks tab and the machines table follow the 13/10 decision** (#1375, Changed). The "SMART" column in the drawer disks tab shows a chip only when there is something to say -- a warning, a failed drive, a failed previous cloning, read-only, an incomplete write; a healthy drive (`smart: ok`) is an empty cell instead of a constant "תקין". A drive with no SMART reading still says "לא נבדק" (principle 5: not read is not healthy). In the machines table the "דיסקים" column (a count) became "נפח": the marketed size of each drive, identical drives grouped ("2 × 500GB"), a drive with no size reported as "גודל לא ידוע"; the zero-disks findings of #402 are unchanged. The "באתחול הבא" column (#2550) is untouched. Renaming stays in the connections map (#1485, #2001). `static` bumped to `?v=12.79`.

- **A console certificate the server generated before v0.75 is replaced once, with the console address in its SAN** (#1380, Fixed; owner decision 07/11, option A). A server updated from before v0.75 has no `generated.sha256`, so its own certificate counted as the operator's and every start printed "upload a matching certificate". Now a self-signed certificate with CN `imagesrv-console` or `imagectl-console` and no marker of any kind is regenerated when the console address is not in its SAN (never for loopback). A certificate the operator uploaded is never touched: `install_upload` now writes `uploaded.sha256` next to it, and a stale `generated.sha256` also means "not ours". The browser fingerprint changes once (confirm the new certificate); `/me` `tls` shows the new one and the journal row `console_tls_regenerated` carries `old_sha256`, `new_sha256` and `legacy_migration=1`; the old pair stays as `console.crt.prev`/`console.key.prev`. The interserver identity (the SPKI a secondary pins, #740) is a separate identity in `interserver/` and is not touched. If the new certificate cannot be made or written, the old pair is restored, it keeps being served, and the server prints `error: ... still serving the old one` on every start. See `docs/architecture/interfaces.md`.
- **A capture with shrink no longer fails on a 16GB build machine** (#2354, Fixed). When the plan's relocations do not fit the RAM view (metal 03/11, HP-BUILD-2: 27,706 MB to move, 30,860 MB needed, 15,156 MB there), the capture shrinks as far as the memory goes instead of stopping, and the image comes out somewhat larger (owner 04/11, option A; no word in the confirm dialog). `_snap_partial` halves what the plan gives up, measuring each size with `ntfsresize -n` on the view, to within 1GiB, and keeps going down to 1MiB while nothing fits, so a machine with little RAM shrinks a little instead of being refused. The limit follows the machine's own RAM (owner 07/11), nothing is fixed at 16GB: each view's store is (`MemAvailable` - 1GiB reserved for the copy) / views, and a size fits when its relocations M satisfy M + M/10 + 256MB (+128MB if hibernated) <= the store. The same disk shrinks partly at 4, 8 and 16GB and fully at 32 and 64GB (tests). A `MemAvailable` that cannot be read still refuses by name, never assumes a value. A measure that fails, or no memory even for the view's own writes, still refuses by name. The local clone stays refused: its size is what the target holds.
- **Two dead-code removals** (refs #1691, refs #1693, Removed). `openWizard` (`server/static/console.js`) had no caller in production, only its own tests; it is gone with them. `tools/lab/ci-host/codeql-measure.sh` had no reference anywhere; it is gone (the owner's decision of 04/11; `reset-test-server.py` of #1693 is still open). No behaviour change. Static assets `?v=12.80`. The other dead items listed in #1691 are still open.
- **The orange "before start" warning shows while a selected cloner is still waiting to join** (#447, Fixed, refs #447). On metal 07/11 (`room_e35abf2c`, v0.88.0) a round with a single target (drawer 1, the APPLE HDD rejected the night before as "slow -- 8 MB/s") showed `round.precheck = []` until the wave left: `room_precheck.warnings()` skipped machines that had not joined, and the wave starts by itself on the very heartbeat the machine joins (`ready >= remaining`), so with one target there was never a moment with the wave open and the machine joined. The warning existed only in the journal (`room_precheck_warned ... learned=slow_write`). In a round with a selection it is now computed from the moment of selection for the selected drawers of a machine that has not joined yet, from its last hello. A machine that was not selected gets nothing; an old round without a selection still waits for the join. The automatic start is unchanged (#432: a warning, not a block). The learning itself was not lost in the update: a v0.86.1 database opened by v0.88.0 keeps its `model_speeds` rows; `model_speed` (median of other disks of the same model) is `null` for a model with one disk by design, and the slow-write verdict lives in `last_write_check`. No console change: the note already shows whenever the wave is open and `precheck` is not empty. Metal verification still open.

- **The trust model of boot and task is written down** (#2578, refs #2657, Documentation). New `docs/architecture/security-model.md`: what is cryptographically verified today (UEFI Secure Boot up to the kernel, the inter-server mTLS channel, signed release tags, the monitor's challenge-response) and what is not (the initrd and `grub.cfg`, the agent channel on 8080, machine identity, the multicast stream), the network it assumes (four VLANs behind a firewall, the deployment VLAN not routed), what happens when a check fails, and where each gap is handled. README and `docs/architecture/agent.md` no longer say the initramfs "does not require a signature" as if that were a protection. `tests/test_replay_2657.py` pins what the server does today with a replayed agent request: refused once the task or round is closed, accepted inside an open task.
- **Nothing is written to a disk before the server confirms the job is open for this machine, and an image is served only inside such a job** (refs #2657, Security). A hello answer that says "restore" was acted on as it stood: the write check, TRIM and the partition table ran before any evidence that the round was still open, and a round that had ended was noticed only at the stream, with the disk already wiped. Now the station (`do_restore`) and the cloning room (`do_restore_drawers`, the receivers of a direct send included) send the job's first progress report synchronously and go on only on `ok:true` carrying the same `session_id` back (`agent/lib/core/opengate.sh`); anything else -- `not_open`, `not_member`, a member that already ended, an error, no answer, a body that is not JSON -- boots the local disk with nothing written, before the SMART/RAID gates. The server returns `session_id` only for a report it recorded for an active member. `GET /api/v1/images/<id>/manifest|files|readback` now also requires the machine leased on the source address to be a member of an open round or pull of that image (403 `image_fetch_refused`, journaled and throttled like the identity refusals); the GUI restore's console session opens the manifest alone, before its pull. The GUI restore no longer goes on when its pull did not open (it used to erase the disk and fail at the stream), and passes the same gate on its pull id before the disk. With `identity_check` off the image check does not run, as before. Server and initrd are deployed together. Not the channel itself: that is #1076.
- **The console says "המכונה לא הגיעה" after a Wake-on-LAN, and mentions F1 only for a disk it knows stops the BIOS** (#1858, mockup 10, Added). `GET /api/console/machines` gets `never_woke` (`server/learn/never_woke.py`): the machine's latest WoL (#1847) was not answered, it was not seen since, and `max(180 s, 2 x its learned typical time)` passed. `f1` names the drawer, model and reason only when the last hello reported a disk with SMART red because its health check failed or was unread (#2078) -- the same rule as the cloner's idle power-off window (`app_f1_disk`); otherwise `f1` is `null` and no text says "F1" (principle 5a: the server cannot see the BIOS screen). A failed read is `{"state":"unchecked"}`, never "arrived". The machines page shows a "לא הגיע" / "לא הגיע · ייתכן F1" pill (over "דיסק אדום"), an orange banner per machine with "שלח WoL שוב", and an "N לא הגיע" chip in the header; `?v=12.82`. Not here: an F1 *observed and recorded* per disk (the mockup's second source) -- nothing records it yet. Needs metal for the cloner screen (`refs #1858`).
- **`tools/lab/reset-test-server.py` removed** (#1693, Changed). It no longer worked: seeding the admin user failed on the password policy (`הסיסמה חייבת להכיל אותיות`, rc=1, run against a temp data-dir on the testrunner), its default `--data-dir` was the production `/var/lib/imagesrv`, and nothing referenced it. Owner's decision 07/11.
- **The USB clone stick proposes drawer 3 (SATA 2) as the source, or none** (#1874, Fixed). Owner 07/11: the stick chooses the source like the cloner, by the drawer mapping -- SATA 2 is drawer 3 when SATA 0 is drawer 1, and the operator's "מיפוי חיבורים" in the stick's settings (#1959/#2001) moves it. With no disk in drawer 3, or one that is not a source (no table, no system, #1798's cut write), nothing is proposed ("בחרו מקור") and "המשך" cannot start -- no longer "the first disk with a system". `native-gui/src/lc_state.c` (`default_roles`). And, owner 07/11, a stick with no saved mapping now numbers the drawers by the real SATA number, as the cloner (`usbmap.sh` `usb_map_live`, and the window's default in `usbmapwin.sh`): SATA 0 = drawer 1, SATA 1 = 2, SATA 2 = 3 -- an empty SATA between two disks is a drawer too, and the NVMe follow the last SATA with a disk -- instead of numbering the disks it sees in a row (disks on SATA 0 and SATA 2 were drawers 1 and 2, so SATA 2 was never the source). A saved mapping still wins; the settings' default text now says "SATA 0 = דיסק 1".
- **A build machine that loses the network mid-restore waits it out like a cloner** (#2644, refs, Fixed). The single restore ("שחזור לדיסק הזה") streamed over HTTP with curl's own stall ceiling, `HTTP_STALL_TIMEOUT` (120 s, a constant the console never moved), while the cloner's udp-receiver waits `UDPCAST_STALL_TIMEOUT` (300 s, or the console's lost wait + 120, timing.sh). Now the restore's stream takes the cloner's ceiling (`http_get_stream` has an optional ceiling argument; other downloads keep 120): within it the TCP connection carries the stream on from the byte it stopped at, the same request, sha-verified; past it, a visible failure as on the cloner. The restore keeps its stream counter and source rc where the cloner's stream keeps them (`$RUN_DIR/bytes.raw`, `$RUN_DIR/source.rc`), its card's writer (`sr_gui_state`) calls the cloner kiosk's `stream_beep_tick`/`stream_net_record`, and the progress card draws the cloner screen's own red/green bar (`net_bar`, #2067) -- the same tunes, the same bars, the same texts. Not verified on metal (HP-BUILD-2); TCP's retransmission back-off can delay the resume after a long cut (see the PR).
- **The console says "המכונה לא הגיעה" after a Wake-on-LAN, and mentions F1 only for a disk it knows stops the BIOS** (#1858, mockup 10, Added). `GET /api/console/machines` gets `never_woke` (`server/learn/never_woke.py`): the machine's latest WoL (#1847) was not answered, it was not seen since, and `max(180 s, 2 x its learned typical time)` passed. `f1` names the drawer, model and reason only when the last hello reported a disk with SMART red because its health check failed or was unread (#2078) -- the same rule as the cloner's idle power-off window (`app_f1_disk`); otherwise `f1` is `null` and no text says "F1" (principle 5a: the server cannot see the BIOS screen). A failed read is `{"state":"unchecked"}`, never "arrived". The machines page shows a "לא הגיע" / "לא הגיע · ייתכן F1" pill (over "דיסק אדום"), an orange banner per machine with "שלח WoL שוב", and an "N לא הגיע" chip in the header; `?v=12.82`. Needs metal for the cloner screen (`refs #1858`).

- **"נצפה F1 בהדלקה" is remembered per disk, by serial** (#1858, mockup 07/11, Added). New table `disk_f1_marks` (schema 13). An operator marks it ("סמן F1 לדיסק…" on the "לא הגיע" banner, or the row menu of the disks page; "נקה סימון" there), and the server marks it by itself when a machine shown as "לא הגיע · ייתכן F1" comes back (`source: auto`, shown and journaled). A marked disk is red and skipped in cloning like any disk that causes F1 (the 24/10 rule), and says "נצפה F1", not "SMART תקול"; the machine's "לא הגיע" names it as the known F1 disk, and the cloner's drawer shows "נצפה F1 בהדלקה (תאריך)". The same F1 notice as the idle power-off window now also shows in the end-of-task power-off count (#1560); without an F1 disk both screens are as before. Hello carries `f1_observed`; `cdisk=` gets fields 14-16. `?v=12.82`. Needs metal for the cloner screens (`refs #1858`, `refs #1560`).

- **The room's pre-start note names the kind of warning** (#2670, Fixed). Its title was always "drawers in the wave with a suspect link" even when the warning was `learned=slow_write` (#447), so the operator looked for a cable while the disk was slow. It is now "with a suspect link", "with slow writing" or "with a suspect link or slow writing", derived from the warnings (`write_slow` only → writing; any link/CRC sign → link; both → both). `static` bumped to `?v=12.84`. Also (#2662, docs only): the `server/interserver/auth.py` docstring no longer says the interserver TLS is pending; it describes the implemented mTLS 1.3 + SPKI pin + channel binding.
- **Moving an image to a secondary server, and pulling one from it, now starts from the image's ⋯ menu** (#1781, mockup approved 07/11, Added/Changed). "העבר לשרת משנה…" and "משוך משרת משנה…" open one window with a direction switch, the secondary server, and the storage location on the receiving side. A location the image does not fit in is disabled with the numbers (image + 256 MiB + the receiver's system reserve against free space); when nothing fits the start button is disabled and the note says needed / free / missing. A value that could not be read is said as unread, never shown as "fits". The "Transfers" tab of the secondary servers page and the per-secondary "Transfers" tree page are gone; running transfers show in the tasks dock (a pull shows its source and lands on the main) and on the Overview tasks tab, which also cancels a running pull. The inter-site rate cap moved to the servers tab. The server side (`location_id` on push and pull) was already in v0.85 (#2572). UI wording follows the 18/10 rule: "שרת משנה", not the mockup's "סניף".

- **"באתחול הבא" shows the failure screen a machine will get, not "local disk"** (#2682, Fixed; depends on #2681 / #2666). After #2666 a machine whose answer has an unsupported schema, on a distributing card and within the #75 budget, boots the agent's failure screen; the #2550 preview ran `decide()` on the raw answer and said "דיסק מקומי". `boot_preview` now takes the same branch as `hello.make_resolver` (`answer_failure.failed` before `known`/`bootguard`) and asks `answer_failure.peek` -- the read-only twin of `offer` that shares its window (`_cutoff`) and budget (`_within_budget`) and never writes; an unreadable count is `decided: false`. New `kind: failure` and a console case for it (warning, "מסך כישלון"); after the budget the preview says `local`, like the boot. Console `?v=` 12.78 -> 12.92.
- **An answer the boot menu or the agent does not understand ends on the failure screen, not in a silent local boot** (#2666, Changed; owner 07/11, from the #2578 trust-model review). An unsupported `schema` used to turn the GRUB file into a silent `chain_local`, and the agent showed "no work for this machine". Now, when the menu request arrived on a card the server distributes images on (`hello.serves_images` -- today the deploy card only; a distributing classroom card in v2 joins there, for both screens), the server adds `answer_failure_screen` and the menu serves the normal agent entry (`# decision: answer-failure-screen`, clean kernel line, principle 2). The agent's `decide` returns `bad_answer` for any schema other than 1 (an unparsed answer too), and `die_local` shows the reason in Hebrew with "אתחל עכשיו"; nothing is written to a disk. Loop guard: the #75 budget (`bootguard.ATTEMPT_LIMIT`, three servings) counted in `boot_attempts` as `answer-failure` (`server/agent/answer_failure.py`), time-bounded by a 10-minute sliding window; after it, the local disk. Unchanged: a network the server does not distribute on, any doubt, an unverified identity (#2411), the server's own probe, and a server that is off all boot the local disk with no screen. A boot-path change: BIOS and UEFI on metal before a release (#1663).
- **The console draws an end time only when the server's clock is proven synchronized** (#2684, Fixed; depends on #2683 / #1843). `round.forecast`, `/overview.pulls[].forecast` and `/tasks[].forecast` now carry `clock` -- the same evidence the cloner's hello carries (`ctx.clock_trust`, one `ClockTrust`: chrony `synchronized is True`, cached 60 s; a failed or missing reading is `false`). `forecastText` shows "צפוי להסתיים ב-HH:MM" only for `clock === true`; otherwise "לפי היסטוריה: כ-N דק'" (or "כ-N דק' נותרו" from the measured rate). `seconds` and `ends_at` are unchanged. Console `?v=` 12.78 -> 12.91.
- **The cloner's own screen says when the wave is expected to end** (#1843, Added; mockup 07/11, approved by Nadav). Under the big percentage: "צפוי להסתיים ב-HH:MM", then what it rests on, and "כ-N דק' נותרו" stays under it. Three states: the measured rate (the hour is now + the minutes left, "לפי הקצב בפועל"); the rate not measured yet but the server has history of that cloner with the same drawer count ("לפי היסטוריה · הקצב עוד לא נמדד"); no history ("הזמן שנותר טרם נמדד" and "אין עדיין הערכת סיום (אין היסטוריה)" -- never an invented number). The server puts `session.forecast` (`ends_at` epoch, `why`, `clock`) in the hello a running room wave starts a cloner on, from the same estimate as `round.forecast`; the agent passes it to the screen as one `forecast=` record. **No hour is drawn unless the server proved its clock synced** (`clock`, from chrony, cached 60 s; a failed or missing reading is "not proven") -- not even the one computed from the measured rate (#2144). No hour while the stream stands (#2055), during the read-back, or at the end. An older server sends no field and the screen is as it was. The console still shows its hour without checking the server clock (not part of this change). See `docs/architecture/interfaces.md` (`session.forecast`).
- **A disk's set and sticker ("קוריאים · xx-7") now show wherever the disk does** (#1857, mockup 04, Added). `disk_labels` was sent in every hello answer and nothing read it. The cloner screen draws it as a chip beside the drawer's name when idle and as the first words of the size line in a round (`agent/lib/hw/disklabels.sh` turns the answer into `cdisk_label=<dev>|<text>` records, keyed by device like `cused=`; the native GUI reads them). In the console the same text is on the machine drawer's disk boxes, in the tooltip of every drawer, in the red-disks table (so the person knows which disk to pull), on the round's drawer list (`drawer_list[].set_text`) and in the journal lines `disk_failure` / `disk_failure_cleared`, resolved at read time so a disk assigned after its failure is named in the old line too. A disk with no set looks exactly as before; the name is never written to the disk.

- **A BitLocker volume is captured and restored sector by sector, like FOG, Clonezilla and Ghost** (#1840, Changed; Nadav 07/11). Until now a partition carrying `-FVE-FS-` refused the whole capture (#671, #1806). It is now read with `partclone.dd` -- the path every partition without a filesystem we know already takes -- and recorded as `fs: "BitLocker"` (also when busybox's blkid says `unknown`): never shrunk, never grown (`_mark_expandable` skips it), never mounted (`used_bytes: null`), role `data`, and its stream carries a sha256 that the restore checks like any other. A target smaller than the layout is refused by check 2.7 before a byte is written, and the refusal now says the volume is encrypted and copied as it is. The key stays in the source's TPM, so on another computer the copy asks for the recovery key -- and the operator is told: before the first byte the capture's target carries "מוצפן ב-BitLocker: יועתק כמו שהוא, בלי כיווץ; במחשב אחר יידרש מפתח שחזור" (orange to its done, also in a direct send's second pass); the console shows a "BitLocker" tag in the library and the drawer, an "הצפנה" row, and the same sentence in the round window's orange line (`bitlocker` in `GET /api/console/images`); a restore's done is orange with the same words in past tense. The hostname (`code: "bitlocker"`) and the drivers (`skipped`/`bitlocker`) never touch the volume and say they skipped it because of the encryption; the UEFI boot entry (#433) is still written, it sits on the ESP. A BitLocker check that could not run still refuses before a byte. Static assets `?v=12.79`. Not verified on metal with a real BitLocker disk.
- **Static assets on one `?v=12.90` in round/1107c** (#1858, #2670, #1781, #1857, #1840, #404, #1920, #2684, #2682, Changed). The nine console PRs of the round bumped the includes on their own branches to 12.82, 12.84, 12.83, 12.85, 12.79, 12.86, 12.87, 12.91 and 12.92; every include in `index.html` and `monitor.html` is now on one value, 12.90. None of these branches was released, so the number only has to differ from main's 12.81 and be the same everywhere. `monitor.js` stays `?v=3.42`.
- **ISO tab in the images page: upload, list and delete ISO files** (#404, Added; mockup approved 07/11). A fourth tab "ISO" (admin only, main server only) with the same shape as the images: folder tree, search, table (name and original filename, size, short sha256 with the full one on hover, uploaded, a red delete in the row menu, no typed word). `POST /api/console/isos/upload` takes the file as the body and lets it in only after the server proved it is an ISO 9660 image (descriptor at offset 32768; a ZIP, tar or any other file is refused in the open with what it looks like, and nothing stays on the disk) and that the sha256 read back from the disk equals the one computed on the stream; an optional `X-Expected-Sha256` header must match too. Same-name, space-reserve and non-admin refusals are covered. Files live in `<images>/isos/<id>/` (`image.iso` + `iso.json`) and never appear as images. **Booting a machine from an ISO is not built:** how the server serves an ISO on the network (memdisk, iPXE or HTTP) and how that lives with Secure Boot is still undecided, so there is no boot button and no network-boot column. See `docs/architecture/interfaces.md` section 46. `static` bumped to `?v=12.86`.

- **"דיסק-און-קי" page under Infrastructure: the clone stick plugged into the server, and a two-way sync** (#1920, Added, mockup approved 07/11, refs #1914). Admin only (deploy gets 403 and no tree node); the stick is found by positive evidence only (label `IMGSRV_DATA`, FAT, USB bus, the `IMGSRV_USB` sibling, not mounted) and named by its USB serial, never `/dev/sdX`. Looking is read-only; nothing is written until the operator approves one summary ("סנכרן"): the stick's `reports/clone-*.json` come in under a hard schema (a serial or SMART key rejects that record by name, the rest still go in, once each, marked source=usb with the stick's id), and `server-sync/` (rates per computer model, the SMART table, a manifest with sha256) goes out and is read back before the result says ok. `reports/` and `settings.json` on the stick are never touched. The download button of #1914 moved here from Settings. New tables `usb_records`/`usb_syncs` (`SCHEMA_VERSION` 13). Open: Hyper-V has no USB passthrough and a disk handed to the guest as SCSI is refused by name; the stick-side code does not read `server-sync/` yet.
- **The cloner's wait screen opens a table of every SMART attribute, in Hebrew** (#1859, Added; mockup approved 07/11). Under each drawer that has one: "כל מאפייני ה-SMART (N) ›" and an S key; a click on the drawer or S opens a drawer from the side (600 px, 16 rows at 1024x768; Tab the next disk, the arrows and the wheel scroll, Esc closes). Read only, and the disk's colour is not touched. The Hebrew name sits beside the number; an attribute the tables do not know is shown by the name the vendor reported with "לא מוכר", never as fine. "קריטי" is a neutral tag; orange is the row background and a tag only where the agent judged orange (5/187/10/196 over 0, a worn SSD -- the 20/10 rules); "בסף היצרן" is **red** (Nadav: "if I have to press F1, it is red" -- the mockup drew it grey). The "עלה" column exists only when the server compared with a previous round: new `smart_rose` in the hello answer (`[{"serial", "rose": {"5": 3}}]`, only for a disk that has a previous round); without it the banner says "אין עדיין השוואה" (or that the answer was not read), never "יציב". New state records `csmart=` / `csmartrow=` (`agent/lib/smart/smarttable.sh`, written on the idle screen only -- a table during a round is not built). See `docs/architecture/interfaces.md` (#1859) and `native-gui/README.md`.

- **The console shows what each machine will receive on its next boot** (#2550, Added). A new "באתחול הבא" column in the machines table and a card at the top of the machine drawer: task, open round, wait screen, auto-boot, or the selection menu, with the reason from the server's own decision code (`# decision:` in the GRUB file) -- e.g. "the boot-loop guard fired: sent to the agent 3 times in a row". One read-only call for the whole room, `GET /api/console/machines/boot-preview`; refreshed automatically every 10 s on the machines page, with a "בדיקה מחדש" button in the drawer. It runs the real decision (`hello.build_answer(read_only=True)` -> `bootguard.peek` -> `grub_menu.decide`), so it cannot disagree with the boot, and it changes nothing: no boot-attempt counter, no loop guard, no `net_seen`/hello, no `unknown_mac` journal line, no round started or advanced. What the server could not check is shown as "לא ידוע", never guessed (principle 5). Not in the kiosk allowlist: a `deploy` user is still refused by the console gate (#1073). See `docs/architecture/interfaces.md` section 45.

- **Active sessions screen for admins** (#2552, Added). A new "Sessions" tab under "Users and permissions" (admin only, main server only) lists who is signed in to the console: user, address, since, last activity and a short session id. `DELETE /api/console/sessions/{sid}` revokes one session and the browser gets 401 on its very next request (`auth.revoke_hash` into `revoked_tokens`, the same mechanism as logout); an open monitor bridge on that cookie is closed with it. "Sign out of all devices" (the existing `POST /sessions/revoke`) is offered per user and for yourself, with a warning that your own session closes too. No typed confirmation (a red button), the current session cannot be revoked from the list, and a deploy user gets 403. `GET /sessions` gained `sid`, `role`, `current` and `monitor`; the token and the full hash never leave the server (`sid` is the first 6 hex characters of the sha256). Every revoke writes a `session_revoked` journal row (who, whose, from where). `static` bumped to `?v=12.78`.
- **Automatic learning and SATA wiring advice** (#2028, mockup approved 06/11). A new switch next to the automatic rate, off by default, learns from the next 2 real clonings; nothing is written to a disk just to measure. If both are not good evidence nothing changes and the screen says "not learned". The write floor and the multicast opening rate can only rise, by at most x1.5 per run, each with previous and new value, the number behind it, and a revert; a manual change wins. Agent-reported write speeds count only for a registered machine on the wave's roster, within what its link and model allow, and the floor never exceeds the stream the server measured itself. New machines get a learning offer. The connections map shows link, CRC and advice for the model from 2 machines and 5 clonings, and flags a single suspect port with no threshold; the advice never changes a mapping. Admin only.
- **The database backup is daily at the hour the operator chooses, and right after an image is uploaded or deleted** (#1956, Changed). It was hourly and kept 30 days. The timer now ticks every minute and `backup_job` decides: the chosen hour (`backup_time`, default 02:00), an image event from the journal, or a destination not backed up to yet. Image events inside 15 minutes of the last attempt are one backup. A running round makes it wait, and no day is skipped. Backups keep to a reserved size (`backup_reserve_gb`, default 5 GB, at most 20% of the drive): the oldest goes first, never an image, and the new backup, the last good one and one from each of the last three days are always kept. If those do not fit, nothing is written, nothing is deleted, and the health row is orange with the reason. "Stale" on the health page moved from two hours to 26. New: `POST /api/console/backup-policy`, the console row "מדיניות גיבוי המסד", and step 6 "גיבוי" in the native installer (nine steps) with `backup_time` and `backup_reserve_gb` in the answers file.
- **SMB and NFS are no longer backup destinations** (#1956, refs #1965, Removed). Backups go to `<data_dir>/backups` on the server's drive, or to a mounted folder; there is no "not chosen" any more. A server that saved a share backs up to its own drive at once. The saved setting, the fstab line, the credentials file and the share stay as they are, with an orange notice, until the operator chooses a destination. Nothing is deleted from the share.

- **"Every machine of the model" in the connections map works on a machine whose DMI `product_name` is empty or generic** (#2001, Fixed). `server/disks/model_slots.py` took the model from `product_name` alone, so the HP i5-4590 of 22/10 (name not read) could only save "this machine". Without a usable `product_name` the model is now `"<sys_vendor> (board <board_name>)"`, only when both are usable; those two fields are already in the hello's `dmi`, so no field is added. `product_family` and the PCI subsystem id are not collected, so they are not a source. A generic value (`To Be Filled By O.E.M.`, `Default string`, `System Product Name`, ...) is never a key, in `product_name` too: it merged different machines into one model, and a wrong drawer map writes to the wrong disk. With no usable source there is no model ("the machine reported no model", save for this machine only). A map stored earlier under a generic name no longer applies to any machine. The USB stick's `usbmap.sh` still keys by `product_name` only.
- **A round member the server gave up on is shown as an error, not as progress** (#2603). `roomCardState`/`clonerState` (cloner card, room page), `machineState`, the session member row and the group's "needs care" count handled only `failed`, so a member with `state: "lost"` (`room._mark_lost`, #450; `machines[].state` in `GET /room` and the session members) showed "connected 63%". It now shows "אבד הקשר בסבב — לא ידוע אם נכתב" in red (principle 5). The native GUI was not changed. Static assets `?v=12.78`.

- **"Every machine of the model" in the connections map works on a machine whose DMI `product_name` is empty or generic** (#2001, Fixed). `server/disks/model_slots.py` took the model from `product_name` alone, so the HP i5-4590 of 22/10 (name not read) could only save "this machine". Without a usable `product_name` the model is now `"<sys_vendor> (board <board_name>)"`, only when both are usable; those two fields are already in the hello's `dmi`, so no field is added. `product_family` and the PCI subsystem id are not collected, so they are not a source. A generic value (`To Be Filled By O.E.M.`, `Default string`, `System Product Name`, ...) is never a key, in `product_name` too: it merged different machines into one model, and a wrong drawer map writes to the wrong disk. With no usable source there is no model ("the machine reported no model", save for this machine only). A map stored earlier under a generic name no longer applies to any machine. The USB stick follows the same rule (#2602).
- **The clone stick keys its connections map by the same model as the server** (#2602, Fixed). `usb_model` in `agent/lib/usb/usbmap.sh` took the DMI `product_name` as it was, so on boards that left it unfilled (`To Be Filled By O.E.M.`, `Default string`, ...) every such machine shared one key and a drawer map saved on one was put on another, and a machine with no `product_name` (the HP i5-4590 of 22/10) kept no per-model map. It now applies `model_key` of #2001: a generic value is never a key; without a usable `product_name` the key is `"<sys_vendor> (board <board_name>)"`, only when both are usable; otherwise there is no model and nothing is saved ("לא זוהה דגם המחשב"). A map already saved under a real `product_name` keeps its key, byte for byte. The generic list is one list on both sides, and `tests/test_usb_model_key_2602.py` runs the stick's shell (busybox ash) and the server's function on the same inputs. No field is added: `settings.json` still holds `{model: [...]}`. A map an older stick saved under a generic name no longer applies.
- **Relative times and countdowns use the Hebrew singular** (#1503, refs #1503). The cloner's waiting screen printed "השרת ענה hello 200 · לפני 1 שניות"; it now says "לפני שנייה" (and "לפני 2 שניות", "לפני דקה", "לפני שעה"). The words come from one new dependency-free file, `native-gui/src/he_time.c`, used by the waiting screen, the build machine's server pill (which used the cut-off "שנ'"), and the power-off/reboot countdowns ("בעוד שנייה", "דקה בלי פעילות"). The console's `ago`/`sinceSeconds`/`probeSkewText` and the update screen no longer print "לפני 1 ימים"/"1 שעות". Console static version 12.78. Not verified on metal: the other screens listed in #1503.
- **The ISO README no longer reads as if the BIOS menu waits for Enter** (#2097 item 7, docs only). `tools/iso/README.md` keeps its 19/09 measurement ("no timer") but now says it predates #1190: GRUB boots at `timeout=0` and isolinux at `timeout 1`, straight into the installer, and the disk is erased only after "אני מבין" and "החל התקנה". No code or ISO change; the other open items of #2097 wait on Nadav's decisions (see the issue).

- **The console drops ten of Impeccable's 14 "generated UI" tells** (#2061, refs). Toasts, the mapping note and the VM-update note keep a full hairline border instead of a thick one-sided stripe; KPI tiles and disk tiles carry their state colour on the whole border instead of a rounded-card top bar; the LED dots lose their coloured glow; the task-panel header, status bar and monitor bar get 3-6px of vertical inset. `tests/impeccable_baseline.json` shrinks from 14 to 4 entries (the gate fails on a stale one). Left open, needing a mockup or a decision: the navigation selection stripe, the indeterminate progress bar (marquee + stripes). `?v=` 12.78.
- **The cloner screen says the network is gone, and when it is back** (#2067, refs, mockup 08 approved 24/10). While no byte has come for 30 s the cloner shows a red bar over the hero, "הרשת נותקה לפני X שניות", with the receiver's own ceiling ("אם הקשר לא חוזר תוך N דקות, הסבב ייכשל"); when the bytes move again, a green bar "הרשת חזרה אחרי X שניות" for 5 seconds and then it is gone. The agent keeps the last-byte and outage moments next to the tune's own transitions (`roundbeep.sh`: `stream_net_record`, the `net=` state record), so the bar and the tune never disagree; a moment that was not saved is left out of the sentence, never guessed. **"המקור נכשל" is heard when the source cannot be read**, not at `lc_end` after the RAM views are released (`source_failed_beep`; `lc_end` skips its own tune once). **The console's switch is named for what it governs**: "צלילי המשכפל ומחשב הבנייה" (was "צליל בסיום סבב שיכפול"; the USB stick's own switch keeps its label). Still open on #2067: a network cut during a restore on the build machine (no state loop there), and the metal check by ear (cloner 2, HP-BUILD-2).
- **The check after the write is seen: green fills the drawer's blue bar** (#2046, mockup 11, Nadav 24/10). `fanout --readback -p <file> <base> <total>` keeps a one-line counter (`"<done> <total>"`, at most every 250 ms, and once more at the end - also after a failure, where it holds the byte the check reached); the agent gives each partition the bytes of the ones before it and the disk's whole (`rbprog.sh`, `targets/<dev>/rb_prog`), the kiosk carries it as `drawer_rbp=` (cloner) / `lc_rbp=` (local clone) with the verdict and the check's own rate, and `rb_mode=`. The cloner's round screen and the local clone's progress screen draw the blue write bar whole with the green over it from the start side and the check's percent; the title is "בודק את הדיסקים — X מתוך Y אומתו" ("נבדקו" in a sample), the big number is the mean, "קצב בדיקה" is the sum of the rates. A check that stopped keeps its green where it stopped, with a red tick and "הבדיקה נכשלה ב-N%"; "אומת" is said only for a disk whose every chunk was read back (a sample stays "הושלם · נבדק"). Display only: no verdict reads the counter; a file that cannot be written, a half line or a disk with no list gives no bar - never a made-up 0% or 100%. In sample mode chunks nobody drew are passed, not read, so the bar runs fast through them; in a local clone the total is an estimate (used_bytes) and the bar is clamped to it. Not measured here: whether the check really runs on all disks at once on metal (the code starts every disk's read in the background together; `readback: N disk(s) in Ns` in the log is the timing).

- **The cloner screen no longer says "checked" over a drawer the agent did not check** (#2613, principle 5). A finished drawer whose read-back verdict is `unverified` (the image has no check list yet) read "הושלם · נבדק"; it now reads "הושלם · לא אומת" in orange, a `sampled` one "הושלם · נבדק במדגם", and one with no verdict at all just "הושלם" - no claim about a check. `list_not_fetched` (#1907) keeps its own words and `verified` keeps the green "אומת" (#2046). The screen's closing sentences follow the rows: "כל היעדים נכתבו ונבדקו" / "N דיסקים נכתבו ונבדקו" only when every finished drawer was `verified` or `sampled`; with an `unverified` one the hero says "כל היעדים נכתבו — אבל לא כולם נבדקו מול האימג׳.". Screen-side only - the verdict already reaches the screen in `drawer_rbp=` (#2046), so this builds on that branch. The console already said "לא אומת" in orange (`readbackLine`).

- **iSCSI discovery no longer writes records in a name the target chose, and the CHAP secret is off `argv`** (#1983, security, Low). `iscsiadm -m discovery` without an op writes a record as root for every name SendTargets returns; measured on Debian 13's open-iscsi 2.1.11 against a loopback stub, a `../` name landed outside the DB (CVE-2026-44943, open in trixie). Discovery now runs with `-o nonpersistent`. The node record is created in `iscsi_login` (`-o new`, only when none exists), after the IQN and the portal pass the same check as the console's input, and a step that fails stops the login instead of being ignored. A name that is not an IQN is dropped and counted: a warning in the wizard and in the test, and a named failure when none is left. The CHAP secret goes straight into the record file, because `-v` is iscsiadm's only channel and `ps` shows it to every local user. Not yet run against a real target or a real login (#2559). `static` bumped to `?v=12.80` (after #1375's 12.79), and to `?v=12.81` with #1691 in round/1107b.
- **The lab gate reads the whole initrd, and the stations' boot payload goes through it** (#1630, security, Low). `tools/iso/initrd-lab-gate.sh` listed the initrd with `cpio -t`, which stops at the first archive's `TRAILER!!!`; the kernel unpacks every archive after it, so lab access in an appended archive (one gzip member, a second gzip member or a second zstd frame) passed. The new `tools/iso/initrd-list.py` reads newc entries to the end of the stream as the kernel does, and a stream it cannot read to the end is a failed inspection, never a clean one. `tools/boot-payload-build.sh` (firstboot, the update button, the ISO's prebuilt payload) now runs the gate on every built `.new` before it replaces anything: lab access without `--lab` stops the build, the previous payload stays as it was, and no `.new` is left in the HTTP root. Only `build-iso.sh --lab` passes `--lab` on.
- **The console (:8081) refuses requests from other pages** (#1633). Every console response carries `X-Frame-Options: DENY`. A POST/PUT/PATCH/DELETE whose `Sec-Fetch-Site` is present and not `same-origin`/`none` gets 403 (`cross_site`). Without `Sec-Fetch-Site` (an older browser), an `Origin` that is not exactly the request's scheme, host and port, or `Origin: null`, gets 403 (`cross_origin`). A state-changing request must carry `application/json` (or `application/x-tar` for the image and driver uploads), or no body at all; otherwise 415 (`body_type`). A request with neither header passes: agents, curl and the interserver channel are not browsers and do not carry an administrator's cookie. The agent port (:8080) is unchanged. CSP stays in #2493.

- **The server's own failure screens are on the GUI** (#1741, refs #2517). A failed first boot (S3), the live installer's hold with a framebuffer (S9) and the server rebooting or powering off (S7, `imagesrv-shutdown-screen@.service`) now draw the one failure screen, display-only (`--no-input`), with the reason in Hebrew and the log's English line under it (`installer/lib/failgui.sh`). The header says "שרת ImageSrv" / "התקנת שרת" instead of a station's machine, server and agent. Without a framebuffer, a GUI binary or a drawn frame, the text stays the screen. #2517: the live installer copies its GUI binary to the target's `/usr/bin` (an ISO's `/opt/imagesrv-src` had the sources only, and the installer GUI failed with 203/EXEC), the unit is `Type=exec`, and firstboot says the installer GUI is on tty1 only once it drew a frame (`IMAGESRV_GUI_BEAT`); otherwise `installer-gui-failed`.
- **A checkpoint can no longer overlap a commit** (#1831). Debian 13's SQLite 3.46.1 carries the WAL-reset bug (sqlite.org/wal.html section 11) with no backport. Writers no longer checkpoint on their own (`wal_autocheckpoint = 0`); `db.checkpoint` is the only checkpoint, it runs every 200 commits, and it holds SQLite's write lock for its whole duration.
- **The server ISO installs again** (#2520). From v0.82.0 to v0.84.1 the pool carried apt 3.0.4 (trixie-security) next to the netinst's apt-utils 3.0.3 (`Depends: apt (= 3.0.3)`). debootstrap installed both, and the engine stopped at 45% (`apt-install`). `make-pool.sh` now downloads debootstrap's base (Priority required + important) at the same snapshot, in both ISO and `--security` mode. `build-iso.sh` stops when a versioned dependency in the merged index cannot be met (`tools/iso/check-pool.py`). With `--netinst` (the same ISO build-iso uses), `make-pool.sh` also downloads the newer version of every netinst package that pins a sibling it lifted (vim 2:9.1.1230-2 pinned vim-common, lifted to +deb13u1 by the base). The release check is now a full install to `state=done` (`tools/iso/install-test.py`, CONTRIBUTING "גרסאות"), not the first screen.
- **dnsmasq's late-NIC retry (#1816) reaches every server, not only a fresh install** (#2174). `imagesrv-server` reconciles `dnsmasq.service.d/imagesrv-restart.conf` with the tree's copy on every start (Linux only): equal and listed in systemd's `DropInPaths` = nothing; missing, different or not loaded = atomic write + `daemon-reload` (not a dnsmasq restart), checked again in `DropInPaths`. A server updated by an old tag's `server-upgrade.sh` (#2350) now gets it at its first start. A failure is a journal line (`dnsmasq_dropin_failed`) with the reason; the server still starts. The firewall is untouched by design (#1074).

- **The first hello no longer waits for the full hardware probe** (owner 19/10). MAC and the disk list remain synchronous for the server's first decision; the bounded background probe is sent in a later hello. Its marker expires, and a probe that exceeds 35 seconds is killed and logged rather than silently treated as complete.

- **SMART health probes no longer wake sleeping disks** (owner 19/10). Agent health checks pass `-n standby`; smartctl's standby exit is stored and shown as `not checked (standby)`, never as healthy.

- **Initramfs uses zstd only when the target kernel explicitly supports it** (owner 19/10). Builds targeting a kernel with `CONFIG_RD_ZSTD=y` use reproducible `zstd -19 -T0`; an unreadable or non-enabling kernel config visibly falls back to gzip. ISO inspection now selects gzip/zstd from magic bytes and rejects unknown input instead of guessing.

- **DHCP answers are accepted on one-second retry boundaries during boot** (owner 19/10). Both udhcpc calls (the #2040 server-identity probe and the #1949 bind) now make 12 one-second attempts instead of four three-second attempts each: the same 12-second no-answer ceiling, while a lease available on the second attempt is used about two seconds sooner.


### נוסף
- **מסך אתחול לשרת** (#1569, נדב 17/10: "אין מסך שחור ואין מסך שאינו גואי"). מתחילת האתחול ועד שה-DCUI עולה, מסך השרת מציג את סמל ImageSrv ו"מתאתחל…" — אותה תמונה שמחשבי השיכפול מציגים — במקום מסוף שחור עם סמן. באתחול הראשון אחרי ההתקנה, שורות ההתקדמות באנגלית של firstboot ויומן הבקשות של האשף כבר לא נכתבים מעליו. שגיאה עדיין מוצגת: הודעת `[FAILED]` של systemd, שגיאת קרנל חמורה וכישלון firstboot מצוירים מעל התמונה.
- **משכפל שמחכה ל"כבה" מוצג "דלוק — מחכה ללחיצה: יש דיסק עם בעיה", וכפתור "כבה" בקונסולה** (#2031, הכרעת נדב 22/10). אחרי #2024 משכפל עם דיסק בעייתי נשאר דלוק בלי hello (בכוונה — כדי שלא ייראה פנוי לגל), והקונסולה הציגה אותו "לא מחובר". עכשיו הוא שולח כל 15 שניות דיווח קל בנתיב ה-progress (`state: held`, עם הדיסקים הבעייתיים והסיבה), והקונסולה מציגה אותו דלוק — בדף המחשבים, בכרטיס המשכפל ובחדר — עם "דיסק N — הסיבה" וכפתור "כבה" (‏`POST /machines/{mac}/poweroff`, רק למשכפל מוחזק), שהכיבוי שלו מגיע בתשובת הדיווח הבא ועובר דרך הסוכן (‏WoL נדרך קודם). הוא עדיין **לא פנוי לסבבים**: אינו מצטרף לגל, ובגל שממתין לו הוא מופיע "חסר — מחכה ללחיצה" עם "כבה אותו", וגל שלא חזר אליו נכשל בשמו עם הסיבה האמיתית ("מחכה ללחיצה על 'כבה'") ולא "לבדוק חשמל ו-WoL". הכול ירוק — כמו קודם, 20 שניות וכיבוי, בלי דיווח.
- **מקור שעה ניתן להגדרה, מצב סנכרון גלוי ושרת NTP לכל כרטיס** (#1773). ‏chrony מחליף את timesyncd: בהגדרות ← כללי בוחרים בברירת המחדל של Debian או עד ארבעה שרתי ארגון ורואים שרת/סטייה; בשירותי הכרטיסים מדליקים UDP 123 רק ברשתות הרצויות, עם `allow` וכלל nft תואם. כבוי פירושו `port 0` — שעון השרת ממשיך להתעדכן אך אינו נותן שעה לתחנות.
- **זמן אתחול לכל מכונה ולכל שלב — שלב שהתארך, ולפני/אחרי עדכון** (#1848). כל מעבר בשביל הפירורים נמדד
  בשעון השרת (`boot_step_times`). שלב שהתארך פי 2 מהבסיס של המכונה (ולפחות 3ש') — יומן `boot_step_slow` ושורה
  כתומה במסך הבריאות, בשם השלב; ושורה "זמן אתחול לפני/אחרי <גרסה>" שמשווה כל שלב לגרסה הקודמת.
- **מכונה שלא מתעוררת מ-WoL — זיהוי, שליחה חוזרת מוקדמת והתראה** (#1847). כל הערת WoL למכונה כבויה
  נמדדת עד הסימן הראשון שהיא חיה (פירור האתחול או hello). לכל מכונה: שיעור הצלחה וזמן התעוררות טיפוסי;
  אחרי פי 2 מהזמן הטיפוסי — שליחה חוזרת אחת; 3 הערות ברצף בלי תשובה — "לא מתעורר מ-WoL — BIOS/כבל" ביומן
  ובמסך הבריאות.
- **אבדן חבילות ברשת ההפצה מול הבסיס שלה** (#1846). בסוף כל גל השרת משווה את השידורים החוזרים של
  udp-sender לחציון הגלים הקודמים **על אותו כרטיס, באותו מספר מגירות ובאותו קצב**. פי 3 ומעל 0.1% — יומן
  `net_loss_jump` ושורה כתומה במסך הבריאות: "האבדן ברשת ההפצה קפץ — סוויץ'/כבל/IGMP". פחות מ-3 גלים כאלה —
  "לא נבדק". לא חוסם דבר.
- **"איטי מהרגיל לדגם" — מהירות לכל דגם דיסק** (#1845). השרת שומר את ה-MB/s של בדיקת הכתיבה שלפני הזרם
  לכל דיסק בכל גל (`model_speeds`), ומשווה כל דיסק לחציון של **דיסקים אחרים** מאותו דגם: מתחת ל-70% — כתום,
  עם המספרים. בלי בסיס לדגם (פחות מ-3 מדידות או מ-2 דיסקים) — רק הסף המוחלט, שלא השתנה. המלצה, לא חסימה.
- **"כנראה כבל/מחבר" מול "הדיסק עצמו" — לפי ההיסטוריה של כל חיבור SATA** (#1844). בסוף כל גל השרת
  רושם לכל חיבור של כל משכפל את עליית ה-CRC, את מהירות הקישור וכשל כתיבה בסיבת כבל (`port_history`).
  אותו חיבור בעייתי עם ≥2 דיסקים שונים — "כנראה כבל/מחבר, לא הדיסק"; אותו דיסק בעייתי ב-≥2 חיבורים —
  "הדיסק עצמו". כתום באזהרות שלפני "התחל" וב-tooltip של המגירה — המלצה, לא חסימה.
- **זמן סיום צפוי לפי היסטוריה — לגל, למשיכה ולקליטה** (#1843; נדב 19/10: "שילמד את המחשבי שיכפול ובניה").
  לגל: לכל משכפל, מהגלים הקודמים שלו **עם אותו מספר מגירות** — חציון ה-goodput והשלבים הקבועים שנמדדו
  (‏`server/learn_eta.py`); לגל פתוח "לפי היסטוריה: כ-X", לגל רץ "צפוי להסתיים ב-HH:MM", והקצב שנמדד עכשיו גובר.
  משיכה וקליטה — מהקודמות של אותה מכונה. בלי היסטוריה: "אין עדיין הערכת סיום", לא מספר. לא חוסם דבר.
- **אימות קריאה-חוזרת מהדיסק אחרי הכתיבה, ומתג "אימות מלא" בהגדרות** (#1805, #1864; נדב 19–20/10; CLR-01/02/03).
  עד כאן מגירה הייתה "נכתב" לפי ראיה מה**זרם** — ואיש לא קרא את הדיסק. עכשיו, אחרי שכל המחיצות נכתבו ולפני ההרחבה,
  כל דיסק: fsync + `blockdev --flushbufs` לדיסק ולכל מחיצה (קוד יציאה נבדק), ואז `partclone -c` נותן את מפת הבלוקים
  של מערכת הקבצים **שנכתבה** ו-`fanout --readback -x` (חדש, C) קורא אותם ב-`O_DIRECT`, ‏XXH64 לכל 4MiB של זרם הנתונים —
  מול הרשימה שהשרת מחשב מהאימג' (`server/readbackhash.py`, ‏`<file>.readback` ליד קובץ המחיצה). **ברירת המחדל: מדגם** של
  כ-5% (התחלה, אמצע, סוף ואקראי לפי זרע שנרשם ביומן) — "נבדק במדגם", לעולם לא "אומת"; **"אימות מלא אחרי הכתיבה"**
  בהגדרות (‏`readback_full`, נבדק בשרת) — כל החתיכות, "מוסיף כ-5 דק' לסבב של 4 מגירות" (נמדד: sha256 של הכול היה ~8 דק'
  על משכפל 2). כל המגירות במקביל, בכל המסלולים (סבב/משיכה, תחנה ו"שחזור לדיסק הזה", הפצה ישירה, שיכפול מקומי מול
  המקור). כשל — המגירה נכשלת בשמה: "האימות אחרי הכתיבה נכשל — הדיסק לא שמר את מה שנכתב"; בדיקה שלא רצה — גם נכשלת.
  אימג' בלי רשימה — נכתב ואומר **"לא אומת אחרי הכתיבה"**. הגיבוב בשרת מפנה את הדרך לעבודה (ברזל 20/10: ‏`nice`/`ionice -c3`,
  נעצר בזמן שידור, קליטה, העברה או סבב של אימג' אחר, ואימג' שנפתח לו סבב — ראשון).
- **בדיקת הכתיבה תופסת דיסק עם קיבולת מזויפת** (#1805, CLR-02): סמן של 4KiB בכל חזקה של 2 MiB ובסוף, כולם נכתבים
  ואז נקראים — דיסק שמחזיר כתובות מעבר לסופו האמיתי נכשל לפני טבלת המחיצות.

### אבטחה
- **ממצאי CodeQL שאומתו (#2160).** (1) משני שהותקן עם `--primary-url` בשם מארח: השרת השאיר `primary_ip` ריק בשקט, וכל חילול של חומת האש מהקונסולה (הדלקת הפצה, שירותי כרטיס) פתח את 8443 מכל הכרטיס, בלי `saddr`. עכשיו השם נפתר ל-IPv4 בעלייה כמו במתקין, ושם שלא נפתר מסרב לחולל את חומת האש בשם, במקום לפתוח אותה. (2) גיבוי `/etc/network/interfaces` של המתקין וסמן ההחזרה של שינוי רשת נכתבים 0600 ולא 0644. (3) ‏`/api/v1/agent/log` מחזיר "not gzip" קבוע; הפירוט של zlib נשאר ביומן השרת.

### שונה
- **הקצב הלומד מגיע לשיא בפחות גלים ומתחיל מכונה חדשה מראיית הצי** (#1758, ברזל 19/10, משכפל 2). שיא נקי במיוחד — פחות מ-0.5% שידורים חוזרים, פחות מפקיעה אחת ל-GB, בלי ניתוק, ועם שיפור goodput מעל הרעש — מטפס 300m במקום 100m, בלי לעבור גג או קצב שנזכר כלא-בטוח. מכונה שלא נראתה כלל מתחילה צעד אחד מתחת לקצב הגבוה ביותר שמכונה אחרת הוכיחה בבטחה באותו עומס; בלי ראיית צי נשארת הפתיחה הקודמת; מכונה מוכרת במספר מגירות חדש נשארת ב-×0.75 לכל הכפלה (#1842). ספי העצירה, הירידה, הניתוק והמצב "לא נמדד" לא השתנו.
- **משכפל לא נכבה לבד כשיש דיסק עם בעיה — רק בלחיצה על "כבה"** (#2024, הכרעת נדב 22/10). בסוף סבב, "שחזור אימג׳ מהשרת", שיכפול מקומי ו-USB: יעד אדום או כתום — נכשל, דולג, בוטל, הושלם עם הערה, אימות אחרי הכתיבה שלא אישר (‏`unverified` או שלא נרשם), בדיקת כתיבה איטית, דרייברים שלא הונחו או דרייבר אחסון שלא הוגדר — או יעד שהתוצאה שלו לא נקראה, משאיר את התוצאות על המסך **בלי ספירה ובלי תקרה**, עם "המחשב נשאר דלוק כי יש דיסק עם בעיה — לחצו 'כבה' כשסיימתם". כל מגירה/יעד בעייתי מודגש (רקע ומסגרת אדומים או כתומים) והשורה שלו היא הסיבה. הכול ירוק — כמו קודם, 20 שניות וכיבוי. התקרה של 300 שניות (‏`END_HOLD_S`) בוטלה. המחיר, שנדב קיבל: משכפל שנשאר דלוק לא מצטרף לבד לסבב הבא.
- **דיסק יעד כתום נכתב בלי שאלה — שאלה אחת לא עוצרת יותר גל שלם** (#2042, נדב 23/10). בסבב חדר, במשיכה מהשרת, בהפצה ישירה ובשחזור תחנה, שער ה-SMART (`smart_gate`) כותב דיסק כתום (אזהרת SMART או "נכשל בשיכפול הקודם") בלי לשאול; עד היום שאלה על WD אחד (‏187=1682) עצרה את הגל 113 שניות עד תשובה מהקונסולה. מגירה כתומה שהסתיימה מקבלת הערה כתומה עם הסיבה — "SMART: שגיאות שלא תוקנו (187)" — במסך המשכפל ("הושלם — …") ובשורה מתחת למגירה בעמוד החדר בקונסולה. ב-`disk_event` שלה `decision: null` (איש לא הכריע). אדום וירוק — ללא שינוי. מנגנון השאלה נשאר בקוד ואינו נקרא (#2044).
- **מסך המשכפל בלי פס שאלת ה-SMART** (#2347, הצד של C ב-#2044). הפס "החלף דיסק / דלג על הדיסק / כתוב בכל זאת…", המקשים R/S/Enter שלו והשורה "ממתין להכרעת SMART" יצאו מהגואי. הסוכן לא שואל מאז #2044, ולכן הפס לא יכול היה להופיע. שורת `smart_prompt=` מסוכן ישן נרשמת כמפתח לא מוכר ואינה מציירת דבר. חלון אזהרת ה-SMART נשאר בשחזור לדיסק של מחשב הבנייה, בקליטה ובשיכפול המקומי.
- **מחשב שיכפול עם שתי מגירות ומעלה מפענח את הזרם פעם אחת, לא פעם לכל מגירה** (#1746). נמדד על המשכפל ב-18/10: מגירה אחת ב-450m, שתיים ב-264–370m, והמעבד (‏i5-650, שתי ליבות) הוא שהגביל — כל מגירה הריצה `zstd -dc` משלה על אותם בייטים. עכשיו `zstd` אחד מזין את כל המגירות דרך `fanout` שני, והבידוד לא זז: מגירה שמתה נפסלת לבדה. ‏`zstd` שנכשל מכשיל את כל המגירות שהזין בשם הזרם ("פענוח הזרם נכשל"), בלי לרשום כשל על דיסק תקין. המונה של כל מגירה נשאר בבייטים דחוסים (`fanout -U`). עם מגירה אחת — כמו קודם. מתגי ניסוי לברזל ב-`/run/imagectl/tune`: ‏`IMAGECTL_DRAWER_DECODE`, ‏`IMAGECTL_DRAWER_NICE`, ‏`IMAGECTL_DRAWER_DIRECTIO`. הקצב על ברזל עוד לא נמדד.
- **השרת מתקין בלי Recommends** (#1987). ‏`--no-install-recommends` במתקין (`setup-boot-server.sh`, גם חומת האש), בהכנת מארח ה-VM (`vmupdate-host.sh`, שגם כפתור העדכון מריץ) ובהוראת הקרנל של `verify-boot-payload.sh`. המתקין החי של ה-ISO, עדכוני המערכת ובניית ה-initramfs כבר התקינו כך. על Debian 13 נקי (‏debootstrap, ‏testrunner, ‏22/10) הסגירה של המתקין ירדה מ-626 ל-288 חבילות (338 נשמטו), ובין הנשמטות `python3-python-multipart`, ‏`python3-h2` ו-`qemu-system-gui` (‏#1975). שתי חבילות שהשרת משתמש בהן הגיעו עד היום רק דרך Recommends, ולכן נוספו במפורש: `dbus` (‏hostnamectl/timedatectl) ו-`linux-image-amd64` (‏#904, ומכשיר ה-guestfs). טסט סורק את כל הסקריפטים ונכשל על התקנה בלי הדגל.

### תוקן
- **`iscsi_login` ממתין לקישור ה-by-path ולא מחזיר `ok` עם נתיב ריק** (#2649). ‏`iscsiadm -l` חוזר 40-100ms לפני שה-udev יוצר את הקישור של ה-LUN, ובמדידה על testrunner 15 מתוך 15 התחברויות החזירו `device_by_path: ""` והשלב הבא באשף קיבל 409. עכשיו `udevadm settle` ואז polling עד 5 שניות; אחרי התקרה — כישלון בשם ("ההתחברות הצליחה אבל ההתקן לא הופיע"). ‏`valid_iscsi_device` לא הוקל. מבוסס על #2610.
- **אימות מלא להגדרות השרת לפני שמירה** (#1794). ‏`POST /api/console/settings` דוחה ערך מסוג או מטווח שגויים בעברית, בלי לשנות אף ערך; מתגים מקבלים רק `"true"`/`"false"`, והמתנת סבב קצרה מ־30 שניות נדחית במקום להכשיל פתיחת סבב מאוחר יותר.
- **מגירת ההתראות אינה אומרת "הכול תקין" כשבדיקת הבריאות לא נקראה** (#2060). ‏/health שנכשל (שגיאה, timeout) הוצג כ"אין התראות" עם pill ירוק; עכשיו שורה "בדיקת הבריאות לא נקראה: <סיבה>" ו-pill "לא נבדק" בצבע אזהרה.
- **מכונה בסבב ארוך מ-12 שעות כבר אינה נזרקת כשחכירת ה-DHCP שלה פגה** (#1949, ‏R2 22/10). הסוכן הריץ `udhcpc -q` — חכירה אחת ויציאה — ואיש לא חידש אותה; ‏dnsmasq מחק אותה כשפגה, שומר הזהות סירב לכל דיווח, והמכונה סומנה `client_lost`. עכשיו `udhcpc` נשאר ברקע ומחדש ב-T1, וה-hook אינו מוריד כתובת שכבר על הכרטיס (חידוש באמצע שחזור אינו `flush`; חכירה שאבדה משאירה את הכתובת). שומר הזהות לא השתנה. ‏**ויומן:** סירוב זהות זהה (אותו MAC/כתובת ואותה סיבה) נרשם פעם אחת ב-5 דקות, והשורה הבאה אומרת כמה נבלעו — עד כאן 346 שורות ב-10 דקות. הסירוב עצמו (403) לא השתנה.
- **מחשב שסומן "אבד" ועדיין מקבל את הזרם כבר אינו מתחיל מחיצה בלי החבר החי** (#1950, ‏R2 22/10, ‏0/2). ‏#1430 מוציא `lost` מ-`--min-receivers`; ‏VCLONE-2 האבוד הגיע ראשון למחיצה 4, השולח יצא לדרך, ומשכפל 2 הצטרף 4 שניות אחריו וקיבל זרם ריק. עכשיו חבר `lost` שקיבל את המחיצה הקודמת עד סופה (לפי הלוג של udp-sender: התחבר, לא נזרק) נספר שוב, ביומן `send_counts_lost`; חבר שהשולח זרק — לא, כמו ב-#1430.
- **שם מחשב או שרת בעברית נרשם בלי אזהרה — שרת המשנה "חיפה", "מחשב בנייה"** (#1997, נדב 22/10). שם של מחשב בנייה, משכפל, השרת עצמו ושרת משנה הוא ה-hostname: אותיות אנגליות, ספרות ומקף, עד 15 תווים, לא מתחיל ולא נגמר במקף. כלל אחד בשרת (`server/hostname_rule.py`), זהה לסוכן ולמתקין (טסט משווה). נאכף בהוספה, בהדבקה ובעריכה של מכונה, בשינוי שם השרת (עד כאן RFC 1123 — 253 תווים ונקודות), באשף ובמתקין (עד כאן 63), ברישום שרת משנה (לפני ה-pairing) ובעריכתו; כל סירוב אומר את השם ואת הכלל. שם קיים שאינו חוקי אינו משתנה לבד ואינו חוסם: הוא מוצג כמו שהוא, ומופיע ב"דורש טיפול" בסקירה עם "שינוי שם". עברית נשארת מותרת בשם אימג', תיקייה ודיסק. בקונסולה "שם חופשי לבנייה ולשיכפול" הוחלף ב"שם מחשב — באנגלית, עד 15 תווים".
- **אימג' Windows שעבר sysprep קיבל שם אקראי בעלייה הראשונה** (#1976). שלב ה-specialize קובע את השם מקובץ התשובות, ו-`ComputerName` = `*` (או בלעדיו) דרס את מה שנכתב לרג'יסטרי. כשמצב האימג' (`Setup\State\ImageState`) הוא GENERALIZE_RESEAL, השם נכתב גם לקובץ התשובות ש-Setup יקרא (`Panther\Unattend`, ‏`Panther`, ‏`System32\Sysprep`), נוסף לשלב ה-specialize כשחסר, ונוצר `Panther\unattend.xml` כשאין קובץ. אימג' בלי sysprep — אין שינוי. מצב שלא נקרא, או קובץ ב-UTF-16 — כשל גלוי (`unattend_failed`).
- **סירוב לשם מחשב שאינו חוקי ב-Windows לא אמר מה השם ומה הכלל** (#1977). הסוכן כבר סירב לשם כזה לפני הכתיבה, אבל ההודעה על היעד הייתה "שם המחשב אינו תקין". עכשיו היא נוקבת בשם ובכלל (אותיות אנגליות, ספרות ומקף; עד 15 תווים). הכלל בשרת — #1997.
- **שחזור לינוקס: אותו machine-id ואותם מפתחות SSH בכל המשוכפלים** (#1979). בכל מגירה, סבב ושיכפול מקומי `/etc/machine-id` מתרוקן (systemd יוצר חדש), ‏`ssh_host_*` נמחקים, ויחידה חד-פעמית מריצה `ssh-keygen -A` לפני sshd. בלי systemd המפתחות נשארים — ונאמר על היעד. ‏#1978: נבדק שכל גישה בסוכן היא דרך צומת המגירה ולא UUID/LABEL, ונוסף שומר.
- **שרת קיים עובר מ-systemd-timesyncd ל-chrony בכפתור העדכון, וחוזר בגלוי אם זה נכשל** (#1773, #1814).
  עד כה chrony נכנסה לקריאת ה-apt הכללית, ‏`--no-remove` סירב להסיר את timesyncd, וכל עדכון של שרת קיים
  חזר לתג הקודם. עכשיו `install/timesync-swap.sh` מחליף את הזוג בשלב משלו, מהמאגר החתום, עם שרת הזמן
  שהיה מוגדר ב-timesyncd, ומחכה ל-`chronyc tracking` מסונכרן. כשל מחזיר את systemd-timesyncd מאותו מאגר
  (‏`make-pool.sh --security` אורז אותה כדרך חזרה). ‏`makestep 1.0 -1` בהגדרה של chrony — שעון שקפץ (VM
  אחרי suspend) חוזר בעדכון הבא ולא נועל את ה-MFA. שורת הבריאות "שעון": מקור, מסונכרן או לא, וסטייה עם
  כיוון; מ-30 ש' — אדום.
- **`wol_arm_failed` נרשם פעם אחת לכל אתחול** (#1847) — עד כאן כל hello, כל 5 דקות, הוסיף שורה ליומן.
- **דיסק שהכתיבה אליו נקטעה (אתחול, נפילת חשמל) אומר זאת על הדיסק עצמו, ולעולם אינו מקור** (#1798, נדב 19/10). על ברזל, שיכפול מקומי שנקטע ב-`reboot -f` השאיר יעד עם GPT מלא וחצי מהנתונים: "SMART תקין" במסך ההמתנה, "Windows · 4 מחיצות" בשיכפול המקומי, ואפשר היה לבחור אותו כמקור. עכשיו כל מחיצה בטבלה שהסוכן כותב נקראת `imagectl-incomplete`, באותה קריאת sgdisk שכותבת את הטבלה, והשמות המקוריים חוזרים רק אחרי הכתיבה האחרונה (האימג' כולו וההרחבה), ונקראים בחזרה. דיסק מסומן מוצג בכתום "כתיבה לא הושלמה — לא שמיש" במסך ההמתנה, במסך המשיכה מהשרת, בשיכפול המקומי ובקונסולה. הוא **אסור כמקור** (שיכפול מקומי, קליטה, הפצה ישירה — הסוכן מסרב בשם) ו**מותר כיעד**. המקור עצמו אינו מסומן ואינו נפגע. סימן שלא ניתן לקרוא — "לא נבדק", לא "תקין".
- **קצב של 2 מגירות אינו מוחל כמות שהוא על 4** (‏#1842, ברזל 19/10, משכפל 2). הזיכרון נבחר לפי (מחשב שיכפול, מספר מגירות) כבר מ-17/10, אבל כשלמכונה אין גל עם מספר המגירות הנוכחי והקרוב הוא עם **פחות** מגירות, הקצב עבר "לכל היותר כמו שהוכח" — ולכן סבב של 4 מגירות יצא ב-432m של סבב 2 מגירות: ‏963k שידורים חוזרים, ‏623 פקיעות, ‏10:27 (הבא, ב-324m: ‏9:18). מעכשיו (הכרעת נדב 20/10) הקצב שהוכח יורד ×0.75 לכל הכפלה של מספר המגירות (‏432m → 324m — הקצב שהברזל הוכיח ל-4 מגירות), והחיפוש ממשיך מהזיכרון של 4. קצב שרשום כלא-בטוח למכונה עם מספר המגירות הזה או פחות עדיין קיר. מגל עם **יותר** מגירות — כמות שהוא. מסך החדר: "נגזר מגל עם פחות מגירות — הקצב הוקטן לפי מספר המגירות"; היומן: `basis=drawers(...)`. ‏`?v=` ‏11.83.
- **אימג' עם מחיצת NTFS קטנה לפני C: נדחה בכל דיסק** (‏#1875, ‏Vm / img_5790e4: ‏3GB לפני C: של 223GB — "יישארו רק 2.0 GB פנויים ב-C:" גם על 500GB). בדיקת המקום הפנוי (סוכן ושרת) בחרה את מחיצת ה-`windows` הראשונה על הפלטה, וההרחבה את האחרונה. עכשיו כלל אחד בסוכן (`diskfit.sh:system_part`) ובשרת (`minfree.py:system_partition`): המחיצה שהקליטה מצאה בה את כוורת `Windows/System32/config/SYSTEM` (נרשם במניפסט כ-`"system": true`), ובמניפסט ישן — ה-`windows` הגדולה ביותר; שתיים בגודל העליון = "לא נבדק", לא "לא נכנס" שגוי. כתיבת השם מעגנת את מחיצת המערכת ראשונה, ולא את מחיצת ה-OEM.
- **מסך המשכפל דחה אימג' שנכנס לדיסק, בזמן שהקונסולה אישרה אותו** (#1863, הכרעת נדב 20/10). אם אימג' מתאים לדיסק מכריע עכשיו רק הגודל האמיתי (הפריסה ועוד המינימום הפנוי) מול הדיסק שנבחר — אותה הכרעה בקונסולה, במסך המשכפל, במחשב הבנייה וברשימת האימג'ים שהמכונה מציגה. "משפחה" 256/500 נשארת תווית תצוגה. אימג' שלא נכנס לאף מגירה לא מוצע; אימג' שנכנס לחלק מהן מוצע, ומגירה קטנה מדי נדחית לפני כתיבה במשפט של הקונסולה ("דיסק 3 במחשב 1 הוא 43GB, האימג' צריך 61GB", קוד `fit`) במקום "האימג' הזה אינו מותר למחשב הזה". אימג' שאין דיסק שבו יישאר בו המינימום הפנוי נדחה עכשיו גם בקונסולה, ולא נפתח סבב שייכשל בכל מגירה.
- **כפתור העדכון מתקין חבילות שנוספו ל-`packages.txt`** (‏#1803). עד כה הוא שידרג רק חבילות מותקנות, ולכן שרת שעודכן מ-v0.59.2 לא קיבל את ifenslave (‏#1264) — "כרטיס שני" החזיר 409. עכשיו כל שם ב-`tools/iso/packages.txt` של התג החדש שחסר בשרת מותקן מהמאגר החתום של הגרסה, עם קריאה חוזרת כראיה; חבילה שאינה במאגר מפילה את העדכון בשמה וחוזרת לתג הקודם. ‏`make-pool.sh --security` אורז עכשיו את כל הסגירה של הרשימה, ולא רק עדכוני אבטחה — **המאגר צריך להיבנות מחדש**. מול #1773 (‏chrony מתנגש ב-systemd-timesyncd) — ראו `docs/lab/release-signing.md`.

## [v0.75.1] - 2026-10-19

> גרסת בדיקה לפני v1 (נדב 19/10): נועדה לעדכון שרת המשנה (חיפה) מהלשונית שלו ולבדיקות בין-שרתיות. ‏v0.76.0 שמור ל-v1.

### שונה
- **בלי מידע על דיסקים ב"דורש טיפול" של הסקירה** (נדב 18/10). כשלי כתיבה (אדום/כתום, ‏#1580/#1742), SMART של NVMe ו"נקה אחרי החלפה" יצאו מהסקירה. הם נשארים בדף "מחשבים" (לשונית "דיסקים אדומים", עם "נקה"), בחלון המכונה ("נקה אחרי החלפה"), בכרטיסי המגירות ובמסכי המשכפל. דף "דיסקים" ייבנה ב-#1765. הפעמון לא ספר דיסקים גם קודם.
- **דף "סבב הפצה" אחד-לאחד לפי המוקאפ המאושר** (`docs/design/apple-mockups/console-full.html`, נדב 18/10: "לא לפי המוקאפ"). כותרת "סבב הפצה" עם תגית ("פעיל" / "פנוי") ושורת משנה "גל N · אימג' · N מחשבים"; פעולה אחת בכותרת — "עצירת הסבב…" (או "התחל סבב"); שלושה אריחים — התקדמות כוללת, קצב זרם, בלוקים ששודרו שוב (‏null = "—" ו"לא נמדד", לא 0); כרטיס "חדר המשכפלים" ("כשל במחשב אחד לא עוצר את השאר", "כיבוי כל המשכפלים…") עם כרטיס לכל משכפל — שם, תגית מצב, IP, ושורה לכל דיסק "דיסק N · פס · אחוז"; לא מחובר מקווקו ("לא הצטרף לסבב"), דיסק אדום ברקע אדום עם הסבר. בלי סבב — מצב ריק עם "התחל סבב". מה שאינו במוקאפ ואינו דורש פעולה (הגל והיעד, הזמן, הקצב שנבחר) — שורות שקטות בתחתית הכרטיס; מה שדורש פעולה נשאר בהערות. "תצוגה גדולה", "הער משכפלים (WoL)" ו"רענון" ירדו מהכותרת (אינם במוקאפ); WoL למשכפל כבוי ו"בטל…" לדיסק — בריחוף/פוקוס. בורר "מצב לדוגמה" ומתג "הצג מה השתנה" הם כלי המוקאפ ואינם במוצר. ‏`?v=` ‏11.76.
- **נקודת מצב אחת לכל שרת בעץ — TA וכל שרת משנה** (נדב 18/10). היא נגזרת מבדיקות `/health` של אותו שרת, כמו "דורש טיפול" בסקירה שלו: ירוק = מחובר וכל הבדיקות תקינות; כתום = מחובר ויש אזהרה (למשל רשת הפצה שלא הוגדרה); אדום = תקלה, או שלא ענה; אפור = לא נמדד. קודם הנקודה של שרת משנה אמרה רק "מחובר", ולכן חיפה בלי רשת הפצה הייתה ירוקה בעץ וכתומה בסקירה. ה-title אומר את הסיבה. הקופסה בתחתית העץ ("מחובר / כל השירותים פעילים") יצאה. הנתון הוא מה שכבר נטען לשרת המוצג; לשאר השרתים — קריאה אחת ל-`/health` לכל היותר פעם ב-45 שניות.
- **אחרי כל כניסה — "סקירה כללית" והעץ ההתחלתי** (נדב 18/10, TA על b951c1fd). עץ השרתים חוזר למצב של טעינת הדף: TA פתוח, מלאי/תשתית/ניהול מקופלים, כל שרת משנה מקופל. קודם ה-DOM נשאר בין יציאה (או ניתוק בחוסר פעילות) לכניסה הבאה, ומה שנפתח לפני היציאה נשאר פתוח. רענון (F5) בתוך session קיים אינו מאפס.
- **"איפוס אימות דו-שלבי" למשתמש אחר, ו"דרוש אימות דו-שלבי" למשתמש** (נדב 18/10). בתפריט ⋯ של משתמש (admin בלבד, כפתור אדום בלי הקלדת מילה): הסוד וקודי הגיבוי נמחקים וההפעלות שלו מנותקות; יומן — מי איפס למי. על עצמך — מ"החשבון שלי". תיבה "דרוש אימות דו-שלבי" בהוספת משתמש ובעריכה: מנהל מסומן נרשם ל-MFA בכניסה גם כשהחיוב הכללי כבוי, ואינו יכול לבטל אותו בעצמו.
- **"שרת משנה" גם ביחיד** (נדב 18/10: שם אחד לדבר אחד). "שרת משני", "השרת המשני", "המשני" ו"משניים" — "שרת משנה" / "שרת המשנה" / "שרתי משנה" בכל טקסט מוצג: הקונסולה, מסך השרת, הגואי הנייטיבי, והודעות השרת (כולל "רישום שרת משנה"). "DNS משני" אינו שרת ונשאר.
- **"שרתי משנה" במקום "סניפים" בכל הממשק, ובלי כרטיס בסקירה** (נדב 18/10). תפריט, כותרות, לשוניות, כפתורים, הודעות, חלון הרישום, היומן, מסך השרת והגואי — "שרתי משנה" / "שרת משנה" (המזהים בקוד לא השתנו). כרטיס "שרתי משנה" יצא מהסקירה הכללית ("יש בשביל זה את הלשוניות"); משני שלא מגיב, ורשימה שלא נקראה, נשארים ב"דורש טיפול".
- **ניתוק אוטומטי בחוסר פעילות: מתג, ו"אחרי N דקות"** (נדב 18/10). דקות (1–1440) בממשק ובשום מקום לא שניות; כבוי = אין ניתוק בחוסר פעילות (`console_idle_seconds=0`). ברירת המחדל נשארת פעילה, 10 דקות; ערך שמור אינו משתנה בעדכון. השרת מסרב לכל ערך אחר בעברית.
- **חיוב אימות דו-שלבי למנהלים הוא מתג, כבוי כברירת מחדל** (נדב 18/10: "תכלס לא צריך לזה MFA. אם בנוי, אפשר להפעיל או לא"). הגדרות ← אבטחה ← "חיוב אימות דו-שלבי למנהלים" (`mfa_required_for_admins`). כבוי: אין רישום כפוי בכניסה ובהחלפת סיסמה; מי שהפעיל MFA ממשיך להתבקש לקוד; ב"החשבון שלי" אפשר להפעיל, ולבטל עם קוד (`POST /me/mfa/disable`). דלוק: בדיוק מה שהיה. בשדרוג החיוב כבוי, ושורת יומן אומרת את זה.
- **דיסק אדום (SMART נכשל) לעולם אינו יעד, בשום מסלול** (#1732, הכרעת נדב 18/10 אחרי התייעצות עם איש מקצוע; מחליף את "כתוב בכל זאת" של #1580). סבב חדר, משיכה מהשרת, הפצה ישירה, סבב כיתה, "שחזור לדיסק הזה" ושיכפול מקומי — אף אחד לא כותב עליו. בסבב המגירה האדומה מדולגת בגלוי, בלי שאלה ובלי המתנה של 30 דקות, עם ההודעה "SMART נכשל — לא כותבים על הדיסק הזה; אפשר להעתיק ממנו או לחלץ לשרת". מותר רק כמקור: העתקה ממנו בשיכפול מקומי, קליטה/העלאה לשרת וחילוץ. בגואי: "יעד" אפור בשיכפול מקומי, מגירה אדומה אינה נבחרת במסך החדר ובמסך המשיכה, וחלון ה-SMART על כתיבה לדיסק אדום הוא סירוב עם כפתור "סגור" בלבד. בקונסולה אין "כתוב בכל זאת" לאדום. השרת דוחה פתיחת סבב/הפצה ישירה/סבב ממסך משכפל על מגירה שדווחה אדומה, ודוחה "כתוב" על שאלה אדומה גם עם אישור; הסוכן מדלג עליה גם אם הבקשה עברה בכל זאת. כתום (נכשל בשיכפול הקודם, SMART תקין) נשאר כמו שהוא, עם "נסה שוב".
- **משכפל שהוער ב-WoL מתחיל לכתוב מה-hello הראשון שלו** (#1703). ה-hello שמשלים את הגל — כל הנבחרים הצטרפו ומגירותיהם מכסות את היתרה — מוציא אותו באותה תשובה, במקום להשאיר אותו `open` עד הדופק הבא. על ברזל (17/10) זה היה 22–23 ש' בין ה-hello הראשון ל-`GET manifest` בכל גל קר: הסוכן עבר בינתיים SMART, הרמת גואי, מסך הבחירה ו-`poll_sleep` לפני שלמד שהגל יצא. בגל שממתין עוד למשכפל — מסך הבחירה יוצא מיד על ה-`open` של ה-hello הראשי, בלי לחכות לדופק שיגיד אותו שוב. חבר שעוד בדרך אינו מוחמץ: התנאי ליציאה לא השתנה.
- **מסך ההפצה במחשב הבנייה ומסך "שיכפול במקביל" במחשב השיכפול בעיצוב מסך השיכפול המקומי** (נדב 17/10, "גרסה ב׳"; המוקאפ ב-#1578). שורה לכל דיסק — מספר מגירה, שם וגודל, תג מצב, מה קורה עכשיו, אחוז ופס — במצבים כותב / ממתין להכרעת SMART / דולג / נכשל / הושלם, ומעליה כותרת עם אחוז גדול ושלושה מדדים. במחשב הבנייה — כלוב לכל מחשב שיכפול בסבב, שניים זה לצד זה. השורה היא רכיב אחד (`lcbay.c`) לשלושת המסכים, כולל השיכפול המקומי.
### נוסף
- **עדכון שרת משנה מהקונסולה של הראשי** (#1776, נדב 19/10: "אני מתל אביב רוצה לעדכן את חיפה"). בעץ, תחת שרת המשנה, הגדרות ← "עדכונים ותחזוקה" היא אותה קבוצה של הראשי, על המשני: גרסה מותקנת, "בדוק עכשיו", הגרסה הזמינה עם החתימה, "החל עדכון", ומסך העדכון עם השלבים והשגיאה. הערוץ הבין-שרתי המאומת מעביר עכשיו `POST /update/check` ו-`POST /update/apply` (admin בראשי בלבד; deploy — 403). המשני מחיל במסלול החתום שלו: תג שאינו חתום נדחה בשלב "בודק חתימה", והסיבה מגיעה למסך דרך `GET /update/status`. כל פעולה נרשמת ביומן של שני השרתים (`peer_write`; במשני גם `update_apply_started` בשם `primary:<user>`). "חזור לגרסה הקודמת" אינו עובר בערוץ. ‏`?v=` ‏11.80.
- **מסך כישלון אחיד בגואי הנייטיב** (#1569, מוקאפ #1550 — נדב 17/10: "אין מסך לא-גרפי ואין מסך שחור"). כל עצירה של הסוכן (`ui_error_hold`: קליטה, שליחה ישירה, שחזור, מגירות שדולגו או נכשלו, ו"השרת לא אישר") וכל יציאת חירום (`die_local`, למשל סירוב לפני כתיבה ב"שחזור לדיסק הזה") מוצגות במסך אחד בעברית: כותרת, "מה קרה" עם הסיבה של היעד, "מה עושים עכשיו", ופרטים טכניים מתקפלים (‏Enter). שורת המצב אומרת אם השרת עדיין שומע את ההמתנה — מה-hello האחרון, לא מהנחה. על קיוסק חי המסך מופיע בו; בלעדיו עולה גואי לתצוגה בלבד. "מכונה לא מוכרת" ממתינה להכרעה (#1550).
### אבטחה
- **גישת משתמש ה-VM לאזור העבודה של עדכון ה-VM ניתנת בשמו (ACL), לא דרך הקבוצה** (#1610). ‏CodeQL מסמן כל ביט שאינו של הבעלים (`py/overly-permissive-file`, גם 0o710), והערת `codeql[...]` אינה סוגרת התראה בשער הציבורי (נמדד: #341 נשארה פתוחה). עכשיו תיקיות העבודה וה-ESP נפתחות ל-uid של משתמש ה-VM למעבר בלבד, וקובצי האתחול לקריאה בלבד; הקבוצה ו"אחרים" — אפס; ‏root נשאר הבעלים. חבר אחר בקבוצה של משתמש ה-VM כבר אינו עובר. מערכת קבצים בלי ACL נכשלת בגלוי, לא נופלת להרשאה רחבה.
### אבטחה
- **עדכוני האבטחה של Debian מגיעים לשרת בתוך כל גרסה חתומה** (#1411, הכרעת נדב 15/10). `make-pool.sh --security` אורז את חבילות trixie-security של מה שהשרת מתקין (ונכשל בלי trixie-security במקורות של המארח הבונה); `publish` מסרב לפרסם בלי המאגר ומצמיד אותו ל-Release של התג; כפתור העדכון מוריד אותו, דוחה מאגר שאינו תואם לגיבוב שבתג החתום, ומתקין ממנו בלבד, בשמן, את החבילות המפגרות (`apt-get install --no-remove`) — בתקרת זמן, עם חזרה לגרסה הקודמת בכשל. מסך השרת מציג "עדכוני מערכת מתאריך …".

### תוקן
- **מגירה תקועה לא מפילה עוד את המגירה הבריאה שלצדה** (#1796, ברזל 19/10, משכפל 2). ‏zstd של מגירה אחת נעצר; ‏fanout הוציא אותה רק אחרי 150 שניות, ובכל הזמן הזה לא קרא את הזרם — ‏udp-receiver לא ענה, ‏udp-sender ניתק את המכונה כ-40 שניות אחר כך, ושתי המגירות נכשלו ב"פג הזמן -- הזרם לא הגיע". עכשיו יש ל-fanout שעון נוסף, על הזמן שבו קריאת הזרם עצמה חסומה (`FANOUT_BLOCK_EVICT_MS`, ‏45 שניות): מגירה שעדיין מלאה אחריהם, בזמן שלמגירה אחרת יש מקום, מוצאת בשמה ("הדיסק לא עמד בקצב — הוא עיכב את הזרם והוצא מהסבב"), והאחרות ממשיכות. זה תופס גם מגירה שמטפטפת בייטים בודדים, שהשעון של 150 השניות לא תפס לעולם. הסף האפקטיבי הוא ‏~23KB/s (‏1MiB ב-45 שניות) — כונן איטי אך תקין אינו מוצא. מגירה יחידה (תחנה, קליטה) אינה מוצאת בשעון הזה, כי אין מי שירוויח מכך; ב-directsend השעון מוזז מעבר לתקרת המעטפת, כמו שעון ה-150. ‏`?v=` ‏11.83.
- **ספירת 20 השניות במסך הבחירה של מחשב השיכפול מתחילה כשהמסך מוצג** (ברזל 18/10, ‏rc6, משכפל 2: "עד שעלתה לי תצוגה כבר עברו 20 השניות, והוא המשיך מהשרת"). הסוכן הכריז "on screen" והתחיל לספור לפני שהגואי פתח את התצוגה. עכשיו הגואי כותב חותמת (`lcboot.shown`) אחרי שהציג את הפריים הראשון של המסך, והסוכן סופר ממנה. גואי שלא הציג אחרי 60 שניות — הספירה מתחילה בלעדיו, בשורה ביומן, והמחשב ממשיך מהשרת כמו קודם.
- **מגירה שדולגה כאדומה סוגרת את המקום שלה בסבב** (#1760, ברזל D6b 18/10). לא פותחת גל ולא מעירה את המשכפל (`skipped_serials`, ‏`skipped_drives`); הסבב נסגר `written=1/2 skipped_red=1`. דילוג אדום על דיסק לקריאה בלבד נזכר בשרת (`disk_failures`, ‏`cause=readonly`, לפי סידורי) כדי שאחרי אתחול הדיסק יהיה אדום ולא כתום, ורשומה ישנה `unclassified` עם ראיית EPERM נקראת `readonly`. ספירת הסיום של המשכפל כוללת את המגירה שדולגה — "1 מתוך 2 דיסקים נכתבו" ולא "הדיסקים נכתבו".
- **דיסק במצב קריאה בלבד אדום, ומוצג אדום לפני השיכפול** (#1742, נדב 18/10; D6 על משכפל 2). `/sys/block/<d>/ro` נקרא בכל hello ובכל מסך שמציג יעדים, גם אחרי ה-probe של ההמתנה — `ro`=1 הוא האדום של SMART (`fail`, הסיבה `readonly`, אותו מנגנון כמו #1732), והמסכים אומרים "הדיסק במצב קריאה בלבד". `ro` שלא נקרא הוא "לא נבדק", לעולם לא "תקין". כתיבה שנדחתה ב-EPERM/EROFS נרשמת `cause=readonly` ולא `unclassified`, והדיסק הזה (לפי סידורי) אדום עד "נקה".
- **השרת לא מתמלא: רזרבה למערכת ותקרה לכל מה שאינו אימג'** (#1737, נדב 18/10: "שעוד חצי שנה הוא לא יפסיק לעבוד"). האימג'ים, ה-DB והיומנים יושבים על אותה מחיצה (נמדד על TA), וקליטה יכלה למלא אותה עד אפס. עכשיו: (1) **10GiB נשמרים למערכת** — קליטה, העלאה מהקונסולה, קליטה במשני, משיכה מהמשני ועדכון ב-VM (פתיחה ושמירה) מסרבים לכל כתיבה שתשאיר פחות, לפני הבייט הראשון ובזרמים בלי אורך מוצהר גם תוך כדי — בעברית ובמספרים; (2) **ניקוי יומי אוטומטי** מתוך שעון החדר: יומן האירועים — שנה, טבלאות המדידה והמצב החי — 90 יום, תמיד בלי לגעת בשורה שהקוד קורא כ"מצב נוכחי" (הדגימה האחרונה של כל מכונה, בסיס ה-CRC, חלון הקצב של כל כונן, זיכרון הגלים, כשלי דיסק פתוחים), וכל מחיקה נרשמת ביומן במספרים; (3) **אזורי עבודה נטושים** — קליטה בלי משימה, ייבוא, העלאה ומשיכה שנכשלה — נמחקים אחרי יממה בלי כתיבה, ומשימה פתוחה לעולם לא; (4) **תקרה ל-journald** (1G, ‏90 יום) — בהתקנה ובעדכון הגרסה; (5) **התראה בסקירה** כשהמקום הפנוי מתקרב לרזרבה, גם כשהאחוז נראה תקין. אימג'ים לעולם לא נמחקים אוטומטית.
- **גל שנפתח בזמן שמחשב השיכפול עוד סופר לכיבוי כבר אינו נתקע** (#1721, ברזל rc5 18/10). גל שכתב חלק מהמגירות פתח את הגל הבא ושלח WoL באותה שנייה — למשכפל שעוד היה דלוק בספירת 20 השניות, והחבילה נבלעה; המשכפל כבה והסבב נשאר `active` בלי שאיש יעיר אותו. עכשיו, כל עוד הגל פתוח ומחשב שיכפול שנבחר ושנשארה לו מגירה לכתוב לא נראה מאז הפתיחה, השרת שולח לו WoL חוזר כל 60 שניות, עד 6 פעמים (יומן `room_rewake`). מכונה שלא חזרה גם אחרי זה ו-3 דקות לאתחול — הסבב נכשל בגלוי, בשמה (`room_wake_failed`), והחריץ משתחרר. רק מי שנבחר מוער (#1580).
- **שיכפול מקומי: אחרי עצירה מסך הבחירה כבר לא מציע את היעד הריק כמקור** (#1722, ברזל R3 18/10). המסך הציע את מגירה 3 כמקור כשנפתח, והחזיק בתפקיד הזה לאורך ההעתקה ואחריה — גם כשהדיסק כבר בלי טבלת מחיצות ("מדיסק 3 אל דיסק 2": אישור היה דורס את המקור). עכשיו, בכל חזרה למסך אחרי שיכפול — עצירה, "שכפול נוסף", כשל, חזרה וכניסה — אין בחירה מוכנה מראש; אחרי עצירה חוזרת רק הבחירה שהמפעיל עשה בעצמו, בדיוק (אותם דיסקים ומספרים סידוריים). מקור שהמגירות כבר לא מאפשרות יורד, וההצעה הראשונה רק לדיסק שיש עליו מערכת.
- **גואי שקרס באמצע משיכה חוזר תוך שניות, במקום מסך קפוא** (#1726, ברזל rc5 18/10). אחרי `kill -9` לגואי באמצע משיכה ל-2 מגירות המסך נשאר על הפריים האחרון — 17% — עד הכיבוי: ההפעלה מחדש של #1569 רצה רק בין hello ל-hello, והסוכן היה עסוק בכתיבה. עכשיו לגואי יש שומר משלו שמחזיר אותו לאותו מסך בכל מסלול (משיכה, סבב חדר, שיכפול מקומי, סיום, "שחזור לדיסק הזה"), מזהה גם תהליך שנעלם, ואחרי 3 ניסיונות מציג את המסך הסטטי. שאלה שהייתה פתוחה כשהגואי קרס (SMART, כיווץ) נגמרת מיד בברירת המחדל של "אין תשובה", כמו קודם.
- **מגירה שנשלפה באמצע הכתיבה אינה צובעת את הדיסק כתום** (נדב 17/10). כשהכונן נעלם באמצע הכתיבה ולא החזיר שגיאה משלו (‏`device offline error` בלי טיפול שגיאות של ATA, ‏`/sys/block` שנעלם, או `DevExch`), השרת מסווג את הכשל `removed` — והדיסק ירוק בהפצה הבאה, כמו אחרי זרם שנקטע. שגיאת כתיבה אמיתית (סימן קו או דיסק) נשארת כתומה. רשומות ישנות שנרשמו "לא סווג" ונושאות ראיה לשליפה מפסיקות לצבוע בלי "נקה".
- **הטקסט של שני כרטיסי ההפצה אומר מה שנמדד** (#1264, מעבדת netns 17/10). בלשונית "רשת הפצה" ובחלון הכרטיס השני נכתב "התעבורה מתחלקת ביניהם" ו"השני ממשיך בלי הפסקה". נמדד: סבב שיכפול (מולטיקאסט) יוצא בכרטיס אחד בכל רגע, וניתוק כרטיס באמצע סבב עוצר אותו ל-0.5–3 שניות — והסבב מסתיים, עם sha זהה בכל המקבלים. עכשיו כתוב שתעבורה למחשבים שונים מתחלקת, שסבב יוצא בכרטיס אחד, ושסבב שרץ נעצר לשניות ספורות ומסתיים כרגיל. המדידה ב-`docs/architecture/interfaces.md`.
- **הפצה ישירה ממחשב הבנייה נכשלה תמיד במחיצה 1** (#1574, ברזל 17/10): "הבייטים שנשלחו שונים מהמניפסט". מאז #1472 הקריאה הראשונה (המניפסט) הריצה partclone עם `-a0 -z 524288` והשנייה (השידור) בלי — שני זרמים שונים. עכשיו שני המעברים לוקחים את הדגלים ממקום אחד (`capture_pcl_flags`); דיסק שבאמת השתנה בין המעברים עדיין נכשל בשמו. נמדד על דיסק loop עם ESP אמיתי: העיגון של boot_ca (‏ro) לא משנה בייט.
- **משכפל שאותחל באמצע הזרם כבר אינו מצטרף לאמצע אותו גל, ושני דיסקים תקינים אינם נצבעים כתום** (#1728, ברזל rc5 18/10). `reboot -f` באמצע מחיצה 3: המשכפל חזר תוך 27 שניות, קיבל את הגל הרץ, ניסה לכתוב את מחיצה 1 מזרם שכבר היה במחיצה 3 (`fanout: empty stream`), שתי המגירות נרשמו `disk_failure` והסבב נכשל 0/2. עכשיו hello מצטרף של חבר שכבר נכנס לזרם מקבל "אין סבב", החבר מסומן `lost` (יומן `room_rejoin`), וגל שלא כתב דבר אבל המשכפל שלו חזר וחי — פותח את הגל הבא במקום להכשיל את הסבב. זרם ריק/קצר/ארוך הוא כשל של הזרם: הסוכן אינו אוסף עליו ראיית דיסק, והשרת אינו רושם עליו `disk_failure` (גם מסוכן ישן), ורשומות כאלה שכבר נרשמו אינן צובעות.
- **מחשב בנייה אחרי כשל קליטה/הפצה ישירה לא לקח את המשימה הבאה** (#1575): "קליטה נוספת" מהגואי נשארה "ממתין לסוכן" עד אתחול. מסך הכשל נשאר, אבל משימה חדשה מהשרת מסיימת אותו ונלקחת מיד.
- **משימה שהוזמנה מהגואי של מחשב הבנייה רצה לבד אחרי אתחול** (#1576, הכרעת נדב 17/10): משימת גואי שלא התחילה מבוטלת כשהמכונה מאתחלת ("בוטלה: המכונה אותחלה לפני תחילת המשימה"), והמסך חוזר להתחברות; בהפצה ישירה נסגרים גם הסבב והגל. משימה מהקונסולה ממשיכה לחכות לאתחול כמו קודם. השרת קובע מאיפה הוזמנה המשימה לפי הערוץ (פורט הסוכן מול הקונסולה), לא לפי מה שהבקשה מצהירה.
- **צבעי הדיסק בשיכפול לפי הכרעת נדב 17/10** (#1580, מחליף את כלל #872; האדום הוחלף ב-#1732, למעלה). **אדום** = SMART אומר שהדיסק תקול: אזהרה חזקה ולא חסימה, כמו Clonezilla — "כתוב בכל זאת" רק דרך חלון עם הסיבה, המשפט "הדיסק תקול לפי SMART — הכתיבה עלולה להיכשל או להשחית נתונים" והכפתור האדום (גם מהקונסולה), ובלי תשובה מדלגים. עד היום "תקלת SMART" הייתה כתומה ונכתבה בלי תשובה. **כתום** = נכשל בשיכפול הקודם ו-SMART תקין: "נסה שוב" כותב, ובלי תשובה כותב. עד היום זה היה אדום שאי אפשר לכתוב עליו. **ירוק** = הכשל הקודם היה עצירה באמצע (הסבב נעצר, המכונה כובתה, הזרם לא הגיע): לא צובע בכלל.
- **השידור נכשל אחרי 180 שניות בזמן שמחשב שיכפול חיכה לתשובה על דיסק (SMART)** (ממצא ברזל 17/10). הגל יצא לדרך, המכונה עצרה על חלון ה-SMART, והשעון של השולח רץ מתחת לשאלה — התשובה הגיעה אחרי כמעט שלוש דקות, והשולח יצא עשר שניות אחריה. עכשיו השולח לא מתחיל כל עוד מכונה בגל ממתינה לאדם, והיומן אומר "השידור ממתין לתשובה על דיסק". מכונה שנעלמה באמצע השאלה לא מעכבת את השידור יותר מדקה, ושאלה שאיש לא עונה עליה נסגרת אחרי חצי שעה בברירת המחדל של הצבע (כתום כותב, אדום מדלג); אם המכונה בכל זאת לא ממשיכה, השרת משחרר 35 דקות אחרי תחילת הגל רק אותה מהגל — גם אם היא שואלת שאלה אחרי שאלה — והשאר משודרים.
- **מחשב שיכפול המשיך לכתוב אחרי שהשרת סגר את הסבב** (ממצא ברזל 17/10). הסוכן ראה שהסבב נסגר ועצר רק את הדיווח; הכתיבה המשיכה מול שולח שכבר יצא, המכונה נשארה "כותבת" והדיסק חצי-מוכן. עכשיו סגירת הסבב עוצרת את הכתיבה — לפני הטבלה, בין מחיצות ובאמצע ההמתנה לזרם — וכל מגירה מסומנת "נכשל" עם הסיבה "השרת סגר את הסבב".
- **"כתוב בכל זאת" על דיסק אדום (נכשל בשיכפול הקודם) הונמך בשקט ל"דלג"** (ממצא ברזל 17/10, מחשב שיכפול 2). המפעיל ענה על מגירה 2 "כתוב בכל זאת", היומן רשם את התשובה — וההכרעה על הדיסק יצאה "דלג", אף מגירה לא הצטרפה, והשליחה נכשלה אחרי 180 שניות. הסוכן מבצע עכשיו תשובה מפורשת כמות שהיא; בלי תשובה דיסק אדום עדיין מדלג.
- **מחשב הבנייה נכבה באמצע סבב שהוא פתח** (#1560, ברזל 17/10, פעמיים). המסך הראה 91% וגל משדר, ואז "אין פעילות — המחשב יכבה" — שעון ה-idle ספר רק מקלדת ועכבר. עכשיו סבב שנפתח מהמחשב הזה ועדיין פעיל, או סבב/שיעור פעיל שהמסך מציג, אינו "אין פעילות". בסוף סבב שנפתח מכאן, וגם בסוף שליחה ישירה, המחשב שואל "כבה" / "חזרה לתפריט", כמו בסוף קליטה (עד היום: 20 שניות וכיבוי בלי שאלה).
- **אחרי קליטה מחשב הבנייה נכבה לבד אחרי 20 שניות** (#1560, ברזל 17/10). השאלה "כבה / חזרה לתפריט" הייתה עם תקרה של 20 שניות, ו"20" הוצג כטקסט קבוע. עכשיו הדיאלוג סופר בפועל (20, 19, 18…), ובסופו — כיבוי (הכרעת נדב). וכל ספירה על המסך — גם אזהרת ה-idle והספירה של המשכפל — יורדת בכל שנייה (עד היום: כל 2 שניות).
- **מחשב שיכפול נשאר דלוק ולא הצטרף לסבב הבא** (#1560, ברזל 17/10). אחרי "שחזור מהשרת" שנכשל הוא נשאר חצי שעה על המסך, וסבב שנפתח עבורו התחיל בלעדיו — WoL לא מעיר מכונה דלוקה. עכשיו בסוף כל משימה של משכפל — סבב, שחזור מהשרת או שיכפול מקומי, **גם כשנכשלו** — ספירה של 20 שניות על המסך וכיבוי — גם כשהגיעה משליחה ישירה, וגם כשכל המגירות דולגו — (עם WoL דרוך), או מיד ב"כבה עכשיו"; כשחלק מהמגירות נכשלו המסך אומר כמה נכתבו, ואפשר לעצור את הספירה כדי לקרוא את התוצאות (הכיבוי אז בעוד 5 דקות, עם אותו כפתור); "עצור" ידני בשיכפול מקומי חוזר לבחירה; והמסך הראשי, השיכפול המקומי בלי עבודה רצה ומסך השחזור יוצאים מעצמם כשנפתח סבב עבור המחשב.
- **הפצה ישירה שכל המגירות שלה נכתבו סומנה "נכשלה"** (#1613, רגרסיה של #1547). מאז #1432 השרת מכשיל גל ישיר כשלא נשאר בו מקבל חי — אבל "חי" לא הבחין בין מקבל שנפל לבין מקבל ש**סיים**. בחלון שבין דיווח הסיום של מחשבי השיכפול לבין הדופק שסוגר את הגל, hello של מחשב הבנייה (למשל הדופק של מסך ההמתנה אחרי דיווח סופי שלא נשמע) הפך סבב שנכתב 2/2 לסבב שנכשל, "0 נכתבו", ודיווח ה"הצלחה" של המקור נדחה. עכשיו הגל נכשל רק כשאף מקבל לא חי **ואף אחד לא סיים**. זה גם מה שהפיל את סימולציית הקצה-לקצה בשלב 9ב ואת השער הציבורי.
### תוקן
- **אתחול חוזר מהיר נתקע עד 18 שניות נוספות על חיבורי ה-HTTP של GRUB** (#1423, נמדד על מחשב שיכפול 2, 17/10). ‏GRUB יוצא מאותם פורטי מקור בכל אתחול ואינו עונה ב-RST, ולכן כל socket שנשאר אצל השרת מהאתחול הקודם עלה 40 SYN ב-15.6 שניות. שני מקורות: TIME_WAIT, כי ‎/boot שלח `Connection: close` והשרת סגר ראשון, ו-LAST-ACK על החיבור האחרון, ש-GRUB סוגר עם חלון אפס ולא מאשר. עכשיו ‎/boot לא שולח `close`, ו-GRUB שסוגר חיבור ‎/boot מקבל RST. אתחול חוזר מיד אחרי הקודם ירד מחציון 36 שניות ל-24, עם אפס התנגשויות ב-10 אתחולים.
- **טסטים השאירו תהליכים חיים ב-Testrunner** (#1736). ~100 תהליכים בני עד 23 שעות (`bash -c`, `sleep 300`, stub של fanout) מילאו את הדיסק עד 92%. השורש בכלי הבדיקה: `subprocess.run` מחכה לתהליך הישיר ולא לצאצאיו (לולאת ה-beat של `attended_start` נשארה אחרי `lc_pull_session`; קוד הייצור עוצר אותה ב-`local_clone_offer`). עכשיו `hygiene.run_group` הופך את התהליך לראש session ו-`killpg` בסוף, ו-`tests/orphan_guard.py` הוא שומר אוטומטי: טסט שמשאיר תהליך שמזכיר את ה-tmp_path שלו נכשל בשמו.
- **דיסק עם טבלת מחיצות פגומה נדחה בשחזור** (#1545, ממצא ברזל 17/10). דיסק שסבב קודם נקטע עליו (GPT ראשי עם CRC שגוי, הגיבוי תקין) נכשל ב"sgdisk דחה את תוכנית השחזור — הדיסק לא שונה" — בדיוק הדיסק שהכי צריך שחזור. ‏sgdisk שלא מצליח לטעון את הטבלה הישנה מתעלם מהמחיצות החדשות ויוצא 2, גם עם `--clear`. עכשיו בדיקת התוכנית רצה על קובץ דליל בגודל הדיסק — נבדקת התוכנית החדשה, לא הטבלה הישנה — ותוכנית לא חוקית עדיין נדחית בשמה לפני שהדיסק שונה. ומאותה סיבה `--zap-all` על דיסק כזה מוחק ויוצא 2: הוא רץ פעם שנייה, ורק יציאה 0 של השנייה מתירה להמשיך. תקף לתחנה/מחשב הבנייה, למגירות ולשיכפול המקומי.
- **סבב חדר שנכשל חטף את מסך "שיכפול מהשרת" במחשב הבנייה** (#1568, ברזל 17/10). מסך הבחירה הופיע לשנייה וקפץ למסך ההפצה של הסבב הכושל, ולא היה אפשר לבחור אימג' עד שהסבב נעצר ידנית. סבב `failed` אינו מוצג עוד כסבב חי: מסך הבחירה נשאר, עם שורה אדומה "הסבב הקודם נכשל: <סיבה>"; הסיבה נשארת ביומן ובהיסטוריה, והשרת כבר פותח סבב חדש מעל סבב `failed`.
- **מסך הסבב הציג את כל מחשבי השיכפול** — עכשיו רק אלה שהסבב כותב אליהם (‏`target_slots`), בגואי ובקונסולה.
- **ההתקדמות של המכונה צוירה על כל המגירות שלה** — נבחרה ונכתבה רק מגירה 3, והמסך הראה 16% גם על מגירה 2. כל מגירה מציגה עכשיו את היעד שלה בגל; מגירה שאינה בגל — "לא נבחרה", בלי פס.
- **גל שנכשל נראה כמשדר** — הכותרת אמרה "משדר" והפס "הגל משדר". רק גל רץ אומר זאת; גל שנכשל מוצג באדום, וגל שהסתיים — "הגל הסתיים".
- **הקצב במסך ההפצה היה ממוצע מאז שהגל יצא** (המסך הראה "32.8 MB/שנ׳" בזמן שהמחשב קלט 75MB/s). הקצב הוא עכשיו הבייטים שהמגירה כתבה ב-10 השניות האחרונות לפחות, מחותמות השרת על הדיווחים, וזמן הסיום נגזר ממנו.
- **"הער את מחשבי השיכפול" במסך הבחירה לא עשה כלום, וכשהעיר — העיר את כל החדר** (ברזל 17/10). הכפתור היה אפור מאז 16/10 ולא שלח בקשה; ופתיחת סבב העירה את כל מחשבי השיכפול (`wol_sent grp_CLONERS count=3`), גם כשנבחרו מגירות של מחשב אחד. עכשיו בוחרים במסך מחשב שיכפול שלם — גם כבוי, לפני שדיווח מגירות — או את כל החדר ("בחר / נקה הכל"), ו"העֵר את המחשבים שנבחרו" מעיר רק אותם; אחרי שהמחשב עולה בוחרים את המגירות. גם פתיחת סבב מעירה רק את המחשבים שנבחרו בו. `POST /api/console/room/wake` מקבל `macs` (בלעדיו — כל החדר, כמו בקונסולה).
- **בדשבורד של הקונסולה, סבב שיכפול חי הופיע פעמיים** (17/10, ‏room_855f6a13): פעם כסבב שיכפול ("0% · 1 מחשבים") ופעם כסבב הפצה ("19% · 1/3 מחשבים"). עכשיו שורה אחת, עם אחוז אחד (הבייטים של הגל בזמן שהוא משדר), מספר המחשבים של הסבב והקצב של 10 השניות האחרונות.
- **מסך הכישלון של "שחזור לדיסק הזה" היה טקסט באנגלית על מסך שחור** (#1545). הגואי חוזר עכשיו כמסך הודעה בעברית — "השחזור נכשל — השאירו את המחשב דלוק ופנו ל-IT" — עם הסיבה של היעד עצמו.
- **"שחזור לדיסק הזה" מוצג עכשיו בגואי מתחילתו ועד סופו, ומסתיים בכיבוי** (#1561, ממצא ברזל 17/10). עד כאן הגואי נסגר ברגע שהכתיבה התחילה, וכל השחזור היה שורות באנגלית על מסוף שחור. עכשיו הגואי חוזר מיד אחרי לחיצת "שחזר", כתהליך אחד שמחזיק את המסך עד הסוף, ומציג את מסך ההתקדמות הקיים (`--screen progress`): שם האימג׳, הדיסק לפי המיפוי, "מחיצה N מתוך M", אחוז, קצב וזמן שנותר. כל הנתונים נלקחים מהקבצים שהכתיבה כבר מנהלת, ואין בו ערך משוער לפני שנמדד. בהצלחה מוצג "השחזור הושלם" ו"המחשב יכבה בעוד N שניות", והמחשב **נכבה** אחרי 20 שניות או מיד ב"כבה עכשיו" — לא עוד ריסט (הכרעת נדב, 17/10). כיבוי שלא תפס (Wake-on-LAN לא נדרך, או שפקודת הכיבוי חזרה) נאמר על המסך. במסך ההתקדמות יש עכשיו **"ביטול השחזור"** (הכרעת נדב, 17/10): אחרי אישור בכפתור האדום הכתיבה נעצרת בצורה נקייה, טבלת המחיצות נמחקת — הראשית וגם הגיבוי — כדי שהדיסק ייראה ריק ולא חצי-תקין (עיקרון 4), ואחריו "השחזור בוטל — הדיסק ריק" וספירה של 20 שניות לכיבוי. ביטול אינו נרשם כתקלת דיסק. אם מחיקת הטבלה נכשלה, זה נאמר על המסך והמחשב נשאר דלוק. כישלון מוצג על אותו גואי (ההודעה של #1545). סירוב אחרי לחיצת "שחזר" ולפני הכתיבה (מזהה פגום, סשן שפג, אימג׳ שכבר אינו מותר, מניפסט שלא נקרא, אין דיסק) מוצג בעברית ("השחזור לא התחיל — לא נכתב דבר"), ורק אחריו הריסט. ברגע מסירת התצוגה באפר המסוף מתאפס עוד לפני שהגואי הקודם משחרר אותו, ולכן טקסט ישן לא מבזיק. בלי גואי, המסוף ממשיך להציג את השורות כמו קודם.
- **מסך שחור או טקסט כשהגואי מת** (הכרעת נדב 17/10). גואי שקרס או יצא מופעל מחדש — עד 3 פעמים באתחול — במקום להיעלם עד האתחול הבא. כשגם זה נכשל (או שהספריות שלו לא נטענות), נצבע ישר על המסך מסך סטטי בעיצוב שלנו — סמל ImageSrv ו"ImageSrv — מתאתחל…" — שהגואי עצמו מרנדר בזמן הבנייה, ומונח במרכז בכל רזולוציה; הקונסולה הטקסטואלית מנותקת ממנו כדי שלא תצייר עליו. וכל עוד הגואי (או המסך הסטטי) על המסך, שורות היומן של הסוכן אינן נכתבות עוד לקונסולה שמתחתיו — הן ממשיכות ליומן ולשרת — ולכן גם לא נחשפות כשהגואי נסגר.

## [v0.75.0] - 2026-10-16

### נוסף
- **מחשב השיכפול: "שחזור אימג' מהשרת" — אפשרות שלישית במסך הבחירה** (הכרעת נדב 16/10). מסך השחזור של מחשב הבנייה (תיקיות, אימג'ים, אזהרה, חלון אישור עם כפתור אדום), והיעד — מגירות המחשב עצמו, לפי מספר הדיסק ועם צבע SMART, אף אחת לא מסומנת מעצמה; עובד גם בעכבר וגם במקלדת בלבד (Tab למגירות, ← → ביניהן, רווח מסמן, A — כל התקינות). האישור פותח בשרת סבב חדר של מכונה אחת על המגירות שסומנו (`POST /api/v1/agent/cloner-round`), והמחשב ממשיך מהשרת כמו בכל סבב חדר: שער SMART, מולטיקאסט, התקדמות לכל מגירה, כיבוי בסוף. בלי שם משתמש וסיסמה — והנתיב נכשל סגור: זהות MAC↔חכירה חובה, אסימון מסך חד-פעמי, ניסיון אחד ל-10 שניות, מגירות ואימג'ים מנתוני השרת בלבד, וכל ניסיון ביומן. מחשב עם דיסק אחד מקבל עכשיו את מסך הבחירה (20 שניות, ברירת מחדל "מהשרת"), עם כרטיס השיכפול המקומי כבוי.

### שונה
- **דיסק עם אזהרת SMART: כתיבה וקריאה רק אחרי חלון אזהרה** (הכרעת נדב 16/10). החלון מציג את הדיסק לפי המיפוי, את הצבע (כתום/אדום), את הסיבה (למשל סקטורים ממתינים), את הדגם ואת המספר הסידורי. יש בו שני כפתורים: "דלג" (ברירת המחדל, הפוקוס עליו, Esc) ו"כתוב בכל זאת" באדום, בלי הקלדת מילה. רק הכפתור האדום מבצע. החלון מופיע במחשב השיכפול, כש-Enter או קליק על "כתוב בכל זאת…" בשאלת ה-SMART כבר לא כותב מיד. הוא מופיע גם בשיכפול מקומי: דיסק עם אזהרה הופך ליעד רק דרכו, ומקור עם אזהרה עובר דרכו ב"המשך לאישור". במחשב הבנייה הוא מופיע בשחזור לדיסק של המחשב (אחרי "מחק ושחזר") ובקליטה מדיסק עם אזהרה. לשם כך מחשב הבנייה קורא SMART לדיסקים שלו במסכי הקליטה והשחזור (`dsmart=`), פעם אחת לכל דיסק. "שחזור אימג' מהשרת" כותב דרך שער ה-SMART של הסבב, ולכן עובר דרך אותו חלון.
- **מסך החדר במחשב הבנייה: W ו-N לא שולחים כלום כשאין סבב** (הכרעת נדב 16/10). בשורת המקשים הם מוצגים באפור, והכפתור "העֵר את מחשבי השיכפול" אפור ולא לחיץ. כשיש סבב — כמו קודם.

### תוקן
- **כיווץ על עותק בזיכרון: היעד הקטן סירב לאימג'** (#1466, נמצא ב-Testrunner 16/10). ‏ntfsresize מקטין את מערכת הקבצים בתוך העותק, אבל ההתקן נשאר באורך המחיצה הישן, ו-partclone רושם את גודל ההתקן. לכן partclone -r על מחיצת יעד בגודל החדש נכשל: "Target partition size is smaller than source". מעכשיו partclone קורא חיתוך של העותק באורך החדש בדיוק (dm `linear`). האורך נבדק בטבלה וב-`blockdev --getsize64`, והחיתוך משוחרר לפני העותק. בנוסף, הודעת הכשל במסך היא בעברית, והשורה הגולמית של הכלי הולכת ליומן.
- **העברת אימג' לסניף (push): המשני מחלץ ומאמת תוך כדי הזרם** (#1457). עד עכשיו המשני כתב את כל ה-tar לקובץ זמני, העתיק אותו בחילוץ וקרא כל מחיצה שוב ל-sha256 — כ-3× קלט/פלט אחרי הבייט האחרון, בזמן שהראשי ממתין בתקרה קבועה (20MiB/s, לא נמדד). על דיסק איטי הראשי היה יכול לדווח "נכשל" בזמן שהאימג' נכנס לספריית המשני. עכשיו כל קובץ נכתב ישר לאזור הביניים עם sha256 על אותם בייטים, ואחרי הבייט האחרון נשארת רק ההשוואה והכניסה לספרייה. **המקום הפנוי שנדרש במשני ירד מ-`2×` ל-`1×` גודל ה-tar** (+256MiB). אותם שומרים: sha256 לכל מחיצה לפני הכניסה, קטיעה בשמה עם המספרים, ודחייה בשמה של פריט שאינו קובץ רגיל — **כולל sparse/contiguous**, שנפרשים מעבר למה שנשלח — עם תקרה לסך הבייטים שנכתבים. המתנת הראשי לא שונתה (נשארת למשני בגרסה ישנה). נמדד בתחנת הפיתוח (ווינדוס, 512MiB, 16/10): חילוץ+sha256 ב-260MB/s; **לא נמדד** על דביאן ולא בין האתרים.
- **משיכת אימג' מהשרת המשני נכשלה בכל "נסה שוב" אם הניתוק היה אחרי הקובץ הראשון** (#1456). על קובץ שכבר הורד במלואו המשני עונה 416 וסוגר את החיבור, והראשי המשיך על החיבור הסגור. עכשיו הראשי פותח חיבור חדש וממשיך. הוא מקבל 416 כ"כבר שלם" רק כשהמשני מדווח גודל קובץ שווה לגודל שכבר ירד (`Content-Range: bytes */N`). בכל מקרה אחר זה כשל עם שני המספרים.
- **משיכת אימג' לא בדקה אם יש מקום בשרת הראשי** (#1458). עכשיו הבדיקה רצה לפני הבייט הראשון: היא מסרבת עם "נדרשים N בייטים, פנויים M" ולא נתקעת על דיסק מלא באמצע ההעברה. בחידוש נדרש רק מה שנשאר להוריד. אם הגודל לא דווח או שהמקום הפנוי לא נקרא, זה כשל ולא "נכנס".
- **השרת קפא לכל משך אימות הקליטה** (#1461). הגיבוב רץ על לולאת האירועים שמשותפת לסוכנים ולקונסולה, ולכן אף hello, דיווח או מסך קונסולה לא נענו עד סופו. עכשיו הוא רץ בתהליכון ברקע. טסט מריץ hello על אותה לולאה בזמן אימות שמוחזק, ונופל על הקוד הישן.
- **גם ההעלאה של קליטה חסמה את השרת** (#1488). כל chunk נכתב וגובב על אותה לולאה, ודיסק שרת איטי עיכב את כל השאר. עכשיו הכתיבה והגיבוב רצים בתהליכון, באצוות של 1MiB, וכל אצווה ממתינה לדיסק לפני שנקרא עוד מהזרם. ה-sha256 מזמן הקבלה (#1470) והטיפול בדיסק מלא (#411) לא השתנו.
- **השעון בפינת הגואי של מחשבי השיכפול והבנייה הציג UTC** (#1447) — 3 שעות פיגור, נמדד על ברזל 16/10. השרת שולח עכשיו את אזור הזמן שלו (ברירת מחדל `Asia/Jerusalem`) בתשובת ה-hello וב-`/state`, והגואי מציג לפיו שעה מקומית. בלי אזור זמן מהשרת השעון מוצג עם המילה `UTC` לידו. שרת חדש מותקן עם `Asia/Jerusalem` במקום UTC של דביאן.
- **שעון המכונה עצמו זחל** (#1447) — משכפל 2 רץ 13 דקות קדימה מהשרת. תשובת ה-hello נושאת את שעון השרת, והסוכן מכוון לפיו את שעון המערכת כשהפער גדול משתי שניות, ורושם ביומן בכמה תיקן. לא תלוי בסוללת ה-BIOS, וה-RTC אינו נכתב.
- **אימג' ישן בלי `used_bytes` נכשל בסבב חדר בכל מגירה** (#1494, ממצא ברזל 16/10). אימג' שנקלט לפני #1368 נושא `used_bytes: null` על מחיצות ה-NTFS, וכלל המקום הפנוי (#1354) "לא נבדק" — הסוכן מסרב לו בכל מגירה, בצדק. שני תיקונים:
  - **השלמה מכותרת partclone.** בעליית השרת, ברקע ופעם אחת, כל מחיצה עם `used_bytes: null` נקראת מראש הזרם שלה (‏`zstd -dc`, ‏110 הבייטים של כותרת v2 בלבד), ו-`used_bytes = usedblocks × block_size` נכתב ל-`manifest.json` אטומית עם `"used_bytes_source": "partclone_header"`. כותרת שלא עברה magic/גרסה/endianess/CRC, או ערך גדול מהמחיצה — נשאר `null`, ושורת יומן אומרת למה. הרצה יבשה לאימות: `python -m server.usedbackfill <תיקיית-אימג'>`.
  - **אימג' שהכלל אינו ניתן לבדיקה עליו אינו נפתח לסבב.** חלון "סבב חדר חדש" מציג אותו מושבת עם סיבת הסוכן (במקום "גודל נדרש לא ידוע"), ו-`POST /room` מסרב לו ב-409 — גם כשגודל המגירות אינו ידוע.
- **כל מחרוזת שמגיעה למסך המפעיל היא עברית (#1503).** ברזל 16/10: כרטיס
  "State could not be verified" בגואי של מכונת הבנייה. הביקורת
  (`docs/design/hebrew-audit-1016.md`) מצאה 205 אתרים; תוקנו 203 — כרטיסי
  הגואי וטוסטים (`guistate.sh`, `guibridge.sh`), שדה error של יעד ושל
  דרייברים/אתחול, שגיאות שדה באשף ההתקנה (קוד כשל → עברית, האנגלית ללוג),
  פירוט יומן הקונסולה (תרגום בקריאה, גם שורות ישנות), DCUI גרפי ושגיאות
  HTTP של הקונסולה. פלט גולמי של כלי יוצא מהכותרת אל הלוג. נשארו מחוץ
  לתיקון בכוונה: סירובי Fast Startup/chkdsk (#1502) ומסכי טקסט/GRUB
  שאינם מרנדרים עברית. שומר חדש: `tests/test_hebrew_screen_strings.py`.

- **שידור ישיר ממחשב הבנייה: מחיצה 2 ואילך יכלה להיהרג כ"תקועה" בזמן שהשולח עוד ממתין למגירות** (#1497). מונה הבייטים זז ברגע שחוצצי הצינור התמלאו, לפני ש-`udp-sender` קרא בייט, ו-`wait_progress` החליף את תקרת ההתחלה (900ש') בתקרת השקט (300ש') — בעוד שהשרת מרשה לשולח להמתין עד 780ש'. עכשיו שעון השקט מתחיל רק משורת `Starting transfer:` ביומן של `udp-sender` (המקבלים הצטרפו); עד אז חלה תקרת ההתחלה. תקרת השקט עצמה לא השתנתה: זרם שנעצר באמצע עדיין נגמר בדיווח. לפי קוד, לא נצפה במעבדה.
- **שיכפול מקומי ליעד קטן דרש שהשרת יענה** (#1478). מסך המקור סימן יעד קטן כ"לא נבדק" כשהשרת לא ענה, כי כיווץ במקום דרש רשומה של הפריסה המקורית בשרת (#926). מאז #1466 הכיווץ רץ על עותק בזיכרון והמקור לא נכתב, ולכן אין מה לרשום: המדידה כבר לא שואלת את השרת, ושיכפול מקומי לא תלוי בו כלל. כשאין עותק (אין device-mapper, אין מספיק זיכרון) היעד הקטן עדיין מסורב בשמו לפני שנכתב אליו בייט.
- **מסכי השיכפול המקומי אמרו שהמקור מכווץ ויוחזר, אחרי שהוא כבר לא נכתב** (#1477). מאז #1466 יעד קטן מקבל עותק מכווץ בזיכרון, אבל שלב הכיווץ עוד אמר "מכווץ זמנית את המקור… המחיצות יוחזרו לגודלן", וחלון האישור "יכווצו במקומן… ויוחזרו". עכשיו: "מכין עותק מכווץ בזיכרון… המקור נקרא בלבד ולא נכתב", בחלון האישור "נקרא בלבד — יכווץ עותק בזיכרון", ובמסך הסיום "נקרא בלבד ולא שונה — הכיווץ נעשה על עותק בזיכרון". שורת `lc_run=` נושאת את המסלול (`shrink_path`, שדה אחרון) כדי שהמסך לא ינחש אותו.
### נוסף
- **קצב, זמן משוער וזמן אימות בהעברה בין אתרים** (#1462). בשלב "שולח" — MB/s מהפרש בין שתי דגימות של השרת (`bytes_sent`, `updated_at`) וזמן שנותר; לפני שתי דגימות "מודד קצב…" (לא 0), ובלי עדכון 30 שניות — "אין התקדמות N". בשלב "מאמת" — פס נע וזמן שעבר מ-`seconds_since_update` החדש (שעון השרת). ב-pull התווית היא "הראשי מאמת". `?v=11.43` (אחרי האיחוד ב-integration/v0743-speed; ‏monitor.js ‏3.32).
- **הגבלת קצב להעברות בין אתרים** (#1473, הכרעת נדב 16/10). אופציונלי, **ברירת מחדל ללא הגבלה**. במסך הסניפים, מעל טבלת ההעברות ובדף כל משני: "הגבלת קצב בין אתרים: ללא הגבלה / עד 200 Mb/s (25 MB/s) לכל העברה", ו"שינוי" למנהל (Mb/s, ‏0 = ללא הגבלה, נרשם ביומן). חל על העברה לסניף ועל העברה מסניף לראשי, לכל העברה בנפרד; שינוי תופס גם העברה שכבר רצה (תוך 2 שניות). הראשי אוכף בשני הכיוונים — ב-pull הוא קורא בקצב התקרה, וה-TCP מאט את המשני ששולח, כך שגם משני בגרסה קודמת מוגבל.
- **מחשב הבנייה משתחרר מיד אחרי ההעלאה, והשרת מאמת את האימג' ברקע** (#1470, הכרעת נדב 16/10; עיקרון 6 נשאר). המניפסט מקבל תשובה מיד אחרי הבדיקות הזולות, והקריאה החוזרת של כל קובץ מהדיסק רצה בשרת. בזמן הזה הקליטה במצב חדש, **"מאמת"**, עם אחוז, בספרייה, בלשונית הקליטות ובסקירה. האימג' **אינו** בספרייה עד שהאימות עובר. כישלון נרשם בשם ביומן ובקונסולה, והאימג' נשאר בחוץ. שרת שעלה מחדש באמצע מריץ את האימות שוב מההתחלה, ולעולם אינו מכניס בלי אימות. בנוסף, ה-sha256 שהשרת חישב בזמן הקבלה (עד עכשיו חושב ונזרק) מושווה למניפסט כבדיקה נוספת. על 17/09 האימות היה 17.5 מתוך 45 דקות של קליטה, והמחשב חיכה לכולן. **לא נמדד עדיין:** קליטה כזו על שרת ה-ESXi או על ברזל.
- **ל-VM לעדכון זהות רשת משלו** (#1468, הכרעת נדב 16/10). בהגדרות הרשת › כרטיסי רשת יש "כרטיס לעדכוני VM": ה-VM מחובר אליו ב-macvtap, עם MAC שהשרת ייצר פעם אחת ומציג ליד הבחירה — ל"כתובת שמורה" ב-DHCP של המכללה — וחומת האש של המכללה רואה אותו בכתובת משלו, לא בכתובת של השרת. כרטיס ההפצה מסורב בשמו; כרטיס הניהול מותר. מסך ה-VM מציג את הכתובת שה-VM קיבל, ובלי כתובת תוך 5 דק' ה-VM נכבה עם הסיבה ומה צריך בפלטפורמה (ESXi / Hyper-V / ברזל). השרת עצמו אינו נגיש לאורח ברשת של הכרטיס (macvtap, וכלל לפי MAC ב-IPv4 וב-IPv6); מה שעובר דרך הנתב של הוילן — חומת האש של המכללה.
- **‏`dmsetup` ומודולי `dm-mod`, ‏`dm-snapshot`, ‏`dm-bufio`, ‏`brd` ב-initramfs** (#1466). הם מוצהרים ב-`REQUIRED_MODULES`, ולכן קרנל שחסר בו אחד מהם עוצר את הבנייה (#78). הם נטענים רק כשצריך, ולא באתחול.
- **קוד צימוד במסך השרת של המשני — F4, בלי סיסמה** (הכרעת נדב 16/10, מוקאפ #1525 וריאנט א'). במשני לא מצומד, F4 מציג קוד חד-פעמי גדול, ספירה לאחור של 10 דקות, "זהות השרת" (16 התווים הראשונים של ה-SPKI, שהם גם סיומת המזהה), ה-SPKI המלא, והכתובת שהראשי מקליד. כשהראשי משתמש בקוד המסך עובר ל"✓ השרת מצומד"; קוד שפג — "קוד חדש" ב-Enter; Esc מבטל את הקוד. במשני מצומד F4 הוא "ניתוק מהשרת הראשי", מאחורי כפתור אדום בלי הקלדה. בראשי אין F4. הנוכחות ליד השרת היא ההרשאה; אין לזה כתובת ברשת, וה-API המקומי (loopback + מנהל) לא השתנה.
- **"הכתובת שהראשי רואה"** — פריט חדש ב-F2 של מסך השרת: הכתובת שבה הראשי מגיע למשני (מאחורי NAT — החיצונית), נשמרת ומוצגת במסך הקוד. כשלא הוגדרה — אזהרה צהובה במקום כתובת משוערת.
- **קונסולת הראשי, רישום משני שלב 2: טביעה בקבוצות ושמונה תיבות לקוד** (מוקאפ #1525, ג׳1–ג׳3). ארבע הקבוצות שהמשני מציג כ"זהות השרת" מודגשות; הכפתור "רשום שרת משני" כבוי עד שכל שמונה התיבות מלאות בתו חוקי; קוד שנדחה מסמן את התיבות ומפנה ל-F5 במשני. ‏`?v=11.46`.
- **דף "סניפים" מוצג גם לפני המשני הראשון** (ממצא ביקורת הקונסולה 16/10). עד עכשיו הדף — והכפתור "+ רישום שרת משני" שבו — הופיע רק כשכבר היה משני פעיל, ולכן לא הייתה דרך בממשק לרשום את הראשון. עכשיו ראשי/עצמאי רואה את הדף תמיד: בלי משני — מצב ריק "אין עדיין שרת משני" עם כפתור רישום בולט; העברות וקבוצות מופיעות רק כשיש משני.

### שונה
- **קוד הצימוד הוא 8 תווים (`XXXX-XXXX`) במקום 9 (`XXX-XXX-XXX`)**, גם מה-API המקומי, כדי שיתאים לשמונה התיבות בראשי; אותו אלפבית בלי 0/O/1/I/L, ו-5 ניסיונות לחלון. התקרה של חלון הצימוד עלתה מ-5 ל-10 דקות (מסך השרת פותח 10; ה-API נשאר על 3). הסתרת הקוד ביומן מכירה את שתי הצורות.
- **קצב הגל נזכר לפי מחשב השיכפול, לא לפי הכוננים** (#1471, הכרעת נדב 16/10). עד עכשיו הגל עלה רק מגל קודם שכל הכוננים שלו היו בו — ובחדר מחליפים מגירות בכל גל, ולכן כמעט כל גל יצא ב-600m והגג לא הושג. מעכשיו: לכל מחשב שיכפול בגל — הגל האחרון שלו, והקצב שהגל ההוא מוריש (‏+100m על גל נקי, ×0.75 על שידורים חוזרים, אחרת כמות שהוא); הגל יוצא מ**המינימום** על המכונות, עדיין חסום בגג הכרטיסים. מחשב שיכפול שלא נראה (חדש, או שהוחלף) — כל הגל בפתיחה, והוא נזכר לפעם הבאה. ההגנות לכל כונן לא השתנו (כונן איטי מוכר מוריד, בדיקת הכתיבה). מסך החדר: "קצב פתיחה — מחשב שיכפול בלי גל קודם"; היומן `wave_rate` מוסיף `mac=` של המכונה שקבעה.
- **הקליטה מריצה partclone עם `-a0 -z 524288`, כמו השיכפול המקומי** (#1472, הכרעת נדב 16/10). בלי ה-checksum של partclone עצמו ועם חוצץ של 512KB — על מחשב שיכפול 2 (i5-650, 15/10) זה נמדד 108 → 157MB/s ו-160 → 170MB/s בצד הקריאה. בטוח כי `fanout -S` מגבב את כל הזרם הדחוס והשרת מאמת אותו לפני שהאימג' נכנס לספרייה. ‏`partclone.dd` אינו מקבל את הדגלים. **לא נמדד עדיין:** קליטה עם הדגלים על ברזל, ושחזור (מולטיקאסט ויוניקאסט) של אימג' כזה.
- **שיכפול מקומי ליעד קטן לא כותב יותר לדיסק המקור** (#1466, הכרעת נדב 16/10). עד עכשיו מחיצת ה-NTFS של המקור כווצה במקום (`ntfsresize -s`, ‏`sgdisk` על הטבלה, ‏`ntfsfix`), הועתקה, והוגדלה חזרה. מעכשיו הכיווץ רץ על **עותק copy-on-write בזיכרון**: snapshot של device-mapper מעל המחיצה, והמאגר ב-RAM disk ‏(brd). ‏partclone קורא מהעותק, והמקור נקרא בלבד. בסוף העותק משוחרר, ונבדק שהוא אכן נעלם. לפני כתיבה נמדד התקציב: `ntfsresize -n` על העותק אומר כמה MB יזוזו, ומשווים לזיכרון הפנוי פחות 1GB שנשמר להעתקה. **כשאין מקום או אין device-mapper**, היעד הקטן מסורב לפני שנכתב אליו דבר, והמסך אומר למה ועם המספרים (למשל "להזזת 8000 MB נדרשים 9056 MB זיכרון, ויש 7168 MB"). הכרעת נדב: המקור לא נכתב לעולם, ולכן אין יותר כיווץ במקום בשיכפול המקומי. יעד שנכנס בלי כיווץ מועתק כרגיל. אם שחרור העותק נכשל, המסך אומר זאת באדום: "המקור לא נכתב, אבל העותק בזיכרון לא שוחרר — אתחלו את המחשב".
- **‏`--info` אחד במקום שניים** (#1466). התוכנית שנמדדה במסך המקור משמשת שוב בשיכפול, אם זה אותו דיסק (סידורי) ואותה טבלה. אחרת מודדים מחדש.
- **זמני שלבים בדיווח השיכפול המקומי** (#1466). שורת היומן בשרת מוסיפה: מדידה, כיווץ, כמה MB הועברו, העתקה, אימות וסיום, וגם באיזה מסלול רץ הכיווץ. ערך שלא נמדד לא מודפס, ולא מוצג כ-0.
- **גם הקליטה לא כותבת יותר לדיסק המקור** (#1466, הכרעת נדב 16/10). הכיווץ בקליטה רץ על אותו עותק בזיכרון, ו-partclone קורא ממנו. אין `ntfsresize`, ‏`ntfsfix` או `sgdisk` על המקור, ואין רשומת כיווץ בשרת (#926), כי אין מה להחזיר. **כשאין עותק**, הקליטה נעצרת לפני הזרם, והמסך אומר למה ועם המספרים. לאימג' בגודל מלא קולטים שוב ובוחרים "בלי כיווץ", שאינו כותב דבר. דיסקים שכווצו במקום לפני השינוי עדיין מוצעים להחזרה באתחול.

<!-- v0.74.3 לא תויג; התוכן שלו יצא ב-v0.75.0 -->

### תוקן
- **מסך השיכפול המקומי** (#1437, ‏#1440). שלושה תיקונים, שנמצאו על ברזל ב-15/10:
  - **מחיצה שלא נמדד בה כמה בשימוש** מוצגת כ"לא נמדד", ולא בגודל שלה עם התווית "בשימוש". הסך והזמן שנותר אומרים במפורש שאינם ידועים, ובאיזו מחיצה.
  - **הזמן שנותר** מחושב מהדקה האחרונה, לא מהממוצע מההתחלה (444 → 33 דקות על אותו מצב). בדקה הראשונה כתוב "מחשב…".
  - **אחוזים מתחת ל-10%** מוצגים עם ספרה אחרי הנקודה. **בלי שרת** מוצגים ה-MAC ו"שרת: לא מחובר (שיכפול מקומי)".
- **מחשבים ישנים, לפני 2011, רצו בלי דרייבר תדר** (#1441). המודול `acpi-cpufreq` נארז, והסוכן מעלה את המעבד למצב ביצועים ורושם ביומן את המצב שנקרא בחזרה. אם הקובץ חסר, האתחול לא נעצר (עיקרון 1).

### נוסף
- **‏SHA-256 בתוך `fanout`** (`-S`, ‏`--sha256`), למסלולים שבהם ה-sha256 הוא חלק מהמניפסט. על i5-650 הוא נותן 149MB/s, מול 108 של busybox. אומת מול hashlib ומול sha256sum על 1GB אקראי.

### שונה
- **סבב חדר השיכפולים והקליטה מגבבים ומונים בתוך `fanout`** (#1438). ‏`pv | tee | sha256sum` הוחלף ב-`fanout -S <sha> -P <מונה>`: ה-`tee` של busybox מעתיק 1KiB לקריאה (בשיכפול המקומי ההחלפה נתנה 195→246MB/s על מחשב שיכפול 2). אותם בייטים מגובבים (הזרם הדחוס), וה-sha256 במניפסט זהה. ‏`-S` נכתב רק בסוף זרם שנקרא כולו, ולכן קליטה בלי ערך sha נכשלת בשמה ואינה שולחת `"sha256":""`. דורש את `fanout -S` (ענף נפרד).
- **הקצב לפי כרטיס ההפצה** (הכרעת נדב 15/10: "הכי מהר שאפשר, והשרת מגביל לפי כרטיס הרשת שלו"). עד עכשיו הגל הראשון יצא ב-600m והגג היה 900m — גם כשכרטיס ההפצה 10G. מעכשיו השרת קורא את מהירות הקישור (`/sys/class/net/<if>/speed`): הגג 90% ממנה והפתיחה 60%. על 1GbE זה בדיוק 900m/600m — שום דבר לא זז; על 2.5G ‏2250m/1500m; על 10G ‏9000m/6000m. מהירות שלא נקראה (‏-1, קישור למטה, קובץ חסר, כרטיס וירטואלי) — 600m/900m של היום, והסיבה נאמרת. הרצפה נשארת 150m. שורת החדר: "כרטיס ההפצה: 10Gb/s · תקרת שידור: 1.1GB/s"; היומן `wave_rate` מוסיף את הכרטיס והגג.
- **גם מחשבי השיכפול מגבילים את הגל** (הכרעת נדב 15/10). הגג = הנמוך מבין כרטיס השרת לבין הכרטיס האיטי מבין מחשבי השיכפול בגל. המהירות של כל מחשב: ידנית (שדה חדש במגירת המכונה, "מהירות כרטיס רשת": אוטומטי / 1Gb/s / 2.5Gb/s / 10Gb/s, מנהל בלבד, נרשם ביומן), ואחרת מה שהמחשב מדווח בבדיקת המכונה. מחשב שמהירותו לא ידועה **מונח 1Gb/s** — וזה נאמר כהנחה: "כרטיס רשת: לא ידוע — הונח 1Gb/s", ובשורת החדר "תקרה לפי מחשב 2: 1Gb/s (הנחה — המהירות לא ידועה)". 1GbE בכל מקום = בדיוק המספרים של היום. ⚠️ `IMAGECTL_MAX_BITRATE` מעל 900m כבר אינו מגביה את הגג כשיש בגל מחשב ב-1G.

## [v0.74.2] - 2026-10-15

### תוקן
- **שיכפול מקומי איטי** (#1438). מדידה על ברזל, מחשב שיכפול 2 (‏i5-650): השיכפול רץ ב-33MB/s. dd על אותה מכונה כותב 265MB/s, אבל מעתיק את כל הדיסק. אצלנו הצוואר היה המעבד: sha256 של busybox חושב פעמיים, partclone חישב checksum משלו, וכל בייט עבר שבעה תהליכים בצינורות של 64KB. התיקון:
  - **הגיבוב (xxh64), ספירת ההתקדמות וצינורות של 1MB עברו לתוך `fanout`** (תוכנית ה-C שלנו). בצד המקור כבר אין pv, ‏tee ו-md5sum.
  - **בכל יעד** `fanout -H` מגבב ומעביר ל-partclone (‏`tee` של busybox העתיק 1KB בכל קריאה). נשארת אותה השוואה בין מה שנקרא למה שכל יעד קיבל.
  - **partclone רץ בלי checksum פנימי** (`-a0`) ועם חוצץ קריאה של 512KB.
  - **התוצאה בחלון יציב, לפי מוני הדיסק:** ‏33 → 138 (md5) → 195 (fanout) → **‏246MB/s** (ממסר fanout במקום tee). partclone לבדו נותן 263, ו-dd ‏265. בדיסק של 500GB עם 203GB בשימוש זה כ-14 דקות, מול כ-40 של dd. בדיסק מלא כמעט: כ-34 דקות, מול כ-31 של dd.
- הגיבוב אומת מול ה-checksum של zstd, שהוא XXH64 הרשמי.

## [v0.74.1] - 2026-10-15

### תוקן
- **ה-CI הציבורי אדום מאז v0.73.2:** טסט האינטגרציה של עדכון ה-VM הוא opt-in (qemu, ‏root), ודילוג עליו תחת `REQUIRE_NATIVE=1` נחשב כישלון. עכשיו יש לו marker משלו (`vm_it`), וה-CI מסיר אותו במפורש, כמו את `uinput`. בנוסף תוקנה אזהרת ציטוט (SC2046) בבניית ה-ISO.

## [v0.74.0] - 2026-10-15

### תוקן
- **אתחול GRUB נתקע כל עוד עדכון אימג' ב-VM טעון** (#1422, ‏#1405): חומת האש של ה-VM החריגה "כל מי שאינו משתמש ה-VM" — וחבילה של חיבור שהשרת כבר סגר (כל הורדת kernel/initrd) אינה שייכת לאף משתמש, ולכן נזרקה. עכשיו רק מה ששייך **בוודאות** למשתמש ה-VM נבדק; כל השאר עובר.
- **מכונה שנכשלה לא מעכבת את שאר החדר** (#1430): המחיצה הבאה ממתינה רק למכונות שעוד חיות. גם הסוכן כבר לא מוותר אחרי כ-6 דקות על מחיצה שנייה שמתעכבת: מונה הזרם מתאפס בין מחיצות. **מגבלה ידועה:** מכונה שמתה בשקט נספרת עד שהיא מוכרזת אבודה, כ-3 דקות.
- **שעון החדר ומתזמן הריסטרט רצו פעמיים** (#1431). הקונסולה מוגשת על שתי כתובות, וכל אחת הפעילה אותם. עכשיו הם רצים פעם אחת לכל שרת.
- **שחזור שנכשל מוחק את ה-GPT רק ביעד שבאמת נכתב** (#1409): ראיית כתיבה לכל יעד, ומגירה שלא נכתבה לא נמחקת.
- **מניפסט נבדק לפני שחזור** (#1407), וקריאת קבצי מחיצה מאימג' מוגנת מנתיבים חורגים (#1408).
- **WoL נדרך לפני כל כיבוי של הסוכן** (#1378), ו-`wol_armed` מדווח לשרת.
- **GRUB מנסה שוב טעינת kernel/initrd** ותיקון גבולות פקודות הניסיון החוזר; רשומת שחזור ב-GRUB של המתקין.
- **udp-sender לא משאיר ילדים יתומים**; דוחות למשימה סגורה לא נשלחים שוב; בקשת כיבוי/אתחול נענית גם בהמתנה לאדם.
- **שעון השרת**: סנכרון זמן מופעל ומנוטר (#1389).
- **תעודת TLS שהמפעיל העלה נשמרת** בעדכון, ו-SAN ישנים מתחדשים.
- **הגדרות הקונסולה נשמרות אטומית** — הגדרה לא תקינה נדחית בשמה ולא נשמרת חלקית.
- **קליטה: `used_bytes` של מחיצות NTFS נמדד** (#1368): המדידה עיגנה ב-`mount -t ntfs`, ובקרנל של הסוכן אין סוג כזה — כל מחיצת NTFS יצאה `null`. עכשיו דרך `ntfs-3g` לקריאה בלבד, ובניית ה-initramfs עוצרת אם FUSE אינו בקרנל.
- **קליטה: רק מחיצת המערכת מסומנת `windows`** (#1369): מחיצת OEM/נתונים עם אותו GUID סומנה גם היא, וכתיבת שם המחשב פנתה אליה ונכשלה. התפקיד נקבע לפי `Windows\System32` במחיצה; אימג'ים ישנים עם שתי מחיצות `windows` מקבלים את השם במחיצה הנכונה. בדיסק C:+D: ‏D: היא עכשיו `data`, ולכן C: היא שמורחבת בשחזור (עד כאן D: נמתחה ו-C: נשארה מכווצת).
- **משימת קליטה שהסתיימה מציגה סך בייטים** (#1370): `bytes_total` מתמלא מהמניפסט כשהקליטה נסגרת.
- **מסך ה-VM בקונסולה: שחור בחיבור הראשון, ובלי מקלדת ועכבר** (#1392): noVNC 1.6 עונה ל-QEMU בהודעת לוח מורחבת שאורכה שלילי, והגשר קרא אותה כ-4GB — ומאז בלע כל בקשת עדכון, עכבר ומקש בלי לסגור את החיבור. מקש של noVNC מול QEMU (הודעה 255) גם הוא לא היה מוכר לגשר. עכשיו ההודעה המורחבת נזרקת, המקש עובר (רק לשולט), והודעה ענקית נסגרת בשם במקום להמתין.
### נוסף
- **ריסטרט אוטומטי לשרת בשעה שנקבעת בהגדרות** (#1406): בזמן סבב, קליטה, עדכון או שיכפול מקומי הוא ממתין, ואם השרת לא התפנה — מדלג למועד הבא ונרשם ביומן.
- **חלון "התחל סבב" לפי המוקאפ המאושר** (#1275): מגירות בשורות, "בחר הכול" בוחר רק ירוקות, ופתיחת סבב כבויה עד שנבחרה מגירה; השרת דורש בחירה מפורשת של מגירות (#1410).
- **מכונות בנייה ושיכפול ככרטיסים**, נפחי דיסק בגודל השיווקי, ומצב קישור של כרטיס הרשת.
- **כיבוי מעל שיכפול מקומי** (#1367): במחשב הבנייה ובשיכפול — אזהרה ואישור לפני כיבוי.
- **מינימום מקום פנוי אחרי שחזור** (#1354, הכרעת נדב 13/10): יעד שאחרי השחזור יישארו בו פחות מ-20GB פנויים ב-C: (או במחיצת הלינוקס הראשית) **לא נכנס** — בסבב חדר, בשחזור במחשב הבנייה ובשיכפול מקומי, לפני שנגעו בדיסק: "לא נכנס — יישארו רק X GB פנויים ב-C:, המינימום 20GB". המינימום הוא **הגדרה** בהגדרות ← כללי ("מינימום מקום פנוי אחרי שחזור (GB)", ‏0–500, ‏0 = כבוי), ומשתנה בלי עדכון גרסה. "נכנס לדיסק מ-X GB" כולל אותו, וחדר השיכפולים מסרב לאימג' כזה לפני שנפתח גל. אימג' שלא נמדד כמה תפוס ב-C: — "לא נבדק", ולא נכתב.
- **בדיקת כתיבה לפני הגל** (#1366): לכל דיסק שנבחר לסבב — 4MiB בהתחלה, באמצע ובסוף, כתיבה ישירה עם fsync, קריאה חוזרת והשוואה. דיסק שלא מקבל כתיבה נכשל בשמו **לפני** הזרם ("הדיסק לא מקבל כתיבה — …"), והשאר ממשיכים; בדיקה שלא יכלה לרוץ = "לא נבדק" וממשיכים. מגירה שלא נבחרה לא נכתבת (#1275).
- **מהירות הכתיבה לפני הגל** (#1366): MB/s מכל דיסק; מתחת ל-50 MB/s, או מתחת לחצי מהחציון של שאר המגירות במכונה — אדום, "איטי — N MB/s", ולא נכתב.
- **סף CRC מוחלט** (#1366): הגדרה בקונסולה, `crc_warn_threshold` — **כבויה כברירת מחדל** (0; המונים לא עלו בגל של 13/10, כלומר היסטוריים). כשמדליקים, מגירה עם יותר מהסף — כתומה: "כבל או מגירה חשודים — N שגיאות", במסך ההמתנה, בגואי, בקונסולה ובשאלת "התחל בכל זאת".

### שונה
- **SMART שעוצר BIOS על F1 — כתום** (#1366, נדב 13/10): גם 197/198 מעל 0 (לא רק שורת בריאות FAILED) הם `fail`, והקונסולה מציגה אותו כתום ולא אדום: "ה-BIOS יעצור ב-F1 — SMART: …". בלי תשובה — נכתב.

### אבטחה
- **initramfs עם שכבת מעבדה נכנס ל-ISO רק בדגל מפורש** (#1412) — בלי הדגל הבנייה עוצרת.
- **צפייה ב-VM נסגרת כשהסשן מבוטל**, ואינה מתחברת מחדש אחרי אובדן הרשאה.

## [v0.73.2] - 2026-10-13

### תוקן
- **הציבורי אדום ב-v0.73.1** (#1359): אזהרת shellcheck בסקריפט העדכון, ומגבלת זמן של 35 דקות שהסוויטה כבר לא נכנסת בה (60).

## [v0.73.1] - 2026-10-13

### תוקן
- **מסך השרת: Backspace מוחק בכל השדות** (#1258): בסיסמת F2/F12, בטופס הרשת ובכתובת לפני ההתקנה — tty1 שולח את המקש כתו ולא כקוד, והקוד ציפה רק לאחד מהם.
- **הפעימה של מחשב עם אדם לידו** (#1333): ערך לא שלם בהגדרת הפעימה הפך אותה לסערת hello, וערך כזה בפעימת שאלת ה-SMART הרג אותה בשקט; עכשיו ערך לא תקין נרשם ונלקחת ברירת המחדל.
- **הציבורי אדום ב-v0.73.0** (#1356): אזהרת shellcheck בבדיקת רשות האישורים, וכלי השיכפול המקומי חסרו במכונת ה-CI.

## [v0.73.0] - 2026-10-13

**עדכון אימג' בתוך VM על השרת** (#1334): פותחים גרסה של אימג' כמכונה וירטואלית ישר מהקונסולה, מעדכנים (Windows Update, תוכנות), ושומרים כגרסה חדשה בספרייה — בלי מחשב בנייה. **שיכפול מקומי** במחשב השיכפול (#1322): דיסק לדיסקים, בלי שרת. **מסך עדכון** שמראה את שלבי העדכון עד שהשרת חוזר (#1301), **שירותים לכל כרטיס רשת** שהשרת אוכף (#705), ובדיקת **מהירות קישור** ו**מחשב חסר** לפני גל (#432, #65). מחשבי השיכפול והבנייה עובדים **בגואי בלבד** (#1289).

### נוסף
- **עדכון אימג' ב-VM** (#1334, מוקאפ מאושר 13/10): "פתח ב-VM" על גרסה בלשונית הגרסאות → השרת בונה דיסק מהאימג' (אותן פונקציות שחזור של הסוכן), מדליק VM (QEMU, UEFI, Secure Boot לפי האימג') ומציג אותו בדפדפן. "שמור כגרסה חדשה" מכווץ, קולט ומאמת sha256 כמו קליטה רגילה; הגרסה הקודמת נשארת, ואפשר לקבוע איזו גרסה היא ברירת המחדל. צופה שני רואה בלבד; בקשת שליטה באישור מי ששולט. מנהל בלבד. ה-VM מבודד ברשת: יוצא רק לאינטרנט דרך כרטיס הניהול, לא לשרת עצמו ולא לרשתות הפנימיות. העדכון מהכפתור מתקין את החבילות הדרושות; אם ההתקנה נכשלת, השרת מתעדכן ומציג "עדכון ב-VM לא זמין" עם הסיבה.
- **שיכפול מקומי במחשב השיכפול** (#1322, מוקאפ מאושר): בעלייה, 20 שניות לבחירה "מהשרת" / "שיכפול מקומי" (בלי מגע — מהשרת, כמו היום). דיסק מקור אחד לכמה יעדים במקביל, רק בלוקים בשימוש, ואימות sha256 לכל יעד. לפני ההעתקה כל יעד מקבל "נכנס / נכנס עם כיווץ / לא נכנס"; יעד גדול — המחיצה האחרונה גדלה; יעד קטן — המקור מכווץ זמנית ומוחזר לגודלו.
- **מסך העדכון** (#1301, מוקאפ מאושר): אחרי "החל" — הורדה, אימות חתימה, התקנה, הפעלה מחדש, ו"עודכן ✓" רק כשהשרת חזר ואומת. כשל בשם (חתימה נדחתה, הורדה נכשלה, הוחזר לגרסה הקודמת). החיבור לקונסולה שורד את ההפעלה מחדש.
- **שירותים לכל כרטיס רשת, והשרת אוכף אותם** (#705, מוקאפ מאושר 13/10): ברשת ← חיבורים פיזיים עמודת "שירותים" — קונסולה, מוניטור, שידור, אתחול מהרשת ובין-שרתים. כבוי = השרת באמת לא מאזין ולא משדר על הכרטיס. ברירות המחדל הן ההתנהגות של היום. הקונסולה נעולה בכרטיס האחרון; כיבוי שלה בכרטיס שדרכו אתה מחובר — 60 שניות לאישור מהכתובת החדשה, אחרת חוזר לבד.
- **מהירות קישור ו-CRC לכל מגירה** (#432): מחשב השיכפול מדווח את מהירות ה-SATA בפועל ואת התקרה של הפורט; מגירה איטית או עם עליית CRC מסומנת בכתום בקונסולה, בגואי ובמסך ההמתנה, ו"התחל" שואל לפני גל עם אזהרה (אזהרה, לא חסימה).
- **מחשב חסר בגל** (#65): הגל הפתוח מראה מי לא הצטרף ולמה (כבוי / לא הצטרף / חסרות מגירות), עם "כבה אותו" למחשב בודד ו"התחל בכל זאת".
- **בדיקת רשות האישורים של Secure Boot לפני שחזור** (#672): אם ה-db של המכונה קריא ואין בו את הרשות שחתמה על האימג' — השחזור נעצר לפני שנוגעים בדיסק, עם הודעה בשם. db שלא נקרא = "לא נבדק" ומדווח, לא "תקין".

### שונה
- **גואי בלבד במחשבי השיכפול והבנייה** (#1289): מסכי הטקסט הוסרו; אם הגואי לא עולה — הודעה, אירוע `gui_failed` בשרת ועלייה מהדיסק.
- **הגרסה המוצגת היא התג החתום** (#1297): השרת מציג את התג החתום הגבוה ביותר על הקוד שרץ; בלי תג חתום — מה ש-git מוצא, עם "לא מאומת" לידו.

### תוקן
- **הציבורי היה אדום מאז v0.72.0** (#1341): תיקיית הלוגו סווגה פרטית ולא פורסמה.
- **השרת לא קורס בקונסולת ווינדוס cp1252** (#1278).
- **טסט פעימת ה-SMART דטרמיניסטי** (#994).

## [v0.72.0] - 2026-10-13

**הגרסה הראשונה שחתומה דיגיטלית** (#1277): מכאן השרת מתעדכן רק לגרסה חתומה. הקונסולה בנויה מחדש לפי המוקאפ המאושר (#1300): סקירה כללית, אימג'ים, מגירות, בריאות, הגדרות, משתמשים, רשת, אחסון, מוניטור, דרייברים, עץ צד ופס אייקונים. השם והלוגו החדשים — **ImageSrv** (#1302) — בקונסולה, בגואי, במתקין ובמסך השרת; לוגו מוסד הוסר. מסך השרת בעברית גרפית (#1203), מסך המתנה חדש במחשבי השיכפול (#1304), שרת משני מה-ISO מאזין ב-8443 (#1313), ובדיקת חיבור לראשי במתקין שחייבת לצאת ירוקה (#1310).

### אבטחה
- **אין עוד מעטפת ניפוי בסוכן** (#1303). `imagectl.debug=1` בשורת ה-GRUB פתח מעטפת root על tty2 ועל הטורית לכל מי שעמד ליד מחשב. הדגל נמחק, והסוכן מתייחס אליו כפרמטר לא מוכר. השרת ממשיך להסיר אותו מ-`--extra-cmdline`, בשביל initramfs ישן.
- **ארגז הכלים נחסם גם במחשב עצמו** (#1303): רשומת `tool-run` שמגיעה לגשר במהדורה בלי ארגז הכלים נדחית, ולא רק הכפתור מוסתר.
- **‏PXE proxy לא נדלק מרשומה ישנה** (#1303): בלי כיתות, החלת DHCP אינה מפעילה את `imagectl-proxy` גם כשב-DB שמורה רשומת proxy מלפני הגידור, ו-`GET /net/proxy-support` עונה 404.

### שונה
- **הוסרה האפשרות להחליף לוגו** (#1302, נדב 12/10): `server/branding.py`, ‏`GET/POST/DELETE /api/console/branding/logo`, ‏`GET /api/v1/agent/branding/logo` וכרטיס "מיתוג" בהגדרות — הסרה מהקוד, לא הסתרה. לוגו שהועלה בעבר נשאר בתיקיית הנתונים אך אינו נקרא ואינו נכנס לגיבוי.
- **הוסרו מהמוצר** (#1303): `server/agentctl/` (תשתית צי הסוכנים, שייכת ל-otogit), החסימה לפי כתובת מקור של הקונסולה (#151, הדגל `--console-allow-from`; ההפרדה היא בחומת האש), ושני נתיבים בלי קורא: `GET /api/console/net/dnsmasq` ו-`GET /api/console/storage-nodes/{id}/transfers` (הקונסולה משתמשת ב-`/storage-transfers`).

### נוסף
- **לוגו ImageSrv בקונסולה** (#1302 שלב ב'): הסמל החדש בסרגל העליון, הלוגו המלא במסך הכניסה (בהיר/כהה לפי ערכת הנושא), favicon בקונסולה, במוניטור ובאשף ההתקנה, והשם ImageSrv בכותרות הדפדפן, בתפריט האתחול (`menuentry "ImageSrv"`, ה-id נשאר `imagectl`), בכותרת מסכי הסוכן ובאפליקציית האימות (issuer).
- **מסך השרת בעברית, גרפי** (#1203): המסך שרואים כשמחברים מסך לשרת מצויר עכשיו כמו שאר המסכים — מצב כללי (ירוק / דורש טיפול עם מה קרה, מה ההשלכה ומה לעשות / ממתין לחיבור מהראשי), ארבעה אריחים, שורת מצב ומקשים; F2 (הגדרות), F9 (איפוס סיסמה) ו-F12 (אתחול/כיבוי) בחלונות בעברית. שדות הסיסמה באנגלית בלבד (המקלדת ננעלת על אנגלית, Caps Lock מוצג). ב-F12 המיקוד מתחיל על "ביטול" — Enter אקראי לא מאתחל, Tab ואז Enter מבצע (גם במסך הטקסט). מסך הטקסט באנגלית נשאר רק לשרת בלי מסך גרפי (בלי DRM/fbdev), והוא אומר למה.

### תוקן
- **מתקין, שרת משני — "בדוק חיבור" יכול להיות ירוק, ו"הבא" מחכה לו** (#1310, הכרעת נדב 12/10): השדה "כתובת השרת הראשי" מקבל IP או שם בלבד (גם `https://HOST:8443` עובד), ומתחתיו "כתובת ה-IP של השרת הראשי, כפי שהיא נראית מאתר זה". ירוק מציג את שם הראשי, הגרסה וטביעת האצבע של המפתח שלו; אדום אומר מה נכשל (timeout, נדחה, אין נתיב, TLS, "זה אינו שרת ImageSrv ראשי"). "הבא" מושבת עד ירוק, שינוי בכתובת מחזיר ל"לא נבדק", והמנוע מסרב להתקין משני בלי בדיקה ירוקה. עד היום הבדיקה שאלה נתיב שלא היה קיים, ונכשלה תמיד.
- **הראשי עונה "אתה שם?" על 8443:** מאזין נפרד על כרטיס השרתים של הראשי, נתיב אחד (`GET /api/interserver/v1/alive`) שמחזיר רק שם, גרסה וטביעת אצבע — בלי כניסה, בלי פעולות ובלי הקונסולה. חומת האש של הראשי פותחת 8443 על כרטיס השרתים; בין האתרים זה החריג היחיד לכלל "הראשי תמיד יוזם" (חומות האש של המעבדה עודכנו, `tools/lab/fw-ta`, `tools/lab/fw-haifa`).

## [v0.71.0] - 2026-10-11

### שונה
- **קצב השידור נקבע לבד:** פתיחה ב-600m (גם בשרת שהותקן מה-ISO, שעד היום שידר בלי הגבלה), עלייה של 100m רק אחרי גל נקי לגמרי (אפס שידורים חוזרים, כל כונן עמד בקצב, והזרם עצמו הגיע ל-80% מהיעד לפחות דקה), ירידה ברבע על שידורים חוזרים או לפי כונן איטי מוכר; תקרה 900m, רצפה 150m. אותו חדר בסבב חדש פותח מהקצב שעבד. שורת החדר אומרת למה ("עלה — הגל הקודם נקי"). הפצה ישירה ממחשב בנייה פותחת ב-600m ויורדת לפי כונן איטי.

### תוקן
- **עדכון שחזר לגרסה הקודמת מחזיר גם את קובצי האתחול** (#1287).
- **משני שנתקע לא תוקע את הראשי** (#1286): תקרת זמן לכל בקשה, ולזרם — לכל מנה (זרם איטי שמתקדם לא נחתך).

## [v0.70.0] - 2026-10-11

### נוסף
- **התקדמות סבב על פני גלים** (#989): "גל N · נכתבו X מתוך Y · נשארו Z · משוער: עוד K גלים" בקונסולה ובגואי; בסוף גל כשנשאר מה לכתוב — "החליפו דיסקים והעירו את מחשבי השיכפול" עם כפתור השכמה. מחשב שחזר עם דיסקים חדשים מצטרף לאותו סבב.

### אבטחה
- **SSH ופורט 22 יצאו מהמוצר** (#1266): בלי מתג SSH, בלי dropbear ב-initramfs וב-ISO, בלי כלל 22 בחומת האש, בלי טביעת מפתח במגירת מחשב. הקוד נמחק, וטסט שומר נכשל אם משהו מזה חוזר. כלי הטכנאי של המעבדה עבר ל-overlay מחוץ לריפו.

## [v0.69.0] - 2026-10-11

### נוסף
- **שרת משני מהראשי, כל הדפים והלשוניות** (#1179): בחירת שרת בעץ — אותם דפים, לשוניות וצמתים כמו בראשי (אותו קוד, הנתונים דרך הממסר החתום). שינוי משתמשים, הגדרות ורשת במשני מהראשי, עם יומן בשני השרתים; כשל = "לא נשלחה" או "לא ידוע אם בוצע". מחשבי המשני במוניטור של הראשי (#1008).
- **חיפוש גלובלי** (#554): תוצאות מקובצות (אימג'ים, מחשבים, משתמשים, שרתים, דפים), מקלדת, ו"סנן בעמוד הנוכחי".
- **זרימת הפצה מלאה בקונסולה:** בחירת אימג' לפי תיקיות, יעד נגזר מהסימון, שורה חיה של הגל, תוצאת דרייברים לכל דיסק, ו**תשובה לשאלת SMART מהקונסולה** (#989) — הראשון שעונה קובע.
- **שמות לחריצים ולכוננים** (#704): בהגדרות מחשב השיכפול — SATA לפי חריץ, NVMe/USB לפי כונן; השם מוצג בקונסולה, בגואי וביומן.
- **קצב שידור שלומד מהכוננים** (#447): לעולם לא מעל התקרה, לא מתחת לרבע ממנה, ורק אחרי שני גלים שבהם כונן האט.
- **מחשב מהקונסולה:** אתחול מרחוק ועריכת MAC (#977); שלבי הקליטה, נפח אחרון ומצב NTFS (#980).
- **הודעה בעברית כשאין גישה ל-github.com** (#1281).

### שונה
- **כיתות ו-PXE proxy מסורבים בשרת** כל עוד הכיתות כבויות (v1), גם בנתיבי הסוכן.
- **מסך השרת:** בלי הקלדת מילה (F9/F12 — חלון אישור), ובלי "הפעלה מחדש של אשף ההתקנה".
- **ב-v1 נתמכת רק התקנה מה-ISO** (#332).

### תוקן
- **מאזין בין-שרתי בלינוקס** לא נסגר בכיבוי הפורט; קבצי mktemp לא נמחקו (#1285); TFTP נצמד ל-loopback כשאין כרטיס (#1078).


## [v0.68.0] - 2026-10-11

### נוסף
- **תעודת TLS ושם המארח בהגדרות** (#153): העלאת תעודה ומפתח עם בדיקה לפני שמירה (PEM, התאמת מפתח, תוקף, והשרת באמת עולה איתם); הזוג הקודם נשמר. שינוי hostname לפי RFC 1123, ואימות שהמכונה החזירה את השם החדש.
- **"השלם הגדרה" לכרטיס השני ברשת ההפצה** (#1274) — כשההחלפה הצליחה אבל ההשלמה נכשלה.

### שונה
- **עץ הניווט כמו במוקאפ:** "תרשים" הוא צומת משלו תחת "רשת"; תפקיד עם קבוצה אחת מציג את המחשבים ישירות (בלי "מחשב בניית אימג'ים" באמצע); קליק אחד פותח וסוגר — בלי דאבל-קליק.

### תוקן
- **כפתור העדכון: לחיצה כפולה על "החל"** (#1273) כבר לא דורסת "רץ" ב"נכשל"; השרת מסרב לעדכון שני בזמן שהראשון רץ.

### אבטחה
- **שני נתיבי בדיקת סיסמה עוקפים את הגבלת הניסיונות** (#1282): פתיחת סבב מהתחנה ו-/agent/pulls עוברים עכשיו דרך אותו מסלול כמו הכניסה; כשמתג הכיתות כבוי — סירוב לפני בדיקת הסיסמה.
- **#742:** route עם כמה מתודות נכנס לרשימת ההיתר רק אם כולן ברשימה; טריאז' code-scanning (#291).

### אבטחה
- **‏SSH יצא מהמוצר** (#1266, הכרעת נדב 10/10: "כל מה שקשור לפורט 22 אמור להיות מחוץ לקוד"). אין מתג SSH בקונסולה, אין שורת 22 בדף הפורטים, אין טביעת מפתח בכרטיס המכונה, ואין `/api/console/ssh`. חומת האש של השרת אינה פותחת 22 בשום מצב (`--ssh-if` נדחה), וה-initramfs וה-ISO אינם אורזים dropbear או מפתח טכנאי. בעלייה השרת מוחק את הגדרות המתגים הישנות ואת טבלת מפתחות ה-host. `imagectl.debug` אינו מוגש מ-PXE לעולם.

### נוסף
- **overlay של מעבדה/אתר ב-initramfs** (#1266): `IMAGECTL_EXTRA_OVERLAY=<dir>` מעתיק עץ קבצים מעל האימג' לפני האריזה, ו-`<dir>/.binaries` אורז בינאריים מהבונה. hooks ב-`/etc/imagectl/hooks.d/*.sh` רצים אחרי DHCP, בסוכן ובמתקין החי. המוצר אינו יודע מה הם עושים. גישת הטכנאי של המעבדה עברה לשם, מחוץ לריפו.

## [v0.67.1] - 2026-10-11

### תוקן
- **בטיחות — "פתח סבב" בלי דיסק מסומן נחסם גם בגואי של מחשב הבנייה** (#1275, הכרעת נדב 11/10). התיקון נכתב לפני v0.66.0 אבל הקומיט לא נכנס למיזוג; בלעדיו סבב מהספרייה בלי סימון כתב לכל המגירות. עכשיו גם הגשר מסרב לשלוח רשומה בלי בחירה.

## [v0.67.0] - 2026-10-11

### שונה
- **הקונסולה מיושרת למוקאפ המאושר, עמוד אחר עמוד:** מסך כניסה (שדות בקופסה, כללי סיסמה בשתי עמודות), כותרת עליונה ועץ ניווט, כותרות עמוד ולשוניות, סקירה (אריח "דיסק אדום"; "לא נקרא" אינו 0), אימג'ים (סדר עמודות ומונים בלשוניות), יומן, משתמשים והרשאות, מגירה צפה, דוק משימות שנפתח בתוך הדוק.
- **חלונות אישור בנוסח אחד:** שאלה קצרה, הנושא, רשימת השלכות עם אייקונים וכפתור אדום — בכל הפעולות ההרסניות.
- **מגירת אימג':** "פתח סבב עם אימג' זה" ככפתור ראשי.

### נוסף
- **"החשבון שלי" כדף בעץ הניווט.**
- **מוניטור בתוך הדף:** רשימה וכרטיס צפייה זה לצד זה; "פתח בחלון נפרד" נשאר. בלי מסך ריק — הסבר כשהמוניטור כבוי או שהמחשב לא מחובר.

## [v0.66.0] - 2026-10-11

### נוסף
- **שני כרטיסים ברשת ההפצה** (#1264): כרטיס שני (רשות) בלשונית "רשת הפצה" — שניהם פעילים על אותה כתובת (bond0, balance-alb), חלוקת עומס ונפילה שקופה, בלי הגדרה בסוויץ'. השרת קורא בחזרה את מצב ה-bond ומחזיר את ההגדרה הקודמת אם האימות נכשל; "לא נבדק" אינו "פעיל". דורש ifenslave (נכלל ב-ISO).
- **דרייברים לפי דגם בסבב** (#1269): מתג "להוסיף דרייברים", דגם לכל הדיסקים או לכל דיסק, מתוך החבילות שבשרת — בגואי של מחשב הבנייה ובקונסולה. דגם בלי חבילה = כשל גלוי, לא דילוג בשקט. אין "אוטומטי" (יגיע עם הכיתות).
- **בחירת דיסקים לסבב בקונסולה** (#1275): גריד כמו בגואי — כרטיס לכל מחשב שיכפול, תגית לכל דיסק, "בחר / נקה הכל", "N נבחרו".
- **עברית בשדות הגואי** (#1245): Alt+Shift או לחיצה על "עב/EN"; משתמש/סיסמה/שם מחשב נשארים באנגלית.
- **דף הפורטים לפי רשת** (#1267): הפצה / ניהול / בין שרתים; חומת אש של השרת לכל פורט שבין רשתות (פתוח/חסום/לא נבדק), "בדוק עכשיו" ו"הצג פרטים". בלי מתגי פתח/סגור.
- **"שירותים" בדף הבריאות:** עצירה (חלון אישור) והפעלה של TFTP/8080/8081/8443.
- **עיצוב הקונסולה — שלב 3:** בריאות, הגדרות (עדכונים, גיבוי, מראה), יומן, משתמשים, היסטוריית סבבים, מסך מלא במוניטור, לשונית "דגמים" בדרייברים.

### שונה
- **גואי ומתקין באותם טוקנים כמו הקונסולה:** רדיוסים, כפתורים, שדות, טבעת פוקוס, תגיות וצללים; טסט שוויון נכשל אם צבע משתנה בצד אחד בלבד.
- **CLAUDE.md, עיקרון 7:** פעולה הרסנית מאחורי חלון אישור — אין הקלדת מילה בשום מקום.

### תוקן
- **בטיחות — גואי מחשב הבנייה** (#1275): בסבב מהספרייה הסימון בגריד לא נשלח והסבב כתב לכל הדיסקים. עכשיו הסימון נשלח תמיד, סימון פגום מסורב, ובלי סימון אין סבב.
- **כפתור העדכון** (#1193): כשל אחרי ההורדה (תג, יחידות systemd) מחזיר אוטומטית לגרסה הקודמת ולא משאיר קוד חדש על יחידות ישנות.

## [v0.65.0] - 2026-10-11

### נוסף
- **איפוס סיסמה למשתמש אחר** (#1268): בתפריט המשתמש "איפוס סיסמה…" — חלון אישור, סיסמה זמנית שמוצגת פעם אחת עם העתקה, והמשתמש בוחר סיסמה חדשה בכניסה הבאה. "שנה סיסמה" לעצמך עבר ל"החשבון שלי". למנהל שנוצר בהתקנה: רק איפוס סיסמה וניתוק הפעלות.
- **עמודת "מתאים לדיסק" בספריית האימג'ים:** הדיסק הקטן ביותר מהשוק (128GB עד 4TB) שהאימג' נכנס אליו, ו"נדרש לפחות X GB". בלי נתון — "—".

### שונה
- **אין הקלדת מילה לאישור בשום מקום** (#1270): מחיקת אימג', עצירת סבב, מחיקת משתמש, אחסון, מוניטור ועוד — חלון אישור עם ההשלכות וכפתור אדום. גם בגואי במכונה: שחזור, עצירת סבב וכלים. ה-API לא השתנה.
- **MFA: אין "אל תבקש קוד בדפדפן הזה"** — כל כניסה מבקשת קוד; עוגייה ישנה מתעלמים ממנה.
- **גואי במכונה:** כניסה כמו בקונסולה (תוויות מעל, שדות ממוסגרים), בוחרי אימג' לפי תיקיות (שחזור וסבב), דיסק נבחר בגריד בולט (טבעת, ✓, "N נבחרו"), "קלוט בלי כיווץ" ככפתור משני.

### תוקן
- **גואי:** שמות אימג' בלטינית בשורה עברית לא מתהפכים; גיל "עודכן" במילים; אפס אזהרות קומפילציה.

## [v0.64.0] - 2026-10-11

### נוסף
- **כרטיס רשת בשלושה מצבים** (#1263): הפצה / לקוח DHCP / סטטי עם שני DNS. ברשת ההפצה מגדירים רשת+מסכה, טווח וזמן חכירה, וכתובת השרת נגזרת (המארח הראשון). בלי gateway/DNS ובלי proxy ברשת ההפצה. בטבלת הכרטיסים "תיאור" (ניתן לעריכה) ו"תפקיד" (נגזר) הן עמודות נפרדות.
- **הקונסולה בעיצוב המוקאפ שאושר — שלבים 1 ו-2:** מסגרת חלון אחת לכל החלונות, שורת משימות+סטטוס אחת, תרשים רשת, ספריית אימג'ים לפי תיקיות עם תפריט שורה, פסי ניצול אחסון (תפוס/סך הכול ו-%), סקירה עם אריחי שירות ואימג'ים אחרונים, וכרטיסי חשבון.
- **הגואי במכונה לפי המוקאפ שאושר:** תפריט מחשב הבנייה, המתקין בעמודה אחת עם שורת מקשים, ספירת דיסקים שנמצאו בקלונר ובסבב, משוב לחיצה ותבנית אחת לפעולה הרסנית.

### שונה
- **שדות שם משתמש וסיסמה משמאל לימין** בכל מקום, "הצג" בצד ימין בתוך השדה בסגנון אחד, וכללי הסיסמה כעיגולי ✓ ירוקים.
- **מצב כהה:** כחול פעולה כהה יותר ובדיקת ניגודיות.

### אבטחה
- **המנהל שנוצר בהתקנה לא ניתן למחיקה, להשבתה או להורדה** (#1262), גם בשרת וגם בקונסולה.

### תוקן
- **הסקירה** מציגה כישלון קריאת אימג'ים ככישלון ולא כספרייה ריקה; קיבולת הספרייה סופרת רק מיקומים מחוברים; "לא נמדד" ו"הבדיקה נכשלה" הן שתי הודעות שונות.
- "1000 Mbps" לא מתהפך ב-RTL; עמודת שם האימג' שומרת רוחב קריא; אזהרת הכרטיסים היתומים חזרה לתרשים הרשת.
- הנתיב הישן של DHCP מסרב ל-DHCP/proxy גם על כרטיס הניהול.

## [v0.63.0] - 2026-10-10

### שונה
- **מחשב בנייה — חלון הכיווץ** (#928): "כווץ וקלוט" הוא התשובה הראשית היחידה; קליטה רגילה בלי כיווץ היא קישור משני עם הסבר.
- **ביקורת ממשק מלאה של הקונסולה** (528 פריטי מלאי, 341 נבדקו): כפתור ☰ מכווץ את עץ הניווט במקום מגירה בצד הנגדי; שורת הסטטוס וכותרת העץ נגזרות מ-/health ולא ירוקות קבוע; חלונות הם דיאלוג אמיתי (פוקוס פנימה, Tab כלוא, חזרה לפותח); המגירה נגמרת מעל שורת המשימות; כפתור "רענון" אחיד בכל הדפים; "+ פעולה" רק בדף שיש לו פעולות; החיפוש בסרגל מסנן גם טבלאות; שורש שביל הניווט = שם השרת; אייקון האימג'ים חוזר לתיקייה.

### תוקן
- **monitor.html** מושך את console.css ואת הטוקנים בגרסה הנוכחית — דפדפן שזכר אותם הציג את המוניטור בעיצוב ישן.
- נקודות הסטטוס הזיזו את "API" ב-6px; טבלת הפורטים יצאה מגבולות הכרטיס.

## [v0.62.0] - 2026-10-10

### נוסף
- **איפוס סיסמת admin במסך השרת, F9** (#1249). לא צריך את הסיסמה הישנה, כי הנוכחות הפיזית היא ההרשאה. מקלידים RESET, ואחריו סיסמה חדשה פעמיים לפי מדיניות הסיסמה. כל הסשנים נסגרים, ה-MFA ונעילות הכניסה מתאפסים, והאיפוס נרשם ביומן. האיפוס הישן לאדמין/אדמין בתפריט F2 הוסר.
- **בדיקת מקום בשרת לפני הכיווץ** (#1248): סירוב אם התפוס גדול מהפנוי, ואזהרה אם הוא גבולי, עם שני המספרים. כשאי אפשר לבדוק: "לא נבדק".
- **גרסת הסוכן מוטבעת ב-initrd** (#1257). הגואי מציג "סוכן vX · שרת vY", ואם הן שונות: "ה-initrd ישן מהשרת". השרת שומר את הגרסה שלו במטמון, ולא מריץ `git describe` כל 2 שניות.
- **אחוזים בזמן קליטה** (#1254): "נקלט… N% · מחיצה K מתוך M", לפי ההתקדמות שכלי ההעתקה (partclone) מדווח.

### שונה
- **ניתוק בחוסר פעילות: 10 דקות** במקום 5 (#1243). התיבה "זכור ל-7 שעות" הוסרה ממסך הכניסה, כי לא עשתה כלום. במסך ה-MFA הנוסח מדויק: "אל תבקש קוד בדפדפן הזה ל-7 שעות".
- **"דיסק מכווץ" לא מופיע ב"דורש טיפול" בזמן קליטה שרצה** (#1253), אלא ב"מה קורה עכשיו". כשאי אפשר לבדוק: "לא נבדק".
- **מסמכי הריפו** (#1246): ‏`NadavOked/ImageCtl` מתואר עכשיו בכל המסמכים כריפו הציבורי שלנו. נוסף README ל-install/ ול-installer/.

### תוקן
- **מקשי ה-DCUI על tty1:** ‏Enter ו-Esc התקבלו רק כמספרים, ולא כתווים (`get_wch`).

## [v0.61.0] - 2026-10-10

### נוסף
- **ריסטרט אוטומטי בסוף ההתקנה** (#1250): ספירה לאחור של 90 שניות במסך "ההתקנה הושלמה", עם "הפעל מחדש" מיד או "בטל". לפני הריסטרט מוציאים את המדיה, וכישלון בהוצאה לא עוצר את הריסטרט.
- **פאנל "משימות אחרונות" כמו ב-vCenter** (#1255): התחלה, משך שמתעדכן, ויוזם, במקום "-" קבוע. ‏`/tasks` מחזיר `created_by`. שם היוזם לא נחשף במסלול של הסוכן.
- **ארבעת ממצאי העיצוב שאושרו:**
  - עמודת פעולות דביקה ותפריט "⋯" בטבלאות רחבות. הפעולה ההרסנית בתחתית ובאדום.
  - הסימון בעץ עוקב אחרי הלשונית.
  - אייקונים ייחודיים בעץ. "אימג'ים" נשאר תיקייה.
  - כותרת הסקירה בשורה אחת, ו"דורש טיפול" מקופל.

### שונה
- **קליטה: תיקיית יעד חובה** (#1251). השרת מחזיר 400 על קליטה בלי תיקייה, ובגואי ובקונסולה אין יותר "ללא תיקייה". אימג'ים ישנים ממשיכים לעבוד.
- **דשבורדים:** כותרת אחידה ומספרים מיושרים בסקירה, בבריאות, באחסון, ברשת, במוניטור ובחדר ההפצה.

### תוקן
- **הגואי הציג שרת וגרסה מקודדים**, "imagectl-srv · 10.10.10.1 · v0.48.1" (#1252). עכשיו מוצגים השם, הכתובת והגרסה האמיתיים, ו"לא ידוע" כשלא ידוע.
- **כרטיס מחשב הבנייה נשבר מילה בשורה.**

## [v0.60.0] - 2026-10-10

### נוסף
- **שאלת הכיווץ בקליטה היא חלון גרפי בגואי** (#928). עד עכשיו היא נפלה למסך טקסט. כפתורים בעברית: "כווץ וקלוט" / "קלוט בלי כיווץ" / "בטל קליטה". בלי תשובה, או כשהגשר נכשל, הקליטה מבוטלת ולא מכווצים בשקט. תקרת ההמתנה היא 30 דקות.
- **עיצוב "vCenter בעיצוב אפל" בקונסולה** (ביקורת מול הסקיל apple-design):
  - משוב מיידי בלחיצה (`:active`).
  - toast ומגירה עם כניסה ויציאה.
  - טוקני תנועה, ו-reduced-motion בכל קובץ CSS.
  - `:focus-visible` גלובלי.
  - גרירה בעכבר אחרי 150ms (במגע נשאר 400ms).
  - **F06:** החלון צומח מהכפתור שפתח אותו, והמגירה מחליקה.
  - **F09:** 6 גדלי גופן ב-rem במקום 13 בפיקסלים.

### שונה
- **מחשב בנייה: "קליטת אימג' חדש" ראשון בתפריט** (#1244). אחריו שיכפול מהשרת, שיכפול ישיר ושחזור, בגואי ובמסך הטקסט.
- **הוסרו מסך התחנה בדפדפן ופורט 8082** (#1247). זה שריד מלפני הגואי הנייטיב: לא היה בשימוש, והיה עוד פורט פתוח ברשת ההפצה. ‏`kiosk.py` נשאר, כי הסוכן צורך אותו ב-8080.

### תוקן
- **החלת כתובת לכרטיס רשת מהקונסולה נכשלה בשרת מה-ISO** (#1241). ‏ifup לא יכול היה לכתוב את `/run/network` תחת `ProtectSystem=strict`.
- **מעבר ויזואלי בדפדפן אמיתי**, 40+ דפים:
  - שגיאת רשת מוצגת כ"השרת לא עונה — בדוק שהוא דלוק ונסה שוב" ולא כ-"Failed to fetch", ונבדלת משגיאה שהשרת החזיר.
  - חצי העץ ב-RTL.
  - מגירה שנחתכה.
  - כפתור מחיקה אדום.
  - הפוקוס נשמר ברענון החדר.
  - מסך הכניסה וה-MFA.

## [v0.59.3] - 2026-10-10

### תוקן
- **באתחול הראשון אחרי ההתקנה אין יותר מסך `login:` של דביאן** (#1240). נמדד על VM מה-ISO של v0.59.2: במשך דקות, עד שה-DCUI עלה, tty1 הציג login ריק. עכשיו המתקין שומר את tty1, ו-firstboot מציג בו את ההתקדמות ("preparing the server - Stage A/B"). כישלון מוצג על המסך עם המצב, ההודעה ו-"Press Alt+F2 to log in on tty2".
- **כונן בלי serial מוצג "לא ניתן לזהות" גם בגואי הנייטיב ובקונסולה** (#1235, המשך #105). ‏`guistate.sh` כותב `-1` ולא `0`, שפירושו "נכתב". הגואי מציג שורת אזהרה במסך ההכנה ובמסך הסבב החי, והקונסולה מציגה הערה לכל חריץ עם `fresh === null`.

### תיעוד
- **"ענה לאחרונה" של שרת משני** (#1237). הפער כבר נסגר ב-#1017; השורות המיושנות ב-`branches.md` סומנו, ונוסף הטסט שהיה חסר: הקונסולה מציגה את הזמן ששמר השרת גם אחרי רענון.

## [v0.59.2] - 2026-09-25

### תוקן
- **כונן בלי מספר סידורי כבר לא מוצג "נכתב"** (#105). במחשב הבנייה (מסך הטקסט והקיוסק) כונן ריק בלי serial הוצג תחת WRITTEN, וטסט אישר את זה. עכשיו יש לו מצב משלו: `fresh: null`, "לא ניתן לזהות את הכונן בחריץ N". במקביל, השרת מחזיר 400 עם הסבר כשבוחרים חריץ כזה כיעד, והיומן רושם `room_drawer_unidentified`.
- **שם המחשב בשחזור לינוקס** (#89, #107):
  - ב-btrfs, שורש המערכת נמצא לפי ה-subvolume שמכיל את `/etc`, ולא רק `@`. כשלא נמצא שורש או שנמצאו שניים, זה כשל שנקרא בשמו.
  - בדיסק dual-boot השם נכתב גם ל-Windows וגם ללינוקס, והדיווח מבחין בין "שניהם", "רק Windows", "רק לינוקס" ו"אף אחד".
- **טסט השאיר תיקייה זמנית בכל ריצה** (#1238). ב-`/tmp` של ה-Testrunner הצטברו 9,654 תיקיות, ובשער הופיע 507 מזויף.

### שונה
- **מסמכי `console-redesign` סומנו מול הקוד** (#1234). 60 שורות של "דורש API" מסומנות עכשיו "מומש ב-#N", והשאר מסומנות "פתוח: #N".

## [v0.59.1] - 2026-09-24

### תוקן
- **כפתור העדכון בונה עכשיו גם את ה-initrd של המכונות** (#1230). עד עכשיו `server-upgrade.sh` בנה אותו רק כשהקובץ `/etc/imagectl/initrd.flags` קיים, ואף רכיב לא כתב את הקובץ הזה. התוצאה הייתה שהשרת עלה לגרסה החדשה, בזמן שהמכונות המשיכו לאתחל initrd ישן.
  - סקריפט אחד, `tools/boot-payload-build.sh`, משמש עכשיו גם את ההתקנה (firstboot) וגם את העדכון. בכל ריצה הוא גוזר מחדש את הקרנל ואת ה-epoch ומעתיק את `vmlinuz`. הוא בונה לקבצי `.new` ומחליף את כולם יחד, כך שבנייה שנכשלה לא משאירה initrd קטוע.
  - קובץ הדגלים מכיל רק את מה שלא משתנה בין עדכונים.
  - בשרת ISO ישן שאין בו את הקובץ, העדכון בונה לפי ברירת המחדל של ה-ISO.
  - כשאין שום ראיה לאופן שבו השרת נבנה, העדכון חוזר לגרסה הקודמת ומדווח על כך.

## [v0.59.0] - 2026-09-24

### נוסף
- **עץ הניווט של שרת משני זהה לעץ של הראשי** (#1179, היקף חלקי). העץ כולל סקירה כללית, סבב הפצה, מלאי (אימג'ים, מחשבים, העברות) ותשתית (בריאות ושירותים, אחסון, רשת, מוניטור, דרייברים). עמוד שיש לו נתונים דרך ה-relay הקיים מציג אותם. עמוד שעוד אין לו נתיב ב-8443 מופיע בעץ עם "דורש API", ולא מוסתר. כשמשני לא עונה, הענף שלו אדום וכל עמוד כותב "‹שם› לא ענה" ואת הסיבה. "ניהול" עוד לא נכנס לעץ, ונתיבי ה-relay החדשים ממתינים להכרעה.

## [v0.58.1] - 2026-09-24

### תוקן
- **אישור הגדרת רשת כבר לא מדווח "אושר" כשסמן ההחזרה לא נמחק** (#1226). ‏`clear_pending` בלע כישלון מחיקה, והקונסולה החזירה `ok:true` ורשמה `net_confirmed`. בסוף החלון הזרוע החזירה את השינוי שלמפעיל נאמר שנשמר. עכשיו מוחזרת שגיאה 500 שאומרת שהשינוי יוחזר, בדיוק כמו שה-DCUI כבר בדק. נמצא בטריאז' של code-scanning (#291).
- **המתג של המוניטור מציג את מה שהשרת החזיר, לא את מה שביקשו** (#1227). כשהערך שחזר שונה, מוצגת שגיאה, וכשאין ערך מוצג "לא אומת".

## [v0.58.0] - 2026-09-24

### נוסף
- **מחשב הבנייה שואל בסוף קליטה: "חזור לתפריט" או "כבה"** (#409, הכרעת נדב מ-05/09). עד היום הוא כבה אוטומטית. כשאין תשובה או שאין מסך, הוא כבה אחרי 20 שניות, דרך `arm_wol`, כמו קודם. בתחנה (reboot) ובמחשב שיכפול (poweroff) לא השתנה דבר.

### תוקן
- **קליטה או סבב שהצליחו כבר לא מדווחים `failed`** כשה-WoL לא נדרך בסוף (#1222, #1224). ‏`finish_and_stop` חוזר במכוון, כדי שהמכונה תישאר דולקת (#587), ולכן הזרימה נפלה לנתיב הכישלון. השרת קיבל `done` ואחריו `failed`, ומחשב השיכפול הציג FAILED אחרי סבב שהצליח. עכשיו המכונה נשארת דולקת ומציגה "הושלם", ו"החלף דיסק" מציג הוראה לכבות ידנית.
- **המוניטור המרוחק לא מצייר יותר את סמן ברירת המחדל** (X בגודל 8x7) של LibVNCServer בפינת המסך (#1221).

## [v0.57.0] - 2026-09-24

### נוסף
- **מחשבי השיכפול בקונסולה** (#980):
  - **"כבה את כל המשכפלים":** ‏admin בלבד. דורש להקליד את שם הקבוצה, ונדחה (409) כשיש סבב פעיל. הבקשה יוצאת בתשובה ל-hello הקיים (`power_action`), בלי polling חדש. המכונה מכבה רק אחרי ש-WoL נדרך ונקרא בחזרה, ובקשה שלא נמסרה פגה אחרי 120 שניות.
  - היסטוריית סבבי החדר (`/room/history`).
  - תיקיית היעד של כל קליטה בדף מחשבי הבנייה.
  - **דורש פריסה גם של השרת וגם של ה-initrd.** סוכן ישן מתעלם מבקשת הכיבוי.

### תוקן
- **המוניטור המרוחק** (#850):
  - **הצבעים היו הפוכים.** השרת הצהיר על R@0, כשה-framebuffer הוא R@16. נמדד: על הבסיס `ServerInit shifts R@0 G@8 B@16`.
  - **נוספה בדיקת גבולות למיפוי ה-fb:** חשבון ב-64 ביט, ‏yoffset/xoffset מול `smem_len`, וגודל עד U16 כפי ש-RFB מגביל. גיאומטריה שגלשה ב-32 ביט גרמה לקריסה.
- **‏`/health/live` מדווח למה הגרסה לא נקראה** (#1159): `version_error` הוא קטגוריה קבועה, בלי נתיבים ובלי ה-stderr של git, כי ה-endpoint אינו דורש הזדהות. הסיבה המלאה נכתבת ליומן, וזמינה ל-admin ב-`/update` בשדה `current_error`.

### שונה
- **שלושה קבצי סוכן שישבו על קיר 300 השורות פוצלו** (#1217), בלי שינוי התנהגות. גופי הפונקציות זהים בית-בבית:
  - ‏`imagectl-agent` (248): הפונקציה `do_task` (קליטה ושידור ישיר) עברה ל-`lib/task.sh` (57). הלולאה הראשית ושני מסלולי השחזור נשארו.
  - ‏`lib/capture.sh` (266): נשאר `capture_disk`, שקורא את הדיסק. היעדים של התוצאה (העלאת המחיצה, המניפסט וסיבת הכשל) עברו ל-`lib/capturesink.sh` (42).
  - ‏`lib/restore.sh` (246): בדיקה 2.7 (`required_bytes`, ‏`disk_fits`) עברה ל-`lib/diskfit.sh` (60).

## [v0.56.0] - 2026-09-24

### נוסף
- **חדר המשכפלים מציג את קצב הזרם ואת מספר הבלוקים ששודרו שוב** (#989, סעיף 1). הנתונים נקראים מהלוג שה-`udp-sender` כבר כותב, בלי תעבורה חדשה ובלי polling. כשהשרת אינו המשדר, השדות ריקים ומוצגים "לא נמדד". בלוקים ששודרו שוב אינם בייטים שאבדו: הפרוטוקול שלח אותם מחדש ותיקן.
- **הרשאות ויומן** (#1008):
  - לכל משתמש מוצגת הכניסה האחרונה ומאיזו כתובת. משתמשים קיימים מוצגים "לא נרשמה" עד הכניסה הבאה.
  - אפשר לדפדף ביומן (`before=`) ולראות את הסך הכולל. כשהסינון רץ על טקסט מתורגם, הסך לא נמסר.
  - ייצוא ל-CSV (admin בלבד), עם BOM לעברית באקסל ובלי תאים שאקסל יריץ כנוסחה.

### תוקן
- ‏`tests/machines_page.test.cjs` לא נכשל עוד בין 00:00 ל-00:09 UTC (#1219).

## [v0.55.0] - 2026-09-24

### נוסף
- **מגירת האימג' מציגה את המניפסט עצמו** (#972): טבלת מחיצות (תפקיד, מערכת קבצים, גודל, בשימוש או "לא נמדד", כווץ מ-, sha256), מי קלט את האימג', ‏`boot_ca`, דחיסה, **תוצאת האימות האחרונה, שנשמרת בשרת** (`last_scrub`), ומספר הסבבים ב-30 יום (`rounds_30d`, כולל סבבי חדר ובלי ספירה כפולה של גלים). נקודת קצה חדשה: ‏`GET /api/console/images/{id}`.
- **אובייקט הרשת** (#1039):
  - מי מחובר לקונסולה ומאיזו כתובת (`GET /api/console/sessions`, ‏last_seen מתעדכן לכל היותר פעם בדקה).
  - חכירות DHCP לכל כרטיס, כולל מכונות שקיבלו כתובת ולא דיברו עם השרת, עם כפתור "רשום".
  - ההבחנה בין "טבלת הסוקטים לא נקראה" (`bind: null`) לבין "אף שירות לא מאזין".

### תוקן
- **ה-initramfs טוען את ספק ה-crc32c לפני ext4, ‏btrfs ו-xfs** (#1207): כל אתחול הדפיס "ERROR required modules did not load: btrfs xfs". המודולים היו ארוזים, אבל נכשלו בטעינה: ‏libcrc32c החזיר "unknown symbol" כשספק ה-crc32c עוד לא נטען, ו-busybox modprobe לא מכבד את ה-softdep. זה נמדד ב-QEMU בשינוי משתנה אחד.
- **ה-GUID הייחודי של כל מחיצה נבדק בלכידה ונקרא בחזרה בשחזור** (#1212): קריאה שנכשלה נבלעה ב-`2>/dev/null` ונרשמה כ-"", והשחזור נתן ל-sgdisk להמציא GUID חדש. ה-BCD של Windows מאתר את מחיצת המערכת לפי זוג ה-GUID (#26). הפטור של לינוקס נשמר.
- **אימות sha256 של אימג' במיקום אחסון נוסף** (#1215): היום הוא מחזיר 404. מיקום לא זמין מוצג "לא נבדק", ולא "sha256 לא תואם".
- **מסך החדר: מכונה ש"נעלמה" (lost) מוצגת "נעלם · לא ידוע אם נכתב" עם נורית כבויה** (#1213), ולא עם האחוז האחרון שלה.

### שונה
- **‏`agent/lib/smart.sh` פוצל לשניים לפני קיר 300 השורות** (#1211), בלי שינוי התנהגות: ‏`smart.sh` (130) נשאר הקריאה, הסיווג והמטמון — מה שה-hello והקיוסק צריכים — ו-`smartgate.sh` (177) הוא השער מול המפעיל: תפריט, ‏disk_event ומסך ההחלפה. גופי הפונקציות זהים בית-בבית.

## [v0.54.0] - 2026-09-23

### נוסף
- **המתקין הגרפי אפשר להשלים כולו מהמקלדת** (#1206): ‏↑/↓, ‏Tab ו-Shift+Tab עוברים בין כל הפקדים במסך, וכשהפוקוס נוחת על אפשרות רדיו היא נבחרת. רווח לוחץ על הכפתור שבפוקוס ומחליף DHCP/סטטי במקום, והפקד שבפוקוס מסומן בטבעת. הצורך נמדד על ESXi ב-23/09: הקונסולה של ESXi בדפדפן בולעת Tab, ובלי עכבר אי אפשר היה לשנות שום בחירה בתוך מסך.
- **כפתור "חזור" גם במסך תפקיד השרת** (#1205), כמו במסכים 3–6.

### תוקן
- **מחיקת אימג' שבשימוש בסבב פעיל נדחית (409)** (#784): ה-Issue נסגר ב-13/09, אבל התיקון לא נכנס ל-main, ומחיקה באמצע שידור הריצה `rmtree`. נמצא בביקורת ענפים.
- **יש עכבר במתקין על VM של ESXi** (#1204): ‏`psmouse` נארז ונטען ב-initramfs, ובבנייה עם GUI הוא מודול חובה.
- **השרת המותקן עולה בלי תפריט GRUB** (#1208, הכרעת נדב): ‏`GRUB_TIMEOUT_STYLE=hidden` ו-`GRUB_TIMEOUT=2`, ו-Esc מציג את התפריט.
- **‏CI ציבורי: `apt-get update` לפני התקנת חבילות** (#1210): ‏pytest על v0.53.5 לא רץ בכלל, כי האינדקס השמור ב-runner החזיר 404.

## [v0.53.5] - 2026-09-22

### תוקן
- **הדרישה מזנב הכונן היא עותק הגיבוי של ה-GPT, לא מגה-בייט מעוגל כלפי מעלה** (#1171, סעיף א', הכרעת נדב 22/09): הכלל היה "סוף הפריסה ועוד מגה, מעוגל למגה" — עד 2MiB בפועל. כונן פיזי אינו כפולה של מגה (‏`256,060,514,304 mod 1MiB = 352,256`), ולכן פריסה שנגמרה עד **1.32MB** לפני סוף היעד נפסלה למרות שהיא נכנסת; היא עברה רק דרך ההצמדה ל-`source_disk_bytes`, כלומר כל יעד קטן ולו בבייט אחד מהמקור נפסל. הדרישה עכשיו היא סוף הפריסה ועוד **256KiB** (עותק הגיבוי הוא 33 סקטורים: 16,896 בייט ב-512, ‏135,168 ב-4Kn); היישור של `sgdisk` הוא של המחיצות ואינו דרישה על גודל הכונן. **שלושת המימושים של הכלל משתנים יחד** — פייתון בשרת, ‏jq בסוכן ו-awk בקליטה — והטסט שמצליב ביניהם הוא שתפס את השלישי.

## [v0.53.4] - 2026-09-22

### תוקן
- **`tools/backup-server.sh --system-root`** (#1128): שלב קובצי המערכת קורא משורש נתון (ברירת מחדל `/`); הטסטים מצביעים על שורש מזויף ולא על `/etc` של המכונה שמריצה אותם — ב-CI הציבורי (ubuntu, לא root) `/etc/iscsi/initiatorname.iscsi` הפיל את שלושת טסטי הגיבוי.
- **"disk too small" אומר בכמה** (#1171, סעיף ב'): ההודעה מתחילה בהפרש ב-MiB ובבייטים — "קטן ב-2 MiB (1,744,896 בייטים)" — ולא בשני מספרים של 12 ספרות. הפסילה עצמה נשארת (עיקרון 4); הכיווץ במקום פסילה ממתין להכרעה.

## [v0.53.3] - 2026-09-22

### נוסף
- **גיבוי ושחזור של השרת** (#1128, עיצוב R41): **"הורד גיבוי הגדרות"** בכרטיס הגרסה בקונסולה (מנהל; `GET /api/console/update/backup`) — tar.gz של ה-DB (עותק עקבי דרך ה-backup API של sqlite, לא `cp`; ‏`integrity_check`=ok כראיה) ושאר תיקיית הנתונים, בלי אימג'ים, עם `MANIFEST.txt` ו-`SHA256SUMS` ואזהרת סודות לפני ההורדה. `tools/backup-server.sh` (DB + נתונים + קובצי המערכת של ImageCtl + אימג'ים ב-rsync בלי `--delete` ואימות מול המניפסטים; `--no-images` לשעתי; `umask 077`), `tools/verify-images.py` (sha256 לכל מחיצה; אפס אימג'ים אינו פאס), `tools/restore-drill.sh` (גיבוי → שחזור לזמני → שרת שני על loopback כ-`nobody` → `/health/live` + מספר אימג'ים + verify). סעיף "גיבוי ושחזור" ב-`docs/guides/server-install.md` (RPO, cron, סדר שחזור לשרת חדש). `rsync` נוסף לחבילות השרת; בלעדיו הכלי מעתיק ב-`cp` ואומר זאת.

### תוקן
- **`idle_touched` בלי `-nt`** (#434): ‏`-nt` אינו מוגדר ב-POSIX sh (SC3013 — ה-shellcheck הציבורי האדים על v0.53.2); ‏`find -newer` עם אותה סמנטיקה.
- **אירוע היומן של העברה בין-שרתית נכתב לפני המצב הסופי**: ה-CI הציבורי (3.13) תפס פעם אחת `failed` בלי `storage_transfer_failed` — מי שרואה מצב סופי רואה גם את האירוע.

## [v0.53.2] - 2026-09-22

### נוסף
- **כיבוי אחרי חמש דקות המתנה — לעולם לא במסך כשל** (#434; נדב 05/09): מכונה שממתינה לאיש (`poll_sleep`) בלי פעילות `IDLE_POWEROFF_S` (300; ‏0 מבטל) מקבלת ספירה לאחור של 30 שניות על המסך ("כל מקש מבטל") ונכבית דרך `arm_wol`; מקש בגואי (`touch`), תשובת hello שהשתנתה, סבב פתוח, משימה או `stay_on` מאפסים. מסך כשל אינו מגיע לשעון כלל. בלי WoL — נשארת דולקת, בשם.
- **מטריצת deploy על כל נתיב קונסולה** (#555): נמנית מהאפליקציה — deploy מקבל 403 על כל 66+ הנתיבים בקונסולה; בקיוסק בדיוק ה-allowlist, כשקליטה ותיקיות חדשות נשארות למנהל; בלי כניסה 401, לא 403.

### תוקן
- **שרת שיש בו sshd נסגר ללולאה המקומית כבר בהתקנה** (#729): ה-ISO בא בלי sshd (#1148); ‏setup-boot-server כותב את ה-drop-in של המתג ריק מכתובות ומטעין מחדש.

### תיעוד
- **`docs/imagectl-spec.md` גרסה 1.2** (#1137): v1 כפי שנמסר — היקף v1/v1.1/v1.2 בראש, ראשי/משני, 3 וילנים ורשת הפצה מהקונסולה, תפקידים לפי דלת, כניסה ו-MFA, אחסון, המתקין החי וכפתור העדכון, מה הוכח ואיפה; כיתות בנספח; חמשת המשפטים המטעים של R56 הוחלפו.

## [v0.53.1] - 2026-09-22

יום הבוקר אחרי v0.53.0: מה שהציבורי תפס, ומה שנסגר בזמן שנדב מתקין. **patch**: אין מסך חדש; הלוגו והקצב הם תיקוני תצוגה.

### תוקן
- **shellcheck בציבורי** (v0.53.0 היה אדום שם): `wait`/`! cmd`/`CDPATH=`/`n_grub` — ‏`n_grub` הפך לשומר "ערך GRUB יחיד"; ‏`build_initramfs` קורא את `find` מקובץ (גם תחת dash של הטסטים).
- **הפרסום לציבורי איבד כל `+x`** (עותק ווינדוס, `core.fileMode=false`): הקלון הציבורי v0.53.0 לא יכול היה לבנות ISO. `publish-to-public.sh` משחזר את המודים מה-ref הפרטי; טסט עם מראה מקומית.
- **הודעות קרנל מעל ה-DCUI** (#1195): `kernel.printk 3 4 1 3` בהתקנה, וכלל ה-`imagectl-drop` ב-nftables ב-`level info`.
- **סבב שנכשל הוא מצב, לא `running`** (#854, #1126): ‏`failed` + סיבה ב-sessions וב-room_rounds; `send_failed` מעביר לשם, חברים מקבלים "אין זרם" ב-hello הבא ועולים מהדיסק; החדר לא פותח גל אוטומטי אחרי כשל שולח; hello מצטרף לגל ב-CAS; מגירה שהוחלפה בין done לספירה לא נספרת (serial נקשר כשקיים; סוכן ישן ממשיך להיספר); מצב `closing` שהשעון מתעלם ממנו; הקונסולה: "נכשל: <סיבה>" + "פתח מחדש". **הסימולציה** קיבלה שולח מדומה מפורש (`--sender-simulate`, בסימולציה בלבד) — עד עכשיו היא "עברה" רק כי כשל השולח נבלע.
- **הקשחות R36/R39/R40** (#1127): logout מותר במצבי ביניים; תקרת 100 תווים לשדות טקסט; `self_probe` בלי לקוח; ‏hello: 64KB/עומק 8 → 413; ‏GRUB מסרב לתווי מטא ב-`server_base`; כתיבות סטטוס עדכון שנכשלו נרשמות; מיגרציה בטרנזאקציה אחת.
- **מצב ה-Secure Boot שהסוכן מודד נשמר** (#500): ‏`firmware`/`secure_boot` ב-net_devices, חוזרים מ-`/machines` ו-`/net`; NULL = לא דווח. העובדה החסרה ל-#61.
- **55 אירועי יומן בלי תווית עברית** (#292): כולם קיבלו תווית, וטסט מצליב כל `journal(conn, "…")` מול הטבלה.
- **הגואי: done עם אזהרה בכתום** (#943), **ולוגו המכללה בכותרת** (#1168) — נמשך פעם אחת מפורט הסוכן (`/api/v1/agent/branding/logo`), PNG בלבד, אחרת ה-brandmark.
- **קליטה בלי `used_bytes` מציגה קצב** (#408): הגואי מודד MB/s משתי קריאות מצב; אין מסך קפוא.
- **`install/firewall-rules.md` מתאר את מודל הפורטים של v1** (#999): socket לכל תפקיד, בלי סינון נתיבים, בלי כיתות; חומות האש של המעבדה הן מימוש ההתייחסות.

## [v0.53.0] - 2026-09-21

המתקין החי על מסך השרת (הכרעות נדב 21/09) — קצה-לקצה על ESXi: ISO → גואי → התקנה מהמדיה → "הסר את המדיה והפעל מחדש" → firstboot מהתשובות → DCUI → כניסה לקונסולה עם הסיסמה שנקבעה. ‏minor: יכולת שהמפעיל רואה.

### נוסף
- **מתקין חי משלנו על ה-ISO** (#1190, #1188, #1189): ה-ISO מעלה את ה-initramfs שלנו (`imagectl.mode=installer`) ומציג אשף גרפי נייטיבי (Pango/fbdev, RTL) על מסך השרת: דיסק (טבלה, "יימחק", "אני מבין", אתחול מקומי) → תפקיד → **כתובת השרת** (DHCP/סטטי לפי כרטיס) → שם השרת → חשבון ניהול (שם משתמש נבחר, "הצג" לשתי הסיסמאות) → סיכום → התקדמות → סיום עם "הסר את מדיית ההתקנה" ואז ריסטרט. אין debian-installer ואין preseed; ‏`installer/` (מנוע POSIX sh + `gui-bridge.sh`) ו-`tools/build_initramfs.sh --installer`. ‏firstboot: `tools/iso/firstboot-answers.sh` — ‏setup-boot-server מהתשובות, אימות המטען, חותמת, DCUI; בלי האשף בדפדפן. החוזים ב-`docs/architecture/interfaces.md`.
- **בריאות: מצב "לא ישים"** (#1148): בדיקות שתלויות ברשת ההפצה אפורות עד שהיא מוגדרת; שרת טרי = פריט אחד "הגדר את רשת ההפצה". שורות SSH רק כשמתג דלוק, וכלל 22 ב-nftables רק לכרטיס שהמתג שלו דלוק. הטקסט "SSH 22" ירד מהמתקין.
- **הגואי הקטן: `hello_age`/`hello_rc`/`smart_pending`** (#1154) — צד הסוכן ל-#1090.
- **ISO: build-iso מוכיח שה-pool מכסה את packages.txt** (#1162) לפי שדה `Package`; ‏verify-boot-payload בודק את `initrd.img.gui` (#1161); ‏`imagectl-wizard-rerun.service` ל-DCUI, והתנאי חזר ל-`imagectl-wizard.service` (#1163).

### תוקן
- **initrd: סגירת softdep** (#1169): `libcrc32c` בלי ספק crc32c — ext4/xfs/btrfs לא עלו על ברזל; ‏`modules.softdep` נארז, capability names נפתרים דרך `modules.alias`, ומודול שנכשל נרשם ב-`/run/imagectl/modules.failed`.
- **הסוכן: אמת בכישלון** (#1124, #1130): ‏grow.sh לא מסמן done אחרי ntfsfix/umount שנכשלו (הדגל המלוכלך נאמר), ‏hostname.sh על מניפסט שנכשל, ספירת plan מאומתת; ‏BitLocker/stage_drivers בצינור, watchdog בזמן attended, kill של wrapper בלבד, arm_wol נכשל→כיבוי, expand.sh שומר GPT attrs.
- **המתקין החי — מה שנמצא בארבע הריצות על ESXi** (#1190): busybox `sh` מריץ את ה-applets שלו בלי קשר ל-PATH (‏dpkg-deb/dpkg/tar/chroot) — ‏dash+GNU tar נארזים, המנוע ו-debootstrap רצים תחת dash; ‏grub: `--removable`+`--force-extra-removable` סותרים, efivarfs ליעד, מחיצת bios_grub; ניקוי מה ש-debootstrap השאיר מורכב; ‏umask 022 ליעד (‏`_apt` לא קרא את ה-repo המקומי); ‏`dbus`/`ca-certificates`/`systemd-timesyncd` (‏debootstrap אינו d-i standard); ‏ifup אחרי setup-boot-server; **סיסמת המנהל הייתה `admin_pass=...` מילולית** — הערך נחלץ; פוקוס הגואי עוקב אחרי המסך; ISO מעבדה (`--source`) עובר בלי `.git` לפי `source_git` במניפסט.
- **MFA: ה-otpauth URI לא מוצג; הסוד הידני מאחורי "הצג"** (#1177; נדב).
- **תרשים הרשת: משיכת תפריט מהשרת עצמו/MAC אפס לא יוצרת מכונה** (#1178; נדב); שורות MAC-אפס קיימות נמחקות בעלייה.
- **אשף ההתקנה בדפדפן** (#1191; נדב — נשאר לשרת בלי מסך): "הצג" לסיסמאות, "החל" נעלם בזמן הריצה, "מה קורה עכשיו" במקום החלון השחור, סיום גם כשה-handoff מחזיר 404.
- **הודעת הסיום של המתקין** אומרת איזה משתמש נוצר והאם הסיסמה נקבעה בהתקנה (#1160).

## [v0.52.2] - 2026-09-21

### תוקן
- **כפתור העדכון: "החל" רץ כולו מחוץ לסנדבוקס של השרת** (#1193): מבחן העדכון של נדב (שרת מ-ISO v0.52.0) מצא v0.52.1 ונפל על `git fetch` — `.git/FETCH_HEAD: Read-only file system` (‏`ProtectSystem=strict`). ‏fetch/checkout עברו ל-`server-upgrade.sh` (‏systemd-run); ‏`/opt/imagectl` נשאר לקריאה בלבד לתהליך הווב. השרת של 21/09 (0.52.0) לא יכול לעדכן את עצמו — ההתקנה הבאה מה-ISO.
- **דיאלוג העדכון בלי הקלדת שם השרת** (#1192; נדב): אישור/ביטול; ודיאלוגי המחיקה מקבלים את שם השרת האמיתי (דרשו `127.0.0.1`).
- **`imagectl-dcui.service` לא עלה על דביאן 13** (#1189): ‏`ReadWritePaths=/var/lib/dhcp` (אין — dhcpcd) → 226/NAMESPACE, 156 ניסיונות בשקט; ‏`Type=exec` כדי ש-firstboot יראה כישלון אמיתי. ‏firstboot: 169.254 = "no dhcp offer".
- **המתקין (ביניים, עד #1190): בחירת דיסק + אישור מחיקה תמיד; ISO עולה ישר להתקנה** (#1189; נדב: "לשרת מחוברים 3 כוננים").

### תיעוד
- מעבדת ה-ESXi: המעקף (DNAT 8081) ירד משתי חומות האש — הלנובו עם נתיב סטטי, כמו במכללה; ‏Testrunner: ניקוי `/tmp` לפני כל ריצה.

## [v0.52.1] - 2026-09-20

### תוקן
- `tools/iso/firstboot.sh`: משתנה לולאה לא בשימוש (shellcheck SC2034 — הציבורי v0.52.0 אדום ב-shellcheck). זה גם התג הקטן שמשמש למבחן כפתור העדכון v0.52.0 → v0.52.1 (#1185).

## [v0.52.0] - 2026-09-20

ההתקנה הראשונה מה-ISO בידי נדב — ESXi 8, UEFI + Secure Boot — ומה שהיא לימדה. כל הסעיפים מהיום, בסדר שבו נראו.

### נוסף
- **תפריט האתחול של ה-ISO הוא של ImageCtl** (#1180; Codex): שני ערכים בלבד (שרת / שרת משני), כותרת `ImageCtl <תג> installer`, צבעי הקונסולה, `timeout=-1`; ערכי דביאן (Graphical install / Install / Advanced) אינם מוצגים. התבניות ב-`tools/iso/*.cfg.in`, ו-`build-iso.sh` מאמת שנותרו בדיוק שניים.
- **ה-DCUI עולה לפני האשף, כמו ב-ESXi** (#1181; Codex): שלב ב' מדליק DHCP זמני על כל כרטיס עם קישור, ומציג על tty1 מסך טרום-התקנה — כתובת לכל כרטיס, הקישור לאשף, F2 לכתובת סטטית זמנית. עד היום שרת שה-DHCP נכשל לו בהתקנה עלה **בלי שום כתובת**, והאשף האזין למי שאינו יכול להגיע אליו.

### תוקן
- **כפתור העדכון עובד משרת שהותקן מה-ISO** (#1185): ה-ISO נושא את `.git` של ה-clone מהציבורי (עם התג), `git` מותקן תמיד ונמצא ב-pool. עד היום: "לא ידועה (אין תגית git על העץ)" ו-"No such file or directory: 'git'" — התקנת ISO לא יכלה לעדכן לעולם. כלי חסר נקרא בשמו, לא ב-Errno.
- **"החל" באשף רץ ברקע** (#1184; Codex): `POST /apply` מחזיר 202 מיד, הדפדפן רק שואל `progress` (עם backoff עד 5 דק'), ניתוק בזמן הגדרת כרטיס הניהול מוצג כ"מתחבר מחדש…" ולא כ"ההחלה נכשלה" — ההתקנה של הבוקר הצליחה בזמן שהמסך אמר Failed to fetch.
- **הודעות המתקין על המסך באנגלית** (#1182; Codex): ה-VT הציג את העברית כ-♦♦♦; הפלט המפורט של בניית ה-initramfs עבר ל-`/var/log/imagectl-firstboot-build.log`.
- **אשף: הטביעה נשברת בתוך הכרטיס; כרטיס לא-מודלק אינו "מנותק"** (#1183; Codex): "לא מודלק" / "אין קישור" / "מחובר".

### תיעוד
- מעבדת ה-ESXi: seeds של FW-TA ו-FW-Haifa (`tools/lab/fw-ta`, `fw-haifa`) — nftables לפי R42 והכרעות 18–19/09, DHCP עם reservation לשרת (#1088), `fw-update on|off` ל-443; Testrunner עם החבילות החסרות וניקוי `/tmp` לפני כל ריצה.

## [v0.51.2] - 2026-09-20

### נוסף
- **מסך ההמתנה של המשכפל מציג את הדיסקים המחוברים** (#1170; נדב, ברזל 20/09; Codex): שורה לכל פורט SATA — דגם, גודל, SMART — או "ריק"; מחשב בנייה: "דיסק פנימי"/"מגירה"; `disk_probe` → ניסוחי #402.

### תוקן
- **כפתור "הצג" בשדה הסיסמה עבר לימין** (#1167; Codex): במסך RTL הוא בתחילת השדה; הטסט מודד דיו בתוך תיבת הסיסמה (הגרסה הראשונה של הטסט עברה גם כשהכפתור היה משמאל — הוחלפה).

### תיעוד
- פנקס האימותים: מסלול ד' — שיכפול ישיר על ברזל (20/09, ◐: תוכן זהה למקור בכל 4 הדיסקים; HP1 עלה פעם לאובונטו אחרי done).

## [v0.51.1] - 2026-09-20

### אבטחה
- **ה-ISO נבנה מהריפו הציבורי, לא מהפרטי** (20/09): `build-iso.sh` משכפל את `NadavOked/ImageCtl` בתג (`--public-url`), ושומר מסרב לארוז נתיבים פרטיים (`tools/agents`, `.agents`, `logs`, `docs/research`…) גם עם `--source` מקומי. ה-ISO הראשון של v0.51.0 נשא 62 תשובות מחקר וכלי סוכנים ב-`imagectl-src/` — נבנה מחדש.

### תוקן
- `tests/health_ports_page.test.cjs`: תאריך ה-fixture של "בדיקה אחרונה" מחושב (היום UTC) — נפל ב-20/09 על 19/09 קשיח.

### תיעוד
- מסירה `docs/handoffs/handoff-2026-09-20.md`; `tools/agents/report-check.py` (Jev — פיילוט בדיקת דוחות).

## [v0.51.0] - 2026-09-20

### נוסף
- **אשף התקנת שרת ראשון (#910, חלק 1)** — שירות HTTPS זמני על 8081, שישה מסכי RTL לפי המוקאפים המאושרים, בחירת תפקיד ורשת ניהול, hostname וחשבון `admin`, החלה עם פלט חי ושער `verify-boot-payload`, טביעת TLS והעברה לקונסולה. ה-ISO מפעיל את האשף אחרי בניית ה-initrd במקום להריץ את המתקין ללא מפעיל; סיסמת המנהל עוברת ב-FD ולא ב-argv.
- **מסך השרת הפיזי DCUI על tty1 (#910, חלק 2)** — ממשק curses אנגלי 80×25, מסך מצב לקריאה, F2 מוגן בסיסמת `admin` לרשת ניהול/איפוס/שירותים/TLS/אשף, ו-F12 עם הקלדת hostname. שינויי רשת משתמשים במסלול הקונסולה ובחלון ההחזרה של 60 שניות; יחידת systemd עצמאית ממשיכה לעבוד גם כשהשרת למטה.

### תיעוד
- README: "מה מנהל הרשת מגדיר בסוויץ' ההפצה" — וילן, port security, IGMP querier, DHCP snooping, לא 802.1X (נדב 19/09: הכבל אינו של הקוד; #1079).

## [v0.50.1] - 2026-09-19

### תוקן
- **הגואי: שעון קבוע ב-`--png`** — שני רינדורים משני צדי דקה נבדלו ב-9 פיקסלים והפילו `test_class_deploy_switch` אקראית (נתפס בגייט br). על המסך האמיתי השעון רץ.

### תיעוד
- **מוקאפי אשף ההתקנה וה-DCUI (#910)** (Fable; נדב 19/09): 6 שלבים, רק רשת הניהול, "שרת ראשי"/"שרת משני", אפס SSH; 5 מסכי DCUI ב-ASCII (`docs/design/dcui/`, פרטי); `console-redesign/install.md` עם 4 שאלות לנדב.
- **README — "רשת ופורטים" בלי ההשוואה ל-FOG/Clonezilla** (נדב 19/09), מיושר ל-`ports.py`: 5900 מוניטור, SSH לתחנות ככלי טכנאי, "אין SSH על שרת הייצור" (#1148), טווח המולטיקאסט 9000–9001 גם בטבלת החסימה.

## [v0.50.0] - 2026-09-19

### נוסף
- **ISO של המתקין — דביאן 13 + ImageCtl, offline, BIOS+UEFI/Secure Boot** (#1139, לפי R25/R57–R63; Opus): `tools/iso/` — `packages.txt` (איחוד כל רשימות החבילות, נאכף ב-`test_iso_build`), `make-pool.sh`, `preseed.cfg` (מוחק את הדיסק הראשון שאינו USB, בלי שאלות), `late-command.sh`, `firstboot.sh` + יחידת systemd (בונה שני initrd ומריץ את המתקין עם `--servers-if` בלבד — רשת ההפצה מהקונסולה, #1088), `build-iso.sh`, `test-iso.sh` (QEMU/OVMF). תפריט ה-ISO: "Install ImageCtl" / "Install ImageCtl Secondary" (`imagectl.role=secondary`), בלי טיימר. **בלי `openssh-server`** — SSH אינו חלק מהמוצר (נדב 19/09). נבנה והותקן מקצה לקצה ב-QEMU עם Secure Boot במעבדה; Hyper-V/ברזל — עדיין לא. `docs/guides/server-install.md` §"התקנה מ-ISO".
- **הגואי הקטן (מחשבי בנייה/שיכפול) בשפת העיצוב של הקונסולה, על כל המסך** (#1090; Codex gpt-5.6-sol + Opus): `theme.c` קורא את טוקני `design-tokens.css` אחד-לאחד (בהיר מ-`:root`, כהה מ-`:root[data-theme="dark"]`), header 48 כמו ה-app-shell, שורת סטטוס 36 עם מקשי הקיצור, כניסה כמו הקונסולה, תפריט 3/4 עמודות, חדר 6/4 עמודות עם פס לכל דיסק, משכפל — 3 כרטיסים על כל הגובה + SMART 2×3 בעברית, שחזור עם גריד אימג'ים ואישור בשם המכונה, ו-`SCREEN_STANDBY` חדש עם חיווי hello (`hello_age=`/`hello_rc=` בקובץ ה-state — interfaces). ‏R44: שורה בלי `
` נדחית, חריגה מ-`MAX_*` מדווחת, mtime > 10 שניות = "הסוכן לא מעדכן מצב". כפתור "כלים" נשאר מאחורי `menu_tools` (v1 כבוי). `test_cloner_gui`/`test_native_gui_theme` לעיצוב החדש.

### תוקן
- **‏#1150 — מסך הגדרת ה-MFA בלי QR** (נדב 19/09, מעבדה; Opus):
  ‏`POST /me/mfa/setup` מחזיר גם `svg` — QR של ה-`otpauth_url` inline דרך
  ‏`qrcode.image.svg.SvgPathImage` (‏interfaces §MFA). ‏`python3-qrcode` (דביאן
  13: 8.2) נוסף למתקין ול-`requirements.txt`. כשהחבילה חסרה: ‏`svg: ""`, יומן
  ‏`mfa_setup` "python3-qrcode חסר בשרת", והקונסולה מציגה הודעה אדומה במקום
  QR — לא שקט ולא קריסה של הכניסה. ‏`?v=` 9.6.

## [v0.49.0] - 2026-09-19

### נוסף
- **מסך חדר השיכפול: זמן שעבר, קצב, הערכת סיום ו-"עודכן לפני N שניות"** (#410; Opus): `room.py:_pace` — `rate_bps`/`eta_s` לכל מגירה, `started_at`/`elapsed_s`/`rate_bps`/`eta_s` לסבב (`None` ≠ 0); `roomdraw.sh` (Elapsed/Rate/ETA + Updated), ‏`roomflow.sh` רענון אוטומטי (`read -t`), הגואי הנייטיבי מרענן כל שנייה. ‏interfaces §2–§3.
- **"מגירות=0" מבחין בין "לא חוברו דיסקים", "אין פורטי SATA בקושחה" ו-"לא נבדק"** (#402; Opus): עמודה `net_devices.disk_probe` מה-hello, מוחזרת ב-`/net`, `/machines`, `/room`; הקונסולה מציגה את הסיבה במקום "0 דיסקים".
- **רשומת Boot#### לווינדוס המשוחזר על UEFI** (#433; Opus): `efibootmgr` ב-initramfs, ‏`agent/lib/bootentry.sh` נטען מהסוכן ורץ מ-`stage_drivers` — יצירה בלבד (`-C`, לא מקדים את ווינדוס ב-BootOrder), קריאה חוזרת, `BootNext` פעם אחת; בלי efivars = `skipped: no_efivars`. כשל = `done` עם שגיאה, לא `failed` (אחרת לולאת שחזור, #856). ‏interfaces §5, §12.
- **מסך GRUB לדיסק המקומי מבחין "אין דיסק" מ-"אין מערכת על הדיסק"** (#416; Opus): `chain_local` עם `probe --driver (hd0)` ו-`ls` על המסך; 5 פירורי `LOCAL_STEPS` ב-`boot/trace.py` (`local-no-disk`/`local-search-empty` ב-`/api/console/net`).

### שונה
- **סבב `running` יתום בעליית השרת → `failed` בשם** (#411, הכרעת נדב 06/09; Opus): `server/reconcile.py:reconcile_on_start` — סבב מולטיקאסט בלי משדר חי: חברים לא-סופיים `failed` ("השרת עלה מחדש — המשדר אבד"), הסבב `closed`, יומן `session_orphaned`; קליטה `running` → `failed`, או `done` אם המניפסט כבר בספרייה. תחת `_write_lock`+`writing` (#54).
- **מחשב שיכפול = Legacy BIOS כדרישת רכש** (#391, #419; Opus): `docs/imagectl-spec.md`, טקסט מסך ה-Legacy ("Only the BIOS boot order keeps them from booting…") בשני העותקים, צ'קליסט התקנה ב-`docs/guides/server-install.md`.
- קונסולה `?v=` 9.5.

### תוקן
- **#1149 — המתקין נפל בכל הרצה, גם `--dry-run`: `IMAGECTL_STORAGE_ARGS: unbound variable`.** הערה בתוך heredoc לא-מצוטט (`override.conf`, מאז #734) הכילה
  `$IMAGECTL_STORAGE_ARGS` מילולית, ותחת `set -u` זה כישלון. נמצא בהתקנה
  מ-ISO (#1139) ב-QEMU. ‏`\$` מוברח, ו-`tests/test_installer_heredocs.py` בודק
  סטטית שכל משתנה שמורחב בתוך heredoc לא-מצוטט מוגדר בסקריפט.
- **`tools/build_initramfs.sh`: קובץ חסר עוצר את הבנייה, ושתי בניות = אותם
  בייטים** (#1125, סקירה R33; הדפוס של #33/#78/#84). ארבעה מקומות שבהם
  קובץ חסר נבלע ויצא 0: הלודר ו-`libnss_*` (`cp … 2>/dev/null || true`),
  `modules.order`/`modules.builtin` (`|| true`), תת-עצי `MODULE_SUBDIRS`
  (`if [ -d ]`) ו-`FIRMWARE_DIRS` (`if [ -d ]`) — כולם נאספים ונקראים בשם,
  ‏`exit 1`. אין רשימת "אופציונליים": כל 20 העצים קיימים בקרנל דביאן 13
  (נמדד). האריזה reproducible (מזין #1078): `find -print0 | LC_ALL=C sort -z
  | cpio --reproducible | gzip -9n`, וכל mtime מוצמד ל-`SOURCE_DATE_EPOCH`
  (‏`--source-date-epoch`, הסביבה, או `git log -1 --format=%ct`; בלי אף אחד
  — עצירה, לא `date`). ‏dlopen ל-GUI נמדד ולא הונח: 0 טעינות דינמיות
  ברינדור 32 מסכים ב-chroot — אין מה להוסיף. `tests/test_build_initramfs_gates.py`.
- **המשך R27/R47 — רשת ההפצה מהקונסולה, המתקין וסקריפט השדרוג** (#1121, #1131; Opus):
  `deploy_net.complete()` apply→commit (כשל = 500, שום דבר לא נרשם, ניסיון חוזר עובד);
  nftables מהקונסולה דרך candidate והחלפה אטומית; יחידת restart ייחודית; DHCP מלא רק על
  כרטיס ההפצה אחרי `source=console` (409) + נעילה; `ipv4_of` scope global/לא deprecated;
  הדוגם שורד חריגה, rebind עם אסימון דור. המתקין: חומת אש **אחרונה** עם החזרה בכשל,
  `write_file` אטומי עם גיבוי תמיד, "כבר מותקן" + `--force`, רשת הפצה מהקונסולה נשמרת
  בהרצה חוזרת, סיסמה ב-fd 3, סינון שמות כרטיסים. `server-upgrade.sh`: סבב פתוח, אימות
  אחרי restart (`is-active` + `/health/live` בגרסה + payload) ו-rollback לתג הקודם עם
  `failed` ב-DB. חדש: `GET /api/console/health/live`, `install/console-live.sh`. interfaces §19, §28.

## [v0.48.1] - 2026-09-19

### שונה
- **אזהרה אדומה לפני כיבוי של כל פורט עם מתג, לא רק 69** (נדב 19/09; Opus):
  `ports.WARNINGS_HE` + `warning_for()`, ‏`/health` נושא `warning_he` בכל שורת
  מתג (‏interfaces §16), והקונסולה מציגה אותה (`data-testid="port-warning"`)
  בשני נתיבי הכיבוי — הקלדת שם ו-API. ניסוח אחיד "כיבוי X עוצר את…".

### תוקן
- **‏#1120 (R44) — ‏MFA:** ‏`/login/mfa` מוגבל-ניסיונות, ‏challenge חד-פעמי,
  סיסמה חדשה מבטלת את כל ה-sessions, תקרת 128 לקוד.
- **‏#1122 (R45) — ערוץ בין-שרתי:** תקרת זמן ל-handshake, אימוץ תעודה
  מחודשת על אותו מפתח, סיבת סגירה למנהרה, גוף לא-תואם נדחה בשם.
- **‏#1123 (R47) — אחסון:** סיסמת SMB לקובץ credentials במקום ל-fstab,
  כתיבת fstab אטומית, validation לשמות רשת, ו-`connected` רק אחרי
  fstab→mount שהצליחו.

### אבטחה
- **‏#1129 (R43/R48) — `esc()` וגרשיים, ואישור כוח במוניטור בשרת.**
  `esc()` בקונסולה היה `textContent→innerHTML`, שממיר רק `&<>` — ו-esc()
  משמש ב-67 attributes; ערך עם `"` (שם שיתוף SMB/NFS מסריקה, IQN, LLDP)
  סגר את ה-attribute. עכשיו replace מפורש לששת התווים, ומזהי שרת בתוך
  `onclick="fn('…')"` עוברים `encodeId`+`decodeURIComponent`. ‏`?v=` 9.4.
  במוניטור: הפעלה-מחדש/כיבוי עוברים ב-`POST /monitor/{mac}/power`
  (+ דרך משני) עם `confirm` מול השם **מהטבלה** — לא `?name=` מהכתובת ולא
  ‏JS; הגשר מסרב ל-ClientCutText מהדפדפן (4403) ומעביר בגבולות הודעה;
  ‏Origin זר → 4403 לפני accept; יומן `monitor_connected`/`monitor_closed`/
  `monitor_power`; 10 דק' בלי תעבורה → 4408 ושחרור המנעול; תקרות ל-RFB
  בדפדפן (מלבן ≤32MB ובתוך המסך, ServerCutText ≤64KB, חוצץ ≤64MB).
  ‏interfaces §14.

### תיעוד
- README: 8080 בלבד גלוי (לא הקונסולה), התקנה עם `--servers-if` ובלי הפצה,
  הקונסולה על וילן השרתים (R56). 21 תשובות נוספות של הצי (R43–R63, כולל
  גל ה-ISO) ב-`docs/research/answers/` (פרטי).

## [v0.48.0] - 2026-09-19

### שונה
- **v1 יוצאת בלי ארגז הכלים** (הכרעת נדב 19/09; v1.1 = הכלים — #649, #1104,
  #928, ולכל כלי מוקאפ של החלון שלו לפני בנייה). דגל מהדורה `tools`
  ב-`server/capabilities.py` (ליד `classrooms`, #1081), `False`: הצומת
  "ארגז כלים" תחת תשתית והניתוב אליו נעלמים מהקונסולה, `GET /tools/catalog`
  ו-`PUT /tools/selection` עונים 404 בשם, ו-hello/`/state` נושאים `tools: false`
  → `guistate.sh` כותב `menu_tools=0` → הגואי הקטן אינו מצייר את כפתור
  "כלים" במחשב הבנייה. הקוד לא נמחק; `TOOLS = True` מחזיר הכול
  (‏interfaces §23).

### נוסף
- **פערי ה-API של העיצוב מחדש, חלק א'** (#968, #1000, #1017; Opus): `/me` עם `uptime_seconds` ו-`deploy_ip`;
  `severity` לכל רשומת יומן מ-`journal_he` (ההיוריסטיקה בצד הלקוח נמחקה); `folder` ב-`/tasks`;
  `POST /update/check` שומר `last_check`; `/health` נושא `X-Health-Checked-At`; `POST /storage-nodes/{id}/check`
  + `last_seen_at`/`last_error` מכל קריאה בין-שרתית — "ענתה לאחרונה" מזמן השרת. interfaces §29.
- **מתג ל-TFTP 69 בדף הפורטים** (#1013; Opus): `enable-tftp` עבר מקובץ המתקין לקובץ שהשרת מרנדר;
  אזהרה לפני הקלדת שם השרת: "כיבוי 69 (TFTP) עוצר את ה-PXE: מחשבי בנייה ושיכפול לא יעלו מהשרת".
  שרת קיים עם הקובץ הישן — "אין מתג" בשם, לא "כבוי" מזויף.

### תיעוד
- 22 סקירות ועיצובים של הצי החינמי (R21–R42) ב-`docs/research/answers/` (פרטי); 13 Issues מאומתים
  (#1120–#1128); `interfaces.md` §2 — בלוק `monitor_secret` כפול שנשאר ממיזוג #1077 הוסר.

## [v0.47.5] - 2026-09-19

### תוקן
- **הציבורי אדום על v0.47.4**: חמשת טסטי ה-evdev של #949 דורשים `/dev/uinput` (root);
  סמן pytest `uinput`, וב-`tests.yml` הם רצים בצעד נפרד תחת `sudo -E` עם אותו אינטרפרטר.
  במעבדה (root) — ריצה אחת כמו קודם.

## [v0.47.4] - 2026-09-19

### תוקן
- **מוניטור: הסמן עוקב 1:1 אחרי העכבר בדפדפן** (#949). ‏`monitor.c` יוצר
  התקן uinput **מוחלט** (‏`EV_ABS`, טווח = מידות הפריימבאפר) במקום עכבר
  יחסי, ו-`native-gui` ממפה התקן מוחלט בלי `BTN_TOUCH` ישירות לפיקסלים
  בלי להסתיר את הסמן. "העכבר קופץ" על מחשב הבנייה (17/09) — הסמן של
  הגואי הצטבר בנפרד מהמיקום שהדפדפן שלח.

## [v0.47.3] - 2026-09-18

### שונה
- **ריצה מלאה בגייט המעבדה מריצה גם את סימולציית הקצה-לקצה** (`lab-run-tests-remote.sh`),
  אחרי pytest ירוק — הפרצה שהולידה את v0.47.2 נסגרה. ריצה ממוקדת נשארת pytest בלבד.

### תוקן
- `test_port_listeners`: המתנה לעליית מאזין 15 ש' ולא 5 — runner עמוס של GitHub (3.12)
  עבר את 5 ונפל "timed out waiting"; עדיין נופל בקול על תלייה אמיתית.

## [v0.47.2] - 2026-09-18

### תוקן
- **סימולציית הקצה-לקצה** (`tools/e2e_simulation.py`, רצה רק ב-CI הציבורי) נשברה
  בשקט בשלושה מקומות מאז #1073/#1085: סיסמה שלא לפי הנוהל, admin שנדרש לרישום
  MFA, ו-deploy שנדחה בקונסולה. עכשיו היא עוברת אותם כמו דפדפן — רישום MFA
  ב-HTTP (TOTP), deploy נכנס דרך הקיוסק ונבדק כ-403 בשם בקונסולה, מטריצת
  ההרשאות לפי #1073. 132 ✓ במעבדה ובתחנה.

## [v0.47.1] - 2026-09-18

### תוקן
- **הציבורי היה אדום** (v0.46.0/v0.47.0): `nft -c` פותח netlink גם בבדיקה יבשה ודורש
  CAP_NET_ADMIN — ה-runner של GitHub אינו root; במעבדה (root) לא נראה. בלי root → `sudo -n`,
  בלי שניהם → דילוג בשם (לא "עבר"). וגם SC2120 במתקין (`list_nics` עם ארגומנט מת, #1088).

## [v0.47.0] - 2026-09-18

### נוסף
- **כרטיס וילן השרתים = לקוח DHCP, ורשת ההפצה מוגדרת מהקונסולה** (#1088; הכרעת נדב
  18/09): `--console-host` (וגם `--interserver-host`) מקבל **שם כרטיס** — השרת פותר
  את הכתובת בעלייה ו**מאזין מחדש** כשה-lease מחליף אותה (דוגם כל 10 ש', יומן
  `bind_address_changed`; בלי כתובת עדיין — loopback בלבד עד שתגיע). המתקין שואל
  קודם על כרטיס השרתים (DHCP ברירת מחדל, `hostname` באופציה 12; סטטי = בחירה),
  ועל כרטיס ההפצה — **או "לא עכשיו"** (ברירת המחדל במסירה): אז `IMAGECTL_URL` ריק,
  dnsmasq כבוי, grub.cfg לא נכתב וחומת האש פותחת רק את וילן השרתים. השרת עולה
  במצב "רשת ההפצה לא הוגדרה" (סוכן וקיוסק על 127.0.0.1; שורת בריאות + הערה בדף
  הרשת), וההדלקה הראשונה של DHCP מהקונסולה — אחרי כתובת סטטית על הכרטיס, שנאכפת
  ב-409 — **משלימה את ההתקנה**: רושמת `deploy:interface`/`deploy:url`, כותבת
  grub.cfg, מפעילה dnsmasq, מחוללת מחדש את חומת האש עם הכרטיס ומאתחלת את השרת.
  `GET /api/console/net/deploy`; שורת בריאות "כתובת השרתים: … (DHCP, פג ב-…)"
  מקובץ החכירות של dhclient. תעודת הקונסולה — SAN של הכתובת הנוכחית + שם המארח,
  ואינה מתחדשת בחילוף כתובת (תקפה לפי השם). pairing לפי FQDN — מאומת שהיה כבר
  אפשרי (הפין הוא SPKI, בלי כתובת). `docs/architecture/interfaces.md` §28.
- **ארגז הכלים נארז לפי הבחירה בקונסולה** (#1050, PR #1103; Opus): `build_initramfs.sh
  --tools-selection <קובץ>` מכניס ל-initrd רק את הבינארים של הכלים שסומנו בדף
  "ארגז כלים" (`agent/lib/toolbins.sh` — 25 מזהים → בינארים; מזהה זר עוצר את
  הבנייה). בתחנה הבחירה היא רשימת-היתר גם ל-`tools_list` וגם ל-`tools_run`;
  בלי קובץ בחירה — אין כלים (fail closed). מודולי d2/d4/d5/d6 גוזמו לקטלוג
  (‏40 כלים הוסרו, 9 נותרו); d3 (רשת) לא מוזג — אין לו כלי בקטלוג. תוקן: המסגרת
  לא העבירה את שם המחשב למודול — כל כלי rw/destroy נכשל rc 3. 16 מזהים נארזים
  אך עדיין בלי מודול הרצה — #1104.

### תוקן
- `test_cloner_gui` בנה את הגואי מרשימת מקורות קשיחה בלי `screens_tools.c`
  (9 כשלי לינקר בגייט); עכשיו `$(make -s print-gui-src)` כמו `test_headless_framebuffer`.
- טסטים שקיבעו `imagectl-agent == 300` (‏#1048/#1049/#1050) → `<= 300`; טסטי #1088
  עם סיסמה שאינה לפי נוהל #1085.

## [v0.46.0] - 2026-09-18

### נוסף
- **מסכי הכניסה החדשים** (#1085 שלב ב', PR #1098; Grok 4.6 התחיל, Opus השלים):
  חמישה מסכים לפי המוקאפ המאושר (`login-mockup-2026-09-18.html`) — כניסה בסגנון
  vSphere, החלפת סיסמה כפויה ל-`admin` הראשון, רישום MFA (URI + סוד להקלדה + 8
  קודי גיבוי), קוד אימות עם "זכור את הדפדפן 7 שעות", ושגיאות בעברית. הכניסה עברה
  לדלגציה על `#login` (רגרסיה שנתפסה ב-`branches_page.test.cjs` ותוקנה).
- **גריד הקלונרים** (#1033, PR #1097; Fable): מלבן אנכי למכונה, דיסקים מוערמים לפי
  SATA (דיסק 1 למעלה), שתי שורות לתיבה (`דיסק N` + GB) והשאר ב-tooltip; 12 מכונות
  × 3 דיסקים נכנסות ב-1440px בלי גלילה אופקית. רכיב משותף לאובייקט הקבוע ולחדר הסבב.
- **משיכת אימג' מהמשני אל הראשי (pull)** (#1071; Grok 4.6): יזום מקונסולת הראשי דרך
  8443 הקיים — המשני רק עונה `GET`; sha256 מאומת לפני הכניסה לספרייה, `id` קיים →
  409. משלים את הדחיפה ראשי→משני; **המשני לעולם אינו יוזם**.

### אבטחה
- **SSH בתחנות** (#1080/R20; Grok 4.6): dropbear נקשר ל-`$IP:22` של וילן ההפצה ולא
  ל-`0.0.0.0`; טביעת מפתח ה-host (ed25519) נשלחת ב-hello, נשמרת מגורסת ונכתבת
  ל-`<data_dir>/ssh/known_hosts` (אטומית — tmp + replace, ממצא סקירת Nemotron-3
  Ultra); כל SSH שהשרת פותח לתחנה רץ עם `StrictHostKeyChecking=yes`. בכרטיס המכונה:
  SHA256 מקוצר; שינוי באותו אתחול — כתום, מפתח חדש מאיתחול — אפור.

### תוקן
- `test_netprobe`/`test_probe` קיבעו `imagectl-agent == 300`; הקיר הוא `<= 300`
  (הנורה 280, #483).

## [v0.45.0] - 2026-09-18

### נוסף
- **כניסה כמו באלפון + MFA** (#1085 שלב א' + #1075, PR #1099; Grok 4.6): ההתקנה יוצרת
  `admin`/`admin` עם **החלפת סיסמה כפויה** במסך הראשון (כל API אחר 403 עד אז);
  נוהל סיסמאות מאלפון (≥8, אותיות, ספרות, תו מיוחד — הודעות בעברית), שם משתמש
  ייחודי ללא רגישות לאותיות; **MFA (TOTP, RFC 6238 ב-stdlib) חובה לכל admin שאינו
  המקומי** — מופעל בכניסה הראשונה (URI + סוד להקלדה, 8 קודי גיבוי), כניסה
  דו-שלבית, "זכור את הדפדפן 7 שעות"; ה-admin המקומי — בלי MFA, ניתן לשינוי
  סיסמה ולהשבתה; **הגבלת ניסיונות** (5 → השהיה, 10 → נעילה 15 דק') וביטול
  sessions; הקיוסק/מחשב הבנייה — סיסמה בלבד. המסכים החדשים — בגל הבא.
- **v1 בלי כיתות** (#1081, PR #1100; Grok 4.6): `capabilities.classrooms=false` קבוע —
  "כיתות" נעלם מהעץ, מלשונית המחשבים, מהתרשים, מטפסי הרישום ומתפריט מחשב
  הבנייה. v2 מדליק את הדגל. הקוד לא נמחק.

### אבטחה
- **המוניטור** (#1077/R20-F4, PR #1101; Grok 4.6): 5900 מקבל חיבור **רק מכתובת
  השרת**; אימות **אתגר-תגובה HMAC-SHA256** (`agent/hmac_sha256.c`) במקום שליחת
  הסוד; סוכן ישן (סוד גולמי) מסורב בקול; הסוד מתחלף אחרי כל חיבור; ברירת המחדל
  כבוי ("הדלקה חושפת 5900 על וילן ההפצה"). HMAC ב-C אומת מול וקטור פייתון על gcc.

## [v0.44.0] - 2026-09-18

### נוסף
- **דף "אחסון" תחת תשתית** (#1066 שלב ב', PR #1091; לפי המוקאפ שנדב אישר): רשימת
  מיקומים (מקומי / NFS / SMB / iSCSI) עם ארבעה מצבים — מחובר · לא נגיש · מנותק ·
  לא נבדק; "הוסף מיקום" → סוג → טופס עם "סרוק" ו-"בדוק חיבור" ("שמור ועגן" נדלק
  רק אחרי בדיקה מוצלחת); iSCSI בשני שלבים (פורטל → גילוי → IQN → התחברות; ואז "מה
  על הדיסק": עיגון בלי פירמוט / פירמוט בהקלדת ה-IQN / לא נקרא = בלי פירמוט);
  מגירת ניתוק; שורות בבריאות; אימג' על מיקום לא נגיש מסומן "לא זמין".
- **ערכת נושא לפי משתמש, נשמרת בשרת** (#1093, PR #1095; Grok 4.6): ברירת מחדל
  "לפי המערכת"; בחירה מפורשת (בהיר/כהה) נשמרת במשתמש ומוחלת בכל דפדפן שבו הוא
  נכנס; המתג בכותרת — מערכת → בהיר → כהה; לפני הכניסה — לפי המערכת.

## [v0.43.0] - 2026-09-18

### נוסף
- **מודל התפקידים ל-v1** (#1073, PR #1087): למשתמש הפצה **אין ניהול וובי** — הכניסה
  לקונסולה מסורבת ("משתמש הפצה עובד ממחשב הבנייה"), וכל נתיב ניהול חסום גם עם
  עוגייה מהקיוסק; במחשב הבנייה הפצה מקבל בדיוק שלוש פעולות — שיכפול למשכפלים
  מאימג' בשרת · שיכפול ישיר מהדיסק · **שחזור אימג' לדיסק הזה** (חדש: `selfrestore.sh`,
  אישור בהקלדת שם המכונה, רק מספריית השרת הזה) — ו-admin גם קליטה; "כיתה" מוסתר.
  #988 כללים 2/4 בוטלו. WoL למכונה/קבוצה — admin בלבד.
- **חומת אש לפי וילן במתקין** (#1074/R20-F1, PR #1084; Grok 4.6): `install/nftables-rules.sh`
  → `/etc/nftables.conf` (policy drop; הפצה: 67/69/8080/8082/מולטיקאסט; שרתים:
  8081/22; 8443 רק במשני וממקור הראשי; forward drop), `nft -c -f` לפני הפעלה,
  `--no-firewall`; הסוכן/קיוסק/TFTP קשורים לכרטיס ההפצה (0.0.0.0 רק במפורש);
  שורת "חומת אש" בפורטים ובבריאות. שדרוג של שרת קיים **אינו** מפעיל.
- **מיקומי אחסון — שלב א' (שרת)** (#1066, PR #1086; Grok 4.6): תיקייה / NFS / SMB /
  iSCSI כמיקומי ספרייה; hooks מוזרקים; יצירה רק אחרי "בדוק חיבור"; פירמוט מאחורי
  הקלדת IQN, דיסק שלא נקרא לא מפורמט; מנטר 60ש'; אימג' על מיקום לא נגיש = "לא
  זמין" וסבב מסרב; בריאות; המתקין מביא `open-iscsi nfs-common cifs-utils`. הדף — בגל הבא.

## [v0.42.1] - 2026-09-18

### שונה
- **תרשים הרשת לפי מודל האתרים** (PR #1070): הוילן "ניהול — קונסולה" → "שרתים —
  קונסולה", "בין-שרתים" → "שרתים — בין-שרתי"; השרת המשני מוצג "דרך FW · 8443
  בלבד" עם קו מקווקו — חיפה מאחורי FW משלו, הראשי יוזם.

### תיעוד
- **חוקי חומת האש בין האתרים** (`docs/architecture/interfaces.md` §18): ת"א — יוצא 8443 אל
  חיפה בלבד; חיפה — נכנס 8443 מהראשי בלבד; 8081/22 לא נפתחים בין האתרים;
  pairing מקומי. הסטייה במעבדה (FW2 מנתב פנימה) מתועדת — לתקן ב-ESXi.
- מחקרים: R19 (חיבור אחסון ב-vCenter/Proxmox/TrueNAS, #1066) ו-R20 (ביקורת
  אבטחת הפורטים, Codex GPT-6 Astra → #1074–#1080, כולם v1); מוקאפ "אחסון" אושר.

## [v0.42.0] - 2026-09-18

### נוסף
- **מתג ופורט בקיר, ובדיקת כבל — בכרטיס המכונה** (#1048, PR #1067; Grok 4.6):
  `agent/lldpsniff.c` (C יחיד, raw socket) מאזין 35ש' לפריים LLDP ומדפיס JSON
  (מתג, פורט, תיאור, TTL) — בלי `lldpd`; `agent/lib/netprobe.sh`: בדיקת כבל
  (`ethtool --cable-test`) **רק כשאין קישור**, LLDP, מטמון 15 דק' והפעם הראשונה
  ברקע. השרת שומר בתוך `machine_probe`; חיווי אדום לכבל פתוח/קצר; מתג שלא
  משדר = "לא נקלט", לא תקלה. הכרעת נדב: "זה חלק מהשרת, לא כלי לתכנאי".

### תוקן
- **תרשים הרשת — הטקסט גלש מחוץ לתיבות על הקווים** (#1064, PR #1065): `<text>`
  של SVG → `foreignObject` עם ellipsis ו-tooltip; `bind: SSH 22 · HTTP 8080/8082`;
  כתובות עד 2 + `+N`. אומת ב-1366/1100/375 ובמצב כהה.
- `lldpsniff`: בייט לא-ASCII בשם מתג מקודד `\u00xx` — אחרת ה-hello כולו נדחה.

### נוסף
- **כבל ומתג+פורט בכרטיס המכונה** (#1048): הסוכן מריץ `ethtool --cable-test`
  רק כשאין קישור (הבדיקה מנתקת), ומאזין לפריים LLDP אחד (`imagectl-lldpsniff`,
  timeout 35 שניות, ברקע כדי שה-hello הראשון לא יתעכב). מגיע ב-hello כ-
  `netprobe` ונשמר באותה שורת `machine_probe`. כבל פתוח/קצר הוא שער אדום;
  מתג שלא משדר אינו תקלה. בכרטיס: "מתג ופורט" ו"כבל", שלושת המצבים.

## [v0.41.1] - 2026-09-18

### תוקן
- **הציבורי אדום מאז v0.40.0** (PR #1062): `tests/test_tools_catalog.py` (ציבורי)
  קרא את `docs/tools/` והמחולל (פרטיים) — פעם שלישית ביום של אותו דפוס
  (המוקאפ של #828 ב-v0.39.0). הטסט פרטי, ו**שומר חדש**: כל נתיב-בריפו
  שטסט ציבורי נוקב חייב להיות ציבורי.

## [v0.41.0] - 2026-09-18

### נוסף
- **"בריאות המכונה" בכרטיס המכונה** (#1049 שלב ב', PR #1059): השרת שומר את
  ה-`probe` של הסוכן בטבלת `machine_probe` מגורסת (שורה חדשה רק כשהתוכן
  השתנה; שדות נדיפים — שעון, טמפ', מוני רשת — מעדכנים במקום), חושף ב-
  `GET /api/console/machines` (‏`probe`, `probe_seen_at`, `probe_verdicts`)
  ומציג 11 שדות בשלושה מצבים (נמדד / לא נבדק / לא הצלחנו לבדוק). **שישה
  חיוויים** — על סוללה · שעון CMOS סוטה (סוללת BIOS, מחשב 1 15/09) · NVMe
  (media errors / בלאי ≥90%) · קריסה באתחול הקודם · כפילות IP · שגיאות CRC —
  בכרטיס ובעמוד הבית. **מציגים בלבד**; עצירת סבב = הכרעה נפרדת.

### תוקן
- `machines_page.test.cjs` — "נראו ברשת היום" תלוי בשעון UTC בין 00:00–03:00
  (PR #1060; נפל במעבדה 02:35).

## [v0.40.1] - 2026-09-18

### נוסף
- **הסוכן נושא "בדיקת מכונה" (`probe`) ב-hello** (#1049 שלב א', PR #1057; Grok 4.6):
  חשמל/סוללה, סטיית RTC, מעבד+מיקרוקוד+פרצות, זיכרון (DIMMs/ECC), טמפרטורות,
  סטטיסטיקת NIC + כפילות IP, PCI בלי דרייבר, lockdown/taint, pstore, מפתח
  OEM, NVMe/SMART, הצפנה. כל שדה: נמדד · `null` (לא בדקנו) · `error`. במטמון
  `$RUN_DIR` עם `PROBE_TTL` — hello נשאר זול. השרת עדיין אינו שומר (שלב ב').

## [v0.40.0] - 2026-09-18

### נוסף
- **דף "ארגז כלים" תחת תשתית** (#649, PR #1052) — 25 כלים לאיש IT ב-3 קבוצות
  (מחיקה לפני מסירה · ווינדוס נעול · שחזור קבצים), נבחרו מ-160 מועמדים ב-11
  מקורות אחרי סיווג "מובנה מול כלי" (‏`docs/tools/`). לכל כלי ☐ למחשבי
  בנייה/שיכפול ו-☐ למחשב תלמיד, סיכון (ro/rw/destroy), גודל עם מקור, "סמן
  את המומלצים". השמירה (admin) ב-`settings` וב-`<data_dir>/tools-selection.json`;
  ‏`GET /tools/catalog`, ‏`PUT /tools/selection` (‏interfaces §23). האריזה
  לפי הבחירה — #1050.

## [v0.39.2] - 2026-09-17

### תוקן
- **הסוויטה הציבורית ביטלה את עצמה בשקט** (PR #1054): `timeout-minutes: 15` מול
  4,208 טסטים (~18 דק') — ארבע ריצות היום הסתיימו `cancelled`, שאינו אדום
  ולכן לא נתפס. התקרה 35 דק'.
- **main אדום אחרי #1045** (#1051, PR #1053): `docs/tools/` ו-`docs/research/
  tools-fleet-0917/` לא סווגו במניפסט הפרסום.

## [v0.39.1] - 2026-09-17

### תוקן
- **הציבורי אדום ב-v0.39.0**: `tests/test_native_gui_theme.py` (ציבורי) קורא את
  המוקאפ `docs/design/native-gui-mockup-2026-09-13.html` שסווג פרטי ב-v0.39.0.
  המוקאפ הוא המפרט שהטסט מאמת מולו את `theme.c` — נוסע איתו לציבורי.

## [v0.39.0] - 2026-09-17

### נוסף
- **מסכי מחשב הבנייה והמשכפל לפי המוקאפ של נדב (13/09)** (#828, PR #1035) —
  ה-GUI המקורי (`native-gui/`) מיושר לעיצוב החדש: מסכי חדר, כיתה, משכפל
  ושחזור (`screens_room/class/cloner/restore.c`), טסטים לפריסה ולערכת הצבעים.
- **דף הרשת בעיצוב החדש — גל 8 של #954** (PR #1040): תרשים, "חיבורים
  פיזיים" (כרטיס NIC מאוחד: בפועל מול מוגדר, פער, DHCP מהמצב החי) ו"רשת
  הפצה"; דרישות #761 נשמרו, הטסטים הותאמו למיקום החדש.
- **צומתי-האב בעץ (כיתות / בנייה / שיכפול) פותחים דף מחשבים ממוקד לתפקיד**
  (PR #1030) — בלי הלשונית "נראו ברשת"; כפתור המוניטור הוסר משורות/כרטיסים
  ונשאר רק בדף המוניטור (הכרעת נדב 17/09).

### תוקן
- **כיבוי מחלון המוניטור דורך Wake-on-LAN לפני `poweroff`** (#1027, PR #1031) —
  `monitor.c` קרא ל-`poweroff -f` ישירות ועקף את `arm_wol`; מכונה שכובתה
  מהמוניטור לא התעוררה.
- **ליטוש הקונסולה** (#1032, PR #1037) — שורות אינן זזות/גדלות ב-hover
  (בלי layout shift), שורת הסינון באותו מקום בכל הדפים (מחשבים, יומן),
  כפתורים כפולים הוסרו.
- **רשימת bind בדף הפורטים** (PR #1034) — שורה לכתובת, link-local ו-`[::1]`
  מקופלים ל-`+N`; טבלת TFTP לא מותחת את הדף לגלילה אופקית.
- **טסטים על מבנה ישן**: רשימת מקורות ה-GUI מ-`make print-gui-src`;
  `docs/design/native-gui-*` מסווג במניפסט הפרסום.

## [v0.38.1] - 2026-09-17

### תוקן
- **הבדיקה העצמית לא רושמת `unknown_mac` ביומן** (#1007, PR #1025) — כל כניסה
  לקונסולה רשמה "מכונה לא רשומה 00:00:00:00:00:00" מהבדיקה של השרת על עצמו;
  `record_journal=not probe` לצד `record_seen` (#976). מכונה זרה אמיתית — עדיין
  נרשמת.

### אבטחה
- **שאריות שומר הזהות** (#1012, PR #1026): `PUT /capture/<task>/files|manifest`
  ו-`GET /agent/state|drivers?mac=` מאחורי `identity_gate`; הקיוסק לא מבקש עוד
  מניפסט מלא (שדות מחיצת המערכת מ-`/api/console/images`, המניפסט נשאר סגור
  לקיוסק); `/agent/groups`, `/boot/*` והורדת דרייברים — פתוחים במודע ומתועדים
  (§21).

שניהם נכתבו ע"י Astra (Codex, Sol) ואומתו ע"י המתאם. שער המעבדה ×2 (4097).

## [v0.38.0] - 2026-09-17

מה שהמפעיל רואה: **מתג הדלקה/כיבוי לכל פורט** שבאמת סוגר את המאזין, **דף
סניפים ושרת משני** מחדש — הגל האחרון של העיצוב מחדש (#954, 7 גלים) — ו**כיווץ
של דיסק עם D: אחרי C:**. וכל נתיבי הסוכן מאחורי שומר הזהות.

### נוסף
- **מתג לכל פורט בזמן ריצה** (#996, PR #1015): `PUT /ports/{id}` סוגר/פותח
  מאזין uvicorn בפועל (נמדד: bind מבחוץ מצליח אחרי כיבוי), נשמר ב-DB ושורד
  אתחול; 8080/8081 מאחורי הקלדת שם השרת, 8081 דרך 8081 → 409 בשמו; `/ports`
  עם `enabled`/`listening`/`bind`/`toggle`/`off_means` ושורות 67/8443/SSH×כרטיס;
  "כבוי" ≠ "לא מאזין" ≠ "לא נקרא". TFTP בלי מתג בכוונה (#1013). דף הפורטים
  לחוזה (PR #1020): מקור אחד לכל שורה, `bind` כרשימה, הקלדה לפי השרת.
- **סניפים ושרת משני** (#954 גל 7, PR #1019): טבלת סניפים עם חיבור **נמדד**
  (לא "בודק חיבור…" קפוא), העברות עם "נסה שוב", קבוצות; **רישום שרת משני**
  מהקונסולה (preview SPKI → קוד צימוד → enroll); שרת משני כאובייקט (KPI,
  note אדום, העבר אימג'/בדוק חיבור/השבת). פערי API ב-#1017. ‏`?v=7.9`.
- **כיווץ כל מחיצות ה-NTFS בקליטה** (#929, PR #1018): דיסק עם D: אחרי C:
  מכווץ — כל מחיצה במקומה (ntfsresize), בלי להזיז נתונים על דיסק הבנייה;
  היעד מקבל פריסה צפופה, `min_target_bytes` = סכום; שאלה אחת למפעיל עם
  הטבלה של כולן; החזרה של כולן בסדר הפוך גם אחרי כשל; רשומת #926 אחת לדיסק.
  `shrink.sh` פוצל (`shrinkplan.sh`, `shrinkoffer.sh`) — כולם מתחת ל-300.
  על היעד נמתחת האחרונה (D:) אלא אם בוחרים אחרת בפתיחת הסבב.

### אבטחה
- **שומר הזהות על שאר נתיבי הסוכן** (#997, PR #1011): login, pulls, sessions,
  disk-event, shrink-*, direct — אותה `identity_gate` של #855. `manifest`/
  `files`: זהות הפוכה — כתובת המקור → חכירה → MAC **רשום** (בלי שינוי בסוכן).
  שאריות ב-#1012.
- **נראות ראשי/משני לפי תפקיד** (#988, PR #1016): ארבעת כללי נדב כטבלה
  קבועה (52 תאים) — השרת כבר אכף; מתועד ב-`interfaces.md` §18.

אומת בשער המעבדה פעמיים (4081) + e2e.

## [v0.37.0] - 2026-09-17

מה שהמפעיל רואה: **הקונסולה ב-HTTPS** (תעודה עצמית, אישור פעם אחת בדפדפן),
**WoL למחשב בנייה/משכפל בודד** בכפתור, וחמשת הדפים הקטנים מחדש — מוניטור,
דרייברים, הגדרות, הרשאות, יומן (גל 6 של #954).

### נוסף
- **TLS לקונסולה** (#703 tracer 5, PR #1009): 8081 מוגש **HTTPS בלבד** —
  תעודה עצמית נוצרת פעם אחת ל-`<data_dir>/console-tls/` (מפתח 0600, SAN =
  `--console-host` + hostname); HTTP על 8081 לא מוגש. `--console-tls off`
  מותר רק על loopback (פיתוח/e2e). Cookie `Secure`; טביעת SHA-256 בשורת
  הסטטוס, ב-`/me` ובסיכום המתקין — משווים פעם אחת ומאשרים. 8080/8082/8443/
  5900 לא משתנים.
- **WoL למחשב בודד — הכפתורים** (#984, PR #1010): בכרטיס מחשב בנייה, בכרטיס
  משכפל (אובייקט וחדר), במגירה ובשורת קבוצה → `POST /machines/{mac}/wake`;
  "הער את כל הבנייה" → `groups/grp_BUILD/wake`; toast "נשלח (N)" — נשלח,
  לא התעוררה. #984 נסגר.
- **חמשת הדפים הקטנים** (#954 גל 6): **מוניטור** — טבלה, "פתח מוניטור"
  רק שם (הכרעת נדב) ורק כשמחובר; **דרייברים** — טבלת חבילות + כיסוי לפי
  מכונה (בנייה/שיכפול), מחיקה בהקלדת שם; **הגדרות** — עברית, שורה לכל
  הגדרה, שמור/בטל עד שינוי, כולל מתג זהות המכונה; **הרשאות** — טבלה אחת,
  המנהל הפעיל האחרון בגלוי, מטריצת "מה כל תפקיד רואה" מהקוד; **יומן** —
  סינון בדף, "עוד" עד 1,000. פערי API ב-#1008. ‏`?v=7.7`.

### תוקן
- `test_server_api` (#880) — מתג "הפצה לכיתות" נבדק במבנה מסך ההגדרות החדש.

אומת בשער המעבדה פעמיים (3997) + e2e.

## [v0.36.0] - 2026-09-17

מה שהמפעיל רואה: **מתג בכל שורה בדף הפורטים** (SSH/מוניטור עברו לשם),
**בריאות = בדיקות + עדכון**, ו**שומר זהות מכונה** — hello מכתובת שאינה של
חכירת ה-DHCP של ה-MAC נדחה (הכרעת נדב: "מוגן כבר ב-v1").

### נוסף
- **בריאות ושירותים** (#954 גל 5, PR #1001): טבלת בדיקות אחת בחמישה מצבים
  ("לא נבדק" אפור-כתום, לעולם לא ירוק), לולאות אתחול מקובצות, כרטיס גרסה
  ועדכון (בדוק / החל בהקלדת שם השרת / חזור). המתגים יצאו מכאן.
- **רשת › פורטים** (גל 5): טבלה אחת — שירות, פורט, מי מתחבר, כרטיס, מצב
  נמדד, **מתג בכל שורה** (הכרעת נדב 17/09), "מה קורה אם מכבים". מחובר היום:
  DHCP 67 (🔒 שם הכרטיס), PXE proxy, מוניטור 5900, SSH לתחנות, SSH לשרת
  × כרטיס ("הדלת האחרונה"); לשאר — חוזה #996 (בבנייה): מתג "לא ידוע" שמסביר.
  פערים ב-#1000. ‏`?v=7.5`.
- **זהות מכונה** (#855, PR #998): `hello` ו-`progress` (סבב ומשימה) דורשים
  שכתובת המקור תואמת את חכירת ה-DHCP של ה-MAC המוצהר (`dnsmasq.leases`,
  `--dhcp-leases`); אי-התאמה → 403 `identity_refused` ביומן, בלי רישום
  ב-`net_seen`; אין חכירה/קובץ → `identity_unverifiable` (לא "תקין", עיקרון
  5). מתג `identity_check` בהגדרות (admin, נרשם מי כיבה) + שורה "זהות מכונה"
  ב-`/health`. `progress` של קליטה דורש את אסימון המשימה כמו ההעלאה.
  שאר נתיבי הסוכן — #997 (בבנייה).
- **WoL למחשב בודד — צד השרת** (#984, PR #995): `POST /machines/{mac}/wake`
  (build/cloner בלבד; כיתה → 403 "v2"), `POST /groups/grp_BUILD/wake`;
  `{sent, failed, reasons}` — `sent` ≠ התעוררה. הכפתורים — גל 6.

### תוקן
- `test_health_probe_marks_itself` — הראיה בלי `seconds`/`stalled` (שעון קיר;
  נפל 1/3978 בשער).

אומת בשער המעבדה פעמיים (3978 / 3977+1 שעון קיר) + e2e.

## [v0.35.0] - 2026-09-17

מה שהמפעיל רואה: **סבב הפצה = חדר המשכפלים** — הגריד מחשב→חריץ עם צבע לכל
דיסק, השאלה למפעיל בדף, וסבב חדש inline — גל 4 של #954 (#695). ומוניטור —
רק בדף המוניטור.

### נוסף
- **חדר המשכפלים** (#954 גל 4, PR #990): כותרת "גל N מתוך M" · אימג' ·
  יעד/נכתבו/נשארו · תג; פעולות: התחל גל, הער משכפלים, עצור (הקלדת שם), תצוגה
  גדולה 4:3; KPI ×5; **אותו גריד** של "מחשבי שיכפול" במצב סבב (`/room`);
  **השאלה למפעיל בדף** (SMART כתום = ממשיך לכתוב, אדום = מדלג — הכרעת נדב
  16/09) עם "נקה"; **סבב חדש inline** עם "נכנס לדיסק מ-X GB" (#953, סירוב
  fit בגלוי) ומקור דיסק בנייה (#715). לשוניות: חדר המשכפלים · כיתה (הקיים,
  v2) · היסטוריה (דורש API). פערי API ב-#989.

### שונה
- **מוניטור רק בדף המוניטור** (PR #993, הכרעת נדב 17/09 "במקום החדש שביקשתי
  ורק שם"): הכפתור הוסר משורת מחשב, כרטיס משכפל, כרטיס בנייה ומגירת מחשב;
  "לענות ליד המסך" בהודעת המפעיל מפנה לדף המוניטור.

אומת בשער המעבדה פעמיים (3939). ‏`?v=7.4`.

## [v0.34.0] - 2026-09-17

מה שהמפעיל רואה: **"מחשבי שיכפול" ו"מחשבי בנייה" בעץ הם אובייקטים** —
חדר עם גריד מחשב→חריץ ומסך קליטה עם המחשבים — גל 3א של #954.

### נוסף
- **מחשבי שיכפול כאובייקט** (#954 גל 3א, PR #981): כותרת עם מונים ותג סבב,
  פעולות (פתח סבב, WoL לחדר, + מחשב), KPI ×5, **גריד מחשב→חריץ** "דיסק N ·
  SATA N-1" (בלי sd*), צבע לכל דיסק (אדום > כתום > כותב > ירוק > "לא נבדק"
  אפור > ריק), "נקה" על אדום; מסלול `/room` בסבב ו-`/machines[].disks` בלעדיו;
  לשוניות סיכום · מגירות (גדול, 4:3) · סבבים.
- **מחשבי בנייה כאובייקט**: "+ קליטת אימג'", הפצה ישירה (#715), **קליטה
  בתהליך** (רשימה כשיש שתיים; ביטול בהקלדת שם), כרטיסי מחשבים עם "קלוט
  מכאן", קליטות אחרונות; לשוניות סיכום · קליטות. פערי API ב-#980.
  ‏`?v=7.2`.

### תוקן
- **הבדיקה העצמית של השרת אינה נרשמת כמכונה** (#976). ‏`/health`, ‏`/ports`
  ו-`/ssh` מושכים `GET /boot/menu?mac=<MAC רשום>` ו-`?mac=00:00:00:00:00:00`
  ונרשמו כ-GRUB: כל כניסה לקונסולה דרסה IP/נראה-לאחרונה/שביל-פירורים של
  מכונה אמיתית ל-`127.0.0.1 · 1/9`, יצרה התקן "לא רשום" `00:00:00:00:00:00`
  — **וספרה אתחול** בתקציב לולאת האתחול (#75): ארבע כניסות לקונסולה במהלך
  סבב פתוח שלחו את המכונה הרשומה הראשונה לדיסק המקומי. הבדיקה מסמנת את
  עצמה (`X-ImageCtl-Probe: 1`), והסימון מכובד רק כשהפונה הוא השרת עצמו
  (‏loopback או כתובת השרת); מבחוץ הבקשה נרשמת כמו כל GRUB.

אומת בשער המעבדה פעמיים (3937).

## [v0.33.0] - 2026-09-17

מה שהמפעיל רואה: **ספריית אימג'ים כמו datastore browser** (תיקיות ואימג'ים
בתוכן) ו**דף מחשבים בטבלה אחת מקובצת** — גלים 2–3 של העיצוב מחדש (#954).

### נוסף
- **ספריית אימג'ים מחדש** (#954 גל 2, PR #973): עץ תיקיות (כל האימג'ים / תיקיות
  עם מונים / ללא תיקייה), טבלה אחת (שם+תיאור, מערכת, "נכנס לדיסק מ-X GB"
  מ-#953, בשרת, נוצר, אימות, בשימוש), קליטה בתהליך כשורה, בחירה מרובה
  (העבר / הורדה / שינוי שם / מחיקה בהקלדת שם לכל אימג'), גרירה (אימג'→תיקייה
  = העברה, סדר תיקיות/אימג'ים), לשוניות קבצים · קליטות · אחסון (scrub).
  **מגירת אימג'**: פעולות, אימות, מאפיינים (כולל "בשימוש במקור"), מחיצות
  מ-scrub, היסטוריה מהיומן. `library.js` המת הוסר. פערי API ב-#972.
- **דף מחשבים מחדש** (#954 גל 3, PR #978): כותרת עם מונים ותגים ("N דיסקים
  אדומים", "N לא רשומים"), סינון, **טבלה אחת מקובצת** — בנייה, שיכפול, ואז
  קבוצה מתקפלת לכל כיתה עם roll-up; "דיסק N · SATA N-1" ולעולם לא sd*;
  שלושת מצבי `disks` נשמרים; "+ מחשב" (אחד / הדבקה עם `dry_run`); לשוניות
  כל המחשבים · נראו ברשת · דיסקים אדומים. **מגירת מחשב** (דיסקים לפי חריץ,
  חומרה, שלב אתחול, דיסק אדום עם "נקה"). **כיתה כאובייקט** (`groupPage`,
  ישמש למשכפלים/בנייה). `machines.js` נמחק. פערי API ב-#977; **מחשבי כיתה
  = v2** (הכרעת נדב 17/09).

### תוקן
- `test_known_macs` — האזהרה על כשל DHCP נבדקת ב-`console.js` בלבד (4 קריאות).

אומת בשער המעבדה פעמיים (3925). ‏`?v=7.1`.

## [v0.32.1] - 2026-09-17

### תוקן
- `agent/lib/hibernation.sh` — לולאה של כלי אחד שנותרה מ-#952 (SC2043) הפילה
  את `shellcheck` בציבורי על v0.31.1 ו-v0.32.0; בדיקה ישירה, בלי שינוי
  התנהגות (PR #979).

## [v0.32.0] - 2026-09-17

מה שהמפעיל רואה: **סקירה כללית חדשה** — הגל הראשון של העיצוב מחדש (#954)
שנדב אישר 17/09, ובעץ אייקון "אימג'ים" הוא תיקייה.

### נוסף
- **סקירה כללית מחדש** (#954 גל 1, PR #969) לפי `docs/design/console-redesign/home.md`,
  מה-API הקיים בלבד: KPI ×5 עם שורת משמעות · "מה קורה עכשיו" (סבב / סבב
  שיכפול / משיכות / קליטות / העברות) · "דורש טיפול" (דיסקים אדומים, מכווצים,
  לא רשומות, סניפים, בריאות) · "אירועים אחרונים" · "השרת". לשוניות: סיכום ·
  משימות · אירועים (deploy בלי אירועים — `/journal` הוא admin-only). מה
  ש"דורש API" מוצג כ"בקרוב" ומרוכז ב-#968. הדף הישן נמחק, לא הוסתר.
- **שפת העיצוב המשותפת בקוד** — `UI.*` בראש `console.js` (כותרת אובייקט,
  לשוניות, KPI, כרטיס, datagrid, פס מצב, פס התקדמות, מפתח-ערך, ציר-זמן, הודעה,
  ריק-מצב) + CSS תחת `.page` מהטוקנים בלבד; מצב כהה מאותם משתנים. אייקון
  "אימג'ים" בעץ → תיקייה (תוספת נדב).
- **`tools/drivers/build-package.py`** (#959, PR #970) — מ-driver pack של
  HP/Lenovo/Dell (CAB או תיקייה) לחבילה ש-`POST /drivers/upload` מקבל:
  פריסת CAB (`expand.exe` / `cabextract` / `7z`, ובלעדיהם סירוב בשמו), סריקת
  INF ל-`PCI\VEN&DEV` של Net/HDC/SCSIAdapter, manifest בפורמט #720 שעובר
  `validate_manifest` לפני שה-tar נכתב. הפורמט הורחב: מזהה PCI בלי class
  (`vendor:device`) וכלל `pci_any` (אחד מהמזהים מספיק, עד 512).
  ‏`expand.exe` על קובץ שאינו CAB יוצא 0 ומעתיק — הכלי משווה ומסרב (עיקרון 5).

### תוקן
- **bidi בקונסולה** — "38.4 GB" שהוצג "GB 38.4" ותאריך+שעה הפוכים (`ltr()` +
  `<bdi dir=auto>`); הרינדור כל 2 שניות איפס גלילה (`scrollTop` נשמר).

אומת בשער המעבדה פעמיים (3919, 15 דילוגים — השניים החדשים: אין extractor
ל-CAB בדביאן, skipif מוצהר). ‏`?v=6.9`.

## [v0.31.1] - 2026-09-17

### תוקן
- **המתנה ארוכה יותר למחיצות 2+ בזרם** (#957 — ממצא ההשוואה מול FOG, GH-536) —
  `udp-sender` מקבל למחיצה השנייה והלאה `--max-wait max(600, …)` ו-`--start-timeout`
  מתאים (780), והמקבל מחכה 900 (מקור udpcast: `--start-timeout` של השולח מגביל
  את כל ההמתנה, והמקבל מודד מהחיבור). הראשונה ללא שינוי. גם בהפצה ישירה (#715,
  ממשק 3). סוכן חדש מול שרת ישן מסרב בגלוי.

### תיעוד
- `docs/design/console-redesign/` — 23 מוקאפים + 18 מסמכי עמוד + README לעיצוב
  מחדש של הקונסולה (#954), **מאושרים ע"י נדב 17/09**; הבנייה מתחילה (גל 1 —
  סקירה כללית — PR #969). אומת בשער המעבדה פעמיים (3896).

## [v0.31.0] - 2026-09-17

מה שהמפעיל רואה: **"נכנס לדיסק מ-X GB" לכל אימג', ואימג' שלא נכנס למגירה
הקטנה בקבוצה מושבת עם הסיבה בפתיחת סבב** (#953 — בקשת נדב מהלילה).

### נוסף
- **התאמת אימג' לדיסקים** (#953) — הספרייה ומסך האימג' מציגים "נכנס לדיסק
  מ-X GB" (‏`min_target_bytes` הנגזר), "בשימוש" (לא ידוע ≠ 0) ו"בשרת"; בפתיחת
  סבב (`station/room.js`) `GET /room` מחזיר `disk_floor` — האימג' שלא נכנס
  מושבת עם "דיסק 2 במחשב 2 הוא 256GB, האימג' צריך 300GB"; **השרת** מסרב
  409 עם אותה מחרוזת (`room.fit_refusal`, גם עם `target_slots` #695). ‏`?v=6.8`.

### תוקן
- **`apply_gpt` מסרב כשגודל הסקטור של היעד שונה מהמניפסט** (#958 — ממצא
  ההשוואה מול FOG): `blockdev --getss` מול `sector_size`; אימג' 512n על דיסק
  4Kn = סירוב בקול לפני בייט עם שני המספרים (המרה — v2). אומת בשער המעבדה
  פעמיים (3890).

## [v0.30.0] - 2026-09-17

לילה 16→17/09 על ברזל: הכיווץ (#87) אומת קצה-לקצה על דיסק 256 (10 דק') ועל
דיסק 512 עם 203GB בשימוש (45 דק'; C: 494→223GB, הדיסק חזר לגודלו), והאימג'
המכווץ (146GB) שוכפל לדיסק 256 ב-34 דק' (850m; על 200m — 100 דק').

### נוסף
- **זיכרון הפריסה המקורית בשרת לפני הכיווץ** (#926) — הסוכן שולח `shrink-open`
  (סידורי, מחיצה, תחילה, גודל, GUID, דגלים, גודל NTFS) **לפני** `ntfsresize`;
  בלי 200 — אין כיווץ. אחרי החזרה — `shrink-close`. ב-hello של דיסק עם רשומה
  פתוחה (זהות = סידורי + השוואת תחילה/GUID מהדיסק) הסוכן מציע "[1] החזר /
  [2] השאר"; מערכת קבצים קטנה ממחיצה שכבר בגודלה → מציע מתיחה, לא סוגר
  בשקט; כשל החזרה → `shrink-note` והרשומה נשארת. כרטיס "דיסקים מכווצים"
  בקונסולה עם "נקה". סקירת Fable: תאומי-סידורי → לא נוגעים.

### תוקן
- **הכניסה במסך מחשב הבנייה — `gui_role` מתשובת ה-login** (#947 המשך) —
  `/api/console/me` הוא נתיב ניהול (רשימת אסטרה) ואינו על פורט הסוכן; התפקיד
  נקרא מתשובת ה-login. נמדד 17/09 01:49 על ברזל.
- **שער המהובר לפי `ntfs-3g.probe` בלבד** (#651 המשך) — `hiberfil.sys` קיים על
  כל ווינדוס עם hibernation מופעל, גם אחרי כיבוי מלא; המצב המהובר הוא בכותרת
  הקובץ, וזה מה ש-probe rc=14 בודק. נמדד 17/09 01:59: דיסק נקי סורב "מהובר".

### תיעוד
- `docs/design/fog-clonezilla-deep-compare-0917.md` — השוואת עומק מול הקוד של
  FOG/Clonezilla/udpcast (13 סעיפים, דרייברים, אוטומציה, המלצות v1/v2).
- `docs/design/console-screens-inventory.md` — מלאי 18 מסכי הקונסולה עם צילומים
  ועמודת "הכרעת נדב" (#954). ‏`?v=6.7`. אומת בשער המעבדה פעמיים (3875).

## [v0.29.2] - 2026-09-17

### תוקן
- **הכניסה במסך מחשב הבנייה החזירה 404 מאז #738** (#947) — נמדד על השרת החי
  17/09 01:06: `POST /api/console/login` מ-10.44.0.88 לפורט הסוכן → 404, בזמן
  שאותם פרטים מתחברים לקונסולה. ‏allowlist הקיוסק (login/folders/images/
  tasks-capture/room — בלי שום נתיב ניהול) חי עכשיו גם על פורט הסוכן; אותה
  משפחה כמו #824. `test_app_split` אף אישר את המצב השגוי — תוקן.
- **כשל בכתיבת שם המחשב אחרי שחזור נראה** (#856) — היעד מסתיים `done` עם
  `error` = "שם המחשב לא נכתב: <סיבה>" (אותו דפוס כמו grow deferred/#927),
  כתום בקונסולה בכרטיס החבר ובמגירת התחנה; השחזור עצמו לא נכשל. ‏`?v=6.6`.
  הגואי הגרפי — #943. אומת בשער המעבדה פעמיים (3826).

## [v0.29.1] - 2026-09-17

שני המשכים של #87 מסקירת Fable, ומסירת 16/09.

### תוקן
- **אזהרת "המקור לא הוחזר לגודלו" נראית בקונסולה** (#927) — קליטה שהצליחה
  אבל החזרת מחיצת המקור נכשלה הגיעה כמשימה `done` עם `error`, ו-`loadCaptures`
  סינן רק pending/running — האזהרה נעלמה. עכשיו הודעה כתומה במסך האימג'ים
  ובמגירת המחשב, עם הטקסט המלא. ‏`?v=6.5`.
- **סירוב קליטה כש-C: אינה המחיצה האחרונה — עם הסבר** (חלק מ-#929) — "מחיצה 3
  (511.1 GB) אינה האחרונה על הדיסק, והכיווץ פועל רק על המחיצה האחרונה (מחיצה 4,
  53.7 GB): מחקו/מזגו את המחיצה שאחריה ב-Windows וקלטו מחדש". ההכרעה הגדולה
  (לכווץ הכול / להזיז) פתוחה.

### תיעוד
- `docs/handoffs/handoff-2026-09-16.md` (פרטי) · ‏`bakara-shlilit`: קומיט לפני הבקרה
  השלילית. אומת בשער המעבדה פעמיים (3817).

## [v0.29.0] - 2026-09-17

מה שהמפעיל רואה: **השרתים המשניים בעץ הניווט** — כמו vCenter, צומת לכל סניף.

### נוסף
- **השרתים המשניים בעץ הניווט נבנים מה-API** (#936) — צומת לכל משני
  מ-`GET /storage-nodes` עם נקודת מצב (ירוק / אדום עם הסיבה / אפור למושבת),
  ומתחתיו סקירה · מחשבים (עם מוניטור דרך הראשי) · אימג'ים · העברות — דף
  `branch` אחד עם המשני שנבחר. "אימג'ים" של משני = ההעברות שהראשי השלים
  אליו (בערוץ אין רשימת מלאי; הדיסק של המשני הוא מקור האמת שלו). הצומת
  הסטטי "שרת משנה — חיפה / יוגדר בהמשך" נמחק. שם השרת הראשי בצומת העליון
  הוא האמיתי (`server_name` מההגדרות, אחרת hostname) ושינוי שם מהעץ נשמר
  בשרת. משתמש deploy לא רואה משניים (הנתיב admin בלבד). ‏`?v=6.4`.
  אומת בשער המעבדה פעמיים (3814).

## [v0.28.2] - 2026-09-16

### תוקן
- **סרגל הניווט: גלילה בלי פס גלילה נראה, ודאבל-קליק בלי סימון טקסט** (#931
  המשך) — `scrollbar-width:none` + `::-webkit-scrollbar{display:none}` על העץ
  (עדיין נגלל בגלגלת/מגע), `user-select:none` על שורות הצומת. ‏`?v=6.3`.

### תיעוד
- **תרשימי v1 חדשים ב-README** — ארכיטקטורה + זרימה בזמן, בהיר וכהה
  (`docs/architecture-v1*.svg`, `docs/flow-v1*.svg`), לפי הבריף של נדב;
  `architecture-current*` הוסרו. אומת בשער המעבדה פעמיים (3809).

## [v0.28.1] - 2026-09-16

### תוקן
- **סרגל הניווט בקונסולה: גלילה בגלגלת, וקליק/דאבל-קליק לפי הבקשה** (#931) —
  `.sidebar` לא היה חתוך לגובה המסך ולכן העץ גדל מעבר לגבול בלי פס
  גלילה; קליק בודד על שורה = מציג את החלון של הצומת (מחשבים, אימג'ים…),
  דאבל-קליק על השורה = פותח/סוגר פעם אחת, החץ הקטן ממשיך לפתוח/לסגור
  בקליק. שם השרת: דאבל-קליק על הטקסט = שינוי שם. ‏`?v=6.2`. אומת בשער
  המעבדה פעמיים (3809).

## [v0.28.0] - 2026-09-16

### נוסף
- **כיווץ מחיצת ה-NTFS של המקור בקליטה, והחזרתה אחריה** (#87) — דיסק בנייה
  של 512GB עם 100GB בשימוש נקלט לאימג' שנכנס ל-256GB. בקליטה: `ntfsresize
  --info` → יעד = מינימום + max(10%, 2GiB) → שאלה לאדם ליד המכונה ([1]
  כווץ / [2] קלוט בלי לכווץ / [3] בטל; מגיעה גם לקונסולה דרך `attended`) →
  `ntfsresize -s` → כניסת GPT מצומצמת → `blockdev --rereadpt` → `ntfsfix
  -d`; אחרי הזרם (וגם על כשל) המקור חוזר לגודלו. המניפסט נושא `size_bytes`
  מכווץ + `shrunk_from_bytes`; מחיצת recovery שאחרי C: מקבלת `start_sector`
  מוזז + `source_start_sector` ואינה מוזזת על המקור. שידור ישיר (#715) לא
  מכווץ. סקירת Fable על הנתיב ההרסני תיקנה חמישה ממצאים לפני המיזוג (בלי
  `ntfsfix -b` על המקור, `ntfsresize` בלי `-f` ועם y מפורש, `sgdisk -a 1`
  וקריאה חוזרת של התחילה, כשל כפול לא נבלע, דגלי מחיצה ריקים = סירוב).
  המשך: #926 #927 #928 #929. אומת בשער המעבדה פעמיים (3809); **טרם על ברזל**.

## [v0.27.0] - 2026-09-16

מה שהמפעיל רואה: הקונסולה בלי שאריות הדמו מה-HTML — גרסת השרת האמיתית
בשורת הסטטוס, פעמון התראות שמראה מה הוא סופר, ודף מחשבים שמציג את הסינון
במקום להסתיר אותו (#915, #916). מסך הכניסה של מחשב הבנייה שולח hello
(#912), המוניטור מסביר למה נסגר (#904 סעיף 4), תרשים הארכיטקטורה ב-README
לפי היקף v1 — בלי תחנות כיתה, עם שרת משני (#888), וחריג guard-bash
לענפי שער (#918). אומת בשער המעבדה פעמיים (3781 עברו).

### תוקן
- **קונסולה: שאריות דמו שהוצגו כנתונים חיים** (#915) — `v0.9` הקשיח
  הוחלף בגרסת השרת מ-`GET /api/console/me` (`version`, תג git); מעגן
  המשימות בלי `3`/`74%`/`הודעות: 2`/שורות דמו; מגירת ההתראות מציגה את
  מה שהתג בסרגל סופר (מכונות שנכשלו + בדיקות בריאות warn/bad).
- **קונסולה: "אין מחשבים רשומים" מול עץ מאויש** (#916) — נמדד: סינון
  קבוצה מהעץ שנשאר בלי חיווי. צומת "מחשבים" מאפס אותו, הסינון מוצג עם
  כפתור ביטול, והלשוניות הן שתיים חיות ("כל התחנות" / "ניהול לפי סוג")
  במקום שלוש שבהן שתיים זהות; כרטיס "כללי naming" (‏LAB1-05) הוסר.
- **מסך הכניסה (Sign in) לא שלח hello בזמן ההמתנה** (#912) — הפער
  ש-#908 השאיר בכוונה: השער מסתיים ב-`die_local` כשהכניסה נדחית.
  ‏`recovery_login` עוטף עכשיו את ההמתנה ב-`attended "signin"` — ולכן גם
  מחשב בנייה, גם אשף השחזור וגם הכניסה הנוספת לסבב כיתה מווילן ההפצה —
  והפעימה נעצרת לפני `login_failed`, בלי תהליך יתום (נבדק: אחרי
  TEST-REBOOT מספר ה-hello אינו גדל). ‏`attended.sh`: פעימה בתוך פעימה
  כבר אינה כפולה — נמדד 16/09 שקינון יצר שתי פעימות במקביל ויתומה אחת;
  הפנימית מחליפה את החיצונית, והחיצונית חוזרת כשהפנימית נענתה. הקונסולה
  מתרגמת את שתי המילים הקבועות: "ממתין למפעיל: תפריט" / "כניסה"
  (‏`?v=6.1`).
- **המוניטור נסגר עם קוד וסיבה שמגיעים לדפדפן** (#904 סעיף 4) — accept
  ואז close: "מכונה לא מוכרת" (4404), "כבר פתוח מוניטור" (4409) ושאר
  ה-44xx/45xx במקום 403 אילם. שומר כתובת המקור (#859) נשאר 403 בכוונה.

### שונה
- **guard-bash: המתאם רשאי למחוק ענפי שער `integration/gate-*`** (#918) —
  אישור נדב 16/09; כל שם אחר, תג או פקודה שנייה נשארים חסומים.

### תיעוד
- **תרשים הארכיטקטורה ב-README לפי היקף v1** — עמודת "תחנת כיתה" הוסרה
  (v2), למשכפלים ולמחשב הבנייה מסך GUI גרפי, ועמודת "שרת משני (סניף)"
  (#655) במקומה. עובר לציבורי עם הפרסום.

## [v0.26.2] - 2026-09-16

תיקון אחד, מהמדידה של הערב: תפריט מחשב הבנייה שולח hello בזמן שהוא
ממתין לבחירה (#908) — אותו דפוס כמו #906. אומת בשער המעבדה פעמיים
(3737 עברו, ריצות 1+2).

### תוקן

- **תפריט מחשב הבנייה חסם את ה-hello בזמן שהמתין לבחירה** (#908) — אותו
  דפוס כמו #906: מחשב בנייה שעמד בתפריט נראה בקונסולה "לא נראתה" אחרי
  `ONLINE_SECONDS`, והמוניטור אליו נחסם. ‏`build_menu` עטוף עכשיו
  ב-`attended "menu"` (‏`agent/lib/attended.sh`): פעימת hello עם
  `waiting_for: operator` ו-`prompt: menu` כל זמן שהתפריט והזרימות
  שמאחוריו ממתינים לאדם, ונעצרת כשהתפריט חוזר ללולאה. הגואי לא נגע: הקיוסק
  רץ ברקע והלולאה הראשית ממשיכה לשלוח hello בעצמה.

## [v0.26.1] - 2026-09-16

שני תיקונים מהמדידות של היום: הסוכן ממשיך hello בזמן שהוא ממתין למפעיל
(#906 — "ממתין למפעיל: <השאלה>" בקונסולה, המוניטור לא נחסם), ופערי ההתקנה
של סניף משני (#904 — websockets במתקין, קונסולה גם על loopback ל-pairing,
מסלול initrd לשרת cloud). וגם: מסמך המחקר למתקין הגרפי (#910).

## [v0.26.0] - 2026-09-16

מה שהמפעיל רואה: **כפתור "עדכן" בהגדרות השרת** (#748 — כבוי כברירת מחדל,
בדיקה מול הריפו הציבורי בחיבור חד-פעמי, עדכון לתג בהקלדת שם, חזרה לגרסה
הקודמת, ו"הופעל" ≠ "אומת"). ותיקון #902 (ה-preview של pairing נפל במעבדה
מול חיפה). הסניף המשני בחיפה אומת היום במעבדה: pairing, העברה, מכונות, מוניטור.

מה שהמפעיל רואה: **כפתור "עדכן" בהגדרות השרת** (#748, הכרעת נדב 16/09):
מתג `update_enabled` כבוי כברירת מחדל, "בדוק עדכון" מול הריפו הציבורי
(`git ls-remote --tags`, חיבור יוצא חד-פעמי בלבד — לא קבוע), "עדכן
ל-vX.Y.Z" מאחורי הקלדת שם השרת וחסום כשיש סבב פתוח/רץ, ו"חזור לגרסה
הקודמת". השדרוג עצמו (`tools/server-upgrade.sh`) מריץ דרך `systemd-run`
מנותק מתהליך ה-API, כי הוא מסתיים ב-restart של `imagectl-server` עצמו.
אימות אחרי השדרוג הוא ראיה חיובית — הגרסה שרצה בפועל, לא "אין שגיאה".
פירוט מלא: `docs/architecture/interfaces.md` סעיף 18.

## [v0.25.0] - 2026-09-16

שני פיצ'רי v1 (#888): **הפצה ישירה ממחשב הבנייה למחשבי השיכפול בלי לעבור
בשרת** (#715 — כרטיס חמישי, השרת מתזמר, הדיסק משדר) ו**שרתים משניים** (#655
— העברת אימג'ים ראשי→משני, מחשבי המשני ומוניטור דרך הראשי, לשונית "סניפים";
#883 נסגר). וגם: ה-CI הציבורי חזר לירוק אחרי 11 ימים (#900 — SC2115 אמיתי
ב-postdeploy).

### נוסף
- **הפצה ישירה ממחשב הבנייה למחשבי השיכפול — בלי לעבור בשרת** (#715,
  ‏v1 #888; הכרעת נדב 12/09 + 16/09). כרטיס חמישי בתפריט מחשב הבנייה,
  "הפצה מהדיסק הזה למחשבי השיכפול", בין "הפצה למחשבי שיכפול" ל"הפצה
  לכיתות" — בטקסט (`directflow.sh`) ובגואי (אותו גריד של #695, בלי
  בורר אימג'). השרת נשאר **המתזמר היחיד** (סבב חדר עם
  `source_kind=build_disk`, WoL **רק לנבחרים**, ספירת מקבלים, התחלה,
  התקדמות, קונסולה "מקור: מחשב הבנייה"); רק **המקור** משתנה: מחשב
  הבנייה מקבל משימת `direct_send` ומריץ `udp-sender` (נארז עכשיו
  ב-initrd) על הדיסק שלו, קריאה בלבד, עם **אותם דגלים** של
  `SenderEngine` (#437/#438). האימג' **לא נכתב ולא נשמר** בשרת — רק
  המניפסט עובר (`PUT /api/v1/direct/<task>/manifest`) ומוגש למקבלים
  כ-`GET /api/v1/images/live_…/manifest`, ולכן **המקבלים לא השתנו**
  (‏`restore.sh` כמות שהוא, שערי #701/#652 בעינם). הדיסק נקרא פעמיים:
  קריאה ראשונה = `capture_disk` בלי העלאה (אותם שערים: GPT, מהובר
  #651, BitLocker #671) שמייצרת את ה-sha256 שהמקבלים מאמתים מולו;
  קריאה שנייה = השידור, שמגובב על החוט ומושווה למניפסט — אי-שוויון
  הוא כשל **במקור**, בשמו. הגל אינו יוצא לפני המניפסט; **סבב יחיד**
  (הסט הנבחר קבוע, ‏`target_drives` = סכומו), נסגר עם הגל גם כשמגירה
  נכשלה; עצירה מהקונסולה (הקלדת `<מכונה>:<דיסק>`) מבטלת גם את
  המשימה. ממשקים: `docs/architecture/interfaces.md` סעיפים 3, 4 (`sending`), 7 וסבב
  החדר. סימולציה: שלב 9ב ב-`tools/e2e_simulation.py` (124 בדיקות).
  ‏`?v=5.6`. **לא אומת על ברזל ולא במעבדה** — ראו יומן המשימה.

- **שרתים משניים — העברת אימג'ים וצפייה מהראשי** (#655 v1, הכרעת נדב
  16/09; ‏Issue-גג #888). לשונית **"סניפים"** בקונסולת הראשי (רק כשיש
  משני פעיל; על משני אין לשונית — חד-כיווני): כרטיס לכל משני עם מצב
  חיבור, המכונות שלו כפי שהמשני דיווח, **"העבר אימג'"** עם התקדמות
  (בייטים/סה"כ, "המשני מאמת sha256", כשל עם הסיבה) ו**מוניטור** למכונה
  של המשני. ההעברה: הראשי דוחף את ה-tar של תיקיית האימג' בערוץ ה-mTLS
  המאומת (#740), והמשני מכניס דרך `import_tar` — ‏sha256 לכל מחיצה
  **לפני** שהאימג' נכנס לספרייה (עיקרון 6); קטוע/פגום → נמחק ומדווח.
  ניתוק באמצע = `failed` גלוי ומתחילים מחדש (v1 — אין חידוש מאמצע).
  המוניטור הוא פרוקסי-של-פרוקסי: הדפדפן ↔ הראשי ↔ מנהרה בערוץ המאומת ↔
  המשני ↔ המכונה; המשני מזדהה מול המכונה בסוד שלו (#839/#846) והראשי
  לעולם אינו רואה סוד של מכונה. ‏`docs/architecture/interfaces.md` סעיף 18; טבלת
  `storage_transfers`; ‏`?v=5.1`.

### תוקן
- **לשונית מותנית-יכולת (`[data-cap]`) נשארה נסתרת אחרי login עד רענון
  הדף** — ‏`ME` היה תשובת ה-login, שאינה נושאת `capabilities`; עכשיו נטען
  מ-`/me` אחרי ההתחברות (נתגלה כשלשונית "סניפים" לא הופיעה).

### אבטחה
- **‏`secondary_id` שהמשני הצהיר ב-JSON של pair-begin שימש כשם קובץ
  הטוקן** (`secondaries/{id}.token`) — משני מאומת-אך-עוין יכול היה לכתוב
  קובץ 0600 מחוץ לתיקייה, כמשתמש השרת (#883, פריט 3 של #860). הראשי
  גוזר את המזהה מה-SPKI שהמפעיל הצמיד; הצהרה שסותרת אותו → 400 **לפני**
  שליחת הקוד; שם הקובץ ו-`enroll_node` מאמתים `^sn_[0-9a-f]{16}$`.
  בקרה שלילית על main: `../x` → 200 ו-`<data>/x.token`.

## [v0.24.0] - 2026-09-16

מה שהמפעיל רואה: **דרייברים לפי דגם** (#720, MVP בלי DISM — הכרעת נדב
16/09): מלאי חומרה של כל מכונה ב-hello, ספריית חבילות דרייברים חתומות בקונסולה,
הנחה על ה-NTFS המשוחזר + `DevicePath`, וסטטוס "הונחו / אין התאמה / נכשל" בסבב.

### נוסף
- **דרייברים לפי חומרה — MVP בלי DISM** (‏#720, הכרעת נדב 16/09; ‏Issue-גג
  #888). ארבעה חלקים, ארבעה קומיטים, וכולם נחוצים לתמונה אחת:
  - **‏hello schema 2 נושא מלאי חומרה** (`agent/lib/inventory.sh`): ‏DMI
    (‏`sys_vendor`, ‏`product_name`, ‏`product_version`, ‏`board_name`),
    ‏PCI `vendor:device:class` של רשת (02) ואחסון (01) בלבד מ-sysfs, ו-TPM
    בשלושה מצבים (‏`null` = לא נבדק / אין / יש+גרסה). השרת שומר אותו
    **מגורסת** (`machine_inventory`, ‏`server/inventory.py`): שורה חדשה רק
    כשהמלאי השתנה, ‏`seen_at` = מתי הגרסה נראתה לראשונה; מלאי פגום או
    חסר אינו דורס גרסה קודמת. כרטיס המכונה מציג דגם/לוח/TPM/PCI.
  - **ספריית חבילות דרייברים** (`server/drivers.py`, ‏`<data-dir>/drivers/`):
    ‏`manifest.json` עם `match` (‏`{pci: [...]}` או `{vendor, model}`) ו-
    sha256 לכל קובץ. ייבוא `POST /api/console/drivers/upload` (admin, tar)
    באותו דפוס של ייבוא אימג' (#71): **כל** קובץ מאומת לפני שהחבילה מקבלת
    את שמה — sha שונה, קובץ חסר או קובץ עודף דוחים את כולה ולא משאירים
    דבר (עיקרון 6). חבילה בלתי-משתנה אחרי הכניסה; מחיקה מאחורי הקלדת שם
    (עיקרון 7). ‏`match()` טהורה ודטרמיניסטית: PCI גובר על דגם, אין
    התאמה = רשימה ריקה.
  - **staging בסוכן** (`agent/lib/postdeploy.sh`): אחרי השחזור, ההרחבה
    והשם, לפני הדיווח הסופי — רק ליעד Windows. ‏`GET /api/v1/agent/drivers?mac=`,
    הורדה + sha256 (כשל עוצר לפני שהדיסק נגוע), העתקה ל-`\ImageCtl\Drivers\<חבילה>\`
    עם קריאה-חזרה של כל קובץ מול ה-sha, והוספת `%SystemRoot%\..\ImageCtl\Drivers`
    ל-`DevicePath` — ‏Windows סורק אותה בעלייה הראשונה. **לעולם לא מפיל**
    שחזור: התוצאה (`staged` / `no_match` / `skipped` / `failed <סיבה>`)
    נוסעת בדיווח הסיום (`drivers`, ממשק 4), נשמרת ב-`session_members.drivers_json`,
    מוצגת ליד המחשב בקונסולה ונרשמת ביומן. "הושלם, דרייברים לא הונחו" הוא
    מצב, לא כשל שקט (עיקרון 5). **בקר אחסון שבלעדיו Windows לא עולה אינו
    מכוסה** — זה WinPE+DISM, v2. לינוקס — v2.
  - **דף "דרייברים" בקונסולה** (`server/static/drivers.js`): רשימה, ייבוא,
    מחיקה, ו"מתאים ל-N מכונות" לפי המלאי האחרון של המכונות הרשומות.
    ‏`?v=5.2`.
- **‏`hivewrite -x`** כותב `REG_EXPAND_SZ` (‏`agent/hivewrite.c`). ‏`DevicePath`
  הוא `%SystemRoot%\inf;…` ו-Windows מרחיב `%SystemRoot%` רק לסוג הזה;
  כתיבה כ-REG_SZ הייתה משאירה את הטקסט ומבטלת בשקט את כל הרשומות, כולל
  המקורית. הדגל מפורש, ו-`postdeploy.sh` תמיד מעביר אותו.

### שונה
- **‏`staging`** הוא מצב חדש בדיווח ההתקדמות (ממשק 4, ‏`VALID_STATES`) —
  לא סופי; ‏`stage_drivers` מחזיר ל-`done` בכל מסלול יציאה.

## [v0.23.0] - 2026-09-16

מה שהמפעיל רואה: **דף הפורטים** נטען מהשרת — כולל 5900 (מוניטור), 8082 ו-SSH
לתחנות (#822). ותיקון #874 על ברזל: ראש הכשל נשמר, והכבל של פורט 1 מסווג
ככבל (#890).

### נוסף
- **מסך "פורטים" בקונסולה קורא את הרשימה מהשרת** (#822, החלק שנשאר אחרי
  דף המוניטור ב-#825). `GET /api/console/ports` (מחובר, admin לא נדרש)
  מחזיר לכל פורט שם/מספר/פרוטוקול/יעד ומצב **שנקרא**, לא defs קבוע ב-JS
  שהיה חסר בו 5900 (מוניטור), 8082 (קיוסק) ו-SSH לתחנות — בדיוק מה
  שנדב חיפש ב-14/09 ולא מצא. מוניטור נגזר ממתג `monitor:stations`
  (‏`off`/`warn`, לא ירוק כשדלוק — הדלקתו פותחת RFB בכל תחנה), SSH
  לתחנות משתף את אותה ראיה כמו `/health` (התפריט שהשרת מגיש בפועל, לא
  ההגדרה), ו-8081/4011/8082 עדיין `off`/"לא אומת" בלי hook שקורא
  אותם — לא נצבעים ירוק בלי בדיקה (עיקרון 5). כל שורה כוללת הסבר קצר
  לפתיחה ב-FW. ‏`?v=5.3`.

### תוקן
- **כשל כתיבה על הכבל הידוע סווג "דיסק" — הראיה נחתכה** (#890, נמדד על
  ברזל 16/09, מחשב 2 פורט 1). ‏`ata_capture` שמר את 40 השורות **האחרונות**
  של הפורט, והקרנל כותב את הראיה **בראש** הרצף (`exception Emask` →
  `SError: { … 10B8B Handshk }` → הפקודות שנכשלו) ואחריה איפוס ורעש;
  מה שהגיע לשרת היה 13 שורות `ACPI cmd … filtered out` ו-`Read log 0x00
  page 0x00 failed` — והאחרון סווג כ"דיסק". עכשיו: 40 השורות **הראשונות**
  מה-`exception` הראשון (או `I/O error, dev`), בלי רעש האיפוס (‏ACPI
  filtered out / SATA link up / configured for UDMA / EH complete); ובשרת
  ‏`Read log … failed` לבדו — בלי UNC/ABRT/media error — הוא `unclassified`,
  לא `disk`. ה-dmesg האמיתי מהכשל (121 שורות) נוסף כ-fixture
  (`tests/fixtures/ata1-cable-failure-0916.txt`) ומסווג `cable`; הזנב
  כפי שנשמר ב-DB של המעבדה — `unclassified`. הרשומה השגויה ב-DB
  מנוקה ב"נקה", לא בקוד.


## [v0.22.0] - 2026-09-16

**v1 "מהדורת שיכפול"** (הכרעת נדב 16/09, #888): מה שהמפעיל רואה — מתג
"הפצה לכיתות ממחשב הבנייה" בהגדרות, כבוי כברירת מחדל; הכרטיס יורד
מתפריט מחשב הבנייה והשרת מסרב לסבב כיתה ממנו (#880).

### שונה
- **v1 "מהדורת שיכפול": "הפצה לכיתות" יורדת מתפריט מחשב הבנייה, והשרת
  מסרב לסבב כיתה ממנו** (#880, הכרעת נדב 16/09). הגדרה חדשה
  `class_deploy_enabled` — **ברירת מחדל כבוי, גם על שרת שכבר פרוס** —
  עם מתג במסך ההגדרות ("הפצה לכיתות ממחשב הבנייה"). ‏hello למחשב הבנייה
  נושא את הערך; תפריט הטקסט (`build_menu_options`) וה-GUI (`menu_class=`
  בקובץ המצב, `show[3]`) מציגים את הכרטיס רק כשהוא דלוק, ושדה חסר =
  כבוי. ההסתרה אינה האכיפה: `POST /api/v1/agent/sessions` מחזיר `409`
  `class_deploy_disabled` ורושם `class_deploy_refused` ביומן — אחרי
  הכניסה, כדי שסיסמה שגויה תישאר `401`. מסך הכיתות של הקונסולה
  (`/api/console/sessions`) אינו מושפע. קוד הכיתות לא נמחק (v2).

## [v0.21.1] - 2026-09-16

חמישה תיקונים בלי יכולת חדשה למפעיל: #859 #498 #860 (אבטחת שרת), #876 #858 (סוכן).

### תוקן
- **`hostname.json` נפתח בשורת יומן גם כשהשם נכתב בהצלחה** (#876, המשפחה
  של #875). ‏`write_hostname` נלכד ב-`$( )`, ו-`log "writing hostname …"`
  שבתוכו מודפס גם ל-stdout — כך שהקובץ היה `imagectl: writing hostname
  LAB1-05 into ControlSet001` ואז ה-JSON, וכל מי שיקרא אותו כ-JSON (#856)
  היה נופל. כל `log` בפונקציה נלכדת (‏`write_hostname` ועוזרותיה,
  ‏`_mount_*`, ‏`_control_set`, ‏`_umount_checked`, ‏`manifest_plan`,
  ‏`monitor_secret`) הולך ל-stderr; ‏`log()` עצמו לא השתנה. טסט-שומר
  חדש (‏`tests/test_captured_stdout.py`) מריץ כל אחת מהן עם זיופים
  ומשווה את **כל** ה-stdout לערך.
- **דיסק SCSI/SAS עם `SMART Health Status: FAILURE` נכתב כ-`unchecked`,
  ו-NVMe עם שגיאות שלמות נרשם 0 תמיד** (#858). שורת הבריאות מזוהה עכשיו
  ב-`FAIL`/`PASS` (תופס `FAILURE`, `FAILED!` של NVMe), ו-bit 3 של קוד
  היציאה של smartctl ("הדיסק נכשל") הוא ראיה חיובית ל-`FAILED` גם בניסוח
  זר. `Media and Data Integrity Errors:    N` של NVMe (שורה עם רווחים,
  לא טבלה) נקרא ל-`uncorrectable` — **לאחסון בלבד**, כי לפי #872 רק שורת
  הבריאות מכריעה. `smart.sh`: 294 שורות.

## [v0.21.0] - 2026-09-16

מה שהמפעיל רואה: **דיסק אדום לפני הסבב, בלי שהסוכן כותב על הדיסק** —
זיכרון כשלי כתיבה בשרת לפי מספר סידורי ומכונה+חריץ, סיווג **כבל / דיסק**
משורות ה-ATA של הקרנל, וכרטיס "דיסקים אדומים" עם "נקה" בקונסולה (#874).
הרקע: דיסק 1 במחשב 2, שנחשב "פגום" מ-12/09, נכשל בשתי מדידות באותו פורט
עם שני דיסקים שונים — `SError: { 10B8B Handshk }`, `ATA bus error` —
כלומר הכבל של SATA 0, לא הדיסק.

### תוקן
- **דיסק שהכתיבה אליו נכשלה נשאר עם GPT תקין של המקור ונראה "נכון"**
  (#845 → **הוחלף ב-#874**). הסימון על הדיסק (`IMAGECTL-FAILED`) חי
  בענף בלבד ונפסל בהכרעת נדב 16/09 לפני שיצא בתג: הסוכן אינו משנה דיסק
  מחוץ לשיכפול, ובראיה השנייה הדיסק נעלם מהקו לפני שנכתבה טבלה — לא היה
  מה לסמן. הפתרון: זיכרון בשרת (ראו "נוסף").
- **disk_event שלא נמסר הפך למילים ברשימת הדיסקים לכתיבה** (#875).
  ‏`smart_gate` לוכד את `smart_preflight` ב-`$( )`, ו-`log` של
  "not delivered" נכתב גם ל-stdout — curl כושל (שרת בעדכון) היה שולח את
  `imagectl:`, `disk_event`, `for`… כשמות דיסקים ל-`run_restore_drawers`.
  השורה הולכת ל-stderr. שאר המשפחה (‏`write_hostname` ועוד) — #876.

### נוסף
- **אדום לפני הסבב בלי לכתוב על הדיסק: זיכרון כשלי כתיבה בשרת, וסיווג
  כבל/דיסק משורות ה-ATA** (#874, הכרעת נדב 16/09). בכשל כתיבה הסוכן שולח
  בדיווח ההתקדמות את הסידורי, החריץ, ועד 40 שורות `dmesg` של הפורט של
  היעד (‏`ata<N>` / `I/O error`) — **כמות שהן**; השרת מסווג (‏`ATA bus
  error` / `10B8B` / `Handshk` / `ICRC` / `limiting SATA link speed` →
  כבל/חריץ; ‏`UNC` / `media error` / `ABRT` בלי סימן קו → הדיסק; אחרת
  לא סווג) ורושם בטבלת `disk_failures`. באתחול, hello עונה `disk_failures`
  — לפי **סידורי** (הדיסק נדד למכונה אחרת) **או** לפי **מכונה+חריץ**
  (שני דיסקים שונים נכשלו באותה חתימה על פורט 1 של מחשב 2 — הפורט אשם)
  — והמסך הממתין והשער לפני כתיבה צובעים אדום עם "כבל/חריץ SATA N" או
  "הדיסק". בקונסולה, מסך המחשבים: כרטיס "דיסקים אדומים" (סידורי,
  מכונה·דיסק, מתי, סיבה, שורות הקרנל) עם "נקה" — הדיסק הוחלף / הכבל תוקן.
  **שום דבר לא נכתב על הדיסק**, ואותיות `sd*` אינן זהות בשום מקום.

## [v0.20.0] - 2026-09-16

675 מיזוגים מאז `v0.19.0` (04/09) בלי תג, ושני תגים (v0.18.0, v0.19.0) בלי
Release בגיטהאב — "Latest" בטלפון של נדב הראה v0.17.0 מ-31/08. מהיום התג
וה-Release הם חלק משער המיזוג. מה שהמפעיל רואה בגרסה הזו: **המסכים
הנייטיביים החדשים** (מוקאפ vCenter, #828 — על ה-initrd הפרוס), **מוניטור
מאובטח** מהקונסולה גם למכונה בלי מסך (#839/#835), **SMART בשלושה צבעים**
לפי הכרעת נדב (#872), ורשימת דיסקים לפי SATA 1/2/3 (#867).

### אבטחה
- **המוניטור (‏5900) דרש אפס אימות, וכפתורי ההפעלה-מחדש/כיבוי (‏#781)
  היו נגישים לכל מכונה בוילן ההפצה** (#839). ‏5900 קשור לכתובת ההפצה,
  ו"רק ה-WebSocket של ה-admin מגיע לשם" לא היה נכון. מעכשיו לחיצת-היד
  של ה-RFB היא השער: המכונה מגרילה סוד לכל אתחול, מדווחת אותו ב-hello
  (`monitor_secret`), ומוסרת אותו ל-`imagectl-monitor` (`--secret-file`);
  המוניטור מציע רק סוג-אבטחה 2 ותשובה = 16 בייטי הסוד, והפרוקסי בשרת
  הוא היחיד שעונה — הדפדפן רואה את אותה לחיצת-יד None כמו קודם, ואף
  תשובת קונסולה אינה נושאת את הסוד. בלי הסוד: לא מסך, לא קלט, לא
  reboot. הכיוון (מכונה→שרת ולא להפך) מכוון: ‏hello אינו מאומת. המחיר
  המוצהר: הסוד נשלח כמו שהוא — מאזין פסיבי בוילן יכול לשדרו מחדש עד
  האתחול הבא (ראו `docs/architecture/interfaces.md` סעיף 14).

### תוקן
- **SMART: שלושה צבעים בלבד, ו-CRC אינו צובע** (#872, הכרעת נדב 16/09).
  🟢 אין F1 · 🟠 F1 (`overall-health: FAILED`) — שואל החלף/המשך, המשך
  כותב · 🔴 נכשל בשיכפול הקודם — שואל החלף/המשך, המשך מדלג. בלי תשובה
  = "המשך" של אותו צבע (שיכפול הוא תמיד עם אדם ליד המחשבים). המונים
  (5/197/198/199) נשמרים ב-hello כמידע ולא מכריעים; CRC נמדד **כהפרש**
  בסבב (`crc_delta`, "לבדוק כבל" במסך החדר). הרקע: אחרי #866 התברר
  ש-6/6 דיסקי הכיתה נושאים 137–55,242 שגיאות CRC — היסטוריה של כבלים,
  לא מצב הדיסק — וההכרעה הקודמת (#652) הייתה עוצרת ושואלת על כולם.
- **מאפיין SMART 199 נקרא לפי מזהה, לא לפי שם** (#866). סמסונג מדפיסה
  `CRC_Error_Count` ולא `UDMA_CRC_Error_Count`; לפי שם נקרא 0 תמיד.
- **מסך המשכפל: דיסקים לפי SATA 1/2/3 ולא לפי sdX, ודיסק שנכשל
  בשיכפול הקודם מוצג באדום** (#867). אותיות sdX מתחלפות בין אתחולים.
- **13 טסטים נתנו לסוכן `DEVROOT=/dev` — ומאז #845 כל ריצת שער סימנה
  `IMAGECTL-FAILED` על דיסק המערכת של שרת המעבדה** (#870). DEVROOT
  תמיד בתיקיית הטסט; שומר סטטי ושומר בזמן-ריצה (`hostguard.detach_host_dev`).
- **`monitor.c`: ‏`fatal()` מתוך callback של LibVNCServer, סדר ניקוי הפוך,
  מקשים שנשארים לחוצים, זומבי מ-`run_power`** (#839). אירוע uinput שנפל
  נרשם ונזרק במקום להפיל את המוניטור; ‏`rfbScreenCleanup` לפני
  ‏`UI_DEV_DESTROY`; ‏`clientGoneHook` משחרר מקשים/כפתור של לקוח שנעלם
  באמצע לחיצה; ‏`SIGCHLD=SIG_IGN` מונע זומבי כש-`exec` נכשל.
- **דיסק שהכתיבה אליו נכשלה נשאר עם GPT תקין של המקור ונראה "נכון"** (#845).
  הטבלה נכתבת לפני הזרם, ומחשב 2/sda שנפל אחרי 15.5MB הראה ב-`sgdisk -p`
  בדיוק מה ש-sdb/sdc שהצליחו הראו. עכשיו `agent/lib/failmark.sh` מסמן את
  היעד שנכשל **על הדיסק**: כל מחיצה נקראת `IMAGECTL-FAILED` וה-ESP מאבדת
  את סוג EF00 — סימון הפיך, לא מחיקה. רק היעד שעובר עכשיו ל-failed אחרי
  שהטבלה נכתבה עליו; תוצאת הסימון (או כשלו) מצטרפת לשדה `error` של היעד.

### תיעוד
- **תבנית ה-Issue ו-`SECURITY.md` הניחו שהריפו ציבורי** (#586).
  `the private repository` פרטי; Issue שם מותר. מה שאסור הוא פרסום לציבורי
  לפני תיקון. Advisory נדרש כשהחולשה נוגעת למי שאינו נדב.

### נוסף
- **בחירת המחיצה להרחבה מהקונסולה, בפתיחת סבב כיתה או סבב חדר** (#59).
  עד כה השחזור בחר לבד את מחיצת windows/linux האחרונה פיזית להרחבה
  (#58), בלי דרך לעקוף — וזה בדיוק הרגע היחיד שבו גודל היעד ידוע.
  חלון בפתיחה מציג את הבחירה האוטומטית (שם המחיצה וגודלה) ומאפשר
  לכבות את ההרחבה או לבחור מחיצה אחרת ביד; שתיהן נרשמות ביומן. סוכן
  ישן, ומשיכת יוניקאסט/התפריט הטקסטואלי שלא עודכנו, ממשיכים בבחירה
  האוטומטית בלי לדעת שהשדה קיים.
- **שלושה בוטים שתופסים מצב שסטה.** `bot-stale-baseline` מדווח כשהבסיס
  של `ic-test` חסר, יושב ב-`/tmp`, או נושא SHA שאינו `origin/main`;
  `ic-test` עצמו כותב עכשיו ל-`/srv` עם ה-SHA בשורה הראשונה.
  `bot-initrd-lag` משווה את הקומיט האחרון ב-`agent/` לזמן ה-initrd
  הפרוס (קריאה בלבד). `bot-env-drift` משווה `packages:` ב-user-data
  מול מה ש-`apt` התקין בפועל, בשני הכיוונים, ותופס גם `gcc` בלי
  `libc6-dev`. הבדיקה רצה לפני המודל; המודל מנסח.

### אבטחה
- **ניתוק על חוסר פעילות הכריז "נותקת" בלי ראיה שהסשן בוטל** (#516).
  כש-`POST /logout` נכשל (רשת, 500, שרת מתאתחל) הקונסולה עברה למסך
  כניסה עם "נותקת עקב חוסר פעילות." — העוגייה נשארה, ורענון החזיר
  מחובר. המפעיל קם בהנחה שניתק. עכשיו "נותקת" מוצגת רק אחרי תשובה
  חיובית מ-`/logout`; כשל משאיר את הסשן המקומי ומציג שהניתוק לא אושר.
- **ארבע בקשות `GET` שלחו תחנת כיתה לדיסק המקומי** (#536).
  `GET /boot/menu?mac=<MAC>` לקח את ה-MAC **מהשאילתה**, ו-`bootguard`
  ספר עליו ניסיון אתחול. ‏`ATTEMPT_LIMIT` הוא 3, ולכן ארבע בקשות בלי
  גוף ובלי עוגייה גמרו את תקציב האתחולים של תחנה חיה בסבב פתוח —
  והשומר שנועד להציל אותה מלולאה הוא זה שהוציא אותה. ‏MAC בשאילתה אינו
  זהות; מה שכן ניתן לאמת הוא על איזו מכתובות השרת הבקשה התקבלה. בקשה
  שלא התקבלה על וילן ההפצה נענית כרגיל — תחנה מחוץ לווילן ממשיכה למשוך
  תפריט לגיטימי (תרחיש 3, ‏#39) — אבל **אינה נספרת**, מאותה סיבה שהיא
  אינה נספרת כלולאה ב-#42: מחוץ לווילן ההפצה השרת אינו מגיש את שרשרת
  האתחול, ואין לו ראיה שהמכונה עברה בתפריט שלו.

### תוקן
- **התחלת סבב עם 0 חברים נדחית ב-409, בשני המסלולים** (#843). "התחל"
  לפני שמישהו הצטרף העביר את הסבב ל-`running`; ‏hello מצטרף רק לסבב
  `open`, ולכן זה היה גל שאיש אינו יכול להצטרף אליו — udp-sender המתין
  180ש', רשם `send_failed`, והסבב נשאר `running` ריק (נמדד במעבדה
  15/09). עכשיו `start_now` דורש מצטרף אחד לפחות, הסבב נשאר `open`,
  ומסך החדר מציג את הסיבה במקום לחיצה שקטה.
- **‏28 כשלי ווינדוס שהסתירה את הרגרסיה** (#312). לכל אחד הכרעה:
  תיקון בטסט (מפריד נתיב, `port_holders` ב-fixture, `as_posix`) או
  `skipif` מוצהר (`/proc/net/udp`, ‏`SO_BINDTODEVICE`, קישור סימבולי).
  שער ריק: כשל חדש בווינדוס אינו "עוד אחד מה-28" — הוא נופל בשמו.
- **`fanout` דיווח `ok` על EOF מוקדם** (#427). קלט ריק, יצרן שנהרג
  באמצע, ואימג' שלם נכנסו לאותו מסלול EOF ויצאו 0. עכשיו זרם ריק הוא
  `failed empty stream`; `FANOUT_EXPECTED_BYTES` שאינו תואם הוא
  `short`/`long stream` בשמו; `fsync` (לא-FIFO) ו-`close()` שנכשלו
  אינם `ok`. המונה נשאר בייטים שנמסרו לצינור — `done` הוא האישור.
- **מגירה סומנה `done` בלי שנכתב עליה** (#426). `run_restore_drawers`
  הריץ `manifest_plan | while` — בלי `pipefail` כשל היצרן נבלע, לולאה
  על אפס שורות נראתה כמו סיום, ו-`finish_grow` חתם `done` על דיסק
  שרק טבלת המחיצות שלו שונתה. עכשיו התוכנית מחומרנת לקובץ, נספרות
  מחיצות כתובות מול צפויות, ואי-התאמה = `failed` בכל המגירות החיות.
- **ארבעה שערי בדיקה ב-diskless-PXE הדפיסו PASS כשהסריקה לא רצה** (#304).
  `if grep ... 2>/dev/null` על נתיבים יחסיים קרא את קוד 2 של grep
  (שורש חסר, גם עם התאמה) כנקי. עכשיו 0/1/אחר נקראים בשם, PASS נושא
  כמה קבצים נסרקו, אפס הוא כישלון, ו-`ci/run.sh` קורא בנתיב מוחלט.
- **חמישה בודקים ב-diskless-PXE דיווחו תקין כשמצאו בעיה** (#307).
  `check-host`/`check-runtime-deps` יצאו 0 אחרי WARN; `wait-online`
  התעלם מה-URL; `imagectl-env` נפל ל-localhost; `detect-target` איבד
  דיסק עם MODEL דו-מילתי ו-`plan.sh` קיפל שלושה מצבים ל-NONE.
- **`diskless-pxe/rootfs/build.sh` בנה rootfs ריק והכריז הצלחה** (#385).
  רשימת החבילות הורכבה מ-`$(grep ... packages.txt | tr ...)`. קוד
  היציאה של ה-pipeline הוא של `tr`, ו-POSIX זורק את הסטטוס של `$( )`,
  אז `packages.txt` חסר או שכולו הערות — ו-`apk add` בלי ארגומנטים
  יוצא 0. עכשיו `grep` נקרא בשם (0/1/2), הנתיב נפתר מ-`$0`, והצלחה
  מדפיסה כמה חבילות הוזנו.
- **ספרייה משותפת חסרה בבניית ה-initramfs יצאה 0** (#507). `copy_libs` בלע `ldd` שנכשל (`|| true`, תיקון #12 ל-wrapper של shell), דילג על `libfoo.so => not found` ב-`[ -f ]`, ובלע `cp` שנכשל. wrapper נשאר תקין; ספרייה חסרה או `cp` שנכשל עוצרים את הבנייה.
- **`--http-root` של המתקין לא הגיע לשרת** (#395). הדגל יצר תיקייה
  והעתיק אליה את הקרנל, אבל `imagectl-server.service` הריץ את
  `server.main` בלי `--boot-dir` — ברירת המחדל הקשיחה
  `/srv/imagectl/boot`. התקנה שנראית מוצלחת, ואף תחנה אינה יכולה לעלות.
  עכשיו המתקין מזריק `--boot-dir` דרך `IMAGECTL_STORAGE_ARGS` ב-drop-in.
- **ה-uploader של `phase_capture` לא שלח `X-Imagectl-Task-Token`** (#737).
  `PUT /api/v1/capture/.../files` נעצר ב-401, וה-e2e לא בדק את נתיב
  ההעלאה מעבר לאימות. עכשיו האסימון מ-hello נשלח כמו אצל הסוכן האמיתי.
- **`disk_guid` ריק באימג' Windows עבר בקליטה בשקט, והשחזור הגיע ל-`done`**
  (#571). הסוכן גוזר את `disk_guid` מ-`sgdisk -p`, וכשל קריאה יוצא
  מחרוזת ריקה; `apply_gpt` דילג על `sgdisk -U` בלי אפילו ה-WARNING של
  #537, והמגירה הגיעה ל-`done` עם טבלה בלי GUID הדיסק — `winload.efi
  0xc000000e` על כל מחשב משוחזר (#26). `disk_guid` ריק אינו "אין צורך
  ב-GUID" אלא "לא ידענו מה ה-GUID" (עיקרון 5). ההכרעה: אימג' Windows
  אינו תקין לשחזור בלי `disk_guid`, ולכן **הקליטה מסרבת לו, לא השחזור**
  (עיקרון 6) — נתפס לפני הכניסה לספרייה. לינוקס פטור: הוא אינו קשור
  ל-`disk_guid` (GRUB מאתר לפי UUID של מערכת הקבצים), ונקלט בלעדיו כרגיל.
- **`idea-refine` דילג על כל תפקידו ויצא ירוק** (#538). התנאי
  `if: hashFiles('/tmp/thread.json') != ''` נשען על פונקציה שפועלת רק
  בתוך `GITHUB_WORKSPACE`: כל קובץ שמחוץ לעץ מסונן ("Ignore '...' since
  it is not under GITHUB_WORKSPACE") והתוצאה היא **מחרוזת ריקה תמיד** —
  נמדד מול המימוש עצמו (`@actions/glob` 0.7.0). לכן השלב שכותב `READY`,
  ואיתו שאלות ההבהרה ופתיחת ה-Issue, לא רצו מעולם. התנאי הוסר, ובמקומו
  `test -s /tmp/thread.json` שנכשל בקול. `tests/test_workflow_hashfiles_scope.py`
  נופל אם `hashFiles` תחזור לנתיב שמחוץ ל-workspace באיזשהו workflow.
- **`sgdisk -U` שיצא 0 לא הוכיח שה-GUID נכתב** (#572). `verify_table`
  כבר קרא את המחיצות בחזרה מהדיסק, אבל לא את GUID הדיסק. קוד יציאה 0
  מ-`sgdisk -U` אינו ראיה — אותה משפחה כמו `ethtool -s` (R17). Windows
  קושר את ה-BCD ל-GUID הזה; ערך שלא נכתב הוא `winload.efi 0xc000000e`
  על כל כיתה (#26). עכשיו הטבלה נקראת בחזרה, והיעד נכשל אם ה-GUID
  על הדיסק אינו מה שהוצהר במניפסט.
- **כישלון בקביעת GUID הדיסק היה WARNING, והשחזור הגיע ל-`done`** (#537).
  `sgdisk -U` הוא השורה היחידה ב-`apply_gpt` שלא הסתיימה ב-`|| return 1`.
  Windows קושר את ה-BCD ל-GUID של הדיסק; GUID אחר הוא `winload.efi 0xc000000e`
  — בדיוק #26. עכשיו היעד נכשל, כמו כל שכניו בטבלה. מגירה אחת שנכשלה
  אינה עוצרת את השאר — `return 1` מ-`apply_gpt` כבר מטופל כך בחדר השיכפולים.
- **כשל דיסק במגירה היחידה דווח כקטיעת זרם** (#440). כשהמגירה האחרונה
  מתה באמצע כתיבה, `fanout` מפסיק לקרוא ויוצא 1, המקור מקבל SIGPIPE
  (`source.rc=141`), וההודעה אמרה "הזרם נקטע — הבעיה ברשת או בשרת".
  התקלה בכבל SATA. עכשיו כל מגירה מדווחת מ-`fanout.out` ומהלוג של
  partclone, ו-141 אינו סיבת רשת.
- **כשל בהרחבת מערכת הקבצים נרשם כ-`done`** (#444). `ntfsresize` /
  `resize2fs` / `e2fsck` שיצאו בכישלון, או מערכת קבצים בלי כלי הרחבה
  אחרי שה-GPT כבר גדל, השאירו את היעד `done` — Disk Management הראה
  מחיצה גדולה עם NTFS בגודל המקורי. עכשיו היעד `failed` וההודעה נוקבת
  בהרחבה.
- **רץ הסוכנים כבר לא מציג כשל של `gh`/`git` כ"אין עבודה"** (#428).
  קוד היציאה נבדק אחרי כל קריאה; ה-SHA נקרא בחזרה לפני שהפרומפט מצהיר
  על `origin/main`; ותביעה יתומה משתחררת ב-`finally` גם כש-`grok.exe`
  נזרק. שורת `STATUS:` מבחינה בין `no-work` לכשל, ו-`LastTaskResult`
  אינו 0 על נפילה. ויתור ה-`--jq` עצמו כבר נחת ב-#423; כאן הוא עובר
  ל-`runner-lib.ps1`, שם `Convert-GhJson` הופך פלט ריק לחריגה במקום
  לרשימה ריקה.

---

## [v0.19.0] - 2026-09-04

66 מיזוגים מאז `v0.18.0` ואפס תגים — בדיוק #240 ("תג לכל תיקון הוא כלל
בלי מנגנון"). הרשומה הזו סוגרת את הפער.

### נוסף
- **שלוש שורות חדשות על מסך הבריאות והיומן — אלה השינויים שהמפעיל רואה
  בגרסה הזו, והם מה שמצדיק את קפיצת ה-minor.**
  - **סינון וחיפוש ביומן הקונסולה** (#115). עד היום היומן היה גלילה
    בלבד — כדי למצוא מתי מחשב מסוים פנה, המפעיל גלל ידנית מאות שורות.
    מהיום יש סינון לפי סוג אירוע, מכונה/MAC, חיפוש חופשי וטווח תאריכים,
    בלי טעינה מחדש. **המלכודת שנתפסה בטסט:** מה שהמפעיל מקליד אינו מה
    שכתוב בעמודת `detail` הגולמית — חיפוש "מבנה מדעים" חייב למצוא קבוצה
    ששמורה כ-`grp_9f2e`, ולכן הסינון לפי מכונה וחיפוש חופשי רץ **אחרי**
    התרגום לעברית, לא על המזהה הפנימי. החיפוש סורק את 20,000 השורות
    האחרונות בלבד; כשיש יותר, המסך מציג במפורש "החיפוש מכסה רק את
    השורות האחרונות" — כדי ש"לא סרקנו הכול" לא ייראה כמו "אין תוצאות".
  - **שורה אדומה בבריאות למחשב שפונה מרשת שאינה וילן ההפצה** (#137). עד
    היום פנייה כזו נרשמה ב-`log.info` אחד ומסך הבריאות הכריז "הכול
    תקין" — כפי שקרה בפועל כששתי מכונות פנו מ-`10.10.10.8` בעוד וילן
    ההפצה הוא `10.44.12.10`. הבדיקה יושבת במודול נפרד
    (`server/foreign_vlan.py`) ולא כהרחבה של שומר "מחשבים שנופלים
    לסוכן" הקיים, כי מכונה יכולה להיכנס לכל היותר לאחת משתי הרשימות.
  - **שורה בבריאות לסחיפת ה-shim מול העדכון שדביאן מספק** (#329).
    המתקין מעתיק את ה-shim ל-TFTP פעם אחת ואינו מתעדכן; מיקרוסופט
    דוחפת עדכוני SBAT שמבטלים גרסאות ישנות, ואז **הצי כולו** מפסיק
    לעלות ב-PXE בבת אחת בלי שנגענו בקוד. הבדיקה משווה sha256 בין
    ה-TFTP לעותק החי, ומדווחת "לא ידוע" (לא "תקין") כשאי-אפשר לקרוא
    אחד מהם — שרת בלי `shim-signed` מותקן לא היה מדווח בטעות ששרשרת
    האתחול עדכנית.
- **תעודת החתימה של מטעני האתחול נגזרת בקליטה** (#85). הלנובו קיבל
  שחזור מלא של tiny11 ואז "Secure Boot Violation": `bootmgfw.efi` חתום
  ב-Windows UEFI CA 2023 והקושחה מכירה רק תעודות 2011. עכשיו הקליטה
  קוראת את טבלת האישורים על ה-ESP וכותבת אותה למניפסט כ-**רשימה**
  (מדידה על שרת המעבדה הראתה שקובץ PE אחד נושא יותר מחתימה אחת —
  מחרוזת יחידה הייתה מסתירה בדיוק את זו שהמכונה כן מכירה). שדה חסר
  (`[]`, אין חתימה) ושדה שנכשל (`null` + `boot_ca_error`) הם שני מצבים
  שונים ונכתבים תמיד — עיקרון 5. **הסקופ הוא קליטה + מניפסט בלבד: אין
  עדיין שער שחזור שקורא את השדה**, וזה מחכה להכרעה נפרדת.
- **בחירת initrd לפי תפקיד המכונה** (#32) — `build`/`classroom` יקבלו
  `initrd.img.gui`, ‏`cloner` וכל תפקיד אחר ממשיכים ב-textual. הבחירה
  נגזרת פעם אחת ב-`decide()` **לפני** ההסתעפות, ולכן מכסה מבנית את כל
  שלושת מסלולי ה-AGENT של cloner ולא רק את אחד מהם — בדיוק המלכודת
  ש-#320 (למטה) נפל בה. ברירת המחדל הבטוחה: תפקיד לא מוכר, MAC לא רשום
  או שקובץ ה-GUI פשוט לא קיים — כולם נופלים לטקסטואלי. **הפיצ'ר עדיין
  אינו פעיל בפועל**: לא נבנה `initrd.img.gui` ולא הוגדר squashfs
  (#327); זה יתחיל לעבוד ברגע שמישהו יריץ `--with-gui` ויעתיק אותו.

### שונה
- **הריפו התפצל לארבעה** (#318, #321, #324). מעבר `ImageCtl` ← בדיוק
  אותו שם ← נתפס תוך כדי על ידי פרויקט **אחר**, ומרגע השינוי כל הפניה
  קשיחה ל-`NadavOked/ImageCtl` בריפו הזה הצביעה על קוד זר: ה-badges
  ב-README הציגו בפועל CI ירוק **של הפרויקט האחר** (200, לא 404 — סימן
  ירוק אמיתי, רק לא שלנו), וכלים כמו `agent-claim.sh` ו-`record-deploy.sh`
  היו תובעים Issues ורושמים פריסות בריפו הלא נכון בהצלחה לכאורה. תוקנו
  87 הפניות תיעוד, חמישה כלים הפכו ל-fail-closed על ריפו לא מוגדר
  (`${IMAGECTL_REPO:?...}` במקום ברירת מחדל מנחשת), וה-badges המתים
  הוסרו. `CLAUDE.md` מתעד עכשיו את המבנה הרבע: `the private repository`
  (פרטי, ריפו העבודה) / `ImageCtl` (ציבורי, פרסום נקי) / `otogit`
  ו-`otogit-lab` (תשתית סוכנים). זה שינוי מבני, לא יכולת שהמפעיל רואה,
  ולכן patch מבחינת הכלל — אבל ראוי לפסקה כי הוא משנה לאן כל הפניה
  עתידית בריפו צריכה להצביע.
- **שומר הפקודות ההרסניות (`guard-bash.py`) עבר מסע שלם בגרסה אחת: נוצר,
  הוסר בטעות, הוחזר, וחודד.** נוצר (#250) כ-hook על כל קריאת Bash
  שחוסם מחיקת דיסק, הרס בגיטהאב וגישה למעבדה. ב-04/09 הוא הוסר
  מ-`.claude/settings.json` באבחון שגוי — "הקבצים עברו ל-otogit" —
  שהתברר כתלוי בעץ עבודה שפיגר 148 קומיטים; ברגע שעודכן, הקבצים היו
  שם כל הזמן. הוחזר (#359) עם בקרה שלילית לשני הכיוונים: חוסם פקודה
  הרסנית **וגם** מעביר פקודה תמימה — כי שומר שחוסם הכול הוא ניתוק, לא
  שומר. ואז חודד (#367/#368): הכלל שהגביל גישה למעבדה חל גם על
  **המתאם** עצמו, לא רק על subagents — בזמן שהנימוק בקוד כבר אמר
  שהחריגה מיועדת רק להם. ההבחנה נגזרת ממנגנון, לא ממוסכמה: `.git` הוא
  תיקייה בעץ הראשי וקובץ בכל worktree מבודד, וזה בדיוק הגבול בין מתאם
  לסוכן מאז שכל סוכן מקביל רץ ב-`isolation: "worktree"`.
- **הצי החינמי של מודלי הסוכנים נבנה והוכח בלייב** (#277, #278, #279,
  #280, #281, #283, #284, #287, #300, #305). ‏LiteLLM עם hard $0 על
  פרוקסי מקומי, ניתוב לפי אופי המשימה (`routing.order` הפך ממסמך
  למנגנון אכוף), מונה תקציב שנבדק **לפני** ההוצאה ולא אחריה, ומודל
  Cloudflare חי שהוכיח שהצי כולו עובד קצה-לקצה. זו תשתית לעבודת
  הסוכנים על הריפו — לא קוד ImageCtl.
- **תשתית איכות ל-PR-ים ולסוכנים** (#249, #251, #253, #259, #262,
  #263, #264, #267, #268, #270, #271, #273, #274, #289, #325, #339).
  בין השאר: `tools/issue-new.sh` שמסרב **לפני** `gh issue create` אם
  חסר label/milestone (במקום Issue חצי-פתוח ש-70 מהם הצטברו); שער
  ‏shellcheck שמכשיל אם קובץ מעטפת חדש לא נרשם; דילוג מוצהר וסָפיר
  לטסטים שדורשים PowerShell שאינו על שרת המעבדה, במקום אדום קבוע
  שמנרמל את עצמו (#295); ותיקון להרנס בדיקות הקליטה שלא טען
  `manifest.sh` — ריצה "מוצלחת" הפיקה מניפסט עם `os: ""` וכל שדה ריק,
  והטסטים ספרו את זה כהצלחה (#338). כל אלה תשתית עבודה על הריפו, לא
  שינוי בהתנהגות ImageCtl.
- **חבילת `vendor/diskless-pxe` הוכנסה לגיט, ושומר הפקודות ההרסניות
  שבתוכה תוקן** (#231). ‏`! grep ...` תחת `set -eu` הוא pipeline
  שמתחיל ב-`!`, ו-`set -e` **אינו** חל עליו — דווקא כש-grep מצא פקודה
  הרסנית, השומר הדפיס אותה והכריז `PASS`. **החבילה הזו עדיין אינה
  מחוברת לאף מסלול אתחול בריפו** (לא ל-`dhcp-boot`, לא ל-`grub_menu.py`
  ולא ל-`/srv/tftp`) — החלטת האינטגרציה פתוחה ב-#230 וממתינה לנדב,
  ולכן זה תיקון בקוד ספרייה שיושב בגיט ולא עוד תכונת ImageCtl.
- `tools/lab/demo-steps.ps1` מקבל מצב `-Physical` להדגמה מול חומרה
  אמיתית ולא רק Hyper-V (#296) — התסריט נתקע ביום ראשון מול המנהל כי
  15 קריאות הלכו ל-cmdlets שלא קיימים בלי VM. כלי הדגמה בלבד, לא
  ImageCtl עצמו.

### תוקן
- **שני באגים, לא flake: תהליכון uvicorn ממוחזר השבית כתיבה לדיסק עד
  אתחול השרת** (#272). טסט מקביליות נפל בקביעות ב-CI ולא במעבדה — כי
  ‏`/tmp` במעבדה הוא tmpfs (‏`fsync` הוא no-op שם) ובכל CI זה דיסק
  אמיתי; עם השהיית כתיבה מלאכותית (`dm-delay`) הכשל חזר 3 מתוך 3
  במעבדה גם כן. שני באגים באותה שורה: **הרעבת כותב** (‏sqlite אינו
  מבטיח הוגנות בין כותבים מתחרים) ו**כתיבה שנכשלה שמרעילה את החיבור**
  — ‏pysqlite פותח `BEGIN` לפני כל DML, ובלי `rollback` החיבור נשאר
  בתוך טרנזאקציה; מכיוון שתהליכוני uvicorn ממוחזרים, **אירוע עומס
  חולף אחד השבית כתיבה לאותו תהליכון עד שהשרת עצמו אותחל מחדש** — לא
  עד שהנעילה השתחררה. התיקון: `with _write_lock, writing(conn)`
  סביב `net_seen`.
- **אותו חיבור מורעל תוקף גם את `agent_loops.note` ואת `bootguard.guard`
  — והשני חמור יותר** (#356). ‏#272 (למעלה) תיקן את התבנית ב-`net_seen`
  בלבד; שני אתרים נוספים על **אותו מסלול hello** נשאו את אותה צורה
  בדיוק. חיבור מורעל ב-`bootguard._record` מחזיר `None`, ‏`guard`
  מתרגם את זה ל-`exhausted` (נכון — עיקרון 5), והתוצאה בפועל היא
  **כיתה שלמה שנשלחת לדיסק המקומי באמצע סבב** כי שומר שקט אינו שומר.
  התיקון זהה: אותם שני מנגנונים קיימים ב-`server/db.py`, בלי הרחבה.
- **שידור בלי מקבל אחד נתקע לנצח במקום להיכשל** (#341). `server/sender.py`
  לא העביר `--start-timeout` ל-`udp-sender`, ו-`--max-wait` מתחיל
  לספור **רק מהמקבל הראשון** — כלומר מחשב שיכפול שלא הגיע (כבל, BIOS,
  ‏VLAN, MAC לא רשום) השאיר את המסך קפוא בלי הודעה, לא נכשל. עכשיו
  הפקיעה אומרת בפירוש כמה זמן חיכתה ומה לבדוק, וחלון-חסד סוגר את
  האבחון הזה משני הכיוונים כדי שכישלון ארוך-אחרי-שהתחיל לא ייוחס
  בטעות ל"אף אחד לא הצטרף".
- **סבב חדר נשאר תלוי לצמיתות על גל שנסגר מתחתיו** (#217). שני מחשבי
  שיכפול שסיימו גל **באותו רגע** — בדיוק התרחיש שחדר השיכפולים קיים
  בשבילו — יכלו להשאיר את `room_rounds` מצביע על סשן סגור, בלי אף
  מסלול חזרה: החדר נראה פתוח, אף גל לא נפתח, וה-`open_round` הבא מסרב
  "כבר יש סבב פעיל". ענף שלישי ב-`tick` מאפס את המצביע **לפני** שהוא
  פותח גל חדש, ומאמץ יורש יתום שכבר תפס את חריץ המולטיקאסט לפני שהוא
  מנסה לפתוח שני. (מתוך שלוש טענות שנבדקו כאן — רק אחת אושרה; שתיים
  הופרכו במדידה, וגם הן קיבלו טסט ששומר על ההפרכה.)
- **`record_hello` יכול להשאיר נעילת כתיבה יתומה** (#290) — הצטרפות
  לסבב פתוח היא רצף של שתי כתיבות בלי `rollback` ביניהן; כשל בשנייה
  משאיר את הנעילה תפוסה, וכל השאר ממתינים `busy_timeout` שלם ומקבלים
  "database is locked" שנראה כמו עומס. `db.writing()` — context manager
  חדש — משחרר במקום אחד. סקירת **כל** רצפי הכתיבה ב-`server/` (AST,
  שני מעברים כולל כתיבה בתוך לולאה) מצאה שלושה אתרים דומים נוספים,
  נדירים ו-admin-only — נפתחו כ-#313 ולא תוקנו "על הדרך".
- **תפריט ה-GRUB של cloner נשא דרך מלאה אל הכוננים למרות ערך תפריט
  יחיד** (#320). `decide()` החזיר את ההחלטה הנכונה מאז #17, אבל הקובץ
  שנרנדר בפועל עדיין כלל `chainloader` ו-`menuentry "Boot from local
  disk"` — לא גלויים בתפריט (`timeout=0`, `hidden`), אבל קיימים בקובץ
  על 12 נתיבי EFI. הדרישה היא **שלא תהיה דרך, לא שקשה להגיע אליה.**
  התיקון מכסה גם את שני מסלולי ה-AGENT הנוספים של cloner
  (`session-joinable`, `task-assigned`) שהכיסוי המקורי פספס.
- **הנפילה לדיסק המקומי לא הבחינה בין UEFI ל-Legacy BIOS** (#323).
  על i386-pc, `chainloader` על קובץ `.efi` אינו מעלה Windows — הוא
  קופץ לתוך PE שהקושחה אינה יודעת להריץ, וסורק את כל הכוננים במצב לא
  מוגדר. שומר על `$grub_platform` (שהמידע קיים בו לפני כל פנייה
  לשרת) עוצר את הנפילה ל-Legacy; UEFI ממשיך לקבל את ההתנהגות הקודמת
  בדיוק.
- **פריסה שגדולה מכונן היעד נכשלה בבדיקה ידנית מול כיתה, אחרי שהקליטה
  כבר רצה שעה** (#87). `capture_disk` מעולם לא שאל "לאיזה כונן האימג'
  הזה נכנס". שער חמישי חדש (`CAPTURE_TARGET_BYTES`) מסרב **לפני**
  שבייט אחד זז, ומדווח שלושה מספרים: מה צריך, מה יש, כמה לכווץ.
  `null` ברצפה פירושו "לא נבדק", לא "נבדק ונכנס" — עיקרון 5. הכיווץ
  עצמו (destructive ל-NTFS של מחשב הבנייה) לא מומש כאן; #87 נשאר פתוח
  עד שמחיצה מכווצת בפועל.
- **כישלון שידור נקב רק ב"נכשל", לא במי שמחזיק את הפורט** (#202).
  ‏`udp-sender` שנתקל בפורט תפוס דיווח כתובת+פורט בלבד; עכשיו ההודעה
  נוקבת בתהליך ובמזהה שמחזיקים אותו, כשמצליחים לזהות אותו — ומבחינה
  בין "תפוס" ל"פנוי אבל הכישלון לא ברור" ל"הבדיקה עצמה לא רצה", שלוש
  הודעות לשלושה מצבים שונים ולא אחת שקוברת אותם.
- **מסך הכשל בעליית GRUB אמר "No operating system found" גם כשמערכת
  הפעלה נמצאה ונדחתה** (#61). `chainloader` שנכשל לא מנע את הרצת
  `boot` שאחריו, ולכן ההודעה האמיתית נקברה מתחת ל"you need to load
  the kernel first". מהיום המסך אומר איזה מטען אתחול נמצא ובאיזה
  נתיב, ומזכיר Secure Boot רק כש-GRUB עצמו מייצא סימן שהוא זה שדחה
  אותו — לא ניחוש.
- **התקנה נקייה על מכונה ריקה תוקנה בחלקה — שני חוסמים נשארים פתוחים,
  בכוונה** (#166, refs ולא closes). תוקנו: הרשאת הרצה שחסרה לסקריפט
  ההתקנה (`command not found` על קובץ קיים), הודעת שגיאה שקטה בכישלון
  clone לריפו פרטי, תיעוד שהבטיח שלב שלא קיים, ו-VHDX sparse שמפיל
  ‏Hyper-V בלי הסבר. **מה שנשאר לא נבדק: `/srv/imagectl/boot` נשארת
  ריקה אחרי ההתקנה** (המתקין לא בונה initrd ולא מעתיק קרנל — כל תחנה
  עם משימה מקבלת 404), **ומסך הבריאות מדווח `ok` על שרת שלא יכול
  לשרת** — הבדיקה בוחנת רק את שורש ה-TFTP. שני אלה הם עיקרון 5 בדיוק
  במקום שהמפעיל מסתכל בו, ונשארים פתוחים ל-#166.

### תיעוד
- `docs/architecture/grub-generator.md` מתאר עכשיו את מה שהקוד עושה בפועל, ולא רק
  את הכוונה מאחוריו (#198) — אותה מחלה שהניעה את `AGENTS.md` בעבר:
  מסמך שמבטיח מה שאין.
- מסירות יומיות, יומני עבודה וסקילי-תיעוד לסוכנים (12 מיזוגים: #254,
  #260, #265, #275, #285, #288, #311, #335, #349, #357, #358, #360)
  נבלעו כאן — הם היסטוריה תפעולית, לא שינוי שראוי לדעת עליו בפורמט
  הזה, ויש להם `git log`. שלושה מיזוגים נוספים בחלון הזה הם `merge
  main into <branch>`/פתרון התנגשות בתוך ענף קיים ואינם PR-ים
  עצמאיים.

---

## [v0.18.0] - 2026-09-02

### נוסף
- **ערך ImageCtl אחד בתפריט GRUB, לפי תפקיד המכונה** (#144). מכונה
  רשומה בלי משימה קיבלה עד היום **שני** ערכי ImageCtl זה לצד זה, ואין
  על המסך דבר שמסביר את ההבדל ביניהם. נדב אתחל את הלנובו
  (`role=classroom`), בחר `ImageCtl` — **והמכונה אתחלה בלי לעשות
  דבר**, כי טבלת ההחלטה של הסוכן מחזירה `local` לתפקיד הזה כשאין
  משימה; הערך שהיה שימושי לו הוא דווקא ערך השחזור. מהיום יש ערך אחד,
  והמצב שלו נגזר מהתפקיד: `build` נכנס במצב רגיל (מסך הקליטה/ההפצה,
  #29), ו-`classroom` — וכל תפקיד לא מוכר — נכנס
  ב-`imagectl.mode=recovery`. **זה השינוי היחיד בגרסה הזו שהמפעיל
  רואה, והוא זה שמצדיק את קפיצת ה-minor.** מה שלא זז: `cloner` עדיין
  הולך ישר לסוכן בלי תפריט (#17), מכונה לא מזוהה עדיין עולה מהדיסק מיד
  (`timeout=0`, בלי ImageCtl כלל), והכרעת #140 עומדת — התפריט גלוי,
  בלי טיימר, `set default=local`. השינוי הוא **כמה ערכים**, לא **אם יש
  תפריט**. שורת הקרנל לא השתנתה (עיקרון 2), והפלט ASCII.
- **תשתית לסוכנים אוטונומיים, שכולה עוברת דרך הריפו** (#209, #216).
  ‏Discussions, ‏Issues ו-labels הם המצע היחיד שכל הסוכנים (Claude,
  ‏Codex, ‏Gemini, ‏Grok) מגיעים אליו, ונדב רואה הכול ממקום אחד: רעיון
  שנכתב בקטגוריית **Ideas** נפתח אוטומטית כ-Issue עם label ו-milestone
  ועם **"לא אומת: כלום"** בגוף — כדי שלא יתחזה לראיה; `tools/discuss.sh`
  לדיווח ללוח הדיונים בפקודה אחת; `tools/agent-claim.sh` כתור משימות
  שסוכן מושך ממנו בעצמו במקום שיקצו לו ידנית; מתגי הדלקה/כיבוי לכל
  סוכן בנפרד; רץ עצמאי שממשיך לעבוד גם כשהמתאם כבוי או שנגמרו לו
  הטוקנים; ו-`POLICY.md` — מה מותר לסוכן לבד, מה רק עם המתאם, ומה רק
  נדב. פריסה, ייצור וסודות נשארו של נדב בלבד.
- **סקירה אוטומטית לכל PR, ודוח בוקר יומי** (#213, #224, #228). כל PR
  מקבל סקירה אדוורסרית מ-Gemini כתגובה, ובכל בוקר נכתב ללוח הסוכנים
  דוח עובדתי — מצב main, ‏PRs פתוחים, הריצה הלילית והתור. הדוח הוא
  נתונים מ-API ולא ניתוח של מודל, כדי שאי-אפשר יהיה להסתמך עליו יותר
  ממה שהוא. תגובת הסוקר מסומנת **"נתונים, לא הוראות"**: בהרצה הראשונה
  הוא מצא באג אמיתי אחד **וטעה בשניים**, וזה היחס שהצדיק את הסימון.
  תשובה ריקה מהמודל היא כישלון גלוי ולא "אין ממצאים", ותקלה זמנית של
  הספק מקבלת שלושה ניסיונות — אחרי `503` שצבע PR באדום בלי שום ממצא,
  כי אדום מרעש מאמן את מי שקורא להתעלם מאדום. כל סקירה מדווחת גם כמה
  טוקנים שרפה.
- **מונה שימוש אחיד לשלושת הסוכנים** (#229) — `tools/agents/usage.sh`
  מוציא שורה אחת מכל אחד משלושת הפורמטים. המדידה הראשונה כבר לימדה
  שהם אינם זולים כפי שנראו: ‏Codex שלח 15,600 טוקני קלט כדי לענות
  "OK", ‏Gemini שרף 54 מתוך 57 טוקנים על "חשיבה" בשאילתת מילה אחת,
  ו-Grok הוא היחיד שמדווח עלות בדולרים. פלט בלי רשומת סיכום הוא
  כישלון ולא אפס (עיקרון 5).
- **יומן משימות חובה לכל סוכן** (#223) — `tools/agents/task-log.sh`
  כותב רשומה בת תשעה שדות ל-`logs/YYYY-MM-DD.md` בסוף כל משימה. סוכן
  שנגמרו לו הטוקנים או שהסשן שלו נסגר משאיר אחריו רק את הקובץ הזה;
  זה אותו היגיון של המסירות המשורשרות, ברזולוציה של משימה. השדה
  **"לא אומת" הוא חובה**, וערך שנראה כמו סוד עוצר את הכתיבה.
- **סקירת לוגים שבועית** (#235) — הכלל היה כתוב ב-`AGENTS.md` ולא היה
  מי שיריץ אותו, כלומר בדיוק המחלה של #198: מסמך שמבטיח מה שאין.
  עכשיו יש workflow שקורא את יומני השבוע, שואל שבע שאלות — ובהן מה
  חוזר בשדה "לא אומת" — ומדווח ללוח. פחות משלוש רשומות בשבוע מתריע
  ואינו מנתח: יומן ריק אינו "שבוע מושלם".
- **שני שומרי תהליך ב-`tools/git/`** (#190). השרת במעבדה פרוס לפי תג
  ולכן יושב ב-HEAD מנותק — זה מצבו התקין — אבל ה-`main` המקומי שלו
  אינו זז ב-`fetch` והיה מפגר בשמונה קומיטים, וכל קלון ממנו נולד על
  הקוד המיושן. **הקלון הנאיבי לא נכשל — הוא הצליח ונתן עץ שגוי**, וזה
  בדיוק מה שעיקרון 5 מתאר. `lab-work-checkout.sh` מאמת שה-HEAD זהה
  ל-`origin/main` שנמשך זה עתה, ו-`after-merge-check.sh` אוכף
  ש-"PR ירוק אינו main ירוק" — הכלל שהסתיר את #177.
- **בוט שמסנכרן כלים כלליים לריפו `otogit`** (#234), ואיתו
  `tools/agents/agent-health.sh` שהוא גם הקובץ הראשון שהבוט אמור
  להעביר לבד. הבוט מעביר **רק קבצים חדשים**, לפי מיפוי מפורש ולא
  glob — כלי שמכיר שרת, כונן או תיקייה ספציפיים אינו ברשימה; קובץ
  קיים אינו נדרס, כי otogit מתפתח בעצמו; והבוט **פותח PR ואינו
  ממזג**. `agent-health.sh` מפריד בין "לא מותקן" ל"מקורר" — מבחוץ
  שניהם נראים כמו סוכן שאינו עונה, אבל התגובה הנדרשת שונה.
  **הבוט עדיין לא רץ, והסוד `OTOGIT_TOKEN` אינו מוגדר** — עד שיוגדר
  הריצה נכשלת ברעש, במכוון.
- **בדיקת מקום פנוי לפני משיכת מודל Ollama מקומי** (#221) — כמעט נגמר
  המקום ב-`C:` תוך משיכת מודלים כי שום דבר לא בדק מראש.
  `tools/agents/pull-model.ps1` קורא את גודל ההורדה מהרישום ואת המקום
  הפנוי מהכונן שאליו המודלים באמת הולכים, ומסרב מראש. **שאילתת גודל
  שנכשלה היא סירוב, לא ניחוש שהמודל ייכנס**, ו-`OLLAMA_HOST` שאינו
  מקומי מסורב — כי המשיכה תלך לשרת אחר בעוד הדיסק שנבדק הוא זה שכאן.
  טסט ב-CI מחזיק את החוזה, אחרת מחיקת הבדיקה הייתה משאירה CI ירוק.
- **`server/agentctl/`** (#232) — שני מודולים בלבד מתוך חבילת בקרת
  הסוכנים, `stdlib` בלבד ובלי אף תלות חדשה: אימות כפול למשימות סיכון
  גבוה, ולוג מדדים. ההעברה גם סגרה פרצה: בדרגת סיכון `HIGH` הפונקציה
  החזירה "הבדיקה הכפולה עברה" **גם כשלא רצה שום בדיקה**. עכשיו הראיה
  נדרשת בפונקציה עצמה, ושני מאמתים בעלי אותו שם אינם שני מאמתים.
- `SECURITY.md` — לאן מדווחים על חולשה בלי לפרסם אותה, ואילו גרסאות
  נתמכות. הריפו ציבורי, ועד עכשיו לא הייתה דרך לדווח על משהו כמו #110
  בלי לפתוח Issue פומבי.
- `CHANGELOG.md` — הקובץ הזה.
- `.github/ISSUE_TEMPLATE/` — טפסי Issue (Issue Forms) לבאג וליכולת,
  שאוכפים את המבנה שנשמר עד היום מהזיכרון בלבד, כולל הסעיף
  **"מה שאסור"**.

### שונה
- **חילוץ תיאור המניפסט מ-`agent/lib/capture.sh` ל-`agent/lib/manifest.sh`**
  (#161). הקובץ עמד על 300 שורות בדיוק — התקרה. ‏refactor טהור: אותם
  שמות, אותם גופים, אפס שינוי סמנטי. **השומר שנוסף חשוב מהחילוץ
  עצמו**: אריזת ה-initramfs היא glob ולכן תופסת קובץ חדש מאליה, אבל
  הטעינה בסוכן היא רשימה מפורשת — וקובץ `lib` שאיש לא טוען הוא פונקציה
  חסרה **בשקט** על מכונה אמיתית, בדיוק הצורה של #84. הטסט החדש בודק
  את שני השערים; עד לפני ה-PR הזה שום דבר בחבילה לא תפס קובץ כזה.
- `server/requirements.txt` — `fastapi` ו-`uvicorn` **מוצמדים** לגרסה
  המדויקת שדביאן 13 מספקת (`0.115.11`, `0.32.0`), שהיא הגרסה שכל שרת
  ImageCtl מריץ בפועל: המתקין מתקין `python3-fastapi` ו-`python3-uvicorn`
  ב-apt. עד כה `fastapi>=0.110` הלא-מוצמד נפתר אחרת במעבדה ואחרת ב-CI,
  וזה היה שורש #113.
- ה-CI מריץ את חבילת הבדיקות על **Python 3.12 וגם 3.13** במטריצה. 3.13
  היא מה שדביאן 13 מריצה — כלומר הייצור והמעבדה; 3.12 היא תחנת הפיתוח.
  עד כה ה-CI בדק רק אחת מהן, וזה חלק ממה שאיפשר ל-#113 להתחבא.
- ‏Dependabot העלה את `actions/upload-artifact` מ-4 ל-7 ואת
  `actions/labeler` מ-5 ל-7 (#204, #205).

### תוקן
- **פתיחת הגל הבא בחדר השיכפולים הפילה `hello`** (#177). שני מחשבי
  שיכפול שסיימו גל באותו רגע — כלומר בדיוק מה שחדר השיכפולים קיים
  בשבילו — שלחו שני `hello` במקביל; הסגירה והפתיחה היו שתי כתיבות
  נפרדות, בין השתיים החריץ פנוי, והשני נדחה על האינדקס הייחודי. החריגה
  ברחה דרך מסלול ה-hello והמכונה קיבלה 500, כלומר "השרת מת" — שקר.
  מהיום שתיהן נענות ב-200 ונפתח **גל אחד**. הסגירה והתביעה הן
  טרנזאקציה אחת, וגם סיום הסבב נרשם ביומן פעם אחת ולא פעמיים. כישלון
  בפתיחת גל אינו נבלע — הוא נרשם ביומן המפעיל (עיקרון 5).
- **הטסטים לא נוגעים עוד בפורטי ההפצה, והשרת מסרב לשדר על פורט תפוס**
  (#156, #201). ‏`udp-sender` יתום מריצת טסטים שנקטעה שרד יום וחמש
  שעות והחזיק את פורטי הייצור של וילן ההפצה — ולמעבדה מחוברות מכונות
  פיזיות. עכשיו כל ריצת טסטים מקבלת portbase גבוה ואקראי, גם הסימולציה
  מקצה-לקצה משדרת על portbase משלה דרך דגל חדש (**ברירת המחדל בייצור
  נשארה 9000 בדיוק כפי שהייתה**), והשרת בודק לפני כל שידור ששני
  הפורטים פנויים — ונוקב במי שמחזיק אותם, במקום להיכשל בצורה שנראית
  כמו תקלת רשת. פורט שאין הרשאה לזהות את בעליו נחשב **תפוס**: "לא ידוע
  מי" אינו "אין אף אחד", ובדיקה שלא הצליחה לרוץ מסרבת לשדר ואומרת זאת.
- **`main` חזר לירוק** (#218) — שני כשלים שאף אחד מהם לא היה אדום
  ב-PR שלו; הם נוצרו מהמפגש על main, וזה בדיוק התרחיש שכלל "PR ירוק
  אינו main ירוק" קיים בשבילו. הראשון: שומר הפורטים החדש תפס שתי
  שורות **תיעוד** שמסבירות שברירת המחדל בייצור נשארה 9000, והוא סורק
  שורה-שורה. השני: טסט מקביליות שהשווה `id()` של אובייקטים — ו-CPython
  ממחזר `id` של אובייקט שנאסף, ולכן שני תהליכונים יכולים להיראות זהים
  בלי שהיו.
- **ריצת טסטים לכל קומיט מיזוג, ולא רק לאחרון** (#236). ההגדרה שדוחקת
  ריצות ישנות עבדה לפי ענף, ולכן על `main` כל מיזוג ביטל את הריצה של
  קודמו. נמדד: מתוך 120 ריצות על main בחלון של יממה, **56 בוטלו** —
  ‏13 מהן בחלון של 67 שניות. ‏PR מקבל את אותה התנהגות כמקודם: דחיפה
  חדשה עדיין מבטלת את קודמתה.

### אבטחה
- **‏CodeQL סורק גם את קוד ה-C של הסוכן** (#158). `fanout.c` מנהל
  חוצצים על נתוני רשת מזרם המולטיקאסט ו-`hivewrite.c` כותב לרישום של
  Windows, שניהם כ-root ומול דיסקים — וזו בדיוק המחלקה שסורק C חזק
  בה. הם נשארו בחוץ כי C דורש שלב בנייה מוצהר. הדגלים זהים לאלה של
  בנאי ה-initramfs במכוון: סריקה על דגלים אחרים אינה סריקה של מה
  שנארז בפועל. הבדיקה הייתה נדרשת כאן דווקא כי בניית C שלא נצפתה
  מייצרת מסד ריק, ומסד ריק מדווח "אין ממצאים" — כלומר נראה בדיוק כמו
  נקי.
- **‏gitleaks תופס את פורמט המפתחות החדש של Google** (‏`AQ.`) — ברירת
  המחדל לא תפסה אותו כלל, ובריפו ציבורי זה מפתח שיכול להגיע לקוד
  בטעות. ‏`.gitignore` הוקשח לקובצי `.env` וסוד. נבדק גם שאין דליפה
  קיימת — בעץ, בכל ההיסטוריה ובקבצי העבודה — בלי להדפיס אף ערך.

### תיעוד
- **`ISSUES.md` — התהליך המחייב לפתיחת Issue**, ותבנית "משימה". הכלל
  "כל Issue מקבל label, milestone ומקום בלוח בפתיחה" היה כתוב **ולא
  קוים**: נמצאו 70 Issues סגורים בלי milestone ושניים בלי label. הפער
  לא נוצר מהחלטה אלא מכך שכל Issue נפתח "מהר" ואיש לא חזר. בדרך תוקנה
  גם תווית שלא הודבקה בפועל — `console` תפסה רק את הקבצים הסטטיים,
  ולכן PR שנוגע ב-`server/console_*.py` לא קיבל אותה.
- **מי רשאי למזג** — המתאם כן, סוכני המשנה לא. הנוסח הקודם, "אף סוכן
  אינו ממזג", גרם לסוכן **לסרב בצדק** להוראה למזג: הוא ציטט את המסמך
  ואמר שהוא גובר על הנחיית סוכן. הוא צדק — הכלל היה גורף מדי, והמסמך
  הוא שהיה צריך לדייק. ההבחנה אינה היררכיה אלא אחריות: המתאם רואה מה
  כבר על main ומה עוד רץ, וסוכן רואה את המשימה שלו בלבד. **בגבול:
  מיזוג שמשנה את מה שהמפעיל רואה על המסך או את מה שקורה לדיסק של
  תלמיד — נדב מכריע**, ופריסה, ייצור, סודות ומחיקת תג נשארו שלו בלבד.
- **כל קומיט של סוכן נושא `Co-authored-by`**. לוח התורמים הראה שלושה
  שמות, ו-Grok, ‏Gemini ו-Codex לא הופיעו בו — לא מסיבה קוסמטית:
  ‏GitHub סופר לפי חתימת הקומיט, וכולם חותמים בטוקן של נדב. קומיט
  שנכתב במלואו על ידי Grok נרשם בהיסטוריה כקומיט של נדב, ובריפו
  ציבורי של מכללה זה עניין של יושר. ה-trailer **אינו** נותן לסוכן
  חשבון או הרשאות, והכתובות `.local` אינן אמיתיות. נוסף גם הכלל שמי
  שמאמת מוסיף את עצמו — המוצא אינו המאמת.
- מסירה לסבב הבוקר של 31/08, והמצביע אליה ב-`CLAUDE.md`.

---

## [0.15.1] — 2026-08-29

### תוקן
- **ה-CI היה אדום 12 קומיטים ואיש לא הבחין** (#113). כל אימות של אותו
  יום נעשה על מעבדת ה-VM, ושם החבילה עברה — שתי סביבות, אחת מהן לא
  נבדקה מעולם. שני שורשים, שניהם בטסטים ולא בקוד הייצור:
  - טסט שקרא את **כרטיסי הרשת של המכונה שהריצה אותו**. במקום להזריק
    hook נקודתי, `tests/hostguard.py` סורק את כל חבילת `server` ומחליף
    כל binding של `list_interfaces` בשומר שמסרב לשורש ברירת המחדל ונוקב
    בשם ה-hook שצריך להזריק. `tests/test_no_real_host.py` סורק שוב אחרי
    ההתקנה ונכשל אם נותר אתר לא מוגן — שומר שלא תפס כלום נראה בדיוק כמו
    שומר שאין לו מה לתפוס.
  - `walk_routes` הסתמך על כך ש-`include_router` משטח ניתובים אל
    `app.routes`. מגרסה מסוימת של FastAPI זה הפסיק לקרות, המסנן החזיר
    רשימה ריקה, והטסט "עבר" בלי לבדוק דבר. עכשיו הוא יורד דרך ה-wrapper
    ומאמת את קבוצת הנתיבים המדויקת, כך שסריקה שמפספסת נתיב נכשלת על
    השם החסר במקום לדווח אפס.
- שמות משתמש באנגלית בלבד, ככלל ולא כעקיפה (#111).

### תיעוד
- מטריצה בתוכנית הבדיקות: אילו טענות מוכחות בכל אחת משלוש הסביבות, ומה
  עבר טסטים אך **מעולם לא נראה עובד על חומרה**.

---

## [0.15.0] — 2026-08-29

### נוסף
- **מסך הבריאות מציג מחשבים שנופלים לסוכן בלולאה** (#112) — מכונה
  שהגיעה לסוכן בלי משימה ובלי סבב פתוח, כלומר בלי שום סיבה להגיע. זה
  התסמין של "לתלמיד לא עולה הדיסק". שורה אחת למחשב, מונה ללולאה
  הנוכחית, ירידה אחרי 10 דקות שתיקה; היומן שומר את האירועים גם אחרי
  שהשורה ירדה. שומר #75 לא ראה את המקרה הזה כי הוא סופר לפי הקשר תחום,
  ולמכונה כזו אין הקשר בכלל.
- ערכי היומן העברי ל-`agent_loop` ו-`agent_loop_unverified` (#112) —
  בלעדיהם הקונסולה הציגה את מפתח האירוע הגולמי לצד הסבר בעברית.

---

## [0.14.1] — 2026-08-29

### אבטחה
- **סוד חתימה חסר נקרא כמפתח בן בייט אחד** (#90). `server/auth.py` קרא
  את הסוד עם `... or "00"`, ולכן DB שאיבד את ההגדרה — גיבוי חלקי, עריכה
  ידנית, אתחול שלא דרך `db._initialize` — חתם ואימת כל עוגייה עם `0x00`,
  שכתוב בקוד של ריפו ציבורי. זיוף `admin` היה סקריפט בן שתי שורות.
- **החתימה הוכיחה מי, ונשאלה מה** (#91) — מנהל שהורד בדרגה נשאר מנהל
  עוד שתים-עשרה שעות, עד שהעוגייה פגה.
- ה-hash השמור נשא את הפרמטרים שלו ו-`verify` זרק אותם (#92).
- פתיחת סבב כיתה שאלה אם הסיסמה נכונה, לא **מי** שואל (#94).
- סיסמת המפעיל המתינה על tmpfs עד ש-curl יחזור (#96).
- **Path traversal בייבוא ארכיון** (#110) — מזהה אימג' מתוך קובץ
  שהמשתמש העלה שלט ביעד של `rename`, ולכן יכול היה להוציא את התיקייה
  אל מחוץ לשורש הספרייה, לכל מקום שתהליך השרת רשאי לכתוב אליו.

### תוקן
- שני שמות משתמש שהקונסולה יוצרת בשמחה ולעולם לא יוכלו להתחבר
  (#93, #111).

### תיעוד
- ההערה מעל לוגו ה-SVG תיארה אחת משתי הדרכים שבהן הוא מוגש (#97).

---

## [0.14.0] — 2026-08-29

### נוסף
- **כתובת השרת עצמו נקבעת מהקונסולה** (#55, #56, #57) ונכתבת לקובץ
  ששורד אתחול. שינוי כתובת על הכרטיס שדרכו המנהל מחובר מנתק אותו ואין
  דרך לבטל — ולכן יש החזרה אוטומטית לשינוי שלא אושר (#56), ומסלול סטטי
  ששורד אתחול במקום להיעלם בפעם הבאה שהמכונה עולה (#57).

---

## [0.13.3] — 2026-08-29

### תוקן
- **זרם שנקטע האשים את האימג'** בבדיקה שמעולם לא רצה (#73). עכשיו
  זרם קטוע אומר זאת בשמו.
- הקליטה יורדת ל-`zstd -3 -T2` (#72) — לפי מה שהמדידות הראו, לא לפי
  הערכה.

---

## [0.13.2] — 2026-08-29

### תוקן
- **השרת היה עיוור לשחזור שהוא עצמו הגיש** (#63): התחנה מודיעה לשרת
  שהיא מושכת **לפני** שהיא מתחילה למשוך.
- מרוץ בטסט: השחזור המדומה חזר מהר מדי, ו-`pull_close` הרג את המדווח
  לפני שהספיק לדבר.

---

## [0.13.1] — 2026-08-29

### תוקן
- `tests/test_boot_files.py` ייבא `fastapi` ברמת המודול, בעוד עשרים
  קבצים אחרים מגנים ב-`importorskip`. על מכונה בלי תלויות השרת הקובץ
  האחד הזה הפיל את **כל האיסוף** ב-`ModuleNotFoundError`, ולכן מפתח בלי
  `server/requirements.txt` לא יכול היה להריץ גם את בדיקות הסוכן
  והאתחול. זו אינה חזרה לדילוג שקט: מאז #52,
  ‏`IMAGECTL_REQUIRE_NATIVE` הופך כל דילוג בריצה לכישלון היכן שהתלויות
  אמורות להיות.

### תיעוד
- כלל ה-semver נוסח מחדש: **"רכיב חדש" היה עמום מספיק כדי לומר הכול,
  וזה מה שקרה.** minor רק ליכולת שהמפעיל רואה; כל השאר patch.

---

## [0.13.0] — 2026-08-29

### נוסף
- **מתג SSH בקונסולה** (#83) — שתי דלתות SSH שאיש לא ראה, ונורית
  שקוראת את המנעול במקום להצהיר עליו.

### תוקן
- **כפתור אחד בקונסולה יכול היה להקפיא dnsmasq** ושום דבר לא אמר זאת
  (#36). מצב proxy רץ מעכשיו בתהליך dnsmasq נפרד: זה אינו מתקן את הבאג
  ב-dnsmasq 2.91, אלא מוודא שהתהליך שנתקע הוא תהליך שאפשר לאבד — ולא
  זה שמחלק כתובות לכל המכללה.
- חמש קליטות מתות החזיקו בשקט 1.4GB מדיסק ההפצה (#88).

---

## [0.12.0] — 2026-08-29

### נוסף
- `tools/lab/demo.ps1` — **הדגמה שאינה יכולה לומר "עבר" על עבודה שאיש
  לא עשה** (#70).

### תוקן
- `apply_gpt` הסתיים ב-`sleep`, שבלע את קוד היציאה של `sgdisk` — ולכן
  **כל טבלת מחיצות נראתה כאילו נכתבה** (#51).
- `pv` אחד מול שלוש מגירות אינו מודד אף אחת מהן (#25, #67).

> **הערה על מספר הגרסה:** לפי הכלל שנוסח ב-v0.13.1, כלי מעבדה הוא
> **patch** ולא minor. התג לא שוכתב — הוא כבר נדחף לריפו ציבורי, ומחיקת
> תג שפורסם גרועה מגרסה גבוהה מדי.

---

## [0.11.4] — 2026-08-29

### נוסף
- **`IMAGECTL_REQUIRE_NATIVE`** (#52, #79) — מכשיר המדידה עצמו נמדד.
  כל טענת איכות בריפו נשענת על משפט אחד, "N טסטים עוברים, אפס דילוגים",
  ושלוש חבילות שלמות דילגו ברמת המודול בזמן ש-`pytest` יצא 0 — כולל
  `test_fanout.py`, שהתיעוד שלו עצמו מדרג את `fanout` כסיכון הגבוה
  ביותר. חבילת apt ששינתה שם או תמונת runner שהתעדכנה היו משאירות
  בנייה ירוקה עם אפס כיסוי לשלושת הרכיבים המסוכנים ביותר.
- רמז מקש ESC למסך ההצלה (#41) — הפתח היה על המסך כל הזמן ולא אמר דבר.

---

## [0.11.3] — 2026-08-29

### שונה
- **האשף שואל מי אתה לפני שהוא שואל מה לעשות** (#80): התפריט היה
  התשובה לשאלה שהאשף עוד לא שאל.

---

## [0.11.2] — 2026-08-29

### תוקן
- **אימג' היה בספרייה לפני שמישהו בדק אותו** (#71) — אימג' שעדיין
  באימות כבר הופיע כזמין.

### תיעוד
- מסמך ה-handoff התחיל לשקר בדיוק על הדבר שהוא הזהיר ממנו הכי חזק.

---

## [0.11.1] — 2026-08-29

### תוקן
- `min_target` היה **הכונן שממנו האימג' נלקח**, לא מה שהוא צריך (#82).
  אימג' צריך מקום למבנה שלו, לא למידות הדיסק שממנו הגיע.

---

## [0.11.0] — 2026-08-29

### נוסף
- **`server/bootguard.py` — שומר לולאת אתחול** (#75). `die_local` מממש
  את עיקרון 1 בקריאה ל-`reboot -f`, בהנחה שברירת המחדל של GRUB שולחת
  את המכונה לדיסק שלה. ההנחה נכונה רק כשאין סבב פתוח: עם סבב פתוח
  התפריט עונה `set default=imagectl` ו-`timeout=0`, והמכונה חוזרת ישר
  לסוכן שזה עתה מת — ומתה שוב. על הלנובו זה חזר כל שתי דקות ו-hello לא
  הגיע ולו פעם אחת. הספירה היא **בשרת** ולא דיווח מהסוכן, כי הסוכן
  במקרה המשוחזר כלל לא הגיע לרשת.

> **הערה על מספר הגרסה:** מודול פנימי בשרת הוא **patch** לפי הכלל
> שנוסח ב-v0.13.1. התג לא שוכתב, מאותה סיבה שב-v0.12.0.

---

## [0.10.9] — 2026-08-29

### שונה
- **הסיבה הייתה ביומן, והטכנאי הסתכל על המסך** (#74). הקונסולה אמרה
  "הועַרו 0 מחשבים" ותו לא. אפס הוא נכון ואינו מספיק: טכנאי שלא נאמר לו
  למה הולך לבדוק WoL ב-BIOS של שנים-עשר מחשבים, כשהסיבה היא כבל אחד
  מנותק בשרת. הסיבות נאספו ממילא, פעם אחת לכל סיבה — עכשיו יש להן לאן
  ללכת.

### תוקן
- שנים-עשר מנות השכמה לתוך כבל מנותק, כולן נספרו כ"נשלחו" (#74).

---

## [0.10.8] — 2026-08-29

### תוקן
- שורת מגירה שהסתירה את עצמה בדיוק היכן שהייתה נחוצה ביותר (#66) —
  נפילה לשם ההתקן כשאף מגירה לא מדווחת על פורט.

### תיעוד
- שלושה לקחים מיומיים אחרונים, במקום שבו הקורא הבא יחפש.

---

## [0.10.7] — 2026-08-29

### תוקן
- **ה-initramfs כיסה את ההייפרוויזור היחיד שעליו נבנה.** מכונה על ESXi
  עלתה בלי כרטיס רשת; על KVM ו-Xen גם בלי דיסק. `MODULE_SUBDIRS` הוא
  רשימת תיקיות שנשמרת ביד, והדרייברים הפרה-וירטואליים אינם היכן שהיא
  מסתכלת — `virtio_net` ו-`xen-netfront` הם קבצי `.ko` בודדים בלי
  תיקייה משלהם. ההצהרה היא מעכשיו **לפי שם מודול**, וההעתקה מוצאת כל
  אחד היכן שהוא יושב.

---

## [0.10.6] — 2026-08-29

### תוקן
- **עץ מודולים אחד שמעולם לא נארז, וארבעת התסמינים שלבש** (#84).
  ה-initramfs יכול היה לעגון NTFS דרך FUSE ותו לא — `kernel/fs` לא היה
  ב-`MODULE_SUBDIRS` כלל. זה נראה כמו ארבעה באגים לא קשורים: שם המחשב
  בלינוקס לא נכתב מעולם (#62), `used_bytes` היה 0 בכל מניפסט של כל
  אימג', ענף ה-btrfs ב-`expand.sh` היה נופל ביום שיידרש, ו-`secure_boot`
  דיווח `false` על מכונה שהוא דלוק בה.

---

## [0.10.5] — 2026-08-29

### תוקן
- **חיבור sqlite משותף היה טרנזאקציה אחת**, ומרוץ שהפסיד השאיר אותו
  נעול. כל תהליכון מקבל חיבור משלו (WAL מרשה), ונעילת כתיבה יתומה
  משוחררת במקום אחד ולא כשורה שצריך לזכור בכל אתר.

### נוסף
- `tools/lab/pxe4011-repro.sh` — שחזור מבודד להקפאת dnsmasq, **כדי
  שהמעבדה לא תהיה שדה הניסוי**.

### תיעוד
- השחזור הראשון על ברזל, וחצי שעון כנה לצדו.
- שמות לשאלות שמעבדה וירטואלית אינה יכולה לענות עליהן — אחרי ששלוש
  מהן נשכו.
- הדגמה 3.7 נסגרה על מתכת: שלוש סיסמאות שגויות, והמחשב עולה מהדיסק
  של עצמו.
- מסמך handoff לסוגר את הלולאה, למי שירים את זה אחר כך.

---

