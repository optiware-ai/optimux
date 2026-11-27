//! Dashboard → Skills, as in the web page: when skills run (off, manual only, auto
//! allowed); every skill with its scope, mode and use; new, edit (in your editor),
//! Manual/Auto, remove (moved aside, not deleted), and a draft from a chat.

use crossterm::event::{KeyCode, KeyEvent};
use ratatui::Frame;
use ratatui::layout::Rect;
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::Paragraph;
use serde_json::{Value, json};

use super::{Answer, Ctx, Section, draw_head, fmt, head_lines};
use crate::api::{Method, enc};
use crate::chat::text::{DIM, wrap};
use crate::cli::{num, s};
use crate::ui::confirm::{self, Confirm};
use crate::ui::external::Edit;
use crate::ui::form::{self, Field, Form, Kind};
use crate::ui::table::{Row, Table};

pub const MODES: &[(&str, &str, &str)] = &[
    ("off", "Off", "No skill is used, and /name commands are refused."),
    (
        "manual",
        "Manual only",
        "A skill runs only when you type /name in the chat. Idle skills cost nothing.",
    ),
    (
        "auto",
        "Auto allowed",
        "Skills set to Auto are also listed in every request (≈30 tokens each), so models can pick them.",
    ),
];

/// A skill's name, as the server's NAME_RE.
pub fn name_ok(n: &str) -> bool {
    let mut c = n.chars();
    c.next().is_some_and(|f| f.is_ascii_lowercase() || f.is_ascii_digit())
        && n.len() <= 64
        && c.all(|x| x.is_ascii_lowercase() || x.is_ascii_digit() || x == '-')
}

/// What is being edited in the editor: the skill and whether it is new.
#[derive(Clone)]
struct Editing {
    scope: String,
    name: String,
}

enum View {
    List,
    New {
        form: Form,
        draft_of: Option<String>,
    },
    Chats(Table),
    Remove(Value, Confirm),
    Draft {
        session: String,
        scope: String,
        name: String,
        confirm: Confirm,
    },
}

pub struct Skills {
    table: Table,
    info: Value,
    settings: Value,
    estimates: Value,
    view: View,
    editing: Option<Editing>,
    /// The text the editor opened with (or last saved): saved as it is for a new
    /// skill, and opened again after a failed save.
    base: String,
    /// A save failed: e opens `base` again for `editing`.
    reopen: bool,
    pending_edit: Option<Edit>,
    msg: Option<(bool, String)>,
}

impl Default for Skills {
    fn default() -> Skills {
        let mut table = Table::new(&[
            ("Skill", 22),
            ("Scope", 8),
            ("Mode", 7),
            ("Description", 0),
            ("Used", 18),
        ]);
        table.empty = "No skills yet: n makes one.".into();
        Skills {
            table,
            info: Value::Null,
            settings: Value::Null,
            estimates: Value::Null,
            view: View::List,
            editing: None,
            base: String::new(),
            reopen: false,
            pending_edit: None,
            msg: None,
        }
    }
}

fn key_of(sk: &Value) -> String {
    format!("{}/{}", s(sk, "scope"), s(sk, "name"))
}

impl Skills {
    fn skills(&self) -> Vec<Value> {
        self.info
            .get("skills")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default()
    }

    fn chosen(&self) -> Option<Value> {
        let id = self.table.chosen()?.id.clone();
        self.skills().into_iter().find(|sk| key_of(sk) == id)
    }

    fn rows(&mut self) {
        let rows = self
            .skills()
            .iter()
            .map(|sk| {
                let mut desc = s(sk, "description");
                if desc.is_empty() {
                    desc = "No description: models can't use it until it has one.".into();
                }
                if sk.get("usable").and_then(Value::as_bool) == Some(true)
                    && sk.get("active").and_then(Value::as_bool) != Some(true)
                {
                    desc = format!("(overridden by the project's) {desc}");
                }
                let used = format!(
                    "{:.0} calls · {}",
                    sk.pointer("/all_time/calls").and_then(Value::as_f64).unwrap_or(0.0),
                    fmt::cost(sk.pointer("/all_time/cost").and_then(Value::as_f64).unwrap_or(0.0))
                );
                let mode = s(sk, "mode");
                Row::new(key_of(sk))
                    .styled(format!("/{}", s(sk, "name")), Style::new().fg(Color::LightCyan))
                    .styled(s(sk, "scope"), DIM)
                    .styled(
                        mode.clone(),
                        if mode == "auto" {
                            Style::new().fg(Color::Green)
                        } else {
                            DIM
                        },
                    )
                    .cell(desc)
                    .styled(used, DIM)
            })
            .collect();
        self.table.set_rows(rows);
    }

    fn scope_choices(&self) -> Vec<(String, String)> {
        let mut v = vec![("global".to_string(), "global (every project)".to_string())];
        if !s(&self.settings, "active_project").is_empty() {
            v.insert(
                0,
                (
                    "project".into(),
                    format!("project ({})", s(&self.settings, "active_project")),
                ),
            );
        }
        v
    }

    fn new_form(&self, draft_of: Option<String>) -> View {
        let mut f = Form::new(
            if draft_of.is_some() {
                "Save a chat as a skill"
            } else {
                "New skill"
            },
            vec![
                Field::new("name", "Name", Kind::Text)
                    .required()
                    .help("Lowercase letters, digits and dashes, like release-notes; run it with /name"),
                Field::new("scope", "For", Kind::Choice(self.scope_choices())),
            ],
        )
        .submit_label(if draft_of.is_some() { "Draft it" } else { "Write it" });
        f.intro = if draft_of.is_some() {
            "The Simple model drafts the instructions from the chat (one paid call); you review them in your editor before saving.".into()
        } else {
            "Opens a template in your editor; save and quit to keep it. New skills start as Manual.".into()
        };
        View::New { form: f, draft_of }
    }

    fn reload(&self, ctx: &Ctx) {
        ctx.call("skills", Method::Get, "/api/skills", None);
    }
}

impl Section for Skills {
    fn load(&mut self, ctx: &Ctx) {
        self.reload(ctx);
        ctx.call("settings", Method::Get, "/api/settings", None);
        ctx.call("estimates", Method::Get, "/api/costs/estimates", None);
    }

    fn key(&mut self, k: KeyEvent, ctx: &Ctx) -> bool {
        let view = std::mem::replace(&mut self.view, View::List);
        let (view, used) = match view {
            View::New { mut form, draft_of } => match form.key(k) {
                form::Action::Cancel => (View::List, true),
                form::Action::Submit => {
                    let name = form.get("name").trim().to_string();
                    let scope = form.get("scope").to_string();
                    if !name_ok(&name) {
                        form.error = Some("Use lowercase letters, digits and dashes (e.g. release-notes).".into());
                        (View::New { form, draft_of }, true)
                    } else if self
                        .skills()
                        .iter()
                        .any(|sk| s(sk, "name") == name && s(sk, "scope") == scope)
                    {
                        form.error = Some(format!("A {scope} skill /{name} exists already: e edits it."));
                        (View::New { form, draft_of }, true)
                    } else {
                        match draft_of {
                            Some(session) => {
                                let est = self.estimates.get("skill_draft").filter(|v| !v.is_null());
                                let cost = match est {
                                    Some(e) => format!(
                                        " About {} (the average of your last {:.0} drafts).",
                                        fmt::cost(num(e, "avg").unwrap_or(0.0)),
                                        num(e, "n").unwrap_or(0.0)
                                    ),
                                    None => String::new(),
                                };
                                let confirm = Confirm::new(
                                    "Draft the skill?",
                                    format!(
                                        "The Simple model reads the chat and writes /{name}. This is one paid model call.{cost}"
                                    ),
                                    "draft it",
                                );
                                (
                                    View::Draft {
                                        session,
                                        scope,
                                        name,
                                        confirm,
                                    },
                                    true,
                                )
                            }
                            None => {
                                self.editing = Some(Editing {
                                    scope,
                                    name: name.clone(),
                                });
                                ctx.call(
                                    "template",
                                    Method::Get,
                                    format!("/api/skills/template?name={}", enc(&name)),
                                    None,
                                );
                                (View::List, true)
                            }
                        }
                    }
                }
                form::Action::None => (View::New { form, draft_of }, true),
            },
            View::Chats(mut t) => {
                if t.key(k) {
                    (View::Chats(t), true)
                } else {
                    match (k.code, t.chosen().map(|r| r.id.clone())) {
                        (KeyCode::Esc, _) => (View::List, true),
                        (KeyCode::Enter, Some(id)) => (self.new_form(Some(id)), true),
                        _ => (View::Chats(t), true),
                    }
                }
            }
            View::Draft {
                session,
                scope,
                name,
                mut confirm,
            } => match confirm.key(k) {
                confirm::Answer::Yes => {
                    self.msg = Some((true, format!("Drafting /{name}…")));
                    self.editing = Some(Editing {
                        scope,
                        name: name.clone(),
                    });
                    ctx.call(
                        "draft",
                        Method::Post,
                        "/api/skills/draft",
                        Some(json!({"session_id": session, "name": name})),
                    );
                    (View::List, true)
                }
                confirm::Answer::No => (View::List, true),
                confirm::Answer::Waiting => (
                    View::Draft {
                        session,
                        scope,
                        name,
                        confirm,
                    },
                    true,
                ),
            },
            View::Remove(sk, mut c) => match c.key(k) {
                confirm::Answer::Yes => {
                    ctx.call(
                        format!("removed:{}", s(&sk, "name")),
                        Method::Post,
                        format!("/api/skills/{}/{}/remove", s(&sk, "scope"), enc(&s(&sk, "name"))),
                        None,
                    );
                    (View::List, true)
                }
                confirm::Answer::No => (View::List, true),
                confirm::Answer::Waiting => (View::Remove(sk, c), true),
            },
            View::List => {
                if self.table.key(k) {
                    (View::List, true)
                } else {
                    let chosen = self.chosen();
                    match (k.code, chosen) {
                        (KeyCode::Char('s'), _) => {
                            let now = s(&self.info, "skills_mode");
                            let i = MODES.iter().position(|(m, ..)| *m == now).unwrap_or(2);
                            let next = MODES[(i + 1) % MODES.len()].0;
                            ctx.save_settings("mode_saved", json!({"skills_mode": next}));
                            (View::List, true)
                        }
                        (KeyCode::Char('n'), _) => (self.new_form(None), true),
                        (KeyCode::Char('d'), _) => {
                            ctx.call("chats", Method::Get, "/api/sessions?limit=200", None);
                            let mut t = Table::new(&[("Chat", 0), ("Updated", 14)]);
                            t.empty = "Loading the chats of the active project…".into();
                            (View::Chats(t), true)
                        }
                        (KeyCode::Char('m'), Some(sk)) if sk.get("usable").and_then(Value::as_bool) == Some(true) => {
                            let next = if s(&sk, "mode") == "auto" { "manual" } else { "auto" };
                            ctx.call(
                                format!("skill_mode:{}={next}", s(&sk, "name")),
                                Method::Post,
                                format!("/api/skills/{}/{}/mode", s(&sk, "scope"), enc(&s(&sk, "name"))),
                                Some(json!({"mode": next})),
                            );
                            (View::List, true)
                        }
                        (KeyCode::Char('m'), Some(_)) => {
                            self.msg = Some((
                                false,
                                "Give it a description first (e edits it): a skill needs one to be listed.".into(),
                            ));
                            (View::List, true)
                        }
                        (KeyCode::Char('e'), _) if self.reopen && self.editing.is_some() => {
                            self.reopen = false;
                            self.pending_edit = Some(Edit {
                                text: self.base.clone(),
                                suffix: ".md".into(),
                            });
                            (View::List, true)
                        }
                        (KeyCode::Char('e') | KeyCode::Enter, Some(sk)) => {
                            self.editing = Some(Editing {
                                scope: s(&sk, "scope"),
                                name: s(&sk, "name"),
                            });
                            ctx.call(
                                "content",
                                Method::Get,
                                format!("/api/skills/{}/{}", s(&sk, "scope"), enc(&s(&sk, "name"))),
                                None,
                            );
                            (View::List, true)
                        }
                        (KeyCode::Char('x'), Some(sk)) => {
                            let c = Confirm::new(
                                format!("Remove the skill /{}?", s(&sk, "name")),
                                format!(
                                    "This {} skill's folder is moved to ~/.optimux/legacy/skills/, not deleted.",
                                    s(&sk, "scope")
                                ),
                                "remove it",
                            );
                            (View::Remove(sk, c), true)
                        }
                        (KeyCode::Char('r'), _) => {
                            self.load(ctx);
                            (View::List, true)
                        }
                        _ => (View::List, false),
                    }
                }
            }
        };
        self.view = view;
        used
    }

    fn paste(&mut self, text: &str) {
        if let View::New { form, .. } = &mut self.view {
            form.paste(text);
        }
    }

    fn take_edit(&mut self) -> Option<Edit> {
        self.pending_edit.take()
    }

    fn edited(&mut self, result: Result<Option<String>, String>, ctx: &Ctx) {
        let Some(e) = self.editing.clone() else { return };
        let is_new = !self
            .skills()
            .iter()
            .any(|sk| s(sk, "name") == e.name && s(sk, "scope") == e.scope);
        let text = match result {
            Err(err) => {
                self.msg = Some((false, err));
                self.editing = None;
                return;
            }
            Ok(Some(text)) => text,
            // a template or a draft kept as it was is still saved: the user asked for it
            Ok(None) if is_new => self.base.clone(),
            Ok(None) => {
                self.msg = Some((true, "No change.".into()));
                self.editing = None;
                return;
            }
        };
        self.base = text.clone();
        self.msg = Some((true, format!("Saving /{}…", e.name)));
        ctx.call(
            "saved",
            Method::Post,
            format!("/api/skills/{}/{}", e.scope, enc(&e.name)),
            Some(json!({"content": text})),
        );
    }

    fn on_answer(&mut self, a: Answer, ctx: &Ctx) {
        let (what, arg) = a.parts();
        let (what, arg) = (what.to_string(), arg.to_string());
        match (what.as_str(), a.result) {
            ("skills", Ok(v)) => {
                self.info = v;
                self.rows();
            }
            ("settings", Ok(v)) => self.settings = v,
            ("estimates", Ok(v)) => self.estimates = v,
            ("mode_saved", Ok(v)) => {
                self.settings = v;
                let m = s(&self.settings, "skills_mode");
                let label = MODES.iter().find(|(id, ..)| *id == m).map(|(_, l, _)| *l).unwrap_or("");
                self.msg = Some((true, format!("When skills run: {label}.")));
                self.reload(ctx);
            }
            ("skill_mode", Ok(_)) => {
                let (name, mode) = arg.split_once('=').unwrap_or((&arg, ""));
                self.msg = Some((
                    true,
                    if mode == "auto" {
                        format!("/{name} is Auto: listed in every request.")
                    } else {
                        format!("/{name} is Manual: it runs only on /{name}.")
                    },
                ));
                self.reload(ctx);
            }
            ("removed", Ok(v)) => {
                self.msg = Some((true, format!("/{arg} moved to {}.", s(&v, "moved_to"))));
                self.reload(ctx);
            }
            ("chats", Ok(v)) => {
                if let View::Chats(t) = &mut self.view {
                    let rows = v
                        .as_array()
                        .into_iter()
                        .flatten()
                        .map(|c| {
                            let title = if s(c, "title").is_empty() {
                                "(untitled)".into()
                            } else {
                                s(c, "title")
                            };
                            Row::new(s(c, "id")).cell(title).styled(
                                num(c, "updated").map(crate::dash::projects::ago).unwrap_or_default(),
                                DIM,
                            )
                        })
                        .collect();
                    t.empty = "No chats in the active project.".into();
                    t.set_rows(rows);
                }
            }
            ("content", Ok(v)) | ("template", Ok(v)) | ("draft", Ok(v)) => {
                self.base = s(&v, "content");
                self.reopen = false;
                self.msg = None;
                self.pending_edit = Some(Edit {
                    text: self.base.clone(),
                    suffix: ".md".into(),
                });
            }
            ("saved", Ok(_)) => {
                let name = self.editing.take().map(|e| e.name).unwrap_or_default();
                self.reopen = false;
                self.msg = Some((true, format!("/{name} saved.")));
                self.reload(ctx);
            }
            ("saved", Err(e)) => {
                self.reopen = true;
                let e = if e.ends_with(['.', '!', '?']) {
                    e
                } else {
                    format!("{e}.")
                };
                self.msg = Some((false, format!("Not saved: {e} Press e to open your text again.")));
            }
            (_, Err(e)) => {
                if let View::New { form, .. } = &mut self.view {
                    form.error = Some(e);
                } else {
                    self.msg = Some((false, e));
                    self.editing = None;
                }
            }
            _ => {}
        }
    }

    fn keys(&self) -> String {
        match &self.view {
            View::List => {
                "s when skills run · n new · d from a chat · e edit · m manual/auto · x remove · / find · Esc menu"
                    .into()
            }
            View::Chats(_) => "Enter this chat · / find · Esc back".into(),
            _ => String::new(),
        }
    }

    fn draw(&mut self, f: &mut Frame, area: Rect) -> Vec<(u16, usize)> {
        let width = area.width as usize;
        let body = draw_head(
            f,
            area,
            head_lines(
                "Skills",
                "  ·  reusable instructions, loaded only when a task needs them",
                &self.msg,
                width,
            ),
        );
        let bold = Style::new().fg(Color::LightCyan).add_modifier(Modifier::BOLD);
        match &self.view {
            View::List => {
                let mode = s(&self.info, "skills_mode");
                let mut top = vec![Line::from(
                    vec![Span::styled("When skills run (s): ", DIM)]
                        .into_iter()
                        .chain(MODES.iter().flat_map(|(id, label, _)| {
                            let style = if *id == mode {
                                Style::new().fg(Color::Black).bg(Color::LightCyan)
                            } else {
                                DIM
                            };
                            [Span::styled(format!(" {label} "), style), Span::raw(" ")]
                        }))
                        .collect::<Vec<_>>(),
                )];
                if let Some((.., help)) = MODES.iter().find(|(id, ..)| *id == mode) {
                    top.extend(wrap(vec![Span::styled(help.to_string(), DIM)], width, "", ""));
                }
                let tokens = num(&self.info, "catalog_tokens").unwrap_or(0.0);
                let listed = self.info.get("listed").and_then(Value::as_array).map_or(0, Vec::len);
                top.push(Line::from(Span::styled(
                    if tokens > 0.0 {
                        format!("{listed} skill(s) listed in every request: ≈{tokens:.0} tokens per model call")
                    } else {
                        "Nothing listed: skills cost nothing until you call one".into()
                    },
                    DIM,
                )));
                top.push(Line::default());
                let n = (top.len() as u16).min(body.height);
                f.render_widget(Paragraph::new(top), Rect { height: n, ..body });
                let table = Rect {
                    y: body.y + n,
                    height: body.height.saturating_sub(n + 3),
                    ..body
                };
                let placed = self.table.draw(f, table);
                let mut foot = vec![Line::default()];
                foot.extend(wrap(
                    vec![Span::styled(
                        format!(
                            "Global skills: {} · project skills: the {} folder of each project",
                            s(&self.info, "global_dir"),
                            s(&self.info, "project_dir_name")
                        ),
                        DIM,
                    )],
                    width,
                    "",
                    "",
                ));
                f.render_widget(
                    Paragraph::new(foot),
                    Rect {
                        y: table.y + table.height,
                        height: 3.min(body.height),
                        ..body
                    },
                );
                placed
            }
            View::New { form, .. } => {
                let title = vec![Line::from(Span::styled(form.title.clone(), bold)), Line::default()];
                f.render_widget(Paragraph::new(title), Rect { height: 2, ..body });
                form.draw(
                    f,
                    Rect {
                        y: body.y + 2,
                        height: body.height.saturating_sub(2),
                        ..body
                    },
                );
                Vec::new()
            }
            View::Chats(t) => {
                f.render_widget(
                    Paragraph::new(Line::from(Span::styled("Which chat becomes a skill?", bold))),
                    Rect { height: 1, ..body },
                );
                t.draw(
                    f,
                    Rect {
                        y: body.y + 2,
                        height: body.height.saturating_sub(2),
                        ..body
                    },
                )
            }
            View::Remove(_, c) | View::Draft { confirm: c, .. } => {
                let mut lines = vec![Line::from(Span::styled(c.title.clone(), bold)), Line::default()];
                lines.extend(c.lines(width));
                f.render_widget(Paragraph::new(lines), body);
                Vec::new()
            }
        }
    }

    fn tabs(&self) -> Vec<Vec<String>> {
        if !matches!(self.view, View::List) {
            return Vec::new();
        }
        vec![MODES.iter().map(|(_, l, _)| format!(" {l} ")).collect()]
    }

    fn click_tab(&mut self, _row: usize, i: usize, ctx: &Ctx) {
        // a click chooses that one (s goes to the next)
        let Some((mode, ..)) = MODES.get(i) else { return };
        if s(&self.info, "skills_mode") != *mode {
            ctx.save_settings("mode_saved", json!({"skills_mode": mode}));
        }
    }

    fn has_window(&self) -> bool {
        !matches!(self.view, View::List) || self.table.filtering
    }

    fn click_row(&mut self, i: usize, _ctx: &Ctx) -> bool {
        match &mut self.view {
            View::List => self.table.selected = i.min(self.table.visible().len().saturating_sub(1)),
            View::Chats(t) => t.selected = i.min(t.visible().len().saturating_sub(1)),
            _ => {}
        }
        false
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn skill_names_as_the_server() {
        assert!(name_ok("release-notes"));
        assert!(name_ok("a1"));
        assert!(!name_ok("Release"));
        assert!(!name_ok("-x"));
        assert!(!name_ok(&"a".repeat(65)));
    }
}
