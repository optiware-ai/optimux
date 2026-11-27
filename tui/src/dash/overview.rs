//! Dashboard → Overview, as in the web page (its chart stays there): today's and this
//! month's spending, the cache hit, the output, the estimated savings; the budgets of
//! the month; the recent chats (Enter opens one); every model's status and roles.

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use ratatui::Frame;
use ratatui::layout::Rect;
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::Paragraph;
use serde_json::Value;

use super::{Answer, Ctx, Section, draw_head, fmt, head_lines};
use crate::api::Method;
use crate::chat::text::DIM;
use crate::cli::{num, s};
use crate::dash::projects::ago;
use crate::ui::table::{Row, Table};

/// The KPI tiles: (label, value, below).
pub fn kpis(usage: &Value, savings: &Value) -> Vec<(String, String, String)> {
    let Some(today) = usage.get("today").and_then(|t| t.get("total")) else {
        return Vec::new();
    };
    let month = usage.get("this_month").and_then(|t| t.get("total")).unwrap_or(today);
    let n = |v: &Value, k: &str| num(v, k).unwrap_or(0.0);
    let plus = |v: &Value| if n(v, "calls_without_cost") > 0.0 { "+" } else { "" };
    let hit = if n(month, "prompt_tokens") > 0.0 {
        n(month, "cached_tokens") / n(month, "prompt_tokens")
    } else {
        0.0
    };
    let mut out = vec![
        (
            "Today".into(),
            format!("{}{}", fmt::cost(n(today, "cost")), plus(today)),
            format!("{:.0} calls", n(today, "calls")),
        ),
        (
            "This month".into(),
            format!("{}{}", fmt::cost(n(month, "cost")), plus(month)),
            format!("{:.0} calls", n(month, "calls")),
        ),
        (
            "Cache hit · month".into(),
            fmt::percent(hit),
            format!(
                "{} of {} input tokens",
                fmt::tokens(n(month, "cached_tokens")),
                fmt::tokens(n(month, "prompt_tokens"))
            ),
        ),
        (
            "Output · month".into(),
            fmt::tokens(n(month, "completion_tokens")),
            format!("{} of it thinking", fmt::tokens(n(month, "reasoning_tokens"))),
        ),
    ];
    if n(savings, "calls") > 0.0 {
        let share = num(savings, "saved_share")
            .map(|x| format!("{} less than the same tokens on Complex", fmt::percent(x)))
            .unwrap_or_default();
        out.push(("Est. saved · month".into(), fmt::cost(n(savings, "saved")), share));
    }
    out
}

#[derive(Default)]
pub struct Overview {
    usage: Value,
    savings: Value,
    budgets: Value,
    config: Value,
    routing: Value,
    cooldowns: Value,
    chats: Table,
    msg: Option<(bool, String)>,
    goto: Option<(&'static str, String)>,
}

impl Overview {
    fn roles(&self, id: &str) -> Vec<&'static str> {
        [
            ("conductor_model", "Conductor"),
            ("simple_model", "Simple"),
            ("complex_model", "Complex"),
        ]
        .iter()
        .filter(|(k, _)| s(&self.routing, k) == id)
        .map(|(_, l)| *l)
        .collect()
    }
}

impl Section for Overview {
    fn load(&mut self, ctx: &Ctx) {
        if self.chats.columns.is_empty() {
            self.chats = Table::new(&[("Recent chat", 0), ("Project", 14), ("Updated", 13), ("Cost", 9)]);
            self.chats.empty = "No chats yet.".into();
        }
        ctx.call("usage", Method::Get, "/api/usage", None);
        ctx.call("savings", Method::Get, "/api/savings?period=month", None);
        ctx.call("budgets", Method::Get, "/api/budgets", None);
        ctx.call("config", Method::Get, "/api/config", None);
        ctx.call("routing", Method::Get, "/api/routing", None);
        ctx.call("cooldowns", Method::Get, "/api/cooldowns", None);
        ctx.call("chats", Method::Get, "/api/sessions?all_projects=true&limit=8", None);
    }

    fn key(&mut self, k: KeyEvent, ctx: &Ctx) -> bool {
        if self.chats.key(k) {
            return true;
        }
        if k.modifiers.contains(KeyModifiers::CONTROL) {
            return false;
        }
        match k.code {
            KeyCode::Enter => {
                if let Some(r) = self.chats.chosen() {
                    self.goto = Some(("Chat", r.id.clone()));
                }
            }
            KeyCode::Char('r') => self.load(ctx),
            KeyCode::Char('h') => self.goto = Some(("History", String::new())),
            KeyCode::Char('m') => self.goto = Some(("Models", String::new())),
            KeyCode::Char('b') => self.goto = Some(("Budgets", String::new())),
            _ => return false,
        }
        true
    }

    fn on_answer(&mut self, a: Answer, _ctx: &Ctx) {
        let what = a.parts().0.to_string();
        match (what.as_str(), a.result) {
            ("usage", Ok(v)) => self.usage = v,
            ("savings", Ok(v)) => self.savings = v,
            ("budgets", Ok(v)) => self.budgets = v,
            ("config", Ok(v)) => self.config = v,
            ("routing", Ok(v)) => self.routing = v,
            ("cooldowns", Ok(v)) => self.cooldowns = v,
            ("chats", Ok(v)) => {
                let rows = v
                    .as_array()
                    .into_iter()
                    .flatten()
                    .map(|c| {
                        Row::new(s(c, "id"))
                            .cell(if s(c, "title").is_empty() {
                                "Untitled chat".into()
                            } else {
                                s(c, "title")
                            })
                            .styled(
                                if s(c, "project").is_empty() {
                                    "(Workspace)".into()
                                } else {
                                    s(c, "project")
                                },
                                DIM,
                            )
                            .styled(num(c, "updated").map(ago).unwrap_or_default(), DIM)
                            .cell(fmt::cost(num(c, "cost").unwrap_or(0.0)))
                    })
                    .collect();
                self.chats.set_rows(rows);
            }
            (_, Err(e)) => self.msg = Some((false, e)),
            _ => {}
        }
    }

    fn keys(&self) -> String {
        "Enter open the chat · h all chats · m models · b budgets · r refresh · Esc menu".into()
    }

    fn draw(&mut self, f: &mut Frame, area: Rect) -> Vec<(u16, usize)> {
        let width = area.width as usize;
        let body = draw_head(
            f,
            area,
            head_lines(
                "Overview",
                "  ·  spending, budgets, models and recent chats (chart: web page)",
                &self.msg,
                width,
            ),
        );
        let bold = Style::new().add_modifier(Modifier::BOLD);
        let mut lines = Vec::new();
        // the tiles, side by side when they fit
        let tiles = kpis(&self.usage, &self.savings);
        if tiles.is_empty() {
            lines.push(Line::from(Span::styled("Loading…", DIM)));
        } else {
            let w = (width / tiles.len()).clamp(18, 34);
            let cell = |t: &str, w: usize| crate::ui::table::fit(t, w);
            lines.push(Line::from(
                tiles
                    .iter()
                    .map(|(l, ..)| Span::styled(cell(l, w), DIM))
                    .collect::<Vec<_>>(),
            ));
            lines.push(Line::from(
                tiles
                    .iter()
                    .map(|(_, v, _)| Span::styled(cell(v, w), bold.fg(Color::White)))
                    .collect::<Vec<_>>(),
            ));
            lines.push(Line::from(
                tiles
                    .iter()
                    .map(|(.., sub)| Span::styled(cell(sub, w), DIM))
                    .collect::<Vec<_>>(),
            ));
        }
        lines.push(Line::default());
        lines.push(Line::from(Span::styled("Budgets this month", bold)));
        let active: Vec<(&String, &Value)> = self
            .budgets
            .as_object()
            .into_iter()
            .flatten()
            .filter(|(_, b)| !b.is_null() && b.get("paused").and_then(Value::as_bool) != Some(true))
            .collect();
        if active.is_empty() {
            lines.push(Line::from(Span::styled("  No budgets set (b: Budgets).", DIM)));
        }
        for (name, b) in active {
            let ratio = num(b, "ratio").unwrap_or(0.0);
            let bar_w = 20usize;
            let filled = ((ratio.min(1.0)) * bar_w as f64).round() as usize;
            let colour = if ratio >= 1.0 {
                Color::Red
            } else if ratio >= 0.8 {
                Color::Yellow
            } else {
                Color::Green
            };
            lines.push(Line::from(vec![
                Span::raw(format!("  {}", crate::ui::table::fit(name, 18))),
                Span::styled("█".repeat(filled), Style::new().fg(colour)),
                Span::styled("░".repeat(bar_w - filled), DIM),
                Span::raw(format!(
                    "  {} / {}",
                    fmt::cost(num(b, "spent").unwrap_or(0.0)),
                    fmt::cost(num(b, "monthly_usd").unwrap_or(0.0))
                )),
            ]));
        }
        lines.push(Line::default());
        lines.push(Line::from(Span::styled("Model status", bold)));
        let models: Vec<(Value, bool)> = [("available", true), ("requires_setup", false)]
            .iter()
            .flat_map(|(k, ready)| {
                self.config
                    .get(*k)
                    .and_then(Value::as_array)
                    .cloned()
                    .unwrap_or_default()
                    .into_iter()
                    .map(move |m| (m, *ready))
            })
            .collect();
        if models.is_empty() {
            lines.push(Line::from(Span::styled("  No models configured (m: Models).", DIM)));
        }
        for (m, ready) in &models {
            let id = s(m, "id");
            let (state, colour) = if let Some(c) = self.cooldowns.get(&id) {
                (format!("cooldown: {}", s(c, "reason")), Color::Yellow)
            } else if !ready {
                ("needs key".into(), Color::Yellow)
            } else {
                ("ready".into(), Color::Green)
            };
            let roles = self.roles(&id);
            lines.push(Line::from(vec![
                Span::styled("  ● ", Style::new().fg(colour)),
                Span::raw(crate::ui::table::fit(&s(m, "name"), 24)),
                Span::styled(crate::ui::table::fit(&id, 38), DIM),
                Span::styled(
                    crate::ui::table::fit(&roles.join(" · "), 26),
                    Style::new().fg(Color::LightCyan),
                ),
                Span::styled(state, Style::new().fg(colour)),
            ]));
        }
        lines.push(Line::default());
        let n = (lines.len() as u16).min(body.height);
        f.render_widget(Paragraph::new(lines), Rect { height: n, ..body });
        self.chats.draw(
            f,
            Rect {
                y: body.y + n,
                height: body.height.saturating_sub(n),
                ..body
            },
        )
    }

    fn has_window(&self) -> bool {
        self.chats.filtering
    }

    fn click_row(&mut self, i: usize, _ctx: &Ctx) -> bool {
        self.chats.selected = i.min(self.chats.visible().len().saturating_sub(1));
        false
    }

    fn take_goto(&mut self) -> Option<(&'static str, String)> {
        self.goto.take()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn the_tiles_as_the_page() {
        let u = json!({
            "today": {"total": {"cost": 0.01, "calls": 3}},
            "this_month": {"total": {"cost": 2.0, "calls": 40, "prompt_tokens": 1000, "cached_tokens": 250, "completion_tokens": 500, "reasoning_tokens": 100, "calls_without_cost": 1}},
        });
        let t = kpis(&u, &json!({"calls": 0}));
        assert_eq!(t.len(), 4);
        assert_eq!(t[1].1, "$2.00+");
        assert_eq!(t[2].1, "25%");
        let with_savings = kpis(&u, &json!({"calls": 10, "saved": 0.5, "saved_share": 0.2}));
        assert_eq!(with_savings[4].1, "$0.500");
    }
}
