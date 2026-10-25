# native-gui -- build on Debian 13 (see deps-debian.txt for the packages).
#   make            both binaries
#   make smoke      build + run the Hebrew bidi smoke test (writes hebrew-bidi.png)
#   make png        render every card, both themes, to ./out/*.png (sample data;
#                   STATE=/path/to/state.txt renders the real thing)
CC      ?= cc
CFLAGS  ?= -O2 -g -Wall -Wextra -std=c11
CFLAGS  += -D_GNU_SOURCE

GUI_PKGS   = pangocairo cairo libdrm
SMOKE_PKGS = pangocairo cairo harfbuzz fribidi

GUI_SRC = src/main.c src/screens.c src/screens_capture.c src/screens_failure.c src/screens_rounds.c src/screens_room.c src/screens_drivers.c src/screens_class.c src/screens_cloner.c src/screens_standby.c src/screens_restore.c src/screens_tools.c src/screens_shrink.c src/screens_ask.c src/screens_syspart.c src/screens_lcboot.c src/screens_local.c src/screens_lcrun.c src/lcbay.c src/lc_state.c src/lc_text.c src/lc_pull.c src/smartdlg.c src/canceldlg.c src/screens_install.c src/screens_install_setup.c src/screens_install_forms.c src/screens_install_final.c src/install.c src/dcui.c src/screens_dcui.c src/widgets.c src/brand.c src/splash.c           src/state.c src/text.c src/draw.c src/theme.c src/backend.c src/input.c
GUI_HDR = src/splash.h src/localclone.h src/lcui.h src/smartdlg.h src/canceldlg.h src/dcui.h src/brand.h src/screens_rounds.h src/screens_install.h src/install.h src/install_raid.h src/install_state.h src/ui.h src/widgets.h src/state.h src/text.h src/draw.h src/theme.h src/backend.h src/input.h src/utf8.h

all: imagectl-station-gui hebrew-bidi-smoke

imagectl-station-gui: $(GUI_SRC) $(GUI_HDR)
	$(CC) $(CFLAGS) $$(pkg-config --cflags $(GUI_PKGS)) -o $@ $(GUI_SRC) $$(pkg-config --libs $(GUI_PKGS)) -lm

hebrew-bidi-smoke: smoke/hebrew_bidi.c
	$(CC) $(CFLAGS) $$(pkg-config --cflags $(SMOKE_PKGS)) -o $@ $< $$(pkg-config --libs $(SMOKE_PKGS)) -lm

smoke: hebrew-bidi-smoke
	./hebrew-bidi-smoke hebrew-bidi.png

png: imagectl-station-gui
	mkdir -p out
	./imagectl-station-gui --png out/station --mac 3C:52:82:A1:00:1F --ip 10.10.10.31 $(if $(STATE),--state $(STATE),)

clean:
	rm -f imagectl-station-gui hebrew-bidi-smoke hebrew-bidi.png
	rm -rf out

.PHONY: all smoke png clean print-gui-src

# tests/test_headless_framebuffer.py builds the GUI from this list, so a new
# screens_*.c is added in one place (#828 added four and the test went red).
print-gui-src:
	@echo $(GUI_SRC)
