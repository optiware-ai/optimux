//! Dashboard → Budgets, as in the web page: each project's monthly budget (USD, the
//! calendar month) and what happens at 100% (Block or Warn); deleted projects apart;
//! the cost warnings below.

use crossterm::event::{KeyCode, KeyEvent};
use ratatui::Frame;
use ratatui::layout::Rect;
use ratatui::style::{Color, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::Paragraph;
use serde_json::{Value, json};

use super::{Answer, Ctx, Section, draw_head, fmt, head_lines};
use crate::api::Method;
use crate::chat::text::{DIM, wrap};
use crate::cli::{num, s};
use crate::ui::form::{self, Field, Form, Kind};
use crate::ui::table::{Row, Table};

pub const WARNINGS: &[(&str, &str)] = &[
    ("confirm_update_memory", "Warn me before \"Update memory now\""),
    ("confirm_escalate", "Warn me before \"Escalate ↑\""),
    ("confirm_auto_escalate", "Ask before a stuck request moves to Complex"),
];

enum View {
    List,
    Edit(String, Form),
    Warnings(Form),
}

pub struct Budgets {
    table: Table,
    projects: Vec<String>,
    budgets: Value,
    usage: Value,
    deleted: Value,
    settings: Value,
    view: View,
    msg: Option<(bool, String)>,
}

impl Default for Budgets {
    fn default() -> Budgets {
        let mut table = Table::new(&[
            ("Project", 0),
            ("This month", 12),
            ("Budget", 10),
            ("At 100%", 8),
            ("", 16),
        ]);
        table.empty = "No projects yet.".into();
        Budgets {
            table,
            projects: Vec::new(),
            budgets: Value::Null,
            usage: Value::Null,
            deleted: Value::Null,
            settings: Value::Null,
            view: View::List,
            msg: None,
        }
    }
}

/// A budget's state in words: (text, colour).
pub fn state_text(b: &Value) -> (String, Color) {
    if b.get("paused").and_then(Value::as_bool) == Some(true) {
        return ("paused (archived)".into(), Color::DarkGray);
    }
    let pct = num(b, "ratio").map(fmt::percent).unwrap_or_default();
    match b.get("state").and_then(Value::as_str) {
        Some("over") if s(b, "on_limit") == "block" => (format!("{pct} · blocked"), Color::Red),
        Some("over") => (format!("{pct} · over"), Color::Red),
        Some("warning") => (format!("{pct} · near"), Color::Yellow),
        _ => (pct, Color::Green),
    }
}

impl Budgets {
    fn month_cost(&self, p: &str) -> f64 {
        self.usage
            .pointer(&format!(
                "/this_month/by_project/{}",
                p.replace('~', "~0").replace('/', "~1")
            ))
            .and_then(|b| num(b, "cost"))
            .unwrap_or(0.0)
    }

    fn rows(&mut self) {
        let deleted = self.deleted.as_object().cloned().unwrap_or_default();
        let mut names: Vec<String> = self.projects.clone();
        for k in self.budgets.as_object().into_iter().flat_map(|o| o.keys()) {
            if !names.contains(k) {
                names.push(k.clone());
            }
        }
        names.retain(|n| !deleted.contains_key(n));
        names.sort();
        let mut rows: Vec<Row> = names
            .iter()
            .map(|p| {
                let b = self.budgets.get(p).filter(|v| !v.is_null());
                let spent = b.and_then(|b| num(b, "spent")).unwrap_or_else(|| self.month_cost(p));
                let (amount, limit, (state, colour)) = match b {
                    Some(b) => (
                        fmt::cost(num(b, "monthly_usd").unwrap_or(0.0)),
                        if s(b, "on_limit") == "warn" { "Warn" } else { "Block" }.to_string(),
                        state_text(b),
                    ),
                    None => ("none".into(), "–".into(), (String::new(), Color::Reset)),
                };
                Row::new(p.clone())
                    .cell(p.clone())
                    .cell(fmt::cost(spent))
                    .styled(amount, if b.is_some() { Style::new() } else { DIM })
                    .styled(limit, DIM)
                    .styled(state, Style::new().fg(colour))
            })
            .collect();
        let gone: f64 = deleted.keys().map(|p| self.month_cost(p)).sum();
        if !deleted.is_empty() {
            rows.push(
                Row::new("")
                    .styled(format!("Deleted projects ({})", deleted.len()), DIM)
                    .styled(fmt::cost(gone), DIM)
                    .styled("no budgets", DIM),
            );
        }
        self.table.set_rows(rows);
    }

    fn edit_form(&self, p: &str) -> Form {
        let b = self.budgets.get(p).filter(|v| !v.is_null());
        let amount = b
            .and_then(|b| num(b, "monthly_usd"))
            .map(|n| n.to_string())
            .unwrap_or_default();
        let limit = b.map(|b| s(b, "on_limit")).unwrap_or_else(|| "block".into());
        Form::new(
            format!("Budget of {p}"),
            vec![
                Field::new("amount", "Monthly $", Kind::Number)
                    .value(amount)
                    .help("Empty: no budget."),
                Field::new(
                    "on_limit",
                    "At 100%",
                    Kind::Choice(vec![
                        ("block".into(), "Block: refuse new requests, pause a running one".into()),
                        ("warn".into(), "Warn: only show notes".into()),
                    ]),
                )
                .value(limit),
            ],
        )
        .intro("For the calendar month, in USD. A note appears at 80%. Calls without a known price aren't counted.")
        .submit_label("Save budget")
    }

    fn warnings_form(&self) -> Form {
        let fields = WARNINGS
            .iter()
            .map(|(k, label)| {
                let on = self.settings.get(*k).and_then(Value::as_bool) != Some(false);
                Field::new(k, *label, Kind::Toggle).value(if on { "1" } else { "" })
            })
            .collect();
        Form::new("Cost warnings", fields)
            .intro("These only decide whether a warning with the estimated cost appears first. They don't turn the features on or off.")
            .submit_label("Save")
    }
}

impl Section for Budgets {
    fn load(&mut self, ctx: &Ctx) {
        ctx.call("projects", Method::Get, "/api/projects", None);
        ctx.call("budgets", Method::Get, "/api/budgets", None);
        ctx.call("usage", Method::Get, "/api/usage", None);
        ctx.call("deleted", Method::Get, "/api/projects/deleted", None);
        ctx.call("settings", Method::Get, "/api/settings", None);
    }

    fn key(&mut self, k: KeyEvent, ctx: &Ctx) -> bool {
        let view = std::mem::replace(&mut self.view, View::List);
        let (view, used) = match view {
            View::Edit(p, mut f) => match f.key(k) {
                form::Action::Cancel => (View::List, true),
                form::Action::Submit => match f.number("amount") {
                    Ok(Some(n)) if n <= 0.0 => {
                        f.error = Some("A budget must be more than 0 (empty for none).".into());
                        (View::Edit(p, f), true)
                    }
                    Ok(amount) => {
                        f.busy = true;
                        let body = json!({"project": p, "monthly_usd": amount, "on_limit": f.get("on_limit")});
                        ctx.call(format!("saved:{p}"), Method::Post, "/api/budgets", Some(body));
                        (View::Edit(p, f), true)
                    }
                    Err(e) => {
                        f.error = Some(e);
                        (View::Edit(p, f), true)
                    }
                },
                form::Action::None => (View::Edit(p, f), true),
            },
            View::Warnings(mut f) => match f.key(k) {
                form::Action::Cancel => (View::List, true),
                form::Action::Submit => {
                    f.busy = true;
                    let changes: serde_json::Map<String, Value> = WARNINGS
                        .iter()
                        .map(|(key, _)| (key.to_string(), json!(f.on(key))))
                        .collect();
                    ctx.save_settings("warnings", Value::Object(changes));
                    (View::Warnings(f), true)
                }
                form::Action::None => (View::Warnings(f), true),
            },
            View::List => {
                if self.table.key(k) {
                    (View::List, true)
                } else {
                    let chosen = self.table.chosen().map(|r| r.id.clone()).filter(|id| !id.is_empty());
                    match (k.code, chosen) {
                        (KeyCode::Enter | KeyCode::Char('e'), Some(p)) => {
                            self.msg = None;
                            let f = self.edit_form(&p);
                            (View::Edit(p, f), true)
                        }
                        (KeyCode::Char('x'), Some(p)) if self.budgets.get(&p).is_some_and(|b| !b.is_null()) => {
                            self.msg = Some((true, format!("Removing the budget of {p}…")));
                            ctx.call(
                                format!("saved:{p}"),
                                Method::Post,
                                "/api/budgets",
                                Some(json!({"project": p, "monthly_usd": null})),
                            );
                            (View::List, true)
                        }
                        (KeyCode::Char('w'), _) => {
                            self.msg = None;
                            (View::Warnings(self.warnings_form()), true)
                        }
                        (KeyCode::Char('r'), _) => {
                            self.load(ctx);
                            (View::List, true)
                        }
                        _ => (View::List, false),
                    }
                }
            }
        };
        self.view = view;
        used
    }

    fn paste(&mut self, text: &str) {
        if let View::Edit(_, f) = &mut self.view {
            f.paste(text);
        }
    }

    fn on_answer(&mut self, a: Answer, ctx: &Ctx) {
        let (what, arg) = a.parts();
        let (what, arg) = (what.to_string(), arg.to_string());
        match (what.as_str(), a.result) {
            ("projects", Ok(v)) => {
                self.projects = v
                    .as_array()
                    .into_iter()
                    .flatten()
                    .filter_map(Value::as_str)
                    .map(str::to_string)
                    .collect()
            }
            ("budgets", Ok(v)) => self.budgets = v,
            ("usage", Ok(v)) => self.usage = v,
            ("deleted", Ok(v)) => self.deleted = v,
            ("settings", Ok(v)) => self.settings = v,
            ("saved", Ok(v)) => {
                let text = match v.get("budget").filter(|b| !b.is_null()) {
                    Some(b) => format!(
                        "Budget of {arg}: {} a month.",
                        fmt::cost(num(b, "monthly_usd").unwrap_or(0.0))
                    ),
                    None => format!("{arg} has no budget now."),
                };
                self.msg = Some((true, text));
                self.view = View::List;
                ctx.call("budgets", Method::Get, "/api/budgets", None);
                return;
            }
            ("warnings", Ok(v)) => {
                self.settings = v;
                self.view = View::List;
                self.msg = Some((true, "Cost warnings saved.".into()));
            }
            (_, Err(e)) => match &mut self.view {
                View::Edit(_, f) | View::Warnings(f) => {
                    f.busy = false;
                    f.error = Some(e);
                }
                View::List => self.msg = Some((false, e)),
            },
            _ => return,
        }
        self.rows();
    }

    fn keys(&self) -> String {
        match self.view {
            View::List => "e set budget · x remove it · w cost warnings · r refresh · / find · Esc menu".into(),
            _ => String::new(),
        }
    }

    fn draw(&mut self, f: &mut Frame, area: Rect) -> Vec<(u16, usize)> {
        let width = area.width as usize;
        let body = draw_head(
            f,
            area,
            head_lines("Budgets", "  ·  monthly spending limits per project", &self.msg, width),
        );
        match &self.view {
            View::List => {
                let table_h = (self.table.visible().len() as u16 + 3).clamp(4, body.height.saturating_sub(6).max(4));
                let placed = self.table.draw(
                    f,
                    Rect {
                        height: table_h.min(body.height),
                        ..body
                    },
                );
                let mut lines = vec![Line::default()];
                let warn: Vec<String> = WARNINGS
                    .iter()
                    .map(|(k, label)| {
                        let on = self.settings.get(*k).and_then(Value::as_bool) != Some(false);
                        format!("{} {label}", if on { "[x]" } else { "[ ]" })
                    })
                    .collect();
                lines.push(Line::from(Span::styled(
                    "Cost warnings (w)",
                    Style::new().fg(Color::Gray),
                )));
                for w in warn {
                    lines.push(Line::from(Span::styled(format!("  {w}"), DIM)));
                }
                lines.extend(wrap(
                    vec![Span::styled(
                        "Block refuses new requests and pauses a running one at 100%; Warn only shows notes. A note appears at 80%.",
                        DIM,
                    )],
                    width,
                    "",
                    "",
                ));
                let rest = Rect {
                    y: body.y + table_h,
                    height: body.height.saturating_sub(table_h),
                    ..body
                };
                f.render_widget(Paragraph::new(lines), rest);
                placed
            }
            View::Edit(_, form) | View::Warnings(form) => {
                let title = vec![
                    Line::from(Span::styled(form.title.clone(), Style::new().fg(Color::LightCyan))),
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
                Vec::new()
            }
        }
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
