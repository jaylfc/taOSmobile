#!/bin/sh
# Install taos-locationd for keeping modem GPS enabled and publishing location fixes.
#
# Run as root ON the device. Idempotent.
#
# This installs:
# - /usr/local/bin/taos-locationd (755)
# - /etc/systemd/system/taos-locationd.service (644)
# - Enables and starts the service
#
# It then verifies installation with checks that print PASS/FAIL and exit 1 on FAIL.

set -eu

HERE="$(dirname "$0")"
BIN_SRC="$HERE/bin/taos-locationd"
UNIT_SRC="$HERE/systemd/taos-locationd.service"

# 1. Install binaries and units
install -m 755 "$BIN_SRC" /usr/local/bin/
echo "installed /usr/local/bin/taos-locationd"

install -m 644 "$UNIT_SRC" /etc/systemd/system/
echo "installed /etc/systemd/system/taos-locationd.service"

# 2. Configure and start the service
systemctl daemon-reload
systemctl enable --now taos-locationd.service

# 3. Verification checks

# Check 1: unit is active
if systemctl is-active --quiet taos-locationd.service; then
    echo "PASS: unit is active"
else
    echo "FAIL: unit is not active"
    exit 1
fi

# Check 2: /run/taos-location exists with group taos
if [ -d /run/taos-location ]; then
    if stat -c %G /run/taos-location 2>/dev/null | grep -q "^taos$"; then
        echo "PASS: /run/taos-location exists with group taos"
    else
        echo "FAIL: /run/taos-location exists but not with group taos"
        exit 1
    fi
else
    echo "FAIL: /run/taos-location does not exist"
    exit 1
fi

# Check 3: prove the active unit is ours (ExecStart contains /usr/local/bin/taos-locationd)
EXEC_START=$(systemctl show -p ExecStart taos-locationd.service | cut -d= -f2)
if echo "$EXEC_START" | grep -q "/usr/local/bin/taos-locationd"; then
    echo "PASS: ExecStart is /usr/local/bin/taos-locationd"
else
    echo "FAIL: ExecStart is not /usr/local/bin/taos-locationd"
    echo "  ExecStart: $EXEC_START"
    exit 1
fi

echo "SUCCESS: taos-locationd installed and verified"