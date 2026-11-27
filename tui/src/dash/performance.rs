//! Dashboard → Performance (its Details; the charts stay in the web page): cost, speed
//! and success of every model, for a period and a project.

use crossterm::event::{KeyCode, KeyEvent};
use ratatui::Frame;
use ratatui::layout::Rect;
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::Paragraph;
use serde_json::Value;

use super::{Answer, Ctx, Section, draw_head, fmt, head_lines};
use crate::api::{Method, enc};
use crate::chat::text::{DIM, wrap};
use crate::cli::{num, s};
use crate::ui::table::{Row, Table};

pub const DAYS: &[(u32, &str)] = &[(7, "7 days"), (30, "30 days"), (90, "90 days"), (0, "All time")];

/// One model's row: model, calls, share, cost, $/call, median time, speed, reliability,
/// escalations, 👍 share.
pub fn cells(m: &Value) -> Vec<String> {
    let opt = |k: &str, f: &dyn Fn(f64) -> String| num(m, k).map(f).unwrap_or_else(|| "–".into());
    let quality = match (num(m, "rated"), num(m, "up")) {
        (Some(r), Some(u)) if r > 0.0 => format!("{:.0}% of {r:.0}", u / r * 100.0),
        _ => "–".into(),
    };
    vec![
        s(m, "model"),
        format!("{:.0}", num(m, "calls").unwrap_or(0.0)),
        opt("share", &fmt::percent),
        fmt::cost(num(m, "cost").unwrap_or(0.0)),
        opt("cost_per_call", &|c| fmt::cost(c)),
        opt("p50_ms", &fmt::ms),
        opt("speed_tps", &|t| format!("{t:.0} tok/s")),
        opt("reliability", &fmt::percent),
        opt("escalation_rate", &fmt::percent),
        quality,
    ]
}

pub struct Performance {
    table: Table,
    data: Value,
    projects: Vec<String>,
    days: usize,
    /// 0: every project, else projects[i - 1].
    project: usize,
    msg: Option<(bool, String)>,
}

impl Default for Performance {
    fn default() -> Performance {
        let mut table = Table::new(&[
            ("Model", 0),
            ("Calls", 6),
            ("Share", 6),
            ("Cost", 9),
            ("$/call", 8),
            ("Median", 7),
            ("Speed", 10),
            ("Works", 6),
            ("Escal.", 6),
            ("👍", 9),
        ]);
        table.empty = "No calls in this period.".into();
        Performance {
            table,
            data: Value::Null,
            projects: Vec::new(),
            days: 1,
            project: 0,
            msg: None,
        }
    }
}

impl Performance {
    fn fetch(&self, ctx: &Ctx) {
        let mut q = format!("/api/analytics?days={}", DAYS[self.days].0);
        if self.project > 0 {
            if let Some(p) = self.projects.get(self.project - 1) {
                q.push_str(&format!("&project={}", enc(p)));
            }
        }
        ctx.call("data", Method::Get, q, None);
    }

    fn rows(&mut self) {
        let rows = self
            .data
            .get("models")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .map(|m| {
                let mut r = Row::new(s(m, "model"));
                for c in cells(m) {
                    r = r.cell(c);
                }
                r
            })
            .collect();
        self.table.set_rows(rows);
    }

    fn project_label(&self) -> String {
        if self.project == 0 {
            "every project".into()
        } else {
            self.projects.get(self.project - 1).cloned().unwrap_or_default()
        }
    }
}

impl Section for Performance {
    fn load(&mut self, ctx: &Ctx) {
        ctx.call("projects", Method::Get, "/api/projects", None);
        self.fetch(ctx);
    }

    fn key(&mut self, k: KeyEvent, ctx: &Ctx) -> bool {
        if self.table.key(k) {
            return true;
        }
        match k.code {
            KeyCode::Char(c @ '1'..='4') => self.days = c as usize - '1' as usize,
            KeyCode::Char('p') => self.project = (self.project + 1) % (self.projects.len() + 1),
            KeyCode::Char('r') => {}
            _ => return false,
        }
        self.fetch(ctx);
        true
    }

    fn on_answer(&mut self, a: Answer, _ctx: &Ctx) {
        let what = a.parts().0.to_string();
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
            ("data", Ok(v)) => {
                self.data = v;
                self.msg = None;
                self.rows();
            }
            (_, Err(e)) => self.msg = Some((false, e)),
            _ => {}
        }
    }

    fn keys(&self) -> String {
        "1-4 period · p project · r refresh · / find · Esc menu".into()
    }

    fn draw(&mut self, f: &mut Frame, area: Rect) -> Vec<(u16, usize)> {
        let width = area.width as usize;
        let body = draw_head(
            f,
            area,
            head_lines(
                "Performance",
                "  ·  which model gives you the most for your money (charts: web page)",
                &self.msg,
                width,
            ),
        );
        let mut top = Vec::new();
        let mut spans = vec![Span::styled("Period ", DIM)];
        for (i, (_, label)) in DAYS.iter().enumerate() {
            let style = if i == self.days {
                Style::new().fg(Color::Black).bg(Color::LightCyan)
            } else {
                DIM
            };
            spans.push(Span::styled(format!(" {label} "), style));
            spans.push(Span::raw(" "));
        }
        spans.push(Span::styled("   Project ", DIM));
        spans.push(Span::styled(self.project_label(), Style::new().fg(Color::LightCyan)));
        top.push(Line::from(spans));
        top.push(Line::default());
        if let Some(t) = self.data.get("totals") {
            let n = |k: &str| num(t, k).unwrap_or(0.0);
            top.push(Line::from(vec![
                Span::styled("Calls ", DIM),
                Span::styled(format!("{:.0}", n("calls")), Style::new().add_modifier(Modifier::BOLD)),
                Span::styled("   Cost ", DIM),
                Span::raw(fmt::cost(n("cost"))),
                Span::styled("   Median time ", DIM),
                Span::raw(num(t, "p50_ms").map(fmt::ms).unwrap_or_else(|| "–".into())),
                Span::styled("   Works ", DIM),
                Span::raw(num(t, "reliability").map(fmt::percent).unwrap_or_else(|| "–".into())),
                Span::styled("   Errors ", DIM),
                Span::raw(format!("{:.0}", n("errors"))),
                Span::styled("   Escalations ", DIM),
                Span::raw(format!("{:.0}", n("escalations"))),
            ]));
            top.push(Line::default());
        }
        let top_h = top.len() as u16;
        f.render_widget(
            Paragraph::new(top),
            Rect {
                height: top_h.min(body.height),
                ..body
            },
        );
        let note = wrap(
            vec![Span::styled(
                "Works: calls that answered. Escal.: requests moved to Complex. 👍: your thumbs up of the rated replies.",
                DIM,
            )],
            width,
            "",
            "",
        );
        let note_h = note.len() as u16 + 1;
        let table = Rect {
            y: body.y + top_h,
            height: body.height.saturating_sub(top_h + note_h),
            ..body
        };
        let placed = self.table.draw(f, table);
        let mut foot = vec![Line::default()];
        foot.extend(note);
        f.render_widget(
            Paragraph::new(foot),
            Rect {
                y: table.y + table.height,
                height: note_h.min(body.height),
                ..body
            },
        );
        placed
    }

    fn tabs(&self) -> Vec<Vec<String>> {
        vec![DAYS.iter().map(|(_, l)| format!(" {l} ")).collect()]
    }

    fn click_tab(&mut self, _row: usize, i: usize, ctx: &Ctx) {
        self.days = i.min(DAYS.len() - 1);
        self.fetch(ctx);
    }

    fn has_window(&self) -> bool {
        self.table.filtering
    }

    fn click_row(&mut self, i: usize, _ctx: &Ctx) -> bool {
        self.table.selected = i.min(self.table.visible().len().saturating_sub(1));
        false
    }
}
