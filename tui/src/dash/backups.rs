//! Dashboard → Backups, as in the web page. Snapshots kept on this machine: take one
//! (everything, data only, or projects), see what goes in first, read its details and
//! README, restore parts of it (a typed sentence), download it encrypted to the
//! computer optimux-tui runs on, delete it, set the space limit. Daily database backups:
//! back up now, keep, check, restore, download, delete. Restore from a downloaded file:
//! upload it from here. And the restore waiting for a restart.

use std::path::{Path, PathBuf};

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use ratatui::Frame;
use ratatui::layout::Rect;
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::Paragraph;
use serde_json::{Value, json};

use super::{Answer, Ctx, Section, draw_head, head_lines};
use crate::api::{Method, enc};
use crate::chat::text::{DIM, wrap};
use crate::cli::{num, s};
use crate::dash::projects::ago;
use crate::ui::confirm::{self, Confirm};
use crate::ui::form::{self, Field, Form, Kind};
use crate::ui::table::{Row, Table};

pub const KINDS: &[(&str, &str, &str)] = &[
    (
        "full",
        "Everything",
        "Your data and all your projects, in one snapshot taken at the same moment (recommended).",
    ),
    (
        "data",
        "Data only",
        "Settings, models and routing, memory, skills, chats and usage, and the keys.",
    ),
    (
        "workspace",
        "Workspace only",
        "Your project folders (all or some), each with its project memory.",
    ),
];

pub fn kind_label(k: &str) -> &'static str {
    KINDS
        .iter()
        .find(|(id, ..)| *id == k)
        .map(|(_, l, _)| *l)
        .unwrap_or("Snapshot")
}

pub fn bytes(n: f64) -> String {
    // in 1024s, as the server counts its limit
    let (k, m, g) = (1024.0, 1024.0 * 1024.0, 1024.0 * 1024.0 * 1024.0);
    if n >= g {
        format!("{:.1} GB", n / g)
    } else if n >= m {
        format!("{:.1} MB", n / m)
    } else if n >= k {
        format!("{:.0} kB", n / k)
    } else {
        format!("{n:.0} B")
    }
}

/// The age command that opens a download (the page's dlCommand).
pub fn age_command(mode: &str, recipient: &str, key_file: &str, filename: &str) -> String {
    let key = if mode == "key" {
        key_file.to_string()
    } else if recipient.contains("ssh-ed25519") {
        "~/.ssh/id_ed25519".into()
    } else if recipient.contains("ssh-rsa") {
        "~/.ssh/id_rsa".into()
    } else {
        "<your-age-key-file>".into()
    };
    let out = filename.trim_end_matches(".age");
    format!("age -d -i {key} -o {out} {filename}")
}

/// A file only you can read, never replacing one (a one-time key).
pub fn write_private_new(path: &Path, text: &str) -> std::io::Result<()> {
    use std::io::Write;
    let mut o = std::fs::OpenOptions::new();
    o.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        o.mode(0o600);
    }
    o.open(path)?.write_all(text.as_bytes())
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Tab {
    Snapshots,
    Daily,
    File,
}

/// What is downloaded: a snapshot (its id) or a daily backup (its file name).
#[derive(Clone)]
enum Item {
    Snapshot(String),
    Daily(String),
}

impl Item {
    fn id(&self) -> String {
        match self {
            Item::Snapshot(id) => id.clone(),
            Item::Daily(n) => format!("daily-{n}"),
        }
    }
}

enum View {
    List,
    Take(Form),
    Preview {
        body: Value,
        preview: Value,
    },
    Details(Value, Option<String>),
    Restore {
        sid: String,
        info: Value,
        form: Form,
    },
    DailyRestore(String, Confirm),
    Delete(Item, Confirm),
    Download(Item, Form),
    Downloading(Item),
    Downloaded {
        item: Item,
        result: Value,
        path: String,
        key_saved: Option<String>,
    },
    Limit(Form),
    Keep(Form),
    Upload(Form),
    UploadKey {
        token: String,
        filename: String,
        form: Form,
    },
}

pub struct Backups {
    tab: Tab,
    snaps: Value,
    daily: Value,
    status: Value,
    settings: Value,
    projects: Vec<String>,
    table: Table,
    view: View,
    msg: Option<(bool, String)>,
    polling: bool,
    scroll: usize,
    /// The snapshot asked about in a preview, taken on "y".
    pending_body: Option<Value>,
    /// Where a download goes, and the public key it was encrypted to.
    download_folder: PathBuf,
    download_recipient: String,
    /// The prepared download (its link, name and key) while the file comes.
    export: Option<Value>,
    upload_name: String,
}

impl Default for Backups {
    fn default() -> Backups {
        Backups {
            tab: Tab::Snapshots,
            snaps: Value::Null,
            daily: Value::Null,
            status: Value::Null,
            settings: Value::Null,
            projects: Vec::new(),
            table: Table::new(&[]),
            view: View::List,
            msg: None,
            polling: false,
            scroll: 0,
            pending_body: None,
            download_folder: PathBuf::from("."),
            download_recipient: String::new(),
            export: None,
            upload_name: String::new(),
        }
    }
}

impl Backups {
    fn snapshot(&self, id: &str) -> Option<Value> {
        self.snaps
            .get("snapshots")?
            .as_array()?
            .iter()
            .find(|x| s(x, "id") == id)
            .cloned()
    }

    fn daily_backup(&self, name: &str) -> Option<Value> {
        self.daily
            .get("backups")?
            .as_array()?
            .iter()
            .find(|x| s(x, "name") == name)
            .cloned()
    }

    fn rows(&mut self) {
        match self.tab {
            Tab::Snapshots => {
                self.table.columns = [("Taken", 18), ("What", 0), ("Note", 26), ("Size", 14)]
                    .iter()
                    .map(|(t, w)| (t.to_string(), *w))
                    .collect();
                self.table.empty = "No snapshots yet: n takes one.".into();
                let rows = self
                    .snaps
                    .get("snapshots")
                    .and_then(Value::as_array)
                    .into_iter()
                    .flatten()
                    .map(|x| {
                        let mut what = kind_label(&s(x, "kind")).to_string();
                        if s(x, "kind") != "data" {
                            if let Some(p) = x.get("projects").and_then(Value::as_array) {
                                let names: Vec<&str> = p.iter().filter_map(Value::as_str).collect();
                                what.push_str(&if !names.is_empty() && names.len() <= 3 {
                                    format!(" ({})", names.join(", "))
                                } else {
                                    format!(" ({} projects)", names.len())
                                });
                            }
                        }
                        if x.get("auto").and_then(Value::as_bool) == Some(true) {
                            what.push_str(" · auto");
                        }
                        if x.get("includes_secrets").and_then(Value::as_bool) == Some(true) {
                            what.push_str(" · keys");
                        }
                        let warn = num(x, "unreadable_count").unwrap_or(0.0) + num(x, "changed_count").unwrap_or(0.0);
                        if warn > 0.0 {
                            what.push_str(&format!(" · {warn:.0} warning(s)"));
                        }
                        let (size, style) = match s(x, "status").as_str() {
                            "running" => ("taking…".to_string(), Style::new().fg(Color::LightCyan)),
                            "failed" => (format!("failed: {}", s(x, "error")), Style::new().fg(Color::Red)),
                            _ => (bytes(num(x, "size_bytes").unwrap_or(0.0)), DIM),
                        };
                        Row::new(s(x, "id"))
                            .styled(num(x, "created").map(ago).unwrap_or_default(), DIM)
                            .cell(what)
                            .styled(s(x, "note"), DIM)
                            .styled(size, style)
                    })
                    .collect();
                self.table.set_rows(rows);
            }
            Tab::Daily => {
                self.table.columns = [("Day", 12), ("Written", 14), ("Check", 0), ("Size", 10)]
                    .iter()
                    .map(|(t, w)| (t.to_string(), *w))
                    .collect();
                self.table.empty = "No daily backup yet: b backs up now (or restart Optimux).".into();
                let rows = self
                    .daily
                    .get("backups")
                    .and_then(Value::as_array)
                    .into_iter()
                    .flatten()
                    .map(|b| {
                        let (check, style) = match b.get("checked").and_then(Value::as_bool) {
                            Some(true) => ("✓ checked".to_string(), Style::new().fg(Color::Green)),
                            Some(false) => (format!("✕ damaged: {}", s(b, "error")), Style::new().fg(Color::Red)),
                            None => ("not checked: c checks it".to_string(), Style::new().fg(Color::Yellow)),
                        };
                        Row::new(s(b, "name"))
                            .cell(s(b, "date"))
                            .styled(num(b, "mtime").map(ago).unwrap_or_default(), DIM)
                            .styled(check, style)
                            .styled(bytes(num(b, "size_bytes").unwrap_or(0.0)), DIM)
                    })
                    .collect();
                self.table.set_rows(rows);
            }
            Tab::File => self.table.set_rows(Vec::new()),
        }
    }

    fn reload(&self, ctx: &Ctx) {
        ctx.call("snaps", Method::Get, "/api/snapshots", None);
        ctx.call("daily", Method::Get, "/api/backups", None);
        ctx.call("status", Method::Get, "/api/restore/status", None);
    }

    fn take_form(&self) -> Form {
        let kinds = KINDS.iter().map(|(id, l, _)| (id.to_string(), l.to_string())).collect();
        let mut f = Form::new(
            "Take a snapshot",
            vec![
                Field::new("kind", "What to save", Kind::Choice(kinds)).value("full").help(KINDS[0].2),
                Field::new("projects", "Projects", Kind::Text)
                    .value(self.projects.join(", "))
                    .help("For Workspace only: the projects, separated by commas (all of them now)"),
                Field::new("skip", "Skip rebuildable folders", Kind::Toggle).value("1").help("node_modules, .venv, __pycache__, …: npm install or pip install bring them back"),
                Field::new("note", "Note", Kind::Text).help("Optional, like: before testing a new routing"),
            ],
        )
        .intro("A copy kept on this machine, to come back to later. Nothing is sent anywhere. It is not a real backup: if the disk fails, the snapshots go with it.")
        .submit_label("Show what goes in");
        f.focus = 0;
        f
    }

    fn download_form(&self, item: &Item) -> Form {
        let snap = match item {
            Item::Snapshot(id) => self.snapshot(id),
            Item::Daily(_) => None,
        };
        let mut modes = vec![
            (
                "key".to_string(),
                "Encrypt with a one-time key (recommended)".to_string(),
            ),
            (
                "recipient".to_string(),
                "Encrypt to my public key (age1… or ssh-ed25519 …)".to_string(),
            ),
        ];
        if let Some(x) = &snap {
            if x.get("includes_secrets").and_then(Value::as_bool) != Some(true) && s(x, "kind") == "workspace" {
                modes.push((
                    "plain".into(),
                    "No encryption (anyone with the file can read your code)".into(),
                ));
            }
        }
        let here = std::env::current_dir()
            .map(|d| d.display().to_string())
            .unwrap_or_else(|_| ".".into());
        let mut f = Form::new(
            "Download",
            vec![
                Field::new("mode", "Protect it", Kind::Choice(modes)).value("key"),
                Field::new("recipient", "Public key", Kind::Text)
                    .value(s(&self.settings, "snapshot_recipient"))
                    .help("For Encrypt to my public key: from the computer you'll open it on (cat ~/.ssh/id_ed25519.pub). Only the public part, never the private key."),
                Field::new("remember", "Remember the public key", Kind::Toggle),
                Field::new("folder", "Save in folder", Kind::Text).value(here).required().help("On the computer optimux-tui runs on; an existing file is never replaced"),
            ],
        )
        .intro(match item {
            Item::Daily(_) => "It holds your chats, so it can only leave this server encrypted. Open it with age; the result is an SQLite file.".to_string(),
            Item::Snapshot(_) if snap.as_ref().and_then(|x| x.get("includes_secrets")).and_then(Value::as_bool) == Some(true) => {
                "It holds your API keys and sign-in data, so it can only leave this server encrypted.".to_string()
            }
            _ => String::new(),
        })
        .submit_label("Prepare and download");
        f.focus = 0;
        f
    }

    fn list_key(&mut self, k: KeyEvent, ctx: &Ctx) -> bool {
        if self.tab != Tab::File && self.table.key(k) {
            return true;
        }
        if k.modifiers.contains(KeyModifiers::CONTROL) {
            return false;
        }
        let chosen = self.table.chosen().map(|r| r.id.clone());
        let pending = self.status.get("pending").is_some_and(|p| !p.is_null());
        match (k.code, self.tab, chosen) {
            (KeyCode::Char('1'), ..) => self.tab = Tab::Snapshots,
            (KeyCode::Char('2'), ..) => self.tab = Tab::Daily,
            (KeyCode::Char('3'), ..) => {
                self.tab = Tab::File;
                let mut f = Form::new(
                    "Restore from a downloaded file",
                    vec![Field::new("file", "File", Kind::Text).required().help("optimux-snapshot-….tar.gz.age (or .tar.gz), on this computer")],
                )
                .intro("It is sent to Optimux, checked completely (structure and every checksum) and added to the snapshot list; nothing is restored until you choose the parts and confirm.")
                .submit_label("Upload and check");
                f.focus = 0;
                self.view = View::Upload(f);
            }
            (KeyCode::Char('r'), ..) => self.reload(ctx),
            (KeyCode::Char('!'), ..) if pending => {
                self.msg = Some((true, "Asking Optimux to restart…".into()));
                ctx.call("restart", Method::Post, "/api/restore/restart", Some(json!({})));
            }
            (KeyCode::Char('X'), ..) if pending => {
                ctx.call("cancelled", Method::Post, "/api/restore/cancel", Some(json!({})))
            }
            (KeyCode::Char('d'), ..) if self.status.get("last").is_some_and(|l| !l.is_null()) => {
                ctx.call("dismissed", Method::Post, "/api/restore/dismiss", Some(json!({})))
            }
            (KeyCode::Char('n'), Tab::Snapshots, _) => self.view = View::Take(self.take_form()),
            (KeyCode::Char('L'), Tab::Snapshots, _) => {
                let gb = num(&self.snaps, "limit_bytes")
                    .map(|b| format!("{}", (b / (1u64 << 30) as f64 * 10.0).round() / 10.0))
                    .unwrap_or_default();
                let mut f = Form::new(
                    "Space for snapshots",
                    vec![Field::new("gb", "Limit (GB)", Kind::Number).value(gb).required().help(
                        "When a new snapshot would pass it, automatic snapshots are removed first, oldest first. Yours are never removed without asking.",
                    )],
                )
                .submit_label("Save");
                f.focus = 0;
                self.view = View::Limit(f);
            }
            (KeyCode::Enter, Tab::Snapshots, Some(id)) => {
                if let Some(x) = self.snapshot(&id) {
                    self.view = View::Details(x, None);
                }
            }
            (KeyCode::Char('R'), Tab::Snapshots, Some(id)) => {
                if pending {
                    self.msg = Some((
                        false,
                        "Another restore is waiting for the restart: ! restarts Optimux, X cancels it.".into(),
                    ));
                } else {
                    self.msg = Some((true, "Reading the snapshot…".into()));
                    ctx.call(
                        format!("inspect:{id}"),
                        Method::Get,
                        format!("/api/snapshots/{}/restore", enc(&id)),
                        None,
                    );
                }
            }
            (KeyCode::Char('D'), Tab::Snapshots, Some(id)) => {
                if self.snapshot(&id).is_some_and(|x| s(&x, "status") == "ready") {
                    let item = Item::Snapshot(id);
                    let f = self.download_form(&item);
                    self.view = View::Download(item, f);
                }
            }
            (KeyCode::Char('x'), Tab::Snapshots, Some(id)) => {
                let label = self.snapshot(&id).map(|x| {
                    let note = s(&x, "note");
                    format!(
                        "{}{}",
                        kind_label(&s(&x, "kind")),
                        if note.is_empty() {
                            String::new()
                        } else {
                            format!(" ({note})")
                        }
                    )
                });
                let c = Confirm::new(
                    "Delete this snapshot?",
                    format!(
                        "The {} snapshot is removed from the server, and this can't be undone.",
                        label.unwrap_or_default()
                    ),
                    "delete it",
                );
                self.view = View::Delete(Item::Snapshot(id), c);
            }
            (KeyCode::Char('b'), Tab::Daily, _) => {
                self.msg = Some((true, "Backing up…".into()));
                ctx.call("backed_up", Method::Post, "/api/backups", Some(json!({})));
            }
            (KeyCode::Char('k'), Tab::Daily, _) => {
                let range = self
                    .daily
                    .get("keep_range")
                    .and_then(Value::as_array)
                    .cloned()
                    .unwrap_or_default();
                let (lo, hi) = (
                    range.first().and_then(Value::as_i64).unwrap_or(3),
                    range.get(1).and_then(Value::as_i64).unwrap_or(60),
                );
                let mut f = Form::new(
                    "Daily backups kept",
                    vec![
                        Field::new("keep", "Keep the newest", Kind::Number)
                            .value(self.daily.get("keep").map(|v| v.to_string()).unwrap_or_default())
                            .required()
                            .help(format!(
                                "From {lo} to {hi}. When there are more, the oldest go at the next backup."
                            )),
                    ],
                )
                .submit_label("Save");
                f.focus = 0;
                self.view = View::Keep(f);
            }
            (KeyCode::Char('c'), Tab::Daily, Some(name)) => ctx.call(
                format!("checked:{name}"),
                Method::Post,
                format!("/api/backups/{}/check", enc(&name)),
                Some(json!({})),
            ),
            (KeyCode::Char('R'), Tab::Daily, Some(name)) => {
                if pending {
                    self.msg = Some((
                        false,
                        "Another restore is waiting for the restart: ! restarts Optimux, X cancels it.".into(),
                    ));
                } else if self
                    .daily_backup(&name)
                    .and_then(|b| b.get("checked").and_then(Value::as_bool))
                    == Some(false)
                {
                    self.msg = Some((false, "This backup is damaged: it can't be restored.".into()));
                } else {
                    let date = self.daily_backup(&name).map(|b| s(&b, "date")).unwrap_or_default();
                    let c = Confirm::new(
                        format!("Restore the database of {date}?"),
                        format!(
                            "Chats and memory history go back to how they were on {date}; chats from after that day leave the list.\nYour current data is saved first, in a new Data snapshot, so this can be undone. The database is put back when Optimux restarts.\nMerged, not rolled back: history from both sides, the newer copy of each chat, your current sign-ins. Settings, keys, memory files and projects don't change."
                        ),
                        "restore it",
                    )
                    .typed(s(&self.status, "confirm_phrase"));
                    self.view = View::DailyRestore(name, c);
                }
            }
            (KeyCode::Char('D'), Tab::Daily, Some(name)) => {
                let item = Item::Daily(name);
                let f = self.download_form(&item);
                self.view = View::Download(item, f);
            }
            (KeyCode::Char('x'), Tab::Daily, Some(name)) => {
                let date = self.daily_backup(&name).map(|b| s(&b, "date")).unwrap_or_default();
                let c = Confirm::new(
                    "Delete this daily backup?",
                    format!("The daily backup of {date} is removed from the server, and this can't be undone."),
                    "delete it",
                );
                self.view = View::Delete(Item::Daily(name), c);
            }
            _ => return false,
        }
        self.rows();
        true
    }
}

impl Section for Backups {
    fn load(&mut self, ctx: &Ctx) {
        self.reload(ctx);
        ctx.call("settings", Method::Get, "/api/settings", None);
        ctx.call("projects", Method::Get, "/api/projects", None);
    }

    fn key(&mut self, k: KeyEvent, ctx: &Ctx) -> bool {
        let view = std::mem::replace(&mut self.view, View::List);
        let (view, used) = match view {
            View::List => {
                let used = self.list_key(k, ctx);
                (std::mem::replace(&mut self.view, View::List), used)
            }
            View::Take(mut f) => {
                let before = f.get("kind").to_string();
                let a = f.key(k);
                if f.get("kind") != before {
                    let help = KINDS
                        .iter()
                        .find(|(id, ..)| *id == f.get("kind"))
                        .map(|(.., h)| h.to_string())
                        .unwrap_or_default();
                    if let Some(field) = f.field_mut("kind") {
                        field.help = help;
                    }
                }
                match a {
                    form::Action::Cancel => (View::List, true),
                    form::Action::Submit => {
                        let projects: Vec<String> = f
                            .get("projects")
                            .split(',')
                            .map(str::trim)
                            .filter(|p| !p.is_empty())
                            .map(str::to_string)
                            .collect();
                        if f.get("kind") == "workspace" && projects.is_empty() {
                            f.error = Some("Choose at least one project.".into());
                            (View::Take(f), true)
                        } else if let Some(p) = projects
                            .iter()
                            .find(|p| !self.projects.contains(p))
                            .filter(|_| f.get("kind") == "workspace")
                        {
                            f.error = Some(format!("No project {p} in the Workspace."));
                            (View::Take(f), true)
                        } else {
                            let body = json!({
                                "kind": f.get("kind"), "note": f.get("note").trim(),
                                "projects": if f.get("kind") == "workspace" { projects } else { vec![] },
                                "skip_rebuildable": f.on("skip"),
                            });
                            f.busy = true;
                            ctx.call("preview", Method::Post, "/api/snapshots/preview", Some(body.clone()));
                            self.view = View::Take(f);
                            // the preview's answer moves on to View::Preview with this body
                            self.pending_body = Some(body);
                            return true;
                        }
                    }
                    form::Action::None => (View::Take(f), true),
                }
            }
            View::Preview { body, preview } => match k.code {
                KeyCode::Char('y') if preview.get("fits").and_then(Value::as_bool) != Some(false) => {
                    self.msg = Some((true, "Taking the snapshot…".into()));
                    ctx.call("taken", Method::Post, "/api/snapshots", Some(body));
                    (View::List, true)
                }
                KeyCode::Char('f') if s(&body, "kind") != "full" => {
                    let mut b = body.clone();
                    b["kind"] = json!("full");
                    b["projects"] = json!([]);
                    ctx.call("preview", Method::Post, "/api/snapshots/preview", Some(b.clone()));
                    self.pending_body = Some(b);
                    (View::Preview { body, preview }, true)
                }
                KeyCode::Esc | KeyCode::Char('n') => (View::List, true),
                _ => (View::Preview { body, preview }, true),
            },
            View::Details(x, readme) => match k.code {
                KeyCode::Esc => (View::List, true),
                KeyCode::Char('m') if readme.is_some() => (View::Details(x, None), true),
                KeyCode::Char('m') => {
                    ctx.call(
                        format!("readme:{}", s(&x, "id")),
                        Method::Get,
                        format!("/api/snapshots/{}/readme", enc(&s(&x, "id"))),
                        None,
                    );
                    (View::Details(x, Some("Reading…".into())), true)
                }
                KeyCode::Char('R') | KeyCode::Char('D') | KeyCode::Char('x') => {
                    self.view = View::List;
                    self.table.selected = self
                        .table
                        .visible()
                        .iter()
                        .position(|r| r.id == s(&x, "id"))
                        .unwrap_or(self.table.selected);
                    let used = self.list_key(k, ctx);
                    (std::mem::replace(&mut self.view, View::List), used)
                }
                KeyCode::Down | KeyCode::PageDown => {
                    self.scroll += if k.code == KeyCode::Down { 1 } else { 10 };
                    (View::Details(x, readme), true)
                }
                KeyCode::Up | KeyCode::PageUp => {
                    self.scroll = self.scroll.saturating_sub(if k.code == KeyCode::Up { 1 } else { 10 });
                    (View::Details(x, readme), true)
                }
                _ => (View::Details(x, readme), true),
            },
            View::Restore { sid, info, mut form } => match form.key(k) {
                form::Action::Cancel => (View::List, true),
                form::Action::Submit => {
                    let parts: Vec<String> = info
                        .get("parts")
                        .and_then(Value::as_array)
                        .into_iter()
                        .flatten()
                        .enumerate()
                        .filter(|(i, _)| form.on(part_key(*i)))
                        .map(|(_, p)| s(p, "path"))
                        .collect();
                    if parts.is_empty() {
                        form.error = Some("Choose at least one part.".into());
                    } else if form.get("phrase").trim() != s(&info, "confirm_phrase") {
                        form.error = Some(format!("Type exactly: {}", s(&info, "confirm_phrase")));
                    } else {
                        form.busy = true;
                        ctx.call(
                            "restored",
                            Method::Post,
                            format!("/api/snapshots/{}/restore", enc(&sid)),
                            Some(json!({"parts": parts, "confirm": form.get("phrase").trim()})),
                        );
                    }
                    (View::Restore { sid, info, form }, true)
                }
                form::Action::None => (View::Restore { sid, info, form }, true),
            },
            View::DailyRestore(name, mut c) => match c.key(k) {
                confirm::Answer::Yes => {
                    let phrase = c.input.trim().to_string();
                    self.msg = Some((true, "Preparing the restore (saving the current state first)…".into()));
                    ctx.call(
                        "restored",
                        Method::Post,
                        format!("/api/backups/{}/restore", enc(&name)),
                        Some(json!({"confirm": phrase})),
                    );
                    (View::List, true)
                }
                confirm::Answer::No => (View::List, true),
                confirm::Answer::Waiting => (View::DailyRestore(name, c), true),
            },
            View::Delete(item, mut c) => match c.key(k) {
                confirm::Answer::Yes => {
                    match &item {
                        Item::Snapshot(id) => ctx.call(
                            "deleted",
                            Method::Post,
                            format!("/api/snapshots/{}/delete", enc(id)),
                            Some(json!({})),
                        ),
                        Item::Daily(n) => ctx.call(
                            "deleted",
                            Method::Post,
                            format!("/api/backups/{}/delete", enc(n)),
                            Some(json!({})),
                        ),
                    }
                    (View::List, true)
                }
                confirm::Answer::No => (View::List, true),
                confirm::Answer::Waiting => (View::Delete(item, c), true),
            },
            View::Download(item, mut f) => match f.key(k) {
                form::Action::Cancel => (View::List, true),
                form::Action::Submit => {
                    let folder = PathBuf::from(f.get("folder").trim());
                    if !folder.is_dir() {
                        f.error = Some(format!("{} is not a folder here.", folder.display()));
                        (View::Download(item, f), true)
                    } else if f.get("mode") == "recipient" && f.get("recipient").trim().is_empty() {
                        f.error = Some("Paste your public key (age1… or ssh-ed25519 …).".into());
                        (View::Download(item, f), true)
                    } else if f.get("mode") == "recipient" && f.get("recipient").contains("PRIVATE KEY") {
                        f.error =
                            Some("That is a private key: never paste it anywhere. Use the .pub file's line.".into());
                        (View::Download(item, f), true)
                    } else {
                        f.busy = true;
                        let body = json!({"mode": f.get("mode"), "recipient": f.get("recipient").trim(), "remember": f.on("remember")});
                        let path = match &item {
                            Item::Snapshot(id) => format!("/api/snapshots/{}/export", enc(id)),
                            Item::Daily(n) => format!("/api/backups/{}/export", enc(n)),
                        };
                        self.download_folder = folder;
                        self.download_recipient = f.get("recipient").trim().to_string();
                        ctx.call("exported", Method::Post, path, Some(body));
                        (View::Download(item, f), true)
                    }
                }
                form::Action::None => (View::Download(item, f), true),
            },
            View::Downloading(item) => (View::Downloading(item), true),
            View::Downloaded {
                item,
                result,
                path,
                key_saved,
            } => match k.code {
                KeyCode::Char('s') if !s(&result, "key").is_empty() && key_saved.is_none() => {
                    let file = self.download_folder.join(format!("optimux-key-{}.txt", item.id()));
                    let saved = match write_private_new(&file, &format!("{}\n", s(&result, "key"))) {
                        Ok(()) => Some(file.display().to_string()),
                        Err(e) => {
                            self.msg = Some((false, format!("Couldn't save the key as {}: {e}", file.display())));
                            None
                        }
                    };
                    (
                        View::Downloaded {
                            item,
                            result,
                            path,
                            key_saved: saved,
                        },
                        true,
                    )
                }
                // any other key: the key leaves the screen
                _ => (View::List, true),
            },
            View::Limit(mut f) => match f.key(k) {
                form::Action::Cancel => (View::List, true),
                form::Action::Submit => match f.number("gb") {
                    Ok(Some(gb)) if gb >= 0.1 => {
                        f.busy = true;
                        ctx.call("limit", Method::Post, "/api/snapshots/limit", Some(json!({"gb": gb})));
                        (View::Limit(f), true)
                    }
                    _ => {
                        f.error = Some("At least 0.1 GB.".into());
                        (View::Limit(f), true)
                    }
                },
                form::Action::None => (View::Limit(f), true),
            },
            View::Keep(mut f) => match f.key(k) {
                form::Action::Cancel => (View::List, true),
                form::Action::Submit => match f.get("keep").trim().parse::<i64>() {
                    Ok(n) => {
                        f.busy = true;
                        ctx.call("keep", Method::Post, "/api/backups/keep", Some(json!({"keep": n})));
                        (View::Keep(f), true)
                    }
                    Err(_) => {
                        f.error = Some("A whole number.".into());
                        (View::Keep(f), true)
                    }
                },
                form::Action::None => (View::Keep(f), true),
            },
            View::Upload(mut f) => match f.key(k) {
                form::Action::Cancel => {
                    self.tab = Tab::Snapshots;
                    self.rows();
                    (View::List, true)
                }
                form::Action::Submit => {
                    let file = PathBuf::from(shellish(f.get("file").trim()));
                    if !file.is_file() {
                        f.error = Some(format!("{} is not a file here.", file.display()));
                    } else {
                        f.busy = true;
                        self.upload_name = file
                            .file_name()
                            .map(|n| n.to_string_lossy().to_string())
                            .unwrap_or_default();
                        ctx.upload("uploaded", "/api/restore/upload", file);
                    }
                    (View::Upload(f), true)
                }
                form::Action::None => (View::Upload(f), true),
            },
            View::UploadKey {
                token,
                filename,
                mut form,
            } => match form.key(k) {
                form::Action::Cancel => (View::List, true),
                form::Action::Submit => {
                    form.busy = true;
                    ctx.call(
                        "imported",
                        Method::Post,
                        "/api/restore/import",
                        Some(json!({"token": token, "key": form.get("key").trim(), "filename": filename})),
                    );
                    (View::UploadKey { token, filename, form }, true)
                }
                form::Action::None => (View::UploadKey { token, filename, form }, true),
            },
        };
        self.view = view;
        used
    }

    fn paste(&mut self, text: &str) {
        match &mut self.view {
            View::Take(f) | View::Download(_, f) | View::Limit(f) | View::Keep(f) | View::Upload(f) => f.paste(text),
            View::Restore { form, .. } | View::UploadKey { form, .. } => form.paste(text),
            _ => {}
        }
    }

    fn on_answer(&mut self, a: Answer, ctx: &Ctx) {
        let (what, arg) = a.parts();
        let (what, arg) = (what.to_string(), arg.to_string());
        match (what.as_str(), a.result) {
            ("snaps", Ok(v)) => {
                let running = v.get("running").and_then(Value::as_bool) == Some(true)
                    || v.get("snapshots")
                        .and_then(Value::as_array)
                        .is_some_and(|l| l.iter().any(|x| s(x, "status") == "running"));
                self.snaps = v;
                if running && !self.polling {
                    self.polling = true;
                    ctx.call_later(1500, "poll", Method::Get, "/api/snapshots");
                }
            }
            ("poll", Ok(v)) => {
                let running = v.get("running").and_then(Value::as_bool) == Some(true)
                    || v.get("snapshots")
                        .and_then(Value::as_array)
                        .is_some_and(|l| l.iter().any(|x| s(x, "status") == "running"));
                self.snaps = v;
                if running {
                    ctx.call_later(1500, "poll", Method::Get, "/api/snapshots");
                } else {
                    self.polling = false;
                    if matches!(&self.msg, Some((true, m)) if m.starts_with("Taking")) {
                        self.msg = Some((true, "Snapshot ready.".into()));
                    }
                }
            }
            ("daily", Ok(v)) => self.daily = v,
            ("status", Ok(v)) => self.status = v,
            ("settings", Ok(v)) => self.settings = v,
            ("projects", Ok(v)) => {
                self.projects = v
                    .as_array()
                    .into_iter()
                    .flatten()
                    .filter_map(Value::as_str)
                    .map(str::to_string)
                    .collect()
            }
            ("preview", Ok(v)) => {
                if let Some(body) = self.pending_body.take() {
                    self.view = View::Preview { body, preview: v };
                }
            }
            ("taken", Ok(_)) => {
                self.msg = Some((true, "Taking the snapshot… (the list shows it as taking…)".into()));
                self.polling = false;
                self.reload(ctx);
            }
            ("readme", Ok(v)) => {
                if let View::Details(x, readme) = &mut self.view {
                    if s(x, "id") == arg {
                        *readme = Some(s(&v, "text"));
                    }
                }
            }
            ("inspect", Ok(info)) => {
                self.msg = None;
                let mut fields = Vec::new();
                for (i, p) in info
                    .get("parts")
                    .and_then(Value::as_array)
                    .into_iter()
                    .flatten()
                    .enumerate()
                {
                    let blocked = s(p, "blocked");
                    let keys = if matches!(s(p, "path").as_str(), "secrets.env" | "auth.json") {
                        " · keys"
                    } else {
                        ""
                    };
                    let mut help = if blocked.is_empty() {
                        s(p, "effect")
                    } else {
                        blocked.clone()
                    };
                    if !s(p, "note").is_empty() {
                        help.push_str(&format!(" {}", s(p, "note")));
                    }
                    let mut fl = Field::new(part_key(i), format!("{}{keys}", s(p, "path")), Kind::Toggle)
                        .value(
                            if p.get("default").and_then(Value::as_bool) == Some(true) && blocked.is_empty() {
                                "1"
                            } else {
                                ""
                            },
                        )
                        .help(format!("{} · {}", s(p, "label"), help));
                    if !blocked.is_empty() {
                        fl = fl.locked();
                    }
                    fields.push(fl);
                }
                fields.push(
                    Field::new("phrase", "To confirm, type", Kind::Text)
                        .required()
                        .help(s(&info, "confirm_phrase")),
                );
                let mut intro = String::from(
                    "Your current data is saved first, in a new Data snapshot. Data is put back when Optimux restarts; projects are put back now (an existing one moves to Archived projects first). The database is merged, not rolled back. Every file is checked against the snapshot's checksums first.",
                );
                if !s(&info, "optimux_version").is_empty() {
                    intro.push_str(&format!(" Taken by Optimux {}.", s(&info, "optimux_version")));
                }
                intro.push_str(&format!(" To confirm, type: {}", s(&info, "confirm_phrase")));
                let mut form = Form::new("Restore this snapshot", fields)
                    .intro(intro)
                    .submit_label("Restore");
                form.focus = form.fields.iter().position(|f| !f.locked).unwrap_or(0);
                self.view = View::Restore { sid: arg, info, form };
            }
            ("restored", Ok(v)) => {
                let mut text = String::new();
                if let Some(p) = v.get("projects").and_then(Value::as_array).filter(|p| !p.is_empty()) {
                    text.push_str(&format!(
                        "Put back now: {}. ",
                        p.iter().filter_map(Value::as_str).collect::<Vec<_>>().join(", ")
                    ));
                }
                if let Some(a) = v.get("archived").and_then(Value::as_array).filter(|p| !p.is_empty()) {
                    text.push_str(&format!(
                        "Moved to Archived projects first: {}. ",
                        a.iter().filter_map(Value::as_str).collect::<Vec<_>>().join(", ")
                    ));
                }
                if !s(&v, "before_snapshot").is_empty() {
                    text.push_str(&format!("Your data was saved first in {}. ", s(&v, "before_snapshot")));
                }
                if v.get("restart_needed").and_then(Value::as_bool) != Some(false)
                    && (v
                        .get("data")
                        .is_some_and(|d| d.as_array().is_some_and(|a| !a.is_empty()))
                        || s(&v, "source").starts_with("daily"))
                {
                    text.push_str("Restart Optimux to finish.");
                }
                self.msg = Some((true, if text.is_empty() { "Restored.".into() } else { text }));
                self.view = View::List;
                self.reload(ctx);
            }
            ("deleted", Ok(_)) => {
                self.msg = Some((true, "Deleted.".into()));
                self.reload(ctx);
            }
            ("exported", Ok(v)) => {
                let item = match &self.view {
                    View::Download(item, _) => item.clone(),
                    _ => return,
                };
                let dest = self.download_folder.join(s(&v, "filename"));
                self.export = Some(v.clone());
                self.view = View::Downloading(item);
                ctx.download("downloaded", s(&v, "url"), dest);
            }
            ("downloaded", Ok(d)) => {
                let item = match &self.view {
                    View::Downloading(item) => item.clone(),
                    _ => return,
                };
                let result = self.export.take().unwrap_or(Value::Null);
                self.view = View::Downloaded {
                    item,
                    result,
                    path: s(&d, "path"),
                    key_saved: None,
                };
            }
            ("backed_up", Ok(v)) => {
                self.msg = Some((true, format!("Backed up: {}", s(&v, "path"))));
                self.reload(ctx);
            }
            ("keep", Ok(v)) => {
                self.view = View::List;
                self.msg = Some((
                    true,
                    format!(
                        "The newest {} daily backups are kept.",
                        v.get("keep").map(|k| k.to_string()).unwrap_or_default()
                    ),
                ));
                self.reload(ctx);
            }
            ("checked", Ok(v)) => {
                let ok = v.get("ok").and_then(Value::as_bool) != Some(false)
                    && v.get("checked").and_then(Value::as_bool) != Some(false);
                self.msg = Some((
                    ok,
                    if ok {
                        "Checked: the backup is sound.".into()
                    } else {
                        format!("Damaged: {}", s(&v, "error"))
                    },
                ));
                self.reload(ctx);
            }
            ("limit", Ok(v)) => {
                self.view = View::List;
                self.msg = Some((
                    true,
                    format!(
                        "Space for snapshots: {} GB.",
                        v.get("snapshot_limit_gb").map(|g| g.to_string()).unwrap_or_default()
                    ),
                ));
                self.reload(ctx);
            }
            ("uploaded", Ok(v)) => {
                if v.get("encrypted").and_then(Value::as_bool) == Some(true) {
                    let mut form = Form::new(
                        "The file is encrypted",
                        vec![Field::new("key", "Its key", Kind::Secret).required().help("AGE-SECRET-KEY-1… (the one shown with the download, or in its optimux-key-….txt)")],
                    )
                    .intro("The key is used once, here, and kept nowhere. Encrypted to your SSH key? Open it on your computer first (age -d -i ~/.ssh/id_ed25519 -o snapshot.tar.gz <file>.age), then upload the .tar.gz.")
                    .submit_label("Decrypt and check");
                    form.focus = 0;
                    self.view = View::UploadKey {
                        token: s(&v, "token"),
                        filename: self.upload_name.clone(),
                        form,
                    };
                } else {
                    self.msg = Some((true, "Checking every file…".into()));
                    ctx.call(
                        "imported",
                        Method::Post,
                        "/api/restore/import",
                        Some(json!({"token": s(&v, "token"), "key": "", "filename": self.upload_name})),
                    );
                    self.view = View::List;
                }
            }
            ("imported", Ok(v)) => {
                self.msg = Some((
                    true,
                    format!(
                        "Checked and added to the snapshots: {}. R restores parts of it.",
                        s(&v, "id")
                    ),
                ));
                self.view = View::List;
                self.tab = Tab::Snapshots;
                self.reload(ctx);
            }
            ("restart", Ok(_)) => {
                self.msg = Some((
                    true,
                    "Optimux is restarting: the chat line comes back by itself.".into(),
                ))
            }
            ("cancelled", Ok(v)) => {
                self.msg = Some((
                    true,
                    format!("Restore cancelled; the prepared files are in {}.", s(&v, "moved_to")),
                ));
                self.reload(ctx);
            }
            ("dismissed", Ok(_)) => self.reload(ctx),
            (_, Err(e)) => match &mut self.view {
                View::Take(f) | View::Download(_, f) | View::Limit(f) | View::Keep(f) | View::Upload(f) => {
                    f.busy = false;
                    f.error = Some(e);
                }
                View::Restore { form, .. } | View::UploadKey { form, .. } => {
                    form.busy = false;
                    form.error = Some(e);
                }
                View::Downloading(_) => {
                    self.msg = Some((false, e));
                    self.view = View::List;
                }
                _ => self.msg = Some((false, e)),
            },
            _ => {}
        }
        self.rows();
    }

    fn keys(&self) -> String {
        let pending = self.status.get("pending").is_some_and(|p| !p.is_null());
        let extra = if pending {
            " · ! restart now · X cancel the restore"
        } else {
            ""
        };
        match (&self.view, self.tab) {
            (View::List, Tab::Snapshots) => format!(
                "n take · Enter details · R restore · D download · x delete · L space · 2 daily · 3 from a file{extra}"
            ),
            (View::List, Tab::Daily) => {
                format!("b back up now · k keep · c check · R restore · D download · x delete · 1 snapshots{extra}")
            }
            (View::Preview { body, .. }, _) if s(body, "kind") != "full" => {
                "y take it · f take Everything instead · n back".into()
            }
            (View::Preview { .. }, _) => "y take it · n back".into(),
            (View::Details(..), _) => "m the README · R restore · D download · x delete · ↑↓ scroll · Esc back".into(),
            (View::Downloaded { result, key_saved, .. }, _) if !s(result, "key").is_empty() && key_saved.is_none() => {
                "s save the key as a file · any other key: I have saved the key".into()
            }
            (View::Downloaded { .. }, _) => "any key closes".into(),
            _ => String::new(),
        }
    }

    fn draw(&mut self, f: &mut Frame, area: Rect) -> Vec<(u16, usize)> {
        let width = area.width as usize;
        let body = draw_head(
            f,
            area,
            head_lines(
                "Backups",
                "  ·  snapshots and daily backups, kept on this machine",
                &self.msg,
                width,
            ),
        );
        let bold = Style::new().add_modifier(Modifier::BOLD);
        let title = Style::new().fg(Color::LightCyan).add_modifier(Modifier::BOLD);
        let mut top = Vec::new();
        if let Some(p) = self.status.get("pending").filter(|p| !p.is_null()) {
            let parts: Vec<&str> = p
                .get("parts")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
                .filter_map(Value::as_str)
                .collect();
            top.push(Line::from(Span::styled(
                "⚠ Restart Optimux to finish the restore",
                Style::new().fg(Color::Yellow).add_modifier(Modifier::BOLD),
            )));
            top.extend(wrap(
                vec![Span::styled(
                    format!(
                        "Ready to put back: {}{}. {}",
                        parts.join(", "),
                        if s(p, "source").is_empty() { String::new() } else { format!(", from {}", s(p, "source")) },
                        if self.status.get("can_restart").and_then(Value::as_bool) == Some(true) {
                            "! restarts it now, X cancels the restore."
                        } else {
                            "Restart it where it runs: optimux restart (on a server: sudo optimux restart). X cancels the restore."
                        }
                    ),
                    Style::new().fg(Color::Yellow),
                )],
                width,
                "  ",
                "  ",
            ));
            top.push(Line::default());
        }
        if let Some(l) = self.status.get("last").filter(|l| !l.is_null()) {
            let ok = l.get("ok").and_then(Value::as_bool) == Some(true);
            let text = if ok {
                format!(
                    "✓ Restore finished: {}. Merged with your data: nothing recorded on either side was lost. The files they replaced are in {}. (d dismisses this)",
                    l.get("parts")
                        .and_then(Value::as_array)
                        .into_iter()
                        .flatten()
                        .filter_map(Value::as_str)
                        .collect::<Vec<_>>()
                        .join(", "),
                    s(l, "aside")
                )
            } else {
                format!(
                    "✕ The restore could not be finished: {}. Nothing was changed. The prepared files are in {}. (d dismisses this)",
                    s(l, "error"),
                    s(l, "aside")
                )
            };
            top.extend(wrap(
                vec![Span::styled(
                    text,
                    Style::new().fg(if ok { Color::Green } else { Color::Red }),
                )],
                width,
                "",
                "",
            ));
            top.push(Line::default());
        }
        match &mut self.view {
            View::List => {
                let tab = |on: bool| {
                    if on {
                        Style::new().fg(Color::Black).bg(Color::LightCyan)
                    } else {
                        DIM
                    }
                };
                top.push(Line::from(vec![
                    Span::styled(" 1 Snapshots ", tab(self.tab == Tab::Snapshots)),
                    Span::raw(" "),
                    Span::styled(" 2 Daily backups of the database ", tab(self.tab == Tab::Daily)),
                    Span::raw(" "),
                    Span::styled(" 3 Restore from a downloaded file ", tab(self.tab == Tab::File)),
                ]));
                match self.tab {
                    Tab::Snapshots => top.push(Line::from(Span::styled(
                        format!(
                            "In {} · using {} of {} · {} free on disk",
                            s(&self.snaps, "dir"),
                            bytes(num(&self.snaps, "used_bytes").unwrap_or(0.0)),
                            bytes(num(&self.snaps, "limit_bytes").unwrap_or(0.0)),
                            bytes(num(&self.snaps, "free_bytes").unwrap_or(0.0))
                        ),
                        DIM,
                    ))),
                    Tab::Daily => {
                        top.push(Line::from(Span::styled(
                            format!(
                                "In {} · the newest {} kept · one copy each time Optimux starts, checked before it replaces anything",
                                s(&self.daily, "dir"),
                                self.daily.get("keep").map(|k| k.to_string()).unwrap_or_default()
                            ),
                            DIM,
                        )));
                        if let Some(c) = self
                            .daily
                            .get("last_check")
                            .filter(|c| c.get("ok").and_then(Value::as_bool) == Some(false))
                        {
                            top.extend(wrap(
                                vec![Span::styled(
                                    format!(
                                        "✕ The last backup failed its check: {}. It was kept aside as {}.",
                                        s(c, "error"),
                                        s(c, "file")
                                    ),
                                    Style::new().fg(Color::Red),
                                )],
                                width,
                                "",
                                "",
                            ));
                        }
                    }
                    Tab::File => {}
                }
                top.push(Line::default());
                let n = (top.len() as u16).min(body.height);
                f.render_widget(Paragraph::new(top), Rect { height: n, ..body });
                return self.table.draw(
                    f,
                    Rect {
                        y: body.y + n,
                        height: body.height.saturating_sub(n),
                        ..body
                    },
                );
            }
            View::Take(form) | View::Download(_, form) | View::Limit(form) | View::Keep(form) | View::Upload(form) => {
                top.push(Line::from(Span::styled(form.title.clone(), title)));
                top.push(Line::default());
                let n = (top.len() as u16).min(body.height);
                f.render_widget(Paragraph::new(top), Rect { height: n, ..body });
                form.draw(
                    f,
                    Rect {
                        y: body.y + n,
                        height: body.height.saturating_sub(n),
                        ..body
                    },
                );
            }
            View::Restore { form, .. } | View::UploadKey { form, .. } => {
                top.push(Line::from(Span::styled(form.title.clone(), title)));
                top.push(Line::default());
                let n = (top.len() as u16).min(body.height);
                f.render_widget(Paragraph::new(top), Rect { height: n, ..body });
                form.draw(
                    f,
                    Rect {
                        y: body.y + n,
                        height: body.height.saturating_sub(n),
                        ..body
                    },
                );
            }
            View::Preview { body: b, preview } => {
                top.push(Line::from(Span::styled(
                    format!("What goes in: {}", kind_label(&s(b, "kind"))),
                    title,
                )));
                if s(b, "kind") != "full" {
                    top.extend(wrap(
                        vec![Span::styled(
                            if s(b, "kind") == "data" {
                                "Only part of Optimux: your projects will not be in it. To be able to go back fully after an experiment, take Everything (f)."
                            } else {
                                "Only part of Optimux: your settings, models, keys, memory, skills and chats will not be in it. To be able to go back fully, take Everything (f)."
                            }
                            .to_string(),
                            Style::new().fg(Color::Yellow),
                        )],
                        width,
                        "⚠ ",
                        "  ",
                    ));
                }
                top.push(Line::default());
                for i in preview.get("items").and_then(Value::as_array).into_iter().flatten() {
                    top.push(Line::from(vec![
                        Span::raw(format!("  {}", crate::ui::table::fit(&s(i, "path"), 28))),
                        Span::styled(
                            crate::ui::table::fit(&s(i, "label"), width.saturating_sub(58).max(20)),
                            DIM,
                        ),
                        Span::styled(
                            format!(
                                "{:>8} files {:>10}",
                                num(i, "files").unwrap_or(0.0),
                                bytes(num(i, "bytes").unwrap_or(0.0))
                            ),
                            DIM,
                        ),
                    ]));
                }
                top.push(Line::default());
                top.push(Line::from(Span::styled(
                    format!(
                        "{} before compression",
                        bytes(num(preview, "total_bytes").unwrap_or(0.0))
                    ),
                    bold,
                )));
                if let Some(sk) = preview.get("skipped").filter(|_| {
                    s(b, "kind") != "data" && b.get("skip_rebuildable").and_then(Value::as_bool) == Some(true)
                }) {
                    if num(sk, "dirs").unwrap_or(0.0) > 0.0 {
                        top.push(Line::from(Span::styled(
                            format!(
                                "Skipped: {:.0} rebuildable folder(s) ({}).",
                                num(sk, "dirs").unwrap_or(0.0),
                                bytes(num(sk, "bytes").unwrap_or(0.0))
                            ),
                            DIM,
                        )));
                    }
                }
                let left: Vec<&str> = preview
                    .get("left_out")
                    .and_then(Value::as_array)
                    .into_iter()
                    .flatten()
                    .filter_map(Value::as_str)
                    .collect();
                if !left.is_empty() {
                    top.extend(wrap(
                        vec![Span::styled(format!("Not included: {}.", left.join("; ")), DIM)],
                        width,
                        "",
                        "",
                    ));
                }
                let fits = preview.get("fits").and_then(Value::as_bool) != Some(false);
                top.push(Line::from(Span::styled(
                    if fits {
                        format!(
                            "✓ Fits: {} free on disk.",
                            bytes(num(preview, "free_bytes").unwrap_or(0.0))
                        )
                    } else {
                        format!("✕ {}", s(preview, "reason"))
                    },
                    Style::new().fg(if fits { Color::Green } else { Color::Red }),
                )));
                f.render_widget(Paragraph::new(top), body);
            }
            View::Details(x, readme) => {
                top.push(Line::from(Span::styled(
                    format!(
                        "{} · {}",
                        kind_label(&s(x, "kind")),
                        num(x, "created").map(ago).unwrap_or_default()
                    ),
                    title,
                )));
                if s(x, "kind") != "data" {
                    let p: Vec<&str> = x
                        .get("projects")
                        .and_then(Value::as_array)
                        .into_iter()
                        .flatten()
                        .filter_map(Value::as_str)
                        .collect();
                    top.push(Line::from(vec![
                        Span::styled("Projects: ", DIM),
                        Span::raw(if p.is_empty() { "none".into() } else { p.join(", ") }),
                    ]));
                }
                if !s(x, "note").is_empty() {
                    top.push(Line::from(vec![Span::styled("Note: ", DIM), Span::raw(s(x, "note"))]));
                }
                for i in x.get("items").and_then(Value::as_array).into_iter().flatten() {
                    top.push(Line::from(vec![
                        Span::raw(format!("  {}", crate::ui::table::fit(&s(i, "path"), 28))),
                        Span::styled(
                            crate::ui::table::fit(&s(i, "label"), width.saturating_sub(58).max(20)),
                            DIM,
                        ),
                        Span::styled(
                            format!(
                                "{:>8} files {:>10}",
                                num(i, "files").unwrap_or(0.0),
                                bytes(num(i, "bytes").unwrap_or(0.0))
                            ),
                            DIM,
                        ),
                    ]));
                }
                let left: Vec<&str> = x
                    .get("left_out")
                    .and_then(Value::as_array)
                    .into_iter()
                    .flatten()
                    .filter_map(Value::as_str)
                    .collect();
                if !left.is_empty() {
                    top.extend(wrap(
                        vec![Span::styled(format!("Not included: {}.", left.join("; ")), DIM)],
                        width,
                        "",
                        "",
                    ));
                }
                top.push(Line::from(vec![
                    Span::styled("File: ", DIM),
                    Span::raw(format!("{}/{}.tar.gz", s(&self.snaps, "dir"), s(x, "id"))),
                ]));
                if !s(x, "optimux_version").is_empty() {
                    top.push(Line::from(vec![
                        Span::styled("Taken by Optimux ", DIM),
                        Span::raw(s(x, "optimux_version")),
                    ]));
                }
                if let Some(r) = readme {
                    top.push(Line::default());
                    top.push(Line::from(Span::styled(
                        "README (inside the file: contents and how to restore by hand)",
                        bold,
                    )));
                    for l in r.lines() {
                        top.extend(wrap(vec![Span::raw(l.to_string())], width, "", ""));
                    }
                }
                self.scroll = self.scroll.min(top.len().saturating_sub(body.height as usize));
                f.render_widget(
                    Paragraph::new(top.into_iter().skip(self.scroll).collect::<Vec<_>>()),
                    body,
                );
            }
            View::DailyRestore(_, c) | View::Delete(_, c) => {
                top.push(Line::from(Span::styled(
                    c.title.clone(),
                    Style::new().fg(Color::Red).add_modifier(Modifier::BOLD),
                )));
                top.push(Line::default());
                top.extend(c.lines(width));
                f.render_widget(Paragraph::new(top), body);
            }
            View::Downloading(_) => {
                top.push(Line::from(Span::styled("Encrypting and downloading…", title)));
                f.render_widget(Paragraph::new(top), body);
            }
            View::Downloaded {
                item,
                result,
                path,
                key_saved,
            } => {
                top.push(Line::from(Span::styled("Downloaded", title)));
                top.push(Line::from(vec![
                    Span::styled("Saved as ", DIM),
                    Span::raw(format!("{path} ({})", bytes(num(result, "size").unwrap_or(0.0)))),
                ]));
                top.push(Line::default());
                let key = s(result, "key");
                if !key.is_empty() {
                    top.push(Line::from(Span::styled(
                        "⚠ Save this key now: it is shown only once",
                        Style::new().fg(Color::Yellow).add_modifier(Modifier::BOLD),
                    )));
                    top.extend(wrap(
                        vec![Span::styled(
                            "Optimux doesn't keep it. Without it the file can't be opened, by anyone. Store it apart from the file (a password manager is a good place).",
                            DIM,
                        )],
                        width,
                        "",
                        "",
                    ));
                    top.push(Line::from(Span::styled(key.clone(), bold)));
                    if let Some(k) = key_saved {
                        top.push(Line::from(Span::styled(
                            format!("✓ Also saved as {k} (readable only by you)."),
                            Style::new().fg(Color::Green),
                        )));
                    }
                    top.push(Line::default());
                }
                if s(result, "mode") != "plain" {
                    let keyfile = format!("optimux-key-{}.txt", item.id());
                    top.push(Line::from(Span::styled(
                        "To open it, with age (age-encryption.org):",
                        DIM,
                    )));
                    top.push(Line::from(Span::raw(age_command(
                        &s(result, "mode"),
                        &self.download_recipient,
                        &keyfile,
                        &s(result, "filename"),
                    ))));
                }
                f.render_widget(Paragraph::new(top), body);
            }
        }
        Vec::new()
    }

    fn tabs(&self) -> Vec<Vec<String>> {
        if !matches!(self.view, View::List) {
            return Vec::new();
        }
        vec![vec![
            " 1 Snapshots ".into(),
            " 2 Daily backups of the database ".into(),
            " 3 Restore from a downloaded file ".into(),
        ]]
    }

    fn click_tab(&mut self, _row: usize, i: usize, ctx: &Ctx) {
        let c = char::from(b'1' + i.min(2) as u8);
        self.key(KeyEvent::new(KeyCode::Char(c), KeyModifiers::NONE), ctx);
    }

    fn has_window(&self) -> bool {
        !matches!(self.view, View::List) || self.table.filtering
    }

    fn click_row(&mut self, i: usize, _ctx: &Ctx) -> bool {
        if matches!(self.view, View::List) {
            self.table.selected = i.min(self.table.visible().len().saturating_sub(1));
        }
        false
    }
}

/// The form key of a restore's part i ("part0", "part1"…), made once for all forms.
fn part_key(i: usize) -> &'static str {
    static KEYS: std::sync::OnceLock<Vec<&'static str>> = std::sync::OnceLock::new();
    let keys = KEYS.get_or_init(|| {
        (0..1000)
            .map(|i| &*Box::leak(format!("part{i}").into_boxed_str()))
            .collect()
    });
    keys.get(i).copied().unwrap_or("part-more")
}

/// A path as typed in a shell: ~ is the home folder, quotes around it are dropped.
pub fn shellish(p: &str) -> String {
    let p = p.trim().trim_matches(|c| c == '"' || c == '\'');
    if let Some(rest) = p.strip_prefix("~/") {
        if let Some(home) = std::env::var_os("HOME") {
            return Path::new(&home).join(rest).display().to_string();
        }
    }
    p.to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_age_command_names_the_right_key() {
        assert_eq!(
            age_command("key", "", "optimux-key-x.txt", "s.tar.gz.age"),
            "age -d -i optimux-key-x.txt -o s.tar.gz s.tar.gz.age"
        );
        assert!(age_command("recipient", "ssh-ed25519 AAAA", "", "s.age").contains("~/.ssh/id_ed25519"));
        assert!(age_command("recipient", "age1xyz", "", "s.age").contains("<your-age-key-file>"));
    }

    #[test]
    fn sizes_count_in_1024s() {
        assert_eq!(bytes(5.0 * 1024.0 * 1024.0 * 1024.0), "5.0 GB");
        assert_eq!(bytes(2048.0), "2 kB");
        assert_eq!(bytes(10.0), "10 B");
    }

    #[test]
    fn paths_as_typed() {
        assert_eq!(shellish("'/tmp/a b.age'"), "/tmp/a b.age");
        assert!(shellish("~/x.age").ends_with("/x.age") && !shellish("~/x.age").starts_with('~'));
    }

    #[test]
    fn a_key_file_is_private_and_never_replaced() {
        let dir = tempfile::tempdir().unwrap();
        let f = dir.path().join("k.txt");
        write_private_new(&f, "AGE-SECRET-KEY-1X\n").unwrap();
        assert!(write_private_new(&f, "other").is_err());
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            assert_eq!(std::fs::metadata(&f).unwrap().permissions().mode() & 0o777, 0o600);
        }
    }
}
