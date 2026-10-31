# Third-party notices

ImageSrv / ImageCtl is released under the MIT License (see `LICENSE`).
The ISO, the boot image (initramfs) and the server install additionally
contain, or install from the Debian archive, software written by others.
That software stays under its own licenses, listed below. Nothing here
changes those licenses.

> DRAFT for review by the project owner. Versions are not frozen here: the
> exact package versions of every release are in `imagectl-iso.json`
> (`pool_debs`: name + version + sha256), shipped next to each ISO and
> inside it. License names below are the Debian packaging metadata
> (`/usr/share/doc/<package>/copyright`) as understood at drafting time and
> MUST be re-checked against the release pool before publishing. Rows marked
> "verified" were read from Debian's copyright file on 2026-10-18.

## 1. Where each group ends up

| Group | Goes into |
|---|---|
| A. Python libraries | server (installed from Debian packages, not bundled in our source) |
| B. Server system packages | the ISO pool and the installed server |
| C. Boot-image (initramfs) tools | the agent initramfs served to machines over PXE, and the installer live image on the ISO |
| D. Kiosk / GUI libraries and font | the initramfs (`--with-gui`) |
| E. Optional technician toolbox | the initramfs, only when selected in `--tools-selection` |
| F. Kernel and firmware | the ISO and the initramfs |

## 2. Inventory

"Debian 13" means the Debian 13 (trixie) archive. "+" means "or any later version".

### A. Python libraries (server)

| Component | Version | License | Notes |
|---|---|---|---|
| FastAPI (`python3-fastapi`) | 0.115.11 | MIT | pinned in `server/requirements.txt` |
| Starlette, Pydantic (FastAPI dependencies) | per Debian 13 | BSD-3-Clause, MIT | transitive |
| Uvicorn (`python3-uvicorn`) | 0.32.0 | BSD-3-Clause | |
| websockets | 15.0.1 | BSD-3-Clause | |
| cryptography | 43.0.0 | Apache-2.0 OR BSD-3-Clause | |
| pyOpenSSL | 25.0.0 | Apache-2.0 | |
| python-qrcode | 8.2 | BSD-3-Clause | |
| Python 3 itself | Debian 13 | PSF-2.0 | |
| SQLite (`sqlite3`) | Debian 13 | public domain | |

The console (`server/static`) and the installer wizard (`server/wizard/static`)
are our own HTML/CSS/JS. No third-party JavaScript library, no web font and no
CDN resource is bundled or loaded. The font names in the CSS (Segoe UI,
Assistant, Cascadia Mono, ...) are system-font fallbacks; no font file is shipped.

### B. Server system packages (ISO pool / installed server)

| Component | License |
|---|---|
| Linux kernel `linux-image-amd64` | GPL-2.0 (with the Linux-syscall-note); some modules dual-licensed |
| GRUB (`grub-efi-amd64-signed`, `grub-pc-bin`) | GPL-3.0+ |
| shim (`shim-signed`) | BSD-2-Clause; binary signed by Microsoft/Debian keys |
| Syslinux / ISOLINUX (from the Debian netinst the ISO is built on) | GPL-2.0+ |
| dnsmasq | GPL-2.0 or GPL-3.0 |
| dhcpcd (`dhcpcd-base`) | BSD-2-Clause |
| nftables | GPL-2.0 |
| open-iscsi | GPL-2.0+ |
| nfs-common | GPL-2.0+ |
| cifs-utils | GPL-3.0+ |
| git | GPL-2.0 |
| rsync | GPL-3.0+ |
| dbus | AFL-2.1 OR GPL-2.0+ |
| systemd-timesyncd | LGPL-2.1+ |
| ca-certificates | MPL-2.0 (CA bundle), GPL-2.0+ (scripts) |
| Everything else pulled in as a dependency by `make-pool.sh` | see `pool_debs` in `imagectl-iso.json` and each package's `copyright` file |

### C. Boot-image (initramfs) tools

| Component (Debian package) | License |
|---|---|
| BusyBox (`busybox-static`) | GPL-2.0 |
| partclone | GPL-2.0+ |
| udpcast (`udp-sender`, `udp-receiver`) | GPL-2.0+ |
| ntfs-3g (`ntfs-3g`, `libntfs-3g`) | GPL-2.0+ (tools), LGPL-2.0+ (library) |
| hivex (`libhivex`) | LGPL-2.1+ |
| zstd | BSD-3-Clause OR GPL-2.0 |
| curl / libcurl | curl license (MIT-style) |
| Dropbear (`dropbear-bin`) | MIT-style (with BSD and public-domain parts) |
| gdisk (`sgdisk`) | GPL-2.0+ |
| jq | MIT |
| pv | Artistic-2.0 |
| hdparm | BSD-style (own license) |
| coreutils | GPL-3.0+ |
| util-linux | GPL-2.0+, with BSD-licensed parts |
| OpenSSL | Apache-2.0 |
| e2fsprogs | GPL-2.0 (libraries LGPL-2.0 / BSD) |
| btrfs-progs | GPL-2.0 |
| xfsprogs | GPL-2.0+ / LGPL-2.1 |
| cpio, gzip, tar | GPL-3.0+ |
| ethtool | GPL-2.0 |
| smartmontools | GPL-2.0+ |
| dmidecode | GPL-2.0+ |
| efibootmgr (+ efivar) | GPL-2.0+ (efivar LGPL-2.1) |
| GNU C Library (every dynamically linked binary) | LGPL-2.1+ |
| Live installer only: debootstrap, dpkg, perl-base, dash, dosfstools, eject | per Debian `copyright` files (mostly GPL-2.0+ / BSD / Artistic) |

Our own programs in the initramfs (`fanout`, `hivewrite`, `lldpsniff`,
`imagectl-monitor`, `native-gui`, the agent shell scripts) are MIT. Note that
`imagectl-monitor` links LibVNCServer (section D), see the note there.

### D. Kiosk / GUI libraries and font (`--with-gui`)

| Component | Version | License |
|---|---|---|
| **LibVNCServer** (`libvncserver1`) | 0.9.15+dfsg-1+deb13u2 (verified) | **GPL-2.0+**, no linking exception (verified; some files permissive) |
| - its runtime dependencies: libgcrypt20, GnuTLS | Debian 13 | LGPL-2.1+ |
| - libjpeg-turbo | Debian 13 | BSD-3-Clause / IJG / Zlib |
| - LZO (`liblzo2-2`) | Debian 13 | GPL-2.0+ |
| - zlib | Debian 13 | Zlib |
| Pango | 1.56.3-1 | LGPL-2.0+ |
| Cairo | 1.18.4-1 | LGPL-2.1 OR MPL-1.1 |
| Pixman | 0.44.0-3 | MIT |
| HarfBuzz | 10.2.0-1 | MIT |
| FriBidi | 1.0.16-1 | LGPL-2.1+ |
| FreeType | 2.13.3 | FTL OR GPL-2.0+ |
| Fontconfig | 2.15.0-2 | MIT-style |
| GLib | 2.84.4-3 | LGPL-2.1+ |
| libdrm | 2.4.124-2 | MIT |
| **IBM Plex** (`fonts-ibm-plex`, only a few faces are used) | 6.1.1-1 | **SIL OFL-1.1** (Reserved Font Name "Plex") (verified) |

Linking note: `imagectl-monitor` is our MIT code linked with LibVNCServer
(GPL-2.0+). The resulting binary is distributed under the GPL-2.0+; its
complete source is `agent/monitor.c` and `agent/hmac_sha256.c` in this
repository (MIT, which is GPL-compatible).

Font note: Plex is shipped unmodified, as files copied from the Debian
package, together with its license (`/usr/share/doc/fonts-ibm-plex/copyright`
and the OFL text). It is not sold separately.

### E. Optional technician toolbox (only if selected)

nvme-cli (GPL-2.0+), nwipe (GPL-2.0), tpm2-tools (BSD-3-Clause),
chntpw (GPL-2.0), testdisk / photorec (GPL-2.0+), scalpel (Apache-2.0),
foremost (public domain / GPL, see package), extundelete (GPL-2.0),
magicrescue (GPL-2.0), recoverjpeg (GPL-2.0+), squashfs-tools (GPL-2.0+),
libarchive-tools / bsdtar (BSD-2-Clause and others), gddrescue (GPL-2.0+),
safecopy (GPL-2.0+), dosfstools (GPL-3.0+).

### F. Kernel and firmware

* Linux kernel and the modules in the initramfs: GPL-2.0 with the
  Linux-syscall-note.
* `linux-firmware` pieces shipped as binary blobs (from Debian
  `firmware-realtek` and `firmware-intel-graphics`, non-free-firmware):
  * `rtl_nic/*` (Realtek): Debian license id `binary-redist-Realtek-permissive`
    (the `rtl8153*` and `rtl8156*` files: `binary-redist-Realtek-restrictive`).
    Text not yet read, see open item in the cover note.
  * `i915/*` (Intel): Debian license id `binary-redist-Intel` (verified):
    redistribution only in binary form, unmodified; the copyright notice,
    conditions and disclaimer must be reproduced in the accompanying
    documentation; no reverse engineering; Intel's name may not be used to
    endorse derived products.
  The full texts are in the packages' `copyright` files and must travel with
  the firmware (ISO and initramfs).
* Wired network-card firmware (#1820), on the ISO pool and installed on the
  server, **not** in the initramfs. Each whole Debian 13 package is shipped;
  the license ids below are those Debian's `copyright` file assigns to the
  files each package ships (read from the 20250410-2 packages on 2026-10-31;
  the texts themselves not yet reviewed, same open item as `rtl_nic`):
  * `firmware-bnx2`, `firmware-bnx2x` (Broadcom/QLogic NetXtreme II):
    `binary-redist-firmware`.
  * `firmware-qlogic` (QLogic FastLinQ, Brocade): `binary-redist-QLogic-1`
    to `-3`, `binary-redist-QLogic-BR-series`, `BSD-2-clause-OpenIB.org`.
  * `firmware-netxen` (QLogic 3xxx): `binary-redist-QLogic-4`.
  * `firmware-myricom` (Myri-10G): `BSD-2-clause-Myricom`.
  * `firmware-netronome` (Agilio): `binary-redist-Netronome`.
  * `firmware-cavium` (LiquidIO): `binary-redist-Cavium`.
  * `firmware-intel-misc`: `ice` DDP `binary-redist-Intel`, `e100`
    `BSD-3-clause-Intel`. The package also carries other Intel firmware under
    `binary-redist-Intel-1`, `-Intel-Ice-enhanced` and
    `-Intel-narrower-patent`.
  * `firmware-misc-nonfree`: `tg3` and `tehuti` `binary-redist-firmware`,
    Chelsio `cxgb4` `binary-redist-Chelsio`, `cxgb3` `BSD-2-clause` and
    `binary-redist-NetLogic`, `typhoon` `BSD-3-clause-3Com`. This package
    also carries firmware for many non-network devices under dozens of
    other ids; the per-file list is in its `copyright` file.
  * `firmware-linux-free` (Debian main; `pcnet_cs`): GPL-2, GPL-2+, GPL-3.

## 3. Source code for GPL / LGPL components

Several components above are licensed under the GNU General Public License or
Lesser General Public License, which give you the right to receive the
corresponding source code of the exact binaries we distribute.

**Written offer.** For three years from the date of the release you received,
and for as long as we offer support or updates for it, we will give any
third party, on request, the complete corresponding source code of every
GPL/LGPL component contained in that release, for no more than the cost of
physically providing it. Contact: <CONTACT-ADDRESS-TO-BE-FILLED>.

**Where it is.** Every component in sections B-F is an unmodified Debian 13
package. Its source package of the same version is published by Debian:
`https://deb.debian.org/debian/pool/` and permanently at
`https://snapshot.debian.org/`. The exact versions of a release are listed in
`imagectl-iso.json` (`pool_debs`). `apt-get source <package>=<version>` fetches
a given source package. We keep a copy of those source packages for each
release. (TO BE CONFIRMED: see decision 2.)

**Our own code** (MIT) is in this repository: https://github.com/NadavOked/ImageSrv

## 4. Full license texts

* GPL-2.0, GPL-3.0, LGPL-2.1, LGPL-3.0, Apache-2.0, BSD-3-Clause, MPL-2.0,
  Artistic-2.0 (the standard texts): `/usr/share/common-licenses/` on every
  Debian system.
* Everything else: `/usr/share/doc/<package>/copyright`, which Debian
  installs for every package. These files are inside each `.deb` of the ISO pool.
* IBM Plex, SIL Open Font License 1.1: https://openfontlicense.org/ and
  `/usr/share/doc/fonts-ibm-plex/`.

## 5. Not distributed

The directory `vendor/diskless-pxe` (an iPXE/Alpine/kiosk scaffold) is stored
in the repository only. It is not built into any release; the packages it
names (Alpine, cage, cog, Mesa, iPXE) are not shipped.

"Debian" is a registered trademark of Software in the Public Interest, Inc.
ImageCtl is not affiliated with or endorsed by the Debian project; the ISO is
built from the official Debian netinst image and Debian packages.
