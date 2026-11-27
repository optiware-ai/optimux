//! The keys window (Ctrl+b ?): every shortcut by kind (panes, tabs, chat, Dashboard,
//! shell…), with a search line. A click on one, or Enter, does it.

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use ratatui::Frame;
use ratatui::layout::Rect;
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, BorderType, Clear, Paragraph};

/// What a line of the window does when chosen.
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Run {
    /// Ctrl+b, then this key.
    Prefix(KeyEvent),
    /// This key, to the focused pane.
    Pane(KeyEvent),
    /// Only said here (a mouse action, a range of keys).
    Nothing,
}

pub struct Entry {
    pub group: &'static str,
    pub keys: &'static str,
    pub what: &'static str,
    pub run: Run,
}

const fn k(c: char) -> KeyEvent {
    KeyEvent::new(KeyCode::Char(c), KeyModifiers::NONE)
}
const fn ck(c: char) -> KeyEvent {
    KeyEvent::new(KeyCode::Char(c), KeyModifiers::CONTROL)
}
const fn code(c: KeyCode) -> KeyEvent {
    KeyEvent::new(c, KeyModifiers::NONE)
}

const fn e(group: &'static str, keys: &'static str, what: &'static str, run: Run) -> Entry {
    Entry { group, keys, what, run }
}

pub const ENTRIES: &[Entry] = &[
    e(
        "Panes",
        "Ctrl+b v",
        "split: a new pane on the right",
        Run::Prefix(k('v')),
    ),
    e("Panes", "Ctrl+b -", "split: a new pane below", Run::Prefix(k('-'))),
    e(
        "Panes",
        "Ctrl+b ←",
        "move to the pane on the left (h too)",
        Run::Prefix(code(KeyCode::Left)),
    ),
    e(
        "Panes",
        "Ctrl+b ↓",
        "move to the pane below (j too)",
        Run::Prefix(code(KeyCode::Down)),
    ),
    e(
        "Panes",
        "Ctrl+b ↑",
        "move to the pane above (k too)",
        Run::Prefix(code(KeyCode::Up)),
    ),
    e(
        "Panes",
        "Ctrl+b →",
        "move to the pane on the right (l too)",
        Run::Prefix(code(KeyCode::Right)),
    ),
    e(
        "Panes",
        "Ctrl+b Tab",
        "the next pane (Shift+Tab: the one before)",
        Run::Prefix(code(KeyCode::Tab)),
    ),
    e(
        "Panes",
        "Ctrl+b o",
        "flip the pane: shell ⇄ chat (the other keeps running behind)",
        Run::Prefix(k('o')),
    ),
    e(
        "Panes",
        "Ctrl+b z",
        "zoom the pane to the whole tab; again: back",
        Run::Prefix(k('z')),
    ),
    e("Panes", "Ctrl+b x", "close the pane", Run::Prefix(k('x'))),
    e(
        "Panes",
        "Ctrl+b r",
        "resize: then h j k l or the arrows",
        Run::Prefix(k('r')),
    ),
    e(
        "Panes",
        "drag a border",
        "resize two panes with the mouse",
        Run::Nothing,
    ),
    e("Panes", "click a pane", "move to it", Run::Nothing),
    e(
        "Panes",
        "Ctrl+b b",
        "show or hide the chat list (in a chat)",
        Run::Prefix(k('b')),
    ),
    e("Panes", "Ctrl+b Ctrl+b", "the pane gets Ctrl+b itself", Run::Nothing),
    e("Tabs", "Ctrl+b c", "new tab", Run::Prefix(k('c'))),
    e("Tabs", "Ctrl+b n", "the next tab", Run::Prefix(k('n'))),
    e("Tabs", "Ctrl+b p", "the tab before", Run::Prefix(k('p'))),
    e(
        "Tabs",
        "Ctrl+b 1…9",
        "that tab (or a click on it, bottom right)",
        Run::Nothing,
    ),
    e("Tabs", "Ctrl+b T", "name the tab", Run::Prefix(k('T'))),
    e(
        "Tabs",
        "yellow tab, !",
        "a question waits in one of its chats",
        Run::Nothing,
    ),
    e(
        "Quit",
        "Ctrl+b q",
        "quit everything; running requests keep running",
        Run::Prefix(k('q')),
    ),
    e(
        "Quit",
        "Ctrl+Q, Ctrl+C",
        "quit (asks first while a request runs)",
        Run::Pane(ck('q')),
    ),
    e(
        "Quit",
        "Ctrl+D",
        "quit, leaving the running request running",
        Run::Pane(ck('d')),
    ),
    e(
        "Chat",
        "Enter",
        "send the message (Alt+Enter, Ctrl+J: new line)",
        Run::Nothing,
    ),
    e("Chat", "Esc", "stop the running request", Run::Pane(code(KeyCode::Esc))),
    e(
        "Chat",
        "Ctrl+N",
        "new chat (a running request is left running)",
        Run::Pane(ck('n')),
    ),
    e("Chat", "Ctrl+O", "open a chat of this project", Run::Pane(ck('o'))),
    e("Chat", "Ctrl+P", "switch project", Run::Pane(ck('p'))),
    e(
        "Chat",
        "Alt+↑, Alt+↓",
        "the chat above, below in the list",
        Run::Nothing,
    ),
    e(
        "Chat",
        "Ctrl+E",
        "give the last request to the Complex model",
        Run::Pane(ck('e')),
    ),
    e(
        "Chat",
        "Ctrl+T",
        "details: the thinking and the notes in full",
        Run::Pane(ck('t')),
    ),
    e(
        "Chat",
        "Ctrl+R / Ctrl+K",
        "run / cancel a waiting plan (Ctrl+X skips a step)",
        Run::Nothing,
    ),
    e(
        "Chat",
        "PgUp, PgDn",
        "scroll the chat (Ctrl+End: back to its end)",
        Run::Nothing,
    ),
    e(
        "Chat",
        "Ctrl+W, Ctrl+U",
        "delete a word, the whole message",
        Run::Nothing,
    ),
    e("Chat", "Ctrl+L", "reconnect now", Run::Pane(ck('l'))),
    e("Chat", "y / n / a", "answer a question card", Run::Nothing),
    e("Chat", "Ctrl+H, F1", "the chat's own key list", Run::Pane(ck('h'))),
    e(
        "Dashboard",
        "Ctrl+G",
        "open the Dashboard (in a chat); again: back",
        Run::Pane(ck('g')),
    ),
    e(
        "Dashboard",
        "↑↓ Enter, click",
        "choose a section in the menu",
        Run::Nothing,
    ),
    e(
        "Dashboard",
        "Esc",
        "back to the menu; again: back to the chat",
        Run::Nothing,
    ),
    e("Dashboard", "click a row twice", "open it (as Enter)", Run::Nothing),
    e(
        "Dashboard",
        "the cyan line",
        "each section's keys, clickable",
        Run::Nothing,
    ),
    e("Dashboard", "/", "filter a list (Esc clears)", Run::Nothing),
    e("Studio", "e", "edit the file in your editor", Run::Nothing),
    e(
        "Studio",
        "v then s",
        "mark lines and send them to the chat",
        Run::Nothing,
    ),
    e("Studio", "g", "the Git pane: add, diff, commit, push", Run::Nothing),
    e(
        "Shell",
        "every key",
        "goes to the shell (Ctrl+C too); only Ctrl+b is kept",
        Run::Nothing,
    ),
    e(
        "Shell",
        "Ctrl+b o",
        "back to the chat; again: back to the shell",
        Run::Prefix(k('o')),
    ),
    e(
        "Shell",
        "the wheel",
        "scroll back through what scrolled off",
        Run::Nothing,
    ),
    e("Mouse", "Shift+drag", "select text to copy", Run::Nothing),
    e("Keys", "Ctrl+b ?", "this window (Ctrl+b H too)", Run::Nothing),
];

/// The entries whose group, keys or words hold every word typed.
pub fn matching(query: &str) -> Vec<usize> {
    let words: Vec<String> = query.to_lowercase().split_whitespace().map(str::to_string).collect();
    ENTRIES
        .iter()
        .enumerate()
        .filter(|(_, e)| {
            let hay = format!("{} {} {}", e.group, e.keys, e.what).to_lowercase();
            words.iter().all(|w| hay.contains(w.as_str()))
        })
        .map(|(i, _)| i)
        .collect()
}

#[derive(Default)]
pub struct Help {
    pub query: String,
    /// The chosen line among the matches.
    pub selected: usize,
    pub top: usize,
    /// Each drawn entry's place, for clicks.
    pub hits: Vec<(Rect, usize)>,
    pub area: Rect,
}

/// What a key in the window asks the workspace to do.
pub enum Outcome {
    Stay,
    Close,
    Run(Run),
}

impl Help {
    pub fn key(&mut self, key: KeyEvent) -> Outcome {
        let found = matching(&self.query);
        let ctrl = key.modifiers.contains(KeyModifiers::CONTROL);
        match key.code {
            KeyCode::Esc if !self.query.is_empty() => {
                self.query.clear();
                self.selected = 0;
            }
            KeyCode::Esc => return Outcome::Close,
            KeyCode::Down => self.selected = (self.selected + 1).min(found.len().saturating_sub(1)),
            KeyCode::Up => self.selected = self.selected.saturating_sub(1),
            KeyCode::PageDown => self.selected = (self.selected + 8).min(found.len().saturating_sub(1)),
            KeyCode::PageUp => self.selected = self.selected.saturating_sub(8),
            KeyCode::Enter => {
                if let Some(&i) = found.get(self.selected) {
                    if ENTRIES[i].run != Run::Nothing {
                        return Outcome::Run(ENTRIES[i].run);
                    }
                }
            }
            KeyCode::Backspace => {
                self.query.pop();
                self.selected = 0;
            }
            KeyCode::Char('u') if ctrl => {
                self.query.clear();
                self.selected = 0;
            }
            KeyCode::Char(c) if !ctrl => {
                self.query.push(c);
                self.selected = 0;
            }
            _ => {}
        }
        Outcome::Stay
    }

    pub fn click(&mut self, i: usize) -> Outcome {
        let found = matching(&self.query);
        if let Some(pos) = found.iter().position(|x| *x == i) {
            self.selected = pos;
        }
        match ENTRIES[i].run {
            Run::Nothing => Outcome::Stay,
            run => Outcome::Run(run),
        }
    }

    pub fn wheel(&mut self, down: bool) {
        let n = matching(&self.query).len();
        self.selected = if down {
            (self.selected + 3).min(n.saturating_sub(1))
        } else {
            self.selected.saturating_sub(3)
        };
    }

    /// The window at the bottom right of `area`.
    pub fn draw(&mut self, f: &mut Frame, area: Rect) {
        let w = 66.min(area.width.saturating_sub(2)).max(20);
        let h = 26.min(area.height.saturating_sub(1)).max(8);
        let r = Rect {
            x: area.x + area.width.saturating_sub(w + 1),
            y: area.y + area.height.saturating_sub(h),
            width: w,
            height: h,
        };
        self.area = r;
        f.render_widget(Clear, r);
        let accent = Style::new().fg(Color::LightCyan);
        let block = Block::bordered()
            .border_type(BorderType::Rounded)
            .border_style(accent)
            .title(Span::styled(" Keys ", accent.add_modifier(Modifier::BOLD)))
            .title_bottom(Span::styled(
                " type to find · ↑↓ · Enter or a click does it · Esc ",
                Style::new().fg(Color::DarkGray),
            ));
        let inner = block.inner(r);
        f.render_widget(block, r);
        // the search line
        let search = Line::from(vec![
            Span::styled("Find: ", accent),
            Span::raw(self.query.clone()),
            Span::styled("█", accent),
        ]);
        f.render_widget(Paragraph::new(search), Rect { height: 1, ..inner });
        let list = Rect {
            y: inner.y + 2,
            height: inner.height.saturating_sub(2),
            ..inner
        };
        let found = matching(&self.query);
        self.selected = self.selected.min(found.len().saturating_sub(1));
        // lines: a group's name before its first entry
        let mut lines: Vec<(Line, Option<usize>)> = Vec::new();
        let mut group = "";
        let key_w = 18usize;
        for (pos, &i) in found.iter().enumerate() {
            let en = &ENTRIES[i];
            if en.group != group {
                if !group.is_empty() {
                    lines.push((Line::default(), None));
                }
                group = en.group;
                lines.push((
                    Line::from(Span::styled(
                        en.group.to_uppercase(),
                        Style::new().fg(Color::Gray).add_modifier(Modifier::BOLD),
                    )),
                    None,
                ));
            }
            let chosen = pos == self.selected;
            let runs = en.run != Run::Nothing;
            let key_style = if runs {
                Style::new().fg(Color::LightCyan)
            } else {
                Style::new().fg(Color::Cyan)
            };
            let row = if chosen {
                Style::new().bg(Color::Indexed(237))
            } else {
                Style::new()
            };
            let keys = format!("{:<key_w$}", en.keys);
            let what_w = (list.width as usize).saturating_sub(key_w + 3);
            lines.push((
                Line::from(vec![
                    Span::styled(if chosen { "▸ " } else { "  " }, row.fg(Color::LightCyan)),
                    Span::styled(keys, row.patch(key_style)),
                    Span::styled(format!(" {}", crate::ui::table::fit(en.what, what_w)), row),
                ]),
                Some(pos),
            ));
        }
        if found.is_empty() {
            lines.push((
                Line::from(Span::styled(
                    "Nothing: try another word (Esc clears).",
                    Style::new().fg(Color::DarkGray),
                )),
                None,
            ));
        }
        // keep the chosen line in sight
        let sel_line = lines.iter().position(|(_, p)| *p == Some(self.selected)).unwrap_or(0);
        let h = list.height as usize;
        if sel_line < self.top {
            self.top = sel_line.saturating_sub(1);
        } else if h > 0 && sel_line >= self.top + h {
            self.top = sel_line + 1 - h;
        }
        self.top = self.top.min(lines.len().saturating_sub(h));
        self.hits.clear();
        let mut shown = Vec::new();
        for (n, (line, pos)) in lines.into_iter().skip(self.top).take(h).enumerate() {
            if let Some(p) = pos {
                let y = list.y + n as u16;
                self.hits.push((Rect { y, height: 1, ..list }, found[p]));
            }
            shown.push(line);
        }
        f.render_widget(Paragraph::new(shown), list);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn finding_keys_by_words() {
        let all = matching("");
        assert_eq!(all.len(), ENTRIES.len());
        let split = matching("split");
        assert!(split.iter().all(|&i| ENTRIES[i].what.contains("split")));
        assert_eq!(split.len(), 2);
        // words of the group and the keys count too
        assert!(matching("tabs new").iter().any(|&i| ENTRIES[i].keys == "Ctrl+b c"));
        assert!(matching("ctrl+g").iter().any(|&i| ENTRIES[i].group == "Dashboard"));
        assert!(matching("zzzz").is_empty());
    }

    #[test]
    fn enter_does_the_chosen_one() {
        let mut h = Help::default();
        for c in "zoom".chars() {
            assert!(matches!(h.key(k(c)), Outcome::Stay));
        }
        assert!(matches!(h.key(code(KeyCode::Enter)), Outcome::Run(Run::Prefix(x)) if x == k('z')));
        // Esc clears the words first, then closes
        assert!(matches!(h.key(code(KeyCode::Esc)), Outcome::Stay));
        assert!(h.query.is_empty());
        assert!(matches!(h.key(code(KeyCode::Esc)), Outcome::Close));
    }
}
