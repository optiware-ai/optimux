//! Dashboard → History, as in the web page. Chats: every stored chat, filtered by
//! project, time and status, sorted; open one in the chat window, rename, archive or
//! delete it. What was sent: the prompts kept for every request, how long they are kept,
//! and each request's calls with what each one sent.

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use ratatui::Frame;
use ratatui::layout::Rect;
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::Paragraph;
use serde_json::{Value, json};

use super::{Answer, Ctx, Section, draw_head, fmt, head_lines};
use crate::api::{Method, enc};
use crate::chat::text::{DIM, wrap};
use crate::cli::{num, s};
use crate::dash::projects::ago;
use crate::ui::confirm::{self, Confirm};
use crate::ui::form::{self, Field, Form, Kind};
use crate::ui::table::{Row, Table};

pub const PERIODS: &[(&str, &str, f64)] = &[
    ("1", "Today", 1.0),
    ("7", "7 days", 7.0),
    ("30", "30 days", 30.0),
    ("all", "Any time", 0.0),
];
pub const SORTS: &[(&str, &str)] = &[
    ("updated", "Updated"),
    ("cost", "Cost"),
    ("turns", "Turns"),
    ("title", "Title"),
];

/// The role of a kept call, in words (the page's sentRoleName).
pub fn role_name(r: &str) -> String {
    match r {
        "conductor" => "Conductor".into(),
        "simple" => "Simple".into(),
        "complex" => "Complex".into(),
        "scout" => "Scout".into(),
        "planner" => "Planner".into(),
        "curator" => "Memory update".into(),
        "skill_draft" => "Skill draft".into(),
        "commit_message" => "Commit message".into(),
        "test" => "Model test".into(),
        o => o.to_string(),
    }
}

/// Seconds back for a period ("all": none).
fn since(period: usize) -> f64 {
    let days = PERIODS[period].2;
    if days == 0.0 {
        return 0.0;
    }
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0);
    now - days * 86400.0
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Block {
    Chats,
    Sent,
}

enum View {
    List,
    Rename(String, Form),
    Delete(Value, Confirm),
    Find(Form),
    Keep(Form),
    KeepAsk(i64, Confirm),
    /// One request: its calls (the meta-prompt first), and the chosen one's parts.
    Request {
        row: Value,
        detail: Value,
        steps: Table,
        whole: Option<Value>,
        scroll: usize,
    },
}

pub struct History {
    block: Block,
    chats: Value,
    sent: Value,
    projects: Vec<String>,
    /// 0: every project; 1: the Workspace itself; then projects[i - 2].
    project: usize,
    period: usize,
    archived: bool,
    sort: usize,
    query: String,
    settings: Value,
    table: Table,
    view: View,
    msg: Option<(bool, String)>,
    goto: Option<(&'static str, String)>,
    /// Opened for one project before the project list arrived.
    want: Option<String>,
}

impl Default for History {
    fn default() -> History {
        History {
            block: Block::Chats,
            chats: Value::Null,
            sent: Value::Null,
            projects: Vec::new(),
            project: 0,
            period: 3,
            archived: false,
            sort: 0,
            query: String::new(),
            settings: Value::Null,
            table: Table::new(&[]),
            view: View::List,
            msg: None,
            goto: None,
            want: None,
        }
    }
}

impl History {
    fn project_query(&self) -> String {
        match self.project {
            0 => "all_projects=true".into(),
            1 => "project=".into(),
            i => format!(
                "project={}",
                enc(self.projects.get(i - 2).map(String::as_str).unwrap_or(""))
            ),
        }
    }

    fn project_label(&self) -> String {
        match self.project {
            0 => "every project".into(),
            1 => "the Workspace (no project)".into(),
            i => self.projects.get(i - 2).cloned().unwrap_or_default(),
        }
    }

    fn fetch(&self, ctx: &Ctx) {
        let base = format!(
            "{}&archived={}&q={}",
            self.project_query(),
            self.archived,
            enc(&self.query)
        );
        match self.block {
            Block::Chats => ctx.call("chats", Method::Get, format!("/api/sessions?{base}&limit=5000"), None),
            Block::Sent => {
                let since = since(self.period) as i64;
                ctx.call(
                    "sent",
                    Method::Get,
                    format!("/api/sent/requests?{base}&since={since}&limit=300"),
                    None,
                )
            }
        }
    }

    fn rows(&mut self) {
        match self.block {
            Block::Chats => {
                self.table.columns = [
                    ("Title", 0),
                    ("Project", 16),
                    ("Updated", 14),
                    ("Turns", 6),
                    ("Cost", 10),
                ]
                .iter()
                .map(|(t, w)| (t.to_string(), *w))
                .collect();
                self.table.empty = "No chats match.".into();
                let from = since(self.period);
                let mut list: Vec<Value> = self
                    .chats
                    .as_array()
                    .cloned()
                    .unwrap_or_default()
                    .into_iter()
                    .filter(|c| from == 0.0 || num(c, "updated").unwrap_or(0.0) >= from)
                    .collect();
                let key = SORTS[self.sort].0;
                list.sort_by(|a, b| {
                    if key == "title" {
                        s(a, "title").to_lowercase().cmp(&s(b, "title").to_lowercase())
                    } else {
                        num(b, key)
                            .unwrap_or(0.0)
                            .partial_cmp(&num(a, key).unwrap_or(0.0))
                            .unwrap_or(std::cmp::Ordering::Equal)
                    }
                });
                let rows = list
                    .iter()
                    .map(|c| {
                        let title = if s(c, "title").is_empty() {
                            "Untitled chat".into()
                        } else {
                            s(c, "title")
                        };
                        let plus = if num(c, "calls_without_cost").unwrap_or(0.0) > 0.0 {
                            "+"
                        } else {
                            ""
                        };
                        Row::new(s(c, "id"))
                            .cell(title)
                            .styled(
                                if s(c, "project").is_empty() {
                                    "(Workspace)".into()
                                } else {
                                    s(c, "project")
                                },
                                DIM,
                            )
                            .styled(num(c, "updated").map(ago).unwrap_or_default(), DIM)
                            .cell(format!("{:.0}", num(c, "turns").unwrap_or(0.0)))
                            .cell(format!("{}{plus}", fmt::cost(num(c, "cost").unwrap_or(0.0))))
                    })
                    .collect();
                self.table.set_rows(rows);
            }
            Block::Sent => {
                self.table.columns = [
                    ("Request", 0),
                    ("Chat", 22),
                    ("Project", 12),
                    ("When", 13),
                    ("Calls", 6),
                ]
                .iter()
                .map(|(t, w)| (t.to_string(), *w))
                .collect();
                self.table.empty = "Nothing kept matches. Prompts are kept from the next model call on.".into();
                let rows = self
                    .sent
                    .get("requests")
                    .and_then(Value::as_array)
                    .into_iter()
                    .flatten()
                    .enumerate()
                    .map(|(i, r)| {
                        let what = if r.get("reply").and_then(Value::as_bool) == Some(true) {
                            if s(r, "prompt").is_empty() {
                                "(no text)".into()
                            } else {
                                s(r, "prompt")
                            }
                        } else {
                            role_name(&s(r, "role"))
                        };
                        let chat = if r.get("known").and_then(Value::as_bool) == Some(true) {
                            if s(r, "title").is_empty() {
                                "Untitled chat".into()
                            } else {
                                s(r, "title")
                            }
                        } else if s(r, "session").is_empty() {
                            "Outside a chat".into()
                        } else {
                            "Deleted chat".into()
                        };
                        Row::new(i.to_string())
                            .cell(what)
                            .styled(chat, DIM)
                            .styled(s(r, "project"), DIM)
                            .styled(num(r, "ts").map(ago).unwrap_or_default(), DIM)
                            .cell(format!("{:.0}", num(r, "calls").unwrap_or(0.0)))
                    })
                    .collect();
                self.table.set_rows(rows);
            }
        }
    }

    fn chosen_chat(&self) -> Option<Value> {
        let id = self.table.chosen()?.id.clone();
        self.chats.as_array()?.iter().find(|c| s(c, "id") == id).cloned()
    }

    fn chosen_request(&self) -> Option<Value> {
        let i: usize = self.table.chosen()?.id.parse().ok()?;
        self.sent.get("requests")?.as_array()?.get(i).cloned()
    }

    fn step_rows(detail: &Value) -> Table {
        let mut t = Table::new(&[("Call", 18), ("Model", 0), ("When", 13), ("Tokens", 9)]);
        let mut rows = Vec::new();
        if detail
            .get("meta")
            .and_then(Value::as_array)
            .is_some_and(|m| !m.is_empty())
        {
            rows.push(
                Row::new("meta")
                    .styled("Meta-prompt", Style::new().fg(Color::LightCyan))
                    .styled("Optimux's instructions and your memory", DIM),
            );
        }
        for st in detail.get("steps").and_then(Value::as_array).into_iter().flatten() {
            rows.push(
                Row::new(s(st, "id"))
                    .cell(role_name(&s(st, "role")))
                    .styled(s(st, "model"), DIM)
                    .styled(num(st, "ts").map(ago).unwrap_or_default(), DIM)
                    .cell(format!(
                        "≈{}",
                        fmt::tokens((num(st, "chars").unwrap_or(0.0) / 4.0).round())
                    )),
            );
        }
        t.set_rows(rows);
        t
    }
}

/// The parts of a call as lines: a header per part, then its text.
pub fn part_lines(parts: &Value, width: usize) -> Vec<Line<'static>> {
    let mut out = Vec::new();
    for p in parts.as_array().into_iter().flatten() {
        let kind = s(p, "kind");
        let head = match kind.as_str() {
            "system" => "Instructions",
            "user" => "Message",
            "assistant" => "Model's answer",
            "tool" | "tool_result" => "Tool result",
            "tool_call" => "Tool call",
            "memory" => "Memory",
            _ => "",
        };
        let head = if head.is_empty() {
            let mut c = kind.chars();
            c.next()
                .map(|f| f.to_uppercase().chain(c).collect::<String>())
                .unwrap_or_default()
        } else {
            head.to_string()
        };
        out.push(Line::from(Span::styled(
            format!(
                "── {head}{}",
                num(p, "chars")
                    .map(|c| format!(" · ≈{} tok", fmt::tokens((c / 4.0).round())))
                    .unwrap_or_default()
            ),
            Style::new().fg(Color::LightCyan),
        )));
        let text = if s(p, "text").is_empty() {
            s(p, "content")
        } else {
            s(p, "text")
        };
        for l in text.lines() {
            out.extend(wrap(vec![Span::raw(l.to_string())], width, "", ""));
        }
        out.push(Line::default());
    }
    out
}

impl Section for History {
    fn load(&mut self, ctx: &Ctx) {
        ctx.call("projects", Method::Get, "/api/projects", None);
        ctx.call("settings", Method::Get, "/api/settings", None);
        ctx.call(
            "count",
            Method::Get,
            "/api/sent/requests?all_projects=true&count_only=true",
            None,
        );
        self.fetch(ctx);
    }

    fn open_with(&mut self, arg: &str) {
        // from Projects: a project's chats
        self.block = Block::Chats;
        self.view = View::List;
        match self.projects.iter().position(|p| p == arg) {
            Some(i) => self.project = i + 2,
            None if !arg.is_empty() => self.want = Some(arg.to_string()),
            None => {}
        }
    }

    fn key(&mut self, k: KeyEvent, ctx: &Ctx) -> bool {
        let view = std::mem::replace(&mut self.view, View::List);
        let (view, used) = match view {
            View::Rename(id, mut f) => match f.key(k) {
                form::Action::Cancel => (View::List, true),
                form::Action::Submit => {
                    f.busy = true;
                    ctx.call(
                        "renamed",
                        Method::Post,
                        format!("/api/sessions/{}/rename", enc(&id)),
                        Some(json!({"title": f.get("title").trim()})),
                    );
                    (View::Rename(id, f), true)
                }
                form::Action::None => (View::Rename(id, f), true),
            },
            View::Find(mut f) => match f.key(k) {
                form::Action::Cancel => (View::List, true),
                form::Action::Submit => {
                    self.query = f.get("q").trim().to_string();
                    self.fetch(ctx);
                    (View::List, true)
                }
                form::Action::None => (View::Find(f), true),
            },
            View::Keep(mut f) => match f.key(k) {
                form::Action::Cancel => (View::List, true),
                form::Action::Submit => match f.get("days").trim().parse::<i64>() {
                    Ok(n) if (0..=365).contains(&n) => {
                        ctx.call(
                            format!("preview:{n}"),
                            Method::Get,
                            format!("/api/sent/prune-preview?days={n}"),
                            None,
                        );
                        (View::List, true)
                    }
                    _ => {
                        f.error = Some("A whole number of days from 0 to 365.".into());
                        (View::Keep(f), true)
                    }
                },
                form::Action::None => (View::Keep(f), true),
            },
            View::KeepAsk(days, mut c) => match c.key(k) {
                confirm::Answer::Yes => {
                    ctx.save_settings(format!("kept:{days}+prune"), json!({"keep_sent_days": days}));
                    (View::List, true)
                }
                confirm::Answer::No => (View::List, true),
                confirm::Answer::Waiting => (View::KeepAsk(days, c), true),
            },
            View::Delete(c, mut conf) => match conf.key(k) {
                confirm::Answer::Yes => {
                    let typed = conf.input.trim().to_string();
                    ctx.call(
                        "deleted",
                        Method::Post,
                        format!("/api/sessions/{}/delete", enc(&s(&c, "id"))),
                        Some(json!({"confirm": typed})),
                    );
                    (View::List, true)
                }
                confirm::Answer::No => (View::List, true),
                confirm::Answer::Waiting => (View::Delete(c, conf), true),
            },
            View::Request {
                row,
                detail,
                mut steps,
                whole,
                mut scroll,
            } => {
                let before = steps.selected;
                if steps.key(k) {
                    let whole = if steps.selected != before { None } else { whole };
                    if steps.selected != before {
                        scroll = 0;
                    }
                    (
                        View::Request {
                            row,
                            detail,
                            steps,
                            whole,
                            scroll,
                        },
                        true,
                    )
                } else {
                    match k.code {
                        KeyCode::Esc => (View::List, true),
                        KeyCode::PageDown | KeyCode::Char('j') => {
                            scroll += 10;
                            (
                                View::Request {
                                    row,
                                    detail,
                                    steps,
                                    whole,
                                    scroll,
                                },
                                true,
                            )
                        }
                        KeyCode::PageUp | KeyCode::Char('k') => {
                            scroll = scroll.saturating_sub(10);
                            (
                                View::Request {
                                    row,
                                    detail,
                                    steps,
                                    whole,
                                    scroll,
                                },
                                true,
                            )
                        }
                        KeyCode::Char('w') => {
                            let id = steps.chosen().map(|r| r.id.clone()).filter(|id| id != "meta");
                            match (id, whole.is_some()) {
                                (_, true) => (
                                    View::Request {
                                        row,
                                        detail,
                                        steps,
                                        whole: None,
                                        scroll: 0,
                                    },
                                    true,
                                ),
                                (Some(id), false) => {
                                    ctx.call(
                                        format!("whole:{id}"),
                                        Method::Get,
                                        format!("/api/sent/{}", enc(&id)),
                                        None,
                                    );
                                    (
                                        View::Request {
                                            row,
                                            detail,
                                            steps,
                                            whole,
                                            scroll,
                                        },
                                        true,
                                    )
                                }
                                (None, false) => (
                                    View::Request {
                                        row,
                                        detail,
                                        steps,
                                        whole,
                                        scroll,
                                    },
                                    true,
                                ),
                            }
                        }
                        KeyCode::Char('g') if row.get("known").and_then(Value::as_bool) == Some(true) => {
                            self.goto = Some(("Chat", s(&row, "session")));
                            (
                                View::Request {
                                    row,
                                    detail,
                                    steps,
                                    whole,
                                    scroll,
                                },
                                true,
                            )
                        }
                        _ => (
                            View::Request {
                                row,
                                detail,
                                steps,
                                whole,
                                scroll,
                            },
                            true,
                        ),
                    }
                }
            }
            View::List => {
                if self.table.key(k) {
                    (View::List, true)
                } else if k.modifiers.contains(KeyModifiers::CONTROL) {
                    (View::List, false)
                } else {
                    let mut refetch = true;
                    match k.code {
                        KeyCode::Char('1') => self.block = Block::Chats,
                        KeyCode::Char('2') => self.block = Block::Sent,
                        KeyCode::Char('p') => self.project = (self.project + 1) % (self.projects.len() + 2),
                        KeyCode::Char('t') => self.period = (self.period + 1) % PERIODS.len(),
                        KeyCode::Char('a') => self.archived = !self.archived,
                        KeyCode::Char('o') if self.block == Block::Chats => {
                            self.sort = (self.sort + 1) % SORTS.len();
                            refetch = false;
                            self.rows();
                        }
                        KeyCode::Char('f') => {
                            let mut f = Form::new(
                                "Search",
                                vec![Field::new("q", "Find", Kind::Text).value(self.query.clone()).help(
                                    match self.block {
                                        Block::Chats => "In the chats' titles and messages; empty: everything",
                                        Block::Sent => {
                                            "In the titles, the models and the whole requests; empty: everything"
                                        }
                                    },
                                )],
                            )
                            .submit_label("Find");
                            f.focus = 0;
                            self.view = View::Find(f);
                            refetch = false;
                        }
                        KeyCode::Char('c') => {
                            self.project = 0;
                            self.period = 3;
                            self.archived = false;
                            self.query.clear();
                        }
                        KeyCode::Char('r') => {}
                        KeyCode::Char('k') if self.block == Block::Sent => {
                            let now = self
                                .settings
                                .get("keep_sent_days")
                                .map(|v| v.to_string())
                                .unwrap_or_else(|| "14".into());
                            let mut f = Form::new(
                                "Keep the sent prompts",
                                vec![
                                    Field::new("days", "Keep them for (days)", Kind::Number)
                                        .value(now)
                                        .required()
                                        .help("0 keeps nothing. Prompts older than this are deleted for good."),
                                ],
                            )
                            .submit_label("Save");
                            f.focus = 0;
                            self.view = View::Keep(f);
                            refetch = false;
                        }
                        KeyCode::Enter if self.block == Block::Chats => {
                            if let Some(c) = self.chosen_chat() {
                                self.goto = Some(("Chat", s(&c, "id")));
                            }
                            refetch = false;
                        }
                        KeyCode::Enter => {
                            if let Some(r) = self.chosen_request() {
                                ctx.call(
                                    "request",
                                    Method::Get,
                                    format!(
                                        "/api/sent/request?session={}&key={}",
                                        enc(&s(&r, "session")),
                                        enc(&s(&r, "key"))
                                    ),
                                    None,
                                );
                                self.view = View::Request {
                                    row: r,
                                    detail: Value::Null,
                                    steps: Table::new(&[]),
                                    whole: None,
                                    scroll: 0,
                                };
                            }
                            refetch = false;
                        }
                        KeyCode::Char('n') if self.block == Block::Chats => {
                            if let Some(c) = self.chosen_chat() {
                                let mut f = Form::new(
                                    "Rename the chat",
                                    vec![
                                        Field::new("title", "New title", Kind::Text)
                                            .value(s(&c, "title"))
                                            .required(),
                                    ],
                                );
                                f.focus = 0;
                                self.view = View::Rename(s(&c, "id"), f);
                            }
                            refetch = false;
                        }
                        KeyCode::Char('A') if self.block == Block::Chats => {
                            if let Some(c) = self.chosen_chat() {
                                let to = !c.get("archived").and_then(Value::as_bool).unwrap_or(false);
                                ctx.call(
                                    format!("archived:{to}"),
                                    Method::Post,
                                    format!("/api/sessions/{}/archive", enc(&s(&c, "id"))),
                                    Some(json!({"archived": to})),
                                );
                            }
                            refetch = false;
                        }
                        KeyCode::Char('D') if self.block == Block::Chats => {
                            if let Some(c) = self.chosen_chat() {
                                let expected = if s(&c, "title").is_empty() {
                                    s(&c, "id")
                                } else {
                                    s(&c, "title")
                                };
                                let conf = Confirm::new(
                                    "Delete this chat permanently?",
                                    "Its messages can't be recovered (cost records are kept).",
                                    "delete it",
                                )
                                .typed(expected);
                                self.view = View::Delete(c, conf);
                            }
                            refetch = false;
                        }
                        _ => {
                            return {
                                self.view = View::List;
                                false
                            };
                        }
                    }
                    if refetch {
                        self.table.selected = 0;
                        self.fetch(ctx);
                    }
                    (std::mem::replace(&mut self.view, View::List), true)
                }
            }
        };
        self.view = view;
        used
    }

    fn paste(&mut self, text: &str) {
        match &mut self.view {
            View::Rename(_, f) | View::Find(f) | View::Keep(f) => f.paste(text),
            _ => {}
        }
    }

    fn on_answer(&mut self, a: Answer, ctx: &Ctx) {
        let (what, arg) = a.parts();
        let (what, arg) = (what.to_string(), arg.to_string());
        match (what.as_str(), a.result) {
            ("projects", Ok(v)) => {
                self.projects = v
                    .as_array()
                    .into_iter()
                    .flatten()
                    .filter_map(Value::as_str)
                    .map(str::to_string)
                    .collect();
                if let Some(want) = self.want.take() {
                    if let Some(i) = self.projects.iter().position(|p| *p == want) {
                        self.project = i + 2;
                        self.fetch(ctx);
                    }
                }
            }
            ("settings", Ok(v)) => self.settings = v,
            ("count", Ok(_)) => {}
            ("chats", Ok(v)) => {
                self.chats = v;
                if self.block == Block::Chats {
                    self.rows();
                }
            }
            ("sent", Ok(v)) => {
                self.sent = v;
                if self.block == Block::Sent {
                    self.rows();
                }
            }
            ("request", Ok(v)) => {
                if let View::Request { detail, steps, .. } = &mut self.view {
                    *steps = History::step_rows(&v);
                    *detail = v;
                }
            }
            ("whole", Ok(v)) => {
                if let View::Request { whole, scroll, .. } = &mut self.view {
                    *whole = Some(v);
                    *scroll = 0;
                }
            }
            ("renamed", Ok(_)) => {
                self.msg = Some((true, "Renamed.".into()));
                self.view = View::List;
                self.fetch(ctx);
            }
            ("archived", Ok(_)) => {
                self.msg = Some((
                    true,
                    if arg == "true" {
                        "Archived: a shows the archived chats."
                    } else {
                        "Back among the active chats."
                    }
                    .into(),
                ));
                self.fetch(ctx);
            }
            ("deleted", Ok(_)) => {
                self.msg = Some((true, "Deleted. Its cost records are kept.".into()));
                self.fetch(ctx);
            }
            ("preview", Ok(v)) => {
                let days: i64 = arg.parse().unwrap_or(14);
                let current = num(&v, "current").unwrap_or(14.0) as i64;
                let calls = num(&v, "calls").unwrap_or(0.0);
                if days < current && calls > 0.0 {
                    let c = Confirm::new(
                        format!("Keeping {days} day(s) deletes older prompts for good"),
                        format!(
                            "{:.0} request(s) ({calls:.0} call(s)) older than that would be deleted now, and this cannot be undone.",
                            num(&v, "requests").unwrap_or(0.0)
                        ),
                        format!("delete them and keep {days}"),
                    );
                    self.view = View::KeepAsk(days, c);
                } else {
                    ctx.save_settings(format!("kept:{days}"), json!({"keep_sent_days": days}));
                }
            }
            ("kept", Ok(v)) => {
                self.settings = v;
                let (days, prune) = arg.split_once('+').map_or((arg.as_str(), false), |(d, _)| (d, true));
                if prune {
                    let d: i64 = days.parse().unwrap_or(14);
                    ctx.call("pruned", Method::Post, "/api/sent/prune", Some(json!({"days": d})));
                }
                self.msg = Some((true, format!("The sent prompts are kept {days} day(s).")));
                self.fetch(ctx);
            }
            ("pruned", Ok(v)) => {
                self.msg = Some((
                    true,
                    format!(
                        "{} older call(s) deleted.",
                        v.get("deleted").map(|d| d.to_string()).unwrap_or_default()
                    ),
                ));
                self.fetch(ctx);
            }
            (_, Err(e)) => match &mut self.view {
                View::Rename(_, f) | View::Keep(f) => {
                    f.busy = false;
                    f.error = Some(e);
                }
                _ => self.msg = Some((false, e)),
            },
            _ => {}
        }
    }

    fn keys(&self) -> String {
        match (&self.view, self.block) {
            (View::List, Block::Chats) => {
                "Enter open in the chat · n rename · A archive · D delete · 2 what was sent · p project · t time · a archived · o sort · f find".into()
            }
            (View::List, Block::Sent) => "Enter its calls · k keep days · 1 chats · p project · t time · a archived · f find · c clear".into(),
            (View::Request { .. }, _) => "↑↓ a call · w the whole prompt · PgUp/PgDn scroll · g go to the chat · Esc back".into(),
            _ => String::new(),
        }
    }

    fn draw(&mut self, f: &mut Frame, area: Rect) -> Vec<(u16, usize)> {
        let width = area.width as usize;
        let body = draw_head(
            f,
            area,
            head_lines(
                "History",
                "  ·  every stored chat, and what was sent to the models",
                &self.msg,
                width,
            ),
        );
        let bold = Style::new().fg(Color::LightCyan).add_modifier(Modifier::BOLD);
        let tab = |on: bool| {
            if on {
                Style::new().fg(Color::Black).bg(Color::LightCyan)
            } else {
                DIM
            }
        };
        match &mut self.view {
            View::List => {
                let count = match self.block {
                    Block::Chats => format!("{} chat(s)", self.table.visible().len()),
                    Block::Sent => format!("{} request(s)", num(&self.sent, "count").unwrap_or(0.0)),
                };
                let mut top = vec![Line::from(vec![
                    Span::styled(" 1 Chats ", tab(self.block == Block::Chats)),
                    Span::raw(" "),
                    Span::styled(" 2 What was sent to the models ", tab(self.block == Block::Sent)),
                    Span::styled(format!("   {count}"), DIM),
                ])];
                let mut filters = format!(
                    "Project: {} · {} · {}",
                    self.project_label(),
                    PERIODS[self.period].1,
                    if self.archived { "archived" } else { "active" }
                );
                if self.block == Block::Chats {
                    filters.push_str(&format!(" · sorted by {}", SORTS[self.sort].1.to_lowercase()));
                }
                if !self.query.is_empty() {
                    filters.push_str(&format!(" · find “{}”", self.query));
                }
                if self.block == Block::Sent {
                    filters.push_str(&format!(
                        " · kept {} days",
                        self.settings
                            .get("keep_sent_days")
                            .map(|v| v.to_string())
                            .unwrap_or_default()
                    ));
                }
                top.push(Line::from(Span::styled(filters, DIM)));
                top.push(Line::default());
                let n = (top.len() as u16).min(body.height);
                f.render_widget(Paragraph::new(top), Rect { height: n, ..body });
                self.table.draw(
                    f,
                    Rect {
                        y: body.y + n,
                        height: body.height.saturating_sub(n),
                        ..body
                    },
                )
            }
            View::Rename(_, form) | View::Find(form) | View::Keep(form) => {
                let title = vec![Line::from(Span::styled(form.title.clone(), bold)), Line::default()];
                f.render_widget(Paragraph::new(title), Rect { height: 2, ..body });
                form.draw(
                    f,
                    Rect {
                        y: body.y + 2,
                        height: body.height.saturating_sub(2),
                        ..body
                    },
                );
                Vec::new()
            }
            View::Delete(_, c) | View::KeepAsk(_, c) => {
                let mut lines = vec![
                    Line::from(Span::styled(c.title.clone(), Style::new().fg(Color::Red))),
                    Line::default(),
                ];
                lines.extend(c.lines(width));
                f.render_widget(Paragraph::new(lines), body);
                Vec::new()
            }
            View::Request {
                row,
                detail,
                steps,
                whole,
                scroll,
            } => {
                let head = vec![
                    Line::from(Span::styled(
                        if s(row, "prompt").is_empty() {
                            role_name(&s(row, "role"))
                        } else {
                            s(row, "prompt")
                        },
                        bold,
                    )),
                    Line::from(Span::styled(
                        format!("{} · {}", s(row, "title"), s(row, "project")),
                        DIM,
                    )),
                    Line::default(),
                ];
                f.render_widget(
                    Paragraph::new(head),
                    Rect {
                        height: 3.min(body.height),
                        ..body
                    },
                );
                if detail.is_null() {
                    f.render_widget(
                        Paragraph::new(Span::styled("Loading…", DIM)),
                        Rect {
                            y: body.y + 3,
                            height: 1,
                            ..body
                        },
                    );
                    return Vec::new();
                }
                let table_h = (steps.visible().len() as u16 + 2)
                    .min(8)
                    .min(body.height.saturating_sub(4));
                let placed = steps.draw(
                    f,
                    Rect {
                        y: body.y + 3,
                        height: table_h,
                        ..body
                    },
                );
                let chosen = steps.chosen().map(|r| r.id.clone()).unwrap_or_default();
                let mut lines = Vec::new();
                if let Some(w) = whole {
                    lines.push(Line::from(Span::styled(
                        "The whole prompt of this call, as it left this machine (w hides it):",
                        DIM,
                    )));
                    lines.extend(part_lines(w.get("parts").unwrap_or(&Value::Null), width));
                } else if chosen == "meta" {
                    lines.extend(part_lines(detail.get("meta").unwrap_or(&Value::Null), width));
                } else if let Some(st) = detail
                    .get("steps")
                    .and_then(Value::as_array)
                    .and_then(|a| a.iter().find(|st| s(st, "id") == chosen))
                {
                    if let Some(e) = st.get("earlier").filter(|e| !e.is_null()) {
                        lines.push(Line::from(Span::styled(
                            format!(
                                "Earlier in this chat: {:.0} part(s) (≈{} tok), sent again with this call. w shows the whole prompt.",
                                num(e, "count").unwrap_or(0.0),
                                fmt::tokens((num(e, "chars").unwrap_or(0.0) / 4.0).round())
                            ),
                            DIM,
                        )));
                    }
                    lines.extend(part_lines(st.get("parts").unwrap_or(&Value::Null), width));
                    if !s(st, "answer").is_empty() {
                        lines.push(Line::from(Span::styled(
                            "── What it answered",
                            Style::new().fg(Color::Green),
                        )));
                        for l in s(st, "answer").lines() {
                            lines.extend(wrap(vec![Span::raw(l.to_string())], width, "", ""));
                        }
                    }
                }
                *scroll = (*scroll).min(lines.len().saturating_sub(1));
                let rest = Rect {
                    y: body.y + 3 + table_h,
                    height: body.height.saturating_sub(3 + table_h),
                    ..body
                };
                f.render_widget(
                    Paragraph::new(lines.into_iter().skip(*scroll).collect::<Vec<_>>()),
                    rest,
                );
                placed
            }
        }
    }

    fn tabs(&self) -> Vec<Vec<String>> {
        if !matches!(self.view, View::List) {
            return Vec::new();
        }
        vec![vec![" 1 Chats ".into(), " 2 What was sent to the models ".into()]]
    }

    fn click_tab(&mut self, _row: usize, i: usize, ctx: &Ctx) {
        let c = if i == 0 { '1' } else { '2' };
        self.key(KeyEvent::new(KeyCode::Char(c), KeyModifiers::NONE), ctx);
    }

    fn has_window(&self) -> bool {
        !matches!(self.view, View::List) || self.table.filtering
    }

    fn click_row(&mut self, i: usize, _ctx: &Ctx) -> bool {
        match &mut self.view {
            View::List => {
                self.table.selected = i.min(self.table.visible().len().saturating_sub(1));
            }
            View::Request {
                steps, whole, scroll, ..
            } => {
                steps.selected = i.min(steps.visible().len().saturating_sub(1));
                *whole = None;
                *scroll = 0;
            }
            _ => {}
        }
        false
    }

    fn take_goto(&mut self) -> Option<(&'static str, String)> {
        self.goto.take()
    }
}
