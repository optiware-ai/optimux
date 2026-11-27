//! Dashboard → Projects, as in the web page: every project with its costs, chats and
//! budget; make one active, create one, clone one from GitHub, archive it; the archived
//! projects (restore, delete permanently) and the deleted ones (records kept).
//! Push and Update with GitHub come with the GitHub step.

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
use crate::ui::confirm::{self, Confirm};
use crate::ui::form::{self, Field, Form, Kind};
use crate::ui::table::{Row, Table};

/// The kinds of project, as TEMPLATE_SPECS in core/workspace.py (tests/test_templates.py
/// compares them): (id, label, folders).
pub const KINDS: &[(&str, &str, &str)] = &[
    ("basic", "Basic", ""),
    ("python", "Python", "src/, tests/"),
    ("web", "Web page", "css/, js/, assets/"),
    ("node", "Node.js", "src/, tests/"),
    ("java", "Java", "src/main/java/, src/test/java/"),
    ("c", "C", "src/, include/, tests/"),
    ("cpp", "C++", "src/, include/, tests/"),
    ("rust", "Rust", "src/, tests/"),
    ("go", "Go", "cmd/, internal/"),
    ("firmware", "Firmware", "src/, include/, lib/, test/, docs/"),
];

/// A project's folder name, as the page's PROJECT_NAME_RE.
pub fn name_problem(n: &str) -> Option<&'static str> {
    let mut chars = n.chars();
    let first_ok = chars.next().is_some_and(|c| c.is_ascii_alphanumeric());
    let rest_ok = chars.all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '-'));
    if n.is_empty() {
        Some("Choose a folder name.")
    } else if !first_ok || !rest_ok || n.len() > 100 {
        Some("Use letters, digits, . _ or - (not starting with . or -).")
    } else {
        None
    }
}

/// The typed delete sentence: the words in any case and spacing, the name exactly
/// (the page's and the server's rule).
pub fn phrase_ok(typed: &str, phrase: &str) -> bool {
    let got: Vec<&str> = typed.split_whitespace().collect();
    let want: Vec<&str> = phrase.split(' ').collect();
    got.len() == want.len()
        && got.last() == want.last()
        && got
            .iter()
            .zip(&want)
            .take(want.len().saturating_sub(1))
            .all(|(a, b)| a.eq_ignore_ascii_case(b))
}

/// A free folder name for a clone: name, name-2, name-3…
pub fn free_name(base: &str, taken: &[String]) -> String {
    let base: String = base
        .trim_start_matches(['.', '-'])
        .chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '-') {
                c
            } else {
                '-'
            }
        })
        .take(96)
        .collect();
    let mut name = base.clone();
    let mut i = 2;
    while taken.contains(&name) && i < 1000 {
        name = format!("{base}-{i}");
        i += 1;
    }
    name
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Tab {
    Live,
    Archived,
    Deleted,
}

enum View {
    List,
    New(Form),
    Repos(Table),
    CloneForm(String, Form),
    Cloning {
        id: String,
        full_name: String,
        name: String,
        progress: String,
        switch: bool,
    },
    Archive(String, Confirm),
    Delete(Value, Form),
}

pub struct Projects {
    table: Table,
    tab: Tab,
    overview: Value,
    archived: Value,
    settings: Value,
    repos: Value,
    /// "Where its files are", per project, when asked for (l).
    locations: Option<(String, Value)>,
    view: View,
    msg: Option<(bool, String)>,
    goto: Option<(&'static str, String)>,
    /// A project's git window (Push, Update, Status, Identity, manual lines).
    git: Option<super::git::GitWindow>,
}

impl Default for Projects {
    fn default() -> Projects {
        Projects {
            table: Table::new(&[]),
            tab: Tab::Live,
            overview: Value::Null,
            archived: Value::Null,
            settings: Value::Null,
            repos: Value::Null,
            locations: None,
            view: View::List,
            msg: None,
            goto: None,
            git: None,
        }
    }
}

fn list(v: &Value) -> impl Iterator<Item = &Value> {
    v.as_array().into_iter().flatten()
}

impl Projects {
    fn names(&self) -> Vec<String> {
        list(&self.overview)
            .filter(|p| p.get("exists").and_then(Value::as_bool) == Some(true))
            .map(|p| s(p, "project"))
            .collect()
    }

    fn rows(&mut self) {
        match self.tab {
            Tab::Live => {
                self.table.columns = [
                    ("Project", 0),
                    ("This month", 11),
                    ("All time", 10),
                    ("Chats", 6),
                    ("Budget", 18),
                    ("Last activity", 14),
                ]
                .iter()
                .map(|(t, w)| (t.to_string(), *w))
                .collect();
                self.table.empty = "No projects yet: n creates one (or set the Workspace folder in Scope).".into();
                let rows = list(&self.overview)
                    .filter(|p| {
                        p.get("exists").and_then(Value::as_bool) == Some(true)
                            || (p.get("archived").and_then(Value::as_bool) != Some(true)
                                && p.get("deleted").is_none_or(Value::is_null))
                    })
                    .map(|p| {
                        let name = s(p, "project");
                        let exists = p.get("exists").and_then(Value::as_bool) == Some(true);
                        let active = p.get("active").and_then(Value::as_bool) == Some(true);
                        let label = if active {
                            format!("{name}  ● active")
                        } else if !exists {
                            format!("{name}  (folder missing)")
                        } else {
                            name.clone()
                        };
                        let budget = match p.get("budget").filter(|b| !b.is_null()) {
                            Some(b) => format!(
                                "{} · {}",
                                fmt::cost(num(b, "monthly_usd").unwrap_or(0.0)),
                                fmt::percent(num(b, "ratio").unwrap_or(0.0))
                            ),
                            None => "none".into(),
                        };
                        let style = if active {
                            Style::new().fg(Color::LightCyan)
                        } else if exists {
                            Style::new()
                        } else {
                            DIM
                        };
                        Row::new(name)
                            .styled(label, style)
                            .cell(fmt::cost(num(p, "month_cost").unwrap_or(0.0)))
                            .cell(fmt::cost(num(p, "all_time_cost").unwrap_or(0.0)))
                            .cell(format!("{:.0}", num(p, "chats").unwrap_or(0.0)))
                            .styled(budget, DIM)
                            .styled(num(p, "last_activity").map(ago).unwrap_or_else(|| "–".into()), DIM)
                    })
                    .collect();
                self.table.set_rows(rows);
            }
            Tab::Archived => {
                self.table.columns = [
                    ("Archived project", 0),
                    ("Archived", 18),
                    ("Chats", 6),
                    ("Cost", 10),
                    ("GitHub", 30),
                ]
                .iter()
                .map(|(t, w)| (t.to_string(), *w))
                .collect();
                self.table.empty = "No archived projects.".into();
                let rows = list(&self.archived)
                    .map(|a| {
                        Row::new(s(a, "folder"))
                            .cell(format!("{}  (.archive/{})", s(a, "name"), s(a, "folder")))
                            .styled(num(a, "archived").map(ago).unwrap_or_default(), DIM)
                            .cell(format!(
                                "{:.0}",
                                a.pointer("/records/chats").and_then(Value::as_f64).unwrap_or(0.0)
                            ))
                            .cell(fmt::cost(
                                a.pointer("/records/cost").and_then(Value::as_f64).unwrap_or(0.0),
                            ))
                            .styled(s(a, "remote"), DIM)
                    })
                    .collect();
                self.table.set_rows(rows);
            }
            Tab::Deleted => {
                self.table.columns = [("Deleted project", 0), ("Deleted", 14), ("Chats", 6), ("All time", 10)]
                    .iter()
                    .map(|(t, w)| (t.to_string(), *w))
                    .collect();
                self.table.empty = "No deleted projects with records kept.".into();
                let rows = list(&self.overview)
                    .filter(|p| {
                        p.get("exists").and_then(Value::as_bool) != Some(true)
                            && p.get("deleted").is_some_and(|d| !d.is_null())
                    })
                    .map(|p| {
                        Row::new(s(p, "project"))
                            .styled(s(p, "project"), DIM)
                            .styled(num(p, "deleted").map(ago).unwrap_or_default(), DIM)
                            .cell(format!("{:.0}", num(p, "chats").unwrap_or(0.0)))
                            .cell(fmt::cost(num(p, "all_time_cost").unwrap_or(0.0)))
                    })
                    .collect();
                self.table.set_rows(rows);
            }
        }
    }

    fn new_form(&self) -> Form {
        let kinds = KINDS
            .iter()
            .map(|(id, label, folders)| {
                (
                    id.to_string(),
                    if folders.is_empty() {
                        label.to_string()
                    } else {
                        format!("{label} ({folders})")
                    },
                )
            })
            .collect();
        Form::new(
            "New project",
            vec![
                Field::new("name", "Folder name", Kind::Text)
                    .required()
                    .help("Letters, digits, . _ or -; a folder in the Workspace"),
                Field::new("template", "Kind", Kind::Choice(kinds)).value("basic"),
                Field::new("git", "Git repository", Kind::Toggle).value("1"),
                Field::new(
                    "github",
                    "GitHub repository",
                    Kind::Choice(vec![
                        (String::new(), "none".into()),
                        ("private".into(), "private".into()),
                        ("public".into(), "public".into()),
                    ]),
                )
                .help("Needs a GitHub token (Dashboard → GitHub) and the git repository"),
                Field::new("description", "Description", Kind::Text)
                    .help("Optional: for GitHub and the project's first memory"),
                Field::new("switch", "Make it active", Kind::Toggle).value("1"),
            ],
        )
        .intro(format!(
            "In {}. No model is involved.",
            s(&self.settings, "projects_root")
        ))
        .submit_label("Create")
    }

    fn repos_table(&self) -> Table {
        let mut t = Table::new(&[("Repository", 0), ("", 9), ("Updated", 12), ("In your Workspace", 20)]);
        t.empty = "Loading your repositories…".into();
        let rows = list(self.repos.get("repos").unwrap_or(&Value::Null))
            .map(|r| {
                let private = if r.get("private").and_then(Value::as_bool) == Some(true) {
                    "private"
                } else {
                    "public"
                };
                let mut name = s(r, "full_name");
                if !s(r, "description").is_empty() {
                    name = format!("{name} · {}", s(r, "description"));
                }
                Row::new(s(r, "full_name"))
                    .cell(name)
                    .styled(private, DIM)
                    .styled(s(r, "updated_at").chars().take(10).collect::<String>(), DIM)
                    .styled(s(r, "local"), Style::new().fg(Color::Green))
            })
            .collect();
        t.set_rows(rows);
        if self.repos.get("repos").is_some() {
            t.empty = "No repositories.".into();
        }
        t
    }

    fn chosen(&self) -> Option<String> {
        self.table.chosen().map(|r| r.id.clone())
    }

    fn reload(&self, ctx: &Ctx) {
        ctx.call("overview", Method::Get, "/api/projects/overview", None);
        ctx.call("archived", Method::Get, "/api/projects/archived", None);
    }

    fn list_key(&mut self, k: KeyEvent, ctx: &Ctx) -> bool {
        if self.table.key(k) {
            return true;
        }
        if k.modifiers.contains(KeyModifiers::CONTROL) {
            return false;
        }
        let chosen = self.chosen();
        let live = self.tab == Tab::Live;
        let exists = |n: &str| self.names().iter().any(|x| x == n);
        match (k.code, chosen) {
            (KeyCode::Char('v'), _) => {
                self.tab = match self.tab {
                    Tab::Live => Tab::Archived,
                    Tab::Archived => Tab::Deleted,
                    Tab::Deleted => Tab::Live,
                };
                self.table.selected = 0;
                self.table.filter.clear();
                self.rows();
            }
            (KeyCode::Char('r'), _) => self.load(ctx),
            (KeyCode::Char('n'), _) if live => {
                if s(&self.settings, "projects_root").is_empty() {
                    self.msg = Some((false, "Set the Workspace folder first (Dashboard → Scope).".into()));
                } else {
                    self.view = View::New(self.new_form());
                }
            }
            (KeyCode::Char('c'), _) if live => {
                self.repos = Value::Null;
                ctx.call("repos", Method::Get, "/api/github/repos", None);
                self.view = View::Repos(self.repos_table());
            }
            (KeyCode::Enter | KeyCode::Char('e'), Some(p)) if live && exists(&p) => self.goto = Some(("Project", p)),
            (KeyCode::Char('h'), Some(p)) => self.goto = Some(("History", p)),
            (KeyCode::Char(c @ ('P' | 'U' | 'S' | 'I' | 'M')), Some(p)) if live && exists(&p) => {
                use super::git::{GitWindow, Kind as G};
                let kind = match c {
                    'P' => G::Push,
                    'U' => G::Update,
                    'S' => G::Status,
                    'I' => G::Identity,
                    _ => G::Commands,
                };
                self.git = Some(GitWindow::open(kind, &p, ctx));
            }
            (KeyCode::Char('a'), Some(p)) if live && exists(&p) => {
                self.msg = Some((true, format!("Making {p} active…")));
                ctx.save_settings(format!("active:{p}"), json!({"active_project": p}));
            }
            (KeyCode::Char('l'), Some(p)) if live => {
                self.locations = Some((p.clone(), Value::Null));
                ctx.call(
                    format!("locations:{p}"),
                    Method::Get,
                    format!("/api/projects/{}/locations", enc(&p)),
                    None,
                );
            }
            (KeyCode::Char('A'), Some(p)) if live && exists(&p) => {
                self.view = View::Archive(
                    p.clone(),
                    Confirm::new(
                        format!("Archive {p}?"),
                        "Its folder moves to the Workspace's .archive folder. Its chats, costs and memory are kept, and you can restore it from here (v: Archived).",
                        "archive it",
                    ),
                );
            }
            (KeyCode::Char('u'), Some(folder)) if self.tab == Tab::Archived => {
                self.msg = Some((true, format!("Restoring {folder}…")));
                ctx.call(
                    format!("restored:{folder}"),
                    Method::Post,
                    format!("/api/projects/archived/{}/restore", enc(&folder)),
                    None,
                );
            }
            (KeyCode::Char('D'), Some(folder)) if self.tab == Tab::Archived => {
                let Some(a) = list(&self.archived).find(|a| s(a, "folder") == folder).cloned() else {
                    return true;
                };
                let phrase = s(&a, "phrase");
                let mut f = Form::new(
                    format!("Delete {} permanently?", s(&a, "name")),
                    vec![
                        Field::new("phrase", "Type", Kind::Text).required().help(format!("To delete it, type: {phrase}")),
                        Field::new("keep", "Keep its records", Kind::Toggle)
                            .value("1")
                            .help("On: its chats, memory, costs and statistics stay, marked as deleted. Off: everything recorded about it goes too."),
                    ],
                )
                .intro(format!(
                    "The folder .archive/{folder} is removed from disk; this can't be undone. Optimux never deletes GitHub repositories."
                ))
                .submit_label("Delete permanently");
                if !s(&a, "remote").is_empty() {
                    f.intro.push_str(" Checking its GitHub repository…");
                    ctx.call(
                        format!("gh:{folder}"),
                        Method::Get,
                        format!("/api/projects/archived/{}/github", enc(&folder)),
                        None,
                    );
                }
                self.view = View::Delete(a, f);
            }
            _ => return false,
        }
        true
    }
}

/// "3 h ago" from a Unix time.
pub fn ago(t: f64) -> String {
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0);
    let s = (now - t).max(0.0) as u64;
    match s {
        0..=59 => "just now".into(),
        60..=3599 => format!("{} min ago", s / 60),
        3600..=86399 => format!("{} h ago", s / 3600),
        _ => format!("{} days ago", s / 86400),
    }
}

impl Section for Projects {
    fn load(&mut self, ctx: &Ctx) {
        self.reload(ctx);
        ctx.call("settings", Method::Get, "/api/settings", None);
    }

    fn key(&mut self, k: KeyEvent, ctx: &Ctx) -> bool {
        if let Some(g) = &mut self.git {
            if g.key(k, ctx) {
                let ran = g.kind;
                self.git = None;
                if matches!(ran, super::git::Kind::Push | super::git::Kind::Update) {
                    self.reload(ctx);
                }
            }
            return true;
        }
        let view = std::mem::replace(&mut self.view, View::List);
        let (view, used) = match view {
            View::List => {
                let used = self.list_key(k, ctx);
                (std::mem::replace(&mut self.view, View::List), used)
            }
            View::New(mut f) => match f.key(k) {
                form::Action::Cancel => (View::List, true),
                form::Action::Submit => {
                    let name = f.get("name").trim().to_string();
                    if let Some(p) = name_problem(&name) {
                        f.error = Some(p.into());
                    } else if self.names().contains(&name) {
                        f.error = Some("A project with this name already exists.".into());
                    } else if !f.get("github").is_empty() && !f.on("git") {
                        f.error = Some("A GitHub repository needs the git repository.".into());
                    } else {
                        f.busy = true;
                        let body = json!({
                            "name": name, "template": f.get("template"), "git_init": f.on("git"),
                            "github": f.get("github"), "description": f.get("description").trim(),
                        });
                        let tag = format!("created:{}{name}", if f.on("switch") { "+" } else { "" });
                        ctx.call(tag, Method::Post, "/api/projects", Some(body));
                    }
                    (View::New(f), true)
                }
                form::Action::None => (View::New(f), true),
            },
            View::Repos(mut t) => {
                if t.key(k) {
                    (View::Repos(t), true)
                } else {
                    match k.code {
                        KeyCode::Esc => (View::List, true),
                        KeyCode::Enter => match t.chosen().map(|r| r.id.clone()) {
                            Some(full) => {
                                let base = full.rsplit('/').next().unwrap_or(&full).to_string();
                                let all: Vec<String> = list(&self.overview).map(|p| s(p, "project")).collect();
                                let f = Form::new(
                                    format!("Clone {full}"),
                                    vec![
                                        Field::new("name", "Folder name", Kind::Text).value(free_name(&base, &all)).required(),
                                        Field::new("switch", "Make it active", Kind::Toggle).value("1"),
                                    ],
                                )
                                .intro("Into the Workspace, with git (no model involved). Cloning again makes another copy under a free name.")
                                .submit_label("Clone");
                                (View::CloneForm(full, f), true)
                            }
                            None => (View::Repos(t), true),
                        },
                        _ => (View::Repos(t), true),
                    }
                }
            }
            View::CloneForm(full, mut f) => match f.key(k) {
                form::Action::Cancel => (View::Repos(self.repos_table()), true),
                form::Action::Submit => {
                    let name = f.get("name").trim().to_string();
                    if let Some(p) = name_problem(&name) {
                        f.error = Some(p.into());
                    } else if self.names().contains(&name) {
                        f.error = Some("A project with this name already exists.".into());
                    } else {
                        f.busy = true;
                        let tag = format!("clone:{}{name}", if f.on("switch") { "+" } else { "" });
                        ctx.call(
                            tag,
                            Method::Post,
                            "/api/github/clone",
                            Some(json!({"full_name": full, "name": name})),
                        );
                    }
                    (View::CloneForm(full, f), true)
                }
                form::Action::None => (View::CloneForm(full, f), true),
            },
            View::Cloning {
                id,
                full_name,
                name,
                progress,
                switch,
            } => {
                if matches!(k.code, KeyCode::Esc | KeyCode::Char('x')) {
                    ctx.call(
                        "clone_cancel",
                        Method::Delete,
                        format!("/api/github/clone/{}", enc(&id)),
                        None,
                    );
                }
                (
                    View::Cloning {
                        id,
                        full_name,
                        name,
                        progress,
                        switch,
                    },
                    true,
                )
            }
            View::Archive(p, mut c) => match c.key(k) {
                confirm::Answer::Yes => {
                    self.msg = Some((true, format!("Archiving {p}…")));
                    ctx.call(
                        format!("archived_one:{p}"),
                        Method::Post,
                        format!("/api/projects/{}/archive", enc(&p)),
                        None,
                    );
                    (View::List, true)
                }
                confirm::Answer::No => (View::List, true),
                confirm::Answer::Waiting => (View::Archive(p, c), true),
            },
            View::Delete(a, mut f) => match f.key(k) {
                form::Action::Cancel => (View::List, true),
                form::Action::Submit => {
                    if !phrase_ok(f.get("phrase"), &s(&a, "phrase")) {
                        f.error = Some(format!("Type exactly: {}", s(&a, "phrase")));
                    } else {
                        f.busy = true;
                        let folder = s(&a, "folder");
                        ctx.call(
                            format!("deleted_one:{}", s(&a, "name")),
                            Method::Post,
                            format!("/api/projects/archived/{}/delete", enc(&folder)),
                            Some(json!({"confirm": f.get("phrase").trim(), "keep_records": f.on("keep")})),
                        );
                    }
                    (View::Delete(a, f), true)
                }
                form::Action::None => (View::Delete(a, f), true),
            },
        };
        self.view = view;
        used
    }

    fn paste(&mut self, text: &str) {
        match &mut self.view {
            View::New(f) | View::CloneForm(_, f) | View::Delete(_, f) => f.paste(text),
            _ => {}
        }
    }

    fn on_answer(&mut self, a: Answer, ctx: &Ctx) {
        if a.tag.starts_with("git:") {
            if let Some(g) = &mut self.git {
                g.on_answer(a);
            }
            return;
        }
        let (what, arg) = a.parts();
        let (what, arg) = (what.to_string(), arg.to_string());
        let (switch, name) = match arg.strip_prefix('+') {
            Some(n) => (true, n.to_string()),
            None => (false, arg.clone()),
        };
        match (what.as_str(), a.result) {
            ("overview", Ok(v)) => self.overview = v,
            ("archived", Ok(v)) => self.archived = v,
            ("settings", Ok(v)) => self.settings = v,
            ("repos", Ok(v)) => {
                self.repos = v;
                if let View::Repos(_) = self.view {
                    self.view = View::Repos(self.repos_table());
                }
            }
            ("locations", Ok(v)) => self.locations = Some((arg, v.get("locations").cloned().unwrap_or(Value::Null))),
            ("active", Ok(v)) => {
                self.settings = v;
                let before = match &self.msg {
                    Some((true, m)) if m.starts_with("Created") || m.starts_with("Cloned") => format!("{m} "),
                    _ => String::new(),
                };
                self.msg = Some((true, format!("{before}{arg} is the active project; the chat follows.")));
                self.reload(ctx);
            }
            ("created", Ok(v)) => {
                let ok = v.get("ok").and_then(Value::as_bool) == Some(true);
                let mut text = format!("Created {name}.");
                if !s(&v, "github_url").is_empty() {
                    text.push_str(&format!(" GitHub: {}", s(&v, "github_url")));
                }
                if !ok {
                    text = format!("{name} was created, but not every step worked:\n{}", s(&v, "report"));
                }
                self.msg = Some((ok, text));
                self.view = View::List;
                if switch {
                    ctx.save_settings(format!("active:{name}"), json!({"active_project": name}));
                }
                self.reload(ctx);
            }
            ("clone", Ok(v)) => {
                let id = s(&v, "id");
                self.view = View::Cloning {
                    id: id.clone(),
                    full_name: s(&v, "full_name"),
                    name: name.clone(),
                    progress: s(&v, "progress"),
                    switch,
                };
                ctx.call_later(
                    700,
                    "clone_state",
                    Method::Get,
                    format!("/api/github/clone/{}", enc(&id)),
                );
            }
            ("clone_state", Ok(v)) | ("clone_cancel", Ok(v)) => {
                let View::Cloning { id, switch, name, .. } = &mut self.view else {
                    return;
                };
                let (id, switch, name) = (id.clone(), *switch, name.clone());
                match s(&v, "state").as_str() {
                    "running" => {
                        if let View::Cloning { progress, .. } = &mut self.view {
                            *progress = s(&v, "progress");
                        }
                        if what == "clone_state" {
                            ctx.call_later(
                                700,
                                "clone_state",
                                Method::Get,
                                format!("/api/github/clone/{}", enc(&id)),
                            );
                        }
                    }
                    "done" => {
                        self.msg = Some((true, format!("Cloned {} into {name}.", s(&v, "full_name"))));
                        self.view = View::List;
                        if switch {
                            ctx.save_settings(format!("active:{name}"), json!({"active_project": name}));
                        }
                        self.reload(ctx);
                    }
                    "failed" => {
                        self.msg = Some((
                            false,
                            format!("Couldn't clone {}: {}", s(&v, "full_name"), s(&v, "error")),
                        ));
                        self.view = View::List;
                    }
                    _ => {
                        self.msg = Some((true, "Clone cancelled. Nothing was left behind.".into()));
                        self.view = View::List;
                    }
                }
            }
            ("archived_one", Ok(v)) => {
                self.msg = Some((
                    true,
                    format!(
                        "Archived {arg}: moved to {}. v shows the archived projects.",
                        s(&v, "archived_to")
                    ),
                ));
                self.reload(ctx);
            }
            ("restored", Ok(v)) => {
                self.msg = Some((true, format!("Restored: back in {}.", s(&v, "path"))));
                self.reload(ctx);
            }
            ("gh", Ok(v)) => {
                if let View::Delete(_, f) = &mut self.view {
                    let note = match s(&v, "state").as_str() {
                        "exists" => format!(
                            " Its GitHub repository {} still exists and stays: delete it on GitHub if you want it gone.",
                            s(&v, "repo")
                        ),
                        "missing" => " Its GitHub repository is gone already.".to_string(),
                        _ => format!(
                            " Couldn't tell whether its GitHub repository exists ({}).",
                            s(&v, "reason")
                        ),
                    };
                    f.intro = f.intro.replace(" Checking its GitHub repository…", &note);
                }
            }
            ("deleted_one", Ok(v)) => {
                let kept = v.get("records_kept").and_then(Value::as_bool) == Some(true);
                self.msg = Some((
                    true,
                    format!(
                        "Deleted {arg}. {}",
                        if kept {
                            "Its chats, memory, costs and statistics are kept, marked as deleted."
                        } else {
                            "Everything recorded about it is gone too."
                        }
                    ),
                ));
                self.view = View::List;
                self.reload(ctx);
            }
            (_, Err(e)) => match &mut self.view {
                View::New(f) | View::CloneForm(_, f) | View::Delete(_, f) => {
                    f.busy = false;
                    f.error = Some(e);
                }
                View::Repos(t) => {
                    t.empty = e;
                }
                View::Cloning { .. } if what == "clone_state" => {
                    self.msg = Some((false, "Optimux lost track of this clone (it restarted, or you were signed out). Nothing was left behind; try again.".into()));
                    self.view = View::List;
                }
                _ => self.msg = Some((false, e)),
            },
            _ => return,
        }
        self.rows();
    }

    fn keys(&self) -> String {
        if let Some(g) = &self.git {
            return g.keys();
        }
        match (&self.view, self.tab) {
            (View::List, Tab::Live) => {
                "Enter page · h chats · a active · n new · c clone · A archive · l files · P push · U update · S status · I identity · M manual · v archived".into()
            }
            (View::List, Tab::Archived) => "u restore · D delete permanently · v deleted · r refresh · Esc menu".into(),
            (View::List, Tab::Deleted) => "h its chats · v projects · r refresh · Esc menu".into(),
            (View::Repos(_), _) => "Enter clone it · / find · Esc back".into(),
            (View::Cloning { .. }, _) => "Esc cancel the clone".into(),
            _ => String::new(),
        }
    }

    fn draw(&mut self, f: &mut Frame, area: Rect) -> Vec<(u16, usize)> {
        let width = area.width as usize;
        let extra = match self.tab {
            Tab::Live => format!("  ·  in {}", s(&self.settings, "projects_root")),
            Tab::Archived => "  ·  archived (in the Workspace's .archive folder)".into(),
            Tab::Deleted => "  ·  deleted: folders gone, chats and costs kept".into(),
        };
        let body = draw_head(f, area, head_lines("Projects", &extra, &self.msg, width));
        if let Some(g) = &mut self.git {
            g.draw(f, body);
            return Vec::new();
        }
        let bold = Style::new().fg(Color::LightCyan).add_modifier(Modifier::BOLD);
        match &self.view {
            View::List => {
                let table_h = (self.table.visible().len() as u16 + 3)
                    .clamp(4, body.height / 2 + 3)
                    .min(body.height);
                let placed = self.table.draw(
                    f,
                    Rect {
                        height: table_h,
                        ..body
                    },
                );
                let mut lines = Vec::new();
                if let (Tab::Live, Some(p)) = (self.tab, self.chosen()) {
                    let root = s(&self.settings, "projects_root");
                    lines.push(Line::from(Span::styled(
                        p.clone(),
                        Style::new().add_modifier(Modifier::BOLD),
                    )));
                    if !root.is_empty() {
                        lines.push(Line::from(Span::styled(
                            format!("{}/{p}", root.trim_end_matches('/')),
                            DIM,
                        )));
                    }
                    match &self.locations {
                        Some((lp, locs)) if *lp == p && locs.is_array() => {
                            lines.push(Line::default());
                            for l in list(locs) {
                                let mut what = s(l, "what");
                                if l.get("exists").and_then(Value::as_bool) == Some(false) {
                                    what.push_str(" · not created yet");
                                }
                                if l.get("shared").and_then(Value::as_bool) == Some(true) {
                                    what.push_str(" · shared with other projects");
                                }
                                lines.push(Line::from(vec![
                                    Span::styled(format!("{:<26}", s(l, "label")), Style::new().fg(Color::Gray)),
                                    Span::raw(s(l, "path")),
                                ]));
                                lines.extend(wrap(
                                    vec![Span::styled(what, DIM)],
                                    width,
                                    &" ".repeat(26),
                                    &" ".repeat(26),
                                ));
                            }
                        }
                        Some((lp, _)) if *lp == p => lines.push(Line::from(Span::styled("Looking…", DIM))),
                        _ => lines.push(Line::from(Span::styled("l: where its files are", DIM))),
                    }
                }
                let rest = Rect {
                    y: body.y + table_h,
                    height: body.height.saturating_sub(table_h),
                    ..body
                };
                f.render_widget(Paragraph::new(lines), rest);
                placed
            }
            View::New(form) | View::CloneForm(_, form) | View::Delete(_, form) => {
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
            View::Repos(t) => {
                let mut lines = vec![Line::from(Span::styled("Clone from GitHub", bold))];
                if self.repos.get("truncated").and_then(Value::as_bool) == Some(true) {
                    lines.push(Line::from(Span::styled(
                        "Only the first repositories are listed: / finds by name.",
                        DIM,
                    )));
                }
                let h = lines.len() as u16 + 1;
                f.render_widget(Paragraph::new(lines), Rect { height: h, ..body });
                t.draw(
                    f,
                    Rect {
                        y: body.y + h,
                        height: body.height.saturating_sub(h),
                        ..body
                    },
                )
            }
            View::Cloning {
                full_name,
                name,
                progress,
                ..
            } => {
                let lines = vec![
                    Line::from(Span::styled(format!("Cloning {full_name} into {name}…"), bold)),
                    Line::default(),
                    Line::from(Span::styled(progress.clone(), DIM)),
                ];
                f.render_widget(Paragraph::new(lines), body);
                Vec::new()
            }
            View::Archive(_, c) => {
                let mut lines = vec![Line::from(Span::styled(c.title.clone(), bold)), Line::default()];
                lines.extend(c.lines(width));
                f.render_widget(Paragraph::new(lines), body);
                Vec::new()
            }
        }
    }

    fn tabs(&self) -> Vec<Vec<String>> {
        self.git.as_ref().map(|w| w.tabs()).unwrap_or_default()
    }

    fn click_tab(&mut self, _row: usize, i: usize, ctx: &Ctx) {
        if let Some(w) = &mut self.git {
            w.click_tab(i, ctx);
        }
    }

    fn has_window(&self) -> bool {
        self.git.is_some() || !matches!(self.view, View::List) || self.table.filtering
    }

    fn click_row(&mut self, i: usize, _ctx: &Ctx) -> bool {
        match &mut self.view {
            View::List => self.table.selected = i.min(self.table.visible().len().saturating_sub(1)),
            View::Repos(t) => t.selected = i.min(t.visible().len().saturating_sub(1)),
            _ => {}
        }
        false
    }

    fn take_goto(&mut self) -> Option<(&'static str, String)> {
        self.goto.take()
    }

    fn take_edit(&mut self) -> Option<crate::ui::external::Edit> {
        self.git.as_mut().and_then(|g| g.take_edit())
    }

    fn edited(&mut self, result: Result<Option<String>, String>, _ctx: &Ctx) {
        if let Some(g) = &mut self.git {
            g.edited(result);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn folder_names_as_the_page() {
        assert_eq!(name_problem("my-app.2"), None);
        assert!(name_problem("").is_some());
        assert!(name_problem("-x").is_some());
        assert!(name_problem("a b").is_some());
        assert!(name_problem(&"a".repeat(101)).is_some());
    }

    #[test]
    fn the_delete_sentence_as_the_server() {
        let want = "yes I want to delete demo";
        assert!(phrase_ok("YES i want   to delete demo", want));
        assert!(!phrase_ok("yes I want to delete Demo", want), "the name exactly");
        assert!(!phrase_ok("yes I want to delete", want));
    }

    #[test]
    fn a_clone_gets_a_free_name() {
        let taken = vec!["app".to_string(), "app-2".to_string()];
        assert_eq!(free_name("app", &taken), "app-3");
        assert_eq!(free_name(".my repo", &taken), "my-repo");
    }
}
