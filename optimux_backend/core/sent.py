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


def _like(text: str) -> str:
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _prompt_terms(conn, query: str) -> tuple[str, list]:
    """SQL (and its arguments) for "one of this call's user messages holds `query`". The parts are stored by
    hash and a call lists [kind, hash]: the parts holding the words are found first (the newest 300), then the
    calls that list one of them as a user message. Optimux's own text, in every call, is never the match."""
    hashes = [r["hash"] for r in conn.execute(
        "SELECT hash FROM sent_parts WHERE text LIKE ? ESCAPE '\\' ORDER BY rowid DESC LIMIT 300", (_like(query),))]
    if not hashes:
        return "", []
    return " OR (" + " OR ".join(["instr(parts, ?) > 0"] * len(hashes)) + ")", [f'["user", "{h}"]' for h in hashes]


def _last_user_text(conn, refs: list) -> str:
    for kind, h in reversed(refs):
        if kind == "user":
            row = conn.execute("SELECT text FROM sent_parts WHERE hash = ?", (h,)).fetchone()
            if row and row["text"].strip():
                return row["text"]
    return ""


def turns(session: str, query: str = "") -> list[dict]:
    """The kept calls of one chat ("" = those outside any chat), grouped by the request that made them, in the
    order they were asked: [{key, reply, prompt, hit, ts, calls: [{id, ts, role, model, chars}]}]. A reply's calls
    share a task id (the Conductor's, the executor's, the scout's); a call made on its own (the memory update, a
    commit message) is a turn of its own with no prompt (`reply` false). `prompt` is the last thing the user said
    in the executor's call, cut for a list; `hit` says whether it holds `query`."""
    conn = store.connect()
    try:
        rows = conn.execute("SELECT id, ts, task_id, role, model, chars, parts FROM sent_calls "
                            "WHERE COALESCE(session, '') = ? ORDER BY id", (session,)).fetchall()
        groups: dict[str, list] = {}
        for r in rows:
            groups.setdefault(r["task_id"] or f"call-{r['id']}", []).append(r)
        out = []
        for key, calls in groups.items():
            reply = not key.startswith("call-")
            prompt = ""
            if reply:
                first = next((c for c in calls if c["role"] in ("simple", "complex")), calls[0])
                prompt = " ".join(_last_user_text(conn, json.loads(first["parts"])).split())[:PROMPT_CUT]
            out.append({"key": key, "reply": reply, "prompt": prompt,
                        "hit": bool(query.strip()) and query.strip().lower() in prompt.lower(), "ts": calls[0]["ts"],
                        "calls": [{"id": c["id"], "ts": c["ts"], "role": c["role"], "model": c["model"], "chars": c["chars"]}
                                  for c in calls]})
    finally:
        conn.close()
    return out


def chats(project: str | None = None, since: float | None = None, archived: bool = False,
          query: str = "", limit: int = 300) -> list[dict]:
    """The kept calls by chat, the most recently used chat first (Dashboard -> History):
    {session, title, known, project, archived, last, calls, chars, models}. The filters are
    the chat list's: a project ("" = the Workspace), calls since a time, archived chats,
    words in the title, in a model's name or in one of the user's requests (the whole chat is listed, not only the matching calls). `since` keeps the chats used since then. Calls made outside any chat (a commit
    message, a memory update) come as one row with session "" and no title; those of a
    chat that was deleted keep their id, with known false and no title."""
    from optimux_backend.core.security import mask_secrets
    where, args = ["COALESCE(s.archived, 0) = ?"], [int(archived)]
    if project is not None:
        where.append("COALESCE(c.project, '') = ?"); args.append(project)
    conn = store.connect()
    try:
        if query.strip():
            # a chat whose title, a model's name or one of whose requests holds the words: the whole chat is listed
            terms, hit_args = _prompt_terms(conn, query.strip())
            where.append("(s.title LIKE ? OR COALESCE(c.session, '') IN (SELECT COALESCE(session, '') FROM sent_calls "
                         "WHERE model LIKE ?" + terms + "))")
            args += [f"%{query.strip()}%"] * 2 + hit_args
        sql = ("SELECT COALESCE(c.session, '') AS session, MAX(s.title) AS title, MAX(s.archived) AS archived, "
               "MAX(c.ts) AS last, COUNT(*) AS calls, SUM(c.chars) AS chars, GROUP_CONCAT(DISTINCT c.model) AS models, "
               "MAX(COALESCE(c.project, '')) AS project, MAX(s.id) AS known "
               "FROM sent_calls c LEFT JOIN sessions s ON s.id = c.session WHERE " + " AND ".join(where)
               + " GROUP BY COALESCE(c.session, '')" + (" HAVING MAX(c.ts) >= ?" if since is not None else "")
               + " ORDER BY last DESC LIMIT ?")
        found = conn.execute(sql, (*args, *([since] if since is not None else []), max(1, min(int(limit), 1000)))).fetchall()
    finally:
        conn.close()
    return [{"session": r["session"], "title": mask_secrets(r["title"] or "")[0], "project": r["project"],
             "archived": bool(r["archived"]), "known": r["known"] is not None, "last": r["last"], "calls": r["calls"], "chars": r["chars"] or 0,
             "models": sorted(m for m in (r["models"] or "").split(",") if m)} for r in found]


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
