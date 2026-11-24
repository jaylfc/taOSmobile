# pmos/mesh

`install-mesh-instance.sh PERSON [--start]` installs the taOS per-person mesh
instance (slot 0) on the phone, using the same module and unit template every
taOS host uses (`tinyagentos.taosnet.mesh_instances`, `taos-mesh@.service`).

What it does, as root:

1. Checks PERSON against `^[a-z][a-z0-9-]{0,30}$`.
2. Records the personal tailnet (default socket `tailscale status --json`:
   BackendState and own IPs, plus `ip route show table 52`). Read only.
3. Runs `install-template` (owner step), then `allocate PERSON` as user
   `taos`, and refuses unless the slot is 0.
4. Inert unless `--start`: then `systemctl enable --now taos-mesh@PERSON` and
   waits up to 20 s for `/run/taos/mesh/PERSON.sock`.
5. Re-reads the personal tailnet. If anything changed it restarts the
   personal `tailscaled` and fails loudly if it is still different.

It never runs `tailscale up/login/logout/set`, and never touches the kiosk or
the personal tailscaled's config. `TAOS_PYTHON` overrides the python path
(default `/root/tinyagentos/.venv/bin/python`).

This is NOT run on the handset until the security gate has passed. Today it is
exercised only by `test_install_mesh_instance.py`, with every tool stubbed.
