#!/bin/sh
# Sourced by the guest builders: the taOS agent's own account in a guest, so the agent keeps root
# (sudo, no password) whatever the owner later does to the guest's own users and passwords during its
# first-time setup. dualboot_agent_account ROOT HOME GUEST_HOME KEYS
#   ROOT        the guest's root as mounted now (for OSTree, the deployment)
#   HOME        the agent's home as mounted now (OSTree: the stateroot's var/home/taos-agent)
#   GUEST_HOME  the same home as the guest sees it
#   KEYS        the authorized_keys file to give it
# A system uid (below 1000), so greeters and first-user setups do not list it; password '*' (no
# password login; ssh keys only). passwd/group/shadow are appended to in place, never rewritten, so
# their mode, owner and labels stay the guest's own. Running it again changes nothing.
dualboot_agent_account() {
local R=$1 H=$2 GH=$3 KEYS=$4 U=taos-agent id f
grep -qE '^[#@]includedir /etc/sudoers.d' "$R/etc/sudoers" \
  || { echo "NOSUDOERSD $R/etc/sudoers"; return 1; }
id=$(awk -F: -v u=$U '$1==u{print $3}' "$R/etc/passwd")
if [ -z "$id" ]; then
  id=949
  while cat "$R/etc/passwd" "$R/usr/lib/passwd" "$R/etc/group" "$R/usr/lib/group" 2>/dev/null \
        | awk -F: -v i=$id '$3==i{f=1} END{exit !f}'; do
    id=$((id - 1))
  done
  echo "$U:x:$id:$id:taOS agent:$GH:/bin/bash" >> "$R/etc/passwd"
fi
grep -q "^$U:" "$R/etc/group" || echo "$U:x:$id:" >> "$R/etc/group"
grep -q "^$U:" "$R/etc/shadow" || echo "$U:*::0:99999:7:::" >> "$R/etc/shadow"
if [ -f "$R/etc/gshadow" ] && ! grep -q "^$U:" "$R/etc/gshadow"; then echo "$U:!::" >> "$R/etc/gshadow"; fi
f=$R/etc/sudoers.d/90-taos-agent
echo "$U ALL=(ALL) NOPASSWD: ALL" > "$f.new"; chmod 440 "$f.new"; mv "$f.new" "$f"
mkdir -p "$H/.ssh"
cp "$KEYS" "$H/.ssh/authorized_keys"
chmod 700 "$H" "$H/.ssh"; chmod 600 "$H/.ssh/authorized_keys"
chown -R "$id:$id" "$H"
echo "agent account $U uid $id"
}
