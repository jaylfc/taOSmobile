# Sourced by kexec-guest.sh and refresh-guest-boot.sh (each also works from a copy of this directory).
# The guest kernel must be the build that is running, or the modules on taOS's root will not load.
# Both the release and the whole build tag are compared as literal strings, never as a regex prefix,
# so build "#9" does not accept "#90".

# dualboot_build_matches <"Linux version" line> <release> <build tag>
dualboot_build_matches() {
  _rel=$(printf '%s\n' "$1" | sed -n 's/^Linux version \([^ ]*\) .*/\1/p')
  _tag=$(printf '%s\n' "$1" | tr ' ' '\n' | sed -n '/^#/{p;q;}')
  [ -n "$_rel" ] && [ -n "$_tag" ] && [ "$_rel" = "$2" ] && [ "$_tag" = "$3" ]
}

# An uncompressed kernel image on stdin against the running kernel (uname -r, first field of uname -v)
dualboot_stream_matches_running() {
  _line=$(strings | grep -m1 '^Linux version') || return 1
  dualboot_build_matches "$_line" "$(uname -r)" "$(uname -v | cut -d' ' -f1)"
}
