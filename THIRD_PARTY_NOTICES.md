# Third-party notices

ImageSrv is released under the MIT License (see `LICENSE`).
The ISO, the boot image (initramfs) and the server install additionally
contain, or install from the Debian archive, software written by others.
That software stays under its own licenses, listed below. Nothing here
changes those licenses.

> Checked on 2026-11-13 against the package pool of ImageSrv v0.94.0
> (`imagesrv-iso.json`, `pool_debs`: 1,020 Debian 13 packages, pool date
> 2026-11-13). Each license below was read from that package's
> `/usr/share/doc/<package>/copyright`, taken from the `.deb` in the pool.
> The exact list (name, version, sha256) is `imagesrv-iso.json`, at the root
> of every ISO.

## 1. Where each group ends up

| Group | Goes into |
|---|---|
| A. Python libraries | server (installed from Debian packages, not bundled in our source) |
| B. Server system packages | the ISO pool and the installed server |
| C. Boot-image (initramfs) tools | the agent initramfs served to machines over PXE, and the installer live image on the ISO |
| D. Kiosk / GUI libraries and font | the initramfs (`--with-gui`) |
| E. Optional technician toolbox | the initramfs, only when selected in `--tools-selection` |
| F. Kernel and firmware | the ISO and the initramfs |
| G. Build tools | the ISO pool and the installed server (they compile our own boot-image programs; none is copied into the boot image) |

## 2. Inventory

"Debian 13" means the Debian 13 (trixie) archive. "+" means "or any later version".

### A. Python libraries (server)

| Component | Version | License | Notes |
|---|---|---|---|
| FastAPI (`python3-fastapi`) | 0.115.11-4 | MIT | pinned in `server/requirements.txt` |
| Starlette (`python3-starlette`) | 0.46.1-3+deb13u3 | BSD-3-Clause | FastAPI dependency |
| Pydantic (`python3-pydantic`, `python3-pydantic-core`) | 2.10.6-2, 2.27.2-3+b1 | MIT | FastAPI dependency |
| Uvicorn (`python3-uvicorn`) | 0.32.0-3 | BSD-3-Clause | |
| websockets (`python3-websockets`) | 15.0.1-1 | BSD-3-Clause | |
| cryptography (`python3-cryptography`) | 43.0.0-3+deb13u1 | Apache-2.0 | |
| pyOpenSSL (`python3-openssl`) | 25.0.0-1+deb13u1 | Apache-2.0 | |
| python-qrcode (`python3-qrcode`) | 8.2-1 | BSD-3-Clause | |
| libguestfs Python binding (`python3-guestfs`) | 1:1.54.1-2+deb13u1 | LGPL-2.0+ (library), GPL-2.0+ (programs) | VM update path |
| Python 3 itself (`python3.13`) | 3.13.5-2+deb13u5 | PSF-2.0 | |
| SQLite (`sqlite3`, `libsqlite3-0`) | 3.46.1-7+deb13u2 | public domain | |

The console (`server/static`) and the installer wizard (`server/wizard/static`)
are our own HTML/CSS/JS. No third-party JavaScript library, no web font and no
CDN resource is bundled or loaded. The font names in the CSS (Segoe UI,
Assistant, Cascadia Mono, ...) are system-font fallbacks; no font file is shipped.

### B. Server system packages (ISO pool / installed server)

| Component | License |
|---|---|
| Linux kernel (`linux-image-amd64` -> `linux-image-6.12.116+deb13-amd64`) | GPL-2.0; some files GPL-2.0+ OR X11, LGPL-2.1, or GPL-2.0 OR BSD-2-Clause |
| GRUB (`grub-efi-amd64-signed`, `grub-pc-bin`, `grub-common`) | GPL-3.0+ (read from `grub-common`, the same source; the `-signed` package's own file covers only its packaging) |
| shim (`shim-signed`, `shim-unsigned`) | BSD-2-Clause; binary signed by Microsoft/Debian keys |
| Syslinux (`syslinux`, `syslinux-common`) | GPL-2.0+, many files Expat |
| ISOLINUX boot files on the ISO, taken from the Debian netinst image the ISO is built on | not in the pool; not checked here (the pool's `syslinux` above is GPL-2.0+) |
| dnsmasq | GPL-2.0 OR GPL-3.0 |
| dhcpcd (`dhcpcd-base`) | BSD-2-Clause |
| nftables | GPL-2.0 |
| open-iscsi | GPL-2.0+ |
| nfs-common | GPL-2.0+; the RPC code BSD-3-Clause |
| cifs-utils | GPL-3.0+ |
| Samba client (`smbclient`) | GPL-3.0+; parts LGPL-3.0+ and BSD-3-Clause |
| git | GPL-2.0 |
| rsync | GPL-3.0+ |
| dbus | AFL-2.1 OR GPL-2.0+ |
| chrony | GPL-2.0 |
| systemd-timesyncd | LGPL-2.1+ |
| ca-certificates | MPL-2.0 (CA bundle), GPL-2.0+ (scripts) |
| ifenslave | GPL-3.0+ |
| Network UPS Tools (`nut-client`, `nut-server`) | GPL-2.0+; its Python scripts GPL-3.0+ |
| mdadm | GPL-2.0+ |
| `util-linux-extra` | GPL-2.0+, with LGPL and BSD-licensed parts |
| tzdata | public domain |
| QEMU (`qemu-system-x86`, `qemu-system-common`) | GPL-2.0; parts LGPL-2.0+ and BSD |
| OVMF (`ovmf`) | BSD-2-Clause-Patent |
| Everything else pulled in as a dependency by `make-pool.sh` | see `pool_debs` in `imagesrv-iso.json` and each package's `copyright` file; not listed or checked row by row here |

### C. Boot-image (initramfs) tools

| Component (Debian package) | License |
|---|---|
| BusyBox (`busybox-static`) | GPL-2.0 |
| partclone | GPL-2.0+ |
| udpcast (`udp-sender`, `udp-receiver`) | GPL-2.0; `fec.c` BSD-2-Clause |
| ntfs-3g (`ntfs-3g`, `libntfs-3g89t64`) | GPL-2.0+; the bundled fuse-lite LGPL-2.0+ |
| hivex (`libhivex0`, `libhivex-dev`) | LGPL-2.1 (library), GPL-2.0+ (tools such as `hivexsh`) |
| zstd | BSD-3-Clause OR GPL-2.0 |
| curl / libcurl | curl license (MIT-style) |
| gdisk (`sgdisk`) | LGPL-2.0+ (as stated in Debian's copyright file) |
| jq | MIT |
| pv | GPL-3.0 |
| hdparm | own permissive license (free use, modification and redistribution; keep the notice) |
| coreutils | GPL-3.0+ |
| util-linux | GPL-2.0+, with LGPL and BSD-licensed parts |
| OpenSSL | Apache-2.0 |
| e2fsprogs | GPL-2.0 (libraries LGPL-2.0 / BSD) |
| btrfs-progs | GPL-2.0 |
| xfsprogs | GPL-2.0 (library parts LGPL-2.1) |
| cpio, gzip, tar | GPL-3.0+ |
| ethtool | GPL-2.0 |
| smartmontools | GPL-2.0+ |
| dmidecode | GPL-2.0+ |
| pciutils | GPL-2.0+ |
| pci.ids | GPL-2.0+ OR BSD-3-Clause |
| dmsetup (`dmsetup`, `libdevmapper1.02.1`) | GPL-2.0 (library LGPL-2.1) |
| efibootmgr (+ efivar) | GPL-2.0+ (efivar LGPL-2.0+) |
| GNU C Library (every dynamically linked binary; statically linked into `fanout` and `imagesrv-uevent`) | LGPL-2.1+ |
| tzdata (installer live image) | public domain |
| Live installer only: debootstrap | MIT (Expat) |
| Live installer only: dpkg | GPL-2.0+ |
| Live installer only: perl-base | GPL-1.0+ OR Artistic-1.0 |
| Live installer only: dash | BSD-3-Clause |
| Live installer only: dosfstools | GPL-3.0+ |
| Live installer only: eject | GPL-2.0+ (from util-linux) |
| Dropbear SSH server | **not in release builds**: only in a lab build (`build-iso.sh --lab`), and not in the release pool |

Our own programs in the initramfs (`fanout`, `hivewrite`, `lldpsniff`,
`imagesrv-monitor`, `native-gui`, the agent shell scripts) are MIT. Note that
`imagesrv-monitor` links LibVNCServer (section D), see the note there.

### D. Kiosk / GUI libraries and font (`--with-gui`)

| Component | Version | License |
|---|---|---|
| **LibVNCServer** (`libvncserver1`) | 0.9.15+dfsg-1+deb13u2 | **GPL-2.0+**, no linking exception (some files permissive) |
| - its runtime dependencies: libgcrypt (`libgcrypt20`) | 1.11.0-7+deb13u1 | LGPL-2.1+ (library), GPL-2.0+ (tools) |
| - GnuTLS (`libgnutls30t64`) | 3.8.9-3+deb13u4 | LGPL-2.1+ |
| - libjpeg-turbo (`libjpeg62-turbo`) | 1:2.1.5-4 | IJG (BSD-style) / BSD-3-Clause / Zlib |
| - LZO (`liblzo2-2`) | 2.10-3+b1 | GPL-2.0+ |
| - zlib (`zlib1g`) | 1:1.3.dfsg+really1.3.1-1+deb13u1 | Zlib |
| Pango | 1.56.3-1 | LGPL-2.0+ (parts LGPL-2.1+) |
| Cairo | 1.18.4-1+b1 | LGPL-2.1 OR MPL-1.1 |
| Pixman | 0.44.0-3 | MIT |
| HarfBuzz | 10.2.0-1+deb13u1 | MIT |
| FriBidi | 1.0.16-1 | LGPL-2.1+ |
| FreeType | 2.13.3+dfsg-1+deb13u1 | FTL |
| Fontconfig | 2.15.0-2.3 | MIT-style |
| GLib | 2.84.4-3~deb13u5 | LGPL-2.1+ |
| libdrm | 2.4.124-2 | MIT |
| **IBM Plex** (`fonts-ibm-plex`, only a few faces are used) | 6.1.1-1 | **SIL OFL-1.1** (Reserved Font Name "Plex") |

Linking note: `imagesrv-monitor` is our MIT code linked with LibVNCServer
(GPL-2.0+). The resulting binary is distributed under the GPL-2.0+; its
complete source is `agent/monitor.c` and `agent/hmac_sha256.c` in this
repository (MIT, which is GPL-compatible).

Font note: Plex is shipped unmodified, as files copied from the Debian
package, together with its license (`/usr/share/doc/fonts-ibm-plex/copyright`
and the OFL text). It is not sold separately.

### E. Optional technician toolbox (only if selected)

nvme-cli (GPL-2.0+), nwipe (GPL-2.0+, some files GPL-2.0), tpm2-tools
(BSD-3-Clause), chntpw (GPL-2.0, library parts LGPL-2.1), testdisk / photorec
(GPL-2.0), scalpel (GPL-2.0+), foremost (public domain), extundelete
(GPL-2.0), magicrescue (GPL-2.0+), recoverjpeg (GPL-2.0), squashfs-tools
(GPL-2.0+), libarchive-tools / bsdtar (BSD-2-Clause and others), gddrescue
(GPL-2.0+), safecopy (GPL-2.0+), dosfstools (GPL-3.0+).

### F. Kernel and firmware

* Linux kernel and the modules in the initramfs: GPL-2.0 (section B).
* Firmware in the initramfs, shipped as binary blobs (from Debian
  `firmware-realtek` and `firmware-intel-graphics`, non-free-firmware):
  * `rtl_nic/*` (Realtek): Debian license id `binary-redist-Realtek-permissive`
    (distribution permitted with the copyright notice accompanying it); the
    `rtl8153*` and `rtl8156*` files: `binary-redist-Realtek-restrictive`
    (binary redistribution without modification; reproduce the notice and the
    disclaimer in the documentation; no reverse engineering; Realtek's name
    may not be used to endorse derived products; a limited patent license).
  * `i915/*` (Intel): Debian license id `binary-redist-Intel`:
    redistribution only in binary form, unmodified; the copyright notice,
    conditions and disclaimer must be reproduced in the accompanying
    documentation; no reverse engineering; Intel's name may not be used to
    endorse derived products.
  The ISO pool carries the whole of both packages: the rest of
  `firmware-realtek` (`rtl_bt/*` and others) is under the same two Realtek
  ids, and `firmware-intel-graphics` also has one `intel/ipu` file under
  `binary-redist-Intel-2`.
  The full texts are in the packages' `copyright` files and must travel with
  the firmware (ISO and initramfs).
* Wired network-card firmware (#1820), on the ISO pool and installed on the
  server, **not** in the initramfs. Each whole Debian 13 package is shipped;
  the license ids below are those Debian's `copyright` file assigns to the
  files each package ships (20250410-2; `firmware-linux-free` 20241210-2).
  The ids were matched file by file; the license texts themselves are not
  summarised here and are in each package's `copyright` file:
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
    `-Intel-narrower-patent`. One file, `intel/ice/ddp-lag/ice_lag-1.3.1.0.pkg`,
    has no entry in Debian's `copyright` file; its license is not verified.
  * `firmware-misc-nonfree`: `tg3` and `tehuti` `binary-redist-firmware`,
    Chelsio `cxgb4` `binary-redist-Chelsio`, `cxgb3` `BSD-2-clause` and
    `binary-redist-NetLogic`, `typhoon` `BSD-3-clause-3Com`. This package
    also carries firmware for many non-network devices under dozens of
    other ids; the per-file list is in its `copyright` file.
  * `firmware-linux-free` (Debian main; `pcnet_cs`): GPL-2.0, GPL-2.0+,
    GPL-2.0 OR MPL-1.1, GPL-3.0.
* Firmware on the ISO pool only, pulled in as Recommends of
  `firmware-misc-nonfree` and `firmware-linux-free`. The installer does not
  install them (every install is `--no-install-recommends`):
  * `firmware-nvidia-graphics`: `binary-redist-Nvidia`.
  * `firmware-mediatek`: `binary-redist-MediaTek`, `binary-redist-Ralink`.
    Three files under `mediatek/mt7925/` have no entry in Debian's `copyright`
    file; their license is not verified.
  * `firmware-ath9k-htc` (Debian main): BSD-3-Clause-Clear.
  * `firmware-carl9170` (Debian main): GPL-2.0+.

### G. Build tools (ISO pool / installed server)

| Component | License |
|---|---|
| GCC (`gcc`, `gcc-14`, `cpp`) | GPL-3.0+, with the GCC Runtime Library Exception |
| GNU make (`make`) | GPL-3.0+ |
| pkgconf (`pkg-config`, `pkgconf`) | ISC |
| GNU C Library headers (`libc6-dev`) | LGPL-2.1+ |
| `libhivex-dev`, `libpango1.0-dev`, `libcairo2-dev`, `libdrm-dev`, `libvncserver-dev` | the same as the library each belongs to (sections C and D) |

## 3. Source code for GPL / LGPL components

Several components above are licensed under the GNU General Public License or
Lesser General Public License, which give you the right to receive the
corresponding source code of the exact binaries we distribute.

**Source comes with the release.** Every ImageSrv release is published on
GitHub (https://github.com/NadavOked/ImageSrv/releases). The corresponding
source code of every GPL/LGPL component contained in that release is attached
to the same Release page, next to the ISO and the USB image, so you receive the
source from the same place and at the same time as the binaries. No request
and no contact address are needed. The exact package versions of a release
are listed in `imagesrv-iso.json` (`pool_debs`), also attached to the Release.

**Debian's own copy.** Every component in sections B-F is an unmodified
Debian 13 package, and the source package of the same version is also
published by Debian: `https://deb.debian.org/debian/pool/` and permanently at
`https://snapshot.debian.org/`. `apt-get source <package>=<version>` fetches
a given source package.

**Our own code** (MIT) is in this repository: https://github.com/NadavOked/ImageSrv

## 4. Full license texts

* GPL-2.0, GPL-3.0, LGPL-2.1, LGPL-3.0, Apache-2.0, BSD-3-Clause, MPL-2.0,
  Artistic-2.0 (the standard texts): `/usr/share/common-licenses/` on every
  Debian system.
* Everything else: `/usr/share/doc/<package>/copyright`, which Debian
  installs for every package. These files are inside each `.deb` of the ISO pool.
* IBM Plex, SIL Open Font License 1.1: https://openfontlicense.org/ and
  `/usr/share/doc/fonts-ibm-plex/`.

"Debian" is a registered trademark of Software in the Public Interest, Inc.
ImageSrv is not affiliated with or endorsed by the Debian project; the ISO is
built from the official Debian netinst image and Debian packages.
