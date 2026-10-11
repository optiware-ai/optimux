"""Skills: reusable instructions (and optional scripts) loaded only when needed.

A skill is a folder with a SKILL.md:

    ---
    name: commit-message
    description: Write a commit message for staged changes, following the repo template.
    mode: manual            # optional: manual (default) | auto
    tier: simple            # optional: simple | complex
    max_steps: 6            # optional: step limit while the skill is active
    thinking: default       # optional: default | minimal | low | medium | high
    ---
    Instructions in markdown...

Modes (Feature 19): a manual skill (the default) costs nothing while idle; it
runs only when the user types /<name> (or /skill <name>). An auto skill is also
listed in every prompt, so the Conductor or the model can pick it, which costs
about 30 tokens per skill on every model call. The global skills_mode setting
can turn skills off, force manual for all, or allow auto skills.

Global skills live in ~/.optimux/skills/<name>/, project skills in
<project>/.optimux-skills/<name>/ (a project skill overrides a global one with
the same name). Only names and descriptions go into prompts (about 30 tokens
each); the instructions load when the Conductor picks the skill or the model
calls load_skill. Scripts in the folder are run through execute_bash, like
any command, and need the same approval.
"""
import os
import re
import shutil
import time

from optimux_backend.core import paths

PROJECT_SKILLS_DIR = ".optimux-skills"
OLD_PROJECT_SKILLS_DIR = ".codemux-skills"  # before the rename to Optimux; still read


def project_skills_dir(project_dir: str) -> str:
    """The project's skills folder: .optimux-skills, or .codemux-skills in a
    project that has only the old one (it is used as it is, never renamed)."""
    new = os.path.join(project_dir, PROJECT_SKILLS_DIR)
    old = os.path.join(project_dir, OLD_PROJECT_SKILLS_DIR)
    return old if not os.path.lexists(new) and os.path.isdir(old) else new
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
TIERS = ("simple", "complex")
MODES = ("manual", "auto")
GLOBAL_MODES = ("off", "manual", "auto")
RESERVED = {"new", "simple", "complex", "skill"}   # slash commands that aren't skills
THINKING = ("default", "minimal", "low", "medium", "high")
MAX_DESCRIPTION = 200


class SkillError(Exception):
    pass


def validate_name(name: str) -> str:
    name = (name or "").strip()
    if not NAME_RE.match(name):
        raise SkillError(f"'{name}' isn't a valid skill name: lowercase letters, digits and '-', up to 64 characters.")
    return name


def parse(text: str) -> tuple[dict, str]:
    """Front matter (simple key: value lines) and body."""
    meta, body = {}, text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            for line in text[3:end].strip().splitlines():
                if ":" in line and not line.lstrip().startswith("#"):
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.split(" #", 1)[0].strip().strip('"').strip("'")
            body = text[end + 4:].lstrip("\n")
    return meta, body


def _clean_meta(meta: dict, folder_name: str) -> dict:
    out = {"name": meta.get("name") or folder_name, "description": (meta.get("description") or "")[:MAX_DESCRIPTION],
           "mode": meta.get("mode") if meta.get("mode") in MODES else "manual"}
    if meta.get("tier") in TIERS:
        out["tier"] = meta["tier"]
    if meta.get("thinking") in THINKING and meta["thinking"] != "default":
        out["thinking"] = meta["thinking"]
    try:
        steps = int(meta.get("max_steps", 0))
        if 1 <= steps <= 100:
            out["max_steps"] = steps
    except (TypeError, ValueError):
        pass
    return out


def roots(project_dir: str | None) -> list[tuple[str, str]]:
    """(scope, folder) pairs, global first so project skills override."""
    out = [("global", paths.SKILLS_DIR)]
    if project_dir:
        out.append(("project", project_skills_dir(project_dir)))
    return out


def discover(project_dir: str | None = None) -> dict[str, dict]:
    """All usable skills by name. A skill without a description is skipped
    (the model couldn't tell when to use it)."""
    found: dict[str, dict] = {}
    for scope, root in roots(project_dir):
        if not os.path.isdir(root):
            continue
        for folder in sorted(os.listdir(root)):
            path = os.path.join(root, folder, "SKILL.md")
            if not os.path.isfile(path) or not NAME_RE.match(folder):
                continue
            with open(path) as f:
                meta, body = parse(f.read())
            info = _clean_meta(meta, folder)
            if not info["description"]:
                continue
            info.update(scope=scope, folder=os.path.join(root, folder), path=path, name=folder)
            found[folder] = info
    return found


def listed(skills: dict[str, dict], global_mode: str) -> dict[str, dict]:
    """The skills that go into prompts: auto skills, only when the global mode is auto."""
    if global_mode != "auto":
        return {}
    return {n: s for n, s in skills.items() if s.get("mode") == "auto"}


def catalog_tokens(skills: dict[str, dict]) -> int:
    """Rough prompt cost of listing these skills (catalog, instruction line and the
    load_skill tool), per model call. 0 when nothing is listed."""
    if not skills:
        return 0
    return len(catalog_text(skills)) // 4 + 60


def parse_command(text: str, skills: dict[str, dict]) -> tuple[str | None, str]:
    """'/run-tests only unit' or '/skill run-tests only unit' -> ('run-tests', 'only unit').
    Only names of existing skills count, so '/home/me/file' stays a normal message."""
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None, text
    head, _, rest = stripped.partition(" ")
    name = head[1:].lower()
    if name == "skill":
        name, _, rest = rest.strip().partition(" ")
        name = name.lower()
    if name in RESERVED or name not in skills:
        return None, text
    return name, rest.strip()


def set_mode(scope: str, name: str, mode: str, project_dir: str | None) -> None:
    """Change a skill's mode by rewriting the mode line of its front matter."""
    if mode not in MODES:
        raise SkillError("Mode must be 'manual' or 'auto'.")
    text = read_raw(scope, name, project_dir)
    if not text.startswith("---") or text.find("\n---", 3) == -1:
        raise SkillError("SKILL.md has no front matter.")
    end = text.find("\n---", 3)
    head, tail = text[:end], text[end:]
    lines = head.split("\n")
    for i, line in enumerate(lines):
        if line.split(":", 1)[0].strip() == "mode" and not line.lstrip().startswith("#"):
            lines[i] = f"mode: {mode}"
            break
    else:
        at = next((i + 1 for i, l in enumerate(lines) if l.startswith("description:")), 1)
        lines.insert(at, f"mode: {mode}")
    with open(os.path.join(_folder(scope, name, project_dir), "SKILL.md"), "w") as f:
        f.write("\n".join(lines) + tail)


def catalog_text(skills: dict[str, dict]) -> str:
    """The compact list that goes into prompts."""
    return "\n".join(f"- {s['name']}: {s['description']}" for s in skills.values())


def load(name: str, project_dir: str | None = None) -> dict:
    """Full instructions plus the files in the skill's folder (for load_skill)."""
    skills = discover(project_dir)
    if name not in skills:
        raise SkillError(f"No skill named '{name}'. Available: {', '.join(skills) or 'none'}.")
    s = skills[name]
    with open(s["path"]) as f:
        _, body = parse(f.read())
    files = []
    for dirpath, _, filenames in os.walk(s["folder"]):
        for fn in filenames:
            rel = os.path.relpath(os.path.join(dirpath, fn), s["folder"])
            if rel != "SKILL.md":
                files.append(rel)
    return {**{k: v for k, v in s.items() if k != "path"}, "instructions": body, "files": sorted(files)}


def read_raw(scope: str, name: str, project_dir: str | None) -> str:
    folder = _folder(scope, name, project_dir)
    path = os.path.join(folder, "SKILL.md")
    if not os.path.isfile(path):
        raise SkillError(f"No {scope} skill named '{name}'.")
    with open(path) as f:
        return f.read()


def save_raw(scope: str, name: str, text: str, project_dir: str | None) -> dict:
    """Write SKILL.md (creating the folder). The text must have a description."""
    name = validate_name(name)
    meta, _ = parse(text)
    if not (meta.get("description") or "").strip():
        raise SkillError("SKILL.md needs a 'description:' line in its front matter; it's how the model knows when to use it.")
    folder = _folder(scope, name, project_dir)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "SKILL.md"), "w") as f:
        f.write(text if text.endswith("\n") else text + "\n")
    return _clean_meta(meta, name)


def remove(scope: str, name: str, project_dir: str | None) -> str:
    """Move a skill folder to ~/.optimux/legacy/skills/ (never deleted outright)."""
    folder = _folder(scope, name, project_dir)
    if not os.path.isdir(folder):
        raise SkillError(f"No {scope} skill named '{name}'.")
    dest = os.path.join(paths.LEGACY_DIR, "skills", f"{scope}-{name}-{time.strftime('%Y%m%d-%H%M%S')}")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.move(folder, dest)
    return dest


def _folder(scope: str, name: str, project_dir: str | None) -> str:
    name = validate_name(name)
    if scope == "global":
        return os.path.join(paths.SKILLS_DIR, name)
    if scope == "project":
        if not project_dir:
            raise SkillError("No active project for a project skill.")
        return os.path.join(project_skills_dir(project_dir), name)
    raise SkillError("Scope must be 'global' or 'project'.")


def template(name: str, description: str = "") -> str:
    return (f"---\nname: {name}\ndescription: {description or 'One line: what this skill does and when to use it.'}\n"
            "mode: manual            # manual: runs only when you type /name; auto: listed in every prompt (costs tokens)\n"
            "# tier: simple          # optional: simple | complex\n# max_steps: 8          # optional\n"
            "# thinking: default     # optional: default | minimal | low | medium | high\n---\n"
            "## Steps\n1. ...\n\n## Rules\n- ...\n")


STARTERS = {
    "commit-message": (
        "---\nname: commit-message\ndescription: Write a git commit message for the current changes, "
        "following the repository's commit template if it has one.\nmode: manual\ntier: simple\nmax_steps: 6\n---\n"
        "## Steps\n1. Run `git status --short` and `git diff --staged --stat` (or `git diff --stat` if nothing is staged).\n"
        "2. If `commit-template.txt` exists (or `git config commit.template` names a file), read it and follow its format exactly.\n"
        "3. Write the message: a summary line under 70 characters, then a body explaining what changed and why.\n\n"
        "## Rules\n- Don't run `git commit` unless the user asks; show the message.\n"
        "- Keep every line at most 70 characters unless the template says otherwise.\n"),
    "run-tests": (
        "---\nname: run-tests\ndescription: Find and run the project's tests, then summarize failures with "
        "their likely cause.\nmode: manual\ntier: simple\nmax_steps: 6\n---\n"
        "## Steps\n1. Detect the test runner from the files present (pytest for Python, `npm test` for Node, "
        "`cargo test`, `go test ./...`).\n2. Run it once, quietly (e.g. `pytest -q`).\n"
        "3. Report: passed/failed counts, then each failure with file, test name and the likely cause.\n\n"
        "## Rules\n- Don't change code unless the user asks.\n"
        "- Don't paste the full output; the user already sees it.\n"),
}


def seed_starters() -> list[str]:
    """On a fresh install (empty global skills folder), add the starter skills."""
    os.makedirs(paths.SKILLS_DIR, exist_ok=True)
    if any(os.scandir(paths.SKILLS_DIR)):
        return []
    added = []
    for name, text in STARTERS.items():
        os.makedirs(os.path.join(paths.SKILLS_DIR, name))
        with open(os.path.join(paths.SKILLS_DIR, name, "SKILL.md"), "w") as f:
            f.write(text)
        added.append(name)
    return added


LOAD_SKILL_TOOL = {
    "type": "function",
    "function": {
        "name": "load_skill",
        "description": "Load a skill's full instructions (and list its files) before doing a task it covers.",
        "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
    },
}
