//! Panes and tabs, as in Herdr: the screen holds tabs (at the bottom right), each tab
//! is split into panes, and a pane shows a chat (whose Dashboard opens with Ctrl+G) or
//! a shell. Ctrl+b, then a key, works them (the same keys as Herdr); a border between
//! panes can be dragged with the mouse. The layout is
//! saved (layout.json, next to the sign-in) and comes back at the next start; shells
//! start again in their folder.

pub mod help;
pub mod shell;

use std::collections::HashMap;
use std::io::{IsTerminal, Write, stdout};
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use anyhow::{Result, bail};
use crossterm::event::{
    DisableBracketedPaste, DisableMouseCapture, EnableBracketedPaste, EnableMouseCapture, Event as TermEvent,
    EventStream, KeyCode, KeyEvent, KeyEventKind, KeyModifiers, MouseButton, MouseEvent, MouseEventKind,
};
use crossterm::execute;
use crossterm::terminal::{EnterAlternateScreen, LeaveAlternateScreen, disable_raw_mode, enable_raw_mode};
use futures_util::StreamExt;
use ratatui::Frame;
use ratatui::Terminal;
use ratatui::backend::CrosstermBackend;
use ratatui::layout::{Constraint, Layout, Position, Rect};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, BorderType, Clear, Paragraph};
use serde_json::{Value, json};
use tokio::sync::mpsc::{self, UnboundedSender};
use unicode_width::UnicodeWidthStr;

use crate::api::Api;
use crate::chat::app::{After, App, Bg};
use crate::chat::link::{self, Cred, Event};
use crate::chat::view;
use crate::server::Server;
use shell::Shell;

const ACCENT: Color = Color::LightCyan;
const DIM: Style = Style::new().fg(Color::DarkGray);
const LAYOUT_FILE: &str = "layout.json";

/// How the space of a tab is cut: one pane, or two parts side by side (right) or one
/// above the other.
#[derive(Clone, Debug, PartialEq)]
pub enum Node {
    Leaf(u64),
    Split {
        right: bool,
        ratio: f32,
        a: Box<Node>,
        b: Box<Node>,
    },
}

impl Node {
    pub fn leaves(&self) -> Vec<u64> {
        match self {
            Node::Leaf(id) => vec![*id],
            Node::Split { a, b, .. } => [a.leaves(), b.leaves()].concat(),
        }
    }

    /// Each pane's place in `area` (a split leaves one column or line between them
    /// to the borders the panes draw).
    pub fn rects(&self, area: Rect, out: &mut Vec<(u64, Rect)>) {
        match self {
            Node::Leaf(id) => out.push((*id, area)),
            Node::Split { right, ratio, a, b } => {
                let (ra, rb) = cut(area, *right, *ratio);
                a.rects(ra, out);
                b.rects(rb, out);
            }
        }
    }

    /// Splits the pane `id`: it keeps the first part, `new` gets the second.
    pub fn split(&mut self, id: u64, new: u64, right: bool) -> bool {
        match self {
            Node::Leaf(x) if *x == id => {
                *self = Node::Split {
                    right,
                    ratio: 0.5,
                    a: Box::new(Node::Leaf(id)),
                    b: Box::new(Node::Leaf(new)),
                };
                true
            }
            Node::Leaf(_) => false,
            Node::Split { a, b, .. } => a.split(id, new, right) || b.split(id, new, right),
        }
    }

    /// The tree without the pane `id` (its neighbour takes its place); None when it
    /// was the only one.
    pub fn without(self, id: u64) -> Option<Node> {
        match self {
            Node::Leaf(x) if x == id => None,
            Node::Leaf(_) => Some(self),
            Node::Split { right, ratio, a, b } => match (a.without(id), b.without(id)) {
                (Some(a), Some(b)) => Some(Node::Split {
                    right,
                    ratio,
                    a: Box::new(a),
                    b: Box::new(b),
                }),
                (Some(n), None) | (None, Some(n)) => Some(n),
                (None, None) => None,
            },
        }
    }

    /// Moves the border nearest the pane `id` that runs across `right` (a left/right
    /// border for h/l): `by` from the left or top.
    pub fn resize(&mut self, id: u64, right: bool, by: f32) -> bool {
        match self {
            Node::Leaf(_) => false,
            Node::Split { right: r, ratio, a, b } => {
                let inside = a.leaves().contains(&id) || b.leaves().contains(&id);
                if !inside {
                    return false;
                }
                // the deepest border first: the one right next to the pane
                if a.resize(id, right, by) || b.resize(id, right, by) {
                    return true;
                }
                if *r == right {
                    *ratio = (*ratio + by).clamp(0.1, 0.9);
                    return true;
                }
                false
            }
        }
    }

    /// Every border in `area`: where it can be grabbed (the two columns or lines where
    /// its panes meet), the split's path (false: a, true: b) and the split's area.
    pub fn borders(&self, area: Rect, path: &mut Vec<bool>, out: &mut Vec<(Rect, Vec<bool>, Rect, bool)>) {
        if let Node::Split { right, ratio, a, b } = self {
            let (ra, rb) = cut(area, *right, *ratio);
            let grab = if *right {
                Rect {
                    x: rb.x.saturating_sub(1),
                    width: 2,
                    ..area
                }
            } else {
                Rect {
                    y: rb.y.saturating_sub(1),
                    height: 2,
                    ..area
                }
            };
            out.push((grab, path.clone(), area, *right));
            path.push(false);
            a.borders(ra, path, out);
            path.pop();
            path.push(true);
            b.borders(rb, path, out);
            path.pop();
        }
    }

    /// The split at `path`, to change its ratio.
    pub fn at(&mut self, path: &[bool]) -> Option<&mut Node> {
        match (path.split_first(), self) {
            (None, n) => Some(n),
            (Some((first, rest)), Node::Split { a, b, .. }) => {
                if *first {
                    b.at(rest)
                } else {
                    a.at(rest)
                }
            }
            _ => None,
        }
    }

    fn to_json(&self, pane: &dyn Fn(u64) -> Value) -> Value {
        match self {
            Node::Leaf(id) => pane(*id),
            Node::Split { right, ratio, a, b } => json!({
                "split": if *right { "right" } else { "down" }, "ratio": ratio,
                "a": a.to_json(pane), "b": b.to_json(pane),
            }),
        }
    }
}

/// `area` cut in two at `ratio`, side by side or one above the other.
fn cut(area: Rect, right: bool, ratio: f32) -> (Rect, Rect) {
    if right {
        let w = ((area.width as f32 * ratio).round() as u16).clamp(1, area.width.saturating_sub(1).max(1));
        (
            Rect { width: w, ..area },
            Rect {
                x: area.x + w,
                width: area.width - w,
                ..area
            },
        )
    } else {
        let h = ((area.height as f32 * ratio).round() as u16).clamp(1, area.height.saturating_sub(1).max(1));
        (
            Rect { height: h, ..area },
            Rect {
                y: area.y + h,
                height: area.height - h,
                ..area
            },
        )
    }
}

/// The pane next to `from` in a direction (h j k l): the nearest one on that side
/// that faces it.
pub fn neighbour(rects: &[(u64, Rect)], from: u64, dir: char) -> Option<u64> {
    let (_, f) = *rects.iter().find(|(id, _)| *id == from)?;
    let (fx0, fx1, fy0, fy1) = (f.x as i32, (f.x + f.width) as i32, f.y as i32, (f.y + f.height) as i32);
    rects
        .iter()
        .filter(|(id, _)| *id != from)
        .filter_map(|(id, r)| {
            let (x0, x1, y0, y1) = (r.x as i32, (r.x + r.width) as i32, r.y as i32, (r.y + r.height) as i32);
            let overlap_y = fy0.max(y0) < fy1.min(y1);
            let overlap_x = fx0.max(x0) < fx1.min(x1);
            let gap = match dir {
                'l' if overlap_y && x0 >= fx1 => x0 - fx1,
                'h' if overlap_y && x1 <= fx0 => fx0 - x1,
                'j' if overlap_x && y0 >= fy1 => y0 - fy1,
                'k' if overlap_x && y1 <= fy0 => fy0 - y1,
                _ => return None,
            };
            Some((gap, *id))
        })
        .min()
        .map(|(_, id)| id)
}

fn node_from(v: &Value, make: &mut dyn FnMut(&Value) -> Option<u64>) -> Option<Node> {
    if let Some(dir) = v.get("split").and_then(Value::as_str) {
        let a = node_from(v.get("a")?, make);
        let b = node_from(v.get("b")?, make);
        let ratio = v.get("ratio").and_then(Value::as_f64).unwrap_or(0.5) as f32;
        return match (a, b) {
            (Some(a), Some(b)) => Some(Node::Split {
                right: dir == "right",
                ratio: ratio.clamp(0.1, 0.9),
                a: Box::new(a),
                b: Box::new(b),
            }),
            (Some(n), None) | (None, Some(n)) => Some(n),
            _ => None,
        };
    }
    make(v).map(Node::Leaf)
}

pub struct Tab {
    pub name: String,
    pub root: Node,
    pub focus: u64,
    pub zoom: bool,
}

enum Pane {
    Chat(Box<App>),
    Shell(Box<Shell>),
    /// A new pane asks what it shows.
    Choose,
    /// A shell is starting: its folder is being asked for.
    Starting,
}

enum Msg {
    Link(u64, Event),
    Bg(u64, Bg),
    /// Where a new shell starts (the pane waiting for it).
    ShellDir(u64, PathBuf, Option<String>),
}

pub struct Workspace {
    server: Server,
    cred: Cred,
    api: Api,
    tabs: Vec<Tab>,
    active: usize,
    panes: HashMap<u64, Pane>,
    next: u64,
    tx: UnboundedSender<Msg>,
    shell_tx: UnboundedSender<u64>,
    prefix: bool,
    resizing: bool,
    renaming: Option<String>,
    flash: Option<(String, Instant)>,
    hits: Vec<(Rect, Hit)>,
    /// The borders of the tab shown, from the last draw.
    borders: Vec<(Rect, Vec<bool>, Rect, bool)>,
    dragging: Option<Drag>,
    /// The keys window (Ctrl+b ?), while open.
    help: Option<help::Help>,
    /// What a pane holds behind the one it shows (Ctrl+b o flips them): a shell
    /// behind a chat keeps running, a chat behind a shell keeps its line.
    behind: HashMap<u64, Pane>,
    /// Chat panes closed with Ctrl+b x: their quitting closes only the pane. Any other
    /// chat that quits (Ctrl+Q, Ctrl+C, Ctrl+D) quits optimux-tui, as before panes.
    closing: std::collections::HashSet<u64>,
    quitting: bool,
    pub fatal: Option<u16>,
    saved: String,
    last_save: Instant,
}

#[derive(Clone, Copy)]
enum Hit {
    Tab(usize),
    Pane(u64),
    Prefix,
    /// "Ctrl+b ? keys" in the bar: the keys window.
    Keys,
    /// A border between panes: the index in `borders`.
    Border(usize),
}

/// A border being dragged: the split's path, its area, and its direction.
struct Drag {
    path: Vec<bool>,
    area: Rect,
    right: bool,
}

/// The keys Ctrl+b opens (also the bar while it waits for one).
pub const PREFIX_KEYS: &str = "? all keys · o shell ⇄ chat · v split right · - split down · h j k l move · Tab next pane · z zoom · x close · c new tab · n/p tabs · 1-9 tab · T rename tab · r resize · b chat list · q quit all · Esc";

impl Workspace {
    fn new(
        server: Server,
        cred: Cred,
        api: Api,
        tx: UnboundedSender<Msg>,
        shell_tx: UnboundedSender<u64>,
    ) -> Workspace {
        Workspace {
            server,
            cred,
            api,
            tabs: Vec::new(),
            active: 0,
            panes: HashMap::new(),
            next: 1,
            tx,
            shell_tx,
            prefix: false,
            resizing: false,
            renaming: None,
            flash: None,
            hits: Vec::new(),
            borders: Vec::new(),
            dragging: None,
            help: None,
            behind: HashMap::new(),
            closing: std::collections::HashSet::new(),
            quitting: false,
            fatal: None,
            saved: String::new(),
            last_save: Instant::now(),
        }
    }

    fn id(&mut self) -> u64 {
        self.next += 1;
        self.next
    }

    /// A chat pane: its own line to the server and its own chat.
    fn chat(&mut self, begin: Option<After>, section: Option<&str>) -> u64 {
        let id = self.id();
        self.chat_at(id, begin, section);
        id
    }

    /// A chat in the pane `id` (its messages are addressed to that id, wherever the
    /// chat is: in front, or behind a shell).
    fn chat_at(&mut self, id: u64, begin: Option<After>, section: Option<&str>) {
        let (out, mut incoming) = link::start(self.server.clone(), self.cred.clone());
        let (bg_tx, mut bg_rx) = mpsc::unbounded_channel();
        let mut app = App::new(out, bg_tx, self.api.clone(), self.cred.clone());
        if let Some(b) = begin {
            app.begin(b);
        }
        if let Some(name) = section {
            app.toggle_dash();
            if let Some(d) = &mut app.dash {
                d.open_named(name);
            }
        }
        let tx = self.tx.clone();
        tokio::spawn(async move {
            while let Some(ev) = incoming.recv().await {
                if tx.send(Msg::Link(id, ev)).is_err() {
                    break;
                }
            }
        });
        let tx = self.tx.clone();
        tokio::spawn(async move {
            while let Some(bg) = bg_rx.recv().await {
                if tx.send(Msg::Bg(id, bg)).is_err() {
                    break;
                }
            }
        });
        self.panes.insert(id, Pane::Chat(Box::new(app)));
    }

    /// A shell for the pane `id`: in the active project's folder when it is on this
    /// computer (asked for first), else in your home folder.
    fn start_shell(&mut self, id: u64) {
        self.panes.insert(id, Pane::Starting);
        let (api, cred, tx) = (self.api.clone(), self.cred.clone(), self.tx.clone());
        tokio::spawn(async move {
            let home = std::env::var_os("HOME")
                .map(PathBuf::from)
                .unwrap_or_else(|| PathBuf::from("/"));
            let (dir, note) = match api.get_json::<Value>(&cred.auth(), "/api/settings").await {
                Ok(s) => {
                    let root = s.get("projects_root").and_then(Value::as_str).unwrap_or("");
                    let project = s.get("active_project").and_then(Value::as_str).unwrap_or("");
                    let dir = Path::new(root).join(project);
                    if !root.is_empty() && dir.is_dir() {
                        (dir, None)
                    } else if root.is_empty() {
                        (
                            home,
                            Some("No project folder known: the shell starts in your home folder.".into()),
                        )
                    } else {
                        let d = dir.display().to_string();
                        (
                            home,
                            Some(format!(
                                "{d} isn't on this computer (Optimux runs elsewhere): the shell starts in your home folder."
                            )),
                        )
                    }
                }
                Err(e) => (
                    home,
                    Some(format!(
                        "Optimux couldn't be asked for the project folder ({e:#}): the shell starts in your home folder."
                    )),
                ),
            };
            let _ = tx.send(Msg::ShellDir(id, dir, note));
        });
    }

    fn shell_in(&mut self, id: u64, dir: &Path, note: Option<String>) {
        match Shell::start(dir, note, self.shell_tx.clone(), id) {
            Ok(sh) => {
                self.panes.insert(id, Pane::Shell(Box::new(sh)));
            }
            Err(e) => {
                self.panes.insert(id, Pane::Choose);
                self.say(format!("{e:#}"));
            }
        }
    }

    fn say(&mut self, text: impl Into<String>) {
        self.flash = Some((text.into(), Instant::now()));
    }

    fn tab(&mut self) -> &mut Tab {
        &mut self.tabs[self.active]
    }

    fn focused(&self) -> Option<u64> {
        self.tabs.get(self.active).map(|t| t.focus)
    }

    /// What a pane is called on its border and in the saved layout.
    fn title(&self, id: u64) -> String {
        let front = Self::pane_title(self.panes.get(&id));
        match self.behind.get(&id) {
            Some(Pane::Chat(app)) if app.state.ask.is_some() => {
                format!("{front} · behind it: a chat, ! a question waits (Ctrl+b o)")
            }
            Some(Pane::Chat(_)) => format!("{front} · behind it: the chat (Ctrl+b o)"),
            Some(Pane::Shell(_) | Pane::Starting) => format!("{front} · behind it: the shell (Ctrl+b o)"),
            _ if matches!(self.panes.get(&id), Some(Pane::Shell(_))) => format!("{front} · Ctrl+b o: a chat"),
            _ => front,
        }
    }

    fn pane_title(pane: Option<&Pane>) -> String {
        match pane {
            Some(Pane::Chat(app)) => {
                let what = match app.section() {
                    Some(sec) => format!("Dashboard · {sec}"),
                    None if app.state.chat_title.is_empty() => "New chat".into(),
                    None => app.state.chat_title.clone(),
                };
                let flag = if app.state.ask.is_some() {
                    " · ! a question waits"
                } else if app.state.busy {
                    " · working"
                } else {
                    ""
                };
                format!("{what}{flag}")
            }
            Some(Pane::Shell(sh)) => {
                let home = std::env::var("HOME").unwrap_or_default();
                let d = sh.cwd.display().to_string();
                let d = if !home.is_empty() && d.starts_with(&home) {
                    format!("~{}", &d[home.len()..])
                } else {
                    d
                };
                format!(
                    "Shell · {d}{} · optimux-tui",
                    if sh.exited.is_some() { " · ended" } else { "" }
                )
            }
            Some(Pane::Choose) => "New pane".into(),
            Some(Pane::Starting) => "Shell · starting…".into(),
            None => String::new(),
        }
    }

    fn split(&mut self, right: bool) {
        let Some(focus) = self.focused() else { return };
        let new = self.id();
        self.panes.insert(new, Pane::Choose);
        let tab = self.tab();
        tab.zoom = false;
        if tab.root.split(focus, new, right) {
            tab.focus = new;
        }
    }

    fn new_tab(&mut self) {
        let id = self.id();
        self.panes.insert(id, Pane::Choose);
        // unnamed: the bar shows its number (Ctrl+b T names it)
        self.tabs.push(Tab {
            name: String::new(),
            root: Node::Leaf(id),
            focus: id,
            zoom: false,
        });
        self.active = self.tabs.len() - 1;
    }

    /// The pane leaves its tab (and the tab goes when it was its last pane).
    fn remove(&mut self, id: u64) {
        self.panes.remove(&id);
        // what was behind it takes its place
        if let Some(b) = self.behind.remove(&id) {
            self.panes.insert(id, b);
            return;
        }
        let Some(t) = self.tabs.iter().position(|t| t.root.leaves().contains(&id)) else {
            return;
        };
        let root = self.tabs[t].root.clone();
        match root.without(id) {
            Some(root) => {
                let tab = &mut self.tabs[t];
                if tab.focus == id {
                    tab.focus = root.leaves()[0];
                }
                tab.root = root;
                tab.zoom = false;
            }
            None => {
                self.tabs.remove(t);
                if self.active >= self.tabs.len() {
                    self.active = self.tabs.len().saturating_sub(1);
                } else if t < self.active {
                    self.active -= 1;
                }
            }
        }
    }

    /// Closes the focused pane: a chat asks first while its request runs (as Ctrl+Q).
    fn close_focused(&mut self) {
        let Some(id) = self.focused() else { return };
        match self.panes.get_mut(&id) {
            Some(Pane::Chat(app)) => {
                self.closing.insert(id);
                app.on_key(KeyEvent::new(KeyCode::Char('q'), KeyModifiers::CONTROL));
                // asked first (a request runs) and answered no: it stays a quit key
                if !app.quit && app.modal.is_none() {
                    self.closing.remove(&id);
                }
            }
            Some(Pane::Shell(sh)) => {
                sh.stop();
                self.remove(id);
            }
            _ => self.remove(id),
        }
    }

    fn rects(&self, main: Rect) -> Vec<(u64, Rect)> {
        let Some(tab) = self.tabs.get(self.active) else {
            return Vec::new();
        };
        if tab.zoom {
            return vec![(tab.focus, main)];
        }
        let mut out = Vec::new();
        tab.root.rects(main, &mut out);
        out
    }

    fn main_area(area: Rect) -> Rect {
        Rect {
            height: area.height.saturating_sub(1),
            ..area
        }
    }

    fn on_key(&mut self, k: KeyEvent, screen: Rect) {
        if k.kind == KeyEventKind::Release {
            return;
        }
        let ctrl = k.modifiers.contains(KeyModifiers::CONTROL);
        if let Some(h) = &mut self.help {
            match h.key(k) {
                help::Outcome::Stay => {}
                help::Outcome::Close => self.help = None,
                help::Outcome::Run(run) => self.run_help(run, screen),
            }
            return;
        }
        if let Some(name) = &mut self.renaming {
            match k.code {
                KeyCode::Enter => {
                    // an empty name: the tab shows only its number again
                    let n = name.trim().to_string();
                    self.renaming = None;
                    self.tab().name = n;
                }
                KeyCode::Esc => self.renaming = None,
                KeyCode::Backspace => {
                    name.pop();
                }
                KeyCode::Char(c) if !ctrl && name.chars().count() < 30 => name.push(c),
                _ => {}
            }
            return;
        }
        if self.resizing {
            let Some(focus) = self.focused() else { return };
            let (right, by) = match k.code {
                KeyCode::Char('h') | KeyCode::Left => (true, -0.05),
                KeyCode::Char('l') | KeyCode::Right => (true, 0.05),
                KeyCode::Char('k') | KeyCode::Up => (false, -0.05),
                KeyCode::Char('j') | KeyCode::Down => (false, 0.05),
                _ => {
                    self.resizing = false;
                    return;
                }
            };
            if !self.tab().root.resize(focus, right, by) {
                self.say("No border that way.");
            }
            return;
        }
        if self.prefix {
            self.prefix = false;
            return self.action(k, screen);
        }
        if ctrl && k.code == KeyCode::Char('b') {
            self.prefix = true;
            return;
        }
        self.key_to_pane(k);
    }

    /// An entry of the keys window, done: the window closes first.
    fn run_help(&mut self, run: help::Run, screen: Rect) {
        self.help = None;
        match run {
            help::Run::Prefix(k) => self.action(k, screen),
            help::Run::Pane(k) => self.key_to_pane(k),
            help::Run::Nothing => {}
        }
    }

    fn key_to_pane(&mut self, k: KeyEvent) {
        let Some(id) = self.focused() else { return };
        match self.panes.get_mut(&id) {
            Some(Pane::Chat(app)) => app.on_key(k),
            Some(Pane::Shell(sh)) => {
                if sh.exited.is_some() {
                    self.remove(id);
                } else {
                    sh.key(k);
                }
            }
            Some(Pane::Choose) => self.choose(id, k),
            _ => {}
        }
    }

    /// The new pane's question: what it shows.
    fn choose(&mut self, id: u64, k: KeyEvent) {
        match k.code {
            KeyCode::Char('c') | KeyCode::Enter => {
                let new = self.chat(Some(After::New), None);
                self.replace(id, new);
            }
            KeyCode::Char('d') => {
                let new = self.chat(Some(After::New), Some("Overview"));
                self.replace(id, new);
            }
            KeyCode::Char('s') => self.start_shell(id),
            KeyCode::Esc if self.panes.len() > 1 => self.remove(id),
            _ => {}
        }
    }

    /// The pane `old` becomes the pane `new` (same place in its tab).
    fn replace(&mut self, old: u64, new: u64) {
        self.panes.remove(&old);
        for t in &mut self.tabs {
            fn swap(n: &mut Node, old: u64, new: u64) {
                match n {
                    Node::Leaf(x) if *x == old => *x = new,
                    Node::Leaf(_) => {}
                    Node::Split { a, b, .. } => {
                        swap(a, old, new);
                        swap(b, old, new);
                    }
                }
            }
            swap(&mut t.root, old, new);
            if t.focus == old {
                t.focus = new;
            }
        }
    }

    fn action(&mut self, k: KeyEvent, screen: Rect) {
        let ctrl = k.modifiers.contains(KeyModifiers::CONTROL);
        let main = Self::main_area(screen);
        match k.code {
            // Ctrl+b twice: the pane gets Ctrl+b (in a chat it shows or hides the list)
            KeyCode::Char('b') if ctrl => self.key_to_pane(k),
            KeyCode::Char('b') => self.key_to_pane(KeyEvent::new(KeyCode::Char('b'), KeyModifiers::CONTROL)),
            KeyCode::Char('v' | '%' | '|') => self.split(true),
            KeyCode::Char('-' | '"') => self.split(false),
            KeyCode::Char(c @ ('h' | 'j' | 'k' | 'l')) => self.move_focus(c, main),
            KeyCode::Left => self.move_focus('h', main),
            KeyCode::Down => self.move_focus('j', main),
            KeyCode::Up => self.move_focus('k', main),
            KeyCode::Right => self.move_focus('l', main),
            KeyCode::Tab | KeyCode::BackTab => {
                let back = k.code == KeyCode::BackTab;
                let tab = self.tab();
                let all = tab.root.leaves();
                if let Some(i) = all.iter().position(|x| *x == tab.focus) {
                    let n = all.len();
                    tab.focus = all[if back { (i + n - 1) % n } else { (i + 1) % n }];
                }
            }
            KeyCode::Char('z') => {
                let tab = self.tab();
                tab.zoom = !tab.zoom && tab.root.leaves().len() > 1;
            }
            KeyCode::Char('x') => self.close_focused(),
            KeyCode::Char('c') => self.new_tab(),
            KeyCode::Char('n') => self.active = (self.active + 1) % self.tabs.len().max(1),
            KeyCode::Char('p') => self.active = (self.active + self.tabs.len().max(1) - 1) % self.tabs.len().max(1),
            KeyCode::Char(c @ '1'..='9') => {
                let i = c as usize - '1' as usize;
                if i < self.tabs.len() {
                    self.active = i;
                } else {
                    self.say(format!("There is no tab {}.", i + 1));
                }
            }
            KeyCode::Char('T') => self.renaming = Some(self.tabs[self.active].name.clone()),
            KeyCode::Char('r') => {
                if self.tabs[self.active].root.leaves().len() > 1 {
                    self.resizing = true;
                } else {
                    self.say("One pane only: nothing to resize.");
                }
            }
            KeyCode::Char('q') => self.quit_all(),
            KeyCode::Char('?' | 'H') => self.help = Some(help::Help::default()),
            KeyCode::Char('o') => self.flip(),
            KeyCode::Esc => {}
            _ => self.say("Ctrl+b: that key does nothing (the bar lists them)."),
        }
    }

    fn move_focus(&mut self, dir: char, main: Rect) {
        let rects = self.rects(main);
        let Some(focus) = self.focused() else { return };
        if let Some(n) = neighbour(&rects, focus, dir) {
            self.tab().focus = n;
        }
    }

    /// Ctrl+b q: every chat leaves its running request running on the server, then all
    /// close; shells end.
    fn quit_all(&mut self) {
        // saved first: the chats leave their panes as they close
        self.save();
        self.quitting = true;
        for p in self.panes.values_mut().chain(self.behind.values_mut()) {
            if let Pane::Chat(app) = p {
                app.quit_leaving();
            }
        }
    }

    fn on_mouse(&mut self, m: MouseEvent) {
        let at = Position::new(m.column, m.row);
        // a border being dragged follows the pointer until the button goes up
        if let Some(d) = &self.dragging {
            match m.kind {
                MouseEventKind::Drag(MouseButton::Left) => {
                    let (pos, start, len) = if d.right {
                        (m.column, d.area.x, d.area.width)
                    } else {
                        (m.row, d.area.y, d.area.height)
                    };
                    if len > 2 {
                        let ratio = ((pos.saturating_sub(start) as f32 + 1.0) / len as f32).clamp(0.1, 0.9);
                        let path = d.path.clone();
                        let tab = self.tab();
                        if let Some(Node::Split { ratio: r, .. }) = tab.root.at(&path) {
                            *r = ratio;
                        }
                    }
                }
                MouseEventKind::Up(_) => self.dragging = None,
                _ => {}
            }
            return;
        }
        if let Some(h) = &mut self.help {
            let inside = h.area.contains(at);
            match m.kind {
                MouseEventKind::Down(MouseButton::Left) if inside => {
                    if let Some(i) = h.hits.iter().find(|(r, _)| r.contains(at)).map(|(_, i)| *i) {
                        if let help::Outcome::Run(run) = h.click(i) {
                            let screen = Rect {
                                height: h.area.y + h.area.height + 1,
                                ..h.area
                            };
                            self.run_help(run, screen);
                        }
                    }
                    return;
                }
                MouseEventKind::ScrollDown if inside => return h.wheel(true),
                MouseEventKind::ScrollUp if inside => return h.wheel(false),
                // a click outside closes it (and does what it does)
                MouseEventKind::Down(_) => self.help = None,
                _ if inside => return,
                _ => {}
            }
        }
        let hit = self.hits.iter().rev().find(|(r, _)| r.contains(at)).map(|(_, h)| *h);
        match (m.kind, hit) {
            (MouseEventKind::Down(MouseButton::Left), Some(Hit::Tab(i))) => {
                self.active = i;
                return;
            }
            (MouseEventKind::Down(MouseButton::Left), Some(Hit::Prefix)) => {
                self.prefix = !self.prefix;
                return;
            }
            (MouseEventKind::Down(MouseButton::Left), Some(Hit::Keys)) => {
                self.prefix = false;
                self.help = Some(help::Help::default());
                return;
            }
            (MouseEventKind::Down(MouseButton::Left), Some(Hit::Border(i))) => {
                if let Some((_, path, area, right)) = self.borders.get(i) {
                    self.dragging = Some(Drag {
                        path: path.clone(),
                        area: *area,
                        right: *right,
                    });
                }
                return;
            }
            (MouseEventKind::Down(MouseButton::Left), Some(Hit::Pane(id))) => self.tab().focus = id,
            _ => {}
        }
        // the event goes to the pane under the pointer (the wheel too)
        let target = match hit {
            Some(Hit::Pane(id)) => Some(id),
            _ => None,
        };
        let Some(id) = target else { return };
        match self.panes.get_mut(&id) {
            Some(Pane::Chat(app)) => app.on_mouse(m),
            Some(Pane::Shell(sh)) => match m.kind {
                MouseEventKind::ScrollUp => sh.wheel(true),
                MouseEventKind::ScrollDown => sh.wheel(false),
                _ => {}
            },
            _ => {}
        }
    }

    fn on_paste(&mut self, text: &str) {
        if let Some(name) = &mut self.renaming {
            name.push_str(&text.replace(['\n', '\r'], " "));
            return;
        }
        let Some(id) = self.focused() else { return };
        match self.panes.get_mut(&id) {
            Some(Pane::Chat(app)) => app.on_paste(text),
            Some(Pane::Shell(sh)) => sh.paste(text),
            _ => {}
        }
    }

    fn on_msg(&mut self, msg: Msg) {
        match msg {
            Msg::Link(id, ev) => {
                if let Some(Pane::Chat(app)) = self.panes.get_mut(&id).or(self.behind.get_mut(&id)) {
                    app.on_link(ev);
                }
            }
            Msg::Bg(id, bg) => {
                if let Some(Pane::Chat(app)) = self.panes.get_mut(&id).or(self.behind.get_mut(&id)) {
                    app.on_bg(bg);
                }
            }
            Msg::ShellDir(id, dir, note) => {
                if matches!(self.panes.get(&id), Some(Pane::Starting)) {
                    self.shell_in(id, &dir, note);
                } else if matches!(self.behind.get(&id), Some(Pane::Starting)) {
                    // flipped away while it started: it starts behind
                    if let Ok(sh) = Shell::start(&dir, note, self.shell_tx.clone(), id) {
                        self.behind.insert(id, Pane::Shell(Box::new(sh)));
                    } else {
                        self.behind.remove(&id);
                    }
                }
            }
        }
    }

    fn tick(&mut self) {
        for p in self.panes.values_mut().chain(self.behind.values_mut()) {
            match p {
                Pane::Chat(app) => app.tick(),
                Pane::Shell(sh) => {
                    sh.check();
                }
                _ => {}
            }
        }
        if self
            .flash
            .as_ref()
            .is_some_and(|(_, at)| at.elapsed() > Duration::from_secs(4))
        {
            self.flash = None;
        }
        if self.last_save.elapsed() > Duration::from_secs(5) {
            self.save();
        }
    }

    /// A chat closed with Ctrl+b x leaves its pane; a chat quit with its own keys
    /// (Ctrl+Q, Ctrl+C, Ctrl+D) quits everything; a refused sign-in ends everything.
    fn reap(&mut self) {
        // a chat behind a shell that quits (a refused sign-in) just goes
        let behind_gone: Vec<u64> = self
            .behind
            .iter()
            .filter_map(|(id, p)| matches!(p, Pane::Chat(a) if a.quit).then_some(*id))
            .collect();
        for id in behind_gone {
            if let Some(Pane::Chat(app)) = self.behind.remove(&id) {
                if let Some(code) = app.fatal {
                    self.fatal = Some(code);
                }
            }
        }
        let gone: Vec<u64> = self
            .panes
            .iter()
            .filter_map(|(id, p)| match p {
                Pane::Chat(app) if app.quit => Some(*id),
                _ => None,
            })
            .collect();
        for id in gone {
            if let Some(Pane::Chat(app)) = self.panes.get(&id) {
                if let Some(code) = app.fatal {
                    self.fatal = Some(code);
                }
            }
            if self.quitting {
                self.panes.remove(&id);
            } else if self.closing.remove(&id) {
                self.remove(id);
            } else {
                // its own quit key: the whole of optimux-tui goes, the layout saved first
                // (with this chat in it)
                self.quit_all();
                self.panes.remove(&id);
            }
        }
        // a pane closed while it asked whether to quit, and the answer was no
        let asking: Vec<u64> = self.closing.iter().copied().collect();
        for id in asking {
            if let Some(Pane::Chat(app)) = self.panes.get(&id) {
                if app.modal.is_none() && !app.quit {
                    self.closing.remove(&id);
                }
            }
        }
    }

    fn done(&self) -> bool {
        self.fatal.is_some()
            || self.tabs.is_empty()
            || (self.quitting
                && !self
                    .panes
                    .values()
                    .chain(self.behind.values())
                    .any(|p| matches!(p, Pane::Chat(_))))
    }

    fn ring(&mut self) -> bool {
        let mut ring = false;
        for p in self.panes.values_mut().chain(self.behind.values_mut()) {
            if let Pane::Chat(app) = p {
                ring |= std::mem::take(&mut app.ring);
            }
        }
        ring
    }

    fn take_edit(&mut self) -> Option<(u64, crate::ui::external::Edit)> {
        self.panes.iter_mut().find_map(|(id, p)| match p {
            Pane::Chat(app) => app.dash.as_mut().and_then(|d| d.take_edit()).map(|e| (*id, e)),
            _ => None,
        })
    }

    fn edited(&mut self, id: u64, result: Result<Option<String>, String>) {
        if let Some(Pane::Chat(app)) = self.panes.get_mut(&id) {
            if let Some(d) = app.dash.as_mut() {
                d.edited(result);
            }
        }
    }

    fn draw(&mut self, f: &mut Frame) {
        let area = f.area();
        let [main, bar] = Layout::vertical([Constraint::Min(3), Constraint::Length(1)]).areas(area);
        self.hits.clear();
        let rects = self.rects(main);
        let multi = rects.len() > 1;
        let focus = self.focused();
        for (id, r) in &rects {
            let focused = Some(*id) == focus;
            // a shell alone keeps its frame: it must not look like a plain terminal
            let framed = multi
                || self.tabs[self.active].zoom
                || matches!(self.panes.get(id), Some(Pane::Shell(_) | Pane::Starting | Pane::Choose))
                || self.behind.contains_key(id);
            let inner = if framed {
                let title = self.title(*id);
                let style = if focused { Style::new().fg(ACCENT) } else { DIM };
                let mut block = Block::bordered()
                    .border_type(BorderType::Rounded)
                    .border_style(style)
                    .title(Span::styled(
                        format!(
                            " {} ",
                            crate::ui::table::fit(&title, r.width.saturating_sub(4) as usize)
                        ),
                        if focused {
                            style.add_modifier(Modifier::BOLD)
                        } else {
                            style
                        },
                    ));
                if self.tabs[self.active].zoom {
                    block = block.title_bottom(Span::styled(" zoomed: Ctrl+b z ", DIM));
                }
                let inner = block.inner(*r);
                f.render_widget(block, *r);
                inner
            } else {
                *r
            };
            self.hits.push((*r, Hit::Pane(*id)));
            match self.panes.get_mut(id) {
                Some(Pane::Chat(app)) => {
                    view::draw_in(f, app, inner);
                }
                Some(Pane::Shell(sh)) => {
                    sh.draw(f, inner, focused && self.renaming.is_none());
                    if let Some(note) = sh.note.clone() {
                        if inner.height > 2 {
                            let w = (note.width() as u16 + 2).min(inner.width);
                            let at = Rect {
                                x: inner.x + inner.width - w,
                                y: inner.y + inner.height - 1,
                                width: w,
                                height: 1,
                            };
                            f.render_widget(Clear, at);
                            f.render_widget(
                                Paragraph::new(Span::styled(format!(" {note}"), Style::new().fg(Color::Yellow))),
                                at,
                            );
                        }
                    }
                    if let Some(end) = &sh.exited {
                        let text = format!(" {end} Any key closes the pane. ");
                        let w = (text.width() as u16).min(inner.width);
                        let at = Rect {
                            x: inner.x,
                            y: inner.y + inner.height.saturating_sub(1),
                            width: w,
                            height: 1,
                        };
                        f.render_widget(
                            Paragraph::new(Span::styled(text, Style::new().fg(Color::Black).bg(Color::Yellow))),
                            at,
                        );
                    }
                }
                Some(Pane::Choose) => draw_choose(f, inner),
                Some(Pane::Starting) => {
                    f.render_widget(Paragraph::new(Span::styled("Starting the shell…", DIM)), inner);
                }
                None => {}
            }
        }
        // the borders between panes can be dragged (on top of the panes' own clicks)
        self.borders.clear();
        if multi && !self.tabs[self.active].zoom {
            let mut path = Vec::new();
            let mut out = Vec::new();
            self.tabs[self.active].root.borders(main, &mut path, &mut out);
            for (i, (grab, ..)) in out.iter().enumerate() {
                self.hits.push((*grab, Hit::Border(i)));
            }
            if let Some(d) = &self.dragging {
                // the border being moved, in cyan
                if let Some((grab, ..)) = out.iter().find(|(_, p, ..)| *p == d.path) {
                    let buf = f.buffer_mut();
                    for y in grab.y..grab.y + grab.height {
                        for x in grab.x..grab.x + grab.width {
                            if let Some(c) = buf.cell_mut((x, y)) {
                                c.set_style(Style::new().fg(ACCENT).add_modifier(Modifier::BOLD));
                            }
                        }
                    }
                }
            }
            self.borders = out;
        }
        self.draw_bar(f, bar);
        if let Some(h) = &mut self.help {
            h.draw(f, main);
        }
    }

    fn draw_bar(&mut self, f: &mut Frame, bar: Rect) {
        // the tabs, at the right
        let mut chips: Vec<(String, Style, usize)> = Vec::new();
        for (i, t) in self.tabs.iter().enumerate() {
            let waits = t.root.leaves().iter().any(|id| {
                [self.panes.get(id), self.behind.get(id)]
                    .into_iter()
                    .any(|p| matches!(p, Some(Pane::Chat(a)) if a.state.ask.is_some()))
            });
            let name = if t.name.is_empty() {
                String::new()
            } else {
                format!(" {}", crate::ui::table::fit(&t.name, 14))
            };
            let label = format!(" {}{name}{} ", i + 1, if waits && i != self.active { " !" } else { "" });
            let style = if i == self.active {
                Style::new().fg(Color::Black).bg(ACCENT)
            } else if waits {
                Style::new().fg(Color::Black).bg(Color::Yellow)
            } else {
                Style::new().fg(Color::Gray).bg(Color::Indexed(236))
            };
            chips.push((label, style, i));
        }
        let mut tabs_w: u16 = chips.iter().map(|(l, ..)| l.width() as u16 + 1).sum();
        // too many to show: only the numbers
        if tabs_w > bar.width / 2 {
            for (l, _, i) in &mut chips {
                *l = format!(" {} ", *i + 1);
            }
            tabs_w = chips.iter().map(|(l, ..)| l.width() as u16 + 1).sum();
        }
        let mut x = bar.x + bar.width.saturating_sub(tabs_w);
        for (label, style, i) in &chips {
            let w = label.width() as u16;
            if x + w > bar.x + bar.width {
                break;
            }
            let r = Rect {
                x,
                y: bar.y,
                width: w,
                height: 1,
            };
            f.render_widget(Paragraph::new(Span::styled(label.clone(), *style)), r);
            self.hits.push((r, Hit::Tab(*i)));
            x += w + 1;
        }
        // the left: what Ctrl+b does, or what is being done
        let left_w = bar.width.saturating_sub(tabs_w + 1);
        let left = Rect { width: left_w, ..bar };
        let (text, style) = if let Some(name) = &self.renaming {
            (
                format!(" Tab name: {name}█  Enter save · Esc cancel"),
                Style::new().fg(ACCENT),
            )
        } else if self.resizing {
            (
                " Resize: h j k l or the arrows move the border · any other key ends".into(),
                Style::new().fg(ACCENT),
            )
        } else if self.prefix {
            (
                format!(" Ctrl+b: {PREFIX_KEYS}"),
                Style::new().fg(Color::Black).bg(ACCENT),
            )
        } else if let Some((m, _)) = &self.flash {
            (format!(" {m}"), Style::new().fg(Color::Yellow))
        } else {
            (" Ctrl+b panes and tabs · Ctrl+b ? keys".into(), DIM)
        };
        f.render_widget(
            Paragraph::new(Span::styled(crate::ui::table::fit(&text, left_w as usize), style)),
            left,
        );
        if !self.prefix && self.renaming.is_none() && !self.resizing && self.flash.is_none() {
            // "Ctrl+b panes and tabs" opens the keys bar, "Ctrl+b ? keys" this window
            self.hits.push((
                Rect {
                    width: 22.min(left_w),
                    ..bar
                },
                Hit::Prefix,
            ));
            if left_w > 40 {
                self.hits.push((
                    Rect {
                        x: bar.x + 25,
                        width: 14,
                        ..bar
                    },
                    Hit::Keys,
                ));
            }
        }
    }

    /// A saved pane, made again in the pane `id`. Behind another one, a shell whose
    /// folder is gone is left out (it can't start in the background where it was).
    fn build_at(&mut self, id: u64, p: &Value, behind: bool) {
        if let Some(path) = p.get("shell").and_then(Value::as_str) {
            let dir = PathBuf::from(path);
            if dir.is_dir() {
                self.shell_in(id, &dir, None);
            } else if !behind {
                self.start_shell(id);
            }
            return;
        }
        if p.get("chat").is_some() {
            let begin = match p.get("chat").and_then(Value::as_str) {
                Some(cid) => After::Open(cid.to_string()),
                None => After::New,
            };
            let section = p.get("dash").and_then(Value::as_str).and_then(|s| {
                crate::dash::SECTIONS
                    .iter()
                    .find(|(_, n, _)| *n == s)
                    .map(|(_, n, _)| *n)
            });
            self.chat_at(id, Some(begin), section);
            return;
        }
        if !behind {
            self.panes.insert(id, Pane::Choose);
        }
    }

    /// Ctrl+b o: the pane shows what is behind it. A shell with nothing behind gets a
    /// chat (the latest), a chat a shell (in the project's folder); the one that goes
    /// behind keeps running.
    fn flip(&mut self) {
        let Some(id) = self.focused() else { return };
        if let Some(b) = self.behind.remove(&id) {
            if let Some(front) = self.panes.remove(&id) {
                self.behind.insert(id, front);
            }
            self.panes.insert(id, b);
            return;
        }
        match self.panes.get(&id) {
            Some(Pane::Shell(_) | Pane::Starting) => {
                if let Some(sh) = self.panes.remove(&id) {
                    self.behind.insert(id, sh);
                }
                self.chat_at(id, None, None);
                self.say("The shell keeps running behind this chat: Ctrl+b o brings it back.");
            }
            Some(Pane::Chat(_)) => {
                if let Some(chat) = self.panes.remove(&id) {
                    self.behind.insert(id, chat);
                }
                self.start_shell(id);
                self.say("The chat stays behind this shell: Ctrl+b o brings it back.");
            }
            _ => self.say("Choose what this pane shows first (c, d or s)."),
        }
    }

    /// The layout as saved: tabs, splits, and what each pane shows.
    fn layout(&self) -> Value {
        let one = |p: Option<&Pane>| -> Value {
            match p {
                Some(Pane::Chat(app)) => json!({"chat": app.chat_id(), "dash": app.section()}),
                Some(Pane::Shell(sh)) => json!({"shell": sh.cwd.display().to_string()}),
                _ => json!({"new": true}),
            }
        };
        let pane = |id: u64| -> Value {
            let mut v = one(self.panes.get(&id));
            if let Some(b @ (Pane::Chat(_) | Pane::Shell(_))) = self.behind.get(&id) {
                v["behind"] = one(Some(b));
            }
            v
        };
        json!({
            "active": self.active,
            "tabs": self.tabs.iter().map(|t| json!({
                "name": t.name, "zoom": t.zoom,
                "focus": t.root.leaves().iter().position(|x| *x == t.focus).unwrap_or(0),
                "root": t.root.to_json(&pane),
            })).collect::<Vec<_>>(),
        })
    }

    fn save(&mut self) {
        self.last_save = Instant::now();
        let text = self.layout().to_string();
        if text == self.saved {
            return;
        }
        if let Ok(dir) = crate::store::folder() {
            if write_private(&dir.join(LAYOUT_FILE), &text).is_ok() {
                self.saved = text;
            }
        }
    }

    /// The saved layout back; false when there is none (or it can't be read).
    fn restore(&mut self) -> bool {
        let Ok(dir) = crate::store::folder() else { return false };
        let Ok(text) = std::fs::read_to_string(dir.join(LAYOUT_FILE)) else {
            return false;
        };
        let Ok(v) = serde_json::from_str::<Value>(&text) else {
            return false;
        };
        for t in v.get("tabs").and_then(Value::as_array).into_iter().flatten() {
            let mut make = |p: &Value| -> Option<u64> {
                let id = self.id();
                // what was behind the pane first (it moves behind), then the pane
                if let Some(b) = p.get("behind") {
                    self.build_at(id, b, true);
                    if let Some(x) = self.panes.remove(&id) {
                        self.behind.insert(id, x);
                    }
                }
                self.build_at(id, p, false);
                Some(id)
            };
            let Some(root) = t.get("root").and_then(|r| node_from(r, &mut make)) else {
                continue;
            };
            let leaves = root.leaves();
            let fi = t.get("focus").and_then(Value::as_u64).unwrap_or(0) as usize;
            let focus = leaves.get(fi).copied().unwrap_or(leaves[0]);
            let name = t.get("name").and_then(Value::as_str).unwrap_or("").to_string();
            self.tabs.push(Tab {
                name,
                root,
                focus,
                zoom: t.get("zoom").and_then(Value::as_bool) == Some(true) && leaves.len() > 1,
            });
        }
        self.active =
            (v.get("active").and_then(Value::as_u64).unwrap_or(0) as usize).min(self.tabs.len().saturating_sub(1));
        !self.tabs.is_empty()
    }
}

/// A new pane's question.
fn draw_choose(f: &mut Frame, area: Rect) {
    let key = |k: &str| Span::styled(format!(" {k} "), Style::new().fg(Color::Black).bg(ACCENT));
    let lines = vec![
        Line::from(Span::styled(
            "What should this pane show?",
            Style::new().add_modifier(Modifier::BOLD),
        )),
        Line::default(),
        Line::from(vec![key("c"), Span::raw("  a new chat (Enter too)")]),
        Line::from(vec![
            key("d"),
            Span::raw("  the Dashboard (Ctrl+G in it goes back to its chat)"),
        ]),
        Line::from(vec![key("s"), Span::raw("  a shell, in the active project's folder")]),
        Line::default(),
        Line::from(Span::styled("Esc closes this pane.", DIM)),
    ];
    let h = (lines.len() as u16).min(area.height);
    let y = area.y + area.height.saturating_sub(h) / 2;
    let w = 62.min(area.width);
    let x = area.x + area.width.saturating_sub(w) / 2;
    f.render_widget(
        Paragraph::new(lines),
        Rect {
            x,
            y,
            width: w,
            height: h,
        },
    );
}

/// A file only you can read, written whole or not at all.
fn write_private(path: &Path, text: &str) -> std::io::Result<()> {
    use std::os::unix::fs::OpenOptionsExt;
    let tmp = path.with_extension("tmp");
    let _ = std::fs::remove_file(&tmp);
    let mut f = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(&tmp)?;
    f.write_all(text.as_bytes())?;
    f.sync_all()?;
    std::fs::rename(&tmp, path)
}

/// Opens the panes until the user quits (the chat window of T3, now in panes).
pub async fn run(server: Server, cred: Cred, mouse: bool) -> Result<()> {
    if !stdout().is_terminal() {
        bail!("The chat window needs a terminal.");
    }
    let api = Api::new(&server)?;
    let (tx, mut rx) = mpsc::unbounded_channel();
    let (shell_tx, mut shell_rx) = mpsc::unbounded_channel::<u64>();
    let mut ws = Workspace::new(server, cred, api, tx, shell_tx);
    if !ws.restore() {
        // the first time: one chat, as before (the latest one)
        let id = ws.chat(None, None);
        ws.tabs.push(Tab {
            name: String::new(),
            root: Node::Leaf(id),
            focus: id,
            zoom: false,
        });
    }

    let mut terminal = open_screen(mouse)?;
    let mut keys = EventStream::new();
    let mut tick = tokio::time::interval(Duration::from_millis(100));
    let result: Result<()> = async {
        loop {
            terminal.draw(|f| ws.draw(f))?;
            if ws.ring() {
                // a question waits: the terminal's bell (a sound, or a flash, per its settings)
                let mut out = stdout();
                let _ = out.write_all(b"\x07");
                let _ = out.flush();
            }
            let screen = {
                let s = terminal.size()?;
                Rect::new(0, 0, s.width, s.height)
            };
            tokio::select! {
                ev = keys.next() => match ev {
                    Some(Ok(TermEvent::Key(key))) => ws.on_key(key, screen),
                    Some(Ok(TermEvent::Paste(text))) => ws.on_paste(&text),
                    Some(Ok(TermEvent::Mouse(m))) => ws.on_mouse(m),
                    // a new size: the whole screen is painted again (nothing left behind)
                    Some(Ok(TermEvent::Resize(..))) => terminal.clear()?,
                    Some(Ok(_)) => {}
                    Some(Err(e)) => return Err(e.into()),
                    None => break,
                },
                Some(msg) = rx.recv() => {
                    ws.on_msg(msg);
                    // many messages come at once while streaming: take them all before drawing
                    while let Ok(msg) = rx.try_recv() {
                        ws.on_msg(msg);
                    }
                }
                Some(_) = shell_rx.recv() => {
                    // a shell printed: drawn again (after what else came meanwhile)
                    while shell_rx.try_recv().is_ok() {}
                }
                _ = tick.tick() => ws.tick(),
            }
            ws.reap();
            if ws.done() {
                break;
            }
            // A Dashboard section wants a long text edited: the editor gets the terminal
            if let Some((id, req)) = ws.take_edit() {
                drop(keys); // nothing else may read the keys meant for the editor
                close_screen(&mut terminal);
                let result = tokio::task::spawn_blocking(move || crate::ui::external::edit(&req.text, &req.suffix))
                    .await
                    .map_err(|e| e.to_string())
                    .and_then(|r| r.map_err(|e| format!("{e:#}")));
                terminal = open_screen(mouse)?;
                keys = EventStream::new();
                ws.edited(id, result);
            }
        }
        Ok(())
    }
    .await;
    if !ws.quitting && ws.fatal.is_none() {
        ws.save();
    }
    close_screen(&mut terminal);
    result?;
    if let Some(code) = ws.fatal {
        return Err(crate::socket::Refused(code).into());
    }
    Ok(())
}

type Screen = Terminal<CrosstermBackend<std::io::Stdout>>;

pub fn open_screen(mouse: bool) -> Result<Screen> {
    enable_raw_mode()?;
    execute!(stdout(), EnterAlternateScreen, EnableBracketedPaste)?;
    if mouse {
        execute!(stdout(), EnableMouseCapture)?;
    }
    // A crash must give the terminal back as it was, or the shell is unusable (once:
    // the screen opens again after an external editor)
    static HOOK: std::sync::Once = std::sync::Once::new();
    HOOK.call_once(|| {
        let default_hook = std::panic::take_hook();
        std::panic::set_hook(Box::new(move |info| {
            let _ = disable_raw_mode();
            let _ = execute!(
                stdout(),
                DisableMouseCapture,
                DisableBracketedPaste,
                LeaveAlternateScreen
            );
            default_hook(info);
        }));
    });
    Ok(Terminal::new(CrosstermBackend::new(stdout()))?)
}

pub fn close_screen(terminal: &mut Screen) {
    let _ = disable_raw_mode();
    let _ = execute!(
        terminal.backend_mut(),
        DisableMouseCapture,
        DisableBracketedPaste,
        LeaveAlternateScreen
    );
    let _ = terminal.show_cursor();
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tree() -> Node {
        // 1 | (2 over 3)
        let mut n = Node::Leaf(1);
        assert!(n.split(1, 2, true));
        assert!(n.split(2, 3, false));
        n
    }

    #[test]
    fn splits_cut_the_space() {
        let n = tree();
        assert_eq!(n.leaves(), vec![1, 2, 3]);
        let mut r = Vec::new();
        n.rects(Rect::new(0, 0, 100, 40), &mut r);
        assert_eq!(
            r,
            vec![
                (1, Rect::new(0, 0, 50, 40)),
                (2, Rect::new(50, 0, 50, 20)),
                (3, Rect::new(50, 20, 50, 20))
            ]
        );
        assert_eq!(neighbour(&r, 1, 'l'), Some(2));
        assert_eq!(neighbour(&r, 2, 'j'), Some(3));
        assert_eq!(neighbour(&r, 3, 'k'), Some(2));
        assert_eq!(neighbour(&r, 3, 'h'), Some(1));
        assert_eq!(neighbour(&r, 1, 'h'), None);
    }

    #[test]
    fn borders_can_be_found_and_moved() {
        let mut n = tree();
        let mut out = Vec::new();
        n.borders(Rect::new(0, 0, 100, 40), &mut Vec::new(), &mut out);
        // the right split's border at column 50, then the down split's at line 20
        assert_eq!(out[0].0, Rect::new(49, 0, 2, 40));
        assert!(out[0].1.is_empty() && out[0].3);
        assert_eq!(out[1].0, Rect::new(50, 19, 50, 2));
        assert_eq!(out[1].1, vec![true]);
        if let Some(Node::Split { ratio, .. }) = n.at(&[true]) {
            *ratio = 0.3;
        }
        let mut r = Vec::new();
        n.rects(Rect::new(0, 0, 100, 40), &mut r);
        assert_eq!(r[1].1, Rect::new(50, 0, 50, 12));
        assert!(n.at(&[false, true]).is_none());
    }

    #[test]
    fn closing_a_pane_gives_its_space_to_its_neighbour() {
        let n = tree().without(2).unwrap();
        assert_eq!(n.leaves(), vec![1, 3]);
        assert!(Node::Leaf(1).without(1).is_none());
    }

    #[test]
    fn resizing_moves_the_nearest_border() {
        let mut n = tree();
        assert!(n.resize(3, false, 0.1)); // 2 over 3: the down split
        assert!(n.resize(1, true, -0.1)); // the right split
        match &n {
            Node::Split { ratio, b, .. } => {
                assert!((*ratio - 0.4).abs() < 1e-6);
                match &**b {
                    Node::Split { ratio, .. } => assert!((*ratio - 0.6).abs() < 1e-6),
                    _ => panic!(),
                }
            }
            _ => panic!(),
        }
        assert!(!Node::Leaf(1).resize(1, true, 0.1));
    }

    #[test]
    fn a_layout_comes_back_as_it_was_saved() {
        let n = tree();
        let v = n.to_json(&|id| json!({"id": id}));
        let mut make = |p: &Value| p.get("id").and_then(Value::as_u64);
        assert_eq!(node_from(&v, &mut make), Some(n));
        // a pane that can't come back: its neighbour takes the place
        let mut skip2 = |p: &Value| p.get("id").and_then(Value::as_u64).filter(|id| *id != 2);
        assert_eq!(node_from(&v, &mut skip2).unwrap().leaves(), vec![1, 3]);
    }
}
