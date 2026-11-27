//! "Are you sure?": y / n, or a sentence to type for what can't be undone (the web
//! page's "yes I want to delete …"). Enter never says yes to a plain question.

use crossterm::event::{KeyCode, KeyEvent};
use ratatui::style::{Color, Style};
use ratatui::text::{Line, Span};

use crate::chat::text::{DIM, wrap};

#[derive(Debug, Clone)]
pub struct Confirm {
    pub title: String,
    pub text: String,
    /// What a yes does ("delete", "remove the key").
    pub ok: String,
    /// The sentence to type; None: y / n.
    pub typed: Option<String>,
    pub input: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Answer {
    Waiting,
    Yes,
    No,
}

impl Confirm {
    pub fn new(title: impl Into<String>, text: impl Into<String>, ok: impl Into<String>) -> Confirm {
        Confirm {
            title: title.into(),
            text: text.into(),
            ok: ok.into(),
            typed: None,
            input: String::new(),
        }
    }

    #[allow(dead_code)] // the Projects section (step 3) asks for a typed sentence
    pub fn typed(mut self, sentence: impl Into<String>) -> Confirm {
        self.typed = Some(sentence.into());
        self
    }

    pub fn key(&mut self, k: KeyEvent) -> Answer {
        match (&self.typed, k.code) {
            (_, KeyCode::Esc) => Answer::No,
            (None, KeyCode::Char('y')) => Answer::Yes,
            (None, KeyCode::Char('n')) => Answer::No,
            (Some(want), KeyCode::Enter) if self.input.trim() == want.as_str() => Answer::Yes,
            (Some(_), KeyCode::Char(c)) => {
                self.input.push(c);
                Answer::Waiting
            }
            (Some(_), KeyCode::Backspace) => {
                self.input.pop();
                Answer::Waiting
            }
            _ => Answer::Waiting,
        }
    }

    pub fn lines(&self, width: usize) -> Vec<Line<'static>> {
        let mut out = Vec::new();
        for para in self.text.lines() {
            out.extend(wrap(vec![Span::raw(para.to_string())], width, "", ""));
        }
        out.push(Line::default());
        match &self.typed {
            None => out.push(Line::from(vec![
                Span::styled(" y ", Style::new().fg(Color::Black).bg(Color::Red)),
                Span::raw(format!(" {}   ", self.ok)),
                Span::styled(" n ", Style::new().fg(Color::Black).bg(Color::Gray)),
                Span::raw(" no"),
            ])),
            Some(want) => {
                out.extend(wrap(
                    vec![Span::styled(format!("To {}, type: {want}", self.ok), DIM)],
                    width,
                    "",
                    "",
                ));
                let ok = self.input.trim() == want.as_str();
                out.push(Line::from(vec![
                    Span::raw("> "),
                    Span::styled(
                        self.input.clone(),
                        Style::new().fg(if ok { Color::Red } else { Color::White }),
                    ),
                    Span::styled("▏", Style::new().fg(Color::LightCyan)),
                ]));
                out.push(Line::from(Span::styled("Enter confirms when it matches · Esc no", DIM)));
            }
        }
        out
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crossterm::event::KeyModifiers;

    fn k(c: KeyCode) -> KeyEvent {
        KeyEvent::new(c, KeyModifiers::NONE)
    }

    #[test]
    fn y_or_n_and_never_enter() {
        let mut c = Confirm::new("Delete?", "x", "delete");
        assert_eq!(c.key(k(KeyCode::Enter)), Answer::Waiting);
        assert_eq!(c.key(k(KeyCode::Char('y'))), Answer::Yes);
        assert_eq!(Confirm::new("", "", "").key(k(KeyCode::Esc)), Answer::No);
    }

    #[test]
    fn a_typed_sentence_must_match() {
        let mut c = Confirm::new("Delete?", "x", "delete it").typed("yes I want to delete demo");
        assert_eq!(c.key(k(KeyCode::Char('y'))), Answer::Waiting, "y is only a letter here");
        for ch in "es I want to delete demo".chars() {
            c.key(k(KeyCode::Char(ch)));
        }
        assert_eq!(c.key(k(KeyCode::Enter)), Answer::Yes);
        let mut wrong = Confirm::new("", "", "").typed("abc");
        wrong.key(k(KeyCode::Char('a')));
        assert_eq!(wrong.key(k(KeyCode::Enter)), Answer::Waiting);
    }
}
