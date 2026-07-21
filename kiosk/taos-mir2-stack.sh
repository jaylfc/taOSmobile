#!/bin/bash
# taOS exclusive surface: Mir 2 kiosk shell hosting Electron, nested on the
# Ubuntu Touch system compositor.
#
# WHY THIS SHAPE
#
#   lomiri-system-compositor   Mir 1. Owns the VT and the Android hwcomposer
#                              HAL via libhybris. Left completely alone —
#                              every attempt to take the display from it
#                              (cage/wlroots, Qt eglfs) powered the phone off.
#     └── miral-kiosk          Mir 2.20 using the `mir:wayland` platform, so
#                              it nests as an ordinary Wayland client instead
#                              of touching hardware. Provides a kiosk shell
#                              (single fullscreen surface, no panel) AND a
#                              modern Wayland implementation.
#     └── Electron 43          Current Chromium. Renders the taOS SPA properly
#                              — unlike webapp-container (QtWebEngine 5.15 ==
#                              Chromium 87), which lacks Object.hasOwn/
#                              structuredClone and mis-renders the UI.
#
# Electron could not run directly on Mir 1: its Wayland lacks wp_viewporter
# and text-input-v3 and errors on every Surface::commit(). Mir 2 supplies all
# of that, which is what makes this stack work.
#
# Notes:
#  - Platform modules are named mir:wayland / mir:egl-generic, NOT the .so
#    filenames (graphics-wayland.so) — Mir rejects the latter.
#  - Mir resolves WAYLAND_DISPLAY relative to XDG_RUNTIME_DIR, so the system
#    compositor's socket (/run/wayland-syscomp) must be linked into
#    /run/user/<uid>/ first.
set -u

RUNTIME_DIR="/run/user/$(id -u)"
SYSCOMP_SOCKET=/run/wayland-syscomp
LOG_DIR="$HOME/.taos-logs"; mkdir -p "$LOG_DIR"
CONTROLLER_URL="${TAOS_URL:-http://localhost:6969/}"

log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$LOG_DIR/mir2-stack.log"; }

export XDG_RUNTIME_DIR="$RUNTIME_DIR"

# 1. Make the host compositor's socket reachable by name.
ln -sf "$SYSCOMP_SOCKET" "$RUNTIME_DIR/wayland-syscomp"

# 2. Start the Mir 2 kiosk shell, nested.
before="$(ls "$RUNTIME_DIR" | grep -E '^wayland-[0-9]+$' | sort)"
setsid nohup env \
    XDG_RUNTIME_DIR="$RUNTIME_DIR" \
    MIR_SERVER_PLATFORM_DISPLAY_LIBS=mir:wayland \
    MIR_SERVER_PLATFORM_RENDERING_LIBS=mir:egl-generic \
    MIR_SERVER_WAYLAND_HOST=wayland-syscomp \
    WAYLAND_DISPLAY=wayland-syscomp \
    MIR_SERVER_ADD_WAYLAND_EXTENSIONS=all \
    miral-kiosk \
    >> "$LOG_DIR/miral-kiosk.log" 2>&1 < /dev/null &

# Wait for the shell to publish its own socket rather than guessing a name.
waited=0
while [ "$waited" -lt 30 ]; do
    now="$(ls "$RUNTIME_DIR" | grep -E '^wayland-[0-9]+$' | sort)"
    SOCK="$(comm -13 <(echo "$before") <(echo "$now") | head -1)"
    [ -n "${SOCK:-}" ] && break
    sleep 2; waited=$((waited + 2))
done
SOCK="${SOCK:-wayland-0}"
log "miral-kiosk socket: $SOCK (after ${waited}s)"

# 3. Electron as a client of the kiosk shell.
exec env \
    XDG_RUNTIME_DIR="$RUNTIME_DIR" \
    WAYLAND_DISPLAY="$SOCK" \
    TAOS_URL="$CONTROLLER_URL" \
    /home/phablet/electron/electron /home/phablet/electron/app \
        --no-sandbox \
        --ozone-platform=wayland \
        --enable-features=UseOzonePlatform \
    >> "$LOG_DIR/electron.log" 2>&1
