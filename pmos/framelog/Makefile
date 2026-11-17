CC ?= cc
CFLAGS ?= -O2
CFLAGS += -Wall -Wextra -Wno-unused-parameter -std=gnu11
BUILD ?= build

PROTO_DIR := $(shell pkg-config --variable=pkgdatadir wayland-protocols 2>/dev/null)
WLR_XML ?= /usr/share/wlr-protocols/unstable/wlr-screencopy-unstable-v1.xml
XDGO_XML ?= $(PROTO_DIR)/unstable/xdg-output/xdg-output-unstable-v1.xml

GEN := $(BUILD)/wlr-screencopy-unstable-v1-client-protocol.h \
       $(BUILD)/wlr-screencopy-unstable-v1-protocol.c \
       $(BUILD)/xdg-output-unstable-v1-client-protocol.h \
       $(BUILD)/xdg-output-unstable-v1-protocol.c

all: taos-framelog

$(BUILD):
	mkdir -p $(BUILD)

$(BUILD)/wlr-screencopy-unstable-v1-client-protocol.h: $(WLR_XML) | $(BUILD)
	wayland-scanner client-header $< $@
$(BUILD)/wlr-screencopy-unstable-v1-protocol.c: $(WLR_XML) | $(BUILD)
	wayland-scanner private-code $< $@
$(BUILD)/xdg-output-unstable-v1-client-protocol.h: $(XDGO_XML) | $(BUILD)
	wayland-scanner client-header $< $@
$(BUILD)/xdg-output-unstable-v1-protocol.c: $(XDGO_XML) | $(BUILD)
	wayland-scanner private-code $< $@

taos-framelog: framelog.c stats.c stats.h $(GEN)
	$(CC) $(CFLAGS) -I$(BUILD) -o $@ framelog.c stats.c \
	    $(BUILD)/wlr-screencopy-unstable-v1-protocol.c \
	    $(BUILD)/xdg-output-unstable-v1-protocol.c \
	    $(shell pkg-config --cflags --libs wayland-client)

test_stats: test_stats.c stats.c stats.h
	$(CC) $(CFLAGS) -o $@ test_stats.c stats.c

test: test_stats
	./test_stats

clean:
	rm -rf $(BUILD) taos-framelog test_stats

.PHONY: all test clean
