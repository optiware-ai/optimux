# optimux-tui

Optimux in the terminal. It is a client of the Optimux server, like the web page:
the server does the work, and the client talks to it with the protocol in
[docs/PROTOCOL.md](../docs/PROTOCOL.md). The web page keeps working as before.

**Status:** sign-in, the chat window, the whole Dashboard (but Team), the Studio,
panes and tabs with shells, and a command for every section.

## Install

A separate package from the server, for the computer you work from:

```bash
curl -fsSL https://raw.githubusercontent.com/optiware-ai/optimux/main/install-tui.sh | bash
```

It downloads the ready-made build for Linux or macOS (x86_64, ARM) attached to the
GitHub release, checks its SHA-256 checksum and puts `optimux-tui` in `~/.local/bin`;
where there is none, it builds it from the source (below). `--help` lists its options;
run it again to update, `--uninstall` removes it. The builds come from
`.github/workflows/release-tui.yml`, run for each `v*` tag.

## Build

You need Rust 1.85 or newer ([rustup.rs](https://rustup.rs)).

```bash
cd tui
cargo build --release
# the program: tui/target/release/optimux-tui
```

## Use

```bash
optimux-tui                                # the chat window (asks for the password first
                                           # when you are not signed in, or the session ended)
optimux-tui login                          # only sign in (asks for the password and the code)
optimux-tui status                         # server, signed in or not, version
optimux-tui logout
optimux-tui --server https://optimux.example.com login
```

### The chat window

It opens the active project's latest chat. If a request of that chat is still running
(you quit, or the line dropped), you get it back where it is.

| Key | What it does |
|-----|--------------|
| Enter | send the message (Alt+Enter or Ctrl+J: new line; pasting keeps the lines) |
| Esc | stop the running request |
| Ctrl+N | new chat (a running request is left running) |
| Alt+↑, Alt+↓ | the chat above, below in the list |
| Ctrl+b b | show or hide the chat list (Ctrl+b alone starts a pane key) |
| Ctrl+G | the Dashboard (models and settings); Ctrl+G again: back to the chat |
| Ctrl+D | quit, leaving the running request running |
| Ctrl+O | open a chat of this project |
| Ctrl+P | switch project |
| Ctrl+E | give the last request to the Complex model |
| Ctrl+T | details: the model's thinking, and routine notes in full |
| Ctrl+R / Ctrl+K / Ctrl+X | run (or resume) / cancel a waiting plan / skip a step |
| PgUp, PgDn, ↑↓, Ctrl+End | scroll the chat |
| Ctrl+L | reconnect now |
| Ctrl+H | all keys (F1 too) |
| Ctrl+Q, Ctrl+C | quit optimux-tui, every pane (asks first while a request runs); Ctrl+b x closes only a pane |

### Panes and tabs (Ctrl+b)

As in Herdr (and tmux): the screen holds **tabs**, shown at the bottom right (a click
opens one; a yellow tab with ! has a question waiting), and each tab is split into
**panes**. A pane shows a chat (its own chat and its own line to Optimux; Ctrl+G opens
its Dashboard), the Dashboard, or a **shell**: your shell on this computer, in the
active project's folder (your home folder when Optimux runs on another machine; the
pane says so). A shell always has its frame and title, even alone in its tab, so it
can't be taken for a plain terminal. Nothing of Optimux goes into the shell's
environment. A new pane asks
what it shows: `c` a chat, `d` the Dashboard, `s` a shell.

Press **Ctrl+b**, let go, then a key (the bar at the bottom lists them while it waits):

| Key | What it does |
|-----|--------------|
| ? (or H) | **the keys window**: every shortcut by kind, with a search line (below) |
| o | flip the pane: shell ⇄ chat. The other one keeps running behind it (a shell's programs, a chat's request); again: back |
| v (or % or \|) | split the pane: a new one on the right |
| - (or ") | split the pane: a new one below |
| h j k l, arrows | move to the pane on that side |
| Tab, Shift+Tab | the next, previous pane |
| z | zoom the pane to the whole tab; again: back |
| x | close the pane (a chat asks first while a request runs; a shell ends) |
| c | new tab |
| n, p | the next, previous tab |
| 1 to 9 | that tab |
| T | name the tab (empty: its number only) |
| r | resize: h j k l or the arrows move the nearest border, any other key ends |
| b | show or hide the chat list (in a chat pane) |
| Ctrl+b | the pane gets Ctrl+b itself |
| q | quit everything: running requests keep running on the server |
| Esc | nothing (cancels) |

**Shell and chat in one pane:** Ctrl+b o on a shell shows a chat in its place (the
latest one) and keeps the shell running behind it; Ctrl+b o again brings the shell back
as you left it. On a chat it does the same the other way (a new shell in the project's
folder the first time). The pane's frame says what is behind it, and a question
waiting in a chat behind turns the tab yellow too. Ctrl+b x closes what is in front;
what was behind takes its place. Both are saved with the layout.

**The keys window** (Ctrl+b ?, or a click on "Ctrl+b ? keys" in the bar) opens at the
bottom right: every shortcut, grouped (panes, tabs, quit, chat, Dashboard, Studio,
shell, mouse). Type words to find one ("split", "tab", "dashboard"); ↑↓ choose, and
Enter or a click does it (the window closes first). Esc clears the words, then closes;
a click outside closes it too. Ctrl+b h stays "move left", as in Herdr.

A click on a pane moves there; dragging the border between two panes resizes them;
the wheel scrolls the pane under the pointer (in a shell: what scrolled off its
screen). In a shell every key goes to the shell (Ctrl+C
too); only Ctrl+b is kept. The layout (tabs, splits, the chat each pane shows, the
Dashboard section, a shell's folder) is saved in `layout.json` (readable only by you,
next to the sign-in) and comes back at the next start; shells start again, fresh, in
their folder. Delete the file to start with one chat again.

### The Dashboard (Ctrl+G)

The web page's Dashboard, in the terminal: a menu of sections on the left (↑↓, Enter,
or a click), the section on the right. Esc goes back to the menu, Esc again (or
Ctrl+G) to the chat; a request keeps running meanwhile, and the key bar says when a
question waits in the chat. The sections come to the terminal step by step; the grey
ones still say where to find them in the web page.

**Models** (here now) does what the web page's Models section does:

| Key | What it does |
|-----|--------------|
| t | test the chosen model (one tiny real call) |
| e / c | edit it (a new model ID keeps Routing and the projects using it) / duplicate it |
| k | add or update its API key: typed hidden, saved in `~/.optimux/secrets.env` on the server, never shown again |
| h | its thinking level |
| x | delete it (asks first; the key stays in secrets.env) |
| a | add a model from the catalog: a provider, then its models, cheapest first (`t` tools only, `h` hide retired, `r` the provider's list for your key, `/` find); the online catalog is turned on and off with `o`, the price list refreshed with `p` |
| o | add a model by hand (any LiteLLM ID, a server of your own); a server that is not this computer or your network needs "I understand" ticked |
| / | find a model in the list |

**Routing:** the Conductor, Simple and Complex models (←/→ change a choice), *Plan
big tasks* and the scout; Ctrl+S saves. The projects with their own models are listed
below.

**Budgets:** each project's monthly budget: `e` sets it (empty: none; Block or Warn at
100%), `x` removes it, `w` the cost warnings.

**Usage** and **Performance:** the numbers of the web page's *Details* (the charts stay
there). Usage: `1`–`4` the period, `g` the group (project, role, job, model, skill…),
`x` deletes the task records (asks first). Performance: `1`–`4` the period, `p` the
project.

**Projects:** every project with its costs, chats and budget. Enter opens its page,
`a` makes it active (the chat follows), `n` creates one, `c` clones one from GitHub,
`A` archives it, `l` shows where its files are; `v` switches to the archived projects
(`u` restores one, `D` deletes it for good after you type the sentence; GitHub
repositories are never deleted) and to the deleted ones. `h` opens the project's
History. On a project with git: `P` push, `U` update from GitHub, `S` status, `I` who
commits and talks to the remote, `M` the lines to push from your own terminal. Push
and Update show what will happen first and run only after Enter: `e` writes the
commit message in your editor, `m` has the Simple model write it (one paid call), `c`
says you checked the warnings, `1`/`2` push only the commits already made or commit
the files too.

**Project:** every setting of one project: autonomy, budget, its own models,
planning, memory updates, execution. "Follow global" uses the value set for all
projects. ←/→ on the first line picks another project; Ctrl+S saves; Ctrl+A makes it
active.

**Scope:** the Workspace folder.

**Memory:** `1`–`3` the personal, professional or project file (`p` another
project); `e` opens it in **your editor** (`$VISUAL` or `$EDITOR`, else nano, vim or
vi): save and quit, and it is saved. The project file also has `a` automatic updates
on/off, `i` the idle minutes, `u` update now (one paid call; asks first unless turned
off in Budgets → Cost warnings) and `h` its history (Enter shows a change, `z` undoes
it).

**Skills:** `s` when skills run (off, manual only, auto allowed); `n` a new skill
(from the template, in your editor), `d` a draft from one of the project's chats (one
paid call, then your editor), `e` edit, `m` Manual/Auto, `x` remove (moved to
`~/.optimux/legacy/skills/`, not deleted).

**Execution:** the default autonomy level, the sandbox and its network, extra
read-only folders (commas between them), extra variables, the limits and the steps
per turn. A refused value (a folder that doesn't exist) keeps what you typed and says
why.

The editor runs in the same terminal; while it is open the chat keeps its line to the
server. The text goes to a private temporary file (readable only by you), removed
afterwards.

**Overview:** today's and this month's spending, cache hit, output, the estimated
savings, the budgets of the month, every model's status and roles, the recent chats
(Enter opens one in the chat window).

**History:** `1` the chats, `2` what was sent to the models. Filters: `p` project, `t`
time, `a` archived, `f` find (in the messages and whole requests too), `c` clears
them; `o` sorts the chats. A chat: Enter opens it in the chat window, `n` renames,
`A` archives (or brings back), `D` deletes it after you type its title. A request:
Enter shows its calls (the meta-prompt first) and what each one added, `w` its whole
prompt, `g` goes to its chat; `k` sets how many days the prompts are kept (a shorter
period asks before deleting older ones).

**System:** `l` language, `w` how long a request left running waits, the security
checklist, versions and files, the sign-ins (`o` signs out the others), `P` the
password, `2` two-step codes (on: the key to type in your authenticator app, then a
code; off: password and code), `T` rotates the access token (shown once), storage,
`c`/`C` clear cooldowns. To sign out this terminal: `optimux-tui logout`.

**GitHub:** `t` adds or replaces the token (typed hidden; checked with GitHub before
it is saved), `k` checks it, `x` removes it, `i` who commits (git's name and email),
`r` asks GitHub again.

**Backups:** `1` snapshots, `2` daily backups of the database, `3` restore from a
downloaded file. Snapshots: `n` takes one (what goes in is shown first; `f` switches
to Everything), Enter its details (`m` the README inside it), `R` restores parts of it
(you choose the parts and type the sentence; your current data is saved first), `D`
downloads it to this computer, encrypted with a one-time key (shown once; `s` saves
it as a file only you can read) or to your public key, `x` deletes it, `L` the space
limit. Daily: `b` back up now, `k` how many are kept, `c` check one, `R` restore its
database (typed sentence), `D` download (always encrypted), `x` delete. A restore that
waits for the restart shows a yellow line: `!` restarts Optimux (when it can restart
itself), `X` cancels the restore, `d` hides the "finished" note.

**Studio** (first in the menu): the active project's files, as the web page's Studio;
the preview stays in the web page (a terminal can't show a page). The tree with git's
letters (**M** changed, **U** new, **A** added, **D** deleted, **C** a conflict, **•**
something inside changed; grey: ignored by git), and it follows changes made elsewhere
(a model, a terminal) every few seconds. Enter opens a folder or a file, ←/→ fold,
`f` finds a file by words of its path, `n` new file (opens in your editor), `N` new
folder, `R` rename or move, `x` moves to the Trash (never deleted for good), `m`
mentions it in the chat. A file: `e` edits it in your editor (saved only if nobody
changed it meanwhile; if they did, nothing is written and your text is kept: `R`
reloads, `e` opens your text again), `v` marks from a line and `s` sends the marked
lines to the chat (with where they are), `m` mentions the file. Saved keys show as
`••••(NAME)`: keep the marks and the keys are written back. `g` the Git pane: Space
adds a file to the next commit or takes it back, `a`/`A` all, `d` the diff, `c` commit
(`e` the message in your editor, `w` written by the Simple model, one call), `P` push
and `U` update (the same windows as in Projects).

**Getting started:** the setup steps, each ticked by Optimux from your real settings,
with what it found. The next step is chosen; Enter goes to the section that does it
("Send your first request" goes back to the chat). `f` chooses your features (memory
updates, plan mode, skills), `o` says the defaults are fine, `w`/`x` show or hide the
checklist on the web page's chat.

**Guide:** the user guide (docs/GUIDE.md) from the server. Enter reads from a heading;
in the text ↑↓ PgUp PgDn Space scroll, `n`/`p` the next or previous section, `N`/`P`
the next or previous chapter, `c` the contents (on the section you were reading). `f`
finds words in every section (Enter reads there, `r` comes back to the results). `/`
filters the headings. Tables fit the width: in columns when they fit, else one short
block per row (the chat's replies too).

**Forms:** Tab or ↑↓ move between fields, Ctrl+S saves (or Enter on the last field),
Esc cancels, Ctrl+U clears a field, ←/→ or Space change a choice. A key field shows
only dots.

**Keys:** every section shows its keys in cyan right under what it shows (a long list
leaves them room at the bottom), as well as in the bar at the bottom. A form or a
question shows its own keys there.

### Commands (for scripts)

The same things without the screen. They use the saved sign-in (or `OPTIMUX_TOKEN`);
not signed in: exit code 3. A failed `models test`: exit code 1.

```bash
optimux-tui models list                       # --json for scripts
optimux-tui models show gemini/gemini-3.6-flash
optimux-tui models test gemini/gemini-3.6-flash
optimux-tui models catalog                    # the providers
optimux-tui models catalog gemini --find flash   # a provider's models, cheapest first
optimux-tui models add gemini/gemini-2.5-flash   # asks for the key (hidden) when missing
echo "$KEY" | optimux-tui models add groq/llama-3.3-70b-versatile --key-stdin
optimux-tui models add ollama/phi3 --key-var none --api-base http://127.0.0.1:11434
optimux-tui models edit gemini/gemini-2.5-flash --name Flash --input 0.25 --output none
optimux-tui models edit openai/old-id --id openai/new-id   # Routing follows
optimux-tui models thinking gemini/gemini-2.5-flash low
optimux-tui models delete gemini/gemini-2.5-flash          # --yes in a script
optimux-tui models online on                  # then: models refresh prices
optimux-tui keys list
optimux-tui keys set GEMINI_API_KEY           # typed hidden; --stdin to pipe it in
optimux-tui keys remove GEMINI_API_KEY        # --yes in a script
optimux-tui routing show
optimux-tui routing set --simple gemini/gemini-2.5-flash --conductor off --plan ask --scout on
optimux-tui budgets list
optimux-tui budgets set demo 5 --warn         # none removes it; without --warn: block
optimux-tui budgets warnings escalate off
optimux-tui usage show --period week --by model   # today, week, month, all
optimux-tui usage tasks-csv > tasks.csv
optimux-tui performance --days 7 --project demo
optimux-tui projects list                     # --all: also archived and deleted
optimux-tui projects new my-app --kind python --github private --switch
optimux-tui projects switch my-app            # --workspace: no project
optimux-tui projects files my-app             # where its files are
optimux-tui projects archive my-app           # --yes in a script
optimux-tui projects archived
optimux-tui projects restore my-app
optimux-tui projects delete my-app            # asks for "yes I want to delete my-app"
optimux-tui projects repos --find web         # needs a GitHub token
optimux-tui projects clone owner/repo --switch
optimux-tui project show my-app
optimux-tui project set my-app budget=5 on_limit=warn simple_model=gemini/gemini-2.5-flash max_steps_simple=12
optimux-tui project set my-app simple_model=global   # follow the global value again
optimux-tui scope show
optimux-tui scope set /home/you/git
optimux-tui execution show
optimux-tui execution set --autonomy checkpointed --steps-simple 12 --env "VIRTUAL_ENV JAVA_HOME"
optimux-tui memory show personal              # personal, professional, project (--project)
optimux-tui memory edit project               # in your editor
optimux-tui memory set professional < pro.md
optimux-tui memory history --diff             # the active project's (or --project)
optimux-tui memory undo 12                    # a change's number from history
optimux-tui memory update --project my-app    # one paid call; --yes in a script
optimux-tui memory auto on --idle-minutes 15
optimux-tui skills list
optimux-tui skills new release-notes          # in your editor (--stdin to pipe it)
optimux-tui skills edit release-notes
optimux-tui skills mode release-notes auto
optimux-tui skills remove release-notes       # moved aside, not deleted
optimux-tui skills when manual                # off, manual, auto
optimux-tui overview
optimux-tui history chats --project demo      # --archived, --find, --json (ids)
optimux-tui history rename <id> "New title"
optimux-tui history archive <id>              # unarchive <id>
optimux-tui history delete <id>               # asks for the title (--confirm in a script)
optimux-tui history sent --find deploy
optimux-tui history request <session> <key> --whole
optimux-tui history keep 7                    # asks before deleting older prompts
optimux-tui system show
optimux-tui system language it
optimux-tui system detach-wait 45
optimux-tui system signout-others
optimux-tui system password                   # typed hidden
optimux-tui system totp on                    # shows the key, then asks for a code
optimux-tui system rotate-token               # the new one is printed once
optimux-tui system cooldowns --clear-all
optimux-tui studio tree [folder]              # the active project's files, git's letters
optimux-tui studio find app config
optimux-tui studio read src/app.py
optimux-tui studio edit src/app.py            # your editor; never overwrites others' changes
echo "hi" | optimux-tui studio write notes.txt   # --replace for a file that exists
optimux-tui studio new docs/intro.md          # mkdir, mv <from> <to>, rm (to the Trash)
optimux-tui studio changes                    # what isn't committed, what is added
optimux-tui studio add src/app.py             # --all; unadd takes them back
optimux-tui studio diff --staged
optimux-tui studio commit -m "Fix the port"   # --write-message; push: projects push
optimux-tui start                             # the setup steps and the next one
optimux-tui start features --plan ask         # --memory on|off, --skills off|manual|auto
optimux-tui start fine                        # show, hide: the web chat's checklist
optimux-tui guide                             # the headings
optimux-tui guide find sandbox network
optimux-tui guide read the sandbox            # one section (a chapter with its parts)
optimux-tui guide all | less
optimux-tui github show                       # --fresh asks GitHub again
optimux-tui github token                      # typed hidden (--stdin in a script)
optimux-tui github check
optimux-tui github remove
optimux-tui github identity --name "Ada" --email ada@example.com
optimux-tui projects status demo              # git fetch, status, last commits
optimux-tui projects push demo -m "Fix login" # shows what goes, then asks
optimux-tui projects push demo --write-message  # the Simple model writes it
optimux-tui projects push demo --commits-only # leaves the uncommitted files
optimux-tui projects update demo
optimux-tui projects identity demo
optimux-tui projects git-lines demo           # --ssh for the SSH address
optimux-tui backups snapshots list
optimux-tui backups snapshots preview --what workspace --project demo
optimux-tui backups snapshots take --note "before the new routing"  # waits until ready
optimux-tui backups snapshots show <id>       # readme <id>, delete <id>
optimux-tui backups snapshots restore <id> --list
optimux-tui backups snapshots restore <id> --part memory   # asks for the sentence
optimux-tui backups snapshots download <id> --dir ~/safe --key-file ~/keys/k.txt
optimux-tui backups snapshots limit 10        # GB
optimux-tui backups daily list                # now, keep 14, check <name>, delete <name>
optimux-tui backups daily restore <name>
optimux-tui backups daily download <name> --protect recipient --recipient "age1…"
optimux-tui backups status                    # restart, cancel, dismiss
optimux-tui backups upload ~/safe/optimux-snapshot-….tar.gz.age   # asks for its key
```

A download never replaces a file. Without `--key-file` the one-time key is printed
once on the standard output (the rest goes to the standard error), so
`... download <id> > key.txt` keeps only the key. Uploads and downloads go up to 2 GB
from the terminal; larger files: the web page.

Passwords and tokens are always typed hidden, never given as arguments. A command
piped into `head` or `less` ends quietly when the pipe closes.

A key is never a command-line argument (it would stay in the shell's history), and it
is never printed. An `--api-base` that is not this computer or your network needs
`--remote-ok`: your code goes there. A model is saved before its key, so a model the
server refuses leaves no key behind.

### Several chats at once

On a terminal 100 columns wide or more, the **chat list** on the left shows the
project's chats and what each one is doing: **●** a question waits for you, **⠋**
working, **✓** finished while you were in another chat (until you open it). The
terminal's **bell** rings when a question appears, in this chat or another.

Opening another chat while a request runs (a click in the list, Alt+↑/↓, Ctrl+O,
Ctrl+N, Ctrl+P) **leaves that request running** on the server: it goes on for up to
30 minutes without a window (Dashboard → System → "Leaving a request running"), and
opening its chat again gets it back where it is. **Ctrl+D** does the same and quits.
The budget still applies, and a question waits for your answer: nothing runs without it.

**The mouse:** the key bar at the bottom, the project and chat names at the top, the
buttons of question cards, list rows and the hints in the chat ("thinking … Ctrl+T",
a plan's "Ctrl+R run") are clickable; a click outside a list closes it, and the wheel
scrolls. In the Dashboard: a click chooses a row and a click on the chosen row opens it
(as Enter); in the Studio's tree and the Guide's contents one click opens. Tabs (Usage's
periods, Backups, Memory…) and the cyan key line under each section are clickable too. While optimux-tui has the mouse, **Shift+drag** selects text to copy (in
almost every terminal). `optimux-tui --no-mouse` leaves the mouse to the terminal: a
plain drag selects text, and nothing is clickable.

**Questions** (a command to run, a file change, out of steps) open a card: `y` yes,
`n` no (then an optional reason for the model), `a` the "always" choice when there is
one, Esc stops the request. **Use another model?** (a model ran out of credit, isn't
answering, or is stuck) says what likely went wrong: `y` goes on with the other model
for this request, `m` opens Dashboard → Models (fix its key, test it, add another) and
`r` Routing; the question waits, and Ctrl+G brings you back to it. Ctrl+G opens the
Dashboard while any question waits. The keys work after a short moment (0.7 s), so a
word you were typing can't answer the card. Enter never answers. A Push or Update window the model opens
during a request still needs the web page; your own push and update are in
Dashboard → Projects.

**The line:** the client pings every 5 seconds and reconnects by itself (1, 2, 4… up
to 15 s; Ctrl+L tries at once). A request runs on while no client is there for
2 minutes; open the client again to get it back.

On the computer where Optimux runs, `optimux tui ...` does the same with the right
address already filled in.

- **Which server:** `--server`, else `$OPTIMUX_URL`, else the last one you signed
  in to, else `127.0.0.1:8000`.
- **Plain http** is only allowed to this computer (`127.0.0.1`, `localhost`). For a
  server, use https or an SSH tunnel (`optimux open` on the server prints it).
- **Scripts:** `--password-stdin` reads the password, and the code on the next line.
  `OPTIMUX_TOKEN` signs in with the access token instead of a session.
- **Exit codes:** `0` fine, `1` error, `2` wrong arguments, `3` not signed in.

## Where the sign-in is kept

`~/.config/optimux-tui/sessions.json` (or `$XDG_CONFIG_HOME/optimux-tui/`, or
`$OPTIMUX_TUI_HOME`): readable only by you (`600`, folder `700`). It is not in
`~/.optimux`, so the server's backups never carry it. A session lasts 30 days.
It shows in Dashboard → System → Sign-in as `optimux-tui`, and you can end it there.

## Develop

```bash
cargo fmt --check && cargo clippy --all-targets -- -D warnings && cargo test
```

CI runs the same (the `tui` job), also on Rust 1.85. Linux and macOS only for now.
Mouse scrolling is off on purpose: it would stop the terminal's own text selection.
