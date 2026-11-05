"""The scout (v0.8): before a Complex task, the Simple model looks around the
project with read-only tools and writes a short brief; the Complex model starts
from it instead of spending its own (5-20x dearer) calls on ls, cat and grep.

In an agent loop every call re-reads the whole conversation so far, so a search
phase on the Complex model pays its input price again on each step. The scout
does that phase on the cheap model, within a cap.

The scout only reads, in Python, never through a shell: nothing from the project
runs, nothing needs approval, and the same path checks as the file tools apply
(inside the project, never .git, never Optimux's own data, links followed and
checked). Its job is factual (which files, what they say, what is missing);
deciding what is wrong and what to do stays with the Complex model.

run() returns the brief and the calls made; provider errors are raised for the
caller, which then starts the Complex model without a brief.
"""
import asyncio
import json
import os
import re
import time

from optimux_backend.core import files as filetools
from optimux_backend.core import planner

MAX_TOOL_CALLS = 8           # reads, searches and listings in all
MAX_PROMPT_TOKENS = 60_000   # input tokens in all: past this the scout must write the brief
CALL_TIMEOUT = 60            # seconds per model call
READ_LINES = 200             # lines one read_file returns at most
READ_CHARS = 12_000          # and characters
LIST_MAX = 200               # paths one list_files returns
SEARCH_MAX = 40              # matches one search_files returns
SEARCH_FILES = 2_000         # files one search looks at
SEARCH_BYTES = 300_000       # files larger than this aren't searched
BRIEF_CHARS = 8_000          # the brief handed to the Complex model, at most
FILES_SHOWN = 150            # project files listed in the scout's first message
# Waits before trying a call again after a passing error (Gemini's 503 "overloaded",
# a rate limit, a dropped connection). Not after a timeout: that already took CALL_TIMEOUT.
RETRY_DELAYS = (2.0, 5.0)

TOOLS = [
    {"type": "function", "function": {
        "name": "list_files",
        "description": "List the files under a folder of the project (paths relative to the project).",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "Folder relative to the project; empty for the whole project."}},
            "required": []}}},
    {"type": "function", "function": {
        "name": "read_file",
        "description": f"Read a text file of the project, with line numbers. At most {READ_LINES} lines per call.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "File path relative to the project."},
            "start_line": {"type": "integer", "description": "First line to read (1 = the start)."}},
            "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "search_files",
        "description": "Find files whose path, or lines whose text, contains a text (not case-sensitive).",
        "parameters": {"type": "object", "properties": {
            "text": {"type": "string", "description": "The text to look for."},
            "path": {"type": "string", "description": "Folder to search, relative to the project; empty for all."}},
            "required": ["text"]}}},
]


def _folder(root: str, rel: str) -> tuple[str, str]:
    """(real folder, clean relative path) inside the project, or ValueError."""
    rel = (rel or "").strip().strip("/")
    if not rel or rel == ".":
        return os.path.realpath(root), ""
    if "\0" in rel or os.path.isabs(rel):
        raise ValueError("use a folder path relative to the project")
    real_root = os.path.realpath(root)
    real = os.path.realpath(os.path.join(real_root, rel))
    if not filetools._inside(real, real_root):
        raise ValueError(f"{rel} is outside the project folder")
    if ".git" in os.path.relpath(real, real_root).split(os.sep):
        raise ValueError(f"{rel} is inside .git")
    from optimux_backend.core import paths
    if filetools._inside(real, os.path.realpath(paths.HOME)):
        raise ValueError(f"{rel} is in Optimux's own data folder")
    if not os.path.isdir(real):
        raise ValueError(f"{rel} isn't a folder")
    return real, os.path.relpath(real, real_root)


def list_files(root: str, path: str = "") -> str:
    real, rel = _folder(root, path)
    found, total = planner.file_list(real)
    found = found[:LIST_MAX]
    if not found:
        return f"{rel or 'The project'} has no files."
    lines = [os.path.join(rel, f) if rel else f for f in found]
    more = f"\n({total - len(found)} more not listed)" if total > len(found) else ""
    return "\n".join(lines) + more


def read_file(root: str, path: str, start_line: int = 1) -> str:
    try:
        real, rel = filetools.resolve_target(root, str(path or ""))
    except filetools.FileToolError as e:
        raise ValueError(str(e).replace(" can be written", " can be read"))
    if not os.path.isfile(real):
        raise ValueError(f"{rel} doesn't exist")
    size = os.path.getsize(real)
    if size > filetools.MAX_BYTES:
        raise ValueError(f"{rel} is {size} bytes, too large to read here")
    with open(real, "rb") as f:
        data = f.read()
    if b"\0" in data:
        return f"{rel} is a binary file ({size} bytes)."
    lines = data.decode("utf-8", errors="replace").splitlines()
    try:
        start = max(1, int(start_line or 1))
    except (TypeError, ValueError):
        start = 1
    out, chars, n = [], 0, start
    for n in range(start, min(len(lines), start + READ_LINES - 1) + 1):
        line = f"{n}: {lines[n - 1]}"
        if chars + len(line) > READ_CHARS:
            n -= 1
            break
        out.append(line)
        chars += len(line) + 1
    head = f"{rel} ({len(lines)} lines)"
    if not out:
        return f"{head}: nothing at line {start}."
    tail = f"\n(continues: read again from line {n + 1})" if n < len(lines) else ""
    return head + "\n" + "\n".join(out) + tail


def search_files(root: str, text: str, path: str = "") -> str:
    needle = str(text or "").strip().lower()
    if not needle:
        raise ValueError("text is empty")
    real, rel = _folder(root, path)
    real_root = os.path.realpath(root)
    hits, looked = [], 0
    for dirpath, dirnames, filenames in os.walk(real, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in planner.SKIP_DIRS and not d.startswith("."))
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            if os.path.islink(full) or looked >= SEARCH_FILES:
                continue
            looked += 1
            rel_path = os.path.relpath(full, real_root)
            if needle in rel_path.lower():         # names count too (find -name)
                hits.append(f"{rel_path}: (the file's name or folder matches)")
            try:
                if os.path.getsize(full) > SEARCH_BYTES:
                    continue
                with open(full, "rb") as f:
                    data = f.read()
            except OSError:
                continue
            if b"\0" in data:
                continue
            for i, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), 1):
                if needle in line.lower():
                    hits.append(f"{rel_path}:{i}: {line.strip()[:200]}")
                    if len(hits) >= SEARCH_MAX:
                        return "\n".join(hits) + f"\n(stopped at {SEARCH_MAX} matches)"
    return "\n".join(hits) if hits else f"No file name or line contains {text!r}."


def run_tool(root: str, name: str, args_str: str) -> str:
    """One tool call's result as text; a problem is the result too, so the scout can adjust."""
    try:
        args = json.loads(args_str or "{}")
        if not isinstance(args, dict):
            raise ValueError
    except Exception:
        return "Error: the arguments weren't valid JSON."
    try:
        if name == "list_files":
            return list_files(root, args.get("path", ""))
        if name == "read_file":
            return read_file(root, args.get("path", ""), args.get("start_line", 1))
        if name == "search_files":
            return search_files(root, args.get("text", ""), args.get("path", ""))
        return f"Error: there is no tool {name}; use list_files, read_file or search_files."
    except (ValueError, OSError) as e:
        return f"Error: {e}."


_PATHISH = re.compile(r"[\w./-]*[\w-]\.[A-Za-z][\w]{0,7}\b|[\w.-]+/[\w./-]+")


def names_files(request: str, files: list[str]) -> list[str]:
    """Project files the request names by path or file name (./ and a leading / ignored)."""
    if not files:
        return []
    by_name: dict[str, list[str]] = {}
    for f in files:
        by_name.setdefault(os.path.basename(f).lower(), []).append(f)
    full = {f.lower(): f for f in files}
    named = []
    for token in _PATHISH.findall(request):
        t = token.strip("./").lower()
        if not t:
            continue
        if t in full:
            named.append(full[t])
        elif "/" not in t and t in by_name:
            named.extend(by_name[t])
    return sorted(set(named))


def skip_reason(request: str, routing: dict, workdir: str, files: list[str]) -> str:
    """"" when the scout should run before the Complex model; otherwise why not."""
    simple, complex_ = routing.get("simple_model") or "", routing.get("complex_model") or ""
    if not simple:
        return "no Simple model"
    if simple == complex_:
        return "Simple and Complex are the same model"
    if not workdir:
        return "no project is open"
    if not files:
        return "the project has no files yet"
    named = names_files(request, files)
    if named:
        return "the request names " + ", ".join(named[:3]) + ("…" if len(named) > 3 else "")
    return ""


def first_message(request: str, memory: str, files: list[str], total: int) -> str:
    listing = "\n".join(files[:FILES_SHOWN]) or "(none)"
    more = f"\n({total - min(len(files), FILES_SHOWN)} more not listed)" if total > min(len(files), FILES_SHOWN) else ""
    return (f"Task for the stronger model:\n{request}\n\n"
            f"Project memory:\n{memory.strip() or '(empty)'}\n\n"
            f"Project files ({min(len(files), FILES_SHOWN)} shown):\n{listing}{more}")


def outcome(name: str, args_str: str, result: str) -> str:
    """One line for the lookup log: what was run and what it found, written by
    Optimux from the real result, so the Complex model can rely on it."""
    try:
        args = json.loads(args_str or "{}")
        args = args if isinstance(args, dict) else {}
    except Exception:
        args = {}
    where = str(args.get("path") or "").strip("/")
    if name == "search_files":
        what = f'search "{args.get("text", "")}"' + (f" in {where}/" if where else "")
    elif name == "list_files":
        what = f"list {where or 'the project'}"
    else:
        what = f"read {where or '?'}"
    if result.startswith("Error:"):
        return f"{what} → {result[7:].strip()}"
    if result.startswith(("Not run", "No file name or line contains")):
        return f"{what} → " + ("not run (budget used up)" if result.startswith("Not run") else "nothing found")
    if name == "search_files":
        hits = [h for h in result.splitlines() if not h.startswith("(stopped")]
        found = sorted({h.split(":", 1)[0] for h in hits})
        return f"{what} → {len(hits)} match{'es' if len(hits) != 1 else ''} in {', '.join(found[:6])}" + \
            ("…" if len(found) > 6 else "")
    if name == "list_files":
        if result.endswith("has no files."):
            return f"{what} → no files"
        return f"{what} → {len([x for x in result.splitlines() if not x.startswith('(')])} files"
    head = result.splitlines()[0] if result else ""
    return f"{what} → {head.split(' (', 1)[1].rstrip(')') if ' (' in head else 'done'}"


def _tokens(usage) -> int:
    try:
        return int(getattr(usage, "prompt_tokens", None) or (usage or {}).get("prompt_tokens") or 0)
    except Exception:
        return 0


async def _with_retries(call, **kwargs):
    """One model call, tried again after a passing error (RETRY_DELAYS); anything else is raised."""
    from optimux_backend.core import providers
    for wait in (*RETRY_DELAYS, None):
        try:
            return await call(**kwargs)
        except Exception as e:
            info = providers.classify(e)
            if wait is None or info.kind != "transient" or info.summary.startswith("timed out"):
                raise
            print(f"Scout: {info.summary}; trying again in {wait:g} s")
            await asyncio.sleep(wait)


async def run(request: str, model: str, memory: str, workdir: str, call=None) -> dict:
    """Scout the project for this request. Returns {"brief": text ("" if none came back),
    "calls": [(usage, duration_ms)], "tools": tool calls run, "read": [files read]}."""
    import litellm
    from optimux_backend.core.agents import get_scout_prompt
    from optimux_backend.core.config_manager import litellm_key_kwargs
    from optimux_backend.core.security import mask_messages
    call = call or litellm.acompletion
    files, total = planner.file_list(workdir)
    messages: list[dict] = [{"role": "system", "content": get_scout_prompt(MAX_TOOL_CALLS)},
                            {"role": "user", "content": first_message(request, memory, files, total)}]
    calls, read, log, used, spent = [], [], [], 0, 0
    done: set[tuple[str, str]] = set()     # a lookup already run isn't run (or counted) again
    brief = ""
    for _ in range(MAX_TOOL_CALLS + 2):
        last = used >= MAX_TOOL_CALLS or spent >= MAX_PROMPT_TOKENS
        if last:
            messages.append({"role": "user", "content": "That's all the reading there is room for: write the brief now."})
        started = time.monotonic()
        response = await _with_retries(call, model=model, messages=mask_messages(messages), tools=TOOLS,
                                       tool_choice="none" if last else "auto", drop_params=True,
                                       timeout=CALL_TIMEOUT, **litellm_key_kwargs(model))
        usage = getattr(response, "usage", None)
        calls.append((usage, int((time.monotonic() - started) * 1000)))
        spent += _tokens(usage)
        message = response.choices[0].message
        tool_calls = getattr(message, "tool_calls", None) or []
        if not tool_calls or last:
            brief = (message.content or "").strip()
            break
        messages.append({"role": "assistant", "content": message.content or "",
                         "tool_calls": [{"id": tc.id, "type": "function",
                                         "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"}}
                                        for tc in tool_calls]})
        for tc in tool_calls:
            try:
                key = (tc.function.name, json.dumps(json.loads(tc.function.arguments or "{}"), sort_keys=True))
            except Exception:
                key = (tc.function.name, tc.function.arguments or "")
            if key in done:
                result = "Already run above, with the same result: use that one."
            elif used >= MAX_TOOL_CALLS:
                result = "Not run: the reading budget is used up."
            else:
                used += 1
                done.add(key)
                result = run_tool(workdir, tc.function.name, tc.function.arguments)
                log.append(outcome(tc.function.name, tc.function.arguments, result))
                if tc.function.name == "read_file" and not result.startswith("Error"):
                    try:
                        read.append(str(json.loads(tc.function.arguments or "{}").get("path", "")))
                    except Exception:
                        pass
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})
    if len(brief) > BRIEF_CHARS:
        brief = brief[:BRIEF_CHARS].rstrip() + "\n(brief cut short)"
    return {"brief": brief, "calls": calls, "tools": used, "read": sorted(set(p for p in read if p)),
            "log": log, "files": files, "total": total}


HANDOFF_FILES = 80           # project files listed for the Complex model, at most


def report(result: dict) -> str:
    """What Optimux itself knows from the scout's run: the project's files and
    each lookup with its real outcome. Not written by the scout model, so it is exact."""
    files, total, log = result.get("files") or [], result.get("total") or 0, result.get("log") or []
    shown = files[:HANDOFF_FILES]
    more = f"\n({total - len(shown)} more not listed)" if total > len(shown) else ""
    lines = [f"Project files ({total} in all):", *shown]
    out = "\n".join(lines) + more
    if log:
        out += "\n\nLookups run:\n" + "\n".join(f"- {x}" for x in log)
    return out


def handoff(request: str, result: dict, model: str) -> str:
    """The request as the Complex model gets it: the user's words, then the scout's report
    and brief, and how to use them. The Complex model once went on searching for what the
    brief already said doesn't exist, so the report shows the real lookups and says to trust them."""
    return (f"{request}\n\n---\nScout report. Before this task reached you, {model} looked through the project "
            "with read-only tools. The file list and the lookups below were run by Optimux itself just now, so "
            f"they are exact and current.\n\n{report(result)}\n\nScout's brief:\n{result['brief']}\n\n"
            "How to use this: don't repeat these lookups or list the project again: what they found, or didn't "
            "find, is so. Read a file again only to change it, and look further only for what this report doesn't "
            "cover. If it shows that what the task works on doesn't exist, don't search for it: say so in one or "
            "two sentences and ask the user how to go on (for example, whether to create it).")
