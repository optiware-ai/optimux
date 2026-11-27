//! `optimux-tui models ...`: the Models section as commands.

use anyhow::{Result, bail};
use clap::Subcommand;
use serde_json::{Map, Value, json};

use super::{Client, Failed, confirm, num, price_arg, print_json, read_key, s, table};
use crate::api::enc;
use crate::dash::models::{host_of, is_local_host};

pub const THINKING: &[&str] = &["default", "minimal", "low", "medium", "high"];

#[derive(Subcommand)]
pub enum ModelsCmd {
    /// The configured models: key set or missing, prices, thinking.
    List {
        #[arg(long)]
        json: bool,
    },
    /// One model in full.
    Show {
        id: String,
        #[arg(long)]
        json: bool,
    },
    /// One tiny real call to check the model and its key (costs a few tokens).
    Test { id: String },
    /// Add a model: from the catalog (like gemini/gemini-2.5-flash) or any LiteLLM ID.
    Add {
        id: String,
        /// Display name (default: the part after the last /).
        #[arg(long)]
        name: Option<String>,
        /// The key's variable, like GROQ_API_KEY (default: what the server proposes;
        /// "none" for a local model).
        #[arg(long)]
        key_var: Option<String>,
        /// Send this model's calls to this server (a proxy, a reseller, your own).
        #[arg(long = "api-base", value_name = "URL")]
        api_base: Option<String>,
        /// Allow an --api-base that is not this computer or your network: your code goes there.
        #[arg(long)]
        remote_ok: bool,
        /// Your prices, $ per million tokens (default: the price list's).
        #[arg(long)]
        input: Option<String>,
        /// Output tokens, $ per million.
        #[arg(long)]
        output: Option<String>,
        /// Cached input tokens, $ per million.
        #[arg(long)]
        cached: Option<String>,
        /// default, minimal, low, medium, high.
        #[arg(long)]
        thinking: Option<String>,
        /// Read the API key from standard input (else it is asked, hidden, when missing).
        #[arg(long)]
        key_stdin: bool,
    },
    /// Change a configured model. Only what you give changes.
    Edit {
        id: String,
        /// A new model ID: Routing and the projects that use the model follow it.
        #[arg(long = "id", value_name = "NEW_ID")]
        new_id: Option<String>,
        #[arg(long)]
        name: Option<String>,
        /// "none": no key (a local model).
        #[arg(long)]
        key_var: Option<String>,
        /// "none" removes it.
        #[arg(long = "api-base", value_name = "URL")]
        api_base: Option<String>,
        #[arg(long)]
        remote_ok: bool,
        /// "none" removes your price (the list's is used).
        #[arg(long)]
        input: Option<String>,
        #[arg(long)]
        output: Option<String>,
        #[arg(long)]
        cached: Option<String>,
    },
    /// Set the thinking level: default, minimal, low, medium, high.
    Thinking { id: String, level: String },
    /// Remove a model from the list (its key stays in secrets.env).
    Delete {
        id: String,
        /// Don't ask.
        #[arg(long)]
        yes: bool,
    },
    /// The catalog: the providers, or one provider's models (cheapest first).
    Catalog {
        provider: Option<String>,
        /// Also models without tool calls (Optimux needs tools to work on code).
        #[arg(long)]
        all: bool,
        /// Also retired models.
        #[arg(long)]
        retired: bool,
        /// Only models whose ID contains this.
        #[arg(long)]
        find: Option<String>,
        #[arg(long)]
        json: bool,
    },
    /// Turn the online catalog on or off (off: only the price list shipped with Optimux).
    Online { state: OnOff },
    /// Refresh the price list, or a provider's models for your key (online catalog on).
    Refresh {
        what: Refresh,
        /// For `models`: the provider, like gemini.
        provider: Option<String>,
    },
}

#[derive(Clone, Copy, clap::ValueEnum)]
pub enum OnOff {
    On,
    Off,
}

#[derive(Clone, Copy, clap::ValueEnum)]
pub enum Refresh {
    Prices,
    Models,
}

pub fn price(n: Option<f64>) -> String {
    match n {
        Some(0.0) => "0".into(),
        Some(x) if x < 0.1 => format!("{x:.3}"),
        Some(x) => format!("{x:.2}"),
        None => "–".into(),
    }
}

/// Every configured model, with whether its key is set.
async fn all(c: &Client) -> Result<Vec<(Value, bool)>> {
    let v = c.get("/api/config").await?;
    let take = |k: &str, ready: bool| {
        v.get(k)
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default()
            .into_iter()
            .map(move |m| (m, ready))
    };
    Ok(take("available", true).chain(take("requires_setup", false)).collect())
}

async fn one(c: &Client, id: &str) -> Result<(Value, bool)> {
    match all(c).await?.into_iter().find(|(m, _)| s(m, "id") == id) {
        Some(m) => Ok(m),
        None => bail!("{id} is not configured. See: optimux-tui models list"),
    }
}

fn key_state(m: &Value, ready: bool) -> String {
    if s(m, "env_key").is_empty() {
        "none needed".into()
    } else if ready {
        "set".into()
    } else {
        "missing".into()
    }
}

fn prices_text(m: &Value) -> String {
    if num(m, "input_cost_per_mtok")
        .or(num(m, "output_cost_per_mtok"))
        .is_some()
    {
        format!(
            "{} / {} (yours)",
            price(num(m, "input_cost_per_mtok")),
            price(num(m, "output_cost_per_mtok"))
        )
    } else if let Some(l) = m.get("list_prices").filter(|v| !v.is_null()) {
        format!("{} / {} (list)", price(num(l, "input")), price(num(l, "output")))
    } else if s(m, "env_key").is_empty() || is_local_host(&s(m, "api_host")) {
        "local: free".into()
    } else {
        "unknown".into()
    }
}

/// The server the model's calls go to may get the user's code: outside this computer
/// and the local network, it must be said (the web page's "I understand").
fn check_base(base: &str, remote_ok: bool) -> Result<()> {
    let base = base.trim();
    if base.is_empty() || base == "none" {
        return Ok(());
    }
    let host = host_of(base);
    if host.is_empty() {
        bail!("--api-base must start with http:// or https://.");
    }
    if !is_local_host(&host) && !remote_ok {
        bail!("Your code would go to {host}. If that is what you want, add --remote-ok.");
    }
    Ok(())
}

fn set_prices(
    body: &mut Map<String, Value>,
    input: &Option<String>,
    output: &Option<String>,
    cached: &Option<String>,
) -> Result<()> {
    for (field, arg, what) in [
        ("input_cost_per_mtok", input, "--input"),
        ("output_cost_per_mtok", output, "--output"),
        ("cached_input_cost_per_mtok", cached, "--cached"),
    ] {
        if let Some(p) = price_arg(arg, what)? {
            body.insert(field.into(), p.map_or(Value::Null, |n| json!(n)));
        }
    }
    Ok(())
}

pub async fn run(c: &Client, cmd: ModelsCmd) -> Result<()> {
    match cmd {
        ModelsCmd::List { json } => {
            let models = all(c).await?;
            if json {
                let list: Vec<Value> = models
                    .iter()
                    .map(|(m, ready)| {
                        let mut m = m.clone();
                        m["key"] = json!(key_state(&m, *ready));
                        m
                    })
                    .collect();
                return print_json(&json!(list));
            }
            if models.is_empty() {
                println!("No models configured. Add one: optimux-tui models catalog");
                return Ok(());
            }
            let rows: Vec<Vec<String>> = models
                .iter()
                .map(|(m, ready)| {
                    let mut name = s(m, "name");
                    if !s(m, "problem").is_empty() {
                        name.push_str(" (!)");
                    }
                    vec![name, s(m, "id"), key_state(m, *ready), prices_text(m), s(m, "thinking")]
                })
                .collect();
            println!(
                "{}",
                table(&["NAME", "MODEL ID", "KEY", "$/1M IN / OUT", "THINKING"], &rows)
            );
            if models.iter().any(|(m, _)| !s(m, "problem").is_empty()) {
                println!("\n(!) has a problem: optimux-tui models show <id>");
            }
        }
        ModelsCmd::Show { id, json } => {
            let (m, ready) = one(c, &id).await?;
            if json {
                return print_json(&m);
            }
            println!("Name:      {}", s(&m, "name"));
            println!("Model ID:  {}", s(&m, "id"));
            let env = s(&m, "env_key");
            println!(
                "Key:       {}",
                if env.is_empty() {
                    "none needed (a local model)".into()
                } else {
                    format!("{env} ({})", key_state(&m, ready))
                }
            );
            if !s(&m, "api_base").is_empty() {
                println!("Server:    {}", s(&m, "api_base"));
            }
            println!("Prices:    {} $ per million tokens", prices_text(&m));
            if let Some(c) = num(&m, "cached_input_cost_per_mtok") {
                println!("Cached:    {} (yours)", price(Some(c)));
            }
            let support = match m.get("supports_thinking").and_then(Value::as_bool) {
                Some(false) => " (LiteLLM lists it without thinking support)",
                None => " (support unknown)",
                _ => "",
            };
            println!("Thinking:  {}{support}", s(&m, "thinking"));
            if !s(&m, "problem").is_empty() {
                println!("Problem:   {}", s(&m, "problem"));
            }
            if !ready && !s(&m, "instructions").is_empty() {
                println!("\n{}", s(&m, "instructions"));
                println!("Then: optimux-tui keys set {env}");
            }
        }
        ModelsCmd::Test { id } => {
            one(c, &id).await?;
            let v = c.post("/api/models/test", json!({"id": id})).await?;
            if v.get("ok").and_then(Value::as_bool) == Some(true) {
                println!("✓ {}", s(&v, "message"));
            } else {
                println!("✗ {}", s(&v, "message"));
                return Err(Failed.into());
            }
        }
        ModelsCmd::Add {
            id,
            name,
            key_var,
            api_base,
            remote_ok,
            input,
            output,
            cached,
            thinking,
            key_stdin,
        } => {
            let id = id.trim().to_string();
            if all(c).await?.iter().any(|(m, _)| s(m, "id") == id) {
                bail!("{id} is already configured. Change it with: optimux-tui models edit {id}");
            }
            let base = api_base.unwrap_or_default();
            check_base(&base, remote_ok)?;
            if let Some(t) = &thinking {
                if !THINKING.contains(&t.as_str()) {
                    bail!("--thinking: one of {}.", THINKING.join(", "));
                }
            }
            let env = match key_var.as_deref().map(str::trim) {
                Some("none") | Some("") => String::new(),
                Some(v) => super::key_var(v)?,
                None => {
                    let v = c
                        .get(&format!(
                            "/api/models/key-suggestion?id={}&base={}",
                            enc(&id),
                            enc(base.trim())
                        ))
                        .await?;
                    s(&v, "env_key")
                }
            };
            let name = name.unwrap_or_else(|| id.rsplit('/').next().unwrap_or(&id).to_string());
            let mut body = json!({"id": id, "name": name, "env_key": env, "api_base": base.trim()});
            let obj = body.as_object_mut().expect("an object");
            set_prices(obj, &input, &output, &cached)?;
            if let Some(t) = thinking {
                obj.insert("thinking".into(), json!(t));
            }
            // The model first, then its key: a model the server refuses leaves no key behind.
            let piped = if key_stdin {
                if env.is_empty() {
                    bail!("This model has no key variable (--key-var VAR).");
                }
                read_key("", true, false)?
            } else {
                None
            };
            c.post("/api/models", body).await?;
            if let Some(key) = piped {
                c.post("/api/keys", json!({"env_key": env, "api_key": key})).await?;
            }
            println!(
                "Added {id}{}.",
                if env.is_empty() {
                    String::new()
                } else {
                    format!(" (key: {env})")
                }
            );
            if !key_stdin && !env.is_empty() {
                let (m, ready) = one(c, &id).await?;
                if !ready {
                    if std::io::IsTerminal::is_terminal(&std::io::stdin()) {
                        if !s(&m, "instructions").is_empty() {
                            println!("{}", s(&m, "instructions"));
                        }
                        match read_key(&format!("API key for {env} (hidden; Enter to skip): "), false, true)? {
                            Some(key) => {
                                c.post("/api/keys", json!({"env_key": env, "api_key": key})).await?;
                                println!("Key saved in secrets.env on the server.");
                            }
                            None => println!("No key yet. Later: optimux-tui keys set {env}"),
                        }
                    } else {
                        println!("Its key is missing: optimux-tui keys set {env}");
                    }
                }
            }
            println!("To use it, choose it in Routing (Dashboard → Routing).");
        }
        ModelsCmd::Edit {
            id,
            new_id,
            name,
            key_var,
            api_base,
            remote_ok,
            input,
            output,
            cached,
        } => {
            let (m, _) = one(c, &id).await?;
            if let Some(b) = &api_base {
                check_base(b, remote_ok)?;
            }
            let env = match key_var.as_deref().map(str::trim) {
                None => s(&m, "env_key"),
                Some("none") | Some("") => String::new(),
                Some(v) => super::key_var(v)?,
            };
            let mut model = json!({
                "id": new_id.clone().unwrap_or_else(|| id.clone()).trim(),
                "name": name.unwrap_or_else(|| s(&m, "name")),
                "env_key": env,
                "api_base": match api_base.as_deref().map(str::trim) {
                    None => s(&m, "api_base"),
                    Some("none") => String::new(),
                    Some(b) => b.to_string(),
                },
                "input_cost_per_mtok": m.get("input_cost_per_mtok").cloned().unwrap_or(Value::Null),
                "output_cost_per_mtok": m.get("output_cost_per_mtok").cloned().unwrap_or(Value::Null),
                "cached_input_cost_per_mtok": m.get("cached_input_cost_per_mtok").cloned().unwrap_or(Value::Null),
            });
            set_prices(model.as_object_mut().expect("an object"), &input, &output, &cached)?;
            let v = c
                .post("/api/models/update", json!({"old_id": id, "model": model}))
                .await?;
            let new = s(&model, "id");
            println!("Saved {new}.");
            if v.get("renamed").and_then(Value::as_bool) == Some(true) {
                let roles: Vec<String> = v
                    .get("roles")
                    .and_then(Value::as_array)
                    .into_iter()
                    .flatten()
                    .filter_map(Value::as_str)
                    .map(str::to_string)
                    .collect();
                let projects: Vec<String> = v
                    .get("projects")
                    .and_then(Value::as_array)
                    .into_iter()
                    .flatten()
                    .filter_map(Value::as_str)
                    .map(str::to_string)
                    .collect();
                if !roles.is_empty() {
                    println!("Routing uses the new ID for: {}.", roles.join(", "));
                }
                if !projects.is_empty() {
                    println!("These projects use the new ID: {}.", projects.join(", "));
                }
            }
        }
        ModelsCmd::Thinking { id, level } => {
            if !THINKING.contains(&level.as_str()) {
                bail!("The level: one of {}.", THINKING.join(", "));
            }
            let (m, _) = one(c, &id).await?;
            c.post("/api/models/thinking", json!({"id": id, "thinking": level}))
                .await?;
            println!("Thinking of {}: {level}.", s(&m, "name"));
            if m.get("supports_thinking").and_then(Value::as_bool) == Some(false) {
                println!("Note: LiteLLM lists this model without thinking support.");
            }
        }
        ModelsCmd::Delete { id, yes } => {
            one(c, &id).await?;
            confirm(&format!("Delete {id}? Its API key stays in secrets.env."), yes)?;
            c.delete(&format!("/api/models/{}", enc(&id))).await?;
            println!("Deleted {id}.");
        }
        ModelsCmd::Catalog {
            provider,
            all: everything,
            retired,
            find,
            json,
        } => match provider {
            None => {
                let v = c.get("/api/catalog").await?;
                if json {
                    return print_json(&v);
                }
                let online = v.get("online").and_then(Value::as_bool) == Some(true);
                println!(
                    "Online catalog: {}.\n",
                    if online {
                        "on"
                    } else {
                        "off (the price list shipped with Optimux)"
                    }
                );
                let rows: Vec<Vec<String>> = v
                    .get("providers")
                    .and_then(Value::as_array)
                    .into_iter()
                    .flatten()
                    .map(|p| {
                        let key = if s(p, "env").is_empty() {
                            "none needed".to_string()
                        } else if p.get("key_saved").and_then(Value::as_bool) == Some(true) {
                            format!("{} saved", s(p, "env"))
                        } else {
                            format!("{} missing", s(p, "env"))
                        };
                        vec![s(p, "id"), s(p, "label"), key]
                    })
                    .collect();
                println!("{}", table(&["PROVIDER", "NAME", "KEY"], &rows));
                println!("\nIts models: optimux-tui models catalog <provider>");
            }
            Some(pid) => {
                let v = c.get(&format!("/api/catalog/{}", enc(&pid))).await?;
                let configured: Vec<String> = all(c)
                    .await?
                    .iter()
                    .map(|(m, _)| bare(&s(m, "id")).to_string())
                    .collect();
                let mut models: Vec<Value> = v.get("models").and_then(Value::as_array).cloned().unwrap_or_default();
                let find = find.unwrap_or_default().to_lowercase();
                models.retain(|m| {
                    (everything || m.get("tools").and_then(Value::as_bool) != Some(false))
                        && (retired || m.get("retired").and_then(Value::as_bool) != Some(true))
                        && (find.is_empty() || s(m, "id").to_lowercase().contains(&find))
                });
                models.sort_by(|a, b| {
                    let (pa, pb) = (num(a, "input").unwrap_or(f64::MAX), num(b, "input").unwrap_or(f64::MAX));
                    pa.partial_cmp(&pb).unwrap_or(std::cmp::Ordering::Equal)
                });
                if json {
                    return print_json(&json!(models));
                }
                let rows: Vec<Vec<String>> = models
                    .iter()
                    .map(|m| {
                        let id = s(m, "id");
                        let note = if configured.iter().any(|c| c == bare(&id)) {
                            "added".to_string()
                        } else if m.get("retired").and_then(Value::as_bool) == Some(true) {
                            "retired".into()
                        } else if !s(m, "deprecated").is_empty() {
                            format!("until {}", s(m, "deprecated"))
                        } else if m.get("unpriced").and_then(Value::as_bool) == Some(true) {
                            "no price".into()
                        } else {
                            String::new()
                        };
                        let ctx = num(m, "context")
                            .map(|c| {
                                if c >= 1e6 {
                                    format!("{:.0}M", c / 1e6)
                                } else {
                                    format!("{:.0}k", c / 1e3)
                                }
                            })
                            .unwrap_or_default();
                        let thinking = if m.get("reasoning").and_then(Value::as_bool) == Some(true) {
                            "yes"
                        } else {
                            ""
                        };
                        vec![
                            id,
                            price(num(m, "input")),
                            price(num(m, "output")),
                            ctx,
                            thinking.into(),
                            note,
                        ]
                    })
                    .collect();
                if rows.is_empty() {
                    println!("No model matches (--all: also without tools, --retired: also retired).");
                } else {
                    println!(
                        "{}",
                        table(&["MODEL ID", "IN $/1M", "OUT $/1M", "CONTEXT", "THINKING", ""], &rows)
                    );
                    println!("\nAdd one: optimux-tui models add <model id>");
                }
            }
        },
        ModelsCmd::Online { state } => {
            let on = matches!(state, OnOff::On);
            c.post("/api/catalog/online", json!({"on": on})).await?;
            println!(
                "Online catalog {}.",
                if on {
                    "on: refresh asks the providers and GitHub, only when you run it"
                } else {
                    "off"
                }
            );
        }
        ModelsCmd::Refresh { what, provider } => {
            let body = match (what, provider) {
                (Refresh::Prices, _) => json!({"what": "prices"}),
                (Refresh::Models, Some(p)) => json!({"what": "models", "provider": p}),
                (Refresh::Models, None) => bail!("Which provider? Like: optimux-tui models refresh models gemini"),
            };
            let v = c.post("/api/catalog/refresh", body.clone()).await?;
            let count = v.get("count").and_then(Value::as_u64).unwrap_or(0);
            if body["what"] == "prices" {
                println!("Prices updated: {count} models.");
            } else {
                println!("The provider lists {count} models for your key.");
            }
        }
    }
    Ok(())
}

/// "anthropic/claude-x" and "claude-x" are the same model (as the page's bareId).
fn bare(id: &str) -> &str {
    id.strip_prefix("anthropic/")
        .or_else(|| id.strip_prefix("openai/"))
        .unwrap_or(id)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_remote_server_must_be_said() {
        assert!(check_base("", false).is_ok());
        assert!(check_base("http://127.0.0.1:11434", false).is_ok());
        assert!(check_base("https://api.groq.com/openai/v1", false).is_err());
        assert!(check_base("https://api.groq.com/openai/v1", true).is_ok());
        assert!(check_base("api.groq.com", true).is_err(), "no scheme");
    }
}
