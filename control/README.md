# Installed control plane

`sjg_control` is the trusted execution boundary. It is packaged from a reviewed golden
release and installed under a separate OS identity. It is deliberately not copied by
`bin/orggen`; generated repositories contain only unprivileged clients and desired state.

The reference enforcement core is a single-host modular monolith using SQLite WAL. It
rejects multi-host/NFS state. Human-only operations are absent from the agent request
protocol. This repository does not ship a turnkey privileged host daemon or credential
bootstrapper; a deployment is not operational until the host integration contract is
implemented and commissioned outside agent-writable directories.

Commissioning, service identities, GitHub App permissions, policy activation, and rollback
are documented in `docs/CONTROL-PLANE.md`.
