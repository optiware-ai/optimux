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
PROMPT_CUT = 300          # characters of a request shown in a list
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
    instructions (the system text), memory (the user's memory, cut out of the
    instructions or of a user message when `memory` is found there), user, assistant, call (a tool the model asked for),
    result (what the tool answered), tools (the tool definitions offered)."""
    parts: list[tuple[str, str]] = []
    for m in messages:
        role, text = m.get("role"), _text(m.get("content"))
        if role == "tool":
            parts.append(("result", text))
            continue
        kind = "instructions" if role == "system" else ("assistant" if role == "assistant" else "user")
        # The memory, wherever it was put: in the instructions (the executors) or in the
        # first message (the scout, the planner, the memory update)
        at = text.find(memory) if memory and kind != "assistant" else -1
        if at >= 0:
            parts += [(kind, text[:at]), ("memory", memory), (kind, text[at + len(memory):])]
        else:
            parts.append((kind, text))
        if kind == "assistant":
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


def recent(limit: int = 100, role: str | None = None, project: str | None = None,
           session: str | None = None) -> list[dict]:
    """The kept calls, newest first, without their text: {id, ts, role, model,
    project, session, chars}. For the Dashboard's list. `session` keeps one chat's
    calls ("" = the calls made outside any chat)."""
    where, args = [], []
    if role:
        where.append("role = ?"); args.append(role)
    if project is not None:
        where.append("project = ?"); args.append(project)
    if session is not None:
        where.append("COALESCE(session, '') = ?"); args.append(session)
    sql = ("SELECT id, ts, role, model, project, session, chars FROM sent_calls"
           + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id DESC LIMIT ?")
    conn = store.connect()
    try:
        return [dict(r) for r in conn.execute(sql, (*args, max(1, min(int(limit), 1000))))]
    finally:
        conn.close()


EXECUTORS = ("simple", "complex")


def _user_start(refs: list) -> int:
    """Where this request starts in an executor's call: its last user message (everything before is
    Optimux's instructions and memory, then the earlier conversation sent again)."""
    return max((i for i, (kind, _) in enumerate(refs) if kind == "user"), default=0)


def _prefix(a: list, b: list) -> int:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def _request_text(conn, refs: list) -> str:
    start = _user_start(refs)
    if start < len(refs) and refs[start][0] == "user":
        row = conn.execute("SELECT text FROM sent_parts WHERE hash = ?", (refs[start][1],)).fetchone()
        return row["text"] if row else ""
    return ""


def requests(project: str | None = None, since: float | None = None, archived: bool = False,
             query: str = "", limit: int = 300) -> list[dict]:
    """The kept calls as a flat list of requests, the most recent first (Dashboard -> History): one row per
    thing the user asked, whatever its chat: {key, session, title, known, project, reply, role, prompt, ts, last,
    calls, ids, chars, models}. A reply's calls (the Conductor's, the executor's, the scout's) share a task id; a
    call made on its own (the memory update, a commit message) is a row of its own (`reply` false, no prompt).
    `prompt` is what the user said, cut for a list. The filters are the chat list's (a project, "" = the
    Workspace; requests used since a time; archived chats); `query` keeps the requests whose chat title, a
    model's name or whole request holds the words (Optimux's own text, in every call, never matches)."""
    from optimux_backend.core.security import mask_secrets
    where, args = ["COALESCE(s.archived, 0) = ?"], [int(archived)]
    if project is not None:
        where.append("COALESCE(c.project, '') = ?"); args.append(project)
    conn = store.connect()
    try:
        rows = conn.execute("SELECT c.id, c.ts, COALESCE(c.session, '') AS session, c.task_id, c.role, c.model, c.chars, "
                            "COALESCE(c.project, '') AS project, s.title AS title, s.id AS known "
                            "FROM sent_calls c LEFT JOIN sessions s ON s.id = c.session WHERE " + " AND ".join(where)
                            + " ORDER BY c.id", args).fetchall()
        groups: dict[tuple, list] = {}
        for r in rows:
            groups.setdefault((r["session"], r["task_id"] or f"call-{r['id']}"), []).append(r)
        found = [(g[-1]["ts"], key, g) for key, g in groups.items() if since is None or g[-1]["ts"] >= since]
        found.sort(key=lambda f: f[0], reverse=True)
        words = " ".join(query.lower().split())
        out = []
        for last, (session, key), calls in found:
            first = next((c for c in calls if c["role"] in EXECUTORS), calls[0])
            reply = not key.startswith("call-")
            title = mask_secrets(calls[0]["title"] or "")[0]
            models = sorted({c["model"] for c in calls if c["model"]})
            prompt = ""
            if reply:
                refs = json.loads(conn.execute("SELECT parts FROM sent_calls WHERE id = ?", (first["id"],)).fetchone()["parts"])
                prompt = " ".join(_request_text(conn, refs).split())
            if words and not (words in title.lower() or any(words in m.lower() for m in models) or words in prompt.lower()):
                continue
            out.append({"key": key, "session": session, "title": title, "known": calls[0]["known"] is not None,
                        "project": calls[0]["project"], "reply": reply, "role": first["role"], "prompt": prompt[:PROMPT_CUT], "ts": calls[0]["ts"],
                        "last": last, "calls": len(calls), "ids": [c["id"] for c in calls], "chars": sum(c["chars"] or 0 for c in calls), "models": models})
            if len(out) >= max(1, min(int(limit), 1000)):
                break
    finally:
        conn.close()
    return out


def count(project: str | None = None, since: float | None = None, archived: bool = False) -> int:
    """How many requests `requests()` would list for the same filters, without reading any text (a header's
    number while the list is closed). A search needs the text: count that with len(requests(...))."""
    where, args = ["COALESCE(s.archived, 0) = ?"], [int(archived)]
    if project is not None:
        where.append("COALESCE(c.project, '') = ?"); args.append(project)
    having = " HAVING MAX(c.ts) >= ?" if since is not None else ""
    if since is not None:
        args.append(since)
    conn = store.connect()
    try:
        return conn.execute("SELECT COUNT(*) FROM (SELECT 1 FROM sent_calls c LEFT JOIN sessions s ON s.id = c.session WHERE "
                            + " AND ".join(where) + " GROUP BY COALESCE(c.session, ''), COALESCE(c.task_id, 'call-' || c.id)"
                            + having + ")", args).fetchone()[0]
    finally:
        conn.close()


def prune_preview(days: int) -> dict:
    """What prune(days) would delete, counted and left alone: {calls, requests}, so a shorter keeping
    period can be shown to the user before anything goes."""
    cutoff = time.time() - max(0, days) * 86400
    conn = store.connect()
    try:
        calls = conn.execute("SELECT COUNT(*) FROM sent_calls WHERE ts < ?", (cutoff,)).fetchone()[0]
        requests_ = conn.execute("SELECT COUNT(*) FROM (SELECT 1 FROM sent_calls WHERE ts < ? "
                                 "GROUP BY COALESCE(session, ''), COALESCE(task_id, 'call-' || id))", (cutoff,)).fetchone()[0]
        return {"calls": calls, "requests": requests_}
    finally:
        conn.close()


def request_detail(session: str, key: str) -> dict | None:
    """One request as a timeline, for opening a row: {meta, steps}. `meta` is Optimux's own prompt for the
    executor, once (its instructions and your memory). `steps` are the calls in the order they were made (the
    Conductor, then the executor, the scout...): each {id, ts, role, model, chars, parts, earlier}, where
    `parts` is only what that call added (the request, the tool calls and results so far; instructions, memory
    and tools shown before are not repeated) and `earlier` counts the conversation of the chats before it,
    sent again with the first executor call ({count, chars}, else None). Whole prompts: get(id)."""
    conn = store.connect()
    try:
        if key.startswith("call-") and key[5:].isdigit():
            rows = conn.execute("SELECT * FROM sent_calls WHERE id = ? AND COALESCE(session, '') = ?", (int(key[5:]), session)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM sent_calls WHERE task_id = ? AND COALESCE(session, '') = ? ORDER BY id", (key, session)).fetchall()
        if not rows:
            return None
        refs_of = {r["id"]: json.loads(r["parts"]) for r in rows}
        wanted = sorted({h for refs in refs_of.values() for _, h in refs})
        texts = {}
        for i in range(0, len(wanted), 500):
            chunk = wanted[i:i + 500]
            for t in conn.execute(f"SELECT hash, text FROM sent_parts WHERE hash IN ({','.join('?' * len(chunk))})", chunk):
                texts[t["hash"]] = t["text"]
    finally:
        conn.close()
    part = lambda ref: {"kind": ref[0], "text": texts.get(ref[1], ""), "chars": len(texts.get(ref[1], ""))}
    meta, steps, prev_exec, prev_role, seen_tools = [], [], None, {}, set()
    for r in rows:
        refs, earlier = refs_of[r["id"]], None
        if r["role"] in EXECUTORS:
            start = _user_start(refs)
            if prev_exec is None:
                meta = [part(x) for x in refs[:start] if x[0] in ("instructions", "memory")]
                history = [part(x) for x in refs[:start] if x[0] not in ("instructions", "memory")]
                earlier = {"count": len(history), "chars": sum(p["chars"] for p in history)} if history else None
                new = refs[start:]
            else:
                new = refs[max(_prefix(refs, prev_exec), start):]
            prev_exec = refs
        else:
            new = refs[_prefix(refs, prev_role[r["role"]]):] if r["role"] in prev_role else refs
            prev_role[r["role"]] = refs
        new = [x for x in new if not (x[0] == "tools" and x[1] in seen_tools)]
        seen_tools.update(x[1] for x in new if x[0] == "tools")
        steps.append({"id": r["id"], "ts": r["ts"], "role": r["role"], "model": r["model"], "chars": r["chars"],
                      "parts": [part(x) for x in new], "earlier": earlier})
    return {"meta": meta, "steps": steps}


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
