//! `optimux-tui studio ...`: the active project's files and its Git pane, as the
//! Studio (Dashboard → Studio).

use std::io::Read;

use anyhow::{Result, bail};
use clap::Subcommand;
use serde_json::{Value, json};

use super::{Client, confirm, num, print_json, s, table};
use crate::api::enc;
use crate::dash::backups::bytes;
use crate::dash::git::change_label;
use crate::dash::studio::suffix_of;

#[derive(Subcommand)]
pub enum StudioCmd {
    /// A folder's files (the project's top folder by default), with git's letters.
    Tree {
        #[arg(default_value = "")]
        path: String,
        #[arg(long)]
        json: bool,
    },
    /// Files and folders whose path has every word.
    Find {
        #[arg(required = true)]
        words: Vec<String>,
    },
    /// A file's text (saved keys show as ••••(NAME)).
    Read { path: String },
    /// Edit a file in your editor; saved only if nobody changed it meanwhile.
    Edit { path: String },
    /// Write standard input into a file (a new one; --replace for one that exists).
    Write {
        path: String,
        #[arg(long)]
        replace: bool,
    },
    /// A new empty file (folders in the name are made).
    New { path: String },
    /// A new folder.
    Mkdir { path: String },
    /// Rename or move a file or folder.
    Mv { from: String, to: String },
    /// Move a file or folder to the Trash (never deleted for good).
    Rm {
        path: String,
        #[arg(long)]
        yes: bool,
    },
    /// Every uncommitted change, and what is added for the next commit.
    Changes {
        #[arg(long)]
        json: bool,
    },
    /// The changed lines (every file, or one).
    Diff {
        path: Option<String>,
        /// Only what is added for the next commit.
        #[arg(long)]
        staged: bool,
    },
    /// Add files to the next commit (git add).
    Add {
        paths: Vec<String>,
        #[arg(long, conflicts_with = "paths")]
        all: bool,
    },
    /// Take files back out of the next commit.
    Unadd {
        paths: Vec<String>,
        #[arg(long, conflicts_with = "paths")]
        all: bool,
    },
    /// Commit here (the added files, or every one); push with `projects push`.
    Commit {
        #[arg(long, short)]
        message: Option<String>,
        /// The Simple model writes the message (one paid call).
        #[arg(long, conflicts_with = "message")]
        write_message: bool,
        /// Commit without asking; this also says you checked any warnings.
        #[arg(long)]
        yes: bool,
    },
}

/// The active project, for the git calls (the Studio works on it).
async fn project(c: &Client) -> Result<String> {
    let v = c.get("/api/files/tree").await?;
    let p = s(&v, "project");
    if p.is_empty() {
        bail!("No active project: optimux-tui projects switch <name>.");
    }
    Ok(p)
}

fn strings(v: &Value, k: &str) -> Vec<String> {
    v.get(k)
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .map(str::to_string)
        .collect()
}

async fn save(c: &Client, path: &str, text: &str, digest: Option<String>, project: &str) -> Result<()> {
    let v = c
        .post(
            "/api/files/save",
            json!({"path": path, "text": text, "digest": digest, "project": project}),
        )
        .await?;
    if v.get("changed").and_then(Value::as_bool) == Some(false) {
        println!("No change.");
    } else {
        println!("Saved {}.", s(&v, "path"));
    }
    Ok(())
}

pub async fn run(c: &Client, cmd: StudioCmd) -> Result<()> {
    match cmd {
        StudioCmd::Tree { path, json } => {
            let v = c.get(&format!("/api/files/tree?path={}", enc(&path))).await?;
            let st = c.get("/api/files/status").await?;
            if json {
                return print_json(&v);
            }
            let letters = st.get("files").and_then(Value::as_object).cloned().unwrap_or_default();
            let rows: Vec<Vec<String>> = v
                .get("entries")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
                .map(|e| {
                    let dir = s(e, "type") == "dir";
                    let l = letters
                        .get(&s(e, "path"))
                        .and_then(Value::as_str)
                        .unwrap_or("")
                        .to_string();
                    let inside = dir && letters.keys().any(|k| k.starts_with(&format!("{}/", s(e, "path"))));
                    vec![
                        if l.is_empty() && inside { "•".into() } else { l },
                        format!("{}{}", s(e, "name"), if dir { "/" } else { "" }),
                        if dir {
                            String::new()
                        } else {
                            bytes(num(e, "size").unwrap_or(0.0))
                        },
                    ]
                })
                .collect();
            println!(
                "{} · {}",
                s(&v, "project"),
                if path.is_empty() { "the top folder".into() } else { path }
            );
            println!("{}", table(&["", "Name", "Size"], &rows));
        }
        StudioCmd::Find { words } => {
            let v = c.get(&format!("/api/files/search?q={}", enc(&words.join(" ")))).await?;
            let found = v.get("results").and_then(Value::as_array).cloned().unwrap_or_default();
            if found.is_empty() {
                bail!("Nothing found.");
            }
            for r in found {
                println!("{}{}", s(&r, "path"), if s(&r, "type") == "dir" { "/" } else { "" });
            }
        }
        StudioCmd::Read { path } => {
            let v = c.get(&format!("/api/files/read?path={}", enc(&path))).await?;
            print!("{}", s(&v, "text"));
            if num(&v, "hidden").unwrap_or(0.0) > 0.0 {
                eprintln!(
                    "({} saved key(s) shown as ••••(NAME))",
                    num(&v, "hidden").unwrap_or(0.0)
                );
            }
        }
        StudioCmd::Edit { path } => {
            let v = c.get(&format!("/api/files/read?path={}", enc(&path))).await?;
            let project = s(&v, "project");
            let edited = tokio::task::spawn_blocking({
                let (text, suffix) = (s(&v, "text"), suffix_of(&path));
                move || crate::ui::external::edit(&text, &suffix)
            })
            .await??;
            match edited {
                None => println!("No change."),
                Some(text) => {
                    if let Err(e) = save(c, &s(&v, "path"), &text, Some(s(&v, "digest")), &project).await {
                        // the edited text is never lost
                        let keep = std::env::temp_dir().join(format!(
                            "optimux-edit-{}{}",
                            std::process::id(),
                            suffix_of(&path)
                        ));
                        crate::dash::backups::write_private_new(&keep, &text)?;
                        bail!("{e}\nYour text is kept in {} (readable only by you).", keep.display());
                    }
                }
            }
        }
        StudioCmd::Write { path, replace } => {
            let mut text = String::new();
            std::io::stdin().read_to_string(&mut text)?;
            let project = project(c).await?;
            let digest = if replace {
                Some(s(
                    &c.get(&format!("/api/files/read?path={}", enc(&path))).await?,
                    "digest",
                ))
            } else {
                None
            };
            save(c, &path, &text, digest, &project).await?;
        }
        StudioCmd::New { path } => {
            let project = project(c).await?;
            save(c, &path, "", None, &project).await?;
        }
        StudioCmd::Mkdir { path } => {
            let project = project(c).await?;
            let v = c
                .post("/api/files/folder", json!({"path": path, "project": project}))
                .await?;
            println!("Made the folder {}.", s(&v, "path"));
        }
        StudioCmd::Mv { from, to } => {
            let project = project(c).await?;
            let v = c
                .post("/api/files/rename", json!({"path": from, "to": to, "project": project}))
                .await?;
            println!("Renamed: now {}.", s(&v, "to"));
        }
        StudioCmd::Rm { path, yes } => {
            let v = c.get(&format!("/api/files/delete?path={}", enc(&path))).await?;
            let where_ = if s(&v, "trash") == "home" {
                "the Trash"
            } else {
                "the Workspace's .trash folder"
            };
            let what = if s(&v, "kind") != "file" {
                format!(
                    "the folder {path} ({} file(s), {})",
                    num(&v, "files").unwrap_or(0.0),
                    bytes(num(&v, "size").unwrap_or(0.0))
                )
            } else {
                format!("{path} ({})", bytes(num(&v, "size").unwrap_or(0.0)))
            };
            confirm(&format!("Move {what} to {where_}?"), yes)?;
            c.post("/api/files/delete", json!({"path": path, "project": s(&v, "project")}))
                .await?;
            println!("Moved {path} to {where_}.");
        }
        StudioCmd::Changes { json } => {
            let p = project(c).await?;
            let v = c.get(&format!("/api/projects/{}/git-state", enc(&p))).await?;
            if json {
                return print_json(&v);
            }
            let rows: Vec<Vec<String>> = v
                .get("files")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
                .map(|f| {
                    let staged = f.get("staged").and_then(Value::as_bool) == Some(true);
                    let again = f.get("again").and_then(Value::as_bool) == Some(true);
                    vec![
                        format!("{}{}", if staged { "[✓]" } else { "[ ]" }, if again { "↻" } else { "" }),
                        s(f, "path"),
                        change_label(f),
                    ]
                })
                .collect();
            println!(
                "{} · {} · ↑{} to push · ↓{} to pull",
                p,
                s(&v, "branch"),
                num(&v, "ahead").unwrap_or(0.0),
                num(&v, "behind").unwrap_or(0.0)
            );
            if rows.is_empty() {
                println!("Nothing to commit.");
            } else {
                println!("{}", table(&["", "File", "Change"], &rows));
                let staged = v.get("staged").and_then(Value::as_u64).unwrap_or(0);
                if staged > 0 {
                    println!("{staged} of {} added: commit takes only these.", rows.len());
                }
            }
            for x in strings(&v, "problems") {
                println!("⛔ {x}");
            }
            for x in strings(&v, "warnings") {
                println!("⚠ {x}");
            }
        }
        StudioCmd::Diff { path, staged } => {
            let p = project(c).await?;
            let q = path.as_deref().map(|x| format!("&path={}", enc(x))).unwrap_or_default();
            let v = c
                .get(&format!("/api/projects/{}/diff?staged={staged}{q}", enc(&p)))
                .await?;
            for f in v.get("files").and_then(Value::as_array).into_iter().flatten() {
                println!(
                    "=== {} ({}, +{} −{})",
                    s(f, "path"),
                    s(f, "status"),
                    num(f, "added").unwrap_or(0.0),
                    num(f, "removed").unwrap_or(0.0)
                );
                if f.get("binary").and_then(Value::as_bool) == Some(true) {
                    println!("(a binary file)");
                    continue;
                }
                for l in f.get("lines").and_then(Value::as_array).into_iter().flatten() {
                    let k = s(l, "k");
                    if k == "@" {
                        println!("{}", s(l, "t"));
                    } else {
                        println!("{}{}", if k.is_empty() { " " } else { &k }, s(l, "t"));
                    }
                }
                if num(f, "more").unwrap_or(0.0) > 0.0 {
                    println!("… {} more line(s)", num(f, "more").unwrap_or(0.0));
                }
            }
        }
        StudioCmd::Add { paths, all } | StudioCmd::Unadd { paths, all } if !all && paths.is_empty() => {
            bail!("Name the files, or --all.")
        }
        StudioCmd::Add { paths, .. } => {
            let p = project(c).await?;
            let v = c
                .post(
                    &format!("/api/projects/{}/stage", enc(&p)),
                    json!({"paths": paths, "staged": true}),
                )
                .await?;
            println!(
                "Added: {} file(s) for the next commit.",
                v.get("staged").and_then(Value::as_u64).unwrap_or(0)
            );
        }
        StudioCmd::Unadd { paths, .. } => {
            let p = project(c).await?;
            let v = c
                .post(
                    &format!("/api/projects/{}/stage", enc(&p)),
                    json!({"paths": paths, "staged": false}),
                )
                .await?;
            println!(
                "Now {} file(s) added for the next commit.",
                v.get("staged").and_then(Value::as_u64).unwrap_or(0)
            );
        }
        StudioCmd::Commit {
            message,
            write_message,
            yes,
        } => {
            let p = project(c).await?;
            let v = c.get(&format!("/api/projects/{}/git-state", enc(&p))).await?;
            let files = v.get("files").and_then(Value::as_array).cloned().unwrap_or_default();
            if files.is_empty() {
                bail!("Nothing to commit.");
            }
            let staged = v.get("staged").and_then(Value::as_u64).unwrap_or(0);
            let takes: Vec<Value> = if staged > 0 {
                files
                    .into_iter()
                    .filter(|f| f.get("staged").and_then(Value::as_bool) == Some(true))
                    .collect()
            } else {
                files
            };
            println!("Commits here (push with: optimux-tui projects push {p}):");
            for f in &takes {
                println!("  {:<50} {}", s(f, "path"), change_label(f));
            }
            let problems = strings(&v, "problems");
            if !problems.is_empty() {
                for x in problems {
                    eprintln!("⛔ {x}");
                }
                bail!("It can't be committed.");
            }
            let warnings = strings(&v, "warnings");
            for x in &warnings {
                println!("⚠ {x}");
            }
            let message = if write_message {
                println!("Writing the message (one call to the Simple model)…");
                s(
                    &c.post(
                        &format!("/api/projects/{}/commit-message", enc(&p)),
                        json!({"staged": staged > 0}),
                    )
                    .await?,
                    "message",
                )
            } else {
                message.unwrap_or_default()
            };
            if message.trim().is_empty() {
                bail!("A commit message is needed: --message \"…\" or --write-message.");
            }
            println!("Message:\n  {}", message.trim().replace('\n', "\n  "));
            confirm(
                if warnings.is_empty() {
                    "Commit?"
                } else {
                    "I checked the warnings. Commit?"
                },
                yes,
            )?;
            let r = c
                .post(
                    &format!("/api/projects/{}/commit", enc(&p)),
                    json!({"message": message.trim(), "tree": v.get("tree"), "confirmed": true, "staged_tree": v.get("staged_tree")}),
                )
                .await?;
            println!("Committed {}.", s(&r, "commit"));
        }
    }
    Ok(())
}
