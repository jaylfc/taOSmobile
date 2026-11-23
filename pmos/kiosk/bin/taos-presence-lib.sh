# Sourced by taos-kiosk-screen and taos-kiosk-charger; runs nothing itself.
# Publishes small state files for the controller's presence beats. The
# contract is docs/presence-source.md.
#
#   presence_write NAME VALUE
#
# Writes "VALUE\n" to $XDG_RUNTIME_DIR/taos-presence/NAME: a temp file in the
# same directory, then mv -f over the target, so a watcher sees one moved-to
# event and never a half-written file. Skipped when the file already holds the
# value, so a repeat is not a change. Always returns 0: presence must never
# break the helper that calls it.
presence_write() {
    (
        _pd="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/taos-presence"
        [ -d "$_pd" ] || { umask 077; mkdir -p "$_pd"; } || exit 0
        if [ -f "$_pd/$1" ] && [ "$(cat "$_pd/$1" 2>/dev/null)" = "$2" ]; then
            exit 0
        fi
        _pt="$_pd/.$1.$$"
        printf '%s\n' "$2" > "$_pt" && mv -f "$_pt" "$_pd/$1" || rm -f "$_pt"
    ) >/dev/null 2>&1 || true
    return 0
}
