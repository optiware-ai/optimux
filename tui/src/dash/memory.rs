//! Dashboard → Memory, as in the web page: the personal, professional and project
//! memory files (edited in your editor), automatic project memory updates, "Update
//! now", and the project memory's history with its changes and undo.

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use ratatui::Frame;
use ratatui::layout::Rect;
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::Paragraph;
use serde_json::{Value, json};

use super::{Answer, Ctx, Section, draw_head, fmt, head_lines};
use crate::api::{Method, enc};
use crate::chat::text::{DIM, hard_wrap};
use crate::cli::{num, s};
use crate::dash::projects::ago;
use crate::ui::confirm::{self, Confirm};
use crate::ui::external::Edit;
use crate::ui::form::{self, Field, Form, Kind};
use crate::ui::table::{Row, Table};

/// The server's limit per file (memory_manager.MEMORY_MAX_CHARS).
pub const MAX_CHARS: usize = 20_000;

pub const TABS: &[(&str, &str)] = &[
    ("personal", "Personal"),
    ("professional", "Professional"),
    ("project", "Project"),
];

/// Where a memory file is read and written.
pub fn path_of(tab: &str, project: &str) -> String {
    if tab == "project" {
        format!("/api/memory/project/{}", enc(project))
    } else {
        format!("/api/memory/{tab}")
    }
}

/// The body that saves it.
pub fn save_body(tab: &str, content: &str) -> Value {
    if tab == "project" {
        json!({"content": content})
    } else {
        json!({"file_type": tab, "content": content})
    }
}

enum View {
    Text,
    History,
    Diff(Value),
    Confirm(Value, Confirm),
    Curate(Confirm),
    Idle(Form),
}

pub struct Memory {
    tab: usize,
    projects: Vec<String>,
    project: usize,
    content: String,
    exists: bool,
    loaded: bool,
    scroll: usize,
    versions: Value,
    history: Table,
    settings: Value,
    estimates: Value,
    view: View,
    pending_edit: Option<Edit>,
    busy: bool,
    msg: Option<(bool, String)>,
}

impl Default for Memory {
    fn default() -> Memory {
        let mut history = Table::new(&[("", 6), ("What changed", 0), ("Cost", 9), ("When", 13)]);
        history.empty = "No changes recorded yet.".into();
        Memory {
            tab: 0,
            projects: Vec::new(),
            project: 0,
            content: String::new(),
            exists: true,
            loaded: false,
            scroll: 0,
            versions: Value::Null,
            history,
            settings: Value::Null,
            estimates: Value::Null,
            view: View::Text,
            pending_edit: None,
            busy: false,
            msg: None,
        }
    }
}

impl Memory {
    fn tab(&self) -> &'static str {
        TABS[self.tab].0
    }

    fn project(&self) -> String {
        self.projects.get(self.project).cloned().unwrap_or_default()
    }

    fn fetch(&mut self, ctx: &Ctx) {
        self.loaded = false;
        self.scroll = 0;
        if self.tab() == "project" {
            let p = self.project();
            if p.is_empty() {
                self.content.clear();
                self.loaded = true;
                return;
            }
            ctx.call(format!("content:{p}"), Method::Get, path_of("project", &p), None);
            ctx.call(
                format!("versions:{p}"),
                Method::Get,
                format!("/api/memory/project/{}/versions", enc(&p)),
                None,
            );
        } else {
            ctx.call(
                format!("content:{}", self.tab()),
                Method::Get,
                path_of(self.tab(), ""),
                None,
            );
        }
    }

    fn history_rows(&mut self) {
        let rows = self
            .versions
            .as_array()
            .into_iter()
            .flatten()
            .map(|v| {
                let who = match s(v, "source").as_str() {
                    "curator" => "auto",
                    "user" => "you",
                    "model" => "model",
                    "undo" => "undo",
                    o => o,
                }
                .to_string();
                Row::new(s(v, "id"))
                    .styled(who, Style::new().fg(Color::LightCyan))
                    .cell(s(v, "summary"))
                    .styled(num(v, "cost").map(fmt::cost).unwrap_or_default(), DIM)
                    .styled(num(v, "ts").map(ago).unwrap_or_default(), DIM)
            })
            .collect();
        self.history.set_rows(rows);
    }

    fn chosen_version(&self) -> Option<Value> {
        let id = self.history.chosen()?.id.clone();
        self.versions.as_array()?.iter().find(|v| s(v, "id") == id).cloned()
    }

    fn curate(&mut self, ctx: &Ctx) {
        let p = self.project();
        self.busy = true;
        self.msg = Some((true, format!("Updating the memory of {p} from its latest chat…")));
        ctx.call(
            "curated",
            Method::Post,
            format!("/api/memory/project/{}/curate", enc(&p)),
            None,
        );
    }
}

impl Section for Memory {
    fn load(&mut self, ctx: &Ctx) {
        ctx.call("projects", Method::Get, "/api/projects", None);
        ctx.call("settings", Method::Get, "/api/settings", None);
        ctx.call("estimates", Method::Get, "/api/costs/estimates", None);
        self.fetch(ctx);
    }

    fn key(&mut self, k: KeyEvent, ctx: &Ctx) -> bool {
        let view = std::mem::replace(&mut self.view, View::Text);
        let (view, used) = match view {
            View::Confirm(v, mut c) => match c.key(k) {
                confirm::Answer::Yes => {
                    ctx.call(
                        "undone",
                        Method::Post,
                        format!("/api/memory/versions/{}/undo", s(&v, "id")),
                        None,
                    );
                    (View::History, true)
                }
                confirm::Answer::No => (View::History, true),
                confirm::Answer::Waiting => (View::Confirm(v, c), true),
            },
            View::Curate(mut c) => match c.key(k) {
                confirm::Answer::Yes => {
                    self.curate(ctx);
                    (View::Text, true)
                }
                confirm::Answer::No => (View::Text, true),
                confirm::Answer::Waiting => (View::Curate(c), true),
            },
            View::Idle(mut f) => match f.key(k) {
                form::Action::Cancel => (View::Text, true),
                form::Action::Submit => match f.get("minutes").trim().parse::<i64>() {
                    Ok(n) if (1..=240).contains(&n) => {
                        ctx.save_settings("settings_saved", json!({"curator_idle_minutes": n}));
                        (View::Text, true)
                    }
                    _ => {
                        f.error = Some("A whole number from 1 to 240.".into());
                        (View::Idle(f), true)
                    }
                },
                form::Action::None => (View::Idle(f), true),
            },
            View::Diff(v) => match k.code {
                KeyCode::Esc | KeyCode::Enter => (View::History, true),
                KeyCode::Up => {
                    self.scroll = self.scroll.saturating_sub(1);
                    (View::Diff(v), true)
                }
                KeyCode::Down => {
                    self.scroll += 1;
                    (View::Diff(v), true)
                }
                _ => (View::Diff(v), true),
            },
            View::History => {
                if self.history.key(k) {
                    (View::History, true)
                } else {
                    match k.code {
                        KeyCode::Esc | KeyCode::Char('h') => (View::Text, true),
                        KeyCode::Enter => match self.chosen_version() {
                            Some(v) => {
                                self.scroll = 0;
                                (View::Diff(v), true)
                            }
                            None => (View::History, true),
                        },
                        KeyCode::Char('z') => match self.chosen_version() {
                            Some(v) => {
                                let c = Confirm::new(
                                    "Undo this memory change?",
                                    format!(
                                        "{}\nThe project memory goes back to how it was before this change. The undo is recorded too, so it can be undone.",
                                        s(&v, "summary")
                                    ),
                                    "undo it",
                                );
                                (View::Confirm(v, c), true)
                            }
                            None => (View::History, true),
                        },
                        _ => (View::History, true),
                    }
                }
            }
            View::Text => {
                let project_tab = self.tab() == "project";
                match k.code {
                    KeyCode::Char(c @ '1'..='3') => {
                        self.tab = c as usize - '1' as usize;
                        self.msg = None;
                        self.fetch(ctx);
                    }
                    KeyCode::Char('p') if project_tab && !self.projects.is_empty() => {
                        self.project = (self.project + 1) % self.projects.len();
                        self.msg = None;
                        self.fetch(ctx);
                    }
                    KeyCode::Char('e') | KeyCode::Enter if self.loaded => {
                        if project_tab && self.project().is_empty() {
                            self.msg = Some((false, "No project yet.".into()));
                        } else {
                            self.pending_edit = Some(Edit {
                                text: self.content.clone(),
                                suffix: ".md".into(),
                            });
                        }
                    }
                    KeyCode::Char('a') if project_tab => {
                        let on = self.settings.get("memory_curator").and_then(Value::as_bool) == Some(true);
                        ctx.save_settings("settings_saved", json!({"memory_curator": !on}));
                    }
                    KeyCode::Char('i') if project_tab => {
                        let now = self
                            .settings
                            .get("curator_idle_minutes")
                            .map(|v| v.to_string())
                            .unwrap_or_default();
                        let mut f = Form::new(
                            "Idle minutes",
                            vec![
                                Field::new("minutes", "After idle minutes", Kind::Number)
                                    .value(now)
                                    .required()
                                    .help("1 to 240: how long a chat rests before its automatic memory update"),
                            ],
                        );
                        f.focus = 0;
                        return {
                            self.view = View::Idle(f);
                            true
                        };
                    }
                    KeyCode::Char('u') if project_tab && !self.busy && !self.project().is_empty() => {
                        if self.settings.get("confirm_update_memory").and_then(Value::as_bool) == Some(false) {
                            self.curate(ctx);
                        } else {
                            let est = self.estimates.get("memory_update").filter(|v| !v.is_null());
                            let cost = match est {
                                Some(e) => format!(
                                    " About {} per update (the average of your last {:.0}).",
                                    fmt::cost(num(e, "avg").unwrap_or(0.0)),
                                    num(e, "n").unwrap_or(0.0)
                                ),
                                None => " No priced update yet.".into(),
                            };
                            return {
                                self.view = View::Curate(Confirm::new(
                                    "Update memory now?",
                                    format!(
                                        "The Simple model reads the whole latest chat of {} and rewrites its memory. This is one paid model call.{cost}\n(This question can be turned off in Budgets → Cost warnings.)",
                                        self.project()
                                    ),
                                    "update it",
                                ));
                                true
                            };
                        }
                    }
                    KeyCode::Char('h') if project_tab => {
                        self.history_rows();
                        return {
                            self.view = View::History;
                            true
                        };
                    }
                    KeyCode::Char('r') => self.fetch(ctx),
                    KeyCode::Up => self.scroll = self.scroll.saturating_sub(1),
                    KeyCode::Down => self.scroll += 1,
                    KeyCode::PageUp => self.scroll = self.scroll.saturating_sub(10),
                    KeyCode::PageDown => self.scroll += 10,
                    _ => return false,
                }
                (std::mem::replace(&mut self.view, View::Text), true)
            }
        };
        self.view = view;
        used
    }

    fn take_edit(&mut self) -> Option<Edit> {
        self.pending_edit.take()
    }

    fn edited(&mut self, result: Result<Option<String>, String>, ctx: &Ctx) {
        match result {
            Err(e) => self.msg = Some((false, e)),
            Ok(None) => self.msg = Some((true, "No change.".into())),
            Ok(Some(text)) if text.chars().count() > MAX_CHARS => {
                self.msg = Some((
                    false,
                    format!(
                        "Too long: {} characters, the limit is {MAX_CHARS}. Nothing was saved.",
                        text.chars().count()
                    ),
                ))
            }
            Ok(Some(text)) => {
                let (tab, p) = (self.tab(), self.project());
                self.msg = Some((true, "Saving…".into()));
                ctx.call(
                    "saved",
                    Method::Post,
                    if tab == "project" {
                        path_of(tab, &p)
                    } else {
                        "/api/memory".into()
                    },
                    Some(save_body(tab, &text)),
                );
                self.content = text;
            }
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
                // start on the active project
                let active = s(&self.settings, "active_project");
                if let Some(i) = self.projects.iter().position(|p| *p == active) {
                    self.project = i;
                }
            }
            ("settings", Ok(v)) => {
                let active = s(&v, "active_project");
                if self.settings.is_null() {
                    if let Some(i) = self.projects.iter().position(|p| *p == active) {
                        self.project = i;
                    }
                }
                self.settings = v;
            }
            ("settings_saved", Ok(v)) => {
                self.settings = v;
                self.msg = Some((
                    true,
                    format!(
                        "Automatic project memory updates: {}, after {} idle minutes.",
                        if self.settings.get("memory_curator").and_then(Value::as_bool) == Some(true) {
                            "on"
                        } else {
                            "off"
                        },
                        shown_number(self.settings.get("curator_idle_minutes"))
                    ),
                ));
            }
            ("estimates", Ok(v)) => self.estimates = v,
            ("content", Ok(v)) => {
                let expected = if self.tab() == "project" {
                    self.project()
                } else {
                    self.tab().to_string()
                };
                if arg == expected {
                    self.content = s(&v, "content");
                    self.exists = v.get("exists").and_then(Value::as_bool) != Some(false);
                    self.loaded = true;
                }
            }
            ("versions", Ok(v)) => {
                if arg == self.project() {
                    self.versions = v;
                    self.history_rows();
                }
            }
            ("saved", Ok(_)) => {
                self.msg = Some((true, "Saved. Every model gets it from the next request.".into()));
                self.exists = true;
                self.fetch(ctx);
            }
            ("undone", Ok(_)) => {
                self.msg = Some((true, "Undone.".into()));
                self.fetch(ctx);
            }
            ("curated", Ok(v)) => {
                self.busy = false;
                let changed = v.get("changed").and_then(Value::as_bool) == Some(true);
                let mut text = String::new();
                if !s(&v, "chat_title").is_empty() {
                    text.push_str(&format!("Read “{}”. ", s(&v, "chat_title")));
                }
                text.push_str(&if changed {
                    format!("Updated: {}", s(&v, "summary"))
                } else {
                    format!("No update: {}", s(&v, "reason"))
                });
                if let Some(c) = num(&v, "cost") {
                    text.push_str(&format!(" ({})", fmt::cost(c)));
                }
                self.msg = Some((true, text));
                self.fetch(ctx);
            }
            (_, Err(e)) => {
                self.busy = false;
                if let View::Idle(f) = &mut self.view {
                    f.error = Some(e);
                } else {
                    self.msg = Some((false, e));
                }
            }
            _ => {}
        }
    }

    fn keys(&self) -> String {
        match &self.view {
            View::Text if self.tab() == "project" => {
                "1-3 file · p project · e edit · a auto updates · i idle minutes · u update now · h history · Esc menu"
                    .into()
            }
            View::Text => "1-3 file · e edit in your editor · ↑↓ scroll · r reload · Esc menu".into(),
            View::History => "Enter its changes · z undo it · Esc back".into(),
            View::Diff(_) => "↑↓ scroll · Esc back".into(),
            _ => String::new(),
        }
    }

    fn draw(&mut self, f: &mut Frame, area: Rect) -> Vec<(u16, usize)> {
        let width = area.width as usize;
        let body = draw_head(
            f,
            area,
            head_lines(
                "Memory",
                "  ·  what every model is told about you and your work",
                &self.msg,
                width,
            ),
        );
        let mut top = Vec::new();
        let mut spans = vec![];
        for (i, (_, label)) in TABS.iter().enumerate() {
            let style = if i == self.tab {
                Style::new().fg(Color::Black).bg(Color::LightCyan)
            } else {
                DIM
            };
            spans.push(Span::styled(format!(" {} {label} ", i + 1), style));
            spans.push(Span::raw(" "));
        }
        if self.tab() == "project" {
            spans.push(Span::styled("  Project ", DIM));
            spans.push(Span::styled(
                if self.project().is_empty() {
                    "none".into()
                } else {
                    self.project()
                },
                Style::new().fg(Color::LightCyan).add_modifier(Modifier::BOLD),
            ));
        }
        top.push(Line::from(spans));
        let chars = self.content.chars().count();
        let mut info = vec![Span::styled(
            format!(
                "{chars} / {MAX_CHARS} characters · ≈{} tokens on every request",
                chars.div_ceil(4)
            ),
            if chars > MAX_CHARS {
                Style::new().fg(Color::Red)
            } else {
                DIM
            },
        )];
        if self.tab() == "project" {
            if !self.exists {
                info.push(Span::styled("  · not created yet", Style::new().fg(Color::Yellow)));
            }
            let on = self.settings.get("memory_curator").and_then(Value::as_bool) == Some(true);
            info.push(Span::styled(
                format!(
                    "  · automatic updates {}{}",
                    if on { "on, after " } else { "off" },
                    if on {
                        format!(
                            "{} idle minutes",
                            shown_number(self.settings.get("curator_idle_minutes"))
                        )
                    } else {
                        String::new()
                    }
                ),
                DIM,
            ));
        }
        top.push(Line::from(info));
        top.push(Line::default());
        let n = top.len() as u16;
        f.render_widget(
            Paragraph::new(top),
            Rect {
                height: n.min(body.height),
                ..body
            },
        );
        let rest = Rect {
            y: body.y + n,
            height: body.height.saturating_sub(n),
            ..body
        };
        let bold = Style::new().fg(Color::LightCyan).add_modifier(Modifier::BOLD);
        match &self.view {
            View::Text | View::Idle(_) | View::Curate(_) => {
                if let View::Idle(form) = &self.view {
                    form.draw(f, rest);
                    return Vec::new();
                }
                if let View::Curate(c) = &self.view {
                    let mut lines = vec![Line::from(Span::styled(c.title.clone(), bold)), Line::default()];
                    lines.extend(c.lines(width));
                    f.render_widget(Paragraph::new(lines), rest);
                    return Vec::new();
                }
                if !self.loaded {
                    f.render_widget(Paragraph::new(Span::styled("Loading…", DIM)), rest);
                    return Vec::new();
                }
                let lines: Vec<Line> = self
                    .content
                    .lines()
                    .flat_map(|l| hard_wrap(l, width.max(10), "", Style::new()))
                    .collect();
                self.scroll = self.scroll.min(lines.len().saturating_sub(1));
                f.render_widget(
                    Paragraph::new(lines.into_iter().skip(self.scroll).collect::<Vec<_>>()),
                    rest,
                );
                Vec::new()
            }
            View::History => self.history.draw(f, rest),
            View::Diff(v) => {
                let mut lines = vec![Line::from(Span::styled(s(v, "summary"), bold)), Line::default()];
                for l in s(v, "diff").lines() {
                    let style = if l.starts_with('+') {
                        Style::new().fg(Color::Green)
                    } else if l.starts_with('-') {
                        Style::new().fg(Color::Red)
                    } else {
                        DIM
                    };
                    lines.push(Line::from(Span::styled(l.to_string(), style)));
                }
                f.render_widget(
                    Paragraph::new(lines.into_iter().skip(self.scroll).collect::<Vec<_>>()),
                    rest,
                );
                Vec::new()
            }
            View::Confirm(_, c) => {
                let mut lines = vec![Line::from(Span::styled(c.title.clone(), bold)), Line::default()];
                lines.extend(c.lines(width));
                f.render_widget(Paragraph::new(lines), rest);
                Vec::new()
            }
        }
    }

    fn tabs(&self) -> Vec<Vec<String>> {
        if !matches!(self.view, View::Text) {
            return Vec::new();
        }
        vec![
            TABS.iter()
                .enumerate()
                .map(|(i, (_, l))| format!(" {} {l} ", i + 1))
                .collect(),
        ]
    }

    fn click_tab(&mut self, _row: usize, i: usize, ctx: &Ctx) {
        // the same as its number key
        let c = char::from(b'1' + i.min(TABS.len() - 1) as u8);
        self.key(KeyEvent::new(KeyCode::Char(c), KeyModifiers::NONE), ctx);
    }

    fn has_window(&self) -> bool {
        !matches!(self.view, View::Text) || self.history.filtering
    }

    fn click_row(&mut self, i: usize, _ctx: &Ctx) -> bool {
        if matches!(self.view, View::History) {
            self.history.selected = i.min(self.history.visible().len().saturating_sub(1));
        }
        false
    }
}

fn shown_number(v: Option<&Value>) -> String {
    v.map(|n| n.to_string()).unwrap_or_else(|| "?".into())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn where_each_file_goes() {
        assert_eq!(path_of("personal", ""), "/api/memory/personal");
        assert_eq!(path_of("project", "my app"), "/api/memory/project/my%20app");
        assert_eq!(
            save_body("professional", "x"),
            json!({"file_type": "professional", "content": "x"})
        );
        assert_eq!(save_body("project", "x"), json!({"content": "x"}));
    }
}
