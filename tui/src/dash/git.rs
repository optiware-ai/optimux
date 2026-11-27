//! A project's git actions, as the web page's Push and Update windows, its Status and
//! Identity terminal, and the Push manually / Pull manually lines. Opened from
//! Projects; the owning section passes on its keys and the answers tagged "git:…".
//! Push and Update are prepared first (nothing changes), then run on confirm; never
//! forced, fast-forward only (the server's rules).

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use ratatui::Frame;
use ratatui::layout::Rect;
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::Paragraph;
use serde_json::{Value, json};

use super::{Answer, Ctx, fmt};
use crate::api::{Method, enc};
use crate::chat::text::{DIM, wrap};
use crate::cli::{num, s};
use crate::ui::external::Edit;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    Push,
    Update,
    Status,
    Identity,
    Commands,
}

impl Kind {
    /// The window's route for a project (written whole, so the contract test sees it).
    fn url(self, project: &str) -> String {
        let p = enc(project);
        match self {
            Kind::Push => format!("/api/projects/{p}/push"),
            Kind::Update => format!("/api/projects/{p}/update"),
            Kind::Status => format!("/api/projects/{p}/status"),
            Kind::Identity => format!("/api/projects/{p}/identity"),
            Kind::Commands => format!("/api/projects/{p}/git-commands"),
        }
    }

    fn title(self) -> &'static str {
        match self {
            Kind::Push => "Push",
            Kind::Update => "Update from GitHub",
            Kind::Status => "Git status",
            Kind::Identity => "Git identity",
            Kind::Commands => "Push and pull manually",
        }
    }
}

/// A changed file in words (the page's changeLabel).
pub fn change_label(f: &Value) -> String {
    let st = s(f, "status");
    let added = num(f, "added").unwrap_or(0.0);
    let removed = num(f, "removed").unwrap_or(0.0);
    match st.as_str() {
        "new" => format!("new · +{added:.0}"),
        "deleted" => format!("deleted · −{removed:.0}"),
        "binary" => "binary".into(),
        _ => format!("+{added:.0} −{removed:.0}"),
    }
}

pub struct GitWindow {
    pub kind: Kind,
    pub project: String,
    data: Value,
    error: Option<String>,
    result: Option<Value>,
    loading: bool,
    busy: bool,
    /// Push: the commit message; also commit the files (when commits wait already).
    message: String,
    commit_too: bool,
    confirmed: bool,
    writing: bool,
    note: String,
    /// Commands: the pull line over SSH.
    ssh: bool,
    scroll: usize,
    pending_edit: Option<Edit>,
}

impl GitWindow {
    pub fn open(kind: Kind, project: &str, ctx: &Ctx) -> GitWindow {
        let w = GitWindow {
            kind,
            project: project.to_string(),
            data: Value::Null,
            error: None,
            result: None,
            loading: true,
            busy: false,
            message: String::new(),
            commit_too: true,
            confirmed: false,
            writing: false,
            note: String::new(),
            ssh: false,
            scroll: 0,
            pending_edit: None,
        };
        w.fetch(ctx);
        w
    }

    fn fetch(&self, ctx: &Ctx) {
        ctx.call("git:data", Method::Get, self.kind.url(&self.project), None);
    }

    fn files(&self) -> Vec<Value> {
        self.data
            .get("files")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default()
    }

    fn commits(&self) -> u64 {
        self.data.get("commits").and_then(Value::as_u64).unwrap_or(0)
    }

    /// Commits already made wait, and files are uncommitted: the user picks.
    fn can_choose(&self) -> bool {
        self.kind == Kind::Push && !self.files().is_empty() && self.commits() > 0
    }

    /// Pushing only the commits already made (the files stay uncommitted).
    fn commits_only(&self) -> bool {
        self.kind == Kind::Push && !self.files().is_empty() && !self.commit_too
    }

    fn list(&self, key: &str) -> Vec<String> {
        self.data
            .get(key)
            .and_then(Value::as_array)
            .map(|a| a.iter().filter_map(Value::as_str).map(str::to_string).collect())
            .unwrap_or_default()
    }

    /// The problems (blocking) and warnings, without those of uncommitted files when
    /// they stay out (the page's syncProblems / syncWarnings).
    fn problems(&self) -> (Vec<String>, Vec<String>) {
        let (mut p, mut w) = (self.list("problems"), self.list("warnings"));
        if self.commits_only() {
            let (fp, fw) = (self.list("file_problems"), self.list("file_warnings"));
            p.retain(|x| !fp.contains(x));
            w.retain(|x| !fw.contains(x));
        }
        (p, w)
    }

    fn ready(&self) -> Result<(), &'static str> {
        if self.data.is_null() || self.result.is_some() {
            return Err("");
        }
        let (p, w) = self.problems();
        if !p.is_empty() {
            return Err("It can't run: see ⛔ above.");
        }
        if !w.is_empty() && !self.confirmed {
            return Err("Read the warnings, then c to say you checked them.");
        }
        if self.kind == Kind::Push && !self.files().is_empty() && self.commit_too && self.message.trim().is_empty() {
            return Err("Write a commit message first (e, or m to have it written).");
        }
        Ok(())
    }

    pub fn take_edit(&mut self) -> Option<Edit> {
        self.pending_edit.take()
    }

    pub fn edited(&mut self, result: Result<Option<String>, String>) {
        match result {
            Ok(Some(text)) => self.message = text.trim_end().to_string(),
            Ok(None) => {}
            Err(e) => self.note = e,
        }
    }

    /// True when the window closes.
    pub fn key(&mut self, k: KeyEvent, ctx: &Ctx) -> bool {
        if self.busy {
            return false;
        }
        match k.code {
            KeyCode::Esc => return true,
            KeyCode::Up => self.scroll = self.scroll.saturating_sub(1),
            KeyCode::Down => self.scroll += 1,
            KeyCode::PageUp => self.scroll = self.scroll.saturating_sub(10),
            KeyCode::PageDown => self.scroll += 10,
            KeyCode::Char('r') if matches!(self.kind, Kind::Status | Kind::Identity | Kind::Commands) => {
                self.loading = true;
                self.fetch(ctx);
            }
            KeyCode::Char('s') if self.kind == Kind::Commands => self.ssh = !self.ssh,
            KeyCode::Char('e') if self.kind == Kind::Push && self.result.is_none() => {
                self.pending_edit = Some(Edit {
                    text: self.message.clone(),
                    suffix: ".txt".into(),
                });
            }
            KeyCode::Char('m')
                if self.kind == Kind::Push && self.result.is_none() && !self.writing && !self.data.is_null() =>
            {
                self.writing = true;
                self.note = "The Simple model is writing the message (one small paid call)…".into();
                ctx.call(
                    "git:message",
                    Method::Post,
                    format!("/api/projects/{}/commit-message", enc(&self.project)),
                    Some(json!({})),
                );
            }
            KeyCode::Char('c') if !self.data.is_null() => self.confirmed = !self.confirmed,
            KeyCode::Char('1') if self.can_choose() => {
                self.commit_too = false;
                self.confirmed = false;
            }
            KeyCode::Char('2') if self.can_choose() => {
                self.commit_too = true;
                self.confirmed = false;
            }
            KeyCode::Enter => {
                if matches!(self.kind, Kind::Push | Kind::Update) {
                    if self.result.is_some() {
                        return true;
                    }
                    match self.ready() {
                        Ok(()) => {
                            self.busy = true;
                            self.note.clear();
                            let body = if self.kind == Kind::Push {
                                json!({
                                    "message": self.message, "tree": self.data.get("tree"), "confirmed": self.confirmed,
                                    "commit": !self.commits_only(), "head": self.data.get("head"),
                                })
                            } else {
                                json!({"sha": self.data.get("sha")})
                            };
                            ctx.call("git:ran", Method::Post, self.kind.url(&self.project), Some(body));
                        }
                        Err(why) if !why.is_empty() => self.note = why.into(),
                        Err(_) => {}
                    }
                }
            }
            _ => {}
        }
        false
    }

    pub fn on_answer(&mut self, a: Answer) {
        let what = a.parts().1.to_string();
        match (what.as_str(), a.result) {
            ("data", Ok(v)) => {
                self.loading = false;
                self.error = None;
                if self.kind == Kind::Push && self.data.is_null() {
                    self.message = s(&v, "default_message");
                }
                self.data = v;
                if self.kind == Kind::Push {
                    // from a fresh look: commit the files too, unless commits wait and … the user picks
                    self.commit_too = true;
                }
            }
            ("message", Ok(v)) => {
                self.writing = false;
                self.message = s(&v, "message");
                self.note = format!(
                    "Written by {}{} · {}. Check it before pushing (e edits it).",
                    s(&v, "model"),
                    if v.get("template").and_then(Value::as_bool) == Some(true) {
                        ", following the commit template"
                    } else {
                        ""
                    },
                    num(&v, "cost").map(fmt::cost).unwrap_or_else(|| "cost unknown".into())
                );
            }
            ("ran", Ok(v)) => {
                self.busy = false;
                self.result = Some(v);
            }
            (_, Err(e)) => {
                self.loading = false;
                self.busy = false;
                self.writing = false;
                if self.data.is_null() {
                    self.error = Some(e);
                } else {
                    self.note = e;
                }
            }
            _ => {}
        }
    }

    /// Its two choices (commits only / commit the files too), as drawn.
    pub fn tabs(&self) -> Vec<Vec<String>> {
        if !self.can_choose() {
            return Vec::new();
        }
        vec![vec![
            format!(" 1 Push the {} commit(s) already made ", self.commits()),
            " 2 Commit the files below too, then push ".into(),
        ]]
    }

    pub fn click_tab(&mut self, i: usize, ctx: &Ctx) {
        let c = if i == 0 { '1' } else { '2' };
        self.key(KeyEvent::new(KeyCode::Char(c), KeyModifiers::NONE), ctx);
    }

    pub fn keys(&self) -> String {
        match self.kind {
            _ if self.result.is_some() => "Enter or Esc close".into(),
            Kind::Push => {
                let mut k = String::from("Enter run · e edit the message · m write it for me");
                if self.can_choose() {
                    k.push_str(" · 1 only the commits · 2 commit the files too");
                }
                k.push_str(" · c I checked the warnings · Esc cancel");
                k
            }
            Kind::Update => "Enter update · c I checked the warnings · Esc cancel".into(),
            Kind::Commands => "s HTTPS/SSH · r refresh · ↑↓ scroll · Esc close".into(),
            _ => "r run it again · ↑↓ scroll · Esc close".into(),
        }
    }

    pub fn draw(&mut self, f: &mut Frame, area: Rect) {
        let width = area.width as usize;
        let bold = Style::new().fg(Color::LightCyan).add_modifier(Modifier::BOLD);
        let mut lines = vec![
            Line::from(Span::styled(format!("{} · {}", self.kind.title(), self.project), bold)),
            Line::default(),
        ];
        let d = &self.data;
        if self.loading && d.is_null() {
            lines.push(Line::from(Span::styled(
                match self.kind {
                    Kind::Push | Kind::Update | Kind::Status => "Checking the project and GitHub…",
                    _ => "Reading the project…",
                },
                DIM,
            )));
        } else if let Some(e) = &self.error {
            lines.extend(wrap(
                vec![Span::styled(e.clone(), Style::new().fg(Color::Red))],
                width,
                "",
                "",
            ));
        } else if let Some(r) = &self.result {
            let text = if self.kind == Kind::Push {
                let mut t = format!(
                    "✓ Pushed {} commit(s) to {} · {}.",
                    r.get("commits").map(|v| v.to_string()).unwrap_or_default(),
                    s(r, "branch"),
                    s(r, "commit")
                );
                if !s(r, "url").is_empty() {
                    t.push_str(&format!(" {}", s(r, "url")));
                }
                t
            } else {
                format!(
                    "✓ Updated: {} new commit(s), {} file(s) changed. Now at {}.",
                    r.get("commits").map(|v| v.to_string()).unwrap_or_default(),
                    r.get("files").map(|v| v.to_string()).unwrap_or_default(),
                    s(r, "head")
                )
            };
            lines.extend(wrap(
                vec![Span::styled(text, Style::new().fg(Color::Green))],
                width,
                "",
                "",
            ));
        } else {
            match self.kind {
                Kind::Push | Kind::Update => self.sync_lines(&mut lines, width),
                Kind::Status | Kind::Identity => {
                    for st in d.get("steps").and_then(Value::as_array).into_iter().flatten() {
                        let ok = st.get("ok").and_then(Value::as_bool) != Some(false);
                        lines.push(Line::from(Span::styled(
                            format!("$ {}", s(st, "command")),
                            Style::new().fg(if ok { Color::Green } else { Color::Red }),
                        )));
                        for l in s(st, "output").lines() {
                            let style =
                                if l.trim_start().starts_with("modified:") || l.trim_start().starts_with("deleted:") {
                                    Style::new().fg(Color::Red)
                                } else {
                                    Style::new()
                                };
                            lines.extend(wrap(vec![Span::styled(l.to_string(), style)], width, "", ""));
                        }
                        lines.push(Line::default());
                    }
                    for n in d
                        .get("notes")
                        .and_then(Value::as_array)
                        .into_iter()
                        .flatten()
                        .filter_map(Value::as_str)
                    {
                        lines.extend(wrap(vec![Span::styled(n.to_string(), DIM)], width, "· ", "  "));
                    }
                    if self.kind == Kind::Status {
                        let (ahead, behind) = (num(d, "ahead").unwrap_or(0.0), num(d, "behind").unwrap_or(0.0));
                        lines.push(Line::from(Span::styled(
                            format!("{ahead:.0} commit(s) to push · {behind:.0} to pull"),
                            DIM,
                        )));
                    }
                }
                Kind::Commands => {
                    if !s(d, "problem").is_empty() {
                        lines.extend(wrap(
                            vec![Span::styled(s(d, "problem"), Style::new().fg(Color::Yellow))],
                            width,
                            "",
                            "",
                        ));
                    } else {
                        if !s(d, "warning").is_empty() {
                            lines.extend(wrap(
                                vec![Span::styled(s(d, "warning"), Style::new().fg(Color::Yellow))],
                                width,
                                "⚠ ",
                                "  ",
                            ));
                        }
                        lines.push(Line::from(Span::styled(
                            format!(
                                "Push from a terminal on {}:",
                                if d.get("server").and_then(Value::as_bool) == Some(true) {
                                    "this server (it acts as the Optimux user through sudo)"
                                } else {
                                    "this computer"
                                }
                            ),
                            Style::new().add_modifier(Modifier::BOLD),
                        )));
                        lines.extend(wrap(vec![Span::raw(s(d, "push"))], width, "  ", "  "));
                        lines.push(Line::default());
                        let pull = if self.ssh && !s(d, "pull_ssh").is_empty() {
                            s(d, "pull_ssh")
                        } else {
                            s(d, "pull")
                        };
                        lines.push(Line::from(vec![
                            Span::styled("Get it on another computer", Style::new().add_modifier(Modifier::BOLD)),
                            Span::styled(
                                if s(d, "pull_ssh").is_empty() {
                                    String::new()
                                } else {
                                    format!("  ({}; s switches)", if self.ssh { "SSH" } else { "HTTPS" })
                                },
                                DIM,
                            ),
                        ]));
                        lines.extend(wrap(vec![Span::raw(pull)], width, "  ", "  "));
                        lines.push(Line::default());
                        lines.extend(wrap(
                            vec![Span::styled(
                                format!(
                                    "A wrapped line copies badly from the screen: optimux-tui projects git-lines {} prints each one whole.",
                                    self.project
                                ),
                                DIM,
                            )],
                            width,
                            "",
                            "",
                        ));
                    }
                }
            }
        }
        if !self.note.is_empty() {
            lines.push(Line::default());
            lines.extend(wrap(
                vec![Span::styled(self.note.clone(), Style::new().fg(Color::Yellow))],
                width,
                "",
                "",
            ));
        }
        self.scroll = self.scroll.min(lines.len().saturating_sub(area.height as usize));
        f.render_widget(
            Paragraph::new(lines.into_iter().skip(self.scroll).collect::<Vec<_>>()),
            area,
        );
    }

    fn sync_lines(&self, lines: &mut Vec<Line<'static>>, width: usize) {
        let d = &self.data;
        let files = self.files();
        let what = if self.kind == Kind::Push {
            if !files.is_empty() && self.commit_too {
                "Commits every change below and pushes"
            } else {
                "Pushes"
            }
        } else {
            "Brings in GitHub's new commits for"
        };
        let target = if s(d, "target").is_empty() {
            s(d, "branch")
        } else {
            s(d, "target")
        };
        lines.extend(wrap(
            vec![Span::raw(format!("{what} the branch {target} of {}.", s(d, "remote")))],
            width,
            "",
            "",
        ));
        if !s(d, "copy_of").is_empty() {
            lines.extend(wrap(
                vec![Span::styled(
                    format!(
                        "This is a copy of {}, so it uses its own branch and never changes {}'s.",
                        s(d, "copy_of"),
                        s(d, "copy_of")
                    ),
                    DIM,
                )],
                width,
                "",
                "",
            ));
        }
        if self.can_choose() {
            lines.push(Line::default());
            let pick = |on: bool| {
                if on {
                    Style::new().fg(Color::Black).bg(Color::LightCyan)
                } else {
                    DIM
                }
            };
            lines.push(Line::from(vec![
                Span::styled(
                    format!(" 1 Push the {} commit(s) already made ", self.commits()),
                    pick(!self.commit_too),
                ),
                Span::raw("  "),
                Span::styled(" 2 Commit the files below too, then push ", pick(self.commit_too)),
            ]));
        }
        let (problems, warnings) = self.problems();
        for p in &problems {
            lines.extend(wrap(
                vec![Span::styled(p.clone(), Style::new().fg(Color::Red))],
                width,
                "⛔ ",
                "   ",
            ));
        }
        for w in &warnings {
            lines.extend(wrap(
                vec![Span::styled(w.clone(), Style::new().fg(Color::Yellow))],
                width,
                "⚠ ",
                "  ",
            ));
        }
        lines.push(Line::default());
        if self.kind == Kind::Push {
            if !files.is_empty() {
                let more = num(d, "more").unwrap_or(0.0);
                lines.push(Line::from(Span::styled(
                    if self.commits_only() {
                        format!("Not committed, and left as they are ({:.0})", files.len() as f64 + more)
                    } else {
                        format!("Files to commit ({:.0})", files.len() as f64 + more)
                    },
                    Style::new().add_modifier(Modifier::BOLD),
                )));
                for fl in &files {
                    let style = if fl.get("warn").and_then(Value::as_bool) == Some(true) {
                        Style::new().fg(Color::Yellow)
                    } else {
                        Style::new()
                    };
                    lines.push(Line::from(vec![
                        Span::styled(
                            format!(
                                "  {}",
                                crate::ui::table::fit(&s(fl, "path"), width.saturating_sub(24).max(20))
                            ),
                            style,
                        ),
                        Span::styled(change_label(fl), DIM),
                    ]));
                }
                if more > 0.0 {
                    lines.push(Line::from(Span::styled(format!("  and {more:.0} more"), DIM)));
                }
            } else if num(d, "ahead").unwrap_or(0.0) > 0.0 {
                lines.push(Line::from(Span::raw(format!(
                    "No new changes; {:.0} commit(s) not on GitHub yet will be pushed.",
                    num(d, "ahead").unwrap_or(0.0)
                ))));
            }
            if !files.is_empty() && self.commit_too && problems.is_empty() {
                lines.push(Line::default());
                lines.push(Line::from(vec![
                    Span::styled("Commit message", Style::new().add_modifier(Modifier::BOLD)),
                    Span::styled(
                        if d.get("template").and_then(Value::as_bool) == Some(true) {
                            "  (this project has a commit template)"
                        } else {
                            ""
                        },
                        DIM,
                    ),
                ]));
                for l in self.message.lines() {
                    lines.extend(wrap(
                        vec![Span::styled(l.to_string(), Style::new().bg(Color::Indexed(236)))],
                        width,
                        "  ",
                        "  ",
                    ));
                }
                if self.message.trim().is_empty() {
                    lines.push(Line::from(Span::styled(
                        "  (empty: e writes it, m has it written)",
                        DIM,
                    )));
                }
            }
        } else {
            let commits = d.get("commits").and_then(Value::as_array).cloned().unwrap_or_default();
            if !commits.is_empty() {
                lines.push(Line::from(Span::styled(
                    format!(
                        "New on GitHub ({:.0} commit(s))",
                        num(d, "behind").unwrap_or(commits.len() as f64)
                    ),
                    Style::new().add_modifier(Modifier::BOLD),
                )));
                for c in &commits {
                    lines.push(Line::from(vec![
                        Span::styled(format!("  {} ", s(c, "sha")), Style::new().fg(Color::Yellow)),
                        Span::raw(s(c, "subject")),
                        Span::styled(format!("  {}", s(c, "author")), DIM),
                    ]));
                }
            }
            if !files.is_empty() {
                lines.push(Line::from(Span::styled(
                    format!("Files it changes ({})", files.len()),
                    Style::new().add_modifier(Modifier::BOLD),
                )));
                for fl in files.iter().take(30) {
                    lines.push(Line::from(vec![
                        Span::raw(format!("  {}  ", s(fl, "path"))),
                        Span::styled(s(fl, "status"), DIM),
                    ]));
                }
            }
        }
        lines.push(Line::default());
        if !warnings.is_empty() && problems.is_empty() {
            lines.push(Line::from(Span::styled(
                format!(
                    "[{}] I checked the warnings above (c)",
                    if self.confirmed { "x" } else { " " }
                ),
                Style::new().fg(Color::Yellow),
            )));
        }
        lines.extend(wrap(
            vec![Span::styled(
                if self.kind == Kind::Push {
                    "Never forced: if GitHub has newer commits, Optimux stops and asks you to update first."
                } else {
                    "Fast-forward only: your changes are never merged, overwritten or reset."
                },
                DIM,
            )],
            width,
            "",
            "",
        ));
        if self.busy {
            lines.push(Line::from(Span::styled(
                if self.kind == Kind::Push {
                    "Pushing…"
                } else {
                    "Updating…"
                },
                Style::new().fg(Color::LightCyan),
            )));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn changes_in_words() {
        assert_eq!(change_label(&json!({"status": "new", "added": 3})), "new · +3");
        assert_eq!(
            change_label(&json!({"status": "changed", "added": 1, "removed": 2})),
            "+1 −2"
        );
    }
}
