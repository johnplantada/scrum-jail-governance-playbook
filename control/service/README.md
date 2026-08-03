# Service templates

These are operator-reviewed starting points, not self-installing service definitions. Copy the
matching template out of the immutable release, update only host paths, then load it as root.
The supervisor must retain root solely to own the operator socket and start each provider
process as the separate configured agent UID/GID.

The systemd template assumes mutable state under `/var/lib/scrum-jail`, sockets under
`/run/scrum-jail`, and workspaces under `/srv/scrum-jail/workspaces`. Extend
`ReadWritePaths` only for the exact commissioned directories. The launchd template is a
system daemon plist and must be installed in `/Library/LaunchDaemons` with root ownership.

Neither template installs credentials, activates a release, creates OS identities, or enables
live mode. Those remain explicit Human/operator commissioning actions.
