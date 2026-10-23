# Changelog

What each release of Optimux changed. The version rules are in [CONTRIBUTING.md](CONTRIBUTING.md#versions-and-releases); the newest release is at the top.

## v0.6.0 (2026-10-23) — beta

Measure. Optimux now shows what routing saves, keeps a small record of every request (for routing that learns from outcomes later), refuses requests for unknown host names, and every change is checked by a real install on fresh Ubuntu machines.

**Before you update**

- Nothing to do for a local install opened as `localhost` or by IP address, the SSH tunnel, or a server installed with a domain.
- **Only if you reach Optimux by a name over TCP** (your own proxy to `127.0.0.1:PORT`, or `http://mybox.lan:8000`): add that name to `OPTIMUX_ALLOWED_HOSTS` in Optimux's environment, or it answers 400.
- The first start adds two columns to the usage table and a new `tasks` table (automatic). Open tabs offer a reload.

**New**

- **What routing saved** (top of Dashboard → Performance): what you spent, what the same calls would have cost on your Complex model, and the difference, per day, per role and per project; a **Saved · month** tile on Overview. Calls from before this release are estimated with today's prices and marked so.
- **Task records:** one row per request (tier, model, the Conductor's decision, escalation and why, outcome, steps, calls, cost, time, files changed, commands approved and rejected), with no message text. **Download CSV** or **Delete all** in Dashboard → Usage.
- **👍 / 👎 under each reply:** saved in the request's record and shown again when you reopen the chat.
- **Escalations count as overhead:** when a model gets stuck and hands over (or you press Escalate), its calls in that request count as extra cost in the savings.
- **`scripts/smoke-test.py`:** checks an install end to end (a fake model, one approved command) without an API key. CI now runs install, status, this check, update and uninstall on fresh Ubuntu 22.04 and 24.04, local and server.

**Fixed**

- **DNS rebinding:** a website could point its own name at your computer and reach a local Optimux's sign-in page. Unknown host names now get 400 (localhost, IP addresses and `OPTIMUX_ALLOWED_HOSTS` are allowed).
- `optimux uninstall --yes` asked anyway and failed without a terminal; it now removes the program and keeps your data.
- `optimux update` and `restart` said they were done before Optimux answered; they now wait for it.
- The installer's final box was too wide for an 80-column terminal.

## v0.5.2 (2026-10-23) — beta

Small safety. The daily-backups screen is a small feature that grew out of checking the backups (IM-05), so this release is a little more than a patch, like v0.5.1.

**Before you update**

- Nothing to do. Reload open Optimux tabs once after this update; from now on a tab tells you itself when Optimux was updated.
- A memory file over 20,000 characters keeps working, but can't be saved larger until it is shortened.

**New**

- **Daily backups in Dashboard → Backups:** every daily database backup with its check, Back up now, how many to keep (3 to 60), Restore (with the snapshot rules: your current data is saved first, usage, costs and sign-ins are never rolled back), an encrypted Download, and Delete.
- **"Optimux was updated — Reload":** a tab that stayed open during an update says so and offers a reload, instead of running old code.
- **Memory limit:** 20,000 characters per memory file (about 5,000 tokens on every request), with a counter in the editor. A longer save, or a model's "remember this" that would go over it, is refused with a message.

**Fixed**

- Daily backups were never checked: a damaged copy could replace a good one, and good ones were pruned anyway. Each copy is now checked before it replaces anything; a failed one is kept aside and Dashboard → System warns.
- Memory files were written in place: a crash or a full disk could leave them cut. They are now written in one step.
- The memory editor said "Saved!" even when the save was refused.
- A command in a sandbox without network failed with "Could not resolve host" and models retried; now a line says why and what to do.

## v0.5.1 (2026-10-23) — beta

Cost and secrets. Besides the fixes, this release has a few small features that came out of testing them on a server (Reject with a reason, the time limit in the approval box, keep waiting at the limit), so it is a little more than a patch.

**Before you update**

- Commands may now run for **120 seconds** before Optimux asks about them (it was 30, and they were killed). Set another value in **Dashboard → Execution → Timeout**.
- The first start after the update hides saved keys in chats stored before (after the daily backup). Older daily backups and snapshots keep what they had; daily backups are replaced within 14 days.
- Reload any open Optimux tab after `optimux update`.

**New**

- **Saved keys never leave the server:** a key from `secrets.env` (or a key-like variable of the server) is shown as `••••(NAME)` in command output, file diffs, your messages, the model's replies, stored chats and everything sent to a model. A model can keep a key where a file already has it, but can never place or copy one.
- **Long output is cut in the middle:** a command keeps the first 8 KB and the last 16 KB of its output (cut at whole lines), with a note on how to see a part, so one huge output can't flood the model or fill the server's memory.
- **Command time limits:** a Timeout setting (10 s to 30 min), a time limit per command in the approval box (or no limit), a running counter, and at the limit the command keeps running while Optimux asks whether to keep waiting.
- **Reject with a reason:** type why next to Reject; the model gets it with the refusal and tries again your way.

**Fixed**

- The full output of a command was resent to the model at every following step of a reply.
- A key inside a file a command printed reached the chat, the database and the provider.
- Every command was killed after 30 seconds, including installs, builds and test runs.
- After a Reject, the model refused to run the command for the rest of the chat even when asked.
- A timed-out command lost what it had printed.

## v0.5.0 (2026-10-22) — beta

**Before you update**

- Nothing to do. After `optimux update`, reload any open Optimux tab so it gets the new page.
- On a server, if `optimux port` from the previous days left the service and the tunnel on different ports, `sudo optimux update` (or `sudo optimux port <the port in install.env>`) puts them back together.

**New**

- **Dashboard → Performance:** what every model costs and how well it works, per project and period: spending over time, price against speed, who does the work (each role's share of calls and of money), how long a call takes (p10, median, p90, tokens per second), what the tokens are, where the money goes (every project × every model, with totals), and a sortable table with cost per call, cost per 1,000 output tokens, cache share, reliability and escalations. Failed calls and escalations are recorded from this version on.
- **Push and Update from GitHub:** Push… and Update… on every project card and under a reply that changed files. Push shows every file, refuses saved keys inside files, and can write the commit message with the cheap model, following the project's commit template. Update is fast-forward only and never touches your local changes.
- **Files changed:** every reply that changed the project lists the files it added, changed or deleted, from git.
- **Models with a Base URL:** proxies, resellers and your own servers (OpenAI- or Anthropic-compatible), with a clear warning about where your prompts go and how prices are counted. Model cards show the real price.
- **Chat page:** a flashlight route animation (also while waiting for your approval), the chat name and the prompt on one line, a ↑ button that steps back prompt by prompt, rename from the chat line, and the commit-message cost in the header counter.
- **CLI:** `optimux open` with `--local-port`, `--user`, `--host`, `--background` and `--ssh-config` (also as `optimux tunnel` and `optimux browser`); `optimux port` to move Optimux on a server; clear, structured output for help, open, port and status.

**Fixed**

- The Conductor failed on gpt-5-nano (temperature 0 refused).
- A model could garble a file with escaped characters or halve it with `write_file`; such writes are now refused before approval.
- `optimux port` on a server stopped halfway ("CM_LOG: unbound variable"), leaving the service and the tunnel on different ports.
- Push could say the git identity was fine and then fail to commit on accounts without a full name (and the GitHub Actions tests failed for the same reason).
- "Files changed" could miss a same-size edit made right after git wrote its index.

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
