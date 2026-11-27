//! Commands for scripts and quick changes, without the screen: `optimux-tui models ...`,
//! `optimux-tui keys ...` and the later sections' commands. They use the same calls as
//! the Dashboard (tests/test_tui_api.py checks them), print plain text (or JSON with
//! `--json`), and never print or take a key as an argument: a key is typed hidden or
//! read from standard input.

pub mod backups;
pub mod budgets;
pub mod config;
pub mod github;
pub mod help;
pub mod keys;
pub mod models;
pub mod projects;
pub mod routing;
pub mod studio;
pub mod system;
pub mod usage;

use std::io::{BufRead, IsTerminal, Write};

use anyhow::{Result, bail};
use serde_json::Value;
use unicode_width::UnicodeWidthStr;

use crate::api::{Api, Method};
use crate::chat::link::Cred;
use crate::server::Server;
use crate::store::Store;

/// Not signed in: main turns this into exit code 3.
#[derive(Debug)]
pub struct NotSignedIn(pub String);

impl std::fmt::Display for NotSignedIn {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.0)
    }
}

impl std::error::Error for NotSignedIn {}

/// A failed check of a command (a test that failed): exit 1, the text already printed.
#[derive(Debug)]
pub struct Failed;

impl std::fmt::Display for Failed {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "failed")
    }
}

impl std::error::Error for Failed {}

pub struct Client {
    api: Api,
    cred: Cred,
}

impl Client {
    /// The access token ($OPTIMUX_TOKEN) or the saved session; no password is asked
    /// here (commands are for scripts: `optimux-tui login` first).
    pub async fn new(server: &Server, saved: &Store) -> Result<Client> {
        let api = Api::new(server)?;
        if let Some(token) = std::env::var("OPTIMUX_TOKEN").ok().filter(|t| !t.is_empty()) {
            return Ok(Client {
                api,
                cred: Cred::Token(token),
            });
        }
        match saved.servers.get(&server.base) {
            Some(entry) if api.check(&entry.cookie).await? => Ok(Client {
                api,
                cred: Cred::Session(entry.cookie.clone()),
            }),
            Some(_) => Err(NotSignedIn(format!(
                "The session with {} ended. Run: optimux-tui login",
                server.base
            ))
            .into()),
            None => Err(NotSignedIn(format!("Not signed in to {}. Run: optimux-tui login", server.base)).into()),
        }
    }

    pub async fn get(&self, path: &str) -> Result<Value> {
        self.api.call(&self.cred.auth(), Method::Get, path, None).await
    }

    pub async fn post(&self, path: &str, body: Value) -> Result<Value> {
        self.api.call(&self.cred.auth(), Method::Post, path, Some(&body)).await
    }

    pub async fn text(&self, path: &str) -> Result<String> {
        self.api.get_text(&self.cred.auth(), path).await
    }

    /// Changes some settings: all are read and sent back with these changed
    /// (POST /api/settings saves every setting).
    pub async fn save_settings(&self, changes: Value) -> Result<Value> {
        let mut all = self.get("/api/settings").await?;
        let (Some(obj), Some(ch)) = (all.as_object_mut(), changes.as_object()) else {
            bail!("The server's settings came back in an unexpected form.");
        };
        for (k, v) in ch {
            obj.insert(k.clone(), v.clone());
        }
        let answer = self.post("/api/settings", all.clone()).await?;
        // the server's note (a new Workspace without the active project) as "_note"
        if let (Some(note), Some(obj)) = (answer.get("note").filter(|n| !n.is_null()), all.as_object_mut()) {
            obj.insert("_note".into(), note.clone());
        }
        Ok(all)
    }

    /// A file from Optimux into `dest` (a new file, readable only by you); its size.
    pub async fn download(&self, path: &str, dest: &std::path::Path) -> Result<u64> {
        self.api.download_to(&self.cred.auth(), path, dest).await
    }

    /// A file on this computer sent to Optimux (up to 2 GB).
    pub async fn upload(&self, path: &str, file: &std::path::Path) -> Result<Value> {
        self.api.upload_file(&self.cred.auth(), path, file).await
    }

    pub async fn delete(&self, path: &str) -> Result<Value> {
        self.api.call(&self.cred.auth(), Method::Delete, path, None).await
    }
}

/// A text field of a JSON answer ("" when missing).
pub fn s(v: &Value, k: &str) -> String {
    match v.get(k) {
        Some(Value::String(t)) => t.clone(),
        Some(Value::Null) | None => String::new(),
        Some(other) => other.to_string(),
    }
}

pub fn num(v: &Value, k: &str) -> Option<f64> {
    v.get(k).and_then(Value::as_f64)
}

pub fn print_json(v: &Value) -> Result<()> {
    println!("{}", serde_json::to_string_pretty(v)?);
    Ok(())
}

/// Columns padded to their widest cell; the last one is not padded.
pub fn table(headers: &[&str], rows: &[Vec<String>]) -> String {
    let n = headers.len();
    let mut widths: Vec<usize> = headers.iter().map(|h| h.width()).collect();
    for r in rows {
        for (i, c) in r.iter().enumerate().take(n) {
            widths[i] = widths[i].max(c.width());
        }
    }
    let line = |cells: Vec<&str>| {
        let mut out = String::new();
        for (i, c) in cells.iter().enumerate() {
            if i + 1 == cells.len() {
                out.push_str(c);
            } else {
                out.push_str(c);
                out.push_str(&" ".repeat(widths[i] - c.width() + 2));
            }
        }
        out.trim_end().to_string()
    };
    let mut out = vec![line(headers.to_vec())];
    out.extend(rows.iter().map(|r| line(r.iter().map(String::as_str).collect())));
    out.join("\n")
}

/// "Are you sure?" before something that can't be undone. `yes`: --yes was given.
/// Without a terminal and without --yes, it stops: a script must say it.
pub fn confirm(question: &str, yes: bool) -> Result<()> {
    if yes {
        return Ok(());
    }
    if !std::io::stdin().is_terminal() {
        bail!("{question} Add --yes to confirm without a terminal.");
    }
    print!("{question} [y/N] ");
    std::io::stdout().flush()?;
    let mut answer = String::new();
    std::io::stdin().read_line(&mut answer)?;
    if matches!(answer.trim(), "y" | "Y" | "yes") {
        Ok(())
    } else {
        bail!("Nothing changed.")
    }
}

/// An API key: from standard input (`--stdin`: the first line), else typed hidden.
/// `optional`: an empty answer is fine (None).
pub fn read_key(prompt: &str, from_stdin: bool, optional: bool) -> Result<Option<String>> {
    let key = if from_stdin {
        let mut line = String::new();
        std::io::stdin().lock().read_line(&mut line)?;
        line.trim().to_string()
    } else if std::io::stdin().is_terminal() {
        rpassword::prompt_password(prompt)?.trim().to_string()
    } else {
        bail!("No terminal to type the key in: pipe it in with --stdin.");
    };
    if key.is_empty() {
        if optional {
            return Ok(None);
        }
        bail!("No key given.");
    }
    Ok(Some(key))
}

/// A key variable as the server wants it: letters, digits and _ (upper-cased).
pub fn key_var(v: &str) -> Result<String> {
    let v = v.trim().to_uppercase();
    if v.is_empty()
        || !v
            .chars()
            .all(|c| c.is_ascii_uppercase() || c.is_ascii_digit() || c == '_')
    {
        bail!("A key variable has letters, digits and _ only, like GROQ_API_KEY.");
    }
    Ok(v)
}

/// A price argument: a number, or "none" to remove it (Some(None)).
pub fn price_arg(v: &Option<String>, what: &str) -> Result<Option<Option<f64>>> {
    match v.as_deref().map(str::trim) {
        None => Ok(None),
        Some("none") | Some("") => Ok(Some(None)),
        Some(t) => match t.replace(',', ".").parse::<f64>() {
            Ok(n) if n >= 0.0 && n.is_finite() => Ok(Some(Some(n))),
            _ => bail!("{what}: a number like 0.75, or none."),
        },
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tables_line_up_with_wide_characters() {
        let t = table(
            &["Name", "Key"],
            &[vec!["Gémini".into(), "✓ set".into()], vec!["X".into(), "–".into()]],
        );
        assert_eq!(t, "Name    Key\nGémini  ✓ set\nX       –");
    }

    #[test]
    fn key_variables_and_prices() {
        assert_eq!(key_var(" groq_api_key ").unwrap(), "GROQ_API_KEY");
        assert!(key_var("my-key").is_err());
        assert_eq!(price_arg(&Some("0,5".into()), "x").unwrap(), Some(Some(0.5)));
        assert_eq!(price_arg(&Some("none".into()), "x").unwrap(), Some(None));
        assert_eq!(price_arg(&None, "x").unwrap(), None);
        assert!(price_arg(&Some("-1".into()), "x").is_err());
    }
}
