//! Dashboard → Guide: docs/GUIDE.md from the server (GET /api/guide), as in the web
//! page: the contents (its ## and ### headings), the text, and a search over every
//! section. `parse` and `find` are shared with `optimux-tui guide`.

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use ratatui::Frame;
use ratatui::layout::Rect;
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::Paragraph;

use super::{Answer, Ctx, Section, draw_head, head_lines};
use crate::api::Method;
use crate::chat::text::{DIM, markdown};
use crate::cli::s;
use crate::ui::table::{Row, Table};

/// One ## or ### section: its heading, where it sits, its Markdown (heading
/// included) and its text without Markdown (for the search).
#[derive(Clone, Debug, PartialEq)]
pub struct Sec {
    pub title: String,
    pub level: usize,
    /// "Chapter › Section" for a ###, the title for a ##.
    pub path: String,
    pub src: String,
    pub text: String,
}

/// The guide cut at its ## and ### headings (outside code blocks), as the web page
/// does; the text before the first one comes first, as its own part.
pub fn parse(md: &str) -> (String, Vec<Sec>) {
    let (mut intro, mut secs, mut fence, mut chapter) = (String::new(), Vec::<Sec>::new(), false, String::new());
    for line in md.lines() {
        if line.trim_start().starts_with("```") {
            fence = !fence;
        }
        let head = (!fence)
            .then(|| {
                line.strip_prefix("### ")
                    .map(|t| (3, t))
                    .or_else(|| line.strip_prefix("## ").map(|t| (2, t)))
            })
            .flatten();
        if let Some((level, title)) = head {
            let title = title.trim().to_string();
            if level == 2 {
                chapter = title.clone();
            }
            let path = if level == 3 && !chapter.is_empty() {
                format!("{chapter} › {title}")
            } else {
                title.clone()
            };
            secs.push(Sec {
                title,
                level,
                path,
                src: format!("{line}\n"),
                text: String::new(),
            });
        } else if let Some(sec) = secs.last_mut() {
            sec.src.push_str(line);
            sec.src.push('\n');
        } else {
            intro.push_str(line);
            intro.push('\n');
        }
    }
    for sec in &mut secs {
        sec.text = plain(&sec.src);
    }
    (intro, secs)
}

/// Markdown as plain words (the page's plainMd): no code blocks, marks or links.
fn plain(md: &str) -> String {
    let mut out = String::new();
    let mut fence = false;
    for line in md.lines() {
        if line.trim_start().starts_with("```") {
            fence = !fence;
            continue;
        }
        if fence || line.starts_with("## ") || line.starts_with("### ") {
            continue;
        }
        // [text](url) → text
        let mut l = String::new();
        let mut rest = line;
        while let Some(a) = rest.find('[') {
            let Some(b) = rest[a..].find("](").map(|b| a + b) else {
                break;
            };
            let Some(c) = rest[b..].find(')').map(|c| b + c) else {
                break;
            };
            l.push_str(&rest[..a]);
            l.push_str(&rest[a + 1..b]);
            rest = &rest[c + 1..];
        }
        l.push_str(rest);
        out.push_str(&l.replace(['`', '*', '_', '>', '#', '|'], " "));
        out.push(' ');
    }
    out.split_whitespace().collect::<Vec<_>>().join(" ")
}

/// The sections holding every word (titles first), with a snippet around the first
/// word; at most 25, as in the web page.
pub fn find(secs: &[Sec], query: &str) -> Vec<(usize, String)> {
    let words: Vec<String> = query.to_lowercase().split_whitespace().map(str::to_string).collect();
    if words.is_empty() {
        return Vec::new();
    }
    let mut out: Vec<(usize, usize, String)> = Vec::new();
    for (i, sec) in secs.iter().enumerate() {
        let hay = format!("{} {}", sec.title, sec.text).to_lowercase();
        if !words.iter().all(|w| hay.contains(w.as_str())) {
            continue;
        }
        let low = sec.text.to_lowercase();
        let at = low.find(words[0].as_str()).unwrap_or(0);
        let chars: Vec<char> = sec.text.chars().collect();
        // positions in characters, not bytes (the text has accents and arrows)
        let at_c = sec.text[..at.min(sec.text.len())].chars().count();
        let from = at_c.saturating_sub(40);
        let to = (at_c + 120).min(chars.len());
        let mut snip: String = chars[from..to].iter().collect();
        if from > 0 {
            snip.insert(0, '…');
        }
        if to < chars.len() {
            snip.push('…');
        }
        let score = usize::from(!sec.title.to_lowercase().contains(words[0].as_str()));
        out.push((score, i, snip));
    }
    out.sort_by_key(|(score, i, _)| (*score, *i));
    out.into_iter().take(25).map(|(_, i, s)| (i, s)).collect()
}

/// The section a reader means by these words: the title itself, else a title holding
/// every word, else the first search result.
pub fn pick(secs: &[Sec], words: &str) -> Option<usize> {
    let w = words.trim().to_lowercase();
    secs.iter()
        .position(|s| s.title.to_lowercase() == w)
        .or_else(|| {
            let ws: Vec<&str> = w.split_whitespace().collect();
            secs.iter().position(|s| {
                let t = s.title.to_lowercase();
                !ws.is_empty() && ws.iter().all(|x| t.contains(x))
            })
        })
        .or_else(|| find(secs, &w).first().map(|(i, _)| *i))
}

/// A ## section with its ### sections (all of a chapter), or one ### section.
pub fn whole(secs: &[Sec], i: usize) -> String {
    let mut out = secs[i].src.clone();
    if secs[i].level == 2 {
        for s in secs[i + 1..].iter().take_while(|s| s.level > 2) {
            out.push_str(&s.src);
        }
    }
    out
}

enum View {
    Contents,
    Read,
    Results,
}

pub struct Guide {
    intro: String,
    secs: Vec<Sec>,
    error: Option<String>,
    loaded: bool,
    view: View,
    contents: Table,
    results: Table,
    query: String,
    typing: bool,
    scroll: usize,
    /// The text drawn at a width: its lines and where each section starts.
    drawn: Option<(usize, Vec<Line<'static>>, Vec<usize>)>,
    /// The section to show once the text is drawn (its width is known then).
    want: Option<usize>,
    height: usize,
}

impl Default for Guide {
    fn default() -> Guide {
        let mut contents = Table::new(&[("Contents", 0)]);
        contents.empty = "Loading the guide…".into();
        let mut results = Table::new(&[("Section", 34), ("Where the words are", 0)]);
        results.empty = "Nothing found. Try another word.".into();
        Guide {
            intro: String::new(),
            secs: Vec::new(),
            error: None,
            loaded: false,
            view: View::Contents,
            contents,
            results,
            query: String::new(),
            typing: false,
            scroll: 0,
            drawn: None,
            want: None,
            height: 20,
        }
    }
}

impl Guide {
    fn open_at(&mut self, i: usize) {
        self.view = View::Read;
        self.want = Some(i);
    }

    /// The section the reader is in (the last one starting at or above the top line).
    fn current(&self) -> Option<usize> {
        let (_, _, starts) = self.drawn.as_ref()?;
        starts.iter().rposition(|st| *st <= self.scroll)
    }

    fn jump(&mut self, forward: bool, chapters: bool) {
        let Some((_, _, starts)) = &self.drawn else { return };
        let here = self.current();
        let ok = |i: usize| !chapters || self.secs[i].level == 2;
        let next = if forward {
            (here.map_or(0, |h| h + 1)..self.secs.len()).find(|i| ok(*i))
        } else {
            match here {
                // at a section's first line: the one before; inside it: its start
                Some(h) if starts[h] < self.scroll && ok(h) => Some(h),
                Some(h) => (0..h).rev().find(|i| ok(*i)),
                None => None,
            }
        };
        if let Some(n) = next {
            self.scroll = starts[n];
        }
    }

    fn search(&mut self) {
        let rows = find(&self.secs, &self.query)
            .into_iter()
            .map(|(i, snip)| {
                Row::new(i.to_string())
                    .cell(self.secs[i].path.clone())
                    .styled(snip, DIM)
            })
            .collect();
        self.results.set_rows(rows);
        self.results.selected = 0;
        self.view = View::Results;
    }
}

impl Section for Guide {
    fn load(&mut self, ctx: &Ctx) {
        if !self.loaded {
            ctx.call("guide", Method::Get, "/api/guide", None);
        }
    }

    fn key(&mut self, k: KeyEvent, _ctx: &Ctx) -> bool {
        if self.typing {
            match k.code {
                KeyCode::Esc => self.typing = false,
                KeyCode::Enter => {
                    self.typing = false;
                    if !self.query.trim().is_empty() {
                        self.search();
                    }
                }
                KeyCode::Backspace => {
                    self.query.pop();
                }
                KeyCode::Char('u') if k.modifiers.contains(KeyModifiers::CONTROL) => self.query.clear(),
                KeyCode::Char(c) if !k.modifiers.contains(KeyModifiers::CONTROL) => self.query.push(c),
                _ => {}
            }
            return true;
        }
        if k.modifiers.contains(KeyModifiers::CONTROL) {
            return false;
        }
        if k.code == KeyCode::Char('f') && self.loaded {
            self.typing = true;
            return true;
        }
        let page = self.height.saturating_sub(2).max(1);
        match self.view {
            View::Contents => {
                if k.code == KeyCode::Enter {
                    if let Some(i) = self.contents.chosen().and_then(|r| r.id.parse().ok()) {
                        self.open_at(i);
                    }
                    return true;
                }
                self.contents.key(k)
            }
            View::Results => match k.code {
                KeyCode::Enter => {
                    if let Some(i) = self.results.chosen().and_then(|r| r.id.parse().ok()) {
                        self.open_at(i);
                    }
                    true
                }
                KeyCode::Esc | KeyCode::Char('c') => {
                    self.view = View::Contents;
                    true
                }
                _ => self.results.key(k),
            },
            View::Read => {
                match k.code {
                    KeyCode::Down | KeyCode::Char('j') => self.scroll += 1,
                    KeyCode::Up | KeyCode::Char('k') => self.scroll = self.scroll.saturating_sub(1),
                    KeyCode::PageDown | KeyCode::Char(' ') => self.scroll += page,
                    KeyCode::PageUp => self.scroll = self.scroll.saturating_sub(page),
                    KeyCode::Home => self.scroll = 0,
                    KeyCode::End => self.scroll = usize::MAX,
                    KeyCode::Char('n') => self.jump(true, false),
                    KeyCode::Char('p') => self.jump(false, false),
                    KeyCode::Char('N') => self.jump(true, true),
                    KeyCode::Char('P') => self.jump(false, true),
                    KeyCode::Esc | KeyCode::Char('c') => {
                        // back to the contents, on the section being read
                        if let Some(i) = self.current() {
                            if let Some(pos) = self.contents.visible().iter().position(|r| r.id == i.to_string()) {
                                self.contents.selected = pos;
                            }
                        }
                        self.view = View::Contents;
                    }
                    KeyCode::Char('r') if !self.results.rows.is_empty() => self.view = View::Results,
                    _ => return false,
                }
                true
            }
        }
    }

    fn paste(&mut self, text: &str) {
        if self.typing {
            self.query.push_str(&text.replace(['\n', '\r'], " "));
        }
    }

    fn on_answer(&mut self, a: Answer, _ctx: &Ctx) {
        match a.result {
            Ok(v) => {
                let (intro, secs) = parse(&s(&v, "content"));
                self.intro = intro;
                self.secs = secs;
                self.loaded = true;
                self.error = None;
                self.drawn = None;
                let bold = Style::new().add_modifier(Modifier::BOLD);
                let rows = self
                    .secs
                    .iter()
                    .enumerate()
                    .map(|(i, sec)| {
                        if sec.level == 2 {
                            Row::new(i.to_string()).styled(sec.title.clone(), bold)
                        } else {
                            Row::new(i.to_string()).cell(format!("    {}", sec.title))
                        }
                    })
                    .collect();
                self.contents.set_rows(rows);
                self.contents.empty = "The guide is empty.".into();
            }
            Err(e) => {
                self.contents.empty = String::new();
                self.error = Some(e);
            }
        }
    }

    fn keys(&self) -> String {
        if self.typing {
            return "type words · Enter find · Esc cancel".into();
        }
        match self.view {
            View::Contents => "↑↓ choose · Enter read · f find in the guide · / filter the headings".into(),
            View::Results => "↑↓ choose · Enter read there · f find again · c contents".into(),
            View::Read => {
                let r = if self.results.rows.is_empty() {
                    ""
                } else {
                    " · r results"
                };
                format!("↑↓ PgUp PgDn Space scroll · n/p next/previous section · N/P chapter · c contents · f find{r}")
            }
        }
    }

    fn draw(&mut self, f: &mut Frame, area: Rect) -> Vec<(u16, usize)> {
        let width = area.width as usize;
        let extra = match (&self.view, self.current()) {
            (View::Read, Some(i)) => format!("  ·  {}", self.secs[i].path),
            (View::Results, _) => format!("  ·  {} result(s) for “{}”", self.results.rows.len(), self.query.trim()),
            _ => "  ·  everything about Optimux, from docs/GUIDE.md".into(),
        };
        let msg = self
            .error
            .clone()
            .map(|e| (false, format!("The guide could not be loaded: {e}")));
        let mut head = head_lines("Guide", &extra, &msg, width);
        if self.typing {
            head.push(Line::from(vec![
                Span::styled("Find: ", Style::new().fg(Color::LightCyan)),
                Span::raw(self.query.clone()),
                Span::styled("█", Style::new().fg(Color::LightCyan)),
            ]));
        }
        let body = draw_head(f, area, head);
        self.height = body.height as usize;
        match self.view {
            View::Contents => self.contents.draw(f, body),
            View::Results => self.results.draw(f, body),
            View::Read => {
                let w = width.saturating_sub(1).max(20);
                if self.drawn.as_ref().is_none_or(|(dw, ..)| *dw != w) {
                    // keep the place when the width changes
                    let here = self.current();
                    let mut lines = markdown(&self.intro, w);
                    let mut starts = Vec::new();
                    for sec in &self.secs {
                        starts.push(lines.len());
                        lines.extend(markdown(&sec.src, w));
                    }
                    self.drawn = Some((w, lines, starts));
                    if self.want.is_none() {
                        self.want = here;
                    }
                }
                let (_, lines, starts) = self.drawn.as_ref().expect("drawn above");
                if let Some(i) = self.want.take() {
                    self.scroll = starts.get(i).copied().unwrap_or(0);
                }
                self.scroll = self.scroll.min(lines.len().saturating_sub(body.height as usize));
                let shown: Vec<Line> = lines
                    .iter()
                    .skip(self.scroll)
                    .take(body.height as usize)
                    .cloned()
                    .collect();
                f.render_widget(Paragraph::new(shown), body);
                Vec::new()
            }
        }
    }

    fn has_window(&self) -> bool {
        self.typing || self.contents.filtering || self.results.filtering
    }

    fn click_row(&mut self, i: usize, _ctx: &Ctx) -> bool {
        match self.view {
            View::Contents => {
                if let Some(id) = self.contents.visible().get(i).and_then(|r| r.id.parse().ok()) {
                    self.contents.selected = i;
                    self.open_at(id);
                }
            }
            View::Results => {
                if let Some(id) = self.results.visible().get(i).and_then(|r| r.id.parse().ok()) {
                    self.open_at(id);
                }
            }
            View::Read => return false,
        }
        // a heading or a result opens at once
        true
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const MD: &str = "# Guide\nhello\n## Start\nintro words\n```\n## not a heading\n```\n### Install\nRun `optimux` with [the script](x.sh).\n## Models\n### Adding a model\nKeys go in secrets.env.\n";

    #[test]
    fn the_guide_is_cut_at_its_headings() {
        let (intro, secs) = parse(MD);
        assert_eq!(intro, "# Guide\nhello\n");
        let titles: Vec<&str> = secs.iter().map(|s| s.path.as_str()).collect();
        assert_eq!(
            titles,
            vec!["Start", "Start › Install", "Models", "Models › Adding a model"]
        );
        assert!(secs[0].src.contains("## not a heading"));
        assert_eq!(secs[1].text, "Run optimux with the script.");
        assert!(whole(&secs, 2).contains("Keys go in"));
        assert!(!whole(&secs, 1).contains("Models"));
    }

    #[test]
    fn finding_and_picking() {
        let (_, secs) = parse(MD);
        let r = find(&secs, "SECRETS keys");
        assert_eq!(r.len(), 1);
        assert_eq!(secs[r[0].0].title, "Adding a model");
        // a title match comes first
        assert_eq!(find(&secs, "install")[0].0, 1);
        assert_eq!(pick(&secs, "models"), Some(2));
        assert_eq!(pick(&secs, "adding"), Some(3));
        assert_eq!(pick(&secs, "secrets"), Some(3));
        assert_eq!(pick(&secs, "nothing like this"), None);
        assert!(find(&secs, "   ").is_empty());
    }
}
