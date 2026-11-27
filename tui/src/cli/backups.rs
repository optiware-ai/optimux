//! `optimux-tui backups ...`: snapshots, daily database backups, and restoring
//! (Dashboard → Backups).

use std::path::PathBuf;
use std::time::Duration;

use anyhow::{Result, bail};
use clap::{Subcommand, ValueEnum};
use serde_json::{Value, json};

use super::system::ask_line;
use super::{Client, confirm, num, print_json, read_key, s, table};
use crate::api::enc;
use crate::dash::backups::{age_command, bytes, kind_label, shellish, write_private_new};
use crate::dash::projects::ago;

#[derive(Clone, Copy, ValueEnum)]
pub enum What {
    /// Your data and all your projects (recommended).
    Full,
    /// Settings, models, memory, skills, chats and usage, and the keys.
    Data,
    /// Project folders (all, or --project).
    Workspace,
}

impl What {
    fn id(self) -> &'static str {
        match self {
            What::Full => "full",
            What::Data => "data",
            What::Workspace => "workspace",
        }
    }
}

#[derive(Clone, Copy, ValueEnum, PartialEq, Eq)]
pub enum Protect {
    /// A one-time key, shown once (recommended).
    Key,
    /// Your public key (age1… or ssh-ed25519 …).
    Recipient,
    /// No encryption (workspace snapshots without keys only).
    Plain,
}

#[derive(clap::Args)]
pub struct SaveTo {
    /// The folder to save it in (this computer; an existing file is never replaced).
    #[arg(long, default_value = ".")]
    dir: String,
    #[arg(long, value_enum, default_value_t = Protect::Key)]
    protect: Protect,
    /// For --protect recipient (else the one remembered).
    #[arg(long)]
    recipient: Option<String>,
    /// Remember the public key for next time.
    #[arg(long)]
    remember: bool,
    /// Save the one-time key in this file (readable only by you) instead of printing it.
    #[arg(long)]
    key_file: Option<String>,
}

#[derive(Subcommand)]
pub enum SnapshotsCmd {
    /// The snapshots kept on the server, and the space they use.
    List {
        #[arg(long)]
        json: bool,
    },
    /// What a snapshot would hold; nothing is taken.
    Preview {
        #[arg(long, value_enum, default_value_t = What::Full)]
        what: What,
        /// For --what workspace (repeat it); all projects when left out.
        #[arg(long)]
        project: Vec<String>,
        /// Keep node_modules, .venv and the like too.
        #[arg(long)]
        keep_rebuildable: bool,
    },
    /// Take a snapshot (the preview first, then a question) and wait until it is ready.
    Take {
        #[arg(long, value_enum, default_value_t = What::Full)]
        what: What,
        #[arg(long)]
        project: Vec<String>,
        #[arg(long)]
        keep_rebuildable: bool,
        #[arg(long)]
        note: Option<String>,
        #[arg(long)]
        yes: bool,
        /// Return as soon as it starts.
        #[arg(long)]
        no_wait: bool,
    },
    /// One snapshot: what it holds, its file, its version.
    Show {
        id: String,
        #[arg(long)]
        json: bool,
    },
    /// The README inside a snapshot (contents and how to restore by hand).
    Readme { id: String },
    /// Delete a snapshot from the server.
    Delete {
        id: String,
        #[arg(long)]
        yes: bool,
    },
    /// Restore parts of a snapshot; without --part it lists them (and restores the usual ones).
    Restore {
        id: String,
        /// A part's path, like memory or project:myapp (repeat it).
        #[arg(long)]
        part: Vec<String>,
        /// Only list the parts.
        #[arg(long)]
        list: bool,
        /// The sentence, to confirm without a terminal.
        #[arg(long)]
        confirm: Option<String>,
    },
    /// Download a snapshot, encrypted, to this computer.
    Download {
        id: String,
        #[command(flatten)]
        how: SaveTo,
    },
    /// The space snapshots may use, in GB (no number: shows it).
    Limit { gb: Option<f64> },
}

#[derive(Subcommand)]
pub enum DailyCmd {
    /// The daily copies of the database.
    List {
        #[arg(long)]
        json: bool,
    },
    /// Back up the database now.
    Now,
    /// How many daily backups are kept (no number: shows it).
    Keep { n: Option<i64> },
    /// Open a backup and check it is sound.
    Check { name: String },
    /// Put a backup's database back (merged) when Optimux restarts.
    Restore {
        name: String,
        #[arg(long)]
        confirm: Option<String>,
    },
    /// Download a backup, encrypted, to this computer.
    Download {
        name: String,
        #[command(flatten)]
        how: SaveTo,
    },
    Delete {
        name: String,
        #[arg(long)]
        yes: bool,
    },
}

#[derive(Subcommand)]
pub enum BackupsCmd {
    /// Snapshots: take, list, restore, download.
    Snapshots {
        #[command(subcommand)]
        cmd: SnapshotsCmd,
    },
    /// Daily database backups.
    Daily {
        #[command(subcommand)]
        cmd: DailyCmd,
    },
    /// A restore waiting for the restart, and the last one finished.
    Status {
        #[arg(long)]
        json: bool,
    },
    /// Restart Optimux to finish a restore.
    Restart,
    /// Cancel the restore waiting for the restart.
    Cancel {
        #[arg(long)]
        yes: bool,
    },
    /// Hide the "restore finished" note.
    Dismiss,
    /// Send a downloaded snapshot (.tar.gz.age or .tar.gz) back; it is checked and added to the snapshots.
    Upload {
        file: String,
        /// Read the key from the first line of standard input.
        #[arg(long)]
        stdin: bool,
    },
}

fn body(what: What, projects: Vec<String>, keep: bool, note: Option<String>) -> Value {
    json!({
        "kind": what.id(),
        "projects": if matches!(what, What::Workspace) { projects } else { vec![] },
        "skip_rebuildable": !keep,
        "note": note.unwrap_or_default().trim(),
    })
}

fn print_items(v: &Value) {
    let rows: Vec<Vec<String>> = v
        .get("items")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .map(|i| {
            vec![
                s(i, "path"),
                s(i, "label"),
                format!("{:.0}", num(i, "files").unwrap_or(0.0)),
                bytes(num(i, "bytes").unwrap_or(0.0)),
            ]
        })
        .collect();
    println!("{}", table(&["Part", "What", "Files", "Size"], &rows));
    let left: Vec<&str> = v
        .get("left_out")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .collect();
    if !left.is_empty() {
        println!("Not included: {}.", left.join("; "));
    }
}

fn print_preview(b: &Value, p: &Value) -> bool {
    print_items(p);
    println!("{} before compression.", bytes(num(p, "total_bytes").unwrap_or(0.0)));
    match s(b, "kind").as_str() {
        "data" => println!("⚠ Only part of Optimux: your projects will not be in it (--what full takes everything)."),
        "workspace" => println!(
            "⚠ Only part of Optimux: settings, keys, memory, skills and chats will not be in it (--what full takes everything)."
        ),
        _ => {}
    }
    let fits = p.get("fits").and_then(Value::as_bool) != Some(false);
    if fits {
        println!("✓ Fits: {} free on disk.", bytes(num(p, "free_bytes").unwrap_or(0.0)));
    } else {
        println!("✕ {}", s(p, "reason"));
    }
    fits
}

/// The typed sentence: from --confirm, else asked.
fn phrase(given: Option<String>, wanted: &str) -> Result<String> {
    let typed = match given {
        Some(t) => t,
        None => ask_line(&format!("To confirm, type: {wanted}\n> "))?,
    };
    if typed.trim() != wanted {
        bail!("That isn't the sentence: nothing changed.");
    }
    Ok(typed.trim().to_string())
}

fn after_restore(v: &Value) {
    let list = |k: &str| {
        v.get(k)
            .and_then(Value::as_array)
            .map(|a| a.iter().filter_map(Value::as_str).collect::<Vec<_>>().join(", "))
            .unwrap_or_default()
    };
    if !list("projects").is_empty() {
        println!("Put back now: {}.", list("projects"));
    }
    if !list("archived").is_empty() {
        println!("Moved to Archived projects first: {}.", list("archived"));
    }
    if !s(v, "before_snapshot").is_empty() {
        println!("Your data was saved first in snapshot {}.", s(v, "before_snapshot"));
    }
    if v.get("restart_needed").and_then(Value::as_bool) != Some(false) {
        println!("Restart Optimux to finish: optimux-tui backups restart (or optimux restart where it runs).");
    }
}

async fn download(c: &Client, export_path: String, how: SaveTo, id: String) -> Result<()> {
    let dir = PathBuf::from(shellish(&how.dir));
    if !dir.is_dir() {
        bail!("{} is not a folder here.", dir.display());
    }
    let recipient = how.recipient.clone().unwrap_or_default();
    if how.protect == Protect::Recipient && recipient.contains("PRIVATE KEY") {
        bail!("That is a private key: never paste it anywhere. Use the line of the .pub file.");
    }
    let mode = match how.protect {
        Protect::Key => "key",
        Protect::Recipient => "recipient",
        Protect::Plain => "plain",
    };
    // the key file is written before the download: a name already taken stops it here
    let key_file = how.key_file.as_deref().map(|k| PathBuf::from(shellish(k)));
    if let Some(k) = &key_file {
        if k.exists() {
            bail!("{} already exists: choose another --key-file.", k.display());
        }
    }
    let v = c
        .post(
            &export_path,
            json!({"mode": mode, "recipient": recipient.trim(), "remember": how.remember}),
        )
        .await?;
    let dest = dir.join(s(&v, "filename"));
    let size = c.download(&s(&v, "url"), &dest).await?;
    println!("Saved as {} ({}).", dest.display(), bytes(size as f64));
    let key = s(&v, "key");
    let mut key_name = format!("optimux-key-{id}.txt");
    if !key.is_empty() {
        match key_file {
            Some(k) => {
                write_private_new(&k, &format!("{key}\n"))?;
                println!(
                    "The key is in {} (readable only by you). Keep it apart from the file: without it the file can't be opened.",
                    k.display()
                );
                key_name = k.display().to_string();
            }
            None => {
                eprintln!(
                    "Save this key now, apart from the file (as {key_name}, say): it is shown only once, and without it the file can't be opened."
                );
                println!("{key}");
            }
        }
    }
    if mode != "plain" {
        let recip = if recipient.is_empty() && mode == "recipient" {
            s(&v, "recipient")
        } else {
            recipient
        };
        eprintln!(
            "Open it with: {}",
            age_command(mode, &recip, &key_name, &s(&v, "filename"))
        );
    }
    Ok(())
}

async fn snapshots(c: &Client, cmd: SnapshotsCmd) -> Result<()> {
    match cmd {
        SnapshotsCmd::List { json } => {
            let v = c.get("/api/snapshots").await?;
            if json {
                return print_json(&v);
            }
            let rows: Vec<Vec<String>> = v
                .get("snapshots")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
                .map(|x| {
                    let mut what = kind_label(&s(x, "kind")).to_string();
                    if x.get("auto").and_then(Value::as_bool) == Some(true) {
                        what.push_str(" · auto");
                    }
                    if x.get("includes_secrets").and_then(Value::as_bool) == Some(true) {
                        what.push_str(" · keys");
                    }
                    let size = match s(x, "status").as_str() {
                        "running" => "taking…".into(),
                        "failed" => format!("failed: {}", s(x, "error")),
                        _ => bytes(num(x, "size_bytes").unwrap_or(0.0)),
                    };
                    vec![
                        s(x, "id"),
                        num(x, "created").map(ago).unwrap_or_default(),
                        what,
                        s(x, "note"),
                        size,
                    ]
                })
                .collect();
            if rows.is_empty() {
                println!("No snapshots yet: optimux-tui backups snapshots take");
            } else {
                println!("{}", table(&["Id", "Taken", "What", "Note", "Size"], &rows));
            }
            println!(
                "Using {} of {} · {} free on disk · in {}",
                bytes(num(&v, "used_bytes").unwrap_or(0.0)),
                bytes(num(&v, "limit_bytes").unwrap_or(0.0)),
                bytes(num(&v, "free_bytes").unwrap_or(0.0)),
                s(&v, "dir")
            );
        }
        SnapshotsCmd::Preview {
            what,
            project,
            keep_rebuildable,
        } => {
            let b = body(what, project, keep_rebuildable, None);
            let p = c.post("/api/snapshots/preview", b.clone()).await?;
            print_preview(&b, &p);
        }
        SnapshotsCmd::Take {
            what,
            project,
            keep_rebuildable,
            note,
            yes,
            no_wait,
        } => {
            let mut projects = project;
            if matches!(what, What::Workspace) && projects.is_empty() {
                // the page's default: every project
                projects = c
                    .get("/api/projects")
                    .await?
                    .as_array()
                    .into_iter()
                    .flatten()
                    .filter_map(Value::as_str)
                    .map(str::to_string)
                    .collect();
                if projects.is_empty() {
                    bail!("There are no projects to save.");
                }
            }
            let b = body(what, projects, keep_rebuildable, note);
            let p = c.post("/api/snapshots/preview", b.clone()).await?;
            if !print_preview(&b, &p) {
                bail!("It doesn't fit.");
            }
            confirm("Take it?", yes)?;
            let r = c.post("/api/snapshots", b).await?;
            let id = s(&r, "id");
            if no_wait {
                println!("Taking snapshot {id}…");
                return Ok(());
            }
            eprint!("Taking snapshot {id}…");
            loop {
                tokio::time::sleep(Duration::from_millis(1500)).await;
                let v = c.get("/api/snapshots").await?;
                let x = v
                    .get("snapshots")
                    .and_then(Value::as_array)
                    .and_then(|l| l.iter().find(|x| s(x, "id") == id))
                    .cloned();
                match x.as_ref().map(|x| s(x, "status")).as_deref() {
                    Some("running") => eprint!("."),
                    Some("failed") => bail!(
                        "\nThe snapshot failed: {}",
                        x.map(|x| s(&x, "error")).unwrap_or_default()
                    ),
                    Some(_) => {
                        eprintln!();
                        println!(
                            "Snapshot {id} ready ({}).",
                            bytes(x.as_ref().and_then(|x| num(x, "size_bytes")).unwrap_or(0.0))
                        );
                        return Ok(());
                    }
                    None => bail!("\nThe snapshot {id} is no longer in the list."),
                }
            }
        }
        SnapshotsCmd::Show { id, json } => {
            let v = c.get("/api/snapshots").await?;
            let Some(x) = v
                .get("snapshots")
                .and_then(Value::as_array)
                .and_then(|l| l.iter().find(|x| s(x, "id") == id))
                .cloned()
            else {
                bail!("No snapshot {id}: optimux-tui backups snapshots list");
            };
            if json {
                return print_json(&x);
            }
            println!(
                "{} · {}{}",
                kind_label(&s(&x, "kind")),
                num(&x, "created").map(ago).unwrap_or_default(),
                if s(&x, "note").is_empty() {
                    String::new()
                } else {
                    format!(" · {}", s(&x, "note"))
                }
            );
            if s(&x, "kind") != "data" {
                let p: Vec<&str> = x
                    .get("projects")
                    .and_then(Value::as_array)
                    .into_iter()
                    .flatten()
                    .filter_map(Value::as_str)
                    .collect();
                println!("Projects: {}", if p.is_empty() { "none".into() } else { p.join(", ") });
            }
            print_items(&x);
            println!("File: {}/{}.tar.gz", s(&v, "dir"), id);
            if !s(&x, "optimux_version").is_empty() {
                println!("Taken by Optimux {}", s(&x, "optimux_version"));
            }
        }
        SnapshotsCmd::Readme { id } => {
            let v = c.get(&format!("/api/snapshots/{}/readme", enc(&id))).await?;
            println!("{}", s(&v, "text"));
        }
        SnapshotsCmd::Delete { id, yes } => {
            confirm(&format!("Delete snapshot {id}? This can't be undone."), yes)?;
            c.post(&format!("/api/snapshots/{}/delete", enc(&id)), json!({}))
                .await?;
            println!("Deleted.");
        }
        SnapshotsCmd::Restore {
            id,
            part,
            list,
            confirm: typed,
        } => {
            let path = format!("/api/snapshots/{}/restore", enc(&id));
            let info = c.get(&path).await?;
            let parts = info.get("parts").and_then(Value::as_array).cloned().unwrap_or_default();
            if list || (part.is_empty() && typed.is_none() && !std::io::IsTerminal::is_terminal(&std::io::stdin())) {
                let rows: Vec<Vec<String>> = parts
                    .iter()
                    .map(|p| {
                        let blocked = s(p, "blocked");
                        vec![
                            s(p, "path"),
                            if !blocked.is_empty() {
                                "can't".into()
                            } else if p.get("default").and_then(Value::as_bool) == Some(true) {
                                "usual".into()
                            } else {
                                String::new()
                            },
                            s(p, "label"),
                            if blocked.is_empty() { s(p, "effect") } else { blocked },
                        ]
                    })
                    .collect();
                println!("{}", table(&["Part", "", "What", "Restoring it"], &rows));
                println!("Choose with --part (repeat it); without --part the usual ones are restored.");
                return Ok(());
            }
            let chosen: Vec<String> = if part.is_empty() {
                parts
                    .iter()
                    .filter(|p| p.get("default").and_then(Value::as_bool) == Some(true) && s(p, "blocked").is_empty())
                    .map(|p| s(p, "path"))
                    .collect()
            } else {
                for want in &part {
                    match parts.iter().find(|p| &s(p, "path") == want) {
                        None => bail!("No part {want} in this snapshot: --list shows them."),
                        Some(p) if !s(p, "blocked").is_empty() => bail!("{want}: {}", s(p, "blocked")),
                        _ => {}
                    }
                }
                part
            };
            if chosen.is_empty() {
                bail!("No part to restore: --list shows them.");
            }
            println!("Restores: {}.", chosen.join(", "));
            println!(
                "Your current data is saved first, in a new Data snapshot. Data is put back when Optimux restarts; projects now (an existing one moves to Archived projects first). The database is merged, not rolled back."
            );
            let ok = phrase(typed, &s(&info, "confirm_phrase"))?;
            let r = c.post(&path, json!({"parts": chosen, "confirm": ok})).await?;
            after_restore(&r);
        }
        SnapshotsCmd::Download { id, how } => {
            download(c, format!("/api/snapshots/{}/export", enc(&id)), how, id).await?
        }
        SnapshotsCmd::Limit { gb } => match gb {
            None => {
                let v = c.get("/api/snapshots").await?;
                println!(
                    "{} (using {})",
                    bytes(num(&v, "limit_bytes").unwrap_or(0.0)),
                    bytes(num(&v, "used_bytes").unwrap_or(0.0))
                );
            }
            Some(gb) => {
                if gb < 0.1 {
                    bail!("At least 0.1 GB.");
                }
                c.post("/api/snapshots/limit", json!({"gb": gb})).await?;
                println!(
                    "Space for snapshots: {gb} GB. Automatic snapshots go first when a new one would pass it; yours are never removed without asking."
                );
            }
        },
    }
    Ok(())
}

async fn daily(c: &Client, cmd: DailyCmd) -> Result<()> {
    match cmd {
        DailyCmd::List { json } => {
            let v = c.get("/api/backups").await?;
            if json {
                return print_json(&v);
            }
            let rows: Vec<Vec<String>> = v
                .get("backups")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
                .map(|b| {
                    let check = match b.get("checked").and_then(Value::as_bool) {
                        Some(true) => "✓ checked".into(),
                        Some(false) => format!("✕ damaged: {}", s(b, "error")),
                        None => "not checked".into(),
                    };
                    vec![
                        s(b, "name"),
                        s(b, "date"),
                        num(b, "mtime").map(ago).unwrap_or_default(),
                        check,
                        bytes(num(b, "size_bytes").unwrap_or(0.0)),
                    ]
                })
                .collect();
            if rows.is_empty() {
                println!("No daily backup yet: optimux-tui backups daily now");
            } else {
                println!("{}", table(&["Name", "Day", "Written", "Check", "Size"], &rows));
            }
            println!(
                "The newest {} kept · in {}",
                v.get("keep").map(|k| k.to_string()).unwrap_or_default(),
                s(&v, "dir")
            );
        }
        DailyCmd::Now => {
            let v = c.post("/api/backups", json!({})).await?;
            println!("Backed up: {}", s(&v, "path"));
        }
        DailyCmd::Keep { n } => match n {
            None => println!(
                "{}",
                c.get("/api/backups")
                    .await?
                    .get("keep")
                    .map(|k| k.to_string())
                    .unwrap_or_default()
            ),
            Some(n) => {
                c.post("/api/backups/keep", json!({"keep": n})).await?;
                println!("The newest {n} daily backups are kept.");
            }
        },
        DailyCmd::Check { name } => {
            let v = c.post(&format!("/api/backups/{}/check", enc(&name)), json!({})).await?;
            if v.get("ok").and_then(Value::as_bool) == Some(false)
                || v.get("checked").and_then(Value::as_bool) == Some(false)
            {
                bail!("Damaged: {}", s(&v, "error"));
            }
            println!("Checked: the backup is sound.");
        }
        DailyCmd::Restore { name, confirm: typed } => {
            let st = c.get("/api/restore/status").await?;
            if st.get("pending").is_some_and(|p| !p.is_null()) {
                bail!("Another restore is waiting for the restart: optimux-tui backups restart, or backups cancel.");
            }
            println!(
                "Chats and memory history go back to how they were that day; your current data is saved first, in a new Data snapshot. Merged, not rolled back. Settings, keys, memory files and projects don't change. The database is put back when Optimux restarts."
            );
            let ok = phrase(typed, &s(&st, "confirm_phrase"))?;
            let r = c
                .post(&format!("/api/backups/{}/restore", enc(&name)), json!({"confirm": ok}))
                .await?;
            after_restore(&r);
        }
        DailyCmd::Download { name, how } => {
            if how.protect == Protect::Plain {
                bail!(
                    "A daily backup holds your chats: it can only leave the server encrypted (--protect key or recipient)."
                );
            }
            let id = format!("daily-{name}");
            download(c, format!("/api/backups/{}/export", enc(&name)), how, id).await?
        }
        DailyCmd::Delete { name, yes } => {
            confirm(&format!("Delete the daily backup {name}? This can't be undone."), yes)?;
            c.post(&format!("/api/backups/{}/delete", enc(&name)), json!({}))
                .await?;
            println!("Deleted.");
        }
    }
    Ok(())
}

pub async fn run(c: &Client, cmd: BackupsCmd) -> Result<()> {
    match cmd {
        BackupsCmd::Snapshots { cmd } => snapshots(c, cmd).await?,
        BackupsCmd::Daily { cmd } => daily(c, cmd).await?,
        BackupsCmd::Status { json } => {
            let v = c.get("/api/restore/status").await?;
            if json {
                return print_json(&v);
            }
            match v.get("pending").filter(|p| !p.is_null()) {
                Some(p) => {
                    let parts: Vec<&str> = p
                        .get("parts")
                        .and_then(Value::as_array)
                        .into_iter()
                        .flatten()
                        .filter_map(Value::as_str)
                        .collect();
                    println!(
                        "⚠ A restore waits for the restart: {}{}.",
                        parts.join(", "),
                        if s(p, "source").is_empty() {
                            String::new()
                        } else {
                            format!(", from {}", s(p, "source"))
                        }
                    );
                    println!("  optimux-tui backups restart finishes it, backups cancel cancels it.");
                }
                None => println!("No restore is waiting."),
            }
            if let Some(l) = v.get("last").filter(|l| !l.is_null()) {
                if l.get("ok").and_then(Value::as_bool) == Some(true) {
                    println!(
                        "✓ The last restore finished. The files it replaced are in {}.",
                        s(l, "aside")
                    );
                } else {
                    println!(
                        "✕ The last restore could not be finished: {}. Nothing was changed.",
                        s(l, "error")
                    );
                }
            }
        }
        BackupsCmd::Restart => {
            c.post("/api/restore/restart", json!({})).await?;
            println!("Optimux is restarting.");
        }
        BackupsCmd::Cancel { yes } => {
            confirm("Cancel the restore waiting for the restart?", yes)?;
            let v = c.post("/api/restore/cancel", json!({})).await?;
            println!("Cancelled; the prepared files are in {}.", s(&v, "moved_to"));
        }
        BackupsCmd::Dismiss => {
            c.post("/api/restore/dismiss", json!({})).await?;
        }
        BackupsCmd::Upload { file, stdin } => {
            let path = PathBuf::from(shellish(&file));
            if !path.is_file() {
                bail!("{} is not a file here.", path.display());
            }
            let name = path
                .file_name()
                .map(|n| n.to_string_lossy().to_string())
                .unwrap_or_default();
            eprintln!("Sending {}…", path.display());
            let up = c.upload("/api/restore/upload", &path).await?;
            let key = if up.get("encrypted").and_then(Value::as_bool) == Some(true) {
                read_key("Its key, AGE-SECRET-KEY-1… (hidden): ", stdin, false)?.unwrap_or_default()
            } else {
                String::new()
            };
            eprintln!("Checking every file…");
            let v = c
                .post(
                    "/api/restore/import",
                    json!({"token": s(&up, "token"), "key": key, "filename": name}),
                )
                .await?;
            println!(
                "Added to the snapshots: {}. Restore parts of it with: optimux-tui backups snapshots restore {}",
                s(&v, "id"),
                s(&v, "id")
            );
        }
    }
    Ok(())
}
