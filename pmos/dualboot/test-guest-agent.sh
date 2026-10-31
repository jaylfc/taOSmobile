#!/bin/sh
# As root (chown to the new uid): sh pmos/dualboot/test-guest-agent.sh. Runs dualboot_agent_account
# on a fake guest root and checks what a guest would see. Prints PASS/FAIL lines, exits 1 on any FAIL.
. "$(dirname "$0")/guest-agent.sh"
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
fails=0
ok() { if eval "$2"; then echo "PASS $1"; else echo "FAIL $1"; fails=$((fails + 1)); fi; }

R=$T/root; mkdir -p "$R/etc/sudoers.d" "$R/usr/lib"
printf 'root:x:0:0::/root:/bin/bash\nluma:x:1000:1000::/home/luma:/bin/bash\n' > "$R/etc/passwd"
# 949 taken in the OSTree-style passwd, 948 as a group only: the account must land on 947
printf 'greetd:x:949:949::/:/sbin/nologin\n' > "$R/usr/lib/passwd"
printf 'root:x:0:\nfoo:x:948:\n' > "$R/etc/group"
printf 'root:*::0:99999:7:::\nluma:$6$x::0:99999:7:::\n' > "$R/etc/shadow"; chmod 600 "$R/etc/shadow"
printf 'root:::\n' > "$R/etc/gshadow"
printf 'root ALL=(ALL) ALL\n#includedir /etc/sudoers.d\n' > "$R/etc/sudoers"
echo 'ssh-ed25519 AAAA test' > "$T/keys"
ino=$(stat -c %i "$R/etc/shadow")

dualboot_agent_account "$R" "$T/home/taos-agent" /home/taos-agent "$T/keys" > "$T/out1"; rc1=$?
dualboot_agent_account "$R" "$T/home/taos-agent" /home/taos-agent "$T/keys" > "$T/out2"; rc2=$?

ok "both runs exit 0" '[ $rc1 = 0 ] && [ $rc2 = 0 ]'
ok "uid skips ids taken in usr/lib/passwd and etc/group" 'grep -qx "taos-agent:x:947:947:taOS agent:/home/taos-agent:/bin/bash" "$R/etc/passwd"'
ok "one passwd line after two runs" '[ "$(grep -c "^taos-agent:" "$R/etc/passwd")" = 1 ]'
ok "one group line" '[ "$(grep -c "^taos-agent:x:947:" "$R/etc/group")" = 1 ]'
ok "one shadow line, no password" '[ "$(grep -c "^taos-agent:\*:" "$R/etc/shadow")" = 1 ]'
ok "gshadow line" 'grep -q "^taos-agent:!::" "$R/etc/gshadow"'
ok "shadow appended in place: same inode" '[ "$(stat -c %i "$R/etc/shadow")" = "$ino" ]'
ok "shadow keeps mode 600" '[ "$(stat -c %a "$R/etc/shadow")" = 600 ]'
ok "existing shadow lines untouched" 'grep -qx "luma:\$6\$x::0:99999:7:::" "$R/etc/shadow"'
ok "sudoers drop-in 440, NOPASSWD" '[ "$(stat -c %a "$R/etc/sudoers.d/90-taos-agent")" = 440 ] && grep -qx "taos-agent ALL=(ALL) NOPASSWD: ALL" "$R/etc/sudoers.d/90-taos-agent"'
ok "keys installed 600, owned by the agent" '[ "$(stat -c %a:%u "$T/home/taos-agent/.ssh/authorized_keys")" = 600:947 ] && cmp -s "$T/keys" "$T/home/taos-agent/.ssh/authorized_keys"'
ok "home 700" '[ "$(stat -c %a "$T/home/taos-agent")" = 700 ]'

# a guest whose sudoers reads no drop-ins would silently leave the agent without sudo: refuse
R2=$T/root2; cp -a "$R" "$R2"; printf 'root ALL=(ALL) ALL\n' > "$R2/etc/sudoers"
dualboot_agent_account "$R2" "$T/home2" /home/taos-agent "$T/keys" > "$T/out3" 2>&1; rc3=$?
ok "no includedir: refused" '[ $rc3 != 0 ] && grep -q NOSUDOERSD "$T/out3"'
ok "no includedir: home not created" '[ ! -e "$T/home2" ]'

[ $fails = 0 ] && echo "ALL PASS" || { echo "$fails FAILED"; exit 1; }
