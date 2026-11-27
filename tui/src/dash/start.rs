//! Dashboard → Getting started: the setup steps in order (GET /api/onboarding), each
//! ticked by the server from the real settings. Enter goes to the section that does
//! it; "Choose your features" has its three choices here, as in the web page.

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
use crate::ui::form::{self, Field, Form, Kind};
use crate::ui::table::{Row, Table};

/// A step's target (the web page's tab id) → the Dashboard section, and the command.
pub const TARGETS: &[(&str, &str, &str)] = &[
    ("models", "Models", "optimux-tui models add"),
    ("routing", "Routing", "optimux-tui routing"),
    ("scope", "Scope", "optimux-tui scope"),
    ("execution", "Execution", "optimux-tui execution"),
    ("memory", "Memory", "optimux-tui start features"),
    ("projects", "Projects", "optimux-tui projects"),
    ("budgets", "Budgets", "optimux-tui budgets"),
    ("backups", "Backups", "optimux-tui backups snapshots take"),
    ("github", "GitHub", "optimux-tui github token"),
    ("chat", "Chat", "optimux-tui chat"),
];

pub fn target(t: &str) -> Option<(&'static str, &'static str)> {
    TARGETS
        .iter()
        .find(|(id, ..)| *id == t)
        .map(|(_, sec, cmd)| (*sec, *cmd))
}

pub const PLAN: &[(&str, &str)] = &[("off", "Off"), ("ask", "Ask first"), ("auto", "Automatic")];
pub const SKILLS: &[(&str, &str)] = &[("off", "Off"), ("manual", "Manual only"), ("auto", "Auto allowed")];

/// ✓ done, its number, or · for an optional step.
pub fn mark(step: &Value, i: usize) -> String {
    if step.get("done").and_then(Value::as_bool) == Some(true) {
        "✓".into()
    } else if step.get("optional").and_then(Value::as_bool) == Some(true) {
        "·".into()
    } else {
        format!("{}", i + 1)
    }
}

pub struct Start {
    data: Value,
    settings: Value,
    table: Table,
    features: Option<Form>,
    msg: Option<(bool, String)>,
    goto: Option<(&'static str, String)>,
}

impl Default for Start {
    fn default() -> Start {
        let mut table = Table::new(&[("", 3), ("Step", 0), ("Found", 0)]);
        table.empty = "Loading…".into();
        Start {
            data: Value::Null,
            settings: Value::Null,
            table,
            features: None,
            msg: None,
            goto: None,
        }
    }
}

impl Start {
    fn steps(&self) -> Vec<Value> {
        self.data
            .get("steps")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default()
    }

    fn step(&self) -> Option<Value> {
        let id = self.table.chosen()?.id.clone();
        self.steps().into_iter().find(|st| s(st, "id") == id)
    }

    fn rows(&mut self) {
        let next = s(&self.data, "next");
        let rows = self
            .steps()
            .iter()
            .enumerate()
            .map(|(i, st)| {
                let done = st.get("done").and_then(Value::as_bool) == Some(true);
                let optional = st.get("optional").and_then(Value::as_bool) == Some(true);
                let mark_style = if done {
                    Style::new().fg(Color::Green)
                } else if s(st, "id") == next {
                    Style::new().fg(Color::LightCyan).add_modifier(Modifier::BOLD)
                } else {
                    DIM
                };
                let title = format!("{}{}", s(st, "title"), if optional { "  (optional)" } else { "" });
                let title_style = if done { DIM } else { Style::new() };
                Row::new(s(st, "id"))
                    .styled(mark(st, i), mark_style)
                    .styled(title, title_style)
                    .styled(s(st, "check"), if done { Style::new().fg(Color::Green) } else { DIM })
            })
            .collect();
        self.table.set_rows(rows);
    }

    fn features_form(&self) -> Form {
        let opts = |o: &[(&str, &str)]| o.iter().map(|(a, b)| (a.to_string(), b.to_string())).collect();
        let set = |k: &str, d: &str| {
            let v = s(&self.settings, k);
            if v.is_empty() { d.to_string() } else { v }
        };
        let memory = self.settings.get("memory_curator").and_then(Value::as_bool) != Some(false);
        let mut f = Form::new(
            "Choose your features",
            vec![
                Field::new("memory_curator", "Update memory after each chat", Kind::Toggle)
                    .value(if memory { "1" } else { "" })
                    .help("A small cost per chat; the project file stays up to date"),
                Field::new("plan_mode", "Plan big tasks", Kind::Choice(opts(PLAN)))
                    .value(set("plan_mode", "off"))
                    .help("Big tasks are split into steps the cheap model can do"),
                Field::new("skills_mode", "Skills", Kind::Choice(opts(SKILLS)))
                    .value(set("skills_mode", "auto"))
                    .help("Reusable instructions, run with /name (or chosen by the model: Auto)"),
            ],
        )
        .intro("The defaults are fine to start. Saving any of them ticks this step.")
        .submit_label("Save");
        f.focus = 0;
        f
    }

    fn go(&mut self, step: &Value) {
        let t = s(step, "target");
        if s(step, "id") == "features" {
            self.features = Some(self.features_form());
            return;
        }
        match target(&t) {
            // the chat window: "" leaves the Dashboard without opening another chat
            Some(("Chat", _)) => self.goto = Some(("Chat", String::new())),
            Some((sec, _)) => self.goto = Some((sec, String::new())),
            None => self.msg = Some((false, format!("This step is done in the web page ({t})."))),
        }
    }
}

impl Section for Start {
    fn load(&mut self, ctx: &Ctx) {
        self.rows();
        ctx.call("status", Method::Get, "/api/onboarding", None);
        ctx.call("settings", Method::Get, "/api/settings", None);
    }

    fn key(&mut self, k: KeyEvent, ctx: &Ctx) -> bool {
        if let Some(f) = &mut self.features {
            match f.key(k) {
                form::Action::Cancel => self.features = None,
                form::Action::Submit => {
                    f.busy = true;
                    let changes = json!({
                        "memory_curator": f.on("memory_curator"),
                        "plan_mode": f.get("plan_mode"),
                        "skills_mode": f.get("skills_mode"),
                    });
                    ctx.save_settings("features", changes);
                }
                form::Action::None => {}
            }
            return true;
        }
        if self.table.key(k) {
            return true;
        }
        if k.modifiers.contains(KeyModifiers::CONTROL) {
            return false;
        }
        let act = |action: &str| {
            ctx.call(
                "status",
                Method::Post,
                "/api/onboarding",
                Some(json!({"action": action})),
            )
        };
        let step = self.step();
        match k.code {
            KeyCode::Enter => {
                if let Some(st) = step {
                    self.msg = None;
                    self.go(&st);
                }
            }
            KeyCode::Char('f') => self.features = Some(self.features_form()),
            KeyCode::Char('o')
                if self
                    .steps()
                    .iter()
                    .any(|st| s(st, "id") == "features" && st.get("done").and_then(Value::as_bool) != Some(true)) =>
            {
                self.msg = Some((true, "The features are fine as they are.".into()));
                act("features_ok");
            }
            KeyCode::Char('w') => {
                self.msg = Some((true, "The checklist shows on the web page's chat again.".into()));
                act("reopen");
            }
            KeyCode::Char('x') if self.data.get("show_in_chat").and_then(Value::as_bool) == Some(true) => {
                self.msg = Some((
                    true,
                    "The checklist no longer shows on the web page's chat (w brings it back).".into(),
                ));
                act("dismiss");
            }
            KeyCode::Char('r') => self.load(ctx),
            _ => return false,
        }
        true
    }

    fn paste(&mut self, text: &str) {
        if let Some(f) = &mut self.features {
            f.paste(text);
        }
    }

    fn on_answer(&mut self, a: Answer, ctx: &Ctx) {
        let what = a.parts().0.to_string();
        match (what.as_str(), a.result) {
            ("status", Ok(v)) => {
                self.data = v;
                self.rows();
                // the next step is chosen the first time
                if let Some(pos) = self.table.visible().iter().position(|r| r.id == s(&self.data, "next")) {
                    if self.table.selected == 0 {
                        self.table.selected = pos;
                    }
                }
            }
            ("settings", Ok(v)) => self.settings = v,
            ("features", Ok(v)) => {
                self.settings = v;
                self.features = None;
                self.msg = Some((true, "Saved: your features.".into()));
                ctx.call("status", Method::Get, "/api/onboarding", None);
            }
            ("features", Err(e)) => {
                if let Some(f) = &mut self.features {
                    f.busy = false;
                    f.error = Some(e);
                }
            }
            (_, Err(e)) => self.msg = Some((false, e)),
            _ => {}
        }
    }

    fn keys(&self) -> String {
        if self.features.is_some() {
            return String::new();
        }
        let mut k = String::from("↑↓ choose · Enter go there · f features");
        if self
            .steps()
            .iter()
            .any(|st| s(st, "id") == "features" && st.get("done").and_then(Value::as_bool) != Some(true))
        {
            k.push_str(" · o the features are fine");
        }
        if self.data.get("show_in_chat").and_then(Value::as_bool) == Some(true) {
            k.push_str(" · x hide it in the web chat");
        } else {
            k.push_str(" · w show it in the web chat");
        }
        k.push_str(" · r refresh");
        k
    }

    fn draw(&mut self, f: &mut Frame, area: Rect) -> Vec<(u16, usize)> {
        let width = area.width as usize;
        let ready = self.data.get("ready").and_then(Value::as_bool) == Some(true);
        let extra = if self.data.is_null() {
            String::new()
        } else if ready {
            "  ·  ✓ all set".into()
        } else {
            format!(
                "  ·  {}/{} done · each step ticks itself when it is done",
                self.data.get("done").and_then(Value::as_u64).unwrap_or(0),
                self.data.get("total").and_then(Value::as_u64).unwrap_or(0)
            )
        };
        let body = draw_head(f, area, head_lines("Getting started", &extra, &self.msg, width));
        if let Some(form) = &mut self.features {
            let title = vec![
                Line::from(Span::styled(
                    form.title.clone(),
                    Style::new().fg(Color::LightCyan).add_modifier(Modifier::BOLD),
                )),
                Line::default(),
            ];
            f.render_widget(
                Paragraph::new(title),
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
            return Vec::new();
        }
        // the table, then what the chosen step is about
        let n = (self.steps().len() as u16 + 2).min(body.height);
        let rows = self.table.draw(f, Rect { height: n, ..body });
        let mut lines = vec![Line::default()];
        if let Some(st) = self.step() {
            lines.push(Line::from(Span::styled(
                s(&st, "title"),
                Style::new().add_modifier(Modifier::BOLD),
            )));
            let why = if s(&st, "info").is_empty() {
                s(&st, "why")
            } else {
                s(&st, "info")
            };
            lines.extend(wrap(vec![Span::raw(why)], width, "", ""));
            if let Some((sec, cmd)) = target(&s(&st, "target")) {
                let place = if sec == "Chat" {
                    "the chat window".to_string()
                } else {
                    format!("Dashboard → {sec}")
                };
                lines.push(Line::from(vec![
                    Span::styled("Where: ", DIM),
                    Span::raw(place),
                    Span::styled(format!("  ·  as a command: {cmd}"), DIM),
                ]));
            }
        }
        f.render_widget(
            Paragraph::new(lines),
            Rect {
                y: body.y + n,
                height: body.height.saturating_sub(n),
                ..body
            },
        );
        rows
    }

    fn has_window(&self) -> bool {
        self.features.is_some() || self.table.filtering
    }

    fn click_row(&mut self, i: usize, _ctx: &Ctx) -> bool {
        if self.features.is_none() {
            self.table.selected = i.min(self.table.visible().len().saturating_sub(1));
        }
        false
    }

    fn take_goto(&mut self) -> Option<(&'static str, String)> {
        self.goto.take()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_target_has_a_place() {
        for t in [
            "models",
            "routing",
            "scope",
            "execution",
            "memory",
            "projects",
            "budgets",
            "backups",
            "github",
            "chat",
        ] {
            assert!(target(t).is_some(), "{t}");
        }
        assert_eq!(mark(&json!({"done": true}), 0), "✓");
        assert_eq!(mark(&json!({"optional": true}), 3), "·");
        assert_eq!(mark(&json!({}), 2), "3");
    }
}
