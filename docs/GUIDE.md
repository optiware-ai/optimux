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
3. **Set your Workspace folder.** In **Dashboard → Scope**, set the folder that holds your projects, for example `/home/you/git`. Every folder inside it becomes a project.
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
- **The project menu** chooses the active project, or **Workspace** (no project). Switching opens that project's latest chat.
- **+** next to it creates a **new project** with a form (no model involved, so it costs nothing; see *Creating a project*).
- **The cost button** shows this month's cost for the active project, or *spent / budget* when it has a budget (yellow from 80%, red from 100%). Hover it for today, this month, all time and this browser session; click it to open Budgets or the Overview.
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
| **Open it when it's ready** | Switches to the new project. In the chat, the reply finishes first, then the chat moves to the new project; the conversation you were in stays in History under its own project. |

Nothing is created until you press **Create project**, and the server checks every value again. A notification slides in when the project is ready (with a link to the GitHub repository); if a step failed (for example the push), the folder is kept and the notification says so. Creating a project always waits for you, even with auto-approve on.

### Cloning a project from GitHub

**+ New project → From GitHub** (or **Clone from GitHub** on the Projects page) lists the repositories your GitHub sign-in can see (Dashboard → GitHub): yours, those you collaborate on, and your organizations', newest first, with search. Repositories already in your Workspace are marked *in your Workspace as …* and can't be picked again.

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

The token is kept in `secrets.env` with the other keys, and only the two steps that talk to GitHub use it (`gh repo create`, and the push, which goes over HTTPS through `gh` as git's credential helper, so the token is never written to a git config or shown on a command line). **Commands the model runs don't get it**, and with the **sandbox** on they can't read `secrets.env` either, so the model can't push or change your GitHub account. With the sandbox off, an approved command runs with Optimux's permissions and could read it (or use `gh`'s own sign-in): review commands, or turn the sandbox on. Pushing later is up to you.

**Who commits.** The first commit (and commits the model makes, when you approve them) use git's own name and email. Set a different **name and email** in Dashboard → GitHub (saved on their own, with their own **Save**). On a server the Optimux user usually has none, so saving a token fills them in with your GitHub account and its private `…@users.noreply.github.com` address when git has nothing set.

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

### Escalation and fallback

- **Escalation (Simple → Complex)** happens automatically when the Simple model reaches its step limit or keeps repeating the same action. The Complex model receives the whole conversation so far and continues. You can also click **Escalate ↑** yourself.
- **Fallback** handles provider trouble: if a model fails (outage, quota), Optimux retries once, then switches to the other tier for that request. A model that keeps failing goes into a **cooldown** and is skipped until it recovers (see *Models*).

### Pinning and overrides

- `/simple` or `/complex` **pins** the tier for the rest of the chat; the Conductor stops switching. **New chat** unpins.
- Choosing a specific model in the selector under the chat box **overrides** routing for your requests: that model answers, with the Complex step limit, and no automatic switching.

### Steps and turns

A **turn** is one request and its reply. A **step** is one model call inside it: the model calls a command, reads the output, decides the next command, and so on. Each step resends the conversation, so long turns cost more per step. Step limits (in **Dashboard → Execution**) cap how far one request can go before Optimux escalates or asks you.

## Models

### Adding a model

In **Dashboard → Models → Add Model**:

- **Model ID**: the provider prefix and model name, as LiteLLM knows it: `gemini/gemini-3.6-flash`, `anthropic/claude-sonnet-5-5`, `openai/gpt-5`, `ollama/llama3.1:8b`.
- **Display name**: how it appears in menus.
- **Environment key** and **API key** (optional): the variable name (e.g. `GEMINI_API_KEY`) and the key itself. Keys are stored in `~/.optimux/secrets.env`, readable only by you, never in the repository.
- **Prices** (optional): input, output and cached-input prices in USD per million tokens. Leave them empty to use LiteLLM's built-in price list; set them when a model is missing from it or your price differs.

### Each model card

- **Status**: *ready*, *needs key*, *test failed*, or *cooldown* (temporarily skipped after errors).
- **Test** makes a tiny real call (a few tokens) to check the ID and key. A successful test also clears a cooldown.
- **Key** lets you replace the key; **Prices** lets you edit them; the bin deletes the model (its key stays in `secrets.env`).
- **Thinking**: how much the model reasons before answering: *Default* (the provider decides), *Minimal*, *Low*, *Medium* or *High*. More thinking means more output tokens, more cost and more time, and usually better results on hard tasks. A note tells you when LiteLLM doesn't list the model as supporting it.

### Cooldowns

When a provider says your quota is used up, or errors keep coming after a retry, the model goes into **cooldown**: Optimux skips it (and uses the other tier) instead of paying for failing calls. Quota cooldowns last until the provider's retry time (30 minutes when it gives none); other errors one minute. You see them on the model card and in **Dashboard → System**, where you can **Clear** them. A server restart clears all.

### Local models

Ollama models (`ollama/...`) cost nothing per token. They work for simple chat, but small local models are often unreliable at tool use (running commands step by step), so use them with care as executors.

## Commands, approvals and safety

### What the model can do

The model has a terminal tool: it proposes a shell command, you approve it, Optimux runs it in the active project and gives the output back to the model. Commands time out after **30 seconds**, so very long builds or servers should be started by you.

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

### Backing up, starting over, moving

**Back up** by copying the whole folder while the server is stopped, or just `~/.optimux/backups/` (a daily copy of the database) to another disk from time to time. The backups live in the same folder, so they don't protect you against deleting it.

**Starting over.** If `~/.optimux` disappears, the next start rebuilds the structure: a **new access token** (printed in the terminal; the sign-in page asks for it to set a new password), an empty database, default personal and professional memory, the starter skills and the example models. **Everything else is gone for good**: API keys, chats, usage and cost history, memory and its history, your skills, models, routing, budgets and settings. Your projects and their `.optimux-skills/` are untouched. To start fresh without losing anything, **rename** the folder instead of deleting it:

```bash
mv ~/.optimux ~/.optimux.old      # Optimux starts clean; put it back to undo
```

If the folder is deleted while the server is running, Optimux keeps working with a fresh, empty database; restart it to also get a new token and the starter files.

**Moving to another machine.** Stop the server, copy `~/.optimux` (it contains your keys: use a secure channel, e.g. `scp` or an encrypted drive), and start Optimux there. Update the Workspace folder in **Scope** if your projects live elsewhere.

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
The sandbox is on, but bubblewrap is missing or can't create a sandbox (some systems disable unprivileged user namespaces). Install it (`sudo apt install bubblewrap`), then click **check again** in Execution → Sandbox. Commands are refused on purpose rather than run unprotected.

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
