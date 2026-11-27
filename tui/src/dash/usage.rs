//! Dashboard → Usage (its Details; the charts stay in the web page): tokens, cost and
//! time per project, role, job, model and skill, for a period; the task records.

use crossterm::event::{KeyCode, KeyEvent};
use ratatui::Frame;
use ratatui::layout::Rect;
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::Paragraph;
use serde_json::Value;

use super::{Answer, Ctx, Section, draw_head, fmt, head_lines};
use crate::api::Method;
use crate::chat::text::DIM;
use crate::cli::num;
use crate::ui::confirm::{self, Confirm};
use crate::ui::table::{Row, Table};

pub const PERIODS: &[(&str, &str)] = &[
    ("today", "Today"),
    ("last_7_days", "7 days"),
    ("this_month", "This month"),
    ("all_time", "All time"),
];

pub const GROUPS: &[(&str, &str)] = &[
    ("by_project", "By project"),
    ("by_role", "By role"),
    ("by_job", "By job"),
    ("by_model", "By model"),
    ("by_skill", "By skill (using)"),
    ("skill_drafts", "Skill drafting"),
    ("memory_updates", "Memory updates"),
];

/// Calls that don't answer a chat request: jobs, apart from the roles (app.js JOBS).
pub const JOBS: &[&str] = &["commit_message", "curator", "skill_draft", "test"];

/// The rows of one group for one period: (name, numbers). Deleted projects add up in
/// one row at the end (as the page's closed group).
pub fn group_rows(usage: &Value, period: &str, group: &str, deleted: &Value) -> Vec<(String, Value, bool)> {
    let key = if group == "by_job" { "by_role" } else { group };
    let Some(data) = usage.get(period).and_then(|p| p.get(key)).and_then(Value::as_object) else {
        return Vec::new();
    };
    let per_project = matches!(group, "by_project" | "memory_updates");
    let mut live = Vec::new();
    let mut gone: Vec<&Value> = Vec::new();
    for (name, b) in data {
        if matches!(group, "by_role" | "by_job") && JOBS.contains(&name.as_str()) != (group == "by_job") {
            continue;
        }
        if per_project && deleted.get(name).is_some() {
            gone.push(b);
        } else {
            let label = if per_project && name.is_empty() {
                "(Workspace)".to_string()
            } else {
                name.clone()
            };
            live.push((label, b.clone(), false));
        }
    }
    live.sort_by(|a, b| {
        num(&b.1, "cost")
            .unwrap_or(0.0)
            .partial_cmp(&num(&a.1, "cost").unwrap_or(0.0))
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    if !gone.is_empty() {
        let mut sum = serde_json::Map::new();
        for k in [
            "calls",
            "prompt_tokens",
            "cached_tokens",
            "completion_tokens",
            "reasoning_tokens",
            "cost",
            "calls_without_cost",
        ] {
            sum.insert(
                k.into(),
                serde_json::json!(gone.iter().map(|b| num(b, k).unwrap_or(0.0)).sum::<f64>()),
            );
        }
        live.push((format!("Deleted projects ({})", gone.len()), Value::Object(sum), true));
    }
    live
}

pub fn cells(b: &Value) -> Vec<String> {
    let n = |k: &str| num(b, k).unwrap_or(0.0);
    let plus = if n("calls_without_cost") > 0.0 { "+" } else { "" };
    vec![
        format!("{:.0}", n("calls")),
        fmt::tokens(n("prompt_tokens")),
        fmt::tokens(n("cached_tokens")),
        fmt::tokens(n("completion_tokens")),
        fmt::tokens(n("reasoning_tokens")),
        format!("{}{plus}", fmt::cost(n("cost"))),
        num(b, "avg_duration_ms").map(fmt::ms).unwrap_or_else(|| "–".into()),
    ]
}

pub struct Usage {
    table: Table,
    usage: Value,
    deleted: Value,
    tasks: Value,
    period: usize,
    group: usize,
    confirm: Option<Confirm>,
    msg: Option<(bool, String)>,
}

impl Default for Usage {
    fn default() -> Usage {
        let mut table = Table::new(&[
            ("Name", 0),
            ("Calls", 7),
            ("Input", 7),
            ("Cached", 7),
            ("Output", 7),
            ("Thinking", 8),
            ("Cost", 10),
            ("Avg time", 8),
        ]);
        table.empty = "Nothing in this period.".into();
        Usage {
            table,
            usage: Value::Null,
            deleted: Value::Null,
            tasks: Value::Null,
            period: 2,
            group: 0,
            confirm: None,
            msg: None,
        }
    }
}

impl Usage {
    fn rows(&mut self) {
        let rows = group_rows(&self.usage, PERIODS[self.period].0, GROUPS[self.group].0, &self.deleted)
            .into_iter()
            .map(|(name, b, gone)| {
                let style = if gone { DIM } else { Style::new() };
                let mut r = Row::new(name.clone()).styled(name, style);
                for c in cells(&b) {
                    r = r.styled(c, style);
                }
                r
            })
            .collect();
        self.table.set_rows(rows);
    }
}

impl Section for Usage {
    fn load(&mut self, ctx: &Ctx) {
        ctx.call("usage", Method::Get, "/api/usage", None);
        ctx.call("deleted", Method::Get, "/api/projects/deleted", None);
        ctx.call("tasks", Method::Get, "/api/tasks", None);
    }

    fn key(&mut self, k: KeyEvent, ctx: &Ctx) -> bool {
        if let Some(c) = &mut self.confirm {
            match c.key(k) {
                confirm::Answer::Yes => {
                    self.confirm = None;
                    ctx.call("deleted_tasks", Method::Delete, "/api/tasks", None);
                }
                confirm::Answer::No => self.confirm = None,
                confirm::Answer::Waiting => {}
            }
            return true;
        }
        if self.table.key(k) {
            return true;
        }
        match k.code {
            KeyCode::Char(c @ '1'..='4') => self.period = c as usize - '1' as usize,
            KeyCode::Right => self.period = (self.period + 1) % PERIODS.len(),
            KeyCode::Char('g') => self.group = (self.group + 1) % GROUPS.len(),
            KeyCode::Char('G') => self.group = (self.group + GROUPS.len() - 1) % GROUPS.len(),
            KeyCode::Char('r') => {
                self.load(ctx);
                return true;
            }
            KeyCode::Char('x') => {
                let n = num(&self.tasks, "count").unwrap_or(0.0);
                if n > 0.0 {
                    self.confirm = Some(Confirm::new(
                        "Delete the task records?",
                        format!("Delete all {n:.0} task records? Chats and usage stay."),
                        "delete them",
                    ));
                } else {
                    self.msg = Some((false, "There are no task records.".into()));
                }
                return true;
            }
            _ => return false,
        }
        self.table.selected = 0;
        self.rows();
        true
    }

    fn on_answer(&mut self, a: Answer, ctx: &Ctx) {
        let what = a.parts().0.to_string();
        match (what.as_str(), a.result) {
            ("usage", Ok(v)) => self.usage = v,
            ("deleted", Ok(v)) => self.deleted = v,
            ("tasks", Ok(v)) => self.tasks = v,
            ("deleted_tasks", Ok(v)) => {
                self.msg = Some((
                    true,
                    format!(
                        "{} task records deleted.",
                        v.get("deleted").and_then(Value::as_u64).unwrap_or(0)
                    ),
                ));
                ctx.call("tasks", Method::Get, "/api/tasks", None);
            }
            (_, Err(e)) => self.msg = Some((false, e)),
            _ => {}
        }
        self.rows();
    }

    fn keys(&self) -> String {
        if self.confirm.is_some() {
            return String::new();
        }
        "1-4 period · g group · x delete task records · r refresh · / find · Esc menu".into()
    }

    fn draw(&mut self, f: &mut Frame, area: Rect) -> Vec<(u16, usize)> {
        let width = area.width as usize;
        let body = draw_head(
            f,
            area,
            head_lines(
                "Usage",
                "  ·  tokens, cost and time (charts: in the web page)",
                &self.msg,
                width,
            ),
        );
        if let Some(c) = &self.confirm {
            let mut lines = vec![
                Line::from(Span::styled(c.title.clone(), Style::new().fg(Color::Red))),
                Line::default(),
            ];
            lines.extend(c.lines(width));
            f.render_widget(Paragraph::new(lines), body);
            return Vec::new();
        }
        let mut top = Vec::new();
        let tabs = |items: &[(&str, &str)], on: usize, key: &str| {
            let mut spans = vec![Span::styled(format!("{key} "), DIM)];
            for (i, (_, label)) in items.iter().enumerate() {
                let style = if i == on {
                    Style::new().fg(Color::Black).bg(Color::LightCyan)
                } else {
                    DIM
                };
                spans.push(Span::styled(format!(" {label} "), style));
                spans.push(Span::raw(" "));
            }
            Line::from(spans)
        };
        top.push(tabs(PERIODS, self.period, "Period"));
        top.push(tabs(GROUPS, self.group, "Group "));
        top.push(Line::default());
        if let Some(t) = self.usage.get(PERIODS[self.period].0).and_then(|p| p.get("total")) {
            let n = |k: &str| num(t, k).unwrap_or(0.0);
            let plus = if n("calls_without_cost") > 0.0 {
                "+ (some calls have no known price)"
            } else {
                ""
            };
            top.push(Line::from(vec![
                Span::styled("Cost ", DIM),
                Span::styled(
                    format!("{}{plus}", fmt::cost(n("cost"))),
                    Style::new().add_modifier(Modifier::BOLD),
                ),
                Span::styled("   Calls ", DIM),
                Span::raw(format!("{:.0}", n("calls"))),
                Span::styled("   Input / cached ", DIM),
                Span::raw(format!(
                    "{} / {}",
                    fmt::tokens(n("prompt_tokens")),
                    fmt::tokens(n("cached_tokens"))
                )),
                Span::styled("   Output ", DIM),
                Span::raw(fmt::tokens(n("completion_tokens"))),
            ]));
            top.push(Line::default());
        } else if self.usage.is_null() {
            top.push(Line::from(Span::styled("Loading…", DIM)));
        }
        let top_h = top.len() as u16;
        f.render_widget(
            Paragraph::new(top),
            Rect {
                height: top_h.min(body.height),
                ..body
            },
        );
        let foot_h = 3u16;
        let table = Rect {
            y: body.y + top_h,
            height: body.height.saturating_sub(top_h + foot_h),
            ..body
        };
        let placed = self.table.draw(f, table);
        let count = num(&self.tasks, "count").unwrap_or(0.0);
        let foot = vec![
            Line::default(),
            Line::from(vec![
                Span::styled("Task records: ", Style::new().fg(Color::Gray)),
                Span::styled(
                    format!(
                        "{count:.0} requests · 👍 {} · 👎 {}   CSV: optimux-tui usage tasks-csv > tasks.csv",
                        num(&self.tasks, "thumbs_up").unwrap_or(0.0),
                        num(&self.tasks, "thumbs_down").unwrap_or(0.0)
                    ),
                    DIM,
                ),
            ]),
        ];
        f.render_widget(
            Paragraph::new(foot),
            Rect {
                y: table.y + table.height,
                height: foot_h.min(body.height),
                ..body
            },
        );
        placed
    }

    fn tabs(&self) -> Vec<Vec<String>> {
        if self.confirm.is_some() {
            return Vec::new();
        }
        let labels = |items: &[(&str, &str)]| items.iter().map(|(_, l)| format!(" {l} ")).collect();
        vec![labels(PERIODS), labels(GROUPS)]
    }

    fn click_tab(&mut self, row: usize, i: usize, _ctx: &Ctx) {
        if row == 0 {
            self.period = i.min(PERIODS.len() - 1);
        } else {
            self.group = i.min(GROUPS.len() - 1);
        }
        self.table.selected = 0;
        self.rows();
    }

    fn has_window(&self) -> bool {
        self.confirm.is_some() || self.table.filtering
    }

    fn click_row(&mut self, i: usize, _ctx: &Ctx) -> bool {
        self.table.selected = i.min(self.table.visible().len().saturating_sub(1));
        false
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn jobs_apart_from_roles_and_deleted_projects_summed() {
        let u = json!({"this_month": {
            "by_role": {"simple": {"cost": 1.0}, "test": {"cost": 0.1}},
            "by_project": {"demo": {"cost": 2.0, "calls": 3}, "old": {"cost": 0.5, "calls": 1}, "gone": {"cost": 0.25, "calls": 1}},
        }});
        let deleted = json!({"old": 1, "gone": 2});
        let roles: Vec<String> = group_rows(&u, "this_month", "by_role", &deleted)
            .into_iter()
            .map(|r| r.0)
            .collect();
        let jobs: Vec<String> = group_rows(&u, "this_month", "by_job", &deleted)
            .into_iter()
            .map(|r| r.0)
            .collect();
        assert_eq!((roles, jobs), (vec!["simple".to_string()], vec!["test".to_string()]));
        let p = group_rows(&u, "this_month", "by_project", &deleted);
        assert_eq!(p.len(), 2);
        assert_eq!(p[1].0, "Deleted projects (2)");
        assert_eq!(num(&p[1].1, "cost"), Some(0.75));
    }
}
