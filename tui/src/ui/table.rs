//! A table: rows to move through (↑↓, PgUp/PgDn, Home/End), filtered by typing after
//! `/`. Each row has an id, so the section knows which one is chosen.

use crossterm::event::{KeyCode, KeyEvent};
use ratatui::Frame;
use ratatui::layout::Rect;
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::Paragraph;
use unicode_width::UnicodeWidthStr;

use crate::chat::text::DIM;

#[derive(Debug, Clone, PartialEq)]
pub struct Row {
    pub id: String,
    pub cells: Vec<(String, Style)>,
}

impl Row {
    pub fn new(id: impl Into<String>) -> Row {
        Row {
            id: id.into(),
            cells: Vec::new(),
        }
    }

    pub fn cell(mut self, text: impl Into<String>) -> Row {
        self.cells.push((text.into(), Style::new()));
        self
    }

    pub fn styled(mut self, text: impl Into<String>, style: Style) -> Row {
        self.cells.push((text.into(), style));
        self
    }
}

#[derive(Debug, Clone, Default)]
pub struct Table {
    /// (title, width): width 0 takes what is left.
    pub columns: Vec<(String, u16)>,
    pub rows: Vec<Row>,
    pub selected: usize,
    pub filter: String,
    /// Typing goes to the filter (after `/`).
    pub filtering: bool,
    pub empty: String,
}

impl Table {
    pub fn new(columns: &[(&str, u16)]) -> Table {
        Table {
            columns: columns.iter().map(|(t, w)| (t.to_string(), *w)).collect(),
            empty: "Nothing here.".into(),
            ..Table::default()
        }
    }

    /// New rows; the same row stays chosen when it is still there.
    pub fn set_rows(&mut self, rows: Vec<Row>) {
        let keep = self.chosen().map(|r| r.id.clone());
        self.rows = rows;
        if let Some(id) = keep {
            if let Some(i) = self.visible().iter().position(|r| r.id == id) {
                self.selected = i;
            }
        }
        self.selected = self.selected.min(self.visible().len().saturating_sub(1));
    }

    pub fn visible(&self) -> Vec<&Row> {
        let f = self.filter.to_lowercase();
        self.rows
            .iter()
            .filter(|r| f.is_empty() || r.cells.iter().any(|(t, _)| t.to_lowercase().contains(&f)))
            .collect()
    }

    pub fn chosen(&self) -> Option<&Row> {
        self.visible().get(self.selected).copied()
    }

    /// True when the key was the table's.
    pub fn key(&mut self, k: KeyEvent) -> bool {
        let n = self.visible().len();
        if self.filtering {
            match k.code {
                KeyCode::Esc | KeyCode::Enter => self.filtering = false,
                KeyCode::Backspace => {
                    self.filter.pop();
                    self.selected = 0;
                }
                KeyCode::Char(c) => {
                    self.filter.push(c);
                    self.selected = 0;
                }
                KeyCode::Up => self.selected = self.selected.saturating_sub(1),
                KeyCode::Down => self.selected = (self.selected + 1).min(n.saturating_sub(1)),
                _ => return false,
            }
            return true;
        }
        match k.code {
            KeyCode::Up => self.selected = self.selected.saturating_sub(1),
            KeyCode::Down => self.selected = (self.selected + 1).min(n.saturating_sub(1)),
            KeyCode::PageUp => self.selected = self.selected.saturating_sub(10),
            KeyCode::PageDown => self.selected = (self.selected + 10).min(n.saturating_sub(1)),
            KeyCode::Home => self.selected = 0,
            KeyCode::End => self.selected = n.saturating_sub(1),
            KeyCode::Char('/') => self.filtering = true,
            _ => return false,
        }
        true
    }

    fn widths(&self, total: u16) -> Vec<u16> {
        let fixed: u16 = self.columns.iter().map(|(_, w)| *w + 1).sum();
        let flex = self.columns.iter().filter(|(_, w)| *w == 0).count() as u16;
        let left = total.saturating_sub(fixed + 1);
        self.columns
            .iter()
            .map(|(_, w)| if *w == 0 { left / flex.max(1) } else { *w })
            .collect()
    }

    /// Draws the table; returns, for each drawn row, its screen row and its index among
    /// the visible rows (for mouse clicks).
    pub fn draw(&self, f: &mut Frame, area: Rect) -> Vec<(u16, usize)> {
        let widths = self.widths(area.width);
        let mut lines = Vec::new();
        let head: Vec<Span> = self
            .columns
            .iter()
            .zip(&widths)
            .map(|((t, _), w)| Span::styled(fit(t, *w as usize + 1), DIM.add_modifier(Modifier::BOLD)))
            .collect();
        lines.push(Line::from([vec![Span::raw("  ")], head].concat()));
        let rows = self.visible();
        let body_h = area
            .height
            .saturating_sub(if self.filtering || !self.filter.is_empty() {
                3
            } else {
                2
            }) as usize;
        let first = self.selected.saturating_sub(body_h.saturating_sub(1));
        let mut placed = Vec::new();
        if rows.is_empty() {
            lines.push(Line::from(Span::styled(format!("  {}", self.empty), DIM)));
        }
        for (i, r) in rows.iter().enumerate().skip(first).take(body_h) {
            let here = i == self.selected;
            let base = if here {
                Style::new().bg(Color::Indexed(236))
            } else {
                Style::new()
            };
            let mut spans = vec![Span::styled(if here { "▸ " } else { "  " }, base.fg(Color::LightCyan))];
            for ((text, style), w) in r.cells.iter().zip(&widths) {
                spans.push(Span::styled(fit(text, *w as usize + 1), base.patch(*style)));
            }
            let used: usize = spans.iter().map(|s| s.width()).sum();
            spans.push(Span::styled(
                " ".repeat((area.width as usize).saturating_sub(used)),
                base,
            ));
            placed.push((area.y + lines.len() as u16, i));
            lines.push(Line::from(spans));
        }
        if self.filtering || !self.filter.is_empty() {
            let cursor = if self.filtering { "▏" } else { "" };
            lines.push(Line::from(vec![
                Span::styled("  Find: ", DIM),
                Span::raw(self.filter.clone()),
                Span::styled(cursor, Style::new().fg(Color::LightCyan)),
                Span::styled(format!("   {} of {}", rows.len(), self.rows.len()), DIM),
            ]));
        }
        f.render_widget(Paragraph::new(lines), area);
        placed
    }
}

/// Cut or padded to exactly `w` columns.
pub fn fit(text: &str, w: usize) -> String {
    if text.width() <= w.saturating_sub(1) {
        return format!("{text}{}", " ".repeat(w - text.width()));
    }
    let mut out = String::new();
    for c in text.chars() {
        if out.width() + unicode_width::UnicodeWidthChar::width(c).unwrap_or(0) > w.saturating_sub(2) {
            break;
        }
        out.push(c);
    }
    out.push('…');
    format!("{out}{}", " ".repeat(w.saturating_sub(out.width())))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crossterm::event::KeyModifiers;

    fn k(c: KeyCode) -> KeyEvent {
        KeyEvent::new(c, KeyModifiers::NONE)
    }

    fn table() -> Table {
        let mut t = Table::new(&[("Name", 10), ("ID", 0)]);
        t.set_rows(vec![
            Row::new("a").cell("Gemini").cell("gemini/flash"),
            Row::new("b").cell("Claude").cell("anthropic/sonnet"),
            Row::new("c").cell("Fake").cell("openai/fake"),
        ]);
        t
    }

    #[test]
    fn moving_and_choosing() {
        let mut t = table();
        t.key(k(KeyCode::Down));
        assert_eq!(t.chosen().unwrap().id, "b");
        t.key(k(KeyCode::End));
        assert_eq!(t.chosen().unwrap().id, "c");
        t.key(k(KeyCode::Down));
        assert_eq!(t.chosen().unwrap().id, "c", "stays on the last row");
    }

    #[test]
    fn typing_after_slash_filters() {
        let mut t = table();
        assert!(t.key(k(KeyCode::Char('/'))));
        for c in "anthro".chars() {
            t.key(k(KeyCode::Char(c)));
        }
        assert_eq!(t.visible().len(), 1);
        assert_eq!(t.chosen().unwrap().id, "b");
        t.key(k(KeyCode::Enter));
        assert!(
            !t.filtering && !t.key(k(KeyCode::Char('x'))),
            "after the filter, letters are the section's keys"
        );
    }

    #[test]
    fn new_rows_keep_the_chosen_one() {
        let mut t = table();
        t.key(k(KeyCode::Down));
        let mut rows = t.rows.clone();
        rows.insert(0, Row::new("z").cell("New").cell("x"));
        t.set_rows(rows);
        assert_eq!(t.chosen().unwrap().id, "b");
    }

    #[test]
    fn cells_fit_their_column() {
        assert_eq!(fit("abc", 5), "abc  ");
        assert_eq!(fit("abcdefgh", 5), "abc… ");
        assert_eq!(fit("abcdefgh", 5).width(), 5);
    }
}
