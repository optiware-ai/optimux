//! Dashboard → Scope: the Workspace folder, the folder that holds the projects.

use crossterm::event::{KeyCode, KeyEvent};
use ratatui::Frame;
use ratatui::layout::Rect;
use ratatui::text::Span;
use ratatui::widgets::Paragraph;
use serde_json::{Value, json};

use super::{Answer, Ctx, Section, draw_head, head_lines};
use crate::api::Method;
use crate::chat::text::{DIM, wrap};
use crate::cli::s;
use crate::ui::form::{self, Field, Form, Kind};

pub const ABOUT: &str = "The Workspace folder holds your projects: each folder inside it is a project; with no project \
selected you are in the Workspace itself. Commands start in the active project (or in the Workspace), and commands that \
refer to paths outside it are flagged before you approve them. To really confine commands to it, turn on the sandbox in \
Execution.";

#[derive(Default)]
pub struct Scope {
    form: Option<Form>,
    msg: Option<(bool, String)>,
}

impl Section for Scope {
    fn load(&mut self, ctx: &Ctx) {
        ctx.call("settings", Method::Get, "/api/settings", None);
    }

    fn key(&mut self, k: KeyEvent, ctx: &Ctx) -> bool {
        let Some(f) = &mut self.form else { return false };
        if k.code == KeyCode::Esc {
            return false;
        }
        if f.key(k) == form::Action::Submit {
            let path = f.get("root").trim().to_string();
            if !path.starts_with('/') {
                f.error = Some("An absolute path, like /home/you/git.".into());
                return true;
            }
            f.busy = true;
            self.msg = None;
            ctx.save_settings("saved", json!({"projects_root": path}));
        }
        true
    }

    fn paste(&mut self, text: &str) {
        if let Some(f) = &mut self.form {
            f.paste(text);
        }
    }

    fn on_answer(&mut self, a: Answer, _ctx: &Ctx) {
        let what = a.parts().0.to_string();
        match (what.as_str(), a.result) {
            ("settings", Ok(v)) => {
                let mut f = Form::new(
                    "Workspace folder",
                    vec![
                        Field::new("root", "Workspace folder", Kind::Text)
                            .value(s(&v, "projects_root"))
                            .required()
                            .help("An absolute path, like /home/you/git"),
                    ],
                )
                .submit_label("Save")
                .no_keys_line();
                f.focus = 0;
                self.form = Some(f);
            }
            ("saved", Ok(v)) => {
                if let Some(f) = &mut self.form {
                    f.busy = false;
                }
                let note = v.get("_note").and_then(Value::as_str).unwrap_or("");
                self.msg = Some((
                    true,
                    if note.is_empty() {
                        format!("Saved: {}.", s(&v, "projects_root"))
                    } else {
                        format!("Saved. {note}")
                    },
                ));
            }
            (_, Err(e)) => {
                if let Some(f) = &mut self.form {
                    f.busy = false;
                    f.error = Some(e);
                } else {
                    self.msg = Some((false, e));
                }
            }
            _ => {}
        }
    }

    fn keys(&self) -> String {
        "Ctrl+S save · Ctrl+U clear · Esc menu".into()
    }

    fn draw(&mut self, f: &mut Frame, area: Rect) -> Vec<(u16, usize)> {
        let width = area.width as usize;
        let body = draw_head(
            f,
            area,
            head_lines("Scope", "  ·  the folder that holds your projects", &self.msg, width),
        );
        let about = wrap(vec![Span::styled(ABOUT, DIM)], width, "", "");
        let n = about.len() as u16 + 1;
        f.render_widget(
            Paragraph::new(about),
            Rect {
                height: n.min(body.height),
                ..body
            },
        );
        match &self.form {
            Some(form) => form.draw(
                f,
                Rect {
                    y: body.y + n,
                    height: body.height.saturating_sub(n),
                    ..body
                },
            ),
            None => f.render_widget(
                Paragraph::new(Span::styled("Loading…", DIM)),
                Rect {
                    y: body.y + n,
                    height: 1,
                    ..body
                },
            ),
        }
        Vec::new()
    }

    fn has_window(&self) -> bool {
        false
    }
}
