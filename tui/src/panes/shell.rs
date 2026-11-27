//! A shell pane: your shell on a pseudo-terminal, its screen kept by a terminal
//! emulator (vt100) and drawn into the pane. It runs on this computer, in the active
//! project's folder when that folder is here. Nothing of Optimux is added to its
//! environment (no token, no key): it is your own shell.

use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

use anyhow::{Context, Result};
use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use portable_pty::{Child, CommandBuilder, MasterPty, PtySize, native_pty_system};
use ratatui::Frame;
use ratatui::layout::{Position, Rect};
use ratatui::style::{Color, Modifier, Style};
use tokio::sync::mpsc::UnboundedSender;

/// Lines kept above the screen, for the wheel.
const SCROLLBACK: usize = 5000;

pub struct Shell {
    pub cwd: PathBuf,
    /// Why it isn't in the project's folder, when it isn't.
    pub note: Option<String>,
    parser: Arc<Mutex<vt100::Parser>>,
    writer: Box<dyn Write + Send>,
    master: Box<dyn MasterPty + Send>,
    child: Box<dyn Child + Send + Sync>,
    size: (u16, u16),
    /// Lines scrolled up into the scrollback (0: the live screen).
    pub scroll: usize,
    pub exited: Option<String>,
}

/// The shell to run: $SHELL, else /bin/sh.
fn shell_program() -> String {
    std::env::var("SHELL")
        .ok()
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| "/bin/sh".into())
}

impl Shell {
    /// Starts the shell in `cwd`; `redraw` is told when it prints something (the pane's
    /// id), and once more when it ends.
    pub fn start(cwd: &Path, note: Option<String>, redraw: UnboundedSender<u64>, id: u64) -> Result<Shell> {
        let size = PtySize {
            rows: 24,
            cols: 80,
            pixel_width: 0,
            pixel_height: 0,
        };
        let pair = native_pty_system()
            .openpty(size)
            .context("Can't open a terminal for the shell")?;
        let mut cmd = CommandBuilder::new(shell_program());
        cmd.cwd(cwd);
        // a terminal the emulator understands; the rest of the environment is yours
        cmd.env("TERM", "xterm-256color");
        // the client's own access token must never reach a shell
        cmd.env_remove("OPTIMUX_TOKEN");
        let child = pair.slave.spawn_command(cmd).context("Can't start the shell")?;
        drop(pair.slave);
        let parser = Arc::new(Mutex::new(vt100::Parser::new(size.rows, size.cols, SCROLLBACK)));
        let mut reader = pair.master.try_clone_reader()?;
        let writer = pair.master.take_writer()?;
        {
            let parser = parser.clone();
            std::thread::spawn(move || {
                let mut buf = [0u8; 8192];
                loop {
                    match reader.read(&mut buf) {
                        Ok(0) | Err(_) => break,
                        Ok(n) => {
                            if let Ok(mut p) = parser.lock() {
                                p.process(&buf[..n]);
                            }
                            let _ = redraw.send(id);
                        }
                    }
                }
                let _ = redraw.send(id);
            });
        }
        Ok(Shell {
            cwd: cwd.to_path_buf(),
            note,
            parser,
            writer,
            master: pair.master,
            child,
            size: (size.rows, size.cols),
            scroll: 0,
            exited: None,
        })
    }

    /// The shell ended: its exit is noted (the pane says so; any key closes it).
    pub fn check(&mut self) -> bool {
        if self.exited.is_none() {
            if let Ok(Some(status)) = self.child.try_wait() {
                self.exited = Some(if status.success() {
                    "The shell ended.".into()
                } else {
                    format!("The shell ended ({status}).")
                });
            }
        }
        self.exited.is_some()
    }

    pub fn send(&mut self, bytes: &[u8]) {
        self.scroll = 0;
        let _ = self.writer.write_all(bytes);
        let _ = self.writer.flush();
    }

    pub fn key(&mut self, k: KeyEvent) {
        let app_cursor = self
            .parser
            .lock()
            .map(|p| p.screen().application_cursor())
            .unwrap_or(false);
        if let Some(b) = key_bytes(k, app_cursor) {
            self.send(&b);
        }
    }

    pub fn paste(&mut self, text: &str) {
        let bracketed = self
            .parser
            .lock()
            .map(|p| p.screen().bracketed_paste())
            .unwrap_or(false);
        let text = text.replace("\r\n", "\r").replace('\n', "\r");
        if bracketed {
            self.send(format!("\x1b[200~{text}\x1b[201~").as_bytes());
        } else {
            self.send(text.as_bytes());
        }
    }

    /// The wheel: up into what scrolled off the screen, down back to it.
    pub fn wheel(&mut self, up: bool) {
        let max = self.parser.lock().map(|mut p| {
            p.set_scrollback(usize::MAX);
            let m = p.screen().scrollback();
            p.set_scrollback(0);
            m
        });
        let max = max.unwrap_or(0);
        self.scroll = if up {
            (self.scroll + 3).min(max)
        } else {
            self.scroll.saturating_sub(3)
        };
    }

    /// Draws the screen into `area` (the terminal follows its size); the cursor shows
    /// in the focused pane.
    pub fn draw(&mut self, f: &mut Frame, area: Rect, focused: bool) {
        let (rows, cols) = (area.height.max(1), area.width.max(1));
        if (rows, cols) != self.size {
            self.size = (rows, cols);
            let _ = self.master.resize(PtySize {
                rows,
                cols,
                pixel_width: 0,
                pixel_height: 0,
            });
            if let Ok(mut p) = self.parser.lock() {
                p.set_size(rows, cols);
            }
        }
        let Ok(mut p) = self.parser.lock() else { return };
        p.set_scrollback(self.scroll);
        let screen = p.screen();
        let buf = f.buffer_mut();
        for r in 0..rows {
            for c in 0..cols {
                let Some(cell) = screen.cell(r, c) else { continue };
                let x = area.x + c;
                let y = area.y + r;
                let out = &mut buf[(x, y)];
                let text = cell.contents();
                if cell.is_wide_continuation() {
                    out.set_symbol("");
                    continue;
                }
                out.set_symbol(if text.is_empty() { " " } else { &text });
                let mut st = Style::new().fg(color(cell.fgcolor())).bg(color(cell.bgcolor()));
                if cell.bold() {
                    st = st.add_modifier(Modifier::BOLD);
                }
                if cell.italic() {
                    st = st.add_modifier(Modifier::ITALIC);
                }
                if cell.underline() {
                    st = st.add_modifier(Modifier::UNDERLINED);
                }
                if cell.inverse() {
                    st = st.add_modifier(Modifier::REVERSED);
                }
                out.set_style(st);
            }
        }
        if self.scroll > 0 {
            let note = format!(" ↑ {} lines up · the wheel or a key comes back ", self.scroll);
            let w = (note.chars().count() as u16).min(area.width);
            buf.set_string(
                area.x + area.width - w,
                area.y,
                &note,
                Style::new().fg(Color::Black).bg(Color::Yellow),
            );
        }
        let (cr, cc) = screen.cursor_position();
        let hidden = screen.hide_cursor();
        p.set_scrollback(0);
        if focused && self.scroll == 0 && !hidden && self.exited.is_none() && cr < rows && cc < cols {
            f.set_cursor_position(Position::new(area.x + cc, area.y + cr));
        }
    }

    pub fn stop(&mut self) {
        let _ = self.child.kill();
    }
}

impl Drop for Shell {
    fn drop(&mut self) {
        let _ = self.child.kill();
    }
}

fn color(c: vt100::Color) -> Color {
    match c {
        vt100::Color::Default => Color::Reset,
        vt100::Color::Idx(i) => Color::Indexed(i),
        vt100::Color::Rgb(r, g, b) => Color::Rgb(r, g, b),
    }
}

/// What a key sends to a terminal program (xterm's way).
pub fn key_bytes(k: KeyEvent, app_cursor: bool) -> Option<Vec<u8>> {
    let ctrl = k.modifiers.contains(KeyModifiers::CONTROL);
    let alt = k.modifiers.contains(KeyModifiers::ALT);
    let shift = k.modifiers.contains(KeyModifiers::SHIFT);
    // xterm's modifier number for arrows and the like: 1 + shift + 2·alt + 4·ctrl
    let m = 1 + u8::from(shift) + 2 * u8::from(alt) + 4 * u8::from(ctrl);
    let arrow = |c: char| -> Vec<u8> {
        if m > 1 {
            format!("\x1b[1;{m}{c}").into_bytes()
        } else if app_cursor {
            format!("\x1bO{c}").into_bytes()
        } else {
            format!("\x1b[{c}").into_bytes()
        }
    };
    let tilde = |n: u8| -> Vec<u8> {
        if m > 1 {
            format!("\x1b[{n};{m}~").into_bytes()
        } else {
            format!("\x1b[{n}~").into_bytes()
        }
    };
    let mut out = match k.code {
        KeyCode::Char(c) if ctrl => {
            let b = match c.to_ascii_lowercase() {
                c @ 'a'..='z' => c as u8 - b'a' + 1,
                ' ' | '@' | '2' => 0,
                '[' | '3' => 27,
                '\\' | '4' => 28,
                ']' | '5' => 29,
                '^' | '6' => 30,
                '_' | '7' | '/' => 31,
                '8' | '?' => 127,
                _ => return None,
            };
            vec![b]
        }
        KeyCode::Char(c) => c.to_string().into_bytes(),
        KeyCode::Enter => vec![b'\r'],
        KeyCode::Tab => vec![b'\t'],
        KeyCode::BackTab => b"\x1b[Z".to_vec(),
        KeyCode::Backspace => vec![if ctrl { 8 } else { 127 }],
        KeyCode::Esc => vec![27],
        KeyCode::Up => arrow('A'),
        KeyCode::Down => arrow('B'),
        KeyCode::Right => arrow('C'),
        KeyCode::Left => arrow('D'),
        KeyCode::Home => arrow('H'),
        KeyCode::End => arrow('F'),
        KeyCode::Insert => tilde(2),
        KeyCode::Delete => tilde(3),
        KeyCode::PageUp => tilde(5),
        KeyCode::PageDown => tilde(6),
        KeyCode::F(n @ 1..=4) => {
            let c = b"PQRS"[(n - 1) as usize];
            if m > 1 {
                format!("\x1b[1;{m}{}", c as char).into_bytes()
            } else {
                vec![27, b'O', c]
            }
        }
        KeyCode::F(n @ 5..=12) => tilde([15, 17, 18, 19, 20, 21, 23, 24][(n - 5) as usize]),
        _ => return None,
    };
    // Alt+a letter: ESC first (arrows carry Alt in their number already)
    if alt && matches!(k.code, KeyCode::Char(_) | KeyCode::Enter | KeyCode::Backspace) {
        out.insert(0, 27);
    }
    Some(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn k(code: KeyCode, m: KeyModifiers) -> Vec<u8> {
        key_bytes(KeyEvent::new(code, m), false).unwrap()
    }

    #[test]
    fn keys_are_sent_as_a_terminal_sends_them() {
        let n = KeyModifiers::NONE;
        assert_eq!(k(KeyCode::Char('a'), n), b"a");
        assert_eq!(k(KeyCode::Char('c'), KeyModifiers::CONTROL), vec![3]);
        assert_eq!(k(KeyCode::Char('x'), KeyModifiers::ALT), b"\x1bx");
        assert_eq!(k(KeyCode::Enter, n), b"\r");
        assert_eq!(k(KeyCode::Backspace, n), vec![127]);
        assert_eq!(k(KeyCode::Up, n), b"\x1b[A");
        assert_eq!(key_bytes(KeyEvent::new(KeyCode::Up, n), true).unwrap(), b"\x1bOA");
        assert_eq!(k(KeyCode::Right, KeyModifiers::CONTROL), b"\x1b[1;5C");
        assert_eq!(k(KeyCode::Delete, n), b"\x1b[3~");
        assert_eq!(k(KeyCode::F(5), n), b"\x1b[15~");
        assert_eq!(k(KeyCode::F(1), n), b"\x1bOP");
        assert_eq!(k(KeyCode::Char('é'), n), "é".as_bytes());
    }

    #[test]
    fn a_shell_runs_and_its_screen_is_kept() {
        let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel();
        let dir = tempfile::tempdir().unwrap();
        let mut sh = Shell::start(dir.path(), None, tx, 7).unwrap();
        sh.send(b"echo hello-pane; exit\r");
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(10);
        while !sh.check() && std::time::Instant::now() < deadline {
            let _ = rx.try_recv();
            std::thread::sleep(std::time::Duration::from_millis(50));
        }
        assert!(sh.exited.is_some());
        let text = sh.parser.lock().unwrap().screen().contents();
        assert!(text.contains("hello-pane"), "{text}");
    }
}
