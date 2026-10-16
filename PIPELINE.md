# Optimux Pipeline & AI Operational Guidelines

> **Notice to LLMs & Agents:** This file defines the strict coding style, structural expectations, and operational workflow for the Optimux project. Read and internalize these rules before generating code or commits.

## 1. Project Structure

Optimux relies heavily on decentralized context to feed different LLMs. 

### Documentation Layer
- **`MANIFEST.md`**: Optimux's purpose and design in one page.
- **`README.md`**: What it is, how to install it, how it works; the roadmap.
- **`docs/GUIDE.md`**: The user guide (also shown in the app).
- **`SECURITY.md`**: How to report a vulnerability.
- **`commit-template.txt`**: **[CRITICAL]** The strict convention for Optimux commits.

### Application Layer (Code & Agent Sidecars)
Optimux pairs every core file with an `_AGENT.md` file containing its context.
```text
├── .github/workflows/tests.yml  (GitHub Actions: pytest on Python 3.10 and 3.13, shellcheck)
├── .github/ISSUE_TEMPLATE/      (bug report and feature request forms; config.yml links Q&A, Ideas and private security reporting)
├── index.html                   (Vue 3 SPA page and templates)
├── index.html_AGENT.md
├── web/                         (app.js, boot.js, login page, styles, logo.svg, pinned libraries in vendor/)
├── deploy/                      (server kit: systemd unit, Caddyfile, env example, AppArmor profile)
├── install.sh                   (one-line installer, local or server; library of the optimux command)
├── install.sh_AGENT.md
├── scripts/optimux              (the optimux command: start/stop/status/update/uninstall…)
├── scripts/build-css.sh         (rebuilds web/app.css with Tailwind)
├── docs/
│   └── GUIDE.md                 (User guide, shown in Dashboard → Guide)
├── optimux_backend/
│   ├── __init__.py              (runs first: settings other libraries read on import)
│   ├── __init__.py_AGENT.md
│   ├── main.py                  (FastAPI orchestrator & WebSocket)
│   ├── main.py_AGENT.md
│   ├── admin.py                 (setup commands for the installer: password, keys, models, Workspace, GitHub)
│   ├── admin.py_AGENT.md
│   └── core/
│       ├── agents.py            (System prompts: router & executor)
│       ├── auth.py              (Password sign-in, sessions, two-step codes, rate limits)
│       ├── auth.py_AGENT.md
│       ├── agents.py_AGENT.md
│       ├── budgets.py           (Monthly cost budgets per project)
│       ├── budgets.py_AGENT.md
│       ├── catalog.py           (Model catalog: providers, models, prices; opt-in refresh)
│       ├── catalog.py_AGENT.md
│       ├── ratelimits.py        (Rate limits of your keys, learned from replies and errors)
│       ├── ratelimits.py_AGENT.md
│       ├── config_manager.py    (Env & API key handling)
│       ├── curator.py           (Project memory curator & history)
│       ├── curator.py_AGENT.md
│       ├── config_manager.py_AGENT.md
│       ├── executor.py          (Async bash subprocess, scoped to project)
│       ├── exports.py           (Encrypted snapshot downloads, age)
│       ├── exports.py_AGENT.md
│       ├── executor.py_AGENT.md
│       ├── llm_client.py        (LiteLLM router & Tool schemas)
│       ├── llm_client.py_AGENT.md
│       ├── memory_manager.py    (Decoupled state saved to ~/.optimux)
│       ├── memory_manager.py_AGENT.md
│       ├── paths.py             (~/.optimux layout & one-time file moves)
│       ├── paths.py_AGENT.md
│       ├── providers.py         (Provider error classes & model cooldowns)
│       ├── providers.py_AGENT.md
│       ├── sandbox.py           (bubblewrap sandbox for model-run commands)
│       ├── sandbox.py_AGENT.md
│       ├── security.py          (Auth, origin, scope & env-key validation)
│       ├── skills.py            (Skills: discovery, loading, editing, starters)
│       ├── snapshots.py         (Data and Workspace snapshots kept on this machine)
│       ├── snapshots.py_AGENT.md
│       ├── skills.py_AGENT.md
│       ├── sessions.py          (Stored chats: state, event log, history)
│       ├── sessions.py_AGENT.md
│       ├── store.py             (SQLite store: usage, sessions; backups)
│       ├── workspace.py         (Project tools: list, create (form), switch, archive; restore and delete from the Dashboard; GitHub token and gh)
│       ├── project_records.py   (Deleted projects: kept records listed as deleted, or forgotten)
│       ├── project_records.py_AGENT.md
│       ├── workspace.py_AGENT.md
│       ├── store.py_AGENT.md
│       ├── security.py_AGENT.md
│       ├── usage.py             (Token & cost metering per LLM call)
│       └── usage.py_AGENT.md
├── tests/                       (pytest suite covering the core modules)
└── requirements.txt
```

## 2. Commit Standards (Mandatory)

All commits made to the Optimux repository **MUST strictly follow** the format defined in `commit-template.txt`. 

- **Structure:** `[ TYPE ] Scope: Description`
- **Files Modified:** Every commit body must begin with a `FILES MODIFIED:` section listing the exact paths changed.
- **Detailed Description:** A detailed description of the changes made in this commit and the reasoning behind them.
- **Optimux Specific Scopes:** Use the localized backend/frontend scopes defined in the template (e.g., `ROUTER`, `LLM`, `EXECUTOR`, `MEMORY`, `CONFIG`, `UI`, `STATE`, `WS`).
*Agents: Every single line in the commit must be broke into maximum of 70 characters.
*Agents: Do not guess the commit format. Read `commit-template.txt` if you are unsure.*

## 3. Maintenance of AGENT Files

Optimux uses `_AGENT.md` files so that smaller local models (like `phi3`) can quickly read and compress context before querying expensive models. 

**The Golden Rule:** 
For every single modification made to a Python script or HTML file, you **MUST** evaluate if the logic, dependencies, or data structures changed. If they did, you **MUST update the corresponding `_AGENT.md` file immediately** in the same commit. 

If you create a new module, you must scaffold its `_AGENT.md` file alongside it detailing its purpose, state, and relations to `main.py` or `core/` modules.

## 4. Optimux Style Guide
- **Backend:** Python 3.10+ using `FastAPI`. All shell executions must go through `executor.py` asynchronously. State must never be saved to the active Git directory: every file Optimux keeps lives under `~/.optimux` (`OPTIMUX_HOME`), with its location taken from `paths.py`. Secrets go only in `~/.optimux/secrets.env`.
- **Frontend:** Vue 3 (pinned copy in `web/vendor/`), styled with TailwindCSS compiled to `web/app.css` by `scripts/build-css.sh`. No bundler (Vite/Webpack). Templates live in `index.html`, logic in `web/app.js`. No inline scripts or styles and no CDNs: the Content Security Policy forbids them.

## 5. Feature Documentation (TODOs)
For every major feature implementation, the process is documented in a local `todos/` directory (git-ignored: it is the maintainer's working notes and isn't published) using three sequential files:
1. **`TODO-[number]-01-[feature-name].md`**: Contains the initial feature request and description.
2. **`TODO-[number]-02-implementation-plan.md`**: The AI-generated implementation plan detailing the architecture changes before coding begins.
3. **`TODO-[number]-03-walkthrough.md`**: The final walkthrough documenting what was actually built, tested, and validated.
These files serve as an immutable log of the project's evolution.
