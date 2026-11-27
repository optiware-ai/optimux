//! Draws the screen from `App`. Nothing here changes the chat.
//!
//!  Optimux · project · chat title                       ● connected
//!  (the chat)
//!  ⠋ Writing · gemini/flash                        12.3k tokens · $0.0042
//!  ┌ Message ───────────────────────────────────────────────────────────┐
//!  │ ...                                                                │
//!  └────────────────────────────────────────────────────────────────────┘
//!  Enter send · Alt+Enter new line · Esc stop · Ctrl+O chats · Ctrl+H keys

use ratatui::Frame;
use ratatui::layout::{Constraint, Layout, Rect};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, BorderType, Borders, Clear, Paragraph, Wrap};
use serde_json::Value;
use unicode_width::UnicodeWidthStr;

use super::app::{App, Click, Modal, PickKind};
use super::state::{Ask, Entry, Kind, Note, State, kind_of};
use super::text::{self, CODE, DIM, hard_wrap, markdown, wrap};
use crossterm::event::{KeyCode, KeyModifiers};

const SPIN: [&str; 10] = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"];
const ACCENT: Color = Color::LightCyan;
/// Lines of command output shown in the chat (the start and the end of longer ones).
const OUTPUT_LINES: usize = 12;

/// The chat list's width, and the narrowest terminal that still shows it.
const SIDE_W: u16 = 30;
const SIDE_FROM: u16 = 100;

/// The chat window in `area` (all the screen, or its pane).
pub fn draw_in(f: &mut Frame, app: &mut App, area: Rect) {
    app.hits.clear();
    let [body, keys] = Layout::vertical([Constraint::Min(5), Constraint::Length(1)]).areas(area);
    if app.dash.is_some() && matches!(app.modal, None | Some(Modal::ConfirmQuit)) {
        return draw_dash(f, app, body, keys, area);
    }
    let main = if app.sidebar && area.width >= SIDE_FROM {
        let [side, main] = Layout::horizontal([Constraint::Length(SIDE_W), Constraint::Min(40)]).areas(body);
        draw_side(f, app, side);
        main
    } else {
        body
    };
    let input_h = input_height(app, main.width) + 2;
    let [top, chat, status, input] = Layout::vertical([
        Constraint::Length(1),
        Constraint::Min(3),
        Constraint::Length(1),
        Constraint::Length(input_h),
    ])
    .areas(main);
    draw_top(f, app, top);
    draw_chat(f, app, chat);
    draw_status(f, app, status);
    draw_input(f, app, input);
    draw_keys(f, app, keys);
    if let Some(Ask::Approval(_) | Ask::Limit { .. }) = &app.state.ask {
        if !matches!(app.modal, Some(Modal::Reason(_))) {
            draw_ask(f, app, chat);
        }
    }
    match &app.modal {
        Some(Modal::Reason(ed)) => draw_reason(f, &ed.text(), &ed.before_cursor(), area),
        Some(Modal::Picker(_)) => draw_picker(f, app, area),
        Some(Modal::ConfirmQuit) => draw_confirm_quit(f, app, area),
        Some(Modal::Help) => draw_help(f, area), // a click anywhere closes it
        None => {}
    }
}

/// The chat list: the project's chats, the open one marked, each with its state:
/// ● a question waits for you, ⠋ working, ✓ finished while you were elsewhere.
fn draw_side(f: &mut Frame, app: &mut App, area: Rect) {
    let block = Block::new().borders(Borders::RIGHT).border_style(RAIL);
    let inner = block.inner(area);
    f.render_widget(block, area);
    let width = inner.width as usize;
    let new = "+ New ";
    let head = Line::from(vec![
        Span::styled(" Chats", Style::new().add_modifier(Modifier::BOLD)),
        Span::raw(" ".repeat(width.saturating_sub(6 + new.width()))),
        Span::styled(new, Style::new().fg(ACCENT)),
    ]);
    f.render_widget(Paragraph::new(head), Rect { height: 1, ..inner });
    app.hits.push((
        Rect {
            x: inner.x + inner.width.saturating_sub(new.width() as u16),
            y: inner.y,
            width: new.width() as u16,
            height: 1,
        },
        ctrl('n'),
    ));
    let rows_h = inner.height.saturating_sub(2) as usize;
    let list = Rect {
        y: inner.y + 2,
        height: rows_h as u16,
        ..inner
    };
    if app.chats.is_empty() {
        f.render_widget(Paragraph::new(Span::styled(" No chats yet.", DIM)), list);
        return;
    }
    let open = app.chats.iter().position(|c| Some(&c.id) == app.state.chat_id.as_ref());
    let first = open.map_or(0, |i| i.saturating_sub(rows_h.saturating_sub(1)));
    let mut lines = Vec::new();
    for (i, c) in app.chats.iter().enumerate().skip(first).take(rows_h) {
        let (badge, color) = match c.state.as_deref() {
            Some("waiting") => ("●", Color::Yellow),
            Some(_) => (SPIN[app.spinner % SPIN.len()], ACCENT),
            None if app.done.contains(&c.id) => ("✓", Color::Green),
            None => (" ", Color::Reset),
        };
        let here = Some(i) == open;
        let base = if here {
            Style::new().bg(USER_BG).add_modifier(Modifier::BOLD)
        } else {
            Style::new()
        };
        let title_style = if c.away && !here { base.fg(Color::Gray) } else { base };
        let room = width.saturating_sub(4);
        let mut title = c.title.clone();
        if title.width() > room {
            title = title.chars().take(room.saturating_sub(1)).collect::<String>() + "…";
        }
        let pad = width.saturating_sub(title.width() + 3);
        lines.push(Line::from(vec![
            Span::styled(" ", base),
            Span::styled(badge, base.fg(color).add_modifier(Modifier::BOLD)),
            Span::styled(" ", base),
            Span::styled(title, title_style),
            Span::styled(" ".repeat(pad), base),
        ]));
        let y = list.y + (i - first) as u16;
        app.hits.push((
            Rect {
                x: inner.x,
                y,
                width: inner.width,
                height: 1,
            },
            Click::Chat(i),
        ));
    }
    f.render_widget(Paragraph::new(lines), list);
}

fn draw_top(f: &mut Frame, app: &mut App, area: Rect) {
    let project = match app.state.project.as_deref() {
        Some("") | None => "Workspace".to_string(),
        Some(p) => p.to_string(),
    };
    let title = if app.state.chat_title.is_empty() {
        "New chat"
    } else {
        app.state.chat_title.as_str()
    };
    // the project opens the projects, the chat's title the chats
    let px = area.x + 11; // " Optimux · "
    let pw = project.width() as u16;
    let tw = title.width() as u16;
    app.hits.push((
        Rect {
            x: px,
            y: area.y,
            width: pw,
            height: 1,
        },
        ctrl('p'),
    ));
    app.hits.push((
        Rect {
            x: px + pw + 3,
            y: area.y,
            width: tw,
            height: 1,
        },
        ctrl('o'),
    ));
    let left = Line::from(vec![
        Span::styled(" Optimux", Style::new().fg(ACCENT).add_modifier(Modifier::BOLD)),
        Span::styled(" · ", DIM),
        Span::styled(project, Style::new().add_modifier(Modifier::BOLD)),
        Span::styled(" · ", DIM),
        Span::raw(title.to_string()),
    ]);
    let right = if app.connected {
        Span::styled("● connected ", Style::new().fg(Color::Green))
    } else {
        Span::styled("○ offline ", Style::new().fg(Color::Yellow))
    };
    f.render_widget(Paragraph::new(left), area);
    let w = right.width() as u16;
    if area.width > w {
        f.render_widget(
            Paragraph::new(Line::from(right)),
            Rect {
                x: area.x + area.width - w,
                width: w,
                ..area
            },
        );
    }
}

/// Text wraps at this many columns at most: long lines on a wide screen are hard to read.
pub const READ_WIDTH: usize = 120;
/// The background of your messages (256-colour grey, close to the page's bubbles).
const USER_BG: Color = Color::Indexed(236);
const RAIL: Style = Style::new().fg(Color::DarkGray);

/// The chat as lines at this width. Each kind of entry has its own look, so a turn
/// reads at a glance: your message on a shaded band, Optimux's answer under its
/// label and indented, commands in a frame, warnings and errors with a coloured bar,
/// and routine notes kept to one dim line (in full with `details`).
pub fn chat_lines(state: &State, width: usize, details: bool) -> Vec<Line<'static>> {
    let width = width.min(READ_WIDTH);
    let body = width.saturating_sub(2); // the answer and the rest of a turn are indented by 2
    let mut out: Vec<Line<'static>> = Vec::new();
    // Optimux's label goes above the first answer of each turn
    let mut labelled = false;
    for e in &state.entries {
        match e {
            Entry::User(t) => {
                if !out.is_empty() {
                    out.push(Line::default());
                }
                out.extend(user_block(t, width));
                out.push(Line::default());
                labelled = false;
            }
            Entry::Assistant(t) => {
                if !labelled {
                    if !out.is_empty() && !out.last().is_some_and(|l| l.width() == 0) {
                        out.push(Line::default());
                    }
                    out.push(Line::from(Span::styled(
                        "◆ Optimux",
                        Style::new().fg(Color::LightMagenta).add_modifier(Modifier::BOLD),
                    )));
                    labelled = true;
                }
                out.extend(indent(markdown(t, body), "  "));
            }
            Entry::Thinking(t) => {
                let style = DIM.add_modifier(Modifier::ITALIC);
                if details {
                    for line in t.lines() {
                        out.extend(wrap(vec![Span::styled(line.to_string(), style)], width, "  ┊ ", "  ┊ "));
                    }
                } else {
                    let words = t.split_whitespace().count();
                    out.push(Line::from(Span::styled(
                        format!("  ┊ thinking ({words} words) · Ctrl+T shows it"),
                        style,
                    )));
                }
            }
            Entry::Note(Note::Info, t) => {
                if details {
                    for (n, line) in t.lines().enumerate() {
                        let first = if n == 0 { "  · " } else { "    " };
                        out.extend(wrap(text::inline(line, DIM), width, first, "    "));
                    }
                } else {
                    // routine notes (routing, the Conductor...): one line each
                    let one = t.lines().next().unwrap_or("");
                    let more = t.lines().count() > 1;
                    out.push(Line::from(Span::styled(cut(&format!("  · {one}"), width, more), DIM)));
                }
            }
            Entry::Note(kind, t) => {
                let (bar, mark, color) = match kind {
                    Note::Error => ("▌", "✗ ", Color::Red),
                    _ => ("▌", "! ", Color::Yellow),
                };
                let style = Style::new().fg(color);
                let prefix = format!("{bar}{mark}");
                for (n, line) in t.lines().enumerate() {
                    let first = if n == 0 { prefix.as_str() } else { "▌  " };
                    let mut lines = wrap(text::inline(line, style), width, first, "▌  ");
                    for l in &mut lines {
                        if let Some(s) = l.spans.first_mut() {
                            s.style = style.add_modifier(Modifier::BOLD);
                        }
                    }
                    out.extend(lines);
                }
            }
            Entry::Command(c) => {
                out.extend(command_block(c, body));
            }
            Entry::Changes(files, more) => {
                out.push(Line::from(Span::styled(
                    "  Files changed",
                    Style::new().add_modifier(Modifier::BOLD),
                )));
                for (status, path, added, removed) in files {
                    let (mark, color) = match status.as_str() {
                        "new" => ("+", Color::Green),
                        "deleted" => ("−", Color::Red),
                        _ => ("~", Color::Yellow),
                    };
                    out.push(Line::from(vec![
                        Span::styled(format!("    {mark} "), Style::new().fg(color)),
                        Span::raw(path.clone()),
                        Span::styled(format!("  +{added} −{removed}"), DIM),
                    ]));
                }
                if *more > 0 {
                    out.push(Line::from(Span::styled(format!("    … and {more} more"), DIM)));
                }
            }
            Entry::Plan(p) => {
                out.push(Line::default());
                out.push(Line::from(vec![
                    Span::styled("  Plan: ", Style::new().add_modifier(Modifier::BOLD)),
                    Span::raw(p.goal.clone()),
                    Span::styled(format!("  [{}]", p.status), DIM),
                ]));
                for (n, (title, status)) in p.steps.iter().enumerate() {
                    let mark = match status.as_str() {
                        "done" => "✓",
                        "running" => "▸",
                        "failed" | "error" => "✗",
                        "skipped" => "↷",
                        _ => " ",
                    };
                    out.extend(wrap(
                        vec![Span::raw(title.clone())],
                        width,
                        &format!("    {mark} {}. ", n + 1),
                        "       ",
                    ));
                }
                let keys = match p.status.as_str() {
                    "waiting" => "    Ctrl+R run · Ctrl+K cancel",
                    "paused" => "    Ctrl+R resume · Ctrl+X skip the step · Ctrl+K cancel",
                    _ => "",
                };
                if !keys.is_empty() {
                    out.push(Line::from(Span::styled(keys, Style::new().fg(ACCENT))));
                }
            }
        }
    }
    out
}

/// Your message: a shaded band the full reading width, with a bar on the left.
fn user_block(text: &str, width: usize) -> Vec<Line<'static>> {
    let bg = Style::new().bg(USER_BG);
    let bar = Style::new().fg(ACCENT).bg(USER_BG);
    let words = Style::new().fg(Color::White).bg(USER_BG).add_modifier(Modifier::BOLD);
    let inner = width.saturating_sub(4);
    let mut out = Vec::new();
    for line in text.lines() {
        for l in wrap(vec![Span::styled(line.to_string(), words)], inner, "", "") {
            let used = l.width();
            let mut spans = vec![Span::styled("▌ ", bar)];
            spans.extend(l.spans.into_iter().filter(|s| !s.content.is_empty()).map(|s| {
                let style = s.style.bg(USER_BG);
                Span::styled(s.content, style)
            }));
            spans.push(Span::styled(" ".repeat(width.saturating_sub(used + 2)), bg));
            out.push(Line::from(spans));
        }
    }
    out
}

/// A command and its output in a frame: `┌ $ cmd`, `│ output`, `└ exit 0`.
fn command_block(c: &super::state::Command, width: usize) -> Vec<Line<'static>> {
    let mut out = Vec::new();
    let cmd = Style::new().fg(Color::White).add_modifier(Modifier::BOLD);
    let w = width.saturating_sub(4);
    for (n, line) in c.command.lines().enumerate() {
        let lead = if n == 0 { "  ┌ $ " } else { "  │   " };
        for (i, piece) in hard_wrap(line, w, "", cmd).into_iter().enumerate() {
            let mut spans = vec![Span::styled(if i == 0 { lead } else { "  │   " }, RAIL)];
            spans.extend(piece.spans.into_iter().skip(1));
            out.push(Line::from(spans));
        }
    }
    if c.running {
        out.push(Line::from(Span::styled("  │ running…", DIM)));
    }
    if let Some(output) = &c.output {
        let lines: Vec<&str> = output.trim_end().lines().collect();
        let half = OUTPUT_LINES / 2;
        let gray = Style::new().fg(Color::Gray);
        let put = |line: &str, out: &mut Vec<Line<'static>>| {
            for piece in hard_wrap(line, w, "", gray) {
                let mut spans = vec![Span::styled("  │ ", RAIL)];
                spans.extend(piece.spans.into_iter().skip(1));
                out.push(Line::from(spans));
            }
        };
        if lines.len() > OUTPUT_LINES {
            for l in &lines[..half] {
                put(l, &mut out);
            }
            let hidden = lines.len() - half * 2;
            out.push(Line::from(Span::styled(format!("  │ … {hidden} more lines …"), DIM)));
            for l in &lines[lines.len() - half..] {
                put(l, &mut out);
            }
        } else {
            for l in &lines {
                put(l, &mut out);
            }
        }
        if c.cut {
            out.push(Line::from(Span::styled("  │ (the server shortened this output)", DIM)));
        }
    }
    match c.exit_code {
        Some(code) => {
            let style = if code == 0 {
                Style::new().fg(Color::Green)
            } else {
                Style::new().fg(Color::Red)
            };
            out.push(Line::from(vec![
                Span::styled("  └ ", RAIL),
                Span::styled(format!("exit {code}"), style),
            ]));
        }
        None if !c.running => out.push(Line::from(Span::styled("  └", RAIL))),
        None => {}
    }
    out
}

/// Every line moved right by `prefix`.
fn indent(lines: Vec<Line<'static>>, prefix: &'static str) -> Vec<Line<'static>> {
    lines
        .into_iter()
        .map(|l| {
            if l.width() == 0 {
                return l;
            }
            let mut spans = vec![Span::raw(prefix)];
            spans.extend(l.spans);
            Line::from(spans)
        })
        .collect()
}

/// One line of at most `width` columns, with "…" when cut (or when more lines follow).
fn cut(text: &str, width: usize, more: bool) -> String {
    let mut out = String::new();
    let mut used = 0;
    for ch in text.chars() {
        let w = unicode_width::UnicodeWidthChar::width(ch).unwrap_or(0);
        if used + w > width.saturating_sub(1) {
            out.push('…');
            return out;
        }
        out.push(ch);
        used += w;
    }
    if more {
        out.push_str(" …");
    }
    out
}

fn draw_chat(f: &mut Frame, app: &mut App, area: Rect) {
    let inner = Rect {
        x: area.x + 1,
        width: area.width.saturating_sub(2),
        ..area
    };
    let lines = chat_lines(&app.state, inner.width as usize, app.show_details);
    let height = inner.height as usize;
    let max_scroll = lines.len().saturating_sub(height);
    app.scroll = app.scroll.min(max_scroll);
    let end = lines.len() - app.scroll;
    let start = end.saturating_sub(height);
    if lines.is_empty() {
        let hello = vec![
            Line::default(),
            Line::from(Span::styled("Write a message and press Enter.", DIM)),
            Line::from(Span::styled(
                "Ctrl+O opens a past chat, Ctrl+P another project, Ctrl+H lists the keys.",
                DIM,
            )),
        ];
        f.render_widget(Paragraph::new(hello), inner);
        return;
    }
    // the hints inside the chat are buttons: the thinking line, a waiting plan's keys
    for (row, line) in lines[start..end].iter().enumerate() {
        let y = inner.y + row as u16;
        let text: String = line.spans.iter().map(|s| s.content.as_ref()).collect();
        if text.contains("Ctrl+T shows it") {
            app.hits.push((
                Rect {
                    x: inner.x,
                    y,
                    width: line.width() as u16,
                    height: 1,
                },
                ctrl('t'),
            ));
        } else if line.spans.first().is_some_and(|s| s.style.fg == Some(ACCENT)) {
            link_labels(&text, inner.x, y, &mut app.hits);
        }
    }
    f.render_widget(Paragraph::new(lines[start..end].to_vec()), inner);
    if app.scroll > 0 {
        let note = format!(" ↓ {} more lines · End ", app.scroll);
        let w = note.width() as u16;
        let r = Rect {
            x: area.x + area.width.saturating_sub(w + 1),
            y: area.y + area.height - 1,
            width: w,
            height: 1,
        };
        f.render_widget(
            Paragraph::new(Span::styled(note, Style::new().bg(Color::DarkGray).fg(Color::White))),
            r,
        );
        app.hits.push((r, Click::End));
    }
}

fn draw_status(f: &mut Frame, app: &mut App, area: Rect) {
    let s = &app.state;
    let left: Vec<Span> = if let Some(note) = &app.line_note {
        vec![Span::styled(format!(" {note}"), Style::new().fg(Color::Yellow))]
    } else if let Some((flash, _)) = &app.flash {
        vec![Span::styled(format!(" {flash}"), Style::new().fg(ACCENT))]
    } else if s.ask.is_some() {
        vec![Span::styled(
            " Waiting for your answer",
            Style::new().fg(Color::Yellow).add_modifier(Modifier::BOLD),
        )]
    } else if s.busy {
        let stage = match s.stage.as_deref() {
            Some("conductor") => "Choosing the model",
            Some("files") => "Reading files",
            Some("model") => "Working",
            _ => "Working",
        };
        let mut v = vec![Span::styled(
            format!(" {} {stage}", SPIN[app.spinner % SPIN.len()]),
            Style::new().fg(ACCENT),
        )];
        if let Some(m) = &s.model {
            v.push(Span::styled(format!(" · {m}"), DIM));
        }
        v.push(Span::styled(" · Esc stops", DIM));
        v
    } else if s.read_only {
        vec![Span::styled(" Read only · Ctrl+N starts a new chat", DIM)]
    } else {
        vec![Span::styled(" Ready", DIM)]
    };
    let text: String = left.iter().map(|s| s.content.as_ref()).collect();
    link_labels(&text, area.x, area.y, &mut app.hits);
    f.render_widget(Paragraph::new(Line::from(left)), area);
    let cost = match s.cost {
        Some(c) => format!("${c:.4}"),
        None if s.tokens > 0 => "$ ?".to_string(),
        None => String::new(),
    };
    let mut right = String::new();
    if s.tokens > 0 {
        right = format!("{} tokens", short(s.tokens));
    }
    if !cost.is_empty() {
        right = if right.is_empty() {
            cost
        } else {
            format!("{right} · {cost}")
        };
    }
    let style = match s.budget.as_deref() {
        Some("over") => Style::new().fg(Color::Red),
        Some("warning") => Style::new().fg(Color::Yellow),
        _ => DIM,
    };
    let right = format!("{right} ");
    let w = right.width() as u16;
    if area.width > w + 20 {
        f.render_widget(
            Paragraph::new(Span::styled(right, style)),
            Rect {
                x: area.x + area.width - w,
                width: w,
                ..area
            },
        );
    }
}

fn input_height(app: &App, width: u16) -> u16 {
    let w = width.saturating_sub(4).max(10) as usize;
    let lines: usize = app.input.text().split('\n').map(|l| l.width().div_ceil(w).max(1)).sum();
    lines.clamp(1, 8) as u16
}

fn draw_input(f: &mut Frame, app: &App, area: Rect) {
    let (title, color) = if !app.connected {
        (" Not connected ", Color::Yellow)
    } else if app.state.ask.is_some() {
        (" Answer the question above ", Color::DarkGray)
    } else if app.state.busy {
        (" Working… Esc stops ", Color::DarkGray)
    } else {
        (" Message ", ACCENT)
    };
    let block = Block::new()
        .borders(Borders::ALL)
        .border_type(BorderType::Rounded)
        .border_style(Style::new().fg(color))
        .title(Span::styled(title, Style::new().fg(color)));
    let inner = block.inner(area);
    f.render_widget(block, area);
    let width = inner.width.max(1) as usize;
    let mut lines: Vec<Line> = Vec::new();
    for l in app.input.text().split('\n') {
        lines.extend(hard_wrap(l, width, "", Style::new()));
    }
    // the cursor: where the text before it ends, in wrapped lines
    let before = app.input.before_cursor();
    let mut row = 0usize;
    let mut col = 0usize;
    for (i, l) in before.split('\n').enumerate() {
        if i > 0 {
            row += 1;
        }
        let w = l.width();
        row += w / width;
        col = w % width;
    }
    let skip = row.saturating_sub(inner.height.saturating_sub(1) as usize);
    let shown: Vec<Line> = lines.into_iter().skip(skip).take(inner.height as usize).collect();
    f.render_widget(Paragraph::new(shown), inner);
    if app.modal.is_none() && app.state.ask.is_none() {
        f.set_cursor_position((inner.x + col as u16, inner.y + (row - skip) as u16));
    }
}

fn draw_keys(f: &mut Frame, app: &mut App, area: Rect) {
    let keys = if !app.connected {
        "Ctrl+L reconnect now · Ctrl+H keys · Ctrl+Q quit"
    } else if app.state.ask.is_some() {
        "Esc stops the request · ↑↓ scroll the card"
    } else if app.state.busy {
        "Esc stop · Ctrl+D leave it running · Ctrl+T details · Ctrl+H keys"
    } else {
        "Enter send · Alt+Enter new line · Ctrl+O chats · Ctrl+P projects · Ctrl+H keys"
    };
    link_labels(keys, area.x + 1, area.y, &mut app.hits);
    f.render_widget(Paragraph::new(Span::styled(format!(" {keys}"), DIM)), area);
}

/// The Dashboard in place of the chat; the chat's question, if one comes, is announced.
fn draw_dash(f: &mut Frame, app: &mut App, body: Rect, keys: Rect, area: Rect) {
    let Some(dash) = &mut app.dash else { return };
    let (menu, rows) = dash.draw(f, body);
    let mut text = dash.keys();
    app.hits.extend(menu.into_iter().map(|(r, i)| (r, Click::Section(i))));
    let row_x = body.x + 22;
    app.hits.extend(rows.into_iter().map(|(y, i)| {
        (
            Rect {
                x: row_x,
                y,
                width: body.width.saturating_sub(22),
                height: 1,
            },
            Click::DashRow(i),
        )
    }));
    // the section's tabs and its cyan key line, clickable too
    app.hits
        .extend(dash.spots.tabs.iter().map(|(r, row, i)| (*r, Click::DashTab(*row, *i))));
    for (x, y, line) in &dash.spots.keys {
        link_labels(line, *x, *y, &mut app.hits);
    }
    let waiting = app.state.ask.is_some();
    if waiting {
        text = format!("Ctrl+G a question waits in the chat · {text}");
    } else if !text.is_empty() {
        text = format!("{text} · Ctrl+G chat");
    }
    link_labels(&text, keys.x + 1, keys.y, &mut app.hits);
    let style = if waiting { Style::new().fg(Color::Yellow) } else { DIM };
    f.render_widget(Paragraph::new(Span::styled(format!(" {text}"), style)), keys);
    if matches!(app.modal, Some(Modal::ConfirmQuit)) {
        draw_confirm_quit(f, app, area);
    }
}

fn ctrl(c: char) -> Click {
    Click::Key(KeyCode::Char(c), KeyModifiers::CONTROL)
}

/// The key a hint starts with ("Ctrl+O chats", "Esc stop", "Enter send"), as a click.
pub fn key_of(label: &str) -> Option<Click> {
    let key = label.split_whitespace().next()?;
    match key {
        "Enter" => Some(Click::Key(KeyCode::Enter, KeyModifiers::NONE)),
        "Esc" => Some(Click::Key(KeyCode::Esc, KeyModifiers::NONE)),
        "End" | "Ctrl+End" => Some(Click::End),
        "Tab" => Some(Click::Key(KeyCode::Tab, KeyModifiers::NONE)),
        // the Dashboard's one-letter keys ("t test", "/ find")
        k if k.chars().count() == 1 && (k.chars().all(|c| c.is_ascii_lowercase()) || k == "/") => {
            Some(Click::Key(KeyCode::Char(k.chars().next()?), KeyModifiers::NONE))
        }
        _ => {
            let c = key.strip_prefix("Ctrl+")?;
            let mut chars = c.chars();
            match (chars.next(), chars.next()) {
                (Some(c), None) if c.is_ascii_alphabetic() => Some(ctrl(c.to_ascii_lowercase())),
                _ => None,
            }
        }
    }
}

/// Makes each "Key what" of a "a · b · c" line a button, where it is drawn.
pub fn link_labels(text: &str, x: u16, y: u16, hits: &mut Vec<(Rect, Click)>) {
    let mut col = 0usize;
    for (i, seg) in text.split(" · ").enumerate() {
        if i > 0 {
            col += 3;
        }
        let lead = seg.width() - seg.trim_start().width();
        let label = seg.trim();
        if let Some(click) = key_of(label) {
            let r = Rect {
                x: x + (col + lead) as u16,
                y,
                width: label.width() as u16,
                height: 1,
            };
            hits.push((r, click));
        }
        col += seg.width();
    }
}

/// The whole card (border included) from its inside: clicks there don't close it.
fn outer(inner: Rect) -> Rect {
    Rect {
        x: inner.x.saturating_sub(2),
        y: inner.y.saturating_sub(1),
        width: inner.width + 4,
        height: inner.height + 2,
    }
}

// ------------------------------------------------------------------ cards

fn card(f: &mut Frame, area: Rect, title: &str, color: Color, want_w: u16, want_h: u16) -> Rect {
    let w = want_w.min(area.width.saturating_sub(2)).max(20);
    let h = want_h.min(area.height).max(5);
    let r = Rect {
        x: area.x + (area.width.saturating_sub(w)) / 2,
        y: area.y + (area.height.saturating_sub(h)) / 2,
        width: w,
        height: h,
    };
    f.render_widget(Clear, r);
    let block = Block::new()
        .borders(Borders::ALL)
        .border_type(BorderType::Rounded)
        .border_style(Style::new().fg(color))
        .title(Span::styled(
            format!(" {title} "),
            Style::new().fg(color).add_modifier(Modifier::BOLD),
        ));
    let inner = block.inner(r);
    f.render_widget(block, r);
    Rect {
        x: inner.x + 1,
        width: inner.width.saturating_sub(2),
        ..inner
    }
}

/// The question's card: what is asked, in full, and the keys that answer it.
pub fn ask_lines(ask: &Ask, width: usize) -> (String, Vec<Line<'static>>, Vec<(&'static str, &'static str)>) {
    let mut lines = Vec::new();
    match ask {
        Ask::Limit {
            command,
            elapsed,
            limit,
        } => {
            lines.extend(hard_wrap(command, width.saturating_sub(2), "$ ", CODE));
            lines.push(Line::default());
            lines.push(Line::raw(format!(
                "It has run for {:.0} s; its limit is {:.0} s. It keeps running while you decide.",
                elapsed, limit
            )));
            (
                "The command takes long".into(),
                lines,
                vec![("m", "wait more"), ("u", "until it ends"), ("s", "stop it")],
            )
        }
        Ask::Approval(req) => {
            let s = |k: &str| req.get(k).and_then(Value::as_str).unwrap_or("").to_string();
            let kind = kind_of(req);
            let command = s("command");
            match kind {
                Kind::Command { .. } => {
                    for (n, l) in command.lines().enumerate() {
                        lines.extend(hard_wrap(
                            l,
                            width.saturating_sub(2),
                            if n == 0 { "$ " } else { "  " },
                            CODE,
                        ));
                    }
                    lines.push(Line::default());
                    if !s("workdir").is_empty() {
                        lines.push(Line::from(vec![Span::styled("in ", DIM), Span::raw(s("workdir"))]));
                    }
                    if req.get("sandboxed").and_then(Value::as_bool) == Some(true) {
                        lines.push(Line::from(Span::styled(
                            "in the sandbox: it can change only this project",
                            Style::new().fg(Color::Green),
                        )));
                    } else {
                        lines.push(Line::from(Span::styled(
                            "NOT in the sandbox: it can change anything you can",
                            Style::new().fg(Color::Red).add_modifier(Modifier::BOLD),
                        )));
                    }
                    if let Some(paths) = req
                        .get("outside_paths")
                        .and_then(Value::as_array)
                        .filter(|p| !p.is_empty())
                    {
                        let p: Vec<String> = paths.iter().filter_map(|v| v.as_str().map(str::to_string)).collect();
                        lines.extend(wrap(
                            vec![Span::styled(
                                format!("Reaches outside the project: {}", p.join(", ")),
                                Style::new().fg(Color::Yellow),
                            )],
                            width,
                            "",
                            "",
                        ));
                    }
                    if !s("ask_reason").is_empty() {
                        lines.extend(wrap(vec![Span::styled(s("ask_reason"), DIM)], width, "", ""));
                    }
                }
                Kind::FileChange => {
                    lines.extend(wrap(vec![Span::raw(command.clone())], width, "", ""));
                    lines.push(Line::default());
                    let diff = req.pointer("/file_change/diff").and_then(Value::as_str).unwrap_or("");
                    for l in diff.lines() {
                        let style = if l.starts_with('+') && !l.starts_with("+++") {
                            Style::new().fg(Color::Green)
                        } else if l.starts_with('-') && !l.starts_with("---") {
                            Style::new().fg(Color::Red)
                        } else if l.starts_with("@@") {
                            Style::new().fg(Color::Cyan)
                        } else {
                            Style::new().fg(Color::Gray)
                        };
                        lines.extend(hard_wrap(l, width, "", style));
                    }
                }
                _ => {
                    for l in command.lines() {
                        lines.extend(wrap(text::inline(l, Style::new()), width, "", ""));
                    }
                    if let Some(fb) = req
                        .get("fallback")
                        .and_then(|v| v.get("summary"))
                        .and_then(Value::as_str)
                        .filter(|fb| !command.contains(fb.trim()))
                    // often said in the question already
                    {
                        lines.push(Line::default());
                        lines.extend(wrap(vec![Span::styled(fb.to_string(), DIM)], width, "", ""));
                    }
                    if let Some(fb) = req.get("fallback").filter(|v| !v.is_null()) {
                        lines.extend(fallback_lines(fb, width));
                    }
                    if let Some(form) = req.get("form").and_then(Value::as_object) {
                        lines.push(Line::default());
                        for (k, v) in form {
                            let v = v.as_str().map(str::to_string).unwrap_or_else(|| v.to_string());
                            if !v.is_empty() && v != "null" && v != "false" {
                                lines.push(Line::from(vec![Span::styled(format!("{k}: "), DIM), Span::raw(v)]));
                            }
                        }
                    }
                }
            }
            let (title, keys): (&str, Vec<(&str, &str)>) = match kind {
                Kind::Command { raise: true } => (
                    "Run this command?",
                    vec![
                        ("y", "run"),
                        ("n", "no…"),
                        ("a", "run, and stop asking for such commands in this project"),
                    ],
                ),
                Kind::Command { raise: false } => ("Run this command?", vec![("y", "run"), ("n", "no…")]),
                Kind::FileChange => (
                    "Change this file?",
                    vec![("y", "yes"), ("a", "yes to every edit of this reply"), ("n", "no…")],
                ),
                Kind::Steps { escalate: true } => (
                    "Out of steps",
                    vec![
                        ("m", "more steps"),
                        ("e", "give it to the Complex model"),
                        ("s", "stop"),
                    ],
                ),
                Kind::Steps { escalate: false } => ("Out of steps", vec![("m", "more steps"), ("s", "stop")]),
                Kind::Sync => ("GitHub", vec![("n", "close (push and update in the web page)")]),
                Kind::Fallback => {
                    let stuck = req.pointer("/fallback/kind").and_then(Value::as_str) == Some("stuck");
                    (
                        if stuck {
                            "The Simple model is stuck"
                        } else {
                            "Use another model?"
                        },
                        vec![
                            ("y", "use the other model for this request"),
                            ("m", "Models"),
                            ("r", "Routing"),
                            ("n", "stop…"),
                        ],
                    )
                }
                Kind::YesNo => {
                    let title = match req.get("action").and_then(Value::as_str).unwrap_or("") {
                        "create_project" => "Create this project?",
                        "archive_project" => "Archive this project?",
                        _ => "Allow this?",
                    };
                    (title, vec![("y", "yes"), ("n", "no…")])
                }
            };
            (title.into(), lines, keys)
        }
    }
}

/// What a failed model means and what to do (the web page's card says the same).
fn fallback_lines(fb: &Value, width: usize) -> Vec<Line<'static>> {
    let get = |k: &str| fb.get(k).and_then(Value::as_str).unwrap_or("").to_string();
    let why = match get("kind").as_str() {
        "stuck" => "It didn't finish the request. Its work so far is kept either way.",
        "quota" => "Its credit or quota is used up, or the provider is limiting it. Check the provider account.",
        "transient" => "The provider is busy or unreachable right now; it may work again in a few minutes.",
        _ => "The model may be retired, misspelled, or its key may be wrong.",
    };
    let mut out = vec![Line::default()];
    out.extend(wrap(vec![Span::styled(why.to_string(), DIM)], width, "", ""));
    out.extend(wrap(
        vec![Span::styled(
            "m opens Models (fix its key, test it, or add another), r opens Routing (choose who does what): this question waits, and Ctrl+G brings you back to it.".to_string(),
            DIM,
        )],
        width,
        "",
        "",
    ));
    out
}

fn draw_ask(f: &mut Frame, app: &mut App, area: Rect) {
    let Some(ask) = &app.state.ask else { return };
    let width = area.width.saturating_sub(6).min(100) as usize;
    let (title, lines, keys) = ask_lines(ask, width);
    let ready = app.ask_since.is_none_or(|t| t.elapsed() >= super::app::ANSWER_GUARD);
    let want_h = lines.len() as u16 + 4;
    let inner = card(f, area, &title, Color::Yellow, width as u16 + 4, want_h);
    let body_h = inner.height.saturating_sub(2) as usize;
    let max = lines.len().saturating_sub(body_h);
    app.ask_scroll = app.ask_scroll.min(max);
    let shown: Vec<Line> = lines.into_iter().skip(app.ask_scroll).take(body_h).collect();
    f.render_widget(
        Paragraph::new(shown),
        Rect {
            height: body_h as u16,
            ..inner
        },
    );
    let foot = Rect {
        y: inner.y + inner.height.saturating_sub(1),
        height: 1,
        ..inner
    };
    app.hits.push((outer(inner), Click::Nothing));
    let mut x = foot.x;
    let mut spans = Vec::new();
    for (k, what) in keys {
        // " y " and its words are one button
        let w = (k.width() + 2 + 1 + what.width()) as u16;
        let key = match k.chars().next() {
            Some(c) => Click::Key(KeyCode::Char(c), KeyModifiers::NONE),
            None => Click::Nothing,
        };
        app.hits.push((
            Rect {
                x,
                y: foot.y,
                width: w,
                height: 1,
            },
            key,
        ));
        x += w + 3;
        let key_style = if ready {
            Style::new().fg(Color::Black).bg(Color::Yellow)
        } else {
            Style::new().fg(Color::DarkGray).bg(Color::Gray)
        };
        spans.push(Span::styled(format!(" {k} "), key_style));
        spans.push(Span::raw(format!(" {what}   ")));
    }
    if max > 0 {
        spans.push(Span::styled(format!("↑↓ {}/{}", app.ask_scroll, max), DIM));
    }
    f.render_widget(Paragraph::new(Line::from(spans)), foot);
}

fn draw_reason(f: &mut Frame, text: &str, before: &str, area: Rect) {
    let inner = card(f, area, "Why not? (optional)", Color::Yellow, 70, 7);
    let lines = vec![
        Line::raw(text.to_string()),
        Line::default(),
        Line::from(Span::styled(
            "Enter sends the no (with this reason for the model) · Esc back",
            DIM,
        )),
    ];
    f.render_widget(Paragraph::new(lines).wrap(Wrap { trim: false }), inner);
    f.set_cursor_position((inner.x + before.width().min(inner.width as usize - 1) as u16, inner.y));
}

fn draw_picker(f: &mut Frame, app: &mut App, area: Rect) {
    let mut hits = Vec::new();
    let Some(Modal::Picker(p)) = &app.modal else { return };
    let title = match p.kind {
        PickKind::Chat => "Chats of this project",
        PickKind::Project => "Projects",
    };
    let inner = card(f, area, title, ACCENT, 90, area.height.saturating_sub(4));
    hits.push((outer(inner), Click::Nothing));
    let mut lines = vec![Line::from(vec![
        Span::styled("Find: ", DIM),
        Span::raw(p.filter.clone()),
        Span::styled("▏", Style::new().fg(ACCENT)),
    ])];
    lines.push(Line::default());
    let list_h = inner.height.saturating_sub(3) as usize;
    if let Some(e) = &p.error {
        lines.push(Line::from(Span::styled(e.clone(), Style::new().fg(Color::Red))));
    } else if p.items.is_none() {
        lines.push(Line::from(Span::styled("Loading…", DIM)));
    } else {
        let items = p.visible();
        if items.is_empty() {
            lines.push(Line::from(Span::styled("Nothing found.", DIM)));
        }
        let first = p.selected.saturating_sub(list_h.saturating_sub(1));
        for (i, (_, label, detail)) in items.iter().enumerate().skip(first).take(list_h) {
            let sel = i == p.selected;
            let style = if sel {
                Style::new().bg(Color::DarkGray).add_modifier(Modifier::BOLD)
            } else {
                Style::new()
            };
            let width = inner.width as usize;
            let mut label = label.clone();
            let room = width.saturating_sub(detail.width() + 4);
            if label.width() > room {
                label = label.chars().take(room.saturating_sub(1)).collect::<String>() + "…";
            }
            let pad = width.saturating_sub(label.width() + detail.width() + 3);
            let row = Rect {
                x: inner.x,
                y: inner.y + lines.len() as u16,
                width: inner.width,
                height: 1,
            };
            hits.push((row, Click::Pick(i)));
            lines.push(Line::from(vec![
                Span::styled(if sel { " ▸ " } else { "   " }, style),
                Span::styled(label, style),
                Span::styled(" ".repeat(pad), style),
                Span::styled(detail.to_string(), if sel { style } else { DIM }),
            ]));
        }
    }
    f.render_widget(Paragraph::new(lines), inner);
    let foot = Rect {
        y: inner.y + inner.height.saturating_sub(1),
        height: 1,
        ..inner
    };
    let keys = "↑↓ choose · Enter open · type to find · Esc close";
    link_labels(keys, foot.x, foot.y, &mut hits);
    f.render_widget(Paragraph::new(Span::styled(keys, DIM)), foot);
    app.hits.extend(hits);
}

fn draw_confirm_quit(f: &mut Frame, app: &mut App, area: Rect) {
    let inner = card(f, area, "A request is running", Color::Yellow, 76, 10);
    let away = if app.state.away >= 120 {
        format!("{} minutes", app.state.away / 60)
    } else {
        format!("{} seconds", app.state.away)
    };
    let minutes = (app.state.detach_wait / 60).max(1);
    let lines = vec![
        Line::raw(format!(
            "Leave it running, and it goes on for up to {minutes} minutes: open its chat again \
             (here or in the web page) to get it back. If you just quit, it goes on for {away} only."
        )),
        Line::from(Span::styled(
            "Either way, a question waits for you: nothing runs without your answer.",
            DIM,
        )),
    ];
    let body = Rect {
        height: inner.height.saturating_sub(1),
        ..inner
    };
    f.render_widget(Paragraph::new(lines).wrap(Wrap { trim: false }), body);
    let foot = Rect {
        y: inner.y + inner.height.saturating_sub(1),
        height: 1,
        ..inner
    };
    let leave = format!(" leave it running ({minutes} min)");
    let buttons = [
        ("d", leave.as_str(), 'd'),
        ("y", " quit", 'y'),
        ("s", " stop it", 's'),
        ("other key", " stay", 'x'),
    ];
    let mut spans = Vec::new();
    let mut x = foot.x;
    app.hits.push((outer(inner), Click::Nothing));
    for (key, what, c) in buttons {
        let w = (key.width() + 2 + what.width()) as u16;
        app.hits.push((
            Rect {
                x,
                y: foot.y,
                width: w,
                height: 1,
            },
            Click::Key(KeyCode::Char(c), KeyModifiers::NONE),
        ));
        x += w + 3;
        let bg = if c == 'x' { Color::Gray } else { Color::Yellow };
        spans.push(Span::styled(format!(" {key} "), Style::new().fg(Color::Black).bg(bg)));
        spans.push(Span::raw(format!("{what}   ")));
    }
    f.render_widget(Paragraph::new(Line::from(spans)), foot);
}

pub const HELP: &[(&str, &str)] = &[
    ("Enter", "send the message"),
    ("Alt+Enter, Ctrl+J", "new line in the message"),
    ("Esc", "stop the running request"),
    ("Ctrl+N", "new chat (a running request is left running)"),
    ("Alt+↑, Alt+↓", "the chat above, below in the list"),
    ("Ctrl+b b", "show or hide the chat list"),
    ("Ctrl+b ?", "all the keys, with a search (panes, tabs, chat, Dashboard)"),
    (
        "Ctrl+b …",
        "panes and tabs: v split right, - down, h j k l move, c new tab…",
    ),
    ("Ctrl+G", "the Dashboard (models and settings); again: back to the chat"),
    ("Ctrl+D", "quit, leaving the running request running"),
    ("Ctrl+O", "open a chat of this project"),
    ("Ctrl+P", "switch project"),
    ("Ctrl+E", "give the last request to the Complex model"),
    ("Ctrl+T", "details: the thinking and the notes in full"),
    ("Ctrl+R / Ctrl+K", "run / cancel a waiting plan; Ctrl+X skips a step"),
    ("PgUp, PgDn, ↑↓", "scroll the chat"),
    ("Ctrl+End", "back to the end of the chat"),
    ("Ctrl+W, Ctrl+U", "delete a word, the whole message"),
    ("Ctrl+L", "reconnect now"),
    ("y / n / a", "answer a question card (keys work after a short moment)"),
    (
        "Mouse",
        "click the keys below, titles, cards and lists; the wheel scrolls",
    ),
    ("Shift+drag", "select text to copy (or start with --no-mouse)"),
    ("Ctrl+H, F1", "this list"),
    ("Ctrl+Q, Ctrl+C", "quit"),
];

fn draw_help(f: &mut Frame, area: Rect) {
    let inner = card(f, area, "Keys", ACCENT, 84, HELP.len() as u16 + 4);
    let mut lines: Vec<Line> = HELP
        .iter()
        .map(|(k, v)| {
            Line::from(vec![
                Span::styled(format!("{k:<18}"), Style::new().fg(ACCENT)),
                Span::raw(*v),
            ])
        })
        .collect();
    lines.push(Line::default());
    lines.push(Line::from(Span::styled("Any key closes this.", DIM)));
    f.render_widget(Paragraph::new(lines), inner);
}

fn short(n: u64) -> String {
    match n {
        0..=999 => n.to_string(),
        1_000..=999_999 => format!("{:.1}k", n as f64 / 1000.0),
        _ => format!("{:.2}M", n as f64 / 1_000_000.0),
    }
}

#[cfg(test)]
mod card_tests {
    use super::*;
    use serde_json::json;

    fn card(req: Value) -> (String, Vec<String>) {
        let (title, lines, _) = ask_lines(&Ask::Approval(req), 80);
        let text = lines
            .iter()
            .map(|l| l.spans.iter().map(|s| s.content.as_ref()).collect::<String>())
            .collect();
        (title, text)
    }

    #[test]
    fn a_costlier_model_card_says_what_it_is_and_says_it_once() {
        let (title, lines) = card(json!({"action": "fallback", "tool_call_id": null,
            "command": "gemini isn't answering: quota exhausted, resets in ~29m. Use claude instead?",
            "fallback": {"model": "gemini", "alt": "claude", "summary": "quota exhausted, resets in ~29m"}}));
        assert_eq!(title, "Use another model?");
        assert!(lines.iter().any(|l| l.contains("m opens Models")), "{lines:?}");
        assert_eq!(
            lines.iter().filter(|l| l.contains("resets in ~29m")).count(),
            1,
            "{lines:?}"
        );
    }

    #[test]
    fn project_cards_are_named() {
        assert_eq!(
            card(json!({"action": "create_project", "command": "x"})).0,
            "Create this project?"
        );
        assert_eq!(
            card(json!({"action": "archive_project", "command": "x"})).0,
            "Archive this project?"
        );
    }
}

#[cfg(test)]
mod look_tests {
    use super::*;
    use serde_json::json;

    fn chat(msgs: &[Value], width: usize, details: bool) -> Vec<Line<'static>> {
        let mut s = State::default();
        for m in msgs {
            s.apply(m);
        }
        chat_lines(&s, width, details)
    }

    fn plain(l: &Line) -> String {
        l.spans.iter().map(|s| s.content.as_ref()).collect()
    }

    #[test]
    fn your_message_is_a_shaded_band() {
        let lines = chat(&[json!({"type": "prompt", "content": "hello"})], 40, false);
        let band = &lines[0];
        assert!(plain(band).starts_with("▌ hello"));
        assert_eq!(band.width(), 40, "the full width");
        assert!(band.spans.iter().all(|s| s.style.bg == Some(USER_BG)));
    }

    #[test]
    fn the_answer_has_a_label_once_per_turn_and_is_indented() {
        let lines = chat(
            &[
                json!({"type": "prompt", "content": "q"}),
                json!({"type": "token", "content": "first"}),
                json!({"type": "command_start", "command": "ls"}),
                json!({"type": "command_output", "command": "ls", "output": "a", "exit_code": 0}),
                json!({"type": "token", "content": "second"}),
            ],
            60,
            false,
        );
        let text: Vec<String> = lines.iter().map(plain).collect();
        assert_eq!(text.iter().filter(|l| l.as_str() == "◆ Optimux").count(), 1, "{text:?}");
        assert!(text.contains(&"  first".to_string()) && text.contains(&"  second".to_string()));
        assert!(text.contains(&"  ┌ $ ls".to_string()));
        assert!(text.contains(&"  │ a".to_string()));
        assert!(text.contains(&"  └ exit 0".to_string()));
    }

    #[test]
    fn routine_notes_take_one_line_unless_details() {
        let long =
            "The Conductor couldn't decide: quota exhausted, resets in ~25m. This request starts on the simple tier.";
        let msgs = [json!({"type": "system", "content": long})];
        let short = chat(&msgs, 50, false);
        assert_eq!(short.len(), 1);
        assert!(plain(&short[0]).ends_with('…') && short[0].width() <= 50);
        let full = chat(&msgs, 50, true);
        assert!(full.len() > 1, "in full with details");
    }

    #[test]
    fn warnings_and_errors_are_never_cut() {
        let long = "Stopped: the model isn't answering (quota exhausted). Check it in Dashboard → Models.";
        let lines = chat(&[json!({"type": "error", "content": long})], 40, false);
        assert!(lines.len() > 1);
        assert!(lines.iter().all(|l| plain(l).starts_with('▌')));
        let all: String = lines.iter().map(plain).collect::<Vec<_>>().join(" ");
        assert!(all.contains("Dashboard"), "{all}");
    }

    #[test]
    fn a_wide_screen_wraps_at_the_reading_width() {
        let lines = chat(&[json!({"type": "token", "content": "word ".repeat(100)})], 300, false);
        assert!(lines.iter().all(|l| l.width() <= READ_WIDTH));
    }
}

#[cfg(test)]
mod click_tests {
    use super::*;

    #[test]
    fn a_hint_line_becomes_buttons_where_it_is_drawn() {
        let mut hits = Vec::new();
        link_labels(
            "Enter send · Alt+Enter new line · Ctrl+O chats · Esc stop",
            1,
            9,
            &mut hits,
        );
        let found: Vec<(u16, u16, Click)> = hits.iter().map(|(r, c)| (r.x, r.width, *c)).collect();
        assert_eq!(
            found,
            vec![
                (1, 10, Click::Key(KeyCode::Enter, KeyModifiers::NONE)),
                (35, 12, Click::Key(KeyCode::Char('o'), KeyModifiers::CONTROL)),
                (50, 8, Click::Key(KeyCode::Esc, KeyModifiers::NONE)),
            ]
        );
        assert!(hits.iter().all(|(r, _)| r.y == 9 && r.height == 1));
    }

    #[test]
    fn keys_from_labels() {
        assert_eq!(key_of("Ctrl+H keys"), Some(ctrl('h')));
        assert_eq!(key_of("Ctrl+End"), Some(Click::End));
        assert_eq!(key_of("Alt+Enter new line"), None);
        assert_eq!(key_of("PgUp/PgDn scroll"), None);
        assert_eq!(key_of("↑↓ choose"), None);
        assert_eq!(key_of("thinking (45 words)"), None);
    }
}
