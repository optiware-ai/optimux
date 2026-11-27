//! `optimux-tui keys ...`: API keys, saved in ~/.optimux/secrets.env on the server.
//! A key is never an argument (it would stay in the shell's history and in `ps`): it is
//! typed hidden, or piped in with --stdin. It is never printed.

use std::collections::BTreeMap;

use anyhow::Result;
use clap::Subcommand;
use serde_json::{Value, json};

use super::{Client, confirm, key_var, read_key, s, table};
use crate::api::enc;

#[derive(Subcommand)]
pub enum KeysCmd {
    /// The key variables the models and providers use, and which are saved.
    List,
    /// Save or replace a key (typed hidden; --stdin reads it from standard input).
    Set {
        /// Like GEMINI_API_KEY.
        var: String,
        #[arg(long)]
        stdin: bool,
    },
    /// Remove a key from secrets.env (the models that use it stop working).
    Remove {
        var: String,
        #[arg(long)]
        yes: bool,
    },
}

pub async fn run(c: &Client, cmd: KeysCmd) -> Result<()> {
    match cmd {
        KeysCmd::List => {
            // var → (saved, users)
            let mut vars: BTreeMap<String, (bool, Vec<String>)> = BTreeMap::new();
            let config = c.get("/api/config").await?;
            for (k, ready) in [("available", true), ("requires_setup", false)] {
                for m in config.get(k).and_then(Value::as_array).into_iter().flatten() {
                    let env = s(m, "env_key");
                    if !env.is_empty() {
                        let e = vars.entry(env).or_insert((ready, Vec::new()));
                        e.0 |= ready;
                        e.1.push(s(m, "id"));
                    }
                }
            }
            let catalog = c.get("/api/catalog").await?;
            for p in catalog.get("providers").and_then(Value::as_array).into_iter().flatten() {
                let env = s(p, "env");
                if !env.is_empty() {
                    let saved = p.get("key_saved").and_then(Value::as_bool) == Some(true);
                    let e = vars.entry(env).or_insert((saved, Vec::new()));
                    e.0 |= saved;
                }
            }
            let rows: Vec<Vec<String>> = vars
                .into_iter()
                .map(|(var, (saved, users))| {
                    vec![var, if saved { "saved".into() } else { "–".into() }, users.join(", ")]
                })
                .collect();
            println!("{}", table(&["VARIABLE", "KEY", "USED BY"], &rows));
        }
        KeysCmd::Set { var, stdin } => {
            let var = key_var(&var)?;
            let key = read_key(&format!("{var} (hidden): "), stdin, false)?.unwrap_or_default();
            let v = c.post("/api/keys", json!({"env_key": var, "api_key": key})).await?;
            println!("{var} saved in secrets.env on the server.");
            let cleared = v.get("cooldowns_cleared").and_then(Value::as_array).map_or(0, Vec::len);
            if cleared > 0 {
                println!("{cleared} paused model(s) can be used again.");
            }
        }
        KeysCmd::Remove { var, yes } => {
            let var = key_var(&var)?;
            confirm(
                &format!("Remove {var} from secrets.env? The models that use it stop working."),
                yes,
            )?;
            c.delete(&format!("/api/keys/{}", enc(&var))).await?;
            println!("{var} removed.");
        }
    }
    Ok(())
}
