//! `optimux-tui projects ...`, `optimux-tui project ...` and `optimux-tui scope ...`:
//! the Projects, Project and Scope sections as commands.

use std::io::{IsTerminal, Write};
use std::time::Duration;

use anyhow::{Result, bail};
use clap::Subcommand;
use serde_json::{Map, Value, json};

use super::{Client, confirm, num, print_json, s, table};
use crate::api::enc;
use crate::dash::fmt;
use crate::dash::project::{CHOICES, LEVELS, MODELS, NUMBERS, parsed, shown};
use crate::dash::projects::{KINDS, ago, free_name, name_problem, phrase_ok};

#[derive(Subcommand)]
pub enum ProjectsCmd {
    /// Commit the changes and push them (what goes, then a question; nothing runs without it).
    Push {
        name: String,
        /// The commit message (else the project's default; --write-message has the Simple model write it).
        #[arg(long, short)]
        message: Option<String>,
        /// The Simple model writes the message (one paid call).
        #[arg(long, conflicts_with = "message")]
        write_message: bool,
        /// Push only the commits already made; the uncommitted files stay.
        #[arg(long)]
        commits_only: bool,
        /// Push without asking; this also says you checked any warnings.
        #[arg(long)]
        yes: bool,
    },
    /// Bring in GitHub's new commits (what comes, then a question).
    Update {
        name: String,
        #[arg(long)]
        yes: bool,
    },
    /// git fetch, git status and the last commits. No files change.
    Status { name: String },
    /// Who commits and who talks to the remote.
    Identity { name: String },
    /// The lines to push from your own terminal and get the project elsewhere, each whole.
    GitLines {
        name: String,
        /// The SSH address for getting it elsewhere, when there is one.
        #[arg(long)]
        ssh: bool,
    },
    /// Every project: costs, chats, budget, last activity.
    List {
        /// Also the archived and the deleted projects.
        #[arg(long)]
        all: bool,
        #[arg(long)]
        json: bool,
    },
    /// Create a project in the Workspace (no model involved).
    New {
        name: String,
        /// basic, python, web, node, java, c, cpp, rust, go, firmware.
        #[arg(long, default_value = "basic")]
        kind: String,
        /// No git repository.
        #[arg(long)]
        no_git: bool,
        /// Also a GitHub repository: private or public.
        #[arg(long)]
        github: Option<String>,
        #[arg(long, default_value = "")]
        description: String,
        /// Make it the active project.
        #[arg(long)]
        switch: bool,
    },
    /// Make a project the active one ("" or --workspace: the Workspace itself).
    Switch {
        name: Option<String>,
        #[arg(long)]
        workspace: bool,
    },
    /// Where a project's files are (folder, memory, database).
    Files { name: String },
    /// Move a project's folder to the Workspace's .archive (chats and costs are kept).
    Archive {
        name: String,
        #[arg(long)]
        yes: bool,
    },
    /// The archived projects.
    Archived {
        #[arg(long)]
        json: bool,
    },
    /// Move an archived project back (its folder name under .archive, or its name).
    Restore { folder: String },
    /// Delete an archived project's folder for good. GitHub repositories are never touched.
    Delete {
        folder: String,
        /// The sentence "yes I want to delete <name>" (asked when not given).
        #[arg(long)]
        confirm: Option<String>,
        /// Also forget its chats, memory, costs and statistics (default: kept).
        #[arg(long)]
        forget: bool,
    },
    /// Your GitHub repositories (a token in Dashboard → GitHub is needed).
    Repos {
        #[arg(long)]
        find: Option<String>,
    },
    /// Clone a GitHub repository into the Workspace, like owner/repo.
    Clone {
        full_name: String,
        /// The folder name (default: the repository's, made free).
        #[arg(long)]
        name: Option<String>,
        #[arg(long)]
        switch: bool,
    },
}

#[derive(Subcommand)]
pub enum ProjectCmd {
    /// A project's settings: its own values and the global ones they follow.
    Show {
        name: String,
        #[arg(long)]
        json: bool,
    },
    /// Change a project's settings: key=value, or key=global to follow the global value.
    /// Keys: autonomy, budget, on_limit, conductor_model, simple_model, complex_model,
    /// plan_mode, scout, memory_curator, sandbox_network, curator_idle_minutes,
    /// max_steps_simple, max_steps_complex, command_timeout.
    Set {
        name: String,
        #[arg(required = true)]
        changes: Vec<String>,
    },
}

#[derive(Subcommand)]
pub enum ScopeCmd {
    /// The Workspace folder.
    Show,
    /// Change the Workspace folder (an absolute path).
    Set { path: String },
}

async fn archived(c: &Client) -> Result<Vec<Value>> {
    Ok(c.get("/api/projects/archived")
        .await?
        .as_array()
        .cloned()
        .unwrap_or_default())
}

/// An archived project by its .archive folder or its name (the newest).
async fn find_archived(c: &Client, key: &str) -> Result<Value> {
    let all = archived(c).await?;
    if let Some(a) = all.iter().find(|a| s(a, "folder") == key) {
        return Ok(a.clone());
    }
    let mut named: Vec<&Value> = all.iter().filter(|a| s(a, "name") == key).collect();
    named.sort_by(|a, b| {
        num(b, "archived")
            .partial_cmp(&num(a, "archived"))
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    match named.as_slice() {
        [one] => Ok((*one).clone()),
        [_, ..] => bail!(
            "{key} is archived more than once; name the folder: {}",
            named.iter().map(|a| s(a, "folder")).collect::<Vec<_>>().join(", ")
        ),
        [] => bail!("No archived project {key}. See: optimux-tui projects archived"),
    }
}

async fn switch(c: &Client, name: &str) -> Result<()> {
    c.save_settings(json!({"active_project": name})).await?;
    if name.is_empty() {
        println!("The Workspace itself is active now (no project).");
    } else {
        println!("{name} is the active project.");
    }
    Ok(())
}

pub async fn run(c: &Client, cmd: ProjectsCmd) -> Result<()> {
    match cmd {
        ProjectsCmd::Push {
            name,
            message,
            write_message,
            commits_only,
            yes,
        } => git_push(c, &name, message, write_message, commits_only, yes).await?,
        ProjectsCmd::Update { name, yes } => git_update(c, &name, yes).await?,
        ProjectsCmd::Status { name } => {
            let v = c.get(&format!("/api/projects/{}/status", enc(&name))).await?;
            print_steps(&v);
            println!(
                "{} commit(s) to push · {} to pull",
                num(&v, "ahead").unwrap_or(0.0),
                num(&v, "behind").unwrap_or(0.0)
            );
        }
        ProjectsCmd::Identity { name } => {
            let v = c.get(&format!("/api/projects/{}/identity", enc(&name))).await?;
            print_steps(&v);
        }
        ProjectsCmd::GitLines { name, ssh } => {
            let v = c.get(&format!("/api/projects/{}/git-commands", enc(&name))).await?;
            if !s(&v, "problem").is_empty() {
                bail!("{}", s(&v, "problem"));
            }
            if !s(&v, "warning").is_empty() {
                eprintln!("⚠ {}", s(&v, "warning"));
            }
            let pull = if ssh && !s(&v, "pull_ssh").is_empty() {
                s(&v, "pull_ssh")
            } else {
                s(&v, "pull")
            };
            println!(
                "# Push, from a terminal on {}",
                if v.get("server").and_then(Value::as_bool) == Some(true) {
                    "the server"
                } else {
                    "this computer"
                }
            );
            println!("{}", s(&v, "push"));
            println!("# Get it on another computer");
            println!("{pull}");
        }
        ProjectsCmd::List { all, json } => {
            let v = c.get("/api/projects/overview").await?;
            if json {
                return print_json(&v);
            }
            let rows: Vec<Vec<String>> = v
                .as_array()
                .into_iter()
                .flatten()
                .filter(|p| {
                    all || p.get("exists").and_then(Value::as_bool) == Some(true)
                        || (p.get("archived").and_then(Value::as_bool) != Some(true)
                            && p.get("deleted").is_none_or(Value::is_null))
                })
                .map(|p| {
                    let exists = p.get("exists").and_then(Value::as_bool) == Some(true);
                    let state = if p.get("active").and_then(Value::as_bool) == Some(true) {
                        "active"
                    } else if exists {
                        ""
                    } else if p.get("archived").and_then(Value::as_bool) == Some(true) {
                        "archived"
                    } else if p.get("deleted").is_some_and(|d| !d.is_null()) {
                        "deleted"
                    } else {
                        "folder missing"
                    };
                    let budget = p
                        .get("budget")
                        .filter(|b| !b.is_null())
                        .map(|b| {
                            format!(
                                "{} ({})",
                                fmt::cost(num(b, "monthly_usd").unwrap_or(0.0)),
                                fmt::percent(num(b, "ratio").unwrap_or(0.0))
                            )
                        })
                        .unwrap_or_else(|| "none".into());
                    vec![
                        s(p, "project"),
                        state.into(),
                        fmt::cost(num(p, "month_cost").unwrap_or(0.0)),
                        fmt::cost(num(p, "all_time_cost").unwrap_or(0.0)),
                        format!("{:.0}", num(p, "chats").unwrap_or(0.0)),
                        budget,
                        num(p, "last_activity").map(ago).unwrap_or_else(|| "–".into()),
                    ]
                })
                .collect();
            if rows.is_empty() {
                println!("No projects yet. Create one: optimux-tui projects new <name>");
            } else {
                println!(
                    "{}",
                    table(
                        &[
                            "PROJECT",
                            "",
                            "THIS MONTH",
                            "ALL TIME",
                            "CHATS",
                            "BUDGET",
                            "LAST ACTIVITY"
                        ],
                        &rows
                    )
                );
            }
        }
        ProjectsCmd::New {
            name,
            kind,
            no_git,
            github,
            description,
            switch: make_active,
        } => {
            if let Some(p) = name_problem(&name) {
                bail!("{p}");
            }
            if !KINDS.iter().any(|(k, ..)| *k == kind) {
                bail!(
                    "--kind: one of {}.",
                    KINDS.iter().map(|(k, ..)| *k).collect::<Vec<_>>().join(", ")
                );
            }
            let github = github.unwrap_or_default();
            if !matches!(github.as_str(), "" | "private" | "public") {
                bail!("--github: private or public.");
            }
            if !github.is_empty() && no_git {
                bail!("A GitHub repository needs git (drop --no-git).");
            }
            let v = c
                .post(
                    "/api/projects",
                    json!({"name": name, "template": kind, "git_init": !no_git, "github": github, "description": description}),
                )
                .await?;
            println!("{}", s(&v, "report"));
            if v.get("ok").and_then(Value::as_bool) == Some(true) {
                println!("Created {name} in {}.", s(&v, "path"));
            } else {
                println!("{name} was created, but not every step worked (above).");
            }
            if make_active {
                switch(c, &name).await?;
            }
        }
        ProjectsCmd::Switch { name, workspace } => {
            let name = if workspace {
                String::new()
            } else {
                name.unwrap_or_default()
            };
            if !name.is_empty() {
                let names: Vec<String> = c
                    .get("/api/projects")
                    .await?
                    .as_array()
                    .into_iter()
                    .flatten()
                    .filter_map(Value::as_str)
                    .map(str::to_string)
                    .collect();
                if !names.contains(&name) {
                    bail!("No project {name} in the Workspace. See: optimux-tui projects list");
                }
            }
            switch(c, &name).await?;
        }
        ProjectsCmd::Files { name } => {
            let v = c.get(&format!("/api/projects/{}/locations", enc(&name))).await?;
            for l in v.get("locations").and_then(Value::as_array).into_iter().flatten() {
                let mut what = s(l, "what");
                if l.get("exists").and_then(Value::as_bool) == Some(false) {
                    what.push_str(" (not created yet)");
                }
                if l.get("shared").and_then(Value::as_bool) == Some(true) {
                    what.push_str(" (shared with other projects)");
                }
                println!("{}\n  {}\n  {what}", s(l, "label"), s(l, "path"));
            }
        }
        ProjectsCmd::Archive { name, yes } => {
            confirm(
                &format!("Archive {name}? Its folder moves to .archive; chats, costs and memory are kept."),
                yes,
            )?;
            let v = c
                .post(&format!("/api/projects/{}/archive", enc(&name)), json!({}))
                .await?;
            println!("Archived {name}: moved to {}.", s(&v, "archived_to"));
            if v.get("was_active").and_then(Value::as_bool) == Some(true) {
                println!("It was the active project: the Workspace itself is active now.");
            }
        }
        ProjectsCmd::Archived { json } => {
            let all = archived(c).await?;
            if json {
                return print_json(&json!(all));
            }
            if all.is_empty() {
                println!("No archived projects.");
                return Ok(());
            }
            let rows: Vec<Vec<String>> = all
                .iter()
                .map(|a| {
                    vec![
                        s(a, "name"),
                        s(a, "folder"),
                        num(a, "archived").map(ago).unwrap_or_default(),
                        format!(
                            "{:.0}",
                            a.pointer("/records/chats").and_then(Value::as_f64).unwrap_or(0.0)
                        ),
                        fmt::cost(a.pointer("/records/cost").and_then(Value::as_f64).unwrap_or(0.0)),
                        s(a, "remote"),
                    ]
                })
                .collect();
            println!(
                "{}",
                table(
                    &["NAME", "FOLDER (.archive/)", "ARCHIVED", "CHATS", "COST", "GITHUB"],
                    &rows
                )
            );
        }
        ProjectsCmd::Restore { folder } => {
            let a = find_archived(c, &folder).await?;
            let v = c
                .post(
                    &format!("/api/projects/archived/{}/restore", enc(&s(&a, "folder"))),
                    json!({}),
                )
                .await?;
            println!("Restored {}: back in {}.", s(&a, "name"), s(&v, "path"));
        }
        ProjectsCmd::Delete {
            folder,
            confirm: typed,
            forget,
        } => {
            let a = find_archived(c, &folder).await?;
            let phrase = s(&a, "phrase");
            let folder = s(&a, "folder");
            if !s(&a, "remote").is_empty() {
                let gh = c
                    .get(&format!("/api/projects/archived/{}/github", enc(&folder)))
                    .await?;
                match s(&gh, "state").as_str() {
                    "exists" => println!(
                        "Its GitHub repository {} still exists and stays: Optimux never deletes GitHub repositories.",
                        s(&gh, "repo")
                    ),
                    "missing" => println!("Its GitHub repository is gone already."),
                    _ => println!("Couldn't tell whether its GitHub repository exists."),
                }
            }
            let typed = match typed {
                Some(t) => t,
                None if std::io::stdin().is_terminal() => {
                    println!(
                        "This removes .archive/{folder} from disk for good{}.",
                        if forget {
                            ", and everything recorded about it"
                        } else {
                            " (its chats, memory and costs are kept)"
                        }
                    );
                    print!("To delete it, type: {phrase}\n> ");
                    std::io::stdout().flush()?;
                    let mut line = String::new();
                    std::io::stdin().read_line(&mut line)?;
                    line.trim().to_string()
                }
                None => bail!("Without a terminal, give --confirm \"{phrase}\"."),
            };
            if !phrase_ok(&typed, &phrase) {
                bail!("The sentence doesn't match: nothing was deleted. It is: {phrase}");
            }
            let v = c
                .post(
                    &format!("/api/projects/archived/{}/delete", enc(&folder)),
                    json!({"confirm": typed, "keep_records": !forget}),
                )
                .await?;
            println!(
                "Deleted {}. {}",
                s(&v, "project"),
                if v.get("records_kept").and_then(Value::as_bool) == Some(true) {
                    "Its chats, memory, costs and statistics are kept, marked as deleted."
                } else {
                    "Everything recorded about it is gone too."
                }
            );
        }
        ProjectsCmd::Repos { find } => {
            let v = c.get("/api/github/repos").await?;
            let find = find.unwrap_or_default().to_lowercase();
            let rows: Vec<Vec<String>> = v
                .get("repos")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
                .filter(|r| find.is_empty() || s(r, "full_name").to_lowercase().contains(&find))
                .map(|r| {
                    vec![
                        s(r, "full_name"),
                        if r.get("private").and_then(Value::as_bool) == Some(true) {
                            "private"
                        } else {
                            "public"
                        }
                        .into(),
                        s(r, "updated_at").chars().take(10).collect(),
                        s(r, "local"),
                    ]
                })
                .collect();
            println!("{}", table(&["REPOSITORY", "", "UPDATED", "IN YOUR WORKSPACE"], &rows));
        }
        ProjectsCmd::Clone {
            full_name,
            name,
            switch: make_active,
        } => {
            let taken: Vec<String> = c
                .get("/api/projects")
                .await?
                .as_array()
                .into_iter()
                .flatten()
                .filter_map(Value::as_str)
                .map(str::to_string)
                .collect();
            let name = name.unwrap_or_else(|| free_name(full_name.rsplit('/').next().unwrap_or(&full_name), &taken));
            if let Some(p) = name_problem(&name) {
                bail!("{p}");
            }
            let job = c
                .post("/api/github/clone", json!({"full_name": full_name, "name": name}))
                .await?;
            let id = s(&job, "id");
            println!("Cloning {full_name} into {name}… (Ctrl+C stops waiting; the clone goes on)");
            let mut last = String::new();
            loop {
                tokio::time::sleep(Duration::from_millis(700)).await;
                let st = c.get(&format!("/api/github/clone/{}", enc(&id))).await?;
                let progress = s(&st, "progress");
                if progress != last && !progress.is_empty() {
                    println!("  {progress}");
                    last = progress;
                }
                match s(&st, "state").as_str() {
                    "running" => continue,
                    "done" => {
                        println!("Cloned {full_name} into {name}.");
                        if make_active {
                            switch(c, &name).await?;
                        }
                        break;
                    }
                    "failed" => bail!("Couldn't clone {full_name}: {}", s(&st, "error")),
                    _ => bail!("The clone was cancelled. Nothing was left behind."),
                }
            }
        }
    }
    Ok(())
}

pub async fn run_project(c: &Client, cmd: ProjectCmd) -> Result<()> {
    match cmd {
        ProjectCmd::Show { name, json } => {
            let v = c.get(&format!("/api/projects/{}/settings", enc(&name))).await?;
            let autonomy = c.get("/api/autonomy").await?;
            let budgets = c.get("/api/budgets").await?;
            if json {
                return print_json(
                    &json!({"settings": v, "autonomy": autonomy.get("projects").and_then(|p| p.get(&name)), "budget": budgets.get(&name)}),
                );
            }
            let own = v.get("overrides").cloned().unwrap_or(Value::Null);
            let glob = v.get("global").cloned().unwrap_or(Value::Null);
            let level = autonomy
                .get("projects")
                .and_then(|p| p.get(&name))
                .map(|l| shown(Some(l)))
                .unwrap_or_default();
            let line = |key: &str, own_v: String, global: String| {
                if own_v.is_empty() {
                    println!("{key:<22}{global}  (global)");
                } else {
                    println!("{key:<22}{own_v}  (own; global: {global})");
                }
            };
            line("autonomy", level, s(&autonomy, "default"));
            match budgets.get(&name).filter(|b| !b.is_null()) {
                Some(b) => println!(
                    "{:<22}{} a month, {} at 100% ({} spent)",
                    "budget",
                    fmt::cost(num(b, "monthly_usd").unwrap_or(0.0)),
                    s(b, "on_limit"),
                    fmt::cost(num(b, "spent").unwrap_or(0.0))
                ),
                None => println!("{:<22}none", "budget"),
            }
            for (key, _) in MODELS.iter().chain(CHOICES).chain(NUMBERS) {
                line(key, shown(own.get(*key)), shown(glob.get(*key)));
            }
        }
        ProjectCmd::Set { name, changes } => {
            let mut settings = Map::new();
            let mut level: Option<Value> = None;
            let mut budget: Option<Option<f64>> = None;
            let mut on_limit: Option<String> = None;
            let keys: Vec<&str> = MODELS.iter().chain(CHOICES).chain(NUMBERS).map(|(k, _)| *k).collect();
            for ch in &changes {
                let Some((k, v)) = ch.split_once('=') else {
                    bail!("{ch}: write key=value (or key=global).")
                };
                let (k, v) = (k.trim(), v.trim());
                let follow = v == "global" || v.is_empty();
                match k {
                    "autonomy" => {
                        if !follow && !LEVELS.iter().any(|(id, _)| *id == v) {
                            bail!("autonomy: supervised, checkpointed, autonomous or global.");
                        }
                        level = Some(if follow { Value::Null } else { json!(v) });
                    }
                    "budget" => {
                        budget = Some(match v {
                            "none" | "global" | "" => None,
                            t => match t.trim_start_matches('$').parse::<f64>() {
                                Ok(n) if n > 0.0 => Some(n),
                                _ => bail!("budget: dollars more than 0, or none."),
                            },
                        })
                    }
                    "on_limit" => {
                        if !matches!(v, "block" | "warn") {
                            bail!("on_limit: block or warn.");
                        }
                        on_limit = Some(v.into());
                    }
                    "scout" | "memory_curator" | "sandbox_network" if !follow => {
                        let b = match v {
                            "on" | "true" => "true",
                            "off" | "false" => "false",
                            _ => bail!("{k}: on, off or global."),
                        };
                        settings.insert(k.into(), parsed(k, b));
                    }
                    k if keys.contains(&k) => {
                        settings.insert(k.into(), if follow { Value::Null } else { parsed(k, v) });
                    }
                    _ => bail!(
                        "Unknown key {k}. Keys: autonomy, budget, on_limit, {}.",
                        keys.join(", ")
                    ),
                }
            }
            if !settings.is_empty() {
                c.post(
                    &format!("/api/projects/{}/settings", enc(&name)),
                    Value::Object(settings),
                )
                .await?;
            }
            if let Some(l) = level {
                c.post("/api/autonomy", json!({"project": name, "level": l})).await?;
            }
            if budget.is_some() || on_limit.is_some() {
                let current = c.get("/api/budgets").await?;
                let b = current.get(&name).filter(|b| !b.is_null());
                let amount = budget.unwrap_or_else(|| b.and_then(|b| num(b, "monthly_usd")));
                let limit = on_limit.unwrap_or_else(|| b.map(|b| s(b, "on_limit")).unwrap_or_else(|| "block".into()));
                if amount.is_none() && budget.is_none() {
                    bail!("{name} has no budget: set one with budget=<dollars>.");
                }
                c.post(
                    "/api/budgets",
                    json!({"project": name, "monthly_usd": amount, "on_limit": limit}),
                )
                .await?;
            }
            println!("Saved for {name}:");
            Box::pin(run_project(c, ProjectCmd::Show { name, json: false })).await?;
        }
    }
    Ok(())
}

pub async fn run_scope(c: &Client, cmd: ScopeCmd) -> Result<()> {
    match cmd {
        ScopeCmd::Show => {
            let v = c.get("/api/settings").await?;
            let root = s(&v, "projects_root");
            println!(
                "{}",
                if root.is_empty() {
                    "No Workspace folder yet.".into()
                } else {
                    root
                }
            );
        }
        ScopeCmd::Set { path } => {
            let path = path.trim().to_string();
            if !path.starts_with('/') {
                bail!("An absolute path, like /home/you/git.");
            }
            let v = c.save_settings(json!({"projects_root": path})).await?;
            println!("Workspace folder: {}", s(&v, "projects_root"));
            if !s(&v, "_note").is_empty() {
                println!("{}", s(&v, "_note"));
            }
        }
    }
    Ok(())
}

/// A git report's commands and their output (status, identity, a push's steps).
fn print_steps(v: &Value) {
    for st in v.get("steps").and_then(Value::as_array).into_iter().flatten() {
        let ok = st.get("ok").and_then(Value::as_bool) != Some(false);
        println!("{} $ {}", if ok { "✓" } else { "✗" }, s(st, "command"));
        for l in s(st, "output").lines() {
            println!("    {l}");
        }
    }
    for n in v
        .get("notes")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
    {
        println!("· {n}");
    }
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

/// Problems stop it; warnings need --yes or a "y" (the window's "I checked the warnings").
fn problems_and_warnings(v: &Value, leave_files: bool) -> Result<Vec<String>> {
    let (mut p, mut w) = (strings(v, "problems"), strings(v, "warnings"));
    if leave_files {
        let (fp, fw) = (strings(v, "file_problems"), strings(v, "file_warnings"));
        p.retain(|x| !fp.contains(x));
        w.retain(|x| !fw.contains(x));
    }
    if !p.is_empty() {
        for x in &p {
            eprintln!("⛔ {x}");
        }
        bail!("It can't run.");
    }
    for x in &w {
        println!("⚠ {x}");
    }
    Ok(w)
}

async fn git_push(
    c: &Client,
    name: &str,
    message: Option<String>,
    write: bool,
    commits_only: bool,
    yes: bool,
) -> Result<()> {
    let path = format!("/api/projects/{}/push", enc(name));
    let v = c.get(&path).await?;
    let files = v.get("files").and_then(Value::as_array).cloned().unwrap_or_default();
    let commits = v.get("commits").and_then(Value::as_u64).unwrap_or(0);
    let commit = !files.is_empty() && !commits_only;
    if files.is_empty() && commits == 0 {
        println!("Nothing to push.");
        return Ok(());
    }
    if commits_only && commits == 0 {
        bail!("There are no commits to push: only uncommitted files (leave out --commits-only to commit them).");
    }
    let target = if s(&v, "target").is_empty() {
        s(&v, "branch")
    } else {
        s(&v, "target")
    };
    println!(
        "{} the branch {target} of {}.",
        if commit {
            "Commits every change below and pushes"
        } else {
            "Pushes"
        },
        s(&v, "remote")
    );
    if commit {
        for f in &files {
            println!("  {:<50} {}", s(f, "path"), crate::dash::git::change_label(f));
        }
        if num(&v, "more").unwrap_or(0.0) > 0.0 {
            println!("  … and {} more", num(&v, "more").unwrap_or(0.0));
        }
    } else if !files.is_empty() {
        println!("The {} uncommitted file(s) stay as they are.", files.len());
    }
    if commits > 0 {
        println!("{commits} commit(s) already made go too.");
    }
    let warnings = problems_and_warnings(&v, !commit)?;
    if !commit && commits == 0 {
        println!("Nothing to push.");
        return Ok(());
    }
    let message = if !commit {
        String::new()
    } else if write {
        println!("Writing the message (one call to the Simple model)…");
        let m = c
            .post(&format!("/api/projects/{}/commit-message", enc(name)), json!({}))
            .await?;
        s(&m, "message")
    } else {
        message.unwrap_or_else(|| s(&v, "default_message"))
    };
    if commit {
        if message.trim().is_empty() {
            bail!("A commit message is needed: --message \"…\" or --write-message.");
        }
        println!("Message:\n  {}", message.trim().replace('\n', "\n  "));
    }
    confirm(
        if warnings.is_empty() {
            "Push?"
        } else {
            "I checked the warnings. Push?"
        },
        yes,
    )?;
    let r = c
        .post(
            &path,
            json!({"message": message.trim(), "tree": v.get("tree"), "confirmed": true, "commit": commit, "head": v.get("head")}),
        )
        .await?;
    print_steps(&r);
    if r.get("ok").and_then(Value::as_bool) == Some(false) {
        bail!(
            "{}",
            if s(&r, "error").is_empty() {
                "The push failed.".to_string()
            } else {
                s(&r, "error")
            }
        );
    }
    println!("Pushed.");
    Ok(())
}

async fn git_update(c: &Client, name: &str, yes: bool) -> Result<()> {
    let path = format!("/api/projects/{}/update", enc(name));
    let v = c.get(&path).await?;
    if v.get("up_to_date").and_then(Value::as_bool) == Some(true) {
        println!("Already up to date.");
        return Ok(());
    }
    let target = if s(&v, "target").is_empty() {
        s(&v, "branch")
    } else {
        s(&v, "target")
    };
    println!(
        "Brings in GitHub's new commits for the branch {target} of {}:",
        s(&v, "remote")
    );
    for cm in v.get("commits").and_then(Value::as_array).into_iter().flatten() {
        println!(
            "  {} {}",
            s(cm, "sha").chars().take(7).collect::<String>(),
            s(cm, "subject")
        );
    }
    let warnings = problems_and_warnings(&v, false)?;
    confirm(
        if warnings.is_empty() {
            "Update?"
        } else {
            "I checked the warnings. Update?"
        },
        yes,
    )?;
    let r = c.post(&path, json!({"sha": v.get("sha")})).await?;
    print_steps(&r);
    if r.get("ok").and_then(Value::as_bool) == Some(false) {
        bail!(
            "{}",
            if s(&r, "error").is_empty() {
                "The update failed.".to_string()
            } else {
                s(&r, "error")
            }
        );
    }
    println!("Updated.");
    Ok(())
}
