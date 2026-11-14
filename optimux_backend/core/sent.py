"""What was sent to the models (v0.8).

Every model call's prompt is kept exactly as it left the machine (saved keys
already hidden), so the user can read it: Optimux's own instructions, the memory
put into them, the conversation, the tool results and the tools offered.

A conversation is sent again in full with every call, so the text is stored part
by part, each part once (`sent_parts`, keyed by its hash); a call (`sent_calls`)
is a list of references. Calls older than the kept days are dropped with the parts
nobody uses any more. Nothing here may ever break a model call: `record` returns
None on any failure.
"""
import hashlib
import json
import time

from optimux_backend.core import store

KEEP_DAYS = 14            # the default of the keep_sent_days setting; 0 keeps nothing
PRUNE_EVERY = 6 * 3600    # seconds between clean-ups, done on the way by record()
_last_prune = 0.0


def _text(content) -> str:
    """A message's content as text: a string, or a list of blocks (Anthropic's
    cache marks, several text blocks)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    return "" if content is None else str(content)


def _call_text(call: dict) -> str:
    fn = call.get("function") or {}
    args = fn.get("arguments", "")
    try:
        args = json.dumps(json.loads(args), indent=2, ensure_ascii=False) if isinstance(args, str) else json.dumps(args, indent=2)
    except (ValueError, TypeError):
        args = str(args)
    return f"{fn.get('name', '?')}\n{args}"


def split(messages: list, tools: list | None, memory: str = "") -> list[tuple[str, str]]:
    """(kind, text) for every part of one call, in the order it was sent. Kinds:
    instructions (the system text), memory (the user's memory inside it, when
    `memory` is found there), user, assistant, call (a tool the model asked for),
    result (what the tool answered), tools (the tool definitions offered)."""
    parts: list[tuple[str, str]] = []
    for m in messages:
        role, text = m.get("role"), _text(m.get("content"))
        if role == "system":
            at = text.find(memory) if memory else -1
            if at >= 0:
                parts += [("instructions", text[:at]), ("memory", memory), ("instructions", text[at + len(memory):])]
            else:
                parts.append(("instructions", text))
        elif role == "tool":
            parts.append(("result", text))
        else:
            parts.append(("assistant" if role == "assistant" else "user", text))
            for call in m.get("tool_calls") or []:
                parts.append(("call", _call_text(call if isinstance(call, dict) else dict(call))))
    if tools:
        parts.append(("tools", json.dumps(tools, indent=2, ensure_ascii=False, default=str)))
    return [(k, t) for k, t in parts if t]


def record(session_id: str, role: str, model: str, messages: list, tools: list | None,
           project: str = "", memory: str = "", task_id: str | None = None,
           keep_days: int = KEEP_DAYS) -> int | None:
    """Keep one call's prompt; returns its id (None when it could not be kept)."""
    global _last_prune
    try:
        parts = split(messages, tools, memory)
        refs = []
        conn = store.connect()
        try:
            with conn:
                for kind, text in parts:
                    h = hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()[:32]
                    conn.execute("INSERT OR IGNORE INTO sent_parts (hash, text, chars) VALUES (?, ?, ?)", (h, text, len(text)))
                    refs.append([kind, h])
                names = [(t.get("function") or {}).get("name", "?") for t in tools or [] if isinstance(t, dict)]
                cur = conn.execute(
                    "INSERT INTO sent_calls (ts, session, task_id, project, role, model, parts, tools, chars) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (time.time(), session_id, task_id, project or "", role, model, json.dumps(refs),
                     json.dumps(names), sum(len(t) for _, t in parts)))
                sid = cur.lastrowid
        finally:
            conn.close()
        if time.time() - _last_prune > PRUNE_EVERY:
            _last_prune = time.time()
            prune(keep_days)
        return sid
    except Exception as e:      # never in the way of the call itself
        print(f"[Optimux] the sent prompt could not be kept: {e}")
        return None


def get(sid: int) -> dict | None:
    """One call: {id, ts, session, task_id, project, role, model, chars, tools (names),
    parts: [{kind, text, chars}]}."""
    conn = store.connect()
    try:
        row = conn.execute("SELECT * FROM sent_calls WHERE id = ?", (sid,)).fetchone()
        if row is None:
            return None
        refs = json.loads(row["parts"])
        texts = {}
        wanted = sorted({h for _, h in refs})
        for i in range(0, len(wanted), 500):
            chunk = wanted[i:i + 500]
            for r in conn.execute(f"SELECT hash, text FROM sent_parts WHERE hash IN ({','.join('?' * len(chunk))})", chunk):
                texts[r["hash"]] = r["text"]
    finally:
        conn.close()
    parts = [{"kind": k, "text": texts.get(h, ""), "chars": len(texts.get(h, ""))} for k, h in refs]
    return {"id": row["id"], "ts": row["ts"], "session": row["session"], "task_id": row["task_id"],
            "project": row["project"], "role": row["role"], "model": row["model"], "chars": row["chars"],
            "tools": json.loads(row["tools"] or "[]"), "parts": parts}


def prune(days: int) -> int:
    """Drop the calls older than `days` and the parts no call uses any more.
    Returns how many calls went."""
    cutoff = time.time() - max(0, days) * 86400
    conn = store.connect()
    try:
        with conn:
            gone = conn.execute("DELETE FROM sent_calls WHERE ts < ?", (cutoff,)).rowcount
            if gone:
                used = set()
                for r in conn.execute("SELECT parts FROM sent_calls"):
                    used.update(h for _, h in json.loads(r["parts"]))
                have = [r["hash"] for r in conn.execute("SELECT hash FROM sent_parts")]
                dead = [h for h in have if h not in used]
                for i in range(0, len(dead), 500):
                    chunk = dead[i:i + 500]
                    conn.execute(f"DELETE FROM sent_parts WHERE hash IN ({','.join('?' * len(chunk))})", chunk)
        return gone
    finally:
        conn.close()
