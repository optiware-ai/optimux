//! A form: labelled fields (text, hidden text for keys, numbers, a choice, on/off),
//! moved through with Tab / ↑↓, sent with Ctrl+S (or Enter on the last field),
//! closed with Esc. Used by every Dashboard section.

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use ratatui::Frame;
use ratatui::layout::Rect;
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::Paragraph;
use unicode_width::UnicodeWidthStr;

use crate::chat::text::{DIM, wrap};

#[derive(Debug, Clone, PartialEq)]
pub enum Kind {
    Text,
    /// Typed but never shown (API keys): shown as dots, never in the screen's scrollback.
    Secret,
    /// A number, or empty for none.
    Number,
    /// One of (value, label); ←/→ or Space change it.
    Choice(Vec<(String, String)>),
    /// On or off ("1" / ""); Space changes it.
    Toggle,
}

#[derive(Debug, Clone)]
pub struct Field {
    pub key: &'static str,
    pub label: String,
    pub kind: Kind,
    pub value: String,
    /// A line under the field.
    pub help: String,
    pub required: bool,
    /// Only shown, not changed (the form says why in `help`).
    pub locked: bool,
    cursor: usize,
}

impl Field {
    pub fn new(key: &'static str, label: impl Into<String>, kind: Kind) -> Field {
        // a choice starts on its first option
        let value = match &kind {
            Kind::Choice(opts) => opts.first().map(|(v, _)| v.clone()).unwrap_or_default(),
            _ => String::new(),
        };
        Field {
            key,
            label: label.into(),
            kind,
            value,
            help: String::new(),
            required: false,
            locked: false,
            cursor: 0,
        }
    }

    pub fn value(mut self, v: impl Into<String>) -> Field {
        self.value = v.into();
        self.cursor = self.value.chars().count();
        if let Kind::Choice(opts) = &self.kind {
            if !opts.iter().any(|(val, _)| *val == self.value) {
                self.value = opts.first().map(|(v, _)| v.clone()).unwrap_or_default();
            }
        }
        self
    }

    pub fn help(mut self, h: impl Into<String>) -> Field {
        self.help = h.into();
        self
    }

    pub fn required(mut self) -> Field {
        self.required = true;
        self
    }

    pub fn locked(mut self) -> Field {
        self.locked = true;
        self
    }

    fn shown(&self) -> String {
        match &self.kind {
            Kind::Secret => "•".repeat(self.value.chars().count()),
            Kind::Choice(opts) => {
                let label = opts
                    .iter()
                    .find(|(v, _)| *v == self.value)
                    .map(|(_, l)| l.as_str())
                    .unwrap_or("");
                format!("‹ {label} ›")
            }
            Kind::Toggle => if self.value.is_empty() { "[ ] no" } else { "[x] yes" }.to_string(),
            _ => self.value.clone(),
        }
    }

    fn typed(&self) -> bool {
        matches!(self.kind, Kind::Text | Kind::Secret | Kind::Number)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Action {
    None,
    Submit,
    Cancel,
}

#[derive(Debug, Clone)]
pub struct Form {
    pub title: String,
    pub intro: String,
    pub fields: Vec<Field>,
    pub focus: usize,
    /// Shown in red under the fields (a check failed, or the server said no).
    pub error: Option<String>,
    pub submit: String,
    /// Sending: keys wait.
    pub busy: bool,
    /// Its own cyan line of keys (off when the page shows its keys there instead).
    pub keys_line: bool,
}

impl Form {
    pub fn new(title: impl Into<String>, fields: Vec<Field>) -> Form {
        let focus = fields.iter().position(|f| !f.locked).unwrap_or(0);
        Form {
            title: title.into(),
            intro: String::new(),
            fields,
            focus,
            error: None,
            submit: "Save".into(),
            busy: false,
            keys_line: true,
        }
    }

    /// The page shows the keys under the form, in its place.
    pub fn no_keys_line(mut self) -> Form {
        self.keys_line = false;
        self
    }

    pub fn intro(mut self, t: impl Into<String>) -> Form {
        self.intro = t.into();
        self
    }

    pub fn submit_label(mut self, s: impl Into<String>) -> Form {
        self.submit = s.into();
        self
    }

    pub fn get(&self, key: &str) -> &str {
        self.fields
            .iter()
            .find(|f| f.key == key)
            .map(|f| f.value.as_str())
            .unwrap_or("")
    }

    pub fn on(&self, key: &str) -> bool {
        !self.get(key).is_empty()
    }

    #[allow(dead_code)] // used by the tests and the later sections
    pub fn field_mut(&mut self, key: &str) -> Option<&mut Field> {
        self.fields.iter_mut().find(|f| f.key == key)
    }

    /// A number field: None when empty; an error names the field.
    pub fn number(&self, key: &str) -> Result<Option<f64>, String> {
        let v = self.get(key).trim();
        if v.is_empty() {
            return Ok(None);
        }
        let label = self
            .fields
            .iter()
            .find(|f| f.key == key)
            .map(|f| f.label.clone())
            .unwrap_or_default();
        match v.replace(',', ".").parse::<f64>() {
            Ok(n) if n >= 0.0 && n.is_finite() => Ok(Some(n)),
            _ => Err(format!("{label}: a number, like 0.75 (or empty).")),
        }
    }

    /// The form's own checks before sending: required fields and numbers.
    pub fn check(&self) -> Result<(), String> {
        for f in &self.fields {
            if f.required && f.value.trim().is_empty() {
                return Err(format!("{} is needed.", f.label));
            }
            if f.kind == Kind::Number {
                self.number(f.key)?;
            }
        }
        Ok(())
    }

    pub fn paste(&mut self, text: &str) {
        let line = text.lines().next().unwrap_or("").trim();
        if let Some(f) = self.fields.get_mut(self.focus) {
            if f.typed() && !f.locked {
                for c in line.chars() {
                    insert(f, c);
                }
            }
        }
    }

    pub fn key(&mut self, k: KeyEvent) -> Action {
        if self.busy {
            return Action::None;
        }
        let ctrl = k.modifiers.contains(KeyModifiers::CONTROL);
        match k.code {
            KeyCode::Esc => return Action::Cancel,
            KeyCode::Char('s') if ctrl => return self.try_submit(),
            KeyCode::Tab | KeyCode::Down => self.move_focus(1),
            KeyCode::BackTab | KeyCode::Up => self.move_focus(-1),
            KeyCode::Enter => {
                if self.next_open(self.focus, 1).is_none() {
                    return self.try_submit();
                }
                self.move_focus(1);
            }
            _ => {
                let Some(f) = self.fields.get_mut(self.focus) else {
                    return Action::None;
                };
                if f.locked {
                    return Action::None;
                }
                self.error = None;
                match (&f.kind, k.code) {
                    (Kind::Toggle, KeyCode::Char(' ')) => {
                        f.value = if f.value.is_empty() { "1".into() } else { String::new() };
                    }
                    (Kind::Choice(opts), KeyCode::Char(' ') | KeyCode::Right | KeyCode::Left) => {
                        let opts = opts.clone();
                        let i = opts.iter().position(|(v, _)| *v == f.value).unwrap_or(0);
                        let n = opts.len().max(1);
                        let j = if k.code == KeyCode::Left {
                            (i + n - 1) % n
                        } else {
                            (i + 1) % n
                        };
                        if let Some((v, _)) = opts.get(j) {
                            f.value = v.clone();
                        }
                    }
                    (_, KeyCode::Char('u')) if ctrl && f.typed() => {
                        f.value.clear();
                        f.cursor = 0;
                    }
                    (_, KeyCode::Char(c)) if !ctrl && f.typed() => insert(f, c),
                    (_, KeyCode::Backspace) if f.typed() && f.cursor > 0 => {
                        let at = byte_at(&f.value, f.cursor - 1);
                        f.value.remove(at);
                        f.cursor -= 1;
                    }
                    (_, KeyCode::Delete) if f.typed() && f.cursor < f.value.chars().count() => {
                        let at = byte_at(&f.value, f.cursor);
                        f.value.remove(at);
                    }
                    (_, KeyCode::Left) if f.typed() => f.cursor = f.cursor.saturating_sub(1),
                    (_, KeyCode::Right) if f.typed() => f.cursor = (f.cursor + 1).min(f.value.chars().count()),
                    (_, KeyCode::Home) => f.cursor = 0,
                    (_, KeyCode::End) => f.cursor = f.value.chars().count(),
                    _ => {}
                }
            }
        }
        Action::None
    }

    fn try_submit(&mut self) -> Action {
        match self.check() {
            Ok(()) => Action::Submit,
            Err(e) => {
                self.error = Some(e);
                Action::None
            }
        }
    }

    fn next_open(&self, from: usize, step: isize) -> Option<usize> {
        let mut i = from as isize + step;
        while i >= 0 && (i as usize) < self.fields.len() {
            if !self.fields[i as usize].locked {
                return Some(i as usize);
            }
            i += step;
        }
        None
    }

    fn move_focus(&mut self, step: isize) {
        if let Some(i) = self.next_open(self.focus, step) {
            self.focus = i;
        }
    }

    /// The form's lines at this width, and where its cursor goes (row, col).
    pub fn lines(&self, width: usize) -> (Vec<Line<'static>>, Option<(usize, usize)>) {
        let label_w = self.fields.iter().map(|f| f.label.width()).max().unwrap_or(0).min(24) + 2;
        let value_w = width.saturating_sub(label_w + 2).max(10);
        let mut out = Vec::new();
        let mut cursor = None;
        if !self.intro.is_empty() {
            out.extend(wrap(vec![Span::styled(self.intro.clone(), DIM)], width, "", ""));
            out.push(Line::default());
        }
        for (i, f) in self.fields.iter().enumerate() {
            let here = i == self.focus;
            let mark = if f.required { "*" } else { " " };
            let label = format!("{:<w$}", format!("{}{mark}", f.label), w = label_w);
            let label_style = if here {
                Style::new().fg(Color::LightCyan).add_modifier(Modifier::BOLD)
            } else if f.locked {
                DIM
            } else {
                Style::new()
            };
            let mut shown = f.shown();
            // long values: the end that holds the cursor
            let shown_w = shown.width();
            let mut cur_col = if f.typed() {
                shown.chars().take(f.cursor).collect::<String>().width()
            } else {
                0
            };
            if shown_w > value_w {
                let skip = cur_col.saturating_sub(value_w.saturating_sub(1));
                shown = shown.chars().skip(skip).take(value_w).collect();
                cur_col -= skip;
            }
            let value_style = if f.locked {
                DIM
            } else if here {
                Style::new().bg(Color::Indexed(236))
            } else {
                Style::new().fg(Color::Gray)
            };
            let pad = if here && f.typed() {
                value_w.saturating_sub(shown.width())
            } else {
                0
            };
            out.push(Line::from(vec![
                Span::styled(label, label_style),
                Span::styled(shown, value_style),
                Span::styled(" ".repeat(pad), value_style),
            ]));
            if here && f.typed() && !f.locked {
                cursor = Some((out.len() - 1, label_w + cur_col));
            }
            if !f.help.is_empty() && (here || f.locked) {
                out.extend(wrap(
                    vec![Span::styled(f.help.clone(), DIM)],
                    width,
                    &" ".repeat(label_w),
                    &" ".repeat(label_w),
                ));
            }
        }
        out.push(Line::default());
        if let Some(e) = &self.error {
            out.extend(wrap(
                vec![Span::styled(e.clone(), Style::new().fg(Color::Red))],
                width,
                "✗ ",
                "  ",
            ));
        }
        let keys = if self.busy {
            "Sending…".to_string()
        } else if self.keys_line {
            format!("Ctrl+S {} · Tab next field · Esc cancel", self.submit.to_lowercase())
        } else {
            String::new()
        };
        if !keys.is_empty() {
            out.push(Line::from(Span::styled(keys, Style::new().fg(Color::LightCyan))));
        }
        (out, cursor)
    }

    pub fn draw(&self, f: &mut Frame, area: Rect) {
        let (lines, cursor) = self.lines(area.width as usize);
        let skip = cursor.map_or(0, |(row, _)| row.saturating_sub(area.height.saturating_sub(3) as usize));
        f.render_widget(Paragraph::new(lines.into_iter().skip(skip).collect::<Vec<_>>()), area);
        if let Some((row, col)) = cursor {
            let y = area.y + (row - skip) as u16;
            if y < area.y + area.height {
                f.set_cursor_position((area.x + col as u16, y));
            }
        }
    }
}

fn byte_at(s: &str, char_idx: usize) -> usize {
    s.char_indices().nth(char_idx).map(|(b, _)| b).unwrap_or(s.len())
}

fn insert(f: &mut Field, c: char) {
    if c.is_control() {
        return;
    }
    let at = byte_at(&f.value, f.cursor);
    f.value.insert(at, c);
    f.cursor += 1;
}

#[cfg(test)]
mod tests {
    use super::*;

    fn key(c: KeyCode) -> KeyEvent {
        KeyEvent::new(c, KeyModifiers::NONE)
    }

    fn form() -> Form {
        Form::new(
            "t",
            vec![
                Field::new("id", "Model ID", Kind::Text).required(),
                Field::new("key", "API key", Kind::Secret),
                Field::new("price", "Input $/1M", Kind::Number),
                Field::new(
                    "think",
                    "Thinking",
                    Kind::Choice(vec![("a".into(), "A".into()), ("b".into(), "B".into())]),
                ),
                Field::new("ok", "I understand", Kind::Toggle),
            ],
        )
    }

    fn typed(f: &mut Form, s: &str) {
        for c in s.chars() {
            f.key(key(KeyCode::Char(c)));
        }
    }

    #[test]
    fn typing_moving_and_sending() {
        let mut f = form();
        assert_eq!(
            f.key(KeyEvent::new(KeyCode::Char('s'), KeyModifiers::CONTROL)),
            Action::None
        );
        assert_eq!(f.error.as_deref(), Some("Model ID is needed."));
        typed(&mut f, "gpt-x");
        f.key(key(KeyCode::Tab));
        typed(&mut f, "sk-secret");
        f.key(key(KeyCode::Tab));
        typed(&mut f, "0,75");
        f.key(key(KeyCode::Tab));
        f.key(key(KeyCode::Right));
        f.key(key(KeyCode::Tab));
        f.key(key(KeyCode::Char(' ')));
        assert_eq!(
            f.key(key(KeyCode::Enter)),
            Action::Submit,
            "Enter on the last field sends"
        );
        assert_eq!((f.get("id"), f.get("key"), f.get("think")), ("gpt-x", "sk-secret", "b"));
        assert_eq!(f.number("price"), Ok(Some(0.75)));
        assert!(f.on("ok"));
    }

    #[test]
    fn a_choice_starts_on_its_first_option() {
        let f = Form::new(
            "t",
            vec![Field::new(
                "c",
                "C",
                Kind::Choice(vec![("x".into(), "X".into()), ("y".into(), "Y".into())]),
            )],
        );
        assert_eq!(f.get("c"), "x");
    }

    #[test]
    fn a_key_is_never_shown() {
        let mut f = form();
        f.key(key(KeyCode::Tab));
        typed(&mut f, "sk-secret");
        let (lines, _) = f.lines(60);
        let all: String = lines
            .iter()
            .flat_map(|l| l.spans.iter().map(|s| s.content.to_string()))
            .collect();
        assert!(!all.contains("sk-secret") && all.contains("•••••••••"), "{all}");
    }

    #[test]
    fn a_bad_number_is_named() {
        let mut f = form();
        typed(&mut f, "x");
        f.key(key(KeyCode::Tab));
        f.key(key(KeyCode::Tab));
        typed(&mut f, "cheap");
        assert_eq!(
            f.key(KeyEvent::new(KeyCode::Char('s'), KeyModifiers::CONTROL)),
            Action::None
        );
        assert!(f.error.as_deref().unwrap().starts_with("Input $/1M"));
    }

    #[test]
    fn locked_fields_are_skipped_and_kept() {
        let mut f = Form::new(
            "t",
            vec![
                Field::new("a", "A", Kind::Text).value("fixed").locked(),
                Field::new("b", "B", Kind::Text),
            ],
        );
        assert_eq!(f.focus, 1);
        f.key(key(KeyCode::Up));
        assert_eq!(f.focus, 1);
        typed(&mut f, "z");
        assert_eq!((f.get("a"), f.get("b")), ("fixed", "z"));
    }

    #[test]
    fn editing_in_the_middle_with_accents() {
        let mut f = form();
        typed(&mut f, "héllo");
        f.key(key(KeyCode::Left));
        f.key(key(KeyCode::Left));
        f.key(key(KeyCode::Backspace));
        typed(&mut f, "L");
        assert_eq!(f.get("id"), "héLlo");
    }
}
