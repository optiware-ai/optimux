//! Dashboard → Routing, as in the web page: the Conductor, Simple and Complex models,
//! planning big tasks, and the scout; the projects with models of their own below.

use crossterm::event::{KeyCode, KeyEvent};
use ratatui::Frame;
use ratatui::layout::Rect;
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::Paragraph;
use serde_json::{Value, json};

use super::{Answer, Ctx, Section, draw_head, head_lines};
use crate::api::Method;
use crate::chat::text::{DIM, wrap};
use crate::cli::models::price;
use crate::cli::{num, s};
use crate::ui::form::{self, Field, Form, Kind};

pub const ROLES: &[(&str, &str, &str)] = &[
    (
        "conductor_model",
        "Conductor",
        "Decides which tier each new message needs: one short call per message; a fast, cheap model is enough. Off: no call, only escalation changes the tier.",
    ),
    (
        "simple_model",
        "Simple Executor",
        "Handles most requests: questions, explanations, commands and small edits. Pick a cheap, capable model.",
    ),
    (
        "complex_model",
        "Complex Executor",
        "For multi-step work, refactoring and design, and the fallback when Simple fails or its provider is down.",
    ),
];

pub const PLAN: &[(&str, &str, &str)] = &[
    ("off", "Off", "Never plan; /plan still works."),
    (
        "ask",
        "Ask first",
        "Show each plan and its estimate; nothing runs until you press Run.",
    ),
    (
        "auto",
        "Automatic",
        "Show each plan and start it at once; you can still stop it.",
    ),
];

#[derive(Default)]
pub struct Routing {
    form: Option<Form>,
    /// The configured models: (id, label, prices text).
    models: Vec<(String, String, String)>,
    routing: Value,
    settings: Value,
    config: Value,
    own: Value,
    msg: Option<(bool, String)>,
    /// Answers still awaited before the form can be built / the save is done.
    waiting: usize,
}

/// "in / out $ per 1M" of a model, as the page's priceTag.
pub fn price_tag(m: &Value) -> String {
    if num(m, "input_cost_per_mtok")
        .or(num(m, "output_cost_per_mtok"))
        .is_some()
    {
        format!(
            "{} / {}",
            price(num(m, "input_cost_per_mtok")),
            price(num(m, "output_cost_per_mtok"))
        )
    } else if let Some(l) = m.get("list_prices").filter(|v| !v.is_null()) {
        format!("{} / {}", price(num(l, "input")), price(num(l, "output")))
    } else {
        "price unknown".into()
    }
}

impl Routing {
    fn build(&mut self) {
        self.models.clear();
        for (k, ready) in [("available", true), ("requires_setup", false)] {
            for m in self.config.get(k).and_then(Value::as_array).into_iter().flatten() {
                let note = if ready { String::new() } else { " (key missing)".into() };
                self.models
                    .push((s(m, "id"), format!("{}{note}", s(m, "name")), price_tag(m)));
            }
        }
        let mut fields = Vec::new();
        for (key, label, help) in ROLES {
            let mut opts = vec![(String::new(), "– no model –".to_string())];
            if *key == "conductor_model" {
                opts.push(("off".into(), "Off: always start on Simple".into()));
            }
            // only usable models, plus the one chosen now even when its key is missing
            let now = s(&self.routing, key);
            for (id, name, tag) in &self.models {
                if !name.ends_with("(key missing)") || *id == now {
                    opts.push((id.clone(), format!("{name} · {tag} $/1M")));
                }
            }
            if !now.is_empty() && !opts.iter().any(|(v, _)| *v == now) {
                opts.push((now.clone(), format!("{now} (not configured)")));
            }
            fields.push(Field::new(key, *label, Kind::Choice(opts)).value(now).help(*help));
        }
        let plan = PLAN.iter().map(|(v, l, _)| (v.to_string(), l.to_string())).collect();
        let plan_now = self
            .settings
            .get("plan_mode")
            .and_then(Value::as_str)
            .unwrap_or("off")
            .to_string();
        fields.push(Field::new("plan_mode", "Plan big tasks", Kind::Choice(plan)).value(plan_now));
        let scout = self.settings.get("scout").and_then(Value::as_bool) != Some(false);
        fields.push(
            Field::new("scout", "Scout before Complex", Kind::Toggle)
                .value(if scout { "1" } else { "" })
                .help("The cheap model reads the project first, so the Complex model starts with a brief."),
        );
        let mut f = Form::new("Routing", fields).submit_label("Save routing").no_keys_line();
        f.focus = 0;
        self.form = Some(f);
        self.refresh_help();
    }

    /// The plan field says what the chosen choice does.
    fn refresh_help(&mut self) {
        let Some(f) = &mut self.form else { return };
        let plan = f.get("plan_mode").to_string();
        if let Some(field) = f.field_mut("plan_mode") {
            field.help = PLAN
                .iter()
                .find(|(v, ..)| *v == plan)
                .map(|(.., h)| h.to_string())
                .unwrap_or_default();
        }
    }

    fn save(&mut self, ctx: &Ctx) {
        let Some(f) = &mut self.form else { return };
        let body = json!({
            "conductor_model": f.get("conductor_model"),
            "simple_model": f.get("simple_model"),
            "complex_model": f.get("complex_model"),
        });
        f.busy = true;
        self.waiting = 2;
        ctx.call("saved", Method::Post, "/api/routing", Some(body));
        ctx.save_settings(
            "saved",
            json!({"plan_mode": f.get("plan_mode"), "scout": f.on("scout")}),
        );
    }
}

impl Section for Routing {
    fn load(&mut self, ctx: &Ctx) {
        self.waiting = 4;
        self.form = None;
        ctx.call("routing", Method::Get, "/api/routing", None);
        ctx.call("settings", Method::Get, "/api/settings", None);
        ctx.call("config", Method::Get, "/api/config", None);
        ctx.call("own", Method::Get, "/api/routing/projects", None);
    }

    fn key(&mut self, k: KeyEvent, ctx: &Ctx) -> bool {
        let Some(f) = &mut self.form else { return false };
        if k.code == KeyCode::Esc {
            return false;
        }
        let action = f.key(k);
        self.refresh_help();
        if action == form::Action::Submit {
            self.msg = None;
            self.save(ctx);
        }
        true
    }

    fn on_answer(&mut self, a: Answer, _ctx: &Ctx) {
        let (what, _) = a.parts();
        let what = what.to_string();
        match (what.as_str(), a.result) {
            ("saved", Ok(v)) => {
                if v.is_object() && v.get("plan_mode").is_some() {
                    self.settings = v;
                }
                self.waiting = self.waiting.saturating_sub(1);
                if self.waiting == 0 {
                    if let Some(f) = &mut self.form {
                        f.busy = false;
                    }
                    self.msg = Some((true, "Saved. New messages use it.".into()));
                }
            }
            ("saved", Err(e)) => {
                self.waiting = 0;
                if let Some(f) = &mut self.form {
                    f.busy = false;
                }
                self.msg = Some((false, e));
            }
            (part, Ok(v)) => {
                match part {
                    "routing" => self.routing = v,
                    "settings" => self.settings = v,
                    "config" => self.config = v,
                    "own" => self.own = v.get("projects").cloned().unwrap_or(Value::Null),
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
        "←→ change · Tab next · Ctrl+S save · Esc menu".into()
    }

    fn draw(&mut self, f: &mut Frame, area: Rect) -> Vec<(u16, usize)> {
        let width = area.width as usize;
        let body = draw_head(
            f,
            area,
            head_lines("Routing", "  ·  which model handles which tier", &self.msg, width),
        );
        let Some(form) = &self.form else {
            f.render_widget(Paragraph::new(Span::styled("Loading…", DIM)), body);
            return Vec::new();
        };
        let (mut lines, cursor) = form.lines(width);
        let _ = cursor;
        lines.push(Line::default());
        lines.push(Line::from(Span::styled(
            "Projects with their own models",
            Style::new().add_modifier(Modifier::BOLD),
        )));
        match self.own.as_object() {
            Some(o) if !o.is_empty() => {
                for (name, own) in o {
                    let mut spans = vec![Span::styled(format!("  {name}  "), Style::new().fg(Color::LightCyan))];
                    for (k, label, _) in ROLES {
                        let v = s(own, k);
                        if !v.is_empty() {
                            let v = if v == "off" { "Off".into() } else { v };
                            spans.push(Span::styled(format!("{}: {v}   ", label.split(' ').next().unwrap_or(label)), DIM));
                        }
                    }
                    lines.push(Line::from(spans));
                }
            }
            _ => lines.extend(wrap(
                vec![Span::styled(
                    "None: every project uses the models above. A project's own models are chosen on its page (Dashboard → Project).",
                    DIM,
                )],
                width,
                "  ",
                "  ",
            )),
        }
        f.render_widget(Paragraph::new(lines), body);
        Vec::new()
    }

    fn has_window(&self) -> bool {
        false
    }
}
