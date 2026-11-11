"""File work inside model calls (v0.8): how much of a call is reading the
project's files and how much is writing them.

A model call isn't "a read" or "a write": the output of `cat app.py` stays in
the conversation and is sent again with every later call, and a file the model
writes is part of its answer. So each call gets two estimates, stored with its
usage row:

- read_tokens: the share of its input that is the output of commands that only
  read or search (cat, sed -n, grep, ls, git diff, ...), by characters.
- write_tokens: the share of its output that is the arguments of write_file and
  edit_file (thinking left out), by characters.

The savings view (analytics.savings_of) prices them. Estimates, not measurements.
"""
import json
import re

SHELL_TOOL = "execute_bash"
WRITE_TOOLS = ("write_file", "edit_file")
READ_COMMANDS = {"cat", "head", "tail", "nl", "less", "more", "bat", "grep", "egrep", "fgrep", "rg", "ag",
                 "ls", "tree", "find", "fd", "wc", "file", "stat", "du", "pwd", "cd", "sort", "uniq", "cut", "awk"}
GIT_READS = {"diff", "status", "log", "show", "blame", "ls-files", "grep"}
_SPLIT = re.compile(r"&&|\|\||;|\||\n")


def is_read_command(command: str) -> bool:
    """True when every part of the command only reads or searches: nothing is
    redirected to a file and no part edits, builds or runs the project."""
    command = (command or "").strip()
    if not command or re.search(r"(?<![0-9&])>(?!&)", command.replace("2>/dev/null", "")):
        return False
    real = 0
    for part in _SPLIT.split(command):
        words = part.strip().split()
        if not words:
            continue
        name = words[0].rsplit("/", 1)[-1]
        if name == "git":
            sub = next((w for w in words[1:] if not w.startswith("-")), "")
            if sub not in GIT_READS:
                return False
        elif name == "sed":
            if "-n" not in words or any(w.startswith("-i") or w == "--in-place" for w in words):
                return False
        elif name not in READ_COMMANDS:
            return False
        real += name != "cd"
    return real > 0


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):          # provider content blocks
        return "".join(str(b.get("text") or "") if isinstance(b, dict) else str(b) for b in content)
    return "" if content is None else str(content)


def _args(tool_call: dict) -> str:
    return str(((tool_call or {}).get("function") or {}).get("arguments") or "")


def chars(message: dict) -> int:
    """The size of one message as the model gets it: its text and its tool calls."""
    return len(_text(message.get("content"))) + sum(len(_args(tc)) for tc in message.get("tool_calls") or [])


def shares(messages: list, reply_text: str, reply_calls: dict | list, tokens: dict) -> dict:
    """read_tokens and write_tokens for one call (see the module text)."""
    reads: set[str] = set()
    for m in messages:
        for tc in m.get("tool_calls") or []:
            if ((tc.get("function") or {}).get("name")) != SHELL_TOOL:
                continue
            try:
                command = json.loads(_args(tc) or "{}").get("command", "")
            except (ValueError, AttributeError):
                command = ""
            if is_read_command(command):
                reads.add(tc.get("id"))
    total = sum(chars(m) for m in messages)
    read = sum(len(_text(m.get("content"))) for m in messages
               if m.get("role") == "tool" and m.get("tool_call_id") in reads)
    prompt = int(tokens.get("prompt_tokens") or 0)
    calls = list(reply_calls.values()) if isinstance(reply_calls, dict) else list(reply_calls or [])
    wrote = sum(len(_args(tc)) for tc in calls if ((tc.get("function") or {}).get("name")) in WRITE_TOOLS)
    out = len(reply_text or "") + sum(len(_args(tc)) for tc in calls)
    answer = max(0, int(tokens.get("completion_tokens") or 0) - int(tokens.get("reasoning_tokens") or 0))
    return {"read_tokens": round(prompt * read / total) if total else 0,
            "write_tokens": round(answer * wrote / out) if out else 0}
