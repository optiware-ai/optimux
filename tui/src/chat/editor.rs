//! The message box: a small multi-line editor (characters, not bytes, so accents and
//! wide characters move as one).

#[derive(Debug, Default)]
pub struct Editor {
    chars: Vec<char>,
    cursor: usize,
}

impl Editor {
    pub fn text(&self) -> String {
        self.chars.iter().collect()
    }

    pub fn is_blank(&self) -> bool {
        self.chars.iter().all(|c| c.is_whitespace())
    }

    pub fn take(&mut self) -> String {
        self.cursor = 0;
        std::mem::take(&mut self.chars).into_iter().collect()
    }

    pub fn insert(&mut self, c: char) {
        let c = if c == '\r' { '\n' } else { c };
        self.chars.insert(self.cursor, c);
        self.cursor += 1;
    }

    pub fn insert_str(&mut self, s: &str) {
        for c in s.replace("\r\n", "\n").chars() {
            self.insert(c);
        }
    }

    pub fn backspace(&mut self) {
        if self.cursor > 0 {
            self.cursor -= 1;
            self.chars.remove(self.cursor);
        }
    }

    pub fn delete(&mut self) {
        if self.cursor < self.chars.len() {
            self.chars.remove(self.cursor);
        }
    }

    pub fn left(&mut self) {
        self.cursor = self.cursor.saturating_sub(1);
    }

    pub fn right(&mut self) {
        self.cursor = (self.cursor + 1).min(self.chars.len());
    }

    pub fn home(&mut self) {
        self.cursor = self.line_start(self.cursor);
    }

    pub fn end(&mut self) {
        while self.cursor < self.chars.len() && self.chars[self.cursor] != '\n' {
            self.cursor += 1;
        }
    }

    /// Up one line, keeping the column. False when already on the first line.
    pub fn up(&mut self) -> bool {
        let start = self.line_start(self.cursor);
        if start == 0 {
            return false;
        }
        let col = self.cursor - start;
        let prev = self.line_start(start - 1);
        self.cursor = (prev + col).min(start - 1);
        true
    }

    /// Down one line, keeping the column. False when already on the last line.
    pub fn down(&mut self) -> bool {
        let start = self.line_start(self.cursor);
        let col = self.cursor - start;
        let Some(nl) = self.chars[self.cursor..].iter().position(|&c| c == '\n') else {
            return false;
        };
        let next = self.cursor + nl + 1;
        let next_end = self.chars[next..]
            .iter()
            .position(|&c| c == '\n')
            .map_or(self.chars.len(), |p| next + p);
        self.cursor = (next + col).min(next_end);
        true
    }

    /// Deletes the word before the cursor (Ctrl+W).
    pub fn delete_word(&mut self) {
        while self.cursor > 0 && self.chars[self.cursor - 1].is_whitespace() {
            self.backspace();
        }
        while self.cursor > 0 && !self.chars[self.cursor - 1].is_whitespace() {
            self.backspace();
        }
    }

    /// The text before the cursor: the screen uses it to place the cursor.
    pub fn before_cursor(&self) -> String {
        self.chars[..self.cursor].iter().collect()
    }

    fn line_start(&self, mut i: usize) -> usize {
        while i > 0 && self.chars[i - 1] != '\n' {
            i -= 1;
        }
        i
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ed(s: &str) -> Editor {
        let mut e = Editor::default();
        e.insert_str(s);
        e
    }

    #[test]
    fn typing_and_moving() {
        let mut e = ed("héllo");
        e.left();
        e.backspace();
        e.insert('X');
        assert_eq!(e.text(), "hélXo");
        e.home();
        e.delete();
        assert_eq!(e.text(), "élXo");
        assert_eq!(e.take(), "élXo");
        assert!(e.is_blank());
    }

    #[test]
    fn up_and_down_keep_the_column() {
        let mut e = ed("abcdef\nxy\nlonger line");
        assert!(e.up()); // from the end of "longer line" (col 11) to the end of "xy"
        assert_eq!(e.before_cursor(), "abcdef\nxy");
        assert!(e.up());
        assert_eq!(e.before_cursor(), "ab");
        assert!(!e.up());
        assert!(e.down());
        assert_eq!(e.before_cursor(), "abcdef\nxy");
        assert!(e.down());
        assert!(!e.down());
    }

    #[test]
    fn pasted_windows_lines_become_plain_lines() {
        assert_eq!(ed("a\r\nb").text(), "a\nb");
    }

    #[test]
    fn delete_a_word() {
        let mut e = ed("git commit  ");
        e.delete_word();
        assert_eq!(e.text(), "git ");
    }
}
