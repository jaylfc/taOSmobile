#!/bin/sh
# Install the handset's touch browser: Firefox with postmarketOS's phone layout.
# Run as root ON THE DEVICE.
#
# Jay 2026-10-09: agents' sign-in links (Claude subscription and the like)
# open in a real touch browser, and Firefox + mobile-config-firefox is the
# pmOS-packaged one. Chrome for Android would need Waydroid (a whole Android
# container) and was ruled out. The kiosk chromium stays the taOS shell only.
#
# A FILE in this repo, not a hand-edit: a reflash wipes anything typed onto the
# handset.
set -eu

# An `apk add` fires boot-deploy; without this guard it re-flashed the active
# boot slot once (see pmos/etc-deviceinfo). Refuse rather than risk that.
grep -q '^deviceinfo_flash_kernel_on_update="false"' /etc/deviceinfo 2>/dev/null || {
  echo "refusing: /etc/deviceinfo lacks the boot-flash off switch (install pmos/etc-deviceinfo first)" >&2
  exit 1
}

echo "==> packages"
apk add --no-interactive firefox mobile-config-firefox >/dev/null

echo "==> check"
firefox --version
apk info -e mobile-config-firefox >/dev/null || {
  echo "mobile-config-firefox missing after install" >&2
  exit 1
}
echo "Firefox is installed with the mobile layout."
