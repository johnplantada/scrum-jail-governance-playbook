# Installed control plane

`sjg_control` is the trusted execution boundary. It is packaged from a reviewed golden
release and installed under a separate OS identity. It is deliberately not copied by
`bin/orggen`; generated repositories contain only unprivileged clients and desired state.

The 1.0 control plane is a single-host modular monolith using SQLite WAL. It rejects
multi-host/NFS state. Human-only operations are absent from the agent request protocol.
`sjg-supervisor` supplies complete GitHub polling, durable routing and leases, role-bound
wake sockets, provider process groups, budget settlement, crash recovery, ambiguous-write
verification, shadow mode, and an operator-authenticated admin socket. It never installs a
credential or turns on live mode by itself.

The installer includes an exact JSON configuration example plus reviewed systemd and launchd
templates under `share/`. Commissioning, service identities, GitHub App permissions, policy
activation, sentinel proof, and rollback are documented in `docs/CONTROL-PLANE.md` and
`RUNBOOK.md`.
