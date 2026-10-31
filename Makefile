# ImageSrv -- one entry point for the commands that already exist (#2246).
# A thin wrapper: every recipe is the command as CLAUDE.md, README.md or
# tools/iso/README.md writes it. `make help` (the default) lists the targets.
# The pytest settings live nowhere in here -- see tests/conftest.py.

PYTHON ?= python3

# iso: the work tree of tools/iso/README.md (pool in repo/, live initrd in live.img)
ISO_DIR ?= /srv/iso
# usb: the output directory in the console's own hint (server/console_usb.py)
USB_OUT ?= /var/tmp/usb

# Extra arguments, passed through untouched.
INITRAMFS_ARGS ?=
USB_ARGS ?=

.DEFAULT_GOAL := help
.PHONY: help test test-native e2e initramfs iso usb

help:
	@echo "ImageSrv -- make targets (each one runs a command that is documented elsewhere)"
	@echo ""
	@echo "  make test         python -m pytest tests -q                     Python 3.12 or 3.13"
	@echo "  make test-native  same, IMAGESRV_REQUIRE_NATIVE=1 (a skip fails)  needs gcc + native-gui deps"
	@echo "  make e2e          python tools/e2e_simulation.py                real server + four simulated machines"
	@echo "  make initramfs    sudo tools/build_initramfs.sh                 ROOT, DEBIAN 13 only"
	@echo "                    INITRAMFS_ARGS='--output FILE --with-gui ...'"
	@echo "  make iso TAG=vX.Y.Z   sudo tools/iso/build-iso.sh               ROOT, DEBIAN 13 + internet; TAG is required"
	@echo "                    needs, once: tools/iso/make-pool.sh --out \$$ISO_DIR/repo and"
	@echo "                    make initramfs INITRAMFS_ARGS='--installer --with-gui --output \$$ISO_DIR/live.img'"
	@echo "                    (ISO_DIR=$(ISO_DIR); see tools/iso/README.md)"
	@echo "  make usb          sudo tools/build_usb_clone.sh                 ROOT, DEBIAN 13 only"
	@echo "                    USB_OUT=$(USB_OUT)  USB_ARGS='--publish DATA_DIR ...'"
	@echo ""
	@echo "PYTHON=$(PYTHON). On the lab host run the tests through tools/git/lab-run-tests.sh <issue>"
	@echo "(CONTRIBUTING.md, hariza-bemaabada) -- not a bare make test."

test:
	$(PYTHON) -m pytest tests -q

test-native:
	IMAGESRV_REQUIRE_NATIVE=1 $(PYTHON) -m pytest tests -q

e2e:
	$(PYTHON) tools/e2e_simulation.py

initramfs:
	sudo ./tools/build_initramfs.sh $(INITRAMFS_ARGS)

iso:
	@test -n "$(TAG)" || { echo "make iso: TAG=vX.Y.Z is required (the git ref that goes into the ISO)" >&2; exit 2; }
	sudo tools/iso/build-iso.sh --out $(ISO_DIR)/out --pool $(ISO_DIR)/repo --ref $(TAG) --live-initrd $(ISO_DIR)/live.img

usb:
	sudo tools/build_usb_clone.sh --out $(USB_OUT) $(USB_ARGS)
