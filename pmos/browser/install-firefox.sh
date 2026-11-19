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
# Read the RESOLVED value the way boot-deploy does: a later assignment, here or
# in /usr/share/deviceinfo, overrides a matching line in /etc/deviceinfo.
# Unset first so a value exported into this shell cannot stand in for the
# device's own; deviceinfo_arch is the control that the source happened.
RESOLVED="$(sh -c 'unset deviceinfo_flash_kernel_on_update deviceinfo_arch
                   . /usr/share/misc/source_deviceinfo 2>/dev/null || exit 1
                   [ -n "${deviceinfo_arch:-}" ] || exit 1
                   printf "%s" "${deviceinfo_flash_kernel_on_update:-}"')" || RESOLVED=""
[ "$RESOLVED" = "false" ] || {
  echo "refusing: deviceinfo_flash_kernel_on_update resolves to '$RESOLVED', not false (install pmos/etc-deviceinfo first)" >&2
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
