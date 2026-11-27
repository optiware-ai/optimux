//! `optimux-tui history ...`, `optimux-tui overview` and `optimux-tui system ...`.

use std::io::{IsTerminal, Write};

use anyhow::{Result, bail};
use clap::Subcommand;
use serde_json::{Value, json};

use super::{Client, confirm, num, print_json, read_key, s, table};
use crate::api::enc;
use crate::dash::fmt;
use crate::dash::history::{part_lines, role_name};
use crate::dash::overview::kpis;
use crate::dash::projects::ago;
use crate::dash::system::{LANGUAGES, agent_label};

#[derive(Subcommand)]
pub enum HistoryCmd {
    /// The stored chats, newest first (every project, or --project).
    Chats {
        #[arg(long)]
        project: Option<String>,
        /// The chats with no project.
        #[arg(long)]
        workspace: bool,
        #[arg(long)]
        archived: bool,
        /// In the titles and messages.
        #[arg(long)]
        find: Option<String>,
        #[arg(long)]
        json: bool,
    },
    /// Rename a chat (its id from `history chats --json`).
    Rename { id: String, title: String },
    /// Archive a chat (hidden from the lists, kept).
    Archive { id: String },
    /// Bring an archived chat back.
    Unarchive { id: String },
    /// Delete a chat for good (its cost records are kept); asks for its title.
    Delete {
        id: String,
        /// The chat's title, to confirm without a terminal.
        #[arg(long)]
        confirm: Option<String>,
    },
    /// The requests whose prompts are kept ("What was sent to the models").
    Sent {
        #[arg(long)]
        project: Option<String>,
        /// In the titles, the models and the whole requests.
        #[arg(long)]
        find: Option<String>,
        #[arg(long, default_value_t = 30)]
        limit: u32,
        #[arg(long)]
        json: bool,
    },
    /// One kept request: every call and what it sent (SESSION and KEY from `history sent --json`).
    Request {
        session: String,
        key: String,
        /// Each call's whole prompt, not only what it added.
        #[arg(long)]
        whole: bool,
    },
    /// How many days the sent prompts are kept (0 keeps nothing); a shorter period deletes older ones.
    Keep {
        days: Option<i64>,
        #[arg(long)]
        yes: bool,
    },
}

#[derive(Subcommand)]
pub enum SystemCmd {
    /// Versions, files, security checklist, sign-ins, storage, cooldowns.
    Show {
        #[arg(long)]
        json: bool,
    },
    /// The language of the web page and the server's messages: en or it.
    Language { code: String },
    /// Minutes a request left running by optimux-tui waits for a window (1 to 1440).
    DetachWait { minutes: i64 },
    /// Sign out every other browser and terminal.
    SignoutOthers {
        #[arg(long)]
        yes: bool,
    },
    /// Change the password (asked hidden; every other browser is signed out).
    Password,
    /// Two-step codes: on (asks for your password, shows the key, then a code) or off.
    Totp { state: super::models::OnOff },
    /// Replace the access token (asked hidden); the new one is printed once.
    RotateToken,
    /// Models in cooldown; --clear MODEL or --clear-all.
    Cooldowns {
        #[arg(long)]
        clear: Option<String>,
        #[arg(long)]
        clear_all: bool,
    },
}

pub fn ask_line(prompt: &str) -> Result<String> {
    if !std::io::stdin().is_terminal() {
        bail!("This needs a terminal to ask.");
    }
    print!("{prompt}");
    std::io::stdout().flush()?;
    let mut l = String::new();
    std::io::stdin().read_line(&mut l)?;
    Ok(l.trim().to_string())
}

/// A password or token, typed hidden (never an argument, never from a file).
fn secret(prompt: &str) -> Result<String> {
    if !std::io::stdin().is_terminal() {
        bail!("This needs a terminal to ask (it is typed hidden, never given as an argument).");
    }
    Ok(read_key(prompt, false, false)?.unwrap_or_default())
}

pub async fn run_history(c: &Client, cmd: HistoryCmd) -> Result<()> {
    match cmd {
        HistoryCmd::Chats {
            project,
            workspace,
            archived,
            find,
            json,
        } => {
            let scope = match (project, workspace) {
                (_, true) => "project=".to_string(),
                (Some(p), _) => format!("project={}", enc(&p)),
                _ => "all_projects=true".into(),
            };
            let v = c
                .get(&format!(
                    "/api/sessions?{scope}&archived={archived}&q={}&limit=5000",
                    enc(&find.unwrap_or_default())
                ))
                .await?;
            if json {
                return print_json(&v);
            }
            let rows: Vec<Vec<String>> = v
                .as_array()
                .into_iter()
                .flatten()
                .map(|x| {
                    vec![
                        if s(x, "title").is_empty() {
                            "Untitled chat".into()
                        } else {
                            s(x, "title")
                        },
                        if s(x, "project").is_empty() {
                            "(Workspace)".into()
                        } else {
                            s(x, "project")
                        },
                        num(x, "updated").map(ago).unwrap_or_default(),
                        format!("{:.0}", num(x, "turns").unwrap_or(0.0)),
                        fmt::cost(num(x, "cost").unwrap_or(0.0)),
                        s(x, "id"),
                    ]
                })
                .collect();
            if rows.is_empty() {
                println!("No chats match.");
            } else {
                println!(
                    "{}",
                    table(&["TITLE", "PROJECT", "UPDATED", "TURNS", "COST", "ID"], &rows)
                );
            }
        }
        HistoryCmd::Rename { id, title } => {
            if title.trim().is_empty() {
                bail!("The title can't be empty.");
            }
            c.post(
                &format!("/api/sessions/{}/rename", enc(&id)),
                json!({"title": title.trim()}),
            )
            .await?;
            println!("Renamed.");
        }
        HistoryCmd::Archive { id } => {
            c.post(
                &format!("/api/sessions/{}/archive", enc(&id)),
                json!({"archived": true}),
            )
            .await?;
            println!("Archived (history chats --archived lists it).");
        }
        HistoryCmd::Unarchive { id } => {
            c.post(
                &format!("/api/sessions/{}/archive", enc(&id)),
                json!({"archived": false}),
            )
            .await?;
            println!("Back among the active chats.");
        }
        HistoryCmd::Delete { id, confirm: typed } => {
            let typed = match typed {
                Some(t) => t,
                None => ask_line("Delete this chat permanently? Its messages can't be recovered. Type its title: ")?,
            };
            c.post(&format!("/api/sessions/{}/delete", enc(&id)), json!({"confirm": typed}))
                .await?;
            println!("Deleted. Its cost records are kept.");
        }
        HistoryCmd::Sent {
            project,
            find,
            limit,
            json,
        } => {
            let scope = match project {
                Some(p) => format!("project={}", enc(&p)),
                None => "all_projects=true".into(),
            };
            let v = c
                .get(&format!(
                    "/api/sent/requests?{scope}&q={}&limit={limit}",
                    enc(&find.unwrap_or_default())
                ))
                .await?;
            if json {
                return print_json(&v);
            }
            let rows: Vec<Vec<String>> = v
                .get("requests")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
                .map(|r| {
                    let what = if r.get("reply").and_then(Value::as_bool) == Some(true) {
                        s(r, "prompt")
                    } else {
                        role_name(&s(r, "role"))
                    };
                    vec![
                        what,
                        s(r, "title"),
                        num(r, "ts").map(ago).unwrap_or_default(),
                        format!("{:.0}", num(r, "calls").unwrap_or(0.0)),
                        format!("{} {}", s(r, "session"), s(r, "key")),
                    ]
                })
                .collect();
            println!("{} request(s) kept.\n", num(&v, "count").unwrap_or(0.0));
            println!(
                "{}",
                table(
                    &["REQUEST", "CHAT", "WHEN", "CALLS", "SESSION KEY (for history request)"],
                    &rows
                )
            );
        }
        HistoryCmd::Request { session, key, whole } => {
            let v = c
                .get(&format!(
                    "/api/sent/request?session={}&key={}",
                    enc(&session),
                    enc(&key)
                ))
                .await?;
            let width = 100;
            let print = |lines: Vec<ratatui::text::Line<'static>>| {
                for l in lines {
                    println!(
                        "{}",
                        l.spans.iter().map(|sp| sp.content.to_string()).collect::<String>()
                    );
                }
            };
            if v.get("meta").and_then(Value::as_array).is_some_and(|m| !m.is_empty()) {
                println!("=== Meta-prompt: Optimux's instructions and your memory\n");
                print(part_lines(v.get("meta").unwrap_or(&Value::Null), width));
            }
            for st in v.get("steps").and_then(Value::as_array).cloned().unwrap_or_default() {
                println!(
                    "=== {} · {} · {}\n",
                    role_name(&s(&st, "role")),
                    s(&st, "model"),
                    num(&st, "ts").map(ago).unwrap_or_default()
                );
                if whole {
                    let w = c.get(&format!("/api/sent/{}", s(&st, "id"))).await?;
                    print(part_lines(w.get("parts").unwrap_or(&Value::Null), width));
                } else {
                    print(part_lines(st.get("parts").unwrap_or(&Value::Null), width));
                }
                if !s(&st, "answer").is_empty() {
                    println!("── What it answered\n{}\n", s(&st, "answer"));
                }
            }
        }
        HistoryCmd::Keep { days, yes } => {
            let Some(days) = days else {
                let st = c.get("/api/settings").await?;
                println!(
                    "The sent prompts are kept {} day(s).",
                    st.get("keep_sent_days").map(|v| v.to_string()).unwrap_or_default()
                );
                return Ok(());
            };
            if !(0..=365).contains(&days) {
                bail!("0 to 365 days.");
            }
            let p = c.get(&format!("/api/sent/prune-preview?days={days}")).await?;
            let shorter = days < num(&p, "current").unwrap_or(14.0) as i64 && num(&p, "calls").unwrap_or(0.0) > 0.0;
            if shorter {
                confirm(
                    &format!(
                        "Keeping {days} day(s) deletes {:.0} request(s) ({:.0} call(s)) older than that, for good.",
                        num(&p, "requests").unwrap_or(0.0),
                        num(&p, "calls").unwrap_or(0.0)
                    ),
                    yes,
                )?;
            }
            c.save_settings(json!({"keep_sent_days": days})).await?;
            if shorter {
                let r = c.post("/api/sent/prune", json!({"days": days})).await?;
                println!(
                    "{} older call(s) deleted.",
                    r.get("deleted").map(|d| d.to_string()).unwrap_or_default()
                );
            }
            println!("The sent prompts are kept {days} day(s).");
        }
    }
    Ok(())
}

pub async fn overview(c: &Client, json_out: bool) -> Result<()> {
    let usage = c.get("/api/usage").await?;
    let savings = c.get("/api/savings?period=month").await?;
    let budgets = c.get("/api/budgets").await?;
    let config = c.get("/api/config").await?;
    let routing = c.get("/api/routing").await?;
    let cooldowns = c.get("/api/cooldowns").await?;
    let chats = c.get("/api/sessions?all_projects=true&limit=8").await?;
    if json_out {
        return print_json(
            &json!({"usage": usage, "savings": savings, "budgets": budgets, "models": config, "routing": routing, "cooldowns": cooldowns, "recent_chats": chats}),
        );
    }
    for (label, value, sub) in kpis(&usage, &savings) {
        println!("{:<20}{:<12}{sub}", label, value);
    }
    println!("\nBudgets this month:");
    let mut any = false;
    for (name, b) in budgets.as_object().into_iter().flatten().filter(|(_, b)| !b.is_null()) {
        any = true;
        println!(
            "  {name:<20}{} / {} ({})",
            fmt::cost(num(b, "spent").unwrap_or(0.0)),
            fmt::cost(num(b, "monthly_usd").unwrap_or(0.0)),
            fmt::percent(num(b, "ratio").unwrap_or(0.0))
        );
    }
    if !any {
        println!("  none");
    }
    println!("\nModels:");
    for (k, ready) in [("available", true), ("requires_setup", false)] {
        for m in config.get(k).and_then(Value::as_array).into_iter().flatten() {
            let id = s(m, "id");
            let roles: Vec<&str> = [
                ("conductor_model", "Conductor"),
                ("simple_model", "Simple"),
                ("complex_model", "Complex"),
            ]
            .iter()
            .filter(|(r, _)| s(&routing, r) == id)
            .map(|(_, l)| *l)
            .collect();
            let state = if let Some(cd) = cooldowns.get(&id) {
                format!("cooldown: {}", s(cd, "reason"))
            } else if ready {
                "ready".into()
            } else {
                "needs key".into()
            };
            println!("  {:<24}{:<40}{:<24}{state}", s(m, "name"), id, roles.join(" · "));
        }
    }
    println!("\nRecent chats:");
    for x in chats.as_array().into_iter().flatten() {
        println!(
            "  {:<40}{:<14}{:<14}{}",
            if s(x, "title").is_empty() {
                "Untitled chat".into()
            } else {
                s(x, "title")
            },
            s(x, "project"),
            num(x, "updated").map(ago).unwrap_or_default(),
            fmt::cost(num(x, "cost").unwrap_or(0.0))
        );
    }
    Ok(())
}

pub async fn run_system(c: &Client, cmd: SystemCmd) -> Result<()> {
    match cmd {
        SystemCmd::Show { json } => {
            let info = c.get("/api/system").await?;
            let auth = c.get("/api/auth").await?;
            let sec = c.get("/api/security").await?;
            let cd = c.get("/api/cooldowns").await?;
            let st = c.get("/api/settings").await?;
            if json {
                return print_json(&json!({"system": info, "auth": auth, "security": sec, "cooldowns": cd}));
            }
            println!(
                "Optimux {} · LiteLLM {} · Python {}",
                s(&info, "optimux"),
                s(&info, "litellm"),
                s(&info, "python")
            );
            println!(
                "Language: {} · a request left running waits {} minutes",
                s(&st, "language"),
                st.get("detach_wait_minutes").map(|v| v.to_string()).unwrap_or_default()
            );
            println!("\nSecurity checklist:");
            for x in sec.get("checks").and_then(Value::as_array).into_iter().flatten() {
                let mark = if x.get("ok").and_then(Value::as_bool) == Some(true) {
                    "✓"
                } else if x.get("important").and_then(Value::as_bool) == Some(true) {
                    "!"
                } else {
                    "·"
                };
                println!("  {mark} {:<26}{}", s(x, "label"), s(x, "detail"));
            }
            println!("\nFiles:");
            for (k, p) in info.get("paths").and_then(Value::as_object).into_iter().flatten() {
                println!("  {k:<12}{}", p.as_str().unwrap_or(""));
            }
            for m in info.get("migrations").and_then(Value::as_array).into_iter().flatten() {
                println!("  moved: {}", m.as_str().unwrap_or(""));
            }
            println!(
                "\nSigned in (two-step codes {}):",
                if auth.get("totp").and_then(Value::as_bool) == Some(true) {
                    "on"
                } else {
                    "off"
                }
            );
            for x in auth.get("sessions").and_then(Value::as_array).into_iter().flatten() {
                let me = if x.get("current").and_then(Value::as_bool) == Some(true) {
                    "  (this terminal)"
                } else {
                    ""
                };
                println!(
                    "  {:<16}{:<14}{}{me}",
                    s(x, "ip"),
                    num(x, "last_seen").map(ago).unwrap_or_default(),
                    agent_label(&s(x, "agent"))
                );
            }
            let stg = info.get("storage").cloned().unwrap_or(Value::Null);
            println!(
                "\nDatabase: {} ({:.0} usage rows)",
                s(&stg, "path"),
                num(&stg, "usage_rows").unwrap_or(0.0)
            );
            if !s(&stg, "latest_backup").is_empty() {
                println!(
                    "Latest backup: {} ({}, {:.0} kept)",
                    s(&stg, "latest_backup"),
                    num(&stg, "latest_backup_time").map(ago).unwrap_or_default(),
                    num(&stg, "backups").unwrap_or(0.0)
                );
            }
            println!("\nModels in cooldown:");
            match cd.as_object().filter(|o| !o.is_empty()) {
                Some(o) => {
                    for (k, x) in o {
                        println!("  {k}: {}", s(x, "reason"));
                    }
                }
                None => println!("  none"),
            }
        }
        SystemCmd::Language { code } => {
            if !LANGUAGES.iter().any(|(c, _)| *c == code) {
                bail!(
                    "The language: {}.",
                    LANGUAGES.iter().map(|(c, _)| *c).collect::<Vec<_>>().join(" or ")
                );
            }
            let v = c.post("/api/language", json!({"language": code})).await?;
            println!("Language: {}.", s(&v, "language"));
        }
        SystemCmd::DetachWait { minutes } => {
            if !(1..=1440).contains(&minutes) {
                bail!("1 to 1440 minutes.");
            }
            c.save_settings(json!({"detach_wait_minutes": minutes})).await?;
            println!("A request left running waits {minutes} minutes for a window.");
        }
        SystemCmd::SignoutOthers { yes } => {
            confirm(
                "Sign out every other browser and terminal? This one stays signed in.",
                yes,
            )?;
            let v = c.post("/api/auth/signout-others", json!({})).await?;
            println!(
                "{} other sign-in(s) ended.",
                v.get("ended").map(|e| e.to_string()).unwrap_or_default()
            );
        }
        SystemCmd::Password => {
            let current = secret("Current password: ")?;
            let new = secret("New password (at least 12 characters): ")?;
            if new.chars().count() < 12 {
                bail!("The new password needs at least 12 characters.");
            }
            if secret("New password again: ")? != new {
                bail!("The two new passwords are not the same.");
            }
            let v = c
                .post("/api/auth/password", json!({"current": current, "new": new}))
                .await?;
            println!(
                "Password changed. {} other sign-in(s) ended.",
                v.get("ended").map(|e| e.to_string()).unwrap_or_default()
            );
        }
        SystemCmd::Totp { state } => match state {
            super::models::OnOff::On => {
                let password = secret("Your password: ")?;
                let v = c.post("/api/auth/totp/start", json!({"password": password})).await?;
                println!(
                    "Add Optimux to your authenticator app with this key:\n\n  {}\n\nor this link:\n  {}\n",
                    s(&v, "secret"),
                    s(&v, "uri")
                );
                let code = ask_line("The 6-digit code it shows: ")?;
                c.post("/api/auth/totp/confirm", json!({"code": code})).await?;
                println!("Two-step codes are on.");
            }
            super::models::OnOff::Off => {
                let password = secret("Password: ")?;
                let code = ask_line("Code: ")?;
                c.post("/api/auth/totp/disable", json!({"password": password, "code": code}))
                    .await?;
                println!("Two-step codes are off.");
            }
        },
        SystemCmd::RotateToken => {
            let current = secret("The current access token (hidden): ")?;
            let v = c.post("/api/system/rotate-token", json!({"current": current})).await?;
            println!(
                "New access token (shown once; it's also in secrets.env):\n{}",
                s(&v, "token")
            );
        }
        SystemCmd::Cooldowns { clear, clear_all } => {
            if clear_all || clear.is_some() {
                let body = match clear {
                    Some(m) => json!({"model": m}),
                    None => json!({}),
                };
                let v = c.post("/api/cooldowns/clear", body).await?;
                println!(
                    "{} cooldown(s) cleared.",
                    v.get("cleared").and_then(Value::as_array).map_or(0, Vec::len)
                );
            } else {
                let v = c.get("/api/cooldowns").await?;
                match v.as_object().filter(|o| !o.is_empty()) {
                    Some(o) => {
                        for (k, x) in o {
                            println!("{k}: {}", s(x, "reason"));
                        }
                    }
                    None => println!("No model is in cooldown."),
                }
            }
        }
    }
    Ok(())
}
