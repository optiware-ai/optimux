//! What happens on a key, a server message or an answer from the HTTP API. The
//! screen (`view.rs`) only draws what is here.

use std::collections::HashSet;
use std::time::{Duration, Instant};

use crossterm::event::{KeyCode, KeyEvent, KeyEventKind, KeyModifiers, MouseButton, MouseEvent, MouseEventKind};
use ratatui::layout::{Position, Rect};
use serde_json::{Value, json};
use tokio::sync::mpsc::UnboundedSender;

use super::editor::Editor;
use super::link::{Cred, Event, RECONNECT, REOPEN};
use super::state::{Answer, Ask, Entry, Kind, State, answer, kind_of};
use crate::api::{Api, ChatInfo};

/// How often the chat list asks the server for the chats and their state.
const LIST_EVERY: Duration = Duration::from_secs(3);
/// Quitting with "leave it running" waits this long at most for the server's yes.
const LEAVE_WAIT: Duration = Duration::from_secs(3);

/// A chat of the list on the left.
#[derive(Debug, Clone, PartialEq)]
pub struct ChatRow {
    pub id: String,
    pub title: String,
    /// "working" or "waiting" while a request runs in it.
    pub state: Option<String>,
    /// No window is on its request (left running, or its line dropped).
    pub away: bool,
}

impl From<ChatInfo> for ChatRow {
    fn from(c: ChatInfo) -> ChatRow {
        let title = if c.title.is_empty() {
            "(untitled)".to_string()
        } else {
            c.title
        };
        ChatRow {
            id: c.id,
            title,
            state: c.state,
            away: c.away,
        }
    }
}

/// What the list's new state means: the chats whose request ended while this window was
/// elsewhere (marked ✓ until opened), and whether one started waiting for an answer
/// (ring the bell). The open chat is left out: it shows all that itself.
pub fn marks(before: &[ChatRow], now: &[ChatRow], open: Option<&str>) -> (Vec<String>, bool) {
    let mut finished = Vec::new();
    let mut ring = false;
    for row in now {
        if Some(row.id.as_str()) == open {
            continue;
        }
        let was = before.iter().find(|b| b.id == row.id).and_then(|b| b.state.as_deref());
        if was.is_some() && row.state.is_none() {
            finished.push(row.id.clone());
        }
        if row.state.as_deref() == Some("waiting") && was != Some("waiting") {
            ring = true;
        }
    }
    (finished, ring)
}

/// What to open on the fresh line after leaving a running request.
#[derive(Debug, Clone, PartialEq)]
pub enum After {
    Open(String),
    New,
}

/// Keys that answer a question are ignored this long after it appears: a word being
/// typed when the card pops up must not answer it (PROTOCOL.md, 4: never by mistake).
pub const ANSWER_GUARD: Duration = Duration::from_millis(700);

pub enum Modal {
    /// "Why not?" after n: the reason goes to the model.
    Reason(Editor),
    Picker(Picker),
    ConfirmQuit,
    Help,
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum PickKind {
    Chat,
    Project,
}

pub struct Picker {
    pub kind: PickKind,
    /// (id, label, detail). `None` while loading.
    pub items: Option<Vec<(String, String, String)>>,
    pub filter: String,
    pub selected: usize,
    pub error: Option<String>,
}

impl Picker {
    pub fn visible(&self) -> Vec<&(String, String, String)> {
        let f = self.filter.to_lowercase();
        self.items
            .iter()
            .flatten()
            .filter(|(_, label, detail)| f.is_empty() || format!("{label} {detail}").to_lowercase().contains(&f))
            .collect()
    }
}

/// What a click on a part of the screen does. The screen records these while it draws
/// (`App::hits`); a click becomes the key it stands for, so a click obeys every rule a
/// key does (the answer guard included).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Click {
    Key(KeyCode, KeyModifiers),
    /// A row of a picker: choose it.
    Pick(usize),
    /// Back to the end of the chat.
    End,
    /// Inside a card: nothing (so the click doesn't close it).
    Nothing,
    /// A chat of the list on the left (its index).
    Chat(usize),
    /// Dashboard: a section of the menu.
    Section(usize),
    /// Dashboard: a row of the section's table (its index among the visible rows).
    DashRow(usize),
    /// A tab of the Dashboard's section: its row of tabs, the tab.
    DashTab(usize, usize),
}

/// Results of the HTTP calls, which run beside the screen.
pub enum Bg {
    Chats(anyhow::Result<Vec<(String, String, String)>>),
    Projects(anyhow::Result<Vec<(String, String, String)>>),
    Switched(anyhow::Result<String>),
    /// The active project, asked once at the start (an empty project sends no name).
    Active(String),
    /// The chat list's refresh.
    List(anyhow::Result<Vec<ChatInfo>>),
    /// A Dashboard call's answer.
    Dash(crate::dash::Answer),
}

pub struct App {
    pub state: State,
    pub input: Editor,
    /// Lines scrolled up from the bottom (0: following the chat).
    pub scroll: usize,
    pub show_details: bool,
    pub modal: Option<Modal>,
    pub connected: bool,
    /// Why the line is down, when it is.
    pub line_note: Option<String>,
    /// A short message under the chat, cleared after a few seconds.
    pub flash: Option<(String, Instant)>,
    /// When the open question appeared (for ANSWER_GUARD) and how far its card is scrolled.
    pub ask_since: Option<Instant>,
    pub ask_scroll: usize,
    pub spinner: usize,
    /// Clickable parts of the screen, from the last draw (later ones are on top).
    pub hits: Vec<(Rect, Click)>,
    pub quit: bool,
    /// The sign-in was refused (close code): reported after the screen closes.
    pub fatal: Option<u16>,
    out: UnboundedSender<Value>,
    bg: UnboundedSender<Bg>,
    api: Api,
    cred: Cred,
    seen_ask: bool,
    /// The project this window is switching to (its own switch, not another window's).
    switching: Option<String>,
    /// The chat list (left): the active project's chats and their state.
    pub chats: Vec<ChatRow>,
    pub sidebar: bool,
    /// Chats whose request ended while this window was elsewhere: ✓ until opened.
    pub done: HashSet<String>,
    /// Ring the terminal's bell after the next draw.
    pub ring: bool,
    /// After leaving a running request: what to open on the fresh line.
    after: Option<After>,
    /// Quitting with "leave it running": since when it waits for the server's yes.
    leaving: Option<Instant>,
    listed: Option<Instant>,
    listing: bool,
    /// The Dashboard (Ctrl+G), in place of the chat while open.
    pub dash: Option<crate::dash::Dash>,
}

impl App {
    pub fn new(out: UnboundedSender<Value>, bg: UnboundedSender<Bg>, api: Api, cred: Cred) -> App {
        {
            let (api, cred, bg) = (api.clone(), cred.clone(), bg.clone());
            tokio::spawn(async move {
                if let Ok(p) = api.active_project(&cred.auth()).await {
                    let _ = bg.send(Bg::Active(p));
                }
            });
        }
        App {
            state: State::default(),
            input: Editor::default(),
            scroll: 0,
            show_details: false,
            modal: None,
            connected: false,
            line_note: Some("Connecting…".into()),
            flash: None,
            ask_since: None,
            ask_scroll: 0,
            spinner: 0,
            hits: Vec::new(),
            quit: false,
            fatal: None,
            out,
            bg,
            api,
            cred,
            seen_ask: false,
            switching: None,
            chats: Vec::new(),
            sidebar: true,
            done: HashSet::new(),
            ring: false,
            after: None,
            leaving: None,
            listed: None,
            listing: false,
            dash: None,
        }
    }

    /// Ctrl+G: the Dashboard opens (the chat keeps running behind it) or closes.
    /// What to open once the line is up, in place of the latest chat (a new pane: a
    /// new chat; a restored pane: its chat).
    pub fn begin(&mut self, after: After) {
        self.after = Some(after);
    }

    /// The chat shown (for the saved layout), and the Dashboard section open, if any.
    pub fn chat_id(&self) -> Option<String> {
        self.state.chat_id.clone()
    }

    pub fn section(&self) -> Option<&'static str> {
        self.dash.as_ref().map(|d| d.name())
    }

    /// Quits, leaving a running request running on the server (as Ctrl+D).
    pub fn quit_leaving(&mut self) {
        self.leave_and_quit();
    }

    pub fn toggle_dash(&mut self) {
        if self.dash.take().is_none() {
            let ctx = crate::dash::Ctx {
                api: self.api.clone(),
                cred: self.cred.clone(),
                bg: self.bg.clone(),
                section: "",
            };
            self.dash = Some(crate::dash::Dash::new(ctx));
        }
    }

    /// The Dashboard at one section (from a question card: the question waits).
    fn open_dash_at(&mut self, name: &str) {
        if self.dash.is_none() {
            self.toggle_dash();
        }
        if let Some(d) = &mut self.dash {
            d.open_named(name);
        }
    }

    pub fn say(&mut self, text: impl Into<String>) {
        self.flash = Some((text.into(), Instant::now()));
    }

    fn send(&mut self, msg: Value) -> bool {
        if !self.connected {
            self.say("Not connected: wait for the line to come back.");
            return false;
        }
        self.out.send(msg).is_ok()
    }

    /// A job (open, new chat...): only when nothing runs, as the server wants.
    fn job(&mut self, msg: Value) {
        if self.state.busy {
            self.say("Still working: Esc stops it first.");
            return;
        }
        if self.connected {
            let msg = self.state.job(msg);
            self.send(msg);
        } else {
            self.say("Not connected: wait for the line to come back.");
        }
    }

    // ------------------------------------------------------------------ the line

    pub fn on_link(&mut self, ev: Event) {
        match ev {
            Event::Message(msg) => {
                if msg.get("type").and_then(Value::as_str) == Some("active_project_changed") {
                    let project = msg.get("project").and_then(Value::as_str).unwrap_or("").to_string();
                    return self.follow_project(project);
                }
                let kind = msg.get("type").and_then(Value::as_str).unwrap_or("").to_string();
                if kind == "detached" && self.leaving.is_some() {
                    self.quit = true; // the server keeps it: now the window can go
                    return;
                }
                self.state.apply(&msg);
                if kind == "auth_ok" {
                    self.connected = true;
                    self.line_note = None;
                    self.state.busy = false;
                    match self.after.take() {
                        // the request we left keeps running; open what was asked for
                        Some(after) => self.run_after(after),
                        // the chat we were in (and its request back if it still runs), or the latest
                        None => {
                            let id = self.state.chat_id.clone().unwrap_or_else(|| "latest".into());
                            self.job(json!({"type": "open_session", "id": id}));
                        }
                    }
                }
                if matches!(
                    kind.as_str(),
                    "session_loaded" | "session_started" | "done" | "chat_reset"
                ) {
                    if let Some(id) = &self.state.chat_id {
                        self.done.remove(id);
                    }
                    self.listed = None; // ask for the list again soon
                }
                self.track_ask();
            }
            Event::Down(why, secs) => {
                self.connected = false;
                // A question can't be answered on a dead line; it comes back with the request
                self.state.ask = None;
                self.track_ask();
                self.line_note = Some(format!("Line lost ({why}). Trying again in {secs} s…"));
            }
            Event::TakenOver => {
                self.connected = false;
                self.state.ask = None;
                self.state.busy = false;
                self.track_ask();
                self.line_note = Some("This chat's request went to another window (Ctrl+L takes it back).".into());
            }
            Event::Refused(code) => {
                self.fatal = Some(code);
                self.quit = true;
            }
        }
    }

    fn track_ask(&mut self) {
        match (&self.state.ask, self.seen_ask) {
            (Some(_), false) => {
                self.ring = true;
                self.seen_ask = true;
                self.ask_since = Some(Instant::now());
                self.ask_scroll = 0;
                self.scroll = 0;
            }
            (None, true) => {
                self.seen_ask = false;
                self.ask_since = None;
                if matches!(self.modal, Some(Modal::Reason(_))) {
                    self.modal = None;
                }
            }
            _ => {}
        }
    }

    pub fn on_bg(&mut self, bg: Bg) {
        match bg {
            Bg::Dash(a) => {
                if let Some(d) = &mut self.dash {
                    d.on_answer(a);
                }
            }
            Bg::Chats(r) | Bg::Projects(r) => {
                if let Some(Modal::Picker(p)) = &mut self.modal {
                    match r {
                        Ok(items) => {
                            // start on the open chat, or the active project
                            let current = match p.kind {
                                PickKind::Chat => self.state.chat_id.as_ref(),
                                PickKind::Project => self.state.project.as_ref(),
                            };
                            p.selected = items.iter().position(|(id, ..)| Some(id) == current).unwrap_or(0);
                            p.items = Some(items);
                        }
                        Err(e) => p.error = Some(format!("{e:#}")),
                    }
                }
            }
            Bg::Switched(Ok(project)) => {
                self.switching = None;
                // the server's announcement may have come first and opened the chat already
                if self.state.project.as_deref() != Some(project.as_str()) {
                    self.open_project(project.clone());
                }
                self.say(format!("Now in {}.", project_name(&project)));
            }
            Bg::Switched(Err(e)) => {
                self.switching = None;
                self.say(format!("{e:#}"));
            }
            Bg::Active(p) => {
                if self.state.project.is_none() {
                    self.state.project = Some(p);
                }
            }
            Bg::List(r) => {
                self.listing = false;
                if let Ok(rows) = r {
                    let rows: Vec<ChatRow> = rows.into_iter().map(ChatRow::from).collect();
                    let (finished, ring) = marks(&self.chats, &rows, self.state.chat_id.as_deref());
                    self.done.extend(finished);
                    self.done.retain(|id| rows.iter().any(|r| &r.id == id));
                    self.ring |= ring;
                    self.chats = rows;
                }
            }
        }
    }

    // ------------------------------------------------------------------ several chats

    /// Opens another chat (or a new one). While a request runs here, it is left running
    /// on the server (detach: 30 minutes by default) and this window opens the other
    /// chat on a fresh line; the list shows the one left behind, and opening it again
    /// gets it back.
    fn leave(&mut self, after: After) {
        if self.state.busy && self.connected {
            self.mark_left();
            let _ = self.out.send(json!({"type": "detach"}));
            let _ = self.out.send(json!({"type": REOPEN}));
            self.after = Some(after);
            self.connected = false;
            self.state.busy = false;
            self.state.ask = None;
            self.track_ask();
            self.line_note = Some("Leaving the request running…".into());
            let minutes = (self.state.detach_wait / 60).max(1);
            self.say(format!(
                "It keeps running for up to {minutes} minutes: it is in the chat list."
            ));
        } else {
            self.run_after(after);
        }
    }

    /// The chat being left with a request running shows ⠋ in the list at once (it is
    /// added if it is new, not listed yet): the list's next refresh then gives it a ✓
    /// when the request has ended, even if that took less than one refresh.
    fn mark_left(&mut self) {
        let Some(id) = self.state.chat_id.clone() else { return };
        match self.chats.iter_mut().find(|c| c.id == id) {
            Some(c) => {
                c.state.get_or_insert_with(|| "working".into());
                c.away = true;
            }
            None => {
                let title = if self.state.chat_title.is_empty() {
                    "New chat".to_string()
                } else {
                    self.state.chat_title.clone()
                };
                self.chats.insert(
                    0,
                    ChatRow {
                        id,
                        title,
                        state: Some("working".into()),
                        away: true,
                    },
                );
            }
        }
        self.listed = None;
    }

    fn run_after(&mut self, after: After) {
        match after {
            After::Open(id) => self.job(json!({"type": "open_session", "id": id})),
            After::New => self.job(json!({"type": "new_chat"})),
        }
    }

    /// A Dashboard section asked to open a chat (History, Overview): the Dashboard closes
    /// and the chat opens here, as a click in the chat list does.
    fn after_dash(&mut self) {
        if let Some(text) = self.dash.as_mut().and_then(|d| d.chat_text.take()) {
            self.dash = None;
            // after what is already typed, on a line of its own
            if !self.input.text().is_empty() && !self.input.text().ends_with('\n') {
                self.input.insert('\n');
            }
            self.input.insert_str(&text);
            self.say("Added to your message: write what you want, then Enter.");
            return;
        }
        if let Some(id) = self.dash.as_mut().and_then(|d| d.open_chat.take()) {
            self.dash = None;
            // "": back to the chat window as it is (Getting started → Send your first request)
            if !id.is_empty() {
                self.go_to(id);
            }
        }
    }

    fn go_to(&mut self, id: String) {
        if self.state.chat_id.as_deref() == Some(id.as_str()) {
            return;
        }
        self.leave(After::Open(id));
    }

    /// Alt+↑ / Alt+↓: the chat above or below the open one in the list.
    fn next_chat(&mut self, down: bool) {
        let here = self
            .chats
            .iter()
            .position(|c| Some(&c.id) == self.state.chat_id.as_ref());
        let target = match (here, down) {
            (Some(i), true) => self.chats.get(i + 1),
            (Some(i), false) => i.checked_sub(1).and_then(|i| self.chats.get(i)),
            (None, _) => self.chats.first(),
        };
        if let Some(c) = target.map(|c| c.id.clone()) {
            self.go_to(c);
        }
    }

    /// Ctrl+D: quit, leaving a running request running (detach) for the server's wait.
    fn leave_and_quit(&mut self) {
        if self.state.busy && self.connected {
            let _ = self.out.send(json!({"type": "detach"}));
            self.leaving = Some(Instant::now());
            self.line_note = Some("Leaving the request running…".into());
        } else {
            self.quit = true;
        }
    }

    fn refresh_list(&mut self) {
        if self.listing || self.listed.is_some_and(|t| t.elapsed() < LIST_EVERY) {
            return;
        }
        self.listing = true;
        self.listed = Some(Instant::now());
        let (api, cred, bg) = (self.api.clone(), self.cred.clone(), self.bg.clone());
        tokio::spawn(async move {
            let _ = bg.send(Bg::List(api.chats(&cred.auth()).await));
        });
    }

    /// `active_project_changed`: the one active project changed, maybe in another window.
    /// Follow it, like the web page does, unless it is ours already.
    fn follow_project(&mut self, project: String) {
        if self.state.project.as_deref() == Some(project.as_str()) {
            return;
        }
        let ours = self.switching.as_deref() == Some(project.as_str());
        if self.state.busy && !ours {
            // a running request stays in its chat; the next one goes to the new project
            self.state.project = Some(project.clone());
            self.say(format!(
                "Another window switched to {}. This request ends here first.",
                project_name(&project)
            ));
            return;
        }
        self.open_project(project.clone());
        if !ours {
            self.say(format!("Another window switched to {}.", project_name(&project)));
        }
    }

    fn open_project(&mut self, project: String) {
        self.state.project = Some(project);
        self.chats.clear();
        self.listed = None;
        // a request running here stays in its chat, left running
        self.leave(After::Open("latest".into()));
        self.state.chat_id = None;
    }

    // ------------------------------------------------------------------ mouse

    pub fn on_mouse(&mut self, m: MouseEvent) {
        match m.kind {
            MouseEventKind::ScrollUp => self.wheel(KeyCode::Up),
            MouseEventKind::ScrollDown => self.wheel(KeyCode::Down),
            MouseEventKind::Down(MouseButton::Left) => {
                let at = Position::new(m.column, m.row);
                let hit = self.hits.iter().rev().find(|(r, _)| r.contains(at)).map(|(_, c)| *c);
                match hit {
                    Some(Click::Key(code, mods)) => self.on_key(KeyEvent::new(code, mods)),
                    Some(Click::Pick(i)) => {
                        if let Some(Modal::Picker(p)) = &mut self.modal {
                            p.selected = i;
                            self.on_key(KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE));
                        }
                    }
                    Some(Click::End) => self.scroll = 0,
                    Some(Click::Chat(i)) => {
                        if self.modal.is_none() && self.state.ask.is_none() {
                            if let Some(id) = self.chats.get(i).map(|c| c.id.clone()) {
                                self.go_to(id);
                            }
                        }
                    }
                    Some(Click::Section(i)) => {
                        if let Some(d) = &mut self.dash {
                            d.choose(i);
                        }
                    }
                    Some(Click::DashRow(i)) => {
                        if let Some(d) = &mut self.dash {
                            d.click_row(i);
                        }
                        self.after_dash();
                    }
                    Some(Click::DashTab(row, i)) => {
                        if let Some(d) = &mut self.dash {
                            d.click_tab(row, i);
                        }
                        self.after_dash();
                    }
                    Some(Click::Nothing) => {}
                    // a click outside a list or the keys closes it
                    None if matches!(self.modal, Some(Modal::Help | Modal::Picker(_))) => self.modal = None,
                    None => {}
                }
            }
            _ => {}
        }
    }

    /// The wheel scrolls what is in front: a list, a question card, or the chat.
    fn wheel(&mut self, code: KeyCode) {
        if self.modal.is_none() && self.dash.is_some() {
            return self.on_key(KeyEvent::new(code, KeyModifiers::NONE));
        }
        match &self.modal {
            Some(Modal::Picker(_)) => self.on_key(KeyEvent::new(code, KeyModifiers::NONE)),
            Some(_) => {}
            None if self.state.ask.is_some() => self.on_key(KeyEvent::new(code, KeyModifiers::NONE)),
            None if code == KeyCode::Up => self.scroll += 3,
            None => self.scroll = self.scroll.saturating_sub(3),
        }
    }

    // ------------------------------------------------------------------ keys

    pub fn on_paste(&mut self, text: &str) {
        if let (None, Some(d)) = (&self.modal, &mut self.dash) {
            return d.paste(text);
        }
        match &mut self.modal {
            Some(Modal::Reason(ed)) => ed.insert_str(text),
            Some(Modal::Picker(p)) => p.filter.push_str(text.lines().next().unwrap_or("")),
            Some(_) => {}
            None if self.state.ask.is_none() => self.input.insert_str(text),
            None => {}
        }
    }

    pub fn on_key(&mut self, key: KeyEvent) {
        if key.kind == KeyEventKind::Release {
            return;
        }
        let ctrl = key.modifiers.contains(KeyModifiers::CONTROL);
        if ctrl && matches!(key.code, KeyCode::Char('c') | KeyCode::Char('q')) && self.modal.is_none() {
            return self.ask_quit();
        }
        if let Some(modal) = self.modal.take() {
            return self.modal_key(modal, key);
        }
        if let Some(d) = &mut self.dash {
            if d.key(key) == crate::dash::Close::Close {
                self.dash = None;
            }
            return self.after_dash();
        }
        if self.state.ask.is_some() {
            return self.ask_key(key);
        }
        self.chat_key(key, ctrl);
    }

    fn ask_quit(&mut self) {
        if self.state.busy && self.connected {
            self.modal = Some(Modal::ConfirmQuit);
        } else {
            self.quit = true;
        }
    }

    fn chat_key(&mut self, key: KeyEvent, ctrl: bool) {
        let alt = key.modifiers.contains(KeyModifiers::ALT);
        let shift = key.modifiers.contains(KeyModifiers::SHIFT);
        match key.code {
            KeyCode::Enter if alt || shift => self.input.insert('\n'),
            KeyCode::Char('j') if ctrl => self.input.insert('\n'),
            KeyCode::Enter => self.submit(),
            KeyCode::Esc => {
                if self.state.busy {
                    if self.send(json!({"type": "stop"})) {
                        self.say("Stopping…");
                    }
                } else {
                    self.flash = None;
                }
            }
            KeyCode::Char('n') if ctrl => self.leave(After::New),
            KeyCode::Char('d') if ctrl => self.leave_and_quit(),
            KeyCode::Char('b') if ctrl => self.sidebar = !self.sidebar,
            KeyCode::Char('g') if ctrl => self.toggle_dash(),
            KeyCode::Up if alt => self.next_chat(false),
            KeyCode::Down if alt => self.next_chat(true),
            KeyCode::Char('o') if ctrl => self.open_picker(PickKind::Chat),
            KeyCode::Char('p') if ctrl => self.open_picker(PickKind::Project),
            KeyCode::Char('e') if ctrl => {
                if matches!(
                    self.state.entries.last(),
                    Some(Entry::Assistant(_)) | Some(Entry::Note(..))
                ) {
                    self.job(json!({"type": "escalate"}));
                } else {
                    self.say("Nothing to give to the Complex model yet.");
                }
            }
            KeyCode::Char('t') if ctrl => {
                self.show_details = !self.show_details;
                self.state.revision += 1;
            }
            KeyCode::Char('r') if ctrl => self.plan_action("run"),
            KeyCode::Char('x') if ctrl => self.plan_action("skip"),
            KeyCode::Char('k') if ctrl => self.plan_action("cancel"),
            KeyCode::Char('w') if ctrl => self.input.delete_word(),
            KeyCode::Char('l') if ctrl => {
                if self.connected {
                    self.say("Connected.");
                } else {
                    self.line_note = Some("Connecting…".into());
                    let _ = self.out.send(json!({"type": RECONNECT}));
                }
            }
            KeyCode::Char('u') if ctrl => {
                self.input.take();
            }
            // Ctrl+H; F1 too, for terminals whose Backspace key sends Ctrl+H
            KeyCode::Char('h') if ctrl => self.modal = Some(Modal::Help),
            KeyCode::F(1) => self.modal = Some(Modal::Help),
            KeyCode::PageUp => self.scroll += 10,
            KeyCode::PageDown => self.scroll = self.scroll.saturating_sub(10),
            KeyCode::Home if ctrl => self.scroll = usize::MAX / 2,
            KeyCode::End if ctrl => self.scroll = 0,
            KeyCode::Up => {
                if !self.input.up() {
                    self.scroll += 1;
                }
            }
            KeyCode::Down => {
                if !self.input.down() {
                    self.scroll = self.scroll.saturating_sub(1);
                }
            }
            KeyCode::Left => self.input.left(),
            KeyCode::Right => self.input.right(),
            KeyCode::Home => self.input.home(),
            KeyCode::End => self.input.end(),
            KeyCode::Backspace => self.input.backspace(),
            KeyCode::Delete => self.input.delete(),
            KeyCode::Tab => self.input.insert_str("    "),
            KeyCode::Char(c) if !ctrl => self.input.insert(c),
            _ => {}
        }
    }

    fn submit(&mut self) {
        if self.input.is_blank() {
            return;
        }
        if self.state.busy {
            return self.say("Still working: wait, or Esc to stop it.");
        }
        if !self.connected {
            return self.say("Not connected: your message stays here until the line is back.");
        }
        if self.state.read_only {
            return self.say("This chat can only be read: Ctrl+N starts a new one.");
        }
        let text = self.input.take();
        let text = text.trim_end().to_string();
        let msg = self.state.prompt(&text);
        self.scroll = 0;
        self.send(msg);
    }

    fn plan_action(&mut self, action: &str) {
        let Some((id, status)) = self.state.open_plan().map(|(i, s)| (i.to_string(), s.to_string())) else {
            return self.say("No plan is waiting.");
        };
        let action = match (action, status.as_str()) {
            ("run", "paused") => "resume",
            ("run", _) => "run",
            ("skip", "paused") => "skip",
            ("skip", _) => return self.say("Skip works on a paused plan."),
            (a, _) => a,
        };
        let plan_id: Value = id.parse::<i64>().map(Value::from).unwrap_or(Value::String(id));
        self.job(json!({"type": "plan_action", "action": action, "plan_id": plan_id}));
    }

    // ------------------------------------------------------------------ questions

    fn ask_key(&mut self, key: KeyEvent) {
        let Some(ask) = self.state.ask.clone() else { return };
        // the Dashboard while a question waits: it is still there when you come back
        if key.code == KeyCode::Char('g') && key.modifiers.contains(KeyModifiers::CONTROL) {
            return self.toggle_dash();
        }
        match key.code {
            KeyCode::Esc => {
                if self.send(json!({"type": "stop"})) {
                    self.say("Stopping…");
                }
                return;
            }
            KeyCode::Up => return self.ask_scroll = self.ask_scroll.saturating_sub(1),
            KeyCode::Down => return self.ask_scroll += 1,
            KeyCode::PageUp => return self.ask_scroll = self.ask_scroll.saturating_sub(10),
            KeyCode::PageDown => return self.ask_scroll += 10,
            _ => {}
        }
        let KeyCode::Char(c) = key.code else { return };
        if self.ask_since.is_some_and(|t| t.elapsed() < ANSWER_GUARD) {
            return; // too soon: probably the end of a word being typed
        }
        match ask {
            Ask::Limit { .. } => {
                let choice = match c {
                    'm' => "more",
                    'u' => "until_end",
                    's' => "stop",
                    _ => return,
                };
                self.send(json!({"type": "limit_response", "choice": choice}));
                self.state.ask = None;
            }
            Ask::Approval(req) => {
                let a = match (kind_of(&req), c) {
                    (Kind::Sync, 'n') => Answer::No(String::new()),
                    (Kind::Sync, _) => return self.say("Push and update with GitHub in the web page; n closes this."),
                    (Kind::Steps { .. }, 'm') => {
                        let steps = req.pointer("/steps_box/steps").and_then(Value::as_u64).unwrap_or(10);
                        Answer::MoreSteps(steps as u32)
                    }
                    (Kind::Steps { escalate: true }, 'e') => Answer::Escalate,
                    (Kind::Steps { .. }, 's') => Answer::Stop,
                    (Kind::Steps { .. }, _) => return,
                    (Kind::Command { raise: true }, 'a') => Answer::YesRaise,
                    (Kind::FileChange, 'a') => Answer::YesAllEdits,
                    (Kind::Fallback, 'm') => return self.open_dash_at("Models"),
                    (Kind::Fallback, 'r') => return self.open_dash_at("Routing"),
                    (_, 'y') => Answer::Yes,
                    (_, 'n') => return self.modal = Some(Modal::Reason(Editor::default())),
                    _ => return,
                };
                self.answer(a);
            }
        }
        self.track_ask();
    }

    fn answer(&mut self, a: Answer) {
        if self.send(answer(&a)) {
            self.state.ask = None;
            self.state.revision += 1;
        }
    }

    // ------------------------------------------------------------------ windows

    fn modal_key(&mut self, modal: Modal, key: KeyEvent) {
        match modal {
            Modal::Help => {} // any key closes it
            Modal::ConfirmQuit => match key.code {
                KeyCode::Char('y') => self.quit = true,
                KeyCode::Char('d') => self.leave_and_quit(),
                KeyCode::Char('s') => {
                    self.send(json!({"type": "stop"}));
                    self.quit = true;
                }
                _ => {}
            },
            Modal::Reason(mut ed) => match key.code {
                KeyCode::Esc => {} // back to the card
                KeyCode::Enter => {
                    self.answer(Answer::No(ed.take()));
                    self.track_ask();
                }
                KeyCode::Backspace => {
                    ed.backspace();
                    self.modal = Some(Modal::Reason(ed));
                }
                KeyCode::Left => {
                    ed.left();
                    self.modal = Some(Modal::Reason(ed));
                }
                KeyCode::Right => {
                    ed.right();
                    self.modal = Some(Modal::Reason(ed));
                }
                KeyCode::Char(c) => {
                    ed.insert(c);
                    self.modal = Some(Modal::Reason(ed));
                }
                _ => self.modal = Some(Modal::Reason(ed)),
            },
            Modal::Picker(mut p) => {
                let n = p.visible().len();
                match key.code {
                    KeyCode::Esc => return,
                    KeyCode::Up => p.selected = p.selected.saturating_sub(1),
                    KeyCode::Down => p.selected = (p.selected + 1).min(n.saturating_sub(1)),
                    KeyCode::PageUp => p.selected = p.selected.saturating_sub(10),
                    KeyCode::PageDown => p.selected = (p.selected + 10).min(n.saturating_sub(1)),
                    KeyCode::Backspace => {
                        p.filter.pop();
                        p.selected = 0;
                    }
                    KeyCode::Char(c) if !key.modifiers.contains(KeyModifiers::CONTROL) => {
                        p.filter.push(c);
                        p.selected = 0;
                    }
                    KeyCode::Enter => {
                        let chosen = p.visible().get(p.selected).map(|(id, ..)| id.clone());
                        if let Some(id) = chosen {
                            return self.choose(p.kind, id);
                        }
                    }
                    _ => {}
                }
                self.modal = Some(Modal::Picker(p));
            }
        }
    }

    fn open_picker(&mut self, kind: PickKind) {
        self.modal = Some(Modal::Picker(Picker {
            kind,
            items: None,
            filter: String::new(),
            selected: 0,
            error: None,
        }));
        let (api, cred, bg) = (self.api.clone(), self.cred.clone(), self.bg.clone());
        tokio::spawn(async move {
            let auth = cred.auth();
            let msg = match kind {
                PickKind::Chat => Bg::Chats(api.chats(&auth).await.map(|chats| {
                    chats
                        .into_iter()
                        .map(|c| {
                            let title = if c.title.is_empty() {
                                "(untitled)".to_string()
                            } else {
                                c.title
                            };
                            let turns = if c.turns == 1 {
                                "1 turn".to_string()
                            } else {
                                format!("{} turns", c.turns)
                            };
                            let detail = format!("{} · {turns} · ${:.4}", when(c.updated), c.cost);
                            (c.id, title, detail)
                        })
                        .collect()
                })),
                PickKind::Project => Bg::Projects(api.projects(&auth).await.map(|projects| {
                    let mut items = vec![(String::new(), "Workspace (no project)".to_string(), String::new())];
                    items.extend(projects.into_iter().filter(|p| !p.archived && p.exists).map(|p| {
                        let chats = if p.chats == 1 {
                            "1 chat".to_string()
                        } else {
                            format!("{} chats", p.chats)
                        };
                        let detail = format!(
                            "{}{chats} · ${:.2} this month",
                            if p.active { "active · " } else { "" },
                            p.month_cost
                        );
                        (p.project.clone(), p.project, detail)
                    }));
                    items
                })),
            };
            let _ = bg.send(msg);
        });
    }

    fn choose(&mut self, kind: PickKind, id: String) {
        match kind {
            PickKind::Chat => self.go_to(id),
            PickKind::Project => {
                let (api, cred, bg) = (self.api.clone(), self.cred.clone(), self.bg.clone());
                self.switching = Some(id.clone());
                self.say("Switching…");
                tokio::spawn(async move {
                    let r = api.switch_project(&cred.auth(), &id).await;
                    let _ = bg.send(Bg::Switched(r));
                });
            }
        }
    }

    pub fn tick(&mut self) {
        self.spinner = self.spinner.wrapping_add(1);
        if self.connected {
            self.refresh_list();
        }
        if self.leaving.is_some_and(|t| t.elapsed() > LEAVE_WAIT) {
            self.quit = true; // no yes from the server: the ordinary wait applies
        }
        if self
            .flash
            .as_ref()
            .is_some_and(|(_, t)| t.elapsed() > Duration::from_secs(5))
        {
            self.flash = None;
        }
    }
}

fn project_name(p: &str) -> String {
    if p.is_empty() {
        "the Workspace".to_string()
    } else {
        p.to_string()
    }
}

/// "3 h ago" from a Unix time.
fn when(ts: f64) -> String {
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0);
    let s = (now - ts).max(0.0) as u64;
    match s {
        0..=59 => "just now".into(),
        60..=3599 => format!("{} min ago", s / 60),
        3600..=86399 => format!("{} h ago", s / 3600),
        _ => format!("{} d ago", s / 86400),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn row(id: &str, state: Option<&str>) -> ChatRow {
        ChatRow {
            id: id.into(),
            title: id.into(),
            state: state.map(str::to_string),
            away: false,
        }
    }

    #[test]
    fn a_request_that_ends_elsewhere_gets_a_check_mark() {
        let before = [row("a", Some("working")), row("b", Some("working")), row("c", None)];
        let now = [row("a", None), row("b", Some("working")), row("c", None)];
        assert_eq!(marks(&before, &now, Some("x")), (vec!["a".to_string()], false));
    }

    #[test]
    fn the_open_chat_gets_no_mark_and_no_bell() {
        let before = [row("a", Some("working"))];
        assert_eq!(marks(&before, &[row("a", None)], Some("a")), (vec![], false));
        assert_eq!(marks(&before, &[row("a", Some("waiting"))], Some("a")), (vec![], false));
    }

    #[test]
    fn a_request_left_running_that_ends_before_the_next_refresh_still_gets_its_mark() {
        // marked ⠋ by mark_left(), and the server already says it ended
        let left = [row("new", Some("working"))];
        let now = [row("new", None), row("other", None)];
        assert_eq!(marks(&left, &now, Some("other")), (vec!["new".to_string()], false));
    }

    #[test]
    fn a_question_elsewhere_rings_once() {
        let working = [row("a", Some("working"))];
        let waiting = [row("a", Some("waiting"))];
        assert_eq!(marks(&working, &waiting, None), (vec![], true));
        assert_eq!(
            marks(&waiting, &waiting, None),
            (vec![], false),
            "not again while it waits"
        );
        assert_eq!(
            marks(&[], &waiting, None),
            (vec![], true),
            "a chat seen waiting the first time"
        );
    }
}
