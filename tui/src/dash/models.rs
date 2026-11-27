//! Dashboard → Models, as in the web page: the configured models (test, edit,
//! duplicate, key, thinking, delete), adding one from the catalog or by hand, and the
//! online catalog (on/off, refresh). The same calls as the page's app.js.

use std::collections::HashMap;

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use ratatui::Frame;
use ratatui::layout::Rect;
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::Paragraph;
use serde_json::{Value, json};

use super::{Answer, Ctx, Section};
use crate::api::{Method, enc};
use crate::chat::text::{DIM, wrap};
use crate::ui::confirm::{self, Confirm};
use crate::ui::form::{self, Field, Form, Kind};
use crate::ui::table::{Row, Table};

const THINKING: &[(&str, &str)] = &[
    ("default", "Default (provider decides)"),
    ("minimal", "Minimal"),
    ("low", "Low"),
    ("medium", "Medium"),
    ("high", "High"),
];

#[derive(Debug, Clone, PartialEq)]
enum FormFor {
    Edit(String),
    Duplicate,
    Key(String),
    Thinking(String),
    Other,
    /// From the catalog: provider id, its key variable, whether a key must be typed.
    Catalog {
        pid: String,
        env: String,
        needs_key: bool,
    },
}

enum View {
    List,
    Form(FormFor, Form),
    Delete(String, Confirm),
    Providers(Table),
    Catalog { pid: String, table: Table },
}

pub struct Models {
    pub table: Table,
    config: Value,
    catalog: Value,
    /// The models of the provider being browsed.
    provider_models: Value,
    tools_only: bool,
    hide_retired: bool,
    view: View,
    /// A result per model id (test, saved...): (worked, text).
    notices: HashMap<String, (bool, String)>,
    /// The section's last message: (worked, text).
    msg: Option<(bool, String)>,
    loading: bool,
}

impl Default for Models {
    fn default() -> Models {
        let mut table = Table::new(&[
            ("Name", 22),
            ("Model ID", 0),
            ("Key", 12),
            ("$/1M in / out", 20),
            ("Thinking", 9),
        ]);
        table.empty = "No models configured. a adds one from the catalog, o by hand.".into();
        Models {
            table,
            config: Value::Null,
            catalog: Value::Null,
            provider_models: Value::Null,
            tools_only: true,
            hide_retired: true,
            view: View::List,
            notices: HashMap::new(),
            msg: None,
            loading: false,
        }
    }
}

fn s(v: &Value, k: &str) -> String {
    v.get(k).and_then(Value::as_str).unwrap_or("").to_string()
}

fn num(v: &Value, k: &str) -> Option<f64> {
    v.get(k).and_then(Value::as_f64)
}

fn price(n: Option<f64>) -> String {
    match n {
        Some(0.0) => "0".into(),
        Some(x) if x < 0.1 => format!("{x:.3}"),
        Some(x) => format!("{x:.2}"),
        None => "–".into(),
    }
}

/// The page's rule (app.js isLocalHost): this computer or a private network.
pub fn is_local_host(h: &str) -> bool {
    let h = h.trim_start_matches('[').trim_end_matches(']');
    let private_172 = h
        .strip_prefix("172.")
        .and_then(|r| r.split('.').next())
        .and_then(|n| n.parse::<u8>().ok());
    h == "localhost"
        || h.starts_with("127.")
        || h.starts_with("10.")
        || h.starts_with("192.168.")
        || private_172.is_some_and(|n| (16..=31).contains(&n))
        || h == "::1"
        || h.ends_with(".local")
        || (!h.is_empty() && !h.contains('.') && !h.contains(':'))
}

/// The host of an http(s) URL ("" when it isn't one).
pub fn host_of(url: &str) -> String {
    let u = url.trim();
    let Some(rest) = u.strip_prefix("https://").or_else(|| u.strip_prefix("http://")) else {
        return String::new();
    };
    let authority = rest.split(['/', '?', '#']).next().unwrap_or("");
    let host = authority.rsplit('@').next().unwrap_or("");
    if let Some(end) = host.find(']') {
        return host[..=end].to_lowercase();
    }
    host.split(':').next().unwrap_or("").to_lowercase()
}

/// "anthropic/claude-x" and "claude-x" (also openai/) are the same model (app.js bareId).
fn bare(id: &str) -> &str {
    id.strip_prefix("anthropic/")
        .or_else(|| id.strip_prefix("openai/"))
        .unwrap_or(id)
}

/// The price fields of a form, as the server wants them (empty: null).
fn prices(f: &Form) -> Result<Value, String> {
    Ok(json!({
        "input_cost_per_mtok": f.number("input")?,
        "output_cost_per_mtok": f.number("output")?,
        "cached_input_cost_per_mtok": f.number("cached")?,
    }))
}

fn price_fields(input: Option<f64>, output: Option<f64>, cached: Option<f64>) -> Vec<Field> {
    let v = |n: Option<f64>| n.map(|x| x.to_string()).unwrap_or_default();
    vec![
        Field::new("input", "Input $/1M", Kind::Number)
            .value(v(input))
            .help("Your price per million tokens; empty: the price list's"),
        Field::new("output", "Output $/1M", Kind::Number).value(v(output)),
        Field::new("cached", "Cached $/1M", Kind::Number).value(v(cached)),
    ]
}

impl Models {
    pub fn load(&mut self, ctx: &Ctx) {
        self.loading = true;
        ctx.call("config", Method::Get, "/api/config", None);
    }

    pub fn has_window(&self) -> bool {
        !matches!(self.view, View::List) || self.table.filtering
    }

    fn all(&self) -> Vec<(Value, bool)> {
        let take = |k: &str, ready: bool| {
            self.config
                .get(k)
                .and_then(Value::as_array)
                .cloned()
                .unwrap_or_default()
                .into_iter()
                .map(move |m| (m, ready))
        };
        take("available", true).chain(take("requires_setup", false)).collect()
    }

    fn model(&self, id: &str) -> Option<(Value, bool)> {
        self.all().into_iter().find(|(m, _)| s(m, "id") == id)
    }

    fn rows(&mut self) {
        let rows = self
            .all()
            .into_iter()
            .map(|(m, ready)| {
                let id = s(&m, "id");
                let key = if s(&m, "env_key").is_empty() {
                    ("none (local)".to_string(), DIM)
                } else if ready {
                    ("✓ set".to_string(), Style::new().fg(Color::Green))
                } else {
                    ("✗ missing".to_string(), Style::new().fg(Color::Yellow))
                };
                let own = num(&m, "input_cost_per_mtok")
                    .or(num(&m, "output_cost_per_mtok"))
                    .is_some();
                let list = m.get("list_prices").filter(|v| !v.is_null());
                let (prices, pstyle) = if own {
                    (
                        format!(
                            "{} / {} yours",
                            price(num(&m, "input_cost_per_mtok")),
                            price(num(&m, "output_cost_per_mtok"))
                        ),
                        Style::new(),
                    )
                } else if let Some(l) = list {
                    (
                        format!("{} / {} list", price(num(l, "input")), price(num(l, "output"))),
                        Style::new(),
                    )
                } else if s(&m, "env_key").is_empty() || is_local_host(&s(&m, "api_host")) {
                    ("local: free".to_string(), DIM)
                } else {
                    ("⚠ unknown".to_string(), Style::new().fg(Color::Yellow))
                };
                let name_style = if s(&m, "problem").is_empty() {
                    Style::new()
                } else {
                    Style::new().fg(Color::Yellow)
                };
                Row::new(id.clone())
                    .styled(s(&m, "name"), name_style)
                    .cell(id)
                    .styled(key.0, key.1)
                    .styled(prices, pstyle)
                    .cell(s(&m, "thinking"))
            })
            .collect();
        self.table.set_rows(rows);
    }

    /// A click chooses a row; a click on the chosen row opens it (as Enter).
    pub fn click_row(&mut self, i: usize, _ctx: &Ctx) -> bool {
        let table = match &mut self.view {
            View::List => &mut self.table,
            View::Providers(t) | View::Catalog { table: t, .. } => t,
            _ => return false,
        };
        table.selected = i.min(table.visible().len().saturating_sub(1));
        false
    }

    pub fn paste(&mut self, text: &str) {
        match &mut self.view {
            View::Form(_, f) => f.paste(text),
            View::Providers(t) | View::Catalog { table: t, .. } if t.filtering => {
                t.filter.push_str(text.lines().next().unwrap_or(""))
            }
            _ => {}
        }
    }

    // ------------------------------------------------------------------ keys

    pub fn key(&mut self, k: KeyEvent, ctx: &Ctx) -> bool {
        let view = std::mem::replace(&mut self.view, View::List);
        let (view, used) = match view {
            // the list's keys may open a form or a view: they set self.view themselves
            View::List => {
                let used = self.list_key(k, ctx);
                (std::mem::replace(&mut self.view, View::List), used)
            }
            View::Form(kind, mut f) => match f.key(k) {
                form::Action::Cancel => (View::List, true),
                form::Action::Submit => {
                    self.submit(&kind, &mut f, ctx);
                    (View::Form(kind, f), true)
                }
                form::Action::None => (View::Form(kind, f), true),
            },
            View::Delete(id, mut c) => match c.key(k) {
                confirm::Answer::Yes => {
                    self.msg = Some((true, format!("Deleting {id}…")));
                    ctx.call(
                        format!("deleted:{id}"),
                        Method::Delete,
                        format!("/api/models/{}", enc(&id)),
                        None,
                    );
                    (View::List, true)
                }
                confirm::Answer::No => (View::List, true),
                confirm::Answer::Waiting => (View::Delete(id, c), true),
            },
            View::Providers(mut t) => {
                if t.key(k) {
                    (View::Providers(t), true)
                } else {
                    match k.code {
                        KeyCode::Esc => (View::List, true),
                        KeyCode::Enter => {
                            let pid = t.chosen().map(|r| r.id.clone());
                            match pid {
                                Some(pid) => {
                                    self.provider_models = Value::Null;
                                    ctx.call(
                                        format!("provider:{pid}"),
                                        Method::Get,
                                        format!("/api/catalog/{}", enc(&pid)),
                                        None,
                                    );
                                    let mut table = Table::new(&[
                                        ("Model ID", 0),
                                        ("In $/1M", 9),
                                        ("Out $/1M", 9),
                                        ("Cached", 8),
                                        ("Context", 9),
                                        ("Tools", 6),
                                        ("", 18),
                                    ]);
                                    table.empty = "Loading…".into();
                                    (View::Catalog { pid, table }, true)
                                }
                                None => (View::Providers(t), true),
                            }
                        }
                        KeyCode::Char('o') => {
                            let on = !self.catalog.get("online").and_then(Value::as_bool).unwrap_or(false);
                            ctx.call("online", Method::Post, "/api/catalog/online", Some(json!({"on": on})));
                            (View::Providers(t), true)
                        }
                        KeyCode::Char('p') => {
                            if self.catalog.get("online").and_then(Value::as_bool) != Some(true) {
                                self.msg = Some((false, "Turn the online catalog on first (o).".into()));
                            } else {
                                self.msg = Some((true, "Downloading the price list…".into()));
                                ctx.call(
                                    "refresh:prices",
                                    Method::Post,
                                    "/api/catalog/refresh",
                                    Some(json!({"what": "prices"})),
                                );
                            }
                            (View::Providers(t), true)
                        }
                        _ => (View::Providers(t), false),
                    }
                }
            }
            View::Catalog { pid, mut table } => {
                if table.key(k) {
                    (View::Catalog { pid, table }, true)
                } else {
                    match k.code {
                        KeyCode::Esc => (View::Providers(self.provider_table()), true),
                        KeyCode::Char('t') => {
                            self.tools_only = !self.tools_only;
                            self.catalog_rows(&mut table);
                            (View::Catalog { pid, table }, true)
                        }
                        KeyCode::Char('h') => {
                            self.hide_retired = !self.hide_retired;
                            self.catalog_rows(&mut table);
                            (View::Catalog { pid, table }, true)
                        }
                        KeyCode::Char('r') => {
                            if self.catalog.get("online").and_then(Value::as_bool) != Some(true) {
                                self.msg = Some((false, "Turn the online catalog on first (Esc, then o).".into()));
                            } else {
                                self.msg = Some((true, "Asking the provider for its models…".into()));
                                ctx.call(
                                    format!("refresh:models:{pid}"),
                                    Method::Post,
                                    "/api/catalog/refresh",
                                    Some(json!({"what": "models", "provider": pid})),
                                );
                            }
                            (View::Catalog { pid, table }, true)
                        }
                        KeyCode::Enter => {
                            let chosen = table.chosen().map(|r| r.id.clone());
                            match chosen.and_then(|id| self.catalog_form(&pid, &id)) {
                                Some((kind, f)) => (View::Form(kind, f), true),
                                None => (View::Catalog { pid, table }, true),
                            }
                        }
                        _ => (View::Catalog { pid, table }, false),
                    }
                }
            }
        };
        self.view = view;
        used
    }

    fn list_key(&mut self, k: KeyEvent, ctx: &Ctx) -> bool {
        if self.table.key(k) {
            return true;
        }
        if k.modifiers.contains(KeyModifiers::CONTROL) {
            return false;
        }
        let chosen = self.table.chosen().map(|r| r.id.clone());
        let model = chosen.as_deref().and_then(|id| self.model(id));
        let before = self.msg.take();
        match (k.code, model) {
            (KeyCode::Char('a'), _) => {
                ctx.call("catalog", Method::Get, "/api/catalog", None);
                self.view = View::Providers(self.provider_table());
            }
            (KeyCode::Char('o'), _) => self.view = View::Form(FormFor::Other, other_form()),
            (KeyCode::Char('r'), _) => self.load(ctx),
            (KeyCode::Char('t'), Some((m, _))) => {
                let id = s(&m, "id");
                self.notices
                    .insert(id.clone(), (true, "Testing: one tiny real call…".into()));
                ctx.call(
                    format!("tested:{id}"),
                    Method::Post,
                    "/api/models/test",
                    Some(json!({"id": id})),
                );
            }
            (KeyCode::Char('e') | KeyCode::Enter, Some((m, _))) => {
                self.view = View::Form(FormFor::Edit(s(&m, "id")), edit_form(&m, false))
            }
            (KeyCode::Char('c'), Some((m, _))) => self.view = View::Form(FormFor::Duplicate, edit_form(&m, true)),
            (KeyCode::Char('k'), Some((m, ready))) => {
                let env = s(&m, "env_key");
                if env.is_empty() {
                    self.msg = Some((
                        false,
                        "This model needs no key (a local model). e edits it to give it one.".into(),
                    ));
                } else {
                    let f = Form::new(
                        format!("{} key for {}", if ready { "Update the" } else { "Add the" }, s(&m, "name")),
                        vec![Field::new("key", env.clone(), Kind::Secret).required().help(
                            "Saved in ~/.optimux/secrets.env on the server; never shown again. Every model using this variable gets it.",
                        )],
                    )
                    .intro(s(&m, "instructions"))
                    .submit_label("Save key");
                    self.view = View::Form(FormFor::Key(env), f);
                }
            }
            (KeyCode::Char('h'), Some((m, _))) => {
                let opts = THINKING.iter().map(|(v, l)| (v.to_string(), l.to_string())).collect();
                let help = match m.get("supports_thinking").and_then(Value::as_bool) {
                    Some(false) => "LiteLLM lists this model without thinking support.",
                    None => "Thinking support unknown for this model.",
                    Some(true) => "Higher levels think longer: better on hard tasks, more tokens.",
                };
                let f = Form::new(
                    format!("Thinking of {}", s(&m, "name")),
                    vec![
                        Field::new("thinking", "Thinking", Kind::Choice(opts))
                            .value(s(&m, "thinking"))
                            .help(help),
                    ],
                );
                self.view = View::Form(FormFor::Thinking(s(&m, "id")), f);
            }
            (KeyCode::Char('x'), Some((m, _))) => {
                let id = s(&m, "id");
                let c = Confirm::new(
                    "Delete this model?",
                    format!("{id}\nIt will be removed from the model list. Its API key stays in secrets.env."),
                    "delete it",
                );
                self.view = View::Delete(id, c);
            }
            _ => {
                self.msg = before;
                return false;
            }
        }
        true
    }

    fn provider_table(&self) -> Table {
        let mut t = Table::new(&[("Provider", 0), ("Key", 12), ("Models", 14)]);
        t.empty = "Loading…".into();
        let rows = self
            .catalog
            .get("providers")
            .and_then(Value::as_array)
            .map(|ps| {
                ps.iter()
                    .map(|p| {
                        let key = if s(p, "env").is_empty() {
                            ("none needed".to_string(), DIM)
                        } else if p.get("key_saved").and_then(Value::as_bool) == Some(true) {
                            ("✓ saved".to_string(), Style::new().fg(Color::Green))
                        } else {
                            ("–".to_string(), DIM)
                        };
                        let refreshed = if p.get("models_refreshed").is_some_and(|v| !v.is_null()) {
                            "your key's list"
                        } else {
                            "built-in list"
                        };
                        Row::new(s(p, "id"))
                            .cell(s(p, "label"))
                            .styled(key.0, key.1)
                            .styled(refreshed, DIM)
                    })
                    .collect()
            })
            .unwrap_or_default();
        t.set_rows(rows);
        t
    }

    fn catalog_rows(&self, t: &mut Table) {
        let configured: Vec<String> = self.all().iter().map(|(m, _)| bare(&s(m, "id")).to_string()).collect();
        let mut models: Vec<Value> = self
            .provider_models
            .get("models")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        models.retain(|m| {
            (!self.tools_only || m.get("tools").and_then(Value::as_bool) != Some(false))
                && (!self.hide_retired || m.get("retired").and_then(Value::as_bool) != Some(true))
        });
        // cheapest first, unpriced last (as the page)
        models.sort_by(|a, b| {
            let pa = num(a, "input").unwrap_or(f64::MAX);
            let pb = num(b, "input").unwrap_or(f64::MAX);
            pa.partial_cmp(&pb).unwrap_or(std::cmp::Ordering::Equal)
        });
        let rows = models
            .iter()
            .map(|m| {
                let id = s(m, "id");
                let (note, style) = if configured.iter().any(|c| c == bare(&id)) {
                    ("added".to_string(), Style::new().fg(Color::Green))
                } else if m.get("retired").and_then(Value::as_bool) == Some(true) {
                    ("retired".to_string(), Style::new().fg(Color::Red))
                } else if !s(m, "deprecated").is_empty() {
                    (format!("until {}", s(m, "deprecated")), Style::new().fg(Color::Yellow))
                } else if m.get("unpriced").and_then(Value::as_bool) == Some(true) {
                    ("no price".to_string(), DIM)
                } else if m.get("zero_listed").and_then(Value::as_bool) == Some(true) {
                    ("free".to_string(), DIM)
                } else {
                    (String::new(), Style::new())
                };
                let ctx_len = num(m, "context").map(|c| {
                    if c >= 1e6 {
                        format!("{:.0}M", c / 1e6)
                    } else {
                        format!("{:.0}k", c / 1e3)
                    }
                });
                let tools = match (
                    m.get("tools").and_then(Value::as_bool),
                    m.get("reasoning").and_then(Value::as_bool),
                ) {
                    (Some(true), Some(true)) => "✓ ◆",
                    (Some(true), _) => "✓",
                    (_, Some(true)) => "◆",
                    _ => "",
                };
                Row::new(id.clone())
                    .cell(id)
                    .cell(price(num(m, "input")))
                    .cell(price(num(m, "output")))
                    .cell(price(num(m, "cached")))
                    .cell(ctx_len.unwrap_or_default())
                    .cell(tools)
                    .styled(note, style)
            })
            .collect();
        t.empty = "No model matches. t: tools only · h: hide retired".into();
        t.set_rows(rows);
    }

    fn catalog_form(&mut self, pid: &str, id: &str) -> Option<(FormFor, Form)> {
        let provider = self.provider_models.get("provider").cloned().unwrap_or(Value::Null);
        let key_saved = self
            .provider_models
            .get("key_saved")
            .and_then(Value::as_bool)
            .unwrap_or(false);
        let env = s(&provider, "env");
        let row = self
            .provider_models
            .get("models")
            .and_then(Value::as_array)?
            .iter()
            .find(|m| s(m, "id") == id)?
            .clone();
        let configured = self.all().iter().any(|(m, _)| bare(&s(m, "id")) == bare(id));
        if configured {
            self.msg = Some((false, format!("{id} is already added.")));
            return None;
        }
        let is_new = row.get("new").and_then(Value::as_bool) == Some(true);
        let needs_key = !env.is_empty() && !key_saved;
        let mut fields = vec![
            Field::new("id", "Model ID", Kind::Text).value(id).locked(),
            Field::new("name", "Display name", Kind::Text)
                .value(id.rsplit('/').next().unwrap_or(id))
                .required(),
        ];
        if !env.is_empty() {
            let mut key = Field::new("key", format!("{} key", s(&provider, "label")), Kind::Secret);
            key = if needs_key {
                key.required()
                    .help(format!("Saved as {env}. Get one at {}", s(&provider, "keys_url")))
            } else {
                key.help(format!("A key is saved ({env}). Type one only to replace it."))
            };
            fields.push(key);
        }
        let p = |k: &str| if is_new { num(&row, k) } else { None };
        fields.extend(price_fields(p("input"), p("output"), None));
        let f = Form::new(format!("Add {id}"), fields)
            .intro(format!(
                "List price: {} in / {} out per million tokens. Leave the prices empty to use the list.",
                price(num(&row, "input")),
                price(num(&row, "output"))
            ))
            .submit_label("Add model");
        Some((
            FormFor::Catalog {
                pid: pid.to_string(),
                env,
                needs_key,
            },
            f,
        ))
    }

    // ------------------------------------------------------------------ sending

    fn submit(&mut self, kind: &FormFor, f: &mut Form, ctx: &Ctx) {
        let price_body = match prices(f) {
            Ok(p) => p,
            Err(e) => {
                f.error = Some(e);
                return;
            }
        };
        let merge = |mut a: Value, b: &Value| {
            if let (Some(a), Some(b)) = (a.as_object_mut(), b.as_object()) {
                for (k, v) in b {
                    a.insert(k.clone(), v.clone());
                }
            }
            a
        };
        let key_step = |env: &str, key: &str| -> Option<(Method, String, Option<Value>)> {
            (!env.is_empty() && !key.trim().is_empty()).then(|| {
                (
                    Method::Post,
                    "/api/keys".to_string(),
                    Some(json!({"env_key": env, "api_key": key.trim()})),
                )
            })
        };
        match kind {
            FormFor::Key(env) => {
                f.busy = true;
                ctx.call(
                    "key",
                    Method::Post,
                    "/api/keys",
                    Some(json!({"env_key": env, "api_key": f.get("key").trim()})),
                );
            }
            FormFor::Thinking(id) => {
                f.busy = true;
                ctx.call(
                    format!("thinking:{id}"),
                    Method::Post,
                    "/api/models/thinking",
                    Some(json!({"id": id, "thinking": f.get("thinking")})),
                );
            }
            FormFor::Edit(_) | FormFor::Duplicate
                if f.get("id").trim().is_empty() || f.get("name").trim().is_empty() =>
            {
                f.error = Some("The model ID and the name are needed.".into());
            }
            FormFor::Edit(old) => {
                let env = f.get("env").trim().to_uppercase();
                let model = merge(
                    json!({"id": f.get("id").trim(), "name": f.get("name").trim(), "env_key": env, "api_base": f.get("base").trim()}),
                    &price_body,
                );
                let mut steps = vec![(
                    Method::Post,
                    "/api/models/update".to_string(),
                    Some(json!({"old_id": old, "model": model})),
                )];
                // the update first (its answer says what followed a new ID), then the key
                steps.extend(key_step(&env, f.get("key")));
                f.busy = true;
                ctx.calls(format!("edited:{}", f.get("id").trim()), steps);
            }
            FormFor::Duplicate => {
                let id = f.get("id").trim().to_string();
                if self.model(&id).is_some() {
                    f.error = Some(format!("{id} is already configured: give the copy another model ID."));
                    return;
                }
                let env = f.get("env").trim().to_uppercase();
                let model = merge(
                    json!({"id": id, "name": f.get("name").trim(), "env_key": env, "api_base": f.get("base").trim(), "thinking": f.get("thinking")}),
                    &price_body,
                );
                let mut steps = vec![(Method::Post, "/api/models".to_string(), Some(model))];
                steps.extend(key_step(&env, f.get("key")));
                f.busy = true;
                ctx.calls(format!("added:{id}"), steps);
            }
            FormFor::Other => {
                let base = f.get("base").trim().to_string();
                let host = host_of(&base);
                if !base.is_empty() && host.is_empty() {
                    f.error = Some("The server address must start with http:// or https://.".into());
                    return;
                }
                if !host.is_empty() && !is_local_host(&host) && !f.on("ack") {
                    f.error = Some(format!("Tick \"I understand\": your code goes to {host}."));
                    return;
                }
                let env = f.get("env").trim().to_uppercase();
                if !env.is_empty()
                    && !env
                        .chars()
                        .all(|c| c.is_ascii_uppercase() || c.is_ascii_digit() || c == '_')
                {
                    f.error = Some("The key variable: letters, digits and _ only, like GROQ_API_KEY.".into());
                    return;
                }
                let id = f.get("id").trim().to_string();
                let model = merge(
                    json!({"id": id, "name": f.get("name").trim(), "env_key": env, "api_base": base}),
                    &price_body,
                );
                let mut steps = vec![(Method::Post, "/api/models".to_string(), Some(model))];
                steps.extend(key_step(&env, f.get("key")));
                f.busy = true;
                ctx.calls(format!("added:{id}"), steps);
            }
            FormFor::Catalog { env, needs_key, .. } => {
                if *needs_key && f.get("key").trim().is_empty() {
                    f.error = Some("Paste the API key: Optimux can't call this provider without it.".into());
                    return;
                }
                let id = f.get("id").to_string();
                let name = if f.get("name").trim().is_empty() {
                    id.clone()
                } else {
                    f.get("name").trim().to_string()
                };
                let model = merge(json!({"id": id, "name": name, "env_key": env}), &price_body);
                // the model first: one the server refuses leaves no key behind
                let mut steps = vec![(Method::Post, "/api/models".to_string(), Some(model))];
                steps.extend(key_step(env, f.get("key")));
                f.busy = true;
                ctx.calls(format!("added:{id}"), steps);
            }
        }
    }

    pub fn on_answer(&mut self, a: Answer, ctx: &Ctx) {
        let (what, arg) = a.tag.split_once(':').map_or((a.tag.as_str(), ""), |(w, x)| (w, x));
        let arg = arg.to_string();
        match (what, a.result) {
            ("config", Ok(v)) => {
                self.loading = false;
                self.config = v;
                self.rows();
                if let View::Catalog { table, .. } = &mut self.view {
                    let mut t = std::mem::take(table);
                    self.catalog_rows(&mut t);
                    if let View::Catalog { table, .. } = &mut self.view {
                        *table = t;
                    }
                }
            }
            ("catalog", Ok(v)) => {
                self.catalog = v;
                if let View::Providers(_) = self.view {
                    self.view = View::Providers(self.provider_table());
                }
            }
            ("provider", Ok(v)) => {
                self.provider_models = v;
                if let View::Catalog { pid, table } = &mut self.view {
                    if *pid == arg {
                        let mut t = std::mem::take(table);
                        self.catalog_rows(&mut t);
                        if let View::Catalog { table, .. } = &mut self.view {
                            *table = t;
                        }
                    }
                }
            }
            ("tested", Ok(v)) => {
                let ok = v.get("ok").and_then(Value::as_bool).unwrap_or(false);
                let text = s(&v, "message");
                self.notices.insert(
                    arg,
                    (
                        ok,
                        if text.is_empty() {
                            if ok { "It works.".into() } else { "It failed.".into() }
                        } else {
                            text
                        },
                    ),
                );
                self.load(ctx);
            }
            ("deleted", Ok(_)) => {
                self.msg = Some((true, format!("{arg} deleted.")));
                self.notices.remove(&arg);
                self.load(ctx);
            }
            ("key", Ok(v)) => {
                let cleared = v
                    .get("cooldowns_cleared")
                    .and_then(Value::as_array)
                    .map_or(0, |c| c.len());
                let extra = if cleared > 0 {
                    format!(" {cleared} paused model(s) can be used again.")
                } else {
                    String::new()
                };
                self.msg = Some((true, format!("Key saved.{extra}")));
                self.view = View::List;
                self.load(ctx);
            }
            ("thinking", Ok(_)) => {
                self.view = View::List;
                self.load(ctx);
                self.msg = Some((true, "Thinking saved.".into()));
            }
            ("edited", Ok(v)) => {
                let mut moved: Vec<String> = v
                    .get("roles")
                    .and_then(Value::as_array)
                    .map(|r| {
                        let roles: Vec<String> = r.iter().filter_map(Value::as_str).map(capital).collect();
                        if roles.is_empty() {
                            vec![]
                        } else {
                            vec![format!("Routing ({})", roles.join(", "))]
                        }
                    })
                    .unwrap_or_default();
                if let Some(ps) = v.get("projects").and_then(Value::as_array) {
                    moved.extend(ps.iter().filter_map(Value::as_str).map(|p| format!("project {p}")));
                }
                let text = if v.get("renamed").and_then(Value::as_bool) == Some(true) && !moved.is_empty() {
                    format!(
                        "Saved as {arg}. {} now {} the new ID.",
                        moved.join(" and "),
                        if moved.len() == 1 { "uses" } else { "use" }
                    )
                } else {
                    "Saved.".to_string()
                };
                self.notices.insert(arg, (true, text));
                self.view = View::List;
                self.load(ctx);
            }
            ("added", Ok(_)) => {
                self.msg = Some((true, format!("{arg} added: assign it in Routing to use it.")));
                self.view = View::List;
                self.load(ctx);
                ctx.call("catalog", Method::Get, "/api/catalog", None);
            }
            ("online", Ok(v)) => {
                let on = v.get("online").and_then(Value::as_bool) == Some(true);
                self.msg = Some((true, format!("Online catalog {}.", if on { "on" } else { "off" })));
                ctx.call("catalog", Method::Get, "/api/catalog", None);
            }
            ("refresh", Ok(v)) => {
                let count = v.get("count").and_then(Value::as_u64).unwrap_or(0);
                self.msg = Some((
                    true,
                    if arg == "prices" {
                        format!("Prices updated: {count} models. Costs in Usage use them from now on.")
                    } else {
                        format!("The provider lists {count} models for your key.")
                    },
                ));
                ctx.call("catalog", Method::Get, "/api/catalog", None);
                if let Some(pid) = arg.strip_prefix("models:") {
                    ctx.call(
                        format!("provider:{pid}"),
                        Method::Get,
                        format!("/api/catalog/{}", enc(pid)),
                        None,
                    );
                } else {
                    self.load(ctx);
                }
            }
            (_, Err(e)) => {
                self.loading = false;
                // an error of a form stays in the form, the others under the list
                match &mut self.view {
                    View::Form(_, f) => {
                        f.busy = false;
                        f.error = Some(e);
                    }
                    _ if what == "tested" => {
                        self.notices.insert(arg, (false, e));
                    }
                    _ => self.msg = Some((false, e)),
                }
            }
            _ => {}
        }
    }

    // ------------------------------------------------------------------ screen

    pub fn keys(&self) -> String {
        match &self.view {
            View::List if self.table.filtering => "type to find · Enter keep · Esc".into(),
            View::List => {
                "t test · e edit (or Enter) · c copy · k key · h thinking · x delete · a add · o other · / find".into()
            }
            View::Form(..) | View::Delete(..) => String::new(),
            View::Providers(_) => {
                "Enter its models · o online catalog on/off · p refresh prices · / find · Esc back".into()
            }
            View::Catalog { .. } => {
                "Enter add · t tools only · h hide retired · r refresh · / find · Esc providers".into()
            }
        }
    }

    /// Draws the section; returns the table's drawn rows (screen row, index) for clicks.
    pub fn draw(&mut self, f: &mut Frame, area: Rect) -> Vec<(u16, usize)> {
        let width = area.width as usize;
        let bold = Style::new().add_modifier(Modifier::BOLD);
        let mut head = vec![Line::from(vec![
            Span::styled("Models", bold),
            Span::styled(
                match &self.view {
                    View::Providers(_) | View::Catalog { .. } => "  ·  add a model from the catalog",
                    _ => "",
                },
                DIM,
            ),
        ])];
        if let Some((ok, m)) = &self.msg {
            let style = if *ok {
                Style::new().fg(Color::Green)
            } else {
                Style::new().fg(Color::Red)
            };
            head.extend(wrap(
                vec![Span::styled(m.clone(), style)],
                width,
                if *ok { "✓ " } else { "✗ " },
                "  ",
            ));
        }
        head.push(Line::default());
        let head_h = head.len() as u16;
        f.render_widget(
            Paragraph::new(head),
            Rect {
                height: head_h.min(area.height),
                ..area
            },
        );
        let body = Rect {
            y: area.y + head_h,
            height: area.height.saturating_sub(head_h),
            ..area
        };
        match &self.view {
            View::List => {
                let table_h = (self.table.visible().len() as u16 + 3)
                    .clamp(4, body.height / 2 + 2)
                    .min(body.height);
                let placed = self.table.draw(
                    f,
                    Rect {
                        height: table_h,
                        ..body
                    },
                );
                let detail = Rect {
                    y: body.y + table_h,
                    height: body.height.saturating_sub(table_h),
                    ..body
                };
                f.render_widget(Paragraph::new(self.detail_lines(width)), detail);
                placed
            }
            View::Form(_, form) => {
                let title = vec![
                    Line::from(Span::styled(form.title.clone(), bold.fg(Color::LightCyan))),
                    Line::default(),
                ];
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
            View::Delete(_, c) => {
                let mut lines = vec![
                    Line::from(Span::styled(c.title.clone(), bold.fg(Color::Red))),
                    Line::default(),
                ];
                lines.extend(c.lines(width));
                f.render_widget(Paragraph::new(lines), body);
                Vec::new()
            }
            View::Providers(t) => {
                let online = self.catalog.get("online").and_then(Value::as_bool) == Some(true);
                let note = format!(
                    "Online catalog: {}. {}",
                    if online { "on" } else { "off" },
                    if online {
                        "Refresh asks the providers and GitHub, only when you press it."
                    } else {
                        "Optimux uses the price list shipped with it; nothing is asked online."
                    }
                );
                let lines = wrap(vec![Span::styled(note, DIM)], width, "", "");
                let n = lines.len() as u16 + 1;
                f.render_widget(Paragraph::new(lines), Rect { height: n, ..body });
                t.draw(
                    f,
                    Rect {
                        y: body.y + n,
                        height: body.height.saturating_sub(n),
                        ..body
                    },
                )
            }
            View::Catalog { pid, table } => {
                let label = s(self.provider_models.get("provider").unwrap_or(&Value::Null), "label");
                let filters = format!(
                    "{} · cheapest first · $ per million tokens · ✓ tools ◆ thinking · {} · {}",
                    if label.is_empty() { pid.clone() } else { label },
                    if self.tools_only { "tools only" } else { "all models" },
                    if self.hide_retired {
                        "retired hidden"
                    } else {
                        "retired shown"
                    }
                );
                f.render_widget(
                    Paragraph::new(Line::from(Span::styled(filters, DIM))),
                    Rect { height: 1, ..body },
                );
                table.draw(
                    f,
                    Rect {
                        y: body.y + 2,
                        height: body.height.saturating_sub(2),
                        ..body
                    },
                )
            }
        }
    }

    fn detail_lines(&self, width: usize) -> Vec<Line<'static>> {
        let mut out = Vec::new();
        if self.loading && self.config.is_null() {
            out.push(Line::from(Span::styled("Loading…", DIM)));
            return out;
        }
        let Some((m, ready)) = self.table.chosen().and_then(|r| self.model(&r.id)) else {
            return out;
        };
        let id = s(&m, "id");
        out.push(Line::from(vec![
            Span::styled(s(&m, "name"), Style::new().add_modifier(Modifier::BOLD)),
            Span::styled(format!("  {id}"), DIM),
        ]));
        let env = s(&m, "env_key");
        let key = if env.is_empty() {
            "Key: none needed (a local model)".to_string()
        } else if ready {
            format!("Key: {env} is set")
        } else {
            format!("Key: {env} is missing: k adds it")
        };
        out.push(Line::from(Span::styled(
            key,
            if ready || env.is_empty() {
                DIM
            } else {
                Style::new().fg(Color::Yellow)
            },
        )));
        if !s(&m, "api_host").is_empty() {
            out.push(Line::from(Span::styled(
                format!("Calls go to {}", s(&m, "api_host")),
                DIM,
            )));
        }
        let thinking = match m.get("supports_thinking").and_then(Value::as_bool) {
            Some(false) => " (LiteLLM lists it without thinking support)",
            None => " (support unknown)",
            _ => "",
        };
        out.push(Line::from(Span::styled(
            format!("Thinking: {}{thinking}", s(&m, "thinking")),
            DIM,
        )));
        if !s(&m, "problem").is_empty() {
            out.extend(wrap(
                vec![Span::styled(s(&m, "problem"), Style::new().fg(Color::Yellow))],
                width,
                "! ",
                "  ",
            ));
        }
        if !ready && !s(&m, "instructions").is_empty() {
            out.extend(wrap(vec![Span::styled(s(&m, "instructions"), DIM)], width, "", ""));
        }
        if let Some((ok, text)) = self.notices.get(&id) {
            let style = if *ok {
                Style::new().fg(Color::Green)
            } else {
                Style::new().fg(Color::Red)
            };
            out.extend(wrap(
                vec![Span::styled(text.clone(), style)],
                width,
                if *ok { "✓ " } else { "✗ " },
                "  ",
            ));
        }
        out
    }
}

impl Section for Models {
    fn load(&mut self, ctx: &Ctx) {
        Models::load(self, ctx)
    }
    fn key(&mut self, k: KeyEvent, ctx: &Ctx) -> bool {
        Models::key(self, k, ctx)
    }
    fn paste(&mut self, text: &str) {
        Models::paste(self, text)
    }
    fn on_answer(&mut self, a: Answer, ctx: &Ctx) {
        Models::on_answer(self, a, ctx)
    }
    fn keys(&self) -> String {
        Models::keys(self)
    }
    fn draw(&mut self, f: &mut Frame, area: Rect) -> Vec<(u16, usize)> {
        Models::draw(self, f, area)
    }
    fn has_window(&self) -> bool {
        Models::has_window(self)
    }
    fn click_row(&mut self, i: usize, ctx: &Ctx) -> bool {
        Models::click_row(self, i, ctx)
    }
}

fn edit_form(m: &Value, duplicate: bool) -> Form {
    let id = s(m, "id");
    let mut fields = vec![
        Field::new("id", "Model ID", Kind::Text)
            .value(id.clone())
            .required()
            .help(if duplicate {
                "The copy needs another model ID (for example another model of the same provider)."
            } else {
                "A new ID keeps Routing and the projects that use this model."
            }),
        Field::new("name", "Name", Kind::Text).value(s(m, "name")).required(),
        Field::new("env", "Key variable", Kind::Text)
            .value(s(m, "env_key"))
            .help("Like GEMINI_API_KEY; empty: none (a local model)"),
        Field::new("base", "Server", Kind::Text)
            .value(s(m, "api_base"))
            .help("https://… to send its calls to a proxy or your own server; empty: the provider"),
    ];
    fields.extend(price_fields(
        num(m, "input_cost_per_mtok"),
        num(m, "output_cost_per_mtok"),
        num(m, "cached_input_cost_per_mtok"),
    ));
    fields.push(Field::new("key", "API key", Kind::Secret).help("Only to change it; empty keeps the saved key"));
    if duplicate {
        let opts = THINKING.iter().map(|(v, l)| (v.to_string(), l.to_string())).collect();
        fields.push(Field::new("thinking", "Thinking", Kind::Choice(opts)).value(s(m, "thinking")));
    }
    let title = if duplicate {
        format!("Duplicate {id}")
    } else {
        format!("Edit {id}")
    };
    Form::new(title, fields).submit_label(if duplicate { "Add the copy" } else { "Save" })
}

fn other_form() -> Form {
    let mut fields = vec![
        Field::new("id", "Model ID", Kind::Text)
            .required()
            .help("Any LiteLLM model ID, e.g. ollama/phi3 or groq/llama-3.3-70b-versatile"),
        Field::new("name", "Display name", Kind::Text).required(),
        Field::new("base", "Server", Kind::Text)
            .help("https://… for a proxy, a reseller or your own server; empty: the provider"),
        Field::new("env", "Key variable", Kind::Text).help("e.g. GROQ_API_KEY; empty for a local model"),
        Field::new("key", "API key", Kind::Secret).help("Saved under the key variable, in secrets.env"),
    ];
    fields.extend(price_fields(None, None, None));
    fields.push(
        Field::new("ack", "I understand", Kind::Toggle)
            .help("Needed when the server is not this computer or your network: your code goes there"),
    );
    Form::new("Add a model by hand", fields).submit_label("Add model")
}

fn capital(s: &str) -> String {
    let mut c = s.chars();
    c.next()
        .map(|f| f.to_uppercase().chain(c).collect())
        .unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn local_hosts_as_the_web_page_sees_them() {
        for h in [
            "localhost",
            "127.0.0.1",
            "10.1.2.3",
            "192.168.1.5",
            "172.16.0.1",
            "172.31.9.9",
            "box.local",
            "ollama",
            "[::1]",
        ] {
            assert!(is_local_host(h), "{h}");
        }
        for h in ["api.groq.com", "172.32.0.1", "8.8.8.8", "openrouter.ai"] {
            assert!(!is_local_host(h), "{h}");
        }
    }

    #[test]
    fn hosts_of_urls() {
        assert_eq!(host_of("https://api.groq.com/openai/v1"), "api.groq.com");
        assert_eq!(host_of("http://127.0.0.1:11434"), "127.0.0.1");
        assert_eq!(host_of("http://user@Host.Example:8080/x"), "host.example");
        assert_eq!(host_of("ftp://x"), "");
        assert_eq!(host_of(""), "");
    }

    #[test]
    fn model_ids_in_paths_and_the_same_model() {
        assert_eq!(enc("gemini/gemini-3.6-flash"), "gemini%2Fgemini-3.6-flash");
        assert_eq!(bare("anthropic/claude-x"), "claude-x");
        assert_eq!(bare("gemini/x"), "gemini/x");
    }

    #[test]
    fn prices_go_as_numbers_or_null() {
        let mut f = Form::new("t", price_fields(Some(1.0), None, None));
        assert_eq!(
            prices(&f).unwrap(),
            json!({"input_cost_per_mtok": 1.0, "output_cost_per_mtok": null, "cached_input_cost_per_mtok": null})
        );
        f.field_mut("output").unwrap().value = "x".into();
        assert!(prices(&f).is_err());
    }
}
