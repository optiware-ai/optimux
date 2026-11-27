//! The Dashboard: the web page's Dashboard in the terminal (Ctrl+G). A menu of sections
//! on the left, the section on the right. Each section talks to the same `/api/...`
//! calls as the web page (see tests/test_tui_api.py). Sections come step by step; the
//! ones not built yet say where to find them in the web page.
//!
//! A section is a `Section`: it loads when shown, takes the keys while it is in front,
//! and gets back the answers of its own calls (`Answer::section`).

pub mod backups;
pub mod budgets;
pub mod execution;
pub mod fmt;
pub mod git;
pub mod github;
pub mod guide;
pub mod history;
pub mod memory;
pub mod models;
pub mod overview;
pub mod performance;
pub mod project;
pub mod projects;
pub mod routing;
pub mod scope;
pub mod skills;
pub mod start;
pub mod studio;
pub mod system;
pub mod usage;

use std::collections::HashMap;
use unicode_width::UnicodeWidthStr;

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use ratatui::Frame;
use ratatui::layout::{Constraint, Layout, Rect};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, Borders, Paragraph};
use serde_json::Value;
use tokio::sync::mpsc::UnboundedSender;

use crate::api::{Api, Method};
use crate::chat::app::Bg;
use crate::chat::link::Cred;
use crate::chat::text::{DIM, wrap};

/// The web page's sections, in its order. `step`: when it comes to the terminal
/// (0: here now). Video and Team stay in the web page.
pub const SECTIONS: &[(&str, &str, u8)] = &[
    ("Files", "Studio", 0),
    ("Activity", "Overview", 0),
    ("Activity", "Usage", 0),
    ("Activity", "Performance", 0),
    ("Activity", "Budgets", 0),
    ("Activity", "Projects", 0),
    ("Activity", "History", 0),
    ("Configuration", "Models", 0),
    ("Configuration", "Routing", 0),
    ("Configuration", "Project", 0),
    ("Configuration", "Memory", 0),
    ("Configuration", "Skills", 0),
    ("Configuration", "Execution", 0),
    ("Configuration", "GitHub", 0),
    ("Configuration", "Scope", 0),
    ("Configuration", "Backups", 0),
    ("Configuration", "System", 0),
    ("Help", "Getting started", 0),
    ("Help", "Guide", 0),
];

/// The section of this name, when it is in the terminal already.
fn make(name: &str) -> Option<Box<dyn Section>> {
    Some(match name {
        "Models" => Box::new(models::Models::default()),
        "Routing" => Box::new(routing::Routing::default()),
        "Budgets" => Box::new(budgets::Budgets::default()),
        "Usage" => Box::new(usage::Usage::default()),
        "Performance" => Box::new(performance::Performance::default()),
        "Projects" => Box::new(projects::Projects::default()),
        "Project" => Box::new(project::ProjectPage::default()),
        "Scope" => Box::new(scope::Scope::default()),
        "Execution" => Box::new(execution::Execution::default()),
        "Memory" => Box::new(memory::Memory::default()),
        "Skills" => Box::new(skills::Skills::default()),
        "History" => Box::new(history::History::default()),
        "Overview" => Box::new(overview::Overview::default()),
        "System" => Box::new(system::System::default()),
        "GitHub" => Box::new(github::GitHub::default()),
        "Backups" => Box::new(backups::Backups::default()),
        "Getting started" => Box::new(start::Start::default()),
        "Guide" => Box::new(guide::Guide::default()),
        "Studio" => Box::new(studio::Studio::default()),
        _ => return None,
    })
}

const MENU_W: u16 = 22;
const ACCENT: Color = Color::LightCyan;

/// One section of the Dashboard.
pub trait Section {
    /// Shown (again): load what it shows.
    fn load(&mut self, ctx: &Ctx);
    /// A key while the section is in front; false: not its key (Esc goes to the menu).
    fn key(&mut self, k: KeyEvent, ctx: &Ctx) -> bool;
    fn paste(&mut self, _text: &str) {}
    /// The answer of one of its own calls.
    fn on_answer(&mut self, a: Answer, ctx: &Ctx);
    /// Its keys, for the bottom row.
    fn keys(&self) -> String;
    /// Draws itself; returns its table's drawn rows (screen row, index) for clicks.
    fn draw(&mut self, f: &mut Frame, area: Rect) -> Vec<(u16, usize)>;
    /// A form or a question is open: Esc and Ctrl+G are its own.
    fn has_window(&self) -> bool;
    /// Its keys are also shown in cyan under what it draws (as a form's are). Off
    /// while a window is open (a form or a question shows its own) and for the pages
    /// that are one form.
    fn keys_under(&self) -> bool {
        !self.has_window()
    }
    /// A click on a row of its table.
    /// A click on row i of the list it drew. True when it did the row's action itself
    /// (a folder opens); false when it only chose the row: a second click on the
    /// chosen row is then Enter.
    fn click_row(&mut self, _i: usize, _ctx: &Ctx) -> bool {
        false
    }
    /// Its rows of tabs, each label exactly as drawn (" 7 days "): the Dashboard finds
    /// them on the screen and a click on one calls click_tab(row, i).
    fn tabs(&self) -> Vec<Vec<String>> {
        Vec::new()
    }
    fn click_tab(&mut self, _row: usize, _i: usize, _ctx: &Ctx) {}
    /// Opened from another section for one thing (a project's page): before load().
    fn open_with(&mut self, _arg: &str) {}
    /// The section asks to show another one (its name and what to open there);
    /// ("Chat", id) opens a chat in the chat window; ("ChatText", text) puts text in
    /// the prompt box and goes back to the chat.
    fn take_goto(&mut self) -> Option<(&'static str, String)> {
        None
    }
    /// The section asks for a long text to be edited in the user's editor.
    fn take_edit(&mut self) -> Option<crate::ui::external::Edit> {
        None
    }
    /// What came back from the editor: Some(text) changed, None unchanged, Err why not.
    fn edited(&mut self, _result: Result<Option<String>, String>, _ctx: &Ctx) {}
}

/// What a section needs to call the server; the answers come back as `Bg::Dash`.
#[derive(Clone)]
pub struct Ctx {
    pub api: Api,
    pub cred: Cred,
    pub bg: UnboundedSender<Bg>,
    /// Whose calls these are: the answers go back to this section.
    pub section: &'static str,
}

/// A server answer: `section` and `tag` say whose call it was.
#[derive(Debug)]
pub struct Answer {
    pub section: &'static str,
    pub tag: String,
    pub result: Result<Value, String>,
}

impl Answer {
    /// "what:arg" tags: (what, arg).
    pub fn parts(&self) -> (&str, &str) {
        self.tag.split_once(':').unwrap_or((self.tag.as_str(), ""))
    }
}

impl Ctx {
    fn for_section(&self, section: &'static str) -> Ctx {
        Ctx {
            section,
            ..self.clone()
        }
    }

    fn send(&self, tag: String, result: Result<Value, String>) {
        let _ = self.bg.send(Bg::Dash(Answer {
            section: self.section,
            tag,
            result,
        }));
    }

    /// One call, in the background.
    pub fn call(&self, tag: impl Into<String>, method: Method, path: impl Into<String>, body: Option<Value>) {
        let (me, tag, path) = (self.clone(), tag.into(), path.into());
        tokio::spawn(async move {
            let result = me
                .api
                .call(&me.cred.auth(), method, &path, body.as_ref())
                .await
                .map_err(|e| format!("{e:#}"));
            me.send(tag, result);
        });
    }

    /// One call after a wait (polling a job), in the background.
    pub fn call_later(&self, ms: u64, tag: impl Into<String>, method: Method, path: impl Into<String>) {
        let (me, tag, path) = (self.clone(), tag.into(), path.into());
        tokio::spawn(async move {
            tokio::time::sleep(std::time::Duration::from_millis(ms)).await;
            let result = me
                .api
                .call(&me.cred.auth(), method, &path, None)
                .await
                .map_err(|e| format!("{e:#}"));
            me.send(tag, result);
        });
    }

    /// A download saved to a local file, in the background; the answer is
    /// {"bytes", "path"}.
    pub fn download(&self, tag: impl Into<String>, path: impl Into<String>, dest: std::path::PathBuf) {
        let (me, tag, path) = (self.clone(), tag.into(), path.into());
        tokio::spawn(async move {
            let result = me
                .api
                .download_to(&me.cred.auth(), &path, &dest)
                .await
                .map(|n| serde_json::json!({"bytes": n, "path": dest.display().to_string()}))
                .map_err(|e| format!("{e:#}"));
            me.send(tag, result);
        });
    }

    /// A local file sent as the request body, in the background.
    pub fn upload(&self, tag: impl Into<String>, path: impl Into<String>, file: std::path::PathBuf) {
        let (me, tag, path) = (self.clone(), tag.into(), path.into());
        tokio::spawn(async move {
            let result = me
                .api
                .upload_file(&me.cred.auth(), &path, &file)
                .await
                .map_err(|e| format!("{e:#}"));
            me.send(tag, result);
        });
    }

    /// Several calls in order, in the background: the main call first, then what goes
    /// with it (a key), only if the one before worked. The answer is the main call's, or
    /// the first failure.
    pub fn calls(&self, tag: impl Into<String>, steps: Vec<(Method, String, Option<Value>)>) {
        let (me, tag) = (self.clone(), tag.into());
        tokio::spawn(async move {
            let mut first: Option<Result<Value, String>> = None;
            for (method, path, body) in steps {
                let r = me
                    .api
                    .call(&me.cred.auth(), method, &path, body.as_ref())
                    .await
                    .map_err(|e| format!("{e:#}"));
                let failed = r.is_err();
                match (&first, r) {
                    (None, r) => first = Some(r),
                    (Some(_), Err(e)) => first = Some(Err(format!("Saved, but the key was not: {e}"))),
                    _ => {}
                }
                if failed {
                    break;
                }
            }
            me.send(tag, first.unwrap_or(Ok(Value::Null)));
        });
    }

    /// Changes some settings. `POST /api/settings` saves every setting (PROTOCOL.md), so
    /// all of them are read first and sent back with only these changed. The answer
    /// is the settings as saved.
    pub fn save_settings(&self, tag: impl Into<String>, changes: Value) {
        let (me, tag) = (self.clone(), tag.into());
        tokio::spawn(async move {
            let auth = me.cred.auth();
            let result = async {
                let mut all = me.api.call(&auth, Method::Get, "/api/settings", None).await?;
                let (Some(obj), Some(ch)) = (all.as_object_mut(), changes.as_object()) else {
                    anyhow::bail!("The server's settings came back in an unexpected form.");
                };
                for (k, v) in ch {
                    obj.insert(k.clone(), v.clone());
                }
                let answer = me.api.call(&auth, Method::Post, "/api/settings", Some(&all)).await?;
                // the server's note (a new Workspace without the active project) as "_note"
                if let (Some(note), Some(obj)) = (answer.get("note").filter(|n| !n.is_null()), all.as_object_mut()) {
                    obj.insert("_note".into(), note.clone());
                }
                Ok(all)
            }
            .await
            .map_err(|e| format!("{e:#}"));
            me.send(tag, result);
        });
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Close {
    Stay,
    Close,
}

/// What a draw left clickable: the menu's sections (area, index) and the table's rows
/// (screen row, index).
pub type Drawn = (Vec<(Rect, usize)>, Vec<(u16, usize)>);

/// What else can be clicked in the section drawn last: its tabs (the place, the row
/// of tabs, the tab) and its key line (where each line of it starts, and its text).
#[derive(Default)]
pub struct Spots {
    pub tabs: Vec<(Rect, usize, usize)>,
    pub keys: Vec<(u16, u16, String)>,
}

pub struct Dash {
    pub section: usize,
    /// A chat to open in the chat window (a section asked for it with ("Chat", id)).
    pub open_chat: Option<String>,
    /// Text for the chat's prompt box (the Studio's "send to chat").
    pub chat_text: Option<String>,
    /// The keys go to the menu (true) or to the section.
    pub in_menu: bool,
    open: HashMap<&'static str, Box<dyn Section>>,
    ctx: Ctx,
    /// Tabs and keys of the section drawn last.
    pub spots: Spots,
    /// The row clicked last (section, row): clicking it again is Enter.
    last_click: Option<(&'static str, usize)>,
}

impl Dash {
    pub fn new(ctx: Ctx) -> Dash {
        let section = SECTIONS.iter().position(|(_, n, _)| *n == "Models").unwrap_or(0);
        let mut d = Dash {
            section,
            open_chat: None,
            chat_text: None,
            in_menu: false,
            open: HashMap::new(),
            ctx,
            spots: Spots::default(),
            last_click: None,
        };
        d.show();
        d
    }

    pub fn name(&self) -> &'static str {
        SECTIONS[self.section].1
    }

    /// The section is shown: made the first time, then it loads what it shows.
    fn show(&mut self) {
        let name = self.name();
        if !self.open.contains_key(name) {
            if let Some(s) = make(name) {
                self.open.insert(name, s);
            }
        }
        let ctx = self.ctx.for_section(name);
        if let Some(s) = self.open.get_mut(name) {
            s.load(&ctx);
        }
    }

    /// The section in front and its context.
    fn front(&mut self) -> Option<(&mut Box<dyn Section>, Ctx)> {
        let name = self.name();
        let ctx = self.ctx.for_section(name);
        self.open.get_mut(name).map(|s| (s, ctx))
    }

    /// Shows the section of this name (from outside the Dashboard).
    pub fn open_named(&mut self, name: &str) {
        if let Some(i) = SECTIONS.iter().position(|(_, n, _)| *n == name) {
            self.choose(i);
        }
    }

    pub fn choose(&mut self, i: usize) {
        if i < SECTIONS.len() {
            self.section = i;
            self.in_menu = false;
            self.show();
        }
    }

    fn busy_section(&mut self) -> bool {
        self.front().is_some_and(|(s, _)| s.has_window())
    }

    pub fn key(&mut self, k: KeyEvent) -> Close {
        self.last_click = None;
        let ctrl = k.modifiers.contains(KeyModifiers::CONTROL);
        if ctrl && k.code == KeyCode::Char('g') && (self.in_menu || !self.busy_section()) {
            return Close::Close;
        }
        if self.in_menu {
            match k.code {
                KeyCode::Up => self.section = self.section.saturating_sub(1),
                KeyCode::Down => self.section = (self.section + 1).min(SECTIONS.len() - 1),
                KeyCode::Home => self.section = 0,
                KeyCode::End => self.section = SECTIONS.len() - 1,
                KeyCode::Enter | KeyCode::Right | KeyCode::Tab => self.choose(self.section),
                KeyCode::Esc => return Close::Close,
                _ => {}
            }
            return Close::Stay;
        }
        let used = self.front().is_some_and(|(s, ctx)| s.key(k, &ctx));
        if !used && matches!(k.code, KeyCode::Esc | KeyCode::Left | KeyCode::BackTab | KeyCode::Tab) {
            self.in_menu = true;
        }
        self.follow_goto();
        Close::Stay
    }

    /// A section asked for another one: open it there.
    fn follow_goto(&mut self) {
        let Some((name, arg)) = self.front().and_then(|(s, _)| s.take_goto()) else {
            return;
        };
        if name == "Chat" {
            self.open_chat = Some(arg);
            return;
        }
        if name == "ChatText" {
            self.chat_text = Some(arg);
            return;
        }
        let Some(i) = SECTIONS.iter().position(|(_, n, _)| *n == name) else {
            return;
        };
        self.section = i;
        self.in_menu = false;
        if !self.open.contains_key(name) {
            if let Some(s) = make(name) {
                self.open.insert(name, s);
            }
        }
        if let Some(s) = self.open.get_mut(name) {
            s.open_with(&arg);
        }
        self.show();
    }

    pub fn click_row(&mut self, i: usize) {
        self.in_menu = false;
        let name = self.name();
        let again = self.last_click == Some((name, i));
        self.last_click = Some((name, i));
        if let Some((s, ctx)) = self.front() {
            let done = s.click_row(i, &ctx);
            if done {
                self.last_click = None;
            } else if again {
                // the chosen row clicked again: what Enter does (open it)
                s.key(KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE), &ctx);
                self.last_click = None;
            }
        }
        self.follow_goto();
    }

    pub fn click_tab(&mut self, row: usize, i: usize) {
        self.in_menu = false;
        self.last_click = None;
        if let Some((s, ctx)) = self.front() {
            s.click_tab(row, i, &ctx);
        }
        self.follow_goto();
    }

    /// A text to edit in the user's editor, asked for by the section in front.
    pub fn take_edit(&mut self) -> Option<crate::ui::external::Edit> {
        self.front().and_then(|(s, _)| s.take_edit())
    }

    pub fn edited(&mut self, result: Result<Option<String>, String>) {
        if let Some((s, ctx)) = self.front() {
            s.edited(result, &ctx);
        }
    }

    pub fn paste(&mut self, text: &str) {
        if let Some((s, _)) = self.front() {
            s.paste(text);
        }
    }

    pub fn on_answer(&mut self, a: Answer) {
        let ctx = self.ctx.for_section(a.section);
        if let Some(s) = self.open.get_mut(a.section) {
            s.on_answer(a, &ctx);
        }
    }

    /// The keys shown at the bottom: the section's, or the menu's.
    pub fn keys(&self) -> String {
        if self.in_menu {
            return "↑↓ choose · Enter open · Esc back to the chat".into();
        }
        match self.open.get(self.name()) {
            Some(s) => s.keys(),
            None => "Esc menu".into(),
        }
    }

    /// Draws the Dashboard in `area`; returns what is clickable.
    pub fn draw(&mut self, f: &mut Frame, area: Rect) -> Drawn {
        let [menu, body] = Layout::horizontal([Constraint::Length(MENU_W), Constraint::Min(30)]).areas(area);
        let menu_hits = self.draw_menu(f, menu);
        let inner = Rect {
            x: body.x + 1,
            width: body.width.saturating_sub(2),
            ..body
        };
        let name = self.name();
        self.spots = Spots::default();
        let rows = match self.open.get_mut(name) {
            Some(s) if s.keys_under() && !s.keys().is_empty() => {
                // the keys under the content; a long list leaves them room at the bottom
                let hint = key_lines(&s.keys(), inner.width as usize);
                let room = (hint.len() as u16 + 1).min(inner.height / 3);
                let rows = s.draw(
                    f,
                    Rect {
                        height: inner.height - room,
                        ..inner
                    },
                );
                self.spots.keys = draw_keys_under(f, inner, hint);
                rows
            }
            Some(s) => s.draw(f, inner),
            None => {
                let step = SECTIONS[self.section].2;
                let text = format!(
                    "{name} comes to the terminal in step {step} of the plan. For now, use it in the web page: \
                     Dashboard → {name}."
                );
                let mut lines = vec![
                    Line::from(Span::styled(
                        name.to_string(),
                        Style::new().add_modifier(Modifier::BOLD),
                    )),
                    Line::default(),
                ];
                lines.extend(wrap(vec![Span::styled(text, DIM)], inner.width as usize, "", ""));
                f.render_widget(Paragraph::new(lines), inner);
                Vec::new()
            }
        };
        // the section's tabs, found where it drew them
        if let Some(sec) = self.open.get(name) {
            for (row, labels) in sec.tabs().iter().enumerate() {
                if let Some(rects) = find_tabs(f.buffer_mut(), inner, labels) {
                    self.spots
                        .tabs
                        .extend(rects.into_iter().enumerate().map(|(i, r)| (r, row, i)));
                }
            }
        }
        (menu_hits, rows)
    }

    fn draw_menu(&self, f: &mut Frame, area: Rect) -> Vec<(Rect, usize)> {
        let block = Block::new()
            .borders(Borders::RIGHT)
            .border_style(Style::new().fg(Color::DarkGray));
        let inner = block.inner(area);
        f.render_widget(block, area);
        let mut lines = vec![Line::from(Span::styled(
            " Dashboard",
            Style::new().add_modifier(Modifier::BOLD),
        ))];
        let mut hits = Vec::new();
        let mut group = "";
        for (i, (g, name, step)) in SECTIONS.iter().enumerate() {
            if *g != group {
                group = g;
                lines.push(Line::default());
                lines.push(Line::from(Span::styled(format!(" {}", g.to_uppercase()), DIM)));
            }
            let here = i == self.section;
            let mut style = if *step == 0 { Style::new() } else { DIM };
            if here {
                style = style.bg(Color::Indexed(236)).add_modifier(Modifier::BOLD);
                if self.in_menu {
                    style = style.fg(ACCENT);
                }
            }
            let mark = if here { "▸" } else { " " };
            let text = format!("{mark} {name}");
            let pad = (inner.width as usize).saturating_sub(unicode_width::UnicodeWidthStr::width(text.as_str()));
            hits.push((
                Rect {
                    x: inner.x,
                    y: inner.y + lines.len() as u16,
                    width: inner.width,
                    height: 1,
                },
                i,
            ));
            lines.push(Line::from(Span::styled(format!("{text}{}", " ".repeat(pad)), style)));
        }
        if SECTIONS.iter().any(|(.., step)| *step > 0) {
            lines.push(Line::default());
            lines.push(Line::from(Span::styled(" grey: web page", DIM)));
        }
        f.render_widget(Paragraph::new(lines), inner);
        hits.retain(|(r, _)| r.y < inner.y + inner.height);
        hits
    }
}

/// A section's title line and, under it, its last message (green or red).
pub fn head_lines(title: &str, extra: &str, msg: &Option<(bool, String)>, width: usize) -> Vec<Line<'static>> {
    let mut head = vec![Line::from(vec![
        Span::styled(title.to_string(), Style::new().add_modifier(Modifier::BOLD)),
        Span::styled(extra.to_string(), DIM),
    ])];
    if let Some((ok, m)) = msg {
        let style = if *ok {
            Style::new().fg(Color::Green)
        } else {
            Style::new().fg(Color::Red)
        };
        head.extend(wrap(
            vec![Span::styled(m.clone(), style)],
            width,
            if *ok { "✓ " } else { "✗ " },
            "  ",
        ));
    }
    head.push(Line::default());
    head
}

/// Draws the head lines at the top of `area`; returns what is left under them.
/// The keys one line below the last row the section drew in `area`.
/// A key line cut into lines of at most `width`, only between two keys (" · "), so
/// a click on a label lands on its key.
pub fn key_lines(text: &str, width: usize) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    for seg in text.split(" · ") {
        match out.last_mut() {
            Some(l) if l.width() + 3 + seg.width() <= width => {
                l.push_str(" · ");
                l.push_str(seg);
            }
            _ => out.push(seg.to_string()),
        }
    }
    out
}

/// Where each label of a row of tabs is on the screen: the first screen line holding
/// them all, in order, inside `area`.
pub fn find_tabs(buf: &ratatui::buffer::Buffer, area: Rect, labels: &[String]) -> Option<Vec<Rect>> {
    for y in area.y..area.y + area.height {
        // the line's text, and the screen column of each of its characters
        let (mut text, mut cols) = (String::new(), Vec::new());
        for x in area.x..area.x + area.width {
            let sym = buf[(x, y)].symbol();
            for _ in sym.chars() {
                cols.push(x);
            }
            text.push_str(sym);
        }
        let chars: Vec<char> = text.chars().collect();
        let mut from = 0;
        let mut found = Vec::new();
        for label in labels {
            let want: Vec<char> = label.chars().collect();
            let at = (from..chars.len().saturating_sub(want.len()) + 1).find(|&i| chars[i..].starts_with(&want));
            match at {
                Some(i) if !want.is_empty() => {
                    let x0 = cols[i];
                    let x1 = cols[i + want.len() - 1];
                    found.push(Rect {
                        x: x0,
                        y,
                        width: x1 - x0 + 1,
                        height: 1,
                    });
                    from = i + want.len();
                }
                _ => break,
            }
        }
        if found.len() == labels.len() && !labels.is_empty() {
            return Some(found);
        }
    }
    None
}

fn draw_keys_under(f: &mut Frame, area: Rect, hint: Vec<String>) -> Vec<(u16, u16, String)> {
    let buf = f.buffer_mut();
    let used = (area.y..area.y + area.height)
        .rev()
        .find(|&y| (area.x..area.x + area.width).any(|x| buf[(x, y)].symbol() != " "));
    let top = used.map_or(area.y, |y| y + 2);
    let bottom = area.y + area.height;
    if top >= bottom {
        return Vec::new();
    }
    let h = (hint.len() as u16).min(bottom - top);
    let spots = hint
        .iter()
        .take(h as usize)
        .enumerate()
        .map(|(i, t)| (area.x, top + i as u16, t.clone()))
        .collect();
    let lines: Vec<Line> = hint
        .into_iter()
        .map(|t| Line::from(Span::styled(t, Style::new().fg(ACCENT))))
        .collect();
    f.render_widget(
        Paragraph::new(lines),
        Rect {
            y: top,
            height: h,
            ..area
        },
    );
    spots
}

pub fn draw_head(f: &mut Frame, area: Rect, head: Vec<Line<'static>>) -> Rect {
    let h = (head.len() as u16).min(area.height);
    f.render_widget(Paragraph::new(head), Rect { height: h, ..area });
    Rect {
        y: area.y + h,
        height: area.height.saturating_sub(h),
        ..area
    }
}

#[cfg(test)]
mod click_tests {
    use super::*;
    use ratatui::buffer::Buffer;

    #[test]
    fn key_lines_break_only_between_keys() {
        let l = key_lines("n new file · N new folder · x delete", 26);
        assert_eq!(l, vec!["n new file · N new folder", "x delete"]);
        assert_eq!(key_lines("", 10), vec![""]);
    }

    #[test]
    fn tabs_are_found_where_they_are_drawn() {
        let area = Rect::new(0, 0, 40, 3);
        let mut buf = Buffer::empty(area);
        buf.set_string(0, 0, "a 7 days note", Style::new());
        buf.set_string(0, 1, "Period  7 days   30 days  · ✓ ok", Style::new());
        let labels = vec![" 7 days ".to_string(), " 30 days ".to_string()];
        let r = find_tabs(&buf, area, &labels).unwrap();
        // the line holding both, in order: not the first line's lone " 7 days "
        assert_eq!((r[0].x, r[0].y, r[0].width), (7, 1, 8));
        assert_eq!((r[1].x, r[1].y, r[1].width), (16, 1, 9));
        assert!(find_tabs(&buf, area, &[" 90 days ".to_string()]).is_none());
    }
}
