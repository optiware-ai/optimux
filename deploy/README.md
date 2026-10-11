# Deployment kit

Files for running Optimux on a server others can reach (a VPS). The installer
(`install.sh`, server mode) installs and fills them in for you; this folder is
what it uses, and what you'd use to do it by hand. The full walkthrough is in
the user guide: **docs/GUIDE.md → "Running on a server"** (also in the app under
Dashboard → Guide).

| File | Goes to | What it does |
|---|---|---|
| `optimux.service` | `/etc/systemd/system/optimux.service` | Runs Optimux as its own user, with a read-only system, resource limits and the sandbox required. |
| `optimux.env.example` | `/etc/optimux/optimux.env` | Server mode and where Optimux keeps its data. |
| `Caddyfile` | `/etc/caddy/Caddyfile` | HTTPS with automatic certificates, in front of Optimux. |
| `apparmor-bwrap` | `/etc/apparmor.d/bwrap` | Only on Ubuntu 23.10+ if the sandbox check fails. |

These files were checked for syntax, not on a live VPS: keep an SSH session
open the first time you start the service.
