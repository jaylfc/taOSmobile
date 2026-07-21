#!/bin/bash
# Undo the taOS boot splash. Run on the phone: sudo ./restore-plymouth.sh
set -u
ORIG_FILE=/usr/share/plymouth/themes/.taos-original
ORIG="$(cat "$ORIG_FILE" 2>/dev/null)"

mount -o remount,rw / 2>/dev/null
update-alternatives --remove default.plymouth /usr/share/plymouth/themes/taos/taos.plymouth 2>/dev/null

if [ -n "$ORIG" ] && [ -f "$ORIG" ]; then
    update-alternatives --set default.plymouth "$ORIG"
    echo "restored: $ORIG"
else
    # Nothing was registered before (this image had no alternative set), so
    # just drop ours and let plymouth fall back to its built-in default.
    update-alternatives --auto default.plymouth 2>/dev/null
    echo "no previous theme was registered; reverted to auto"
fi
rm -rf /usr/share/plymouth/themes/taos
