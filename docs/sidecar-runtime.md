# Sidecar Runtime Filesystem Policy

Agent runtime sidecars are configured with read-only root filesystems in
`infra/docker-compose.yml`. Runtime state must be written only to the explicit
tmpfs mounts below:

- `/tmp`: transient process scratch space, mounted `rw,noexec,nosuid,nodev`.
- `/var/run/ao`: agent-orchestration sidecar sockets and pid files, mounted
  `rw,nosuid,nodev`.

Helper and observability sidecars must keep the `ao.sidecar: "true"` label,
`read_only: true`, and the documented tmpfs mounts. CI coverage rejects sidecar
services that remove the read-only root or add undocumented writable paths.
