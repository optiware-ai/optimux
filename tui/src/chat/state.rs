//! The chat as data: what the server said (docs/PROTOCOL.md, part 3) turned into a list
//! of entries plus the few things the screen shows around it (busy, the open question,
//! the cost). No screen and no network here, so it is tested with plain messages.
//!
//! The same `apply` plays a live message and a stored chat (`session_loaded.events`),
//! so an opened chat looks exactly like one watched live.

use serde_json::{Value, json};

#[derive(Debug, Clone, PartialEq)]
pub enum Entry {
    /// What you wrote.
    User(String),
    /// The model's answer (Markdown), built from `token` pieces.
    Assistant(String),
    /// The model's thinking, built from `thinking` pieces.
    Thinking(String),
    Note(Note, String),
    Command(Command),
    /// Files changed by the request: (status, path, added, removed).
    Changes(Vec<(String, String, i64, i64)>, i64),
    Plan(Plan),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Note {
    Info,
    Warn,
    Error,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Command {
    pub command: String,
    pub output: Option<String>,
    pub exit_code: Option<i64>,
    /// The output was shortened by the server.
    pub cut: bool,
    pub running: bool,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Plan {
    pub id: String,
    pub goal: String,
    /// waiting, running, paused, done, cancelled
    pub status: String,
    /// (title, status)
    pub steps: Vec<(String, String)>,
}

/// A question the server waits on. Nothing else runs until it is answered.
#[derive(Debug, Clone, PartialEq)]
pub enum Ask {
    /// `approval_request`, kept whole: its fields depend on the kind (PROTOCOL.md, 4).
    Approval(Value),
    /// `command_limit`: the command runs longer than its limit.
    Limit { command: String, elapsed: f64, limit: f64 },
}

#[derive(Debug, Default)]
pub struct State {
    pub entries: Vec<Entry>,
    /// A request (or an open/new chat) is running: ends with `done`.
    pub busy: bool,
    /// `status.stage` and `status.model` while busy.
    pub stage: Option<String>,
    pub model: Option<String>,
    pub ask: Option<Ask>,
    pub chat_id: Option<String>,
    pub chat_title: String,
    pub project: Option<String>,
    pub read_only: bool,
    pub tokens: u64,
    /// The chat's cost; None while a price is unknown.
    pub cost: Option<f64>,
    pub budget: Option<String>,
    pub version: String,
    /// Seconds a dropped request waits for this client.
    pub away: u64,
    /// Seconds a request left on purpose (detach) waits for a window.
    pub detach_wait: u64,
    /// Bumped on every change, so the screen can keep its layout until then.
    pub revision: u64,
}

impl State {
    /// Plays one message from the server. Unknown types are ignored, so a newer server
    /// still works with this client.
    pub fn apply(&mut self, msg: &Value) {
        self.revision += 1;
        let kind = msg.get("type").and_then(Value::as_str).unwrap_or("");
        let text = |k: &str| msg.get(k).and_then(Value::as_str).unwrap_or("").to_string();
        let content = text("content");
        match kind {
            "auth_ok" => {
                self.version = text("version");
                self.away = msg.get("away").and_then(Value::as_u64).unwrap_or(0);
                self.detach_wait = msg.get("detach_wait").and_then(Value::as_u64).unwrap_or(0);
            }
            "token" => self.append_text(content, false),
            "thinking" => self.append_text(content, true),
            "prompt" => self.entries.push(Entry::User(content)),
            "prompt_masked" => {
                if let Some(Entry::User(last)) = self.entries.iter_mut().rev().find(|e| matches!(e, Entry::User(_))) {
                    *last = content;
                }
            }
            "system" | "step_limit" => self.note(Note::Info, content),
            "error" | "no_projects" => self.note(Note::Error, content),
            "sandbox_problem" => self.note(Note::Warn, content),
            "budget" => {
                self.budget = Some(text("state")).filter(|s| !s.is_empty());
                self.note(Note::Warn, content);
            }
            "status" => {
                self.stage = Some(text("stage")).filter(|s| !s.is_empty());
                if let Some(m) = msg.get("model").and_then(Value::as_str) {
                    self.model = Some(m.to_string());
                }
            }
            "usage" => {
                if let Some(t) = msg.get("session_tokens").and_then(Value::as_u64) {
                    self.tokens = t;
                }
                let complete = msg
                    .get("session_cost_complete")
                    .and_then(Value::as_bool)
                    .unwrap_or(true);
                self.cost = msg.get("session_cost").and_then(Value::as_f64).filter(|_| complete);
                if let Some(m) = msg.get("model").and_then(Value::as_str) {
                    self.model = Some(m.to_string());
                }
            }
            "approval_request" => self.ask = Some(Ask::Approval(msg.clone())),
            "command_limit" => {
                self.ask = Some(Ask::Limit {
                    command: text("command"),
                    elapsed: msg.get("elapsed").and_then(Value::as_f64).unwrap_or(0.0),
                    limit: msg.get("limit").and_then(Value::as_f64).unwrap_or(0.0),
                })
            }
            "command_limit_done" => {
                if matches!(self.ask, Some(Ask::Limit { .. })) {
                    self.ask = None;
                }
            }
            "command_start" => self.entries.push(Entry::Command(Command {
                command: text("command"),
                output: None,
                exit_code: None,
                cut: false,
                running: true,
            })),
            "command_output" => self.command_output(msg),
            "changes" => self.changes(msg),
            "plan" => self.plan(msg.get("plan").unwrap_or(&Value::Null)),
            "session_started" => self.chat_info(msg.get("session")),
            "session_loaded" => self.load(msg),
            "chat_reset" => self.reset(),
            "project_switched" | "project_created" => {
                let name = text("project");
                if kind == "project_switched" {
                    self.project = Some(name.clone());
                }
                let what = if kind == "project_switched" {
                    "Now in project"
                } else {
                    "Project created:"
                };
                self.note(Note::Info, format!("{what} {name}"));
            }
            "project_offer" => self.note(
                Note::Info,
                format!("The model suggests the project {} (Ctrl+P to switch).", text("project")),
            ),
            "memory_updated" => self.note(Note::Info, format!("Project memory updated. {}", text("summary"))),
            // "stopped" is followed by the server's own note ("Stopped." or why)
            "escalate" => self.note(Note::Info, "Given to the Complex model.".into()),
            "done" => {
                self.busy = false;
                self.stage = None;
                self.ask = None;
            }
            // pong, task_done, memory_updating/unchanged, plan_action and the Workspace
            // messages change nothing on this screen
            _ => {}
        }
    }

    /// A prompt to send: shown at once, like the web page (the server doesn't echo it).
    pub fn prompt(&mut self, text: &str) -> Value {
        self.revision += 1;
        self.entries.push(Entry::User(text.to_string()));
        self.busy = true;
        json!({"prompt": text, "model": "auto"})
    }

    /// A job without a prompt (`new_chat`, `open_session`, `escalate`, `plan_action`): busy until `done`.
    pub fn job(&mut self, msg: Value) -> Value {
        self.revision += 1;
        self.busy = true;
        msg
    }

    /// The waiting plan, if any: (plan id, status), for the plan keys.
    pub fn open_plan(&self) -> Option<(&str, &str)> {
        self.entries.iter().rev().find_map(|e| match e {
            Entry::Plan(p) if p.status == "waiting" || p.status == "paused" => Some((p.id.as_str(), p.status.as_str())),
            _ => None,
        })
    }

    fn note(&mut self, kind: Note, text: String) {
        if !text.trim().is_empty() {
            self.entries.push(Entry::Note(kind, text));
        }
    }

    fn append_text(&mut self, piece: String, thinking: bool) {
        match (self.entries.last_mut(), thinking) {
            (Some(Entry::Assistant(t)), false) | (Some(Entry::Thinking(t)), true) => t.push_str(&piece),
            (_, false) => self.entries.push(Entry::Assistant(piece)),
            (_, true) => self.entries.push(Entry::Thinking(piece)),
        }
    }

    fn command_output(&mut self, msg: &Value) {
        let command = msg.get("command").and_then(Value::as_str).unwrap_or("").to_string();
        let output = msg.get("output").and_then(Value::as_str).unwrap_or("").to_string();
        let exit_code = msg.get("exit_code").and_then(Value::as_i64);
        let cut = msg.get("cut").and_then(Value::as_bool).unwrap_or(false);
        let open = self.entries.iter_mut().rev().find_map(|e| match e {
            Entry::Command(c) if c.running && c.command == command => Some(c),
            _ => None,
        });
        match open {
            Some(c) => {
                c.output = Some(output);
                c.exit_code = exit_code;
                c.cut = cut;
                c.running = false;
            }
            None => self.entries.push(Entry::Command(Command {
                command,
                output: Some(output),
                exit_code,
                cut,
                running: false,
            })),
        }
    }

    fn changes(&mut self, msg: &Value) {
        let files = msg
            .get("files")
            .and_then(Value::as_array)
            .map(|files| {
                files
                    .iter()
                    .map(|f| {
                        let s = |k: &str| f.get(k).and_then(Value::as_str).unwrap_or("").to_string();
                        let n = |k: &str| f.get(k).and_then(Value::as_i64).unwrap_or(0);
                        (s("status"), s("path"), n("added"), n("removed"))
                    })
                    .collect::<Vec<_>>()
            })
            .unwrap_or_default();
        let more = msg.get("more").and_then(Value::as_i64).unwrap_or(0);
        if !files.is_empty() {
            self.entries.push(Entry::Changes(files, more));
        }
        if let Some(e) = msg.get("error").and_then(Value::as_str).filter(|e| !e.is_empty()) {
            self.note(Note::Warn, e.to_string());
        }
    }

    fn plan(&mut self, p: &Value) {
        let s = |v: &Value, k: &str| v.get(k).and_then(Value::as_str).unwrap_or("").to_string();
        let plan = Plan {
            id: p
                .get("id")
                .map(|v| v.as_str().map(str::to_string).unwrap_or_else(|| v.to_string()))
                .unwrap_or_default(),
            goal: s(p, "goal"),
            status: s(p, "status"),
            steps: p
                .get("steps")
                .and_then(Value::as_array)
                .map(|steps| {
                    steps
                        .iter()
                        .map(|st| {
                            let title = [s(st, "title"), s(st, "task"), s(st, "text")]
                                .into_iter()
                                .find(|t| !t.is_empty())
                                .unwrap_or_default();
                            (title, s(st, "status"))
                        })
                        .collect()
                })
                .unwrap_or_default(),
        };
        match self
            .entries
            .iter_mut()
            .find(|e| matches!(e, Entry::Plan(old) if old.id == plan.id))
        {
            Some(old) => *old = Entry::Plan(plan),
            None => self.entries.push(Entry::Plan(plan)),
        }
    }

    fn chat_info(&mut self, session: Option<&Value>) {
        let Some(s) = session else { return };
        if let Some(id) = s.get("id").and_then(Value::as_str) {
            self.chat_id = Some(id.to_string());
        }
        if let Some(t) = s.get("title").and_then(Value::as_str) {
            self.chat_title = t.to_string();
        }
        if let Some(p) = s.get("project").and_then(Value::as_str) {
            self.project = Some(p.to_string());
        }
    }

    fn reset(&mut self) {
        self.entries.clear();
        self.chat_id = None;
        self.chat_title.clear();
        self.read_only = false;
        self.tokens = 0;
        self.cost = Some(0.0);
    }

    /// `session_loaded`: the whole chat again. `running`: its request is still going,
    /// and its messages follow (PROTOCOL.md, 5).
    fn load(&mut self, msg: &Value) {
        self.reset();
        self.cost = None;
        self.chat_info(msg.get("session"));
        self.read_only = msg.get("read_only").and_then(Value::as_bool).unwrap_or(false);
        let busy = self.busy;
        if let Some(events) = msg.get("events").and_then(Value::as_array) {
            for e in events {
                self.apply(e);
            }
        }
        self.busy = busy || msg.get("running").and_then(Value::as_bool).unwrap_or(false);
        self.ask = None; // a stored chat holds no open question; a live one is sent again
        if self.read_only {
            let why = msg
                .get("reason")
                .and_then(Value::as_str)
                .unwrap_or("This chat can only be read.");
            self.note(Note::Info, why.to_string());
        }
    }
}

/// How an approval can be answered (docs/PROTOCOL.md, part 4).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    /// A shell command: `raise` when "stop asking in this project" is offered.
    Command { raise: bool },
    /// A file change: approve this one, or all edits of this reply.
    FileChange,
    /// The model used all its steps: more steps, the Complex model, or stop.
    Steps { escalate: bool },
    /// Push or update with GitHub: done in the web page's window only.
    Sync,
    /// A model failed or is stuck: go on with a costlier one, or fix it in Models.
    Fallback,
    /// Anything else (make or archive a project): yes or no.
    YesNo,
}

pub fn kind_of(req: &Value) -> Kind {
    let action = req.get("action").and_then(Value::as_str).unwrap_or("");
    let has = |k: &str| req.get(k).is_some_and(|v| !v.is_null());
    if action == "step_limit" || has("steps_box") {
        let alt = req.pointer("/steps_box/alt").and_then(Value::as_str).unwrap_or("");
        Kind::Steps {
            escalate: !alt.is_empty(),
        }
    } else if has("sync") {
        Kind::Sync
    } else if has("file_change") {
        Kind::FileChange
    } else if has("fallback") {
        Kind::Fallback
    } else if action.is_empty() && !has("fallback") {
        Kind::Command {
            raise: req.get("raise_to").is_some_and(|v| !v.is_null() && v != ""),
        }
    } else {
        Kind::YesNo
    }
}

/// The answers, as the server reads them.
#[derive(Debug, Clone, PartialEq)]
pub enum Answer {
    Yes,
    /// Yes, and stop asking for this kind of command in this project.
    YesRaise,
    /// Yes to every edit of this reply.
    YesAllEdits,
    No(String),
    MoreSteps(u32),
    Escalate,
    Stop,
}

pub fn answer(a: &Answer) -> Value {
    match a {
        Answer::Yes => json!({"type": "approval_response", "approved": true}),
        Answer::YesRaise => json!({"type": "approval_response", "approved": true, "raise_level": true}),
        Answer::YesAllEdits => json!({"type": "approval_response", "approved": true, "all_edits": true}),
        Answer::No(reason) if reason.trim().is_empty() => json!({"type": "approval_response", "approved": false}),
        Answer::No(reason) => {
            let reason: String = reason.trim().chars().take(500).collect();
            json!({"type": "approval_response", "approved": false, "reason": reason})
        }
        Answer::MoreSteps(n) => {
            json!({"type": "approval_response", "approved": true, "choice": "more", "steps": (*n).clamp(1, 100)})
        }
        Answer::Escalate => json!({"type": "approval_response", "approved": true, "choice": "escalate"}),
        Answer::Stop => json!({"type": "approval_response", "approved": false}),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn play(msgs: &[Value]) -> State {
        let mut s = State::default();
        for m in msgs {
            s.apply(m);
        }
        s
    }

    #[test]
    fn pieces_of_text_make_one_answer() {
        let s = play(&[
            json!({"type": "thinking", "content": "Let me "}),
            json!({"type": "thinking", "content": "see."}),
            json!({"type": "token", "content": "Hello "}),
            json!({"type": "token", "content": "world"}),
        ]);
        assert_eq!(
            s.entries,
            vec![
                Entry::Thinking("Let me see.".into()),
                Entry::Assistant("Hello world".into())
            ]
        );
    }

    #[test]
    fn a_command_gets_its_output() {
        let s = play(&[
            json!({"type": "command_start", "command": "ls", "file": null, "time_limit": 120}),
            json!({"type": "token", "content": "x"}),
            json!({"type": "command_output", "command": "ls", "output": "a\nb", "exit_code": 0, "cut": false}),
        ]);
        assert_eq!(
            s.entries[0],
            Entry::Command(Command {
                command: "ls".into(),
                output: Some("a\nb".into()),
                exit_code: Some(0),
                cut: false,
                running: false
            })
        );
    }

    #[test]
    fn busy_until_done_and_the_question_goes_with_it() {
        let mut s = State::default();
        let sent = s.prompt("hi");
        assert_eq!(sent, json!({"prompt": "hi", "model": "auto"}));
        assert!(s.busy);
        s.apply(&json!({"type": "status", "stage": "model", "model": "gemini/flash"}));
        s.apply(&json!({"type": "approval_request", "command": "rm x", "tool_call_id": "1"}));
        assert!(matches!(s.ask, Some(Ask::Approval(_))));
        assert_eq!(s.model.as_deref(), Some("gemini/flash"));
        s.apply(&json!({"type": "done"}));
        assert!(!s.busy && s.ask.is_none() && s.stage.is_none());
    }

    #[test]
    fn an_opened_chat_is_played_like_a_live_one() {
        let mut s = State::default();
        s.job(json!({"type": "open_session", "id": "latest"}));
        s.apply(&json!({"type": "session_loaded", "running": false,
            "session": {"id": "s1", "title": "Fix the bug", "project": "web"},
            "events": [
                {"type": "prompt", "content": "fix it"},
                {"type": "token", "content": "Done."},
                {"type": "usage", "session_tokens": 1200, "session_cost": 0.002, "session_cost_complete": true, "model": "m"}
            ]}));
        assert_eq!(
            s.entries,
            vec![Entry::User("fix it".into()), Entry::Assistant("Done.".into())]
        );
        assert_eq!((s.chat_id.as_deref(), s.project.as_deref()), (Some("s1"), Some("web")));
        assert_eq!((s.tokens, s.cost), (1200, Some(0.002)));
        assert!(s.busy, "busy until the job's done");
        s.apply(&json!({"type": "done"}));
        assert!(!s.busy);
    }

    #[test]
    fn a_running_chat_comes_back_with_its_question() {
        let mut s = State::default();
        s.job(json!({"type": "open_session", "id": "s1"}));
        s.apply(&json!({"type": "session_loaded", "running": true, "session": {"id": "s1"}, "events": []}));
        s.apply(&json!({"type": "approval_request", "command": "make"}));
        assert!(s.busy);
        assert!(matches!(s.ask, Some(Ask::Approval(_))));
    }

    #[test]
    fn unknown_prices_are_not_shown_as_free() {
        let s = play(&[
            json!({"type": "usage", "session_tokens": 10, "session_cost": 0.0, "session_cost_complete": false}),
        ]);
        assert_eq!(s.cost, None);
    }

    #[test]
    fn the_masked_prompt_replaces_what_was_typed() {
        let mut s = State::default();
        s.prompt("key sk-123");
        s.apply(&json!({"type": "prompt_masked", "content": "key ••••(OPENAI)"}));
        assert_eq!(s.entries, vec![Entry::User("key ••••(OPENAI)".into())]);
    }

    #[test]
    fn a_plan_is_updated_in_place() {
        let mut s = play(&[
            json!({"type": "plan", "plan": {"id": 7, "goal": "g", "status": "waiting",
            "steps": [{"title": "a", "status": "pending"}]}}),
        ]);
        assert_eq!(s.open_plan(), Some(("7", "waiting")));
        s.apply(&json!({"type": "plan", "plan": {"id": 7, "goal": "g", "status": "done", "steps": []}}));
        assert_eq!(s.entries.len(), 1);
        assert_eq!(s.open_plan(), None);
    }

    #[test]
    fn the_keep_waiting_question_is_live_only() {
        let mut s = play(&[json!({"type": "command_limit", "command": "sleep 999", "elapsed": 121.0, "limit": 120})]);
        assert!(matches!(s.ask, Some(Ask::Limit { .. })));
        s.apply(&json!({"type": "command_limit_done", "command": "sleep 999"}));
        assert!(s.ask.is_none());
    }

    #[test]
    fn unknown_messages_are_ignored() {
        let s = play(&[
            json!({"type": "from_the_future", "content": "x"}),
            json!({"no_type": 1}),
        ]);
        assert!(s.entries.is_empty());
    }

    #[test]
    fn approval_kinds() {
        assert_eq!(
            kind_of(&json!({"command": "ls", "raise_to": null})),
            Kind::Command { raise: false }
        );
        assert_eq!(
            kind_of(&json!({"command": "ls", "raise_to": "safe"})),
            Kind::Command { raise: true }
        );
        assert_eq!(
            kind_of(&json!({"command": "edit", "file_change": {"diff": "-a\n+b"}})),
            Kind::FileChange
        );
        assert_eq!(
            kind_of(&json!({"action": "step_limit", "steps_box": {"alt": "claude"}})),
            Kind::Steps { escalate: true }
        );
        assert_eq!(
            kind_of(&json!({"action": "push", "sync": {"kind": "push"}})),
            Kind::Sync
        );
        assert_eq!(kind_of(&json!({"action": "archive_project"})), Kind::YesNo);
        assert_eq!(
            kind_of(&json!({"action": "fallback", "fallback": {"model": "m"}})),
            Kind::Fallback
        );
    }

    #[test]
    fn answers_as_the_server_reads_them() {
        assert_eq!(
            answer(&Answer::Yes),
            json!({"type": "approval_response", "approved": true})
        );
        assert_eq!(
            answer(&Answer::No("  ".into())),
            json!({"type": "approval_response", "approved": false})
        );
        let long = "x".repeat(600);
        assert_eq!(answer(&Answer::No(long))["reason"].as_str().unwrap().len(), 500);
        assert_eq!(answer(&Answer::YesRaise)["raise_level"], json!(true));
        assert_eq!(answer(&Answer::YesAllEdits)["all_edits"], json!(true));
        assert_eq!(answer(&Answer::MoreSteps(500))["steps"], json!(100));
        assert_eq!(answer(&Answer::Escalate)["choice"], json!("escalate"));
        assert_eq!(answer(&Answer::Stop)["approved"], json!(false));
    }
}
