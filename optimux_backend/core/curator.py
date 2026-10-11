"""Memory curator: keeps each project's memory up to date from its chats.

When a chat ends (New chat, opening another chat, switching project, closing
the page, or after some idle minutes), a cheap model reads what happened in
the chat since it was last curated and rewrites the project's memory file
(~/.optimux/memory/projects/<name>.md) with durable facts only, in fixed
sections and under a size cap: project memory is part of every prompt, so
every character costs tokens on every call.

Every change to project memory (curator, dashboard edits, the model's
update_markdown_memory tool, undo) is stored as a version with the text
before and after, so any change can be inspected and undone. Personal and
professional memory are never touched here.
"""
import difflib
import os
import time

from optimux_backend.core import store

SECTIONS = ("Overview", "Stack", "Commands", "Conventions", "Decisions", "Known issues")
MAX_TOKENS = 1500
MAX_CHARS = MAX_TOKENS * 4          # rough chars-per-token estimate
TRANSCRIPT_CHARS = 16000            # how much of the chat the curator reads
NO_CHANGES = "NO_CHANGES"


# ------------------------------------------------------------------ files and versions

def memory_path(project: str) -> str:
    from optimux_backend.core.memory_manager import PROJECTS_DIR
    from optimux_backend.core.security import validate_project_name
    validate_project_name(project)
    return os.path.join(PROJECTS_DIR, f"{project}.md")


def read_memory(project: str) -> str:
    path = memory_path(project)
    if not os.path.exists(path):
        return ""
    with open(path) as f:
        return f.read()


def record_version(project: str, source: str, before: str, after: str,
                   summary: str = "", session_id: str | None = None, cost: float | None = None) -> int | None:
    """Store one change (with the model call's cost for automatic updates).
    Nothing is recorded when the text didn't change."""
    if before == after:
        return None
    conn = store.connect()
    try:
        with conn:
            cur = conn.execute(
                "INSERT INTO memory_versions (project, ts, source, session_id, summary, before, after, cost) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (project, time.time(), source, session_id, summary, before, after, cost))
        return cur.lastrowid
    finally:
        conn.close()


def write_memory(project: str, content: str, source: str, summary: str = "",
                 session_id: str | None = None, cost: float | None = None) -> int | None:
    """Replace the project's memory file and record the change."""
    from optimux_backend.core.memory_manager import init_memory
    init_memory()
    before = read_memory(project)
    with open(memory_path(project), "w") as f:
        f.write(content)
    return record_version(project, source, before, content, summary, session_id, cost)


def _diff(before: str, after: str) -> str:
    return "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                        "before", "after", n=1))


def list_versions(project: str, limit: int = 50) -> list[dict]:
    conn = store.connect()
    try:
        rows = conn.execute("SELECT * FROM memory_versions WHERE project = ? ORDER BY id DESC LIMIT ?",
                            (project, limit)).fetchall()
    finally:
        conn.close()
    return [{"id": r["id"], "ts": r["ts"], "source": r["source"], "session_id": r["session_id"],
             "summary": r["summary"], "diff": _diff(r["before"], r["after"]),
             "chars": len(r["after"]), "cost": r["cost"]} for r in rows]


def undo(version_id: int) -> dict:
    """Put back the text from before a change (itself recorded as an 'undo' version)."""
    conn = store.connect()
    try:
        row = conn.execute("SELECT project, before FROM memory_versions WHERE id = ?", (version_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        raise KeyError(version_id)
    new_id = write_memory(row["project"], row["before"], "undo", f"Undid change #{version_id}")
    return {"project": row["project"], "version_id": new_id}


# ------------------------------------------------------------------ what to read

def curated_turns(session_id: str) -> int:
    conn = store.connect()
    try:
        row = conn.execute("SELECT curated_turns FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return row["curated_turns"] if row else 0
    finally:
        conn.close()


def mark_curated(session_id: str, turns: int) -> None:
    conn = store.connect()
    try:
        with conn:
            conn.execute("UPDATE sessions SET curated_turns = ? WHERE id = ?", (turns, session_id))
    finally:
        conn.close()


def transcript(events: list[dict], skip_turns: int = 0) -> str:
    """A compact view of the chat: requests, commands with short output, answers.
    The first `skip_turns` turns (already curated) are left out."""
    lines, turn = [], 0
    for e in events:
        kind = e.get("type")
        if kind in ("prompt", "escalate"):
            turn += 1
        if turn <= skip_turns:
            continue
        if kind == "prompt":
            lines.append(f"USER: {e.get('content', '')}")
        elif kind == "command_output":
            out = (e.get("output") or "").strip()
            lines.append(f"COMMAND: {e.get('command', '')} (exit {e.get('exit_code')})"
                         + (f"\n  {out[:300]}" if out else ""))
        elif kind == "token":
            lines.append(f"ASSISTANT: {(e.get('content') or '')[:1200]}")
        elif kind == "error":
            lines.append(f"ERROR: {e.get('content', '')}")
    text = "\n".join(lines)
    return text[-TRANSCRIPT_CHARS:]


def build_prompt(project: str, current: str, chat: str) -> str:
    sections = ", ".join(SECTIONS)
    return (
        f"You maintain the memory file of the software project '{project}'. Coding assistants read it at "
        "the start of every request, so it must contain only durable, useful facts and stay short.\n\n"
        "Update it with what this chat shows: how to build, run and test the project; its stack and "
        "structure; conventions; decisions made and why; known issues. Ignore one-off details, the "
        "conversation itself, anything temporary, guesses that weren't confirmed, and secrets "
        "(API keys, tokens, passwords).\n\n"
        f"Rules:\n- Use exactly these markdown sections, in this order, leaving out empty ones: {sections}. "
        f"Start with '# {project}'.\n"
        "- Keep facts from the current memory unless the chat shows they changed; merge duplicates.\n"
        f"- Stay under {MAX_TOKENS} tokens (about {MAX_CHARS // 6} words): condense rather than append.\n"
        "- Write short bullet points.\n"
        f"- If the chat adds nothing durable, reply with exactly {NO_CHANGES}.\n"
        "- Otherwise reply with the complete updated file and nothing else, then a last line "
        "'SUMMARY: <what changed, one sentence>'.\n\n"
        f"CURRENT MEMORY:\n{current or '(empty)'}\n\nCHAT:\n{chat}"
    )


def parse_reply(text: str) -> tuple[str | None, str]:
    """(new memory or None for no changes, summary)."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip() if "\n" in text else ""
    if not text or text.startswith(NO_CHANGES):
        return None, "No durable changes."
    summary = ""
    lines = text.splitlines()
    if lines and lines[-1].upper().startswith("SUMMARY:"):
        summary = lines[-1].split(":", 1)[1].strip()
        text = "\n".join(lines[:-1]).rstrip()
    return text + "\n", summary or "Updated from a chat."


def acceptable(new: str) -> str | None:
    """Why a curator result is rejected, or None if it's fine."""
    if not new.lstrip().startswith("#"):
        return "the result wasn't a markdown memory file"
    if len(new) > MAX_CHARS * 1.25:
        return f"the result was too long ({len(new)} characters, limit about {MAX_CHARS})"
    return None
