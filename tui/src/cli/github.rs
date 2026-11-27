//! `optimux-tui github ...`: the token New project and Push use, and who commits
//! (Dashboard → GitHub).

use anyhow::Result;
use clap::Subcommand;
use serde_json::{Value, json};

use super::{Client, confirm, print_json, read_key, s};
use crate::dash::github::TOKEN_STEPS;

#[derive(Subcommand)]
pub enum GitHubCmd {
    /// The GitHub CLI, the sign-in and who commits.
    Show {
        /// Ask GitHub again now (otherwise a recent answer is reused).
        #[arg(long)]
        fresh: bool,
        #[arg(long)]
        json: bool,
    },
    /// Add or replace the token: typed hidden (or the first line of --stdin), checked with GitHub, then saved.
    Token {
        #[arg(long)]
        stdin: bool,
    },
    /// Ask GitHub whether the saved token still works.
    Check,
    /// Remove the saved token from secrets.env.
    Remove {
        #[arg(long)]
        yes: bool,
    },
    /// Who commits: git's user.name and user.email (both, or neither to see them).
    Identity {
        #[arg(long)]
        name: Option<String>,
        #[arg(long)]
        email: Option<String>,
    },
}

fn print_status(v: &Value) {
    let mark = |ok: bool| if ok { "✓" } else { "✗" };
    let gh = v.get("gh").and_then(Value::as_bool) == Some(true);
    println!(
        "{} GitHub CLI  {}",
        mark(gh),
        if gh {
            s(v, "gh_version")
        } else {
            "not installed (only needed to sign in with gh auth login)".into()
        }
    );
    let ready = v.get("ready").and_then(Value::as_bool) == Some(true);
    let sign_in = if v.get("token_saved").and_then(Value::as_bool) == Some(true) {
        format!(
            "token saved in Optimux{}",
            if s(v, "login").is_empty() {
                String::new()
            } else {
                format!(" · @{}", s(v, "login"))
            }
        )
    } else if !s(v, "login").is_empty() {
        format!("{} · @{}", s(v, "source"), s(v, "login"))
    } else {
        "not set up yet: optimux-tui github token".into()
    };
    println!("{} Sign-in     {sign_in}", mark(ready));
    if !s(v, "reason").is_empty() {
        println!("              {}", s(v, "reason"));
    }
    let who = if s(v, "git_name").is_empty() && s(v, "git_email").is_empty() {
        "not set: optimux-tui github identity --name … --email …".to_string()
    } else {
        format!("{} <{}>", s(v, "git_name"), s(v, "git_email"))
    };
    println!("  Who commits {who}");
    if v.get("sandbox").and_then(Value::as_bool) == Some(false) {
        println!(
            "⚠ The sandbox is off: an approved command runs with Optimux's permissions, so it could read secrets.env or use gh's sign-in."
        );
    }
}

pub async fn run(c: &Client, cmd: GitHubCmd) -> Result<()> {
    match cmd {
        GitHubCmd::Show { fresh, json } => {
            let v = c
                .get(if fresh { "/api/github?fresh=true" } else { "/api/github" })
                .await?;
            if json {
                return print_json(&v);
            }
            print_status(&v);
        }
        GitHubCmd::Token { stdin } => {
            if !stdin {
                println!("A token GitHub accepts:");
                for (n, step) in TOKEN_STEPS.iter().enumerate() {
                    println!("  {}. {}", n + 1, step.replace(" (t)", ""));
                }
            }
            let token = read_key("GitHub token (hidden): ", stdin, false)?.unwrap_or_default();
            let v = c.post("/api/github", json!({"token": token})).await?;
            println!(
                "Token saved{}.",
                if s(&v, "login").is_empty() {
                    String::new()
                } else {
                    format!(": @{}", s(&v, "login"))
                }
            );
            if !s(&v, "warning").is_empty() {
                println!("⚠ {}", s(&v, "warning"));
            }
        }
        GitHubCmd::Check => {
            // a token GitHub refuses comes back as an error (exit 1)
            let v = c.post("/api/github/check", json!({})).await?;
            println!(
                "The token works: @{}{}.",
                s(&v, "login"),
                if s(&v, "name").is_empty() {
                    String::new()
                } else {
                    format!(" ({})", s(&v, "name"))
                }
            );
            for k in ["warning", "hint"] {
                if !s(&v, k).is_empty() {
                    println!("⚠ {}", s(&v, k));
                }
            }
        }
        GitHubCmd::Remove { yes } => {
            confirm("Remove the GitHub token from secrets.env?", yes)?;
            c.delete("/api/github").await?;
            println!(
                "Token removed. New projects can't create GitHub repositories until you add one (or use gh auth login)."
            );
        }
        GitHubCmd::Identity { name, email } => match (name, email) {
            (None, None) => {
                let v = c.get("/api/github").await?;
                println!("{} <{}>", s(&v, "git_name"), s(&v, "git_email"));
            }
            (Some(n), Some(e)) => {
                c.post(
                    "/api/github/identity",
                    json!({"git_name": n.trim(), "git_email": e.trim()}),
                )
                .await?;
                println!("Saved: {} <{}> commits.", n.trim(), e.trim());
            }
            _ => anyhow::bail!("Give both --name and --email."),
        },
    }
    Ok(())
}
