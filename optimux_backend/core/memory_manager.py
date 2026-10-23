import os
import json
import tempfile
from optimux_backend.core.config_manager import get_settings
from optimux_backend.core.security import ScopeError, validate_project_name

from optimux_backend.core import paths

GLOBAL_DIR = paths.MEMORY_DIR
PROJECTS_DIR = os.path.join(GLOBAL_DIR, "projects")

PERSONAL_MD = os.path.join(GLOBAL_DIR, "personal.md")
PROFESSIONAL_MD = os.path.join(GLOBAL_DIR, "professional.md")

DEFAULT_PERSONAL = """# Personal Memory
This file contains personal preferences, name, personality, likes, and dislikes.
"""

DEFAULT_PROFESSIONAL = """# Professional Memory
This file contains coding style, commit habits, and general architecture rules.
"""

DEFAULT_PROJECT = """# Project Memory
This file contains project-specific rules, architecture, and context.
"""

# Memory is sent with every request, so a pasted log or document would make
# every call expensive (v0.5.2, IM-04). About 5,000 tokens per file at most.
MEMORY_MAX_CHARS = 20_000


class MemoryTooLarge(ValueError):
    """The text is over MEMORY_MAX_CHARS; the message says why that matters."""


def check_size(content: str, what: str) -> None:
    n = len(content or "")
    if n > MEMORY_MAX_CHARS:
        raise MemoryTooLarge(
            f"{what} memory would be {n:,} characters; the limit is {MEMORY_MAX_CHARS:,}. Memory is sent with every "
            f"request (about {n // 4:,} tokens each time), so keep it to the facts that matter and move long "
            "texts into project files.")


def write_atomic(path: str, content: str) -> None:
    """Write through a temporary file and rename it over the target, so a crash
    or a full disk never leaves a cut or empty memory file."""
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".memory-", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        if os.path.exists(path):
            os.chmod(tmp, os.stat(path).st_mode & 0o777)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def init_memory():
    """Initialize centralized markdown memory directories and files."""
    if not os.path.exists(PROJECTS_DIR):
        os.makedirs(PROJECTS_DIR, exist_ok=True)
    
    if not os.path.exists(PERSONAL_MD):
        write_atomic(PERSONAL_MD, DEFAULT_PERSONAL)

    if not os.path.exists(PROFESSIONAL_MD):
        write_atomic(PROFESSIONAL_MD, DEFAULT_PROFESSIONAL)

def _read_md(filepath: str, default: str) -> str:
    try:
        with open(filepath, 'r') as f:
            return f.read()
    except FileNotFoundError:
        return default

def get_memory_context() -> str:
    """Retrieve centralized markdown context."""
    init_memory()
    personal = _read_md(PERSONAL_MD, DEFAULT_PERSONAL)
    professional = _read_md(PROFESSIONAL_MD, DEFAULT_PROFESSIONAL)
    
    context = personal + "\n\n" + professional + "\n\n"
    
    settings = get_settings()
    active_project = settings.get("active_project", "")
    if active_project:
        try:
            validate_project_name(active_project)
        except ScopeError:
            return context  # skip project memory rather than read outside PROJECTS_DIR
        project_md = os.path.join(PROJECTS_DIR, f"{active_project}.md")
        project_content = _read_md(project_md, DEFAULT_PROJECT)
        context += f"## Active Project: {active_project}\n{project_content}\n"
        
    return context

def update_memory(file_type: str, content: str):
    """Append content to a specific memory file."""
    init_memory()
    
    if file_type == "personal":
        filepath = PERSONAL_MD
    elif file_type == "professional":
        filepath = PROFESSIONAL_MD
    elif file_type == "project":
        settings = get_settings()
        active_project = settings.get("active_project", "")
        if not active_project:
            return "Error: No active project is set. Cannot update project memory."
        try:
            validate_project_name(active_project)
        except ScopeError as e:
            return f"Error: {e}"
        filepath = os.path.join(PROJECTS_DIR, f"{active_project}.md")
    else:
        return "Error: Invalid file_type. Must be 'personal', 'professional', or 'project'."
        
    before = _read_md(filepath, "")
    after = before + "\n" + content + "\n"
    try:
        check_size(after, file_type.capitalize())
    except MemoryTooLarge as e:
        return f"Error: {e} Nothing was saved; tell the user."
    write_atomic(filepath, after)
    if file_type == "project":
        # project memory history (Feature 17): the model's own additions can be undone too
        from optimux_backend.core import curator
        curator.record_version(active_project, "model", before, after, "Added by the model during a chat")

    return f"Successfully updated {file_type} memory."

# Provide an empty session for legacy compatibility if anything still imports it
def log_command_to_session(command: str, exit_code: int):
    pass
