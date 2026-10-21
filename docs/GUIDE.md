# Optimux Guide

## Welcome

Optimux is a coding assistant that runs on your own machine and works on your own projects. You chat with it in the browser; it can read your files, run commands in a terminal (after you approve them), edit code, and remember what it learns about you and each project.

What makes it different is **how it spends money**. Every answer from a language model is billed by the number of tokens (pieces of words) it reads and writes, and models differ a lot in price. Optimux sends each request to the cheapest model that can handle it, keeps prompts small, and shows you exactly what everything costs, so you decide the balance between **cost, speed and reliability**.

The ideas in one minute:

- **Two tiers.** A cheap, fast *Simple* model handles most work. A stronger, more expensive *Complex* model takes over for hard tasks, or when the Simple model gets stuck.
- **The Conductor** is a tiny, cheap call that looks at each new message and decides which tier it needs.
- **The Workspace and its projects.** The Workspace is one folder on your disk; every folder inside it is a project. Commands run inside the active project, and each project has its own memory, chats, costs and budget.
- **Memory** is a few short Markdown files sent with every request so the model knows you and your project.
- **Skills** are reusable instructions you can run with a `/command`.
- **Everything is measured:** every model call is recorded with its tokens, cost and time, and nothing is ever lost when the server restarts.

> **New here?** Read *Getting started* first, then *The chat screen*. *How to spend less* is the chapter to come back to once you have used Optimux for a few days.

## Getting started

### What you need

- Linux or macOS with **Python 3.10 or newer**.
- An API key for at least one model provider: Google (Gemini), Anthropic (Claude), OpenAI, or a local model through Ollama.
- `git`, and optionally the GitHub CLI `gh` if you want Optimux to create GitHub repositories for new projects.

### Install

One command installs everything, on your own computer or on a server:

```bash
curl -fsSL https://raw.githubusercontent.com/optiware-ai/optimux/main/install.sh | bash
```

It asks **where** you're installing (this computer, or a server reachable from the internet), checks your system, installs what's missing (Python's venv, git, bubblewrap for the sandbox), downloads Optimux into its own virtual environment, and then offers a short setup:

- your **sign-in password** (so you don't need the access token);
- your **Workspace folder** (the folder that holds your projects);
- your first **AI provider**: paste one API key, and Optimux sets up sensible models and routing (and can test the key with one tiny call);
- **GitHub** (optional): it installs the GitHub CLI (`gh`) and checks a token with GitHub, so new projects can get a GitHub repository (see *GitHub*);
- the **sandbox**, and whether Optimux starts by itself when you log in.

Every question can be skipped: each one says where to change it later in the web interface. Passwords and keys are typed hidden, go to Optimux on standard input, and never appear on screen, in arguments or in the install log.

On your own computer, the program goes to `~/.local/share/optimux`, your data to `~/.optimux`, and Optimux listens on `127.0.0.1` only. On a server, see *Running on a server*.

> **Prefer to read it first?** `curl -fsSL https://raw.githubusercontent.com/optiware-ai/optimux/main/install.sh -o install.sh`, read it, then `bash install.sh`. It's plain bash, and nothing runs until the file's last line, so a broken download can't run half of it.

**Options for scripts:** `--local` or `--server`, `--domain NAME` or `--no-domain`, `--yes` (accept every default), `--workspace PATH`, `--port N`, `--no-start`, `--update`, `--uninstall`. `bash install.sh --help` lists them.

### The optimux command

The installer adds an `optimux` command (on a server, run it with `sudo`):

> **"optimux: command not found" right after installing?** On your own computer the command is in `~/.local/bin`, which a fresh account doesn't have in its PATH until the next login. The installer adds it for new terminals (after asking); in the terminal you installed from, run `export PATH="$HOME/.local/bin:$PATH"` once, or open a new terminal. The final summary of the installer says so when it applies.

| Command | What it does |
|---|---|
| `optimux start`, `stop`, `restart` | Run Optimux, or stop it |
| `optimux status` | Whether it runs and answers, its address, version and folders |
| `optimux logs` | Follow the server's log (Ctrl+C to leave) |
| `optimux open` | The address, opened in your browser when there is one (on a server without a domain: the SSH tunnel command to run on your computer) |
| `optimux update` | Download the latest version, update its packages, restart |
| `optimux version` | The installed version and commit, like `v0.4.0 (5e0673b)`; the same as Dashboard → System |
| `optimux password` | Choose a new password (signs every browser out) |
| `optimux reset-password` | Forget the password and two-step codes |
| `optimux token` | Show the access token |
| `optimux github` | Install the GitHub CLI and add (or replace) the GitHub token |
| `optimux uninstall` | Remove Optimux; asks separately before deleting your data |

Running the installer again offers the same: update, set up again, or uninstall.

### Installing by hand

If you'd rather set it up yourself (for development, for example):

```bash
git clone https://github.com/optiware-ai/optimux.git
cd optimux
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn optimux_backend.main:app --host 127.0.0.1 --port 8000
```

Then open **http://127.0.0.1:8000** in your browser.

> **Keep `--host 127.0.0.1`.** It makes Optimux reachable only from your own machine. To reach it from elsewhere, use an SSH tunnel, or follow *Running on a server* (HTTPS, server mode, a hardened service). Optimux can run commands on your computer, so treat access to it like access to your terminal.

### Signing in

On its first start, the server creates an **access token** and prints it in the terminal:

```
[Optimux] Generated access token (saved to ~/.optimux/secrets.env as OPTIMUX_TOKEN):
    1kz...
```

The first time you open Optimux, the sign-in page asks for this token **once**, to prove you own the server, and lets you choose a **password** (12 characters or more; a few random words work well). From then on you sign in with the password.

- The browser stays signed in for up to **30 days** (14 days without use). The sign-in lives in a cookie that page scripts can't read.
- **Sign out**, see which browsers are signed in, sign the others out, change the password and turn on **two-step codes** in **Dashboard → System → Sign-in**.
- When another browser signs in, every open Optimux page shows a **New sign-in** notice (browser, address, time) until you dismiss it. If it wasn't you, **Not me: sign out the others**, then change your password. The list of signed-in browsers updates by itself.
- After **5 wrong attempts** from one address, that address has to wait 15 minutes. After 30 wrong passwords from anywhere in 15 minutes, or 10 wrong two-step codes in an hour, signing in with a password pauses for everyone; browsers already signed in and the access token keep working, and a restart clears it.
- **Forgot the password?** On the server, delete `~/.optimux/auth.json` and reload the page: you can set a new one with the access token. This also turns off two-step codes and signs out every browser. To move two-step codes to a new phone, turn them off (password and a current code), then set them up again.

The token stays useful for scripts: they can call the API with the header `Authorization: Bearer <token>`. You can always find it in `~/.optimux/secrets.env` and replace it in **Dashboard → System**.

### First-time setup (5 minutes)

1. **Add your models.** Open **Dashboard → Models**, add a model (for example `gemini/gemini-3.6-flash`) and paste its API key. Click **Test**: a tiny call (a fraction of a cent) checks that the model and key work. See *Models* for details.
2. **Choose who does what.** In **Dashboard → Routing**, pick the Conductor, the Simple model and the Complex model. A good start: a fast, cheap model for the Conductor and Simple, a strong one for Complex.
3. **Set your Workspace folder.** In **Dashboard → Scope**, set the folder that holds your projects, for example `/home/you/git`. Every folder inside it becomes a project. Optimux must be able to write in it, or the folder is refused. On a server, the service may only write inside `/var/lib/optimux`, so use a folder there that the `optimux` user owns: `sudo install -d -m 750 -o optimux -g optimux /var/lib/optimux/projects`. (Another place, such as a second disk, works after allowing it with a drop-in: `sudo systemctl edit optimux`, then `[Service]` and `ReadWritePaths=/srv/work`; a drop-in survives `optimux update`.) Changing the folder keeps your chats, costs and memory, which belong to project names; if the active project isn't in the new folder, the Workspace itself becomes active.
4. **Turn on the sandbox** (recommended): install bubblewrap (`sudo apt install bubblewrap` on Debian/Ubuntu), then tick **Dashboard → Execution → Sandbox**. Commands can then only change the project they work in. See *The sandbox*.
5. **Pick a project** in the drop-down next to the logo, or create one with the **+** next to it (or by asking: *"create a new python project called notes-api"*).
6. **Optional but wise:** set a monthly **budget** for the project in **Dashboard → Budgets**.

You're ready. Type a request and press **Enter**.

### Your first request

Try something small to see how it works:

> *what does this project do? look at the files*

You will see, in order: which tier answered (**Routed to: …**), any commands the model wants to run (each one waits for your **Approve**), the answer, and a line with the calls, tokens, cost and time of the reply.

## The chat screen

### The header

From left to right:

- **The dot next to the logo** shows the connection to the server: green connected, yellow connecting, red disconnected. Optimux reconnects by itself and reopens your chat.
- **The logo and the name** take you back to your **most recent chat**, in whichever project it is (the project menu follows), or to the **Workspace** when there is no chat yet. In the Dashboard, the logo at the top of the sidebar does the same and closes the Dashboard. Chats of archived or deleted projects are skipped.
- **The project menu** chooses the active project, or **Workspace** (no project). Switching opens that project's latest chat.
- **+** next to it creates a **new project** with a form (no model involved, so it costs nothing; see *Creating a project*).
- **The prompt bar.** When you scroll down a long reply, the prompt it answers stays pinned above the chat on one line. **More ▾** shows it in full, **↑** scrolls up to it.
- **The green trail** under a reply means Optimux is waiting for a model: before the first output and between steps, while the model thinks about the next one.
- **The Plan chip**, next to *New chat* under the message box, shows the plan mode (*Plan: Off*, *Ask first* or *Automatic*; see **Plan big tasks**). Click it to switch to the next mode; it's the same setting as in Dashboard → Routing. It's dimmed when you picked a model by hand, because then nothing is planned.
- **The cost button** shows this month's cost for the active project, or *spent / budget* when it has a budget (yellow from 80%, red from 100%). It moves with every model call and counts like a mechanical counter: the last digit turns one step at a time, and the digit to its left turns when the last one passes 9. Hover it for today, this month, all time and this browser session; click it to open Budgets or the Overview.
- **The memory pill** appears briefly while project memory is being updated in the background ("Updating memory…", then "✓ Memory updated" or "Memory: nothing new").
- **History** opens the list of past chats; **Dashboard** opens the settings and reports.

Under the header, the **chat title** (the first message of the chat) has a ✎ to rename it.

### Writing a request

- **Enter** sends; **Shift+Enter** adds a new line.
- Type **`/`** to see the commands and your skills. Arrow keys move, **Enter** or **Tab** picks one.
- The selector under the box says **⚡ Auto-Router** by default: the Conductor chooses the model. Pick a specific model to force it for your requests (it then stays selected until you change it back).
- **+ New chat** starts a fresh conversation. The model forgets the previous one, which also makes requests cheaper.
- While a request runs, the send button becomes a red **Stop** (or press **Esc**).

### Commands you can type

| Command | What it does |
|---|---|
| `/new` | Start a new chat (same as the New chat button). |
| `/simple` | Use the Simple tier for the rest of this chat (the Conductor stops switching). Add a request after it to send it right away: `/simple explain this error`. |
| `/complex` | Same, with the Complex tier. Useful when you know a task is hard. |
| `/plan <task>` | Split this task into steps for the cheaper model (see *Plan big tasks*), even if the Conductor wouldn't. |
| `/noplan <task>` | Do this task the usual way, without a plan. |
| `/<skill>` | Run a skill, e.g. `/run-tests` or `/run-tests only the unit tests`. |
| `/skill <name>` | Same as `/<name>`. |

### Anatomy of a reply

A reply can contain, from top to bottom:

- **Routing notes** in blue: *Routed to: gemini/gemini-3.6-flash (simple)*, *Skill: run-tests*, *Escalated to …*.
- **Thinking**, collapsed: the model's reasoning, when the provider shares it. Click **▸ Thinking** to read it, **Copy** to copy it. Some providers (Gemini) share only a summary or nothing; then you see *"Thinking: N tokens (the model didn't share the text)"* so you still know it was billed.
- **Command boxes**: the command, its **exit** code (green 0 = success, red = failure) and its output. **View** opens the command and output in a side panel with syntax highlighting, line numbers, a language picker, **Wrap** for long lines and **Copy**.
- **The answer**, in Markdown. Code blocks have a **View** button in their corner too.
- **The usage line**: which models were called and how often, tokens (with thinking), cost and time. While the reply is still running, a pulsing **So far:** line shows the cost growing after every model call. Commands themselves are free; only model calls cost money.
- **Buttons**: **Copy** (this question and answer as Markdown), **Save as skill** (turn this chat into a reusable skill; costs one model call), **Escalate ↑** (re-run your last request on the Complex model; shown after a Simple reply).

### Approving commands

Before a command runs you see **Approval Required** with the full command:

- **Approve** runs it once. **Reject** refuses it; the model is told and continues without it.
- **Always Approve** runs it and turns on auto-approve for all future commands (you can turn it off in **Dashboard → Execution**).
- A red **Outside the project** box appears when the command touches paths outside the active project. These are never auto-approved, and *Always Approve* is hidden.
- Creating a project shows a **form** instead (see *Creating a project*); creating and archiving always wait for you, even with auto-approve on.
- A **file change** (marked **✎ project file**) shows the file and its diff: removed lines in red, added lines in green. Nothing is written before you press **Approve**. See *Writing files*.

### When a reply stops early

If a reply was stopped, hit a limit or failed, the last reply shows why, with buttons:

- **Continue ↻** lets the model carry on from where it stopped (after Stop, a step limit, a budget pause or a repeated action).
- **Retry ⟳** sends your original request again.
- **Raise budget** opens Budgets when the project's budget blocked the request.

### Copying and exporting

- **Copy** under a reply copies that question and answer as Markdown.
- **Copy session as Markdown** (below the last message) copies the whole chat, with commands, outputs, exit codes, thinking and costs.

### History

**History** in the header opens a drawer with your chats: this project, all projects, or archived ones, with a title search. Each chat shows its project, date, number of turns and cost, and can be **opened** (to continue it), **renamed**, **archived** or **deleted** (you type the title to confirm; its cost records are kept). **Dashboard → History** is the full-page version, with filters and sorting.

A chat whose project was **archived** or **deleted** opens **read only**: you can read it, but not continue it, because its folder is gone and commands couldn't run there. A bar above the input says why, with a button back to your current project. To continue the chat, restore the project first (**Dashboard → Projects → Archived projects**). The project selector is locked while a reply is running or waiting for your approval, so the chat on screen always belongs to the project shown.

Chats are saved as you go and **survive server restarts**. Opening a chat restores both what you see and what the model remembers of it.

## The Workspace and projects

### Two levels

Optimux works on two levels, and the project menu next to the logo switches between them:

```
/home/you/git/            ← the Workspace folder (set in Dashboard → Scope)
├── notes-api/            ← a project
├── website/              ← a project
└── .archive/             ← archived projects
    └── old-experiment/
```

- **The Workspace** is the folder that holds all your projects. Choosing **Workspace** in the menu means *no project is selected*: you are talking to Optimux about your projects as a whole.
- **A project** is one folder inside the Workspace. Choosing it in the menu means you are working *on* that project: its files, its commands, its memory.

### What changes between them

| | Workspace (no project selected) | A project |
|---|---|---|
| What you do there | Manage projects: list, create, switch, archive | Work on the code: read, edit, run, test |
| Commands start in | The Workspace folder | The project's folder |
| Commands may change (with the sandbox on) | The whole Workspace folder | Only that project's folder |
| Memory sent with requests | Personal and professional | Personal, professional **and the project's memory** |
| Automatic memory updates | None (there is no project memory) | After each finished chat |
| Project skills | Not available | `<project>/.optimux-skills/` |
| Chats, costs, budget | Counted under *Workspace* | Counted for that project |
| Project actions | List, create, switch, archive | List, create and switch |

**Rule of thumb:** select a project as soon as you work on one. You get its memory, its skills and its budget, and commands are confined to it. Use the Workspace to organize projects, not to edit code.

### Working in a project

When a project is active:

- commands start in its folder, and commands reaching outside it are flagged before you approve them;
- its **project memory** is sent with every request and kept up to date automatically;
- its chats, costs and budget are tracked separately;
- its own skills (in `<project>/.optimux-skills/`) are available.

Nothing Optimux keeps (chats, memory, costs, keys) is written inside your project folders, except project skills you create there on purpose. Your repositories stay clean.

### Creating a project

There are two ways, and both end in the same form:

- **The + button** next to the project menu (or **+ New project** in Dashboard → Projects). No model is involved, so it costs nothing.
- **Ask in the chat**, for example *"create a private Python project called scraper on GitHub"*. The model fills the form in with what you said and shows it in the chat instead of an approval box; it doesn't ask you questions one by one, so this costs one model call.

The form has:

| Field | What it does |
|---|---|
| **Name** | The folder (and GitHub repository) name: letters, digits, `.`, `_`, `-`. The path is shown under it; a name that exists already is flagged as you type. |
| **Starter files** | *Basic*: README and `.gitignore`. *Python*: also `src/` and `tests/`. |
| **Git repository** | `git init`, a commit template and a first commit. |
| **Also on GitHub** | Creates the repository with `gh` and pushes the first commit. **Private** or **Public**. Greyed out, with the reason and a *Set up GitHub* link, until GitHub is set up. |
| **Description** | Optional (up to 350 characters): goes in the README and on GitHub. Leave it empty for now if you like. |
| **Open it when it's ready** | From the **+** dialog: switches to the new project. In the chat (*Offer to open it when it's ready*): you stay where you are and can finish reading the reply; a small **Open <name>** button appears above the chat box, and the chat moves to the new project only when you click it (× dismisses it). The conversation you were in stays in History under its own project. |

Nothing is created until you press **Create project**, and the server checks every value again. A notification slides in when the project is ready (with a link to the GitHub repository); if a step failed (for example the push), the folder is kept and the notification says so. Creating a project always waits for you, even with auto-approve on.

### Cloning a project from GitHub

**+ New project → From GitHub** (or **Clone from GitHub** on the Projects page) lists the repositories your GitHub sign-in can see (Dashboard → GitHub): yours, those you collaborate on, and your organizations', newest first, with search. Repositories already in your Workspace are marked *in your Workspace as …*; you can still clone them again, as a separate copy (for example to try two approaches side by side): the folder name then starts as *name-2*, *name-3*…, and the copy comes from GitHub, so changes you haven't pushed from the first copy aren't in it. A copy pushes to **its own branch**, named after its folder (see **Pushing and cloning by hand**), so it never changes the original's `main`.

Pick one, check the folder name, and press **Clone**. The clone runs on the Optimux machine in the background, with its progress shown and a **Cancel** button; it keeps going if you close the window, and a notification says when it's done. *Open it when it's ready* switches to it.

While it runs, the clone sits in a hidden `.clone-…` folder in the Workspace and only takes its name when it's complete, so a half-done clone never shows up as a project. A failed or cancelled clone leaves nothing behind, and it never touches anything else: if a folder with the same name appears in the meantime, that folder wins and the clone is discarded.

- It's a one-time import: after that, pulling and pushing are yours, as with any repository.
- The token is used only for that command (git asks `gh` for it), so it isn't written into the project's `.git/config`; the origin is the plain `https://github.com/owner/repo.git` address.
- Submodules and Git LFS files aren't downloaded, and the repository's hooks don't run. Fetch them yourself (`git submodule update --init`, `git lfs pull`) if the project needs them.
- A fine-grained token sees only the account or organization it was made for; give it access to more in GitHub if some repositories are missing.

No model is involved, so cloning costs nothing.

### Changes made outside Optimux

While Optimux is open in a browser, it notices folders added, removed or renamed in the Workspace (and in `.archive`) outside Optimux: made by hand, cloned in a terminal, or by a command. The project menu and the Projects page update by themselves within a few seconds.

### Managing projects from the chat

The model has dedicated project actions; it never uses shell commands (`mkdir`, `git init`, `gh repo create`) for these:

- **list** the projects (with git status, GitHub remote and last change);
- **create** a project, through the form above, from the Workspace or from inside another project;
- **switch** to a project (doesn't change anything on disk, so it doesn't ask);
- **archive** a project, from the Workspace: its folder moves to `<Workspace folder>/.archive/`; its chats, costs and memory are kept. Archiving always asks for your approval.

Projects are **never deleted from the chat**: the model can archive, but it can't restore or delete. Those are buttons in Dashboard → Projects (see below), so a manipulated model can never destroy a project.

### GitHub

To create GitHub repositories, Optimux needs two things **on the machine it runs on**: the GitHub CLI (`gh`) and a sign-in. Set both up in **Dashboard → GitHub**, with `optimux github`, or while installing.

1. Create a token at github.com → Settings → Developer settings → **Fine-grained tokens** → *Generate new token*: Repository access **All repositories**; permissions **Administration: Read and write** (creates repositories) and **Contents: Read and write** (pushes the first commit). A classic token with the `repo` scope works too.
2. Paste it in **Dashboard → GitHub** and press **Check and save**. Optimux asks GitHub whose token it is before saving it, and warns if a classic token lacks the `repo` scope. (With `optimux github` or `admin remove-github` on the command line, restart Optimux afterwards so a running server picks the change up.)

Instead of a token, Optimux also uses `gh auth login` if it was done by the user Optimux runs as (common on your own computer).

The token is kept in `secrets.env` with the other keys, and only the two steps that talk to GitHub use it (`gh repo create`, and the push, which goes over HTTPS through `gh` as git's credential helper, so the token is never written to a git config or shown on a command line). **Commands the model runs don't get it**, and with the **sandbox** on they can't read `secrets.env` either, so the model can't push or change your GitHub account. With the sandbox off, an approved command runs with Optimux's permissions and could read it (or use `gh`'s own sign-in): review commands, or turn the sandbox on. Pushing later is up to you (see **Pushing and cloning by hand** below).

**Who commits.** The first commit (and commits the model makes, when you approve them) use git's own name and email. Set a different **name and email** in Dashboard → GitHub (saved on their own, with their own **Save**). On a server the Optimux user usually has none, so saving a token fills them in with your GitHub account and its private `…@users.noreply.github.com` address when git has nothing set.

### Pushing and cloning by hand

The model can commit (when you approve the command) but **can't push**: its commands don't get the GitHub token, and on a server the sandbox has no network either. A push it tries fails with *Could not resolve host* or asks for a username. Push yourself, from a terminal on the machine Optimux runs on.

**The quick way:** on the project's card in **Dashboard → Projects**, press **Push manually** or **Pull manually** and **Copy** the one-line command it shows. It's made for that project: its folder, its GitHub address, the user Optimux runs as (through `sudo` on a server) and the saved token for that one command. *Push manually* commits every change first as *Update from Optimux* (edit the message before running it if you like), with the name and email from **Dashboard → GitHub** (on a server, git itself usually has none), then pushes the current branch. *Pull manually* is for another computer: run it in the folder where you keep your projects; it clones the project the first time and pulls after that. Pick **SSH** to clone with your SSH key, and put your SSH host alias (from `~/.ssh/config`) in the *host* box if you use one; the browser remembers it. The address never contains a token, even if the project's origin does. **A copy** (a project whose folder isn't named like its repository, such as `t3-off` cloned from `makerhub2`) pushes to a branch named after its folder (`HEAD:t3-off`), because the original and every other copy share the same repository: pushing them all to `main` would replace each other's work, and git would refuse the second push. Its *Pull manually* clones that branch into a folder with the copy's name. To push a copy to `main` on purpose, change `HEAD:t3-off` to `HEAD` at the end of the command. The sections below explain the same commands step by step.

**On your own computer**, where Optimux runs as you and `gh auth login` is usually done, a plain push works:

```bash
cd ~/git/myproject          # the project folder (Dashboard → Projects shows the path)
git status                  # anything not committed yet?
git add -A && git commit -m "Describe the change"
git push origin HEAD
```

If git asks for a username, use the token Optimux keeps, for this one command only:

```bash
set -a; . ~/.optimux/secrets.env; set +a
GH_TOKEN=$OPTIMUX_GITHUB_TOKEN git -c credential.helper= \
  -c 'credential.helper=!gh auth git-credential' push origin HEAD
```

**On a server**, the project belongs to the `optimux` user and its data is in `/var/lib/optimux/.optimux`. Run git as that user, with the token for this one command:

```bash
P=/var/lib/optimux/workspace/myproject
S=/var/lib/optimux/.optimux/secrets.env
sudo -u optimux git -C $P status
sudo -u optimux git -C $P add -A
sudo -u optimux git -C $P commit -m "Describe the change"
sudo -u optimux bash -c "set -a; . $S; set +a; GH_TOKEN=\$OPTIMUX_GITHUB_TOKEN \
  git -C $P -c credential.helper= -c 'credential.helper=!gh auth git-credential' push origin HEAD"
```

- Don't type the token at git's *Username* prompt and don't save it with `git config credential.helper store`: a stored login would let every command the model runs push too. Press Ctrl+C at the prompt instead.
- `gh` must be installed for all users (`gh --version` as the optimux user), and the token needs **Contents: Read and write** on the repository.
- If `secrets.env` isn't there, find it with `sudo find /var/lib /opt /home -name secrets.env -path '*optimux*'`.
- A project without a GitHub repository has no `origin` (`git remote -v` prints nothing). Create the repository on GitHub, then `git remote add origin https://github.com/<owner>/<name>.git` and push as above.

**Cloning it on another computer.** Once it's on GitHub, clone it where you want to work or look at it:

```bash
git clone https://github.com/<owner>/<name>.git                # public, or private after gh auth login
git clone git@github.com:<owner>/<name>.git                    # with an SSH key added to GitHub
```

A private repository needs a sign-in on that computer: `gh auth login` (then the HTTPS address works), or an SSH key: `ssh-keygen -t ed25519`, add `~/.ssh/id_ed25519.pub` in github.com → Settings → **SSH and GPG keys**, and test with `ssh -T git@github.com`. With several GitHub accounts, give each key a host alias in `~/.ssh/config` and use it in the address:

```
Host github-work
    HostName github.com
    User git
    IdentityFile ~/.ssh/id_ed25519_work
    IdentitiesOnly yes
```

```bash
git clone git@github-work:<owner>/<name>.git
```

Later changes come over with `git pull` in that folder. To bring a repository **into** Optimux, use **Clone from GitHub** (above) instead: it needs no terminal.

### Dashboard → Projects

One card per project with this month's and all-time cost, budget and number of chats, plus **Make active**, **Archive** (refused while a request runs in that project, in any tab; every open page leaves an archived project), and links to its chats, memory and budget. Cards are marked *archived*, *deleted* (folder gone, records kept) or *folder missing* (the folder disappeared outside Optimux; its history is kept). **+ New project** opens the New project form.

The page starts with the **Workspace folder**'s full path (with a copy button and a link to Scope); hovering the project menu in the header shows the active project's path too, and System → Files lists it.

**Archived projects**, under the cards, lists the folders in `<Workspace folder>/.archive/` (a hidden folder: `ls -a` shows it), each named `<project>-<date>-<time>`; the list shows the real path, and archiving tells you where the folder went:

- **Restore** moves the folder back into the Workspace (refused if a project with that name exists).
- **Delete permanently…** removes the folder from disk. Only archived projects can be deleted, so deleting always takes two deliberate steps. The dialog:
  - asks you to type **`yes I want to delete <name>`** (pasting is blocked; the words in any case, the name exactly);
  - offers **Keep its chats, costs and memory** (the default) or **Delete them too** (it shows how many chats and model calls, and what they cost);
  - never touches the **GitHub repository**. For a GitHub project it first asks GitHub whether the repository still exists (one request, no model): if it does, it links to its settings page, where you can delete it yourself; if it's gone, it says so. Without a GitHub sign-in (Dashboard → GitHub), GitHub hides private repositories, so the dialog can only say "deleted, or private" and links to GitHub to check.

A deleted project whose records you kept still appears in **History** and **Recent chats** with a red **deleted** mark. In **Usage** (By project, Memory updates), **Budgets** and **Projects**, deleted projects are gathered in one closed line, *Deleted projects (N)*, carrying their combined numbers so the tables still add up; click it to list them (the choice is remembered in this browser). Its budget is removed, since nothing can spend there any more.

An **archived** project's budget is **paused**: kept, so restoring the project brings it back, but listed apart under *Archived projects (N)* in Budgets and left out of the Overview, since nothing can spend in an archived project. Archived projects appear only in the Archived projects list on the Projects page, not as cards. Creating a new project with the same name makes those records belong to the new project. *Delete them too* removes its chats, usage rows, memory and memory history, and budget, so the Usage totals go down; it's refused while another folder (archived or in the Workspace) has the same name, since they share these records.

## How routing works

### The two tiers

| | Simple | Complex |
|---|---|---|
| Used for | Questions, explanations, single commands, small edits | Multi-step work, changes across files, refactoring, design, investigation |
| Typical model | Fast and cheap (e.g. Gemini Flash) | Strong and pricier (e.g. Claude Sonnet) |
| Step limit (default) | 8 model calls per request | 25 model calls per request |
| At the step limit | Escalates to Complex | Pauses and offers **Continue** |

Both tiers have the same tools: they can run commands, edit files and save memory.

### The Conductor

For every new message, the Conductor (one short, cheap call) classifies the request as simple or complex, and, if you have **Auto** skills, may pick one. It reads only your message, not your memory or the chat, so it stays cheap.

- It can move a chat **up** to Complex at any time.
- It moves a chat back **down** to Simple only after the previous task finished normally, so an unfinished task is never handed to a weaker model halfway.
- **Conductor: Off** (in Routing) skips this call entirely: chats start on Simple and only escalation changes the tier. Cheapest, but hard tasks start on the weaker model.
- **When the Conductor can't decide** (its call fails, it is in a cooldown, or its answer isn't the JSON it was asked for), the request starts on Simple and the chat says so: *The Conductor (…) couldn't decide: … This request starts on the simple tier.* If you see this on every message, the Conductor model doesn't work with these settings: test it in **Models**, or pick another one.
- To keep it cheap, Optimux asks a **Gemini** Conductor for *minimal* thinking. Models that refuse an extra setting like this (Gemma, for example, served through the Gemini API) are asked again once without it.

### Escalation and fallback

- **Escalation (Simple → Complex)** happens automatically when the Simple model reaches its step limit or keeps repeating the same action. The Complex model receives the whole conversation so far and continues. You can also click **Escalate ↑** yourself.
- **Fallback** handles provider trouble: if a model fails (outage, quota), Optimux retries once, then switches to the other tier for that request. A model that keeps failing goes into a **cooldown** and is skipped until it recovers (see *Models*).

### Pinning and overrides

- `/simple` or `/complex` **pins** the tier for the rest of the chat; the Conductor stops switching. **New chat** unpins.
- Choosing a specific model in the selector under the chat box **overrides** routing for your requests: that model answers, with the Complex step limit, and no automatic switching.

### Steps and turns

A **turn** is one request and its reply. A **step** is one model call inside it: the model calls a command, reads the output, decides the next command, and so on. Each step resends the conversation, so long turns cost more per step. Step limits (in **Dashboard → Execution**) cap how far one request can go before Optimux escalates or asks you.

### Plan big tasks

*New in v0.4, off by default.* We measured it on six everyday tasks (a landing page, a small API, a theme switch, a small fix, a command-line tool with tests, CI setup), each done once with plans and once without. Plans never gave a better result, and cost about 18% more in total. Splitting closely connected work between models lost details (tests written by one model didn't match the code written by another), and a step whose tool couldn't run stopped the whole plan. So the Conductor doesn't offer plans unless you turn it on. Try `/plan` for a large task with clearly separate, routine parts.

A big task usually mixes hard thinking with routine work: reading files, writing code to a clear spec, running tests. Without a plan, the Complex model pays for all of it. With **Plan big tasks** (in **Dashboard → Routing**), the Complex model first writes a short plan of 2 to 8 steps, and each step runs on the tier it needs: routine steps on Simple, only the hard ones on Complex.

- **Off** (the default): never plan. `/plan <task>` still plans one task.
- **Ask first**: show each plan and wait until you press **Run** (or **Cancel**).
- **Automatic**: show each plan and start it at once; you can still stop it.

The Conductor decides which tasks are worth a plan: a request to do work now, in separate phases, with routine parts. Nothing is planned for creating, switching or archiving a project (Optimux has its own tools for that), or for a request too vague to act on without asking you first; the planner itself can also decline ("Not planned: …"). Nothing is planned either when you picked a model by hand, when a skill runs, when no project is active, or when Simple and Complex are the same model or one of them is unavailable. `/plan <task>` plans anyway (it says why when it can't), and `/noplan <task>` never plans.

Planning costs one Complex call (two if its first answer can't be used); it is shown in Usage with the role **planner**. The planner gets your request, the project memory and the list of the project's files (up to 300 paths). It doesn't open files or run anything. If it can't make a usable plan, the task is done the usual way, and the chat says so. A new request replaces a plan that hasn't started.

The chat shows the whole plan as a card: for each step, what it will do (**Do**), how it knows it is finished (**Done when**) and the files it creates or changes (a step isn't accepted until they exist), then the final **Check**. Read the steps before you run them: a cheap model will work from these instructions alone. When the planner model shares its thinking (Claude does; Gemini keeps it hidden), you can open it above the plan. For risky changes, take a Workspace snapshot first (**Dashboard → Backups**).

Before you run it, the card shows:

- **An estimate**: a cost range for the steps (and the check), from the average cost of your recent calls on each tier: 2 to 4 calls for a Simple step, 1 to 3 for a Complex step and for the check. Steps start from a small context, so the real cost is usually lower. With no priced call on a tier yet, it says so instead of guessing. A paused plan shows the estimate for what is left.
- **The planning cost**: what the planner's call cost.
- **Edit**: change a step's tier (**Make it simple** / **Make it complex**) or **Remove** a step. Each change is saved at once. A plan keeps at least 2 steps and one Simple step; for anything else, **Cancel** and ask again. A removed step's number is used by the next step, but later steps that mention it keep their text, so read them again.

**How a plan runs.** The steps run one after another, each on its own tier and in a **fresh, small context**: the usual instructions, the goal, the step itself, and what the earlier steps handed over, never their whole conversation. That is where the savings come from.

- A step ends by reporting a short **result** (what it did, the files it changed) for the next steps. A step that decides something longer the next steps must follow, such as a design or a list of test cases, also leaves **notes**; the card shows them under the step. Results and notes are kept in Optimux's database (in `~/.optimux`), never in your project.
- Every command still needs your approval and runs in the sandbox when it is on; the budget is checked before every model call.
- The card updates live: each step's status, model, number of calls and cost, and the plan's cost so far. In the reply below it, each step has a heading with what it did (commands, output, text) under it; a finished step folds, and a click opens it again.
- At the end, the plan's **check** runs as a last Simple step (for example, running the tests) and writes the reply. It ends with a verdict shown on the card: **✓ Passed**, **✗ Failed** (the card's status says *done · check failed*), or **? Not verified** (*done · not verified*) when the check couldn't run, for example because a tool or package is missing, or when the model gave no verdict. Steps are done either way; the verdict tells you whether to trust them. A plan without a check gets a summary of the step results, with no model call.
- The chat history keeps a short record of the plan (each step's tier, model and result) and the reply, so a follow-up question knows what happened without paying for every step again.

**When a step has trouble:**

| What happens | What Optimux does |
| --- | --- |
| A step says it's done, but a file it was planned to create doesn't exist anywhere in the project | Not accepted: the model is told which files are missing and goes on; if it keeps failing, the usual escalation follows |
| A step answers in text instead of doing the work ("Shall I write the file?") | It is told the plan is approved and to do the step; after two such answers, it counts as stuck |
| A Simple step reaches its step limit or repeats the same call | That step only continues on Complex (escalation); the next steps keep their tier, and the chat doesn't move to Complex |
| It is still stuck on Complex | The plan pauses: **Try step N again** (on Complex), **Skip step N**, or **Stop the plan** |
| A step says it can't be done (blocked) | The plan pauses with its reason, with the same three buttons |
| A model fails (outage, quota) | The step moves to the other tier once; if that fails too, the plan pauses |
| You press **Stop** | The plan pauses; **Resume** does the cut step again |
| The budget blocks | The plan pauses; raise the budget, then **Resume** |
| Optimux restarts mid-plan | The plan shows "Interrupted by a restart at step N" with **Resume** |

Nothing is undone automatically: a step that stops halfway may leave files changed, which its result and the reply list. Usage counts each step's calls under the Simple or Complex role, with the plan and step they belong to.

## Models

### Adding a model

In **Dashboard → Models → Add a model**:

1. **Provider**: OpenAI, Anthropic, Google (Gemini), xAI, Mistral, DeepSeek, Groq, OpenRouter or Ollama (local). "✓ key saved" means its key is already there.
2. **Model**: the provider's models, cheapest first, with input and output price per million tokens and the context size. By default it shows only models that can use tools (the Simple and Complex roles run commands, which needs them) and hides models the provider has retired. Until you refresh the provider's models (below), the list is LiteLLM's and can include models the provider has switched off. Some cloud models are listed at **$0**: that can be a model the provider really serves for free (Google's Gemma models, for example) or an experimental one already switched off (Test then says "model not found"). They show "listed at $0, unconfirmed", at the end; after **Refresh <provider> models**, the ones your key can use show as **free**, first. A model with no price at all shows **—** ("price not listed"). With Google, a key from a project without billing gets the free tier on many models, within rate limits; the prices shown, and the costs Optimux counts, are the paid ones.
3. Check the price tiles (input, output, cached input, context, tools, reasoning), and the **display name**.
4. **API key**: a key belongs to a provider, not to a model: once your OpenAI key is saved, every OpenAI model uses it, and the form says "✓ saved". When it's missing, the field is required (with a link to get one). Ollama needs none. Keys are stored in `~/.optimux/secrets.env`, readable only by you, never in the repository.
5. **Use my own prices** only when yours differ (a discount) or the model is new and the list doesn't know its price yet.

Below the form, **Prices** lists every model of the provider in a table you can sort by input or output price, context size or name.

**Other** keeps the free form for any model LiteLLM can route (`together_ai/…`, `bedrock/…`): the model ID with its provider prefix, an optional **Base URL** (below), the key variable, the key, and prices only to override LiteLLM's list.

The **key variable** is the name your key is saved under in `~/.optimux/secrets.env` (`GROQ_API_KEY=…`). Optimux fills it in from the model ID (`groq/…` → `GROQ_API_KEY`) or from the Base URL (`https://api.apmix.ai` → `APMIX_API_KEY`); you can change it. Models with the same key variable share one key: type it once, and leave the API key empty for the next model. A second name (`GROQ_KEY_2`) gives a model its own key, for example from a second account. Leave it empty only for local models that need no key.

### Providers that aren't listed (proxies, resellers, your own server)

Some services sell access to known models through their own server: OpenRouter-style gateways, resellers, a company proxy, or your own vLLM or LM Studio server. They give you a key and a **base URL**. In **Other**:

1. **Model ID**: the model name the service uses, with the prefix of the format it speaks. A base URL ending in `/v1` usually speaks the OpenAI format: `openai/<their model name>`. A service that offers an "Anthropic base URL" speaks Anthropic's format: `anthropic/<their model name>`.
2. **Base URL**: the address they give you, for example `https://api.apmix.ai/v1` (with `openai/…`) or `https://api.apmix.ai` (with `anthropic/…`).
3. **Key variable**: proposed from the host (`APMIX_API_KEY`). A key is never shared between different servers: Optimux refuses `ANTHROPIC_API_KEY` for a model behind another server, because that would send your Anthropic key there. A model with a base URL also never falls back to the provider's default key.
4. **Prices**: their prices per million tokens, or 0 and 0 for a free allowance or your own server.

Before you add a model behind someone else's server, the form shows what that means, and asks you to confirm:

- **Privacy:** everything you send this model passes through that server: prompts, project files, command output, memory. Optimux can't see or limit what they keep. Use a service you trust with that code.
- **Reliability:** a reseller can be slower, change or stop the service, or serve a different model than its name says; some resold access may not be allowed by the model maker's terms.
- **Cost:** Optimux can't know what that server charges, so it never uses LiteLLM's price list for it. Without your prices, the cost shows as **unknown** and budgets can't count it.

The model card shows **↪ via <host>**, so you always see which models go elsewhere. A server on your own machine or network (`localhost`, `192.168.…`) gets only the note about prices. `http://` to a server on the internet sends your key and code unencrypted: use `https://`.

### Where the prices come from, and the online catalog

The model list and prices come from the price list of LiteLLM (the library Optimux uses to talk to providers), shipped with Optimux: **no network is needed**, and it is as fresh as your Optimux version. Providers change prices, so check their pricing page before relying on a number.

**Online catalog** (off by default) lets the Models screen go online, **only when you click Refresh**; nothing runs in the background:

- **Refresh prices** downloads LiteLLM's public price list from GitHub (`raw.githubusercontent.com`). Providers don't publish prices through their APIs; this list is what LiteLLM itself uses. Costs in Usage use the new prices from then on, also after a restart. Prices you set on a model still win.
- **Refresh <provider> models** asks that provider (for example `api.openai.com`), with your own saved key, which models your account can use: the list then marks them ("Your key") and adds brand-new models the price list doesn't know yet. Ollama is asked on this machine, so it works with the online catalog off.

**Your limits** (in the Prices table, under the chosen model, and on each model card): how many requests and tokens your key may use per minute or per day. No list has them, because they depend on your account and its tier. Most providers send them back with every reply, so Optimux shows them after the first call to a model (**Test** or a chat), without any extra request. Google sends none: Optimux learns a Google limit only when it is hit (the error names it, for example "15 requests/min, free tier"), and links to Google's rate limits page.

### Each model card

- **Status**: *ready*, *needs key*, *test failed*, or *cooldown* (temporarily skipped after errors).
- **Test** makes a tiny real call (a few tokens) to check the ID and key. A successful test also clears a cooldown.
- **Key** lets you replace the key; **Prices** lets you edit them; the bin deletes the model (its key stays in `secrets.env`).
- **Thinking**: how much the model reasons before answering: *Default* (the provider decides), *Minimal*, *Low*, *Medium* or *High*. More thinking means more output tokens, more cost and more time, and usually better results on hard tasks. A note tells you when LiteLLM doesn't list the model as supporting it. Newer Claude models (Sonnet 5 and later, Opus 4.7 and later) may think even on *Default*, but then Anthropic **hides the text**: the chat shows only "Thinking: N tokens (the model didn't share the text)". Pick a level (*Low*, *Medium*…) to get a readable summary of the thinking. You pay for the full thinking either way; the summary costs nothing extra.

### Cooldowns

When a provider says your quota is used up, or errors keep coming after a retry, the model goes into **cooldown**: Optimux skips it (and uses the other tier) instead of paying for failing calls. Quota cooldowns last until the provider's retry time (30 minutes when it gives none); other errors one minute. You see them on the model card and in **Dashboard → System**, where you can **Clear** them. A server restart clears all.

### Local models

Ollama models (`ollama/...`) cost nothing per token. They work for simple chat, but small local models are often unreliable at tool use (running commands step by step), so use them with care as executors.

## Commands, approvals and safety

### What the model can do

The model has a terminal tool: it proposes a shell command, you approve it, Optimux runs it in the active project and gives the output back to the model. Commands time out after **30 seconds**, so very long builds or servers should be started by you.

### Files changed

After a reply that ran commands or changed files, a **Files changed** box under it lists what changed, from git: `+12 −1` for an edited file, `new, 23 lines`, `deleted`. A file that was deleted, or lost half of its lines or more (for example `285 → 0 lines`), is shown first and in **red**, because that is how a broken edit usually looks, even when the reply says it's done. It costs nothing (no model call) and doesn't touch what you staged with `git add`. It compares with the files as they were just before the reply's first command, so changes you make yourself meanwhile appear too; files your `.gitignore` excludes don't. A project without git gets no list (`git init` in the project makes it possible). The list is also in the chat export.

### Writing files

To create or change a file, the model uses two file tools instead of shell commands, so a file is never left half-written or emptied, and no quotes get lost on the way:

- **write_file** creates a file or replaces a whole file.
- **edit_file** replaces one exact piece of text. It must appear exactly once in the file; if the model copied it wrong (0 matches) or too short (several matches), nothing changes, and you see the refusal in red in the chat, so a missed edit can't pass as done.

Two more checks catch a model that garbles a file before you are even asked:

- **write_file can't cut a long file in half.** Replacing a file of 20 lines or more with half its lines or fewer is refused; the model is told to use edit_file. A small model once rewrote a 783-line stylesheet from memory and kept 296 lines.
- **Mangled characters are refused.** New text with control characters or emoji escape codes (like `\ud83d`) that the file didn't have is refused. Small models sometimes escape special characters (—, ©, emoji) wrongly, and the file would get garbage.

You approve each change with its diff, like a command (auto-approve applies the same way). Optimux writes the file itself: only inside the active project (never in `.git`, never through a link that leads outside), only UTF-8 text up to 1 MB, and in one step (a temporary file renamed over the old one). A file keeps its permissions. If the file changed after you were asked, nothing is written and the model is told to read it again. In the chat a change shows as one line, like `✎ edit site/index.html (+1 −1)`, with its diff below. The tools are offered only when a project is active.

### Scope

- Commands start in the active project's folder (or the Workspace folder when no project is selected).
- Before you approve, Optimux checks every path in the command. If any points outside the project, you see a red **Outside the project** warning and auto-approve does not apply. Scripts inside skill folders are allowed.
- The approval box shows **🔒 sandboxed** or **not sandboxed**, so you always know what an approved command can reach.

### The sandbox

Without the sandbox, an approved command can do anything your user account can: read your home folder, change other projects, delete files anywhere you can. The **sandbox** confines every command the model runs, using **bubblewrap** (Linux):

- only the **working folder** can be changed: the active project, or the Workspace folder when no project is selected;
- the rest of the system is **read-only**, so compilers, interpreters and tools keep working;
- your **home folder** and Optimux's own folder (keys, token, chats, memory) are **hidden and read-only**: writing there fails right away with *"Read-only file system"* (it never seems to work and then vanishes); your global skills stay readable;
- `~` inside a command is a **scratch folder** (with your `~/.gitconfig`), so tool caches like pip's and npm's work; it's emptied after every command;
- `/tmp` is private and empty for each command, other processes are invisible, and Docker's socket isn't reachable;
- the **network** is allowed by default on your own computer (for `pip install`, `npm install`, git over HTTPS) and **off by default on a server**; you can change it either way.

**Turning it on.** Install bubblewrap (`sudo apt install bubblewrap` on Debian/Ubuntu, `sudo dnf install bubblewrap` on Fedora), then tick **Dashboard → Execution → Sandbox**. The page checks that bubblewrap really works on your machine before it lets you turn it on.

**It fails closed.** If the sandbox is on but bubblewrap stops working, commands are **refused** with an explanation; they never silently run unprotected.

**What changes for you:**

| | Without sandbox | With sandbox |
|---|---|---|
| Change files in the project | Yes | Yes |
| Change files elsewhere | Yes | No |
| Read your home folder (SSH keys, other projects, Optimux's keys) | Yes | No |
| Use system tools (python, node, git, gcc…) | Yes | Yes |
| A virtualenv **inside** the project | Yes | Yes |
| A virtualenv or library **outside** the project | Yes | Only if listed under *Extra read-only folders* |
| `pip install --user`, files in `~` | Kept | Go to the scratch `~`, thrown away after the command |
| Write to your real home or another project by its path | Yes | No: *Read-only file system* |
| git push/pull over SSH | Yes | No (SSH keys are hidden); use HTTPS or run it yourself |
| Network | Yes | Yes on your computer, no on a server, unless you change it |

**Extra read-only folders** (Execution → Sandbox) lets a project see things it needs outside its folder, such as a shared library it imports or a virtualenv, without being able to change them. Optimux's own folder and whole home folders are refused there.

**On a server.** If anyone but you can reach Optimux (a VPS, a shared machine), start it with `OPTIMUX_SERVER=1` (see *Running on a server*). To only force the sandbox on, without the rest of server mode, use `OPTIMUX_REQUIRE_SANDBOX=1`:

```bash
OPTIMUX_REQUIRE_SANDBOX=1 uvicorn optimux_backend.main:app --host 127.0.0.1 --port 8000
```

Either way the sandbox is always on, and nobody can turn it off from the Dashboard, even with the access token.

**What it can't protect:** the project itself. A command can still change any file in the project, including scripts, `Makefile`s and git hooks (`.git/hooks/`) that *you* may later run outside Optimux with all your permissions. Review changes (`git diff`, `git status`) before running project scripts yourself, and keep projects under git so you can undo.

**Not covered by the sandbox:** the project actions (create, archive, GitHub repository) are fixed commands run by the server itself, each one confirmed by you; they don't go through the sandbox. The GitHub token is given only to these, never to model-run commands.

### Auto-approve

**Dashboard → Execution → Auto-approve terminal commands** runs commands without asking, except commands flagged as outside the project, and creating or archiving projects. It saves clicks; it removes your last check. Combine it with the **sandbox**, and use it on projects you can restore from git. On a server (`OPTIMUX_SERVER=1`) it is always off.

### What commands see, and their limits

Commands get only a few basic variables from the server's environment: `PATH`, `HOME`, `USER`, `SHELL`, `LANG` and `LC_*`, `TERM`, `TZ`. Everything else is left out, so a command like `env` (or an instruction hidden in a file) finds no keys or tokens. If a project needs more (`VIRTUAL_ENV`, `JAVA_HOME`…), add the names in **Execution → Command environment and limits**. Keys Optimux keeps in `secrets.env` can never be added.

The same card sets **limits per command** (0 = none):

| Limit | Default | Notes |
|---|---|---|
| Memory (MB) | 0 | Caps address space. Node, Go and Java reserve much more than they use, so leave it at 0 unless you need it. |
| File size (MB) | 1024 | The largest file one command can write. |
| Processes | 0 | Counts every process of the server's user, Optimux included, so set it well above what Optimux runs (a few hundred). |

Open files are always capped at 4,096 and core dumps are off. Each command also stops after **30 seconds**. On a server, the systemd unit in `deploy/` limits Optimux as a whole as well.

### Secrets

- API keys, the access token and the GitHub token live in `~/.optimux/secrets.env` (mode 600), never in your repositories.
- Your password is stored only as a scrypt hash in `~/.optimux/auth.json` (mode 600), together with the two-step secret if you turned it on.
- Commands get an allow-list of variables, not a copy of the server's environment (see *What commands see, and their limits*), so a command like `env` can't leak keys.
- Automatic memory updates are told never to store secrets.

## Memory

Memory is plain Markdown, sent to the model with **every request**, so it knows who you are and how your project works without being told again. There are three files:

| File | What goes in it | Changed by |
|---|---|---|
| **Personal** | Your name, language, how you like answers | You, or the model when you ask it to remember |
| **Professional** | Coding style, commit habits, general rules | You, or the model when you ask |
| **Project** | Build/test commands, stack, structure, conventions, decisions, known issues of the active project | You, the model when asked, and **automatic updates** |

Edit them in **Dashboard → Memory**. To have the model save something, just say it: *"remember that I prefer pytest over unittest"*.

> Memory costs tokens on every single model call. Short, precise memory is cheaper **and** works better than long memory.

### Automatic project memory

When a chat ends (New chat, opening another chat, switching project, closing the page, or after the idle time you set, 10 minutes by default), the Simple model reads what is new in that chat and rewrites the project memory with durable facts only, in fixed sections and under about **1,500 tokens**. It ignores one-off details, guesses and secrets, and changes nothing when the chat taught nothing new. Workspace chats and very short chats are skipped. Personal and professional memory are never changed automatically.

- The header pill shows the update while it runs, and a toast shows what changed, with **View changes** and **Undo**.
- **Dashboard → Memory → Project** has the on/off switch, the idle minutes, **Update now from the latest chat** (reads that whole chat again, which also works after an undo), the size bar, and the typical cost per update.
- **History** lists every change to project memory (automatic, yours, the model's, undos) with its cost, a coloured diff (**Changes**) and **Undo**. An undo is itself recorded, so it can be undone too.

## Skills

A **skill** is a reusable set of instructions (and optionally scripts) for a task you do often: running the tests, writing a commit message, preparing a release.

### Running a skill

Type `/` and pick it, or type its name: `/run-tests`, optionally followed by what you want: `/run-tests only the integration tests`. The reply shows *Skill: run-tests (you asked for it)*.

### Modes

Each skill is **Manual** or **Auto**, switched on its card in **Dashboard → Skills**:

- **Manual** (the default): runs only when you type `/name`. It is never added to your prompts, so an idle manual skill costs **nothing**.
- **Auto**: its name and description (about 30 tokens) are added to **every** request, so the Conductor or the model can pick it by themselves. Convenient, but you pay for it on every model call, used or not.

The **When skills run** switch at the top of the Skills page decides what is allowed:

- **Off**: no skills at all; `/name` is refused.
- **Manual only**: nothing is listed; `/name` works.
- **Auto allowed**: Auto skills are listed. The page shows what the listed skills add to each model call.

### Writing a skill

A skill is a folder with a `SKILL.md`:

```markdown
---
name: release-notes
description: Write release notes from the git log since the last tag.
mode: manual        # manual (default) or auto
tier: simple        # optional: simple or complex
max_steps: 6        # optional: step limit while the skill runs
thinking: low       # optional: default, minimal, low, medium, high
---
## Steps
1. Find the last tag with `git describe --tags --abbrev=0`.
2. List commits since then with `git log <tag>..HEAD --oneline`.
3. Group them into Features, Fixes and Other.

## Rules
- Don't push or tag anything unless asked.
```

- The **description** is required: it tells the model when the skill applies.
- **tier**, **max_steps** and **thinking** override the normal settings while the skill runs, so a skill can be kept cheap.
- Other files in the folder (scripts, templates) are listed for the model; scripts run through the normal approval.
- **Global** skills live in `~/.optimux/skills/<name>/`; **project** skills in `<project>/.optimux-skills/<name>/` (a project skill with the same name wins).

Create them with **New skill** (starts from a template) or **Save as skill** under a reply: the Simple model drafts a skill from that chat (one paid call; the dialog shows the typical cost), and you review it before anything is saved. **Remove** moves a skill to `~/.optimux/legacy/skills/`; nothing is deleted.

Two starter skills are added on first start: `run-tests` and `commit-message`.

## Costs, usage and budgets

### How a cost is counted

Every model call is billed by tokens:

- **Input**: everything sent to the model: instructions, memory, listed skills, the conversation so far, command outputs. Usually the biggest part.
- **Cached input**: input the provider has seen recently in the same order. It costs a fraction of the normal price. Optimux keeps prompts stable (same instructions first, memory next) and marks them for caching where the provider needs it (Anthropic), so long chats get cheaper per step.
- **Output**: what the model writes, **including thinking**.

The price per token comes from your model settings or LiteLLM's price list. When a price is unknown, costs show with a **+** ("at least this much").

### Where you see costs

- **Live**, under a running reply (**So far:**), and on the usage line of every reply.
- **In the header**: the active project's month (and budget).
- **Dashboard → Overview**: today, this month, the cache-hit rate, output and thinking, cost per day for the last 30 days, budgets, model status and recent chats.
- **Dashboard → Usage**: totals for today, 7 days, this month or all time, broken down **by project, role, model, skill**, plus **skill drafting** and **memory updates**, with calls, input, cached, output, thinking, cost and average time. *Roles* are who made the call: `conductor`, `simple`, `complex`, `override` (a model you picked), `curator` (memory updates), `skill_draft`, `test`.
- **History**: the cost of every chat.

### Budgets

In **Dashboard → Budgets**, give each project a monthly limit in USD and choose what happens at 100%:

- **Block**: new requests are refused and a running one is paused, with a **Raise budget** button.
- **Warn**: only notes appear; nothing stops.

A note appears at 80% either way. Budgets follow the calendar month. Calls without a known price can't be counted, so set prices for every model you use.

### Cost warnings

Some buttons call a model besides your chat messages. **Dashboard → Budgets → Cost warnings** decides whether Optimux warns you first, with an estimate from your own past calls ("≈ $0.0012 per update, average of your last 4"):

- **Warn me before "Update memory now"**.
- **Warn me before "Escalate ↑"** (the estimate is per call; a reply usually takes a few).

Each warning has a *Don't warn me again* box; turn warnings back on here. **Save as skill** always shows its cost in its own dialog. These switches only control the warnings, never the features themselves.

## How to spend less

Most of the cost is **input tokens resent on every step**. The levers below are ordered roughly by impact. None is mandatory; pick the balance of cost, speed and reliability that suits you.

### Biggest savings

- **Start new chats often.** Every step resends the conversation. When you change subject, click **New chat**: the next request starts small again. (Project memory carries the important facts over.)
- **Use a cheap Simple model** and keep the Conductor on: most requests never need the expensive model.
- **Keep memory short.** Every line of Personal, Professional and Project memory is paid on every model call. Remove what isn't useful; watch the size bar.
- **Keep skills Manual**, or set the global skills switch to *Manual only*. Each Auto skill costs about 30 tokens on every call.
- **Lower thinking** on models where you don't need it (**Models → Thinking: Minimal or Low**), especially the Conductor and Simple models. Thinking tokens are billed as output, the most expensive kind.

### Good habits

- **Ask questions as questions.** "How do I run the tests?" gets an explanation; "run the tests" runs them. Fewer steps, fewer tokens.
- **Be specific**: name the file or function. The model needs fewer exploratory commands.
- **Use `/simple`** for chats you know are easy, and `/complex` only for chats you know are hard, so the Conductor doesn't have to guess.
- **Watch the So far line**, and **Stop** (Esc) a reply that is going the wrong way; then say what you want instead.
- **Set step limits** in Execution: a lower Simple limit escalates sooner (pricier model, fewer wasted cheap steps); a lower Complex limit pauses big tasks for your OK.

### Background costs

- **Automatic memory updates** cost one Simple call per finished chat with something new (usually a fraction of a cent; the Memory page shows your average). Turn them off in **Memory → Project** and use **Update now** when you want one.
- **Conductor: Off** saves one small call per message, at the price of hard tasks starting on the weaker model.
- **Test** on a model and **Save as skill** each make one call.

### Safety nets

- A **budget with Block** on every project means a runaway task can't surprise you.
- **Cost warnings** stop expensive buttons from being clicked by accident.
- Set **prices** for every model, so nothing shows up as "+" (unknown).

## Dashboard reference

| Section | What you find there |
|---|---|
| **Overview** | Today and this month, cache-hit rate, output and thinking, cost per day (30 days), budgets, model status, recent chats. |
| **Usage** | Tokens, cost and time per project, role (hover a role for what it is), model and skill, plus skill drafting and memory updates, for today, 7 days, this month or all time. |
| **Budgets** | Monthly limits per project (Block or Warn) and the cost warnings. |
| **Projects** | Cost, budget, chats and memory per project; make one active, create, archive; restore or permanently delete archived projects. |
| **History** | Every stored chat with filters (project, period, active or archived, title search) and sortable columns; open one to continue it. |
| **Routing** | The Conductor, Simple and Complex models. |
| **Models** | Add models, keys, prices, thinking level, test, cooldowns. |
| **Memory** | Personal, professional and project memory; automatic updates and their history. |
| **Skills** | When skills run (Off, Manual only, Auto allowed), each skill's mode, editing, removing, usage and drafting cost. |
| **Execution** | Auto-approve, the sandbox, what commands see and their limits, the step limits. |
| **GitHub** | The GitHub CLI, the GitHub token (check, replace, remove) and who commits. |
| **Scope** | The Workspace folder. |
| **Backups** | Snapshots of everything, your data or your projects, kept on this machine: take one, see what goes in, delete, and the space limit. |
| **System** | The security checklist, versions, where files are kept, sign-in (password, two-step codes, signed-in browsers), the access token, database and backups, models in cooldown. |
| **Guide** | This guide. |

Every page has **?** marks: hover one for an explanation, click to keep it open. Each section has its own address (for example `#/dashboard/usage`), so the browser's back button and bookmarks work. **Esc** or **← Back to chat** returns to the chat, which keeps running in the background.

## Settings reference

| Setting | Where | Default | Effect |
|---|---|---|---|
| Workspace folder | Scope | (none) | The folder that holds your projects; each folder inside is a project. |
| Conductor / Simple / Complex | Routing | (none) | Which model does what. Conductor can be *Off*. |
| Thinking per model | Models | Default | Reasoning effort: Default, Minimal, Low, Medium, High. |
| Prices per model | Models | LiteLLM list | USD per million input, output, cached-input tokens. |
| Auto-approve commands | Execution | Off | Run commands without asking (never outside the project, never creating or archiving projects). |
| Sandbox | Execution | Off | Run commands in bubblewrap: only the working folder can change. Forced on by `OPTIMUX_REQUIRE_SANDBOX=1`. |
| Sandbox: network | Execution | On (off on a server) | Allow network access inside the sandbox. |
| Sandbox: extra read-only folders | Execution | (none) | Folders outside the project that commands may read. |
| Extra variables for commands | Execution | (none) | Variable names passed on to commands, besides the basic ones. |
| Command limits | Execution | Memory 0, file 1024 MB, processes 0 | Per-command limits; 0 = none. |
| GitHub token | GitHub | (none) | Lets new projects get a GitHub repository; checked with GitHub before it's saved. |
| Commit name and email | GitHub | (git's own) | Who the first commit of a new project, and approved model commits, are from. |
| Snapshot space limit | Backups | 5 GB | The most space snapshots may use. Automatic snapshots are removed first to make room; yours never. |
| Two-step codes | System → Sign-in | Off | Ask for a 6-digit code from an authenticator app when signing in. |
| Steps per turn: Simple | Execution | 8 | Model calls before escalating to Complex. |
| Steps per turn: Complex | Execution | 25 | Model calls before pausing with Continue. |
| Update project memory automatically | Memory → Project | On | Curate memory when a chat ends. |
| Idle minutes | Memory → Project | 10 | Update memory after this long without activity in a chat. |
| Skills | Skills | Auto allowed | Off, Manual only, or Auto allowed. |
| Skill mode | Skills (per skill) | Manual | Manual (`/name` only) or Auto (listed in prompts). |
| Monthly budget | Budgets (per project) | None | USD per calendar month, Block or Warn at 100%. |
| Warn before "Update memory now" | Budgets | On | Show the estimate and ask first. |
| Warn before "Escalate ↑" | Budgets | On | Show the estimate and ask first. |

## Keyboard and commands

| Key or command | Action |
|---|---|
| **Enter** | Send. In the `/` list: pick the highlighted item. |
| **Shift+Enter** | New line. |
| **Esc** | Close a dialog or the code viewer, leave the Dashboard, or stop the running request (in that order). |
| **/** | Show commands and skills; **↑ ↓** to move, **Tab** or **Enter** to pick. |
| `/new` | New chat. |
| `/simple`, `/complex` | Pin the tier for this chat (optionally followed by a request). |
| `/<skill>`, `/skill <name>` | Run a skill (optionally followed by a request). |

## Where your data lives

Everything Optimux keeps is in one folder, `~/.optimux` (set `OPTIMUX_HOME` to move it). Your projects are never used for this.

```
~/.optimux/
├── secrets.env                   API keys and the access token (mode 600: readable only by you)
├── auth.json                     your password's hash and the two-step secret (mode 600)
├── config/
│   ├── settings.json             Workspace folder, approvals, sandbox, memory, skills…
│   ├── models.json               your models, prices and thinking levels
│   ├── routing.json              which model is Conductor, Simple and Complex
│   └── budgets.json              monthly budget per project
├── data/
│   └── optimux.db                SQLite: chats, every model call and its cost, memory history, sign-ins
├── backups/
│   ├── optimux-2026-10-07.db     one copy of the database per day…
│   └── optimux-2026-10-08.db     …the newest 14 are kept
├── snapshots/                    snapshots taken in Dashboard → Backups
│   ├── 2026-10-14_093000-data.tar.gz
│   └── 2026-10-14_093000-data.json   what the snapshot holds (for the list)
├── memory/
│   ├── personal.md               about you
│   ├── professional.md           your coding style and rules
│   └── projects/
│       ├── notes-api.md          one file per project (kept up to date automatically)
│       └── website.md
├── skills/                       global skills, available in every project
│   ├── run-tests/
│   │   └── SKILL.md
│   └── commit-message/
│       └── SKILL.md
├── logs/
│   └── migrations.log            files moved into this layout from older versions
└── legacy/                       old files and removed skills: moved here, never deleted
    └── skills/
        └── global-old-skill-20261008-101500/
```

Inside your Workspace folder, Optimux only ever adds what you ask for:

```
~/git/                            the Workspace folder
├── notes-api/                    a project (your files)
│   └── .optimux-skills/          optional: skills for this project only
│       └── release-notes/
│           └── SKILL.md
├── website/
└── .archive/                     projects you archived (moved here, never deleted)
```

- **Chats and costs are never cleared**: not by a restart, a new chat, or an update. Deleting a chat removes the conversation; its cost records stay in Usage.
- A database **backup** is made each time the server starts (one per day, 14 kept).
- **Dashboard → System** shows all these paths, the database size and the latest backup.
- `~/.optimux` is readable only by you (mode 700), and `secrets.env` and `auth.json` by nobody but you (mode 600); Optimux sets this on every start.

### Snapshots

A **snapshot** is a copy kept on this machine, so you can try something and go back. Open **Dashboard → Backups** and choose what to save:

- **Everything** (the default, recommended) holds your data and all your projects, in one snapshot taken at the same moment. Use it before an experiment, so you can go back fully.
- **Data only** holds your settings, models and routing, memory, skills, the database (chats, usage and costs, memory history, sign-ins) and your keys (`secrets.env`, `auth.json`). It leaves out the daily backups, the snapshots themselves, logs and `legacy/`.
- **Workspace only** holds your projects (all of them, or the ones you tick), each with its project memory file. Folders that can be rebuilt (`node_modules`, `.venv`, `venv`, `__pycache__` and tool caches) are skipped unless you untick the box. Archived projects are not included, a project folder that is a link to another folder is not followed, and Optimux's own data folder (with your keys) is never copied into a project, even when a project contains it.

Choosing Data only or Workspace only shows a yellow note saying what will **not** be saved. **Take snapshot** then asks first, naming what is left out (including projects you didn't tick): **Take Everything instead**, **Proceed with only my data** / **only the selected projects**, or **Cancel**.

**Show what goes in** lists every part with its size, what is skipped and what is left out, and says whether it fits. **Take snapshot** runs in the background; the list shows *taking…* until it is ready. Add a note (for example "before trying the new routing") so you know later why you took it.

Each row names its projects (or counts them when there are more than three); **Details** shows the projects, every part with its size, what was skipped or left out, the file's full path (with **Copy**), the Optimux version that took it, and **Read the README**, which shows `BACKUP-README.md` from inside the file, formatted, without unpacking it; each command has a **Copy** button. The version is the release and the exact commit, such as `v0.4.0 (5e0673b)` (`-dirty` means the Optimux code had uncommitted changes). **Delete** asks inside the row before removing the file, and a message confirms it. The folder is shown above the list, with **Copy** and a **?** explaining the files.

Each snapshot is one `.tar.gz` file in `~/.optimux/snapshots/`, readable only by you (on a server installed with the installer, that is the `optimux` user, so use `sudo`). Inside, `BACKUP-README.md` (Markdown, easy to read in a terminal too) explains the contents and how to restore by hand, `manifest.json` describes it for Optimux, and `SHA256SUMS` holds a checksum of every file (after unpacking, `sha256sum -c SHA256SUMS` checks them).

A file that changes while it is copied (a log, a database in use) is stored as it was when the copy began, and a file that can't be read is left out; the list shows a **warning** badge for both (hover it for the files). Databases inside your projects are copied as plain files: if one was in use, check it after a restore.

**Space.** Sizes are counted before compression, so the archive is usually smaller. Snapshots may use up to the limit set in Backups (5 GB by default), and never the last 200 MB of free disk. To make room, Optimux removes its own **automatic** snapshots, oldest first. Yours are never removed automatically: delete them yourself when you no longer need them.

A snapshot lives on the same disk as the data, so it doesn't protect you against losing the disk. Keep a copy elsewhere too: download it.

### Downloading a snapshot

In a snapshot's **Details**, **Download this snapshot…** prepares a copy to keep on your computer or another disk. A snapshot with your keys (Data, Everything) only leaves the server **encrypted**, with [age](https://age-encryption.org):

- **Encrypt with a one-time key** (recommended): Optimux makes a key and shows it **once**. Copy it or **Save the key as a file**, tick **I have saved the key**, then download. Optimux doesn't keep the key: without it, nobody can open the file, and neither can you if you lose it. Keep the key apart from the file, for example in a password manager.
- **Encrypt to my public key**: paste an `age1…` key or your SSH public key (`ssh-ed25519 …` or `ssh-rsa …`); only your secret key opens it. **Remember this public key** saves it for next time. **How do I get a public key?** in the panel walks you through it:
    1. Use a key from **the computer where you will open the download**, never one made on the server (the secret half must not live next to the backup).
    2. Most developers already have one: on your computer, `cat ~/.ssh/id_ed25519.pub` (or `id_rsa.pub`; on Windows, `type $env:USERPROFILE\.ssh\id_ed25519.pub`).
    3. None yet: `ssh-keygen -t ed25519`, or a key just for backups with `age-keygen -o ~/optimux-backup.key` (it prints `Public key: age1…`).
    4. Paste only the public part: the `.pub` line or the `age1…` key, never the secret file. Hardware-token keys (`sk-ssh-…`) don't work with age.
- **No encryption**: only for a Workspace snapshot, which holds no keys.

The download link works for one hour. To open the file on your computer, install age (`apt install age`, `brew install age`) and run the command shown, for example:

```bash
age -d -i optimux-key-2026-10-15_093000-full.txt -o snapshot.tar.gz optimux-snapshot-2026-10-15_093000-full.tar.gz.age
mkdir -m 700 restore && tar -xzf snapshot.tar.gz -C restore
```

### Restoring a snapshot

In the list, **Restore…** on a snapshot opens a panel that lists its parts. Tick what to put back:

- **Settings, memory, skills** and the **database** (chats, memory history, the list of deleted projects) are ticked by default.
- **API keys** (`secrets.env`) and **password and two-step codes** (`auth.json`) are **not** ticked by default: an older copy would bring back keys or a password you may have changed since. Tick them on a new server, or if you lost them.
- **Each project** is ticked. A project that exists now moves to **Archived projects** first (you can bring it back from there); a project that no longer exists comes back as new. Its project memory comes with it, and the change is recorded in the memory history (so **Undo** works).

Then type **yes I am sure I want to restore the backup** and click **Restore**. What happens:

1. Every file in the snapshot is checked against its checksum (`SHA256SUMS`), and its structure is checked (no paths that leave the folder, nothing written through a link). If anything doesn't match, **nothing is restored**.
2. When you restore data, your **current data is saved first** in a new Data snapshot ("Before restoring …"). It is yours: it is never removed automatically.
3. **Projects are put back at once.**
4. **Data is put back when Optimux restarts.** A yellow box at the top of Backups says so. Click **Restart now** when it is offered: Optimux restarts itself and the page reloads. It is offered when Optimux runs as a service set up by the installer (after `optimux update`, older installs get it too). Otherwise run `optimux restart` (`sudo optimux restart` on a server). While the restore waits, other changes (settings, memory, models) wait too, since the restart would replace them. Until then, chat replies wait, so no new chat is lost by the restore. **Cancel the restore** drops it without changing anything.
5. After the restart, a green box says what was put back and where the files it replaced are: `~/.optimux/legacy/before-restore-<time>/`. **Nothing is deleted.**

Links (shortcuts) inside your projects come back as links. In Optimux's own data a link could point your keys or database somewhere else, so links there are never restored (the result says how many were left out).

**Never rolled back:** your **usage and cost history** (Usage and budgets always show what you really spent) and your **current sign-ins** (a sign-out you did is not undone). A restored database gets the current ones.

A restore is refused while a reply is running, while a snapshot is being taken, or while another restore waits for the restart.

**From a downloaded file.** **Restore from a downloaded file** (under the list) takes a snapshot you downloaded earlier, from this server or another one: this is how you move Optimux to a new machine.

1. Choose the file (`….tar.gz.age` or `….tar.gz`) and click **Upload and check**.
2. If it is encrypted with a one-time key, paste the key (`AGE-SECRET-KEY-1…`) or **Choose the key file** (`optimux-key-….txt`), then **Decrypt and check**. The key is used once and kept nowhere. A wrong key can be corrected without uploading again.
3. The whole file is checked; it then appears in the list as **Uploaded: …**, with the restore panel open.

If you encrypted the download to your **SSH key**, don't paste your private SSH key into a web page: open the file on your computer first (`age -d -i ~/.ssh/id_ed25519 -o snapshot.tar.gz <file>.age`) and upload the `.tar.gz`.

### Backing up, starting over, moving

**Back up** by copying the whole folder while the server is stopped, or just `~/.optimux/backups/` (a daily copy of the database) to another disk from time to time. The backups live in the same folder, so they don't protect you against deleting it.

**Starting over.** If `~/.optimux` disappears, the next start rebuilds the structure: a **new access token** (printed in the terminal; the sign-in page asks for it to set a new password), an empty database, default personal and professional memory, the starter skills and the example models. **Everything else is gone for good**: API keys, chats, usage and cost history, memory and its history, your skills, models, routing, budgets and settings. Your projects and their `.optimux-skills/` are untouched. To start fresh without losing anything, **rename** the folder instead of deleting it:

```bash
mv ~/.optimux ~/.optimux.old      # Optimux starts clean; put it back to undo
```

If the folder is deleted while the server is running, Optimux keeps working with a fresh, empty database; restart it to also get a new token and the starter files.

**Moving to another machine.** The easiest way: take an **Everything** snapshot, download it encrypted, install Optimux on the new machine, and restore the file there (see *Restoring a snapshot*; tick the keys and the password too). By hand: stop the server, copy `~/.optimux` (it contains your keys: use a secure channel, e.g. `scp` or an encrypted drive), and start Optimux there. Update the Workspace folder in **Scope** if your projects live elsewhere.

## Running on a server

Optimux can run on a VPS so you can use it from anywhere. A server is reachable by everyone on the internet, so it needs more than a password. The `deploy/` folder in the repository has the files used below; **Dashboard → System → Security checklist** shows what is in place.

### What server mode changes

Start Optimux with `OPTIMUX_SERVER=1` and:

- the **sandbox is required** (if bubblewrap doesn't work, commands are refused);
- **auto-approve is off** and can't be turned on: every command waits for you;
- commands have **no network** unless you turn it on in **Execution → Sandbox**;
- the sign-in cookie is only ever sent over **HTTPS**.

### With the installer

On the server (as root, or a user with `sudo`):

```bash
curl -fsSL https://raw.githubusercontent.com/optiware-ai/optimux/main/install.sh | bash
```

Choose **A server (VPS)**. The installer then:

1. asks for your **domain name** (and checks it points at the server), or sets up **SSH-tunnel-only** access if you have none;
2. installs Python, git, bubblewrap, **Caddy** (HTTPS) and ufw;
3. creates the **optimux** user, puts the program in `/opt/optimux` (owned by root, read-only for the service) and the data in `/var/lib/optimux`;
4. writes `/etc/optimux/optimux.env` with **server mode** on, and asks for your **password**, a first **API key**, **GitHub** (installs `gh`, checks a token), and checks the **sandbox** (applying the AppArmor fix on Ubuntu 23.10+ if needed);
5. installs the hardened **systemd service** and gives Caddy its own file, `/etc/caddy/optimux.caddyfile`, imported from the main Caddyfile, so **other sites on the server keep working** (if the Caddyfile is still the package's example, it is replaced and kept as `Caddyfile.before-optimux`); the configuration is validated before Caddy restarts, and put back if Caddy rejects it;
6. offers the **firewall**: ufw (SSH stays open on the port it really uses; it won't turn the firewall on unattended if it can't tell which port that is). Before that it lists the **other services listening on public ports** (another website, a VPN, ...), which the firewall would block, and asks: keep them reachable, leave the firewall off, or block them. Unattended (`--yes`), it leaves the firewall off when it finds any, and prints the command to turn it on. On Fedora it opens 80/443 in **firewalld** instead; and **automatic security updates** on Debian/Ubuntu;
7. starts everything and waits for the HTTPS certificate.

Unattended: `... | sudo bash -s -- --server --domain optimux.example.com --yes` (then set the password with `sudo optimux password`).

#### Without a domain: the SSH tunnel

With **SSH tunnel only**, Optimux listens only inside the server (`127.0.0.1:8000`); nothing on the internet can reach it, not even you directly. You reach it through your normal SSH login, with a tunnel that **you run on your own computer**, not on the server:

```bash
ssh -N -L 8000:127.0.0.1:8000 you@your-server-address
```

Then open **http://localhost:8000** in your browser. Keep that terminal open: the tunnel lasts as long as the SSH connection.

- Use the login and address you normally use for SSH (`root@203.0.113.7`, for example). The installer's final summary prints the exact command, and `sudo optimux open` or `sudo optimux status` on the server prints it again.
- `-L 8000:127.0.0.1:8000` means "port 8000 on my computer → port 8000 inside the server". If 8000 is taken on your computer, change the first number (`-L 8080:127.0.0.1:8000`, then open http://localhost:8080). If the installer chose another port on the server, change the second one.
- `-N` opens only the tunnel, without a shell. Add `-p 2222` if your SSH server listens on another port.
- On Windows, the same command works in PowerShell (OpenSSH is built in).
- **Behind NAT, an office network or in a container**, the address the installer finds (the one the server uses to reach the internet) may belong to the router or gateway, not to the server; the installer then adds a note with the server's own address. Use whichever address your computer can reach. On cloud servers (AWS, Google Cloud, ...) the public address is the right one.
- **No SSH server on the machine** (common in containers): the installer says so instead of printing a command that can't work. Install one (`sudo apt install openssh-server`), or, in an Incus container, forward the port from the host: `incus config device add <container> web proxy listen=tcp:127.0.0.1:8000 connect=tcp:127.0.0.1:8000`.

Uninstalling (`sudo optimux uninstall`) removes the program, the service, Optimux's Caddy file and its import line (other sites stay), and asks separately before deleting `/var/lib/optimux` and the optimux user. Firewall rules for 80/443 are left in place.

> On Fedora and RHEL, `sudo` doesn't search `/usr/local/bin`: use `sudo /usr/local/bin/optimux`. With SELinux enforcing, Caddy may need permission to connect to Optimux's socket; this hasn't been tested yet.

The steps below do the same by hand.

### Step by step (Debian or Ubuntu)

These steps assume a fresh server, a DNS name (here `optimux.example.com`) pointing at it, and SSH access as a user with `sudo`. Keep your SSH session open until the end.

1. **Packages and firewall.**
   ```bash
   sudo apt update && sudo apt install -y python3-venv git bubblewrap caddy ufw
   sudo ufw allow OpenSSH && sudo ufw allow 80,443/tcp && sudo ufw enable
   ```
   (On older releases, install Caddy from caddyserver.com's apt repository.) If the server already runs other services, check first with `sudo ss -tulpn` and add a `sudo ufw allow <port>/<tcp|udp>` rule for each one that must stay reachable, or the firewall will block it.
2. **A user just for Optimux** (no login, no sudo):
   ```bash
   sudo useradd --system --create-home --home-dir /var/lib/optimux --shell /usr/sbin/nologin optimux
   sudo -u optimux mkdir -p /var/lib/optimux/workspace
   ```
3. **The code**, owned by root so the service can't change it:
   ```bash
   sudo git clone https://github.com/optiware-ai/optimux.git /opt/optimux
   sudo python3 -m venv /opt/optimux/.venv
   sudo /opt/optimux/.venv/bin/pip install -r /opt/optimux/requirements.txt
   ```
4. **Settings and service:**
   ```bash
   sudo install -d -m 750 -g optimux /etc/optimux
   sudo install -m 640 -g optimux /opt/optimux/deploy/optimux.env.example /etc/optimux/optimux.env
   sudo cp /opt/optimux/deploy/optimux.service /etc/systemd/system/
   sudo systemctl daemon-reload && sudo systemctl enable --now optimux
   sudo journalctl -u optimux -n 30     # the access token is printed here on the first start
   ```
5. **HTTPS.** Let Caddy reach Optimux's socket, put your DNS name in `/opt/optimux/deploy/Caddyfile`, and install it:
   ```bash
   sudo usermod -aG optimux caddy
   sudo cp /opt/optimux/deploy/Caddyfile /etc/caddy/Caddyfile    # then edit the name in it
   sudo systemctl restart caddy
   ```
   Caddy gets the certificate by itself.
6. **Sign in** at `https://optimux.example.com`: paste the token, choose a password, then turn on **two-step codes** in **Dashboard → System → Sign-in**.
7. **Check the sandbox** in **Dashboard → Execution**. On Ubuntu 23.10 and later, if it says bubblewrap "can't create a sandbox here", AppArmor is blocking it: copy `deploy/apparmor-bwrap` to `/etc/apparmor.d/bwrap`, run `sudo apparmor_parser -r /etc/apparmor.d/bwrap`, then **check again**. (This lets every local user use bubblewrap, which is fine on a server that only runs Optimux.)
8. **Set the Workspace folder** to `/var/lib/optimux/workspace` in **Dashboard → Scope**, and add your models.

> **There is no port to open.** Optimux listens on a Unix socket (`/run/optimux/optimux.sock`) that only Caddy can open, and commands run by the model can't see it; Caddy is the only way in. The service unit also keeps the system read-only for Optimux (except `/var/lib/optimux`), caps the memory, processes and CPU of Optimux and its commands together, and gives it no way to gain privileges.

**Updating:** `sudo optimux update` (installed with the installer), or by hand: `cd /opt/optimux && sudo git pull && sudo /opt/optimux/.venv/bin/pip install -r requirements.txt && sudo systemctl restart optimux`.

**Backups:** copy `/var/lib/optimux/.optimux` (it holds your keys: keep the copy encrypted) and your Workspace off the server.

**Local models** (Ollama) are usually too slow on a small VPS; use hosted models, or a cheap hosted model as the Conductor.

## Security

- **Run on 127.0.0.1** unless you know you need more. (Locally, uvicorn believes the `X-Forwarded-For` header from 127.0.0.1, so other programs on the machine could hide which address they come from; add `--no-proxy-headers` if other people use the same machine. *Running on a server* avoids this with a socket only Caddy can open.) For remote access, use an SSH tunnel (`ssh -L 8000:127.0.0.1:8000 yourserver`) or *Running on a server*.
- **Sign-in.** Every page, API call and the live connection need a signed-in browser (password, plus a two-step code if you turned it on) or the access token. Wrong attempts are limited per address.
- **Other websites** open in your browser can't use your Optimux: the sign-in cookie is never sent with requests from other sites, and changes from other web origins are refused (add exceptions with `OPTIMUX_ALLOWED_ORIGINS` only if you really need them).
- **Replies can't run code in the page.** Everything shown as formatted text (model replies, the guide, charts) is cleaned before it is displayed, and the page only runs Optimux's own scripts, all served by Optimux itself (no CDN). Browsers are told never to put Optimux inside another site's frame.
- **Turn on the sandbox.** Without it, approved commands run with all your permissions. With it, they can only change the working folder and can't see your home folder or Optimux's keys. On a server, use `OPTIMUX_SERVER=1` so it can't be turned off.
- **Keys never enter your repositories or command environments**, and memory updates are told never to store secrets.

## Troubleshooting

**The sign-in page asks for the access token.**
No password is set yet (first start, or `auth.json` was deleted). Copy the token from `~/.optimux/secrets.env` (the `OPTIMUX_TOKEN=` line) and choose a password.

**"Too many failed attempts" / "Too many wrong codes".**
Five wrong passwords or codes from one address lock it for 15 minutes; 30 wrong passwords from anywhere, or 10 wrong codes in an hour, pause password sign-in for everyone. Wait, or restart the server to clear the counts.

**"Wrong or already used code".**
Each two-step code works once. Right after turning codes on, wait for the next one (they change every 30 seconds). If codes are always refused, check the time on the phone and the server.

**Signed out right after signing in, on a server.**
The cookie is only sent over HTTPS in server mode. Open the `https://` address.

**The dot is red ("Offline, retrying…").**
The server isn't running or isn't reachable. Start it again; the page reconnects by itself and reopens your chat.

**"Skipping … (cooldown)" or a model shows *cooldown*.**
The provider reported a quota limit or repeated errors. Optimux uses the other tier meanwhile. Check your provider account, then **Clear** the cooldown in System or click **Test** on the model.

**"Routing role 'simple_model' is not configured".**
Choose models in **Dashboard → Routing**.

**A request was refused or paused because of the budget.**
The project reached a **Block** budget. Click **Raise budget**, or switch the project to **Warn**.

**"Outside the project" on a command that looks harmless.**
The command mentions a path outside the active project (for example `cd ..` or an absolute path). Check it; approve it if you meant it. It is never auto-approved.

**"Also on GitHub" is greyed out, or the GitHub step failed.**
The reason is shown next to it. *gh isn't installed*: install it on the Optimux machine (or run `optimux github`). *GitHub isn't set up*: add a token in Dashboard → GitHub. *Resource not accessible* or *403* during the push: a fine-grained token needs **Administration** and **Contents** set to *Read and write* for **All repositories**. The project folder is kept either way; push it yourself later.

**"git doesn't know who commits".**
Set the commit name and email in Dashboard → GitHub (on a server, the Optimux user has no git settings), then make the first commit by hand.

**"Refused: the sandbox is on but unavailable".**
The sandbox is on, but bubblewrap is missing or can't create a sandbox (some systems disable unprivileged user namespaces). Install it (`sudo apt install bubblewrap`), then click **check again** in Execution → Sandbox. Commands are refused on purpose rather than run unprotected. Optimux checks this when it starts: a red notice above the chat box says so before you approve anything.

If the reason is **"Can't mount proc … Operation not permitted"**, something is mounted over parts of `/proc` where Optimux runs, and the kernel then refuses each command its own `/proc`. In a systemd service this comes from `ProtectHostname`, `ProtectProc`, `ProcSubset`, `ProtectKernelTunables` or `ProtectKernelLogs` (`ProtectHostname` only does it together with other settings, such as `ProtectSystem`). The service file Optimux installs sets none of them since v0.4; `sudo optimux update` installs the corrected file. To check a service by hand: `systemctl cat optimux | grep -E "^(Protect|Proc)"`. In a container (`systemd-detect-virt` says `lxc` or `openvz`), the host may mount files over `/proc`, and only the host can change that.

**With the sandbox on, a command can't find a file, a virtualenv or a library.**
Only the project folder, the system and the folders you list are visible. Keep the virtualenv inside the project, or add the folder under **Extra read-only folders**. Files written to `~` or `/tmp` disappear after each command.

**`git push` fails inside the sandbox.**
SSH keys are hidden from sandboxed commands. Use an HTTPS remote with a credential helper, or run the push yourself.

**A command was cut off with exit 124.**
Commands time out after 30 seconds. Run long builds, servers or watchers yourself in a terminal.

**The model did work I only asked about.**
Ask as a question ("how do I…?") and it should explain and offer instead. If it still acts, Reject the command and say so; consider turning auto-approve off.

**The cost shows "+".**
Some calls had no known price. Set the model's prices in **Models**.

**Memory says "nothing new" when I expected an update.**
The chat had nothing durable to add, or it was very short, or it was a Workspace chat. Use **Update now** to re-read the latest chat, or edit the memory yourself.

**`pytest` errors about `TestClient` when developing Optimux.**
Run the tests inside the virtual environment, with the pinned requirements installed (`pip install -r requirements.txt`).

## Glossary

- **Token**: a piece of a word; models are billed per token read (input) and written (output).
- **Cached tokens**: input the provider recognizes from a recent call; billed at a fraction of the price.
- **Thinking tokens**: output the model spends reasoning before answering; billed as output.
- **Turn**: one request and its reply.
- **Step**: one model call inside a turn.
- **Tier**: Simple or Complex, the two kinds of model Optimux routes between.
- **Conductor**: the small call that picks the tier (and Auto skills) for each new message.
- **Escalation**: moving a request from Simple to Complex when Simple gets stuck.
- **Fallback**: switching tier because a provider failed.
- **Cooldown**: a pause during which a failing model is skipped.
- **Pin**: fixing a chat's tier with `/simple` or `/complex`.
- **Override**: choosing a specific model for your requests in the chat's model selector.
- **Workspace**: the folder that holds your projects; selecting *Workspace* in the menu means no project is active, and you list, create, switch and archive projects there.
- **Project**: one folder inside the Workspace, with its own memory, skills, chats, costs and budget.
- **Sandbox**: bubblewrap confinement for commands: only the working folder can change, your home folder is hidden.
- **Curator**: the background job that keeps project memory up to date (shown as *auto* in memory history, *curator* in Usage).
- **Skill**: reusable instructions run with `/name` (Manual) or picked automatically (Auto).
- **Budget**: a monthly cost limit per project, with Block or Warn.
