//! Dashboard → GitHub, as in the web page: what Optimux needs to create GitHub
//! repositories (the GitHub CLI and a sign-in), the token (typed hidden, checked with
//! GitHub before it is saved, never shown), and who commits.

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use ratatui::Frame;
use ratatui::layout::Rect;
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::Paragraph;
use serde_json::{Value, json};

use super::{Answer, Ctx, Section, draw_head, head_lines};
use crate::api::Method;
use crate::chat::text::{DIM, wrap};
use crate::cli::s;
use crate::ui::confirm::{self, Confirm};
use crate::ui::form::{self, Field, Form, Kind};

pub const TOKEN_STEPS: &[&str] = &[
    "Open https://github.com/settings/personal-access-tokens/new (a new fine-grained token), name it Optimux.",
    "Repository access: All repositories.",
    "Permissions → Repository: Administration Read and write (creates repositories), Contents Read and write (pushes the first commit).",
    "Generate it, copy it, and paste it here (t). A classic token with the repo scope works too.",
];

enum View {
    Main,
    Token(Form),
    Identity(Form),
    Remove(Confirm),
}

pub struct GitHub {
    info: Value,
    view: View,
    busy: bool,
    msg: Option<(bool, String)>,
}

impl Default for GitHub {
    fn default() -> GitHub {
        GitHub {
            info: Value::Null,
            view: View::Main,
            busy: false,
            msg: None,
        }
    }
}

impl Section for GitHub {
    fn load(&mut self, ctx: &Ctx) {
        ctx.call("info", Method::Get, "/api/github?fresh=true", None);
    }

    fn key(&mut self, k: KeyEvent, ctx: &Ctx) -> bool {
        let view = std::mem::replace(&mut self.view, View::Main);
        let (view, used) = match view {
            View::Token(mut f) => match f.key(k) {
                form::Action::Cancel => (View::Main, true),
                form::Action::Submit => {
                    f.busy = true;
                    ctx.call(
                        "saved",
                        Method::Post,
                        "/api/github",
                        Some(json!({"token": f.get("token").trim()})),
                    );
                    (View::Token(f), true)
                }
                form::Action::None => (View::Token(f), true),
            },
            View::Identity(mut f) => match f.key(k) {
                form::Action::Cancel => (View::Main, true),
                form::Action::Submit => {
                    f.busy = true;
                    ctx.call(
                        "identity",
                        Method::Post,
                        "/api/github/identity",
                        Some(json!({"git_name": f.get("name").trim(), "git_email": f.get("email").trim()})),
                    );
                    (View::Identity(f), true)
                }
                form::Action::None => (View::Identity(f), true),
            },
            View::Remove(mut c) => match c.key(k) {
                confirm::Answer::Yes => {
                    ctx.call("removed", Method::Delete, "/api/github", None);
                    (View::Main, true)
                }
                confirm::Answer::No => (View::Main, true),
                confirm::Answer::Waiting => (View::Remove(c), true),
            },
            View::Main => {
                if k.modifiers.contains(KeyModifiers::CONTROL) || self.busy {
                    return {
                        self.view = View::Main;
                        false
                    };
                }
                let saved = self.info.get("token_saved").and_then(Value::as_bool) == Some(true);
                let v = match k.code {
                    KeyCode::Char('t') => {
                        let mut f = Form::new(
                            if saved { "Replace the token" } else { "Add a token" },
                            vec![Field::new("token", "Token", Kind::Secret).required().help("github_pat_… or ghp_…; shown as dots, never again")],
                        )
                        .intro("Optimux asks GitHub who the token belongs to before saving it. It is kept in secrets.env, used only by New project and Push: commands the model runs don't get it.")
                        .submit_label("Check and save");
                        f.focus = 0;
                        View::Token(f)
                    }
                    KeyCode::Char('k') if saved => {
                        self.busy = true;
                        self.msg = Some((true, "Asking GitHub…".into()));
                        ctx.call("checked", Method::Post, "/api/github/check", Some(json!({})));
                        View::Main
                    }
                    KeyCode::Char('x') if saved => View::Remove(Confirm::new(
                        "Remove the GitHub token?",
                        "It is removed from secrets.env. New projects can't create GitHub repositories until you add one (or use gh auth login).",
                        "remove it",
                    )),
                    KeyCode::Char('i') => {
                        let mut f = Form::new(
                            "Who commits",
                            vec![
                                Field::new("name", "Name", Kind::Text).value(s(&self.info, "git_name")),
                                Field::new("email", "Email", Kind::Text)
                                    .value(s(&self.info, "git_email"))
                                    .help("GitHub's private address looks like 12345+login@users.noreply.github.com"),
                            ],
                        )
                        .intro("On the first commit of new projects and on commits the model makes (when you approve them). Empty: git's own settings.")
                        .submit_label("Save");
                        f.focus = 0;
                        View::Identity(f)
                    }
                    KeyCode::Char('r') => {
                        self.load(ctx);
                        View::Main
                    }
                    _ => {
                        self.view = View::Main;
                        return false;
                    }
                };
                (v, true)
            }
        };
        self.view = view;
        used
    }

    fn paste(&mut self, text: &str) {
        match &mut self.view {
            View::Token(f) | View::Identity(f) => f.paste(text),
            _ => {}
        }
    }

    fn on_answer(&mut self, a: Answer, ctx: &Ctx) {
        let what = a.parts().0.to_string();
        let who = |v: &Value| {
            let mut t = format!("@{}", s(v, "login"));
            if !s(v, "name").is_empty() {
                t.push_str(&format!(" ({})", s(v, "name")));
            }
            t
        };
        match (what.as_str(), a.result) {
            ("info", Ok(v)) => self.info = v,
            ("saved", Ok(v)) => {
                let mut text = format!("Token saved: {}.", who(&v));
                for k in ["warning", "hint"] {
                    if !s(&v, k).is_empty() {
                        text.push_str(&format!(" {}", s(&v, k)));
                    }
                }
                if v.get("identity_set").and_then(Value::as_bool) == Some(true) {
                    text.push_str(" Who commits was filled in from it.");
                }
                // saved: a warning (a missing permission) is said, but it is saved
                self.msg = Some((true, text));
                self.view = View::Main;
                self.load(ctx);
            }
            ("checked", Ok(v)) => {
                self.busy = false;
                let mut text = format!("The token works: {}.", who(&v));
                if !s(&v, "warning").is_empty() {
                    text.push_str(&format!(" {}", s(&v, "warning")));
                }
                self.msg = Some((s(&v, "warning").is_empty(), text));
            }
            ("removed", Ok(_)) => {
                self.msg = Some((true, "Token removed.".into()));
                self.load(ctx);
            }
            ("identity", Ok(_)) => {
                self.msg = Some((true, "Saved: who commits.".into()));
                self.view = View::Main;
                self.load(ctx);
            }
            (_, Err(e)) => {
                self.busy = false;
                match &mut self.view {
                    View::Token(f) | View::Identity(f) => {
                        f.busy = false;
                        f.error = Some(e);
                    }
                    _ => self.msg = Some((false, e)),
                }
            }
            _ => {}
        }
    }

    fn keys(&self) -> String {
        match self.view {
            View::Main => {
                if self.info.get("token_saved").and_then(Value::as_bool) == Some(true) {
                    "t replace the token · k check it · x remove it · i who commits · r refresh · Esc menu".into()
                } else {
                    "t add a token · i who commits · r refresh · Esc menu".into()
                }
            }
            _ => String::new(),
        }
    }

    fn draw(&mut self, f: &mut Frame, area: Rect) -> Vec<(u16, usize)> {
        let width = area.width as usize;
        let body = draw_head(
            f,
            area,
            head_lines(
                "GitHub",
                "  ·  creating repositories for new projects, and who commits",
                &self.msg,
                width,
            ),
        );
        let bold = Style::new().add_modifier(Modifier::BOLD);
        match &self.view {
            View::Main => {
                if self.info.is_null() {
                    f.render_widget(Paragraph::new(Span::styled("Loading…", DIM)), body);
                    return Vec::new();
                }
                let i = &self.info;
                let mark = |ok: bool| {
                    Span::styled(
                        if ok { "✓ " } else { "✗ " },
                        Style::new().fg(if ok { Color::Green } else { Color::Red }),
                    )
                };
                let gh = i.get("gh").and_then(Value::as_bool) == Some(true);
                let mut lines = vec![Line::from(Span::styled("Status", bold))];
                lines.push(Line::from(vec![
                    mark(gh),
                    Span::raw("GitHub CLI  "),
                    Span::styled(
                        if gh {
                            if s(i, "gh_version").is_empty() { "gh".to_string() } else { s(i, "gh_version") }
                        } else {
                            "not installed on the Optimux machine (sudo apt install gh, sudo dnf install gh, or cli.github.com)".into()
                        },
                        DIM,
                    ),
                ]));
                let source = s(i, "source");
                lines.push(Line::from(vec![
                    mark(!source.is_empty()),
                    Span::raw("Sign-in     "),
                    Span::styled(
                        match source.as_str() {
                            "optimux" => format!(
                                "token saved in Optimux{}",
                                if s(i, "login").is_empty() {
                                    String::new()
                                } else {
                                    format!(" · @{}", s(i, "login"))
                                }
                            ),
                            "gh" => {
                                "the GitHub CLI's own sign-in (gh auth login); a token saved here would take its place"
                                    .into()
                            }
                            _ => "not set up yet: t adds a token".into(),
                        },
                        DIM,
                    ),
                ]));
                if i.get("ready").and_then(Value::as_bool) != Some(true) && !s(i, "reason").is_empty() {
                    lines.extend(wrap(
                        vec![Span::styled(s(i, "reason"), Style::new().fg(Color::Yellow))],
                        width,
                        "  ",
                        "  ",
                    ));
                }
                if i.get("sandbox").and_then(Value::as_bool) == Some(false) {
                    lines.extend(wrap(
                        vec![Span::styled(
                            "The sandbox is off: an approved command runs with Optimux's permissions, so it could read secrets.env or use gh's sign-in. Review commands before approving them, or turn the sandbox on (Execution).",
                            Style::new().fg(Color::Yellow),
                        )],
                        width,
                        "⚠ ",
                        "  ",
                    ));
                }
                lines.push(Line::default());
                lines.push(Line::from(Span::styled(
                    if i.get("token_saved").and_then(Value::as_bool) == Some(true) {
                        "Replace the token (t)"
                    } else {
                        "Add a token (t)"
                    },
                    bold,
                )));
                for (n, step) in TOKEN_STEPS.iter().enumerate() {
                    lines.extend(wrap(
                        vec![Span::styled(step.to_string(), DIM)],
                        width,
                        &format!("  {}. ", n + 1),
                        "     ",
                    ));
                }
                lines.push(Line::default());
                lines.push(Line::from(Span::styled("Who commits (i)", bold)));
                let name = s(i, "git_name");
                let email = s(i, "git_email");
                lines.push(Line::from(Span::styled(
                    if name.is_empty() && email.is_empty() {
                        "  git's own settings (git config --global user.name)".to_string()
                    } else {
                        format!("  {name} <{email}>")
                    },
                    DIM,
                )));
                f.render_widget(Paragraph::new(lines), body);
            }
            View::Token(form) | View::Identity(form) => {
                let title = vec![
                    Line::from(Span::styled(form.title.clone(), bold.fg(Color::LightCyan))),
                    Line::default(),
                ];
                f.render_widget(Paragraph::new(title), Rect { height: 2, ..body });
                form.draw(
                    f,
                    Rect {
                        y: body.y + 2,
                        height: body.height.saturating_sub(2),
                        ..body
                    },
                );
            }
            View::Remove(c) => {
                let mut lines = vec![
                    Line::from(Span::styled(c.title.clone(), bold.fg(Color::Red))),
                    Line::default(),
                ];
                lines.extend(c.lines(width));
                f.render_widget(Paragraph::new(lines), body);
            }
        }
        Vec::new()
    }

    fn has_window(&self) -> bool {
        !matches!(self.view, View::Main)
    }
}
