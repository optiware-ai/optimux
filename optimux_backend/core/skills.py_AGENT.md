# skills.py Agent Details

## File Overview
`skills.py` implements skills: reusable instructions (and optional scripts) that cost almost nothing until a task needs them.

## Format
A folder with `SKILL.md`. Front matter holds simple `key: value` lines:
- `name` and `description` (required: a skill without a description is skipped, because the model couldn't tell when to use it).
- Optional `mode` (`manual`, the default, or `auto`), `tier` (simple or complex), `max_steps` (1 to 100) and `thinking` (minimal, low, medium or high).

## Modes (Feature 19)
- **manual** (default, also when the line is missing): never listed in prompts, so it costs nothing while idle. It runs when the user types `/<name>` or `/skill <name>`.
- **auto:** also listed in every prompt (catalog for the Conductor and executor, plus the `load_skill` tool) so it can be picked automatically, at about 30 tokens per skill per model call.
- The global setting `skills_mode` (`GLOBAL_MODES`: off, manual, auto) decides whether auto skills are listed; `off` also refuses `/name`.

The markdown instructions follow the front matter, and any other files in the folder (scripts, templates) are listed when the skill loads.

## Locations
- **Global:** `~/.optimux/skills/<name>/` (`paths.SKILLS_DIR`).
- **Project:** `<project>/.optimux-skills/<name>/`. A project skill overrides a global one with the same name. `project_skills_dir(project)` returns that folder (v0.2 dropped the old `.codemux-skills` name).
- **Names:** lowercase letters, digits and `-` (`NAME_RE`).

## Core Functionality
- **`discover(project_dir)`:** usable skills by name (global first, then project).
- **`listed(skills, global_mode)`:** the auto skills that go into prompts (empty unless the global mode is auto).
- **`catalog_tokens(skills)`:** rough per-call prompt cost of listing them (0 when none).
- **`parse_command(text, skills)`:** `/name rest` or `/skill name rest` → `(name, rest)`; only existing skill names count (not `RESERVED` commands), so a path such as `/home/...` stays a message.
- **`set_mode(scope, name, mode, project_dir)`:** rewrites (or inserts after `description:`) the `mode:` line of `SKILL.md`.
- **`catalog_text(skills)`:** the compact `- name: description` list that goes into the Conductor's and executor's prompts (about 30 tokens per skill).
- **`load(name, project_dir)`:** full instructions plus the folder and its files (used by `load_skill` and when the Conductor picks a skill).
- **`read_raw` / `save_raw` / `remove`:** the dashboard editor. Saving requires a description. Remove moves the folder to `~/.optimux/legacy/skills/` instead of deleting it.
- **`template(name)`:** a starting `SKILL.md` (with `mode: manual`). The starters are manual too.
- **`seed_starters()`:** adds `commit-message` and `run-tests` when the global folder is empty (first start).
- **`LOAD_SKILL_TOOL`:** the tool definition.

## Links & Dependencies
- Used by `llm_client.py` (the Conductor picks a skill) and `main.py` (prompts, tools, `load_skill`, overrides, metering, `/api/skills*`, the `Save as skill` draft).
- `paths.py` gives the global folder and the legacy folder.
