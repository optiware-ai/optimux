//! Text for the screen: a small Markdown renderer (what models write: headings, lists,
//! code blocks, `code`, **bold**, quotes) and word wrapping that knows wide characters.
//! Everything becomes plain lines of styled spans at a given width, so scrolling is
//! a matter of counting lines.

use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use unicode_width::{UnicodeWidthChar, UnicodeWidthStr};

pub const CODE: Style = Style::new().fg(Color::Cyan);
/// The shade behind code blocks: they read as a sample, not as something that ran
/// (commands that ran have a frame instead, see view.rs).
pub const CODE_BG: Color = Color::Indexed(235);
pub const INLINE_CODE: Style = Style::new().fg(Color::Yellow);
pub const DIM: Style = Style::new().fg(Color::DarkGray);

/// Markdown into wrapped lines of `width` columns.
pub fn markdown(src: &str, width: usize) -> Vec<Line<'static>> {
    let width = width.max(10);
    let mut out = Vec::new();
    let mut fence: Option<String> = None; // inside a ``` block: its marker
    let mut table: Vec<Vec<String>> = Vec::new();
    for raw in src.lines() {
        let trimmed = raw.trim_start();
        if let Some(marker) = &fence {
            if trimmed.starts_with(marker.as_str()) {
                fence = None;
                out.push(shaded(Vec::new(), width));
                continue;
            }
            for l in hard_wrap(raw, width.saturating_sub(2), " ", CODE) {
                out.push(shaded(l.spans, width));
            }
            continue;
        }
        // a table: its rows are gathered, then drawn when it ends
        if trimmed.starts_with('|') {
            if !is_rule_row(trimmed) {
                table.push(cells(trimmed));
            }
            continue;
        }
        if !table.is_empty() {
            out.extend(draw_table(&std::mem::take(&mut table), width));
        }
        if trimmed.starts_with("```") || trimmed.starts_with("~~~") {
            let marker: String = trimmed.chars().take_while(|c| *c == '`' || *c == '~').collect();
            let lang = trimmed[marker.len()..].trim();
            out.push(shaded(
                vec![Span::styled(format!(" {lang}"), DIM.add_modifier(Modifier::ITALIC))],
                width,
            ));
            fence = Some(marker);
            continue;
        }
        if trimmed.is_empty() {
            out.push(Line::default());
            continue;
        }
        if let Some((level, title)) = heading(trimmed) {
            let style = if level <= 2 {
                Style::new().add_modifier(Modifier::BOLD).fg(Color::LightBlue)
            } else {
                Style::new().add_modifier(Modifier::BOLD)
            };
            out.extend(wrap(vec![Span::styled(title.to_string(), style)], width, "", ""));
            continue;
        }
        if let Some(rest) = trimmed.strip_prefix("> ").or_else(|| (trimmed == ">").then_some("")) {
            let spans = inline(rest, Style::new().fg(Color::Gray).add_modifier(Modifier::ITALIC));
            out.extend(wrap(spans, width, "▏ ", "▏ "));
            continue;
        }
        if trimmed == "---" || trimmed == "***" || trimmed == "___" {
            out.push(Line::from(Span::styled("─".repeat(width.min(40)), DIM)));
            continue;
        }
        let indent = " ".repeat((raw.len() - trimmed.len()).min(8));
        if let Some((bullet, rest)) = list_item(trimmed) {
            let first = format!("{indent}{bullet} ");
            let next = " ".repeat(UnicodeWidthStr::width(first.as_str()));
            out.extend(wrap(inline(rest, Style::new()), width, &first, &next));
            continue;
        }
        out.extend(wrap(inline(trimmed, Style::new()), width, &indent, &indent));
    }
    if !table.is_empty() {
        out.extend(draw_table(&table, width));
    }
    if fence.is_some() {
        // an answer still streaming inside a code block: nothing to close yet
    }
    out
}

/// |---|:---:| : the line under a table's header.
fn is_rule_row(line: &str) -> bool {
    line.contains('-') && line.chars().all(|c| matches!(c, '|' | '-' | ':' | ' ' | '\t'))
}

/// A table row's cells; a | inside `code` or written \| stays in the cell.
fn cells(line: &str) -> Vec<String> {
    let inner = line.trim().trim_start_matches('|');
    let inner = inner.strip_suffix('|').unwrap_or(inner);
    let (mut out, mut cur, mut code) = (Vec::new(), String::new(), false);
    let mut chars = inner.chars().peekable();
    while let Some(c) = chars.next() {
        match c {
            '\\' if chars.peek() == Some(&'|') => cur.push(chars.next().unwrap_or('|')),
            '`' => {
                code = !code;
                cur.push(c);
            }
            '|' if !code => out.push(std::mem::take(&mut cur).trim().to_string()),
            _ => cur.push(c),
        }
    }
    out.push(cur.trim().to_string());
    out
}

/// A table at `width`: in columns when it fits, else each row as a short block (its
/// first cell, then the others under it, named by their header when there are more
/// than two columns).
fn draw_table(rows: &[Vec<String>], width: usize) -> Vec<Line<'static>> {
    let n = rows.iter().map(Vec::len).max().unwrap_or(0);
    let cell = |r: &Vec<String>, i: usize| r.get(i).cloned().unwrap_or_default();
    let w_of = |t: &str| inline(t, Style::new()).iter().map(|s| s.width()).sum::<usize>();
    let widths: Vec<usize> = (0..n)
        .map(|i| rows.iter().map(|r| w_of(&cell(r, i))).max().unwrap_or(0))
        .collect();
    let bold = Style::new().add_modifier(Modifier::BOLD);
    let mut out = Vec::new();
    if widths.iter().sum::<usize>() + 2 * n.saturating_sub(1) <= width {
        for (k, r) in rows.iter().enumerate() {
            let mut spans = Vec::new();
            for (i, w) in widths.iter().enumerate() {
                let t = cell(r, i);
                spans.extend(inline(&t, if k == 0 { bold } else { Style::new() }));
                if i + 1 < n {
                    spans.push(Span::raw(" ".repeat(w - w_of(&t) + 2)));
                }
            }
            out.push(Line::from(spans));
            if k == 0 && rows.len() > 1 {
                out.push(Line::from(Span::styled(
                    "─".repeat(widths.iter().sum::<usize>() + 2 * n.saturating_sub(1)),
                    DIM,
                )));
            }
        }
        return out;
    }
    let head = &rows[0];
    for r in rows.iter().skip(if rows.len() > 1 { 1 } else { 0 }) {
        out.extend(wrap(inline(&cell(r, 0), bold), width, "• ", "  "));
        for i in 1..n {
            let t = cell(r, i);
            if t.is_empty() {
                continue;
            }
            let mut spans = Vec::new();
            if n > 2 && !cell(head, i).is_empty() {
                spans.push(Span::styled(format!("{}: ", cell(head, i)), DIM));
            }
            spans.extend(inline(&t, Style::new()));
            out.extend(wrap(spans, width, "  ", "  "));
        }
    }
    out
}

/// A line of a code block: the shade across the whole width.
fn shaded(spans: Vec<Span<'static>>, width: usize) -> Line<'static> {
    let used: usize = spans.iter().map(|s| s.width()).sum();
    let mut spans: Vec<Span<'static>> = spans
        .into_iter()
        .map(|s| Span::styled(s.content, s.style.bg(CODE_BG)))
        .collect();
    spans.push(Span::styled(
        " ".repeat(width.saturating_sub(used)),
        Style::new().bg(CODE_BG),
    ));
    Line::from(spans)
}

fn heading(line: &str) -> Option<(usize, &str)> {
    let level = line.chars().take_while(|c| *c == '#').count();
    (1..=6)
        .contains(&level)
        .then(|| line[level..].strip_prefix(' '))
        .flatten()
        .map(|t| (level, t.trim()))
}

fn list_item(line: &str) -> Option<(String, &str)> {
    for b in ["- ", "* ", "+ "] {
        if let Some(rest) = line.strip_prefix(b) {
            let rest_trim = rest.trim_start();
            if let Some(task) = rest_trim.strip_prefix("[ ] ") {
                return Some(("☐".into(), task));
            }
            if let Some(task) = rest_trim
                .strip_prefix("[x] ")
                .or_else(|| rest_trim.strip_prefix("[X] "))
            {
                return Some(("☑".into(), task));
            }
            return Some(("•".into(), rest));
        }
    }
    let digits: String = line.chars().take_while(char::is_ascii_digit).collect();
    if !digits.is_empty() && digits.len() < 4 {
        let after = &line[digits.len()..];
        if let Some(rest) = after.strip_prefix(". ").or_else(|| after.strip_prefix(") ")) {
            return Some((format!("{digits}."), rest));
        }
    }
    None
}

/// `code`, **bold**, *italic*, on top of `base`.
pub fn inline(text: &str, base: Style) -> Vec<Span<'static>> {
    let mut spans = Vec::new();
    let mut buf = String::new();
    let (mut bold, mut italic) = (false, false);
    let style = |bold: bool, italic: bool| {
        let mut s = base;
        if bold {
            s = s.add_modifier(Modifier::BOLD);
        }
        if italic {
            s = s.add_modifier(Modifier::ITALIC);
        }
        s
    };
    let chars: Vec<char> = text.chars().collect();
    let mut i = 0;
    while i < chars.len() {
        let c = chars[i];
        if c == '`' {
            if let Some(end) = chars[i + 1..].iter().position(|&x| x == '`') {
                flush(&mut spans, &mut buf, style(bold, italic));
                spans.push(Span::styled(
                    chars[i + 1..i + 1 + end].iter().collect::<String>(),
                    INLINE_CODE,
                ));
                i += end + 2;
                continue;
            }
        }
        if c == '*' && chars.get(i + 1) == Some(&'*') {
            flush(&mut spans, &mut buf, style(bold, italic));
            bold = !bold;
            i += 2;
            continue;
        }
        // *italic*: a single star that opens before a word or closes after one
        if c == '*' {
            let next_word = chars.get(i + 1).is_some_and(|n| !n.is_whitespace());
            let prev_word = i > 0 && !chars[i - 1].is_whitespace();
            if (!italic && next_word) || (italic && prev_word) {
                flush(&mut spans, &mut buf, style(bold, italic));
                italic = !italic;
                i += 1;
                continue;
            }
        }
        buf.push(c);
        i += 1;
    }
    flush(&mut spans, &mut buf, style(bold, italic));
    spans
}

fn flush(spans: &mut Vec<Span<'static>>, buf: &mut String, style: Style) {
    if !buf.is_empty() {
        spans.push(Span::styled(std::mem::take(buf), style));
    }
}

/// Word wrap of styled spans. `first` starts the first line, `next` the others.
pub fn wrap(spans: Vec<Span<'static>>, width: usize, first: &str, next: &str) -> Vec<Line<'static>> {
    let mut lines = Vec::new();
    let mut line: Vec<Span<'static>> = vec![Span::raw(first.to_string())];
    let mut used = UnicodeWidthStr::width(first);
    let start_of = |n: &str| UnicodeWidthStr::width(n);
    for span in spans {
        let style = span.style;
        // words keep the spaces before them, so the text comes back as it was
        for word in split_keep_space(&span.content) {
            let w = UnicodeWidthStr::width(word.as_str());
            if used + w > width && used > start_of(if lines.is_empty() { first } else { next }) {
                lines.push(Line::from(std::mem::take(&mut line)));
                line.push(Span::raw(next.to_string()));
                used = start_of(next);
                let word = word.trim_start().to_string();
                let w = UnicodeWidthStr::width(word.as_str());
                if w > width.saturating_sub(used) {
                    // one word longer than the line: cut it
                    for (i, piece) in chop(&word, width.saturating_sub(used).max(1)).into_iter().enumerate() {
                        if i > 0 {
                            lines.push(Line::from(std::mem::take(&mut line)));
                            line.push(Span::raw(next.to_string()));
                            used = start_of(next);
                        }
                        used += UnicodeWidthStr::width(piece.as_str());
                        line.push(Span::styled(piece, style));
                    }
                } else {
                    used += w;
                    line.push(Span::styled(word, style));
                }
                continue;
            }
            if w > width.saturating_sub(used) {
                for (i, piece) in chop(&word, width.saturating_sub(used).max(1)).into_iter().enumerate() {
                    if i > 0 {
                        lines.push(Line::from(std::mem::take(&mut line)));
                        line.push(Span::raw(next.to_string()));
                        used = start_of(next);
                    }
                    used += UnicodeWidthStr::width(piece.as_str());
                    line.push(Span::styled(piece, style));
                }
                continue;
            }
            used += w;
            line.push(Span::styled(word, style));
        }
    }
    lines.push(Line::from(line));
    lines
}

/// Cuts at the width, not at spaces: code and command output keep their layout.
pub fn hard_wrap(text: &str, width: usize, prefix: &str, style: Style) -> Vec<Line<'static>> {
    let text = text.replace('\t', "    ");
    chop(&text, width.max(1))
        .into_iter()
        .map(|piece| Line::from(vec![Span::styled(prefix.to_string(), DIM), Span::styled(piece, style)]))
        .collect()
}

fn split_keep_space(s: &str) -> Vec<String> {
    let mut words = Vec::new();
    let mut cur = String::new();
    let mut in_word = false;
    for c in s.chars() {
        if c.is_whitespace() && in_word {
            words.push(std::mem::take(&mut cur));
            in_word = false;
        }
        if !c.is_whitespace() {
            in_word = true;
        }
        cur.push(if c == '\t' { ' ' } else { c });
    }
    if !cur.is_empty() {
        words.push(cur);
    }
    words
}

/// Pieces of at most `width` columns (never splitting a wide character). Always one
/// piece at least, so an empty line stays a line.
fn chop(s: &str, width: usize) -> Vec<String> {
    let mut out = vec![String::new()];
    let mut used = 0;
    for c in s.chars() {
        let w = c.width().unwrap_or(0);
        if used + w > width && used > 0 {
            out.push(String::new());
            used = 0;
        }
        out.last_mut().unwrap().push(c);
        used += w;
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn plain(lines: &[Line]) -> Vec<String> {
        lines
            .iter()
            .map(|l| l.spans.iter().map(|s| s.content.as_ref()).collect::<String>())
            .collect()
    }

    fn widest(lines: &[Line]) -> usize {
        lines.iter().map(|l| l.width()).max().unwrap_or(0)
    }

    #[test]
    fn tables_line_up_or_become_blocks() {
        let md = "| Key | What |\n|---|---|\n| `a|b` | one |\n| c | two |";
        let wide = plain(&markdown(md, 40));
        assert_eq!(wide, vec!["Key  What", "─────────", "a|b  one", "c    two"]);
        // too wide for 12 columns (markdown() never draws narrower than 10)
        let long = "| Key | What |\n|---|---|\n| a | one two three four |";
        let narrow = plain(&markdown(long, 12));
        assert_eq!(narrow, vec!["• a", "  one two", "  three four"]);
        // inside a code block a | line stays code
        assert!(
            plain(&markdown("```\n| x |\n```", 20))
                .iter()
                .any(|l| l.contains("| x |"))
        );
    }

    #[test]
    fn words_wrap_and_nothing_is_lost() {
        let text = "the quick brown fox jumps over the lazy dog again and again";
        let lines = wrap(vec![Span::raw(text)], 20, "", "");
        assert!(widest(&lines) <= 20, "{:?}", plain(&lines));
        let joined = plain(&lines).join(" ");
        assert_eq!(
            joined.split_whitespace().collect::<Vec<_>>(),
            text.split_whitespace().collect::<Vec<_>>()
        );
    }

    #[test]
    fn a_long_word_is_cut() {
        let lines = wrap(vec![Span::raw("x".repeat(25))], 10, "", "");
        assert_eq!(plain(&lines), vec!["x".repeat(10), "x".repeat(10), "x".repeat(5)]);
    }

    #[test]
    fn wide_characters_count_double() {
        let lines = wrap(vec![Span::raw("日本語のテキスト 日本語のテキスト")], 10, "", "");
        assert!(widest(&lines) <= 10, "{:?}", plain(&lines));
    }

    #[test]
    fn code_blocks_keep_their_lines() {
        let md = "Run:\n```bash\ncargo build --release   # long comment that goes on\n```\nDone.";
        let lines = plain(&markdown(md, 30));
        assert_eq!(lines[0], "Run:");
        assert_eq!(lines[1].trim_end(), " bash");
        assert!(lines[2].starts_with(" cargo build"));
        assert_eq!(lines[2].chars().count(), 30, "shaded to the full width: {lines:?}");
        assert!(lines[3].starts_with(" "), "cut, not wrapped at words: {lines:?}");
        assert_eq!(lines[lines.len() - 2].trim(), "");
        assert_eq!(lines.last().unwrap(), "Done.");
    }

    #[test]
    fn a_code_block_still_open_while_streaming() {
        let lines: Vec<String> = plain(&markdown("```\nfn main() {", 40))
            .iter()
            .map(|l| l.trim_end().to_string())
            .collect();
        assert_eq!(lines, vec!["", " fn main() {"]);
    }

    #[test]
    fn lists_headings_and_inline_styles() {
        let lines = markdown("# Title\n- one `x`\n2. two **b**\n- [ ] todo", 40);
        assert_eq!(plain(&lines), vec!["Title", "• one x", "2. two b", "☐ todo"]);
        let code = lines[1].spans.iter().find(|s| s.content == "x").unwrap();
        assert_eq!(code.style, INLINE_CODE);
        let bold = lines[2].spans.iter().find(|s| s.content == "b").unwrap();
        assert!(bold.style.add_modifier.contains(Modifier::BOLD));
    }

    #[test]
    fn list_items_wrap_under_their_text() {
        let lines = plain(&markdown("- alpha beta gamma delta epsilon", 16));
        assert!(lines[1].starts_with("  "), "{lines:?}");
    }

    #[test]
    fn stars_in_math_are_not_italic() {
        let spans = inline("2 * 3 * 4", Style::new());
        assert_eq!(
            spans.iter().map(|s| s.content.as_ref()).collect::<String>(),
            "2 * 3 * 4"
        );
    }
}
