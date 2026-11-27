//! Dashboard → System, as in the web page: language, how long a left request keeps
//! running, the security checklist, versions and files, sign-in (sessions, sign out the
//! others, password, two-step codes, the access token), storage, models in cooldown.

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
use crate::cli::{num, s};
use crate::dash::projects::ago;
use crate::ui::confirm::{self, Confirm};
use crate::ui::form::{self, Field, Form, Kind};

pub const LANGUAGES: &[(&str, &str)] = &[("en", "English"), ("it", "Italiano")];

/// A browser's agent in words (the page's browserLabel, short).
pub fn agent_label(a: &str) -> String {
    let os = if a.contains("Windows") {
        "Windows"
    } else if a.contains("Mac OS") || a.contains("macOS") {
        "macOS"
    } else if a.contains("Android") {
        "Android"
    } else if a.contains("iPhone") || a.contains("iPad") {
        "iOS"
    } else if a.contains("linux") || a.contains("Linux") {
        "Linux"
    } else {
        ""
    };
    let what = if a.starts_with("optimux-tui") {
        "Terminal"
    } else if a.contains("Firefox") {
        "Firefox"
    } else if a.contains("Edg/") {
        "Edge"
    } else if a.contains("Chrome") {
        "Chrome"
    } else if a.contains("Safari") {
        "Safari"
    } else {
        "Browser"
    };
    if os.is_empty() {
        what.into()
    } else {
        format!("{what} on {os}")
    }
}

enum View {
    Main,
    Form(&'static str, Form),
    Ask(&'static str, Confirm),
    /// Two-step setup: the secret to type in the authenticator app, and the code form.
    Totp {
        secret: String,
        uri: String,
        form: Form,
    },
    /// A new access token, shown once.
    Token(String),
}

pub struct System {
    settings: Value,
    info: Value,
    auth: Value,
    security: Value,
    cooldowns: Value,
    view: View,
    scroll: usize,
    msg: Option<(bool, String)>,
}

impl Default for System {
    fn default() -> System {
        System {
            settings: Value::Null,
            info: Value::Null,
            auth: Value::Null,
            security: Value::Null,
            cooldowns: Value::Null,
            view: View::Main,
            scroll: 0,
            msg: None,
        }
    }
}

fn form1(title: &str, f: Vec<Field>, submit: &str) -> Form {
    let mut form = Form::new(title, f).submit_label(submit);
    form.focus = 0;
    form
}

impl System {
    fn fetch(&self, ctx: &Ctx) {
        ctx.call("settings", Method::Get, "/api/settings", None);
        ctx.call("info", Method::Get, "/api/system", None);
        ctx.call("auth", Method::Get, "/api/auth", None);
        ctx.call("security", Method::Get, "/api/security", None);
        ctx.call("cooldowns", Method::Get, "/api/cooldowns", None);
    }

    fn lines(&self, width: usize) -> Vec<Line<'static>> {
        let bold = Style::new().add_modifier(Modifier::BOLD);
        let mut out = Vec::new();
        let lang = s(&self.settings, "language");
        let lang = LANGUAGES
            .iter()
            .find(|(c, _)| *c == lang)
            .map(|(_, l)| *l)
            .unwrap_or("English");
        out.push(Line::from(vec![
            Span::styled("Language (l)            ", bold),
            Span::raw(lang.to_string()),
        ]));
        out.extend(wrap(
            vec![Span::styled(
                "Of every page, menu and server message. This terminal client is in English.",
                DIM,
            )],
            width,
            "  ",
            "  ",
        ));
        out.push(Line::from(vec![
            Span::styled("Leaving a request running (w)  ", bold),
            Span::raw(format!(
                "{} minutes",
                self.settings
                    .get("detach_wait_minutes")
                    .map(|v| v.to_string())
                    .unwrap_or_else(|| "30".into())
            )),
        ]));
        out.extend(wrap(
            vec![Span::styled(
                "How long a request left running by optimux-tui (Ctrl+D, another chat) waits for a window.",
                DIM,
            )],
            width,
            "  ",
            "  ",
        ));
        out.push(Line::default());
        out.push(Line::from(Span::styled("Security checklist", bold)));
        for c in self
            .security
            .get("checks")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
        {
            let ok = c.get("ok").and_then(Value::as_bool) == Some(true);
            let important = c.get("important").and_then(Value::as_bool) == Some(true);
            let (mark, colour) = if ok {
                ("✓", Color::Green)
            } else if important {
                ("!", Color::Red)
            } else {
                ("·", Color::Yellow)
            };
            out.push(Line::from(vec![
                Span::styled(format!("  {mark} "), Style::new().fg(colour)),
                Span::raw(crate::ui::table::fit(&s(c, "label"), 26)),
            ]));
            out.extend(wrap(vec![Span::styled(s(c, "detail"), DIM)], width, "      ", "      "));
        }
        out.push(Line::default());
        out.push(Line::from(vec![
            Span::styled("Optimux ", bold),
            Span::raw(s(&self.info, "optimux")),
            Span::styled("   LiteLLM ", bold),
            Span::raw(s(&self.info, "litellm")),
            Span::styled("   Python ", bold),
            Span::raw(s(&self.info, "python")),
        ]));
        out.push(Line::default());
        out.push(Line::from(Span::styled("Files", bold)));
        for (name, path) in self.info.get("paths").and_then(Value::as_object).into_iter().flatten() {
            out.push(Line::from(vec![
                Span::styled(format!("  {:<12}", name), DIM),
                Span::raw(path.as_str().unwrap_or("").to_string()),
            ]));
        }
        if !s(&self.settings, "projects_root").is_empty() {
            out.push(Line::from(vec![
                Span::styled(format!("  {:<12}", "projects"), DIM),
                Span::raw(s(&self.settings, "projects_root")),
            ]));
        }
        let moves = self
            .info
            .get("migrations")
            .and_then(Value::as_array)
            .map_or(0, Vec::len);
        if moves > 0 {
            out.push(Line::from(Span::styled(
                format!("  {moves} file move(s) into this layout (optimux-tui system show lists them)"),
                DIM,
            )));
        }
        out.push(Line::default());
        let sessions = self
            .auth
            .get("sessions")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        out.push(Line::from(vec![
            Span::styled(
                format!("Sign-in · {} signed-in browser(s) and terminal(s)", sessions.len()),
                bold,
            ),
            Span::styled(
                "   o sign out the others · P password · 2 two-step · T rotate the token",
                DIM,
            ),
        ]));
        for x in &sessions {
            let current = x.get("current").and_then(Value::as_bool) == Some(true);
            out.push(Line::from(vec![
                Span::styled(format!("  {:<16}", s(x, "ip")), DIM),
                Span::styled(format!("{:<14}", num(x, "last_seen").map(ago).unwrap_or_default()), DIM),
                Span::raw(agent_label(&s(x, "agent"))),
                Span::styled(
                    if current {
                        "  · this terminal".to_string()
                    } else {
                        String::new()
                    },
                    Style::new().fg(Color::LightCyan),
                ),
            ]));
        }
        out.push(Line::from(vec![
            Span::styled("  Two-step codes: ", DIM),
            Span::styled(
                if self.auth.get("totp").and_then(Value::as_bool) == Some(true) {
                    "on"
                } else {
                    "off"
                },
                Style::new().fg(if self.auth.get("totp").and_then(Value::as_bool) == Some(true) {
                    Color::Green
                } else {
                    Color::Yellow
                }),
            ),
            Span::styled("   To sign out this terminal: optimux-tui logout", DIM),
        ]));
        out.push(Line::default());
        let st = self.info.get("storage").cloned().unwrap_or(Value::Null);
        out.push(Line::from(Span::styled("Storage", bold)));
        out.push(Line::from(vec![
            Span::styled("  Database        ", DIM),
            Span::raw(format!("{}  ", s(&st, "path"))),
            Span::styled(
                format!(
                    "{} · {:.0} usage rows",
                    bytes(num(&st, "size_bytes").unwrap_or(0.0)),
                    num(&st, "usage_rows").unwrap_or(0.0)
                ),
                DIM,
            ),
        ]));
        if s(&st, "latest_backup").is_empty() {
            out.push(Line::from(Span::styled(
                "  Latest backup   none yet; one is made on every server start",
                DIM,
            )));
        } else {
            let check = st.get("backup_check").filter(|c| !c.is_null());
            let ok = check.is_none_or(|c| c.get("ok").and_then(Value::as_bool) != Some(false));
            out.push(Line::from(vec![
                Span::styled("  Latest backup   ", DIM),
                Span::raw(format!("{}  ", s(&st, "latest_backup"))),
                Span::styled(
                    format!(
                        "{} · {:.0} kept",
                        num(&st, "latest_backup_time").map(ago).unwrap_or_default(),
                        num(&st, "backups").unwrap_or(0.0)
                    ),
                    DIM,
                ),
                Span::styled(
                    if ok {
                        " · checked ✓".to_string()
                    } else {
                        format!(
                            " · ✕ failed its check: {}",
                            check.map(|c| s(c, "error")).unwrap_or_default()
                        )
                    },
                    Style::new().fg(if ok { Color::Green } else { Color::Red }),
                ),
            ]));
        }
        out.push(Line::default());
        out.push(Line::from(vec![
            Span::styled("Models in cooldown", bold),
            Span::styled("   c clear one · C clear all", DIM),
        ]));
        match self.cooldowns.as_object() {
            Some(o) if !o.is_empty() => {
                for (name, c) in o {
                    out.push(Line::from(vec![
                        Span::raw(format!("  {name}: ")),
                        Span::styled(s(c, "reason"), DIM),
                    ]));
                }
            }
            _ => out.push(Line::from(Span::styled("  None. All models are available.", DIM))),
        }
        out
    }
}

fn bytes(n: f64) -> String {
    if n >= 1e9 {
        format!("{:.1} GB", n / 1e9)
    } else if n >= 1e6 {
        format!("{:.1} MB", n / 1e6)
    } else {
        format!("{:.0} kB", n / 1e3)
    }
}

impl Section for System {
    fn load(&mut self, ctx: &Ctx) {
        self.fetch(ctx);
    }

    fn key(&mut self, k: KeyEvent, ctx: &Ctx) -> bool {
        let view = std::mem::replace(&mut self.view, View::Main);
        let (view, used) = match view {
            // the token leaves the screen with any key
            View::Token(_) => (View::Main, true),
            View::Ask(what, mut c) => match c.key(k) {
                confirm::Answer::Yes => {
                    match what {
                        "signout" => ctx.call("signed_out", Method::Post, "/api/auth/signout-others", Some(json!({}))),
                        "clear_all" => ctx.call("cleared", Method::Post, "/api/cooldowns/clear", Some(json!({}))),
                        _ => {}
                    }
                    (View::Main, true)
                }
                confirm::Answer::No => (View::Main, true),
                confirm::Answer::Waiting => (View::Ask(what, c), true),
            },
            View::Totp { secret, uri, mut form } => match form.key(k) {
                form::Action::Cancel => (View::Main, true),
                form::Action::Submit => {
                    form.busy = true;
                    ctx.call(
                        "totp_on",
                        Method::Post,
                        "/api/auth/totp/confirm",
                        Some(json!({"code": form.get("code").trim()})),
                    );
                    (View::Totp { secret, uri, form }, true)
                }
                form::Action::None => (View::Totp { secret, uri, form }, true),
            },
            View::Form(what, mut f) => match f.key(k) {
                form::Action::Cancel => (View::Main, true),
                form::Action::Submit => {
                    match what {
                        "language" => {
                            f.busy = true;
                            ctx.call(
                                "language",
                                Method::Post,
                                "/api/language",
                                Some(json!({"language": f.get("language")})),
                            );
                        }
                        "wait" => match f.get("minutes").trim().parse::<i64>() {
                            Ok(n) if (1..=1440).contains(&n) => {
                                f.busy = true;
                                ctx.save_settings("wait", json!({"detach_wait_minutes": n}));
                            }
                            _ => f.error = Some("A whole number of minutes from 1 to 1440.".into()),
                        },
                        "password" => {
                            if f.get("new").chars().count() < 12 {
                                f.error = Some("The new password needs at least 12 characters.".into());
                            } else if f.get("new") != f.get("again") {
                                f.error = Some("The two new passwords are not the same.".into());
                            } else {
                                f.busy = true;
                                ctx.call(
                                    "password",
                                    Method::Post,
                                    "/api/auth/password",
                                    Some(json!({"current": f.get("current"), "new": f.get("new")})),
                                );
                            }
                        }
                        "totp_start" => {
                            f.busy = true;
                            ctx.call(
                                "totp_start",
                                Method::Post,
                                "/api/auth/totp/start",
                                Some(json!({"password": f.get("password")})),
                            );
                        }
                        "totp_off" => {
                            f.busy = true;
                            ctx.call(
                                "totp_off",
                                Method::Post,
                                "/api/auth/totp/disable",
                                Some(json!({"password": f.get("password"), "code": f.get("code").trim()})),
                            );
                        }
                        "token" => {
                            f.busy = true;
                            ctx.call(
                                "token",
                                Method::Post,
                                "/api/system/rotate-token",
                                Some(json!({"current": f.get("token").trim()})),
                            );
                        }
                        "clear" => {
                            f.busy = true;
                            ctx.call(
                                "cleared",
                                Method::Post,
                                "/api/cooldowns/clear",
                                Some(json!({"model": f.get("model")})),
                            );
                        }
                        _ => {}
                    }
                    (View::Form(what, f), true)
                }
                form::Action::None => (View::Form(what, f), true),
            },
            View::Main => {
                if k.modifiers.contains(KeyModifiers::CONTROL) {
                    return {
                        self.view = View::Main;
                        false
                    };
                }
                self.msg = None;
                let v = match k.code {
                    KeyCode::Char('l') => {
                        let opts = LANGUAGES.iter().map(|(c, l)| (c.to_string(), l.to_string())).collect();
                        View::Form(
                            "language",
                            form1("Language", vec![Field::new("language", "Language", Kind::Choice(opts)).value(s(&self.settings, "language"))], "Save"),
                        )
                    }
                    KeyCode::Char('w') => View::Form(
                        "wait",
                        form1(
                            "Leaving a request running",
                            vec![Field::new("minutes", "Keep it running for (min)", Kind::Number)
                                .value(self.settings.get("detach_wait_minutes").map(|v| v.to_string()).unwrap_or_else(|| "30".into()))
                                .required()
                                .help("1 to 1440. A line that just drops waits 2 minutes, as before.")],
                            "Save",
                        ),
                    ),
                    KeyCode::Char('o') => View::Ask(
                        "signout",
                        Confirm::new("Sign out the others?", "Every other browser and terminal must sign in again. This terminal stays signed in.", "sign them out"),
                    ),
                    KeyCode::Char('P') => View::Form(
                        "password",
                        form1(
                            "Change the password",
                            vec![
                                Field::new("current", "Current password", Kind::Secret).required(),
                                Field::new("new", "New password", Kind::Secret).required().help("At least 12 characters. Every other browser is signed out."),
                                Field::new("again", "New password again", Kind::Secret).required(),
                            ],
                            "Change",
                        ),
                    ),
                    KeyCode::Char('2') if self.auth.get("totp").and_then(Value::as_bool) == Some(true) => View::Form(
                        "totp_off",
                        form1(
                            "Turn two-step codes off",
                            vec![Field::new("password", "Password", Kind::Secret).required(), Field::new("code", "Code", Kind::Text).required()],
                            "Turn off",
                        ),
                    ),
                    KeyCode::Char('2') => View::Form(
                        "totp_start",
                        form1(
                            "Set up two-step codes",
                            vec![Field::new("password", "Your password", Kind::Secret)
                                .required()
                                .help("Then you add Optimux to an authenticator app (Aegis, Google Authenticator, 1Password…).")],
                            "Set up",
                        ),
                    ),
                    KeyCode::Char('T') => View::Form(
                        "token",
                        form1(
                            "Rotate the access token",
                            vec![Field::new("token", "The current token", Kind::Secret)
                                .required()
                                .help("Scripts using the old token stop working. The new one is shown once (it's also in secrets.env).")],
                            "Rotate",
                        ),
                    ),
                    KeyCode::Char('c') => match self.cooldowns.as_object().filter(|o| !o.is_empty()) {
                        Some(o) => {
                            let opts = o.keys().map(|k| (k.clone(), k.clone())).collect();
                            View::Form("clear", form1("Clear a cooldown", vec![Field::new("model", "Model", Kind::Choice(opts))], "Clear"))
                        }
                        None => {
                            self.msg = Some((true, "No model is in cooldown.".into()));
                            View::Main
                        }
                    },
                    KeyCode::Char('C') => {
                        if self.cooldowns.as_object().is_some_and(|o| !o.is_empty()) {
                            View::Ask("clear_all", Confirm::new("Clear every cooldown?", "Optimux tries those models again from the next request.", "clear them"))
                        } else {
                            self.msg = Some((true, "No model is in cooldown.".into()));
                            View::Main
                        }
                    }
                    KeyCode::Char('r') => {
                        self.fetch(ctx);
                        View::Main
                    }
                    KeyCode::Up => {
                        self.scroll = self.scroll.saturating_sub(1);
                        View::Main
                    }
                    KeyCode::Down => {
                        self.scroll += 1;
                        View::Main
                    }
                    KeyCode::PageUp => {
                        self.scroll = self.scroll.saturating_sub(10);
                        View::Main
                    }
                    KeyCode::PageDown => {
                        self.scroll += 10;
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
            View::Form(_, f) | View::Totp { form: f, .. } => f.paste(text),
            _ => {}
        }
    }

    fn on_answer(&mut self, a: Answer, ctx: &Ctx) {
        let what = a.parts().0.to_string();
        let done = |me: &mut System, text: String| {
            me.msg = Some((true, text));
            me.view = View::Main;
            me.fetch(ctx);
        };
        match (what.as_str(), a.result) {
            ("settings", Ok(v)) => self.settings = v,
            ("info", Ok(v)) => self.info = v,
            ("auth", Ok(v)) => self.auth = v,
            ("security", Ok(v)) => self.security = v,
            ("cooldowns", Ok(v)) => self.cooldowns = v,
            ("language", Ok(v)) => done(
                self,
                format!(
                    "Language: {}. The web page and the server's messages use it.",
                    s(&v, "language")
                ),
            ),
            ("wait", Ok(v)) => {
                self.settings = v;
                done(self, "Saved.".into());
            }
            ("signed_out", Ok(v)) => done(
                self,
                format!(
                    "{} other sign-in(s) ended.",
                    v.get("ended").map(|e| e.to_string()).unwrap_or_default()
                ),
            ),
            ("password", Ok(v)) => done(
                self,
                format!(
                    "Password changed. {} other sign-in(s) ended.",
                    v.get("ended").map(|e| e.to_string()).unwrap_or_default()
                ),
            ),
            ("totp_start", Ok(v)) => {
                let mut form = form1(
                    "Turn two-step codes on",
                    vec![Field::new("code", "The code it shows", Kind::Text).required()],
                    "Turn on",
                );
                form.intro = "Add Optimux to your authenticator app with the key below (or the link), then type the 6-digit code it shows.".into();
                self.view = View::Totp {
                    secret: s(&v, "secret"),
                    uri: s(&v, "uri"),
                    form,
                };
            }
            ("totp_on", Ok(_)) => done(
                self,
                "Two-step codes are on: signing in asks for a code from now on.".into(),
            ),
            ("totp_off", Ok(_)) => done(self, "Two-step codes are off.".into()),
            ("token", Ok(v)) => {
                self.view = View::Token(s(&v, "token"));
                self.fetch(ctx);
            }
            ("cleared", Ok(v)) => {
                let n = v.get("cleared").and_then(Value::as_array).map_or(0, Vec::len);
                done(self, format!("{n} cooldown(s) cleared."));
            }
            (_, Err(e)) => match &mut self.view {
                View::Form(_, f) | View::Totp { form: f, .. } => {
                    f.busy = false;
                    f.error = Some(e);
                }
                _ => self.msg = Some((false, e)),
            },
            _ => {}
        }
    }

    fn keys(&self) -> String {
        match &self.view {
            View::Main => "l language · w request wait · o sign out others · P password · 2 two-step · T token · c/C cooldowns · ↑↓ scroll".into(),
            View::Token(_) => "Any key hides it".into(),
            _ => String::new(),
        }
    }

    fn draw(&mut self, f: &mut Frame, area: Rect) -> Vec<(u16, usize)> {
        let width = area.width as usize;
        let body = draw_head(
            f,
            area,
            head_lines(
                "System",
                "  ·  files, sign-in, security, storage and cooldowns",
                &self.msg,
                width,
            ),
        );
        let bold = Style::new().fg(Color::LightCyan).add_modifier(Modifier::BOLD);
        match &self.view {
            View::Main => {
                if self.info.is_null() {
                    f.render_widget(Paragraph::new(Span::styled("Loading…", DIM)), body);
                    return Vec::new();
                }
                let lines = self.lines(width);
                self.scroll = self.scroll.min(lines.len().saturating_sub(body.height as usize));
                f.render_widget(
                    Paragraph::new(lines.into_iter().skip(self.scroll).collect::<Vec<_>>()),
                    body,
                );
            }
            View::Form(_, form) => {
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
            }
            View::Ask(_, c) => {
                let mut lines = vec![Line::from(Span::styled(c.title.clone(), bold)), Line::default()];
                lines.extend(c.lines(width));
                f.render_widget(Paragraph::new(lines), body);
            }
            View::Totp { secret, uri, form } => {
                let mut lines = vec![Line::from(Span::styled(form.title.clone(), bold)), Line::default()];
                lines.extend(wrap(vec![Span::styled(form.intro.clone(), DIM)], width, "", ""));
                lines.push(Line::default());
                lines.push(Line::from(vec![
                    Span::styled("Key   ", DIM),
                    Span::styled(secret.clone(), Style::new().add_modifier(Modifier::BOLD)),
                ]));
                lines.extend(wrap(vec![Span::styled(uri.clone(), DIM)], width, "Link  ", "      "));
                lines.push(Line::default());
                let n = lines.len() as u16;
                f.render_widget(
                    Paragraph::new(lines),
                    Rect {
                        height: n.min(body.height),
                        ..body
                    },
                );
                let mut fm = form.clone();
                fm.intro.clear();
                fm.draw(
                    f,
                    Rect {
                        y: body.y + n,
                        height: body.height.saturating_sub(n),
                        ..body
                    },
                );
            }
            View::Token(t) => {
                let lines = vec![
                    Line::from(Span::styled("New access token", bold)),
                    Line::default(),
                    Line::from(Span::styled(
                        "Shown this once (it's also in secrets.env on the server). Copy it now:",
                        DIM,
                    )),
                    Line::default(),
                    Line::from(Span::styled(t.clone(), Style::new().add_modifier(Modifier::BOLD))),
                ];
                f.render_widget(Paragraph::new(lines), body);
            }
        }
        Vec::new()
    }

    fn has_window(&self) -> bool {
        !matches!(self.view, View::Main)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn agents_in_words() {
        assert_eq!(agent_label("optimux-tui/0.8.0 (linux)"), "Terminal on Linux");
        assert_eq!(
            agent_label(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153 Safari/537.36"
            ),
            "Chrome on Linux"
        );
        assert_eq!(
            agent_label("Mozilla/5.0 (Windows NT 10.0) Gecko/20100101 Firefox/140.0"),
            "Firefox on Windows"
        );
    }
}
