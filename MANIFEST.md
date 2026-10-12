# Optimux Manifest

## Purpose
Optimux is a self-hosted AI coding workspace that routes each task to the cheapest capable model, tracks every token and what it costs, and asks before running commands. Every call is measured, and the user decides the balance between **cost, speed and reliability**. It is built for coding, but not limited to it: it answers anything a chat AI can, and works on any files in a project folder (notes, documents, data).

## Principles
- **Cost is a feature.** Every token sent must earn its place; features cost nothing until they are used.
- **The user decides.** Commands, paid one-off actions and risky changes are shown, explained and confirmed. Defaults are safe.
- **Nothing is lost.** Chats, usage and memory history survive restarts and updates. Files are moved aside, never deleted.
- **Nothing leaks.** Keys stay in `~/.optimux/secrets.env`, never in repositories, prompts or the environment of model-run commands.
- **Your machine, your code.** Optimux runs on your computer or your own server and works on your own repositories.

## How it works
1. **The Conductor**, one small and cheap call per message, decides whether the request is simple or complex.
2. **The Simple model** handles most work. **The Complex model** takes over for hard tasks, or when the Simple one gets stuck (escalation).
3. Models are reached through **LiteLLM** (Gemini, Claude, OpenAI, local models and more), with fallback and cooldowns when a provider fails.
4. The executor model can call **tools**: a shell command (after approval, in a bubblewrap **sandbox** that can change only the active project), memory updates, skills, and Workspace actions (create, switch, archive projects).
5. Every call is **metered** (tokens, cache, thinking, cost, time) and checked against the project's **budget**.

## The product
- **Web interface:** a single-page Vue 3 app: chat, history, a Dashboard (usage, budgets, models, routing, memory, skills, projects, GitHub, security) and the in-app Guide.
- **Server:** FastAPI with a WebSocket for the chat; SQLite for chats and usage; password sign-in with optional two-step codes.
- **Workspace:** one folder whose sub-folders are projects; projects can be created from a form (with an optional GitHub repository) or cloned from GitHub.
- **Memory:** personal, professional and per-project Markdown files; the project file is kept up to date automatically, under a size cap.
- **Skills:** reusable instructions run with `/name`.
- **Installer:** one line for a computer or a VPS (HTTPS with Caddy, a hardened systemd service), plus the `optimux` command.
- **Data:** everything Optimux keeps lives in `~/.optimux` (`OPTIMUX_HOME`), outside the projects.

## Who it is for
Technical founders, solo developers and power users who use AI coding assistants every day and want control over model cost, routing and command execution.

## Where to read more
- [optimux.si](https://optimux.si): the website.
- [README.md](README.md): features, install, architecture, roadmap.
- [docs/GUIDE.md](docs/GUIDE.md): the user guide.
- [PIPELINE.md](PIPELINE.md): file tree, coding rules and workflow.
- [SECURITY.md](SECURITY.md): reporting a vulnerability.
