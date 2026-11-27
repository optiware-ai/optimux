//! Dashboard → Studio: the active project's files, as the web page's Studio (the
//! preview stays in the web page: a terminal can't show a page). The file tree with
//! git's letters, find a file, read a file and edit it in your editor (saved only if
//! nobody changed it meanwhile), new file or folder, rename, delete (moved to the
//! Trash), the Git pane (add one by one, diff, commit, push, update), and a file's
//! path or some of its lines sent to the chat.

use std::collections::{HashMap, HashSet};
use std::time::Instant;

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use ratatui::Frame;
use ratatui::layout::Rect;
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::Paragraph;
use serde_json::{Value, json};
use unicode_width::UnicodeWidthChar;

use super::git::{GitWindow, Kind as GitKind, change_label};
use super::{Answer, Ctx, Section, draw_head, head_lines};
use crate::api::{Method, enc};
use crate::chat::text::{DIM, wrap};
use crate::cli::{num, s};
use crate::dash::backups::bytes;
use crate::ui::confirm::{self, Confirm};
use crate::ui::external::Edit;
use crate::ui::form::{self, Field, Form, Kind};
use crate::ui::table::{Row, Table};

/// git's letter for a file, and its colour (the web page's markers).
pub fn letter_style(l: &str) -> Style {
    match l {
        "M" => Style::new().fg(Color::Yellow),
        "U" | "A" => Style::new().fg(Color::Green),
        "D" => Style::new().fg(Color::Red),
        "C" => Style::new().fg(Color::Magenta),
        _ => DIM,
    }
}

/// The folder a new file goes in: the chosen folder, or the chosen file's folder.
pub fn folder_of(path: &str, is_dir: bool) -> String {
    if is_dir {
        return path.to_string();
    }
    path.rsplit_once('/').map(|(d, _)| d.to_string()).unwrap_or_default()
}

/// A path inside a folder ("" is the project's top).
pub fn join(dir: &str, name: &str) -> String {
    let name = name.trim().trim_start_matches('/');
    if dir.is_empty() {
        name.to_string()
    } else {
        format!("{dir}/{name}")
    }
}

/// The file's ending for the editor's colours: letters and digits only.
pub fn suffix_of(path: &str) -> String {
    let name = path.rsplit('/').next().unwrap_or(path);
    match name.rsplit_once('.') {
        Some((stem, ext))
            if !stem.is_empty()
                && !ext.is_empty()
                && ext.len() <= 10
                && ext.chars().all(|c| c.is_ascii_alphanumeric()) =>
        {
            format!(".{ext}")
        }
        _ => ".txt".into(),
    }
}

/// What the chat gets for "send these lines": where they are, then the lines.
pub fn snippet(path: &str, lines: &[&str], first: usize) -> String {
    let lang = suffix_of(path).trim_start_matches('.').to_string();
    let lang = if lang == "txt" { String::new() } else { lang };
    let range = if lines.len() == 1 {
        format!("line {first}")
    } else {
        format!("lines {first}–{}", first + lines.len() - 1)
    };
    format!("From {path}, {range}:\n```{lang}\n{}\n```\n", lines.join("\n"))
}

/// A line of a file for the screen: tabs as 4 spaces, cut at `width` with "…".
fn shown(line: &str, width: usize) -> String {
    let mut out = String::new();
    let mut w = 0;
    for c in line.chars() {
        let (piece, cw) = if c == '\t' {
            ("    ".to_string(), 4)
        } else {
            (c.to_string(), c.width().unwrap_or(0))
        };
        if w + cw > width.saturating_sub(1) {
            out.push('…');
            return out;
        }
        out.push_str(&piece);
        w += cw;
    }
    out
}

struct Entry {
    name: String,
    path: String,
    dir: bool,
    ignored: bool,
    link: bool,
}

struct OpenFile {
    path: String,
    text: String,
    digest: String,
    hidden: u64,
    cursor: usize,
    top: usize,
    mark: Option<usize>,
    /// Edited text the server refused (changed on disk): kept, e opens it again.
    unsaved: Option<String>,
}

enum Name {
    File(String),
    Folder(String),
    Rename(String),
}

enum View {
    Tree,
    Find,
    File,
    Git,
    Diff(Vec<Line<'static>>),
    Name(Name, Form),
    Delete(String, Confirm),
    Commit,
}

pub struct Studio {
    project: String,
    git: bool,
    dirs: HashMap<String, Vec<Entry>>,
    open: HashSet<String>,
    status: HashMap<String, String>,
    table: Table,
    found: Table,
    query: String,
    typing: bool,
    view: View,
    back: Option<Box<View>>,
    file: Option<OpenFile>,
    state: Value,
    changes: Table,
    message: String,
    checked: bool,
    window: Option<GitWindow>,
    msg: Option<(bool, String)>,
    pending_edit: Option<Edit>,
    /// What the editor is open for: a file's text, or a commit message.
    editing_message: bool,
    goto: Option<(&'static str, String)>,
    mark: String,
    polling: bool,
    drawn_at: Instant,
    height: usize,
    diff_top: usize,
}

impl Default for Studio {
    fn default() -> Studio {
        let mut table = Table::new(&[("", 2), ("Files", 0)]);
        table.empty = "Loading…".into();
        let mut found = Table::new(&[("", 2), ("Path", 0)]);
        found.empty = "Nothing found.".into();
        let mut changes = Table::new(&[("", 4), ("File", 0), ("Change", 18)]);
        changes.empty = "Nothing to commit: every change is committed.".into();
        Studio {
            project: String::new(),
            git: false,
            dirs: HashMap::new(),
            open: HashSet::new(),
            status: HashMap::new(),
            table,
            found,
            query: String::new(),
            typing: false,
            view: View::Tree,
            back: None,
            file: None,
            state: Value::Null,
            changes,
            message: String::new(),
            checked: false,
            window: None,
            msg: None,
            pending_edit: None,
            editing_message: false,
            goto: None,
            mark: String::new(),
            polling: false,
            drawn_at: Instant::now(),
            height: 20,
            diff_top: 0,
        }
    }
}

impl Studio {
    fn load_dir(&self, ctx: &Ctx, path: &str) {
        ctx.call(
            format!("dir:{path}"),
            Method::Get,
            format!("/api/files/tree?path={}", enc(path)),
            None,
        );
    }

    fn reload(&mut self, ctx: &Ctx) {
        self.load_dir(ctx, "");
        let open: Vec<String> = self.open.iter().cloned().collect();
        for d in open {
            self.load_dir(ctx, &d);
        }
        ctx.call("status", Method::Get, "/api/files/status", None);
        if !self.project.is_empty() && self.git {
            ctx.call(
                "state",
                Method::Get,
                format!("/api/projects/{}/git-state", enc(&self.project)),
                None,
            );
        }
    }

    /// The tree as rows: open folders show what they hold.
    fn rows(&mut self) {
        let mut rows = Vec::new();
        self.add_rows("", 0, &mut rows);
        self.table.empty = if self.dirs.contains_key("") {
            "The project is empty: n makes a file.".into()
        } else {
            "Loading…".into()
        };
        self.table.set_rows(rows);
    }

    fn add_rows(&self, dir: &str, depth: usize, rows: &mut Vec<Row>) {
        let Some(entries) = self.dirs.get(dir) else { return };
        for e in entries {
            let changed_inside = e.dir && self.status.keys().any(|p| p.starts_with(&format!("{}/", e.path)));
            let (mark, mark_style) = match self.status.get(&e.path) {
                Some(l) => (l.clone(), letter_style(l)),
                None if changed_inside => ("•".into(), Style::new().fg(Color::Yellow)),
                None => (String::new(), DIM),
            };
            let open = self.open.contains(&e.path);
            let name = format!(
                "{}{}{}{}",
                "  ".repeat(depth),
                if e.dir {
                    if open { "▾ " } else { "▸ " }
                } else {
                    "  "
                },
                e.name,
                if e.dir { "/" } else { "" }
            ) + if e.link { " ↪" } else { "" };
            let style = if e.ignored {
                DIM
            } else if e.dir {
                Style::new().add_modifier(Modifier::BOLD)
            } else {
                Style::new()
            };
            rows.push(
                Row::new(format!("{}{}", if e.dir { "d:" } else { "f:" }, e.path))
                    .styled(mark, mark_style)
                    .styled(name, style),
            );
            if e.dir && open {
                self.add_rows(&e.path, depth + 1, rows);
            }
        }
    }

    /// The chosen row: (path, is a folder).
    fn chosen(&self) -> Option<(String, bool)> {
        let t = if matches!(self.view, View::Find) {
            &self.found
        } else {
            &self.table
        };
        let id = &t.chosen()?.id;
        Some((id[2..].to_string(), id.starts_with("d:")))
    }

    fn open_file(&mut self, ctx: &Ctx, path: &str) {
        ctx.call(
            format!("read:{path}"),
            Method::Get,
            format!("/api/files/read?path={}", enc(path)),
            None,
        );
    }

    fn git_rows(&mut self) {
        let rows = self
            .state
            .get("files")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .map(|f| {
                let staged = f.get("staged").and_then(Value::as_bool) == Some(true);
                let again = f.get("again").and_then(Value::as_bool) == Some(true);
                let mark = format!("{}{}", if staged { "[✓]" } else { "[ ]" }, if again { "↻" } else { "" });
                let style = if staged { Style::new().fg(Color::Green) } else { DIM };
                let warn = f.get("warn").and_then(Value::as_bool) == Some(true);
                Row::new(s(f, "path"))
                    .styled(mark, style)
                    .styled(
                        s(f, "path"),
                        if warn {
                            Style::new().fg(Color::Yellow)
                        } else {
                            Style::new()
                        },
                    )
                    .styled(change_label(f), DIM)
            })
            .collect();
        self.changes.set_rows(rows);
    }

    fn staged(&self) -> u64 {
        self.state.get("staged").and_then(Value::as_u64).unwrap_or(0)
    }

    fn strings(&self, k: &str) -> Vec<String> {
        self.state
            .get(k)
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .filter_map(Value::as_str)
            .map(str::to_string)
            .collect()
    }

    fn name_form(&self, kind: Name) -> Form {
        let (title, label, value, help) = match &kind {
            Name::File(dir) => (
                "New file",
                "Name",
                String::new(),
                format!(
                    "In {}; a name with folders (docs/intro.md) makes them",
                    if dir.is_empty() {
                        "the project's top folder".into()
                    } else {
                        format!("{dir}/")
                    }
                ),
            ),
            Name::Folder(dir) => (
                "New folder",
                "Name",
                String::new(),
                format!(
                    "In {}",
                    if dir.is_empty() {
                        "the project's top folder".into()
                    } else {
                        format!("{dir}/")
                    }
                ),
            ),
            Name::Rename(p) => (
                "Rename or move",
                "New path",
                p.clone(),
                "From the project's top folder; other files that mention the old path don't change".into(),
            ),
        };
        let mut f = Form::new(
            title,
            vec![Field::new("name", label, Kind::Text).value(value).required().help(help)],
        )
        .submit_label("Save");
        f.focus = 0;
        f
    }

    fn tree_key(&mut self, k: KeyEvent, ctx: &Ctx) -> bool {
        if self.table.key(k) {
            return true;
        }
        if k.modifiers.contains(KeyModifiers::CONTROL) {
            return false;
        }
        let chosen = self.chosen();
        match (k.code, chosen) {
            (KeyCode::Enter | KeyCode::Right, Some((p, true))) => {
                if self.open.contains(&p) && k.code == KeyCode::Enter {
                    self.open.remove(&p);
                } else {
                    self.open.insert(p.clone());
                    self.load_dir(ctx, &p);
                }
                self.rows();
            }
            (KeyCode::Enter, Some((p, false))) => {
                self.msg = None;
                self.open_file(ctx, &p);
            }
            (KeyCode::Left, Some((p, is_dir))) => {
                if is_dir && self.open.remove(&p) {
                    self.rows();
                } else if let Some((parent, _)) = p.rsplit_once('/') {
                    // to the folder it is in
                    let id = format!("d:{parent}");
                    if let Some(i) = self.table.visible().iter().position(|r| r.id == id) {
                        self.table.selected = i;
                    }
                }
            }
            (KeyCode::Char('f'), _) => {
                self.typing = true;
                self.query.clear();
                self.view = View::Find;
                self.found.set_rows(Vec::new());
            }
            (KeyCode::Char('n'), c) => {
                let dir = c.map(|(p, d)| folder_of(&p, d)).unwrap_or_default();
                self.view = View::Name(Name::File(dir.clone()), self.name_form(Name::File(dir)));
            }
            (KeyCode::Char('N'), c) => {
                let dir = c.map(|(p, d)| folder_of(&p, d)).unwrap_or_default();
                self.view = View::Name(Name::Folder(dir.clone()), self.name_form(Name::Folder(dir)));
            }
            (KeyCode::Char('R') | KeyCode::F(2), Some((p, _))) => {
                self.view = View::Name(Name::Rename(p.clone()), self.name_form(Name::Rename(p)));
            }
            (KeyCode::Char('x') | KeyCode::Delete, Some((p, _))) => {
                ctx.call(
                    format!("delinfo:{p}"),
                    Method::Get,
                    format!("/api/files/delete?path={}", enc(&p)),
                    None,
                );
            }
            (KeyCode::Char('m'), Some((p, d))) => self.mention(&p, d),
            (KeyCode::Char('g'), _) if self.git => {
                self.view = View::Git;
                ctx.call(
                    "state",
                    Method::Get,
                    format!("/api/projects/{}/git-state", enc(&self.project)),
                    None,
                );
            }
            (KeyCode::Char('g'), _) => self.msg = Some((false, "This project has no git repository.".into())),
            (KeyCode::Char('r'), _) => self.reload(ctx),
            _ => return false,
        }
        true
    }

    fn mention(&mut self, path: &str, dir: bool) {
        let what = if dir { "Folder" } else { "File" };
        self.goto = Some((
            "ChatText",
            format!("{what}: {path} (in the project; read it if you need it)"),
        ));
    }

    fn file_key(&mut self, k: KeyEvent, ctx: &Ctx) -> bool {
        let page = self.height.saturating_sub(2).max(1);
        let Some(file) = &mut self.file else {
            self.view = View::Tree;
            return true;
        };
        let n = file.text.lines().count().max(1);
        match k.code {
            KeyCode::Down => file.cursor = (file.cursor + 1).min(n - 1),
            KeyCode::Up => file.cursor = file.cursor.saturating_sub(1),
            KeyCode::PageDown | KeyCode::Char(' ') => file.cursor = (file.cursor + page).min(n - 1),
            KeyCode::PageUp => file.cursor = file.cursor.saturating_sub(page),
            KeyCode::Home => file.cursor = 0,
            KeyCode::End => file.cursor = n - 1,
            KeyCode::Char('v') => file.mark = if file.mark.is_some() { None } else { Some(file.cursor) },
            KeyCode::Char('s') => {
                let (a, b) = match file.mark {
                    Some(m) => (m.min(file.cursor), m.max(file.cursor)),
                    None => (file.cursor, file.cursor),
                };
                let lines: Vec<&str> = file.text.lines().skip(a).take(b - a + 1).collect();
                if lines.len() > 400 {
                    self.msg = Some((
                        false,
                        "That is more than 400 lines: choose fewer (v marks the start), or m mentions the whole file."
                            .into(),
                    ));
                } else {
                    self.goto = Some(("ChatText", snippet(&file.path, &lines, a + 1)));
                    file.mark = None;
                }
            }
            KeyCode::Char('m') => {
                let p = file.path.clone();
                self.mention(&p, false);
            }
            KeyCode::Char('e') => {
                let text = file.unsaved.clone().unwrap_or_else(|| file.text.clone());
                self.editing_message = false;
                self.pending_edit = Some(Edit {
                    text,
                    suffix: suffix_of(&file.path),
                });
            }
            KeyCode::Char('R') => {
                let p = file.path.clone();
                self.open_file(ctx, &p);
            }
            KeyCode::Esc => {
                self.view = self.back.take().map(|b| *b).unwrap_or(View::Tree);
            }
            _ => return false,
        }
        true
    }

    fn git_key(&mut self, k: KeyEvent, ctx: &Ctx) -> bool {
        if self.changes.key(k) {
            return true;
        }
        if k.modifiers.contains(KeyModifiers::CONTROL) {
            return false;
        }
        let proj = enc(&self.project);
        let chosen = self.changes.chosen().map(|r| r.id.clone());
        let file = |p: &str| {
            self.state
                .get("files")
                .and_then(Value::as_array)
                .and_then(|l| l.iter().find(|f| s(f, "path") == p))
                .cloned()
        };
        match (k.code, chosen) {
            (KeyCode::Char(' '), Some(p)) => {
                let staged = file(&p).and_then(|f| f.get("staged").and_then(Value::as_bool)) == Some(true);
                ctx.call(
                    "state",
                    Method::Post,
                    format!("/api/projects/{proj}/stage"),
                    Some(json!({"paths": [p], "staged": !staged})),
                );
            }
            (KeyCode::Char('a'), _) => ctx.call(
                "state",
                Method::Post,
                format!("/api/projects/{proj}/stage"),
                Some(json!({"paths": [], "staged": true})),
            ),
            (KeyCode::Char('A'), _) => ctx.call(
                "state",
                Method::Post,
                format!("/api/projects/{proj}/stage"),
                Some(json!({"paths": [], "staged": false})),
            ),
            (KeyCode::Enter, Some(p)) => {
                if file(&p).is_some_and(|f| s(&f, "status") == "deleted") {
                    self.msg = Some((false, format!("{p} is deleted: there is nothing to open.")));
                } else {
                    self.back = Some(Box::new(View::Git));
                    self.open_file(ctx, &p);
                }
            }
            (KeyCode::Char('d'), _) => {
                ctx.call(
                    "diff",
                    Method::Get,
                    format!("/api/projects/{proj}/diff?staged={}", self.staged() > 0),
                    None,
                );
            }
            (KeyCode::Char('c'), _) => {
                if self.changes.rows.is_empty() {
                    self.msg = Some((false, "Nothing to commit.".into()));
                } else {
                    self.checked = false;
                    self.view = View::Commit;
                }
            }
            (KeyCode::Char('P'), _) => self.window = Some(GitWindow::open(GitKind::Push, &self.project, ctx)),
            (KeyCode::Char('U'), _) => self.window = Some(GitWindow::open(GitKind::Update, &self.project, ctx)),
            (KeyCode::Char('r'), _) => ctx.call("state", Method::Get, format!("/api/projects/{proj}/git-state"), None),
            (KeyCode::Esc | KeyCode::Char('t'), _) => self.view = View::Tree,
            _ => return false,
        }
        true
    }

    fn commit_key(&mut self, k: KeyEvent, ctx: &Ctx) -> bool {
        match k.code {
            KeyCode::Esc => self.view = View::Git,
            KeyCode::Char('e') => {
                self.editing_message = true;
                self.pending_edit = Some(Edit {
                    text: self.message.clone(),
                    suffix: ".txt".into(),
                });
            }
            KeyCode::Char('w') => {
                self.msg = Some((true, "Writing the message (one call to the Simple model)…".into()));
                ctx.call(
                    "written",
                    Method::Post,
                    format!("/api/projects/{}/commit-message", enc(&self.project)),
                    Some(json!({"staged": self.staged() > 0})),
                );
            }
            KeyCode::Char('c') if !self.strings("warnings").is_empty() => self.checked = !self.checked,
            KeyCode::Enter => {
                if !self.strings("problems").is_empty() {
                    self.msg = Some((false, "It can't be committed: see ⛔ above.".into()));
                } else if !self.strings("warnings").is_empty() && !self.checked {
                    self.msg = Some((false, "Read the warnings, then c to say you checked them.".into()));
                } else if self.message.trim().is_empty() {
                    self.msg = Some((false, "Write a message first (e, or w to have it written).".into()));
                } else {
                    self.msg = Some((true, "Committing…".into()));
                    ctx.call(
                        "committed",
                        Method::Post,
                        format!("/api/projects/{}/commit", enc(&self.project)),
                        Some(json!({
                            "message": self.message.trim(), "tree": self.state.get("tree"), "confirmed": self.checked,
                            "staged_tree": self.state.get("staged_tree"),
                        })),
                    );
                }
            }
            KeyCode::Down | KeyCode::Up | KeyCode::PageDown | KeyCode::PageUp => {}
            _ => return false,
        }
        true
    }

    fn diff_lines(v: &Value, width: usize) -> Vec<Line<'static>> {
        let mut out = Vec::new();
        let files = v.get("files").and_then(Value::as_array).cloned().unwrap_or_default();
        out.push(Line::from(Span::styled(
            format!(
                "{} file(s) · +{} −{}{}",
                files.len(),
                num(v, "added").unwrap_or(0.0),
                num(v, "removed").unwrap_or(0.0),
                if v.get("staged").and_then(Value::as_bool) == Some(true) {
                    " · only what is added"
                } else {
                    ""
                }
            ),
            DIM,
        )));
        for f in files {
            out.push(Line::default());
            out.push(Line::from(vec![
                Span::styled(
                    s(&f, "path"),
                    Style::new().add_modifier(Modifier::BOLD).fg(Color::LightCyan),
                ),
                Span::styled(
                    format!(
                        "  {} · +{} −{}",
                        s(&f, "status"),
                        num(&f, "added").unwrap_or(0.0),
                        num(&f, "removed").unwrap_or(0.0)
                    ),
                    DIM,
                ),
            ]));
            if f.get("binary").and_then(Value::as_bool) == Some(true) {
                out.push(Line::from(Span::styled("  a binary file: no lines to show", DIM)));
                continue;
            }
            for l in f.get("lines").and_then(Value::as_array).into_iter().flatten() {
                let (k, t) = (s(l, "k"), s(l, "t"));
                let style = match k.as_str() {
                    "+" => Style::new().fg(Color::Green),
                    "-" => Style::new().fg(Color::Red),
                    "@" => Style::new().fg(Color::Blue),
                    _ => DIM,
                };
                let lead = if k == "@" {
                    String::new()
                } else {
                    format!("{} ", if k == " " || k.is_empty() { " " } else { &k })
                };
                out.push(Line::from(Span::styled(shown(&format!("{lead}{t}"), width), style)));
            }
            if num(&f, "more").unwrap_or(0.0) > 0.0 {
                out.push(Line::from(Span::styled(
                    format!("  … {} more line(s)", num(&f, "more").unwrap_or(0.0)),
                    DIM,
                )));
            }
        }
        if num(v, "more_files").unwrap_or(0.0) > 0.0 {
            out.push(Line::from(Span::styled(
                format!("… and {} more file(s)", num(v, "more_files").unwrap_or(0.0)),
                DIM,
            )));
        }
        out
    }

    /// git-mark every few seconds while the Studio is on screen: the tree and the Git
    /// pane follow changes made elsewhere (by a model, a terminal, an editor).
    fn poll(&mut self, ctx: &Ctx) {
        if !self.polling && self.git && !self.project.is_empty() {
            self.polling = true;
            let path = format!("/api/projects/{}/git-mark", enc(&self.project));
            if self.mark.is_empty() {
                // the first reading at once: later ones are compared with it
                ctx.call("mark", Method::Get, path, None);
            } else {
                ctx.call_later(4000, "mark", Method::Get, path);
            }
        }
    }
}

impl Section for Studio {
    fn load(&mut self, ctx: &Ctx) {
        self.reload(ctx);
    }

    fn key(&mut self, k: KeyEvent, ctx: &Ctx) -> bool {
        if let Some(w) = &mut self.window {
            if w.key(k, ctx) {
                self.window = None;
                self.reload(ctx);
            }
            return true;
        }
        if self.typing {
            match k.code {
                KeyCode::Esc => {
                    self.typing = false;
                    self.view = View::Tree;
                }
                KeyCode::Enter => {
                    self.typing = false;
                    if self.query.trim().is_empty() {
                        self.view = View::Tree;
                    } else {
                        ctx.call(
                            "found",
                            Method::Get,
                            format!("/api/files/search?q={}", enc(self.query.trim())),
                            None,
                        );
                    }
                }
                KeyCode::Backspace => {
                    self.query.pop();
                }
                KeyCode::Char('u') if k.modifiers.contains(KeyModifiers::CONTROL) => self.query.clear(),
                KeyCode::Char(c) if !k.modifiers.contains(KeyModifiers::CONTROL) => self.query.push(c),
                _ => {}
            }
            return true;
        }
        let view = std::mem::replace(&mut self.view, View::Tree);
        match view {
            View::Tree => self.tree_key(k, ctx),
            View::Find => {
                self.view = View::Find;
                if self.found.key(k) {
                    return true;
                }
                match (k.code, self.chosen()) {
                    (KeyCode::Enter, Some((p, false))) => {
                        self.back = Some(Box::new(View::Find));
                        self.open_file(ctx, &p);
                    }
                    (KeyCode::Enter, Some((p, true))) => {
                        // show the folder in the tree, opened
                        let mut at = String::new();
                        for part in p.split('/') {
                            at = join(&at, part);
                            self.open.insert(at.clone());
                            self.load_dir(ctx, &at);
                        }
                        self.view = View::Tree;
                        self.rows();
                    }
                    (KeyCode::Char('f'), _) => self.typing = true,
                    (KeyCode::Char('m'), Some((p, d))) => self.mention(&p, d),
                    (KeyCode::Esc, _) => self.view = View::Tree,
                    _ => return false,
                }
                true
            }
            View::File => {
                self.view = View::File;
                self.file_key(k, ctx)
            }
            View::Git => {
                self.view = View::Git;
                self.git_key(k, ctx)
            }
            View::Commit => {
                self.view = View::Commit;
                self.commit_key(k, ctx)
            }
            View::Diff(lines) => {
                let page = self.height.saturating_sub(2).max(1);
                match k.code {
                    KeyCode::Esc => {
                        self.view = View::Git;
                        return true;
                    }
                    KeyCode::Down => self.diff_top += 1,
                    KeyCode::Up => self.diff_top = self.diff_top.saturating_sub(1),
                    KeyCode::PageDown | KeyCode::Char(' ') => self.diff_top += page,
                    KeyCode::PageUp => self.diff_top = self.diff_top.saturating_sub(page),
                    KeyCode::Home => self.diff_top = 0,
                    _ => {
                        self.view = View::Diff(lines);
                        return false;
                    }
                }
                self.view = View::Diff(lines);
                true
            }
            View::Name(kind, mut f) => {
                match f.key(k) {
                    form::Action::Cancel => self.view = View::Tree,
                    form::Action::Submit => {
                        let name = f.get("name").trim().to_string();
                        let project = self.project.clone();
                        f.busy = true;
                        match &kind {
                            Name::File(dir) => {
                                let p = join(dir, &name);
                                ctx.call(
                                    format!("created:{p}"),
                                    Method::Post,
                                    "/api/files/save",
                                    Some(json!({"path": p, "text": "", "digest": null, "project": project})),
                                );
                            }
                            Name::Folder(dir) => {
                                let p = join(dir, &name);
                                ctx.call(
                                    "made",
                                    Method::Post,
                                    "/api/files/folder",
                                    Some(json!({"path": p, "project": project})),
                                );
                            }
                            Name::Rename(from) => {
                                ctx.call(
                                    "renamed",
                                    Method::Post,
                                    "/api/files/rename",
                                    Some(json!({"path": from, "to": name.trim_start_matches('/'), "project": project})),
                                );
                            }
                        }
                        self.view = View::Name(kind, f);
                    }
                    form::Action::None => self.view = View::Name(kind, f),
                }
                true
            }
            View::Delete(p, mut c) => {
                match c.key(k) {
                    confirm::Answer::Yes => {
                        ctx.call(
                            format!("deleted:{p}"),
                            Method::Post,
                            "/api/files/delete",
                            Some(json!({"path": p, "project": self.project})),
                        );
                        self.view = View::Tree;
                    }
                    confirm::Answer::No => self.view = View::Tree,
                    confirm::Answer::Waiting => self.view = View::Delete(p, c),
                }
                true
            }
        }
    }

    fn paste(&mut self, text: &str) {
        if self.typing {
            self.query.push_str(&text.replace(['\n', '\r'], " "));
        } else if let View::Name(_, f) = &mut self.view {
            f.paste(text);
        }
    }

    fn on_answer(&mut self, a: Answer, ctx: &Ctx) {
        if a.tag.starts_with("git:") {
            if let Some(w) = &mut self.window {
                w.on_answer(a);
            }
            return;
        }
        let (what, arg) = a.parts();
        let (what, arg) = (what.to_string(), arg.to_string());
        match (what.as_str(), a.result) {
            ("dir", Ok(v)) => {
                let project = s(&v, "project");
                if project != self.project && arg.is_empty() {
                    // another active project: start again
                    if !self.project.is_empty() {
                        self.open.clear();
                        self.dirs.clear();
                        self.file = None;
                        self.view = View::Tree;
                    }
                    self.project = project;
                }
                let entries = v
                    .get("entries")
                    .and_then(Value::as_array)
                    .into_iter()
                    .flatten()
                    .map(|e| Entry {
                        name: s(e, "name"),
                        path: s(e, "path"),
                        dir: s(e, "type") == "dir",
                        ignored: e.get("ignored").and_then(Value::as_bool) == Some(true),
                        link: e.get("link").and_then(Value::as_bool) == Some(true),
                    })
                    .collect();
                self.dirs.insert(arg, entries);
                self.rows();
                // whichever comes last, the tree (the project) or the status (git)
                self.poll(ctx);
            }
            ("dir", Err(e)) if !arg.is_empty() => {
                self.open.remove(&arg);
                self.msg = Some((false, e));
                self.rows();
            }
            ("status", Ok(v)) => {
                self.git = v.get("git").and_then(Value::as_bool) == Some(true);
                self.status = v
                    .get("files")
                    .and_then(Value::as_object)
                    .map(|m| {
                        m.iter()
                            .map(|(k, l)| (k.clone(), l.as_str().unwrap_or("").to_string()))
                            .collect()
                    })
                    .unwrap_or_default();
                self.rows();
                self.poll(ctx);
                if self.git && self.state.is_null() {
                    ctx.call(
                        "state",
                        Method::Get,
                        format!("/api/projects/{}/git-state", enc(&self.project)),
                        None,
                    );
                }
            }
            ("mark", Ok(v)) => {
                self.polling = false;
                let mark = s(&v, "mark");
                if !self.mark.is_empty() && mark != self.mark {
                    self.reload(ctx);
                }
                self.mark = mark;
                // only while the Studio is on screen
                if self.drawn_at.elapsed().as_secs() < 6 {
                    self.poll(ctx);
                }
            }
            ("mark", Err(_)) => self.polling = false,
            ("state", Ok(v)) => {
                self.state = v;
                self.git_rows();
            }
            ("found", Ok(v)) => {
                let rows = v
                    .get("results")
                    .and_then(Value::as_array)
                    .into_iter()
                    .flatten()
                    .map(|r| {
                        let dir = s(r, "type") == "dir";
                        let l = self.status.get(&s(r, "path")).cloned().unwrap_or_default();
                        Row::new(format!("{}{}", if dir { "d:" } else { "f:" }, s(r, "path")))
                            .styled(l.clone(), letter_style(&l))
                            .styled(
                                format!("{}{}", s(r, "path"), if dir { "/" } else { "" }),
                                if dir {
                                    Style::new().add_modifier(Modifier::BOLD)
                                } else {
                                    Style::new()
                                },
                            )
                    })
                    .collect();
                self.found.set_rows(rows);
                self.found.selected = 0;
                self.found.empty = format!("Nothing found for “{}”.", self.query.trim());
                self.view = View::Find;
            }
            ("read", Ok(v)) => {
                let same = self.file.as_ref().is_some_and(|f| f.path == s(&v, "path"));
                let (cursor, unsaved) = match (&self.file, same) {
                    (Some(f), true) => (f.cursor, f.unsaved.clone()),
                    _ => (0, None),
                };
                self.file = Some(OpenFile {
                    path: s(&v, "path"),
                    text: s(&v, "text"),
                    digest: s(&v, "digest"),
                    hidden: v.get("hidden").and_then(Value::as_u64).unwrap_or(0),
                    cursor,
                    top: 0,
                    mark: None,
                    unsaved,
                });
                if self.back.is_none() {
                    self.back = Some(Box::new(View::Tree));
                }
                self.view = View::File;
                if let Some(f) = &self.file {
                    if f.unsaved.is_some() {
                        self.msg = Some((
                            true,
                            "Reloaded from disk. Your edited text is kept: e opens it again.".into(),
                        ));
                    }
                }
            }
            ("read", Err(e)) => self.msg = Some((false, e)),
            ("created", Ok(v)) => {
                self.reload(ctx);
                // a new file opens in the editor straight away; it is the open file now
                // (from this answer, so the editor's text can't go to another file)
                let path = if s(&v, "path").is_empty() {
                    arg.clone()
                } else {
                    s(&v, "path")
                };
                self.file = Some(OpenFile {
                    path: path.clone(),
                    text: String::new(),
                    digest: s(&v, "digest"),
                    hidden: 0,
                    cursor: 0,
                    top: 0,
                    mark: None,
                    unsaved: None,
                });
                self.back = Some(Box::new(View::Tree));
                self.view = View::File;
                self.editing_message = false;
                self.pending_edit = Some(Edit {
                    text: String::new(),
                    suffix: suffix_of(&path),
                });
                self.msg = Some((true, format!("Made {path}.")));
            }
            ("made", Ok(v)) => {
                self.view = View::Tree;
                self.msg = Some((true, format!("Made the folder {}.", s(&v, "path"))));
                self.reload(ctx);
            }
            ("renamed", Ok(v)) => {
                self.view = View::Tree;
                self.msg = Some((true, format!("Renamed: now {}.", s(&v, "to"))));
                self.reload(ctx);
            }
            ("delinfo", Ok(v)) => {
                let where_ = if s(&v, "trash") == "home" {
                    "the Trash (restore it from your file manager)"
                } else {
                    "the Workspace's .trash folder"
                };
                let what = if s(&v, "kind") != "file" {
                    format!(
                        "the folder {arg} ({} file(s), {})",
                        num(&v, "files").unwrap_or(0.0),
                        bytes(num(&v, "size").unwrap_or(0.0))
                    )
                } else {
                    format!("{arg} ({})", bytes(num(&v, "size").unwrap_or(0.0)))
                };
                let tracked = num(&v, "tracked").unwrap_or(0.0);
                let git = if tracked > 0.0 {
                    format!(" git tracks {tracked:.0} of its file(s): the commit will say they are deleted.")
                } else {
                    String::new()
                };
                self.view = View::Delete(
                    arg.clone(),
                    Confirm::new(
                        "Delete?",
                        format!("Moves {what} to {where_}; nothing is deleted for good.{git}"),
                        "move it",
                    ),
                );
            }
            ("deleted", Ok(_)) => {
                self.msg = Some((true, format!("Moved {arg} to the Trash.")));
                if self.file.as_ref().is_some_and(|f| f.path == arg) {
                    self.file = None;
                }
                self.reload(ctx);
            }
            ("saved", Ok(v)) => {
                if let Some(f) = &mut self.file {
                    f.unsaved = None;
                    if !s(&v, "digest").is_empty() {
                        f.digest = s(&v, "digest");
                    }
                    let p = f.path.clone();
                    self.msg = Some((
                        true,
                        if v.get("changed").and_then(Value::as_bool) == Some(false) {
                            "No change.".into()
                        } else {
                            format!("Saved {p}.")
                        },
                    ));
                    self.open_file(ctx, &p);
                }
                ctx.call("status", Method::Get, "/api/files/status", None);
            }
            ("saved", Err(e)) => {
                // the edited text stays; R reloads, then e opens it again
                self.msg = Some((
                    false,
                    format!(
                        "{e} Nothing was written; your text is kept: R reloads the file, then e opens your text again."
                    ),
                ));
            }
            ("diff", Ok(v)) => {
                self.diff_top = 0;
                self.view = View::Diff(Self::diff_lines(&v, 200));
            }
            ("written", Ok(v)) => {
                self.message = s(&v, "message");
                let cost = num(&v, "cost").map(|c| format!(" (${c:.4})")).unwrap_or_default();
                self.msg = Some((true, format!("Message written{cost}: e changes it.")));
            }
            ("committed", Ok(v)) => {
                self.message.clear();
                self.view = View::Git;
                self.msg = Some((true, format!("Committed {}. P pushes it.", s(&v, "commit"))));
                self.reload(ctx);
            }
            (_, Err(e)) => {
                if let View::Name(_, f) = &mut self.view {
                    f.busy = false;
                    f.error = Some(e);
                } else {
                    self.msg = Some((false, e));
                }
            }
            _ => {}
        }
    }

    fn keys(&self) -> String {
        if let Some(w) = &self.window {
            return w.keys();
        }
        if self.typing {
            return "type words of the path · Enter find · Esc cancel".into();
        }
        let git = if self.git { " · g git" } else { "" };
        match &self.view {
            View::Tree => format!("Enter open · ←→ fold · f find a file · n new file · N new folder · R rename · x delete · m mention in chat{git} · r refresh"),
            View::Find => "Enter open · m mention in chat · f find again · Esc the tree".into(),
            View::File => {
                let mark = if self.file.as_ref().is_some_and(|f| f.mark.is_some()) { "v unmark" } else { "v mark from here" };
                format!("↑↓ PgUp PgDn move · e edit in your editor · {mark} · s send the lines to chat · m mention · R reload · Esc back")
            }
            View::Git => "Space add/take back · a add all · A take all back · Enter open · d diff · c commit · P push · U update · r refresh · Esc the tree".into(),
            View::Diff(_) => "↑↓ PgUp PgDn scroll · Esc back".into(),
            View::Commit => {
                let c = if self.strings("warnings").is_empty() { "" } else if self.checked { " · c unchecked" } else { " · c I checked the warnings" };
                format!("e write the message · w have it written (one call){c} · Enter commit · Esc back")
            }
            View::Name(..) | View::Delete(..) => String::new(),
        }
    }

    fn draw(&mut self, f: &mut Frame, area: Rect) -> Vec<(u16, usize)> {
        self.drawn_at = Instant::now();
        let width = area.width as usize;
        let extra = if self.project.is_empty() {
            "  ·  the active project's files".to_string()
        } else {
            format!(
                "  ·  {}{}",
                self.project,
                if self.git {
                    format!(" · {}", s(&self.state, "branch"))
                } else {
                    String::new()
                }
            )
        };
        let mut head = head_lines("Studio", &extra, &self.msg, width);
        if self.typing || matches!(self.view, View::Find) {
            head.push(Line::from(vec![
                Span::styled("Find a file: ", Style::new().fg(Color::LightCyan)),
                Span::raw(self.query.clone()),
                Span::styled(if self.typing { "█" } else { "" }, Style::new().fg(Color::LightCyan)),
            ]));
        }
        let body = draw_head(f, area, head);
        self.height = body.height as usize;
        if let Some(w) = &mut self.window {
            w.draw(f, body);
            return Vec::new();
        }
        let title = Style::new().fg(Color::LightCyan).add_modifier(Modifier::BOLD);
        match &mut self.view {
            View::Tree => self.table.draw(f, body),
            View::Find => self.found.draw(f, body),
            View::File => {
                let Some(file) = &mut self.file else { return Vec::new() };
                let mut top_lines = vec![Line::from(vec![
                    Span::styled(file.path.clone(), title),
                    Span::styled(format!("  ·  {} line(s)", file.text.lines().count()), DIM),
                ])];
                if file.hidden > 0 {
                    top_lines.push(Line::from(Span::styled(
                        format!(
                            "🔒 {} saved key(s) hidden as ••••(NAME): keep the marks and the keys are written back.",
                            file.hidden
                        ),
                        Style::new().fg(Color::Yellow),
                    )));
                }
                if file.unsaved.is_some() {
                    top_lines.push(Line::from(Span::styled(
                        "Your edited text isn't saved: e opens it again.",
                        Style::new().fg(Color::Yellow),
                    )));
                }
                let n = top_lines.len() as u16;
                f.render_widget(
                    Paragraph::new(top_lines),
                    Rect {
                        height: n.min(body.height),
                        ..body
                    },
                );
                let text_area = Rect {
                    y: body.y + n,
                    height: body.height.saturating_sub(n),
                    ..body
                };
                let h = text_area.height as usize;
                let lines: Vec<&str> = file.text.lines().collect();
                let gutter = format!("{}", lines.len().max(1)).len();
                if file.cursor < file.top {
                    file.top = file.cursor;
                } else if h > 0 && file.cursor >= file.top + h {
                    file.top = file.cursor + 1 - h;
                }
                let (a, b) = match file.mark {
                    Some(m) => (m.min(file.cursor), m.max(file.cursor)),
                    None => (file.cursor, file.cursor),
                };
                let out: Vec<Line> = lines
                    .iter()
                    .enumerate()
                    .skip(file.top)
                    .take(h)
                    .map(|(i, l)| {
                        let picked = i >= a && i <= b;
                        let style = if i == file.cursor {
                            Style::new().bg(Color::Indexed(237))
                        } else if picked && file.mark.is_some() {
                            Style::new().bg(Color::Indexed(236))
                        } else {
                            Style::new()
                        };
                        Line::from(vec![
                            Span::styled(format!("{:>gutter$} ", i + 1), DIM),
                            Span::styled(shown(l, width.saturating_sub(gutter + 1)), style),
                        ])
                    })
                    .collect();
                f.render_widget(Paragraph::new(out), text_area);
                Vec::new()
            }
            View::Git => {
                let mut top = Vec::new();
                let ahead = num(&self.state, "ahead").unwrap_or(0.0);
                let behind = num(&self.state, "behind").unwrap_or(0.0);
                top.push(Line::from(vec![
                    Span::styled("Git", title),
                    Span::styled(
                        format!(
                            "  ·  {} · ↑{ahead:.0} to push · ↓{behind:.0} to pull (as of the last Push, Update or Status)",
                            s(&self.state, "branch")
                        ),
                        DIM,
                    ),
                ]));
                let total = self.changes.rows.len();
                if self.staged() > 0 {
                    top.push(Line::from(Span::styled(
                        format!("{} of {total} added: Commit takes only these.", self.staged()),
                        Style::new().fg(Color::Green),
                    )));
                } else if total > 0 {
                    top.push(Line::from(Span::styled(
                        format!("{total} uncommitted file(s): Commit takes them all (Space adds one by one)."),
                        DIM,
                    )));
                }
                for p in self.strings("problems") {
                    top.extend(wrap(
                        vec![Span::styled(p, Style::new().fg(Color::Red))],
                        width,
                        "⛔ ",
                        "   ",
                    ));
                }
                for w in self.strings("warnings") {
                    top.extend(wrap(
                        vec![Span::styled(w, Style::new().fg(Color::Yellow))],
                        width,
                        "⚠ ",
                        "  ",
                    ));
                }
                if self
                    .changes
                    .rows
                    .iter()
                    .any(|r| r.cells.first().is_some_and(|(c, _)| c.contains('↻')))
                {
                    top.push(Line::from(Span::styled(
                        "↻ changed again after it was added: take it back and add it again.",
                        DIM,
                    )));
                }
                top.push(Line::default());
                let n = (top.len() as u16).min(body.height);
                f.render_widget(Paragraph::new(top), Rect { height: n, ..body });
                self.changes.draw(
                    f,
                    Rect {
                        y: body.y + n,
                        height: body.height.saturating_sub(n),
                        ..body
                    },
                )
            }
            View::Diff(lines) => {
                self.diff_top = self.diff_top.min(lines.len().saturating_sub(body.height as usize));
                let shown: Vec<Line> = lines
                    .iter()
                    .skip(self.diff_top)
                    .take(body.height as usize)
                    .cloned()
                    .collect();
                f.render_widget(Paragraph::new(shown), body);
                Vec::new()
            }
            View::Commit => {
                let mut lines = vec![Line::from(Span::styled("Commit", title))];
                let staged = self.staged();
                lines.push(Line::from(Span::styled(
                    if staged > 0 {
                        format!("Takes the {staged} added file(s), here only (P pushes afterwards).")
                    } else {
                        format!(
                            "Takes every one of the {} file(s), here only (P pushes afterwards).",
                            self.changes.rows.len()
                        )
                    },
                    DIM,
                )));
                lines.push(Line::default());
                for p in self.strings("problems") {
                    lines.extend(wrap(
                        vec![Span::styled(p, Style::new().fg(Color::Red))],
                        width,
                        "⛔ ",
                        "   ",
                    ));
                }
                for w in self.strings("warnings") {
                    lines.extend(wrap(
                        vec![Span::styled(w, Style::new().fg(Color::Yellow))],
                        width,
                        "⚠ ",
                        "  ",
                    ));
                }
                if !self.strings("warnings").is_empty() {
                    lines.push(Line::from(Span::styled(
                        if self.checked {
                            "[✓] I checked the warnings"
                        } else {
                            "[ ] I checked the warnings (c)"
                        },
                        if self.checked {
                            Style::new().fg(Color::Green)
                        } else {
                            DIM
                        },
                    )));
                    lines.push(Line::default());
                }
                lines.push(Line::from(Span::styled(
                    "Message",
                    Style::new().add_modifier(Modifier::BOLD),
                )));
                if self.message.trim().is_empty() {
                    lines.push(Line::from(Span::styled(
                        "(none yet: e writes it, w has the Simple model write it)",
                        DIM,
                    )));
                } else {
                    for l in self.message.lines() {
                        lines.extend(wrap(vec![Span::raw(l.to_string())], width, "  ", "  "));
                    }
                }
                f.render_widget(Paragraph::new(lines), body);
                Vec::new()
            }
            View::Name(_, form) => {
                let t = vec![Line::from(Span::styled(form.title.clone(), title)), Line::default()];
                f.render_widget(
                    Paragraph::new(t),
                    Rect {
                        height: 2.min(body.height),
                        ..body
                    },
                );
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
            View::Delete(_, c) => {
                let mut lines = vec![
                    Line::from(Span::styled(
                        c.title.clone(),
                        Style::new().fg(Color::Red).add_modifier(Modifier::BOLD),
                    )),
                    Line::default(),
                ];
                lines.extend(c.lines(width));
                f.render_widget(Paragraph::new(lines), body);
                Vec::new()
            }
        }
    }

    fn tabs(&self) -> Vec<Vec<String>> {
        self.window.as_ref().map(|w| w.tabs()).unwrap_or_default()
    }

    fn click_tab(&mut self, _row: usize, i: usize, ctx: &Ctx) {
        if let Some(w) = &mut self.window {
            w.click_tab(i, ctx);
        }
    }

    fn has_window(&self) -> bool {
        self.window.is_some()
            || self.typing
            || matches!(self.view, View::Name(..) | View::Delete(..))
            || self.table.filtering
            || self.found.filtering
            || self.changes.filtering
    }

    fn click_row(&mut self, i: usize, ctx: &Ctx) -> bool {
        let opens = matches!(self.view, View::Tree | View::Find);
        let t = match self.view {
            View::Tree => &mut self.table,
            View::Find => &mut self.found,
            View::Git => &mut self.changes,
            _ => return false,
        };
        t.selected = i.min(t.visible().len().saturating_sub(1));
        if opens {
            // the tree as in a file manager: a click opens a folder or a file
            self.key(KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE), ctx);
        }
        opens
    }

    fn take_goto(&mut self) -> Option<(&'static str, String)> {
        self.goto.take()
    }

    fn take_edit(&mut self) -> Option<Edit> {
        if let Some(w) = &mut self.window {
            return w.take_edit();
        }
        self.pending_edit.take()
    }

    fn edited(&mut self, result: Result<Option<String>, String>, ctx: &Ctx) {
        if let Some(w) = &mut self.window {
            return w.edited(result);
        }
        match result {
            Err(e) => self.msg = Some((false, e)),
            Ok(None) if !self.editing_message => self.msg = Some((true, "No change.".into())),
            Ok(None) => {}
            Ok(Some(text)) if self.editing_message => self.message = text.trim_end().to_string(),
            Ok(Some(text)) => {
                let Some(file) = &mut self.file else {
                    self.msg = Some((false, "No file is open any more: your text wasn't saved.".into()));
                    return;
                };
                file.unsaved = Some(text.clone());
                self.msg = Some((true, format!("Saving {}…", file.path)));
                ctx.call(
                    "saved",
                    Method::Post,
                    "/api/files/save",
                    Some(json!({"path": file.path, "text": text, "digest": file.digest, "project": self.project})),
                );
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn paths_and_endings() {
        assert_eq!(folder_of("src/a.rs", false), "src");
        assert_eq!(folder_of("a.rs", false), "");
        assert_eq!(folder_of("src", true), "src");
        assert_eq!(join("", "/x.md"), "x.md");
        assert_eq!(join("docs", "intro.md"), "docs/intro.md");
        assert_eq!(suffix_of("src/main.rs"), ".rs");
        assert_eq!(suffix_of("Makefile"), ".txt");
        assert_eq!(suffix_of(".env"), ".txt");
        assert_eq!(suffix_of("a.b$c"), ".txt");
    }

    #[test]
    fn lines_for_the_chat_and_the_screen() {
        assert_eq!(
            snippet("a.py", &["x = 1"], 3),
            "From a.py, line 3:\n```py\nx = 1\n```\n"
        );
        assert!(snippet("notes", &["a", "b"], 1).starts_with("From notes, lines 1–2:\n```\n"));
        assert_eq!(shown("\tab", 20), "    ab");
        assert_eq!(shown("abcdefgh", 5), "abcd…");
    }
}
