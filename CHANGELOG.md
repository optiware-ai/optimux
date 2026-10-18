# Changelog

What each release of Optimux changed. The version rules are in [CONTRIBUTING.md](CONTRIBUTING.md#versions-and-releases); the newest release is at the top.

## v0.4.0 (2026-10-18) — beta

The first numbered release since v0.1.0. It brings together the work of v0.2 to v0.4.

**Before you update**

- Optimux no longer reads installs, data or settings from its old name, CodeMux. Installs made as Optimux are not affected.
- Plan mode is now **Off** by default for new installs. An existing install keeps its setting: switch the **Plan** chip next to *New chat* to *Off* if you want the new default.

**New**

- **Backups:** snapshots of your data and the Workspace (everything, or chosen parts), encrypted downloads, and restore from the list or from a downloaded file.
- **Models:** pick a provider and model from a catalog with prices, see which models are free for your key, and see your keys' rate limits. Refreshing the catalog online is opt-in.
- **Plan mode:** big tasks can be split into steps that run on the cheapest capable model, with a plan card to check, edit and run. Measured on six everyday tasks it didn't pay off yet, so it is off by default; `/plan` still works. See *Plan big tasks* in the Guide.
- **File tools:** models create and change files with `write_file` and `edit_file` instead of shell commands. Each change is approved with its diff, written in one step, and only inside the active project. An edit whose text isn't found is refused and shown in red, so a missed change can't pass as done.
- **Projects:** one-line *Push manually* and *Pull manually* commands per project; a repository can be cloned again as another copy, which pushes to its own branch.
- **Chat page:** a routing animation while Optimux waits for a model, a cost counter in the header that rolls like a mechanical counter after every call, the plan mode next to *New chat*, and the prompt pinned above a long reply.
- **Versions:** a `VERSION` file; Dashboard → System, snapshots and `optimux version` show the release and the exact commit.

**Fixed**

- The sandbox works under the systemd service on servers.
- The Conductor no longer fails silently with some models (Gemma).
- The installer lists other public services before turning on the firewall, and shows how to open a server through an SSH tunnel.
- The Workspace folder can be moved; unwritable folders are refused.
- Chats of removed projects open read-only.
- Dashboard → System showed the version as "unknown" on servers.

## v0.1.0 (2026-10-11) — beta

The first public release: routing to the cheapest capable model, metered costs and budgets, approved and sandboxed commands, memory, skills, projects with GitHub, and the one-line installer.
