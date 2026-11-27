//! Dashboard → Execution, as in the web page: the default autonomy level (and which
//! projects have their own), the sandbox, the command environment and limits, the steps
//! per turn. (The page's desktop notification is the terminal's bell here.)

use crossterm::event::{KeyCode, KeyEvent};
use ratatui::Frame;
use ratatui::layout::Rect;
use ratatui::style::{Color, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::Paragraph;
use serde_json::{Map, Value, json};

use super::{Answer, Ctx, Section, draw_head, head_lines};
use crate::api::Method;
use crate::chat::text::{DIM, wrap};
use crate::cli::s;
use crate::dash::project::LEVELS;
use crate::ui::form::{self, Field, Form, Kind};

/// The number settings of this page: (key, label, min, max).
pub const LIMITS: &[(&str, &str, i64, i64)] = &[
    ("limit_memory_mb", "Memory (MB), 0 none", 0, 1 << 20),
    ("limit_file_mb", "File size (MB), 0 none", 0, 1 << 20),
    ("limit_processes", "Processes, 0 none", 0, 1 << 20),
    ("command_timeout", "Timeout (seconds)", 10, 1800),
    ("max_steps_simple", "Steps per turn: Simple", 1, 100),
    ("max_steps_complex", "Steps per turn: Complex", 1, 100),
];

/// A list typed on one line: commas or spaces between the items.
pub fn split_list(v: &str) -> Vec<String> {
    v.split([',', ' ', '\n'])
        .map(str::trim)
        .filter(|x| !x.is_empty())
        .map(str::to_string)
        .collect()
}

/// Folders typed on one line: commas between them (a folder may hold spaces).
pub fn split_paths(v: &str) -> Vec<String> {
    v.split([',', '\n'])
        .map(str::trim)
        .filter(|x| !x.is_empty())
        .map(str::to_string)
        .collect()
}

fn joined(v: Option<&Value>) -> String {
    v.and_then(Value::as_array)
        .map(|a| a.iter().filter_map(Value::as_str).collect::<Vec<_>>().join(", "))
        .unwrap_or_default()
}

#[derive(Default)]
pub struct Execution {
    settings: Value,
    sandbox: Value,
    autonomy: Value,
    form: Option<Form>,
    waiting: usize,
    saving: usize,
    msg: Option<(bool, String)>,
}

impl Execution {
    fn build(&mut self) {
        let st = &self.settings;
        let blocked = s(&self.autonomy, "blocked");
        let levels = LEVELS
            .iter()
            .filter(|(id, _)| blocked.is_empty() || *id == "supervised")
            .map(|(id, l)| (id.to_string(), l.to_string()))
            .collect();
        let default = s(&self.autonomy, "default");
        let level_help = self
            .autonomy
            .get("levels")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .find(|l| s(l, "id") == default)
            .map(|l| s(l, "text"))
            .unwrap_or_default();
        let required = self.sandbox.get("required").and_then(Value::as_bool) == Some(true);
        let works = self.sandbox.get("works").and_then(Value::as_bool) == Some(true);
        let on = st.get("sandbox").and_then(Value::as_bool) == Some(true) || required;
        let mut sandbox = Field::new("sandbox", "Sandbox", Kind::Toggle).value(if on { "1" } else { "" });
        sandbox = if required {
            sandbox.locked().help(format!(
                "Forced on by {}.",
                if self.sandbox.get("server_mode").and_then(Value::as_bool) == Some(true) {
                    "OPTIMUX_SERVER"
                } else {
                    "OPTIMUX_REQUIRE_SANDBOX"
                }
            ))
        } else if !works && !on {
            sandbox
                .locked()
                .help(format!("Can't be turned on: {}", s(&self.sandbox, "error")))
        } else {
            sandbox.help("Every command runs in bubblewrap: only the working folder can change; home, keys and chats are hidden.")
        };
        let mut fields = vec![
            Field::new("level", "Default autonomy", Kind::Choice(levels))
                .value(default)
                .help(if blocked.is_empty() {
                    level_help
                } else {
                    format!("{blocked} Until then every project is Supervised.")
                }),
            sandbox,
            Field::new("sandbox_network", "Network in the sandbox", Kind::Toggle)
                .value(if st.get("sandbox_network").and_then(Value::as_bool) == Some(true) {
                    "1"
                } else {
                    ""
                })
                .help("On: pip, npm and git over HTTPS work. Off: no network inside the sandbox."),
            Field::new("ro_paths", "Extra read-only folders", Kind::Text)
                .value(joined(st.get("sandbox_ro_paths")))
                .help("Absolute paths, separated by commas: visible in the sandbox, never changed"),
            Field::new("command_env", "Extra variables", Kind::Text)
                .value(joined(st.get("command_env")))
                .help("Variable names commands may see, like VIRTUAL_ENV, JAVA_HOME (keys in secrets.env are refused)"),
        ];
        for (key, label, min, max) in LIMITS {
            let v = st.get(*key).map(|n| n.to_string()).unwrap_or_default();
            fields.push(
                Field::new(key, *label, Kind::Number)
                    .value(v)
                    .required()
                    .help(format!("{min} to {max}")),
            );
        }
        let mut f = Form::new("Execution", fields).submit_label("Save").no_keys_line();
        f.focus = 0;
        self.form = Some(f);
    }

    fn save(&mut self, ctx: &Ctx) {
        let Some(f) = &mut self.form else { return };
        let mut changes = Map::new();
        // the limits first: their own range says more than "a number"
        for (key, label, min, max) in LIMITS {
            let n = f
                .get(key)
                .trim()
                .parse::<i64>()
                .ok()
                .filter(|n| (*min..=*max).contains(n));
            let Some(n) = n else {
                f.error = Some(format!("{label}: a whole number from {min} to {max}."));
                return;
            };
            if self.settings.get(*key).and_then(Value::as_i64) != Some(n) {
                changes.insert(key.to_string(), json!(n));
            }
        }
        if let Err(e) = f.check() {
            f.error = Some(e);
            return;
        }
        let flag = |v: bool| json!(v);
        if self.sandbox.get("required").and_then(Value::as_bool) != Some(true) {
            let on = f.on("sandbox");
            if self.settings.get("sandbox").and_then(Value::as_bool).unwrap_or(false) != on {
                changes.insert("sandbox".into(), flag(on));
            }
        }
        let net = f.on("sandbox_network");
        if self
            .settings
            .get("sandbox_network")
            .and_then(Value::as_bool)
            .unwrap_or(false)
            != net
        {
            changes.insert("sandbox_network".into(), flag(net));
        }
        for (field, key) in [("ro_paths", "sandbox_ro_paths"), ("command_env", "command_env")] {
            // folders may hold spaces: only commas part them
            let list = if field == "ro_paths" {
                split_paths(f.get(field))
            } else {
                split_list(f.get(field))
            };
            if joined(self.settings.get(key)) != list.join(", ") {
                changes.insert(key.into(), json!(list));
            }
        }
        self.saving = 0;
        if !changes.is_empty() {
            self.saving += 1;
            ctx.save_settings("saved", Value::Object(changes));
        }
        if f.get("level") != s(&self.autonomy, "default") {
            self.saving += 1;
            ctx.call(
                "saved",
                Method::Post,
                "/api/autonomy",
                Some(json!({"project": null, "level": f.get("level")})),
            );
        }
        if self.saving == 0 {
            self.msg = Some((true, "Nothing changed.".into()));
        } else {
            f.busy = true;
        }
    }
}

impl Section for Execution {
    fn load(&mut self, ctx: &Ctx) {
        self.waiting = 3;
        ctx.call("settings", Method::Get, "/api/settings", None);
        ctx.call("sandbox", Method::Get, "/api/sandbox?refresh=true", None);
        ctx.call("autonomy", Method::Get, "/api/autonomy", None);
    }

    fn key(&mut self, k: KeyEvent, ctx: &Ctx) -> bool {
        let Some(f) = &mut self.form else { return false };
        if k.code == KeyCode::Esc {
            return false;
        }
        if f.key(k) == form::Action::Submit {
            self.msg = None;
            self.save(ctx);
        }
        true
    }

    fn paste(&mut self, text: &str) {
        if let Some(f) = &mut self.form {
            f.paste(text);
        }
    }

    fn on_answer(&mut self, a: Answer, ctx: &Ctx) {
        let what = a.parts().0.to_string();
        match (what.as_str(), a.result) {
            ("saved", Ok(_)) => {
                self.saving = self.saving.saturating_sub(1);
                if self.saving == 0 {
                    self.msg = Some((true, "Saved. New commands use it.".into()));
                    self.load(ctx);
                }
            }
            ("saved", Err(e)) => {
                // what you typed stays, with the server's reason; Esc and back reloads
                self.saving = 0;
                if let Some(f) = &mut self.form {
                    f.busy = false;
                    f.error = Some(e);
                }
            }
            (part, Ok(v)) => {
                match part {
                    "settings" => self.settings = v,
                    "sandbox" => self.sandbox = v,
                    "autonomy" => self.autonomy = v,
                    _ => return,
                }
                self.waiting = self.waiting.saturating_sub(1);
                if self.waiting == 0 {
                    self.build();
                }
            }
            (_, Err(e)) => {
                self.waiting = 0;
                self.msg = Some((false, e));
            }
        }
    }

    fn keys(&self) -> String {
        "Space switch · ←→ change · Tab next · Ctrl+S save · Esc menu".into()
    }

    fn draw(&mut self, f: &mut Frame, area: Rect) -> Vec<(u16, usize)> {
        let width = area.width as usize;
        let body = draw_head(
            f,
            area,
            head_lines(
                "Execution",
                "  ·  what runs without asking, and where",
                &self.msg,
                width,
            ),
        );
        let mut top = Vec::new();
        if !self.sandbox.is_null() {
            let (text, colour) = if self.sandbox.get("works").and_then(Value::as_bool) == Some(true) {
                (
                    format!(
                        "✓ {} works here",
                        if s(&self.sandbox, "version").is_empty() {
                            "bubblewrap".into()
                        } else {
                            s(&self.sandbox, "version")
                        }
                    ),
                    Color::Green,
                )
            } else {
                (format!("✗ {}", s(&self.sandbox, "error")), Color::Yellow)
            };
            top.push(Line::from(Span::styled(text, Style::new().fg(colour))));
        }
        if let Some(own) = self
            .autonomy
            .get("projects")
            .and_then(Value::as_object)
            .filter(|o| !o.is_empty())
        {
            let list: Vec<String> = own
                .iter()
                .map(|(p, l)| format!("{p}: {}", l.as_str().unwrap_or("")))
                .collect();
            top.extend(wrap(
                vec![Span::styled(
                    format!(
                        "Projects with their own level: {} (change on their Project page)",
                        list.join(", ")
                    ),
                    DIM,
                )],
                width,
                "",
                "",
            ));
        }
        if let Some(h) = self.autonomy.get("hard_limits").and_then(Value::as_array) {
            let items: Vec<&str> = h.iter().filter_map(Value::as_str).collect();
            top.extend(wrap(
                vec![Span::styled(
                    format!(
                        "Always asked, at every level: {}; creating or archiving a project.",
                        items.join("; ")
                    ),
                    DIM,
                )],
                width,
                "",
                "",
            ));
        }
        top.push(Line::default());
        let n = (top.len() as u16).min(body.height);
        f.render_widget(Paragraph::new(top), Rect { height: n, ..body });
        let rest = Rect {
            y: body.y + n,
            height: body.height.saturating_sub(n),
            ..body
        };
        match &self.form {
            Some(form) => form.draw(f, rest),
            None => f.render_widget(Paragraph::new(Span::styled("Loading…", DIM)), rest),
        }
        Vec::new()
    }

    fn has_window(&self) -> bool {
        false
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn lists_on_one_line() {
        assert_eq!(
            split_list(" VIRTUAL_ENV, JAVA_HOME  GOPATH,"),
            vec!["VIRTUAL_ENV", "JAVA_HOME", "GOPATH"]
        );
        assert!(split_list("  ").is_empty());
        assert_eq!(split_paths("/a b/c, /d"), vec!["/a b/c", "/d"]);
    }
}
