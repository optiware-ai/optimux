//! Dashboard → Project: every setting of one project in one place, as in the web page:
//! autonomy, budget, its own models, planning, memory updates and execution. A setting
//! left on "Follow global" uses the value set for all projects.

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use ratatui::Frame;
use ratatui::layout::Rect;
use ratatui::text::Span;
use ratatui::widgets::Paragraph;
use serde_json::{Map, Value, json};

use super::{Answer, Ctx, Section, draw_head, fmt, head_lines};
use crate::api::{Method, enc};
use crate::chat::text::{DIM, wrap};
use crate::cli::{num, s};
use crate::ui::form::{self, Field, Form, Kind};

pub const LEVELS: &[(&str, &str)] = &[
    ("supervised", "Supervised"),
    ("checkpointed", "Checkpointed"),
    ("autonomous", "Autonomous"),
];

/// The project's own settings (choices: "" follows the global value).
pub const CHOICES: &[(&str, &str)] = &[
    ("plan_mode", "Plan big tasks"),
    ("scout", "Scout before Complex"),
    ("memory_curator", "Memory updates"),
    ("sandbox_network", "Network for commands"),
];
pub const NUMBERS: &[(&str, &str)] = &[
    ("curator_idle_minutes", "Memory: idle minutes"),
    ("max_steps_simple", "Steps per turn: Simple"),
    ("max_steps_complex", "Steps per turn: Complex"),
    ("command_timeout", "Command timeout (s)"),
];
pub const MODELS: &[(&str, &str)] = &[
    ("conductor_model", "Conductor"),
    ("simple_model", "Simple"),
    ("complex_model", "Complex"),
];

/// A setting's value as a form field holds it ("" = follow global).
pub fn shown(v: Option<&Value>) -> String {
    match v {
        None | Some(Value::Null) => String::new(),
        Some(Value::String(t)) => t.clone(),
        Some(Value::Bool(b)) => b.to_string(),
        Some(Value::Number(n)) => n.to_string(),
        Some(other) => other.to_string(),
    }
}

/// A form value back to what the server takes ("" = null: follow global).
pub fn parsed(key: &str, v: &str) -> Value {
    let v = v.trim();
    if v.is_empty() {
        return Value::Null;
    }
    if NUMBERS.iter().any(|(k, _)| *k == key) {
        return v.parse::<i64>().map(Value::from).unwrap_or_else(|_| json!(v));
    }
    match v {
        "true" => json!(true),
        "false" => json!(false),
        _ => json!(v),
    }
}

fn on_off(v: Option<&Value>) -> &'static str {
    match v {
        Some(Value::Bool(true)) => "On",
        Some(Value::Bool(false)) => "Off",
        _ => "?",
    }
}

#[derive(Default)]
pub struct ProjectPage {
    name: String,
    names: Vec<String>,
    view: Value,
    autonomy: Value,
    config: Value,
    budgets: Value,
    overview: Value,
    active: String,
    form: Option<Form>,
    waiting: usize,
    saving: usize,
    msg: Option<(bool, String)>,
}

impl ProjectPage {
    fn fetch(&mut self, ctx: &Ctx) {
        self.form = None;
        self.waiting = 6;
        ctx.call("names", Method::Get, "/api/projects", None);
        ctx.call("autonomy", Method::Get, "/api/autonomy", None);
        ctx.call("config", Method::Get, "/api/config", None);
        ctx.call("budgets", Method::Get, "/api/budgets", None);
        ctx.call("overview", Method::Get, "/api/projects/overview", None);
        ctx.call("settings", Method::Get, "/api/settings", None);
    }

    fn fetch_view(&mut self, ctx: &Ctx) {
        self.form = None;
        self.waiting += 1;
        ctx.call(
            "view",
            Method::Get,
            format!("/api/projects/{}/settings", enc(&self.name)),
            None,
        );
    }

    fn model_name(&self, id: &str) -> String {
        ["available", "requires_setup"]
            .iter()
            .flat_map(|k| {
                self.config
                    .get(*k)
                    .and_then(Value::as_array)
                    .cloned()
                    .unwrap_or_default()
            })
            .find(|m| s(m, "id") == id)
            .map(|m| s(&m, "name"))
            .unwrap_or_else(|| id.to_string())
    }

    fn build(&mut self) {
        if self.name.is_empty() || self.view.is_null() {
            return;
        }
        let own = self.view.get("overrides").cloned().unwrap_or(Value::Null);
        let glob = self.view.get("global").cloned().unwrap_or(Value::Null);
        let mut fields = vec![
            Field::new(
                "project",
                "Project",
                Kind::Choice(self.names.iter().map(|n| (n.clone(), n.clone())).collect()),
            )
            .value(self.name.clone())
            .help("←/→ another project"),
        ];
        // autonomy
        let default = s(&self.autonomy, "default");
        let label_of = |id: &str| LEVELS.iter().find(|(v, _)| *v == id).map(|(_, l)| *l).unwrap_or("");
        let mut levels = vec![(String::new(), format!("Follow global ({})", label_of(&default)))];
        let blocked = s(&self.autonomy, "blocked");
        for (id, label) in LEVELS {
            if blocked.is_empty() || *id == "supervised" {
                levels.push((id.to_string(), label.to_string()));
            }
        }
        let level = self
            .autonomy
            .get("projects")
            .and_then(|p| p.get(&self.name))
            .map(|v| shown(Some(v)))
            .unwrap_or_default();
        let level_help = self
            .autonomy
            .get("levels")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .find(|l| {
                s(l, "id")
                    == if level.is_empty() {
                        default.as_str()
                    } else {
                        level.as_str()
                    }
            })
            .map(|l| s(l, "text"))
            .unwrap_or_default();
        fields.push(
            Field::new("autonomy", "What runs without asking", Kind::Choice(levels))
                .value(level)
                .help(if blocked.is_empty() { level_help } else { blocked }),
        );
        // budget
        let b = self.budgets.get(&self.name).filter(|v| !v.is_null());
        fields.push(
            Field::new("budget", "Monthly budget $", Kind::Number)
                .value(
                    b.and_then(|b| num(b, "monthly_usd"))
                        .map(|n| n.to_string())
                        .unwrap_or_default(),
                )
                .help(match b {
                    Some(b) => format!(
                        "{} of {} spent this month. Empty: no budget.",
                        fmt::cost(num(b, "spent").unwrap_or(0.0)),
                        fmt::cost(num(b, "monthly_usd").unwrap_or(0.0))
                    ),
                    None => "No budget: leave it empty for none.".into(),
                }),
        );
        fields.push(
            Field::new(
                "on_limit",
                "At 100%",
                Kind::Choice(vec![
                    ("block".into(), "Block".into()),
                    ("warn".into(), "Warn only".into()),
                ]),
            )
            .value(b.map(|b| s(b, "on_limit")).unwrap_or_else(|| "block".into())),
        );
        // models
        let stale = self.view.get("stale").cloned().unwrap_or(Value::Null);
        for (key, label) in MODELS {
            let g = s(&glob, key);
            let gname = if g == "off" {
                "Off".into()
            } else if g.is_empty() {
                "none".into()
            } else {
                self.model_name(&g)
            };
            let mut opts = vec![(String::new(), format!("Follow global ({gname})"))];
            if *key == "conductor_model" {
                opts.push(("off".into(), "Off (every message starts on Simple)".into()));
            }
            for (k, ready) in [("available", true), ("requires_setup", false)] {
                for m in self.config.get(k).and_then(Value::as_array).into_iter().flatten() {
                    let lock = if ready { "" } else { " (needs a key)" };
                    opts.push((s(m, "id"), format!("{}{lock}", s(m, "name"))));
                }
            }
            let now = shown(own.get(key));
            if !now.is_empty() && !opts.iter().any(|(v, _)| *v == now) {
                opts.push((now.clone(), format!("{now} (not configured)")));
            }
            let mut f = Field::new(key, format!("Model: {label}"), Kind::Choice(opts)).value(now);
            if let Some(old) = stale.get(key).filter(|v| !v.is_null()) {
                f = f.help(format!(
                    "{} was removed from Models: Routing's applies",
                    shown(Some(old))
                ));
            }
            fields.push(f);
        }
        // planning, memory updates, network
        for (key, label) in CHOICES {
            let g = glob.get(key);
            let opts = if *key == "plan_mode" {
                let gl = match shown(g).as_str() {
                    "ask" => "Ask first",
                    "auto" => "Automatic",
                    _ => "Off",
                };
                vec![
                    (String::new(), format!("Follow global ({gl})")),
                    ("off".into(), "Off".into()),
                    ("ask".into(), "Ask first".into()),
                    ("auto".into(), "Automatic".into()),
                ]
            } else {
                vec![
                    (String::new(), format!("Follow global ({})", on_off(g))),
                    ("true".into(), "On".into()),
                    ("false".into(), "Off".into()),
                ]
            };
            let mut f = Field::new(key, *label, Kind::Choice(opts)).value(shown(own.get(key)));
            if *key == "sandbox_network" {
                f = f.help("Matters only with the sandbox on (Dashboard → Execution).");
            }
            fields.push(f);
        }
        for (key, label) in NUMBERS {
            fields.push(
                Field::new(key, *label, Kind::Number)
                    .value(shown(own.get(key)))
                    .help(format!("Empty: follow global ({})", shown(glob.get(key)))),
            );
        }
        let mut f = Form::new(self.name.clone(), fields).submit_label("Save").no_keys_line();
        f.focus = 0;
        self.form = Some(f);
    }

    fn save(&mut self, ctx: &Ctx) {
        let Some(f) = &mut self.form else { return };
        if let Err(e) = f.check() {
            f.error = Some(e);
            return;
        }
        let own = self.view.get("overrides").cloned().unwrap_or(Value::Null);
        let mut changes = Map::new();
        for key in MODELS.iter().chain(CHOICES).chain(NUMBERS).map(|(k, _)| *k) {
            let new = parsed(key, f.get(key));
            let old = own.get(key).cloned().unwrap_or(Value::Null);
            if new != old {
                changes.insert(key.into(), new);
            }
        }
        let name = self.name.clone();
        self.saving = 0;
        if !changes.is_empty() {
            self.saving += 1;
            ctx.call(
                "saved",
                Method::Post,
                format!("/api/projects/{}/settings", enc(&name)),
                Some(Value::Object(changes)),
            );
        }
        let level_now = self
            .autonomy
            .get("projects")
            .and_then(|p| p.get(&name))
            .map(|v| shown(Some(v)))
            .unwrap_or_default();
        if f.get("autonomy") != level_now {
            self.saving += 1;
            let level = if f.get("autonomy").is_empty() {
                Value::Null
            } else {
                json!(f.get("autonomy"))
            };
            ctx.call(
                "saved",
                Method::Post,
                "/api/autonomy",
                Some(json!({"project": name, "level": level})),
            );
        }
        let b = self.budgets.get(&name).filter(|v| !v.is_null());
        let amount = f.number("budget").ok().flatten();
        if amount.is_some_and(|n| n <= 0.0) {
            f.error = Some("A budget must be more than 0 (empty for none).".into());
            self.saving = 0;
            return;
        }
        let (old_amount, old_limit) = (
            b.and_then(|b| num(b, "monthly_usd")),
            b.map(|b| s(b, "on_limit")).unwrap_or_else(|| "block".into()),
        );
        if amount != old_amount || (amount.is_some() && f.get("on_limit") != old_limit) {
            self.saving += 1;
            ctx.call(
                "saved",
                Method::Post,
                "/api/budgets",
                Some(json!({"project": name, "monthly_usd": amount, "on_limit": f.get("on_limit")})),
            );
        }
        if self.saving == 0 {
            self.msg = Some((true, "Nothing changed.".into()));
        } else {
            f.busy = true;
        }
    }
}

impl Section for ProjectPage {
    fn load(&mut self, ctx: &Ctx) {
        self.fetch(ctx);
    }

    fn open_with(&mut self, arg: &str) {
        self.name = arg.to_string();
        self.msg = None;
    }

    fn key(&mut self, k: KeyEvent, ctx: &Ctx) -> bool {
        let Some(f) = &mut self.form else { return false };
        if k.code == KeyCode::Esc {
            return false;
        }
        if k.modifiers.contains(KeyModifiers::CONTROL) && k.code == KeyCode::Char('a') {
            let name = self.name.clone();
            if name == self.active {
                self.msg = Some((true, format!("{name} is already the active project.")));
            } else {
                ctx.save_settings(format!("active:{name}"), json!({"active_project": name}));
            }
            return true;
        }
        let before = f.get("project").to_string();
        let action = f.key(k);
        let after = f.get("project").to_string();
        if after != before {
            self.name = after;
            self.msg = None;
            self.fetch_view(ctx);
            ctx.call("budgets", Method::Get, "/api/budgets", None);
            self.waiting += 1;
            return true;
        }
        if action == form::Action::Submit {
            self.msg = None;
            self.save(ctx);
        }
        true
    }

    fn on_answer(&mut self, a: Answer, ctx: &Ctx) {
        let (what, arg) = a.parts();
        let (what, arg) = (what.to_string(), arg.to_string());
        match (what.as_str(), a.result) {
            ("saved", Ok(_)) => {
                self.saving = self.saving.saturating_sub(1);
                if self.saving == 0 {
                    self.msg = Some((true, format!("Saved for {}.", self.name)));
                    self.waiting = 0;
                    self.fetch(ctx);
                    self.fetch_view(ctx);
                }
            }
            ("active", Ok(v)) => {
                self.active = s(&v, "active_project");
                self.msg = Some((true, format!("{arg} is the active project. The chat follows.")));
            }
            ("saved", Err(e)) | ("active", Err(e)) => {
                self.saving = 0;
                if let Some(f) = &mut self.form {
                    f.busy = false;
                }
                self.msg = Some((false, e));
            }
            (part, Ok(v)) => {
                match part {
                    "names" => {
                        self.names = v
                            .as_array()
                            .into_iter()
                            .flatten()
                            .filter_map(Value::as_str)
                            .map(str::to_string)
                            .collect()
                    }
                    "autonomy" => self.autonomy = v,
                    "config" => self.config = v,
                    "budgets" => self.budgets = v,
                    "overview" => self.overview = v,
                    "settings" => self.active = s(&v, "active_project"),
                    "view" => self.view = v,
                    _ => return,
                }
                self.waiting = self.waiting.saturating_sub(1);
                if self.waiting > 0 {
                    return;
                }
                // the project asked for, else the active one, else the first
                if !self.names.contains(&self.name) {
                    self.name = if self.names.contains(&self.active) {
                        self.active.clone()
                    } else {
                        self.names.first().cloned().unwrap_or_default()
                    };
                }
                if self.name.is_empty() {
                    return;
                }
                if s(&self.view, "project") != self.name {
                    self.fetch_view(ctx);
                } else {
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
        "←→ change · Tab next · Ctrl+S save · Ctrl+A make active · Esc menu".into()
    }

    fn draw(&mut self, f: &mut Frame, area: Rect) -> Vec<(u16, usize)> {
        let width = area.width as usize;
        let row = self
            .overview
            .as_array()
            .into_iter()
            .flatten()
            .find(|p| s(p, "project") == self.name)
            .cloned();
        let extra = match &row {
            Some(p) => format!(
                "  ·  {}{} this month · {}",
                if self.name == self.active { "active · " } else { "" },
                fmt::cost(num(p, "month_cost").unwrap_or(0.0)),
                match num(p, "chats").unwrap_or(0.0) as u64 {
                    1 => "1 chat".to_string(),
                    n => format!("{n} chats"),
                }
            ),
            None => String::new(),
        };
        let body = draw_head(f, area, head_lines("Project", &extra, &self.msg, width));
        if self.names.is_empty() && self.waiting == 0 {
            f.render_widget(
                Paragraph::new(Span::styled("No projects yet: create one in Projects.", DIM)),
                body,
            );
            return Vec::new();
        }
        let Some(form) = &self.form else {
            f.render_widget(Paragraph::new(Span::styled("Loading…", DIM)), body);
            return Vec::new();
        };
        let note = wrap(
            vec![Span::styled(
                "Everything here applies to this project only. Its memory: Dashboard → Memory. Git (push, update, status, identity): Dashboard → Projects, keys P U S I M.",
                DIM,
            )],
            width,
            "",
            "",
        );
        let n = note.len() as u16 + 1;
        f.render_widget(
            Paragraph::new(note),
            Rect {
                height: n.min(body.height),
                ..body
            },
        );
        let rest = Rect {
            y: body.y + n,
            height: body.height.saturating_sub(n),
            ..body
        };
        form.draw(f, rest);
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
    fn values_between_form_and_server() {
        assert_eq!(parsed("max_steps_simple", "12"), json!(12));
        assert_eq!(parsed("max_steps_simple", ""), Value::Null);
        assert_eq!(parsed("scout", "false"), json!(false));
        assert_eq!(parsed("plan_mode", "ask"), json!("ask"));
        assert_eq!(shown(Some(&json!(true))), "true");
        assert_eq!(shown(None), "");
    }
}
